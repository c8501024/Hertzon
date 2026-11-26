import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.model.design import Project, ParametricSetup, project_from_dict  # noqa: E402
from hertzon.solver.keys import model_key  # noqa: E402
from hertzon.solver.parametric import (entry_values, variation_table, variation_project, calc_value,  # noqa: E402
                                      variation_id, describe)

EX = Path(__file__).resolve().parents[1] / "examples"


def test_sweep_types():
    e = {"variable": "w", "type": "Linear step", "start": "1mm", "stop": "2mm", "step": "0.25mm"}
    assert entry_values(e) == ["1mm", "1.25mm", "1.5mm", "1.75mm", "2mm"] and describe(e) == "LIN 1mm 2mm 0.25mm"
    assert entry_values({**e, "step": "0.3mm"}) == ["1mm", "1.3mm", "1.6mm", "1.9mm"]   # stop is not passed
    assert entry_values({**e, "type": "Linear count", "step": "3"}) == ["1mm", "1.5mm", "2mm"]
    assert entry_values({**e, "type": "Single value"}) == ["1mm"]
    assert entry_values({"variable": "f", "type": "Decade count", "start": "1GHz", "stop": "100GHz",
                         "step": "1"}) == ["1GHz", "10GHz", "100GHz"]
    assert entry_values({"variable": "f", "type": "Octave count", "start": "1", "stop": "8", "step": "1"}) == \
        ["1", "2", "4", "8"]
    assert len(entry_values({**e, "type": "Exponential count", "step": "4"})) == 4
    with pytest.raises(ValueError):
        entry_values({**e, "step": "-0.1mm"})


def test_table_nested_and_synchronized():
    ps = ParametricSetup("P", sweeps=[
        {"variable": "a", "type": "Linear count", "start": "1mm", "stop": "2mm", "step": "2"},
        {"variable": "b", "type": "Linear count", "start": "5mm", "stop": "7mm", "step": "3"},
        {"variable": "a", "type": "Single value", "start": "3mm"}])
    rows = variation_table(ps)
    assert len(rows) == 9 and rows[0] == {"a": "1mm", "b": "5mm"} and rows[1] == {"a": "1mm", "b": "6mm"}
    for e in ps.sweeps:
        e["sync"] = "1"
    assert variation_table(ps) == [{"a": "1mm", "b": "5mm"}, {"a": "2mm", "b": "6mm"}, {"a": "3mm", "b": "7mm"}]
    ps.sweeps.pop()
    with pytest.raises(ValueError):
        variation_table(ps)                     # synchronized variables with 2 and 3 values
    assert variation_id({"a": "1mm"}) != variation_id({"a": "2mm"})


def test_parametric_setup_is_saved_and_does_not_change_the_result_key():
    p = Project.load(EX / "Microstrip.hzn")
    d = p.designs[0]
    key = model_key(p, d, d.setups[0])
    ps = ParametricSetup("ParametricSetup1", sweeps=[{"variable": "w", "type": "Linear count", "start": "2.5mm",
                                                      "stop": "3.5mm", "step": "3"}], setups=[d.setups[0].name],
                         calcs=[{"calc": "dB(S(1,1))", "solution": "Setup1 : Sweep", "func": "Max"}])
    d.parametrics.append(ps)
    assert model_key(p, d, d.setups[0]) == key
    q = project_from_dict(p.to_dict())
    assert q.designs[0].parametrics[0] == ps
    v = variation_project(p, d.name, {"w": "2.5mm"}, save_fields=False)
    vd = v.design(d.name)
    assert vd.variables["w"] == "2.5mm" and not vd.parametrics and d.variables["w"] != "2.5mm"
    assert vd.setups[0].props["SaveFields"] == "False"


def test_calculations():
    f = np.linspace(1e9, 3e9, 21)
    S = np.zeros((21, 1, 1), complex)
    S[:, 0, 0] = np.linspace(0.1, 0.5, 21)
    data = {"freq": f, "S": S, "z0": np.array([50.0])}
    ev = Project().designs[0].evaluator(Project())
    m = {"calc": "mag(S(1,1))", "start": "", "stop": ""}
    assert np.isclose(calc_value({**m, "func": "Max"}, data, ev), 0.5)
    assert np.isclose(calc_value({**m, "func": "Min", "start": "2GHz", "stop": "3GHz"}, data, ev), 0.3)
    assert np.isclose(calc_value({**m, "func": "Value", "start": "1.5GHz"}, data, ev), 0.2)
    assert np.isclose(calc_value({**m, "func": "Mean"}, data, ev), 0.3)
    assert np.isclose(calc_value({**m, "func": "Peak to Peak"}, data, ev), 0.4)


