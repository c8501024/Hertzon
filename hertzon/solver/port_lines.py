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


def edge_starts(P: np.ndarray, d: np.ndarray, height: float, n: int = N_LINES) -> np.ndarray | None:
    """Start points (n, 3) evenly over the width of the low edge of a sheet with nodes P (N, 3), field
    direction d. None if the edge has no width (a single node)."""
    d = np.asarray(d, float)
    d = d / np.linalg.norm(d)
    t = P @ d
    low = P[t <= t.min() + 1e-4 * max(float(height), 1e-12)]
    if len(low) < 2:
        return None
    q = low - np.outer(low @ d, d)                      # projected onto the plane across the field
    _, _, vt = np.linalg.svd(q - q.mean(axis=0), full_matrices=False)
    w = vt[0]
    s = low @ w
    if np.ptp(s) <= 1e-6 * max(float(height), 1e-12):
        return None
    order = np.argsort(s)
    s, low = s[order], low[order]
    sk = s[0] + (np.arange(n) + 0.5) / n * (s[-1] - s[0])
    return np.stack([np.interp(sk, s, low[:, i]) for i in range(3)], axis=1)


def set_port_lines(bc, P: np.ndarray, n: int = N_LINES) -> bool:
    """Voltage integration lines of the lumped port bc evenly over its width; P (N, 3) are its node
    coordinates. False (and the lines of EMerge are kept) where the sheet has no width."""
    from emerge._emerge.coord import Line
    d = np.asarray(bc.Vdirection.np, float)
    d = d / np.linalg.norm(d)
    starts = edge_starts(P, d, bc.height, n)
    if starts is None:
        return False
    bc.voltage_integration_line = [Line.from_points(a, a + d * bc.height, LINE_POINTS) for a in starts]
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
