"""Circuit designs: schematic connectivity, the linear network solver and the schematic editor."""
import os

import numpy as np
import pytest

from hertzon.model import circuit as CK
from hertzon.model.circuit import Component as C, Wire as W
from hertzon.model.design import Design, Project, circuit_setup, project_from_dict
from hertzon.solver.circuit import CircuitError, analyze, run_setup
from hertzon.solver.results import ResultStore, results_dir


def _circuit(*comps, wires=()):
    p = Project()
    d = Design(name="C", kind="circuit")
    d.components, d.wires = list(comps), list(wires)
    p.designs.append(d)
    return p, d


def test_pins_rotate_and_nets_join_wires_and_t_junctions():
    r = C("R1", "R", 50, 30, 90)
    assert CK.pins(r) == [(50, 0), (50, 60)]
    comps = [C("P1", "PORT", 0, 0), r, C("G", "GND", 50, 70), C("P2", "PORT", 100, 0, 180)]
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
    p, d = _circuit(C("Port1", "PORT", 0, 0), C("R1", "R", 50, 0, 0, {"R": "50ohm"}), C("Port2", "PORT", 100, 0, 180))
    r = analyze(p, d, np.array([1e9]))
    assert r.ports == ["Port1", "Port2"]
    assert np.allclose(r.S[0], [[1 / 3, 2 / 3], [2 / 3, 1 / 3]])
    d.components = [C("Port1", "PORT", 0, 0), C("TL1", "TLIN", 60, 0, 0, {"Z0": "50ohm", "E": "90deg", "F": "1GHz"}),
                    C("Port2", "PORT", 120, 0, 180)]
    r = analyze(p, d, np.array([1e9, 2e9]))
    assert np.allclose(r.S[:, 1, 0], [-1j, -1], atol=1e-6) and np.allclose(abs(r.S[:, 0, 0]), 0, atol=1e-6)


def test_shunt_capacitor_against_the_formula():
    p, d = _circuit(C("Port1", "PORT", 0, 0), C("C1", "C", 50, 30, 90, {"C": "1pF"}), C("G", "GND", 50, 70),
                    C("R1", "R", 110, 0, 0, {"R": "1e-9ohm"}), C("Port2", "PORT", 160, 0, 180),
                    wires=[W([[20, 0], [80, 0]])])
    r = analyze(p, d, np.array([1e9]))
    yc = 1j * 2 * np.pi * 1e9 * 1e-12
    assert abs(r.S[0, 1, 0] - 2 / (2 + 50 * yc)) < 1e-6


def test_errors_are_explained():
    p, d = _circuit(C("R1", "R", 0, 0))
    with pytest.raises(CircuitError, match="no port"):
        analyze(p, d, np.array([1e9]))
    d.components = [C("Port1", "PORT", 0, 0), C("G", "GND", 20, 10)]
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
    d.components = [blk, C("Port1", "PORT", a[0] - 20, a[1]), C("Port2", "PORT", b[0] + 20, b[1], 180)]
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
