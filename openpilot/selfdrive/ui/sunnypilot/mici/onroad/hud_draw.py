"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): drawing primitives for the comma 4 HUD (536x240; sizes are device pixels).

Ported from the approved mockups (2026-10-03, round 5 2026-10-09, the MAX card styles D1 and D3 2026-10-12), which
were drawn with these same raylib calls over the real onroad view. What they need that the stock widgets do not do:
  * text placed by its INK box, not its line box: the digits of a 60 px sign sit in its center, and a 35 px number lines
    up with the sign beside it whatever the font's ascent;
  * condensed digits - each glyph squeezed horizontally to ~0.8 - so "110" reaches ~24 px tall inside a 60 px sign, as
    on an Australian sign.
Nominal font sizes go through gui_app's FONT_SCALE like every other text in this UI.
"""
import math
from functools import lru_cache
from typing import NamedTuple

import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import CARD_APPROACH, CARD_PENDING, CARD_DONE, MaxCard,   prompt_parts
from openpilot.system.ui.lib.application import gui_app, FontWeight, FONT_SCALE
from openpilot.system.ui.lib.multilang import tr

# Australian R4-1 speed sign: white face, red annulus, black legend
AU_RED = rl.Color(204, 31, 46, 255)
AU_WHITE = rl.Color(255, 255, 255, 255)
AU_BLACK = rl.Color(12, 12, 12, 255)
# the green of sunnypilot's own img_minus_arrow_down.png / img_plus_arrow_up.png (median of the opaque pixels)
KEY_GREEN = rl.Color(42, 255, 96, 255)
WHITE = rl.Color(255, 255, 255, 255)
# sunnypilot's speed-limit offset badge (speed_limit.py _render_vienna): black disc, its DARK_GREY rim, white digits
OFFSET_FACE = rl.Color(0, 0, 0, 255)
OFFSET_RIM = rl.Color(77, 77, 77, 255)


def a(c: rl.Color, alpha: float) -> rl.Color:
  return rl.Color(c.r, c.g, c.b, int(max(0, min(255, c.a * alpha))))


def fmt_mmss(seconds: float) -> str:
  s = max(0, int(seconds))
  if s >= 3600:
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
  return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------------------------------- text by ink box
def _run(f: rl.Font, txt: str, px: float):
  scale = px / f.baseSize
  out, x = [], 0.0
  for ch in txt:
    idx = rl.get_glyph_index(f, ord(ch))
    g, r = f.glyphs[idx], f.recs[idx]
    out.append((idx, x))
    x += (g.advanceX if g.advanceX else r.width) * scale
  return out, x, scale


@lru_cache(maxsize=512)
def _ink(txt: str, size: float, w: FontWeight) -> tuple[float, float, float, float]:
  f = gui_app.font(w)
  run, _, scale = _run(f, txt, size * FONT_SCALE)
  xs, ys = [], []
  for idx, ox in run:
    g, r = f.glyphs[idx], f.recs[idx]
    if r.width <= 0:
      continue
    xs += [ox + g.offsetX * scale, ox + (g.offsetX + r.width) * scale]
    ys += [g.offsetY * scale, (g.offsetY + r.height) * scale]
  if not xs:
    return 0.0, 0.0, 0.0, 0.0
  return max(xs) - min(xs), max(ys) - min(ys), min(xs), min(ys)


def ink(txt: str, size: float, sx: float = 1.0, w: FontWeight = FontWeight.BOLD) -> tuple[float, float, float, float]:
  """(ink width after the squeeze, ink height, ink x offset, ink y offset) of txt at a nominal size."""
  iw, ih, ix0, iy0 = _ink(txt, float(size), w)
  return iw * sx, ih, ix0, iy0


def text_ink(txt: str, size: float, x: float, cy: float, color: rl.Color, sx: float = 1.0, w: FontWeight = FontWeight.BOLD,
             anchor: str = "center", shadow: float = 0.0) -> tuple[float, float]:
  """Draw txt with its ink box centered vertically on cy; anchor = center | right | left on x. sx < 1 squeezes the glyphs
  horizontally (condensed digits). shadow > 0 adds a 1.5 px drop shadow at that fraction of the color's alpha.
  Returns (ink width, ink height)."""
  f = gui_app.font(w)
  run, _, scale = _run(f, txt, size * FONT_SCALE)
  iw, ih, ix0, iy0 = ink(txt, size, sx, w)
  if anchor == "center":
    left = x - iw / 2
  elif anchor == "right":
    left = x - iw
  else:
    left = x
  x0 = left - ix0 * sx
  y0 = cy - ih / 2 - iy0
  pad = f.glyphPadding

  def draw(dx: float, dy: float, col: rl.Color):
    for idx, ox in run:
      g, r = f.glyphs[idx], f.recs[idx]
      if r.width <= 0:
        continue
      src = rl.Rectangle(r.x - pad, r.y - pad, r.width + 2 * pad, r.height + 2 * pad)
      dst = rl.Rectangle(x0 + dx + (ox + (g.offsetX - pad) * scale) * sx, y0 + dy + (g.offsetY - pad) * scale,
                         (r.width + 2 * pad) * scale * sx, (r.height + 2 * pad) * scale)
      rl.draw_texture_pro(f.texture, src, dst, rl.Vector2(0, 0), 0.0, col)

  if shadow > 0:
    draw(1.5, 1.5, rl.Color(0, 0, 0, int(shadow * color.a)))
  draw(0, 0, color)
  return iw, ih


@lru_cache(maxsize=256)
def fit_digits(txt: str, cap_px: float, max_w: float, sx: float = 0.8, min_sx: float = 0.68,
               w: FontWeight = FontWeight.BOLD) -> tuple[float, float]:
  """(nominal size, squeeze) so txt is cap_px tall in ink and no wider than max_w."""
  size = cap_px / (0.727 * FONT_SCALE)
  _, ih, _, _ = ink(txt, size, 1.0, w)
  if ih > 0:
    size *= cap_px / ih
  iw, _, _, _ = ink(txt, size, 1.0, w)
  s = sx
  if iw * s > max_w:
    s = max(min_sx, max_w / iw)
  if iw * s > max_w:
    size *= max_w / (iw * s)
  return size, s


# ------------------------------------------------------------------------------------------- shading
def soft_disc(cx: float, cy: float, r: float, alpha: float = 0.45):
  rl.draw_circle_gradient(rl.Vector2(cx, cy), r, rl.Color(0, 0, 0, int(255 * max(0.0, min(1.0, alpha)))), rl.BLANK)


def text_shadow(right_x: float, cy: float, w: float, h: float, alpha: float = 0.42):
  """Radial shadow behind a run of digits, like the stock MAX number's circle gradient."""
  n = max(1, int(round(w / max(h * 0.9, 1.0))))
  r = h * 0.95
  per = 1 - (1 - alpha) ** (1 / n) if n > 1 else alpha
  for i in range(n):
    soft_disc(right_x - w * (i + 0.5) / n, cy, r, per)


