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
MEASURED = [(231112, 1, 3.2), (231112, 2, 3.1), (231112, 3, 3.6), (231112, 4, 4.2), (231112, 6, 5.7),
            (522360, 1, 6.9), (522360, 2, 7.0), (522360, 4, 9.1), (522360, 6, 12.8),
            (2051544, 1, 28.2), (2051544, 2, 28.2), (2051544, 4, 35.2)]


@pytest.mark.parametrize("unknowns,n,gb", MEASURED)
def test_estimate_follows_the_measurements(unknowns, n, gb):
    est = T.memory_estimate(n, unknowns) / GB
    assert 0.97 * gb <= est <= 1.08 * gb


# over a sweep (unknowns, variables, measured peak GB): simstation02 and a 15 GB cloud computer
MEASURED_BAND = [(231112, 1, 3.52), (231112, 2, 4.30), (231112, 4, 6.67), (231112, 6, 10.44),
                 (231112, 1, 3.75), (231112, 2, 4.51), (231112, 4, 6.72), (231112, 6, 10.30),
                 (522360, 1, 7.62), (522360, 2, 9.31), (522360, 4, 14.48)]


@pytest.mark.parametrize("unknowns,n,gb", MEASURED_BAND)
def test_band_estimate_follows_the_measurements(unknowns, n, gb):
    est = T.memory_estimate(n, unknowns, band=True) / GB
    assert 0.98 * gb <= est <= 1.12 * gb


def test_states_count_and_band_needs_more():
    assert [T.mesh_states(n) for n in (1, 2, 3, 6)] == [3, 6, 10, 28]
    assert T.memory_estimate(4, 231112, band=True) > T.memory_estimate(4, 231112)


def test_shortage_names_the_variables_that_fit():
    u = 2051544                                                     # one frequency: 28.7 GB up to 2 variables
    assert T.memory_shortage(2, u, 64 * GB) is None
    assert T.memory_shortage(2, 0, 1 * GB) is None                # nothing known: no warning
    short = T.memory_shortage(6, u, 42 * GB)                       # 6 variables: 50.8 GB, 4: 36.5 GB
    assert short is not None and short["fit"] == 4 and short["need"] > 50 * GB
    assert T.memory_shortage(2, u, 20 * GB)["fit"] == 0
    short = T.memory_shortage(3, 231112, 6 * GB, band=True)        # over a band: 5.7 GB, 2 variables 4.7 GB
    assert short is not None and short["fit"] == 2
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
    monkeypatch.setattr(T, "free_memory", lambda: 1.0 * GB)
    monkeypatch.delenv(T.NO_MEMORY_CHECK, raising=False)
    with pytest.raises(MO.MorphError, match="not enough memory"):
        T._check_free_memory(2, 231112, True, lambda s: None)        # over a band: 4.7 GB of 4 GB
    T._check_free_memory(1, 231112, False, lambda s: None)           # one frequency: 3.2 GB, fits
    monkeypatch.setenv(T.NO_MEMORY_CHECK, "1")                       # Run Anyway
    T._check_free_memory(4, 231112, True, lambda s: None)


def test_band_matrices_share_one_pattern_and_act_as_operators():
    import scipy.sparse as sp
    rng = np.random.default_rng(0)
    A = sp.random(60, 60, density=0.2, format="csr", random_state=1) + sp.eye(60, format="csr")
    pat = (A.indptr, A.indices)
    E0 = sp.csr_matrix((A.data.astype(complex), A.indices.copy(), A.indptr.copy()), shape=A.shape)
    Ep = sp.csr_matrix((A.data * 1.01 + 0j, A.indices.copy(), A.indptr.copy()), shape=A.shape)
    Em = sp.csr_matrix((A.data * 0.98 + 0j, A.indices.copy(), A.indptr.copy()), shape=A.shape)
    c = T._compact(E0, pat, False)
    assert c.dtype == np.float64 and np.shares_memory(c.indices, pat[1])   # real, on the shared pattern
    d = T._combination([(1, Ep), (-2, E0), (1, Em)], pat, 0.5)
    assert d.dtype == np.float32 and np.shares_memory(d.indices, pat[1])
    assert np.allclose(d.toarray(), ((Ep - 2 * E0 + Em) / 0.5).toarray().real, rtol=1e-6, atol=1e-6)
    B = sp.csr_matrix((A.data * (1 - 0.01j), A.indices, A.indptr), shape=A.shape)
    Bc = T._compact(B, pat, True)
    assert Bc.dtype == np.complex64
    R = sp.random(60, 60, density=0.02, format="csr", random_state=2) * 1j
    X = rng.standard_normal((60, 3)) + 1j * rng.standard_normal((60, 3))
    op = T._Combined(d, Bc, R, 2.5)
    ref = (d.astype(np.complex128) - 2.5 * Bc.astype(np.complex128) + R) @ X
    assert np.allclose(T._mul(op, X), ref, rtol=1e-12, atol=1e-12)    # double precision products


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
    assert GT.memory_check(win, "Setup1", 2, True) == {}              # 4.7 GB of 64 GB: no question
    assert not shown
    monkeypatch.setattr(T, "free_memory", lambda: 3 * GB)
    answer["text"] = "Conjugate Gradient"
    assert GT.memory_check(win, "Setup1", 2, False, optimization=True) == {O.ALGORITHM_ENV: O.CONJUGATE_GRADIENT}
    assert "needs about 3.2 GB" in shown[-1]
    answer["text"] = "Run Anyway"
    assert GT.memory_check(win, "Setup1", 2, False) == {T.NO_MEMORY_CHECK: "1"}
    answer["text"] = "Remote / Cloud..."
    assert GT.memory_check(win, "Setup1", 2, False) is None and opened == ["Remote"]
    answer["text"] = "Cancel"
    assert GT.memory_check(win, "Setup1", 2, False) is None
    monkeypatch.setattr(T, "free_memory", lambda: 8 * GB)
    assert GT.memory_check(win, "Setup1", 2, False) == {}              # one frequency 3.2 GB: fits
    monkeypatch.setattr(T, "free_memory", lambda: 6 * GB)
    answer["text"] = "Run Anyway"
    assert GT.memory_check(win, "Setup1", 4, True) == {T.NO_MEMORY_CHECK: "1"}   # over a band 7 GB
    win.solver.remote = object()                                       # a remote computer solves: no question
    assert GT.memory_check(win, "Setup1", 6, False) == {}
