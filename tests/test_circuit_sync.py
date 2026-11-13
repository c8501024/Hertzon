"""Circuit and 3D design as a linked pair (model/circuit_sync.py): EM blocks, calibration, comparison."""
import copy
import types

import numpy as np
import pytest

from hertzon.model import circuit as CK
from hertzon.model import circuit_from_3d as CF
from hertzon.model import circuit_sync as CS
from hertzon.model import structures as S
from hertzon.model.design import Project
from hertzon.solver import circuit as SC


def _pair(key):
    res = S.create_structure(key)
    p = Project(key)
    p.materials += res.materials
    p.designs += res.designs
    d3 = next(d for d in res.designs if d.kind != "circuit" and d.objects)
    rc, notes = CF.circuit_from_design(p, d3)
    p.designs.append(rc)
    return p, d3, rc, notes


def test_wilkinson_junction_becomes_a_3d_design_block():
    p, d3, rc, notes = _pair("wilkinson")
    blocks = [d for d in p.designs if d.settings.get(CF.EM_OF) == d3.name]
    assert len(blocks) == 1
    b = blocks[0]
    assert [e.kind for e in b.excitations] == ["Lumped Port"] * 3 and b.setups
    em = next(c for c in rc.components if c.type == "EM")
    assert em.props["Design"] == b.name and em.props["Ports"] == "3"
    _, nodes, _, _ = CK.netlist(rc.components, rc.wires, False)
    assert all(n > 0 for n in nodes[em.name])          # every pin on a line
    # the block builds, its ports stand at the ends of its copper
    from hertzon.kernel import occ as K
    import gmsh
    K.ensure_gmsh("t")
    gmsh.model.add("t_block")
    try:
        assert K.build_design(b, b.evaluator(p)).errors == {}
    finally:
        gmsh.model.remove()


def test_blocks_can_be_left_out():
    res = S.create_structure("wilkinson")
    p = Project("w")
    p.materials += res.materials
    p.designs += res.designs
    d3 = next(d for d in res.designs if d.kind != "circuit" and d.objects)
    rc, notes = CF.circuit_from_design(p, d3, blocks=False)
    assert not any(c.type == "EM" for c in rc.components)
    assert not any(d.settings.get(CF.EM_OF) for d in p.designs)


def test_calibration_finds_the_factors_of_known_data():
    p, d3, rc, _ = _pair("stubbs")
    # "3D" data: the circuit itself with lines 4 % longer and 6 % narrower
    ref = copy.deepcopy(rc)
    ref.name = "ref"
    p.designs.append(ref)
    CS.add_factors(ref, ("Cal_L", "Cal_W"))
    ref.variables.update(Cal_L="1.04", Cal_W="0.94")
    f = np.linspace(1e9, 5e9, 41)
    data = types.SimpleNamespace(freq=f, S=SC.analyze(p, ref, f).S,
                                 names=[e.name for e in sorted(d3.ports(), key=lambda e: int(e.props["PortNumber"]))])
    r = CS.calibrate(p, rc, data=data)
    assert r.rms_before > 0.05 and r.rms_after < 1e-4
    assert abs(r.values["Cal_L"] - 1.04) < 1e-3 and abs(r.values["Cal_W"] - 0.94) < 1e-3
    assert rc.variables["Cal_L"] and all("Cal_L" in c.props["L"] for c in rc.components if c.type == "MLIN")
    assert CS.strip_factor("MS3_L*Cal_L") == "MS3_L" and CS.strip_factor("(a+b)*Cal_W") == "a+b"


def test_compare_and_apply_in_both_directions():
    p, d3, rc, _ = _pair("qwt")
    assert CS.compare(p, rc) == []
    var = next(k for k in d3.variables if k.endswith("_L") and k in rc.variables)
    d3.variables[var] = "12.5mm"
    diffs = CS.compare(p, rc)
    assert [(d.kind, d.name) for d in diffs] == [("variable", var)]
    CS.apply(p, rc, [(diffs[0], CS.TO_CIRCUIT)])
    assert rc.variables[var] == "12.5mm" and CS.compare(p, rc) == []
    rc.variables[var] = "13mm"
    CS.apply(p, rc, [(CS.compare(p, rc)[0], CS.TO_3D)])
    assert d3.variables[var] == "13mm"
    # a dimension typed as a number in the circuit: back to the 3D variable, or written into it
    line = next(c for c in rc.components if c.type == "MLIN")
    line.props["W"] = "1.5mm"
    d = next(x for x in CS.compare(p, rc) if x.kind == "dimension")
    assert d.name == f"{line.name}.W" and CS.TO_3D in d.directions
    CS.apply(p, rc, [(d, CS.TO_3D)])
    assert d3.variables[d.target] == "1.5mm"
    # now the circuit's variable differs, and the part has a number instead of the shared variable
    diffs = CS.compare(p, rc)
    assert sorted(x.kind for x in diffs) == ["dimension", "variable"]
    CS.apply(p, rc, [(x, CS.TO_CIRCUIT) for x in diffs])
    assert line.props["W"] == d.target and rc.variables[d.target] == "1.5mm" and CS.compare(p, rc) == []


def test_topology_change_in_3d_rebuilds_the_circuit():
    p, d3, rc, _ = _pair("stub")
    CS.add_factors(rc, ("Cal_L",))
    rc.variables["Cal_L"] = "1.02"
    stub = next(c for c in rc.components if c.type in ("MLOC", "MLSC"))
    objs = json_objects(rc, stub.name)
    for o in objs:                       # remove the stub from the 3D design
        d3.objects = [x for x in d3.objects if x.name != o]
        for a in d3.boundaries:
            a.objects = [x for x in a.objects if x != o]
    diffs = CS.compare(p, rc)
    assert any(d.kind == "topology" and d.name == stub.name for d in diffs)
    done = CS.apply(p, rc, [(d, CS.TO_CIRCUIT) for d in diffs if d.kind == "topology"])
    assert done and not any(c.type in ("MLOC", "MLSC") for c in rc.components)
    assert rc.variables["Cal_L"] == "1.02" and all("Cal_L" in c.props["L"] for c in rc.components
                                                   if c.type == "MLIN")


def json_objects(rc, name):
    import json
    return json.loads(rc.settings[CF.TABLE])[name]["objects"]


def test_not_a_recognized_circuit():
    p = Project("x")
    from hertzon.model.design import Design
    d = Design(name="C", kind="circuit")
    p.designs.append(d)
    with pytest.raises(CS.SyncError):
        CS.compare(p, d)


def test_sync_dialog_in_the_window(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog
    QApplication.instance() or QApplication([])
    from hertzon.gui import circuit_sync_dialog as SD
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    p, d3, rc, _ = _pair("qwt")
    w.doc.project.materials += p.materials
    w.doc.project.designs += p.designs
    w.doc.set_design(d3.name)
    var = next(k for k in d3.variables if k.endswith("_L") and k in rc.variables)
    d3.variables[var] = "11mm"
    seen = {}

    def run(self):
        seen["rows"] = self.table.rowCount()
        self.set_all(CS.TO_CIRCUIT)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SD.SyncDialog, "exec", run)
    w.circuit_sync_3d()                       # from the 3D design: its newest recognized circuit
    assert seen["rows"] == 1
    assert w.doc.project.design(rc.name).variables[var] == "11mm"