# ------------------------------------------------------------------------------------------- signs
def sign(cx: float, cy: float, d: float, value: int, alpha: float = 1.0, dashed: bool = False, shadow: bool = True,
         ring_frac: float = 0.18):
  """AU R4-1 roundel (white face, red annulus, black legend). dashed = a limit not yet confirmed (the confirm banner's
  pending sign)."""
  r = d / 2
  if shadow:
    soft_disc(cx, cy, r * 1.4, 0.45 * alpha)
  c = rl.Vector2(cx, cy)
  rl.draw_circle_v(c, r, a(AU_WHITE, alpha))
  ring_in = r * (1 - ring_frac)
  if dashed:
    for k in range(12):
      s = k * 30 + 4
      rl.draw_ring(c, ring_in, r, s, s + 19, 8, a(AU_RED, alpha))
  else:
    rl.draw_ring(c, ring_in, r, 0, 360, 64, a(AU_RED, alpha))
  txt = str(int(value))
  cap = d * (0.40 if len(txt) >= 3 else 0.44)
  size, sx = fit_digits(txt, round(cap, 2), round(ring_in * 2 * 0.86, 2))
  text_ink(txt, size, cx, cy, a(AU_BLACK, alpha), sx=sx)


def stopwatch_glyph(right_x: float, cy: float, size: float = 28, alpha: float = 0.95) -> float:
  """Filled stopwatch: a disc with its hand cut out, the crown on top. size = overall height. Returns its width."""
  r = size * 0.40
  cx = right_x - r
  ccy = cy + size * 0.08
  col = rl.Color(255, 255, 255, int(255 * alpha))
  cut = rl.Color(0, 0, 0, int(255 * alpha))
  rl.draw_circle_v(rl.Vector2(cx + 1.2, ccy + 1.2), r, rl.Color(0, 0, 0, int(140 * alpha)))
  rl.draw_circle_v(rl.Vector2(cx, ccy), r, col)
  rl.draw_rectangle_rounded(rl.Rectangle(cx - size * 0.13, ccy - r - size * 0.17, size * 0.26, size * 0.13), 0.5, 6, col)
  rl.draw_line_ex(rl.Vector2(cx, ccy), rl.Vector2(cx, ccy - r * 0.62), 2.4, cut)
  rl.draw_line_ex(rl.Vector2(cx, ccy), rl.Vector2(cx + r * 0.45, ccy + r * 0.2), 2.4, cut)
  return 2 * r


def big_digits(right_x: float, cy: float, txt: str, size: float = 50, alpha: float = 1.0) -> tuple[float, float]:
  """The stop time: ~35 px white digits on a soft radial shadow, right-aligned at right_x."""
  w, h, _, _ = ink(txt, size)
  text_shadow(right_x, cy, w, h, 0.45 * alpha)
  return text_ink(txt, size, right_x, cy, a(WHITE, alpha), anchor="right", shadow=0.75)


# ------------------------------------------------------------------------------------------- alerts
BANNER_H = 66


def standstill_banner(x: float, y: float, line1: str, line2: str, color: rl.Color, alpha: float = 1.0,
                      width: float | None = None) -> tuple[float, float]:
  """The 'take control / resume driving manually' prompt as a compact 66 px banner: ~16 px caps on the first line,
  ~15 px on the second. Returns (w, h)."""
  w1, _, _, _ = ink(line1, 24)
  w2, _, _, _ = ink(line2, 22, w=FontWeight.MEDIUM)
  w = width or (max(w1, w2) + 28)
  h = BANNER_H
  rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, h), 0.3, 10, rl.Color(color.r, color.g, color.b, int(238 * alpha)))
  text_ink(line1, 24, x + 14, y + 21, a(WHITE, alpha), anchor="left")
  text_ink(line2, 22, x + 14, y + 47, rl.Color(255, 255, 255, int(235 * alpha)), w=FontWeight.MEDIUM, anchor="left")
  return w, h


BANNER_LINE_H = 40   # a one-line compact alert
CONFIRM_H = 46       # the compact confirm, with room for the pending sign
CONFIRM_SIGN_D = 36  # the pending sign it carries
KEY_W, KEY_H = 22, 10


def _fit(txt: str, size: float, max_w: float, w: FontWeight = FontWeight.BOLD) -> tuple[float, float]:
  """(nominal size, squeeze) so txt is no wider than max_w: squeezed down to 0.8 first, then smaller."""
  iw = ink(txt, size, 1.0, w)[0]
  if iw <= max_w or iw <= 0:
    return size, 1.0
  sx = max(0.8, max_w / iw)
  return (size if iw * sx <= max_w else size * max_w / (iw * sx)), sx


def compact_banner(x: float, y: float, line1: str, line2: str, color: rl.Color, alpha: float = 1.0,
                   max_w: float | None = None) -> tuple[float, float]:
  """A compact alert, in the standstill banner's style: line1 in ~16 px caps and, when there is one, line2 under it in
  ~15 px. One line is BANNER_LINE_H tall, two BANNER_H. Wider than max_w: the text is squeezed, then shrunk.
  Returns (w, h)."""
  pad = 14
  s1, sx1 = _fit(line1, 24, (max_w or 1e9) - 2 * pad)
  w1 = ink(line1, s1, sx1)[0]
  w2 = 0.0
  if line2:
    s2, sx2 = _fit(line2, 22, (max_w or 1e9) - 2 * pad, FontWeight.MEDIUM)
    w2 = ink(line2, s2, sx2, FontWeight.MEDIUM)[0]
  w, h = max(w1, w2) + 2 * pad, (BANNER_H if line2 else BANNER_LINE_H)
  rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, h), 0.3, 10, rl.Color(color.r, color.g, color.b, int(238 * alpha)))
  text_ink(line1, s1, x + pad, y + (21 if line2 else h / 2), a(WHITE, alpha), sx=sx1, anchor="left")
  if line2:
    text_ink(line2, s2, x + pad, y + 47, rl.Color(255, 255, 255, int(235 * alpha)), sx=sx2, w=FontWeight.MEDIUM,
             anchor="left")
  return w, h


