"""Changing a design variable in the docked Properties panel updates the Evaluated Value of the variables that
use it right away (report #430 for 0.3.22: only the edited row was updated, the others after re-selecting)."""
import os


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dependent_variable_updates_when_its_input_changes(monkeypatch):
    app = _app()
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    monkeypatch.setattr(w, "maybe_save", lambda *a, **k: True)
    try:
        w.show()
        w.doc.design.variables.update({"a": "2mm", "b": "3*a"})
        w.doc.commit("variables")
        w.props.show_design()
        app.processEvents()
        g = w.props.tabs.widget(0)
        t = g.table

        def row(name):
            return next(r for r in range(t.rowCount()) if t.item(r, 0).text() == name)

        assert t.item(row("b"), 2).text() == "0.006"
        t.item(row("a"), 1).setText("5mm")          # as if typed and committed
        app.processEvents()
        assert w.doc.design.variables["a"] == "5mm"
        assert t.item(row("a"), 2).text() == "0.005"
        assert t.item(row("b"), 2).text() == "0.015"
    finally:
        w.close()
