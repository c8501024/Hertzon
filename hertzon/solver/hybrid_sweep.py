"""Interpolating sweep with a reduced basis for the validation points (fewer factorizations).

The interpolating sweep solves a few frequencies directly, fits a rational model and checks it at new
frequencies, until the model holds. Every check frequency used to be a full factorization. Here the
field solutions of the direct solves span a basis V; a check frequency is assembled exactly and first
solved in that basis (Galerkin projection V^T A V y = V^T b, x = V y, as in the fast sweep). Only when
its residual on the full system is above a tenth of the S-parameter tolerance it is factorized
(afterwards, in parallel when switched on) and its solution joins the basis.

The basis values join the fit like direct solutions; the fit needs them, checking the fit with them
without adding them let a fit through too few points pass (hairpin: S error 0.2 at 1 % tolerance).
For complex symmetric A the port quantities are off by r^T A^-1 r, quadratic in the residual r, but
near resonances A^-1 is large. Hairpin filter, 1 % tolerance, S errors against direct solves:
residual up to 1.2e-3: 4e-5, up to 2.5e-3: 2e-3, up to 9e-3: 4e-4. Hence the margin of a tenth.

HERTZON_HYBRID_SWEEP=0 switches it off.
"""
from __future__ import annotations

import threading
import time

import numpy as np

ACTIVE: "Hybrid | None" = None
_DONE = False


class Hybrid:
    def __init__(self, tol: float = 1e-4):
        self.tol = tol
        self.V: np.ndarray | None = None     # orthonormal basis on the solve dofs
        self.ids: np.ndarray | None = None
        self.reduced = False                 # try the reduced basis first (validation rounds)
        self.probe = False                   # reduced only: a frequency the basis cannot reach is not
        self.missed: list[float] = []        # factorized but listed here (solved directly afterwards)
        self._pending = False
        self.lock = threading.Lock()
        self.n_direct = 0
        self.n_reduced = 0
        self.worst = 0.0
        self.t_reduced = 0.0
        self.rejected: list[float] = []      # residuals that were too large (solved directly)

    def add(self, X: np.ndarray) -> None:
        with self.lock:
            for j in range(X.shape[1]):
                v = np.array(X[:, j], dtype=np.complex128)
                n0 = np.linalg.norm(v)
                if n0 == 0:
                    continue
                for _ in range(2):          # Gram-Schmidt twice for a numerically orthonormal basis
                    if self.V is not None and self.V.shape[1]:
                        v -= self.V @ (self.V.conj().T @ v)
                n = np.linalg.norm(v)
                if n <= 1e-10 * n0:
                    continue
                v /= n
                self.V = v[:, None] if self.V is None else np.hstack([self.V, v[:, None]])

    def try_reduced(self, A, b2, solve_ids, symmetric: bool = False):
        """(x on the solve dofs, relative residual) in the basis, or None."""
        if self.V is None or self.ids is None or not np.array_equal(self.ids, solve_ids):
            return None
        from emerge._emerge.mth.csc_submatrix import csc_submatrix as submatrix
        Asel = submatrix(A, solve_ids)
        bsel = np.asarray(b2[solve_ids, :], dtype=np.complex128)
        bn = np.maximum(np.linalg.norm(bsel, axis=0), 1e-300)
        V = self.V
        # Apple's Accelerate BLAS (NumPy on macOS) raises spurious divide/overflow/invalid warnings in matmul
        # with finite results. They are silenced here only: the residual below is computed from the same
        # products, and a result that is really not finite gets an infinite residual and is rejected.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            AV = _matmul(Asel, V, symmetric)
            Ar = V.T @ AV                   # complex symmetric A: V^T keeps the reduced system symmetric
            br = V.T @ bsel
            try:
                y = np.linalg.solve(Ar, br)
            except np.linalg.LinAlgError:
                y = np.linalg.lstsq(AV, bsel, rcond=None)[0]
            r = bsel - AV @ y
            rel = float(np.max(np.linalg.norm(r, axis=0) / bn))
            x = V @ y
        if not (np.isfinite(rel) and np.all(np.isfinite(x))):
            rel = float("inf")
        return x, rel

    def summary(self) -> str:
        r = 0 if self.V is None else self.V.shape[1]
        rej = f", rejected residuals {min(self.rejected):.1e} to {max(self.rejected):.1e}" if self.rejected else ""
        return (f"{self.n_direct} direct solution(s), {self.n_reduced} from the reduced basis of {r} vectors "
                f"(worst residual {self.worst:.1e}{rej}, {self.t_reduced:.1f} s)")


