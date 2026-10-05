"""More After Effects effects (parameters are looked up by their English display names)."""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import skia

from .paint import color4f

# effect match name -> (apply function, padding function)
REGISTRY: dict[str, tuple[Callable, Callable | None]] = {}


def effect(match_name: str, pad: Callable | None = None):
    def deco(fn):
        REGISTRY[match_name] = (fn, pad)
        return fn

    return deco


def _surface(img: skia.Image) -> skia.Surface:
    s = skia.Surface.MakeRasterN32Premul(img.width(), img.height())
    s.getCanvas().clear(skia.ColorTRANSPARENT)
    return s


def _blur_dims(img: skia.Image, sigma: float, dims: int) -> skia.Image:
    if sigma <= 0.01:
        return img
    sx = sigma if dims in (1, 2) else 0
    sy = sigma if dims in (1, 3) else 0
    s = _surface(img)
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(),
                            skia.Paint(ImageFilter=skia.ImageFilters.Blur(sx, sy, skia.TileMode.kDecal)))
    return s.makeImageSnapshot()


def _to_px(ctx, pt) -> tuple[float, float]:
    b = ctx.bounds
    return ((pt[0] - b.left()) * ctx.res, (pt[1] - b.top()) * ctx.res)


def _arr(img: skia.Image) -> np.ndarray:
    """Unpremultiplied float RGBA in 0..1."""
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                       alphaType=skia.AlphaType.kUnpremul_AlphaType).astype(np.float32) / 255.0


def _img(a: np.ndarray) -> skia.Image:
    out = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(out), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kUnpremul_AlphaType)


# --------------------------------------------------------------------------- blurs

@effect("ADBE Fast Blur", pad=lambda p: float(p.get("Blurriness", 0)) * 1.5 + 2)
def fast_blur(ctx, p, img):
    return _blur_dims(img, float(p.get("Blurriness", 0)) * ctx.res / 2.0, int(p.get("Blur Dimensions", 1)))


@effect("ADBE Box Blur2", pad=lambda p: float(p.get("Blur Radius", 0)) * 2 + 2)
def box_blur(ctx, p, img):
    r = float(p.get("Blur Radius", 0)) * ctx.res
    it = max(1, int(p.get("Iterations", 1)))
    # n box passes ~ gaussian with sigma = r * sqrt(n/3)
    return _blur_dims(img, r * math.sqrt(it / 3.0), int(p.get("Blur Dimensions", 1)))


# --------------------------------------------------------------------------- transform

def _geometry_pad(p):
    a = p.get("Anchor Point", [0, 0])
    pos = p.get("Position", a)
    sc = max(float(p.get("Scale Height", 100) or 100), float(p.get("Scale Width", 100) or 100), 100) / 100
    return math.hypot(pos[0] - a[0], pos[1] - a[1]) + 400 * (sc - 1) + 20


@effect("ADBE Geometry2", pad=_geometry_pad)
def transform(ctx, p, img):
    a = p.get("Anchor Point", [0, 0])
    pos = p.get("Position", a)
    uniform = bool(p.get("Uniform Scale", 1))
    sh = float(p.get("Scale Height", 100))
    sw = sh if uniform else float(p.get("Scale Width", 100))
    skew = float(p.get("Skew", 0))
    skew_axis = float(p.get("Skew Axis", 0))
    rot = float(p.get("Rotation", 0))
    op = float(p.get("Opacity", 100)) / 100.0
    m = skia.Matrix()
    px, py = _to_px(ctx, pos)
    ax, ay = _to_px(ctx, a)
    m.preTranslate(px, py)
    m.preRotate(rot)
    if skew:
        m.preRotate(skew_axis)
        m.preConcat(skia.Matrix.MakeAll(1, -math.tan(math.radians(skew)), 0, 0, 1, 0, 0, 0, 1))
        m.preRotate(-skew_axis)
    m.preScale(sw / 100.0, sh / 100.0)
    m.preTranslate(-ax, -ay)
    s = _surface(img)
    c = s.getCanvas()
    c.concat(m)
    c.drawImage(img, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear), skia.Paint(Alphaf=op))
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- glow

