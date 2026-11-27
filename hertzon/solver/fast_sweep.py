"""Fast frequency sweep ("Fast", the idea of ALPS): one factorization for the whole band.

The matrix is factorized once, at the centre frequency. Every other frequency is still assembled exactly
(materials, radiation boundaries, port modes and excitations), but instead of factorizing it, its system is
solved in a small basis V of field solutions (Galerkin projection, V^T A V y = V^T b, x = V y). The
residual of the full system says how good that solution is. When it is above the tolerance the basis is
enriched with A0^-1 r (one back substitution with the centre factorization), which is a Krylov step of the
preconditioned system, and the frequency is solved again. A frequency the basis still cannot reach after a
few enrichments is solved directly and its solution joins the basis (a second expansion point).

So every solution meets the residual tolerance, and S-parameters and fields exist at every sweep point,
while the expensive factorization runs once (or a few times for very wide bands). The frequencies are
solved from the centre outwards, so the basis grows smoothly.
"""
from __future__ import annotations

import time

import numpy as np

try:
    from numba import njit, prange
except Exception:  # noqa: BLE001
    njit = None

if njit is not None:
    @njit(cache=True, nogil=True, parallel=True)
    def _csr_matmat(indptr, indices, data, X, Y):  # pragma: no cover - compiled
        """Y = A X for a CSR matrix A and a row-major block X, rows in parallel."""
        m = X.shape[1]
        for i in prange(indptr.shape[0] - 1):
            for j in range(m):
                Y[i, j] = 0.0
            for k in range(indptr[i], indptr[i + 1]):
                c = indices[k]
                v = data[k]
                for j in range(m):
                    Y[i, j] += v * X[c, j]


def spmm(A, X: np.ndarray, out: np.ndarray | None = None, chunk: int = 32) -> np.ndarray:
    """A @ X for a sparse CSR matrix and a dense block on all cores (numba; scipy's product runs on one core:
    half the time of a fast sweep with 11 ports went there), written into `out` when given. X is taken in
    chunks of columns, each copied row-major, so the copy stays small."""
    m = X.shape[1]
    if out is None:
        out = np.empty((A.shape[0], m), dtype=np.complex128, order="F")
    if njit is None or not m or A.format != "csr" or A.dtype != np.complex128:
        out[:, :] = A @ X
        return out
    for j0 in range(0, m, chunk):
        Xc = np.ascontiguousarray(X[:, j0:j0 + chunk], dtype=np.complex128)
        Yc = np.empty((A.shape[0], Xc.shape[1]), dtype=np.complex128)
        _csr_matmat(A.indptr, A.indices, A.data, Xc, Yc)
        out[:, j0:j0 + Xc.shape[1]] = Yc
    return out


