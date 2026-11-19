"""Metadata of report quantities and the functions of complex field quantities.

Whether a quantity has a phase reference (a phasor of a field, whose real part at a phase φ is the
instantaneous value) or is a magnitude (no phase at all) decides which functions a report offers and whether
it shows the phase slider. The GUI asks quantity_meta(), it keeps no lists of names of its own."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class QuantityMeta:
    has_phase_reference: bool = False   # a field phasor: re/im follow the phase φ of the slider
    is_magnitude: bool = False          # real and non-negative (no phase): only mag (and dB)


_FIELD_COMPONENT = re.compile(r"^Near[EH][XYZ]$")       # NearEX, NearHZ: complex field components
_FIELD_MAGNITUDE = re.compile(r"^Near[EH]Total$")       # sqrt(|X|^2+|Y|^2+|Z|^2)
_MAGNITUDES = {"VSWR"}


def _base(name: str) -> str:
    """'S(1,1)' -> 'S', 'NearEX' -> 'NearEX'."""
    return re.split(r"[(\s]", name.strip(), maxsplit=1)[0]


def quantity_meta(name: str) -> QuantityMeta:
    """Metadata of a report quantity. Field components are phasors with a phase reference; S, Z, Y, Zo,
    Gamma ... are complex network quantities without one (re/im at φ = 0); E Total, H Total and VSWR are
    magnitudes."""
    b = _base(name)
    if _FIELD_COMPONENT.match(b):
        return QuantityMeta(has_phase_reference=True)
    if _FIELD_MAGNITUDE.match(b) or b in _MAGNITUDES:
        return QuantityMeta(is_magnitude=True)
    return QuantityMeta()


# functions of the Near Field Line report; ang_deg is the angle ("ang") in degrees
NF_FUNCS = ["mag", "dB", "re", "im", "ang_deg"]
MAGNITUDE_FUNCS = ["mag", "dB"]
# removed functions of old reports: envelope was |X| (= mag) for a component, inst was Re{X e^(jφ)} (= re)
LEGACY_NF_FUNCS = {"envelope": "mag", "inst": "re"}


def functions_for(name: str) -> list[str]:
    """The functions offered for a Near Field Line quantity."""
    return list(MAGNITUDE_FUNCS if quantity_meta(name).is_magnitude else NF_FUNCS)


def phasor_value(func: str, v, phase_deg: float = 0.0, meta: QuantityMeta | None = None) -> np.ndarray:
    """func of the complex values v. re and im of a quantity with a phase reference are taken of
    v e^(jφ) (re is then the instantaneous value at φ); without one φ is 0. mag and ang do not depend on φ."""
    v = np.asarray(v)
    meta = meta or QuantityMeta()
    if func in ("re", "im"):
        if meta.has_phase_reference and phase_deg:
            v = v * np.exp(1j * np.radians(phase_deg))
        return np.real(v) if func == "re" else np.imag(v)
    if func == "mag":
        return np.abs(v)
    if func == "dB":
        return 20 * np.log10(np.maximum(np.abs(v), 1e-30))
    if func == "ang_deg":
        return np.degrees(np.angle(v))
    if func == "ang_rad":
        return np.angle(v)
    raise ValueError(f"Unknown function '{func}'")


def migrate_near_trace(trace: str) -> tuple[str, str | None]:
    """A Near Field Line trace of an old report in the current functions: envelope -> mag, inst -> re
    (mag for a magnitude such as NearETotal, which has no re/im/ang either). Returns the trace and a warning
    (None when nothing changed)."""
    m = re.fullmatch(r"\s*(\w+)\((\w+)\)\s*", trace)
    if not m:
        return trace, None
    func, name = m.groups()
    new = LEGACY_NF_FUNCS.get(func, func)
    if quantity_meta(name).is_magnitude and new not in MAGNITUDE_FUNCS:
        new = "mag"
    if new == func:
        return trace, None
    out = f"{new}({name})"
    return out, f"{trace} is no longer available and was changed to {out}"


def migrate_near_report(report) -> list[str]:
    """Change the removed functions of a Near Field Line report (envelope, inst, re/im/ang of a magnitude)
    to the current ones, in place; the warnings. Other reports are left alone."""
    if getattr(report, "kind", "") != "Near Field Line":
        return []
    warnings, traces = [], []
    for t in report.traces:
        new, w = migrate_near_trace(t)
        if w:
            warnings.append(w)
        if new not in traces:
            traces.append(new)
    if warnings:
        report.traces[:] = traces
        skin = report.props.get("skin", "")
        if skin:
            tr, sep, rng = skin.partition("|")
            report.props["skin"] = migrate_near_trace(tr)[0] + sep + rng
    return warnings
