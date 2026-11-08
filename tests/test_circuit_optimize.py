"""Optimization of circuit designs: every evaluation is a circuit analysis in the optimizer process."""
import json
import time

import numpy as np
import pytest

from hertzon.model.circuit import Component as C
from hertzon.model.design import Design, Optimization, Project, circuit_setup
from hertzon.solver.optimize import Optimizer


def _transformer(tmp_path, optimizer="Conjugate Gradient"):
    """A quarter-wave line between a 50 ohm and a 100 ohm port: matched at 2 GHz for Z0 = sqrt(50 * 100)."""
    p = Project()
    d = Design(name="Match", kind="circuit")
    d.variables = {"zt": "50ohm"}
    d.components = [C("Port1", "PORT", 20, 40, 0, {"Z0": "50ohm"}),
                    C("TL1", "TLIN", 60, 0, 0, {"Z0": "zt", "E": "90deg", "F": "2GHz"}),
                    C("Port2", "PORT", 100, 40, 0, {"Z0": "100ohm"})]
    st = circuit_setup()
    st.sweeps[0].props.update(Start="1.9GHz", Stop="2.1GHz", Count="5")
    d.setups.append(st)
    op = Optimization("Opt1", "Setup1 : Sweep",
                      [{"name": "zt", "include": True, "min": "40ohm", "max": "100ohm"}],
                      [{"calc": "dB(S(1,1))", "start": "2GHz", "stop": "2GHz", "cond": "<=", "value": "-40",
                        "weight": "1"}],
                      {"Optimizer": optimizer, "MaxEvaluations": "400"})
    d.optimetrics.append(op)
    p.designs.append(d)
    path = tmp_path / "match.hzn"
    p.save(path)
    return path


@pytest.mark.parametrize("optimizer", ["Conjugate Gradient", "Latin Hypercube + Nelder-Mead"])
def test_circuit_optimization_finds_the_quarter_wave_transformer(tmp_path, optimizer):
    out = tmp_path / "opt"
    out.mkdir()
    t = time.time()
    assert Optimizer(str(_transformer(tmp_path, optimizer)), "Match", "Opt1", out).run()
    assert time.time() - t < 30                              # no solver processes, no waiting between designs
    best = json.loads((out / "best.json").read_text())
    zt = float(best["variables"]["zt"].replace("ohm", ""))
    assert abs(zt - np.sqrt(5000)) < 1.5, zt
    hist = json.loads((out / "history.json").read_text())
    assert len(hist["evaluations"]) == json.loads((out / "summary.json").read_text())["evaluations"]
    assert not (out / "evals").exists()                     # nothing written per evaluation


def test_circuit_rejects_the_trust_region(tmp_path):
    with pytest.raises(ValueError, match="3D mesh"):
        Optimizer(str(_transformer(tmp_path, "Derivative Trust Region")), "Match", "Opt1", tmp_path)


def test_window_lists_circuit_optimizations_and_applies_the_best(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.main_window import MainWindow
    from hertzon.gui.optimetrics import goal_solutions
    from hertzon.gui.solve import optimization_dir
    path = _transformer(tmp_path)
    w = MainWindow()
    w.file_open(str(path))
    w.doc.set_design("Match")
    assert goal_solutions(w.des) == ["Setup1 : Sweep"]          # no adaptive solution in a circuit
    w.project_tree.rebuild()
    labels = []

    def walk(it):
        labels.append(it.text(0))
        for i in range(it.childCount()):
            walk(it.child(i))
    for i in range(w.project_tree.topLevelItemCount()):
        walk(w.project_tree.topLevelItem(i))
    assert "Optimization" in labels and "Opt1" in labels
    out = optimization_dir(str(path), "Match", "Opt1")
    out.mkdir(parents=True, exist_ok=True)
    assert Optimizer(str(path), "Match", "Opt1", out).run()
    w.apply_best_variables("Opt1")
    zt = float(w.des.variables["zt"].replace("ohm", ""))
    assert abs(zt - np.sqrt(5000)) < 1.5
    from hertzon.solver.results import ResultStore, results_dir
    data = ResultStore(results_dir(str(path), "Match", "Setup1")).sparams("Sweep")
    assert data is not None and np.abs(data["S"][2, 0, 0]) < 0.02    # analyzed again with the applied value
