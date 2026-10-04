"""Batch rendering: one output file per table row (CSV from Excel/Numbers/LibreOffice).

Columns are the template's control names. Optional extra columns:
  Dateiname (filename)  – output file name (without extension)
  Dauer (duration)      – new duration (seconds, timecode or frames)
Empty cells keep the template default.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .mogrt import Control, Mogrt

FILENAME_COLS = ("dateiname", "filename", "datei", "file", "name der datei")
DURATION_COLS = ("dauer", "duration", "länge", "laenge")
EDITABLE = ("text", "slider", "angle", "checkbox", "color", "point", "point3d", "scale", "dropdown")


def columns(m: Mogrt) -> list[tuple[str, Control]]:
    """(column header, control) for every editable control; duplicate names get ' (2)' etc."""
    out, seen = [], {}
    for c in m.controls:
        if c.type not in EDITABLE:
            continue
        n = seen.get(c.name, 0) + 1
        seen[c.name] = n
        out.append((c.name if n == 1 else f"{c.name} ({n})", c))
    return out


def format_value(c: Control, v: Any) -> str:
    if c.type == "checkbox":
        return "ja" if v else "nein"
    if c.type == "color" and isinstance(v, (list, tuple)):
        return "#" + "".join(f"{int(round(max(0, min(1, x)) * 255)):02X}" for x in v[:3])
    if c.type in ("point", "point3d", "scale") and isinstance(v, (list, tuple)):
        vals = v[:1] if c.type == "scale" else v
        return " ".join(f"{x:g}" for x in vals)
    if isinstance(v, float):
        return f"{v:g}"
    return "" if v is None else str(v)


def parse_value(c: Control, raw: str) -> Any:
    """Typed control value from a cell / command-line string."""
    s = raw.strip() if c.type != "text" else raw
    if c.type == "text":
        return s.replace("\r\n", "\n").replace("\\n", "\n")
    if c.type == "checkbox":
        return s.lower() in ("1", "true", "ja", "yes", "on", "x", "wahr", "an")
    if c.type in ("slider", "angle"):
        return float(s.replace(",", ".")) if s else c.default
    if c.type == "color":
        if s.startswith("#") and len(s) in (4, 7):
            h = s[1:]
            if len(h) == 3:
                h = "".join(ch * 2 for ch in h)
            return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]
        nums = [float(x) for x in re.split(r"[\s;,/]+", s) if x]
        if nums and max(nums) > 1:
            nums = [x / 255 for x in nums]
        return (nums + [1.0] * 4)[:4]
    if c.type in ("point", "point3d", "scale"):
        nums = [float(x.replace(",", ".")) for x in re.split(r"[\s;/x×|]+", s) if x]
        if not nums:
            return c.default
        if c.type == "scale" and len(nums) == 1:
            nums = [nums[0], nums[0]]
        return nums
    return s


@dataclass
class Row:
    index: int
    values: dict[str, Any] = field(default_factory=dict)  # control id -> value
    filename: str | None = None
    duration: str | None = None
    label: str = ""


@dataclass
class Table:
    rows: list[Row]
    unknown_columns: list[str]
    errors: list[str]


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_csv(data: bytes | str) -> list[dict[str, str]]:
    text = _decode(data) if isinstance(data, bytes) else data
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [{(k or "").strip(): (v if v is not None else "") for k, v in r.items()} for r in reader]


def build_rows(m: Mogrt, records: list[dict[str, str]]) -> Table:
    cols = columns(m)
    by_header = {h.strip().lower(): c for h, c in cols}
    rows, errors, unknown = [], [], set()
    for i, rec in enumerate(records, 1):
        if not any((v or "").strip() for v in rec.values()):
            continue  # empty line
        row = Row(index=i)
        for header, raw in rec.items():
            key = header.strip().lower()
            if not key:
                continue
            if key in FILENAME_COLS:
                row.filename = raw.strip() or None
                continue
            if key in DURATION_COLS:
                row.duration = raw.strip() or None
                continue
            c = by_header.get(key)
            if c is None:
                unknown.add(header)
                continue
            if raw is None or (raw.strip() == "" and c.type != "text") or raw == "":
                continue  # keep default
            try:
                row.values[c.id] = parse_value(c, raw)
            except ValueError:
                errors.append(f"Zeile {i}, Spalte „{header}“: ungültiger Wert {raw!r}")
        texts = [str(v).split("\n")[0] for cid, v in row.values.items()
                 if any(c.id == cid and c.type == "text" for _, c in cols)]
        row.label = texts[0] if texts else f"Zeile {i}"
        rows.append(row)
    return Table(rows, sorted(unknown), errors)


def template_csv(m: Mogrt, values: dict[str, Any] | None = None) -> str:
    """CSV skeleton (semicolon, for Excel) with headers and one example row."""
    values = values or {}
    cols = columns(m)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(["Dateiname"] + [h for h, _ in cols] + ["Dauer"])
    example = [format_value(c, values.get(c.id, values.get(c.name, c.default))) for _, c in cols]
    w.writerow([safe_filename(m.name)] + example + [f"{m.duration:g}"])
    return "﻿" + out.getvalue()  # BOM so Excel detects UTF-8 (umlauts)


def safe_filename(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:100] or "render"


def output_names(m: Mogrt, rows: list[Row]) -> list[str]:
    """Unique file names: explicit 'Dateiname', else template name + first text value."""
    names, used = [], set()
    for r in rows:
        base = safe_filename(r.filename) if r.filename else safe_filename(f"{m.name} - {r.label}")
        name, n = base, 2
        while name.lower() in used:
            name = f"{base} ({n})"
            n += 1
        used.add(name.lower())
        names.append(name)
    return names


def render_rows(m_path: str | Path, rows: list[Row], out_dir: Path, fmt: str = "prores4444",
                scale: float = 1.0, audio: bool = True, progress=None, motion: dict | None = None) -> list[Path]:
    """Render every row (sequentially). progress(row_index, frame, frames)."""
    from .mogrt import load
    from .output import collect_audio, render_video
    from .render.compositor import Renderer
    from .timing import parse_duration

    m = load(m_path)
    names = output_names(m, rows)
    results = []
    for k, (row, name) in enumerate(zip(rows, names)):
        r = Renderer(m, row.values, scale=scale)
        if row.duration:
            r.set_duration(parse_duration(row.duration, m.frame_rate))
        r.set_motion(motion)
        cb = (lambda i, n, k=k: progress(k, i, n)) if progress else None
        results.append(render_video(r, out_dir / name, fmt=fmt, audio=collect_audio(r) if audio else [], progress=cb))
    return results
