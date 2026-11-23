"""set_sweep with Count but without Distribution (an AI client's 9-point Discrete sweep, 2.4-2.485 GHz): the
default 'Linear Step' with Step 0.1GHz ignored the Count and solved one frequency, silently. Count now selects
'Linear Count', a property the Distribution does not use is refused, and a range that collapses to one frequency
is warned about by set_sweep, set_setup, validate and the solver."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.mcp import tools  # noqa: E402
from hertzon.mcp.protocol import Server  # noqa: E402


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))      # no live Hertzon window
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    tools.S.default_path = None
    tools.S.designs.clear()
    return Server("hertzon", "test", tools.INSTRUCTIONS, tools.TOOLS)


def raw(srv, _tool, **args):
    return srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": _tool, "arguments": args}})["result"]


def call(srv, _tool, **args):
    res = raw(srv, _tool, **args)
    text = "\n".join(c["text"] for c in res["content"] if c["type"] == "text")
    assert not res["isError"], text
    return json.loads(text)


def error(srv, _tool, **args) -> str:
    res = raw(srv, _tool, **args)
    assert res["isError"]
    return res["content"][0]["text"]


def _project(server, tmp_path):
    path = str(tmp_path / "s.hzn")
    call(server, "create_project", path=path)
    call(server, "create_patch_antenna", frequency_ghz=2.45)
    return path


def test_count_without_distribution_gives_all_points(server, tmp_path):
    from hertzon.model.design import Project
    from hertzon.solver.sweeps import sweep_frequencies
    path = _project(server, tmp_path)
    r = call(server, "set_sweep", name="Band", props={"SweepType": "Discrete", "Start": "2.4GHz", "Stop": "2.485GHz",
                                                      "Count": 9})
    assert r["frequencies"] == 9 and r["range_ghz"] == [2.4, 2.485]
    assert r["props"]["Distribution"] == "Linear Count" and "Count" in r["props"]
    assert "Step" not in r["props"] and "Points" not in r["props"]      # hidden defaults are not shown
    assert "Linear Count" in r["note"] and "warnings" not in r
    d = Project.load(path).design("Patch_2.45GHz")
    f = sweep_frequencies(d.setup("Setup1").sweeps[-1].props, d.evaluator(Project.load(path)))
    assert len(f) == 9 and f[0] == pytest.approx(2.4e9) and f[-1] == pytest.approx(2.485e9)
    # the same through set_setup
    r = call(server, "set_setup", name="Setup1", sweeps=[{"name": "Band2", "props": {
        "SweepType": "Discrete", "Start": "2.4GHz", "Stop": "2.485GHz", "Count": "9"}}])
    band2 = next(w for w in r["sweeps"] if w["name"] == "Band2")
    assert band2["frequencies"] == 9 and band2["props"]["Distribution"] == "Linear Count"


def test_property_of_another_distribution_is_refused(server, tmp_path):
    _project(server, tmp_path)
    msg = error(server, "set_sweep", name="Band", props={"Distribution": "Linear Step", "Start": "2.4GHz",
                                                         "Stop": "2.485GHz", "Count": 9})
    assert "Count" in msg and "Linear Count" in msg
    msg = error(server, "set_sweep", name="Band", props={"Count": 9, "Step": "0.01GHz"})
    assert "different distributions" in msg


def test_range_with_one_frequency_is_warned(server, tmp_path):
    path = _project(server, tmp_path)
    r = call(server, "set_sweep", name="Band", props={"SweepType": "Discrete", "Distribution": "Linear Step",
                                                      "Start": "2.4GHz", "Stop": "2.485GHz"})
    assert r["frequencies"] == 1
    assert len(r["warnings"]) == 1 and "only one frequency" in r["warnings"][0]
    from hertzon.model.design import Project
    from hertzon.model.validate import validate
    p = Project.load(path)
    d = p.design("Patch_2.45GHz")
    checks = validate(p, d)
    assert any("only one frequency" in c.message for c in checks), [(c.level, c.message) for c in checks if c.category == "Analysis Setup"]
    # a step that fits clears it; Single Points with one frequency is meant
    r = call(server, "set_sweep", name="Band", props={"Step": "0.005GHz"})
    assert r["frequencies"] == 18 and "warnings" not in r
    r = call(server, "set_sweep", name="Band", props={"Points": "2.45GHz"})
    assert r["frequencies"] == 1 and r["props"]["Distribution"] == "Single Points" and "warnings" not in r
