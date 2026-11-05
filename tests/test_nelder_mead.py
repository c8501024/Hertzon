"""Optimizer "Latin Hypercube + Nelder-Mead": the sample, the chains and the loop of the optimizer process."""
from pathlib import Path

import numpy as np

from hertzon.model import schema
from hertzon.solver.optimetrics import SampledNelderMead, latin_hypercube

EX = Path(__file__).resolve().parents[1] / "examples"


def _drive(alg, f, limit=5000):
    """Solve what the optimizer proposes, batch by batch as the optimizer process does."""
    n, batches = 0, []
    while not alg.done and n < limit:
        pts = alg.propose()
        if not pts:
            break
        batches.append(len(pts))
        for p in pts:
            alg.observe(p, f(np.asarray(p)))
            n += 1
    return n, batches


def test_latin_hypercube_has_one_point_in_every_slice():
    u = latin_hypercube(10, 3, np.random.default_rng(0))
    assert u.shape == (10, 3) and np.all((u >= 0) & (u < 1))
    for j in range(3):
        assert sorted(np.floor(u[:, j] * 10).astype(int)) == list(range(10))


def test_chains_find_the_global_minimum_of_a_function_with_two_minima():
    # a deep minimum far from the start and a shallow one at the start (as an antenna tuned to the wrong mode)
    def f(x):
        return float(min(1.0 + np.sum((x - 0.2) ** 2) * 10, np.sum((x - np.array([0.8, 0.7])) ** 2) * 10))
    alg = SampledNelderMead([0.2, 0.2], {"Samples": 16, "Chains": 3, "ChainEvaluations": 60})
    seen = []
    n, batches = _drive(alg, lambda x: (seen.append(x), f(x))[1])
    assert alg.done and alg.reason
    best = min(seen, key=f)
    assert np.allclose(best, [0.8, 0.7], atol=0.02), best
    assert batches[0] == 17                                  # the sample and the start design at once
    assert max(batches[1:]) >= 3                             # the chains run side by side
    assert n <= 17 + 3 * 60 + 3                              # budget per chain (a shrink may overshoot)


def test_failed_designs_are_avoided_and_cost_zero_stops():
    def f(x):
        if x[0] > 0.6:
            return float("inf")                              # e.g. a geometry the solver cannot mesh
        return max(0.0, float(np.sum((x - 0.4) ** 2)) - 1e-4)
    alg = SampledNelderMead([0.1, 0.9], {"Samples": 8, "Chains": 2, "ChainEvaluations": 80})
    _drive(alg, f)
    assert alg.reason == "All goals are met (cost 0)"


def test_the_same_seed_gives_the_same_sample():
    a = SampledNelderMead([0.5], {"Samples": 5, "Seed": 3}).propose()
    b = SampledNelderMead([0.5], {"Samples": 5, "Seed": 3}).propose()
    c = SampledNelderMead([0.5], {"Samples": 5, "Seed": 4}).propose()
    assert a == b and a != c and a[0] == [0.5]


def test_optimizer_choice_and_settings_in_the_dialog():
    names = {p.name: p for p in schema.OPTIMIZATION}
    assert "Latin Hypercube + Nelder-Mead" in names["Optimizer"].choices
    for k in ("Samples", "Chains", "ChainEvaluations", "SimplexStep", "SimplexTolerance", "Seed"):
        assert names[k].visible_if == ("Optimizer", "Latin Hypercube + Nelder-Mead")
    assert names["ParallelEvaluations"].visible_if == ("Optimizer", "!Derivative Trust Region")


def test_optimizer_process_runs_the_sample_and_the_chains(tmp_path, monkeypatch):
    import json
    from hertzon.model.design import Optimization, Project
    from hertzon.solver import optimize as O
    p = Project.load(EX / "PatchAntenna.hzn")
    d = p.designs[0]
    d.optimetrics.append(Optimization(
        "NM", "Setup1 : Sweep",
        variables=[{"name": "Lp", "include": True, "min": "24mm", "max": "32mm"},
                   {"name": "inset", "include": True, "min": "2mm", "max": "14mm"}],
        goals=[{"calc": "dB(S(1,1))", "start": "2.4GHz", "stop": "2.5GHz", "cond": "<=", "value": "-10"}],
        props={"Optimizer": "Latin Hypercube + Nelder-Mead", "Samples": "10", "Chains": "2",
               "ChainEvaluations": "30", "MaxEvaluations": "200"}))
    p.save(tmp_path / "p.hzn")
    (tmp_path / "out").mkdir()
    opt = O.Optimizer(str(tmp_path / "p.hzn"), d.name, "NM", tmp_path / "out")
    target = np.array([27.0, 9.0])
    truth = lambda v: float(np.sum(((np.asarray(v) - target) / [8, 12]) ** 2))   # noqa: E731
    phases = []

    def solve(points, phase, iteration):
        phases.append(phase)
        out = []
        for x in points:
            opt.n += 1
            c = truth(opt.vars.values(x))
            row = {"eval": opt.n, "phase": phase, "cost": c, "variables": opt.vars.texts(x)}
            opt.history.append(row)
            if opt.best is None or c < opt.best["cost"]:
                opt.best = row
            out.append(c)
        return out

    monkeypatch.setattr(opt, "_solve_batch", solve)
    assert opt.run()
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert phases[0] == "sampling" and "simplex" in phases
    assert summary["evaluations"] <= 200
    best = [float(v.rstrip("m")) for v in opt.best["variables"].values()]
    assert np.allclose(best, target, atol=0.1), best


def test_only_the_settings_tab_of_the_chosen_optimizer_is_shown():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.props import TabbedPropertyGrid
    app = QApplication.instance() or QApplication([])
    values = {**schema.defaults(schema.OPTIMIZATION), "Optimizer": "Latin Hypercube + Nelder-Mead"}
    g = TabbedPropertyGrid()
    g.set(schema.OPTIMIZATION, values)
    shown = lambda: [g.tabText(i) for i in range(g.count()) if g.isTabVisible(i)]   # noqa: E731
    assert shown() == ["General", "Nelder-Mead"]
    g.grids["General"]._set("Optimizer", "Derivative Trust Region", rebuild=True)
    for _ in range(3):
        app.processEvents()
    assert shown() == ["General", "Derivative Trust Region"]
    assert g.isTabVisible(g.currentIndex())
