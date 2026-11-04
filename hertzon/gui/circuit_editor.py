"""Schematic editor of a circuit design: components, wires, ports and 3D design blocks.

Place a component with its toolbar button and a click, draw wires with the Wire tool (click the corners,
double-click or right-click to end), drag a 3D design from the Project Manager into the schematic to use its
S-parameters as a block. Double-click a component to edit it, R rotates, Del deletes."""
from __future__ import annotations

import base64

import numpy as np
from PySide6.QtCore import QByteArray, QBuffer, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                           QPolygonF)
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QMessageBox, QPushButton, QToolBar, QVBoxLayout, QWidget)

from ..model import circuit as CK
from .theme import GOLD, INK, MUTED

from .trees import MIME_DESIGN
INKC = QColor(INK)
PIN = QColor("#1d4f91")
WIRE = QColor("#1f6f3f")
THUMB = 76                                    # thumbnail size of a 3D design block (pixels)


def snap(v: float) -> int:
    return int(round(v / CK.GRID)) * CK.GRID


# ---- 3D design thumbnail ------------------------------------------------------------------------------
def design_thumbnail(doc, name: str, size: int = 2 * THUMB) -> str:
    """PNG (base64) of a 3D design seen from above at an angle, drawn from its display tessellation, so the
    block looks like the structure it stands for. '' when no tessellation of the design is available yet
    (the design was not shown in this session and has no cached tessellation)."""
    try:
        des = doc.project.design(name)
    except KeyError:
        return ""
    tess = {}
    cache = doc.cache if name == doc.design_name else (doc.design_caches.get(name, ({}, {}))[0])
    for o in des.live_objects():
        if not o.model or o.history[0].kind == "CreateRegion":
            continue
        e = cache.get(o.name)
        t = getattr(e, "tess", None) if e is not None else None
        if t is None:
            try:
                from ..kernel import occ as K
                f = K._tess_cache_file(des, o, des.evaluator(doc.project), getattr(doc, "display_quality", "normal"))
                t = K.load_tess(f) if f is not None and f.exists() else None
            except Exception:  # noqa: BLE001
                t = None
        if t is not None and len(t.tris):
            tess[o.name] = (t, o)
    if not tess:
        return ""
    # leave out air boxes (vacuum solids) and the port sheets; conductor sheets are drawn as copper on top
    ports = {n for e in des.excitations for n in e.objects}
    keep = {k: v for k, v in tess.items()
            if k not in ports and not (v[1].dim == 3 and str(v[1].material).lower() in ("vacuum", "air"))}
    tess = keep or tess
    # a sheet under an opaque solid (the ground plane under the substrate) is hidden: leave it out, the rest of the
    # sheets lie on top of the solids
    def box(t):
        q = np.asarray(t.points, float)
        return q.min(axis=0), q.max(axis=0)
    solids = [box(t) for t, o in tess.values() if o.dim == 3 and float(getattr(o, "transparency", 0) or 0) < 0.5]

    def hidden(t):
        lo, hi = box(t)
        tol = 1e-6 * max(1e-12, float(np.max(hi - lo)))
        return any(np.all(lo[:2] < shi[:2]) and np.all(hi[:2] > slo[:2]) and shi[2] > hi[2] + tol
                   for slo, shi in solids)
    tess = {k: v for k, v in tess.items() if v[1].dim == 3 or not hidden(v[0])} or tess
    # view: azimuth -30 deg, elevation 55 deg (mostly from above: planar structures keep their shape)
    az, el = np.radians(-30), np.radians(55)
    R = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    E = np.array([[1, 0, 0], [0, np.cos(el), -np.sin(el)], [0, np.sin(el), np.cos(el)]])
    M = E @ R
    light = np.array([0.3, -0.5, 0.8])
    light /= np.linalg.norm(light)
    polys = []
    allp = []
    for t, o in tess.values():
        P = np.asarray(t.points, float) @ M.T
        allp.append(P)
        sheet = o.dim < 3
        col = QColor("#c9952f") if sheet and str(o.material).lower() in ("vacuum", "air") else \
            QColor(getattr(o, "color", "#b0b0b0") or "#b0b0b0")
        tr = np.asarray(t.tris, int)
        a, b, c = P[tr[:, 0]], P[tr[:, 1]], P[tr[:, 2]]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n, axis=1)
        ok = ln > 0
        n[ok] /= ln[ok, None]
        shade = 0.45 + 0.55 * np.abs(n @ (M @ light))
        depth = (a[:, 1] + b[:, 1] + c[:, 1]) / 3          # y after the view rotation points into the screen
        alpha = 255 if float(getattr(o, "transparency", 0) or 0) < 0.5 else 110
        for k in range(len(tr)):
            polys.append((int(sheet), depth[k], (a[k], b[k], c[k]), col, shade[k], alpha))
    allp = np.vstack(allp)
    x0, x1 = allp[:, 0].min(), allp[:, 0].max()
    z0, z1 = allp[:, 2].min(), allp[:, 2].max()
    span = max(x1 - x0, z1 - z0) or 1.0
    s = (size - 8) / span
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    polys.sort(key=lambda q: (q[0], -q[1]))              # solids back to front, then the sheets on them
    for _, _, tri, col, sh, alpha in polys:
        c = QColor(int(col.red() * sh), int(col.green() * sh), int(col.blue() * sh), alpha)
        p.setPen(QPen(c, 0.6))
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(size / 2 + (v[0] - cx) * s, size / 2 - (v[2] - cz) * s) for v in tri]))
    p.end()
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode()


