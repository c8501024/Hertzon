"""Edge-launch connectors at lumped ports (hertzon.model.connectors): library, placement, the three modes and
their switching, flatten, transition design and cascade."""
from __future__ import annotations

import json

import pytest

from hertzon.model import connectors as C
from hertzon.model import schema
from hertzon.model.design import Assignment, Project, Setup, Sweep

H, W, LEN = 0.508, 1.1, 20.0


def microstrip(region: str = "region") -> Project:
    """A 50 ohm microstrip on 0.508 mm RO4003C along +Y with lumped ports at both board edges (y = 0, y = LEN)."""
    pr = Project()
    d = pr.designs[0]
    d.variables.update({"h": f"{H}mm", "w": f"{W}mm", "len": f"{LEN}mm"})
    d.add_object("CreateBox", "Substrate", "Rogers RO4003C (tm)", Position="-6mm,0,-h", XSize="12mm", YSize="len",
                 ZSize="h")
    tr = d.add_object("CreateRectangle", "Trace", Position="-w/2,0,0", Axis="Z", XSize="w", YSize="len")
    gnd = d.add_object("CreateRectangle", "Ground", Position="-6mm,0,-h", Axis="Z", XSize="12mm", YSize="len")
    d.boundaries.append(Assignment("PerfE1", "Perfect E", {}, objects=[tr.name, gnd.name]))
    for i, y in ((1, "0"), (2, "len")):
        s = d.add_object("CreateRectangle", f"PortSheet{i}", Position=f"-w/2,{y},-h", Axis="Y", XSize="w", ZSize="h")
        p = schema.defaults(schema.EXCITATIONS["Lumped Port"])
        p.update({"PortNumber": str(i), "IntegrationLine": f"0,{y},-h;0,{y},0"})
        d.excitations.append(Assignment(f"P{i}", "Lumped Port", p, objects=[s.name]))
    if region == "region":
        r = d.add_object("CreateRegion", "Region", PaddingType="Absolute Offset",
                         **{k: "5mm" for k in ("+X", "-X", "+Y", "-Y", "+Z", "-Z")})
        d.boundaries.append(Assignment("Rad1", "Radiation", schema.defaults(schema.BOUNDARIES["Radiation"]),
                                       objects=[r.name]))
    elif region == "auto":
        d.settings["AutoOpen"] = "Auto Open"
    st = Setup("Setup1")
    st.props["Frequency"] = "40GHz"
    sw = Sweep("Sweep")
    sw.props.update({"Distribution": "Linear Count", "Start": "1GHz", "Stop": "40GHz", "Count": "40"})
    st.sweeps.append(sw)
    d.setups.append(st)
    return pr


PID = "southwest-1092-03a-12"         # 2.92 mm flange connector, 0.254 mm launch pin


def test_library():
    assert len(C.parts()) == 131
    assert {p["interface"] for p in C.parts()} == set(C.INTERFACES)
    p = C.part(PID)
    assert C.f_max(p) >= 40
    ids = [p["id"] for p in C.parts()]
    assert len(ids) == len(set(ids))


def test_footprint_rules():
    p = C.part(PID)
    L = C.launch_for(p, H)
    fp = C.footprint_for(L, C.EdgePlacement((0, 0, 0), "-Y", 2, H, W, 3.55))
    assert fp.wp < W and fp.wp >= 1.5 * L.pin            # narrowed at the launch, not widened to the pin
    assert fp.lt > 0
    gap = fp.via_pitch - fp.via_d                          # via fence: quarter-wave resonance above f_max
    assert C.C0_MM_GHZ / (4 * gap * 3.55 ** 0.5) > L.f_max
    assert fp.lf >= L.block_len_est and fp.half >= L.block_w_est / 2 - 1e-9   # ground pour under the clamp block
    assert C.cpwg_z0(W, fp.gc, H, 3.55) == pytest.approx(50, abs=0.5)


def test_placement_from_lumped_port():
    pr = microstrip()
    d = pr.designs[0]
    bb = C.geometry_bboxes(pr, d)
    pl, warns, mat = C.port_placement(pr, d, "P1", bb)
    assert pl.direction == "-Y" and pl.normal == 2
    assert pl.point == pytest.approx((0, 0, 0), abs=1e-9)
    assert pl.board_h == pytest.approx(H) and pl.trace_w == pytest.approx(W)
    assert pl.er == pytest.approx(3.55) and mat == "Rogers RO4003C (tm)"
    pl2, _, _ = C.port_placement(pr, d, "P2", bb)
    assert pl2.direction == "+Y" and pl2.point[1] == pytest.approx(LEN)


def _state(d):
    return json.dumps(d.to_dict() if hasattr(d, "to_dict") else None)


