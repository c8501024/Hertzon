"""Create a circuit design from a planar 3D design (microstrip on one substrate).

Steps
  1. extract(): build the 3D design with the geometry kernel and read
     - the substrate (box from z = 0 to its top, its permittivity, loss tangent),
     - the copper on top of it as 2D polygons (horizontal faces of the conductors),
     - vias (conductors from the ground plane to the top), lumped ports and lumped RLC parts.
  2. recognize(): cut the copper into pieces of constant cross-section (grid of the polygon corners), classify
     them as lines and junctions, find coupled pairs and gaps, connect them into a netlist.
  3. to_circuit(): a circuit design with MLIN / MLOC / MLSC / MCLIN / MTEE / MCORN / MCROSS / MSTEP / MGAP / MVIA,
     the ports and the lumped parts; every dimension is the 3D design's variable where one has the same value and
     is used by the object the piece comes from, a number otherwise. The table element -> 3D objects -> variables
     is returned with it (the link of the pair).

Nothing is guessed silently: copper that is not cut into Manhattan pieces, coupling that no pair model takes and
parts without a counterpart are listed in Recognition.notes.

circuit_from_design() is the entry point used by the GUI and the MCP tool.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import gmsh
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from ..kernel import occ as K
from . import circuit as CK
from . import materials as MAT
from .design import Design, Project, circuit_setup

TOL = 1e-6          # mm: coordinates closer than this are the same
COUPLE_H = 2.0      # coupled pair if the gap is below COUPLE_H * h
NOTE_H = 4.0        # weaker coupling up to NOTE_H * h is reported as neglected
GAP_H = 2.0         # end-to-end gap below GAP_H * h is a series gap (MGAP)


# ------------------------------------------------------------------------------------------------ extraction
@dataclass
class Board:
    h: float = 0.0                 # mm
    t: float = 0.035               # mm
    er: float = 1.0
    tand: float = 0.0
    cond: float = 5.8e7
    copper: list = field(default_factory=list)          # [(Polygon, [object names])]
    vias: list = field(default_factory=list)            # [(geometry in xy, object name)]
    ports: list = field(default_factory=list)           # [(number, Point, z0 text, object names)]
    lumped: list = field(default_factory=list)          # [(name, "series"/"shunt", p0, p1, props, objects)]
    notes: list = field(default_factory=list)
    fmax: float = 0.0              # Hz, highest frequency of the setups


def _face_rings(f: int) -> list[np.ndarray]:
    segs = []
    for _, c in gmsh.model.getBoundary([(2, f)], combined=False, oriented=True, recursive=False):
        t = abs(c)
        lo, hi = gmsh.model.getParametrizationBounds(1, t)
        n = 2 if gmsh.model.getType(1, t) == "Line" else 721
        P = np.array(gmsh.model.getValue(1, t, list(np.linspace(lo[0], hi[0], n)))).reshape(-1, 3)
        segs.append(P[::-1] if c < 0 else P)
    rings, rem = [], segs[:]
    eps = 1e-9
    while rem:
        ring = list(rem.pop(0))
        while rem:
            end = ring[-1]
            if len(ring) > 2 and np.linalg.norm(end - ring[0]) < eps:
                break
            k = next((i for i, s in enumerate(rem) if np.linalg.norm(s[0] - end) < eps), None)
            if k is None:
                k = next((i for i, s in enumerate(rem) if np.linalg.norm(s[-1] - end) < eps), None)
                if k is None:
                    break
                rem[k] = rem[k][::-1]
            ring.extend(rem.pop(k)[1:])
        rings.append(np.array(ring))
    return rings


def _polygon(rings: list[np.ndarray]) -> Polygon:
    polys = [Polygon(r[:, :2] * 1e3) for r in rings if len(r) >= 3]
    polys = [p.buffer(0).simplify(1e-6) for p in polys]     # drop collinear samples of straight non-"Line" edges
    if not polys:
        return Polygon()
    polys.sort(key=lambda p: -p.area)
    out = polys[0]
    for p in polys[1:]:
        out = out.difference(p)
    return out


def _material(project: Project, name: str):
    for m in project.materials:
        if m.name == name:
            return m
    return MAT.library_material(name)


def extract(project: Project, design: Design) -> Board:
    """Substrate, top copper, vias, ports and lumped parts of a 3D design (built in its own gmsh model; the
    current model is restored afterwards)."""
    prev = None
    if gmsh.isInitialized():
        try:
            prev = gmsh.model.getCurrent()
        except Exception:  # noqa: BLE001
            prev = None
    K.ensure_gmsh("hertzon_circuit_from_3d")
    gmsh.model.remove()
    gmsh.model.add("hertzon_circuit_from_3d")
    try:
        return _extract(project, design)
    finally:
        try:
            gmsh.model.remove()
        except Exception:  # noqa: BLE001
            pass
        if prev and prev != "hertzon_circuit_from_3d":
            try:
                gmsh.model.setCurrent(prev)
            except Exception:  # noqa: BLE001
                pass


def _extract(project: Project, design: Design) -> Board:
    ev = design.evaluator(project)
    b = Board()
    ctx = K.build_design(design, ev)
    for n, e in ctx.errors.items():
        b.notes.append(f"{n}: not built ({e})")
    # conductors: objects with a conducting boundary or a conductor material
    cond_names = set()
    for a in design.boundaries:
        if a.kind in ("Finite Conductivity", "Perfect E"):
            cond_names.update(a.objects)
            if a.kind == "Finite Conductivity" and a.props.get("UseMaterial") == "False":
                try:
                    b.cond = ev.value(a.props.get("Conductivity", "5.8e7"))
                    if a.props.get("UseThickness") == "True":
                        b.t = ev.length(a.props.get("Thickness", "35um")) * 1e3
                except Exception:  # noqa: BLE001
                    pass
    subs = []
    for o in design.live_objects():
        m = _material(project, o.material)
        if m is not None and (m.pec or MAT.is_conductor(m)):
            cond_names.add(o.name)
        elif m is not None and o.name in ctx.built and o.material != "vacuum":
            bb = K._bbox(ctx.built[o.name])
            if abs(bb[2]) < 1e-6 and bb[5] > 0:
                subs.append((o, m, bb))
    if not subs:
        raise ValueError("no substrate: a dielectric box standing on z = 0")
    o, m, bb = max(subs, key=lambda s: (s[2][3] - s[2][0]) * (s[2][4] - s[2][1]))
    tops = [pl[1] for f in K.object_faces(ctx.built[o.name]) if (pl := K._plane_of(f)) is not None
            and abs(pl[0][2]) > 0.999]
    zt = max(tops) if tops else bb[5]
    b.h = zt * 1e3
    b.er = ev.value(m.permittivity)
    b.tand = ev.value(m.loss_tangent)
    if len(subs) > 1:
        b.notes.append(f"several dielectric bodies: the substrate is {o.name}, the others are ignored")
    top: dict[str, list] = {}
    for n in sorted(cond_names):
        dt = ctx.built.get(n)
        if not dt:
            continue
        bb = K._bbox(dt)
        if bb[2] < 1e-6 and bb[5] > 0.9 * zt and bb[5] < zt * 1.01 + 1e-4 and bb[5] - bb[2] > 0.5 * zt:
            b.vias.append((box(bb[0] * 1e3 + 1e-4, bb[1] * 1e3 + 1e-4, bb[3] * 1e3 - 1e-4, bb[4] * 1e3 - 1e-4), n))
            continue
        for f in K.object_faces(dt):
            pl = K._plane_of(f)
            if pl is None or abs(pl[0][2]) < 0.999:
                continue
            z = pl[1]
            if zt - 1e-7 <= z <= zt + 2e-4:
                top.setdefault(n, []).append(_polygon(_face_rings(f)))
            elif abs(z) < 1e-9:
                pass                                   # ground plane
            elif z > zt + 2e-4 or 0 < z < zt - 1e-7:
                b.notes.append(f"{n}: copper at z = {z * 1e3:.4g} mm is not on the substrate top (ignored)")
    # union per connected copper, remember which objects make it
    allp = unary_union([p for ps in top.values() for p in ps])
    geoms = list(allp.geoms) if hasattr(allp, "geoms") else ([allp] if not allp.is_empty else [])
    for g in geoms:
        objs = [n for n, ps in top.items() if any(p.intersection(g).area > 1e-9 for p in ps)]
        b.copper.append((g, objs))
    for e in design.excitations:
        if e.kind == "Wave Port":
            # the port sheet stands at the line end: its footprint centre on the board
            dts = [x for n in e.objects for x in ctx.built.get(n, [])]
            if not dts:
                b.notes.append(f"port {e.name}: its sheet was not built")
                continue
            bb = K._bbox(dts)
            b.ports.append((e.props.get("PortNumber", e.name), Point((bb[0] + bb[3]) / 2 * 1e3, (bb[1] + bb[4]) / 2 * 1e3),
                            "50ohm", list(e.objects)))
            b.notes.append(f"port {e.name}: wave port taken as a 50 ohm port at the line end (its de-embedding and "
                           "mode impedance are not carried over)")
            continue
        if e.kind != "Lumped Port":
            b.notes.append(f"port {e.name}: {e.kind} is not taken over")
            continue
        pts = ev.points(e.props.get("IntegrationLine", ""))
        if len(pts) != 2:
            b.notes.append(f"port {e.name}: no integration line")
            continue
        b.ports.append((e.props.get("PortNumber", e.name), Point(pts[0][0] * 1e3, pts[0][1] * 1e3),
                        e.props.get("Impedance", "50ohm"), list(e.objects)))
    for a in design.boundaries:
        if a.kind != "Lumped RLC":
            continue
        pts = ev.points(a.props.get("IntegrationLine", ""))
        if len(pts) != 2:
            b.notes.append(f"{a.name}: lumped part without integration line")
            continue
        p0, p1 = Point(pts[0][0] * 1e3, pts[0][1] * 1e3), Point(pts[1][0] * 1e3, pts[1][1] * 1e3)
        kind = "shunt" if abs(pts[0][2] - pts[1][2]) > 0.5 * zt else "series"
        b.lumped.append((a.name, kind, p0, p1, dict(a.props), list(a.objects)))
    fmax = 0.0
    for st in design.setups:
        for key in ("Frequency",):
            try:
                fmax = max(fmax, ev.freq(st.props.get(key, "0")))
            except Exception:  # noqa: BLE001
                pass
        for sw in st.sweeps:
            try:
                fmax = max(fmax, ev.freq(sw.props.get("Stop", "0")))
            except Exception:  # noqa: BLE001
                pass
    b.fmax = fmax
    return b


# ------------------------------------------------------------------------------------------------ recognition
@dataclass
class Piece:
    kind: str                      # "line", "junction", "coupled"
    x0: float
    y0: float
    x1: float
    y1: float
    axis: str = ""                 # line: "x" or "y"
    poly: int = 0                  # index of the copper polygon
    name: str = ""
    other: "Piece | None" = None   # coupled: the second line

    @property
    def rect(self):
        return box(self.x0, self.y0, self.x1, self.y1)

    @property
    def length(self):
        return (self.x1 - self.x0) if self.axis == "x" else (self.y1 - self.y0)

    @property
    def width(self):
        return (self.y1 - self.y0) if self.axis == "x" else (self.x1 - self.x0)


@dataclass
class Terminal:
    piece: Piece
    seg: LineString                # the edge where it attaches
    out: tuple                     # direction out of the piece
    width: float
    pin: int                       # pin index of the element made from the piece
    node: int = -1


@dataclass
class Recognition:
    board: Board
    pieces: list
    elements: list = field(default_factory=list)     # (type, name, props(mm floats), [nodes], piece, extra)
    notes: list = field(default_factory=list)
    unrecognized_area: float = 0.0
    copper_area: float = 0.0
    miters: list = field(default_factory=list)
    rings: list = field(default_factory=list)
    ring_elements: list = field(default_factory=list)
    bad_junctions: set = field(default_factory=set)
    terms: list = field(default_factory=list)
    em_ports: list = field(default_factory=list)
    port_nodes: list = field(default_factory=list)
    lumped: list = field(default_factory=list)
    via_terms: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    n_nodes: int = 0


def _r(v):
    return round(v / TOL) * TOL


def _cells(poly: Polygon):
    xs = sorted({_r(x) for ring in [poly.exterior, *poly.interiors] for x, _ in ring.coords})
    ys = sorted({_r(y) for ring in [poly.exterior, *poly.interiors] for _, y in ring.coords})
    occ = np.zeros((len(xs) - 1, len(ys) - 1), bool)
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            c = box(xs[i], ys[j], xs[i + 1], ys[j + 1])
            occ[i, j] = poly.intersection(c).area > 0.999 * c.area
    return xs, ys, occ


def _runs(occ, xs, ys):
    """For every occupied cell the run along x (consecutive columns with the same occupied y interval) and the run
    along y; returns per cell (aspect along x, run id x, aspect along y, run id y) and the runs."""
    nx, ny = occ.shape
    col_iv = {}                 # (i, j) -> (j0, j1) occupied interval of column i through j
    for i in range(nx):
        j = 0
        while j < ny:
            if occ[i, j]:
                j0 = j
                while j < ny and occ[i, j]:
                    j += 1
                for q in range(j0, j):
                    col_iv[(i, q)] = (j0, j - 1)
            else:
                j += 1
    row_iv = {}
    for j in range(ny):
        i = 0
        while i < nx:
            if occ[i, j]:
                i0 = i
                while i < nx and occ[i, j]:
                    i += 1
                for q in range(i0, i):
                    row_iv[(q, j)] = (i0, i - 1)
            else:
                i += 1
    hx = {}                       # cell -> (i0, i1, j0, j1) run along x
    for (i, j), iv in col_iv.items():
        i0 = i
        while (i0 - 1, iv[0]) in col_iv and col_iv[(i0 - 1, iv[0])] == iv:
            i0 -= 1
        i1 = i
        while (i1 + 1, iv[0]) in col_iv and col_iv[(i1 + 1, iv[0])] == iv:
            i1 += 1
        hx[(i, j)] = (i0, i1, iv[0], iv[1])
    vy = {}
    for (i, j), iv in row_iv.items():
        j0 = j
        while (iv[0], j0 - 1) in row_iv and row_iv[(iv[0], j0 - 1)] == iv:
            j0 -= 1
        j1 = j
        while (iv[0], j1 + 1) in row_iv and row_iv[(iv[0], j1 + 1)] == iv:
            j1 += 1
        vy[(i, j)] = (iv[0], iv[1], j0, j1)
    return hx, vy


def _pieces_of(poly: Polygon, k: int, notes: list) -> tuple[list[Piece], float]:
    xs, ys, occ = _cells(poly)
    hx, vy = _runs(occ, xs, ys)
    kind = {}
    for c in hx:
        i0, i1, j0, j1 = hx[c]
        ax = (xs[i1 + 1] - xs[i0]) / (ys[j1 + 1] - ys[j0])
        a0, a1, b0, b1 = vy[c]
        ay = (ys[b1 + 1] - ys[b0]) / (xs[a1 + 1] - xs[a0])
        if ax >= 1 and ax >= ay:
            kind[c] = ("x", hx[c])
        elif ay >= 1:
            kind[c] = ("y", vy[c])
        else:
            kind[c] = ("j", None)
    pieces = []
    seen = set()
    for c, (kd, run) in kind.items():
        if c in seen:
            continue
        # connected cells of the same kind (and the same run for lines)
        stack, comp = [c], []
        seen.add(c)
        while stack:
            q = stack.pop()
            comp.append(q)
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                r = (q[0] + d[0], q[1] + d[1])
                if r in kind and r not in seen and kind[r][0] == kd and kind[r][1] == run:
                    seen.add(r)
                    stack.append(r)
        i0 = min(q[0] for q in comp)
        i1 = max(q[0] for q in comp)
        j0 = min(q[1] for q in comp)
        j1 = max(q[1] for q in comp)
        if len(comp) != (i1 - i0 + 1) * (j1 - j0 + 1):
            # not a rectangle: split into its cells' bounding rectangles per row (rare)
            notes.append(f"copper {k}: a {'junction' if kd == 'j' else 'line'} region at "
                         f"({xs[i0]:.3f}, {ys[j0]:.3f}) mm is not rectangular; taken as its bounding box")
        p = Piece("junction" if kd == "j" else "line", xs[i0], ys[j0], xs[i1 + 1], ys[j1 + 1],
                  axis=kd if kd != "j" else "", poly=k)
        pieces.append(p)
    covered = unary_union([p.rect for p in pieces]) if pieces else Polygon()
    rest = poly.difference(covered).area
    return pieces, rest


def _ring(poly: Polygon, k: int, notes: list):
    """A ring of constant width (one hole, a circle) with straight radial feeds: centre, radii, feeds; or None."""
    if len(poly.interiors) != 1:
        return None
    hole = np.array(poly.interiors[0].coords)[:-1]
    if len(hole) < 12:
        return None
    # least-squares circle (x^2 + y^2 = 2 a x + 2 b y + k)
    A = np.c_[2 * hole, np.ones(len(hole))]
    sol = np.linalg.lstsq(A, (hole ** 2).sum(axis=1), rcond=None)[0]
    c = sol[:2]
    d = np.hypot(*(hole - c).T)
    rin = d.mean()
    if d.std() > 1e-4 * rin + 1e-6:
        return None
    ext = np.array(poly.exterior.coords)[:-1]
    de = np.hypot(*(ext - c).T)
    rout = float(np.min(de))
    if rout <= rin + 1e-6:
        return None
    annulus = Point(*c).buffer(rout + 1e-4, 256).difference(Point(*c).buffer(rin - 1e-4, 256))
    rest = poly.difference(annulus)
    parts = [g for g in (rest.geoms if hasattr(rest, "geoms") else [rest]) if g.area > 1e-3]
    feeds = []
    for g in parts:
        u = np.array(g.centroid.coords[0]) - c
        u /= np.linalg.norm(u)
        v = np.array((-u[1], u[0]))
        P = np.array(g.exterior.coords) - c
        along, across = P @ u, P @ v
        w = float(across.max() - across.min())
        r_end = float(along.max())
        ln = r_end - rout
        if ln <= 0 or abs(g.area - w * ln) > 0.05 * w * ln:
            notes.append(f"copper {k}: ring with a part that is not a straight radial feed: not recognized")
            return None
        feeds.append({"theta": math.atan2(u[1], u[0]), "W": w, "L": ln, "r_end": r_end, "poly": g, "u": u})
    feeds.sort(key=lambda f: f["theta"])
    notes.append(f"copper {k}: ring Rm = {(rin + rout) / 2:.4g} mm, W = {rout - rin:.4g} mm with {len(feeds)} radial "
                 "feeds: arcs as lines of their length on the mean radius, feeds through tees")
    return {"c": c, "rin": rin, "rout": rout, "feeds": feeds, "k": k}


def _ring_elements(rec, r):
    """Tees where the feeds meet the ring, lines for the arcs between them (length on the mean radius less half
    a tee on either side), lines for the feeds out to their ends."""
    c, rm, wr = r["c"], (r["rin"] + r["rout"]) / 2, r["rout"] - r["rin"]
    fs = r["feeds"]
    if not fs:
        rec.notes.append("ring without feeds: left out")
        return
    out = rec.ring_elements

    def snap(u):
        return (int(np.sign(u[0])), 0) if abs(u[0]) >= abs(u[1]) else (0, int(np.sign(u[1])))

    def new():
        rec.n_nodes += 1
        return rec.n_nodes
    n = len(fs)
    left = [new() for _ in fs]     # tee arm towards smaller angle
    right = [new() for _ in fs]    # tee arm towards larger angle
    for i, f in enumerate(fs):
        u = f["u"]
        t_cw, t_ccw = np.array((u[1], -u[0])), np.array((-u[1], u[0]))
        stem = new()
        p = c + u * rm
        out.append(Element("MTEE", f"RT{i + 1}", {"W1": wr, "W2": wr, "W3": f["W"]}, [left[i], right[i], stem],
                           [snap(t_cw), snap(t_ccw), snap(u)], (float(p[0]), float(p[1]))))
        end = f.get("port_node") or new()
        q = c + u * (r["rout"] + f["L"] / 2)
        out.append(Element("MLIN", f"RF{i + 1}", {"W": f["W"], "L": f["L"]}, [stem, end], [snap(-u), snap(u)],
                           (float(q[0]), float(q[1]))))
        if not f.get("port_node"):
            rec.notes.append(f"ring feed {i + 1} ends without a port: open line end")
    for i in range(n):
        j = (i + 1) % n
        dth = (fs[j]["theta"] - fs[i]["theta"]) % (2 * math.pi) or 2 * math.pi
        ln = rm * dth - (fs[i]["W"] + fs[j]["W"]) / 2
        mid = fs[i]["theta"] + dth / 2
        u = np.array((math.cos(mid), math.sin(mid)))
        tan = np.array((-u[1], u[0]))
        q = c + u * rm
        out.append(Element("MLIN", f"RA{i + 1}", {"W": wr, "L": ln}, [right[i], left[j]], [snap(-tan), snap(tan)],
                           (float(q[0]), float(q[1]))))


def _fill_miters(poly: Polygon):
    """Square off 45 degree corner cuts (mitered bends): returns the filled polygon and the added triangles."""
    tris = []
    c = list(poly.exterior.coords)
    for (ax, ay), (bx, by) in zip(c, c[1:]):
        dx, dy = bx - ax, by - ay
        if abs(dx) > 1e-6 and abs(dy) > 1e-6 and abs(abs(dx) - abs(dy)) < 1e-6 * max(1, abs(dx)):
            for cx, cy in ((bx, ay), (ax, by)):
                t = Polygon([(ax, ay), (bx, by), (cx, cy)])
                if poly.intersection(t).area < 1e-9 * max(t.area, 1e-12) + 1e-12:
                    tris.append(t)
                    break
    if not tris:
        return poly, []
    filled = unary_union([poly, *[t.buffer(1e-9, join_style=2) for t in tris]]).buffer(0)
    filled = filled.simplify(1e-7)
    if filled.geom_type != "Polygon":
        return poly, []
    # snap tiny buffer offsets away
    filled = Polygon([(_r(x), _r(y)) for x, y in filled.exterior.coords],
                     [[(_r(x), _r(y)) for x, y in i.coords] for i in filled.interiors]).buffer(0)
    return filled, tris


def _diagonal_length(poly: Polygon) -> float:
    out = 0.0
    for ring in [poly.exterior, *poly.interiors]:
        c = list(ring.coords)
        for (ax, ay), (bx, by) in zip(c, c[1:]):
            if abs(ax - bx) > 1e-6 and abs(ay - by) > 1e-6:
                out += math.hypot(bx - ax, by - ay)
    return out


def _touch(a: Piece, b: Piece):
    """The common edge of two pieces (LineString) or None."""
    g = a.rect.intersection(b.rect)
    if g.is_empty or g.length < TOL or g.area > TOL * TOL:
        return None
    if g.geom_type != "LineString":
        g = g.envelope.exterior if g.geom_type == "Polygon" else g
        if g.geom_type != "LineString":
            return None
    return g


def recognize(board: Board) -> Recognition:
    notes = list(board.notes)
    pieces = []
    unrec = 0.0
    miters = []
    rings = []
    for k, (poly, _) in enumerate(board.copper):
        if poly.geom_type != "Polygon":
            notes.append(f"copper {k}: {poly.geom_type} skipped")
            continue
        ring = _ring(poly, k, notes)
        if ring is not None:
            rings.append(ring)
            continue
        poly, tris = _fill_miters(poly)
        miters += tris
        diag = _diagonal_length(poly)
        if diag > 1e-3:
            notes.append(f"copper {k} ({poly.area:.3g} mm² at ({poly.centroid.x:.2f}, {poly.centroid.y:.2f}) mm) has "
                         f"{diag:.3g} mm of slanted or curved edges: not recognized (an EM block in a full "
                         "implementation)")
            unrec += poly.area
            continue
        ps, rest = _pieces_of(poly, k, notes)
        if rest > 1e-6:
            notes.append(f"copper {k}: {rest:.4g} mm² are not Manhattan pieces (not recognized; needs an EM block)")
            unrec += rest
        pieces += ps
    rec = Recognition(board, pieces, notes=notes, unrecognized_area=unrec,
                      copper_area=sum(p.area for p, _ in board.copper))
    rec.miters = miters
    rec.rings = rings
    h = board.h
    # ---- coupled pairs: parallel lines of different or the same copper, gap below COUPLE_H * h
    lines = [p for p in pieces if p.kind == "line"]
    cands = []
    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            if a.axis != b.axis or (a.poly == b.poly and _touch(a, b)):
                continue
            if a.axis == "x":
                lo, hi = max(a.x0, b.x0), min(a.x1, b.x1)
                gap = max(b.y0 - a.y1, a.y0 - b.y1)
            else:
                lo, hi = max(a.y0, b.y0), min(a.y1, b.y1)
                gap = max(b.x0 - a.x1, a.x0 - b.x1)
            if hi - lo > TOL and 0 < gap < NOTE_H * h:
                cands.append((gap, a, b, lo, hi))
    cands.sort(key=lambda c: c[0])
    claimed: dict[int, list] = {}            # piece -> intervals already in a pair
    pairs = []

    def free_iv(p, lo, hi):
        return all(hi <= a + TOL or lo >= b - TOL for a, b in claimed.get(id(p), []))
    for gap, a, b, lo, hi in cands:
        if gap < COUPLE_H * h and free_iv(a, lo, hi) and free_iv(b, lo, hi):
            claimed.setdefault(id(a), []).append((lo, hi))
            claimed.setdefault(id(b), []).append((lo, hi))
            pairs.append((gap, a, b, lo, hi))
        else:
            notes.append(f"coupling neglected: lines at gap {gap:.3f} mm over {hi - lo:.3f} mm "
                         f"(near ({(a.x0 + a.x1) / 2:.2f}, {(a.y0 + a.y1) / 2:.2f}) mm)")
    # cut the paired lines at the ends of all their overlaps
    subs: dict[int, list] = {}
    for p in list(pieces):
        if id(p) not in claimed:
            continue
        q0, q1 = (p.x0, p.x1) if p.axis == "x" else (p.y0, p.y1)
        cuts = sorted({q0, q1, *[c for iv in claimed[id(p)] for c in iv if q0 + TOL < c < q1 - TOL]})
        sub = []
        for s0, s1 in zip(cuts, cuts[1:]):
            sub.append(Piece("line", s0, p.y0, s1, p.y1, "x", p.poly) if p.axis == "x" else
                       Piece("line", p.x0, s0, p.x1, s1, "y", p.poly))
        pieces.remove(p)
        pieces += sub
        subs[id(p)] = sub

    def part(p, lo):
        return next(s for s in subs[id(p)] if abs((s.x0 if s.axis == "x" else s.y0) - lo) < TOL)
    for gap, a, b, lo, hi in pairs:
        ca, cb = part(a, lo), part(b, lo)
        ca.kind, ca.other = "coupled", cb
        pieces.remove(cb)
        if abs(ca.width - cb.width) > TOL:
            notes.append(f"coupled lines of widths {ca.width:.3f} and {cb.width:.3f} mm: taken as the mean width")
    _connect(rec)
    return rec


def _terminals(p: Piece) -> list[Terminal]:
    """The attachment edges of a piece, in the pin order of its element."""
    r = p.rect
    edges = {(-1, 0): LineString([(p.x0, p.y0), (p.x0, p.y1)]), (1, 0): LineString([(p.x1, p.y0), (p.x1, p.y1)]),
             (0, -1): LineString([(p.x0, p.y0), (p.x1, p.y0)]), (0, 1): LineString([(p.x0, p.y1), (p.x1, p.y1)])}
    del r
    if p.kind == "line":
        a, b = ((-1, 0), (1, 0)) if p.axis == "x" else ((0, -1), (0, 1))
        return [Terminal(p, edges[a], a, p.width, 0), Terminal(p, edges[b], b, p.width, 1)]
    if p.kind == "coupled":
        q = p.other
        a, b = ((-1, 0), (1, 0)) if p.axis == "x" else ((0, -1), (0, 1))
        eq = {(-1, 0): LineString([(q.x0, q.y0), (q.x0, q.y1)]), (1, 0): LineString([(q.x1, q.y0), (q.x1, q.y1)]),
              (0, -1): LineString([(q.x0, q.y0), (q.x1, q.y0)]), (0, 1): LineString([(q.x0, q.y1), (q.x1, q.y1)])}
        return [Terminal(p, edges[a], a, p.width, 0), Terminal(p, edges[b], b, p.width, 1),
                Terminal(q, eq[a], a, q.width, 2), Terminal(q, eq[b], b, q.width, 3)]
    return [Terminal(p, edges[d], d, 0.0, k) for k, d in enumerate(edges)]


def _connect(rec: Recognition):
    b = rec.board
    terms = []
    for p in rec.pieces:
        terms += [(p, t) for t in _terminals(p)]
    # nodes: terminals of different pieces whose edges overlap (or a coupled line's own second strip)
    parent = list(range(len(terms)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    contact = {}                     # terminal index -> segment of the actual contact
    links = []
    count: dict[int, set] = {}
    for i, (pa, ta) in enumerate(terms):
        for j in range(i):
            pb, tb = terms[j]
            if pa is pb:
                continue
            g = ta.seg.intersection(tb.seg)
            if not g.is_empty and g.length > TOL and ta.out[0] == -tb.out[0] and ta.out[1] == -tb.out[1]:
                links.append((i, j, g))
                count.setdefault(i, set()).add(id(pb))
                count.setdefault(j, set()).add(id(pa))
    # a junction side that touches several pieces (two lines leaving one side) has no junction model
    rec.bad_junctions = set()
    for i, others in count.items():
        if terms[i][0].kind == "junction" and len(others) > 1:
            rec.bad_junctions.add(id(terms[i][0]))
    for i, j, g in links:
        if id(terms[i][0]) in rec.bad_junctions or id(terms[j][0]) in rec.bad_junctions:
            continue
        parent[find(i)] = find(j)
        contact[i] = contact.get(i, g)
        contact[j] = contact.get(j, g)
    groups = {}
    for i in range(len(terms)):
        groups.setdefault(find(i), []).append(i)
    node = 0
    for g in groups.values():
        if len(g) > 1:
            node += 1
            for i in g:
                terms[i][1].node = node
    rec.terms = terms
    # the line ends at a junction without a model are the ports of an EM region (left open)
    rec.em_ports = []
    for i, j, g in links:
        for a, bq in ((i, j), (j, i)):
            if id(terms[bq][0]) in rec.bad_junctions and terms[a][0].kind != "junction" and terms[a][1].node < 0:
                node += 1
                terms[a][1].node = node
                rec.em_ports.append(node)

    def at(pt: Point, i: int) -> bool:
        return terms[i][1].seg.distance(pt) < 1e-3

    def nearest(pt: Point):
        """The terminal of a line piece nearest to a point on its outline (a part attached at the side of a line
        near its end), or None."""
        c = [i for i, (p, t) in enumerate(terms) if p.kind != "junction" and p.rect.exterior.distance(pt) < 1e-3]
        if not c:
            return None
        i = min(c, key=lambda i: terms[i][1].seg.distance(pt))
        return i if terms[i][1].seg.distance(pt) <= max(terms[i][1].width, 0.2) else None

    free = [i for i, (_, t) in enumerate(terms) if t.node < 0]
    # ports, lumped parts, vias, gaps on free line ends
    rec.port_nodes = []
    for num, pt, z0, objs in b.ports:
        hit = [i for i in free if at(pt, i)] or [i for i, (_, t) in enumerate(terms) if t.node >= 0 and at(pt, i)]
        if not hit:
            rf = [f for r in getattr(rec, "rings", []) for f in r["feeds"] if f["poly"].distance(pt) < 1e-3]
            if rf:
                node += 1
                rf[0]["port_node"] = node
                rec.port_nodes.append((num, node, z0, objs))
                continue
            rec.notes.append(f"port {num}: not at a line end (left out)")
            continue
        i = hit[0]
        if terms[i][1].node < 0:
            node += 1
            terms[i][1].node = node
        rec.port_nodes.append((num, terms[i][1].node, z0, objs))
    rec.lumped = []
    for name, kind, p0, p1, props, objs in b.lumped:
        if kind == "series":
            ia = [i for i in range(len(terms)) if at(p0, i)] or [x for x in [nearest(p0)] if x is not None]
            ib = [i for i in range(len(terms)) if at(p1, i)] or [x for x in [nearest(p1)] if x is not None]
            if (ia and ib) and not (at(p0, ia[0]) and at(p1, ib[0])):
                rec.notes.append(f"{name}: attached at the side of the lines; connected to the nearest line ends")
            if not ia or not ib:
                rec.notes.append(f"{name}: lumped part not between two line ends (left out)")
                continue
            ns = []
            for i in (ia[0], ib[0]):
                if terms[i][1].node < 0:
                    node += 1
                    terms[i][1].node = node
                ns.append(terms[i][1].node)
            rec.lumped.append((name, "series", ns, props, objs))
        else:
            ia = [i for i in range(len(terms)) if at(p0, i)] or [x for x in [nearest(p0)] if x is not None]
            if not ia:
                rec.notes.append(f"{name}: shunt part not at a line end (left out)")
                continue
            i = ia[0]
            if terms[i][1].node < 0:
                node += 1
                terms[i][1].node = node
            rec.lumped.append((name, "shunt", [terms[i][1].node], props, objs))
    rec.via_terms = []
    for g, name in b.vias:
        hit = [i for i in range(len(terms)) if terms[i][1].seg.distance(g) < 1e-3 or
               terms[i][0].rect.buffer(-1e-4).intersects(g)]
        hit = [i for i in hit if terms[i][0].kind == "line"]
        if not hit:
            rec.notes.append(f"{name}: via not at a line (left out)")
            continue
        # the end of the line nearest to the via
        i = min(hit, key=lambda i: terms[i][1].seg.distance(g.centroid))
        rec.via_terms.append((i, g, name))
    # gaps between facing free ends
    rec.gaps = []
    free = [i for i, (_, t) in enumerate(terms) if t.node < 0 and t.piece.kind in ("line", "coupled")]
    for x, i in enumerate(free):
        for j in free[x + 1:]:
            ta, tb = terms[i][1], terms[j][1]
            if ta.out[0] != -tb.out[0] or ta.out[1] != -tb.out[1]:
                continue
            d = ta.seg.distance(tb.seg)
            # facing: the gap is along out, the edges overlap across it
            ca, cb = np.array(ta.seg.centroid.coords[0]), np.array(tb.seg.centroid.coords[0])
            along = (cb - ca) @ np.array(ta.out)
            across = abs((cb - ca) @ np.array((-ta.out[1], ta.out[0])))
            if 0 < along < GAP_H * rec.board.h and across < 0.5 * min(ta.width, tb.width) and abs(d - along) < 1e-6:
                for t in (ta, tb):
                    node += 1
                    t.node = node
                rec.gaps.append((ta, tb, along))
    rec.n_nodes = node
    rec.ring_elements = []
    for r in getattr(rec, "rings", []):
        _ring_elements(rec, r)


# ------------------------------------------------------------------------------------------------ circuit
@dataclass
class Element:
    type: str
    name: str
    props: dict                     # dimension props in mm (float) or text
    nodes: list                     # node per pin (0 = ground)
    dirs: list                      # physical direction out of the element at each pin
    centre: tuple
    objects: list = field(default_factory=list)


def elements(rec: Recognition) -> list[Element]:
    out: list[Element] = []
    count: dict[str, int] = {}

    def name(prefix):
        count[prefix] = count.get(prefix, 0) + 1
        return f"{prefix}{count[prefix]}"
    by_piece: dict[int, list[Terminal]] = {}
    for p, t in rec.terms:
        by_piece.setdefault(id(p), []).append(t)
    via_at = {id(rec.terms[i][1]): (g, n) for i, g, n in rec.via_terms}
    # regions without a model (junctions with no or several arms per side): line ends there are EM ports
    unrec = []
    for p in rec.pieces:
        if p.kind == "junction" and (id(p) in rec.bad_junctions or
                                     not any(t.node >= 0 for t in by_piece.get(id(p), []))):
            unrec.append(p.rect)
    for p in rec.pieces:
        ts = by_piece[id(p)]
        cen = ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)
        if p.kind == "line":
            ends = [t for t in ts]
            open_ = [t for t in ends if t.node < 0]
            shorted = [t for t in ends if id(t) in via_at]
            if len(open_) == 2:
                rec.notes.append(f"line at ({cen[0]:.2f}, {cen[1]:.2f}) mm is connected to nothing (left out)")
                continue
            for t in list(open_):
                if any(t.seg.distance(g) < 1e-3 and t.seg.intersection(g).length > TOL for g in unrec):
                    rec.n_nodes += 1
                    t.node = rec.n_nodes
                    rec.em_ports.append(t.node)
                    open_.remove(t)
                    rec.notes.append(f"line at ({cen[0]:.2f}, {cen[1]:.2f}) mm ends at a region without a circuit "
                                     "model: that end is a port of an EM block (left open)")
            if len(open_) == 1 and not shorted:
                t_in = next(t for t in ends if t.node >= 0)
                out.append(Element("MLOC", name("MSO"), {"W": p.width, "L": p.length}, [t_in.node], [t_in.out], cen))
                continue
            if len(open_) == 1 and shorted and shorted[0] is open_[0]:
                t_in = next(t for t in ends if t.node >= 0)
                g, vn = via_at[id(open_[0])]
                gw = min(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1])
                gl = max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1])
                if gw < 1e-3 and abs(gl - p.width) < 1e-3:       # a wall of the line width at its end: MLSC
                    out.append(Element("MLSC", name("MSS"), {"W": p.width, "L": p.length}, [t_in.node], [t_in.out],
                                       cen, [vn]))
                    continue
                # a round via: the line and a via part at its end
                rec.n_nodes += 1
                open_[0].node = rec.n_nodes
            nodes = [t.node for t in ends]
            out.append(Element("MLIN", name("MS"), {"W": p.width, "L": p.length}, nodes, [t.out for t in ends], cen))
        elif p.kind == "coupled":
            q = p.other
            w = (p.width + q.width) / 2
            if p.axis == "x":
                s = max(q.y0 - p.y1, p.y0 - q.y1)
            else:
                s = max(q.x0 - p.x1, p.x0 - q.x1)
            ts = sorted(ts, key=lambda t: t.pin)
            out.append(Element("MCLIN", name("MSC"), {"W": w, "S": s, "L": p.length}, [t.node for t in ts],
                               [t.out for t in ts], ((p.x0 + q.x1) / 2, (p.y0 + q.y1) / 2)))
            out[-1].other = (p, q)
        else:
            if id(p) in rec.bad_junctions:
                rec.notes.append(f"junction at ({p.x0:.2f}..{p.x1:.2f}, {p.y0:.2f}..{p.y1:.2f}) mm has several lines "
                                 "leaving one side: no circuit model (needs an EM block)")
                rec.unrecognized_area += (p.x1 - p.x0) * (p.y1 - p.y0)
                continue
            arms = [t for t in ts if t.node >= 0]
            # width of the arm: the width of the piece attached there
            for t in arms:
                t.width = next((u.width for _, u in rec.terms if u.node == t.node and u is not t and u.width), 0)
            dirs = [t.out for t in arms]
            jx, jy = p.x1 - p.x0, p.y1 - p.y0
            if len(arms) == 2 and dirs[0] == (-dirs[1][0], -dirs[1][1]):
                # a short wide piece between two lines: a line along the arms
                ax = "x" if dirs[0][0] else "y"
                L, W = (jx, jy) if ax == "x" else (jy, jx)
                out.append(Element("MLIN", name("MS"), {"W": W, "L": L}, [t.node for t in arms], dirs,
                                   ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)))
            elif len(arms) == 2:
                tri = next((t for t in getattr(rec, "miters", []) if t.intersection(p.rect).area > 1e-9), None)
                w = max(jx, jy)
                if tri is not None:
                    leg = math.sqrt(2 * tri.area)
                    if abs(leg / w - 1) > 0.05:
                        rec.notes.append(f"mitered bend at ({p.x0:.2f}, {p.y0:.2f}) mm: cut {leg:.3f} mm = "
                                         f"{100 * leg / w:.0f} % of the width; the MMBEND model assumes Hertzon's "
                                         "own miter (100 %)")
                    out.append(Element("MMBEND", name("MB"), {"W": w}, [t.node for t in arms], dirs,
                                       ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)))
                else:
                    out.append(Element("MCORN", name("MCN"), {"W": w}, [t.node for t in arms], dirs,
                                       ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)))
            elif len(arms) == 3:
                br = next(t for t in arms if not any(u.out == (-t.out[0], -t.out[1]) for u in arms))
                main = [t for t in arms if t is not br]
                out.append(Element("MTEE", name("MT"), {"W1": main[0].width, "W2": main[1].width, "W3": br.width},
                                   [main[0].node, main[1].node, br.node], [main[0].out, main[1].out, br.out],
                                   ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)))
            elif len(arms) == 4:
                order = sorted(arms, key=lambda t: [(-1, 0), (0, 1), (1, 0), (0, -1)].index(t.out))
                out.append(Element("MCROSS", name("MX"), {f"W{k + 1}": t.width for k, t in enumerate(order)},
                                   [t.node for t in order], [t.out for t in order],
                                   ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)))
            elif len(arms) == 1:
                t = arms[0]
                ax = "x" if t.out[0] else "y"
                L, W = (jx, jy) if ax == "x" else (jy, jx)
                out.append(Element("MLOC", name("MSO"), {"W": W, "L": L}, [t.node], [t.out],
                                   ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)))
            else:
                rec.notes.append(f"region at ({p.x0:.2f}..{p.x1:.2f}, {p.y0:.2f}..{p.y1:.2f}) mm "
                                 f"({p.x1 - p.x0:.3g} x {p.y1 - p.y0:.3g} mm) has no line model (patch or wide area): "
                                 "not recognized (needs an EM block)")
                rec.unrecognized_area += (p.x1 - p.x0) * (p.y1 - p.y0)
    # width steps: two line ends meeting with different widths
    seen = set()
    for i, (pa, ta) in enumerate(rec.terms):
        if ta.node < 0 or pa.kind == "junction":
            continue
        for j, (pb, tb) in enumerate(rec.terms):
            if j <= i or tb.node != ta.node or pb.kind == "junction" or pb is pa:
                continue
            if (ta.node) in seen:
                continue
            if abs(ta.width - tb.width) > TOL:
                seen.add(ta.node)
                rec.n_nodes += 1
                n2 = rec.n_nodes
                old = tb.node
                # tb's element gets the new node
                for e in out:
                    for k, nn in enumerate(e.nodes):
                        if nn == old and e.dirs[k] == tb.out and _owns(e, pb):
                            e.nodes[k] = n2
                c = tb.seg.centroid
                out.append(Element("MSTEP", name("MSP"), {"W1": ta.width, "W2": tb.width}, [old, n2],
                                   [tb.out, ta.out], (c.x, c.y)))
    for ta, tb, s in rec.gaps:
        c = ta.seg.centroid
        out.append(Element("MGAP", name("MG"), {"W1": ta.width, "W2": tb.width, "S": s}, [ta.node, tb.node],
                           [(-ta.out[0], -ta.out[1]), (-tb.out[0], -tb.out[1])], (c.x, c.y)))
    for i, g, vn in rec.via_terms:
        t = rec.terms[i][1]
        if any(vn in e.objects for e in out):
            continue
        d = min(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1])
        c = g.centroid
        out.append(Element("MVIA", name("V"), {"D": d, "N": "1", "P": "1mm"}, [t.node], [(-t.out[0], -t.out[1])],
                           (c.x, c.y), [vn]))
    for name_, kind, ns, props, objs in rec.lumped:
        typ, pr = _lumped_type(props)
        if kind == "series":
            out.append(Element(typ, name_, pr, ns, [None, None], (0, 0), objs))
        else:
            out.append(Element(typ, name_, pr, [ns[0], 0], [None, None], (0, 0), objs))
    for num, n, z0, objs in rec.port_nodes:
        out.append(Element("PORT", f"Port{num}", {"Num": str(num), "Z0": z0}, [n], [None], (0, 0), objs))
    out += rec.ring_elements
    # lines far wider than the line models allow are patches or radiators: no circuit model
    lam0 = 299792458.0 / rec.board.fmax * 1e3 if rec.board.fmax else 0.0
    keep = []
    for e in out:
        w = e.props.get("W") if e.type in ("MLIN", "MLOC", "MLSC", "MCLIN") else None
        if isinstance(w, float) and ((lam0 and w > 0.1 * lam0) or w > 20 * rec.board.h):
            rec.notes.append(f"{e.type} at ({e.centre[0]:.2f}, {e.centre[1]:.2f}) mm is {w:.3g} mm wide "
                             f"({w / lam0 if lam0 else 0:.2f} free-space wavelengths, W/h = {w / rec.board.h:.3g}): a "
                             "patch or radiator outside the line models; not recognized (an EM block in a full "
                             "implementation)")
            rec.unrecognized_area += w * e.props.get("L", 0.0)
            continue
        keep.append(e)
    return keep


def _owns(e: Element, p: Piece) -> bool:
    x, y = e.centre
    if e.type == "MCLIN":
        return p in e.other
    return p.x0 - TOL <= x <= p.x1 + TOL and p.y0 - TOL <= y <= p.y1 + TOL


def _lumped_type(p: dict):
    if p.get("Model") == "Series RLC":
        return "SRLC", {"R": p.get("Resistance", "0ohm"), "L": p.get("Inductance", "0nH"),
                        "C": p.get("Capacitance", "1pF")}
    use = [k for k, n in (("R", "UseResistance"), ("L", "UseInductance"), ("C", "UseCapacitance"))
           if p.get(n) == "True"]
    key = {"R": "Resistance", "L": "Inductance", "C": "Capacitance"}
    if len(use) == 1:
        return use[0], {use[0]: p[key[use[0]]]}
    return "PRLC", {k: p.get(key[k], "") for k in "RLC"}


# ---- link: dimensions to the 3D design's variables ---------------------------------------------------------
IDENT = re.compile(r"[A-Za-z_]\w*")
SIZE_KEYS = ("XSize", "YSize", "ZSize", "Radius", "Height", "Width", "Length")


def link_variables(project: Project, design: Design, elems: list[Element], board: Board) -> dict:
    """element -> {"objects": [...], "dims": {prop: variable or value}}: the variable is one of the 3D design
    whose value equals the dimension and that a size of an overlapping object uses."""
    ev = design.evaluator(project)
    vals = {}
    for k in design.variables:
        try:
            vals[k] = ev.length(k) * 1e3
        except Exception:  # noqa: BLE001
            pass
    objs = {o.name: o for o in design.objects}

    def used(o):
        out = []
        for cmd in o.history:
            for key in SIZE_KEYS:
                v = cmd.props.get(key, "")
                out += [x for x in IDENT.findall(v) if x in vals]
        return out
    # the objects behind a piece: those whose own (pre-boolean) footprint overlaps it; consumed tools included
    table = {}
    for e in elems:
        cand = list(e.objects)
        if e.type in ("MLIN", "MLOC", "MLSC", "MCLIN", "MTEE", "MCORN", "MCROSS", "MSTEP", "MGAP"):
            top = {n for _, objs in board.copper for n in objs}
            cand += [n for n in _objects_at(design, ev, e) if n in top or objs[n].consumed_by in top]
        dims = {}
        pool = []
        for n in cand:
            if n in objs:
                pool += used(objs[n])
        for prop, v in e.props.items():
            if not isinstance(v, float):
                continue
            hit = [x for x in dict.fromkeys(pool) if abs(vals[x] - v) < 1e-5]
            if not hit:
                # no object names it (a ring, a feed polygon): a design variable of exactly this value, if only one
                same = [x for x in vals if abs(vals[x] - v) < 1e-5]
                hit = same if len(same) == 1 else []
            dims[prop] = hit[0] if hit else v
        table[e.name] = {"type": e.type, "objects": sorted(set(cand)), "dims": dims}
    return table


def _objects_at(design: Design, ev, e: Element) -> list[str]:
    """Rectangles (also consumed tools) whose footprint contains the element centre (each strip of a coupled
    pair)."""
    if e.type == "MCLIN":
        out = []
        for p in e.other:
            out += _objects_at(design, ev, Element("MLIN", "", {}, [], [], ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2)))
        return out
    out = []
    x, y = e.centre
    for o in design.objects:
        c = o.history[0] if o.history else None
        if c is None or c.kind != "CreateRectangle" or c.props.get("Axis", "Z") != "Z":
            continue
        try:
            px, py, _ = ev.point(c.props["Position"])
            sx, sy = ev.length(c.props["XSize"]), ev.length(c.props["YSize"])
        except Exception:  # noqa: BLE001
            continue
        x0, x1 = sorted((px * 1e3, (px + sx) * 1e3))
        y0, y1 = sorted((py * 1e3, (py + sy) * 1e3))
        if x0 - TOL <= x <= x1 + TOL and y0 - TOL <= y <= y1 + TOL:
            out.append(o.name)
    return out


# ---- schematic -------------------------------------------------------------------------------------------
SCALE = 12          # schematic units per mm


def _phys_dir(a, b):
    """Physical direction between two schematic points (schematic y down)."""
    dx, dy = b[0] - a[0], a[1] - b[1]
    if abs(dx) >= abs(dy):
        return (1 if dx >= 0 else -1), 0
    return 0, (1 if dy > 0 else -1)


def _orient(c: CK.Component, e: Element) -> None:
    """Rotation and mirror of the symbol so that its pins point as the element's arms (as layout_design reads
    them)."""
    best = None
    for mirror in (False, True):
        for rot in (0, 90, 180, 270):
            c.rot, c.mirror = rot, mirror
            pp = CK.pins(c)
            cen = (c.x, c.y)
            ok = 0
            if e.type in ("MLIN", "MSTEP", "MGAP"):
                ok = _phys_dir(pp[1], pp[0]) == tuple(e.dirs[0])
            elif e.type in ("MLOC", "MLSC"):
                ok = _phys_dir(pp[0], cen) == (-e.dirs[0][0], -e.dirs[0][1])
            elif e.type in ("MTEE", "MCORN", "MCROSS", "MMBEND"):
                ok = all(_phys_dir(cen, pp[k]) == tuple(e.dirs[k]) for k in range(len(pp)))
            elif e.type == "MCLIN":
                # pins 0/1 strip A (along), 2/3 strip B: B lies on the side _dir(pp[0], pp[2])
                p, q = e.other
                side = (1, 0) if q.x0 >= p.x1 - TOL and p.axis == "y" else (-1, 0) if p.axis == "y" else \
                    ((0, 1) if q.y0 >= p.y1 - TOL else (0, -1))
                ok = _phys_dir(pp[1], pp[0]) == tuple(e.dirs[0]) and _phys_dir(pp[0], pp[2]) == side
            elif e.type == "MVIA":
                ok = True
            else:
                ok = rot == 0 and not mirror
            if ok:
                return
            best = best or (rot, mirror)
    c.rot, c.mirror = 0, False


def _fmt(v):
    return f"{v:.6g}mm" if isinstance(v, float) else str(v)


def to_circuit(project: Project, design: Design, rec: Recognition, name: str = "") -> tuple[Design, dict]:
    elems = elements(rec)
    table = link_variables(project, design, elems, rec.board)
    # an element whose dimensions are all variables "<P>_<prop>" of one prefix P gets the name P (Hertzon's
    # convention of generated layouts): the link reads the same on both sides
    used = {e.name for e in elems}
    for e in elems:
        dims = table[e.name]["dims"]
        if not dims or not all(isinstance(v, str) for v in dims.values()):
            continue
        pre = {v.rsplit("_", 1)[0] for v in dims.values()}
        if len(pre) == 1 and all(v == f"{next(iter(pre))}_{k}" for k, v in dims.items()):
            new = next(iter(pre))
            if new not in used and re.match(r"^[A-Za-z]\w*$", new):
                used.discard(e.name)
                used.add(new)
                table[new] = table.pop(e.name)
                e.name = new
    b = rec.board
    d = Design(name=name or f"{design.name}_Circuit", kind="circuit", units="mm")
    d.variables = dict(design.variables)
    d.settings[CK.AUTO] = "False"
    sub = dict(CK.TYPES["SUBST"].props)
    sub.update(Er=f"{b.er:g}", H=f"{b.h:.6g}mm", T=f"{b.t:.6g}mm", TanD=f"{b.tand:g}", Cond=f"{b.cond:g}")
    comps = [CK.Component("Sub1", "SUBST", 0, 0, 0, sub)]
    # placement: element centre scaled, y down
    xs = [e.centre[0] for e in elems if e.centre != (0, 0)]
    ys = [e.centre[1] for e in elems if e.centre != (0, 0)]
    xm, ym = (min(xs), max(ys)) if xs else (0, 0)

    def grid(v):
        return int(round(v / CK.GRID)) * CK.GRID
    pos_of_node: dict[int, list] = {}
    placed = []
    for e in elems:
        props = {}
        if e.type in CK.LINE_TYPES or e.type == "MVIA":
            props["Subst"] = "Sub1"
        dims = table[e.name]["dims"]
        for k, v in e.props.items():
            props[k] = _fmt(dims.get(k, v)) if isinstance(v, float) else v
        x, y = e.centre
        c = CK.Component(e.name, e.type, grid((x - xm) * SCALE) + 200, grid((ym - y) * SCALE) + 200, 0, props)
        _orient(c, e)
        comps.append(c)
        placed.append((c, e))
    # lumped, ports, vias placed next to the first pin's node afterwards: collect node pin positions
    for c, e in placed:
        if e.centre == (0, 0):
            continue
        for k, p in enumerate(CK.pins(c)):
            if k < len(e.nodes) and e.nodes[k] > 0:
                pos_of_node.setdefault(e.nodes[k], []).append(p)
    labels = []
    for c, e in placed:
        if e.centre != (0, 0):
            continue
        n0 = e.nodes[0]
        base = pos_of_node.get(n0, [(0, 0)])[0]
        c.x, c.y = base[0], base[1] + 60
        if e.type == "PORT":
            c.y = base[1] + 40
            c.x = base[0] - 60
    # no two pins of different nodes on the same grid point (they would connect): move the later symbol down
    taken: dict[tuple, int] = {}
    for c, e in placed:
        for _ in range(200):
            pp = CK.pins(c)
            if all(taken.get(tuple(p), e.nodes[k] if k < len(e.nodes) else -9) == (e.nodes[k] if k < len(e.nodes)
                   else -9) for k, p in enumerate(pp)) and (c.x, c.y) not in taken:
                break
            c.y += CK.GRID
        for k, p in enumerate(CK.pins(c)):
            taken[tuple(p)] = e.nodes[k] if k < len(e.nodes) else -9
        taken[(c.x, c.y)] = -7
    # connections: one net label per node at every pin (robust; wires are a cosmetic step for later)
    for c, e in placed:
        for k, p in enumerate(CK.pins(c)):
            if k >= len(e.nodes):
                continue
            n = e.nodes[k]
            if n == 0:
                labels.append(CK.Component(f"G_{c.name}_{k}", "GND", p[0], p[1] + 10, 0, {}))
            elif n > 0:
                labels.append(CK.Component(f"N_{c.name}_{k}", "LABEL", p[0], p[1], 0, {"Net": f"n{n}"}))
    d.components = comps + labels
    d.setups = [circuit_setup()]
    return d, table


def compare_netlists(a: Design, b: Design) -> list[str]:
    """Differences of two circuits as multisets of (type, rounded dimensions)."""
    def key(d: Design, project=None):
        ev = d.evaluator(project)
        comps, _, _, _ = CK.netlist(d.components, d.wires, d.settings.get(CK.AUTO, "True") != "False")
        out = []
        for c in comps:
            if c.type in ("SUBST", "LABEL", "GND", "PORT"):
                continue
            dims = []
            for k in sorted(c.props):
                if k in ("Subst", "Num", "N", "P") or k.startswith("_"):
                    continue
                try:
                    v = ev.length(c.props[k]) * 1e3 if c.type.startswith("M") else ev.value(c.props[k])
                    dims.append((k, round(v, 3)))
                except Exception:  # noqa: BLE001
                    dims.append((k, c.props[k]))
            out.append((c.type, tuple(dims)))
        return sorted(out, key=str)
    ka, kb = key(a), key(b)
    from collections import Counter
    ca, cb = Counter(ka), Counter(kb)
    diff = []
    for k in ca - cb:
        diff.append(f"only in original: {k}")
    for k in cb - ca:
        diff.append(f"only in recognized: {k}")
    return diff


SOURCE = "CircuitOf"              # circuit setting: the name of the 3D design it was recognized from
TABLE = "CircuitOfTable"          # circuit setting: element -> 3D objects and variables (JSON)


def circuit_from_design(project: Project, design: Design, name: str | None = None) -> tuple[Design, list[str]]:
    """A new circuit design recognized from the planar 3D design; the list says what was not recognized,
    neglected or approximated (nothing is guessed silently)."""
    import json
    if getattr(design, "kind", "3d") == "circuit":
        raise ValueError(f"{design.name} is a circuit design")
    board = extract(project, design)
    if board.h <= 0 or not board.copper:
        raise ValueError("no substrate with copper on top found: the recognition takes planar microstrip "
                         "(conductors on top of one dielectric layer over ground)")
    rec = recognize(board)
    d, table = to_circuit(project, design, rec, name or "")
    d.settings[SOURCE] = design.name
    d.settings[TABLE] = json.dumps(table)
    notes = list(rec.notes)
    if rec.copper_area > 0 and rec.unrecognized_area > 0:
        notes.append(f"{100 * rec.unrecognized_area / rec.copper_area:.1f} % of the copper area has no circuit "
                     "model; the line ends there are left open")
    return d, notes
