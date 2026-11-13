"""The board of a circuit design: where every part lies on the PCB (2D layout), the tracks drawn on it, and the
3D design built from it.

The schematic says what is connected to what; the board says where it lies. Every part that has a physical form
is a *board part* with its copper in its own coordinates (mm, y up, seen from the top) and *pins*: the points
where its connections attach, each with the direction a line leaves in. Lines and junctions take their size
from the schematic (W, L), R, L, C and devices their pads from the footprint (model/footprints.py).

A placement (x, y in mm, rotation in degrees counter-clockwise) puts a part on the board; parts of the same net
are connected where their pins meet or their copper touches, otherwise a *ratsnest* line shows the missing
connection. Tracks are drawn copper (DC routing, connections the schematic does not have as a line); they are
reported in the 3D design. The board is stored in the circuit design (setting "Board", JSON), so the schematic and
its board travel together.

auto_place() places the parts as the schematic is drawn: from the first power port, each part attached at the pin
it shares with a placed part, turned as its pins point in the schematic (lines straight on or round a corner, a
device so that its pad faces the line that reaches it). It is the starting point the user then rearranges.

layout_3d() builds the 3D design from the board: the substrate and ground plane under the board outline, the
copper of every part (lines and junctions with their dimension variables), lumped parts as Lumped RLC sheets in the
gap between their pads, vias, a Lumped Port from every pad of a device to the ground plane (named part_terminal_
padN), power ports and supply ports, and the port map the Board Circuit needs (circuit_layout.board_circuit).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import circuit as CK
from . import circuit_embed as CE
from . import footprints as FPS
from .kicad import KicadError

BOARD = "Board"                      # circuit design setting: the board (JSON)
NOT_ON_BOARD = ("GND", "LABEL", "SUBST", "SPICELIB")
SKIPPED = {"ATTEN": "an attenuator has no layout", "COAX": "a coaxial line has no planar layout",
           "DCFEED": "an ideal DC feed has no layout"}
GAP_MM = 1.0                         # gap of a lumped part without a footprint
TOL = 1e-4                           # mm: pins closer than this meet


# ---- geometry helpers ----------------------------------------------------------------------------------------
def rot_pt(p, deg: float):
    a = math.radians(deg)
    c, s = round(math.cos(a), 12), round(math.sin(a), 12)
    return c * p[0] - s * p[1], s * p[0] + c * p[1]


def rect(x0, y0, x1, y1) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _sdir(dx: int, dy: int) -> tuple[int, int]:
    """Physical axis direction of a schematic offset (schematic y points down)."""
    if abs(dx) >= abs(dy):
        return (1 if dx > 0 else -1 if dx < 0 else 0), 0
    return 0, (1 if dy < 0 else -1)


# ---- board parts ---------------------------------------------------------------------------------------------
@dataclass
class Pin:
    x: float                          # mm, part coordinates
    y: float
    d: tuple                          # direction a line leaves the pin in (unit axis; a slanted port of a 3D
    #                                   design block: any unit vector)
    w: float = 0.0                    # copper width at the pin (a line attaching there; a port's width)
    box: tuple | None = None          # the pad's bounding box (a line may attach at any of its edges)

    def edge(self, d):
        """The middle of the pad edge on side d (part coordinates) and the pad's extent across d; the pin itself
        where it is no pad."""
        if not self.box:
            return (self.x, self.y), self.w
        x0, y0, x1, y1 = self.box
        if d[0]:
            return ((x1 if d[0] > 0 else x0), (y0 + y1) / 2), y1 - y0
        return ((x0 + x1) / 2, (y1 if d[1] > 0 else y0)), x1 - x0


@dataclass
class Copper:
    pts: list[tuple[float, float]]    # polygon, part coordinates (mm)
    pin: int | None                   # the pin (schematic pin index) whose net it is; None: no net (open pad, the
    #                                   copper of a 3D design block)
    name: str = ""                    # object name suffix in 3D (MS1, C1_Pad1, ...)
    dims: dict[str, str] = field(default_factory=dict)   # for lines: the variables of its size (W, L)
    holes: list = field(default_factory=list)            # inner rings (a ring conductor)


@dataclass
class Lumped:
    a: tuple[float, float]            # the gap between the pads: from a to b (part coordinates), width w
    b: tuple[float, float]
    w: float


@dataclass
class PadPort:
    pin: int                          # schematic pin (terminal index)
    terminal: str
    pad: str
    at: tuple[float, float]           # middle of the pad's inner edge
    d: tuple[int, int]                # inward direction (the port sheet faces it)
    w: float                          # width of the edge


@dataclass
class BoardPart:
    name: str
    type: str
    copper: list[Copper] = field(default_factory=list)
    pins: list[Pin] = field(default_factory=list)          # one per schematic pin, in pin order
    extra: list[tuple[int, Pin]] = field(default_factory=list)   # more anchors of a pin (other pads of a terminal)
    lumped: Lumped | None = None
    vias: list[tuple[float, float, float, int]] = field(default_factory=list)   # x, y, diameter, pin
    shorts: list[tuple[float, float, tuple[int, int], float]] = field(default_factory=list)  # x, y, dir, w
    ports: list[PadPort] = field(default_factory=list)
    body: list[tuple[float, float]] = field(default_factory=list)   # outline (fabrication layer), part coordinates
    footprint: str = ""
    note: str = ""
    sheets: list = field(default_factory=list)            # Lumped RLC sheets of a 3D design block (outlines)
    design: str = ""                  # a 3D design block: the 3D design it stands for

    def outline(self):
        pts = [p for c in self.copper for p in c.pts] + self.body + [(q.x, q.y) for q in self.pins]
        return bbox(pts) if pts else (0, 0, 0, 0)


def _len(ev, text: str) -> float:
    return ev.length(text) * 1e3


def board_parts(project, circuit) -> tuple[dict[str, BoardPart], dict[str, list[int]], list[str]]:
    """The board part of every part of the circuit that has a physical form, the node of each pin, and what has
    no form (reported)."""
    comps, nodes, _, junctions = CK.netlist(circuit.components, circuit.wires,
                                            circuit.settings.get(CK.AUTO, "True") != "False")
    ev = circuit.evaluator(project)
    pdir = Path(project.path).parent if getattr(project, "path", None) else None
    arm_width = {}
    for j in junctions:
        names = {"MCORN": ("W",), "MSTEP": ("W1", "W2"), "MTEE": ("W1", "W2", "W3"),
                 "MCROSS": ("W1", "W2", "W3", "W4")}[j.type]
        for prop, (arm, _) in zip(names, j.arms if j.type != "MCORN" else j.arms[:1]):
            arm_width[(j.name, prop)] = arm
    by_name = {c.name: c for c in comps}
    subs = {c.name: c for c in comps if c.type == "SUBST"}

    def width(c, prop):
        arm = arm_width.get((c.name, prop))
        src = by_name[arm] if arm else c
        return _len(ev, src.props["W" if arm else prop]), f"{arm}_W" if arm else f"{c.name}_{prop}"

    parts: dict[str, BoardPart] = {}
    notes: list[str] = []
    for c in comps:
        t = c.type
        if t in NOT_ON_BOARD:
            continue
        if t in SKIPPED:
            notes.append(f"{c.name}: {SKIPPED[t]}; left out")
            continue
        bp = BoardPart(c.name, t)
        try:
            if t == "EM":
                _fill_em(bp, c, project)
            else:
                _fill(bp, c, ev, width, subs, pdir, notes)
        except (KicadError, KeyError, ValueError, CE.EmbedError) as ex:
            notes.append(f"{c.name}: {ex}; left out")
            continue
        _ground_vias(bp, nodes.get(c.name, []), notes)
        parts[c.name] = bp
    return parts, nodes, notes


VIA_D = 0.3                          # mm: vias of the ground pad of a shunt part (Via_D of the automatic layout)


def _ground_vias(bp: BoardPart, ns: list[int], notes: list[str]) -> None:
    """The ground pad of a shunt part on its footprint gets round vias across the pad, at its centre."""
    if not bp.lumped or bp.type not in ("R", "L", "C", "SRLC", "PRLC"):
        return
    along_x = abs(bp.lumped.a[1] - bp.lumped.b[1]) < 1e-9
    for k, net in enumerate(ns[:2]):
        if net != 0:
            continue
        cu = next((q for q in bp.copper if q.pin == k), None)
        if cu is None:
            continue
        x0, y0, x1, y1 = bbox(cu.pts)
        w = (y1 - y0) if along_x else (x1 - x0)
        nv = max(1, int((w - VIA_D) / (2 * VIA_D)) + 1)
        pitch = (w - VIA_D) / (nv - 1) if nv > 1 else 0.0
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for i in range(nv):
            o = pitch * (i - (nv - 1) / 2)
            bp.vias.append((cx, cy + o, VIA_D, k) if along_x else (cx + o, cy, VIA_D, k))
        notes.append(f"{bp.name}: its ground pad has {nv} via{'s' if nv > 1 else ''} of {VIA_D:g} mm to the ground "
                     "plane; the schematic has none, place a Via to Ground to model it there")


def _fill(bp: BoardPart, c, ev, width, subs, pdir, notes) -> None:
    t, p = c.type, c.props
    if t in ("MLIN", "SLIN", "CPWLIN", "TLIN"):
        if t == "TLIN":
            w, ln = _synth_tl(c, ev, subs)
            wv, lv = f"{c.name}_W", f"{c.name}_L"
            notes.append(f"{c.name}: the ideal line is laid out as microstrip of the same impedance and length")
        else:
            w, wv = width(c, "W")
            ln, lv = _len(ev, p["L"]), f"{c.name}_L"
        # the copper carries the net of pin 1 (copper_net); its far end is the other net, joined by the line
        bp.copper.append(Copper(rect(0, -w / 2, ln, w / 2), -1, c.name, {"W": wv, "L": lv}))
        bp.pins = [Pin(0, 0, (-1, 0), w), Pin(ln, 0, (1, 0), w)]
        return
    if t in ("MLOC", "MLSC", "TLOC", "TLSC"):
        if t in ("TLOC", "TLSC"):
            w, ln = _synth_tl(c, ev, subs)
        else:
            w, _ = width(c, "W")
            ln = _len(ev, p["L"])
        bp.copper.append(Copper(rect(0, -w / 2, ln, w / 2), 0, c.name, {"W": f"{c.name}_W", "L": f"{c.name}_L"}))
        bp.pins = [Pin(0, 0, (-1, 0), w)]
        if t in ("MLSC", "TLSC"):
            bp.shorts.append((ln, 0, (1, 0), w))
        return
    if t == "MCLIN":
        w, s, ln = _len(ev, p["W"]), _len(ev, p["S"]), _len(ev, p["L"])
        off = -(w + s)                                   # line B below line A (schematic pins 3, 4 lower)
        bp.copper += [Copper(rect(0, -w / 2, ln, w / 2), 0, f"{c.name}_A"),
                      Copper(rect(0, off - w / 2, ln, off + w / 2), 2, f"{c.name}_B")]
        bp.pins = [Pin(0, 0, (-1, 0), w), Pin(ln, 0, (1, 0), w), Pin(0, off, (-1, 0), w), Pin(ln, off, (1, 0), w)]
        bp.copper[0].pin, bp.copper[1].pin = -1, -2
        return
    if t == "MTEE":
        w1, _ = width(c, "W1")
        w2, _ = width(c, "W2")
        w3, _ = width(c, "W3")
        wm = max(w1, w2)
        bp.copper.append(Copper(rect(-w3 / 2, -wm / 2, w3 / 2, wm / 2), 0, c.name))
        bp.pins = [Pin(-w3 / 2, 0, (-1, 0), w1), Pin(w3 / 2, 0, (1, 0), w2), Pin(0, -wm / 2, (0, -1), w3)]
        return
    if t in ("MCORN", "MMBEND"):
        w, _ = width(c, "W")
        if t == "MCORN":
            bp.copper.append(Copper(rect(-w / 2, -w / 2, w / 2, w / 2), 0, c.name))
        else:                                  # 50 % miter: the outer corner (top right... here: left-down arms)
            bp.copper.append(Copper([(-w / 2, -w / 2), (w / 2, -w / 2), (-w / 2, w / 2)], 0, c.name))
        bp.pins = [Pin(-w / 2, 0, (-1, 0), w), Pin(0, -w / 2, (0, -1), w)]
        return
    if t == "MCROSS":
        ws = [width(c, f"W{k}")[0] for k in (1, 2, 3, 4)]
        ax, ay = max(ws[1], ws[3]), max(ws[0], ws[2])
        bp.copper.append(Copper(rect(-ax / 2, -ay / 2, ax / 2, ay / 2), 0, c.name))
        bp.pins = [Pin(-ax / 2, 0, (-1, 0), ws[0]), Pin(0, ay / 2, (0, 1), ws[1]), Pin(ax / 2, 0, (1, 0), ws[2]),
                   Pin(0, -ay / 2, (0, -1), ws[3])]
        return
    if t in ("MSTEP", "DCBLOCK"):
        bp.pins = [Pin(0, 0, (-1, 0)), Pin(0, 0, (1, 0))]
        if t == "DCBLOCK":
            notes.append(f"{c.name}: the DC block is a direct connection on the board")
        return
    if t == "MGAP":
        s = _len(ev, p["S"])
        bp.pins = [Pin(0, 0, (-1, 0)), Pin(s, 0, (1, 0))]
        return
    if t == "MVIA":
        d, n, pitch = _len(ev, p.get("D", "0.4mm")), int(round(ev.value(p.get("N", "1")))), _len(ev, p.get("P", "1mm"))
        for i in range(max(1, n)):
            bp.vias.append((0.0, pitch * (i - (n - 1) / 2), d, 0))
        # the pin half a diameter beside the vias, pointing over them: attached to a line end the vias sit just
        # inside the line
        bp.pins = [Pin(-d / 2, 0, (1, 0), d)]
        return
    if t in ("PORT", "VDC", "IDC"):
        bp.pins = [Pin(0, 0, (1, 0))] + ([Pin(0, 0, (-1, 0))] if t in ("VDC", "IDC") else [])
        return
    if t in ("R", "L", "C", "SRLC", "PRLC"):
        board = FPS.on_board(c, pdir)
        if board is None or board.check.errors:
            if board is not None:
                notes.append(f"{board.check.errors[0]}; laid out in a gap")
            g = _len(ev, p.get("Gap") or f"{GAP_MM}mm")
            w = 0.5
            bp.lumped = Lumped((0, 0), (g, 0), w)
            bp.pins = [Pin(0, 0, (-1, 0), w), Pin(g, 0, (1, 0), w)]
            return
        _pads(bp, c, board)
        bp.footprint = board.footprint.name
        # the gap between the pads of terminal 1 and 2
        boxes = []
        for t_ in ("1", "2"):
            qs = [q for n in board.pads.get(t_, []) for q in board.footprint.pads_of(n) if q.copper]
            boxes.append(bbox([v for q in qs for v in q.outline()]))
        (a0, a1, a2, a3), (b0, b1, b2, b3) = boxes
        if abs((a1 + a3) - (b1 + b3)) < 1e-6:             # in a row along x
            lo, hi = (a2, b0) if a2 <= b0 else (b2, a0)
            y = (a1 + a3) / 2
            bp.lumped = Lumped((lo, y), (hi, y), min(a3 - a1, b3 - b1))
        elif abs((a0 + a2) - (b0 + b2)) < 1e-6:           # along y
            lo, hi = (a3, b1) if a3 <= b1 else (b3, a1)
            x = (a0 + a2) / 2
            bp.lumped = Lumped((x, lo), (x, hi), min(a2 - a0, b2 - b0))
        else:
            raise ValueError(f"the two pads of {board.footprint.name} do not lie in a row")
        return
    if t in ("DEVICE", "SNP"):
        board = FPS.on_board(c, pdir)
        if board is None:
            raise ValueError("no footprint (set its Footprint)")
        if board.check.errors:
            raise ValueError(board.check.errors[0])
        notes += board.check.warnings
        _pads(bp, c, board, ports=True)
        bp.footprint = board.footprint.name
        return
    raise ValueError(f"no board form for a {t}")


_EM_CACHE: dict[str, tuple] = {}


def em_geometry(project, c) -> CE.EmGeometry:
    """The geometry of a 3D design block (circuit_embed.geometry, slanted ports accepted), kept while the 3D
    design and the block's port settings stay the same: building it takes the geometry kernel."""
    from dataclasses import asdict
    s = CE.source(project, c)
    key = json.dumps([asdict(s), dict(getattr(project, "variables", {}) or {}),
                      c.props.get("Ports", ""), c.props.get("PortNames", "")], sort_keys=True, default=str)
    hit = _EM_CACHE.get(s.name)
    if hit and hit[0] == key:
        return hit[1]
    g = CE.geometry(project, c, slanted=True)
    _EM_CACHE[s.name] = (key, g)
    return g


