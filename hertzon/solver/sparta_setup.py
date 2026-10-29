"""SPARTA, the sparse direct solver of EMerge's author, as a matrix solver of Hertzon.

SPARTA (https://github.com/FennisRobert/SPARTA, AGPL-3.0) factors the FEM matrix on the GPU (Metal on
Apple silicon, Vulkan on AMD and NVIDIA under Windows and Linux) in single precision, or on the CPU in
double precision. EMerge has it as EMSolver.SPARTA with the modes 'GPU', 'CPU' and 'HYBRID'
(SolverSparta.set_options); Hertzon offers 'GPU' and 'CPU' as "SPARTA (GPU)" and "SPARTA (CPU)".

It is not one of Hertzon's own packages: Tools > Options installs it on request from the wheels of a
SPARTA release (install_args). The solver process imports it through EMerge, the GUI only asks pip's
metadata (installed_version), so that the GUI never loads a GPU driver.
"""
from __future__ import annotations

import platform
import sys

VERSION = "0.3.2"
_RELEASE = f"https://github.com/FennisRobert/SPARTA/releases/download/v{VERSION}/sparta_solver-{VERSION}-cp39-abi3-"
WHEELS = {     # (sys.platform, machine) -> platform tag of the release wheel (one abi3 wheel per platform)
    ("win32", "amd64"): "win_amd64",
    ("linux", "x86_64"): "manylinux_2_17_x86_64.manylinux2014_x86_64",
    ("linux", "aarch64"): "manylinux_2_17_aarch64.manylinux2014_aarch64",
    ("darwin", "arm64"): "macosx_11_0_arm64",
    ("darwin", "x86_64"): "macosx_10_12_x86_64",
}
SOURCE = f"git+https://github.com/FennisRobert/SPARTA@v{VERSION}"     # other platforms: needs Rust

CHOICES = ("SPARTA (GPU)", "SPARTA (CPU)")
MODES = {"SPARTA (GPU)": "GPU", "SPARTA (CPU)": "CPU"}


def is_sparta(name: str) -> bool:
    return name in MODES


def requirement(plat: str | None = None, machine: str | None = None) -> str:
    """What pip installs: the release wheel for this platform, otherwise the release tag from source."""
    plat = plat or sys.platform
    machine = (machine or platform.machine()).lower()
    tag = WHEELS.get((plat, machine))
    return f"{_RELEASE}{tag}.whl" if tag else SOURCE


def install_args(python: str | None = None) -> list[str]:
    """Command line that installs (or updates to) the SPARTA release Hertzon is tested with."""
    return [python or sys.executable, "-m", "pip", "install", "--upgrade", requirement()]


def installed_version() -> str | None:
    """Version of the installed sparta-solver package, None when it is not installed (without importing it)."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("sparta-solver")
        except PackageNotFoundError:
            return None
    except Exception:  # noqa: BLE001
        return None


def mkl_library() -> str | None:
    """MKL's runtime library of this Python environment (it comes with PARDISO), None without one."""
    import glob
    import os
    if sys.platform == "win32":
        pats = [os.path.join(sys.prefix, "Library", "bin", "mkl_rt*.dll")]
    elif sys.platform == "darwin":
        pats = [os.path.join(sys.prefix, "lib", "libmkl_rt*.dylib")]
    else:
        pats = [os.path.join(sys.prefix, "lib", "libmkl_rt.so*")]
    found = sorted(f for p in pats for f in glob.glob(p))
    return found[-1] if found else None


def prefer_mkl() -> None:
    """Let SPARTA's CPU solver use MKL instead of the OpenBLAS of scipy-openblas32. MKL sets its thread count
    per thread, which SPARTA uses to run BLAS inside its own thread pool; OpenBLAS has only a process-wide
    setting, and with many cores (64 on a Threadripper) its concurrent calls overflow its precompiled thread
    table ("precompiled NUM_THREADS exceeded") and the solver process crashes. SPARTA reads SPARTA_BLAS when
    it is imported, so this runs before EMerge is imported. A SPARTA_BLAS set by the user is kept."""
    import os
    if sys.platform == "darwin" or os.environ.get("SPARTA_BLAS"):
        return               # Apple: SPARTA links Accelerate
    lib = mkl_library()
    if lib:
        os.environ["SPARTA_BLAS"] = lib


def configure(solver, mode: str, symmetric: bool = True) -> str:
    """Set an EMerge SolverSparta to `mode` ('GPU' or 'CPU'); returns the mode it got. Without a usable GPU
    in this process it falls back to the CPU. A matrix that is not complex symmetric (periodic cells) is
    factored with LU instead of LDL^T."""
    if mode == "GPU":
        try:
            import sparta
            if not sparta.gpu_available():
                mode = "CPU"
        except Exception:  # noqa: BLE001
            mode = "CPU"
    solver.set_options(mode)
    solver.set_symmetry(symmetric)
    return mode


def describe() -> str:
    """'SPARTA 0.3.2, GPU: yes, BLAS: ...' of the package this process imports (solver log)."""
    try:
        import sparta
        gpu = "yes" if sparta.gpu_available() else "no"
        blas = sparta.blas_library() if hasattr(sparta, "blas_library") else "?"
        return f"SPARTA {sparta.__version__}, GPU available: {gpu}, BLAS: {blas}"
    except Exception as ex:  # noqa: BLE001
        return f"SPARTA not available ({ex})"
