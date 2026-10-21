"""MCP server: protocol handshake and tools, called in-process."""

import base64
import io
import json

from mogrt_converter.mcp_server import Server


def server():
    out = io.StringIO()
    return Server(out), out


def replies(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def call(name, **arguments):
    s, out = server()
    s._call(1, {"name": name, "arguments": arguments, "_meta": {"progressToken": "t"}})
    msgs = replies(out)
    return msgs[-1]["result"], [m for m in msgs if m.get("method") == "notifications/progress"]


def test_handshake_and_tool_list():
    s, out = server()
    s.serve(io.StringIO("\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "nope"},
    ])))
    init, tools, unknown = replies(out)
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert {t["name"] for t in tools["result"]["tools"]} == {"list_templates", "template_info", "preview_still",
                                                             "render", "render_batch"}
    assert unknown["error"]["code"] == -32601


def test_list_and_info(lib):
    res, _ = call("list_templates", collection="kunde")
    data = json.loads(res["content"][0]["text"])
    assert [t["name"] for t in data["templates"]] == ["Kunde A Bauchbinde 16x9"]
    res, _ = call("template_info", template="16x9")
    assert json.loads(res["content"][0]["text"])["fps"] == 25


def test_errors_are_tool_results(lib):
    res, _ = call("template_info", template="Bauchbinde")
    assert res["isError"] and "nicht eindeutig" in res["content"][0]["text"]
    res, _ = call("render", template="16x9", values={"Titel": "x"})
    assert res["isError"] and "Unknown controls: Titel" in res["content"][0]["text"]


def test_preview_still_returns_png(lib):
    res, _ = call("preview_still", template="16x9", time=0.5)
    img = res["content"][0]
    assert img["type"] == "image" and base64.b64decode(img["data"])[:4] == b"\x89PNG"


def test_render_batch(lib):
    res, progress = call("render_batch", template="16x9", format="h264",
                         rows=[{"filename": "eins"}, {"filename": "zwei", "duration": "2"}])
    outputs = json.loads(res["content"][0]["text"])["outputs"]
    assert [o.rsplit("/", 1)[-1] for o in outputs] == ["eins.mp4", "zwei.mp4"]
    assert outputs[0].startswith(str(lib / "out"))  # the app's render folder
    assert progress and progress[-1]["params"]["progress"] == progress[-1]["params"]["total"]


def test_install_claude_desktop_keeps_other_servers(tmp_path, monkeypatch):
    import pytest

    from mogrt_converter import agent_skill

    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    monkeypatch.setattr(agent_skill, "claude_desktop_config_path", lambda: cfg)
    monkeypatch.setattr(agent_skill, "skill_path", lambda root=None: tmp_path / "skills" / "SKILL.md")
    assert agent_skill.setup_status()["claude_desktop"]["installed"] is False
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    agent_skill.install_claude_desktop()
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["mogrt-converter"]["args"][-1] == "mcp"
    assert agent_skill.setup_status()["claude_desktop"]["installed"] is True
    cfg.write_text("{ kaputt")
    with pytest.raises(ValueError):
        agent_skill.install_claude_desktop()
    assert cfg.read_text() == "{ kaputt"  # never overwrite a config we cannot read
