"""Keyboard Shortcuts dialog: a bare Enter always confirms and Esc always cancels, also while a shortcut field
has focus (the field used to record them as shortcuts). With a modifier they are still recorded."""
import os

import pytest


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(monkeypatch):
    app = _app()
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    monkeypatch.setattr(w, "maybe_save", lambda *a, **k: True)
    w.settings.setValue("shortcuts", "{}")
    yield app, w
    w.settings.setValue("shortcuts", "{}")
    w.close()


def _run_dialog(app, w, monkeypatch, keys):
    """Open Edit Shortcuts, focus the first shortcut field, type keys; return the dialog result."""
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    from hertzon.gui.shortcut_edit import ShortcutEdit
    seen = {}

    def drive():
        dlg = next(d for d in app.topLevelWidgets() if isinstance(d, QDialog) and d.isVisible())
        ed = dlg.findChildren(ShortcutEdit)[0]
        ed.setFocus()
        app.processEvents()
        for k, mod in keys:
            QTest.keyClick(ed, k, mod)
            app.processEvents()
        seen["seq"] = ed.keySequence().toString()
        if dlg.isVisible():          # the keys did not close it: close it so exec() returns
            seen["open"] = True
            dlg.reject()

    orig = QDialog.exec

    def exec_(self):
        QTimer.singleShot(0, drive)
        seen["result"] = orig(self)
        return seen["result"]

    monkeypatch.setattr(QDialog, "exec", exec_)
    w.edit_shortcuts()
    return seen


def test_enter_confirms(win, monkeypatch):
    from PySide6.QtCore import Qt
    app, w = win
    seen = _run_dialog(app, w, monkeypatch, [(Qt.Key_F7, Qt.ControlModifier), (Qt.Key_Return, Qt.NoModifier)])
    assert "open" not in seen
    assert seen["result"] == 1
    assert "Return" not in seen["seq"] and "Enter" not in seen["seq"]
    assert "Ctrl+F7" in w.settings.value("shortcuts")


def test_esc_cancels(win, monkeypatch):
    from PySide6.QtCore import Qt
    app, w = win
    seen = _run_dialog(app, w, monkeypatch, [(Qt.Key_F7, Qt.ControlModifier), (Qt.Key_Escape, Qt.NoModifier)])
    assert "open" not in seen
    assert seen["result"] == 0
    assert "Esc" not in seen["seq"]
    assert "Ctrl+F7" not in w.settings.value("shortcuts")


def test_modified_enter_is_still_a_shortcut(win, monkeypatch):
    from PySide6.QtCore import Qt
    app, w = win
    seen = _run_dialog(app, w, monkeypatch, [(Qt.Key_Return, Qt.ControlModifier)])
    assert seen.get("open")
    assert seen["seq"] == "Ctrl+Return"
