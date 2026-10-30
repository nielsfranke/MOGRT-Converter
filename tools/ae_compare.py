"""Compare converter frames with After Effects for AE-based .mogrt files (macOS, AE installed).

    python tools/ae_compare.py out/ae file1.mogrt file2.mogrt ...

AE opens each template's project (dialogs suppressed), applies the .mogrt's Essential Graphics
defaults and saves 5 frames with saveFrameToPng; the converter renders the same times. Writes
results.json (mean abs. difference / share of pixels off by >10 % per frame) and a contact sheet
per template (AE | converter | difference). AE needs "Allow Scripts to Write Files and Access
Network" enabled. Caveats: AE without the template's fonts substitutes them, an English AE
fails on expressions that use German effect parameter names, wiggle() never matches.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mogrt_converter import load
from mogrt_converter.render.compositor import Renderer

FRACS = [0.15, 0.35, 0.5, 0.65, 0.85]
AE_APP = os.environ.get("AE_APP", "Adobe After Effects 2025")

JSX = r"""
app.beginSuppressDialogs();
var out = new File(%(log)s); out.open("w"); out.encoding = "UTF-8";
try {
  if (app.project) app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
  var p = app.open(new File(%(aep)s));
  var comp = null, best = 1e9;
  for (var i = 1; i <= p.numItems; i++) { var c = p.item(i);
    if (!(c instanceof CompItem) || c.name != %(name)s) continue;
    var d = Math.abs(c.width - %(w)d) + Math.abs(c.height - %(h)d) + Math.abs(c.duration - %(dur)f);
    if (d < best) { best = d; comp = c; } }
  if (!comp) throw new Error("comp not found");
  comp.resolutionFactor = [1, 1];
  // Essential Graphics defaults of the .mogrt (what Premiere and the converter use)
  var ctrls = %(ctrls)s, applied = 0, notes = [];
  for (var ci = 0; ci < ctrls.length; ci++) {
    var C = ctrls[ci];
    try {
      var cc = p.itemByID(C.comp), L = null;
      for (var li = 1; li <= cc.numLayers; li++) if (cc.layer(li).id == C.layer) L = cc.layer(li);
      var node = L;
      for (var pi = 0; pi < C.path.length; pi++) {
        var mn = C.path[pi][0], occ = C.path[pi][1], hit = null, seen = 0;
        for (var q = 1; q <= node.numProperties; q++) if (node.property(q).matchName == mn) { if (seen == occ) { hit = node.property(q); break; } seen++; }
        node = hit;
      }
      while (node.numKeys > 0) node.removeKey(1);
      if (C.type == "text") { var d = node.value; d.text = C.value; node.setValue(d); }
      else { var v = C.value; if (v instanceof Array && node.value instanceof Array) { var cur = node.value; for (var z = v.length; z < cur.length; z++) v.push(cur[z]); v = v.slice(0, cur.length); } node.setValue(v); }
      applied++;
    } catch (ce) { notes.push(C.name + ": " + ce.toString()); }
  }
  var times = %(times)s;
  for (var k = 0; k < times.length; k++) {
    var f = new File(%(png)s + k + ".png");
    comp.saveFrameToPng(times[k], f);
    for (var wt = 0; wt < 600 && !f.exists; wt++) $.sleep(100);  // async: wait for the file
    $.sleep(300);
  }
  var fonts = [];
  try { var mf = app.fonts.missingOrSubstitutedFonts; for (var f = 0; f < mf.length; f++) fonts.push(mf[f].postScriptName); } catch (e2) {}
  out.writeln("OK ctrls=" + applied + "/" + ctrls.length + (notes.length ? " (" + notes.join("; ") + ")" : "") + " missing_fonts=" + fonts.join(","));
} catch (e) { out.writeln("ERR " + e.toString() + " line " + e.line); }
out.close();
try { app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); } catch (e3) {}
app.endSuppressDialogs(false);
"""


def js(s):
    return json.dumps(str(s))


def controls_for_ae(m):
    """Essential Graphics defaults as (comp id, layer id, match-name path) for the JSX side."""
    from mogrt_converter.evaluator import Evaluator, owning_layer
    ev = Evaluator(m)
    ctrls = m.controllers()
    out = []
    for c in m.controls:
        ctl = ctrls.get(c.id)
        if not c.editable or ctl is None:
            continue
        prop = ev._resolve_controller(ctl)
        if prop is None:
            continue
        layer = owning_layer(prop)
        path, node = [], prop
        while node is not layer:
            par = node.parent_property
            same = [k for k in par if k.match_name == node.match_name]
            path.insert(0, [node.match_name, next(i for i, k in enumerate(same) if k is node)])
            node = par
        val = c.default
        if c.type == "text":
            val = str(val).replace("\n", "\r")
        elif c.type == "checkbox":
            val = 1 if val else 0
        elif c.type == "color":
            val = list(val)[:3]
        out.append({"comp": int(ctl.source_comp_id), "layer": int(ctl.source_layer_id), "path": path,
                    "type": c.type, "value": val, "name": c.name})
    return out


def ae_frames(m, aep: Path, work: Path, times):
    log = work / "ae.log"
    log.unlink(missing_ok=True)
    jsx = work / "run.jsx"
    jsx.write_text(JSX % dict(log=js(log), aep=js(aep), name=js(m.main_comp.name), w=m.width, h=m.height,
                              dur=m.duration, times=json.dumps(times), ctrls=json.dumps(controls_for_ae(m)), png=js(work / "ae_")), encoding="utf-8")
    cmd = ["osascript", "-e", "with timeout of 600 seconds",
           "-e", f'tell application "{AE_APP}" to DoScriptFile "{jsx}"', "-e", "end timeout"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=700)
    for _ in range(60):  # saveFrameToPng may finish a moment later
        if log.exists() and log.read_text().strip() and all((work / f"ae_{k}.png").exists() for k in range(len(times))):
            break
        time.sleep(1)
    return log.read_text().strip() if log.exists() else f"no log ({r.stderr.strip()})"


def load_ae(path: Path) -> np.ndarray:
    """AE's saveFrameToPng stores premultiplied colour in the PNG: unpremultiply."""
    a = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)
    al = a[..., 3:] / 255
    rgb = np.where(al > 0, np.clip(a[..., :3] / np.maximum(al, 1e-6), 0, 255), 0)
    return np.concatenate([rgb, a[..., 3:]], -1).round().astype(np.uint8)


