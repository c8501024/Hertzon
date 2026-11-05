"""Constants & Functions: the names an expression may use, inserted into a value with one click."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QHeaderView, QStyledItemDelegate)

from ..model.expr import CONSTANT_INFO, FUNCTION_INFO, UNIT_GROUPS, Evaluator


def _unit_text(factor: float, group: str) -> str:
    base = {"Length": "m", "Frequency": "Hz", "Angle": "rad"}.get(group, "")
    return f"= {factor:.6g} {base}".rstrip()


def insert_into(text: str, sel_start: int, sel_len: int, snippet: str) -> tuple[str, int]:
    """Insert ``snippet`` at the cursor (replacing a selection). A function wraps the selection
    as its first argument. Returns the new text and the cursor position after the insert."""
    sel_start = max(0, min(sel_start, len(text)))
    sel_len = max(0, min(sel_len, len(text) - sel_start))
    selected = text[sel_start:sel_start + sel_len]
    paren = snippet.find("(")
    if paren >= 0:                                  # function: cursor (or the selection) goes inside the brackets
        ins = snippet[:paren + 1] + selected + snippet[paren + 1:]
        cursor = sel_start + paren + 1 + len(selected)
    else:
        ins = snippet
        cursor = sel_start + len(ins)
    return text[:sel_start] + ins + text[sel_start + sel_len:], cursor


class CursorDelegate(QStyledItemDelegate):
    """Item delegate that remembers where the cursor was when a value cell stopped being edited,
    so that a constant picked in another window lands where the user was typing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last: tuple[int, int, int] | None = None     # (row, selection start, selection length)
        self.editor: QLineEdit | None = None

    def createEditor(self, parent, option, index):
        ed = super().createEditor(parent, option, index)
        self.editor = ed if isinstance(ed, QLineEdit) else None
        return ed

    def setModelData(self, editor, model, index):
        if isinstance(editor, QLineEdit):
            if editor.hasSelectedText():
                self.last = (index.row(), editor.selectionStart(), len(editor.selectedText()))
            else:
                self.last = (index.row(), editor.cursorPosition(), 0)
        super().setModelData(editor, model, index)


class ExpressionReferenceDialog(QDialog):
    """List of constants, functions, units and variables usable in expressions.
    Double-click or Insert emits ``inserted`` with the text to insert; the window stays open."""

    inserted = Signal(str)

    def __init__(self, variables: dict[str, str] | None = None, model_unit: str = "mm", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Constants & Functions")
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search ...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Value", "Description"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._insert(it))
        self.tree.currentItemChanged.connect(lambda *_: self._update())
        lay.addWidget(self.tree)
        self.status = QLabel("Double-click an entry to insert it into the selected value.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.insert_btn = QPushButton("Insert")
        self.insert_btn.clicked.connect(lambda: self._insert(self.tree.currentItem()))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addStretch()
        row.addWidget(self.insert_btn)
        row.addWidget(close)
        lay.addLayout(row)
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._build(variables or {}, model_unit)
        self._update()
        self.resize(620, 520)

    # ------------------------------------------------------------------------------------------
    def _group(self, title: str, expanded: bool = True) -> QTreeWidgetItem:
        g = QTreeWidgetItem([title])
        g.setFlags(Qt.ItemIsEnabled)
        f = g.font(0)
        f.setBold(True)
        g.setFont(0, f)
        self.tree.addTopLevelItem(g)
        g.setExpanded(expanded)
        g.setFirstColumnSpanned(True)
        self._groups[title] = g
        return g

    @staticmethod
    def _entry(group: QTreeWidgetItem, name: str, value: str, desc: str, snippet: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem([name, value, desc])
        it.setData(0, Qt.UserRole, snippet)
        it.setToolTip(2, desc)
        group.addChild(it)
        return it

    def _build(self, variables: dict[str, str], model_unit: str) -> None:
        g = self._group("Constants")
        for name, snippet, desc in CONSTANT_INFO:
            val = Evaluator({}, model_unit).value(snippet)
            self._entry(g, name, f"{val:.10g}", desc, snippet)
        g = self._group("Functions")
        for name, snippet, desc in FUNCTION_INFO:
            self._entry(g, name, "", desc, snippet)
        for title, units in UNIT_GROUPS:
            g = self._group(f"Units: {title}", expanded=title == "Length")
            for u, factor in units.items():
                self._entry(g, u, _unit_text(factor, title), f"Write after a number, e.g. 2{u}", u)
        if variables:
            g = self._group("Variables")
            ev = Evaluator(variables, model_unit)
            for name, expr in variables.items():
                try:
                    v, has_unit = ev._eval(name)
                    shown = f"{v * 1e3:.6g}mm" if has_unit and abs(v) < 1 else f"{v:.6g}"
                except Exception:  # noqa: BLE001
                    shown = "?"
                self._entry(g, name, shown, expr, name)
        self.tree.setCurrentItem(self._groups["Constants"].child(0))

    def _filter(self, text: str) -> None:
        t = text.strip().lower()
        for g in self._groups.values():
            any_shown = False
            for i in range(g.childCount()):
                it = g.child(i)
                show = not t or t in it.text(0).lower() or t in it.text(2).lower()
                it.setHidden(not show)
                any_shown |= show
            g.setHidden(not any_shown)
            if t and any_shown:
                g.setExpanded(True)

    def _update(self) -> None:
        it = self.tree.currentItem()
        self.insert_btn.setEnabled(bool(it is not None and it.data(0, Qt.UserRole)))

    def _insert(self, it: QTreeWidgetItem | None) -> None:
        snippet = it.data(0, Qt.UserRole) if it is not None else None
        if snippet:
            self.inserted.emit(snippet)

    def set_status(self, text: str) -> None:
        self.status.setText(text)
