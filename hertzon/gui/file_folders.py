"""Start folders of the file dialogs: where Open, Save As and the Touchstone export begin.

Without a start folder Qt opens the working folder, which for an installed Hertzon is its install folder.
The rules (stored per user in the settings):

* Open project: the folder last used for a project in this session (opened or saved), else the default
  project folder of Tools > Options > General, else the folder of the last project, else Documents.
* Save As: the folder of the current project, else the folder last saved to, else as Open.
* Touchstone export: the folder of the current project file, else the folder of the last Touchstone
  export, else as Open.

Folders that no longer exist are skipped.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_DIR = "files/default_project_dir"      # set by the user in Tools > Options > General
LAST_PROJECT_DIR = "files/last_project_dir"    # folder of the last project opened or saved
LAST_SAVE_DIR = "files/last_save_dir"          # folder of the last Save As
LAST_TOUCHSTONE_DIR = "files/last_touchstone_dir"

_session_dir: str | None = None                # last project folder of this run of Hertzon


def _settings(settings):
    if settings is None:
        from PySide6.QtCore import QSettings
        settings = QSettings("Hertzon", "Hertzon")
    return settings


def _existing(*dirs) -> str | None:
    for d in dirs:
        if d and Path(str(d)).is_dir():
            return str(d)
    return None


def _documents() -> str:
    try:
        from PySide6.QtCore import QStandardPaths
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    except Exception:      # noqa: BLE001 - without Qt: the home folder
        docs = ""
    return _existing(docs) or str(Path.home())


def _project_parent(project_path) -> str | None:
    return str(Path(project_path).parent) if project_path else None


def default_project_dir(settings=None) -> str:
    """The default project folder of the options, '' when none is set."""
    return str(_settings(settings).value(DEFAULT_DIR, "") or "")


def open_dir(settings=None) -> str:
    """Start folder of File > Open (and of the other dialogs that pick a project file)."""
    s = _settings(settings)
    return _existing(_session_dir, s.value(DEFAULT_DIR, ""), s.value(LAST_PROJECT_DIR, "")) or _documents()


def save_dir(project_path=None, settings=None) -> str:
    """Start folder of File > Save As."""
    s = _settings(settings)
    return _existing(_project_parent(project_path), s.value(LAST_SAVE_DIR, "")) or open_dir(s)


def touchstone_dir(project_path=None, settings=None) -> str:
    """Start folder of the Touchstone export."""
    s = _settings(settings)
    return _existing(_project_parent(project_path), s.value(LAST_TOUCHSTONE_DIR, "")) or open_dir(s)


def remember_project(path, settings=None, saved: bool = False) -> None:
    """A project was opened (or saved, saved=True) at path: its folder is where the next dialog starts."""
    global _session_dir
    if not path:
        return
    folder = str(Path(path).parent)
    _session_dir = folder
    s = _settings(settings)
    s.setValue(LAST_PROJECT_DIR, folder)
    if saved:
        s.setValue(LAST_SAVE_DIR, folder)


def remember_touchstone(path, settings=None) -> None:
    if path:
        _settings(settings).setValue(LAST_TOUCHSTONE_DIR, str(Path(path).parent))


def in_dir(folder: str, name: str) -> str:
    """The proposed file name inside the start folder."""
    return str(Path(folder) / name)
