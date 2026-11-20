"""Far field over an infinite ground plane (image theory): a far-field surface open at a flat conducting floor
(PML or radiation on the other five sides) is mirrored at the floor, and there is no field behind it. Checked
against the closed surface around the source and its image."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.model.expr import Evaluator  # noqa: E402
from hertzon.solver.bridge import box_quadrature  # noqa: E402
from hertzon.solver.farfield_post import compute_sphere  # noqa: E402

F = 3e9
K = 2 * np.pi * F / 299792458.0
ETA = 376.730313668


def _dipole(P, z0):
    """E, H (3, N) of a z-directed Hertzian dipole (I l = 1 A m) at (0, 0, z0), time convention e^{+j w t}."""
    r = P - np.array([[0.0], [0.0], [z0]])
    R = np.linalg.norm(r, axis=0)
    ct, st = r[2] / R, np.hypot(r[0], r[1]) / R
    ph = np.arctan2(r[1], r[0])
    e = np.exp(-1j * K * R)
    kr = K * R
    Hph = 1j * K / (4 * np.pi * R) * (1 + 1 / (1j * kr)) * st * e
    Er = ETA / (2 * np.pi * R ** 2) * (1 + 1 / (1j * kr)) * ct * e
    Et = 1j * ETA * K / (4 * np.pi * R) * (1 + 1 / (1j * kr) - 1 / kr ** 2) * st * e
    rh = r / R
    th = np.stack([ct * np.cos(ph), ct * np.sin(ph), -st])
    phh = np.stack([-np.sin(ph), np.cos(ph), np.zeros_like(ph)])
    return Er * rh + Et * th, Hph * phh


def _huy(bounds, z0, skip=()):
    vis, wns = box_quadrature(np.array(bounds, float), 0.01, set(skip))
    E1, H1 = _dipole(vis, z0)
    E2, H2 = _dipole(vis, -z0)               # the image of a vertical electric dipole over a conductor
    return {"vis": vis, "wns": wns, "E_0_1": E1 + E2, "H_0_1": H1 + H2, "freq_0": np.array([F]),
            "Prad_0_1": np.array([1.0])}


PROPS = {"PhiStart": "0deg", "PhiStop": "90deg", "PhiStep": "45deg",
         "ThetaStart": "0deg", "ThetaStop": "180deg", "ThetaStep": "10deg"}


def test_open_surface_on_a_ground_plane_equals_the_closed_surface_with_the_image():
    ev = Evaluator({}, "mm")
    z0 = 0.01
    closed = compute_sphere(_huy((-0.05, -0.05, -0.05, 0.05, 0.05, 0.05), z0), PROPS, ev)
    huy = _huy((-0.05, -0.05, 0.0, 0.05, 0.05, 0.05), z0, skip=[(2, 0)])
    huy.update(gnd_kind=np.array(["E"]), gnd_axis=np.array([2]), gnd_pos=np.array([0.0]), gnd_dir=np.array([1]))
    open_ = compute_sphere(huy, PROPS, ev)
    th = closed["theta"]
    up = th < np.pi / 2 - 1e-9
    ref = np.linalg.norm(closed["E_0_1"], axis=0)
    got = np.linalg.norm(open_["E_0_1"], axis=0)
    assert ref[up].max() > 1.0                                 # a real far field, not zero by a wrong sign
    assert np.allclose(got[up], ref[up], rtol=2e-3, atol=2e-3 * ref.max())
    assert np.all(got[th > np.pi / 2 + 1e-9] == 0)             # nothing behind the ground plane
    assert open_["Prad_0_1"][0] == 1.0                          # the power into the real structure is kept
    # without the mirror the open surface misses the image: the pattern is wrong
    plain = compute_sphere(_huy((-0.05, -0.05, 0.0, 0.05, 0.05, 0.05), z0, skip=[(2, 0)]), PROPS, ev)
    assert not np.allclose(np.linalg.norm(plain["E_0_1"], axis=0)[up], ref[up], rtol=0.05)
