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
