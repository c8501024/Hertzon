"""Optimization with the derivative tuning model as a surrogate (trust region).

Every round solves the design once, computes its tuning model (hertzon.solver.tuning) on the adaptive mesh of
that solve, and minimizes the cost of the goals on the model instead of with solves: a model evaluation costs
milliseconds, so the minimization over the box is cheap. The step is limited to a trust region, a fraction
of each variable's tuning range around the solved values (the model is accurate near them). The design at
the model's optimum is then solved; the ratio of the actual to the predicted improvement decides whether the
step is taken and whether the region grows or shrinks:

    rho = (cost(x) - cost(x + dp)) / (model_cost(0) - model_cost(dp))

    rho >= ACCEPT          take the step; a new tuning model around the new design
    rho >= GROW and the step reached the edge of the region: the region grows
    rho <  SHRINK          the region shrinks (a rejected step keeps the current model)

At dp = 0 the model reproduces the solved response exactly, so model_cost(0) is the cost of the solved
design on the model's frequencies. The functions here are independent of the solver processes
(hertzon.solver.optimize runs them).
"""
from __future__ import annotations

from typing import Callable

import numpy as np

ACCEPT = 0.1            # actual / predicted improvement at least this: the step is taken
GROW = 0.75             # ... at least this and the step at the edge of the region: the region doubles
SHRINK = 0.25           # below this the region halves
MIN_RADIUS = 0.02       # region (fraction of the tuning ranges) below which the optimizer stops
MAX_RADIUS = 1.0        # the whole tuning range (where no tetrahedron shrinks below 30 % of its volume)


def step_bounds(ranges: np.ndarray, radius: float, lo: np.ndarray, hi: np.ndarray,
                values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(lower, upper) offsets allowed in this round: `radius` times the tuning range of every variable,
    within the optimization's Min and Max."""
    ranges = np.asarray(ranges, float)
    blo = np.maximum(radius * ranges[:, 0], np.asarray(lo, float) - values)
    bhi = np.minimum(radius * ranges[:, 1], np.asarray(hi, float) - values)
    return np.minimum(blo, 0.0), np.maximum(bhi, 0.0)


def minimize_box(cost: Callable[[np.ndarray], float], blo: np.ndarray, bhi: np.ndarray,
                 starts: int = 6, seed: int = 0) -> tuple[np.ndarray, float, int]:
    """(offsets, cost, evaluations) of the smallest cost in the box blo..bhi: Powell's method (no gradient,
    the cost of goal violations has kinks) from the solved values and from a few points spread over the
    box. Among offsets of equal cost the one closest to the solved values is kept (no needless step). Where
    every goal is met (cost 0) the search stops inside that region, not exactly at its border, which leaves
    some margin for the error of the model."""
    from scipy.optimize import minimize
    n = len(blo)
    span = np.where(bhi > blo, bhi - blo, 1.0)
    count = [0]

    def f(u):
        count[0] += 1
        dp = blo + np.clip(u, 0.0, 1.0) * span
        v = float(cost(dp))
        return v if np.isfinite(v) else 1e30

    u0 = np.clip(-blo / span, 0.0, 1.0)
    rng = np.random.default_rng(seed)
    points = [u0] + [rng.uniform(0.0, 1.0, n) for _ in range(max(0, starts - 1))]
    best_u, best = u0, f(u0)
    for p in points:
        r = minimize(f, p, method="Powell", bounds=[(0.0, 1.0)] * n,
                     options={"xtol": 1e-4, "ftol": 1e-10, "maxfev": 400 * n})
        u = np.clip(r.x, 0.0, 1.0)
        v = f(u)
        closer = np.linalg.norm((u - u0) * span) < np.linalg.norm((best_u - u0) * span)
        if v < best - 1e-12 * max(abs(best), 1.0) or (abs(v - best) <= 1e-12 * max(abs(best), 1.0) and closer):
            best_u, best = u, v
    return blo + best_u * span, best, count[0]


def at_edge(dp: np.ndarray, blo: np.ndarray, bhi: np.ndarray, tol: float = 0.02) -> bool:
    """The step reached the edge of the trust region in some variable."""
    span = np.maximum(bhi - blo, 1e-30)
    return bool(np.any((np.abs(dp - blo) < tol * span) & (blo < 0)) or
                np.any((np.abs(bhi - dp) < tol * span) & (bhi > 0)))


def update(radius: float, actual: float, predicted: float, edge: bool) -> tuple[bool, float, float]:
    """(step taken, new radius, rho) from the actual and the predicted improvement of a step."""
    rho = actual / predicted if predicted > 0 else (-np.inf if actual < 0 else 0.0)
    taken = bool(rho >= ACCEPT and actual > 0)
    if rho < SHRINK:
        radius *= 0.5
    elif rho >= GROW and edge:
        radius = min(MAX_RADIUS, 2.0 * radius)
    return taken, radius, float(rho)