def key_glyph(x: float, cy: float, lower: bool, alpha: float = 1.0):
  """The green '-' (or '+') of sunnypilot's confirm arrow, KEY_W wide, its left edge at x."""
  col = a(KEY_GREEN, alpha)
  rl.draw_rectangle_rounded(rl.Rectangle(x, cy - KEY_H / 2, KEY_W, KEY_H), 0.5, 6, col)
  if not lower:
    rl.draw_rectangle_rounded(rl.Rectangle(x + KEY_W / 2 - KEY_H / 2, cy - KEY_W / 2, KEY_H, KEY_W), 0.5, 6, col)


def confirm_banner(x: float, y: float, text: str, lower: bool | None, color: rl.Color, alpha: float = 1.0,
                   key_alpha: float = 1.0, value: int = 0, offset: int = 0, max_w: float | None = None) -> tuple[float, float]:
  """The speed-limit confirm as a compact banner: 'press + to set 55', the green key (blinking as the arrow does;
  none while the direction is not known) and, with value > 0, the pending limit as a dashed sign with its offset badge.
  Returns (w, h)."""
  pad, gap = 14, 12
  h = CONFIRM_H if value > 0 else BANNER_LINE_H
  extra = (gap + KEY_W if lower is not None else 0) + (gap + CONFIRM_SIGN_D if value > 0 else 0)
  size, sx = _fit(text, 24, (max_w or 1e9) - 2 * pad - extra)
  tw = ink(text, size, sx)[0]
  w = pad + tw + extra + pad
  cy = y + h / 2
  rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, h), 0.3, 10, rl.Color(color.r, color.g, color.b, int(238 * alpha)))
  text_ink(text, size, x + pad, cy, a(WHITE, alpha), sx=sx, anchor="left")
  cx = x + pad + tw
  if lower is not None:
    key_glyph(cx + gap, cy, lower, key_alpha)
    cx += gap + KEY_W
  if value > 0:
    scx = cx + gap + CONFIRM_SIGN_D / 2
    sign(scx, cy, CONFIRM_SIGN_D, value, alpha=alpha, dashed=True, shadow=False)
    if offset:
      r = CONFIRM_SIGN_D / 2
      offset_badge(scx + r * 0.60, cy - r * 0.80, CONFIRM_SIGN_D * 0.2, offset, alpha=alpha)
  return w, h


def dashed_rrect(x: float, y: float, w: float, h: float, r: float, thick: float, dash: float, gap: float, col: rl.Color,
                 phase: float | None = None):
  """A rounded rectangle's outline in round-capped dashes, walked clockwise from the top edge's left end.

  phase (px along the outline, clockwise) animates it: the dashes are drawn that far on, so a phase growing with time
  marches them round the box. With a phase, dash and gap are stretched a little so a whole number of them fits the
  outline - no seam where the walk starts, so the march has no short dash blinking there. None: the plain dashes, as
  before the phase existed."""
  r = max(0.0, min(r, w / 2, h / 2))
  path = [(rl.Vector2(x + r, y), rl.Vector2(x + w - r, y))]
  corners = ((x + w - r, y + r, -90.0), (x + w - r, y + h - r, 0.0), (x + r, y + h - r, 90.0), (x + r, y + r, 180.0))
  edges = ((rl.Vector2(x + w, y + r), rl.Vector2(x + w, y + h - r)), (rl.Vector2(x + w - r, y + h), rl.Vector2(x + r, y + h)),
           (rl.Vector2(x, y + h - r), rl.Vector2(x, y + r)), None)
  for (cx, cy, a0), edge in zip(corners, edges, strict=True):
    arc = [rl.Vector2(cx + r * math.cos(math.radians(a0 + 90 * k / 8)), cy + r * math.sin(math.radians(a0 + 90 * k / 8)))
           for k in range(9)]
    path += list(zip(arc, arc[1:], strict=False))
    if edge is not None:
      path.append(edge)
  on, left = True, dash
  if phase is not None:
    per = sum(math.hypot(p1.x - p0.x, p1.y - p0.y) for p0, p1 in path)
    k = per / (max(1, round(per / (dash + gap))) * (dash + gap))
    dash, gap = dash * k, gap * k
    p = (-phase) % (dash + gap)
    on, left = (True, dash - p) if p < dash else (False, dash + gap - p)
  for p0, p1 in path:
    seg = math.hypot(p1.x - p0.x, p1.y - p0.y)
    pos = 0.0
    while pos < seg - 1e-6:
      step = min(left, seg - pos)
      if on:
        q0 = rl.Vector2(p0.x + (p1.x - p0.x) * pos / seg, p0.y + (p1.y - p0.y) * pos / seg)
        q1 = rl.Vector2(p0.x + (p1.x - p0.x) * (pos + step) / seg, p0.y + (p1.y - p0.y) * (pos + step) / seg)
        rl.draw_line_ex(q0, q1, thick, col)
        rl.draw_circle_v(q0, thick / 2, col)
        rl.draw_circle_v(q1, thick / 2, col)
      pos += step
      left -= step
      if left <= 1e-6:
        on = not on
        left = dash if on else gap


def _solid_rrect(x: float, y: float, w: float, h: float, r: float, thick: float, col: rl.Color):
  rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(x, y, w, h), min(1.0, 2 * r / min(w, h)), 12, thick, col)


def _rr(x: float, y: float, w: float, h: float, r: float, col: rl.Color):
  """A filled rounded rectangle with a corner RADIUS of r px (raylib's own argument is a fraction of the short side)."""
  if w > 0 and h > 0:
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, h), min(1.0, 2 * r / min(w, h)), 12, col)


def _right_rounded(x: float, y: float, w: float, h: float, r: float, round_bottom: bool, col: rl.Color):
  """A filled rectangle whose RIGHT corners are rounded (the bottom one only when round_bottom) and whose left side is
  square: the part of a card right of a divider. Pieces that never overlap, so a see-through color stays even."""
  r = max(0.0, min(r, w, h / 2))
  if w <= 0 or h <= 0:
    return
  bottom_r = r if round_bottom else 0.0
  rl.draw_rectangle_rec(rl.Rectangle(x, y + r, w, h - r - bottom_r), col)
  rl.draw_rectangle_rec(rl.Rectangle(x, y, w - r, r), col)
  rl.draw_circle_sector(rl.Vector2(x + w - r, y + r), r, 270, 360, 8, col)
  if round_bottom:
    rl.draw_rectangle_rec(rl.Rectangle(x, y + h - r, w - r, r), col)
    rl.draw_circle_sector(rl.Vector2(x + w - r, y + h - r), r, 0, 90, 8, col)


