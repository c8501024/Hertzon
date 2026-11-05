"""Schematic to 3D layout and back.

layout_design() turns a circuit design into a new 3D design: the substrate with its ground plane, the microstrip
(or stripline) traces placed end to end as the schematic connects them, the lumped R, L and C parts as Lumped RLC
sheets in a gap of the trace (or from the trace to ground for a shunt part) and the power ports as Lumped Ports
from the trace to ground. Every dimension is a design variable named after its part ("MS1_W", "MS1_L", "Sub1_H"),
so the layout can be tuned or optimized in 3D and update_schematic() copies the values back into the schematic.

The physical direction of a part follows its orientation in the schematic (right = +x, up = +y); the layout
starts at the first power port. Parts that have no layout are reported: ideal lines are converted into
microstrip of the same impedance and electrical length, a DC block becomes a direct connection, Touchstone and
3D blocks, attenuators, coaxial lines and DC feeds are left out.
"""
from __future__ import annotations

import json
import re
import math
from collections import Counter

from . import circuit as CK
from . import schema
from .design import Assignment, Command, Design, Project, Setup, Sweep
from .materials import Material

LINE2 = ("MLIN", "SLIN", "TLIN", "CPWLIN")
STUBS = ("MLOC", "MLSC", "TLOC", "TLSC")
LUMPED = ("R", "L", "C", "SRLC", "PRLC")
SKIP = {"ATTEN": "the attenuator has no layout and was left out",
        "COAX": "a coaxial line has no planar layout and was left out",
        "DCFEED": "the DC feed is open at RF and was left out",
        "SNP": "Touchstone blocks have no layout and were left out",
        "EM": "3D design blocks have no layout and were left out"}
LINK = "LayoutOf"                # design setting of a generated layout: the name of its circuit design
GAP = "1mm"                      # gap of a series lumped part (pad to pad)
MARGIN = "5mm"                   # substrate beyond the traces where no port sits on the board edge


class Lin(dict):
    """A linear combination of design variables {name: coefficient}: coordinates as exact expressions."""

    def __add__(self, o: Lin) -> Lin:
        r = Lin(self)
        for k, v in o.items():
            r[k] = r.get(k, 0.0) + v
            if abs(r[k]) < 1e-12:
                del r[k]
        return r

    def __sub__(self, o: Lin) -> Lin:
        return self + o * -1

    def __mul__(self, s: float) -> Lin:
        return Lin({k: v * s for k, v in self.items()}) if s else Lin()

    def text(self) -> str:
        if not self:
            return "0"
        out = ""
        for k, v in sorted(self.items()):
            sign = "-" if v < 0 else "+"
            a = abs(v)
            term = k if abs(a - 1) < 1e-12 else f"{a:g}*{k}"
            out += (("-" if sign == "-" else "") if not out else sign) + term
        return out

    def num(self, vals: dict[str, float]) -> float:
        return sum(v * vals[k] for k, v in self.items())


def var(name: str) -> Lin:
    return Lin({name: 1.0})


class Pt:
    def __init__(self, x: Lin, y: Lin):
        self.x, self.y = x, y

    def __add__(self, o: Pt) -> Pt:
        return Pt(self.x + o.x, self.y + o.y)

    def __sub__(self, o: Pt) -> Pt:
        return Pt(self.x - o.x, self.y - o.y)


def along(d: tuple[int, int], length: Lin) -> Pt:
    return Pt(length * d[0], length * d[1])