def test_parametric_dialog_stores_and_checks_names(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hertzon.gui.doc import Document
    from hertzon.gui.optimetrics import ParametricDialog, new_parametric
    doc = Document(Project.load(EX / "Microstrip.hzn"))
    ps = new_parametric(doc)
    ps.calcs = [{"calc": "dB(S(1,1))", "solution": "Setup1 : Sweep", "func": "Min", "start": "", "stop": ""}]
    assert ps.name == "ParametricSetup1" and ps.setups == [s.name for s in doc.design.setups]
    dlg = ParametricDialog(None, doc, ps)
    dlg.sweeps.append({"variable": "w", "type": "Linear step", "start": "2mm", "stop": "3mm", "step": "0.5mm"})
    dlg._fill_defs()
    assert dlg.table.rowCount() == 3
    assert dlg._store() and ps.sweeps[0]["variable"] == "w"
    # no Calculations tab (Results > Create Report with Families plots); calcs of older projects are kept
    assert "Calculations" not in [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert ps.calcs[0]["calc"] == "dB(S(1,1))"
    doc.design.parametrics.append(ps)
    other = new_parametric(doc)
    dlg2 = ParametricDialog(None, doc, other)
    dlg2.name.setText("ParametricSetup1")
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    assert not dlg2._store()                    # name already used
    dlg.close()
    dlg2.close()
    del app


def test_benchmark_recommendation():
    from hertzon.solver.benchmark import default_cores, CASES, CASES_DIR
    from hertzon.gui.benchmark import recommendation
    assert default_cores(6) == [1, 2, 4, 6] and default_cores(8) == [1, 2, 4, 8] and default_cores(1) == [1]
    for _, f, design, setup in CASES:
        p = Project.load(CASES_DIR / f)
        st = p.design(design).setup(setup)
        assert st.props["MaximumPasses"] == "50" and st.props["Threads"] == "0"
    run = lambda c, t: {"kind": "cores", "cores": c, "total": t, "tets": 1000}   # noqa: E731
    res = {"machine": {"logical_cores": 8}, "runs": [run(8, 50), run(4, 52), run(2, 80), run(1, 150)]}
    text, cores, workers = recommendation(res)
    assert cores == 8 and workers is None and "With 4 cores" in text
    res["runs"] += [{"kind": "sweep", "cores": 8, "workers": 1, "sweep": 40},
                    {"kind": "sweep", "cores": 8, "workers": 4, "sweep": 20}]
    assert recommendation(res)[2] == 4
    same = {"machine": {"logical_cores": 64}, "runs": [run(64, 30.6), run(32, 29.3), run(16, 29.4), run(1, 77.7)]}
    assert recommendation(same)[1] == 16        # as fast as 32 within the measuring accuracy


def test_solution_of_a_variation_is_found_without_solving(tmp_path):
    import json
    from hertzon.solver.parametric import parametric_dir, solution_dir, _variation_keys
    from hertzon.solver.results import results_dir
    p = Project.load(EX / "Microstrip.hzn")
    d = p.designs[0]
    ps = ParametricSetup("P1", sweeps=[{"variable": "w", "type": "Single value", "start": "2.5mm"}], setups=["Setup1"])
    d.parametrics.append(ps)
    p.save(tmp_path / "ms.hzn")
    out = parametric_dir(p.path, d.name, "P1")
    vid = variation_id({"w": "2.5mm"})
    folder = out / "variations" / vid / "Setup1"
    folder.mkdir(parents=True)
    (out / "table.json").write_text(json.dumps({"variations": [{"id": vid, "variables": {"w": "2.5mm"},
                                                                "state": {"Setup1": "done"}}]}))
    nominal = results_dir(p.path, d.name, "Setup1")
    assert solution_dir(p, d, "Setup1") == nominal
    d.variables["w"] = "2.5mm"
    keys = _variation_keys(p, d, "Setup1", save_fields=False)
    (folder / "status.json").write_text(json.dumps({"state": "done", **keys}))
    assert solution_dir(p, d, "Setup1") == folder            # the variation, not the unsolved nominal
    d.variables["h"] = "1.7mm"                               # another design: no stored solution
    assert solution_dir(p, d, "Setup1") == nominal


def test_setup_defaults_follow_basis_order(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])
    from hertzon.model import schema
    from hertzon.model.design import Setup
    from hertzon.gui.doc import Document
    from hertzon.gui import dialogs
    d = schema.defaults(schema.SETUP)
    assert d["BasisOrder"] == "First Order" and d["LambdaTarget"] == "0.3333"
    doc = Document(Project.load(EX / "Microstrip.hzn"))
    st = Setup("S")
    st.props = dict(d)

    def run(self):
        grid = next(g for g in self.grid.grids.values() if any(p.name == "BasisOrder" for p in g.props))
        grid._set("BasisOrder", "Second Order", rebuild=True)
        app.processEvents()
        return QDialog.Accepted
    monkeypatch.setattr(dialogs.NamedPropsDialog, "exec", run)
    assert dialogs.edit_setup(None, doc, st)
    assert st.props["BasisOrder"] == "Second Order" and st.props["LambdaTarget"] == "0.6667"
    st.props["LambdaTarget"] = "0.25"                 # a value of its own stays
    st.props["BasisOrder"] = "First Order"
    assert dialogs.edit_setup(None, doc, st) and st.props["LambdaTarget"] == "0.25"
