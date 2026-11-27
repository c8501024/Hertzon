"""Which matrix solvers of the setup (schema.SOLVERS) can run on this computer, without importing EMerge.

A solver that cannot exist on this platform (PARDISO without an x86-64 processor, Apple Accelerate outside macOS)
is not offered at all; one that only needs a package (SPARTA, MUMPS, UMFPACK) is offered but greyed out in the
solver benchmark with the reason, instead of a run falling back to the automatic choice under its name."""
from __future__ import annotations

import importlib.util
import platform
import sys
from importlib.metadata import PackageNotFoundError, version

from . import sparta_setup

ACCELERATE = "Apple Accelerate"     # EMerge's AASDS: Apple's sparse direct solver (macOS, package emerge-aasds)


def _installed(dist: str, module: str | None = None) -> bool:
    try:
        version(dist)
        return True
    except PackageNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        return False
    try:
        return module is not None and importlib.util.find_spec(module) is not None
    except Exception:  # noqa: BLE001
        return False


def _arm() -> bool:
    return "arm" in platform.processor().lower() or platform.machine().lower() in ("arm64", "aarch64")


def _pardiso() -> str | None:
    # EMerge's PARDISO is the one of Intel MKL, which exists for x86-64 only (EMerge: "arm" not in processor)
    if _arm():
        return ("PARDISO needs Intel MKL, which exists for x86-64 processors only"
                + (f"; on this Mac use {ACCELERATE} or SPARTA" if sys.platform == "darwin" else ""))
    return None


def _sparta() -> str | None:
    return None if sparta_setup.installed_version() else \
        "SPARTA is not installed (Tools > Options > Install SPARTA)"


def unavailable(name: str) -> str | None:
    """Why the solver `name` cannot run here, None when it can ("Auto" and SuperLU always can)."""
    if name in ("Auto", "SuperLU", ""):
        return None
    if name == "PARDISO":
        return _pardiso()
    if sparta_setup.is_sparta(name):
        return _sparta()
    if name in sparta_setup.SWEEP_ONLY:
        return _pardiso() or _sparta()
    if name == ACCELERATE:
        if sys.platform != "darwin":
            return f"{ACCELERATE} exists on macOS only"
        return None if _installed("emerge-aasds", "emerge_aasds") else \
            f"{ACCELERATE} needs the package emerge-aasds (pip install emerge-aasds)"
    if name == "MUMPS":
        return None if _installed("mumps-emerge", "mumps_emerge") else "MUMPS needs the package mumps_emerge"
    if name == "UMFPACK":
        return None if _installed("scikit-umfpack") else "UMFPACK needs the package scikit-umfpack"
    return None


def impossible(name: str) -> bool:
    """The solver cannot run on this platform whatever is installed: it is not offered."""
    if name == "PARDISO" or name in sparta_setup.SWEEP_ONLY:
        return _arm()
    if name == ACCELERATE:
        return sys.platform != "darwin"
    return False


def offered(solvers=None) -> list[str]:
    """The setup's solvers (schema.SOLVERS) this platform can run, in their order."""
    if solvers is None:
        from ..model import schema
        solvers = schema.SOLVERS
    return [s for s in solvers if not impossible(s)]


def available(name: str) -> bool:
    return unavailable(name) is None


def auto_solver() -> str | None:
    """The solver "Auto" stands for on this computer, for every matrix size; None leaves the choice to EMerge.

    The fastest direct solver the platform has: PARDISO on an x86-64 processor; on a Mac SPARTA when it is
    installed (on the CPU, in double precision: measured two to three times faster than Apple Accelerate
    there), otherwise Apple Accelerate.
    EMerge's own automatic choice takes SuperLU below 10,000 unknowns, which is never faster (Windows: the same
    time as PARDISO; Mac: up to 25 % slower than Accelerate), and on a Mac without SPARTA it prefers nothing
    over SuperLU in the Fast sweep. The caller checks that EMerge can load the solver."""
    if sys.platform == "darwin":
        if _sparta() is None:
            return "SPARTA (CPU)"            # double precision; the GPU mode is single precision (|dS| up to 0.4)
        if available(ACCELERATE):
            return ACCELERATE
        return None
    if _pardiso() is None:
        return "PARDISO"
    return None


def factor_accelerate() -> bool:
    """Fast sweep and tuning factorize with Apple Accelerate when PARDISO is missing (a Mac), instead of SuperLU."""
    return sys.platform == "darwin" and available(ACCELERATE)
