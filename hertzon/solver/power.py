"""Power balance of an antenna solution: the power dissipated in lossy materials, finite conductors and lumped
resistors (Ploss), next to the radiated power (Prad, the far field surface) and the accepted power (from the
S-parameters).

For a converged solution Prad + Ploss = Pacc. The radiation efficiency is taken as Prad / (Prad + Ploss): it needs
no subtraction 1 - |S11|^2, which loses its accuracy where the antenna is badly matched (|S11| close to 1), and
a lossless antenna gets exactly 1. The balance error |Prad + Ploss - Pacc| / Pin measures how well the solution
is converged (#231); it is relative to the incident power, so a small S11 error at a poorly matched frequency
does not blow it up.

Ploss is a Hermitian form in the port amplitudes a: Ploss = a^H L a with L[m, n] = the cross dissipation of the
fields of ports m and n. L is stored per frequency, so any excitation of Edit Sources is evaluated exactly.
Time convention e^{+j w t}: a lossy permittivity has a negative imaginary part.
"""
from __future__ import annotations

import numpy as np

EPS0 = 8.8541878128e-12
MU0 = 4e-7 * np.pi
Z0 = 376.730313668
C0 = 299792458.0

# 4-point Gauss rule on a tetrahedron (barycentric coordinates, weights 1/4 each): exact for quadratics
_A, _B = 0.5854101966249685, 0.1381966011250105
_TET = np.array([[_A, _B, _B, _B], [_B, _A, _B, _B], [_B, _B, _A, _B], [_B, _B, _B, _A]])
# 6-point Gauss rule on a triangle (barycentric, weights): exact for degree 4
_TA, _TB, _TC, _TD = 0.445948490915965, 0.108103018168070, 0.091576213509771, 0.816847572980459
_TRI = np.array([[_TA, _TA, _TB], [_TA, _TB, _TA], [_TB, _TA, _TA],
                 [_TC, _TC, _TD], [_TC, _TD, _TC], [_TD, _TC, _TC]])
_TRI_W = np.array([0.223381589678011] * 3 + [0.109951743655322] * 3)

# EMerge boundary conditions that dissipate power on a surface (absorbing boundaries and ports are not losses)
LOSSY_BCS = ("SurfaceImpedance", "ThinConductor", "LumpedElement")


class LossModel:
    """Quadrature points of the lossy parts of a mesh, found once per mesh and used for every frequency and
    port: the tetrahedra with conductivity or a lossy permittivity / permeability, and the faces of finite
    conductivity, impedance and lumped RLC boundaries."""

    def __init__(self, m, fld):
        nodes = np.asarray(m.mesh.nodes, float)
        tets = np.asarray(m.mesh.tets, np.int64)
        self.vol = None
        lossy = _lossy_tets(fld)
        if lossy.any():
            ids = np.nonzero(lossy)[0]
            P = nodes[:, tets[:, ids]]                                  # (3, 4, T)
            a, b, c = (P[:, i] - P[:, 0] for i in (1, 2, 3))
            vol = np.abs(np.einsum("it,it->t", a, np.cross(b, c, axis=0))) / 6
            pts = np.einsum("qk,ikt->iqt", _TET, P).reshape(3, -1)      # (3, 4*T), point q of tet t at q*T+t
            self.vol = {"ids": ids, "pts": pts, "w": np.tile(vol / 4, 4), "map": np.tile(ids, 4)}
        self.surf = []
        for bc in getattr(getattr(m.mw, "bc", None), "boundary_conditions", []) or []:
            if type(bc).__name__ not in LOSSY_BCS:
                continue
            try:
                tri_ids = np.asarray(m.mesh.get_triangles(list(bc.tags)), np.int64)
            except Exception:  # noqa: BLE001
                continue
            if not len(tri_ids):
                continue
            T = nodes[:, np.asarray(m.mesh.tris, np.int64)[:, tri_ids]]   # (3, 3, F)
            n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0], axis=0)
            area = np.linalg.norm(n, axis=0) / 2
            n = n / np.maximum(2 * area, 1e-300)
            pts = np.einsum("qk,ikf->iqf", _TRI, T).reshape(3, -1)
            w = np.outer(_TRI_W, area).ravel()
            nrm = np.repeat(n[:, None, :], len(_TRI), axis=1).reshape(3, -1)
            # the containing tetrahedron of a point on a face: either side, the tangential E is continuous
            self.surf.append({"bc": bc, "pts": pts, "w": w, "n": nrm})

    @property
    def empty(self) -> bool:
        return self.vol is None and not self.surf


