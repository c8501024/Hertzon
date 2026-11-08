"""Matching networks of the design wizard."""
from __future__ import annotations

import math

from .base import AVAILABLE, Field, Structure, Substrate, Synthesis, Variable, band, goal, new_circuit, substrate_part
from .circuits import Schematic


def binomial_impedances(z0: float, zl: float, n: int) -> list[float]:
    """Section impedances of an n-section binomial (maximally flat) quarter-wave transformer from z0 to zl
    (Pozar 5.6): ln(Z[k+1]/Z[k]) = 2^-n C(n, k) ln(zl/z0)."""
    out, lz = [], math.log(z0)
    for k in range(n):
        lz += 2.0 ** -n * math.comb(n, k) * math.log(zl / z0)
        out.append(math.exp(lz))
    return out


class QuarterWaveTransformer(Structure):
    key = "qwt"
    title = "Quarter-wave transformer"
    category = "match"
    description = "1 to 4 sections, binomial (maximally flat)"
    use = "Match two real impedances, e.g. 50 ohm to a 12.5 ohm load"
    status = AVAILABLE
    ports = 2
    fields = (
        Field("f0", "Centre frequency", 2.45, "GHz", 0.05, 100),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("zl", "Load impedance", 12.5, "ohm", 2, 500, 2, help="Real load; port 2 of the design has this "
              "impedance"),
        Field("n", "Sections", 2, "", 1, 4, 0, True),
        Field("bw", "Bandwidth", 30.0, "%", 1, 150, 1, help="Range of the return loss goal around the centre"),
        Field("rl", "Return loss in the band", 20.0, "dB", 3, 60, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"QWT_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        f0, n = spec["f0"], int(spec["n"])
        zs = binomial_impedances(spec["z0"], spec["zl"], n)
        syn = Synthesis()
        for i, z in enumerate(zs, 1):
            w = sub.width(z, f0)
            syn.variables.append(Variable(f"W{i}", w, f"section {i}: width, {z:.1f} ohm"))
            syn.variables.append(Variable(f"L{i}", sub.lambda_g(w, f0) / 4, f"section {i}: length, lambda_g/4"))
        wf, wl = sub.width(spec["z0"], f0), sub.width(spec["zl"], f0)
        syn.variables.append(Variable("Wf", wf, f"feed width, {spec['z0']:g} ohm", include=False))
        syn.variables.append(Variable("Wl", wl, f"load line width, {spec['zl']:g} ohm", include=False))
        syn.variables.append(Variable("lf", max(sub.lambda_g(wf, f0) / 8, 2 * sub.h), "feed line length",
                                      include=False))
        syn.note = ("Binomial transformer: ln(Z[k+1]/Z[k]) = 2^-N C(N,k) ln(ZL/Z0), sections "
                    + ", ".join(f"{z:.1f}" for z in zs) + " ohm, each a quarter guided wavelength at f0. The "
                    "width steps are modelled in the circuit and are part of the 3D layout.")
        for v in syn.variables:
            if v.name.startswith("W") and v.value > 0.5 * sub.lambda_g(v.value, f0):
                syn.warnings.append(f"{v.name} = {v.value:.2f} mm is wider than half a guided wavelength: the "
                                    "line models lose accuracy (use a thinner substrate or fewer steps)")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        n = int(spec["n"])
        d = new_circuit(name, sub, syn)
        s = Schematic()
        s.parts.append(substrate_part(sub))
        x = 0
        lines = [("Feed1", "Wf", "lf")] + [(f"MS{i}", f"W{i}", f"L{i}") for i in range(1, n + 1)] + \
            [("Feed2", "Wl", "lf")]
        prev = None
        for nm, w, length in lines:
            c = s.add(nm, "MLIN", x + 40, 0, 0, Subst="Sub1", W=w, L=length)
            if prev is not None:
                s.net(f"N{nm}", s.pin(prev, "right"), s.pin(c, "left"))
            prev = c
            x += 120
        s.port(1, f"{spec['z0']:g}ohm", s.pin(s.parts[1], "left"), "left")
        s.port(2, f"{spec['zl']:g}ohm", s.pin(prev, "right"), "right")
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        a, b = band(spec["f0"], spec["bw"])
        return [goal("dB(S(1,1))", a, b, "<=", -spec["rl"])]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        a, b = band(spec["f0"], max(2 * spec["bw"], 60))
        return max(a, 0.05 * spec["f0"]), b, 201
