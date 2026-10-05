"""Command line: mogrt info | still | render | inspect."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .mogrt import load


def _parse_values(args: argparse.Namespace, m) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if args.values:
        values.update(json.loads(Path(args.values).read_text(encoding="utf-8")))
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"--set erwartet NAME=WERT, bekam {item!r}")
        key, raw = item.split("=", 1)
        ctrl = m.control(key)
        from .batch import parse_value

        values[ctrl.id] = parse_value(ctrl, raw)
        continue
        if ctrl.type == "text":
            val: Any = raw.replace("\\n", "\n")
        elif ctrl.type == "checkbox":
            val = raw.lower() in ("1", "true", "ja", "yes", "on")
        elif ctrl.type in ("slider", "angle"):
            val = float(raw)
        elif ctrl.type == "color" and raw.startswith("#"):
            h = raw.lstrip("#")
            val = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]
        elif ctrl.type in ("point", "point3d", "color", "scale"):
            val = [float(x) for x in raw.replace(";", ",").split(",")]
            if ctrl.type == "scale" and len(val) == 1:
                val = [val[0], val[0]]
        else:
            val = raw
        values[ctrl.name] = val
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

    m = load(args.mogrt)
    if args.template:
        Path(args.template).write_text(template_csv(m), encoding="utf-8")
        print(f"CSV-Vorlage geschrieben: {args.template}")
        return
    if not args.csv:
        raise SystemExit("CSV-Datei angeben (oder --template DATEI für eine leere Vorlage)")
    table = build_rows(m, read_csv(Path(args.csv).read_bytes()))
    for e in table.errors:
        print("FEHLER:", e, file=sys.stderr)
    if table.unknown_columns:
        print("Hinweis: unbekannte Spalten ignoriert:", ", ".join(table.unknown_columns), file=sys.stderr)
    if table.errors:
        raise SystemExit(1)
    out_dir = Path(args.output or ".")
    names = output_names(m, table.rows)
    print(f"{len(table.rows)} Zeile(n) → {out_dir}")
    t0 = time.time()

    def progress(k: int, i: int, n: int) -> None:
        if i == n or i % 10 == 0:
            print(f"\r  [{k + 1}/{len(table.rows)}] {names[k][:40]:40s} Frame {i}/{n}   ", end="", file=sys.stderr, flush=True)

    paths = render_rows(args.mogrt, table.rows, out_dir, fmt=args.format, scale=args.scale,
                        audio=not args.no_audio, progress=progress, motion=_motion(args, m))
    print(f"\nFertig in {time.time() - t0:.0f}s", file=sys.stderr)
    for p in paths:
        print(p)


def cmd_info(args: argparse.Namespace) -> None:
    from . import fonts

    m = load(args.mogrt)
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
    out = args.output or Path(args.mogrt).with_suffix(f".{t:.2f}s.png").name
    save_png(r.render_frame(t), out)
    print(out)


def cmd_render(args: argparse.Namespace) -> None:
    from .output import collect_audio, render_video
    from .render.compositor import Renderer

    m = load(args.mogrt)
    values = _parse_values(args, m)
    r = Renderer(m, values, scale=args.scale)
    _apply_duration(args, r)
    out = args.output or str(Path(args.mogrt).with_suffix("").name)
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

    path = render_video(r, out, fmt=args.format, start=args.start, end=args.end, background=bg, audio=audio, progress=progress)
    print(f"\n{path}", file=sys.stderr)
    print(path)


def cmd_app(args: argparse.Namespace) -> None:
    from .app.server import serve

    serve(port=args.port, open_browser=not args.no_browser)


def cmd_install_resolve(args: argparse.Namespace) -> None:
    from .app.server import load_config
    from .resolve_scripts import install

    out = args.output_dir or load_config()["output_dir"]
    for p in install(out):
        print(f"installiert: {p}")
    print("In Resolve: Workspace → Scripts → 'MOGRT Converter starten' / 'MOGRT Renders importieren'")


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

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("mogrt", help="Pfad zur .mogrt-Datei")
        p.add_argument("--set", action="append", metavar="NAME=WERT", help="Reglerwert setzen (mehrfach möglich)")
        p.add_argument("--values", metavar="JSON", help="JSON-Datei mit Reglerwerten {Name: Wert}")
        p.add_argument("--scale", type=float, default=1.0, help="Auflösungsfaktor (Standard 1.0)")
        p.add_argument("-o", "--output", help="Ausgabedatei")
        p.add_argument("-d", "--duration", metavar="DAUER",
                       help="neue Dauer: Sekunden (8.5), Timecode (00:00:08:12) oder Frames (200f)")
        motion_args(p)

    p = sub.add_parser("info", help="Regler, Fonts und Eckdaten anzeigen")
    p.add_argument("mogrt")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("fonts", help="Schriften prüfen und fehlende von Google Fonts laden")
    p.add_argument("mogrt", nargs="+")
    p.add_argument("--download", action="store_true", help="fehlende freie Schriften herunterladen")
    p.set_defaults(fn=cmd_fonts)

    p = sub.add_parser("inspect", help="Ebenen/Keyframes ausgeben (Diagnose)")
    p.add_argument("mogrt")
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
    p.add_argument("mogrt")
    p.add_argument("csv", nargs="?", help="CSV mit Reglernamen als Spalten (+ optional Dateiname, Dauer)")
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

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
