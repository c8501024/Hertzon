"""Power dividers of the design wizard."""
from __future__ import annotations

import math

from .base import AVAILABLE, Field, Structure, Substrate, Synthesis, Variable, band, goal, new_circuit, substrate_part
from .circuits import Schematic


class TeeDivider(Structure):
    """Lossless T-junction divider: the input splits at a tee into two quarter-wave transformers that bring the
    two halves of the input admittance back to the port impedance. No isolation between the outputs."""
    key = "tee"
    title = "T-junction divider"
    category = "divider"
    description = "With quarter-wave transformers, equal or unequal"
    use = "Simple splits, array feeds"
    status = AVAILABLE
    ports = 3
    fields = (
        Field("f0", "Centre frequency", 2.45, "GHz", 0.05, 100),
        Field("k", "Power ratio P3/P2", 1.0, "", 0.1, 10, 3),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("bw", "Bandwidth", 20.0, "%", 1, 80, 1),
        Field("rl", "Return loss at port 1", 20.0, "dB", 6, 50, 1),
        Field("tol", "Split tolerance", 0.3, "dB", 0.05, 3, 2),
    )

    def default_name(self, spec: dict) -> str:
        return f"TeeDivider_{spec['f0']:g}GHz"

    @staticmethod
    def impedances(z0: float, k: float) -> tuple[float, float]:
        """Transformer impedances to ports 2 and 3: the input sees Z0 (1 + K) and Z0 (1 + K) / K in parallel
        (power ratio K = P3/P2), each transformed to Z0 by sqrt(Z_in Z0)."""
        return math.sqrt(z0 * z0 * (1 + k)), math.sqrt(z0 * z0 * (1 + k) / k)

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        f0, z0, k = spec["f0"], spec["z0"], spec["k"]
        z2, z3 = self.impedances(z0, k)
        wf = sub.width(z0, f0)
        syn = Synthesis()
        for n, z in (("2", z2), ("3", z3)):
            w = sub.width(z, f0)
            syn.variables.append(Variable(f"Wt{n}", w, f"transformer to port {n}: width, {z:.1f} ohm"))
            syn.variables.append(Variable(f"Lt{n}", sub.lambda_g(w, f0) / 4 - wf / 2,
                                          f"transformer to port {n}: length, lambda_g/4 from the tee centre"))
        syn.variables.append(Variable("Wf", wf, f"feed width, {z0:g} ohm", include=False))
        syn.variables.append(Variable("lf", max(sub.lambda_g(wf, f0) / 8, 2 * sub.h), "feed line length",
                                      include=False))
        syn.note = (f"Power ratio K = P3/P2 = {k:g}: the input splits into {z0 * (1 + k):.1f} ohm and "
                    f"{z0 * (1 + k) / k:.1f} ohm, transformed to {z0:g} ohm by {z2:.1f} and {z3:.1f} ohm quarter-wave "
                    "lines. Port 1 in (bottom), 2 left, 3 right.")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        from ..circuit import AUTO
        d = new_circuit(name, sub, syn)
        d.settings[AUTO] = "False"
        s = Schematic()
        s.parts.append(substrate_part(sub, -200, -120))
        sp = {"Subst": "Sub1"}
        # the tee with its branch (pin 3) pointing down to the input, the transformers to the left and right
        t = s.add("T1", "MTEE", 0, 0, 0, False, **sp, W1="Wt2", W2="Wt3", W3="Wf")
        feed1 = s.add("Feed1", "MLIN", 0, 110, 90, **sp, W="Wf", L="lf")
        s.net("in", s.pin(t, 2), s.pin(feed1, "top"))
        s.port(1, f"{spec['z0']:g}ohm", s.pin(feed1, "bottom"), "right")
        for n, side, dx in (("2", "left", -1), ("3", "right", 1)):
            tr = s.add(f"MS{n}", "MLIN", dx * 110, 0, 0, **sp, W=f"Wt{n}", L=f"Lt{n}")
            fd = s.add(f"Feed{n}", "MLIN", dx * 230, 0, 0, **sp, W="Wf", L="lf")
            inner, outer = ("right", "left") if side == "left" else ("left", "right")
            s.net(f"t{n}", s.pin(t, 0 if side == "left" else 1), s.pin(tr, inner))
            s.net(f"f{n}", s.pin(tr, outer), s.pin(fd, inner))
            s.port(int(n), f"{spec['z0']:g}ohm", s.pin(fd, outer), side)
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        a, b = band(spec["f0"], spec["bw"])
        k, tol = spec["k"], spec["tol"]
        p2, p3 = 10 * math.log10(1 / (1 + k)), 10 * math.log10(k / (1 + k))
        return [goal("dB(S(1,1))", a, b, "<=", -spec["rl"]),
                goal("dB(S(2,1))", a, b, ">=", p2 - tol), goal("dB(S(2,1))", a, b, "<=", p2 + tol),
                goal("dB(S(3,1))", a, b, ">=", p3 - tol), goal("dB(S(3,1))", a, b, "<=", p3 + tol)]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        a, b = band(spec["f0"], max(2 * spec["bw"], 60))
        return a, b, 201