def em_frame(g: CE.EmGeometry) -> tuple[float, float]:
    """The point of the 3D design (mm) that is the origin of its board part: the middle of its extent."""
    x0, y0, x1, y1 = g.bbox
    return (x0 + x1) / 2 * 1e3, (y0 + y1) / 2 * 1e3


def _fill_em(bp: BoardPart, c, project) -> None:
    """A 3D design block: its copper seen from the top, a pin at every port (the middle of the port sheet,
    pointing out of the design along the sheet's normal, the sheet's width), its outline as the body."""
    g = em_geometry(project, c)
    cx, cy = em_frame(g)

    def mm(p):
        return p[0] * 1e3 - cx, p[1] * 1e3 - cy
    for i, (ring, holes) in enumerate(g.copper):
        bp.copper.append(Copper([mm(p) for p in ring], None, f"{c.name}_Cu{i + 1}",
                                holes=[[mm(p) for p in h] for h in holes]))
    bp.sheets = [[mm(p) for p in ring] for ring, _ in g.lumped]
    x0, y0, x1, y1 = g.bbox
    bp.body = rect(x0 * 1e3 - cx, y0 * 1e3 - cy, x1 * 1e3 - cx, y1 * 1e3 - cy)
    bp.design = g.design.name
    for pg in g.ports:
        nx, ny = pg.normal
        d = (int(round(nx)), int(round(ny))) if abs(abs(nx) + abs(ny) - 1) < 1e-9 else (float(nx), float(ny))
        bp.pins.append(Pin(*mm(pg.centre), d, pg.width * 1e3))


