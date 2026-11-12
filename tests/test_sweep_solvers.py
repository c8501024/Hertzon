import sys
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _family(m=40):
    """A(f) = K - f^2 M + i f C: a complex symmetric, frequency dependent sparse system like the FEM one."""
    T = sp.diags([-1.0, 2.0, -1.0], [-1, 0, 1], shape=(m, m))
    I = sp.identity(m)
    K = (sp.kron(I, T) + sp.kron(T, I)).tocsr()
    n = K.shape[0]
    M = sp.diags(np.linspace(0.5, 1.5, n)).tocsr()
    C = sp.diags(np.r_[np.ones(m), np.zeros(n - m)]).tocsr()
    b = np.zeros((n, 1), complex)
    b[: m, 0] = 1.0
    return (lambda f: (K - f * f * M + 1j * f * C).tocsr()), b


def test_reduced_basis_reaches_frequencies_between_direct_solutions():
    from hertzon.solver.hybrid_sweep import Hybrid
    A, b = _family()
    n = b.shape[0]
    ids = np.arange(n)
    h = Hybrid(tol=1e-6)
    h.ids = ids
    import scipy.sparse.linalg as sla
    for f in np.linspace(0.01, 0.08, 6):          # below the first resonance, like a band between samples
        h.add(sla.spsolve(A(f).tocsc(), b)[:, None])
    f = 0.047
    x, rel = h.try_reduced(A(f), b, ids)
    ref = sla.spsolve(A(f).tocsc(), b)
    assert rel < 1e-6 and np.abs(x[:, 0] - ref).max() < 1e-5 * np.abs(ref).max()
    _, rel_far = h.try_reduced(A(2.0), b, ids)          # far outside the band: rejected, solved directly
    assert rel_far > 1e-6
    assert h.try_reduced(A(f), b, ids[:-1]) is None      # other unknowns: no basis


def test_reduced_basis_port_error_is_quadratic_in_the_residual():
    """Why the interpolating sweep may accept a residual as large as its S tolerance: b^T x (the port
    quantities) of the Galerkin solution is off by r^T A^-1 r only."""
    import scipy.sparse.linalg as sla
    from hertzon.solver.hybrid_sweep import Hybrid
    A, b = _family()
    ids = np.arange(b.shape[0])
    h = Hybrid()
    h.ids = ids
    for f in np.linspace(0.01, 0.08, 3):
        h.add(sla.spsolve(A(f).tocsc(), b)[:, None])
    for f in (0.03, 0.047, 0.07):
        x, rel = h.try_reduced(A(f), b, ids)
        ref = sla.spsolve(A(f).tocsc(), b)
        port = abs(b[:, 0] @ (x[:, 0] - ref)) / abs(b[:, 0] @ ref)
        assert 1e-5 < rel < 1e-2 and port < 10 * rel ** 2


def test_threaded_matmul_matches_scipy(monkeypatch):
    from hertzon.solver import bridge
    from hertzon.solver.hybrid_sweep import _matmul
    monkeypatch.setattr(bridge, "SOLVER_THREADS", 4)
    A, _ = _family(300)                      # 90000 unknowns: 4 row blocks
    S = A(0.2).tocsc()
    rng = np.random.default_rng(2)
    V = rng.standard_normal((S.shape[0], 3)) + 1j * rng.standard_normal((S.shape[0], 3))
    assert np.allclose(_matmul(S, V, True), S @ V)
    U = (S + sp.diags([0.3], [1], shape=S.shape)).tocsc()     # not symmetric
    assert np.allclose(_matmul(U, V, False), U @ V)


def test_fast_sweep_factor_symmetric_block_solve():
    try:
        from hertzon.solver import emerge_patches
        emerge_patches.apply()
        from emerge._emerge.solvers.pardiso import PardisoInterface
        PardisoInterface()
    except Exception:  # noqa: BLE001
        pytest.skip("PARDISO not available")
    from hertzon.solver.fast_sweep import _Factor
    A, _ = _family(60)
    M = A(0.2)
    M.sort_indices()
    rng = np.random.default_rng(1)
    B = rng.standard_normal((M.shape[0], 4)) + 1j * rng.standard_normal((M.shape[0], 4))
    f = _Factor(M)
    assert f.kind == "LDL^T"
    X = f.solve(B)
    assert np.abs(M @ X - B).max() < 1e-9 * np.abs(B).max()
    assert np.allclose(f.solve(B[:, 1])[:, 0], X[:, 1])
    U = (M + sp.diags([0.3, 0.1], [1, -1], shape=M.shape)).tocsr()     # not symmetric: LU
    U.sort_indices()
    g = _Factor(U)
    assert g.kind == "LU" and np.abs(U @ g.solve(B) - B).max() < 1e-9 * np.abs(B).max()
    f.close()
    g.close()


def test_parallel_workers_use_the_factor_size(monkeypatch):
    from hertzon.solver import bridge, fast_assembly

    class _M:
        class mesh:
            n_tets = 10000
    monkeypatch.setattr(bridge, "SOLVER_THREADS", 32)
    monkeypatch.setattr(bridge, "available_memory", lambda: 64 * 2 ** 30)
    monkeypatch.setattr(bridge, "_current_rss", lambda: 4 * 2 ** 30)
    monkeypatch.setitem(fast_assembly.FACTOR, "bytes", 10 * 2 ** 30)
    # 0.85 * 64 - 4 = 50.4 GB free, 13 GB per frequency: 3 at a time
    assert bridge._parallel_workers(_M, 8, 8) == 3
    assert bridge._parallel_workers(_M, 2, 8) == 2
    monkeypatch.setitem(fast_assembly.FACTOR, "bytes", 100 * 2 ** 30)
    assert bridge._parallel_workers(_M, 8, 8) == 1


def test_memory_forecast_grows_faster_than_the_mesh(monkeypatch):
    from hertzon.solver import bridge
    monkeypatch.setattr(bridge, "_peak_rss", lambda: 10e9)
    same, _ = bridge.memory_forecast(1e5, 1e5)
    double, _ = bridge.memory_forecast(2e5, 1e5)
    assert np.isclose(same, 10e9) and np.isclose(double, 10e9 * 2 ** (4 / 3))
