"""Physics diagnostics for the MCP results: they help a calling AI read results correctly.

- efficiency_breakdown: total efficiency = mismatch efficiency x radiation efficiency, and which one limits.
- port_impedance / impedance_rows: input impedance, Q (Yaghjian-Best) and resonances (X = 0) of a port.
- convergence_note: a warning when the adaptive mesh did not reach its Delta S target.
- band_hints: factual hints for points with strong reflection (resonance nearby or not, Q).

The hints state facts and the checks that tell causes apart; they never decide the cause.
"""
from __future__ import annotations

import math

import numpy as np

# thresholds of the hints
HIGH_Q = 20.0               # Q_Z above this: narrow band, a match holds only over ~1/Q fractional bandwidth
STRONG_REFLECTION = 0.5     # mismatch efficiency below this (|S11| above -3 dB) counts as strong reflection
LIMIT_MARGIN_DB = 1.0       # the other factor must be this much better to call one factor "limiting"


def _r(x, n=4):
    x = float(x)
    return x if not math.isfinite(x) else float(f"{x:.{n}g}")


def port_z0(data: dict, i: int) -> np.ndarray:
    """Reference impedance of port i per frequency (real part), from a stored S-parameter solution."""
    nf = len(np.atleast_1d(data["freq"]))
    z0 = np.asarray(data.get("z0", 50.0))
    if z0.ndim == 0 or z0.size == 0:
        v = float(np.real(z0)) if z0.size else 50.0
        return np.full(nf, v)
    if z0.ndim == 1:
        v = z0[i] if len(z0) > i else z0[0]
        return np.full(nf, float(np.real(v)))
    col = z0[:, i] if z0.shape[1] > i else z0[:, 0]
    return np.real(col).astype(float)


def q_yaghjian(f, Z) -> np.ndarray:
    """Q_Z(w) = w / (2R) * sqrt(R'^2 + (X' + |X|/w)^2) (Yaghjian & Best 2005), derivatives by finite
    differences on the frequency grid. Valid for a single isolated resonance; NaN where R <= 0 or < 3 points."""
    f = np.asarray(f, float)
    Z = np.asarray(Z, complex)
    if len(f) < 3:
        return np.full(len(f), np.nan)
    w = 2 * np.pi * f
    R, X = Z.real, Z.imag
    dR = np.gradient(R, w)
    dX = np.gradient(X, w)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = w / (2 * R) * np.sqrt(dR ** 2 + (dX + np.abs(X) / w) ** 2)
    q[R <= 0] = np.nan
    return q


def resonances(f, Z) -> list[dict]:
    """Zero crossings of X: 'series' where X rises through 0 (impedance minimum type), 'parallel' where it falls
    (antiresonance, usually high R). Linear interpolation between grid points."""
    f = np.asarray(f, float)
    Z = np.asarray(Z, complex)
    q = q_yaghjian(f, Z)
    X = Z.imag
    out = []
    for k in range(len(f) - 1):
        if X[k] == 0 or X[k] * X[k + 1] < 0:
            t = 0.0 if X[k] == 0 else X[k] / (X[k] - X[k + 1])
            fr = f[k] + t * (f[k + 1] - f[k])
            R = Z.real[k] + t * (Z.real[k + 1] - Z.real[k])
            qq = q[k] + t * (q[k + 1] - q[k])
            out.append({"freq_GHz": _r(fr / 1e9, 6), "type": "series" if X[k + 1] > X[k] else "parallel",
                        "R_ohm": _r(R), "Q": _r(qq, 3)})
    return out


def port_impedance(data: dict, i: int = 0):
    """(f, Z, Q, z0) of port i of a stored S-parameter solution."""
    f = np.asarray(data["freq"], float)
    S = np.asarray(data["S"])[:, i, i]
    z0 = port_z0(data, i)
    with np.errstate(divide="ignore", invalid="ignore"):
        Z = z0 * (1 + S) / (1 - S)
    return f, Z, q_yaghjian(f, Z), z0


def efficiency_breakdown(Pinc: float, Pacc: float, Prad: float, Ploss: float | None = None) -> dict:
    """total = Prad/Pinc = (Pacc/Pinc) * (Prad/Pacc): mismatch (incl. port coupling) times radiation efficiency."""
    Pinc = max(float(Pinc), 1e-300)
    mis = float(Pacc) / Pinc
    rad = float(Prad) / max(float(Pacc), 1e-300)
    tot = float(Prad) / Pinc
    db = lambda v: 10 * math.log10(max(v, 1e-30))  # noqa: E731
    out = {"total_efficiency": _r(tot), "mismatch_efficiency": _r(mis), "radiation_efficiency": _r(rad),
           "total_dB": _r(db(tot)), "mismatch_dB": _r(db(mis)), "radiation_dB": _r(db(rad)),
           "definition": "total = Prad/Pinc = mismatch efficiency (Pacc/Pinc) x radiation efficiency (Prad/Pacc)"}
    dm, dr = db(mis), db(rad)
    if dm < dr - LIMIT_MARGIN_DB:
        out["limited_by"] = "reflection"
        out["note"] = ("Reflection is a symptom, not a cause: it is a mismatch only if the structure has a "
                       "radiating resonance nearby that a matching network can reach. Check Q_Z and the "
                       "resonances (get_results kind impedance) before calling it a mismatch.")
    elif dr < dm - LIMIT_MARGIN_DB:
        out["limited_by"] = "radiation efficiency"
    else:
        out["limited_by"] = "both"
    if Ploss is not None:
        out["radiation_efficiency_from_loss"] = _r(float(Prad) / max(float(Prad) + float(Ploss), 1e-300))
    if rad > 1.05:
        out["warning"] = ("Prad > Pacc: the power balance is off (unconverged mesh, far-field sphere too coarse or "
                          "too close); do not draw conclusions from the split until it is fixed.")
    return out


