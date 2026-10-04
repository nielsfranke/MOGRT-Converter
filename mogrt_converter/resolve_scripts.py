"""DaVinci Resolve integration: scripts for Workspace > Scripts."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "darwin":
    RESOLVE_SCRIPTS = Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
elif sys.platform == "win32":
    import os

    RESOLVE_SCRIPTS = Path(os.environ.get("APPDATA", "")) / "Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility"
else:
    RESOLVE_SCRIPTS = Path.home() / ".local/share/DaVinciResolve/Fusion/Scripts/Utility"

LAUNCH = '''"""MOGRT Converter starten (installiert von mogrt install-resolve)."""
import socket, subprocess, sys, time, webbrowser

CMD = {cmd!r}
OPEN_BROWSER = {open_browser!r}
URL = "http://127.0.0.1:8765/"


def running():
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=0.3):
            return True
    except OSError:
        return False


if OPEN_BROWSER:
    if not running():
        subprocess.Popen(CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            if running():
                break
            time.sleep(0.2)
    webbrowser.open(URL)
else:
    subprocess.Popen(CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
'''

IMPORT = '''"""Gerenderte MOGRTs in den Media Pool importieren (installiert von mogrt install-resolve)."""
import os

OUTPUT_DIR = {output_dir!r}
BIN_NAME = "MOGRTs"
EXTS = (".mov", ".mp4")


def get_resolve():
    r = globals().get("resolve")
    if r:
        return r
    try:
        return bmd.scriptapp("Resolve")  # noqa: F821 (provided by Resolve)
    except Exception:
        pass
    import DaVinciResolveScript as dvr
    return dvr.scriptapp("Resolve")


resolve = get_resolve()
project = resolve.GetProjectManager().GetCurrentProject()
pool = project.GetMediaPool()
root = pool.GetRootFolder()
target = next((f for f in root.GetSubFolderList() if f.GetName() == BIN_NAME), None) or pool.AddSubFolder(root, BIN_NAME)
existing = set()
for clip in target.GetClipList() or []:
    existing.add(clip.GetClipProperty("File Path"))
files = []
if os.path.isdir(OUTPUT_DIR):
    for name in sorted(os.listdir(OUTPUT_DIR)):
        path = os.path.join(OUTPUT_DIR, name)
        if name.lower().endswith(EXTS) and path not in existing:
            files.append(path)
pool.SetCurrentFolder(target)
if files:
    pool.ImportMedia(files)
print("MOGRT-Import: %d neue Datei(en) in Bin '%s'" % (len(files), BIN_NAME))
'''


def _launch_command() -> tuple[list[str], bool]:
    """How Resolve should start the converter: the bundled app if frozen, else the dev server."""
    from .paths import FROZEN

    if FROZEN:
        exe = Path(sys.executable)
        if sys.platform == "darwin":
            app = next((p for p in exe.parents if p.suffix == ".app"), None)
            if app is not None:
                return ["open", "-a", str(app)], False
        return [str(exe)], False
    mogrt = Path(sys.executable).with_name("mogrt.exe" if sys.platform == "win32" else "mogrt")
    return [str(mogrt), "app", "--no-browser"], True


def install(output_dir: str) -> list[Path]:
    cmd, open_browser = _launch_command()
    RESOLVE_SCRIPTS.mkdir(parents=True, exist_ok=True)
    a = RESOLVE_SCRIPTS / "MOGRT Converter starten.py"
    b = RESOLVE_SCRIPTS / "MOGRT Renders importieren.py"
    a.write_text(LAUNCH.format(cmd=cmd, open_browser=open_browser), encoding="utf-8")
    b.write_text(IMPORT.format(output_dir=str(Path(output_dir).expanduser())), encoding="utf-8")
    return [a, b]
