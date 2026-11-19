"""F2 renames the selected item (issue #475)."""
import os


def _win():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.main_window import MainWindow
    return MainWindow()


def _close(w):
    w.doc.dirty = False                 # no "Save changes?" question on close
    w.close()


def _find(tree, data):
    from hertzon.gui.trees import ROLE

    def walk(it):
        if it.data(0, ROLE) == data:
            return it
        for i in range(it.childCount()):
            r = walk(it.child(i))
            if r is not None:
                return r
        return None
    for i in range(tree.topLevelItemCount()):
        r = walk(tree.topLevelItem(i))
        if r is not None:
            return r
    return None


def test_f2_is_the_rename_shortcut():
    w = _win()
    assert w.a_rename.shortcut().toString() == "F2"
    _close(w)


def test_f2_renames_object_from_model_tree_and_view(monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog
    from hertzon.gui.doc import Pick
    w = _win()
    w.des.add_object("CreateBox", "Box1", Position="0,0,0", XSize="1mm", YSize="1mm", ZSize="1mm")
    w.doc.rebuild()
    w.model_tree.rebuild()
    names = iter(["Body", "Body2"])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (next(names), True))
    it = _find(w.model_tree, ("object", "Box1"))
    w.model_tree.setCurrentItem(it)
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: w.model_tree))
    w.a_rename.trigger()
    assert [o.name for o in w.des.objects] == ["Body"]
    # elsewhere (3D view): the one selected object
    w.doc.select([Pick("Body")])
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: w.view))
    w.a_rename.trigger()
    assert [o.name for o in w.des.objects] == ["Body2"]
    w.doc.undo()
    assert [o.name for o in w.des.objects] == ["Body"]
    _close(w)


def test_f2_renames_design_in_project_tree(monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog
    w = _win()
    old = w.doc.design_name
    w.project_tree.rebuild()
    w.project_tree.setCurrentItem(_find(w.project_tree, ("design", old)))
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: w.project_tree))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed", True))
    w.a_rename.trigger()
    assert w.doc.design_name == "Renamed" and w.des.name == "Renamed"
    _close(w)


def test_f2_leaves_text_fields_alone(monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit
    w = _win()
    ed = QLineEdit()
    called = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: called.append(1) or ("x", True))
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: ed))
    w.a_rename.trigger()
    assert not called
    _close(w)
