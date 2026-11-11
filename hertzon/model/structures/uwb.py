"""Printed ultra-wideband monopoles of the design wizard: a circular disc or a rectangular plate with bevelled
lower corners, printed on the wizard's substrate and fed either

  * by a microstrip line with a partial ground on the bottom layer (the ground ends below the radiator), or
  * by a coplanar waveguide with the ground on the same (top) layer as the radiator.

Start values from the lowest frequency of a planar monopole (the equivalent cylindrical monopole of the same
height and area): f_L = 7.2 / ((l + r + p) k) GHz with l, r, p in cm, l the height of the radiator, r the radius
of the cylinder of the same area (2 pi r l = area), p the gap between the radiator and the ground, k the
substrate factor (about 1.15 on FR4 1.6 mm; Ray, "Design aspects of printed monopole antennas for ultra-wide band
applications", Int. J. Antennas Propag. 2008). The optimization tunes the radiator, the gap and the ground length
on the sweep of the whole band (Derivative Trust Region on the band model of the tuning).
"""
from __future__ import annotations

import math

from .. import schema
from ..design import Command, Design, InfiniteSphere, Setup, Sweep
from ..microstrip import C0
from .antennas import _assign
from .base import AVAILABLE, Field, Structure, Substrate, Synthesis, Variable, goal

MICROSTRIP, CPW = 0, 1
FEEDS = ("Microstrip, partial ground", "Coplanar, ground on top")


def substrate_factor(er: float) -> float:
    """k of the lowest frequency of a printed monopole: sqrt of an effective permittivity 1 + 0.095 (er - 1)
    (most of the field runs in air; 1.15 on FR4, er 4.4, as Ray)."""
    return math.sqrt(1 + 0.095 * (er - 1))


def cpw_z0(w: float, s: float, sub: Substrate) -> float:
    """Impedance of a coplanar waveguide without a ground below: strip w, gaps s (mm)."""
    from ...solver.tlines import cpw_static
    return cpw_static(w * 1e-3, s * 1e-3, sub.h * 1e-3, sub.t * 1e-6, sub.er, False)[0]


def cpw_line(z0: float, sub: Substrate) -> tuple[float, float] | None:
    """(strip width, gap) in mm of a coplanar line of z0: a strip of about 3.5 h (1 to 3 mm), the gap from it; a
    gap below the smallest feature is raised to it and the strip found for it. None if out of reach."""
    from scipy.optimize import brentq
    w = min(3.0, max(1.0, 3.5 * sub.h))
    try:
        s = brentq(lambda g: cpw_z0(w, g, sub) - z0, 1e-3, 20 * w, xtol=1e-7)
    except ValueError:
        return None
    if s < sub.min_feature:
        s = sub.min_feature
        try:
            w = brentq(lambda x: cpw_z0(x, s, sub) - z0, 0.02, 30.0, xtol=1e-7)
        except ValueError:
            return None
    return w, s


