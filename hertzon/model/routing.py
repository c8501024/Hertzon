"""Automatic connections on the board: lines of one impedance between the pins the schematic connects, led round
the other copper with smooth bends, and equal phase over several of them.

A *route* (board.Route) is a microstrip of width W from a pin to a pin: its centre line is a chain of straight
pieces at right angles (and a first or last piece along a slanted port), every corner a circular bend of radius r
(on the centre line) or a 50 % mitered 90 degree bend. Where a pin is wider or narrower than W (the feed of a 3D
design block), a linear taper of a few line widths joins the two, so the line has no step in impedance.

connect() finds the routes for the connections the board does not make yet (the ratsnest): W from the impedance Z0
on the circuit's substrate (the closed-form microstrip model of the circuit simulation, at the analysis frequency),
the path by an A* search on the grid of the coordinates that matter (the two ends and the edges of the copper
around, kept at a gap of a few line widths), with a cost per bend so the paths have few of them.

match_phase() gives a group of routes the same insertion phase at a frequency f0 (or the phase of the longest one
plus an offset per route): every route that is short of the target gets a meander (a row of U-shaped detours) in
its longest straight piece, its height first from the electrical length and then corrected on the circuit model
of the route until the phase is right.

The routes are part of the circuit: expand() replaces, for the circuit simulation, the ideal connection between
the two ends of a route by the chain of its pieces (MLIN pieces, the tapers as short steps of MLIN, MMBEND for a
mitered bend, a circular bend as an MLIN of its arc length), so what the board shows is what is simulated.
"""
from __future__ import annotations

import heapq
import math

from . import board as BD
from . import circuit as CK

GAP_W = 3.0                     # edge gap to other copper, in line widths (tried smaller when no path is found)
RADIUS_W = 3.0                  # radius of a circular bend (centre line), in line widths
RADIUS_MIN_W = 1.0              # smallest radius a tight spot may shrink a bend to
TAPER_W = 3.0                   # length of a taper, in line widths (of the wider end)
TAPER_STEPS = 5                 # steps of the taper's circuit model
STYLES = ("Arc", "Miter")


class RouteError(Exception):
    pass


# ---- line width ----------------------------------------------------------------------------------------------
def substrate(project, circuit) -> tuple[str, dict]:
    """The name and evaluated values (SI) of the circuit's substrate (the first one)."""
    subs = [c for c in circuit.components if c.type == "SUBST"]
    if not subs:
        raise RouteError("the circuit has no substrate: place a Substrate (Transmission Lines)")
    ev = circuit.evaluator(project)
    p = subs[0].props
    return subs[0].name, dict(er=ev.value(p["Er"]), h=ev.length(p["H"]), t=ev.length(p["T"]),
                              tand=ev.value(p["TanD"]), cond=ev.value(p["Cond"]))


def width_for(z0: float, sub: dict, f: float) -> float:
    """Microstrip width (mm) of the impedance z0 at f on the substrate sub, with the circuit simulation's own line
    model (so the line is z0 in the simulation)."""
    from ..solver import tlines as TL
    lo, hi = 1e-6, 100 * sub["h"]
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        z = TL.microstrip(f, mid, sub["h"], sub["t"], sub["er"], sub["tand"], sub["cond"])[0].real
        if z > z0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi) * 1e3


def analysis_frequency(project, circuit) -> float:
    """The middle of the circuit's analysis range (Hz), 1 GHz without one."""
    try:
        from ..solver.circuit import analysis_span
        span = analysis_span(project, circuit)
    except Exception:  # noqa: BLE001
        span = None
    if span:
        return 0.5 * (span[0] + span[1])
    return 1e9


# ---- geometry of a route -------------------------------------------------------------------------------------
def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 0 else (0.0, 0.0)


def _turn(u, v) -> float:
    """Angle (rad) between the directions u and v."""
    return math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1])))


def simplify(pts):
    """Drop repeated points and points on a straight line."""
    out = []
    for p in pts:
        p = (round(p[0], 9), round(p[1], 9))
        if out and math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) < 1e-9:
            continue
        out.append(p)
    k = 1
    while k < len(out) - 1:
        u, v = _unit(out[k - 1], out[k]), _unit(out[k], out[k + 1])
        if abs(u[0] * v[1] - u[1] * v[0]) < 1e-9 and u[0] * v[0] + u[1] * v[1] > 0:
            del out[k]
        else:
            k += 1
    return out


def allowances(r: BD.Route) -> list[float]:
    """Length each inner corner takes from the pieces on either side: r tan(angle/2) for a circular bend, w/2 for
    a mitered one."""
    out = []
    for i in range(1, len(r.pts) - 1):
        th = _turn(_unit(r.pts[i - 1], r.pts[i]), _unit(r.pts[i], r.pts[i + 1]))
        rad = r.radii[i - 1] if i - 1 < len(r.radii) else 0.0
        out.append(rad * math.tan(th / 2) if rad > 0 else r.w / 2)
    return out


