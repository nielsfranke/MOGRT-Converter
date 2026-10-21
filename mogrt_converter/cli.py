"""Command line: mogrt list | info | still | render | batch | inspect.

With --json the commands print one JSON object to stdout (for scripts and AI agents);
progress and warnings stay on stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import library
from .mogrt import load as _load_path


def load(ref: str):
    """Template by path or by its name in the app library."""
    return _load_path(library.resolve(ref))


class _Warnings:
    """Tee for stderr that remembers the renderer's WARNUNG lines (reported in --json output)."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._buf = ""
        self._err = sys.stderr

    def write(self, s: str) -> int:
        self._buf += s
        *done, self._buf = self._buf.split("\n")
        self.lines += [ln.removeprefix("WARNUNG: ").strip() for ln in done if ln.startswith("WARNUNG")]
        return self._err.write(s)

    def __getattr__(self, name: str):
        return getattr(self._err, name)

    def __enter__(self) -> "_Warnings":
        sys.stderr = self
        return self

    def __exit__(self, *exc) -> None:
        sys.stderr = self._err

    def unique(self) -> list[str]:
        return list(dict.fromkeys(self.lines))


def _emit(args: argparse.Namespace, data: dict, text: str | None = None) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif text is not None:
        print(text)


def _control_info(m) -> list[dict]:
    from .batch import columns, format_value

    header = {c.id: h for h, c in columns(m)}
    out = []
    for c in m.controls:
        d = {"name": c.name, "type": c.type, "default": format_value(c, c.default)}
        if c.id in header:
            d["column"] = header[c.id]  # CSV/JSON key for batch (unique even if names repeat)
        if c.min is not None and c.max is not None:
            d["min"], d["max"] = c.min, c.max
        out.append(d)
    return out


def _parse_values(args: argparse.Namespace, m) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if args.values:
        values.update(json.loads(Path(args.values).read_text(encoding="utf-8")))
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"--set erwartet NAME=WERT, bekam {item!r}")
        key, raw = item.split("=", 1)
        try:
            ctrl = m.control(key.strip())
        except KeyError as e:
            raise SystemExit(e.args[0]) from None
        from .batch import parse_value

        values[ctrl.id] = parse_value(ctrl, raw)
    return values


def _motion(args: argparse.Namespace, m) -> dict | None:
    """Premiere-style motion from --position/--offset/--motion-scale/--rotation/--opacity."""
    def pair(s: str) -> list[float]:
        v = [float(x) for x in s.replace(";", ",").replace(" ", ",").split(",") if x]
        if len(v) != 2:
            raise SystemExit(f"zwei Werte erwartet (X,Y), bekam {s!r}")
        return v

    pos = [m.width / 2, m.height / 2]
    if getattr(args, "position", None):
        pos = pair(args.position)
    if getattr(args, "offset", None):
        dx, dy = pair(args.offset)
        pos = [pos[0] + dx, pos[1] + dy]
    motion = {"position": pos, "scale": getattr(args, "motion_scale", None) or 100,
              "rotation": getattr(args, "rotation", None) or 0,
              "opacity": 100 if getattr(args, "opacity", None) is None else args.opacity}
    return motion


def _apply_duration(args: argparse.Namespace, r) -> None:
    if getattr(args, "duration", None):
        from .timing import parse_duration

        r.set_duration(parse_duration(args.duration, r.mogrt.frame_rate))
    r.set_motion(_motion(args, r.mogrt))


