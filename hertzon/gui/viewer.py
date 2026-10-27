"""3D modeler window: VTK view with picking, highlighting, drawing and overlays."""
from __future__ import annotations

import math
import types
from dataclasses import dataclass, field

import numpy as np
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

from .doc import Document, Pick
from ..model.schema import BOUNDARY_COLORS, EXCITATION_COLORS
from ..model.materials import normalize_color
from ..model.arcs import angular_arc


def _alt_key() -> bool:
    """Alt held now. VTK's Qt interactor only passes Ctrl and Shift on, so its GetAltKey() is always false."""
    from PySide6.QtWidgets import QApplication
    return bool(QApplication.queryKeyboardModifiers() & Qt.AltModifier)

HIGHLIGHT = "#e8b04a"          # selected objects, faces, edges
HIGHLIGHT_METAL = "#ffcf66"    # selected conductors: brighter than copper
SEL_EDGE = "#b06f00"           # dark gold outline of the selection: easy to see what was hit
PRESELECT = "#ffd27f"
PLANE_NORMAL = {"XY": 2, "YZ": 0, "XZ": 1, "ZX": 1}     # names XZ; ZX kept for old settings


def _rgb(color: str):
    color = normalize_color(color).lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


@dataclass
class ObjActors:
    surface: object = None
    edges: object = None
    vertices: object = None
    mesh: pv.PolyData | None = None
    edge_mesh: pv.PolyData | None = None
    tess_id: int = 0
    dim: int = 3


@dataclass
class DrawState:
    kind: str
    phases: int
    points: list = field(default_factory=list)
    preview: object = None
    current: np.ndarray | None = None
    seg_mode: str = "Line"                         # polyline segment type for the next points (edge type)
    modes: list = field(default_factory=list)     # segment type of every point after the first
    sweep: float = 0.0                             # center point arc: angle swept so far (rad, follows the cursor)
    sweep_ref: float = 0.0                         # its cursor angle at the last update
    ghost: list = field(default_factory=list)     # objects shown moved/mirrored while an operation is drawn


DRAW_PHASES = {"CreateBox": 3, "CreateCylinder": 3, "CreateCone": 4, "CreateSphere": 2, "CreateTorus": 3,
               "CreateRegularPolyhedron": 3, "CreateRectangle": 2, "CreateCircle": 2, "CreateEllipse": 3,
               "CreateRegularPolygon": 2, "CreatePolyline": 999, "Measure": 2,
               "MeasurePosition": 1, "MeasureAngle": 3,
               "OpMove": 2, "OpDupLine": 2, "OpMirror": 2, "OpDupMirror": 2,
               "CSOffset": 1, "CSRotated": 2, "CSBoth": 3, "IntegrationLine": 2,
               "PlaceComponent": 1}
# interactive operations on selected objects (Move / Duplicate Along Line / Mirror by clicking points)
OP_KINDS = {"OpMove", "OpDupLine", "OpMirror", "OpDupMirror"}
# drawn in global coordinates with free 3D points (not held to the plane of the first point)
FREE_KINDS = OP_KINDS | {"IntegrationLine", "PlaceComponent"}
# phases that move along the plane normal instead of in the plane
HEIGHT_PHASE = {"CreateBox": {2}, "CreateCylinder": {2}, "CreateCone": {3}, "CreateRegularPolyhedron": {2}}


SNAP_MODES = ("grid", "vertex", "edge_center", "face_center", "quadrant", "arc_center")
SNAP_LABELS = {"grid": "Grid", "vertex": "Vertex", "edge_center": "Edge Center", "face_center": "Face Center",
               "quadrant": "Quadrant", "arc_center": "Arc Center"}
# priority bonus in pixels: a vertex wins over an edge center at nearly the same spot, and so on
SNAP_PRIORITY = {"vertex": 0.0, "edge_center": 2.0, "quadrant": 2.0, "arc_center": 3.0, "face_center": 4.0}


def _seg_dist(q: np.ndarray, P: np.ndarray) -> float:
    """Distance of point q to the polyline P."""
    A, B = P[:-1], P[1:]
    d = B - A
    L2 = np.einsum("ij,ij->i", d, d)
    t = np.clip(np.einsum("ij,ij->i", q - A, d) / np.where(L2 > 0, L2, 1), 0, 1)
    return float(np.min(np.linalg.norm(A + d * t[:, None] - q, axis=1)))


def _circle(E: np.ndarray):
    """Centre, radius and normal when the polyline E lies on a circle, else None."""
    if len(E) < 4:
        return None
    closed = np.linalg.norm(E[0] - E[-1]) < 1e-9 * (1 + np.abs(E).max())
    i = len(E) // 3 if closed else len(E) // 2
    j = 2 * len(E) // 3 if closed else len(E) - 1
    a, b, c = E[0], E[i], E[j]
    ab, ac = b - a, c - a
    nrm = np.cross(ab, ac)
    nn = float(nrm @ nrm)
    if nn <= 1e-24 * max(float(ab @ ab), 1e-300) ** 2:
        return None
    ctr = a + (np.cross(nrm, ab) * (ac @ ac) + np.cross(ac, nrm) * (ab @ ab)) / (2 * nn)
    r = np.linalg.norm(E - ctr, axis=1)
    R = float(r.mean())
    if R <= 0 or np.ptp(r) > 1e-3 * R:
        return None
    return ctr, R, nrm / math.sqrt(nn)


def snap_candidates(t) -> dict[str, np.ndarray]:
    """Snap points of one tessellated object per snap mode (cached by the caller)."""
    out = {"vertex": np.asarray(t.vertices, float).reshape(-1, 3)}
    mids, centers, quads = [], [], []
    for E in t.edges:
        E = np.asarray(E, float)
        if len(E) < 2:
            continue
        seg = np.linalg.norm(np.diff(E, axis=0), axis=1)
        L = float(seg.sum())
        if L <= 0:
            continue
        circ = _circle(E)
        closed = np.linalg.norm(E[0] - E[-1]) <= 1e-9 * (1 + np.abs(E).max())
        if not closed:
            cum = np.concatenate([[0.0], np.cumsum(seg)])
            k = min(int(np.searchsorted(cum, L / 2)), len(E) - 1)
            k = max(k, 1)
            f = (L / 2 - cum[k - 1]) / max(seg[k - 1], 1e-300)
            mids.append(E[k - 1] + f * (E[k] - E[k - 1]))
        if circ is not None:
            ctr, R, nrm = circ
            centers.append(ctr)
            for e in np.eye(3):
                u = e - (e @ nrm) * nrm
                if np.linalg.norm(u) < 1e-6:
                    continue
                u = u / np.linalg.norm(u)
                for q in (ctr + R * u, ctr - R * u):
                    if _seg_dist(q, E) < 0.02 * R:
                        quads.append(q)
    out["edge_center"] = np.array(mids).reshape(-1, 3)
    out["arc_center"] = np.array(centers).reshape(-1, 3)
    out["quadrant"] = np.unique(np.round(np.array(quads).reshape(-1, 3), 12), axis=0)
    out["face_center"] = np.asarray(t.face_center, float).reshape(-1, 3) if t.dim >= 2 else np.zeros((0, 3))
    return out



def _vtk_matrix(O, R):
    m = vtk.vtkMatrix4x4()
    for i in range(3):
        for j in range(3):
            m.SetElement(i, j, float(R[i, j]))
        m.SetElement(i, 3, float(O[i]))
    return m


class WorkingCSActor:
    """draws the working coordinate system as an axis triad at its origin, the same screen size
    at every zoom (red X, green Y, blue Z)."""
    PIXELS = 70

    def __init__(self, renderer, name: str = "Global", pixels: int | None = None):
        self.ren = renderer
        self.name = name
        self.origin = np.zeros(3)
        if pixels:
            self.PIXELS = pixels
        self.axes = vtk.vtkAxesActor()
        self.axes.SetShaftTypeToLine()
        self.axes.SetConeRadius(0.25)
        self.axes.SetNormalizedTipLength(0.18, 0.18, 0.18)
        self.axes.SetPickable(False)
        self.axes.SetUseBounds(False)           # never part of Fit All
        for cap in (self.axes.GetXAxisCaptionActor2D(), self.axes.GetYAxisCaptionActor2D(),
                    self.axes.GetZAxisCaptionActor2D()):
            tp = cap.GetCaptionTextProperty()
            tp.SetColor(0, 0, 0)
            tp.SetBold(True)
            tp.SetItalic(False)
            tp.SetShadow(False)
            tp.SetFontSize(13)
            cap.GetTextActor().SetTextScaleModeToNone()
        self.axes.GetXAxisShaftProperty().SetLineWidth(2)
        self.axes.GetYAxisShaftProperty().SetLineWidth(2)
        self.axes.GetZAxisShaftProperty().SetLineWidth(2)
        renderer.AddViewProp(self.axes)
        self.visible = True
        renderer.AddObserver("StartEvent", lambda *a: self.update())

    def set_visible(self, on: bool) -> None:
        self.visible = on
        self.axes.SetVisibility(on)

    def set_frame(self, O, R) -> None:
        """Place the triad at origin O with axes R (columns), e.g. a relative coordinate system."""
        self.origin = np.asarray(O, float)
        m = vtk.vtkMatrix4x4()
        for i in range(3):
            for j in range(3):
                m.SetElement(i, j, float(R[i, j]))
            m.SetElement(i, 3, float(O[i]))
        self.axes.SetUserMatrix(m)

    def remove(self) -> None:
        self.ren.RemoveViewProp(self.axes)

    def update(self) -> None:
        if not self.visible:
            return
        h = self.ren.GetSize()[1]
        if h < 50:
            return
        cam = self.ren.GetActiveCamera()
        if cam.GetParallelProjection():
            world_h = 2 * cam.GetParallelScale()
        else:
            o = self.origin
            d = abs(np.dot(o - np.array(cam.GetPosition()), np.array(cam.GetDirectionOfProjection())))
            world_h = 2 * max(d, 1e-9) * math.tan(math.radians(cam.GetViewAngle() / 2))
        L = self.PIXELS * world_h / h
        if L > 0 and math.isfinite(L):
            self.axes.SetTotalLength(L, L, L)
            self._keep_in_depth(cam, 1.5 * L)

    def _keep_in_depth(self, cam, r: float) -> None:
        """Widen the clipping range so the triad is drawn: it does not count for the camera bounds, and with
        the model far from the origin its shafts fell outside, leaving only the X/Y/Z captions on screen."""
        d = float(np.dot(self.origin - np.array(cam.GetPosition()), np.array(cam.GetDirectionOfProjection())))
        near, far = cam.GetClippingRange()
        lo, hi = d - r, d + r
        if not cam.GetParallelProjection():
            if hi <= 0:
                return                  # behind the camera
            lo = max(lo, 1e-3 * max(far, hi))
        if lo < near or hi > far:
            cam.SetClippingRange(min(near, lo), max(far, hi))


def nice_length(x: float) -> float:
    """Largest 1/2/2.5/5 x 10^n not above x."""
    if not (x > 0 and math.isfinite(x)):
        return 1.0
    e = 10 ** math.floor(math.log10(x))
    for m in (5, 2.5, 2, 1):
        if m * e <= x:
            return m * e
    return e


class ScaleBar:
    """length ruler at the bottom of the 3D view: alternating black/white blocks with
    0 / half / full length labels in the model unit, rescaled on every render."""
    SEGMENTS = 4
    TARGET_PX = 200

    def __init__(self, renderer, unit_fn):
        self.ren, self.unit_fn = renderer, unit_fn
        self.poly = vtk.vtkPolyData()
        mapper = vtk.vtkPolyDataMapper2D()
        mapper.SetInputData(self.poly)
        mapper.SetScalarModeToUseCellData()
        self.bar = vtk.vtkActor2D()
        self.bar.SetMapper(mapper)
        self.bar.SetPickable(False)
        self.ren.AddViewProp(self.bar)
        self.labels = []
        for _ in range(3):
            t = vtk.vtkTextActor()
            tp = t.GetTextProperty()
            tp.SetFontSize(12)
            tp.SetColor(0, 0, 0)
            tp.SetJustificationToCentered()
            t.SetPickable(False)
            self.ren.AddViewProp(t)
            self.labels.append(t)
        self.visible = True
        renderer.AddObserver("StartEvent", lambda *a: self.update())

    def set_visible(self, on: bool) -> None:
        self.visible = on
        self.bar.SetVisibility(on)
        for t in self.labels:
            t.SetVisibility(on)

    def update(self) -> None:
        if not self.visible:
            return
        w, h = self.ren.GetSize()
        if w < 50 or h < 50:
            return
        cam = self.ren.GetActiveCamera()
        if cam.GetParallelProjection():
            world_h = 2 * cam.GetParallelScale()
        else:
            world_h = 2 * cam.GetDistance() * math.tan(math.radians(cam.GetViewAngle() / 2))
        if not world_h > 0:
            return
        unit, scale = self.unit_fn()
        px_per_unit = h / (world_h / scale)
        length = nice_length(self.TARGET_PX / px_per_unit)
        L = length * px_per_unit
        x0, y0, bh = (w - L) / 2, 28.0, 7.0
        pts = vtk.vtkPoints()
        cells = vtk.vtkCellArray()
        colors = vtk.vtkUnsignedCharArray()
        colors.SetNumberOfComponents(3)
        colors.InsertNextTuple3(0, 0, 0)        # outline: lines come before polys in the cell order
        n = 2                                   # white first half, black second half
        for i in range(n):
            xa, xb = x0 + L * i / n, x0 + L * (i + 1) / n
            k = pts.GetNumberOfPoints()
            for x, y in ((xa, y0), (xb, y0), (xb, y0 + bh), (xa, y0 + bh)):
                pts.InsertNextPoint(x, y, 0)
            cells.InsertNextCell(4, [k, k + 1, k + 2, k + 3])
            colors.InsertNextTuple3(*((255, 255, 255) if i % 2 == 0 else (0, 0, 0)))
        k = pts.GetNumberOfPoints()                         # outline
        for x, y in ((x0, y0), (x0 + L, y0), (x0 + L, y0 + bh), (x0, y0 + bh)):
            pts.InsertNextPoint(x, y, 0)
        lines = vtk.vtkCellArray()
        lines.InsertNextCell(5, [k, k + 1, k + 2, k + 3, k])
        self.poly.SetPoints(pts)
        self.poly.SetPolys(cells)
        self.poly.SetLines(lines)
        self.poly.GetCellData().SetScalars(colors)
        self.poly.Modified()
        fmt = lambda v: f"{v:g}"
        for t, (x, txt) in zip(self.labels, ((x0, "0"), (x0 + L / 2, fmt(length / 2)),
                                             (x0 + L, f"{fmt(length)} ({unit})"))):
            t.SetInput(txt)
            t.SetDisplayPosition(int(x), int(y0 - 17))
        # the unit text is centred on the bar end like the other labels; shift it so the number sits there
        self.labels[2].GetTextProperty().SetJustificationToLeft()
        self.labels[2].SetDisplayPosition(int(x0 + L - 4), int(y0 - 17))


