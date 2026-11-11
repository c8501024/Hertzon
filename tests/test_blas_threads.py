"""The window and the MCP processes load numpy's and scipy's OpenBLAS with few threads (each thread reserves a
work buffer, about 1 GB committed memory on 16 cores), and processes they start get the user's environment."""
import os
import subprocess
import sys

CODE = r"""
import ctypes, glob, os, sys
from hertzon.blas_threads import load_with_few_threads
load_with_few_threads(2)
assert "OPENBLAS_NUM_THREADS" not in os.environ, "the environment of later processes stays as it was"
import numpy, scipy
def threads(pkg, fn):
    for lib in glob.glob(os.path.join(os.path.dirname(pkg.__file__) + ".libs", "*openblas*")):
        f = getattr(ctypes.CDLL(lib), fn, None)
        if f is not None:
            return f()
    return None   # another BLAS build (the test then checks the environment only)
print(threads(numpy, "scipy_openblas_get_num_threads64_"), threads(scipy, "scipy_openblas_get_num_threads"))
"""


def _run(env):
    out = subprocess.run([sys.executable, "-c", CODE], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return [None if v == "None" else int(v) for v in out.stdout.split()]


def test_openblas_loads_with_two_threads_and_the_environment_is_restored():
    env = {k: v for k, v in os.environ.items() if k not in ("OPENBLAS_NUM_THREADS", "HERTZON_BLAS_THREADS")}
    for n in _run(env):
        assert n is None or n <= 2


def test_a_thread_count_of_the_user_is_kept():
    env = {k: v for k, v in os.environ.items() if k != "HERTZON_BLAS_THREADS"}
    env["OPENBLAS_NUM_THREADS"] = "1"
    out = subprocess.run([sys.executable, "-c", "import os; from hertzon.blas_threads import load_with_few_threads;"
                          "load_with_few_threads(2); print(os.environ['OPENBLAS_NUM_THREADS'])"],
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "1", out.stderr
