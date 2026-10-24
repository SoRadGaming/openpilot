"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): radar dots (push13, 2026-10-24) - every track the car's radar reports (radarTracks, card's RadarData; the
old liveTracks) as a small dot on the comma 4's road view, on the road under the object, with the track(s) radard
picked as the lead marked. A diagnostic, OFF by default: HudRadarDots, Settings > toggles 'radar dots' on the comma 4
and sunnylink Visuals > HUD 'Radar Dots'. Display only: nothing that drives reads anything here.

What the dots say:
  * white, small: a track radard did not pick;
  * cyan, filled, bigger: radard's lead - leadOne's track; leadTwo's, when it is another track, a little smaller;
  * a cyan ring: the track radard's ELESYS clutter guard is HOLDING the plan on (held_lead.py). That lead's distance
    is made up; the ring is where the radar really sees the object.
A vision-only lead (radarTrackId -1) marks nothing: no radar track stands for it. A dot shrinks a little with range,
and every dot has a thin dark rim, so it reads on a bright sky by day and on a dark road at night. Engaged or not.

Where. A track at dRel (from the radar, which is RADAR_TO_CAMERA ahead of the model's origin) and yRel (left positive)
is put on the model's path height at that range plus the camera height - the road under the object, as the lead
chevron is - and projected through the model renderer's car-space transform. The projection is this file's own (3x3
matrix times the points), so it does not depend on the model renderer keeping _map_to_screen (upstream's lead-bar
change removes it).

When. The model renderer lays the dots out (update(), where the transform and the path are), and the road view draws
them (draw(), mici augmented_road_view.py) right after its bottom fade, still inside the camera's scissor and under the
alerts. Drawn under the fade, a track nearer than about 10-12 m - exactly the range where the clutter guard holds one
and a stopped queue sits - was dimmed to nothing (review, push13). draw() draws only what update() laid out since the
last draw, so a frame the model renderer leaves early (no model or calibration yet) draws no dots.

Cost. With the toggle off the UI does no work for it: radarTracks is NOT in the UI's shared SubMaster, no socket is
open, nothing is parsed, projected or drawn - one cached settings read a frame (hud_settings, re-read from the params
at most once a second). On, it reads radarTracks from its own conflated socket without a poller (a SubMaster's
update(0) costs ~0.1 ms a frame on a PC even with nothing new; this read ~0.2 us), takes the points out of each message
once, projects them again only when the tracks, the leads, the model path, the camera transform or the rect changed,
and draws two circles a dot (at most ~13 ELESYS points). Off again closes the socket. No message for STALE_S (what a
SubMaster's alive would allow radarTracks), or a message card marked invalid (a radar error): no dots.
"""
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pyray as rl

from openpilot.cereal import messaging
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettingsReader, hud_settings
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import held_track_id, is_held

SERVICE = "radarTracks"
RADAR_TO_CAMERA = 1.52   # radard.RADAR_TO_CAMERA: the radar is this far ahead of the model's origin (test pins the two)
# a SubMaster's alive for radarTracks (10 periods of services.py's 20 Hz). The ELESYS radar sends at 10 Hz (its 0x423
# trigger; owner routes: median gap 0.100 s, 99th percentile 0.11 s), so here it is 5 of its periods.
STALE_S = 0.5

OTHER, LEAD, LEAD2, HELD = 0, 1, 2, 3   # what radard made of a track

# dot radius in screen px (the comma 4's 536x240), near -> far, by range
RANGE_M = (10.0, 80.0)
RADIUS = {OTHER: (3.6, 2.6), LEAD: (5.2, 4.0), LEAD2: (4.4, 3.4), HELD: (5.2, 4.0)}
RIM = 1.2         # the dark rim round every dot
RING = 2.0        # a held track's ring width
SEGMENTS = 16     # per circle: round to 0.1 px at these sizes, and under half the vertices of DrawCircleV's 36
WHITE = rl.Color(255, 255, 255, 235)
CYAN = rl.Color(0, 200, 255, 255)
RIM_COLOR = rl.Color(0, 0, 0, 170)
FILL = {OTHER: WHITE, LEAD: CYAN, LEAD2: CYAN, HELD: CYAN}


@dataclass(frozen=True)
class Dot:
  x: float      # screen, rect origin added
  y: float
  r: float
  mark: int
  track_id: int
  d_rel: float


def lead_marks(radar_state) -> dict[int, int]:
  """track id -> LEAD / LEAD2 / HELD, from radarState (None: nothing marked). leadOne wins a track both leads name."""
  marks: dict[int, int] = {}
  if radar_state is None:
    return marks
  for lead, mark in ((radar_state.leadOne, LEAD), (radar_state.leadTwo, LEAD2)):
    if not lead.present:
      continue
    if is_held(lead):
      tid, mark = held_track_id(lead), HELD
    else:
      tid = int(lead.radarTrackId)
      if tid < 0:   # vision only: no radar track stands for it
        continue
    marks.setdefault(int(tid), mark)
  return marks


def radius(mark: int, d_rel: float) -> float:
  near, far = RADIUS[mark]
  k = min(max((d_rel - RANGE_M[0]) / (RANGE_M[1] - RANGE_M[0]), 0.0), 1.0)
  return near + (far - near) * k


def track_arrays(points) -> tuple[list[int], np.ndarray, np.ndarray]:
  """(trackId, dRel, yRel) of the finite tracks ahead of the radar, out of capnp once per message."""
  ids, d, y = [], [], []
  for p in points:
    dr, yr = float(p.dRel), float(p.yRel)
    if math.isfinite(dr) and math.isfinite(yr) and dr > 0.0:
      ids.append(int(p.trackId))
      d.append(dr)
      y.append(yr)
  return ids, np.array(d, dtype=np.float32), np.array(y, dtype=np.float32)


def place(ids: list[int], d_rel: np.ndarray, y_rel: np.ndarray, marks: dict[int, int], transform: np.ndarray,
          path: np.ndarray, z_offset: float, camera_offset: float, rect) -> list[Dot]:
  """The dots, in draw order: the unmarked first, the marked on top, far under near. A track not in front of the
  camera or off the rect has no dot. path: the model's path (N x 3, car space); its height at the track's range puts
  the dot on the road."""
  if not ids:
    return []
  x = d_rel + RADAR_TO_CAMERA
  if path is not None and len(path) > 1:
    z = np.interp(x, path[:, 0], path[:, 2]).astype(np.float32)
  else:
    z = np.zeros_like(x)
  car = np.stack([x, camera_offset - y_rel, z + z_offset])   # 3 x N; car space is y right, z down
  proj = np.asarray(transform, dtype=np.float32) @ car
  depth = proj[2]
  with np.errstate(divide="ignore", invalid="ignore"):
    sx, sy = (proj[0] / depth).tolist(), (proj[1] / depth).tolist()
  dots = []
  for tid, px, py, dep, d in zip(ids, sx, sy, depth.tolist(), d_rel.tolist(), strict=True):
    if not (dep > 1e-3 and math.isfinite(px) and math.isfinite(py)):
      continue
    mark = marks.get(tid, OTHER)
    r = radius(mark, d)
    if -r <= px <= rect.width + r and -r <= py <= rect.height + r:
      dots.append(Dot(rect.x + px, rect.y + py, r, mark, tid, d))
  dots.sort(key=lambda dot: (dot.mark != OTHER, -dot.d_rel))
  return dots


def layout(points, marks: dict[int, int], transform: np.ndarray, path: np.ndarray, z_offset: float,
           camera_offset: float, rect) -> list[Dot]:
  """The dots for the tracks in points (radarTracks.points): track_arrays() then place()."""
  return place(*track_arrays(points), marks, transform, path, z_offset, camera_offset, rect)


def draw_dot(center, r: float, mark: int) -> None:
  rl.draw_circle_sector(center, r + RIM, 0.0, 360.0, SEGMENTS, RIM_COLOR)
  if mark == HELD:   # a ring on the dark disc: hollow
    rl.draw_ring(center, r - RING, r, 0.0, 360.0, SEGMENTS, FILL[HELD])
  else:
    rl.draw_circle_sector(center, r, 0.0, 360.0, SEGMENTS, FILL[mark])


class TracksSocket:
  """radarTracks on a conflated socket of its own, read without waiting and without a poller."""
  def __init__(self):
    self._sock = messaging.sub_sock(SERVICE, conflate=True)
    self.tracks = None       # the last message's radarTracks
    self.valid = False
    self.recv_t = -math.inf

  def update(self, now: float) -> None:
    msg = messaging.recv_one_or_none(self._sock)
    if msg is not None:
      self.tracks, self.valid, self.recv_t = msg.radarTracks, msg.valid, now


class RadarDots:
  """The hooks: update() from the model renderer once a frame, after the path (mici model_renderer.py), and draw() from
  the road view after its bottom fade (mici augmented_road_view.py)."""
  def __init__(self, settings: HudSettingsReader = hud_settings, sock_factory: Callable = TracksSocket,
               clock: Callable[[], float] = time.monotonic):
    self._settings = settings
    self._sock_factory = sock_factory
    self._clock = clock
    self._sock = None
    self._tracks = None              # the message track_arrays() last read
    self._arrays: tuple = ([], None, None)
    self._inputs: tuple = ()
    self.dots: list[Dot] = []
    self._draw: list[tuple] = []     # (Vector2, r, mark) per dot, made once per layout
    self._fresh = False              # update() ran since the last draw()

  @property
  def subscribed(self) -> bool:
    return self._sock is not None

  def _clear(self) -> None:
    self._tracks, self._arrays, self._inputs, self.dots, self._draw = None, ([], None, None), (), [], []

  def update(self, rect, transform, path, z_offset: float, camera_offset: float, radar_state) -> list[Dot]:
    self._fresh = True
    if not self._settings.get().radar_dots:
      if self._sock is not None:   # off: drop the socket and the dots
        self._sock = None
        self._clear()
      return self.dots
    if self._sock is None:
      self._sock = self._sock_factory()
    now = self._clock()
    sock = self._sock
    sock.update(now)
    if sock.tracks is None or not sock.valid or now - sock.recv_t > STALE_S:
      if self._inputs or self.dots:
        self._clear()
      return self.dots
    if sock.tracks is not self._tracks:
      self._tracks, self._arrays = sock.tracks, track_arrays(sock.tracks.points)
    # the messages, the transform and the path are new objects whenever they change: compared by identity
    inputs = (sock.tracks, radar_state, transform, path, (z_offset, camera_offset, rect.x, rect.y, rect.width, rect.height))
    if not self._inputs or any(a is not b for a, b in zip(inputs[:4], self._inputs[:4], strict=True)) or \
       inputs[4] != self._inputs[4]:
      self._inputs = inputs
      self.dots = place(*self._arrays, lead_marks(radar_state), transform, path, z_offset, camera_offset, rect)
      self._draw = [(rl.Vector2(d.x, d.y), d.r, d.mark) for d in self.dots]
    return self.dots

  def draw(self) -> None:
    """The dots update() laid out, over the bottom fade; nothing on a frame update() did not run."""
    if not self._fresh:
      return
    self._fresh = False
    for center, r, mark in self._draw:
      draw_dot(center, r, mark)
