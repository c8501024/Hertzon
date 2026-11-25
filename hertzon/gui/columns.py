"""Table columns the user can resize (issue #545).

A column in QHeaderView.ResizeToContents mode follows its content but cannot be dragged, so a long value or a
small screen left it too narrow or too wide. FitColumns puts the columns in Interactive mode (drag the edge in
the header) and fits them to their content after the table was filled, once per fill and after the event that
filled it, so filling cell by cell does not measure all rows again each time. A column the user has dragged
keeps its width until the columns change; a column can have a minimum and a maximum width when it is fitted
(the full text stays in the cell's tooltip or can be seen by widening the column).

No column is in QHeaderView.Stretch mode: Qt does not let the user drag the edge of such a column, so with a
stretched column in the middle the edges on both sides of it could not be dragged (#545 on macOS: "Value" and
"Evaluated Value" in the Properties panel). Spare width goes to the last column instead (stretch_last), whose
left edge stays draggable.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QHeaderView, QTableWidget


class FitColumns(QObject):
    def __init__(self, table: QTableWidget, fit=None, max_width: dict[int, int] | None = None,
                 stretch_last: bool = False, min_width: dict[int, int] | None = None):
        super().__init__(table)
        self.table = table
        self.fit_cols = None if fit is None else tuple(fit)        # None: every column
        self.stretch_last = stretch_last
        self.max_width = dict(max_width or {})
        self.min_width = dict(min_width or {})
        self.user: set[int] = set()              # columns the user has resized
        self._fitting = False
        self._pending = False
        self._ncols = table.columnCount()
        h = table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(stretch_last)
        h.sectionResized.connect(self._resized)
        m = table.model()
        for sig in (m.rowsInserted, m.columnsInserted, m.columnsRemoved, m.modelReset, m.dataChanged):
            sig.connect(self.schedule)

    def _last(self, col: int) -> bool:
        try:
            return self.stretch_last and col == self.table.columnCount() - 1
        except RuntimeError:
            return False

    def _resized(self, col: int, _old: int, _new: int) -> None:
        if not self._fitting and not self._last(col):     # the stretched last column follows the others
            self.user.add(col)

    def schedule(self, *_) -> None:
        """Fit the columns once the current event is done (many cells are set in one go)."""
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self.fit)

    def fit(self) -> None:
        self._pending = False
        t = self.table
        try:
            n = t.columnCount()
        except RuntimeError:          # the table is gone
            return
        if n != self._ncols:          # other columns (another report): the user's widths no longer apply
            self._ncols = n
            self.user.clear()
            t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        cols = range(n) if self.fit_cols is None else [c for c in self.fit_cols if c < n]
        self._fitting = True
        try:
            for c in cols:
                if c in self.user or self._last(c):
                    continue
                t.resizeColumnToContents(c)
                w = self.max_width.get(c)
                if w and t.columnWidth(c) > w:
                    t.setColumnWidth(c, w)
                w = self.min_width.get(c)
                if w and t.columnWidth(c) < w:
                    t.setColumnWidth(c, w)
        finally:
            self._fitting = False
