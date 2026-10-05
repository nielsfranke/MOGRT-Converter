"""More After Effects effects: effects that read other layers, and generators (registered in effects_extra.REGISTRY)."""

from __future__ import annotations

import math

import numpy as np
import skia

from .effects_extra import _arr, _img, _sample, _surface, _vnoise3, effect  # noqa: F401


# --------------------------------------------------------------------------- helpers

def _premul(img: skia.Image) -> np.ndarray:
    """Premultiplied float RGBA in 0..1."""
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                       alphaType=skia.AlphaType.kPremul_AlphaType).astype(np.float32) / 255.0


def _from_premul(a: np.ndarray) -> skia.Image:
    out = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(out), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kPremul_AlphaType)


def _hsl(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    l = (mx + mn) / 2
    d = mx - mn
    s = np.where(d < 1e-6, 0, d / np.maximum(1e-6, 1 - np.abs(2 * l - 1)))
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    dd = np.maximum(d, 1e-6)
    h = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) / 6
    return np.where(d < 1e-6, 0, h), np.clip(s, 0, 1), l


def _channel(a: np.ndarray, which: int, neutral: float = 0.0) -> np.ndarray:
    """A map channel of an unpremultiplied RGBA array (AE enum: 1 red … 5 luminance, 6 hue, 7 lightness,
    8 saturation, 9 full, 10 half, 11 off). Transparent map pixels read as `neutral`."""
    rgb, al = a[..., :3], a[..., 3]
    if which == 4:
        return al
    if which == 9:
        return np.ones_like(al)
    if which == 10:
        return np.full_like(al, 0.5)
    if which == 11:
        return np.zeros_like(al)
    if which in (1, 2, 3):
        v = rgb[..., which - 1]
    elif which in (6, 7, 8):
        h, s, l = _hsl(rgb)
        v = {6: h, 7: l, 8: s}[which]
    else:
        v = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    return v * al + neutral * (1 - al)


