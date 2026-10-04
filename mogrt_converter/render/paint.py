"""Colors and gradients."""

from __future__ import annotations

import math
from typing import Any

import skia


def color4f(c: Any, alpha: float = 1.0) -> skia.Color4f:
    c = list(c) + [1.0] * (4 - len(c))
    return skia.Color4f(float(c[0]), float(c[1]), float(c[2]), float(c[3]) * alpha)


def _interp_stops(stops: list[tuple[float, float, Any]], x: float, lerp) -> Any:
    """stops: (offset, midpoint, value). AE midpoint: position where the mix is 50%."""
    if x <= stops[0][0]:
        return stops[0][2]
    if x >= stops[-1][0]:
        return stops[-1][2]
    for (o0, m0, v0), (o1, _m1, v1) in zip(stops, stops[1:]):
        if o0 <= x <= o1:
            u = 0.0 if o1 == o0 else (x - o0) / (o1 - o0)
            m = min(max(m0, 0.01), 0.99)
            if abs(m - 0.5) > 1e-4 and u > 0:
                u = u ** (math.log(0.5) / math.log(m))
            return lerp(v0, v1, u)
    return stops[-1][2]


def gradient_stops(grad: Any) -> tuple[list[skia.Color4f], list[float]]:
    cs = sorted([(s.offset, s.midpoint, tuple(s.color[:3])) for s in grad.color_stops])
    al = sorted([(s.offset, s.midpoint, s.alpha) for s in grad.alpha_stops]) or [(0.0, 0.5, 1.0), (1.0, 0.5, 1.0)]
    positions = {0.0, 1.0}
    for st in (cs, al):
        positions.update(o for o, _, _ in st)
        for (o0, m0, _), (o1, _, _) in zip(st, st[1:]):
            if abs(m0 - 0.5) > 1e-4:
                positions.update(o0 + (o1 - o0) * k / 16 for k in range(1, 16))
    pos = sorted(p for p in positions if 0 <= p <= 1)
    lerp3 = lambda a, b, u: tuple(x + (y - x) * u for x, y in zip(a, b))
    lerp1 = lambda a, b, u: a + (b - a) * u
    colors = []
    for p in pos:
        c = _interp_stops(cs, p, lerp3)
        a = _interp_stops(al, p, lerp1)
        colors.append(skia.Color4f(c[0], c[1], c[2], a))
    return colors, pos


def gradient_shader(kind: int, start, end, hl_len: float, hl_angle: float, grad: Any) -> skia.Shader | None:
    if grad is None:
        return None
    colors, pos = gradient_stops(grad)
    cols = [c.toColor() for c in colors]
    if kind == 2:
        r = math.hypot(end[0] - start[0], end[1] - start[1])
        if r <= 0:
            return None
        base = math.atan2(end[1] - start[1], end[0] - start[0])
        hl = max(min(hl_len, 99.0), -99.0) / 100.0
        a = base + math.radians(hl_angle)
        focal = (start[0] + math.cos(a) * r * hl, start[1] + math.sin(a) * r * hl)
        if abs(hl) < 1e-4:
            return skia.GradientShader.MakeRadial(skia.Point(start[0], start[1]), r, cols, pos)
        return skia.GradientShader.MakeTwoPointConical(
            skia.Point(*focal), 0, skia.Point(start[0], start[1]), r, cols, pos
        )
    return skia.GradientShader.MakeLinear(
        [skia.Point(start[0], start[1]), skia.Point(end[0], end[1])], cols, pos
    )