class _Factor:
    """Factorization of the centre matrix, reused for many right-hand sides (PARDISO, else SuperLU).

    EMerge's PARDISO interface factorizes the complex symmetric FEM matrix as LDL^T on its upper triangle (as in
    the main solve, about half the factor memory of LU), a matrix that is not symmetric as LU. All right-hand
    sides of a call are solved in one PARDISO call (nrhs > 1)."""

    def __init__(self, A):
        self.A = A.tocsr()
        self.A.sort_indices()
        self.M = self.A
        self.kind = ""
        self._p = None
        self._lu = None
        try:
            from emerge._emerge.solvers import pardiso as PI
            p = PI.PardisoInterface()
            # EMerge factorizes a complex symmetric matrix as LDL^T on its upper triangle, any other one as LU
            if p.symbolic(self.A) == 0 and p.numeric(self.A) == 0:
                self._p, self.kind = p, "LDL^T" if p.symmetric else "LU"
            else:
                p.clear_memory()
        except Exception:  # noqa: BLE001
            self._p = None
        if self._p is None:
            from scipy.sparse.linalg import splu
            self._lu = splu(self.A.tocsc())
            self.kind = "SuperLU"

    def solve(self, B: np.ndarray) -> np.ndarray:
        B = np.asarray(B, dtype=np.complex128)
        if B.ndim == 1:
            B = B[:, None]
        if self._lu is not None:
            return self._lu.solve(B)
        try:
            return self._block(B)
        except Exception:  # noqa: BLE001
            pass
        out = np.empty_like(B)
        for i in range(B.shape[1]):
            x, err = self._p.solve(self.M, np.ascontiguousarray(B[:, i]))
            if err != 0:
                raise RuntimeError(f"PARDISO error {err}")
            out[:, i] = x
        return out

    def _block(self, B: np.ndarray) -> np.ndarray:
        """All columns of B in one PARDISO solve (phase 33 with nrhs columns, column major)."""
        import ctypes
        from emerge._emerge.solvers import pardiso as PI
        p = self._p
        p._configure(self.A)
        if not p.complex:
            raise TypeError("complex matrix expected")
        M = p._prepare_A(self.A)            # the upper triangle on the LDL^T path, else A itself
        Bf = np.asfortranarray(B)
        X = np.zeros_like(Bf, order="F")
        if p.IPARM[34] == 1:                # zero-based indices
            ia = np.ascontiguousarray(M.indptr, dtype=np.int32)
            ja = np.ascontiguousarray(M.indices, dtype=np.int32)
        else:
            ia = (M.indptr + 1).astype(np.int32)
            ja = (M.indices + 1).astype(np.int32)
        err = ctypes.c_int32(0)
        p._pardiso_interface(
            p.PT.ctypes.data_as(ctypes.POINTER(PI.PT_A)), PI.c_int(1), PI.c_int(1), PI.c_int(p.MATRIX_TYPE.value),
            PI.c_int(PI.PARDISOPhase.NUMERIC_SOLVE.value), PI.c_int(M.shape[0]), M.data.ctypes.data_as(PI.CPX16_P),
            ia.ctypes.data_as(PI.CINT32_P), ja.ctypes.data_as(PI.CINT32_P), p.PERM.ctypes.data_as(PI.CINT32_P),
            PI.c_int(Bf.shape[1]), p.IPARM.ctypes.data_as(PI.CINT32_P), PI.c_int(p.message_level),
            Bf.ctypes.data_as(PI.CPX16_P), X.ctypes.data_as(PI.CPX16_P), ctypes.byref(err))
        if err.value != 0:
            raise RuntimeError(f"PARDISO error {err.value}")
        return np.ascontiguousarray(X)

    def close(self) -> None:
        if self._p is not None:
            try:
                self._p.clear_memory()
            except Exception:  # noqa: BLE001
                pass
        self._p = self._lu = self.A = None


