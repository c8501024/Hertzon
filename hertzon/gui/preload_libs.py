"""Load the libraries of the main window once, without a window: python -m hertzon.gui.preload_libs

Run by python -m hertzon.solver.warmup at the end of every installation and update. On the first load of a
new library the virus scanner (Windows) or the system's code check (macOS) reads it, which for the Qt and
VTK libraries of the main window took from a few minutes up to 20 minutes on some machines (issue #474).
The solver warm-up loads only the solver's libraries, so before this the first start of Hertzon paid for the
rest, behind a loading screen that seemed to hang. Here it happens in the setup window, which says so.
"""
from __future__ import annotations

import os
import sys
import time


def load(log=print) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # no window, also without a display
    t0 = time.monotonic()
    from .app import _heavy_imports, _material_library, _web_engine_setup
    _web_engine_setup()                     # Qt WebEngine (the formula editor), the largest Qt library
    stage: dict = {}
    _heavy_imports(stage)
    _material_library()
    import matplotlib.font_manager  # noqa: F401   (builds matplotlib's font list on its first use)
    log(f"User interface libraries loaded in {time.monotonic() - t0:.0f} s")


def main() -> int:
    try:
        load()
    except Exception as e:  # noqa: BLE001   (the first start then loads them itself)
        print(f"User interface libraries could not be loaded now: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
