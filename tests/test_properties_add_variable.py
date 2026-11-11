"""Return in the docked Properties panel commits the value, so a new name typed there opens the Add Variable
dialog and becomes a design variable (report for 0.3.17: the window-wide Return shortcut of Draw > Line took
the key, the value stayed unchanged and the dialog never came)."""
import os


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_new_name_in_properties_panel_creates_variable(monkeypatch):
    app = _app()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QInputDialog
    from hertzon.gui.main_window import MainWindow
    asked = []
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: (asked.append(a[2]), ("12.5", True))[1]))
    w = MainWindow()
    monkeypatch.setattr(w, "maybe_save", lambda *a, **k: True)
    try:
        w.show()
        w.doc.design.add_object("CreateBox", "Box1", Position="-5,30,0", XSize="15", YSize="-80", ZSize="35")
        w.doc.commit("model")
        w.props.show_object("Box1", 0)
        app.processEvents()
        t = w.props.tabs.widget(0).table
        row = next(r for r in range(t.rowCount()) if t.item(r, 0).text() == "XSize")
        QTest.mouseDClick(t.viewport(), Qt.LeftButton, pos=t.visualRect(t.model().index(row, 1)).center())
        app.processEvents()
        ed = app.focusWidget()
        ed.selectAll()
        QTest.keyClicks(ed, "len")
        QTest.keyClick(ed, Qt.Key_Return)
        app.processEvents()
        assert asked and "'len' is not defined" in asked[0]
        assert w.doc.design.variables.get("len") == "12.5"
        assert w.doc.design.obj("Box1").history[0].props["XSize"] == "len"
        # Return still finishes a polyline while one is drawn, and only then
        assert not w.a_enter.isEnabled()
        w.view.start_draw("CreatePolyline")
        assert w.a_enter.isEnabled()
        w.view.cancel_draw()
        assert not w.a_enter.isEnabled()
        w.view.start_draw("CreateBox")
        assert not w.a_enter.isEnabled()
        w.view.cancel_draw()
    finally:
        w.close()