class ModelerView(QWidget):
    picked = Signal(object, bool)               # list[Pick], add
    contextMenuRequested = Signal(QPoint)
    drawFinished = Signal(str, list)            # kind, points (np arrays in metres)
    cursorMoved = Signal(object)                # np.ndarray | None (snapped working point)
    drawPhaseChanged = Signal(str)
    tabPressed = Signal()
    navModeChanged = Signal(str)                # "rotate" / "pan" while a View button holds the left mouse, "" off
    hoverInfo = Signal(str)                       # Tab while drawing: jump to the coordinate fields

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(self)
        lay.addWidget(self.plotter.interactor)
        self.plotter.interactor.installEventFilter(self)
        p = self.plotter
        p.set_background("#dfe3e8", top="#f1f3f6")
        p.enable_anti_aliasing("ssaa") if hasattr(p, "enable_anti_aliasing") else None
        try:
            p.iren.clear_key_event_callbacks()
        except Exception:  # noqa: BLE001
            pass
        p.add_axes(interactive=False, line_width=2, color="#3b4558", xlabel="X", ylabel="Y", zlabel="Z")
        self._cube_widget = self._view_cube()
        self._cube_press = False
        self.scale_bar = ScaleBar(p.renderer, lambda: self.length_unit)
        self.working_cs = WorkingCSActor(p.renderer)
        self.actors: dict[str, ObjActors] = {}
        self.highlight_actors: list = []
        self.assignment_actors: list = []
        self.direction_actors: list = []                 # Thicken sheet preview arrows
        self.overlay_actors: dict[str, list] = {}
        self.overlay_bars: dict[str, list[str]] = {}     # colour bar titles per overlay
        self.overlay_focus: set[str] | None = None       # overlays drawn full, the others half transparent
        self.grid_actor = None
        self.origin_actor = None
        self.show_grid = True
        self.grid_plane = "XY"
        self.wcs = (np.zeros(3), np.eye(3))      # working coordinate system (origin, axes) in global
        self._frame = self.wcs                   # frame of the current drawing operation
        self._cs_actors: list = []
        self.grid_spacing = None        # metres; None = automatic
        self.draw: DrawState | None = None
        self.snap_vertices = True       # legacy flag; snap_modes decides
        self.snap_modes: set[str] = set(SNAP_MODES)
        self.snap_tolerance = 12        # pixels
        self.snap_kind = ""             # what the last drawing point snapped to
        self.movement_mode = "3D"       # movement mode: 3D, In Plane, Out of Plane
        self.axis_lock: int | None = None   # X/Y/Z keys while drawing lock the movement to one axis
        self.length_unit = ("mm", 1e-3)
        self._dim_actor = None
        self.prehighlight = True        # highlight what a click would select
        self._hover = None              # (pos, list[Pick], key)
        self._hover_index = 0           # B key: next item behind the cursor
        self._hover_actors: list = []
        self._styled_sel: set[str] = set()     # selected objects when the colours were last set
        self._styled_mode = None
        self._edge_cache = None                 # screen-space edge picking: all edge segments, see _edge_segments
        self._hover_pos = None
        self._snap_cache: dict[int, tuple] = {}
        self._snap_marker = None
        self._press_pos = None
        self._band = None               # plain left drag on the model: (start pos, fill actor, outline actor)
        self._navigating = set()        # mouse buttons currently moving the camera (also while drawing)
        self.nav_mode = ""              # "rotate" / "pan": the left mouse moves the camera until switched off
        self.on_projection = None       # callback(orthographic: bool) after the projection changed
        self._rpress_pos = None
        self._first_fit = True
        self._setup_style()
        from PySide6.QtCore import QTimer
        self._style_timer = QTimer(self)
        self._style_timer.timeout.connect(self.ensure_style)
        self._style_timer.start(500)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._update_hover)
        doc.geometryChanged.connect(self.refresh_geometry)
        doc.selectionChanged.connect(self.refresh_selection)
        QTimer.singleShot(0, self.fit_all)   # empty project: start on a 10 cm x 10 cm working area

    def request_render(self) -> None:
        """Redraw the view once the current event is handled. Several changes in one step (geometry,
        selection, highlight) then cost one render instead of one each."""
        w = self.plotter.interactor
        if w.isVisible():
            w.update()                  # Qt merges repeated requests into one paint
        else:
            self.plotter.render()

    def _plain_actor(self, mesh, color: str, opacity: float = 1.0, line_width: float | None = None,
                     point_size: float | None = None, spheres: bool = False, pickable: bool = False):
        """Single-colour actor added straight to the renderer, styled like pyvista's add_mesh with a colour.
        add_mesh costs milliseconds per call and, with a name, searches all actors, so models with
        hundreds of objects took seconds to show."""
        mapper = vtk.vtkDataSetMapper()
        mapper.SetInputData(mesh)
        mapper.ScalarVisibilityOff()
        act = vtk.vtkActor()
        act.SetMapper(mapper)
        prop = act.GetProperty()
        prop.SetColor(*_rgb(color))
        prop.SetOpacity(opacity)
        prop.SetInterpolationToFlat()
        prop.SetSpecularPower(100.0)
        prop.SetPointSize(point_size or 5.0)
        if line_width is not None:
            prop.SetLineWidth(line_width)
        prop.SetRenderPointsAsSpheres(spheres)
        act.SetPickable(pickable)
        self.plotter.renderer.AddActor(act)
        return act

    def _drop_actor(self, act) -> None:
        self.plotter.renderer.RemoveActor(act)

    def warm_up(self) -> None:
        """Draw every pre-highlight and selection style once, unseen, right after the window is shown. The
        graphics driver builds the shaders for a style (half transparent faces, wide lines, vertex spheres)
        the first time it is drawn: without this the first hover or click in the 3D view hung while they
        were built, and everything ran smoothly afterwards."""
        if getattr(self, "_warmed", False):
            return
        self._warmed = True
        ren = self.plotter.renderer
        rw = self.plotter.render_window
        cam = ren.GetActiveCamera()
        c = np.array(cam.GetFocalPoint(), float)
        s = 1e-6 * max(float(cam.GetDistance()), 1e-9)         # far below one pixel
        pts = c + s * np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
        t = types.SimpleNamespace(points=pts, tris=np.array([[0, 1, 2]]), tri_face=np.zeros(1, np.int32),
                                  edges=[pts[:2], pts[1:]], vertices=pts, dim=3)
        a = self._make_actors("", t)
        acts = [a.surface, a.edges, a.vertices]
        a.vertices.SetVisibility(True)
        face = a.mesh.extract_cells([0])
        edge = a.edge_mesh.extract_cells([0])
        point = pv.PolyData(pts[:1])
        # the pre-highlight styles of _update_hover
        hover = [self._plain_actor(a.mesh, HIGHLIGHT, opacity=0.45), self._plain_actor(face, HIGHLIGHT, opacity=0.6),
                 self._plain_actor(edge, HIGHLIGHT, line_width=4),
                 self._plain_actor(point, HIGHLIGHT, point_size=12, spheres=True)]
        for act in hover:
            act.GetMapper().SetResolveCoincidentTopologyToPolygonOffset()
            act.GetMapper().SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -2)
        # the selection styles of refresh_selection
        n = len(self.highlight_actors)
        self._add_highlight_mesh(a.mesh, HIGHLIGHT, opacity=0.35)
        self._add_highlight_mesh(face, HIGHLIGHT)
        acts += hover + self.highlight_actors[n:]
        del self.highlight_actors[n:]
        rim = self._plain_actor(edge, SEL_EDGE, line_width=2.5)
        rim.GetMapper().SetResolveCoincidentTopologyToPolygonOffset()
        rim.GetMapper().SetRelativeCoincidentTopologyLineOffsetParameters(-2, -300)
        acts += [rim, self._plain_actor(a.edge_mesh, SEL_EDGE, line_width=2.5),
                 self._plain_actor(edge, SEL_EDGE, line_width=5),
                 self._plain_actor(point, SEL_EDGE, point_size=14, spheres=True)]
        cullers = [ren.GetCullers().GetItemAsObject(i) for i in range(ren.GetCullers().GetNumberOfItems())]
        ren.GetCullers().RemoveAllItems()       # the tiny objects must be drawn, not culled
        swap = rw.GetSwapBuffers()
        try:
            rw.SwapBuffersOff()                 # drawn into the back buffer only, never shown
            rw.Render()
        except Exception:  # noqa: BLE001
            pass
        finally:
            rw.SetSwapBuffers(swap)
            for cl in cullers:
                ren.AddCuller(cl)
            for act in acts:
                self._drop_actor(act)
        self.request_render()

    # ------------------------------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------------------------------
    def _setup_style(self) -> None:
        style = vtk.vtkInteractorStyleTrackballCamera()
        # register with pyvista too: it re-applies its own style on some calls (update_style), which
        # silently dropped our handlers (no picking, no context menu, zoom only to the centre)
        try:
            self.plotter.iren.style = style
        except Exception:  # noqa: BLE001
            pass
        self.plotter.iren.interactor.SetInteractorStyle(style)
        self.style = style
        g = self._guard
        style.AddObserver("LeftButtonPressEvent", g(self._on_left_press))
        style.AddObserver("LeftButtonReleaseEvent", g(self._on_left_release))
        style.AddObserver("RightButtonPressEvent", g(self._on_right_press))
        style.AddObserver("RightButtonReleaseEvent", g(self._on_right_release))
        style.AddObserver("MouseMoveEvent", g(self._on_move))
        style.AddObserver("MouseWheelForwardEvent", g(lambda *a: self.zoom_at_cursor(1.2)))
        style.AddObserver("MouseWheelBackwardEvent", g(lambda *a: self.zoom_at_cursor(1 / 1.2)))
        style.AddObserver("MiddleButtonPressEvent", g(self._on_middle_press))
        style.AddObserver("MiddleButtonReleaseEvent", g(self._on_middle_release))
        style.AddObserver("KeyPressEvent", g(self._on_key))
        style.AddObserver("CharEvent", lambda *a: None)
        self.picker = vtk.vtkCellPicker()
        self.picker.SetTolerance(0.002)
        self.line_picker = vtk.vtkCellPicker()
        self.line_picker.SetTolerance(0.006)
        self.point_picker = vtk.vtkPointPicker()
        self.point_picker.SetTolerance(0.01)

    CUBE_VIEWPORT = (0.88, 0.78, 1.0, 1.0)

    def _view_cube(self):
        """Orientation cube at the top right; a click on a face turns the view to that side."""
        try:
            from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
            from vtkmodules.vtkRenderingAnnotation import vtkAnnotatedCubeActor
        except ImportError:
            return None
        a = vtkAnnotatedCubeActor()
        for side, text in (("XPlus", "Right"), ("XMinus", "Left"), ("YPlus", "Back"), ("YMinus", "Front"),
                           ("ZPlus", "Top"), ("ZMinus", "Bottom")):
            getattr(a, f"Set{side}FaceText")(text)
            fp = getattr(a, f"Get{side}FaceProperty")()
            fp.SetColor(0.30, 0.34, 0.42)
            fp.SetAmbient(1.0)
            fp.SetDiffuse(0.0)
        a.GetZPlusFaceProperty().SetColor(0.72, 0.50, 0.12)
        a.SetFaceTextScale(0.21)
        a.SetTextEdgesVisibility(False)
        cp = a.GetCubeProperty()
        cp.SetColor(0.97, 0.975, 0.985)
        cp.SetAmbient(0.75)
        cp.SetDiffuse(0.35)
        cp.SetEdgeVisibility(True)
        cp.SetEdgeColor(0.72, 0.75, 0.80)
        w = vtkOrientationMarkerWidget()
        w.SetOrientationMarker(a)
        w.SetInteractor(self.plotter.iren.interactor)
        w.SetViewport(*self.CUBE_VIEWPORT)
        w.SetEnabled(1)
        w.InteractiveOff()
        return w

    def _cube_face(self, x: float, y: float) -> str | None:
        """View name for a click at widget pixel (x, y) on the orientation cube, None outside it."""
        if self._cube_widget is None:
            return None
        W, H = self.plotter.interactor.width(), self.plotter.interactor.height()
        x0, y0, x1, y1 = self.CUBE_VIEWPORT
        cx, cy = (x0 + x1) / 2 * W, (1 - (y0 + y1) / 2) * H
        r = min((x1 - x0) * W, (y1 - y0) * H) / 2
        u, v = (x - cx) / r, (cy - y) / r
        if u * u + v * v > 1.0 or r <= 0:
            return None
        cam = self.plotter.camera
        f = np.subtract(cam.focal_point, cam.position)
        f /= np.linalg.norm(f)
        up = np.asarray(cam.up, float)
        up = up - f * up.dot(f)
        up /= np.linalg.norm(up)
        right = np.cross(f, up)
        n = right * u + up * v - f * math.sqrt(max(0.0, 1 - u * u - v * v))
        i = int(np.argmax(np.abs(n)))
        return [("left", "right"), ("front", "back"), ("bottom", "top")][i][int(n[i] > 0)]

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) and ev.button() == Qt.LeftButton:
            pos = ev.position()
            face = self._cube_face(pos.x(), pos.y()) if self.draw is None else None
            if ev.type() == QEvent.MouseButtonPress and face:
                self._cube_press = True
                return True
            if ev.type() == QEvent.MouseButtonRelease and self._cube_press:
                self._cube_press = False
                if face:
                    self.view(face)
                return True
        if self.draw is not None and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.tabPressed.emit()
            return True
        return super().eventFilter(obj, ev)

    def _on_key(self, obj, ev):
        key = self.plotter.iren.interactor.GetKeySym() or ""
        k = key.lower()
        if self.draw is None:
            if k == "b":
                self.next_behind()
            return
        if self.draw.kind == "CreatePolyline" and k in SEG_KEYS:
            # right click > Set Edge Type (Straight / 3 Point Arc / Center Point Arc / Spline)
            if _center_pending(self.draw):
                return      # the center of an arc is placed: finish the arc first
            self.draw.seg_mode = SEG_KEYS[k]
            self.drawPhaseChanged.emit(self._phase_hint())
            self._update_preview()
            return
        if k in ("x", "y", "z"):
            i = "xyz".index(k)
            self.axis_lock = None if self.axis_lock == i else i
            self.drawPhaseChanged.emit(self._phase_hint())
            pos = self._event_pos()
            p = self._draw_point(pos)
            self.draw.current = p
            self._show_snap_marker(p, self.snap_kind)
            self._show_dimensions(p, pos)
            self._update_preview()
            self.cursorMoved.emit(p)

    def _guard(self, fn):
        """Mouse handlers must never leave the view in a half-pressed state: on an error report it and
        reset the interaction so selecting and the context menu keep working."""
        def wrapped(*args):
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                try:
                    self.doc.message.emit("error", f"3D view: {type(e).__name__}: {e}")
                except Exception:  # noqa: BLE001
                    pass
                self.reset_interaction()
        return wrapped

    def ensure_style(self) -> None:
        """Re-install our interactor style if something replaced it."""
        iren = self.plotter.iren.interactor
        if iren.GetInteractorStyle() is not self.style:
            iren.SetInteractorStyle(self.style)

    def reset_interaction(self) -> None:
        """Release any camera interaction and pressed-button state (Escape also calls this)."""
        self.ensure_style()
        self._navigating.clear()
        self._dolly = False
        self._panning = False
        self._mid_mode = None
        self._rot_last = None
        self._press_pos = None
        self._rpress_pos = None
        self._last_hits = None
        if getattr(self, "_band", None) is not None:
            self._show_band(None)
            self._band = None
        try:
            for end in (self.style.EndRotate, self.style.EndPan, self.style.EndDolly, self.style.EndSpin,
                        self.style.EndZoom):
                end()
        except Exception:  # noqa: BLE001
            pass

    def _event_pos(self):
        return self.plotter.iren.interactor.GetEventPosition()

    def zoom_at_cursor(self, factor: float, pos=None) -> None:
        """Zoom in (factor > 1) or out about the point under the mouse, which stays fixed on screen."""
        ren = self.plotter.renderer
        cam = ren.GetActiveCamera()
        pos = pos or self._event_pos()
        fp = np.array(cam.GetFocalPoint())
        ren.SetWorldPoint(*fp, 1.0)
        ren.WorldToDisplay()
        zf = ren.GetDisplayPoint()[2]
        ren.SetDisplayPoint(pos[0], pos[1], zf)
        ren.DisplayToWorld()
        w = np.array(ren.GetWorldPoint())
        P = w[:3] / (w[3] or 1.0)
        cp = np.array(cam.GetPosition())
        if cam.GetParallelProjection():
            cam.SetParallelScale(cam.GetParallelScale() / factor)
            shift = (P - fp) * (1 - 1 / factor)
            cam.SetFocalPoint(*(fp + shift))
            cam.SetPosition(*(cp + shift))
        else:
            cam.SetFocalPoint(*(P + (fp - P) / factor))
            cam.SetPosition(*(P + (cp - P) / factor))
        ren.ResetCameraClippingRange()
        if self.draw is not None:
            p = self._draw_point(pos)
            self.draw.current = p
            self._show_snap_marker(p, self.snap_kind)
        self.request_render()

    def _start_dolly(self) -> None:
        # not OnRightButtonDown: with Shift held VTK starts its environment rotation instead
        x, y = self._event_pos()
        self.style.FindPokedRenderer(x, y)
        self.style.StartDolly()

    def _on_middle_press(self, obj, ev):
        # middle drag rotates (also while drawing, the snap stays active),
        # Ctrl + middle pans, Shift + middle zooms
        self._navigating.add("middle")
        iren = self.plotter.iren.interactor
        self._mid_mode = "pan" if iren.GetControlKey() else "zoom" if iren.GetShiftKey() else "rotate"
        if self._mid_mode == "pan":
            self.style.OnMiddleButtonDown()
        elif self._mid_mode == "zoom":
            self._start_dolly()
        else:
            self._rot_last = self._event_pos()

    def _on_middle_release(self, obj, ev):
        self._navigating.discard("middle")
        mode, self._mid_mode = getattr(self, "_mid_mode", "pan"), None
        if mode == "pan":
            self.style.OnMiddleButtonUp()
        elif mode == "zoom":
            self.style.EndDolly()
        else:
            self._rot_last = None
        if self.draw is not None:
            p = self._draw_point(self._event_pos())
            self.draw.current = p
            self._show_snap_marker(p, self.snap_kind)
            self._update_preview()

    def _on_left_press(self, obj, ev):
        self._press_pos = self._event_pos()
        if self.draw is None:
            self._navigating.discard("left")
        if self.draw is not None:
            # Alt + left drag rotates while drawing; a plain click enters a point
            if _alt_key():
                self._navigating.add("left")
                self._rot_last = self._press_pos
            return
        iren = self.plotter.iren.interactor
        self._rot_last = None
        if self.nav_mode == "rotate":
            self._navigating.add("left")
            self._rot_last = self._press_pos
            return
        if self.nav_mode == "pan":
            self._navigating.add("left")
            self._panning = True
            self.style.OnMiddleButtonDown()
            return
        if _alt_key() and iren.GetShiftKey():
            self._start_dolly()
            self._dolly = True
        elif _alt_key():
            self._dolly = False                 # Alt + left drag rotates (a click without a drag still selects)
            self._navigating.add("left")
            self._rot_last = self._press_pos
        elif iren.GetShiftKey():
            self._dolly = False
            self._panning = True
            self.style.OnMiddleButtonDown()     # Shift + left drag pans
        else:
            self._dolly = False                 # plain left drag: box selection (rotate with the middle button)
            self._show_band(None)               # a rectangle whose release was lost goes, it is not left behind
            self._band = (self._press_pos, None, None)

    def _on_left_release(self, obj, ev):
        pos = self._event_pos()
        moved = self._press_pos is None or abs(pos[0] - self._press_pos[0]) + abs(pos[1] - self._press_pos[1]) > 3
        self._press_pos = None
        band = self._band
        if band is not None:
            self._show_band(None)
            self._band = None
        if self.draw is not None:
            if "left" in self._navigating:
                self._navigating.discard("left")
                self._rot_last = None
                if moved:
                    return
            if not moved:
                self._draw_click(pos)
            return
        if getattr(self, "_dolly", False):
            self._dolly = False
            self.style.EndDolly()
        elif getattr(self, "_panning", False):
            self._panning = False
            self.style.OnMiddleButtonUp()
        self._rot_last = None
        self._navigating.discard("left")
        if self.nav_mode:
            return                              # Rotate / Pan mode: the left mouse does not select
        add = bool(self.plotter.iren.interactor.GetControlKey())
        if not moved:
            self.picked.emit(self.pick(pos), add)
        elif band is not None:
            picks = self.box_picks(band[0], pos)
            if add:     # Ctrl + drag adds to the selection (a Ctrl click on one item toggles it)
                have = {(p.obj, p.index) for p in self.doc.selection}
                picks = [p for p in picks if (p.obj, p.index) not in have]
                if picks:
                    self.picked.emit(picks, True)
            else:
                self.picked.emit(picks, False)

    def _on_right_press(self, obj, ev):
        self._rpress_pos = self._event_pos()
        self._navigating.add("right")
        self.style.OnRightButtonDown()          # right drag zooms, also while drawing

    def _on_right_release(self, obj, ev):
        pos = self._event_pos()
        rp, self._rpress_pos = self._rpress_pos, None
        self._navigating.discard("right")
        self.style.OnRightButtonUp()
        if rp and abs(pos[0] - rp[0]) + abs(pos[1] - rp[1]) <= 3:
            if self.draw is not None and self.draw.kind == "CreatePolyline":
                self.finish_polyline()
                return
            if self.draw is not None:
                self.cancel_draw()
                return
            h = self.plotter.interactor.height()
            ratio = self.plotter.interactor.devicePixelRatioF()
            self.contextMenuRequested.emit(QPoint(int(pos[0] / ratio), int(h - pos[1] / ratio)))

    def _rotate_view(self, pos) -> None:
        """Turntable rotation about the model centre: horizontal drag turns about the view's up
        axis, vertical drag tilts about the screen's horizontal axis; a constant angle per pixel and no
        roll drift, the model stays in view."""
        last = self._rot_last
        self._rot_last = pos
        if last is None:
            return
        dx, dy = pos[0] - last[0], pos[1] - last[1]
        if dx == 0 and dy == 0:
            return
        ren = self.plotter.renderer
        cam = ren.GetActiveCamera()
        h = max(1, self.plotter.interactor.height() * self.plotter.interactor.devicePixelRatioF())
        k = np.radians(300.0) / h               # a drag over the full view height turns by 300 deg
        b = np.asarray(self.doc.model_bbox(), float)
        c = (b[:3] + b[3:]) / 2 if self.doc.tess else np.array(cam.GetFocalPoint())
        P, F, U = (np.array(cam.GetPosition()), np.array(cam.GetFocalPoint()), np.array(cam.GetViewUp()))
        d = F - P
        d /= np.linalg.norm(d) or 1.0
        right = np.cross(d, U)
        right /= np.linalg.norm(right) or 1.0
        U = np.cross(right, d)

        def rot(axis, ang):
            axis = axis / (np.linalg.norm(axis) or 1.0)
            K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
            return np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K
        R = rot(right, dy * k) @ rot(U, -dx * k)
        cam.SetPosition(*(c + R @ (P - c)))
        cam.SetFocalPoint(*(c + R @ (F - c)))
        cam.SetViewUp(*(R @ U))
        cam.OrthogonalizeViewUp()
        ren.ResetCameraClippingRange()
        self.request_render()

    def _on_move(self, obj, ev):
        if getattr(self, "_rot_last", None) is not None:
            self._rotate_view(self._event_pos())
            return
        if self.draw is not None and self._navigating:
            self.style.OnMouseMove()            # pan / rotate / zoom without leaving the draw mode
            p = self._draw_point(self._event_pos())
            self.draw.current = p
            self._show_snap_marker(p, self.snap_kind)
            return
        if self.draw is not None:
            pos = self._event_pos()
            p = self._draw_point(pos)
            self.draw.current = p
            self._show_snap_marker(p, self.snap_kind)
            self._show_dimensions(p, pos)
            self._update_preview()
            self.cursorMoved.emit(p)
            return
        if self._band is not None and not QApplication.mouseButtons() & Qt.LeftButton:
            # the release went elsewhere (another tab, window or dialog took the mouse): no rectangle stays
            self._show_band(None)
            self._band = None
            self._press_pos = None
        if self._band is not None and self._press_pos is not None:
            pos = self._event_pos()
            p0 = self._band[0]
            if self._band[1] is not None or abs(pos[0] - p0[0]) + abs(pos[1] - p0[1]) > 3:
                if self._hover_actors:
                    self._clear_hover(render=False)
                self._show_band(pos)
            return
        self.style.OnMouseMove()
        if self.prehighlight and not self._navigating and self._press_pos is None and self._rpress_pos is None:
            pos = self._event_pos()
            if self._hover_pos is None or abs(pos[0] - self._hover_pos[0]) + abs(pos[1] - self._hover_pos[1]) > 2:
                self._hover_index = 0
            self._hover_pos = pos
            if not self._hover_timer.isActive():
                self._hover_timer.start(40)
        elif self._hover_actors:
            self._clear_hover()

    # ------------------------------------------------------------------------------------------
    # picking
    # ------------------------------------------------------------------------------------------
    def _actor_owner(self, actor, part: str) -> str | None:
        for name, a in self.actors.items():
            if getattr(a, part) is actor:
                return name
        return None

    def _surface_hits(self, pos, max_hits: int = 12) -> list[tuple[float, str, int]]:
        """All object surfaces under the cursor, front to back. Coincident surfaces (a sheet lying on a
        solid face, e.g. a trace on a substrate) count as the same depth and the sheet comes first,
        as it is drawn on top."""
        ren = self.plotter.renderer
        cam = np.array(ren.GetActiveCamera().GetPosition())
        hits, disabled = [], []
        try:
            for _ in range(max_hits):
                self.picker.Pick(pos[0], pos[1], 0, ren)
                actor = self.picker.GetActor()
                if actor is None:
                    break
                name = self._actor_owner(actor, "surface")
                actor.PickableOff()
                disabled.append(actor)
                if name is None:
                    continue
                d = float(np.linalg.norm(np.array(self.picker.GetPickPosition()) - cam))
                hits.append((d, name, int(self.picker.GetCellId())))
        finally:
            for a in disabled:
                a.PickableOn()
        if not hits:
            return []
        b = np.array(ren.ComputeVisiblePropBounds()).reshape(3, 2)
        tol = 1e-4 * max(float(np.linalg.norm(b[:, 1] - b[:, 0])), 1e-12)
        hits.sort(key=lambda h: h[0])
        out, i = [], 0
        while i < len(hits):            # group coincident hits, sheets before solids inside a group
            j = i
            while j + 1 < len(hits) and hits[j + 1][0] - hits[i][0] <= tol:
                j += 1
            grp = sorted(hits[i:j + 1], key=lambda h: self.actors[h[1]].dim)
            out += grp
            i = j + 1
        # a (mostly transparent) region around the model would catch every click: the objects inside
        # come first, the region is still reached by clicking again / B
        des = self.doc.design

        def background(h):
            if not des.has_obj(h[1]):
                return False
            o = des.obj(h[1])
            return o.history[0].kind == "CreateRegion" or float(o.transparency) >= 0.7
        front = [h for h in out if not background(h)]
        return front + [h for h in out if background(h)] if front else out

    def next_behind(self) -> None:
        """B key: pre-highlight the next object/face behind the one under the cursor."""
        if self._hover_pos is None:
            return
        self._hover_index += 1
        self._update_hover()

    def peek(self, pos, k: int = 0) -> tuple[list[Pick], int, int]:
        """What a click at pos would select, taking the k-th surface behind. Returns (picks, k, n)."""
        mode = self.doc.selection_mode
        if mode == "object":
            ln = self._nearest_edge(pos, dims=(0, 1))
            if ln is not None:
                return [Pick(ln[0], -1)], 0, 1
        if mode in ("object", "face"):
            hits = self._surface_hits(pos)
            if not hits:
                return [], 0, 0
            k = k % len(hits)
            _, name, cid = hits[k]
            if mode == "object":
                return [Pick(name, -1)], k, len(hits)
            mesh = self.actors[name].mesh
            if mesh is None or cid < 0:
                return [], k, len(hits)
            return [Pick(name, int(mesh.cell_data["face"][cid]))], k, len(hits)
        return self._pick_line_point(pos, mode), 0, 1

    def _update_hover(self) -> None:
        pos = self._hover_pos
        if pos is None or self.draw is not None or not self.prehighlight:
            self._clear_hover()
            return
        try:
            picks, k, n = self.peek(pos, self._hover_index)
        except Exception:  # noqa: BLE001
            picks, k, n = [], 0, 0
        self._hover_index = k
        key = (self.doc.selection_mode, tuple((p.obj, p.index) for p in picks))
        if self._hover is not None and self._hover[2] == key:
            self._hover = (pos, picks, key)
            return
        self._clear_hover(render=False)
        self._hover = (pos, picks, key)
        sel = {(s.obj, s.index) for s in self.doc.selection}
        for pk in picks:
            if (pk.obj, pk.index) in sel:
                continue
            a = self.actors.get(pk.obj)
            if a is None:
                continue
            mode = self.doc.selection_mode
            color = "#ffb000"
            if mode == "object" and a.mesh is not None:
                self._hover_actors.append(self._plain_actor(a.mesh, color, opacity=0.45))
            elif mode == "face" and a.mesh is not None:
                sub = a.mesh.extract_cells(np.where(a.mesh.cell_data["face"] == pk.index)[0])
                if sub.n_cells:
                    self._hover_actors.append(self._plain_actor(sub, color, opacity=0.6))
            elif mode == "edge" and a.edge_mesh is not None:
                sub = a.edge_mesh.extract_cells(np.where(a.edge_mesh.cell_data["edge"] == pk.index)[0])
                self._hover_actors.append(self._plain_actor(sub, color, line_width=4))
            elif mode == "vertex":
                t = self.doc.tess.get(pk.obj)
                if t is not None and pk.index < len(t.vertices):
                    self._hover_actors.append(self._plain_actor(
                        pv.PolyData(np.asarray(t.vertices[pk.index], float).reshape(1, 3)), color,
                        point_size=12, spheres=True))
        for act in self._hover_actors:
            try:
                act.GetMapper().SetResolveCoincidentTopologyToPolygonOffset()
                act.GetMapper().SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -2)
            except Exception:  # noqa: BLE001
                pass
        if n > 1 and picks:
            self.hoverInfo.emit(f"{picks[0].obj}  ({k + 1}/{n} under the cursor, B: next behind)")
        else:
            self.hoverInfo.emit(picks[0].obj if picks else "")
        self.request_render()

    def _clear_hover(self, render: bool = True) -> None:
        for act in self._hover_actors:
            self._drop_actor(act)
        self._hover_actors = []
        self._hover = None
        if render:
            self.request_render()

    PICK_PX = 8     # screen distance (pixels) within which a line or edge is picked

    def _edge_segments(self):
        """Every edge polyline segment of all objects as flat arrays, rebuilt only when the objects change:
        (names, actors, points, segment start/end point, object of each segment, line of each segment, edge id
        of each line). Lines are numbered over all objects in the order of self.actors."""
        key = tuple((n, id(a.edge_mesh)) for n, a in self.actors.items())
        if self._edge_cache is not None and self._edge_cache[0] == key:
            return self._edge_cache[1]
        from vtkmodules.util.numpy_support import vtk_to_numpy
        names, acts, P, IA, IB, SO, SC, EID = [], [], [], [], [], [], [], []
        npt = ncell = 0
        for k, (name, a) in enumerate(self.actors.items()):
            names.append(name)
            acts.append(a)
            em = a.edge_mesh
            if em is None or em.n_cells == 0:
                continue
            ca = em.GetLines()
            off = vtk_to_numpy(ca.GetOffsetsArray()).astype(np.int64)
            con = vtk_to_numpy(ca.GetConnectivityArray()).astype(np.int64)
            i = np.arange(len(con) - 1)
            i = i[~np.isin(i, off[1:-1] - 1)]          # no segment from the end of one line to the next line
            P.append(np.asarray(em.points, float))
            IA.append(con[i] + npt)
            IB.append(con[i + 1] + npt)
            SO.append(np.full(len(i), k))
            SC.append(np.searchsorted(off, i, side="right") - 1 + ncell)
            EID.append(np.asarray(em.cell_data["edge"]))
            npt += em.n_points
            ncell += len(off) - 1
        cat = lambda L, dt: np.concatenate(L).astype(dt) if L else np.zeros(0, dt)  # noqa: E731
        data = (names, acts, np.vstack(P) if P else np.zeros((0, 3)), cat(IA, np.int64), cat(IB, np.int64),
                cat(SO, np.int64), cat(SC, np.int64), cat(EID, np.int64))
        # the meshes stay referenced with the cache, so a new mesh can never reuse an id in the key
        self._edge_cache = (key, data, [a.edge_mesh for a in acts])
        return data

    def _nearest_edge(self, pos, dims=None, px: float | None = None) -> tuple[str, int] | None:
        """Visible edge (or polyline) closest to the cursor in screen space, within PICK_PX pixels.
        Much easier to hit than a ray pick on a 1 px line. All segments at once (numpy): a loop over
        the lines took most of a second on models with hundreds of objects."""
        px = self.PICK_PX if px is None else px
        names, acts, X3, ia, ib, so, sc, eid = self._edge_segments()
        use = np.array([a.edge_mesh is not None and a.edges is not None and bool(a.edges.GetVisibility())
                        and (dims is None or a.dim in dims) for a in acts], bool)
        if not len(ia) or not use.any():
            return None
        keep = use[so]
        ia, ib, so, sc = ia[keep], ib[keep], so[keep], sc[keep]
        if not len(ia):
            return None
        ren = self.plotter.renderer
        w, h = ren.GetSize()
        cam = ren.GetActiveCamera()
        M = np.array([[cam.GetCompositeProjectionTransformMatrix(w / max(h, 1), -1, 1).GetElement(i, j)
                       for j in range(4)] for i in range(4)])
        X = np.c_[X3, np.ones(len(X3))] @ M.T
        wv = X[:, 3]
        ok = wv > 1e-12
        ws = np.where(ok, wv, 1)
        xy = np.empty((len(X3), 2))
        xy[:, 0] = (X[:, 0] / ws + 1) * 0.5 * w
        xy[:, 1] = (X[:, 1] / ws + 1) * 0.5 * h
        z = X[:, 2] / ws
        P = np.array(pos, float)
        A, B = xy[ia], xy[ib]
        d = B - A
        L2 = np.maximum((d ** 2).sum(1), 1e-12)
        t = np.clip(((P - A) * d).sum(1) / L2, 0, 1)
        dist = np.linalg.norm(A + t[:, None] * d - P, axis=1)
        dist[~(ok[ia] & ok[ib])] = np.inf
        # per line its closest segment (the first one on a tie), then the line nearest on screen, nearer in
        # depth among equally near ones, the first line on a full tie
        order = np.lexsort((dist, sc))
        first = np.r_[True, sc[order][1:] != sc[order][:-1]]
        j = order[first]
        j = j[dist[j] <= px]
        if not len(j):
            return None
        depth = z[ia[j]] + t[j] * (z[ib[j]] - z[ia[j]])
        b = j[np.lexsort((sc[j], depth, np.round(dist[j] / 2)))[0]]
        return names[int(so[b])], int(eid[sc[b]])

    def _pick_line_point(self, pos, mode) -> list[Pick]:
        ren = self.plotter.renderer
        if mode == "edge":
            hit = self._nearest_edge(pos)
            return [Pick(hit[0], hit[1])] if hit else []
        if mode == "vertex":
            self.point_picker.Pick(pos[0], pos[1], 0, ren)
            actor = self.point_picker.GetActor()
            name = self._actor_owner(actor, "vertices") if actor else None
            if name is None:
                return []
            return [Pick(name, int(self.point_picker.GetPointId()))]
        return []

    def pick(self, pos) -> list[Pick]:
        mode = self.doc.selection_mode
        ren = self.plotter.renderer
        hv = self._hover
        if hv is not None and hv[2][0] == mode and abs(hv[0][0] - pos[0]) + abs(hv[0][1] - pos[1]) <= 3 \
                and self._hover_index > 0:
            # B was used: select exactly what is pre-highlighted
            self._last_hits = None
            picks = hv[1]
            self._clear_hover(render=False)
            return picks
        self._clear_hover(render=False)
        if mode == "object":
            ln = self._nearest_edge(pos, dims=(0, 1))
            if ln is not None:      # polylines have no surface: pick them by screen distance
                self._last_hits = None
                return [Pick(ln[0], -1)]
        if mode in ("object", "face"):
            hits = self._surface_hits(pos)
            if not hits:
                self._last_hits = None
                return []
            # clicking again on the same spot cycles through everything under the cursor (front to back)
            key = [mode] + [(h[1], h[2]) for h in hits]
            last = getattr(self, "_last_hits", None)
            k = 0
            if last and abs(last[0][0] - pos[0]) + abs(last[0][1] - pos[1]) <= 3 and last[1] == key:
                k = (last[2] + 1) % len(hits)
            self._last_hits = (pos, key, k)
            _, name, cid = hits[k]
            if k > 0:
                self.doc.message.emit("info", f"Picked {name} ({k + 1}/{len(hits)} under cursor; "
                                              "click again to select the next one behind)")
            if mode == "object":
                return [Pick(name, -1)]
            mesh = self.actors[name].mesh
            if mesh is None or cid < 0:
                return []
            return [Pick(name, int(mesh.cell_data["face"][cid]))]
        if mode == "edge":
            return self._pick_line_point(pos, mode)
        if mode == "vertex":
            self.point_picker.Pick(pos[0], pos[1], 0, ren)
            actor = self.point_picker.GetActor()
            name = self._actor_owner(actor, "vertices") if actor else None
            if name is None:
                return []
            return [Pick(name, int(self.point_picker.GetPointId()))]
        return []

    # ------------------------------------------------------------------------------------------
    # box selection
    # ------------------------------------------------------------------------------------------
    def _show_band(self, pos) -> None:
        """Draw the selection rectangle from the press position to pos (None removes it)."""
        if self._band is None:
            return
        p0, fill, line = self._band
        ren = self.plotter.renderer
        if pos is None:
            for act in (fill, line):
                if act is not None:
                    ren.RemoveViewProp(act)
            self._band = (p0, None, None)
            self.request_render()
            return
        x0, y0 = p0
        x1, y1 = pos
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        if fill is None:
            fill, line = vtk.vtkActor2D(), vtk.vtkActor2D()
            for act, opacity in ((fill, 0.12), (line, 0.9)):
                m = vtk.vtkPolyDataMapper2D()
                act.SetMapper(m)
                act.GetProperty().SetColor(*_rgb(SEL_EDGE))
                act.GetProperty().SetOpacity(opacity)
                act.PickableOff()
                ren.AddViewProp(act)
            line.GetProperty().SetLineWidth(1.5)
            self._band = (p0, fill, line)
        pts = vtk.vtkPoints()
        for x, y in corners:
            pts.InsertNextPoint(x, y, 0)
        for act, closed in ((fill, False), (line, True)):
            cells = vtk.vtkCellArray()
            ids = [0, 1, 2, 3, 0] if closed else [0, 1, 2, 3]
            cells.InsertNextCell(len(ids))
            for i in ids:
                cells.InsertCellPoint(i)
            pd = vtk.vtkPolyData()
            pd.SetPoints(pts)
            pd.SetLines(cells) if closed else pd.SetPolys(cells)
            act.GetMapper().SetInputData(pd)
        self.request_render()

    def _box_parts(self, name: str, t, mode: str) -> list[tuple[int, np.ndarray]]:
        """The selectable parts of one object in a selection mode: (pick index, its points in world)."""
        if mode == "object":
            X = [np.asarray(t.points, float).reshape(-1, 3)] + [np.asarray(e, float).reshape(-1, 3)
                                                                  for e in t.edges]
            X = np.vstack(X) if X else np.empty((0, 3))
            return [(-1, X)] if len(X) else []
        if mode == "face":
            if not len(t.tris):
                return []
            tris, tf, P = np.asarray(t.tris), np.asarray(t.tri_face), np.asarray(t.points, float)
            return [(int(f), P[np.unique(tris[tf == f])]) for f in np.unique(tf)]
        if mode == "edge":
            return [(i, np.asarray(e, float).reshape(-1, 3)) for i, e in enumerate(t.edges) if len(e)]
        if mode == "vertex":
            return [(i, np.asarray(v, float).reshape(1, 3)) for i, v in enumerate(np.asarray(t.vertices))]
        return []

    def box_picks(self, p0, p1) -> list[Pick]:
        """Everything (objects, or faces / edges / vertices in those modes) of the visible objects that
        lies completely inside the screen rectangle p0-p1 and in front of the camera."""
        xmin, xmax = sorted((p0[0], p1[0]))
        ymin, ymax = sorted((p0[1], p1[1]))
        mode = self.doc.selection_mode
        out, seen = [], set()
        for name, t in self.doc.tess.items():
            a = self.actors.get(name)
            if a is None:
                continue
            shown = [act for act in (a.surface, a.edges) if act is not None]
            if not shown or not any(act.GetVisibility() for act in shown):
                continue
            seen.add(name)
            for index, X in self._box_parts(name, t, mode):
                if not len(X):
                    continue
                xy, ok = self._display_coords(X)
                if ok.all() and (xy[:, 0] >= xmin).all() and (xy[:, 0] <= xmax).all() \
                        and (xy[:, 1] >= ymin).all() and (xy[:, 1] <= ymax).all():
                    out.append(Pick(name, index))
        if mode == "object":
            # a group is one unit: take it only when every shown member is inside (the selection expands
            # a member to its whole group)
            from ..model import groups as G
            des = self.doc.design
            inside = {p.obj for p in out}
            out = [p for p in out if not des.has_obj(p.obj) or not des.obj(p.obj).group
                   or set(G.members(des, des.obj(p.obj).group)) & seen <= inside]
        return out

    # ------------------------------------------------------------------------------------------
    # geometry display
    # ------------------------------------------------------------------------------------------
    def refresh_cs(self) -> None:
        """Working CS (drawing frame, grid, triad): the only coordinate system shown in the view."""
        from ..model import coordsys
        des = self.doc.design
        ev = self.doc.evaluator()
        try:
            self.wcs = coordsys.frame(des, des.working_cs, ev)
        except (KeyError, ValueError, Exception):  # noqa: BLE001
            self.wcs = self._identity
        self.working_cs.set_frame(*self.wcs)
        self.working_cs.name = des.working_cs
        for a in self._cs_actors:
            a.remove()
        self._cs_actors = []
        self.update_grid()

    def refresh_geometry(self) -> None:
        des = self.doc.design
        try:
            self.refresh_cs()
        except Exception:  # noqa: BLE001
            pass
        live = {o.name: o for o in des.live_objects()}
        for name in list(self.actors):
            t = self.doc.tess.get(name)
            if name not in live or t is None or id(t) != self.actors[name].tess_id:
                self._remove_obj(name)
        for name, o in live.items():
            t = self.doc.tess.get(name)
            if t is None:
                continue
            if name not in self.actors:
                self.actors[name] = self._make_actors(name, t)
        self.update_grid()
        self.refresh_selection(full=True)
        self._refresh_assignments()
        if self._first_fit and self.doc.tess:
            self._first_fit = False
            self.fit_all()
        elif not self.doc.tess and not self._first_fit:
            self._first_fit = True      # model emptied: back to the 10 cm working area
            self.fit_all()
        self.request_render()

    def _remove_obj(self, name: str) -> None:
        a = self.actors.pop(name, None)
        if a:
            for act in (a.surface, a.edges, a.vertices):
                if act is not None:
                    self._drop_actor(act)

    def _make_actors(self, name: str, t) -> ObjActors:
        a = ObjActors(tess_id=id(t), dim=int(getattr(t, "dim", 3)))
        if len(t.tris):
            # one vertex per (point, CAD face): normals are smooth inside a curved face and sharp at its
            # edges, so spheres and cones look round instead of faceted
            tris = np.asarray(t.tris)
            tf = np.asarray(t.tri_face)
            key = tris.astype(np.int64) * (int(tf.max()) + 1 if len(tf) else 1) + tf[:, None]
            uk, inv = np.unique(key.ravel(), return_inverse=True)
            pts = np.asarray(t.points)[uk // (int(tf.max()) + 1 if len(tf) else 1)]
            tris2 = inv.reshape(-1, 3)
            faces = np.hstack([np.full((len(tris2), 1), 3), tris2]).ravel()
            mesh = pv.PolyData(pts, faces)
            mesh.cell_data["face"] = t.tri_face
            try:
                nm = mesh.compute_normals(cell_normals=False, split_vertices=False, consistent_normals=True,
                                          auto_orient_normals=False, feature_angle=180)
                if nm.n_cells == mesh.n_cells:
                    mesh = nm
                    mesh.point_data.active_normals_name = "Normals"
            except Exception:  # noqa: BLE001
                pass
            a.mesh = mesh
            a.surface = self._plain_actor(mesh, "#999999", pickable=True)
            a.surface.GetProperty().SetInterpolationToPhong()
            # coplanar faces: pull sheets forward by a hair so they win over the solid face they lie on,
            # small enough that a sheet behind a thin solid (ground under a substrate) stays hidden
            mp = a.surface.GetMapper()
            mp.SetResolveCoincidentTopologyToPolygonOffset()
            if t.dim == 2:
                mp.SetRelativeCoincidentTopologyPolygonOffsetParameters(-1, -1)
            else:
                mp.SetRelativeCoincidentTopologyPolygonOffsetParameters(0, 0)
        if t.edges:
            pts, lines, eids = [], [], []
            off = 0
            for i, e in enumerate(t.edges):
                if len(e) < 2:
                    continue
                pts.append(e)
                n = len(e)
                lines.append(np.r_[n, np.arange(off, off + n)])
                eids.append(i)
                off += n
            if pts:
                em = pv.PolyData(np.vstack(pts), lines=np.concatenate(lines))
                em.cell_data["edge"] = np.array(eids)
                a.edge_mesh = em
                a.edges = self._plain_actor(em, "#202020", line_width=1, pickable=True)
        if len(t.vertices):
            vm = pv.PolyData(np.asarray(t.vertices, float))
            a.vertices = self._plain_actor(vm, "#000000", point_size=7, spheres=True, pickable=True)
            a.vertices.SetVisibility(self.doc.selection_mode == "vertex")
        return a

    def _objects(self) -> dict:
        """Design objects by name, built once per refresh: Design.obj() searches the whole list, which
        made restyling hundreds of objects quadratic."""
        objs = {}
        for o in self.doc.design.objects:
            objs.setdefault(o.name, o)       # the first one, as Design.obj() finds it
        return objs

    def _style_obj(self, name: str, objs: dict | None = None) -> None:
        a = self.actors.get(name)
        o = (objs if objs is not None else self._objects()).get(name)
        if a is None or o is None:
            return
        vis = o.visible
        if a.surface is not None:
            prop = a.surface.GetProperty()
            prop.SetColor(*_rgb(o.color))
            op = max(0.0, min(1.0, 1.0 - float(o.transparency)))
            if self.overlay_actors:   # see field overlays through the model, like a transparent view
                op = min(op, 0.2)
            prop.SetOpacity(op)
            prop.SetRepresentationToWireframe() if o.wireframe else prop.SetRepresentationToSurface()
            a.surface.SetVisibility(vis)
            a.surface.SetPickable(vis and o.transparency < 0.95)
            if o.history[0].kind == "CreateRegion":
                a.surface.SetPickable(vis)
        if a.edges is not None:
            a.edges.SetVisibility(vis)
            a.edges.GetProperty().SetColor(*_rgb("#202020"))
            a.edges.GetProperty().SetLineWidth(1)
            a.edges.GetProperty().SetOpacity(0.35 if o.transparency > 0.5 else 1.0)
        if a.vertices is not None:
            a.vertices.SetVisibility(vis and self.doc.selection_mode == "vertex")

    def restyle_all(self) -> None:
        self.refresh_selection(full=True)

    # ------------------------------------------------------------------------------------------
    # selection / assignment highlight
    # ------------------------------------------------------------------------------------------
    def _clear_highlight(self) -> None:
        for act in self.highlight_actors:
            self._drop_actor(act)
        self.highlight_actors = []

    def refresh_selection(self, full: bool = False) -> None:
        """Selection colours. Only the objects that were or are selected are restyled, unless full is set
        or the selection mode changed (vertex markers show or hide everywhere)."""
        self._clear_highlight()
        objs = self._objects()
        mode = self.doc.selection_mode
        sel = {s.obj for s in self.doc.selection}
        if full or mode != self._styled_mode:
            names = list(self.actors)
        else:
            names = [n for n in self._styled_sel | sel if n in self.actors]
        for n in names:
            self._style_obj(n, objs)
        for n in names:
            a = self.actors[n]
            if a.vertices is not None:
                a.vertices.SetVisibility(mode == "vertex")
        self._styled_sel, self._styled_mode = sel, mode
        for s in self.doc.selection:
            a = self.actors.get(s.obj)
            if a is None:
                continue
            hl = self._highlight_color(s.obj)
            if s.index < 0 or self.doc.selection_mode == "object":
                if a.surface is not None:
                    a.surface.GetProperty().SetColor(*_rgb(hl))
                    if s.obj in objs and objs[s.obj].transparency > 0.5:
                        self._add_highlight_mesh(a.mesh, hl, opacity=0.35)
                if a.edges is not None:
                    a.edges.GetProperty().SetColor(*_rgb(SEL_EDGE))
                    a.edges.GetProperty().SetLineWidth(2.5)
            elif self.doc.selection_mode == "face" and a.mesh is not None:
                sub = a.mesh.extract_cells(np.where(a.mesh.cell_data["face"] == s.index)[0])
                self._add_highlight_mesh(sub, hl)
                try:                                  # red outline of the face
                    rim = sub.extract_surface(algorithm="dataset_surface").extract_feature_edges(boundary_edges=True, feature_edges=False,
                                                                      manifold_edges=False, non_manifold_edges=False)
                    if rim.n_cells:
                        act = self._plain_actor(rim, SEL_EDGE, line_width=2.5)
                        act.GetMapper().SetResolveCoincidentTopologyToPolygonOffset()
                        act.GetMapper().SetRelativeCoincidentTopologyLineOffsetParameters(-2, -300)
                        self.highlight_actors.append(act)
                except Exception:  # noqa: BLE001
                    pass
            elif self.doc.selection_mode == "edge" and a.edge_mesh is not None:
                sub = a.edge_mesh.extract_cells(np.where(a.edge_mesh.cell_data["edge"] == s.index)[0])
                self.highlight_actors.append(self._plain_actor(sub, SEL_EDGE, line_width=5))
            elif self.doc.selection_mode == "vertex":
                t = self.doc.tess.get(s.obj)
                if t is not None and s.index < len(t.vertices):
                    self.highlight_actors.append(self._plain_actor(
                        pv.PolyData(np.asarray(t.vertices[s.index], float).reshape(1, 3)), SEL_EDGE,
                        point_size=14, spheres=True))
        self.request_render()

    def _highlight_color(self, name: str) -> str:
        """Selection colour: copper for conductors (as before), #8080ff for everything else."""
        try:
            from ..model.materials import is_conductor
            o = self.doc.design.obj(name)
            return HIGHLIGHT_METAL if is_conductor(self.doc.project.material(o.material)) else HIGHLIGHT
        except Exception:  # noqa: BLE001
            return HIGHLIGHT

    def _add_highlight_mesh(self, mesh, color, opacity=1.0) -> None:
        if mesh is None or mesh.n_cells == 0:
            return
        act = self._plain_actor(mesh, color, opacity=opacity)
        mapper = act.GetMapper()
        mapper.SetResolveCoincidentTopologyToPolygonOffset()
        mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-1.5, -2)
        self.highlight_actors.append(act)

    def show_thicken_arrows(self, names: list[str], reverse: bool = False, both: bool = False) -> None:
        """Arrows on the sheets in names pointing where Thicken sheet grows (both ways with both)."""
        from ..kernel.occ import thicken_direction
        self.clear_thicken_arrows(render=False)
        for name in names:
            a = self.actors.get(name)
            if a is None or a.dim != 2 or a.mesh is None or a.mesh.n_points < 3:
                continue
            P = np.asarray(a.mesh.points, float)
            c = P.mean(axis=0)
            # the normal of the best fitting plane; its sign does not matter, thicken_direction sets it
            n = np.linalg.svd(P - c, full_matrices=False)[2][-1]
            d = thicken_direction(n, reverse)
            size = float(np.linalg.norm(np.ptp(P, axis=0)))
            L = 0.3 * size if np.isfinite(size) and size > 0 else 1.0
            for v in ((d, -d) if both else (d,)):
                arrow = pv.Arrow(start=c, direction=v, scale=L, tip_length=0.25, tip_radius=0.08,
                                 shaft_radius=0.03)
                act = self.plotter.add_mesh(arrow, color="#c2185b", pickable=False, render=False,
                                            reset_camera=False)
                # drawn over the model, so an arrow pointing behind the sheet is not hidden by it
                self.plotter.renderer.RemoveActor(act)
                self._top_layer().AddActor(act)
                self.direction_actors.append(act)
        self.request_render()

    def clear_thicken_arrows(self, render: bool = True) -> None:
        for act in self.direction_actors:
            self._top_layer().RemoveActor(act)
        self.direction_actors = []
        if render:
            self.request_render()

    def _top_layer(self):
        """A renderer on top of the model with the same camera, for helpers that must stay visible."""
        r = getattr(self, "_top_ren", None)
        if r is None:
            rw = self.plotter.render_window
            rw.SetNumberOfLayers(max(2, rw.GetNumberOfLayers()))
            r = vtk.vtkRenderer()
            r.SetLayer(1)
            r.InteractiveOff()
            r.SetViewport(self.plotter.renderer.GetViewport())
            r.SetActiveCamera(self.plotter.renderer.GetActiveCamera())
            rw.AddRenderer(r)
            self._top_ren = r
        return r

    def show_assignments(self, assignments: list, kind_colors: dict | None = None) -> None:
        """Highlight the faces of boundaries/excitations (selected in the project tree)."""
        for act in self.assignment_actors:
            self.plotter.remove_actor(act, render=False)
        self.assignment_actors = []
        self._shown = (list(assignments), kind_colors)
        colors = {**BOUNDARY_COLORS, **EXCITATION_COLORS, **(kind_colors or {})}
        for a in assignments:
            color = colors.get(a.kind, "#ff00ff")
            refs = [(f.obj, f.face) for f in a.faces] + [(o, -1) for o in a.objects]
            for obj, face in refs:
                act = self.actors.get(obj)
                if act is None or act.mesh is None:
                    continue
                mesh = act.mesh if face < 0 else act.mesh.extract_cells(np.where(act.mesh.cell_data["face"] == face)[0])
                if mesh.n_cells == 0:
                    continue
                ac = self.plotter.add_mesh(mesh, color=color, opacity=0.85, pickable=False, render=False,
                                           reset_camera=False)
                m = ac.GetMapper()
                m.SetResolveCoincidentTopologyToPolygonOffset()
                m.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -3)
                self.assignment_actors.append(ac)
            if a.kind == "Incident Plane Wave":
                self._plane_wave_arrows(a)
            # lumped port / RLC integration line
            line = a.props.get("IntegrationLine", "").strip() if hasattr(a, "props") else ""
            if line:
                try:
                    from ..model.objframe import global_points
                    pts = global_points(self.doc.design, a, "IntegrationLine", self.doc.evaluator())
                    if len(pts) == 2:
                        p0, p1 = np.asarray(pts[0]), np.asarray(pts[1])
                        arrow = pv.Arrow(start=p0, direction=p1 - p0, scale=float(np.linalg.norm(p1 - p0)),
                                         tip_length=0.25, tip_radius=0.08, shaft_radius=0.03)
                        self.assignment_actors.append(self.plotter.add_mesh(arrow, color="#000000", pickable=False,
                                                                            render=False, reset_camera=False))
                except Exception:  # noqa: BLE001
                    pass
        self.request_render()

    def _refresh_assignments(self) -> None:
        """Draws the shown boundaries/excitations again on the rebuilt geometry, so faces and integration
        lines follow their moved objects."""
        shown, colors = getattr(self, "_shown", ([], None))
        if not shown:
            return
        des = self.doc.design
        now = []
        for a in shown:
            try:
                now.append(des.assignment(a.name))
            except (KeyError, AttributeError):
                pass                        # deleted meanwhile
        self.show_assignments(now, colors)

    def _plane_wave_arrows(self, a) -> None:
        """draws the incident plane wave as its propagation vector k with the E vector at its tip."""
        from ..model import planewave
        try:
            k, e, origin = planewave.vectors(a.props, self.doc.evaluator())
        except Exception:  # noqa: BLE001
            return
        b = self.plotter.bounds
        size = float(np.linalg.norm([b[1] - b[0], b[3] - b[2], b[5] - b[4]]))
        size = size if np.isfinite(size) and size > 0 else 10.0
        L = 0.3 * size
        # both vectors start at the zero phase position, k (yellow) and Eo (red)
        # both vectors start at the zero phase position, same length and thickness
        tail = origin
        dot = pv.Sphere(radius=0.04 * L, center=origin)
        self.assignment_actors.append(self.plotter.add_mesh(dot, color="#f1c40f", pickable=False, render=False,
                                                            reset_camera=False))
        for d, col in ((k, "#f1c40f"), (e, "#e74c3c")):
            arrow = pv.Arrow(start=origin, direction=d, scale=L, tip_length=0.2, tip_radius=0.06, shaft_radius=0.02)
            self.assignment_actors.append(self.plotter.add_mesh(arrow, color=col, pickable=False, render=False,
                                                                reset_camera=False))
        lab = self.plotter.add_point_labels(np.array([origin + k * L * 1.1, origin + e * L * 1.1]),
                                            ["k", "Eo"], text_color="black", font_size=14, shape=None,
                                            show_points=False, always_visible=True, render=False,
                                            reset_camera=False, name="pw_labels")
        self.assignment_actors.append(lab)

    # ------------------------------------------------------------------------------------------
    # overlays (fields, mesh)
    # ------------------------------------------------------------------------------------------
    def set_overlay(self, name: str, meshes: list[tuple[pv.DataSet, dict]], render: bool = True) -> None:
        self.remove_overlay(name, render=False)
        acts = []
        bars = []
        for mesh, kw in meshes:
            kw = dict(kw)
            sb = kw.get("scalar_bar_args")
            if kw.get("show_scalar_bar", True) and kw.get("scalars") is not None and isinstance(sb, dict) \
                    and sb.get("title"):
                # one colour bar per overlay: pyvista joins mappers with the same title into one bar
                # (and one colour range), so two Mag_E plots would share their scale
                sb = dict(sb, title=f"{name}\n{sb['title']}")
                kw["scalar_bar_args"] = sb
                bars.append(sb["title"])
            kw.setdefault("pickable", False)
            kw.setdefault("render", False)
            kw.setdefault("reset_camera", False)
            act = self.plotter.add_mesh(mesh, **kw)
            try:
                m = act.GetMapper()
                m.SetResolveCoincidentTopologyToPolygonOffset()
                m.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2.5, -4)
                if getattr(mesh, "n_lines", 0) and mesh.n_lines == mesh.n_cells:
                    # mesh lines on top of a plotted surface (solver grid): drawn in front of it
                    m.SetRelativeCoincidentTopologyLineOffsetParameters(-2, -300)
            except Exception:  # noqa: BLE001
                pass
            try:
                act._hzn_opacity = float(act.GetProperty().GetOpacity())
            except Exception:  # noqa: BLE001
                pass
            acts.append(act)
        self.overlay_actors[name] = acts
        self.overlay_bars[name] = bars
        self._apply_overlay_focus()
        self._layout_bars()
        self.restyle_all()
        if render:
            self.request_render()

    def remove_overlay(self, name: str, render: bool = True) -> None:
        had = name in self.overlay_actors
        for act in self.overlay_actors.pop(name, []):
            self.plotter.remove_actor(act, render=False)
        for t in self.overlay_bars.pop(name, []):
            try:
                self.plotter.remove_scalar_bar(t, render=False)
            except Exception:  # noqa: BLE001
                pass
        if had and not self.overlay_actors:
            self.restyle_all()
        if had:
            self._layout_bars()
        if render:
            self.request_render()

    def clear_overlays(self) -> None:
        for n in list(self.overlay_actors):
            self.remove_overlay(n, render=False)
        self.request_render()

    def set_overlay_focus(self, names) -> None:
        """Overlays in names drawn as set up, all others at half their opacity; None: all as set up."""
        self.overlay_focus = set(names) if names else None
        self._apply_overlay_focus()
        self.request_render()

    def _apply_overlay_focus(self) -> None:
        foc = self.overlay_focus
        for name, acts in self.overlay_actors.items():
            f = 1.0 if foc is None or name in foc else 0.5
            for act in acts:
                try:
                    act.GetProperty().SetOpacity(getattr(act, "_hzn_opacity", 1.0) * f)
                except Exception:  # noqa: BLE001
                    pass

    def _layout_bars(self) -> None:
        """Colour bars of several overlays side by side from the right edge."""
        try:
            have = dict(self.plotter.scalar_bars.items())
        except Exception:  # noqa: BLE001
            return
        i = 0
        for name in self.overlay_actors:
            for t in self.overlay_bars.get(name, []):
                bar = have.get(t)
                if bar is None:
                    continue
                try:
                    bar.SetPosition(max(0.0, 0.86 - 0.13 * i), 0.12)   # below the view cube
                except Exception:  # noqa: BLE001
                    pass
                i += 1

    # ------------------------------------------------------------------------------------------
    # view
    # ------------------------------------------------------------------------------------------
    EMPTY_HALF = 0.05          # half size (m) of the area shown while the model is empty

    def view_bbox(self):
        """Model bounding box, or the 10 cm x 10 cm working area of an empty model."""
        if not self.doc.tess:
            h = self.EMPTY_HALF
            return (-h, -h, -h, h, h, h)       # any drawing plane gets the full 10 cm x 10 cm
        return self.doc.model_bbox()

    def fit_all(self) -> None:
        if not self.doc.tess:
            b = self.view_bbox()
            if self.grid_actor is None:
                self.update_grid()
            n = PLANE_NORMAL[self.grid_plane]
            lo, hi = list(b[:3]), list(b[3:])
            lo[n] = hi[n] = 0.0
            self.plotter.reset_camera(bounds=(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]), render=False)
        else:
            self.plotter.reset_camera(render=False)
        self.request_render()

    def fit_selection(self) -> None:
        objs = self.doc.selected_objects()
        if not objs:
            return self.fit_all()
        boxes = [self.doc.tess[o].bbox for o in objs if o in self.doc.tess]
        if boxes:
            b = np.array(boxes)
            self.plotter.reset_camera(bounds=(b[:, 0].min(), b[:, 3].max(), b[:, 1].min(), b[:, 4].max(),
                                              b[:, 2].min(), b[:, 5].max()), render=False)
            self.request_render()

    def view(self, which: str) -> None:
        p = self.plotter
        {"iso": p.view_isometric, "top": p.view_xy, "bottom": lambda: p.view_xy(negative=True),
         "front": p.view_xz, "back": lambda: p.view_xz(negative=True), "right": p.view_yz,
         "left": lambda: p.view_yz(negative=True)}[which]()
        self.request_render()

    def screenshot(self, path: str) -> None:
        self.plotter.render()           # pending render requests are not drawn yet
        self.plotter.screenshot(path)

    def is_orthographic(self) -> bool:
        return bool(self.plotter.camera.GetParallelProjection())

    def set_orthographic(self, on: bool) -> None:
        """Orthographic (parallel) or perspective projection; the model keeps its size on screen."""
        cam = self.plotter.camera
        if bool(cam.GetParallelProjection()) != on:
            if on:
                d = float(np.linalg.norm(np.array(cam.GetFocalPoint()) - np.array(cam.GetPosition())))
                cam.SetParallelScale(max(d, 1e-12) * math.tan(math.radians(cam.GetViewAngle() / 2)))
            cam.SetParallelProjection(on)
        if self.on_projection is not None:
            self.on_projection(on)
        self.request_render()

    # ------------------------------------------------------------------------------------------
    # grid / working plane
    # ------------------------------------------------------------------------------------------
    def _grid_bounds(self) -> list[float]:
        """Model bounding box in the working CS, widened to take in the CS origin: the grid always reaches the
        origin, also when the model lies far away from it."""
        b = self.view_bbox()
        Ow, Rw = self.wcs
        if np.linalg.norm(Ow) > 0 or not np.allclose(Rw, np.eye(3)):
            C = np.array([[b[i] if (k >> j) & 1 == 0 else b[i + 3] for j, i in enumerate(range(3))]
                          for k in range(8)])
            L = (C - Ow) @ Rw
            b = list(L.min(axis=0)) + list(L.max(axis=0))
        return [min(b[i], 0.0) for i in range(3)] + [max(b[i + 3], 0.0) for i in range(3)]

    GRID_MAX_LINES = 200       # per direction; a far origin coarsens the automatic spacing

    def auto_spacing(self) -> float:
        if self.grid_spacing:
            return self.grid_spacing
        b = self.view_bbox()
        size = max(b[3] - b[0], b[4] - b[1], b[5] - b[2], 1e-3)
        g = self._grid_bounds()
        extent = max(g[3] - g[0], g[4] - g[1], g[5] - g[2])
        raw = max(size / 20, extent / self.GRID_MAX_LINES)
        e = 10 ** math.floor(math.log10(raw))
        for m in (1, 2, 5, 10):
            if raw <= m * e:
                return m * e
        return 10 * e

    def update_grid(self) -> None:
        if self.grid_actor is not None:
            self.plotter.remove_actor(self.grid_actor, render=False)
            self.grid_actor = None
        if not self.show_grid:
            return
        s = self.auto_spacing()
        b = self._grid_bounds()
        Ow, Rw = self.wcs
        n = PLANE_NORMAL[self.grid_plane]
        axes = [i for i in range(3) if i != n]
        while max(b[i + 3] - b[i] for i in range(3)) / s > 10 * self.GRID_MAX_LINES:
            s *= 10                 # a fine fixed spacing over a far origin: draw every 10th line only
        pad = 0 if not self.doc.tess else 5 * s
        lo = [math.floor((b[i] - pad) / s + 1e-9) * s for i in range(3)]
        hi = [math.ceil((b[i + 3] + pad) / s - 1e-9) * s for i in range(3)]
        pts, lines = [], []
        a0, a1 = axes
        k = 0
        for v in np.arange(lo[a0], hi[a0] + s / 2, s):
            p0 = [0.0, 0.0, 0.0]
            p1 = [0.0, 0.0, 0.0]
            p0[a0] = p1[a0] = v
            p0[a1], p1[a1] = lo[a1], hi[a1]
            pts += [p0, p1]
            lines += [2, k, k + 1]
            k += 2
        for v in np.arange(lo[a1], hi[a1] + s / 2, s):
            p0 = [0.0, 0.0, 0.0]
            p1 = [0.0, 0.0, 0.0]
            p0[a1] = p1[a1] = v
            p0[a0], p1[a0] = lo[a0], hi[a0]
            pts += [p0, p1]
            lines += [2, k, k + 1]
            k += 2
        if not pts:
            return
        g = pv.PolyData(np.array(pts, float), lines=np.array(lines))
        self.grid_actor = self.plotter.add_mesh(g, color="#a3abb9", opacity=0.9, line_width=1, pickable=False,
                                                render=False, reset_camera=False, name="grid")
        self.grid_actor.GetProperty().SetLighting(False)     # unlit: same tone at every view angle
        if np.linalg.norm(Ow) > 0 or not np.allclose(Rw, np.eye(3)):
            self.grid_actor.SetUserMatrix(_vtk_matrix(Ow, Rw))

    # ------------------------------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------------------------------
    _identity = (np.zeros(3), np.eye(3))

    def set_nav_mode(self, mode: str) -> None:
        """Rotate / Pan button: the left mouse rotates or pans until Escape or the same button again."""
        mode = "" if mode == self.nav_mode else mode
        if mode and self.draw is not None:
            self.cancel_draw()
        self.reset_interaction()
        self.nav_mode = mode
        if mode:
            self.plotter.interactor.setCursor(Qt.SizeAllCursor if mode == "pan" else Qt.OpenHandCursor)
            self._clear_hover()
        else:
            self.plotter.interactor.unsetCursor()
        self.navModeChanged.emit(mode)

    def start_draw(self, kind: str) -> None:
        if self.nav_mode:
            self.set_nav_mode("")
        self.cancel_draw()
        # primitives are drawn in the working CS; measuring and move/mirror stay global
        local = not kind.startswith("Measure") and kind not in FREE_KINDS
        self._frame = self.wcs if local else self._identity
        self.draw = DrawState(kind, DRAW_PHASES.get(kind, 2))
        self.plotter.interactor.setCursor(Qt.CrossCursor)
        self.drawPhaseChanged.emit(self._phase_hint())

    def cancel_draw(self) -> None:
        if self.draw is not None and self.draw.preview is not None:
            self.plotter.remove_actor(self.draw.preview, render=False)
        self.draw = None
        self.axis_lock = None
        self._show_dimensions(None, None)
        self._show_snap_marker(None, "")
        self.plotter.interactor.unsetCursor()
        self.drawPhaseChanged.emit("")
        self.request_render()

    def _phase_hint(self) -> str:
        if self.draw is None:
            return ""
        k, n = self.draw.kind, len(self.draw.points)
        hints = {
            "CreateBox": ["Enter the first corner", "Enter the opposite corner of the base", "Enter the height"],
            "CreateCylinder": ["Enter the center", "Enter the radius", "Enter the height"],
            "CreateCone": ["Enter the center", "Enter the lower radius", "Enter the upper radius", "Enter the height"],
            "CreateSphere": ["Enter the center", "Enter the radius"],
            "CreateTorus": ["Enter the center", "Enter the major radius", "Enter the minor radius"],
            "CreateRegularPolyhedron": ["Enter the center", "Enter the start point", "Enter the height"],
            "CreateRectangle": ["Enter the first corner", "Enter the opposite corner"],
            "CreateCircle": ["Enter the center", "Enter the radius"],
            "CreateEllipse": ["Enter the center", "Enter the major radius", "Enter the minor radius"],
            "CreateRegularPolygon": ["Enter the center", "Enter the start point"],
            "Measure": ["Enter the first point", "Enter the second point"],
            "MeasurePosition": ["Enter the point"],
            "OpMove": ["Enter the reference point", "Enter the target point"],
            "OpDupLine": ["Enter the first point of the duplicate vector", "Enter the second point"],
            "OpMirror": ["Enter a point on the mirror plane", "Enter a point along the plane normal"],
            "OpDupMirror": ["Enter a point on the mirror plane", "Enter a point along the plane normal"],
            "CSOffset": ["Enter the origin of the new coordinate system"],
            "CSRotated": ["Enter a point on the new X axis", "Enter a point in the new XY plane"],
            "CSBoth": ["Enter the origin", "Enter a point on the new X axis", "Enter a point in the new XY plane"],
            "IntegrationLine": ["Enter the start point of the integration line (snaps to vertices and edge centers)",
                                "Enter the end point of the integration line"],
            "PlaceComponent": ["Click the centre of the component on the board surface (snaps to surfaces, "
                               "vertices and edge centers)"],
            "MeasureAngle": ["Enter a point on the first leg", "Enter the vertex of the angle",
                             "Enter a point on the second leg"],
        }.get(k)
        lock = f"  [locked to {'XYZ'[self.axis_lock]} axis, press {'XYZ'[self.axis_lock]} to release]" \
            if self.axis_lock is not None else ("  (X/Y/Z: lock axis, Tab: type coordinates)" if n else
                                                 "  (Tab: type coordinates)")
        if k == "CreatePolyline":
            seg = SEG_NAMES[self.draw.seg_mode]
            if self.draw.seg_mode == "CenterArc" and n:
                if _center_pending(self.draw):
                    return (f"[{seg}] Enter the end angle ({math.degrees(self.draw.sweep):.1f} deg; move the cursor"
                            " around the center to sweep)") + lock
                return f"[{seg}] Enter the center of the arc" + lock
            return (f"[{seg}] Enter a point (right click or Enter to finish, click the first point to close;"
                    " L/A/C/S: straight, 3 point arc, center point arc, spline)") + lock
        return (hints[n] if hints and n < len(hints) else "") + lock

    def _world_ray(self, pos):
        ren = self.plotter.renderer
        ren.SetDisplayPoint(pos[0], pos[1], 0.0)
        ren.DisplayToWorld()
        w0 = np.array(ren.GetWorldPoint()[:3]) / (ren.GetWorldPoint()[3] or 1)
        ren.SetDisplayPoint(pos[0], pos[1], 1.0)
        ren.DisplayToWorld()
        w1 = np.array(ren.GetWorldPoint()[:3]) / (ren.GetWorldPoint()[3] or 1)
        return w0, w1 - w0

    def _display_coords(self, X: np.ndarray) -> np.ndarray:
        """World points (N,3) to display pixels (N,2) and a mask of points in front of the camera."""
        ren = self.plotter.renderer
        cam = ren.GetActiveCamera()
        M = cam.GetCompositeProjectionTransformMatrix(ren.GetTiledAspectRatio(), -1, 1)
        A = np.array([[M.GetElement(i, j) for j in range(4)] for i in range(4)])
        H = np.c_[X, np.ones(len(X))] @ A.T
        w = H[:, 3]
        ok = w > 1e-30
        w = np.where(ok, w, 1.0)
        nx, ny = H[:, 0] / w, H[:, 1] / w
        vp = ren.GetViewport()
        W, Hh = ren.GetRenderWindow().GetSize()
        x0, y0 = vp[0] * W, vp[1] * Hh
        sx, sy = (vp[2] - vp[0]) * W, (vp[3] - vp[1]) * Hh
        return np.c_[x0 + (nx + 1) * sx / 2, y0 + (ny + 1) * sy / 2], ok

    def _candidates(self):
        pts, kinds = [], []
        for name, t in self.doc.tess.items():
            a = self.actors.get(name)
            if a is not None and getattr(a, "surface", None) is not None and not a.surface.GetVisibility() \
                    and (getattr(a, "edges", None) is None or not a.edges.GetVisibility()):
                continue
            c = self._snap_cache.get(id(t))
            if c is None or c[0] is not t:
                try:
                    c = (t, snap_candidates(t))
                except Exception:  # noqa: BLE001
                    c = (t, {})
                self._snap_cache[id(t)] = c
            for k, P in c[1].items():
                if k in self.snap_modes and len(P):
                    pts.append(P)
                    kinds += [k] * len(P)
        if len(self._snap_cache) > 4 * max(len(self.doc.tess), 1):
            live = {id(t) for t in self.doc.tess.values()}
            self._snap_cache = {k: v for k, v in self._snap_cache.items() if k in live}
        if not pts:
            return np.zeros((0, 3)), []
        return np.vstack(pts), kinds

    def snap_geometry(self, pos) -> tuple[np.ndarray | None, str]:
        """Nearest object snap point (vertex, edge center, face center, quadrant, arc center) within the
        snap tolerance in pixels."""
        X, kinds = self._candidates()
        if not len(X):
            return None, ""
        D, ok = self._display_coords(X)
        d = np.hypot(D[:, 0] - pos[0], D[:, 1] - pos[1])
        d[~ok] = np.inf
        score = d + np.array([SNAP_PRIORITY.get(k, 5.0) for k in kinds])
        score[d > self.snap_tolerance] = np.inf
        i = int(np.argmin(score))
        if not np.isfinite(score[i]):
            return None, ""
        return X[i].copy(), kinds[i]

    def _surface_point(self, pos) -> np.ndarray | None:
        """3D movement mode: the point on the visible object surface under the cursor."""
        ren = self.plotter.renderer
        pk = getattr(self, "_surf_picker", None)
        if pk is None:
            pk = self._surf_picker = vtk.vtkCellPicker()
            pk.SetTolerance(0.0005)
            pk.PickFromListOn()
        pk.InitializePickList()
        n = 0
        for a in self.actors.values():
            if a.surface is not None and a.surface.GetVisibility() and a.surface.GetPickable():
                pk.AddPickList(a.surface)
                n += 1
        if not n or not pk.Pick(pos[0], pos[1], 0, ren) or pk.GetActor() is None:
            return None
        return np.array(pk.GetPickPosition(), float)

    def _show_dimensions(self, p, pos) -> None:
        """dx / dy / dz / length next to the cursor while drawing."""
        ren = self.plotter.renderer
        d = self.draw
        if p is None or d is None or not d.points or pos is None:
            if self._dim_actor is not None:
                self._dim_actor.SetVisibility(False)
            return
        if self._dim_actor is None:
            t = vtk.vtkTextActor()
            tp = t.GetTextProperty()
            tp.SetFontSize(13)
            tp.SetColor(0.1, 0.1, 0.1)
            tp.SetBackgroundColor(1.0, 1.0, 0.85)
            tp.SetBackgroundOpacity(0.9)
            tp.SetFrame(True)
            tp.SetFrameColor(0.5, 0.5, 0.5)
            t.PickableOff()
            ren.AddViewProp(t)
            self._dim_actor = t
        u, sc = self.length_unit
        dv = (np.asarray(p, float) - d.points[-1]) / sc
        L = float(np.linalg.norm(dv))
        self._dim_actor.SetInput(f"dX {dv[0]:.4g}  dY {dv[1]:.4g}  dZ {dv[2]:.4g}\nLength {L:.4g} {u}")
        self._dim_actor.SetPosition(pos[0] + 18, pos[1] - 40)
        self._dim_actor.SetVisibility(True)

    def _snap_plane(self, p: np.ndarray, n: int) -> np.ndarray:
        if "grid" in self.snap_modes:
            s = self.auto_spacing()
            q = np.round(p / s) * s
            q[n] = p[n]
            return q
        return p

    def _show_snap_marker(self, p: np.ndarray | None, kind: str) -> None:
        ren = self.plotter.renderer
        if self._snap_marker is None and (p is None or not kind):
            return
        if self._snap_marker is None:
            src = vtk.vtkGlyphSource2D()
            src.SetScale(13)
            src.FilledOff()
            mapper = vtk.vtkPolyDataMapper2D()
            mapper.SetInputConnection(src.GetOutputPort())
            mapper.ScalarVisibilityOff()
            act = vtk.vtkActor2D()
            act.SetMapper(mapper)
            act.GetProperty().SetLineWidth(2.5)
            act.PickableOff()
            ren.AddViewProp(act)
            self._snap_marker = (src, act)
        src, act = self._snap_marker
        if p is None or not kind:
            act.SetVisibility(False)
            return
        glyph = {"grid": "SetGlyphTypeToCross", "vertex": "SetGlyphTypeToSquare",
                 "edge_center": "SetGlyphTypeToTriangle", "face_center": "SetGlyphTypeToCircle",
                 "quadrant": "SetGlyphTypeToDiamond", "arc_center": "SetGlyphTypeToCircle"}[kind]
        getattr(src, glyph)()
        src.SetCross(kind == "arc_center")
        src.SetScale(11 if kind == "grid" else 17)
        color = (0.1, 0.35, 0.85) if kind == "grid" else (0.9, 0.1, 0.1)
        act.GetProperty().SetColor(*color)
        O, R = self._frame
        D, _ = self._display_coords((O + R @ np.asarray(p, float)).reshape(1, 3))
        act.SetPosition(float(D[0, 0]), float(D[0, 1]))
        act.SetVisibility(True)

    def _draw_point(self, pos) -> np.ndarray | None:
        """Working point under the cursor. Object snaps keep their full 3D position for the first point
        (the sheet or base lies in the plane through it); later in-plane points are projected
        into the plane through the first point. Height points move along the plane normal."""
        d = self.draw
        if d is None:
            return None
        O, R = self._frame
        o, v = self._world_ray(pos)
        o, v = R.T @ (o - O), R.T @ v            # draw in the working coordinate system
        n = PLANE_NORMAL[self.grid_plane]
        phase = len(d.points)
        geo, kind = self.snap_geometry(pos)
        if geo is not None:
            geo = R.T @ (geo - O)
        if self.axis_lock is not None and d.points:
            base = d.points[-1]
            axis = np.zeros(3)
            axis[self.axis_lock] = 1.0
            p = base.copy()
            if geo is not None:
                p[self.axis_lock] = geo[self.axis_lock]
            else:
                w = base - o
                a, b, c = axis @ axis, axis @ v, v @ v
                dd, e = axis @ w, v @ w
                den = a * c - b * b
                p = base + axis * ((b * e - c * dd) / den if abs(den) > 1e-30 else 0.0)
                if "grid" in self.snap_modes:
                    sp = self.auto_spacing()
                    p[self.axis_lock] = round(p[self.axis_lock] / sp) * sp
                kind = "grid" if "grid" in self.snap_modes else ""
            self.snap_kind = kind
            return p
        out_of_plane = self.movement_mode == "Out of Plane" and d.points
        if (phase in HEIGHT_PHASE.get(d.kind, set()) or out_of_plane) and d.points:
            base = d.points[-1]
            axis = np.zeros(3)
            axis[n] = 1.0
            p = base.copy()
            if geo is not None:
                p[n] = geo[n]
            else:
                w = base - o
                a, b, c = axis @ axis, axis @ v, v @ v
                dd, e = axis @ w, v @ w
                den = a * c - b * b
                t = (b * e - c * dd) / den if abs(den) > 1e-30 else 0.0
                p = base + axis * t
                if "grid" in self.snap_modes:
                    s = self.auto_spacing()
                    p[n] = round(p[n] / s) * s
                kind = "grid" if "grid" in self.snap_modes else ""
            self.snap_kind = kind
            return p
        if geo is None and (self.movement_mode == "3D" or d.kind == "PlaceComponent"):
            hit = self._surface_point(pos)
            if hit is not None:
                hit = R.T @ (hit - O)
                geo, kind = self._snap_plane(hit, n), ("grid" if "grid" in self.snap_modes else "")
        if geo is not None:
            p = geo
            if d.points and not d.kind.startswith("Measure") and d.kind not in FREE_KINDS:
                p[n] = d.points[0][n]
            self.snap_kind = kind
            return p
        offset = d.points[0][n] if d.points else 0.0
        if abs(v[n]) < 1e-15:
            return None
        t = (offset - o[n]) / v[n]
        p = self._snap_plane(o + t * v, n)
        p[n] = offset
        self.snap_kind = "grid" if "grid" in self.snap_modes else ""
        return p

    def _draw_click(self, pos) -> None:
        d = self.draw
        p = self._draw_point(pos)
        if p is None:
            return
        self.add_draw_point(p)

    def add_draw_point(self, p: np.ndarray) -> None:
        d = self.draw
        if d is None:
            return
        if d.kind == "CreatePolyline" and d.points and d.seg_mode == "CenterArc":
            # center point arc: first the center, then the end angle (the end point lies on the circle)
            p = np.asarray(p, float)
            if not _center_pending(d):
                if np.allclose(p, d.points[-1]):
                    return
                d.modes.append("CenterArc")
                d.points.append(p)
                d.sweep = d.sweep_ref = 0.0
            else:
                self._track_sweep(p)
                if abs(d.sweep) < 1e-9:
                    return
                d.modes.append(f"CenterArc:{math.degrees(d.sweep):.10g}")
                d.points.append(angular_arc(d.points[-2], d.points[-1], d.sweep, self._arc_plane())[-1])
                if np.allclose(d.points[-1], d.points[0]):
                    self.finish_polyline(closed=True)
                    return
            self.axis_lock = None
            if len(d.points) >= d.phases:
                self.finish_polyline()
                return
            self.drawPhaseChanged.emit(self._phase_hint())
            self._update_preview()
            return
        if d.kind == "CreatePolyline" and len(d.points) >= 2 and np.allclose(p, d.points[0]):
            d.points.append(np.asarray(p, float))
            d.modes.append(d.seg_mode)
            self.finish_polyline(closed=True)
            return
        if d.points and d.kind == "CreatePolyline":
            d.modes.append(d.seg_mode)
        d.points.append(np.asarray(p, float))
        self.axis_lock = None
        if d.kind == "CreatePolyline" and len(d.points) >= d.phases:
            self.finish_polyline()      # Draw > Line / Draw > Arc: done after one segment
            return
        if len(d.points) >= d.phases:
            pts = d.points
            kind = d.kind
            self.cancel_draw()
            self.drawFinished.emit(kind, pts)
            return
        self.drawPhaseChanged.emit(self._phase_hint())
        self._update_preview()

    def finish_polyline(self, closed: bool = False) -> None:
        d = self.draw
        if d is None:
            return
        pts = list(d.points)
        modes = list(d.modes)
        if _center_pending(d):      # an arc without its end angle: drop the center
            pts, modes = pts[:-1], modes[:-1]
        if closed and len(pts) > 1 and np.allclose(pts[-1], pts[0]):
            pts = pts[:-1]
            if modes and modes[-1] != "Line":
                # the closing segment is curved: keep the first point as explicit end point
                pts = pts + [pts[0]]
            else:
                modes = modes[:-1]
        self.last_segment_types = segment_spec(modes[:max(0, len(pts) - 1)], self._arc_plane())
        self.cancel_draw()
        if len(pts) >= 2:
            self.drawFinished.emit("CreatePolyline" + (":closed" if closed else ""), pts)

    def _update_preview(self) -> None:
        d = self.draw
        if d is None:
            return
        if d.preview is not None:
            self.plotter.remove_actor(d.preview, render=False)
            d.preview = None
        pts = list(d.points) + ([d.current] if d.current is not None else [])
        modes = None
        if d.kind == "CreatePolyline":
            nxt = d.seg_mode
            if _center_pending(d) and d.current is not None:
                self._track_sweep(d.current)
                nxt = f"CenterArc:{math.degrees(d.sweep):.10g}"
                self.drawPhaseChanged.emit(self._phase_hint())
            modes = d.modes + [nxt]
        mesh = preview_mesh(d.kind, pts, PLANE_NORMAL[self.grid_plane], modes)
        if d.kind in OP_KINDS and len(pts) >= 2:
            mesh = self._ghost_mesh(d, np.asarray(pts[0], float), np.asarray(pts[1], float), mesh)
        if mesh is not None:
            d.preview = self.plotter.add_mesh(mesh, color="#ff4fa0", opacity=0.45, line_width=2, pickable=False,
                                              render=False, reset_camera=False, show_edges=False)
            if self._frame is not self._identity:
                d.preview.SetUserMatrix(_vtk_matrix(*self._frame))
        self.request_render()

    def _arc_plane(self) -> str:
        """Plane of center point arcs: the grid plane (ArcPlane)."""
        return ARC_PLANE[PLANE_NORMAL[self.grid_plane]]

    def _track_sweep(self, p) -> None:
        """Center point arc: add the cursor's turn about the center since the last call to the swept angle,
        so the arc follows the mouse past 180 degrees and in both directions."""
        d = self.draw
        a = _plane_angle(d.points[-2], d.points[-1], p, PLANE_NORMAL[self.grid_plane])
        if a is None:
            return
        d.sweep += (a - d.sweep_ref + math.pi) % (2 * math.pi) - math.pi
        d.sweep_ref = a

    def _ghost_mesh(self, d, a: np.ndarray, b: np.ndarray, line):
        """Selected objects translated by b-a, or mirrored at the plane through a with normal b-a."""
        parts = [line] if line is not None else []
        v = b - a
        if np.linalg.norm(v) < 1e-15:
            return line
        for name in d.ghost:
            act = self.actors.get(name)
            if act is None or act.mesh is None:
                continue
            m = act.mesh.copy()
            if d.kind in ("OpMove", "OpDupLine"):
                m.points = m.points + v
            else:
                m = m.reflect(v / np.linalg.norm(v), point=a)
            parts.append(m.extract_surface() if not isinstance(m, pv.PolyData) else m)
        if not parts:
            return None
        out = parts[0]
        for q in parts[1:]:
            out = out.merge(q) if hasattr(out, "merge") else out
        return out


