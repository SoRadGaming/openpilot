"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): drawing primitives for the comma 4 HUD (536x240; sizes are device pixels).

Ported from the approved mockups (2026-10-03), which were drawn with these same raylib calls over the real onroad
view. What they need that the stock widgets do not do:
  * text placed by its INK box, not its line box: the digits of a 60 px sign sit in its centre, and a 35 px number lines
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
  """Draw txt with its ink box centred vertically on cy; anchor = center | right | left on x. sx < 1 squeezes the glyphs
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
    cap = d * 0.2
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


def pending_icon(bx: float, by: float, size: float, value: int, lower: bool, key_alpha: float = 1.0, sign_alpha: float = 1.0):
  """In place of the green arrow of the 'press - to confirm speed limit' alert (the same size x size box): the PENDING
  limit as a sign with a dashed ring (= not confirmed yet), beside the same green '-' (or '+') the arrow image carries.
  The key keeps the arrow's blink (key_alpha); the sign stays solid so it can be read."""
  d = size * 0.70
  scx, scy = bx + size - d / 2, by + size / 2
  sign(scx, scy, d, value, alpha=sign_alpha, dashed=True, shadow=True)
  bw, bh = size * 0.22, size * 0.12
  col = a(KEY_GREEN, key_alpha)
  rl.draw_rectangle_rounded(rl.Rectangle(bx, scy - bh / 2, bw, bh), 0.5, 6, col)
  if not lower:
    rl.draw_rectangle_rounded(rl.Rectangle(bx + bw / 2 - bh / 2, scy - bw / 2, bh, bw), 0.5, 6, col)
