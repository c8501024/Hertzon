"""Modeler > PCB Stackup: graphical editor of the layer stack of a design.

Left the cross-section (schematic or true scale, with a z ruler), right the top view of the selected layer
with rulers, the detected size and a two-click measurement, below the table and the list of problems.
Every value shows where it came from; problems carry suggestions that change nothing until accepted.
"""
from __future__ import annotations

import copy
import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QRadioButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..model import stackup as S
from ..model.materials import Material

KIND_COLORS = {S.COPPER: "#c8892b"}
DIEL_COLORS = {S.CORE: "#6f9a5a", S.PREPREG: "#a9c48a", "": "#8fb070"}
LEVEL_COLORS = {"error": "#c0392b", "warning": "#d68910", "info": "#1f618d"}
LEVEL_ICONS = {"error": "✖", "warning": "⚠", "info": "ℹ"}
ORIGIN_TEXT = {"file": "file", "assumed": "assumed", "template": "template", "suggestion": "suggestion",
               "user": "user"}
UNITS = {"mm": 1e-3, "um": 1e-6, "mil": 25.4e-6}
COLS = ["Name", "Type", "Thickness", "Material", "εr", "tan δ", "Model", "Fill (solid copper)", "Origin"]
TEMPLATES = [(n, t) for n in (2, 4, 6, 8) for t in ("0.8mm", "1.0mm", "1.6mm")] + [(1, "1.6mm")]


