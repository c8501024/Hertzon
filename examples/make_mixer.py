"""Create the example Mixer_DBM.hzn: a broadband double-balanced diode mixer (RF and LO about 1.25 to 4 GHz).

    python examples/make_mixer.py            ->  examples/Mixer_DBM.hzn

Who computes what:
  * Hertzon: the schematic, the microstrip lines, junctions and SMD parts, the reduction of all linear parts to one
    network (Kron), and the 3D design of the balun (solved by EMerge, Hertzon's FEM solver).
  * circuitRF: the two-tone harmonic balance (LO and RF) with the diode model card of the manufacturer.
  * ngspice: the independent check of the harmonic balance (examples/mixer/check_ngspice.py, a transient analysis).

The board is a standard 1.6 mm 4-layer stack: L1-L2 RO4350B 0.762 mm core, L2-L3 RO4450F 0.102 mm prepreg, L3-L4
RO4350B 0.762 mm core; L1 and L4 are ground. The two baluns are broadside-coupled Marchand baluns in stripline: the
primary on L3, the two secondaries on L2 right above it (edge coupling on one layer is far too weak for a broadband
Marchand balun). The diode ring, the capacitors and the connectors sit on L1.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.model.design import Project, Design, Command, Assignment, Setup, Sweep, Report  # noqa: E402,F401
from hertzon.model.materials import Material  # noqa: E402
from hertzon.model import schema  # noqa: E402

HERE = Path(__file__).resolve().parent
PREPREG = "RO4450F prepreg"


def _assign(lst, table, kind, name, objects, **props):
    q = schema.defaults(table[kind])
    q.update({k: str(v) for k, v in props.items()})
    a = Assignment(name, kind, q, objects=list(objects))
    lst.append(a)
    return a


def balun_design(p: Project, name: str = "Balun") -> Design:
    """Broadside-coupled Marchand balun in stripline, inside the 1.6 mm 4-layer board.

    L1 (top) and L4 (bottom) are ground, the coupled strips lie on L3 (the primary) and L2 (the two secondaries)
    right above each other, separated by the 0.102 mm prepreg: a homogeneous stripline, so even and odd mode travel
    at the same speed and the balun keeps its balance over the band (a microstrip version on top of the board does
    not, and edge coupling on one layer is far too weak for a broadband Marchand balun).
    The primary is fed at its left end and open at its right end; each secondary is a quarter wave long at f0,
    their inner ends are the balanced outputs, their outer ends the cold ends. Every end reaches the top layer
    through a via onto a pad in a clearance of the L1 ground, where the SMD parts sit; the ports are the gaps between
    these pads and the ground around them (where a part to ground would be soldered):
      1  unbalanced input            2  output +  (inner end of the left secondary)
      3  output -  (inner end of the right secondary)
      4  cold end of the left secondary          5  cold end of the right secondary
    A circuit grounds the cold ends (LO balun: pad shorted to the ground pour) or takes the IF from them through
    bypass capacitors (RF balun), so one 3D design serves both. The board edges are a perfect conductor, the via
    fence of a real board, and a lid 2 mm above the top layer closes the shield.
    """
    d = Design(name=name)
    p.designs.append(d)
    d.variables.update({
        "b": "0.762mm",           # L1-L2 and L3-L4, RO4350B cores
        "s": "0.102mm",           # L2-L3, RO4450F prepreg
        "hT": "2*b+s",            # board thickness (L4 at z = 0, L1 at hT)
        "Lc": "12.5mm",           # length of each coupled section (quarter wave near 3.1 GHz)
        "w": "0.3mm",             # width of the coupled strips (primary and secondaries)
        "g": "1mm",               # gap between the inner ends of the secondaries
        "lp": "1.2mm",            # primary beyond the secondaries at the input (room for its via)
        "rv": "0.15mm",           # via radius (0.3 mm drill)
        "pad": "0.5mm",           # square pads on L1
        "cl": "0.2mm",            # clearance between a pad and the L1 ground
        "xend": "2*Lc+g",         # right end of the secondaries
        "yb": "2.5mm",            # half width of the board (via fence at the edges)
    })
    x0, x1 = "-lp-1.5mm", "xend+1.5mm"
    size = dict(XSize=f"{x1}-({x0})", YSize="2*yb")
    d.add_object("CreateBox", name="Core_L3L4", material="Rogers RO4350B (tm)", Position=f"{x0},-yb,0", ZSize="b",
                 **size)
    d.add_object("CreateBox", name="Prepreg_L2L3", material=PREPREG, Position=f"{x0},-yb,b", ZSize="s", **size)
    d.add_object("CreateBox", name="Core_L1L2", material="Rogers RO4350B (tm)", Position=f"{x0},-yb,b+s", ZSize="b",
                 **size)
    # air above the top layer up to the lid of the shield (the outer faces are a perfect conductor)
    d.add_object("CreateBox", name="Air", material="air", Position=f"{x0},-yb,hT", ZSize="2mm", **size)
    gnd4 = d.add_object("CreateRectangle", name="Ground_L4", Position=f"{x0},-yb,0", Axis="Z", **size)
    gnd1 = d.add_object("CreateRectangle", name="Ground_L1", Position=f"{x0},-yb,hT", Axis="Z", **size)
    prim = d.add_object("CreateRectangle", name="Primary_L3", Position="-lp,-w/2,b", Axis="Z", XSize="lp+xend",
                        YSize="w")
    s1 = d.add_object("CreateRectangle", name="Secondary1_L2", Position="0,-w/2,b+s", Axis="Z", XSize="Lc", YSize="w")
    s2 = d.add_object("CreateRectangle", name="Secondary2_L2", Position="Lc+g,-w/2,b+s", Axis="Z", XSize="Lc",
                      YSize="w")
    # vias to the top layer: the input (from L3), the outputs and the cold ends (from L2)
    vias = [("In", "-lp+rv+0.1mm", "b"), ("Out1", "Lc-rv", "b+s"), ("Out2", "Lc+g+rv", "b+s"),
            ("Cold1", "rv", "b+s"), ("Cold2", "xend-rv", "b+s")]
    pads, conductors = [], [prim.name, s1.name, s2.name]
    for nm, xv, z in vias:
        v = d.add_object("CreateCylinder", name=f"Via_{nm}", material="pec", CenterPosition=f"{xv},0,{z}", Axis="Z",
                         Radius="rv", Height=f"hT-({z})")
        v.color = "#c08040"
        pd = d.add_object("CreateRectangle", name=f"Pad_{nm}", Position=f"{xv}-pad/2,-pad/2,hT", Axis="Z",
                          XSize="pad", YSize="pad")
        hole = d.add_object("CreateRectangle", name=f"Clear_{nm}", Position=f"{xv}-pad/2-cl,-pad/2-cl,hT", Axis="Z",
                            XSize="pad+2*cl", YSize="pad+2*cl")
        gnd1.history.append(Command.new("Subtract", Tools=hole.name))
        hole.consumed_by = gnd1.name
        pads.append((nm, xv, pd))
        conductors.append(pd.name)
    _assign(d.boundaries, schema.BOUNDARIES, "Perfect E", "PerfE_Ground", [gnd1.name, gnd4.name])
    _assign(d.boundaries, schema.BOUNDARIES, "Perfect E", "PerfE_Copper", conductors)
    order = {"In": 1, "Out1": 2, "Out2": 3, "Cold1": 4, "Cold2": 5}
    for nm, xv, _ in sorted(pads, key=lambda q: order[q[0]]):
        o = d.add_object("CreateRectangle", name=f"Port{order[nm]}Sheet", Position=f"{xv}-pad/2,pad/2,hT", Axis="Z",
                         XSize="pad", YSize="cl")
        _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", str(order[nm]), [o.name], PortNumber=order[nm],
                IntegrationLine=f"{xv},pad/2,hT;{xv},pad/2+cl,hT")
    _assign(d.mesh_ops, schema.MESH_OPS, "Length Based On Selection", "Length_Strips", [prim.name, s1.name, s2.name],
            MaxLength="0.3mm")
    s = Setup("Setup1")
    s.props.update(Frequency="4.5GHz", MaximumPasses="10", MaximumDeltaS="0.02")
    sw = Sweep("Sweep")
    sw.props.update(SweepType="Interpolating", Distribution="Linear Count", Start="0.05GHz", Stop="26GHz",
                    Count="520")
    s.sweeps.append(sw)
    d.setups.append(s)
    return d


def mixer_circuit(p: Project, name: str = "Mixer", balun: str = "Balun") -> Design:
    """The mixer as a circuit: the 3D balun twice (LO and RF), the HSMS-2829 ring between their outputs, the IF from
    the cold ends of the RF balun through two bypass capacitors, coplanar lines along the balun and an IF choke."""
    from hertzon.model import circuit as CK
    from hertzon.model.design import circuit_setup  # noqa: F401
    d = Design(name=name, kind="circuit")
    p.designs.append(d)
    d.variables.update({
        "Lpkg": "0.5nH",          # SOT-143 lead and pad of each ring node (estimate, not from the data sheet)
        "Cbyp": "10pF",           # 0402 C0G bypass capacitors at the cold ends of the RF balun
        "ESL": "0.2nH",           # their series inductance with the via right at the pad (0402)
        "Lif": "14.3mm",          # coplanar line from each cold end to the IF junction (half the balun)
        "hb": "1.626mm",          # board thickness (L1 to L4)
        "Lch": "47nH",            # 0603 wire-wound IF choke: keeps the RF and LO residue off the IF port
    })
    d.settings[CK.AUTO] = "False"
    comps = d.components

    def part(nm, t, props, x, y, rot=0, nets=(), mirror=False):
        c = CK.Component(nm, t, x, y, rot, {**CK.TYPES[t].props, **props}, mirror)
        comps.append(c)
        for k, ((px, py), net) in enumerate(zip(CK.pins(c), nets)):
            if net == "0":
                comps.append(CK.Component(f"GND_{nm}_{k}", "GND", px, py + 10, 0, {}))
            elif net:
                comps.append(CK.Component(f"N_{nm}_{k}", "LABEL", px, py, 0, {"Net": net}))
        return c
    em = {"Design": balun, "Solution": "Setup1 : Sweep", "Ports": "5", "PortNames": "1,2,3,4,5"}
    part("Sub1", "SUBST", {"Er": "3.66", "H": "hb", "T": "35um", "TanD": "0.0037"}, -400, -300)
    part("LIB1", "SPICELIB", {"File": "mixer/hsms282x.lib"}, -200, -300)
    # LO balun: input from the LO connector, cold ends grounded (pads shorted to the ground pour)
    part("LO", "PORT", {"Num": "1"}, -400, -60, nets=["lo"])
    part("BalunLO", "EM", em, -200, 0, nets=["lo", "A", "C", "0", "0"])
    # RF balun: cold ends to the IF
    part("RF", "PORT", {"Num": "2"}, -400, 240, nets=["rf"])
    part("BalunRF", "EM", em, -200, 300, nets=["rf", "B", "D", "x1", "x2"])
    # HSMS-2829 cross-over quad: four junctions as a ring a-b-c-d, LO across a-c, RF across b-d
    for nm, n1, n2, x, y in (("LA", "A", "a", 0, -20), ("LC", "C", "c", 0, 20), ("LB", "B", "b", 0, 280),
                             ("LD", "D", "d", 0, 320)):
        part(nm, "L", {"L": "Lpkg"}, x, y, nets=[n1, n2])
    dev = {"Kind": "Model card", "Model": "HSMS282X", "Pins": "a k"}
    for nm, an, ca, y in (("D1", "a", "b", 60), ("D2", "b", "c", 120), ("D3", "c", "d", 180), ("D4", "d", "a", 240)):
        part(nm, "DEVICE", dev, 200, y, nets=[an, ca])
    # IF: bypass capacitors at both cold ends, coplanar lines to the junction, the IF connector
    for k, x in ((1, -40), (2, 40)):
        part(f"Cb{k}", "SRLC", {"R": "0.1ohm", "L": "ESL", "C": "Cbyp"}, x - 380, 480, 90, nets=[f"x{k}", "0"])
        part(f"IF{k}", "CPWLIN", {"Subst": "Sub1", "W": "0.3mm", "S": "0.2mm", "L": "Lif", "Backed": "True"},
             x - 240, 440, nets=[f"x{k}", "ifn"])
    part("LCH", "L", {"L": "Lch"}, -60, 520, nets=["ifn", "ifp"])
    part("IF", "PORT", {"Num": "3"}, 100, 520, nets=["ifp"])
    # analyses: conversion loss and isolation over 1 to 5 GHz at +7 dBm LO (the balun covers about 1.25 to 4 GHz);
    # the LO drive at 3 GHz. Mixing order 5: for this ring within about 0.2 dB of order 9 (at most 0.7 dB; a
    # single diode needs 11 to 13), and its products (up to 5 x 5 GHz) stay inside the balun data (to 26 GHz;
    # block data are never extrapolated)
    s = Setup("Band")
    s.props.update(CircuitAnalysis="Mixer", LOPort="LO", RFPort="RF", IFPort="IF", LOPower="7", RFPower="-20",
                   RFStart="1GHz", RFStop="5GHz", RFPoints="17", IFFreq="100MHz", LOSide="Low", MixOrder="5")
    d.setups.append(s)
    s2 = Setup("LODrive")
    s2.props.update(CircuitAnalysis="Mixer", LOPort="LO", RFPort="RF", IFPort="IF", LOPower="-3:1:13",
                    RFPower="-20", RFStart="3GHz", RFStop="3GHz", RFPoints="1", IFFreq="100MHz", LOSide="Low",
                    MixOrder="5")
    d.setups.append(s2)
    d.notes = ("Broadband double-balanced diode mixer, RF and LO about 1.25 to 4 GHz, IF 100 MHz. Analyze the 3D "
               "design Balun first (a few minutes), then the setups Band and LODrive of this circuit (circuitRF). "
               "Results: mixer.csv in the results folder of each setup. Hertzon: circuit, linear network, balun "
               "(EMerge); circuitRF: two-tone harmonic balance; diode card: Broadcom data sheet HSMS-282x.")
    return d


def materials(p: Project) -> None:
    if not any(m.name == PREPREG for m in p.materials):
        p.materials.append(Material(PREPREG, permittivity="3.52", loss_tangent="0.004", color="#7a8f4a",
                                    description="Rogers RO4450F bondply (design Dk 3.52, Df 0.004)"))


def mixer_project() -> Project:
    p = Project("Mixer_DBM")
    p.designs.clear()
    materials(p)
    balun_design(p)
    mixer_circuit(p)
    return p


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "Mixer_DBM.hzn"
    proj = mixer_project()
    proj.save(str(out))
    print(out)