def _pads(bp: BoardPart, c, board: FPS.OnBoard, ports: bool = False) -> None:
    """Copper of the footprint's pads (centred on the courtyard), one pin per terminal at the outer edge of its
    outermost pad, the other pads of a terminal as extra anchors; with ports, a pad port at every pad."""
    fp = board.footprint
    x0, y0, x1, y1 = fp.bbox()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ts = FPS.terminals(c)
    # the body as drawn on the fabrication layer (the courtyard adds the assembly margin), else the pads
    pts = [q for poly in fp.fab for q in poly] or [q for pad in fp.pads if pad.copper for q in pad.outline(2)]
    if pts:
        bx0, by0, bx1, by1 = bbox(pts)
        bp.body = rect(bx0 - cx, by0 - cy, bx1 - cx, by1 - cy)
    else:
        bp.body = rect(x0 - cx, y0 - cy, x1 - cx, y1 - cy)
    anchors: dict[int, list[Pin]] = {}
    for q in fp.pads:
        if not q.copper or not q.number:
            continue
        t = board.terminal_of(q.number)
        k = ts.index(t) if t in ts else None
        pts = [(x - cx, y - cy) for x, y in q.outline(2)]
        bp.copper.append(Copper(pts, k, f"{c.name}_Pad{q.number}"))
        bx0, by0, bx1, by1 = bbox(pts)
        mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
        # outward: from the footprint centre towards the pad (the dominant axis)
        if abs(mx) < 1e-9 and abs(my) < 1e-9:
            out = (1, 0)
        elif abs(mx) >= abs(my):
            out = (1 if mx > 0 else -1, 0)
        else:
            out = (0, 1 if my > 0 else -1)
        if out[0]:
            edge, w = (bx1 if out[0] > 0 else bx0, my), by1 - by0
            inner = (bx0 if out[0] > 0 else bx1, my)
        else:
            edge, w = (mx, by1 if out[1] > 0 else by0), bx1 - bx0
            inner = (mx, by0 if out[1] > 0 else by1)
        if k is not None:
            anchors.setdefault(k, []).append(Pin(edge[0], edge[1], out, w, (bx0, by0, bx1, by1)))
            if ports:
                bp.ports.append(PadPort(k, t, q.number, inner, (-out[0], -out[1]), w))
        if q.plated_hole and k is not None:
            bp.vias.append((q.x - cx, q.y - cy, q.drill, k))
    bp.pins = []
    for k in range(len(ts)):
        lst = anchors.get(k, [])
        if not lst:
            bp.pins.append(Pin(0, 0, (1, 0)))
            continue
        main = max(lst, key=lambda p: abs(p.x) + abs(p.y))
        bp.pins.append(main)
        bp.extra += [(k, p) for p in lst if p is not main]


def _synth_tl(c, ev, subs) -> tuple[float, float]:
    from . import microstrip as MS
    sub = next(iter(subs.values())).props if subs else CK.TYPES["SUBST"].props
    er, h, t = ev.value(sub["Er"]), ev.length(sub["H"]), ev.length(sub["T"])
    z0, e, f = ev.value(c.props["Z0"]), ev.angle(c.props["E"]), ev.freq(c.props["F"])
    w = MS.synthesize(z0, h, er, t, f, builtin=True)
    lam = MS.guided_wavelength(w, h, er, t, f)
    return w * 1e3, lam * e / (2 * math.pi) * 1e3


# ---- the board -----------------------------------------------------------------------------------------------
@dataclass
class Placement:
    x: float = 0.0
    y: float = 0.0
    rot: float = 0.0                  # degrees, counter-clockwise, multiple of 90

    def world(self, p):
        q = rot_pt(p, self.rot)
        return q[0] + self.x, q[1] + self.y

    def wdir(self, d):
        q = rot_pt(d, self.rot)
        return int(round(q[0])), int(round(q[1]))

    def wvec(self, d):
        """A direction turned into the board, as it is (slanted directions stay slanted)."""
        q = rot_pt(d, self.rot)
        return round(q[0], 12), round(q[1], 12)


@dataclass
class Track:
    pts: list[tuple[float, float]]    # centre line, mm
    w: float = 0.3


