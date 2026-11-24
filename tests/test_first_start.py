"""First start after an installation (issue #474): the loading screen says what it waits for, the main window
loads only the VTK modules it uses, and the installation loads the user interface libraries once."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_loading_screen_explains_a_long_first_start():
    from hertzon.gui.app import SLOW_START_HINT, SLOW_START_S, loading_text
    assert loading_text("Loading the 3D viewer (VTK) ...", 1.0) == "Loading the 3D viewer (VTK) ..."
    assert loading_text("Loading the 3D viewer (VTK) ...", 7.4) == "Loading the 3D viewer (VTK) ...  7 s"
    slow = loading_text("Loading the 3D viewer (VTK) ...", SLOW_START_S + 100)
    assert SLOW_START_HINT in slow and slow.endswith("120 s")


def test_vtk_classes_cover_the_viewer_and_field_plots():
    from hertzon.gui import vtk_classes
    used = set()
    for f in ("viewer.py", "fields.py"):
        src = (ROOT / "hertzon" / "gui" / f).read_text(encoding="utf-8")
        assert re.search(r"^import vtk\b", src, re.M) is None, f"{f} loads every VTK module again"
        used |= set(re.findall(r"\bvtk\.(\w+)", src))
    missing = sorted(n for n in used if not hasattr(vtk_classes, n))
    assert not missing, f"add to hertzon/gui/vtk_classes.py: {missing}"


def _env():
    return dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT), HERTZON_NO_PRELOAD="1")


def test_main_window_does_not_load_every_vtk_module():
    code = ("import sys, hertzon.gui.main_window\n"
            "print(int('vtkmodules.all' in sys.modules or 'vtk' in sys.modules),"
            " len([m for m in sys.modules if m.startswith('vtkmodules.vtk')]))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=_env(), cwd=str(ROOT),
                       timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    loads_all, n = map(int, r.stdout.split()[-2:])
    assert not loads_all
    assert n < 60, f"{n} VTK modules loaded (all of them are about 150)"


def test_preload_libs_loads_the_user_interface_without_a_window():
    r = subprocess.run([sys.executable, "-m", "hertzon.gui.preload_libs"], capture_output=True, text=True,
                       env=_env(), cwd=str(ROOT), timeout=900, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    assert "User interface libraries loaded" in r.stdout


def test_warmup_loads_the_user_interface_also_when_the_kernels_are_current(monkeypatch):
    from hertzon.solver import bridge, warmup
    calls = []
    monkeypatch.setattr(warmup, "ui_libraries", lambda *a, **k: calls.append(1) or True)
    monkeypatch.setattr(bridge, "_first_run", lambda: False)
    monkeypatch.setattr(warmup, "_done", lambda state: None)
    monkeypatch.setattr(sys, "argv", ["warmup"])
    assert warmup.main() == 0
    assert calls == [1]
    monkeypatch.setattr(sys, "argv", ["warmup", "--no-ui"])     # the Docker image of the cloud solver
    assert warmup.main() == 0
    assert calls == [1]
