"""Optimization variables, goals / cost function, the conjugate gradient optimizer and the Latin hypercube +
Nelder-Mead optimizer.

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

    @property
    def phase(self) -> str:
        return self.s["phase"]

    @property
    def iteration(self) -> int:
        return self.s.get("iteration", 0)

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


# ---------------------------------------------------------------------------------------------
# Latin hypercube sample + Nelder-Mead chains
# ---------------------------------------------------------------------------------------------
def latin_hypercube(n: int, dim: int, rng) -> np.ndarray:
    """n points in the unit box, one in every 1/n slice of every variable."""
    return (np.argsort(rng.random((dim, n)), axis=1).T + rng.random((n, dim))) / n


def _key(x) -> tuple:
    return tuple(np.round(np.asarray(x, float), 9))


class _Chain:
    """One Nelder-Mead chain as a generator: it yields the points to solve and receives their costs."""

    def __init__(self, x0, f0: float, step: float, tol: float, budget: int):
        self.budget, self.used = budget, 0
        self.gen = self._run(np.asarray(x0, float), float(f0), step, tol)
        self.points: list[np.ndarray] = []
        self.costs: list[float | None] = []
        self.done = False
        self._ask(None)

    def _ask(self, costs) -> None:
        try:
            pts = self.gen.send(costs) if costs is not None else next(self.gen)
        except StopIteration:
            self.done, self.points, self.costs = True, [], []
            return
        self.points = [np.clip(np.asarray(p, float), 0, 1) for p in pts]
        self.costs = [None] * len(self.points)

    @property
    def ready(self) -> bool:
        return not self.done and all(c is not None for c in self.costs)

    def advance(self) -> None:
        self.used += len(self.costs)
        if self.used >= self.budget:
            self.done, self.points, self.costs = True, [], []
            return
        self._ask(list(self.costs))

    @staticmethod
    def _run(x0, f0, step, tol):
        n = len(x0)
        X = [x0] + [np.clip(x0 + step * np.eye(n)[j] * (1 if x0[j] < 0.9 else -1), 0, 1) for j in range(n)]
        F = [f0] + list((yield X[1:]))
        while True:
            order = np.argsort(F, kind="stable")
            X, F = [X[i] for i in order], [F[i] for i in order]
            if max(np.max(np.abs(x - X[0])) for x in X[1:]) <= tol:
                return
            c = np.mean(X[:-1], axis=0)
            xw, fw = X[-1], F[-1]
            xr = np.clip(2 * c - xw, 0, 1)
            fr = (yield [xr])[0]
            if fr < F[0]:                                       # expansion
                xe = np.clip(3 * c - 2 * xw, 0, 1)
                fe = (yield [xe])[0]
                X[-1], F[-1] = (xe, fe) if fe < fr else (xr, fr)
            elif fr < F[-2]:                                    # reflection
                X[-1], F[-1] = xr, fr
            else:                                               # contraction, else shrink
                outside = fr < fw
                xc = np.clip(c + 0.5 * ((xr if outside else xw) - c), 0, 1)
                fc = (yield [xc])[0]
                if fc <= fr if outside else fc < fw:
                    X[-1], F[-1] = xc, fc
                else:
                    xs = [X[0] + 0.5 * (x - X[0]) for x in X[1:]]
                    fs = yield xs
                    X, F = [X[0]] + xs, [F[0]] + list(fs)


class SampledNelderMead:
    """Global search for designs whose cost has several minima (e.g. an antenna with several resonances):

    1. a Latin hypercube sample of the variable box (and the start design), all solved in parallel;
    2. Nelder-Mead chains from the best distinct sample points, run side by side (each round solves the
       next point of every chain in parallel), each with its own evaluation budget.

    Variables scaled to 0..1 between Min and Max; points outside are clipped. propose() gives the points
    to solve next, observe() takes their costs (as ConjugateGradient). A design that cannot be solved has
    an infinite cost and is avoided by the chains."""

    def __init__(self, x0, props: dict):
        self.p = {"samples": max(0, int(float(props.get("Samples", 24)))),
                  "chains": max(1, int(float(props.get("Chains", 4)))),
                  "budget": max(2, int(float(props.get("ChainEvaluations", 40)))),
                  "step": min(0.5, max(1e-3, float(props.get("SimplexStep", 0.08)))),
                  "tol": max(0.0, float(props.get("SimplexTolerance", 0.005))),
                  "seed": int(float(props.get("Seed", 1)))}
        x0 = np.clip(np.asarray(x0, float), 0, 1)
        rng = np.random.default_rng(self.p["seed"])
        pts = [x0] + list(latin_hypercube(self.p["samples"], len(x0), rng)) if self.p["samples"] else [x0]
        self.sample = {"points": pts, "costs": [None] * len(pts)}
        self.chains: list[_Chain] = []
        self.phase = "sampling"
        self.iteration = 0
        self._done, self._reason = False, ""

    @property
    def done(self) -> bool:
        return self._done

    @property
    def reason(self) -> str:
        return self._reason

    def _stop(self, reason: str) -> None:
        self._done, self._reason = True, reason

    def _pending(self) -> list[list[float]]:
        if self.phase == "sampling":
            return [p.tolist() for p, c in zip(self.sample["points"], self.sample["costs"]) if c is None]
        return [p.tolist() for ch in self.chains if not ch.done for p, c in zip(ch.points, ch.costs) if c is None]

    def propose(self) -> list[list[float]]:
        if self._done:
            return []
        pend = self._pending()
        if pend:
            return pend
        if self.phase == "sampling":
            self._start_chains()
        else:
            for ch in self.chains:
                if ch.ready:
                    ch.advance()
            self.iteration += 1
        if not self._done and not any(not ch.done for ch in self.chains):
            self._stop("All Nelder-Mead chains converged or used their evaluations")
        return [] if self._done else self._pending()

    def _start_chains(self) -> None:
        pts, costs = self.sample["points"], np.array(self.sample["costs"], float)
        order = [i for i in np.argsort(costs, kind="stable") if np.isfinite(costs[i])]
        if not order:
            self._stop("No design of the sample could be evaluated")
            return
        seen, starts = set(), []
        for i in order:
            k = tuple(np.round(pts[i], 2))
            if k not in seen:
                seen.add(k)
                starts.append(i)
            if len(starts) >= self.p["chains"]:
                break
        self.phase, self.iteration = "simplex", 1
        self.chains = [_Chain(pts[i], costs[i], self.p["step"], self.p["tol"], self.p["budget"]) for i in starts]

    def observe(self, point, cost: float) -> None:
        k = _key(point)
        cost = float(cost) if np.isfinite(cost) else float("inf")
        if self.phase == "sampling":
            for i, p in enumerate(self.sample["points"]):
                if self.sample["costs"][i] is None and _key(p) == k:
                    self.sample["costs"][i] = cost
        else:
            for ch in self.chains:
                for i, p in enumerate(ch.points):
                    if ch.costs[i] is None and _key(p) == k:
                        ch.costs[i] = cost
        if cost == 0:                              # the best evaluation meets every goal
            self._stop("All goals are met (cost 0)")
