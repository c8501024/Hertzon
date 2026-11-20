"""Curved wave ports for EMerge's curved (isoparametric) elements.

EMerge keeps the faces of wave ports straight: the 2D port mode is solved on the flat triangles of the port face,
so a coax or circular port is a polygon (Coax50: Z0 50.5 Ohm on the port against 50.05 Ohm of the line). Here the
port face is curved like the walls behind it:

* The rim edges of the port face get the same curved midpoints as the tetrahedra on the port (one map
  x = y + sum_e d_e 4 l_i l_j for the face and the volume).
* The 2D mode problem is solved on the straight triangles with the pulled-back materials of the curved ones,
  eps_t K, mu_t K and eps_z det, mu_z det with D = dx/dy and K = det(D) D^-1 D^-T (det K = 1), averaged over each
  triangle. The solution is the pulled-back mode e^ (a covariant field, E = D^-T e^).
* Every port integral (power normalization, kappa, the dense port term, the excitation, the S-parameter
  projection) runs over the straight triangles with the metric K: the E-like fields (E_t, grad E_z, the mode
  profile and the 3D solution on the port) are evaluated as K^1/2 e^, the H-like ones as K^-1/2 h^. Then
  a.b = e^.K.f^ and (E x H).n = e^ x h^_true, the exact integrals over the curved face. Line integrals (voltage,
  current) use the pulled-back fields themselves: e^ and K^-1 h^.
* The Robin term of the port, gamma * integral N_i.K.N_j, takes K at every quadrature point like the excitation;
  with K averaged per triangle the two disagree and the incident wave is off by about 0.1 % (Coax50 S11 -54 dB
  instead of -62 dB after two passes).

Applied by emerge_patches.apply() unless HERTZON_EMERGE_NATIVE=curved_ports.
"""
from __future__ import annotations

import numpy as np
from emerge._emerge.elements.nedleg2 import FieldFunctionClass

# (pair of triangle corners) for the three edges of a triangle
_TPAIRS = np.array([[0, 1], [1, 2], [0, 2]], dtype=np.int64)
_TETPAIRS = np.array([[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]], dtype=np.int64)
_STATE: dict = {}
_FINDERS: dict = {}


# ------------------------------------------------------------------------------------------------
# Geometry of a curved port face
# ------------------------------------------------------------------------------------------------

def _edge_displacements(mesh, bcs) -> tuple[np.ndarray, np.ndarray] | None:
    """(sorted edge keys, displacements (n, 3)) of all curved mesh edges, as the volume assembly uses them."""
    from emerge._emerge.physics.microwave.assembly import curvilinear as C
    cd = C.curved_data_bcs(mesh, bcs)
    if cd is None:
        return None
    tc, d = cd
    T = np.asarray(mesh.tets, np.int64)[:, tc]
    n = int(np.asarray(mesh.nodes).shape[1])
    a = T[_TETPAIRS[:, 0]].T.ravel()
    b = T[_TETPAIRS[:, 1]].T.ravel()
    keys = np.minimum(a, b) * n + np.maximum(a, b)
    disp = np.asarray(d, float).reshape(-1, 3)
    keys, first = np.unique(keys, return_index=True)
    return keys, disp[first]


def port_geometry(mesh, bcs, tri_ids: np.ndarray, cs) -> dict | None:
    """Local corner coordinates P (M, 3, 2) and edge midpoint displacements d (M, 3, 2) (edges 01, 12, 02) of the
    port triangles, in the port's local coordinates; None when no edge of the face is curved."""
    ed = _edge_displacements(mesh, bcs)
    if ed is None:
        return None
    keys, disp = ed
    n = int(np.asarray(mesh.nodes).shape[1])
    tris = np.asarray(mesh.tris, np.int64)[:, tri_ids]
    a, b = tris[_TPAIRS[:, 0]], tris[_TPAIRS[:, 1]]               # (3, M)
    k = np.minimum(a, b) * n + np.maximum(a, b)
    pos = np.clip(np.searchsorted(keys, k), 0, len(keys) - 1)
    hit = keys[pos] == k
    if not hit.any():
        return None
    d3 = np.where(hit[..., None], disp[pos], 0.0)                  # (3, M, 3) global
    dl = np.stack(cs.in_local_basis(d3[..., 0], d3[..., 1], d3[..., 2]), -1)[..., :2]   # (3, M, 2)
    X = np.asarray(mesh.nodes, float)[:, tris]                     # (3 coords, 3 corners, M)
    xl = np.stack(cs.in_local_cs(X[0], X[1], X[2]), -1)[..., :2]   # (3 corners, M, 2)
    return {"P": np.ascontiguousarray(np.transpose(xl, (1, 0, 2))),
            "d": np.ascontiguousarray(np.transpose(dl, (1, 0, 2)))}


