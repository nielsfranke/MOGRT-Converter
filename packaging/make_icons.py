"""Generate the app icon (.png / .icns / .ico) with Skia."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import skia
from PIL import Image

OUT = Path(__file__).parent / "icons"


def draw(size: int = 1024) -> skia.Image:
    surf = skia.Surface.MakeRasterN32Premul(size, size)
    c = surf.getCanvas()
    c.clear(skia.ColorTRANSPARENT)
    s = size / 1024
    # macOS icon grid: 824px tile centred, corner radius ~185
    tile = skia.Rect.MakeXYWH(100 * s, 100 * s, 824 * s, 824 * s)
    rr = skia.RRect.MakeRectXY(tile, 185 * s, 185 * s)
    shadow = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(90, 0, 0, 0),
                        MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 18 * s))
    c.save()
    c.translate(0, 12 * s)
    c.drawRRect(rr, shadow)
    c.restore()
    bg = skia.Paint(AntiAlias=True, Shader=skia.GradientShader.MakeLinear(
        [skia.Point(0, tile.top()), skia.Point(0, tile.bottom())],
        [skia.Color(52, 55, 61), skia.Color(27, 28, 31)]))
    c.drawRRect(rr, bg)
    c.save()
    c.clipRRect(rr, doAntiAlias=True)
    # transparency checker = the alpha channel the app renders
    cell = 68 * s
    dark = skia.Paint(Color=skia.Color(62, 65, 72))
    y, row = tile.top(), 0
    while y < tile.bottom():
        x = tile.left() + (cell if row % 2 else 0)
        while x < tile.right():
            c.drawRect(skia.Rect.MakeXYWH(x, y, cell, cell), dark)
            x += 2 * cell
        y += cell
        row += 1
    # fade the checker towards the bottom
    fade = skia.Paint(Shader=skia.GradientShader.MakeLinear(
        [skia.Point(0, tile.top() + 260 * s), skia.Point(0, tile.bottom() - 120 * s)],
        [skia.Color4f(0.106, 0.11, 0.122, 0.0).toColor(), skia.Color(27, 28, 31)]))
    c.drawRect(tile, fade)
    # lower third: amber bar with a slanted end + two text lines
    amber = skia.Color(232, 177, 58)
    bar = skia.Path()
    x0, y0, w, h = tile.left(), 560 * s, 640 * s, 168 * s
    bar.moveTo(x0, y0)
    bar.lineTo(x0 + w, y0)
    bar.lineTo(x0 + w - 70 * s, y0 + h)
    bar.lineTo(x0, y0 + h)
    bar.close()
    c.drawPath(bar, skia.Paint(AntiAlias=True, Color=amber))
    ink = skia.Paint(AntiAlias=True, Color=skia.Color(27, 28, 31))
    c.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x0 + 86 * s, y0 + 40 * s, 380 * s, 44 * s), 10 * s, 10 * s), ink)
    c.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x0 + 86 * s, y0 + 104 * s, 250 * s, 26 * s), 8 * s, 8 * s),
                skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(170, 27, 28, 31)))
    # thin second bar (animation hint)
    c.drawRect(skia.Rect.MakeXYWH(x0, y0 + h + 26 * s, 300 * s, 14 * s), skia.Paint(Color=skia.ColorSetARGB(200, 232, 177, 58)))
    c.restore()
    # subtle rim
    rim = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2 * s, Color=skia.ColorSetARGB(40, 255, 255, 255))
    c.drawRRect(rr, rim)
    return surf.makeImageSnapshot()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw(1024)
    png = OUT / "icon.png"
    img.save(str(png), skia.kPNG)
    pil = Image.open(png)
    pil.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    for sz in (16, 32, 48, 64, 128, 256, 512):
        pil.resize((sz, sz), Image.LANCZOS).save(OUT / f"icon_{sz}.png")
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = OUT / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for sz in (16, 32, 128, 256, 512):
            pil.resize((sz, sz), Image.LANCZOS).save(iconset / f"icon_{sz}x{sz}.png")
            pil.resize((sz * 2, sz * 2), Image.LANCZOS).save(iconset / f"icon_{sz}x{sz}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "icon.icns")], check=True)
        shutil.rmtree(iconset)
    print("Icons in", OUT)


if __name__ == "__main__":
    main()
