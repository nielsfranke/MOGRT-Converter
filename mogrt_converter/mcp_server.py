"""MCP server (stdio) so Claude Desktop, Claude Code and other MCP clients can fill in and render templates.

    mogrt mcp

Speaks JSON-RPC 2.0, one message per line, without the MCP SDK (keeps the bundled app small).
stdout carries only protocol messages; everything else is redirected to stderr.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from . import __version__, library

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
FORMATS = ["prores4444", "prores4444xq", "png-mov", "h264"]
PREVIEW_PX = 1280  # longer side of stills sent back to the model

INSTRUCTIONS = """\
Renders After Effects Motion Graphics Templates (.mogrt) – lower thirds, titles, call-outs,
outros – as ProRes 4444 clips with alpha for DaVinci Resolve. Workflow: list_templates →
template_info (control names) → preview_still to check that the texts fit (long names can
overflow) → render or render_batch. Only change the controls the user asked for. Renders go to
the app's render folder unless the user names another; Resolve imports new clips from there."""

_TEMPLATE = {"type": "string", "description": "Template name from list_templates (or a unique part of it) or a path to a .mogrt"}
_VALUES = {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean"]},
           "description": "Control values keyed by the control's `column` from template_info. Text: \\n for a line break; "
                          "checkbox: true/false; colour: #RRGGBB; point: \"960 540\". Omitted controls keep their default."}
_DURATION = {"type": "string", "description": "New duration: seconds (8.5), timecode (00:00:08:12) or frames (200f). "
                                              "Intro and outro keep their timing."}
_MOTION = {"type": "object", "description": "Optional motion like in Premiere",
           "properties": {"offset": {"type": "string", "description": "DX,DY in px, e.g. \"0,-300\" = 300 px higher"},
                          "position": {"type": "string", "description": "X,Y of the graphic's centre in px"},
                          "scale": {"type": "number", "description": "percent"},
                          "rotation": {"type": "number", "description": "degrees"},
                          "opacity": {"type": "number", "description": "percent"}}}

