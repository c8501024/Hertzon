"""Schematic to 3D layout (model/circuit_layout.py) and the way back."""
import pytest

from hertzon.model import circuit as CK
from hertzon.model import circuit_layout as CL
from hertzon.model.circuit import TYPES
from hertzon.model.circuit import Component as C
from hertzon.model.design import Design, Project, circuit_setup, project_from_dict


def P(name, x, y):
    return C(name, "PORT", x, y + 40, 0, {"Z0": "50ohm"})


def _circuit(*comps):
    p = Project()
    d = Design(name="Net", kind="circuit")
    d.components = [C("Sub1", "SUBST", -200, 200, props=dict(TYPES["SUBST"].props)), *comps]
    d.setups = [circuit_setup()]
    p.designs.append(d)
    return p, d


def _line(name, x, y, rot=0, ctype="MLIN", **props):
    return C(name, ctype, x, y, rot, {"Subst": "Sub1", **TYPES[ctype].props, **props})


def _box(design, project, name):
    """(x, y, xsize, ysize) of a rectangle in mm."""
    ev = design.evaluator(project)
    pr = design.obj(name).history[0].props
    x, y, _ = ev.point(pr["Position"])
    return round(x * 1e3, 6), round(y * 1e3, 6), round(ev.length(pr["XSize"]) * 1e3, 6), \
        round(ev.length(pr["YSize"]) * 1e3, 6)


def test_line_stub_and_lumped_parts_are_laid_out_end_to_end():
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0, L="10mm"),
                    _line("MSO1", 80, 40, ctype="MLOC", W="0.8mm", L="8mm"),     # stub down from (80, 0)
                    _line("MS2", 120, 0, L="6mm"), C("R1", "R", 190, 0, 0, {"R": "20ohm"}),
                    C("C1", "C", 220, 30, 90, {"C": "1pF"}), C("G1", "GND", 220, 70),
                    C("TL1", "TLIN", 260, 0, 0, {"Z0": "50ohm", "E": "90deg", "F": "3GHz"}), P("Port2", 300, 0))
    d.settings[CK.AUTO] = "False"                             # ideal nodes: lines from the node centre
    L, warn = CL.layout_design(p, d)
    assert L.kind == "3d" and L.settings[CL.LINK] == "Net"
    assert L.variables["MS1_L"] == "10mm" and L.variables["MSO1_W"] == "0.8mm" and L.variables["Sub1_H"] == "0.508mm"
    assert any("TL1" in w for w in warn)
    # the ideal 90 degree line becomes microstrip of about a quarter guided wavelength at 3 GHz
    assert 14 < float(L.variables["TL1_L"].removesuffix("mm")) < 16
    p.designs.append(L)
    assert _box(L, p, "MS1") == (0, -0.55, 10, 1.1)
    assert _box(L, p, "MS2") == (10, -0.55, 6, 1.1)
    assert _box(L, p, "MSO1") == (9.6, -8, 0.8, 8)                 # downwards from the junction
    assert _box(L, p, "R1")[:3] == (16, -0.55, 1)                   # gap after MS2
    assert _box(L, p, "TL1")[0] == 17
    # the ports sit on the board edges, the substrate reaches beyond the stub by the margin
    sub = _box(L, p, "Substrate")
    assert sub[0] == 0 and sub[2] == pytest.approx(17 + float(L.variables["TL1_L"][:-2]))
    assert sub[1] == -13
    kinds = {a.name: a.kind for a in L.boundaries}
    assert kinds["R1"] == kinds["C1"] == "Lumped RLC"
    rlc = {a.name: a.props for a in L.boundaries}
    assert rlc["R1"]["Resistance"] == "20ohm" and rlc["R1"]["UseResistance"] == "True"
    assert rlc["C1"]["Capacitance"] == "1pF" and rlc["C1"]["UseResistance"] == "False"
    assert [(e.kind, e.props["PortNumber"]) for e in L.excitations] == [("Lumped Port", "1"), ("Lumped Port", "2")]
    # the stub, both lines and the junction patch are one conductor; the line after the gap another
    tr = next(a for a in L.boundaries if a.name == "Copper_Traces")
    assert tr.objects == ["MS1", "TL1"]
    assert [h.props["Tools"] for h in L.obj("MS1").history[1:]] == ["MSO1", "MS2", "Junction"]
    # the material and the design survive a round trip of the project
    q = project_from_dict(p.to_dict())
    assert q.design(L.name).settings[CL.LINK] == "Net" and any(m.name.startswith("Sub1_") for m in q.materials)


