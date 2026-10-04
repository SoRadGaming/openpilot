"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): drawing primitives for the comma 4 HUD (536x240; sizes are device pixels).

Ported from the approved mockups (2026-10-03), which were drawn with these same raylib calls over the real onroad
view. What they need that the stock widgets do not do:
  * text placed by its INK box, not its line box: the digits of a 60 px sign sit in its center, and a 35 px number lines
    up with the sign beside it whatever the font's ascent;
  * condensed digits - each glyph squeezed horizontally to ~0.8 - so "110" reaches ~24 px tall inside a 60 px sign, as
    on an Australian sign.
Nominal font sizes go through gui_app's FONT_SCALE like every other text in this UI.
"""
from functools import lru_cache

import pyray as rl

from openpilot.system.ui.lib.application import gui_app, FontWeight, FONT_SCALE

# Australian R4-1 speed sign: white face, red annulus, black legend
AU_RED = rl.Color(204, 31, 46, 255)
AU_WHITE = rl.Color(255, 255, 255, 255)
AU_BLACK = rl.Color(12, 12, 12, 255)
# NSW electronic variable speed limit sign: black face, red LED ring, white LED legend
LED_RED = rl.Color(232, 36, 36, 255)
LED_WHITE = rl.Color(246, 246, 240, 255)
VSL_FACE = rl.Color(14, 14, 14, 255)
# a limit the resolver is only holding (not a current one): the grey sunnypilot's own sign uses
HELD_GREY = rl.Color(145, 155, 149, 255)
# NSW school-zone flashing lights
LAMP_AMBER = rl.Color(255, 170, 0, 255)
LAMP_AMBER_GLOW = rl.Color(255, 185, 40, 170)
LAMP_AMBER_OFF = rl.Color(105, 72, 18, 255)
LAMP_BEZEL = rl.Color(22, 22, 22, 235)
LAMP_GREY = rl.Color(64, 64, 64, 255)
LAMP_GREY_RIM = rl.Color(150, 150, 150, 255)
SCHOOL_AMBER = rl.Color(255, 176, 0, 255)
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
  horizontally (condensed digits). shadow > 0 adds a 1.5 px drop shadow at that fraction of the colour's alpha.
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
def sign(cx: float, cy: float, d: float, value: int, alpha: float = 1.0, dashed: bool = False, electronic: bool = False,
         shadow: bool = True, held: bool = False, ring_frac: float = 0.18):
  """AU R4-1 roundel (white face, red annulus, black legend), or with electronic=True the NSW electronic variable
  speed limit sign (black face, red LED ring, white LED digits). dashed = a limit not yet confirmed. held = a limit the
  resolver is only holding: grey legend, as sunnypilot's own sign does."""
  r = d / 2
  if shadow:
    soft_disc(cx, cy, r * 1.4, 0.45 * alpha)
  face = VSL_FACE if electronic else AU_WHITE
  ring = LED_RED if electronic else AU_RED
  legend = HELD_GREY if held else (LED_WHITE if electronic else AU_BLACK)
  c = rl.Vector2(cx, cy)
  rl.draw_circle_v(c, r, a(face, alpha))
  ring_in = r * (1 - ring_frac)
  if dashed:
    for k in range(12):
      s = k * 30 + 4
      rl.draw_ring(c, ring_in, r, s, s + 19, 8, a(ring, alpha))
  else:
    rl.draw_ring(c, ring_in, r, 0, 360, 64, a(ring, alpha))
  if electronic:
    # the LED ring sits on a black housing: a thin dark rim inside it
    rl.draw_ring(c, ring_in - 1.2, ring_in - 0.2, 0, 360, 64, rl.Color(0, 0, 0, int(255 * alpha)))
  txt = str(int(value))
  cap = d * (0.40 if len(txt) >= 3 else 0.44)
  size, sx = fit_digits(txt, round(cap, 2), round(ring_in * 2 * 0.86, 2))
  text_ink(txt, size, cx, cy, a(legend, alpha), sx=sx)