def _grad_lambda(P: np.ndarray) -> np.ndarray:
    """Gradients of the barycentric coordinates of straight triangles P (M, 3, 2): (M, 3, 2)."""
    e1 = P[:, 1] - P[:, 0]
    e2 = P[:, 2] - P[:, 0]
    det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    g1 = np.stack([e2[:, 1], -e2[:, 0]], -1) / det[:, None]
    g2 = np.stack([-e1[:, 1], e1[:, 0]], -1) / det[:, None]
    return np.stack([-g1 - g2, g1, g2], 1)


def jacobian(P: np.ndarray, d: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """D = dx/dy (N, 2, 2) of the curved map at barycentric points lam (N, 3) of the triangles P, d (N, 3, 2)."""
    g = _grad_lambda(P)
    D = np.broadcast_to(np.eye(2), (len(P), 2, 2)).copy()
    for e, (i, j) in enumerate(_TPAIRS):
        gphi = 4 * (lam[:, i, None] * g[:, j] + lam[:, j, None] * g[:, i])      # (N, 2)
        D += d[:, e, :, None] * gphi[:, None, :]
    return D


def metric(D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """K = det D^-1 D^-T (det K = 1) and det D for Jacobians D (N, 2, 2)."""
    det = D[:, 0, 0] * D[:, 1, 1] - D[:, 0, 1] * D[:, 1, 0]
    Di = np.stack([np.stack([D[:, 1, 1], -D[:, 0, 1]], -1), np.stack([-D[:, 1, 0], D[:, 0, 0]], -1)], 1) / det[:, None, None]
    K = det[:, None, None] * Di @ np.transpose(Di, (0, 2, 1))
    return K, det


def _sqrt_unimodular(K: np.ndarray, inverse: bool = False) -> np.ndarray:
    """K^1/2 (or K^-1/2) of symmetric positive 2x2 matrices with det 1: (K + I)/sqrt(tr K + 2)."""
    if inverse:
        K = np.stack([np.stack([K[:, 1, 1], -K[:, 0, 1]], -1), np.stack([-K[:, 1, 0], K[:, 0, 0]], -1)], 1)
    s = np.sqrt(K[:, 0, 0] + K[:, 1, 1] + 2.0)
    return (K + np.eye(2)) / s[:, None, None]


# ------------------------------------------------------------------------------------------------
# Effective materials of the 2D port problem
# ------------------------------------------------------------------------------------------------

def effective_materials(geo: dict, er: np.ndarray, ur: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pulled-back eps and mu (3, 3, M) for the port element of every triangle, in the local port frame.

    The element (emr generalized_eigen_hb) weighs E_t.E_t with eps_t and eps_z E_z^2, curl_z E_t^2 with 1/mu_z, and
    the covariant combinations (j beta E_t + grad E_z) with the 2x2 block W of 1/mu. Under the curved map
    (D3 = diag(D, 1)) these become det D^-1 eps_t D^-T, det eps_z, 1/(det mu_z) and det D^-1 W D^-T. They are
    averaged over the triangle; for a constant K the TEM mode keeps beta = k0 sqrt(eps mu).

    B: the port basis (columns are the local axes in global coordinates)."""
    from emerge._emerge.mth.optimized import gaus_quad_tri
    q = gaus_quad_tri(4)
    w, L = q[0], q[1:].T                                           # (nq,), (nq, 3)
    P, d = geo["P"], geo["d"]
    M = len(P)
    erl = np.einsum("ai,ijm,jb->mab", B.T, er, B)                  # local frame (M, 3, 3)
    uil = np.linalg.inv(np.einsum("ai,ijm,jb->mab", B.T, ur, B))
    W = uil[:, :2, :2]
    acc_e = np.zeros((M, 3, 3), complex)
    acc_ui = np.zeros((M, 3, 3), complex)
    for wq, lam in zip(w, L):
        D2 = jacobian(P, d, np.broadcast_to(lam, (M, 3)))
        det = D2[:, 0, 0] * D2[:, 1, 1] - D2[:, 0, 1] * D2[:, 1, 0]
        Di = np.linalg.inv(D2)
        D3i = np.zeros((M, 3, 3))
        D3i[:, :2, :2] = Di
        D3i[:, 2, 2] = 1.0
        acc_e += wq * det[:, None, None] * (D3i @ erl @ np.transpose(D3i, (0, 2, 1)))
        ui = np.zeros((M, 3, 3), complex)
        ui[:, :2, :2] = det[:, None, None] * (Di @ W @ np.transpose(Di, (0, 2, 1)))
        ui[:, 2, 2] = uil[:, 2, 2] / det
        acc_ui += wq * ui
    return np.transpose(acc_e, (1, 2, 0)).copy(), np.transpose(np.linalg.inv(acc_ui), (1, 2, 0)).copy()


# ------------------------------------------------------------------------------------------------
# Point-wise metric on the port face
# ------------------------------------------------------------------------------------------------

def _finder(ff):
    key = id(ff.tris), id(ff.nodes)
    f = _FINDERS.get(key)
    if f is None:
        from matplotlib.tri import Triangulation
        tri = Triangulation(np.asarray(ff.nodes[0], float), np.asarray(ff.nodes[1], float),
                            np.asarray(ff.tris, np.int64).T)
        f = (tri.get_trifinder(), (ff.tris, ff.nodes))
        if len(_FINDERS) > 64:
            _FINDERS.clear()
        _FINDERS[key] = f
    return f[0]


def local_metric(ff, xl: np.ndarray, yl: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """K (N, 2, 2) and det D (N,) at local points of the port face of the field function ff (identity outside)."""
    geo = ff._hz_curv
    xl = np.asarray(xl, float).ravel()
    yl = np.asarray(yl, float).ravel()
    # the port integrals sample the same points at every frequency of a sweep
    memo = geo.setdefault("_memo", {})
    key = (len(xl), hash(xl.tobytes()), hash(yl.tobytes()))
    hit = memo.get(key)
    if hit is not None:
        return hit
    if len(memo) > 32:
        memo.clear()
    memo[key] = out = _local_metric(ff, geo, xl, yl)
    return out


def _local_metric(ff, geo, xl, yl):
    t = np.asarray(_finder(ff)(xl, yl), np.int64)
    K = np.broadcast_to(np.eye(2), (len(xl), 2, 2)).copy()
    det = np.ones(len(xl))
    ok = np.flatnonzero(t >= 0)
    if ok.size:
        P = geo["P"][t[ok]]
        g = _grad_lambda(P)
        r = np.stack([xl[ok], yl[ok]], -1) - P[:, 0]
        l1 = np.einsum("ni,ni->n", g[:, 1], r)
        l2 = np.einsum("ni,ni->n", g[:, 2], r)
        lam = np.stack([1 - l1 - l2, l1, l2], -1)
        K[ok], det[ok] = metric(jacobian(P, geo["d"][t[ok]], lam))
    return K, det


def _apply(ff, xs, ys, zs, F: np.ndarray, which: str) -> np.ndarray:
    """F (3, N) global vectors at global points: transverse part times K^1/2 ('E'), K^-1/2 ('H'), K^-1 ('Hraw')."""
    xl, yl, _ = ff.cs.in_local_cs(xs, ys, zs)
    K, det = local_metric(ff, xl, yl)
    if which == "E":
        A = _sqrt_unimodular(K)
    elif which == "H":
        A = _sqrt_unimodular(K, inverse=True)
    else:
        A = np.stack([np.stack([K[:, 1, 1], -K[:, 0, 1]], -1), np.stack([-K[:, 1, 0], K[:, 0, 0]], -1)], 1)
    F = np.asarray(F)
    shp = F.shape
    F = F.reshape(3, -1)
    lx, ly, lz = ff.cs.in_local_basis(F[0], F[1], F[2])
    nx = A[:, 0, 0] * lx + A[:, 0, 1] * ly
    ny = A[:, 1, 0] * lx + A[:, 1, 1] * ly
    gx, gy, gz = ff.cs.in_global_basis(nx, ny, lz)
    return np.array([gx, gy, gz]).reshape(shp)


class CurvedFieldFunction(FieldFunctionClass):
    """Port mode field on a curved port face: E-like values K^1/2 e^, H-like values K^-1/2 h^ (see the module)."""

    _hz_raw = False

    def _t(self, xs, ys, zs, F, kind):
        if self._hz_raw:
            return F if kind == "E" else _apply(self, xs, ys, zs, F, "Hraw")
        return _apply(self, xs, ys, zs, F, kind)

    def __call__(self, xs, ys, zs):
        F = FieldFunctionClass.__call__(self, xs, ys, zs)
        return self._t(xs, ys, zs, F, "E" if self.eh == "E" else "H")

    def calcExy(self, xs, ys, zs):
        return self._t(xs, ys, zs, FieldFunctionClass.calcExy(self, xs, ys, zs), "E")

    def calcEzGrad(self, xs, ys, zs):
        return self._t(xs, ys, zs, FieldFunctionClass.calcEzGrad(self, xs, ys, zs), "E")

    def calcHxy(self, xs, ys, zs):
        return self._t(xs, ys, zs, FieldFunctionClass.calcHxy(self, xs, ys, zs), "H")

    def calcHz(self, xs, ys, zs):
        F = FieldFunctionClass.calcHz(self, xs, ys, zs)
        xl, yl, _ = self.cs.in_local_cs(xs, ys, zs)
        _, det = local_metric(self, xl, yl)
        return F / det

    def raw(self):
        """The pulled-back field e^ (h^ = K^-1 h of the code) for line integrals."""
        c = object.__new__(type(self))
        c.__dict__.update(self.__dict__)
        c._hz_raw = True
        return c


def raw_field(f):
    """The pulled-back field of a curved port mode function for a line integral, else f itself."""
    owner = getattr(f, "__self__", f)
    if not isinstance(owner, CurvedFieldFunction):
        return f
    raw = owner.raw()
    if f is owner:
        return raw
    return getattr(raw, f.__name__)


def _wrap_ff(ff, geo):
    ff.__class__ = CurvedFieldFunction
    ff._hz_curv = geo
    return ff


def khalf(port, k0, xs, ys, zs, F):
    """F (3, N) times K^1/2 on the curved port face of port (unchanged on a straight port)."""
    ff = _port_ff(port, k0)
    if ff is None:
        return F
    return _apply(ff, xs, ys, zs, F, "E")


def _port_ff(port, k0):
    if getattr(port, "_hz_curv", None) is None:
        return None
    try:
        modes = port.get_modes(k0)
    except Exception:  # noqa: BLE001
        return None
    for m in modes:
        if getattr(m.E_function, "_hz_curv", None) is not None:
            return m.E_function
    return None


# ------------------------------------------------------------------------------------------------
# Patches
# ------------------------------------------------------------------------------------------------

def _is_wave_port(bc) -> bool:
    """A port with a 2D mode solve on its face (ModalPort, WavePort)."""
    from emerge._emerge.physics.microwave.bcs.port_bcs import FloquetPort, LumpedPort, ModalPort
    return isinstance(bc, ModalPort) and not isinstance(bc, (LumpedPort, FloquetPort))


def _tri_key(tri_ids) -> bytes:
    return np.ascontiguousarray(np.asarray(tri_ids, np.int64)).tobytes()


def _basis_at(nlf, X: np.ndarray, nd: int) -> np.ndarray:
    """The transverse parts of the first nd local basis functions of every triangle of nlf at the local points X
    (M, nq, 2) inside that triangle: (M, nd, nq, 2). Triangles of one colour share no edge, so one global unit vector
    per colour and local slot gives each triangle exactly its own basis function."""
    from emerge._emerge.compiled import MATHLIB
    t2e = np.asarray(nlf.mesh.tri_to_edge, np.int64)
    M, nq = X.shape[0], X.shape[1]
    colour = np.full(M, -1)
    owner = {}
    for e, t in zip(t2e.ravel(), np.tile(np.arange(M), 3)):
        owner.setdefault(int(e), []).append(int(t))
    for t in range(M):
        used = {colour[o] for e in t2e[:, t] for o in owner[int(e)] if o != t}
        colour[t] = min(c for c in range(5) if c not in used)
    t2f = np.asarray(nlf.tri_to_field, np.int64)
    out = np.zeros((M, nd, nq, 2), complex)
    for c in range(colour.max() + 1):
        tc = np.flatnonzero(colour == c)
        pts = X[tc].reshape(-1, 2).T.copy()
        for s in range(nd):
            f = np.zeros(nlf.n_field, complex)
            f[t2f[s, tc]] = 1.0
            v = MATHLIB.ned2_tri_interp_full(pts, f, np.asarray(nlf.mesh.tris, np.int64),
                                             np.asarray(nlf.local_nodes, float), t2f, np.asarray(nlf.dofcodes, np.int64))
            out[tc, s] = np.stack([v[0], v[1]], -1).reshape(len(tc), nq, 2)
    return out


def robin_values(field, tri_ids, gamma, other_side, nlf, geo):
    """The Robin term gamma * integral (n x N_i).K.(n x N_j) dS of a curved port face, with the metric K of the curved
    triangle evaluated at every quadrature point (the same K the excitation and the S-parameters use)."""
    from emerge._emerge.mth.optimized import gaus_quad_tri
    nd = field.n_tri_dofs
    if not np.array_equal(np.asarray(nlf.dofcodes), np.asarray(field.dofcodes2d)) or len(nlf.dofcodes) != nd:
        return None
    q = gaus_quad_tri(6)                                                  # exact for the quartic N.N
    w, lam = np.asarray(q[0], float), np.asarray(q[1:4], float).T          # (nq,), (nq, 3)
    P, d = geo["P"], geo["d"]
    M = len(P)
    X = np.einsum("qk,mki->mqi", lam, P)
    K = np.stack([metric(jacobian(P, d, np.broadcast_to(l, (M, 3))))[0] for l in lam], 1)   # (M, nq, 2, 2)
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    area = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    N = _basis_at(nlf, X, nd)
    blk = np.einsum("q,miqa,mqab,mjqb->mij", w, N, K, N) * area[:, None, None]
    blk = blk * np.asarray(gamma, complex).reshape(-1)[:, None, None]
    rows, cols = field.tri_rowcol(tri_ids, other_side=other_side)
    return np.ascontiguousarray(blk.reshape(-1)), rows, cols


def apply() -> None:
    from emerge._emerge.coord import Line
    from emerge._emerge.elements.nedleg2 import NedelecLegrange2
    from emerge._emerge.physics.microwave import microwave_3d as MW
    from emerge._emerge.physics.microwave.assembly import assembler as AS
    from emerge._emerge.physics.microwave.assembly import curvilinear as C
    from emerge._emerge.physics.microwave.assembly import emr_microwave as EM
    from emerge._emerge.physics.microwave.assembly import system_pattern as SP
    from emerge._emerge.physics.microwave.bcs.port_bcs import ModalPort, WavePort
    if getattr(C.exclude_tags, "_hertzon_ports", False):
        return

    # 1. the faces of wave ports may be curved (their surface integrals carry the metric now)
    plain_exclude = C.exclude_tags

    def exclude_tags(bcs) -> frozenset:
        ports = {int(t) for bc in bcs if _is_wave_port(bc) for t in (getattr(bc, "tags", None) or [])}
        return frozenset(t for t in plain_exclude(bcs) if t not in ports)
    exclude_tags._hertzon_ports = True
    C.exclude_tags = exclude_tags

    # 2. conductor tets known before the port solve, so that the port and the volume see the same curved edges
    plain_modal = MW.Microwave3D.modal_analysis

    def modal_analysis(self, port, *a, **k):
        try:
            _STATE["sim"] = self
            from .emerge_patches import prepare_conductor_tets
            prepare_conductor_tets(self)
        except Exception as ex:  # noqa: BLE001
            from loguru import logger
            logger.debug(f"Curved ports: conductor tets not prepared ({ex})")
        return plain_modal(self, port, *a, **k)
    MW.Microwave3D.modal_analysis = modal_analysis

    # 3. pulled-back materials for the 2D mode problem of a curved port face
    plain_bma = AS.Assembler.assemble_bma_matrices

    def assemble_bma_matrices(self, field, er, ur, sig, k0, port, bc_set, dofset):
        geo = None
        if getattr(self.settings, "mw_curved_elements", False) and _is_wave_port(port):
            try:
                tri_ids = field.mesh.get_triangles(port.tags)
                geo = port_geometry(field.mesh, bc_set.boundary_conditions, tri_ids, port.cs)
            except Exception as ex:  # noqa: BLE001
                from loguru import logger
                logger.warning(f"Curved ports: port {port} stays straight ({ex})")
                geo = None
        _STATE["geo"] = geo
        _STATE["basis"] = np.column_stack([port.cs.xax.np, port.cs.yax.np, port.cs.zax.np]) if geo else None
        try:
            E, B, ids, nlf = plain_bma(self, field, er, ur, sig, k0, port, bc_set, dofset)
        finally:
            _STATE["geo"] = None
        nlf._hz_curv = geo
        port._hz_curv = geo
        key = _tri_key(field.mesh.get_triangles(port.tags))
        if geo is not None:
            _STATE.setdefault("ports", {})[key] = (nlf, geo)
        else:
            _STATE.setdefault("ports", {}).pop(key, None)
        return E, B, ids, nlf
    AS.Assembler.assemble_bma_matrices = assemble_bma_matrices

    plain_gem = EM.generelized_eigenvalue_matrix

    def generelized_eigenvalue_matrix(field, er, ur, inward_normal, k0):
        geo = _STATE.get("geo")
        if geo is not None and len(geo["P"]) == er.shape[2]:
            er, ur = effective_materials(geo, er, ur, _STATE["basis"])
        return plain_gem(field, er, ur, inward_normal, k0)
    EM.generelized_eigenvalue_matrix = generelized_eigenvalue_matrix

    # 4. mode field functions of a curved port
    plain_ef, plain_hf = NedelecLegrange2.interpolate_Ef, NedelecLegrange2.interpolate_Hf

    def interpolate_Ef(self, *a, **k):
        ff = plain_ef(self, *a, **k)
        geo = getattr(self, "_hz_curv", None)
        return _wrap_ff(ff, geo) if geo is not None else ff

    def interpolate_Hf(self, *a, **k):
        ff = plain_hf(self, *a, **k)
        geo = getattr(self, "_hz_curv", None)
        return _wrap_ff(ff, geo) if geo is not None else ff
    NedelecLegrange2.interpolate_Ef = interpolate_Ef
    NedelecLegrange2.interpolate_Hf = interpolate_Hf

    # 5. line integrals use the pulled-back fields
    plain_line = Line.line_integral

    def line_integral(self, evalfunc):
        return plain_line(self, raw_field(evalfunc))
    Line.line_integral = line_integral

    # 6. the dense port term, its excitation and the S-parameter projection with the metric
    plain_all = WavePort.get_modepf_kappa_all

    def get_modepf_kappa_all(self, k0, nodes, tris):
        out = plain_all(self, k0, nodes, tris)
        if getattr(self, "_hz_curv", None) is None:
            return out
        res = []
        for number, f, kappa in out:
            def fk(x, y, z, f=f):
                return khalf(self, k0, x, y, z, f(x, y, z))
            res.append((number, fk, kappa))
        return res
    WavePort.get_modepf_kappa_all = get_modepf_kappa_all

    for cls in (ModalPort, WavePort):
        def _uinc(plain):
            def get_Uinc(self, x, y, z, k0, mode_nr=1):
                return khalf(self, k0, x, y, z, plain(self, x, y, z, k0, mode_nr))
            return get_Uinc
        cls.get_Uinc = _uinc(cls.get_Uinc)

    plain_s = MW.Microwave3D._compute_s_data

    def _compute_s_data(self, bc, mode_nr, active, fieldfunction, tri_vertices, k0, erp, urp):
        if getattr(bc, "_hz_curv", None) is not None and not bc.v_integration:
            f0 = fieldfunction

            def fieldfunction(x, y, z):
                return khalf(bc, k0, x, y, z, f0(x, y, z))
        return plain_s(self, bc, mode_nr, active, fieldfunction, tri_vertices, k0, erp, urp)
    MW.Microwave3D._compute_s_data = _compute_s_data

    # 7. the Robin term of a ModalPort with the metric
    plain_robin = SP.assemble_robin_bc

    def assemble_robin_bc(field, tri_ids, gamma, other_side=False):
        hit = _STATE.get("ports", {}).get(_tri_key(tri_ids))
        if hit is not None:
            try:
                r = robin_values(field, tri_ids, gamma, other_side, *hit)
                if r is not None:
                    return r
            except Exception as ex:  # noqa: BLE001
                from loguru import logger
                logger.warning(f"Curved ports: Robin term on the straight face ({ex})")
        return plain_robin(field, tri_ids, gamma, other_side)
    SP.assemble_robin_bc = assemble_robin_bc
