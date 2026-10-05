"""More After Effects effects: time-based effects and particle simulations (registered in effects_extra.REGISTRY)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import skia

from .effects_extra import _arr, _img, _surface, effect  # noqa: F401


def _premul(img: skia.Image) -> np.ndarray:
    """Premultiplied float RGBA in 0..1."""
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                       alphaType=skia.AlphaType.kPremul_AlphaType).astype(np.float32) / 255.0


def _from_premul(a: np.ndarray) -> skia.Image:
    out = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(out), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kPremul_AlphaType)


def _effect_prop(ctx, match_name: str, suffix: str) -> Any:
    """The keyframed property of this layer's (first enabled) effect, for values at other times."""
    parade = ctx.renderer._prop(ctx.layer, "ADBE Effect Parade")
    for e in parade or []:
        if e.match_name == match_name and getattr(e, "enabled", True):
            for p in e:
                if p.match_name == f"{match_name}-{suffix}":
                    return p
    return None


def _at(ctx, prop: Any, t: float, default: Any) -> Any:
    if prop is None:
        return default
    try:
        return ctx.renderer.ev.value(prop, t)
    except Exception:
        return default


def _comp_size(ctx) -> tuple[float, float]:
    comp = ctx.renderer.ev._comp_of_layer.get(id(ctx.layer), ctx.renderer.mogrt.main_comp)
    return float(comp.width), float(comp.height)


def _point_origin(ctx) -> tuple[float, float]:
    """Raw effect points on shape and text layers refer to the comp-sized layer rect (content origin
    at its centre); effects._params converts them, values read straight from a property are not."""
    if type(ctx.layer).__name__ in ("ShapeLayer", "TextLayer"):
        w, h = _comp_size(ctx)
        return -w / 2, -h / 2
    return 0.0, 0.0


def _layer_size(ctx) -> tuple[float, float]:
    if type(ctx.layer).__name__ in ("ShapeLayer", "TextLayer"):
        return _comp_size(ctx)
    w = float(getattr(ctx.layer, "width", 0) or 0)
    h = float(getattr(ctx.layer, "height", 0) or 0)
    return (w, h) if w > 0 and h > 0 else _comp_size(ctx)


def _rand(i: np.ndarray, k: int, seed: int) -> np.ndarray:
    """Deterministic uniform [0, 1) per particle index and stream k."""
    n = (i.astype(np.int64) * 374761393 + k * 668265263 + seed * 1274126177) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n = n ^ (n >> 16)
    return (n & 0xFFFFFF).astype(np.float64) / float(0x1000000)