def pieces(r: BD.Route) -> list[float]:
    """Length of the straight piece between consecutive corners (what the bends and the tapers leave)."""
    al = allowances(r)
    out = []
    for j in range(len(r.pts) - 1):
        L = math.hypot(r.pts[j + 1][0] - r.pts[j][0], r.pts[j + 1][1] - r.pts[j][1])
        if j > 0:
            L -= al[j - 1]
        if j < len(r.pts) - 2:
            L -= al[j]
        out.append(L)
    return out


def centre_line(r: BD.Route, seg: float = 0.05) -> list[tuple[float, float]]:
    """The centre line with the circular bends drawn as arcs (points every ~seg of the radius' angle)."""
    pts = r.pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        rad = r.radii[i - 1] if i - 1 < len(r.radii) else 0.0
        u, v = _unit(pts[i - 1], pts[i]), _unit(pts[i], pts[i + 1])
        th = _turn(u, v)
        if rad <= 0 or th < 1e-9:
            out.append(pts[i])
            continue
        t = rad * math.tan(th / 2)
        a = (pts[i][0] - u[0] * t, pts[i][1] - u[1] * t)
        side = 1.0 if u[0] * v[1] - u[1] * v[0] > 0 else -1.0       # left turn: centre on the left
        cx, cy = a[0] - side * u[1] * rad, a[1] + side * u[0] * rad
        a0 = math.atan2(a[1] - cy, a[0] - cx)
        n = max(4, int(math.ceil(th / 0.08)))
        for k in range(n + 1):
            ang = a0 + side * th * k / n
            out.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    out.append(pts[-1])
    clean = [out[0]]
    for q in out[1:]:
        if math.hypot(q[0] - clean[-1][0], q[1] - clean[-1][1]) > 1e-7:
            clean.append(q)
    return clean


