"""Derivative tuning: S-parameters of nearby designs from one solve, for live sliders.

For the selected design variables p the solver mesh is morphed (hertzon.solver.morph: the connectivity
stays, the nodes move with the variables), so the FEM matrix A(p) changes smoothly with p. At every
frequency of the sweep:

1. A is assembled on the nominal mesh and on slightly morphed meshes; central differences give its first
   and second derivatives with respect to every variable (and the mixed second derivatives), so that
   A(p0 + dp) ~ A0 + sum dp_i A_i + 1/2 sum dp_i dp_j A_ij;
2. the nominal system is factorized once, and the same factorization gives the solution x0 and its first
   and second derivatives (A0 x_i = -A_i x0, A0 x_ij = -(A_ij x0 + A_i x_j + A_j x_i));
3. these vectors span a small basis W; the matrix terms are projected onto it (W^T A W) and the
   S-parameter functionals of the ports are evaluated on its columns.

With several frequencies one basis serves the whole band (BandModel): the volume matrices of every mesh
state do not depend on the frequency and are assembled once, only the boundary terms are assembled per
frequency, and the basis holds the solutions and derivatives of a few expansion frequencies, chosen where
the residual of the reduced solution is largest until it is below 1e-4 at every frequency. So only a few
frequencies are factorized; every other frequency is still the exact FEM system, solved in the basis.

A slider position dp then costs one small dense solve per frequency: (W^T A(p0+dp) W) y = W^T b and
S = L W y. The approximation is applied to the matrix, not to the S-parameters, so resonances move with
the variables instead of being smeared by a Taylor series of S. At dp = 0 the result is the nominal
solution exactly.

The model is valid while the morphed mesh stays reasonable: the range of every variable is limited to the
offsets at which no tetrahedron shrinks below 30 % of its volume (morph.valid_range), and the further a
slider is from the solved value, the larger the error. A full solve at the new value is the reference.

Saved as tuning_<sweep>.npz in the result folder of the setup; tuning_model() reads it and evaluate()
gives the S-parameters for slider positions (no solver needed, used by the GUI).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import morph as MO


# ------------------------------------------------------------------------------------------------
# solver side
# ------------------------------------------------------------------------------------------------
def set_nodes(mesh, X: np.ndarray) -> None:
    """Move the mesh nodes to X (3, N) and update the quantities EMerge derives from them."""
    mesh.nodes[...] = X
    n, t, tr, e = mesh.nodes, mesh.tets, mesh.tris, mesh.edges
    mesh.centers = (n[:, t[0]] + n[:, t[1]] + n[:, t[2]] + n[:, t[3]]) / 4
    mesh.tri_centers = (n[:, tr[0]] + n[:, tr[1]] + n[:, tr[2]]) / 3
    mesh.edge_centers = (n[:, e[0]] + n[:, e[1]]) / 2
    mesh.edge_lengths = np.linalg.norm(n[:, e[0]] - n[:, e[1]], axis=0)
    mesh.areas = np.linalg.norm(np.cross((n[:, tr[1]] - n[:, tr[0]]).T, (n[:, tr[2]] - n[:, tr[0]]).T), axis=1) / 2


def _lumped_classes():
    from emerge._emerge.physics.microwave.bcs.port_bcs import LumpedPort
    from emerge._emerge.physics.microwave.bcs.boundary_conditions import LumpedElement
    return LumpedPort, LumpedElement


class LumpedGeometry:
    """Width, height and voltage integration lines of the lumped ports and lumped RLC elements follow the
    morphed mesh: their sheets stretch with the variables (a port across a substrate whose height is
    tuned), and the sheet impedance and the excitation depend on that size."""

    def __init__(self, mw, X0: np.ndarray):
        LumpedPort, LumpedElement = _lumped_classes()
        self.mw = mw
        self.items = []
        for bc in mw.bc.boundary_conditions:
            if not isinstance(bc, (LumpedPort, LumpedElement)):
                continue
            nid = np.unique(np.asarray(mw.mesh.get_nodes(bc.tags), int))
            if len(nid) < 3:
                continue
            P = X0[:, nid].T
            _, _, vt = np.linalg.svd(P - P.mean(axis=0), full_matrices=False)
            if isinstance(bc, LumpedPort):
                d = np.asarray(bc.Vdirection.np, float)
            else:                           # the in-plane axis whose extent is the height
                d = min(vt[:2], key=lambda a: abs(np.ptp(P @ a) - bc.height))
            d = d / np.linalg.norm(d)
            w = np.cross(vt[2], d)
            w /= max(np.linalg.norm(w), 1e-300)
            self.items.append({"bc": bc, "nid": nid, "d": d, "w": w, "w0": bc.width, "h0": bc.height,
                               "ed": np.ptp(P @ d), "ew": np.ptp(P @ w), "port": isinstance(bc, LumpedPort)})

    def apply(self, X: np.ndarray) -> None:
        from emerge._emerge.coord import Line
        for it in self.items:
            bc, P = it["bc"], X[:, it["nid"]].T
            bc.height = it["h0"] * np.ptp(P @ it["d"]) / it["ed"]
            bc.width = it["w0"] * np.ptp(P @ it["w"]) / it["ew"] if it["ew"] > 0 else it["w0"]
            if it["port"]:
                # integration lines from the nodes on the low side of the sheet along the field direction,
                # as EMerge defines them (with a tolerance, so the morphed nodes keep their lines)
                t = P @ it["d"]
                low = P[t <= t.min() + 1e-6 * bc.height]
                bc.voltage_integration_line = [Line.from_points(a, a + it["d"] * bc.height, 21) for a in low]


class Assembly:
    """FEM systems of the EMerge model m at given node positions (the nominal mesh is restored after).

    Every node position (state) keeps its own sparsity pattern object with the frequency independent volume
    matrices and boundary matrices cached on it, as long as memory allows, so that the next frequency of a
    state only adds the frequency dependent terms."""

    def __init__(self, m, keep_states: bool | None = None):
        self.m = m
        self.mw = m.mw
        self.mesh = m.mesh
        self.X0 = np.array(m.mesh.nodes, copy=True)
        self._at = None                 # key of the node positions in the mesh (None: nominal)
        self._systems: dict = {}        # state key -> EMerge SystemPattern of that state
        self.keep_states = keep_states
        self.lumped = LumpedGeometry(m.mw, self.X0)

    _ANON = ("anonymous",)

    def _move(self, X, key) -> None:
        if key == self._at and key != self._ANON:
            return
        asy = self.mw.assembler
        if self.keep_states and asy._system is not None and self._at != self._ANON:
            self._systems[self._at] = asy._system
        set_nodes(self.mesh, self.X0 if X is None else X)
        self.lumped.apply(self.mesh.nodes)
        asy.reset_cache()
        if self.keep_states and key != self._ANON:
            asy._system = self._systems.pop(key, None)
        self._at = key

    def system(self, freq: float, X=None, key=None):
        """(A, b, solve_ids, mats) at frequency freq with the nodes at X (None: nominal); key names the node
        positions (a state), so that its cached matrices are reused at the next frequency. Without a key the
        system at X is assembled anew."""
        if X is not None and key is None:
            key = self._ANON
        self._move(X, key)
        mw = self.mw
        job, mats = mw.assembler.assemble_freq_matrix(mw.basis, mw.mat_assy, mw.bc.boundary_conditions, freq,
                                                      cache_matrices=True)
        if self.keep_states is None:
            self.keep_states = _memory_allows(job.A.nnz)
        # one column per port mode, in the order of the S-matrix (iter_port_modes)
        order = [job.b_ind_map[si] for _, si, _ in mw.bc.iter_port_modes()]
        return job.A, job.b_vectors[:, order], np.asarray(job.solve_ids), mats

    def restore(self) -> None:
        self._move(None, None)

    def close(self) -> None:
        self.restore()
        self._systems.clear()


def _memory_allows(nnz: int, states: int = 8) -> bool:
    """Room for the cached matrices of all states (two complex arrays of nnz entries each)?"""
    need = states * 2 * 16 * nnz
    try:
        import psutil
        return need < 0.3 * psutil.virtual_memory().available
    except Exception:  # noqa: BLE001
        return need < 2 * 2 ** 30


def s_functionals(mw, freq: float, W: np.ndarray, NF: int, ids: np.ndarray, mats, powers: dict):
    """S-parameter functionals of the ports on the basis W (solve dofs x k): S_ij = c_ij + L_ij . y_j for
    a solution x_j = W y_j of excitation j. Returns (Lp, La, c0): Lp[i] (passive port i), La[i] (port i
    excited, linear part) and c0[i] (port i excited, constant part), exactly as EMerge's post-processing."""
    k0 = 2 * np.pi * freq / 299792458
    mesh, basis = mw.mesh, mw.basis
    er, ur, cond = mats
    ertri, urtri, _ = mw._materials_to_tris(er, ur, cond)
    pm = list(mw.bc.iter_port_modes())
    n, k = len(pm), W.shape[1]
    Lp = np.zeros((n, k), complex)
    La = np.zeros((n, k), complex)
    c0 = np.zeros(n, complex)
    full = np.zeros(NF, complex)
    for i, (port, si, mi) in enumerate(pm):
        tets = mesh.get_face_tets(port.tags)
        tris = mesh.get_triangles(port.tags)
        tv = mesh.tris[:, tris]
        er_i, ur_i = ertri[:, :, tris], urtri[:, :, tris]
        pw = abs(powers[si])

        def value(x, active):
            ff = basis.interpolate_Ef(x, tetids=tets)
            a, e = mw._compute_s_data(port, mi, active, ff, tv, k0, er_i, ur_i)
            return a / e * pw

        full[:] = 0
        c0[i] = value(full, True)
        for j in range(k):
            full[:] = 0
            full[ids] = W[:, j]
            Lp[i, j] = value(full, False)
            La[i, j] = value(full, True) - c0[i]
    return Lp, La, c0


