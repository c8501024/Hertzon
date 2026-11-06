"""Schematic editor of a circuit design: components, wires, power ports and 3D design blocks.

The parts come from the Circuit ribbon or the part library on the left (drag them into the schematic, or
double-click and click to place). Wires: click the corners; a click on a pin or another wire ends the wire, a
double-click or right-click too. Drag a 3D design from the Project Manager (or the library) into the schematic to
use its S-parameters as a block. Double-click a part to edit it; R rotates, M mirrors, Del deletes."""
from __future__ import annotations

import base64
import json

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetricsF, QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..model import circuit as CK
from .circuit_thumb import THUMB, design_thumbnail
from .theme import GOLD, INK, MUTED
from .trees import MIME_DESIGN

MIME_PART = "application/x-hertzon-part"
MIME_CIRCUIT = "application/x-hertzon-circuit"          # copied schematic parts on the clipboard
INKC = QColor(INK)
PIN = QColor("#1d4f91")
WIRE = QColor("#1f6f3f")
COPPER = QColor("#c9952f")
SUBSTRATE = QColor("#dfe6d0")
FILL = QColor("#e9f1fb")

# size (w, h) of the body at rotation 0, for the outline used in selection and label placement
SIZES = {"GND": (24, 28), "MVIA": (28, 44), "PORT": (26, 80), "LABEL": (20, 20), "TLIN": (80, 20), "TLOC": (18, 70),
         "TLSC": (24, 80), "MLIN": (80, 20), "MLOC": (18, 70), "MLSC": (24, 80), "MCLIN": (80, 40),
         "MTEE": (60, 40), "MCORN": (60, 60), "MMBEND": (60, 60), "MSTEP": (60, 22), "MGAP": (60, 18),
         "MCROSS": (60, 60), "SLIN": (80, 24), "CPWLIN": (80, 26), "COAX": (80, 22), "SUBST": (80, 46), "SRLC": (80, 20), "PRLC": (80, 40),
         "ATTEN": (60, 24), "DCBLOCK": (60, 44), "DCFEED": (60, 44)}


def snap(v: float) -> int:
    return int(round(v / CK.GRID)) * CK.GRID


def body_size(c: CK.Component) -> tuple[int, int]:
    if c.type in CK.NPORT_TYPES:
        return CK.BLOCK_W, CK.block_height(CK.nport_count(c))
    return SIZES.get(c.type, (60, 24))


def _image(b64: str) -> QImage | None:
    if not b64:
        return None
    img = QImage()
    img.loadFromData(QByteArray(base64.b64decode(b64)), "PNG")
    return None if img.isNull() else img


# ---- symbols ------------------------------------------------------------------------------------------
def _zigzag(p: QPainter, x0: float, x1: float, amp: float = 5, n: int = 6, vertical: bool = False) -> None:
    pts = []
    for i in range(n + 1):
        t = x0 + (x1 - x0) * i / n
        d = 0 if i in (0, n) else (-amp if i % 2 else amp)
        pts.append(QPointF(d, t) if vertical else QPointF(t, d))
    p.drawPolyline(QPolygonF(pts))


def _coil(p: QPainter, x0: float, x1: float, n: int = 4, r: float = 6) -> None:
    path = QPainterPath(QPointF(x0, 0))
    step = (x1 - x0) / n
    for i in range(n):
        path.arcTo(QRectF(x0 + step * i, -r, step, 2 * r), 180, -180)
    p.drawPath(path)


def _plates(p: QPainter, x: float, h: float = 10, gap: float = 4) -> None:
    p.drawLine(QPointF(x - gap, -h), QPointF(x - gap, h))
    p.drawLine(QPointF(x + gap, -h), QPointF(x + gap, h))


def _ground(p: QPainter, y: float) -> None:
    p.drawLine(QPointF(-10, y), QPointF(10, y))
    p.drawLine(QPointF(-6, y + 4), QPointF(6, y + 4))
    p.drawLine(QPointF(-2, y + 8), QPointF(2, y + 8))


def _small_text(p: QPainter, rect: QRectF, text: str, size: float = 6.5) -> None:
    f = QFont(p.font())
    f.setPointSizeF(size)
    f.setBold(True)
    p.save()
    p.setFont(f)
    p.drawText(rect, Qt.AlignCenter, text)
    p.restore()


