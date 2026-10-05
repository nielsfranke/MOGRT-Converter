"""More After Effects effects: colour and matte effects (registered in effects_extra.REGISTRY)."""

from __future__ import annotations

import math

import numpy as np
import skia

from .effects_extra import _arr, _img, _surface, effect  # noqa: F401


def _blend(orig: np.ndarray, out: np.ndarray, blend_pct: float) -> np.ndarray:
    """'Blend With Original' in percent."""
    b = max(0.0, min(1.0, float(blend_pct or 0) / 100.0))
    return out if b <= 0 else out * (1 - b) + orig * b


def _lum(rgb: np.ndarray) -> np.ndarray:
    return rgb @ np.array([0.299, 0.587, 0.114], np.float32)


def _premul_arr(img: skia.Image) -> np.ndarray:
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                       alphaType=skia.AlphaType.kPremul_AlphaType).astype(np.float32) / 255.0


def _premul_img(a: np.ndarray) -> skia.Image:
    out = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(out), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kPremul_AlphaType)


def _filtered(img: skia.Image, f: skia.ImageFilter) -> skia.Image:
    s = _surface(img)
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=f))
    return s.makeImageSnapshot()


# --------------------------------------------------------------------------- no-ops

@effect("ADBE Broadcast Colors")  # 8-bit output is legal enough
@effect("ADBE ATG Extract")  # Inner/Outer Key without selected paths does nothing
@effect("ADBE Separate XYZ Position")  # helper effect, renders nothing
def _passthrough(ctx, p, img):
    return img


# --------------------------------------------------------------------------- colour

def _to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


@effect("ADBE Exposure2")
def exposure(ctx, p, img):
    # 0001 Channels (1 master, 2 individual), 0003 Exposure, 0004 Offset, 0005 Gamma,
    # 0008/0013/0018 R/G/B Exposure, 0009/0014/0019 Offset, 0010/0015/0020 Gamma, 0022 Bypass Linear
    if int(p.get("0001", 1) or 1) == 2:
        ex = [float(p.get(k, 0)) for k in ("0008", "0013", "0018")]
        off = [float(p.get(k, 0)) for k in ("0009", "0014", "0019")]
        gam = [float(p.get(k, 1) or 1) for k in ("0010", "0015", "0020")]
    else:
        ex = [float(p.get("0003", 0))] * 3
        off = [float(p.get("0004", 0))] * 3
        gam = [float(p.get("0005", 1) or 1)] * 3
    if all(abs(e) < 1e-6 for e in ex) and all(abs(o) < 1e-6 for o in off) and all(abs(g - 1) < 1e-6 for g in gam):
        return img
    linear = not int(p.get("0022", 0) or 0)
    a = _arr(img)
    rgb = _to_linear(a[..., :3]) if linear else a[..., :3]
    rgb = rgb * np.exp2(np.array(ex, np.float32)) + np.array(off, np.float32)
    rgb = np.clip(rgb, 0, None) ** (1.0 / np.maximum(np.array(gam, np.float32), 1e-3))
    a[..., :3] = _to_srgb(rgb) if linear else rgb
    return _img(a)


@effect("ADBE Tritone")
def tritone(ctx, p, img):
    hi = np.array(p.get("0001", [1, 1, 1, 1])[:3], np.float32)
    mid = np.array(p.get("0002", [0.5, 0.5, 0.5, 1])[:3], np.float32)
    lo = np.array(p.get("0003", [0, 0, 0, 1])[:3], np.float32)
    a = _arr(img)
    orig = a[..., :3].copy()
    l = _lum(orig)[..., None]
    lower = lo + (mid - lo) * np.clip(l * 2, 0, 1)
    upper = mid + (hi - mid) * np.clip(l * 2 - 1, 0, 1)
    out = np.where(l < 0.5, lower, upper)
    a[..., :3] = _blend(orig, out, p.get("0004", 0))
    return _img(a)


def _hls(rgb: np.ndarray):
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    l = (mx + mn) / 2
    d = mx - mn
    s = np.where(d < 1e-6, 0, d / np.maximum(1e-6, 1 - np.abs(2 * l - 1)))
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    dd = np.maximum(d, 1e-6)
    h = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) / 6
    h = np.where(d < 1e-6, 0, h)
    return h, l, np.clip(s, 0, 1)


@effect("ADBE Shift Channels")
def shift_channels(ctx, p, img):
    a = _arr(img)
    rgb = a[..., :3]
    h, l, s = _hls(rgb)
    ones = np.ones_like(l)
    # 10 is "Off": templates split RGB into three Screen copies that keep one channel each
    src = {1: a[..., 3], 2: rgb[..., 0], 3: rgb[..., 1], 4: rgb[..., 2], 5: _lum(rgb), 6: h, 7: l, 8: s,
           9: ones, 10: ones * 0, 11: ones * 0.5}
    pick = lambda key, d: src.get(int(p.get(key, d) or d), src[d]).copy()  # noqa: E731
    out = np.empty_like(a)
    out[..., 3] = pick("0001", 1)
    out[..., 0] = pick("0002", 2)
    out[..., 1] = pick("0003", 3)
    out[..., 2] = pick("0004", 4)
    return _img(out)