def _fmt(v: float, unit: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "?"
    x = v / UNITS[unit]
    return f"{x:.4g} {'µm' if unit == 'um' else unit}"


def nice_step(span: float, ticks: int = 8) -> float:
    """1, 2 or 5 times a power of ten, giving about ``ticks`` ticks over ``span``."""
    if span <= 0:
        return 1.0
    raw = span / ticks
    p = 10 ** math.floor(math.log10(raw))
    for k in (1, 2, 5, 10):
        if raw <= k * p:
            return k * p
    return 10 * p


def ruler_unit(step: float) -> tuple[str, float]:
    """Unit that suits the tick step: um, mm or m."""
    if step < 1e-4:
        return "µm", 1e-6
    if step < 0.1:
        return "mm", 1e-3
    return "m", 1.0


def size_text(w: float, h: float) -> str:
    """Board size in the unit boards are given in: mm (um below 1 mm, m from 1 m)."""
    big = max(w, h)
    unit, f = ("µm", 1e-6) if big < 1e-3 else (("m", 1.0) if big >= 1.0 else ("mm", 1e-3))
    return f"{w / f:.4g} × {h / f:.4g} {unit}"


# ================================================================================================
# cross-section
# ================================================================================================
class CrossSection(QWidget):
    selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 320)
        self.st: S.Stackup | None = None
        self.ev = None
        self.issues: list[S.Issue] = []
        self.sel = -1
        self.true_scale = False
        self.unit = "mm"
        self._rects: list[tuple[int, QRectF]] = []

    def set_data(self, st, ev, issues, sel):
        self.st, self.ev, self.issues, self.sel = st, ev, issues, sel
        self.update()

    def _heights(self, zs, avail: float) -> list[float]:
        ts = [0.0 if math.isnan(z.thickness) else z.thickness for z in zs]
        if self.true_scale:
            tot = sum(ts) or 1.0
            return [avail * t / tot for t in ts]
        fixed = {S.COPPER: 12.0}
        h = [fixed.get(z.layer.kind, 0.0) for z in zs]
        free = max(avail - sum(h), 20.0)
        dts = [t for z, t in zip(zs, ts) if z.layer.kind == S.DIELECTRIC]
        tot = sum(dts) or 1.0
        n = max(1, len(dts))
        return [x if z.layer.kind != S.DIELECTRIC else max(18.0, free * (0.5 * t / tot + 0.5 / n))
                for z, t, x in zip(zs, ts, h)]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#ffffff"))
        self._rects = []
        if not self.st or not self.st.layers:
            p.drawText(self.rect(), Qt.AlignCenter, "No layers")
            return
        zs = S.evaluate(self.st, self.ev)
        top, left = 14.0, 70.0
        avail = self.height() - 2 * top
        hs = self._heights(zs, avail)
        k = avail / max(sum(hs), 1.0)
        hs = [h * k for h in hs]
        w = max(120.0, self.width() * 0.42)
        issues = {}
        for i in self.issues:
            if i.layer >= 0:
                prev = issues.get(i.layer)
                order = ("info", "warning", "error")
                if prev is None or order.index(i.level) > order.index(prev):
                    issues[i.layer] = i.level
        y = top
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
        p.setFont(small)
        for z, h in zip(zs, hs):
            L = z.layer
            r = QRectF(left, y, w, max(h, 1.0))
            col = KIND_COLORS.get(L.kind) or DIEL_COLORS.get(L.subtype, DIEL_COLORS[""])
            p.fillRect(r, QColor(col))
            if any(L.origin_of(f) == "assumed" for f in S.FIELDS if getattr(L, f)
                   and not (L.kind == S.COPPER and f == "material")):
                p.fillRect(r, QBrush(QColor(255, 255, 255, 150), Qt.BDiagPattern))
            lvl = issues.get(z.index)
            if z.index == self.sel:
                p.setPen(QPen(QColor("#1565c0"), 3))
            elif lvl in ("error", "warning"):
                p.setPen(QPen(QColor(LEVEL_COLORS[lvl]), 2))
            else:
                p.setPen(QPen(QColor("#555555"), 0.5))
            p.drawRect(r)
            self._rects.append((z.index, r))
            # label right of the stack
            p.setPen(QColor("#202020"))
            txt = f"{L.name}   {_fmt(z.thickness, self.unit)}"
            if L.kind != S.COPPER:
                er, td = S.layer_er_tand(L, self.ev)
                txt += f"   εr {er:g}" if er is not None else "   εr ?"
            if lvl:
                txt = LEVEL_ICONS[lvl] + " " + txt
            if h >= 10 or not self.true_scale:   # true scale: thin layers would overwrite each other
                p.drawText(QRectF(left + w + 8, y + h / 2 - 9, self.width() - left - w - 10, 18),
                           Qt.AlignVCenter | Qt.AlignLeft, txt)
            y += h
        # z ruler: heights of the layer boundaries (true heights in every scale)
        p.setPen(QColor("#303030"))
        p.drawLine(QPointF(left - 6, top), QPointF(left - 6, y))
        y = top
        bounds = [(top, zs[0].z1)]
        for z, h in zip(zs, hs):
            y += h
            bounds.append((y, z.z0))
        last = -1e9
        for yy, zz in bounds:
            p.drawLine(QPointF(left - 10, yy), QPointF(left - 6, yy))
            if yy - last >= 11:
                p.drawText(QRectF(0, yy - 8, left - 12, 16), Qt.AlignVCenter | Qt.AlignRight,
                           f"{(0.0 if abs(zz) < 1e-12 else zz) / UNITS[self.unit]:.4g}")
                last = yy
        p.drawText(QRectF(0, 0, left - 8, 12), Qt.AlignRight, "z / " + ("µm" if self.unit == "um" else self.unit))

    def mousePressEvent(self, e):
        for i, r in self._rects:
            if r.contains(e.position()):
                self.selected.emit(i)
                return