def paint_symbol(p: QPainter, c: CK.Component, pen: QPen, img: QImage | None = None, pins: bool = True) -> None:
    """The symbol of a part at rotation 0 around its origin (the caller sets rotation and mirror)."""
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    t = c.type
    if t == "R":
        p.drawLine(-30, 0, -18, 0)
        _zigzag(p, -18, 18)
        p.drawLine(18, 0, 30, 0)
    elif t == "L":
        p.drawLine(-30, 0, -20, 0)
        _coil(p, -20, 20)
        p.drawLine(20, 0, 30, 0)
    elif t == "C":
        p.drawLine(-30, 0, -4, 0)
        _plates(p, 0)
        p.drawLine(4, 0, 30, 0)
    elif t == "SRLC":
        p.drawLine(-40, 0, -34, 0)
        _zigzag(p, -34, -14, 4, 4)
        _coil(p, -14, 10, 3, 5)
        p.drawLine(10, 0, 18, 0)
        _plates(p, 21, 8, 3)
        p.drawLine(24, 0, 40, 0)
    elif t == "PRLC":
        p.drawLine(-40, 0, -24, 0)
        p.drawLine(24, 0, 40, 0)
        p.drawLine(-24, -14, -24, 14)
        p.drawLine(24, -14, 24, 14)
        p.save()
        p.translate(0, -14)
        p.drawLine(-24, 0, -12, 0)
        _zigzag(p, -12, 12, 4, 6)
        p.drawLine(12, 0, 24, 0)
        p.translate(0, 14)
        p.drawLine(-24, 0, -12, 0)
        _coil(p, -12, 12, 3, 4)
        p.drawLine(12, 0, 24, 0)
        p.translate(0, 14)
        p.drawLine(-24, 0, -3, 0)
        _plates(p, 0, 6, 3)
        p.drawLine(3, 0, 24, 0)
        p.restore()
    elif t in ("DCBLOCK", "DCFEED"):
        p.drawLine(-30, 0, -14, 0)
        p.drawLine(14, 0, 30, 0)
        p.drawRect(QRectF(-14, -11, 28, 22))
        if t == "DCBLOCK":
            p.drawLine(-9, 0, -2, 0)
            _plates(p, 0, 6, 2)
            p.drawLine(2, 0, 9, 0)
        else:
            _coil(p, -9, 9, 3, 3)
        p.setPen(QPen(pen.color(), 0.8))
        _small_text(p, QRectF(-14, -22, 28, 10), "DC", 6)
    elif t == "ATTEN":
        p.drawLine(-30, 0, -20, 0)
        p.drawLine(20, 0, 30, 0)
        p.setBrush(QColor("#ffffff"))
        p.drawRect(QRectF(-20, -12, 40, 24))
        _small_text(p, QRectF(-20, -12, 40, 24), "dB")
    elif t == "GND":
        p.drawLine(0, -10, 0, 0)
        _ground(p, 0)
    elif t == "MVIA":
        # pad on top, plated barrel through the substrate, ground plane
        p.drawLine(0, -20, 0, -12)
        p.setBrush(SUBSTRATE)
        p.drawRect(QRectF(-12, -10, 24, 14))
        p.setBrush(COPPER)
        p.drawRect(QRectF(-8, -12, 16, 3))
        p.drawRect(QRectF(-3, -10, 6, 14))
        p.drawLine(0, 4, 0, 8)
        _ground(p, 8)
    elif t == "PORT":
        # power port: pin at the top, internal resistance, source, ground
        p.drawLine(0, -40, 0, -32)
        _zigzag(p, -32, -14, 5, 6, vertical=True)
        p.drawLine(0, -14, 0, -10)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(0, 0), 10, 10)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(-6, 0))
        path.cubicTo(-4, -7, -1, -7, 0, 0)
        path.cubicTo(1, 7, 4, 7, 6, 0)
        p.drawPath(path)
        p.drawLine(0, 10, 0, 18)
        _ground(p, 18)
    elif t == "LABEL":
        p.drawLine(0, 0, 0, -8)
        p.drawEllipse(QPointF(0, 0), 1.5, 1.5)
    elif t in ("TLIN", "MLIN", "SLIN", "COAX", "CPWLIN"):
        p.drawLine(-40, 0, -30, 0)
        p.drawLine(30, 0, 40, 0)
        if t == "TLIN":
            p.setBrush(FILL)
            p.drawRect(QRectF(-30, -8, 60, 16))
        elif t == "MLIN":
            p.setBrush(SUBSTRATE)
            p.drawRect(QRectF(-30, -9, 60, 18))
            p.setBrush(COPPER)
            p.drawRect(QRectF(-30, -4, 60, 8))
        elif t == "CPWLIN":
            p.setBrush(SUBSTRATE)
            p.drawRect(QRectF(-30, -12, 60, 24))
            p.setBrush(COPPER)
            p.drawRect(QRectF(-30, -3, 60, 6))
            p.drawRect(QRectF(-30, -12, 60, 5))
            p.drawRect(QRectF(-30, 7, 60, 5))
        elif t == "SLIN":
            p.setBrush(SUBSTRATE)
            p.drawRect(QRectF(-30, -11, 60, 22))
            p.setBrush(COPPER)
            p.drawRect(QRectF(-30, -2.5, 60, 5))
            p.drawLine(-30, -11, 30, -11)
            p.drawLine(-30, 11, 30, 11)
        else:
            p.setBrush(QColor("#d9dde4"))
            p.drawRect(QRectF(-30, -10, 60, 20))
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QRectF(-34, -10, 8, 20))
            p.drawLine(-30, 0, 30, 0)
    elif t in ("TLOC", "TLSC", "MLOC", "MLSC"):
        p.drawLine(0, -40, 0, -30)
        if t.startswith("TL"):
            p.setBrush(FILL)
            p.drawRect(QRectF(-8, -30, 16, 56))
        else:
            p.setBrush(SUBSTRATE)
            p.drawRect(QRectF(-9, -30, 18, 56))
            p.setBrush(COPPER)
            p.drawRect(QRectF(-4, -30, 8, 56))
        if t.endswith("SC"):
            p.drawLine(0, 26, 0, 30)
            _ground(p, 30)
    elif t == "MTEE":
        p.setBrush(SUBSTRATE)
        p.drawPolygon(QPolygonF([QPointF(-30, -9), QPointF(30, -9), QPointF(30, 9), QPointF(9, 9), QPointF(9, 30),
                                 QPointF(-9, 30), QPointF(-9, 9), QPointF(-30, 9)]))
        p.setBrush(COPPER)
        p.drawPolygon(QPolygonF([QPointF(-30, -4), QPointF(30, -4), QPointF(30, 4), QPointF(4, 4), QPointF(4, 30),
                                 QPointF(-4, 30), QPointF(-4, 4), QPointF(-30, 4)]))
    elif t in ("MCORN", "MMBEND", "MSTEP", "MGAP", "MCROSS"):
        def poly(pts):
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))

        def outline(a):                                     # copper (a = 4) or substrate (a = 9) outline
            if t == "MCORN":
                return [(-30, -a), (a, -a), (a, 30), (-a, 30), (-a, a), (-30, a)]
            if t == "MMBEND":
                return [(-30, -a), (a - 6, -a), (a, -a + 6), (a, 30), (-a, 30), (-a, a), (-30, a)]
            return [(-30, -a), (-a, -a), (-a, -30), (a, -30), (a, -a), (30, -a), (30, a), (a, a), (a, 30),
                    (-a, 30), (-a, a), (-30, a)]
        p.setBrush(SUBSTRATE)
        if t == "MSTEP":
            p.drawRect(QRectF(-30, -11, 60, 22))
            p.setBrush(COPPER)
            poly([(-30, -7), (0, -7), (0, -3), (30, -3), (30, 3), (0, 3), (0, 7), (-30, 7)])
        elif t == "MGAP":
            p.drawRect(QRectF(-30, -9, 60, 18))
            p.setBrush(COPPER)
            p.drawRect(QRectF(-30, -4, 24, 8))
            p.drawRect(QRectF(6, -4, 24, 8))
        else:
            poly(outline(9))
            p.setBrush(COPPER)
            poly(outline(4))
    elif t == "MCLIN":
        for y in (-10, 10):
            p.drawLine(-40, y, -30, y)
            p.drawLine(30, y, 40, y)
        p.setBrush(SUBSTRATE)
        p.drawRect(QRectF(-30, -18, 60, 36))
        p.setBrush(COPPER)
        p.drawRect(QRectF(-30, -14, 60, 8))
        p.drawRect(QRectF(-30, 6, 60, 8))
    elif t == "SUBST":
        p.setBrush(QColor("#ffffff"))
        p.drawRect(QRectF(-40, -23, 80, 46))
        p.setBrush(SUBSTRATE)
        p.drawRect(QRectF(-32, -6, 64, 14))
        p.setBrush(COPPER)
        p.drawRect(QRectF(-14, -10, 28, 4))
        p.drawRect(QRectF(-32, 8, 64, 3))
        p.setPen(QPen(pen.color(), 0.8))
        _small_text(p, QRectF(-40, -22, 80, 11), "SUBSTRATE", 5.5)
    elif t in CK.NPORT_TYPES:
        w, h = body_size(c)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), 6, 6)
        for (dx, dy) in CK.nport_pins(CK.nport_count(c)):
            p.drawLine(QPointF(dx, dy), QPointF(dx - 10 if dx > 0 else dx + 10, dy))
        if img is not None:
            side = min(w - 20, h - 16, THUMB)
            p.drawImage(QRectF(-side / 2, -side / 2, side, side), img)
        else:
            p.setPen(QPen(QColor(MUTED)))
            _small_text(p, QRectF(-w / 2, -h / 2, w, h), "S" if t == "SNP" else "3D", 12)
        names = [n for n in c.props.get("PortNames", "").split(",") if n]
        f = QFont(p.font())
        f.setPointSizeF(7)
        p.setFont(f)
        p.setPen(QPen(QColor(MUTED)))
        for k, (dx, dy) in enumerate(CK.nport_pins(CK.nport_count(c))):
            label = names[k] if k < len(names) else str(k + 1)
            r = QRectF(-w / 2 + 3, dy - 7, w / 2 - 6, 14) if dx < 0 else QRectF(3, dy - 7, w / 2 - 6, 14)
            p.drawText(r, (Qt.AlignLeft if dx < 0 else Qt.AlignRight) | Qt.AlignVCenter, label)
    if pins:
        p.setPen(Qt.NoPen)
        p.setBrush(PIN)
        for dx, dy in CK.pin_offsets(c):
            p.drawEllipse(QPointF(dx, dy), 2.5, 2.5)


