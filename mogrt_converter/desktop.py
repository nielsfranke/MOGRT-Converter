"""Desktop entry point: native window (pywebview) around the local web UI.

Also the entry point of the bundled app; CLI subcommands are passed through,
e.g. ``"MOGRT Converter" render file.mogrt --set Titel=…``.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote

from . import paths

CLI_COMMANDS = {"info", "inspect", "still", "render", "app", "install-resolve", "fonts", "batch", "-h", "--help"}


class Api:
    """Exposed to JavaScript as window.pywebview.api."""

    def __init__(self) -> None:
        self.window = None

    def _dialog(self, kind, **kw):
        import webview

        return self.window.create_file_dialog(kind, **kw) if self.window else None

    def pick_folder(self, initial: str = "") -> str | None:
        import webview

        start = str(Path(initial).expanduser()) if initial else ""
        res = self._dialog(webview.FileDialog.FOLDER, directory=start if Path(start).is_dir() else "")
        return res[0] if res else None

    def save_text(self, filename: str, content: str) -> str | None:
        """Save dialog for text files (CSV templates)."""
        import webview

        res = self._dialog(webview.FileDialog.SAVE, save_filename=filename,
                           file_types=("CSV (*.csv)",))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        Path(path).write_text(content, encoding="utf-8")
        return path

    def pick_csv(self) -> str | None:
        """Open dialog for a CSV table; returns its text."""
        import webview

        res = self._dialog(webview.FileDialog.OPEN, file_types=("CSV (*.csv;*.tsv;*.txt)", "All files (*.*)"))
        if not res:
            return None
        from .batch import _decode

        return _decode(Path(res[0]).read_bytes())

    def pick_mogrts(self) -> list[str]:
        import webview

        res = self._dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=("MOGRT (*.mogrt)", "All files (*.*)"))
        return list(res or [])


class _OpenQueue:
    """Files opened via Finder before/after the page is ready."""

    def __init__(self) -> None:
        self.window = None
        self.ready = False
        self.pending: list[str] = []

    def push(self, files: list[str]) -> None:
        import json

        files = [f for f in files if f.lower().endswith(".mogrt")]
        if not files:
            return
        if self.ready and self.window is not None:
            # never block the (Cocoa) main thread: evaluate_js waits for it
            js = f"window.openExternal && window.openExternal({json.dumps(files)})"
            threading.Thread(target=self.window.evaluate_js, args=(js,), daemon=True).start()
        else:
            self.pending += files

    def on_loaded(self) -> None:
        self._install_drop_handler()
        self.ready = True
        files, self.pending = self.pending, []
        if files:
            self.push(files)


    def _install_drop_handler(self) -> None:
        """Files dragged into the window: pywebview resolves their real paths on the Python side."""
        if getattr(self, "_drop_installed", False) or self.window is None:
            return
        try:
            from webview.dom import DOMEventHandler

            def on_drop(event: dict) -> None:
                files = (event.get("dataTransfer") or {}).get("files") or []
                paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
                if paths:
                    self.push(paths)

            self.window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)
            self._drop_installed = True
        except Exception as e:  # pragma: no cover - older pywebview / platform without DOM API
            print(f"Drag & Drop-Handler nicht verfügbar: {e}", file=sys.stderr)


OPEN_QUEUE = _OpenQueue()


def _install_mac_open_handler() -> None:
    """Add application:openFiles: to pywebview's app delegate (double-click, Open With, Dock drop)."""
    import objc
    from webview.platforms import cocoa

    def application_openFiles_(self, app, filenames):
        OPEN_QUEUE.push([str(f) for f in filenames])
        app.replyToOpenOrPrint_(0)  # NSApplicationDelegateReplySuccess

    objc.classAddMethods(cocoa.BrowserView.AppDelegate, [
        objc.selector(application_openFiles_, selector=b"application:openFiles:", signature=b"v@:@@"),
    ])


def _start_server():
    from .app.server import make_server

    httpd = make_server(8765)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _running_jobs() -> int:
    from .app.server import STATE

    return sum(1 for j in STATE.jobs.values() if j.get("status") == "running")


def main(argv: list[str] | None = None) -> None:
    args = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("-psn_")]
    if args and args[0] in CLI_COMMANDS:
        from .cli import main as cli_main

        cli_main(args)
        return

    os.environ["MOGRT_DESKTOP"] = "1"
    files = [str(Path(a).resolve()) for a in args if a.lower().endswith(".mogrt") and Path(a).exists()]
    httpd = _start_server()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if files:
        url += "?open=" + quote(files[0])

    try:
        import webview
    except Exception as e:  # no GUI backend available (e.g. Linux without GTK/Qt)
        print(f"Kein natives Fenster verfügbar ({e}) – öffne im Browser: {url}", file=sys.stderr)
        _browser_fallback(httpd, url)
        return

    api = Api()
    window = webview.create_window(
        paths.APP_NAME, url, js_api=api, width=1440, height=900, min_size=(1000, 640),
        background_color="#1b1c1f", text_select=True,
    )
    api.window = window

    def on_closing():
        n = _running_jobs()
        if not n:
            return True
        return window.create_confirmation_dialog(
            "Rendern läuft",
            f"{n} Rendering{'s' if n > 1 else ''} läuft noch. Beim Beenden wird es abgebrochen.",
        )

    window.events.closing += on_closing
    OPEN_QUEUE.window = window
    window.events.loaded += OPEN_QUEUE.on_loaded
    if sys.platform == "darwin":
        try:
            _install_mac_open_handler()
        except Exception as e:  # pragma: no cover - best effort
            print(f"Öffnen-Handler nicht verfügbar: {e}", file=sys.stderr)

    try:
        webview.start(debug=bool(os.environ.get("MOGRT_DEBUG")), private_mode=False,
                      storage_path=str(paths.data_dir() / "webview"))
    except Exception as e:
        print(f"Natives Fenster konnte nicht gestartet werden ({e}) – öffne im Browser: {url}", file=sys.stderr)
        _browser_fallback(httpd, url)
        return
    httpd.shutdown()


def _browser_fallback(httpd, url: str) -> None:
    webbrowser.open(url)
    print("Beenden mit Ctrl+C")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