@dataclass
class Route:
    """A line of one impedance from a pin to a pin, laid by the router (routing.py) and part of the circuit."""
    a: tuple                          # (part, pin index) where it starts
    b: tuple                          # (part, pin index) where it ends
    pts: list                         # centre line corners from pin a to pin b, mm
    w: float                          # width, mm
    radii: list = field(default_factory=list)   # per inner corner: bend radius of the centre line; 0: mitered
    wa: float = 0.0                   # width at pin a (a taper from it to w over la); 0: no taper
    wb: float = 0.0
    la: float = 0.0
    lb: float = 0.0
    z0: float = 50.0                  # impedance it was laid for (ohm)
    group: str = ""                   # equal-phase group
    offset: float = 0.0               # phase offset in its group (degrees)
    ga: list = field(default_factory=list)      # the pins joined to pin a on the board (one node with it)
    gb: list = field(default_factory=list)
    plain: list = field(default_factory=list)   # corners before a meander was put in (to match again)

    def to_dict(self) -> dict:
        r6 = lambda v: round(float(v), 6)  # noqa: E731
        return {"a": [self.a[0], int(self.a[1])], "b": [self.b[0], int(self.b[1])],
                "pts": [[r6(x), r6(y)] for x, y in self.pts], "w": r6(self.w), "radii": [r6(v) for v in self.radii],
                "wa": r6(self.wa), "wb": r6(self.wb), "la": r6(self.la), "lb": r6(self.lb), "z0": r6(self.z0),
                "group": self.group, "offset": r6(self.offset), "ga": [[p[0], int(p[1])] for p in self.ga],
                "gb": [[p[0], int(p[1])] for p in self.gb], "plain": [[r6(x), r6(y)] for x, y in self.plain]}

    @staticmethod
    def from_dict(d: dict) -> Route:
        pt = lambda lst: [(float(x), float(y)) for x, y in lst]  # noqa: E731
        return Route((d["a"][0], int(d["a"][1])), (d["b"][0], int(d["b"][1])), pt(d["pts"]), float(d["w"]),
                     [float(v) for v in d.get("radii", [])], float(d.get("wa", 0)), float(d.get("wb", 0)),
                     float(d.get("la", 0)), float(d.get("lb", 0)), float(d.get("z0", 50)), d.get("group", ""),
                     float(d.get("offset", 0)), [(p[0], int(p[1])) for p in d.get("ga", [])],
                     [(p[0], int(p[1])) for p in d.get("gb", [])], pt(d.get("plain", [])))


@dataclass
class Board:
    placements: dict[str, Placement] = field(default_factory=dict)
    tracks: list[Track] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    opts: dict = field(default_factory=dict)      # router settings: z0, style, f0, simulate

    def to_json(self) -> str:
        d = {"placements": {k: [round(float(v.x), 6), round(float(v.y), 6), float(v.rot)] for k, v in self.placements.items()},
             "tracks": [{"pts": [[round(x, 6), round(y, 6)] for x, y in t.pts], "w": t.w} for t in self.tracks]}
        if self.routes:
            d["routes"] = [r.to_dict() for r in self.routes]
        if self.opts:
            d["opts"] = self.opts
        return json.dumps(d)

    @staticmethod
    def from_json(text: str) -> Board:
        b = Board()
        if not text:
            return b
        try:
            d = json.loads(text)
        except ValueError:
            return b
        for k, v in (d.get("placements") or {}).items():
            b.placements[k] = Placement(float(v[0]), float(v[1]), float(v[2]))
        for t in d.get("tracks") or []:
            b.tracks.append(Track([(float(x), float(y)) for x, y in t["pts"]], float(t.get("w", 0.3))))
        for r in d.get("routes") or []:
            try:
                b.routes.append(Route.from_dict(r))
            except (KeyError, ValueError, TypeError, IndexError):
                pass
        b.opts = dict(d.get("opts") or {})
        return b


def load(circuit) -> Board | None:
    text = circuit.settings.get(BOARD, "")
    return Board.from_json(text) if text else None


def store(circuit, board: Board) -> None:
    circuit.settings[BOARD] = board.to_json()


def pin_nets(bp: BoardPart, nodes: dict[str, list[int]]) -> list[int]:
    return nodes.get(bp.name, [])


def copper_net(bp: BoardPart, cu: Copper, nodes) -> int | None:
    """Net of a copper polygon: a line's copper carries the net of its first pin (the nets of its two pins are
    joined by the line only electrically: on the board the copper is one piece, see anchors())."""
    ns = nodes.get(bp.name, [])
    if cu.pin is None:
        return None
    if cu.pin == -1:
        return ns[0] if ns else None
    if cu.pin == -2:
        return ns[2] if len(ns) > 2 else None
    return ns[cu.pin] if cu.pin < len(ns) else None


def anchors(parts: dict[str, BoardPart], board: Board, nodes) -> dict[int, list[tuple[str, float, float]]]:
    """Every pin of every placed part in world coordinates, grouped by net: (part, x, y). Ground (net 0) is left
    out: it is the ground plane."""
    out: dict[int, list] = {}
    for name, bp in parts.items():
        pl = board.placements.get(name)
        if pl is None:
            continue
        ns = nodes.get(name, [])
        pins = list(enumerate(bp.pins)) + list(bp.extra)
        for k, pin in pins:
            if k >= len(ns) or ns[k] == 0:
                continue
            x, y = pl.world((pin.x, pin.y))
            out.setdefault(ns[k], []).append((name, x, y))
    return out


def route_ok(parts, board: Board, r: Route) -> bool:
    """A route still ends on its two pins (the parts have not moved since it was laid)."""
    for ref, p in ((r.a, r.pts[0]), (r.b, r.pts[-1])):
        bp, pl = parts.get(ref[0]), board.placements.get(ref[0])
        if bp is None or pl is None or ref[1] >= len(bp.pins):
            return False
        x, y = pl.world((bp.pins[ref[1]].x, bp.pins[ref[1]].y))
        if math.hypot(x - p[0], y - p[1]) > 1e-3:
            return False
    return True


def _groups(parts, board: Board, nodes, with_routes: bool = True):
    """Per net: its pins on the board [(part, pin index, x, y)] and the union-find parent of each (pins that meet:
    same part, same point, touching copper, joined by a track, and with_routes by a route)."""
    shapes: dict[str, list] = {}
    for name, _net, poly in world_polys(parts, board, nodes):
        shapes.setdefault(name, []).append(poly.buffer(1e-3))
    out = {}
    for net, pts in pin_refs(parts, board, nodes).items():
        n = len(pts)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            parent[find(i)] = find(j)
        for i in range(n):
            for j in range(i):
                same_part = pts[i][0] == pts[j][0]
                close = abs(pts[i][2] - pts[j][2]) < 1e-3 and abs(pts[i][3] - pts[j][3]) < 1e-3
                touch = (_on_copper(pts[i][2:], shapes.get(pts[j][0], ())) or
                         _on_copper(pts[j][2:], shapes.get(pts[i][0], ())))
                if same_part or close or touch:
                    union(i, j)
        for tr in board.tracks:
            hit = [i for i, p in enumerate(pts) if any(_near_segment((p[2], p[3]), a, b, tr.w / 2 + 1e-3)
                                                       for a, b in zip(tr.pts, tr.pts[1:]))]
            for i in hit[1:]:
                union(i, hit[0])
        if with_routes:
            for r in board.routes:
                if not route_ok(parts, board, r):
                    continue
                ia = [i for i, p in enumerate(pts) if (p[0], p[1]) == tuple(r.a)]
                ib = [i for i, p in enumerate(pts) if (p[0], p[1]) == tuple(r.b)]
                if ia and ib:
                    union(ia[0], ib[0])
        out[net] = (pts, [find(i) for i in range(n)])
    return out


def pin_refs(parts, board: Board, nodes) -> dict[int, list[tuple[str, int, float, float]]]:
    """Like anchors(), with the pin index: (part, pin index, x, y) per net."""
    out: dict[int, list] = {}
    for name, bp in parts.items():
        pl = board.placements.get(name)
        if pl is None:
            continue
        ns = nodes.get(name, [])
        for k, pin in list(enumerate(bp.pins)) + list(bp.extra):
            if k >= len(ns) or ns[k] == 0:
                continue
            x, y = pl.world((pin.x, pin.y))
            out.setdefault(ns[k], []).append((name, k, x, y))
    return out