def value_lines(c: CK.Component) -> list[str]:
    """The values shown next to a part."""
    p = c.props
    t = c.type
    if t in ("R", "L", "C"):
        return [p.get(t, "")]
    if t in ("SRLC", "PRLC"):
        return [f"R={p.get('R')}  L={p.get('L')}  C={p.get('C')}"]
    if t == "PORT":
        num = p.get("Num", "").strip()
        return [(f"Num={num}  " if num else "") + f"Z={p.get('Z0')}"]
    if t == "ATTEN":
        return [p.get("Loss", "")]
    if t in ("TLIN", "TLOC", "TLSC"):
        return [f"Z={p.get('Z0')}  E={p.get('E')}", f"F={p.get('F')}"]
    if t in ("MLIN", "MLOC", "MLSC", "SLIN"):
        return [f"W={p.get('W')}  L={p.get('L')}", f"{p.get('Subst')}"]
    if t == "MVIA":
        n = p.get("N", "1").strip() or "1"
        return [f"D={p.get('D')}" + (f"  N={n}  P={p.get('P')}" if n != "1" else ""), f"{p.get('Subst')}"]
    if t == "CPWLIN":
        gnd = "  backed" if p.get("Backed", "False") == "True" else ""
        return [f"W={p.get('W')}  S={p.get('S')}", f"L={p.get('L')}  {p.get('Subst')}{gnd}"]
    if t == "MTEE":
        return [f"W1={p.get('W1')}  W2={p.get('W2')}", f"W3={p.get('W3')}  {p.get('Subst')}"]
    if t in ("MCORN", "MMBEND"):
        return [f"W={p.get('W')}", f"{p.get('Subst')}"]
    if t == "MSTEP":
        return [f"W1={p.get('W1')}  W2={p.get('W2')}", f"{p.get('Subst')}"]
    if t == "MGAP":
        return [f"W1={p.get('W1')}  W2={p.get('W2')}", f"S={p.get('S')}  {p.get('Subst')}"]
    if t == "MCROSS":
        return [f"W1={p.get('W1')}  W2={p.get('W2')}", f"W3={p.get('W3')}  W4={p.get('W4')}", f"{p.get('Subst')}"]
    if t == "MCLIN":
        return [f"W={p.get('W')}  S={p.get('S')}", f"L={p.get('L')}  {p.get('Subst')}"]
    if t == "COAX":
        return [f"Di={p.get('Di')}  Do={p.get('Do')}", f"Er={p.get('Er')}  L={p.get('L')}"]
    if t == "SUBST":
        return [f"Er={p.get('Er')}  H={p.get('H')}", f"T={p.get('T')}  tanD={p.get('TanD')}"]
    if t == "EM":
        return [p.get("Design", ""), p.get("Solution", "")]
    if t == "SNP":
        return [p.get("File", "").replace("\\", "/").split("/")[-1]]
    return []


def component_icon(ctype: str, size: int = 32) -> QIcon:
    """The symbol of a part type as an icon (ribbon and library)."""
    c = CK.Component("", ctype, props={**CK.TYPES[ctype].props, "Ports": "2"})
    w, h = body_size(c)
    pins = CK.pin_offsets(c)
    xs = [x for x, _ in pins] + [-w / 2, w / 2]
    ys = [y for _, y in pins] + [-h / 2, h / 2]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1)
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = (size * 2 - 6) / span
    p.translate(size - (max(xs) + min(xs)) / 2 * s, size - (max(ys) + min(ys)) / 2 * s)
    p.scale(s, s)
    paint_symbol(p, c, QPen(INKC, 3.4 / max(s, 0.4), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin), pins=False)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


