"""The Properties panel shows only what is selected now, and is empty when the selection has no properties
(nothing, several objects, faces) or what it showed is gone."""
import os


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_properties_follow_the_selection(monkeypatch):
    app = _app()
    from hertzon.gui.doc import Pick
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    monkeypatch.setattr(w, "maybe_save", lambda *a, **k: True)
    try:
        w.show()
        d = w.doc.design
        d.add_object("CreateBox", "Box1", Position="0,0,0", XSize="1", YSize="1", ZSize="1")
        d.add_object("CreateBox", "Box2", Position="2,0,0", XSize="1", YSize="1", ZSize="1")
        w.doc.commit("model")
        app.processEvents()
        w.doc.select([Pick("Box1")])
        assert w.props.target == ("object", "Box1")
        w.doc.select([Pick("Box1"), Pick("Box2")])           # several objects: no properties
        assert w.props.target is None and w.props.tabs.count() == 0
        w.doc.select([Pick("Box2")])
        assert w.props.target == ("object", "Box2")
        w.doc.select([])                                      # nothing selected
        assert w.props.target is None and w.props.tabs.count() == 0
        w._view_picked([], False)                             # a click into empty space
        assert w.props.target is None
        # a project item: shown while selected, gone when the tree selection goes
        w._project_selected(("design", d.name))
        assert w.props.target == ("design", d.name)
        w._project_selected(None)
        assert w.props.target is None
        w._project_selected(("design", d.name))
        w._project_selected(("group", "Field Overlays"))      # an item without properties
        assert w.props.target is None
        # a deleted object leaves the panel
        w.doc.select([Pick("Box1")])
        assert w.props.target == ("object", "Box1")
        d.objects = [o for o in d.objects if o.name != "Box1"]
        w.doc.commit("model")
        assert w.props.target is None
    finally:
        w.close()