def _lossy_tets(fld) -> np.ndarray:
    def lossy_im(a):
        a = np.asarray(a)
        if a.ndim == 3:                       # tensor (3, 3, T)
            return np.abs(a.imag).max(axis=(0, 1)) > 0
        return np.abs(np.imag(a)) > 0
    n = np.asarray(fld._der).shape[-1]
    out = np.zeros(n, bool)
    out |= lossy_im(fld._der)
    out |= lossy_im(fld._dur)
    sig = getattr(fld, "_dsig", None)
    if sig is not None:
        out |= np.abs(np.asarray(sig)) > 0
    return out


def _fields_at(fld, pts, mapping=None):
    """E, H (3, N) at points; mapping: their tetrahedra when known."""
    if mapping is None:
        mapping = np.asarray(fld.basis.interpolate_index(*pts), np.int64)
    fld.interpolate(pts[0], pts[1], pts[2], usenan=False, mapping=mapping)
    return np.array([fld.Ex, fld.Ey, fld.Ez]), np.array([fld.Hx, fld.Hy, fld.Hz]), mapping


def loss_matrix(model: LossModel, fld, excitations: list, excite) -> np.ndarray:
    """L[m, n] (W for unit port amplitudes) of the excitations (ports) of one frequency: Ploss = a^H L a.
    excite(fld, x) sets the field of excitation x."""
    n = len(excitations)
    L = np.zeros((n, n), complex)
    if model.empty or not n:
        return L
    w = 2 * np.pi * float(fld.freq)
    k0 = w / C0
    vol_e, vol_h, surf_e = [], [], [[] for _ in model.surf]
    vmap = model.vol["map"] if model.vol is not None else None
    smaps = [None] * len(model.surf)
    for x in excitations:
        excite(fld, x)
        if model.vol is not None:
            E, H, _ = _fields_at(fld, model.vol["pts"], vmap)
            vol_e.append(E)
            vol_h.append(H)
        for i, s in enumerate(model.surf):
            E, _, smaps[i] = _fields_at(fld, s["pts"], smaps[i])
            nn = s["n"]
            surf_e[i].append(E - nn * np.einsum("in,in->n", nn, E))           # tangential E
    if model.vol is not None:
        ids, wt, q = model.vol["ids"], model.vol["w"], len(_TET)
        er = np.asarray(fld._der)
        ur = np.asarray(fld._dur)
        sig = np.asarray(getattr(fld, "_dsig", np.zeros(er.shape[-1])))
        # conduction + dielectric: J = (sigma + j w eps0 eps_r) E, p = 1/2 Re(E* . J) -> the anti-Hermitian part
        def tens(a):
            a = a[..., ids]
            if a.ndim == 1:
                a = np.eye(3)[:, :, None] * a[None, None, :]
            return np.concatenate([a] * q, axis=2)                        # (3, 3, q*T) in the point order
        Ye = tens(sig.astype(complex)) + 1j * w * EPS0 * tens(er.astype(complex))
        Zh = 1j * w * MU0 * tens(ur.astype(complex))
        for a in range(n):
            for b in range(n):
                je = np.einsum("ijn,jn->in", Ye, vol_e[b])
                jh = np.einsum("ijn,jn->in", Zh, vol_h[b])
                # cross term of 1/2 Re(E*.J): the Hermitian part of the bilinear form
                L[a, b] += 0.25 * np.sum(wt * (np.einsum("in,in->n", np.conj(vol_e[a]), je)
                                               + np.conj(np.einsum("in,in->n", np.conj(vol_e[b]),
                                                                   np.einsum("ijn,jn->in", Ye, vol_e[a])))))
                L[a, b] += 0.25 * np.sum(wt * (np.einsum("in,in->n", np.conj(vol_h[a]), jh)
                                               + np.conj(np.einsum("in,in->n", np.conj(vol_h[b]),
                                                                   np.einsum("ijn,jn->in", Zh, vol_h[a])))))
    for i, s in enumerate(model.surf):
        bc = s["bc"]
        try:
            zs = 1j * k0 * Z0 / complex(bc.get_gamma(k0))            # sheet / surface impedance (ohm)
        except Exception:  # noqa: BLE001
            continue
        if not np.isfinite(zs) or zs == 0:
            continue
        y = 1 / zs
        g = float(np.real(y))                                      # J_s = E_t / Z_s, p = 1/2 Re(1/Z_s) |E_t|^2
        for a in range(n):
            for b in range(n):
                L[a, b] += 0.5 * g * np.sum(s["w"] * np.einsum("in,in->n", np.conj(surf_e[i][a]), surf_e[i][b]))
    return 0.5 * (L + L.conj().T)


def balance(prad: float, ploss: float, pacc: float, pin: float = 1.0) -> float:
    """Power balance error (Prad + Ploss - Pacc) / Pin."""
    return (prad + ploss - pacc) / max(pin, 1e-300)


def efficiency(prad: float, ploss: float) -> float:
    return prad / max(prad + ploss, 1e-300) if prad > 0 else 1.0