def _top_band(x0: float, x1: float, top: float, h: float, right: float, r: float, col: rl.Color):
  """A band h px tall along a card's top edge from x0 to x1, cut by the card's rounded top-right corner (radius r, the
  card's right edge at `right`) as the card itself is - the split card's red bar and its glow."""
  if x1 <= x0 or h <= 0:
    return
  cx = right - r
  if x1 <= cx:
    rl.draw_rectangle_rec(rl.Rectangle(x0, top, x1 - x0, h), col)
    return
  if cx > x0:
    rl.draw_rectangle_rec(rl.Rectangle(x0, top, cx - x0, h), col)
  # the corner: under the arc (center cx, top + r), down to the band's bottom, right to x1 - a fan from its bottom left
  h = min(h, r)
  end_y = math.asin((h - r) / r)                        # where the arc meets the band's bottom
  end_x = -math.acos(min(1.0, (x1 - cx) / r))           # ... and where it reaches x1
  end = min(end_y, end_x)
  n = 8
  pts = [rl.Vector2(cx + r * math.cos(-math.pi / 2 + (end + math.pi / 2) * k / n),
                    top + r + r * math.sin(-math.pi / 2 + (end + math.pi / 2) * k / n)) for k in range(n + 1)]
  if end_x < end_y:
    pts.append(rl.Vector2(x1, top + h))
  c = rl.Vector2(cx, top + h)
  for p0, p1 in zip(pts, pts[1:], strict=False):
    _tri(c, p0, p1, col)


def chevron(cx: float, cy: float, s: float, down: bool, col: rl.Color):
  """A filled chevron-triangle s px wide, pointing down (a lower limit coming) or up (a higher one)."""
  k = 1 if down else -1
  _tri(rl.Vector2(cx - s / 2, cy - k * s * 0.28), rl.Vector2(cx + s / 2, cy - k * s * 0.28), rl.Vector2(cx, cy + k * s * 0.36),
       col)


SP_HALO = ((14, 50), (9, 90), (5, 140))   # the stock-plus card's flash: (stroke px, alpha), widest first
SC_HALO = ((9, 45), (6, 90))               # the split card's, round the target only: it must not swamp the card


def _flash_outline(x: float, y: float, w: float, h: float, r: float, line: float, alpha: float, halo: tuple):
  """The confirm's outline flashing: one solid white line with a soft halo round it (and a dark line under it, so it
  holds on a bright sky)."""
  for th, al in halo:
    _solid_rrect(x - (th - line) / 2, y - (th - line) / 2, w + th - line, h + th - line, r + (th - line) / 2, th,
                 rl.Color(255, 255, 255, int(al * alpha)))
  _solid_rrect(x + 1, y + 1, w, h, r, line + 1.0, rl.Color(0, 0, 0, int(110 * alpha)))
  _solid_rrect(x, y, w, h, r, line, rl.Color(255, 255, 255, int(255 * alpha)))


# ------------------------------------------------------------------------------------------- the card in MAX's place
# The coming limit and the confirm, in the stock MAX number's corner, in the style HudLimitStyle picks. Both drawers
# take the same hud_model.MaxCard - what to show and where each animation is, decided once by HudRendererSP - and
# return the (x0, y0, x1, y1) they drew in. They keep to the stock MAX number's corner: HudRenderer._draw_set_speed puts
# its digits at (x + 13 + 4, y + 3 - 8 - 3 + 4) in the DISPLAY font at FONT_SIZES.set_speed, 'MAX' at
# (x + 25, y + set_speed - 7 + 4) in SemiBold 36, in white at 0.9, over a black radial shadow of radius 81.
MAX_DIGITS_X, MAX_DIGITS_Y = 13 + 4, 3 - 8 - 3 + 4
MAX_SIZE = 112
MAX_LABEL_X, MAX_LABEL_Y, MAX_LABEL_SIZE = 25, MAX_SIZE - 7 + 4, 36
MAX_SHADOW_R = 162 // 2
MAX_WHITE = 0.9


def _max_ink(txt: str) -> tuple[float, float, float, float]:
  """(x, y, w, h) of txt's ink as the stock MAX number draws it, from the HUD rect's top left."""
  iw, ih, ix0, iy0 = ink(txt, MAX_SIZE, 1.0, FontWeight.DISPLAY)
  return MAX_DIGITS_X + ix0, MAX_DIGITS_Y + iy0, iw, ih


# ---- D1, stock-plus: the number always in the stock MAX digits' place; a card round it says what it is. See-through
# with a thin outline and a draining bar: a limit coming. Solid, the stock MAX's size, a breathing outline and
# 'press [-] to set 100' on MAX's own line: the confirm. No card at all: MAX.
SP_CARD_X, SP_CARD_Y = 12.0, 10.0   # the card's top left, ~10 px outside the stock MAX digits' ink
SP_PAD = 10.0
SP_RADIUS = 12.0
SP_APPROACH_SIZE = 96   # a coming limit's digits: 69 px caps (the stock MAX 112: 81 px), their ink's top left the same
SP_LABEL = 24           # the row under the digits, ~17 px caps
SP_CHEVRON = 16
SP_ROW_GAP = 9.0        # the digits' ink to the row's
SP_BAR_H = 6.0          # 6 px: solid on the device, where a 2 px line is 1
SP_BAR_GAP = 9.0
SP_APPROACH_FILL = 145  # see-through: 4.9:1 for the digits over a 240-grey sky
SP_PENDING_FILL = 230   # solid: act
SP_BREATH_MIN = 170     # the confirm's outline breathes 170..255: still 'act' at its lowest
SP_KEY_FLOOR = 0.5      # the green key blinks 0.5..1, so 'press [-] to set 100' never loses its key


def _sp_row(c: MaxCard) -> list[tuple]:
  """The row under the digits: ('text', words, weight, alpha) | ('chev', down) | ('key', lower, alpha) | ('gap', px)."""
  if c.kind == CARD_APPROACH:
    return [("chev", bool(c.lower)), ("gap", 8.0),
            ("text", c.text, FontWeight.BOLD if c.text == "now" else FontWeight.SEMI_BOLD, 240)]
  parts = prompt_parts(c.text)
  if parts is None or c.lower is None:
    row: list[tuple] = [("text", c.text, FontWeight.BOLD, 240)]
  else:
    key = SP_KEY_FLOOR + (1.0 - SP_KEY_FLOOR) * c.key_alpha
    row = [("text", parts[0], FontWeight.BOLD, 240), ("gap", 9.0), ("key", c.lower, key), ("gap", 9.0),
           ("text", parts[1], FontWeight.BOLD, 240)]
  if c.note:
    row += [("gap", 12.0), ("text", c.note, FontWeight.MEDIUM, 215)]
  return row