SEG_KEYS = {"l": "Line", "a": "Arc", "c": "CenterArc", "s": "Spline"}
SEG_NAMES = {"Line": "Straight", "Arc": "3 Point Arc", "CenterArc": "Center Point Arc", "Spline": "Spline"}
ARC_PLANE = {2: "XY", 0: "YZ", 1: "ZX"}      # plane normal index -> ArcPlane


def _center_pending(d) -> bool:
    """Center point arc: the center is placed, the end angle not yet."""
    return d is not None and bool(d.modes) and d.modes[-1] == "CenterArc"


def _plane_angle(start, center, p, n: int):
    """Angle of p about center, from the direction of start, counter-clockwise about axis n; None at the center."""
    i, j = (n + 1) % 3, (n + 2) % 3
    u = np.asarray(start, float) - np.asarray(center, float)
    v = np.asarray(p, float) - np.asarray(center, float)
    if np.hypot(v[i], v[j]) < 1e-15 or np.hypot(u[i], u[j]) < 1e-15:
        return None
    return math.atan2(u[i] * v[j] - u[j] * v[i], u[i] * v[i] + u[j] * v[j])


def segment_spec(modes: list[str], plane: str = "XY") -> str:
    """Per-point segment types (points 1..n-1) -> CreatePolyline SegmentTypes ('Line,Arc,Spline:4').

    A center point arc is "CenterArc" (center) followed by "CenterArc:<deg>" (end) -> 'AngularArc:<deg>:<plane>'."""
    out, i, n = [], 0, len(modes)
    while i < n:
        m = modes[i]
        if m == "CenterArc" and i + 1 < n and modes[i + 1].startswith("CenterArc:"):
            out.append(f"AngularArc:{modes[i + 1].split(':', 1)[1]}:{plane}")
            i += 2
        elif m == "Arc" and i + 1 < n and modes[i + 1] == "Arc":
            out.append("Arc")
            i += 2
        elif m == "Spline":
            j = i
            while j < n and modes[j] == "Spline":
                j += 1
            out.append(f"Spline:{j - i + 1}" if j - i >= 2 else "Line")
            i = j
        else:
            out.append("Line")
            i += 1
    return ",".join(out) if any(x != "Line" for x in out) else ""