def test_coupled_lines_and_the_way_back():
    p, d = _circuit(P("Port1", 0, 0), _line("MSC1", 40, 10, ctype="MCLIN", W="1mm", S="0.3mm", L="12mm"),
                    P("Port2", 80, 20))                            # through A in -> B out
    L, warn = CL.layout_design(p, d)
    p.designs.append(L)
    assert not warn
    a, b = _box(L, p, "MSC1_A"), _box(L, p, "MSC1_B")
    assert a == (0, -0.5, 12, 1) and b == (0, -1.8, 12, 1)       # B below A (schematic +y is down), gap 0.3
    assert {e.objects[0] for e in L.excitations} == {"Port1", "Port2"}
    L.variables["MSC1_S"] = "0.25mm"
    L.variables["Sub1_H"] = "0.8mm"
    circ, changes = CL.update_schematic(p, L)
    mc = next(c for c in circ.components if c.name == "MSC1")
    assert mc.props["S"] == "0.25mm" and circ.components[0].props["H"] == "0.8mm" and len(changes) == 2
    assert CL.update_schematic(p, L)[1] == []


def test_layout_errors():
    p, d = _circuit(P("Port1", 0, 0), C("R1", "R", 50, 0, 0, {"R": "50ohm"}), P("Port2", 80, 0))
    with pytest.raises(CL.LayoutError, match="no lines"):
        CL.layout_design(p, d)
    d.components = d.components[1:]
    with pytest.raises(CL.LayoutError, match="no substrate"):
        CL.layout_design(p, d)
    assert p.materials == []
    with pytest.raises(CL.LayoutError, match="not created from a schematic"):
        CL.update_schematic(p, p.designs[0])


def test_layout_meshes_without_errors():
    from hertzon.mcp.tools import tessellate
    from hertzon.model.validate import validate
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0), _line("MSS1", 80, 40, ctype="MLSC"),
                    _line("MS2", 120, 0), P("Port2", 160, 0))
    L, _ = CL.layout_design(p, d)
    p.designs.append(L)
    assert L.has_obj("MSS1_Short") and L.has_obj("Tee1")              # the automatic tee at the stub
    tess, err = tessellate(p, L)
    assert not err
    assert all(c.level == "ok" for c in validate(p, L, tess, err))


def test_tee_is_laid_out_as_a_junction_with_the_lines_at_its_edges():
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0, L="9mm"),
                    C("MT1", "MTEE", 110, 0, 0, {"Subst": "Sub1", "W1": "1.1mm", "W2": "1.1mm", "W3": "0.8mm"}),
                    _line("MSO1", 110, 70, ctype="MLOC", W="0.8mm", L="8mm"), _line("MS2", 180, 0, L="9mm"),
                    P("Port2", 220, 0))
    L, warn = CL.layout_design(p, d)
    p.designs.append(L)
    assert not warn and L.variables["MT1_W3"] == "0.8mm"
    assert _box(L, p, "MT1") == (9, -0.55, 0.8, 1.1)                 # W3 long, the width of the main line
    assert _box(L, p, "MS2")[0] == 9.8
    assert _box(L, p, "MSO1") == (9, -8.55, 0.8, 8)                  # the branch starts at the main line edge
    assert not L.has_obj("Junction")


