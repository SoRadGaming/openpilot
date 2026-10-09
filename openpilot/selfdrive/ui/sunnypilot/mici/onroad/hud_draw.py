"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): drawing primitives for the comma 4 HUD (536x240; sizes are device pixels).

Ported from the approved mockups (2026-10-03, round 5 2026-10-09), which were drawn with these same raylib calls over
the real onroad view. What they need that the stock widgets do not do:
  * text placed by its INK box, not its line box: the digits of a 60 px sign sit in its center, and a 35 px number lines
    up with the sign beside it whatever the font's ascent;
  * condensed digits - each glyph squeezed horizontally to ~0.8 - so "110" reaches ~24 px tall inside a 60 px sign, as
    on an Australian sign.
Nominal font sizes go through gui_app's FONT_SCALE like every other text in this UI.
"""
import math
from functools import lru_cache

import pyray as rl

from openpilot.system.ui.lib.application import gui_app, FontWeight, FONT_SCALE

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
  """The speed-limit confirm as a compact banner: 'press + to confirm', the green key (blinking as the arrow does;
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


PENDING_PAD = 7          # the dashed outline, this far round the digits' ink
PENDING_PILL_H = 32
PENDING_PILL_TEXT = 22   # ~15 px caps: the compact banners' first line is 24
PENDING_PILL_GAP = 6     # the outline to the pill


def pending_max(pos: rl.Vector2, size: float, value: int, text: str, lower: bool | None, color: rl.Color,
                alpha: float = 1.0, key_alpha: float = 1.0, note: str = "", max_w: float | None = None,
                shadow_c: "rl.Vector2 | None" = None, lit: bool = False,
                phase: float | None = None) -> tuple[float, float, float, float]:
  """The compact speed-limit confirm in the stock MAX number's place: the set speed the confirm would set, in the stock
  MAX digits (the caller passes their exact place, pos, and size - the DISPLAY font, as HudRenderer._draw_set_speed
  draws them), with a dashed rounded outline round them instead of 'MAX' (dashed: not set yet, as the HUD's pending sign
  is), and under it, where 'MAX' would be, a pill in the alert's color: 'press + to confirm', the green key (blinking as
  the arrow does; none while the direction is not known) and an optional note ('limit 50 +5'). Wider than max_w, the
  pill's words are squeezed, then shrunk. The outline's animation (hud_model.box_outline): lit = one solid line with a
  soft halo (a flash), else the dashes at phase (None: still). Only the outline moves: the digits never blink.
  Returns the (x0, y0, x1, y1) it drew in."""
  txt = str(int(value))
  if shadow_c is not None:
    # the stock MAX number's drop shadow, a little bigger: the pill sits under it
    rl.draw_circle_gradient(shadow_c, 100, rl.Color(0, 0, 0, int(255 / 2 * alpha)), rl.BLANK)
  rl.draw_text_ex(gui_app.font(FontWeight.DISPLAY), txt, pos, size, 0, rl.Color(255, 255, 255, int(255 * 0.9 * alpha)))
  iw, ih, ix0, iy0 = ink(txt, size, 1.0, FontWeight.DISPLAY)
  bx, by, bw, bh = pos.x + ix0 - PENDING_PAD, pos.y + iy0 - PENDING_PAD, iw + 2 * PENDING_PAD, ih + 2 * PENDING_PAD
  if lit:
    # the flash: the outline closes into one solid, thicker line with a soft white halo round it
    for th, al in ((16, 55), (11, 95), (7, 150)):
      _solid_rrect(bx - (th - 3) / 2, by - (th - 3) / 2, bw + th - 3, bh + th - 3, 10 + (th - 3) / 2, th,
                   rl.Color(255, 255, 255, int(al * alpha)))
    _solid_rrect(bx + 1, by + 1, bw, bh, 10, 4.4, rl.Color(0, 0, 0, int(110 * alpha)))
    _solid_rrect(bx, by, bw, bh, 10, 3.4, rl.Color(255, 255, 255, int(255 * alpha)))
  else:
    # a dark underlay first, so the dashes hold on a bright sky
    dashed_rrect(bx + 1, by + 1, bw, bh, 10, 3.4, 9, 6, rl.Color(0, 0, 0, int(110 * alpha)), phase=phase)
    dashed_rrect(bx, by, bw, bh, 10, 2.4, 9, 6, rl.Color(255, 255, 255, int(235 * alpha)), phase=phase)

  pad, gap = 12, 10
  top = by + bh + PENDING_PILL_GAP
  key_w = gap + KEY_W if lower is not None else 0.0
  note_size = PENDING_PILL_TEXT
  note_w = ink(note, note_size, 1.0, FontWeight.MEDIUM)[0] + gap + 2 if note else 0.0
  t_size, t_sx = _fit(text, PENDING_PILL_TEXT, (max_w or 1e9) - 2 * pad - key_w - note_w)
  tw = ink(text, t_size, t_sx)[0]
  pw = pad + tw + key_w + note_w + pad
  cy = top + PENDING_PILL_H / 2
  rl.draw_rectangle_rounded(rl.Rectangle(bx, top, pw, PENDING_PILL_H), 0.45, 10,
                            rl.Color(color.r, color.g, color.b, int(238 * alpha)))
  text_ink(text, t_size, bx + pad, cy, a(WHITE, alpha), sx=t_sx, anchor="left")
  cx = bx + pad + tw
  if lower is not None:
    key_glyph(cx + gap, cy, lower, key_alpha * alpha)
    cx += key_w
  if note:
    text_ink(note, note_size, cx + gap + 2, cy, rl.Color(255, 255, 255, int(215 * alpha)), w=FontWeight.MEDIUM, anchor="left")
  return bx, by, max(bx + bw, bx + pw), top + PENDING_PILL_H


def _solid_rrect(x: float, y: float, w: float, h: float, r: float, thick: float, col: rl.Color):
  rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(x, y, w, h), min(1.0, 2 * r / min(w, h)), 12, thick, col)