def _image(b64: str) -> QImage | None:
    if not b64:
        return None
    img = QImage()
    img.loadFromData(QByteArray(base64.b64decode(b64)), "PNG")
    return None if img.isNull() else img


# ---- items --------------------------------------------------------------------------------------------
class ComponentItem(QGraphicsItem):
    def __init__(self, editor: "CircuitEditor", comp: CK.Component):
        super().__init__()
        self.editor, self.comp = editor, comp
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos(comp.x, comp.y)
        self.setRotation(comp.rot)
        self._img = _image(comp.props.get("Thumbnail", "")) if comp.type == "EM" else None
        self.setToolTip(self._tip())

    def _tip(self) -> str:
        c = self.comp
        info = CK.TYPES[c.type]
        vals = ", ".join(f"{k} = {v}" for k, v in c.props.items() if k not in ("Thumbnail", "PortNames", "Ports"))
        return f"{c.name}: {info.label}" + (f"\n{vals}" if vals else "")

    def _size(self) -> tuple[int, int]:
        c = self.comp
        if c.type in CK.NPORT_TYPES:
            return CK.BLOCK_W, CK.block_height(CK.nport_count(c))
        return {"GND": (30, 30), "PORT": (50, 30), "TLIN": (90, 30)}.get(c.type, (70, 30))

    def boundingRect(self) -> QRectF:
        w, h = self._size()
        if self.comp.type in CK.NPORT_TYPES:
            return QRectF(-w / 2 - 14, -h / 2 - 22, w + 28, h + 44)
        r = max(w / 2 + 30, h / 2 + 170)           # labels stay upright, beside a vertical part
        return QRectF(-r, -r, 2 * r, 2 * r)

    def shape(self) -> QPainterPath:
        w, h = self._size()
        path = QPainterPath()
        path.addRect(QRectF(-w / 2 - 4, -h / 2 - 4, w + 8, h + 8))
        return path

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(snap(value.x()), snap(value.y()))
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        self.editor.items_moved()

    def mouseDoubleClickEvent(self, e) -> None:
        self.editor.edit_component(self.comp.name)

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        c = self.comp
        sel = self.isSelected()
        pen = QPen(QColor(GOLD) if sel else INKC, 2 if sel else 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        t = c.type
        if t == "R":
            p.drawLine(-30, 0, -18, 0)
            p.drawPolyline(QPolygonF([QPointF(-18 + 4 * i, (0 if i in (0, 9) else (-6 if i % 2 else 6)))
                                      for i in range(10)]))
            p.drawLine(18, 0, 30, 0)
        elif t == "L":
            p.drawLine(-30, 0, -20, 0)
            path = QPainterPath(QPointF(-20, 0))
            for i in range(4):
                path.arcTo(QRectF(-20 + 10 * i, -6, 10, 12), 180, -180)
            p.drawPath(path)
            p.drawLine(20, 0, 30, 0)
        elif t == "C":
            p.drawLine(-30, 0, -4, 0)
            p.drawLine(-4, -10, -4, 10)
            p.drawLine(4, -10, 4, 10)
            p.drawLine(4, 0, 30, 0)
        elif t == "GND":
            p.drawLine(0, -10, 0, 0)
            p.drawLine(-10, 0, 10, 0)
            p.drawLine(-6, 4, 6, 4)
            p.drawLine(-2, 8, 2, 8)
        elif t == "PORT":
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(0, 0), 9, 9)
            path = QPainterPath(QPointF(-5, 0))
            path.cubicTo(-3, -6, -1, -6, 0, 0)
            path.cubicTo(1, 6, 3, 6, 5, 0)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.drawLine(9, 0, 20, 0)
        elif t == "TLIN":
            p.drawLine(-40, 0, -30, 0)
            p.drawLine(30, 0, 40, 0)
            p.setBrush(QColor("#e9f1fb"))
            p.drawRect(QRectF(-30, -8, 60, 16))
        elif t in CK.NPORT_TYPES:
            w, h = self._size()
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), 6, 6)
            for (dx, dy) in CK.nport_pins(CK.nport_count(c)):
                p.drawLine(QPointF(dx, dy), QPointF(dx - 10 if dx > 0 else dx + 10, dy))
            if self._img is not None:
                side = min(w - 20, h - 16, THUMB)
                p.drawImage(QRectF(-side / 2, -side / 2, side, side), self._img)
            else:
                p.setPen(QPen(QColor(MUTED)))
                p.drawText(QRectF(-w / 2, -h / 2, w, h), Qt.AlignCenter, "S" if t == "SNP" else "3D")
            names = [n for n in c.props.get("PortNames", "").split(",") if n]
            f = QFont(p.font())
            f.setPointSizeF(7)
            p.setFont(f)
            p.setPen(QPen(QColor(MUTED)))
            for k, (dx, dy) in enumerate(CK.nport_pins(CK.nport_count(c))):
                label = names[k] if k < len(names) else str(k + 1)
                r = QRectF(-w / 2 + 3, dy - 7, w / 2 - 6, 14) if dx < 0 else QRectF(3, dy - 7, w / 2 - 6, 14)
                p.drawText(r, (Qt.AlignLeft if dx < 0 else Qt.AlignRight) | Qt.AlignVCenter, label)
        # pins
        p.setPen(Qt.NoPen)
        p.setBrush(PIN)
        offs = CK.nport_pins(CK.nport_count(c)) if t in CK.NPORT_TYPES else CK.TYPES[t].pins
        for dx, dy in offs:
            p.drawEllipse(QPointF(dx, dy), 2.5, 2.5)
        # name and value, kept upright
        p.save()
        p.rotate(-self.rotation())
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        w, h = self._size()
        val = {"R": c.props.get("R"), "L": c.props.get("L"), "C": c.props.get("C"), "PORT": c.props.get("Z0"),
               "TLIN": f"{c.props.get('Z0')}, {c.props.get('E')}",
               "EM": c.props.get("Design"), "SNP": c.props.get("File", "").replace("\\", "/").split("/")[-1]}.get(t)
        if self.comp.rot % 180 and t not in CK.NPORT_TYPES and t != "GND":
            # vertical part: name and value beside it
            boxes = (QRectF(h / 2 + 4, -15, 160, 14), QRectF(h / 2 + 4, 1, 160, 14), Qt.AlignLeft | Qt.AlignVCenter)
        else:
            hh = w if self.comp.rot % 180 else h
            boxes = (QRectF(-80, -hh / 2 - 18, 160, 14), QRectF(-80, hh / 2 + 4, 160, 14), Qt.AlignCenter)
        p.setPen(QPen(INKC))
        if t != "GND":
            p.drawText(boxes[0], boxes[2], c.name)
        if val:
            p.setPen(QPen(QColor(MUTED)))
            p.drawText(boxes[1], boxes[2], val)
        p.restore()