def _arc3(a, b, c, n=24):
    a, b, c = (np.asarray(x, float) for x in (a, b, c))
    u, v = b - a, c - a
    w = np.cross(u, v)
    ww = w @ w
    if ww < 1e-24 * max(u @ u, 1e-30) ** 2:
        return np.array([a, c])
    ctr = a + (np.cross(w, u) * (v @ v) + np.cross(v, w) * (u @ u)) / (2 * ww)
    r = np.linalg.norm(a - ctr)
    e1 = (a - ctr) / r
    e2 = np.cross(w / np.sqrt(ww), e1)
    ang = lambda p: np.arctan2((p - ctr) @ e2, (p - ctr) @ e1) % (2 * np.pi)
    tb, tc = ang(b), ang(c)
    if tb > tc:  # going the other way round
        tc = tc - 2 * np.pi
    t = np.linspace(0, tc, n)
    return ctr + r * (np.cos(t)[:, None] * e1 + np.sin(t)[:, None] * e2)


def _spline(P, n=12):
    P = np.asarray(P, float)
    Q = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = [P[0]]
    for i in range(1, len(Q) - 2):
        p0, p1, p2, p3 = Q[i - 1], Q[i], Q[i + 1], Q[i + 2]
        for t in np.linspace(0, 1, n)[1:]:
            out.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    return np.array(out)


