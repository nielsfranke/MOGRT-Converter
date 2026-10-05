"""More After Effects effects: distortion effects (registered in effects_extra.REGISTRY)."""

from __future__ import annotations

import math
import struct

import numpy as np
import skia

from .effects_extra import _arr, _blur_dims, _fractal, _img, _sample, _surface, effect  # noqa: F401


def _pre(img: skia.Image) -> np.ndarray:
    """Premultiplied float RGBA in 0..1 (for resampling without dark fringes)."""
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                       alphaType=skia.AlphaType.kPremul_AlphaType).astype(np.float32) / 255.0


def _from_pre(a: np.ndarray) -> skia.Image:
    out = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(out), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kPremul_AlphaType)


def _grid(ctx, img: skia.Image) -> tuple[np.ndarray, np.ndarray]:
    """Layer coordinates of every pixel centre."""
    h, w = img.height(), img.width()
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    b = ctx.bounds
    return b.left() + (xx + 0.5) / ctx.res, b.top() + (yy + 0.5) / ctx.res


def _resized(img: skia.Image, w: int, h: int) -> skia.Image:
    s = skia.Surface.MakeRasterN32Premul(w, h)
    s.getCanvas().clear(skia.ColorTRANSPARENT)
    s.getCanvas().drawImageRect(img, skia.Rect.MakeWH(w, h), skia.SamplingOptions(skia.FilterMode.kLinear))
    return s.makeImageSnapshot()


def _dilated(img: skia.Image, r: int) -> skia.Image:
    s = _surface(img)
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=skia.ImageFilters.Dilate(r, r)))
    return s.makeImageSnapshot()


def _upsample(field: np.ndarray, step: int, h: int, w: int) -> np.ndarray:
    """Bilinear upsampling of a field sampled every `step` pixels to h x w (separable: rows, then columns)."""
    if step == 1:
        return field[:h, :w]
    x = np.arange(w, dtype=np.float32) / step
    i0 = x.astype(np.int32)
    fx = x - i0
    rows = field[:, i0] * (1 - fx) + field[:, i0 + 1] * fx
    y = np.arange(h, dtype=np.float32) / step
    j0 = y.astype(np.int32)
    fy = (y - j0)[:, None]
    return rows[j0] * (1 - fy) + rows[j0 + 1] * fy


def _lookup(ctx, pre: np.ndarray, lx: np.ndarray, ly: np.ndarray) -> np.ndarray:
    """Sample the premultiplied image at layer coordinates."""
    b = ctx.bounds
    return _sample(pre, (lx - b.left()) * ctx.res - 0.5, (ly - b.top()) * ctx.res - 0.5)


def _pt(v, default) -> list[float]:
    v = list(v) if isinstance(v, (list, tuple)) and len(v) >= 2 else list(default)
    if max(abs(v[0]), abs(v[1])) > 20000:  # stored at 100x in some projects
        v = [v[0] / 100, v[1] / 100]
    return [float(v[0]), float(v[1])]


# --------------------------------------------------------------------------- waves / offsets

