"""Optional: render every MOGRT in testdata/ (not part of the repo, see tools/corpus.py).

Run with:  MOGRT_CORPUS=1 pytest tests/test_corpus.py
"""

import os
from pathlib import Path

import pytest

from mogrt_converter import load
from mogrt_converter.render.compositor import Renderer

FILES = sorted((Path(__file__).parent.parent / "testdata").rglob("*.mogrt"))
pytestmark = pytest.mark.skipif(not os.environ.get("MOGRT_CORPUS") or not FILES, reason="set MOGRT_CORPUS=1 and add files to testdata/")

BROKEN = {"mixkit-53"}  # archive without After Effects project


@pytest.mark.parametrize("path", FILES, ids=[p.parent.name + "/" + p.stem for p in FILES])
def test_corpus_renders(path):
    if path.stem in BROKEN:
        pytest.skip("known broken file")
    m = load(path)
    r = Renderer(m, scale=0.1)
    r.render_frame(m.duration * 0.5)