def _sp_row_w(row: list[tuple]) -> float:
  w = 0.0
  for p in row:
    w += ink(p[1], SP_LABEL, 1.0, p[2])[0] if p[0] == "text" else SP_CHEVRON if p[0] == "chev" else \
      KEY_W if p[0] == "key" else p[1]
  return w


def _sp_draw_row(row: list[tuple], x: float, cy: float, alpha: float):
  """The row on ONE baseline, its caps centered on cy: 'press' (a descender) and 'to set 100' line up."""
  for p in row:
    if p[0] == "text":
      iw, _, ix0, _ = ink(p[1], SP_LABEL, 1.0, p[2])
      cap = ink("H", SP_LABEL, 1.0, p[2])[1]
      top = cy + cap / 2 - unit_baseline(SP_LABEL, p[2])
      rl.draw_text_ex(gui_app.font(p[2]), p[1], rl.Vector2(x - ix0, top), SP_LABEL, 0,
                      rl.Color(255, 255, 255, int(p[3] * alpha)))
      x += iw
    elif p[0] == "chev":
      chevron(x + SP_CHEVRON / 2, cy, SP_CHEVRON, p[1], rl.Color(255, 255, 255, int(240 * alpha)))
      x += SP_CHEVRON
    elif p[0] == "key":
      key_glyph(x, cy, p[1], p[2] * alpha)
      x += KEY_W
    else:
      x += p[1]


def stock_plus_card(c: MaxCard, x0: float, y0: float) -> tuple[float, float, float, float]:
  """D1. A coming limit: its digits at 96 with their ink's top left where the stock MAX's are, a chevron and 'in 300 m'
  under them, a bar that drains as the change comes ('now': the bar empty, the outline full white, one halo). The
  confirm: the target in the stock MAX digits exactly, 'press [-] to set 100' on MAX's line, the card solid in the
  alert's color, its outline flashing twice, then breathing. grow < 1: the coming limit's card turning into it. Done:
  the card and the prompt dissolve round the digits - already the stock MAX's - as 'MAX' and its shadow come in."""
  txt = str(int(c.value))
  a = c.alpha
  u = max(0.0, min(1.0, c.done)) if c.kind == CARD_DONE else 0.0
  if u > 0:
    rl.draw_circle_gradient(rl.Vector2(x0 + MAX_SHADOW_R, y0 + MAX_SHADOW_R), MAX_SHADOW_R,
                            rl.Color(0, 0, 0, int(255 / 2 * u * a)), rl.BLANK)
  ca = a * (1.0 - u)   # the card itself
  g = 0.0 if c.kind == CARD_APPROACH else max(0.0, min(1.0, c.grow))
  mx, my, _, _ = _max_ink(txt)
  ink_x, ink_y = x0 + mx, y0 + my
  size = round(SP_APPROACH_SIZE + (MAX_SIZE - SP_APPROACH_SIZE) * g, 1)
  iw, _, ix0, iy0 = ink(txt, size, 1.0, FontWeight.DISPLAY)
  row = _sp_row(c)
  cap = ink("H", SP_LABEL, 1.0, FontWeight.BOLD)[1]
  # the row: under the coming limit's digits, or on the stock 'MAX' label's line for the confirm
  cy_a = ink_y + ink(txt, SP_APPROACH_SIZE, 1.0, FontWeight.DISPLAY)[1] + SP_ROW_GAP + cap / 2
  _, mh, _, my0 = ink("MAX", MAX_LABEL_SIZE, 1.0, FontWeight.SEMI_BOLD)
  cy_p = y0 + MAX_LABEL_Y + my0 + mh / 2
  row_cy = cy_a + (cy_p - cy_a) * g
  inner_w = max(iw, _sp_row_w(row))
  bottom = row_cy + cap / 2
  bar_y = None
  if c.kind == CARD_APPROACH:
    bar_y = bottom + SP_BAR_GAP
    bottom = bar_y + SP_BAR_H
  bx, by = x0 + SP_CARD_X, y0 + SP_CARD_Y
  bw, bh = ink_x + inner_w + SP_PAD - bx, bottom + SP_PAD - by
  # the card: see-through black for a coming limit, the alert's color, solid, for the confirm
  fill = SP_APPROACH_FILL + (SP_PENDING_FILL - SP_APPROACH_FILL) * g
  col = [int(v * g) for v in c.color[:3]]
  _rr(bx, by, bw, bh, SP_RADIUS, rl.Color(col[0], col[1], col[2], int(fill * ca)))
  if c.kind == CARD_APPROACH:
    line, line_a = 2.0, (255.0 if c.text == "now" else 150.0)
  else:
    line, line_a = 2.0 + g, SP_BREATH_MIN + (255.0 - SP_BREATH_MIN) * max(0.0, min(1.0, c.breath))
  if c.lit:
    _flash_outline(bx, by, bw, bh, SP_RADIUS, line, ca, SP_HALO)
  else:
    _solid_rrect(bx, by, bw, bh, SP_RADIUS, line, rl.Color(255, 255, 255, int(line_a * ca)))
  rl.draw_text_ex(gui_app.font(FontWeight.DISPLAY), txt, rl.Vector2(ink_x - ix0, ink_y - iy0), size, 0,
                  rl.Color(255, 255, 255, int(255 * MAX_WHITE * a)))
  _sp_draw_row(row, ink_x, row_cy, a * max(0.0, 1.0 - 2.0 * u))
  if u > 0:
    rl.draw_text_ex(gui_app.font(FontWeight.SEMI_BOLD), tr("MAX"), rl.Vector2(x0 + MAX_LABEL_X, y0 + MAX_LABEL_Y),
                    MAX_LABEL_SIZE, 0, rl.Color(255, 255, 255, int(255 * MAX_WHITE * a * u)))
  if bar_y is not None:
    # what is left of the window, anchored left: it drains toward the left as the change comes
    _rr(ink_x, bar_y, inner_w, SP_BAR_H, SP_BAR_H / 2, rl.Color(255, 255, 255, int(55 * a)))
    fw = inner_w * max(0.0, min(1.0, c.frac))
    if fw >= SP_BAR_H:
      _rr(ink_x, bar_y, fw, SP_BAR_H, SP_BAR_H / 2, rl.Color(255, 255, 255, int(235 * a)))
  return bx, by, bx + bw, by + bh