class WireItem(QGraphicsPathItem):
    def __init__(self, index: int, wire: CK.Wire):
        super().__init__()
        self.index = index
        path = QPainterPath()
        pts = wire.points
        if pts:
            path.moveTo(*pts[0])
            for q in pts[1:]:
                path.lineTo(*q)
        self.setPath(path)
        self.setPen(QPen(WIRE, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.setFlags(QGraphicsItem.ItemIsSelectable)

    def paint(self, p, opt, w=None) -> None:
        self.setPen(QPen(QColor(GOLD) if self.isSelected() else WIRE, 2.4 if self.isSelected() else 1.8,
                         Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        super().paint(p, opt, w)

    def shape(self) -> QPainterPath:
        from PySide6.QtGui import QPainterPathStroker
        s = QPainterPathStroker()
        s.setWidth(8)
        return s.createStroke(self.path())


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

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME_DESIGN):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME_DESIGN):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME_DESIGN):
            name = bytes(e.mimeData().data(MIME_DESIGN)).decode()
            pos = self.mapToScene(e.position().toPoint())
            self.editor.add_em_block(name, snap(pos.x()), snap(pos.y()))
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
        if e.key() == Qt.Key_Escape:
            self.editor.cancel()
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.editor.delete_selected()
        elif e.key() == Qt.Key_R:
            self.editor.rotate_selected()
        elif e.key() == Qt.Key_W:
            self.editor.set_mode("wire")
        else:
            super().keyPressEvent(e)


class CircuitEditor(QWidget):
    """Schematic of the active circuit design; changes go through the document (undo, dirty flag)."""
    analyzeRequested = Signal()
    reportRequested = Signal()

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.mode = "select"
        self.place_type = ""
        self.wire_pts: list[list[int]] = []
        self._preview: QGraphicsPathItem | None = None
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(-2000, -2000, 4000, 4000)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar = QToolBar()
        self.actions_: dict[str, QAction] = {}
        a = self.bar.addAction("Select", lambda: self.set_mode("select"))
        a.setCheckable(True)
        self.actions_["select"] = a
        a = self.bar.addAction("Wire (W)", lambda: self.set_mode("wire"))
        a.setCheckable(True)
        self.actions_["wire"] = a
        self.bar.addSeparator()
        for t in ("R", "L", "C", "GND", "PORT", "TLIN", "SNP"):
            a = self.bar.addAction(CK.TYPES[t].label, lambda t=t: self.set_mode("place", t))
            a.setCheckable(True)
            a.setToolTip(CK.TYPES[t].help)
            self.actions_["place:" + t] = a
        self.bar.addSeparator()
        self.bar.addAction("Rotate (R)", self.rotate_selected)
        self.bar.addAction("Delete", self.delete_selected)
        self.bar.addSeparator()
        self.bar.addAction("Analyze", self.analyzeRequested.emit)
        self.bar.addAction("S-Parameter Report", self.reportRequested.emit)
        lay.addWidget(self.bar)
        self.hint = QLabel()
        self.hint.setStyleSheet(f"color: {MUTED}; padding: 3px 8px;")
        lay.addWidget(self.hint)
        self.view = SchematicView(self)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context)
        lay.addWidget(self.view, 1)
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
        sel = {it.comp.name for it in self.scene.selectedItems() if isinstance(it, ComponentItem)}
        self._preview = None
        self.scene.clear()
        for i, w in enumerate(self.des.wires):
            self.scene.addItem(WireItem(i, w))
        for c in self.des.components:
            it = ComponentItem(self, c)
            self.scene.addItem(it)
            it.setSelected(c.name in sel)
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

    # ---- modes --------------------------------------------------------------------------------
    def set_mode(self, mode: str, ctype: str = "") -> None:
        self.cancel(keep_mode=True)
        self.mode, self.place_type = mode, ctype
        for k, a in self.actions_.items():
            a.setChecked(k == (f"place:{ctype}" if mode == "place" else mode))
        self.view.setDragMode(QGraphicsView.RubberBandDrag if mode == "select" else QGraphicsView.NoDrag)
        self.hint.setText({
            "select": "Drag a 3D design from the Project Manager into the schematic to use its S-parameters. "
                      "Double-click a component to edit it, R rotates, Del deletes.",
            "wire": "Wire: click the corners, double-click or right-click to end, Esc cancels.",
            "place": f"{CK.TYPES[ctype].label if ctype else ''}: click to place, R rotates, Esc ends."}[mode])

    def cancel(self, keep_mode: bool = False) -> None:
        self.wire_pts = []
        if self._preview is not None:
            self.scene.removeItem(self._preview)
            self._preview = None
        if not keep_mode and self.mode != "select":
            self.set_mode("select")

    def handle_press(self, pos: QPointF, button) -> bool:
        x, y = snap(pos.x()), snap(pos.y())
        if self.mode == "place":
            if button == Qt.RightButton:
                self.set_mode("select")
                return True
            self.place(self.place_type, x, y)
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
            self._update_preview(x, y)
            return True
        return False

    def handle_move(self, pos: QPointF) -> None:
        if self.mode == "wire" and self.wire_pts:
            self._update_preview(snap(pos.x()), snap(pos.y()))

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
    def place(self, ctype: str, x: int, y: int, props: dict | None = None) -> CK.Component:
        c = CK.Component(CK.next_name(self.des.components, ctype), ctype, x, y, 0,
                         {**CK.TYPES[ctype].props, **(props or {})})
        if ctype == "SNP" and not props:
            path, _ = QFileDialog.getOpenFileName(self, "Touchstone File", "", "Touchstone (*.s*p)")
            if not path:
                return c
            c.props["File"] = path
            try:
                from ..model.touchstone import read
                c.props["Ports"] = str(read(path)[1].shape[1])
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Touchstone", str(e))
                return c
        self._change()
        self.des.components.append(c)
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

    def items_moved(self) -> None:
        moved = [it for it in self.scene.selectedItems() if isinstance(it, ComponentItem)
                 and (int(it.pos().x()), int(it.pos().y())) != (it.comp.x, it.comp.y)]
        if not moved:
            return
        self._change()
        for it in moved:
            it.comp.x, it.comp.y = snap(it.pos().x()), snap(it.pos().y())
        self._commit()

    def delete_selected(self) -> None:
        comps = {it.comp.name for it in self.scene.selectedItems() if isinstance(it, ComponentItem)}
        wires = {it.index for it in self.scene.selectedItems() if isinstance(it, WireItem)}
        if not comps and not wires:
            return
        self._change()
        self.des.components = [c for c in self.des.components if c.name not in comps]
        self.des.wires = [w for i, w in enumerate(self.des.wires) if i not in wires]
        self._commit()

    def rotate_selected(self) -> None:
        if self.mode == "place":
            return
        sel = [it for it in self.scene.selectedItems() if isinstance(it, ComponentItem)]
        if not sel:
            return
        self._change()
        for it in sel:
            it.comp.rot = (it.comp.rot + 90) % 360
        self._commit()

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
            m.addAction("Rotate", self.rotate_selected)
            m.addAction("Delete", self.delete_selected)
        elif isinstance(it, WireItem):
            it.setSelected(True)
            m.addAction("Delete", self.delete_selected)
        else:
            sp = self.view.mapToScene(pos)
            for t in ("R", "L", "C", "GND", "PORT", "TLIN"):
                m.addAction(f"Add {CK.TYPES[t].label}", lambda t=t: self.place(t, snap(sp.x()), snap(sp.y())))
            m.addSeparator()
            m.addAction("Analyze", self.analyzeRequested.emit)
        m.exec(self.view.viewport().mapToGlobal(pos))


