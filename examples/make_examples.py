"""Create the example projects shipped with Hertzon.

python examples/make_examples.py  ->  examples/*.hzn
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.model.design import (Project, Design, Command, Assignment, FaceRef, Setup, Sweep, InfiniteSphere,  # noqa: E402
                                  Report)
from hertzon.model import schema  # noqa: E402


def boundary(d: Design, kind: str, name: str, faces=(), objects=(), **props) -> Assignment:
    p = schema.defaults(schema.BOUNDARIES[kind])
    p.update({k: str(v) for k, v in props.items()})
    a = Assignment(name, kind, p, faces=[FaceRef(*f) for f in faces], objects=list(objects))
    d.boundaries.append(a)
    return a


def excitation(d: Design, kind: str, name: str, faces=(), objects=(), **props) -> Assignment:
    p = schema.defaults(schema.EXCITATIONS[kind])
    p.update({k: str(v) for k, v in props.items()})
    a = Assignment(name, kind, p, faces=[FaceRef(*f) for f in faces], objects=list(objects))
    d.excitations.append(a)
    return a


def setup(d: Design, name="Setup1", **props) -> Setup:
    s = Setup(name)
    s.props.update({k: str(v) for k, v in props.items()})
    d.setups.append(s)
    return s


def sweep(s: Setup, name="Sweep", **props) -> Sweep:
    sw = Sweep(name)
    sw.props.update({k: str(v) for k, v in props.items()})
    s.sweeps.append(sw)
    return sw


def waveguide_post() -> Project:
    """WR-90 waveguide with an inductive post, two wave ports."""
    p = Project("WaveguidePost")
    d = p.designs[0]
    d.name = "WR90_Post"
    d.variables.update({"a": "22.86mm", "b": "10.16mm", "L": "50mm", "r_post": "1.5mm"})
    wg = d.add_object("CreateBox", name="Waveguide", material="vacuum", Position="0,0,0", XSize="a", YSize="L", ZSize="b")
    post = d.add_object("CreateCylinder", name="Post", material="pec", CenterPosition="a/2,L/2,0", Radius="r_post",
                        Height="b", Axis="Z")
    wg.history.append(Command.new("Subtract", Tools=post.name))
    post.consumed_by = wg.name
    # faces of the waveguide: find indices with y = 0 and y = L at build time is tedious here,
    # so ports are placed on dedicated sheets exactly on the end faces (as in the reference workflow)
    p1 = d.add_object("CreateRectangle", name="Port1Sheet", Position="0,0,0", Axis="Y", ZSize="b", XSize="a")
    p2 = d.add_object("CreateRectangle", name="Port2Sheet", Position="0,L,0", Axis="Y", ZSize="b", XSize="a")
    excitation(d, "Wave Port", "1", objects=[p1.name], PortNumber=1)
    excitation(d, "Wave Port", "2", objects=[p2.name], PortNumber=2)
    s = setup(d, Frequency="10GHz", MaximumPasses=6, MaximumDeltaS=0.02)
    sweep(s, "Sweep", SweepType="Interpolating", Distribution="Linear Count", Start="8GHz", Stop="12GHz", Count=201)
    sweep(s, "Discrete", SweepType="Discrete", Distribution="Linear Step", Start="8GHz", Stop="12GHz", Step="0.5GHz",
          Enabled="False")
    return p


def microstrip_line() -> Project:
    """50 ohm microstrip on FR4 with lumped ports and Auto Open (filter variant)."""
    p = Project("Microstrip")
    d = p.designs[0]
    d.name = "Microstrip50"
    d.variables.update({"h": "1.6mm", "w": "3mm", "Lsub": "40mm", "Wsub": "30mm"})
    d.settings["AutoOpen"] = "Auto Open - Filter"
    d.settings["FilterAirMargin"] = "8mm"
    sub = d.add_object("CreateBox", name="Substrate", material="FR4_epoxy", Position="-Wsub/2,0,0", XSize="Wsub",
                       YSize="Lsub", ZSize="h")
    gnd = d.add_object("CreateRectangle", name="Ground", Position="-Wsub/2,0,0", Axis="Z", XSize="Wsub", YSize="Lsub")
    trace = d.add_object("CreateRectangle", name="Trace", Position="-w/2,0,h", Axis="Z", XSize="w", YSize="Lsub")
    port1 = d.add_object("CreateRectangle", name="Port1", Position="-w/2,0,0", Axis="Y", ZSize="h", XSize="w")
    port2 = d.add_object("CreateRectangle", name="Port2", Position="-w/2,Lsub,0", Axis="Y", ZSize="h", XSize="w")
    boundary(d, "Perfect E", "PerfE_Ground", objects=[gnd.name])
    boundary(d, "Finite Conductivity", "Copper_Trace", objects=[trace.name], UseThickness="True", Thickness="35um",
             TwoSided="True")
    excitation(d, "Lumped Port", "1", objects=[port1.name], PortNumber=1, IntegrationLine="0,0,0;0,0,h")
    excitation(d, "Lumped Port", "2", objects=[port2.name], PortNumber=2, IntegrationLine="0,Lsub,0;0,Lsub,h")
    s = setup(d, Frequency="4GHz", MaximumPasses=5, MaximumDeltaS=0.03)
    sweep(s, "Sweep", SweepType="Interpolating", Distribution="Linear Count", Start="1GHz", Stop="6GHz", Count=101)
    return p


def patch_antenna() -> Project:
    """2.45 GHz inset-fed patch with Auto Open (PML chosen automatically) and far field."""
    p = Project("PatchAntenna")
    d = p.designs[0]
    d.name = "Patch245"
    d.variables.update({"h": "1.6mm", "Wp": "37.3mm", "Lp": "28.5mm", "Ws": "60mm", "Ls": "60mm",
                        "wf": "3mm", "inset": "8mm", "gap": "1mm"})
    d.settings["AutoOpen"] = "Auto Open"
    d.settings["AutoOpenBoundary"] = "Auto"
    d.add_object("CreateBox", name="Substrate", material="FR4_epoxy", Position="-Ws/2,-Ls/2,0", XSize="Ws",
                 YSize="Ls", ZSize="h")
    gnd = d.add_object("CreateRectangle", name="Ground", Position="-Ws/2,-Ls/2,0", Axis="Z", XSize="Ws", YSize="Ls")
    patch = d.add_object("CreateRectangle", name="Patch", Position="-Wp/2,-Lp/2,h", Axis="Z", XSize="Wp", YSize="Lp")
    notch = d.add_object("CreateRectangle", name="Notch", Position="-wf/2-gap,-Lp/2,h", Axis="Z", XSize="wf+2*gap",
                         YSize="inset")
    patch.history.append(Command.new("Subtract", Tools=notch.name))
    notch.consumed_by = patch.name
    feed = d.add_object("CreateRectangle", name="Feed", Position="-wf/2,-Ls/2,h", Axis="Z", XSize="wf",
                        YSize="Ls/2-Lp/2+inset")
    patch.history.append(Command.new("Unite", Tools=feed.name))
    feed.consumed_by = patch.name
    port = d.add_object("CreateRectangle", name="PortSheet", Position="-wf/2,-Ls/2,0", Axis="Y", ZSize="h", XSize="wf")
    boundary(d, "Perfect E", "PerfE_Ground", objects=[gnd.name])
    boundary(d, "Perfect E", "PerfE_Patch", objects=[patch.name])
    excitation(d, "Lumped Port", "1", objects=[port.name], PortNumber=1, IntegrationLine="0,-Ls/2,0;0,-Ls/2,h")
    s = setup(d, Frequency="2.45GHz", MaximumPasses=5, MaximumDeltaS=0.02, MaximumRefinement=100000,
              MinimumConvergedPasses=1)
    sweep(s, "Sweep", SweepType="Interpolating", Distribution="Linear Count", Start="2.2GHz", Stop="2.7GHz", Count=201)
    d.spheres.append(InfiniteSphere("Infinite Sphere1"))
    return p


def hairpin_filter() -> Project:
    """2.3-2.6 GHz band-pass of three tapped hairpins on RO4350B with the conjugate gradient optimization
    (Tools > Filter Design > Tapped Hairpin with its defaults)."""
    from hertzon.model.filters import HairpinSpec, hairpin_project
    p = hairpin_project(HairpinSpec(n=3))
    p.name = "HairpinFilter"
    return p


def differential_pair() -> Project:
    """Edge-coupled microstrip pair on RO4350B in a shielded box, terminal solution: one wave port per end with
    a terminal per trace (the even and odd mode are absorbed each with its own propagation constant), one
    differential pair per end and the mixed-mode S-parameters."""
    import json
    p = Project("DifferentialPair")
    d = p.designs[0]
    d.name = "CoupledMicrostrip"
    d.settings["SolutionType"] = "Terminal"
    d.settings["DifferentialPairs"] = json.dumps([{"pos": 1, "neg": 2}, {"pos": 3, "neg": 4}])
    d.variables.update({"h": "0.508mm", "w": "0.8mm", "s": "0.4mm", "L": "20mm", "Wbox": "6mm", "Hair": "3mm"})
    d.add_object("CreateBox", name="Substrate", material="Rogers RO4350B (tm)", Position="-Wbox/2,0,0", XSize="Wbox",
                 YSize="L", ZSize="h")
    d.add_object("CreateBox", name="Air", material="air", Position="-Wbox/2,0,h", XSize="Wbox", YSize="L",
                 ZSize="Hair")
    tp = d.add_object("CreateRectangle", name="TraceP", Position="-s/2-w,0,h", Axis="Z", XSize="w", YSize="L")
    tn = d.add_object("CreateRectangle", name="TraceN", Position="s/2,0,h", Axis="Z", XSize="w", YSize="L")
    p1 = d.add_object("CreateRectangle", name="Port1Sheet", Position="-Wbox/2,0,0", Axis="Y", XSize="Wbox",
                      ZSize="h+Hair")
    p2 = d.add_object("CreateRectangle", name="Port2Sheet", Position="-Wbox/2,L,0", Axis="Y", XSize="Wbox",
                      ZSize="h+Hair")
    boundary(d, "Perfect E", "PerfE_Traces", objects=[tp.name, tn.name])
    # the outer faces of the box are a perfect conductor: the shield and the ground of the lines
    excitation(d, "Wave Port", "P1", objects=[p1.name], PortNumber=1)
    excitation(d, "Wave Port", "P2", objects=[p2.name], PortNumber=2)
    s = setup(d, Frequency="10GHz", MaximumPasses=6, MaximumDeltaS=0.02)
    sweep(s, "Sweep", SweepType="Interpolating", Distribution="Linear Count", Start="1GHz", Stop="12GHz", Count=111)
    sol = "Setup1 : Sweep"
    d.reports += [
        Report("Differential S", "Rectangular", sol, ["dB(S(Diff1,Diff1))", "dB(S(Diff2,Diff1))"]),
        Report("Mode Conversion", "Rectangular", sol, ["dB(S(Comm1,Diff1))", "dB(S(Comm2,Diff1))"]),
        Report("Single-Ended S", "Rectangular", sol, ["dB(S(1,1))", "dB(S(3,1))", "dB(S(2,1))", "dB(S(4,1))"]),
        Report("Line Impedance", "Rectangular", sol, ["re(Zo(1))", "re(Zo(2))"]),
        Report("Propagation Constant", "Rectangular", sol, ["im(Gamma(1))", "im(Gamma(2))"]),
    ]
    return p


def fss_unit_cell() -> Project:
    """Frequency selective surface: a cross dipole on RO3003 in a periodic unit cell, Floquet ports below and
    above, TE00 and TM00. The cell is the infinite array; the scan angle is the variable scan_theta (in the
    xz plane). The transmission has its stop band where the cross arms are half a wavelength long."""
    p = Project("FSS_UnitCell")
    d = p.designs[0]
    d.name = "CrossDipoleFSS"
    d.variables.update({"px": "15mm", "py": "15mm", "h": "0.76mm", "ls": "11mm", "ws": "1mm", "air": "15mm",
                        "scan_theta": "0deg"})
    d.add_object("CreateBox", name="Cell", material="vacuum", Position="-px/2,-py/2,-air", XSize="px", YSize="py",
                 ZSize="2*air+h").transparency = 0.85
    d.add_object("CreateBox", name="Substrate", material="Rogers RO3003 (tm)", Position="-px/2,-py/2,0", XSize="px",
                 YSize="py", ZSize="h")
    a = d.add_object("CreateRectangle", name="Cross", Position="-ls/2,-ws/2,h", Axis="Z", XSize="ls", YSize="ws")
    b = d.add_object("CreateRectangle", name="CrossY", Position="-ws/2,-ls/2,h", Axis="Z", XSize="ws", YSize="ls")
    a.history.append(Command.new("Unite", Tools=b.name, KeepOriginals="False"))
    b.consumed_by = a.name
    boundary(d, "Perfect E", "PerfE_Cross", objects=[a.name])
    # the side faces of the cell: sheets on them carry the periodic boundaries (Secondary = Primary moved by px / py)
    side = {}
    for nm, pos, ax, sx, sy, sz in (("SideXm", "-px/2,-py/2,-air", "X", "", "py", "2*air+h"),
                                    ("SideXp", "px/2,-py/2,-air", "X", "", "py", "2*air+h"),
                                    ("SideYm", "-px/2,-py/2,-air", "Y", "px", "", "2*air+h"),
                                    ("SideYp", "-px/2,py/2,-air", "Y", "px", "", "2*air+h")):
        kw = {k: v for k, v in (("XSize", sx), ("YSize", sy), ("ZSize", sz)) if v}
        side[nm] = d.add_object("CreateRectangle", name=nm, Position=pos, Axis=ax, **kw).name
    boundary(d, "Primary", "Primary_X", objects=[side["SideXm"]], UVector="-px/2,-py/2,-air;-px/2,py/2,-air")
    boundary(d, "Secondary", "Secondary_X", objects=[side["SideXp"]], Primary="Primary_X",
             UVector="px/2,-py/2,-air;px/2,py/2,-air", PhaseMode="Scan Angles", ScanTheta="scan_theta", ScanPhi="0deg")
    boundary(d, "Primary", "Primary_Y", objects=[side["SideYm"]], UVector="-px/2,-py/2,-air;px/2,-py/2,-air")
    boundary(d, "Secondary", "Secondary_Y", objects=[side["SideYp"]], Primary="Primary_Y",
             UVector="-px/2,py/2,-air;px/2,py/2,-air", PhaseMode="Scan Angles", ScanTheta="scan_theta", ScanPhi="0deg")
    bot = d.add_object("CreateRectangle", name="PortBottom", Position="-px/2,-py/2,-air", Axis="Z", XSize="px", YSize="py")
    top = d.add_object("CreateRectangle", name="PortTop", Position="-px/2,-py/2,air+h", Axis="Z", XSize="px", YSize="py")
    excitation(d, "Floquet Port", "FP1", objects=[bot.name], PortNumber=1, NumberOfModes=2)
    excitation(d, "Floquet Port", "FP2", objects=[top.name], PortNumber=2, NumberOfModes=2)
    s = setup(d, Frequency="12GHz", MaximumPasses=10, MaximumDeltaS=0.02)
    sweep(s, "Sweep", SweepType="Interpolating", Distribution="Linear Count", Start="6GHz", Stop="18GHz", Count=241)
    sol = "Setup1 : Sweep"
    # S indices: 1 = FP1:TE00, 2 = FP1:TM00, 3 = FP2:TE00, 4 = FP2:TM00
    d.reports += [
        Report("Transmission", "Rectangular", sol, ["dB(S(3,1))", "dB(S(4,2))"]),
        Report("Reflection", "Rectangular", sol, ["dB(S(1,1))", "dB(S(2,2))"]),
    ]
    return p


if __name__ == "__main__":
    out = Path(__file__).resolve().parent
    for fn in (waveguide_post, microstrip_line, patch_antenna, hairpin_filter, differential_pair, fss_unit_cell):
        proj = fn()
        path = out / f"{proj.name}.hzn"
        proj.save(path)
        print("wrote", path)
