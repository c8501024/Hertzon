"""Model view usability (user requests): Return in the coordinate fields of the status bar enters the typed
point, also while a line or polyline is drawn (the window-wide Return shortcut "Finish" took the key there and
ended the line without the point); a click into the model view deselects a boundary or excitation that is
selected in the Project Manager, so its highlighted faces go away."""
import os

import numpy as np


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window(monkeypatch):
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    monkeypatch.setattr(w, "maybe_save", lambda *a, **k: True)
    w.edit_new_primitives = False           # no properties dialog after each new object
    w.show()
    return w


def _type_point(app, w, x, y, z):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    for ed, v in ((w.cx, x), (w.cy, y), (w.cz, z)):
        QTest.mouseClick(ed, Qt.LeftButton)
        ed.selectAll()
        QTest.keyClicks(ed, v)
    QTest.keyClick(w.cz, Qt.Key_Return)
    app.processEvents()


def test_return_in_coordinate_fields_enters_points(monkeypatch):
    app = _app()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w = _window(monkeypatch)
    try:
        s = 1e-3                             # mm
        done = []
        w.view.drawFinished.connect(lambda k, p: done.append((k, [np.asarray(q) for q in p])))
        w.view.start_draw("CreatePolyline")
        app.processEvents()
        for p in (("0", "0", "0"), ("2", "0", "0"), ("2", "3", "0")):
            _type_point(app, w, *p)
        assert w.view.draw is not None and len(w.view.draw.points) == 3
        np.testing.assert_allclose(w.view.draw.points[-1], [2 * s, 3 * s, 0])
        # Return in the view still finishes the polyline
        QTest.keyClick(w.view.plotter.interactor, Qt.Key_Return)
        app.processEvents()
        assert w.view.draw is None and done and done[-1][0] == "CreatePolyline" and len(done[-1][1]) == 3
        # a box from three typed points
        w.view.start_draw("CreateBox")
        app.processEvents()
        for p in (("0", "0", "0"), ("1", "2", "0"), ("0", "0", "3")):
            _type_point(app, w, *p)
        assert done[-1][0] == "CreateBox" and len(done[-1][1]) == 3
    finally:
        w.close()


def test_click_in_view_deselects_boundary(monkeypatch):
    app = _app()
    from hertzon.model import schema
    from hertzon.model.design import Assignment
    w = _window(monkeypatch)
    try:
        d = w.doc.design
        d.add_object("CreateBox", "Box1", Position="0,0,0", XSize="10", YSize="10", ZSize="1")
        p = schema.defaults(schema.BOUNDARIES["Perfect E"])
        d.boundaries.append(Assignment("PerfE1", "Perfect E", p, objects=["Box1"]))
        w.doc.commit("boundaries")
        app.processEvents()
        shown = []
        monkeypatch.setattr(w.view, "show_assignments", lambda ass, *a, **k: shown.append([x.name for x in ass]))
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        from hertzon.gui.trees import ROLE
        it = QTreeWidgetItemIterator(w.project_tree)
        while it.value() is not None and it.value().data(0, ROLE) != ("boundary", "PerfE1"):
            it += 1
        w.project_tree.setCurrentItem(it.value())     # a click on the boundary in the Project Manager
        app.processEvents()
        assert shown[-1] == ["PerfE1"] and w.props.target is not None
        w.view.picked.emit([], False)        # a click into an empty part of the view
        app.processEvents()
        assert not w.project_tree.selectedItems()
        assert shown[-1] == []
        assert w.props.target is None
    finally:
        w.close()
