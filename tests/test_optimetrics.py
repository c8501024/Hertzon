import numpy as np

from hertzon.model.design import Design, Optimization, Project, Setup, project_from_dict
from hertzon.solver.keys import model_key
import pytest

from hertzon.solver.optimetrics import (ConjugateGradient, Variables, check_antenna_goals, evaluate_goals,
                                       goal_margins, positive_area, uses_antenna)


def test_cg_converges_on_quadratic():
    t = np.array([0.3, 0.8, 0.55])

    def f(x):
        return float(np.sum((np.asarray(x) - t) ** 2 * [1, 4, 9]))
    cg = ConjugateGradient([0.5, 0.5, 0.5], {"DifferenceStep": 0.05, "InitialStep": 0.1,
                                             "GradientTolerance": 1e-4, "MaxLineTrials": 16})
    n = 0
    while not cg.done and n < 500:
        for p in cg.propose():
            cg.observe(p, f(p))
            n += 1
    assert cg.done and cg.s["value"] < 0.02 and f([0.5] * 3) > 0.3


def test_gradient_batch_is_one_sample_per_variable():
    cg = ConjugateGradient([0.5, 1.0], {})
    cg.observe(cg.propose()[0], 1.0)
    pts = np.array(cg.propose())
    assert pts.shape == (2, 2)
    assert np.allclose(pts[1], [0.5, 0.95])          # at the upper bound the step goes down


def test_positive_area_with_crossing():
    assert np.isclose(positive_area([0, 1], [-1, 1]), 0.25)
    assert np.isclose(positive_area([0, 2], [1, 1]), 2.0)


def _design():
    d = Design()
    d.variables = {"w": "2mm", "g": "0.3mm", "e": "w*2"}
    d.setups = [Setup("Setup1")]
    return d


def test_variables_and_goals():
    d = _design()
    op = Optimization("Opt1", "Setup1 : Sweep", [{"name": "w", "include": True, "min": "1", "max": "3mm"},
                                                 {"name": "g", "include": False, "min": "0.1mm", "max": "0.5mm"}],
                      [{"calc": "dB(S(1,1))", "start": "1GHz", "stop": "2GHz", "cond": "<=", "value": "-10",
                        "weight": "1"}])
    ev = d.evaluator()
    v = Variables(d, op, ev)
    assert v.names == ["w"] and np.isclose(v.x0()[0], 0.5) and v.texts([1.0]) == {"w": "3mm"}
    f = np.linspace(1e9, 2e9, 11)
    S = np.zeros((11, 1, 1), complex)
    S[:, 0, 0] = 10 ** (-5 / 20)                      # -5 dB everywhere: 5 dB too high over 1 GHz
    cost, rows = evaluate_goals(op, {"freq": f, "S": S, "z0": np.array([50.0])}, ev)
    assert np.isclose(cost, 5.0) and np.isclose(rows[0]["worst"], 5.0)
    op.props["CostFunction"] = "Worst Case"
    assert np.isclose(evaluate_goals(op, {"freq": f, "S": S, "z0": np.array([50.0])}, ev)[0], 25.0)


def test_goal_margins_reserve_violation_and_errors():
    """The margins the tuning window shows: positive with reserve, negative when violated, an error for a
    goal outside the solution."""
    d = _design()
    ev = d.evaluator()
    f = np.linspace(1e9, 2e9, 11)
    S = np.zeros((11, 1, 1), complex)
    S[:, 0, 0] = 10 ** (-15 / 20)                     # -15 dB everywhere
    goals = [{"calc": "dB(S(1,1))", "start": "1GHz", "stop": "2GHz", "cond": "<=", "value": "-10"},
             {"calc": "dB(S(1,1))", "start": "1.5GHz", "stop": "1.5GHz", "cond": "<=", "value": "-20"},
             {"calc": "dB(S(1,1))", "start": "2GHz", "stop": "3GHz", "cond": "<=", "value": "-10"},
             {"calc": "", "start": "1GHz", "cond": "<=", "value": "0"}]
    rows = goal_margins(goals, {"freq": f, "S": S, "z0": np.array([50.0])}, ev)
    assert len(rows) == 3
    assert np.isclose(rows[0]["margin"], 5.0) and np.isclose(rows[1]["margin"], -5.0)
    assert "does not cover" in rows[2]["error"]