@effect("ADBE Glo2", pad=lambda p: float(p.get("Glow Radius", 10)) * 2 + 4)
def glow(ctx, p, img):
    based_on_alpha = int(p.get("Glow Based On", 2)) == 1
    thr = float(p.get("Glow Threshold", 60)) / 100.0
    radius = float(p.get("Glow Radius", 10)) * ctx.res
    intensity = float(p.get("Glow Intensity", 1))
    composite = int(p.get("Composite Original", 2))  # 1 on top, 2 behind, 3 none
    colors_ab = int(p.get("Glow Colors", 1)) != 1
    a = _arr(img)
    rgb, alpha = a[..., :3], a[..., 3:4]
    if based_on_alpha:
        level = alpha[..., 0]
    else:
        level = (rgb @ np.array([0.299, 0.587, 0.114], np.float32)) * alpha[..., 0]
    mask = np.clip((level - thr) / max(1e-3, 1 - thr), 0, 1)[..., None]
    src = np.concatenate([rgb * mask, alpha * mask], axis=2)
    if colors_ab:
        ca = np.array(p.get("Color A", [1, 1, 1, 1])[:3], np.float32)
        cb = np.array(p.get("Color B", [0, 0, 0, 1])[:3], np.float32)
        src[..., :3] = (ca * mask + cb * (1 - mask)) * (mask > 0)
    # premultiply and blur
    pre = src.copy()
    pre[..., :3] *= pre[..., 3:4]
    bl = _blur_dims(skia.Image.fromarray(np.ascontiguousarray((np.clip(pre, 0, 1) * 255).astype(np.uint8)),
                                         colorType=skia.ColorType.kRGBA_8888_ColorType,
                                         alphaType=skia.AlphaType.kPremul_AlphaType), radius / 2.5, 1)
    s = _surface(img)
    c = s.getCanvas()
    if composite == 2:
        c.drawImage(img, 0, 0)
    glow_paint = skia.Paint(BlendMode=skia.BlendMode.kPlus)
    for _ in range(max(1, int(math.ceil(intensity)))):
        f = min(1.0, intensity - _) if intensity > 1 else intensity
        glow_paint.setAlphaf(max(0.0, min(1.0, f)))
        c.drawImage(bl, 0, 0, skia.SamplingOptions(), glow_paint)
    if composite == 1:
        c.drawImage(img, 0, 0)
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- mattes / colour

@effect("ADBE Simple Choker")
def simple_choker(ctx, p, img):
    choke = float(p.get("Choke Matte", 0)) * ctx.res
    if abs(choke) < 0.05:
        return img
    f = skia.ImageFilters.Erode(choke, choke) if choke > 0 else skia.ImageFilters.Dilate(-choke, -choke)
    s = _surface(img)
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=f))
    return s.makeImageSnapshot()


@effect("ADBE Invert")
def invert(ctx, p, img):
    a = _arr(img)
    ch = int(p.get("Channel", 1))
    blend = float(p.get("Blend With Original", 0)) / 100.0
    out = a.copy()
    if ch in (1, 2, 3, 4, 5, 6, 7, 8, 9):  # RGB-ish variants -> invert colours
        out[..., :3] = 1 - a[..., :3]
    elif ch == 16:  # alpha
        out[..., 3] = 1 - a[..., 3]
    out = a * blend + out * (1 - blend)
    return _img(out)


@effect("ADBE Easy Levels2")
def levels(ctx, p, img):
    ib = float(p.get("Input Black", 0))
    iw = float(p.get("Input White", 1))
    g = float(p.get("Gamma", 1)) or 1
    ob = float(p.get("Output Black", 0))
    ow = float(p.get("Output White", 1))
    a = _arr(img)
    x = np.clip((a[..., :3] - ib) / max(1e-6, iw - ib), 0, 1) ** (1 / g)
    a[..., :3] = ob + x * (ow - ob)
    return _img(a)


@effect("ADBE Brightness & Contrast 2")
def brightness_contrast(ctx, p, img):
    b = float(p.get("Brightness", 0)) / 100.0
    k = float(p.get("Contrast", 0)) / 100.0
    a = _arr(img)
    x = a[..., :3] + b
    a[..., :3] = (x - 0.5) * (1 + k) + 0.5
    return _img(a)


