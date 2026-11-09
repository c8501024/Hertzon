"""Couplers and hybrids of the design wizard."""
from __future__ import annotations

import math

from .. import schema
from ..microstrip import C0
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


class CoupledLineCoupler(Structure):
    """Quarter-wave coupled-line directional coupler (Pozar 7.6): Z0e,o = Z0 sqrt((1 +- c) / (1 -+ c)) with
    c = 10^(-C/20). Backward coupling: port 1 in, 2 through, 3 coupled (beside the input), 4 isolated."""
    key = "coupled"
    title = "Coupled-line coupler"
    category = "coupler"
    description = "Quarter-wave directional coupler, 6-25 dB"
    use = "Power monitoring, sampling ports"
    status = AVAILABLE
    ports = 4
    fields = (
        Field("f0", "Centre frequency", 2.45, "GHz", 0.05, 100),
        Field("c", "Coupling", 15.0, "dB", 6, 30, 2),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("bw", "Bandwidth", 20.0, "%", 1, 60, 1),
        Field("rl", "Return loss", 20.0, "dB", 6, 50, 1),
        Field("iso", "Isolation", 20.0, "dB", 6, 60, 1, help="Microstrip limits the directivity: the even and odd "
              "modes travel at different speeds"),
        Field("tol", "Coupling tolerance", 0.5, "dB", 0.05, 3, 2),
    )

    def default_name(self, spec: dict) -> str:
        return f"Coupler_{spec['c']:g}dB_{spec['f0']:g}GHz"

    @staticmethod
    def impedances(z0: float, c_db: float) -> tuple[float, float]:
        c = 10 ** (-c_db / 20)
        return z0 * math.sqrt((1 + c) / (1 - c)), z0 * math.sqrt((1 - c) / (1 + c))

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        from .filters import coupled_lines
        f0, z0 = spec["f0"], spec["z0"]
        ze, zo = self.impedances(z0, spec["c"])
        w, s, ee = coupled_lines(sub, ze, zo, f0)
        wf = sub.width(z0, f0)
        syn = Synthesis([
            Variable("W", w, f"strip width, Z0e/Z0o {ze:.1f}/{zo:.1f} ohm"),
            Variable("S", s, "gap"),
            Variable("L", C0 / (f0 * 1e9) / math.sqrt(ee) * 1e3 / 4, "length, a quarter wavelength of the mean "
                     "phase constant"),
            Variable("Wf", wf, f"feed width, {z0:g} ohm", include=False),
            Variable("lf", max(sub.lambda_g(wf, f0) / 8, 2 * sub.h), "feed line length", include=False),
        ])
        syn.note = (f"Coupling {spec['c']:g} dB: Z0e = {ze:.1f} ohm, Z0o = {zo:.1f} ohm, a quarter wavelength long. "
                    "The feeds turn away from the coupled section with corners. Port 1 in, 2 through, 3 coupled, "
                    "4 isolated.")
        if s < sub.min_feature:
            syn.warnings.append(f"The gap {s:.3f} mm is below the smallest gap: use weaker coupling or a thinner "
                                "substrate (or a Lange coupler)")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        d = new_circuit(name, sub, syn)
        s = Schematic()
        s.parts.append(substrate_part(sub, -260, -200))
        sp = {"Subst": "Sub1"}
        z = f"{spec['z0']:g}ohm"
        cl = s.add("CL1", "MCLIN", 0, 0, 0, **sp, W="W", S="S", L="L")
        # (pin of the coupled lines, corner rotation and mirror, vertical direction, port number, side)
        ends = ((0, 90, True, -1, 1, "left"), (1, 0, True, -1, 2, "right"), (2, 270, False, 1, 3, "left"),
                (3, 0, False, 1, 4, "right"))
        for pin, rot, mir, sy, num, side in ends:
            px, py = s.pin(cl, pin)
            dx = -30 if side == "left" else 30
            corner = s.add(f"C{num}", "MCORN", px + dx, py, rot, mir, **sp, W="W")
            feed = s.add(f"Feed{num}", "MLIN", px + dx, py + sy * 70, 90, **sp, W="Wf", L="lf")
            s.net(f"e{num}", (px, py), s.pin(corner, "right" if side == "left" else "left"))
            s.net(f"f{num}", s.pin(corner, "top" if sy < 0 else "bottom"), s.pin(feed, "bottom" if sy < 0 else "top"))
            s.port(num, z, s.pin(feed, "top" if sy < 0 else "bottom"), side)
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        a, b = band(spec["f0"], spec["bw"])
        tol = spec["tol"]
        return [goal("dB(S(1,1))", a, b, "<=", -spec["rl"]),
                goal("dB(S(4,1))", a, b, "<=", -spec["iso"]),
                goal("dB(S(3,1))", a, b, ">=", -spec["c"] - tol), goal("dB(S(3,1))", a, b, "<=", -spec["c"] + tol)]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        a, b = band(spec["f0"], max(3 * spec["bw"], 60))
        return a, b, 201


