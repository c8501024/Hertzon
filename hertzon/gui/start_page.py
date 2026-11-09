"""Start page: File > New (and at program start) asks what to simulate, 3D FEM or Circuit, and the solution
type. Project > Insert Design shows the same choice without the recent projects and examples, because a
project can hold 3D and circuit designs side by side. Types that are not built yet are shown greyed out with
"not implemented yet" and cannot be chosen."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QRadioButton, QSizePolicy, QVBoxLayout, QWidget)

from ..model import schema
from ..model.expr import LENGTH_UNITS
from .theme import GOLD, GOLD_BG, INK, LINE, MUTED

OFF = "#a8adb8"
NOT_YET = "not implemented yet"
SHOW_AT_STARTUP = "startpage/show_at_startup"
UNITS = ("um", "mm", "cm", "m", "mil", "in")

ICON_3D = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 96 80'>
<path d='M8 50 L48 30 L88 50 L48 70 Z' fill='#e9f1fb' stroke='#1d4f91' stroke-width='1.5' stroke-linejoin='round'/>
<path d='M8 50 L8 56 L48 76 L88 56 L88 50' fill='#cfe0f5' stroke='#1d4f91' stroke-width='1.5' stroke-linejoin='round'/>
<path d='M48 70 L48 76' stroke='#1d4f91' stroke-width='1.5'/>
<g stroke='#9cc3ee' stroke-width='1' fill='none'><path d='M28 40 L48 50 L68 40'/><path d='M28 60 L48 50 L68 60'/>
<path d='M18 45 L38 55'/><path d='M58 35 L78 45'/><path d='M48 30 L48 70'/></g>
<path d='M34 47 L48 40 L62 47 L48 54 Z' fill='#c9952f' stroke='#9a6c14' stroke-width='1'/>
<g fill='none' stroke='#c9952f' stroke-width='1.6' stroke-linecap='round'>
<path d='M40 36 C36 22 40 10 48 6'/><path d='M56 36 C60 22 56 10 48 6'/>
<path d='M34 40 C22 30 22 16 30 10'/><path d='M62 40 C74 30 74 16 66 10'/></g></svg>"""

