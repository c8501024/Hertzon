"""The Claude plugin in claude-plugin/ stays consistent with the MCP server it describes.

Skills and commands name MCP tools as `tool_name()`. A tool that is renamed or removed in hertzon/mcp must be
changed there too, or the assistant is told to call a tool that does not exist.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "claude-plugin"


def _docs():
    return sorted(PLUGIN.glob("skills/*/SKILL.md")) + sorted(PLUGIN.glob("commands/*.md"))


def _frontmatter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "missing front matter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def test_manifests():
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "hertzon"
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == "hertzon")
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_front_matter():
    assert _docs()
    for f in _docs():
        fm = _frontmatter(f.read_text(encoding="utf-8"))
        assert fm.get("description"), f
        if f.name == "SKILL.md":
            assert fm.get("name") == f.parent.name, f


def test_named_tools_exist():
    from hertzon.mcp.tools import TOOLS
    names = {t.name for t in TOOLS}
    for f in _docs():
        used = set(re.findall(r"`([a-z_]+)\(\)`", f.read_text(encoding="utf-8")))
        missing = used - names
        assert not missing, f"{f.relative_to(ROOT)} names unknown MCP tools: {sorted(missing)}"