@effect("ADBE Wave Warp", pad=lambda p: abs(float(p.get("0002", 10))) + 2)
def wave_warp(ctx, p, img):
    # 0001 Wave Type, 0002 Height, 0003 Width, 0004 Direction, 0005 Speed, 0006 Pinning, 0007 Phase
    kind = int(p.get("0001", 1) or 1)
    height = float(p.get("0002", 10))
    width = max(1.0, float(p.get("0003", 40)))
    a = math.radians(float(p.get("0004", 90)))
    speed = float(p.get("0005", 1))
    phase = math.radians(float(p.get("0007", 0)))
    if abs(height) < 0.01:
        return img
    lx, ly = _grid(ctx, img)
    dx, dy = math.sin(a), -math.cos(a)  # direction of travel (AE: 0° = up)
    nx, ny = -dy, dx  # displacement is perpendicular to it
    x = (lx * dx + ly * dy) / width - speed * ctx.t + phase / (2 * math.pi)
    f = x - np.floor(x)
    if kind == 2:  # square
        wv = np.where(f < 0.5, 1.0, -1.0)
    elif kind == 3:  # triangle
        wv = 1 - 4 * np.abs(f - 0.5)
    elif kind == 4:  # sawtooth
        wv = 2 * f - 1
    else:
        wv = np.sin(2 * math.pi * x)
    d = wv.astype(np.float32) * height
    pinning = int(p.get("0006", 1) or 1)
    if pinning != 1:  # pin the layer's edges
        r = ctx.layer_rect()
        ramp = max(width / 2, 1.0)
        wx = np.clip(np.minimum(lx - r.left(), r.right() - lx) / ramp, 0, 1)
        wy = np.clip(np.minimum(ly - r.top(), r.bottom() - ly) / ramp, 0, 1)
        d = d * {2: np.minimum(wx, wy), 3: wy, 4: wx}.get(pinning, np.minimum(wx, wy))
    return _from_pre(_lookup(ctx, _pre(img), lx - nx * d, ly - ny * d))


@effect("ADBE Offset")
def offset(ctx, p, img):
    # 0001 Shift Center To, 0002 Blend With Original
    r = ctx.layer_rect()
    to = _pt(p.get("0001"), [r.centerX(), r.centerY()])
    sx, sy = to[0] - r.centerX(), to[1] - r.centerY()
    blend = float(p.get("0002", 0)) / 100.0
    if (abs(sx) < 1e-3 and abs(sy) < 1e-3) or blend >= 1:
        return img
    lx, ly = _grid(ctx, img)
    w, h = max(r.width(), 1e-3), max(r.height(), 1e-3)
    qx = r.left() + np.mod(lx - sx - r.left(), w)
    qy = r.top() + np.mod(ly - sy - r.top(), h)
    pre = _pre(img)
    out = _lookup(ctx, pre, qx, qy)
    inside = ((lx >= r.left()) & (lx < r.right()) & (ly >= r.top()) & (ly < r.bottom()))[..., None]
    out = np.where(inside, out, pre)
    return _from_pre(out * (1 - blend) + pre * blend)


@effect("ADBE Mirror", pad=lambda p: 200)
def mirror(ctx, p, img):
    # 0001 Reflection Center, 0002 Reflection Angle
    r = ctx.layer_rect()
    c = _pt(p.get("0001"), [r.centerX(), r.centerY()])
    a = math.radians(float(p.get("0002", 0)))
    nx, ny = math.cos(a), math.sin(a)
    lx, ly = _grid(ctx, img)
    d = (lx - c[0]) * nx + (ly - c[1]) * ny
    d = np.maximum(d, 0)  # the side the normal points to shows the mirrored other side
    pre = _pre(img)
    out = _lookup(ctx, pre, lx - 2 * d * nx, ly - 2 * d * ny)
    return _from_pre(np.where((d > 0)[..., None], out, pre))


def _corner_pad(p) -> float:
    pts = [_pt(p.get(k), [0, 0]) for k in ("0001", "0002", "0003", "0004")]
    return min(4000.0, max(abs(v) for pt in pts for v in pt) * 0.5 + 100)


@effect("ADBE Corner Pin", pad=_corner_pad)
def corner_pin(ctx, p, img):
    # 0001 Upper Left, 0002 Upper Right, 0003 Lower Left, 0004 Lower Right (layer coordinates)
    r = ctx.layer_rect()
    dst = [_pt(p.get("0001"), [r.left(), r.top()]), _pt(p.get("0002"), [r.right(), r.top()]),
           _pt(p.get("0004"), [r.right(), r.bottom()]), _pt(p.get("0003"), [r.left(), r.bottom()])]
    src = [[r.left(), r.top()], [r.right(), r.top()], [r.right(), r.bottom()], [r.left(), r.bottom()]]
    b, res = ctx.bounds, ctx.res

    def px(q):
        return skia.Point((q[0] - b.left()) * res, (q[1] - b.top()) * res)

    m = skia.Matrix()
    if not m.setPolyToPoly([px(q) for q in src], [px(q) for q in dst]):
        return img
    s = _surface(img)
    c = s.getCanvas()
    c.clipRect(skia.Rect.MakeWH(img.width(), img.height()))
    c.concat(m)
    # only the layer's own rectangle is pinned
    c.clipRect(skia.Rect.MakeLTRB(*(px(src[0])), *(px(src[2]))), doAntiAlias=True)
    c.drawImage(img, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear))
    return s.makeImageSnapshot()


