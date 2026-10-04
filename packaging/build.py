"""Build the desktop app for the current platform.

    python packaging/build.py            # build into dist/
    python packaging/build.py --no-dmg   # macOS: skip the .dmg

Results (dist/):
  macOS    MOGRT Converter.app, MOGRT-Converter-<ver>-macOS-<arch>.dmg
  Windows  MOGRT Converter/ (folder), MOGRT-Converter-<ver>-Windows-x64.zip (+ Setup .exe if Inno Setup is installed)
  Linux    MOGRT Converter/ (folder), MOGRT-Converter-<ver>-Linux-<arch>.tar.gz
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = "MOGRT Converter"
sys.path.insert(0, str(ROOT))
from mogrt_converter import __version__  # noqa: E402


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def arch() -> str:
    m = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(m, m)


def ghostscript() -> None:
    """Provide build/ghostscript/<platform>/ for the spec: build from source (macOS/Linux)
    or copy an installed official Ghostscript (Windows)."""
    if sys.platform == "win32":
        osname = "windows"
        out = ROOT / "build" / "ghostscript" / f"{osname}-{platform.machine()}"
        roots = [Path(p) for p in ("C:/Program Files/gs", "C:/Program Files (x86)/gs") if Path(p).is_dir()]
        cands = sorted((d for r in roots for d in r.glob("gs*") if (d / "bin" / "gswin64c.exe").exists()), reverse=True)
        if not cands:
            print("WARNUNG: Ghostscript nicht installiert (choco install ghostscript) – App ohne Ghostscript.")
            return
        gs = cands[0]
        out.mkdir(parents=True, exist_ok=True)
        for name in ("gswin64c.exe", "gsdll64.dll"):
            shutil.copy(gs / "bin" / name, out / name)
        for lic in ("LICENSE", "doc/COPYING"):
            if (gs / lic).exists():
                shutil.copy(gs / lic, out / (Path(lic).name + ".txt"))
        (out / "SOURCE.txt").write_text(
            f"Ghostscript ({gs.name}, Artifex Software), GNU AGPL-3.0.\n"
            "Source: https://github.com/ArtifexSoftware/ghostpdl-downloads/releases\n", encoding="utf-8")
        return
    run(["sh", str(ROOT / "packaging" / "ghostscript" / "build_gs.sh")])


def pyinstaller() -> None:
    run([sys.executable, str(ROOT / "packaging" / "make_icons.py")])
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
         str(ROOT / "packaging" / "mogrt_converter.spec")])


def mac_dmg() -> Path:
    app = DIST / f"{APP}.app"
    dmg = DIST / f"MOGRT-Converter-{__version__}-macOS-{arch()}.dmg"
    stage = ROOT / "build" / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    run(["ditto", str(app), str(stage / app.name)])
    (stage / "Applications").symlink_to("/Applications")
    dmg.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", APP, "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(dmg)])
    return dmg


def mac_sign() -> None:
    """Ad-hoc sign the bundle so Gatekeeper on the build machine accepts it."""
    app = DIST / f"{APP}.app"
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])


def windows_package() -> list[Path]:
    folder = DIST / APP
    out = [DIST / f"MOGRT-Converter-{__version__}-Windows-{arch()}.zip"]
    with zipfile.ZipFile(out[0], "w", zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob("*"):
            z.write(f, Path(APP) / f.relative_to(folder))
    iscc = shutil.which("iscc") or next((str(p) for p in Path("C:/Program Files (x86)").glob("Inno Setup*/ISCC.exe")), None)
    if iscc:
        run([iscc, f"/DAppVersion={__version__}", f"/DSourceDir={folder}", f"/DOutDir={DIST}", str(ROOT / "packaging" / "windows" / "installer.iss")])
        out += list(DIST.glob("MOGRT-Converter-*-Setup.exe"))
    else:
        print("Inno Setup nicht gefunden – nur ZIP erzeugt.")
    return out


def linux_package() -> Path:
    folder = DIST / APP
    shutil.copy(ROOT / "packaging" / "icons" / "icon_256.png", folder / "mogrt-converter.png")
    shutil.copy(ROOT / "packaging" / "linux" / "mogrt-converter.desktop", folder / "mogrt-converter.desktop")
    shutil.copy(ROOT / "packaging" / "linux" / "install.sh", folder / "install.sh")
    (folder / "install.sh").chmod(0o755)
    out = DIST / f"MOGRT-Converter-{__version__}-Linux-{arch()}.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        t.add(folder, arcname="MOGRT-Converter")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-dmg", action="store_true")
    ap.add_argument("--no-ghostscript", action="store_true", help="Ghostscript nicht mitbündeln")
    args = ap.parse_args()
    if not args.no_ghostscript:
        ghostscript()
    pyinstaller()
    if sys.platform == "darwin":
        mac_sign()
        if not args.no_dmg:
            print("DMG:", mac_dmg())
    elif sys.platform == "win32":
        print("Pakete:", windows_package())
    else:
        print("Paket:", linux_package())


if __name__ == "__main__":
    main()
