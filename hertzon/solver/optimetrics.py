"""Optimization variables, goals / cost function and the conjugate gradient optimizer.

No Qt here: used by the optimizer process (hertzon.solver.optimize) and by the dialogs.

The conjugate gradient is the projected nonlinear CG (Polak-Ribiere+) of the filterbench hairpin
runs: variables scaled to 0..1 between Min and Max, one-sided finite differences for the gradient (all
samples of a gradient can be solved in parallel), a backtracking line search with the Armijo condition.
"""
from __future__ import annotations

import re

import numpy as np

from ..model.expr import ALL_UNITS

_NUM = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)\s*$")


# ---------------------------------------------------------------------------------------------
# variables
# ---------------------------------------------------------------------------------------------
def split_value(text: str) -> tuple[float, str] | None:
    """'2.5mm' -> (2.5, 'mm'); None when the value is an expression (cannot be optimized)."""
    m = _NUM.match(str(text))
    if not m or (m.group(2) and m.group(2) not in ALL_UNITS):
        return None
    return float(m.group(1)), m.group(2)


def in_unit(text: str, unit: str, ev=None) -> float:
    """A Min / Max entry in the unit of the variable (a plain number is taken in that unit)."""
    m = _NUM.match(str(text))
    if m and not m.group(2):
        return float(m.group(1))
    if m and m.group(2) == unit:
        return float(m.group(1))
    if ev is None:
        raise ValueError(f"'{text}': give the value in {unit or 'the unit of the variable'}")
    v = float(ev.value(str(text)))
    return v / ALL_UNITS[unit] if unit else v


def fmt_value(v: float, unit: str) -> str:
    return f"{float(v):.6g}{unit}"


class Variables:
    """The included optimization variables: start values and ranges in their own unit, scaling to 0..1."""

    def __init__(self, design, opt, ev=None):
        self.names, self.units, self.lo, self.hi, self.start = [], [], [], [], []
        for row in opt.variables:
            if not row.get("include", True):
                continue
            name = row["name"]
            if name not in design.variables:
                raise ValueError(f"Variable '{name}' is not a design variable")
            sv = split_value(design.variables[name])
            if sv is None:
                raise ValueError(f"Variable '{name}' = {design.variables[name]} is an expression; only "
                                 "numbers (with a unit) can be optimized")
            v, unit = sv
            lo, hi = in_unit(row.get("min", ""), unit, ev), in_unit(row.get("max", ""), unit, ev)
            if not hi > lo:
                raise ValueError(f"Variable '{name}': Max must be larger than Min")
            self.names.append(name)
            self.units.append(unit)
            self.lo.append(lo)
            self.hi.append(hi)
            self.start.append(v)
        if not self.names:
            raise ValueError("No variable is included in the optimization")
        self.lo, self.hi, self.start = map(np.array, (self.lo, self.hi, self.start))

    def __len__(self) -> int:
        return len(self.names)

    def x0(self) -> np.ndarray:
        return np.clip((self.start - self.lo) / (self.hi - self.lo), 0, 1)

    def values(self, x) -> np.ndarray:
        return self.lo + np.clip(np.asarray(x, float), 0, 1) * (self.hi - self.lo)

    def texts(self, x) -> dict[str, str]:
        return {n: fmt_value(v, u) for n, v, u in zip(self.names, self.values(x), self.units)}


# ---------------------------------------------------------------------------------------------
# goals and cost
# ---------------------------------------------------------------------------------------------
def positive_area(f, error) -> float:
    """Integral of the positive part of a piecewise linear error, with the zero crossings."""
    f, error = np.asarray(f, float), np.asarray(error, float)
    if len(f) < 2:
        return float(max(0.0, error.max())) if len(error) else 0.0
    left, right = error[:-1], error[1:]
    a, b = np.maximum(left, 0), np.maximum(right, 0)
    df = np.diff(f)
    areas = (a + b) * df / 2
    crossing = left * right < 0
    areas[crossing] = (a[crossing] + b[crossing]) ** 2 * df[crossing] / (2 * np.abs(right[crossing] - left[crossing]))
    return float(areas.sum())


