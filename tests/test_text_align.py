"""Text: exact sourceRectAtTime bounds, explicitly empty texts and optical left alignment (align_text)."""

import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import skia

from mogrt_converter import cli, load
from mogrt_converter.batch import build_rows, read_csv
from mogrt_converter.mcp_server import _rows
from mogrt_converter.mogrt import Control
from mogrt_converter.render.compositor import Renderer
from mogrt_converter.render.text import font_data

# The template the misalignment was reported on (not in the repository, read only)
IWB_CALLOUT = Path(os.environ.get(
    "MOGRT_IWB_CALLOUT", "/Volumes/Media 4TB/Video/2026/IWB Schulungsclips Data & Digital Leadership/"
                         "Media Files/IWB Brandhub/IWB Video Call-Out.mogrt"))
IWB_PAIRS = [("WELCHER NUTZEN?", "WIE GEMESSEN?"), ("KONSEQUENT", "MESSEN & LERNEN"),
             ("ALLES DAZU IM SAP", "MELDEPFLICHTIGE\nANLAGEN"), ("DRANBLEIBEN", "TROTZ AUFSCHUB")]


def make_text_mogrt(out: Path) -> Path:
    """Bold title over a two-line regular subtitle, both at the same x, plus an indented line.

    All text layers hang on a null whose scale animates 100 % -> 200 %, so the alignment has to
    scale with the layers. Glyph origins line up (like in AE), the ink edges do not: 'D' has a
    wide left side bearing, 'T' and 'W' almost none.
    """
    import py_aep

    app = py_aep.new()
    comp = app.project.root_folder.add_comp("Align", 1600, 900, 1.0, 2.0, 25)
    null = comp.add_null()
    null.transform.property("ADBE Position").value = [100, 100, 0]
    sc = null.transform.property("ADBE Scale")
    sc.set_value_at_time(0.0, [100, 100, 100])
    sc.set_value_at_time(2.0, [200, 200, 100])

    def text(s, font, pos):
        layer = comp.add_text(s)
        prop = layer.property("ADBE Text Properties").property("ADBE Text Document")
        doc = prop.value
        doc.font, doc.font_size = font, 80
        prop.value = doc
        layer.transform.property("ADBE Position").value = [pos[0], pos[1], 0]
        layer.parent = null
        return layer

    text("INDENT", "Montserrat-Regular", (400, 360))  # 300 px to the right: must stay put
    text("TROTZ\rWIE", "Montserrat-Regular", (100, 200))
    text("DRAN", "Montserrat-Bold", (100, 100))  # added last = topmost layer = reference

    buf = io.BytesIO()
    aep = out.with_suffix(".aep")
    app.project.save(str(aep))
    with zipfile.ZipFile(buf, "w") as z:
        z.write(aep, "Align.aep")
    aep.unlink()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("definition.json", json.dumps({"authorApp": "aefx", "capsuleName": "Align", "clientControls": []}))
        z.writestr("project.aegraphic", buf.getvalue())
    return out


def ink_bands(img: skia.Image, dark_text: bool = False) -> list[tuple[int, float]]:
    """(first row, left ink edge with subpixel interpolation) of each horizontal band of text."""
    a = img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType,
                    alphaType=skia.AlphaType.kUnpremul_AlphaType).astype(float) / 255
    cov = a[..., 3]
    if dark_text:  # black text on a light background, ignore the blue IWB hexagon
        cov = cov * (1 - a[..., :3].max(-1))
        cov[a[..., 2] - a[..., 0] > 0.3] = 0
    rows = np.nonzero((cov > 0.5).any(1))[0]
    bands, start = [], rows[0]
    for p, q in zip(rows, rows[1:]):
        if q - p > 3:
            bands.append((start, p))
            start = q
    bands.append((start, rows[-1]))
    out = []
    for r0, r1 in bands:
        d = cov[r0:r1 + 1].max(0)
        c = int(np.nonzero(d >= 0.5)[0][0])
        out.append((int(r0), c - (d[c] - 0.5) / max(d[c] - d[c - 1], 1e-6)))
    return out


