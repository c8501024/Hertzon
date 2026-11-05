"""Circuit designs: a schematic of components and wires (Design.kind == "circuit").

Coordinates are schematic grid units (GRID = 10 per grid step). A component has pins at fixed offsets from its
origin, mirrored (top-bottom) and then rotated by its rotation (0, 90, 180, 270 degrees). Pins and wire points
that lie on the same grid point are connected; a wire connects all of its points; net labels with the same name
are connected. Every pin of an N-port block (Touchstone file or a 3D design) is a port of that block against
ground, as in a Touchstone file.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GRID = 10


@dataclass
class Component:
    name: str
    type: str                      # see TYPES
    x: int = 0
    y: int = 0
    rot: int = 0                   # degrees, multiple of 90
    props: dict[str, str] = field(default_factory=dict)
    mirror: bool = False           # flipped top-bottom before the rotation


@dataclass
class Wire:
    points: list[list[int]] = field(default_factory=list)     # [[x, y], ...] on the grid


@dataclass
class TypeInfo:
    label: str
    prefix: str
    props: dict[str, str]
    pins: list[tuple[int, int]]    # offsets at rotation 0 (N-port blocks: computed, see pins())
    help: str = ""
    category: str = ""


_SUB = {"Subst": "Sub1"}
TYPES: dict[str, TypeInfo] = {
    # ---- basic
    "GND": TypeInfo("Ground", "GND", {}, [(0, -10)], "Reference node", "Basic"),
    "PORT": TypeInfo("Power Port", "Port", {"Num": "", "Z0": "50ohm"}, [(0, -40)],
                     "Port of the S-parameters: a source with internal resistance Z0 against ground. Num sets the "
                     "port order (empty: by name)", "Basic"),
    "LABEL": TypeInfo("Net Label", "N", {"Net": "net1"}, [(0, 0)],
                      "Connects every point that carries a label with the same net name", "Basic"),
    # ---- lumped elements
    "R": TypeInfo("Resistor", "R", {"R": "50ohm"}, [(-30, 0), (30, 0)], "Resistance", "Lumped Elements"),
    "L": TypeInfo("Inductor", "L", {"L": "1nH"}, [(-30, 0), (30, 0)], "Inductance", "Lumped Elements"),
    "C": TypeInfo("Capacitor", "C", {"C": "1pF"}, [(-30, 0), (30, 0)], "Capacitance", "Lumped Elements"),
    "SRLC": TypeInfo("Series RLC", "SRLC", {"R": "0ohm", "L": "1nH", "C": "1pF"}, [(-40, 0), (40, 0)],
                     "R, L and C in series (leave R at 0 for a lossless resonator)", "Lumped Elements"),
    "PRLC": TypeInfo("Parallel RLC", "PRLC", {"R": "1e9ohm", "L": "1nH", "C": "1pF"}, [(-40, 0), (40, 0)],
                     "R, L and C in parallel", "Lumped Elements"),
    "DCBLOCK": TypeInfo("DC Block", "DCB", {}, [(-30, 0), (30, 0)],
                        "Ideal DC block: open at DC, short at every other frequency", "Lumped Elements"),
    "DCFEED": TypeInfo("DC Feed", "DCF", {}, [(-30, 0), (30, 0)],
                       "Ideal DC feed (bias tee choke): short at DC, open at every other frequency",
                       "Lumped Elements"),
    "ATTEN": TypeInfo("Attenuator", "ATT", {"Loss": "3dB", "Z0": "50ohm"}, [(-30, 0), (30, 0)],
                      "Matched attenuator (resistive pi network)", "Lumped Elements"),
    # ---- ideal lines
    "TLIN": TypeInfo("Ideal Line", "TL", {"Z0": "50ohm", "E": "90deg", "F": "1GHz"}, [(-40, 0), (40, 0)],
                     "Lossless TEM line: impedance Z0, electrical length E at frequency F", "Transmission Lines"),
    "TLOC": TypeInfo("Open Stub", "TLO", {"Z0": "50ohm", "E": "90deg", "F": "1GHz"}, [(0, -40)],
                     "Lossless TEM line open at its far end, as a shunt stub", "Transmission Lines"),
    "TLSC": TypeInfo("Short Stub", "TLS", {"Z0": "50ohm", "E": "90deg", "F": "1GHz"}, [(0, -40)],
                     "Lossless TEM line shorted at its far end, as a shunt stub", "Transmission Lines"),
    # ---- physical lines
    "SUBST": TypeInfo("Substrate", "Sub", {"Er": "3.66", "H": "0.508mm", "T": "35um", "TanD": "0.0037",
                                           "Cond": "5.8e7"}, [],
                      "Substrate of the microstrip and stripline elements: permittivity, height (stripline: ground "
                      "plane spacing), metal thickness, loss tangent, metal conductivity (S/m)", "Transmission Lines"),
    "MLIN": TypeInfo("Microstrip Line", "MS", {**_SUB, "W": "1.1mm", "L": "10mm"}, [(-40, 0), (40, 0)],
                     "Microstrip line (Hammerstad-Jensen, dispersion after Kirschning-Jansen, conductor and "
                     "dielectric loss)", "Transmission Lines"),
    "MLOC": TypeInfo("Microstrip Open Stub", "MSO", {**_SUB, "W": "1.1mm", "L": "10mm"}, [(0, -40)],
                     "Microstrip stub open at its end, with the open-end length extension", "Transmission Lines"),
    "MLSC": TypeInfo("Microstrip Short Stub", "MSS", {**_SUB, "W": "1.1mm", "L": "10mm"}, [(0, -40)],
                     "Microstrip stub shorted to ground at its end by a via of the line width (its inductance "
                     "included)", "Transmission Lines"),
    "MCLIN": TypeInfo("Coupled Microstrip", "MSC", {**_SUB, "W": "1mm", "S": "0.3mm", "L": "10mm"},
                      [(-40, -10), (40, -10), (-40, 10), (40, 10)],
                      "Symmetric coupled microstrip lines, even and odd mode (Garg-Bahl)", "Transmission Lines"),
    "MTEE": TypeInfo("Microstrip Tee", "MT", {**_SUB, "W1": "1.1mm", "W2": "1.1mm", "W3": "1.1mm"},
                     [(-30, 0), (30, 0), (0, 30)],
                     "T junction of microstrip lines (Hammerstad): W1 and W2 the main line, W3 the branch. The "
                     "lines attach at its edges", "Transmission Lines"),
    "MCORN": TypeInfo("Microstrip Corner", "MCN", {**_SUB, "W": "1.1mm"}, [(-30, 0), (0, 30)],
                      "90 degree corner of a microstrip line, not mitered (Kirschning, Jansen, Koster). The lines "
                      "attach at the edges of the corner square", "Transmission Lines"),
    "MMBEND": TypeInfo("Mitered Bend", "MB", {**_SUB, "W": "1.1mm"}, [(-30, 0), (0, 30)],
                       "90 degree microstrip bend with a 50 % miter (Kirschning, Jansen, Koster). The lines attach "
                       "at the edges of the corner square", "Transmission Lines"),
    "MSTEP": TypeInfo("Microstrip Step", "MSP", {**_SUB, "W1": "2mm", "W2": "1.1mm"}, [(-30, 0), (30, 0)],
                      "Width step from W1 to W2 at one reference plane", "Transmission Lines"),
    "MGAP": TypeInfo("Microstrip Gap", "MG", {**_SUB, "W1": "1.1mm", "W2": "1.1mm", "S": "0.2mm"},
                     [(-30, 0), (30, 0)], "Gap S between the open ends of two microstrip lines (Kirschning, Jansen)",
                     "Transmission Lines"),
    "MCROSS": TypeInfo("Microstrip Cross", "MX", {**_SUB, "W1": "1.1mm", "W2": "1.1mm", "W3": "1.1mm",
                                                   "W4": "1.1mm"},
                       [(-30, 0), (0, -30), (30, 0), (0, 30)],
                       "Cross junction of microstrip lines: arms 1 and 3 opposite, 2 and 4 opposite. The lines "
                       "attach at its edges", "Transmission Lines"),
    "SLIN": TypeInfo("Stripline", "SL", {**_SUB, "W": "0.4mm", "L": "10mm"}, [(-40, 0), (40, 0)],
                     "Centred stripline between two ground planes spaced H of the substrate", "Transmission Lines"),
    "CPWLIN": TypeInfo("Coplanar Line", "CPW", {**_SUB, "W": "1mm", "S": "0.2mm", "L": "10mm", "Backed": "False"},
                       [(-40, 0), (40, 0)],
                       "Coplanar waveguide: centre strip W, gaps S to the coplanar grounds on both sides; Backed True "
                       "adds a ground plane under the substrate (conformal mapping, dispersion after Frankel)",
                       "Transmission Lines"),
    "COAX": TypeInfo("Coaxial Line", "CX", {"Di": "0.91mm", "Do": "2.98mm", "Er": "2.1", "TanD": "0.0002",
                                             "Cond": "5.8e7", "L": "100mm"}, [(-40, 0), (40, 0)],
                     "Coaxial line: inner and outer diameter, dielectric, conductor loss", "Transmission Lines"),
    # ---- data
    "SNP": TypeInfo("Touchstone", "S", {"File": ""}, [], "N-port from a Touchstone file, ports against ground",
                    "Data"),
    "EM": TypeInfo("3D Design", "EM", {"Design": "", "Solution": ""}, [],
                   "S-parameters of a 3D design of this project, ports against ground", "Data"),
}
NPORT_TYPES = ("SNP", "EM")
LINE_TYPES = ("MLIN", "MLOC", "MLSC", "MCLIN", "MTEE", "MCORN", "MMBEND", "MSTEP", "MGAP", "MCROSS",
              "SLIN", "CPWLIN")
LINE_MODEL = "LineModel"            # design setting: "Closed form" or "Full wave" (cross-section solve)
FULL_WAVE_TYPES = ("MLIN", "MLOC", "MLSC", "CPWLIN")     # lines the full-wave line model applies to
CATEGORIES = ("Basic", "Lumped Elements", "Transmission Lines", "Data")
BLOCK_W = 100                       # width of an N-port block


def block_height(n: int) -> int:
    return max(80, 20 * ((n + 1) // 2) + 40)


def nport_pins(n: int) -> list[tuple[int, int]]:
    """Pin offsets of an N-port block: odd ports on the left, even ports on the right, top down."""
    left, right = (n + 1) // 2, n // 2
    out = []
    for k in range(n):
        side, count = (-1, left) if k % 2 == 0 else (1, right)
        out.append((side * (BLOCK_W // 2 + 10), -(count - 1) * 10 + 20 * (k // 2)))
    return out


def nport_count(c: Component) -> int:
    try:
        return max(1, int(c.props.get("Ports", "0") or 0))
    except ValueError:
        return 1


def rotate(dx: int, dy: int, rot: int) -> tuple[int, int]:
    r = rot % 360
    if r == 90:
        return -dy, dx
    if r == 180:
        return -dx, -dy
    if r == 270:
        return dy, -dx
    return dx, dy


def pin_offsets(c: Component) -> list[tuple[int, int]]:
    """Pin offsets at rotation 0, not mirrored."""
    return nport_pins(nport_count(c)) if c.type in NPORT_TYPES else TYPES[c.type].pins


def pins(c: Component) -> list[tuple[int, int]]:
    """Absolute pin positions of a component."""
    offs = [(dx, -dy if c.mirror else dy) for dx, dy in pin_offsets(c)]
    return [(c.x + rx, c.y + ry) for rx, ry in (rotate(dx, dy, c.rot) for dx, dy in offs)]


def next_name(components: list[Component], ctype: str) -> str:
    prefix = TYPES[ctype].prefix
    used = {c.name for c in components}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def port_order(components: list[Component]) -> list[Component]:
    """The power ports in S-parameter order: by Num where given, then by name."""
    import re

    def key(c):
        num = c.props.get("Num", "").strip()
        try:
            n = float(num)
        except ValueError:
            n = float("inf")
        return n, [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", c.name)]
    return sorted((c for c in components if c.type == "PORT"), key=key)


def nets(components: list[Component], wires: list[Wire]) -> tuple[dict[tuple[int, int], int], int]:
    """Node number of every connected grid point: 0 is ground (any net touching a ground pin), 1.. the others.
    Returns (node of point, number of nodes without ground)."""
    parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(p):
        parent.setdefault(p, p)
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for w in wires:
        pts = [tuple(p) for p in w.points]
        for p in pts:
            find(p)
        for a, b in zip(pts, pts[1:]):
            union(a, b)
    for c in components:
        for p in pins(c):
            find(p)
    all_pts = list(parent)
    # a pin or another wire's point lying on a segment joins it (T junctions)
    for w in wires:
        pts = [tuple(p) for p in w.points]
        for a, b in zip(pts, pts[1:]):
            for p in all_pts:
                if p not in (a, b) and _on_segment(p, a, b):
                    union(p, a)
    # net labels with the same name are one net; a label named GND or 0 is ground
    by_name: dict[str, tuple[int, int]] = {}
    ground_labels = []
    for c in components:
        if c.type == "LABEL":
            p = pins(c)[0]
            name = c.props.get("Net", "").strip()
            if not name:
                continue
            if name.upper() in ("GND", "0"):
                ground_labels.append(p)
            elif name in by_name:
                union(p, by_name[name])
            else:
                by_name[name] = p
    ground = {find(p) for c in components if c.type == "GND" for p in pins(c)} | {find(p) for p in ground_labels}
    ids: dict[tuple[int, int], int] = {}
    node_of_root: dict = {}
    n = 0
    for p in all_pts:
        r = find(p)
        if r in ground:
            ids[p] = 0
            continue
        if r not in node_of_root:
            n += 1
            node_of_root[r] = n
        ids[p] = node_of_root[r]
    return ids, n


def _on_segment(p, a, b) -> bool:
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if (x2 - x1) * (y - y1) != (y2 - y1) * (x - x1):
        return False
    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)


def from_dict(d: dict) -> tuple[list[Component], list[Wire]]:
    comps = [Component(c["name"], c["type"], int(c.get("x", 0)), int(c.get("y", 0)), int(c.get("rot", 0)),
                       dict(c.get("props", {})), bool(c.get("mirror", False)))
             for c in d.get("components", []) if c.get("type") in TYPES]
    wires = [Wire([[int(a), int(b)] for a, b in w.get("points", [])]) for w in d.get("wires", [])]
    return comps, wires


def drag_wires(components: list[Component], wires: list[Wire], moves: dict[str, tuple[int, int]]) -> list[Wire]:
    """The wires after moving the components named in moves by (dx, dy): wire points on their pins follow,
    with an elbow where a segment would become slanted, and a pin that touched a wire in the middle of a segment
    or the pin of a part that stays gets a new wire to its new place."""
    delta: dict[tuple[int, int], tuple[int, int]] = {}
    for c in components:
        if c.name in moves and moves[c.name] != (0, 0):
            for p in pins(c):
                delta[p] = moves[c.name]
    if not delta:
        return [Wire([list(p) for p in w.points]) for w in wires]

    def moved(p):
        d = delta.get(p)
        return (p[0] + d[0], p[1] + d[1]) if d else p

    out: list[Wire] = []
    at_points = set()
    for w in wires:
        pts = [tuple(p) for p in w.points]
        at_points.update(pts)
        hits = [p for p in pts if p in delta]
        if not hits:
            out.append(Wire([list(p) for p in pts]))
            continue
        if pts[0] in delta and pts[-1] in delta and delta[pts[0]] == delta[pts[-1]] and \
                all(delta.get(p, delta[pts[0]]) == delta[pts[0]] for p in pts):
            dx, dy = delta[pts[0]]                 # both ends follow the same move: the wire moves along
            out.append(Wire([[x + dx, y + dy] for x, y in pts]))
            continue
        new = [moved(pts[0])]
        for a, b in zip(pts, pts[1:]):
            a2, b2 = moved(a), moved(b)
            if a2[0] != b2[0] and a2[1] != b2[1]:
                horizontal = a[1] == b[1]
                if b not in delta:                   # keep the direction of the segment at the end that stays
                    new.append((b2[0], a2[1]) if not horizontal else (a2[0], b2[1]))
                else:
                    new.append((b2[0], a2[1]) if horizontal else (a2[0], b2[1]))
            new.append(b2)
        out.append(Wire([list(p) for p in _simplify(new)]))
    # pins that joined a segment in its middle or the pin of a part that stays: a new wire keeps the connection
    still = {p for c in components if c.name not in moves for p in pins(c)}
    for p, (dx, dy) in delta.items():
        if p in at_points:
            continue
        on_wire = any(_on_segment(p, tuple(a), tuple(b)) for w in wires for a, b in zip(w.points, w.points[1:]))
        if on_wire or p in still:
            q = (p[0] + dx, p[1] + dy)
            out.append(Wire([list(x) for x in _simplify([p, (q[0], p[1]), q])]))
    return out


def _simplify(pts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Without repeated points and without corners that lie on a straight run."""
    out: list[tuple[int, int]] = []
    for p in pts:
        if out and out[-1] == p:
            continue
        if len(out) >= 2 and _on_segment(out[-1], out[-2], p) and \
                (out[-2][0] == p[0] or out[-2][1] == p[1]):
            out[-1] = p
            continue
        out.append(p)
    return out


