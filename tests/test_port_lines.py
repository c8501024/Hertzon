"""Voltage integration lines of lumped ports evenly over the width of the sheet (#231): EMerge started them at
the edge nodes with exactly the lowest coordinate, often a single corner node, and the S-parameters of a wide
port took the voltage at its corner (UWB dipole: accepted power 2 % below the radiated power)."""
from types import SimpleNamespace as NS

import numpy as np

from hertzon.solver import port_lines


def _sheet(nx=9, nz=4, w=12.8e-3, h=3e-3, jitter=1e-19):
    x, z = np.meshgrid(np.linspace(-w / 2, w / 2, nx), np.linspace(-h / 2, h / 2, nz), indexing="ij")
    P = np.stack([x.ravel(), np.zeros(x.size), z.ravel()], axis=1)
    rng = np.random.default_rng(1)
    P[:, 2] += jitter * rng.standard_normal(len(P))          # rounding: the low edge is not exactly level
    return P


def test_lines_are_spread_evenly_over_the_width():
    P = _sheet()
    starts = port_lines.edge_starts(P, (0, 0, 1), 3e-3, n=4)
    assert starts.shape == (4, 3)
    assert np.allclose(np.sort(starts[:, 0]), [-4.8e-3, -1.6e-3, 1.6e-3, 4.8e-3])
    assert np.allclose(starts[:, 2], -1.5e-3, atol=1e-12)


def test_crowded_corner_nodes_do_not_weigh_more():
    P = _sheet()
    corner = np.array([[-6.4e-3 + 1e-5 * k, 0, -1.5e-3] for k in range(30)])   # a mesh refined at a corner
    starts = port_lines.edge_starts(np.vstack([P, corner]), (0, 0, 1), 3e-3, n=11)
    assert abs(starts[:, 0].mean()) < 1e-9


def test_port_gets_the_even_lines():
    bc = NS(Vdirection=NS(np=np.array([0.0, 0.0, 1.0])), height=3e-3, voltage_integration_line=[])
    assert port_lines.set_port_lines(bc, _sheet())
    lines = bc.voltage_integration_line
    assert len(lines) == port_lines.N_LINES
    for ln in lines:
        assert np.isclose(ln.zs[0], -1.5e-3) and np.isclose(ln.zs[-1], 1.5e-3)
    assert abs(np.mean([ln.xs[0] for ln in lines])) < 1e-9


def test_a_sheet_without_width_keeps_the_lines_of_emerge():
    P = np.array([[0.0, 0.0, -1.5e-3], [0.0, 0.0, 0.0], [0.0, 0.0, 1.5e-3]])
    bc = NS(Vdirection=NS(np=np.array([0.0, 0.0, 1.0])), height=3e-3, voltage_integration_line=["emerge"])
    assert not port_lines.set_port_lines(bc, P)
    assert bc.voltage_integration_line == ["emerge"]
