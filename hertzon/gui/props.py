"""Schema driven property grid (Name | Value | Evaluated Value), used by the docked
Properties window and by every assignment/setup dialog."""
from __future__ import annotations


import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QWidget, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QCheckBox,
                               QVBoxLayout, QTabWidget, QLineEdit, QColorDialog, QPushButton, QHBoxLayout,
                               QAbstractItemView)

from ..model.schema import Prop, is_visible
from ..model.expr import Evaluator, fmt_freq


def evaluate_display(prop: Prop, text: str, ev: Evaluator | None) -> tuple[str, bool]:
    """Evaluated value as shown in the third column and whether it is valid."""
    if ev is None or prop.type in ("bool", "choice", "material", "text", "objects", "color"):
        return "", True
    try:
        if prop.type == "length":
            if not str(text).strip():
                return "auto", True
            return f"{ev.length(text) / ev.unit_scale:.6g}{ev.model_unit}", True
        if prop.type in ("point", "vector"):
            pts = ev.point(text)
            return ", ".join(f"{p / ev.unit_scale:.6g}" for p in pts) + ev.model_unit, True
        if prop.type == "points":
            if not str(text).strip():
                return "", True
            pts = ev.points(text)
            return f"{len(pts)} points", True
        if prop.type == "freq":
            return fmt_freq(ev.freq(text)), True
        if prop.type == "angle":
            import math
            return f"{math.degrees(ev.angle(text)):.6g}deg", True
        if prop.type in ("int",):
            return str(ev.integer(text)), True
        if prop.type in ("expr", "float"):
            return f"{ev.value(text):.6g}", True
    except Exception as e:  # noqa: BLE001
        return str(e), False
    return "", True


# typing an unknown name into a value field offers to create that variable.
# Set by the main window: callable(name, value) that stores a new design variable.
ADD_VARIABLE_HOOK = None
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _unknown_names(text: str, ev: Evaluator) -> list[str]:
    from ..model.expr import ExprError
    out = []
    for _ in range(10):
        try:
            for part in str(text).split(";"):
                for c in part.split(","):
                    if c.strip():
                        ev.value(c)
            return out
        except ExprError as e:
            m = re.search(r"Unknown variable '([^']+)'", str(e))
            if not m or m.group(1) in out:
                return out
            out.append(m.group(1))
            ev.variables[m.group(1)] = "0"      # probe further names; removed by the caller
            ev._cache.clear()
        except Exception:  # noqa: BLE001
            return out
    return out


def _old_component(old: str, new: str, name: str) -> str:
    """Previous value that the new name replaces: the whole field, or the same component of a point."""
    o_parts, n_parts = str(old or "").split(","), str(new).split(",")
    if len(n_parts) > 1 and len(o_parts) == len(n_parts):
        for o, n in zip(o_parts, n_parts):
            if name in _IDENT.findall(n):
                return o.strip()
    return str(old or "").strip()


# prop name -> callable giving the current choices (e.g. coordinate system names)
DYNAMIC_CHOICES: dict = {}