@effect("ADBE HUE SATURATION")
def hue_saturation(ctx, p, img):
    import colorsys  # noqa: F401  (vectorised below)

    hue = float(p.get("Master Hue", 0)) / 360.0
    sat = float(p.get("Master Saturation", 0)) / 100.0
    light = float(p.get("Master Lightness", 0)) / 100.0
    a = _arr(img)
    rgb = a[..., :3]
    mx, mn = rgb.max(-1), rgb.min(-1)
    l = (mx + mn) / 2
    d = mx - mn
    s = np.where(d == 0, 0, d / (1 - np.abs(2 * l - 1) + 1e-6))
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.where(d == 0, 0,
                 np.where(mx == r, ((g - b) / (d + 1e-6)) % 6,
                          np.where(mx == g, (b - r) / (d + 1e-6) + 2, (r - g) / (d + 1e-6) + 4))) / 6
    h = (h + hue) % 1
    s = np.clip(s * (1 + sat), 0, 1)
    l = np.clip(l + light * (1 - l if light > 0 else l), 0, 1)
    c = (1 - np.abs(2 * l - 1)) * s
    hp = h * 6
    x = c * (1 - np.abs(hp % 2 - 1))
    z = np.zeros_like(c)
    seg = np.floor(hp).astype(int) % 6
    lut = [(c, x, z), (x, c, z), (z, c, x), (z, x, c), (x, z, c), (c, z, x)]
    out = np.zeros_like(rgb)
    for i, (rr, gg, bb) in enumerate(lut):
        msk = seg == i
        out[..., 0] = np.where(msk, rr, out[..., 0])
        out[..., 1] = np.where(msk, gg, out[..., 1])
        out[..., 2] = np.where(msk, bb, out[..., 2])
    a[..., :3] = out + (l - c / 2)[..., None]
    return _img(a)


@effect("ADBE Mosaic")
def mosaic(ctx, p, img):
    hb = max(1, int(p.get("Horizontal Blocks", 10)))
    vb = max(1, int(p.get("Vertical Blocks", 10)))
    small = skia.Surface.MakeRasterN32Premul(hb, vb)
    small.getCanvas().drawImageRect(img, skia.Rect.MakeWH(hb, vb), skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear))
    s = _surface(img)
    s.getCanvas().drawImageRect(small.makeImageSnapshot(), skia.Rect.MakeWH(img.width(), img.height()),
                                skia.SamplingOptions(skia.FilterMode.kNearest))
    return s.makeImageSnapshot()


@effect("CC Composite")
def cc_composite(ctx, p, img):
    original = getattr(ctx, "original", None)
    if original is None:
        return img
    op = float(p.get("Opacity", 100)) / 100.0
    mode = int(p.get("Composite Original", 1))
    s = _surface(img)
    c = s.getCanvas()
    c.drawImage(img, 0, 0)
    blend = {1: skia.BlendMode.kSrcOver, 2: skia.BlendMode.kDstOver, 3: skia.BlendMode.kDstIn, 4: skia.BlendMode.kDstOut,
             5: skia.BlendMode.kPlus, 6: skia.BlendMode.kMultiply, 7: skia.BlendMode.kScreen}.get(mode, skia.BlendMode.kSrcOver)
    c.drawImage(original, 0, 0, skia.SamplingOptions(), skia.Paint(Alphaf=op, BlendMode=blend))
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- transitions

def _mask_apply(img: skia.Image, mask: np.ndarray) -> skia.Image:
    a = _arr(img)
    a[..., 3] *= np.clip(mask, 0, 1)
    return _img(a)


@effect("ADBE Venetian Blinds")
def venetian_blinds(ctx, p, img):
    comp = float(p.get("Transition Completion", 0)) / 100.0
    if comp <= 0:
        return img
    ang = math.radians(float(p.get("Direction", 0)))
    width = max(1.0, float(p.get("Width", 10)) * ctx.res)
    feather = float(p.get("Feather", 0)) * ctx.res
    h, w = img.height(), img.width()
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    proj = xx * math.sin(ang) + yy * math.cos(ang)
    phase = (proj % width) / width  # 0..1 across each blind
    edge = comp
    if feather > 0:
        f = feather / width
        mask = np.clip((phase - edge) / max(f, 1e-3) + 0.5, 0, 1)
    else:
        mask = (phase >= edge).astype(np.float32)
    return _mask_apply(img, mask)


