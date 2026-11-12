"""PARDISO helpers for EMerge 3 (applied once in the solver worker). EMerge factorizes a complex symmetric
system matrix as LDL^T itself (FennisRobert/EMerge#87) and removes the PEC rows and columns in parallel
(FennisRobert/EMerge#78); Hertzon records the memory of each factorization (FACTOR) and keeps a fast upper
triangle of a symmetric matrix (upper_symmetric).
"""
from __future__ import annotations

import threading

import numpy as np
from numba import njit, prange

_DONE = False
STATS: dict = {}


def _main_thread() -> bool:
    """numba's default (workqueue) threading layer aborts the process when parallel kernels run in two
    threads at once: EMerge's parallel sweeps solve in worker threads, which use the serial *_s copies."""
    return threading.current_thread() is threading.main_thread()


# -- upper triangle of a complex symmetric matrix ----------------------------------------------------------
# The upper triangle (CSR, int32, diagonal always present) of a symmetric matrix in one parallel pass, or None
# when the matrix is not symmetric.

@njit(cache=True, nogil=True, parallel=True)
def _upper_counts(indptr, indices):
    n = indptr.shape[0] - 1
    cnt = np.zeros(n + 1, dtype=np.int64)
    for j in prange(n):
        m = 0
        diag = False
        for k in range(indptr[j], indptr[j + 1]):
            if indices[k] >= j:
                m += 1
                if indices[k] == j:
                    diag = True
        cnt[j + 1] = m if diag else -1
    return cnt


@njit(cache=True, nogil=True)
def _upper_counts_s(indptr, indices):
    n = indptr.shape[0] - 1
    cnt = np.zeros(n + 1, dtype=np.int64)
    for j in range(n):
        m = 0
        diag = False
        for k in range(indptr[j], indptr[j + 1]):
            if indices[k] >= j:
                m += 1
                if indices[k] == j:
                    diag = True
        cnt[j + 1] = m if diag else -1
    return cnt


@njit(cache=True, nogil=True, parallel=True)
def _upper_fill(indptr, indices, data, outptr, outind, outdata):
    for j in prange(indptr.shape[0] - 1):
        p = outptr[j]
        for k in range(indptr[j], indptr[j + 1]):
            if indices[k] >= j:
                outind[p] = indices[k]
                outdata[p] = data[k]
                p += 1


@njit(cache=True, nogil=True)
def _upper_fill_s(indptr, indices, data, outptr, outind, outdata):
    for j in range(indptr.shape[0] - 1):
        p = outptr[j]
        for k in range(indptr[j], indptr[j + 1]):
            if indices[k] >= j:
                outind[p] = indices[k]
                outdata[p] = data[k]
                p += 1


def upper_symmetric(A):
    """Upper triangle (CSR) of a complex symmetric CSC/CSR matrix, or None when that is not safe."""
    from scipy.sparse import csr_matrix
    if (getattr(A, "format", "") not in ("csc", "csr") or A.shape[0] != A.shape[1] or A.dtype != np.complex128
            or not A.has_sorted_indices or A.shape[0] < 2):
        return None
    rng = np.random.default_rng(12345)
    n = A.shape[0]
    x = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    y = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    s1, s2 = y @ (A @ x), x @ (A @ y)
    if not abs(s1 - s2) <= 1e-9 * (abs(s1) + abs(s2)):
        return None
    # the arrays of a symmetric CSC matrix are its CSR arrays: keep column >= row in every row
    par = _main_thread()
    cnt = (_upper_counts if par else _upper_counts_s)(A.indptr, A.indices)
    if np.any(cnt[1:] < 0):           # PARDISO needs every diagonal entry in the pattern
        return None
    outptr = np.cumsum(cnt).astype(np.int32)
    outind = np.empty(outptr[-1], dtype=np.int32)
    outdata = np.empty(outptr[-1], dtype=np.complex128)
    (_upper_fill if par else _upper_fill_s)(A.indptr, A.indices, A.data, outptr, outind, outdata)
    U = csr_matrix((outdata, outind, outptr), shape=A.shape)
    return U


def _pardiso_solve(orig):
    """Records the memory of each PARDISO factorization (FACTOR). EMerge 3 picks LDL^T for a complex symmetric
    matrix itself (FennisRobert/EMerge#87, PardisoInterface.symmetric)."""
    def solve(self, A, b, precon, id: int = -1):  # noqa: A002
        out = orig(self, A, b, precon, id)
        sym = bool(getattr(self.solver, "symmetric", False))
        _record_factor(self.solver, A.shape[0], "complex symmetric (LDLT)" if sym else "unsymmetric (LU)")
        return out
    solve._hertzon_fast = True
    return solve


# memory of the last PARDISO factorization (iparm 15-17, in kB), for the memory check of parallel sweeps
FACTOR: dict = {}


def _record_factor(pardiso, n: int, kind: str) -> None:
    try:
        ip = pardiso.IPARM
        kb = max(int(ip[14]), int(ip[15]) + int(ip[16]))
        if kb > 0:
            FACTOR.update(bytes=kb * 1024.0, unknowns=int(n), kind=kind)
    except Exception:  # noqa: BLE001
        pass


def apply() -> None:
    """Record the PARDISO factorization memory (FACTOR, for the memory check of parallel sweeps)."""
    global _DONE
    if _DONE:
        return
    _DONE = True
    try:
        from emerge._emerge.solvers import SolverPardiso
        if not getattr(SolverPardiso.solve, "_hertzon_fast", False):
            SolverPardiso.solve = _pardiso_solve(SolverPardiso.solve)
        STATS["pardiso"] = "EMerge (LDL^T for symmetric systems)"
    except Exception:  # noqa: BLE001
        STATS["pardiso"] = "unchanged"
