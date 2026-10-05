"""Layer effects (applied in layer space) and footage rasterization."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import skia

from .paint import color4f

CONTROL_EFFECTS = {
    "ADBE Checkbox Control",
    "ADBE Slider Control",
    "ADBE Point Control",
    "ADBE Point3D Control",
    "ADBE Color Control",
    "ADBE Angle Control",
    "ADBE Layer Control",
    "ADBE Dropdown Control",
}


@dataclass
class EffectContext:
    renderer: Any
    layer: Any
    t: float
    bounds: skia.Rect  # layer-space rect covered by the image
    res: float  # pixels per layer unit
    original: Any = None  # layer image before any effect (CC Composite)

    def to_layer(self, px: float, py: float) -> tuple[float, float]:
        """Image pixel -> layer coordinates."""
        return self.bounds.left() + px / self.res, self.bounds.top() + py / self.res

    def layer_rect(self) -> skia.Rect:
        """The layer's own rectangle (AE layer size) in layer coordinates."""
        lb = effect_rect(self.renderer, self.layer, self.t)
        if lb.width() <= 0 or lb.height() <= 0:
            return skia.Rect.MakeLTRB(self.bounds.left(), self.bounds.top(), self.bounds.right(), self.bounds.bottom())
        return lb

    def _blank(self) -> skia.Surface:
        s = skia.Surface.MakeRasterN32Premul(max(1, int(math.ceil(self.bounds.width() * self.res))),
                                             max(1, int(math.ceil(self.bounds.height() * self.res))))
        s.getCanvas().clear(skia.ColorTRANSPARENT)
        return s

    def _to_pixels(self, c: skia.Canvas) -> None:
        c.scale(self.res, self.res)
        c.translate(-self.bounds.left(), -self.bounds.top())

    def layer_image(self, index: Any, sizes_differ: int = 1) -> skia.Image | None:
        """A layer parameter (1-based index in this comp, 0 = none) as an image on this effect's pixel grid.

        Like AE, the other layer's transform is ignored: its content (with masks and effects) is
        centred on this layer (sizes_differ 1) or stretched to it (2). The layer itself gives the
        image before this effect's chain.
        """
        try:
            idx = int(index or 0)
        except (TypeError, ValueError):
            return None
        if idx <= 0:
            return None
        r = self.renderer
        comp = r.ev._comp_of_layer.get(id(self.layer))
        if comp is None or idx > len(comp.layers):
            return None
        other = comp.layers[idx - 1]
        if other is self.layer:
            return self.original
        stack = r.__dict__.setdefault("_layer_param_stack", [])
        if id(other) in stack:
            return None
        stack.append(id(other))
        try:
            img, origin, res = r.render_layer_offscreen(other, self.t, self.res, skia.Matrix())
        finally:
            stack.pop()
        if img is None:
            return None
        mine, theirs = self.layer_rect(), effect_rect(r, other, self.t)
        s = self._blank()
        c = s.getCanvas()
        self._to_pixels(c)
        if int(sizes_differ or 1) == 2 and theirs.width() > 0 and theirs.height() > 0:
            c.translate(mine.left(), mine.top())
            c.scale(mine.width() / theirs.width(), mine.height() / theirs.height())
            c.translate(-theirs.left(), -theirs.top())
        else:
            c.translate(mine.centerX() - theirs.centerX(), mine.centerY() - theirs.centerY())
        c.drawImageRect(img, skia.Rect.MakeXYWH(origin[0], origin[1], img.width() / res, img.height() / res),
                        skia.SamplingOptions(skia.FilterMode.kLinear), skia.Paint(AntiAlias=True))
        return s.makeImageSnapshot()

    def content_at(self, t: float) -> skia.Image:
        """The layer's content (no masks or effects) at comp time t on this effect's pixel grid."""
        s = self._blank()
        c = s.getCanvas()
        self._to_pixels(c)
        self.renderer.draw_content(c, self.layer, t, self.res)
        return s.makeImageSnapshot()


def _is_vector_layer(layer: Any) -> bool:
    return type(layer).__name__ in ("ShapeLayer", "TextLayer")


