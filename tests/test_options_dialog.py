"""Tools > Options opens at once: what waits for the network or the disk runs in the background."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace


def test_options_dialog_opens_without_waiting(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path))
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from hertzon.gui.dialogs import OptionsDialog
    from hertzon.remote import local
    from hertzon.solver import sparta_setup
    app = QApplication.instance() or QApplication([])
    # slow as on Windows: a refused connection to this computer takes about 2 s, host name lookups can hang
    monkeypatch.setattr(local, "status", lambda: (time.sleep(1.0), None)[1])
    monkeypatch.setattr(local, "share_addresses", lambda: (time.sleep(1.0), ["here:47820"])[1])
    monkeypatch.setattr(sparta_setup, "installed_version", lambda: (time.sleep(1.0), None)[1])
    win = SimpleNamespace(settings=QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat),
                          edit_new_primitives=False, doc=SimpleNamespace(display_quality="normal"),
                          solver=SimpleNamespace(show_emerge_log=False))
    t = time.perf_counter()
    dlg = OptionsDialog(win)
    assert time.perf_counter() - t < 0.5
    assert dlg.share_state.text() == "Checking ..." and not dlg.share_btn.isEnabled()
    end = time.monotonic() + 10
    while time.monotonic() < end and "Checking ..." in (dlg.share_state.text(), dlg.sparta_state.text()):
        app.processEvents()
        time.sleep(0.01)
    assert dlg.share_state.text() == "Not running" and dlg.share_btn.text() == "Start Service"
    assert dlg.share_btn.isEnabled() and dlg.share_addr.text() == "here:47820"
    assert dlg.sparta_state.text() == "Not installed" and dlg.sparta_install.isEnabled()


def test_solver_installed_while_hertzon_runs_needs_no_restart(tmp_path, monkeypatch):
    """Issue #536: SPARTA installed in Tools > Options can be chosen in the open Solver Benchmark at once."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path))
    from PySide6.QtWidgets import QApplication, QWidget

    from hertzon.gui.benchmark import COMPARE, BenchmarkDialog
    from hertzon.solver import sparta_setup
    app = QApplication.instance() or QApplication([])
    assert "SPARTA (CPU)" in COMPARE
    installed = {"v": None}
    monkeypatch.setattr(sparta_setup, "installed_version", lambda: installed["v"])
    win = QWidget()
    win.doc = SimpleNamespace(project=SimpleNamespace(path=None))
    win.solver = SimpleNamespace(busy=False)
    dlg = BenchmarkDialog(win)
    box = dlg.solver_boxes["SPARTA (CPU)"]
    assert not box.isEnabled() and not box.isChecked() and "not installed" in box.toolTip()
    installed["v"] = sparta_setup.VERSION          # what pip of "Install SPARTA" did meanwhile
    dlg.refresh_solvers()
    assert box.isEnabled() and box.isChecked() and box.toolTip() == ""
    installed["v"] = None                          # and back: shown again, the dialog asks again
    dlg.show()
    app.processEvents()
    assert not box.isEnabled() and not box.isChecked()
    dlg.close()


def test_install_sparta_refreshes_the_open_solver_benchmark(tmp_path, monkeypatch):
    """After "Install SPARTA" the benchmark of this window lists SPARTA as available without a restart."""
    import sys
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path))
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QWidget

    from hertzon.gui.benchmark import BenchmarkDialog
    from hertzon.gui.dialogs import OptionsDialog
    from hertzon.remote import local
    from hertzon.solver import sparta_setup
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(local, "status", lambda: None)
    monkeypatch.setattr(local, "share_addresses", lambda: [])
    installed = {"v": None}
    monkeypatch.setattr(sparta_setup, "installed_version", lambda: installed["v"])
    # stands in for pip: the package is there once the process has finished
    monkeypatch.setattr(sparta_setup, "install_args", lambda: [sys.executable, "-c", "pass"])
    msgs = []
    win = QWidget()
    win.settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    win.edit_new_primitives = False
    win.doc = SimpleNamespace(display_quality="normal", project=SimpleNamespace(path=None))
    win.solver = SimpleNamespace(show_emerge_log=False, busy=False)
    win.msgs = SimpleNamespace(add=lambda level, text: msgs.append((level, text)))
    win._benchmark = BenchmarkDialog(win)
    box = win._benchmark.solver_boxes["SPARTA (CPU)"]
    assert not box.isEnabled()
    dlg = OptionsDialog(win)
    dlg._install_sparta()
    installed["v"] = sparta_setup.VERSION
    end = time.monotonic() + 30
    while time.monotonic() < end and not msgs:
        app.processEvents()
        time.sleep(0.01)
    assert msgs and msgs[0][0] == "info" and "no restart" in msgs[0][1], msgs
    assert box.isEnabled() and box.isChecked()