def polygon(r: BD.Route):
    """The copper of the route (shapely)."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    line = LineString(centre_line(r))
    body = line.buffer(r.w / 2, cap_style=2, join_style=1, quad_segs=16)   # the miters are cut below
    pts = r.pts
    cuts = []
    for i in range(1, len(pts) - 1):            # mitered corners: the outer half of the corner square goes
        rad = r.radii[i - 1] if i - 1 < len(r.radii) else 0.0
        if rad > 0:
            continue
        u, v = _unit(pts[i - 1], pts[i]), _unit(pts[i], pts[i + 1])
        h = r.w / 2
        c = pts[i]
        P = lambda su, sv: (c[0] + (su * u[0] + sv * v[0]) * h, c[1] + (su * u[1] + sv * v[1]) * h)  # noqa: E731
        cuts.append(Polygon([P(1, -1), P(1.02, 1.02), P(-1.02, -1.02)]))
    if cuts:
        body = body.difference(unary_union(cuts))
    for end, wq, lq in ((0, r.wa, r.la), (1, r.wb, r.lb)):
        if wq <= 0 or lq <= 0:
            continue
        p, q = (pts[0], pts[1]) if end == 0 else (pts[-1], pts[-2])
        u = _unit(p, q)
        n = (-u[1], u[0])
        wide = max(wq, r.w) / 2 + 1e-3
        e = (p[0] + u[0] * lq, p[1] + u[1] * lq)
        zone = Polygon([(p[0] + n[0] * wide, p[1] + n[1] * wide), (e[0] + n[0] * wide, e[1] + n[1] * wide),
                        (e[0] - n[0] * wide, e[1] - n[1] * wide), (p[0] - n[0] * wide, p[1] - n[1] * wide)])
        trap = Polygon([(p[0] + n[0] * wq / 2, p[1] + n[1] * wq / 2), (e[0] + n[0] * r.w / 2, e[1] + n[1] * r.w / 2),
                        (e[0] - n[0] * r.w / 2, e[1] - n[1] * r.w / 2), (p[0] - n[0] * wq / 2, p[1] - n[1] * wq / 2)])
        body = body.difference(zone).union(trap)
    return body.buffer(0)


def length(r: BD.Route) -> float:
    """Length of the centre line (mm)."""
    c = centre_line(r, 0.02)
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(c, c[1:]))


# ---- circuit model of a route --------------------------------------------------------------------------------
def elements(r: BD.Route) -> list[tuple[str, dict[str, float]]]:
    """The chain of circuit parts from pin a to pin b: (type, {W, L in mm})."""
    out: list[tuple[str, dict[str, float]]] = []
    pc = pieces(r)
    n = len(pc)

    def taper(wq, lq, rev):
        steps = []
        for k in range(TAPER_STEPS):
            t = (k + 0.5) / TAPER_STEPS
            steps.append(("MLIN", {"W": wq + (r.w - wq) * t, "L": lq / TAPER_STEPS}))
        return steps[::-1] if rev else steps
    for j in range(n):
        L = pc[j]
        if j == 0 and r.wa > 0 and r.la > 0:
            out += taper(r.wa, r.la, False)
            L -= r.la
        tail = []
        if j == n - 1 and r.wb > 0 and r.lb > 0:
            tail = taper(r.wb, r.lb, True)
            L -= r.lb
        if L > 1e-6:
            out.append(("MLIN", {"W": r.w, "L": L}))
        out += tail
        if j < n - 1:
            rad = r.radii[j] if j < len(r.radii) else 0.0
            th = _turn(_unit(r.pts[j], r.pts[j + 1]), _unit(r.pts[j + 1], r.pts[j + 2]))
            if rad > 0:
                out.append(("MLIN", {"W": r.w, "L": rad * th}))      # circular bend: a line of its arc length
            else:
                out.append(("MMBEND", {"W": r.w}))
    return out


def _components(r: BD.Route, prefix: str, subst: str) -> list[CK.Component]:
    out = []
    for i, (t, q) in enumerate(elements(r) or [("MLIN", {"W": r.w, "L": 0.0})]):
        props = {"Subst": subst, **{k: f"{v:.9g}mm" for k, v in q.items()}}
        out.append(CK.Component(f"{prefix}_{i + 1}", t, props=props))
    return out


def abcd(r: BD.Route, sub: dict, f: float):
    """ABCD matrix of the route at f (closed-form models of the circuit simulation)."""
    import numpy as np

    from ..solver import circuit as SC
    M = np.eye(2, dtype=complex)
    for t, q in elements(r):
        c = CK.Component("x", t, props={k: f"{v:.9g}mm" for k, v in q.items()})
        v = ({k: q[k] * 1e-3 for k in q}, sub)
        Y = SC.element_y(c, f, v)
        y11, y12, y21, y22 = Y[0, 0], Y[0, 1], Y[1, 0], Y[1, 1]
        M = M @ np.array([[-y22 / y21, -1 / y21], [-(y11 * y22 - y12 * y21) / y21, -y11 / y21]])
    return M


def phase(r: BD.Route, sub: dict, f: float, z0: float | None = None) -> float:
    """Insertion phase of the route (degrees, positive, the delay: -arg S21 unwrapped by the electrical length)."""
    import numpy as np

    from ..solver import tlines as TL
    M = abcd(r, sub, f)
    z = z0 or r.z0
    a, b, c, d = M[0, 0], M[0, 1], M[1, 0], M[1, 1]
    s21 = 2 / (a + b / z + c * z + d)
    raw = -math.degrees(float(np.angle(s21)))
    g = TL.microstrip(f, r.w * 1e-3, sub["h"], sub["t"], sub["er"], sub["tand"], sub["cond"])[1]
    est = math.degrees(float(np.imag(g)) * length(r) * 1e-3)
    k = round((est - raw) / 360.0)
    return raw + 360.0 * k


# ---- path search ---------------------------------------------------------------------------------------------
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def find_path(s, ds, e, de, blocked, bend_cost: float, bounds) -> list[tuple[float, float]] | None:
    """Orthogonal path from s (leaving in the direction ds, any unit vector) to e (arriving so that it can turn
    into the direction de there): the corners from s to e, or None. blocked: the area the centre line must keep
    out of (shapely, prepared); bounds: (x0, y0, x1, y1) of the search."""
    from shapely.geometry import LineString
    xs = {s[0], e[0], bounds[0], bounds[2]}
    ys = {s[1], e[1], bounds[1], bounds[3]}
    eps = 1e-3
    geo = blocked.context if hasattr(blocked, "context") else blocked
    for g in getattr(geo, "geoms", [geo]):
        if g.is_empty:
            continue
        x0, y0, x1, y1 = g.bounds
        xs.update((x0 - eps, x1 + eps))
        ys.update((y0 - eps, y1 + eps))
    xs = sorted(x for x in xs if bounds[0] - 1e-9 <= x <= bounds[2] + 1e-9)
    ys = sorted(y for y in ys if bounds[1] - 1e-9 <= y <= bounds[3] + 1e-9)
    xs = sorted(set(xs + [(a + b) / 2 for a, b in zip(xs, xs[1:])]))
    ys = sorted(set(ys + [(a + b) / 2 for a, b in zip(ys, ys[1:])]))
    ix = {x: i for i, x in enumerate(xs)}
    iy = {y: i for i, y in enumerate(ys)}
    start, goal = (ix[s[0]], iy[s[1]]), (ix[e[0]], iy[e[1]])
    ok_cache: dict = {}

    def free(a, b):
        key = (a, b) if a <= b else (b, a)
        v = ok_cache.get(key)
        if v is None:
            p, q = (xs[a[0]], ys[a[1]]), (xs[b[0]], ys[b[1]])
            v = not blocked.intersects(LineString([p, q]))
            ok_cache[key] = v
        return v

    def h(n):
        return abs(xs[n[0]] - e[0]) + abs(ys[n[1]] - e[1])
    # states: (node, direction index); the start may leave in any direction not turned back by more than 90 deg
    heap = []
    best: dict = {}
    came: dict = {}
    for k, m in enumerate(DIRS):
        dot = m[0] * ds[0] + m[1] * ds[1]
        if dot < -1e-9:
            continue
        cost = 0.0 if dot > 1 - 1e-9 else bend_cost
        st = (start, k)
        best[st] = cost
        heapq.heappush(heap, (cost + h(start), cost, st))
        came[st] = None
    end_state = None
    while heap:
        _f, g, st = heapq.heappop(heap)
        if g > best.get(st, math.inf) + 1e-12:
            continue
        node, k = st
        if node == goal:
            m = DIRS[k]
            dot = m[0] * de[0] + m[1] * de[1]
            if dot >= -1e-9 or node == start:
                end_state = st
                break
        m = DIRS[k]
        for k2, m2 in enumerate(DIRS):
            if m2[0] == -m[0] and m2[1] == -m[1]:
                continue
            nxt = (node[0] + m2[0], node[1] + m2[1])
            if not (0 <= nxt[0] < len(xs) and 0 <= nxt[1] < len(ys)):
                continue
            if not free(node, nxt):
                continue
            step = abs(xs[nxt[0]] - xs[node[0]]) + abs(ys[nxt[1]] - ys[node[1]])
            g2 = g + step + (bend_cost if k2 != k else 0.0)
            st2 = (nxt, k2)
            if g2 < best.get(st2, math.inf) - 1e-12:
                best[st2] = g2
                came[st2] = st
                heapq.heappush(heap, (g2 + h(nxt), g2, st2))
    if end_state is None:
        return None
    path = []
    st = end_state
    while st is not None:
        path.append((xs[st[0][0]], ys[st[0][1]]))
        st = came[st]
    return path[::-1]


# ---- routing -------------------------------------------------------------------------------------------------
def pin_world(parts, board, ref):
    """(position, direction, width) of the pin ref = (part, pin index) on the board."""
    name, k = ref
    bp, pl = parts[name], board.placements[name]
    pin = bp.pins[k] if k < len(bp.pins) else None
    if pin is None:
        raise RouteError(f"{name} has no pin {k + 1}")
    d = pl.wvec(pin.d)
    n = math.hypot(*d) or 1.0
    return pl.world((pin.x, pin.y)), (d[0] / n, d[1] / n), pin.w


def fit_radii(r: BD.Route, want) -> list[str]:
    """Bend radii as large as want where the pieces leave room (a circular bend at every corner that is not 90
    degrees in any case, a mitered one where want is 0); a corner without room for even the smallest radius
    falls back to a mitered bend (90 degrees) or keeps the smallest radius. Returns warnings."""
    warn = []
    pts = r.pts
    nc = len(pts) - 2
    th = [_turn(_unit(pts[i - 1], pts[i]), _unit(pts[i], pts[i + 1])) for i in range(1, len(pts) - 1)]
    rad = []
    wants = list(want) if isinstance(want, (list, tuple)) else [want] * nc
    for i in range(nc):
        right = abs(th[i] - math.pi / 2) < 1e-6
        wi = wants[i] if i < len(wants) else 0.0
        rad.append(wi if (wi > 0 or not right) else 0.0)
        if rad[-1] <= 0 and not right:
            rad[-1] = RADIUS_W * r.w
    seg = [math.hypot(pts[j + 1][0] - pts[j][0], pts[j + 1][1] - pts[j][1]) for j in range(len(pts) - 1)]
    for _ in range(4):
        al = [rad[i] * math.tan(th[i] / 2) if rad[i] > 0 else r.w / 2 for i in range(nc)]
        changed = False
        for j in range(len(seg)):
            need = (al[j - 1] if j > 0 else 0.0) + (al[j] if j < nc else 0.0)
            need += (r.la if j == 0 else 0.0) + (r.lb if j == len(seg) - 1 else 0.0)
            if need <= seg[j] + 1e-9:
                continue
            room = seg[j] - (r.la if j == 0 else 0.0) - (r.lb if j == len(seg) - 1 else 0.0)
            ends = [i for i in (j - 1, j) if 0 <= i < nc and rad[i] > 0]
            if not ends:
                continue
            fixed = sum(al[i] for i in (j - 1, j) if 0 <= i < nc and rad[i] <= 0)
            share = max(room - fixed, 0.0) / len(ends)
            for i in ends:
                r_new = share / math.tan(th[i] / 2) if th[i] > 1e-9 else rad[i]
                if r_new < rad[i] - 1e-9:
                    rad[i] = r_new
                    changed = True
        if not changed:
            break
    for i in range(nc):
        if 0 < rad[i] < RADIUS_MIN_W * r.w:
            if abs(th[i] - math.pi / 2) < 1e-6:
                rad[i] = 0.0
            else:
                rad[i] = RADIUS_MIN_W * r.w
                warn.append("a slanted bend has less room than its radius")
    r.radii = rad
    pc = pieces(r)
    if any(p < -1e-6 for p in pc):
        warn.append("tight jog: the bends of the route overlap; line the parts up or move them apart")
    return warn


def _obstacles(parts, board, nodes, skip_parts, routes, w, gap):
    """Copper the centre line of a new line of width w has to keep out of: other parts at gap, the two parts it
    connects just off their copper, the routes so far at gap."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    geos = []
    for name, bp in parts.items():
        pl = board.placements.get(name)
        if pl is None:
            continue
        own = name in skip_parts
        for cu in bp.copper:
            if len(cu.pts) < 3:
                continue
            poly = Polygon([pl.world(p) for p in cu.pts], [[pl.world(p) for p in h] for h in cu.holes]).buffer(0)
            geos.append(poly.buffer(w / 2 + (0.02 if own else gap)))
        if bp.type in ("PORT", "VDC", "IDC") or own:
            continue
        for vx, vy, dv, _ in bp.vias:
            wx, wy = pl.world((vx, vy))
            from shapely.geometry import Point
            geos.append(Point(wx, wy).buffer(dv / 2 + w / 2 + gap))
    for r in routes:
        geos.append(polygon(r).buffer(w / 2 + gap))
    return unary_union(geos) if geos else None


