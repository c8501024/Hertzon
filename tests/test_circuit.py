"""Circuit designs: schematic connectivity, the linear network solver and the schematic editor."""
import os

import numpy as np
import pytest

from hertzon.model import circuit as CK
from hertzon.model.circuit import Component as C, Wire as W
from hertzon.model.design import Design, Project, circuit_setup, project_from_dict
from hertzon.solver.circuit import CircuitError, analyze, run_setup
from hertzon.solver.results import ResultStore, results_dir


def P(name, x, y, **props):
    """A power port whose pin is at (x, y)."""
    return C(name, "PORT", x, y + 40, 0, {"Z0": "50ohm", **props})


def _circuit(*comps, wires=()):
    p = Project()
    d = Design(name="C", kind="circuit")
    d.components, d.wires = list(comps), list(wires)
    p.designs.append(d)
    return p, d


def test_pins_rotate_and_nets_join_wires_and_t_junctions():
    r = C("R1", "R", 50, 30, 90)
    assert CK.pins(r) == [(50, 0), (50, 60)]
    comps = [P("P1", 20, 0), r, C("G", "GND", 50, 70), P("P2", 80, 0)]
    ids, n = CK.nets(comps, [W([[20, 0], [80, 0]])])
    assert ids[(20, 0)] == ids[(50, 0)] == ids[(80, 0)] != 0          # (50, 0) lies on the wire: T junction
    assert ids[(50, 60)] == 0 and n == 1
    assert CK.nport_pins(3) == [(-60, -10), (60, 0), (-60, 10)]


def test_circuit_round_trip():
    p, _ = _circuit(C("R1", "R", 10, 20, 90, {"R": "75ohm"}), wires=[W([[0, 0], [10, 0]])])
    q = project_from_dict(p.to_dict())
    dd = q.designs[-1]
    assert dd.kind == "circuit" and dd.components[0].props["R"] == "75ohm" and dd.components[0].rot == 90
    assert dd.wires[0].points == [[0, 0], [10, 0]]


def test_series_resistor_and_quarter_wave_line():
    p, d = _circuit(P("Port1", 20, 0), C("R1", "R", 50, 0, 0, {"R": "50ohm"}), P("Port2", 80, 0))
    r = analyze(p, d, np.array([1e9]))
    assert r.ports == ["Port1", "Port2"]
    assert np.allclose(r.S[0], [[1 / 3, 2 / 3], [2 / 3, 1 / 3]])
    d.components = [P("Port1", 20, 0), C("TL1", "TLIN", 60, 0, 0, {"Z0": "50ohm", "E": "90deg", "F": "1GHz"}),
                    P("Port2", 100, 0)]
    r = analyze(p, d, np.array([1e9, 2e9]))
    assert np.allclose(r.S[:, 1, 0], [-1j, -1], atol=1e-6) and np.allclose(abs(r.S[:, 0, 0]), 0, atol=1e-6)


def test_shunt_capacitor_against_the_formula():
    p, d = _circuit(P("Port1", 20, 0), C("C1", "C", 50, 30, 90, {"C": "1pF"}), C("G", "GND", 50, 70),
                    C("R1", "R", 110, 0, 0, {"R": "1e-9ohm"}), P("Port2", 140, 0),
                    wires=[W([[20, 0], [80, 0]])])
    r = analyze(p, d, np.array([1e9]))
    yc = 1j * 2 * np.pi * 1e9 * 1e-12
    assert abs(r.S[0, 1, 0] - 2 / (2 + 50 * yc)) < 1e-6


def test_errors_are_explained():
    p, d = _circuit(C("R1", "R", 0, 0))
    with pytest.raises(CircuitError, match="no port"):
        analyze(p, d, np.array([1e9]))
    d.components = [P("Port1", 20, 0), C("G", "GND", 20, 10)]
    with pytest.raises(CircuitError, match="ground"):
        analyze(p, d, np.array([1e9]))


