"""Instructions for AI agents (Claude Code skill, AGENTS.md) on how to drive the command line."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from . import paths

SKILL = """\
---
name: mogrt-converter
description: Fill in and render After Effects Motion Graphics Templates (.mogrt) – lower thirds ("Bauchbinden"), title cards, call-outs, outros – as ProRes 4444 clips with alpha for DaVinci Resolve, using the MOGRT Converter command line. Use when someone asks for lower thirds, name inserts, titles or other graphics from a .mogrt or from their MOGRT Converter library, also in batches (one clip per person).
---

# MOGRT Converter

Renders `.mogrt` templates without Adobe software. Command:

    {cmd}

Always pass `--json`: the result is one JSON object on stdout, progress and warnings go to
stderr. On failure the exit code is 1 and stdout is `{{"ok": false, "error": "..."}}`.
Messages in the error text are German.

## Workflow

1. **Find the template.** `{cmd} list --json` lists the app's library: `templates[].name`,
   `path`, `collections` (e.g. one collection per client) and the app's render folder
   `output_dir`. Narrow down with `--collection NAME`. Wherever a template is expected you can
   pass its path or its library name (exact, or a unique part of it).
   Pick the template that matches the aspect ratio the user needs (16:9 vs 9:16 is usually in
   the name; `info` shows `width`/`height`).
2. **Read its controls.** `{cmd} info TEMPLATE --json` → `controls[]` with `name`, `type`,
   `default` and `column` (the key for batch rows), plus `duration`, `fps`,
   `protected_regions` and `fonts[].available`. Only change the controls the user asked for;
   leave the rest at their defaults, they carry the brand's look.
3. **Check one frame before rendering video.** Render a still at a moment where the graphic is
   fully visible (after the intro, e.g. the end of the first protected region or the middle):
   `{cmd} still TEMPLATE --set "Titel=Anna Meier" -t 2 -o /tmp/check.png --json`
   and look at the PNG. Check that long names or titles fit and do not overflow or get cut off.
   If text is too long, tell the user and suggest a shorter version or a line break (`\\n`)
   instead of silently changing their text.
4. **Render.**
   - One clip: `{cmd} render TEMPLATE --set "NAME=VALUE" ... -o OUTPUT_DIR/FILENAME --json`
     (the extension is added automatically; returns `output`).
   - Many clips: write a JSON file with one object per clip, keys = the `column` values from
     `info`, plus optional `filename` and `duration`:
     `[{{"filename": "LT Anna Meier", "Titel": "Anna Meier", "Untertitel": "Leiterin Kommunikation"}}]`
     then `{cmd} batch TEMPLATE rows.json -o OUTPUT_DIR --json` (returns `outputs`).
     A CSV with the same columns works too (`batch TEMPLATE --template rows.csv` writes one).
   Unless the user names a folder, render into `output_dir` from `list --json`: the
   "MOGRT Renders importieren" script in DaVinci Resolve (Workspace → Scripts) imports new clips
   from there into the "MOGRTs" bin.
5. **Report** the output paths and any `warnings` (unsupported effects, missing fonts).

## Values

- Text: as is; `\\n` for a line break.
- Checkbox: `ja`/`nein` (or true/false in JSON rows).
- Colour: `#RRGGBB`. Point: `960 540`. Slider/angle: a number within `min`–`max`.
- Duration (`-d` or the `duration` key): `8`, `8.5`, `00:00:08:12` or `200f`. Intro and outro
  (`protected_regions`) keep their timing, only the hold in the middle changes.

## Options

- `-f prores4444` (default, alpha), `prores4444xq`, `png-mov`, `h264` (no alpha, for previews).
- Motion like in Premiere: `--offset 0,-300` (300 px higher), `--position X,Y`,
  `--motion-scale 90`, `--rotation`, `--opacity`.
- `--scale 0.5` renders at half resolution (quick previews only).
- Missing fonts: `{cmd} fonts TEMPLATE --download` fetches free ones from Google Fonts.
  Commercial fonts must be installed by the user; until then a substitute is used, so tell them.
"""


def command_argv() -> list[str]:
    """How to start the command line of this installation (absolute: agents run without our venv)."""
    if paths.FROZEN:
        twin = Path(sys.executable).with_name("mogrt.exe")  # Windows: the GUI exe has no stdout
        return [str(twin)] if sys.platform == "win32" and twin.exists() else [sys.executable]
    script = Path(sys.executable).parent / ("mogrt.exe" if sys.platform == "win32" else "mogrt")
    exe = str(script) if script.exists() else shutil.which("mogrt")
    return [exe] if exe else [sys.executable, "-m", "mogrt_converter.cli"]


def command() -> str:
    """command_argv() for a shell."""
    return " ".join(shlex.quote(a) for a in command_argv())


MCP_NAME = "mogrt-converter"


def mcp_entry() -> dict:
    argv = command_argv()
    return {"command": argv[0], "args": argv[1:] + ["mcp"]}


def mcp_config() -> str:
    """Entry for claude_desktop_config.json (and other clients with the same mcpServers format)."""
    return json.dumps({"mcpServers": {MCP_NAME: mcp_entry()}}, indent=2, ensure_ascii=False)


def claude_code_command() -> str:
    return f"claude mcp add --scope user {MCP_NAME} -- {command()} mcp"


def claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def _read_desktop_config() -> dict:
    p = claude_desktop_config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"{p} ist kein gültiges JSON ({e}). Bitte von Hand korrigieren.") from None
    if not isinstance(data, dict):
        raise ValueError(f"{p} hat ein unerwartetes Format")
    return data


def install_claude_desktop() -> Path:
    """Add (or update) our server in Claude Desktop's config; other entries stay untouched."""
    p = claude_desktop_config_path()
    data = _read_desktop_config()
    data.setdefault("mcpServers", {})[MCP_NAME] = mcp_entry()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def setup_status() -> dict:
    """What the app's "Mit KI arbeiten" dialog shows."""
    desktop = claude_desktop_config_path()
    try:
        entry = _read_desktop_config().get("mcpServers", {}).get(MCP_NAME)
    except ValueError:
        entry = None
    skill = skill_path()
    return {
        "claude_desktop": {"installed": entry == mcp_entry(), "outdated": entry is not None and entry != mcp_entry(),
                           "present": desktop.parent.is_dir(), "config_path": str(desktop)},
        "skill": {"installed": skill.exists() and skill.read_text(encoding="utf-8") == skill_text(), "path": str(skill)},
        "claude_code_command": claude_code_command(),
        "mcp_config": mcp_config(),
        "skill_text": skill_text(),
    }


def skill_text() -> str:
    return SKILL.format(cmd=command())


def skill_path(root: Path | None = None) -> Path:
    return (root or Path.home() / ".claude" / "skills") / "mogrt-converter" / "SKILL.md"


def install(root: Path | None = None) -> Path:
    dest = skill_path(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(skill_text(), encoding="utf-8")
    return dest