def _num(v: Any, d: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _color(v: Any, d: list[float]) -> np.ndarray:
    v = v if isinstance(v, (list, tuple)) and len(v) >= 3 else d
    return np.array(list(v[:3]), np.float64)


_TRANSFER = {1: skia.BlendMode.kSrcOver, 2: skia.BlendMode.kScreen, 3: skia.BlendMode.kPlus,
             4: skia.BlendMode.kPlus, 5: skia.BlendMode.kSrcOver, 6: skia.BlendMode.kLighten,
             7: skia.BlendMode.kDarken}


def _draw_particles(ctx, x, y, diam, rgb, alpha, mode: int, soft: bool) -> skia.Image:
    """Particles (layer coordinates, diameters in layer units) on this effect's pixel grid."""
    s = _surface_for(ctx)
    c = s.getCanvas()
    c.scale(ctx.res, ctx.res)
    c.translate(-ctx.bounds.left(), -ctx.bounds.top())
    blend = _TRANSFER.get(mode, skia.BlendMode.kSrcOver)
    b = ctx.bounds
    vis = (alpha > 0.003) & (diam > 0) & (x + diam > b.left()) & (x - diam < b.right()) \
        & (y + diam > b.top()) & (y - diam < b.bottom())
    min_r = 0.5 / ctx.res  # thinner than a pixel: keep the coverage, not the size
    for xi, yi, di, ci, ai in zip(x[vis], y[vis], diam[vis], rgb[vis], alpha[vis]):
        r = di / 2
        a = float(ai) * (min(1.0, (r / min_r) ** 2) if r < min_r else 1.0)
        r = max(r, min_r)
        col = skia.Color4f(float(ci[0]), float(ci[1]), float(ci[2]), a)
        p = skia.Paint(AntiAlias=True, BlendMode=blend)
        if soft and r * ctx.res > 1.5:
            p.setShader(skia.GradientShader.MakeRadial(
                skia.Point(float(xi), float(yi)), float(r),
                [col.toColor(), skia.Color4f(col.fR, col.fG, col.fB, a * 0.6).toColor(),
                 skia.Color4f(col.fR, col.fG, col.fB, 0).toColor()], [0.0, 0.55, 1.0]))
        else:
            p.setColor4f(col)
        c.drawCircle(float(xi), float(yi), float(r), p)
    return s.makeImageSnapshot()


def _surface_for(ctx) -> skia.Surface:
    s = skia.Surface.MakeRasterN32Premul(max(1, int(math.ceil(ctx.bounds.width() * ctx.res))),
                                         max(1, int(math.ceil(ctx.bounds.height() * ctx.res))))
    s.getCanvas().clear(skia.ColorTRANSPARENT)
    return s


def _fit(img: skia.Image, out: skia.Image) -> skia.Image:
    """Same pixel size as the input image (surface sizes are rounded the same way, but be safe)."""
    if out.width() == img.width() and out.height() == img.height():
        return out
    s = _surface(img)
    s.getCanvas().drawImage(out, 0, 0)
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- echo

def _echo_frame(ctx, t: float, img: skia.Image) -> skia.Image:
    """The layer's content at time t for Echo.

    Shape and text layers are rasterized after their transform in AE, so the echo also shows
    the layer's motion (e.g. a parent rotating it): draw the content at t through the change
    of the layer's world transform between t and now.
    """
    if type(ctx.layer).__name__ not in ("ShapeLayer", "TextLayer"):
        return _fit(img, ctx.content_at(t))
    from .compositor import to_skia_affine

    r = ctx.renderer
    try:
        rel = np.linalg.inv(r.world_matrix(ctx.layer, ctx.t)) @ r.world_matrix(ctx.layer, t)
    except np.linalg.LinAlgError:
        return _surface(img).makeImageSnapshot()
    s = _surface(img)
    c = s.getCanvas()
    c.scale(ctx.res, ctx.res)
    c.translate(-ctx.bounds.left(), -ctx.bounds.top())
    c.concat(to_skia_affine(rel))
    r.draw_content(c, ctx.layer, t, ctx.res)
    return s.makeImageSnapshot()


def _echo_pad(p) -> float:
    span = abs(_num(p.get("0001", 0), 0)) * max(0.0, _num(p.get("0002", 0), 0))
    return min(1500.0, 200.0 + 4000.0 * span)


@effect("ADBE Echo", pad=_echo_pad)
def echo(ctx, p, img):
    # 0001 Echo Time, 0002 Number Of Echoes, 0003 Starting Intensity, 0004 Decay, 0005 Echo Operator
    dt = _num(p.get("0001", -1 / 30), -1 / 30)
    n = int(round(_num(p.get("0002", 1), 1)))
    start = _num(p.get("0003", 1), 1)
    decay = _num(p.get("0004", 1), 1)
    op = int(_num(p.get("0005", 1), 1))
    if n <= 0 or abs(dt) < 1e-6:
        return img
    # rendering 60 echoes of a layer per frame is too slow: sample at most 24 and weight them
    ks = np.unique(np.round(np.linspace(1, n, min(n, 24))).astype(int))
    weight = n / len(ks)
    base = _premul(img)
    layers = [(base * start, 0)]
    for k in ks:
        inten = start * decay ** k
        if inten <= 1e-3:
            continue
        e = _premul(_echo_frame(ctx, ctx.t + k * dt, img))
        layers.append((e * inten, k))
    if op == 1:  # add
        out = layers[0][0] + sum(e * weight for e, _ in layers[1:])
    elif op == 2:
        out = np.maximum.reduce([e for e, _ in layers])
    elif op == 3:
        out = np.minimum.reduce([e for e, _ in layers])
    elif op == 4:  # screen
        inv = np.ones_like(base)
        for e, _ in layers:
            inv *= 1 - np.clip(e, 0, 1)
        out = 1 - inv
    elif op == 5:  # composite in back: older echoes behind
        out = layers[0][0]
        for e, _ in layers[1:]:
            out = out + e * (1 - out[..., 3:4])
    elif op == 6:  # composite in front: older echoes on top
        out = layers[0][0]
        for e, _ in layers[1:]:
            out = e + out * (1 - e[..., 3:4])
    else:  # blend
        out = sum(e for e, _ in layers) / len(layers)
    return _from_premul(out)


# --------------------------------------------------------------------------- particles

def _spawn(ctx, rate: float, life: float, seed: int, cap: int = 60000):
    """Indices and ages of the particles alive now (one born every 1/rate s since the layer's in point)."""
    t0 = float(getattr(ctx.layer, "in_point", 0.0))
    now = ctx.t
    if rate <= 0 or life <= 0 or now < t0:
        return np.zeros(0, np.int64), np.zeros(0), t0
    first = max(0, int(math.floor((now - life - t0) * rate)))
    last = int(math.floor((now - t0) * rate))
    if last - first > cap:
        first = last - cap
    idx = np.arange(first, last + 1, dtype=np.int64)
    birth = t0 + idx / rate
    age = now - birth
    keep = (age >= 0) & (age < life)
    return idx[keep], age[keep], t0


def _opacity_curve(kind: int, u: np.ndarray) -> np.ndarray:
    if kind == 1:  # constant
        return np.ones_like(u)
    if kind == 2:  # fade out
        return 1 - u
    if kind == 3:  # fade in
        return u
    return np.clip(np.minimum(u * 5, (1 - u) * 5), 0, 1)  # fade in and out


def _motion(age, vx, vy, gx, gy, drag):
    """Position offset after age seconds with linear drag and gravity."""
    if drag > 1e-6:
        f = (1 - np.exp(-drag * age)) / drag
        return vx * f + 0.5 * gx * age ** 2, vy * f + 0.5 * gy * age ** 2
    return vx * age + 0.5 * gx * age ** 2, vy * age + 0.5 * gy * age ** 2


@effect("CC Particle Systems II")
def particle_systems_2(ctx, p, img):
    rate = _num(p.get("0001", 2), 2) * 150  # particles per second
    life = _num(p.get("0002", 1), 1)
    seed = int(_num(p.get("0030", 0), 0))
    idx, age, _ = _spawn(ctx, rate, life, seed)
    if len(idx) == 0:
        return _fit(img, _surface_for(ctx).makeImageSnapshot())
    w, h = _layer_size(ctx)
    ox, oy = _point_origin(ctx)
    # the producer position at each particle's birth (it may be animated)
    pos_prop = _effect_prop(ctx, "CC Particle Systems II", "0004")
    births = ctx.t - age
    uniq = np.round(births * 60) / 60  # evaluate the path at 60 samples per second
    keys, inv = np.unique(uniq, return_inverse=True)
    cur = p.get("0004", [w / 2, h / 2])
    pts = np.array([list(_at(ctx, pos_prop, float(k), cur))[:2] for k in keys], np.float64)
    px, py = pts[inv, 0] + ox, pts[inv, 1] + oy
    rx, ry = _num(p.get("0005", 0), 0) * w / 100, _num(p.get("0006", 0), 0) * h / 100
    px = px + (_rand(idx, 1, seed) * 2 - 1) * rx
    py = py + (_rand(idx, 2, seed) * 2 - 1) * ry

    anim = int(_num(p.get("0009", 1), 1))
    unit = 0.3 * w  # velocity 1.0 ~ 0.3 layer widths per second
    speed = _num(p.get("0010", 1), 1) * unit * (0.6 + 0.8 * _rand(idx, 3, seed))
    direction = math.radians(_num(p.get("0014", 0), 0))
    extra = _num(p.get("0015", 0), 0)
    if anim in (1, 4, 5, 6, 7):  # explosive, viscous, twirls, vortex: all directions
        ang = _rand(idx, 4, seed) * 2 * math.pi
    else:  # direction / cone / fire / jet: along Direction (0 = up), Extra widens the spread
        ang = direction + (_rand(idx, 4, seed) - 0.5) * math.pi * min(2.0, 0.15 + extra)
    vx, vy = np.sin(ang) * speed, -np.cos(ang) * speed
    gravity = _num(p.get("0012", 0), 0) * unit
    drag = _num(p.get("0013", 0), 0) * 3
    dx, dy = _motion(age, vx, vy, 0.0, gravity, drag)
    x, y = px + dx, py + dy

    u = np.clip(age / life, 0, 1)
    bs, ds = _num(p.get("0019", 0.25), 0.25), _num(p.get("0020", 0.5), 0.5)
    var = _num(p.get("0021", 0), 0)
    diam = np.maximum(0.0, (bs + (ds - bs) * u) * (1 + var * (_rand(idx, 5, seed) * 2 - 1))) * w * 0.05
    maxop = _num(p.get("0023", 75), 75)
    maxop = maxop if maxop <= 1 else maxop / 100
    alpha = _opacity_curve(int(_num(p.get("0022", 1), 1)), u) * maxop
    c0, c1 = _color(p.get("0025"), [1, 1, 1]), _color(p.get("0026"), [1, 1, 1])
    rgb = c0[None] + (c1 - c0)[None] * u[:, None]
    ptype = int(_num(p.get("0018", 1), 1))
    out = _draw_particles(ctx, x, y, diam, rgb, alpha, int(_num(p.get("0028", 1), 1)), soft=ptype in (4, 5, 13, 14))
    return _fit(img, out)


@effect("CC Particle World")
def particle_world(ctx, p, img):
    rate = _num(p.get("0004", 1), 1) * 1500  # particles per second (calibrated on a Mixkit preview)
    life = _num(p.get("0005", 1), 1)
    seed = int(_num(p.get("0104", 0), 0))
    idx, age, _ = _spawn(ctx, rate, life, seed, cap=300000)
    w, h = _layer_size(ctx)
    if len(idx) == 0:
        out = _surface_for(ctx).makeImageSnapshot()
    else:
        n = len(idx)
        pos = np.stack([np.full(n, _num(p.get(k, 0), 0)) for k in ("0007", "0008", "0009")], 1)
        rad = np.array([_num(p.get(k, 0), 0) for k in ("0010", "0011", "0012")])
        pos = pos + (np.stack([_rand(idx, 1 + j, seed) for j in range(3)], 1) * 2 - 1) * rad
        anim = int(_num(p.get("0015", 1), 1))
        speed = _num(p.get("0016", 1), 1) * (0.5 + _rand(idx, 4, seed))
        if anim in (2, 3):  # direction / cone axis
            axis = np.array([_num(p.get(k, 0), 0) for k in ("0095", "0096", "0097")])
            axis = axis / (np.linalg.norm(axis) or 1)
            dirs = axis[None] + (np.stack([_rand(idx, 5 + j, seed) for j in range(3)], 1) * 2 - 1) \
                * (0.2 + _num(p.get("0019", 0), 0))
        else:  # explosive and the rest: all directions
            z = _rand(idx, 5, seed) * 2 - 1
            a = _rand(idx, 6, seed) * 2 * math.pi
            r = np.sqrt(1 - z * z)
            dirs = np.stack([r * np.cos(a), r * np.sin(a), z], 1)
        dirs = dirs / np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-9)
        vel = dirs * speed[:, None]
        g = _num(p.get("0018", 0), 0)
        gvec = np.array([_num(p.get(k, 0), 0) for k in ("0100", "0101", "0102")])
        if not gvec.any():
            gvec = np.array([0.0, 1.0, 0.0])
        gvec = gvec / np.linalg.norm(gvec) * g
        drag = _num(p.get("0041", 0), 0)
        f = (1 - np.exp(-drag * age)) / drag if drag > 1e-6 else age
        world = pos + vel * f[:, None] + 0.5 * gvec[None] * (age ** 2)[:, None]

        # effect camera: orbit around the origin
        dist = max(1e-3, _num(p.get("0033", 2), 2))
        rx, ry, rz = (math.radians(_num(p.get(k, 0), 0)) for k in ("0034", "0035", "0036"))
        fov = math.radians(min(170.0, max(1.0, _num(p.get("0037", 45), 45))))
        cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
        rot = (np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
               @ np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
               @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]))
        cam = world @ rot.T
        depth = cam[:, 2] + dist
        front = depth > 1e-3
        focal = (w / 2) / math.tan(fov / 2)  # pixels per world unit at depth 1
        x = w / 2 + cam[:, 0] / np.where(front, depth, 1) * focal
        y = h / 2 + cam[:, 1] / np.where(front, depth, 1) * focal
        u = np.clip(age / life, 0, 1)
        bs, ds = _num(p.get("0024", 0.1), 0.1), _num(p.get("0025", 0.1), 0.1)
        var = _num(p.get("0026", 0), 0) / 100
        size = np.maximum(0.0, (bs + (ds - bs) * u) * (1 + var * (_rand(idx, 8, seed) * 2 - 1)))
        # perspective size, but limited: particles passing the camera must not fill the frame
        diam = size * 0.03 * focal / dist * np.clip(dist / np.where(front, depth, 1), 0.5, 1.5)
        maxop = _num(p.get("0027", 75), 75) / 100
        alpha = np.where(front, maxop * np.clip(np.minimum(u * 10, (1 - u) * 4), 0, 1), 0)
        c0, c1 = _color(p.get("0029"), [1, 1, 1]), _color(p.get("0030"), [1, 1, 1])
        mix = _rand(idx, 9, seed) if int(_num(p.get("0028", 1), 1)) == 2 else u  # origin-based: per-particle
        rgb = c0[None] + (c1 - c0)[None] * mix[:, None]
        order = np.argsort(-depth)  # far ones first
        ptype = int(_num(p.get("0023", 1), 1))
        ox, oy = (0.0, 0.0) if type(ctx.layer).__name__ not in ("ShapeLayer", "TextLayer") else (-w / 2, -h / 2)
        out = _draw_particles(ctx, x[order] + ox, y[order] + oy, diam[order], rgb[order], alpha[order],
                              int(_num(p.get("0039", 1), 1)), soft=ptype in (4, 5, 13, 14))
    if int(_num(p.get("0092", 0), 0)):  # composite with original
        s = _surface(img)
        s.getCanvas().drawImage(img, 0, 0)
        s.getCanvas().drawImage(_fit(img, out), 0, 0)
        return s.makeImageSnapshot()
    return _fit(img, out)