@effect("ADBE Radial Wipe")
def radial_wipe(ctx, p, img):
    comp = float(p.get("Transition Completion", 0)) / 100.0
    if comp <= 0:
        return img
    start = float(p.get("Start Angle", 0))
    cx, cy = _to_px(ctx, p.get("Wipe Center", [ctx.bounds.centerX(), ctx.bounds.centerY()]))
    mode = int(p.get("Wipe", 1))  # 1 clockwise, 2 counterclockwise, 3 both
    feather = float(p.get("Feather", 0))
    h, w = img.height(), img.width()
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ang = (np.degrees(np.arctan2(xx - cx, -(yy - cy))) - start) % 360  # 0 = up, clockwise
    if mode == 2:
        ang = (360 - ang) % 360
    if mode == 3:
        ang = np.minimum(ang, 360 - ang) * 2
    cut = comp * 360
    mask = np.clip((ang - cut) / max(feather, 0.5), 0, 1) if feather > 0 else (ang >= cut).astype(np.float32)
    return _mask_apply(img, mask)


# --------------------------------------------------------------------------- tiling

def _repetile_pad(p):
    return max(float(p.get("Expand Right", 0)), float(p.get("Expand Left", 0)),
               float(p.get("Expand Down", 0)), float(p.get("Expand Up", 0)))


@effect("CC RepeTile", pad=_repetile_pad)
def cc_repetile(ctx, p, img):
    """Repeat the layer's own (source) rect into the expanded area."""
    r = ctx.renderer
    src = r.layer_content_bounds(ctx.layer, ctx.t)
    if src.isEmpty():
        return img
    x0, y0 = _to_px(ctx, (src.left(), src.top()))
    x1, y1 = _to_px(ctx, (src.right(), src.bottom()))
    tile = img.makeSubset(skia.IRect.MakeLTRB(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))))
    if tile is None:
        return img
    mode = int(p.get("Tiling", 1))  # 1 repeat, 2 checker flip H, 3 checker flip V, 4 checker flip, 5+ brick/…
    tm = skia.TileMode.kMirror if mode in (2, 3, 4) else skia.TileMode.kRepeat
    shader = tile.makeShader(tm, tm, skia.SamplingOptions(skia.FilterMode.kLinear), skia.Matrix.Translate(x0, y0))
    er = float(p.get("Expand Right", 0)) * ctx.res
    el = float(p.get("Expand Left", 0)) * ctx.res
    ed = float(p.get("Expand Down", 0)) * ctx.res
    eu = float(p.get("Expand Up", 0)) * ctx.res
    s = _surface(img)
    s.getCanvas().drawRect(skia.Rect.MakeLTRB(x0 - el, y0 - eu, x1 + er, y1 + ed), skia.Paint(Shader=shader))
    return s.makeImageSnapshot()


@effect("ADBE Tile")
def motion_tile(ctx, p, img):
    r = ctx.renderer
    src = r.layer_content_bounds(ctx.layer, ctx.t)
    cx, cy = _to_px(ctx, p.get("Tile Center", [src.centerX(), src.centerY()]))
    tw = float(p.get("Tile Width", 100)) / 100
    th = float(p.get("Tile Height", 100)) / 100
    ow = float(p.get("Output Width", 100)) / 100
    oh = float(p.get("Output Height", 100)) / 100
    mirror = bool(p.get("Mirror Edges", 0))
    x0, y0 = _to_px(ctx, (src.left(), src.top()))
    x1, y1 = _to_px(ctx, (src.right(), src.bottom()))
    tile = img.makeSubset(skia.IRect.MakeLTRB(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))))
    if tile is None:
        return img
    m = skia.Matrix()
    m.preTranslate(cx, cy)
    m.preScale(tw, th)
    m.preTranslate(-(x1 - x0) / 2, -(y1 - y0) / 2)
    tm = skia.TileMode.kMirror if mirror else skia.TileMode.kRepeat
    shader = tile.makeShader(tm, tm, skia.SamplingOptions(skia.FilterMode.kLinear), m)
    w, h = (x1 - x0), (y1 - y0)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    s = _surface(img)
    s.getCanvas().drawRect(skia.Rect.MakeLTRB(mx - w * ow / 2, my - h * oh / 2, mx + w * ow / 2, my + h * oh / 2), skia.Paint(Shader=shader))
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- generators