def pin_group(parts, board: Board, nodes, ref) -> list[tuple[str, int]]:
    """The pins that meet the pin ref on the board without routes (one node with it)."""
    ns = nodes.get(ref[0], [])
    if ref[1] >= len(ns):
        return [tuple(ref)]
    pts, roots = _groups(parts, board, nodes, with_routes=False).get(ns[ref[1]], ([], []))
    mine = [roots[i] for i, p in enumerate(pts) if (p[0], p[1]) == tuple(ref)]
    if not mine:
        return [tuple(ref)]
    return sorted({(p[0], p[1]) for i, p in enumerate(pts) if roots[i] == mine[0]})


def connections(parts, board: Board, nodes) -> list[tuple[int, tuple[str, int], tuple[str, int]]]:
    """The connections the board does not make yet: per net, the shortest that join the groups of pins that meet
    (Prim), as (net, (part, pin), (part, pin)); pins with a pin index of a main pin (not an extra pad) are
    preferred as ends, the router attaches there."""
    out = []
    for net, (pts, roots) in _groups(parts, board, nodes).items():
        groups: dict[int, list[int]] = {}
        for i, r in enumerate(roots):
            groups.setdefault(r, []).append(i)
        gl = list(groups.values())
        if len(gl) < 2:
            continue
        done = [gl[0]]
        rest = gl[1:]
        while rest:
            best = None
            for gi, g in enumerate(rest):
                for a in (i for d_ in done for i in d_):
                    for b in g:
                        dd = math.hypot(pts[a][2] - pts[b][2], pts[a][3] - pts[b][3])
                        if best is None or dd < best[0]:
                            best = (dd, gi, a, b)
            _, gi, a, b = best
            out.append((net, (pts[a][0], pts[a][1]), (pts[b][0], pts[b][1]), (pts[a][2], pts[a][3]),
                        (pts[b][2], pts[b][3])))
            done.append(rest.pop(gi))
    return [(net, a, b) for net, a, b, _, _ in out]


def ratsnest(parts, board: Board, nodes) -> list[tuple[int, tuple[float, float], tuple[float, float]]]:
    """The connections the board does not make yet (connections()) as lines (net, from, to)."""
    out = []
    for net, a, b in connections(parts, board, nodes):
        pa = board.placements[a[0]].world((parts[a[0]].pins[a[1]].x, parts[a[0]].pins[a[1]].y)) \
            if a[1] < len(parts[a[0]].pins) else None
        pb = board.placements[b[0]].world((parts[b[0]].pins[b[1]].x, parts[b[0]].pins[b[1]].y)) \
            if b[1] < len(parts[b[0]].pins) else None
        if pa is not None and pb is not None:
            out.append((net, pa, pb))
    return out


def _on_copper(p, polys) -> bool:
    from shapely.geometry import Point
    q = Point(p)
    return any(poly.contains(q) for poly in polys)


def _near_segment(p, a, b, r) -> bool:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy) <= r


def copper_nets(bp: BoardPart, cu: Copper, nodes) -> set[int]:
    """Every net a copper polygon touches: both ends of a line (its copper is one piece from one net to the
    other), the net of its pin otherwise."""
    ns = nodes.get(bp.name, [])
    if cu.pin == -1:
        return set(ns[:2])
    if cu.pin == -2:
        return set(ns[2:4])
    n = copper_net(bp, cu, nodes)
    return set() if n is None else {n}


def world_copper(parts, board: Board, nodes) -> list[tuple[str, int | None, list[tuple[float, float]]]]:
    """Every copper polygon of the placed parts in world coordinates with its net: (part, net, points)."""
    out = []
    for name, bp in parts.items():
        pl = board.placements.get(name)
        if pl is None:
            continue
        for cu in bp.copper:
            out.append((name, copper_net(bp, cu, nodes), [pl.world(p) for p in cu.pts]))
    return out


def world_polys(parts, board: Board, nodes) -> list[tuple[str, int | None, object]]:
    """Every copper polygon of the placed parts as a shapely polygon (with its holes) in world coordinates:
    (part, net, polygon)."""
    from shapely.geometry import Polygon
    out = []
    for name, bp in parts.items():
        pl = board.placements.get(name)
        if pl is None:
            continue
        for cu in bp.copper:
            if len(cu.pts) < 3:
                continue
            poly = Polygon([pl.world(p) for p in cu.pts], [[pl.world(p) for p in h] for h in cu.holes if len(h) >= 3])
            out.append((name, copper_net(bp, cu, nodes), poly.buffer(0)))
    return out


def route_name(i: int) -> str:
    return f"Route{i + 1}"


def overlaps(parts, board: Board, nodes) -> list[tuple[str, str]]:
    """Pairs of parts (and routes, Route1, ...) whose copper of different nets lies on each other (shapely); a
    route may touch the two parts it connects."""
    from shapely.geometry import Polygon
    from . import routing as RT
    polys = []                        # (part, the nets its copper touches, polygon): a line joins both its nets
    for name, bp in parts.items():
        pl = board.placements.get(name)
        if pl is None:
            continue
        for cu in bp.copper:
            if len(cu.pts) < 3:
                continue
            poly = Polygon([pl.world(p) for p in cu.pts], [[pl.world(p) for p in h] for h in cu.holes if len(h) >= 3])
            polys.append((name, copper_nets(bp, cu, nodes), poly.buffer(0)))
    ends = {}
    for i, r in enumerate(board.routes):
        ns = nodes.get(r.a[0], [])
        net = ns[r.a[1]] if r.a[1] < len(ns) else None
        polys.append((route_name(i), set() if net is None else {net}, RT.polygon(r)))
        ends[route_name(i)] = {r.a[0], r.b[0]}
    out = set()
    for i, (an, anets, ap) in enumerate(polys):
        for bn, bnets, bpoly in polys[:i]:
            if an == bn or not anets or not bnets or anets & bnets:
                continue
            if bn in ends.get(an, ()) or an in ends.get(bn, ()):
                continue
            if ap.intersection(bpoly).area > 1e-6:
                out.add(tuple(sorted((an, bn))))
    return sorted(out)


# ---- automatic placement -------------------------------------------------------------------------------------
def auto_place(project, circuit) -> tuple[Board, list[str]]:
    """Place the parts as the schematic is drawn (see the module notes). Parts the walk does not reach are put in
    a row below the board."""
    parts, nodes, notes = board_parts(project, circuit)
    comps = {c.name: c for c in CK.netlist(circuit.components, circuit.wires,
                                            circuit.settings.get(CK.AUTO, "True") != "False")[0]}
    board = Board()
    users: dict[int, list[tuple[str, int]]] = {}
    for name in parts:
        for k, n in enumerate(nodes.get(name, [])):
            if n != 0:
                users.setdefault(n, []).append((name, k))
    ports = [c.name for c in CK.port_order(list(comps.values())) if c.name in parts]
    queue: list[str] = []
    parent: dict[str, str] = {}
    if ports:
        board.placements[ports[0]] = Placement(0, 0, 0)
        queue.append(ports[0])
    while True:
        while queue:
            name = queue.pop(0)
            bp, pl = parts[name], board.placements[name]
            ns = nodes.get(name, [])
            for k, pin in list(enumerate(bp.pins)):
                if k >= len(ns) or ns[k] == 0:
                    continue
                ax, ay = pl.world((pin.x, pin.y))
                d_out = pl.wdir(pin.d)
                for other, j in users.get(ns[k], []):
                    if other in board.placements:
                        continue
                    ob = parts[other]
                    if pin.box:          # a pad: the part attaches at the pad edge facing the way it goes
                        sd = _schem_dir(ob, j, comps[other])
                        if sd is not None:
                            ld = (int(round(rot_pt(sd, -pl.rot)[0])), int(round(rot_pt(sd, -pl.rot)[1])))
                            (ex, ey), _ = pin.edge(ld)
                            ax, ay = pl.world((ex, ey))
                            d_out = sd
                    q = _choose_rot(ob, j, comps[other], d_out)
                    p0 = Placement(0, 0, q)
                    px, py = p0.world((ob.pins[j].x, ob.pins[j].y))
                    board.placements[other] = Placement(ax - px, ay - py, q)
                    parent[other] = name
                    queue.append(other)
        rest = [n for n in parts if n not in board.placements]
        if not rest:
            break
        # a part the walk did not reach (another island of the circuit): below what is placed, then walk on
        ys = [y for nm, pl in board.placements.items() for y in (pl.world(p)[1] for p in
                                                                   _corners(parts[nm]))] or [0.0]
        board.placements[rest[0]] = Placement(0, min(ys) - 5.0, 0)
        queue.append(rest[0])
    _spread_blocks(parts, board, parent)
    return board, notes