def test_3d_design_block_reads_its_solved_s_parameters(tmp_path):
    p = Project()
    p.path = str(tmp_path / "p.hzn")
    em = p.designs[0]
    em.name = "Filter"
    fr = np.linspace(1e9, 3e9, 5)
    S = np.zeros((5, 2, 2), complex)
    S[:, 0, 0], S[:, 1, 0], S[:, 0, 1], S[:, 1, 1] = 0.2, 0.9j, 0.9j, 0.3
    results_dir(p.path, "Filter", "Setup1").mkdir(parents=True)
    st = ResultStore(results_dir(p.path, "Filter", "Setup1"))
    ports = [{"name": "In", "number": 1, "kind": "Lumped Port", "modes": 1},
             {"name": "Out", "number": 2, "kind": "Lumped Port", "modes": 1}]
    st.save_sparams("Sweep", fr, S, 50.0, ports)
    st.write_json("ports.json", ports)
    em.setups.append(circuit_setup())          # any setup with the same name holds the results
    d = Design(name="Match", kind="circuit")
    d.setups.append(circuit_setup())
    d.setups[0].sweeps[0].props.update({"Start": "1GHz", "Stop": "3GHz", "Count": "3"})
    blk = C("EM1", "EM", 0, 0, 0, {"Design": "Filter", "Solution": "Setup1 : Sweep", "Ports": "2"})
    a, b = CK.pins(blk)
    d.components = [blk, P("Port1", a[0], a[1]), P("Port2", b[0], b[1])]
    p.designs.append(d)
    out, warns = run_setup(p, d, d.setups[0])
    got = ResultStore(out).sparams("Sweep")
    assert np.allclose(got["S"], S[[0, 2, 4]]) and not warns
    assert ResultStore(out).read_json("status.json", {})["state"] == "done"