ICON_CIRCUIT = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 96 80'>
<g fill='none' stroke='#1f2633' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'>
<circle cx='14' cy='40' r='8'/><path d='M10 40 c1.5 -3 2.5 -3 4 0 s2.5 3 4 0'/>
<path d='M14 32 V16 H28'/><path d='M28 16 l3 -4 4 8 4 -8 4 8 3 -4'/><path d='M46 16 H62'/>
<path d='M62 16 V30'/><path d='M54 30 H70'/><path d='M54 35 H70'/><path d='M62 35 V64'/>
<path d='M14 48 V64 H82'/><path d='M62 16 H82 V30'/><path d='M48 64 v6'/><path d='M42 70 H54'/><path d='M45 73 H51'/>
<path d='M82 50 V64'/></g>
<rect x='74' y='30' width='16' height='20' rx='2' fill='#e9f1fb' stroke='#1d4f91' stroke-width='1.5'/>
<path d='M77 44 L82 35 L87 44 Z' fill='#c9952f'/>
<circle cx='14' cy='16' r='2' fill='#c9952f'/><circle cx='62' cy='16' r='2' fill='#1f2633'/>
<circle cx='62' cy='64' r='2' fill='#1f2633'/></svg>"""


@dataclass
class StartChoice:
    """What the user picked: action is "create", "empty", "open" (a recent project), "example" or "template"."""
    action: str
    kind: str = "3d"
    solution_type: str = "Modal"
    units: str = "mm"
    name: str = ""
    path: str = ""          # open / example
    template: str = ""      # "patch" or "hairpin"


def _svg_pixmap_paint(p: QPainter, svg: str, rect: QRectF, enabled: bool) -> None:
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:      # pragma: no cover  (QtSvg ships with PySide6)
        return
    r = QSvgRenderer(QByteArray(svg.encode()))
    if not enabled:
        p.setOpacity(0.35)
    r.render(p, rect)
    p.setOpacity(1.0)


class TypeCard(QAbstractButton):
    """A large, checkable card with a symbol, a title and one line of description."""
    doubleClicked = Signal()

    def __init__(self, kind: str, title: str, text: str, svg: str, available: bool, parent=None):
        super().__init__(parent)
        self.kind, self.title, self.text, self.svg, self.available = kind, title, text, svg, available
        self.setCheckable(True)
        self.setEnabled(available)
        self.setCursor(Qt.PointingHandCursor if available else Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(title + ("" if available else f" ({NOT_YET})"))
        if not available:
            self.setToolTip(f"{title}: {NOT_YET}")

    def sizeHint(self) -> QSize:
        return QSize(320, 112)

    def mouseDoubleClickEvent(self, e) -> None:
        if self.available:
            self.setChecked(True)
            self.doubleClicked.emit()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        sel = self.isChecked() and self.available
        p.setPen(QPen(QColor(GOLD if sel else LINE), 2 if sel else 1.5))
        p.setBrush(QColor(GOLD_BG if sel else "#ffffff"))
        p.drawRoundedRect(r, 8, 8)
        if self.hasFocus():
            p.setPen(QPen(QColor(GOLD), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(3, 3, -3, -3), 6, 6)
        _svg_pixmap_paint(p, self.svg, QRectF(r.left() + 14, r.center().y() - 40, 96, 80), self.available)
        x = r.left() + 126
        w = r.right() - x - 12
        f = p.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(INK if self.available else OFF))
        p.drawText(QRectF(x, r.top() + 14, w, 26), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        f = self.font()
        p.setFont(f)
        p.setPen(QColor(MUTED if self.available else OFF))
        y = r.top() + 42
        if not self.available:
            f.setItalic(True)
            p.setFont(f)
            p.drawText(QRectF(x, y, w, 18), Qt.AlignLeft | Qt.AlignVCenter, NOT_YET)
            f.setItalic(False)
            p.setFont(f)
            y += 20
        p.drawText(QRectF(x, y, w, r.bottom() - y - 8), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.text)
        p.end()


def _option(name: str, text: str, available: bool) -> tuple[QWidget, QRadioButton]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 2, 0, 2)
    lay.setSpacing(0)
    rb = QRadioButton(name if available else f"{name}   ({NOT_YET})")
    rb.setEnabled(available)
    f = rb.font()
    f.setBold(available)
    f.setItalic(not available)
    rb.setFont(f)
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {MUTED if available else OFF}; margin-left: 22px;")
    lab.setWordWrap(True)
    lay.addWidget(rb)
    lay.addWidget(lab)
    if not available:
        w.setToolTip(f"{name}: {NOT_YET}")
    return w, rb


def _group(title: str, hint: str) -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("StartGroup")
    box.setStyleSheet(f"QFrame#StartGroup {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 6px; }}")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(4)
    t = QLabel(title.upper())
    t.setStyleSheet(f"color: {MUTED}; font-weight: 600; letter-spacing: 1px;")
    h = QLabel(hint)
    h.setStyleSheet(f"color: {MUTED};")
    h.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(h)
    return box, lay


class StartPageDialog(QDialog):
    """mode "new": File > New and the program start; mode "insert": Project > Insert Design."""

    def __init__(self, parent=None, mode: str = "new", settings=None, recent: list[str] | None = None,
                 examples: list[Path] | None = None, default_units: str = "mm", default_name: str = ""):
        super().__init__(parent)
        self.mode = mode
        self.settings = settings
        self.choice: StartChoice | None = None
        insert = mode == "insert"
        self.setWindowTitle("Insert Design" if insert else "New Project")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(20, 18, 20, 14)
        ml.setSpacing(12)
        q = QLabel("Which kind of design?" if insert else "What do you want to simulate?")
        f = q.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        q.setFont(f)
        ml.addWidget(q)
        sub = QLabel("A project can hold 3D and circuit designs side by side." if insert else
                     "A project can hold 3D and circuit designs side by side. You can add more with Insert Design.")
        sub.setStyleSheet(f"color: {MUTED};")
        sub.setWordWrap(True)
        ml.addWidget(sub)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards: dict[str, TypeCard] = {}
        self.card_group = QButtonGroup(self)
        self.card_group.setExclusive(True)
        texts = {"3d": ("Fields in 3D models: antennas, filters, connectors, coils. Adaptive mesh.", ICON_3D),
                 "circuit": ("Schematic with R, L, C, lines, Touchstone blocks and linked 3D designs.", ICON_CIRCUIT)}
        for kind, (title, ok) in schema.DESIGN_KINDS.items():
            c = TypeCard(kind, title, texts[kind][0], texts[kind][1], ok)
            c.toggled.connect(self._kind_changed)
            c.doubleClicked.connect(self._create)
            self.card_group.addButton(c)
            self.cards[kind] = c
            cards.addWidget(c)
        ml.addLayout(cards)

        # second step: solution type of a 3D design, analysis of a circuit
        self.types = QButtonGroup(self)
        self.type_radio: dict[str, QRadioButton] = {}
        self.step3d = QWidget()
        g = QGridLayout(self.step3d)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(12)
        for col, (title, hint, rows) in enumerate((
                ("High frequency · full wave", "Structure about λ/10 or larger. S-parameters, fields, far field.",
                 schema.HIGH_FREQUENCY_TYPES),
                ("Low frequency · quasi-static", "Structure much smaller than λ. Results as C, L, R, fields and "
                 "forces.", schema.LOW_FREQUENCY_TYPES))):
            box, lay = _group(title, hint)
            for name, text, ok in rows:
                w, rb = _option(name, text, ok)
                self.types.addButton(rb)
                self.type_radio[name] = rb
                lay.addWidget(w)
            lay.addStretch(1)
            g.addWidget(box, 0, col)
        self.stepc = QWidget()
        cl = QVBoxLayout(self.stepc)
        cl.setContentsMargins(0, 0, 0, 0)
        box, lay = _group("Analysis", "More analyses can be added to the same schematic later.")
        self.analysis = QButtonGroup(self)
        for name, text, ok in schema.CIRCUIT_ANALYSES:
            w, rb = _option(name, text, ok)
            self.analysis.addButton(rb)
            rb.setChecked(ok and not self.analysis.checkedButton())
            lay.addWidget(w)
        cl.addWidget(box)
        ml.addWidget(self.step3d)
        ml.addWidget(self.stepc)
        ml.addStretch(1)

        # bottom bar
        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(QLabel("Name"))
        self.name = QLineEdit(default_name or ("Design1" if insert else "Project1"))
        self.name.setMaximumWidth(180)
        bar.addWidget(self.name)
        self.units_label = QLabel("Units")
        bar.addWidget(self.units_label)
        self.units = QComboBox()
        units = list(UNITS) + ([default_units] if default_units in LENGTH_UNITS and default_units not in UNITS else [])
        self.units.addItems(units)
        self.units.setCurrentText(default_units if default_units in units else "mm")
        bar.addWidget(self.units)
        self.startup = QCheckBox("Show at startup")
        self.startup.setChecked(self._show_at_startup())
        self.startup.setVisible(not insert)
        bar.addWidget(self.startup)
        bar.addStretch(1)
        self.empty_btn = QPushButton("Empty Project")
        self.empty_btn.setToolTip("A project with one empty 3D design, without asking")
        self.empty_btn.clicked.connect(self._empty)
        self.empty_btn.setVisible(not insert)
        bar.addWidget(self.empty_btn)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bar.addWidget(cancel)
        self.create_btn = QPushButton("Insert" if insert else "Create")
        self.create_btn.setDefault(True)
        self.create_btn.setStyleSheet(f"QPushButton {{ background: {GOLD}; color: #ffffff; font-weight: 600; "
                                      "border: 1px solid #b2822a; border-radius: 4px; padding: 5px 16px; }"
                                      "QPushButton:disabled { background: #d9dce2; border-color: #d9dce2; }")
        self.create_btn.clicked.connect(self._create)
        bar.addWidget(self.create_btn)
        ml.addLayout(bar)
        root.addWidget(main, 1)

        # right column: recent projects, examples, templates
        self.side = QFrame()
        self.side.setObjectName("StartSide")
        self.side.setStyleSheet(f"QFrame#StartSide {{ background: #ffffff; border-left: 1px solid {LINE}; }}")
        self.side.setFixedWidth(280)
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(14, 16, 14, 14)
        sl.setSpacing(6)
        self.recent = self._list(sl, "Recent")
        for path in (recent or [])[:8]:
            it = QListWidgetItem(re.split(r"[\\/]", path)[-1])     # Windows paths also on other systems
            it.setToolTip(path)
            it.setData(Qt.UserRole, ("open", path))
            self.recent.addItem(it)
        if not self.recent.count():
            it = QListWidgetItem("(no recent projects)")
            it.setFlags(Qt.NoItemFlags)
            self.recent.addItem(it)
        self.examples = self._list(sl, "Examples")
        for p in examples or []:
            it = QListWidgetItem(p.stem)
            it.setToolTip(str(p))
            it.setData(Qt.UserRole, ("example", str(p)))
            self.examples.addItem(it)
        self.templates = self._list(sl, "Start from a template")
        for label, key in (("Design Wizard: couplers, filters, antennas ...", "wizard"),
                           ("Rectangular patch antenna ...", "patch"), ("Tapped hairpin filter ...", "hairpin")):
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, ("template", key))
            self.templates.addItem(it)
        self.side.setVisible(not insert)
        root.addWidget(self.side)

        self.cards["3d"].setChecked(True)
        self.type_radio["Modal"].setChecked(True)
        self._kind_changed()
        self.resize(820 if insert else 1100, 560)

    # ---- helpers ------------------------------------------------------------------------------
    def _list(self, lay: QVBoxLayout, title: str) -> QListWidget:
        t = QLabel(title.upper())
        t.setStyleSheet(f"color: {MUTED}; font-weight: 600; letter-spacing: 1px; margin-top: 6px;")
        lay.addWidget(t)
        lw = QListWidget()
        lw.setFrameShape(QFrame.NoFrame)
        lw.setStyleSheet("QListWidget::item { padding: 3px 2px; }")
        lw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lw.setTextElideMode(Qt.ElideMiddle)
        lw.itemActivated.connect(self._item)
        lw.itemDoubleClicked.connect(self._item)
        lay.addWidget(lw, 1)
        return lw

    def _show_at_startup(self) -> bool:
        return self.settings.value(SHOW_AT_STARTUP, True, type=bool) if self.settings is not None else True

    def _save_startup(self) -> None:
        if self.settings is not None and self.mode == "new":
            self.settings.setValue(SHOW_AT_STARTUP, self.startup.isChecked())

    def kind(self) -> str:
        return next((k for k, c in self.cards.items() if c.isChecked()), "3d")

    def solution_type(self) -> str:
        return next((n for n, rb in self.type_radio.items() if rb.isChecked()), "Modal")

    def _kind_changed(self, *_a) -> None:
        k = self.kind()
        self.step3d.setVisible(k == "3d")
        self.stepc.setVisible(k == "circuit")
        self.units.setVisible(k == "3d")
        self.units_label.setVisible(k == "3d")
        self.create_btn.setEnabled(schema.DESIGN_KINDS[k][1])

    # ---- results ------------------------------------------------------------------------------
    def _finish(self, choice: StartChoice) -> None:
        self.choice = choice
        self._save_startup()
        self.accept()

    def _create(self) -> None:
        k = self.kind()
        if not schema.DESIGN_KINDS[k][1]:
            return
        self._finish(StartChoice("create", kind=k, solution_type=self.solution_type() if k == "3d" else "Modal",
                                 units=self.units.currentText(), name=self.name.text().strip()))

    def _empty(self) -> None:
        self._finish(StartChoice("empty", units=self.units.currentText(), name=self.name.text().strip()))

    def _item(self, it: QListWidgetItem) -> None:
        data = it.data(Qt.UserRole)
        if not data:
            return
        action, value = data
        if action == "template":
            self._finish(StartChoice("template", template=value, units=self.units.currentText(),
                                     name=self.name.text().strip()))
        else:
            self._finish(StartChoice(action, path=value))

    def reject(self) -> None:
        self._save_startup()
        super().reject()
