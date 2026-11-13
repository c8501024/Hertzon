"""Footprint of a circuit part (Component dialog): KiCad footprint and symbol, the pads of each terminal and a
drawing of the footprint to scale with every pad's number and terminal."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QWidget)

from ..model import circuit as CK
from ..model import footprints as F
from ..model.kicad import KicadError
from .theme import MUTED

# one colour per terminal, in terminal order; open pads grey
TERMINAL_COLORS = ["#c4473a", "#2f6db5", "#2e7d32", "#b8860b", "#7b3fa0", "#00838f", "#ad1457", "#5d4037"]
OPEN = "#9e9e9e"


class PadView(QWidget):
    """The footprint seen from the top, to scale: pads coloured by terminal, labelled with number and terminal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 240)
        self.board: F.OnBoard | None = None
        self.terminals: list[str] = []

    def show_board(self, board: F.OnBoard | None, terminals: list[str]) -> None:
        self.board, self.terminals = board, terminals
        self.update()

    def color(self, t: str) -> QColor:
        if t in self.terminals:
            return QColor(TERMINAL_COLORS[self.terminals.index(t) % len(TERMINAL_COLORS)])
        return QColor(OPEN)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#1f2a1f"))
        if not self.board:
            p.setPen(QColor("#cfd8cf"))
            p.drawText(self.rect(), Qt.AlignCenter, "no footprint")
            return
        fp = self.board.footprint
        x0, y0, x1, y1 = fp.bbox()
        pad = 14
        top = 22                # room for the legend
        s = min((self.width() - 2 * pad) / max(x1 - x0, 1e-3),
                (self.height() - 2 * pad - 16 - top) / max(y1 - y0, 1e-3))
        ox = (self.width() - (x1 - x0) * s) / 2

        def map_(x, y):        # y up in the footprint, y down on the screen
            return QPointF(ox + (x - x0) * s, top + pad + (y1 - y) * s)
        p.setPen(QPen(QColor("#e0e0e0"), 1, Qt.DashLine))
        for poly in fp.fab:
            p.drawPolyline(QPolygonF([map_(*q) for q in poly]))
        font = QFont(self.font())
        font.setPointSizeF(max(6.5, min(9.0, s * 0.35)))
        p.setFont(font)
        for q in fp.pads:
            if not q.copper:
                continue
            t = self.board.terminal_of(q.number)
            c = self.color(t)
            c.setAlpha(210)
            p.setPen(QPen(QColor("#202020"), 0.8))
            p.setBrush(QBrush(c))
            for poly in q.polygons():
                p.drawPolygon(QPolygonF([map_(*v) for v in poly]))
            if q.drill > 0:
                p.setBrush(QBrush(QColor("#1f2a1f")))
                r = q.drill / 2 * s
                p.drawEllipse(map_(q.x, q.y), r, r)
        drawn = set()
        for q in fp.pads:
            if not q.copper or not q.number or (q.number, round(q.x, 3), round(q.y, 3)) in drawn:
                continue
            drawn.add((q.number, round(q.x, 3), round(q.y, 3)))
            c = map_(q.x, q.y)
            r = QRectF(c.x() - 40, c.y() - 16, 80, 32)
            p.setPen(QColor("white"))
            p.drawText(r, Qt.AlignCenter, q.number)
        # legend: the colour of each terminal
        x = 6.0
        fm = p.fontMetrics()
        for t in self.terminals + ["open"]:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(self.color(t)))
            p.drawRect(QRectF(x, 5, 10, 10))
            p.setPen(QColor("#cfd8cf"))
            p.drawText(QPointF(x + 13, 14), t)
            x += 22 + fm.horizontalAdvance(t)
        p.setPen(QColor("#cfd8cf"))
        p.drawText(QRectF(4, self.height() - 18, self.width() - 8, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{fp.name}   {x1 - x0:.2f} x {y1 - y0:.2f} mm (courtyard), seen from the top")


class FootprintBox(QGroupBox):
    """Footprint, Symbol and Pads of a part, with the drawing and the check of the assignment."""

    def __init__(self, doc, comp: CK.Component, parent=None):
        super().__init__("Footprint (board layout and 3D)", parent)
        self.doc, self.comp = doc, comp
        f = QFormLayout(self)
        self.footprint = QComboBox()
        self.footprint.setEditable(True)
        self.footprint.addItems([""] + self._suggestions())
        self.footprint.setCurrentText(comp.props.get("Footprint", ""))
        self.footprint.setToolTip("KiCad library id Library:Name (looked up in the KiCad library folders of\n"
                                  "Tools > Options and of the KiCad installation) or a .kicad_mod file, e.g. from\n"
                                  "SamacSys, SnapEDA or Ultra Librarian. Empty: the part has no footprint and the\n"
                                  "3D layout uses a gap for it.")
        f.addRow("Footprint", self._with_browse(self.footprint, self._browse_fp))
        self.symbol = QLineEdit(comp.props.get("Symbol", ""))
        self.symbol.setPlaceholderText("optional: names the pins, Library:Name or file.kicad_sym:Name")
        f.addRow("Symbol", self._with_browse(self.symbol, self._browse_sym))
        self.pads = QLineEdit(comp.props.get("Pads", ""))
        self.pads.setPlaceholderText("empty: automatic")
        self.pads.setToolTip("Pads of each terminal of the model, e.g. b=3; c=1; e1=2; e2=4 or d=5,6,7,8; g=4; "
                             "s=1,2,3,17.\nNC=... marks pads that stay open on purpose.")
        auto = QPushButton("Automatic")
        auto.setToolTip("Fill in the assignment Hertzon finds (pad 1 and 2 of a two-terminal part, otherwise the "
                        "pin names of the symbol)")
        auto.clicked.connect(self._auto)
        row = QHBoxLayout()
        row.addWidget(self.pads, 1)
        row.addWidget(auto)
        w = QWidget()
        w.setLayout(row)
        f.addRow("Pads", w)
        self.view = PadView()
        f.addRow(self.view)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {MUTED};")
        f.addRow(self.status)
        for sig in (self.footprint.currentTextChanged, self.symbol.textChanged, self.pads.textChanged):
            sig.connect(lambda *_: self.refresh())
        self.refresh()

    def _suggestions(self) -> list[str]:
        if self.comp.type in ("R", "L", "C", "SRLC", "PRLC"):
            kind = self.comp.type if self.comp.type in ("R", "L", "C") else ("C" if self.comp.type == "SRLC" else "L")
            return [F.default_footprint(kind, code) for code in F.CHIPS]
        return [n for n in F.BUILTIN_NAMES if not n.startswith(("R_", "C_", "L_"))]

    def _with_browse(self, edit, slot) -> QWidget:
        b = QPushButton("...")
        b.setFixedWidth(32)
        b.clicked.connect(slot)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(b)
        w = QWidget()
        w.setLayout(row)
        return w

    def project_dir(self) -> str | None:
        path = self.doc.project.path
        return str(Path(path).parent) if path else None

    def _browse_fp(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Footprint", self.project_dir() or "",
                                              "KiCad footprint (*.kicad_mod);;All files (*)")
        if path:
            self.footprint.setCurrentText(self._keep(path, "footprints"))

    def _browse_sym(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Symbol", self.project_dir() or "",
                                              "KiCad symbol library (*.kicad_sym);;All files (*)")
        if not path:
            return
        from ..model.kicad import read_symbols
        try:
            names = list(read_symbols(path))
        except KicadError as ex:
            self.status.setText(str(ex))
            return
        ref = self._keep(path, "symbols")
        if len(names) > 1:
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getItem(self, "Symbol", "Symbol in the library", names, 0, False)
            if not ok:
                return
            ref += f":{name}"
        self.symbol.setText(ref)

    def _keep(self, path: str, sub: str) -> str:
        """A file from outside the project is copied into the project's folder (footprints/, symbols/), so that
        the project carries it along; an untitled project keeps the absolute path."""
        d = self.project_dir()
        if not d:
            return path
        src = Path(path)
        try:
            return str(src.relative_to(d))
        except ValueError:
            pass
        dst = Path(d) / sub / src.name
        try:
            dst.parent.mkdir(exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            return str(dst.relative_to(d))
        except OSError:
            return path

    def _auto(self) -> None:
        board = self._board(automatic=True)
        if board:
            self.pads.setText(F.format_pads(board.pads))

    def _props(self, automatic=False) -> dict[str, str]:
        return {"Footprint": self.footprint.currentText().strip(), "Symbol": self.symbol.text().strip(),
                "Pads": "" if automatic else self.pads.text().strip()}

    def _board(self, automatic=False) -> F.OnBoard | None:
        c = CK.Component(self.comp.name, self.comp.type, props={**self.comp.props, **self._props(automatic)})
        self.error = ""
        try:
            return F.on_board(c, self.project_dir())
        except KicadError as ex:
            self.error = str(ex)
            return None

    def refresh(self) -> None:
        board = self._board()
        ts = F.terminals(self.comp)
        self.view.show_board(board, ts)
        if board is None:
            self.status.setText(self.error or "No footprint: the 3D layout puts this part in a gap.")
            return
        lines = [f"Source: {board.footprint.source}"]
        if board.automatic:
            lines.append(f"Pads (automatic): {F.format_pads(board.pads) or 'none found'}")
        lines += [f"✗ {m}" for m in board.check.errors] + [f"! {m}" for m in board.check.warnings]
        self.status.setText("\n".join(lines))

    def values(self) -> dict[str, str]:
        return self._props()
