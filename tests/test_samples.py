"""Smoke test: every MOGRT in samples/ loads and renders a few frames without errors.

samples/ is not part of the repository (templates have their own licenses): put your own
.mogrt files there to run these tests.
"""

from pathlib import Path

import pytest

from mogrt_converter import load
from mogrt_converter.render.compositor import Renderer

SAMPLES = sorted((Path(__file__).parent.parent / "samples").glob("*.mogrt"))


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_render(path):
    m = load(path)
    r = Renderer(m, scale=0.15)
    for f in (0.25, 0.5, 0.9):
        img = r.render_frame(m.duration * f)
        assert img.width() == round(m.width * 0.15)
    assert r.ev._failed == set(), "expressions failed"


BAUCHBINDE = next((p for p in SAMPLES if "Bauchbinde (links" in p.name), None)


@pytest.mark.skipif(BAUCHBINDE is None, reason="sample missing")
def test_text_override():
    m = load(BAUCHBINDE)
    r = Renderer(m, {"Titel": "Erika Musterfrau"}, scale=0.2)
    layer = m.main_comp.layers[4]
    assert r.ev.value(layer.text.source_text, 2.0).text == "Erika Musterfrau"