def test_modes_switch_losslessly():
    pr = microstrip()
    d = pr.designs[0]
    region_before = dict(d.obj("Region").history[0].props)
    j1 = C.attach_connector(pr, d, "P1", PID)
    j2 = C.attach_connector(pr, d, "P2", PID)
    assert (j1.name, j2.name) == ("J1", "J2")
    # Ideal: lumped ports solved, connector not in the model, cuts suppressed
    assert [e.name for e in d.ports()] == ["P1", "P2"]
    assert all(not d.obj(n).model for n in C.parts_of(j1).body + C.parts_of(j1).footprint)
    assert all(d.obj(n).transparency >= C.PREVIEW_TRANSPARENCY for n in C.parts_of(j1).body)
    tr = d.obj("Trace")
    cuts = [c for c in tr.history if c.kind == "Subtract"]
    assert len(cuts) == 2 and all(c.suppressed for c in cuts)
    assert d.obj("Region").history[0].props == region_before
    snap_ideal = json.dumps(Project.to_dict(pr))
    # 3D for J1
    C.set_mode(pr, d, "3D", ["J1"])
    names = [e.name for e in d.ports()]
    assert names == [C.parts_of(j1).wave_port, "P2"]
    assert d.ports()[0].props["PortNumber"] == "1"
    assert all(d.obj(n).model for n in C.parts_of(j1).body + C.parts_of(j1).footprint)
    assert not d.obj("PortSheet1").model
    assert [c.suppressed for c in tr.history if c.kind == "Subtract"] == [False, True]
    assert d.obj("Region").history[0].props["-Y"] == "0"
    rad = d.assignment("Rad1")
    assert rad.objects == [] and sorted(f.face for f in rad.faces) == [0, 1, 3, 4, 5]
    # both 3D
    C.set_mode(pr, d, "3D")
    assert sorted(f.face for f in d.assignment("Rad1").faces) == [0, 1, 4, 5]
    # back to Ideal: identical to before
    C.set_mode(pr, d, "Ideal")
    assert json.dumps(Project.to_dict(pr)) == snap_ideal


def test_auto_open_becomes_region_in_3d():
    pr = microstrip("auto")
    d = pr.designs[0]
    C.attach_connector(pr, d, "P1", PID)
    snap = json.dumps(Project.to_dict(pr))
    C.set_mode(pr, d, "3D")
    regs = [o for o in d.objects if o.history[0].kind == "CreateRegion"]
    assert len(regs) == 1 and d.settings["AutoOpen"] == "Off"
    assert regs[0].history[0].props["-Y"] == "0"
    rad = next(b for b in d.boundaries if b.kind == "Radiation")
    assert sorted(f.face for f in rad.faces) == [0, 1, 3, 4, 5]
    C.set_mode(pr, d, "Ideal")
    assert json.dumps(Project.to_dict(pr)) == snap


@pytest.mark.parametrize("mode", ["Ideal", "3D"])
def test_geometry_builds_and_validates(mode):
    from hertzon.mcp.geomworker import tessellate_here
    from hertzon.model.validate import validate
    pr = microstrip()
    d = pr.designs[0]
    C.attach_connector(pr, d, "P1", PID)
    C.attach_connector(pr, d, "P2", "cinch-142-0701-801")
    C.set_mode(pr, d, mode)
    tess, errors = tessellate_here(pr, d, None)
    assert errors == {}
    checks = validate(pr, d, tess=tess, errors=errors)
    bad = [c.message for c in checks if c.level == "error"]
    assert bad == []
    # the wave port circle lies on the region face toward the connector
    if mode == "3D":
        port = tess["J1_Port"]
        reg = tess["Region"]
        assert port.bbox[1] == pytest.approx(reg.bbox[1], abs=1e-6)


def test_flatten():
    pr = microstrip()
    d = pr.designs[0]
    j = C.attach_connector(pr, d, "P1", PID)
    wave = C.parts_of(j).wave_port
    C.flatten(pr, d, "J1")
    assert d.connectors == []
    assert not any(e.name == "P1" for e in d.excitations)
    assert not d.has_obj("PortSheet1")
    assert [e.name for e in d.ports()] == [wave, "P2"]
    assert d.obj("Region").history[0].props["-Y"] == "0"
    # a later mode change of another connector keeps the flattened side open
    C.attach_connector(pr, d, "P2", PID)
    C.set_mode(pr, d, "Ideal")
    assert d.obj("Region").history[0].props["-Y"] == "0"
    assert d.obj("Region").history[0].props["+Y"] == "5mm"


def test_remove_connector_restores_design():
    pr = microstrip()
    snap = json.dumps(Project.to_dict(pr))
    d = pr.designs[0]
    C.attach_connector(pr, d, "P1", PID)
    C.set_mode(pr, d, "3D")
    C.remove_connector(pr, d, "J1")
    assert json.dumps(Project.to_dict(pr)) == snap


def test_save_load_roundtrip(tmp_path):
    pr = microstrip()
    d = pr.designs[0]
    C.attach_connector(pr, d, "P1", PID, mode="3D")
    f = tmp_path / "c.hzn"
    pr.save(f)
    q = Project.load(f)
    c = q.designs[0].connectors[0]
    assert (c.name, c.part, c.port, c.mode) == ("J1", PID, "P1", "3D")
    assert c.props == d.connectors[0].props