# ---- discontinuities found at the nodes ----------------------------------------------------------------------
AUTO_ARMS = ("MLIN", "MLOC", "MLSC")          # lines whose meeting points get a junction model automatically
AUTO = "AutoJunctions"                         # design setting: "False" switches the automatic junctions off


@dataclass
class Junction:
    node: int
    point: tuple[int, int]
    type: str                                 # MCORN, MSTEP, MTEE or MCROSS
    props: dict[str, str]
    arms: list[tuple[str, int]]               # (component, pin) in the pin order of the junction part
    rot: int = 0
    mirror: bool = False

    @property
    def name(self) -> str:
        return f"~{self.type}@{self.point[0]},{self.point[1]}"

    def component(self) -> Component:
        return Component(self.name, self.type, self.point[0], self.point[1], self.rot, dict(self.props), self.mirror)


def _arm_dir(c: Component, k: int) -> tuple[int, int]:
    """Schematic direction from pin k of a line into the line (unit axis vector)."""
    p = pins(c)[k]
    q = pins(c)[1 - k] if c.type == "MLIN" else (c.x, c.y)
    dx, dy = q[0] - p[0], q[1] - p[1]
    if abs(dx) >= abs(dy):
        return (1 if dx > 0 else -1), 0
    return 0, (1 if dy > 0 else -1)


