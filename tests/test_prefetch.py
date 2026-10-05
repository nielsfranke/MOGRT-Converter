"""Synthetic sample renders, and playback prefetch renders all frames on worker processes."""

import time

import pytest

from mogrt_converter import load
from mogrt_converter.app.prefetch import Prefetcher
from mogrt_converter.render.compositor import Renderer

from sample_mogrt import make


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    return make(tmp_path_factory.mktemp("sample") / "sample.mogrt", duration=1.0)


def test_sample_renders(sample):
    m = load(sample)
    r = Renderer(m, scale=0.2)
    arr = r.render_frame(0.9).toarray()
    assert arr[..., 3].max() > 0  # the bar is visible
    assert r.ev._failed == set()


def test_prefetch_renders_all_frames(sample):
    m = load(sample)
    count = round(m.duration * m.frame_rate)
    p = Prefetcher()
    state = {"values": {}, "duration": None, "motion": None, "scale": 0.1}
    try:
        key = p.start(str(m.path), state, m.frame_rate, count)
        deadline = time.time() + 120
        while len(p.cached(key)) < count and time.time() < deadline:
            time.sleep(0.2)
        assert p.cached(key) == list(range(count))
        assert p.get(key, 0)[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        p.forget(str(m.path))
