"""Composition renderer: layers, parenting, precomps, mattes, blend modes, 2.5D, masks, effects."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import skia

from ..evaluator import Evaluator, is_group
from ..mogrt import Mogrt
from . import effects as fx
from .paths import contour_from_shape, add_contour
from .shapes import draw_shape_layer, shape_bounds
from .text import TextRenderer

SAMPLING = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)

_BLEND = {
    "NORMAL": skia.BlendMode.kSrcOver,
    "DARKEN": skia.BlendMode.kDarken,
    "MULTIPLY": skia.BlendMode.kMultiply,
    "COLOR_BURN": skia.BlendMode.kColorBurn,
    "CLASSIC_COLOR_BURN": skia.BlendMode.kColorBurn,
    "LINEAR_BURN": skia.BlendMode.kMultiply,
    "ADD": skia.BlendMode.kPlus,
    "LINEAR_DODGE": skia.BlendMode.kPlus,
    "LIGHTEN": skia.BlendMode.kLighten,
    "SCREEN": skia.BlendMode.kScreen,
    "COLOR_DODGE": skia.BlendMode.kColorDodge,
    "CLASSIC_COLOR_DODGE": skia.BlendMode.kColorDodge,
    "OVERLAY": skia.BlendMode.kOverlay,
    "SOFT_LIGHT": skia.BlendMode.kSoftLight,
    "HARD_LIGHT": skia.BlendMode.kHardLight,
    "DIFFERENCE": skia.BlendMode.kDifference,
    "CLASSIC_DIFFERENCE": skia.BlendMode.kDifference,
    "EXCLUSION": skia.BlendMode.kExclusion,
    "HUE": skia.BlendMode.kHue,
    "SATURATION": skia.BlendMode.kSaturation,
    "COLOR": skia.BlendMode.kColor,
    "LUMINOSITY": skia.BlendMode.kLuminosity,
    "STENCIL_ALPHA": skia.BlendMode.kDstIn,
    "SILHOUETE_ALPHA": skia.BlendMode.kDstOut,
    "ALPHA_ADD": skia.BlendMode.kPlus,
    "LIGHTER_COLOR": skia.BlendMode.kLighten,
    "DARKER_COLOR": skia.BlendMode.kDarken,
}

LUMA_TO_ALPHA = skia.ColorFilters.Matrix([
    0, 0, 0, 0, 1,
    0, 0, 0, 0, 1,
    0, 0, 0, 0, 1,
    0.2126, 0.7152, 0.0722, 0, 0,
])

# --------------------------------------------------------------------------- matrices


def _T(x, y, z=0.0):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def _S(x, y, z=1.0):
    return np.diag([x, y, z, 1.0])


def _Rz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[0, 0], m[0, 1], m[1, 0], m[1, 1] = c, -s, s, c
    return m


def _Ry(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, -s, s, c
    return m


def _Rx(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[1, 1], m[1, 2], m[2, 1], m[2, 2] = c, s, -s, c
    return m


def to_skia_affine(m: np.ndarray) -> skia.Matrix:
    return skia.Matrix.MakeAll(m[0, 0], m[0, 1], m[0, 3], m[1, 0], m[1, 1], m[1, 3], 0, 0, 1)


def to_skia_homography(h: np.ndarray) -> skia.Matrix:
    h = h / h[2, 2] if abs(h[2, 2]) > 1e-12 else h
    return skia.Matrix.MakeAll(h[0, 0], h[0, 1], h[0, 2], h[1, 0], h[1, 1], h[1, 2], h[2, 0], h[2, 1], h[2, 2])


@dataclass
class Camera:
    view: np.ndarray  # world -> camera
    zoom: float
    cx: float
    cy: float

    def projection(self) -> np.ndarray:
        return np.array([[self.zoom, 0, self.cx, 0], [0, self.zoom, self.cy, 0], [0, 0, 1, 0]], dtype=float)


def _look_at(eye: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Rotation (3x3) whose +z axis points from eye to target, y down (AE convention)."""
    f = target - eye
    n = np.linalg.norm(f)
    if n < 1e-9:
        return np.eye(3)
    f /= n
    up = np.array([0.0, -1.0, 0.0])
    r = np.cross(up, f)
    if np.linalg.norm(r) < 1e-9:
        r = np.array([1.0, 0, 0])
    r /= np.linalg.norm(r)
    d = np.cross(f, r)  # down
    return np.column_stack([r, d, f])