def school_cue(cx: float, cy: float, d: float, active: bool, t: float, alpha: float = 1.0):
  """The NSW school-zone cue on a round speed sign of diameter d at (cx, cy). THE ONE PLACE TO RESTYLE IT.

  The sign itself keeps the shape every other limit has; only this is added:
    active   - two amber lamps on the rim at 10:30 and 1:30, alternating about once a second (one lit for 0.5 s, then
               the other), as the lights on a NSW school-zone sign do, and "SCHOOL" in amber under the sign.
    inactive - the same two lamps, unlit grey, and no text: a school zone is here, and it is not on now.
  t = seconds on any steady clock (it only picks the lit lamp)."""
  r = d / 2
  k = 0.7071  # 45 degrees
  lamps = ((cx - r * k, cy - r * k), (cx + r * k, cy - r * k))
  lamp_r = max(4.0, d * 0.09)  # ~11 px across on the 60 px sign
  lit = int(t * 2.0) % 2 if active else -1
  for i, (lx, ly) in enumerate(lamps):
    c = rl.Vector2(lx, ly)
    rl.draw_circle_v(c, lamp_r + 1.6, a(LAMP_BEZEL, alpha))
    if not active:
      rl.draw_circle_v(c, lamp_r, a(LAMP_GREY, alpha))
      rl.draw_ring(c, lamp_r - 1.0, lamp_r, 0, 360, 20, a(LAMP_GREY_RIM, alpha))
    elif i == lit:
      rl.draw_circle_gradient(c, lamp_r * 2.3, a(LAMP_AMBER_GLOW, alpha), rl.BLANK)
      rl.draw_circle_v(c, lamp_r, a(LAMP_AMBER, alpha))
    else:
      rl.draw_circle_v(c, lamp_r, a(LAMP_AMBER_OFF, alpha))
  if active:
    cap = d * 0.25  # 15 px on the 60 px sign: the smallest text the approved mockups used
    size, sx = fit_digits("SCHOOL", round(cap, 2), round(d, 2), sx=0.92, min_sx=0.7)
    lw, _, _, _ = ink("SCHOOL", size, sx)
    ly = cy + r + 4 + cap / 2 + 3
    rl.draw_rectangle_rounded(rl.Rectangle(cx - lw / 2 - 5, ly - cap / 2 - 4, lw + 10, cap + 8), 0.5, 8,
                              rl.Color(0, 0, 0, int(150 * alpha)))
    text_ink("SCHOOL", size, cx, ly, a(SCHOOL_AMBER, alpha), sx=sx)


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
  """The speed (or the stop time): ~35 px white digits on a soft radial shadow, right-aligned at right_x."""
  w, h, _, _ = ink(txt, size)
  text_shadow(right_x, cy, w, h, 0.45 * alpha)
  return text_ink(txt, size, right_x, cy, a(WHITE, alpha), anchor="right", shadow=0.75)


# ------------------------------------------------------------------------------------------- next limit
NEXT_BAR_LEN = 62
DIST_TEXT_SIZE = 26  # ~18 px cap


def next_row(right_x: float, cy: float, value: int, frac: float, dist_text: str, bar: bool, text: bool, d: float = 36,
             alpha: float = 1.0) -> float:
  """The next LOWER limit: its small sign right-aligned at right_x and, to its left, a bar that starts full when the
  limit comes into range and shrinks toward the sign (frac = what is left, 0..1), the distance as ~18 px text, or both
  (the text over the bar). Returns the row's left x."""
  sign(right_x - d / 2, cy, d, value, alpha=alpha, shadow=True)
  x1 = right_x - d - 8
  left = x1
  if bar:
    h = 6 if text else 8
    bcy = cy + 11 if text else cy
    L = NEXT_BAR_LEN
    f = max(0.06, min(1.0, frac))
    x0 = x1 - L
    rl.draw_rectangle_rounded(rl.Rectangle(x0 - 1.5, bcy - h / 2 - 1.5, L + 3, h + 3), 1.0, 8, rl.Color(0, 0, 0, int(140 * alpha)))
    rl.draw_rectangle_rounded(rl.Rectangle(x0, bcy - h / 2, L, h), 1.0, 8, rl.Color(255, 255, 255, int(70 * alpha)))
    rl.draw_rectangle_rounded(rl.Rectangle(x1 - L * f, bcy - h / 2, L * f, h), 1.0, 8, rl.Color(255, 255, 255, int(235 * alpha)))
    left = min(left, x0)
  if text:
    tcy = cy - 7 if bar else cy
    iw, _ = text_ink(dist_text, DIST_TEXT_SIZE, x1, tcy, a(WHITE, alpha), w=FontWeight.SEMI_BOLD, anchor="right", shadow=0.7)
    left = min(left, x1 - iw)
  return left


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
CONFIRM_SIGN_D = 36  # the pending sign it carries when the cluster draws none (the next limit's size)
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
  """The speed-limit confirm as a compact banner: 'press + to confirm', the green key (blinking as the arrow does;
  none while the direction is not known) and, with value > 0, the pending limit as a dashed sign with its offset badge
  - for when the cluster draws no sign of its own to show it. Returns (w, h)."""
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


def pending_icon(bx: float, by: float, size: float, value: int, lower: bool, key_alpha: float = 1.0, sign_alpha: float = 1.0,
                 offset: int = 0):
  """In place of the green arrow of the 'press - to confirm speed limit' alert (the same size x size box): the PENDING
  limit as a sign with a dashed ring (= not confirmed yet), beside the same green '-' (or '+') the arrow image carries,
  and a non-zero offset as sunnypilot's badge on the ring, up and right, inside the box.
  The key keeps the arrow's blink (key_alpha); the sign stays solid so it can be read."""
  d = size * 0.70
  scx, scy = bx + size - d / 2, by + size / 2
  sign(scx, scy, d, value, alpha=sign_alpha, dashed=True, shadow=True)
  if offset:
    r = d / 2
    offset_badge(scx + r * 0.60, scy - r * 0.80, d * 0.2, offset, alpha=sign_alpha)
  bw, bh = size * 0.22, size * 0.12
  col = a(KEY_GREEN, key_alpha)
  rl.draw_rectangle_rounded(rl.Rectangle(bx, scy - bh / 2, bw, bh), 0.5, 6, col)
  if not lower:
    rl.draw_rectangle_rounded(rl.Rectangle(bx + bw / 2 - bh / 2, scy - bw / 2, bh, bw), 0.5, 6, col)


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