def test_corner_bend_cross_step_and_gap_layouts():
    from hertzon.mcp.tools import tessellate
    from hertzon.model.validate import validate
    sub = {"Subst": "Sub1"}
    # Port1 -> MS1 -> cross -> MS2 -> step -> MS3 -> gap -> MS4 -> mitered bend (down) -> MS5 -> corner -> Port2
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0, L="5mm"),
                    C("MX1", "MCROSS", 110, 0, 0, {**sub, **{f"W{j}": "1.1mm" for j in range(1, 5)}}),
                    _line("MSO1", 110, -70, 180, "MLOC", L="4mm"), _line("MSO2", 110, 70, ctype="MLOC", L="4mm"),
                    _line("MS2", 180, 0, L="5mm"), C("ST1", "MSTEP", 250, 0, 0, {**sub, "W1": "1.1mm", "W2": "2mm"}),
                    _line("MS3", 320, 0, W="2mm", L="5mm"),
                    C("G1", "MGAP", 390, 0, 0, {**sub, "W1": "2mm", "W2": "2mm", "S": "0.3mm"}),
                    _line("MS4", 460, 0, W="2mm", L="5mm"), C("B1", "MMBEND", 530, 0, 0, {**sub, "W": "2mm"}),
                    _line("MS5", 530, 70, 90, W="2mm", L="5mm"),
                    C("K1", "MCORN", 530, 140, 90, {**sub, "W": "2mm"}),
                    P("Port2", 500, 140))
    L, warn = CL.layout_design(p, d)
    p.designs.append(L)
    assert not warn, warn
    assert _box(L, p, "MX1") == (5, -0.55, 1.1, 1.1)
    assert _box(L, p, "MS2")[0] == 6.1
    assert _box(L, p, "MSO1")[1] == 0.55 and _box(L, p, "MSO1")[3] == 4      # up from the crossing edge
    assert _box(L, p, "MS3")[0] == 11.1                                     # the step has no length
    assert _box(L, p, "MS4")[0] == 16.4                                     # after the 0.3 mm gap
    assert L.obj("B1").history[0].kind == "CreatePolyline"
    assert _box(L, p, "MS5") == (21.4, -6, 2, 5)                            # down from the bend edge
    assert _box(L, p, "K1") == (21.4, -8, 2, 2)
    tr = next(a for a in L.boundaries if a.name == "Copper_Traces")
    assert len(tr.objects) == 2                                             # the gap splits the copper
    tess, err = tessellate(p, L)
    assert not err
    assert all(c.level == "ok" for c in validate(p, L, tess, err))
    from hertzon.solver.circuit import analyze
    import numpy as np
    r = analyze(p, d, np.array([2e9, 6e9]))
    assert np.all(np.isfinite(r.S)) and np.allclose(r.S, r.S.transpose(0, 2, 1), atol=1e-9)


def test_line_to_ground_is_shorted_by_a_via_and_the_tee_marker_sits_where_the_wires_meet():
    from hertzon.model.circuit import Wire
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 160, 0, L="8mm"), _line("MS2", 240, 80, 90),
                    C("G1", "GND", 240, 130), _line("MS3", 320, 0))
    d.wires = [Wire([[0, 0], [120, 0]]), Wire([[200, 0], [280, 0]]), Wire([[240, 40], [240, 0]])]
    j = CK.find_junctions(d.components, d.wires)
    assert [(x.type, x.point) for x in j] == [("MTEE", (240, 0))]
    L, _ = CL.layout_design(p, d)
    assert L.has_obj("MS2_Short")
    tr = next(a for a in L.boundaries if a.name == "Copper_Traces")
    assert "MS2_Short" in tr.objects


def test_via_part_is_laid_out_as_round_vias_inside_the_line_end():
    from hertzon.model.circuit import Wire
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 160, 0, L="18mm"),
                    C("V1", "MVIA", 200, 20, 0, {"Subst": "Sub1", "D": "0.4mm", "N": "2", "P": "0.7mm"}))
    d.wires = [Wire([[0, 0], [120, 0]])]
    L, warn = CL.layout_design(p, d)
    assert not warn
    p.designs.append(L)
    ev = L.evaluator(p)
    centres = sorted(tuple(round(v * 1e3, 6) for v in ev.point(L.obj(n).history[0].props["CenterPosition"]))
                     for n in ("V1_1", "V1_2"))
    assert centres == [(17.8, -0.35, 0), (17.8, 0.35, 0)]
    assert ev.length(L.obj("V1_1").history[0].props["Radius"]) == pytest.approx(0.2e-3)