def effect_rect(r: Any, layer: Any, t: float) -> skia.Rect:
    """The layer rectangle effects work in. Shape and text layers count as comp-sized, centred on
    the layer origin (that is where AE puts their effect points and layer parameters)."""
    if _is_vector_layer(layer):
        comp = r.ev._comp_of_layer.get(id(layer), r.mogrt.main_comp)
        return skia.Rect.MakeXYWH(-comp.width / 2, -comp.height / 2, comp.width, comp.height)
    if getattr(layer, "collapse_transformation", False) and type(getattr(layer, "source", None)).__name__ == "CompItem":
        cb = r.collapsed_bounds(layer, t)
        return cb if not cb.isEmpty() else r.layer_content_bounds(layer, t)
    return r.layer_content_bounds(layer, t)


_SPATIAL = {"TwoD_SPATIAL", "ThreeD_SPATIAL"}


def _params(r: Any, effect: Any, t: float) -> dict[str, Any]:
    """Effect parameters keyed by index suffix ("0001") and by display name (first one wins).

    Effect points are returned in layer coordinates."""
    out = {}
    shift = None
    for p in effect:
        if hasattr(p, "keyframes"):
            key = p.match_name.split("-")[-1]
            v = r.ev.value(p, t)
            if (isinstance(v, list) and 2 <= len(v) <= 3 and all(isinstance(x, (int, float)) for x in v)
                    and max(abs(x) for x in v[:2]) > 20000):
                # untouched default points (layer centre) are stored at 100x
                v = [x / 100 for x in v]
            if isinstance(v, list) and getattr(getattr(p, "property_value_type", None), "name", "") in _SPATIAL:
                if shift is None:
                    from ..evaluator import owning_layer

                    lay = owning_layer(effect)
                    rect = effect_rect(r, lay, t) if _is_vector_layer(lay) else None
                    shift = (rect.left(), rect.top()) if rect is not None else (0.0, 0.0)
                if shift != (0.0, 0.0):
                    v = [v[0] + shift[0], v[1] + shift[1]] + list(v[2:])
            out[key] = v
            if p.name and p.name not in out:
                out[p.name] = out[key]
    return out


def is_control(effect: Any) -> bool:
    """Expression controls and pseudo effects (custom control presets) never render anything."""
    mn = effect.match_name
    return mn in CONTROL_EFFECTS or mn.startswith("Pseudo/")


def padding(r: Any, effect: Any, t: float) -> float:
    from .effects_extra import REGISTRY

    mn = effect.match_name
    p = _params(r, effect, t)
    if mn in REGISTRY:
        pad_fn = REGISTRY[mn][1]
        if pad_fn is None:
            return 0.0
        try:
            if pad_fn.__code__.co_argcount >= 2:  # pad functions may also take the layer's content rect
                from ..evaluator import owning_layer

                return float(pad_fn(p, r.layer_content_bounds(owning_layer(effect), t)))
            return float(pad_fn(p))
        except Exception:
            return 0.0
    if mn == "ADBE Drop Shadow":
        return float(p.get("0004", 5)) + float(p.get("0005", 0)) * 1.5 + 2
    if mn in ("ADBE Gaussian Blur 2", "ADBE Gaussian Blur"):
        return float(p.get("0001", 0)) * 1.5 + 2
    if mn == "ADBE Camera Lens Blur":
        return float(p.get("0001", 0)) * 1.2 + 2
    if mn == "ADBE Glo2":
        return 50.0
    return 0.0


_warned: set[str] = set()


def _surface_like(img: skia.Image) -> skia.Surface:
    s = skia.Surface.MakeRasterN32Premul(img.width(), img.height())
    s.getCanvas().clear(skia.ColorTRANSPARENT)
    return s


def _blur(img: skia.Image, sigma_px: float) -> skia.Image:
    if sigma_px <= 0.01:
        return img
    s = _surface_like(img)
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=skia.ImageFilters.Blur(sigma_px, sigma_px, skia.TileMode.kDecal)))
    return s.makeImageSnapshot()