# ================================================================================================
# top view with rulers and measurement
# ================================================================================================
class LayerView(QWidget):
    """Polygons (metres) of one layer with rulers in a unit that follows the zoom, the detected size and a
    two-click measurement. Wheel zooms, drag pans, two clicks measure."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.setMouseTracking(True)
        self.polys: list[tuple[np.ndarray, list[np.ndarray]]] = []
        self.outline: list[np.ndarray] = []
        self.title = ""
        self.color = QColor(KIND_COLORS[S.COPPER])
        self.scale = 1.0
        self.center = np.zeros(2)
        self.meas: list[np.ndarray] = []
        self._drag = None
        self._moved = False

    # ---- data ---------------------------------------------------------------------------------
    def set_shapes(self, polys, outline, title: str, color: str) -> None:
        self.polys, self.outline, self.title = polys, outline, title
        self.color = QColor(color)
        self.meas = []
        self.fit()

    def bbox(self):
        pts = [q for o, hs in self.polys for q in [o]] + list(self.outline)
        if not pts:
            return None
        a = np.vstack(pts)
        return a.min(0), a.max(0)

    def fit(self) -> None:
        bb = self.bbox()
        if bb is None:
            self.update()
            return
        lo, hi = bb
        span = np.maximum(hi - lo, 1e-9)
        self.center = (lo + hi) / 2
        self.scale = 0.8 * min((self.width() - 60) / span[0], (self.height() - 60) / span[1])
        self.update()

    # ---- coordinates --------------------------------------------------------------------------
    def to_screen(self, q) -> QPointF:
        cx, cy = self.width() / 2 + 15, self.height() / 2 - 10
        return QPointF(cx + (q[0] - self.center[0]) * self.scale, cy - (q[1] - self.center[1]) * self.scale)

    def to_model(self, pt) -> np.ndarray:
        cx, cy = self.width() / 2 + 15, self.height() / 2 - 10
        return np.array([self.center[0] + (pt.x() - cx) / self.scale, self.center[1] - (pt.y() - cy) / self.scale])

    def _path(self, outer, holes) -> QPainterPath:
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        for loop in [outer] + list(holes):
            if len(loop) < 2:
                continue
            path.moveTo(self.to_screen(loop[0]))
            for q in loop[1:]:
                path.lineTo(self.to_screen(q))
            path.closeSubpath()
        return path

    # ---- painting -----------------------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#20262b"))
        R = 28   # ruler width
        bb = self.bbox()
        if bb is None:
            p.setPen(QColor("#c8c8c8"))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                       "No geometry for this layer.\nRead the layers from the model, or import a board.")
            return
        for o in self.outline:
            p.setPen(QPen(QColor("#e0e0e0"), 1.2))
            p.setBrush(QColor(60, 90, 60, 120))
            p.drawPath(self._path(o, []))
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        for o, hs in self.polys:
            p.drawPath(self._path(o, hs))
        self._rulers(p, R)
        lo, hi = bb
        w, h = hi - lo
        txt = f"{self.title}   size {size_text(w, h)}"
        warn = max(w, h) < 2e-3 or max(w, h) > 1.0
        p.setPen(QColor("#ff8a80") if warn else QColor("#ffffff"))
        p.drawText(QRectF(R + 6, self.height() - R - 22, self.width() - R - 12, 18), Qt.AlignLeft, txt +
                   ("   ⚠ unusual board size, check the units" if warn else ""))
        # scale bar
        step = nice_step((self.width() - R) / self.scale, 4)
        unit, f = ruler_unit(step)
        x0, y0 = self.width() - 20 - step * self.scale, self.height() - R - 30
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + step * self.scale, y0))
        p.drawText(QRectF(x0 - 20, y0 - 18, step * self.scale + 40, 14), Qt.AlignCenter, f"{step / f:g} {unit}")
        # measurement
        if self.meas:
            p.setPen(QPen(QColor("#4fc3f7"), 1.5))
            pts = [self.to_screen(q) for q in self.meas]
            for s in pts:
                p.drawEllipse(s, 3, 3)
            if len(pts) == 2:
                p.drawLine(pts[0], pts[1])
                d = float(np.linalg.norm(self.meas[1] - self.meas[0]))
                u, fu = ruler_unit(d / 4)
                dx, dy = np.abs(self.meas[1] - self.meas[0]) / fu
                p.drawText(QPointF((pts[0].x() + pts[1].x()) / 2 + 6, (pts[0].y() + pts[1].y()) / 2 - 6),
                           f"{d / fu:.4g} {u}  (dx {dx:.4g}, dy {dy:.4g})")

    def _rulers(self, p: QPainter, R: int) -> None:
        p.fillRect(QRectF(0, self.height() - R, self.width(), R), QColor("#e8e8e8"))
        p.fillRect(QRectF(0, 0, R, self.height()), QColor("#e8e8e8"))
        p.setPen(QColor("#202020"))
        a = self.to_model(QPointF(R, self.height() - R))
        b = self.to_model(QPointF(self.width(), 0))
        step = nice_step(b[0] - a[0])
        unit, f = ruler_unit(step)
        x = math.ceil(a[0] / step) * step
        while x <= b[0]:
            s = self.to_screen((x, 0)).x()
            p.drawLine(QPointF(s, self.height() - R), QPointF(s, self.height() - R + 6))
            p.drawText(QRectF(s - 30, self.height() - R + 7, 60, 14), Qt.AlignCenter, f"{x / f:g}")
            x += step
        p.drawText(QRectF(self.width() - 40, self.height() - 14, 38, 14), Qt.AlignRight, unit)
        y = math.ceil(a[1] / step) * step
        while y <= b[1]:
            s = self.to_screen((0, y)).y()
            p.drawLine(QPointF(R - 6, s), QPointF(R, s))
            p.save()
            p.translate(R - 8, s)
            p.rotate(-90)
            p.drawText(QRectF(-30, -14, 60, 12), Qt.AlignCenter, f"{y / f:g}")
            p.restore()
            y += step
        p.fillRect(QRectF(0, self.height() - R, R, R), QColor("#d0d0d0"))

    # ---- mouse --------------------------------------------------------------------------------
    def wheelEvent(self, e):
        q = self.to_model(e.position())
        k = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.scale *= k
        self.center = q + (self.center - q) / k
        self.update()

    def mousePressEvent(self, e):
        self._drag = (e.position(), self.center.copy())
        self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons():
            d = e.position() - self._drag[0]
            if abs(d.x()) + abs(d.y()) > 3:
                self._moved = True
                self.center = self._drag[1] - np.array([d.x(), -d.y()]) / self.scale
                self.update()

    def mouseReleaseEvent(self, e):
        if not self._moved and e.button() == Qt.LeftButton:
            q = self.to_model(e.position())
            self.meas = [q] if len(self.meas) != 1 else self.meas + [q]
            self.update()
        self._drag = None

    def mouseDoubleClickEvent(self, _):
        self.meas = []
        self.fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()


# ================================================================================================
# dialog
# ================================================================================================
class _Mats:
    """Material lookup of the project plus the materials made in this dialog."""

    def __init__(self, project, extra: list[Material]):
        self.project, self.extra = project, extra

    def material(self, name):
        for m in self.extra:
            if m.name == name:
                return m
        return self.project.material(name)

    def all_materials(self):
        return list(self.extra) + self.project.all_materials()


class StackupDialog(QDialog):
    def __init__(self, parent, doc):
        super().__init__(parent)
        self.doc = doc
        self.project, self.des = doc.project, doc.design
        self.ev = self.des.evaluator(self.project)
        self.st: S.Stackup = copy.deepcopy(self.des.stackup) if self.des.stackup else S.template(2, "1.6mm")
        self.new_materials: list[Material] = []
        self.mats = _Mats(self.project, self.new_materials)
        self.issues: list[S.Issue] = []
        self.sel = 0
        self.model_layers = None             # copper geometry read from the model (Gerber export classification)
        self._filling = False
        self.setWindowTitle(f"PCB Stackup: {self.project.name} - {self.des.name}")
        self.resize(1250, 820)
        lay = QVBoxLayout(self)

        # ---- top bar --------------------------------------------------------------------------
        bar = QHBoxLayout()
        self.source = QLabel()
        bar.addWidget(self.source)
        bar.addStretch(1)
        bar.addWidget(QLabel("Board thickness (stated):"))
        self.nominal = QLineEdit(self.st.nominal_thickness)
        self.nominal.setPlaceholderText("e.g. 1.6mm")
        self.nominal.setMaximumWidth(90)
        self.nominal.editingFinished.connect(self._nominal_changed)
        bar.addWidget(self.nominal)
        self.total = QLabel()
        bar.addWidget(self.total)
        lay.addLayout(bar)

        bar = QHBoxLayout()
        tb = QToolButton()
        tb.setText("Templates")
        tb.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(tb)
        for n, t in TEMPLATES:
            menu.addAction(f"{n} layers, {t}", lambda n=n, t=t: self._template(n, t))
        tb.setMenu(menu)
        bar.addWidget(tb)
        for text, fn in (("Load job file...", self._load_job), ("Open...", self._open), ("Save...", self._save),
                         ("Fit to board thickness", self._fit_total), ("Mirror", self._mirror)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("Cross-section:"))
        self.r_schem, self.r_true = QRadioButton("schematic"), QRadioButton("true scale")
        self.r_schem.setChecked(True)
        g = QButtonGroup(self)
        for r in (self.r_schem, self.r_true):
            g.addButton(r)
            bar.addWidget(r)
            r.toggled.connect(self._refresh_views)
        bar.addWidget(QLabel("Unit:"))
        self.unit = QComboBox()
        self.unit.addItems(list(UNITS))
        self.unit.currentTextChanged.connect(self._refresh_views)
        bar.addWidget(self.unit)
        lay.addLayout(bar)

        # ---- views ----------------------------------------------------------------------------
        split = QSplitter(Qt.Vertical)
        top = QSplitter(Qt.Horizontal)
        self.cross = CrossSection()
        self.cross.selected.connect(self._select)
        top.addWidget(self.cross)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.view = LayerView()
        rl.addWidget(self.view, 1)
        hb = QHBoxLayout()
        self.read_model = QPushButton("Read layers from the model")
        self.read_model.setToolTip("Copper and board outline of the design, found as for the Gerber export")
        self.read_model.clicked.connect(self._read_model)
        hb.addWidget(self.read_model)
        hb.addWidget(QLabel("Click twice to measure, double-click to fit, wheel to zoom."))
        hb.addStretch(1)
        rl.addLayout(hb)
        top.addWidget(right)
        top.setSizes([560, 640])
        split.addWidget(top)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        hb = QHBoxLayout()
        for text, fn in (("Add copper", lambda: self._add(S.COPPER)), ("Add dielectric", lambda: self._add(S.DIELECTRIC)),
                         ("Delete", self._delete),
                         ("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1)),
                         ("New material...", self._new_material)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            hb.addWidget(b)
        hb.addStretch(1)
        bl.addLayout(hb)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for c, w in enumerate((150, 110, 90, 210, 60, 70, 70, 170)):
            self.table.setColumnWidth(c, w)
        self.table.itemChanged.connect(self._item_changed)
        self.table.currentCellChanged.connect(lambda r, *_: self._select(r) if r >= 0 else None)
        bl.addWidget(self.table, 2)
        hb = QHBoxLayout()
        self.issue_label = QLabel()
        hb.addWidget(self.issue_label)
        hb.addStretch(1)
        self.apply_btn = QPushButton("Apply suggestion")
        self.apply_btn.clicked.connect(self._apply_selected)
        hb.addWidget(self.apply_btn)
        self.confirm_btn = QPushButton("Confirm assumed values")
        self.confirm_btn.setToolTip("Mark the assumed values of the selected layer as checked by you")
        self.confirm_btn.clicked.connect(self._confirm)
        hb.addWidget(self.confirm_btn)
        bl.addLayout(hb)
        self.issue_list = QListWidget()
        self.issue_list.itemDoubleClicked.connect(lambda *_: self._apply_selected())
        self.issue_list.currentRowChanged.connect(self._issue_selected)
        bl.addWidget(self.issue_list, 1)
        split.addWidget(bottom)
        split.setSizes([430, 390])
        lay.addWidget(split, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._refresh()

    # ---- refresh ------------------------------------------------------------------------------
    def _refresh(self) -> None:
        self.issues = S.check(self.st, self.ev, self.mats)
        self._fill_table()
        self._fill_issues()
        self._refresh_views()
        tot = S.total_thickness(self.st, self.ev)
        unit = self.unit.currentText()
        txt = f"Sum {_fmt(tot, unit)}"
        try:
            nom = self.ev.length(self.st.nominal_thickness) if self.st.nominal_thickness.strip() else None
        except Exception:  # noqa: BLE001
            nom = None
        if nom:
            txt += f"  ({(tot - nom) / nom * 100:+.1f} %)"
        self.total.setText(txt)
        self.source.setText(f"Source: {self.st.source}" + (f" ({self.st.generator})" if self.st.generator else ""))
        errors = [i for i in self.issues if i.level == "error"]
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not errors)
        self.buttons.button(QDialogButtonBox.Ok).setToolTip("Fix the errors first" if errors else "")

    def _refresh_views(self, *_) -> None:
        self.cross.true_scale = self.r_true.isChecked()
        self.cross.unit = self.unit.currentText()
        self.cross.set_data(self.st, self.ev, self.issues, self.sel)
        self._show_layer()

    def _fill_table(self) -> None:
        self._filling = True
        t = self.table
        t.setRowCount(0)                     # drops the combo boxes of the last fill
        t.setRowCount(len(self.st.layers))
        flagged = {}
        for i in self.issues:
            if i.layer >= 0 and i.level != "info":
                flagged.setdefault((i.layer, i.field), i.level)
        for r, L in enumerate(self.st.layers):
            er, td = S.layer_er_tand(L, self.ev, self.mats)
            vals = [L.name, L.kind + (f" ({L.subtype})" if L.subtype else ""), L.thickness, L.material,
                    L.permittivity if L.permittivity else (f"{er:g}" if er is not None and L.kind != S.COPPER else ""),
                    L.loss_tangent if L.loss_tangent else (f"{td:g}" if td is not None and L.kind != S.COPPER else ""),
                    L.representation if L.kind == S.COPPER else "",
                    _fill_text(self.st, r) if L.kind == S.COPPER and L.representation == S.SOLID else "", ""]
            fields = ["name", "", "thickness", "material", "permittivity", "loss_tangent", "", "", ""]
            origins = sorted({L.origin_of(f) for f in S.FIELDS if getattr(L, f)})
            vals[8] = ", ".join(ORIGIN_TEXT.get(o, o) for o in origins)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                f = fields[c]
                if c in (1, 3, 6, 7, 8):
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if L.kind == S.COPPER and c in (4, 5):
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if c in (4, 5) and not getattr(L, f, "") and L.material:
                    it.setForeground(QBrush(QColor("#808080")))     # from the material
                if f and L.origin_of(f) == "assumed" and getattr(L, f, ""):
                    it.setBackground(QBrush(QColor("#fff3cd")))
                if (r, f) in flagged:
                    it.setBackground(QBrush(QColor("#f8d7da" if flagged[(r, f)] == "error" else "#ffe5b4")))
                t.setItem(r, c, it)
            t.setCellWidget(r, 3, self._material_combo(r, L))
            if L.kind == S.COPPER:
                cb = QComboBox()
                cb.addItems([S.SHEET, S.SOLID])
                cb.setCurrentText(L.representation)
                cb.currentTextChanged.connect(
                    lambda v, r=r: QTimer.singleShot(0, lambda: self._set(r, "representation", v)))
                t.setCellWidget(r, 6, cb)
            else:
                t.removeCellWidget(r, 6)
        if 0 <= self.sel < t.rowCount():
            t.selectRow(self.sel)
        self._filling = False

    def _material_combo(self, r: int, L: S.StackLayer) -> QComboBox:
        cb = QComboBox()
        cb.setEditable(False)
        if L.kind == S.COPPER:
            names = [m.name for m in self.mats.all_materials() if m.pec or _conductor(m)]
        else:
            names = [m.name for m in self.mats.all_materials() if not m.pec and not _conductor(m)
                     and m.name not in ("vacuum", "air")]
        cb.addItem("(own values)" if L.kind != S.COPPER else "", "")
        sug = S.suggest_materials(L, self.ev, self.mats) if L.kind != S.COPPER else []
        for n, why in sug:
            cb.addItem(f"★ {n}  ({why})", n)
        if sug:
            cb.insertSeparator(cb.count())
        for n in names:
            cb.addItem(n, n)
        idx = next((i for i in range(cb.count()) if cb.itemData(i) == L.material and i > 0), 0)
        if L.material and idx == 0:
            cb.addItem(f"{L.material} (unknown)", L.material)
            idx = cb.count() - 1
        cb.setCurrentIndex(idx)
        cb.activated.connect(lambda i, r=r, cb=cb: (lambda v: QTimer.singleShot(0, lambda: self._set(r, "material", v)))(
            cb.itemData(i) or ""))
        return cb

    def _fill_issues(self) -> None:
        self.issue_list.clear()
        n = {k: 0 for k in LEVEL_ICONS}
        for i in self.issues:
            n[i.level] += 1
            it = QListWidgetItem(f"{LEVEL_ICONS[i.level]}  {i.text}" + (f"   → {i.fix.label}" if i.fix else ""))
            it.setForeground(QBrush(QColor(LEVEL_COLORS[i.level])))
            self.issue_list.addItem(it)
        self.issue_label.setText(f"Problems:  ✖ {n['error']}   ⚠ {n['warning']}   ℹ {n['info']}"
                                 + ("   (double-click a suggestion to apply it)" if any(i.fix for i in self.issues)
                                    else ""))
        self.apply_btn.setEnabled(False)

    # ---- top view -----------------------------------------------------------------------------
    def _show_layer(self) -> None:
        if not (0 <= self.sel < len(self.st.layers)):
            self.view.set_shapes([], [], "", "#ffffff")
            return
        L = self.st.layers[self.sel]
        polys, outline = [], []
        if self.model_layers is not None:
            res = self.model_layers
            outline = [c.outer.polygon() for c in res.outline]
            cu = self.st.copper()
            if L.kind == S.COPPER and self.sel in cu:
                k = cu.index(self.sel)
                if k < len(res.layers):
                    polys = [(c.outer.polygon(), [h.polygon() for h in c.holes]) for c in res.layers[k].components]
        col = KIND_COLORS.get(L.kind) or DIEL_COLORS.get(L.subtype, DIEL_COLORS[""])
        self.view.set_shapes(polys, outline, L.name, col)

    def _read_model(self) -> None:
        from ..export import gerber as G
        try:
            res = G.run(self.project, self.des, G.Options(cs=self.st.cs, plane=self.st.plane, drills=False))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "PCB Stackup", f"The layers cannot be read from the model:\n{e}")
            return
        self.model_layers = res
        ncu = len(self.st.copper())
        if res.layers and len(res.layers) != ncu:
            QMessageBox.information(self, "PCB Stackup", f"The model has {len(res.layers)} copper layers, the "
                                    f"stackup {ncu}. They are matched from the top.")
        self._show_layer()

    # ---- edits --------------------------------------------------------------------------------
    def _select(self, i: int) -> None:
        if i == self.sel or not (0 <= i < len(self.st.layers)):
            return
        self.sel = i
        if self.table.currentRow() != i:
            self.table.selectRow(i)
        self.cross.set_data(self.st, self.ev, self.issues, self.sel)
        self._show_layer()

    def _set(self, r: int, f: str, v: str) -> None:
        L = self.st.layers[r]
        if getattr(L, f) == v:
            return
        if f == "material" and L.kind != S.COPPER and v:
            L.set("permittivity", "", "user")       # the material gives the values again
            L.set("loss_tangent", "", "user")
        L.set(f, v, "user")
        self._refresh()

    def _item_changed(self, it: QTableWidgetItem) -> None:
        if self._filling:
            return
        r, c = it.row(), it.column()
        f = {0: "name", 2: "thickness", 4: "permittivity", 5: "loss_tangent"}.get(c)
        if not f:
            return
        v = it.text().strip()
        L = self.st.layers[r]
        if f in ("permittivity", "loss_tangent") and L.material:
            mat = _safe(self.mats, L.material)
            if mat is not None and v == str(getattr(mat, f)):
                v = ""
        self._set(r, f, v)

    def _nominal_changed(self) -> None:
        v = self.nominal.text().strip()
        if v != self.st.nominal_thickness:
            self.st.nominal_thickness = v
            self._refresh()

    def _add(self, kind: str) -> None:
        i = max(self.sel, 0)
        L = S.StackLayer({S.COPPER: "New copper", S.DIELECTRIC: "New dielectric"}[kind],
                         kind, S.DEFAULT_THICKNESS.get(kind, "0.2mm"))
        if kind == S.COPPER:
            L.material = S.DEFAULT_COPPER_MATERIAL
        elif kind == S.DIELECTRIC:
            L.material, L.subtype = S.DEFAULT_DIELECTRIC, S.PREPREG
        self.st.layers.insert(i + 1 if self.st.layers else 0, L)
        self.sel = i + 1 if len(self.st.layers) > 1 else 0
        self._refresh()

    def _delete(self) -> None:
        if 0 <= self.sel < len(self.st.layers):
            del self.st.layers[self.sel]
            self.sel = min(self.sel, len(self.st.layers) - 1)
            self._refresh()

    def _move(self, d: int) -> None:
        i, j = self.sel, self.sel + d
        if 0 <= i < len(self.st.layers) and 0 <= j < len(self.st.layers):
            Ls = self.st.layers
            Ls[i], Ls[j] = Ls[j], Ls[i]
            self.sel = j
            self._refresh()

    def _mirror(self) -> None:
        """Make the lower half a mirror image of the upper half (symmetric board)."""
        Ls = self.st.layers
        n = len(Ls)
        for k in range(n // 2):
            a, b = Ls[k], Ls[n - 1 - k]
            if a.kind == b.kind:
                for f in ("thickness", "material", "permittivity", "loss_tangent"):
                    b.set(f, getattr(a, f), "user")
                b.subtype = a.subtype
        self._refresh()

    def _template(self, n: int, t: str) -> None:
        if self.st.layers and QMessageBox.question(self, "PCB Stackup", "Replace the current stackup by the "
                                                   f"template ({n} layers, {t})?") != QMessageBox.Yes:
            return
        keep = (self.st.cs, self.st.plane)
        self.st = S.template(n, t)
        self.st.cs, self.st.plane = keep
        self.nominal.setText(self.st.nominal_thickness)
        self.sel = 0
        self._refresh()

    def _fit_total(self) -> None:
        try:
            nom = self.ev.length(self.st.nominal_thickness)
        except Exception:  # noqa: BLE001
            QMessageBox.information(self, "PCB Stackup", "Enter the stated board thickness first.")
            return
        fx = S.scale_dielectrics(self.st, self.ev, nom)
        if fx:
            S.apply_fix(self.st, fx)
            self._refresh()

    def _apply_selected(self) -> None:
        r = self.issue_list.currentRow()
        if 0 <= r < len(self.issues) and self.issues[r].fix:
            S.apply_fix(self.st, self.issues[r].fix)
            self._refresh()

    def _issue_selected(self, r: int) -> None:
        ok = 0 <= r < len(self.issues)
        self.apply_btn.setEnabled(ok and self.issues[r].fix is not None)
        if ok and self.issues[r].layer >= 0:
            self._select(self.issues[r].layer)

    def _confirm(self) -> None:
        if 0 <= self.sel < len(self.st.layers):
            L = self.st.layers[self.sel]
            for f, o in list(L.origin.items()):
                if o == "assumed":
                    L.origin[f] = "user"
            self._refresh()

    def _new_material(self) -> None:
        if not (0 <= self.sel < len(self.st.layers)) or self.st.layers[self.sel].kind == S.COPPER:
            QMessageBox.information(self, "PCB Stackup", "Select a dielectric layer first.")
            return
        from .dialogs import MaterialEditDialog
        L = self.st.layers[self.sel]
        er, td = S.layer_er_tand(L, self.ev, self.mats)
        base = (L.file_material or L.name).replace("/", "_").replace(".", "_")
        name = base
        taken = {m.name for m in self.mats.all_materials()}
        k = 1
        while name in taken:
            k += 1
            name = f"{base}_{k}"
        m = Material(name=name, permittivity=f"{er:g}" if er is not None else "4.4",
                     loss_tangent=f"{td:g}" if td is not None else "0.02", color=DIEL_COLORS.get(L.subtype, "#8fb070"),
                     description=f"from stackup layer {L.name}")
        dlg = MaterialEditDialog(m, self)
        if dlg.exec() != QDialog.Accepted:
            return
        m = dlg.result_material()
        if not m.name or m.name in taken:
            QMessageBox.warning(self, "PCB Stackup", f"The material name '{m.name}' is empty or taken.")
            return
        self.new_materials.append(m)
        L.set("material", m.name, "user")
        L.set("permittivity", "", "user")
        L.set("loss_tangent", "", "user")
        self._refresh()

    # ---- files --------------------------------------------------------------------------------
    def _load_job(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Gerber job file", "", "Gerber job file (*.gbrjob);;All (*)")
        if path:
            self.load_job(path)

    def load_job(self, path: str) -> list[S.Issue]:
        try:
            st, notes = S.read_gbrjob(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "PCB Stackup", f"Cannot read '{path}':\n{e}")
            return []
        st.cs, st.plane = self.st.cs, self.st.plane
        self.st = st
        self.nominal.setText(st.nominal_thickness)
        self.sel = 0
        self._refresh()
        if notes:
            QMessageBox.information(self, "PCB Stackup", "\n".join(LEVEL_ICONS[n.level] + " " + n.text for n in notes))
        return notes

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open stackup", "", "Hertzon stackup (*.hzstack)")
        if path:
            try:
                self.st = S.Stackup.load(path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "PCB Stackup", str(e))
                return
            self.nominal.setText(self.st.nominal_thickness)
            self.sel = 0
            self._refresh()

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save stackup", "stackup.hzstack", "Hertzon stackup (*.hzstack)")
        if path:
            self.st.save(path)

    def result(self) -> tuple[S.Stackup, list[Material]]:
        return self.st, list(self.new_materials)


def _fill_text(st: S.Stackup, i: int) -> str:
    f = S.fill_of(st, i)
    own = " (set)" if st.layers[i].fill else ""
    return (f[6:] + " (its values)" if f.startswith("layer:") else f) + own


def _conductor(m) -> bool:
    from ..model.materials import is_conductor
    return is_conductor(m)


def _safe(mats, name):
    try:
        return mats.material(name)
    except KeyError:
        return None