class ReducedSolver:
    """Stands in for EMerge's SolveRoutine.solve during a fast sweep."""

    def __init__(self, direct_solve, tol: float = 1e-4, max_enrich: int = 8, n_freqs: int = 1,
                 room=None, release=None, n_tets: int = 0, keep_fields: bool = True):
        self.direct = direct_solve           # EMerge's own solve (A, b, solve_ids, ...) -> (x, report)
        self.tol = tol
        self.max_enrich = max_enrich
        self.n_freqs = n_freqs               # frequencies of the sweep (their solutions are kept)
        self.room = room                     # () -> bytes this process may still grow by, None: no limit
        self.release = release               # () frees the memory of the direct solver after a direct solve
        self.n_tets = n_tets                 # tetrahedra of the mesh (per frequency EMerge keeps data on each)
        self.keep_fields = keep_fields       # EMerge keeps the fields of every frequency (bridge.keep_fields)
        self.max_basis: int | None = None    # basis vectors that fit in memory (set after the first solve)
        self.n_restart = 0
        self._buf: np.ndarray | None = None  # preallocated basis (with a memory limit)
        self._avbuf: np.ndarray | None = None
        self.V: np.ndarray | None = None     # orthonormal basis on the solve dofs
        self.ids: np.ndarray | None = None
        self.fac: _Factor | None = None
        self.n_direct = 0
        self.n_reduced = 0
        self.n_enrich = 0
        self.worst = 0.0
        self.t_fact = 0.0
        self.t_solve = 0.0

    # basis ---------------------------------------------------------------------------------------
    def _add(self, X: np.ndarray) -> int:
        """Orthonormalize the columns of X against the basis and each other and append the new directions:
        block Gram-Schmidt against the basis (twice, two passes over it for the whole block; vector by vector
        it was two passes per vector), then a QR factorization of the block that drops directions already in
        the basis. Into the preallocated basis when there is one, else with one copy for the whole block."""
        W = np.array(X, dtype=np.complex128, order="F")
        if W.ndim == 1:
            W = W[:, None]
        n0 = np.linalg.norm(W, axis=0)
        keep0 = n0 > 0
        if not keep0.any():
            return 0
        W, n0 = W[:, keep0], n0[keep0]
        for _ in range(2):
            if self.V is not None and self.V.shape[1]:
                C = (W.conj().T @ self.V).conj().T          # V^H W without a conjugated copy of V
                W -= self.V @ C
        Q, R = np.linalg.qr(W)
        good = np.abs(np.diag(R)) > 1e-10 * n0
        if not good.any():
            return 0
        Q = Q[:, good]
        k = 0 if self.V is None else self.V.shape[1]
        a = Q.shape[1]
        if self._buf is not None and k + a <= self._buf.shape[1]:
            self._buf[:, k:k + a] = Q                       # into the preallocated basis: no copy of the basis
            self.V = self._buf[:, :k + a]
        else:
            Q = np.asfortranarray(Q)
            self.V = Q if not k else np.hstack([self.V, Q])
        return a

    def _set_max_basis(self, n: int, nb: int, nf: int) -> None:
        """How many basis vectors fit in the memory left after the centre factorization: each one is held
        twice (V and A V of the reduced solve, counted 2.5 times for the temporaries), the solutions of all
        sweep frequencies are kept as far as EMerge keeps them (bridge.sweep_kept_bytes), a direct solve needs its
        own factorization (bridge.fast_sweep_direct_bytes), and 15 % of the room stays free. The basis is then
        preallocated, so it no longer copies itself when it grows. Without a limit the basis grew with ports x enrichments x frequencies (Rotman lens array, 11
        ports, 17 frequencies, 425k unknowns: more than 280 vectors, 2 GB each copy) until the operating
        system ended the solver on a 15 GB machine; a limit that counted 3 vectors with the copies did too."""
        if self.room is None:
            return
        try:
            room = float(self.room())
        except Exception:  # noqa: BLE001
            return
        if not np.isfinite(room):
            return
        room = max(0.0, room)
        from .bridge import fast_sweep_direct_bytes, sweep_kept_bytes
        # what the sweep keeps, a direct solve's factorization next to the centre one, and the right hand
        # sides, residuals and solutions of one frequency (6 x nb vectors)
        keep = (sweep_kept_bytes(nf, nb, self.n_tets, self.n_freqs, self.keep_fields) + fast_sweep_direct_bytes()
                + 16.0 * 6 * nb * n)
        self.max_basis = max(2 * nb, int((0.85 * room - keep) / (2.5 * 16.0 * n)))
        # column major: V = buf[:, :k] stays contiguous, so the products with V and V^T copy nothing
        self._buf = np.empty((n, self.max_basis + nb), dtype=np.complex128, order="F")  # pages taken when written
        k = 0 if self.V is None else self.V.shape[1]       # the first solution, nb <= max_basis vectors
        if k:
            self._buf[:, :k] = self.V
            self.V = self._buf[:, :k]

    def _restart_if_full(self, x: np.ndarray | None, extra: int) -> None:
        """Basis at its memory limit: start again from the current solution (the factorization stays), like
        a restarted Krylov method; later frequencies may need more enrichments, but the memory is bounded."""
        if self.max_basis is None or self.V is None or self.V.shape[1] + extra <= self.max_basis:
            return
        self.V = None
        if x is not None:
            self._add(x)
        self.n_restart += 1

    # solve -----------------------------------------------------------------------------------------
    def solve(self, A, b, solve_ids, direct=True, id=-1, matrix_type=None, **kw):  # noqa: A002
        from emerge._emerge.solvers import SolveReport
        t = time.time()
        b2 = b.reshape((b.shape[0], 1)) if b.ndim == 1 else b
        if self.ids is not None and not np.array_equal(self.ids, solve_ids):
            self.reset()                    # different unknowns (should not happen within one sweep)
        from emerge._emerge.mth.csc_submatrix import csc_submatrix as submatrix
        Asel = submatrix(A, solve_ids).tocsr()
        bsel = np.asarray(b2[solve_ids, :], dtype=np.complex128)
        bn = np.maximum(np.linalg.norm(bsel, axis=0), 1e-300)
        if self.fac is None:
            t0 = time.time()
            self.fac = _Factor(Asel)
            self.t_fact += time.time() - t0
            self.ids = np.asarray(solve_ids).copy()
            x = self.fac.solve(bsel)
            self._add(x)
            self.n_direct += 1
            how = "centre factorization"
        else:
            if self.max_basis is None and self._buf is None:
                # after the first solve, when the factorization and the first solution hold their memory
                self._set_max_basis(bsel.shape[0], bsel.shape[1], A.shape[0])
            x, rel, it = self._reduced(Asel, bsel, bn)
            if rel > self.tol:
                self._avbuf = None      # its memory goes back before the direct solve factorizes
                sol, rep = self.direct(A, b, solve_ids, direct=direct, id=id,
                                       **({"matrix_type": matrix_type} if matrix_type is not None else {}))
                if self.release is not None:
                    try:        # its factorization is not reused: free it beside the centre factorization
                        self.release()
                    except Exception:  # noqa: BLE001
                        pass
                xs = np.asarray(sol)[solve_ids, :]
                self._restart_if_full(None, xs.shape[1])
                self._add(xs)
                self.n_direct += 1
                rep.add(fast_sweep=f"direct solve (residual {rel:.1e} after {it} enrichments)")
                return sol, rep
            self.n_reduced += 1
            self.worst = max(self.worst, rel)
            how = f"reduced basis {self.V.shape[1]}, residual {rel:.1e}"
        NF, NB = A.shape[0], b2.shape[1]
        sol = np.zeros((NF, NB), dtype=np.complex128)
        sol[solve_ids, :] = x
        self.t_solve += time.time() - t
        rep = SolveReport(solver="Fast sweep", exit_code=0)
        rep.add(fast_sweep=how)
        rep.jobid = id
        rep.simtime = time.time() - t
        rep.nnz = A.nnz
        rep.ndof = b2.shape[0]
        rep.nnz_solve = Asel.nnz
        rep.ndof_solve = bsel.shape[0]
        return sol, rep

    def _av(self, Asel, AV, k0: int) -> np.ndarray:
        """A V for the current basis: columns from k0 on are computed, the ones before are kept (the matrix
        is the same within a frequency; recomputing all of them every enrichment took half the sweep)."""
        V = self.V
        k = V.shape[1]
        if self._avbuf is None and self._buf is not None:
            # A V of the current frequency, as many columns as the basis; freed before a direct solve
            self._avbuf = np.empty(self._buf.shape, dtype=np.complex128, order="F")
        if self._avbuf is not None and k <= self._avbuf.shape[1]:
            if AV is not None and k0 and AV.base is not self._avbuf and AV is not self._avbuf:
                self._avbuf[:, :k0] = AV[:, :k0]
            spmm(Asel, V[:, k0:], out=self._avbuf[:, k0:k])
            return self._avbuf[:, :k]
        new = spmm(Asel, V[:, k0:])
        return new if not k0 else np.hstack([AV[:, :k0], new])

    def _reduced(self, Asel, bsel, bn):
        rel = np.inf
        x = None
        it, limit = 0, self.max_enrich
        AV, Ar, k_done = None, None, 0
        while True:
            V = self.V
            k = V.shape[1]
            AV = self._av(Asel, AV, k_done)
            # A is complex symmetric: V^T keeps the reduced system symmetric, so only the new columns of
            # V^T A V are computed and mirrored
            Ar_new = np.empty((k, k), dtype=np.complex128)
            if k_done:
                Ar_new[:k_done, :k_done] = Ar
            B = V.T @ AV[:, k_done:]
            Ar_new[:, k_done:] = B
            Ar_new[k_done:, :k_done] = B[:k_done, :].T
            Ar, k_done = Ar_new, k
            br = V.T @ bsel
            try:
                y = np.linalg.solve(Ar, br)
            except np.linalg.LinAlgError:
                y = np.linalg.lstsq(AV, bsel, rcond=None)[0]
            x = V @ y
            r = bsel - AV @ y
            col = np.linalg.norm(r, axis=0) / bn
            rel = float(np.max(col))
            if rel <= self.tol or it >= limit:
                return x, rel, it
            restarts = self.n_restart
            open_cols = col > self.tol          # ports that already meet the tolerance need no enrichment
            self._restart_if_full(x, int(open_cols.sum()))
            if self.n_restart > restarts:
                AV, Ar, k_done = None, None, 0
                if limit == self.max_enrich:
                    # a fresh basis from the current solution needs its own enrichments, still cheaper than a
                    # direct solve (a factorization, and memory beside the centre one)
                    limit = it + self.max_enrich
            if self._add(self.fac.solve(r[:, open_cols])) == 0:
                return x, rel, it
            self.n_enrich += 1
            it += 1

    def reset(self) -> None:
        if self.fac is not None:
            self.fac.close()
        self.fac = None
        self.V = None
        self._buf = None
        self._avbuf = None
        self.ids = None

    def summary(self) -> str:
        r = 0 if self.V is None else self.V.shape[1]
        kind = f", {self.fac.kind}" if self.fac is not None and self.fac.kind else ""
        return (f"{self.n_direct} factorization(s) ({self.t_fact:.1f} s for the centre{kind}), {self.n_reduced} "
                f"frequencies from a basis of {r} vectors ({self.n_enrich} enrichments"
                f"{f', {self.n_restart} restarts at the memory limit of {self.max_basis} vectors' if self.n_restart else ''}), "
                f"worst residual {self.worst:.1e}, {self.t_solve:.1f} s in the solves")


