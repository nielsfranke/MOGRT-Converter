"""The app's template library (config.json): folders, single files, collections, names.

Shared by the app and the command line, so `mogrt render "Lower third"` finds the
same templates the app shows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths

CONFIG_PATH = paths.data_dir() / "config.json"
LEGACY_CONFIG = Path.home() / ".config" / "mogrt-converter" / "config.json"


def load_config() -> dict:
    cfg = {
        "library": [str(paths.default_movies_dir() / "MOGRTs")],
        "output_dir": str(paths.default_movies_dir() / "MOGRT Renders"),
        "format": "prores4444",
    }
    src = CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG
    if src.exists():
        try:
            cfg.update(json.loads(src.read_text()))
        except Exception:
            pass
    for k, v in (("files", []), ("hidden", []), ("aliases", {}), ("collections", []), ("recent", {})):
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def items(cfg: dict) -> list[dict[str, Any]]:
    """All known templates. `folder` is the library folder, "" for single files, None if only in a collection."""
    hidden, aliases, recent = set(cfg["hidden"]), cfg["aliases"], cfg["recent"]
    out, seen = [], set()

    def add(f: Path, folder: str | None) -> None:
        key = str(f.resolve())
        if key in seen or key in hidden:
            return
        seen.add(key)
        out.append({"path": key, "name": aliases.get(key) or f.stem, "file": f.stem, "folder": folder,
                    "recent": recent.get(key, 0), "added": f.stat().st_mtime})

    for f in cfg["files"]:
        if Path(f).exists():
            add(Path(f), "")
    for d in cfg["library"]:
        root = Path(d).expanduser()
        if root.is_dir():
            for f in root.rglob("*.mogrt"):
                add(f, str(root))
    for c in cfg["collections"]:  # collection items whose folder is no longer in the library
        for f in c["items"]:
            if Path(f).exists():
                add(Path(f), None)
    for i in out:
        i["collections"] = [c["name"] for c in cfg["collections"] if i["path"] in c["items"]]
    out.sort(key=lambda i: i["name"].lower())
    return out


def resolve(ref: str, cfg: dict | None = None) -> Path:
    """A .mogrt path, or a template from the library by name (exact, else a unique part of the name)."""
    p = Path(ref).expanduser()
    if p.suffix.lower() == ".mogrt" or p.exists():
        if not p.exists():
            raise SystemExit(f"Datei nicht gefunden: {ref}")
        return p
    known = items(cfg or load_config())
    q = ref.strip().lower()
    for match in (lambda i: q in (i["name"].lower(), i["file"].lower()),
                  lambda i: q in i["name"].lower() or q in i["file"].lower()):
        hits = [i for i in known if match(i)]
        if len(hits) == 1:
            return Path(hits[0]["path"])
        if len(hits) > 1:
            raise SystemExit(f"„{ref}“ ist nicht eindeutig: " + "; ".join(i["name"] for i in hits))
    raise SystemExit(f"Keine Vorlage „{ref}“ in der Bibliothek (siehe mogrt list)")
