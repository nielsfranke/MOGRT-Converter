"""Resolve PostScript font names (as stored in AE text documents) to font files."""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont

from .mogrt import CACHE_DIR
from .paths import bundled_fonts_dir, user_fonts_dir

# where users drop missing fonts (shown in the app); bundled fonts are scanned too
PROJECT_FONTS = user_fonts_dir()
_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}


def font_dirs() -> list[Path]:
    dirs = [PROJECT_FONTS, bundled_fonts_dir()]
    extra = os.environ.get("MOGRT_FONT_DIRS")
    if extra:
        dirs += [Path(p) for p in extra.split(os.pathsep)]
    if sys.platform == "darwin":
        dirs += [Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    elif sys.platform == "win32":
        dirs += [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
    else:
        dirs += [Path("/usr/share/fonts"), Path.home() / ".fonts", Path.home() / ".local/share/fonts"]
    return [d for d in dirs if d.is_dir()]


def _scan() -> dict[str, list]:
    index: dict[str, list] = {}
    for d in font_dirs():
        for f in d.rglob("*"):
            if f.suffix.lower() not in _SUFFIXES:
                continue
            try:
                fonts = TTCollection(str(f), lazy=True).fonts if f.suffix.lower() in (".ttc", ".otc") else [TTFont(str(f), lazy=True)]
            except Exception:
                continue
            for i, font in enumerate(fonts):
                try:
                    ps = font["name"].getDebugName(6)
                except Exception:
                    ps = None
                if ps and ps not in index:
                    index[ps] = [str(f), i]
    return index


@lru_cache(maxsize=1)
def _index() -> dict[str, list]:
    cache = CACHE_DIR / "fontindex.json"
    stamp = [str(d) + ":" + str(int(d.stat().st_mtime)) for d in font_dirs()]
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            if data.get("stamp") == stamp:
                return data["fonts"]
        except Exception:
            pass
    fonts = _scan()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"stamp": stamp, "fonts": fonts}))
    return fonts


class MissingFont(LookupError):
    pass


_warned: set[str] = set()


def resolve(ps_name: str) -> tuple[str, int]:
    """Return (path, face index) for a PostScript name, with a fallback."""
    idx = _index()
    if ps_name in idx:
        return tuple(idx[ps_name])  # type: ignore[return-value]
    # loose match: ignore case / dashes
    key = ps_name.replace("-", "").lower()
    for name, val in idx.items():
        if name.replace("-", "").lower() == key:
            return tuple(val)  # type: ignore[return-value]
    if ps_name not in _warned:
        _warned.add(ps_name)
        print(f"WARNUNG: Font '{ps_name}' nicht gefunden – nutze Ersatzschrift. "
              f"Lege die Datei in {PROJECT_FONTS} ab.", file=sys.stderr)
    for fb in ("Helvetica", "ArialMT", "DejaVuSans"):
        if fb in idx:
            return tuple(idx[fb])  # type: ignore[return-value]
    return tuple(next(iter(idx.values())))  # type: ignore[return-value]


def is_available(ps_name: str) -> bool:
    return ps_name in _index()
