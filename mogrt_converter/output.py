"""Frame output: ProRes 4444 (with alpha) / PNG sequences via ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import skia

from .render.compositor import Renderer

FORMATS = {
    "prores4444": (".mov", ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-vendor", "apl0"]),
    "prores4444xq": (".mov", ["-c:v", "prores_ks", "-profile:v", "4444xq", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-vendor", "apl0"]),
    "png-mov": (".mov", ["-c:v", "png", "-pix_fmt", "rgba"]),
    "h264": (".mp4", ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]),
}


def ffmpeg_bin() -> str:
    """Bundled ffmpeg (imageio-ffmpeg) first, then the system one."""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(cand).exists():
            return cand
    raise FileNotFoundError("ffmpeg nicht gefunden")


def frame_rgba(img: skia.Image) -> bytes:
    """Straight (unpremultiplied) RGBA bytes."""
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType, alphaType=skia.AlphaType.kUnpremul_AlphaType).tobytes()


def render_video(
    renderer: Renderer,
    out_path: str | Path,
    fmt: str = "prores4444",
    start: float | None = None,
    end: float | None = None,
    background: tuple[int, int, int] | None = None,
    audio: list[tuple[Path, float]] | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    m = renderer.mogrt
    fps = m.frame_rate
    start = 0.0 if start is None else start
    end = renderer.duration if end is None else end
    n_frames = max(1, int(round((end - start) * fps)))
    w = int(round(m.width * renderer.scale))
    h = int(round(m.height * renderer.scale))
    suffix, vargs = FORMATS[fmt]
    out_path = Path(out_path)
    if out_path.suffix.lower() != suffix:
        out_path = out_path.with_suffix(suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ffmpeg_bin(), "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-"]
    audio = audio or []
    for path, offset in audio:
        cmd += ["-itsoffset", f"{offset:.4f}", "-i", str(path)]
    filters, maps = [], []
    if background is not None or fmt == "h264":
        bg = background or (0, 0, 0)
        cmd += ["-f", "lavfi", "-i", f"color=c=0x{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}:s={w}x{h}:r={fps}"]
        filters.append(f"[{1 + len(audio)}:v][0:v]overlay=shortest=1:format=auto[v]")
        maps += ["-map", "[v]"]
    else:
        maps += ["-map", "0:v"]
    if len(audio) == 1:
        maps += ["-map", "1:a"]
    elif len(audio) > 1:
        ins = "".join(f"[{i + 1}:a]" for i in range(len(audio)))
        filters.append(f"{ins}amix=inputs={len(audio)}:normalize=0[a]")
        maps += ["-map", "[a]"]
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    cmd += maps
    if audio:
        cmd += ["-c:a", "pcm_s24le" if suffix == ".mov" else "aac", "-t", f"{n_frames / fps:.4f}"]
    cmd += vargs + ["-dn", "-map_metadata", "-1", "-r", f"{fps}", str(out_path)]
    from .paths import no_window_flags

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=no_window_flags())
    try:
        for i in range(n_frames):
            if cancel and cancel():
                proc.stdin.close()
                proc.kill()
                raise KeyboardInterrupt("abgebrochen")
            t = start + i / fps
            img = renderer.render_frame(t)
            proc.stdin.write(frame_rgba(img))
            if progress:
                progress(i + 1, n_frames)
        proc.stdin.close()
        rc = proc.wait()
    except BrokenPipeError:
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg ist mit Code {rc} abgebrochen")
    return out_path


def save_png(img: skia.Image, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), skia.kPNG)
    return path


def collect_audio(renderer: Renderer) -> list[tuple[Path, float]]:
    """Audio footage layers of the main comp (and nested comps) with their comp offsets."""
    out: list[tuple[Path, float]] = []

    def walk(comp, offset: float, depth: int) -> None:
        if depth > 8:
            return
        for layer in comp.layers:
            src = getattr(layer, "source", None)
            if src is None or not getattr(layer, "audio_enabled", True):
                continue
            kind = type(src).__name__
            if kind == "CompItem":
                if getattr(src, "has_audio", False):
                    walk(src, offset + layer.start_time, depth + 1)
                continue
            if kind == "FootageItem" and getattr(src, "has_audio", False) and not getattr(src, "has_video", False):
                hit = renderer.find_footage_file(src)
                if hit is not None:
                    start = offset + layer.start_time
                    if renderer.time_map is not None and depth == 0:
                        start = renderer.time_map.inverse(max(0.0, start)) + min(0.0, start)
                    out.append((hit, start))

    walk(renderer.mogrt.main_comp, 0.0, 0)
    return out
