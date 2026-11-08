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