def _matmul(A, V: np.ndarray, symmetric: bool) -> np.ndarray:
    """A @ V on all cores: row blocks of a CSR matrix in threads (scipy releases the GIL). A symmetric
    CSC matrix is its own transpose, read as CSR without a copy."""
    import os
    import scipy.sparse as sp
    from concurrent.futures import ThreadPoolExecutor
    if A.format == "csc" and symmetric:
        A = sp.csr_matrix((A.data, A.indices, A.indptr), shape=A.shape, copy=False)
    elif A.format != "csr":
        A = A.tocsr()
    n = A.shape[0]
    try:
        from . import bridge
        nt = bridge.SOLVER_THREADS or os.cpu_count() or 1
    except Exception:  # noqa: BLE001
        nt = os.cpu_count() or 1
    nt = max(1, min(nt, n // 20000))
    if nt == 1:
        return A @ V
    out = np.empty((n, V.shape[1]), dtype=np.result_type(A.dtype, V.dtype))
    edges = np.linspace(0, n, nt + 1).astype(np.int64)
    ptr, ind, dat = A.indptr, A.indices, A.data

    def block(i):
        a, b = edges[i], edges[i + 1]
        p0, p1 = ptr[a], ptr[b]
        blk = sp.csr_matrix((dat[p0:p1], ind[p0:p1], ptr[a:b + 1] - p0), shape=(b - a, A.shape[1]), copy=False)
        out[a:b] = blk @ V
    with ThreadPoolExecutor(nt) as ex:
        list(ex.map(block, range(nt)))
    return out


def apply() -> None:
    """Wrap SolveRoutine.solve once (class level, so the routines of the worker threads use it too)."""
    global _DONE
    if _DONE:
        return
    _DONE = True
    from emerge._emerge import solveroutine as S
    orig = S.SolveRoutine.solve

    def solve(self, A, b, solve_ids, *a, **kw):
        h = ACTIVE
        if h is None:
            return orig(self, A, b, solve_ids, *a, **kw)
        b2 = b.reshape((b.shape[0], 1)) if b.ndim == 1 else b
        got = None
        if h.reduced and threading.current_thread() is threading.main_thread():
            t = time.time()
            got = h.try_reduced(A, b2, solve_ids, getattr(kw.get("matrix_type"), "symmetric", False))
            if got is not None and got[1] <= h.tol:
                x, rel = got
                sol = np.zeros((A.shape[0], b2.shape[1]), dtype=np.complex128)
                sol[solve_ids, :] = x
                h.n_reduced += 1
                h.worst = max(h.worst, rel)
                h.t_reduced += time.time() - t
                rep = S.SolveReport(solver="Reduced basis", exit_code=0)
                rep.add(interpolating_sweep=f"reduced basis {h.V.shape[1]}, residual {rel:.1e}")
                rep.jobid = kw.get("id", -1)
                rep.simtime = time.time() - t
                rep.nnz = A.nnz
                rep.ndof = b2.shape[0]
                rep.nnz_solve = A.nnz
                rep.ndof_solve = len(solve_ids)
                return sol, rep
            if got is not None:
                h.rejected.append(got[1])
            h.t_reduced += time.time() - t
            if h.probe and got is not None:
                h._pending = True           # SimJob.submit_solution notes the frequency
                sol = np.zeros((A.shape[0], b2.shape[1]), dtype=np.complex128)
                sol[solve_ids, :] = got[0]
                rep = S.SolveReport(solver="Reduced basis", exit_code=0)
                rep.add(interpolating_sweep=f"rejected, residual {got[1]:.1e}")
                rep.jobid = kw.get("id", -1)
                rep.simtime = time.time() - t
                return sol, rep
        sol, rep = orig(self, A, b, solve_ids, *a, **kw)
        ids = np.asarray(solve_ids)
        with h.lock:
            if h.ids is None or not np.array_equal(h.ids, ids):
                h.ids, h.V = ids.copy(), None   # new unknowns: start a new basis
            h.n_direct += 1
        h.add(np.asarray(sol)[ids, :])
        return sol, rep

    solve._hertzon_fast = getattr(orig, "_hertzon_fast", False)
    S.SolveRoutine.solve = solve

    from emerge._emerge.physics.microwave.simjob import SimJob
    submit = SimJob.submit_solution

    def submit_solution(self, *a, **k):
        h = ACTIVE
        if h is not None and h._pending and threading.current_thread() is threading.main_thread():
            h._pending = False
            h.missed.append(float(np.real(self.freq)))
        return submit(self, *a, **k)
    SimJob.submit_solution = submit_solution