def _orth(cols: list[np.ndarray], tol: float = 1e-10) -> np.ndarray:
    """Orthonormal basis (Gram-Schmidt twice) of the columns, dropping dependent ones."""
    V = []
    for v in cols:
        v = np.array(v, dtype=np.complex128)
        n0 = np.linalg.norm(v)
        if n0 == 0:
            continue
        for _ in range(2):
            for u in V:
                v -= u * (u.conj() @ v)
        n = np.linalg.norm(v)
        if n > tol * n0:
            V.append(v / n)
    return np.column_stack(V)


def derivative_solutions(A0, b, Ai: list, Aii: list, Aij: dict, factor, bi: list, bii: list,
                         bij: dict) -> np.ndarray:
    """The solution of A0 x = b and its first and second derivatives with respect to the variables (one
    factorization of A0), as columns (solve dofs x (1 + 2n + n(n-1)/2) * ports)."""
    n = len(Ai)
    fac = factor(A0)
    try:
        x0 = fac.solve(b)
        xi = [fac.solve(bi[i] - Ai[i] @ x0) for i in range(n)]
        xii = [fac.solve(bii[i] - (Aii[i] @ x0 + 2 * (Ai[i] @ xi[i]))) for i in range(n)]
        xij = {(i, j): fac.solve(bij[(i, j)] - (Aij[(i, j)] @ x0 + Ai[i] @ xi[j] + Ai[j] @ xi[i]))
               for (i, j) in Aij}
    finally:
        if hasattr(fac, "close"):
            fac.close()
    return np.hstack([x0] + xi + xii + [xij[k] for k in sorted(xij)])


