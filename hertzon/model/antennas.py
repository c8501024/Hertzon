"""Antenna design: a parametrized inset-fed rectangular microstrip patch (Tools > Antenna Design).

The start dimensions come from the transmission line model (Balanis, Antenna Theory ch. 14): width for
good radiation efficiency, length from the effective permittivity and the fringing extension, the edge
resistance from the slot conductances G1 and G12, and the inset depth that matches it to the feed. The
design comes with setup, sweep, far-field sphere and an optimization of the patch length and the inset
on the return loss at the design frequency: on the adaptive solution (LastAdaptive), so no sweep is solved
while optimizing, with the Derivative Trust Region (the start values are close, so a few solves with the
derivative tuning model are enough). The sweep (+-15 %) is only there to look at the resonance afterwards.

No Qt here: used by the dialog, the example generator and scripts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_trapz = getattr(np, "trapezoid", None) or np.trapz

from . import schema
from .design import Assignment, Command, Design, InfiniteSphere, Optimization, Project, Setup, Sweep
from .microstrip import C0, engine, synthesize


@dataclass
class PatchSpec:
    f0: float = 2.45                    # GHz
    match_f0: float = 20.0              # dB return loss at f0 (goal of the optimization)
    material: str = "FR4_epoxy"
    er: float = 4.4
    tand: float = 0.02
    h: float = 1.6                      # mm
    t: float = 35.0                     # um copper
    z_feed: float = 50.0
    margin: float = 0.0                 # mm ground beyond the patch; 0 = automatic (max(6h, lambda0/10))
    sweep: float = 15.0                 # +- % around f0 (for looking at the resonance; not optimized)
    optimization: bool = True
    name: str = ""
    sigma: float = 5.8e7


def _slot_conductances(w: float, l: float, f: float) -> tuple[float, float]:
    """G1 (single radiating slot) and G12 (mutual) of the transmission line model, in S."""
    k0 = 2 * math.pi * f / C0
    th = np.linspace(1e-6, math.pi - 1e-6, 2001)
    s = np.sin(th)
    c = np.cos(th)
    fac = np.sin(k0 * w / 2 * c) ** 2 / c ** 2 * s ** 3
    g1 = float(_trapz(fac, th)) / (120 * math.pi ** 2)
    tau = np.linspace(0, math.pi, 801)
    j0 = _trapz(np.cos(np.outer(k0 * l * s, np.sin(tau))), tau, axis=1) / math.pi
    g12 = float(_trapz(fac * j0, th)) / (120 * math.pi ** 2)
    return g1, g12


def nominal(spec: PatchSpec) -> dict:
    if spec.f0 <= 0 or spec.h <= 0 or spec.er < 1:
        raise ValueError("Give a positive frequency, substrate height and a permittivity >= 1")
    f, h, er = spec.f0 * 1e9, spec.h * 1e-3, spec.er
    lam0 = C0 / f
    w = C0 / (2 * f) * math.sqrt(2 / (er + 1))
    eeff = (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 * h / w)
    dl = 0.412 * h * (eeff + 0.3) * (w / h + 0.264) / ((eeff - 0.258) * (w / h + 0.8))
    length = C0 / (2 * f * math.sqrt(eeff)) - 2 * dl
    g1, g12 = _slot_conductances(w, length, f)
    rin = 1 / (2 * (g1 + g12))
    warnings = []
    wf = synthesize(spec.z_feed, h, er, spec.t * 1e-6, f, spec.tand, spec.sigma)
    if spec.z_feed >= rin:
        warnings.append(f"The edge resistance {rin:.0f} ohm is below the feed impedance: no inset (edge feed)")
        inset = 0.0
    else:
        inset = length / math.pi * math.acos(math.sqrt(spec.z_feed / rin))
    gap = wf / 3
    margin = spec.margin * 1e-3 or max(6 * h, lam0 / 10)
    if wf + 2 * gap > w:
        warnings.append("The feed line is wider than the patch: use a thicker or lower permittivity substrate")
    mm = 1e3
    return {"Wp": w * mm, "Lp": length * mm, "inset": inset * mm, "wf": wf * mm, "gap": gap * mm,
            "Ws": (w + 2 * margin) * mm, "Ls": (length + 2 * margin) * mm, "eeff": eeff, "rin": rin,
            "lambda0": lam0 * mm, "warnings": warnings, "synthesis": engine()}


def _mm(v: float) -> str:
    return f"{v:.4g}mm" if v < 10 else f"{v:.5g}mm"


def patch_design(spec: PatchSpec) -> tuple[Design, dict]:
    nom = nominal(spec)
    d = Design(name=spec.name or f"Patch_{spec.f0:g}GHz", units="mm")
    d.variables.update({"h": _mm(spec.h), "t_cu": f"{spec.t:g}um", "Wp": _mm(nom["Wp"]), "Lp": _mm(nom["Lp"]),
                        "inset": _mm(nom["inset"]), "wf": _mm(nom["wf"]), "gap": _mm(nom["gap"]),
                        "Ws": _mm(nom["Ws"]), "Ls": _mm(nom["Ls"])})
    d.settings["AutoOpen"] = "Auto Open"
    d.settings["AutoOpenBoundary"] = "Auto"
    d.add_object("CreateBox", name="Substrate", material=spec.material, Position="-Ws/2,-Ls/2,0", XSize="Ws",
                 YSize="Ls", ZSize="h")
    gnd = d.add_object("CreateRectangle", name="Ground", Position="-Ws/2,-Ls/2,0", Axis="Z", XSize="Ws", YSize="Ls")
    patch = d.add_object("CreateRectangle", name="Patch", Position="-Wp/2,-Lp/2,h", Axis="Z", XSize="Wp", YSize="Lp")
    if nom["inset"] > 0:
        notch = d.add_object("CreateRectangle", name="Notch", Position="-wf/2-gap,-Lp/2,h", Axis="Z",
                             XSize="wf+2*gap", YSize="inset")
        patch.history.append(Command.new("Subtract", Tools=notch.name))
        notch.consumed_by = patch.name
    feed = d.add_object("CreateRectangle", name="Feed", Position="-wf/2,-Ls/2,h", Axis="Z", XSize="wf",
                        YSize="Ls/2-Lp/2+inset")
    patch.history.append(Command.new("Unite", Tools=feed.name))
    feed.consumed_by = patch.name
    port = d.add_object("CreateRectangle", name="PortSheet", Position="-wf/2,-Ls/2,0", Axis="Y", ZSize="h", XSize="wf")

    def assign(lst, table, kind, name, objects, **props):
        p = schema.defaults(table[kind])
        p.update(props)
        lst.append(Assignment(name, kind, p, objects=list(objects)))

    # one-sided surface impedance with thickness
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Patch", [patch.name],
           UseThickness="True", Thickness="t_cu", TwoSided="False")
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Ground", [gnd.name],
           UseThickness="True", Thickness="t_cu", TwoSided="False")
    assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "1", [port.name], PortNumber="1",
           Impedance=f"{spec.z_feed:g}ohm", IntegrationLine="0,-Ls/2,0;0,-Ls/2,h")
    f0 = spec.f0
    st = Setup("Setup1")
    # two converged passes in a row: with one, a narrowband patch stopped early on a lucky pass
    st.props.update(Frequency=f"{f0:g}GHz", MaximumPasses="15", MaximumDeltaS="0.02", MinimumConvergedPasses="2")
    sw = Sweep("Sweep")
    a, b = f0 * (1 - spec.sweep / 100), f0 * (1 + spec.sweep / 100)
    sw.props.update(SweepType="Interpolating", Distribution="Linear Count", Start=f"{a:.6g}GHz", Stop=f"{b:.6g}GHz",
                    Count="301")
    st.sweeps.append(sw)
    d.setups.append(st)
    d.spheres.append(InfiniteSphere("Infinite Sphere1"))
    if spec.optimization:
        # one frequency, on the adaptive solution: no sweep per evaluation; the start design is close to the
        # goal, so the tuning model finds the step in a few solves
        op = Optimization("PatchOpt", "Setup1 : LastAdaptive", props={"Optimizer": "Derivative Trust Region"})
        lp, ins = nom["Lp"], nom["inset"]
        di = max(min(0.5 * ins, 0.45 * lp - ins), 0.1 * ins)     # start in the middle, notch inside the patch
        op.variables = [{"name": "Lp", "include": True, "min": _mm(0.94 * lp), "max": _mm(1.06 * lp)},
                        {"name": "inset", "include": ins > 0, "min": _mm(ins - di), "max": _mm(ins + di)},
                        {"name": "Wp", "include": False, "min": _mm(0.9 * nom["Wp"]), "max": _mm(1.1 * nom["Wp"])}]
        op.goals = [{"calc": "dB(S(1,1))", "start": f"{f0:g}GHz", "stop": f"{f0:g}GHz", "cond": "<=",
                     "value": f"{-spec.match_f0:g}", "weight": "1"}]
        d.optimetrics.append(op)
    return d, nom


def patch_project(spec: PatchSpec) -> Project:
    d, _ = patch_design(spec)
    p = Project(f"Patch_{spec.f0:g}GHz")
    p.designs = [d]
    return p


def outlines(design: Design, project: Project | None = None) -> dict[str, list[tuple[float, float]]]:
    """Top view (x, y in mm) of the patch with its feed and inset notch (preview)."""
    ev = design.evaluator(project)
    v = {k: ev.length(k) * 1e3 for k in ("Wp", "Lp", "inset", "wf", "gap", "Ws", "Ls")}
    wp, lp, ins, wf, gap, ls = v["Wp"], v["Lp"], v["inset"], v["wf"], v["gap"], v["Ls"]
    y0 = -lp / 2
    if ins > 0:
        patch = [(-wp / 2, y0), (-wf / 2 - gap, y0), (-wf / 2 - gap, y0 + ins), (-wf / 2, y0 + ins), (-wf / 2, -ls / 2),
                 (wf / 2, -ls / 2), (wf / 2, y0 + ins), (wf / 2 + gap, y0 + ins), (wf / 2 + gap, y0), (wp / 2, y0),
                 (wp / 2, lp / 2), (-wp / 2, lp / 2)]
    else:
        patch = [(-wp / 2, y0), (-wf / 2, y0), (-wf / 2, -ls / 2), (wf / 2, -ls / 2), (wf / 2, y0), (wp / 2, y0),
                 (wp / 2, lp / 2), (-wp / 2, lp / 2)]
    return {"Patch": patch}
