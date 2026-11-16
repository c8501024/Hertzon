"""Voltage integration lines of lumped ports.

The S-parameters of a lumped port come from its voltage, the line integral of E across the sheet, averaged over
the integration lines of the port. EMerge starts a line at every mesh node of the sheet edge with exactly the
lowest coordinate along the field direction. With rounding, often a single node matches, a corner of the sheet,
where the field differs most from the rest of the port: the UWB dipole of #231 (a 12.8 x 3 mm port) got its
voltage on the edge of the port only, 5 degrees off the width average, and its accepted power 2 % below the
radiated power. Where several nodes match, the lines crowd at the corners, where the mesh is finest.

Here the lines are spread evenly over the width of the sheet (midpoint rule), so their average is the width
average of the voltage, the one that belongs to the total current through the port's sheet impedance.
"""
from __future__ import annotations

import numpy as np

N_LINES = 11          # lines across the width of a port
LINE_POINTS = 21      # points of each line (as EMerge)


def _hull_2d(q: np.ndarray) -> np.ndarray:
    """Convex hull (counter-clockwise, no repeated point) of the 2D points q (monotone chain)."""
    q = np.unique(np.round(q, 15), axis=0)
    if len(q) < 3:
        return q

    def half(pts):
        h = []
        for p in pts:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - \
                    (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h[:-1]
    return np.array(half(q) + half(q[::-1]))


def _span_at(hull: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lowest and highest second coordinate of the convex polygon hull (2D) on the lines first coordinate = s."""
    lo = np.full(len(s), np.inf)
    hi = np.full(len(s), -np.inf)
    a, b = hull, np.roll(hull, -1, axis=0)
    for (s0, t0), (s1, t1) in zip(a, b):
        if s1 == s0:
            continue
        u = (s - s0) / (s1 - s0)
        ok = (u >= -1e-12) & (u <= 1 + 1e-12)
        t = t0 + np.clip(u, 0, 1) * (t1 - t0)
        lo = np.where(ok, np.minimum(lo, t), lo)
        hi = np.where(ok, np.maximum(hi, t), hi)
    return lo, hi


def port_line_segments(P: np.ndarray, d, height: float, n: int = N_LINES):
    """Voltage lines (starts (n, 3), ends (n, 3)) of a sheet with nodes P (N, 3) and field direction d, evenly
    over its width. None if the sheet has no width.

    A rectangle with its edges across the field: the lines start on the low edge and run the height of the port
    along d. Any other sheet, e.g. a parallelogram between conductors whose edges are not square to the field (a
    board edge a fraction of a degree off the axis), has no level low edge: each line then runs along d from
    one side of the sheet to the other, at its place across the width. (Before, such a sheet found a single
    low node, and the port voltage was taken at its corner only: the accepted power was off by several percent
    in a lossless model.)"""
    d = np.asarray(d, float)
    d = d / np.linalg.norm(d)
    P = np.asarray(P, float)
    t = P @ d
    hgt = max(float(height), 1e-12)
    low = P[t <= t.min() + 1e-4 * hgt]
    q = P - np.outer(t, d)                          # projected onto the plane across the field
    c = q.mean(axis=0)
    _, _, vt = np.linalg.svd(q - c, full_matrices=False)
    w = vt[0]
    s_all = P @ w
    full = float(np.ptp(s_all))
    if full <= 1e-6 * hgt:
        return None
    if len(low) >= 2:
        s = low @ w
        if np.ptp(s) >= (1 - 1e-3) * full:          # a level low edge over the whole width
            order = np.argsort(s)
            s, low = s[order], low[order]
            sk = s[0] + (np.arange(n) + 0.5) / n * (s[-1] - s[0])
            starts = np.stack([np.interp(sk, s, low[:, i]) for i in range(3)], axis=1)
            return starts, starts + d * height
    # the sheet in its own plane: coordinates (s across the field, t along it)
    nrm = np.cross(w, d)
    off = float(np.mean(P @ nrm))
    hull = _hull_2d(np.stack([s_all, t], axis=1))
    sk = s_all.min() + (np.arange(n) + 0.5) / n * full
    lo, hi = _span_at(hull, sk)
    if not (np.all(np.isfinite(lo)) and np.all(hi > lo)):
        return None
    base = np.outer(sk, w) + off * nrm
    return base + np.outer(lo, d), base + np.outer(hi, d)


def edge_starts(P: np.ndarray, d: np.ndarray, height: float, n: int = N_LINES) -> np.ndarray | None:
    """Start points (n, 3) of the voltage lines evenly over the width of a sheet with nodes P (N, 3), field
    direction d (see port_line_segments). None if the sheet has no width."""
    seg = port_line_segments(P, d, height, n)
    return None if seg is None else seg[0]


def set_port_lines(bc, P: np.ndarray, n: int = N_LINES) -> bool:
    """Voltage integration lines of the lumped port bc evenly over its width; P (N, 3) are its node
    coordinates. False (and the lines of EMerge are kept) where the sheet has no width."""
    from emerge._emerge.coord import Line
    d = np.asarray(bc.Vdirection.np, float)
    d = d / np.linalg.norm(d)
    seg = port_line_segments(P, d, bc.height, n)
    if seg is None:
        return False
    bc.voltage_integration_line = [Line.from_points(a, b, LINE_POINTS) for a, b in zip(*seg)]
    return True


def spread_lumped_port_lines(m) -> None:
    """Even integration lines on every lumped port of the EMerge model m (after the mesh is generated: EMerge
    defines its own lines only for ports that have none)."""
    from emerge._emerge.physics.microwave.bcs.port_bcs import LumpedPort
    nodes = np.asarray(m.mesh.nodes, float)
    for bc in m.mw.bc.oftype(LumpedPort):
        nid = np.unique(np.asarray(m.mesh.get_nodes(bc.tags), int))
        if len(nid):
            set_port_lines(bc, nodes[:, nid].T)
