#!/usr/bin/env python3
import hashlib
import json
import lzma
import os
import struct
import subprocess
import time
from collections.abc import Callable, Generator  # FORK(UPDATER)

import requests

SPARSE_CHUNK_FMT = struct.Struct('H2xI4x')

AGNOS_MANIFEST_FILE = "openpilot/system/hardware/comma/agnos.json"


class StreamingDecompressor:
  def __init__(self, url: str, on_chunk: Callable[[int, int | None], None] | None = None) -> None:  # FORK(UPDATER)
    self.buf = b""

    self.req = requests.get(url, stream=True, headers={'Accept-Encoding': 'identity'}, timeout=60)
    self.it = self.req.iter_content(chunk_size=1024 * 1024)
    self.decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
    self.eof = False
    self.sha256 = hashlib.sha256()

    # FORK(UPDATER): compressed bytes received, against Content-Length when the server sends one
    length = self.req.headers.get('Content-Length', '')
    self.total: int | None = int(length) if str(length).isdigit() and int(length) > 0 else None
    self.received = 0
    self.on_chunk = on_chunk

  def read(self, length: int) -> bytes:
    while len(self.buf) < length and not self.eof:
      if self.decompressor.needs_input:
        self.req.raise_for_status()

        try:
          compressed = next(self.it)
        except StopIteration:
          self.eof = True
          break
        # FORK(UPDATER): per received chunk, so a 252 MB raw sparse chunk still moves the progress
        self.received += len(compressed)
        if self.on_chunk is not None:
          self.on_chunk(self.received, self.total)
      else:
        compressed = b''

      self.buf += self.decompressor.decompress(compressed, max_length=length)

      if self.decompressor.eof:
        self.eof = True
        break

    result = self.buf[:length]
    self.buf = self.buf[length:]

    self.sha256.update(result)
    return result