TOOLS = [
    {"name": "list_templates",
     "description": "Templates in the MOGRT Converter library (folders, files, collections such as one per client) "
                    "and the render folder.",
     "inputSchema": {"type": "object", "properties": {
         "collection": {"type": "string", "description": "only templates of this collection (part of the name is enough)"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "template_info",
     "description": "Controls of a template (name, type, default, `column` = key for values), size, fps, duration, "
                    "protected intro/outro regions and whether its fonts are installed.",
     "inputSchema": {"type": "object", "properties": {"template": _TEMPLATE}, "required": ["template"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "preview_still",
     "description": "Render one frame with the given values and return it as an image. Use it to check that texts fit "
                    "before rendering video. Default time: the end of the intro (graphic fully visible).",
     "inputSchema": {"type": "object", "properties": {
         "template": _TEMPLATE, "values": _VALUES, "duration": _DURATION, "motion": _MOTION,
         "time": {"type": "number", "description": "seconds"}}, "required": ["template"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "render",
     "description": "Render one clip (default ProRes 4444 with alpha) and return its path.",
     "inputSchema": {"type": "object", "properties": {
         "template": _TEMPLATE, "values": _VALUES, "duration": _DURATION, "motion": _MOTION,
         "filename": {"type": "string", "description": "file name without extension (default: template name + first text)"},
         "output_dir": {"type": "string", "description": "default: the app's render folder"},
         "format": {"type": "string", "enum": FORMATS}}, "required": ["template"]}},
    {"name": "render_batch",
     "description": "Render one clip per row, e.g. a lower third for each person. Returns the paths.",
     "inputSchema": {"type": "object", "properties": {
         "template": _TEMPLATE, "motion": _MOTION,
         "rows": {"type": "array", "items": {"type": "object", "properties": {
             "values": _VALUES, "duration": _DURATION,
             "filename": {"type": "string", "description": "file name without extension"}}}},
         "output_dir": {"type": "string", "description": "default: the app's render folder"},
         "format": {"type": "string", "enum": FORMATS}}, "required": ["template", "rows"]}},
]


class ToolError(Exception):
    """Reported to the model as a tool result with isError (it can correct itself)."""


def _cell(v: Any) -> str:
    return "ja" if v is True else "nein" if v is False else "" if v is None else str(v)


def _rows(m, rows: list[dict]):
    """Rows via the batch table logic, so values are parsed exactly like CSV cells."""
    from .batch import build_rows

    records = []
    for r in rows:
        rec = {str(k): _cell(v) for k, v in (r.get("values") or {}).items()}
        for key in ("filename", "duration"):
            if r.get(key) not in (None, ""):
                rec[key] = _cell(r[key])
        records.append(rec)
    table = build_rows(m, records)
    if table.unknown_columns:
        raise ToolError("Unknown controls: " + ", ".join(table.unknown_columns)
                        + ". Available: " + ", ".join(h for h, _ in _columns(m)))
    if table.errors:
        raise ToolError("; ".join(table.errors))
    if len(table.rows) != len(rows):
        # build_rows skips rows without any value; keep them as default renders
        from .batch import Row

        table.rows = [next((t for t in table.rows if t.index == i), Row(index=i, label=f"Zeile {i}"))
                      for i in range(1, len(rows) + 1)]
    return table.rows


def _columns(m):
    from .batch import columns

    return columns(m)


def _motion(m, motion: dict | None) -> dict:
    from .cli import _motion as cli_motion

    mo = motion or {}
    ns = argparse.Namespace(position=mo.get("position"), offset=mo.get("offset"), motion_scale=mo.get("scale"),
                            rotation=mo.get("rotation"), opacity=mo.get("opacity"))
    return cli_motion(ns, m)


def _load(ref: str):
    from .mogrt import load

    if not ref:
        raise ToolError("template is required")
    try:
        return load(library.resolve(ref))
    except SystemExit as e:
        raise ToolError(str(e.code)) from None


class _Capture:
    """Collect the renderer's WARNUNG lines while a tool runs (warnings go to stderr)."""

    def __enter__(self):
        from .cli import _Warnings

        self.w = _Warnings().__enter__()
        return self.w

    def __exit__(self, *exc):
        self.w.__exit__(*exc)


class Server:
    def __init__(self, out=None) -> None:
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.render_lock = threading.Lock()  # one render at a time, they use all cores anyway

    # --- transport -------------------------------------------------------------------------
    def send(self, msg: dict) -> None:
        with self.lock:
            self.out.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.out.flush()

    def serve(self, stdin=None) -> None:
        for line in stdin or sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            for m in msg if isinstance(msg, list) else [msg]:
                self.dispatch(m)

    def dispatch(self, msg: dict) -> None:
        method, mid = msg.get("method"), msg.get("id")
        if method is None or mid is None:
            return  # a response or a notification (initialized, cancelled): nothing to do
        params = msg.get("params") or {}
        if method == "tools/call":  # may take minutes: keep answering pings meanwhile
            threading.Thread(target=self._call, args=(mid, params), daemon=True).start()
            return
        try:
            result = self.handle(method, params)
        except KeyError:
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
            return
        self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def handle(self, method: str, params: dict) -> dict:
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mogrt-converter", "title": "MOGRT Converter", "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        raise KeyError(method)

    def _call(self, mid: Any, params: dict) -> None:
        name, args = params.get("name"), params.get("arguments") or {}
        token = (params.get("_meta") or {}).get("progressToken")
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
            return
        try:
            with _Capture() as w:
                content = fn(args, self._progress(token))
            if w.unique():
                content.append({"type": "text", "text": "Warnings: " + "; ".join(w.unique())})
            result = {"content": content}
        except ToolError as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def _progress(self, token):
        def report(done: float, total: float, message: str) -> None:
            if token is not None:
                self.send({"jsonrpc": "2.0", "method": "notifications/progress",
                           "params": {"progressToken": token, "progress": done, "total": total, "message": message}})
        return report

    # --- tools -----------------------------------------------------------------------------
    @staticmethod
    def _json(data: Any) -> list[dict]:
        return [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]

    def tool_list_templates(self, args: dict, progress) -> list[dict]:
        cfg = library.load_config()
        found = library.items(cfg)
        if args.get("collection"):
            q = args["collection"].lower()
            found = [i for i in found if any(q in c.lower() for c in i["collections"])]
        return self._json({"output_dir": cfg["output_dir"], "collections": [c["name"] for c in cfg["collections"]],
                           "templates": [{"name": i["name"], "path": i["path"], "collections": i["collections"]}
                                         for i in found]})

    def tool_template_info(self, args: dict, progress) -> list[dict]:
        from . import fonts
        from .cli import _control_info

        m = _load(args.get("template", ""))
        return self._json({"name": m.name, "path": str(m.path), "width": m.width, "height": m.height,
                           "fps": m.frame_rate, "duration": m.duration,
                           "protected_regions": [[round(s, 3), round(e, 3)] for s, e in m.protected_regions],
                           "fonts": [{"name": f, "available": fonts.is_available(f)} for f in m.fonts],
                           "controls": _control_info(m)})

    def tool_preview_still(self, args: dict, progress) -> list[dict]:
        from .output import save_png
        from .paths import cache_dir
        from .render.compositor import Renderer
        from .timing import parse_duration

        m = _load(args.get("template", ""))
        row = _rows(m, [{"values": args.get("values"), "duration": args.get("duration")}])[0]
        r = Renderer(m, row.values, scale=min(1.0, PREVIEW_PX / max(m.width, m.height)))
        if row.duration:
            r.set_duration(parse_duration(row.duration, m.frame_rate))
        r.set_motion(_motion(m, args.get("motion")))
        t = args.get("time")
        if t is None:
            regs = m.protected_regions  # the intro keeps its timing, so its end is the same in the output
            t = regs[0][1] if regs and regs[0][0] < 0.05 else r.duration / 2
        t = max(0.0, min(float(t), r.duration - 1 / m.frame_rate))
        with self.render_lock:
            img = r.render_frame(t)
        out = save_png(img, cache_dir() / "previews" / f"{m.path.stem}.png")
        data = bytes(img.encodeToData())  # PNG
        return [{"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": "image/png"},
                {"type": "text", "text": f"Frame at {t:.2f} s of {r.duration:.2f} s ({img.width()}x{img.height()} preview of "
                                         f"{m.width}x{m.height}). Saved: {out}"}]

    def tool_render(self, args: dict, progress) -> list[dict]:
        row = {k: args.get(k) for k in ("values", "duration", "filename")}
        return self._render(args, [row], progress)

    def tool_render_batch(self, args: dict, progress) -> list[dict]:
        rows = args.get("rows") or []
        if not rows:
            raise ToolError("rows is empty")
        return self._render(args, rows, progress)

    def _render(self, args: dict, rows: list[dict], progress) -> list[dict]:
        from .batch import render_rows

        m = _load(args.get("template", ""))
        fmt = args.get("format") or "prores4444"
        if fmt not in FORMATS:
            raise ToolError(f"format must be one of {FORMATS}")
        table = _rows(m, rows)
        out_dir = Path(args.get("output_dir") or library.load_config()["output_dir"]).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)

        def cb(k: int, i: int, n: int) -> None:
            if i == n or i % 10 == 0:
                progress(k * n + i, len(table) * n, f"clip {k + 1}/{len(table)}, frame {i}/{n}")

        with self.render_lock:
            paths = render_rows(m.path, table, out_dir, fmt=fmt, progress=cb, motion=_motion(m, args.get("motion")))
        return self._json({"outputs": [str(p) for p in paths], "output_dir": str(out_dir)})


def main() -> None:
    out = sys.stdout
    sys.stdout = sys.stderr  # stray prints must not corrupt the protocol
    try:
        Server(out).serve(sys.stdin)
    except KeyboardInterrupt:
        pass