@pytest.fixture(scope="module")
def align_mogrt(tmp_path_factory):
    return load(make_text_mogrt(tmp_path_factory.mktemp("align") / "align.mogrt"))


def test_source_rect_uses_exact_outline_bounds(align_mogrt):
    """sourceRectAtTime().left is the outline's left edge like in AE (4.14 at 50 px for Bold 'D'
    measured in AE 2025), not skia's padded integer glyph mask bounds."""
    r = Renderer(align_mogrt)
    title = next(l for l in align_mogrt.main_comp.layers if l.name == "DRAN")
    fd = font_data("Montserrat-Bold")
    f = skia.Font(fd.typeface, 80)
    lsb = f.getPath(f.unicharToGlyph(ord("D"))).computeTightBounds().left()
    assert r.source_rect(title, 1.0, False)["left"] == pytest.approx(lsb, abs=1e-3)
    assert lsb / 80 * 50 == pytest.approx(4.14, abs=0.02)


def test_default_keeps_glyph_origins_like_ae(align_mogrt):
    bands = ink_bands(Renderer(align_mogrt).render_frame(0.0))
    assert len(bands) == 4
    (_, dran), (_, trotz), (_, wie), (_, indent) = bands
    assert dran - trotz > 3 and dran - wie > 2  # the side bearings show


@pytest.mark.parametrize("t", [0.0, 1.0, 1.96])
def test_ink_left_aligns_every_line(align_mogrt, t):
    r = Renderer(align_mogrt)
    r.set_text_alignment("ink-left")
    base = ink_bands(Renderer(align_mogrt).render_frame(t))
    (_, dran), (_, trotz), (_, wie), (_, indent) = ink_bands(r.render_frame(t))
    # pixel edges of a pointed 'W' read up to ~0.3 px late at 2x scale; unaligned they differ by 3-10 px
    assert trotz == pytest.approx(dran, abs=0.5)
    assert wie == pytest.approx(dran, abs=0.5)
    assert indent == pytest.approx(base[3][1], abs=0.05)  # beyond the tolerance: untouched
    assert dran == pytest.approx(base[0][1], abs=0.05)  # the reference layer does not move


def test_ink_left_offset_is_constant_in_layer_space(align_mogrt):
    r = Renderer(align_mogrt)
    r.set_text_alignment("ink-left")
    r.render_frame(0.0)
    first = {k: list(v) for k, v in r.text.line_shift.items()}
    r.render_frame(1.5)
    assert r.text.line_shift == first and len(first) == 2  # title (0 shift) and subtitle
    r.set_text_alignment("none")
    assert r.text.line_shift == {} and r.text_align is None
    with pytest.raises(ValueError):
        r.set_text_alignment("optical")


