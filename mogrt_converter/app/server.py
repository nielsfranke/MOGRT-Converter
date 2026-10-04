"""Local web app: browse MOGRTs, edit controls with live preview, render ProRes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import skia

from .. import fonts
from ..mogrt import Mogrt, load
from ..output import collect_audio, render_video
from ..render.compositor import Renderer
from .. import paths
from .prefetch import PREFETCH, state_key

STATIC = Path(__file__).parent / "static"
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


class Session:
    """A loaded MOGRT with a preview renderer (guarded by a lock)."""

    def __init__(self, path: Path):
        self.path = path
        self.mogrt: Mogrt = load(path)
        self.renderer = Renderer(self.mogrt, {}, scale=1.0)
        self.lock = threading.Lock()
        self.values_key = ""


class State:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.sessions: dict[str, Session] = {}
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.loading: dict[str, threading.Lock] = {}

    def session(self, path: str) -> Session:
        p = str(Path(path).expanduser().resolve())
        with self.lock:
            s = self.sessions.get(p)
            if s is not None:
                return s
            plock = self.loading.setdefault(p, threading.Lock())
        with plock:  # load outside the global lock so other templates stay responsive
            with self.lock:
                s = self.sessions.get(p)
            if s is None:
                s = Session(Path(p))
                with self.lock:
                    self.sessions[p] = s
            return s


STATE = State()


def _library() -> list[dict[str, Any]]:
    """All known templates. `folder` is the library folder, "" for single files, None if only in a collection."""
    cfg = STATE.cfg
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
    out.sort(key=lambda i: i["name"].lower())
    return out


def _edit_library(op: str, body: dict) -> None:
    """Organise the template list: hide/rename templates, manage collections ("Sammlungen")."""
    cfg = STATE.cfg
    path = body.get("path")
    cols = cfg["collections"]
    col = next((c for c in cols if c["id"] == body.get("id")), None)
    if op == "remove":  # from the list only, the file stays where it is
        if path in cfg["files"]:
            cfg["files"].remove(path)
        else:
            if path not in cfg["hidden"]:
                cfg["hidden"].append(path)
        for c in cols:
            c["items"] = [p for p in c["items"] if p != path]
    elif op == "rename":
        name = str(body.get("name") or "").strip()
        if name:
            cfg["aliases"][path] = name
        else:
            cfg["aliases"].pop(path, None)
    elif op == "remove_folder":
        cfg["library"] = [d for d in cfg["library"] if d != body.get("folder")]
    elif op == "unhide_all":
        cfg["hidden"] = []
    elif op == "collection_create":
        cid = uuid.uuid4().hex[:8]
        cols.append({"id": cid, "name": str(body.get("name") or "Neue Sammlung").strip(), "items": list(body.get("paths") or [])})
    elif op == "collection_rename" and col:
        col["name"] = str(body.get("name") or col["name"]).strip()
    elif op == "collection_delete" and col:
        cols.remove(col)
    elif op == "collection_add" and col:
        for p in body.get("paths") or [path]:
            if p and p not in col["items"]:
                col["items"].append(p)
    elif op == "collection_remove" and col:
        col["items"] = [p for p in col["items"] if p != path]
    else:
        raise ValueError(f"unbekannte Aktion {op}")
    save_config(cfg)


def _thumbnail(path: str) -> bytes:
    """Rendered poster frame (cached next to the extracted MOGRT)."""
    s = STATE.session(path)
    cache = s.mogrt.root / "poster.png"
    if cache.exists():
        return cache.read_bytes()
    m = s.mogrt
    with s.lock:
        s.renderer.scale = 160 / max(m.width, m.height)
        img = None
        # pick the frame with the most visible content among a few candidates
        best = -1.0
        for f in (0.5, 0.65, 0.8, 0.35):
            cand = s.renderer.render_frame(m.duration * f)
            arr = cand.toarray()
            cov = float((arr[..., 3] > 16).mean())
            if cov > best + 0.02:
                best, img = cov, cand
        s.values_key = ""
    surf = skia.Surface.MakeRasterN32Premul(img.width(), img.height())
    c = surf.getCanvas()
    c.clear(skia.Color(17, 18, 20))
    c.drawImage(img, 0, 0)
    data = bytes(surf.makeImageSnapshot().encodeToData(skia.EncodedImageFormat.kPNG, 100))
    cache.write_bytes(data)
    return data


def _control_json(c) -> dict:
    return {"id": c.id, "type": c.type, "name": c.name, "default": c.default, "min": c.min, "max": c.max}


RENDER_POOL = None


def _submit(job: dict) -> None:
    """Queue a render job (at most two run at the same time)."""
    global RENDER_POOL
    from concurrent.futures import ThreadPoolExecutor

    if RENDER_POOL is None:
        RENDER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="render")
    job["status"] = "queued"
    STATE.jobs[job["id"]] = job
    RENDER_POOL.submit(_render_job, job)


def _new_job(path: str, name: str, values: dict, body: dict, duration: str | None = None,
             filename: str | None = None, batch: str | None = None) -> dict:
    return {
        "id": uuid.uuid4().hex[:10],
        "path": path,
        "name": name,
        "values": values,
        "duration": duration,
        "format": body.get("format", STATE.cfg.get("format", "prores4444")),
        "output_dir": body.get("output_dir") or STATE.cfg["output_dir"],
        "filename": _safe_name(filename or name),
        "no_audio": bool(body.get("no_audio")),
        "motion": body.get("motion"),
        "batch": batch,
        "done": 0,
        "total": 0,
        "started": time.time(),
    }


def _render_job(job: dict) -> None:
    if job.get("cancel"):
        job["status"] = "cancelled"
        return
    job["status"] = "running"
    try:
        m = load(job["path"])
        r = Renderer(m, job["values"], scale=job.get("scale", 1.0))
        if job.get("duration"):
            from ..timing import parse_duration

            r.set_duration(parse_duration(job["duration"], m.frame_rate))
        r.set_motion(job.get("motion"))
        audio = [] if job.get("no_audio") else collect_audio(r)
        out_dir = Path(job["output_dir"]).expanduser()
        out = out_dir / job["filename"]

        def progress(i: int, n: int) -> None:
            job["done"], job["total"] = i, n

        path = render_video(r, out, fmt=job["format"], audio=audio, progress=progress, cancel=lambda: job.get("cancel", False))
        job["output"] = str(path)
        job["status"] = "done"
    except KeyboardInterrupt:
        job["status"] = "cancelled"
    except Exception as e:
        traceback.print_exc()
        job["status"] = "error"
        job["error"] = str(e)
    job["finished"] = time.time()


def _safe_name(s: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in " -_äöüÄÖÜß." else "_" for ch in s).strip()
    return keep[:80] or "render"


class Handler(BaseHTTPRequestHandler):
    server_version = "MOGRTConverter/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        if os.environ.get("MOGRT_DEBUG"):
            super().log_message(fmt, *args)

    # ---------------------------------------------------------------- helpers
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _error(self, e: Exception) -> None:
        traceback.print_exc()
        self._json({"error": str(e)}, 500)

    # ---------------------------------------------------------------- GET
    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/library":
                self._json({"items": _library(), "config": STATE.cfg})
            elif url.path == "/api/thumb":
                self._send(200, _thumbnail(q["path"]), "image/png", {"Cache-Control": "max-age=3600"})
            elif url.path.startswith("/api/job/"):
                job = STATE.jobs.get(url.path.rsplit("/", 1)[-1])
                if job is None:
                    self._json({"error": "unbekannter Job"}, 404)
                else:
                    self._json({k: v for k, v in job.items() if k != "values"})
            elif url.path == "/api/status":
                from ..render.effects import ghostscript_bin

                self._json({"ghostscript": ghostscript_bin() is not None, "desktop": paths.FROZEN or bool(os.environ.get("MOGRT_DESKTOP"))})
            elif url.path == "/api/jobs":
                self._json({"jobs": [{k: v for k, v in j.items() if k != "values"} for j in STATE.jobs.values()]})
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._error(e)

    # ---------------------------------------------------------------- POST
    def do_POST(self) -> None:
        url = urlparse(self.path)
        try:
            body = self._body()
            if url.path == "/api/open":
                s = STATE.session(body["path"])
                m = s.mogrt
                STATE.cfg["recent"][str(s.path)] = time.time()
                save_config(STATE.cfg)
                self._json({
                    "path": str(s.path),
                    # same name as in the list: alias, else the file name (often nicer than the comp name)
                    "name": STATE.cfg["aliases"].get(str(s.path)) or s.path.stem,
                    "template_name": s.path.stem,
                    "width": m.width,
                    "height": m.height,
                    "fps": m.frame_rate,
                    "duration": m.duration,
                    "protected": m.protected_regions,
                    "controls": [_control_json(c) for c in m.controls],
                    "fonts": [{"name": f, "ok": fonts.is_available(f)} for f in m.fonts],
                    "font_dir": str(fonts.PROJECT_FONTS),
                })
            elif url.path == "/api/frame":
                s = STATE.session(body["path"])
                frame = body.get("frame")
                ckey = state_key(str(s.path), body) if frame is not None else None
                cached = PREFETCH.get(ckey, int(frame)) if ckey else None
                if cached is not None:
                    self._send(200, cached, "image/png", {"X-Cached": "1"})
                    return
                key = hashlib.sha1(json.dumps(body.get("values", {}), sort_keys=True).encode()).hexdigest()
                with s.lock:
                    if key != s.values_key:
                        s.renderer.set_values(body.get("values", {}))
                        s.values_key = key
                    s.renderer.scale = float(body.get("scale", 0.5))
                    dur = body.get("duration")
                    s.renderer.set_duration(float(dur) if dur else None)
                    s.renderer.set_motion(body.get("motion"))
                    img = s.renderer.render_frame(float(body.get("t", 0.0)))
                    data = bytes(img.encodeToData(skia.EncodedImageFormat.kPNG, 100))
                if ckey:
                    PREFETCH.put(ckey, int(frame), data)
                self._send(200, data, "image/png")
            elif url.path == "/api/prefetch":
                s = STATE.session(body["path"])
                m = s.mogrt
                dur = float(body.get("duration") or m.duration)
                count = max(1, int(round(dur * m.frame_rate)))
                key = PREFETCH.start(str(s.path), body, m.frame_rate, count, int(body.get("frame") or 0))
                self._json({"key": key, "count": count})
            elif url.path == "/api/prefetch/status":
                self._json({"frames": PREFETCH.cached(body.get("key", ""))})
            elif url.path == "/api/prefetch/stop":
                PREFETCH.stop()
                self._json({"ok": True})
            elif url.path == "/api/render":
                m = STATE.session(body["path"]).mogrt
                name = body.get("filename") or m.name
                dur = body.get("duration")
                job = _new_job(body["path"], name, body.get("values", {}), body,
                               duration=str(dur) if dur else None, filename=name)
                _submit(job)
                self._json({"id": job["id"]})
            elif url.path == "/api/batch/template":
                from ..batch import template_csv

                m = STATE.session(body["path"]).mogrt
                self._json({"csv": template_csv(m, body.get("values") or {}), "filename": f"{_safe_name(m.name)} - Stapel.csv"})
            elif url.path == "/api/batch/parse":
                from ..batch import build_rows, columns, read_csv

                m = STATE.session(body["path"]).mogrt
                table = build_rows(m, read_csv(body.get("csv", "")))
                self._json({
                    "rows": [{"values": r.values, "filename": r.filename, "duration": r.duration, "label": r.label} for r in table.rows],
                    "unknown": table.unknown_columns,
                    "errors": table.errors,
                })
            elif url.path == "/api/batch/render":
                from ..batch import Row, output_names

                m = STATE.session(body["path"]).mogrt
                text_ids = [c.id for c in m.controls if c.type == "text"]

                def label(vals: dict, i: int) -> str:
                    for cid in text_ids:
                        v = str(vals.get(cid) or "").strip().split("\n")[0]
                        if v:
                            return v
                    return f"Zeile {i + 1}"

                rows = [Row(index=i + 1, values=r.get("values") or {}, filename=r.get("filename") or None,
                            duration=(str(r["duration"]) if r.get("duration") else None),
                            label=r.get("label") or label(r.get("values") or {}, i))
                        for i, r in enumerate(body.get("rows", []))]
                batch_id = uuid.uuid4().hex[:8]
                ids = []
                for row, fname in zip(rows, output_names(m, rows)):
                    job = _new_job(body["path"], fname, row.values, body, duration=row.duration, filename=fname, batch=batch_id)
                    _submit(job)
                    ids.append(job["id"])
                self._json({"ids": ids, "batch": batch_id})
            elif url.path.startswith("/api/job/") and url.path.endswith("/cancel"):
                job = STATE.jobs.get(url.path.split("/")[3])
                if job:
                    job["cancel"] = True
                self._json({"ok": True})
            elif url.path == "/api/library/edit":
                _edit_library(body.get("op", ""), body)
                self._json({"items": _library(), "config": STATE.cfg})
            elif url.path == "/api/config":
                for k in ("output_dir", "format"):
                    if k in body:
                        STATE.cfg[k] = body[k]
                if "add_library" in body:
                    p = str(Path(body["add_library"]).expanduser())
                    if p not in STATE.cfg["library"]:
                        STATE.cfg["library"].append(p)
                if "add_files" in body:
                    files = STATE.cfg.setdefault("files", [])
                    for f in body["add_files"]:
                        f = str(Path(f).expanduser().resolve())
                        if f in STATE.cfg["hidden"]:
                            STATE.cfg["hidden"].remove(f)
                        if f.lower().endswith(".mogrt") and f not in files:
                            files.append(f)
                if "remove_file" in body:
                    STATE.cfg["files"] = [f for f in STATE.cfg.get("files", []) if f != body["remove_file"]]
                if "remove_library" in body:
                    STATE.cfg["library"] = [d for d in STATE.cfg["library"] if d != body["remove_library"]]
                save_config(STATE.cfg)
                self._json({"config": STATE.cfg})
            elif url.path == "/api/upload":
                # raw .mogrt upload (drag & drop) -> first library folder
                from urllib.parse import unquote

                name = Path(unquote(self.headers.get("X-Filename", "upload.mogrt"))).name
                if not name.lower().endswith(".mogrt"):
                    raise ValueError("nur .mogrt-Dateien")
                n = int(self.headers.get("Content-Length") or 0)
                if not STATE.cfg["library"]:
                    STATE.cfg["library"].append(str(paths.default_movies_dir() / "MOGRTs"))
                    save_config(STATE.cfg)
                dest_dir = Path(STATE.cfg["library"][0]).expanduser()
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / name
                dest.write_bytes(self.rfile.read(n))
                if str(dest.resolve()) in STATE.cfg["hidden"]:
                    STATE.cfg["hidden"].remove(str(dest.resolve()))
                    save_config(STATE.cfg)
                self._json({"path": str(dest)})
            elif url.path == "/api/reveal":
                if body.get("path"):
                    paths.reveal(body["path"])
                self._json({"ok": True})
            elif url.path == "/api/open-folder":
                paths.open_folder(Path(body.get("path", "")).expanduser())
                self._json({"ok": True})
            elif url.path == "/api/fonts/download":
                from ..google_fonts import download_missing

                s = STATE.session(body["path"])
                res = download_missing([f for f in s.mogrt.fonts if not fonts.is_available(f)])
                with STATE.lock:  # fresh renderer so the new fonts are used
                    STATE.sessions.pop(str(s.path), None)
                PREFETCH.forget(str(s.path))
                (s.mogrt.root / "poster.png").unlink(missing_ok=True)
                self._json({"result": res})
            elif url.path == "/api/install-resolve":
                from ..resolve_scripts import install

                files = install(STATE.cfg["output_dir"])
                self._json({"files": [str(f) for f in files]})
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._error(e)

    def do_PUT(self) -> None:
        self.do_POST()


def make_server(port: int = 8765) -> ThreadingHTTPServer:
    """Bind on the given port, or a free one if it is taken (port 0 = any)."""
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def serve(port: int = 8765, open_browser: bool = True) -> None:
    httpd = make_server(port)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"MOGRT Converter läuft auf {url}  (Beenden mit Ctrl+C)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