class PropertyGrid(QWidget):
    """Edits a ``dict[str, str]`` according to a list of :class:`Prop`."""

    changed = Signal(str, str)        # name, new value

    def __init__(self, parent=None, show_evaluated: bool = True):
        super().__init__(parent)
        self.table = QTableWidget(0, 3 if show_evaluated else 2, self)
        self.table.setHorizontalHeaderLabels(["Name", "Value", "Evaluated Value"][: self.table.columnCount()])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        if show_evaluated:
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.table.itemChanged.connect(self._item_changed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        self.props: list[Prop] = []
        self.values: dict[str, str] = {}
        self.ev: Evaluator | None = None
        self.materials: list[str] = []
        self.objects: list[str] = []
        self.readonly: set[str] = set()
        self._loading = False

    def set(self, props: list[Prop], values: dict[str, str], ev: Evaluator | None = None,
            materials: list[str] | None = None, objects: list[str] | None = None, readonly=()) -> None:
        self.props = list(props)
        self.values = values
        self.ev = ev
        self.materials = materials or []
        self.objects = objects or []
        self.readonly = set(readonly)
        self.rebuild()

    def rebuild(self) -> None:
        self._loading = True
        t = self.table
        # Retire the old editors safely: a combo box may still be closing its popup or emitting the signal that
        # triggered this rebuild. Deleting it right away (clearContents) crashed Qt on Windows with an access
        # violation (Auto Open dialog). Detach, hide and let Qt delete them once the event is done.
        old = []
        for r in range(t.rowCount()):
            w = t.cellWidget(r, 1)
            if w is not None:
                w.hide()
                w.blockSignals(True)
                w.setParent(None)
                w.deleteLater()
                old.append(w)
        self._retired = old              # keep the Python wrappers alive until deleteLater runs
        t.clearContents()
        visible = [p for p in self.props if is_visible(p, self.values)]
        t.setRowCount(len(visible))
        for r, p in enumerate(visible):
            name = QTableWidgetItem(p.title)
            name.setFlags(Qt.ItemIsEnabled)
            from ..model.help import help_for
            tip = help_for(p, getattr(self, "group", "")) or p.name
            name.setToolTip(f"<b>{p.title}</b><br>{tip}")
            name.setData(Qt.UserRole, p.name)
            t.setItem(r, 0, name)
            val = self.values.get(p.name, p.default)
            ro = p.name in self.readonly
            off = p.enabled_if is not None and str(self.values.get(p.enabled_if[0], "")) != p.enabled_if[1]
            ro = ro or off
            if off:
                name.setForeground(QBrush(QColor("#9a9a9a")))
            if p.type == "bool":
                cb = QCheckBox()
                cb.setChecked(str(val) == "True" and not off)
                cb.setEnabled(not ro)
                cb.toggled.connect(lambda v, n=p.name: self._set(n, "True" if v else "False", rebuild=True))
                t.setCellWidget(r, 1, _wrap(cb))
            elif p.type in ("choice", "material"):
                combo = QComboBox()
                opts = list(p.choices) if p.type == "choice" else self.materials
                if p.type == "choice" and p.name in DYNAMIC_CHOICES:
                    try:
                        opts = list(DYNAMIC_CHOICES[p.name]())
                    except Exception:  # noqa: BLE001
                        pass
                combo.addItems(opts)
                if val not in opts:
                    combo.addItem(val)
                combo.setCurrentText(val)
                combo.setEnabled(not ro)
                combo.currentTextChanged.connect(lambda v, n=p.name: self._set(n, v, rebuild=True))
                t.setCellWidget(r, 1, combo)
            elif p.type == "color":
                btn = QPushButton(val)
                btn.setStyleSheet(f"background:{val};")
                btn.clicked.connect(lambda _=False, n=p.name, b=btn: self._pick_color(n, b))
                t.setCellWidget(r, 1, btn)
            else:
                it = QTableWidgetItem(str(val))
                if ro:
                    it.setFlags(Qt.ItemIsEnabled)
                it.setData(Qt.UserRole, p.name)
                t.setItem(r, 1, it)
            cw = t.cellWidget(r, 1)
            (cw.setToolTip if cw is not None else (t.item(r, 1).setToolTip if t.item(r, 1) else (lambda _x: None)))(
                f"<b>{p.title}</b><br>{tip}")
            if t.columnCount() > 2:
                txt, ok = evaluate_display(p, val, self.ev)
                ev_it = QTableWidgetItem(txt)
                ev_it.setFlags(Qt.ItemIsEnabled)
                if not ok:
                    ev_it.setForeground(QBrush(QColor("#c0392b")))
                t.setItem(r, 2, ev_it)
        self._loading = False

    def _pick_color(self, name: str, btn: QPushButton) -> None:
        c = QColorDialog.getColor(QColor(self.values.get(name, "#808080")), self)
        if c.isValid():
            btn.setText(c.name())
            btn.setStyleSheet(f"background:{c.name()};")
            self._set(name, c.name())

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        name = item.data(Qt.UserRole)
        if name:
            if not self._offer_variables(name, item):
                return
            self._set(name, item.text(), rebuild=False)
            # refresh evaluated column
            r = item.row()
            p = next(p for p in self.props if p.name == name)
            if self.table.columnCount() > 2:
                txt, ok = evaluate_display(p, item.text(), self.ev)
                self._loading = True
                ev_it = QTableWidgetItem(txt)
                ev_it.setFlags(Qt.ItemIsEnabled)
                if not ok:
                    ev_it.setForeground(QBrush(QColor("#c0392b")))
                self.table.setItem(r, 2, ev_it)
                self._loading = False

    def refresh_evaluated(self, ev: Evaluator | None = None) -> None:
        """Recompute the Evaluated Value column of every row (with a new evaluator if given), e.g. after a
        design variable changed that other values refer to."""
        if ev is not None:
            self.ev = ev
        if self.table.columnCount() <= 2:
            return
        self._loading = True
        try:
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 1)
                name = it.data(Qt.UserRole) if it is not None else None
                p = next((p for p in self.props if p.name == name), None)
                if p is None:
                    continue
                txt, ok = evaluate_display(p, self.values.get(p.name, ""), self.ev)
                ev_it = QTableWidgetItem(txt)
                ev_it.setFlags(Qt.ItemIsEnabled)
                if not ok:
                    ev_it.setForeground(QBrush(QColor("#c0392b")))
                self.table.setItem(r, 2, ev_it)
        finally:
            self._loading = False

    def _offer_variables(self, name: str, item: QTableWidgetItem) -> bool:
        """Add Variable: a new name typed into a value field is created as a variable whose value is
        the field's previous value (empty if there was none). Cancel restores the old text."""
        p = next((p for p in self.props if p.name == name), None)
        if (p is None or self.ev is None or ADD_VARIABLE_HOOK is None
                or p.type not in ("length", "point", "vector", "points", "freq", "angle", "int", "expr", "float")):
            return True
        from PySide6.QtWidgets import QInputDialog
        probe = Evaluator(dict(self.ev.variables), self.ev.model_unit)
        old = self.values.get(name, "")
        for var in _unknown_names(item.text(), probe):
            default = _old_component(old, item.text(), var)
            if _IDENT.fullmatch(default or "") and default not in self.ev.variables:
                default = ""
            val, ok = QInputDialog.getText(self, "Add Variable",
                                           f"'{var}' is not defined.\nCreate design variable {var} with value:",
                                           text=default)
            if not ok or not val.strip():
                self._loading = True
                item.setText(old)
                self._loading = False
                return False
            ADD_VARIABLE_HOOK(var, val.strip())
            self.ev.variables[var] = val.strip()
            self.ev._cache.clear()
        return True

    def _set(self, name: str, value: str, rebuild: bool = False) -> None:
        if self.values.get(name) == value:
            return
        self.values[name] = value
        self.changed.emit(name, value)
        if rebuild and any((p.visible_if and p.visible_if[0] == name) or (p.enabled_if and p.enabled_if[0] == name)
                           for p in self.props):
            # deferred: rebuilding now would delete the combo box / check box whose signal is still running,
            # which crashes Qt on Windows (e.g. switching Auto Open in the Solution Type dialog)
            from PySide6.QtCore import QTimer
            import shiboken6
            QTimer.singleShot(0, lambda: shiboken6.isValid(self.table) and self.rebuild())


