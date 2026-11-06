"""Load the solver libraries once in the background: python -m hertzon.solver.preload

Hertzon starts this right after its window opens. On Windows the first solver process after a restart or an
update read EMerge and its libraries (MKL, gmsh, scipy, the compiled solver) from disk while the virus scanner
checked every one of them: "import emerge" took about 50 s on a laptop instead of about 1 s. This process
does the same imports as a solver process and exits, so the files are cached and checked before the first
Analyze. It runs at low priority, solves nothing and writes nothing.
"""
from __future__ import annotations

import sys


def low_priority() -> None:
    """Below normal priority (Windows) or nice 10, so the preload does not slow down the starting window."""
    try:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00004000)    # BELOW_NORMAL_PRIORITY_CLASS
        else:
            import os
            os.nice(10)
    except Exception:  # noqa: BLE001
        pass


def preload() -> None:
    """The imports of a solver process before it builds the geometry (run.main and Bridge.run)."""
    from . import sparta_setup
    sparta_setup.prefer_mkl()          # the same libraries as the solver: SPARTA then loads MKL
    import emerge                      # noqa: F401
    import gmsh                        # noqa: F401
    from . import bridge               # noqa: F401
    from .emerge_patches import apply
    apply()


def main() -> int:
    low_priority()
    try:
        preload()
        code = 0
    except Exception:  # noqa: BLE001     (the solver process reports any real problem itself)
        code = 1
    import os
    os._exit(code)     # like the solver process: no interpreter shutdown that waits for library threads


if __name__ == "__main__":
    sys.exit(main())