def test_values_and_cli_reach_the_renderer(align_mogrt, tmp_path, capsys):
    out = tmp_path / "s.png"
    cli.main(["still", str(align_mogrt.path), "-t", "0", "--align-text", "ink-left", "-o", str(out), "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] and out.exists()


# ------------------------------------------------------------------ explicitly empty texts

def _fake(*controls):
    return SimpleNamespace(controls=list(controls), name="Fake")


TITEL = Control("t1", "text", "Titel", "Lorem ipsum")
UNTER = Control("t2", "text", "Untertitel", "Delit simmit")
FARBE = Control("c1", "color", "Farbe", [1, 0, 0, 1])


def test_mcp_rows_keep_empty_text():
    m = _fake(TITEL, UNTER, FARBE)
    rows = _rows(m, [{"values": {"Titel": "", "Untertitel": ""}}, {"values": {"Titel": "A", "Untertitel": None}}, {}])
    assert rows[0].values == {"t1": "", "t2": ""}  # explicit "": no text
    assert rows[1].values == {"t1": "A"}  # null: default
    assert rows[2].values == {} and len(rows) == 3


def test_json_and_csv_rows_keep_empty_text(tmp_path):
    p = tmp_path / "rows.json"
    p.write_text(json.dumps([{"Titel": "", "Untertitel": None, "Farbe": ""}, {"filename": "x"}]))
    records = cli._read_rows(p)
    assert records == [{"Titel": "", "Farbe": ""}, {"filename": "x"}]
    table = build_rows(_fake(TITEL, UNTER, FARBE), records, keep_empty=True)
    assert [r.values for r in table.rows] == [{"t1": ""}, {}]  # empty colour cell: default
    csv = "Titel;Untertitel;Farbe\r\n;Leitung;\r\nAnna;;#00FF00\r\n;;\r\n"
    table = build_rows(_fake(TITEL, UNTER, FARBE), read_csv(csv))
    assert len(table.rows) == 2  # the blank line is skipped
    assert table.rows[0].values == {"t1": "", "t2": "Leitung"} and table.rows[0].label == "Leitung"
    assert table.rows[1].values["t2"] == ""


def test_cli_set_empty_text():
    args = SimpleNamespace(values=None, set=["Titel="])
    assert cli._parse_values(args, SimpleNamespace(control=lambda k: TITEL)) == {"t1": ""}


# ------------------------------------------------------------------ the reported template

@pytest.fixture(scope="module")
def iwb():
    if not IWB_CALLOUT.exists():
        pytest.skip("IWB Video Call-Out.mogrt not available")
    return load(IWB_CALLOUT)


def test_iwb_empty_text_renders_nothing(iwb):
    import base64

    from mogrt_converter.mcp_server import Server

    row = _rows(iwb, [{"values": {"Titel": "", "Untertitel": ""}}])[0]
    r = Renderer(iwb, row.values, scale=0.25)
    for layer in iwb.main_comp.layers:
        if type(layer).__name__ == "TextLayer":
            assert r.ev.value(layer.text.source_text, 2.4).text == ""
            assert r.text.layout(layer, 2.4)[0].glyphs == []
    out = io.StringIO()
    Server(out)._call(1, {"name": "preview_still", "arguments": {
        "template": str(IWB_CALLOUT), "values": {"Titel": "", "Untertitel": ""}, "time": 2.4, "align_text": "ink-left"}})
    res = json.loads(out.getvalue().splitlines()[-1])["result"]
    assert not res.get("isError"), res
    img = skia.Image.MakeFromEncoded(base64.b64decode(res["content"][0]["data"]))
    a = img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType).astype(int)
    assert ((a[..., 3] > 128) & (a[..., :3].max(-1) < 60)).sum() == 0  # no black text at all


@pytest.mark.parametrize("size", [68, 100])
def test_iwb_matches_after_effects_without_alignment(iwb, size):
    """Title vs. subtitle left ink edge as After Effects 2025 reports it (sourceRectAtTime + toComp
    on this template, frame 60): AE aligns glyph origins, so the template itself is not flush."""
    ae = {68: [3.464, 0.151, 4.970, 4.884], 100: [5.094, 0.221, 7.309, 7.182]}[size]
    for (ti, su), want in zip(IWB_PAIRS, ae):
        r = Renderer(iwb, {"Schriftgrösse": [size, size], "Titel": ti, "Untertitel": su})
        r.ev.clear_cache()
        lefts = []
        for layer in iwb.main_comp.layers:
            if type(layer).__name__ == "TextLayer":
                w = r.world_matrix(layer, 2.4)
                lefts.append(w[0, 0] * r.text.bounds(layer, 2.4).left() + w[0, 3])
        assert lefts[0] - lefts[1] == pytest.approx(want, abs=0.05)


@pytest.mark.parametrize("size", [68, 100])
def test_iwb_ink_left_within_one_pixel(iwb, size):
    for ti, su in IWB_PAIRS:
        r = Renderer(iwb, {"Schriftgrösse": [size, size], "Titel": ti, "Untertitel": su})
        r.set_text_alignment("ink-left")
        bands = ink_bands(r.render_frame(2.4), dark_text=True)  # frame 60, 2000 x 2000
        assert len(bands) == 2 + su.count("\n")
        for _, x in bands[1:]:
            assert abs(x - bands[0][1]) <= 1.0, (ti, su, bands)