# ------------------------------------------------------------------------------------------- the coming limit
APPROACH_SIZE = 64        # the coming limit's digits: the stock MAX number's DISPLAY font at 64 (MAX is 112)
APPROACH_PAD = 6          # the dashed outline, this far round the digits' ink
APPROACH_PILL_H = 28
APPROACH_PILL_TEXT = 21
APPROACH_CHEVRON = 15


def chevron(cx: float, cy: float, s: float, down: bool, col: rl.Color):
  """A filled chevron-triangle s px wide, pointing down (a lower limit coming) or up (a higher one)."""
  k = 1 if down else -1
  _tri(rl.Vector2(cx - s / 2, cy - k * s * 0.28), rl.Vector2(cx + s / 2, cy - k * s * 0.28), rl.Vector2(cx, cy + k * s * 0.36),
       col)


def approach_max(pos: rl.Vector2, value: int, lower: bool, text: str, frac: float, alpha: float = 1.0,
                 shadow_c: "rl.Vector2 | None" = None) -> tuple[float, float, float, float]:
  """The coming limit in the stock MAX number's place (the owner's option C, round 5): the limit in the MAX digits'
  DISPLAY font at APPROACH_SIZE, its ink's top left at pos, in a dashed outline (dashed: not in force yet - the pending
  box's language, smaller and still), a chevron left of it (down: lower, up: higher), and under it a dark pill with the
  distance ('in 300 m', 'now') whose light fill drains as the change comes (frac, what is left of the window). Returns
  the (x0, y0, x1, y1) it drew in."""
  txt = str(int(value))
  if shadow_c is not None:
    rl.draw_circle_gradient(shadow_c, 80, rl.Color(0, 0, 0, int(110 * alpha)), rl.BLANK)
  rl.draw_text_ex(gui_app.font(FontWeight.DISPLAY), txt, pos, APPROACH_SIZE, 0, rl.Color(255, 255, 255, int(230 * alpha)))
  iw, ih, ix0, iy0 = ink(txt, APPROACH_SIZE, 1.0, FontWeight.DISPLAY)
  P = APPROACH_PAD
  bx, by, bw, bh = pos.x + ix0 - P, pos.y + iy0 - P, iw + 2 * P, ih + 2 * P
  dashed_rrect(bx + 1, by + 1, bw, bh, 8, 3.0, 7, 5, rl.Color(0, 0, 0, int(110 * alpha)))
  dashed_rrect(bx, by, bw, bh, 8, 2.2, 7, 5, rl.Color(255, 255, 255, int(235 * alpha)))
  chevron(bx - 12, by + bh / 2, APPROACH_CHEVRON, lower, rl.Color(255, 255, 255, int(235 * alpha)))
  top, ph = by + bh + 6, APPROACH_PILL_H
  tw = ink(text, APPROACH_PILL_TEXT, 1.0, FontWeight.SEMI_BOLD)[0]
  pw = max(bw, tw + 22)
  rl.draw_rectangle_rounded(rl.Rectangle(bx, top, pw, ph), 0.5, 10, rl.Color(0, 0, 0, int(170 * alpha)))
  fw = pw * max(0.0, min(1.0, frac))
  if fw >= ph:   # narrower than its own rounded ends it would be a blob, not a bar: drained
    rl.draw_rectangle_rounded(rl.Rectangle(bx, top, fw, ph), 0.5, 10, rl.Color(255, 255, 255, int(55 * alpha)))
  text_ink(text, APPROACH_PILL_TEXT, bx + pw / 2, top + ph / 2, a(WHITE, alpha), w=FontWeight.SEMI_BOLD)
  return bx - 12 - APPROACH_CHEVRON / 2, by, bx + max(bw, pw), top + ph


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
