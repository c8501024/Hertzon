"""Memory of the derivative tuning model: the estimate from the solve on the same mesh, the warning before
the start, and Conjugate Gradient instead of the trust region when the model does not fit."""
import json
from pathlib import Path

import numpy as np
import pytest

from hertzon.solver import tuning as T

EX = Path(__file__).resolve().parents[1] / "examples"
GB = 2 ** 30


# (unknowns, variables, measured peak GB) on simstation02, patch antenna 2.45 GHz at the solution frequency
MEASURED = [(231112, 1, 5.65), (231112, 2, 9.24), (231112, 3, 13.62), (231112, 4, 19.01), (231112, 6, 32.80),
            (522360, 1, 12.58), (522360, 2, 20.75), (522360, 4, 42.88), (522360, 6, 74.36),
            (2051544, 1, 50.77), (2051544, 2, 82.09)]


@pytest.mark.parametrize("unknowns,n,gb", MEASURED)
def test_estimate_follows_the_measurements(unknowns, n, gb):
    assert T.memory_estimate(n, unknowns) == pytest.approx(gb * GB, rel=0.04)


def test_band_model_needs_less_and_states_count():
    assert [T.mesh_states(n) for n in (1, 2, 3, 6)] == [3, 6, 10, 28]
    # measured over a sweep: 2 variables 5.88 GB, 4 variables 11.07 GB (the estimate errs on the safe side)
    for n, gb in ((2, 5.88), (4, 11.07)):
        assert gb * GB <= T.memory_estimate(n, 231112, band=True) <= 1.15 * gb * GB


def test_shortage_names_the_variables_that_fit():
    u = 231112
    assert T.memory_shortage(2, u, 64 * GB) is None
    assert T.memory_shortage(2, 0, 1 * GB) is None              # nothing known: no warning
    short = T.memory_shortage(3, u, 12 * GB)
    assert short is not None and short["fit"] == 2 and short["need"] > 13 * GB
    assert T.memory_shortage(2, u, 4 * GB)["fit"] == 0
    assert T.memory_shortage(3, u, 12 * GB, band=True) is None
    msg = T.memory_message(3, short)
    assert msg.startswith("not enough memory") and "at most 2 variables" in msg
    assert "Remote" in msg and "Conjugate Gradient" in msg


def test_unknowns_of_the_last_adaptive_pass(tmp_path):
    assert T.solved_unknowns(tmp_path) == 0
    (tmp_path / "convergence.json").write_text(json.dumps({"passes": [{"unknowns": 9}, {"unknowns": 231112}]}))
    assert T.solved_unknowns(tmp_path) == 231112


def test_out_of_memory_is_told_from_other_failures():
    from hertzon.solver.optimize import out_of_memory
    assert out_of_memory("The tuning model of evaluation 1 failed: not enough memory for the tuning model")
    assert out_of_memory("MemoryError: Unable to allocate 2.1 GiB")
    assert out_of_memory("the solver process ended with code -9, for example out of memory; see x")
    assert not out_of_memory("the solver process ended with code 1, for example out of memory; see x")
    assert not out_of_memory("variable 'w' does not change the geometry of the model")


def _project(tmp_path):
    from hertzon.model.design import Optimization, Project
    p = Project.load(EX / "PatchAntenna.hzn")
    d = p.designs[0]
    d.optimetrics.append(Optimization(
        "TR", "Setup1 : Sweep",
        variables=[{"name": "Lp", "include": True, "min": "24mm", "max": "32mm"},
                   {"name": "inset", "include": True, "min": "2mm", "max": "14mm"}],
        goals=[{"calc": "dB(S(1,1))", "start": "2.4GHz", "stop": "2.5GHz", "cond": "<=", "value": "-10"}],
        props={"Optimizer": "Derivative Trust Region", "MaxEvaluations": "40"}))
    p.save(tmp_path / "p.hzn")
    return d.name


def _solver(opt, tmp_path, truth):
    def solve(points, phase, iteration):
        out = []
        for x in points:
            opt.n += 1
            opt.folders[opt.n] = tmp_path
            c = truth(opt.vars.values(x))
            opt.history.append({"eval": opt.n, "phase": phase, "cost": c})
            if opt.best is None or c < opt.best["cost"]:
                opt.best = {"eval": opt.n, "cost": c, "variables": opt.vars.texts(x)}
            out.append(c)
        return out
    return solve