class _UWB(Structure):
    category = "antenna"
    status = AVAILABLE
    kind = "3d"
    coarse = ""
    optimizer = "Derivative Trust Region"
    solution = "Setup1 : Sweep"
    ports = 1
    common = (
        Field("feed", "Feed", MICROSTRIP, choices=FEEDS, help="Microstrip: the strip on top, a ground on the bottom "
              "that ends below the radiator. Coplanar: the strip between two grounds on the top layer"),
        Field("fl", "Lowest frequency", 3.1, "GHz", 0.3, 30, 3),
        Field("fh", "Highest frequency", 10.6, "GHz", 0.5, 60, 3),
        Field("z0", "Port impedance", 50.0, "ohm", 10, 200, 1),
        Field("rl", "Return loss in the band", 10.0, "dB", 3, 30, 1),
    )

    # ---- what a radiator provides
    def radiator(self, spec: dict, total: float, p: float) -> tuple[list[Variable], float, float, str]:
        """(its variables, its width, its height, a note) for an equivalent height l + r = total - p (mm)."""
        raise NotImplementedError

    def draw(self, d: Design, y0: str) -> object:
        """The radiator on the top layer, its lowest point on the feed axis at y = y0."""
        raise NotImplementedError

    # ---- synthesis
    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        fl, fh, z0 = spec["fl"], spec["fh"], spec["z0"]
        if fh <= 1.2 * fl:
            raise ValueError("The highest frequency must be at least 1.2 times the lowest")
        cpw = round(spec["feed"]) == CPW
        lam_l = C0 / (fl * 1e9) * 1e3
        k = substrate_factor(sub.er)
        total = 72.0 / (fl * k)                                     # l + r + p in mm
        p = max(0.5, 2 * sub.min_feature)
        rad, width, height, note = self.radiator(spec, total, p)
        fc = math.sqrt(fl * fh)
        warnings = []
        if cpw:
            line = cpw_line(z0, sub)
            if line is None:
                wf, s = sub.width(z0, fc), max(0.3, sub.min_feature)
                warnings.append(f"No coplanar line of {z0:g} ohm on this substrate: the strip has the microstrip "
                                f"width and {s:g} mm gaps")
            else:
                wf, s = line
            feedvars = [Variable("wf", wf, f"strip width of the coplanar line, {z0:g} ohm", include=False),
                        Variable("s", s, "gap between the strip and the coplanar grounds", include=False)]
        else:
            wf = sub.width(z0, fc)
            feedvars = [Variable("wf", wf, f"microstrip width, {z0:g} ohm at {fc:.3g} GHz", include=False)]
        lg = 0.15 * lam_l
        bx = max(width + 0.16 * lam_l, wf + 10 * sub.h)
        margin = max(1.0, 2 * sub.h)
        if cpw and bx / 2 < wf / 2 + feedvars[1].value + 2:
            bx = wf + 2 * feedvars[1].value + 4
        syn = Synthesis(rad + [
            Variable("p", p, "gap between the ground edge and the radiator", lo=max(sub.min_feature, 0.4 * p),
                     hi=4 * p),
            Variable("Lg", lg, "ground length (to the board edge with the port)", lo=0.6 * lg, hi=1.5 * lg),
            *feedvars,
            Variable("Bx", bx, "board width", include=False),
            Variable("By", lg + p + height + margin, "board length", include=False),
            Variable("h", sub.h, "substrate height", include=False),
        ])
        where = ("the strip between two grounds on the top layer, the grounds joined at the board edge by the port "
                 "(as a connector)" if cpw else "a microstrip on the top layer, the ground on the bottom ends below "
                 "the radiator (partial ground)")
        syn.note = (f"Lowest frequency of a planar monopole f_L = 7.2 / ((l + r + p) k) GHz (cm): l + r + p = "
                    f"{total:.1f} mm with k = {k:.3f} on er {sub.er:g}. {note} Feed: {where}. The ground length "
                    f"starts at 0.15 lambda at f_L ({lg:.1f} mm), the gap at {p:g} mm. The optimization tunes the "
                    f"radiator, the gap and the ground on the sweep {fl:g} to {fh:g} GHz.")
        if bx > 0.6 * lam_l:
            warnings.append("The board is wider than 0.6 lambda at the lowest frequency")
        syn.warnings += warnings
        return syn

    def materials3d(self, spec: dict, sub: Substrate) -> list:
        from ..materials import Material
        return [Material(f"UWB_Er{sub.er:g}", permittivity=f"{sub.er:g}", loss_tangent=f"{sub.tand:g}",
                         color="#2e7d32", description="Substrate of a printed UWB monopole")]

    # ---- 3D design
    def model3d(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        cpw = round(spec["feed"]) == CPW
        d = Design(name=name, units="mm")
        d.settings.update(AutoOpen="Auto Open", AutoOpenBoundary="Auto")
        d.variables.update({v.name: v.text() for v in syn.variables if v.name != "h"})
        d.variables.update(h=f"{sub.h:g}mm", t_cu=f"{sub.t:g}um")
        d.add_object("CreateBox", name="Substrate", material=f"UWB_Er{sub.er:g}", Position="-Bx/2,0,0",
                     XSize="Bx", YSize="By", ZSize="h")
        rad = self.draw(d, "Lg + p")
        if cpw:
            # grounds left and right of the strip, joined at the board edge by a bar under the port (the shell of
            # an edge connector); the strip starts above the port gap
            d.variables.update(gb="1mm", gp=f"{max(0.5, sub.min_feature):g}mm")
            gnd = d.add_object("CreateRectangle", name="CPWGndLeft", Position="-Bx/2,0,h", Axis="Z",
                               XSize="Bx/2 - wf/2 - s", YSize="Lg")
            for nm, pos, xs in (("CPWGndRight", "wf/2 + s,0,h", "Bx/2 - wf/2 - s"),
                                ("CPWGndBar", "-wf/2 - s - 0.5mm,0,h", "wf + 2*s + 1mm")):
                o = d.add_object("CreateRectangle", name=nm, Position=pos, Axis="Z", XSize=xs,
                                 YSize="Lg" if nm == "CPWGndRight" else "gb")
                gnd.history.append(Command.new("Unite", Tools=o.name))
                o.consumed_by = gnd.name
            strip = d.add_object("CreateRectangle", name="Feed", Position="-wf/2,gb + gp,h", Axis="Z", XSize="wf",
                                 YSize="Lg + p + wf - gb - gp")
            port = d.add_object("CreateRectangle", name="PortSheet", Position="-wf/2,gb,h", Axis="Z", XSize="wf",
                                YSize="gp")
            line = "0,gb,h;0,gb + gp,h"
        else:
            gnd = d.add_object("CreateRectangle", name="PartialGnd", Position="-Bx/2,0,0", Axis="Z", XSize="Bx",
                               YSize="Lg")
            strip = d.add_object("CreateRectangle", name="Feed", Position="-wf/2,0,h", Axis="Z", XSize="wf",
                                 YSize="Lg + p + wf")
            port = d.add_object("CreateRectangle", name="PortSheet", Position="-wf/2,0,0", Axis="Y", XSize="wf",
                                ZSize="h")
            line = "0,0,0;0,0,h"
        rad.history.append(Command.new("Unite", Tools=strip.name))
        strip.consumed_by = rad.name
        _assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper", [gnd.name, rad.name],
                UseMaterial="False", Conductivity=f"{sub.sigma:g}", UseThickness="True", Thickness="t_cu",
                TwoSided="False")
        _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "1", [port.name], PortNumber="1",
                Impedance=f"{spec['z0']:g}ohm", IntegrationLine=line)
        a, b, n = self.sweep(spec)
        st = Setup("Setup1")
        st.props.update(Frequency=f"{spec['fh']:g}GHz", MaximumPasses="12", MaximumDeltaS="0.02",
                        MinimumConvergedPasses="2")
        sw = Sweep("Sweep")
        sw.props.update(SweepType="Interpolating", Distribution="Linear Count", Start=f"{a:.6g}GHz",
                        Stop=f"{b:.6g}GHz", Count=str(n))
        st.sweeps.append(sw)
        d.setups.append(st)
        d.spheres.append(InfiniteSphere("Infinite Sphere1"))
        return d

    def goals(self, spec: dict) -> list[dict]:
        return [goal("dB(S(1,1))", spec["fl"], spec["fh"], "<=", -spec["rl"])]

    def adapt(self, spec: dict) -> float | None:
        return spec["fh"]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.8 * spec["fl"], 1.1 * spec["fh"], 201

    def default_name(self, spec: dict) -> str:
        tag = "CPW" if round(spec["feed"]) == CPW else "MS"
        return f"{self.prefix}_{tag}_{spec['fl']:g}-{spec['fh']:g}GHz"