def test_antenna_goals_use_the_antenna_parameters_of_the_solution():
    """A goal on an antenna parameter is evaluated on the antenna data (its own frequencies), the other goals
    on the S-parameters; without antenna data the goal says why."""
    d = _design()
    ev = d.evaluator()
    outvars = {"G": "dB(PeakRealizedGain)", "R": "dB(S(1,1))"}
    assert uses_antenna("dB(PeakRealizedGain)") and uses_antenna("G + 1", outvars)
    assert not uses_antenna("dB(S(1,1))") and not uses_antenna("R", outvars)
    f = np.linspace(1e9, 2e9, 11)
    S = np.full((11, 1, 1), 10 ** (-15 / 20), complex)
    fa = np.array([1.4e9, 1.5e9, 1.6e9])
    ant = {"freq": fa, "solved": fa, "antenna": {"PeakRealizedGain": np.array([3.0, 4.0, 3.5]),
                                                 "RadiationEfficiency": np.array([0.7, 0.8, 0.75])}}
    goals = [{"calc": "dB(S(1,1))", "start": "1GHz", "stop": "2GHz", "cond": "<=", "value": "-10", "weight": "1"},
             {"calc": "dB(PeakRealizedGain)", "start": "1.5GHz", "stop": "1.5GHz", "cond": ">=", "value": "7",
              "weight": "1"},
             {"calc": "RadiationEfficiency", "start": "1.4GHz", "stop": "1.6GHz", "cond": ">=", "value": "0.6",
              "weight": "1"}]
    data = {"freq": f, "S": S, "z0": np.array([50.0])}
    rows = goal_margins(goals, data, ev, None, ant)
    assert np.isclose(rows[0]["margin"], 5.0)
    assert np.isclose(rows[1]["margin"], 10 * np.log10(4.0) - 7)       # 10 log10 of a gain: 6.02 dBi
    assert np.isclose(rows[2]["margin"], 0.1)
    op = Optimization("Opt1", "Setup1 : Sweep", [], goals)
    cost, _ = evaluate_goals(op, data, ev, None, ant)
    assert np.isclose(cost, 7 - 10 * np.log10(4.0))           # only the gain goal is violated (at one point)
    rows = goal_margins(goals, data, ev, None, "turn on Save fields")
    assert "margin" in rows[0] and rows[1]["error"] == "turn on Save fields"
    with pytest.raises(ValueError, match="far field setup"):
        check_antenna_goals(d, "Setup1", "LastAdaptive")


def test_optimetrics_saved_and_not_in_model_key():
    p = Project()
    d = p.designs[0]
    d.setups = [Setup("Setup1")]
    k = model_key(p, d, d.setups[0])
    d.optimetrics.append(Optimization("Opt1", "Setup1 : LastAdaptive"))
    assert model_key(p, d, d.setups[0]) == k
    q = project_from_dict(p.to_dict())
    assert q.designs[0].optimetrics[0].name == "Opt1"
    assert q.designs[0].optimetrics[0].props["DifferenceStep"] == "0.05"


def _patch_optimization(tmp_path):
    from pathlib import Path
    ex = Path(__file__).resolve().parents[1] / "examples"
    p = Project.load(ex / "PatchAntenna.hzn")
    d = p.designs[0]
    d.optimetrics.append(Optimization(
        "Opt1", "Setup1 : Sweep", variables=[{"name": "Lp", "include": True, "min": "24mm", "max": "32mm"}],
        goals=[{"calc": "dB(S(1,1))", "start": "2.4GHz", "stop": "2.5GHz", "cond": "<=", "value": "-10"}]))
    p.save(tmp_path / "p.hzn")
    return Project.load(tmp_path / "p.hzn")


