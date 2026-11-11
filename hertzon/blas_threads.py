"""Fewer threads for the linear algebra libraries of the window and of the MCP processes.

numpy, scipy and gmsh each bring their own OpenBLAS. When it is loaded, every copy reserves a work buffer for
each thread it will use, and by default it uses one thread per logical CPU. On Windows this memory is
committed at once: on a laptop with 16 logical CPUs the three copies took about 1.2 GB of the 1.8 GB that an
empty Hertzon window had committed (Task Manager, Details, "Commit size"), although the window never does
large matrix algebra (the solver runs in processes of its own).

load_with_few_threads() loads the three libraries with OPENBLAS_NUM_THREADS set to a small number and then
puts the environment back as it was: OpenBLAS reads the variable once when it is loaded, and processes started
later (the solver, the solver preload) get the environment of the user unchanged and use every core.
"""
from __future__ import annotations

import os
import sys

THREADS = 2          # enough for the small matrices of the window (3x3 frames, circuits, plots)
_KEY = "OPENBLAS_NUM_THREADS"


def load_with_few_threads(threads: int = THREADS) -> None:
    """Import numpy, scipy.linalg and gmsh with at most `threads` OpenBLAS threads (libraries that are
    already loaded keep their threads). HERTZON_BLAS_THREADS=0 switches it off."""
    want = os.environ.get("HERTZON_BLAS_THREADS")
    if want is not None:
        try:
            threads = int(want)
        except ValueError:
            pass
    if threads <= 0 or _KEY in os.environ:     # switched off, or the user chose a number
        _import_all()
        return
    os.environ[_KEY] = str(threads)
    try:
        _import_all()
    finally:
        os.environ.pop(_KEY, None)


def _import_all() -> None:
    for name in ("numpy", "scipy.linalg", "gmsh"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except Exception:  # noqa: BLE001   (a missing library is reported where it is used)
            pass