def apply(ctx: EffectContext, effect: Any, img: skia.Image) -> skia.Image:
    from .effects_extra import REGISTRY

    mn = effect.match_name
    r = ctx.renderer
    p = _params(r, effect, ctx.t)
    res = ctx.res
    if mn in REGISTRY:
        return REGISTRY[mn][0](ctx, p, img)
    if mn == "ADBE Drop Shadow":
        col = p.get("0001", [0, 0, 0, 1])
        opacity = float(p.get("0002", 127.5)) / 255.0
        direction = math.radians(float(p.get("0003", 135.0)))
        dist = float(p.get("0004", 5.0)) * res
        soft = float(p.get("0005", 0.0)) * res
        only = bool(p.get("0006", 0))
        # AE: direction 0 = up, clockwise
        dx, dy = math.sin(direction) * dist, -math.cos(direction) * dist
        sigma = soft / 2.0
        cf = color4f(col[:3], opacity)
        if only:
            f = skia.ImageFilters.DropShadowOnly(dx, dy, sigma, sigma, cf.toColor())
        else:
            f = skia.ImageFilters.DropShadow(dx, dy, sigma, sigma, cf.toColor())
        s = _surface_like(img)
        s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=f))
        return s.makeImageSnapshot()
    if mn in ("ADBE Gaussian Blur 2", "ADBE Gaussian Blur"):
        amount = float(p.get("0001", 0.0)) * res
        return _blur(img, amount / 2.0)
    if mn == "ADBE Camera Lens Blur":
        radius = float(p.get("0001", 0.0)) * res
        return _blur(img, radius / 2.0)
    if mn == "ADBE Ramp":
        b = ctx.bounds
        start = p.get("0001", [0, 0])
        c1 = p.get("0002", [0, 0, 0, 1])
        end = p.get("0003", [0, 100])
        c2 = p.get("0004", [1, 1, 1, 1])
        shape = int(p.get("0005", 1))
        blend_orig = float(p.get("0007", 0.0)) / 100.0
        to_px = lambda pt: skia.Point((pt[0] - b.left()) * res, (pt[1] - b.top()) * res)
        cols = [color4f(c1[:3]).toColor(), color4f(c2[:3]).toColor()]
        if shape == 2:
            rad = math.hypot(end[0] - start[0], end[1] - start[1]) * res
            shader = skia.GradientShader.MakeRadial(to_px(start), max(rad, 0.01), cols)
        else:
            shader = skia.GradientShader.MakeLinear([to_px(start), to_px(end)], cols)
        s = _surface_like(img)
        c = s.getCanvas()
        # Ramp fills the layer (respecting its alpha), blended with the original
        c.drawImage(img, 0, 0)
        paint = skia.Paint(Shader=shader, Alphaf=1.0 - blend_orig, BlendMode=skia.BlendMode.kSrcATop)
        c.drawRect(skia.Rect.MakeWH(img.width(), img.height()), paint)
        return s.makeImageSnapshot()
    if mn == "ADBE Linear Wipe":
        c_ = min(max(float(p.get("0001", 0.0)) / 100.0, 0.0), 1.0)
        if c_ <= 0:
            return img
        ang = math.radians(float(p.get("0002", 90.0)))
        feather = float(p.get("0003", 0.0)) * res
        d = (math.sin(ang), -math.cos(ang))
        w, h = img.width(), img.height()
        proj = [x * d[0] + y * d[1] for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
        lo, hi = min(proj), max(proj)
        span = hi - lo + feather
        edge = lo - feather / 2 + c_ * span
        f = max(feather, 0.75)
        p0 = skia.Point(d[0] * (edge - f / 2), d[1] * (edge - f / 2))
        p1 = skia.Point(d[0] * (edge + f / 2), d[1] * (edge + f / 2))
        shader = skia.GradientShader.MakeLinear([p0, p1], [skia.ColorTRANSPARENT, skia.ColorBLACK])
        s_ = _surface_like(img)
        cv = s_.getCanvas()
        cv.drawImage(img, 0, 0)
        cv.drawRect(skia.Rect.MakeWH(w, h), skia.Paint(Shader=shader, BlendMode=skia.BlendMode.kDstIn))
        return s_.makeImageSnapshot()
    if mn == "ADBE Fill":
        col = p.get("Color", [1, 0, 0, 1])
        opacity = float(p.get("Opacity", 1.0))
        s = _surface_like(img)
        c = s.getCanvas()
        c.drawImage(img, 0, 0)
        c.drawRect(skia.Rect.MakeWH(img.width(), img.height()),
                   skia.Paint(Color4f=color4f(col[:3]), Alphaf=opacity, BlendMode=skia.BlendMode.kSrcATop))
        return s.makeImageSnapshot()
    if mn == "ADBE Tint":
        black = p.get("0001", [0, 0, 0, 1])
        white = p.get("0002", [1, 1, 1, 1])
        amount = float(p.get("0003", 100.0)) / 100.0
        arr = img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType, alphaType=skia.AlphaType.kUnpremul_AlphaType).astype(np.float32) / 255
        lum = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
        for ch in range(3):
            tinted = black[ch] + (white[ch] - black[ch]) * lum
            arr[..., ch] = arr[..., ch] + (tinted - arr[..., ch]) * amount
        out = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
        return skia.Image.fromarray(out, colorType=skia.ColorType.kRGBA_8888_ColorType, alphaType=skia.AlphaType.kUnpremul_AlphaType)
    if mn not in _warned:
        _warned.add(mn)
        import sys

        print(f"WARNUNG: Effekt '{effect.name}' ({mn}) wird noch nicht unterstützt – übersprungen", file=sys.stderr)
    if mn in GENERATORS:
        # generators replace the layer content; showing the raw (usually white) solid instead
        # would be far more wrong than showing nothing
        return _surface_like(img).makeImageSnapshot()
    return img


