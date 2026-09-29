"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): load and validate an NSW speed-zone index file.

Pure numpy + stdlib, no openpilot imports: build_index.py loads this file by path in CI.

  load_index(path, mmap=False)          -> dict of decoded numpy arrays (layout: build_index.py docstring)
  read_meta(arrays)                     -> the meta dict stored inside the index
  read_manifest(path)                   -> manifest.json as a dict (validated shape)
  verify_index(npz, manifest=None, ...) -> IndexInfo, or raises IndexInvalid with the reason
  write_stored_aligned(arrays, path)    -> an uncompressed, 64-byte aligned copy that load_index(mmap=True) maps

Contains data from Transport for NSW, CC BY 4.0, modified.
"""

import hashlib
import io
import json
import os
import struct
import zipfile
from dataclasses import dataclass

import numpy as np

FORMAT_VERSION = 2
# Format 1 = the P1 (offline) index; it lacks the embedded school calendar, which the matcher then takes from
# school_days.py. Everything else is identical, so the matcher reads both.
SUPPORTED_FORMATS = (1, 2)
INDEX_NAME = "nsw_zones.npz"
MANIFEST_NAME = "manifest.json"
ATTRIBUTION_NAME = "ATTRIBUTION.txt"


class IndexInvalid(ValueError):
  """The index file (or its manifest) must not be used. str(e) says why."""


@dataclass(frozen=True)
class IndexInfo:
  path: str
  format_version: int
  sha256: str | None
  data_version: str
  attribution: str
  bytes: int


def sha256_file(path, bufsize=1 << 22):
  h = hashlib.sha256()
  with open(path, 'rb') as f:
    while True:
      b = f.read(bufsize)
      if not b:
        break
      h.update(b)
  return h.hexdigest()


# ============================================================================ decoding
def _mmap_npz(path):
  """Memory-map every member of an UNCOMPRESSED .npz. Pages are shared and lazy."""
  out = {}
  with zipfile.ZipFile(path) as z, open(path, 'rb') as f:
    for info in z.infolist():
      if not info.filename.endswith('.npy'):
        continue
      if info.compress_type != zipfile.ZIP_STORED:
        raise IndexInvalid(f'{info.filename} is compressed; mmap needs a stored file (write_stored_aligned)')
      f.seek(info.header_offset)
      h = f.read(30)
      nlen = int.from_bytes(h[26:28], 'little')
      xlen = int.from_bytes(h[28:30], 'little')
      f.seek(info.header_offset + 30 + nlen + xlen)
      ver = np.lib.format.read_magic(f)
      if ver == (1, 0):
        shape, fortran, dtype = np.lib.format.read_array_header_1_0(f)
      else:
        shape, fortran, dtype = np.lib.format.read_array_header_2_0(f)
      off = f.tell()
      name = info.filename[:-4]
      if dtype.hasobject:
        raise IndexInvalid(f'{name}: object arrays are not allowed')
      if int(np.prod(shape)) == 0:
        out[name] = np.zeros(shape, dtype)
      elif shape == () or off % max(np.dtype(dtype).alignment, 1):
        # a scalar (np.memmap cannot map 0-d), or an unaligned member, is read into RAM instead
        f.seek(off)
        out[name] = np.reshape(np.fromfile(f, dtype=dtype, count=int(np.prod(shape))), shape, order='F' if fortran else 'C')
      else:
        out[name] = np.asarray(np.memmap(path, dtype=dtype, mode='r', offset=off, shape=shape, order='F' if fortran else 'C'))
  return out


def _derive_chunks(part_v0, chunk):
  lens = np.diff(part_v0.astype(np.int64))
  nseg = lens - 1
  nch = (nseg + chunk - 1) // chunk
  C = int(nch.sum())
  chunk_part = np.repeat(np.arange(len(lens), dtype=np.int64), nch)
  k = np.arange(C, dtype=np.int64) - np.repeat(np.cumsum(nch) - nch, nch)
  chunk_v0 = part_v0.astype(np.int64)[chunk_part] + k * chunk
  chunk_n = np.minimum(chunk, nseg[chunk_part] - k * chunk)
  return chunk_v0.astype(np.int32), chunk_n.astype(np.uint8), chunk_part.astype(np.int32)


def read_meta(arrays):
  try:
    return json.loads(bytes(np.asarray(arrays['meta_json'])).decode('utf-8'))
  except (KeyError, ValueError, UnicodeDecodeError) as e:
    raise IndexInvalid(f'no readable meta_json ({e!r})') from e


def load_index(path, mmap=False):
  """Load and decode an index file into a dict of arrays. mmap=True needs a stored (uncompressed) file.
  Raises IndexInvalid for an unsupported format version, a missing member or a file that is not an index."""
  try:
    if mmap:
      a = _mmap_npz(path)
    else:
      with np.load(path, allow_pickle=False) as z:
        a = {k: z[k] for k in z.files}
  except IndexInvalid:
    raise
  except (OSError, ValueError, zipfile.BadZipFile, EOFError) as e:
    raise IndexInvalid(f'cannot read {os.path.basename(str(path))}: {e!r}') from e
  meta = read_meta(a)
  fv = meta.get('format_version')
  if fv not in SUPPORTED_FORMATS:
    raise IndexInvalid(f'index format {fv} is not supported (supported {SUPPORTED_FORMATS})')
  for p in ('b', 'o'):
    for n in ('vlat', 'vlon', 'grid_keys'):
      k = f'{p}_{n}_d'
      if k in a:
        a[f'{p}_{n}'] = np.cumsum(a.pop(k), dtype=np.int32)
    if f'{p}_part_len' in a:
      ln = a.pop(f'{p}_part_len')
      v0 = np.zeros(len(ln) + 1, np.int32)
      v0[1:] = np.cumsum(ln, dtype=np.int64)
      a[f'{p}_part_v0'] = v0
    if f'{p}_grid_count' in a:
      c = a.pop(f'{p}_grid_count')
      s = np.zeros(len(c) + 1, np.int32)
      s[1:] = np.cumsum(c, dtype=np.int64)
      a[f'{p}_grid_start'] = s
    for need in ('vlat', 'vlon', 'part_v0', 'part_speed', 'part_type', 'part_dir', 'grid_keys', 'grid_start', 'grid_items'):
      if f'{p}_{need}' not in a:
        raise IndexInvalid(f'index lacks {p}_{need}')
    if f'{p}_chunk_v0' not in a:
      a[f'{p}_chunk_v0'], a[f'{p}_chunk_n'], a[f'{p}_chunk_part'] = _derive_chunks(a[f'{p}_part_v0'], int(meta['chunk']))
    if len(a[f'{p}_vlat']) != int(a[f'{p}_part_v0'][-1]):
      raise IndexInvalid(f'{p}: vertex count does not match part_v0')
  return a


# ============================================================================ writing (builder + device conversion)
def _npy_bytes(v):
  buf = io.BytesIO()
  np.lib.format.write_array(buf, np.asarray(v, order='C'), allow_pickle=False)
  return buf.getvalue()


def write_npz(arrays, path, compress=True):
  """np.savez(_compressed) equivalent with fixed member timestamps (identical arrays -> identical bytes),
  written to path + '.tmp' and renamed into place."""
  tmp = path + '.tmp'
  comp = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
  with zipfile.ZipFile(tmp, 'w', compression=comp, allowZip64=True) as z:
    for k in sorted(arrays):
      zi = zipfile.ZipInfo(k + '.npy', date_time=(1980, 1, 1, 0, 0, 0))
      zi.compress_type = comp
      z.writestr(zi, _npy_bytes(arrays[k]), compresslevel=6 if compress else None)
  os.replace(tmp, path)


def write_stored_aligned(arrays, path, align=64):
  """Uncompressed .npz whose array data start on `align`-byte boundaries, so load_index(mmap=True) gets aligned
  views. Padding goes in a zip extra field (id 0xD935, as Android's zipalign does); any zip reader copes.
  `arrays` are DECODED arrays (load_index output)."""
  tmp = path + '.tmp'
  with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
    for k in sorted(arrays):
      data = _npy_bytes(arrays[k])
      name = k + '.npy'
      zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
      zi.compress_type = zipfile.ZIP_STORED
      off = z.fp.tell() + 30 + len(name.encode()) + 4
      pad = (-off) % align
      zi.extra = struct.pack('<HH', 0xD935, pad) + bytes(pad)
      z.writestr(zi, data)
  os.replace(tmp, path)


# ============================================================================ manifest + verification
MANIFEST_REQUIRED = ('format_version', 'sha256', 'bytes', 'data_version', 'attribution')


def read_manifest(path):
  try:
    with open(path, encoding='utf-8') as f:
      m = json.load(f)
  except (OSError, ValueError) as e:
    raise IndexInvalid(f'cannot read manifest: {e!r}') from e
  if not isinstance(m, dict):
    raise IndexInvalid('manifest is not a JSON object')
  missing = [k for k in MANIFEST_REQUIRED if k not in m]
  if missing:
    raise IndexInvalid(f'manifest lacks {missing}')
  return m


def manifest_compatible(manifest):
  """True if this code can use the index the manifest describes (the downloader asks this BEFORE downloading)."""
  try:
    return int(manifest.get('format_version')) in SUPPORTED_FORMATS
  except (TypeError, ValueError):
    return False


def verify_index(npz_path, manifest_path=None, check_sha=True, load=True):
  """Check an index file before use. With a manifest: format version, byte size and sha256 must match it.
  load=True also decodes the file (format version inside, required arrays, attribution).
  -> IndexInfo, or raises IndexInvalid."""
  if not os.path.exists(npz_path):
    raise IndexInvalid('index file missing')
  size = os.path.getsize(npz_path)
  sha = None
  data_version = ''
  m = None
  if manifest_path is not None:
    m = read_manifest(manifest_path)
    if not manifest_compatible(m):
      raise IndexInvalid(f'manifest format {m.get("format_version")} is not supported (supported {SUPPORTED_FORMATS})')
    if int(m['bytes']) != size:
      raise IndexInvalid(f'size {size} != manifest {m["bytes"]}')
    data_version = str(m.get('data_version') or '')
    if check_sha:
      sha = sha256_file(npz_path)
      if sha != str(m['sha256']).lower():
        raise IndexInvalid('sha256 does not match the manifest')
  fv = int(m['format_version']) if m else -1
  attribution = str(m.get('attribution', '')) if m else ''
  if load:
    a = load_index(npz_path)
    meta = read_meta(a)
    fv = int(meta['format_version'])
    if m is not None and fv != int(m['format_version']):
      raise IndexInvalid(f'index format {fv} != manifest {m["format_version"]}')
    attribution = meta.get('attribution_long') or meta.get('attribution') or attribution
    data_version = data_version or str(meta.get('data_version') or meta.get('built_utc', '')[:10])
  if 'Transport for NSW' not in attribution:
    raise IndexInvalid('attribution missing')
  return IndexInfo(path=npz_path, format_version=fv, sha256=sha, data_version=data_version, attribution=attribution, bytes=size)
