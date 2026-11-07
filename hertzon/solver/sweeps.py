"""Frequencies of a sweep (Distribution, Start, Stop, Step, Count, ...), without the solver imports."""
from __future__ import annotations

import math

import numpy as np

from ..model.expr import Evaluator


def sweep_frequencies(props: dict[str, str], ev: Evaluator) -> np.ndarray:
    dist = props.get("Distribution", "Linear Step")
    if dist == "Single Points":
        return np.array(sorted(ev.freq(x) for x in props.get("Points", "").split(",") if x.strip()))
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
