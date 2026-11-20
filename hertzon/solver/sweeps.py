"""Frequencies of a sweep (Distribution, Start, Stop, Step, Count, ...), without the solver imports."""
from __future__ import annotations

import math

import numpy as np

from ..model.expr import Evaluator


DISTRIBUTIONS = ("Linear Step", "Linear Count", "Log Scale", "Single Points")


def split_points(text: str) -> list[str]:
    """Single frequencies of a sweep, separated by ';' or ',' (as the frequency list of a setup)."""
    return [t.strip() for t in str(text or "").replace(";", ",").split(",") if t.strip()]


def sweep_frequencies(props: dict[str, str], ev: Evaluator) -> np.ndarray:
    dist = props.get("Distribution") or "Linear Step"
    if dist not in DISTRIBUTIONS:
        raise ValueError(f"Unknown sweep distribution '{dist}'. Use one of: {', '.join(DISTRIBUTIONS)}")
    if dist == "Single Points":
        pts = split_points(props.get("Points", ""))
        if not pts:
            raise ValueError("Sweep with Single Points has no frequencies (Points, separated by ';' or ',')")
        return np.array(sorted(ev.freq(x) for x in pts))
    f1, f2 = ev.freq(props["Start"]), ev.freq(props["Stop"])
    if dist == "Linear Count":
        return np.linspace(f1, f2, max(2, ev.integer(props["Count"])))
    if dist == "Log Scale":
        n = max(2, int(round(math.log10(f2 / f1) * ev.integer(props["SamplesPerDecade"]))) + 1)
        return np.logspace(math.log10(f1), math.log10(f2), n)
    step = ev.freq(props["Step"])
    if step <= 0:
        raise ValueError("Sweep step must be positive")
    n = int(math.floor((f2 - f1) / step + 1e-9)) + 1
    return f1 + step * np.arange(n)