def goal_range(goal: dict, ev) -> tuple[float, float]:
    """Frequency range (Hz) of a goal, ascending."""
    lo, hi = float(ev.freq(str(goal["start"]))), float(ev.freq(str(goal.get("stop") or goal["start"])))
    return (hi, lo) if hi < lo else (lo, hi)


def goal_error(goal: dict, f_hz: np.ndarray, y: np.ndarray, ev) -> tuple[np.ndarray, np.ndarray]:
    """(frequencies in Hz, error) of one goal on a response y(f): the error is positive where the goal is
    violated, its negative maximum is the margin of the goal."""
    lo, hi = goal_range(goal, ev)
    tol = 1e-6 * max(abs(hi), 1.0)
    if f_hz[0] > lo + tol or f_hz[-1] < hi - tol:
        raise ValueError(f"The solution ({f_hz[0] / 1e9:g}-{f_hz[-1] / 1e9:g} GHz) does not cover the goal range "
                         f"{lo / 1e9:g}-{hi / 1e9:g} GHz of {goal['calc']}")
    grid = np.r_[lo, f_hz[(f_hz > lo) & (f_hz < hi)], hi] if hi > lo else np.array([lo])
    v = np.interp(grid, f_hz, y)
    target = float(goal.get("value", 0))
    cond = goal.get("cond", "<=")
    if cond == ">=":
        err = target - v
    elif cond == "=":
        err = np.abs(v - target)
    elif cond.startswith("Ripple"):
        err = v.max() - v - target
    else:
        err = v - target
    return grid, err


def goal_violation(goal: dict, f_hz: np.ndarray, y: np.ndarray, ev) -> tuple[float, float]:
    """(violation area in unit*GHz, largest violation) of one goal on a response y(f)."""
    grid, err = goal_error(goal, f_hz, y, ev)
    return positive_area(grid / 1e9, err), float(max(0.0, err.max()))


def goal_margins(goals: list[dict], data: dict, ev, outvars=None, antenna=None) -> list[dict]:
    """Margin of every goal on a solution (data as for evaluate_goals): {"goal", "margin"} with the
    margin in the unit of the goal (positive: reserve, negative: violated), or {"goal", "error"} when the
    goal cannot be evaluated on it (an expression error, a range outside the solution)."""
    from .expressions import eval_expr
    out = []
    for g in goals:
        if not str(g.get("calc", "")).strip():
            continue
        try:
            d = _goal_input(g, data, antenna, outvars)
            f = np.asarray(d["freq"], float)
            order = np.argsort(f)
            y = eval_expr(d, g["calc"], outvars)
            y = np.real(y) if np.iscomplexobj(y) else np.asarray(y, float)
            _, err = goal_error(g, f[order], np.broadcast_to(y, f.shape)[order], ev)
            out.append({"goal": g, "margin": -float(np.max(err))})
        except Exception as e:  # noqa: BLE001
            out.append({"goal": g, "error": str(e)})
    return out


def uses_antenna(calc: str, outvars=None, _depth: int = 0) -> bool:
    """Does a goal expression use antenna parameters (PeakRealizedGain, RadiationEfficiency, ...), directly
    or through an output variable?"""
    from .ffquant import ANTENNA_QUANTITIES
    words = set(re.findall(r"[A-Za-z_]\w*", str(calc)))
    if words & set(ANTENNA_QUANTITIES):
        return True
    return _depth < 8 and any(uses_antenna(outvars[w], outvars, _depth + 1) for w in words & set(outvars or {}))


