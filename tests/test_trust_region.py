"""Optimizer "Derivative Trust Region": the trust region steps and the loop of model optimum and check."""
from pathlib import Path

import numpy as np
import pytest

from hertzon.solver import trust_region as TR

EX = Path(__file__).resolve().parents[1] / "examples"


def test_bounds_are_the_tuning_range_within_min_and_max():
    ranges = np.array([[-2.0, 3.0], [-1.0, 1.0]])
    lo, hi = TR.step_bounds(ranges, 0.5, np.array([0.0, 4.5]), np.array([10.0, 10.0]), np.array([5.0, 5.0]))
    assert lo == pytest.approx([-1.0, -0.5]) and hi == pytest.approx([1.5, 0.5])
    lo, hi = TR.step_bounds(ranges, 1.0, np.array([0.0, 4.5]), np.array([5.5, 10.0]), np.array([5.0, 5.0]))
    assert lo == pytest.approx([-2.0, -0.5]) and hi == pytest.approx([0.5, 1.0])     # Min and Max win


def test_minimize_box_finds_the_optimum_and_the_closest_of_equal_ones():
    dp, c, n = TR.minimize_box(lambda d: (d[0] - 0.3) ** 2 + (d[1] + 0.2) ** 2, np.array([-1.0, -1.0]),
                               np.array([1.0, 1.0]))
    assert dp == pytest.approx([0.3, -0.2], abs=1e-3) and c < 1e-6 and n > 0
    dp, c, _ = TR.minimize_box(lambda d: (d[0] - 3.0) ** 2, np.array([-1.0]), np.array([1.0]))
    assert dp == pytest.approx([1.0], abs=1e-3)                          # at the edge of the region
    # cost 0 (all goals met) on a plateau: a point inside it, not the far end of the region
    dp, c, _ = TR.minimize_box(lambda d: max(0.0, 0.5 - d[0]), np.array([-1.0]), np.array([1.0]))
    assert c == 0 and 0.5 <= dp[0] < 0.9


def test_update_takes_good_steps_and_adapts_the_region():
    assert TR.update(0.6, 0.9, 1.0, edge=True)[:2] == (True, 1.0)        # confirmed at the edge: grows
    assert TR.update(0.6, 0.9, 1.0, edge=False)[:2] == (True, 0.6)
    assert TR.update(0.6, 0.15, 1.0, edge=True)[:2] == (True, 0.3)       # taken, but the model was poor
    assert TR.update(0.6, -0.5, 1.0, edge=True)[:2] == (False, 0.3)      # worse: rejected


class _Model:
    def __init__(self, ranges):
        self.ranges = np.asarray(ranges, float)


class _Surrogate:
    """A model around the values v0 that overestimates the effect of a step by 50 %."""

    def __init__(self, truth, v0, ranges):
        self.truth, self.v0, self.m = truth, np.array(v0, float), _Model(ranges)

    def cost(self, dp):
        return self.truth(self.v0 + 1.5 * np.asarray(dp, float))


def test_trust_region_loop_converges_with_few_solves(tmp_path, monkeypatch):
    from hertzon.model.design import Optimization, Project
    from hertzon.solver import optimize as O
    p = Project.load(EX / "PatchAntenna.hzn")
    d = p.designs[0]
    d.optimetrics.append(Optimization(
        "TR", "Setup1 : Sweep",
        variables=[{"name": "Lp", "include": True, "min": "24mm", "max": "32mm"},
                   {"name": "inset", "include": True, "min": "2mm", "max": "14mm"}],
        goals=[{"calc": "dB(S(1,1))", "start": "2.4GHz", "stop": "2.5GHz", "cond": "<=", "value": "-10"}],
        props={"Optimizer": "Derivative Trust Region", "TrustRadius": "0.6", "MaxRounds": "10"}))
    p.save(tmp_path / "p.hzn")
    opt = O.Optimizer(str(tmp_path / "p.hzn"), d.name, "TR", tmp_path / "out")
    target = np.array([27.0, 9.0])
    truth = lambda v: float(max(0.0, np.sum((np.asarray(v) - target) ** 2) - 0.01))   # noqa: E731
    solves, models = [], []

    def solve(points, phase, iteration):
        out = []
        for x in points:
            opt.n += 1
            v = opt.vars.values(x)
            solves.append(v)
            folder = tmp_path / "out" / "evals" / f"{opt.n:04d}"
            folder.mkdir(parents=True, exist_ok=True)
            opt.folders[opt.n] = folder
            c = truth(v)
            opt.history.append({"eval": opt.n, "phase": phase, "cost": c})
            if opt.best is None or c < opt.best["cost"]:
                opt.best = {"eval": opt.n, "cost": c, "variables": opt.vars.texts(x)}
            out.append(c)
        return out

    def model(n, it, points):
        v0 = solves[n - 1]
        models.append(v0)
        return _Surrogate(truth, v0, [[-2.0, 2.0], [-3.0, 3.0]])

    monkeypatch.setattr(opt, "_solve_batch", solve)
    monkeypatch.setattr(opt, "_tuning_model", model)
    assert opt.run()
    import json
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["reason"] == "All goals are met (cost 0)", summary
    assert len(solves) <= 8 and len(models) < len(solves)          # a rejected step reuses the model
    assert np.sum((solves[-1] - target) ** 2) <= 0.01 + 1e-9
    assert all("predicted" in r for r in opt.history[1:])

    # a tuning model that fails (e.g. out of memory) ends the run with the best design so far
    (tmp_path / "out2").mkdir()
    opt2 = O.Optimizer(str(tmp_path / "p.hzn"), d.name, "TR", tmp_path / "out2")
    solves.clear()
    monkeypatch.setattr(opt2, "_solve_batch", lambda pts, ph, it: (setattr(opt2, "n", opt2.n + 1),
                                                                   opt2.folders.__setitem__(opt2.n, tmp_path),
                                                                   [1.0])[2])

    def fail(n, it, points):
        raise RuntimeError("The tuning model of evaluation 1 failed: out of memory")
    monkeypatch.setattr(opt2, "_tuning_model", fail)
    assert opt2.run()
    summary = json.loads((tmp_path / "out2" / "summary.json").read_text())
    assert summary["reason"].startswith("The tuning model of evaluation 1 failed")


def test_trust_region_needs_at_most_six_variables(tmp_path):
    from hertzon.model.design import Optimization, Project
    from hertzon.solver import optimize as O
    p = Project.load(EX / "PatchAntenna.hzn")
    d = p.designs[0]
    names = list(d.variables)[:7]
    d.optimetrics.append(Optimization(
        "TR", "Setup1 : Sweep", variables=[{"name": n, "include": True, "min": "0.1mm", "max": "99mm"} for n in names],
        goals=[{"calc": "dB(S(1,1))", "start": "2.4GHz", "cond": "<=", "value": "-10"}],
        props={"Optimizer": "Derivative Trust Region"}))
    p.save(tmp_path / "p.hzn")
    with pytest.raises(ValueError, match="at most 6"):
        O.Optimizer(str(tmp_path / "p.hzn"), d.name, "TR", tmp_path / "out")
