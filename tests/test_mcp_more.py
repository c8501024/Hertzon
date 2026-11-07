"""MCP tools of the design assistants, optimizers, derivative tuning and circuits (file mode, no FEM solve)."""
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


def test_new_tools_are_listed(server):
    names = {t["name"] for t in server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert {"create_patch_antenna", "create_hairpin_filter", "apply_optimization", "compute_tuning", "get_tuning",
            "create_circuit", "get_circuit", "edit_circuit", "circuit_layout", "import_3d_designs"} <= names


def test_patch_and_hairpin_assistants_and_optimizers(server, tmp_path):
    call(server, "create_project", path=str(tmp_path / "a.hzn"))
    p = call(server, "create_patch_antenna", frequency_ghz=2.45)
    assert p["design"] == "Patch_2.45GHz" and p["optimization"]["optimizer"] == "Derivative Trust Region"
    assert 27 < float(p["variables"]["Lp"].removesuffix("mm")) < 30
    s = call(server, "get_project", design="Patch_2.45GHz")
    assert s["optimizations"][0]["name"] == "PatchOpt" and s["optimizations"][0]["solution"] == "Setup1 : LastAdaptive"
    assert "Unknown material" in error(server, "create_patch_antenna", frequency_ghz=2.45, material="nothing")
    assert "not a substrate" in error(server, "create_patch_antenna", frequency_ghz=2.45, material="copper")

    h = call(server, "create_hairpin_filter", passband_ghz=[2.3, 2.6], resonators=3)
    assert h["design"] == "Hairpin_N3" and h["optimizations"][0]["name"] == "HairpinCG"
    assert h["stopbands_ghz"] == [[2.0, 2.2], [2.7, 2.9]]
    # the trust region takes at most six variables; the hairpin optimization has six, a seventh is refused
    o = call(server, "set_optimization", design="Hairpin_N3", name="HairpinCG", optimizer="Derivative Trust Region",
             props={"MaxRounds": 4})
    assert o["props"]["Optimizer"] == "Derivative Trust Region" and o["props"]["MaxRounds"] == "4"
    call(server, "set_design", design="Hairpin_N3", variables={"extra": "1mm"})
    rows = o["variables"] + [{"name": "extra", "min": "0.5mm", "max": "2mm"}]
    assert "at most 6" in error(server, "set_optimization", design="Hairpin_N3", name="HairpinCG", variables=rows)
    assert "optimizer must be" in error(server, "set_optimization", design="Hairpin_N3", name="HairpinCG",
                                        optimizer="Simplex")


def test_apply_optimization_and_tuning_state(server, tmp_path):
    from hertzon.solver.results import _safe, studies_dir
    path = str(tmp_path / "o.hzn")
    call(server, "create_project", path=path)
    call(server, "create_patch_antenna", frequency_ghz=5.8)
    assert "no evaluated design" in error(server, "apply_optimization", optimization="PatchOpt")
    folder = studies_dir(path, "Patch_5.8GHz") / _safe("PatchOpt")
    folder.mkdir(parents=True)
    (folder / "history.json").write_text(json.dumps({"variables": ["Lp", "inset"], "evaluations": [
        {"eval": 0, "variables": {"Lp": "12mm", "inset": "3mm"}, "cost": 4.0},
        {"eval": 1, "variables": {"Lp": "12.2mm", "inset": "3.1mm"}, "cost": 0.5},
        {"eval": 2, "variables": {"Lp": "12.4mm", "inset": "3.2mm"}, "cost": None}]}))
    r = call(server, "apply_optimization", optimization="PatchOpt")
    assert r["evaluation"] == 1 and r["applied"] == {"Lp": "12.2mm", "inset": "3.1mm"}
    v = call(server, "get_project")["variables"]
    assert v["Lp"] == "12.2mm" and v["inset"] == "3.1mm"
    assert call(server, "apply_optimization", optimization="PatchOpt", evaluation=0)["applied"]["Lp"] == "12mm"
    # tuning needs a solved setup and plain variables
    assert call(server, "get_tuning")["state"] == "not computed"
    assert "Analyze Setup1 first" in error(server, "compute_tuning", variables=["Lp"])
    assert "plain value" in error(server, "compute_tuning", variables=["nothing"])
    assert "1 to 6" in error(server, "compute_tuning", variables=[])


def test_circuit_build_solve_layout_and_back(server, tmp_path):
    call(server, "create_project", path=str(tmp_path / "c.hzn"))
    c = call(server, "create_circuit", name="Line")
    assert c["kind"] == "circuit" and "MLIN" in c["library"] and c["setups"][0]["name"] == "Setup1"
    c = call(server, "edit_circuit", add=[
        {"type": "PORT", "name": "P1", "x": 0, "y": 0},
        {"type": "MLIN", "name": "MS1", "attach": "P1:1", "props": {"W": "1.1mm", "L": "20mm"}},
        {"type": "PORT", "name": "P2", "attach": "MS1:2"}])
    nets = c["nets"]
    assert sorted(nets.values()) == [["MS1:2", "P2:1"], ["P1:1", "MS1:1"]] or \
        sorted(sorted(v) for v in nets.values()) == [["MS1:1", "P1:1"], ["MS1:2", "P2:1"]]
    assert c["unconnected_pins"] == [] and c["ports"] == ["P1", "P2"]
    # a stub by wire: placed apart, connected with connect
    c = call(server, "edit_circuit", add=[{"type": "MLOC", "name": "St", "x": 300, "y": 200, "props": {"L": "5mm"}}],
             connect=[{"from": "St:1", "to": "MS1:2"}])
    assert any("St:1" in v and "MS1:2" in v for v in c["nets"].values())
    assert "has no property" in error(server, "edit_circuit", add=[{"type": "R", "props": {"Q": "1"}}])
    assert "Unknown part type" in error(server, "edit_circuit", add=[{"type": "XYZ"}])
    c = call(server, "edit_circuit", remove=["St"], remove_wires=[0])
    assert [p["name"] for p in c["parts"]] == ["Sub1", "P1", "MS1", "P2"] and c["wires"] == []
    # solve: a matched 50 ohm line passes everything
    r = call(server, "run_simulation")
    assert r["Setup1"]["state"] == "done"
    sp = call(server, "get_results", kind="sparameters", params=["S21", "S11"], frequencies=["2GHz"])
    row = sp["rows"][0]
    assert row["S21_dB"] > -0.5 and row["S11_dB"] < -20
    img = raw(server, "render_image")
    assert img["content"][-1]["type"] == "image"
    # 3D layout and back
    lay = call(server, "circuit_layout", action="create")
    assert lay["layout"] == "Line_Layout" and lay["variables"]["MS1_L"] == "20mm"
    call(server, "set_design", design="Line_Layout", variables={"MS1_L": "22mm"})
    back = call(server, "circuit_layout", design="Line", action="to_schematic")
    assert back["circuit"] == "Line" and back["changes"]
    ms1 = next(p for p in call(server, "get_circuit", design="Line")["parts"] if p["name"] == "MS1")
    assert ms1["props"]["L"] == "22mm"
    up = call(server, "circuit_layout", design="Line", action="update")
    assert up["rebuilt"]
    assert "not a circuit design" in error(server, "get_circuit", design="Line_Layout")


def test_3d_design_block_in_a_circuit(server, tmp_path):
    src = str(tmp_path / "src.hzn")
    call(server, "create_project", path=src)
    call(server, "create_patch_antenna", frequency_ghz=2.45, optimization=False)
    call(server, "create_project", path=str(tmp_path / "dst.hzn"))
    imp = call(server, "import_3d_designs", file=src)
    assert imp["imported"] == ["Patch_2.45GHz"]
    call(server, "create_circuit", name="Feed")
    c = call(server, "edit_circuit", add=[{"type": "PORT", "name": "P1", "x": 0, "y": 0},
                                          {"type": "MLIN", "name": "MS1", "attach": "P1:1"},
                                          {"type": "EM", "name": "Ant", "attach": "MS1:2",
                                           "props": {"Design": "Patch_2.45GHz"}}])
    ant = next(p for p in c["parts"] if p["name"] == "Ant")
    assert ant["props"]["Ports"] == "1" and len(ant["pins"]) == 1
    assert c["unconnected_pins"] == []
    assert "3D design" in error(server, "edit_circuit", add=[{"type": "EM", "props": {"Design": "Nope"}}])


def test_exports_stackup_pcb_designs_and_examples(server, tmp_path):
    ex = call(server, "list_examples")
    assert any(e["name"] == "Microstrip" for e in ex["examples"])
    o = call(server, "open_example", name="Microstrip", path=str(tmp_path / "ms.hzn"))
    assert Path(o["project"]).exists()
    # stackup on a new design, then Gerber export and import into another design
    call(server, "create_project", path=str(tmp_path / "b.hzn"))
    st = call(server, "set_stackup", copper_layers=2, thickness="1mm", substrate=["0", "0", "20", "10"])
    assert [L["kind"] for L in st["stackup"]["layers"]] == ["Copper", "Dielectric", "Copper"]
    assert st["objects_made"] and any(k.startswith("stk_") for k in st["variables"])
    top = st["coordinate_systems"][0]
    call(server, "set_coordinate_system", name=top, set_working=True)
    call(server, "add_object", kind="CreateRectangle", name="Trace", props={"XSize": "20", "YSize": "1"})
    call(server, "assign", category="boundary", kind="Finite Conductivity", objects=["Trace"])
    call(server, "set_coordinate_system", name="Global", set_working=True)
    g = call(server, "export", kind="gerber", path=str(tmp_path / "gerber"))
    assert g["written"], g["messages"]
    s = call(server, "export", kind="step", path=str(tmp_path / "b"))
    assert Path(s["written"]).stat().st_size > 0
    sc = call(server, "export", kind="script", path=str(tmp_path / "b"))
    assert "emerge" in Path(sc["written"]).read_text(encoding="utf-8").lower()
    assert "no setup" in error(server, "export", kind="touchstone", path=str(tmp_path / "x"))
    call(server, "set_design", new_design="Imported")
    imp = call(server, "import_pcb", design="Imported", files=g["written"])
    assert imp["stackup"] and imp["report"]
    names = [o["name"] for o in call(server, "get_project", design="Imported")["objects"]]
    assert len(names) > 1
    # copy and delete designs
    c = call(server, "manage_design", design="Imported", action="copy")
    assert c["design"] == "Imported_copy"
    d = call(server, "manage_design", design="Imported_copy", action="delete")
    assert "Imported_copy" not in d["designs"]


def test_circuit_touchstone_export(server, tmp_path):
    call(server, "create_project", path=str(tmp_path / "t.hzn"))
    call(server, "create_circuit", name="RC")
    call(server, "edit_circuit", add=[{"type": "PORT", "name": "P1", "x": 0, "y": 0},
                                      {"type": "R", "name": "R1", "attach": "P1:1", "props": {"R": "25ohm"}},
                                      {"type": "PORT", "name": "P2", "attach": "R1:2"}])
    call(server, "run_simulation")
    t = call(server, "export", kind="touchstone", path=str(tmp_path / "rc"))
    assert t["written"].endswith(".s2p") and t["ports"] == 2


def test_window_commands_of_the_new_tools(tmp_path, monkeypatch):
    """The live-link commands the new tools send to a Hertzon window (handled directly, no socket)."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.ai_link import AiLink
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.antennas import PatchSpec, patch_design
    from hertzon.model.circuit import TYPES
    from hertzon.model.circuit import Component as C
    from hertzon.model.design import Design, circuit_setup
    w = MainWindow()
    link = AiLink(w)
    net = Design(name="Net", kind="circuit")
    net.setups.append(circuit_setup())
    net.components = [C("P1", "PORT", 0, 40, 0, dict(TYPES["PORT"].props)),
                      C("R1", "R", 30, 0, 0, {"R": "50ohm"}), C("P2", "PORT", 60, 40, 0, dict(TYPES["PORT"].props))]
    patch, _ = patch_design(PatchSpec(f0=5.8))
    w.doc.project.designs += [net, patch]
    path = str(tmp_path / "w.hzn")
    w.doc.project.save(path)
    w.doc.project.path = path
    r = link.handle("analyze_circuit", {"design": "Net", "setups": ["Setup1"]})
    assert r["Setup1"]["state"] == "done"
    from hertzon.solver.results import ResultStore, results_dir
    data = ResultStore(results_dir(path, "Net", "Setup1")).sparams("Sweep")
    assert abs(data["S"][0, 1, 0]) == pytest.approx(2 / 3, rel=1e-3)        # 50 ohm in series between 50 ohm
    with pytest.raises(RuntimeError, match="analyze Setup1 first"):
        link.handle("tuning", {"design": patch.name, "setup": "Setup1",
                               "tuning": json.dumps({"sweep": "LastAdaptive", "variables": ["Lp"], "points": 3})})
    link.handle("apply_evaluation", {"design": patch.name, "optimization": "PatchOpt",
                                     "row": {"eval": 3, "variables": {"Lp": "12.1mm"}, "cost": 1.0}})
    assert w.doc.project.design(patch.name).variables["Lp"] == "12.1mm"
