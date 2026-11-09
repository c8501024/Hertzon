"""Antennas of the design wizard: a 3D design with a lumped port and an optimization on the adaptive solution at
the design frequency (no circuit: nothing in a schematic radiates). The rectangular patch has its own designer
(model/antennas.py)."""
from __future__ import annotations

import math

from .. import schema
from ..design import Assignment, Design, InfiniteSphere, Setup, Sweep
from ..microstrip import C0
from .base import AVAILABLE, Field, Structure, Substrate, Synthesis, Variable, goal


def resonant_fraction(length_over_diameter: float) -> float:
    """Resonant length of a thin centre-fed dipole in wavelengths: 0.5 less a shortening that grows with the wire
    thickness (about 0.47 for L/d = 50, 0.475 for 100, 0.483 for 1000)."""
    return 0.5 - 0.12 / math.log(max(length_over_diameter, 5.0))


def _assign(lst, table, kind, name, objects, **props):
    p = schema.defaults(table[kind])
    p.update(props)
    lst.append(Assignment(name, kind, p, objects=list(objects)))


def _setup(d: Design, f0: float, span: float = 20.0) -> None:
    """Adaptive at f0 (two converged passes), a sweep of +-span % that is off: the optimization uses the adaptive
    solution only; switch the sweep on to look at the resonance after applying the optimum."""
    st = Setup("Setup1")
    st.props.update(Frequency=f"{f0:g}GHz", MaximumPasses="12", MaximumDeltaS="0.02", MinimumConvergedPasses="2")
    sw = Sweep("Sweep")
    sw.props.update(SweepType="Interpolating", Distribution="Linear Count", Start=f"{f0 * (1 - span / 100):.6g}GHz",
                    Stop=f"{f0 * (1 + span / 100):.6g}GHz", Count="201", Enabled="False")
    st.sweeps.append(sw)
    d.setups.append(st)
    d.spheres.append(InfiniteSphere("Infinite Sphere1"))


class _Wire(Structure):
    kind = "3d"
    view = "side"                       # the wizard preview: side view (x, z)
    coarse = ""
    optimizer = "Derivative Trust Region"
    status = AVAILABLE
    ports = 1

    def goals(self, spec: dict) -> list[dict]:
        return [goal("dB(S(1,1))", spec["f0"], spec["f0"], "<=", -spec["rl"])]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.8 * spec["f0"], 1.2 * spec["f0"], 201

    @staticmethod
    def _new(name: str) -> Design:
        d = Design(name=name, units="mm")
        d.settings.update(AutoOpen="Auto Open", AutoOpenBoundary="Auto", MaterialOverride="True")
        return d


