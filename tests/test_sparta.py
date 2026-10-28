import sys
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import sparta_setup  # noqa: E402


class _Solver:
    """Records what configure() sets, like EMerge's SolverSparta."""
    def set_options(self, mode):
        self.mode = mode
        return self

    def set_symmetry(self, sym):
        self.sym = sym


def test_choices_reach_the_setup_and_options():
    from hertzon.model import schema
    solver = next(p for p in schema.SETUP if p.name == "Solver")
    for name in sparta_setup.CHOICES:
        assert name in schema.SOLVERS and name in solver.choices and sparta_setup.is_sparta(name)
    assert not sparta_setup.is_sparta("PARDISO") and not sparta_setup.is_sparta("Auto")


def test_release_wheel_per_platform_and_source_elsewhere():
    v = sparta_setup.VERSION
    win = sparta_setup.requirement("win32", "AMD64")
    assert win.endswith(f"sparta_solver-{v}-cp39-abi3-win_amd64.whl") and f"/v{v}/" in win
    assert sparta_setup.requirement("linux", "x86_64").endswith("manylinux2014_x86_64.whl")
    assert sparta_setup.requirement("darwin", "arm64").endswith("macosx_11_0_arm64.whl")
    assert sparta_setup.requirement("win32", "ARM64") == sparta_setup.SOURCE
    args = sparta_setup.install_args("py")
    assert args[:4] == ["py", "-m", "pip", "install"] and args[-1] == sparta_setup.requirement()


def test_gpu_without_a_usable_gpu_factorizes_on_the_cpu(monkeypatch):
    fake = type(sys)("sparta")
    fake.gpu_available = lambda: False
    monkeypatch.setitem(sys.modules, "sparta", fake)
    s = _Solver()
    assert sparta_setup.configure(s, "GPU", symmetric=False) == "CPU"
    assert s.mode == "CPU" and s.sym is False
    fake.gpu_available = lambda: True
    assert sparta_setup.configure(s, "GPU") == "GPU" and s.mode == "GPU" and s.sym is True
    assert sparta_setup.configure(s, "CPU") == "CPU"


def _emerge_sparta():
    try:
        from emerge._emerge import solver as S
    except Exception:  # noqa: BLE001
        pytest.skip("EMerge not importable")
    if not S._SPARTA_AVAILABLE:
        pytest.skip("SPARTA not installed")
    return S


@pytest.mark.parametrize("symmetric", [True, False])
def test_emerge_sparta_cpu_solves_symmetric_and_periodic_matrices(symmetric):
    """The FEM matrix is complex symmetric (LDL^T); a periodic cell's is only structurally symmetric (LU)."""
    S = _emerge_sparta()
    m = 60
    T = sp.diags([-1.0, 4.0, -1.0], [-1, 0, 1], shape=(m, m))
    A = (sp.kron(sp.identity(m), T) + sp.kron(sp.diags([-1.0, -1.0], [-1, 1], shape=(m, m)), sp.identity(m)))
    A = (A + 0.3j * sp.identity(m * m)).tocsc().astype(complex)
    if not symmetric:
        U = sp.triu(A, 1).tocsc()
        A = (A + 0.2j * U - 0.2j * U.T).tocsc()        # same pattern, A != A^T
        assert abs(A - A.T).max() > 0.1
    b = np.ones((m * m, 1), complex)
    solver = S.SolverSparta("")
    assert sparta_setup.configure(solver, "CPU", symmetric=symmetric) == "CPU"
    solver.initialize()
    x, report = solver.solve(A, b, None)
    solver.reset()
    assert report.exit_code == 0
    assert np.linalg.norm(A @ x - b) / np.linalg.norm(b) < 1e-9