def _spread_blocks(parts, board: Board, parent: dict[str, str], gap: float = 5.0) -> None:
    """3D design blocks the walk put on each other (two blocks at the outputs of a divider) are moved apart,
    across the way they were attached, with what hangs on them; Connect joins them again."""
    from shapely.geometry import Polygon

    def body(n):
        bp, pl = parts[n], board.placements[n]
        pts = bp.body or [(x, y) for c in bp.copper for x, y in c.pts]
        return Polygon([pl.world(p) for p in pts]).envelope if len(pts) >= 3 else None

    def subtree(n):
        out = [n]
        for k, v in parent.items():
            if v == n:
                out += subtree(k)
        return out
    order = list(board.placements)
    placed: list[str] = []
    for n in order:
        if parts[n].type != "EM":
            placed.append(n)
            continue
        for k in range(40):
            mine = body(n)
            hit = [m for m in placed if m != n and parent.get(n) != m and body(m) is not None and mine is not None
                   and mine.buffer(gap / 2).intersects(body(m).buffer(gap / 2))]
            if not hit:
                break
            # across the attaching direction: away from what it lies on
            pl = board.placements[n]
            ox = sum(body(m).centroid.x for m in hit) / len(hit)
            oy = sum(body(m).centroid.y for m in hit) / len(hit)
            c = mine.centroid
            vertical = abs(pl.wdir((1, 0))[0]) == 1
            step = (0.0, gap if c.y >= oy else -gap) if vertical else (gap if c.x >= ox else -gap, 0.0)
            if k == 0 and abs(c.y - oy) < 1e-9 and abs(c.x - ox) < 1e-9:
                step = (0.0, gap) if vertical else (gap, 0.0)
            if k == 0 and n in parent:      # and once away from the part it hangs on: room for the bends
                px_, py_ = board.placements[parent[n]].x, board.placements[parent[n]].y
                along = (3 * gap if pl.x >= px_ else -3 * gap, 0.0) if vertical else \
                    (0.0, 3 * gap if pl.y >= py_ else -3 * gap)
                step = (step[0] + along[0], step[1] + along[1])
            for m in subtree(n):
                board.placements[m].x += step[0]
                board.placements[m].y += step[1]
        placed.append(n)


def _corners(bp: BoardPart):
    x0, y0, x1, y1 = bp.outline()
    return [(x0, y0), (x1, y1)]


def _schem_dir(bp: BoardPart, j: int, comp):
    """The direction a two-pin part (or a stub) runs in from its pin j in the schematic; None for other parts."""
    pins = CK.pins(comp)
    if len(bp.pins) == 2 and len(pins) == 2 and comp.type not in ("VDC", "IDC"):
        return _sdir(pins[1 - j][0] - pins[j][0], pins[1 - j][1] - pins[j][1])
    if len(bp.pins) == 1 and comp.type in ("MLOC", "MLSC", "TLOC", "TLSC"):
        return _sdir(comp.x - pins[0][0], comp.y - pins[0][1])
    return None


def _choose_rot(bp: BoardPart, j: int, comp, d_out) -> float:
    """Rotation of a part attached at its pin j to a pin that points d_out: two-pin parts follow the direction they
    have in the schematic (the line goes on from the pin into the part); other parts turn so that pin j faces
    back to d_out."""
    want = (-d_out[0], -d_out[1])                       # the attached pin must point back
    pins = CK.pins(comp)
    if len(bp.pins) == 2 and len(pins) == 2 and comp.type not in ("VDC", "IDC"):
        sd = _sdir(pins[1 - j][0] - pins[j][0], pins[1 - j][1] - pins[j][1])   # from pin j into the part
        want_in = sd
        for q in (0, 90, 180, 270):
            d = rot_pt(bp.pins[j].d, q)
            if (round(-d[0]), round(-d[1])) == want_in:
                return float(q)
    if len(bp.pins) == 1 and comp.type in ("MLOC", "MLSC", "TLOC", "TLSC"):
        sd = _sdir(comp.x - pins[0][0], comp.y - pins[0][1])
        for q in (0, 90, 180, 270):
            d = rot_pt(bp.pins[0].d, q)
            if (round(-d[0]), round(-d[1])) == sd:
                return float(q)
    best, bq = None, 0.0
    for q in (0, 90, 180, 270):
        d = rot_pt(bp.pins[j].d, q)
        s = d[0] * want[0] + d[1] * want[1]
        if best is None or s > best + 1e-9:
            best, bq = s, float(q)
    return bq


def complete(project, circuit, board: Board) -> tuple[Board, list[str]]:
    """The board with every part placed: parts added to the schematic since are placed next to what is there
    (by an automatic placement of them), parts removed from it are dropped."""
    parts, _nodes, notes = board_parts(project, circuit)
    missing = [n for n in parts if n not in board.placements]
    for n in list(board.placements):
        if n not in parts:
            del board.placements[n]
    if missing:
        auto, _ = auto_place(project, circuit)
        xs = [pl.x for pl in board.placements.values()] or [0.0]
        ys = [pl.y for pl in board.placements.values()] or [0.0]
        for i, n in enumerate(missing):
            pl = auto.placements.get(n, Placement())
            board.placements[n] = Placement(max(xs) + 5.0 + 3.0 * i, min(ys) - 5.0, pl.rot)
    return board, notes


# ---- the 3D design of the board ------------------------------------------------------------------------------
def _embed_em(d, project, circuit, parts, board: Board, ems, ground, warnings, h: float) -> None:
    """Copy the 3D design of every block on the board into the layout d, turned and moved as it is placed; its
    ports go (the board connects the block: lines, routes or a Power Port at the pin), the layout's ground plane
    gets a hole where the block has its own ground."""
    from .design import Command
    comps = {c.name: c for c in circuit.components}
    for name in ems:
        c, pl = comps[name], board.placements[name]
        g = em_geometry(project, c)
        cx, cy = em_frame(g)
        q = int(round(pl.rot / 90.0)) % 4
        rx, ry = CE.turn((cx, cy), q)
        zg = min((pg.zmin for pg in g.ports), default=0.0)
        move = f"{_mm(pl.x - rx)},{_mm(pl.y - ry)},{_mm(-zg * 1e3)}"
        for pg in g.ports:
            if pg.kind == "Lumped Port" and abs((pg.zmax - pg.zmin) - h) > 0.01 * h:
                warnings.append(f"{name}: the port {pg.name} is {(pg.zmax - pg.zmin) * 1e3:.4g} mm high, the "
                                f"board substrate {h * 1e3:.4g} mm")
        _, w = CE.embed(d, g, c, q, move, {})
        warnings += w
        for x0, y0, x1, y1 in g.grounds:
            ax, ay = CE.turn((x0 * 1e3, y0 * 1e3), q)
            bx, by = CE.turn((x1 * 1e3, y1 * 1e3), q)
            hole = d.add_object("CreateRectangle", name=d.unique_name(f"{name}_GroundCut"),
                                Position=f"{_mm(min(ax, bx) + pl.x - rx)},{_mm(min(ay, by) + pl.y - ry)},0", Axis="Z",
                                XSize=_mm(abs(bx - ax)), YSize=_mm(abs(by - ay)))
            hole.consumed_by = ground.name
            ground.history.append(Command.new("Subtract", Tools=hole.name, KeepOriginals="False"))