def _wrap(w: QWidget) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(4, 0, 0, 0)
    lay.addWidget(w)
    lay.addStretch()
    return box


class TabbedPropertyGrid(QTabWidget):
    """Splits props into tabs by ``Prop.tab`` (General / Options / Advanced ...). A tab none of whose props is
    visible is hidden (e.g. the settings of the optimizers that are not chosen)."""

    changed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grids: dict[str, PropertyGrid] = {}

    def set(self, props: list[Prop], values: dict[str, str], ev: Evaluator | None = None, **kw) -> None:
        self.clear()
        self.grids = {}
        tabs: list[str] = []
        for p in props:
            if p.tab not in tabs:
                tabs.append(p.tab)
        for tab in tabs:
            g = PropertyGrid(self)
            g.set([p for p in props if p.tab == tab], values, ev, **kw)
            g.changed.connect(self._on_changed)
            self.grids[tab] = g
            self.addTab(g, tab)
        self.update_tabs()

    def update_tabs(self) -> None:
        """Show only the tabs with at least one visible prop; the current tab stays when it is still shown."""
        for i in range(self.count()):
            g = self.widget(i)
            if not hasattr(g, "props"):
                continue                    # a tab of its own (the Modes tab of a port): always shown
            self.setTabVisible(i, any(is_visible(p, g.values) for p in g.props))
        if self.count() and not self.isTabVisible(self.currentIndex()):
            shown = [i for i in range(self.count()) if self.isTabVisible(i)]
            if shown:
                self.setCurrentIndex(shown[0])

    def _on_changed(self, name: str, value: str) -> None:
        # other tabs may depend on this value (visibility)
        for g in self.grids.values():
            if g is not self.sender() and any(p.visible_if and p.visible_if[0] == name for p in g.props):
                g.rebuild()
        if any(p.visible_if and p.visible_if[0] == name for g in self.grids.values() for p in g.props):
            # deferred like PropertyGrid.rebuild: the combo box that changed the value is still emitting
            from PySide6.QtCore import QTimer
            import shiboken6
            QTimer.singleShot(0, lambda: shiboken6.isValid(self) and self.update_tabs())
        self.changed.emit(name, value)