def convergence_note(conv: dict, target: float | None = None) -> dict:
    """Converged flag, last Delta S, target and a warning when not converged."""
    passes = conv.get("passes") or []
    last = next((p.get("delta_s") for p in reversed(passes) if p.get("delta_s") is not None), None)
    out = {"converged": bool(conv.get("converged")), "passes": len(passes)}
    if last is not None:
        out["last_delta_s"] = _r(last)
    if target is not None:
        out["target_delta_s"] = _r(target)
    if conv.get("final_tets"):
        out["final_tets"] = conv["final_tets"]
    if not out["converged"]:
        out["warning"] = (
            "The adaptive mesh did NOT converge" + (f" (last Delta S {last:.3g}" if last is not None else " (")
            + (f", target {target:g})" if target is not None else ")")
            + ". S-parameters, accepted power and everything derived from them (mismatch efficiency, total "
            "efficiency, Q) are uncertain, most of all near high-Q points and deep dips. Refine (more passes, "
            "mesh operations, adaptive frequencies at the critical points) or confirm with a finer solve before "
            "drawing conclusions.")
    return out


def band_hints(f, S11, Z, Q, res: list[dict]) -> list[str]:
    """Factual hints about strongly reflecting ranges of one port."""
    f = np.asarray(f, float)
    mis = 1 - np.abs(np.asarray(S11)) ** 2
    hints = []
    bad = mis < STRONG_REFLECTION
    if not bad.any() or len(f) < 3:
        return hints
    # contiguous ranges of strong reflection
    k = 0
    while k < len(f):
        if not bad[k]:
            k += 1
            continue
        j = k
        while j + 1 < len(f) and bad[j + 1]:
            j += 1
        seg = slice(k, j + 1)
        kk = k + int(np.argmin(mis[seg]))
        f0, f1 = f[k] / 1e9, f[j] / 1e9
        inside = [r for r in res if f0 <= r["freq_GHz"] <= f1]
        q = Q[kk]
        txt = (f"{f0:.4g}-{f1:.4g} GHz: strong reflection (mismatch efficiency down to {mis[kk]:.2f} at "
               f"{f[kk] / 1e9:.4g} GHz, Z = {Z[kk].real:.3g}{Z[kk].imag:+.3g}j ohm, Q_Z ~ "
               f"{'n/a' if not np.isfinite(q) else f'{q:.3g}'}). ")
        if not inside:
            txt += "No resonance (X = 0) of this port in the range."
            if np.isfinite(q) and q > HIGH_Q:
                txt += (f" Q_Z > {HIGH_Q:g} without a resonance means stored energy dominates: a matching network "
                        f"can only give a bandwidth of about 1/Q ({100 / q:.2g} %) and may trade radiation for "
                        "circuit loss. Check whether the structure has a radiating mode here (current distribution, "
                        "radiation efficiency) before calling it a mismatch.")
        else:
            txt += f"Resonance(s) in the range: {inside}; a matching network may help if R is far from Z0."
        hints.append(txt)
        k = j + 1
    return hints


def impedance_rows(f, S11, Z, Q, z0, idx) -> list[dict]:
    rows = []
    for k in idx:
        rows.append({"freq_GHz": _r(f[k] / 1e9, 6), "S11_dB": _r(20 * np.log10(max(abs(S11[k]), 1e-30))),
                     "R_ohm": _r(Z[k].real), "X_ohm": _r(Z[k].imag), "Q_Z": _r(Q[k], 3),
                     "mismatch_efficiency": _r(1 - abs(S11[k]) ** 2), "z0_ohm": _r(z0[k])})
    return rows


DIAGNOSIS_GUIDE = (
    "Reading: total efficiency = mismatch efficiency x radiation efficiency. Call a dip 'mismatch' only if the "
    "radiation efficiency stays high there AND a matching network (or a tuned element) actually recovers it. "
    "Strong reflection with Q_Z >> 10 and no resonance nearby usually means the structure has no radiating mode "
    "there (current circulating on parts that do not radiate); fixes are geometric (new or retuned radiating "
    "branch), not matching. Separate loss from radiation by solving once lossless (PEC, tan d = 0) and comparing.")
