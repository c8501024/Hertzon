"""Couplers and hybrids of the design wizard."""
from __future__ import annotations

import math

from .base import AVAILABLE, Field, Structure, Substrate, Synthesis, Variable, band, goal, new_circuit, substrate_part
from .circuits import Schematic


class Branchline(Structure):
    """90 degree hybrid: a square of four quarter-wave lines. Port 1 in, 2 through (along the series arm),
    3 coupled (diagonal), 4 isolated."""
    key = "branchline"
    title = "Branchline coupler"
    category = "coupler"
    description = "90 degree hybrid, 3 dB or unequal split"
    use = "Balanced amplifiers, IQ modulators, Butler matrices"
    status = AVAILABLE
    ports = 4
    fields = (
        Field("f0", "Centre frequency", 2.45, "GHz", 0.05, 100),
        Field("c", "Coupling", 3.0, "dB", 1.5, 10, 2, help="Power to the coupled port 3 below the input"),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("bw", "Bandwidth", 10.0, "%", 1, 40, 1),
        Field("rl", "Return loss and isolation", 20.0, "dB", 6, 50, 1),
        Field("tol", "Amplitude tolerance", 0.5, "dB", 0.05, 3, 2),
        Field("ptol", "Phase tolerance", 5.0, "deg", 0.5, 30, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"Branchline_{spec['f0']:g}GHz"

    @staticmethod
    def impedances(z0: float, c_db: float) -> tuple[float, float]:
        """(series arm, shunt arm) impedances for the coupling c_db: k = 10^(-C/20) the coupled voltage,
        Z_series = Z0 sqrt(1 - k^2), Z_shunt = Z_series / k (Pozar 7.5, generalized)."""
        k = 10 ** (-c_db / 20)
        zs = z0 * math.sqrt(1 - k * k)
        return zs, zs / k

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        f0 = spec["f0"]
        zs, zp = self.impedances(spec["z0"], spec["c"])
        ws, wp, wf = sub.width(zs, f0), sub.width(zp, f0), sub.width(spec["z0"], f0)
        # the lines meet at tees: the electrical length runs from tee centre to tee centre, so each line is
        # shorter by the width of the crossing arm (half of it at each end)
        ls = sub.lambda_g(ws, f0) / 4 - wp
        lp = sub.lambda_g(wp, f0) / 4 - ws
        syn = Synthesis([
            Variable("Ws", ws, f"series arm: width, {zs:.1f} ohm"),
            Variable("Ls", ls, "series arm: length, lambda_g/4 between tee centres"),
            Variable("Wp", wp, f"shunt arm: width, {zp:.1f} ohm"),
            Variable("Lp", lp, "shunt arm: length, lambda_g/4 between tee centres"),
            Variable("Wf", wf, f"feed width, {spec['z0']:g} ohm", include=False),
            Variable("lf", max(sub.lambda_g(wf, f0) / 8, 2 * sub.h), "feed line length", include=False),
        ])
        syn.note = (f"k = 10^(-C/20) = {10 ** (-spec['c'] / 20):.4f}: series arms Z0 sqrt(1-k^2) = {zs:.1f} ohm, "
                    f"shunt arms Z_series/k = {zp:.1f} ohm, a quarter guided wavelength each between the tee "
                    "centres. Port 1 in, 2 through, 3 coupled, 4 isolated.")
        if ls <= 0 or lp <= 0:
            syn.warnings.append("The lines are too wide for their length: use a thinner substrate")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        from ..circuit import AUTO
        d = new_circuit(name, sub, syn)
        d.settings[AUTO] = "False"             # the four tees are explicit parts
        s = Schematic()
        s.parts.append(substrate_part(sub, -260, -120))
        sp = {"Subst": "Sub1"}
        # corners: 1 top left, 2 top right, 3 bottom right, 4 bottom left; the branch (pin 3) of a tee points to
        # the shunt arm: down for the top tees, up for the mirrored bottom ones
        x0, x1, y0, y1 = 0, 300, 0, 300
        tees = {1: s.add("T1", "MTEE", x0, y0, 0, False, **sp, W1="Wf", W2="Ws", W3="Wp"),
                2: s.add("T2", "MTEE", x1, y0, 0, False, **sp, W1="Ws", W2="Wf", W3="Wp"),
                3: s.add("T3", "MTEE", x1, y1, 0, True, **sp, W1="Ws", W2="Wf", W3="Wp"),
                4: s.add("T4", "MTEE", x0, y1, 0, True, **sp, W1="Wf", W2="Ws", W3="Wp")}
        top = s.add("MS_top", "MLIN", (x0 + x1) // 2, y0, 0, **sp, W="Ws", L="Ls")
        bot = s.add("MS_bot", "MLIN", (x0 + x1) // 2, y1, 0, **sp, W="Ws", L="Ls")
        left = s.add("MS_left", "MLIN", x0, (y0 + y1) // 2, 90, **sp, W="Wp", L="Lp")
        right = s.add("MS_right", "MLIN", x1, (y0 + y1) // 2, 90, **sp, W="Wp", L="Lp")
        s.net("a", s.pin(tees[1], 1), s.pin(top, "left"))
        s.net("b", s.pin(top, "right"), s.pin(tees[2], 0))
        s.net("c", s.pin(tees[4], 1), s.pin(bot, "left"))
        s.net("d", s.pin(bot, "right"), s.pin(tees[3], 0))
        s.net("e", s.pin(tees[1], 2), s.pin(left, "top"))
        s.net("f", s.pin(left, "bottom"), s.pin(tees[4], 2))
        s.net("g", s.pin(tees[2], 2), s.pin(right, "top"))
        s.net("h", s.pin(right, "bottom"), s.pin(tees[3], 2))
        z0 = f"{spec['z0']:g}ohm"
        for num, tee, pin, dx, side in ((1, 1, 0, -110, "left"), (2, 2, 1, 110, "right"),
                                        (3, 3, 1, 110, "right"), (4, 4, 0, -110, "left")):
            t = tees[tee]
            feed = s.add(f"Feed{num}", "MLIN", t.x + dx, t.y, 0, **sp, W="Wf", L="lf")
            s.net(f"t{num}", s.pin(t, pin), s.pin(feed, "right" if side == "left" else "left"))
            s.port(num, z0, s.pin(feed, side), side)
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        a, b = band(spec["f0"], spec["bw"])
        k2 = 10 ** (-spec["c"] / 10)
        thru = 10 * math.log10(1 - k2)
        tol, rl = spec["tol"], spec["rl"]
        return [goal("dB(S(1,1))", a, b, "<=", -rl),
                goal("dB(S(4,1))", a, b, "<=", -rl),
                goal("dB(S(2,1))", a, b, ">=", thru - tol), goal("dB(S(2,1))", a, b, "<=", thru + tol),
                goal("dB(S(3,1))", a, b, ">=", -spec["c"] - tol), goal("dB(S(3,1))", a, b, "<=", -spec["c"] + tol),
                goal("ang_deg(S(3,1)/S(2,1))", a, b, ">=", -90 - spec["ptol"]),
                goal("ang_deg(S(3,1)/S(2,1))", a, b, "<=", -90 + spec["ptol"])]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        a, b = band(spec["f0"], max(3 * spec["bw"], 40))
        return a, b, 201