def antenna_data(st, design, solution: str, ev) -> dict:
    """Antenna parameters over the frequencies of a solution ({"freq", "solved", "antenna"}), for goals on
    PeakRealizedGain, RadiationEfficiency etc. They exist where the far fields were saved: at the solution
    frequency (LastAdaptive), and over a sweep only with Save fields on; the first far field setup gives the
    directions of the peak values. Raises with the reason otherwise."""
    from .ffquant import antenna_series
    if not design.spheres:
        raise ValueError("antenna parameters need a far field setup (Radiation > Insert Far Field Setup)")
    if not (st.folder / f"huygens_{solution}.npz").exists():
        raise ValueError(f"antenna parameters need the fields of '{solution}': turn on Save fields in the sweep "
                         "(the design needs a radiation or PML boundary)")
    f, vals = antenna_series(st, design, design.spheres[0].name, solution, ev)
    return {"freq": f, "solved": f, "antenna": vals}


def check_antenna_goals(design, setup: str, solution: str) -> None:
    """Before an optimization with antenna goals: the solution will have far fields (raises otherwise)."""
    if not design.spheres:
        raise ValueError("Goals on antenna parameters need a far field setup (Radiation > Insert Far Field Setup)")
    if solution == "LastAdaptive":
        return
    st = design.setup(setup)
    sw = next((s for s in st.sweeps if s.name == solution), None)
    if sw is None:
        return
    if sw.props.get("SweepType") == "Interpolating":
        raise ValueError(f"Goals on antenna parameters need the fields at every frequency: '{solution}' is an "
                         "interpolating sweep; use a discrete sweep with Save fields, or LastAdaptive")
    if str(sw.props.get("SaveFields", "False")).lower() not in ("true", "1", "yes"):
        raise ValueError(f"Goals on antenna parameters need the fields of '{solution}': turn on Save fields "
                         "(all frequencies) in the sweep")


def _goal_input(g: dict, data: dict, antenna, outvars):
    """The data a goal is evaluated on: the antenna parameters for an antenna goal, else the S-parameters.
    antenna: antenna_data, or the reason why it is not available (str)."""
    if not uses_antenna(g["calc"], outvars):
        return data
    if isinstance(antenna, dict):
        return antenna
    raise ValueError(str(antenna or "antenna parameters are not available for this solution"))


def evaluate_goals(opt, data: dict, ev, outvars=None, antenna=None) -> tuple[float, list[dict]]:
    """Cost of a solved design and the violation of every goal (antenna: see _goal_input)."""
    from .expressions import eval_expr
    rows, total, wsum = [], 0.0, 0.0
    worst_case = opt.props.get("CostFunction", "Error Area") == "Worst Case"
    for g in opt.goals:
        if not str(g.get("calc", "")).strip():
            continue
        d = _goal_input(g, data, antenna, outvars)
        f = np.asarray(d["freq"], float)
        order = np.argsort(f)
        y = eval_expr(d, g["calc"], outvars)
        y = np.real(y) if np.iscomplexobj(y) else np.asarray(y, float)
        area, worst = goal_violation(g, f[order], y[order], ev)
        w = float(g.get("weight", 1) or 0)
        part = w * worst ** 2 if worst_case else w * area
        total += part
        wsum += w
        rows.append({"calc": g["calc"], "area": area, "worst": worst, "cost": part})
    if not rows:
        raise ValueError("The optimization has no goal")
    if worst_case:
        total /= max(wsum, 1e-30)
    return float(total), rows


