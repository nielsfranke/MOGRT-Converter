"""Loading .mogrt packages: unzip, read definition.json, locate the AE project."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import py_aep

from .paths import cache_dir

CACHE_DIR = cache_dir()

# clientControls[].type in definition.json
CONTROL_TYPES = {
    1: "checkbox",
    2: "slider",
    3: "angle",
    4: "color",
    5: "point",
    6: "text",
    7: "dropdown",
    8: "group",
    9: "scale",  # 2D scale, value [x, y, (ui)], "linked"
    10: "section",  # collapsible group; value = list of child control ids
    13: "media",
}


def _loc_str(obj: Any) -> str:
    """Pick a string out of an Adobe ``{"strDB": [{"localeString", "str"}]}`` block."""
    if not isinstance(obj, dict):
        return "" if obj is None else str(obj)
    db = obj.get("strDB") or []
    if not db:
        return ""
    for loc in ("de_DE", "en_US"):
        for e in db:
            if e.get("localeString") == loc:
                return e.get("str", "")
    return db[0].get("str", "")


@dataclass
class Control:
    id: str
    type: str
    name: str
    default: Any
    min: float | None = None
    max: float | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def editable(self) -> bool:
        return self.type not in ("group", "section")


@dataclass
class Mogrt:
    path: Path
    root: Path
    definition: dict
    controls: list[Control]
    aep_path: Path
    project: Any  # py_aep Project
    main_comp: Any  # py_aep CompItem

    @property
    def name(self) -> str:
        return _loc_str(self.definition.get("capsuleNameLocalized")) or self.definition.get("capsuleName", "")

    @property
    def source_info(self) -> dict:
        si = self.definition.get("sourceInfoLocalized", {})
        return si.get("en_US") or next(iter(si.values()), {})

    @property
    def fonts(self) -> list[str]:
        fl = self.definition.get("usedFontsLocalized", {})
        return fl.get("en_US") or next(iter(fl.values()), [])

    @property
    def width(self) -> int:
        return int(self.main_comp.width)

    @property
    def height(self) -> int:
        return int(self.main_comp.height)

    @property
    def frame_rate(self) -> float:
        return float(self.main_comp.frame_rate)

    @property
    def duration(self) -> float:
        return float(self.main_comp.duration)

    @property
    def protected_regions(self) -> list[tuple[float, float]]:
        from .timing import protected_regions

        return protected_regions(self.main_comp)

    def control(self, key: str) -> Control:
        for c in self.controls:
            if key in (c.id, c.name):
                return c
        raise KeyError(f"Kein Regler '{key}'. Verfügbar: {[c.name for c in self.controls]}")

    def controllers(self) -> dict[str, Any]:
        """Essential Graphics controllers of the main comp, keyed by uuid."""
        return {c.uuid: c for c in self.main_comp.motion_graphics_controllers}


def _parse_control(c: dict) -> Control:
    ctype = CONTROL_TYPES.get(c.get("type"), f"unknown{c.get('type')}")
    value = c.get("value")
    if ctype == "section":
        value = list(value) if isinstance(value, list) else []
    elif ctype in ("text", "group"):
        value = _loc_str(value).replace("\r", "\n")
        if ctype == "text":
            value = value.rstrip("\n")
    elif ctype == "checkbox":
        value = bool(value)
    elif ctype == "point" and isinstance(value, dict):
        value = [value.get("x", 0.0), value.get("y", 0.0)] + ([value["z"]] if "z" in value else [])
    elif ctype == "scale" and isinstance(value, list):
        value = list(value[: c.get("dimensions", 2)])
    elif ctype == "color" and isinstance(value, list):
        value = list(value[:4])
    return Control(
        id=c["id"],
        type=ctype,
        name=_loc_str(c.get("uiName")),
        default=value,
        min=c.get("min"),
        max=c.get("max"),
        raw=c,
    )


def _extract(path: Path) -> Path:
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    root = CACHE_DIR / f"{path.stem}-{digest}"
    if (root / ".done").exists():
        return root
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(root)
    inner = root / "project.aegraphic"
    if inner.exists():
        with zipfile.ZipFile(inner) as z:
            z.extractall(root / "aegraphic")
    (root / ".done").touch()
    return root


def _patch_py_aep_legacy_eg() -> None:
    """Essential Graphics in pre-2019 projects store the template name in LIST:CapS (not CpS2)."""
    from py_aep.binary.utils import ChunkNotFoundError, filter_by_list_type, filter_by_type, find_by_list_type, find_by_type
    from py_aep.models.items import composition
    from py_aep.parsers import essential_graphics as eg

    if getattr(composition, "_mogrt_patched", False):
        return
    original = eg.parse_essential_graphics

    def parse_with_legacy(child_chunks):
        try:
            return original(child_chunks)
        except ChunkNotFoundError:
            pass
        for list_type in ("CIF3", "CIF2", "CIFO"):
            try:
                cif = find_by_list_type(chunks=child_chunks, list_type=list_type)
                break
            except ChunkNotFoundError:
                cif = None
        if cif is None:
            return None
        caps = find_by_list_type(chunks=cif.chunks, list_type="CapS")
        name = filter_by_type(chunks=caps.chunks, chunk_type="Utf8")[0]
        ctls = [_legacy_controller(c) for c in filter_by_list_type(chunks=cif.chunks, list_type="CCtl")]
        return (name, ctls)

    def _legacy_controller(cctl):
        try:
            return eg._parse_controller(cctl)
        except ChunkNotFoundError:
            pass
        caps = find_by_list_type(chunks=cctl.chunks, list_type="CapS")
        name_utf8 = filter_by_type(chunks=caps.chunks, chunk_type="Utf8")[0]
        ctyp = find_by_type(chunks=cctl.chunks, chunk_type="CTyp")
        uuids = filter_by_type(chunks=cctl.chunks, chunk_type="Utf8")
        path, comp_id, layer_id = [], None, None
        try:
            cprp = find_by_list_type(chunks=cctl.chunks, list_type="CPrp")
        except ChunkNotFoundError:
            cprp = None
        if cprp is not None:
            utf = filter_by_type(chunks=cprp.chunks, chunk_type="Utf8")
            if utf:
                path = eg._parse_source_property_path(utf[0].value)
            ccid = filter_by_type(chunks=cprp.chunks, chunk_type="CCId")
            clid = filter_by_type(chunks=cprp.chunks, chunk_type="CLId")
            comp_id = ccid[0].value if ccid else None
            layer_id = clid[0].value if clid else None
        return eg.EssentialGraphicsController(
            _name_utf8=name_utf8, _ctyp=ctyp, uuid=uuids[0].value if uuids else "",
            source_property_path=path, source_comp_id=comp_id, source_layer_id=layer_id)

    composition.parse_essential_graphics = parse_with_legacy
    composition._mogrt_patched = True


_patch_py_aep_legacy_eg()

_TEMPLATE_RIFX = None


def _template_rifx():
    """Root chunks of an empty current-version project (defaults for missing global settings)."""
    global _TEMPLATE_RIFX
    if _TEMPLATE_RIFX is None:
        import tempfile

        from py_aep.binary.chunk import read_aep

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "skeleton.aep"
            py_aep.new().project.save(str(tmp))
            with open(tmp, "rb") as f:
                _TEMPLATE_RIFX, _ = read_aep(f, defer_list_types=frozenset({"Layr"}))
    return _TEMPLATE_RIFX


def _chunk_key(c) -> tuple:
    return (getattr(c, "chunk_type", None), getattr(c, "list_type", None))


def parse_aep(path: str | os.PathLike):
    """py_aep.parse with a fallback for projects from older After Effects versions.

    Old projects (e.g. AE 2017/2018) lack some project-level settings chunks that py_aep
    requires (working gamma, colour management, expression engine, …). Those are copied
    from an empty current-version project; layers, comps and keyframes are untouched.
    """
    from py_aep.binary.utils import ChunkNotFoundError

    try:
        return py_aep.parse(str(path))
    except ChunkNotFoundError:
        pass
    from py_aep.binary.chunk import read_aep
    from py_aep.parsers.application import parse_app
    from py_aep.parsers.project import parse_project

    with open(path, "rb") as f:
        rifx, xmp = read_aep(f, defer_list_types=frozenset({"Layr"}))
    tpl = _template_rifx().chunks
    have = {_chunk_key(c) for c in rifx.chunks}
    fold = next(i for i, c in enumerate(rifx.chunks) if _chunk_key(c) == ("LIST", "Fold"))
    insert = []
    for i, c in enumerate(tpl):
        k = _chunk_key(c)
        if k == ("LIST", "Fold"):
            break
        if k in have or k == ("Utf8", None):
            continue
        insert.append(c)
        # colour-profile chunks are followed by their Utf8 payload
        if k[0] in ("PwCs", "pdvc", "pcms") and i + 1 < len(tpl) and _chunk_key(tpl[i + 1]) == ("Utf8", None):
            insert.append(tpl[i + 1])
    rifx.chunks[fold:fold] = insert
    project = parse_project(rifx, xmp, str(path))
    return parse_app(rifx, project)


def load(path: str | os.PathLike) -> Mogrt:
    path = Path(path).expanduser().resolve()
    root = _extract(path)
    definition = json.loads((root / "definition.json").read_text(encoding="utf-8"))
    if definition.get("authorApp") not in (None, "aefx"):
        raise NotImplementedError(
            f"MOGRT wurde mit '{definition.get('authorApp')}' erstellt – bisher werden nur After-Effects-MOGRTs unterstützt."
        )
    aeps = sorted((root / "aegraphic").glob("*.aep"))
    if not aeps:
        raise FileNotFoundError("Kein After-Effects-Projekt in der MOGRT gefunden.")
    project = parse_aep(aeps[0]).project
    comp_name = definition.get("sourceInfoLocalized", {}).get("en_US", {}).get("name") or definition.get("capsuleName")
    comps = [c for c in project.compositions if c.name == comp_name]
    if not comps:
        # fall back to the comp that carries Essential Graphics controllers
        comps = [c for c in project.compositions if c.motion_graphics_template_controller_count]
    if not comps:
        raise LookupError(f"Hauptkomposition '{comp_name}' nicht gefunden.")
    controls = [_parse_control(c) for c in definition.get("clientControls", [])]
    return Mogrt(path, root, definition, controls, aeps[0], project, comps[0])