def unsparsify(f: StreamingDecompressor) -> Generator[bytes, None, None]:
  # https://source.android.com/devices/bootloader/images#sparse-format
  magic = struct.unpack("I", f.read(4))[0]
  assert(magic == 0xed26ff3a)

  # Version
  major = struct.unpack("H", f.read(2))[0]
  minor = struct.unpack("H", f.read(2))[0]
  assert(major == 1 and minor == 0)

  f.read(2)  # file header size
  f.read(2)  # chunk header size

  block_sz = struct.unpack("I", f.read(4))[0]
  f.read(4)  # total blocks
  num_chunks = struct.unpack("I", f.read(4))[0]
  f.read(4)  # crc checksum

  for _ in range(num_chunks):
    chunk_type, out_blocks = SPARSE_CHUNK_FMT.unpack(f.read(12))

    if chunk_type == 0xcac1:  # Raw
      # TODO: yield in smaller chunks. Yielding only block_sz is too slow. Largest observed data chunk is 252 MB.
      yield f.read(out_blocks * block_sz)
    elif chunk_type == 0xcac2:  # Fill
      filler = f.read(4) * (block_sz // 4)
      for _ in range(out_blocks):
        yield filler
    elif chunk_type == 0xcac3:  # Don't care
      yield b""
    else:
      raise Exception("Unhandled sparse chunk type")


# noop wrapper with same API as unsparsify() for non sparse images
def noop(f: StreamingDecompressor) -> Generator[bytes, None, None]:
  while len(chunk := f.read(1024 * 1024)) > 0:
    yield chunk


def get_target_slot_number() -> int:
  current_slot = subprocess.check_output(["abctl", "--boot_slot"], encoding='utf-8').strip()
  return 1 if current_slot == "_a" else 0


def slot_number_to_suffix(slot_number: int) -> str:
  assert slot_number in (0, 1)
  return '_a' if slot_number == 0 else '_b'


def get_partition_path(target_slot_number: int, partition: dict) -> str:
  path = f"/dev/disk/by-partlabel/{partition['name']}"

  if partition.get('has_ab', True):
    path += slot_number_to_suffix(target_slot_number)

  return path


def get_raw_hash(path: str, partition_size: int) -> str:
  raw_hash = hashlib.sha256()
  pos, chunk_size = 0, 1024 * 1024

  with open(path, 'rb+') as out:
    while pos < partition_size:
      n = min(chunk_size, partition_size - pos)
      raw_hash.update(out.read(n))
      pos += n

  return raw_hash.hexdigest().lower()


def verify_partition(target_slot_number: int, partition: dict[str, str | int], force_full_check: bool = False) -> bool:
  full_check = partition['full_check'] or force_full_check
  path = get_partition_path(target_slot_number, partition)

  if not isinstance(partition['size'], int):
    return False

  partition_size: int = partition['size']

  if not isinstance(partition['hash_raw'], str):
    return False

  partition_hash: str = partition['hash_raw']

  if full_check:
    return get_raw_hash(path, partition_size) == partition_hash.lower()
  else:
    with open(path, 'rb+') as out:
      out.seek(partition_size)
      return out.read(64) == partition_hash.lower().encode()


def clear_partition_hash(target_slot_number: int, partition: dict) -> None:
  path = get_partition_path(target_slot_number, partition)
  with open(path, 'wb+') as out:
    partition_size = partition['size']

    out.seek(partition_size)
    out.write(b"\x00" * 64)
    os.sync()


def extract_compressed_image(target_slot_number: int, partition: dict, cloudlog,
                             progress_cb: Callable[[float], None] | None = None):  # FORK(UPDATER)
  path = get_partition_path(target_slot_number, partition)

  # FORK(UPDATER): progress_cb gets this partition's fraction done: compressed bytes received over
  # Content-Length, or, without one, uncompressed bytes written over the partition size
  def on_chunk(received: int, total: int | None) -> None:
    if progress_cb is not None and total:
      progress_cb(min(received / total, 1.0))

  downloader = StreamingDecompressor(partition['url'], on_chunk=on_chunk)

  with open(path, 'wb+') as out:
    # Flash partition
    last_p = 0
    raw_hash = hashlib.sha256()
    f = unsparsify if partition['sparse'] else noop
    for chunk in f(downloader):
      raw_hash.update(chunk)
      out.write(chunk)
      p = int(out.tell() / partition['size'] * 100)
      if p != last_p:
        last_p = p
        print(f"Installing {partition['name']}: {p}", flush=True)
        if progress_cb is not None and not downloader.total:  # FORK(UPDATER): no Content-Length
          progress_cb(min(p / 100, 1.0))

    if raw_hash.hexdigest().lower() != partition['hash_raw'].lower():
      raise Exception(f"Raw hash mismatch '{raw_hash.hexdigest().lower()}'")

    if downloader.sha256.hexdigest().lower() != partition['hash'].lower():
      raise Exception("Uncompressed hash mismatch")

    if out.tell() != partition['size']:
      raise Exception("Uncompressed size mismatch")

    os.sync()


def flash_partition(target_slot_number: int, partition: dict, cloudlog, standalone=False,
                    progress_cb: Callable[[float], None] | None = None):  # FORK(UPDATER)
  cloudlog.info(f"Downloading and writing {partition['name']}")

  if verify_partition(target_slot_number, partition):
    cloudlog.info(f"Already flashed {partition['name']}")
    if progress_cb is not None:  # FORK(UPDATER)
      progress_cb(1.0)
    return

  # Clear hash before flashing in case we get interrupted
  full_check = partition['full_check']
  if not full_check:
    clear_partition_hash(target_slot_number, partition)

  path = get_partition_path(target_slot_number, partition)

  extract_compressed_image(target_slot_number, partition, cloudlog, progress_cb=progress_cb)  # FORK(UPDATER)

  # Write hash after successful flash
  if not full_check:
    with open(path, 'wb+') as out:
      out.seek(partition['size'])
      out.write(partition['hash_raw'].lower().encode())


def swap(manifest_path: str, target_slot_number: int, cloudlog) -> None:
  update = json.load(open(manifest_path))
  for partition in update:
    if not partition.get('full_check', False):
      clear_partition_hash(target_slot_number, partition)

  while True:
    out = subprocess.check_output(f"abctl --set_active {target_slot_number}", shell=True, stderr=subprocess.STDOUT, encoding='utf8')
    if ("No such file or directory" not in out) and ("lun as boot lun" in out):
      cloudlog.info(f"Swap successful {out}")
      break
    else:
      cloudlog.error(f"Swap failed {out}")


def flash_agnos_update(manifest_path: str, target_slot_number: int, cloudlog, standalone=False,
                       progress_cb: Callable[[float], None] | None = None) -> None:  # FORK(UPDATER)
  update = json.load(open(manifest_path))

  cloudlog.info(f"Target slot {target_slot_number}")

  # set target slot as unbootable
  subprocess.run(f"abctl --set_unbootable {target_slot_number}", shell=True)

  # FORK(UPDATER): progress_cb gets the fraction of the whole update done, each partition weighted
  # by its size. Weighted equally, the six small partitions read 86% before system (99% of the bytes) began.
  total_size = sum(p['size'] for p in update) or 1
  done_size = 0

  for partition in update:
    success = False

    def partition_progress(frac: float, done: int = done_size, size: int = partition['size']) -> None:  # FORK(UPDATER)
      if progress_cb is not None:
        progress_cb((done + frac * size) / total_size)

    for retries in range(10):
      try:
        flash_partition(target_slot_number, partition, cloudlog, standalone, progress_cb=partition_progress)  # FORK(UPDATER)
        success = True
        break

      except requests.exceptions.RequestException:
        cloudlog.exception("Failed")
        cloudlog.info(f"Failed to download {partition['name']}, retrying ({retries})")
        time.sleep(10)

    if not success:
      cloudlog.info(f"Failed to flash {partition['name']}, aborting")
      raise Exception("Maximum retries exceeded")
    done_size += partition['size']  # FORK(UPDATER)

  cloudlog.info(f"AGNOS ready on slot {target_slot_number}")


def verify_agnos_update(manifest_path: str, target_slot_number: int) -> bool:
  update = json.load(open(manifest_path))
  return all(verify_partition(target_slot_number, partition) for partition in update)


if __name__ == "__main__":
  import argparse
  import logging

  parser = argparse.ArgumentParser(description="Flash and verify AGNOS update",
                                   formatter_class=argparse.ArgumentDefaultsHelpFormatter)

  parser.add_argument("--verify", action="store_true", help="Verify and perform swap if update ready")
  parser.add_argument("--swap", action="store_true", help="Verify and perform swap, downloads if necessary")
  parser.add_argument("manifest", help="Manifest json")
  args = parser.parse_args()

  logging.basicConfig(level=logging.INFO)

  target_slot_number = get_target_slot_number()
  if args.verify:
    if verify_agnos_update(args.manifest, target_slot_number):
      swap(args.manifest, target_slot_number, logging)
      exit(0)
    exit(1)
  elif args.swap:
    while not verify_agnos_update(args.manifest, target_slot_number):
      logging.error("Verification failed. Flashing AGNOS")
      flash_agnos_update(args.manifest, target_slot_number, logging, standalone=True)

    logging.warning(f"Verification succeeded. Swapping to slot {target_slot_number}")
    swap(args.manifest, target_slot_number, logging)
  else:
    flash_agnos_update(args.manifest, target_slot_number, logging, standalone=True)