class Dipole(_Wire):
    """Centre-fed half-wave wire dipole in free space: two PEC cylinders and a lumped port in the gap."""
    key = "dipole"
    title = "Dipole"
    category = "antenna"
    description = "Half-wave wire, centre fed"
    use = "Reference antenna, measurements"
    fields = (
        Field("f0", "Frequency", 1.0, "GHz", 0.05, 30),
        Field("r", "Wire radius", 1.0, "mm", 0.01, 50, 3),
        Field("z0", "Port impedance", 73.0, "ohm", 10, 300, 1, help="About 70 ohm at resonance; with 50 ohm the "
              "best return loss is about 15 dB (add a matching network)"),
        Field("rl", "Return loss at the frequency", 15.0, "dB", 3, 40, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"Dipole_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        lam = C0 / (spec["f0"] * 1e9) * 1e3
        r = spec["r"]
        k = resonant_fraction(0.48 * lam / (2 * r))
        g = max(2 * r, lam / 200)
        syn = Synthesis([
            Variable("Larm", (k * lam - g) / 2, f"arm length, total {k:.3f} wavelengths with the gap"),
            Variable("r", r, "wire radius", include=False),
            Variable("g", g, "feed gap", include=False),
        ])
        syn.note = (f"Resonant length {k:.3f} lambda (thin wire shortening for L/d = {0.48 * lam / (2 * r):.0f}), "
                    f"lambda = {lam:.1f} mm; the input resistance at resonance is about 70 ohm. The optimization "
                    "tunes the arm length on the adaptive solution at the frequency.")
        if r > lam / 20:
            syn.warnings.append("The wire is thick (radius > lambda/20): the start length is only a rough guess")
        return syn

    def model3d(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        d = self._new(name)
        d.variables.update({v.name: v.text() for v in syn.variables})
        d.add_object("CreateCylinder", name="ArmTop", material="pec", CenterPosition="0,0,g/2", Axis="Z",
                     Radius="r", Height="Larm", NumberOfSegments="0")
        d.add_object("CreateCylinder", name="ArmBottom", material="pec", CenterPosition="0,0,-g/2", Axis="Z",
                     Radius="r", Height="-Larm", NumberOfSegments="0")
        gap = d.add_object("CreateRectangle", name="Gap", Position="-r,0,-g/2", Axis="Y", XSize="2*r", ZSize="g")
        _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "1", [gap.name], PortNumber="1",
                Impedance=f"{spec['z0']:g}ohm", IntegrationLine="0,0,-g/2;0,0,g/2")
        _setup(d, spec["f0"])
        return d


class Monopole(_Wire):
    """Quarter-wave wire monopole over a square ground plane, fed by a lumped port between the plane and the
    wire."""
    key = "monopole"
    title = "Monopole"
    category = "antenna"
    description = "Quarter wave over a finite ground plane"
    use = "Vehicles, IoT"
    fields = (
        Field("f0", "Frequency", 1.0, "GHz", 0.05, 30),
        Field("r", "Wire radius", 1.0, "mm", 0.01, 50, 3),
        Field("gnd", "Ground plane side", 0.5, "lambda", 0.2, 3, 2, help="Below half a wavelength the plane "
              "shifts the resonance and tilts the beam"),
        Field("z0", "Port impedance", 37.0, "ohm", 10, 300, 1, help="About 36 ohm over a large plane"),
        Field("rl", "Return loss at the frequency", 15.0, "dB", 3, 40, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"Monopole_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        lam = C0 / (spec["f0"] * 1e9) * 1e3
        r = spec["r"]
        k = resonant_fraction(0.48 * lam / (2 * r))            # the image doubles it: a dipole of 2 L
        g = max(r, lam / 400)
        syn = Synthesis([
            Variable("L", k * lam / 2 - g, f"wire length above the feed gap, {k / 2:.3f} wavelengths with it"),
            Variable("r", r, "wire radius", include=False),
            Variable("g", g, "feed gap above the ground plane", include=False),
            Variable("G", spec["gnd"] * lam, f"ground plane side, {spec['gnd']:g} lambda", include=False),
        ])
        syn.note = (f"Half of a resonant dipole: {k / 2:.3f} lambda, lambda = {lam:.1f} mm; about 36 ohm over a large "
                    "plane. The optimization tunes the length on the adaptive solution at the frequency.")
        if spec["gnd"] < 0.5:
            syn.warnings.append("The ground plane is smaller than half a wavelength: expect a shift of the resonance")
        return syn

    def model3d(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        d = self._new(name)
        d.variables.update({v.name: v.text() for v in syn.variables})
        gnd = d.add_object("CreateRectangle", name="Ground", material="pec", Position="-G/2,-G/2,0", Axis="Z",
                           XSize="G", YSize="G")
        d.add_object("CreateCylinder", name="Wire", material="pec", CenterPosition="0,0,g", Axis="Z", Radius="r",
                     Height="L", NumberOfSegments="0")
        gap = d.add_object("CreateRectangle", name="Gap", Position="-r,0,0", Axis="Y", XSize="2*r", ZSize="g")
        _assign(d.boundaries, schema.BOUNDARIES, "Perfect E", "PEC_Ground", [gnd.name])
        _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "1", [gap.name], PortNumber="1",
                Impedance=f"{spec['z0']:g}ohm", IntegrationLine="0,0,0;0,0,g")
        _setup(d, spec["f0"])
        return d


def ifa_eeff(er: float) -> float:
    """Effective permittivity of a printed IFA at the board edge: the field runs mostly in air (about 1.5 on FR4
    1.6 mm, from published 2.4 GHz designs)."""
    return 1 + 0.15 * (er - 1)


class PrintedIFA(Structure):
    """Inverted-F antenna printed at the edge of a board: a short arm to the ground, the radiating arm parallel to
    the ground edge and the feed arm at a distance s from the short, fed by a lumped port across a gap at the ground
    edge. Copper on the top layer (ground and antenna)."""
    key = "ifa"
    title = "Inverted-F (IFA)"
    category = "antenna"
    description = "Printed on the PCB edge, shorted to the ground"
    use = "Bluetooth, Wi-Fi in devices"
    status = AVAILABLE
    kind = "3d"
    coarse = ""
    optimizer = "Derivative Trust Region"
    ports = 1
    fields = (
        Field("f0", "Frequency", 2.45, "GHz", 0.3, 10),
        Field("bx", "Board width", 30.0, "mm", 10, 300, 1),
        Field("gy", "Ground length", 40.0, "mm", 10, 300, 1, help="The ground is part of the antenna: it shifts "
              "the resonance"),
        Field("H", "Arm height above the ground", 4.0, "mm", 1, 20, 2),
        Field("w", "Trace width", 0.8, "mm", 0.1, 5, 2),
        Field("z0", "Port impedance", 50.0, "ohm", 10, 300, 1),
        Field("rl", "Return loss at the frequency", 15.0, "dB", 3, 40, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"IFA_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        lam = C0 / (spec["f0"] * 1e9) * 1e3
        ee = ifa_eeff(sub.er)
        total = lam / 4 / math.sqrt(ee)
        H, w = spec["H"], spec["w"]
        length = total - H
        s = 0.5 * H + w
        syn = Synthesis([
            Variable("L", length, f"radiating arm, L + H = lambda/4 / sqrt({ee:.2f}) = {total:.1f} mm"),
            Variable("s", s, "feed arm to short arm (sets the input resistance)"),
            Variable("H", H, "arm height above the ground edge", include=False),
            Variable("w", w, "trace width", include=False),
            Variable("g", max(0.5, sub.min_feature), "port gap at the ground edge", include=False),
            Variable("Bx", spec["bx"], "board width", include=False),
            Variable("Gy", spec["gy"], "ground length", include=False),
            Variable("h", sub.h, "substrate height", include=False),
        ])
        syn.note = (f"L + H is a quarter wavelength at the board edge (effective permittivity {ee:.2f} on er "
                    f"{sub.er:g}); the distance s from the short to the feed sets the input resistance. The ground "
                    "and the antenna are on the top layer; the optimization tunes L and s on the adaptive solution.")
        if length + 2 > spec["bx"]:
            syn.warnings.append("The arm is longer than the board is wide: use a wider board or a meander")
        return syn

    def materials3d(self, spec: dict, sub: Substrate) -> list:
        from ..materials import Material
        return [Material(f"IFA_Er{sub.er:g}", permittivity=f"{sub.er:g}", loss_tangent=f"{sub.tand:g}",
                         color="#2e7d32", description="Substrate of a printed IFA")]

    def model3d(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        from ..design import Command
        d = Design(name=name, units="mm")
        d.settings.update(AutoOpen="Auto Open", AutoOpenBoundary="Auto")
        d.variables.update({v.name: v.text() for v in syn.variables if v.name not in ("h",)})
        d.variables.update(h=f"{sub.h:g}mm", t_cu=f"{sub.t:g}um", xs="Bx - L - 1mm", Cy="H + 2mm")
        d.add_object("CreateBox", name="Substrate", material=f"IFA_Er{sub.er:g}", Position="0,0,0", XSize="Bx",
                     YSize="Gy + Cy", ZSize="h")
        gnd = d.add_object("CreateRectangle", name="Ground", Position="0,0,h", Axis="Z", XSize="Bx", YSize="Gy")
        ifa = d.add_object("CreateRectangle", name="IFA", Position="xs,Gy,h", Axis="Z", XSize="w", YSize="H")
        for nm, pos, xs_, ys_ in (("Arm", "xs,Gy + H - w,h", "L", "w"), ("FeedArm", "xs + s,Gy + g,h", "w",
                                                                         "H - w - g")):
            o = d.add_object("CreateRectangle", name=nm, Position=pos, Axis="Z", XSize=xs_, YSize=ys_)
            ifa.history.append(Command.new("Unite", Tools=o.name))
            o.consumed_by = ifa.name
        port = d.add_object("CreateRectangle", name="PortSheet", Position="xs + s,Gy,h", Axis="Z", XSize="w",
                            YSize="g")
        cu = {"UseMaterial": "False", "Conductivity": f"{sub.sigma:g}", "UseThickness": "True", "Thickness": "t_cu",
              "TwoSided": "True"}
        _assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper", [gnd.name, ifa.name], **cu)
        _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "1", [port.name], PortNumber="1",
                Impedance=f"{spec['z0']:g}ohm", IntegrationLine="xs + s + w/2,Gy,h;xs + s + w/2,Gy + g,h")
        _setup(d, spec["f0"])
        return d

    def goals(self, spec: dict) -> list[dict]:
        return [goal("dB(S(1,1))", spec["f0"], spec["f0"], "<=", -spec["rl"])]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.8 * spec["f0"], 1.2 * spec["f0"], 201


# ---- patches fed by a probe ---------------------------------------------------------------------------------------
# The probe is a lumped port: a vertical sheet through the substrate from the ground to the patch, as wide as a
# probe (the current runs up the sheet). The substrate is the wizard's board.

PROBE = 1.0                             # mm, width of the probe sheet (about a 50 ohm SMA pin)


def _patch_losses(f: float, h: float, sub: Substrate) -> tuple[float, float]:
    """(Q of the dielectric, Q of the copper) of a patch cavity: 1/tan d and h sqrt(pi f mu0 sigma)."""
    mu0 = 4e-7 * math.pi
    return 1 / max(sub.tand, 1e-6), h * math.sqrt(math.pi * f * mu0 * sub.sigma)


def _patch_board(spec: dict, sub: Substrate) -> float:
    """Ground margin beyond the patch (mm): max(6 h, lambda0 / 10), as the rectangular patch designer."""
    return max(6 * sub.h, C0 / (spec["f0"] * 1e9) * 1e3 / 10)


class _Patch(Structure):
    kind = "3d"
    coarse = ""
    optimizer = "Derivative Trust Region"
    status = AVAILABLE
    ports = 1

    def materials3d(self, spec: dict, sub: Substrate) -> list:
        from ..materials import Material
        return [Material(f"Patch_Er{sub.er:g}", permittivity=f"{sub.er:g}", loss_tangent=f"{sub.tand:g}",
                         color="#2e7d32", description="Substrate of a patch antenna")]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.9 * spec["f0"], 1.1 * spec["f0"], 201

    def _start(self, name: str, syn: Synthesis, sub: Substrate, half: str) -> tuple[Design, object]:
        """Design with the substrate (a square of side 2 half), the ground below and the variables."""
        d = Design(name=name, units="mm")
        d.settings.update(AutoOpen="Auto Open", AutoOpenBoundary="Auto")
        d.variables.update({v.name: v.text() for v in syn.variables})
        d.variables.update(h=f"{sub.h:g}mm", t_cu=f"{sub.t:g}um", Bs=half)
        d.add_object("CreateBox", name="Substrate", material=f"Patch_Er{sub.er:g}", Position="-Bs,-Bs,0",
                     XSize="2*Bs", YSize="2*Bs", ZSize="h")
        gnd = d.add_object("CreateRectangle", name="Ground", Position="-Bs,-Bs,0", Axis="Z", XSize="2*Bs",
                           YSize="2*Bs")
        return d, gnd

    def _finish(self, d: Design, spec: dict, sub: Substrate, gnd, patch, xf: str) -> Design:
        """Copper on the ground and the patch, the probe at (xf, 0), setup and far field."""
        port = d.add_object("CreateRectangle", name="Probe", Position=f"{xf},-wp/2,0", Axis="X", YSize="wp",
                            ZSize="h")
        _assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper", [gnd.name, patch.name],
                UseMaterial="False", Conductivity=f"{sub.sigma:g}", UseThickness="True", Thickness="t_cu",
                TwoSided="False")
        _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "1", [port.name], PortNumber="1",
                Impedance=f"{spec['z0']:g}ohm", IntegrationLine=f"{xf},0,0;{xf},0,h")
        _setup(d, spec["f0"], 10.0)
        return d


def circular_patch(f_ghz: float, sub: Substrate) -> dict:
    """Circular patch in the TM11 mode (Balanis, Antenna Theory 14.3): physical radius a, effective radius ae,
    the conductance of radiation, copper and dielectric, and the input resistance at the edge."""
    from scipy.integrate import quad
    from scipy.special import jv
    f, h_cm, er = f_ghz * 1e9, sub.h / 10, sub.er
    F = 8.791e9 / (f * math.sqrt(er))
    a = F / math.sqrt(1 + 2 * h_cm / (math.pi * er * F) * (math.log(math.pi * F / (2 * h_cm)) + 1.7726))
    ae = a * math.sqrt(1 + 2 * h_cm / (math.pi * a * er) * (math.log(math.pi * a / (2 * h_cm)) + 1.7726))
    k0 = 2 * math.pi * f / C0
    x0 = k0 * ae / 100

    def integrand(t):
        x = x0 * math.sin(t)
        return ((jv(0, x) - jv(2, x)) ** 2 + math.cos(t) ** 2 * (jv(0, x) + jv(2, x)) ** 2) * math.sin(t)
    g_rad = x0 ** 2 / 480 * quad(integrand, 0, math.pi / 2)[0]
    mu0, h = 4e-7 * math.pi, sub.h * 1e-3
    chi = 1.84118 ** 2 - 1
    g_cu = math.pi * (math.pi * f * mu0) ** -1.5 / (4 * h * h * math.sqrt(sub.sigma)) * chi
    g_d = sub.tand / (4 * mu0 * h * f) * chi
    gt = g_rad + g_cu + g_d
    return {"a": a * 10, "ae": ae * 10, "g_rad": g_rad, "gt": gt, "r_edge": 1 / gt,
            "efficiency": g_rad / gt}


class CircularPatch(_Patch):
    """Circular microstrip patch in the TM11 mode, fed by a probe on the x axis at the radius where the input
    resistance is the port impedance."""
    key = "cpatch"
    title = "Circular patch"
    category = "antenna"
    description = "Probe fed, TM11 cavity model"
    use = "Compact, symmetric pattern"
    fields = (
        Field("f0", "Frequency", 2.45, "GHz", 0.3, 40),
        Field("z0", "Port impedance", 50.0, "ohm", 10, 300, 1),
        Field("rl", "Return loss at the frequency", 15.0, "dB", 3, 40, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"CircularPatch_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        from scipy.optimize import brentq
        from scipy.special import jv
        c = circular_patch(spec["f0"], sub)
        a, ae, z0 = c["a"], c["ae"], spec["z0"]
        warnings = []
        # R(rho) = R_edge J1(k rho)^2 / J1(k ae)^2, k ae = 1.841 (J1 rises monotonically up to there)
        if z0 >= c["r_edge"]:
            rho = 0.95 * a
            warnings.append(f"The edge resistance {c['r_edge']:.0f} ohm is below the port impedance: probe at the "
                            "edge (use a thicker substrate or a lower port impedance)")
        else:
            target = jv(1, 1.84118) * math.sqrt(z0 / c["r_edge"])
            rho = brentq(lambda r: jv(1, 1.84118 * r / ae) - target, 1e-6, ae) if target > 0 else 0.1 * a
        rho = max(rho, PROBE)
        syn = Synthesis([
            Variable("a", a, f"patch radius (effective {ae:.2f} mm with the fringing field)", lo=0.94 * a, hi=1.06 * a),
            Variable("rho", rho, "probe distance from the centre", lo=max(PROBE, 0.5 * rho), hi=min(0.9 * a, 1.6 * rho)),
            Variable("wp", PROBE, "probe width", include=False),
        ])
        syn.note = (f"TM11 cavity: radius {a:.2f} mm on er {sub.er:g}, h {sub.h:g} mm; input resistance "
                    f"{c['r_edge']:.0f} ohm at the edge (radiation efficiency about {100 * c['efficiency']:.0f} %), "
                    f"{z0:g} ohm at {rho:.2f} mm from the centre. The optimization tunes the radius and the probe "
                    "on the adaptive solution at the frequency.")
        syn.warnings += warnings
        return syn

    def model3d(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        d, gnd = self._start(name, syn, sub, f"a + {_patch_board(spec, sub):.4g}mm")
        patch = d.add_object("CreateCircle", name="Patch", CenterPosition="0,0,h", Axis="Z", Radius="a",
                             NumberOfSegments="0")
        return self._finish(d, spec, sub, gnd, patch, "rho")

    def goals(self, spec: dict) -> list[dict]:
        return [goal("dB(S(1,1))", spec["f0"], spec["f0"], "<=", -spec["rl"])]


def square_patch(f_ghz: float, sub: Substrate) -> dict:
    """Square patch (transmission line model, W = L): side, total Q (radiation from the Jackson-Alexopoulos
    bandwidth, dielectric and copper) and the input resistance at the edge (slot conductances, losses
    included)."""
    from ..antennas import _slot_conductances
    f, h, er = f_ghz * 1e9, sub.h * 1e-3, sub.er
    length = C0 / (2 * f * math.sqrt(er))
    for _ in range(20):
        ee = (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 * h / length)
        dl = 0.412 * h * (ee + 0.3) * (length / h + 0.264) / ((ee - 0.258) * (length / h + 0.8))
        length = C0 / (2 * f * math.sqrt(ee)) - 2 * dl
    lam0 = C0 / f
    bw = 3.77 * (er - 1) / er ** 2 * h / lam0                   # VSWR 2, W = L
    q_rad = 1 / (math.sqrt(2) * bw)
    q_d, q_c = _patch_losses(f, h, sub)
    q = 1 / (1 / q_rad + 1 / q_d + 1 / q_c)
    g1, g12 = _slot_conductances(length, length, f)
    r_edge = 1 / (2 * (g1 + g12)) * q / q_rad
    return {"L": length * 1e3, "q": q, "q_rad": q_rad, "r_edge": r_edge, "efficiency": q / q_rad}


class CPPatch(_Patch):
    """Circularly polarized square patch with one probe: two corners on a diagonal cut off, so that the two
    orthogonal modes split around the frequency and add up 90 degrees apart (truncated area / patch area =
    1 / (2 Q))."""
    key = "cppatch"
    title = "Circularly polarized patch"
    category = "antenna"
    description = "Truncated corners, one probe"
    use = "GNSS, satellite links"
    fields = (
        Field("f0", "Frequency", 2.45, "GHz", 0.3, 40),
        Field("z0", "Port impedance", 50.0, "ohm", 10, 300, 1),
        Field("rl", "Return loss at the frequency", 15.0, "dB", 3, 40, 1),
        Field("ar", "Axial ratio at the frequency", 3.0, "dB", 0.5, 10, 1, help="In the direction of the maximum"),
    )

    def default_name(self, spec: dict) -> str:
        return f"CPPatch_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        s = square_patch(spec["f0"], sub)
        L, q, z0 = s["L"], s["q"], spec["z0"]
        c = L / math.sqrt(2 * q)
        # the probe on the x axis drives the two diagonal modes with half the power each (cos^2 45 degrees); at the
        # frequency they are 45 degrees off resonance on either side, R/2 (1 - j)/2 + R/2 (1 + j)/2 = R/2: the input
        # resistance is half the one of the x-polarized patch at the same point, so the probe sits where that is 2 Z0
        warnings = []
        if 2 * z0 >= s["r_edge"]:
            y0 = 0.0
            warnings.append(f"The edge resistance {s['r_edge']:.0f} ohm is below twice the port impedance: probe at "
                            "the edge")
        else:
            y0 = L / math.pi * math.acos(math.sqrt(2 * z0 / s["r_edge"]))
        xf = max(L / 2 - y0, PROBE)
        syn = Synthesis([
            Variable("L", L, "side of the square patch", lo=0.94 * L, hi=1.06 * L),
            Variable("c", c, f"cut of the two corners (legs of the triangles), area ratio 1/(2 Q), Q = {q:.0f}",
                     lo=0.5 * c, hi=min(1.8 * c, 0.3 * L)),
            Variable("xf", xf, "probe distance from the centre on the x axis", lo=max(PROBE, 0.6 * xf),
                     hi=min(0.47 * L, 1.4 * xf)),
            Variable("wp", PROBE, "probe width", include=False),
        ])
        syn.note = (f"Square patch {L:.2f} mm (W = L), total Q {q:.0f} with the losses: the corners on one diagonal "
                    f"are cut by {c:.2f} mm, so the two modes split by about 1/Q and are 90 degrees apart at the "
                    f"frequency. Edge resistance {s['r_edge']:.0f} ohm; the probe drives both modes, so it sits where the "
                    f"linear patch would have {2 * z0:g} ohm, {xf:.2f} mm from the centre. The "
                    "optimization tunes the side, the cut and the probe on return loss and axial ratio.")
        syn.warnings += warnings
        return syn

    def model3d(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        d, gnd = self._start(name, syn, sub, f"L/2 + {_patch_board(spec, sub):.4g}mm")
        # counter-clockwise, the corners at (+,+) and (-,-) cut
        pts = ["-L/2+c,-L/2", "L/2,-L/2", "L/2,L/2-c", "L/2-c,L/2", "-L/2,L/2", "-L/2,-L/2+c"]
        patch = d.add_object("CreatePolyline", name="Patch", Points=";".join(f"{p},h" for p in pts), Closed="True",
                             Covered="True")
        return self._finish(d, spec, sub, gnd, patch, "xf")

    def goals(self, spec: dict) -> list[dict]:
        f0 = spec["f0"]
        return [goal("dB(S(1,1))", f0, f0, "<=", -spec["rl"]), goal("dB(AxialRatioAtMax)", f0, f0, "<=", spec["ar"])]
