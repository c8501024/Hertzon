"""The matrix solvers offered per platform (solver/solver_choice.py): no PARDISO on Apple silicon, Apple Accelerate
only on macOS, and a solver that only needs a package is offered but reported as unavailable."""
import sys

import pytest

from hertzon.model import schema
from hertzon.solver import solver_choice as sc


@pytest.fixture
def mac_arm(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc.platform, "processor", lambda: "arm")
    monkeypatch.setattr(sc.platform, "machine", lambda: "arm64")


@pytest.fixture
def win_x86(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "win32")
    monkeypatch.setattr(sc.platform, "processor", lambda: "Intel64 Family 6")
    monkeypatch.setattr(sc.platform, "machine", lambda: "AMD64")


def test_mac_offers_accelerate_not_pardiso(mac_arm):
    got = sc.offered()
    assert "PARDISO" not in got and not any(s in got for s in ("PARDISO + SPARTA sweep (CPU)",
                                                                "PARDISO + SPARTA sweep (GPU)"))
    assert sc.ACCELERATE in got and "SPARTA (CPU)" in got and got[0] == "Auto" and "SuperLU" in got
    assert "x86-64" in sc.unavailable("PARDISO")


def test_windows_offers_pardiso_not_accelerate(win_x86):
    got = sc.offered()
    assert "PARDISO" in got and sc.ACCELERATE not in got
    assert sc.unavailable("PARDISO") is None


def test_accelerate_needs_its_package(mac_arm, monkeypatch):
    monkeypatch.setattr(sc, "_installed", lambda dist, module=None: dist != "emerge-aasds")
    assert "emerge-aasds" in sc.unavailable(sc.ACCELERATE)
    monkeypatch.setattr(sc, "_installed", lambda dist, module=None: True)
    assert sc.available(sc.ACCELERATE)


def test_sparta_not_installed_is_offered_but_unavailable(win_x86, monkeypatch):
    monkeypatch.setattr(sc.sparta_setup, "installed_version", lambda: None)
    assert "SPARTA (CPU)" in sc.offered()
    assert "Install SPARTA" in sc.unavailable("SPARTA (CPU)")
    assert sc.unavailable("PARDISO + SPARTA sweep (GPU)")


def test_every_schema_solver_is_known():
    assert sc.ACCELERATE in schema.SOLVERS
    for s in schema.SOLVERS:
        sc.unavailable(s)          # no error for any name
    assert sc.available("Auto") and sc.available("SuperLU")


@pytest.mark.skipif(sys.platform != "darwin", reason="the real check on a Mac (CI install-macos)")
def test_real_mac():
    assert sc.available(sc.ACCELERATE), sc.unavailable(sc.ACCELERATE)


def test_auto_on_a_mac(mac_arm, monkeypatch):
    monkeypatch.setattr(sc, "_installed", lambda dist, module=None: True)
    monkeypatch.setattr(sc.sparta_setup, "installed_version", lambda: "0.3.2")
    assert sc.auto_solver() == "SPARTA (CPU)"                       # fastest on a Mac when installed
    monkeypatch.setattr(sc.sparta_setup, "installed_version", lambda: None)
    assert sc.auto_solver() == sc.ACCELERATE and sc.factor_accelerate()
    monkeypatch.setattr(sc, "_installed", lambda dist, module=None: False)
    assert sc.auto_solver() is None and not sc.factor_accelerate()     # nothing fast: EMerge's choice


def test_auto_is_pardiso_on_x86(win_x86, monkeypatch):
    monkeypatch.setattr(sc.sparta_setup, "installed_version", lambda: "0.3.2")
    assert sc.auto_solver() == "PARDISO" and not sc.factor_accelerate()


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Accelerate exists on macOS only (CI install-macos)")
@pytest.mark.parametrize("symmetric", [True, False])
def test_fast_sweep_factor_uses_accelerate_on_a_mac(symmetric):
    import numpy as np
    import scipy.sparse as sp
    from hertzon.solver.fast_sweep import _Factor
    rng = np.random.default_rng(1)
    n = 400
    M = sp.random(n, n, density=0.01, random_state=2) + 1j * sp.random(n, n, density=0.01, random_state=3)
    A = (M + M.T if symmetric else M) + sp.identity(n) * (8 + 1j)
    B = rng.standard_normal((n, 3)) + 1j * rng.standard_normal((n, 3))
    f = _Factor(A.tocsr())
    try:
        assert f.kind == "Accelerate"
        X = f.solve(B)
        assert np.linalg.norm(A @ X - B) <= 1e-10 * np.linalg.norm(B)
    finally:
        f.close()