# ---- items --------------------------------------------------------------------------------------------
class ComponentItem(QGraphicsItem):
    def __init__(self, editor: "CircuitEditor", comp: CK.Component, ghost: bool = False):
        super().__init__()
        self.editor, self.comp, self.ghost = editor, comp, ghost
        if not ghost:
            self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                          QGraphicsItem.ItemSendsGeometryChanges)
        else:
            self.setOpacity(0.55)
            self.setZValue(10)
        self.setPos(comp.x, comp.y)
        self.sync_transform()
        self._img = _image(comp.props.get("Thumbnail", "")) if comp.type == "EM" else None
        self.setToolTip(self._tip())

    def sync_transform(self) -> None:
        tr = QTransform()
        tr.rotate(self.comp.rot)
        if self.comp.mirror:
            tr.scale(1, -1)
        self.setTransform(tr)

    def _tip(self) -> str:
        c = self.comp
        info = CK.TYPES[c.type]
        vals = "\n".join(f"{k} = {v}" for k, v in c.props.items() if k not in ("Thumbnail", "PortNames", "Ports"))
        extra = line_summary(self.editor.doc, c) if not self.ghost and c.type in CALC_TYPES else ""
        return f"{c.name}: {info.label}\n{info.help}" + (f"\n\n{vals}" if vals else "") + \
            (f"\n\n{extra}" if extra else "")

    def boundingRect(self) -> QRectF:
        w, h = body_size(self.comp)
        if self.comp.type in CK.NPORT_TYPES:
            return QRectF(-w / 2 - 14, -h / 2 - 36, w + 28, h + 72)
        r = max(w, h) / 2 + 160                # labels stay upright, beside the part
        return QRectF(-r, -r, 2 * r, 2 * r)

    def shape(self) -> QPainterPath:
        w, h = body_size(self.comp)
        path = QPainterPath()
        path.addRect(QRectF(-w / 2 - 4, -h / 2 - 4, w + 8, h + 8))
        return path

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(snap(value.x()), snap(value.y()))
        if change == QGraphicsItem.ItemPositionHasChanged and not self.ghost and self.scene() is not None \
                and self.scene().mouseGrabberItem() is not None:
            self.editor.drag_preview()
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        self.editor.items_moved()

    def mouseDoubleClickEvent(self, e) -> None:
        self.editor.edit_component(self.comp.name)

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        c = self.comp
        sel = self.isSelected()
        pen = QPen(QColor(GOLD) if sel else INKC, 2 if sel else 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setRenderHint(QPainter.Antialiasing)
        paint_symbol(p, c, pen, self._img)
        # name and values, upright and beside the part
        p.save()
        p.setTransform(self.transform().inverted()[0], True)
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        fm = QFontMetricsF(f)
        lh = fm.height()
        w, h = body_size(c)
        if c.rot % 180:
            w, h = h, w
        lines = value_lines(c)
        if c.type == "LABEL":
            text = c.props.get("Net", "")
            tw = fm.horizontalAdvance(text) + 8
            p.setPen(QPen(WIRE, 1.2))
            p.setBrush(QColor("#ffffff"))
            p.drawPolygon(QPolygonF([QPointF(0, -8), QPointF(5, -8 - lh / 2 - 1), QPointF(5 + tw, -8 - lh / 2 - 1),
                                     QPointF(5 + tw, -7 + lh / 2), QPointF(5, -7 + lh / 2)]))
            p.setPen(QPen(WIRE))
            p.drawText(QRectF(9, -8 - lh / 2, tw, lh), Qt.AlignLeft | Qt.AlignVCenter, text)
            p.restore()
            return
        if c.type == "GND":
            p.restore()
            return
        if c.type in ("MTEE", "MCORN", "MMBEND") and c.rot % 180 == 0:          # the text goes to the side away from the branch
            n = 1 + len(lines)
            y0 = -h / 2 - n * lh - 3 if (c.rot == 0) != c.mirror else h / 2 + 3
            p.setPen(QPen(INKC))
            p.drawText(QRectF(-110, y0, 220, lh), Qt.AlignCenter, c.name)
            p.setPen(QPen(QColor(MUTED)))
            for i, line in enumerate(lines):
                p.drawText(QRectF(-110, y0 + (i + 1) * lh, 220, lh), Qt.AlignCenter, line)
        elif h > w and c.type not in CK.NPORT_TYPES:          # vertical part: text to the right
            x, y0 = w / 2 + 6, -(1 + len(lines)) * lh / 2
            p.setPen(QPen(INKC))
            p.drawText(QRectF(x, y0, 220, lh), Qt.AlignLeft | Qt.AlignVCenter, c.name)
            p.setPen(QPen(QColor(MUTED)))
            for i, line in enumerate(lines):
                p.drawText(QRectF(x, y0 + (i + 1) * lh, 220, lh), Qt.AlignLeft | Qt.AlignVCenter, line)
        else:
            p.setPen(QPen(INKC))
            p.drawText(QRectF(-110, -h / 2 - lh - 3, 220, lh), Qt.AlignCenter, c.name)
            p.setPen(QPen(QColor(MUTED)))
            for i, line in enumerate(lines):
                p.drawText(QRectF(-110, h / 2 + 3 + i * lh, 220, lh), Qt.AlignCenter, line)
        p.restore()


class WireItem(QGraphicsPathItem):
    def __init__(self, index: int, wire: CK.Wire):
        super().__init__()
        self.index = index
        self.set_points(wire.points)
        self.setPen(QPen(WIRE, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.setFlags(QGraphicsItem.ItemIsSelectable)

    def set_points(self, pts) -> None:
        path = QPainterPath()
        if pts:
            path.moveTo(*pts[0])
            for q in pts[1:]:
                path.lineTo(*q)
        self.setPath(path)

    def paint(self, p, opt, w=None) -> None:
        self.setPen(QPen(QColor(GOLD) if self.isSelected() else WIRE, 2.4 if self.isSelected() else 1.8,
                         Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        super().paint(p, opt, w)

    def shape(self) -> QPainterPath:
        from PySide6.QtGui import QPainterPathStroker
        s = QPainterPathStroker()
        s.setWidth(8)
        return s.createStroke(self.path())


# ---- part library -------------------------------------------------------------------------------------
class PartLibrary(QWidget):
    """Parts by category with a search field; drag a part into the schematic or double-click it to place it."""

    def __init__(self, editor: "CircuitEditor"):
        super().__init__()
        self.editor = editor
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 0, 4)
        lay.setSpacing(4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parts...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        lay.addWidget(self.search)
        self.tree = _LibraryTree(self)
        lay.addWidget(self.tree, 1)
        self.fill()

    def fill(self, *_a) -> None:
        q = self.search.text().strip().lower()
        self.tree.clear()
        cats: dict[str, QTreeWidgetItem] = {}
        for t, info in CK.TYPES.items():
            if t == "EM":
                continue
            if q and q not in info.label.lower() and q not in info.help.lower() and q not in t.lower():
                continue
            if info.category not in cats:
                cats[info.category] = QTreeWidgetItem([info.category])
                cats[info.category].setFlags(Qt.ItemIsEnabled)
            it = QTreeWidgetItem([info.label])
            it.setIcon(0, component_icon(t))
            it.setToolTip(0, info.help)
            it.setData(0, Qt.UserRole, ("part", t))
            cats[info.category].addChild(it)
        for d in self.editor.doc.project.designs:
            if getattr(d, "kind", "3d") != "3d" or (q and q not in d.name.lower()):
                continue
            if "3D Designs" not in cats:
                cats["3D Designs"] = QTreeWidgetItem(["3D Designs"])
                cats["3D Designs"].setFlags(Qt.ItemIsEnabled)
            it = QTreeWidgetItem([d.name])
            it.setIcon(0, component_icon("EM"))
            it.setToolTip(0, "S-parameters of this 3D design as a block")
            it.setData(0, Qt.UserRole, ("design", d.name))
            cats["3D Designs"].addChild(it)
        for cat in list(CK.CATEGORIES) + ["3D Designs"]:
            if cat in cats:
                self.tree.addTopLevelItem(cats[cat])
                cats[cat].setExpanded(True)


class _LibraryTree(QTreeWidget):
    def __init__(self, lib: PartLibrary):
        super().__init__()
        self.lib = lib
        self.setHeaderHidden(True)
        self.setIconSize(QPixmap(28, 28).size())
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.itemDoubleClicked.connect(self._activate)

    def _activate(self, it, _col) -> None:
        d = it.data(0, Qt.UserRole)
        ed = self.lib.editor
        if d and d[0] == "part":
            ed.set_mode("place", d[1])
            ed.view.setFocus()
        elif d and d[0] == "design":
            r = ed.view.mapToScene(ed.view.viewport().rect().center())
            ed.add_em_block(d[1], snap(r.x()), snap(r.y()))

    def mimeTypes(self) -> list[str]:
        return [MIME_PART, MIME_DESIGN]

    def mimeData(self, items) -> QMimeData:
        md = QMimeData()
        for it in items:
            d = it.data(0, Qt.UserRole)
            if d and d[0] == "part":
                md.setData(MIME_PART, d[1].encode())
            elif d and d[0] == "design":
                md.setData(MIME_DESIGN, d[1].encode())
        return md


# ---- view ---------------------------------------------------------------------------------------------
class SchematicView(QGraphicsView):
    def __init__(self, editor: "CircuitEditor"):
        super().__init__(editor.scene)
        self.editor = editor
        self.setRenderHint(QPainter.Antialiasing)
        self.setAcceptDrops(True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.scale(1.5, 1.5)

    def drawBackground(self, p: QPainter, rect: QRectF) -> None:
        p.fillRect(rect, QColor("#fbfbfc"))
        step = CK.GRID * 2
        p.setPen(QPen(QColor("#d6dae1"), 0))
        x0 = int(rect.left()) - int(rect.left()) % step
        y0 = int(rect.top()) - int(rect.top()) % step
        pts = [QPointF(x, y) for x in range(x0, int(rect.right()) + step, step)
               for y in range(y0, int(rect.bottom()) + step, step)]
        if len(pts) < 40000:
            p.drawPoints(pts)

    def wheelEvent(self, e) -> None:
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def _accepts(self, md) -> bool:
        return md.hasFormat(MIME_DESIGN) or md.hasFormat(MIME_PART)

    def dragEnterEvent(self, e) -> None:
        if self._accepts(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:
        if self._accepts(e.mimeData()):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:
        md = e.mimeData()
        pos = self.mapToScene(e.position().toPoint())
        if md.hasFormat(MIME_DESIGN):
            self.editor.add_em_block(bytes(md.data(MIME_DESIGN)).decode(), snap(pos.x()), snap(pos.y()))
            e.acceptProposedAction()
        elif md.hasFormat(MIME_PART):
            t = bytes(md.data(MIME_PART)).decode()
            if t in CK.TYPES:
                self.editor.place(t, snap(pos.x()), snap(pos.y()))
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    def mousePressEvent(self, e) -> None:
        if self.editor.handle_press(self.mapToScene(e.position().toPoint()), e.button()):
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        self.editor.handle_move(self.mapToScene(e.position().toPoint()))
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        if self.editor.mode == "wire":
            self.editor.finish_wire()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k == Qt.Key_Escape:
            self.editor.cancel()
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.editor.delete_selected()
        elif k == Qt.Key_R:
            self.editor.rotate_selected()
        elif k == Qt.Key_M:
            self.editor.mirror_selected()
        elif k == Qt.Key_W:
            self.editor.set_mode("wire")
        elif k == Qt.Key_G:
            self.editor.set_mode("place", "GND")
        elif k == Qt.Key_F:
            self.editor.fit_all()
        else:
            super().keyPressEvent(e)


class CircuitEditor(QWidget):
    """Schematic of the active circuit design; changes go through the document (undo, dirty flag)."""
    analyzeRequested = Signal()
    reportRequested = Signal()
    modeChanged = Signal(str)
    componentsSelected = Signal(list)          # names of the selected parts (Properties panel)

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.mode = "select"
        self.place_type = ""
        self.wire_pts: list[list[int]] = []
        self._preview: QGraphicsPathItem | None = None
        self._ghost: ComponentItem | None = None
        self._dragging_wires: list | None = None     # wires redrawn while parts are dragged
        self._drag_extra: list = []
        self._ghost_rot, self._ghost_mirror = 0, False
        self._last_paste: tuple[int, int] | None = None    # where the last paste went (repeated pastes step on)
        self.library: PartLibrary | None = None
        self.scene = QGraphicsScene(self)
        self._rebuilding = False
        self.scene.selectionChanged.connect(self._selection_changed)
        self.scene.setSceneRect(-3000, -3000, 6000, 6000)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.hint = QLabel()
        self.hint.setStyleSheet(f"color: {MUTED}; padding: 3px 8px;")
        self.view = SchematicView(self)
        self.library = PartLibrary(self)
        split = QSplitter()
        split.addWidget(self.library)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.hint)
        rl.addWidget(self.view, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([210, 1000])
        lay.addWidget(split, 1)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context)
        self.set_mode("select")

    # ---- model access -------------------------------------------------------------------------
    @property
    def des(self):
        return self.doc.design

    def comp(self, name: str) -> CK.Component:
        return next(c for c in self.des.components if c.name == name)

    def _change(self) -> None:
        self.doc.checkpoint()

    def _commit(self) -> None:
        self.doc.commit("circuit", geometry=False)
        self.rebuild()

    def rebuild(self) -> None:
        if getattr(self.des, "kind", "3d") != "circuit":
            return
        sel = {it.comp.name for it in self.selected_components()}
        self._preview = None
        self._ghost = None
        self._dragging_wires, self._drag_extra = None, []
        self._rebuilding = True
        self.scene.clear()
        for i, w in enumerate(self.des.wires):
            self.scene.addItem(WireItem(i, w))
        for c in self.des.components:
            it = ComponentItem(self, c)
            self.scene.addItem(it)
            it.setSelected(c.name in sel)
        self._rebuilding = False
        # junction dots where three or more connections meet
        count: dict[tuple[int, int], int] = {}
        for w in self.des.wires:
            pts = [tuple(p) for p in w.points]
            for k, q in enumerate(pts):
                count[q] = count.get(q, 0) + (1 if k in (0, len(pts) - 1) else 2)
        for c in self.des.components:
            for q in CK.pins(c):
                count[q] = count.get(q, 0) + 1
        for q, n in count.items():
            if n >= 3:
                self.scene.addEllipse(q[0] - 3, q[1] - 3, 6, 6, QPen(Qt.NoPen), QBrush(WIRE))
        # automatic junction models: a small diamond with what was found
        if self.des.settings.get(CK.AUTO, "True") != "False":
            for j in CK.find_junctions(self.des.components, self.des.wires):
                x, y = j.point
                it = self.scene.addPolygon(QPolygonF([QPointF(x, y - 7), QPointF(x + 7, y), QPointF(x, y + 7),
                                                      QPointF(x - 7, y)]),
                                           QPen(QColor("#b05a00"), 1.2), QBrush(QColor("#ffd9a8")))
                it.setZValue(5)
                vals = "  ".join(f"{k}={v}" for k, v in j.props.items() if k != "Subst")
                it.setToolTip(f"Automatic {CK.TYPES[j.type].label}: {vals}\nLines: "
                              + ", ".join(a for a, _ in j.arms)
                              + "\nThe lines attach at its edges. Place a junction part here (or switch "
                                "Automatic off) to set it yourself.")
        if self.library is not None:
            self.library.fill()
        if self.mode == "place":
            self._make_ghost()

    def fit_all(self) -> None:
        r = QRectF()
        for it in self.scene.items():
            if isinstance(it, ComponentItem) and not it.ghost:
                w, h = body_size(it.comp)
                d = max(w, h) / 2 + 40
                r = r.united(QRectF(it.pos().x() - d, it.pos().y() - d, 2 * d, 2 * d))
            elif isinstance(it, WireItem):
                r = r.united(it.path().boundingRect())
        if r.isValid():
            self.view.fitInView(r.adjusted(-30, -30, 30, 30), Qt.KeepAspectRatio)

    def zoom(self, f: float) -> None:
        self.view.scale(f, f)

    # ---- modes --------------------------------------------------------------------------------
    def set_mode(self, mode: str, ctype: str = "") -> None:
        self.cancel(keep_mode=True)
        self.mode, self.place_type = mode, ctype
        self._ghost_rot, self._ghost_mirror = 0, False
        self.view.setDragMode(QGraphicsView.RubberBandDrag if mode == "select" else QGraphicsView.NoDrag)
        self.view.viewport().setCursor(Qt.ArrowCursor if mode == "select" else Qt.CrossCursor)
        if mode == "place":
            self._make_ghost()
        self.hint.setText({
            "select": "Drag parts from the library or a 3D design from the Project Manager into the schematic. "
                      "Double-click a part to edit it; R rotates, M mirrors, Del deletes, Ctrl+C / Ctrl+V copy "
                      "and paste, W draws wires, F fits.",
            "wire": "Wire: click the corners; a click on a pin or a wire ends it, as do a double-click and a "
                    "right-click. Esc cancels.",
            "place": f"{CK.TYPES[ctype].label if ctype else ''}: click to place, R rotates, M mirrors, "
                     "right-click or Esc ends."}[mode])
        self.modeChanged.emit(mode + (f":{ctype}" if ctype else ""))

    def _make_ghost(self) -> None:
        if self._ghost is not None:
            self.scene.removeItem(self._ghost)
        t = self.place_type
        c = CK.Component(CK.next_name(self.des.components, t), t, -10000, -10000, self._ghost_rot,
                         dict(CK.TYPES[t].props), self._ghost_mirror)
        if t == "PORT":
            c.props["Num"] = str(len([x for x in self.des.components if x.type == "PORT"]) + 1)
        self._ghost = ComponentItem(self, c, ghost=True)
        self.scene.addItem(self._ghost)

    def cancel(self, keep_mode: bool = False) -> None:
        self.wire_pts = []
        if self._preview is not None:
            self.scene.removeItem(self._preview)
            self._preview = None
        if self._ghost is not None:
            self.scene.removeItem(self._ghost)
            self._ghost = None
        if not keep_mode and self.mode != "select":
            self.set_mode("select")

    def _connection_points(self) -> set[tuple[int, int]]:
        pts = {q for c in self.des.components for q in CK.pins(c)}
        for w in self.des.wires:
            pts |= {tuple(p) for p in w.points}
        return pts

    def _on_wire(self, x: int, y: int) -> bool:
        for w in self.des.wires:
            pts = [tuple(p) for p in w.points]
            if any(CK._on_segment((x, y), a, b) for a, b in zip(pts, pts[1:])):
                return True
        return False

    def handle_press(self, pos: QPointF, button) -> bool:
        x, y = snap(pos.x()), snap(pos.y())
        if self.mode == "place":
            if button == Qt.RightButton:
                self.set_mode("select")
                return True
            c = self.place(self.place_type, x, y, None, self._ghost_rot, self._ghost_mirror)
            if c is not None and self.place_type in ("SNP", "SUBST"):
                self.set_mode("select")
            return True
        if self.mode == "wire":
            if button == Qt.RightButton:
                self.finish_wire()
                return True
            if self.wire_pts and self.wire_pts[-1] != [x, y]:
                lx, ly = self.wire_pts[-1]
                if lx != x and ly != y:                  # orthogonal: corner first along the longer leg
                    self.wire_pts.append([x, ly] if abs(x - lx) >= abs(y - ly) else [lx, y])
            if not self.wire_pts or self.wire_pts[-1] != [x, y]:
                self.wire_pts.append([x, y])
            if len(self.wire_pts) >= 2 and ((x, y) in self._connection_points() or self._on_wire(x, y)):
                self.finish_wire()                       # ended on a pin or a wire
                return True
            self._update_preview(x, y)
            return True
        return False

    def handle_move(self, pos: QPointF) -> None:
        x, y = snap(pos.x()), snap(pos.y())
        if self.mode == "wire" and self.wire_pts:
            self._update_preview(x, y)
        elif self.mode == "place" and self._ghost is not None:
            self._ghost.setPos(x, y)

    def _update_preview(self, x: int, y: int) -> None:
        path = QPainterPath()
        path.moveTo(*self.wire_pts[0])
        for q in self.wire_pts[1:]:
            path.lineTo(*q)
        lx, ly = self.wire_pts[-1]
        if (lx, ly) != (x, y):
            if lx != x and ly != y:
                path.lineTo(*((x, ly) if abs(x - lx) >= abs(y - ly) else (lx, y)))
            path.lineTo(x, y)
        if self._preview is None:
            self._preview = self.scene.addPath(path, QPen(WIRE, 1.4, Qt.DashLine))
        else:
            self._preview.setPath(path)

    def finish_wire(self) -> None:
        pts = self.wire_pts
        self.wire_pts = []
        if self._preview is not None:
            self.scene.removeItem(self._preview)
            self._preview = None
        clean = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        if len(clean) >= 2:
            self._change()
            self.des.wires.append(CK.Wire(clean))
            self._commit()

    # ---- editing ------------------------------------------------------------------------------
    def place(self, ctype: str, x: int, y: int, props: dict | None = None, rot: int = 0,
              mirror: bool = False) -> CK.Component | None:
        c = CK.Component(CK.next_name(self.des.components, ctype), ctype, x, y, rot,
                         {**CK.TYPES[ctype].props, **(props or {})}, mirror)
        if ctype == "PORT" and not c.props.get("Num"):
            c.props["Num"] = str(len([o for o in self.des.components if o.type == "PORT"]) + 1)
        if ctype in CK.LINE_TYPES:
            subs = [o.name for o in self.des.components if o.type == "SUBST"]
            if subs and c.props.get("Subst") not in subs:
                c.props["Subst"] = subs[0]
        if ctype == "SNP" and not c.props.get("File"):
            path, _ = QFileDialog.getOpenFileName(self, "Touchstone File", "", "Touchstone (*.s*p)")
            if not path:
                return None
            c.props["File"] = path
            try:
                from ..model.touchstone import read
                c.props["Ports"] = str(read(path)[1].shape[1])
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Touchstone", str(e))
                return None
        self._change()
        self.des.components.append(c)
        if ctype in CK.LINE_TYPES and not any(o.type == "SUBST" for o in self.des.components):
            # the first microstrip or stripline brings its substrate along
            self.des.components.append(CK.Component(c.props.get("Subst", "Sub1"), "SUBST", x, y - 120, 0,
                                                    dict(CK.TYPES["SUBST"].props)))
        self._commit()
        return c

    def add_em_block(self, design: str, x: int, y: int) -> None:
        """A 3D design dropped from the Project Manager: a block with one pin per port."""
        from ..solver.circuit import em_port_names, em_solutions
        try:
            src = self.doc.project.design(design)
        except KeyError:
            return
        if getattr(src, "kind", "3d") != "3d" or design == self.des.name:
            return
        sols = em_solutions(self.doc.project, design)
        sol = next((s for s in sols if "LastAdaptive" not in s), sols[0] if sols else "")
        names = em_port_names(self.doc.project, design, sol)
        if not names:
            QMessageBox.information(self, "3D design block", f"{design} has no ports. Add ports to it first.")
            return
        self.place("EM", x, y, {"Design": design, "Solution": sol, "Ports": str(len(names)),
                                "PortNames": ",".join(names), "Thumbnail": design_thumbnail(self.doc, design)})
        if not sols:
            self.hint.setText(f"{design} is not solved yet: analyze it before the circuit.")

    def update_block(self, name: str) -> None:
        """Ports, solution and picture of a 3D design block from the current 3D design."""
        from ..solver.circuit import em_port_names, em_solutions
        c = self.comp(name)
        design = c.props.get("Design", "")
        sols = em_solutions(self.doc.project, design)
        sol = c.props.get("Solution", "")
        if sol not in sols:
            sol = next((s for s in sols if "LastAdaptive" not in s), sols[0] if sols else "")
        names = em_port_names(self.doc.project, design, sol)
        self._change()
        c.props.update({"Solution": sol, "Ports": str(max(1, len(names))), "PortNames": ",".join(names)})
        img = design_thumbnail(self.doc, design)
        if img:
            c.props["Thumbnail"] = img
        self._commit()

    def selected_components(self) -> list[ComponentItem]:
        return [it for it in self.scene.selectedItems() if isinstance(it, ComponentItem) and not it.ghost]

    def _moves(self) -> dict[str, tuple[int, int]]:
        """Components dragged away from their place: {name: (dx, dy)}."""
        out = {}
        for it in self.scene.items():
            if isinstance(it, ComponentItem) and not it.ghost:
                d = (snap(it.pos().x()) - it.comp.x, snap(it.pos().y()) - it.comp.y)
                if d != (0, 0):
                    out[it.comp.name] = d
        return out

    def drag_preview(self) -> None:
        """While parts are dragged, the wires on their pins follow (shown only; the release commits)."""
        if self._dragging_wires is None:
            self._dragging_wires = [it for it in self.scene.items() if isinstance(it, WireItem)]
        new = CK.drag_wires(self.des.components, self.des.wires, self._moves())
        items = sorted(self._dragging_wires, key=lambda it: it.index)
        for it in items:
            it.set_points(new[it.index].points if it.index < len(new) else [])
        for extra in self._drag_extra:
            self.scene.removeItem(extra)
        self._drag_extra = []
        for i, w in enumerate(new[len(self.des.wires):]):
            it = WireItem(-1 - i, w)
            it.setFlags(QGraphicsItem.GraphicsItemFlag(0))
            self.scene.addItem(it)
            self._drag_extra.append(it)

    def _selection_changed(self) -> None:
        if not self._rebuilding:
            try:
                self.componentsSelected.emit([it.comp.name for it in self.selected_components()])
            except RuntimeError:                    # the scene is being destroyed
                pass

    def items_moved(self) -> None:
        moves = self._moves()
        self._dragging_wires = None
        for extra in self._drag_extra:
            self.scene.removeItem(extra)
        self._drag_extra = []
        if not moves:
            return
        self._change()
        self.des.wires = CK.drag_wires(self.des.components, self.des.wires, moves)
        for c in self.des.components:
            if c.name in moves:
                c.x, c.y = c.x + moves[c.name][0], c.y + moves[c.name][1]
        self._commit()

    def delete_selected(self) -> None:
        comps = {it.comp.name for it in self.selected_components()}
        wires = {it.index for it in self.scene.selectedItems() if isinstance(it, WireItem)}
        if not comps and not wires:
            return
        self._change()
        self.des.components = [c for c in self.des.components if c.name not in comps]
        self.des.wires = [w for i, w in enumerate(self.des.wires) if i not in wires]
        self._commit()

    def rotate_selected(self) -> None:
        if self.mode == "place":
            self._ghost_rot = (self._ghost_rot + 90) % 360
            if self._ghost is not None:
                self._ghost.comp.rot = self._ghost_rot
                self._ghost.sync_transform()
            return
        sel = self.selected_components()
        if not sel:
            return
        self._change()
        for it in sel:
            it.comp.rot = (it.comp.rot + 90) % 360
        self._commit()

    def mirror_selected(self) -> None:
        if self.mode == "place":
            self._ghost_mirror = not self._ghost_mirror
            if self._ghost is not None:
                self._ghost.comp.mirror = self._ghost_mirror
                self._ghost.sync_transform()
            return
        sel = self.selected_components()
        if not sel:
            return
        self._change()
        for it in sel:
            it.comp.mirror = not it.comp.mirror
        self._commit()

    # ---- clipboard ----------------------------------------------------------------------------
    def can_paste(self) -> bool:
        return QApplication.clipboard().mimeData().hasFormat(MIME_CIRCUIT)

    def copy_selected(self) -> bool:
        """Selected parts and wires to the clipboard (also into other circuit designs and Hertzon windows)."""
        names = [it.comp.name for it in self.selected_components()]
        wires = [it.index for it in self.scene.selectedItems() if isinstance(it, WireItem) and it.index >= 0]
        if not names and not wires:
            return False
        data = CK.copy_parts(self.des.components, self.des.wires, names, wires)
        md = QMimeData()
        md.setData(MIME_CIRCUIT, QByteArray(json.dumps(data).encode()))
        QApplication.clipboard().setMimeData(md)
        self._last_paste = None
        n = len(data["components"])
        self.hint.setText(f"Copied {n} part{'s' if n != 1 else ''}"
                          + (f" and {len(data['wires'])} wire{'s' if len(data['wires']) != 1 else ''}"
                             if data["wires"] else "") + ". Ctrl+V pastes at the mouse pointer.")
        return True

    def paste(self) -> bool:
        """The clipboard parts at the mouse pointer (centred on it), else shifted from where they were copied.
        Pasting again without moving the mouse steps on, so copies never land on top of each other."""
        md = QApplication.clipboard().mimeData()
        if not md.hasFormat(MIME_CIRCUIT):
            return False
        try:
            data = json.loads(bytes(md.data(MIME_CIRCUIT)).decode())
        except (ValueError, UnicodeDecodeError):
            return False
        src, wires = CK.from_dict(data)
        if not src and not wires:
            return False
        xs = [c.x for c in src] + [p[0] for w in wires for p in w.points]
        ys = [c.y for c in src] + [p[1] for w in wires for p in w.points]
        cx, cy = snap((min(xs) + max(xs)) / 2), snap((min(ys) + max(ys)) / 2)
        step = 4 * CK.GRID
        vp = self.view.viewport()
        local = vp.mapFromGlobal(QCursor.pos())
        if vp.rect().contains(local):
            m = self.view.mapToScene(local)
            tx, ty = snap(m.x()), snap(m.y())
        else:
            tx, ty = cx + step, cy + step
        if self._last_paste is not None and (tx, ty) in (self._last_paste, (cx, cy)):
            lx, ly = self._last_paste
            tx, ty = lx + step, ly + step
        if (tx, ty) == (cx, cy):
            tx, ty = cx + step, cy + step
        self._last_paste = (tx, ty)
        self.cancel()
        new, new_w = CK.paste_parts(self.des.components, data, tx - cx, ty - cy)
        self._change()
        first_wire = len(self.des.wires)
        self.des.components.extend(new)
        self.des.wires.extend(new_w)
        self._commit()
        names = {c.name for c in new}
        self.scene.clearSelection()
        for it in self.scene.items():
            if isinstance(it, ComponentItem) and not it.ghost and it.comp.name in names:
                it.setSelected(True)
            elif isinstance(it, WireItem) and it.index >= first_wire:
                it.setSelected(True)
        return True

    def edit_component(self, name: str) -> None:
        c = self.comp(name)
        dlg = ComponentDialog(self.doc, c, self)
        if dlg.exec() != QDialog.Accepted:
            return
        new_name, props = dlg.values()
        if new_name != c.name and any(o.name == new_name for o in self.des.components):
            QMessageBox.warning(self, "Component", f"A component named {new_name} exists already.")
            return
        self._change()
        if c.type == "SUBST" and new_name != c.name:      # the lines follow a renamed substrate
            for o in self.des.components:
                if o.props.get("Subst") == c.name:
                    o.props["Subst"] = new_name
        c.name = new_name
        c.props.update(props)
        if c.type == "EM":
            from ..solver.circuit import em_port_names
            names = em_port_names(self.doc.project, c.props.get("Design", ""), c.props.get("Solution", ""))
            if names:
                c.props["Ports"], c.props["PortNames"] = str(len(names)), ",".join(names)
            img = design_thumbnail(self.doc, c.props.get("Design", ""))
            if img:
                c.props["Thumbnail"] = img
        elif c.type == "SNP" and c.props.get("File"):
            try:
                from ..model.touchstone import read
                c.props["Ports"] = str(read(c.props["File"])[1].shape[1])
            except Exception:  # noqa: BLE001
                pass
        self._commit()

    def _context(self, pos) -> None:
        if self.mode == "wire" and self.wire_pts:
            self.finish_wire()
            return
        if self.mode != "select":
            self.set_mode("select")
            return
        it = self.view.itemAt(pos)
        m = QMenu(self)
        if isinstance(it, ComponentItem):
            if not it.isSelected():
                self.scene.clearSelection()
                it.setSelected(True)
            m.addAction("Properties...", lambda: self.edit_component(it.comp.name))
            if it.comp.type == "EM":
                m.addAction("Update ports and picture", lambda: self.update_block(it.comp.name))
                m.addAction("Open 3D design", lambda: self.doc.set_design(it.comp.props.get("Design", "")))
            m.addAction("Rotate (R)", self.rotate_selected)
            m.addAction("Mirror (M)", self.mirror_selected)
            m.addAction("Copy (Ctrl+C)", self.copy_selected)
            m.addAction("Delete", self.delete_selected)
        elif isinstance(it, WireItem):
            if not it.isSelected():
                self.scene.clearSelection()
                it.setSelected(True)
            m.addAction("Copy (Ctrl+C)", self.copy_selected)
            m.addAction("Delete", self.delete_selected)
        else:
            sp = self.view.mapToScene(pos)
            for cat in CK.CATEGORIES:
                sub = m.addMenu(cat)
                for t, info in CK.TYPES.items():
                    if info.category == cat and t not in CK.NPORT_TYPES:
                        sub.addAction(component_icon(t), info.label,
                                      lambda t=t: self.place(t, snap(sp.x()), snap(sp.y())))
            m.addSeparator()
            a = m.addAction("Paste (Ctrl+V)", self.paste)
            a.setEnabled(self.can_paste())
            m.addAction("Fit All (F)", self.fit_all)
            m.addAction("Analyze", self.analyzeRequested.emit)
        m.exec(self.view.viewport().mapToGlobal(pos))


LABELS = {"Z0": "Impedance Z0", "E": "Electrical length", "F": "at frequency", "Num": "Port number",
          "Er": "Permittivity Er", "H": "Height H", "T": "Metal thickness T", "TanD": "Loss tangent",
          "Cond": "Conductivity (S/m)", "W": "Width W", "S": "Spacing (gap) S", "L": "Length L", "Di": "Inner diameter",
          "Do": "Outer diameter", "Subst": "Substrate", "Net": "Net name", "Loss": "Attenuation",
          "W1": "Width W1 (main line)", "W2": "Width W2 (main line)", "W3": "Width W3 (branch)", "W4": "Width W4",
          "Backed": "Ground under the substrate", "D": "Via diameter D", "N": "Number of vias N",
          "P": "Pitch P (centre to centre)"}


CALC_TYPES = ("MLIN", "MLOC", "MLSC", "SLIN")


def line_summary(doc, c: CK.Component) -> str:
    """Z0, effective permittivity and electrical length of a line at the middle of the sweep (tooltip)."""
    import math

    from ..solver import tlines as TL
    try:
        design = doc.design
        ev = design.evaluator(doc.project)
        sub = next(o for o in design.components if o.type == "SUBST" and o.name == c.props.get("Subst"))
        sp = sub.props
        f = ev.freq(_center_frequency(design))
        w, ln = ev.length(c.props["W"]), ev.length(c.props["L"])
        z0, g = TL.line_z0_gamma("SLIN" if c.type == "SLIN" else "MLIN", f, w, ev.length(sp["H"]),
                                 ev.length(sp["T"]), ev.value(sp["Er"]), ev.value(sp["TanD"]),
                                 ev.value(sp["Cond"]))
        ee = (g.imag * TL.C0 / (2 * math.pi * f)) ** 2
        return f"At {f / 1e9:g} GHz: Z0 = {z0:.2f} ohm, eeff = {ee:.3f}, E = {math.degrees(g.imag * ln):.1f} deg"
    except Exception:  # noqa: BLE001 (no substrate or an incomplete value)
        return ""


def _center_frequency(design) -> str:
    """The middle of the first sweep of a circuit design (the calculator's default frequency)."""
    try:
        sw = design.setups[0].sweeps[0].props
        ev = design.evaluator(None)
        return f"{(ev.freq(sw['Start']) + ev.freq(sw['Stop'])) / 2e9:g}GHz"
    except Exception:  # noqa: BLE001
        return "1GHz"


class ComponentDialog(QDialog):
    def __init__(self, doc, comp: CK.Component, parent=None):
        super().__init__(parent)
        self.doc, self.comp = doc, comp
        info = CK.TYPES[comp.type]
        self.setWindowTitle(f"{info.label}: {comp.name}")
        lay = QVBoxLayout(self)
        head = QLabel(info.help)
        head.setWordWrap(True)
        head.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(head)
        form = QFormLayout()
        self.name = QLineEdit(comp.name)
        form.addRow("Name", self.name)
        self.fields: dict[str, object] = {}
        if comp.type == "EM":
            from ..solver.circuit import em_solutions
            self.design = QComboBox()
            self.design.addItems([d.name for d in doc.project.designs if getattr(d, "kind", "3d") == "3d"])
            self.design.setCurrentText(comp.props.get("Design", ""))
            self.solution = QComboBox()

            def fill():
                self.solution.clear()
                self.solution.addItems(em_solutions(doc.project, self.design.currentText()))
                self.solution.setCurrentText(comp.props.get("Solution", ""))
            self.design.currentTextChanged.connect(lambda *_: fill())
            fill()
            form.addRow("3D design", self.design)
            form.addRow("Solution", self.solution)
        elif comp.type == "SNP":
            row = QHBoxLayout()
            self.file = QLineEdit(comp.props.get("File", ""))
            b = QPushButton("...")
            b.clicked.connect(self._browse)
            row.addWidget(self.file, 1)
            row.addWidget(b)
            w = QWidget()
            w.setLayout(row)
            form.addRow("File", w)
        else:
            for k, default in info.props.items():
                if k == "Subst":
                    e = QComboBox()
                    e.setEditable(True)
                    e.addItems([o.name for o in doc.design.components if o.type == "SUBST"])
                    e.setCurrentText(comp.props.get(k, default))
                elif k == "Backed":
                    e = QComboBox()
                    e.addItems(["False", "True"])
                    e.setCurrentText(comp.props.get(k, default))
                else:
                    e = QLineEdit(comp.props.get(k, default))
                self.fields[k] = e
                form.addRow(LABELS.get(k, k), e)
        lay.addLayout(form)
        if comp.type in CALC_TYPES:
            lay.addWidget(self._calculator())
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ---- line calculator: Z0 and electrical length <-> W and L --------------------------------------
    def _calculator(self) -> QWidget:
        from PySide6.QtWidgets import QGroupBox
        box = QGroupBox("Line calculator")
        g = QFormLayout(box)
        self.info = QLabel()
        self.info.setStyleSheet(f"color: {MUTED};")
        self.calc_z0 = QLineEdit("50ohm")
        self.calc_e = QLineEdit("90deg")
        self.calc_f = QLineEdit(_center_frequency(self.doc.design))
        go = QPushButton("Calculate W and L")
        go.clicked.connect(self._synthesize)
        g.addRow("Now", self.info)
        g.addRow("Impedance Z0", self.calc_z0)
        g.addRow("Electrical length", self.calc_e)
        g.addRow("at frequency", self.calc_f)
        g.addRow("", go)
        for k in ("W", "L", "Subst"):
            e = self.fields.get(k)
            sig = getattr(e, "textChanged", None) or getattr(e, "currentTextChanged", None)
            if sig is not None:
                sig.connect(lambda *_: self._analyze())
        self.calc_f.textChanged.connect(lambda *_: self._analyze())
        self._analyze()
        return box

    def _substrate(self, ev) -> dict | None:
        name = self._text(self.fields["Subst"])
        sub = next((c for c in self.doc.design.components if c.type == "SUBST" and c.name == name), None)
        if sub is None:
            return None
        p = sub.props
        return dict(h=ev.length(p["H"]), t=ev.length(p["T"]), er=ev.value(p["Er"]), tand=ev.value(p["TanD"]),
                    cond=ev.value(p["Cond"]))

    def _analyze(self) -> None:
        import math

        from ..solver import tlines as TL
        ev = self.doc.design.evaluator(self.doc.project)
        try:
            s = self._substrate(ev)
            if s is None:
                self.info.setText("no substrate of this name")
                return
            f = ev.freq(self.calc_f.text())
            w, ln = ev.length(self._text(self.fields["W"])), ev.length(self._text(self.fields["L"]))
            kind = "SLIN" if self.comp.type == "SLIN" else "MLIN"
            z0, g = TL.line_z0_gamma(kind, f, w, s["h"], s["t"], s["er"], s["tand"], s["cond"])
            ee = (g.imag * TL.C0 / (2 * math.pi * f)) ** 2
            deg = math.degrees(g.imag * ln)
            self.info.setText(f"Z0 = {z0:.2f} ohm   eeff = {ee:.3f}   E = {deg:.1f} deg")
        except Exception as e:  # noqa: BLE001 (incomplete input while typing)
            self.info.setText(f"({e})")

    def _synthesize(self) -> None:
        import math

        from ..solver import tlines as TL
        ev = self.doc.design.evaluator(self.doc.project)
        try:
            s = self._substrate(ev)
            if s is None:
                raise ValueError("no substrate of this name")
            f, z0, e = ev.freq(self.calc_f.text()), ev.value(self.calc_z0.text()), ev.angle(self.calc_e.text())
            kind = "SLIN" if self.comp.type == "SLIN" else "MLIN"
            w = TL.synthesize(kind, z0, f, s["h"], s["t"], s["er"], s["tand"], s["cond"])
            g = TL.line_z0_gamma(kind, f, w, s["h"], s["t"], s["er"], s["tand"], s["cond"])[1]
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Line calculator", str(ex))
            return
        scale, unit = 1e3, "mm"
        self.fields["W"].setText(f"{w * scale:.4g}{unit}")
        if self.comp.type != "MLOC":
            self.fields["L"].setText(f"{e / g.imag * scale:.4g}{unit}")
        else:                                     # the open end adds its length extension
            ext = TL.open_end_extension(w, s["h"], s["t"], s["er"])
            self.fields["L"].setText(f"{max(e / g.imag - ext, 0) * scale:.4g}{unit}")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Touchstone File", self.file.text(), "Touchstone (*.s*p)")
        if path:
            self.file.setText(path)

    def _text(self, e) -> str:
        return (e.currentText() if isinstance(e, QComboBox) else e.text()).strip()

    def _ok(self) -> None:
        ev = self.doc.design.evaluator(self.doc.project)
        for k, e in self.fields.items():
            if k in ("Subst", "Net") or (k == "Num" and not self._text(e)):
                continue
            text = self._text(e).replace("dB", "") if k == "Loss" else self._text(e)
            msg = ev.check(text)
            if msg:
                QMessageBox.warning(self, "Component", f"{LABELS.get(k, k)}: {msg}")
                return
        self.accept()

    def values(self) -> tuple[str, dict]:
        name = self.name.text().strip() or self.comp.name
        if self.comp.type == "EM":
            return name, {"Design": self.design.currentText(), "Solution": self.solution.currentText()}
        if self.comp.type == "SNP":
            return name, {"File": self.file.text().strip()}
        return name, {k: self._text(e) for k, e in self.fields.items()}
