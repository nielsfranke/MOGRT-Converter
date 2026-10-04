"""Platform-specific locations (works for the dev checkout and the bundled app)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "MOGRT Converter"
FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Read-only resources (static UI, bundled fonts). PyInstaller unpacks them to _MEIPASS."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """User data: config, user-installed fonts (override with MOGRT_DATA_DIR)."""
    if os.environ.get("MOGRT_DATA_DIR"):
        base = Path(os.environ["MOGRT_DATA_DIR"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "mogrt-converter"
    base.mkdir(parents=True, exist_ok=True)
    return base


def cache_dir() -> Path:
    env = os.environ.get("MOGRT_CACHE")
    if env:
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mogrt-converter"


def user_fonts_dir() -> Path:
    d = data_dir() / "Fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_fonts_dir() -> Path:
    return resource_dir() / "fonts"


def platform_tag() -> str:
    """e.g. darwin-arm64, linux-x86_64, windows-AMD64 (matches packaging/ghostscript/build_gs.sh)."""
    import platform

    osname = "windows" if sys.platform == "win32" else sys.platform
    return f"{osname}-{platform.machine()}"


def bundled_ghostscript() -> Path | None:
    """Ghostscript shipped with the app (or built locally into build/ghostscript in a checkout)."""
    exe = "gswin64c.exe" if sys.platform == "win32" else "gs"
    for base in (resource_dir() / "ghostscript", resource_dir() / "build" / "ghostscript" / platform_tag()):
        if (base / exe).exists():
            return base / exe
    return None


def default_movies_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Movies"
    if sys.platform == "win32":
        return Path.home() / "Videos"
    vids = Path.home() / "Videos"
    return vids if vids.is_dir() else Path.home()


def no_window_flags() -> int:
    """subprocess creationflags that keep console windows from flashing up on Windows."""
    if sys.platform == "win32":
        import subprocess

        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def reveal(path: str | os.PathLike) -> None:
    """Show a file in Finder / Explorer / the file manager."""
    import subprocess

    p = Path(path)
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(p)])
    elif sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(p)], creationflags=no_window_flags())
    else:
        subprocess.run(["xdg-open", str(p.parent if p.is_file() else p)])


def open_folder(path: str | os.PathLike) -> None:
    import subprocess

    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(p)])
    elif sys.platform == "win32":
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(p)])