def test_update_from_layout_reads_edited_object_sizes_and_the_layout_is_rebuilt_in_place():
    from hertzon.model.circuit import Wire
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 160, 0, L="18mm"), P("Port2", 240, 0))
    d.wires = [Wire([[0, 0], [120, 0]]), Wire([[200, 0], [240, 0]])]
    L, _ = CL.layout_design(p, d)
    p.designs.append(L)
    L.obj("MS1").history[0].props["XSize"] = "20mm"          # resized on the object, not in MS1_L
    circ, changes = CL.update_schematic(p, L)
    assert changes == ["MS1.L: 18mm → 20mm"] and circ.components[2].props["L"] == "20mm"
    L.setups[0].props["MaximumPasses"] = "4"
    d.components[2].props["W"] = "2mm"
    CL.update_layout(p, L)
    assert L.variables["MS1_W"] == "2mm" and L.variables["MS1_L"] == "20mm"
    assert L.setups[0].props["MaximumPasses"] == "4" and L.obj("MS1").history[0].props["XSize"] == "MS1_L"


def test_update_layout_follows_exchanged_parts():
    from hertzon.model.circuit import Wire
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 160, 0, L="8mm"),
                    C("TLS1", "TLSC", 200, 40, 0, {"Z0": "50ohm", "E": "10deg", "F": "1GHz"}))
    d.wires = [Wire([[0, 0], [120, 0]])]
    L, _ = CL.layout_design(p, d)
    p.designs.append(L)
    assert L.has_obj("TLS1_Short")
    # the ideal short stub replaced by a microstrip line to a round via
    d.components = [c for c in d.components if c.name != "TLS1"] + [
        _line("MS2", 200, 40, 90, L="5mm"), C("V1", "MVIA", 200, 100, 0, {"Subst": "Sub1", "D": "0.4mm"})]
    CL.update_layout(p, L)
    names = {o.name for o in L.objects}
    assert {"MS2", "V1"} <= names and not any(n.startswith("TLS1") for n in names)
    assert "TLS1_W" not in L.variables


def _em_block(p, name, x, y, design, rot=0):
    from hertzon.model import circuit_embed as CE
    s = p.design(design)
    c = C(name, "EM", x, y, rot, {"Design": design, "Solution": "", "Ports": str(len(s.ports())),
                                   "PortNames": ",".join(q.name for q in s.ports())})
    assert CE.pin_ports(s, c)
    return c


def _inner(p, length="6mm"):
    """A 3D design to use as a block: a laid out microstrip line with a lumped port at each end."""
    d = Design(name="Inner", kind="circuit")
    d.components = [C("Sub1", "SUBST", -200, 200, props=dict(TYPES["SUBST"].props)), P("Port1", 0, 0),
                    _line("MS", 40, 0, L=length), P("Port2", 80, 0)]
    d.setups = [circuit_setup()]
    p.designs.append(d)
    inner, _ = CL.layout_design(p, d, "Inner3D")
    del inner.settings[CL.LINK]
    p.designs.append(inner)
    return inner


def test_3d_design_block_is_built_into_the_layout_between_its_lines():
    from hertzon.mcp.tools import tessellate
    from hertzon.model.validate import validate
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0, L="10mm"), P("Port2", 280, 0))
    _inner(p)
    d.components += [_em_block(p, "EM1", 140, 0, "Inner3D"), _line("MS2", 240, 0, L="5mm")]
    L, warn = CL.layout_design(p, d)
    p.designs.append(L)
    assert not warn, warn
    # the block's line sits between MS1 and MS2, its ports were removed, its variables carry the block name
    assert _box(L, p, "MS1") == (0, -0.55, 10, 1.1)
    assert _box(L, p, "MS2") == (16, -0.55, 5, 1.1)
    assert L.has_obj("EM1_MS") and L.obj("EM1_MS").group == "EM1"
    assert L.variables["EM1_MS_L"] == "6mm"
    assert sorted(e.name for e in L.ports()) == ["1", "2"]
    assert not any(o.name.startswith("EM1_Port") for o in L.objects)
    assert {a.name for a in L.boundaries} >= {"EM1_Copper_Traces", "EM1_Copper_Ground"}
    sub = _box(L, p, "Substrate")
    assert sub[0] == 0 and sub[2] == pytest.approx(21)
    # the layout ground has a hole where the block brings its own ground
    assert L.has_obj("EM1_GroundCut") and L.obj("EM1_GroundCut").consumed_by == "Ground"
    tess, err = tessellate(p, L)
    assert not err
    assert all(c.level == "ok" for c in validate(p, L, tess, err))