def _room() -> float:
    """Bytes this process may still grow by under the memory guard (GUARD_FRACTION of the usable memory)."""
    from .bridge import GUARD_FRACTION, _current_rss, usable_memory
    use = usable_memory()
    return GUARD_FRACTION * use - _current_rss() if use > 0 else float("inf")


def fast_sweep(m, freqs, tol: float = 1e-4, log=None, on_solved=None):
    """Solve the sweep frequencies of EMerge model m with one centre factorization and a reduced basis.
    Returns what bridge.solve_at returns: (freq, S, z0, data)."""
    from .bridge import solve_at
    freqs = sorted(float(f) for f in np.atleast_1d(freqs))
    f0 = 0.5 * (freqs[0] + freqs[-1])
    order = sorted(freqs, key=lambda f: abs(f - f0))
    routine = m.mw.solveroutine
    red = ReducedSolver(routine.solve, tol=tol, n_freqs=len(freqs), room=_room, n_tets=int(m.mesh.n_tets),
                        keep_fields=bool(getattr(getattr(m, "settings", None), "mw_sweep_fields", True)),
                        release=lambda: [s.reset() for s in getattr(routine, "solvers", {}).values()])
    routine.solve = red.solve
    cache = getattr(m.mw, "cache_matrices", False)
    try:
        m.mw.cache_matrices = True          # E and B of the mesh are the same for every frequency
        out = solve_at(m, order, on_solved=on_solved, frequency_groups=1)
    finally:
        try:
            del routine.solve               # back to the class method
        except AttributeError:
            pass
        m.mw.cache_matrices = cache
        if log is not None:
            log(red.summary())
        red.reset()
    return out
