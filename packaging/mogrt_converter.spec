# PyInstaller spec for MOGRT Converter (macOS .app, Windows/Linux folder build).
# Build with:  python packaging/build.py
# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from mogrt_converter import __version__  # noqa: E402

APP = "MOGRT Converter"
ICONS = ROOT / "packaging" / "icons"

datas = [
    (str(ROOT / "mogrt_converter" / "app" / "static"), "mogrt_converter/app/static"),
    (str(ROOT / "fonts"), "fonts"),
]
binaries = []

# slim Ghostscript for EPS footage (built/copied by packaging/build.py)
import platform as _platform
_os = "windows" if sys.platform == "win32" else sys.platform
GS_DIR = ROOT / "build" / "ghostscript" / f"{_os}-{_platform.machine()}"
if GS_DIR.is_dir():
    for f in GS_DIR.iterdir():
        if f.suffix.lower() in (".txt", ".md"):
            datas.append((str(f), "ghostscript"))
        else:
            binaries.append((str(f), "ghostscript"))
else:
    print("WARNUNG: kein Ghostscript in", GS_DIR, "- EPS-Footage braucht dann ein System-Ghostscript")
datas.append((str(ROOT / "THIRD-PARTY-NOTICES.md"), "."))
datas.append((str(ROOT / "LICENSE"), "."))
hiddenimports = collect_submodules("py_aep") + ["mogrt_converter.cli", "mogrt_converter.app.server"]
if sys.platform == "darwin":
    hiddenimports += ["PyObjCTools.AppHelper", "Foundation", "AppKit", "objc"]
for pkg in ("py_aep", "imageio_ffmpeg", "pypdfium2", "pypdfium2_raw", "uharfbuzz", "esprima", "webview"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP if sys.platform != "linux" else "mogrt-converter",
    console=False,
    argv_emulation=False,  # macOS "open document" events are handled in desktop.py
    icon=str(ICONS / ("icon.ico" if sys.platform == "win32" else "icon.icns" if sys.platform == "darwin" else "icon.png")),
    codesign_identity=None,
    entitlements_file=None,
)

extra = []
if sys.platform == "win32":
    # console twin for the command line (GUI-subsystem exes cannot print to a terminal)
    extra.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="mogrt", console=True,
                     icon=str(ICONS / "icon.ico")))

coll = COLLECT(exe, *extra, a.binaries, a.datas, strip=False, upx=False, name=APP)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP}.app",
        icon=str(ICONS / "icon.icns"),
        bundle_identifier="io.github.nielsfranke.mogrt-converter",
        version=__version__,
        info_plist={
            "CFBundleName": APP,
            "CFBundleDisplayName": APP,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.video",
            "NSHumanReadableCopyright": "MOGRT Converter – AGPL-3.0-or-later",
            "UTImportedTypeDeclarations": [{
                "UTTypeIdentifier": "com.adobe.motion-graphics-template",
                "UTTypeDescription": "Motion Graphics Template",
                "UTTypeConformsTo": ["public.data", "public.archive"],
                "UTTypeTagSpecification": {"public.filename-extension": ["mogrt"]},
            }],
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Motion Graphics Template",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["com.adobe.motion-graphics-template"],
                "CFBundleTypeExtensions": ["mogrt"],
            }],
        },
    )