def _width(text: str) -> float | None:
    """A width as a comparable number (only literal values; None for expressions)."""
    import re
    m = re.fullmatch(r"\s*([0-9.eE+-]+)\s*(mm|um|mil|in|cm|m)?\s*", text or "")
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v * {"mm": 1e-3, "um": 1e-6, "mil": 2.54e-5, "in": 0.0254, "cm": 1e-2, "m": 1.0, None: 1e-3}[m.group(2)]


def find_junctions(components: list[Component], wires: list[Wire]) -> list[Junction]:
    """Where only microstrip lines of one substrate meet at a point: a corner (two lines at a right angle), a
    width step (two lines in line with different widths), a tee (three) or a cross (four), with the widths of the
    lines. A point that also touches another part (an explicit junction part, a port, a lumped part) has none."""
    ids, _ = nets(components, wires)
    at: dict[int, list[tuple[Component, int]]] = {}
    for c in components:
        for k, p in enumerate(pins(c)):
            n = ids.get(p, 0)
            if n:
                at.setdefault(n, []).append((c, k))
    point = {}
    for c in components:
        for p in pins(c):
            point.setdefault(ids.get(p, 0), p)
    out = []
    for n, conns in at.items():
        if len(conns) < 2 or any(c.type not in AUTO_ARMS for c, _ in conns):
            continue
        if len({c.props.get("Subst", "") for c, _ in conns}) != 1:
            continue
        arms = [(c, k, _arm_dir(c, k)) for c, k in conns]
        dirs = [a[2] for a in arms]
        if len(set(dirs)) != len(dirs):          # two lines on the same side: no junction model
            continue
        sub = {"Subst": conns[0][0].props.get("Subst", "")}
        w = {c.name: c.props.get("W", "") for c, _ in conns}
        if len(arms) == 2:
            (c1, _, d1), (c2, _, d2) = arms
            if d1[0] * d2[0] + d1[1] * d2[1] == 0:
                jt, props, order = "MCORN", {**sub, "W": w[c1.name]}, arms
            else:
                a, b = _width(w[c1.name]), _width(w[c2.name])
                if w[c1.name].strip() == w[c2.name].strip() or (a and b and abs(a / b - 1) < 0.02):
                    continue
                jt, props, order = "MSTEP", {**sub, "W1": w[c1.name], "W2": w[c2.name]}, arms
        elif len(arms) == 3:
            main = [(x, y) for i, x in enumerate(arms) for y in arms[i + 1:]
                    if x[2][0] == -y[2][0] and x[2][1] == -y[2][1]]
            if not main:
                continue
            a, b = main[0]
            br = next(x for x in arms if x is not a and x is not b)
            jt, order = "MTEE", [a, b, br]
            props = {**sub, "W1": w[a[0].name], "W2": w[b[0].name], "W3": w[br[0].name]}
        elif len(arms) == 4:
            a = arms[0]
            opp = next(x for x in arms if x[2] == (-a[2][0], -a[2][1]))
            rest = [x for x in arms if x is not a and x is not opp]
            jt, order = "MCROSS", [a, rest[0], opp, rest[1]]
            props = {**sub, **{f"W{j + 1}": w[x[0].name] for j, x in enumerate(order)}}
        else:
            continue
        # drawn where the wires of the lines meet: the point of the net nearest to all their pins
        ends = [pins(c)[k] for c, k, _ in arms]
        here = min((q for q, m in ids.items() if m == n),
                   key=lambda q: (sum(abs(q[0] - e[0]) + abs(q[1] - e[1]) for e in ends), q != point[n]))
        j = Junction(n, here, jt, props, [(x[0].name, x[1]) for x in order])
        want = [x[2] for x in order]               # the junction's pins must point along its lines
        for mirror in (False, True):
            for rot in (0, 90, 180, 270):
                j.rot, j.mirror = rot, mirror
                cc = j.component()
                got = [(int((q[0] - cc.x) / 30), int((q[1] - cc.y) / 30)) for q in pins(cc)]
                if got == want:
                    break
            else:
                continue
            break
        else:
            continue
        out.append(j)
    return out


def netlist(components: list[Component], wires: list[Wire], auto: bool = True
            ) -> tuple[list[Component], dict[str, list[int]], int, list[Junction]]:
    """The parts with the node of each pin, the automatic junctions inserted: each line meeting at a junction
    gets a node of its own, joined to the others by the junction part. Returns (parts, nodes of the pins of
    each part, number of nodes without ground, junctions)."""
    ids, n = nets(components, wires)
    nodes_of = {c.name: [ids[q] for q in pins(c)] for c in components}
    comps = list(components)
    js = find_junctions(components, wires) if auto else []
    for j in js:
        jn = []
        for name, k in j.arms:
            n += 1
            nodes_of[name][k] = n
            jn.append(n)
        cc = j.component()
        comps.append(cc)
        nodes_of[cc.name] = jn
    return comps, nodes_of, n, js