def _extend(W: np.ndarray | None, C: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """W (orthonormal columns) extended by the part of the columns C outside its span (block Gram-Schmidt
    twice, then an SVD that drops dependent directions)."""
    C = np.array(C, dtype=np.complex128)
    scale = np.linalg.norm(C, axis=0)
    C = C[:, scale > 0] / scale[scale > 0]
    if C.shape[1] == 0:
        return W
    if W is not None and W.shape[1]:
        for _ in range(2):
            C -= W @ (W.conj().T @ C)
    U, sv, _ = np.linalg.svd(C, full_matrices=False)
    U = U[:, sv > tol]
    return U if W is None or not W.shape[1] else np.hstack([W, U])


def reduce_system(A0, b, Ai: list, Aii: list, Aij: dict, factor, bi: list | None = None,
                  bii: list | None = None, bij: dict | None = None) -> tuple[dict, np.ndarray]:
    """Reduced model of A(dp) x = b(dp) with A(dp) = A0 + sum dp_i Ai + 1/2 sum dp_i^2 Aii + sum_{i<j} dp_i
    dp_j Aij (and b(dp) alike, when the excitation moves with the variables: bi, bii, bij): the basis W of
    the solution and its first and second derivatives at dp = 0 (one factorization of A0,
    factor(A0).solve), and the terms projected onto it. Returns (terms, W)."""
    n = len(Ai)
    zero = np.zeros_like(b)
    bi = bi if bi is not None else [zero] * n
    bii = bii if bii is not None else [zero] * n
    bij = bij if bij is not None else {key: zero for key in Aij}
    cols = derivative_solutions(A0, b, Ai, Aii, Aij, factor, bi, bii, bij)
    W = _orth([cols[:, e] for e in range(cols.shape[1])])
    proj = lambda M: W.T @ (M @ W)      # noqa: E731  (complex symmetric: W^T keeps the reduced matrix symmetric)
    k = W.shape[1]
    out = {
        "A0": proj(A0), "b": W.T @ b,
        "Ai": np.array([proj(M) for M in Ai]).reshape(n, k, k),
        "Aii": np.array([proj(M) for M in Aii]).reshape(n, k, k),
        "Aij": np.array([proj(Aij[key]) for key in sorted(Aij)]).reshape(len(Aij), k, k),
    }
    if any(np.any(v) for v in list(bi) + list(bii) + list(bij.values())):
        m = b.shape[1]
        out["b_i"] = np.array([W.T @ v for v in bi]).reshape(n, k, m)
        out["b_ii"] = np.array([W.T @ v for v in bii]).reshape(n, k, m)
        out["b_ij"] = np.array([W.T @ bij[key] for key in sorted(Aij)]).reshape(len(Aij), k, m)
    return out, W


FUNCTIONAL_STEP = 50            # finite difference step of the port functionals, in matrix steps


def frequency_model(asm: Assembly, freq: float, V: list[np.ndarray], steps: list[float], log=None,
                    ports_move: bool = False) -> dict:
    """Reduced model of one frequency (see the module docstring). V: velocity fields (3, N) per variable,
    steps: finite difference step of every variable (in its unit); ports_move: lumped ports stretch with
    the variables, so their S functionals change too."""
    from .fast_sweep import _Factor
    from emerge._emerge.mth.csc_submatrix import csc_submatrix as submatrix
    mw = asm.mw
    n = len(V)
    mw._compute_modes(freq)
    keep = list(mw.frequencies) if hasattr(mw, "frequencies") else None
    try:
        mw.frequencies = [freq]
        powers = mw._check_port_powers()
    finally:
        if keep is not None:
            mw.frequencies = keep
    A, b, ids, mats = asm.system(freq)
    NF = A.shape[0]
    A0 = submatrix(A, ids).tocsr()
    bs = np.asarray(b[ids], dtype=np.complex128)

    def X_of(c: dict):
        return asm.X0 + sum(ci * steps[i] * V[i] for i, ci in c.items())

    def at(c: dict):
        Ac, bc, idc, _ = asm.system(freq, X_of(c), key=tuple(sorted(c.items())))
        if not np.array_equal(idc, ids):
            raise MO.MorphError("the morphed mesh changes the unknowns of the solve")
        return submatrix(Ac, ids).tocsr(), np.asarray(bc[ids], dtype=np.complex128)

    t = time.time()
    Ai, Aii, bi, bii, Ap, bp = [], [], [], [], [], []
    for i in range(n):
        (ap, bpi), (am, bmi) = at({i: +1}), at({i: -1})
        Ai.append((ap - am) / (2 * steps[i]))
        Aii.append((ap - 2 * A0 + am) / steps[i] ** 2)
        bi.append((bpi - bmi) / (2 * steps[i]))
        bii.append((bpi - 2 * bs + bmi) / steps[i] ** 2)
        Ap.append(ap)
        bp.append(bpi)
    Aij, bij = {}, {}
    for i, j in pairs(n):
        apij, bpij = at({i: +1, j: +1})
        Aij[(i, j)] = (apij - Ap[i] - Ap[j] + A0) / (steps[i] * steps[j])
        bij[(i, j)] = (bpij - bp[i] - bp[j] + bs) / (steps[i] * steps[j])
    asm.restore()
    t_asm = time.time() - t
    t = time.time()
    out, W = reduce_system(A0, bs, Ai, Aii, Aij, _Factor, bi, bii, bij)
    t_fac = time.time() - t
    out.update(_functionals(asm, mw, freq, W, NF, ids, mats, powers, V, steps, ports_move))
    if log is not None:
        log(f"{freq / 1e9:.6g} GHz: basis {W.shape[1]}, assembly {t_asm:.1f} s, solves {t_fac:.1f} s")
    return out


# ------------------------------------------------------------------------------------------------
# one reduced model for the whole band
# ------------------------------------------------------------------------------------------------
class NotAffine(RuntimeError):
    """The matrix does not split into frequency independent parts (frequency dependent materials,
    periodic boundaries): the band model is not possible, every frequency is reduced on its own."""


def _fd(vals: dict, n: int, h: list[float]):
    """Central differences of values at the states: (value, [d/dp_i], [d2/dp_i2], [d2/dp_i dp_j])."""
    v0 = vals[()]
    d1 = [(vals[((i, 1),)] - vals[((i, -1),)]) / (2 * h[i]) for i in range(n)]
    d2 = [(vals[((i, 1),)] - 2 * v0 + vals[((i, -1),)]) / h[i] ** 2 for i in range(n)]
    dx = [(vals[((i, 1), (j, 1))] - vals[((i, 1),)] - vals[((j, 1),)] + v0) / (h[i] * h[j]) for i, j in pairs(n)]
    return v0, d1, d2, dx


class BandModel:
    """The FEM matrix of every node state split as A(f) = E - k0^2 B + R(f): the volume matrices E and B do
    not depend on the frequency and are assembled once per state, only the boundary terms R(f) (radiation
    boundaries, ports, surface impedances) and the excitations are assembled per frequency, which is
    cheap. The derivatives with respect to the variables split the same way.

    One basis W serves the whole band: the solution and its derivatives at a few expansion frequencies
    (one factorization each), added where the residual of the reduced solution is largest until it is
    below the tolerance everywhere. Every frequency of the model then costs only boundary assembly and
    small dense algebra."""

    def __init__(self, asm: Assembly, V: list[np.ndarray], steps: list[float], f_ref: float, log=None):
        from emerge._emerge.physics.microwave.bcs import RobinBC
        from emerge._emerge.periodic import Periodic
        self.asm, self.V, self.steps, self.log = asm, V, steps, log or (lambda s: None)
        mw = asm.mw
        self.n = n = len(V)
        if any(isinstance(bc, Periodic) for bc in mw.bc.boundary_conditions):
            raise NotAffine("periodic boundaries")
        self.robin = [bc for bc in mw.bc.boundary_conditions if isinstance(bc, RobinBC)]
        self.states = [()] + [((i, 1),) for i in range(n)] + [((i, -1),) for i in range(n)] + \
            [((i, 1), (j, 1)) for i, j in pairs(n)]
        self.parts = {}
        E, Bv = {}, {}
        for key in self.states:
            st = self._state(key, f_ref)
            E[key], Bv[key] = st.pop("E"), st.pop("B")
            self.parts[key] = st
        self.ids = self.parts[()]["ids"]
        self.mats = self.parts[()]["mats"]
        self.NF = mw.basis.n_field
        self.E = _fd(E, n, steps)
        self.B = _fd(Bv, n, steps)
        del E, Bv
        asm.restore()

    def _move(self, key: tuple) -> None:
        if key:
            X = self.asm.X0 + sum(c * self.steps[i] * self.V[i] for i, c in key)
            self.asm._move(X, key)
        else:
            self.asm.restore()

    def _state(self, key: tuple, f_ref: float) -> dict:
        from emerge._emerge.mth.csc_submatrix import csc_submatrix
        self._move(key)
        mw, asy = self.asm.mw, self.asm.mw.assembler
        field, mesh, bcs = mw.basis, self.asm.mesh, mw.bc.boundary_conditions
        er, ur, cond, fdep = asy._assemble_materials(mw.mat_assy, field, f_ref)
        if fdep:
            raise NotAffine("frequency dependent materials")
        ct = asy._find_conductor_tets(field, bcs, cond)
        system, E, Bm = asy._prepare_system(field, er, ur, ct, self.robin, True, bcs=bcs)
        pec_ids, _ = asy._collect_pec_dofs(field, mesh, bcs, ct, cond)
        ids = np.asarray(asy._solve_ids(field.n_field, pec_ids))
        if key and not np.array_equal(ids, self.parts[()]["ids"]):
            raise MO.MorphError("the morphed mesh changes the unknowns of the solve")
        g = np.full(field.n_field, -1)
        g[ids] = np.arange(len(ids))
        rows = np.asarray(system.indices)
        cols = np.repeat(np.arange(system.N), np.diff(system.indptr))
        return {"system": system, "er": er, "ids": ids, "mats": (er, ur, cond),
                "E": csc_submatrix(system.as_csc_matrix(E), ids).tocsr(),
                "B": csc_submatrix(system.as_csc_matrix(Bm), ids).tocsr(),
                "rows": g[rows], "cols": g[cols], "mask": None}

    def boundary(self, key: tuple, f: float, W: np.ndarray | None = None):
        """(R, b) of a state at frequency f: boundary matrix terms (solve dofs, sparse) and excitations;
        with W their projections (W^T R W, W^T b), from the rows and columns R touches only."""
        import scipy.sparse as sp
        self._move(key)
        st = self.parts[key]
        mw, asy = self.asm.mw, self.asm.mw.assembler
        system = st["system"]
        R = np.zeros(system.nnz, dtype=np.complex128)
        pv = {si: np.zeros(self.NF, dtype=np.complex128) for _, si, _ in mw.bc.iter_port_modes()}
        k0 = 2 * np.pi * f / 299792458
        asy._assemble_robin_terms(system, R, mw.basis, self.asm.mesh, k0, st["er"], self.robin, port_vectors=pv)
        nz = R != 0
        if st["mask"] is None or (nz & ~st["mask"]).any():
            st["mask"] = nz if st["mask"] is None else (st["mask"] | nz)
            st["sel"] = sel = np.flatnonzero(st["mask"] & (st["rows"] >= 0) & (st["cols"] >= 0))
            st["rr"], st["ri"] = np.unique(st["rows"][sel], return_inverse=True)
            st["cc"], st["ci"] = np.unique(st["cols"][sel], return_inverse=True)
        sel = st["sel"]
        b = np.column_stack([pv[si] for _, si, _ in mw.bc.iter_port_modes()])[self.ids]
        if W is not None:
            Rc = sp.csr_matrix((R[sel], (st["ri"], st["ci"])), shape=(len(st["rr"]), len(st["cc"])))
            return W[st["rr"]].T @ (Rc @ W[st["cc"]]), W.T @ b
        n = len(self.ids)
        Rs = sp.csr_matrix((R[sel], (st["rows"][sel], st["cols"][sel])), shape=(n, n))
        return Rs, b

    def boundary_terms(self, f: float, W: np.ndarray | None = None):
        """Boundary matrix terms and excitations at f and their derivatives (_fd form), projected onto W
        when given."""
        Rs, bs = {}, {}
        for key in self.states:
            Rs[key], bs[key] = self.boundary(key, f, W)
        self.asm.restore()
        return _fd(Rs, self.n, self.steps), _fd(bs, self.n, self.steps)

    @staticmethod
    def _combine(Et, Bt, Rt, k0: float):
        """(A0, [A_i], [A_ii], {(i, j): A_ij}) from the _fd forms of E, B and R."""
        n = len(Et[1])
        A0 = Et[0] - k0 ** 2 * Bt[0] + Rt[0]
        Ai = [Et[1][i] - k0 ** 2 * Bt[1][i] + Rt[1][i] for i in range(n)]
        Aii = [Et[2][i] - k0 ** 2 * Bt[2][i] + Rt[2][i] for i in range(n)]
        Aij = {key: Et[3][q] - k0 ** 2 * Bt[3][q] + Rt[3][q] for q, key in enumerate(pairs(n))}
        return A0, Ai, Aii, Aij

    def expansion(self, f: float) -> np.ndarray:
        """Solution and derivatives at f (one factorization)."""
        from .fast_sweep import _Factor
        self.asm.mw._compute_modes(f)
        Rt, bt = self.boundary_terms(f)
        A0, Ai, Aii, Aij = self._combine(self.E, self.B, Rt, 2 * np.pi * f / 299792458)
        bij = {key: bt[3][q] for q, key in enumerate(pairs(self.n))}
        return derivative_solutions(A0.tocsr(), bt[0], Ai, Aii, Aij, _Factor, bt[1], bt[2], bij)

    def basis(self, freqs: np.ndarray, tol: float = 1e-4, max_points: int | None = None) -> np.ndarray:
        """Greedy choice of the expansion frequencies: start in the middle of the band, add the frequency
        with the largest relative residual of the reduced nominal solution until all are below tol."""
        freqs = np.asarray(freqs, float)
        max_points = max_points or len(freqs)
        E0, B0 = self.E[0], self.B[0]
        nominal = {}
        mw = self.asm.mw
        for f in freqs:                     # nominal boundary terms of every frequency (reused each round)
            mw._compute_modes(float(f))
            nominal[float(f)] = self.boundary((), float(f))
        k_mid = int(np.argmin(np.abs(freqs - 0.5 * (freqs.min() + freqs.max()))))
        todo, used = [float(freqs[k_mid])], []
        W = None
        EW = BW = None
        res = {}
        while todo:
            f = todo.pop()
            used.append(f)
            t = time.time()
            W_old = 0 if W is None else W.shape[1]
            W = _extend(W, self.expansion(f))
            new = W[:, W_old:]
            EW = E0 @ new if EW is None else np.hstack([EW, E0 @ new])
            BW = B0 @ new if BW is None else np.hstack([BW, B0 @ new])
            for fq in freqs:
                fq = float(fq)
                if fq in used:
                    res[fq] = 0.0
                    continue
                R0, b0 = nominal[fq]
                k0 = 2 * np.pi * fq / 299792458
                AW = EW - k0 ** 2 * BW + R0 @ W
                y = np.linalg.solve(W.T @ AW, W.T @ b0)
                r = AW @ y - b0
                res[fq] = float(np.max(np.linalg.norm(r, axis=0) / np.maximum(np.linalg.norm(b0, axis=0), 1e-300)))
            worst = max(res, key=res.get)
            self.log(f"expansion at {f / 1e9:.6g} GHz: basis {W.shape[1]}, largest residual {res[worst]:.1e} "
                     f"at {worst / 1e9:.6g} GHz ({time.time() - t:.1f} s)")
            if res[worst] > tol and len(used) < max_points:
                todo.append(worst)
        self.used = used
        return W

    def frequency_terms(self, f: float, W: np.ndarray, ER: tuple, BR: tuple, ports_move: bool,
                        functionals: dict | None = None) -> dict:
        """Reduced model of frequency f on the band basis W (the same keys as frequency_model);
        functionals: the S functionals at f if known (interpolated over the band)."""
        mw = self.asm.mw
        k0 = 2 * np.pi * f / 299792458
        mw._compute_modes(f)
        RR, bt = self.boundary_terms(f, W)
        A0, Ai, Aii, Aij = self._combine(ER, BR, RR, k0)
        n, k = self.n, W.shape[1]
        out = {"A0": A0, "b": bt[0],
               "Ai": np.array(Ai).reshape(n, k, k), "Aii": np.array(Aii).reshape(n, k, k),
               "Aij": np.array([Aij[key] for key in pairs(n)]).reshape(len(Aij), k, k)}
        m = bt[0].shape[1]
        if any(np.abs(v).max() > 1e-12 * max(float(np.abs(bt[0]).max()), 1e-300) for v in bt[1] + bt[2] + bt[3]):
            out["b_i"] = np.array(bt[1]).reshape(n, k, m)
            out["b_ii"] = np.array(bt[2]).reshape(n, k, m)
            out["b_ij"] = np.array(bt[3]).reshape(len(bt[3]), k, m)
        out.update(functionals if functionals is not None else self.functionals(f, W, ports_move))
        return out

    def functionals(self, f: float, W: np.ndarray, ports_move: bool) -> dict:
        mw = self.asm.mw
        mw._compute_modes(f)
        return _functionals(self.asm, mw, f, W, self.NF, self.ids, self.mats, _port_powers(mw, f), self.V,
                            self.steps, ports_move)


def _port_powers(mw, f: float) -> dict:
    keep = list(getattr(mw, "frequencies", []) or [])
    try:
        mw.frequencies = [f]
        return mw._check_port_powers()
    finally:
        mw.frequencies = keep


FUNCTIONAL_POINTS = 7           # frequencies of the port functionals of a band model (interpolated between)


def _interpolated_functionals(bm: "BandModel", freqs: np.ndarray, W: np.ndarray, ports_move: bool,
                              log=None) -> list[dict] | None:
    """The S functionals of the ports on W at all frequencies, from FUNCTIONAL_POINTS frequencies
    (Chebyshev-Lobatto points over the band) and a polynomial through them: they follow the port modes,
    which change smoothly with frequency (the resonances are in the solution, not in the functionals).
    None when the band has few frequencies (all computed exactly)."""
    from scipy.interpolate import BarycentricInterpolator
    nq = FUNCTIONAL_POINTS
    if len(freqs) <= nq + 2:
        return None
    a, b = float(freqs.min()), float(freqs.max())
    xs = np.cos(np.pi * np.arange(nq) / (nq - 1))[::-1]           # -1 .. 1
    vals = [bm.functionals(0.5 * (a + b) + 0.5 * (b - a) * x, W, ports_move) for x in xs]
    t = 2 * (np.asarray(freqs, float) - a) / (b - a) - 1
    out = [dict() for _ in freqs]
    for key in vals[0]:
        P = BarycentricInterpolator(xs, np.array([v[key] for v in vals]), axis=0)
        Y = P(t)
        for k in range(len(freqs)):
            out[k][key] = Y[k]
    if log is not None:
        log(f"Port functionals at {nq} frequencies, interpolated over {len(freqs)}")
    return out


def _functionals(asm, mw, freq, W, NF, ids, mats, powers, V, steps, ports_move) -> dict:
    """S functionals of the ports on W, with their derivatives when lumped ports stretch."""
    n = len(V)
    F0 = s_functionals(mw, freq, W, NF, ids, mats, powers)
    out = {"Lp": F0[0], "La": F0[1], "c0": F0[2]}
    if not (ports_move and n):
        return out
    hs = [FUNCTIONAL_STEP * s_ for s_ in steps]
    states = [{i: +1} for i in range(n)] + [{i: -1} for i in range(n)] + [{i: +1, j: +1} for i, j in pairs(n)]
    Fs = {}
    for c in states:
        asm._move(asm.X0 + sum(ci * hs[i] * V[i] for i, ci in c.items()), asm._ANON)
        Fs[tuple(sorted(c.items()))] = s_functionals(mw, freq, W, NF, ids, mats, powers)
    asm.restore()
    for q, name in enumerate(("Lp", "La", "c0")):
        f0 = F0[q]
        fp = [Fs[((i, +1),)][q] for i in range(n)]
        fm_ = [Fs[((i, -1),)][q] for i in range(n)]
        out[f"{name}_i"] = np.array([(fp[i] - fm_[i]) / (2 * hs[i]) for i in range(n)])
        out[f"{name}_ii"] = np.array([(fp[i] - 2 * f0 + fm_[i]) / hs[i] ** 2 for i in range(n)])
        dx = [(Fs[((i, +1), (j, +1))][q] - fp[i] - fp[j] + f0) / (hs[i] * hs[j]) for i, j in pairs(n)]
        out[f"{name}_ij"] = np.array(dx).reshape((len(dx),) + f0.shape)
    return out


def band_models(asm: Assembly, freqs: np.ndarray, V: list[np.ndarray], steps: list[float], log=None,
                ports_move: bool = False, tol: float = 1e-4, progress=None, abort=None,
                on_frequency=None) -> list[dict]:
    """Reduced models of all frequencies on one band basis (BandModel)."""
    log = log or (lambda s: None)
    t = time.time()
    bm = BandModel(asm, V, steps, float(np.mean(freqs)), log=log)
    log(f"Volume matrices of {len(bm.states)} mesh states: {time.time() - t:.1f} s")
    W = bm.basis(freqs, tol=tol)
    log(f"Band basis: {W.shape[1]} vectors from {len(bm.used)} expansion frequencies")
    proj = lambda M: W.T @ (M @ W)      # noqa: E731
    ER = (proj(bm.E[0]), [proj(M) for M in bm.E[1]], [proj(M) for M in bm.E[2]], [proj(M) for M in bm.E[3]])
    BR = (proj(bm.B[0]), [proj(M) for M in bm.B[1]], [proj(M) for M in bm.B[2]], [proj(M) for M in bm.B[3]])
    funs = _interpolated_functionals(bm, np.asarray(freqs, float), W, ports_move, log=log)
    out = []
    for k, f in enumerate(freqs):
        if abort is not None:
            abort()
        out.append(bm.frequency_terms(float(f), W, ER, BR, ports_move, None if funs is None else funs[k]))
        if on_frequency is not None:
            on_frequency(f)             # the port modes of f are current
        if progress is not None:
            progress(k + 1, len(freqs))
    asm.restore()
    return out


# ------------------------------------------------------------------------------------------------
# evaluation (no solver)
# ------------------------------------------------------------------------------------------------
def pairs(n: int) -> list[tuple[int, int]]:
    return [(i, j) for i in range(n) for j in range(i + 1, n)]


def _taylor(fm: dict, key: str, dp: np.ndarray) -> np.ndarray:
    """Term `key` of a frequency model at the offsets dp: constant, or quadratic in dp when the model has
    its derivatives (key_i, key_ii, key_ij)."""
    v = fm[key]
    if f"{key}_i" not in fm:
        return v
    v = v.astype(np.complex128, copy=True)
    for i, d in enumerate(dp):
        if d:
            v += d * fm[f"{key}_i"][i] + 0.5 * d * d * fm[f"{key}_ii"][i]
    for k, (i, j) in enumerate(pairs(len(dp))):
        if dp[i] and dp[j]:
            v += dp[i] * dp[j] * fm[f"{key}_ij"][k]
    return v


def evaluate_freq(fm: dict, dp: np.ndarray, caps: tuple[bool, bool] = (True, True)) -> np.ndarray:
    """Raw S-matrix (ports x ports) of one frequency model at the variable offsets dp."""
    A = fm["A0"].copy()
    for i, d in enumerate(dp):
        if d:
            A += d * fm["Ai"][i] + 0.5 * d * d * fm["Aii"][i]
    for k, (i, j) in enumerate(pairs(len(dp))):
        if dp[i] and dp[j]:
            A += dp[i] * dp[j] * fm["Aij"][k]
    Y = np.linalg.solve(A, _taylor(fm, "b", dp))
    n = len(fm["c0"])
    S = _taylor(fm, "Lp", dp) @ Y
    S[np.diag_indices(n)] = np.einsum("ik,ki->i", _taylor(fm, "La", dp), Y) + _taylor(fm, "c0", dp)
    col, single = caps
    if col:
        for j in range(n):
            S[:, j] /= max(1.0, float(np.sum(np.abs(S[:, j]) ** 2)))
    if single:
        S /= np.maximum(1.0, np.abs(S))
    return S


# ------------------------------------------------------------------------------------------------
# the tuning run (solver process) and the saved model
# ------------------------------------------------------------------------------------------------
MAX_POINTS = 201
ADAPTIVE = "LastAdaptive"       # sweep name of the tuning model at the solution frequency of the setup


def tuning_path(out: Path, sweep: str) -> Path:
    from .results import _safe
    return Path(out) / f"tuning_{_safe(sweep)}.npz"


def tuning_frequencies(props: dict, ev, points: int) -> np.ndarray:
    """Frequencies of the tuning model: the points of a discrete sweep, else `points` equally spaced over
    the sweep (an interpolating or fast sweep has many points, and every tuning point costs one
    factorization)."""
    from .bridge import sweep_frequencies
    fr = np.unique(np.asarray(sweep_frequencies(props, ev), float))
    if props.get("SweepType") == "Discrete" and len(fr) <= MAX_POINTS:
        return fr
    n = max(2, min(int(points), MAX_POINTS))
    return np.linspace(fr.min(), fr.max(), n) if len(fr) > 1 else fr


def morph_constraints(m) -> tuple[set, set]:
    """(fixed, sliding) face tags of the morphing: wave and Floquet ports stay where they are (their modes
    belong to the port face as solved), nodes on the outer boundary may slide within it (a substrate
    filling the region may change its height). Lumped ports stretch with the mesh (LumpedGeometry)."""
    LumpedPort, _ = _lumped_classes()
    fixed = set()
    for port, _, _ in m.mw.bc.iter_port_modes():
        if not isinstance(port, LumpedPort):
            fixed.update(port.tags)
    return fixed, set(m.mesh.exterior_face_tags) - fixed


def variable_step(text: str) -> float:
    """Finite difference step of the CAD build for a variable value (in its unit)."""
    sv = MO.split_value(text)
    if sv is None:
        raise MO.MorphError(f"'{text}' is an expression; only variables with a value can be tuned")
    return max(abs(sv[0]), 1.0) * 1e-4


def build(bridge, variables: list[str], freqs: np.ndarray, path: Path, log=None, progress=None,
          grid=None, band: bool = True) -> dict:
    """Tuning model of the EMerge model of bridge (prepared, final adaptive mesh) for the design
    variables, saved to path. grid: frequencies of the curve when the model frequencies are the solved
    points of an interpolating sweep (the curve is interpolated over them)."""
    from . import bridge as B
    from .results import port_entries
    m, design = bridge.m, bridge.design
    if B.TERMINALS is not None:
        raise MO.MorphError("derivative tuning does not support terminal designs yet")
    if not variables:
        raise MO.MorphError("select at least one variable")
    log = log or (lambda s: None)
    mesh = m.mesh
    # EMerge state of a regular solve (field, boundary data, materials, port modes)
    fc = 0.5 * (float(freqs.min()) + float(freqs.max()))
    B.solve_at(m, [fc])
    t = time.time()
    fixed, slide = morph_constraints(m)
    diag = float(np.linalg.norm(mesh.nodes.max(axis=1) - mesh.nodes.min(axis=1)))
    tol = 1e-6 * diag
    faces = {}
    for v in variables:
        if v not in design.variables:
            raise MO.MorphError(f"'{v}' is not a design variable")
        faces[v] = MO.face_velocities(bridge.project, design, v, variable_step(design.variables[v]))
        if not any(f.moving for f in faces[v]):
            raise MO.MorphError(f"variable '{v}' does not change the geometry of the model")
    fields = MO.velocity_fields(mesh, faces, fixed, tol, log=log, slide_ftags=slide)
    V = [fields[v] for v in variables]
    log(f"Mesh morphing for {', '.join(variables)}: {time.time() - t:.1f} s")
    X0 = np.array(mesh.nodes)
    ranges = [MO.valid_range(X0, mesh.tets, Vi, 0.3) for Vi in V]
    hmed = float(np.median(mesh.edge_lengths))
    steps = [1e-3 * hmed / max(float(np.abs(Vi).max()), 1e-300) for Vi in V]
    settings = m.mw._settings
    caps = (bool(settings.mw_cap_sp_col), bool(settings.mw_cap_sp_single))
    asm = Assembly(m)
    ports_move = any(np.abs(Vi[:, it["nid"]]).max() > 1e-9 * max(float(np.abs(Vi).max()), 1e-300)
                     for it in asm.lumped.items for Vi in V)
    if ports_move:
        log("Lumped ports or elements stretch with the variables")
    data: dict = {}
    beta, z0 = [], None
    nfreq = len(freqs)

    def abort():
        if (Path(bridge.out) / "ABORT").exists():
            raise B.SolveAbort("aborted")

    def port_data(f):
        nonlocal z0
        k0 = 2 * np.pi * float(f) / 299792458
        pm = list(m.mw.bc.iter_port_modes())
        beta.append([complex(p.get_beta(k0) or 0) for p, _, _ in pm])
        if z0 is None:
            z0 = np.array([complex(p.portZ0(k0) or 50.0) for p, _, _ in pm])

    try:
        fms = None
        if band and nfreq > 1:
            try:
                fms = band_models(asm, freqs, V, steps, log=log, ports_move=ports_move, progress=progress,
                                  abort=abort, on_frequency=port_data)
            except NotAffine as e:
                log(f"No band model ({e}): every frequency is reduced on its own")
                beta.clear()
                asm.restore()
        if fms is None:
            fms = []
            for k, f in enumerate(freqs):
                abort()
                fms.append(frequency_model(asm, float(f), V, steps, log=log, ports_move=ports_move))
                port_data(f)
                if progress is not None:
                    progress(k + 1, nfreq)
        for k, fm in enumerate(fms):
            for key, val in fm.items():
                data[f"f{k}_{key}"] = val
    finally:
        asm.close()
    nominal = np.array([evaluate_freq({kk[len(f"f{k}_"):]: vv for kk, vv in data.items() if kk.startswith(f"f{k}_")},
                                      np.zeros(len(variables)), caps) for k in range(nfreq)])
    meta = {
        "variables": variables,
        "values": [design.variables[v] for v in variables],
        "units": [MO.split_value(design.variables[v])[1] for v in variables],
        "ranges": [[float(a), float(b)] for a, b in ranges],
        "steps": steps,
        "caps": list(caps),
        "ports": port_entries(bridge.port_map or []),
        "model_key": getattr(bridge, "keys", {}).get("model_key", ""),
        "tets": int(mesh.n_tets),
        "grid": None if grid is None else [float(x) for x in grid],
        "created": time.time(),
    }
    np.savez_compressed(path, freq=np.asarray(freqs, float), beta=np.array(beta), z0=z0, nominal=nominal,
                        meta=np.array(json.dumps(meta)), **data)
    return meta


class TuningModel:
    """A saved tuning model: S-parameters for variable offsets without a solver."""

    def __init__(self, path: Path):
        with np.load(path, allow_pickle=False) as z:
            self.freq = z["freq"]
            self.beta = z["beta"]
            self.z0 = z["z0"]
            self.nominal_raw = z["nominal"]
            self.meta = json.loads(str(z["meta"]))
            self.fms = []
            for k in range(len(self.freq)):
                pre = f"f{k}_"
                self.fms.append({name[len(pre):]: z[name] for name in z.files if name.startswith(pre)})
        self.variables = self.meta["variables"]
        self.ranges = np.array(self.meta["ranges"], float)
        self.caps = tuple(self.meta.get("caps", (True, True)))
        from .optimetrics import split_value
        self.values = np.array([split_value(v)[0] for v in self.meta["values"]], float)
        self.units = self.meta["units"]
        grid = self.meta.get("grid")
        self.grid = np.asarray(grid, float) if grid else None
        self.freq_out = self.grid if self.grid is not None else self.freq

    def raw(self, dp) -> np.ndarray:
        dp = np.asarray(dp, float)
        return np.array([evaluate_freq(fm, dp, self.caps) for fm in self.fms])

    def sparams(self, dp) -> tuple[np.ndarray, np.ndarray]:
        """(S at freq_out, reference impedance per row) at the variable offsets dp, after the same port
        post-processing (de-embedding, renormalization) as a solved sweep. A model of the solved points of
        an interpolating sweep is interpolated over the sweep frequencies as the sweep does."""
        from .results import deembed_renormalize, fit_sparams
        S = self.raw(dp)
        beta = self.beta
        if self.grid is not None:
            S = fit_sparams(self.freq, S)(self.grid)
            beta = np.array([np.interp(self.grid, self.freq, b.real) + 1j * np.interp(self.grid, self.freq, b.imag)
                             for b in np.asarray(self.beta).T]).T
        ports = self.meta.get("ports") or []
        beta = beta if any(q.get("deembed") for q in ports) else None
        return deembed_renormalize(S, self.z0, ports, beta)

    def sweep_variable(self, k: int, i: int, values, dp) -> tuple[np.ndarray, np.ndarray]:
        """(S per value, reference impedance per row) at frequency index k with variable i set to the
        offsets `values` and the other variables at their offsets in dp."""
        from .results import deembed_renormalize
        rows = []
        for v in values:
            d = np.array(dp, float)
            d[i] = v
            rows.append(evaluate_freq(self.fms[k], d, self.caps))
        S = np.array(rows)
        ports = self.meta.get("ports") or []
        beta = self.beta[[k] * len(S)] if any(q.get("deembed") for q in ports) else None
        return deembed_renormalize(S, self.z0, ports, beta)

    def nominal(self) -> tuple[np.ndarray, np.ndarray]:
        return self.sparams(np.zeros(len(self.variables)))