# ---- D3, split card: one black card in MAX's corner. Left, small: the MAX the car holds now. Right, big: the coming
# limit, a chevron and 'in 300 m', a red bar along its top filling as the change comes ('now': full, glowing twice, a
# white 'now' chip). The confirm: the same card, the target in a dashed outline (two flashes, then marching), a footer
# 'press - to set 100' and the green key. Done: the target slides into the stock MAX digits' place and grows to
# their size while the card fades, and the stock MAX number takes over where it lands.
SC_X, SC_Y = 10.0, 10.0
SC_RADIUS = 14.0
SC_PAD_X = 13.0         # each half's outer padding
SC_GUTTER = 16.0        # ... and on the divider's side
SC_PAD_TOP = 13.0       # clears the red bar and the dashes
SC_TARGET_CAP = 62.0    # the coming limit / the confirm's target, px of ink
SC_CTX_CAP = 40.0       # the MAX held now, beside it
SC_SX = 0.9             # numerals squeezed a little
SC_NUM_GAP = 9.0
SC_CAP_ROW = 20.0
SC_PAD_BOT = 9.0
SC_CAPTION = 19         # 'MAX', 'in 300 m': ~13.5 px caps
SC_CHEVRON = 13.0
SC_DIV_W = 1.5          # the hairline between the halves
SC_DIV_INSET = 14.0
SC_FOOT_H = 32.0
SC_FOOT_TEXT = 20
SC_RED_BAR = 4.0        # 4 device px: holds on a bright frame
SC_DASH_P = 5.0         # the dashed outline, this far round the target's ink
SC_FILL = 212
SC_FOOT_FRAC = 0.6      # the footer grows in the first 60 % of the hand-over (0.15 s)


@lru_cache(maxsize=8)
def _sc_num(cap: float) -> tuple[float, float]:
  """(nominal size, squeeze) for numerals cap px tall (all digits share the cap, so '–' takes the same size)."""
  return fit_digits("0", cap, 999.0, sx=SC_SX, min_sx=SC_SX, w=FontWeight.DISPLAY)


def _red(alpha: float) -> rl.Color:
  return rl.Color(AU_RED.r, AU_RED.g, AU_RED.b, int(255 * max(0.0, min(1.0, alpha))))


def _sc_caption_w(txt: str, chip: bool) -> float:
  return SC_CHEVRON + 6 + ink(txt, SC_CAPTION, 1.0, FontWeight.SEMI_BOLD)[0] + (14 if chip else 0)


def _sc_caption(x: float, cy: float, lower: bool | None, txt: str, chip: bool, alpha: float):
  if not txt or alpha < 1e-3:
    return
  if lower is not None:
    chevron(x + SC_CHEVRON / 2, cy + (1.0 if lower else -0.5), SC_CHEVRON, lower, rl.Color(255, 255, 255, int(255 * alpha)))
  tx = x + SC_CHEVRON + 6
  if chip:
    tw = ink(txt, SC_CAPTION, 1.0, FontWeight.SEMI_BOLD)[0]
    _rr(tx - 1, cy - 11, tw + 14, 22, 6, rl.Color(255, 255, 255, int(255 * alpha)))
    text_ink(txt, SC_CAPTION, tx + 6, cy, rl.Color(0, 0, 0, int(255 * alpha)), w=FontWeight.SEMI_BOLD, anchor="left")
  else:
    text_ink(txt, SC_CAPTION, tx, cy, rl.Color(255, 255, 255, int(255 * alpha)), w=FontWeight.SEMI_BOLD, anchor="left")


class SplitLayout(NamedTuple):
  lw: float          # the MAX half's width
  rw: float          # the coming half's (fully open)
  caption: str       # under the target: the distance, 'now', or the limit when it is not the target
  chip: bool         # ... as the white 'now' chip
  target: tuple      # the target's ink (x, y, w, h)


def split_layout(c: MaxCard, x0: float, y0: float) -> SplitLayout:
  """Where the split card puts things for c, from the HUD rect's top left (x0, y0): what split_card draws by."""
  ts, tsx = _sc_num(SC_TARGET_CAP)
  cs, csx = _sc_num(SC_CTX_CAP)
  if c.kind == CARD_APPROACH:
    caption, chip = c.text, c.text == "now"
  else:
    caption, chip = (c.note, False) if c.note else ("now", True)
  tw = ink(str(int(c.value)), ts, tsx, FontWeight.DISPLAY)[0]
  lw = SC_PAD_X + max(ink(c.current, cs, csx, FontWeight.DISPLAY)[0], ink("MAX", SC_CAPTION, 1.0, FontWeight.SEMI_BOLD)[0]) +     SC_GUTTER
  rw = SC_GUTTER + max(tw, _sc_caption_w("in 300 m", False), _sc_caption_w(caption, chip)) + SC_PAD_X
  return SplitLayout(lw, rw, caption, chip, (x0 + SC_X + lw + SC_GUTTER, y0 + SC_Y + SC_PAD_TOP, tw, SC_TARGET_CAP))


