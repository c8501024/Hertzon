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
    has_phase_reference: bool = False   # depends on the phase φ of the slider (field phasors, Mag_E)
    is_magnitude: bool = False          # real and non-negative: only mag (and dB)


# Near Field Line quantities, named like the field plots (Plot Fields): Mag_E is the length of the real
# field vector at the phase φ, |Re(E e^(jφ))| (instantaneous, follows the slider); ComplexMag_E is
# sqrt(|Ex|^2+|Ey|^2+|Ez|^2) (independent of the phase); E_X, E_Y, E_Z are the complex components.
NF_QUANTITIES = ["Mag_E", "ComplexMag_E", "E_X", "E_Y", "E_Z", "Mag_H", "ComplexMag_H", "H_X", "H_Y", "H_Z"]
# old names (up to 0.3.29), still accepted: the same values under the new name
NF_ALIASES = {f"Near{w}{a}": f"{w}_{a}" for w in "EH" for a in "XYZ"}
NF_ALIASES.update({"NearETotal": "ComplexMag_E", "NearHTotal": "ComplexMag_H"})

_FIELD_COMPONENT = re.compile(r"^[EH]_[XYZ]$")          # E_X, H_Z: complex field components
_FIELD_MAGNITUDE = re.compile(r"^ComplexMag_[EH]$")     # sqrt(|X|^2+|Y|^2+|Z|^2)
_FIELD_INSTANT = re.compile(r"^Mag_[EH]$")              # |Re(E e^(jφ))|: a magnitude that follows the phase
_MAGNITUDES = {"VSWR"}


def _base(name: str) -> str:
    """'S(1,1)' -> 'S', 'NearEX' -> 'E_X'."""
    b = re.split(r"[(\s]", name.strip(), maxsplit=1)[0]
    return NF_ALIASES.get(b, b)


def nf_name(name: str) -> str:
    """The current name of a Near Field Line quantity: 'NearETotal' -> 'ComplexMag_E', 'E_X' -> 'E_X'."""
    return NF_ALIASES.get(name, name)


def quantity_meta(name: str) -> QuantityMeta:
    """Metadata of a report quantity. Field components are phasors with a phase reference; S, Z, Y, Zo,
    Gamma ... are complex network quantities without one (re/im at φ = 0); ComplexMag_E, ComplexMag_H and
    VSWR are magnitudes; Mag_E and Mag_H are magnitudes that follow the phase."""
    b = _base(name)
    if _FIELD_COMPONENT.match(b):
        return QuantityMeta(has_phase_reference=True)
    if _FIELD_INSTANT.match(b):
        return QuantityMeta(has_phase_reference=True, is_magnitude=True)
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
    """A Near Field Line trace of an old report in the current names and functions. The old quantity names
    (NearETotal, NearEX ...) become the names of the field plots (ComplexMag_E, E_X ...) without a warning,
    the values are the same. The removed functions: envelope -> mag, inst -> re of a component; inst of a
    vector was its length at the phase, now Mag_E; envelope of a vector becomes ComplexMag_E (the same for
    linear polarisation). Returns the trace and a warning (None when the meaning did not change)."""
    m = re.fullmatch(r"\s*(\w+)\((\w+)\)\s*", trace)
    if not m:
        return trace, None
    func, old = m.groups()
    name = nf_name(old)
    new = LEGACY_NF_FUNCS.get(func, func)
    if func == "inst" and _FIELD_MAGNITUDE.match(name):
        name = "Mag_" + name[-1]                     # inst(NearETotal) = |Re(E e^(jφ))|
    if quantity_meta(name).is_magnitude and new not in MAGNITUDE_FUNCS:
        new = "mag"
    out = f"{new}({name})"
    if out == trace.strip():
        return trace, None
    if new == func:
        return out, None                             # only renamed
    return out, f"{trace} is no longer available and was changed to {out}"


def migrate_near_report(report) -> list[str]:
    """Change the old names (NearETotal ...) and the removed functions (envelope, inst, re/im/ang of a
    magnitude) of a Near Field Line report to the current ones, in place; the warnings. Other reports are
    left alone."""
    if getattr(report, "kind", "") != "Near Field Line":
        return []
    warnings, traces, changed = [], [], False
    for t in report.traces:
        new, w = migrate_near_trace(t)
        changed |= new != t
        if w:
            warnings.append(w)
        if new not in traces:
            traces.append(new)
    if changed:
        report.traces[:] = traces
    skin = report.props.get("skin", "")
    if skin:
        tr, sep, rng = skin.partition("|")
        new = migrate_near_trace(tr)[0] + sep + rng
        if new != skin:
            report.props["skin"] = new
    for k in [k for k in report.props if k.startswith("trace:")]:
        # per-trace styles are stored by the trace name
        new = "trace:" + migrate_near_trace(k[6:])[0]
        if new != k and new not in report.props:
            report.props[new] = report.props.pop(k)
    return warnings
