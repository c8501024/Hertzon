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