def split_card(c: MaxCard, x0: float, y0: float) -> tuple[float, float, float, float]:
  """D3 (see above): approach, pending and done (the slide) from one MaxCard."""
  x, y, a = x0 + SC_X, y0 + SC_Y, c.alpha
  val = str(int(c.value))
  ts, tsx = _sc_num(SC_TARGET_CAP)
  cs, csx = _sc_num(SC_CTX_CAP)
  L = split_layout(c, x0, y0)
  lw, rw, cap_txt, chip = L.lw, L.rw, L.caption, L.chip
  ch = SC_PAD_TOP + SC_TARGET_CAP + SC_NUM_GAP + SC_CAP_ROW + SC_PAD_BOT
  u = max(0.0, min(1.0, c.done)) if c.kind == CARD_DONE else 0.0
  enter = max(0.0, min(1.0, c.enter)) if c.kind == CARD_APPROACH else 1.0
  if c.kind == CARD_APPROACH:
    foot = 0.0
  elif c.kind == CARD_PENDING:
    foot = SC_FOOT_H * min(1.0, max(0.0, c.grow) / SC_FOOT_FRAC)
  else:
    foot = SC_FOOT_H * (1.0 - min(1.0, u * 1.6))
  total = lw + rw * enter
  mx, my, mw, mh = _max_ink(val)
  if u > 0:
    # the card narrows toward the stock MAX number as the target slides there, and fades: MAX is not on a card
    total += (x0 + mx + mw + SC_PAD_X - x - total) * u
    rl.draw_circle_gradient(rl.Vector2(x0 + MAX_SHADOW_R, y0 + MAX_SHADOW_R), MAX_SHADOW_R,
                            rl.Color(0, 0, 0, int(255 / 2 * u * a)), rl.BLANK)
  h = ch + foot
  ca = a * (1.0 - u)
  _rr(x + 2, y + 3, total, h, SC_RADIUS, rl.Color(0, 0, 0, int(70 * ca)))
  _rr(x + 1, y + 1.5, total, h, SC_RADIUS, rl.Color(0, 0, 0, int(40 * ca)))
  fill = c.color if c.kind != CARD_APPROACH else (0, 0, 0)
  _rr(x, y, total, h, SC_RADIUS, rl.Color(fill[0], fill[1], fill[2], int(SC_FILL * ca)))
  rx, right = x + lw, x + total
  if right - rx > 1:
    # the coming half: a step lighter, and the red bar along its top - its progress (a dim track, the bright part
    # growing as the change comes; full from 'now' on, glowing twice then)
    _right_rounded(rx, y, right - rx, ch, SC_RADIUS, foot < 0.5, rl.Color(255, 255, 255, int(24 * ca)))
    _top_band(rx, right, y, SC_RED_BAR, right, SC_RADIUS, _red(80 / 255 * ca))
    f = max(0.0, min(1.0, 1.0 - c.frac)) if c.kind == CARD_APPROACH and c.text != "now" else 1.0
    _top_band(rx, rx + (right - rx) * f, y, SC_RED_BAR, right, SC_RADIUS, _red(ca))
    if c.lit and c.kind == CARD_APPROACH:
      for hv, al in ((12, 30), (8, 60), (6, 110)):
        _top_band(rx, right, y, hv, right, SC_RADIUS, _red(al / 255 * ca))
  if c.kind == CARD_APPROACH:
    _rr(rx - SC_DIV_W / 2, y + SC_DIV_INSET, SC_DIV_W, (ch - 2 * SC_DIV_INSET) * enter, SC_DIV_W / 2,
        rl.Color(255, 255, 255, int(60 * a)))
  base_y = y + SC_PAD_TOP + SC_TARGET_CAP        # the numerals' shared baseline
  cap_cy = base_y + SC_NUM_GAP + SC_CAP_ROW / 2
  # left: the MAX the car holds now, small
  la = a * max(0.0, 1.0 - 2.0 * u)
  text_ink(c.current, cs, x + SC_PAD_X, base_y - SC_CTX_CAP / 2, rl.Color(255, 255, 255, int(225 * la)), sx=csx,
           w=FontWeight.DISPLAY, anchor="left")
  text_ink("MAX", SC_CAPTION, x + SC_PAD_X, cap_cy, rl.Color(255, 255, 255, int(190 * la)), w=FontWeight.SEMI_BOLD,
           anchor="left")
  # right: the target, big
  rx0 = rx + SC_GUTTER
  ra = a * (max(0.0, (enter - 0.5) / 0.5) if c.kind == CARD_APPROACH else 1.0)
  if u > 0:
    # sliding into the stock MAX digits' place, growing to their size and color
    size = round(ts + (MAX_SIZE - ts) * u, 1)
    sx = tsx + (1.0 - tsx) * u
    left = rx0 + (x0 + mx - rx0) * u
    bottom = base_y + (y0 + my + mh - base_y) * u
    ih = ink(val, size, sx, FontWeight.DISPLAY)[1]
    text_ink(val, size, left, bottom - ih / 2, rl.Color(255, 255, 255, int((255 + (255 * MAX_WHITE - 255) * u) * a)),
             sx=sx, w=FontWeight.DISPLAY, anchor="left")
  else:
    text_ink(val, ts, rx0, base_y - SC_TARGET_CAP / 2, rl.Color(255, 255, 255, int(255 * ra)), sx=tsx,
             w=FontWeight.DISPLAY, anchor="left")
  _sc_caption(rx0, cap_cy, c.lower, cap_txt, chip, ra if u == 0 else a * max(0.0, 1.0 - 2.0 * u))
  if u > 0.5:
    rl.draw_text_ex(gui_app.font(FontWeight.SEMI_BOLD), tr("MAX"), rl.Vector2(x0 + MAX_LABEL_X, y0 + MAX_LABEL_Y),
                    MAX_LABEL_SIZE, 0, rl.Color(255, 255, 255, int(255 * MAX_WHITE * a * (2.0 * u - 1.0))))
  if c.kind == CARD_PENDING:
    # waiting for the driver: the target dashed (not set yet), flashing twice, then marching
    tx, ty, tw, th = L.target
    P = SC_DASH_P
    bx, by, bw, bh = tx - P, ty - P, tw + 2 * P, th + 2 * P
    if c.lit:
      _flash_outline(bx, by, bw, bh, 9, 3.4, a, SC_HALO)
    else:
      dashed_rrect(bx + 1, by + 1, bw, bh, 9, 3.4, 9, 6, rl.Color(0, 0, 0, int(110 * a)), phase=c.phase)
      dashed_rrect(bx, by, bw, bh, 9, 2.4, 9, 6, rl.Color(255, 255, 255, int(235 * a)), phase=c.phase)
  if foot > 0.5:
    fy, fa = y + ch, a * foot / SC_FOOT_H
    rl.draw_rectangle_rec(rl.Rectangle(x + 10, fy, total - 20, 1.2), rl.Color(255, 255, 255, int(46 * fa)))
    if foot > 18:
      kw = 10 + KEY_W if c.lower is not None else 0.0
      size, sx = _fit(c.text, SC_FOOT_TEXT, total - 20 - kw)
      tw = ink(c.text, size, sx)[0]
      gx, fcy = x + (total - (tw + kw)) / 2, fy + foot / 2 + 0.5
      text_ink(c.text, size, gx, fcy, rl.Color(255, 255, 255, int(255 * fa)), sx=sx, anchor="left")
      if c.lower is not None:
        key_glyph(gx + tw + 10, fcy, c.lower, c.key_alpha * fa)
  _solid_rrect(x, y, total, h, SC_RADIUS, 1.2, rl.Color(255, 255, 255, int(38 * ca)))
  return x, y, x + total, y + h


