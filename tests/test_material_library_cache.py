"""EMerge's material library is read from a small cache file instead of importing emsutil every start
(creating or editing a material waited seconds for that import on Windows)."""
import os

import pytest

from hertzon.model import materials as M


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(M, "_EMERGE_CACHE", None)
    return tmp_path


def test_cache_file_is_written_and_read_back(fresh, monkeypatch):
    path = M._emerge_cache_file()
    if path is None:
        pytest.skip("emsutil not installed")
    first = M.emerge_library()
    assert first and path.exists()
    # second start: the table comes from the file, emsutil is not asked again
    monkeypatch.setattr(M, "_EMERGE_CACHE", None)
    monkeypatch.setattr(M, "_emerge_from_emsutil", lambda: pytest.fail("emsutil read again"))
    second = M.emerge_library()
    assert second == first and list(second) == list(first)


def test_damaged_cache_file_is_rebuilt(fresh):
    path = M._emerge_cache_file()
    if path is None:
        pytest.skip("emsutil not installed")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    mats = M.emerge_library()
    assert mats and M._read_emerge_cache(path) == mats


def test_materials_dialog_opens_one_edit_dialog_at_a_time():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QDialog
    from hertzon.gui import dialogs as D
    from hertzon.model.design import Project

    class Doc:
        project = Project()
        dirty = False

    dlg = D.MaterialsDialog(Doc())
    opened = []

    class Fake:
        def __init__(self, mat, parent):
            opened.append(mat.name)

        def exec(self):
            dlg._add()               # a second click while the first dialog is open
            return QDialog.Rejected

    orig = D.MaterialEditDialog
    D.MaterialEditDialog = Fake
    try:
        dlg._add()
    finally:
        D.MaterialEditDialog = orig
    assert opened == ["Material1"]


def test_refilling_the_material_list_is_fast():
    """Storing a material under an existing name refills the list with the same number of rows. With the
    header on ResizeToContents every cell set measured all rows again: 22 s on Windows, 15 s here."""
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hertzon.gui.dialogs import MaterialsDialog
    from hertzon.model.design import Project
    from hertzon.model.materials import Material

    class Doc:
        project = Project()
        dirty = False

    dlg = MaterialsDialog(Doc())
    dlg.show()
    app.processEvents()
    try:
        dlg._store(Material(name="Material1"))
        t = time.perf_counter()
        dlg._store(Material(name="Material1", permittivity="4.4"))     # same name again: same row count
        app.processEvents()
        assert time.perf_counter() - t < 2.0
        assert dlg.doc.project.material("Material1").permittivity == "4.4"
    finally:
        dlg.close()