@effect("ADBE Sharpen")
def sharpen(ctx, p, img):
    amt = float(p.get("0001", 0)) / 100.0
    if amt <= 0:
        return img
    a = _premul_arr(img)
    bl = _premul_arr(_filtered(img, skia.ImageFilters.Blur(1.0 * ctx.res, 1.0 * ctx.res, skia.TileMode.kClamp)))
    out = a + (a - bl) * amt * 2
    out[..., 3] = a[..., 3]
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return _premul_img(out)


@effect("ADBE Emboss")
def emboss(ctx, p, img):
    ang = math.radians(float(p.get("0001", 45)))
    relief = max(0.0, float(p.get("0002", 1.5))) * ctx.res
    contrast = float(p.get("0003", 100)) / 100.0
    a = _arr(img)
    l = _lum(a[..., :3] * a[..., 3:4])
    dx, dy = math.cos(ang) * relief, -math.sin(ang) * relief
    ix, iy = int(round(dx)), int(round(dy))
    sh = np.roll(np.roll(l, iy, axis=0), ix, axis=1)
    g = np.clip(0.5 + (l - sh) * contrast, 0, 1)
    orig = a[..., :3].copy()
    a[..., :3] = _blend(orig, np.repeat(g[..., None], 3, axis=2), p.get("0004", 0))
    return _img(a)


# --------------------------------------------------------------------------- mattes

def _morph(img: skia.Image, r: float, dilate: bool, direction: int) -> skia.Image:
    if r < 0.5:
        return img
    rx = r if direction in (1, 2) else 0
    ry = r if direction in (1, 3) else 0
    f = skia.ImageFilters.Dilate(rx, ry) if dilate else skia.ImageFilters.Erode(rx, ry)
    return _filtered(img, f)


@effect("ADBE Minimax", pad=lambda p: float(p.get("0002", 0)) + 2)
def minimax(ctx, p, img):
    op = int(p.get("0001", 1) or 1)
    r = float(p.get("0002", 0)) * ctx.res
    d = int(p.get("0004", 1) or 1)
    steps = {1: [True], 2: [False], 3: [False, True], 4: [True, False]}.get(op, [True])
    for dil in steps:
        img = _morph(img, r, dil, d)
    return img


@effect("ADBE Matte Choker")
def matte_choker(ctx, p, img):
    # two passes of (geometric softness blur -> choke levels -> gray level softness)
    a = _arr(img)
    alpha = a[..., 3].copy()
    for _ in range(max(1, int(p.get("0007", 1) or 1))):
        for soft_k, choke_k, gray_k in (("0001", "0002", "0003"), ("0004", "0005", "0006")):
            soft = float(p.get(soft_k, 0)) * ctx.res
            choke = float(p.get(choke_k, 0)) / 255.0  # -127..127 levels
            gray = max(0.001, float(p.get(gray_k, 0) or 0))  # 0..1
            if soft > 0.05:
                m = _img(np.dstack([np.zeros_like(alpha)] * 3 + [alpha]))
                alpha = _arr(_filtered(m, skia.ImageFilters.Blur(soft / 2, soft / 2, skia.TileMode.kDecal)))[..., 3]
            # choke > 0 shrinks, < 0 spreads; gray level softness widens the transition. Solid
            # areas stay solid (only soft edges move), so a hard matte needs geometric softness
            width = max(0.01, min(1.0, gray))
            centre = min(max(0.5 + choke, width / 2), 1 - width / 2)
            alpha = np.clip((alpha - centre) / width + 0.5, 0, 1)
    a[..., 3] = alpha
    return _img(a)


# --------------------------------------------------------------------------- curves

def _curves_lut(ctx) -> bytes | None:
    """The layer's Curves effect as raw data: 4 byte header, 5 x 256 byte LUTs (RGB, R, G, B, A),
    then the control points. The effect function does not get the effect itself, so the n-th call
    for this layer image uses the n-th Curves effect."""
    n = ctx.__dict__.get("_curves_n", 0)
    ctx._curves_n = n + 1
    parade = ctx.renderer._prop(ctx.layer, "ADBE Effect Parade")
    found = [e for e in (parade or []) if e.match_name == "ADBE CurvesCustom" and getattr(e, "enabled", True)]
    for e in found[n:n + 1]:
        try:
            c = next(c for c in e._tdgp.chunks if getattr(c, "list_type", "") == "aRbs")
            data = bytes(c.chunks[0].data)
        except (StopIteration, AttributeError, IndexError):
            continue
        if len(data) >= 4 + 1280:
            return data[4:4 + 1280]
    return None