# --------------------------------------------------------------------------- glue gun

@effect("CC Glue Gun")
def glue_gun(ctx, p, img):
    # 0001 Brush Position, 0002 Stroke Width, 0003 Density, 0004 Time Span (0 = everything so far)
    pos_prop = _effect_prop(ctx, "CC Glue Gun", "0001")
    width_prop = _effect_prop(ctx, "CC Glue Gun", "0002")
    t0 = float(getattr(ctx.layer, "in_point", 0.0))
    span = _num(p.get("0004", 0), 0)
    if span > 0:
        t0 = max(t0, ctx.t - span)
    if ctx.t <= t0:
        return _surface(img).makeImageSnapshot()
    steps = max(2, min(400, int((ctx.t - t0) * 240) + 1))
    ts = np.linspace(t0, ctx.t, steps)
    ox, oy = _point_origin(ctx)
    cur = p.get("0001", [0, 0])
    pts = [list(_at(ctx, pos_prop, float(tt), cur))[:2] for tt in ts]
    wid = [_num(_at(ctx, width_prop, float(tt), p.get("0002", 1)), 1) * 4.0 for tt in ts]
    s = _surface(img)  # the stroke replaces the layer's content
    c = s.getCanvas()
    c.scale(ctx.res, ctx.res)
    c.translate(-ctx.bounds.left(), -ctx.bounds.top())
    # a glossy bead: dark rim, light body and a thin highlight
    for col, scale, shift in (((0.55, 0.55, 0.55, 1.0), 1.0, 0.0), ((0.85, 0.85, 0.85, 1.0), 0.7, -0.08),
                              ((1.0, 1.0, 1.0, 1.0), 0.25, -0.2)):
        paint = skia.Paint(AntiAlias=True, Color4f=skia.Color4f(*col), StrokeCap=skia.Paint.kRound_Cap,
                           Style=skia.Paint.kStroke_Style)
        for (x0, y0), (x1, y1), w0, w1 in zip(pts, pts[1:], wid, wid[1:]):
            wm = (w0 + w1) / 2
            if wm <= 0:
                continue
            paint.setStrokeWidth(wm * scale)
            off = wm * shift
            c.drawLine(x0 + ox, y0 + oy + off, x1 + ox, y1 + oy + off, paint)
    return s.makeImageSnapshot()


@effect("ADBE Posterize Time")
def posterize_time(ctx, p, img):
    # handled where the layer is drawn (compositor); on adjustment layers it has no effect here
    return img