class UWBDisc(_UWB):
    """Circular disc monopole: l = 2 a, r = a / 4 (2 pi r l = pi a^2), so 2.25 a + p = 7.2 / (f_L k) cm."""
    key = "uwb"
    title = "UWB disc monopole"
    description = "Printed disc, microstrip or coplanar feed"
    use = "Ultra-wideband 3.1-10.6 GHz, wideband sensing"
    prefix = "UWBDisc"
    fields = _UWB.common

    def radiator(self, spec: dict, total: float, p: float):
        a = (total - p) / 2.25
        return ([Variable("a", a, "disc radius")], 2 * a, 2 * a,
                f"A disc of radius a is l = 2a high with r = a/4: a = {a:.2f} mm.")

    def draw(self, d: Design, y0: str):
        return d.add_object("CreateCircle", name="Radiator", CenterPosition=f"0,{y0} + a,h", Axis="Z", Radius="a",
                            NumberOfSegments="0")


class UWBRect(_UWB):
    """Rectangular plate W x L with the two lower corners bevelled by c: l = L, r = W / (2 pi)."""
    key = "uwbrect"
    title = "UWB rectangular monopole"
    description = "Printed plate, bevelled corners, microstrip or coplanar feed"
    use = "Ultra-wideband 3.1-10.6 GHz, compact devices"
    prefix = "UWBRect"
    fields = _UWB.common + (
        Field("aspect", "Width / height", 1.0, "", 0.4, 2.5, 2, help="W / L of the plate"),
    )

    def radiator(self, spec: dict, total: float, p: float):
        q = spec["aspect"]
        L = (total - p) / (1 + q / (2 * math.pi))
        W = q * L
        c = 0.2 * min(W, L)
        return ([Variable("W", W, "plate width"), Variable("L", L, "plate height"),
                 Variable("c", c, "bevel of the two lower corners (legs)", lo=0.25 * c, hi=min(2 * c, 0.45 * W))],
                W, L, f"A plate L high and W wide has r = W/(2 pi): L = {L:.2f} mm, W = {W:.2f} mm.")

    def draw(self, d: Design, y0: str):
        pts = [f"-W/2 + c,{y0}", f"W/2 - c,{y0}", f"W/2,{y0} + c", f"W/2,{y0} + L", f"-W/2,{y0} + L",
               f"-W/2,{y0} + c"]
        return d.add_object("CreatePolyline", name="Radiator", Points=";".join(f"{q},h" for q in pts),
                            Closed="True", Covered="True")
