"""Batch-check a folder of MOGRTs: load, render, collect warnings, compare to preview videos.

    python tools/corpus.py testdata/mixkit -o out/corpus      # report.json + report.md + sheets

A MOGRT counts as "ok" when it loads and renders without exceptions or failing expressions.
If a preview video (*.mp4 next to the .mogrt) exists, a similarity score against it is computed.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _preview_frames(mp4: Path, w: int, h: int, fps: float = 8.0) -> np.ndarray | None:
    """All preview frames (sampled at fps), letterboxed to w x h."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(mp4),
           "-vf", f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    r = subprocess.run(cmd, capture_output=True)
    n = len(r.stdout) // (w * h * 3)
    if r.returncode or not n:
        return None
    return np.frombuffer(r.stdout[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)


def _preview_frame(mp4: Path, t: float, w: int, h: int) -> np.ndarray | None:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1",
           "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode or len(r.stdout) != w * h * 3:
        return None
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3)


def check(path: str, out_dir: str) -> dict:
    import skia

    from mogrt_converter import load
    from mogrt_converter.render.compositor import Renderer

    p = Path(path)
    res: dict = {"path": path, "name": p.stem, "ok": False, "warnings": [], "error": None}
    err = io.StringIO()
    t0 = time.time()
    try:
        with contextlib.redirect_stderr(err):
            m = load(p)
            res.update({
                "author": m.definition.get("authorApp"),
                "size": [m.width, m.height], "fps": m.frame_rate, "duration": m.duration,
                "controls": collections.Counter(c.type for c in m.controls),
                "fonts": m.fonts,
            })
            scale = 320 / max(m.width, m.height)
            r = Renderer(m, scale=scale)
            frames = []
            for f in (0.3, 0.5, 0.7):
                img = r.render_frame(m.duration * f)
                frames.append((m.duration * f, img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                                                            alphaType=skia.AlphaType.kPremul_AlphaType)))
            res["failed_expressions"] = len(r.ev._failed)
        res["ok"] = res.get("failed_expressions", 0) == 0
        # compare with preview video (ours composited over black vs. preview frame)
        mp4 = next(iter(p.parent.glob("*.mp4")), None)
        if mp4 is not None:
            scores, tiles = [], []
            h, w = frames[0][1].shape[:2]
            refs = _preview_frames(mp4, w, h)
            for t, arr in frames:
                if refs is None:
                    continue
                alpha = arr[..., 3:4].astype(np.float32) / 255
                best = None
                # previews show the template over black or white (or footage) and are edited clips
                # (other length, offsets): use the best-matching frame and background
                for bg in (0.0, 255.0):
                    ours = arr[..., :3].astype(np.float32) + bg * (1 - alpha)
                    d = np.mean(np.abs(refs.astype(np.float32) - ours[None]), axis=(1, 2, 3)) / 255
                    k = int(np.argmin(d))
                    if best is None or d[k] < best[0]:
                        best = (float(d[k]), k, ours)
                scores.append(best[0])
                tiles.append(np.concatenate([best[2].astype(np.uint8), refs[best[1]]], axis=0))
            if scores:
                res["diff"] = round(float(np.mean(scores)), 4)
                from PIL import Image

                sheet = np.concatenate(tiles, axis=1)
                Path(out_dir, "sheets").mkdir(parents=True, exist_ok=True)
                Image.fromarray(sheet).save(Path(out_dir, "sheets", f"{p.stem}.jpg"), quality=80)
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        res["traceback"] = traceback.format_exc()[-2000:]
    res["seconds"] = round(time.time() - t0, 2)
    warnings = []
    for line in err.getvalue().splitlines():
        line = line.strip()
        if line.startswith("WARNUNG:"):
            warnings.append(line[9:])
    res["warnings"] = sorted(set(warnings))
    return res


def normalize_warning(w: str) -> str:
    w = re.sub(r"'[^']*'", "'…'", w)
    w = re.sub(r"\([^)]*ADBE[^)]*\)", lambda m: m.group(0), w)
    return w


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("-o", "--out", default="out/corpus")
    ap.add_argument("-j", "--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    files = sorted(Path(args.folder).rglob("*.mogrt"))[: args.limit]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with ProcessPoolExecutor(args.jobs) as ex:
        futs = {ex.submit(check, str(f), str(out)): f for f in files}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                results.append(fut.result(timeout=600))
            except Exception as e:
                results.append({"path": str(futs[fut]), "name": futs[fut].stem, "ok": False, "error": f"crash: {e}", "warnings": []})
            if i % 20 == 0:
                print(f"{i}/{len(files)}", file=sys.stderr)
    results.sort(key=lambda r: r["name"])
    (out / "report.json").write_text(json.dumps(results, indent=1, default=dict, ensure_ascii=False))

    # summary
    n = len(results)
    errors = [r for r in results if r.get("error")]
    authors = collections.Counter(r.get("author") for r in results)
    warn_counter = collections.Counter()
    for r in results:
        for w in r["warnings"]:
            warn_counter[normalize_warning(w)] += 1
    err_counter = collections.Counter((r["error"] or "").split(":")[0] + ": " + (r["error"] or "")[:120] for r in errors)
    lines = [f"# Corpus report ({n} MOGRTs)", "",
             f"- ok (rendered, no failed expressions): {sum(r['ok'] for r in results)}",
             f"- errors: {len(errors)}", f"- author apps: {dict(authors)}", ""]
    diffs = [r for r in results if "diff" in r]
    if diffs:
        lines += [f"- median diff vs preview: {np.median([r['diff'] for r in diffs]):.3f}", ""]
    lines += ["## Errors", ""] + [f"- {c}× {e}" for e, c in err_counter.most_common(40)]
    lines += ["", "## Warnings (templates affected)", ""] + [f"- {c}× {w}" for w, c in warn_counter.most_common(80)]
    if diffs:
        lines += ["", "## Largest differences vs preview", ""]
        for r in sorted(diffs, key=lambda r: -r["diff"])[:30]:
            lines.append(f"- {r['diff']:.3f} {r['name']}")
    (out / "report.md").write_text("\n".join(lines))
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
