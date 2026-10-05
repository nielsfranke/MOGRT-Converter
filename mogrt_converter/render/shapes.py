"""Shape layer contents: AE's group/path/modifier/paint stacking model."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import skia

from ..evaluator import Evaluator, is_group, is_synthetic
from . import paths as P
from .paint import gradient_shader, color4f


@dataclass
class Entry:
    geo: P.Geo


@dataclass
class PaintItem:
    prop: Any
    refs: list[tuple[Entry, skia.Matrix]]


@dataclass
class GroupNode:
    matrix: skia.Matrix
    opacity: float
    items: list[Any] = field(default_factory=list)  # PaintItem | GroupNode (AE order, top first)


def _v(ev: Evaluator, group: Any, match: str, t: float, default: Any = None) -> Any:
    try:
        p = group.property(match)
    except Exception:
        p = None
    if p is None:
        return default
    return ev.value(p, t)


def group_matrix(ev: Evaluator, tr: Any, t: float) -> tuple[skia.Matrix, float]:
    if tr is None:
        return skia.Matrix(), 1.0
    a = _v(ev, tr, "ADBE Vector Anchor", t, [0, 0])
    p = _v(ev, tr, "ADBE Vector Position", t, [0, 0])
    s = _v(ev, tr, "ADBE Vector Scale", t, [100, 100])
    r = _v(ev, tr, "ADBE Vector Rotation", t, 0.0)
    sk = _v(ev, tr, "ADBE Vector Skew", t, 0.0)
    ska = _v(ev, tr, "ADBE Vector Skew Axis", t, 0.0)
    op = _v(ev, tr, "ADBE Vector Group Opacity", t, 100.0)
    m = skia.Matrix()
    m.preTranslate(p[0], p[1])
    m.preRotate(r)
    if sk:
        m.preRotate(ska)
        m.preConcat(skia.Matrix.MakeAll(1, -math.tan(math.radians(sk)), 0, 0, 1, 0, 0, 0, 1))
        m.preRotate(-ska)
    m.preScale(s[0] / 100.0, s[1] / 100.0)
    m.preTranslate(-a[0], -a[1])
    return m, op / 100.0


def _path_geo(ev: Evaluator, item: Any, t: float) -> P.Geo | None:
    mn = item.match_name
    direction = int(_v(ev, item, "ADBE Vector Shape Direction", t, 1) or 1)
    if mn == "ADBE Vector Shape - Group":
        shape = _v(ev, item, "ADBE Vector Shape", t)
        if shape is None:
            return None
        c = P.contour_from_shape(shape)
        if direction == 3:
            c = c.reversed()
        return P.Geo([c])
    if mn == "ADBE Vector Shape - Rect":
        return P.Geo([P.contour_rect(
            _v(ev, item, "ADBE Vector Rect Size", t, [100, 100]),
            _v(ev, item, "ADBE Vector Rect Position", t, [0, 0]),
            _v(ev, item, "ADBE Vector Rect Roundness", t, 0.0),
            direction)])
    if mn == "ADBE Vector Shape - Ellipse":
        return P.Geo([P.contour_ellipse(
            _v(ev, item, "ADBE Vector Ellipse Size", t, [100, 100]),
            _v(ev, item, "ADBE Vector Ellipse Position", t, [0, 0]),
            direction)])
    if mn == "ADBE Vector Shape - Star":
        return P.Geo([P.contour_star(
            int(_v(ev, item, "ADBE Vector Star Type", t, 1)),
            _v(ev, item, "ADBE Vector Star Points", t, 5),
            _v(ev, item, "ADBE Vector Star Position", t, [0, 0]),
            _v(ev, item, "ADBE Vector Star Rotation", t, 0.0),
            _v(ev, item, "ADBE Vector Star Inner Radius", t, 50.0),
            _v(ev, item, "ADBE Vector Star Outer Radius", t, 100.0),
            _v(ev, item, "ADBE Vector Star Inner Roundess", t, 0.0),
            _v(ev, item, "ADBE Vector Star Outer Roundess", t, 0.0),
            direction)])
    return None


PAINTS = {
    "ADBE Vector Graphic - Fill",
    "ADBE Vector Graphic - Stroke",
    "ADBE Vector Graphic - G-Fill",
    "ADBE Vector Graphic - G-Stroke",
}

_unsupported_warned: set[str] = set()


def build_group(ev: Evaluator, contents: Any, matrix: skia.Matrix, opacity: float, t: float) -> tuple[GroupNode, list[tuple[Entry, skia.Matrix]]]:
    """Evaluate a vector group. Returns the node and its geometry entries (in this group's space)."""
    node = GroupNode(matrix, opacity)
    entries: list[tuple[Entry, skia.Matrix]] = []
    for item in contents:
        if not getattr(item, "enabled", True):
            continue
        mn = item.match_name
        if mn == "ADBE Vector Group":
            sub_contents = item.property("ADBE Vectors Group")
            m, op = group_matrix(ev, item.property("ADBE Vector Transform Group"), t)
            child, child_entries = build_group(ev, sub_contents, m, op, t)
            node.items.append(child)
            for e, em in child_entries:
                mm = skia.Matrix.Concat(m, em)
                entries.append((e, mm))
        elif mn.startswith("ADBE Vector Shape"):
            g = _path_geo(ev, item, t)
            if g is not None:
                entries.append((Entry(g), skia.Matrix()))
        elif mn in PAINTS:
            node.items.append(PaintItem(item, list(entries)))
        elif mn == "ADBE Vector Filter - Trim":
            s = _v(ev, item, "ADBE Vector Trim Start", t, 0.0)
            e = _v(ev, item, "ADBE Vector Trim End", t, 100.0)
            o = _v(ev, item, "ADBE Vector Trim Offset", t, 0.0)
            ind = int(_v(ev, item, "ADBE Vector Trim Type", t, 1)) == 2
            _apply(entries, lambda geos: P.trim(geos, s, e, o, ind))
        elif mn == "ADBE Vector Filter - RC":
            r = _v(ev, item, "ADBE Vector RoundCorner Radius", t, 0.0)
            _apply(entries, lambda geos: [P.round_corners(g, r) for g in geos])
        elif mn == "ADBE Vector Filter - Merge":
            mode = int(_v(ev, item, "ADBE Vector Merge Type", t, 2))
            merged = P.merge([g.transformed(m).to_path() for (en, m) in entries for g in [en.geo]], mode)
            for en, _ in entries:
                en.geo = P.Geo(path=skia.Path())
            # AE: paints above a Merge Paths in the same group are disabled
            node.items = [it for it in node.items if not isinstance(it, PaintItem)]
            entries = [(Entry(P.Geo(path=merged)), skia.Matrix())]
        elif mn == "ADBE Vector Filter - Offset":
            amt = _v(ev, item, "ADBE Vector Offset Amount", t, 0.0)
            join = int(_v(ev, item, "ADBE Vector Offset Line Join", t, 1))
            miter = _v(ev, item, "ADBE Vector Offset Miter Limit", t, 4.0)
            _apply(entries, lambda geos: [P.offset(g, amt, join, miter) for g in geos])
        elif mn == "ADBE Vector Filter - PB":
            amt = _v(ev, item, "ADBE Vector PuckerBloat Amount", t, 0.0)
            _apply(entries, lambda geos: [P.pucker_bloat(g, amt) for g in geos])
        elif mn == "ADBE Vector Filter - Zigzag":
            size = _v(ev, item, "ADBE Vector Zigzag Size", t, 10.0)
            ridges = _v(ev, item, "ADBE Vector Zigzag Detail", t, 5.0)
            smooth = int(_v(ev, item, "ADBE Vector Zigzag Points", t, 1) or 1) == 2
            _apply(entries, lambda geos: [P.zigzag(g, size, ridges, smooth) for g in geos])
        elif mn == "ADBE Vector Filter - Repeater":
            node.items, entries = _repeat(ev, item, node.items, entries, t)
        elif mn in ("ADBE Vector Transform Group", "ADBE Vector Materials Group"):
            pass
        else:
            if mn not in _unsupported_warned:
                _unsupported_warned.add(mn)
                import sys
                print(f"WARNUNG: Shape-Element '{mn}' wird noch nicht unterstützt", file=sys.stderr)
    return node, entries


def _repeater_matrix(anchor, pos, scale, rot, k: float) -> skia.Matrix:
    """The repeater transform applied k times (fractional k interpolates the last step)."""
    def step(f: float) -> skia.Matrix:
        m = skia.Matrix()
        m.preTranslate(pos[0] * f + anchor[0], pos[1] * f + anchor[1])
        m.preRotate(rot * f)
        m.preScale((scale[0] / 100.0) ** f if scale[0] > 0 else 0.0, (scale[1] / 100.0) ** f if scale[1] > 0 else 0.0)
        m.preTranslate(-anchor[0], -anchor[1])
        return m

    whole = int(math.floor(abs(k)))
    frac = abs(k) - whole
    one = step(1.0)
    if k < 0:
        inv = skia.Matrix()
        one = inv if one.invert(inv) else skia.Matrix()
    m = skia.Matrix()
    for _ in range(whole):
        m = skia.Matrix.Concat(one, m)
    if frac > 1e-9:
        m = skia.Matrix.Concat(step(frac if k > 0 else -frac), m)
    return m


def _repeat(ev: Evaluator, item: Any, items: list[Any], entries: list[tuple[Entry, skia.Matrix]], t: float):
    """AE Repeater: copies of everything above it in the group (geometry and paints)."""
    copies = _v(ev, item, "ADBE Vector Repeater Copies", t, 3.0)
    offset = _v(ev, item, "ADBE Vector Repeater Offset", t, 0.0)
    above = int(_v(ev, item, "ADBE Vector Repeater Order", t, 1) or 1) == 1
    tr = item.property("ADBE Vector Repeater Transform")
    anchor = _v(ev, tr, "ADBE Vector Repeater Anchor", t, [0, 0])
    pos = _v(ev, tr, "ADBE Vector Repeater Position", t, [100, 0])
    scale = _v(ev, tr, "ADBE Vector Repeater Scale", t, [100, 100])
    rot = _v(ev, tr, "ADBE Vector Repeater Rotation", t, 0.0)
    op1 = _v(ev, tr, "ADBE Vector Repeater Opacity 1", t, 100.0) / 100.0
    op2 = _v(ev, tr, "ADBE Vector Repeater Opacity 2", t, 100.0) / 100.0
    n = int(math.ceil(copies - 1e-9)) if copies > 0 else 0
    new_items, new_entries = [], []
    for i in range(n):
        m = _repeater_matrix(anchor, pos, scale, rot, i + offset)
        op = op1 + (op2 - op1) * (i / (n - 1) if n > 1 else 0.0)
        if i == n - 1 and copies < n:  # fractional last copy fades in
            op *= copies - (n - 1)
        new_items.append(GroupNode(m, op, list(items)))
        new_entries += [(en, skia.Matrix.Concat(m, em)) for en, em in entries]
    if above:
        new_items.reverse()  # items are top first: the last copy ends up on top
    return new_items, new_entries


def _apply(entries: list[tuple[Entry, skia.Matrix]], fn) -> None:
    """Apply a modifier to all entries (in this group's space) and write results back."""
    if not entries:
        return
    geos = [en.geo.transformed(m) for en, m in entries]
    new = fn(geos)
    for (en, m), g in zip(entries, new):
        if m.isIdentity():
            en.geo = g
            continue
        inv = skia.Matrix()
        if m.invert(inv):
            en.geo = g.transformed(inv)
        else:
            en.geo = P.Geo(path=skia.Path())


_CAPS = {1: skia.Paint.kButt_Cap, 2: skia.Paint.kRound_Cap, 3: skia.Paint.kSquare_Cap}
_JOINS = {1: skia.Paint.kMiter_Join, 2: skia.Paint.kRound_Join, 3: skia.Paint.kBevel_Join}


def _paint_for(ev: Evaluator, item: Any, t: float, opacity: float) -> tuple[skia.Paint | None, bool]:
    mn = item.match_name
    paint = skia.Paint(AntiAlias=True)
    even_odd = False
    stroke = mn in ("ADBE Vector Graphic - Stroke", "ADBE Vector Graphic - G-Stroke")
    gradient = mn in ("ADBE Vector Graphic - G-Fill", "ADBE Vector Graphic - G-Stroke")
    if stroke:
        prefix = "ADBE Vector Stroke"
        width = _v(ev, item, "ADBE Vector Stroke Width", t, 2.0)
        if width <= 0:
            return None, False
        paint.setStyle(skia.Paint.kStroke_Style)
        paint.setStrokeWidth(width)
        paint.setStrokeCap(_CAPS.get(int(_v(ev, item, "ADBE Vector Stroke Line Cap", t, 1)), skia.Paint.kButt_Cap))
        paint.setStrokeJoin(_JOINS.get(int(_v(ev, item, "ADBE Vector Stroke Line Join", t, 1)), skia.Paint.kMiter_Join))
        paint.setStrokeMiter(_v(ev, item, "ADBE Vector Stroke Miter Limit", t, 4.0))
        dashes = None
        try:
            dashes = item.property("ADBE Vector Stroke Dashes")
        except Exception:
            pass
        if dashes is not None:
            intervals, phase = [], 0.0
            for d in dashes:
                if not getattr(d, "enabled", True) or is_group(d) or is_synthetic(d):
                    continue
                val = ev.value(d, t)
                if d.match_name.startswith("ADBE Vector Stroke Offset"):
                    phase = val
                else:
                    intervals.append(max(val, 0.01))
            if intervals:
                if len(intervals) % 2:
                    intervals.append(intervals[-1])
                paint.setPathEffect(skia.DashPathEffect.Make(intervals, phase))
        op_name = "ADBE Vector Stroke Opacity"
    else:
        rule = int(_v(ev, item, "ADBE Vector Fill Rule", t, 1))
        paint.setStyle(skia.Paint.kFill_Style)
        op_name = "ADBE Vector Fill Opacity"
        even_odd = rule == 2
    alpha = _v(ev, item, op_name, t, 100.0) / 100.0 * opacity
    if alpha <= 0:
        return None, False
    if gradient:
        shader = gradient_shader(
            int(_v(ev, item, "ADBE Vector Grad Type", t, 1)),
            _v(ev, item, "ADBE Vector Grad Start Pt", t, [0, 0]),
            _v(ev, item, "ADBE Vector Grad End Pt", t, [100, 0]),
            _v(ev, item, "ADBE Vector Grad HiLite Length", t, 0.0),
            _v(ev, item, "ADBE Vector Grad HiLite Angle", t, 0.0),
            _v(ev, item, "ADBE Vector Grad Colors", t),
        )
        paint.setShader(shader)
        paint.setAlphaf(alpha)
    else:
        col = _v(ev, item, "ADBE Vector Stroke Color" if stroke else "ADBE Vector Fill Color", t, [1, 1, 1, 1])
        paint.setColor4f(color4f(col, alpha))
    return paint, even_odd


def draw_group(ev: Evaluator, canvas: skia.Canvas, node: GroupNode, t: float, opacity: float = 1.0) -> None:
    canvas.save()
    canvas.concat(node.matrix)
    op = opacity * node.opacity
    for it in reversed(node.items):
        if isinstance(it, GroupNode):
            draw_group(ev, canvas, it, t, op)
            continue
        if not it.refs:
            continue
        paint, even_odd = _paint_for(ev, it.prop, t, op)
        if paint is None:
            continue
        path = skia.Path()
        for en, m in it.refs:
            if en.geo.is_empty():
                continue
            path.addPath(en.geo.to_path(), m)
        if even_odd:
            path.setFillType(skia.PathFillType.kEvenOdd)
        canvas.drawPath(path, paint)
    canvas.restore()


def shape_layer_root(ev: Evaluator, layer: Any, t: float) -> GroupNode:
    root = layer.property("ADBE Root Vectors Group")
    node, _ = build_group(ev, root, skia.Matrix(), 1.0, t)
    return node


def draw_shape_layer(ev: Evaluator, canvas: skia.Canvas, layer: Any, t: float) -> None:
    draw_group(ev, canvas, shape_layer_root(ev, layer, t), t)


def shape_bounds(ev: Evaluator, layer: Any, t: float) -> skia.Rect:
    """Union of geometry bounds (for sourceRectAtTime / effect buffers), incl. stroke widths."""
    node = shape_layer_root(ev, layer, t)
    acc = skia.Rect.MakeEmpty()

    def walk(n: GroupNode, m: skia.Matrix) -> None:
        mm = skia.Matrix.Concat(m, n.matrix)
        for it in n.items:
            if isinstance(it, GroupNode):
                walk(it, mm)
            else:
                pad = 0.0
                if it.prop.match_name in ("ADBE Vector Graphic - Stroke", "ADBE Vector Graphic - G-Stroke"):
                    pad = _v(ev, it.prop, "ADBE Vector Stroke Width", t, 0.0) / 2
                for en, em in it.refs:
                    if en.geo.is_empty():
                        continue
                    p = skia.Path(en.geo.to_path())
                    full = skia.Matrix.Concat(mm, em)
                    p.transform(full)
                    b = p.computeTightBounds()
                    b = skia.Rect.MakeLTRB(b.left() - pad, b.top() - pad, b.right() + pad, b.bottom() + pad)
                    acc.join(b)

    walk(node, skia.Matrix())
    return acc