def premul(a):
    a = a.astype(np.float32) / 255
    return np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], -1)


def compare(ae_png: Path, ours: np.ndarray):
    ae = load_ae(ae_png)
    if ae.shape != ours.shape:
        return {"error": f"size {ae.shape} vs {ours.shape}"}
    pa, po = premul(ae), premul(ours)
    d = np.abs(pa - po).max(-1)
    cover = (pa[..., 3] > 0.02) | (po[..., 3] > 0.02)
    n = max(int(cover.sum()), 1)
    return {
        "mad": float(d[cover].mean() * 255) if cover.any() else 0.0,  # mean abs diff in covered area
        "bad": float((d[cover] > 0.1).sum() / n),                      # share of pixels off by >10%
        "alpha_ae": float(pa[..., 3].mean()), "alpha_ours": float(po[..., 3].mean()),
    }


def sheet(rows, path: Path, w=360):
    tiles = []
    for ae_png, ours in rows:
        ims = []
        for im in (Image.fromarray(load_ae(ae_png)) if ae_png.exists() else Image.new("RGBA", (w, w)),
                   Image.fromarray(ours)):
            bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
            bg.alpha_composite(im)
            ims.append(bg.convert("RGB"))
        a, o = (np.asarray(x).astype(int) for x in ims)
        diff = Image.fromarray(np.clip(np.abs(a - o).max(-1) * 3, 0, 255).astype(np.uint8)).convert("RGB") \
            if a.shape == o.shape else Image.new("RGB", ims[0].size)
        h = int(w * ims[0].height / ims[0].width)
        tiles.append([x.resize((w, h)) for x in (ims[0], ims[1], diff)])
    H = sum(t[0].height for t in tiles)
    out = Image.new("RGB", (w * 3, H), "white")
    y = 0
    for t in tiles:
        for i, x in enumerate(t):
            out.paste(x, (i * w, y))
        y += t[0].height
    out.save(path, quality=85)


def run_one(mogrt: Path, out: Path):
    name = mogrt.stem
    work = out / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    m = load(mogrt)
    with zipfile.ZipFile(mogrt) as z:
        aeg = [n for n in z.namelist() if n.endswith(".aegraphic")][0]
        z.extract(aeg, work)
    with zipfile.ZipFile(work / aeg) as z:
        z.extractall(work / "aep")
    aep = next((work / "aep").rglob("*.aep"))
    times = [round(m.duration * f * m.frame_rate) / m.frame_rate for f in FRACS]
    ae_log = ae_frames(m, aep, work, times)
    r = Renderer(m)
    res = {"name": name, "file": str(mogrt), "ae": ae_log, "size": [m.width, m.height], "times": times, "frames": []}
    rows = []
    for k, t in enumerate(times):
        ours = r.render_frame(t).toarray()
        Image.fromarray(ours).save(work / f"ours_{k}.png")
        ae_png = work / f"ae_{k}.png"
        res["frames"].append(compare(ae_png, ours) if ae_png.exists() else {"error": "no AE frame"})
        rows.append((ae_png, ours))
    res["warnings"] = sorted(r._warned)
    sheet(rows, out / f"{name}.jpg")
    return res


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for f in sys.argv[2:]:
        t0 = time.time()
        try:
            res = run_one(Path(f), out)
        except Exception as e:
            res = {"name": Path(f).stem, "error": repr(e), "tb": traceback.format_exc()}
        res["secs"] = round(time.time() - t0, 1)
        results.append(res)
        fr = res.get("frames", [])
        print(f"{res['name']}: {res.get('ae', res.get('error'))} "
              + " ".join(f"{x.get('mad', -1):.1f}/{x.get('bad', -1):.3f}" for x in fr), flush=True)
        (out / "results.json").write_text(json.dumps(results, indent=1))