class RatRace(Structure):
    """180 degree hybrid (Pozar 7.8): a ring of Z0 sqrt(2), 1.5 guided wavelengths around. Going round: port 2,
    port 1, port 3 and port 4 a quarter wave apart, three quarters from 4 back to 2. Port 1 splits in phase to
    2 and 3, port 4 is isolated (the difference port). The ring is drawn as a ring in 3D (no curved line part in
    the circuit: its lines are the arc lengths between the tees)."""
    key = "ratrace"
    title = "Rat-race coupler"
    category = "coupler"
    description = "180 degree hybrid, 1.5 wavelength ring"
    use = "Balanced mixers, sum and difference networks"
    status = AVAILABLE
    ports = 4
    fields = (
        Field("f0", "Centre frequency", 2.45, "GHz", 0.05, 60),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("bw", "Bandwidth", 10.0, "%", 1, 40, 1),
        Field("rl", "Return loss and isolation", 20.0, "dB", 6, 50, 1),
        Field("tol", "Amplitude tolerance", 0.5, "dB", 0.05, 3, 2, help="Around -3.01 dB, the losses included"),
    )
    # angles of the ports on the ring (degrees), as on the usual boards: 2 left, 1 and 3 below (60 degrees = a
    # quarter wave apart), 4 right; the three-quarter-wave arc from 4 back to 2 runs over the top
    ANGLES = {2: 180, 1: 240, 3: 300, 4: 0}

    def default_name(self, spec: dict) -> str:
        return f"RatRace_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        f0, z0 = spec["f0"], spec["z0"]
        zr = z0 * math.sqrt(2)
        wr, wf = sub.width(zr, f0), sub.width(z0, f0)
        rm = 3 * sub.lambda_g(wr, f0) / (4 * math.pi)
        syn = Synthesis([
            Variable("Wr", wr, f"ring width, {zr:.1f} ohm"),
            Variable("Rm", rm, "mean ring radius: 1.5 lambda_g around"),
            Variable("Wf", wf, f"feed width, {z0:g} ohm", include=False),
            Variable("lf", max(sub.lambda_g(wf, f0) / 8, 2 * sub.h), "feed line length beyond the ring",
                     include=False),
        ])
        syn.note = (f"Ring of Z0 sqrt(2) = {zr:.1f} ohm and 1.5 guided wavelengths, mean radius {rm:.2f} mm: port 1 "
                    "(lower left) splits in phase to 2 and 3 (a quarter wave on either side), port 4 (right) is "
                    f"isolated. The four {z0:g} ohm feeds leave the ring radially, 60 degrees apart, and end at "
                    "the board edges for the connectors.")
        if wr > rm / 3:
            syn.warnings.append("The ring is wide for its radius: use a thinner substrate")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        from ..circuit import AUTO
        d = new_circuit(name, sub, syn)
        d.settings[AUTO] = "False"
        s = Schematic()
        s.parts.append(substrate_part(sub, -300, -200))
        sp = {"Subst": "Sub1"}
        tw = {"W1": "Wr", "W2": "Wr", "W3": "Wf"}
        # top row 2 - 1 - 3 with the feeds up, 4 at the lower right with its feed down
        t = {2: s.add("T2", "MTEE", 0, 0, 0, True, **sp, **tw), 1: s.add("T1", "MTEE", 200, 0, 0, True, **sp, **tw),
             3: s.add("T3", "MTEE", 400, 0, 0, True, **sp, **tw), 4: s.add("T4", "MTEE", 400, 300, 0, False, **sp, **tw)}
        quarter = "Rm*pi/3 - Wf"                 # arc between tee centres less half a tee at each end
        a = s.add("MS21", "MLIN", 100, 0, 0, **sp, W="Wr", L=quarter)
        b = s.add("MS13", "MLIN", 300, 0, 0, **sp, W="Wr", L=quarter)
        c = s.add("MS34", "MLIN", 500, 150, 90, **sp, W="Wr", L=quarter)
        e = s.add("MS4a", "MLIN", 200, 300, 0, **sp, W="Wr", L="Rm*pi/2 - Wf/2")
        g = s.add("MS4b", "MLIN", -100, 150, 90, **sp, W="Wr", L="Rm*pi/2 - Wf/2")
        s.net("n21", s.pin(t[2], 1), s.pin(a, "left"))
        s.net("n12", s.pin(a, "right"), s.pin(t[1], 0))
        s.net("n13", s.pin(t[1], 1), s.pin(b, "left"))
        s.net("n31", s.pin(b, "right"), s.pin(t[3], 0))
        s.net("n34", s.pin(t[3], 1), s.pin(c, "top"))
        s.net("n43", s.pin(c, "bottom"), s.pin(t[4], 1))
        s.net("n4a", s.pin(t[4], 0), s.pin(e, "right"))
        s.net("nab", s.pin(e, "left"), s.pin(g, "bottom"))
        s.net("n2b", s.pin(g, "top"), s.pin(t[2], 0))
        z0 = f"{spec['z0']:g}ohm"
        for n in (2, 1, 3, 4):
            up = n != 4
            tee = t[n]
            feed = s.add(f"Feed{n}", "MLIN", tee.x, tee.y + (-110 if up else 110), 90, **sp, W="Wf", L="lf")
            s.net(f"f{n}", s.pin(tee, 2), s.pin(feed, "bottom" if up else "top"))
            s.port(n, z0, s.pin(feed, "top" if up else "bottom"), "left" if n == 2 else "right")
        s.build(d)
        return d

    def design3d(self, project, circuit, spec: dict, sub: Substrate, name: str):
        """The ring as an annulus with four short feeds leaving it radially (ports 2, 1, 3, 4 at 180, 240, 300 and
        0 degrees), each ending on a board edge square to it, where a lumped port sits (the connector). The board
        follows the feeds: straight sides at 2 and 4, two chamfers at 1 and 3, a flat bottom between them. The
        variables are the circuit's (same names), so multi-fidelity needs no mapping."""
        from ..design import Command, Design, Setup, Sweep
        from ..materials import Material
        d = Design(name=name, units="mm")
        d.variables = dict(circuit.variables)
        # Rp: distance of the ports (board edges) from the centre; Yt: top edge; Sb: board beyond the chamfer feeds
        d.variables.update(Rp="Rm + Wr/2 + lf", Yt="Rm + Wr/2 + lf", Sb="min(Wf/2 + lf/2, 0.5*Rp)")
        d.settings.update(AutoOpen="Auto Open - Filter", FilterAirMargin="10mm", MaterialOverride="True")
        mat = f"Sub1_Er{sub.er:g}"
        c30, s30 = "0.866025404", "0.577350269"
        outline = ["-Rp,Yt", f"-Rp,-{s30}*Rp", f"-0.5*Rp + {c30}*Sb,-{c30}*Rp - 0.5*Sb",
                   f"0.5*Rp - {c30}*Sb,-{c30}*Rp - 0.5*Sb", f"Rp,-{s30}*Rp", "Rp,Yt"]
        board = d.add_object("CreatePolyline", name="Substrate", material=mat,
                             Points=";".join(f"{q},0" for q in outline), Closed="True", Covered="True")
        board.history.append(Command.new("SweepAlongVector", Vector="0,0,h"))
        gnd = d.add_object("CreatePolyline", name="Ground", Points=";".join(f"{q},0" for q in outline),
                           Closed="True", Covered="True")
        ring = d.add_object("CreateCircle", name="Ring", CenterPosition="0,0,h", Axis="Z", Radius="Rm + Wr/2",
                            NumberOfSegments="0")
        hole = d.add_object("CreateCircle", name="RingHole", CenterPosition="0,0,h", Axis="Z", Radius="Rm - Wr/2",
                            NumberOfSegments="0")
        ring.history.append(Command.new("Subtract", Tools=hole.name))
        hole.consumed_by = ring.name
        ports = []
        for n, ang in self.ANGLES.items():
            at = _turned(ang)
            # from the ring's centre line out to the port, Wf wide
            f = d.add_object("CreatePolyline", name=f"Feed{n}", Closed="True", Covered="True",
                             Points=";".join(f"{at(r, t)},h" for r, t in (("Rm", "-Wf/2"), ("Rp", "-Wf/2"),
                                                                         ("Rp", "Wf/2"), ("Rm", "Wf/2"))))
            ring.history.append(Command.new("Unite", Tools=f.name))
            f.consumed_by = ring.name
            # the port: an upright sheet across the feed's end, from the ground to the trace
            o = d.add_object("CreatePolyline", name=f"Port{n}", Closed="True", Covered="True",
                             Points=f"{at('Rp', '-Wf/2')},0;{at('Rp', 'Wf/2')},0;{at('Rp', 'Wf/2')},h;"
                                    f"{at('Rp', '-Wf/2')},h")
            ports.append((n, o, at("Rp", "0")))
        cu = {"UseMaterial": "False", "Conductivity": f"{sub.sigma:g}", "UseThickness": "True", "Thickness": "t_cu",
              "TwoSided": "True"}
        _assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Traces", [ring.name], **cu)
        _assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Ground", [gnd.name], **cu)
        for n, o, xy in sorted(ports, key=lambda q: q[0]):
            _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", str(n), [o.name], PortNumber=str(n),
                    Impedance=f"{spec['z0']:g}ohm", IntegrationLine=f"{xy},0;{xy},h")
        _assign(d.mesh_ops, schema.MESH_OPS, "Length Based On Selection", "Length_Traces", [ring.name],
                MaxLength="0.5*min(Wr, Wf)")
        st = Setup("Setup1")
        st.props.update(MaximumPasses="10", MaximumDeltaS="0.02")
        st.sweeps.append(Sweep("Sweep"))
        d.setups.append(st)
        if not any(m.name == mat for m in project.materials):
            project.materials.append(Material(mat, permittivity=f"{sub.er:g}", loss_tangent=f"{sub.tand:g}",
                                              color="#2e7d32", description=f"Substrate of {circuit.name}"))
        return d, []

    def goals(self, spec: dict) -> list[dict]:
        a, b = band(spec["f0"], spec["bw"])
        tol, rl, p = spec["tol"], spec["rl"], -3.0103
        return [goal("dB(S(1,1))", a, b, "<=", -rl), goal("dB(S(4,4))", a, b, "<=", -rl),
                goal("dB(S(4,1))", a, b, "<=", -rl),
                goal("dB(S(2,1))", a, b, ">=", p - tol), goal("dB(S(2,1))", a, b, "<=", p + tol),
                goal("dB(S(3,1))", a, b, ">=", p - tol), goal("dB(S(3,1))", a, b, "<=", p + tol)]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        a, b = band(spec["f0"], max(3 * spec["bw"], 40))
        return a, b, 201

    def adapt(self, spec: dict) -> float | None:
        return spec["f0"]


def _turned(deg: float):
    """Point (r along, t across) of a direction ``deg`` degrees from +x, as an "x,y" expression text."""
    c, s = round(math.cos(math.radians(deg)), 9), round(math.sin(math.radians(deg)), 9)

    def term(k: float, v: str) -> str:
        if k == 0 or v == "0":
            return ""
        return f"({v})" if k == 1 else f"-({v})" if k == -1 else f"{k:.9g}*({v})"

    def join(*parts: str) -> str:
        out = ""
        for q in parts:
            if q:
                out += q if not out or q.startswith("-") else "+" + q
        return out or "0"

    return lambda r, t: f"{join(term(c, r), term(-s, t))},{join(term(s, r), term(c, t))}"


def _assign(lst, table, kind, name, objects, **props):
    from ..design import Assignment
    p = schema.defaults(table[kind])
    p.update(props)
    lst.append(Assignment(name, kind, p, objects=list(objects)))
