"""Matching networks of the design wizard."""
from __future__ import annotations

import math

from .base import (
    AVAILABLE,
    Field,
    Structure,
    Substrate,
    Synthesis,
    Variable,
    band,
    goal,
    new_circuit,
    substrate_part,
)
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


def single_stub(z0: float, rl: float, xl: float) -> tuple[float, float]:
    """Shunt open stub matching of the load rl + j xl (Pozar 5.2, first solution): (d, l) in wavelengths, d from
    the load to the stub, l the length of the open stub."""
    if abs(rl - z0) < 1e-9:
        t = -xl / (2 * z0)
    else:
        t = (xl + math.sqrt(rl * ((z0 - rl) ** 2 + xl ** 2) / z0)) / (rl - z0)
    d = math.atan(t) / (2 * math.pi)
    if d < 0:
        d += 0.5
    b = (rl * rl * t - (z0 - xl * t) * (xl + z0 * t)) / (z0 * (rl * rl + (xl + z0 * t) ** 2))
    length = -math.atan(b * z0) / (2 * math.pi)
    if length < 0:
        length += 0.5
    return d, length


class StubMatch(Structure):
    """A load R + jX matched by a shunt open stub at a distance d from it. The load is a series RLC to ground at
    the end of the line (a lumped RLC sheet in 3D)."""
    key = "stub"
    title = "Stub matching"
    category = "match"
    description = "Shunt open stub onto a complex load"
    use = "Match a load R + jX, e.g. an antenna input impedance"
    status = AVAILABLE
    ports = 1
    fields = (
        Field("f0", "Frequency", 2.45, "GHz", 0.05, 100),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("rl", "Load resistance R", 25.0, "ohm", 1, 1000, 2),
        Field("xl", "Load reactance X", 15.0, "ohm", -1000, 1000, 2, help="Positive: inductive, negative: "
              "capacitive (at the frequency)"),
        Field("bw", "Bandwidth", 5.0, "%", 0.5, 50, 1),
        Field("rl_goal", "Return loss in the band", 15.0, "dB", 3, 50, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"StubMatch_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        f0, z0 = spec["f0"], spec["z0"]
        d, l = single_stub(z0, spec["rl"], spec["xl"])
        w = sub.width(z0, f0)
        lg = sub.lambda_g(w, f0)
        syn = Synthesis([
            Variable("d", max(d * lg - w / 2, sub.min_feature), f"load to the stub centre, {d:.3f} lambda_g"),
            Variable("l", max(l * lg - w / 2, sub.min_feature), f"open stub length, {l:.3f} lambda_g"),
            Variable("W", w, f"line and stub width, {z0:g} ohm", include=False),
            Variable("lf", max(lg / 8, 2 * sub.h), "feed line length", include=False),
        ])
        syn.note = (f"Shunt open stub (Pozar 5.2): the load {spec['rl']:g} {'+' if spec['xl'] >= 0 else '-'} "
                    f"j{abs(spec['xl']):g} ohm is matched by a stub of {l:.3f} lambda_g at {d:.3f} lambda_g from it "
                    "(less half the line width at the tee; the open end extension is in the stub model). The load "
                    "is a series RLC to ground at the end of the line.")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        from ..circuit import AUTO
        w0 = 2 * math.pi * spec["f0"] * 1e9
        xl = spec["xl"]
        lv = f"{xl / w0 * 1e9:.6g}nH" if xl > 0 else "0nH"
        cv = f"{-1 / (w0 * xl) * 1e12:.6g}pF" if xl < 0 else "1uF"
        d = new_circuit(name, sub, syn, {"R_load": f"{spec['rl']:g}ohm", "L_load": lv, "C_load": cv})
        d.settings[AUTO] = "False"
        s = Schematic()
        s.parts.append(substrate_part(sub, -200, -160))
        sp = {"Subst": "Sub1"}
        feed = s.add("Feed1", "MLIN", 0, 0, 0, **sp, W="W", L="lf")
        t = s.add("T1", "MTEE", 110, 0, 0, True, **sp, W1="W", W2="W", W3="W")
        stub = s.add("Stub", "MLOC", 110, -70, 180, **sp, W="W", L="l")
        line = s.add("MS1", "MLIN", 220, 0, 0, **sp, W="W", L="d")
        load = s.add("Load", "SRLC", 300, 60, 90, R="R_load", L="L_load", C="C_load")
        gnd = s.add("GND1", "GND", 300, 120)
        s.net("a", s.pin(feed, "right"), s.pin(t, 0))
        s.net("b", s.pin(t, 1), s.pin(line, "left"))
        s.net("c", s.pin(t, 2), s.pin(stub, 0))
        s.net("e", s.pin(line, "right"), s.pin(load, "top"))
        s.net("g", s.pin(load, "bottom"), s.pin(gnd, 0))
        s.port(1, f"{spec['z0']:g}ohm", s.pin(feed, "left"), "left")
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        a, b = band(spec["f0"], spec["bw"])
        return [goal("dB(S(1,1))", a, b, "<=", -spec["rl_goal"])]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        a, b = band(spec["f0"], max(4 * spec["bw"], 40))
        return a, b, 201
