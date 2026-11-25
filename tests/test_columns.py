"""Table columns the user can resize (#545) and the macOS menu name (#543)."""
from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _fill(table, rows):
    from PySide6.QtWidgets import QTableWidgetItem
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(v))


def test_columns_can_be_dragged_and_fit_after_the_table_is_filled(app):
    from PySide6.QtWidgets import QHeaderView, QTableWidget
    from hertzon.gui.columns import FitColumns
    t = QTableWidget(0, 3)
    t.resize(800, 300)
    fc = FitColumns(t, fit=(0, 1))
    h = t.horizontalHeader()
    assert all(h.sectionResizeMode(c) == QHeaderView.Interactive for c in range(3))
    _fill(t, [("a" * 60, "v", "x")])
    app.processEvents()                      # the fit runs once the filling event is done
    wide = t.columnWidth(0)
    assert wide > t.columnWidth(1)
    t.setColumnWidth(0, 40)                  # the user drags the column narrower
    _fill(t, [("a" * 60, "v", "x"), ("b", "w", "y")])
    app.processEvents()
    assert t.columnWidth(0) == 40            # ... and it stays so while the table is refilled
    assert 0 in fc.user
    t.setColumnCount(4)                      # other columns: fitted again
    _fill(t, [("a" * 60, "v", "x", "z")])
    app.processEvents()
    assert t.columnWidth(0) == wide
    assert not fc.user


def test_max_width_when_fitted(app):
    from PySide6.QtWidgets import QTableWidget
    from hertzon.gui.columns import FitColumns
    t = QTableWidget(0, 2)
    FitColumns(t, max_width={0: 120})
    _fill(t, [("very long analysis name " * 10, "x")])
    app.processEvents()
    assert t.columnWidth(0) == 120
    t.setColumnWidth(0, 400)                 # the user can still widen it
    app.processEvents()
    assert t.columnWidth(0) == 400


def _draggable_edges(table):
    """Columns whose right edge the user can drag: Interactive, and not the stretched last one."""
    from PySide6.QtWidgets import QHeaderView
    h = table.horizontalHeader()
    n = table.columnCount()
    return [c for c in range(n) if h.sectionResizeMode(c) == QHeaderView.Interactive
            and not (h.stretchLastSection() and c == n - 1)]


def test_properties_panel_every_inner_edge_can_be_dragged(app):
    """#545 (0.4.5 on macOS): the edges of Value and Evaluated Value could not be dragged, because Value was in
    Stretch mode. Now every inner edge is draggable and the last column takes the spare width."""
    from hertzon.gui.props import PropertyGrid
    g = PropertyGrid()
    g.resize(600, 300)
    t = g.table
    assert _draggable_edges(t) == [0, 1]
    assert t.horizontalHeader().stretchLastSection()
    _fill(t, [("Width", "w1/2", "5 mm"), ("Height", "1.6", "1.6 mm")])
    app.processEvents()
    assert t.columnWidth(1) >= 120
    t.setColumnWidth(1, 300)                 # the user drags Value wider
    _fill(t, [("Width", "w1/2", "5 mm")])
    app.processEvents()
    assert t.columnWidth(1) == 300
    g2 = PropertyGrid(show_evaluated=False)
    assert _draggable_edges(g2.table) == [0]


def test_progress_panel_analysis_column(app):
    from PySide6.QtWidgets import QHeaderView
    from hertzon.gui.panels import ProgressPanel
    p = ProgressPanel()
    h = p.table.horizontalHeader()
    assert all(h.sectionResizeMode(c) == QHeaderView.Interactive for c in range(p.table.columnCount()))
    assert _draggable_edges(p.table) == [0, 1, 2]
    _fill(p.table, [("Project - Design - Setup1 " * 6, "", "Queued", "")])
    app.processEvents()
    assert p.table.columnWidth(0) <= 200


@pytest.mark.skipif(sys.platform == "darwin", reason="sets the name on macOS")
def test_mac_app_name_does_nothing_elsewhere():
    from hertzon.gui.app import _mac_app_name
    assert _mac_app_name() is False


def test_finder_files_without_an_application_event_filter():
    """#547: no event filter written in Python on the whole application (it crashed in PySide when Qt WebEngine
    made objects); Finder's open-file events go to the application's own event() instead."""
    from types import SimpleNamespace
    from hertzon.gui.app import _finder_files
    app = SimpleNamespace(open_file=None)
    f = _finder_files(app)
    assert app.open_file is f
    f("/x/a.hzn")
    f("/x/notes.txt")
    assert f.pending == ["/x/a.hzn"]


def test_no_python_event_filter_on_the_application():
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[1] / "hertzon"
    hits = [str(p) for p in src.rglob("*.py") if p.name != "webengine_check.py"
            and re.search(r"\b(app|QApplication\.instance\(\)|QCoreApplication\.instance\(\))\.installEventFilter",
                          p.read_text(encoding="utf-8"))]
    assert not hits, hits