def _mm(v: float) -> str:
    return f"{v:.6g}mm"


def layout_3d(project, circuit, board: Board, name: str | None = None):
    """The 3D design of the board (see the module notes). Returns (design, warnings)."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union

    from . import schema
    from .circuit_layout import DIMS, LINK, PORT_MAP, VARS, LayoutError
    from .design import Assignment, Command, Design, Setup, Sweep
    from .materials import Material

    parts, nodes, warnings = board_parts(project, circuit)
    board, more = complete(project, circuit, board)
    warnings += [w for w in more if w not in warnings]
    ev = circuit.evaluator(project)
    subs = [c for c in circuit.components if c.type == "SUBST"]
    if not subs:
        raise LayoutError("the circuit has no substrate: place a Substrate (Transmission Lines) to define the board")
    sub = subs[0].props
    sname = subs[0].name
    er, tand, cond = ev.value(sub["Er"]), ev.value(sub["TanD"]), ev.value(sub["Cond"])
    d = Design(name=name or f"{circuit.name}_Layout", units=circuit.units)
    d.variables.update(dict(circuit.variables))
    H, T = f"{sname}_H", f"{sname}_T"
    d.variables[H], d.variables[T] = sub["H"], sub["T"]
    zt = H
    dims: dict[str, list[str]] = {}
    for a, b in overlaps(parts, board, nodes):
        warnings.append(f"{a} and {b} lie on each other on the board (different nets)")
    for net, p0, p1 in ratsnest(parts, board, nodes):
        warnings.append(f"net {net}: not connected on the board from ({p0[0]:.3g}, {p0[1]:.3g}) to "
                        f"({p1[0]:.3g}, {p1[1]:.3g}) mm (ratsnest); draw a track or move the parts")

    # copper: one entry per polygon with its shapely form, joined later where they touch
    from . import routing as RT
    pieces: list[dict] = []
    for pname, bp in parts.items():
        pl = board.placements[pname]
        if bp.type == "EM":            # copied from its 3D design below
            continue
        for cu in bp.copper:
            wpts = [pl.world(p) for p in cu.pts]
            pieces.append({"name": cu.name or pname, "pts": wpts, "dims": cu.dims, "part": pname,
                           "start": pl.world((0, 0)), "u": pl.wdir((1, 0)), "poly": Polygon(wpts).buffer(0)})
    for i, tr in enumerate(board.tracks):
        geo = LineString(tr.pts).buffer(tr.w / 2, cap_style=3, join_style=2)
        pieces.append({"name": f"Track{i + 1}", "pts": list(geo.exterior.coords)[:-1], "dims": {}, "part": "",
                       "poly": geo})
    for i, r in enumerate(board.routes):
        geo = RT.polygon(r)
        for k, g in enumerate(getattr(geo, "geoms", [geo])):
            if g.is_empty:
                continue
            nm = route_name(i) if k == 0 else f"{route_name(i)}_{k + 1}"
            pieces.append({"name": nm, "pts": [tuple(p) for p in list(g.exterior.coords)[:-1]], "dims": {},
                           "part": "", "poly": g})
        if not route_ok(parts, board, r):
            warnings.append(f"{route_name(i)} ({r.a[0]}–{r.b[0]}) no longer ends on its pins (a part moved): "
                            "connect again")
    if board.tracks:
        warnings.append(f"{len(board.tracks)} track(s) drawn on the board are not in the schematic")
    ems = [n for n, bp in parts.items() if bp.type == "EM"]
    if not pieces and not ems:
        raise LayoutError("nothing on the board")
    outlines = [p["poly"] for p in pieces] + [Polygon([board.placements[n].world(q) for q in parts[n].body])
                                              for n in ems]
    allc = unary_union(outlines)
    x0, y0, x1, y1 = allc.bounds
    margin = 3.0
    # power ports on the board edge where they are the outermost copper on their side
    edges = {"x-": x0 - margin, "x+": x1 + margin, "y-": y0 - margin, "y+": y1 + margin}
    for pname, bp in parts.items():
        if bp.type != "PORT":
            continue
        pl = board.placements[pname]
        ax, ay = pl.world((0, 0))
        dx, dy = pl.wdir(bp.pins[0].d)
        if dx and abs(ax - (x0 if dx > 0 else x1)) < 1e-6:
            edges["x-" if dx > 0 else "x+"] = ax
        if dy and abs(ay - (y0 if dy > 0 else y1)) < 1e-6:
            edges["y-" if dy > 0 else "y+"] = ay
    bx0, bx1, by0, by1 = edges["x-"], edges["x+"], edges["y-"], edges["y+"]
    mat = f"{sname}_Er{er:g}"
    d.add_object("CreateBox", name="Substrate", material=mat, Position=f"{_mm(bx0)},{_mm(by0)},0",
                 XSize=_mm(bx1 - bx0), YSize=_mm(by1 - by0), ZSize=H)
    ground = d.add_object("CreateRectangle", name="Ground", Position=f"{_mm(bx0)},{_mm(by0)},0", Axis="Z",
                          XSize=_mm(bx1 - bx0), YSize=_mm(by1 - by0))
    d.settings["AutoOpen"] = "Auto Open - Filter"
    d.settings["FilterAirMargin"] = "10mm"
    d.settings[LINK] = circuit.name

    objs = []
    for pc in pieces:
        nm = d.unique_name(pc["name"])
        bx = bbox(pc["pts"])
        is_rect = len(pc["pts"]) == 4 and all(abs(px - bx[0]) < 1e-9 or abs(px - bx[2]) < 1e-9 for px, _ in pc["pts"])
        if pc["dims"] and is_rect:
            wv, lv = pc["dims"]["W"], pc["dims"]["L"]
            for v in (wv, lv):
                src = next((c for c in circuit.components if f"{c.name}_W" == v or f"{c.name}_L" == v), None)
                if src is not None:
                    d.variables[v] = src.props[v.rsplit("_", 1)[1]]
            sx_, sy_ = pc["start"]
            u = pc["u"]
            if u[0]:
                pos = (f"{_mm(sx_)}-{lv}" if u[0] < 0 else _mm(sx_), f"{_mm(sy_)}-0.5*{wv}")
                xs, ys = lv, wv
            else:
                pos = (f"{_mm(sx_)}-0.5*{wv}", f"{_mm(sy_)}-{lv}" if u[1] < 0 else _mm(sy_))
                xs, ys = wv, lv
            o = d.add_object("CreateRectangle", name=nm, Position=f"{pos[0]},{pos[1]},{zt}", Axis="Z",
                             XSize=xs, YSize=ys)
            dims.setdefault(lv, [nm, "XSize" if u[0] else "YSize"])
            dims.setdefault(wv, [nm, "YSize" if u[0] else "XSize"])
        elif is_rect:
            o = d.add_object("CreateRectangle", name=nm, Position=f"{_mm(bx[0])},{_mm(bx[1])},{zt}", Axis="Z",
                             XSize=_mm(bx[2] - bx[0]), YSize=_mm(bx[3] - bx[1]))
        else:
            pts = pc["pts"]
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))
            if area < 0:
                pts = pts[::-1]
            o = d.add_object("CreatePolyline", name=nm, material="vacuum",
                             Points=";".join(f"{_mm(x)},{_mm(y)},{zt}" for x, y in pts), Closed="True",
                             Covered="True")
        objs.append(o)
    # join the copper that touches
    n = len(pieces)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i):
            if pieces[i]["poly"].buffer(1e-6).intersects(pieces[j]["poly"]):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    conductors = []
    for g in groups.values():
        main = objs[g[0]]
        for j in g[1:]:
            main.history.append(Command.new("Unite", Tools=objs[j].name))
            objs[j].consumed_by = main.name
        conductors.append(main.name)

    def vertical(nm, x, y, dd, w):
        if dd[0]:
            return d.add_object("CreateRectangle", name=nm, Position=f"{_mm(x)},{_mm(y - w / 2)},0", Axis="X",
                                YSize=_mm(w), ZSize=zt)
        return d.add_object("CreateRectangle", name=nm, Position=f"{_mm(x - w / 2)},{_mm(y)},0", Axis="Y",
                            XSize=_mm(w), ZSize=zt)

    def assign(lst, table, kind, nm, objects, **props):
        q = schema.defaults(table[kind])
        q.update(props)
        lst.append(Assignment(nm, kind, q, objects=list(objects)))

    shorts, rlc = [], []
    from .circuit_layout import _rlc
    comps = {c.name: c for c in CK.netlist(circuit.components, circuit.wires,
                                            circuit.settings.get(CK.AUTO, "True") != "False")[0]}
    for pname, bp in parts.items():
        pl = board.placements[pname]
        for k, (vx, vy, dv, _) in enumerate(bp.vias):
            wx, wy = pl.world((vx, vy))
            o = d.add_object("CreateCylinder", name=d.unique_name(f"{pname}_Via{k + 1}" if len(bp.vias) > 1 else pname),
                             material="pec", CenterPosition=f"{_mm(wx)},{_mm(wy)},0", Axis="Z",
                             Radius=_mm(dv / 2), Height=zt)
            o.color = "#c08040"
        for sx_, sy_, dd, w in bp.shorts:
            wx, wy = pl.world((sx_, sy_))
            shorts.append(vertical(d.unique_name(f"{pname}_Short"), wx, wy, pl.wdir(dd), w).name)
        if bp.lumped:
            a, b = pl.world(bp.lumped.a), pl.world(bp.lumped.b)
            w = bp.lumped.w
            if abs(a[1] - b[1]) < 1e-9:
                o = d.add_object("CreateRectangle", name=d.unique_name(pname),
                                 Position=f"{_mm(min(a[0], b[0]))},{_mm(a[1] - w / 2)},{zt}", Axis="Z",
                                 XSize=_mm(abs(b[0] - a[0])), YSize=_mm(w))
            else:
                o = d.add_object("CreateRectangle", name=d.unique_name(pname),
                                 Position=f"{_mm(a[0] - w / 2)},{_mm(min(a[1], b[1]))},{zt}", Axis="Z",
                                 XSize=_mm(w), YSize=_mm(abs(b[1] - a[1])))
            line = f"{_mm(a[0])},{_mm(a[1])},{zt};{_mm(b[0])},{_mm(b[1])},{zt}"
            assign(d.boundaries, schema.BOUNDARIES, "Lumped RLC", pname, [o.name], IntegrationLine=line,
                   Component=f"{pname} of {circuit.name}", **_rlc(comps[pname]))
            rlc.append(o.name)
    cu = {"UseMaterial": "False", "Conductivity": f"{cond:g}", "UseThickness": "True", "Thickness": T,
          "TwoSided": "True"}
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Traces", conductors + shorts, **cu)
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Ground", [ground.name], **cu)

    # ports: power ports, then a port at every pad of a device, then the DC sources
    port_map: dict[str, dict] = {}
    anch = anchors(parts, board, nodes)

    def width_at(x, y, net, own):
        ws = [p.w for nm, bp in parts.items() if nm != own
              for k, p in list(enumerate(bp.pins)) + list(bp.extra)
              if k < len(nodes.get(nm, [])) and nodes[nm][k] == net and p.w > 0
              and math.hypot(board.placements[nm].world((p.x, p.y))[0] - x,
                             board.placements[nm].world((p.x, p.y))[1] - y) < 1e-3]
        return max(ws) if ws else 1.0
    _ = anch
    num = 1
    order = [c.name for c in CK.port_order(list(comps.values())) if c.name in parts]
    for pname in order:
        bp, pl = parts[pname], board.placements[pname]
        x, y = pl.world((0, 0))
        dd = pl.wdir(bp.pins[0].d)
        net = nodes[pname][0]
        o = vertical(d.unique_name(f"Port{num}"), x, y, dd, width_at(x, y, net, pname))
        assign(d.excitations, schema.EXCITATIONS, "Lumped Port", str(num), [o.name], PortNumber=str(num),
               Impedance=comps[pname].props.get("Z0", "50ohm") or "50ohm",
               IntegrationLine=f"{_mm(x)},{_mm(y)},0;{_mm(x)},{_mm(y)},{zt}")
        port_map[str(num)] = {"part": pname, "terminal": "", "pad": ""}
        num += 1
    for pname, bp in parts.items():
        pl = board.placements[pname]
        for pp in bp.ports:
            x, y = pl.world(pp.at)
            dd = pl.wdir(pp.d)
            o = vertical(d.unique_name(f"{pname}_P{pp.pad}"), x, y, dd, pp.w)
            assign(d.excitations, schema.EXCITATIONS, "Lumped Port", f"{pname}_{pp.terminal}_pad{pp.pad}", [o.name],
                   PortNumber=str(num), Impedance="50ohm", IntegrationLine=f"{_mm(x)},{_mm(y)},0;{_mm(x)},{_mm(y)},{zt}")
            port_map[str(num)] = {"part": pname, "terminal": pp.terminal, "pad": pp.pad}
            num += 1
    for pname, bp in parts.items():
        if bp.type not in ("VDC", "IDC"):
            continue
        pl = board.placements[pname]
        x, y = pl.world((0, 0))
        net = nodes[pname][0]
        if net == 0:
            continue
        o = vertical(d.unique_name(f"{pname}_Supply"), x, y, pl.wdir(bp.pins[0].d), width_at(x, y, net, pname))
        assign(d.excitations, schema.EXCITATIONS, "Lumped Port", f"{pname}_supply", [o.name], PortNumber=str(num),
               Impedance="50ohm", IntegrationLine=f"{_mm(x)},{_mm(y)},0;{_mm(x)},{_mm(y)},{zt}")
        port_map[str(num)] = {"part": pname, "terminal": "+", "pad": ""}
        num += 1
    d.settings[PORT_MAP] = json.dumps(port_map)
    _embed_em(d, project, circuit, parts, board, ems, ground, warnings, ev.length(sub["H"]))
    wmin = min([p.w for bp in parts.values() for p in bp.pins if p.w > 0] + [r.w for r in board.routes] + [1.0])
    assign(d.mesh_ops, schema.MESH_OPS, "Length Based On Selection", "Length_Traces", conductors,
           MaxLength=_mm(wmin / 2))
    st = Setup("Setup1")
    sw = Sweep("Sweep")
    src = circuit.setups[0].sweeps[0].props if circuit.setups and circuit.setups[0].sweeps else {}
    for key in ("Distribution", "Start", "Stop", "Step", "Count"):
        if key in src:
            sw.props[key] = src[key]
    sw.props["SweepType"] = "Interpolating"
    st.props.update(Frequency=src.get("Stop", "10GHz"), MaximumPasses="10", MaximumDeltaS="0.02")
    st.sweeps.append(sw)
    d.setups.append(st)
    if not any(m.name == mat for m in project.materials):
        project.materials.append(Material(mat, permittivity=f"{er:g}", loss_tangent=f"{tand:g}", color="#2e7d32",
                                          description=f"Substrate {sname} of {circuit.name}"))
    d.notes = (f"Layout of the board of the circuit design '{circuit.name}' (its Board view). Lines keep their "
               "dimension variables; the positions of the parts are those of the board.")
    d.settings[DIMS] = json.dumps(dims)
    d.settings[VARS] = json.dumps(sorted(d.variables))
    return d, warnings
