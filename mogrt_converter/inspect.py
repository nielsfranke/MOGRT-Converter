"""Human-readable dump of a MOGRT: controls, comp tree, layers, animated properties."""

from __future__ import annotations

from typing import Any, TextIO
import sys

from .mogrt import Mogrt

SKIP_GROUPS = {
    "ADBE Layer Styles",
    "ADBE Extrsn Options Group",
    "ADBE Plane Options Group",
    "ADBE Material Options Group",
    "ADBE Audio Group",
    "ADBE Vector Materials Group",
    "ADBE MTrackers",
    "ADBE Layer Sets",
    "ADBE Data Group",
    "ADBE Source Options Group",
    "ADBE Vector Stroke Taper",
    "ADBE Vector Stroke Wave",
}


def _is_group(p: Any) -> bool:
    return hasattr(p, "num_properties") and not hasattr(p, "keyframes")


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    cls = type(v).__name__
    if cls == "Shape":
        return f"<Shape {len(v.vertices)}pt {'closed' if v.closed else 'open'}>"
    if cls == "TextDocument":
        return f"<Text {v.text!r} {v.font} {v.font_size:g}px>"
    return str(v)


def dump_props(group: Any, depth: int, out: TextIO, verbose: bool) -> None:
    for p in group:
        ind = "    " * depth
        if _is_group(p):
            if p.match_name in SKIP_GROUPS:
                continue
            if not getattr(p, "enabled", True):
                out.write(f"{ind}+ {p.name} [{p.match_name}] (DISABLED)\n")
                continue
            out.write(f"{ind}+ {p.name} [{p.match_name}]\n")
            dump_props(p, depth + 1, out, verbose)
            continue
        try:
            kfs = p.keyframes
        except Exception:
            kfs = []
        expr = p.expression if getattr(p, "expression_enabled", False) else ""
        if not (kfs or expr or verbose or p.is_modified):
            continue
        try:
            val = _fmt(p.value)
        except Exception as e:  # pragma: no cover - diagnostic tool
            val = f"<{e}>"
        out.write(f"{ind}- {p.name} [{p.match_name}] = {val}\n")
        for k in kfs:
            out.write(
                f"{ind}      @{k.time:.3f}s {_fmt(k.value)}  {k.in_interpolation_type.name[:3]}/{k.out_interpolation_type.name[:3]}\n"
            )
        if expr:
            out.write(f"{ind}      EXPR: {expr!r}\n")


def dump(m: Mogrt, out: TextIO = sys.stdout, verbose: bool = False) -> None:
    out.write(f"MOGRT: {m.name}\n  {m.width}x{m.height} @ {m.frame_rate:g} fps, {m.duration:g}s\n  Fonts: {m.fonts}\n\n")
    ctrls = m.controllers()
    out.write("Regler:\n")
    for c in m.controls:
        target = ""
        ec = ctrls.get(c.id)
        if ec is not None and ec.source_layer_id is not None:
            path = "/".join(s.match_name for s in ec.source_property_path)
            target = f"  -> layer#{ec.source_layer_id} {path}"
        out.write(f"  [{c.type}] {c.name!r} = {c.default!r}{target}\n")
    seen: set[str] = set()

    def walk(comp: Any, depth: int) -> None:
        if comp.name in seen:
            out.write(f"\n{'  ' * depth}(COMP {comp.name} – siehe oben)\n")
            return
        seen.add(comp.name)
        out.write(f"\n######## COMP {comp.name} {comp.width}x{comp.height} {comp.frame_rate:g}fps {comp.duration:g}s\n")
        subs = []
        for l in comp.layers:
            src = getattr(l, "source", None)
            flags = []
            if not l.enabled:
                flags.append("HIDDEN")
            if getattr(l, "three_d_layer", False):
                flags.append("3D")
            if l.parent is not None:
                flags.append(f"parent={l.parent.name!r}")
            if getattr(l, "has_track_matte", False):
                flags.append(f"matte={l.track_matte_type.name}<-{getattr(l.track_matte_layer, 'name', '?')!r}")
            if getattr(l, "is_track_matte", False):
                flags.append("IS_MATTE")
            if getattr(l, "blending_mode", None) is not None and int(l.blending_mode) != 5212:
                flags.append(f"blend={l.blending_mode.name}")
            if getattr(l, "adjustment_layer", False):
                flags.append("ADJUSTMENT")
            if getattr(l, "collapse_transformation", False):
                flags.append("COLLAPSE")
            if getattr(l, "time_remap_enabled", False):
                flags.append("TIMEREMAP")
            if l.stretch not in (100, 100.0, None):
                flags.append(f"stretch={l.stretch}")
            out.write(
                f"\n  L{l.index} {l.name!r} {type(l).__name__} src={getattr(src, 'name', None)!r} "
                f"in={l.in_point:.3f} out={l.out_point:.3f} start={l.start_time:.3f} {' '.join(flags)}\n"
            )
            dump_props(l, 2, out, verbose)
            if src is not None and type(src).__name__ == "CompItem":
                subs.append(src)
        for s in subs:
            walk(s, depth + 1)

    walk(m.main_comp, 0)