def _gauss(m: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur of a single-channel float array (via skia)."""
    if sigma <= 0.05:
        return m
    u8 = (np.clip(m, 0, 1) * 65535).astype(np.uint16)
    # 16-bit grey is not available as a skia image type: blur the high and low bytes as two channels
    rgba = np.zeros(m.shape + (4,), np.uint8)
    rgba[..., 0] = u8 >> 8
    rgba[..., 1] = u8 & 0xFF
    rgba[..., 3] = 255
    img = skia.Image.fromarray(np.ascontiguousarray(rgba), colorType=skia.ColorType.kRGBA_8888_ColorType,
                               alphaType=skia.AlphaType.kUnpremul_AlphaType)
    s = _surface(img)
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(),
                            skia.Paint(ImageFilter=skia.ImageFilters.Blur(sigma, sigma, skia.TileMode.kClamp)))
    b = s.makeImageSnapshot().toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                                      alphaType=skia.AlphaType.kUnpremul_AlphaType).astype(np.float32)
    return (b[..., 0] * 256 + b[..., 1]) / 65535.0


def _morph(m: np.ndarray, radius: float, dilate: bool) -> np.ndarray:
    """Dilate/erode a single-channel float mask by radius pixels."""
    if radius < 0.5:
        return m
    rgba = np.zeros(m.shape + (4,), np.uint8)
    rgba[..., 3] = (np.clip(m, 0, 1) * 255).astype(np.uint8)
    img = skia.Image.fromarray(np.ascontiguousarray(rgba), colorType=skia.ColorType.kRGBA_8888_ColorType,
                               alphaType=skia.AlphaType.kPremul_AlphaType)
    r = int(round(radius))
    f = skia.ImageFilters.Dilate(r, r) if dilate else skia.ImageFilters.Erode(r, r)
    s = _surface(img)
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=f))
    return _premul(s.makeImageSnapshot())[..., 3]


def _grid_xy(ctx, h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Layer coordinates of every pixel centre."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    b = ctx.bounds
    return b.left() + (xx + 0.5) / ctx.res, b.top() + (yy + 0.5) / ctx.res


def _layer_map(ctx, index, sizes: int, img: skia.Image) -> np.ndarray | None:
    """Unpremultiplied RGBA of a layer parameter; `index` 0 means the layer itself (before the effect)."""
    try:
        idx = int(index or 0)
    except (TypeError, ValueError):
        idx = 0
    m = ctx.layer_image(idx, sizes) if idx > 0 else (ctx.original if ctx.original is not None else img)
    if m is None:
        return None
    if m.width() != img.width() or m.height() != img.height():
        s = _surface(img)
        s.getCanvas().drawImage(m, 0, 0)
        m = s.makeImageSnapshot()
    return _arr(m)


def _bump_light(ctx, p: dict, rgb: np.ndarray, height: np.ndarray, strength: float, keys: dict) -> np.ndarray:
    """Shade colours with a bump map: AE-style light (keys name the parameter indices of the effect)."""
    gy, gx = np.gradient(height)
    k = strength * 40.0
    n = np.stack([-gx * k, -gy * k, np.ones_like(height)], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    inten = float(p.get(keys["intensity"], 100)) / 100.0
    lcol = np.array(list(p.get(keys["color"], [1, 1, 1, 1]))[:3], np.float32)
    elev = math.radians(float(p.get(keys["height"], 65)))
    if int(p.get(keys["type"], 1) or 1) == 2:  # point light
        lp = p.get(keys["position"], [0, 0])
        lp = list(lp) if isinstance(lp, (list, tuple)) else [0, 0]
        if max(abs(lp[0]), abs(lp[1])) > 20000:  # stored at 100x in some projects
            lp = [lp[0] / 100, lp[1] / 100]
        xx, yy = _grid_xy(ctx, *height.shape)
        dx, dy = lp[0] - xx, lp[1] - yy
        dl = np.maximum(np.hypot(dx, dy), 1e-3)
        c = math.cos(elev)
        light = np.stack([dx / dl * c, dy / dl * c, np.full_like(dx, math.sin(elev))], axis=-1)
    else:
        a = math.radians(float(p.get(keys["direction"], -45)))
        c = math.cos(elev)
        light = np.broadcast_to(np.array([math.sin(a) * c, -math.cos(a) * c, math.sin(elev)], np.float32), n.shape)
    ndl = np.clip(np.sum(n * light, axis=-1), 0, 1)[..., None]
    refl_z = 2 * ndl[..., 0] * n[..., 2] - light[..., 2]
    rough = max(0.001, float(p.get(keys["roughness"], 0.05)))
    spec = np.clip(refl_z, 0, 1) ** (1.0 / rough)
    amb = float(p.get(keys["ambient"], 50)) / 100.0
    dif = float(p.get(keys["diffuse"], 50)) / 100.0
    spc = float(p.get(keys["specular"], 50)) / 100.0
    metal = float(p.get(keys["metal"], 100)) / 100.0
    spec_col = (rgb * metal + lcol * (1 - metal)) * lcol
    out = rgb * (amb + dif * ndl * lcol * inten) + spec_col * (spc * spec * inten)[..., None]
    return np.clip(out, 0, 1)


# --------------------------------------------------------------------------- layer maps

@effect("ADBE Displacement Map", pad=lambda p: (max(abs(float(p.get("0003", 0))), abs(float(p.get("0005", 0)))) + 2)
        if int(p.get("0008", 0) or 0) else 0)
def displacement_map(ctx, p, img):
    # 0001 map layer, 0002/0004 channel for horizontal/vertical, 0003/0005 max displacement,
    # 0006 behaviour (1 centre, 2 stretch, 3 tile), 0007 wrap pixels around
    mh, mv = float(p.get("0003", 0)), float(p.get("0005", 0))
    if abs(mh) < 0.01 and abs(mv) < 0.01:
        return img
    behaviour = int(p.get("0006", 1) or 1)
    m = _layer_map(ctx, p.get("0001"), 2 if behaviour == 2 else 1, img)
    if m is None:
        return img
    if behaviour == 3:
        m = _tile_map(ctx, p.get("0001"), m)
    dx = (_channel(m, int(p.get("0002", 1) or 1), 0.5) - 0.5) * 2 * mh * ctx.res
    dy = (_channel(m, int(p.get("0004", 2) or 2), 0.5) - 0.5) * 2 * mv * ctx.res
    h, w = img.height(), img.width()
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx, sy = xx + dx, yy + dy
    if int(p.get("0007", 0) or 0):
        sx, sy = sx % w, sy % h
    return _from_premul(_sample(_premul(img), sx, sy))


def _tile_map(ctx, index, m: np.ndarray) -> np.ndarray:
    """Repeat the (centred) map layer across the whole image."""
    try:
        comp = ctx.renderer.ev._comp_of_layer.get(id(ctx.layer))
        other = comp.layers[int(index) - 1]
        theirs = ctx.renderer.layer_content_bounds(other, ctx.t)
    except Exception:
        return m
    mine = ctx.layer_rect()
    tw, th = theirs.width() * ctx.res, theirs.height() * ctx.res
    if tw < 1 or th < 1:
        return m
    # the centred map covers this pixel rectangle
    x0 = (mine.centerX() - theirs.width() / 2 - ctx.bounds.left()) * ctx.res
    y0 = (mine.centerY() - theirs.height() / 2 - ctx.bounds.top()) * ctx.res
    h, w = m.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = np.clip(x0 + (xx - x0) % tw, 0, w - 1).astype(np.int32)
    sy = np.clip(y0 + (yy - y0) % th, 0, h - 1).astype(np.int32)
    return m[sy, sx]


@effect("ADBE Set Matte3")
def set_matte(ctx, p, img):
    # 0001 matte layer, 0002 channel, 0003 invert, 0004 sizes differ (1 centre, 2 stretch),
    # 0005 composite matte with original, 0006 premultiply matte layer
    idx = int(p.get("0001", 0) or 0)
    if idx <= 0:
        return img
    m = _layer_map(ctx, idx, int(p.get("0004", 1) or 1), img)
    if m is None:
        return img
    ch = int(p.get("0002", 4) or 4)
    if int(p.get("0006", 1) or 1) or ch == 4:
        matte = _channel(m, ch, 0.0)  # colour channels premultiplied with black
    else:
        matte = _channel(m, ch, 0.0) / np.maximum(m[..., 3], 1e-6) * (m[..., 3] > 0)
    matte = np.clip(matte, 0, 1)
    if int(p.get("0003", 0) or 0):
        matte = 1 - matte
    a = _arr(img)
    a[..., 3] = a[..., 3] * matte if int(p.get("0005", 1) or 1) else matte
    return _img(a)


# CC effects: the property menu (Red, Green, Blue, Alpha, Luminance, Lightness)
_CC_PROPERTY = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 7}

_GLASS_LIGHT = {"intensity": "0009", "color": "0010", "type": "0011", "height": "0012", "position": "0013",
                "direction": "0014", "ambient": "0017", "diffuse": "0018", "specular": "0019",
                "roughness": "0020", "metal": "0021"}
_BLOB_LIGHT = {"intensity": "0008", "color": "0009", "type": "0010", "height": "0011", "position": "0012",
               "direction": "0013", "ambient": "0016", "diffuse": "0017", "specular": "0018",
               "roughness": "0019", "metal": "0020"}


def _bump(ctx, p, img, layer_key: str, prop_key: str, soft_key: str) -> np.ndarray | None:
    m = _layer_map(ctx, p.get(layer_key), 1, img)
    if m is None:
        return None
    hmap = _channel(m, _CC_PROPERTY.get(int(float(p.get(prop_key, 4) or 4)), 4), 0.0)
    return _gauss(hmap, float(p.get(soft_key, 10)) * ctx.res / 2)


@effect("CC Glass")
def cc_glass(ctx, p, img):
    # 0002 bump map layer, 0003 property, 0004 softness, 0005 height, 0006 displacement, 0009… light
    hmap = _bump(ctx, p, img, "0002", "0003", "0004")
    if hmap is None:
        return img
    height = float(p.get("0005", 50)) / 100.0
    disp = float(p.get("0006", 0))
    pre = _premul(img)
    if abs(disp) > 0.01:
        gy, gx = np.gradient(hmap)
        h, w = hmap.shape
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        k = disp * ctx.res * 2
        pre = _sample(pre, xx + gx * k, yy + gy * k)
    if abs(height) < 1e-3:
        return _from_premul(pre)
    al = pre[..., 3:4]
    rgb = np.where(al > 0, pre[..., :3] / np.maximum(al, 1e-6), 0)
    rgb = _bump_light(ctx, p, rgb, hmap, height, _GLASS_LIGHT)
    return _from_premul(np.concatenate([rgb * al, al], axis=-1))


@effect("CC Blobbylize")
def cc_blobbylize(ctx, p, img):
    # 0002 blob layer, 0003 property, 0004 softness, 0005 cut away, 0008… light
    hmap = _bump(ctx, p, img, "0002", "0003", "0004")
    if hmap is None:
        return img
    cut = float(p.get("0005", 0)) / 100.0
    a = _arr(img)
    if cut > 0:
        a[..., 3] *= np.clip((hmap - cut) / 0.05, 0, 1)
    a[..., :3] = _bump_light(ctx, p, a[..., :3], hmap, 1.0, _BLOB_LIGHT)
    return _img(a)


@effect("CC Vector Blur", pad=lambda p: abs(float(p.get("0002", 10))) + 2)
def cc_vector_blur(ctx, p, img):
    # 0001 type (1 natural, 2 constant length, 3 perpendicular, 4/5 direction centre/fading), 0002 amount,
    # 0003 angle offset, 0005 vector map layer, 0006 property, 0007 map softness
    amount = float(p.get("0002", 10)) * ctx.res
    if abs(amount) < 0.3:
        return img
    m = _layer_map(ctx, p.get("0005"), 1, img)
    if m is None:
        return img
    hmap = _gauss(_channel(m, _CC_PROPERTY.get(int(float(p.get("0006", 5) or 5)), 5), 0.0),
                  float(p.get("0007", 15)) * ctx.res / 2)
    gy, gx = np.gradient(hmap)
    mag = np.hypot(gx, gy)
    ang = np.arctan2(gy, gx) + math.radians(float(p.get("0003", 0)))
    kind = int(float(p.get("0001", 1) or 1))
    if kind == 3:
        ang = ang + math.pi / 2
    length = amount * (np.ones_like(mag) if kind == 2 else mag / max(float(mag.max()), 1e-6))
    ux, uy = np.cos(ang) * length, np.sin(ang) * length
    pre = _premul(img)
    h, w = hmap.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    n = int(min(24, max(3, abs(amount))))
    acc = np.zeros_like(pre)
    for i in range(n):
        f = i / (n - 1) - 0.5
        acc += _sample(pre, xx + ux * f, yy + uy * f)
    return _from_premul(acc / n)


# --------------------------------------------------------------------------- light

@effect("CC Light Sweep")
def cc_light_sweep(ctx, p, img):
    # 0001 center, 0002 direction, 0003 shape, 0004 width, 0005 sweep intensity, 0006 edge intensity,
    # 0007 edge thickness, 0008 light color, 0009 light reception (1 add, 2 composite, 3 cutout)
    sweep = float(p.get("0005", 50)) / 100.0
    edge_i = float(p.get("0006", 100)) / 100.0
    if sweep <= 0 and edge_i <= 0:
        return img
    c = p.get("0001", [0, 0])
    c = list(c) if isinstance(c, (list, tuple)) else [0, 0]
    a = math.radians(float(p.get("0002", -30)))
    width = max(1e-3, float(p.get("0004", 50)))
    arr = _arr(img)
    h, w = arr.shape[:2]
    lx, ly = _grid_xy(ctx, h, w)
    d = np.abs((lx - c[0]) * math.cos(a) + (ly - c[1]) * math.sin(a)) / width
    shape = int(p.get("0003", 2) or 2)
    if shape == 1:
        prof = np.clip(1 - d, 0, 1)
    elif shape == 3:
        prof = np.clip((1 - d) * 4, 0, 1)
    else:
        prof = np.where(d < 1, 0.5 * (1 + np.cos(np.pi * np.clip(d, 0, 1))), 0)
    al = arr[..., 3]
    light = prof * sweep
    if edge_i > 0:
        th = float(p.get("0007", 2)) * ctx.res
        edge = np.clip(al - _morph(al, th, False), 0, 1)
        light = light + prof * edge * edge_i
    col = np.array(list(p.get("0008", [1, 1, 1, 1]))[:3], np.float32)
    reception = int(p.get("0009", 1) or 1)
    rgb = arr[..., :3]
    if reception == 3:  # cutout: only the light, inside the layer
        arr[..., :3] = col
        arr[..., 3] = al * np.clip(light, 0, 1)
    elif reception == 2:
        k = np.clip(light, 0, 1)[..., None]
        arr[..., :3] = rgb * (1 - k) + col * k
    else:
        arr[..., :3] = rgb + col * light[..., None]
    return _img(arr)


# --------------------------------------------------------------------------- generators

def _over(ctx, img: skia.Image, rgb: np.ndarray, alpha: np.ndarray, opacity: float, mode: int) -> skia.Image:
    """Combine generated colour/alpha with the layer. Mode 1 = None (generated only); otherwise the
    generated colour is blended into the layer (2 normal, 3 add, 4 multiply, 5 screen, 6 overlay,
    11 darken, 12 lighten, 13 difference) and the layer keeps its own alpha."""
    if mode == 1:
        gen = np.concatenate([rgb * alpha[..., None], alpha[..., None]], axis=-1) * opacity
        return _from_premul(gen)
    a = _arr(img)
    base = a[..., :3]
    blended = {
        3: lambda: np.clip(base + rgb, 0, 1),
        4: lambda: base * rgb,
        5: lambda: 1 - (1 - base) * (1 - rgb),
        6: lambda: np.where(base < 0.5, 2 * base * rgb, 1 - 2 * (1 - base) * (1 - rgb)),
        11: lambda: np.minimum(base, rgb),
        12: lambda: np.maximum(base, rgb),
        13: lambda: np.abs(base - rgb),
    }.get(mode, lambda: rgb)()
    k = (alpha * opacity)[..., None]
    a[..., :3] = base * (1 - k) + blended * k
    return _img(a)


def _inside_layer(ctx, lx: np.ndarray, ly: np.ndarray) -> np.ndarray:
    r = ctx.layer_rect()
    return ((lx >= r.left()) & (lx < r.right()) & (ly >= r.top()) & (ly < r.bottom())).astype(np.float32)


@effect("ADBE Grid")
def grid(ctx, p, img):
    # 0001 anchor, 0002 size from (1 corner, 2 width, 3 width & height), 0003 corner, 0004 width, 0005 height,
    # 0006 border, 0008/0009 feather, 0011 invert, 0012 color, 0013 opacity, 0014 blending mode
    anchor = list(p.get("0001", [0, 0]))
    mode = int(p.get("0002", 1) or 1)
    if mode == 1:
        corner = list(p.get("0003", [anchor[0] + 20, anchor[1] + 20]))
        cw, ch = abs(corner[0] - anchor[0]), abs(corner[1] - anchor[1])
    elif mode == 2:
        cw = ch = float(p.get("0004", 20))
    else:
        cw, ch = float(p.get("0004", 20)), float(p.get("0005", 20))
    cw, ch = max(cw, 1e-3), max(ch, 1e-3)
    border = max(0.0, float(p.get("0006", 1)))
    fw, fh = float(p.get("0008", 0) or 0), float(p.get("0009", 0) or 0)
    h, w = img.height(), img.width()
    lx, ly = _grid_xy(ctx, h, w)
    dx = np.abs(((lx - anchor[0]) + cw / 2) % cw - cw / 2)  # distance to the nearest vertical line
    dy = np.abs(((ly - anchor[1]) + ch / 2) % ch - ch / 2)
    px = 0.5 / ctx.res  # antialiasing
    lv = np.clip((border / 2 - dx) / max(px + fw / 2, 1e-3) + 0.5, 0, 1)
    lh = np.clip((border / 2 - dy) / max(px + fh / 2, 1e-3) + 0.5, 0, 1)
    g = np.maximum(lv, lh)
    if int(p.get("0011", 0) or 0):
        g = 1 - g
    g *= _inside_layer(ctx, lx, ly)
    col = np.broadcast_to(np.array(list(p.get("0012", [1, 1, 1, 1]))[:3], np.float32), g.shape + (3,))
    return _over(ctx, img, col, g, float(p.get("0013", 100)) / 100.0, int(p.get("0014", 1) or 1))


@effect("APC Vegas", pad=lambda p: float(p.get("0020", 2)) + 2)
def vegas(ctx, p, img):
    # image contours: 0002 input layer (0 = this layer), 0004 invert, 0006 sizes differ, 0010 channel
    # (1 intensity, 2 red, 3 green, 4 blue, 5 alpha), 0012 threshold, 0014 pre-blur;
    # rendering: 0008 blend mode, 0018 color, 0020 width, 0022 hardness, 0036/0038/0042 opacities
    m = _layer_map(ctx, p.get("0002"), int(p.get("0006", 1) or 1), img)
    if m is None:
        return img
    ch = int(p.get("0010", 1) or 1)
    v = {1: _channel(m, 5), 2: _channel(m, 1), 3: _channel(m, 2), 4: _channel(m, 3), 5: m[..., 3]}.get(ch, m[..., 3])
    v = _gauss(v, float(p.get("0014", 0)) * ctx.res / 2)
    inside = (v > float(p.get("0012", 127)) / 255.0).astype(np.float32)
    if int(p.get("0004", 0) or 0):
        inside = 1 - inside
    half = float(p.get("0020", 2)) * ctx.res / 2
    stroke = np.clip(_morph(inside, half, True) - _morph(inside, half, False), 0, 1)
    if half < 0.5:  # thinner than a pixel: one-pixel outline, faded
        stroke = np.clip(_morph(inside, 1, True) - inside, 0, 1) * (half * 2)
    hard = float(p.get("0022", 1))
    if hard < 1:
        stroke = _gauss(stroke, (1 - hard) * half)
    seg, length = float(p.get("0028", 1) or 1), float(p.get("0024", 1) or 1)
    op = float(p.get("0036", 1)) * min(1.0, seg * length)  # partial strokes are not traced: fade instead
    col = np.broadcast_to(np.array(list(p.get("0018", [1, 1, 1, 1]))[:3], np.float32), stroke.shape + (3,))
    gen = np.concatenate([col * stroke[..., None], stroke[..., None]], axis=-1) * op
    mode = int(float(p.get("0008", 2) or 2))
    if mode == 1:
        return _from_premul(gen)
    pre = _premul(img)
    if mode == 3:
        return _from_premul(pre + gen * (1 - pre[..., 3:4]))
    return _from_premul(gen + pre * (1 - gen[..., 3:4]))


@effect("ADBE AIF Perlin Noise 3D")
def turbulent_noise(ctx, p, img):
    # 0003 invert, 0004 contrast, 0005 brightness, 0006 overflow, 0008 rotation, 0009 uniform scaling,
    # 0010 scale, 0011/0012 scale width/height, 0013 offset, 0015 complexity, 0017 sub influence,
    # 0018 sub scaling, 0020 evolution, 0023 random seed, 0025 opacity, 0026 blending mode
    h, w = img.height(), img.width()
    scale = max(1.0, float(p.get("0010", 100)))
    if int(p.get("0009", 1) or 1):
        sw = sh = scale
    else:
        sw = max(1.0, scale * float(p.get("0011", 100)) / 100)
        sh = max(1.0, scale * float(p.get("0012", 100)) / 100)
    off = p.get("0013", [0, 0])
    off = list(off) if isinstance(off, (list, tuple)) else [0, 0]
    if max(abs(off[0]), abs(off[1])) > 20000:  # stored at 100x in some projects
        off = [off[0] / 100, off[1] / 100]
    rot = math.radians(float(p.get("0008", 0)))
    complexity = max(1.0, float(p.get("0015", 6)))
    influence = float(p.get("0017", 70)) / 100.0
    sub = max(0.05, float(p.get("0018", 56)) / 100.0)
    evo = float(p.get("0020", 0)) / 360.0
    seed = int(p.get("0023", 0) or 0)

    # coarse grid at the finest octave's scale, then upsampled
    finest = min(sw, sh) * sub ** (math.ceil(complexity) - 1)
    step = max(1, int(finest * ctx.res / 3))
    gw, gh = w // step + 2, h // step + 2
    gy, gx = np.mgrid[0:gh, 0:gw].astype(np.float32)
    lx = ctx.bounds.left() + gx * step / ctx.res - off[0]
    ly = ctx.bounds.top() + gy * step / ctx.res - off[1]
    cr, sr = math.cos(-rot), math.sin(-rot)
    u, v = (lx * cr - ly * sr) / sw, (lx * sr + ly * cr) / sh
    total, amp, f, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(int(math.ceil(complexity))):
        wgt = min(1.0, complexity - o)
        total = total + _vnoise3(u * f + o * 17.3, v * f + o * 31.7, evo * (1 + o * 0.3), seed + o * 101) * amp * wgt
        norm += amp * wgt
        amp *= influence
        f /= sub
    n = total / max(norm, 1e-6)
    val = (n * 0.5) * float(p.get("0004", 100)) / 100.0 + 0.5 + float(p.get("0005", 0)) / 100.0
    if int(p.get("0003", 0) or 0):
        val = 1 - val
    val = np.clip(val, 0, 1)  # overflow: clip

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    px, py = xx / step, yy / step
    i0, j0 = np.floor(px).astype(np.int32), np.floor(py).astype(np.int32)
    fx, fy = px - i0, py - j0
    g = ((val[j0, i0] * (1 - fx) + val[j0, i0 + 1] * fx) * (1 - fy)
         + (val[j0 + 1, i0] * (1 - fx) + val[j0 + 1, i0 + 1] * fx) * fy)
    lxx, lyy = _grid_xy(ctx, h, w)
    alpha = _inside_layer(ctx, lxx, lyy)
    rgb = np.repeat(g[..., None], 3, axis=-1)
    return _over(ctx, img, rgb, alpha, float(p.get("0025", 100)) / 100.0, int(p.get("0026", 1) or 1))


# Fractal Noise has the same controls as Turbulent Noise at other indices
_FRACTAL_TO_TURBULENT = {"0003": "0003", "0004": "0004", "0005": "0005", "0006": "0006", "0008": "0008",
                         "0009": "0009", "0010": "0010", "0011": "0011", "0012": "0012", "0013": "0013",
                         "0015": "0015", "0017": "0017", "0018": "0018", "0023": "0020", "0028": "0024",
                         "0027": "0023", "0029": "0025", "0030": "0026"}


@effect("ADBE Fractal Noise")
def fractal_noise(ctx, p, img):
    q = {dst: p[src] for src, dst in _FRACTAL_TO_TURBULENT.items() if src in p}
    q.setdefault("0026", 1)  # Fractal Noise defaults to blending mode None
    return turbulent_noise(ctx, q, img)
