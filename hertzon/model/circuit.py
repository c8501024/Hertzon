"""Circuit designs: a schematic of components and wires (Design.kind == "circuit").

Coordinates are schematic grid units (GRID = 10 per grid step). A component has pins at fixed offsets from its
origin, rotated by its rotation (0, 90, 180, 270 degrees). Pins and wire points that lie on the same grid point
are connected; a wire connects all of its points. Every pin of an N-port block (Touchstone file or a 3D design)
is a port of that block against ground, as in a Touchstone file.
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


TYPES: dict[str, TypeInfo] = {
    "R": TypeInfo("Resistor", "R", {"R": "50ohm"}, [(-30, 0), (30, 0)], "Resistance"),
    "L": TypeInfo("Inductor", "L", {"L": "1nH"}, [(-30, 0), (30, 0)], "Inductance"),
    "C": TypeInfo("Capacitor", "C", {"C": "1pF"}, [(-30, 0), (30, 0)], "Capacitance"),
    "GND": TypeInfo("Ground", "GND", {}, [(0, -10)], "Reference node"),
    "PORT": TypeInfo("Port", "Port", {"Z0": "50ohm"}, [(20, 0)], "Circuit port with reference impedance"),
    "TLIN": TypeInfo("Ideal line", "TL", {"Z0": "50ohm", "E": "90deg", "F": "1GHz"}, [(-40, 0), (40, 0)],
                     "Lossless TEM line: impedance Z0, electrical length E at frequency F"),
    "SNP": TypeInfo("Touchstone", "S", {"File": ""}, [], "N-port from a Touchstone file, ports against ground"),
    "EM": TypeInfo("3D design", "EM", {"Design": "", "Solution": ""}, [],
                   "S-parameters of a 3D design of this project, ports against ground"),
}
NPORT_TYPES = ("SNP", "EM")
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


def pins(c: Component) -> list[tuple[int, int]]:
    """Absolute pin positions of a component."""
    offs = nport_pins(nport_count(c)) if c.type in NPORT_TYPES else TYPES[c.type].pins
    return [(c.x + rx, c.y + ry) for rx, ry in (rotate(dx, dy, c.rot) for dx, dy in offs)]


def next_name(components: list[Component], ctype: str) -> str:
    prefix = TYPES[ctype].prefix
    used = {c.name for c in components}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


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
    ground = {find(p) for c in components if c.type == "GND" for p in pins(c)}
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
                       dict(c.get("props", {}))) for c in d.get("components", []) if c.get("type") in TYPES]
    wires = [Wire([[int(a), int(b)] for a, b in w.get("points", [])]) for w in d.get("wires", [])]
    return comps, wires
