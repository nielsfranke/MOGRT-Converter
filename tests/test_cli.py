"""Command line for scripts and AI agents: library names, --json output, JSON batch rows."""

import json

import pytest

from mogrt_converter import cli, library


def run(capsys, *argv):
    cli.main(list(argv))
    return json.loads(capsys.readouterr().out)


def test_list_json(lib, capsys):
    d = run(capsys, "list", "--json")
    assert [t["name"] for t in d["templates"]] == ["Kunde A Bauchbinde 16x9", "Kunde A Bauchbinde 9x16"]
    assert d["output_dir"] == str(lib / "out")
    d = run(capsys, "list", "--collection", "kunde", "--json")
    assert [t["collections"] for t in d["templates"]] == [["Kunde A"]]


def test_resolve_by_name(lib):
    assert library.resolve("kunde a bauchbinde 9x16").name == "Kunde A Bauchbinde 9x16.mogrt"
    assert library.resolve("16x9").name == "Kunde A Bauchbinde 16x9.mogrt"  # unique part of the name
    with pytest.raises(SystemExit, match="nicht eindeutig"):
        library.resolve("Bauchbinde")
    with pytest.raises(SystemExit, match="Keine Vorlage"):
        library.resolve("Outro")


def test_info_and_still_json(lib, capsys, tmp_path):
    d = run(capsys, "info", "16x9", "--json")
    assert d["fps"] == 25 and d["duration"] == pytest.approx(1.0)
    out = tmp_path / "f.png"
    d = run(capsys, "still", "16x9", "-t", "0.5", "--scale", "0.2", "-o", str(out), "--json")
    assert d["ok"] and out.exists()
    assert any(w.startswith("Font ") for w in d["warnings"])  # the sample uses a font that is not installed


def test_errors_as_json(lib, capsys):
    with pytest.raises(SystemExit):
        cli.main(["info", "Bauchbinde", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] is False and "nicht eindeutig" in d["error"]


def test_batch_rows_from_json(tmp_path):
    p = tmp_path / "rows.json"
    p.write_text(json.dumps([{"Titel": "Anna Meier", "Sichtbar": True, "Größe": 80, "filename": None}]))
    assert cli._read_rows(p) == [{"Titel": "Anna Meier", "Sichtbar": "ja", "Größe": "80", "filename": ""}]
