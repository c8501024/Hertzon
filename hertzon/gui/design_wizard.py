"""Tools > Design Wizard: one window for every structure of the catalogue (model/structures).

Step 1 shows the catalogue as cards by category, with a search field and the description of the selected card;
planned structures are shown greyed out. Step 2 takes the specification and the substrate, shows the synthesized
start values while typing, a top view of the 3D design and what will be created: the circuit, its 3D design and
the multi-fidelity optimization. Structures with their own designer (patch, hairpin filter, hairpin diplexer) open it instead.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..model import structures as ST
from ..model.materials import is_conductor
from .filter_tool import _float
from .theme import GOLD, GOLD_BG, INK, LINE, MUTED

OFF = "#a8adb8"
OK, OK_BG = "#2f7d4f", "#e3f2e8"
PLAN_BG = "#eceef2"
SETTINGS = "wizard/"                 # QSettings prefix: substrate, last structure, show planned


def badge(s: ST.Structure) -> tuple[str, str, str]:
    """(text, colour, background) of the status badge of a card."""
    if s.status == ST.AVAILABLE:
        return "Available", OK, OK_BG
    if s.status == ST.IN_PROGRESS:
        return "In progress", "#9a6c14", GOLD_BG
    return f"Planned · {getattr(s, 'priority', 1)}", MUTED, PLAN_BG


def _svg(p: QPainter, svg: str, rect: QRectF, opacity: float = 1.0) -> None:
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:      # pragma: no cover
        return
    if not svg:
        return
    r = QSvgRenderer(QByteArray(svg.encode()))
    p.setOpacity(opacity)
    r.render(p, rect)
    p.setOpacity(1.0)


class Card(QAbstractButton):
    """A checkable card: picture, title, one line, status badge."""
    doubleClicked = Signal()

    def __init__(self, s: ST.Structure, parent=None):
        super().__init__(parent)
        self.s = s
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"{s.title} ({badge(s)[0]})")
        self.setToolTip(f"{s.title}: {s.description}" + (f"\nNeeds {s.needs}" if s.needs else ""))

    def sizeHint(self) -> QSize:
        return QSize(190, 158)

    def mouseDoubleClickEvent(self, e) -> None:
        self.setChecked(True)
        self.doubleClicked.emit()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        sel = self.isChecked()
        usable = self.s.status == ST.AVAILABLE
        p.setPen(QPen(QColor(GOLD if sel else LINE), 2 if sel else 1.5))
        p.setBrush(QColor(GOLD_BG if sel else "#ffffff"))
        p.drawRoundedRect(r, 8, 8)
        if self.hasFocus():
            p.setPen(QPen(QColor(GOLD), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(3, 3, -3, -3), 6, 6)
        pic_h = 74
        pic = QRectF(r.center().x() - pic_h * 0.75, r.top() + 10, pic_h * 1.5, pic_h)
        _svg(p, ST.icon(self.s), pic, 1.0 if usable else 0.45)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(INK if usable else OFF))
        y = pic.bottom() + 6
        p.drawText(QRectF(r.left() + 10, y, r.width() - 20, 20), Qt.AlignLeft | Qt.AlignVCenter, self.s.title)
        f.setBold(False)
        f.setPointSizeF(f.pointSizeF() * 0.92)
        p.setFont(f)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(r.left() + 10, y + 20, r.width() - 20, r.bottom() - y - 22),
                   Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.s.description)
        text, fg, bg = badge(self.s)
        f.setPointSizeF(f.pointSizeF() * 0.9)
        f.setBold(True)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 12
        b = QRectF(r.right() - w - 6, r.top() + 6, w, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(b, 8, 8)
        p.setPen(QColor(fg))
        p.drawText(b, Qt.AlignCenter, text)
        p.end()


class Picture(QWidget):
    """A large picture of the selected structure (step 1, detail panel)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.svg = ""
        self.setMinimumHeight(150)

    def set(self, svg: str) -> None:
        self.svg = svg
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor("#cfe0f5"), 1))
        p.setBrush(QColor("#e9f1fb"))
        p.drawRoundedRect(r, 6, 6)
        h = min(r.height() - 12, (r.width() - 12) / 1.5)
        _svg(p, self.svg, QRectF(r.center().x() - h * 0.75, r.center().y() - h / 2, 1.5 * h, h))
        p.end()