@effect("CS HexTile")
def hex_tile(ctx, p, img):
    # 0001 Render (1 plain, 2 mirrored), 0002 Radius, 0003 Center, 0005 Rotate
    r = ctx.layer_rect()
    radius = max(1.0, float(p.get("0002", 50)))
    c = _pt(p.get("0003"), [r.centerX(), r.centerY()])
    rot = math.radians(float(p.get("0005", 0)))
    mirrored = int(p.get("0001", 1) or 1) == 2
    lx, ly = _grid(ctx, img)
    cs, sn = math.cos(rot), math.sin(rot)
    x0, y0 = lx - c[0], ly - c[1]
    qx, qy = x0 * cs + y0 * sn, -x0 * sn + y0 * cs  # into the tiling frame
    # pointy-top hexagons with circumradius `radius`: axial coordinates, cube rounding
    fq = (math.sqrt(3) / 3 * qx - qy / 3) / radius
    fr = (2 / 3 * qy) / radius
    fs = -fq - fr
    rq, rr, rs = np.round(fq), np.round(fr), np.round(fs)
    dq, dr, ds = np.abs(rq - fq), np.abs(rr - fr), np.abs(rs - fs)
    rq = np.where((dq > dr) & (dq > ds), -rr - rs, rq)
    rr = np.where(~((dq > dr) & (dq > ds)) & (dr > ds), -rq - rs, rr)
    hx = radius * math.sqrt(3) * (rq + rr / 2)
    hy = radius * 1.5 * rr
    ux, uy = qx - hx, qy - hy
    if mirrored:  # every other tile flipped
        flip = (np.mod(rq + rr, 2) == 1)
        ux = np.where(flip, -ux, ux)
    sx = c[0] + ux * cs - uy * sn
    sy = c[1] + ux * sn + uy * cs
    return _from_pre(_lookup(ctx, _pre(img), sx, sy))


# --------------------------------------------------------------------------- 3-D wraps

def _rot3(rx: float, ry: float, rz: float) -> np.ndarray:
    ax, ay, az = (math.radians(v) for v in (rx, ry, rz))
    mx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    my = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    mz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    return mz @ my @ mx