def offset_badge(cx: float, cy: float, r: float, offset: int, alpha: float = 1.0):
  """A speed-limit offset on a sign, as sunnypilot's own sign shows it: a small black disc with a grey rim and the
  offset in white - '5' for +5, '-5' for -5."""
  txt = f"{'' if offset > 0 else '-'}{abs(int(offset))}"
  c = rl.Vector2(cx, cy)
  rl.draw_circle_v(c, r, a(OFFSET_FACE, alpha))
  rl.draw_ring(c, r - 2.0, r, 0, 360, 36, a(OFFSET_RIM, alpha))
  cap = r * (0.95 if len(txt) < 3 else 0.8)
  size, sx = fit_digits(txt, round(cap, 2), round(r * 1.55, 2), sx=0.9, min_sx=0.7)
  text_ink(txt, size, cx, cy, a(WHITE, alpha), sx=sx)


# ------------------------------------------------------------------------------------------- the right rail
# The glyphs of the approved style-B rail mockup (hud2/design/glyphs.py: SVGs on a 100x100 box), drawn here with raylib
# primitives instead of PNGs: round-capped strokes are a line plus a disc at each end and joint. Every color is opaque,
# so the overlaps never show - fading in and out included: the strip is black, so a fade is the color dimmed toward
# black (dim()), still opaque.
RAIL_GREY = rl.Color(143, 143, 143, 255)  # the model's plan, which openpilot is not following


def dim(c: rl.Color, k: float) -> rl.Color:
  """c at k (0..1) over the black strip, as an opaque color."""
  k = max(0.0, min(1.0, k))
  return rl.Color(int(round(c.r * k)), int(round(c.g * k)), int(round(c.b * k)), 255)


def _tri(p0: rl.Vector2, p1: rl.Vector2, p2: rl.Vector2, col: rl.Color):
  """A filled triangle in either winding (raylib culls the one it does not want)."""
  if (p1.x - p0.x) * (p2.y - p0.y) - (p1.y - p0.y) * (p2.x - p0.x) > 0:
    p1, p2 = p2, p1
  rl.draw_triangle(p0, p1, p2, col)


def _stroke(points: list[rl.Vector2], w: float, col: rl.Color):
  """A polyline w px wide with round caps and round joins."""
  for p0, p1 in zip(points, points[1:], strict=False):
    rl.draw_line_ex(p0, p1, w, col)
  for p in points:
    rl.draw_circle_v(p, w / 2, col)


def stop_glyph(cx: float, top: float, size: float, solid: bool, fade: float = 1.0):
  """The planned stop: an arrow coming down onto a stop line, in a size x size box. solid = openpilot is driving the
  speed and stopping on this plan: white, a solid line. Otherwise grey with the line dashed: the model's plan only.
  fade < 1 dims it toward the black strip."""
  k = size / 100.0
  col = dim(WHITE if solid else RAIL_GREY, fade)

  def p(x: float, y: float) -> rl.Vector2:
    return rl.Vector2(cx + (x - 50) * k, top + y * k)
  _stroke([p(50, 6), p(50, 52)], 14 * k, col)
  _stroke([p(25, 38), p(50, 63), p(75, 38)], 14 * k, col)
  if solid:
    rl.draw_rectangle_rounded(rl.Rectangle(cx - 42 * k, top + 80 * k, 84 * k, 13 * k), 1.0, 8, col)
  else:
    for x in (8, 30.3, 52.6, 75):
      rl.draw_rectangle_rounded(rl.Rectangle(cx + (x - 50) * k, top + 80 * k, 17 * k, 13 * k), 0.77, 6, col)


def curve_glyph(cx: float, top: float, size: float, left: bool, col: rl.Color = WHITE):
  """A curve arrow (the AU curve-sign shape) bending left or right, in a size x size box."""
  k = size / 100.0
  m = -1.0 if left else 1.0

  def p(x: float, y: float) -> rl.Vector2:
    return rl.Vector2(cx + m * (x - 50) * k, top + y * k)
  # M 32 95 V 58 Q 32 30 60 30 H 64, 15 wide
  bend = [p((1 - t) ** 2 * 32 + 2 * (1 - t) * t * 32 + t ** 2 * 60, (1 - t) ** 2 * 58 + 2 * (1 - t) * t * 30 + t ** 2 * 30)
          for t in (i / 10 for i in range(11))]
  _stroke([p(32, 95)] + bend + [p(64, 30)], 15 * k, col)
  # the head: a triangle with a 7-wide round-joined outline
  head = [p(59, 9), p(88, 30), p(59, 51)]
  _tri(head[0], head[1], head[2], col)
  _stroke(head + [head[0]], 7 * k, col)


def rail_figure(num: str, unit: str, cx: float, cy: float, cap: float, max_w: float, col: rl.Color) -> float:
  """Condensed bold figures (cap px tall in ink) centered on cx, and their unit ('m', 'km/h') smaller after them on the
  same baseline; both squeezed together if they would be wider than max_w - the figures never lose height for their
  unit. Returns the total width."""
  size, sx = fit_digits(num, round(cap, 2), round(max_w, 2), sx=0.9, min_sx=0.72)
  nw, nh, _, _ = ink(num, size, sx)
  usize, usx, gap = size * 0.66, 0.95, 1.5
  uw, uh, _, uy0 = ink(unit, usize, usx, FontWeight.SEMI_BOLD)
  total = nw + gap + uw
  if total > max_w:
    f = max_w / total
    sx, usx, nw, uw, total = sx * f, usx * f, nw * f, uw * f, max_w
  x0 = cx - total / 2
  text_ink(num, size, x0, cy, col, sx=sx, anchor="left")
  # the unit's BASELINE on the figures' (their ink bottom: digits have no descenders) - not its ink bottom, which for
  # 'mph' is the p's descender and lifted the whole unit above the digits
  base = cy + nh / 2
  ucy = base - unit_baseline(usize, FontWeight.SEMI_BOLD) + uy0 + uh / 2
  text_ink(unit, usize, x0 + nw + gap, ucy, col, sx=usx, w=FontWeight.SEMI_BOLD, anchor="left")
  return total


def unit_baseline(size: float, w: FontWeight) -> float:
  """Where the baseline is, below the top of the text's line box, at a nominal size: the ink bottom of 'm', which sits
  on it (ink() measures from the line box's top)."""
  _, h, _, y0 = ink("m", size, 1.0, w)
  return y0 + h
