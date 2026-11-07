"""Start folders of Open, Save As and the Touchstone export: never the install (working) folder."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from hertzon.gui import file_folders as ff


class FakeSettings(dict):
    def value(self, key, default=None, type=None):  # noqa: A002 - QSettings signature
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - QSettings signature
        self[key] = value


@pytest.fixture(autouse=True)
def fresh_session(monkeypatch, tmp_path):
    monkeypatch.setattr(ff, "_session_dir", None)
    monkeypatch.setattr(ff, "_documents", lambda: str(tmp_path / "docs"))


def test_open_starts_in_default_folder_after_start(tmp_path):
    default, last = tmp_path / "projects", tmp_path / "last"
    default.mkdir()
    last.mkdir()
    s = FakeSettings({ff.DEFAULT_DIR: str(default), ff.LAST_PROJECT_DIR: str(last)})
    assert ff.open_dir(s) == str(default)


def test_open_follows_last_project_of_the_session(tmp_path):
    default, work = tmp_path / "projects", tmp_path / "work"
    default.mkdir()
    work.mkdir()
    s = FakeSettings({ff.DEFAULT_DIR: str(default)})
    ff.remember_project(str(work / "a.hzn"), s)
    assert ff.open_dir(s) == str(work)


def test_open_without_default_uses_last_project_then_documents(tmp_path):
    s = FakeSettings()
    assert ff.open_dir(s) == str(tmp_path / "docs")
    last = tmp_path / "last"
    last.mkdir()
    s[ff.LAST_PROJECT_DIR] = str(last)
    assert ff.open_dir(s) == str(last)


def test_missing_folders_are_skipped(tmp_path):
    s = FakeSettings({ff.DEFAULT_DIR: str(tmp_path / "gone"), ff.LAST_PROJECT_DIR: str(tmp_path / "gone2")})
    assert ff.open_dir(s) == str(tmp_path / "docs")


def test_save_as_starts_in_project_folder_else_last_save_folder(tmp_path):
    proj, saved = tmp_path / "proj", tmp_path / "saved"
    proj.mkdir()
    saved.mkdir()
    s = FakeSettings()
    ff.remember_project(str(saved / "x.hzn"), s, saved=True)
    assert s[ff.LAST_SAVE_DIR] == str(saved)
    assert ff.save_dir(str(proj / "p.hzn"), s) == str(proj)
    assert ff.save_dir(None, s) == str(saved)          # untitled project: the folder saved to last


def test_touchstone_starts_in_project_folder_else_last_touchstone_folder(tmp_path):
    proj, ts = tmp_path / "proj", tmp_path / "ts"
    proj.mkdir()
    ts.mkdir()
    s = FakeSettings()
    ff.remember_touchstone(str(ts / "a.s2p"), s)
    assert ff.touchstone_dir(str(proj / "p.hzn"), s) == str(proj)
    assert ff.touchstone_dir(None, s) == str(ts)


def test_start_folder_is_never_the_working_folder(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.chdir(install)
    s = FakeSettings()
    for d in (ff.open_dir(s), ff.save_dir(None, s), ff.touchstone_dir(None, s)):
        assert os.path.isabs(d) and d != str(install)
    assert ff.in_dir(ff.save_dir(None, s), "p.hzn") == str(tmp_path / "docs" / "p.hzn")


def test_options_dialog_stores_default_project_folder(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path))
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from hertzon.gui.dialogs import OptionsDialog
    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    settings.setValue(ff.DEFAULT_DIR, str(tmp_path))
    win = SimpleNamespace(settings=settings, edit_new_primitives=False,
                          doc=SimpleNamespace(display_quality="normal"),
                          solver=SimpleNamespace(show_emerge_log=False))
    dlg = OptionsDialog(win)
    assert dlg.project_dir.text() == str(tmp_path)
    dlg.project_dir.setText(f"  {tmp_path / 'p'}  ")
    dlg._store_project_dir(settings)
    assert ff.default_project_dir(settings) == str(tmp_path / "p")


def test_main_window_dialogs_start_in_the_remembered_folders(tmp_path, monkeypatch):
    """File > Open, Save As and the Touchstone export pass the folders above to the file dialogs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from hertzon.gui import main_window as mw
    w = mw.MainWindow()
    try:
        w.settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
        default, work = tmp_path / "projects", tmp_path / "work"
        default.mkdir()
        work.mkdir()
        w.settings.setValue(ff.DEFAULT_DIR, str(default))
        starts = []

        def fake_open(_parent, _title, start, _filter):
            starts.append(start)
            return "", ""
        monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", fake_open)
        monkeypatch.setattr(w, "maybe_save", lambda: True)
        w.file_open()
        assert starts[-1] == str(default)                 # after the start: the default project folder

        saved = str(work / "Copy.hzn")
        monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *a: (starts.append(a[2]) or saved, ""))
        assert w.file_save_as()
        assert starts[-1].startswith(str(default))    # untitled: the default folder (no save folder yet)
        assert w.settings.value(ff.LAST_SAVE_DIR) == str(work)
        w.file_open()
        assert starts[-1] == str(work)                    # then: the folder saved to last
        monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *a: (starts.append(a[2]) or "", ""))
        w.file_save_as()
        assert starts[-1] == str(work / "Copy.hzn")       # Save As again: the folder of the project
    finally:
        w.doc.dirty = False
        w.close()