@effect("CC Cylinder", pad=lambda p: 400)
def cc_cylinder(ctx, p, img):
    # 0002 Radius (%), 0004-0006 Position X/Y/Z, 0009-0011 Rotation X/Y/Z, 0013 Render (1 full, 2 outside,
    # 3 inside), 0015 Light Intensity, 0017 Light Height, 0018 Light Direction, 0021 Ambient, 0022 Diffuse
    r = ctx.layer_rect()
    w, h = max(r.width(), 1.0), max(r.height(), 1.0)
    rad = w / 2 * float(p.get("0002", 100)) / 100.0  # 100 %: as wide as the layer (matches previews)
    if rad <= 0.01:
        return img
    cx = r.centerX() + float(p.get("0004", 0))
    cy = r.centerY() + float(p.get("0005", 0))
    m = _rot3(float(p.get("0009", 0)), float(p.get("0010", 0)), float(p.get("0011", 0)))
    render = int(p.get("0013", 1) or 1)
    ambient = float(p.get("0021", 40)) / 100.0
    diffuse = float(p.get("0022", 50)) / 100.0 * float(p.get("0015", 100)) / 100.0
    lh, ld = math.radians(float(p.get("0017", 45))), math.radians(float(p.get("0018", -45)))
    light = np.array([math.cos(lh) * math.sin(ld), -math.cos(lh) * math.cos(ld), -math.sin(lh)])

    lx, ly = _grid(ctx, img)
    # orthographic rays along +z through each pixel, in cylinder space (axis = y)
    o = np.stack([lx - cx, ly - cy, np.full_like(lx, -10 * (w + h))], axis=-1) @ m  # rows: M^T applied
    d = np.array([0.0, 0.0, 1.0]) @ m
    a = d[0] ** 2 + d[2] ** 2
    pre = _pre(img)
    out = np.zeros_like(pre)
    if a < 1e-9:  # looking along the axis: only the rim is visible
        return _from_pre(out)
    bq = 2 * (o[..., 0] * d[0] + o[..., 2] * d[2])
    cq = o[..., 0] ** 2 + o[..., 2] ** 2 - rad * rad
    disc = bq * bq - 4 * a * cq
    hit = disc >= 0
    sq = np.sqrt(np.maximum(disc, 0))
    layers = []
    if render in (1, 3):
        layers.append((-bq + sq) / (2 * a))  # far side (inside of the cylinder)
    if render in (1, 2):
        layers.append((-bq - sq) / (2 * a))  # near side
    for tt in layers:
        pt = o + tt[..., None] * d
        theta = np.arctan2(pt[..., 0], -pt[..., 2])
        u = cx + theta * rad
        v = cy + pt[..., 1]
        ok = hit & (np.abs(theta * rad) <= w / 2) & (np.abs(pt[..., 1]) <= h / 2)
        col = _lookup(ctx, pre, u, v)
        normal = np.stack([pt[..., 0], np.zeros_like(theta), pt[..., 2]], axis=-1) / rad
        normal = normal @ m.T  # back to view space
        shade = ambient + diffuse * np.maximum(normal @ light, 0)
        col[..., :3] *= np.clip(shade, 0, 2)[..., None]
        col *= ok[..., None]
        out = col + out * (1 - col[..., 3:4])
    return _from_pre(out)


# --------------------------------------------------------------------------- edges / blur

