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