def test_the_best_evaluation_keeps_its_mesh_and_fields(tmp_path):
    from types import SimpleNamespace
    from hertzon.solver import optimize as O
    from hertzon.solver.results import ResultStore
    p = _patch_optimization(tmp_path)
    opt = O.Optimizer(p.path, p.designs[0].name, "Opt1", tmp_path / "out")
    # the evaluation copy keeps the setup's own Save fields (fields at the adaptive frequency), sweeps without
    st = opt._project_for({"Lp": "28mm"}).designs[0].setup("Setup1")
    assert st.props["SaveFields"] == "True" and all(sw.props["SaveFields"] == "False" for sw in st.sweeps)
    f = np.linspace(2.3e9, 2.6e9, 7)

    def evaluation(n, s11):
        folder = tmp_path / "out" / "evals" / f"{n:04d}"
        res = ResultStore(folder / "results")
        res.save_sparams("Sweep", f, np.full((len(f), 1, 1), s11, complex), [50.0], [])
        for name in ("mesh.npz", "solution_LastAdaptive.npz", "amr_state.npz"):
            (res.folder / name).write_bytes(b"x")
        (res.folder / "convergence.json").write_text("{}")
        opt.n = n
        return SimpleNamespace(n=n, x=[0.5], texts={"Lp": f"{27 + n}mm"}, folder=folder, error="")

    kept = lambda n: (tmp_path / "out" / "evals" / f"{n:04d}" / "results" / "mesh.npz").exists()  # noqa: E731
    opt.finish(evaluation(1, 0.5), "start design")           # -6 dB: violated, the best so far
    assert kept(1)
    opt.finish(evaluation(2, 0.9), "gradient")               # worse: mesh and fields removed
    assert kept(1) and not kept(2)
    assert (tmp_path / "out" / "evals" / "0002" / "results" / "convergence.json").exists()
    opt.finish(evaluation(3, 0.1), "line search")            # better: the previous best is removed
    assert kept(3) and not kept(1)
    assert opt.best["eval"] == 3
    opt._end("done")
    assert kept(3)


def test_applied_evaluation_shows_its_solution(tmp_path):
    import json
    from hertzon.solver.parametric import (_variation_keys, evaluation_dir, solution_dir, solution_variations)
    from hertzon.solver.results import results_dir
    p = _patch_optimization(tmp_path)
    d = p.designs[0]
    out = evaluation_dir(p.path, d.name, "Opt1", 2)
    out.mkdir(parents=True)
    rows = [{"eval": 1, "variables": {"Lp": "28.5mm"}, "cost": 1.0},
            {"eval": 2, "variables": {"Lp": "29mm"}, "cost": 0.5}]
    (out.parents[2] / "history.json").write_text(json.dumps({"variables": ["Lp"], "evaluations": rows}))
    nominal = results_dir(p.path, d.name, "Setup1")
    d.variables["Lp"] = "29mm"
    (out / "status.json").write_text(json.dumps({"state": "done", **_variation_keys(p, d, "Setup1", False)}))
    labels = [lbl for lbl, f in solution_variations(p, d, "Setup1") if f == out]
    assert labels and "evaluation 2 (best)" in labels[0]
    assert solution_dir(p, d, "Setup1") == out                # after Apply: the evaluation, no new solve
    d.variables["Lp"] = "28.5mm"                              # evaluation 1 has no stored solution
    assert solution_dir(p, d, "Setup1") == nominal
    d.variables["Lp"] = "29mm"
    d.setup("Setup1").props["BasisOrder"] = "First Order"     # another problem: not the evaluation
    assert solution_dir(p, d, "Setup1") == nominal


def test_solution_data_shows_the_passes_of_an_evaluation(tmp_path):
    import json
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    from hertzon.gui.doc import Document
    from hertzon.gui.panels import SolutionDataDialog
    from hertzon.solver.parametric import evaluation_dir
    app = QApplication.instance() or QApplication([])  # noqa: F841
    p = _patch_optimization(tmp_path)
    d = p.designs[0]
    out = evaluation_dir(p.path, d.name, "Opt1", 1)
    out.mkdir(parents=True)
    (out.parents[2] / "history.json").write_text(json.dumps(
        {"variables": ["Lp"], "evaluations": [{"eval": 1, "variables": {"Lp": "29mm"}, "cost": 0.5}]}))
    (out / "status.json").write_text(json.dumps({"state": "done"}))
    (out / "convergence.json").write_text(json.dumps({"converged": True, "max_delta_s": 0.02, "passes": [
        {"pass": 1, "tets": 1000, "delta_s": 0.1}, {"pass": 2, "tets": 1500, "delta_s": 0.01, "passed": True}]}))
    dlg = SolutionDataDialog(Document(p), "Setup1")
    assert dlg.var.itemText(0) == "Nominal" and dlg.var.count() == 2
    assert "evaluation 1 (best)" in dlg.var.itemText(1)
    assert dlg.st.folder == out              # nothing solved for the design: the best evaluation is shown
    dlg.var.setCurrentIndex(0)
    dlg.var.activated.emit(0)                # the user's choice stays
    assert dlg.st.folder != out
    dlg.var.setCurrentIndex(1)
    dlg.var.activated.emit(1)
    assert dlg.st.folder == out
    texts = [lbl.text() for lbl in dlg.tabs.widget(1).findChildren(QLabel)]
    assert any("Solved Passes: 2" in t for t in texts)
    dlg.reload()                                              # a pass of a running solve: the choice stays
    assert dlg.st.folder == out
    dlg.close()