# ---------------------------------------------------------------------------------------------
# conjugate gradient
# ---------------------------------------------------------------------------------------------
class ConjugateGradient:
    """Projected nonlinear CG (Polak-Ribiere+) in the unit box; state is a JSON-able dict.

    Phases: 'base' (value at x), 'gradient' (one sample per variable, all independent), 'line' (one
    trial at a time). propose() gives the points to solve next, observe() takes their costs."""

    def __init__(self, x0, props: dict, state: dict | None = None):
        self.p = {"step": float(props.get("DifferenceStep", 0.05)), "alpha0": float(props.get("InitialStep", 0.1)),
                  "tol": float(props.get("GradientTolerance", 1e-4)), "trials": int(props.get("MaxLineTrials", 16))}
        self.s = state or {"phase": "base", "x": np.clip(np.asarray(x0, float), 0, 1).tolist(), "done": False,
                           "iteration": 0}

    @property
    def done(self) -> bool:
        return bool(self.s.get("done"))

    @property
    def reason(self) -> str:
        return self.s.get("reason", "")

    def propose(self) -> list[list[float]]:
        s = self.s
        if s["done"]:
            return []
        x = np.array(s["x"])
        if s["phase"] == "base":
            return [x.tolist()]
        if s["phase"] == "gradient":
            out = []
            for i in range(len(x)):
                if i in s["completed"]:
                    continue
                h = self.p["step"] if x[i] + self.p["step"] <= 1 else -self.p["step"]
                y = x.copy()
                y[i] += h
                out.append(y.tolist())
            return out
        return [np.clip(x + s["alpha"] * np.array(s["direction"]), 0, 1).tolist()]

    def _gradient_start(self) -> None:
        self.s.update(phase="gradient", completed=[], gradient=[0.0] * len(self.s["x"]))

    def observe(self, point, cost: float) -> None:
        s = self.s
        point = np.asarray(point, float)
        if s["phase"] == "base":
            if not np.isfinite(cost):
                raise ValueError("The starting design could not be evaluated")
            s["value"] = float(cost)
            self._gradient_start()
        elif s["phase"] == "gradient":
            x = np.array(s["x"])
            changed = np.flatnonzero(point - x)
            if len(changed) != 1:
                raise ValueError("A gradient sample must change exactly one variable")
            i = int(changed[0])
            if i in s["completed"]:
                return
            # a sample that could not be solved gives no slope information in that direction
            s["gradient"][i] = float((cost - s["value"]) / (point[i] - x[i])) if np.isfinite(cost) else 0.0
            s["completed"].append(i)
            if len(s["completed"]) < len(x):
                return
            self._new_direction()
        else:
            x = np.array(s["x"])
            dx = point - x
            sufficient = s["value"] + 1e-4 * float(np.dot(s["previous_gradient"], dx))
            if np.isfinite(cost) and cost < s["value"] and cost <= sufficient:
                s.update(x=point.tolist(), value=float(cost), iteration=s.get("iteration", 0) + 1)
                self._gradient_start()
            else:
                s["alpha"] *= 0.5
                s["line_trials"] += 1
                if s["line_trials"] >= self.p["trials"]:
                    s.update(done=True, reason="Line search found no improvement")
        if cost == 0:                              # the best evaluation meets every goal
            s.update(done=True, reason="All goals are met (cost 0)")

    def _new_direction(self) -> None:
        s = self.s
        x, g = np.array(s["x"]), np.array(s["gradient"])
        g[((x <= 0) & (g > 0)) | ((x >= 1) & (g < 0))] = 0
        if np.linalg.norm(g, np.inf) <= self.p["tol"]:
            s.update(done=True, reason="Projected gradient below the tolerance")
            return
        old = np.array(s.get("previous_gradient", np.zeros_like(g)))
        beta = max(0.0, float(np.dot(g, g - old) / max(np.dot(old, old), 1e-30))) if np.any(old) else 0.0
        d = -g + beta * np.array(s.get("direction", np.zeros_like(g)))
        d[((x <= 0) & (d < 0)) | ((x >= 1) & (d > 0))] = 0
        if np.dot(d, g) >= 0:
            d = -g
        scale = max(1.0, float(np.linalg.norm(d, np.inf)))
        s.update(direction=d.tolist(), previous_gradient=g.tolist(), phase="line",
                 alpha=self.p["alpha0"] / scale, line_trials=0)
