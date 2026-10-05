"""Property evaluation: keyframes, Essential Graphics overrides and expressions."""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable

import quickjs

from .mogrt import Mogrt
from .transpile import PRELUDE as _BIN_PRELUDE, transpile


@dataclass(frozen=True)
class TextValue:
    """Value of a Source Text property: the string plus its (py_aep) document style."""

    text: str
    doc: Any

    def __str__(self) -> str:
        return self.text


def is_group(p: Any) -> bool:
    return hasattr(p, "num_properties") and not hasattr(p, "keyframes")


def is_synthetic(p: Any) -> bool:
    """True for properties py_aep synthesized with defaults (not present in the file)."""
    tdsb = getattr(p, "_tdsb", None)
    return bool(tdsb is not None and getattr(tdsb, "synthetic", False))


def owning_layer(p: Any) -> Any:
    while p is not None and type(p).__name__ not in ("AVLayer", "ShapeLayer", "TextLayer", "CameraLayer", "LightLayer", "Layer"):
        p = p.parent_property
    return p


def camel(name: str) -> str:
    parts = re.split(r"[^0-9A-Za-z]+", name)
    parts = [p for p in parts if p]
    if not parts:
        return name
    return parts[0][:1].lower() + parts[0][1:] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


# AE expression attribute names that do not follow camelCase(display name)
_ALIASES = {
    "rotation": ("ADBE Rotate Z", "ADBE Vector Rotation", "ADBE Text Rotation"),
    "zRotation": ("ADBE Rotate Z",),
    "xRotation": ("ADBE Rotate X",),
    "yRotation": ("ADBE Rotate Y",),
    "position": ("ADBE Position", "ADBE Vector Position", "ADBE Text Position 3D"),
    "anchorPoint": ("ADBE Anchor Point", "ADBE Vector Anchor"),
    "scale": ("ADBE Scale", "ADBE Vector Scale", "ADBE Text Scale 3D"),
    "opacity": ("ADBE Opacity", "ADBE Vector Group Opacity", "ADBE Vector Fill Opacity", "ADBE Vector Stroke Opacity", "ADBE Text Opacity"),
    "sourceText": ("ADBE Text Document",),
    "strokeWidth": ("ADBE Vector Stroke Width",),
    "color": ("ADBE Vector Fill Color", "ADBE Vector Stroke Color"),
    "path": ("ADBE Vector Shape",),
    "timeRemap": ("ADBE Time Remapping",),
    "transform": ("ADBE Transform Group", "ADBE Vector Transform Group"),
    "text": ("ADBE Text Properties",),
    "content": ("ADBE Root Vectors Group", "ADBE Vectors Group"),
    "start": ("ADBE Vector Trim Start", "ADBE Text Percent Start"),
    "end": ("ADBE Vector Trim End", "ADBE Text Percent End"),
    "offset": ("ADBE Vector Trim Offset", "ADBE Text Percent Offset"),
    "size": ("ADBE Vector Rect Size", "ADBE Vector Ellipse Size"),
    "roundness": ("ADBE Vector Rect Roundness",),
    "pointOfInterest": ("ADBE Anchor Point",),
    "zoom": ("ADBE Camera Zoom",),
}


def find_child(group: Any, name: str) -> Any:
    """Locate a child property/group by AE expression name, display name or match name."""
    if isinstance(name, (int, float)):
        kids = list(group)
        i = int(name) - 1
        return kids[i] if 0 <= i < len(kids) else None
    kids = list(group)
    for k in kids:
        if k.name == name or k.match_name == name:
            return k
    aliases = _ALIASES.get(name, ())
    for k in kids:
        if k.match_name in aliases:
            return k
    for k in kids:
        if camel(k.name) == name:
            return k
    # look through the implicit "Contents" level of vector groups
    for k in kids:
        if k.match_name in ("ADBE Vectors Group",) and is_group(k):
            hit = find_child(k, name)
            if hit is not None:
                return hit
    return None