def test_applied_evaluation_becomes_the_solution_of_the_setup(tmp_path):
    """After Apply to Design the evaluation's files are the nominal solution: derivative tuning (adaptive
    mesh), Solution Data and fields work on it as after an analysis."""
    import json
    from hertzon.solver.keys import model_key, setup_key
    from hertzon.solver.parametric import (_variation_keys, adopt_evaluation, evaluation_dir, solution_dir)
    from hertzon.solver.results import results_dir
    p = _patch_optimization(tmp_path)
    d = p.designs[0]
    d.setup("Setup1").sweeps[0].props["SaveFields"] = "True"     # the evaluation solved with sweep fields off
    out = evaluation_dir(p.path, d.name, "Opt1", 1)
    out.mkdir(parents=True)
    (out.parents[2] / "history.json").write_text(json.dumps(
        {"variables": ["Lp"], "evaluations": [{"eval": 1, "variables": {"Lp": "29mm"}, "cost": 0.5}]}))
    d.variables["Lp"] = "29mm"
    (out / "status.json").write_text(json.dumps({"state": "done", **_variation_keys(p, d, "Setup1", False)}))
    for name in ("amr_state.npz", "mesh.npz", "solution_LastAdaptive.npz", "convergence.json"):
        (out / name).write_bytes(b"x")
    nominal = results_dir(p.path, d.name, "Setup1")
    nominal.mkdir(parents=True)
    (nominal / "sparams_Old.npz").write_bytes(b"old")            # the solution of the earlier values
    assert solution_dir(p, d, "Setup1") == out
    assert adopt_evaluation(p, d, "Setup1", out) == nominal
    assert solution_dir(p, d, "Setup1") == nominal
    assert not (nominal / "sparams_Old.npz").exists() and (nominal / "amr_state.npz").exists()
    st = json.loads((nominal / "status.json").read_text())
    s = d.setup("Setup1")
    assert st["model_key"] == model_key(p, d, s) and st["setup_key"] == setup_key(s)
    assert (out / "mesh.npz").exists()                              # the optimization keeps its record


def test_running_evaluations_are_listed(tmp_path):
    import json
    from hertzon.solver.parametric import evaluation_dir, evaluation_label, optimization_evaluations
    from hertzon.solver.results import running_status
    p = _patch_optimization(tmp_path)
    d = p.designs[0]
    first, second = (evaluation_dir(p.path, d.name, "Opt1", n) for n in (1, 2))
    for f in (first, second):
        f.mkdir(parents=True)
    run = first.parents[2]
    (run / "history.json").write_text(json.dumps(
        {"variables": ["Lp"], "evaluations": [{"eval": 1, "variables": {"Lp": "29mm"}, "cost": 0.5}]}))
    (first / "status.json").write_text(json.dumps({"state": "done"}))
    (second / "status.json").write_text(json.dumps(running_status()))
    (run / "status.json").write_text(json.dumps({"state": "done"}))
    assert [r["eval"] for _, r, _, _ in optimization_evaluations(p, d, "Setup1")] == [1]
    (run / "status.json").write_text(json.dumps(running_status()))     # the optimization runs
    rows = optimization_evaluations(p, d, "Setup1")
    assert [r["eval"] for _, r, _, _ in rows] == [1, 2]
    assert evaluation_label("Opt1", rows[1][1]) == "Opt1: evaluation 2 (solving)"
