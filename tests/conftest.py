"""Shared fixtures."""

import json

import pytest

from mogrt_converter import library

from sample_mogrt import make


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """Isolated app config with two templates in a folder and one in a collection."""
    folder = tmp_path / "MOGRTs"
    folder.mkdir()
    a = make(folder / "Kunde A Bauchbinde 16x9.mogrt", duration=1.0)
    make(folder / "Kunde A Bauchbinde 9x16.mogrt", duration=1.0)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"library": [str(folder)], "output_dir": str(tmp_path / "out"),
                               "collections": [{"id": "x", "name": "Kunde A", "items": [str(a.resolve())]}]}))
    monkeypatch.setattr(library, "CONFIG_PATH", cfg)
    return tmp_path
