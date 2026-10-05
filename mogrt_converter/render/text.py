"""Text layers: AE-compatible layout (HarfBuzz) and text animators with range selectors."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
import skia
import uharfbuzz as hb

from .. import fonts
from ..evaluator import Evaluator, TextValue, is_group, is_synthetic
from .paint import color4f

AUTO_LEADING = 1.2


@dataclass
class FontData:
    path: str
    index: int
    hb_font: Any
    upem: int
    typeface: skia.Typeface
    ascender_d: float  # height of 'd' (CoolType ascent) in em units / upem


@lru_cache(maxsize=64)
def font_data(ps_name: str) -> FontData:
    path, index = fonts.resolve(ps_name)
    blob = hb.Blob.from_file_path(path)
    face = hb.Face(blob, index)
    hfont = hb.Font(face)
    upem = face.upem
    tf = skia.Typeface.MakeFromFile(path, index)
    sk = skia.Font(tf, upem)
    gid = sk.unicharToGlyph(ord("d"))
    b = sk.getBounds([gid])[0] if gid else skia.Rect.MakeXYWH(0, -0.7 * upem, 1, 0.7 * upem)
    return FontData(path, index, hfont, upem, tf, -b.top() / upem)


@dataclass
class Glyph:
    gid: int
    char: str
    x: float  # pen position (layer space), baseline
    y: float
    advance: float
    line: int
    word: int
    index: int  # character index in text
    font: FontData
    size: float


@dataclass
class TextLayout:
    glyphs: list[Glyph] = field(default_factory=list)
    lines: list[tuple[int, int]] = field(default_factory=list)
    style: Any = None


def _shape(text: str, fd: FontData, size: float) -> list[tuple[int, float, int]]:
    """Return (glyph id, advance in px, cluster) per glyph."""
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(fd.hb_font, buf, {"liga": False, "clig": False, "kern": True})
    scale = size / fd.upem
    return [(info.codepoint, pos.x_advance * scale, info.cluster) for info, pos in zip(buf.glyph_infos, buf.glyph_positions)]


def _justify_offset(just: int, width: float) -> float:
    name = getattr(just, "name", str(just))
    if "RIGHT" in name and "FULL" not in name:
        return -width
    if "CENTER" in name and "FULL" not in name:
        return -width / 2
    return 0.0


def layout_text(tv: TextValue, char_tracking: list[float] | None = None) -> TextLayout:
    doc = tv.doc
    raw = tv.text
    text = raw.replace("\u0003", "\r").replace("\n", "\r")
    if text.endswith("\r"):
        text = text[:-1]
    if getattr(doc, "all_caps", False):
        text = text.upper()
    size = float(doc.font_size)
    fd = font_data(doc.font)
    tracking = float(getattr(doc, "tracking", 0) or 0) / 1000.0 * size
    hscale = float(getattr(doc, "horizontal_scale", 1.0) or 1.0)
    leading = size * AUTO_LEADING if getattr(doc, "auto_leading", False) else float(doc.leading or size * AUTO_LEADING)
    shift = float(getattr(doc, "baseline_shift", 0) or 0)  # positive raises the text
    just = doc.justification
    box = bool(getattr(doc, "box_text", False)) and doc.box_text_size is not None

    out = TextLayout(style=doc)
    paragraphs = text.split("\r")
    lines: list[list[tuple[int, float, int, str]]] = []  # per line: (gid, adv, char index, char)
    char_base = 0
    box_w = float(doc.box_text_size[0]) if box else None
    for para in paragraphs:
        shaped = _shape(para, fd, size) if para else []
        items = []
        for gid, adv, cl in shaped:
            ci = char_base + cl
            ch = para[cl] if cl < len(para) else ""
            extra = tracking + (char_tracking[ci] if char_tracking and ci < len(char_tracking) else 0.0)
            items.append((gid, adv * hscale + extra, ci, ch))
        if box_w is not None and items:
            # greedy word wrap at spaces (trailing spaces excluded from fit)
            cur: list = []
            word: list = []
            width = 0.0
            for it in items:
                word.append(it)
                if it[3] == " ":
                    ww = sum(a for _, a, _, c in word if c != " ")
                    if cur and width + ww > box_w:
                        lines.append(cur)
                        cur, width = [], 0.0
                    cur += word
                    width += sum(a for _, a, _, _ in word)
                    word = []
            if word:
                ww = sum(a for _, a, _, _ in word)
                if cur and width + ww > box_w:
                    lines.append(cur)
                    cur = []
                cur += word
            lines.append(cur)
        else:
            lines.append(items)
        char_base += len(para) + 1

    if box:
        bx, by = doc.box_text_pos[0], doc.box_text_pos[1]
        y = by + fd.ascender_d * size
    else:
        bx, by = 0.0, 0.0
        y = 0.0
    word_idx = 0
    gi = 0
    for li, line in enumerate(lines):
        if box and y > by + float(doc.box_text_size[1]) + 1e-3:
            break  # like AE, paragraph text that overflows the box is hidden
        visible = line
        while visible and visible[-1][3] == " ":
            visible = visible[:-1]
        width = sum(a for _, a, _, _ in visible) - (tracking if visible else 0)
        if box:
            bw = box_w or 0
            name = getattr(just, "name", "")
            x = bx + (bw - width if "RIGHT" in name else (bw - width) / 2 if "CENTER" in name else 0)
        else:
            x = _justify_offset(just, width)
        start = gi
        prev_space = True
        for gid, adv, ci, ch in line:
            if ch == " ":
                prev_space = True
            elif prev_space:
                word_idx += 1
                prev_space = False
            out.glyphs.append(Glyph(gid, ch, x, y - shift, adv, li, word_idx - 1, ci, fd, size))
            x += adv
            gi += 1
        out.lines.append((start, gi))
        y += leading
    return out


# ----------------------------------------------------------------------- animators

def _bezier_ease(x1: float, y1: float, x2: float, y2: float):
    if x1 == y1 and x2 == y2:
        return lambda t: t

    def bx(t):
        return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3

    def by(t):
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3

    def f(x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            if bx(mid) < x:
                lo = mid
            else:
                hi = mid
        return by((lo + hi) / 2)

    return f


def _p(ev: Evaluator, group: Any, mn: str, t: float, default: Any) -> Any:
    try:
        p = group.property(mn)
    except Exception:
        p = None
    if p is None or not getattr(p, "enabled", True):
        return default
    return ev.value(p, t)


def _selector_values(ev: Evaluator, sel: Any, t: float, units: list[int], total: int) -> list[float]:
    """Evaluate one range selector for each unit index; returns a list of [0..1] amounts."""
    mn = sel.match_name
    adv = sel.property("ADBE Text Range Advanced") if mn == "ADBE Text Selector" else None
    if mn == "ADBE Text Expressible Selector":
        # the Amount expression runs once per unit (textIndex is 1-based)
        prop = sel.property("ADBE Text Expressible Amount")
        if prop is None:
            return [1.0] * len(units)
        cache: dict[int, float] = {}
        out = []
        for u in units:
            if u not in cache:
                v = ev.value_with(prop, t, {"textIndex": u + 1, "textTotal": total, "selectorValue": [100, 100, 100]})
                v = v[0] if isinstance(v, (list, tuple)) else v
                cache[u] = max(-1.0, min(1.0, float(v) / 100.0))
            out.append(cache[u])
        return out
    if mn != "ADBE Text Selector":
        # wiggly selectors not supported: fully selected
        return [1.0] * len(units)
    s = _p(ev, sel, "ADBE Text Percent Start", t, 0.0)
    e = _p(ev, sel, "ADBE Text Percent End", t, 100.0)
    o = _p(ev, sel, "ADBE Text Percent Offset", t, 0.0)
    unit_type = int(_p(ev, adv, "ADBE Text Range Units", t, 1)) if adv else 1
    if unit_type == 2:
        s = _p(ev, sel, "ADBE Text Index Start", t, 0.0)
        e = _p(ev, sel, "ADBE Text Index End", t, float(total))
        o = _p(ev, sel, "ADBE Text Index Offset", t, 0.0)
        s, e = s + o, e + o
    else:
        s = (s + o) / 100.0 * total
        e = (e + o) / 100.0 * total
    if s > e:
        s, e = e, s
    amount = (_p(ev, adv, "ADBE Text Selector Max Amount", t, 100.0) if adv else 100.0) / 100.0
    shape = int(_p(ev, adv, "ADBE Text Range Shape", t, 1)) if adv else 1
    smooth = (_p(ev, adv, "ADBE Text Selector Smoothness", t, 100.0) if adv else 100.0) / 100.0
    ne = _p(ev, adv, "ADBE Text Levels Min Ease", t, 0.0) if adv else 0.0
    xe = _p(ev, adv, "ADBE Text Levels Max Ease", t, 0.0) if adv else 0.0
    x1, y1, x2, y2 = 0.0, 0.0, 1.0, 1.0
    if ne > 0:
        x1 = ne / 100.0
    else:
        y1 = -ne / 100.0
    if xe > 0:
        x2 = 1 - xe / 100.0
    else:
        y2 = 1 + xe / 100.0
    ease = _bezier_ease(x1, y1, x2, y2)
    out = []
    for ind in units:
        if shape == 2:  # ramp up
            m = (1.0 if ind >= e else 0.0) if e == s else max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
            m = ease(m)
        elif shape == 3:  # ramp down
            m = (0.0 if ind >= e else 1.0) if e == s else 1 - max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
            m = ease(m)
        elif shape == 4:  # triangle
            if e == s:
                m = 0.0
            else:
                m = max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
                m = m * 2 if m < 0.5 else 1 - 2 * (m - 0.5)
            m = ease(m)
        elif shape == 5:  # round
            if e == s:
                m = 0.0
            else:
                tot = e - s
                k = min(max(0.0, ind + 0.5 - s), tot)
                x = -tot / 2 + k
                a = tot / 2
                m = math.sqrt(max(0.0, 1 - (x * x) / (a * a)))
            m = ease(m)
        elif shape == 6:  # smooth
            if e == s:
                m = 0.0
            else:
                k = min(max(0.0, ind + 0.5 - s), e - s)
                m = (1 + math.cos(math.pi + math.pi * 2 * k / (e - s))) / 2
            m = ease(m)
        else:  # square
            m = 0.0
            if ind >= math.floor(s):
                if ind - s < 0:
                    m = max(0.0, min(min(e, ind + 1) - s, 1.0))
                else:
                    m = max(0.0, min(e - ind, 1.0))
            if smooth < 1 and 0 < m < 1:
                m = ease(m)
        out.append(m * amount)
    return out


@dataclass
class CharState:
    opacity: float = 1.0
    pos: tuple = (0.0, 0.0)
    scale: tuple = (1.0, 1.0)
    rotation: float = 0.0
    skew: float = 0.0
    fill: Any = None
    stroke: Any = None
    tracking: float = 0.0
    anchor: tuple = (0.0, 0.0)
    blur: float = 0.0


def animate(ev: Evaluator, layer: Any, layout: TextLayout, t: float, tracking_only: bool = False) -> list[CharState]:
    n = len(layout.glyphs)
    states = [CharState() for _ in range(n)]
    try:
        anims = layer.property("ADBE Text Properties").property("ADBE Text Animators")
    except Exception:
        return states
    if anims is None:
        return states
    text_len = (max(g.index for g in layout.glyphs) + 1) if layout.glyphs else 0
    for anim in anims:
        if not getattr(anim, "enabled", True) or not is_group(anim):
            continue
        props = anim.property("ADBE Text Animator Properties")
        sels = anim.property("ADBE Text Selectors")
        has_tracking = props is not None and any(p.match_name == "ADBE Text Tracking Amount" and not is_synthetic(p) for p in props)
        if tracking_only and not has_tracking:
            continue
        # selection per glyph
        sel_amount = [1.0] * n
        first = True
        for sel in (sels or []):
            if not getattr(sel, "enabled", True):
                continue
            adv = sel.property("ADBE Text Range Advanced") if sel.match_name == "ADBE Text Selector" else None
            if sel.match_name == "ADBE Text Expressible Selector":  # its "Based On" sits on the selector itself
                based = int(_p(ev, sel, "ADBE Text Range Type2", t, 1))
            else:
                based = int(_p(ev, adv, "ADBE Text Range Type2", t, 1)) if adv else 1
            if based == 1:
                units = [g.index for g in layout.glyphs]
                total = text_len
            elif based == 2:  # characters excluding spaces
                idx, units = -1, []
                for g in layout.glyphs:
                    if g.char.strip():
                        idx += 1
                    units.append(max(idx, 0))
                total = idx + 1
            elif based == 3:
                units = [g.word for g in layout.glyphs]
                total = (max(units) + 1) if units else 0
            else:
                units = [g.line for g in layout.glyphs]
                total = len(layout.lines)
            vals = _selector_values(ev, sel, t, units, max(total, 1))
            mode = int(_p(ev, adv, "ADBE Text Selector Mode", t, 1)) if adv else 1
            if first:
                sel_amount = vals if mode != 2 else [1 - v for v in vals]
                first = False
            elif mode == 1:  # add
                sel_amount = [min(1.0, a + b) for a, b in zip(sel_amount, vals)]
            elif mode == 2:  # subtract
                sel_amount = [max(0.0, a - b) for a, b in zip(sel_amount, vals)]
            elif mode == 3:  # intersect
                sel_amount = [a * b for a, b in zip(sel_amount, vals)]
            elif mode == 4:  # min
                sel_amount = [min(a, b) for a, b in zip(sel_amount, vals)]
            elif mode == 5:  # max
                sel_amount = [max(a, b) for a, b in zip(sel_amount, vals)]
            elif mode == 6:  # difference
                sel_amount = [abs(a - b) for a, b in zip(sel_amount, vals)]
        if props is None:
            continue
        for p in props:
            if not getattr(p, "enabled", True) or is_group(p) or is_synthetic(p):
                continue
            mn = p.match_name
            v = ev.value(p, t)
            for k, st in enumerate(states):
                s = sel_amount[k]
                if s == 0:
                    continue
                if mn == "ADBE Text Opacity":
                    st.opacity *= 1 + s * (v / 100.0 - 1)
                elif mn == "ADBE Text Position 3D":
                    st.pos = (st.pos[0] + s * v[0], st.pos[1] + s * v[1])
                elif mn == "ADBE Text Scale 3D":
                    st.scale = (st.scale[0] * (1 + s * (v[0] / 100.0 - 1)), st.scale[1] * (1 + s * (v[1] / 100.0 - 1)))
                elif mn in ("ADBE Text Rotation",):
                    st.rotation += s * v
                elif mn == "ADBE Text Skew":
                    st.skew += s * v
                elif mn == "ADBE Text Fill Color":
                    base = st.fill if st.fill is not None else list(layout.style.fill_color)
                    st.fill = [b + s * (c - b) for b, c in zip(base[:3], v[:3])]
                elif mn == "ADBE Text Stroke Color":
                    base = st.stroke if st.stroke is not None else list(layout.style.stroke_color)
                    st.stroke = [b + s * (c - b) for b, c in zip(base[:3], v[:3])]
                elif mn == "ADBE Text Tracking Amount":
                    st.tracking += s * v / 1000.0 * layout.glyphs[k].size
                elif mn == "ADBE Text Anchor Point 3D":
                    st.anchor = (st.anchor[0] + s * v[0], st.anchor[1] + s * v[1])
                elif mn == "ADBE Text Blur":
                    st.blur += s * (v[0] if isinstance(v, list) else v)
    return states


class TextRenderer:
    def __init__(self, ev: Evaluator):
        self.ev = ev
        self._skfonts: dict[tuple[str, float], skia.Font] = {}

    def _skfont(self, fd: FontData, size: float) -> skia.Font:
        key = (fd.path + str(fd.index), size)
        f = self._skfonts.get(key)
        if f is None:
            f = skia.Font(fd.typeface, size)
            f.setEdging(skia.Font.Edging.kAntiAlias)
            f.setHinting(skia.FontHinting.kNone)
            f.setSubpixel(True)
            f.setLinearMetrics(True)
            self._skfonts[key] = f
        return f

    def layout(self, layer: Any, t: float) -> tuple[TextLayout, list[CharState]]:
        tv = self.ev.value(layer.property("ADBE Text Properties").property("ADBE Text Document"), t)
        lay = layout_text(tv)
        # tracking animators change the layout; re-layout with per-char extra tracking
        states = animate(self.ev, layer, lay, t)
        if any(s.tracking for s in states):
            extra = [0.0] * (max((g.index for g in lay.glyphs), default=-1) + 1)
            for g, s in zip(lay.glyphs, states):
                extra[g.index] = s.tracking
            lay = layout_text(tv, extra)
            states = animate(self.ev, layer, lay, t)
        return lay, states

    def bounds(self, layer: Any, t: float) -> skia.Rect:
        """Ink bounds in layer space (like sourceRectAtTime without animators)."""
        tv = self.ev.value(layer.property("ADBE Text Properties").property("ADBE Text Document"), t)
        lay = layout_text(tv)
        acc = skia.Rect.MakeEmpty()
        for g in lay.glyphs:
            if not g.char.strip():
                continue
            f = self._skfont(g.font, g.size)
            b = f.getBounds([g.gid])[0]
            acc.join(skia.Rect.MakeLTRB(g.x + b.left(), g.y + b.top(), g.x + b.right(), g.y + b.bottom()))
        doc = tv.doc
        if getattr(doc, "apply_stroke", False) and doc.stroke_width:
            pad = doc.stroke_width / 2
            acc = skia.Rect.MakeLTRB(acc.left() - pad, acc.top() - pad, acc.right() + pad, acc.bottom() + pad)
        return acc

    def draw(self, canvas: skia.Canvas, layer: Any, t: float) -> None:
        lay, states = self.layout(layer, t)
        doc = lay.style
        fill_on = getattr(doc, "apply_fill", True) is not False
        stroke_on = bool(getattr(doc, "apply_stroke", False)) and (doc.stroke_width or 0) > 0
        stroke_over = getattr(doc, "stroke_over_fill", True)
        for g, st in zip(lay.glyphs, states):
            if not g.char.strip() or st.opacity <= 0.0005:
                continue
            f = self._skfont(g.font, g.size)
            path = f.getPath(g.gid)
            if path is None:
                continue
            m = skia.Matrix()
            # character anchor: horizontal centre of the advance on the baseline
            cx = g.x + g.advance / 2
            m.preTranslate(cx + st.pos[0], g.y + st.pos[1])
            if st.rotation:
                m.preRotate(st.rotation)
            if st.skew:
                m.preConcat(skia.Matrix.MakeAll(1, -math.tan(math.radians(st.skew)), 0, 0, 1, 0, 0, 0, 1))
            if st.scale != (1.0, 1.0):
                m.preScale(st.scale[0], st.scale[1])
            m.preTranslate(-g.advance / 2 - st.anchor[0], -st.anchor[1])
            p = skia.Path(path)
            p.transform(m)
            if fill_on and stroke_on:
                ops = ["fill", "stroke"] if stroke_over else ["stroke", "fill"]
            else:
                ops = ["fill"] if fill_on else ["stroke"] if stroke_on else []
            for op in ops:
                paint = skia.Paint(AntiAlias=True)
                if op == "fill":
                    col = st.fill if st.fill is not None else doc.fill_color
                else:
                    col = st.stroke if st.stroke is not None else doc.stroke_color
                    paint.setStyle(skia.Paint.kStroke_Style)
                    paint.setStrokeWidth(doc.stroke_width)
                    paint.setStrokeJoin(skia.Paint.kRound_Join)
                paint.setColor4f(color4f(list(col)[:3], min(1.0, st.opacity)))
                if st.blur > 0:
                    paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, st.blur / 2))
                canvas.drawPath(p, paint)
