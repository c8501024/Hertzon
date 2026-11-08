"""Filters of the design wizard (the hairpin filter has its own designer, model/filters.py)."""
from __future__ import annotations

import math

from ..filters import chebyshev_g
from .base import AVAILABLE, Field, Structure, Substrate, Synthesis, Variable, goal, new_circuit, substrate_part
from .circuits import Schematic


def ripple_return_loss(ripple_db: float) -> float:
    """Return loss (dB) of a Chebyshev response with the passband ripple ripple_db."""
    return -10 * math.log10(1 - 10 ** (-ripple_db / 10))


class SteppedImpedanceLowpass(Structure):
    """Chebyshev lowpass of alternating low-impedance (shunt C) and high-impedance (series L) sections
    (Pozar 8.6): beta l = g Z_low / Z0 for a capacitor, g Z0 / Z_high for an inductor, at the cutoff."""
    key = "stepped"
    title = "Stepped-impedance lowpass"
    category = "filter"
    description = "Chebyshev, high and low impedance sections"
    use = "Harmonic suppression after an amplifier or oscillator"
    status = AVAILABLE
    ports = 2
    fields = (
        Field("fc", "Cutoff frequency", 2.5, "GHz", 0.05, 60),
        Field("n", "Order (sections)", 7, "", 3, 11, 0, True),
        Field("ripple", "Passband ripple", 0.1, "dB", 0.01, 1, 3),
        Field("zhi", "High impedance", 110.0, "ohm", 70, 160, 1, help="Series inductors: as high as the "
              "smallest line width allows"),
        Field("zlo", "Low impedance", 20.0, "ohm", 8, 40, 1, help="Shunt capacitors: as low as the line "
              "length allows (wide lines)"),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("fs", "Stopband from", 1.6, "x fc", 1.1, 3, 2),
        Field("rej", "Rejection in the stopband", 25.0, "dB", 5, 80, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"Lowpass_{spec['fc']:g}GHz_N{int(spec['n'])}"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        fc, n, z0 = spec["fc"], int(spec["n"]), spec["z0"]
        g = chebyshev_g(n, spec["ripple"])
        whi, wlo = sub.width(spec["zhi"], fc), sub.width(spec["zlo"], fc)
        syn = Synthesis([Variable("Whi", whi, f"high impedance width, {spec['zhi']:g} ohm", include=False),
                         Variable("Wlo", wlo, f"low impedance width, {spec['zlo']:g} ohm", include=False)])
        for k in range(1, n + 1):
            cap = k % 2 == 1                     # starts and ends with a shunt capacitor
            bl = g[k] * spec["zlo"] / z0 if cap else g[k] * z0 / spec["zhi"]
            length = bl / (2 * math.pi) * sub.lambda_g(wlo if cap else whi, fc)
            syn.variables.append(Variable(f"L{k}", length, f"section {k}: {'shunt C (low' if cap else 'series L (high'} "
                                          f"impedance), g{k} = {g[k]:.4f}"))
        wf = sub.width(z0, fc)
        syn.variables.append(Variable("Wf", wf, f"feed width, {z0:g} ohm", include=False))
        syn.variables.append(Variable("lf", max(sub.lambda_g(wf, fc) / 8, 2 * sub.h), "feed line length",
                                      include=False))
        syn.note = (f"Chebyshev {spec['ripple']:g} dB, N = {n}: g = " + ", ".join(f"{x:.4f}" for x in g[1:n + 1]) +
                    ". beta l = g Z_low/Z0 (shunt C) or g Z0/Z_high (series L) at the cutoff. The widths stay; the "
                    "optimization moves the section lengths.")
        if whi < sub.min_feature:
            syn.warnings.append(f"The {spec['zhi']:g} ohm line ({whi:.3f} mm) is narrower than the smallest width")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        n = int(spec["n"])
        d = new_circuit(name, sub, syn)
        s = Schematic()
        s.parts.append(substrate_part(sub))
        lines = [("Feed1", "Wf", "lf")] + [(f"MS{k}", "Wlo" if k % 2 else "Whi", f"L{k}") for k in range(1, n + 1)] \
            + [("Feed2", "Wf", "lf")]
        prev, x = None, 0
        for nm, w, length in lines:
            c = s.add(nm, "MLIN", x + 40, 0, 0, Subst="Sub1", W=w, L=length)
            if prev is not None:
                s.net(f"N{nm}", s.pin(prev, "right"), s.pin(c, "left"))
            prev, x = c, x + 120
        s.port(1, f"{spec['z0']:g}ohm", s.pin(s.parts[1], "left"), "left")
        s.port(2, f"{spec['z0']:g}ohm", s.pin(prev, "right"), "right")
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        fc, fs = spec["fc"], spec["fs"] * spec["fc"]
        rl = ripple_return_loss(spec["ripple"])
        return [goal("dB(S(1,1))", 0.05 * fc, 0.95 * fc, "<=", -min(rl - 1, 25)),
                goal("dB(S(2,1))", fs, 1.25 * fs, "<=", -spec["rej"])]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.05 * spec["fc"], 1.35 * spec["fs"] * spec["fc"], 241
