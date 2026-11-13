"""Board view of a circuit design: the parts on the PCB (2D layout), seen from the top.

Every part with a physical form is shown with its copper (pads, lines, junctions) to scale in mm. Drag a part to
move it (it snaps to the grid, and a pin snaps onto a pin of the same net nearby), R turns the selected parts by
90 degrees. Thin lines (ratsnest) show connections of the schematic the board does not make yet; copper of two
nets on each other is outlined in red. The Track tool draws copper: click the corners, double-click or
right-click to end; Del deletes the selected parts' placement back to automatic or a selected track.

3D design blocks (Wilkinson, rat-race, filters of the Design Wizard, any 3D design with ports on its edge) are
parts too, with their copper seen from the top. Connect lays lines of one impedance (Z0) for the connections
still missing (only those of the selected parts, if any are selected), round the other copper with circular
(Arc) or mitered bends and a taper where a pin is wider or narrower; they are part of the circuit simulation.
Match Phase gives the selected routes the same insertion phase at f0 with meanders. Double-click a bend of a
route to set its radius, or a miter for a 90 degree bend. A route whose parts were
moved is outlined in red: Connect lays it again after deleting it.
Create 3D Layout (here or in the schematic) builds the 3D design from this board."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..model import board as BD
from ..model import routing as RT
from .theme import MUTED

S = 100.0                         # scene units per mm (y down on the screen: scene y = -S * board y)
GRID_MM = 0.05
SNAP_MM = 0.3                     # a pin within this distance of a pin of its net snaps onto it
COPPER = QColor("#c9952f")
COPPER_SEL = QColor("#e8b54a")
RLC = QColor("#c4473a")
RATS = QColor("#2f6db5")
BAD = QColor("#d0021b")
BOARD_BG = QColor("#1f3b28")


def to_scene(x: float, y: float) -> QPointF:
    return QPointF(x * S, -y * S)


def to_board(p: QPointF) -> tuple[float, float]:
    return p.x() / S, -p.y() / S


def snap_mm(v: float) -> float:
    return round(v / GRID_MM) * GRID_MM


class PartItem(QGraphicsItem):
    """A part on the board, drawn in its own coordinates; the item's position and rotation are the placement."""

    def __init__(self, editor: BoardEditor, bp: BD.BoardPart):
        super().__init__()
        self.editor, self.bp = editor, bp
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.bad = False
        x0, y0, x1, y1 = bp.outline()
        m = 0.3
        self._rect = QRectF(x0 * S - m * S, -y1 * S - m * S, (x1 - x0 + 2 * m) * S, (y1 - y0 + 2 * m) * S)
        if bp.type in ("PORT", "VDC", "IDC"):
            self._rect = self._rect.united(QRectF(-0.4 * S, -0.4 * S, 0.8 * S, 0.8 * S))
        cx, cy = ((0.0, -0.55) if bp.type in ("PORT", "VDC", "IDC") else ((x0 + x1) / 2, (y0 + y1) / 2))
        tw = 2.0 if bp.type != "EM" else max(2.0, 8 * min(2.0, 0.08 * max(x1 - x0, y1 - y0)))
        self._paint_rect = self._rect.united(QRectF((cx - tw) * S, (-cy - tw) * S, 2 * tw * S, 2 * tw * S))  # the name
        self.setToolTip(f"{bp.name} ({bp.type}{', ' + bp.footprint if bp.footprint else ''})")

    def boundingRect(self) -> QRectF:
        return self._paint_rect

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self._rect)
        return path

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        sel = self.isSelected()
        for cu in self.bp.copper:
            p.setPen(QPen(BAD if self.bad else QColor("#3a2a10"), 0))
            p.setBrush(QBrush(COPPER_SEL if sel else COPPER))
            if cu.holes:
                path = QPainterPath()
                path.setFillRule(Qt.OddEvenFill)
                for ring in [cu.pts] + list(cu.holes):
                    path.addPolygon(QPolygonF([QPointF(x * S, -y * S) for x, y in ring]))
                    path.closeSubpath()
                p.drawPath(path)
            else:
                p.drawPolygon(QPolygonF([QPointF(x * S, -y * S) for x, y in cu.pts]))
        for ring in self.bp.sheets:
            p.setPen(QPen(QColor("#5a1010"), 0))
            p.setBrush(QBrush(RLC))
            p.drawPolygon(QPolygonF([QPointF(x * S, -y * S) for x, y in ring]))
        if self.bp.lumped:
            a, b, w = self.bp.lumped.a, self.bp.lumped.b, self.bp.lumped.w
            if abs(a[1] - b[1]) < 1e-9:
                r = QRectF(min(a[0], b[0]) * S, -(a[1] + w / 2) * S, abs(b[0] - a[0]) * S, w * S)
            else:
                r = QRectF((a[0] - w / 2) * S, -max(a[1], b[1]) * S, w * S, abs(b[1] - a[1]) * S)
            p.setPen(QPen(QColor("#5a1010"), 0))
            p.setBrush(QBrush(RLC))
            p.drawRect(r)
        for vx, vy, dv, _ in self.bp.vias:
            p.setPen(QPen(QColor("#202020"), 0))
            p.setBrush(QBrush(QColor("#606060")))
            p.drawEllipse(QPointF(vx * S, -vy * S), dv / 2 * S, dv / 2 * S)
        if self.bp.body:
            p.setPen(QPen(QColor("#d8d8d8"), 0, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygonF([QPointF(x * S, -y * S) for x, y in self.bp.body]))
        if self.bp.type in ("PORT", "VDC", "IDC"):
            p.setPen(QPen(QColor("#7fb2ff"), 0))
            p.setBrush(QBrush(QColor("#2f6db5")))
            p.drawEllipse(QPointF(0, 0), 0.25 * S, 0.25 * S)
        if sel:
            p.setPen(QPen(QColor("white"), 0, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._rect)
        # the name, upright whatever the rotation
        x0, y0, x1, y1 = self.bp.outline()
        if self.bp.type in ("PORT", "VDC", "IDC"):
            cx, cy = 0.0, -0.55              # under the port marker
        else:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        p.save()
        p.translate(cx * S, -cy * S)
        p.rotate(-self.rotation())
        f = QFont(self.editor.font())
        size = 0.25 if self.bp.type != "EM" else max(0.25, min(2.0, 0.08 * max(x1 - x0, y1 - y0)))
        f.setPixelSize(max(6, int(size * S)))
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(-8 * size * S, -0.8 * size * S, 16 * size * S, 1.6 * size * S), Qt.AlignCenter,
                   self.bp.name)
        p.restore()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and not self.editor.loading:
            x, y = to_board(value)
            x, y = self.editor.snap_part(self, snap_mm(x), snap_mm(y))
            return to_scene(x, y)
        if change == QGraphicsItem.ItemPositionHasChanged and not self.editor.loading:
            self.editor.moved()
        return super().itemChange(change, value)

    def placement(self) -> BD.Placement:
        x, y = to_board(self.pos())
        return BD.Placement(round(x, 6), round(y, 6), (-self.rotation()) % 360)


class TrackItem(QGraphicsPathItem):
    def __init__(self, index: int, tr: BD.Track):
        super().__init__()
        self.index = index
        path = QPainterPath(to_scene(*tr.pts[0]))
        for q in tr.pts[1:]:
            path.lineTo(to_scene(*q))
        self.setPath(path)
        pen = QPen(COPPER, tr.w * S, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
        self.setPen(pen)
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setToolTip(f"Track {index + 1}, {tr.w:g} mm (not in the schematic)")


class RouteItem(QGraphicsPathItem):
    """A line laid by Connect (routing.py)."""

    def __init__(self, index: int, r: BD.Route, ok: bool, tip: str):
        super().__init__()
        self.index = index
        geo = RT.polygon(r)
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        for g in getattr(geo, "geoms", [geo]):
            if g.is_empty:
                continue
            for ring in [g.exterior] + list(g.interiors):
                path.addPolygon(QPolygonF([to_scene(x, y) for x, y in list(ring.coords)[:-1]]))
                path.closeSubpath()
        self.setPath(path)
        self.ok = ok
        self.setBrush(QBrush(COPPER))
        self.setPen(QPen(QColor("#3a2a10") if ok else BAD, 0 if ok else 2, Qt.SolidLine if ok else Qt.DashLine))
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setZValue(1)
        self.setToolTip(tip)

    def paint(self, p: QPainter, opt, w=None) -> None:
        self.setBrush(QBrush(COPPER_SEL if self.isSelected() else COPPER))
        super().paint(p, opt, w)


class BoardView(QGraphicsView):
    def __init__(self, editor: BoardEditor):
        super().__init__(editor.scene)
        self.editor = editor
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setBackgroundBrush(BOARD_BG)

    def wheelEvent(self, e) -> None:
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def mousePressEvent(self, e) -> None:
        if self.editor.track_press(self.mapToScene(e.position().toPoint()), e.button()):
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        self.editor.track_move(self.mapToScene(e.position().toPoint()))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        self.editor.release()

    def mouseDoubleClickEvent(self, e) -> None:
        if self.editor.track_finish():
            return
        if self.editor.edit_bend(self.mapToScene(e.position().toPoint())):
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_R:
            self.editor.rotate_selected()
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.editor.delete_selected()
        elif e.key() == Qt.Key_Escape:
            self.editor.track_cancel()
        else:
            super().keyPressEvent(e)


class BoardEditor(QWidget):
    """The board of the active circuit design; changes go through the document (undo, dirty flag)."""
    layoutRequested = Signal()        # Create 3D Layout from the board
    schematicRequested = Signal()     # back to the schematic

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.loading = False
        self.scene = QGraphicsScene(self)
        self.items: dict[str, PartItem] = {}
        self.parts: dict[str, BD.BoardPart] = {}
        self.nodes: dict[str, list[int]] = {}
        self.board = BD.Board()
        self._rats: list = []
        self._track_pts: list[tuple[float, float]] = []
        self._track_preview: QGraphicsPathItem | None = None
        self.track_mode = False
        self._dirty = False
        self._storing = False
        self.notes: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QToolBar()                 # folds into its extension menu when the area is narrow
        bar.setContentsMargins(6, 2, 6, 2)

        def btn(text, tip, fn):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
            return b
        btn("Schematic", "Back to the schematic", self.schematicRequested.emit)
        btn("Auto Place", "Place all parts again as the schematic is drawn (the placement so far is replaced)",
            self.auto_place)
        btn("Rotate (R)", "Turn the selected parts by 90 degrees", self.rotate_selected)
        self.track_btn = btn("Track", "Draw a track: click the corners, double-click or right-click to end, Esc "
                             "cancels. Tracks are copper the schematic does not have (DC routing)", self.toggle_track)
        self.track_btn.setCheckable(True)
        bar.addWidget(QLabel("width"))
        self.track_w = QDoubleSpinBox()
        self.track_w.setRange(0.05, 5.0)
        self.track_w.setSingleStep(0.05)
        self.track_w.setValue(0.3)
        self.track_w.setSuffix(" mm")
        bar.addWidget(self.track_w)
        btn("Connect", "Lay lines of the impedance Z0 for the connections still missing (only those of the selected "
            "parts, if any): round the other copper, with Arc or Miter bends and a taper where a pin is wider or "
            "narrower. The lines are part of the circuit simulation", self.connect)
        bar.addWidget(QLabel("Z0"))
        self.z0 = QDoubleSpinBox()
        self.z0.setRange(5.0, 200.0)
        self.z0.setDecimals(1)
        self.z0.setValue(50.0)
        self.z0.setSuffix(" ohm")
        bar.addWidget(self.z0)
        self.bend_style = QComboBox()
        self.bend_style.addItems(list(RT.STYLES))
        self.bend_style.setToolTip("Bends of the lines Connect lays: circular (radius 3 line widths) or 50 % mitered")
        bar.addWidget(self.bend_style)
        btn("Match Phase", "Give the selected routes the same insertion phase at f0: the shorter ones get a meander",
            self.match_phase)
        bar.addWidget(QLabel("f0"))
        self.f0 = QDoubleSpinBox()
        self.f0.setRange(0.001, 1000.0)
        self.f0.setDecimals(3)
        self.f0.setValue(1.0)
        self.f0.setSuffix(" GHz")
        bar.addWidget(self.f0)
        btn("Fit", "Show the whole board", self.fit_all)
        btn("Create 3D Layout", "Build the 3D design (FEM) from this board: copper, lumped parts on their pads, a "
            "port at every pad of a transistor or IC", self.layoutRequested.emit)
        lay.addWidget(bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {MUTED}; padding: 2px 8px;")
        lay.addWidget(self.status)
        self.view = BoardView(self)
        lay.addWidget(self.view, 1)

    @property
    def des(self):
        return self.doc.design

    # ---- loading and storing -------------------------------------------------------------------------------
    def rebuild(self) -> None:
        if getattr(self.des, "kind", "3d") != "circuit" or self._storing:
            return
        self.loading = True
        self.scene.clear()
        self.items.clear()
        self._rats = []
        self._track_preview = None
        self.parts, self.nodes, notes = BD.board_parts(self.doc.project, self.des)
        saved = BD.load(self.des)
        if saved is None:
            self.board, more = BD.auto_place(self.doc.project, self.des)
        else:
            self.board, more = BD.complete(self.doc.project, self.des, saved)
        self.notes = notes + more
        for name, bp in self.parts.items():
            it = PartItem(self, bp)
            pl = self.board.placements.get(name, BD.Placement())
            it.setPos(to_scene(pl.x, pl.y))
            it.setRotation(-pl.rot)
            self.scene.addItem(it)
            self.items[name] = it
        for i, tr in enumerate(self.board.tracks):
            self.scene.addItem(TrackItem(i, tr))
        self._route_tips()
        o = self.board.opts
        if "z0" in o:
            self.z0.setValue(float(o["z0"]))
        if o.get("style") in RT.STYLES:
            self.bend_style.setCurrentText(o["style"])
        try:
            f0 = float(o["f0"]) if "f0" in o else RT.analysis_frequency(self.doc.project, self.des)
            self.f0.setValue(f0 / 1e9)
        except (TypeError, ValueError):
            pass
        for i, r in enumerate(self.board.routes):
            self.scene.addItem(RouteItem(i, r, BD.route_ok(self.parts, self.board, r), self._tips.get(i, "")))
        self.loading = False
        self.refresh()

    def _route_tips(self) -> None:
        self._tips = {}
        try:
            _, sub = RT.substrate(self.doc.project, self.des)
        except RT.RouteError:
            sub = None
        f0 = float(self.board.opts.get("f0", 0) or 0) or None
        for i, r in enumerate(self.board.routes):
            t = (f"Route {i + 1}: {r.a[0]} pin {r.a[1] + 1} to {r.b[0]} pin {r.b[1] + 1}, {r.z0:g} ohm, "
                 f"W {r.w:.4g} mm, length {RT.length(r):.4g} mm")
            if sub is not None and f0:
                try:
                    t += f", phase {RT.phase(r, sub, f0):.2f} deg at {f0 / 1e9:g} GHz"
                except Exception:  # noqa: BLE001
                    pass
            if r.group:
                t += f", phase group {r.group}" + (f" ({r.offset:+g} deg)" if r.offset else "")
            if not BD.route_ok(self.parts, self.board, r):
                t += ". A part moved: delete it and Connect again"
            self._tips[i] = t

    def _collect(self) -> BD.Board:
        b = BD.Board({n: it.placement() for n, it in self.items.items()}, list(self.board.tracks),
                     list(self.board.routes), dict(self.board.opts))
        return b

    def store(self) -> None:
        """The placement as it is now into the circuit design (one undo step)."""
        self.doc.checkpoint()
        self.board = self._collect()
        BD.store(self.des, self.board)
        self._storing = True            # the scene already shows it: no rebuild from the change signal
        try:
            self.doc.commit("circuit", geometry=False)
        finally:
            self._storing = False

    # ---- moving --------------------------------------------------------------------------------------------
    def snap_part(self, item: PartItem, x: float, y: float) -> tuple[float, float]:
        """A pin of the moved part within SNAP_MM of a pin of the same net on another part snaps onto it."""
        bp = item.bp
        rot = (-item.rotation()) % 360
        ns = self.nodes.get(bp.name, [])
        best = None
        others = BD.anchors(self.parts, self._collect_except(bp.name), self.nodes)
        for k, pin in list(enumerate(bp.pins)) + list(bp.extra):
            if k >= len(ns) or ns[k] == 0:
                continue
            px, py = BD.rot_pt((pin.x, pin.y), rot)
            for name, ox, oy in others.get(ns[k], []):
                dx, dy = ox - (x + px), oy - (y + py)
                dd = math.hypot(dx, dy)
                if dd < SNAP_MM and (best is None or dd < best[0]):
                    best = (dd, dx, dy)
        if best:
            return x + best[1], y + best[2]
        return x, y

    def _collect_except(self, name: str) -> BD.Board:
        return BD.Board({n: it.placement() for n, it in self.items.items() if n != name}, list(self.board.tracks),
                        list(self.board.routes), dict(self.board.opts))

    def moved(self) -> None:
        self._dirty = True
        self.refresh()

    def release(self) -> None:
        if self._dirty:
            self._dirty = False
            self.store()

    def rotate_selected(self) -> None:
        sel = [it for it in self.scene.selectedItems() if isinstance(it, PartItem)]
        if not sel:
            return
        for it in sel:
            it.setRotation(it.rotation() - 90)
        self.store()
        self.refresh()

    def delete_selected(self) -> None:
        tracks = sorted((it.index for it in self.scene.selectedItems() if isinstance(it, TrackItem)), reverse=True)
        routes = sorted((it.index for it in self.scene.selectedItems() if isinstance(it, RouteItem)), reverse=True)
        if not tracks and not routes:
            return
        for i in tracks:
            del self.board.tracks[i]
        for i in routes:
            del self.board.routes[i]
        self.store()
        self.rebuild()

    # ---- automatic connections -----------------------------------------------------------------------------
    def connect(self) -> None:
        only = {it.bp.name for it in self.scene.selectedItems() if isinstance(it, PartItem)} or None
        try:
            b, warnings = RT.connect(self.doc.project, self.des, self._collect(), self.z0.value(),
                                     self.bend_style.currentText(), only, f=self.f0.value() * 1e9)
        except RT.RouteError as e:
            self.status.setText(f"Connect: {e}")
            return
        self._apply(b, warnings, "Connect")

    def match_phase(self) -> None:
        idx = sorted(it.index for it in self.scene.selectedItems() if isinstance(it, RouteItem))
        if len(idx) < 2:
            self.status.setText("Match Phase: select two or more routes (click, Ctrl+click)")
            return
        try:
            b, warnings, ph = RT.match_phase(self.doc.project, self.des, self._collect(), idx, self.f0.value() * 1e9)
        except RT.RouteError as e:
            self.status.setText(f"Match Phase: {e}")
            return
        warnings = [", ".join(f"Route {i + 1}: {v:.2f} deg" for i, v in sorted(ph.items()))] + warnings
        self._apply(b, warnings, "Match Phase")

    def edit_bend(self, p: QPointF) -> bool:
        """Double-click on a bend of a route: its radius, or a miter for a 90 degree bend."""
        x, y = to_board(p)
        hit = None
        for it in self.scene.items(p):
            if isinstance(it, RouteItem):
                r = self.board.routes[it.index]
                i = RT.corner_at(r, (x, y))
                if i is not None:
                    hit = (it.index, i)
                    break
        if hit is None:
            for k, r in enumerate(self.board.routes):     # near a bend, not on its copper (inside an arc)
                i = RT.corner_at(r, (x, y))
                if i is not None:
                    hit = (k, i)
                    break
        if hit is None:
            return False
        k, i = hit
        r = self.board.routes[k]
        ang = RT.corner_angle(r, i)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Bend {i + 1} of Route {k + 1}")
        form = QFormLayout(dlg)
        form.addRow(QLabel(f"{ang:.0f} degree bend, line width {r.w:.4g} mm"))
        rad = QDoubleSpinBox()
        rad.setRange(0.0, 100.0)
        rad.setDecimals(3)
        rad.setSuffix(" mm")
        rad.setValue(r.radii[i] if i < len(r.radii) and r.radii[i] > 0 else RT.RADIUS_W * r.w)
        rad.setToolTip("Radius of the centre line; made smaller where the straight pieces leave less room")
        form.addRow("Radius", rad)
        miter = QCheckBox("Mitered (50 %) instead of a circular bend")
        miter.setEnabled(abs(ang - 90) < 1e-3)
        miter.setChecked(i < len(r.radii) and r.radii[i] <= 0)
        miter.toggled.connect(lambda on: rad.setEnabled(not on))
        rad.setEnabled(not miter.isChecked())
        form.addRow(miter)
        every = QCheckBox("All bends of this route")
        form.addRow(every)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return True
        b = self._collect()
        r = b.routes[k]
        corners = list(range(len(r.pts) - 2)) if every.isChecked() else [i]
        warnings = RT.set_bend(r, corners, 0.0 if miter.isChecked() else rad.value())
        self._apply(b, warnings, "Bend")
        return True

    def _apply(self, b: BD.Board, warnings: list[str], what: str) -> None:
        self.doc.checkpoint()
        BD.store(self.des, b)
        self._storing = True
        try:
            self.doc.commit("circuit", geometry=False)
        finally:
            self._storing = False
        self.rebuild()
        if warnings:
            self.status.setText(f"{what}: " + " · ".join(warnings[:4]))

    def auto_place(self) -> None:
        self.doc.checkpoint()
        b, _ = BD.auto_place(self.doc.project, self.des)
        b.tracks = []
        BD.store(self.des, b)
        self.doc.commit("circuit", geometry=False)      # rebuilds through the change signal
        self.rebuild()
        self.fit_all()

    # ---- ratsnest, overlaps, status ------------------------------------------------------------------------
    def refresh(self) -> None:
        for r in self._rats:
            self.scene.removeItem(r)
        self._rats = []
        b = self._collect()
        rats = BD.ratsnest(self.parts, b, self.nodes)
        for _net, a, c in rats:
            path = QPainterPath(to_scene(*a))
            path.lineTo(to_scene(*c))
            it = QGraphicsPathItem(path)
            it.setPen(QPen(RATS, 0, Qt.DashLine))
            it.setZValue(10)
            self.scene.addItem(it)
            self._rats.append(it)
        bad = {n for pair in BD.overlaps(self.parts, b, self.nodes) for n in pair}
        for n, it in self.items.items():
            if it.bad != (n in bad):
                it.bad = n in bad
                it.update()
        msg = [f"{len(rats)} connection(s) missing (dashed)" if rats else "All connections made"]
        if bad:
            msg.append(f"copper of different nets on each other: {', '.join(sorted(bad))}")
        msg += self.notes[:3]
        self.status.setText(" · ".join(msg))

    def fit_all(self) -> None:
        r = self.scene.itemsBoundingRect()
        if r.isValid():
            self.view.fitInView(r.adjusted(-S, -S, S, S), Qt.KeepAspectRatio)

    # ---- tracks --------------------------------------------------------------------------------------------
    def toggle_track(self) -> None:
        self.track_mode = self.track_btn.isChecked()
        self.view.setDragMode(QGraphicsView.NoDrag if self.track_mode else QGraphicsView.RubberBandDrag)
        if not self.track_mode:
            self.track_cancel()

    def _snap_track(self, p: QPointF) -> tuple[float, float]:
        x, y = to_board(p)
        x, y = snap_mm(x), snap_mm(y)
        # onto a pin nearby
        best = None
        for pts in BD.anchors(self.parts, self._collect(), self.nodes).values():
            for _n, ox, oy in pts:
                dd = math.hypot(ox - x, oy - y)
                if dd < SNAP_MM and (best is None or dd < best[0]):
                    best = (dd, ox, oy)
        if best:
            x, y = best[1], best[2]
        if self._track_pts:            # horizontal or vertical from the last corner
            lx, ly = self._track_pts[-1]
            if abs(x - lx) >= abs(y - ly):
                y = ly
            else:
                x = lx
        return x, y

    def track_press(self, p: QPointF, button) -> bool:
        if not self.track_mode:
            return False
        if button == Qt.RightButton:
            self.track_finish()
            return True
        self._track_pts.append(self._snap_track(p))
        return True

    def track_move(self, p: QPointF) -> None:
        if not self.track_mode or not self._track_pts:
            return
        pts = self._track_pts + [self._snap_track(p)]
        path = QPainterPath(to_scene(*pts[0]))
        for q in pts[1:]:
            path.lineTo(to_scene(*q))
        if self._track_preview is None:
            self._track_preview = QGraphicsPathItem()
            self._track_preview.setZValue(20)
            self.scene.addItem(self._track_preview)
        self._track_preview.setPen(QPen(COPPER_SEL, self.track_w.value() * S, Qt.SolidLine, Qt.SquareCap,
                                        Qt.MiterJoin))
        self._track_preview.setPath(path)

    def track_finish(self) -> bool:
        if not self.track_mode:
            return False
        pts = [q for i, q in enumerate(self._track_pts) if i == 0 or q != self._track_pts[i - 1]]
        self.track_cancel()
        if len(pts) >= 2:
            self.board = self._collect()
            self.board.tracks.append(BD.Track(pts, self.track_w.value()))
            self.store()
            self.rebuild()
        return True

    def track_cancel(self) -> None:
        self._track_pts = []
        if self._track_preview is not None:
            self.scene.removeItem(self._track_preview)
            self._track_preview = None