def test_3d_design_block_turned_and_its_port_kept_at_a_power_port():
    import numpy as np
    from hertzon.model import objframe
    ms1 = _line("MS1", 0, 40, rot=90, L="10mm")
    top, bottom = sorted(CK.pins(ms1), key=lambda q: q[1])
    p, d = _circuit(P("Port1", top[0], top[1]), ms1)
    _inner(p)
    # the line runs down into the block: the block turns so that its port 1 faces up, its port 2 is a port
    blk = _em_block(p, "EM1", 200, 200, "Inner3D")
    pins = CK.pins(blk)
    d.components += [blk, C("Port2", "PORT", pins[1][0], pins[1][1] + 40, 0, {"Z0": "25ohm"})]
    d.wires.append(CK.Wire([list(bottom), list(pins[0])]))
    L, warn = CL.layout_design(p, d)
    p.designs.append(L)
    assert not warn, warn
    ports = {e.name: e for e in L.ports()}
    assert sorted(ports) == ["1", "2"]
    assert ports["2"].objects == ["EM1_Port2"] and ports["2"].props["Impedance"] == "25ohm"
    assert ports["2"].props["PortNumber"] == "2"
    assert _box(L, p, "MS1") == (-0.55, -10, 1.1, 10)
    h = L.obj("EM1_MS").history
    assert h[-2].kind == "Rotate" and h[-2].props["Angle"] == "270deg" and h[-1].kind == "Move"
    M = objframe.object_matrix(L, L.obj("EM1_MS"), L.evaluator(p))
    # the far end of the inner line (x = 6 mm in its design) lies 6 mm below the end of MS1
    assert (M @ np.array([0.006, 0, 0, 1.0]))[:2] == pytest.approx([0, -0.016], abs=1e-12)


def test_3d_designs_of_a_project_file_are_copied_in_and_placed_as_blocks(tmp_path, monkeypatch):
    import os
    from hertzon.model import circuit_embed as CE
    from hertzon.solver.results import results_dir
    # a saved project with a 3D design, a solved result and a project variable
    src, _ = _circuit()
    inner = _inner(src)
    src.variables["$f0"] = "3GHz"
    src.save(tmp_path / "parts.hzn")
    r = results_dir(tmp_path / "parts.hzn", inner.name, "Setup1")
    r.mkdir(parents=True)
    (r / "Sweep.s2p").write_text("# GHz S MA R 50\n")
    # the receiving project already has a design of that name
    p, d = _circuit()
    p.designs.append(Design(name="Inner3D"))
    p.path = str(tmp_path / "board.hzn")
    names, warn = CE.import_3d_designs(p, str(tmp_path / "parts.hzn"))
    assert names == ["Inner3D_2"] and not warn
    assert p.variables["$f0"] == "3GHz" and p.design("Inner3D_2").ports()
    assert (results_dir(p.path, "Inner3D_2", "Setup1") / "Sweep.s2p").exists()
    # in the editor: the dropped file becomes a block, one undo step takes it all back
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui import circuit_editor
    from hertzon.gui.doc import Document
    monkeypatch.setattr(circuit_editor.QMessageBox, "information", lambda *a: None)
    p, _ = _circuit()
    doc = Document(p)
    doc.set_design("Net")
    ed = circuit_editor.CircuitEditor(doc)
    assert ed.import_project_files([str(tmp_path / "parts.hzn")], 100, 0) == ["EM1"]
    blk = doc.design.components[-1]
    assert blk.type == "EM" and blk.props["Design"] == "Inner3D" and blk.props["Ports"] == "2"
    doc.undo()
    assert not any(c.type == "EM" for c in doc.design.components)
    assert "Inner3D" not in {x.name for x in doc.project.designs}
    ed.deleteLater()                  # the editor goes now, not at a garbage collection inside a later test
    QApplication.processEvents()