# --------------------------------------------------------------------------- renderer


class Renderer:
    def __init__(self, mogrt: Mogrt, values: dict[str, Any] | None = None, scale: float = 1.0):
        self.mogrt = mogrt
        self.ev = Evaluator(mogrt, values)
        self.ev.source_rect_fn = self.source_rect
        self.text = TextRenderer(self.ev)
        self.scale = scale
        self._footage: dict[int, skia.Image | None] = {}
        self._warned: set[str] = set()
        self._surfaces: list[skia.Surface] = []
        self._comp_cache: dict[tuple, skia.Image] = {}  # per frame, see render_comp()
        self.time_map = None  # set_duration()
        self.motion: dict[str, Any] | None = None  # set_motion()

    def warn(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            print("WARNUNG: " + msg, file=sys.stderr)

    def set_values(self, values: dict[str, Any]) -> None:
        self.ev.apply_controls(values)

    @property
    def duration(self) -> float:
        """Output duration (may differ from the template after set_duration)."""
        return self.time_map.out_duration if self.time_map is not None else float(self.mogrt.duration)

    def set_duration(self, seconds: float | None) -> None:
        """Change the output duration; protected regions keep their speed."""
        from ..timing import TimeMap

        src = float(self.mogrt.duration)
        if seconds is None or abs(float(seconds) - src) < 1e-6:
            self.time_map = None
            return
        if seconds <= 0:
            raise ValueError("Dauer muss größer als 0 sein")
        self.time_map = TimeMap(src, float(seconds), self.mogrt.protected_regions)

    def set_motion(self, motion: dict[str, Any] | None) -> None:
        """Premiere-style "Motion" on top of the graphic.

        position: [x, y] in comp pixels (default: comp centre), scale: percent,
        rotation: degrees, opacity: percent.
        """
        if not motion:
            self.motion = None
            return
        w, h = self.mogrt.width, self.mogrt.height
        pos = motion.get("position") or [w / 2, h / 2]
        m = {
            "position": [float(pos[0]), float(pos[1])],
            "scale": float(motion.get("scale", 100) if motion.get("scale") is not None else 100),
            "rotation": float(motion.get("rotation") or 0),
            "opacity": float(motion.get("opacity", 100) if motion.get("opacity") is not None else 100),
        }
        identity = (abs(m["position"][0] - w / 2) < 1e-6 and abs(m["position"][1] - h / 2) < 1e-6
                    and abs(m["scale"] - 100) < 1e-6 and abs(m["rotation"]) < 1e-6 and abs(m["opacity"] - 100) < 1e-6)
        self.motion = None if identity else m

    def comp_time(self, t: float) -> float:
        return self.time_map(t) if self.time_map is not None else t

    # ------------------------------------------------------------------ public
    def render_frame(self, t: float) -> skia.Image:
        """Render the frame at output time t (seconds)."""
        self.ev.clear_cache()
        self._comp_cache = {}
        comp = self.mogrt.main_comp
        if self.motion is None:
            return self.render_comp(comp, self.comp_time(t), self.scale)
        mo = self.motion
        s = mo["scale"] / 100.0
        # Like Premiere, the graphic is not clipped to the comp frame: the main comp's layers are
        # drawn directly with the motion transform (content beyond the frame edge stays visible)
        w, h = max(1, int(round(comp.width * self.scale))), max(1, int(round(comp.height * self.scale)))
        surf = skia.Surface.MakeRasterN32Premul(w, h)
        c = surf.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        c.scale(self.scale, self.scale)
        c.translate(mo["position"][0], mo["position"][1])
        c.rotate(mo["rotation"])
        c.scale(s, s)
        c.translate(-comp.width / 2, -comp.height / 2)
        opacity = max(0.0, min(1.0, mo["opacity"] / 100.0))
        if opacity < 0.9995:
            c.saveLayer(None, skia.Paint(Alphaf=opacity))
        self._surfaces.append(surf)
        try:
            self.draw_layers(c, comp, self.comp_time(t), self.scale * max(s, 0.05))
        finally:
            self._surfaces.pop()
        if opacity < 0.9995:
            c.restore()
        return surf.makeImageSnapshot()

    def render_comp(self, comp: Any, t: float, scale: float) -> skia.Image:
        # the same precomp is often used several times per frame (layer + track matte, duplicates)
        key = (id(comp), round(t, 6), round(scale, 4))
        img = self._comp_cache.get(key)
        if img is None:
            img = self._comp_cache[key] = self._render_comp(comp, t, scale)
        return img

    def _render_comp(self, comp: Any, t: float, scale: float) -> skia.Image:
        w, h = max(1, int(round(comp.width * scale))), max(1, int(round(comp.height * scale)))
        surf = skia.Surface.MakeRasterN32Premul(w, h)
        canvas = surf.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.scale(scale, scale)
        self._surfaces.append(surf)
        try:
            self.draw_layers(canvas, comp, t, scale)
        finally:
            self._surfaces.pop()
        return surf.makeImageSnapshot()

    # ------------------------------------------------------------------ helpers
    def _prop(self, layer: Any, *path: str) -> Any:
        node = layer
        for p in path:
            try:
                node = node.property(p)
            except Exception:
                return None
            if node is None:
                return None
        return node

    def _val(self, layer: Any, t: float, *path: str, default: Any = None) -> Any:
        p = self._prop(layer, *path)
        if p is None:
            return default
        return self.ev.value(p, t)

    def local_matrix(self, layer: Any, t: float) -> np.ndarray:
        tr = layer.transform
        is3d = bool(getattr(layer, "three_d_layer", False))
        a = self._val(tr, t, "ADBE Anchor Point", default=[0, 0, 0]) or [0, 0, 0]
        pos_p = self._prop(tr, "ADBE Position")
        if pos_p is not None and getattr(pos_p, "dimensions_separated", False):
            p = [
                self._val(tr, t, "ADBE Position_0", default=0.0),
                self._val(tr, t, "ADBE Position_1", default=0.0),
                self._val(tr, t, "ADBE Position_2", default=0.0),
            ]
        else:
            p = self._val(tr, t, "ADBE Position", default=[0, 0, 0]) or [0, 0, 0]
        s = self._val(tr, t, "ADBE Scale", default=[100, 100, 100]) or [100, 100, 100]
        a = list(a) + [0] * (3 - len(a))
        p = list(p) + [0] * (3 - len(p))
        s = list(s) + [100] * (3 - len(s))
        m = _T(p[0], p[1], p[2] if is3d else 0.0)
        if is3d:
            ori = self._val(tr, t, "ADBE Orientation", default=[0, 0, 0]) or [0, 0, 0]
            rx = self._val(tr, t, "ADBE Rotate X", default=0.0)
            ry = self._val(tr, t, "ADBE Rotate Y", default=0.0)
            rz = self._val(tr, t, "ADBE Rotate Z", default=0.0)
            m = m @ _Rx(ori[0]) @ _Ry(ori[1]) @ _Rz(ori[2]) @ _Rx(rx) @ _Ry(ry) @ _Rz(rz)
            m = m @ _S(s[0] / 100, s[1] / 100, s[2] / 100) @ _T(-a[0], -a[1], -a[2])
        else:
            rz = self._val(tr, t, "ADBE Rotate Z", default=0.0)
            m = m @ _Rz(rz) @ _S(s[0] / 100, s[1] / 100) @ _T(-a[0], -a[1])
        return m

    def world_matrix(self, layer: Any, t: float) -> np.ndarray:
        m = self.local_matrix(layer, t)
        par = layer.parent
        guard = 0
        while par is not None and guard < 64:
            m = self.local_matrix(par, t) @ m
            par = par.parent
            guard += 1
        return m

    def camera_for(self, comp: Any, t: float) -> Camera:
        cams = [l for l in comp.layers if type(l).__name__ == "CameraLayer" and l.enabled and l.in_point <= t < l.out_point]
        w, h = comp.width, comp.height
        if not cams:
            zoom = w / 2 / math.tan(math.radians(39.5978 / 2))
            view = np.linalg.inv(_T(w / 2, h / 2, -zoom))
            return Camera(view, zoom, w / 2, h / 2)
        cam = cams[0]
        tr = cam.transform
        pos = np.array((self._val(tr, t, "ADBE Position", default=[w / 2, h / 2, -2000]) + [0, 0, 0])[:3], dtype=float)
        zoom = self._val(cam, t, "ADBE Camera Options Group", "ADBE Camera Zoom", default=w / 2 / math.tan(math.radians(19.8)))
        ori = self._val(tr, t, "ADBE Orientation", default=[0, 0, 0]) or [0, 0, 0]
        rx = self._val(tr, t, "ADBE Rotate X", default=0.0)
        ry = self._val(tr, t, "ADBE Rotate Y", default=0.0)
        rz = self._val(tr, t, "ADBE Rotate Z", default=0.0)
        rot = np.eye(4)
        if int(getattr(cam, "auto_orient", 4212)) == 4214:  # towards point of interest
            poi = np.array((self._val(tr, t, "ADBE Anchor Point", default=[w / 2, h / 2, 0]) + [0, 0, 0])[:3], dtype=float)
            rot[:3, :3] = _look_at(pos, poi)
        rot = rot @ _Rx(ori[0]) @ _Ry(ori[1]) @ _Rz(ori[2]) @ _Rx(rx) @ _Ry(ry) @ _Rz(rz)
        world = _T(*pos) @ rot
        if cam.parent is not None:
            world = self.world_matrix(cam.parent, t) @ world
        return Camera(np.linalg.inv(world), float(zoom), w / 2, h / 2)

    # ------------------------------------------------------------------ source rect
    def source_rect(self, layer: Any, t: float, extents: bool) -> dict:
        kind = type(layer).__name__
        if kind == "TextLayer":
            r = self.text.bounds(layer, t)
        elif kind == "ShapeLayer":
            r = shape_bounds(self.ev, layer, t)
        else:
            return {"top": 0, "left": 0, "width": float(getattr(layer, "width", 0)), "height": float(getattr(layer, "height", 0))}
        if r.isEmpty():
            return {"top": 0.0, "left": 0.0, "width": 0.0, "height": 0.0}
        return {"top": r.top(), "left": r.left(), "width": r.width(), "height": r.height()}

    # ------------------------------------------------------------------ layer content
    def layer_content_bounds(self, layer: Any, t: float) -> skia.Rect:
        kind = type(layer).__name__
        if kind == "ShapeLayer":
            return shape_bounds(self.ev, layer, t)
        if kind == "TextLayer":
            return self.text.bounds(layer, t)
        return skia.Rect.MakeWH(float(getattr(layer, "width", 0) or 0), float(getattr(layer, "height", 0) or 0))

    def source_time(self, layer: Any, t: float) -> float:
        if getattr(layer, "time_remap_enabled", False):
            p = self._prop(layer, "ADBE Time Remapping")
            if p is not None:
                return float(self.ev.value(p, t))
        stretch = (layer.stretch or 100.0) / 100.0
        return (t - layer.start_time) / stretch if stretch else 0.0

    def draw_content(self, canvas: skia.Canvas, layer: Any, t: float, scale: float) -> None:
        """Draw layer content in layer space (no layer transform/opacity)."""
        kind = type(layer).__name__
        if kind == "ShapeLayer":
            draw_shape_layer(self.ev, canvas, layer, t)
            return
        if kind == "TextLayer":
            self.text.draw(canvas, layer, t)
            return
        src = getattr(layer, "source", None)
        if src is None:
            return
        sk = type(src).__name__
        if sk == "CompItem":
            st = self.source_time(layer, t)
            sub_scale = scale * self._matrix_scale_hint
            sub_scale = min(max(sub_scale, 0.05), 4.0)
            img = self.render_comp(src, st, sub_scale)
            canvas.drawImageRect(img, skia.Rect.MakeWH(src.width, src.height), SAMPLING, skia.Paint(AntiAlias=True))
            return
        if sk == "FootageItem":
            ms = src.main_source
            msk = type(ms).__name__
            if msk == "SolidSource":
                col = list(ms.color) + [1.0]
                p = skia.Paint(Color4f=skia.Color4f(*col[:4]))
                canvas.drawRect(skia.Rect.MakeWH(src.width, src.height), p)
                return
            if msk == "PlaceholderSource":
                return
            img = self.footage_image(src)
            if img is not None:
                canvas.drawImageRect(img, skia.Rect.MakeWH(src.width, src.height), SAMPLING, skia.Paint(AntiAlias=True))

    _matrix_scale_hint = 1.0

    def find_footage_file(self, item: Any) -> Path | None:
        """Locate footage collected into the MOGRT (original paths may be Windows paths)."""
        raw = str(getattr(item, "file", "") or "")
        if not raw:
            return None
        name = raw.replace("\\", "/").rsplit("/", 1)[-1]
        p = Path(raw)
        if p.exists():
            return p
        hits = list((self.mogrt.root / "aegraphic").rglob(name))
        return hits[0] if hits else None

    def footage_image(self, item: Any) -> skia.Image | None:
        key = id(item)
        if key in self._footage:
            return self._footage[key]
        img = None
        local = self.find_footage_file(item)
        if local is None:
            self.warn(f"Footage '{item.name}' nicht gefunden")
        else:
            suf = local.suffix.lower()
            try:
                if suf in (".ai", ".pdf"):
                    img = fx.rasterize_pdf(local, item.width, item.height, oversample=4)
                elif suf in (".eps", ".ps"):
                    img = fx.rasterize_eps(local, item.width, item.height, oversample=4)
                elif suf in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".psd"):
                    from PIL import Image as PILImage

                    pil = PILImage.open(local).convert("RGBA")
                    img = skia.Image.fromarray(np.asarray(pil), colorType=skia.ColorType.kRGBA_8888_ColorType)
                elif suf in (".wav", ".aif", ".aiff", ".mp3", ".m4a"):
                    img = None
                else:
                    self.warn(f"Footage-Typ {suf} ({item.name}) wird nicht unterstützt")
            except Exception as e:
                self.warn(f"Footage '{item.name}' konnte nicht geladen werden: {e}")
        self._footage[key] = img
        return img

    # ------------------------------------------------------------------ layers
    def is_visual(self, layer: Any) -> bool:
        kind = type(layer).__name__
        if kind in ("CameraLayer", "LightLayer"):
            return False
        if getattr(layer, "null_layer", False) or getattr(layer, "guide_layer", False):
            return False
        if kind == "AVLayer":
            src = getattr(layer, "source", None)
            if src is None or not getattr(src, "has_video", True):
                return False
        return True

    def draw_layers(self, canvas: skia.Canvas, comp: Any, t: float, scale: float,
                    collapse: tuple[np.ndarray, float, Camera | None, bool] | None = None) -> None:
        """Draw all layers of comp (bottom to top) onto canvas (comp space)."""
        layers = list(comp.layers)
        if collapse is not None:
            base, base_op, camera, base3d = collapse
        else:
            base, base_op, camera, base3d = np.eye(4), 1.0, None, False
        has3d = any(getattr(l, "three_d_layer", False) for l in layers)
        if camera is None and has3d:
            camera = self.camera_for(comp, t)
        # group into runs: consecutive 3D layers are depth-sorted together
        order = [l for l in reversed(layers)]
        run: list[tuple[float, Any, np.ndarray]] = []

        def flush():
            run.sort(key=lambda e: -e[0])
            for _, l, m in run:
                self.draw_layer(canvas, comp, l, t, scale, m, base_op, camera, True)
            run.clear()

        for layer in order:
            if not layer.enabled or not self.is_visual(layer):
                continue
            if not (layer.in_point <= t < layer.out_point):
                continue
            if getattr(layer, "adjustment_layer", False):
                if self._has_effects(layer):
                    flush()
                    if collapse is None and self._surfaces:
                        self.apply_adjustment(canvas, comp, layer, t, scale, base_op)
                    else:
                        self.warn(f"Einstellungsebene '{layer.name}' in kollabierter Precomp wird nicht unterstützt")
                continue
            m = base @ self.world_matrix(layer, t)
            is3d = bool(getattr(layer, "three_d_layer", False)) or (base3d and collapse is not None)
            if is3d and camera is not None:
                # depth of the layer's anchor in camera space
                anchor = self._val(layer.transform, t, "ADBE Anchor Point", default=[0, 0, 0]) or [0, 0, 0]
                pt = m @ np.array([anchor[0], anchor[1], (anchor[2] if len(anchor) > 2 else 0), 1.0])
                depth = (camera.view @ pt)[2]
                run.append((depth, layer, m))
                continue
            flush()
            self.draw_layer(canvas, comp, layer, t, scale, m, base_op, camera, False)
        flush()

    def apply_adjustment(self, canvas: skia.Canvas, comp: Any, layer: Any, t: float, scale: float, op_mult: float) -> None:
        """Apply an adjustment layer's effects to everything drawn so far in this comp."""
        surf = self._surfaces[-1]
        before = surf.makeImageSnapshot()
        bounds = skia.Rect.MakeWH(comp.width, comp.height)
        ctx = fx.EffectContext(self, layer, t, bounds, scale)
        img = before
        effects = [e for e in (self._prop(layer, "ADBE Effect Parade") or []) if getattr(e, "enabled", True) and not fx.is_control(e)]
        for e in effects:
            img = fx.apply(ctx, e, img)
        opacity = self._val(layer.transform, t, "ADBE Opacity", default=100.0) / 100.0 * op_mult
        canvas.save()
        canvas.resetMatrix()
        canvas.clear(skia.ColorTRANSPARENT)
        if opacity < 0.9995:
            canvas.drawImage(before, 0, 0, skia.SamplingOptions(), skia.Paint(Alphaf=1 - opacity))
            canvas.drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(Alphaf=opacity, BlendMode=skia.BlendMode.kPlus))
        else:
            canvas.drawImage(img, 0, 0)
        canvas.restore()

    def _canvas_matrix(self, m: np.ndarray, camera: Camera | None, is3d: bool) -> skia.Matrix | None:
        if is3d and camera is not None:
            full = camera.projection() @ camera.view @ m
            h = full[:, [0, 1, 3]]
            # cull layers behind the camera
            if h[2, 2] <= 1e-6 and abs(h[2, 0]) < 1e-9 and abs(h[2, 1]) < 1e-9:
                return None
            return to_skia_homography(h)
        return to_skia_affine(m)

    def draw_layer(self, canvas: skia.Canvas, comp: Any, layer: Any, t: float, scale: float,
                   m: np.ndarray, op_mult: float, camera: Camera | None, is3d: bool) -> None:
        opacity = self._val(layer.transform, t, "ADBE Opacity", default=100.0) / 100.0 * op_mult
        if opacity <= 0.0005:
            return
        matte_layer = getattr(layer, "track_matte_layer", None) if getattr(layer, "has_track_matte", False) else None
        blend_name = getattr(getattr(layer, "blending_mode", None), "name", "NORMAL")
        if blend_name in ("STENCIL_LUMA", "SILHOUETTE_LUMA"):
            self.warn(f"Blend-Modus {blend_name} angenähert")
        blend = _BLEND.get(blend_name, skia.BlendMode.kSrcOver)
        if blend_name not in _BLEND and blend_name not in ("STENCIL_LUMA", "SILHOUETTE_LUMA"):
            self.warn(f"Blend-Modus {blend_name} nicht unterstützt – nutze Normal")
        if blend_name == "STENCIL_LUMA":
            blend = skia.BlendMode.kDstIn
        if blend_name == "SILHOUETTE_LUMA":
            blend = skia.BlendMode.kDstOut

        # collapsed precomp: draw its layers straight into this canvas
        src = getattr(layer, "source", None)
        if (
            getattr(layer, "collapse_transformation", False)
            and src is not None
            and type(src).__name__ == "CompItem"
            and matte_layer is None
            and not self._has_effects(layer)
            and not self._has_masks(layer)
        ):
            st = self.source_time(layer, t)
            paint = skia.Paint(Alphaf=1.0, BlendMode=blend)
            canvas.saveLayer(None, paint) if blend != skia.BlendMode.kSrcOver else canvas.save()
            self.draw_layers(canvas, src, st, scale, (m, opacity, camera if is3d else None, is3d))
            canvas.restore()
            return

        cm = self._canvas_matrix(m, camera, is3d)
        if cm is None:
            return
        self._matrix_scale_hint = math.sqrt(abs(cm.getScaleX() * cm.getScaleY() - cm.getSkewX() * cm.getSkewY())) or 1.0

        if matte_layer is not None:
            self._draw_with_matte(canvas, comp, layer, matte_layer, t, scale, cm, opacity, blend, camera, op_mult)
            return
        self._draw_layer_body(canvas, layer, t, scale, cm, opacity, blend)

    def _has_effects(self, layer: Any) -> bool:
        if fx.active_styles(layer):
            return True
        parade = self._prop(layer, "ADBE Effect Parade")
        if parade is None:
            return False
        return any(getattr(e, "enabled", True) and not fx.is_control(e) for e in parade)

    def _has_masks(self, layer: Any) -> bool:
        parade = self._prop(layer, "ADBE Mask Parade")
        if parade is None:
            return False
        return any(getattr(mk, "enabled", True) for mk in parade)

    def _draw_layer_body(self, canvas: skia.Canvas, layer: Any, t: float, scale: float,
                         cm: skia.Matrix, opacity: float, blend: skia.BlendMode) -> None:
        if self._has_effects(layer) or self._has_masks(layer):
            img, origin, res = self.render_layer_offscreen(layer, t, scale, cm)
            if img is None:
                return
            canvas.save()
            canvas.concat(cm)
            paint = skia.Paint(AntiAlias=True, Alphaf=opacity, BlendMode=blend)
            dst = skia.Rect.MakeXYWH(origin[0], origin[1], img.width() / res, img.height() / res)
            canvas.drawImageRect(img, dst, SAMPLING, paint)
            canvas.restore()
            return
        canvas.save()
        canvas.concat(cm)
        if opacity < 0.9995 or blend != skia.BlendMode.kSrcOver:
            canvas.saveLayer(None, skia.Paint(Alphaf=opacity, BlendMode=blend))
            self.draw_content(canvas, layer, t, scale)
            canvas.restore()
        else:
            self.draw_content(canvas, layer, t, scale)
        canvas.restore()

    def render_layer_offscreen(self, layer: Any, t: float, scale: float, cm: skia.Matrix):
        """Render content + masks + effects in layer space. Returns (image, origin xy, pixels per unit)."""
        bounds = self.layer_content_bounds(layer, t)
        effects = [e for e in (self._prop(layer, "ADBE Effect Parade") or []) if getattr(e, "enabled", True) and not fx.is_control(e)]
        pad = sum(fx.padding(self, e, t) for e in effects) + fx.style_padding(self, layer, t)
        # effects may grow the layer beyond its bounds (shadows, glows, tiling) like in AE
        pad = min(pad, 4000.0)
        bounds = skia.Rect.MakeLTRB(bounds.left() - pad, bounds.top() - pad, bounds.right() + pad, bounds.bottom() + pad)
        if bounds.isEmpty() or bounds.width() <= 0 or bounds.height() <= 0:
            return None, (0, 0), 1.0
        res = scale * min(max(self._matrix_scale_hint / max(scale, 1e-6), 0.1), 4.0)
        w = int(math.ceil(bounds.width() * res))
        h = int(math.ceil(bounds.height() * res))
        if w * h > 8192 * 8192:
            res *= math.sqrt(8192 * 8192 / (w * h))
            w, h = int(bounds.width() * res), int(bounds.height() * res)
        surf = skia.Surface.MakeRasterN32Premul(max(w, 1), max(h, 1))
        c = surf.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        c.scale(res, res)
        c.translate(-bounds.left(), -bounds.top())
        self.draw_content(c, layer, t, scale)
        if self._has_masks(layer):
            self._apply_masks(c, layer, t, bounds)
        img = surf.makeImageSnapshot()
        ctx = fx.EffectContext(self, layer, t, bounds, res, original=img)
        for e in effects:
            img = fx.apply(ctx, e, img)
        if fx.active_styles(layer):
            img = fx.apply_styles(ctx, img)
        return img, (bounds.left(), bounds.top()), res

    def _apply_masks(self, c: skia.Canvas, layer: Any, t: float, bounds: skia.Rect) -> None:
        parade = self._prop(layer, "ADBE Mask Parade")
        mask_surf_path = None
        coverage = None
        for mk in parade:
            if not getattr(mk, "enabled", True):
                continue
            shape = self._val(mk, t, "ADBE Mask Shape")
            if shape is None:
                continue
            p = skia.Path()
            add_contour(p, contour_from_shape(shape))
            inverted = bool(getattr(mk, "inverted", False))
            if inverted:
                p.toggleInverseFillType()
            op = getattr(getattr(mk, "mask_mode", None), "name", "ADD")
            if coverage is None:
                if op in ("SUBTRACT",):
                    full = skia.Path()
                    full.addRect(bounds)
                    coverage = skia.Op(full, p, skia.PathOp.kDifference_PathOp)
                elif op == "NONE":
                    continue
                else:
                    coverage = p
                continue
            pop = {
                "ADD": skia.PathOp.kUnion_PathOp,
                "SUBTRACT": skia.PathOp.kDifference_PathOp,
                "INTERSECT": skia.PathOp.kIntersect_PathOp,
                "DIFFERENCE": skia.PathOp.kXOR_PathOp,
                "LIGHTEN": skia.PathOp.kUnion_PathOp,
                "DARKEN": skia.PathOp.kIntersect_PathOp,
            }.get(op)
            if pop is not None:
                r = skia.Op(coverage, p, pop)
                coverage = r if r is not None else coverage
        if coverage is None:
            coverage = skia.Path()  # all masks empty -> nothing visible
        paint = skia.Paint(AntiAlias=True)
        opacities = [self._val(mk, t, "ADBE Mask Opacity", default=100.0) for mk in parade if getattr(mk, "enabled", True)]
        if opacities:
            paint.setAlphaf(max(0.0, min(1.0, max(opacities) / 100.0)))
        feather = 0.0
        for mk in parade:
            fv = self._val(mk, t, "ADBE Mask Feather", default=[0, 0])
            if fv:
                feather = max(feather, max(fv) if isinstance(fv, list) else fv)
        if feather > 0:
            paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, feather / 2))
        # composite the coverage with DstIn over the whole layer (pixels outside the path vanish)
        c.saveLayer(None, skia.Paint(BlendMode=skia.BlendMode.kDstIn))
        c.drawPath(coverage, paint)
        c.restore()

    def _draw_with_matte(self, canvas: skia.Canvas, comp: Any, layer: Any, matte: Any, t: float, scale: float,
                         cm: skia.Matrix, opacity: float, blend: skia.BlendMode, camera: Camera | None, op_mult: float) -> None:
        mtype = getattr(getattr(layer, "track_matte_type", None), "name", "ALPHA")
        canvas.saveLayer(None, skia.Paint(BlendMode=blend))
        # layer itself
        self._draw_layer_body(canvas, layer, t, scale, cm, opacity, skia.BlendMode.kSrcOver)
        # matte
        if matte.in_point <= t < matte.out_point:
            mm = self.world_matrix(matte, t)
            m_is3d = bool(getattr(matte, "three_d_layer", False))
            mcm = self._canvas_matrix(mm, camera, m_is3d and camera is not None)
            if mcm is not None:
                if mtype in ("LUMA", "LUMA_INVERTED"):
                    lp = skia.Paint(ColorFilter=LUMA_TO_ALPHA)
                    canvas.saveLayer(None, skia.Paint(BlendMode=skia.BlendMode.kDstIn if mtype == "LUMA" else skia.BlendMode.kDstOut))
                    canvas.saveLayer(None, lp)
                else:
                    canvas.saveLayer(None, skia.Paint(BlendMode=skia.BlendMode.kDstIn if mtype == "ALPHA" else skia.BlendMode.kDstOut))
                m_op = self._val(matte.transform, t, "ADBE Opacity", default=100.0) / 100.0
                saved = self._matrix_scale_hint
                self._matrix_scale_hint = math.sqrt(abs(mcm.getScaleX() * mcm.getScaleY() - mcm.getSkewX() * mcm.getSkewY())) or 1.0
                inner_matte = getattr(matte, "track_matte_layer", None) if getattr(matte, "has_track_matte", False) else None
                if inner_matte is not None:
                    self._draw_with_matte(canvas, comp, matte, inner_matte, t, scale, mcm, m_op, skia.BlendMode.kSrcOver, camera, 1.0)
                else:
                    self._draw_layer_body(canvas, matte, t, scale, mcm, m_op, skia.BlendMode.kSrcOver)
                self._matrix_scale_hint = saved
                if mtype in ("LUMA", "LUMA_INVERTED"):
                    canvas.restore()
                canvas.restore()
        elif mtype in ("ALPHA", "LUMA"):
            canvas.clear(skia.ColorTRANSPARENT)
        canvas.restore()
