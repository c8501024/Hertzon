"""Curved (isoparametric) tetrahedra at curved CAD surfaces.

The solve uses EMerge's curved elements (FennisRobert/EMerge#77, ``Settings.mw_curved_elements``): edge
midpoints on curved CAD faces are moved onto the true geometry, and the element matrices of those
tetrahedra use the covariant Piola map. Hertzon switches them on with the setup option "CurvedElements"
and uses the same curved geometry here to draw the plots and map the fields of the curved tetrahedra:

    x(y) = y + sum_e d_e phi_e(y),   phi_e = 4 l_i l_j,   D = dx/dy,   E = D^-T E_s,   B = D B_s / det D
"""
from __future__ import annotations

import os

import numpy as np
from emerge._emerge.physics.microwave.assembly.curvilinear import (  # noqa: F401
    _PAIRS, _grads, curved_data, curved_data_bcs, curved_volume, exclude_tags)

CTX: dict = {"enabled": True}


def enabled() -> bool:
    env = os.environ.get("HERTZON_CURVED")          # override for experiments
    return env != "0" if env is not None else bool(CTX.get("enabled", True))


def curved_map(X, d, lam):
    """Positions (nt, np, 3) and Jacobians D (nt, np, 3, 3) of the curved map at barycentric points
    lam (np, 4), for straight tets X (nt, 4, 3) with edge displacements d (nt, 6, 3) in _PAIRS order.
    Fields of the straight element map with the covariant Piola transform: E = D^-T E_s, B = D B_s / det D."""
    g, _ = _grads(X)
    pos = np.einsum("pk,tkd->tpd", lam, X)
    D = np.broadcast_to(np.eye(3), (len(X), len(lam), 3, 3)).copy()
    for e, (i, j) in enumerate(_PAIRS):
        pos += 4 * (lam[:, i] * lam[:, j])[None, :, None] * d[:, None, e, :]
        gp = 4 * (lam[None, :, i, None] * g[:, None, j] + lam[None, :, j, None] * g[:, None, i])   # (nt, np, 3)
        D += d[:, None, e, :, None] * gp[:, :, None, :]
    return pos, D