GENERATORS = {
    "CC Glue Gun", "CC Particle World", "CC Particle Systems II", "CC Star Burst", "CC Light Burst 2.5",
    "CC Mr. Mercury", "CC Pixel Polly", "CC Rainfall", "CC Snowfall", "CC Hair", "CC Ball Action",
    "ADBE Fractal Noise", "ADBE AIF Perlin Noise 3D", "ADBE Grid", "ADBE Cell Pattern", "ADBE Checkerboard",
    "ADBE Lightning", "ADBE Lightning 2", "ADBE Write-on", "ADBE Scribble Fill", "ADBE Audio Spectrum",
    "ADBE Audio Waveform", "ADBE Vegas", "APC Vegas", "ADBE Radio Waves", "ADBE Fill Paint",
}


def rasterize_pdf(path: Path, width: float, height: float, oversample: float = 4) -> skia.Image | None:
    """Rasterize an AI/PDF page at the footage size (times oversample)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    page = pdf[0]
    pw, ph = page.get_size()
    scale = (width * oversample) / pw if pw else oversample
    bitmap = page.render(scale=scale, fill_color=(0, 0, 0, 0), may_draw_forms=True)
    pil = bitmap.to_pil().convert("RGBA")
    arr = np.asarray(pil)
    return skia.Image.fromarray(np.ascontiguousarray(arr), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kUnpremul_AlphaType)


# --------------------------------------------------------------------------- layer styles


def _style_params(r: Any, group: Any, t: float) -> dict[str, Any]:
    out = {}
    for p in group:
        if hasattr(p, "keyframes"):
            out[p.match_name.split("/")[-1]] = r.ev.value(p, t)
    return out


def active_styles(layer: Any) -> list[Any]:
    try:
        styles = layer.property("ADBE Layer Styles")
    except Exception:
        return []
    if styles is None or not getattr(styles, "enabled", False):
        return []
    return [g for g in styles if getattr(g, "enabled", False) and "/" in g.match_name]


def style_padding(r: Any, layer: Any, t: float) -> float:
    pad = 0.0
    for g in active_styles(layer):
        p = _style_params(r, g, t)
        kind = g.match_name.split("/")[0]
        if kind == "frameFX":
            pad += float(p.get("size", 3))
        elif kind in ("dropShadow", "outerGlow"):
            pad += float(p.get("distance", 0)) + float(p.get("blur", 5)) * 1.5 + float(p.get("chokeMatte", 0))
    return pad


def apply_styles(ctx: EffectContext, img: skia.Image) -> skia.Image:
    r = ctx.renderer
    res = ctx.res
    original = img
    under: list[skia.Image] = []  # drawn below the layer
    over: list[tuple[skia.Paint, skia.Rect | None]] = []
    s = _surface_like(img)
    c = s.getCanvas()
    for g in active_styles(ctx.layer):
        kind = g.match_name.split("/")[0]
        p = _style_params(r, g, ctx.t)
        if kind == "dropShadow":
            col = p.get("color", [0, 0, 0, 1])
            op = float(p.get("opacity", 75)) / 100
            ang = math.radians(float(p.get("localLightingAngle", 120)))
            dist = float(p.get("distance", 5)) * res
            blur = float(p.get("blur", 5)) * res
            dx, dy = -math.cos(ang) * dist, math.sin(ang) * dist
            f = skia.ImageFilters.DropShadowOnly(dx, dy, blur / 3, blur / 3, color4f(col[:3], op).toColor())
            c.drawImage(original, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=f))
        elif kind == "outerGlow":
            col = p.get("color", [1, 1, 0.75, 1])
            op = float(p.get("opacity", 75)) / 100
            size = float(p.get("blur", 5)) * res
            spread = float(p.get("chokeMatte", 0)) / 100.0
            if op > 0 and size > 0:
                f = skia.ImageFilters.Blur(size / 2.5, size / 2.5, skia.TileMode.kDecal,
                                           skia.ImageFilters.Dilate(size * spread, size * spread) if spread else None)
                cf = skia.ColorFilters.Blend(color4f(col[:3]).toColor(), skia.BlendMode.kSrcIn)
                blend = skia.BlendMode.kScreen if int(p.get("mode2", 1)) == 13 else skia.BlendMode.kSrcOver
                c.drawImage(original, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=f, ColorFilter=cf, Alphaf=op, BlendMode=blend))
        elif kind == "frameFX":
            col = p.get("color", [1, 0, 0, 1])
            op = float(p.get("opacity", 100)) / 100
            size = float(p.get("size", 3)) * res
            style = int(p.get("style", 1))  # 1 outside, 2 inside, 3 center
            radius = size if style == 1 else size / 2
            f = skia.ImageFilters.Dilate(radius, radius)
            cf = skia.ColorFilters.Blend(color4f(col[:3]).toColor(), skia.BlendMode.kSrcIn)
            c.drawImage(original, 0, 0, skia.SamplingOptions(), skia.Paint(ImageFilter=f, ColorFilter=cf, Alphaf=op))
    c.drawImage(original, 0, 0)
    for g in active_styles(ctx.layer):
        kind = g.match_name.split("/")[0]
        p = _style_params(r, g, ctx.t)
        if kind == "solidFill":
            col = p.get("color", [1, 0, 0, 1])
            op = float(p.get("opacity", 100)) / 100
            c.drawRect(skia.Rect.MakeWH(img.width(), img.height()),
                       skia.Paint(Color4f=color4f(col[:3]), Alphaf=op, BlendMode=skia.BlendMode.kSrcATop))
        elif kind == "gradientFill":
            from .paint import gradient_stops

            grad = p.get("gradient")
            op = float(p.get("opacity", 100)) / 100
            if grad is None or op <= 0:
                continue
            cb = r.layer_content_bounds(ctx.layer, ctx.t)
            if cb.isEmpty():
                cb = ctx.bounds
            # content bounds in pixel space of this image
            x0, y0 = (cb.left() - ctx.bounds.left()) * res, (cb.top() - ctx.bounds.top()) * res
            w, h = cb.width() * res, cb.height() * res
            # the stored angle is offset by 90° from the AE UI value (verified against the Call-Out thumbnail)
            ang = math.radians(float(p.get("angle", 90)) + 90)
            scale = float(p.get("scale", 100)) / 100
            off = p.get("offset", [0, 0])
            dx, dy = math.cos(ang), -math.sin(ang)
            length = (abs(w * dx) + abs(h * dy)) * scale
            cx = x0 + w / 2 + off[0] / 100 * w
            cy = y0 + h / 2 + off[1] / 100 * h
            colors, pos = gradient_stops(grad)
            cols = [cc.toColor() for cc in colors]
            if p.get("reverse"):
                cols, pos = cols[::-1], [1 - q for q in pos[::-1]]
            gtype = int(p.get("type", 1))
            if gtype == 2:
                shader = skia.GradientShader.MakeRadial(skia.Point(cx, cy), max(length / 2, 0.5), cols, pos)
            else:
                shader = skia.GradientShader.MakeLinear(
                    [skia.Point(cx - dx * length / 2, cy - dy * length / 2), skia.Point(cx + dx * length / 2, cy + dy * length / 2)], cols, pos)
            c.drawRect(skia.Rect.MakeWH(img.width(), img.height()),
                       skia.Paint(Shader=shader, Alphaf=op, BlendMode=skia.BlendMode.kSrcATop))
        elif kind not in ("dropShadow", "frameFX", "outerGlow", "innerShadow", "innerGlow", "bevelEmboss"):
            if kind not in _warned:
                _warned.add(kind)
                import sys

                print(f"WARNUNG: Ebenenstil '{g.name}' wird noch nicht unterstützt", file=sys.stderr)
    inner = [(g.match_name.split("/")[0], _style_params(r, g, ctx.t)) for g in active_styles(ctx.layer)]
    inner = [(k, p) for k, p in inner if k in ("innerShadow", "innerGlow", "bevelEmboss")]
    if not inner:
        return s.makeImageSnapshot()
    base = s.makeImageSnapshot()
    alpha = original.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                             alphaType=skia.AlphaType.kPremul_AlphaType)[..., 3].astype(np.float32) / 255.0
    out = base.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                       alphaType=skia.AlphaType.kPremul_AlphaType).astype(np.float32) / 255.0
    for kind, p in inner:
        if kind == "innerShadow":
            ang = math.radians(float(p.get("localLightingAngle", 120)))
            dist = float(p.get("distance", 5)) * res
            size = float(p.get("blur", 5)) * res
            inv = _shift_fill(1.0 - alpha, -math.cos(ang) * dist, math.sin(ang) * dist, 1.0)
            inv = _choke_blur(inv, size, float(p.get("chokeMatte", 0)) / 100.0)
            out = _blend_over(out, inv * alpha, p.get("color", [0, 0, 0, 1]), float(p.get("opacity", 75)) / 100,
                              int(p.get("mode2", 5)))
        elif kind == "innerGlow":
            size = float(p.get("blur", 5)) * res
            edge = _choke_blur(1.0 - alpha, size, float(p.get("chokeMatte", 0)) / 100.0, fill=1.0)
            glow = edge if int(p.get("innerGlowSource", 2)) == 2 else 1.0 - edge
            out = _blend_over(out, glow * alpha, p.get("color", [1, 1, 0.75, 1]), float(p.get("opacity", 75)) / 100,
                              int(p.get("mode2", 11)))
        elif kind == "bevelEmboss":
            out = _bevel(out, alpha, p, res)
    out = (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(out), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                alphaType=skia.AlphaType.kPremul_AlphaType)


# layer style blend modes (mode2 / highlightMode / shadowMode)
_STYLE_BLEND = {1: "normal", 4: "darken", 5: "multiply", 10: "lighten", 11: "screen", 12: "dodge", 13: "add", 15: "overlay"}


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    if r <= 0:
        return a
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def _gauss(a: np.ndarray, sigma: float, fill: float = 0.0) -> np.ndarray:
    """Gaussian blur (three box passes) of a single channel; outside the image counts as `fill`."""
    if sigma <= 0.05:
        return a
    r = max(1, int(round(math.sqrt(12 * sigma * sigma / 3 + 1) / 2)))
    pad = 3 * r + 1
    src = np.pad(a.astype(np.float32), pad, constant_values=fill)
    for _ in range(3):
        src = _box(_box(src, r, 0), r, 1)
    return src[pad:-pad, pad:-pad]


def _shift_fill(a: np.ndarray, dx: float, dy: float, fill: float) -> np.ndarray:
    ix, iy = int(round(dx)), int(round(dy))
    out = np.full_like(a, fill)
    h, w = a.shape
    xs, xd = (0, ix) if ix >= 0 else (-ix, 0)
    ys, yd = (0, iy) if iy >= 0 else (-iy, 0)
    cw, ch = w - abs(ix), h - abs(iy)
    if cw > 0 and ch > 0:
        out[yd:yd + ch, xd:xd + cw] = a[ys:ys + ch, xs:xs + cw]
    return out


def _choke_blur(a: np.ndarray, size: float, choke: float, fill: float = 1.0) -> np.ndarray:
    """PS-style size/choke: the first `choke` part of the size spreads the matte, the rest blurs it."""
    if size <= 0.05:
        return a
    spread = size * max(0.0, min(1.0, choke))
    if spread > 0.5:
        a = _gauss(a, spread / 2, fill)
        a = np.clip(a * 2, 0, 1)
    return _gauss(a, max(0.0, size - spread) / 2.5, fill)


def _blend_over(out: np.ndarray, cover: np.ndarray, col, opacity: float, mode: int) -> np.ndarray:
    """Composite a colour with coverage `cover` (inside the layer) onto premultiplied `out`."""
    a = np.clip(cover * opacity, 0, 1)[..., None]
    da = out[..., 3:4]
    dst = np.where(da > 1e-6, out[..., :3] / np.maximum(da, 1e-6), 0.0)
    c = np.array(list(col)[:3], np.float32)
    m = _STYLE_BLEND.get(mode, "normal")
    if m == "multiply":
        res = dst * c
    elif m == "screen":
        res = 1 - (1 - dst) * (1 - c)
    elif m == "add":
        res = np.minimum(dst + c, 1)
    elif m == "darken":
        res = np.minimum(dst, c)
    elif m == "lighten":
        res = np.maximum(dst, c)
    elif m == "dodge":
        res = np.minimum(dst / np.maximum(1 - c, 1e-3), 1)
    elif m == "overlay":
        res = np.where(dst < 0.5, 2 * dst * c, 1 - 2 * (1 - dst) * (1 - c))
    else:
        res = np.broadcast_to(c, dst.shape)
    new = dst * (1 - a) + res * a
    out = out.copy()
    out[..., :3] = new * da
    return out


def _bevel(out: np.ndarray, alpha: np.ndarray, p: dict, res: float) -> np.ndarray:
    size = float(p.get("blur", 5)) * res
    if size <= 0.05:
        return out  # AE draws nothing at size 0
    # only the area around the layer's pixels can change: work on that crop (most of the image is empty)
    ys, xs = np.nonzero(alpha > 0)
    if len(ys) == 0:
        return out
    m = int(2 * size + float(p.get("softness", 0)) * res + 4)
    y0, y1 = max(0, ys.min() - m), min(alpha.shape[0], ys.max() + m + 1)
    x0, x1 = max(0, xs.min() - m), min(alpha.shape[1], xs.max() + m + 1)
    if (y1 - y0) * (x1 - x0) < alpha.size:
        out = out.copy()
        out[y0:y1, x0:x1] = _bevel_crop(out[y0:y1, x0:x1], alpha[y0:y1, x0:x1], p, res, size)
        return out
    return _bevel_crop(out, alpha, p, res, size)


def _bevel_crop(out: np.ndarray, alpha: np.ndarray, p: dict, res: float, size: float) -> np.ndarray:
    style = int(p.get("bevelStyle", 2))  # 1 outer, 2 inner, 3 emboss, 4 pillow, 5 stroke
    depth = float(p.get("strengthRatio", 100)) / 100.0
    up = int(p.get("bevelDirection", 1)) == 1
    soft = float(p.get("softness", 0)) * res
    ang = math.radians(float(p.get("localLightingAngle", 120)))
    alt = math.radians(float(p.get("localLightingAltitude", 30)))
    h = _gauss(alpha, size / 2)
    if style == 4:
        h = np.abs(h - 0.5)
    gy, gx = np.gradient(h)
    k = depth * size
    nx, ny = -gx * k, -gy * k
    if not up:
        nx, ny = -nx, -ny
    lx, ly, lz = math.cos(ang) * math.cos(alt), -math.sin(ang) * math.cos(alt), math.sin(alt)
    shade = (nx * lx + ny * ly + lz) / np.sqrt(nx * nx + ny * ny + 1) - lz
    if soft > 0.05:
        shade = _gauss(shade, soft / 2)
    # the shading only tints the layer's own pixels (outer bevel / emboss outside the layer are not drawn)
    region = alpha if style in (2, 4, 5) else (1 - alpha if style == 1 else np.ones_like(alpha))
    hi = np.clip(shade, 0, None) * 2 * region
    lo = np.clip(-shade, 0, None) * 2 * region
    out = _blend_over(out, hi, p.get("highlightColor", [1, 1, 1, 1]), float(p.get("highlightOpacity", 75)) / 100,
                      int(p.get("highlightMode", 11)))
    out = _blend_over(out, lo, p.get("shadowColor", [0, 0, 0, 1]), float(p.get("shadowOpacity", 75)) / 100,
                      int(p.get("shadowMode", 5)))
    return out


def ghostscript_bin() -> str | None:
    import shutil

    from ..paths import bundled_ghostscript

    own = bundled_ghostscript()
    if own is not None:
        return str(own)
    for name in ("gs", "gswin64c", "gswin32c"):
        exe = shutil.which(name)
        if exe:
            return exe
    cands = [Path("/opt/homebrew/bin/gs"), Path("/usr/local/bin/gs"), Path("/usr/bin/gs")]
    for root in (Path("C:/Program Files/gs"), Path("C:/Program Files (x86)/gs")):
        if root.is_dir():
            cands += sorted(root.glob("*/bin/gswin*c.exe"), reverse=True)
    return next((str(p) for p in cands if p.exists()), None)


def rasterize_eps(path: Path, width: float, height: float, oversample: float = 4) -> skia.Image | None:
    """EPS via Ghostscript if available, else the embedded TIFF preview (DOS EPS)."""
    import io
    import shutil
    import struct
    import subprocess
    import sys

    from PIL import Image as PILImage

    from ..paths import no_window_flags

    gs = ghostscript_bin()
    if gs:
        dpi = 72.0 * oversample
        res = subprocess.run(
            [gs, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dEPSCrop", "-sDEVICE=pngalpha",
             f"-r{dpi}", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", "-sOutputFile=-", str(path)],
            capture_output=True, creationflags=no_window_flags(),
        )
        if res.returncode == 0 and res.stdout:
            pil = PILImage.open(io.BytesIO(res.stdout)).convert("RGBA")
            return skia.Image.fromarray(np.ascontiguousarray(np.asarray(pil)), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                        alphaType=skia.AlphaType.kUnpremul_AlphaType)
    data = path.read_bytes()
    if data[:4] == b"\xc5\xd0\xd3\xc6":
        _, ps_off, ps_len, wmf_off, wmf_len, tif_off, tif_len = struct.unpack("<7I", data[:28])
        if tif_len:
            print(f"WARNUNG: EPS '{path.name}': Ghostscript fehlt – nutze niedrig aufgelöste Vorschau "
                  f"(installieren mit: brew install ghostscript)", file=sys.stderr)
            try:
                pil = PILImage.open(io.BytesIO(data[tif_off:tif_off + tif_len])).convert("RGBA")
            except Exception:
                return None
            return skia.Image.fromarray(np.ascontiguousarray(np.asarray(pil)), colorType=skia.ColorType.kRGBA_8888_ColorType,
                                        alphaType=skia.AlphaType.kUnpremul_AlphaType)
    print(f"WARNUNG: EPS '{path.name}' kann ohne Ghostscript nicht gerendert werden (brew install ghostscript)", file=sys.stderr)
    return None