@effect("ADBE CurvesCustom")
def curves(ctx, p, img):
    data = _curves_lut(ctx)
    if data is None:
        return img
    lut = np.frombuffer(data[:1280], np.uint8).reshape(5, 256)
    ident = np.arange(256, dtype=np.uint8)
    if all((lut[i] == ident).all() for i in range(5)):
        return img
    a = img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType, alphaType=skia.AlphaType.kUnpremul_AlphaType).copy()
    for ch in range(3):
        a[..., ch] = lut[1 + ch][lut[0][a[..., ch]]]
    a[..., 3] = lut[4][a[..., 3]]
    return skia.Image.fromarray(np.ascontiguousarray(a), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kUnpremul_AlphaType)


# --------------------------------------------------------------------------- scatter

@effect("ADBE Scatter", pad=lambda p: float(p.get("0001", 0)) + 2)
def scatter(ctx, p, img):
    amount = float(p.get("0001", 0)) * ctx.res
    if amount < 0.5:
        return img
    grain = int(p.get("0002", 1) or 1)
    seed = int(round(ctx.t * 1000)) if int(p.get("0003", 0) or 0) else 0
    h, w = img.height(), img.width()
    rng = np.random.default_rng(seed + 12345)
    dx = rng.uniform(-amount, amount, (h, w)) if grain in (1, 2) else 0
    dy = rng.uniform(-amount, amount, (h, w)) if grain in (1, 3) else 0
    # samples stay inside the layer: a full-frame background keeps clean edges
    x0, y0, x1, y1 = 0, 0, w - 1, h - 1
    if hasattr(ctx, "layer_rect"):
        lr, b = ctx.layer_rect(), ctx.bounds
        x0 = max(0, int(math.ceil((lr.left() - b.left()) * ctx.res)))
        y0 = max(0, int(math.ceil((lr.top() - b.top()) * ctx.res)))
        x1 = min(w - 1, int((lr.right() - b.left()) * ctx.res) - 1)
        y1 = min(h - 1, int((lr.bottom() - b.top()) * ctx.res) - 1)
        if x1 < x0 or y1 < y0:
            x0, y0, x1, y1 = 0, 0, w - 1, h - 1
    yy, xx = np.mgrid[0:h, 0:w]
    sx = np.clip(np.rint(xx + dx).astype(np.int32), x0, x1)
    sy = np.clip(np.rint(yy + dy).astype(np.int32), y0, y1)
    a = img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType, alphaType=skia.AlphaType.kPremul_AlphaType)
    return skia.Image.fromarray(np.ascontiguousarray(a[sy, sx]), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kPremul_AlphaType)


# --------------------------------------------------------------------------- find edges

@effect("ADBE Find Edges")
def find_edges(ctx, p, img):
    # 0001 Invert, 0002 Blend With Original; edges are dark lines on white (light on black when inverted)
    a = _arr(img)
    rgb = a[..., :3] * a[..., 3:4]  # edges of the visible colour, transparent counts as black
    pad = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode="edge")
    gx = (pad[1:-1, 2:] - pad[1:-1, :-2]) * 2 + (pad[:-2, 2:] - pad[:-2, :-2]) + (pad[2:, 2:] - pad[2:, :-2])
    gy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) * 2 + (pad[2:, :-2] - pad[:-2, :-2]) + (pad[2:, 2:] - pad[:-2, 2:])
    edge = np.clip(np.hypot(gx, gy) / 2, 0, 1)
    res = edge if int(p.get("0001", 0) or 0) else 1 - edge
    blend = float(p.get("0002", 0)) / 100
    a[..., :3] = res * (1 - blend) + a[..., :3] * blend
    return _img(a)


# --------------------------------------------------------------------------- threshold

@effect("CC Threshold")
def cc_threshold(ctx, p, img):
    # 0001 Threshold (0..1), 0002 Channel (1 luminance, 2 red, 3 green, 4 blue, 5 alpha), 0003 Invert,
    # 0004 Blend w. Original
    a = _arr(img)
    ch = int(float(p.get("0002", 1) or 1))
    src = a[..., 3] if ch == 5 else (a[..., ch - 2] if ch in (2, 3, 4) else a[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32))
    on = (src >= float(p.get("0001", 0.5))).astype(np.float32)
    if int(p.get("0003", 0) or 0):
        on = 1 - on
    blend = float(p.get("0004", 0)) / 100
    a[..., :3] = on[..., None] * (1 - blend) + a[..., :3] * blend
    return _img(a)


@effect("ADBE Posterize")
def posterize(ctx, p, img):
    # 0001 Level: tonal levels per channel
    n = max(2, int(round(float(p.get("0001", 6)))))
    a = _arr(img)
    a[..., :3] = np.round(a[..., :3] * (n - 1)) / (n - 1)
    return _img(a)