def densify(P: list, modes: list[str], plane: str = "XY") -> np.ndarray:
    """Polyline points with arcs and splines expanded for display."""
    P = [np.asarray(p, float) for p in P]
    spec = segment_spec(modes[:len(P) - 1], plane)
    out, i = [P[0]], 0
    for tok in (spec.split(",") if spec else []):
        if tok.startswith("AngularArc"):
            ang = math.radians(float(tok.split(":")[1]))
            out += list(angular_arc(P[i], P[i + 1], ang, plane, max(2, int(abs(ang) / 0.1) + 2))[1:])
            i += 2
        elif tok == "Arc":
            out += list(_arc3(P[i], P[i + 1], P[i + 2])[1:])
            i += 2
        elif tok.startswith("Spline"):
            k = int(tok.split(":")[1])
            out += list(_spline(P[i:i + k])[1:])
            i += k - 1
        else:
            out.append(P[i + 1])
            i += 1
    out += P[i + 1:]
    return np.array(out)


def preview_mesh(kind: str, pts: list, n: int, modes: list | None = None):
    pts = [p for p in pts if p is not None]
    if not pts:
        return None
    P = [np.asarray(p, float) for p in pts]
    axis = np.zeros(3)
    axis[n] = 1
    try:
        if len(P) == 1:
            return pv.PolyData(P[0].reshape(1, 3))
        if kind in ("CreateBox", "CreateRectangle"):
            a, b = P[0], P[1]
            lo, hi = np.minimum(a, b), np.maximum(a, b)
            if kind == "CreateBox" and len(P) >= 3:
                h = P[2][n] - P[1][n]
                lo[n], hi[n] = min(a[n], a[n] + h), max(a[n], a[n] + h)
            if np.any(hi - lo <= 0) and kind == "CreateBox":
                hi = hi + (hi == lo) * 1e-9
            return pv.Box(bounds=(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))
        if kind in ("CreateCylinder", "CreateCircle", "CreateCone", "CreateRegularPolyhedron", "CreateRegularPolygon",
                    "CreateSphere", "CreateEllipse", "CreateTorus"):
            c = P[0]
            r = float(np.linalg.norm(P[1] - c)) or 1e-9
            if kind == "CreateSphere":
                return pv.Sphere(radius=r, center=c)
            if kind in ("CreateCylinder", "CreateRegularPolyhedron") and len(P) >= 3:
                h = P[2][n] - c[n]
                res = 6 if kind == "CreateRegularPolyhedron" else 48
                return pv.Cylinder(center=c + axis * h / 2, direction=axis, radius=r, height=abs(h) or 1e-9,
                                   resolution=res)
            if kind == "CreateCone" and len(P) >= 3:
                r2 = float(np.linalg.norm(P[2] - c))
                if len(P) >= 4:
                    h = P[3][n] - c[n]
                    return pv.Cylinder(center=c + axis * h / 2, direction=axis, radius=max(r, r2),
                                       height=abs(h) or 1e-9, resolution=48)
                return pv.Disc(center=c, inner=min(r, r2), outer=max(r, r2), normal=axis, c_res=48)
            res = 6 if kind == "CreateRegularPolygon" else 64
            return pv.Disc(center=c, inner=0, outer=r, normal=axis, c_res=res)
        if kind == "CreatePolyline" and modes:
            return pv.lines_from_points(densify(P, modes, ARC_PLANE[n]))
        if kind == "CSRotated":
            P = [np.zeros(3)] + list(P)
        if kind in ("CreatePolyline", "Measure", "MeasureAngle", "CSRotated", "CSBoth", "IntegrationLine") \
                or kind in OP_KINDS:
            line = pv.lines_from_points(np.array(P))
            return line
    except Exception:  # noqa: BLE001
        return None
    return None