def test_transition_and_cascade():
    from hertzon.mcp.geomworker import tessellate_here
    from hertzon.model.validate import validate
    pr = microstrip()
    d = pr.designs[0]
    C.attach_connector(pr, d, "P1", PID)
    C.attach_connector(pr, d, "P2", PID)
    C.set_mode(pr, d, "Transition")
    # both connectors are alike: one shared transition design
    tnames = {c.props["Transition"] for c in d.connectors}
    assert len(tnames) == 1
    t = pr.design(tnames.pop())
    assert C.transition_state(pr, d, d.connectors[0]) == "current"
    assert [e.name for e in t.ports()] == ["Coax", "Board"]
    line = json.loads(t.settings[C.TRANSITION_LINE])
    assert line["w"] == pytest.approx(W) and line["h"] == pytest.approx(H) and line["Lb"] > 2
    tess, errors = tessellate_here(pr, t, None)
    assert errors == {}
    bad = [c.message for c in validate(pr, t, tess=tess, errors=errors) if c.level == "error"]
    assert bad == []
    # 3D design unchanged in Transition mode (lumped ports solved)
    assert [e.name for e in d.ports()] == ["P1", "P2"]
    c = pr.design(C.cascade_name(d))
    assert c.kind == "circuit"
    types = sorted(x.type for x in c.components)
    assert types == ["EM", "EM", "EM", "MLIN", "MLIN", "PORT", "PORT", "SUBST", "SUBST"]
    assert all(x.props["L"] == f"{-line['Lb']:g}mm" for x in c.components if x.type == "MLIN")
    from hertzon.model import circuit as CK
    net, n = CK.nets(c.components, c.wires)
    # each power port connects to the coax pin of a transition block, its board pin to the main block
    em1 = next(x for x in c.components if x.name == "EM1")
    for ci in d.connectors:
        blk = next(x for x in c.components if x.name == f"EM_{ci.name}")
        port = next(x for x in c.components if x.type == "PORT" and x.props["Num"] == d.assignment(ci.port).props["PortNumber"])
        coax, brd = CK.pins(blk)
        assert net[CK.pins(port)[0]] == net[coax]
        k = int(d.assignment(ci.port).props["PortNumber"]) - 1
        de = next(x for x in c.components if x.name == f"DE_{ci.name}")
        assert {net[CK.pins(em1)[k]], net[brd]} == {net[q] for q in CK.pins(de)}
    # a footprint change makes the transition stale; setting the mode again rebuilds it
    d.variables["J1_wp"] = "0.7mm"
    assert C.transition_state(pr, d, d.connectors[0]) == "stale"
    C.set_mode(pr, d, "Transition", ["J1"])
    assert C.transition_state(pr, d, d.connectors[0]) == "current"
    assert d.connectors[0].props["Transition"] != d.connectors[1].props["Transition"]


def test_launch_optimization_and_pull():
    pr = microstrip()
    d = pr.designs[0]
    j = C.attach_connector(pr, d, "P1", PID)
    C.set_mode(pr, d, "Transition")
    t = pr.design(j.props["Transition"])
    op = C.add_launch_optimization(t)
    assert {v["name"] for v in op.variables} == {"J_wp", "J_lp", "J_gl", "J_lt", "J_gc"}
    assert op.goals[0]["calc"] == "dB(S(1,1))" and op.goals[0]["stop"] == "40GHz"
    t.variables["J_wp"] = "0.75mm"           # as if the optimizer found it
    C.pull_footprint(pr, d, j)
    assert d.variables["J1_wp"] == "0.75mm"
    assert C.transition_state(pr, d, j) == "current"


def test_gui_real_connector_and_modes(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    QApplication.instance() or QApplication([])
    from hertzon.gui.main_window import MainWindow
    from hertzon.gui import connector_dialogs
    w = MainWindow()
    pr = microstrip()
    w.doc._restore(pr.to_dict())
    w.doc.rebuild()
    assert "P1" in [e.name for e in w.des.excitations] and w.doc.tess
    monkeypatch.setattr(connector_dialogs.ConnectorDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(connector_dialogs.ConnectorDialog, "result_values",
                        lambda self: {"part": PID, "name": "J1", "mode": "Ideal"})
    w.connector_at_port("2.92mm", "P1")
    assert [c.name for c in w.des.connectors] == ["J1"]
    w.project_tree.rebuild()
    found = []

    def walk(it):
        found.append(it.data(0, __import__("hertzon.gui.trees", fromlist=["ROLE"]).ROLE))
        for i in range(it.childCount()):
            walk(it.child(i))
    for i in range(w.project_tree.topLevelItemCount()):
        walk(w.project_tree.topLevelItem(i))
    assert ("connector", "J1") in found
    w.connector_mode("3D", ["J1"])
    assert w.des.connectors[0].mode == "3D"
    assert not w.doc.errors
    w.connector_mode("Transition", ["J1"])
    assert any(d.name == C.cascade_name(w.des) for d in w.doc.project.designs)
    w.doc.undo()
    w.doc.undo()
    assert w.des.connectors[0].mode == "Ideal"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w.connector_flatten(["J1"])
    assert w.des.connectors == [] and not any(e.name == "P1" for e in w.des.excitations)
    w.close()
