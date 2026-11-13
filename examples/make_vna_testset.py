"""Create the example VNA_TestSet_2G4.hzn: the reflectometer test set of a two-port network analyzer, put
together on the board from blocks of the Design Wizard and connected by the router.

python examples/make_vna_testset.py  ->  examples/VNA_TestSet_2G4.hzn

- A Wilkinson divider splits the source: one half goes to the reference receiver R, the other half to the test
  port 1 of the device under test (DUT1).
- A 15 dB coupled-line coupler between the divider and DUT1 couples the wave reflected by the device to the
  receiver A; the port that the forward wave couples to is terminated with 50 ohm.
- A second coupler behind DUT2 couples the transmitted wave to the receiver B and passes it on to the 50 ohm
  termination of port 2 (TERM2); its isolated port is terminated with 50 ohm.

The microstrip coupler has a directivity of only a few dB (its even and odd modes travel at different speeds);
the calibration of the analyzer removes it, as for every real test set.

Each block is a 3D design of the wizard and simulates in the circuit with its circuit model. On the board
(Board Layout of the design "TestSet") the blocks are placed by hand; Connect laid all lines with 50 ohm and
circular bends, and Match Phase gave the three lines to the receivers R, A and B the same insertion phase at
2.45 GHz with meanders. Change a placement and press Connect and Match Phase again to see the router work.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.model import board as BD  # noqa: E402
from hertzon.model import circuit as CK  # noqa: E402
from hertzon.model import routing as RT  # noqa: E402
from hertzon.model import structures as ST  # noqa: E402
from hertzon.model.circuit import Component  # noqa: E402
from hertzon.model.design import Design, Project, Report, circuit_setup  # noqa: E402

F0 = 2.45e9
Z0 = 50.0
NAME = "VNA_TestSet_2G4"

# board placements in mm (x right, y up) and degrees
PLACE = {
    "SRC": (-50, 0, 0), "DIV": (-10, 0, 0), "R": (130, 40, 180),
    "CPL1": (50, -25, 0), "DUT1": (130, -5, 180), "A": (130, -60, 180),
    "CPL2": (50, -95, 180), "B": (130, -78, 180), "DUT2": (130, -115, 180), "TERM2": (-50, -115, 0),
}
# ports of the analyzer model: source, receivers, test ports, termination of port 2
PORTS = ["SRC", "R", "A", "B", "DUT1", "DUT2", "TERM2"]


def _blocks(p: Project) -> dict[str, tuple[Design, Design]]:
    """The wizard blocks: their 3D designs, circuit models and materials go into the project."""
    out = {}
    for key, vals in (("wilkinson", None), ("coupled", {"c": 15.0})):
        r = ST.create_structure(key, vals)
        p.designs += r.designs
        p.materials += [m for m in r.materials if m.name not in {x.name for x in p.materials}]
        d3 = next(d for d in r.designs if getattr(d, "kind", "3d") == "3d")
        dc = next(d for d in r.designs if getattr(d, "kind", "3d") == "circuit")
        out[key] = (d3, dc)
    return out


def schematic(blocks) -> Design:
    c = Design(name="TestSet", kind="circuit")
    st = circuit_setup()
    st.sweeps[0].props.update({"Start": "1.5GHz", "Stop": "3.5GHz", "Count": "201"})
    c.setups.append(st)
    sub = Component("Sub1", "SUBST", 0, -260, 0, dict(CK.TYPES["SUBST"].props))
    sub.props.update(Er="3.66", H="0.508mm", T="35um", TanD="0.0037")
    comps = [sub]

    def em(name, key, x, y):
        d3, dc = blocks[key]
        e = Component(name, "EM", x, y, 0, {"Design": d3.name, "Ports": str(len(d3.ports())),
                                             "Model": CK.MODEL_CIRCUIT, "CircuitModel": dc.name})
        comps.append(e)
        return e

    div = em("DIV", "wilkinson", 0, 0)
    c1 = em("CPL1", "coupled", 300, 120)
    c2 = em("CPL2", "coupled", 300, 360)
    ports = {}
    for k, n in enumerate(PORTS):
        x = -260 if n in ("SRC", "TERM2") else 620
        y = {"SRC": 0, "TERM2": 360, "R": -120, "A": 60, "DUT1": 180, "DUT2": 300, "B": 420}[n]
        ports[n] = Component(n, "PORT", x, y, 0 if x < 0 else 180, {"Num": str(k + 1), "Z0": f"{Z0:g}ohm"})
        comps.append(ports[n])
    loads = []
    for k, (x, y) in enumerate(((520, 240), (100, 480))):           # 50 ohm (0603) to a ground via
        r = Component(f"R_iso{k + 1}", "R", x, y, 90, {"R": "50ohm", "Footprint": "R_0603"})
        gx, gy = CK.pins(r)[1]
        v = Component(f"V_iso{k + 1}", "MVIA", gx, gy + 40, 0, {"Subst": "Sub1", "D": "0.3mm", "N": "2",
                                                               "P": "0.6mm"})
        comps += [r, v]
        loads.append((r, v))

    def net(comp, k, name):
        x, y = CK.pins(comp)[k]
        comps.append(Component(f"N{len(comps)}", "LABEL", x, y, 0, {"Net": name}))

    # coupler pins: 1 in, 2 through, 3 coupled (beside the input), 4 isolated
    net(ports["SRC"], 0, "src"), net(div, 0, "src")
    net(div, 1, "ref"), net(ports["R"], 0, "ref")
    net(div, 2, "fwd"), net(c1, 0, "fwd")
    net(c1, 1, "p1"), net(ports["DUT1"], 0, "p1")
    net(c1, 3, "a"), net(ports["A"], 0, "a")              # the wave reflected at DUT1 (it enters at pin 2)
    net(c1, 2, "iso1"), net(loads[0][0], 0, "iso1")
    net(c2, 3, "iso2"), net(loads[1][0], 0, "iso2")
    for k, (r, v) in enumerate(loads):
        net(r, 1, f"via{k + 1}"), net(v, 0, f"via{k + 1}")
    net(ports["DUT2"], 0, "p2"), net(c2, 0, "p2")
    net(c2, 1, "t2"), net(ports["TERM2"], 0, "t2")
    net(c2, 2, "b"), net(ports["B"], 0, "b")              # the wave transmitted through the DUT
    c.components = comps
    # port numbers: 1 SRC, 2 R, 3 A, 4 B, 5 DUT1, 6 DUT2, 7 TERM2
    c.reports = [
        Report("Test set paths", "Rectangular", "Setup1 : Sweep",
               ["dB(S(2,1))", "dB(S(5,1))", "dB(S(3,5))", "dB(S(4,6))", "dB(S(7,6))"]),
        Report("Directivity of the A coupler", "Rectangular", "Setup1 : Sweep",
               ["dB(S(3,5))", "dB(S(3,1)/S(5,1))"]),
        Report("Match", "Rectangular", "Setup1 : Sweep",
               ["dB(S(1,1))", "dB(S(5,5))", "dB(S(6,6))", "dB(S(2,2))", "dB(S(3,3))", "dB(S(4,4))"]),
    ]
    return c


def place(p: Project, c: Design) -> BD.Board:
    """The board with the parts where a designer would put them (before Connect)."""
    b, _ = BD.auto_place(p, c)
    for n, (x, y, r) in PLACE.items():
        b.placements[n] = BD.Placement(x, y, r)
    # the 50 ohm loads in line with the terminated coupler ports, 10 mm away, their vias 5 mm further
    parts, _, _ = BD.board_parts(p, c)

    def line_up(name, pin, rot, x, y):
        """Place the part so that its pin sits at (x, y)."""
        b.placements[name] = BD.Placement(0.0, 0.0, rot)
        (qx, qy), _, _ = RT.pin_world(parts, b, (name, pin))
        b.placements[name] = BD.Placement(x - qx, y - qy, rot)

    for k, (cpl, pin) in enumerate((("CPL1", 2), ("CPL2", 3))):
        (px, py), (_, dy), _ = RT.pin_world(parts, b, (cpl, pin))
        r, v = f"R_iso{k + 1}", f"V_iso{k + 1}"
        line_up(r, 0, 270 if dy < 0 else 90, px, py + dy * 10.0)
        (qx, qy), _, _ = RT.pin_world(parts, b, (r, 1))
        line_up(v, 0, 90 if dy < 0 else 270, qx, qy + dy * 5.0)
    return b


def vna_testset() -> Project:
    p = Project(name=NAME, designs=[])
    blocks = _blocks(p)
    c = schematic(blocks)
    p.designs.append(c)
    b = place(p, c)
    b, warnings = RT.connect(p, c, b, Z0, "Arc", f=F0)
    recv = [i for i, r in enumerate(b.routes) if {r.a[0], r.b[0]} & {"R", "A", "B"}]
    b, w2, _ = RT.match_phase(p, c, b, recv, F0)
    BD.store(c, b)
    return p


if __name__ == "__main__":
    out = Path(__file__).resolve().parent
    proj = vna_testset()
    proj.save(out / f"{proj.name}.hzn")
    print("wrote", out / f"{proj.name}.hzn")
