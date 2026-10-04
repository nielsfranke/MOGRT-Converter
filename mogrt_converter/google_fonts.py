"""Fetch missing fonts from the Google Fonts repository (OFL / Apache / UFL licensed).

Fonts are looked up by PostScript name (as stored in AE text documents, e.g. "Montserrat-Black").
Static font files are used directly; for variable fonts the matching named instance is
instantiated with fontTools and saved as a static TTF in the user font folder.
"""

from __future__ import annotations

import io
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont

from . import fonts as fontmod
from .paths import user_fonts_dir

RAW = "https://raw.githubusercontent.com/google/fonts/main"
LICENSE_DIRS = ("ofl", "apache", "ufl")
_UA = {"User-Agent": "MOGRT-Converter (+https://github.com/nielsfranke/MOGRT-Converter)"}


def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30) as r:
            return r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def _family_dir(ps_name: str) -> str:
    family = ps_name.split("-")[0]
    return re.sub(r"[^a-z0-9]", "", family.lower())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_meta_cache: dict[str, tuple[str, str] | None] = {}


def _metadata(dirname: str) -> tuple[str, str] | None:
    """(license dir, METADATA.pb text) for a family directory name."""
    if dirname in _meta_cache:
        return _meta_cache[dirname]
    for lic in LICENSE_DIRS:
        data = _get(f"{RAW}/{lic}/{dirname}/METADATA.pb")
        if data:
            _meta_cache[dirname] = (lic, data.decode("utf-8", "replace"))
            return _meta_cache[dirname]
    _meta_cache[dirname] = None
    return None


def _font_entries(meta: str) -> list[dict]:
    out = []
    for block in re.findall(r"fonts \{(.*?)\n\}", meta, re.S):
        entry = dict(re.findall(r'(\w+): "?([^"\n]*)"?', block))
        out.append(entry)
    return out


def _instance_for(font: TTFont, ps_name: str) -> dict | None:
    """Axis coordinates of the named instance matching ps_name (variable fonts)."""
    if "fvar" not in font:
        return None
    name = font["name"]
    want = _norm(ps_name)
    style = _norm(ps_name.split("-", 1)[1]) if "-" in ps_name else "regular"
    family = _norm(ps_name.split("-")[0])
    for inst in font["fvar"].instances:
        ps = name.getDebugName(inst.postscriptNameID) if inst.postscriptNameID not in (None, 0xFFFF) else None
        sub = name.getDebugName(inst.subfamilyNameID) or ""
        if (ps and _norm(ps) == want) or _norm(sub) == style or _norm(family + sub) == want:
            return dict(inst.coordinates)
    return None


def _save_static(font_bytes: bytes, ps_name: str, coords: dict | None) -> Path:
    font = TTFont(io.BytesIO(font_bytes))
    if coords is not None:
        from fontTools.varLib.instancer import instantiateVariableFont

        font = instantiateVariableFont(font, coords, updateFontNames=True)
    # make the PostScript name match what After Effects asked for
    for rec in list(font["name"].names):
        if rec.nameID == 6:
            rec.string = ps_name
    out = user_fonts_dir() / f"{ps_name}.ttf"
    font.save(str(out))
    return out


def download(ps_name: str) -> Path | None:
    """Download one font by PostScript name. Returns the saved file or None."""
    meta = _metadata(_family_dir(ps_name))
    if meta is None:
        return None
    lic, text = meta
    dirname = _family_dir(ps_name)
    entries = _font_entries(text)
    # 1) static file with exactly this PostScript name ("Damion" means "Damion-Regular")
    wanted = {ps_name} if "-" in ps_name else {ps_name, f"{ps_name}-Regular"}
    for e in entries:
        if e.get("post_script_name") in wanted and "[" not in e.get("filename", ""):
            data = _get(f"{RAW}/{lic}/{dirname}/{e['filename']}")
            if data:
                return _save_static(data, ps_name, None)
    # 2) variable font: find the named instance
    italic = "italic" in ps_name.lower()
    files = sorted({e["filename"] for e in entries if "[" in e.get("filename", "")},
                   key=lambda f: ("italic" in f.lower()) != italic)
    for fname in files:
        data = _get(f"{RAW}/{lic}/{dirname}/{fname}")
        if not data:
            continue
        coords = _instance_for(TTFont(io.BytesIO(data)), ps_name)
        if coords is not None:
            return _save_static(data, ps_name, coords)
    return None


def download_missing(ps_names: list[str]) -> dict[str, str | None]:
    """Download all fonts that are not installed. Returns {name: saved path or None}."""
    result: dict[str, str | None] = {}
    for ps in ps_names:
        if fontmod.is_available(ps):
            continue
        try:
            path = download(ps)
        except Exception as e:  # network/parse problems must not break rendering
            print(f"WARNUNG: Font {ps} konnte nicht geladen werden: {e}", file=sys.stderr)
            path = None
        result[ps] = str(path) if path else None
    if any(result.values()):
        refresh()
    return result


def refresh() -> None:
    """Forget cached font lookups after installing fonts."""
    fontmod._index.cache_clear()
    fontmod._warned.clear()
    from .render import text

    text.font_data.cache_clear()
