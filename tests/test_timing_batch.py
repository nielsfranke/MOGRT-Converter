"""Duration change (protected regions) and batch table parsing."""

from pathlib import Path

import numpy as np
import pytest
import skia

from mogrt_converter import load
from mogrt_converter.batch import build_rows, output_names, parse_value, read_csv, template_csv
from mogrt_converter.render.compositor import Renderer
from mogrt_converter.timing import TimeMap, parse_duration

SAMPLES = Path(__file__).parent.parent / "samples"
# a lower third with protected regions (intro 0–1.52 s, outro 2.8–4.28 s); not in the repo, see test_samples.py
BAUCHBINDE = next(iter(sorted(SAMPLES.glob("*Bauchbinde (links unten)*.mogrt"))), SAMPLES / "missing.mogrt")


def test_timemap_keeps_protected_regions():
    tm = TimeMap(4.36, 8.0, [(0.0, 1.52), (2.8, 4.28)])
    assert tm(1.0) == pytest.approx(1.0)              # intro untouched
    assert tm(8.0) == pytest.approx(4.36)             # ends at the end
    out_start = tm.inverse(2.8)
    assert tm(out_start + 0.7) == pytest.approx(3.5)  # outro plays at normal speed
    assert TimeMap(5.0, 10.0, []).points == [(0.0, 0.0), (10.0, 5.0)]  # no regions: stretch


@pytest.mark.parametrize("text,sec", [("8", 8), ("8,5", 8.5), ("8.5s", 8.5), ("00:00:08:12", 8.48), ("200f", 8), ("1:30", 90)])
def test_parse_duration(text, sec):
    assert parse_duration(text, 25) == pytest.approx(sec)


@pytest.mark.skipif(not BAUCHBINDE.exists(), reason="sample missing")
def test_protected_regions_from_markers_and_render():
    m = load(BAUCHBINDE)
    assert m.protected_regions == [(0.0, pytest.approx(1.52)), (2.8, pytest.approx(4.28))]
    a, b = Renderer(m, scale=0.2), Renderer(m, scale=0.2)
    b.set_duration(8.0)
    assert b.duration == 8.0
    px = lambda r, t: r.render_frame(t).toarray(colorType=skia.ColorType.kRGBA_8888_ColorType).astype(int)
    assert np.abs(px(a, 1.0) - px(b, 1.0)).mean() < 0.05
    assert np.abs(px(a, 4.2) - px(b, 7.84)).mean() < 0.05


@pytest.mark.skipif(not BAUCHBINDE.exists(), reason="sample missing")
def test_batch_csv_roundtrip():
    m = load(BAUCHBINDE)
    tpl = template_csv(m)
    assert tpl.startswith("﻿Dateiname;Titel;Untertitel;Dauer")
    csv = "Dateiname;Titel;Untertitel;Dauer;Foo\r\nbb_a;Jonas Weiß;Leitung;5;x\r\n;Lea Schäfer;\"Zeile 1\nZeile 2\";00:00:07:00;\r\n;;;;\r\n"
    table = build_rows(m, read_csv(csv.encode("cp1252")))
    assert len(table.rows) == 2 and table.unknown_columns == ["Foo"] and not table.errors
    titel = m.control("Titel")
    assert table.rows[0].values[titel.id] == "Jonas Weiß"
    assert table.rows[1].duration == "00:00:07:00"
    assert output_names(m, table.rows) == ["bb_a", f"{m.name} - Lea Schäfer"]


def test_parse_values_by_type():
    from mogrt_converter.mogrt import Control

    assert parse_value(Control("1", "color", "C", [0, 0, 0, 1]), "#FF8000")[:3] == pytest.approx([1, 128 / 255, 0])
    assert parse_value(Control("2", "point", "P", [0, 0]), "960 540") == [960, 540]
    assert parse_value(Control("3", "checkbox", "B", False), "ja") is True
    assert parse_value(Control("4", "slider", "S", 0), "12,5") == 12.5
    assert parse_value(Control("5", "scale", "Sc", [100, 100]), "120") == [120, 120]


@pytest.mark.skipif(not BAUCHBINDE.exists(), reason="sample missing")
def test_motion_moves_graphic():
    m = load(BAUCHBINDE)
    r = Renderer(m, scale=0.25)

    def top(img):
        a = img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType)[..., 3]
        return int(np.nonzero(a > 20)[0].min())

    base = top(r.render_frame(2.4))
    r.set_motion({"position": [540, 960 - 400]})
    assert base - top(r.render_frame(2.4)) == pytest.approx(100, abs=2)  # 400 px at 1/4 scale
    r.set_motion({"position": [540, 960], "scale": 100, "rotation": 0, "opacity": 100})
    assert r.motion is None  # default motion is a no-op