class ComponentDialog(QDialog):
    def __init__(self, doc, comp: CK.Component, parent=None):
        super().__init__(parent)
        self.doc, self.comp = doc, comp
        self.setWindowTitle(f"{CK.TYPES[comp.type].label}: {comp.name}")
        lay = QVBoxLayout(self)
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
            for k in CK.TYPES[comp.type].props:
                e = QLineEdit(comp.props.get(k, CK.TYPES[comp.type].props[k]))
                self.fields[k] = e
                form.addRow({"Z0": "Impedance Z0", "E": "Electrical length", "F": "at frequency"}.get(k, k), e)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Touchstone File", self.file.text(), "Touchstone (*.s*p)")
        if path:
            self.file.setText(path)

    def _ok(self) -> None:
        ev = self.doc.design.evaluator(self.doc.project)
        for k, e in self.fields.items():
            msg = ev.check(e.text())
            if msg:
                QMessageBox.warning(self, "Component", f"{k}: {msg}")
                return
        self.accept()

    def values(self) -> tuple[str, dict]:
        name = self.name.text().strip() or self.comp.name
        if self.comp.type == "EM":
            return name, {"Design": self.design.currentText(), "Solution": self.solution.currentText()}
        if self.comp.type == "SNP":
            return name, {"File": self.file.text().strip()}
        return name, {k: e.text().strip() for k, e in self.fields.items()}