@effect("ADBE Laser")
def beam(ctx, p, img):
    a = p.get("Starting Point", [0, 0])
    b = p.get("Ending Point", [100, 100])
    length = float(p.get("Length", 25)) / 100
    tm = float(p.get("Time", 0)) / 100
    t0 = float(p.get("Starting Thickness", 8)) * ctx.res
    t1 = float(p.get("Ending Thickness", 8)) * ctx.res
    soft = float(p.get("Softness", 60)) / 100
    inside = p.get("Inside Color", [1, 1, 1, 1])
    outside = p.get("Outside Color", [0.6, 0.4, 1, 1])
    on_original = bool(p.get("Composite On Original", 0))
    head = tm * (1 + length)
    tail = head - length
    s0, s1 = max(0.0, tail), min(1.0, head)
    s = _surface(img)
    c = s.getCanvas()
    if on_original:
        c.drawImage(img, 0, 0)
    if s1 > s0:
        ax, ay = _to_px(ctx, a)
        bx, by = _to_px(ctx, b)
        p0 = (ax + (bx - ax) * s0, ay + (by - ay) * s0)
        p1 = (ax + (bx - ax) * s1, ay + (by - ay) * s1)
        w0, w1 = t0 + (t1 - t0) * s0, t0 + (t1 - t0) * s1
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy) or 1
        nx, ny = -dy / L, dx / L
        path = skia.Path()
        path.moveTo(p0[0] + nx * w0 / 2, p0[1] + ny * w0 / 2)
        path.lineTo(p1[0] + nx * w1 / 2, p1[1] + ny * w1 / 2)
        path.lineTo(p1[0] - nx * w1 / 2, p1[1] - ny * w1 / 2)
        path.lineTo(p0[0] - nx * w0 / 2, p0[1] - ny * w0 / 2)
        path.close()
        glow = skia.Paint(AntiAlias=True, Color4f=color4f(outside[:3]))
        if soft > 0:
            glow.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(w0, w1) * soft * 0.5))
        c.drawPath(path, glow)
        core = skia.Path(path)
        core.transform(skia.Matrix.Translate(0, 0))
        cp = skia.Paint(AntiAlias=True, Color4f=color4f(inside[:3]))
        c.save()
        c.translate((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        c.scale(1, 1)
        c.translate(-(p0[0] + p1[0]) / 2, -(p0[1] + p1[1]) / 2)
        inner = skia.Path()
        k = 0.45
        inner.moveTo(p0[0] + nx * w0 * k / 2, p0[1] + ny * w0 * k / 2)
        inner.lineTo(p1[0] + nx * w1 * k / 2, p1[1] + ny * w1 * k / 2)
        inner.lineTo(p1[0] - nx * w1 * k / 2, p1[1] - ny * w1 * k / 2)
        inner.lineTo(p0[0] - nx * w0 * k / 2, p0[1] - ny * w0 * k / 2)
        inner.close()
        c.drawPath(inner, cp)
        c.restore()
    return s.makeImageSnapshot()


@effect("ADBE 4ColorGradient")
def four_color_gradient(ctx, p, img):
    def lst(v, d):
        return v if isinstance(v, (list, tuple)) else d

    # 0001 Point 1, 0002 Color 1, … 0008 Color 4, 0009 Blend, 0010 Jitter, 0011 Opacity
    pts = [_to_px(ctx, lst(p.get(f"{2 * i - 1:04d}"), [0, 0])) for i in range(1, 5)]
    cols = [np.array(lst(p.get(f"{2 * i:04d}"), [1, 1, 1, 1])[:3], np.float32) for i in range(1, 5)]
    blend = max(1.0, float(p.get("0009", 100) or 100))
    opv = p.get("0011", 1.0)
    op = float(opv) if isinstance(opv, (int, float)) and opv <= 1 else (float(opv) / 100 if isinstance(opv, (int, float)) else 1.0)
    h, w = img.height(), img.width()
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    weights = []
    for (px, py) in pts:
        d = np.hypot(xx - px, yy - py) + 1e-3
        weights.append(1 / d ** (2 * blend / 100))
    wsum = sum(weights)
    rgb = sum(wt[..., None] * c for wt, c in zip(weights, cols)) / wsum[..., None]
    a = _arr(img)
    a[..., :3] = a[..., :3] * (1 - op) + rgb * op
    return _img(a)


# --------------------------------------------------------------------------- distort

def _hash3(ix: np.ndarray, iy: np.ndarray, iz: np.ndarray, seed: int) -> np.ndarray:
    """Lattice values in [-1, 1] (deterministic per seed); uint32 arithmetic wraps like the 32-bit hash."""
    k = np.uint32((int(iz) * 2147483647 + seed * 1274126177) & 0xFFFFFFFF)
    with np.errstate(over="ignore"):
        n = ix.astype(np.uint32) * np.uint32(374761393) + iy.astype(np.uint32) * np.uint32(668265263) + k
        n = (n ^ (n >> np.uint32(13))) * np.uint32(1274126177)
    n = n ^ (n >> np.uint32(16))
    return (n & np.uint32(0xFFFF)).astype(np.float32) / 32767.5 - 1.0


def _vnoise3(x: np.ndarray, y: np.ndarray, z: float, seed: int) -> np.ndarray:
    """Smooth 3D value noise in [-1, 1]; z animates it (one unit per evolution revolution)."""
    x0, y0, z0 = np.floor(x), np.floor(y), math.floor(z)
    fx, fy, fz = x - x0, y - y0, z - z0
    ux, uy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    uz = fz * fz * (3 - 2 * fz)
    ix, iy = x0.astype(np.int64), y0.astype(np.int64)
    out = 0.0
    for dz, wz in ((0, 1 - uz), (1, uz)):
        iz = np.int64(z0 + dz)
        a = _hash3(ix, iy, iz, seed) * (1 - ux) + _hash3(ix + 1, iy, iz, seed) * ux
        b = _hash3(ix, iy + 1, iz, seed) * (1 - ux) + _hash3(ix + 1, iy + 1, iz, seed) * ux
        out = out + (a * (1 - uy) + b * uy) * wz
    return out


def _fractal(x: np.ndarray, y: np.ndarray, z: float, seed: int, complexity: float) -> np.ndarray:
    octaves = max(1.0, complexity)
    total, amp, norm, f = 0.0, 1.0, 0.0, 1.0
    for o in range(int(math.ceil(octaves))):
        w = min(1.0, octaves - o)  # fractional complexity fades the last octave in
        total = total + _vnoise3(x * f + o * 17.3, y * f + o * 31.7, z * f, seed + o * 101) * amp * w
        norm += amp * w
        amp *= 0.5
        f *= 2.0
    return total / max(norm, 1e-6)


def _sample(a: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Bilinear lookup of a premultiplied RGBA array at pixel coordinates (transparent outside)."""
    h, w = a.shape[:2]
    pad = np.zeros((h + 2, w + 2, 4), np.float32)
    pad[1:-1, 1:-1] = a
    x = np.clip(sx + 1, 0, w + 0.999)
    y = np.clip(sy + 1, 0, h + 0.999)
    x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    x1, y1 = np.minimum(x0 + 1, w + 1), np.minimum(y0 + 1, h + 1)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    top = pad[y0, x0] * (1 - fx) + pad[y0, x1] * fx
    bot = pad[y1, x0] * (1 - fx) + pad[y1, x1] * fx
    return top * (1 - fy) + bot * fy


@effect("ADBE Turbulent Displace", pad=lambda p: abs(float(p.get("0002", 50))) * max(1.0, float(p.get("0003", 100))) / 100 + 2)
def turbulent_displace(ctx, p, img):
    # 0001 Displacement, 0002 Amount, 0003 Size, 0004 Offset, 0005 Complexity, 0006 Evolution,
    # 0008 Cycle Evolution, 0009 Cycle, 0011 Random Seed, 0012 Pinning
    kind = int(p.get("0001", 1) or 1)
    amount = float(p.get("0002", 50))
    size = max(1.0, float(p.get("0003", 100)))
    if abs(amount) < 0.01:
        return img
    off = p.get("0004", [0, 0])
    off = list(off) if isinstance(off, (list, tuple)) else [0, 0]
    if max(abs(off[0]), abs(off[1])) > 20000:  # stored at 100x in some projects
        off = [off[0] / 100, off[1] / 100]
    complexity = float(p.get("0005", 1) or 1)
    evo = float(p.get("0006", 0)) / 360.0
    if int(p.get("0008", 0) or 0):
        evo %= max(1.0, float(p.get("0009", 1) or 1))
    seed = int(p.get("0011", 0) or 0)
    pinning = int(p.get("0012", 3) or 3)
    if kind in (4, 5, 6):  # "smoother" variants: less fine detail
        complexity = max(1.0, complexity * 0.5)

    h, w = img.height(), img.width()
    res, b = ctx.res, ctx.bounds
    # displacement field on a coarse grid (the noise is smooth at the scale of Size), then upsampled
    step = max(1, int(size * res / 6))
    gw, gh = w // step + 2, h // step + 2
    gy, gx = np.mgrid[0:gh, 0:gw].astype(np.float32)
    lx = b.left() + gx * step / res  # layer coordinates
    ly = b.top() + gy * step / res
    u, v = (lx - off[0]) / size, (ly - off[1]) / size
    if kind in (2, 3, 5, 6):  # bulge / twist follow the gradient of a single noise field
        e = 0.05
        n0 = _fractal(u, v, evo, seed, complexity)
        du = (_fractal(u + e, v, evo, seed, complexity) - n0) / e
        dv = (_fractal(u, v + e, evo, seed, complexity) - n0) / e
        dx, dy = (du, dv) if kind in (2, 5) else (-dv, du)
        dx, dy = dx * 0.25, dy * 0.25
    else:
        dx = _fractal(u, v, evo, seed, complexity)
        dy = _fractal(u + 57.1, v + 23.9, evo, seed + 7, complexity)
        if kind == 7:
            dx = dx * 0
        elif kind == 8:
            dy = dy * 0
        elif kind == 9:
            dy = dx
    # layer units: the displacement grows with Size (Amount 15 / Size 10 only roughens edges)
    dx, dy = dx * amount * size / 100, dy * amount * size / 100

    # pinning: no displacement at the layer's edges / corners
    if pinning != 1:
        lb = ctx.renderer.layer_content_bounds(ctx.layer, ctx.t)
        if lb.width() > 0 and lb.height() > 0:
            ramp_x = min(size, lb.width() / 2)
            ramp_y = min(size, lb.height() / 2)
            wx = np.clip(np.minimum(lx - lb.left(), lb.right() - lx) / ramp_x, 0, 1)
            wy = np.clip(np.minimum(ly - lb.top(), lb.bottom() - ly) / ramp_y, 0, 1)
            if pinning == 2:
                wgt = np.maximum(wx, wy)
            elif pinning == 4:
                wgt = wy
            elif pinning == 5:
                wgt = wx
            else:
                wgt = np.minimum(wx, wy)
            dx, dy = dx * wgt, dy * wgt

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    px, py = xx / step, yy / step
    flat = np.stack([dx, dy], axis=-1)
    i0, j0 = np.floor(px).astype(np.int32), np.floor(py).astype(np.int32)
    fx, fy = (px - i0)[..., None], (py - j0)[..., None]
    d = (flat[j0, i0] * (1 - fx) + flat[j0, i0 + 1] * fx) * (1 - fy) + (flat[j0 + 1, i0] * (1 - fx) + flat[j0 + 1, i0 + 1] * fx) * fy
    pre = img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                      alphaType=skia.AlphaType.kPremul_AlphaType).astype(np.float32) / 255.0
    out = _sample(pre, xx - d[..., 0] * res, yy - d[..., 1] * res)
    out = (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(out), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kPremul_AlphaType)


# the other effect modules register themselves on import
from . import effects_color, effects_layers, effects_sim, effects_warp  # noqa: E402,F401