def _dir(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    """Physical axis direction from schematic point a towards b (schematic y points down)."""
    dx, dy = b[0] - a[0], a[1] - b[1]
    if abs(dx) >= abs(dy):
        return (1 if dx >= 0 else -1), 0
    return 0, (1 if dy > 0 else -1)


def _perp(d):
    return -d[1], d[0]


class _Rect:
    """A conductor rectangle in the board plane from point p along d, of length ln and width w (centred)."""

    def __init__(self, name, p: Pt, d, ln: Lin, w: Lin, nodes=()):
        self.name, self.p, self.d, self.ln, self.w, self.nodes = name, p, d, ln, w, tuple(nodes)


class LayoutError(Exception):
    pass


def layout_design(project: Project, circuit: Design, name: str | None = None) -> tuple[Design, list[str]]:
    """A new 3D design with the physical layout of the circuit design; the list says what was left out or
    approximated."""
    from . import microstrip as MS
    warnings: list[str] = []
    comps, nodes, _, junctions = CK.netlist(circuit.components, circuit.wires,
                                            circuit.settings.get(CK.AUTO, "True") != "False")
    # an automatic junction takes its widths from the lines that meet there, and a plain object name
    arm_width = {}
    label = {}
    by_name = {c.name: c for c in circuit.components}
    for i, j in enumerate(junctions, 1):
        names = {"MCORN": ("W",), "MSTEP": ("W1", "W2"), "MTEE": ("W1", "W2", "W3"),
                 "MCROSS": ("W1", "W2", "W3", "W4")}[j.type]
        for prop, (arm, _) in zip(names, j.arms if j.type != "MCORN" else j.arms[:1]):
            arm_width[(j.name, prop)] = arm
        label[j.name] = f"{CK.TYPES[j.type].label.split()[-1]}{i}"
    ev = circuit.evaluator(project)

    # ---- substrate
    subs = {c.name: c for c in comps if c.type == "SUBST"}
    used = Counter(c.props.get("Subst", "") for c in comps if c.type in CK.LINE_TYPES)
    if used:
        sname = used.most_common(1)[0][0]
        if len(used) > 1:
            warnings.append(f"the lines use several substrates: the layout is built on {sname}")
    else:
        sname = next(iter(subs), "")
    if sname not in subs:
        raise LayoutError("the circuit has no substrate: place a Substrate (Transmission Lines) to define the board")
    sub = subs[sname].props
    er, tand, cond = ev.value(sub["Er"]), ev.value(sub["TanD"]), ev.value(sub["Cond"])
    h_m, t_m = ev.length(sub["H"]), ev.length(sub["T"])
    lines = [c for c in comps if c.type in CK.LINE_TYPES]
    strip = bool(lines) and all(c.type == "SLIN" for c in lines)
    if not strip and any(c.type == "SLIN" for c in lines):
        warnings.append("striplines and microstrip lines are mixed: the striplines are laid out as microstrip")

    d = Design(name=name or f"{circuit.name}_Layout", units=circuit.units)
    d.variables.update({k: v for k, v in circuit.variables.items()})
    H, T = f"{sname}_H", f"{sname}_T"
    d.variables[H] = sub["H"]
    d.variables[T] = sub["T"]
    d.variables["Margin"] = MARGIN

    def setvar(n: str, value: str) -> Lin:
        d.variables[n] = value
        return var(n)

    def wvar(c: CK.Component, prop: str) -> Lin:
        """The variable of a width: an automatic junction uses the one of its line."""
        arm = arm_width.get((c.name, prop))
        if arm is not None:
            return setvar(f"{arm}_W", by_name[arm].props["W"])
        return setvar(f"{c.name}_{prop}", c.props[prop])

    def oname(c: CK.Component) -> str:
        return label.get(c.name, c.name)

    # ---- walk the netlist from the first power port
    ports = CK.port_order(comps)
    placed: set[str] = set()
    pos: dict[int, Pt] = {}
    rects: list[_Rect] = []
    gaps: list[tuple] = []            # (component, start point, direction, gap, node a, node b)
    shunts: list[tuple] = []          # (component, node)
    shorts: list[tuple] = []          # (component, end point, direction, width)
    node_dirs: dict[int, list[tuple]] = {}   # node -> [(direction away from the node, width)]
    same: list[tuple[int, int]] = []
    pts: list[tuple] = []             # mitered bends: (name, centre, arm 1, arm 2, width, nodes)

    start = None
    if ports:
        start = nodes[ports[0].name][0]
    elif lines:
        start = nodes[lines[0].name][0]
    if not start:
        raise LayoutError("nothing to lay out: connect a power port to a line")
    pos[start] = Pt(Lin(), Lin())
    queue = [start]

    def reach(node: int, p: Pt, comp: CK.Component):
        if node == 0:
            return
        if node in pos:
            vals = _values(d, project)
            if abs(pos[node].x.num(vals) - p.x.num(vals)) + abs(pos[node].y.num(vals) - p.y.num(vals)) > 1e-7:
                warnings.append(f"{comp.name}: the layout does not close at a loop of the circuit")
            return
        pos[node] = p
        queue.append(node)

    def trace(n: str, p: Pt, dd, ln: Lin, w: Lin, na: int, nb: int):
        rects.append(_Rect(n, p, dd, ln, w, (na, nb)))
        node_dirs.setdefault(na, []).append((dd, w))
        node_dirs.setdefault(nb, []).append(((-dd[0], -dd[1]), w))

    def mm(v: float) -> str:
        return f"{v * 1e3:.6g}mm"

    via_parts: list = []
    while queue:
        node = queue.pop(0)
        here = pos[node]
        for c in comps:
            if c.name in placed or node not in nodes[c.name]:
                continue
            k = nodes[c.name].index(node)
            t = c.type
            pp = CK.pins(c)
            if t in ("GND", "LABEL", "SUBST"):
                continue
            placed.add(c.name)
            if t == "PORT":
                continue
            if t in SKIP:
                warnings.append(f"{c.name}: {SKIP[t]}")
                continue
            if t == "MVIA":
                via_parts.append((c, node))
                continue
            if t in LINE2 or t == "DCBLOCK" or t in LUMPED:
                other = 1 - k
                dd = _dir(pp[k], pp[other])
                na, nb = nodes[c.name][k], nodes[c.name][other]
                if t in LUMPED and nb == 0:
                    shunts.append((c, na))
                    continue
                if t == "DCBLOCK":
                    warnings.append(f"{c.name}: the DC block is a direct connection in the layout")
                    reach(nb, here, c)
                    same.append((na, nb))
                    continue
                if t in LUMPED:
                    g = setvar(f"{c.name}_Gap", GAP)
                    gaps.append((c, here, dd, g, na, nb))
                    reach(nb, here + along(dd, g), c)
                    continue
                if t == "TLIN":
                    w_m, l_m = _synth(c, ev, er, h_m, t_m, MS)
                    warnings.append(f"{c.name}: the ideal line is laid out as microstrip of the same impedance and "
                                    "electrical length")
                    w, ln = setvar(f"{c.name}_W", mm(w_m)), setvar(f"{c.name}_L", mm(l_m))
                else:
                    w, ln = setvar(f"{c.name}_W", c.props["W"]), setvar(f"{c.name}_L", c.props["L"])
                if t == "CPWLIN":
                    warnings.append(f"{c.name}: the coplanar line is laid out as its centre strip only; draw the "
                                    "coplanar grounds yourself")
                if nb == 0:             # a line to ground: shorted at its far end by a via, like a short stub
                    rects.append(_Rect(c.name, here, dd, ln, w, (na,)))
                    node_dirs.setdefault(na, []).append((dd, w))
                    shorts.append((c, here + along(dd, ln), dd, w))
                    continue
                trace(c.name, here, dd, ln, w, na, nb)
                reach(nb, here + along(dd, ln), c)
            elif t in STUBS:
                dd = _dir(pp[0], (c.x, c.y))
                if t in ("TLOC", "TLSC"):
                    w_m, l_m = _synth(c, ev, er, h_m, t_m, MS)
                    if t == "TLSC":             # the via adds inductance: the line is shorter by as much
                        from ..solver import tlines as TLS
                        z_s, ee_s = TLS.microstrip_static(w_m, h_m, t_m, er)
                        dl = TLS.via_inductance(w_m, h_m) * TLS.C0 / (math.sqrt(ee_s) * z_s)
                        l_m = max(l_m - dl, 0.1 * l_m)
                    warnings.append(f"{c.name}: the ideal stub is laid out as microstrip of the same impedance and "
                                    "electrical length")
                    w, ln = setvar(f"{c.name}_W", mm(w_m)), setvar(f"{c.name}_L", mm(l_m))
                else:
                    w, ln = setvar(f"{c.name}_W", c.props["W"]), setvar(f"{c.name}_L", c.props["L"])
                rects.append(_Rect(c.name, here, dd, ln, w, (node,)))
                node_dirs.setdefault(node, []).append((dd, w))
                if t in ("MLSC", "TLSC"):
                    shorts.append((c, here + along(dd, ln), dd, w))
            elif t == "MTEE":
                dm = _dir(pp[0], pp[1])
                db = _dir((c.x, c.y), pp[2])
                w1, w2, w3 = (wvar(c, f"W{j}") for j in (1, 2, 3))
                vals = _values(d, project)
                wm = max(w1, w2, key=lambda q: q.num(vals))
                rel = [along(dm, w3 * -0.5), along(dm, w3 * 0.5), along(db, wm * 0.5)]
                centre = here - rel[k]
                n3 = nodes[c.name]
                rects.append(_Rect(oname(c), centre + rel[0], dm, w3, wm, n3))
                for j, (dd, w) in enumerate(((dm, w1), ((-dm[0], -dm[1]), w2), ((-db[0], -db[1]), w3))):
                    node_dirs.setdefault(n3[j], []).append((dd, w))
                for j in range(3):
                    reach(n3[j], centre + rel[j], c)
            elif t in ("MCORN", "MMBEND", "MCROSS"):
                cen = (c.x, c.y)
                dirs = [_dir(cen, q) for q in pp]
                n_ = nodes[c.name]
                if t == "MCROSS":
                    ws = [wvar(c, f"W{j + 1}") for j in range(4)]
                    vals = _values(d, project)
                    # the half extent of the crossing along an arm is half the widest arm across it
                    across = [max(ws[1], ws[3], key=lambda q: q.num(vals)), max(ws[0], ws[2], key=lambda q: q.num(vals))]
                    ext_ = [across[j % 2] * 0.5 for j in range(4)]
                else:
                    w = wvar(c, "W")
                    ws, ext_ = [w, w], [w * 0.5, w * 0.5]
                rel = [along(dirs[j], ext_[j]) for j in range(len(pp))]
                centre = here - rel[k]
                if t == "MMBEND":
                    pts.append((oname(c), centre, dirs[0], dirs[1], ws[0], n_))
                else:
                    sx = ext_[0] * 2 if dirs[0][0] else ext_[1] * 2      # size along x and y of the square
                    sy = ext_[1] * 2 if dirs[0][0] else ext_[0] * 2
                    rects.append(_Rect(oname(c), centre - along((1, 0), sx * 0.5), (1, 0), sx, sy, n_))
                for j in range(len(pp)):
                    node_dirs.setdefault(n_[j], []).append(((-dirs[j][0], -dirs[j][1]), ws[j]))
                    reach(n_[j], centre + rel[j], c)
            elif t == "MSTEP":
                n_ = nodes[c.name]
                for j in (1, 2):
                    wvar(c, f"W{j}")
                same.append((n_[0], n_[1]))
                reach(n_[1 - k], here, c)
            elif t == "MGAP":
                dd = _dir(pp[k], pp[1 - k])
                for j in (1, 2):
                    setvar(f"{c.name}_W{j}", c.props[f"W{j}"])
                g = setvar(f"{c.name}_S", c.props["S"])
                reach(nodes[c.name][1 - k], here + along(dd, g), c)
            elif t == "MCLIN":
                da = _dir(pp[0], pp[1])
                o = _dir(pp[0], pp[2])
                w, ln = setvar(f"{c.name}_W", c.props["W"]), setvar(f"{c.name}_L", c.props["L"])
                s = setvar(f"{c.name}_S", c.props["S"])
                off = along(o, w + s)
                rel = [Pt(Lin(), Lin()), along(da, ln), off, along(da, ln) + off]
                p0 = here - rel[k]
                n4 = nodes[c.name]
                trace(f"{c.name}_A", p0, da, ln, w, n4[0], n4[1])
                trace(f"{c.name}_B", p0 + off, da, ln, w, n4[2], n4[3])
                for j in range(4):
                    reach(n4[j], p0 + rel[j], c)
    for c in comps:
        if c.name not in placed and c.type not in ("GND", "LABEL", "SUBST"):
            warnings.append(f"{c.name}: not connected to the layout, left out")
    if not rects:
        raise LayoutError("the circuit has no lines to lay out")

    vals = _values(d, project)

    def width_at(node: int) -> Lin:
        ws = [w for _, w in node_dirs.get(node, [])]
        return max(ws, key=lambda w: w.num(vals)) if ws else setvar("W_Pad", "1mm")

    def out_dir(node: int):
        """Outward direction at a node (away from its traces)."""
        ds = node_dirs.get(node, [])
        if ds:
            return -ds[0][0][0], -ds[0][0][1]
        for c, p, dd, g, na, nb in gaps:
            if na == node:
                return -dd[0], -dd[1]
            if nb == node:
                return dd
        return -1, 0

    # ---- materials, board and ground
    mat = f"{sname}_Er{er:g}"
    zt = var(H) * (0.5 if strip else 1.0)

    def ext(r: _Rect):
        a = r.p - along(_perp(r.d), r.w * 0.5)
        b = r.p + along(r.d, r.ln) + along(_perp(r.d), r.w * 0.5)
        return (a.x, b.x), (a.y, b.y)

    xs, ys = [], []
    for _, cen, a1, a2, w, _ in pts:
        for v in (cen + along(a1, w * 0.5) + along(a2, w * 0.5), cen - along(a1, w * 0.5) - along(a2, w * 0.5)):
            xs.append(v.x)
            ys.append(v.y)
    for r in rects:
        a, b = ext(r)
        xs += a
        ys += b
    for c, p, dd, g, na, nb in gaps:
        w = max(width_at(na), width_at(nb), key=lambda q: q.num(vals))
        r = _Rect("", p, dd, g, w)
        a, b = ext(r)
        xs += a
        ys += b
    flush = {}                         # side -> coordinate of a port on that side
    for c in ports:
        n = nodes[c.name][0]
        if n not in pos:
            continue
        od = out_dir(n)
        coord = pos[n].x if od[0] else pos[n].y
        side = ("x" if od[0] else "y", od[0] + od[1])
        allv = [q.num(vals) for q in (xs if od[0] else ys)]
        ext_v = max(allv) if side[1] > 0 else min(allv)
        if abs(coord.num(vals) - ext_v) < 1e-9:
            flush[side] = coord
    edges = {}
    for axis, lst in (("x", xs), ("y", ys)):
        lo = min(lst, key=lambda q: q.num(vals))
        hi = max(lst, key=lambda q: q.num(vals))
        edges[(axis, -1)] = flush.get((axis, -1), lo - var("Margin"))
        edges[(axis, 1)] = flush.get((axis, 1), hi + var("Margin"))
    x0, x1, y0, y1 = edges[("x", -1)], edges[("x", 1)], edges[("y", -1)], edges[("y", 1)]
    bx, by = (x1 - x0).text(), (y1 - y0).text()
    d.add_object("CreateBox", name="Substrate", material=mat, Position=f"{x0.text()},{y0.text()},0",
                 XSize=bx, YSize=by, ZSize=H)
    grounds = [d.add_object("CreateRectangle", name="Ground", Position=f"{x0.text()},{y0.text()},0", Axis="Z",
                            XSize=bx, YSize=by)]
    if strip:
        grounds.append(d.add_object("CreateRectangle", name="Ground_Top", Position=f"{x0.text()},{y0.text()},{H}",
                                    Axis="Z", XSize=bx, YSize=by))
    d.settings["AutoOpen"] = "Auto Open - Filter"
    d.settings["FilterAirMargin"] = "10mm"
    d.settings[LINK] = circuit.name

    # ---- traces, joined where they touch
    def sheet(nm: str, r: _Rect):
        (ax, bx_), (ay, by_) = ext(r)
        lx = ax if ax.num(vals) <= bx_.num(vals) else bx_
        ly = ay if ay.num(vals) <= by_.num(vals) else by_
        sx, sy = bx_ - ax, by_ - ay
        if sx.num(vals) < 0:
            sx = sx * -1
        if sy.num(vals) < 0:
            sy = sy * -1
        return d.add_object("CreateRectangle", name=nm, Position=f"{lx.text()},{ly.text()},{zt.text()}", Axis="Z",
                            XSize=sx.text(), YSize=sy.text())

    metal = []
    obj_nodes = []
    for r in rects:
        metal.append(sheet(d.unique_name(r.name), r))
        obj_nodes.append(set(r.nodes))
    for nm, cen, a1, a2, w, ns in pts:
        # the 50 % mitered bend keeps the triangle on the inside of the corner: full width at both arms
        q = [cen + along(a1, w * 0.5) + along(a2, w * 0.5), cen + along(a1, w * 0.5) - along(a2, w * 0.5),
             cen - along(a1, w * 0.5) + along(a2, w * 0.5)]
        area = sum(a.x.num(vals) * b.y.num(vals) - b.x.num(vals) * a.y.num(vals) for a, b in zip(q, q[1:] + q[:1]))
        if area < 0:                                   # counter-clockwise: the normal points up
            q = q[::-1]
        ptxt = ";".join(f"{v.x.text()},{v.y.text()},{zt.text()}" for v in q)
        o = d.add_object("CreatePolyline", name=d.unique_name(nm), material="vacuum", Points=ptxt, Closed="True",
                         Covered="True")
        metal.append(o)
        obj_nodes.append(set(ns))
    # a square patch at every corner and junction keeps the copper continuous where traces meet at an angle
    for n, ds in node_dirs.items():
        axes = {dd[0] != 0 for dd, _ in ds}
        if len(ds) > 1 and len(axes) > 1:
            w = width_at(n)
            r = _Rect("", pos[n] - along((1, 0), w * 0.5), (1, 0), w, w)
            metal.append(sheet(d.unique_name("Junction"), r))
            obj_nodes.append({n})
    for a, b in same:
        for s in obj_nodes:
            if a in s or b in s:
                s.update((a, b))
    parent = list(range(len(metal)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(metal)):
        for j in range(i):
            if obj_nodes[i] & obj_nodes[j]:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(metal)):
        groups.setdefault(find(i), []).append(i)
    conductors = []
    for g in groups.values():
        g = sorted(g)
        main = metal[g[0]]
        for j in g[1:]:
            main.history.append(Command.new("Unite", Tools=metal[j].name))
            metal[j].consumed_by = main.name
        conductors.append(main.name)

    def vertical(nm: str, p: Pt, od, w: Lin, z: Lin):
        """A sheet standing on the ground plane at p, facing the direction od, of width w and height z."""
        if od[0]:
            return d.add_object("CreateRectangle", name=nm, Position=f"{p.x.text()},{(p.y - w * 0.5).text()},0",
                                Axis="X", YSize=w.text(), ZSize=z.text())
        return d.add_object("CreateRectangle", name=nm, Position=f"{(p.x - w * 0.5).text()},{p.y.text()},0",
                            Axis="Y", XSize=w.text(), ZSize=z.text())

    vias = []
    for c, p, dd, w in shorts:
        vias.append(vertical(d.unique_name(f"{c.name}_Short"), p, dd, w, zt).name)
    # via parts: a row of round vias across the line, just inside its end
    for c, n in via_parts:
        if n not in pos:
            warnings.append(f"{c.name}: not connected to the layout, left out")
            continue
        dd = next((x for x, _ in node_dirs.get(n, [])), (1, 0))
        dv, pv = setvar(f"{c.name}_D", c.props.get("D", "0.4mm")), setvar(f"{c.name}_P", c.props.get("P", "1mm"))
        try:
            nv = max(1, int(round(ev.value(c.props.get("N", "1")))))
        except Exception:  # noqa: BLE001
            nv = 1
        vals = _values(d, project)
        if (nv - 1) * pv.num(vals) + dv.num(vals) > width_at(n).num(vals) * (1 + 1e-9):
            warnings.append(f"{c.name}: the vias are wider than the line; they stand out of its end")
        for i in range(nv):
            q = pos[n] + along(dd, dv * 0.5) + along(_perp(dd), pv * (i - (nv - 1) / 2))
            o = d.add_object("CreateCylinder", name=d.unique_name(f"{c.name}_{i + 1}" if nv > 1 else c.name),
                             material="pec", CenterPosition=f"{q.x.text()},{q.y.text()},0", Axis="Z",
                             Radius=(dv * 0.5).text(), Height=zt.text())
            o.color = "#c08040"

    def assign(lst, table, kind, nm, objects, **props):
        q = schema.defaults(table[kind])
        q.update(props)
        lst.append(Assignment(nm, kind, q, objects=list(objects)))

    cu = {"UseMaterial": "False", "Conductivity": f"{cond:g}", "UseThickness": "True", "Thickness": T,
          "TwoSided": "True"}
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Traces", conductors + vias, **cu)
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Ground", [g.name for g in grounds], **cu)

    # ---- lumped parts
    for c, p, dd, g, na, nb in gaps:
        w = max(width_at(na), width_at(nb), key=lambda q: q.num(vals))
        o = sheet(d.unique_name(c.name), _Rect("", p, dd, g, w))
        e = p + along(dd, g)
        line = f"{p.x.text()},{p.y.text()},{zt.text()};{e.x.text()},{e.y.text()},{zt.text()}"
        assign(d.boundaries, schema.BOUNDARIES, "Lumped RLC", c.name, [o.name], IntegrationLine=line,
               Component=f"{c.name} of {circuit.name}", **_rlc(c))
    for c, n in shunts:
        if n not in pos:
            continue
        o = vertical(d.unique_name(c.name), pos[n], out_dir(n), width_at(n), zt)
        p = pos[n]
        assign(d.boundaries, schema.BOUNDARIES, "Lumped RLC", c.name, [o.name],
               IntegrationLine=f"{p.x.text()},{p.y.text()},0;{p.x.text()},{p.y.text()},{zt.text()}",
               Component=f"{c.name} of {circuit.name}", **_rlc(c))

    # ---- ports, mesh, analysis
    for i, c in enumerate(ports, 1):
        n = nodes[c.name][0]
        if n not in pos:
            warnings.append(f"{c.name}: not connected to the layout, left out")
            continue
        p = pos[n]
        o = vertical(d.unique_name(f"Port{i}"), p, out_dir(n), width_at(n), zt)
        assign(d.excitations, schema.EXCITATIONS, "Lumped Port", str(i), [o.name], PortNumber=str(i),
               Impedance=c.props.get("Z0", "50ohm") or "50ohm",
               IntegrationLine=f"{p.x.text()},{p.y.text()},0;{p.x.text()},{p.y.text()},{zt.text()}")
    wmin = min((r.w for r in rects), key=lambda q: q.num(vals))
    assign(d.mesh_ops, schema.MESH_OPS, "Length Based On Selection", "Length_Traces", conductors,
           MaxLength=(wmin * 0.5).text())
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
        project.materials.append(Material(mat, permittivity=f"{er:g}", loss_tangent=f"{tand:g}",
                                          color="#2e7d32", description=f"Substrate {sname} of {circuit.name}"))
    d.notes = (f"Layout of the circuit design '{circuit.name}'. The dimensions are the design variables named "
               "after the parts; 'Update from Layout' (Simulation tab of the schematic) copies them back.")
    # the object sizes that are exactly a dimension variable: "Update from Layout" also reads a size that was
    # edited on the object instead of in the variable
    dims = {}
    board = ("Substrate", "Ground", "Ground_Top")         # their size follows the traces: the traces first
    for o in sorted(d.objects, key=lambda o: o.name in board):
        for k, v in (o.history[0].props.items() if o.history else ()):
            if k in ("XSize", "YSize", "ZSize", "Radius", "Height") and v.strip() in d.variables:
                dims.setdefault(v.strip(), [o.name, k])
    d.settings[DIMS] = json.dumps(dims)
    d.settings[VARS] = json.dumps(sorted(d.variables))
    return d, warnings


def _values(d: Design, project: Project) -> dict[str, float]:
    ev = d.evaluator(project)
    out = {}
    for k in d.variables:
        try:
            out[k] = ev.length(k)
        except Exception:  # noqa: BLE001
            out[k] = 0.0
    return out


def _synth(c, ev, er, h, t, MS) -> tuple[float, float]:
    z0, e, f = ev.value(c.props["Z0"]), ev.angle(c.props["E"]), ev.freq(c.props["F"])
    w = MS.synthesize(z0, h, er, t, f, builtin=True)
    lam = MS.guided_wavelength(w, h, er, t, f)
    return w, lam * e / (2 * math.pi)


def _rlc(c: CK.Component) -> dict[str, str]:
    p, t = c.props, c.type
    if t in ("R", "L", "C"):
        return {"Model": "Parallel RLC", "UseResistance": str(t == "R"), "UseInductance": str(t == "L"),
                "UseCapacitance": str(t == "C"), {"R": "Resistance", "L": "Inductance", "C": "Capacitance"}[t]: p[t]}
    model = "Series RLC" if t == "SRLC" else "Parallel RLC"
    return {"Model": model, "UseResistance": "True", "Resistance": p["R"], "UseInductance": "True",
            "Inductance": p["L"], "UseCapacitance": "True", "Capacitance": p["C"]}


# ---- back to the schematic ---------------------------------------------------------------------------------
BACK = {"MLIN": ("W", "L"), "MLOC": ("W", "L"), "MLSC": ("W", "L"), "SLIN": ("W", "L"),
        "MCLIN": ("W", "S", "L"), "MTEE": ("W1", "W2", "W3"), "MCORN": ("W",), "MMBEND": ("W",),
        "MSTEP": ("W1", "W2"), "MGAP": ("W1", "W2", "S"), "MCROSS": ("W1", "W2", "W3", "W4"),
        "SUBST": ("H", "T"), "CPWLIN": ("W", "S", "L"), "MVIA": ("D", "P")}
DIMS = "LayoutDims"              # layout setting: which object size holds which dimension variable (JSON)
VARS = "LayoutVars"              # layout setting: the design variables the layout made itself (JSON list)
_GENERATED = re.compile(r"^(W_Pad|[A-Za-z]\w*?_(W\d*|L|S|D|P|Gap|H|T|Er|TanD))$")   # layouts made before VARS


def source_circuit(project: Project, layout: Design) -> Design | None:
    name = layout.settings.get(LINK, "")
    for d in project.designs:
        if d.name == name and d.kind == "circuit":
            return d
    return None


def update_schematic(project: Project, layout: Design) -> tuple[Design, list[str]]:
    """Copy the dimensions of a generated layout back into its circuit design: the list names the changes."""
    circuit = source_circuit(project, layout)
    if circuit is None:
        raise LayoutError("this 3D design was not created from a schematic (or its circuit design was renamed "
                          "or deleted)")
    changes = []
    edited = {}
    try:
        dims = json.loads(layout.settings.get(DIMS, "") or "{}")
    except ValueError:
        dims = {}
    ev = layout.evaluator(project)
    for var, (obj, prop) in dims.items():
        if not layout.has_obj(obj) or not layout.obj(obj).history:
            continue
        text = layout.obj(obj).history[0].props.get(prop, "").strip()
        if text and text != var:              # the size was edited on the object: its value
            try:
                edited[var] = f"{ev.length(text) * 1e3:.6g}mm"
            except Exception:  # noqa: BLE001
                pass
    for c in circuit.components:
        for prop in BACK.get(c.type, ()):
            v = edited.get(f"{c.name}_{prop}") or layout.variables.get(f"{c.name}_{prop}")
            if v is not None and v.strip() != c.props.get(prop, "").strip():
                changes.append(f"{c.name}.{prop}: {c.props.get(prop, '')} → {v}")
                c.props[prop] = v.strip()
    return circuit, changes


def update_layout(project: Project, layout: Design) -> list[str]:
    """Rebuild a generated layout from its schematic in place: geometry, variables, boundaries, ports and mesh
    operations are made anew, the setups, reports and other analysis settings stay. Returns the warnings."""
    circuit = source_circuit(project, layout)
    if circuit is None:
        raise LayoutError("this 3D design was not created from a schematic (or its circuit design was renamed "
                          "or deleted)")
    new, warnings = layout_design(project, circuit, layout.name)
    layout.objects, layout.boundaries, layout.excitations = new.objects, new.boundaries, new.excitations
    layout.mesh_ops = new.mesh_ops
    try:
        made = set(json.loads(layout.settings.get(VARS, "") or "null") or [])
    except ValueError:
        made = set()
    if VARS not in layout.settings:
        made = {k for k in layout.variables if _GENERATED.match(k)}
    # variables of parts that are gone go with them; variables the user added stay
    layout.variables = {**{k: v for k, v in layout.variables.items() if k not in new.variables and k not in made},
                        **new.variables}
    layout.settings.update({k: v for k, v in new.settings.items() if k in (LINK, DIMS, VARS)})
    layout.notes = new.notes
    return warnings