def test_editor_places_parts_and_drops_a_3d_design(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui import circuit_editor
    from hertzon.gui.circuit_editor import CircuitEditor, ComponentItem
    shown = []
    monkeypatch.setattr(circuit_editor.QMessageBox, "information", lambda *a: shown.append(a[2]))
    from hertzon.gui.doc import Document
    from hertzon.gui.trees import MIME_DESIGN, ProjectTree
    p = Project()
    p.designs[0].name = "Antenna"
    p.designs.append(Design(name="Net", kind="circuit"))
    doc = Document(p)
    doc.set_design("Net")
    ed = CircuitEditor(doc)
    ed.place("R", 0, 0)
    ed.place("PORT", -60, 0)
    assert [c.name for c in doc.design.components] == ["R1", "Port1"] and doc.dirty
    ed.add_em_block("Antenna", 200, 0)            # no ports in the 3D design: nothing placed
    assert len(doc.design.components) == 2 and "no ports" in shown[0]
    ed.rebuild()
    assert sum(isinstance(i, ComponentItem) for i in ed.scene.items()) == 2
    doc.undo()
    assert [c.name for c in doc.design.components] == ["R1"]
    tree = ProjectTree(doc)
    root = tree.topLevelItem(0)
    items = [root.child(i) for i in range(root.childCount())]
    ant = next(i for i in items if i.text(0).startswith("Antenna"))
    md = tree.mimeData([ant])
    assert bytes(md.data(MIME_DESIGN)).decode() == "Antenna"


def _two_port(*parts, wires=()):
    """Port1 pin at (0, 0), Port2 pin at (200, 0), the parts between."""
    return _circuit(P("Port1", 0, 0), P("Port2", 200, 0), *parts, wires=list(wires))


def test_microstrip_model_against_published_values():
    from hertzon.solver import tlines as TL
    z0, ee = TL.microstrip_static(1.56e-3, 0.508e-3, 0.0, 2.2)     # 50 ohm on 20 mil, er 2.2
    assert abs(z0 - 50) < 1.0 and abs(ee - 1.87) < 0.03
    z0, ee = TL.microstrip_static(3.0e-3, 1.6e-3, 0.0, 4.4)        # 50 ohm on 1.6 mm FR4
    assert abs(z0 - 50) < 1.5 and abs(ee - 3.32) < 0.05
    assert TL.microstrip_eeff(20e9, 1.1e-3, 0.508e-3, 3.66, 2.8) > 2.8        # dispersion raises eeff
    z0, g = TL.coax(1e9, 0.91e-3, 2.98e-3, 2.1, 0, 0)
    assert abs(z0 - 49.1) < 0.5 and g.real == 0
    (z0e, _), (z0o, _) = TL.coupled_microstrip(1e9, 1e-3, 0.3e-3, 0.508e-3, 0, 3.66, 0, 0)
    assert z0e > 55 > 45 > z0o


def test_matched_microstrip_and_quarter_wave_open_stub():
    sub = C("Sub1", "SUBST", 0, -200, 0, {"Er": "2.2", "H": "0.508mm", "T": "0", "TanD": "0", "Cond": "0"})
    p, d = _two_port(sub, C("MS1", "MLIN", 100, 0, 0, {"Subst": "Sub1", "W": "1.56mm", "L": "20mm"}),
                     wires=[W([[0, 0], [60, 0]]), W([[140, 0], [200, 0]])])
    r = analyze(p, d, np.array([1e9, 5e9]))
    assert np.all(abs(r.S[:, 0, 0]) < 0.02) and np.allclose(abs(r.S[:, 1, 0]), 1, atol=1e-3)
    # an open quarter-wave stub shorts the line at its design frequency
    p, d = _two_port(C("TS1", "TLOC", 100, 40, 0, {"Z0": "50ohm", "E": "90deg", "F": "2GHz"}),
                     C("R1", "R", 170, 0, 0, {"R": "1e-9ohm"}), wires=[W([[0, 0], [140, 0]])])
    r = analyze(p, d, np.array([1e9, 2e9]))
    assert abs(r.S[1, 1, 0]) < 1e-4 and abs(r.S[0, 1, 0]) > 0.7


def test_net_labels_attenuator_and_mirror():
    p, d = _two_port(C("A1", "ATTEN", 100, 0, 0, {"Loss": "6dB", "Z0": "50ohm"}),
                     C("N1", "LABEL", 0, 0, 0, {"Net": "a"}), C("N2", "LABEL", 70, 0, 0, {"Net": "a"}),
                     wires=[W([[130, 0], [200, 0]])])
    r = analyze(p, d, np.array([1e9]))
    assert abs(20 * np.log10(abs(r.S[0, 1, 0])) + 6) < 1e-6 and abs(r.S[0, 0, 0]) < 1e-9
    m = C("M", "MCLIN", 0, 0, 0, {}, True)
    assert CK.pins(m) == [(-40, 10), (40, 10), (-40, -10), (40, -10)]
    assert CK.port_order([P("B", 0, 0, Num="1"), P("A", 0, 0, Num="2")])[0].name == "B"


def test_coupled_lines_far_apart_do_not_couple():
    sub = C("Sub1", "SUBST", 0, -300, 0, {"Er": "3.66", "H": "0.508mm", "T": "0", "TanD": "0", "Cond": "0"})
    cl = C("CL", "MCLIN", 100, 0, 0, {"Subst": "Sub1", "W": "1.1mm", "S": "10mm", "L": "15mm"})
    a1, a2, b1, b2 = CK.pins(cl)
    p, d = _circuit(sub, cl, P("Port1", *a1), P("Port2", *a2), P("Port3", *b1), P("Port4", *b2))
    r = analyze(p, d, np.array([3e9]))
    assert abs(r.S[0, 2, 0]) < 0.02 and abs(r.S[0, 1, 0]) > 0.95


def test_part_icons_and_library():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.circuit_editor import CircuitEditor, component_icon
    from hertzon.gui.doc import Document
    for t in CK.TYPES:
        assert not component_icon(t).isNull()
    p = Project()
    p.designs.append(Design(name="Net", kind="circuit"))
    doc = Document(p)
    doc.set_design("Net")
    ed = CircuitEditor(doc)
    cats = [ed.library.tree.topLevelItem(i).text(0) for i in range(ed.library.tree.topLevelItemCount())]
    assert cats == ["Basic", "Lumped Elements", "Transmission Lines", "Data", "3D Designs"]
    ed.library.search.setText("microstrip")
    assert ed.library.tree.topLevelItemCount() == 1
    ed.place("MLIN", 0, 0)                     # the first line brings a substrate
    assert sorted(c.type for c in doc.design.components) == ["MLIN", "SUBST"]
    ed.set_mode("place", "PORT")
    ed.rotate_selected()
    assert ed._ghost.comp.rot == 90


def test_wires_follow_a_moved_part():
    r = C("R1", "R", 100, 0)                                   # pins (70, 0) and (130, 0)
    ws = [W([[0, 0], [70, 0]]), W([[130, 0], [200, 0]])]
    new = CK.drag_wires([r], ws, {"R1": (0, 40)})
    assert [w.points for w in new] == [[[0, 0], [70, 0], [70, 40]], [[130, 40], [130, 0], [200, 0]]]
    assert [w.points for w in CK.drag_wires([r], ws, {"R1": (30, 0)})] == [[[0, 0], [100, 0]], [[160, 0], [200, 0]]]
    # a wire between two moved parts moves along; a pin in the middle of a wire gets a new wire
    p = C("P1", "PORT", 0, 40)
    assert CK.drag_wires([r, p], ws, {"R1": (0, 20), "P1": (0, 20)})[0].points == [[0, 20], [70, 20]]
    g = C("G1", "GND", 100, 10)                                # pin (100, 0) on the segment of a long wire
    new = CK.drag_wires([g], [W([[0, 0], [300, 0]])], {"G1": (0, 40)})
    assert new[1].points == [[100, 0], [100, 40]]
    ids, _ = CK.nets([C("G1", "GND", 100, 50)], new)
    assert ids[(100, 40)] == ids[(0, 0)] == 0


def test_escape_and_delete_in_the_schematic(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    d = Design(name="Net", kind="circuit")
    d.setups.append(circuit_setup())
    w.doc.project.designs.append(d)
    w.doc.set_design("Net")
    ed = w.circuit
    ed.place("R", 0, 0)
    ed.place("PORT", -60, 40)
    w.doc.design.wires.append(W([[-60, 0], [-30, 0]]))
    ed.rebuild()
    ed.set_mode("place", "C")
    w.a_escape.trigger()
    assert ed.mode == "select" and ed._ghost is None
    ed.set_mode("wire")
    w.a_escape.trigger()
    assert ed.mode == "select"
    # dragging the resistor keeps the wire on its pin
    it = next(i for i in ed.scene.items() if getattr(i, "comp", None) is not None and i.comp.name == "R1")
    it.setSelected(True)
    it.setPos(0, 40)
    ed.items_moved()
    assert w.doc.design.wires[0].points == [[-60, 0], [-30, 0], [-30, 40]]
    it = next(i for i in ed.scene.items() if getattr(i, "comp", None) is not None and i.comp.name == "R1")
    it.setSelected(True)
    w.a_delete.trigger()
    assert [c.name for c in w.doc.design.components] == ["Port1"]
    w.doc.dirty = False
    w.maybe_save = lambda *a: True
    w.close()


def test_microstrip_tee():
    from hertzon.solver import tlines as TL
    args = (1.1e-3, 1.1e-3, 1.1e-3, 0.508e-3, 35e-6, 3.66, 0.0037, 5.8e7)
    for f, phase in ((1e8, 0.5), (10e9, 25)):
        y = TL.tee_y(f, *args)
        s = (np.eye(3) - 50 * y) @ np.linalg.inv(np.eye(3) + 50 * y)
        assert np.allclose(y, y.T)
        assert np.allclose(abs(s[0]), [1 / 3, 2 / 3, 2 / 3], atol=0.01)          # nearly an ideal junction
        assert abs(np.angle(s[0, 1], deg=True)) < phase                          # the arms are short
    # in a circuit: three pins, the stub resonance moves up against an ideal node (the junction is shorter)
    sub = C("Sub1", "SUBST", 0, 200, props=dict(CK.TYPES["SUBST"].props))
    line = lambda n, x, y, t="MLIN", **q: C(n, t, x, y, 0, {"Subst": "Sub1", **q})  # noqa: E731
    p, d = _circuit(P("Port1", 0, 0), sub, line("MS1", 40, 0, W="1.1mm", L="9.45mm"),
                    C("MT1", "MTEE", 110, 0, 0, {"Subst": "Sub1", "W1": "1.1mm", "W2": "1.1mm", "W3": "1.1mm"}),
                    line("MSO1", 110, 70, "MLOC", W="1.1mm", L="8.45mm"), line("MS2", 180, 0, W="1.1mm", L="9.45mm"),
                    P("Port2", 220, 0))
    f = np.linspace(4.8e9, 5.6e9, 81)
    s21 = abs(analyze(p, d, f).S[:, 1, 0])
    assert 5.18e9 < f[np.argmin(s21)] < 5.28e9                                   # full wave: 5.25 GHz
