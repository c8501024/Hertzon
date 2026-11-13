"""Circuit from a planar 3D design (model/circuit_from_3d.py): round trip circuit -> 3D layout -> circuit."""
import numpy as np
import pytest

from hertzon.model import circuit_from_3d as CF
from hertzon.model import structures as S
from hertzon.model.design import Project
from hertzon.solver import circuit as SC


def _structure(key):
    res = S.create_structure(key)
    p = Project(key)
    p.materials += res.materials
    circ, d3 = res.designs[0], res.designs[1]
    p.designs += [circ, d3]
    return p, circ, d3


def _s(p, d, f):
    return SC.analyze(p, d, f).S


@pytest.mark.parametrize("key", ["qwt", "stub", "branchline", "stepped", "stubbp", "stubbs", "edge"])
def test_round_trip_gives_the_original_circuit(key):
    p, circ, d3 = _structure(key)
    rc, notes = CF.circuit_from_design(p, d3)
    p.designs.append(rc)
    assert CF.compare_netlists(circ, rc) == []
    assert rc.settings[CF.SOURCE] == d3.name
    f = np.linspace(0.3, 2.0, 25) * float(SC.max_frequency(p, circ) or 5e9)
    assert np.max(np.abs(_s(p, circ, f) - _s(p, rc, f))) < 1e-9


def test_ring_of_the_rat_race_is_recognized():
    p, circ, d3 = _structure("ratrace")
    rc, _ = CF.circuit_from_design(p, d3)
    p.designs.append(rc)
    f = np.linspace(0.3, 2.0, 25) * float(SC.max_frequency(p, circ) or 5e9)
    assert np.max(np.abs(_s(p, circ, f) - _s(p, rc, f))) < 1e-4
    types = [c.type for c in rc.components]
    assert types.count("MTEE") == 4 and types.count("PORT") == 4


def test_dimensions_are_linked_to_the_3d_variables():
    p, circ, d3 = _structure("qwt")
    rc, _ = CF.circuit_from_design(p, d3)
    lines = [c for c in rc.components if c.type == "MLIN"]
    assert lines and all(c.props["W"] in d3.variables and c.props["L"] in d3.variables for c in lines)


def test_tee_divider_finds_the_width_steps():
    p, circ, d3 = _structure("tee")
    rc, _ = CF.circuit_from_design(p, d3)
    diff = CF.compare_netlists(circ, rc)
    assert diff and all(d.startswith("only in recognized: ('MSTEP'") for d in diff)


def test_unrecognized_copper_is_reported_not_guessed():
    p, circ, d3 = _structure("wilkinson")
    rc, notes = CF.circuit_from_design(p, d3)
    assert any("no circuit model" in n for n in notes)


def test_circuit_design_is_refused():
    p, circ, d3 = _structure("qwt")
    with pytest.raises(ValueError):
        CF.circuit_from_design(p, circ)


def test_create_circuit_button(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    p, circ, d3 = _structure("stub")
    w.doc.project.materials += p.materials
    w.doc.project.designs += [circ, d3]
    w.doc.set_design(d3.name)
    before = {d.name for d in w.doc.project.designs}
    w.circuit_from_3d()
    assert w.des.kind == "circuit" and w.des.name not in before and w.des.name.startswith(f"{d3.name}_Circuit")
    assert w.des.settings[CF.SOURCE] == d3.name
    assert CF.compare_netlists(circ, w.des) == []
    first = w.des.name
    w.doc.set_design(d3.name)
    w.circuit_from_3d()
    assert w.des.name not in (first, circ.name, d3.name) and w.des.kind == "circuit"