_PRELUDE = r"""
var __codes = {};
function __wrap(r) {
  if (r === null || r === undefined) return r;
  if (r.t === 'x') throw new Error(r.m);
  if (r.t === 'v') return r.v;
  if (r.t === 'r') return __proxy(r.r);
  if (r.t === 'm') return function() {
    return __wrap(JSON.parse(__callm(JSON.stringify(r.r), r.n, JSON.stringify(Array.prototype.slice.call(arguments)))));
  };
  return undefined;
}
function __proxy(ref) {
  var f = function() {
    return __wrap(JSON.parse(__call(JSON.stringify(ref), JSON.stringify(Array.prototype.slice.call(arguments)))));
  };
  return new Proxy(f, {
    get: function(tg, name) {
      if (name === '__ref') return ref;
      if (typeof name === 'symbol') {
        if (name === Symbol.toPrimitive) return function() { return __wrap(JSON.parse(__get(JSON.stringify(ref), 'value'))); };
        return undefined;
      }
      if (name === 'toJSON') return function() { return __wrap(JSON.parse(__get(JSON.stringify(ref), 'value'))); };
      if (name === 'toString') return function() { return String(__wrap(JSON.parse(__get(JSON.stringify(ref), 'value')))); };
      return __wrap(JSON.parse(__get(JSON.stringify(ref), name)));
    }
  });
}
function __num(v) { return (v && v.__ref) ? v.valueOf() : v; }
function add(a, b) { if (Array.isArray(a)) return a.map(function(x, i) { return x + (Array.isArray(b) ? (b[i] || 0) : b); }); return a + b; }
function sub(a, b) { if (Array.isArray(a)) return a.map(function(x, i) { return x - (Array.isArray(b) ? (b[i] || 0) : b); }); return a - b; }
function mul(a, b) { if (Array.isArray(a)) return a.map(function(x) { return x * b; }); if (Array.isArray(b)) return b.map(function(x) { return x * a; }); return a * b; }
function div(a, b) { if (Array.isArray(a)) return a.map(function(x) { return x / b; }); return a / b; }
function clamp(v, lo, hi) { if (Array.isArray(v)) return v.map(function(x, i) { return Math.min(Math.max(x, Array.isArray(lo) ? lo[i] : lo), Array.isArray(hi) ? hi[i] : hi); }); return Math.min(Math.max(v, lo), hi); }
function length(a, b) { if (b !== undefined) a = sub(a, b); if (!Array.isArray(a)) return Math.abs(a); return Math.sqrt(a.reduce(function(s, x) { return s + x * x; }, 0)); }
function normalize(a) { var l = length(a); return div(a, l || 1); }
function dot(a, b) { return a.reduce(function(s, x, i) { return s + x * b[i]; }, 0); }
function degreesToRadians(d) { return d * Math.PI / 180; }
function radiansToDegrees(r) { return r * 180 / Math.PI; }
function __lerp(a, b, f) { if (Array.isArray(a)) return a.map(function(x, i) { return x + (b[i] - x) * f; }); return a + (b - a) * f; }
function __interp(kind, t, tMin, tMax, v1, v2) {
  if (v1 === undefined) { v1 = tMin; v2 = tMax; tMin = 0; tMax = 1; }
  var f = tMax === tMin ? (t >= tMax ? 1 : 0) : (t - tMin) / (tMax - tMin);
  f = Math.min(Math.max(f, 0), 1);
  if (kind === 1) f = f * f * (3 - 2 * f);
  else if (kind === 2) f = f * f;
  else if (kind === 3) f = 1 - (1 - f) * (1 - f);
  return __lerp(v1, v2, f);
}
function linear(t, a, b, c, d) { return __interp(0, t, a, b, c, d); }
function ease(t, a, b, c, d) { return __interp(1, t, a, b, c, d); }
function easeIn(t, a, b, c, d) { return __interp(2, t, a, b, c, d); }
function easeOut(t, a, b, c, d) { return __interp(3, t, a, b, c, d); }
var __seed = 0, __rs = 1;
function seedRandom(s, timeless) { __rs = (Math.abs(s * 9301 + 49297) % 233280) || 1; }
function __rnd() { __rs = (__rs * 9301 + 49297) % 233280; return __rs / 233280; }
function random(a, b) {
  if (a === undefined) return __rnd();
  if (b === undefined) { if (Array.isArray(a)) return a.map(function(x) { return __rnd() * x; }); return __rnd() * a; }
  if (Array.isArray(a)) return a.map(function(x, i) { return x + __rnd() * (b[i] - x); });
  return a + __rnd() * (b - a);
}
function gaussRandom(a, b) { return random(a, b); }
var __layerNames = {effect:1, content:1, sourceRectAtTime:1, transform:1, text:1, mask:1, index:1, name:1,
  inPoint:1, outPoint:1, startTime:1, position:1, scale:1, rotation:1, anchorPoint:1, opacity:1,
  toComp:1, fromComp:1, toWorld:1, fromWorld:1, width:1, height:1, parent:1, hasParent:1, marker:1,
  timeRemap:1, source:1, sourceTime:1, enabled:1, active:1, audioActive:1};
function __run(id, t, vjson, lref, cref, pref, fd) {
  var time = t;
  var value = JSON.parse(vjson);
  var thisLayer = __proxy(JSON.parse(lref));
  var thisComp = __proxy(JSON.parse(cref));
  var thisProperty = __proxy(JSON.parse(pref));
  var thisProject = null;
  var comp = function(n) { return __wrap(JSON.parse(__callm('{"h":0}', 'comp', JSON.stringify([n])))); };
  var footage = function(n) { return null; };
  var wiggle = function(f, a, o, m, tt) { var r = JSON.parse(__wiggle(f, a, o === undefined ? 1 : o, m === undefined ? 0.5 : m, tt === undefined ? time : tt)); if (r && r.t === 'x') throw new Error(r.m); return r; };
  var smooth = function() { return value; };
  var posterizeTime = function(f) {
    var pt = f > 0 ? Math.floor(time * f + 1e-6) / f : 0;
    if (Math.abs(pt - time) < 1e-9) return;
    var r = JSON.parse(__posterize(pt)); if (r && r.t === 'x') throw new Error(r.m);
    time = pt; value = r.v;
  };
  var valueAtTime = function(tt) { return thisProperty.valueAtTime(tt); };
  var velocityAtTime = function(tt) { return thisProperty.velocityAtTime(tt); };
  var loopOut = function(type, n) { return JSON.parse(__loop('out', type || 'cycle', n || 0, time)); };
  var loopIn = function(type, n) { return JSON.parse(__loop('in', type || 'cycle', n || 0, time)); };
  var loopOutDuration = function(type, d) { return JSON.parse(__loop('out', type || 'cycle', 0, time)); };
  var loopInDuration = function(type, d) { return JSON.parse(__loop('in', type || 'cycle', 0, time)); };
  var numKeys = thisProperty.numKeys;
  var key = function(i) { return thisProperty.key(i); };
  var nearestKey = function(tt) { return thisProperty.nearestKey(tt); };
  var timeToFrames = function(tt, fps) { return Math.floor((tt === undefined ? time : tt) * (fps || 1 / fd)); };
  var framesToTime = function(fr, fps) { return fr / (fps || 1 / fd); };
  var __scope = new Proxy({}, {
    has: function(tg, n) { return Object.prototype.hasOwnProperty.call(__layerNames, n); },
    get: function(tg, n) { if (n === Symbol.unscopables) return undefined; return thisLayer[n]; }
  });
  var __res;
  with (__scope) { __res = eval(__codes[id]); }
  if (__res && __res.__ref) __res = __res.toJSON();
  if (__res === undefined) __res = value;
  return JSON.stringify(__res);
}
"""


