"""MCP server: protocol and the model tools on a project file (no FEM solve)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.mcp import clients, tools  # noqa: E402
from hertzon.mcp.protocol import Server  # noqa: E402


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))      # no live Hertzon window
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    tools.S.default_path = None
    tools.S.designs.clear()
    return Server("hertzon", "test", tools.INSTRUCTIONS, tools.TOOLS)


def call(srv, _tool, **args):
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": _tool, "arguments": args}})
    res = r["result"]
    text = "\n".join(c["text"] for c in res["content"] if c["type"] == "text")
    assert not res["isError"], text
    return json.loads(text)


def test_initialize_and_list(server):
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
    assert r["result"]["protocolVersion"] == "2025-03-26"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert {"create_project", "add_object", "assign", "run_simulation", "get_results"} <= names


def test_build_waveguide(server, tmp_path):
    p = tmp_path / "wg.hzn"
    call(server, "create_project", path=str(p), units="mm")
    call(server, "set_design", variables={"a": "22.86mm", "b": "10.16mm", "L": "30mm"})
    r = call(server, "add_object", kind="CreateBox", name="WG", props={"XSize": "a", "YSize": "L", "ZSize": "b"})
    faces = r["geometry"]["WG"]["faces"]
    assert len(faces) == 6
    end = [f["face"] for f in faces if f["normal"][1] < -0.99][0]
    call(server, "add_object", kind="CreateCylinder", name="Post", material="pec",
         props={"CenterPosition": "a/2,L/2,0", "Radius": "1mm", "Height": "b"})
    r = call(server, "add_operation", object="WG", kind="Subtract", props={"Tools": "Post"})
    assert r["geometry"]["WG"]["faces_total"] == 7
    a = call(server, "assign", category="excitation", kind="Wave Port", faces=[{"object": "WG", "face": end}])
    assert a["port_number"] == "1"
    call(server, "set_setup", props={"Frequency": "10GHz", "MaximumPasses": 2},
         sweeps=[{"name": "S", "props": {"Start": "8GHz", "Stop": "12GHz"}}])
    assert call(server, "validate")["ok"]
    s = call(server, "get_project")
    assert [o["name"] for o in s["objects"]] == ["WG", "Post"]
    assert s["objects"][1]["consumed_by"] == "WG"
    assert s["setups"][0]["sweeps"][0]["props"]["Start"] == "8GHz"


def test_bad_property_is_reported(server, tmp_path):
    call(server, "create_project", path=str(tmp_path / "x.hzn"))
    r = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "add_object", "arguments": {"kind": "CreateBox", "props": {"Width": "1"}}}})
    assert r["result"]["isError"] and "no property 'Width'" in r["result"]["content"][0]["text"]


def test_codex_config_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "a"\n\n[mcp_servers.hertzon]\ncommand = "old"\n'
                   '[mcp_servers.hertzon.env]\nA = "1"\n\n[profiles.p]\nmodel = "y"\n', encoding="utf-8")
    clients.codex_install()
    text = cfg.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.hertzon]") == 1 and '"old"' not in text and "[mcp_servers.hertzon.env]" not in text
    assert "[mcp_servers.other]" in text and "[profiles.p]" in text
    assert clients.codex_status() == "registered"
    clients.codex_remove()
    assert clients.codex_status() == "not registered"
    assert "[profiles.p]" in cfg.read_text(encoding="utf-8")


def test_claude_json_without_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(clients, "_claude_cli", lambda: None)
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"numStartups": 3, "mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    assert clients.claude_status() == "not registered"
    clients.claude_install()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["numStartups"] == 3 and "other" in data["mcpServers"]
    assert data["mcpServers"]["hertzon"]["args"] == ["-m", "hertzon.mcp"]
    assert clients.claude_status() == "registered"
    clients.claude_remove()
    assert clients.claude_status() == "not registered" and "other" in json.loads(cfg.read_text())["mcpServers"]


def test_claude_desktop_config(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "desktop_config", lambda: tmp_path / "Claude" / "claude_desktop_config.json")
    clients.desktop_install()
    assert clients.desktop_status() == "registered"
    clients.desktop_remove()
    assert clients.desktop_status() == "not registered"


def test_auto_register(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(clients, "_claude_cli", lambda: None)
    monkeypatch.setattr(clients, "desktop_config", lambda: tmp_path / "Claude" / "claude_desktop_config.json")
    (tmp_path / "cc").mkdir()
    (tmp_path / "cc" / ".claude.json").write_text("{}", encoding="utf-8")
    assert clients.auto_register() == ["claude"]          # Claude app and Codex are not installed
    assert clients.auto_register() == []                       # already registered
    (tmp_path / "codex").mkdir()
    assert clients.auto_register(skip=("codex",)) == []
    assert clients.auto_register() == ["codex"]


def test_edges_vertices_and_fillet(server, tmp_path):
    call(server, "create_project", path=str(tmp_path / "f.hzn"))
    call(server, "add_object", kind="CreateRectangle", name="Patch", props={"XSize": "10", "YSize": "6"})
    g = call(server, "get_geometry", objects=["Patch"], include_edges=True)["objects"]["Patch"]
    assert len(g["edges"]) == 4 and len(g["vertices"]) == 4
    assert "faces" in g and "edges" not in call(server, "get_geometry")["objects"]["Patch"]
    corner = next(v["vertex"] for v in g["vertices"] if v["position"][:2] == [10, 6])
    r = call(server, "add_operation", object="Patch", kind="Fillet",
             props={"Radius": "2", "Vertices": str(corner)})
    assert r["geometry"]["Patch"]["faces"][0]["area"] < 60 - 0.8      # 4 - pi of the corner cut away


def test_equations_reports_plots_and_studies(server, tmp_path):
    call(server, "create_project", path=str(tmp_path / "s.hzn"))
    call(server, "set_design", variables={"w": "2mm"})
    call(server, "add_object", kind="CreateBox", name="Sub", props={"XSize": "w", "YSize": "5", "ZSize": "1"})
    call(server, "set_setup", props={"Frequency": "5GHz"}, sweeps=[{"name": "Sweep"}])
    # coordinate system: a new one is the working CS of the next objects
    cs = call(server, "set_coordinate_system", props={"Origin": "0,0,1mm"})
    assert cs["working_cs"] == cs["name"] == "RelativeCS1"
    call(server, "add_object", kind="CreateBox", name="Top", props={"XSize": "1", "YSize": "1", "ZSize": "1"})
    call(server, "set_coordinate_system", name="Global", set_working=True)
    # equations, with import and export
    e = call(server, "set_equation", name="RL", expr="-dB(S(1,1))", functions=["sq(x) = x*x"])
    assert e["changed"] == ["RL", "sq()"]
    call(server, "set_equation", equations=[{"expr": "P_in = flux(Port1, re(S))"}],
         export_file=str(tmp_path / "eq.json"))
    bad = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "set_equation", "arguments": {"name": "X", "expr": "1 +* 2"}}})
    assert bad["result"]["isError"]
    # reports and field plots
    r = call(server, "set_report", traces=["dB(S(1,1))", "RL"])
    assert r["name"] == "XY Plot 1" and r["solution"] == "Setup1 : Sweep"
    o = call(server, "set_report", name="XY Plot 1", open=True)      # no window: nothing to open, a note instead
    assert "opened" not in o and "note" in o
    f = call(server, "set_report", fields=True, kind="Data Table", traces=["P_in"], port=1)
    assert f["name"] == "Fields Table 1" and f["props"]["fields"] == "1"
    fp = call(server, "set_field_plot", quantity="Mag_E", objects=["Sub"], attributes={"dB": True})
    assert fp["name"] == "Mag_E1" and fp["solution"] == "Setup1 : LastAdaptive" and fp["props"]["dB"] == "True"
    pl = call(server, "set_field_plot", name="Mag_E1", plane="xy@0.5mm")
    assert pl["plane"] == "XY@0.5mm"
    # parametric sweep and optimization
    ps = call(server, "set_parametric", sweeps=[{"variable": "w", "type": "Linear step", "start": "1mm",
                                                 "stop": "3mm", "step": "1mm"}])
    assert ps["variations"] == 3 and ps["setups"] == ["Setup1"]
    op = call(server, "set_optimization", variables=[{"name": "w", "min": "1mm", "max": "3mm"}],
              goals=[{"calc": "dB(S(1,1))", "start": "4GHz", "stop": "6GHz", "cond": "<=", "value": "-10"}])
    assert op["name"] == "Optimization1" and op["goals"][0]["weight"] == "1"
    assert call(server, "get_status", parametric="ParametricSetup1")["state"] == "not solved"
    assert call(server, "get_schema", section="parametric")["sweep_types"][0] == "Single value"
    s = call(server, "get_project")
    assert [x["name"] for x in s["equations"]] == ["RL", "P_in"] and s["functions"] == ["sq(x) = x*x"]
    assert {x["name"] for x in s["reports"]} == {"XY Plot 1", "Fields Table 1"}
    assert s["field_plots"][0]["plane"] == "XY@0.5mm"
    assert s["objects"][1]["orientation"] == "RelativeCS1" and "orientation" not in s["objects"][0]
    assert s["parametrics"][0]["name"] == "ParametricSetup1"
    # import into another design; delete everything again
    call(server, "set_design", new_design="D2")
    assert call(server, "set_equation", design="D2", import_file=str(tmp_path / "eq.json"))["changed"] == \
        ["RL", "P_in", "sq()"]
    d = call(server, "delete", design="Design1", names=["RL", "sq", "XY Plot 1", "Mag_E1", "ParametricSetup1",
                                                         "Optimization1"])
    assert not d.get("not_found")
    s = call(server, "get_project", design="Design1")
    assert [x["name"] for x in s["equations"]] == ["P_in"] and s["functions"] == []
    assert "parametrics" not in s and "optimizations" not in s and "field_plots" not in s


def test_claude_install_adds_the_plugin(tmp_path, monkeypatch):
    """With the claude command the plugin is installed from the folder of this installation and removed again."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(clients, "_claude_cli", lambda: "claude")
    calls = []

    def run(cmd, timeout=60):
        calls.append(cmd[1:])
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(clients, "_run", run)
    assert clients.plugin_source() == str(clients.ROOT)
    msg = clients.claude_install()
    assert "plugin 'hertzon' installed" in msg
    assert ["plugin", "install", "hertzon", "--marketplace", str(clients.ROOT), "--json"] in calls
    clients.claude_remove()
    assert ["plugin", "uninstall", "hertzon@hertzon"] in calls and ["plugin", "marketplace", "remove", "hertzon"] in calls


def test_plugin_hint_without_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(clients, "_claude_cli", lambda: None)
    monkeypatch.setattr(clients, "ROOT", tmp_path)          # an installation without the plugin folder
    assert "claude plugin install hertzon --marketplace c8501024/hertzon" in clients.claude_install()
