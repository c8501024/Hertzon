"""Schematic to 3D layout and back.

layout_design() turns a circuit design into a new 3D design: the substrate with its ground plane, the microstrip
(or stripline) traces placed end to end as the schematic connects them, the lumped R, L and C parts as Lumped RLC
sheets in a gap of the trace (or from the trace to ground for a shunt part) and the power ports as Lumped Ports
from the trace to ground. Every dimension is a design variable named after its part ("MS1_W", "MS1_L", "Sub1_H"),
so the layout can be tuned or optimized in 3D and update_schematic() copies the values back into the schematic.

The physical direction of a part follows its orientation in the schematic (right = +x, up = +y); the layout
starts at the first power port. Parts that have no layout are reported: ideal lines are converted into
microstrip of the same impedance and electrical length, a DC block becomes a direct connection, Touchstone
blocks, attenuators, coaxial lines and DC feeds are left out. 3D design blocks bring their geometry along
(circuit_embed.py): turned and moved so that the lines attach at their ports.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

from . import circuit as CK
from . import circuit_embed as CE
from . import schema
from .design import Assignment, Command, Design, Project, Setup, Sweep
from .materials import Material

LINE2 = ("MLIN", "SLIN", "TLIN", "CPWLIN")
STUBS = ("MLOC", "MLSC", "TLOC", "TLSC")
LUMPED = ("R", "L", "C", "SRLC", "PRLC")
SKIP = {"ATTEN": "the attenuator has no layout and was left out",
        "COAX": "a coaxial line has no planar layout and was left out",
        "DCFEED": "the DC feed is open at RF and was left out",
        "SNP": "Touchstone blocks have no layout and were left out"}
LINK = "LayoutOf"                # design setting of a generated layout: the name of its circuit design
PORT_MAP = "LayoutPorts"         # layout setting: port number -> part, terminal and pad it stands for (JSON)
GAP = "1mm"                      # gap of a series lumped part (pad to pad)
MARGIN = "5mm"                   # substrate beyond the traces where no port sits on the board edge


class Lin(dict):
    """A linear combination of design variables {name: coefficient}: coordinates as exact expressions. The key
    "" is a constant length in metres (the place of a 3D design block)."""

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
            if k == "":
                term = f"{a * 1e3:.9g}mm"
            else:
                term = k if abs(a - 1) < 1e-12 else f"{a:g}*{k}"
            out += (("-" if sign == "-" else "") if not out else sign) + term
        return out

    def num(self, vals: dict[str, float]) -> float:
        return sum(v * (1.0 if k == "" else vals[k]) for k, v in self.items())


def var(name: str) -> Lin:
    return Lin({name: 1.0})


def const(metres: float) -> Lin:
    return Lin({"": metres}) if abs(metres) > 1e-15 else Lin()


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

    spacings: list[Lin] = []           # gaps of coupled lines and gap parts (they set the trace mesh too)

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
                routes.append((node, p, comp))
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
    routes: list[tuple] = []          # loops that do not close: (node, where the part ends, part)
    ground_pads: list[tuple] = []     # ground pad of a shunt part on its footprint: (part, centre, dir, half, w)
    dev_pads: list[dict] = []         # pads of devices and blocks on their footprints (each gets a port)
    supplies: list[tuple] = []        # DC sources: (part, node of the + pin), each gets a port
    blocks: list[tuple] = []          # 3D design blocks: (component, geometry, quarter turns, origin, z shift)
    em_out: dict[int, tuple] = {}     # node at a block port -> direction out of the block
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
            if t in ("DEVICE", "SNP") and c.props.get("Footprint", "").strip():
                _place_device(c, k, node, here, comps, nodes, placed, node_dirs, rects, dev_pads, project, warnings,
                              reach)
                continue
            if t == "DEVICE":
                warnings.append(f"{c.name}: the device has no footprint and was left out; the parts behind it are "
                                "not in the layout (set its Footprint)")
                continue
            if t in ("VDC", "IDC"):
                if nodes[c.name][1 - k] != 0:
                    warnings.append(f"{c.name}: the other pin of the source is not on ground; its port is between "
                                    "this end and ground")
                supplies.append((c, node))
                continue
            if t in SKIP:
                warnings.append(f"{c.name}: {SKIP[t]}")
                continue
            if t == "MVIA":
                via_parts.append((c, node))
                continue
            if t == "EM":
                try:
                    g = CE.geometry(project, c)
                except CE.EmbedError as e:
                    warnings.append(f"{e}; the block was left out")
                    continue
                # turned so that the port reached here faces the line that reaches it (or its side in the
                # schematic), moved so that the port centre sits on the node
                pk = g.ports[k]
                ds = node_dirs.get(node)
                q = CE.quarter_turns(pk.out, ds[0][0] if ds else _dir((c.x, c.y), pp[k]))
                kx, ky = CE.turn(pk.centre, q)
                origin = here - Pt(const(kx), const(ky))
                n_ = nodes[c.name]
                for j, pg in enumerate(g.ports):
                    jx, jy = CE.turn(pg.centre, q)
                    em_out[n_[j]] = CE.turn(pg.out, q)
                    if j != k:
                        reach(n_[j], origin + Pt(const(jx), const(jy)), c)
                blocks.append((c, g, q, origin, -min(pg.zmin for pg in g.ports)))
                continue
            if t in LINE2 or t == "DCBLOCK" or t in LUMPED:
                other = 1 - k
                dd = _dir(pp[k], pp[other])
                na, nb = nodes[c.name][k], nodes[c.name][other]
                if t in LUMPED:
                    fpp = _chip_pads(c, k, project, warnings)
                    if fpp is not None:            # on the pads of its footprint
                        ek, eo, g_m, wk, wo = fpp
                        a0 = here + along(dd, const(2 * ek))
                        rects.append(_Rect(f"{c.name}_Pad{k + 1}", here, dd, const(2 * ek), const(wk), (na,)))
                        node_dirs.setdefault(na, []).append((dd, const(wk)))
                        b0 = a0 + along(dd, const(g_m))
                        gaps.append((c, a0, dd, const(g_m), na, nb, const(min(wk, wo))))
                        end = b0 + along(dd, const(2 * eo))
                        if nb == 0:                # the ground pad, with vias to the ground plane
                            rects.append(_Rect(f"{c.name}_Pad{2 - k}", b0, dd, const(2 * eo), const(wo), ()))
                            ground_pads.append((c, b0 + along(dd, const(eo)), dd, eo, wo))
                        else:
                            rects.append(_Rect(f"{c.name}_Pad{2 - k}", b0, dd, const(2 * eo), const(wo), (nb,)))
                            node_dirs.setdefault(nb, []).append(((-dd[0], -dd[1]), const(wo)))
                            reach(nb, end, c)
                        continue
                if t in LUMPED and nb == 0:
                    shunts.append((c, na))
                    continue
                if t == "DCBLOCK":
                    warnings.append(f"{c.name}: the DC block is a direct connection in the layout")
                    reach(nb, here, c)
                    same.append((na, nb))
                    continue
                if t in LUMPED:
                    g = setvar(f"{c.name}_Gap", c.props.get("Gap") or GAP)     # pad to pad, an expression too
                    gaps.append((c, here, dd, g, na, nb, None))
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
                spacings.append(g)
                reach(nodes[c.name][1 - k], here + along(dd, g), c)
            elif t == "MCLIN":
                da = _dir(pp[0], pp[1])
                o = _dir(pp[0], pp[2])
                w, ln = setvar(f"{c.name}_W", c.props["W"]), setvar(f"{c.name}_L", c.props["L"])
                s = setvar(f"{c.name}_S", c.props["S"])
                spacings.append(s)
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
    if not rects and not blocks:
        raise LayoutError("the circuit has no lines to lay out")

    vals = _values(d, project)

    # a loop of the circuit that the parts do not close: a thin trace, along x and then along y, from where the
    # last part ends to where the node lies (it is not in the schematic: reported)
    for node, p0, comp in routes:
        rw = setvar("Route_W", "0.3mm")
        vals = _values(d, project)
        q = pos[node]
        dxv, dyv = q.x.num(vals) - p0.x.num(vals), q.y.num(vals) - p0.y.num(vals)
        corner = Pt(q.x, p0.y)
        if abs(dxv) > 1e-9:
            dx = q.x - p0.x
            rects.append(_Rect(f"{comp.name}_Route", p0 - along((1 if dxv > 0 else -1, 0), rw * 0.5),
                               (1 if dxv > 0 else -1, 0), (dx if dxv > 0 else dx * -1) + rw, rw, (node,)))
        if abs(dyv) > 1e-9:
            dy = q.y - p0.y
            rects.append(_Rect(f"{comp.name}_Route", corner - along((0, 1 if dyv > 0 else -1), rw * 0.5),
                               (0, 1 if dyv > 0 else -1), (dy if dyv > 0 else dy * -1) + rw, rw, (node,)))
        warnings.append(f"{comp.name}: the parts do not close a loop of the circuit; a trace of Route_W "
                        f"({abs(dxv) * 1e3 + abs(dyv) * 1e3:.3g} mm, not in the schematic) closes it. Draw the "
                        "loop with lines in the schematic to control it")

    def width_at(node: int) -> Lin:
        ws = [w for _, w in node_dirs.get(node, [])]
        return max(ws, key=lambda w: w.num(vals)) if ws else setvar("W_Pad", "1mm")

    def out_dir(node: int):
        """Outward direction at a node (away from its traces)."""
        ds = node_dirs.get(node, [])
        if ds:
            return -ds[0][0][0], -ds[0][0][1]
        if node in em_out:
            return em_out[node]
        for c, p, dd, g, na, nb, _ in gaps:
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
    for c, p, dd, g, na, nb, wg in gaps:
        w = wg or max(width_at(na), width_at(nb), key=lambda q: q.num(vals))
        r = _Rect("", p, dd, g, w)
        a, b = ext(r)
        xs += a
        ys += b
    for c, g, q, origin, dz in blocks:
        for corner in ((g.bbox[0], g.bbox[1]), (g.bbox[2], g.bbox[3])):
            tx, ty = CE.turn(corner, q)
            xs.append(origin.x + const(tx))
            ys.append(origin.y + const(ty))
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

    warnings += _overlaps(rects, vals)

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

    # the ground pad of a shunt part: round vias across the pad, at its centre
    for c, q0, dd, half, w in ground_pads:
        dv = setvar("Via_D", "0.3mm")
        vals = _values(d, project)
        dvm = dv.num(vals)
        nv = max(1, int((w - dvm) / (2 * dvm)) + 1)
        pitch = (w - dvm) / (nv - 1) if nv > 1 else 0.0
        for i in range(nv):
            q = q0 + along(_perp(dd), const(pitch * (i - (nv - 1) / 2)))
            o = d.add_object("CreateCylinder", name=d.unique_name(f"{c.name}_Via{i + 1}" if nv > 1 else f"{c.name}_Via"),
                             material="pec", CenterPosition=f"{q.x.text()},{q.y.text()},0", Axis="Z",
                             Radius=(dv * 0.5).text(), Height=zt.text())
            o.color = "#c08040"
        warnings.append(f"{c.name}: its ground pad has {nv} via{'s' if nv > 1 else ''} of {dvm * 1e3:g} mm (Via_D) "
                        "to the ground plane; the schematic has none, place a Via to Ground to model it there")

    def assign(lst, table, kind, nm, objects, **props):
        q = schema.defaults(table[kind])
        q.update(props)
        lst.append(Assignment(nm, kind, q, objects=list(objects)))

    cu = {"UseMaterial": "False", "Conductivity": f"{cond:g}", "UseThickness": "True", "Thickness": T,
          "TwoSided": "True"}
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Traces", conductors + vias, **cu)
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Ground", [g.name for g in grounds], **cu)

    # ---- lumped parts
    for c, p, dd, g, na, nb, wg in gaps:
        w = wg or max(width_at(na), width_at(nb), key=lambda q: q.num(vals))
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

    # ---- 3D design blocks
    em_port_nodes = _embed_blocks(d, blocks, comps, nodes, ports, node_dirs, em_out, grounds[0], zt, vals, warnings)

    # ---- ports, mesh, analysis
    port_map: dict[str, dict] = {}
    for i, c in enumerate(ports, 1):
        port_map[str(i)] = {"part": c.name, "terminal": "", "pad": ""}
        n = nodes[c.name][0]
        if n not in pos:
            warnings.append(f"{c.name}: not connected to the layout, left out")
            continue
        if n in em_port_nodes:         # the port of the 3D design block stays the port
            continue
        p = pos[n]
        o = vertical(d.unique_name(f"Port{i}"), p, out_dir(n), width_at(n), zt)
        assign(d.excitations, schema.EXCITATIONS, "Lumped Port", str(i), [o.name], PortNumber=str(i),
               Impedance=c.props.get("Z0", "50ohm") or "50ohm",
               IntegrationLine=f"{p.x.text()},{p.y.text()},0;{p.x.text()},{p.y.text()},{zt.text()}")
    # a port from every pad of a device or block on its footprint to the ground plane, at the pad's inner edge;
    # then a port at the end of the line of every DC source
    nxt = len(ports) + 1
    for dp in dev_pads:
        q0, od, w = dp["edge"], dp["inward"], dp["across"]
        o = vertical(d.unique_name(f"{dp['part']}_P{dp['pad']}"), q0, od, w, zt)
        pname = f"{dp['part']}_{dp['terminal']}_pad{dp['pad']}"
        assign(d.excitations, schema.EXCITATIONS, "Lumped Port", pname, [o.name], PortNumber=str(nxt),
               Impedance="50ohm", IntegrationLine=f"{q0.x.text()},{q0.y.text()},0;{q0.x.text()},{q0.y.text()},{zt.text()}")
        port_map[str(nxt)] = {"part": dp["part"], "terminal": dp["terminal"], "pad": dp["pad"]}
        nxt += 1
        for j, (hx, hy, r) in enumerate(dp["holes"]):
            o = d.add_object("CreateCylinder", name=d.unique_name(f"{dp['part']}_Hole{dp['pad']}"),
                             material="pec", CenterPosition=f"{hx.text()},{hy.text()},0", Axis="Z",
                             Radius=f"{r * 1e3:.6g}mm", Height=zt.text())
            o.color = "#c08040"
    for c, n in supplies:
        if n not in pos:
            continue
        q0 = pos[n]
        o = vertical(d.unique_name(f"{c.name}_Supply"), q0, out_dir(n), width_at(n), zt)
        assign(d.excitations, schema.EXCITATIONS, "Lumped Port", f"{c.name}_supply", [o.name], PortNumber=str(nxt),
               Impedance="50ohm", IntegrationLine=f"{q0.x.text()},{q0.y.text()},0;{q0.x.text()},{q0.y.text()},{zt.text()}")
        port_map[str(nxt)] = {"part": c.name, "terminal": "+", "pad": ""}
        nxt += 1
    d.settings[PORT_MAP] = json.dumps(port_map)
    if rects:
        # half the narrowest trace, and not more than the smallest gap of coupled lines: a gap must be resolved
        # from the first pass (the adaptive mesh at a frequency in the stopband would not refine it)
        lmax = min([r.w * 0.5 for r in rects] + spacings, key=lambda q: q.num(vals))
        assign(d.mesh_ops, schema.MESH_OPS, "Length Based On Selection", "Length_Traces", conductors,
               MaxLength=lmax.text())
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


def _embed_blocks(d: Design, blocks, comps, nodes, ports, node_dirs, em_out, ground, zt: Lin, vals, warnings) \
        -> set[int]:
    """Copy the 3D designs of the blocks into the layout; returns the nodes whose Power Port is a port of a
    block."""
    users: dict[int, list] = {}
    for x in comps:
        for n in nodes.get(x.name, ()):
            users.setdefault(n, []).append(x)
    number = {c.name: i for i, c in enumerate(ports, 1)}
    port_nodes: set[int] = set()
    h = zt.num(vals)
    for c, g, q, origin, dz in blocks:
        keep = {}
        for j, pg in enumerate(g.ports):
            nj = nodes[c.name][j]
            if nj == 0:
                warnings.append(f"{c.name}: pin {j + 1} ({pg.name}) is grounded; its port was removed, draw the "
                                "short in the 3D design")
                continue
            others = [x for x in users.get(nj, []) if x is not c and x.type != "LABEL"]
            if not others:
                warnings.append(f"{c.name}: pin {j + 1} ({pg.name}) is open; its port was removed")
            elif all(x.type == "PORT" for x in others):
                keep[pg.name] = (str(number[others[0].name]), others[0].props.get("Z0", "") or "50ohm")
                port_nodes.add(nj)
            for dd, _ in node_dirs.get(nj, []):
                if tuple(dd) != tuple(em_out[nj]):
                    warnings.append(f"{c.name}: the line at pin {j + 1} ({pg.name}) does not leave the block "
                                    "straight out of its port; turn the line in the schematic")
                    break
            if pg.kind == "Lumped Port" and abs((pg.zmax - pg.zmin) - h) > 0.01 * h:
                warnings.append(f"{c.name}: the port {pg.name} is {(pg.zmax - pg.zmin) * 1e3:.4g} mm high, the "
                                f"layout substrate {h * 1e3:.4g} mm")
        move = f"{origin.x.text()},{origin.y.text()},{dz * 1e3:.9g}mm"
        _, w = CE.embed(d, g, c, q, move, keep)
        warnings += w
        # a hole in the layout's ground plane where the block has its own ground
        for x0, y0, x1, y1 in g.grounds:
            ax, ay = CE.turn((x0, y0), q)
            bx, by = CE.turn((x1, y1), q)
            px = origin.x + const(min(ax, bx))
            py = origin.y + const(min(ay, by))
            hole = d.add_object("CreateRectangle", name=d.unique_name(f"{c.name}_GroundCut"),
                                Position=f"{px.text()},{py.text()},0", Axis="Z",
                                XSize=f"{abs(bx - ax) * 1e3:.9g}mm", YSize=f"{abs(by - ay) * 1e3:.9g}mm")
            hole.consumed_by = ground.name
            ground.history.append(Command.new("Subtract", Tools=hole.name, KeepOriginals="False"))
    return port_nodes


def _overlaps(rects, vals) -> list[str]:
    """Copper of two different nets that lies on each other (the schematic is not drawn as the board): one
    warning per pair of parts."""
    boxes = []
    for i, r in enumerate(rects):
        a = r.p - along(_perp(r.d), r.w * 0.5)
        b = r.p + along(r.d, r.ln) + along(_perp(r.d), r.w * 0.5)
        x0, x1 = sorted((a.x.num(vals), b.x.num(vals)))
        y0, y1 = sorted((a.y.num(vals), b.y.num(vals)))
        net = frozenset(r.nodes) if r.nodes else ("own", i)
        boxes.append((x0, y0, x1, y1, net, r.name.split("_Pad")[0].split("_Route")[0]))
    out, seen = [], set()
    tol = 1e-6
    for i, (ax0, ay0, ax1, ay1, an, aname) in enumerate(boxes):
        for bx0, by0, bx1, by1, bn, bname in boxes[:i]:
            if aname == bname or (isinstance(an, frozenset) and isinstance(bn, frozenset) and an & bn):
                continue
            if ax0 < bx1 - tol and bx0 < ax1 - tol and ay0 < by1 - tol and by0 < ay1 - tol:
                key = tuple(sorted((aname, bname)))
                if key not in seen:
                    seen.add(key)
                    out.append(f"{key[0]} and {key[1]} lie on each other in the layout (different nets): the "
                               "schematic is not drawn as the board; move or turn the parts or add lines")
    return out


def _turn_q(v: tuple[float, float], q: int) -> tuple[float, float]:
    x, y = v
    for _ in range(q % 4):
        x, y = -y, x
    return x, y


def _place_device(c, k, node, here, comps, nodes, placed, node_dirs, rects, dev_pads, project, warnings, reach):
    """Put a nonlinear device (or a Touchstone block) on its footprint: turned in quarter turns so that the pads
    of terminal k face the line that reached them (or by the part's LayoutRot), moved so that the edge of that
    pad touches the line end; every copper pad becomes a conductor of its terminal's node with a port of its own
    (made later from dev_pads), plated holes of the footprint become vias; the lines of the other terminals go on
    from the edge of their pad that faces the direction of the next part in the schematic."""
    from pathlib import Path
    from . import footprints as FPS
    from .kicad import KicadError
    try:
        board = FPS.on_board(c, Path(project.path).parent if project.path else None)
    except KicadError as ex:
        warnings.append(f"{c.name}: {ex}; the device was left out")
        return
    if board.check.errors:
        warnings.append(f"{board.check.errors[0]}; the device was left out")
        return
    warnings += board.check.warnings
    fp = board.footprint
    ts = FPS.terminals(c)
    cx0, cy0, cx1, cy1 = fp.bbox()
    fc = ((cx0 + cx1) / 2, (cy0 + cy1) / 2)
    copper = [q for q in fp.pads if q.copper and q.number]

    def term_pads(t):
        return [q for q in copper if q.number in board.pads.get(t, [])]

    def centre(q):
        b = q.bbox()
        return (b[0] + b[2]) / 2 - fc[0], (b[1] + b[3]) / 2 - fc[1]

    ds = node_dirs.get(node)
    d_in = ds[0][0] if ds else (-1, 0)          # from the node back along the line: where the line lies
    rot = c.props.get("LayoutRot", "").strip()
    pk = term_pads(ts[k])
    if rot:
        try:
            qt = int(round(float(rot.removesuffix("deg")) / 90)) % 4
        except ValueError:
            warnings.append(f"{c.name}: LayoutRot '{rot}' is not 0, 90, 180 or 270; turned automatically")
            rot = ""
    if not rot:
        def score(qq):
            return max(sum(a * b for a, b in zip(_turn_q(centre(q), qq), d_in)) for q in pk)
        qt = max(range(4), key=score)

    def rbox(q):
        """Pad bounding box turned by qt, relative to the footprint centre (mm)."""
        b = q.bbox()
        pts = [_turn_q((x - fc[0], y - fc[1]), qt) for x in (b[0], b[2]) for y in (b[1], b[3])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def edge(q, dd):
        """Middle of the pad's edge on the side dd (turned frame, mm) and the pad's extent across dd."""
        x0, y0, x1, y1 = rbox(q)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if dd[0]:
            return (x1 if dd[0] > 0 else x0, my), y1 - y0
        return (mx, y1 if dd[1] > 0 else y0), x1 - x0

    def pick(qs, dd):
        return max(qs, key=lambda q: sum(a * b for a, b in zip(_turn_q(centre(q), qt), dd)))

    first = pick(pk, d_in)
    (ex, ey), across = edge(first, d_in)
    origin = here - Pt(const(ex * 1e-3), const(ey * 1e-3))
    node_dirs.setdefault(node, []).append(((-d_in[0], -d_in[1]), const(across * 1e-3)))

    def at(x, y):
        return origin + Pt(const(x * 1e-3), const(y * 1e-3))

    users = {}
    for x in comps:
        for n in nodes.get(x.name, ()):
            users.setdefault(n, []).append(x)
    for j, t in enumerate(ts):
        nj = nodes[c.name][j]
        if j == k:
            continue
        qs = term_pads(t)
        if not qs:
            continue
        dj = _next_dir(nj, c, users, nodes, placed)
        if dj is None:                              # outward from the package at that pad
            cx, cy = _turn_q(centre(qs[0]), qt)
            dj = (1 if cx > 0 else -1, 0) if abs(cx) >= abs(cy) else (0, 1 if cy > 0 else -1)
        q = pick(qs, dj)
        (px, py), acr = edge(q, dj)
        cx, cy = _turn_q(centre(q), qt)
        if cx * dj[0] + cy * dj[1] < -1e-9:
            warnings.append(f"{c.name}: the line at {t} (pad {q.number}) leaves towards the package; turn the part "
                            "(LayoutRot) or the line in the schematic")
        if nj != 0:
            node_dirs.setdefault(nj, []).append(((-dj[0], -dj[1]), const(acr * 1e-3)))
            reach(nj, at(px, py), c)
        else:
            warnings.append(f"{c.name}: {t} is on ground in the schematic; draw its vias in the layout or place a "
                            "Via to Ground at it")
    # the pads: conductors of their nodes, and a port each
    ts_node = {t: nodes[c.name][j] for j, t in enumerate(ts)}
    for q in copper:
        t = board.terminal_of(q.number)
        x0, y0, x1, y1 = rbox(q)
        n = ts_node.get(t, 0) if t and t != FPS.NC else 0
        rects.append(_Rect(f"{c.name}_Pad{q.number}", at(x0, (y0 + y1) / 2), (1, 0), const((x1 - x0) * 1e-3),
                           const((y1 - y0) * 1e-3), (n,) if n else ()))
        if not t or t == FPS.NC:
            continue
        cx, cy = _turn_q(centre(q), qt)
        if abs(cx) < 1e-9 and abs(cy) < 1e-9:
            inward = (-1, 0)                        # a pad in the middle (exposed pad): its left edge
        elif abs(cx) >= abs(cy):
            inward = (-1 if cx > 0 else 1, 0)
        else:
            inward = (0, -1 if cy > 0 else 1)
        (px, py), acr = edge(q, inward)
        holes = []
        if q.plated_hole:
            hx, hy = _turn_q((q.x - fc[0], q.y - fc[1]), qt)
            pt = at(hx, hy)
            holes.append((pt.x, pt.y, q.drill / 2 * 1e-3))
        dev_pads.append({"part": c.name, "terminal": t, "pad": q.number, "edge": at(px, py), "inward": inward,
                         "across": const(acr * 1e-3), "holes": holes})
    placed.add(c.name)


def _next_dir(node: int, c, users, nodes, placed):
    """The direction (layout axes) in which the next part at a node leaves it, from the schematic; None when it
    cannot be told (a junction, a via, nothing)."""
    for x in users.get(node, []):
        if x is c or x.name in placed:
            continue
        pp = CK.pins(x)
        if x.type in LINE2 or x.type in LUMPED or x.type == "DCBLOCK":
            idx = nodes[x.name].index(node)
            return _dir(pp[idx], pp[1 - idx])
        if x.type in STUBS:
            return _dir(pp[0], (x.x, x.y))
    return None


def _chip_pads(c: CK.Component, k: int, project: Project, warnings: list[str]):
    """The pads of a two-terminal part on its footprint, seen from terminal k: (half length along the part of the
    pad of terminal k, the same of the other pad, the gap between the pads, the width of the pad of terminal k,
    of the other pad), in metres. None when the part has no footprint (or its pads do not lie in a row, or the
    footprint cannot be read: then it is laid out in a gap, with a warning)."""
    from pathlib import Path
    from . import footprints as FPS
    from .kicad import KicadError
    try:
        board = FPS.on_board(c, Path(project.path).parent if project.path else None)
    except KicadError as ex:
        warnings.append(f"{c.name}: {ex}; laid out in a gap")
        return None
    if board is None:
        return None
    if board.check.errors:
        warnings.append(f"{board.check.errors[0]}; laid out in a gap")
        return None
    ts = FPS.terminals(c)
    boxes = []
    for t in (ts[k], ts[1 - k]):
        pads = [q for n in board.pads.get(t, []) for q in board.footprint.pads_of(n) if q.copper]
        bb = [q.bbox() for q in pads]
        boxes.append((min(b[0] for b in bb), min(b[1] for b in bb), max(b[2] for b in bb), max(b[3] for b in bb)))
    (a0, a1, a2, a3), (b0, b1, b2, b3) = boxes
    ca, cb = ((a0 + a2) / 2, (a1 + a3) / 2), ((b0 + b2) / 2, (b1 + b3) / 2)
    dx, dy = cb[0] - ca[0], cb[1] - ca[1]
    if abs(dy) < 1e-6 and abs(dx) > 1e-6:
        ea, eb, wa, wb, dist = (a2 - a0) / 2, (b2 - b0) / 2, a3 - a1, b3 - b1, abs(dx)
    elif abs(dx) < 1e-6 and abs(dy) > 1e-6:
        ea, eb, wa, wb, dist = (a3 - a1) / 2, (b3 - b1) / 2, a2 - a0, b2 - b0, abs(dy)
    else:
        warnings.append(f"{c.name}: the two pads of {board.footprint.name} do not lie in a row; laid out in a gap")
        return None
    g = dist - ea - eb
    if g <= 0:
        warnings.append(f"{c.name}: the pads of {board.footprint.name} overlap; laid out in a gap")
        return None
    return ea * 1e-3, eb * 1e-3, g * 1e-3, wa * 1e-3, wb * 1e-3


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
    made_cs = {cs.name for cs in new.coord_systems}
    layout.coord_systems = [cs for cs in layout.coord_systems if cs.name not in made_cs] + new.coord_systems
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


def layouts_of(project: Project, circuit: Design) -> list[Design]:
    """The 3D designs generated from the circuit design ('Create 3D Layout'), in project order."""
    return [d for d in project.designs
            if getattr(d, "kind", "3d") != "circuit" and d.settings.get(LINK, "") == circuit.name]


def layout_differences(project: Project, layout: Design) -> list[str]:
    """Where a generated layout no longer follows its schematic: a dimension of a part that differs, or a line
    that is not in the layout (the schematic was edited after 'Create 3D Layout'). Empty when they agree."""
    circuit = source_circuit(project, layout)
    if circuit is None:
        return []
    out = []
    for c in circuit.components:
        props = BACK.get(c.type, ())
        if c.type in ("MLIN", "MLOC", "MLSC", "SLIN", "MCLIN", "CPWLIN") and f"{c.name}_L" not in layout.variables:
            out.append(f"{c.name} of {circuit.name} is not in the layout {layout.name}")
            continue
        for prop in props:
            v = layout.variables.get(f"{c.name}_{prop}")
            if v is not None and c.props.get(prop, "").strip() and v.strip() != c.props[prop].strip():
                out.append(f"{c.name}.{prop} is {c.props[prop]} in {circuit.name} but {v} in the layout "
                           f"{layout.name}")
    if out:
        out.append(f"the layout {layout.name} differs from its schematic: use 'Update 3D Layout' first, so that "
                   "the 3D solves are of the circuit being optimized")
    return out


# ---- the solved layout back in a schematic -------------------------------------------------------------------
def board_circuit(project: Project, layout: Design, name: str | None = None) -> tuple[Design, list[str]]:
    """A new circuit design that puts the parts the layout cannot hold back on its ports: the layout as a 3D
    design block, every pad port wired (by a net label) to the terminal of its device, the power ports at their
    ports, the DC sources at their supply ports; SPICE libraries, setups and variables of the source schematic
    come along. Every pad port of a terminal connects to it: several pads of one terminal are in parallel, as on
    the board. Returns the design and what could not be connected."""
    import copy
    circuit = source_circuit(project, layout)
    if circuit is None:
        raise LayoutError("this 3D design was not created from a schematic (or its circuit design was renamed "
                          "or deleted)")
    try:
        pmap = json.loads(layout.settings.get(PORT_MAP, "") or "{}")
    except ValueError:
        pmap = {}
    ports = layout.ports()
    if not ports:
        raise LayoutError(f"the layout {layout.name} has no ports")
    names = {d.name for d in project.designs}
    base = name or f"{circuit.name}_Board"
    name, i = base, 1
    while name in names:
        i += 1
        name = f"{base}{i}"
    d = Design(name=name, kind="circuit", units=circuit.units)
    d.settings.update({k: v for k, v in circuit.settings.items()})
    d.variables = dict(circuit.variables)
    d.setups = copy.deepcopy(circuit.setups)
    warnings: list[str] = []
    by_name = {c.name: c for c in circuit.components}
    n = len(ports)
    em = CK.Component("EM1", "EM", 0, 0, 0, {"Design": layout.name, "Solution": "", "Ports": str(n),
                                             "PortNames": ",".join(p.name for p in ports)})
    d.components.append(em)
    pins = CK.pins(em)
    gx = CK.GRID

    def label(x, y, net):
        d.components.append(CK.Component(CK.next_name(d.components, "LABEL"), "LABEL", x, y, 0, {"Net": net}))

    def stub(k: int, net: str):
        """A short wire out of block pin k and a label with the net at its end."""
        x, y = pins[k]
        side = -1 if x < 0 else 1
        d.wires.append(CK.Wire([[x, y], [x + side * 4 * gx, y]]))
        label(x + side * 4 * gx, y, net)
        return x + side * 4 * gx, y

    placed_parts: dict[str, CK.Component] = {}
    right = CK.BLOCK_W + 30 * gx
    row = [0]

    def place(src: CK.Component) -> CK.Component:
        if src.name in placed_parts:
            return placed_parts[src.name]
        c = copy.deepcopy(src)
        c.x, c.y, c.rot, c.mirror = right, row[0], 0, False
        row[0] += 16 * gx if c.type in CK.BLOCK_TYPES else 10 * gx
        d.components.append(c)
        placed_parts[src.name] = c
        return c

    for k, p in enumerate(ports):
        info = pmap.get(p.props.get("PortNumber", str(k + 1)), {})
        part = by_name.get(info.get("part", ""))
        if part is None:
            warnings.append(f"port {p.name} of {layout.name} stands for no part of {circuit.name}: left open")
            continue
        if part.type == "PORT":
            x, y = stub(k, f"port_{part.name}")
            c = copy.deepcopy(part)
            c.x, c.y, c.rot = x, y + 40, 0           # the port's pin at (0, -40)
            d.components.append(c)
            continue
        if part.type in ("VDC", "IDC"):
            c = place(part)
            pp = CK.pins(c)
            stub(k, f"{part.name}_supply")
            label(*pp[0], f"{part.name}_supply")
            d.components.append(CK.Component(CK.next_name(d.components, "GND"), "GND", pp[1][0], pp[1][1] + gx))
            continue
        c = place(part)
        from . import footprints as FPS
        ts = FPS.terminals(part)
        t = info.get("terminal", "")
        if t not in ts:
            warnings.append(f"port {p.name}: {part.name} has no terminal {t}")
            continue
        net = f"{part.name}_{t}"
        stub(k, net)
        label(*CK.pins(c)[ts.index(t)], net)
    # the libraries and models the devices need
    for c in circuit.components:
        if c.type == "SPICELIB":
            q = copy.deepcopy(c)
            q.x, q.y = right, row[0] + 10 * gx
            row[0] += 8 * gx
            d.components.append(q)
    # a terminal of a device that no pad port reaches (its pads are open in the layout)
    for src, c in placed_parts.items():
        if c.type in ("DEVICE", "SNP"):
            from . import footprints as FPS
            for j, t in enumerate(FPS.terminals(c)):
                if not any(x.type == "LABEL" and x.props.get("Net") == f"{src}_{t}" for x in d.components):
                    warnings.append(f"{src}.{t} reaches no port of the layout: open in {name}")
    d.notes = (f"The 3D layout {layout.name} (block EM1, one port per pad) with the parts of {circuit.name} it "
               "cannot hold, connected by net labels: every pad port to the terminal of its part. Solve the "
               f"layout first; then this design simulates the board with the devices.")
    return d, warnings
