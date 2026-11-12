"""Parallel sweeps with PARDISO: several frequencies at once, each on a share of the cores.

EMerge solves the frequencies of a parallel sweep in worker threads and allows only solvers that
"release the GIL" there, which leaves SuperLU: much slower than PARDISO and limited to 32 bit fill-in
("Not enough memory to perform factorization" on large models). PARDISO is safe in threads as long as
every thread has its own solver handle (each worker thread gets its own SolveRoutine with its own
PardisoInterface) and the ctypes call releases the GIL. Each worker thread limits MKL to its share of
the cores with mkl_set_num_threads_local, so N workers x (cores / N) threads keep the machine busy
without oversubscription. PARDISO's efficiency drops with many threads on one matrix, so on many
cores a few frequencies at once finish a sweep sooner than one at a time with all cores.

EMerge itself gives every worker thread its own routine and resets it on that thread after each batch
(which frees the PARDISO memory), so nothing else is changed here.
"""
from __future__ import annotations

import ctypes
import threading
import time

WORKER_THREADS = 0      # MKL threads per sweep worker; 0: EMerge's own choice (SuperLU in threads)
_DONE = False
TIMES = {"assembly": 0.0, "solve": 0.0}     # seconds, the solve time summed over the worker threads
_LOCK = threading.Lock()

def _main() -> bool:
    return threading.current_thread() is threading.main_thread()


def _set_threads(lib) -> None:
    """MKL threads of the calling thread only (the C name; the lowercase one is the Fortran entry point
    and takes a pointer)."""
    fn = getattr(lib, "MKL_Set_Num_Threads_Local", None) if lib is not None else None
    if fn is None:
        return
    fn.argtypes = [ctypes.c_int]
    fn.restype = ctypes.c_int
    fn(int(WORKER_THREADS))


def apply() -> None:
    global _DONE
    if _DONE:
        return
    _DONE = True
    from emerge._emerge import solveroutine as R
    from emerge._emerge.solvers import MatrixType, SolverCHOLMOD, SolverPardiso

    pick = R.AutomaticRoutine.pick_solver

    def pick_solver(self, A, b, matrix_type, direct: bool = True):
        if (WORKER_THREADS > 0 and self.parallel == "MT" and direct and A.shape[0] >= 10_000
                and SolverPardiso.available and SolverPardiso in self.solvers
                and not (matrix_type is MatrixType.SPD and SolverCHOLMOD.available)):
            return self.solvers[SolverPardiso]       # this worker thread's own instance
        return pick(self, A, b, matrix_type, direct)

    R.AutomaticRoutine.pick_solver = pick_solver

    solve = SolverPardiso.solve

    def pardiso_solve(self, A, b, precon, id: int = -1):  # noqa: A002
        if WORKER_THREADS > 0 and not _main():
            _set_threads(getattr(self.solver, "libmkl", None))
        t = time.perf_counter()
        try:
            return solve(self, A, b, precon, id)
        finally:
            with _LOCK:
                TIMES["solve"] += time.perf_counter() - t

    pardiso_solve._hertzon_fast = getattr(solve, "_hertzon_fast", False)
    SolverPardiso.solve = pardiso_solve

    from emerge._emerge.physics.microwave.assembly.assembler import Assembler
    assemble = Assembler.assemble_freq_matrix

    def assemble_freq_matrix(self, *a, **k):
        t = time.perf_counter()
        try:
            return assemble(self, *a, **k)
        finally:
            TIMES["assembly"] += time.perf_counter() - t

    assemble_freq_matrix._hertzon_fast = getattr(assemble, "_hertzon_fast", False)
    Assembler.assemble_freq_matrix = assemble_freq_matrix