def _num(v: float, unit: str) -> str:
    return f"{v:.4g} {unit}".strip()


def goal_text(goals: list[dict]) -> str:
    """The goals as short lines; a >= and <= pair on the same quantity and range becomes one band."""
    import html
    lines, i = [], 0
    while i < len(goals):
        g = goals[i]
        rng = f"{g['start']}\u2013{g['stop']}".replace("GHz", "")
        nxt = goals[i + 1] if i + 1 < len(goals) else None
        calc = html.escape(g["calc"])
        if nxt and nxt["calc"] == g["calc"] and nxt["start"] == g["start"] and nxt["stop"] == g["stop"] and \
                {g["cond"], nxt["cond"]} == {">=", "<="}:
            lo, hi = sorted((float(g["value"]), float(nxt["value"])))
            text = f"{lo:g} \u2264 <code>{calc}</code> \u2264 {hi:g}"
            i += 2
        else:
            cond = {"<=": "\u2264", ">=": "\u2265"}.get(g["cond"], html.escape(g["cond"]))
            text = f"<code>{calc}</code> {cond} {g['value']}"
            i += 1
        lines.append(f"{text} <span style='color:{MUTED}'>({rng} GHz)</span>")
    return "<br>".join(lines)


def _section(text: str) -> QLabel:
    t = QLabel(text.upper())
    t.setStyleSheet(f"color: {MUTED}; font-weight: 600; letter-spacing: 1px;")
    return t