def test_trust_region_continues_with_conjugate_gradient_without_memory(tmp_path, monkeypatch):
    from hertzon.solver import optimize as O
    name = _project(tmp_path)
    (tmp_path / "out").mkdir()
    opt = O.Optimizer(str(tmp_path / "p.hzn"), name, "TR", tmp_path / "out")
    target = np.array([27.0, 9.0])
    truth = lambda v: float(np.sum((np.asarray(v) - target) ** 2))   # noqa: E731
    monkeypatch.setattr(opt, "_solve_batch", _solver(opt, tmp_path, truth))
    phases = []

    def fail(n, it, points):
        raise RuntimeError("The tuning model of evaluation 1 failed: " + T.memory_message(2, {
            "need": 9 * GB, "free": 4 * GB, "fit": 0}))
    monkeypatch.setattr(opt, "_tuning_model", fail)
    monkeypatch.setattr(O, "log", lambda msg, level="info": phases.append(msg))
    assert opt.run()
    assert opt.algorithm == O.CONJUGATE_GRADIENT and not opt.trust
    assert any("continues with Conjugate Gradient" in m for m in phases)
    assert opt.n > 3 and opt.best["cost"] < truth([28.0, 8.0])     # the gradient solves went on


def test_optimizer_of_the_run_can_be_chosen_at_the_start(tmp_path, monkeypatch):
    from hertzon.solver import optimize as O
    name = _project(tmp_path)
    monkeypatch.setenv(O.ALGORITHM_ENV, O.CONJUGATE_GRADIENT)
    opt = O.Optimizer(str(tmp_path / "p.hzn"), name, "TR", tmp_path / "out")
    assert opt.algorithm == O.CONJUGATE_GRADIENT and not opt.trust
    monkeypatch.setenv(O.ALGORITHM_ENV, "anything else")
    opt = O.Optimizer(str(tmp_path / "p.hzn"), name, "TR", tmp_path / "out2")
    assert opt.trust


def test_tuning_run_stops_before_the_states_when_memory_is_short(monkeypatch):
    from hertzon.solver import bridge as B
    from hertzon.solver import morph as MO
    monkeypatch.setattr(B, "_current_rss", lambda: 3.0 * GB)
    monkeypatch.setattr(T, "free_memory", lambda: 6.0 * GB)
    monkeypatch.delenv(T.NO_MEMORY_CHECK, raising=False)
    with pytest.raises(MO.MorphError, match="not enough memory"):
        T._check_free_memory(2, 231112, False, lambda s: None)
    T._check_free_memory(1, 231112, False, lambda s: None)          # 5.8 GB of 9 GB: fits
    T._check_free_memory(2, 231112, True, lambda s: None)           # over a band: 6 GB
    monkeypatch.setenv(T.NO_MEMORY_CHECK, "1")                       # Run Anyway
    T._check_free_memory(3, 231112, False, lambda s: None)


def test_gui_asks_before_a_model_that_does_not_fit(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui import tuning as GT
    from hertzon.solver import optimize as O
    from hertzon.solver.results import results_dir
    proj = tmp_path / "p.hzn"
    res = results_dir(str(proj), "D", "Setup1")
    res.mkdir(parents=True)
    (res / "convergence.json").write_text(json.dumps({"passes": [{"unknowns": 231112}]}))
    win = QWidget()
    opened = []
    win.solver, win.doc, win.des = NS(remote=None), NS(project=NS(path=str(proj))), NS(name="D")
    win.options = opened.append
    shown, answer = [], {"text": ""}

    def exec_(box):
        shown.append(box.text())
        for b in box.buttons():
            if b.text() == answer["text"] or (answer["text"] == "Cancel" and box.buttonRole(b) == QMessageBox.RejectRole):
                b.click()
                break
        return 0
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(T, "free_memory", lambda: 64 * GB)
    assert GT.memory_check(win, "Setup1", 2, False) == {}             # 9.3 GB of 64 GB: no question
    assert not shown
    monkeypatch.setattr(T, "free_memory", lambda: 6 * GB)
    answer["text"] = "Conjugate Gradient"
    assert GT.memory_check(win, "Setup1", 2, False, optimization=True) == {O.ALGORITHM_ENV: O.CONJUGATE_GRADIENT}
    assert "needs about 9.3 GB" in shown[-1]
    answer["text"] = "Run Anyway"
    assert GT.memory_check(win, "Setup1", 2, False) == {T.NO_MEMORY_CHECK: "1"}
    answer["text"] = "Remote / Cloud..."
    assert GT.memory_check(win, "Setup1", 2, False) is None and opened == ["Remote"]
    answer["text"] = "Cancel"
    assert GT.memory_check(win, "Setup1", 2, False) is None
    assert GT.memory_check(win, "Setup1", 1, True) == {}               # over a band 3.7 GB: fits
    win.solver.remote = object()                                       # a remote computer solves: no question
    assert GT.memory_check(win, "Setup1", 6, False) == {}
