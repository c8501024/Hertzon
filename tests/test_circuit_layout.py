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