def _primary(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(f"QPushButton {{ background: {GOLD}; color: #ffffff; font-weight: 600; "
                    "border: 1px solid #b2822a; border-radius: 4px; padding: 5px 16px; }"
                    "QPushButton:disabled { background: #d9dce2; border-color: #d9dce2; }")
    return b


class DesignWizard(QDialog):
    """After exec(): result (model.structures.Result) to insert, run (the optimization to start right away), or
    designer (the name of a main window method that opens a structure's own designer)."""

    def __init__(self, parent, project, settings=None, start: str = ""):
        super().__init__(parent)
        self.project = project
        self.settings = settings
        self.result: ST.Result | None = None
        self.run = ""
        self.designer = ""
        self.sel: ST.Structure | None = None
        self.cat = "all"
        self.setWindowTitle("Design Wizard")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.steps = QLabel()
        self.steps.setStyleSheet(f"background: #ffffff; border-bottom: 1px solid {LINE}; padding: 9px 20px;")
        root.addWidget(self.steps)
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._page_choose())
        self.stack.addWidget(self._page_spec())
        first = start or (settings.value(SETTINGS + "last", "branchline") if settings is not None else "branchline")
        self.select(next((s for s in ST.CATALOG if s.key == first), ST.CATALOG[0]))
        self._show(0)
        self.resize(1180, 720)

    # ---- step 1 -------------------------------------------------------------------------------------------
    def _page_choose(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        side = QFrame()
        side.setObjectName("WizSide")
        side.setStyleSheet(f"QFrame#WizSide {{ background: #ffffff; border-right: 1px solid {LINE}; }}")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 14, 10, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: coupler, 3 dB, filter ...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        sl.addWidget(self.search)
        self.cats = QListWidget()
        self.cats.setFrameShape(QFrame.NoFrame)
        self.cats.setStyleSheet(f"QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}"
                                f"QListWidget::item:selected {{ background: {GOLD_BG}; color: {INK}; }}")
        self.cats.currentRowChanged.connect(self._cat_changed)
        sl.addWidget(self.cats, 1)
        body.addWidget(side)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.grid_host = QWidget()
        self.grid = QVBoxLayout(self.grid_host)
        self.grid.setContentsMargins(18, 14, 18, 14)
        self.grid.setSpacing(14)
        self.scroll.setWidget(self.grid_host)
        body.addWidget(self.scroll, 1)

        det = QFrame()
        det.setObjectName("WizDetail")
        det.setStyleSheet(f"QFrame#WizDetail {{ background: #ffffff; border-left: 1px solid {LINE}; }}")
        det.setFixedWidth(300)
        dl = QVBoxLayout(det)
        dl.setContentsMargins(16, 16, 16, 16)
        dl.setSpacing(10)
        self.pic = Picture()
        dl.addWidget(self.pic)
        self.d_title = QLabel()
        f = self.d_title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.d_title.setFont(f)
        self.d_title.setWordWrap(True)
        dl.addWidget(self.d_title)
        self.d_text = QLabel()
        self.d_text.setWordWrap(True)
        self.d_text.setTextFormat(Qt.RichText)
        dl.addWidget(self.d_text)
        dl.addStretch(1)
        body.addWidget(det)
        lay.addLayout(body, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(20, 10, 20, 12)
        self.show_planned = QCheckBox("Show planned structures")
        self.show_planned.setChecked(self.settings.value(SETTINGS + "planned", True, type=bool)
                                     if self.settings is not None else True)
        self.show_planned.toggled.connect(self.fill)
        bar.addWidget(self.show_planned)
        bar.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bar.addWidget(cancel)
        self.next_btn = _primary("Next")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        bar.addWidget(self.next_btn)
        lay.addLayout(bar)
        self._fill_cats()
        return page

    def _visible(self) -> list[ST.Structure]:
        q = self.search.text().strip().lower()
        out = []
        for s in ST.CATALOG:
            if not self.show_planned.isChecked() and s.status != ST.AVAILABLE:
                continue
            if q and q not in f"{s.title} {s.description} {s.use} {s.key}".lower():
                continue
            out.append(s)
        return out

    def _fill_cats(self) -> None:
        self.cats.blockSignals(True)
        self.cats.clear()
        vis = self._visible()
        rows = [("all", "All structures", len(vis))] + \
            [(k, label, sum(s.category == k for s in vis)) for k, label in ST.CATEGORIES]
        for key, label, n in rows:
            it = QListWidgetItem(f"{label}   {n}")
            it.setData(Qt.UserRole, key)
            self.cats.addItem(it)
            if key == self.cat:
                self.cats.setCurrentItem(it)
        self.cats.blockSignals(False)

    def _cat_changed(self, row: int) -> None:
        it = self.cats.item(row)
        if it is not None:
            self.cat = it.data(Qt.UserRole)
            self.fill()

    def fill(self, *_a) -> None:
        self._fill_cats()
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        vis = self._visible()
        self.card_widgets: list[Card] = []
        for key, label in ST.CATEGORIES:
            if self.cat not in ("all", key):
                continue
            items = [s for s in vis if s.category == key]
            if not items:
                continue
            sec = QWidget()
            sl = QVBoxLayout(sec)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(8)
            sl.addWidget(_section(label))
            g = QGridLayout()
            g.setSpacing(10)
            for i, s in enumerate(items):
                c = Card(s)
                c.setChecked(s is self.sel)
                c.clicked.connect(lambda _=False, s=s: self.select(s))
                c.doubleClicked.connect(self._next)
                g.addWidget(c, i // 3, i % 3)
                self.card_widgets.append(c)
            for col in range(3):
                g.setColumnStretch(col, 1)
            sl.addLayout(g)
            self.grid.addWidget(sec)
        if not self.card_widgets:
            self.grid.addWidget(QLabel(f"No structure matches “{self.search.text()}”."))
        self.grid.addStretch(1)

    def select(self, s: ST.Structure) -> None:
        self.sel = s
        for c in getattr(self, "card_widgets", []):
            c.setChecked(c.s is s)
        if not getattr(self, "card_widgets", None):
            self.fill()
        self.pic.set(ST.icon(s))
        self.d_title.setText(s.title)
        text, fg, _ = badge(s)
        if s.designer:
            creates = "Its own designer opens (more options)"
        elif s.category == "antenna":
            creates = "3D design with far field and an optimization"
        else:
            creates = "Circuit, 3D layout and a multi-fidelity optimization"
        rows = [("Status", f"<span style='color:{fg}; font-weight:600'>{text}</span>"), ("Ports", str(s.ports)),
                ("Typical use", s.use), ("Creates", creates)]
        if s.needs:
            rows.append(("Needs first", s.needs))
        self.d_text.setText(f"<p>{s.description}.</p><table cellspacing='0' cellpadding='3'>" + "".join(
            f"<tr><td style='color:{MUTED}; padding-right:10px' valign='top'>{a}</td><td>{b}</td></tr>"
            for a, b in rows) + "</table>")
        self.next_btn.setEnabled(s.status == ST.AVAILABLE)
        self.next_btn.setText("Open Designer" if s.designer else "Next")

    def _next(self) -> None:
        s = self.sel
        if s is None or s.status != ST.AVAILABLE:
            return
        self._remember()
        if s.designer:
            self.designer = s.designer
            self.accept()
            return
        self._build_spec(s)
        self._show(1)

    # ---- step 2 -------------------------------------------------------------------------------------------
    def _page_spec(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(14, 12, 14, 8)
        body.setSpacing(14)

        left = QVBoxLayout()
        self.spec_box = QGroupBox("Specification")
        self.spec_form = QFormLayout(self.spec_box)
        left.addWidget(self.spec_box)
        g = self.sub_box = QGroupBox("Substrate")
        f = QFormLayout(g)
        self.mat = QComboBox()
        mats = [m for m in self.project.all_materials() if not m.pec and not is_conductor(m)
                and _float(m.permittivity) > 1]
        self.mats = {m.name: m for m in mats}
        self.mat.addItems(sorted(self.mats))
        self.mat_info = QLabel()
        self.mat_info.setStyleSheet(f"color: {MUTED};")
        sub = ST.Substrate()
        self.h = self._spin(sub.h, 0.01, 50, 4, " mm")
        self.t = self._spin(sub.t, 0, 500, 1, " um")
        self.dmin = self._spin(sub.min_feature, 0.001, 10, 3, " mm")
        self.dmin.setToolTip("Smallest width or gap the optimization may reach (manufacturing)")
        if self.settings is not None:
            self.mat.setCurrentText(self.settings.value(SETTINGS + "material", sub.material))
            for w, k in ((self.h, "h"), (self.t, "t"), (self.dmin, "dmin")):
                w.setValue(float(self.settings.value(SETTINGS + k, w.value())))
        elif sub.material in self.mats:
            self.mat.setCurrentText(sub.material)
        self.mat.currentTextChanged.connect(self.timer.start)
        f.addRow("Material", self.mat)
        f.addRow("", self.mat_info)
        f.addRow("Height h", self.h)
        f.addRow("Copper thickness", self.t)
        f.addRow("Smallest width or gap", self.dmin)
        left.addWidget(g)
        g = QGroupBox("Optimization")
        f = QFormLayout(g)
        self.mf = QCheckBox("Multi-fidelity: circuit coarse, 3D fine")
        self.mf.setChecked(True)
        self.mf.setToolTip("The goals are optimized on the circuit; every round solves the 3D design once, fits the "
                           "circuit to it and corrects the variables (space mapping). Typically 3 to 6 3D solves")
        self.mf.toggled.connect(self.timer.start)
        self.range = QSpinBox()
        self.range.setRange(2, 60)
        self.range.setValue(20)
        self.range.setSuffix(" %")
        self.range.valueChanged.connect(self.timer.start)
        self.name = QLineEdit()
        f.addRow(self.mf)
        f.addRow("Variable range ±", self.range)
        f.addRow("Design name", self.name)
        left.addWidget(g)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(360)
        body.addWidget(lw)

        mid = QVBoxLayout()
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(4.6, 2.6), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setMinimumHeight(240)
        mid.addWidget(self.canvas, 1)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Variable", "Meaning", "Start", "Min", "Max"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        mid.addWidget(self.table, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {MUTED};")
        self.note.setTextFormat(Qt.RichText)
        mid.addWidget(self.note)
        body.addLayout(mid, 1)

        right = QFrame()
        right.setObjectName("WizCreates")
        right.setStyleSheet(f"QFrame#WizCreates {{ background: #ffffff; border: 1px solid {LINE}; "
                            "border-radius: 6px; }")
        right.setFixedWidth(270)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 12, 12, 12)
        rl.addWidget(_section("Will be created"))
        self.creates = QLabel()
        self.creates.setWordWrap(True)
        self.creates.setTextFormat(Qt.RichText)
        rl.addWidget(self.creates)
        rl.addSpacing(8)
        rl.addWidget(_section("Goals"))
        self.goals = QLabel()
        self.goals.setWordWrap(True)
        self.goals.setTextFormat(Qt.RichText)
        rl.addWidget(self.goals)
        rl.addStretch(1)
        body.addWidget(right)
        lay.addLayout(body, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(20, 8, 20, 12)
        hint = QLabel("Every value becomes a design variable; the optimization moves them within the ranges.")
        hint.setStyleSheet(f"color: {MUTED};")
        bar.addWidget(hint)
        bar.addStretch(1)
        back = QPushButton("Back")
        back.clicked.connect(lambda: self._show(0))
        bar.addWidget(back)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bar.addWidget(cancel)
        self.create_btn = QPushButton("Create")
        self.create_btn.clicked.connect(lambda: self._create(False))
        bar.addWidget(self.create_btn)
        self.go_btn = _primary("Create and Optimize")
        self.go_btn.setDefault(True)
        self.go_btn.clicked.connect(lambda: self._create(True))
        bar.addWidget(self.go_btn)
        lay.addLayout(bar)
        return page

    def _spin(self, v: float, lo: float, hi: float, dec: int, suffix: str = "") -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(dec)
        w.setValue(v)
        w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda *_: self.timer.start())
        return w

    def _build_spec(self, s: ST.Structure) -> None:
        while self.spec_form.rowCount():
            self.spec_form.removeRow(0)
        self.fields: dict[str, QDoubleSpinBox | QSpinBox | QComboBox] = {}
        for fld in s.fields:
            if fld.choices:
                w = QComboBox()
                w.addItems(list(fld.choices))
                w.setCurrentIndex(int(fld.default))
                w.value = w.currentIndex                # read like the spin boxes
                w.setValue = w.setCurrentIndex
                w.currentIndexChanged.connect(lambda *_: self.timer.start())
            elif fld.integer:
                w = QSpinBox()
                w.setRange(int(fld.lo), int(fld.hi))
                w.setValue(int(fld.default))
                w.valueChanged.connect(lambda *_: self.timer.start())
            else:
                w = self._spin(fld.default, fld.lo, fld.hi, fld.decimals)
            if fld.unit and not fld.choices:
                w.setSuffix(" " + fld.unit)
            if fld.help:
                w.setToolTip(fld.help)
            self.fields[fld.key] = w
            self.spec_form.addRow(fld.label, w)
        self.spec_box.setTitle(f"Specification: {s.title}")
        self.sub_box.setVisible(s.view != "side")   # wire antennas have no substrate
        self.mf.setEnabled(s.kind != "3d")          # no circuit: nothing to fit
        self.setWindowTitle(f"Design Wizard: {s.title}")
        self.refresh()

    def values(self) -> dict:
        return {k: w.value() for k, w in self.fields.items()}

    def substrate(self) -> ST.Substrate:
        m = self.mats.get(self.mat.currentText())
        if m is None:
            raise ValueError("Choose a substrate material")
        return ST.Substrate(material=m.name, er=_float(m.permittivity), tand=_float(m.loss_tangent),
                            h=self.h.value(), t=self.t.value(), min_feature=self.dmin.value())

    def _make(self) -> ST.Result:
        return self.sel.create(self.values(), self.substrate(), name=self.name.text().strip(),
                               range_pct=self.range.value(), multi_fidelity=self.mf.isChecked(),
                               project=self.project)

    def refresh(self) -> None:
        if self.sel is None or self.stack.currentIndex() != 1 and not hasattr(self, "fields"):
            return
        m = self.mats.get(self.mat.currentText())
        self.mat_info.setText(f"εr = {m.permittivity}, tan δ = {m.loss_tangent}" if m else "")
        try:
            r = self._make()
        except Exception as e:  # noqa: BLE001
            self.preview = None
            self.note.setText(f"<span style='color:#c0392b'>{e}</span>")
            self.create_btn.setEnabled(False)
            self.go_btn.setEnabled(False)
            return
        self.preview = r
        self.create_btn.setEnabled(True)
        self.go_btn.setEnabled(True)
        syn = r.synthesis
        self.table.setRowCount(len(syn.variables))
        for i, v in enumerate(syn.variables):
            vals = [v.name, v.meaning + ("" if v.include else " (not optimized)"), _num(v.value, v.unit),
                    _num(v.lo, v.unit), _num(v.hi, v.unit)]
            for j, t in enumerate(vals):
                it = QTableWidgetItem(t)
                if j >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if not v.include:
                    it.setForeground(QColor(MUTED))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        note = syn.note
        if r.warnings:
            note += "<br><span style='color:#b9770e'>" + "<br>".join(r.warnings) + "</span>"
        self.note.setText(note)
        circ = r.designs[0] if len(r.designs) > 1 else None
        d3 = r.designs[-1]
        op = next(d for d in r.designs if d.name == r.main).optimetrics[0]
        mf = op.props.get("MultiFidelity") == "True"
        if circ is None:
            self.creates.setText(
                f"<p><b style='color:{GOLD}'>■</b> <b>{d3.name}</b><br><span style='color:{MUTED}'>3D design, "
                f"{len(syn.variables)} design variables, lumped port</span></p>"
                f"<p><b style='color:{OK}'>■</b> <b>{op.name}</b><br><span style='color:{MUTED}'>"
                + ("on the adaptive solution at the frequency" if op.solution.endswith("LastAdaptive") else
                   "on the sweep of the band") + f" ({op.props.get('Optimizer', '')})</span></p>")
        else:
            self.creates.setText(
                f"<p><b style='color:#1d4f91'>■</b> <b>{circ.name}</b><br><span style='color:{MUTED}'>circuit, "
                f"{len(syn.variables)} design variables</span></p>"
                f"<p><b style='color:{GOLD}'>■</b> <b>{d3.name}</b><br><span style='color:{MUTED}'>3D design, "
                f"same variables, lumped ports</span></p>"
                f"<p><b style='color:{OK}'>■</b> <b>{op.name}</b><br><span style='color:{MUTED}'>"
                + ("circuit coarse, 3D fine (space mapping)" if mf else "on the circuit only") + "</span></p>")
        self.goals.setText("<br>".join(f"<code>{g['calc']}</code> {g['cond']} {g['value']}"
                                       f" <span style='color:{MUTED}'>({g['start']}–{g['stop']})</span>"
                                       for g in op.goals))
        self.name.setPlaceholderText(d3.name)
        self._draw(d3, circ)

    def _draw(self, d3, circ) -> None:
        from ..model.design import Project
        ax = self.ax
        ax.clear()
        p = Project("preview")
        p.materials = list(self.project.materials)
        p.designs = [d for d in (circ, d3) if d is not None]
        if getattr(self.sel, "view", "top") == "side":
            board, copper = ST.base.side_view(d3, p)
            view, xl, yl = "side view", "x (mm)", "z (mm)"
        else:
            board, copper, lower = ST.base.top_view(d3, p, bottom=True)
            view, xl, yl = "top view", "x (mm)", "y (mm)"
        if board:
            xs, ys = zip(*board)
            ax.fill(xs, ys, color="#e9f1fb", ec="#9cc3ee", lw=0.8, zorder=0)
        for rect in lower if getattr(self.sel, "view", "top") != "side" else ():
            xs, ys = zip(*rect)        # copper on the bottom layer (a partial ground), seen through the board
            ax.fill(xs, ys, color="#9aa3b2", alpha=0.55, ec="#6b7385", lw=0.6, ls="--", zorder=1)
        for rect in copper:
            xs, ys = zip(*rect)
            ax.fill(xs, ys, color=GOLD, ec="#9a6c14", lw=0.4, zorder=2)
        ax.set_aspect("equal")
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
        ax.set_title(f"3D design, {view}", fontsize=9, color=MUTED)
        self.canvas.draw_idle()

    def _create(self, run: bool) -> None:
        try:
            self.result = self._make()
        except Exception as e:  # noqa: BLE001
            self.note.setText(f"<span style='color:#c0392b'>{e}</span>")
            return
        self.run = self.result.optimization if run else ""
        self._remember()
        self.accept()

    # ---- helpers ------------------------------------------------------------------------------------------
    def _show(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        names = ("Structure", "Specification")
        parts = []
        for k, n in enumerate(names):
            if k < i:
                parts.append(f"<span style='color:{OK}'>✓ {n}</span>")
            elif k == i:
                parts.append(f"<b style='color:{INK}'>{k + 1}&nbsp; {n}</b>")
            else:
                parts.append(f"<span style='color:{MUTED}'>{k + 1}&nbsp; {n}</span>")
        self.steps.setText("&nbsp;&nbsp;&nbsp;&nbsp;›&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))
        if i == 0:
            self.setWindowTitle("Design Wizard")

    def _remember(self) -> None:
        if self.settings is None:
            return
        if self.sel is not None:
            self.settings.setValue(SETTINGS + "last", self.sel.key)
        self.settings.setValue(SETTINGS + "planned", self.show_planned.isChecked())
        if hasattr(self, "fields"):
            self.settings.setValue(SETTINGS + "material", self.mat.currentText())
            for w, k in ((self.h, "h"), (self.t, "t"), (self.dmin, "dmin")):
                self.settings.setValue(SETTINGS + k, w.value())