def cmd_batch(args: argparse.Namespace) -> None:
    from .batch import build_rows, output_names, read_csv, render_rows, template_csv

    path = library.resolve(args.mogrt)
    m = _load_path(path)
    if args.template:
        Path(args.template).write_text(template_csv(m), encoding="utf-8")
        print(f"CSV-Vorlage geschrieben: {args.template}")
        return
    if not args.csv:
        raise SystemExit("CSV-Datei angeben (oder --template DATEI für eine leere Vorlage)")
    table = build_rows(m, _read_rows(Path(args.csv)))
    for e in table.errors:
        print("FEHLER:", e, file=sys.stderr)
    if table.unknown_columns:
        print("Hinweis: unbekannte Spalten ignoriert:", ", ".join(table.unknown_columns), file=sys.stderr)
    if table.errors:
        if args.json:
            _emit(args, {"ok": False, "errors": table.errors, "unknown_columns": table.unknown_columns})
        raise SystemExit(1)
    out_dir = Path(args.output or ".")
    names = output_names(m, table.rows)
    print(f"{len(table.rows)} Zeile(n) → {out_dir}", file=sys.stderr if args.json else sys.stdout)
    t0 = time.time()

    def progress(k: int, i: int, n: int) -> None:
        if i == n or i % 10 == 0:
            print(f"\r  [{k + 1}/{len(table.rows)}] {names[k][:40]:40s} Frame {i}/{n}   ", end="", file=sys.stderr, flush=True)

    with _Warnings() as w:
        paths = render_rows(path, table.rows, out_dir, fmt=args.format, scale=args.scale,
                            audio=not args.no_audio, progress=progress, motion=_motion(args, m))
    print(f"\nFertig in {time.time() - t0:.0f}s", file=sys.stderr)
    _emit(args, {"ok": True, "template": str(path), "outputs": [str(p) for p in paths],
                 "unknown_columns": table.unknown_columns, "warnings": w.unique()},
          "\n".join(str(p) for p in paths))


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Rows from a CSV, or from JSON: a list of {column: value} objects (easier to write for scripts)."""
    from .batch import read_csv

    if path.suffix.lower() != ".json":
        return read_csv(path.read_bytes())
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows", [])
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise SystemExit("JSON-Datei muss eine Liste von Objekten {Spalte: Wert} enthalten")
    cell = lambda v: "ja" if v is True else "nein" if v is False else "" if v is None else str(v)
    return [{str(k): cell(v) for k, v in r.items()} for r in data]


def cmd_list(args: argparse.Namespace) -> None:
    cfg = library.load_config()
    found = library.items(cfg)
    if args.collection:
        q = args.collection.lower()
        found = [i for i in found if any(q in c.lower() for c in i["collections"])]
    if args.json:
        _emit(args, {"output_dir": cfg["output_dir"], "collections": [c["name"] for c in cfg["collections"]],
                     "templates": [{"name": i["name"], "path": i["path"], "collections": i["collections"]}
                                   for i in found]})
        return
    for i in found:
        cols = f"  [{', '.join(i['collections'])}]" if i["collections"] else ""
        print(f"{i['name']}{cols}\n    {i['path']}")
    if not found:
        print("Keine Vorlagen. Ordner oder Dateien in der App hinzufügen.")
    print(f"Render-Ordner der App: {cfg['output_dir']}")


def cmd_info(args: argparse.Namespace) -> None:
    from . import fonts

    m = load(args.mogrt)
    if args.json:
        _emit(args, {"name": m.name, "path": str(m.path), "width": m.width, "height": m.height,
                     "fps": m.frame_rate, "duration": m.duration,
                     "protected_regions": [[round(s, 3), round(e, 3)] for s, e in m.protected_regions],
                     "fonts": [{"name": f, "available": fonts.is_available(f)} for f in m.fonts],
                     "controls": _control_info(m)})
        return
    print(f"{m.name}\n  {m.width}x{m.height}, {m.frame_rate:g} fps, {m.duration:g} s")
    regs = m.protected_regions
    if regs:
        print("  Geschützte Bereiche (bleiben bei Dauer-Änderung unverändert): "
              + ", ".join(f"{s:.2f}–{e:.2f} s" for s, e in regs))
    else:
        print("  Keine geschützten Bereiche: bei Dauer-Änderung wird die ganze Animation gedehnt")
    print("  Fonts:")
    for f in m.fonts:
        print(f"    {'✓' if fonts.is_available(f) else '✗ FEHLT'} {f}")
    from .output import ffmpeg_bin
    from .render.effects import ghostscript_bin

    print(f"  ffmpeg: {ffmpeg_bin()}")
    print(f"  Ghostscript: {ghostscript_bin() or 'nicht gefunden (EPS-Footage nur als Vorschau)'}")
    print("  Regler:")
    for c in m.controls:
        if c.type == "group":
            print(f"    --- {c.name}: {c.default}")
            continue
        rng = f" ({c.min:g}–{c.max:g})" if c.min is not None and c.max is not None else ""
        print(f"    [{c.type}] {c.name!r} = {c.default!r}{rng}")


def cmd_fonts(args: argparse.Namespace) -> None:
    from . import fonts
    from .google_fonts import download_missing

    names: list[str] = []
    for f in args.mogrt:
        names += [n for n in load(f).fonts if n not in names]
    missing = [n for n in names if not fonts.is_available(n)]
    for n in names:
        print(f"  {'✓' if n not in missing else '✗'} {n}")
    if not missing:
        print("Alle Schriften vorhanden.")
        return
    if not args.download:
        print(f"{len(missing)} Schrift(en) fehlen. Mit --download werden freie Schriften von Google Fonts geladen.")
        return
    for name, path in download_missing(missing).items():
        print(f"  {name}: {'geladen' if path else 'nicht bei Google Fonts (kommerziell?) – bitte selbst installieren'}")
    print(f"Font-Ordner: {fonts.PROJECT_FONTS}")


def cmd_inspect(args: argparse.Namespace) -> None:
    from .inspect import dump

    dump(load(args.mogrt), verbose=args.verbose)


def cmd_still(args: argparse.Namespace) -> None:
    from .output import save_png
    from .render.compositor import Renderer

    m = load(args.mogrt)
    r = Renderer(m, _parse_values(args, m), scale=args.scale)
    _apply_duration(args, r)
    t = args.time if args.time is not None else r.duration / 2
    out = args.output or f"{m.path.stem}.{t:.2f}s.png"
    with _Warnings() as w:
        save_png(r.render_frame(t), out)
    _emit(args, {"ok": True, "output": str(Path(out).resolve()), "time": t, "duration": r.duration,
                 "warnings": w.unique()}, out)


def cmd_render(args: argparse.Namespace) -> None:
    from .output import collect_audio, render_video
    from .render.compositor import Renderer

    m = load(args.mogrt)
    values = _parse_values(args, m)
    r = Renderer(m, values, scale=args.scale)
    _apply_duration(args, r)
    out = args.output or m.path.stem
    bg = None
    if args.background:
        h = args.background.lstrip("#")
        bg = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    audio = [] if args.no_audio else collect_audio(r)
    t0 = time.time()

    def progress(i: int, n: int) -> None:
        if i == n or i % 5 == 0:
            el = time.time() - t0
            eta = el / i * (n - i)
            print(f"\r  Frame {i}/{n}  ({el:.0f}s, noch ~{eta:.0f}s)   ", end="", file=sys.stderr, flush=True)

    with _Warnings() as w:
        path = render_video(r, out, fmt=args.format, start=args.start, end=args.end, background=bg, audio=audio, progress=progress)
    print(f"\n{path}", file=sys.stderr)
    _emit(args, {"ok": True, "output": str(Path(path).resolve()), "duration": r.duration, "warnings": w.unique()},
          str(path))


def cmd_app(args: argparse.Namespace) -> None:
    from .app.server import serve

    serve(port=args.port, open_browser=not args.no_browser)


def cmd_install_resolve(args: argparse.Namespace) -> None:
    from .resolve_scripts import install

    out = args.output_dir or library.load_config()["output_dir"]
    for p in install(out):
        print(f"installiert: {p}")
    print("In Resolve: Workspace → Scripts → 'MOGRT Converter starten' / 'MOGRT Renders importieren'")


def cmd_mcp(args: argparse.Namespace) -> None:
    if args.config:
        from .agent_skill import command, mcp_config

        print(mcp_config())
        print(f"\nClaude Desktop: in claude_desktop_config.json eintragen (Einstellungen → Entwickler → Konfiguration bearbeiten)."
              f"\nClaude Code:    claude mcp add --scope user mogrt-converter -- {command()} mcp", file=sys.stderr)
        return
    from .mcp_server import main as serve

    serve()


def cmd_skill(args: argparse.Namespace) -> None:
    from .agent_skill import install, skill_text

    if args.install:
        print(f"Skill installiert: {install()}")
    else:
        print(skill_text())


def motion_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("Bewegung (wie in Premiere)")
    g.add_argument("--position", metavar="X,Y", help="Position der Grafikmitte in Pixeln (Standard: Bildmitte)")
    g.add_argument("--offset", metavar="DX,DY", help="verschieben, z. B. 0,-300 = 300 px höher")
    g.add_argument("--motion-scale", type=float, metavar="PROZENT", help="Skalierung in %%")
    g.add_argument("--rotation", type=float, metavar="GRAD", help="Drehung in Grad")
    g.add_argument("--opacity", type=float, metavar="PROZENT", help="Deckkraft in %%")


def _utf8_console() -> None:
    """Make the console accept umlauts and CJK in template names.

    A frozen Windows build keeps the console codepage (cp936/GBK), which raises
    UnicodeEncodeError on umlauts or CJK. PYTHONIOENCODING does not reach a
    frozen app, so reconfigure the streams explicitly.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> None:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="mogrt", description="MOGRT-Vorlagen ohne Adobe rendern")
    sub = ap.add_subparsers(dest="cmd", required=True)

    tmpl_help = "Pfad zur .mogrt-Datei oder Name der Vorlage in der App-Bibliothek"
    json_help = "Ergebnis als JSON auf stdout (für Skripte und KI-Agenten)"

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("mogrt", help=tmpl_help)
        p.add_argument("--json", action="store_true", help=json_help)
        p.add_argument("--set", action="append", metavar="NAME=WERT", help="Reglerwert setzen (mehrfach möglich)")
        p.add_argument("--values", metavar="JSON", help="JSON-Datei mit Reglerwerten {Name: Wert}")
        p.add_argument("--scale", type=float, default=1.0, help="Auflösungsfaktor (Standard 1.0)")
        p.add_argument("-o", "--output", help="Ausgabedatei")
        p.add_argument("-d", "--duration", metavar="DAUER",
                       help="neue Dauer: Sekunden (8.5), Timecode (00:00:08:12) oder Frames (200f)")
        motion_args(p)

    p = sub.add_parser("list", help="Vorlagen der App-Bibliothek (Ordner, Dateien, Sammlungen) auflisten")
    p.add_argument("--collection", metavar="NAME", help="nur Vorlagen dieser Sammlung")
    p.add_argument("--json", action="store_true", help=json_help)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("info", help="Regler, Fonts und Eckdaten anzeigen")
    p.add_argument("mogrt", help=tmpl_help)
    p.add_argument("--json", action="store_true", help=json_help)
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("fonts", help="Schriften prüfen und fehlende von Google Fonts laden")
    p.add_argument("mogrt", nargs="+")
    p.add_argument("--download", action="store_true", help="fehlende freie Schriften herunterladen")
    p.set_defaults(fn=cmd_fonts)

    p = sub.add_parser("inspect", help="Ebenen/Keyframes ausgeben (Diagnose)")
    p.add_argument("mogrt", help=tmpl_help)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("still", help="Einzelbild als PNG rendern")
    common(p)
    p.add_argument("-t", "--time", type=float, help="Zeitpunkt in Sekunden")
    p.set_defaults(fn=cmd_still)

    p = sub.add_parser("render", help="Video rendern (Standard: ProRes 4444 mit Alpha)")
    common(p)
    p.add_argument("-f", "--format", default="prores4444", choices=["prores4444", "prores4444xq", "png-mov", "h264"])
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--background", metavar="RRGGBB", help="Hintergrundfarbe statt Transparenz")
    p.add_argument("--no-audio", action="store_true", help="Audio der Vorlage nicht übernehmen")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("batch", help="Stapel-Rendering: eine Datei pro Zeile einer CSV-Tabelle")
    p.add_argument("mogrt", help=tmpl_help)
    p.add_argument("csv", nargs="?", help="CSV mit Reglernamen als Spalten (+ optional Dateiname, Dauer) "
                                          "oder JSON-Liste [{Spalte: Wert}]")
    p.add_argument("--json", action="store_true", help=json_help)
    p.add_argument("-o", "--output", help="Zielordner")
    p.add_argument("--template", metavar="CSV", help="nur eine CSV-Vorlage mit allen Spalten schreiben")
    motion_args(p)
    p.add_argument("-f", "--format", default="prores4444", choices=["prores4444", "prores4444xq", "png-mov", "h264"])
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--no-audio", action="store_true")
    p.set_defaults(fn=cmd_batch)

    p = sub.add_parser("app", help="Oberfläche im Browser starten")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_app)

    p = sub.add_parser("install-resolve", help="Scripts für DaVinci Resolve (Workspace → Scripts) installieren")
    p.add_argument("--output-dir", help="Render-Ordner, aus dem importiert wird")
    p.set_defaults(fn=cmd_install_resolve)

    p = sub.add_parser("mcp", help="MCP-Server (stdio) für Claude Desktop, Claude Code und andere KI-Clients")
    p.add_argument("--config", action="store_true", help="Eintrag für Claude Desktop / Claude Code ausgeben")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("skill", help="Anleitung für KI-Agenten (Claude Code, Codex …) ausgeben oder installieren")
    p.add_argument("--install", action="store_true", help="als Skill nach ~/.claude/skills/mogrt-converter installieren")
    p.set_defaults(fn=cmd_skill)

    args = ap.parse_args(argv)
    if not getattr(args, "json", False):
        args.fn(args)
        return
    try:
        args.fn(args)
    except SystemExit as e:  # errors as JSON too, so an agent can read them
        if e.code not in (0, None, 1):
            print(json.dumps({"ok": False, "error": str(e.code)}, ensure_ascii=False))
            raise SystemExit(1) from None
        raise
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
