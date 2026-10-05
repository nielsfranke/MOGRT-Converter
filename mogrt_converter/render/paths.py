"""Bezier contour geometry and AE shape modifiers (trim, round corners, merge, offset)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import skia

KAPPA = 0.5519150244935105  # AE/lottie circle approximation constant


@dataclass
class Contour:
    """Cubic bezier contour. Tangents are relative to their vertex (like AE)."""

    v: np.ndarray  # (n, 2)
    i: np.ndarray  # in tangents, relative
    o: np.ndarray  # out tangents, relative
    closed: bool

    def copy(self) -> "Contour":
        return Contour(self.v.copy(), self.i.copy(), self.o.copy(), self.closed)

    def reversed(self) -> "Contour":
        """Reverse direction, keeping the first vertex first (AE semantics)."""
        n = len(self.v)
        if n == 0:
            return self.copy()
        idx = [0] + list(range(n - 1, 0, -1)) if self.closed else list(range(n - 1, -1, -1))
        return Contour(self.v[idx].copy(), self.o[idx].copy(), self.i[idx].copy(), self.closed)

    def transformed(self, m: skia.Matrix) -> "Contour":
        if m.isIdentity():
            return self.copy()
        a = np.array([[m.getScaleX(), m.getSkewX()], [m.getSkewY(), m.getScaleY()]])
        tr = np.array([m.getTranslateX(), m.getTranslateY()])
        if m.hasPerspective():
            raise ValueError("perspective not supported for contours")
        return Contour(self.v @ a.T + tr, self.i @ a.T, self.o @ a.T, self.closed)


@dataclass
class Geo:
    """Geometry of one path item: list of contours, or a skia path once contours are lost."""

    contours: list[Contour] = field(default_factory=list)
    path: skia.Path | None = None

    def copy(self) -> "Geo":
        return Geo([c.copy() for c in self.contours], skia.Path(self.path) if self.path is not None else None)

    def to_path(self) -> skia.Path:
        if self.path is not None:
            return self.path
        p = skia.Path()
        for c in self.contours:
            add_contour(p, c)
        return p

    def transformed(self, m: skia.Matrix) -> "Geo":
        if self.path is not None:
            p = skia.Path(self.path)
            p.transform(m)
            return Geo(path=p)
        return Geo([c.transformed(m) for c in self.contours])

    def is_empty(self) -> bool:
        if self.path is not None:
            return self.path.isEmpty()
        return not any(len(c.v) for c in self.contours)


def add_contour(p: skia.Path, c: Contour) -> None:
    n = len(c.v)
    if n == 0:
        return
    p.moveTo(*c.v[0])
    segs = n if c.closed else n - 1
    for k in range(segs):
        a = k
        b = (k + 1) % n
        c1 = c.v[a] + c.o[a]
        c2 = c.v[b] + c.i[b]
        if not c.o[a].any() and not c.i[b].any():
            p.lineTo(*c.v[b])
        else:
            p.cubicTo(c1[0], c1[1], c2[0], c2[1], c.v[b][0], c.v[b][1])
    if c.closed:
        p.close()


def contour_from_shape(shape) -> Contour:
    v = np.array(shape.vertices, dtype=float).reshape(-1, 2)
    i = np.array(shape.in_tangents, dtype=float).reshape(-1, 2)
    o = np.array(shape.out_tangents, dtype=float).reshape(-1, 2)
    return Contour(v, i, o, bool(shape.closed))


def contour_rect(size, pos, roundness: float, direction: int) -> Contour:
    w, h = size[0] / 2.0, size[1] / 2.0
    x, y = pos[0], pos[1]
    r = min(roundness, w, h)
    if r <= 0:
        v = np.array([[x + w, y - h], [x + w, y + h], [x - w, y + h], [x - w, y - h]])
        z = np.zeros_like(v)
        c = Contour(v, z, z.copy(), True)
    else:
        k = r * (1 - KAPPA)
        v = np.array([
            [x + w, y - h + r], [x + w, y + h - r],
            [x + w - r, y + h], [x - w + r, y + h],
            [x - w, y + h - r], [x - w, y - h + r],
            [x - w + r, y - h], [x + w - r, y - h],
        ])
        i = np.zeros_like(v)
        o = np.zeros_like(v)
        # corners: bottom-right, bottom-left, top-left, top-right
        o[1] = [0, r - k]; i[2] = [r - k, 0]
        o[3] = [-(r - k), 0]; i[4] = [0, r - k]
        o[5] = [0, -(r - k)]; i[6] = [-(r - k), 0]
        o[7] = [r - k, 0]; i[0] = [0, -(r - k)]
        c = Contour(v, i, o, True)
    return c.reversed() if direction == 3 else c


def contour_ellipse(size, pos, direction: int) -> Contour:
    rx, ry = size[0] / 2.0, size[1] / 2.0
    x, y = pos[0], pos[1]
    kx, ky = rx * KAPPA, ry * KAPPA
    v = np.array([[x, y - ry], [x + rx, y], [x, y + ry], [x - rx, y]])
    i = np.array([[-kx, 0], [0, -ky], [kx, 0], [0, ky]])
    o = np.array([[kx, 0], [0, ky], [-kx, 0], [0, -ky]])
    c = Contour(v, i, o, True)
    return c.reversed() if direction == 3 else c


def contour_star(kind: int, points: float, pos, rotation: float, ir: float, orad: float,
                 iround: float, oround: float, direction: int) -> Contour:
    n = int(math.floor(points))
    if n < 2:
        return Contour(np.zeros((0, 2)), np.zeros((0, 2)), np.zeros((0, 2)), True)
    star = kind == 1
    count = n * 2 if star else n
    angle_step = 2 * math.pi / count
    rot = math.radians(rotation) - math.pi / 2
    vs, ins, outs = [], [], []
    for k in range(count):
        outer = (k % 2 == 0) or not star
        rad = orad if outer else ir
        rnd = (oround if outer else iround) / 100.0
        a = rot + k * angle_step
        px, py = pos[0] + rad * math.cos(a), pos[1] + rad * math.sin(a)
        # tangent length as in lottie: perimeter segment / 4 * roundness
        seg = 2 * math.pi * rad / (count * 4) * rnd if rad else 0
        tx, ty = -math.sin(a) * seg, math.cos(a) * seg
        vs.append([px, py]); ins.append([-tx, -ty]); outs.append([tx, ty])
    c = Contour(np.array(vs), np.array(ins), np.array(outs), True)
    return c.reversed() if direction == 3 else c


# --------------------------------------------------------------------------- modifiers

def round_corners(geo: Geo, radius: float) -> Geo:
    if radius <= 0 or geo.path is not None:
        return geo
    out = []
    for c in geo.contours:
        n = len(c.v)
        if n < 2:
            out.append(c.copy())
            continue
        vs, ins, outs = [], [], []
        for k in range(n):
            cur = c.v[k]
            sharp = not c.i[k].any() and not c.o[k].any()
            if not sharp or (not c.closed and (k == 0 or k == n - 1)):
                vs.append(cur); ins.append(c.i[k]); outs.append(c.o[k])
                continue
            prev = c.v[k - 1] if k > 0 else c.v[n - 1]
            nxt = c.v[k + 1] if k < n - 1 else c.v[0]
            d = np.linalg.norm(prev - cur)
            f = min(d / 2, radius) / d if d else 0
            p1 = cur + (prev - cur) * f
            d2 = np.linalg.norm(nxt - cur)
            f2 = min(d2 / 2, radius) / d2 if d2 else 0
            p2 = cur + (nxt - cur) * f2
            vs.append(p1); ins.append(np.zeros(2)); outs.append((cur - p1) * KAPPA)
            vs.append(p2); ins.append((cur - p2) * KAPPA); outs.append(np.zeros(2))
        out.append(Contour(np.array(vs), np.array(ins), np.array(outs), c.closed))
    return Geo(out)


def _trim_segments(s: float, e: float, o: float) -> list[tuple[float, float]]:
    """Normalized (0..1) trim windows, AE/lottie semantics. o in degrees."""
    o = (o % 360) / 360.0
    s = min(max(s / 100.0, 0.0), 1.0) + o
    e = min(max(e / 100.0, 0.0), 1.0) + o
    if s > e:
        s, e = e, s
    s = round(s * 10000) / 10000
    e = round(e * 10000) / 10000
    if s == e:
        return []
    if e - s >= 1.0:
        return [(0.0, 1.0)]
    if e <= 1:
        return [(s, e)]
    if s >= 1:
        return [(s - 1, e - 1)]
    return [(s, 1.0), (0.0, e - 1)]


def _measure_contours(path: skia.Path) -> list[tuple[skia.PathMeasure, float]]:
    out = []
    pm = skia.PathMeasure(path, False)
    while True:
        length = pm.getLength()
        # copy measure state per contour by re-extracting
        seg = skia.Path()
        pm.getSegment(0, length, seg, True)
        if pm.isClosed():
            seg.close()
        out.append((seg, length))
        if not pm.nextContour():
            break
    return out


def _extract(contours: list[tuple[skia.Path, float]], a: float, b: float, dst: skia.Path) -> None:
    """Extract absolute length range [a, b] from a list of contours into dst."""
    pos = 0.0
    for seg_path, length in contours:
        lo, hi = pos, pos + length
        if length > 0 and b > lo and a < hi:
            pm = skia.PathMeasure(seg_path, False)
            pm.getSegment(max(a, lo) - lo, min(b, hi) - lo, dst, True)
        pos = hi


def trim(geos: list[Geo], s: float, e: float, o: float, individually: bool) -> list[Geo]:
    windows = _trim_segments(s, e, o)
    if not windows:
        return [Geo(path=skia.Path()) for _ in geos]
    if windows == [(0.0, 1.0)]:
        return geos
    if not individually:
        out = []
        for g in geos:
            cs = _measure_contours(g.to_path())
            total = sum(l for _, l in cs)
            dst = skia.Path()
            # each contour of a path is trimmed separately in AE (per path item, contours share)
            pos = 0.0
            for seg, length in cs:
                for wa, wb in windows:
                    _extract([(seg, length)], wa * length, wb * length, dst)
                pos += length
            out.append(Geo(path=dst))
        return out
    # individually: treat all paths as one continuous path
    all_cs = []
    owners = []
    for gi, g in enumerate(geos):
        for seg, length in _measure_contours(g.to_path()):
            all_cs.append((seg, length))
            owners.append(gi)
    total = sum(l for _, l in all_cs)
    dsts = [skia.Path() for _ in geos]
    for wa, wb in windows:
        a, b = wa * total, wb * total
        pos = 0.0
        for (seg, length), gi in zip(all_cs, owners):
            lo, hi = pos, pos + length
            if length > 0 and b > lo and a < hi:
                pm = skia.PathMeasure(seg, False)
                pm.getSegment(max(a, lo) - lo, min(b, hi) - lo, dsts[gi], True)
            pos = hi
    return [Geo(path=d) for d in dsts]


_MERGE_OPS = {
    2: skia.PathOp.kUnion_PathOp,
    3: skia.PathOp.kDifference_PathOp,
    4: skia.PathOp.kIntersect_PathOp,
    5: skia.PathOp.kXOR_PathOp,
}


def merge(paths: list[skia.Path], mode: int) -> skia.Path:
    if not paths:
        return skia.Path()
    if mode == 1:
        out = skia.Path()
        for p in paths:
            out.addPath(p)
        return out
    op = _MERGE_OPS.get(mode, skia.PathOp.kUnion_PathOp)
    acc = paths[0]
    for p in paths[1:]:
        r = skia.Op(acc, p, op)
        acc = r if r is not None else acc
    return acc


def offset(geo: Geo, amount: float, join: int, miter: float) -> Geo:
    if abs(amount) < 1e-6:
        return geo
    p = geo.to_path()
    paint = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=abs(amount) * 2)
    paint.setStrokeJoin({1: skia.Paint.kMiter_Join, 2: skia.Paint.kRound_Join, 3: skia.Paint.kBevel_Join}.get(join, skia.Paint.kMiter_Join))
    paint.setStrokeMiter(miter)
    stroked = skia.Path()
    paint.getFillPath(p, stroked)
    # on a self-intersecting path (a figure eight) the stroke bands of loops running in opposite
    # directions cancel where they overlap (winding +1 - 1); adding each segment's own stroke closes
    # those holes (a union never cancels)
    builder = skia.OpBuilder()
    builder.add(stroked, skia.PathOp.kUnion_PathOp)
    for seg in _segments(p):
        piece = skia.Path()
        paint.getFillPath(seg, piece)
        builder.add(piece, skia.PathOp.kUnion_PathOp)
    stroked = builder.resolve() or stroked
    op = skia.PathOp.kUnion_PathOp if amount > 0 else skia.PathOp.kDifference_PathOp
    r = skia.Op(p, stroked, op)
    return Geo(path=r if r is not None else p)


def _segments(p: skia.Path) -> list[skia.Path]:
    """Every line/curve segment of a path as its own open path."""
    out = []
    start = last = None
    it = skia.Path.Iter(p, False)
    while True:
        verb, pts = it.next()
        if verb == skia.Path.Verb.kDone_Verb:
            break
        seg = skia.Path()
        if verb == skia.Path.Verb.kMove_Verb:
            start = last = pts[0]
            continue
        if verb == skia.Path.Verb.kClose_Verb:
            if last is not None and start is not None and (last.x(), last.y()) != (start.x(), start.y()):
                seg.moveTo(last)
                seg.lineTo(start)
                out.append(seg)
            last = start
            continue
        seg.moveTo(pts[0])
        if verb == skia.Path.Verb.kLine_Verb:
            seg.lineTo(pts[1])
        elif verb == skia.Path.Verb.kQuad_Verb:
            seg.quadTo(pts[1], pts[2])
        elif verb == skia.Path.Verb.kConic_Verb:
            seg.conicTo(pts[1], pts[2], it.conicWeight())
        elif verb == skia.Path.Verb.kCubic_Verb:
            seg.cubicTo(pts[1], pts[2], pts[3])
        else:
            continue
        out.append(seg)
        last = pts[-1]
    return out


def pucker_bloat(geo: Geo, amount: float) -> Geo:
    """AE Pucker & Bloat: vertices move to the contour centre, tangents away from it (or reverse)."""
    if geo.path is not None or abs(amount) < 1e-6:
        return geo
    a = amount / 100.0
    out = []
    for c in geo.contours:
        if not len(c.v):
            out.append(c.copy())
            continue
        ctr = c.v.mean(axis=0)
        v = c.v + (ctr - c.v) * a
        o_abs = c.v + c.o
        i_abs = c.v + c.i
        o_abs = o_abs + (ctr - o_abs) * -a
        i_abs = i_abs + (ctr - i_abs) * -a
        out.append(Contour(v, i_abs - v, o_abs - v, c.closed))
    return Geo(out)


def _bez(p0, p1, p2, p3, t):
    u = 1 - t
    pt = u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3
    d = 3 * u * u * (p1 - p0) + 6 * u * t * (p2 - p1) + 3 * t * t * (p3 - p2)
    return pt, d


def zigzag(geo: Geo, size: float, ridges: float, smooth: bool) -> Geo:
    """AE Zig Zag: every segment is split into ridges+1 steps, points alternate along the normal."""
    if geo.path is not None or abs(size) < 1e-6:
        return geo
    r = max(0, int(round(ridges)))
    out = []
    for c in geo.contours:
        n = len(c.v)
        if n < 2:
            out.append(c.copy())
            continue
        segs = n if c.closed else n - 1
        pts, tans = [], []
        sign = 1.0
        for k in range(segs):
            a, b = k, (k + 1) % n
            p0, p3 = c.v[a], c.v[b]
            p1, p2 = p0 + c.o[a], p3 + c.i[b]
            steps = r + 1
            last = steps if (not c.closed and k == segs - 1) else steps - 1
            for j in range(last + 1):
                t = j / steps
                pt, d = _bez(p0, p1, p2, p3, t)
                ln = math.hypot(d[0], d[1])
                if ln < 1e-9:
                    d = p3 - p0
                    ln = math.hypot(d[0], d[1]) or 1.0
                nrm = np.array([-d[1], d[0]]) / ln
                pts.append(pt + nrm * size * sign)
                seg_len = math.hypot(*(p3 - p0)) / steps
                tans.append(d / ln * seg_len / 2)
                sign = -sign
        v = np.array(pts, dtype=float)
        if smooth:
            tg = np.array(tans, dtype=float)
            out.append(Contour(v, -tg, tg, c.closed))
        else:
            z = np.zeros_like(v)
            out.append(Contour(v, z, z.copy(), c.closed))
    return Geo(out)