def route_one(parts, board, nodes, a, b, w: float, style: str, gap_w: float = GAP_W, routes=(), z0: float = 50.0):
    """A route of width w from the pin a to the pin b (refs (part, pin index)) round the copper on the board and
    the routes given; raises RouteError when there is no path. Returns (route, warnings)."""
    from shapely.prepared import prep
    pa, da, wa = pin_world(parts, board, a)
    pb, db, wb = pin_world(parts, board, b)
    want_r = RADIUS_W * w if style == "Arc" else 0.0
    taper = lambda wq: (wq, TAPER_W * max(wq, w)) if wq > 0 and abs(wq - w) > 0.02 * w else (0.0, 0.0)  # noqa: E731
    (wa_, la), (wb_, lb) = taper(wa), taper(wb)
    bend_allow = max(RADIUS_W * w, w / 2)
    warnings = []
    for gfac in (gap_w, 2.0, 1.0, 0.5):
        gap = gfac * w
        blocked = _obstacles(parts, board, nodes, {a[0], b[0]}, routes, w, gap)
        from shapely.geometry import LineString
        tight = _obstacles(parts, board, nodes, {a[0], b[0]}, routes, w, 0.25 * w)

        def stub(p, d, ln):
            """The end of the straight piece out of a pin: at least ln, longer while the gap is not reached yet
            (as long as the piece itself keeps clear of the copper around)."""
            q = (p[0] + d[0] * ln, p[1] + d[1] * ln)
            for _ in range(20):
                if blocked is None or not blocked.contains(_pt(q)):
                    break
                q2 = (q[0] + d[0] * w, q[1] + d[1] * w)
                if tight is not None and tight.intersects(LineString([q, q2])):
                    break
                q = q2
            return q
        s = stub(pa, da, la + bend_allow + 0.01)
        e = stub(pb, db, lb + bend_allow + 0.01)
        allc = [p for bp_name, bp in parts.items() if bp_name in board.placements
                for cu in bp.copper for p in (board.placements[bp_name].world(q) for q in cu.pts)]
        xs = [p[0] for p in allc] + [s[0], e[0]]
        ys = [p[1] for p in allc] + [s[1], e[1]]
        m = 10 * w + gap
        bounds = (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
        # the stubs themselves must be free
        if tight is not None:            # the stubs leave their pins: the pins' own copper is right there
            stubs_free = not tight.intersects(LineString([pa, s]).difference(_near(pa, w / 2 + 0.05))) and \
                not tight.intersects(LineString([pb, e]).difference(_near(pb, w / 2 + 0.05)))
            if not stubs_free:
                continue
        pb_ = prep(blocked) if blocked is not None else _Never()
        if blocked is not None and (blocked.contains(_pt(s)) or blocked.contains(_pt(e))):
            continue
        path = find_path(s, da, e, (-db[0], -db[1]), pb_, bend_cost=2 * bend_allow + 2 * w, bounds=bounds)
        if path is None:
            continue
        full = _centre_legs(simplify([pa] + path + [pb]), pb_, s, e)
        full = _smooth(full, pb_, s, e, w, want_r or RADIUS_W * w, la, lb)
        path = full[1:-1] if len(full) > 2 else path
        if gfac < gap_w:
            warnings.append(f"{a[0]}–{b[0]}: runs within {gap_w:g} line widths of other copper (gap "
                            f"{gfac:g} line widths)")
        r = BD.Route(a, b, simplify([pa] + path + [pb]), w, [], wa_, wb_, la, lb, z0)
        warnings += [f"{a[0]}–{b[0]}: {x}" for x in fit_radii(r, want_r)]
        return r, warnings
    raise RouteError(f"no path from {a[0]} pin {a[1] + 1} to {b[0]} pin {b[1] + 1}: move the parts apart")


def _line_x(p, u, q, v):
    """Intersection of the line p + t u with the line q + s v: (point, t, s), or None if parallel."""
    den = u[0] * v[1] - u[1] * v[0]
    if abs(den) < 1e-12:
        return None
    dx, dy = q[0] - p[0], q[1] - p[1]
    t = (dx * v[1] - dy * v[0]) / den
    s_ = (dx * u[1] - dy * u[0]) / den
    return (p[0] + u[0] * t, p[1] + u[1] * t), t, s_


def _free(blocked, pts, s, e, first: bool, last: bool) -> bool:
    """The polyline pts keeps clear of blocked; a piece out of a pin is checked from its stub end on."""
    from shapely.geometry import LineString
    pts = list(pts)
    if first:
        pts[0] = s
    if last:
        pts[-1] = e
    return not any(blocked.intersects(LineString([a, b])) for a, b in zip(pts, pts[1:])
                   if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-9)


def _smooth(pts, blocked, s, e, w: float, r: float, la: float, lb: float):
    """Two clean-ups of the orthogonal path from pin to pin (pts):
    * a slanted pin is reached with one bend: the last straight run goes on until it meets the pin's axis, instead
      of turning to an axis next to the pin and then onto the slant (also at the start);
    * a small sideways jog (a Z with a short middle leg) becomes a gentle S: two shallow bends and a slanted
      piece, instead of two 90 degree bends on top of each other."""
    pts = [tuple(p) for p in pts]

    def slanted(u):
        return abs(u[0]) > 1e-6 and abs(u[1]) > 1e-6

    for end in (1, 0):
        q = pts if end else pts[::-1]
        stub, lt = (e, lb) if end else (s, la)
        if len(q) >= 4 and slanted(_unit(q[-2], q[-1])):
            P, C = q[-4], q[-3]
            u, v = _unit(P, C), _unit(q[-2], q[-1])
            hit = _line_x(P, u, q[-1], (-v[0], -v[1]))
            if hit is not None:
                X, t, back = hit
                th = _turn(u, v)
                need = r * math.tan(th / 2) + lt
                P_ = ((s if end else e) if len(q) == 4 else P)     # a piece out of a pin: from its stub on
                if t > 1e-6 and back >= need and th < math.radians(120) and \
                        _free(blocked, [P_, X], s, e, False, False):
                    q = q[:-3] + [X, q[-1]]
                    pts = q if end else q[::-1]
    # gentle S for small jogs
    i = 1
    while i < len(pts) - 2:
        a, b, c, d = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        u1, u2, u3 = _unit(a, b), _unit(b, c), _unit(c, d)
        jog = math.hypot(c[0] - b[0], c[1] - b[1])
        if abs(u1[0] - u3[0]) < 1e-9 and abs(u1[1] - u3[1]) < 1e-9 and abs(u1[0] * u2[0] + u1[1] * u2[1]) < 1e-9 \
                and 1e-6 < jog < 3 * w:
            th = min(math.radians(45), 0.9 * math.sqrt(jog / r))
            half = jog / math.tan(th) / 2
            nb = (b[0] - u1[0] * half, b[1] - u1[1] * half)
            nc = (c[0] + u3[0] * half, c[1] + u3[1] * half)
            ta = r * math.tan(th / 2)
            lim_a = s if i - 1 == 0 else a
            lim_d = e if i + 2 == len(pts) - 1 else d
            room_a = (nb[0] - lim_a[0]) * u1[0] + (nb[1] - lim_a[1]) * u1[1]
            room_d = (lim_d[0] - nc[0]) * u3[0] + (lim_d[1] - nc[1]) * u3[1]
            if room_a >= ta and room_d >= ta and _free(blocked, [a, nb, nc, d], s, e, i - 1 == 0,
                                                       i + 2 == len(pts) - 1):
                pts[i], pts[i + 1] = nb, nc
        i += 1
    return pts


def _centre_legs(path, blocked, s, e):
    """In the centre line path (from pin to pin), a leg between two corners whose neighbours run on in the same
    direction (a Z) is moved to the middle of the room they leave, where that keeps clear of the copper and of
    the straight pieces out of the pins (to s and e): the bends then sit away from the pins."""
    from shapely.geometry import LineString
    pts = [tuple(p) for p in path]
    for i in range(1, len(pts) - 2):
        a, b, c, d = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        u1, u2, u3 = _unit(a, b), _unit(b, c), _unit(c, d)
        if abs(u1[0] - u3[0]) > 1e-9 or abs(u1[1] - u3[1]) > 1e-9 or abs(u1[0] * u2[0] + u1[1] * u2[1]) > 1e-9:
            continue
        ax = 0 if abs(u1[0]) > 0.5 else 1               # the coordinate the leg slides along
        lo, hi = a[ax], d[ax]
        if i - 1 == 0:
            lo = s[ax]
        if i + 2 == len(pts) - 1:
            hi = e[ax]
        if (hi - lo) * (d[ax] - a[ax]) <= 0:
            continue
        for frac in (0.5, 0.35, 0.2):
            want = lo + (hi - lo) * frac
            nb = (want, b[1]) if ax == 0 else (b[0], want)
            nc = (want, c[1]) if ax == 0 else (c[0], want)
            a_ = s if i - 1 == 0 else a                  # the pieces out of the pins touch their own copper
            d_ = e if i + 2 == len(pts) - 1 else d
            segs = [LineString([a_, nb]), LineString([nb, nc]), LineString([nc, d_])]
            if not any(blocked.intersects(g) for g in segs):
                pts[i], pts[i + 1] = nb, nc
                break
    return pts


class _Never:
    is_empty = True

    def intersects(self, _g):
        return False

    def contains(self, _g):
        return False


def _pt(p):
    from shapely.geometry import Point
    return Point(p)


def _near(p, r):
    from shapely.geometry import Point
    return Point(p).buffer(r)


def connect(project, circuit, board: BD.Board, z0: float, style: str = "Arc", only: set[str] | None = None,
            f: float | None = None) -> tuple[BD.Board, list[str]]:
    """Route every connection of the ratsnest (only those touching the parts only, if given) with lines of the
    impedance z0; the board with the new routes and the warnings."""
    parts, nodes, notes = BD.board_parts(project, circuit)
    board, more = BD.complete(project, circuit, board)
    subst, sub = substrate(project, circuit)
    f = f or analysis_frequency(project, circuit)
    w = width_for(z0, sub, f)
    warnings = list(more)
    todo = BD.connections(parts, board, nodes)
    if only:
        todo = [c for c in todo if c[1][0] in only or c[2][0] in only]
    todo.sort(key=lambda c: math.dist(pin_world(parts, board, c[1])[0], pin_world(parts, board, c[2])[0]))
    for _net, a, b in todo:
        try:
            r, ws = route_one(parts, board, nodes, a, b, w, style, routes=board.routes, z0=z0)
        except RouteError as e:
            warnings.append(str(e))
            continue
        r.ga, r.gb = BD.pin_group(parts, board, nodes, a), BD.pin_group(parts, board, nodes, b)
        board.routes.append(r)
        warnings += ws
    board.opts.update({"z0": z0, "style": style})
    return board, warnings


# ---- equal phase ---------------------------------------------------------------------------------------------
def _meander(r: BD.Route, seg: int, side: int, n: int, hgt: float, pitch: float) -> BD.Route:
    """The route with n U-shaped detours of height hgt on side (+1 left, -1 right of the direction of travel) in
    the middle of its straight piece seg."""
    pts = r.pts
    p, q = pts[seg], pts[seg + 1]
    u = _unit(p, q)
    nv = (-u[1] * side, u[0] * side)
    L = math.hypot(q[0] - p[0], q[1] - p[1])
    span = (2 * n - 1) * pitch
    t0 = (L - span) / 2
    new = []
    for i in range(n):
        a = t0 + 2 * i * pitch
        b = a + pitch
        for t, hh in ((a, 0), (a, hgt), (b, hgt), (b, 0)):
            new.append((p[0] + u[0] * t + nv[0] * hh, p[1] + u[1] * t + nv[1] * hh))
    out = BD.Route(**{**r.__dict__})
    out.pts = simplify(pts[:seg + 1] + new + pts[seg + 1:])
    old_r = list(r.radii)
    if len(out.pts) - 2 == len(old_r) + 4 * n:      # the corners of the detours come after corner seg
        out.radii = old_r[:seg] + [None] * (4 * n) + old_r[seg:]
    else:
        out.radii = [None] * (len(out.pts) - 2)
    return out


def match_phase(project, circuit, board: BD.Board, indices: list[int], f0: float, offsets: dict[int, float] | None
                = None, tol: float = 0.2) -> tuple[BD.Board, list[str], dict[int, float]]:
    """Give the routes indices the same insertion phase at f0 (plus offsets[i] degrees): the routes short of the
    target get a meander (see the module notes). Returns the board, warnings and the phase of every route."""
    offsets = offsets or {}
    parts, nodes, _ = BD.board_parts(project, circuit)
    _, sub = substrate(project, circuit)
    routes = board.routes
    base = {i: routes[i] for i in indices}
    # meanders from an earlier matching are taken out first: start from the plain routes
    for i, r in base.items():
        if r.plain:
            r2 = BD.Route(**{**r.__dict__})
            r2.pts = [tuple(p) for p in r.plain]
            r2.plain = []
            want = RADIUS_W * r.w if any(x > 0 for x in r.radii) else 0.0
            fit_radii(r2, want)
            base[i] = r2
    ph = {i: phase(r, sub, f0) for i, r in base.items()}
    target = max(ph[i] - offsets.get(i, 0.0) for i in base)
    warnings = []

    def beta(r):
        from ..solver import tlines as TL
        return float(TL.microstrip(f0, r.w * 1e-3, sub["h"], sub["t"], sub["er"], sub["tand"], sub["cond"])[1].imag)
    # a route that needs only a little more would get a detour too small to build: then all get one
    mins = {i: math.degrees(beta(r) * 2 * r.w * 1e-3) * 1.5 for i, r in base.items()}
    need = {i: target + offsets.get(i, 0.0) - ph[i] for i in base}
    if any(tol < need[i] < mins[i] for i in base):
        target += max(mins.values())
    result = {}
    for i, r in base.items():
        goal = target + offsets.get(i, 0.0)
        if goal - ph[i] <= tol:
            routes[i] = r
            result[i] = ph[i]
            continue
        others = [x for j, x in enumerate(routes) if j != i]
        blocked = _obstacles(parts, board, nodes, {r.a[0], r.b[0]}, others, r.w, 1.5 * r.w)
        pc = pieces(r)
        rad = max(r.radii) if r.radii else 0.0
        rm = min(rad, 1.5 * r.w) if rad > 0 else 0.0
        pitch = max(2 * rm, r.w + 2.0 * r.w)
        order = sorted(range(len(pc)), key=lambda j: -pc[j])
        done = False
        for j in order:
            room = pc[j] - (r.la if j == 0 else 0.0) - (r.lb if j == len(pc) - 1 else 0.0) - 2 * max(rm, r.w)
            nmax = int((room + pitch) // (2 * pitch))
            if nmax < 1:
                continue
            dl = (goal - ph[i]) / math.degrees(beta(r) * 1e-3)        # mm of line still missing
            for n in range(1, nmax + 1):
                hgt = max(dl / (2 * n), max(2 * rm, r.w) + 1e-3)
                if hgt > 25 * r.w * max(1, n / 2) and n < nmax:
                    continue
                for side in (1, -1):
                    cand = None
                    h_lo, h_hi = None, None
                    hh = hgt
                    for _ in range(12):                              # secant on the height: the exact phase
                        cand = _meander(r, j, side, n, hh, pitch)
                        fit_radii(cand, [rm if x is None else x for x in cand.radii])
                        err = phase(cand, sub, f0) - goal
                        if abs(err) <= tol:
                            break
                        if err < 0:
                            h_lo = (hh, err)
                        else:
                            h_hi = (hh, err)
                        if h_lo and h_hi:
                            hh = h_lo[0] - h_lo[1] * (h_hi[0] - h_lo[0]) / (h_hi[1] - h_lo[1])
                        else:
                            hh = max(max(2 * rm, r.w) + 1e-3, hh - err / math.degrees(beta(r) * 1e-3) / (2 * n))
                    if cand is None or abs(err) > max(tol, 1.0):
                        continue
                    if blocked is not None and blocked.intersects(polygon(cand).difference(polygon(r))):
                        continue
                    cand.plain = [tuple(p) for p in (r.plain or r.pts)]
                    routes[i] = cand
                    result[i] = phase(cand, sub, f0)
                    done = True
                    break
                if done:
                    break
            if done:
                break
        if not done:
            routes[i] = r
            result[i] = ph[i]
            warnings.append(f"{r.a[0]}–{r.b[0]}: no room for a meander of {goal - ph[i]:.1f} deg; move the parts "
                            "apart or straighten the route")
    group = board.opts.get("next_group", 1)
    for i in indices:
        routes[i].group = f"G{group}"
        routes[i].offset = offsets.get(i, 0.0)
    board.opts["next_group"] = group + 1
    board.opts["f0"] = f0
    return board, warnings, result


# ---- the routes in the circuit simulation --------------------------------------------------------------------
def expand(project, design, comps, nodes_of, n: int):
    """The netlist with the routes of the design's board in place of the ideal connections they make (see the
    module notes): (components, nodes, node count, notes)."""
    board = BD.load(design)
    if board is None or not board.routes or board.opts.get("simulate", True) is False:
        return comps, nodes_of, n, []
    try:
        subst, _sub = substrate(project, design)
    except RouteError as e:
        return comps, nodes_of, n, [f"Board routes left out: {e}"]
    orig = nodes_of
    nodes_of = {k: list(v) for k, v in nodes_of.items()}
    notes = []
    group_node: dict[tuple[str, int], int] = {}
    comps = list(comps)
    used = 0

    def node_of(group):
        known = [group_node[p] for p in group if p in group_node]
        return known[0] if known else None

    for idx, r in enumerate(board.routes):
        a, b = tuple(r.a), tuple(r.b)
        try:
            na, nb = orig[a[0]][a[1]], orig[b[0]][b[1]]
        except (KeyError, IndexError):
            notes.append(f"Route {idx + 1} ({a[0]}–{b[0]}): a part or pin it connects is gone; left out")
            continue
        if na != nb or na == 0:
            notes.append(f"Route {idx + 1} ({a[0]}–{b[0]}): the schematic no longer connects its two pins; left out")
            continue
        ga = [tuple(p) for p in (r.ga or [a])]
        gb = [tuple(p) for p in (r.gb or [b])]
        xa = node_of(ga)
        xb = node_of(gb)
        if xa is None and xb is None:
            xa = na                           # the first group of the net keeps the net's node
            for p in ga:
                group_node[p] = xa
        if xa is None:
            n += 1
            xa = n
            for p in ga:
                group_node[p] = xa
        if xb is None:
            n += 1
            xb = n
            for p in gb:
                group_node[p] = xb
        for p in ga + gb:
            if p[0] in nodes_of and p[1] < len(nodes_of[p[0]]):
                nodes_of[p[0]][p[1]] = group_node[p]
        chain = _components(r, f"Route{idx + 1}", subst)
        prev = xa
        for k, c in enumerate(chain):
            if k == len(chain) - 1:
                nxt = xb
            else:
                n += 1
                nxt = n
            comps.append(c)
            nodes_of[c.name] = [prev, nxt]
            prev = nxt
        used += 1
    if used:
        notes.append(f"{used} line(s) routed on the board are part of this simulation (Board view)")
    return comps, nodes_of, n, notes
