"""Fast sweep: the reduced-basis solver matches direct solves of a parametrized sparse system."""
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver.fast_sweep import ReducedSolver  # noqa: E402


def _system(n=400, seed=1):
    rng = np.random.default_rng(seed)
    main = 2.0 + rng.random(n)
    K = sp.diags([main, -np.ones(n - 1), -np.ones(n - 1)], [0, -1, 1]).tocsc()   # stiffness-like
    M = sp.diags(0.5 + 0.1 * rng.random(n)).tocsc()                              # mass-like
    C = sp.diags(np.r_[np.zeros(n - 5), np.ones(5)]).tocsc()                     # absorbing boundary
    b = np.zeros((n + 2, 2), complex)
    b[3, 0] = 1.0
    b[n - 4, 1] = 1.0
    return K, M, C, b


def test_reduced_matches_direct():
    K, M, C, b = _system()
    n = K.shape[0]
    ids = np.arange(n)
    pad = lambda A: sp.block_diag([A, sp.identity(2)]).tocsc()   # two prescribed dofs, as PEC edges  # noqa: E731
    calls = []

    def direct(A, bb, solve_ids, **kw):
        from scipy.sparse.linalg import spsolve
        calls.append(1)
        x = np.zeros_like(bb)
        x[solve_ids] = spsolve(A[:, solve_ids][solve_ids, :].tocsc(), bb[solve_ids]).reshape(-1, bb.shape[1])
        from emerge._emerge.solvers import SolveReport
        return x, SolveReport()

    red = ReducedSolver(direct, tol=1e-8)
    freqs = np.linspace(0.6, 0.9, 61)
    order = sorted(freqs, key=lambda f: abs(f - 0.75))
    worst = 0.0
    for f in order:
        A = pad(K - f ** 2 * M + 1j * f * C)
        x, _ = red.solve(A, b, ids)
        xd, _ = direct(A, b, ids)
        worst = max(worst, np.abs(x - xd).max() / np.abs(xd).max())
    assert worst < 1e-6
    assert red.n_direct < 10 and red.n_reduced > 50      # far fewer factorizations than the 61 frequencies


def test_basis_stays_within_memory_and_still_matches_direct():
    """A basis that would outgrow the memory starts again from the current solution instead of growing with
    ports x enrichments x frequencies (Rotman lens, 11 ports: ended by the operating system); the solutions
    still meet the tolerance."""
    K, M, C, b = _system()
    n = K.shape[0]
    ids = np.arange(n)
    pad = lambda A: sp.block_diag([A, sp.identity(2)]).tocsc()  # noqa: E731

    def direct(A, bb, solve_ids, **kw):
        from scipy.sparse.linalg import spsolve
        x = np.zeros_like(bb)
        x[solve_ids] = spsolve(A[:, solve_ids][solve_ids, :].tocsc(), bb[solve_ids]).reshape(-1, bb.shape[1])
        from emerge._emerge.solvers import SolveReport
        return x, SolveReport()

    freqs = np.linspace(0.6, 0.9, 61)
    # room for 12 basis vectors next to the kept solutions of all frequencies
    keep = 16.0 * 2 * (2 * (n + 2) * (len(freqs) + 1) + 6 * n)
    red = ReducedSolver(direct, tol=1e-8, n_freqs=len(freqs), room=lambda: (keep + 12 * 2.5 * 16.0 * n) / 0.85)
    worst, largest = 0.0, 0
    for f in sorted(freqs, key=lambda f: abs(f - 0.75)):
        A = pad(K - f ** 2 * M + 1j * f * C)
        x, _ = red.solve(A, b, ids)
        largest = max(largest, red.V.shape[1])
        xd, _ = direct(A, b, ids)
        worst = max(worst, np.abs(x - xd).max() / np.abs(xd).max())
    assert red.max_basis == 12
    assert red.n_restart > 0 and largest <= 12
    assert worst < 1e-6


def test_no_memory_limit_without_a_room_estimate():
    red = ReducedSolver(lambda *a, **k: None, room=lambda: float("inf"))
    red._set_max_basis(1000, 2, 1002)
    assert red.max_basis is None


def test_parallel_sparse_product_matches_scipy():
    from hertzon.solver.fast_sweep import spmm
    rng = np.random.default_rng(3)
    A = (sp.random(500, 500, density=0.02, random_state=4) + 1j * sp.random(500, 500, density=0.02,
                                                                             random_state=5)).tocsr()
    X = np.asfortranarray(rng.standard_normal((500, 70)) + 1j * rng.standard_normal((500, 70)))
    out = np.empty((500, 90), dtype=np.complex128, order="F")
    Y = spmm(A, X[:, 5:], out=out[:, 10:75])
    assert np.allclose(Y, A @ X[:, 5:]) and np.allclose(out[:, 10:75], A @ X[:, 5:])


def test_block_orthonormalization_drops_known_directions():
    red = ReducedSolver(lambda *a, **k: None)
    rng = np.random.default_rng(7)
    X = rng.standard_normal((300, 6)) + 1j * rng.standard_normal((300, 6))
    assert red._add(X) == 6
    Y = np.hstack([X[:, :2] @ np.array([[1.0], [2.0]]), rng.standard_normal((300, 3)) + 0j])
    assert red._add(Y) == 3                  # the combination of known vectors adds nothing
    V = red.V
    assert np.allclose(V.conj().T @ V, np.eye(9), atol=1e-12)