def _noise1(x: float, seed: int) -> float:
    """Smooth value noise in [-1, 1] (deterministic per seed)."""

    def h(i: int) -> float:
        n = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
        n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
        return ((n ^ (n >> 16)) & 0xFFFF) / 32767.5 - 1.0

    i = math.floor(x)
    f = x - i
    a, b, c, d = h(i - 1), h(i), h(i + 1), h(i + 2)
    # Catmull-Rom for smoothness
    return 0.5 * ((2 * b) + (-a + c) * f + (2 * a - 5 * b + 4 * c - d) * f * f + (-a + 3 * b - 3 * c + d) * f * f * f)


_ELSE_RE = re.compile(r"(?<=[^\s;{}])([ \t]+)else\b")


def legacy_fixups(src: str) -> str:
    """Tolerate legacy ExtendScript syntax AE accepts, e.g. `if (a) 100 else 0` (no `;` before else)."""
    try:
        import esprima

        esprima.parseScript(src, tolerant=False)
        return src
    except Exception:
        pass
    return _ELSE_RE.sub(lambda m: ";" + m.group(1) + "else", src)


class Evaluator:
    """Evaluates property values at a given comp time."""

    def __init__(self, mogrt: Mogrt, values: dict[str, Any] | None = None):
        self.mogrt = mogrt
        self.overrides: dict[int, Any] = {}
        self._cache: dict[tuple[int, float], Any] = {}
        self._objs: dict[int, Any] = {}
        self._codes: dict[int, int] = {}
        self._failed: set[int] = set()
        self.errors: dict[int, tuple] = {}
        self._stack: list[tuple[Any, float]] = []
        self.source_rect_fn: Callable[[Any, float, bool], dict] | None = None
        self.js = quickjs.Context()
        self.js.set_max_stack_size(4 * 1024 * 1024)
        self.js.add_callable("__get", self._safe(self._js_get))
        self.js.add_callable("__call", self._safe(self._js_call))
        self.js.add_callable("__callm", self._safe(self._js_callm))
        self.js.add_callable("__wiggle", self._safe(self._js_wiggle))
        self.js.add_callable("__loop", self._safe(self._js_loop))
        self.js.add_callable("__posterize", self._safe(self._js_posterize))
        self.js.eval(_PRELUDE)
        self.js.eval(_BIN_PRELUDE)
        self._comp_of_layer: dict[int, Any] = {}
        for comp in mogrt.project.compositions:
            for layer in comp.layers:
                self._comp_of_layer[id(layer)] = comp
        self.apply_controls(values or {})

    @staticmethod
    def _safe(fn):
        """Python exceptions must not cross into QuickJS (they would not be catchable there)."""

        def inner(*args):
            try:
                return fn(*args)
            except Exception as e:  # becomes a JS Error via __wrap
                return json.dumps({"t": "x", "m": f"{type(e).__name__}: {e}"})

        return inner

    # ------------------------------------------------------------------ overrides
    def _resolve_controller(self, ctl: Any) -> Any | None:
        if ctl.source_layer_id is None:
            return None
        comp = next((c for c in self.mogrt.project.compositions if str(c.id) == str(ctl.source_comp_id)), self.mogrt.main_comp)
        layer = next((l for l in comp.layers if str(l.id) == str(ctl.source_layer_id)), None)
        if layer is None:
            return None
        node = layer
        for ref in ctl.source_property_path:
            nxt = None
            if ref.prop_index is not None:
                # indexed groups (effects, masks, shape contents) hold several children with the
                # same match name; the index is the 0-based position (verify the match name)
                kids = list(node)
                for idx in (ref.prop_index, ref.prop_index - 1):
                    if 0 <= idx < len(kids) and kids[idx].match_name == ref.match_name:
                        nxt = kids[idx]
                        break
            if nxt is None:
                try:
                    nxt = node.property(ref.match_name)
                except Exception:
                    nxt = None
            if nxt is None:
                return None
            node = nxt
        return node

    def apply_controls(self, values: dict[str, Any]) -> None:
        """Set Essential Graphics values (keyed by control name or id); unspecified ones use defaults."""
        self.overrides.clear()
        self._cache.clear()
        ctrls = self.mogrt.controllers()
        for c in self.mogrt.controls:
            if not c.editable:
                continue
            val = values.get(c.id, values.get(c.name, c.default))
            ctl = ctrls.get(c.id)
            if ctl is None:
                continue
            prop = self._resolve_controller(ctl)
            if prop is None:
                print(f"WARNUNG: Ziel für Regler '{c.name}' nicht gefunden", file=sys.stderr)
                continue
            if c.type == "text":
                val = str(val).replace("\r\n", "\n").replace("\n", "\r")
            elif c.type in ("point", "scale", "point3d") and isinstance(val, (list, tuple)):
                cur = prop.value
                if isinstance(cur, list):
                    val = list(val[: len(cur)]) + list(cur[len(val):])
            elif c.type == "checkbox":
                val = 1.0 if val else 0.0
            elif c.type == "color" and isinstance(val, (list, tuple)):
                val = list(val) + [1.0] * (4 - len(val))
            self.overrides[id(prop)] = (c.type, val)

    # ------------------------------------------------------------------ values
    def raw(self, prop: Any, t: float) -> Any:
        """Keyframed/static value (no expression), with Essential Graphics override."""
        ov = self.overrides.get(id(prop))
        if ov is not None:
            ctype, val = ov
            if ctype == "text":
                base = prop.value_at_time(t) if prop.keyframes else prop.value
                return TextValue(val, base)
            return val
        if prop.keyframes:
            v = prop.value_at_time(t)
        else:
            v = prop.value
        if type(v).__name__ == "TextDocument":
            return TextValue(v.text, v)
        return v

    def value(self, prop: Any, t: float) -> Any:
        key = (id(prop), round(t, 6))
        if key in self._cache:
            return self._cache[key]
        v = self.raw(prop, t)
        if (
            id(prop) not in self.overrides
            and getattr(prop, "expression_enabled", False)
            and prop.expression
            and id(prop) not in self._failed
        ):
            if any(p is prop and abs(pt - t) < 1e-9 for p, pt in self._stack):
                return v  # cycle guard
            self._stack.append((prop, t))
            try:
                v = self._run_expression(prop, t, v)
            except Exception as e:  # expression failure -> pre-expression value
                self._failed.add(id(prop))
                self.errors[id(prop)] = (prop, str(e))
                layer = owning_layer(prop)
                print(
                    f"WARNUNG: Expression auf '{getattr(layer, 'name', '?')}' / '{prop.name}' fehlgeschlagen: {e}",
                    file=sys.stderr,
                )
            finally:
                self._stack.pop()
        self._cache[key] = v
        return v

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------ expressions
    def _ref(self, obj: Any, kind: str) -> dict:
        self._objs[id(obj)] = obj
        return {"h": id(obj), "k": kind}

    def _to_js(self, v: Any) -> Any:
        if isinstance(v, TextValue):
            return v.text.replace("\r", "\r")
        if type(v).__name__ == "Shape":
            return {"vertices": v.vertices, "inTangents": v.in_tangents, "outTangents": v.out_tangents, "closed": v.closed}
        if hasattr(v, "tolist"):
            return v.tolist()
        if isinstance(v, tuple):
            return list(v)
        if isinstance(v, dict):
            return {k: self._to_js(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self._to_js(x) for x in v]
        if isinstance(v, (int, float, str, bool)) or v is None:
            return v
        return None

    def _from_js(self, prop: Any, res: Any, pre: Any) -> Any:
        if isinstance(pre, TextValue):
            if isinstance(res, str):
                return TextValue(res, pre.doc)
            if isinstance(res, dict) and "text" in res:
                return TextValue(str(res["text"]), pre.doc)
            return TextValue(str(res), pre.doc)
        if isinstance(pre, list) and isinstance(res, (int, float)):
            return [float(res)] * len(pre)
        if isinstance(pre, list) and isinstance(res, list):
            out = [float(x) if isinstance(x, (int, float)) else 0.0 for x in res]
            if len(out) < len(pre):
                out += pre[len(out):]
            return out[: len(pre)] if len(pre) else out
        if isinstance(pre, (int, float)) and isinstance(res, list):
            return float(res[0]) if res else pre
        if isinstance(res, bool):
            return float(res)
        if isinstance(res, (int, float)):
            return float(res)
        return pre

    def _run_expression(self, prop: Any, t: float, pre: Any) -> Any:
        code_id = self._codes.get(id(prop))
        if code_id is None:
            code_id = len(self._codes) + 1
            self._codes[id(prop)] = code_id
            src = prop.expression.replace("\r\n", "\n").replace("\r", "\n")
            self.js.eval(f"__codes[{code_id}] = {json.dumps(transpile(legacy_fixups(src)))};")
        layer = owning_layer(prop)
        comp = self._comp_of_layer.get(id(layer), self.mogrt.main_comp)
        res = self.js.eval(
            "__run({}, {!r}, {}, {}, {}, {}, {!r})".format(
                code_id,
                float(t),
                json.dumps(json.dumps(self._to_js(pre))),
                json.dumps(json.dumps(self._ref(layer, "layer"))),
                json.dumps(json.dumps(self._ref(comp, "comp"))),
                json.dumps(json.dumps(self._ref(prop, "prop"))),
                float(comp.frame_duration),
            )
        )
        return self._from_js(prop, json.loads(res), pre)

    @property
    def _now(self) -> float:
        return self._stack[-1][1] if self._stack else 0.0

    def _val(self, v: Any) -> str:
        return json.dumps({"t": "v", "v": self._to_js(v)})

    def _js_get(self, ref_json: str, name: str) -> str:
        ref = json.loads(ref_json)
        obj = self._objs.get(ref["h"])
        kind = ref.get("k")
        t = self._now
        if kind == "comp":
            if name == "layer":
                return json.dumps({"t": "m", "r": ref, "n": "layer"})
            simple = {
                "numLayers": len(obj.layers),
                "width": obj.width,
                "height": obj.height,
                "duration": obj.duration,
                "frameDuration": obj.frame_duration,
                "name": obj.name,
                "pixelAspect": obj.pixel_aspect,
                "displayStartTime": 0,
            }
            if name in simple:
                return self._val(simple[name])
            return self._val(None)
        if kind == "layer":
            if name in ("effect", "content", "mask", "sourceRectAtTime", "toComp", "fromComp", "toWorld", "fromWorld"):
                return json.dumps({"t": "m", "r": ref, "n": name})
            simple = {
                "name": lambda: obj.name,
                "index": lambda: obj.index + 1,
                "inPoint": lambda: obj.in_point,
                "outPoint": lambda: obj.out_point,
                "startTime": lambda: obj.start_time,
                "width": lambda: getattr(obj, "width", 0),
                "height": lambda: getattr(obj, "height", 0),
                "hasParent": lambda: obj.parent is not None,
                "enabled": lambda: obj.enabled,
                "active": lambda: obj.enabled and obj.in_point <= t < obj.out_point,
            }
            if name in simple:
                return self._val(simple[name]())
            if name == "parent":
                return json.dumps({"t": "r", "r": self._ref(obj.parent, "layer")}) if obj.parent else self._val(None)
            if name == "transform":
                return json.dumps({"t": "r", "r": self._ref(obj.transform, "group")})
            if name == "text":
                return json.dumps({"t": "r", "r": self._ref(obj.property("ADBE Text Properties"), "group")})
            if name in ("position", "scale", "rotation", "anchorPoint", "opacity"):
                p = find_child(obj.transform, name)
                return self._val(self.value(p, t)) if p is not None else self._val(None)
            if name == "timeRemap":
                p = obj.property("ADBE Time Remapping")
                return json.dumps({"t": "r", "r": self._ref(p, "prop")})
            if name == "marker":
                p = obj.property("ADBE Marker")
                return json.dumps({"t": "r", "r": self._ref(p, "prop")}) if p is not None else self._val(None)
            p = find_child(obj, name)
            if p is not None:
                return self._node(p, t)
            return self._val(None)
        if kind == "group":
            if name in ("content", "effect", "property", "propertyGroup"):
                return json.dumps({"t": "m", "r": ref, "n": name})
            if name == "name":
                return self._val(obj.name)
            if name == "propertyIndex":
                return self._val(self._prop_index(obj))
            if name == "numProperties":
                return self._val(len(list(obj)))
            p = find_child(obj, name)
            if p is not None:
                return self._node(p, t)
            return self._val(None)
        if kind == "prop":
            if name == "value":
                return self._val(self.value(obj, t))
            if name in ("valueAtTime", "velocityAtTime", "key", "nearestKey", "speedAtTime", "propertyGroup"):
                return json.dumps({"t": "m", "r": ref, "n": name})
            if name == "propertyIndex":
                return self._val(self._prop_index(obj))
            if name == "numKeys":
                return self._val(len(obj.keyframes))
            if name == "name":
                return self._val(obj.name)
            if name == "text":  # sourceText.text
                v = self.value(obj, t)
                return self._val(str(v))
            # attributes of text documents
            v = self.value(obj, t)
            if isinstance(v, TextValue):
                doc = v.doc
                attrs = {
                    "fontSize": lambda: doc.font_size,
                    "font": lambda: doc.font,
                    "fillColor": lambda: list(doc.fill_color),
                    "tracking": lambda: doc.tracking,
                    "leading": lambda: doc.leading,
                    "length": lambda: len(v.text),
                }
                if name in attrs:
                    return self._val(attrs[name]())
            if isinstance(v, list) and name == "length":
                return self._val(len(v))
            return self._val(None)
        return self._val(None)

    @staticmethod
    def _prop_index(obj: Any) -> int:
        par = obj.parent_property
        if par is None:
            return 1
        for i, k in enumerate(par, 1):
            if k is obj:
                return i
        return 1

    def _node(self, p: Any, t: float) -> str:
        if is_group(p):
            return json.dumps({"t": "r", "r": self._ref(p, "group")})
        if p.match_name == "ADBE Layer Control-0001":
            layer = owning_layer(p)
            comp = self._comp_of_layer.get(id(layer), self.mogrt.main_comp)
            idx = int(self.value(p, t) or 0)
            target = comp.layers[idx - 1] if 1 <= idx <= len(comp.layers) else None
            if target is None:
                return self._val(None)
            return json.dumps({"t": "r", "r": self._ref(target, "layer")})
        # return property values directly; AE code mostly uses them as values
        return self._val(self.value(p, t))

    def _js_call(self, ref_json: str, args_json: str) -> str:
        """Calling a group: effect("X")("Param") / effect("X")(1)."""
        ref = json.loads(ref_json)
        obj = self._objs.get(ref["h"])
        args = json.loads(args_json)
        if ref.get("k") == "group" and args:
            p = find_child(obj, args[0])
            if p is None and obj.parent_property is not None and obj.parent_property.match_name == "ADBE Effect Parade":
                # parameter names may be localized (e.g. "Kontrollkästchen"); a single-parameter
                # effect (expression controls) is unambiguous
                params = [k for k in obj if not is_group(k)]
                if len(params) == 1:
                    p = params[0]
            if p is not None:
                return self._node(p, self._now)
        if ref.get("k") == "prop":
            return self._val(self.value(obj, self._now))
        if ref.get("k") == "layer" and args:
            p = find_child(obj, args[0])
            if p is not None:
                return self._node(p, self._now)
        if ref.get("k") == "comp" and args:  # thisComp(…) is not valid AE, but be lenient
            return self._val(None)
        raise ValueError(f"Aufruf mit {args!r} nicht möglich")

    def _js_callm(self, ref_json: str, name: str, args_json: str) -> str:
        ref = json.loads(ref_json)
        obj = self._objs.get(ref["h"])
        args = json.loads(args_json)
        t = self._now
        if name == "comp":
            c = next((c for c in self.mogrt.project.compositions if c.name == args[0]), None)
            return json.dumps({"t": "r", "r": self._ref(c, "comp")}) if c else self._val(None)
        if name == "layer":
            a = args[0]
            layer = None
            if isinstance(a, (int, float)):
                i = int(a) - 1
                if 0 <= i < len(obj.layers):
                    layer = obj.layers[i]
            else:
                layer = next((l for l in obj.layers if l.name == a), None)
            if layer is None:
                raise ValueError(f"Ebene {a!r} nicht gefunden")
            return json.dumps({"t": "r", "r": self._ref(layer, "layer")})
        if name == "effect":
            parade = obj.property("ADBE Effect Parade")
            p = find_child(parade, args[0])
            if p is None:
                raise ValueError(f"Effekt {args[0]!r} nicht gefunden")
            return json.dumps({"t": "r", "r": self._ref(p, "group")})
        if name == "content":
            base = obj.property("ADBE Root Vectors Group") if ref.get("k") == "layer" else obj
            p = find_child(base, args[0])
            if p is None:
                raise ValueError(f"Inhalt {args[0]!r} nicht gefunden")
            return self._node(p, t)
        if name == "mask":
            p = find_child(obj.property("ADBE Mask Parade"), args[0])
            return json.dumps({"t": "r", "r": self._ref(p, "group")}) if p else self._val(None)
        if name == "property":
            p = find_child(obj, args[0])
            return self._node(p, t) if p is not None else self._val(None)
        if name == "propertyGroup":
            n = int(args[0]) if args else 1
            node = obj
            for _ in range(max(n, 1)):
                node = node.parent_property
                if node is None:
                    raise ValueError("propertyGroup: keine übergeordnete Gruppe")
            kind = "layer" if owning_layer(node) is node else "group"
            return json.dumps({"t": "r", "r": self._ref(node, kind)})
        if name == "sourceRectAtTime":
            tt = args[0] if args and args[0] is not None else t
            ext = bool(args[1]) if len(args) > 1 else False
            if self.source_rect_fn is None:
                raise RuntimeError("sourceRectAtTime nicht verfügbar")
            return self._val(self.source_rect_fn(obj, float(tt), ext))
        if name in ("toComp", "toWorld", "fromComp", "fromWorld"):
            return self._val(args[0])  # approximation
        if name == "valueAtTime":
            return self._val(self.value(obj, float(args[0])))
        if name in ("velocityAtTime", "speedAtTime"):
            tt = float(args[0])
            a = self.value(obj, tt - 0.001)
            b = self.value(obj, tt + 0.001)
            if isinstance(a, list):
                vel = [(y - x) / 0.002 for x, y in zip(a, b)]
                return self._val(math.sqrt(sum(v * v for v in vel)) if name == "speedAtTime" else vel)
            return self._val((b - a) / 0.002)
        if name == "key":
            k = obj.keyframes[int(args[0]) - 1]
            return self._val(self._key(k, int(args[0])))
        if name == "nearestKey":
            ks = obj.keyframes
            if not ks:
                return self._val(None)
            i, k = min(enumerate(ks), key=lambda e: abs(e[1].time - float(args[0])))
            return self._val(self._key(k, i + 1))
        return self._val(None)

    def _key(self, k: Any, index: int) -> dict:
        out = {"time": k.time, "value": self._to_js(k.value), "index": index}
        v = k.value
        if type(v).__name__ == "MarkerValue":  # marker.key(i).comment, .duration, .parameters …
            out.update({"comment": v.comment or "", "duration": v.duration or 0, "chapter": v.chapter or "",
                        "url": v.url or "", "frameTarget": v.frame_target or "", "cuePointName": v.cue_point_name or "",
                        "parameters": dict(v.params or {})})
        return out

    def _js_posterize(self, t: float) -> str:
        """posterizeTime(): the rest of the expression (other properties too) runs at time t."""
        prop, _ = self._stack[-1]
        self._stack[-1] = (prop, float(t))
        return json.dumps({"v": self._to_js(self.raw(prop, float(t)))})

    def _js_wiggle(self, freq: float, amp: float, octaves: float, mult: float, t: float) -> str:
        prop, now = self._stack[-1]
        base = self.raw(prop, t)
        layer = owning_layer(prop)
        seed = (getattr(layer, "index", 0) + 1) * 7919 + len(prop.match_name)

        def w(dim: int) -> float:
            total, a, f = 0.0, 1.0, float(freq)
            for o in range(max(1, int(octaves))):
                total += a * _noise1(t * f, seed + dim * 101 + o * 13)
                a *= mult
                f *= 2
            return total * amp

        if isinstance(base, list):
            dims = len(base)
            if prop.match_name in ("ADBE Position", "ADBE Anchor Point", "ADBE Scale") and not getattr(layer, "three_d_layer", False):
                dims = min(dims, 2)
            return json.dumps([v + (w(i) if i < dims else 0) for i, v in enumerate(base)])
        return json.dumps(base + w(0))

    def _js_loop(self, direction: str, kind: str, n: float, t: float) -> str:
        prop, _ = self._stack[-1]
        ks = prop.keyframes
        if len(ks) < 2:
            return json.dumps(self._to_js(self.raw(prop, t)))
        first, last = ks[0].time, ks[-1].time
        if direction == "out" and t > last:
            start = ks[-1 - int(n)].time if n and int(n) < len(ks) else first
            dur = last - start
            if kind in ("cycle", "pingpong") and dur > 0:
                k = (t - start) / dur
                ph = (t - start) % dur
                if kind == "pingpong" and int(k) % 2 == 1:
                    ph = dur - ph
                return json.dumps(self._to_js(self.raw(prop, start + ph)))
            if kind in ("offset", "continue"):
                a, b = self.raw(prop, start), self.raw(prop, last)
                if kind == "offset" and dur > 0:
                    cyc = int((t - start) // dur)
                    ph = (t - start) % dur
                    v = self.raw(prop, start + ph)
                    if isinstance(v, list):
                        return json.dumps([x + cyc * (bb - aa) for x, aa, bb in zip(v, a, b)])
                    return json.dumps(v + cyc * (b - a))
        if direction == "in" and t < first:
            end = ks[int(n)].time if n and int(n) < len(ks) else last
            dur = end - first
            if dur > 0:
                ph = (t - first) % dur
                return json.dumps(self._to_js(self.raw(prop, first + ph)))
        return json.dumps(self._to_js(self.raw(prop, t)))