@effect("ADBE Roughen Edges", pad=lambda p: 2)
def roughen_edges(ctx, p, img):
    # 0001 Edge Type, 0010 Edge Color, 0002 Border, 0003 Edge Sharpness, 0004 Fractal Influence,
    # 0005 Scale, 0006 Stretch, 0007 Offset, 0008 Complexity, 0009 Evolution, 0014/0015 Random Seed
    kind = int(p.get("0001", 1) or 1)
    border = max(0.0, float(p.get("0002", 8)))
    if border < 0.05:
        return img
    sharp = max(0.0, float(p.get("0003", 1)))
    influence = float(p.get("0004", 1))
    scale = max(1.0, float(p.get("0005", 100)))
    stretch = float(p.get("0006", 0))
    off = _pt(p.get("0007"), [0, 0])
    complexity = float(p.get("0008", 2) or 2)
    evo = float(p.get("0009", 0)) / 360.0
    seed = int(p.get("0015", p.get("0014", 0)) or 0)
    a = _arr(img)
    alpha = a[..., 3]
    if not alpha.any():
        return img
    # soft distance to the edge: blur the matte over the border width
    bl = _blur_dims(img, border * ctx.res / 2.0, 1)
    blurred = _arr(bl)[..., 3]
    # relative to the local maximum, so thin strokes (whose blurred matte never reaches 0.5) survive;
    # the maximum is smooth, so it is taken on a reduced image (a large dilate is slow)
    r_px = max(1, int(border * ctx.res))
    f = max(1, r_px // 8)
    peak = _resized(_dilated(_resized(bl, max(1, bl.width() // f), max(1, bl.height() // f)), max(1, r_px // f)),
                    bl.width(), bl.height())
    blurred = blurred / np.maximum(_arr(peak)[..., 3], 1e-3)
    size = scale / 4.0
    sx = size * (2 ** (stretch / 2)) if stretch else size
    sy = size * (2 ** (-stretch / 2)) if stretch else size
    # the noise is smooth at the scale of its size: evaluate it on a coarse grid and upsample
    h, w = alpha.shape
    step = max(1, int(min(sx, sy) * ctx.res / 4))
    gy, gx = np.mgrid[0:h // step + 2, 0:w // step + 2].astype(np.float32)
    b = ctx.bounds
    lx, ly = b.left() + (gx * step + 0.5) / ctx.res, b.top() + (gy * step + 0.5) / ctx.res
    n = _upsample(_fractal((lx - off[0]) / sx, (ly - off[1]) / sy, evo, seed, complexity), step, h, w)
    if kind in (4,):  # spiky: sharper noise
        n = np.sign(n) * np.abs(n) ** 0.5
    level = blurred - 0.5 + influence * 0.5 * n
    k = 2.0 + sharp * 4.0
    matte = np.clip(level * k + 0.5, 0, 1)
    a[..., 3] = alpha * matte
    if kind in (2, 6, 8):  # "… Color": tint the edge
        col = p.get("0010", [1, 1, 1, 1])
        col = np.array(list(col)[:3] if isinstance(col, (list, tuple)) else [1, 1, 1], np.float32)
        rim = np.clip(1 - level * k, 0, 1)[..., None]
        a[..., :3] = a[..., :3] * (1 - rim) + col * rim
    return _img(a)


@effect("ADBE Motion Blur", pad=lambda p: abs(float(p.get("0002", 10))) + 4)
def directional_blur(ctx, p, img):
    # 0001 Direction (0 = vertical), 0002 Blur Length
    length = abs(float(p.get("0002", 10))) * ctx.res
    if length < 0.5:
        return img
    ang = float(p.get("0001", 0))
    w, h = img.width(), img.height()
    diag = int(math.ceil(math.hypot(w, h))) + 2
    big = skia.Surface.MakeRasterN32Premul(diag, diag)
    c = big.getCanvas()
    c.clear(skia.ColorTRANSPARENT)
    c.translate(diag / 2, diag / 2)
    c.rotate(90 - ang)  # blur direction -> +x
    c.translate(-w / 2, -h / 2)
    c.drawImage(img, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
    rot = big.makeImageSnapshot()
    blurred = skia.Surface.MakeRasterN32Premul(diag, diag)
    bc = blurred.getCanvas()
    bc.clear(skia.ColorTRANSPARENT)
    bc.drawImage(rot, 0, 0, skia.SamplingOptions(),
                 skia.Paint(ImageFilter=skia.ImageFilters.Blur(length / 2.0, 0, skia.TileMode.kDecal)))
    s = _surface(img)
    oc = s.getCanvas()
    oc.translate(w / 2, h / 2)
    oc.rotate(ang - 90)
    oc.translate(-diag / 2, -diag / 2)
    oc.drawImage(blurred.makeImageSnapshot(), 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- mesh warp

def _mesh_blocks(effect_obj) -> list[tuple[int, int, np.ndarray]]:
    """Raw distortion meshes (one per keyframe): rows x cols vertices with
    position, up, right, down and left handles, normalised to the layer size."""
    out = []
    for ch in getattr(getattr(effect_obj, "_tdgp", None), "chunks", []) or []:
        if getattr(ch, "list_type", "") != "aRbs":
            continue
        for blk in ch.chunks:
            d = bytes(getattr(blk, "data", b"") or b"")
            if len(d) < 20:
                continue
            rows, cols = struct.unpack(">2i", d[:8])
            n = rows * cols
            if rows < 2 or cols < 2 or len(d) < 20 + 40 * n:
                continue
            v = np.frombuffer(d[20:20 + 40 * n], dtype=">f4").astype(np.float64).reshape(rows, cols, 5, 2)
            out.append((rows, cols, v))
    return out


def _cubic(p0, p1, p2, p3, s):
    u = 1 - s
    return u * u * u * p0 + 3 * u * u * s * p1 + 3 * u * s * s * p2 + s * s * s * p3


def _mesh_eval(rows: int, cols: int, v: np.ndarray, gu: np.ndarray, gv: np.ndarray) -> np.ndarray:
    """Coons patches through the mesh: normalised source (u, v) -> normalised position."""
    fu, fv = gu * (cols - 1), gv * (rows - 1)
    j = np.clip(np.floor(fu).astype(int), 0, cols - 2)
    i = np.clip(np.floor(fv).astype(int), 0, rows - 2)
    s, t = (fu - j)[..., None], (fv - i)[..., None]
    P, UP, RT, DN, LT = 0, 1, 2, 3, 4
    p00, p10, p01, p11 = v[i, j], v[i, j + 1], v[i + 1, j], v[i + 1, j + 1]
    top = _cubic(p00[..., P, :], p00[..., RT, :], p10[..., LT, :], p10[..., P, :], s)
    bot = _cubic(p01[..., P, :], p01[..., RT, :], p11[..., LT, :], p11[..., P, :], s)
    left = _cubic(p00[..., P, :], p00[..., DN, :], p01[..., UP, :], p01[..., P, :], t)
    right = _cubic(p10[..., P, :], p10[..., DN, :], p11[..., UP, :], p11[..., P, :], t)
    bil = ((1 - s) * (1 - t) * p00[..., P, :] + s * (1 - t) * p10[..., P, :]
           + (1 - s) * t * p01[..., P, :] + s * t * p11[..., P, :])
    return (1 - t) * top + t * bot + (1 - s) * left + s * right - bil


@effect("ADBE MESH WARP", pad=lambda p: 300)
def mesh_warp(ctx, p, img):
    # 0001 Rows, 0002 Columns, 0004 Distortion Mesh (raw data, one block per keyframe)
    eff = next((e for e in (ctx.renderer._prop(ctx.layer, "ADBE Effect Parade") or [])
                if e.match_name == "ADBE MESH WARP" and getattr(e, "enabled", True)), None)
    if eff is None:
        return img
    blocks = _mesh_blocks(eff)
    if not blocks:
        return img
    G = 33
    gv, gu = np.mgrid[0:G, 0:G].astype(np.float64) / (G - 1)
    meshes = [_mesh_eval(r, c, v, gu, gv) for r, c, v in blocks]
    prop = None
    try:
        prop = eff.property("ADBE MESH WARP-0004")
    except Exception:
        pass
    keys = list(getattr(prop, "keyframes", None) or []) if prop is not None else []
    if len(keys) == len(meshes) and len(keys) > 1:
        times = [k.time for k in keys]
        t = ctx.t
        if t <= times[0]:
            pos = meshes[0]
        elif t >= times[-1]:
            pos = meshes[-1]
        else:
            k = max(i for i, tk in enumerate(times) if tk <= t)
            hold = "HOLD" in str(getattr(keys[k], "out_interpolation_type", ""))
            f = 0.0 if hold else (t - times[k]) / max(times[k + 1] - times[k], 1e-9)
            pos = meshes[k] * (1 - f) + meshes[k + 1] * f
    else:
        pos = meshes[-1] if keys and ctx.t >= keys[-1].time else meshes[0]
    r = ctx.layer_rect()
    b, res = ctx.bounds, ctx.res

    def to_px(nx, ny):
        return (r.left() + nx * r.width() - b.left()) * res, (r.top() + ny * r.height() - b.top()) * res

    dx, dy = to_px(pos[..., 0], pos[..., 1])
    sx, sy = to_px(gu, gv)
    pts, texs, idx = [], [], []
    for yy in range(G):
        for xx in range(G):
            pts.append(skia.Point(float(dx[yy, xx]), float(dy[yy, xx])))
            texs.append(skia.Point(float(sx[yy, xx]), float(sy[yy, xx])))
    for yy in range(G - 1):
        for xx in range(G - 1):
            a0 = yy * G + xx
            idx += [a0, a0 + 1, a0 + G, a0 + 1, a0 + G + 1, a0 + G]
    verts = skia.Vertices(skia.Vertices.kTriangles_VertexMode, pts, texs, None, idx)
    s = _surface(img)
    paint = skia.Paint(AntiAlias=True, Shader=img.makeShader(skia.TileMode.kDecal, skia.TileMode.kDecal,
                                                             skia.SamplingOptions(skia.FilterMode.kLinear)))
    s.getCanvas().drawVertices(verts, paint)
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- card wipe

def _hash2(i: np.ndarray, j: np.ndarray, seed: int) -> np.ndarray:
    n = (i.astype(np.int64) * 73856093 + j.astype(np.int64) * 19349663 + seed * 83492791) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF).astype(np.float32) / 65535.0


@effect("APC CardWipeCam")
def card_wipe(ctx, p, img):
    # 0002 Transition Completion, 0004 Transition Width, 0006 Back Layer, 0008 Rows & Columns, 0010 Rows,
    # 0012 Columns, 0014 Card Scale, 0016 Flip Axis (1 X, 2 Y, 3 random), 0020 Flip Order, 0022 Gradient
    # Layer, 0024 Timing Randomness, 0026 Random Seed
    comp = float(p.get("0002", 0)) / 100.0
    if comp <= 0:
        return img
    width = max(1e-3, float(p.get("0004", 50)) / 100.0)
    rows = max(1, int(p.get("0010", 10) or 1))
    cols = rows if int(p.get("0008", 1) or 1) == 2 else max(1, int(p.get("0012", 10) or 1))
    card_scale = float(p.get("0014", 1) or 1)
    axis = int(p.get("0016", 1) or 1)
    order = int(p.get("0020", 1) or 1)
    rnd = float(p.get("0024", 0))
    seed = int(p.get("0026", 0) or 0)
    r = ctx.layer_rect()
    lx, ly = _grid(ctx, img)
    cw, chh = r.width() / cols, r.height() / rows
    j = np.clip(np.floor((lx - r.left()) / cw), 0, cols - 1)
    i = np.clip(np.floor((ly - r.top()) / chh), 0, rows - 1)
    fu, fv = (j + 0.5) / cols, (i + 0.5) / rows
    u = {1: fu, 2: 1 - fu, 3: fv, 4: 1 - fv, 5: (fu + fv) / 2, 6: (1 - fu + fv) / 2,
         7: (fu + 1 - fv) / 2, 8: (2 - fu - fv) / 2}.get(order, fu)
    if order == 9:  # gradient layer: its luminance at the card centre
        g = ctx.layer_image(p.get("0022"))
        if g is not None:
            ga = _arr(g)
            lum = (ga[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)) * ga[..., 3]
            b = ctx.bounds
            gx = np.clip(((r.left() + (j + 0.5) * cw - b.left()) * ctx.res).astype(int), 0, img.width() - 1)
            gy = np.clip(((r.top() + (i + 0.5) * chh - b.top()) * ctx.res).astype(int), 0, img.height() - 1)
            u = lum[gy, gx]
    u = np.clip(u + rnd * (_hash2(i, j, seed) - 0.5), 0, 1)
    prog = np.clip((comp * (1 + width) - u) / width, 0, 1)
    ang = prog * math.pi
    sc = np.cos(ang)
    if axis == 3:
        horiz = _hash2(j, i, seed + 1) < 0.5
    else:
        horiz = np.full(lx.shape, axis == 2)
    ccx = r.left() + (j + 0.5) * cw
    ccy = r.top() + (i + 0.5) * chh
    safe = np.where(np.abs(sc) < 1e-3, 1e-3, sc) * card_scale
    sx = np.where(horiz, ccx + (lx - ccx) / safe, lx)
    sy = np.where(horiz, ly, ccy + (ly - ccy) / safe)
    inside = (np.abs(sx - ccx) <= cw / 2) & (np.abs(sy - ccy) <= chh / 2) & (sc > 0)  # back layer: none
    in_rect = (lx >= r.left()) & (lx < r.right()) & (ly >= r.top()) & (ly < r.bottom())
    pre = _pre(img)
    out = _lookup(ctx, pre, sx, sy) * inside[..., None]
    return _from_pre(np.where(in_rect[..., None], out, pre))
