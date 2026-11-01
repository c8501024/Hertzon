"""Main window: menus, toolbars, docks and all user actions."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, QSettings, QEventLoop
from PySide6.QtGui import QAction, QKeySequence, QActionGroup
from PySide6.QtWidgets import (QMainWindow, QDockWidget, QTabWidget, QSplitter, QMenu, QFileDialog, QMessageBox,
                               QLabel, QLineEdit, QComboBox, QInputDialog, QDialog, QFormLayout, QDialogButtonBox,
                               QListWidget, QAbstractItemView, QVBoxLayout,
                               QSpinBox, QDoubleSpinBox, QApplication, QWidget, QTableWidget,
                               QTableWidgetItem, QHeaderView)

from .. import __version__
from ..model import groups as G
from ..model import schema
from ..model.design import (Project, Design, Command, Assignment, FaceRef, Setup, Sweep, InfiniteSphere, Report,
                            FieldPlot, design_from_dict)
from ..model.expr import LENGTH_UNITS
from ..model.materials import is_conductor, normalize_color
from ..solver.results import results_dir, ResultStore
from .doc import Document, Pick, material_targets, result_dir
from .viewer import ModelerView, PLANE_NORMAL
from .trees import ProjectTree, ModelTree
from .panels import PropertiesPanel, MessageManager, ProgressPanel, SolutionDataDialog, rename_object
from .dialogs import (edit_assignment, edit_setup, edit_sweep, edit_sphere, ObjectPropertiesDialog, VariablesDialog,
                      MaterialsDialog, SolutionTypeDialog, ValidationDialog, GridDialog, NamedPropsDialog,
                      SnapModeDialog, SubtractDialog)
from .solve import SolveManager, Job
from .reports import ReportWindow, solution_list
from . import fields as F

def _point_triangle_dist(P, A, B, C):
    """Distance of point P to each triangle (A, B, C arrays of shape (n,3))."""
    ab, ac, ap = B - A, C - A, P - A
    n = np.cross(ab, ac)
    nn = np.einsum("ij,ij->i", n, n)
    nn = np.where(nn > 0, nn, 1e-300)
    d_plane = np.einsum("ij,ij->i", ap, n) / np.sqrt(nn)
    Q = P - (np.einsum("ij,ij->i", ap, n) / nn)[:, None] * n
    # barycentric inside test
    v0, v1, v2 = ac, ab, Q - A
    d00, d01, d11 = (np.einsum("ij,ij->i", v0, v0), np.einsum("ij,ij->i", v0, v1), np.einsum("ij,ij->i", v1, v1))
    d20, d21 = np.einsum("ij,ij->i", v2, v0), np.einsum("ij,ij->i", v2, v1)
    den = np.where(np.abs(d00 * d11 - d01 * d01) > 0, d00 * d11 - d01 * d01, 1e-300)
    u = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    inside = (u >= -1e-9) & (w >= -1e-9) & (u + w <= 1 + 1e-9)
    out = np.where(inside, np.abs(d_plane), np.inf)
    # outside: distance to the edges
    for S, E in ((A, B), (B, C), (C, A)):
        d = E - S
        L2 = np.einsum("ij,ij->i", d, d)
        t = np.clip(np.einsum("ij,ij->i", P - S, d) / np.where(L2 > 0, L2, 1), 0, 1)
        out = np.minimum(out, np.linalg.norm(S + d * t[:, None] - P, axis=1))
    return out


AXIS_OF_PLANE = {"XY": "Z", "YZ": "X", "XZ": "Y", "ZX": "Y"}
FILE_FILTER = "Hertzon project (*.hzn)"
EQ_FILTER = "Hertzon equations (*.json)"
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"   # next to the package in the repo and installs


def example_projects(folder: Path = EXAMPLES_DIR) -> list[Path]:
    """The shipped example projects for File > Open Example, sorted by name."""
    return sorted(folder.glob("*.hzn"), key=lambda p: p.stem.lower()) if folder.is_dir() else []


class MainWindow(QMainWindow):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.settings = QSettings("Hertzon", "Hertzon")
        self.doc = Document(project)
        self.solver = SolveManager(self)
        self.edit_new_primitives = self.settings.value("edit_new_primitives", True, type=bool)
        self.new_objects_model = self.settings.value("modeler/new_objects_model", True, type=bool)
        self.new_material = "vacuum"
        self.ask_non_model = self.settings.value("modeler/ask_non_model", True, type=bool)
        self.clipboard: list[dict] = []
        self.report_tabs: dict[str, ReportWindow] = {}
        self.anim_timer = QTimer(self)
        self.anim_plot: str | None = None
        self._hidden_plots: set[str] = set()      # field plots hidden with Hide / Clear Overlays
        self._overlay_info: dict[str, str] = {}   # status bar text per drawn field plot
        self._plot_geom: dict[str, tuple] = {}    # non-model objects a drawn field plot lies on

        # central area: modeler (history tree + 3D view) and report tabs
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._sync_equation_page)
        self.calc_window = None                  # the Equations tab (Fields Calculator), made when first opened
        self._eq_page: int | None = None         # its page of the ribbon, shown while the tab is open
        self.view = ModelerView(self.doc)
        self.model_tree = ModelTree(self.doc)
        split = QSplitter()
        split.addWidget(self.model_tree)
        from .animation import AnimationBar
        vbox = QWidget()
        vl = QVBoxLayout(vbox)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        vl.addWidget(self.view, 1)
        self.anim_bar = AnimationBar(self)
        vl.addWidget(self.anim_bar)
        split.addWidget(vbox)
        split.setSizes([230, 1000])
        self.tabs.addTab(split, "3D Modeler")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.setCentralWidget(self.tabs)

        # docks
        self.project_tree = ProjectTree(self.doc)
        self.props = PropertiesPanel(self.doc)
        self.msgs = MessageManager()
        self.progress = ProgressPanel()
        dp = self._dock("Project Manager", self.project_tree, Qt.LeftDockWidgetArea)
        dpr = self._dock("Properties", self.props, Qt.LeftDockWidgetArea)
        dm = self._dock("Message Manager", self.msgs, Qt.BottomDockWidgetArea)
        dg = self._dock("Progress", self.progress, Qt.BottomDockWidgetArea)
        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
        self.resizeDocks([dp, dpr], [480, 360], Qt.Vertical)
        self.resizeDocks([dp], [300], Qt.Horizontal)
        self.resizeDocks([dm], [170], Qt.Vertical)
        self.resizeDocks([dm, dg], [700, 450], Qt.Horizontal)

        self._status_bar()
        self._actions()
        self._menus()
        self._apply_shortcuts()
        self._toolbars()
        self._connect()
        self.resize(1500, 950)
        self._restore_settings()
        self._update_title()
        self.doc.rebuild()
        self.msgs.add("info", f"Hertzon {__version__} - 3D full-wave FEM (EMerge solver)")
        self._first_start_note()
        self._start_update_check()
        from .ai_link import AiLink
        self.ai_link = AiLink(self)
        if self.settings.value("ai/live_link", False, type=bool):
            self.ai_link.start()

    # ==========================================================================================
    # construction
    # ==========================================================================================
    def _dock(self, title, widget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _status_bar(self) -> None:
        sb = self.statusBar()
        self.hint = QLabel("Ready")
        sb.addWidget(self.hint, 1)
        self.solve_on = QComboBox()
        self.solve_on.setToolTip("Where analyses run: this computer, a remote computer in the network\n"
                                 "(Tools > Options > Remote) or a cloud server (Tools > Options > Cloud)")
        self.solve_on.activated.connect(self._solve_on_chosen)
        sb.addPermanentWidget(QLabel("Solve on:"))
        sb.addPermanentWidget(self.solve_on)
        self.coord_mode = QComboBox()
        self.coord_mode.addItems(["Absolute", "Relative"])
        self.coord_sys = QComboBox()
        self.coord_sys.addItems(["Cartesian", "Cylindrical", "Spherical"])
        self.coord_sys.setToolTip("Coordinate entry: Cartesian (X, Y, Z), Cylindrical (R, Phi, Z) or "
                                  "Spherical (Rho, Theta, Phi); angles in degrees")
        self.cx, self.cy, self.cz = QLineEdit("0"), QLineEdit("0"), QLineEdit("0")
        self.coord_labels = []
        for lab, w in (("X:", self.cx), ("Y:", self.cy), ("Z:", self.cz)):
            lb = QLabel(lab)
            self.coord_labels.append(lb)
            sb.addPermanentWidget(lb)
            w.setFixedWidth(80)
            w.returnPressed.connect(self._coord_entered)
            sb.addPermanentWidget(w)
        sb.addPermanentWidget(self.coord_mode)
        sb.addPermanentWidget(self.coord_sys)
        self.coord_sys.currentTextChanged.connect(self._coord_sys_changed)
        self.unit_label = QLabel()
        sb.addPermanentWidget(self.unit_label)
        self.mode_label = QLabel()
        sb.addPermanentWidget(self.mode_label)
        self.plane_label = QLabel()
        self.plane_label.setToolTip("Drawing plane (change in the Draw ribbon or with Ctrl+Alt+1/2/3)")
        sb.addPermanentWidget(self.plane_label)

    def _act(self, text, slot=None, shortcut=None, tip=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if slot:
            a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        if tip:
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        self.addAction(a)
        return a

    def _actions(self) -> None:
        A = self._act
        self.a_new = A("&New", self.file_new, "Ctrl+N")
        self.a_open = A("&Open...", self.file_open, "Ctrl+O")
        self.a_save = A("&Save", self.file_save, "Ctrl+S")
        self.a_saveas = A("Save &As...", self.file_save_as, "Ctrl+Shift+S")
        self.a_import = A("&Import Geometry (STEP/IGES/BREP)...", self.import_geometry)
        self.a_exit = A("E&xit", self.close)
        self.a_undo = A("&Undo", self.doc.undo, "Ctrl+Z")
        self.a_redo = A("&Redo", self.doc.redo, "Ctrl+Y")
        self.a_copy = A("&Copy", self.copy_objects, "Ctrl+C")
        self.a_paste = A("&Paste", self.paste_objects, "Ctrl+V")
        self.a_delete = A("&Delete", self.delete_selection, "Delete")
        self.a_selall = A("Select &All", self.select_all, "Ctrl+A")
        self.a_selname = A("Select by &Name...", self.select_by_name, "Ctrl+Shift+N")
        self.a_group = A("&Group", self.group_selection, "Ctrl+G",
                         tip="Group the selected objects: they are selected, moved and transformed as one unit")
        self.a_ungroup = A("U&ngroup", lambda: self.ungroup_selection(), "Ctrl+Shift+G",
                           tip="Dissolve the groups of the selected objects so single objects can be edited")
        self.a_props = A("P&roperties...", self.edit_selected_properties)
        self.a_fit = A("Fit &All", self.view.fit_all, "Ctrl+D")
        self.a_fitsel = A("Fit &Selection", self.view.fit_selection, "Ctrl+E")
        self.a_ortho = A("&Orthographic Projection", lambda on=False: self.view.set_orthographic(bool(on)),
                         tip="Orthographic projection when checked, perspective projection when not",
                         checkable=True)
        self.a_ortho.setChecked(self.view.is_orthographic())
        self.view.on_projection = self.a_ortho.setChecked
        self.sel_group = QActionGroup(self)
        self.a_mode = {}
        for mode, key in (("object", "O"), ("face", "F"), ("edge", "E"), ("vertex", "V")):
            a = A(f"Select {mode.capitalize()}s", lambda _=False, m=mode: self.set_mode(m), key, checkable=True)
            self.sel_group.addAction(a)
            self.a_mode[mode] = a
        self.a_mode["object"].setChecked(True)
        self.a_unite = A("&Unite", lambda: self.boolean("Unite"), "Ctrl+U")
        self.a_subtract = A("&Subtract...", lambda: self.boolean("Subtract"))
        self.a_intersect = A("&Intersect", lambda: self.boolean("Intersect"))
        self.a_material = A("Assign Material...", self.assign_material)
        self.a_validate = A("&Validation Check...", self.validation_check)
        self.a_analyze = A("Analyze &All", self.analyze_all, "F5")
        self.a_escape = A("Cancel", self._escape, "Escape")
        self.a_help = A("Property Help", self.show_help, "F1")
        for i, pl in enumerate(("XY", "YZ", "XZ"), 1):
            A(f"Drawing Plane {pl}", lambda _=False, pl=pl: self.set_drawing_plane(pl), f"Ctrl+Alt+{i}")
        self.a_enter = A("Finish", self._enter, "Return")
        self.a_enter.setShortcutContext(Qt.WidgetWithChildrenShortcut)

    def _menus(self) -> None:
        mb = self.menuBar()
        from .theme import brand
        brand(self)
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_saveas):
            m.addAction(a)
        self.example_menu = m.addMenu("Open Example")
        self._fill_examples()
        self.recent_menu = m.addMenu("Recent Projects")
        self._fill_recent()
        m.addAction("Save As Archive...", self.save_archive)
        m.addAction("Restore Archive...", self.restore_archive)
        m.addSeparator()
        m.addAction("New Window", self.new_window)
        m.addSeparator()
        m.addAction(self.a_import)
        ex = m.addMenu("Export")
        ex.addAction("Geometry (STEP)...", self.export_step)
        ex.addAction("Touchstone...", self.export_touchstone)
        ex.addAction("EMerge Python Script...", self.export_script)
        ex.addSeparator()
        ex.addAction("Gerber...", self.export_gerber)
        m.addSeparator()
        m.addAction(self.a_exit)

        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_copy, self.a_paste, self.a_delete):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_selall)
        m.addAction(self.a_selname)
        m.addSeparator()
        m.addAction(self.a_group)
        m.addAction(self.a_ungroup)
        fs = m.addMenu("Select Faces")
        fs.addAction("All Object Faces", self.select_object_faces)
        fs.addAction("Face by Position...", self.select_face_by_position)
        fs.addAction("Faces by Plane...", self.select_faces_by_plane)
        m.addSeparator()
        arr = m.addMenu("Arrange")
        self._arrange_menu(arr, self._dup_menu(m))
        m.addAction("Scale...", lambda: self.transform("Scale"))
        m.addSeparator()
        m.addAction(self.a_props)

        m = mb.addMenu("&View")
        m.addAction(self.a_fit)
        m.addAction(self.a_fitsel)
        o = m.addMenu("Orientation")
        for w in ("iso", "top", "bottom", "front", "back", "right", "left"):
            o.addAction(w.capitalize(), lambda _=False, w=w: self.view.view(w))
        m.addAction(self.a_ortho)
        m.addSeparator()
        m.addAction("Grid Settings...", self.grid_settings)
        m.addAction("Hide Selection", lambda: self.set_visible(False))
        m.addAction("Show Selection", lambda: self.set_visible(True))
        m.addAction("Show All", self.show_all)
        m.addAction("Toggle Wireframe of Selection", self.toggle_wireframe)
        q = m.addMenu("Display Quality")
        for lvl in ("coarse", "normal", "fine"):
            q.addAction(lvl.capitalize(), lambda _=False, l=lvl: self.set_quality(l))
        m.addSeparator()
        m.addAction("Clear Field Overlays", self.clear_overlays)
        m.addAction("Screenshot...", self.screenshot)

        m = mb.addMenu("&Project")
        m.addAction("Insert Design", self.insert_design)
        m.addAction("Copy Design", self.copy_design)
        m.addAction("Rename Design...", self.rename_design)
        m.addAction("Delete Design", self.delete_design)
        m.addSeparator()
        m.addAction("Project Variables...", lambda: self.edit_variables(True))
        m.addAction("Design Properties...", lambda: self.edit_variables(False))

        m = mb.addMenu("&Draw")
        for label, kind in (("Line", "CreatePolyline:line"), ("Polyline", "CreatePolyline"), (None, None),
                            ("Box", "CreateBox"), ("Cylinder", "CreateCylinder"),
                            ("Regular Polyhedron", "CreateRegularPolyhedron"), ("Cone", "CreateCone"),
                            ("Sphere", "CreateSphere"), ("Torus", "CreateTorus"), (None, None),
                            ("Rectangle", "CreateRectangle"), ("Circle", "CreateCircle"), ("Ellipse", "CreateEllipse"),
                            ("Regular Polygon", "CreateRegularPolygon")):
            if label is None:
                m.addSeparator()
                continue
            m.addAction(label, lambda _=False, k=kind: self.start_draw(k))
            if kind == "CreatePolyline":
                arc = m.addMenu("Arc")      # Draw > Arc: polylines of one arc segment
                arc.addAction("3 Point", lambda: self.start_draw("CreatePolyline:arc3"))
                arc.addAction("Center Point", lambda: self.start_draw("CreatePolyline:arccenter"))
        m.addAction("Helix...", lambda: self.create_by_dialog("CreateHelix"))
        m.addAction("Spiral...", lambda: self.create_by_dialog("CreateSpiral"))
        m.addSeparator()
        m.addAction("Create Primitive by Dialog...", lambda: self.create_by_dialog())
        sw = m.addMenu("Sweep")
        sw.addAction("Along Vector...", lambda: self.transform("SweepAlongVector"))
        sw.addAction("Around Axis...", lambda: self.transform("SweepAroundAxis"))
        sw.addAction("Along Path", lambda: self.two_object_op("SweepAlongPath"))
        m.addAction("Thicken Sheet...", lambda: self.transform("ThickenSheet"))
        m.addAction("Bondwire...", lambda: self.create_by_dialog("CreateBondwire"))
        m.addSeparator()
        m.addAction("Region...", self.create_region)

        m = mb.addMenu("&Modeler")
        m.addAction("Import...", self.import_geometry)
        b = m.addMenu("Boolean")
        for a in (self.a_unite, self.a_subtract, self.a_intersect):
            b.addAction(a)
        b.addAction("Split...", lambda: self.transform("Split"))
        b.addAction("Separate Bodies", self.separate_bodies)
        b.addAction("Imprint", lambda: self.two_object_op("Imprint"))
        s = m.addMenu("Surface")
        s.addAction("Cover Lines", lambda: self.add_command_to_selection("Cover", {}))
        s.addAction("Uncover Faces", lambda: self.face_op("UncoverFaces"))
        s.addAction("Detach Faces", self.detach_faces)
        s.addAction("Sweep Faces Along Normal...", lambda: self.face_op("SweepFacesAlongNormal"))
        s.addAction("Section...", self.section)
        s.addAction("Connect", lambda: self.two_object_op("Connect"))
        m.addAction("Fillet...", self.fillet)
        m.addAction("Chamfer...", self.chamfer)
        m.addSeparator()
        m.addAction(self.a_material)
        m.setToolTipsVisible(True)
        m.addSeparator()
        sel = m.addMenu("Selection Mode")
        for a in self.a_mode.values():
            sel.addAction(a)
        m.addSeparator()
        m.addAction("Units...", self.set_units)
        m.addAction("Grid Settings...", self.grid_settings)
        m.addAction("Snap Mode...", self.snap_mode)
        mm = m.addMenu("New Objects")
        self.a_model = mm.addAction("Model")
        self.a_nonmodel = mm.addAction("Non Model")
        for a, flag in ((self.a_model, True), (self.a_nonmodel, False)):
            a.setCheckable(True)
            a.setChecked(self.new_objects_model == flag)
            a.triggered.connect(lambda _=False, f=flag: self.set_new_objects_model(f))
        mm = m.addMenu("Measure")
        for lab, fn in self._measure_items():
            mm.addAction(lab, fn)
        self.a_editnew = m.addAction("Edit Properties of New Primitives")
        self.a_editnew.setCheckable(True)
        self.a_editnew.setChecked(self.edit_new_primitives)
        self.a_editnew.toggled.connect(self._toggle_edit_new)

        m = mb.addMenu("&Simulation")
        m.addAction("Solution Type...", self.solution_type)
        m.addSeparator()
        bm = m.addMenu("Boundaries")
        ba = bm.addMenu("Assign")
        for k in schema.BOUNDARIES:
            ba.addAction(k, lambda _=False, k=k: self.assign("boundary", k))
        bm.addAction("Delete All", lambda: self.delete_all("boundary"))
        em = m.addMenu("Excitations")
        ea = em.addMenu("Assign")
        for k in schema.EXCITATIONS:
            ea.addAction(k, lambda _=False, k=k: self.assign("excitation", k))
        em.addAction("Edit Sources...", self.edit_sources)
        em.addAction("Delete All", lambda: self.delete_all("excitation"))
        mm = m.addMenu("Mesh")
        ma = mm.addMenu("Assign Mesh Operation")
        for k in schema.MESH_OPS:
            ma.addAction(k, lambda _=False, k=k: self.assign("mesh", k))
        mm.addAction("Plot Mesh on Selection", self.plot_mesh)
        an = m.addMenu("Analysis Setup")
        an.addAction("Add Solution Setup...", self.add_setup)
        an.addAction("Add Eigenmode Setup...", lambda: self.add_setup(eigen=True))
        an.addAction("Add Frequency Sweep...", self.add_sweep)
        rad = m.addMenu("Radiation")
        rad.addAction("Insert Far Field Setup > Infinite Sphere...", self.add_sphere)
        m.addSeparator()
        m.addAction(self.a_validate)
        m.addAction(self.a_analyze)
        m.addSeparator()
        rs = m.addMenu("Results")
        rs.aboutToShow.connect(lambda rs=rs: self._fill_results_menu(rs))
        rs.addAction("Overlay 3D Pattern on Model", lambda: self.overlay_pattern())
        rs.addAction("Solution Data...", self.solution_data)
        rs.addAction("Export Touchstone...", self.export_touchstone)
        fm = m.addMenu("Fields")
        pf = fm.addMenu("Plot Fields")
        for q in F.QUANTITIES:
            pf.addAction(q, lambda _=False, q=q: self.plot_fields(q))
        fm.addAction("Fields Calculator...", lambda: self.fields_calculator())
        fm.addAction("Animate Last Field Plot", self.toggle_animation)
        fm.addAction("Export Animation as GIF...", self.export_animation_gif)
        fm.addAction("Clear Field Overlays", self.clear_overlays)
        om = m.addMenu("Optimization")
        om.addAction("Add Parametric...", self.add_parametric)
        om.addAction("Add Optimization...", self.add_optimization)
        om.addAction("Derivative Tuning...", lambda: self.derivative_tuning())
        m.addSeparator()
        m.addAction("Design Properties...", lambda: self.edit_variables(False))

        m = mb.addMenu("&Tools")
        m.addAction("Edit Materials...", self.edit_materials)
        m.addAction("Options...", self.options)
        m.addAction("Solver Benchmark...", self.solver_benchmark)
        m.addAction("Keyboard Shortcuts...", self.edit_shortcuts)
        m.addSeparator()
        fd = m.addMenu("Filter Design")
        fd.addAction("Tapped Hairpin...", self.filter_hairpin)
        ad = m.addMenu("Antenna Design")
        ad.addAction("Rectangular Patch...", self.antenna_patch)
        m.addSeparator()
        m.addAction("Python Console", self.show_console)
        m.addAction("Run Script...", self.run_script)
        m.addAction("AI Assistants (MCP)...", self.ai_assistants)
        self.a_record = m.addAction("Record Script", self.toggle_record)
        self.a_record.setCheckable(True)
        m.addSeparator()
        self.a_emlog = m.addAction("Show EMerge Solver Log")
        self.a_emlog.setCheckable(True)
        self.a_emlog.toggled.connect(self._toggle_emlog)

        m = mb.addMenu("&Help")
        m.addAction(self.a_help)
        m.addAction("About Hertzon", self.about)
        m.addAction("Feature Overview", self.show_feature_matrix)
        m.addSeparator()
        m.addAction("Check for Updates...", self.check_for_updates)

    def _toolbars(self) -> None:
        """Ribbon like the reference tool: Desktop / View / Draw / Model / Simulation / Results."""
        from .ribbon import Ribbon
        rb = self.ribbon = Ribbon(self)
        t = self.addToolBar("Ribbon")
        t.setObjectName("RibbonTB")
        t.setMovable(False)
        t.setFloatable(False)
        t.setContextMenuPolicy(Qt.PreventContextMenu)
        t.setStyleSheet("QToolBar#RibbonTB { border: none; padding: 0px; spacing: 0px; }")
        t.addWidget(rb)
        draw = lambda k: (lambda: self.start_draw(k))          # noqa: E731
        view = lambda w: (lambda: self.view.view(w))           # noqa: E731
        bnd = lambda k: (lambda: self.assign("boundary", k))   # noqa: E731
        exc = lambda k: (lambda: self.assign("excitation", k))  # noqa: E731

        # ---- Desktop -----------------------------------------------------------------------
        pg = rb.page("Desktop")
        g = pg.group("Project")
        g.large("Save", "save", self.a_save)
        g.small("New", "new", self.a_new)
        g.small("Open", "open", self.a_open)
        g.small("Save As", "saveas", self.a_saveas)
        g = pg.group("Edit")
        g.small("Undo", "undo", self.a_undo)
        g.small("Redo", "redo", self.a_redo)
        g.small("Delete", "delete", self.a_delete)
        g.small("Copy", "copy", self.a_copy)
        g.small("Paste", "paste", self.a_paste)
        g.small("Properties", "variables", self.a_props)
        g = pg.group("Design")
        g.large("Insert Design", "design", self.insert_design)
        g.small("Copy Design", "copy", self.copy_design)
        g.small("Project Variables", "variables", lambda: self.edit_variables(True))
        g.small("Design Properties", "variables", lambda: self.edit_variables(False))
        g = pg.group("Import / Export")
        g.small("Import Geometry", "import", self.import_geometry)
        g.small("Export STEP", "export", self.export_step)
        g.small("Export Script", "export", self.export_script)
        g.small("Export Gerber", "export", self.export_gerber)
        g = pg.group("Tools")
        g.large("Options", "options", self.options)
        g.large("Materials", "material", self.edit_materials)
        g.large("Filter Design", "filter", self.filter_hairpin, menu=[("Tapped Hairpin...", self.filter_hairpin)],
                tip="Tapped hairpin band-pass filter as a parametrized design with optimization")
        g.large("Antenna Design", "antenna", self.antenna_patch, menu=[("Rectangular Patch...", self.antenna_patch)],
                tip="Inset-fed patch antenna as a parametrized design with optimization")
        g.large("Help", "help", self.show_help, menu=[("Property Help (F1)", self.show_help),
                                                      ("Feature Overview", self.show_feature_matrix)])

        # ---- View --------------------------------------------------------------------------
        pg = rb.page("View")
        g = pg.group("Zoom")
        g.large("Fit All", "fit", self.a_fit)
        g.large("Fit Selected", "fitsel", self.a_fitsel)
        g = pg.group("Orient")
        g.large("Iso", "iso", view("iso"))
        g.small("Top", "top", view("top"))
        g.small("Front", "front", view("front"))
        g.small("Right", "right", view("right"))
        g.small("Bottom", "orient", view("bottom"))
        g.small("Back", "orient", view("back"))
        g.small("Left", "orient", view("left"))
        g.large("Orthographic", "projection", self.a_ortho,
                tip="Orthographic projection when pressed, perspective projection when not pressed")
        g = pg.group("Visibility")
        g.small("Hide Selection", "hide", lambda: self.set_visible(False))
        g.small("Show Selection", "show", lambda: self.set_visible(True))
        g.small("Show All", "show", self.show_all)
        g.small("Wireframe", "wire", self.toggle_wireframe)
        g = pg.group("Display")
        g.large("Grid", "grid", self.grid_settings)
        g.small("Quality", "options", menu=[(l.capitalize(), (lambda l=l: self.set_quality(l)))
                                             for l in ("coarse", "normal", "fine")])
        g.small("Clear Overlays", "clear", self.clear_overlays)
        g.small("Screenshot", "screenshot", self.screenshot)

        # ---- Draw --------------------------------------------------------------------------
        pg = rb.page("Draw")
        # Draw ribbon order: Save | Edit | Select | Show/Hide | Zoom/Pan/Rotate/Orient | Fit |
        # 3D | 2D | 1D | Region/Point/Plane | Operations | Boolean | Sweep | Fillet | Measure | Grid | CS | Material
        g = pg.group("File")
        g.large("Save", "save", self.a_save)
        g = pg.group("Edit")
        g.icon_only = True
        g.small("Cut", "cut", self.cut_selection)
        g.small("Copy", "copy", self.a_copy)
        g.small("Paste", "paste", self.a_paste)
        g.small("Undo", "undo", self.a_undo)
        g.small("Redo", "redo", self.a_redo)
        g.small("Delete", "delete", self.a_delete)
        g = pg.group("Select")
        self.sel_combo = QComboBox()
        self.sel_combo.addItems(["Object", "Face", "Edge", "Vertex"])
        self.sel_combo.setMinimumWidth(90)
        self.sel_combo.currentTextChanged.connect(lambda t: self.set_mode(t.lower()))
        for mode, a in self.a_mode.items():
            a.toggled.connect(lambda on, m=mode: on and self.sel_combo.setCurrentText(m.capitalize()))
        g.widget(self.sel_combo)
        g.small("Select by Name", "byname", self.a_selname)
        g.small("Select All", "selall", self.a_selall)
        g = pg.group("Group")
        g.icon_only = True
        self._sel_buttons = [g.small("Group", "group", self.a_group), g.small("Ungroup", "ungroup", self.a_ungroup)]
        g = pg.group("Show")
        g.icon_only = True
        # layout: Show All / Show Selected / Show Only Selected | Overlays on/off / Hide Selected
        g.small("Show All", "show_all", self.show_all)
        g.small("Show Selected", "show_sel", lambda: self.set_visible(True))
        g.small("Show Only Selected", "show_only", self.show_only_selected)
        g.end_column()
        g.small("Hide/Show Overlaid Visualization", "overlay_vis", self.toggle_overlays)
        g.small("Hide Selected", "hide_sel", lambda: self.set_visible(False))
        g.end_column()
        g = pg.group("View")
        g.large("Zoom", "zoom", lambda: self._cam_zoom(1.25),
                menu=[("Zoom In", lambda: self._cam_zoom(1.25)), ("Zoom Out", lambda: self._cam_zoom(0.8))])
        self._nav_btns = {}
        self._nav_btns["pan"] = g.small("Pan", "pan", lambda: self.view.set_nav_mode("pan"), checkable=True,
                tip="Pan with the left mouse (Escape or Pan again ends it)", menu=[("Left", lambda: self._cam_pan(-1, 0)), ("Right", lambda: self._cam_pan(1, 0)),
                                    ("Up", lambda: self._cam_pan(0, 1)), ("Down", lambda: self._cam_pan(0, -1))])
        self._nav_btns["rotate"] = g.small("Rotate", "orbit", lambda: self.view.set_nav_mode("rotate"),
                checkable=True, tip="Rotate with the left mouse (Escape or Rotate again ends it)", menu=[("Left 15°", lambda: self._cam_rot(-15, 0)),
                                          ("Right 15°", lambda: self._cam_rot(15, 0)),
                                          ("Up 15°", lambda: self._cam_rot(0, 15)),
                                          ("Down 15°", lambda: self._cam_rot(0, -15))])
        g.small("Orient", "orient", menu=[(v.capitalize(), view(v)) for v in
                                          ("iso", "top", "bottom", "front", "back", "left", "right")])
        g.end_column()
        g.small("Fit All", "fit", self.a_fit)
        g.small("Fit Selected", "fitsel", self.a_fitsel)
        g = pg.group("3D")
        g.icon_only = True
        for txt, ic, k in (("Box", "box", "CreateBox"), ("Cylinder", "cylinder", "CreateCylinder"),
                           ("Polyhedron", "polyhedron", "CreateRegularPolyhedron"), ("Cone", "cone", "CreateCone"),
                           ("Sphere", "sphere", "CreateSphere"), ("Torus", "torus", "CreateTorus")):
            g.small(txt, ic, draw(k))
        g.small("Helix", "helix", lambda: self.create_by_dialog("CreateHelix"))
        g.small("Spiral", "spiral", lambda: self.create_by_dialog("CreateSpiral"))
        g.small("Bondwire", "bondwire", lambda: self.create_by_dialog("CreateBondwire"))
        g = pg.group("2D")
        g.icon_only = True
        for txt, ic, k in (("Rectangle", "rectangle", "CreateRectangle"), ("Circle", "circle", "CreateCircle"),
                           ("Polygon", "polygon", "CreateRegularPolygon"), ("Ellipse", "ellipse", "CreateEllipse")):
            g.small(txt, ic, draw(k))
        g = pg.group("1D")
        g.icon_only = True
        g.small("Line", "line", draw("CreatePolyline:line"))
        g.small("Polyline", "polyline", draw("CreatePolyline"))
        g.small("3 Point Arc", "arc3", draw("CreatePolyline:arc3"))
        g.small("Center Point Arc", "arc_center", draw("CreatePolyline:arccenter"))
        g.small("By Dialog", "polyline_dlg", lambda: self.create_by_dialog())
        g = pg.group("Region")
        g.icon_only = True
        g.small("Region", "region", self.create_region)
        g.small("Section", "section", self.section)
        g = pg.group("Operations")
        g.icon_only = True
        for txt, ic, fn in (("Move", "move", lambda: self.start_op("OpMove")),
                            ("Rotate", "rotate", lambda: self.transform("Rotate")),
                            ("Mirror", "mirror", lambda: self.start_op("OpMirror")),
                            ("Along Line", "dup_line", lambda: self.start_op("OpDupLine")),
                            ("Around Axis", "dup_axis", self.duplicate_axis),
                            ("Thru Mirror", "dup_mirror", lambda: self.start_op("OpDupMirror"))):
            self._sel_buttons.append(g.small(txt, ic, fn))
        g = pg.group("Boolean")
        g.icon_only = True
        for txt, ic, fn in (("Unite", "unite", self.a_unite), ("Subtract", "subtract", self.a_subtract),
                            ("Intersect", "intersect", self.a_intersect),
                            ("Split", "split", lambda: self.transform("Split"))):
            self._sel_buttons.append(g.small(txt, ic, fn))
        g = pg.group("Sweep")
        g.icon_only = True
        for txt, ic, fn in (("Along Vector", "sweep", lambda: self.transform("SweepAlongVector")),
                            ("Around Axis", "sweep_axis", lambda: self.transform("SweepAroundAxis")),
                            ("Along Path", "sweep_path", lambda: self.two_object_op("SweepAlongPath")),
                            ("Thicken", "thicken", lambda: self.transform("ThickenSheet"))):
            self._sel_buttons.append(g.small(txt, ic, fn))
        g = pg.group("Edges")
        g.icon_only = True
        self._sel_buttons.append(g.small("Fillet", "fillet", self.fillet))
        self._sel_buttons.append(g.small("Chamfer", "chamfer", self.chamfer))
        g = pg.group("Measure")
        g.small("Measure", "measure", menu=self._measure_items())
        g.small("Ruler", "ruler", lambda: self.start_draw("Measure"))
        g.small("Units", "units", self.set_units)
        g = pg.group("Grid")
        g.small("Grid", "grid", self.grid_settings)
        self.plane_combo = QComboBox()
        self.plane_combo.addItems(["XY", "YZ", "XZ"])
        self.plane_combo.setToolTip("Drawing plane: new rectangles, circles, cylinders ... lie in / stand on "
                                    "this plane (Ctrl+Alt+1/2/3)")
        self.plane_combo.currentTextChanged.connect(self.set_drawing_plane)
        g.widget(self.plane_combo)
        self.move_combo = QComboBox()
        self.move_combo.addItems(["3D", "In Plane", "Out of Plane"])
        self.move_combo.setToolTip("Movement mode while drawing: 3D follows object surfaces under the cursor, "
                                   "In Plane stays in the drawing plane, Out of Plane moves along its normal")
        self.move_combo.currentTextChanged.connect(self.set_movement_mode)
        g.widget(self.move_combo)
        g.small("Snap", "snap", self.snap_mode, tip="Snap Mode: grid, vertex, edge center, face center, quadrant, arc center")
        g = pg.group("Coordinate System")
        g.icon_only = True
        # Relative CS / Face CS / Object CS, each with a drop-down
        # Offset / Rotated / Both are drawn by clicking points in the 3D Modeler (Tab: type them)
        rel = [("Offset", lambda: self.start_draw("CSOffset")),
               ("Rotated", lambda: self.start_draw("CSRotated")),
               ("Both", lambda: self.start_draw("CSBoth")),
               ("By Values...", lambda: self.create_relative_cs("Both"))]
        g.small("Relative CS", "cs_rel", lambda: self.start_draw("CSBoth"), menu=rel,
                tip="Create a relative coordinate system (offset and/or rotated); it becomes the working CS")
        g.small("Face CS", "cs_face", self.create_face_cs, menu=[("Face CS", self.create_face_cs)],
                tip="Create a coordinate system on the selected face (origin at the face center, Z = normal)")
        g.small("Object CS", "cs_obj", self.create_object_cs, menu=[("Object CS", self.create_object_cs)],
                tip="Create a coordinate system at the center of the selected object")
        g.end_column()
        self.cs_combo = QComboBox()
        self.cs_combo.setToolTip("Working coordinate system: new objects are drawn and defined in it")
        self.cs_combo.setMinimumWidth(90)
        self.cs_combo.addItem("Global")
        self.cs_combo.textActivated.connect(self.set_working_cs)
        g.widget(self.cs_combo)
        g = pg.group("Material")
        self.model_combo = QComboBox()
        self.model_combo.addItems(["Model", "Non Model"])
        self.model_combo.setToolTip("New objects are part of the simulation model (Model) or only drawn "
                                    "for reference, e.g. for field plots (Non Model)")
        self.model_combo.setCurrentIndex(0 if self.new_objects_model else 1)
        self.model_combo.currentIndexChanged.connect(lambda i: self.set_new_objects_model(i == 0))
        g.widget(self.model_combo)
        self.mat_combo = QComboBox()
        self.mat_combo.setToolTip("Material of new 3D objects")
        self.mat_combo.setMinimumWidth(110)
        self.mat_combo.showPopup = self._mat_popup(self.mat_combo.showPopup)
        self.mat_combo.addItem(self.new_material)
        self.mat_combo.currentTextChanged.connect(lambda t: setattr(self, "new_material", t or "vacuum"))
        g.widget(self.mat_combo)
        self._mat_buttons = [g.small("Material", "material", self.assign_material)]
        self._update_sel_buttons()

        # ---- Model -------------------------------------------------------------------------
        pg = rb.page("Model")
        g = pg.group("Geometry")
        g.large("Import", "import", self.import_geometry)
        g.large("Region", "region", self.create_region)
        g.small("Fillet", "fillet", self.fillet)
        g.small("Chamfer", "fillet", self.chamfer)
        g.small("Cover Lines", "cover", lambda: self.add_command_to_selection("Cover", {}))
        g.small("Section", "split", self.section)
        g.small("Separate", "split", self.separate_bodies)
        g.small("Bondwire", "line", lambda: self.create_by_dialog("CreateBondwire"))
        g.small("Scale", "move", lambda: self.transform("Scale"))
        g = pg.group("Material")
        self._mat_buttons.append(g.large("Assign Material", "material", self.assign_material))
        g.large("Edit Materials", "material", self.edit_materials)
        g = pg.group("Measure")
        g.large("Measure", "measure", lambda: self.start_draw("Measure"), menu=self._measure_items())
        g.large("Units", "units", self.set_units)

        # ---- Simulation --------------------------------------------------------------------
        pg = rb.page("Simulation")
        g = pg.group("Solution")
        g.large("Solution Type", "solution_type", self.solution_type)
        g = pg.group("Setup")
        g.large("Setup", "setup", self.add_setup, menu=[("Add Solution Setup...", self.add_setup),
                                                       ("Add Eigenmode Setup...", lambda: self.add_setup(eigen=True))])
        g.large("Sweep", "freq_sweep", self.add_sweep)
        g = pg.group("Boundaries")
        g.small("Radiation", "radiation", bnd("Radiation"))
        g.small("PML", "pml", bnd("PML"))
        g.small("Perfect E", "perfe", bnd("Perfect E"))
        g.small("Finite Cond.", "boundary", bnd("Finite Conductivity"))
        g.small("Auto Open", "auto_open", self.solution_type, tip="Auto Open (Radiation / PML / Filter)")
        g.small("More", "boundary", menu=[(k, bnd(k)) for k in schema.BOUNDARIES])
        g = pg.group("Excitations")
        g.large("Wave Port", "waveport", exc("Wave Port"))
        g.small("Lumped Port", "lumpedport", exc("Lumped Port"))
        g.small("More", "waveport", menu=[(k, exc(k)) for k in schema.EXCITATIONS])
        g.small("Diff. Pairs", "lumpedport", self.differential_pairs,
                tip="Differential pairs of the terminals (Diff/Comm report quantities)")
        g = pg.group("Mesh")
        g.large("Mesh Ops", "mesh", menu=[(k, (lambda k=k: self.assign("mesh", k))) for k in schema.MESH_OPS])
        g.large("Plot Mesh", "plot_mesh", lambda: self.plot_mesh())
        g = pg.group("Radiation")
        g.large("Far Field", "farfield", self.add_sphere, tip="Insert Far Field Setup > Infinite Sphere")
        g = pg.group("Analyze")
        g.large("Validate", "validate", self.a_validate)
        g.large("Analyze All", "analyze", self.a_analyze)
        self._variation_group(pg)

        # ---- Lumped Elements ---------------------------------------------------------------
        pg = rb.page("Lumped Elements")
        g = pg.group("Components")

        def pkg_menu(kind):
            from ..model.lumped import GENERIC, SERIES
            items = [(p.name, (lambda p=p.name: self.insert_component(kind, p))) for p in GENERIC]
            items += [(p.name, (lambda p=p.name: self.insert_component(kind, p))) for p in SERIES
                      if kind in ("S", p.kind)]
            return items
        tip = (" with its pads: click the package, then the position on the board. With two traces selected it "
               "is placed in the gap between them.")
        g.large("Resistor", "resistor", lambda: self.insert_component("R"), menu=pkg_menu("R"),
                tip="Chip resistor" + tip)
        g.large("Inductor", "inductor", lambda: self.insert_component("L"), menu=pkg_menu("L"),
                tip="Chip inductor" + tip)
        g.large("Capacitor", "capacitor", lambda: self.insert_component("C"), menu=pkg_menu("C"),
                tip="Chip capacitor" + tip)
        g.large("Touchstone", "chip", lambda: self.insert_component("S"), menu=pkg_menu("S"),
                tip="Component measured by a Touchstone file (.s1p/.s2p)" + tip)
        g = pg.group("Boundary")
        g.large("Lumped RLC", "boundary", bnd("Lumped RLC"), tip="Lumped RLC boundary on the selected sheet")

        # ---- Results -----------------------------------------------------------------------
        pg = rb.page("Results")
        self._variation_group(pg)
        g = pg.group("Reports")
        b = g.large("Create Report", "rect_plot", None, menu=[])
        rm = b.menu() or QMenu(b)
        b.setMenu(rm)
        b.setPopupMode(b.ToolButtonPopupMode.InstantPopup)
        rm.aboutToShow.connect(lambda rm=rm: self._fill_report_menu(rm))
        g = pg.group("Solution Data")
        g.large("Solution Data", "soldata", lambda: self.solution_data())
        g.large("Tuning", "variables", lambda: self.derivative_tuning(),
                tip="Derivative Tuning: move design variables with sliders and see the S-parameters follow live")
        g.large("Touchstone", "export", lambda: self.export_touchstone())
        g = pg.group("Fields")
        g.large("Mag E", "field", lambda: self.plot_fields("Mag_E"))
        g.small("Vector E", "field_vec", lambda: self.plot_fields("Vector_E"))
        g.small("Mag H", "field_h", lambda: self.plot_fields("Mag_H"))
        b = g.small("More", "field", menu=[])
        b.menu().aboutToShow.connect(lambda mm=b.menu(): self._fill_plot_fields(mm))
        g.small("Animate", "animate", self.toggle_animation)
        g.small("Modify Plot", "variables", lambda: self._on_current_plot(self.modify_field_plot))
        g.small("Attributes", "options", lambda: self._on_current_plot(self.field_plot_attributes))
        g.small("Clear", "clear", self.clear_overlays)
        g.small("Plot Mesh", "plot_mesh", lambda: self.plot_mesh())
        g.large("Calculator", "calculator", lambda: self.fields_calculator(),
                tip="Fields Calculator: equations of E, H and J in the Equations tab")
        rb.setCurrentIndex(2)

    def _connect(self) -> None:
        d = self.doc
        d.message.connect(self.msgs.add)
        d.titleChanged.connect(self._update_title)
        d.changed.connect(self._on_doc_changed)
        d.changed.connect(lambda *_: self._autosave_tick() if self.doc.dirty else None)
        d.selectionChanged.connect(self._on_selection)
        self.view.picked.connect(lambda picks, add: self.doc.select(picks, add))
        self.view.contextMenuRequested.connect(self._view_context)
        self.view.drawFinished.connect(self._draw_finished)
        self.view.drawPhaseChanged.connect(lambda t: self.hint.setText(t or "Ready"))
        self.view.navModeChanged.connect(self._on_nav_mode)
        self.view.cursorMoved.connect(self._cursor_moved)
        self.view.tabPressed.connect(self._coord_tab)
        self.msgs.messageActivated.connect(self.goto_message)
        self.view.hoverInfo.connect(lambda t: self.view.draw is None and self.hint.setText(t or "Ready"))
        self.model_tree.objectSelected.connect(self._tree_objects)
        self.model_tree.commandSelected.connect(lambda o, i: self.props.show_object(o, i))
        self.model_tree.commandActivated.connect(self.edit_command)
        self.model_tree.contextRequested.connect(self._model_tree_context)
        self.model_tree.csActivated.connect(self.set_working_cs)   # double click: work in this CS (edit: right click)
        from . import props as _props
        _props.ADD_VARIABLE_HOOK = self._add_variable_from_field
        _props.DYNAMIC_CHOICES.update({"Orientation": lambda: self.des.cs_names(),
                                       "CoordinateSystem": lambda: self.des.cs_names(),
                                       "Reference": lambda: self.des.cs_names()})
        self.doc.geometryChanged.connect(self._sync_cs_combo)
        self.project_tree.itemSelectedData.connect(self._project_selected)
        self.project_tree.itemActivatedData.connect(self._project_activated)
        self.project_tree.contextRequested.connect(self._project_context)
        self.solver.message.connect(self.msgs.add)
        self.solver.progress.connect(self.progress.update_progress)
        self.solver.finished.connect(self._solve_finished)
        self.solver.filesArrived.connect(lambda k: (self._pass_data(k, {}), self._refresh_reports()))
        self.solver.passUpdate.connect(self._pass_finished)
        self.solver.passData.connect(self._pass_data)
        self.solver.started.connect(lambda k: self.project_tree.rebuild())
        self.progress.abortRequested.connect(self.solver.abort)
        self.progress.set_manager(self.solver)
        from ..solver import farfield_post
        farfield_post.RUN = self.run_post
        from ..solver import lazy_fields
        import threading

        def run_fields(text, fn):    # fields computed on demand: progress window unless already inside one
            if threading.current_thread() is not threading.main_thread():
                return fn(None)                  # inside a post-processing worker already
            if getattr(self, "_post_busy", False):
                # a redraw (adaptive pass, timer) while another computation runs in the worker: the field
                # kernels must not run in two threads at once (numba aborts the whole program)
                raise RuntimeError("Field data is still being computed; show the plot again when it is done")
            import time
            t = time.time()
            res = self.run_post(f"{text} for the plot", fn)
            self.msgs.add("info", f"{text}: {time.time() - t:.1f} s")
            return res
        lazy_fields.RUN = run_fields

    # ==========================================================================================
    # helpers
    # ==========================================================================================
    @property
    def des(self) -> Design:
        return self.doc.design

    def _update_title(self) -> None:
        p = self.doc.project
        name = Path(p.path).name if p.path else p.name
        self.setWindowTitle(f"Hertzon - {name}{'*' if self.doc.dirty else ''} - {self.doc.design_name} - 3D Modeler")
        self.unit_label.setText(f"  {self.des.units}  ")
        self.view.length_unit = (self.des.units, LENGTH_UNITS.get(self.des.units, 1e-3))

    def _on_doc_changed(self, what: str) -> None:
        self._update_title()
        self._update_sel_buttons()                 # a sheet may have become a solid (Thicken, Sweep)
        self._sync_overlays()
        self._follow_helper_plots()
        if what in ("variables", "all", "model", "results"):
            self._follow_variation()
        if what in ("attributes",):
            self.view.restyle_all()
            self.model_tree.rebuild()
        tgt = getattr(self.props, "target", None)
        if what == "excitations" and tgt and tgt[0] == "excitation":
            try:                                   # live k / Eo arrows while editing the plane wave
                self.view.show_assignments([self.des.assignment(tgt[1])])
            except KeyError:
                pass

    def _update_sel_buttons(self) -> None:
        on = bool(self.doc.selection)
        for b in getattr(self, "_sel_buttons", []):
            b.setEnabled(on)
        # only solids take a material: sheets get a boundary (Finite Conductivity, Impedance, ...)
        solids, others = material_targets(self.doc, self.doc.selected_objects())
        tip = "Assign a material to the selected solids"
        if others and not solids:
            tip = "Sheets and lines have no material: assign a boundary (Finite Conductivity, Impedance, ...)"
        elif others:
            tip += " (selected sheets and lines are skipped)"
        a = getattr(self, "a_material", None)
        if a is not None:
            a.setEnabled(bool(solids))
            a.setToolTip(tip)
        for b in getattr(self, "_mat_buttons", []):
            b.setEnabled(bool(solids))
            b.setToolTip(tip)

    def _mat_popup(self, orig):
        def show():
            cur = self.mat_combo.currentText()
            self.mat_combo.blockSignals(True)
            self.mat_combo.clear()
            self.mat_combo.addItems(sorted({m.name for m in self.des.all_materials()}))
            self.mat_combo.setCurrentText(cur)
            self.mat_combo.blockSignals(False)
            orig()
        return show

    def _apply_new_material(self, o) -> None:
        if o.dim == 3 and self.new_material != "vacuum":
            from model.materials import default_color
            o.material = self.new_material
            o.color = default_color(self.new_material)

    # ---- coordinate systems (Modeler > Coordinate System) ----------------------------------
    def _sync_cs_combo(self) -> None:
        c = getattr(self, "cs_combo", None)
        if c is None:
            return
        c.blockSignals(True)
        c.clear()
        c.addItems(self.des.cs_names())
        c.setCurrentText(self.des.working_cs)
        c.blockSignals(False)

    def _cs_changed(self) -> None:
        self.view.refresh_cs()
        self.view.request_render()
        self.model_tree.rebuild()
        self._sync_cs_combo()

    def _add_variable_from_field(self, name: str, value: str) -> None:
        """a name typed into a value field becomes a design variable (Add Variable dialog)."""
        self.doc.checkpoint()
        self.des.variables[name] = value
        self.msgs.add("info", f"Added design variable {name} = {value}")
        self.doc.commit("variables")

    def set_working_cs(self, name: str) -> None:
        if name not in self.des.cs_names():
            return
        self.des.working_cs = name
        self.doc.dirty = True
        self._cs_changed()
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        from .trees import ROLE
        it = QTreeWidgetItemIterator(self.model_tree)
        while it.value():                         # the new working CS is the selected item in the tree
            if it.value().data(0, ROLE) == ("cs", name):
                self.model_tree.blockSignals(True)
                self.model_tree.setCurrentItem(it.value())
                self.model_tree.blockSignals(False)
                break
            it += 1
        self.msgs.add("info", f"Working coordinate system: {name}")

    def _cs_dialog(self, title: str, name: str, reference: str, values: dict, kind: str = "Relative"):
        from .props import PropertyGrid
        from PySide6.QtWidgets import QLineEdit, QFormLayout
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        ed = QLineEdit(name)
        ref = QComboBox()
        ref.addItems([n for n in self.des.cs_names() if n != name])
        ref.setCurrentText(reference)
        form.addRow("Name", ed)
        form.addRow("Reference CS", ref)
        lay.addLayout(form)
        grid = PropertyGrid()
        vals = dict(values)
        grid.set(schema.COORDINATE_SYSTEM, vals, self.doc.evaluator())
        lay.addWidget(grid)
        lay.addWidget(QLabel(f"Lengths in {self.des.units}. {kind} coordinate system: origin and axes are "
                             "given in the reference CS."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(460, 380)
        if dlg.exec() != QDialog.Accepted:
            return None
        return ed.text().strip(), ref.currentText(), vals

    def _check_cs(self, cs) -> bool:
        from ..model import coordsys
        try:
            coordsys.frame(self.des, cs.name, self.doc.evaluator())
            return True
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Coordinate System", f"Invalid coordinate system: {e}")
            return False

    def create_object_cs(self) -> None:
        from ..model.design import CoordSys
        objs = self.doc.selected_objects()
        if len(objs) != 1 or objs[0] not in self.doc.tess:
            QMessageBox.information(self, "Object CS", "Select exactly one object first.")
            return
        c = np.asarray(self.doc.tess[objs[0]].points, float)
        c = (c.min(axis=0) + c.max(axis=0)) / 2 / LENGTH_UNITS[self.des.units]
        k = 1
        while f"ObjectCS{k}" in self.des.cs_names():
            k += 1
        self.doc.checkpoint()
        cs = CoordSys(f"ObjectCS{k}", "Object", "Global")
        cs.props.update(Origin=",".join(f"{a:.6g}" for a in c), Object=objs[0])
        self.des.coord_systems.append(cs)
        self.des.working_cs = cs.name
        self.doc.dirty = True
        self._cs_changed()
        self.msgs.add("info", f"Created {cs.name} at the center of {objs[0]} (working coordinate system)")

    def create_relative_cs(self, kind: str = "Both") -> None:
        from ..model.design import CoordSys
        k = 1
        while f"RelativeCS{k}" in self.des.cs_names():
            k += 1
        name = f"RelativeCS{k}"
        r = self._cs_dialog(f"Create Relative Coordinate System ({kind})", name, self.des.working_cs,
                            schema.defaults(schema.COORDINATE_SYSTEM))
        if r is None:
            return
        nm, ref, vals = r
        if not nm or nm in self.des.cs_names():
            QMessageBox.warning(self, "Coordinate System", f"Name '{nm}' is empty or already used")
            return
        self.doc.checkpoint()
        cs = CoordSys(nm, "Relative", ref)
        cs.props.update(vals)
        self.des.coord_systems.append(cs)
        if not self._check_cs(cs):
            self.des.coord_systems.remove(cs)
            return
        self.des.working_cs = nm          # a new coordinate system becomes the working CS
        self.doc.dirty = True
        self._cs_changed()
        self.msgs.add("info", f"Created {nm} (working coordinate system)")

    def _cs_drawn(self, kind: str, P: list) -> None:
        """Relative CS from clicked points; the points are in the working CS, which is the reference."""
        from ..model.design import CoordSys
        z = np.zeros(3)
        if kind == "CSOffset":
            o, x, y = P[0], np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
        elif kind == "CSRotated":
            o, x, y = z, P[0], P[1]
        else:
            o, x, y = P[0], P[1] - P[0], P[2] - P[0]
        if kind != "CSOffset":
            if np.linalg.norm(x) < 1e-15 or np.linalg.norm(np.cross(x, y)) < 1e-12 * max(np.linalg.norm(x) ** 2, 1e-30):
                self.msgs.add("error", "Coordinate system: the X axis point and the XY plane point must not be "
                              "collinear with the origin")
                return
        k = 1
        while f"RelativeCS{k}" in self.des.cs_names():
            k += 1
        self.doc.checkpoint()
        cs = CoordSys(f"RelativeCS{k}", "Relative", self.des.working_cs)
        cs.props.update(schema.defaults(schema.COORDINATE_SYSTEM))
        if kind == "CSOffset":
            cs.props.update(Origin=self._pt(o))
        else:
            cs.props.update(Origin=self._pt(o), XAxis=self._pt(x), YPoint=self._pt(y))
        self.des.coord_systems.append(cs)
        if not self._check_cs(cs):
            self.des.coord_systems.remove(cs)
            return
        self.des.working_cs = cs.name
        self.doc.dirty = True
        self._cs_changed()
        self.msgs.add("info", f"Created {cs.name} (working coordinate system)")

    def create_face_cs(self) -> None:
        from ..model.design import CoordSys
        sel = self.doc.selection
        if self.doc.selection_mode != "face" or len(sel) != 1 or sel[0].index < 0:
            QMessageBox.information(self, "Face CS", "Select exactly one face first (face selection mode, F).")
            self.set_mode("face")
            return
        t = self.doc.tess.get(sel[0].obj)
        fi = sel[0].index
        if t is None or fi >= len(t.face_center):
            return
        c = np.asarray(t.face_center[fi], float)
        nrm = np.asarray(t.face_normal[fi], float)
        nrm = nrm / (np.linalg.norm(nrm) or 1.0)
        x = np.array([1.0, 0, 0]) if abs(nrm[0]) < 0.9 else np.array([0, 1.0, 0])
        x = x - (x @ nrm) * nrm
        x /= np.linalg.norm(x)
        y = np.cross(nrm, x)
        s = LENGTH_UNITS[self.des.units]
        name = "FaceCS1"
        k = 1
        while name in self.des.cs_names():
            k += 1
            name = f"FaceCS{k}"
        f = lambda v: ",".join(f"{a:.6g}" for a in v)  # noqa: E731
        self.doc.checkpoint()
        cs = CoordSys(name, "Face", "Global")
        cs.props.update(Mode="Axis/Position", Origin=f(c / s), XAxis=f(x), YPoint=f(y))
        cs.props["Face"] = f"{sel[0].obj}:{fi}"
        self.des.coord_systems.append(cs)
        self.des.working_cs = name
        self.doc.dirty = True
        self._cs_changed()
        self.msgs.add("info", f"Created {name} on {sel[0].obj} face {fi} (working coordinate system)")

    def edit_cs(self, name: str) -> None:
        if name == "Global":
            return
        cs = self.des.cs(name)
        r = self._cs_dialog(f"Coordinate System: {name}", name, cs.reference, cs.props, cs.kind)
        if r is None:
            return
        nm, ref, vals = r
        if nm != name and (not nm or nm in self.des.cs_names()):
            QMessageBox.warning(self, "Coordinate System", f"Name '{nm}' is empty or already used")
            return
        self.doc.checkpoint()
        old = (cs.name, cs.reference, dict(cs.props))
        cs.reference, cs.props = ref, vals
        if nm != name:
            cs.name = nm
            for o in self.des.objects:
                if o.orientation == name:
                    o.orientation = nm
            for c in self.des.coord_systems:
                if c.reference == name:
                    c.reference = nm
            for sp in self.des.spheres:
                if sp.props.get("CoordinateSystem") == name:
                    sp.props["CoordinateSystem"] = nm
            for o in self.des.objects:
                for c in o.history:
                    if c.props.get("CoordinateSystem") == name:
                        c.props["CoordinateSystem"] = nm
            if self.des.working_cs == name:
                self.des.working_cs = nm
        if not self._check_cs(cs):
            cs.name, cs.reference, cs.props = old
            return
        self.doc.commit("model")
        self._cs_changed()

    def delete_cs(self, name: str) -> None:
        users = [o.name for o in self.des.objects if o.orientation == name] + \
                [c.name for c in self.des.coord_systems if c.reference == name] + \
                [sp.name for sp in self.des.spheres if sp.props.get("CoordinateSystem") == name] + \
                [o.name for o in self.des.objects if any(c.props.get("CoordinateSystem") == name for c in o.history)]
        if users:
            QMessageBox.warning(self, "Delete Coordinate System",
                                f"{name} is used by: {', '.join(users[:8])}. Change those first.")
            return
        self.doc.checkpoint()
        self.des.coord_systems = [c for c in self.des.coord_systems if c.name != name]
        if self.des.working_cs == name:
            self.des.working_cs = "Global"
        self.doc.dirty = True
        self._cs_changed()

    def cut_selection(self) -> None:
        self.a_copy.trigger()
        self.a_delete.trigger()

    def show_only_selected(self) -> None:
        sel = set(self.doc.selected_objects())
        if not sel:
            return
        for o in self.des.objects:
            o.visible = o.name in sel
        self.doc.dirty = True
        self.view.restyle_all()
        self.model_tree.rebuild()

    def toggle_overlays(self) -> None:
        acts = [a for L in self.view.overlay_actors.values() for a in L]
        if not acts:
            return
        on = not acts[0].GetVisibility()
        for a in acts:
            a.SetVisibility(on)
        self.view.request_render()

    def hide_all(self) -> None:
        for o in self.des.objects:
            o.visible = False
        self.view.restyle_all()
        self.model_tree.rebuild()

    def _cam(self):
        return self.view.plotter.renderer.GetActiveCamera()

    def _cam_zoom(self, f: float) -> None:
        w, h = self.view.plotter.renderer.GetSize()
        self.view.zoom_at_cursor(f, (w // 2, h // 2))
        self.view.request_render()

    def _cam_pan(self, dx: int, dy: int) -> None:
        import numpy as _np
        cam = self._cam()
        fp, pos = _np.array(cam.GetFocalPoint()), _np.array(cam.GetPosition())
        up = _np.array(cam.GetViewUp())
        right = _np.cross(fp - pos, up)
        right /= _np.linalg.norm(right) or 1.0
        d = (cam.GetParallelScale() if cam.GetParallelProjection() else _np.linalg.norm(fp - pos) * 0.4) * 0.2
        sh = (dx * right + dy * up) * d
        cam.SetFocalPoint(*(fp + sh))
        cam.SetPosition(*(pos + sh))
        self.view.request_render()

    def _cam_rot(self, az: float, el: float) -> None:
        cam = self._cam()
        cam.Azimuth(az)
        cam.Elevation(el)
        cam.OrthogonalizeViewUp()
        self.view.plotter.renderer.ResetCameraClippingRange()
        self.view.request_render()

    def _on_selection(self) -> None:
        self._update_sel_buttons()
        sel = self.doc.selection
        mode = self.doc.selection_mode
        self.mode_label.setText(f"  {mode.capitalize()} mode  ")
        if not sel:
            self.hint.setText("Ready")
            return
        if mode == "object" and len(sel) == 1:
            self.props.show_object(sel[0].obj)
        txt = ", ".join(f"{s.obj}" + ("" if s.index < 0 else f" {mode} {s.index}") for s in sel[:6])
        if len(sel) > 6:
            txt += f" ... ({len(sel)} items)"
        if mode == "face" and len(sel) == 1:
            t = self.doc.tess.get(sel[0].obj)
            if t is not None and sel[0].index < len(t.face_area):
                s = LENGTH_UNITS[self.des.units]
                c = t.face_center[sel[0].index]
                txt += (f"   area {t.face_area[sel[0].index] / s ** 2:.6g} {self.des.units}²,"
                        f" center ({c[0] / s:.4g}, {c[1] / s:.4g}, {c[2] / s:.4g})")
        self.hint.setText("Selected: " + txt)

    def _tree_objects(self, names: list, add: bool) -> None:
        self.set_mode("object")
        # the tree lists the members of a group: a member picked there is selected on its own
        self.doc.select([Pick(n) for n in names], add, whole_groups=False)

    def set_mode(self, mode: str) -> None:
        self.a_mode[mode].setChecked(True)
        self.doc.set_selection_mode(mode)
        self.mode_label.setText(f"  {mode.capitalize()} mode  ")

    def _fmt(self, v: float) -> str:
        return f"{v / LENGTH_UNITS[self.des.units]:.6g}"

    def _pt(self, p) -> str:
        return ",".join(self._fmt(x) for x in p)

    def _materials(self) -> list[str]:
        return [m.name for m in self.doc.project.all_materials()]

    def _on_nav_mode(self, mode: str) -> None:
        for k, b in getattr(self, "_nav_btns", {}).items():
            b.setChecked(k == mode)
        self.hint.setText({"rotate": "Rotate: drag with the left mouse, Escape ends",
                           "pan": "Pan: drag with the left mouse, Escape ends"}.get(mode, "Ready"))

    def _escape(self) -> None:
        if self.view.nav_mode:
            self.view.set_nav_mode("")
            return
        self.view.reset_interaction()
        if self.view.draw is not None:
            self.view.cancel_draw()
        else:
            self.doc.select([])

    def _enter(self) -> None:
        if self.view.draw is not None and self.view.draw.kind == "CreatePolyline":
            self.view.finish_polyline()

    def _toggle_edit_new(self, v: bool) -> None:
        self.edit_new_primitives = v
        self.settings.setValue("edit_new_primitives", v)

    def _fill_solve_on(self) -> None:
        """Status bar: This computer / the remote computer / the cloud / Set up ..."""
        box = getattr(self, "solve_on", None)
        if box is None:
            return
        s = self.settings
        addr = str(s.value("remote/address", "") or "")
        box.blockSignals(True)
        box.clear()
        box.addItem("This computer", "local")
        if addr:
            box.addItem(addr, "remote")
        if s.value("cloud/hetzner_token", ""):
            box.addItem(f"Hetzner Cloud ({s.value('cloud/server_type', 'ccx33')})", "cloud")
        box.addItem("Remote computer ..." if not addr else "Other remote computer ...", "setup-remote")
        box.addItem("Cloud ...", "setup-cloud")
        r = self.solver.remote
        now = "local" if r is None else ("cloud" if hasattr(r, "ensure") else "remote")
        box.setCurrentIndex(max(0, box.findData(now)))
        box.blockSignals(False)

    def _solve_on_chosen(self, i: int) -> None:
        what = self.solve_on.itemData(i)
        if what in ("setup-remote", "setup-cloud"):
            self.options(tab="Remote" if what == "setup-remote" else "Cloud")
            self._fill_solve_on()
            return
        self.settings.setValue("remote/enabled", what == "remote")
        self.settings.setValue("cloud/enabled", what == "cloud")
        self.apply_hpc_settings()
        r = self.solver.remote
        self.msgs.add("info", "Analyses run on this computer" if r is None else f"Analyses run on {r.address}"
                      + (" (the server is created with the first analysis and deleted when idle)"
                         if hasattr(r, "ensure") else ""))

    def options(self, tab: str = "") -> None:
        from .dialogs import OptionsDialog
        dlg = OptionsDialog(self, self)
        if tab:
            from PySide6.QtWidgets import QTabWidget
            tabs = dlg.findChild(QTabWidget)
            for k in range(tabs.count()):
                if tabs.tabText(k) == tab:
                    tabs.setCurrentIndex(k)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()
            self.msgs.add("info", "Options saved")

    # ---- design variations (solved parametric variations shown without solving again) --------------
    def _variation_group(self, page) -> None:
        """Ribbon group with the design variation: Nominal or a solved parametric variation."""
        from PySide6.QtWidgets import QComboBox, QLabel as _QLabel

        win = self

        class VariationCombo(QComboBox):
            def showPopup(self):            # the list follows the solved variations when it opens
                win._fill_variation_combos()
                super().showPopup()

        g = page.group("Design Variation")
        c = VariationCombo()
        c.setMinimumWidth(240)
        c.setToolTip("Show a solved variation of a parametric setup: the design variables are set to its values, "
                     "geometry, field overlays and reports follow without solving again. Fields need "
                     "'Save fields and mesh' in the parametric setup.")
        c.activated.connect(lambda i, c=c: self._variation_chosen(c.itemData(i)))
        g.widget(_QLabel("Variation:"))
        g.widget(c)
        self.__dict__.setdefault("variation_combos", []).append(c)
        self._fill_variation_combos()

    def _fill_variation_combos(self) -> None:
        from ..solver.parametric import design_variations, variation_label, current_values
        try:
            vs = design_variations(self.doc.project, self.des) if self.doc.project.path else []
        except Exception:  # noqa: BLE001
            vs = []
        cur = current_values(self.doc.project, self.des)
        sel = 0
        items = [("Nominal", None)]
        for ps, texts, _ in vs:
            items.append((f"{ps}: {variation_label(texts)}", (ps, texts)))
            if sel == 0 and all(cur.get(k) == v for k, v in texts.items()):
                sel = len(items) - 1
        for c in getattr(self, "variation_combos", []):
            c.blockSignals(True)
            c.clear()
            for label, data in items:
                c.addItem(label, data)
            c.setCurrentIndex(sel)
            c.setEnabled(len(items) > 1)
            c.blockSignals(False)

    def _variation_chosen(self, data) -> None:
        if data is None:
            self.show_nominal()
        else:
            self.show_variation(*data)

    def show_variation(self, ps: str, texts: dict) -> None:
        """set the design to a solved variation; its solution is shown without solving again."""
        from ..solver.parametric import current_values, variation_label
        cur = current_values(self.doc.project, self.des)
        nominal = getattr(self, "_nominal_vars", None)
        if nominal is None:
            nominal = self._nominal_vars = {}
        for k in texts:
            nominal.setdefault(k, cur.get(k))
        self.apply_variables(texts, f"variation {variation_label(texts)} of {ps}")
        folders = [result_dir(self.doc, s.name) for s in self.des.setups]
        if not any(list(f.glob("solution_*.npz")) or list(f.glob("fields_*.npz")) for f in folders if f.exists()):
            self.msgs.add("info", f"{ps}: the fields of this variation were not saved; reports work, field "
                                  "overlays need 'Save fields and mesh' (Options tab of the parametric setup)")
        self._fill_variation_combos()

    def show_nominal(self) -> None:
        nominal = getattr(self, "_nominal_vars", None)
        if nominal:
            self._nominal_vars = None
            self.apply_variables({k: v for k, v in nominal.items() if v is not None}, "the nominal values")
        self._nominal_vars = None
        self._fill_variation_combos()

    def _follow_variation(self) -> None:
        """Field overlays and reports follow when the results of the current design come from another folder
        (a variation was chosen, or the variables were set back)."""
        if not self.doc.project.path:
            return
        try:
            dirs = {s.name: result_dir(self.doc, s.name) for s in self.des.setups}
        except Exception:  # noqa: BLE001
            return
        last = getattr(self, "_shown_dirs", None)
        self._shown_dirs = dirs
        if last is None or last == dirs:
            self._fill_variation_combos()
            return
        for name in [n for n in self.view.overlay_actors if any(p.name == n for p in self.des.field_plots)]:
            self.show_field_plot(name, focus=False)
        self._refresh_reports()
        self.project_tree.rebuild()
        self._fill_variation_combos()

    def solver_benchmark(self) -> None:
        """Tools > Solver Benchmark: scaling of the solve time with the cores on this computer."""
        from .benchmark import BenchmarkDialog
        dlg = getattr(self, "_benchmark", None)
        if dlg is None:
            dlg = self._benchmark = BenchmarkDialog(self)
            dlg.setModal(False)
        dlg.show()
        dlg.raise_()

    def apply_hpc_settings(self) -> None:
        self.solver.cores = int(self.settings.value("hpc/cores", 0))
        self.solver.sweep_workers = int(self.settings.value("hpc/sweep_workers", 2))
        try:                    # the remote service of this computer uses the same HPC settings
            from ..remote import local
            local.write_hpc(self.solver.cores, self.solver.sweep_workers)
        except Exception:  # noqa: BLE001
            pass
        self.solver.remote = None
        s = self.settings
        addr = str(s.value("remote/address", "") or "")
        if s.value("cloud/enabled", False, type=bool) and s.value("cloud/hetzner_token", ""):
            from ..remote.cloud import CloudHost
            self.solver.remote = CloudHost(self.cloud_session())
        elif s.value("remote/enabled", False, type=bool) and addr:
            from ..remote.client import RemoteHost
            self.solver.remote = RemoteHost(addr, str(s.value("remote/token", "") or ""))
        self._fill_solve_on()

    def cloud_session(self):
        """The cloud session of the settings in Tools > Options > Cloud (kept while they do not change, so
        its server and SSH tunnel are reused)."""
        from ..remote.cloud import CloudSession
        from ..remote.cloud.hetzner import DEFAULT_LOCATION, DEFAULT_TYPE, Hetzner
        s = self.settings
        key = (str(s.value("cloud/hetzner_token", "") or "").strip(), str(s.value("cloud/server_type", DEFAULT_TYPE)),
               str(s.value("cloud/location", DEFAULT_LOCATION)), float(s.value("cloud/idle_min", 30)),
               float(s.value("cloud/contact_h", 48)), float(s.value("cloud/quiet_h", 2)))
        old = getattr(self, "_cloud", None)
        if old is not None and old[0] == key:
            return old[1]
        if old is not None:
            old[1].tunnel.close()
        session = CloudSession(Hetzner(key[0]), key[1], key[2], idle_min=key[3], contact_h=key[4], quiet_h=key[5])
        self._cloud = (key, session)
        return session

    def _restore_settings(self) -> None:
        s = self.settings
        self.apply_hpc_settings()
        geo, state = s.value("window/geometry"), s.value("window/state")
        if geo is not None:
            self.restoreGeometry(geo)
        if state is not None:
            self.restoreState(state)
        q = s.value("modeler/display_quality", "normal")
        if q in ("coarse", "normal", "fine"):
            self.doc.display_quality = q
        v = self.view
        v.grid_plane = {"ZX": "XZ"}.get(s.value("grid/plane", v.grid_plane), s.value("grid/plane", v.grid_plane))
        sp = float(s.value("grid/spacing", 0) or 0)
        v.grid_spacing = sp if sp > 0 else None
        v.show_grid = s.value("grid/show", v.show_grid, type=bool)
        from .viewer import SNAP_MODES
        modes = s.value("snap/modes", None)
        if modes is not None:
            if isinstance(modes, str):
                modes = [m for m in modes.split(",") if m]
            v.snap_modes = {m for m in modes if m in SNAP_MODES}
        elif not s.value("grid/snap_vertices", True, type=bool):
            v.snap_modes = {"grid"}
        v.snap_tolerance = int(s.value("snap/tolerance", v.snap_tolerance))
        self.set_drawing_plane(v.grid_plane)
        self.set_movement_mode(s.value("modeler/movement_mode", "3D"))
        self.a_emlog.setChecked(s.value("general/emerge_log", False, type=bool))

    def _save_window_settings(self) -> None:
        s = self.settings
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/state", self.saveState())
        v = self.view
        s.setValue("grid/plane", v.grid_plane)
        s.setValue("grid/spacing", v.grid_spacing or 0.0)
        s.setValue("grid/show", v.show_grid)
        s.setValue("snap/modes", ",".join(sorted(v.snap_modes)))
        s.setValue("snap/tolerance", int(v.snap_tolerance))
        s.sync()

    def _toggle_emlog(self, v: bool) -> None:
        self.settings.setValue("general/emerge_log", v)
        self.solver.show_emerge_log = v
        self.msgs.show_debug = v

    # ==========================================================================================
    # file
    # ==========================================================================================
    def maybe_save(self) -> bool:
        if not self.doc.dirty:
            return True
        r = QMessageBox.question(self, "Hertzon", "Save changes to the project?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.file_save()
        return True

    def set_project(self, project: Project) -> None:
        self.doc.project = project
        self.doc.design_name = project.designs[0].name
        self.doc.undo_stack.clear()
        self.doc.redo_stack.clear()
        self.doc.selection = []
        self.doc.cache.clear()
        self.doc.design_caches.clear()
        self.doc.dirty = False
        self._close_reports()
        self.view.clear_overlays()
        self.view._first_fit = True
        self.doc.rebuild()
        self.doc.changed.emit("all")
        self._update_title()

    def file_new(self) -> None:
        if self.maybe_save():
            p = Project()
            for d in p.designs:
                d.units = self.settings.value("modeler/default_units", d.units)
            self.set_project(p)

    def file_open(self, path: str | None = None, as_copy: bool = False) -> None:
        """Open a project; as_copy opens it untitled (examples), so saving asks for a new file."""
        if not self.maybe_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", FILE_FILTER)
        if not path:
            return
        auto = Path(path + ".auto")
        load_from = path
        if not as_copy and auto.exists() and auto.stat().st_mtime > Path(path).stat().st_mtime:
            r = QMessageBox.question(self, "Autosave",
                                     f"An autosave of {Path(path).name} is newer than the project file "
                                     "(Hertzon was not closed normally).\n\nOpen the autosaved version?",
                                     QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                load_from = str(auto)
        from PySide6.QtWidgets import QApplication, QProgressDialog
        from ..kernel import occ as K
        prog = QProgressDialog(f"Opening {Path(path).name} ...", "", 0, 0, self)
        prog.setWindowTitle("Open Project")
        prog.setCancelButton(None)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(400)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        import threading
        state = {"text": None}

        def on_prog(frac: float, text: str) -> None:
            if threading.current_thread() is not threading.main_thread():
                state["text"] = text       # Qt only from the GUI thread
                return
            prog.setLabelText(f"Opening {Path(path).name} ...\n{text}")
            QApplication.processEvents()

        def run_heavy(fn) -> None:
            # build/tessellate in a worker thread, the window keeps handling events (no "not responding")
            err = []

            def work():
                try:
                    fn()
                except BaseException as e:  # noqa: BLE001
                    err.append(e)
            th = threading.Thread(target=work, daemon=True)
            th.start()
            while th.is_alive():
                th.join(0.03)
                if state["text"] is not None:
                    prog.setLabelText(f"Opening {Path(path).name} ...\n{state['text']}")
                    state["text"] = None
                QApplication.processEvents()
            if err:
                raise err[0]
        K.PROGRESS = on_prog
        self.doc.run_heavy = run_heavy
        QApplication.processEvents()
        try:
            try:
                proj = Project.load(load_from)
                proj.path = None if as_copy else path
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "Open", f"Cannot open {path}:\n{e}")
                return
            on_prog(0.0, "Building the geometry ...")
            self.set_project(proj)
        finally:
            K.PROGRESS = None
            self.doc.__dict__.pop("run_heavy", None)
            prog.close()
            QApplication.restoreOverrideCursor()
        if load_from != path:
            self.doc.dirty = True
            self._update_title()
            self.msgs.add("warning", f"Recovered from autosave; save to keep it ({path})")
        if as_copy:
            self.msgs.add("info", f"Opened example {Path(path).stem} as a copy; save it to a folder of your own")
            return
        self._add_recent(path)
        self.msgs.add("info", f"Opened {path}")

    def file_save(self) -> bool:
        if not self.doc.project.path:
            return self.file_save_as()
        self.doc.project.save(self.doc.project.path)
        self.doc.dirty = False
        self._drop_autosave()
        self._update_title()
        self.project_tree.rebuild()
        self.msgs.add("info", f"Saved {self.doc.project.path}")
        return True

    # ---- autosave, archives (Auto-save after N edits, Save As Archive) ---------------------------
    def _autosave_path(self) -> Path:
        if self.doc.project.path:
            return Path(self.doc.project.path + ".auto")
        from PySide6.QtCore import QStandardPaths
        d = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or Path.home() / ".hertzon")
        d.mkdir(parents=True, exist_ok=True)
        return d / "Untitled.hzn.auto"

    def _autosave_tick(self, *_):
        if not self.settings.value("autosave/enabled", True, type=bool):
            return
        self._edits = getattr(self, "_edits", 0) + 1
        if self._edits < max(1, int(self.settings.value("autosave/edits", 10))):
            return
        self._edits = 0
        try:
            keep = self.doc.project.path           # an autosave copy must not become the project file
            try:
                self.doc.project.save(self._autosave_path())
            finally:
                self.doc.project.path = keep
        except Exception as e:  # noqa: BLE001
            self.msgs.add("warning", f"Autosave failed: {e}")

    def _drop_autosave(self) -> None:
        try:
            self._autosave_path().unlink(missing_ok=True)
        except OSError:
            pass
        self._edits = 0

    def save_archive(self) -> None:
        """Save As Archive: project plus results in one zip file."""
        import zipfile
        if not self.doc.project.path or self.doc.dirty:
            if not self.file_save():
                return
        src = Path(self.doc.project.path)
        path, _ = QFileDialog.getSaveFileName(self, "Save As Archive", str(src.with_suffix(".hznz")),
                                              "Hertzon archive (*.hznz)")
        if not path:
            return
        r = QMessageBox.question(self, "Save As Archive", "Include the solution data (results)?",
                                 QMessageBox.Yes | QMessageBox.No)
        res = src.parent / (src.name + "results")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(src, src.name)
            if r == QMessageBox.Yes and res.exists():
                for f in res.rglob("*"):
                    if f.is_file():
                        z.write(f, str(f.relative_to(src.parent)))
        self.msgs.add("info", f"Archive written: {path}")

    def restore_archive(self) -> None:
        import zipfile
        path, _ = QFileDialog.getOpenFileName(self, "Restore Archive", "", "Hertzon archive (*.hznz)")
        if not path:
            return
        target = QFileDialog.getExistingDirectory(self, "Restore into folder", str(Path(path).parent))
        if not target:
            return
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            bad = [n for n in names if n.startswith("/") or ".." in Path(n).parts]
            if bad:
                QMessageBox.critical(self, "Restore Archive", "The archive contains unsafe paths; not restored.")
                return
            z.extractall(target)
        proj = next((n for n in names if n.endswith(".hzn") and "/" not in n), None)
        if proj:
            self.file_open(str(Path(target) / proj))

    def new_window(self) -> None:
        """A second main window for another project (several projects open)."""
        w = MainWindow()
        w.show()
        self._windows = [x for x in getattr(self, "_windows", []) if x.isVisible()] + [w]

    # ---- keyboard shortcuts ----------------------------------------------------------------------
    def _apply_shortcuts(self) -> None:
        import json as _json
        try:
            custom = _json.loads(self.settings.value("shortcuts", "{}") or "{}")
        except ValueError:
            custom = {}
        for a in self.findChildren(QAction):
            t = a.text().replace("&", "").strip()
            if t in custom:
                a.setShortcut(QKeySequence(custom[t]))

    def edit_shortcuts(self) -> None:
        import json as _json
        from PySide6.QtWidgets import QKeySequenceEdit
        acts = {}
        for a in self.findChildren(QAction):
            t = a.text().replace("&", "").strip()
            if t and not a.isSeparator() and a.menu() is None and t not in acts:
                acts[t] = a
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        lay = QVBoxLayout(dlg)
        flt = QLineEdit()
        flt.setPlaceholderText("Filter commands")
        lay.addWidget(flt)
        tbl = QTableWidget(len(acts), 2)
        tbl.setHorizontalHeaderLabels(["Command", "Shortcut"])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        edits = {}
        for r, (t, a) in enumerate(sorted(acts.items())):
            tbl.setItem(r, 0, QTableWidgetItem(t))
            e = QKeySequenceEdit(a.shortcut())
            tbl.setCellWidget(r, 1, e)
            edits[t] = e
        flt.textChanged.connect(lambda q: [tbl.setRowHidden(r, q.lower() not in tbl.item(r, 0).text().lower())
                                           for r in range(tbl.rowCount())])
        lay.addWidget(tbl)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(560, 600)
        if dlg.exec() != QDialog.Accepted:
            return
        custom = {}
        for t, e in edits.items():
            seq = e.keySequence().toString()
            if seq != acts[t].shortcut().toString():
                acts[t].setShortcut(QKeySequence(seq))
            if seq:
                custom[t] = seq
        self.settings.setValue("shortcuts", _json.dumps(custom))

    # ---- scripting (Tools > Run Script / Record Script, Python console) ---------------------------
    def script_namespace(self) -> dict:
        from ..model import schema as _schema
        from ..model.design import Command as _Command, Assignment as _Assignment, FaceRef as _FaceRef
        return {"app": self, "doc": self.doc, "project": self.doc.project, "des": self.des, "schema": _schema,
                "Command": _Command, "Assignment": _Assignment, "FaceRef": _FaceRef, "np": np,
                "commit": lambda what="model": self.doc.commit(what)}

    def run_script(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Run Script", "", "Python (*.py)")
        if not path:
            return
        self.doc.checkpoint()
        ns = self.script_namespace()
        try:
            exec(compile(Path(path).read_text(encoding="utf-8"), path, "exec"), ns)  # noqa: S102
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", f"Script {Path(path).name}: {type(e).__name__}: {e}")
        self.doc.commit("all")
        self.msgs.add("info", f"Script {Path(path).name} finished")

    def ai_assistants(self) -> None:
        """Tools > AI Assistants: live link for the MCP server and registration with Claude Code, Codex, VS Code."""
        from .ai_link import AiAssistantsDialog
        AiAssistantsDialog(self).exec()

    def show_console(self) -> None:
        from .console import ConsoleDock
        if getattr(self, "console", None) is None:
            self.console = ConsoleDock(self)
            self.addDockWidget(Qt.BottomDockWidgetArea, self.console)
        self.console.show()
        self.console.raise_()

    def toggle_record(self, on: bool) -> None:
        from .console import ScriptRecorder
        if on:
            self.recorder = ScriptRecorder(self.doc)
            self.msgs.add("info", "Recording script: model changes are written as Python")
            return
        rec = getattr(self, "recorder", None)
        self.recorder = None
        if rec is None:
            return
        rec.stop()
        path, _ = QFileDialog.getSaveFileName(self, "Save Recorded Script", "recorded.py", "Python (*.py)")
        if path:
            Path(path).write_text(rec.text(), encoding="utf-8")
            self.msgs.add("info", f"Script saved: {path}")

    def file_save_as(self) -> bool:
        start = f"{self.doc.project.name}.hzn"
        if not self.doc.project.path:      # untitled or an example copy: not into the working (install) folder
            from PySide6.QtCore import QStandardPaths
            docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
            if docs:
                start = str(Path(docs) / start)
        path, _ = QFileDialog.getSaveFileName(self, "Save Project As", start, "Hertzon project (*.hzn)")
        if not path:
            return False
        if not path.endswith(".hzn"):
            path += ".hzn"
        self.doc.project.name = Path(path).stem
        self.doc.project.save(path)
        self.doc.dirty = False
        self._add_recent(path)
        self._update_title()
        self.project_tree.rebuild()
        return True

    def _fill_examples(self) -> None:
        self.example_menu.clear()
        for p in example_projects():
            self.example_menu.addAction(p.stem, lambda _=False, p=p: self.file_open(str(p), as_copy=True))
        if self.example_menu.isEmpty():
            self.example_menu.addAction("(no examples installed)").setEnabled(False)

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for p in self.settings.value("recent", [], type=list)[:10]:
            self.recent_menu.addAction(p, lambda _=False, p=p: self.file_open(p))

    def _add_recent(self, path: str) -> None:
        rec = [p for p in self.settings.value("recent", [], type=list) if p != path]
        self.settings.setValue("recent", [path] + rec[:9])
        self._fill_recent()

    def closeEvent(self, ev) -> None:
        if self.maybe_save():
            self._save_window_settings()
            if self.solver.busy:
                self.solver.abort()
            self._delete_cloud_server()
            self._drop_autosave()
            self.ai_link.stop()
            ev.accept()
        else:
            ev.ignore()

    def _delete_cloud_server(self) -> None:
        """Hertzon closes: delete the cloud server of this session (its watchdog would do it when idle)."""
        cloud = getattr(self, "_cloud", None)
        if cloud is None or not cloud[1].state():
            return
        import threading
        done = threading.Event()

        def run():
            try:
                cloud[1].delete()
            except Exception:  # noqa: BLE001
                pass                    # the server's watchdog deletes it when idle
            done.set()
        threading.Thread(target=run, daemon=True).start()
        done.wait(15)

    def import_geometry(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import", "", "CAD (*.step *.stp *.iges *.igs *.brep *.stl);;STL (*.stl)")
        if not path:
            return
        import gmsh
        from ..kernel import occ as K
        simp = 0
        if path.lower().endswith(".stl"):
            simp = self._ask_stl_simplify(path)
            if simp is None:
                return
        K.ensure_gmsh("hertzon_import_probe")
        gmsh.model.remove()
        gmsh.model.add("hertzon_import_probe")
        try:
            if path.lower().endswith(".stl"):
                # STL: faceted solids, one per connected shell; coordinates in model units
                # only count the shells here; the solids are built once by the model rebuild below
                bodies = K.import_stl(path, LENGTH_UNITS[self.des.units], count_only=True, simplify=simp)
            else:
                bodies = gmsh.model.occ.importShapes(path, highestDimOnly=True)
            gmsh.model.occ.synchronize()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Import", f"Import failed:\n{e}")
            return
        self.doc.checkpoint()
        base = Path(path).stem
        from PySide6.QtWidgets import QApplication, QProgressDialog
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage(f"Importing {Path(path).name} ({len(bodies)} bodies) ...")
        prog = QProgressDialog(f"Importing {Path(path).name} ...", "", 0, 100 * max(1, len(bodies)) + 1, self)
        prog.setWindowTitle("Import")
        prog.setCancelButton(None)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)
        body = [0]

        def on_prog(frac: float, text: str) -> None:
            if frac == 0 and prog.value() > 0:
                body[0] += 1
            prog.setValue(min(prog.maximum() - 1, 100 * body[0] + int(99 * frac)))
            prog.setLabelText(f"{Path(path).name}: body {min(body[0] + 1, len(bodies))}/{len(bodies)}\n{text}")
            QApplication.processEvents()
        K.PROGRESS = on_prog
        QApplication.processEvents()
        for i, (dim, tag) in enumerate(bodies):
            o = self.des.add_object("Import", name=self.des.unique_name(f"{base}_{i + 1}"), File=path, Body=str(i))
            o.history[0].props["Dim"] = str(dim)
            if simp:
                o.history[0].props["Simplify"] = str(simp)
            o.color = "#a0a0c0"
        try:
            self.doc.commit("model")
        finally:
            K.PROGRESS = None
            self.doc.__dict__.pop("run_heavy", None)
            prog.close()
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
        self.msgs.add("info", f"Imported {len(bodies)} bodies from {path} in {self.doc.last_build_time:.2f} s")
        self.view.fit_all()

    STL_WARN_TRIANGLES = 5000

    def _ask_stl_simplify(self, path: str):
        """Large STL: warn and offer a shape-preserving simplification. Returns the target triangle
        count (0 = keep the original) or None when the import is cancelled."""
        from ..kernel import occ as K
        try:
            V, T = K.read_stl(path)
        except Exception:  # noqa: BLE001
            return 0                               # the import itself reports the error
        n = len(T)
        if n <= self.STL_WARN_TRIANGLES:
            return 0
        try:
            import fast_simplification  # noqa: F401
            have = True
        except Exception:  # noqa: BLE001
            have = False
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox, QHBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Large STL file")
        lay = QVBoxLayout(dlg)
        size = float(np.ptp(V, axis=0).max()) or 1.0
        msg = QLabel(f"<b>{Path(path).name}</b> has <b>{n:,}</b> triangles. Every triangle becomes a CAD face, "
                     "which makes building, opening the project and meshing slow"
                     + (" (more than 40,000 cannot be imported)." if n > 40000 else ".")
                     + "<br><br>Simplify the surface first? Flat and nearly flat regions are merged, edges and "
                     "corners are kept (quadric decimation, fast-simplification).")
        msg.setWordWrap(True)
        lay.addWidget(msg)
        row = QHBoxLayout()
        row.addWidget(QLabel("Target triangles:"))
        spin = QSpinBox()
        spin.setRange(100, max(100, n - 1))
        spin.setSingleStep(500)
        spin.setValue(max(100, min(n - 1, max(2000, n // 4))))
        row.addWidget(spin)
        lay.addLayout(row)
        dev = QLabel("")
        lay.addWidget(dev)
        if not have:
            dev.setText("<span style='color:#b35c00'>fast-simplification is not installed "
                        "(.venv\\Scripts\\python -m pip install fast-simplification).</span>")
            spin.setEnabled(False)

        def update_dev():
            if not have:
                return
            try:
                V2, T2 = K.simplify_stl(V, T, spin.value())
                d = K.stl_deviation(V, T, V2, T2)
                dev.setText(f"Largest shape deviation: {d:.4g} {self.des.units} "
                            f"({100 * d / size:.3g} % of the part size)")
            except Exception as e:  # noqa: BLE001
                dev.setText(f"Preview failed: {e}")
        spin.editingFinished.connect(update_dev)
        update_dev()
        bb = QDialogButtonBox()
        b_simp = bb.addButton("Simplify", QDialogButtonBox.AcceptRole)
        b_keep = bb.addButton("Keep original", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        b_simp.setEnabled(have)
        b_keep.setEnabled(n <= 40000)
        choice = {"v": None}
        b_simp.clicked.connect(lambda: (choice.update(v=spin.value()), dlg.accept()))
        b_keep.clicked.connect(lambda: (choice.update(v=0), dlg.accept()))
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return None
        return choice["v"]

    def export_step(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export STEP", f"{self.des.name}.step", "STEP (*.step)")
        if not path:
            return
        import gmsh
        from ..kernel import occ as K
        K.ensure_gmsh("hertzon_export")
        gmsh.model.remove()
        gmsh.model.add("hertzon_export")
        K.build_design(self.des, self.doc.evaluator())
        gmsh.write(path)
        self.msgs.add("info", f"Exported {path}")

    def export_gerber(self) -> None:
        """File > Export > Gerber: copper layers (Gerber X2), board outline and Excellon drill files."""
        from .gerber_dialog import GerberExportDialog
        GerberExportDialog(self, self.doc).exec()

    def export_touchstone(self, sol: str | None = None) -> None:
        sols = [x for x in solution_list(self.doc) if "AdaptivePasses" not in x]
        if not sols:
            QMessageBox.information(self, "Export", "No solution data available. Analyze a setup first.")
            return
        if sol is None:
            pref = [x for x in sols if not x.endswith("LastAdaptive")] + [x for x in sols if x.endswith("LastAdaptive")]
            sol, ok = QInputDialog.getItem(self, "Export S-Parameters", "Solution:", pref, 0, False)
            if not ok:
                return
        from .reports import load_solution
        st, s, data = load_solution(self.doc, sol)
        if data is None or data.get("ports_only"):
            self.msgs.add("warning", f"{sol}: only the ports were solved, there are no S-parameters to export")
            return
        n = data["S"].shape[1]
        path, _ = QFileDialog.getSaveFileName(self, "Export Touchstone", f"{self.des.name}_{s}.s{n}p",
                                              f"Touchstone (*.s{n}p)")
        if path:
            st.write_touchstone(s, data["freq"], data["S"], data["z0"], path)
            self.msgs.add("info", f"Exported {path}")

    def export_script(self) -> None:
        from ..solver.script import design_to_script
        path, _ = QFileDialog.getSaveFileName(self, "Export EMerge Script", f"{self.des.name}.py", "Python (*.py)")
        if path:
            Path(path).write_text(design_to_script(self.doc.project, self.des.name), encoding="utf-8")
            self.msgs.add("info", f"Wrote {path}")

    # ==========================================================================================
    # drawing / modeling
    # ==========================================================================================
    def _arrange_menu(self, arr, _dup=None):
        arr.addAction("Move", lambda: self.start_op("OpMove"))
        arr.addAction("Rotate...", lambda: self.transform("Rotate"))
        arr.addAction("Mirror", lambda: self.start_op("OpMirror"))
        arr.addSeparator()
        arr.addAction("Move by Vector...", lambda: self.transform("Move"))
        arr.addAction("Mirror by Dialog...", lambda: self.transform("Mirror"))
        return arr

    def _dup_menu(self, parent):
        dup = parent.addMenu("Duplicate")
        dup.addAction("Along Line", lambda: self.start_op("OpDupLine"))
        dup.addAction("Around Axis...", self.duplicate_axis)
        dup.addAction("Mirror", lambda: self.start_op("OpDupMirror"))
        dup.addSeparator()
        dup.addAction("Along Line by Vector...", self.duplicate_line)
        dup.addAction("Mirror by Dialog...", self.duplicate_mirror)
        return dup

    def start_op(self, kind: str) -> None:
        """interactive Move / Duplicate Along Line / Mirror: click two points (snaps, Tab work)."""
        objs = self.selected_objects(1)
        if not objs:
            return
        self._op_objs = list(objs)
        self.view.start_draw(kind)
        self.view.draw.ghost = list(objs)
        self.tabs.setCurrentIndex(0)
        self.view.plotter.interactor.setFocus()

    def _op_finished(self, kind: str, P: list) -> None:
        objs = getattr(self, "_op_objs", [])
        if not objs:
            return
        a, b = P[0], P[1]
        v = b - a
        if np.linalg.norm(v) < 1e-15:
            self.msgs.add("warning", "The two points coincide; nothing done")
            return
        vec = ",".join(self._fmt(x) for x in v)
        if kind == "OpMove":
            self.add_command_to_selection("Move", {"Vector": vec}, objs)
        elif kind == "OpMirror":
            self.add_command_to_selection("Mirror", {"Base": self._pt(a), "Normal": vec}, objs)
        elif kind == "OpDupMirror":
            self._duplicate(objs, [[Command("Mirror", {"Base": self._pt(a), "Normal": vec})]])
        else:
            n, ok = QInputDialog.getInt(self, "Duplicate Along Line", "Total number:", 2, 2, 10000)
            if not ok:
                return
            parts = vec.split(",")
            self._duplicate(objs, [[Command("Move", {"Vector": ",".join(f"({p})*{i}" for p in parts)})]
                                   for i in range(1, n)])

    def start_draw(self, kind: str) -> None:
        self.set_mode("object")
        self._line_only = kind == "CreatePolyline:line"
        k = "CreatePolyline" if kind.startswith("CreatePolyline") else kind
        self.view.start_draw(k)
        if self._line_only:
            self.view.draw.phases = 2
        elif kind in ("CreatePolyline:arc3", "CreatePolyline:arccenter"):
            # one arc: start, then middle and end (3 point) or center and end angle (center point)
            self.view.draw.seg_mode = "Arc" if kind.endswith("arc3") else "CenterArc"
            self.view.draw.phases = 3
            self.view.drawPhaseChanged.emit(self.view._phase_hint())
        self.tabs.setCurrentIndex(0)
        self.view.plotter.interactor.setFocus()

    def _coord_sys_changed(self, cs: str) -> None:
        labels = {"Cartesian": ("X:", "Y:", "Z:"), "Cylindrical": ("R:", "Phi:", "Z:"),
                  "Spherical": ("Rho:", "Theta:", "Phi:")}[cs]
        for lb, t in zip(self.coord_labels, labels):
            lb.setText(t)

    def _cursor_moved(self, p) -> None:
        if p is None:
            return
        s = LENGTH_UNITS[self.des.units]
        last = self.view.draw.points[-1] if self.view.draw and self.view.draw.points else None
        if self.coord_mode.currentText() == "Relative" and last is not None:
            p = np.asarray(p) - last
        x, y, z = (float(c) for c in p)
        cs = self.coord_sys.currentText()
        if cs == "Cylindrical":
            vals = (f"{math.hypot(x, y) / s:.6g}", f"{math.degrees(math.atan2(y, x)):.6g}deg", f"{z / s:.6g}")
        elif cs == "Spherical":
            r = math.sqrt(x * x + y * y + z * z)
            th = math.degrees(math.acos(z / r)) if r > 0 else 0.0
            vals = (f"{r / s:.6g}", f"{th:.6g}deg", f"{math.degrees(math.atan2(y, x)):.6g}deg")
        else:
            vals = (f"{x / s:.6g}", f"{y / s:.6g}", f"{z / s:.6g}")
        for w, v in zip((self.cx, self.cy, self.cz), vals):
            if not w.hasFocus():
                w.setText(v)

    def _coord_tab(self) -> None:
        """Tab while drawing: type the next point."""
        self.cx.setFocus()
        self.cx.selectAll()

    def _coord_entered(self) -> None:
        if self.view.draw is None:
            return
        try:
            ev = self.doc.evaluator()
            a, b, c = (w.text() for w in (self.cx, self.cy, self.cz))
            cs = self.coord_sys.currentText()
            if cs == "Cylindrical":
                r, ph, z = ev.length(a), ev.angle(b), ev.length(c)
                p = np.array([r * math.cos(ph), r * math.sin(ph), z])
            elif cs == "Spherical":
                r, th, ph = ev.length(a), ev.angle(b), ev.angle(c)
                p = np.array([r * math.sin(th) * math.cos(ph), r * math.sin(th) * math.sin(ph), r * math.cos(th)])
            else:
                p = np.array([ev.length(a), ev.length(b), ev.length(c)])
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", f"Invalid coordinate: {e}")
            return
        if self.coord_mode.currentText() == "Relative" and self.view.draw.points:
            p = self.view.draw.points[-1] + p
        self.view.add_draw_point(p)
        self.view.plotter.interactor.setFocus()

    def _draw_finished(self, kind: str, pts: list) -> None:
        P = [np.asarray(p, float) for p in pts]
        if kind == "IntegrationLine":
            self._line_drawn(P)
            return
        if kind == "PlaceComponent":
            self._component_placed(P[0])
            return
        if kind.startswith("Op"):
            self._op_finished(kind, P)
            return
        if kind.startswith("CS"):
            self._cs_drawn(kind, P)
            return
        plane = self.view.grid_plane
        n = PLANE_NORMAL[plane]
        axis = AXIS_OF_PLANE[plane]
        if kind.startswith("Measure"):
            s = LENGTH_UNITS[self.des.units]
            u = self.des.units
            f = lambda x: f"{x / s:.6g}"  # noqa: E731
            if kind == "MeasurePosition":
                rows = [("Position", f"({f(P[0][0])}, {f(P[0][1])}, {f(P[0][2])}) {u}")]
            elif kind == "MeasureAngle":
                a, b = P[0] - P[1], P[2] - P[1]
                na, nb = np.linalg.norm(a), np.linalg.norm(b)
                ang = math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b) / (na * nb))))) if na * nb > 0 else 0.0
                rows = [("Angle", f"{ang:.6g} deg"), ("Vertex", f"({f(P[1][0])}, {f(P[1][1])}, {f(P[1][2])}) {u}")]
            else:
                d = P[1] - P[0]
                rows = [("Distance", f"{f(np.linalg.norm(d))} {u}"), ("dX", f"{f(d[0])} {u}"),
                        ("dY", f"{f(d[1])} {u}"), ("dZ", f"{f(d[2])} {u}"),
                        ("Point 1", f"({f(P[0][0])}, {f(P[0][1])}, {f(P[0][2])})"),
                        ("Point 2", f"({f(P[1][0])}, {f(P[1][1])}, {f(P[1][2])})")]
            self.show_measure(kind.replace("Measure", "") or "Distance", rows)
            return
        r = lambda a, b: float(np.linalg.norm(b - a))  # noqa: E731
        props: dict[str, str] = {}
        closed = kind.endswith(":closed")
        kind = kind.split(":")[0]
        try:
            if kind == "CreateBox":
                d = P[1] - P[0]
                h = (P[2] - P[1])[n]
                sizes = [d[0], d[1], d[2]]
                sizes[n] = h
                if min(abs(x) for x in sizes) < 1e-15:
                    raise ValueError("box with zero size")
                props = dict(Position=self._pt(P[0]), XSize=self._fmt(sizes[0]), YSize=self._fmt(sizes[1]),
                             ZSize=self._fmt(sizes[2]))
            elif kind == "CreateCylinder":
                props = dict(CenterPosition=self._pt(P[0]), Axis=axis, Radius=self._fmt(r(P[0], P[1])),
                             Height=self._fmt((P[2] - P[1])[n]))
            elif kind == "CreateCone":
                props = dict(CenterPosition=self._pt(P[0]), Axis=axis, LowerRadius=self._fmt(r(P[0], P[1])),
                             UpperRadius=self._fmt(r(P[0], P[2])), Height=self._fmt((P[3] - P[2])[n]))
            elif kind == "CreateSphere":
                props = dict(CenterPosition=self._pt(P[0]), Radius=self._fmt(r(P[0], P[1])))
            elif kind == "CreateTorus":
                props = dict(CenterPosition=self._pt(P[0]), Axis=axis, MajorRadius=self._fmt(r(P[0], P[1])),
                             MinorRadius=self._fmt(r(P[1], P[2])))
            elif kind == "CreateRegularPolyhedron":
                props = dict(CenterPosition=self._pt(P[0]), StartPosition=self._pt(P[1]), Axis=axis,
                             Height=self._fmt((P[2] - P[1])[n]), NumberOfSides="6")
            elif kind == "CreateRectangle":
                from ..kernel.occ import AXIS_UV
                u, v = (np.asarray(a, float) for a in AXIS_UV[axis])
                d = P[1] - P[0]
                if abs(d @ u) < 1e-15 or abs(d @ v) < 1e-15:
                    raise ValueError("rectangle with zero size")
                sa, sb = schema.RECT_SIZES[axis]
                props = {"Position": self._pt(P[0]), "Axis": axis, sa: self._fmt(d @ u), sb: self._fmt(d @ v)}
            elif kind == "CreateCircle":
                props = dict(CenterPosition=self._pt(P[0]), Axis=axis, Radius=self._fmt(r(P[0], P[1])))
            elif kind == "CreateEllipse":
                a = r(P[0], P[1])
                props = dict(CenterPosition=self._pt(P[0]), Axis=axis, MajorRadius=self._fmt(a),
                             Ratio=f"{r(P[0], P[2]) / max(a, 1e-30):.6g}")
            elif kind == "CreateRegularPolygon":
                props = dict(CenterPosition=self._pt(P[0]), StartPosition=self._pt(P[1]), Axis=axis,
                             NumberOfSides="6")
            elif kind == "CreatePolyline":
                props = dict(Points=";".join(self._pt(p) for p in P), Closed=str(closed), Covered=str(closed))
                seg = getattr(self.view, "last_segment_types", "")
                if seg:
                    props["SegmentTypes"] = seg
            if any(v in ("0", "-0") for k, v in props.items() if k in ("Radius", "Height", "MajorRadius")):
                raise ValueError("zero size")
        except Exception as e:  # noqa: BLE001
            self.msgs.add("warning", f"Primitive not created: {e}")
            return
        model = self._model_flag_for_new()
        if model is None:
            return
        self.doc.checkpoint()
        o = self.des.add_object(kind, **props)
        self._apply_new_material(o)
        o.orientation = self.des.working_cs
        o.model = model
        o.solve_inside = o.solve_inside and model
        self.doc.commit("model")
        self.doc.select([Pick(o.name)])
        self.msgs.add("info", f"Created {o.name}" + ("" if model else " (Non Model)"))
        if self.edit_new_primitives:
            self.edit_object_dialog(o.name, created=True)

    def create_by_dialog(self, kind: str | None = None) -> None:
        if not kind:
            kinds = [k for k in schema.CREATE_COMMANDS if k not in ("Import", "CreateRegion")]
            kind, ok = QInputDialog.getItem(self, "Create Primitive", "Primitive:", kinds, 0, False)
            if not ok:
                return
        model = self._model_flag_for_new()
        if model is None:
            return
        self.doc.checkpoint()
        o = self.des.add_object(kind)
        self._apply_new_material(o)
        o.orientation = self.des.working_cs
        o.model = model
        o.solve_inside = o.solve_inside and model
        self.doc.commit("model")
        self.doc.select([Pick(o.name)])
        self.edit_object_dialog(o.name, created=True)

    def edit_object_dialog(self, name: str, created: bool = False) -> None:
        o = self.des.obj(name)
        dlg = ObjectPropertiesDialog(self.doc, o, self)
        if dlg.exec() != QDialog.Accepted:
            return
        if not created:
            self.doc.checkpoint()
        a = dlg.attr_values
        o.history[0].props.update(dlg.cmd_values)
        new = a.get("Name", o.name).strip()
        if new and new != o.name and new not in self.des.all_names():
            rename_object(self.des, o.name, new)
        o.material = a.get("Material", o.material)
        o.model = a.get("Model") == "True"
        o.solve_inside = a.get("SolveInside") == "True" and o.model
        o.color = normalize_color(a.get("Color", o.color), o.color)
        try:
            o.transparency = float(a.get("Transparency", "0"))
        except ValueError:
            pass
        o.wireframe = a.get("DisplayWireframe") == "True"
        o.orientation = a.get("Orientation", o.orientation) or "Global"
        self.doc.commit("model")

    def edit_command(self, obj: str, index: int) -> None:
        if index < 0:
            self.edit_object_dialog(obj)
            return
        o = self.des.obj(obj)
        c = o.history[index]
        dlg = NamedPropsDialog(f"Properties: {obj} - {c.kind}", c.kind, schema.GEOMETRY.get(c.kind, []), c.props,
                               self.doc.evaluator(), self._materials(), self)
        dlg.name_edit.setEnabled(False)
        if dlg.exec() == QDialog.Accepted:
            self.doc.checkpoint()
            c.props = dlg.values
            self.doc.commit("model")

    def create_region(self) -> None:
        p = schema.defaults(schema.GEOMETRY["CreateRegion"])
        dlg = NamedPropsDialog("Region", "Region", schema.GEOMETRY["CreateRegion"], p, self.doc.evaluator(),
                               parent=self, info="Air region around all model objects (padding per direction).")
        if dlg.exec() != QDialog.Accepted:
            return
        self.doc.checkpoint()
        o = self.des.add_object("CreateRegion", name=self.des.unique_name(dlg.name or "Region"), **dlg.values)
        self.doc.commit("model")
        self.msgs.add("info", f"Created {o.name}. Assign a Radiation or PML boundary to its faces, or use Auto Open.")

    def selected_objects(self, need: int = 1) -> list[str]:
        objs = self.doc.selected_objects()
        if len(objs) < need:
            QMessageBox.information(self, "Selection", f"Please select at least {need} object(s) first.")
            return []
        return objs

    def boolean(self, op: str) -> None:
        objs = self.selected_objects(2)
        if not objs:
            return
        blanks, tools, keep = objs[:1], objs[1:], False
        if op == "Subtract":
            dlg = SubtractDialog(blanks, tools, self)
            if dlg.exec() != QDialog.Accepted:
                return
            blanks, tools, keep = dlg.blanks(), dlg.tools(), dlg.clone.isChecked()
        self.doc.checkpoint()
        consumed = []
        for i, blank in enumerate(blanks):
            b = self.des.obj(blank)
            if keep or i < len(blanks) - 1:
                # every blank part is cut by all tools: clones for all but the last (or for all with "Clone")
                use = []
                for t in tools:
                    c = copy.deepcopy(self.des.obj(t))
                    c.name = self.des.unique_name(t + "_tool")
                    c.consumed_by = blank
                    self.des.objects.append(c)
                    use.append(c.name)
            else:
                use = tools
                for t in tools:
                    self.des.obj(t).consumed_by = blank
                consumed = tools
            b.history.append(Command.new(op, Tools=",".join(use), KeepOriginals=str(keep)))
        self._drop_refs(consumed)
        self.doc.commit("model")
        self.doc.select([Pick(n) for n in blanks])

    def _drop_refs(self, objs: list[str]) -> None:
        """Remove boundary/excitation references to objects that no longer exist on their own."""
        objs = set(objs)
        for lst in (self.des.boundaries, self.des.excitations, self.des.mesh_ops):
            for a in list(lst):
                a.objects = [o for o in a.objects if o not in objs]
                a.faces = [f for f in a.faces if f.obj not in objs]
                if not a.objects and not a.faces and a.kind != "Incident Plane Wave":
                    lst.remove(a)
                    self.msgs.add("warning", f"'{a.name}' removed (its geometry was deleted)")
        for p in list(self.des.field_plots):
            if p.plane or p.quantity == "RadiationField" or not (p.objects or p.faces):
                continue
            p.objects = [o for o in p.objects if o not in objs]
            p.faces = [f for f in p.faces if f.obj not in objs]
            if not p.objects and not p.faces:
                # a plot on deleted surfaces or objects goes with them (undo brings both back)
                self.des.field_plots.remove(p)
                self.msgs.add("warning", f"Field overlay '{p.name}' removed (its geometry was deleted)")

    def transform(self, kind: str) -> None:
        objs = self.selected_objects(1)
        if not objs:
            return
        props = schema.GEOMETRY[kind]
        vals = schema.defaults(props)
        if "CoordinateSystem" in vals:      # Split, Rotate, Around Axis: given in the working coordinate system
            vals["CoordinateSystem"] = self.des.working_cs
        dlg = NamedPropsDialog(kind, kind, props, vals, self.doc.evaluator(), parent=self,
                               info=f"Apply to: {', '.join(objs)}")
        dlg.name_edit.setEnabled(False)
        if kind == "ThickenSheet":          # arrows in the 3D view show where the sheets grow
            def arrows(*_):
                self.view.show_thicken_arrows(objs, dlg.values.get("ReverseDirection") == "True",
                                              dlg.values.get("BothSides") == "True")
            dlg.grid.changed.connect(arrows)
            arrows()
        try:
            accepted = dlg.exec() == QDialog.Accepted
        finally:
            if kind == "ThickenSheet":
                self.view.clear_thicken_arrows()
        if not accepted:
            return
        self.add_command_to_selection(kind, dlg.values, objs)

    def add_command_to_selection(self, kind: str, values: dict, objs: list[str] | None = None) -> None:
        objs = objs or self.selected_objects(1)
        if not objs:
            return
        self.doc.checkpoint()
        for o in objs:
            self.des.obj(o).history.append(Command(kind, dict(values)))
        self.doc.commit("model")         # integration lines follow by themselves (model.objframe)

    def chamfer(self) -> None:
        """Solids: chamfer along the selected edges. Sheets: a chamfer at each selected vertex (corner)."""
        sel = self._edge_or_vertex_selection("Chamfer", "chamfered")
        if sel:
            obj, key, ids = sel
            r, ok = QInputDialog.getText(self, "Chamfer", "Distance:", text="0.5")
            if ok:
                self._edge_op("Chamfer", {"Distance": r, key: ids}, obj)

    def _edge_op(self, kind: str, values: dict, obj: str) -> None:
        """Fillet/Chamfer: if the object can not be built with the new operation, it stays unchanged."""
        self.add_command_to_selection(kind, values, [obj])
        err = self.doc.errors.get(obj)
        if err:
            self.doc.discard_last()
            QMessageBox.warning(self, kind, f"{kind} could not be applied, '{obj}' is unchanged.\n\n{err}")

    def two_object_op(self, kind: str) -> None:
        """Sweep Along Path (profile, then path), Connect (two sheets or lines), Imprint (blank, tool).
        The second object is consumed."""
        objs = self.selected_objects(2)
        if not objs:
            return
        if len(objs) != 2:
            QMessageBox.information(self, kind, "Select exactly two objects (first the profile/blank).")
            return
        a, b = objs
        if kind == "SweepAlongPath" and self.des.obj(b).dim != 1 and self.des.obj(a).dim == 1:
            a, b = b, a
        self.doc.checkpoint()
        self.des.obj(b).consumed_by = a
        self.des.obj(a).history.append(Command.new(kind, Tools=b))
        self._drop_refs([b])
        self.doc.commit("model")
        if a in self.doc.errors:
            self.msgs.add("error", f"{kind}: {self.doc.errors[a]}")
        self.doc.select([Pick(a)])

    def _selected_faces(self):
        if self.doc.selection_mode != "face" or not self.doc.selection:
            QMessageBox.information(self, "Faces", "Select faces (press F) of one object first.")
            return None, ""
        obj = self.doc.selection[0].obj
        return obj, ",".join(str(s.index) for s in self.doc.selection if s.obj == obj and s.index >= 0)

    def face_op(self, kind: str) -> None:
        obj, faces = self._selected_faces()
        if not obj:
            return
        vals = {"Faces": faces}
        if kind == "SweepFacesAlongNormal":
            d, ok = QInputDialog.getText(self, "Sweep Faces Along Normal", "Length of sweep:", text="1")
            if not ok:
                return
            vals["Distance"] = d
        self.add_command_to_selection(kind, vals, [obj])

    def detach_faces(self) -> None:
        """Detach Faces: the faces become new sheet objects, the object keeps the rest."""
        obj, faces = self._selected_faces()
        if not obj:
            return
        self.doc.checkpoint()
        src = self.des.obj(obj)
        new = []
        for f in faces.split(","):
            c = copy.deepcopy(src)
            c.name = self.des.unique_name(f"{obj}_ObjectFromFace")
            c.history.append(Command("KeepFaces", {"Faces": f}))
            self.des.objects.append(c)
            new.append(c.name)
        src.history.append(Command("UncoverFaces", {"Faces": faces}))
        self.doc.commit("model")
        self.msgs.add("info", f"Detached {', '.join(new)}")

    def separate_bodies(self) -> None:
        """Separate Bodies: one object per disjoint body."""
        objs = self.selected_objects(1)
        if not objs:
            return
        self.doc.checkpoint()
        made = []
        for name in objs:
            n = self._body_count(name)
            if n < 2:
                continue
            src = self.des.obj(name)
            for i in range(1, n):
                c = copy.deepcopy(src)
                c.name = self.des.unique_name(f"{name}_Separate{i}")
                c.history.append(Command("KeepBody", {"Body": str(i)}))
                self.des.objects.append(c)
                made.append(c.name)
            src.history.append(Command("KeepBody", {"Body": "0"}))
        self.doc.commit("model")
        self.msgs.add("info", f"Separated into {', '.join(made)}" if made else "Nothing to separate: one body each")

    def _body_count(self, name: str) -> int:
        from ..kernel import occ as K
        import gmsh
        K.ensure_gmsh("hertzon_bodies")
        gmsh.model.remove()
        gmsh.model.add("hertzon_bodies")
        ctx = K.build_design(self.des, self.doc.evaluator(), names=[name])
        dt = ctx.built.get(name, [])
        top = max((d for d, _ in dt), default=0)
        n = len([d for d in dt if d[0] == top])
        gmsh.model.remove()
        K.ensure_gmsh("hertzon_gui")
        return n

    def section(self) -> None:
        """Section: cross section sheets of the selected objects with a plane (originals stay)."""
        objs = self.selected_objects(1)
        if not objs:
            return
        props = schema.GEOMETRY["Section"]
        dlg = NamedPropsDialog("Section", "Section", props, schema.defaults(props), self.doc.evaluator(), parent=self)
        dlg.name_edit.setEnabled(False)
        if dlg.exec() != QDialog.Accepted:
            return
        self.doc.checkpoint()
        for name in objs:
            c = copy.deepcopy(self.des.obj(name))
            c.name = self.des.unique_name(f"{name}_Section1")
            c.history.append(Command("Section", dict(dlg.values)))
            self.des.objects.append(c)
        self.doc.commit("model")

    def fillet(self) -> None:
        """Solids: fillet along the selected edges. Sheets: a fillet at each selected vertex (corner)."""
        sel = self._edge_or_vertex_selection("Fillet", "filleted")
        if sel:
            obj, key, ids = sel
            r, ok = QInputDialog.getText(self, "Fillet", "Radius:", text="0.5")
            if ok:
                self._edge_op("Fillet", {"Radius": r, key: ids}, obj)

    def _edge_or_vertex_selection(self, title: str, done: str):
        """Fillet/Chamfer: (object, "Edges" or "Vertices", indices) of the selection, edges of a solid or
        vertices of a sheet; None (with a hint) otherwise."""
        mode = self.doc.selection_mode
        if mode not in ("edge", "vertex") or not self.doc.selection:
            QMessageBox.information(self, title, "Select edges (press E) of a solid or vertices (press V) "
                                                 "of a sheet first.")
            return None
        obj = self.doc.selection[0].obj
        t = self.doc.tess.get(obj)
        sheet = t is not None and t.dim == 2
        if mode == "edge" and sheet:
            QMessageBox.information(self, title, f"A sheet is {done} at its corners: select the vertices "
                                                 "(press V).")
            return None
        if mode == "vertex" and not sheet:
            QMessageBox.information(self, title, f"Vertices can be {done} on sheets. For a solid select "
                                                 "the edges (press E).")
            return None
        key = "Edges" if mode == "edge" else "Vertices"
        return obj, key, ",".join(str(s.index) for s in self.doc.selection if s.obj == obj)

    def _duplicate(self, objs: list[str], cmds_for_copy) -> None:
        self.doc.checkpoint()
        new = []
        copies: list[dict[str, str]] = [{} for _ in cmds_for_copy]
        for name in objs:
            src = self.des.obj(name)
            for i, cmds in enumerate(cmds_for_copy, start=1):
                c = copy.deepcopy(src)
                c.name = self.des.unique_name(f"{name}_{i}")
                c.history += cmds
                tool_map = {}
                for cmd in c.history:
                    if "Tools" in cmd.props:
                        tools = []
                        for t in [x.strip() for x in cmd.props["Tools"].split(",") if x.strip()]:
                            tc = copy.deepcopy(self.des.obj(t))
                            tc.name = self.des.unique_name(t + f"_{i}")
                            tc.consumed_by = c.name
                            self.des.objects.append(tc)
                            tool_map[t] = tc.name
                            tools.append(tc.name)
                        cmd.props["Tools"] = ",".join(tools)
                self.des.objects.append(c)
                new.append(c.name)
                copies[i - 1][name] = c.name
        for cp in copies:               # every copy of a group is a group of its own
            G.copy_groups(self.des, cp)
        self.doc.commit("model")
        self.msgs.add("info", f"Created {', '.join(new)}")

    def duplicate_line(self) -> None:
        objs = self.selected_objects(1)
        if not objs:
            return
        dlg = NamedPropsDialog("Duplicate Along Line", "Duplicate", [schema.P("Vector", "vector", "1,0,0"),
                                                                     schema.P("TotalNumber", "int", "2")],
                               {"Vector": "1,0,0", "TotalNumber": "2"}, self.doc.evaluator(), parent=self)
        dlg.name_edit.setEnabled(False)
        if dlg.exec() != QDialog.Accepted:
            return
        n = max(2, int(self.doc.evaluator().integer(dlg.values["TotalNumber"])))
        vec = dlg.values["Vector"]
        parts = [p.strip() for p in vec.split(",")]
        cmds = [[Command("Move", {"Vector": ",".join(f"({p})*{i}" for p in parts)})] for i in range(1, n)]
        self._duplicate(objs, cmds)

    def duplicate_axis(self) -> None:
        objs = self.selected_objects(1)
        if not objs:
            return
        vals = {"Axis": "Z", "Angle": "90deg", "TotalNumber": "2", "CoordinateSystem": self.des.working_cs}
        dlg = NamedPropsDialog("Duplicate Around Axis", "Duplicate",
                               [schema.P("Axis", "choice", "Z", choices=schema.AXES), schema.P("Angle", "angle", "90deg"),
                                schema.P("TotalNumber", "int", "2"), schema.AXIS_CS], vals, self.doc.evaluator(),
                               parent=self)
        dlg.name_edit.setEnabled(False)
        if dlg.exec() != QDialog.Accepted:
            return
        n = max(2, int(self.doc.evaluator().integer(dlg.values["TotalNumber"])))
        cmds = [[Command("Rotate", {"Axis": dlg.values["Axis"], "Angle": f"({dlg.values['Angle']})*{i}",
                                    "Center": "0,0,0", "CoordinateSystem": dlg.values["CoordinateSystem"]})]
                for i in range(1, n)]
        self._duplicate(objs, cmds)

    def duplicate_mirror(self) -> None:
        objs = self.selected_objects(1)
        if not objs:
            return
        props = schema.GEOMETRY["Mirror"]
        dlg = NamedPropsDialog("Duplicate Mirror", "Mirror", props, schema.defaults(props), self.doc.evaluator(),
                               parent=self)
        dlg.name_edit.setEnabled(False)
        if dlg.exec() == QDialog.Accepted:
            self._duplicate(objs, [[Command("Mirror", dict(dlg.values))]])

    def copy_objects(self) -> None:
        objs = self.doc.selected_objects()
        self.clipboard = []
        for n in objs:
            o = self.des.obj(n)
            pack = {"obj": copy.deepcopy(o), "tools": []}
            for c in o.history:
                for t in [x.strip() for x in c.props.get("Tools", "").split(",") if x.strip()]:
                    if self.des.has_obj(t):
                        pack["tools"].append(copy.deepcopy(self.des.obj(t)))
            self.clipboard.append(pack)
        if objs:
            self.msgs.add("info", f"Copied {', '.join(objs)}")

    def paste_objects(self) -> None:
        if not self.clipboard:
            return
        self.doc.checkpoint()
        new = []
        copies = {}
        for pack in self.clipboard:
            o = copy.deepcopy(pack["obj"])
            o.name = self.des.unique_name(o.name)
            copies[pack["obj"].name] = o.name
            mapping = {}
            for t in pack["tools"]:
                tc = copy.deepcopy(t)
                tc.name = self.des.unique_name(t.name)
                tc.consumed_by = o.name
                mapping[t.name] = tc.name
                self.des.objects.append(tc)
            for c in o.history:
                if "Tools" in c.props:
                    c.props["Tools"] = ",".join(mapping.get(x.strip(), x.strip()) for x in c.props["Tools"].split(",")
                                                if x.strip())
            self.des.objects.append(o)
            new.append(o.name)
        G.copy_groups(self.des, copies)
        self.doc.commit("model")
        self.doc.select([Pick(n) for n in new])

    def delete_selection(self) -> None:
        if self.view.draw is not None:
            return
        focus = QApplication.focusWidget()
        if focus is self.project_tree or (focus and self.project_tree.isAncestorOf(focus)):
            self._delete_project_items()
            return
        objs = self.doc.selected_objects()
        if not objs:
            return
        self.doc.checkpoint()
        dead = set(objs)
        # tools consumed by deleted objects go too
        changed = True
        while changed:
            changed = False
            for o in self.des.objects:
                if o.consumed_by in dead and o.name not in dead:
                    dead.add(o.name)
                    changed = True
        self.des.objects = [o for o in self.des.objects if o.name not in dead]
        self._drop_refs(list(dead))
        self.doc.selection = []
        self.doc.commit("model")
        self.doc.selectionChanged.emit()
        self.msgs.add("info", f"Deleted {', '.join(objs)}")

    def group_selection(self) -> None:
        """Edit > Group: the selected objects (also of different kinds) become one group."""
        objs = self.doc.selected_objects() if self.doc.selection_mode == "object" else []
        if len(G.expand(self.des, objs)) < 2:
            QMessageBox.information(self, "Group", "Please select at least two objects to group.")
            return
        self.doc.checkpoint()
        name = G.make_group(self.des, objs)
        self.doc.commit("model", geometry=False)
        self.doc.select([Pick(n) for n in G.members(self.des, name)])
        self.msgs.add("info", f"Grouped {', '.join(G.members(self.des, name))} as '{name}'")

    def ungroup_selection(self, names: list[str] | None = None) -> None:
        """Edit > Ungroup: dissolves the groups of the selected objects; the objects stay selected."""
        objs = names or self.doc.selected_objects()
        if not any(self.des.has_obj(n) and self.des.obj(n).group or n in G.groups(self.des) for n in objs):
            QMessageBox.information(self, "Ungroup", "The selection contains no group.")
            return
        sel = G.expand(self.des, [n for n in objs if self.des.has_obj(n)]) or \
            [m for n in objs for m in G.members(self.des, n)]
        self.doc.checkpoint()
        gone = G.ungroup(self.des, objs)
        self.doc.commit("model", geometry=False)
        self.doc.select([Pick(n) for n in sel])
        self.msgs.add("info", f"Ungrouped {', '.join(gone)}")

    def rename_group(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Rename Group", "Name:", text=name)
        if not ok or new.strip() == name:
            return
        self.doc.checkpoint()
        try:
            G.rename_group(self.des, name, new)
        except ValueError as e:
            self.doc.undo_stack.pop()
            QMessageBox.warning(self, "Rename Group", str(e))
            return
        self.doc.commit("model", geometry=False)

    def select_all(self) -> None:
        self.set_mode("object")
        self.doc.select([Pick(o.name) for o in self.des.live_objects() if o.visible])

    def select_object_faces(self) -> None:
        """Edit > Select > All Object Faces: every face of the selected objects (or of all objects)."""
        objs = self.doc.selected_objects() or [o.name for o in self.des.live_objects() if o.visible]
        picks = [Pick(n, i) for n in objs if n in self.doc.tess for i in range(len(self.doc.tess[n].face_area))]
        self.set_mode("face")
        self.doc.select(picks)
        self.msgs.add("info", f"Selected {len(picks)} face(s) of {len(objs)} object(s)")

    def _faces_where(self, test) -> list:
        out = []
        for o in self.des.live_objects():
            t = self.doc.tess.get(o.name)
            if t is None or not o.visible:
                continue
            for i in range(len(t.face_area)):
                X = t.points[np.unique(t.tris[t.tri_face == i])]
                if len(X) and test(X, i, t):
                    out.append(Pick(o.name, i))
        return out

    def select_face_by_position(self) -> None:
        """Select the face(s) passing through a point (Face by Position)."""
        txt, ok = QInputDialog.getText(self, "Face by Position", f"Point (x, y, z in {self.des.units}):", text="0, 0, 0")
        if not ok:
            return
        try:
            P = np.array(self.doc.evaluator().point(txt))
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", f"Invalid point: {e}")
            return
        b = np.array(self.doc.model_bbox()).reshape(2, 3)
        diag = float(np.linalg.norm(b[1] - b[0])) or 1.0
        tol = 1e-6 * diag

        def on_face(X, i, t):
            tri = t.tris[t.tri_face == i]
            A, B, C = t.points[tri[:, 0]], t.points[tri[:, 1]], t.points[tri[:, 2]]
            return _point_triangle_dist(P, A, B, C).min() <= max(tol, 1e-9)
        picks = self._faces_where(on_face)
        self.set_mode("face")
        self.doc.select(picks)
        self.msgs.add("info" if picks else "warning", f"Face by Position: {len(picks)} face(s) found")

    def select_faces_by_plane(self) -> None:
        """Select all planar faces lying in a plane x=, y= or z= value (Faces by Plane)."""
        txt, ok = QInputDialog.getText(self, "Faces by Plane", f"Plane, e.g. z=1.6 or x=0 ({self.des.units}):",
                                       text="z=0")
        if not ok:
            return
        try:
            ax, _, val = txt.replace(" ", "").partition("=")
            i = "xyz".index(ax.lower())
            v = self.doc.evaluator().length(val)
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", f"Invalid plane '{txt}': {e}")
            return
        b = np.array(self.doc.model_bbox()).reshape(2, 3)
        tol = 1e-6 * (float(np.linalg.norm(b[1] - b[0])) or 1.0)
        picks = self._faces_where(lambda X, k, t: np.all(np.abs(X[:, i] - v) <= tol))
        self.set_mode("face")
        self.doc.select(picks)
        self.msgs.add("info" if picks else "warning", f"Faces by Plane {txt}: {len(picks)} face(s) found")

    def select_by_name(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Select Object")
        lay = QVBoxLayout(dlg)
        lw = QListWidget()
        lw.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lw.addItems([o.name for o in self.des.live_objects()])
        lay.addWidget(lw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() == QDialog.Accepted:
            self.set_mode("object")
            self.doc.select([Pick(i.text()) for i in lw.selectedItems()])

    def set_visible(self, vis: bool) -> None:
        for n in self.doc.selected_objects():
            self.des.obj(n).visible = vis
        self.doc.dirty = True
        self.view.restyle_all()
        self.model_tree.rebuild()

    def show_all(self) -> None:
        for o in self.des.objects:
            o.visible = True
        self.view.restyle_all()
        self.model_tree.rebuild()

    def toggle_wireframe(self) -> None:
        for n in self.doc.selected_objects():
            o = self.des.obj(n)
            o.wireframe = not o.wireframe
        self.view.restyle_all()

    def set_quality(self, q: str) -> None:
        self.doc.display_quality = q
        self.settings.setValue("modeler/display_quality", q)
        self.doc.cache.clear()
        self.doc.design_caches.clear()
        self.doc.rebuild()

    def _measure_items(self) -> list:
        return [("Position", lambda: self.start_draw("MeasurePosition")),
                ("Distance", lambda: self.start_draw("Measure")),
                ("Length (selected edges)", self.measure_length),
                ("Angle", lambda: self.start_draw("MeasureAngle")),
                ("Area (selected faces / objects)", self.measure_area),
                ("Volume (selected objects)", self.measure_volume)]

    def show_measure(self, what: str, rows: list) -> None:
        """Non-modal Measure Data window (Modeler > Measure)."""
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        dlg = getattr(self, "_measure_dlg", None)
        if dlg is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Measure Data")
            dlg.setModal(False)
            lay = QVBoxLayout(dlg)
            dlg.table = QTableWidget(0, 2)
            dlg.table.setHorizontalHeaderLabels(["Quantity", "Value"])
            dlg.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            dlg.table.verticalHeader().setVisible(False)
            lay.addWidget(dlg.table)
            dlg.resize(420, 260)
            self._measure_dlg = dlg
        t = dlg.table
        t.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            t.setItem(r, 0, QTableWidgetItem(k))
            t.setItem(r, 1, QTableWidgetItem(v))
        dlg.setWindowTitle(f"Measure Data: {what}")
        dlg.show()
        dlg.raise_()
        self.msgs.add("info", f"{what}: " + ", ".join(f"{k} {v}" for k, v in rows[:1]))

    def measure_length(self) -> None:
        s = LENGTH_UNITS[self.des.units]
        edges = [p for p in self.doc.selection if p.index >= 0 and self.doc.selection_mode == "edge"]
        if not edges:
            self.msgs.add("warning", "Select one or more edges (edge selection mode, E) to measure their length")
            return
        rows, tot = [], 0.0
        for p in edges:
            E = np.asarray(self.doc.tess[p.obj].edges[p.index], float)
            L = float(np.linalg.norm(np.diff(E, axis=0), axis=1).sum())
            tot += L
            rows.append((f"{p.obj} edge {p.index}", f"{L / s:.6g} {self.des.units}"))
        self.show_measure("Length", [("Total length", f"{tot / s:.6g} {self.des.units}")] + rows)

    def measure_area(self) -> None:
        s = LENGTH_UNITS[self.des.units]
        rows, tot = [], 0.0
        for p in self.doc.selection:
            t = self.doc.tess.get(p.obj)
            if t is None:
                continue
            if p.index >= 0 and self.doc.selection_mode == "face":
                a = float(t.face_area[p.index])
                rows.append((f"{p.obj} face {p.index}", f"{a / s ** 2:.6g} {self.des.units}^2"))
            elif p.index < 0:
                a = float(sum(t.face_area))
                rows.append((f"{p.obj} (surface)", f"{a / s ** 2:.6g} {self.des.units}^2"))
            else:
                continue
            tot += a
        if not rows:
            self.msgs.add("warning", "Select faces or objects to measure their area")
            return
        self.show_measure("Area", [("Total area", f"{tot / s ** 2:.6g} {self.des.units}^2")] + rows)

    def measure_volume(self) -> None:
        s = LENGTH_UNITS[self.des.units]
        rows, tot = [], 0.0
        for n in self.doc.selected_objects():
            t = self.doc.tess.get(n)
            if t is None or t.dim < 3:
                continue
            tot += t.volume
            rows.append((n, f"{t.volume / s ** 3:.6g} {self.des.units}^3"))
        if not rows:
            self.msgs.add("warning", "Select one or more solid objects to measure their volume")
            return
        self.show_measure("Volume", [("Total volume", f"{tot / s ** 3:.6g} {self.des.units}^3")] + rows)

    def set_movement_mode(self, mode: str) -> None:
        self.view.movement_mode = mode
        self.settings.setValue("modeler/movement_mode", mode)
        if self.move_combo.currentText() != mode:
            self.move_combo.setCurrentText(mode)

    def set_drawing_plane(self, plane: str) -> None:
        """Working plane for drawing (XY / YZ / XZ in the Draw ribbon)."""
        plane = {"ZX": "XZ"}.get(plane, plane)
        if plane not in ("XY", "YZ", "XZ"):
            return
        v = self.view
        v.grid_plane = plane
        v.update_grid()
        if v.draw is not None:
            v._update_preview()
        v.request_render()
        if hasattr(self, "plane_combo") and self.plane_combo.currentText() != plane:
            self.plane_combo.blockSignals(True)
            self.plane_combo.setCurrentText(plane)
            self.plane_combo.blockSignals(False)
        if hasattr(self, "plane_label"):
            self.plane_label.setText(f"Plane: {plane}")
        self.settings.setValue("grid/plane", plane)

    def snap_mode(self) -> None:
        dlg = SnapModeDialog(self.view, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()
            self._save_window_settings()

    def set_new_objects_model(self, model: bool) -> None:
        """Model / Non Model for newly drawn objects (non-model objects are not simulated)."""
        self.new_objects_model = bool(model)
        self.settings.setValue("modeler/new_objects_model", self.new_objects_model)
        for w, on in ((self.a_model, model), (self.a_nonmodel, not model)):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        if hasattr(self, "model_combo") and self.model_combo.currentIndex() != (0 if model else 1):
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(0 if model else 1)
            self.model_combo.blockSignals(False)

    def solved_setups(self) -> list[str]:
        """Setups of the active design with a finished solution on disk."""
        if not self.doc.project.path:
            return []
        out = []
        from ..solver.results import ResultStore
        for st in self.des.setups:
            try:
                d = ResultStore(result_dir(self.doc, st.name)).read_json("status.json", {}) or {}
            except Exception:  # noqa: BLE001
                d = {}
            if d.get("state") == "done":
                out.append(st.name)
        return out

    def _model_flag_for_new(self) -> bool | None:
        """Model flag of a new object. With solutions present ask whether to create it as a
        non-model object so the solutions stay valid. None = cancelled."""
        if not self.new_objects_model:
            return False
        solved = self.solved_setups()
        if not solved or not self.ask_non_model:
            return True
        from PySide6.QtWidgets import QCheckBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Create Object")
        box.setText("The design has solutions (" + ", ".join(solved) + ").\n\n"
                    "Create the new object as a Non Model object? Non Model objects are not part of the "
                    "simulation, so the existing solutions stay valid; use them for field plots and markers.\n\n"
                    "A Model object changes the geometry and the design has to be solved again.")
        nm = box.addButton("Non Model", QMessageBox.YesRole)
        box.addButton("Model", QMessageBox.NoRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(nm)
        cb = QCheckBox("Don't ask again")
        box.setCheckBox(cb)
        box.exec()
        if cb.isChecked():
            self.ask_non_model = False
            self.settings.setValue("modeler/ask_non_model", False)
        if box.clickedButton() is nm:
            if cb.isChecked():          # don't ask again means: keep drawing Non Model objects
                self.set_new_objects_model(False)
            return False
        if box.buttonRole(box.clickedButton()) == QMessageBox.NoRole:
            return True
        return None

    def grid_settings(self) -> None:
        dlg = GridDialog(self.view, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()
            self.set_drawing_plane(self.view.grid_plane)
            self._save_window_settings()

    def set_units(self) -> None:
        units = ["um", "mm", "cm", "meter", "mil", "in"]
        u, ok = QInputDialog.getItem(self, "Set Model Units", "Select units:", units,
                                     units.index(self.des.units) if self.des.units in units else 1, False)
        if not ok or u == self.des.units:
            return
        rescale = QMessageBox.question(self, "Units", "Rescale the model to the new units?\n"
                                                      "(No keeps the numbers and changes the dimensions)",
                                       QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        self.doc.checkpoint()
        old = self.des.units
        self.des.units = u
        if rescale:
            # unitless numbers are in model units: add explicit units to keep the geometry
            for o in self.des.objects:
                for c in o.history:
                    for p in schema.GEOMETRY.get(c.kind, []):
                        if p.type in ("length", "point", "vector", "points") and c.kind != "CreateRegion":
                            c.props[p.name] = _attach_unit(c.props.get(p.name, ""), old)
        self.doc.commit("model")

    def assign_material(self) -> None:
        objs = self.selected_objects(1)
        if not objs:
            return
        objs, others = material_targets(self.doc, objs)
        if not objs:
            QMessageBox.information(self, "Assign Material",
                                    "Sheets and lines have no material. Assign a boundary such as Finite "
                                    "Conductivity or Impedance instead.")
            return
        if others:
            self.msgs.add("info", f"Assign Material: skipped sheets/lines {', '.join(others)}")
        dlg = MaterialsDialog(self.doc, self, self.des.obj(objs[0]).material)
        if dlg.exec() != QDialog.Accepted or not dlg.selected():
            return
        self.doc.checkpoint()
        mat = self.doc.project.material(dlg.selected())
        for n in objs:
            o = self.des.obj(n)
            o.material = mat.name
            o.color = mat.color
            o.solve_inside = not is_conductor(mat)
        self.doc.commit("model")

    def edit_materials(self) -> None:
        MaterialsDialog(self.doc, self).exec()
        self.project_tree.rebuild()

    def screenshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Screenshot", "model.png", "PNG (*.png)")
        if path:
            self.view.screenshot(path)

    # ==========================================================================================
    # simulation definitions
    # ==========================================================================================
    def solution_type(self) -> None:
        dlg = SolutionTypeDialog(self.doc, self)
        if dlg.exec() == QDialog.Accepted:
            self.doc.checkpoint()
            self.des.settings.update(dlg.result())
            self.doc.commit("settings", geometry=False)

    def differential_pairs(self) -> None:
        """Simulation > Differential Pairs: pairs of terminals for the Diff/Comm report quantities."""
        import json
        from .terminal_dialogs import DiffPairsDialog
        dlg = DiffPairsDialog(self.doc, self)
        if dlg.exec() == QDialog.Accepted:
            self.doc.checkpoint()
            pairs = dlg.pairs()
            self.des.settings["DifferentialPairs"] = json.dumps(pairs) if pairs else ""
            self.doc.commit("settings", geometry=False)
            self.msgs.add("info", f"{len(pairs)} differential pair(s): report quantities "
                          + ", ".join(f"Diff{k + 1}/Comm{k + 1}" for k in range(len(pairs))) if pairs
                          else "Differential pairs removed")

    def port_field_display(self, port: str, mode: int = 1) -> None:
        """The mode field of a wave port on its face (from the newest solution with port fields)."""
        from .terminal_dialogs import port_field_meshes
        try:
            meshes, info = port_field_meshes(self.doc, port, mode=mode)
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", f"Port field display: {e}")
            return
        name = f"Port field {port}"
        self.view.set_overlay(name, meshes)
        self._overlay_info[name] = info
        self.tabs.setCurrentIndex(0)
        self.hint.setText(info)
        self.msgs.add("info", info)

    def _selection_refs(self) -> tuple[list[FaceRef], list[str]]:
        mode = self.doc.selection_mode
        if mode == "face":
            return [FaceRef(s.obj, s.index) for s in self.doc.selection], []
        if mode == "object":
            return [], self.doc.selected_objects()
        return [], []

    def _plot_selection(self) -> tuple[list[FaceRef], list[str]]:
        """Field plot targets: faces, objects, and polylines picked by one of their edges (E mode)."""
        faces, objs = self._selection_refs()
        if not faces and not objs and self.doc.selection_mode == "edge":
            for s in self.doc.selection:
                t = self.doc.tess.get(s.obj)
                if t is not None and t.dim <= 1 and s.obj not in objs:
                    objs.append(s.obj)
        return faces, objs

    def assign(self, family: str, kind: str) -> None:
        faces, objs = self._selection_refs()
        edges: list[FaceRef] = []
        if kind == "Length Based On Edges":
            edges = [FaceRef(s.obj, s.index) for s in self.doc.selection
                     if self.doc.selection_mode == "edge" and s.index >= 0]
            if not edges:
                QMessageBox.information(self, "Assign", "Select one or more edges first (press E).")
                return
            faces, objs = [], []
        if kind not in ("Incident Plane Wave", "Length Based On Edges") and not faces and not objs:
            QMessageBox.information(self, "Assign", "Select faces (F) or objects/sheets (O) first.")
            return
        if family == "mesh" and kind == "Length Based Inside Selection" and faces:
            QMessageBox.information(self, "Assign", "Inside selection needs objects (press O).")
            return
        lists = {"boundary": self.des.boundaries, "excitation": self.des.excitations, "mesh": self.des.mesh_ops}
        table = {"boundary": schema.BOUNDARIES, "excitation": schema.EXCITATIONS, "mesh": schema.MESH_OPS}[family]
        props = schema.defaults(table[kind])
        if family == "excitation" and "PortNumber" in props:
            num = self.des.next_port_number()
            props["PortNumber"] = str(num)
            base = str(num)
        else:
            base = {"Perfect E": "PerfE1", "Perfect H": "PerfH1", "Finite Conductivity": "FiniteCond1",
                    "Impedance": "Imped1", "Lumped RLC": "LumpRLC1", "Radiation": "Rad1", "PML": "PML1",
                    "Symmetry": "Sym1", "Primary": "Primary1", "Secondary": "Secondary1",
                    "Layered Impedance": "LayeredImp1", "Incident Plane Wave": "IncPWave1"}.get(
                kind, kind.split()[0] + "1")
        a = Assignment(self.des.unique_name(base), kind, props, faces=faces, objects=objs, edges=edges)
        if kind == "Lumped Port" or kind == "Lumped RLC":
            line = self._auto_integration_line(faces, objs)
            if line:
                a.props["IntegrationLine"] = self._line_on_object(a, line)
        if not edit_assignment(self, self.doc, family, a):
            return
        self.doc.checkpoint()
        lists[family].append(a)
        self.doc.commit({"boundary": "boundaries", "excitation": "excitations", "mesh": "mesh"}[family], geometry=False)
        self.view.show_assignments([a])
        self.msgs.add("info", f"Assigned {kind} '{a.name}'")

    def _face_triangles(self, obj: str, face: int = -1):
        """points and triangles of one face (face < 0: the whole object) of the display tessellation."""
        t = self.doc.tess.get(obj)
        if t is None:
            return None
        tris = t.tris if face < 0 else t.tris[t.tri_face == face]
        return np.asarray(t.points, float), tris

    def _conductor_surfaces(self, skip: set[str]) -> dict[str, np.ndarray]:
        """surface triangles of every conductor: objects of a conducting material and faces/objects with a
        Perfect E or Finite Conductivity boundary."""
        out: dict[str, list] = {}

        def add(obj, face=-1):
            if obj in skip:
                return
            ft = self._face_triangles(obj, face)
            if ft is not None and len(ft[1]):
                out.setdefault(obj, []).append(ft[0][ft[1]])
        for o in self.des.live_objects():
            try:
                if o.model and is_conductor(self.doc.project.material(o.material)):
                    add(o.name)
            except KeyError:
                pass
        for b in self.des.boundaries:
            if b.enabled and b.kind in ("Perfect E", "Finite Conductivity"):
                for f in b.faces:
                    add(f.obj, f.face)
                for o in b.objects:
                    add(o)
        return {k: np.concatenate(v) for k, v in out.items()}

    def _auto_integration_line(self, faces, objs) -> str:
        """Integration line of a lumped port/RLC sheet: from the centre of its contact with one conductor to
        the centre of its contact with the other; without two touching conductors across the shorter side of
        the sheet (centre to centre)."""
        from ..model.portline import between_conductors
        parts = [self._face_triangles(f.obj, f.face) for f in faces] + [self._face_triangles(o) for o in objs]
        parts = [p for p in parts if p is not None and len(p[1])]
        if parts:
            pts, tris, n = [], [], 0
            for P, T in parts:
                pts.append(P)
                tris.append(T + n)
                n += len(P)
            try:
                line = between_conductors(np.concatenate(pts), np.concatenate(tris),
                                          self._conductor_surfaces({f.obj for f in faces} | set(objs)))
            except Exception:  # noqa: BLE001
                line = None
            if line is not None:
                return f"{self._pt(line[0])};{self._pt(line[1])}"
        boxes = []
        for f in faces:
            t = self.doc.tess.get(f.obj)
            if t is not None and f.face >= 0:
                pts = t.points[np.unique(t.tris[t.tri_face == f.face])]
                boxes.append(np.r_[pts.min(0), pts.max(0)])
        for o in objs:
            t = self.doc.tess.get(o)
            if t is not None:
                boxes.append(np.array(t.bbox))
        if not boxes:
            return ""
        b = np.array(boxes)
        lo, hi = b[:, :3].min(0), b[:, 3:].max(0)
        ext = hi - lo
        nz = [i for i in np.argsort(ext) if ext[i] > 1e-12 * max(ext.max(), 1e-30)]
        if len(nz) < 2:
            return ""
        h = nz[0]
        c = (lo + hi) / 2
        p0, p1 = c.copy(), c.copy()
        p0[h], p1[h] = lo[h], hi[h]
        return f"{self._pt(p0)};{self._pt(p1)}"

    def delete_all(self, family: str) -> None:
        self.doc.checkpoint()
        if family == "boundary":
            self.des.boundaries = []
        else:
            self.des.excitations = []
        self.doc.commit("boundaries", geometry=False)

    def add_setup(self, eigen: bool = False) -> None:
        s = Setup(self.des.unique_name("Setup1"), eigen=eigen)
        if eigen:
            s.props = schema.defaults(schema.EIGEN_SETUP)
        else:
            s.props["Solver"] = self.settings.value("hpc/default_solver", "Auto")
        if edit_setup(self, self.doc, s):
            self.doc.checkpoint()
            self.des.setups.append(s)
            if eigen:
                self.des.settings["SolutionType"] = "Eigenmode"
            self.doc.commit("analysis", geometry=False)

    def add_sweep(self, setup_name: str | None = None) -> None:
        if not self.des.setups:
            QMessageBox.information(self, "Sweep", "Add a solution setup first.")
            return
        st = self.des.setup(setup_name) if setup_name else self.des.setups[0]
        sw = Sweep("Sweep" if not st.sweeps else f"Sweep{len(st.sweeps) + 1}")
        sw.props["Start"] = "1GHz"
        try:
            f = self.doc.evaluator().freq(st.props.get("Frequency", "5GHz"))
            sw.props["Start"] = f"{f * 0.5 / 1e9:g}GHz"
            sw.props["Stop"] = f"{f * 1.5 / 1e9:g}GHz"
            sw.props["Step"] = f"{f / 100 / 1e9:g}GHz"
        except Exception:  # noqa: BLE001
            pass
        if edit_sweep(self, self.doc, sw):
            self.doc.checkpoint()
            st.sweeps.append(sw)
            self.doc.commit("analysis", geometry=False)

    def _farfield(self, sphere: str | None = None):
        if not self.des.spheres:
            raise RuntimeError("Insert a far field setup (Infinite Sphere) and analyze first")
        sph = sphere or self.des.spheres[0].name
        setup = next((s.name for s in self.des.setups
                      if (result_dir(self.doc, s.name) / "huygens_LastAdaptive.npz").exists()
                      or (result_dir(self.doc, s.name) / f"farfield_{sph}_LastAdaptive.npz").exists()), None) \
            if self.doc.project.path else None
        if setup is None:
            raise RuntimeError(f"No far field data for '{sph}': the solution has no radiation/PML boundary "
                               "or was solved with an older version")
        from ..solver.farfield_post import farfield
        st = ResultStore(result_dir(self.doc, setup))
        ff = farfield(st, self.des, sph, "LastAdaptive", self.doc.evaluator())
        if ff is None:
            raise RuntimeError(f"No far field data for '{sph}'")
        from ..solver.ffquant import excited_quantities
        return excited_quantities(st, self.des, ff, "LastAdaptive", 0), sph, setup

    def antenna_parameters(self, sphere: str | None = None) -> None:
        """Radiation > Compute Antenna Parameters."""
        from .reports import antenna_parameters
        try:
            q, sph, setup = self._farfield(sphere)
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", str(e))
            return
        self.show_measure(f"Antenna Parameters: {sph} ({setup} : LastAdaptive)", antenna_parameters(q))

    def radiation_menu(self, menu) -> None:
        """right click > Plot Radiation Field: overlay one of the 3D polar plot reports on the model."""
        polar = [r for r in self.des.reports if r.kind == "3D Polar Plot"]
        pw = not self.des.ports() and any(e.kind == "Incident Plane Wave" and e.enabled for e in self.des.excitations)
        rm = menu.addMenu("Plot Radiation Field")
        rm.setEnabled(bool(self.des.spheres))
        for r in polar:
            tr = r.traces[0] if r.traces else ("dBsm(BistaticRCSTotal)" if pw else "dB(GainTotal)")
            rm.addAction(f"{r.name}: {tr}", lambda _=False, r=r, tr=tr: self.overlay_pattern(r.props.get("sphere") or None, tr))
        if polar:
            rm.addSeparator()
        default = "dBsm(BistaticRCSTotal)" if pw else "dB(GainTotal)"
        for sp in self.des.spheres:
            rm.addAction(f"{sp.name}: {default}", lambda _=False, n=sp.name: self.overlay_pattern(n, default))
        rm.addSeparator()
        rads = [p.name for p in self.des.field_plots if p.quantity == "RadiationField"]
        if rads:
            rm.addAction("Delete Radiation Fields", lambda: self._delete_radiation(rads))

    def _delete_radiation(self, names: list[str]) -> None:
        self.doc.checkpoint()
        self.des.field_plots = [p for p in self.des.field_plots if p.name not in names]
        for n in names:
            self.view.remove_overlay(n)
        self.doc.commit("all", geometry=False)

    def overlay_pattern(self, sphere: str | None = None, quantity: str = "dB(GainTotal)") -> None:
        """Plot Radiation Field: a 3D pattern on the model, listed under Field Overlays."""
        sphere = sphere or (self.des.spheres[0].name if self.des.spheres else "")
        props = {"sphere": sphere, "trace": quantity, "transparency": "0.3", "range": "30", "scale": "0.6"}
        if not self._radiation_dialog(props, "Plot Radiation Field"):
            return
        names = {p.name for p in self.des.field_plots}
        i = 1
        while f"RadiationField{i}" in names:
            i += 1
        self.doc.checkpoint()
        self.des.field_plots.append(FieldPlot(f"RadiationField{i}", "RadiationField", "LastAdaptive", props=props))
        self.doc.commit("all", geometry=False)
        self.show_field_plot(f"RadiationField{i}")

    def _radiation_dialog(self, props: dict, title: str) -> bool:
        from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        fl = QFormLayout(dlg)
        fl.addRow("Far field:", QLabel(f"{props['sphere']}: {props['trace']}"))

        def spin(lo, hi, val, step, suffix=""):
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setValue(float(val))
            sb.setSuffix(suffix)
            return sb
        tr = spin(0, 1, props.get("transparency", "0.3"), 0.1)
        rg = spin(3, 120, props.get("range", "30"), 5, " dB")
        sc = spin(0.05, 10, props.get("scale", "0.6"), 0.1, " x model size")
        fl.addRow("Transparency:", tr)
        fl.addRow("Dynamic range:", rg)
        fl.addRow("Size:", sc)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        fl.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return False
        props.update(transparency=f"{tr.value():g}", range=f"{rg.value():g}", scale=f"{sc.value():g}")
        return True

    def _radiation_meshes(self, props: dict):
        import pyvista as pv
        import re
        from .reports import ff_db
        q, sph, setup = self._farfield(props.get("sphere") or None)
        th, ph = q["_theta"], q["_phi"]
        mt = re.match(r"(\w+)\((\w+)\)", props.get("trace", "dB(GainTotal)"))
        func, quantity = mt.groups() if mt else ("dB", props.get("trace"))
        if quantity not in q:
            raise RuntimeError(f"Unknown far field quantity {quantity}")
        vv = ff_db(func if func in ("dB", "dB10", "dBsm") else "dB", quantity, q[quantity])
        rng = float(props.get("range", "30"))
        vmax = float(vv.max())
        r = np.clip(vv - (vmax - rng), 0, None)
        r = r / (r.max() or 1)
        b = np.asarray(self.doc.model_bbox(), float)
        c = (b[:3] + b[3:]) / 2
        R = float(props.get("scale", "0.6")) * float(np.linalg.norm(b[3:] - b[:3]))
        T, P = np.meshgrid(th, ph, indexing="ij")
        grid = pv.StructuredGrid(c[0] + R * r * np.sin(T) * np.cos(P), c[1] + R * r * np.sin(T) * np.sin(P),
                                 c[2] + R * r * np.cos(T))
        name = f"dB({quantity})"
        grid.point_data[name] = vv.ravel(order="F")
        op = 1.0 - float(props.get("transparency", "0.3"))
        meshes = [(grid, dict(scalars=name, cmap="jet", opacity=op, clim=[vmax - rng, vmax],
                              show_scalar_bar=True, scalar_bar_args=F._bar(quantity, "dB") | {"fmt": "%.1f"}))]
        return meshes, f"3D pattern of {sph}: max {name} = {vmax:.2f} dB ({rng:g} dB range shown)"

    def add_sphere(self) -> None:
        sp = InfiniteSphere(f"Infinite Sphere{len(self.des.spheres) + 1}")
        if edit_sphere(self, self.doc, sp):
            self.doc.checkpoint()
            self.des.spheres.append(sp)
            self.doc.commit("radiation", geometry=False)

    def solid_overlaps(self):
        """Exact check of overlapping model solids (model.validate.Overlap), None if it failed."""
        from ..kernel import occ as K
        from ..model.validate import classify_overlaps
        names = [o.name for o in self.des.live_objects() if o.model and o.history[0].kind != "CreateRegion"
                 and o.name in self.doc.tess and self.doc.tess[o.name].dim == 3]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return classify_overlaps(self.doc.project, self.des,
                                     K.solid_overlaps(self.des, self.doc.evaluator(), names))
        except Exception as e:  # noqa: BLE001
            self.msgs.add("warning", f"Overlap check failed: {e}")
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def resolve_overlaps(self, ovs) -> None:
        """Subtract the winner of each overlap from the loser (as Subtract with Clone tool objects), so the
        model shows the material the solver uses there. Objects that win elsewhere are cut first, so their
        clones carry their own cuts."""
        todo, order = list(ovs), []
        while todo:
            ready = [o for o in todo if not any(p.loser == o.winner for p in todo)] or todo[:1]
            order += ready
            todo = [o for o in todo if o not in ready]
        self.doc.checkpoint()
        for ov in order:
            c = copy.deepcopy(self.des.obj(ov.winner))
            c.name = self.des.unique_name(ov.winner + "_tool")
            c.consumed_by = ov.loser
            self.des.objects.append(c)
            self.des.obj(ov.loser).history.append(Command.new("Subtract", Tools=c.name, KeepOriginals="True"))
            self.msgs.add("info", f"Overlap resolved: '{ov.winner}' subtracted from '{ov.loser}'")
        self.doc.commit("model")
        self.doc.select([Pick(n) for n in dict.fromkeys(o.loser for o in order)])
        self.validation_check()

    def preflight(self) -> bool:
        """Validation before a solve, with the exact overlap check; on errors (e.g. overlapping objects of
        different materials) the Validation Check dialog opens and nothing is solved."""
        from ..model.validate import validate
        ovs = self.solid_overlaps()
        checks = validate(self.doc.project, self.des, self.doc.tess, self.doc.errors, ovs)
        if not any(c.level == "error" for c in checks):
            return True
        self.msgs.add("error", "Validation check failed; see the Validation Check dialog")
        dlg = ValidationDialog(self.doc, self, ovs, self.resolve_overlaps)
        dlg.messageActivated.connect(self.goto_message)
        dlg.exec()
        return False

    def validation_check(self) -> bool:
        dlg = ValidationDialog(self.doc, self, self.solid_overlaps(), self.resolve_overlaps)
        dlg.messageActivated.connect(self.goto_message)
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        dlg.raise_()
        return dlg.ok

    def _first_start_note(self) -> None:
        """Solver kernels not compiled yet on this machine (Hertzon started without the setup's warm-up,
        e.g. from a pip installation or after an update): say so before the first solve takes longer."""
        try:
            from ..solver.bridge import _first_run
            first = _first_run()
        except Exception:  # noqa: BLE001
            return
        if first:
            self.msgs.add("warning", "First start detected: the solver kernels are compiled during the first "
                          "solve, once. It takes a few minutes longer than usual.")
            self.statusBar().showMessage("First start detected: the first solve compiles the solver kernels "
                                         "(once, a few minutes)", 30000)

    def _start_update_check(self) -> None:
        """Ask GitHub for a newer release in the background (Tools > Options > General can switch it off)."""
        from .update_check import SETTING, UpdateChecker, disabled_by_env
        if disabled_by_env() or not self.settings.value(SETTING, True, type=bool):
            return
        self._update_checker = UpdateChecker(__version__, self)
        self._update_checker.available.connect(self._update_available)
        QTimer.singleShot(2000, self._update_checker.start)

    def _update_available(self, release: dict) -> None:
        """Hint in the Message Manager and the status bar; the dialog unless this version was skipped or it
        came up less than PAUSE_DAYS days ago (the status bar link opens it any time)."""
        from datetime import date
        from .update_check import SHOWN_SETTING, SKIP_SETTING, dialog_due, is_skipped
        version, url = release["version"], release["url"]
        if getattr(self, "_update_label", None) is None:
            self.msgs.add("info", f"Hertzon {version} is available (this is {__version__}). To update, click "
                          f"\"Update to {version}\" in the status bar or use Help > Check for Updates. "
                          f"Release page: {url}")
            lab = self._update_label = QLabel(f"Hertzon {version} is available: <a href='#'>Update to {version}...</a>")
            lab.setToolTip("Shows the release notes; Install there downloads and starts the installer.\n"
                           "The check at start can be switched off in Tools > Options > General.")
            lab.linkActivated.connect(lambda _=None, r=release: self._show_update_dialog(r))
            self.statusBar().addPermanentWidget(lab)
        if is_skipped(version, self.settings.value(SKIP_SETTING, "", type=str)):
            return
        if dialog_due(self.settings.value(SHOWN_SETTING, "", type=str), date.today()):
            self.settings.setValue(SHOWN_SETTING, date.today().isoformat())
            self._show_update_dialog(release)

    def check_for_updates(self) -> None:
        """Help > Check for Updates: the same check on request, with an answer also when there is nothing new."""
        from .update_check import UpdateChecker, disabled_by_env
        if disabled_by_env():
            QMessageBox.information(self, "Check for Updates", "Update checks are switched off on this computer "
                                    "(environment variable HERTZON_NO_UPDATE_CHECK).")
            return
        self.statusBar().showMessage("Checking for a new Hertzon version ...", 10000)
        c = self._manual_update_checker = UpdateChecker(__version__, self)
        c.available.connect(self._show_update_dialog)
        c.up_to_date.connect(lambda: QMessageBox.information(
            self, "Check for Updates", f"Hertzon {__version__} is the newest version."))
        c.failed.connect(lambda why: QMessageBox.warning(
            self, "Check for Updates", f"GitHub could not be reached, so no update check was possible.\n\n{why}"))
        c.start()

    def _show_update_dialog(self, release: dict) -> None:
        from .update_check import SKIP_SETTING, UpdateDialog, installed_copy, installer_asset
        old = getattr(self, "_update_dialog", None)
        if old is not None:
            try:
                old.raise_()
                return
            except RuntimeError:  # closed and deleted
                pass
        can_install = installed_copy() is not None and installer_asset(release) is not None
        dlg = self._update_dialog = UpdateDialog(release, __version__, can_install, self)
        dlg.destroyed.connect(lambda *_: setattr(self, "_update_dialog", None))

        def chosen(c: str) -> None:
            if c == "skip":
                self.settings.setValue(SKIP_SETTING, release["version"])
            elif c == "install" and can_install:
                QTimer.singleShot(0, lambda: self._install_update(release))
            elif c == "install":
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(release["url"]))
        dlg.chosen.connect(chosen)
        dlg.show()
        dlg.raise_()

    def _install_update(self, release: dict) -> None:
        """Download the installer (with progress, cancelable, checksum checked), ask to save open changes,
        start the installer and close Hertzon so that the installer can replace the program files."""
        import os
        import threading
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QApplication, QProgressDialog
        from .update_check import download, download_dir, installer_asset
        a = installer_asset(release)
        if self.solver.busy and QMessageBox.question(
                self, "Install Update", "A solve is running. Installing the update stops it. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        class Relay(QObject):
            progress = Signal(int, int)
            done = Signal(object, str)

        relay = Relay(self)
        pd = QProgressDialog(f"Downloading {a['name']} ...", "Cancel", 0, 0, self)
        pd.setWindowTitle("Install Update")
        pd.setWindowModality(Qt.WindowModal)
        pd.setMinimumDuration(0)

        def progress(done: int, total: int) -> None:
            if total:  # without a size the bar stays a busy indicator
                pd.setMaximum(total)
                pd.setValue(done)
        relay.progress.connect(progress)
        cancel = threading.Event()
        pd.canceled.connect(cancel.set)
        loop = QEventLoop()
        result: list = [None, ""]

        def finished(path, err) -> None:
            result[:] = [path, err]
            loop.quit()
        relay.done.connect(finished)

        def run() -> None:
            try:
                path = download(a["browser_download_url"], download_dir() / a["name"], a.get("digest") or "",
                                progress=relay.progress.emit, cancelled=cancel.is_set)
                relay.done.emit(path, "")
            except Exception as e:  # noqa: BLE001
                relay.done.emit(None, str(e) or type(e).__name__)
        threading.Thread(target=run, name="hertzon-update-download", daemon=True).start()
        loop.exec()
        pd.close()
        path, err = result
        if err:
            QMessageBox.warning(self, "Install Update", f"The installer could not be downloaded:\n{err}\n\n"
                                f"It is also on the release page:\n{release['url']}")
            return
        if path is None or not self.maybe_save():  # cancelled
            return
        self.doc.dirty = False  # answered above; closing must not ask again
        try:
            os.startfile(str(path))  # noqa: S606 - the installer that was just downloaded and checked
        except OSError as e:
            QMessageBox.warning(self, "Install Update", f"The installer could not be started:\n{e}\n\n{path}")
            return
        self.msgs.add("info", f"Installing Hertzon {release['version']}: Hertzon closes for the installer.")
        self.close()
        QApplication.quit()

    def goto_message(self, text: str) -> None:
        """Select what a message is about: object, boundary/excitation/mesh operation, setup, report."""
        import re
        if not text:
            return
        des = self.des
        objs = {o.name for o in des.live_objects()}
        ass = {a.name: kind for kind, lst in (("boundary", des.boundaries), ("excitation", des.excitations),
                                              ("meshop", des.mesh_ops)) for a in lst}
        setups = {st.name for st in des.setups}
        reports = {r.name for r in des.reports}
        body = text.split(": ", 1)[1] if re.match(r"^[^:]+ - [^:]+ - [^:]+: ", text) else text
        cands = re.findall(r"'([^']+)'", body)
        names = sorted(objs | set(ass) | setups | reports, key=len, reverse=True)
        for src in (body, text):
            for n in names:
                if re.search(r"(?<![\w.])" + re.escape(n) + r"(?![\w.])", src):
                    cands.append(n)
        for n in cands:
            if n in objs:
                self.set_mode("object")
                self.doc.select([Pick(n)])
                self.props.show_object(n)
                return
            if n in ass:
                self.project_tree.select_data((ass[n], n)) if hasattr(self.project_tree, "select_data") else None
                self._project_selected((ass[n], n))
                return
            if n in setups:
                self.project_tree.select_data(("setup", n)) if hasattr(self.project_tree, "select_data") else None
                self._project_selected(("setup", n))
                return
            if n in reports:
                self.open_report(n)
                return

    def analyze_all(self) -> None:
        self.analyze([s.name for s in self.des.setups if s.props.get("Enabled", "True") == "True"])

    def analyze_sweep(self, setup: str, sweep: str) -> None:
        """right click on a sweep > Analyze (reuses the adaptive solution)."""
        from .trees import solution_state
        if solution_state(self.doc, setup) not in ("done", "outdated"):
            QMessageBox.information(self, "Analyze Sweep", "Analyze the setup first; the sweep reuses its adaptive mesh.")
            return
        if not self.doc.project.path or self.doc.dirty:
            if not self.file_save():
                return
        self.solver.submit(Job(self.doc.project.path, self.des.name, setup, sweep=sweep))
        self.project_tree.rebuild()

    def derivative_tuning(self, setup: str | None = None, sweep: str | None = None) -> None:
        """Optimization > Derivative Tuning: a dock with sliders for design variables; the reports on the
        tuned solution draw the tuned curves live."""
        from .tuning import TuningPanel, tuning_state
        if not self.doc.project.path:
            QMessageBox.information(self, "Derivative Tuning", "Save and analyze the project first.")
            return
        dock = getattr(self, "_tuning_dock", None)
        if dock is None:
            panel = TuningPanel(self, setup, sweep)
            dock = self._tuning_dock = self._dock("Derivative Tuning", panel, Qt.RightDockWidgetArea)

            def shown(visible: bool) -> None:
                if not visible and dock.isHidden():          # closed: the reports show the solved design
                    tuning_state(self.doc).set_model(None, "")
                elif visible and tuning_state(self.doc).model is None:
                    panel.load()
            dock.visibilityChanged.connect(shown)
        else:
            dock.widget().select(setup, sweep)
        dock.show()
        dock.raise_()

    def compute_tuning(self, setup: str, sweep: str, variables: list[str], points: int) -> str:
        """Queue the tuning model of a sweep (reuses the adaptive mesh); returns the job key, '' if not."""
        from .trees import solution_state
        if solution_state(self.doc, setup) != "done":
            QMessageBox.information(self, "Derivative Tuning",
                                    "Analyze the setup first (with the current design); the derivatives are "
                                    "computed on its adaptive mesh.")
            return ""
        if not self.doc.project.path or self.doc.dirty:
            if not self.file_save():
                return ""
        job = Job(self.doc.project.path, self.des.name, setup,
                  tuning=json.dumps({"sweep": sweep, "variables": variables, "points": int(points)}))
        self.solver.submit(job)
        return job.key

    def analyze(self, setups: list[str], mesh_only: bool = False) -> None:
        if not setups:
            QMessageBox.information(self, "Analyze", "No enabled solution setup.")
            return
        if not self.preflight():
            return
        if not self.doc.project.path or self.doc.dirty:
            if not self.file_save():
                return
        for s in setups:
            self.solver.submit(Job(self.doc.project.path, self.des.name, s, mesh_only=mesh_only))
        self.project_tree.rebuild()

    # ---- Optimization (Optimization > Add Optimization) ---------------------------------------
    def _optimization(self, name: str):
        return next(o for o in self.des.optimetrics if o.name == name)

    def add_optimization(self) -> None:
        from .optimetrics import OptimizationDialog, new_optimization
        if not any(not s.eigen for s in self.des.setups):
            QMessageBox.information(self, "Optimization", "Add a driven solution setup (with a sweep) first.")
            return
        op = new_optimization(self.des)

        def store(o) -> None:
            self.doc.checkpoint()
            if not any(x is o for x in self.des.optimetrics):
                self.des.optimetrics.append(o)
            self.doc.commit("analysis", geometry=False)
        if OptimizationDialog(self, self.doc, op, on_apply=store).exec() == QDialog.Accepted:
            store(op)

    def edit_optimization(self, name: str) -> None:
        import copy
        from .optimetrics import OptimizationDialog
        op = self._optimization(name)
        work = copy.deepcopy(op)

        def store(w) -> None:
            self.doc.checkpoint()
            op.name, op.solution, op.variables, op.goals, op.props = (w.name, w.solution, copy.deepcopy(w.variables),
                                                                      copy.deepcopy(w.goals), dict(w.props))
            self.doc.commit("analysis", geometry=False)
        if OptimizationDialog(self, self.doc, work, on_apply=store, original=op).exec() == QDialog.Accepted:
            store(work)

    def copy_optimization(self, name: str) -> None:
        import copy
        op = copy.deepcopy(self._optimization(name))
        names = {o.name for o in self.des.optimetrics}
        base, i = op.name + "_copy", 1
        op.name = base
        while op.name in names:
            i += 1
            op.name = f"{base}{i}"
        self.doc.checkpoint()
        self.des.optimetrics.append(op)
        self.doc.commit("analysis", geometry=False)

    def analyze_optimization(self, name: str) -> None:
        """Run the optimization in the solve queue: every evaluation solves the design with new values."""
        from ..solver.optimetrics import Variables
        op = self._optimization(name)
        try:
            Variables(self.des, op, self.doc.evaluator())
            if not op.goals:
                raise ValueError("The optimization has no goal")
            if not op.solution:
                raise ValueError("Choose the solution of the goals")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Optimization", f"{name}: {e}")
            return
        if not self.preflight():
            return
        if not self.doc.project.path or self.doc.dirty:
            if not self.file_save():
                return
        self.solver.submit(Job(self.doc.project.path, self.des.name, "", optimization=name))
        self.project_tree.rebuild()
        self.optimization_results(name)

    def optimization_results(self, name: str) -> None:
        from .optimetrics import OptimizationResults
        from .solve import optimization_dir
        if not self.doc.project.path:
            QMessageBox.information(self, "Optimization", "Save the project and analyze the optimization first.")
            return
        dlg = OptimizationResults(self, name, optimization_dir(self.doc.project.path, self.des.name, name))
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    # ---- Optimization > Parametric (parametric sweep) ------------------------------------------
    def _parametric(self, name: str):
        return next(o for o in self.des.parametrics if o.name == name)

    def add_parametric(self) -> None:
        from .optimetrics import ParametricDialog, new_parametric
        if not self.des.setups:
            QMessageBox.information(self, "Parametric", "Add a solution setup first.")
            return
        ps = new_parametric(self.doc)

        def store(o) -> None:
            self.doc.checkpoint()
            if not any(x is o for x in self.des.parametrics):
                self.des.parametrics.append(o)
            self.doc.commit("analysis", geometry=False)
        if ParametricDialog(self, self.doc, ps, on_apply=store).exec() == QDialog.Accepted:
            store(ps)

    def edit_parametric(self, name: str) -> None:
        import copy
        from .optimetrics import ParametricDialog
        ps = self._parametric(name)
        work = copy.deepcopy(ps)

        def store(w) -> None:
            self.doc.checkpoint()
            ps.name, ps.sweeps, ps.setups = w.name, copy.deepcopy(w.sweeps), list(w.setups)
            ps.calcs, ps.props = copy.deepcopy(w.calcs), dict(w.props)
            self.doc.commit("analysis", geometry=False)
        if ParametricDialog(self, self.doc, work, on_apply=store, original=ps).exec() == QDialog.Accepted:
            store(work)

    def copy_parametric(self, name: str) -> None:
        import copy
        ps = copy.deepcopy(self._parametric(name))
        names = {o.name for o in self.des.parametrics} | {o.name for o in self.des.optimetrics}
        base, i = ps.name + "_copy", 1
        ps.name = base
        while ps.name in names:
            i += 1
            ps.name = f"{base}{i}"
        self.doc.checkpoint()
        self.des.parametrics.append(ps)
        self.doc.commit("analysis", geometry=False)

    def analyze_parametric(self, name: str, show: bool = True) -> bool:
        """Solve every variation of the parametric setup in the solve queue (still valid ones are reused)."""
        from ..solver.parametric import variation_table
        ps = self._parametric(name)
        try:
            rows = variation_table(ps, self.doc.evaluator())
            if not rows:
                raise ValueError("Add a sweep definition first")
            if not any(any(s.name == x for s in self.des.setups) for x in ps.setups):
                raise ValueError("Choose a setup to simulate (General tab)")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Parametric", f"{name}: {e}")
            return False
        if not self.preflight():
            return False
        if not self.doc.project.path or self.doc.dirty:
            if not self.file_save():
                return False
        self.solver.submit(Job(self.doc.project.path, self.des.name, "", parametric=name))
        self.project_tree.rebuild()
        if show:
            self.parametric_results(name)
        return True

    def analyze_all_parametrics(self) -> None:
        for ps in list(self.des.parametrics):
            if not self.analyze_parametric(ps.name, show=False):
                return

    def parametric_results(self, name: str) -> None:
        from .optimetrics import ParametricResults
        from ..solver.parametric import parametric_dir
        if not self.doc.project.path:
            QMessageBox.information(self, "Parametric", "Save the project and analyze the parametric setup first.")
            return
        dlg = ParametricResults(self, name, parametric_dir(self.doc.project.path, self.des.name, name))
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def apply_best_variables(self, name: str) -> None:
        from .optimetrics import read_history
        from .solve import optimization_dir
        if not self.doc.project.path:
            return
        _, ev, _ = read_history(optimization_dir(self.doc.project.path, self.des.name, name))
        ok = [e for e in ev if e.get("cost") is not None]
        if not ok:
            QMessageBox.information(self, "Optimization", f"{name} has no evaluated design yet.")
            return
        best = min(ok, key=lambda e: e["cost"])
        self.apply_variables(best["variables"], f"the best evaluation ({best['eval']}) of {name}")

    def apply_variables(self, values: dict, source: str) -> None:
        """Set design variables (from an optimization result); undo restores the previous values."""
        pv = self.doc.project.variables
        changed = {k: v for k, v in values.items()
                   if (pv.get(k) if k in pv and k not in self.des.variables else self.des.variables.get(k)) != v}
        if not changed:
            self.msgs.add("info", f"The design already uses the values of {source}")
            return
        self.doc.checkpoint()
        for k, v in changed.items():
            (pv if k in pv and k not in self.des.variables else self.des.variables)[k] = v
        self.doc.commit("variables")
        self.msgs.add("info", f"Applied {source}: " + ", ".join(f"{k} = {v}" for k, v in changed.items())
                      + " (Ctrl+Z to undo)")

    def generate_mesh(self, setups: list[str] | None = None) -> None:
        """'Generate Mesh': initial mesh (with mesh operations and lambda refinement), no solve."""
        if setups is None:
            setups = [s.name for s in self.des.setups if s.props.get("Enabled", "True") == "True"][:1]
        self.analyze(setups, mesh_only=True)

    def run_post(self, text: str, fn):
        """Post-processing in a worker thread; the GUI stays usable and the progress window shows it."""
        import threading
        from PySide6.QtWidgets import QApplication
        if getattr(self, "_post_busy", False):
            raise RuntimeError("Post-processing is still running")
        self._post_busy = True
        st = {"v": 0.0, "res": None, "err": None}

        def work():
            try:
                st["res"] = fn(lambda v: st.__setitem__("v", v))
            except BaseException as e:  # noqa: BLE001
                st["err"] = e
        th = threading.Thread(target=work, daemon=True)
        th.start()
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            while th.is_alive():
                th.join(0.03)
                self.progress.post_progress(text, 100 * st["v"])
                # clicks and keys wait in the queue until the result is there: a double click on a plot
                # must not start the same computation again while this one runs
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        finally:
            QApplication.restoreOverrideCursor()
            self.progress.post_progress(None)
            self._post_busy = False
        if st["err"] is not None:
            raise st["err"]
        return st["res"]

    def _pass_data(self, key: str, msg: dict) -> None:
        """Mesh and fields of the current adaptive pass were saved: redraw what is on screen."""
        if self.anim_bar.frames:
            return                                # the animation shows its own precomputed frames
        for p in list(self.des.field_plots):
            if p.name in self.view.overlay_actors:
                self._draw_field_plot(p.name, loud=False)

    def _pass_finished(self, key: str, msg: dict) -> None:
        """Refresh reports after every adaptive pass (LastAdaptive is available while solving)."""
        self._refresh_reports()

    def _solve_finished(self, key: str, ok: bool) -> None:
        self.project_tree.rebuild()
        if ok:
            self._pass_data(key, {})                  # field plots on screen show the final solution
        self._refresh_reports()
        auto = getattr(self, "_auto_report", None)
        if ok and auto in self.report_tabs:
            # report created during the solve shows the sweep once it exists
            sw = [x for x in solution_list(self.doc) if not x.endswith("LastAdaptive") and "AdaptivePasses" not in x]
            if sw:
                self.report_tabs[auto].sol.setCurrentText(sw[0])
        self._auto_report = None

    def _refresh_reports(self) -> None:
        for w in self.report_tabs.values():
            if w._family():
                w._fill_solutions()
                w.refresh()
                continue
            w.sol.blockSignals(True)
            cur = w.sol.currentText()
            w.sol.clear()
            from .reports import report_solution_list
            items = report_solution_list(self.doc, w.report.kind)
            w.sol.addItems(items + ([cur] if cur and cur not in items else []))
            w.sol.setCurrentText(cur)
            w.sol.blockSignals(False)
            w.refresh()

    def solution_data(self, setup: str | None = None) -> None:
        if not self.des.setups:
            return
        setup = setup or self.des.setups[0].name
        if not self.doc.project.path:
            QMessageBox.information(self, "Solution Data", "Save and analyze the project first.")
            return
        # non-modal: the model, reports and the running solve stay usable
        dlg = SolutionDataDialog(self.doc, setup, self)
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        sigs = (self.solver.passUpdate, self.solver.passData, self.solver.finished)
        for sig in sigs:
            sig.connect(dlg.reload)

        def _gone(*_):
            for sig in sigs:
                try:
                    sig.disconnect(dlg.reload)
                except Exception:  # noqa: BLE001  (already disconnected)
                    pass
        dlg.finished.connect(_gone)       # still alive here; Qt drops the connections when it is deleted
        dlg.show()
        dlg.raise_()

    # ==========================================================================================
    # results
    # ==========================================================================================
    def report_availability(self) -> dict[str, bool]:
        """Which report categories exist for this design (no modal/terminal data without ports ...)."""
        d = self.des
        stype = d.settings.get("SolutionType", "Modal")
        ports = bool(d.ports()) and stype != "Eigenmode"
        pw = not d.ports() and any(e.kind == "Incident Plane Wave" and e.enabled for e in d.excitations)
        ff = bool(d.spheres) and (ports or pw)
        return {"modal": ports and stype == "Modal", "terminal": ports and stype == "Terminal",
                "farfield": ff, "antenna": bool(d.spheres) and ports, "rcs": bool(d.spheres) and pw,
                "near": bool(d.setups) and stype != "Eigenmode", "fields": bool(d.setups)}

    def _fill_report_menu(self, m) -> None:
        """Results > Create ... Report, only the categories this design can produce."""
        for act in [a for a in m.actions() if a.property("report_item")]:
            m.removeAction(act)
        av = self.report_availability()
        items = []

        def sub(title, entries):
            sm = QMenu(title, m)
            for label, fn in entries:
                sm.addAction(label, fn)
            items.append(sm.menuAction())
        plots = [("Rectangular Plot", lambda: self.create_report("Rectangular")),
                 ("Smith Chart", lambda: self.create_report("Smith Chart")),
                 ("Data Table", lambda: self.create_report("Data Table"))]
        if av["modal"]:
            sub("Create Modal Solution Data Report", plots)
        if av["terminal"]:
            sub("Create Terminal Solution Data Report", plots)
        if av["fields"]:
            sub("Create Fields Report", [("Rectangular Plot", lambda: self.field_report()),
                                         ("Data Table", lambda: self.field_report(kind="Data Table"))])
        if av["farfield"]:
            sub("Create Far Fields Report", [("Radiation Pattern", lambda: self.create_report("Radiation Pattern")),
                                             ("Rectangular Plot", lambda: self.create_report(
                                                 "Radiation Pattern", props={"plot": "Rectangular"})),
                                             ("3D Polar Plot", lambda: self.create_report("3D Polar Plot"))])
        if av["antenna"]:
            sub("Create Antenna Parameters Report", [
                ("Rectangular Plot", lambda: self.create_report("Antenna Parameters")),
                ("Data Table", lambda: self.create_report("Antenna Parameters Table"))])
        if av["rcs"]:
            sub("Create Monostatic RCS Report", [("Rectangular Plot", lambda: self.create_report("Monostatic RCS")),
                                                 ("Data Table", lambda: self.create_report("Monostatic RCS Table"))])
        if av["near"]:
            sub("Create Near Fields Report", [("Line", lambda: self.create_report("Near Field Line"))])
        if not items:
            a = QAction("No reports available: assign ports or an incident wave (far fields need an "
                        "infinite sphere)", m)
            a.setEnabled(False)
            items.append(a)
        first = m.actions()[0] if m.actions() else None
        for a in items:
            a.setProperty("report_item", True)
            m.insertAction(first, a)
        if first is not None:
            sep = m.insertSeparator(first)
            sep.setProperty("report_item", True)

    def _fill_results_menu(self, m) -> None:
        self._fill_report_menu(m)

    def create_report(self, kind: str, quiet: bool = False, props: dict | None = None,
                      with_traces: list[str] | None = None) -> None:
        sols = solution_list(self.doc)
        sol = next((s for s in sols if not s.endswith("LastAdaptive") and "AdaptivePasses" not in s),
                   sols[0] if sols else
                   (f"{self.des.setups[0].name} : LastAdaptive" if self.des.setups else ""))
        n = len(self.des.ports())
        if kind in ("Monostatic RCS", "Monostatic RCS Table"):
            from .reports import rcs_solution_list
            rsols = rcs_solution_list(self.doc)
            traces = ["dBsm(MonostaticRCSTotal)"]
            # a sweep over frequency, else the adaptive passes, else LastAdaptive
            sol = next((s for s in rsols if not s.endswith("LastAdaptive") and "AdaptivePasses" not in s),
                       next((s for s in rsols if "AdaptivePasses" in s),
                            rsols[0] if rsols else (f"{self.des.setups[0].name} : LastAdaptive"
                                                    if self.des.setups else sol)))
        elif kind in ("Radiation Pattern", "3D Polar Plot"):
            pw = not self.des.ports() and any(e.kind == "Incident Plane Wave" and e.enabled
                                              for e in self.des.excitations)
            traces = ["dBsm(BistaticRCSTotal)" if pw else "dB(GainTotal)"]
            sol = next((s for s in sols if s.endswith("LastAdaptive")), sol)
        elif kind in ("Antenna Parameters", "Antenna Parameters Table"):
            from .reports import antenna_solution_list
            asols = antenna_solution_list(self.doc)
            # a sweep with far fields over frequency, else the adaptive passes, else LastAdaptive
            sol = next((s for s in asols if not s.endswith("LastAdaptive") and "AdaptivePasses" not in s),
                       next((s for s in asols if "AdaptivePasses" in s), asols[0] if asols else sol))
            traces = ["dB(PeakGain)", "dB(PeakRealizedGain)", "dB(PeakDirectivity)"] if kind == "Antenna Parameters" \
                else ["dB(PeakDirectivity)", "dB(PeakGain)", "dB(PeakRealizedGain)", "RadiationEfficiency",
                      "RadiatedPower", "AcceptedPower", "dB(FrontToBackRatio)", "dB(AxialRatioAtMax)"]
        elif kind == "Near Field Line":
            traces = ["mag(NearETotal)"]
            sol = next((s for s in sols if s.endswith("LastAdaptive")), sol)
        else:
            traces = [f"dB(S({i},1))" for i in range(1, max(1, n) + 1)][:4]
        prefix = {"Rectangular": "XY Plot", "Smith Chart": "Smith Chart", "Data Table": "Data Table",
                  "Radiation Pattern": "Radiation Pattern", "3D Polar Plot": "3D Polar Plot",
                  "Near Field Line": "Near Field", "Monostatic RCS": "Monostatic RCS",
                  "Monostatic RCS Table": "Monostatic RCS Table", "Antenna Parameters": "Antenna Parameters Plot",
                  "Antenna Parameters Table": "Antenna Parameters Table"}[kind]
        if kind == "Radiation Pattern" and (props or {}).get("plot") == "Rectangular":
            prefix = "Far Field Plot"
        names = {r.name for r in self.des.reports}
        i = 1
        while f"{prefix} {i}" in names:
            i += 1
        r = Report(f"{prefix} {i}", kind, sol, with_traces if with_traces is not None else traces)
        r.props.update(props or {})
        if kind in ("Rectangular", "Smith Chart", "Data Table") and not sols and self.doc.project.path:
            # nominal design not solved: show the variations of a solved parametric setup (families)
            from .reports import family_solution_list
            for ps in self.des.parametrics:
                fs = family_solution_list(self.doc, ps.name)
                if fs:
                    r.solution = next((s for s in fs if not s.endswith("LastAdaptive") and "AdaptivePasses" not in s),
                                      fs[0])
                    r.props["families"] = ps.name
                    break
        if kind == "Near Field Line":
            b = np.asarray(self.doc.model_bbox(), float) / LENGTH_UNITS[self.des.units]
            c = (b[:3] + b[3:]) / 2
            r.props.update({"start": f"{b[0]:.6g},{c[1]:.6g},{c[2]:.6g}", "end": f"{b[3]:.6g},{c[1]:.6g},{c[2]:.6g}",
                            "points": "101"})
        self.des.reports.append(r)
        self.doc.dirty = True
        self.doc.changed.emit("results")
        self.open_report(r.name)

    def open_report(self, name: str) -> None:
        if name in self.report_tabs:
            self.tabs.setCurrentWidget(self.report_tabs[name])
            self.report_tabs[name].refresh()
            return
        r = next(x for x in self.des.reports if x.name == name)
        w = ReportWindow(self.doc, r)
        w.changed.connect(lambda: setattr(self.doc, "dirty", True))
        self.report_tabs[name] = w
        self.tabs.addTab(w, name)
        self.tabs.setCurrentWidget(w)

    def rename_report(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Rename Report", "Name:", text=name)
        new = new.strip()
        if not ok or not new or new == name:
            return
        if any(r.name == new for r in self.des.reports):
            QMessageBox.warning(self, "Rename Report", f"A report named '{new}' already exists.")
            return
        next(x for x in self.des.reports if x.name == name).name = new
        w = self.report_tabs.pop(name, None)
        if w is not None:                           # the open tab keeps its plot, with the new name
            self.report_tabs[new] = w
            self.tabs.setTabText(self.tabs.indexOf(w), new)
            w.refresh()
        if getattr(self, "_auto_report", None) == name:
            self._auto_report = new
        self.doc.dirty = True
        self.doc.changed.emit("results")

    def _close_tab(self, idx: int) -> None:
        w = self.tabs.widget(idx)
        for k, v in list(self.report_tabs.items()):
            if v is w:
                del self.report_tabs[k]
        if w is not None and w is self.calc_window:
            w.closing()
        if idx > 0:
            self.tabs.removeTab(idx)

    def _close_reports(self) -> None:
        """Close the report tabs and the Equations tab (another design is shown)."""
        if self.calc_window is not None and self.tabs.indexOf(self.calc_window) >= 0:
            self.calc_window.closing()
            self.calc_window.new()
        while self.tabs.count() > 1:
            self.tabs.removeTab(1)
        self.report_tabs.clear()

    def prepare_equations(self) -> None:
        """Make the Equations tab (hidden) before the window is shown. Its formula editor is a web view, drawn
        by the graphics card like the 3D view: a window gets that way of drawing when it is made, so adding
        the first such view later rebuilt the whole window (it flickered closed and open) and the start of the
        web engine held the GUI on the first click."""
        try:
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
            self._gl_marker = QOpenGLWidget(self)        # the window draws with the graphics card from the start
            self._gl_marker.setFixedSize(0, 0)
            self._gl_marker.hide()
        except Exception:  # noqa: BLE001
            pass
        self._make_equations()

    def _make_equations(self) -> None:
        if self.calc_window is None:
            from .calculator import EquationsTab
            self.calc_window = EquationsTab(self)
            self.calc_window.hide()
            self.calc_window.build_ribbon(self.ribbon.page("Equation"))
            self._eq_page = self.ribbon.count() - 1
            self.ribbon.setTabVisible(self._eq_page, False)

    def fields_calculator(self, name: str | None = None, for_plot: bool = False) -> None:
        """Results > Fields > Calculator: the Equations tab next to the 3D Modeler for equations of E, H and J,
        with the Equation page of the ribbon."""
        self._make_equations()
        w = self.calc_window
        if self.tabs.indexOf(w) < 0:
            self.tabs.insertTab(1, w, "Equations")
        w.refresh()
        self.tabs.setCurrentWidget(w)
        if for_plot:
            w.open_for_plot()
        elif name:
            w.load(name)
        w._dirty_changed()
        w.focus_editor()

    def _sync_equation_page(self, *_) -> None:
        """The Equation page of the ribbon is there while the Equations tab is the current one."""
        if self.tabs.currentIndex() != 0:
            self.view.reset_interaction()       # a drag in the 3D view does not stay pending behind another tab
        if self._eq_page is None:
            return
        rb, idx = self.ribbon, self._eq_page
        on = self.calc_window is not None and self.tabs.currentWidget() is self.calc_window
        if on == rb.isTabVisible(idx):
            return
        rb.setTabVisible(idx, on)
        if on:
            rb.setCurrentIndex(idx)
        elif rb.currentIndex() == idx or not rb.isTabVisible(rb.currentIndex()):
            rb.setCurrentIndex(next(i for i in range(rb.count()) if rb.tabText(i) == "Results"))

    def new_equation(self, library: str | None = None) -> None:
        self.fields_calculator()
        if library:
            self.calc_window._load_library(library)
        else:
            self.calc_window.new()

    def rename_equation(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Rename Equation", "Name:", text=name)
        new = new.strip()
        if ok and new and new != name:
            self.fields_calculator(name)
            self.calc_window.set_name(new)
            self.calc_window.save()

    def export_equations(self) -> None:
        """Equations > Export Equations: the equations and functions of the design into a file, to use them in
        other designs and projects."""
        des = self.des
        if not des.field_exprs and not des.field_funcs:
            QMessageBox.information(self, "Export Equations", "This design has no equations yet.")
            return
        start = str(Path(self.doc.project.path).with_name(f"{des.name}_equations.json")) if self.doc.project.path \
            else f"{des.name}_equations.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export Equations", start, EQ_FILTER)
        if not path:
            return
        data = {"format": "hertzon-equations", "version": 1,
                "equations": [{"name": e.name, "expr": e.expr, "description": e.description}
                              for e in des.field_exprs],
                "functions": list(des.field_funcs)}
        try:
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "Export Equations", f"Cannot write {path}:\n{e}")
            return
        self.msgs.add("info", f"Exported {len(data['equations'])} equations and {len(data['functions'])} "
                              f"functions to {path}")

    def import_equations(self, path: str | None = None) -> None:
        """Equations > Import Equations: equations and functions from a file of Export Equations."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Import Equations", "", EQ_FILTER)
            if not path:
                return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            eqs = [(str(e["name"]), str(e["expr"]), str(e.get("description", ""))) for e in data.get("equations", [])]
            funcs = [str(f) for f in data.get("functions", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            QMessageBox.warning(self, "Import Equations", f"{path} is not a file of equations:\n{e}")
            return
        self._add_equations(eqs, funcs, f"from {path}")

    def open_equation_library(self) -> None:
        """Equations > Open Library: ready-made equations and functions, one or more imported at once."""
        from .calculator import LibraryDialog
        dlg = LibraryDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._add_equations(*dlg.chosen(), "from the library")

    def _add_equations(self, eqs: list[tuple[str, str, str]], funcs: list[str], source: str) -> None:
        """Equations (name, expression, description) and functions into the design; for names it has
        already, the user chooses whether to replace them."""
        from ..model.design import FieldExpression
        des = self.des

        def fname(text):
            return text.split("(", 1)[0].strip()
        have = {e.name for e in des.field_exprs} | {fname(f) for f in des.field_funcs}
        same = [n for n, _, _ in eqs if n in have] + [fname(f) for f in funcs if fname(f) in have]
        replace = False
        if same:
            ans = QMessageBox.question(
                self, "Import Equations", f"The design has already: {', '.join(same)}.\n\nReplace them with the "
                "ones from the file? (No keeps those of the design.)",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                return
            replace = ans == QMessageBox.Yes
        self.doc.checkpoint()
        n = 0
        for name, expr, desc in eqs:
            old = next((e for e in des.field_exprs if e.name == name), None)
            if old is None:
                des.field_exprs.append(FieldExpression(name, expr, desc))
            elif replace:
                old.expr, old.description = expr, desc
            else:
                continue
            n += 1
        for f in funcs:
            if fname(f) in {fname(x) for x in des.field_funcs}:
                if not replace:
                    continue
                des.field_funcs = [x for x in des.field_funcs if fname(x) != fname(f)]
            des.field_funcs.append(f)
            n += 1
        self.doc.dirty = True
        self.doc.changed.emit("fields")
        self.msgs.add("info", f"Imported {n} equations and functions {source}")
        if replace:
            self.redraw_equation_plots()

    def redraw_equation_plots(self, names: list[str] | None = None) -> None:
        """An equation or function changed: the field plots on screen that use it are drawn again with the
        new formula. names None: all plots of equations. A plot whose equation is now a value, not a field,
        is deleted."""
        from .calc_bridge import dependents, expression_kinds
        des = self.des
        eqs = {e.name for e in des.field_exprs}
        if names is None:
            used = eqs
        else:
            used, todo = set(), list(names)
            while todo:                          # the changed names and everything that uses them
                n = todo.pop()
                if n not in used:
                    used.add(n)
                    todo += [d.removesuffix("()") for d in dependents(des, n)]
        kinds = expression_kinds(self.doc)
        gone = [p.name for p in des.field_plots if p.quantity in used and kinds.get(p.quantity) in ("value", "vector")]
        if gone:
            des.field_plots = [p for p in des.field_plots if p.name not in gone]
            self._sync_overlays()
            self.project_tree.rebuild()
            self.msgs.add("info", f"Deleted the field plot{'s' if len(gone) > 1 else ''} {', '.join(gone)}: the "
                                  "equation is a value now, not a field")
        shown = [p.name for p in des.field_plots if p.quantity in used and p.quantity in eqs
                 and p.name in self.view.overlay_actors]
        if not shown:
            return
        if any(n in self.anim_bar.names for n in shown):
            self.anim_bar.close_player()         # its frames show the old formula
        for n in shown:
            self.show_field_plot(n, focus=False)

    def equation_functions(self, select: str | None = None) -> None:
        self.fields_calculator()
        self.calc_window.functions_dialog(select)

    def field_report(self, name: str | None = None, solution: str = "", port: int = 1,
                     kind: str = "Rectangular") -> None:
        """A report of a value equation over frequency: a Fields report for one computed from the fields
        (Results > Create Fields Report: name None, the first such equation), else a solution data report."""
        from .calc_bridge import uses_fields, value_names
        if name is not None and not uses_fields(self.doc, [name]):
            self.create_report(kind, with_traces=[name])
            return
        if name is None:
            name = next(iter(value_names(self.doc, fields=True)), None)
        sols = F.field_solutions(self.doc)
        # over frequency: a sweep with saved fields, else the given solution
        sol = next((s for s in sols if not s.endswith("LastAdaptive")), solution or (sols[0] if sols else ""))
        prefix = "Fields Plot" if kind == "Rectangular" else "Fields Table"
        names = {r.name for r in self.des.reports}
        i = 1
        while f"{prefix} {i}" in names:
            i += 1
        r = Report(f"{prefix} {i}", kind, sol, [name] if name else [], {"port": str(port), "fields": "1"})
        self.des.reports.append(r)
        self.doc.dirty = True
        self.doc.changed.emit("results")
        self.open_report(r.name)

    def plot_fields(self, quantity: str) -> None:
        from .fieldplot_dialogs import FieldPlotDialog, default_attributes
        sols = F.field_solutions(self.doc)
        if not sols:
            QMessageBox.information(self, "Fields", "No field solution available. Analyze with 'Save fields' enabled.")
            return
        faces, objs = self._plot_selection()
        dlg = FieldPlotDialog(self, quantity, has_selection=bool(faces or objs))
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        quantity, pl = v["quantity"], v["plane"]

        def create(faces, objs):
            names = {p.name for p in self.des.field_plots}
            i = 1
            while f"{quantity}{i}" in names:
                i += 1
            props = {**default_attributes(), **v["props"]}
            fp = FieldPlot(f"{quantity}{i}", quantity, v["solution"], faces=faces if not pl else [],
                           objects=objs if not pl else [], plane=pl, props=props)
            self.des.field_plots.append(fp)
            self.doc.dirty = True
            self.doc.changed.emit("fields")
            self.show_field_plot(fp.name)
        if dlg.needs_pick():
            self.pick_plot_targets("solids" if v["props"]["Volume"] == "True" else "faces", create)
        else:
            create(faces, objs)

    def pick_plot_targets(self, what: str, on_use) -> None:
        """Plot on a selection with nothing selected: the faces, objects or solids are picked in the 3D view,
        with a box that says what to pick; Plot calls on_use(faces, objects)."""
        from .fieldplot_dialogs import PickTargets
        old = getattr(self, "_pick_bar", None)
        if old is not None:
            try:
                if old.isVisible():
                    old.reject()
            except RuntimeError:            # closed and deleted
                pass
        self._pick_bar = PickTargets(self, what, on_use)
        self._pick_bar.show()

    def _on_current_plot(self, fn) -> None:
        """Ribbon buttons act on the plot selected in the project tree, else the one on screen."""
        sel = self.project_tree.currentItem()
        from .trees import ROLE
        data = sel.data(0, ROLE) if sel is not None else None
        name = data[1] if isinstance(data, tuple) and data and data[0] == "fieldplot" else self.anim_plot
        if not name and self.des.field_plots:
            name = self.des.field_plots[-1].name
        if not name:
            QMessageBox.information(self, "Field Plot", "Create a field plot first.")
            return
        fn(name)

    def modify_field_plot(self, name: str) -> None:
        """Change quantity, solution, frequency, phase, port or location of an existing plot."""
        mp = next((p for p in self.des.field_plots if p.name == name and p.quantity == "Mesh"), None)
        if mp is not None:
            if self._mesh_dialog(mp):
                self.show_field_plot(name)
            return
        rf = next((p for p in self.des.field_plots if p.name == name and p.quantity == "RadiationField"), None)
        if rf is not None:
            props = dict(rf.props)
            if self._radiation_dialog(props, f"Modify {name}"):
                self.doc.checkpoint()
                rf.props = props
                self.doc.commit("all", geometry=False)
                self.show_field_plot(name)
            return
        from .fieldplot_dialogs import FieldPlotDialog
        fp = next((p for p in self.des.field_plots if p.name == name), None)
        if fp is None:
            return
        faces, objs = self._plot_selection()
        dlg = FieldPlotDialog(self, fp.quantity, fp, has_selection=bool(faces or objs))
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()

        def apply(faces, objs):
            new = v["name"] or fp.name
            if new != fp.name and any(p.name == new for p in self.des.field_plots):
                QMessageBox.warning(self, "Modify Field Plot", f"A plot named '{new}' already exists.")
                new = fp.name
            if self.anim_plot == fp.name:
                self.anim_plot = new
            fp.name = new
            fp.quantity, fp.solution = v["quantity"], v["solution"]
            fp.props.update(v["props"])
            if v["plane"]:
                fp.plane, fp.faces, fp.objects = v["plane"], [], []
            elif not v["keep"] and (faces or objs):
                fp.plane, fp.faces, fp.objects = "", faces, objs
            elif fp.plane and not v["keep"]:
                fp.plane = ""
            self.doc.dirty = True
            self.doc.changed.emit("fields")
            self.project_tree.rebuild()
            self.show_field_plot(fp.name)
        if dlg.needs_pick():
            v["keep"] = False
            self.pick_plot_targets("solids" if v["props"]["Volume"] == "True" else "faces", apply)
        else:
            apply(faces, objs)

    def field_plot_attributes(self, name: str) -> None:
        mp = next((p for p in self.des.field_plots if p.name == name and p.quantity == "Mesh"), None)
        if mp is not None:
            if self._mesh_dialog(mp):
                self.show_field_plot(name)
            return
        rf = next((p for p in self.des.field_plots if p.name == name and p.quantity == "RadiationField"), None)
        if rf is not None:
            props = dict(rf.props)
            if self._radiation_dialog(props, f"Modify {name}"):
                self.doc.checkpoint()
                rf.props = props
                self.doc.commit("all", geometry=False)
                self.show_field_plot(name)
            return
        from .fieldplot_dialogs import PlotAttributesDialog
        if not any(p.name == name for p in self.des.field_plots):
            return
        if self.anim_plot != name or name not in self.view.overlay_actors:
            self.show_field_plot(name)
        dlg = PlotAttributesDialog(self, name)
        dlg.show()
        self._attr_dlg = dlg

    def show_field_plot(self, name: str, focus: bool = True) -> None:
        """Draw (or redraw) a field overlay. focus: it becomes the selected plot and the other field plots
        are drawn half transparent; focus=False redraws it in place (Edit Sources, adaptive passes): the open
        tab, the selection and the other overlays stay."""
        if not any(p.name == name for p in self.des.field_plots):
            return
        if focus and name in self.anim_bar.names:
            self.anim_bar.close_player()          # the plot changed: its frames are stale
        self._hidden_plots.discard(name)
        if not self._draw_field_plot(name, loud=True):
            return
        if focus:
            self.tabs.setCurrentIndex(0)          # the plot is in the 3D Modeler (e.g. from the Equations tab)
            self.doc.select([])
            self.show_overlays([name])

    def show_overlays(self, sel: list[str] | None) -> None:
        """All field plots on screen: the ones in sel drawn full, the others half transparent; sel None
        (the Field Overlays folder): all full. Plots not drawn yet are drawn now."""
        names = [p.name for p in self.des.field_plots]
        sel = [n for n in (sel or []) if n in names]
        if sel:
            self._hidden_plots.difference_update(sel)
        else:
            self._hidden_plots.clear()
        for n in names:
            if n not in self.view.overlay_actors and n not in self._hidden_plots:
                self._draw_field_plot(n, loud=n in sel)
        self.view.set_overlay_focus(set(sel) if sel else None)
        if not self.view.overlay_actors:
            return
        self.tabs.setCurrentIndex(0)
        shown = [n for n in sel if n in self.view.overlay_actors]
        info = self._overlay_info.get(shown[-1], "") if len(shown) == 1 else \
            ", ".join(shown or [n for n in names if n in self.view.overlay_actors])
        self.hint.setText(info)
        for n in reversed(shown or names):
            if n in self.view.overlay_actors and not any(p.name == n and p.quantity in ("Mesh", "RadiationField")
                                                         for p in self.des.field_plots):
                self.anim_plot = n
                break

    def _draw_field_plot(self, name: str, loud: bool = True) -> bool:
        """Compute and draw one field overlay; loud: errors and warnings go to the message window."""
        fp = next((p for p in self.des.field_plots if p.name == name), None)
        if fp is None:
            return False
        if fp.quantity == "Mesh":
            try:
                meshes, info = F.mesh_overlay(self.doc, self.view, fp.solution, fp.objects,
                                              fp.props.get("Quality", "None"))
            except Exception as e:  # noqa: BLE001
                if loud:
                    self.msgs.add("error", f"{name}: {e}")
                return False
            self.view.set_overlay(name, meshes)
            self._overlay_info[name] = info
            return True
        if fp.quantity == "RadiationField":
            try:
                meshes, info = self._radiation_meshes(fp.props)
            except Exception as e:  # noqa: BLE001
                if loud:
                    self.msgs.add("error", f"{name}: {e}")
                return False
            self.view.set_overlay(name, meshes)
            self._overlay_info[name] = info
            return True
        try:
            meshes, info = F.make_overlay(self.doc, self.view, fp.quantity, fp.solution,
                                          int(fp.props.get("freq", 0)), int(fp.props.get("port", 1)),
                                          float(fp.props.get("phase", 0)), fp.faces, fp.objects, fp.plane,
                                          attrs=fp.props, animating=False)
        except Exception as e:  # noqa: BLE001
            if loud:
                self.msgs.add("error", f"Field plot '{name}': {e}")
            return False
        from .trees import solution_state
        if solution_state(self.doc, fp.solution.split(" : ")[0].strip()) == "outdated":
            if loud:
                self.msgs.add("warning", f"Field plot '{name}' shows an outdated solution: the model or setup "
                                         "changed after it was computed. Analyze again.")
            info = info + "   [outdated solution]"
        self.view.set_overlay(name, meshes)
        self._plot_geom[name] = self._helper_geometry(fp)
        self.field_ranges = getattr(self, "field_ranges", {})
        if meshes and meshes[0][1].get("clim") is not None:
            self.field_ranges[name] = tuple(meshes[0][1]["clim"])
        self._overlay_info[name] = info
        if loud and "  (" in info:
            self.msgs.add("warning", f"Field plot '{name}': {info.split('  (', 1)[1].rstrip(')')}")
        return True

    def _helper_geometry(self, fp) -> tuple:
        """The display shapes of the non-model objects a field plot lies on (helper sheets drawn for plots)."""
        names = dict.fromkeys([*(fp.objects or []), *[r.obj for r in (fp.faces or [])]])
        return tuple((n, id(self.doc.tess.get(n))) for n in names
                     if self.des.has_obj(n) and not self.des.obj(n).model)

    def _follow_helper_plots(self) -> None:
        """A field plot on a non-model object follows the object: moved, resized or reshaped, the plot is
        drawn again on the new shape (the solution stays valid, the object is not part of the model)."""
        geo = self._plot_geom
        for fp in self.des.field_plots:
            old = geo.get(fp.name)
            if not old or fp.name not in self.view.overlay_actors:
                continue
            new = self._helper_geometry(fp)
            if new == old or len(new) != len(old) or any(n not in self.doc.tess for n, _ in new):
                continue
            if fp.name in self.anim_bar.names:
                self.anim_bar.close_player()        # its frames show the old shape
            self._draw_field_plot(fp.name, loud=True)

    def edit_sources(self) -> None:
        """Edit Sources: post-processing magnitudes/phases and the incident wave field type."""
        from .dialogs import EditSourcesDialog
        from .terminal_dialogs import field_terminal_names
        from ..model import sources as SRC
        names = field_terminal_names(self.doc) if self.des.settings.get("SolutionType") == "Terminal" else None
        if not SRC.defaults(self.des, names):
            QMessageBox.information(self, "Edit Sources", "Assign a port or an incident wave first.")
            return
        state = {"applied": None}

        def apply(sources, ftype):
            if state["applied"] == (sources, ftype):
                return
            if state["applied"] is None:
                self.doc.checkpoint()      # one undo step for the whole dialog
            state["applied"] = (sources, ftype)
            self._apply_sources(sources, ftype)

        dlg = EditSourcesDialog(self, self.des, on_apply=apply, names=names)
        if dlg.exec() == QDialog.Accepted:
            apply(*dlg.values())

    def _apply_sources(self, sources: dict, ftype: str) -> None:
        """Store the Edit Sources values and update everything that shows the excitation: field and
        radiation plots, animations, reports."""
        from ..model import sources as SRC
        SRC.set_(self.des, sources, ftype)
        self.doc.commit("sources", geometry=False)
        self.anim_bar.close_player()
        shown = [p.name for p in self.des.field_plots if p.name in self.view.overlay_actors]
        for name in shown:
            try:
                self.show_field_plot(name, focus=False)
            except Exception as e:  # noqa: BLE001
                self.msgs.add("error", f"{name}: {e}")
        for w in list(self.report_tabs.values()):
            w.refresh()

    def toggle_animation(self) -> None:
        """Animate: set up the swept variable, precompute all frames, then play them back."""
        if self.anim_bar.is_playing():
            self.anim_bar.pause()
            return
        if not self.anim_plot:
            QMessageBox.information(self, "Animate", "Create a field plot first.")
            return
        fp = next((p for p in self.des.field_plots if p.name == self.anim_plot), None)
        if fp is None:
            return
        from .animation import AnimationSetupDialog, precompute
        try:
            st, sol = F.store_for(self.doc, fp.solution)
            _, flds = F.load_grid(st, sol)
            freqs, ports = F.frequencies(flds), F.ports(flds)
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", f"Animate: {e}")
            return
        # every field plot on screen is animated together (local); mesh and radiation plots stay as they are
        fps = [p for p in self.des.field_plots if p.quantity not in ("Mesh", "RadiationField")
               and (p.name in self.view.overlay_actors or p is fp)]
        dlg = AnimationSetupDialog(self, fp, freqs, ports)
        if all(p.quantity.startswith("ComplexMag") for p in fps):
            dlg.var.removeItem(0)                 # a complex magnitude does not change with phase
            if dlg.var.count() == 0:
                QMessageBox.information(self, "Animate", "This quantity does not depend on phase and the "
                                                         "solution has only one frequency and excitation.")
                return
        if dlg.exec() != QDialog.Accepted:
            return
        if fp.name not in self.view.overlay_actors:
            self.show_overlays([fp.name])
        try:
            frames = precompute(self, fps, dlg.frames(fp))
        except Exception as e:  # noqa: BLE001
            self.msgs.add("error", f"Animate '{fp.name}': {e}")
            return
        if frames:
            self.tabs.setCurrentIndex(0)
            self.anim_bar.load([p.name for p in fps], frames)
            self.anim_bar.play()

    def export_animation_gif(self) -> None:
        if not self.anim_bar.frames:
            QMessageBox.information(self, "Export GIF", "Start an animation first (Animate).")
            return
        self.anim_bar.export_gif()

    def clear_overlays(self, hide: bool = True) -> None:
        """Remove all field overlays; hide: they stay hidden until a plot or the Field Overlays folder is
        selected again."""
        self.anim_timer.stop()
        self.anim_bar.close_player()
        self.view.clear_overlays()
        if hide:
            self._hidden_plots = {p.name for p in self.des.field_plots}

    def _sync_overlays(self) -> None:
        """Overlays (and animation frames) of field plots that no longer exist are taken off the screen."""
        names = {p.name for p in self.des.field_plots}
        if any(n not in names for n in self.anim_bar.names):
            self.anim_bar.close_player()          # its frames would draw the deleted plot again
        gone = [n for n in self.view.overlay_actors if n not in names]
        for n in gone:
            self.view.remove_overlay(n, render=False)
            self._overlay_info.pop(n, None)
        self._hidden_plots &= names
        if self.anim_plot and self.anim_plot not in names:
            self.anim_plot = None
        if gone:
            self.hint.setText("Ready")
            self.view.request_render()

    def hide_overlay(self, name: str) -> None:
        if name in self.anim_bar.names:
            self.anim_bar.close_player()
        self._hidden_plots.add(name)
        self.view.remove_overlay(name)

    def plot_mesh(self, objs: list[str] | None = None) -> None:
        """Plot Mesh: listed under Field Overlays; Modify sets the element quality colouring."""
        objs = objs or self.doc.selected_objects()
        if not self.des.setups:
            QMessageBox.information(self, "Plot Mesh", "Analyze first.")
            return
        if not objs:
            self.pick_plot_targets("objects", lambda _faces, picked: self.plot_mesh(picked))
            return
        names = {p.name for p in self.des.field_plots}
        i = 1
        while f"Mesh{i}" in names:
            i += 1
        self.doc.checkpoint()
        self.des.field_plots.append(FieldPlot(f"Mesh{i}", "Mesh", self.des.setups[0].name, objects=list(objs),
                                              props={"Quality": "None"}))
        self.doc.commit("all", geometry=False)
        self.show_field_plot(f"Mesh{i}")

    def _mesh_dialog(self, fp) -> bool:
        from PySide6.QtWidgets import QFormLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Modify {fp.name}")
        fl = QFormLayout(dlg)
        fl.addRow("Objects:", QLabel(", ".join(fp.objects)))
        q = QComboBox()
        q.addItems(F.MESH_QUALITY)
        q.setCurrentText(fp.props.get("Quality", "None"))
        fl.addRow("Element quality:", q)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        fl.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return False
        self.doc.checkpoint()
        fp.props = {**fp.props, "Quality": q.currentText()}
        self.doc.commit("all", geometry=False)
        return True

    # ==========================================================================================
    # project tree interaction
    # ==========================================================================================
    def _project_selected(self, data) -> None:
        if isinstance(data, list):
            ass = []
            for d in data:
                if d and d[0] in ("boundary", "excitation", "meshop"):
                    try:
                        ass.append(self.des.assignment(d[1]))
                    except KeyError:
                        pass
            self.view.show_assignments(ass)
            plots = [d[1] for d in data if d and d[0] == "fieldplot"]
            if plots:
                self.doc.select([])
                self.show_overlays(plots)
            elif any(d == ("group", "Field Overlays") for d in data):
                self.show_overlays(None)
            return
        if not data:
            self.view.show_assignments([])
            return
        kind = data[0]
        if kind in ("boundary", "excitation", "meshop"):
            try:
                self.view.show_assignments([self.des.assignment(data[1])])
            except KeyError:
                return
            self.props.show_assignment(kind, data[1])
        else:
            self.view.show_assignments([])
        if kind == "setup":
            self.props.show_setup(data[1])
        elif kind == "sweep":
            self.props.show_sweep(data[1], data[2])
        elif kind == "design":
            if data[1] != self.doc.design_name:
                self._close_reports()
                self.view.clear_overlays()
                self.doc.set_design(data[1])
                self.view._first_fit = True
            self.props.show_design()
        elif kind == "fieldplot":
            self.doc.select([])
            self.show_overlays([data[1]])
        elif data == ("group", "Field Overlays"):
            self.show_overlays(None)

    def _project_activated(self, data) -> None:
        if not data:
            return
        kind = data[0]
        if kind in ("boundary", "excitation", "meshop"):
            a = self.des.assignment(data[1])
            fam = {"boundary": "boundary", "excitation": "excitation", "meshop": "mesh"}[kind]
            snapshot = self.doc.project.to_dict()
            if edit_assignment(self, self.doc, fam, a):
                self.doc.undo_stack.append(snapshot)
                self.doc.commit("boundaries", geometry=False)
        elif kind == "setup":
            snapshot = self.doc.project.to_dict()
            if edit_setup(self, self.doc, self.des.setup(data[1])):
                self.doc.undo_stack.append(snapshot)
                self.doc.commit("analysis", geometry=False)
        elif kind == "sweep":
            st = self.des.setup(data[1])
            sw = next(x for x in st.sweeps if x.name == data[2])
            snapshot = self.doc.project.to_dict()
            if edit_sweep(self, self.doc, sw):
                self.doc.undo_stack.append(snapshot)
                self.doc.commit("analysis", geometry=False)
        elif kind == "sphere":
            sp = next(x for x in self.des.spheres if x.name == data[1])
            if edit_sphere(self, self.doc, sp):
                self.doc.commit("radiation", geometry=False)
        elif kind == "report":
            self.open_report(data[1])
        elif kind == "fieldplot":
            self.modify_field_plot(data[1])
        elif kind == "equation":
            self.fields_calculator(data[1])
        elif kind == "eqfunc":
            self.equation_functions(data[1])
        elif kind == "optim":
            self.edit_optimization(data[1])
        elif kind == "param":
            self.edit_parametric(data[1])
        elif kind == "variation":
            self.show_variation(data[1], json.loads(data[2]))
        elif kind in ("design",):
            self.edit_variables(False)
        elif kind == "materials":
            self.edit_materials()
        elif kind == "projvars":
            self.edit_variables(True)
        elif kind == "autoopen":
            self.solution_type()
        elif kind == "model":
            self.tabs.setCurrentIndex(0)

    def _project_context(self, data, pos) -> None:
        m = QMenu(self)
        if not data:
            return
        kind = data[0]
        if kind == "project":
            m.addAction("Insert Design", self.insert_design)
            m.addAction("Save", self.file_save)
            m.addAction("Rename...", self.rename_project)
            m.addAction("Project Variables...", lambda: self.edit_variables(True))
        elif kind == "design":
            m.addAction("Solution Type...", self.solution_type)
            m.addAction("Design Properties...", lambda: self.edit_variables(False))
            m.addAction("Validation Check...", self.validation_check)
            m.addAction("Analyze All", self.analyze_all)
            m.addSeparator()
            m.addAction("Rename...", self.rename_design)
            m.addAction("Copy Design", self.copy_design)
            m.addAction("Delete Design", self.delete_design)
        elif kind == "group":
            g = data[1]
            if g == "Boundaries":
                sub = m.addMenu("Assign")
                for k in schema.BOUNDARIES:
                    sub.addAction(k, lambda _=False, k=k: self.assign("boundary", k))
                m.addAction("Auto Open / Solution Type...", self.solution_type)
                m.addAction("Delete All", lambda: self.delete_all("boundary"))
            elif g == "Excitations":
                sub = m.addMenu("Assign")
                for k in schema.EXCITATIONS:
                    sub.addAction(k, lambda _=False, k=k: self.assign("excitation", k))
                m.addAction("Edit Sources...", self.edit_sources)
                m.addAction("Differential Pairs...", self.differential_pairs)
                m.addAction("Delete All", lambda: self.delete_all("excitation"))
            elif g == "Mesh":
                sub = m.addMenu("Assign Mesh Operation")
                for k in schema.MESH_OPS:
                    sub.addAction(k, lambda _=False, k=k: self.assign("mesh", k))
            elif g == "Analysis":
                m.addAction("Add Solution Setup...", self.add_setup)
                m.addAction("Add Eigenmode Setup...", lambda: self.add_setup(True))
                m.addAction("Analyze All", self.analyze_all)
            elif g == "Results":
                self._fill_report_menu(m)
                m.addSeparator()
                if len(self.des.setups) > 1:
                    sd = m.addMenu("Solution Data")
                    for su in self.des.setups:
                        sd.addAction(su.name, lambda _=False, n=su.name: self.solution_data(n))
                elif self.des.setups:
                    m.addAction("Solution Data...", lambda: self.solution_data(self.des.setups[0].name))
                m.addAction("Export S-Parameters (Touchstone)...", self.export_touchstone)
                m.addAction("Derivative Tuning...", lambda: self.derivative_tuning())
                m.addSeparator()
                m.addAction("Clean Up Solutions...", self.clean_up_solutions)
            elif g == "Equations":
                m.addAction("New Equation", self.new_equation)
                m.addAction("Open Library...", self.open_equation_library)
                m.addAction("Functions...", self.equation_functions)
                m.addSeparator()
                m.addAction("Import Equations...", lambda: self.import_equations())
                a = m.addAction("Export Equations...", self.export_equations)
                a.setEnabled(bool(self.des.field_exprs or self.des.field_funcs))
            elif g == "Field Overlays":
                m.addAction("Clear Overlays", self.clear_overlays)
            elif g == "Radiation":
                m.addAction("Insert Far Field Setup > Infinite Sphere...", self.add_sphere)
            elif g == "Optimization":
                m.addAction("Add Parametric...", self.add_parametric)
                m.addAction("Add Optimization...", self.add_optimization)
                if self.des.parametrics:
                    m.addSeparator()
                    m.addAction("Analyze All Parametrics", self.analyze_all_parametrics)
        elif kind in ("boundary", "excitation", "meshop"):
            m.addAction("Properties...", lambda: self._project_activated(data))
            a = self.des.assignment(data[1])
            m.addAction("Disable" if a.enabled else "Enable", lambda: self._toggle_enabled(data[1]))
            if kind != "meshop":
                m.addAction("Reassign to Selection", lambda: self._reassign(data[1]))
            if a.kind in ("Lumped Port", "Lumped RLC"):
                m.addSeparator()
                m.addAction("Draw Integration Line", lambda: self.draw_integration_line(data[1]))
                m.addAction("Auto Integration Line", lambda: self.auto_integration_line(data[1]))
                m.addSeparator()
            if a.kind == "Wave Port":
                m.addSeparator()
                n = max(1, int(float(a.props.get("NumberOfModes", "1") or 1)))
                term = self.des.settings.get("SolutionType") == "Terminal"
                for k in range(1, (n if not term else max(n, 4)) + 1):
                    m.addAction(f"Port Field Display: Mode {k}", lambda k=k: self.port_field_display(data[1], k))
                m.addAction("Hide Port Field", lambda: self.view.remove_overlay(f"Port field {data[1]}"))
                m.addSeparator()
            if a.kind in ("Wave Port", "Lumped Port"):
                m.addAction("Differential Pairs...", self.differential_pairs)
            m.addAction("Delete", self._delete_project_items)
        elif kind == "setup":
            m.addAction("Properties...", lambda: self._project_activated(data))
            m.addAction("Add Frequency Sweep...", lambda: self.add_sweep(data[1]))
            m.addAction("Analyze", lambda: self.analyze([data[1]]))
            m.addAction("Generate Mesh", lambda: self.generate_mesh([data[1]]))
            m.addAction("Solution Data...", lambda: self.solution_data(data[1]))
            m.addAction("Derivative Tuning...", lambda: self.derivative_tuning(data[1], "LastAdaptive"))
            m.addAction("Clean Up Solutions...", lambda: self.clean_up_solutions([data[1]]))
            m.addAction("Copy", lambda: self._copy_setup(data[1]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "sweep":
            m.addAction("Properties...", lambda: self._project_activated(data))
            m.addAction("Solution Data...", lambda: self.solution_data(data[1]))
            m.addAction("Export S-Parameters (Touchstone)...",
                        lambda: self.export_touchstone(f"{data[1]} : {data[2]}"))
            m.addAction("Analyze", lambda: self.analyze_sweep(data[1], data[2]))
            m.addAction("Analyze (setup)", lambda: self.analyze([data[1]]))
            m.addAction("Derivative Tuning...", lambda: self.derivative_tuning(data[1], data[2]))
            on = self._sweep(data[1], data[2]).props.get("Enabled", "True") == "True"
            m.addAction("Disable" if on else "Enable", lambda: self._toggle_sweep(data[1], data[2]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "report":
            m.addAction("Open", lambda: self.open_report(data[1]))
            m.addAction("Rename...", lambda: self.rename_report(data[1]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "equation":
            from .calc_bridge import expression_kinds
            k = expression_kinds(self.doc).get(data[1], "")
            m.addAction("Open", lambda: self.fields_calculator(data[1]))
            if k == "field":
                m.addAction("Plot Field...", lambda: self.plot_fields(data[1]))
            elif k == "value":
                m.addAction("Add to Report", lambda: self.field_report(data[1]))
            elif k == "vector":                    # a vector value: its magnitude, the components in the report
                m.addAction("Add to Report", lambda: self.field_report(f"mag({data[1]})"))
            m.addSeparator()
            m.addAction("Rename...", lambda: self.rename_equation(data[1]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "eqfunc":
            m.addAction("Edit...", lambda: self.equation_functions(data[1]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "optim":
            m.addAction("Properties...", lambda: self.edit_optimization(data[1]))
            m.addAction("Analyze", lambda: self.analyze_optimization(data[1]))
            m.addAction("View Results...", lambda: self.optimization_results(data[1]))
            m.addAction("Apply Best Variables", lambda: self.apply_best_variables(data[1]))
            m.addSeparator()
            m.addAction("Copy", lambda: self.copy_optimization(data[1]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "variation":
            m.addAction("Show Variation", lambda: self.show_variation(data[1], json.loads(data[2])))
            m.addAction("Back to Nominal", self.show_nominal)
        elif kind == "param":
            m.addAction("Properties...", lambda: self.edit_parametric(data[1]))
            m.addAction("Analyze", lambda: self.analyze_parametric(data[1]))
            m.addAction("View Analysis Result...", lambda: self.parametric_results(data[1]))
            m.addSeparator()
            m.addAction("Copy", lambda: self.copy_parametric(data[1]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "fieldplot":
            m.addAction("Show", lambda: self.show_field_plot(data[1]))
            m.addAction("Modify...", lambda: self.modify_field_plot(data[1]))
            m.addAction("Modify Attributes...", lambda: self.field_plot_attributes(data[1]))
            m.addSeparator()
            if not any(p.name == data[1] and p.quantity in ("RadiationField", "Mesh") for p in self.des.field_plots):
                m.addAction("Animate", lambda: (setattr(self, "anim_plot", data[1]), self.toggle_animation()))
            m.addAction("Hide", lambda: self.hide_overlay(data[1]))
            m.addAction("Delete", self._delete_project_items)
        elif kind == "sphere":
            m.addAction("Properties...", lambda: self._project_activated(data))
            m.addAction("Compute Antenna Parameters...", lambda: self.antenna_parameters(data[1]))
            m.addAction("Overlay 3D Pattern on Model", lambda: self.overlay_pattern(data[1]))
            m.addAction("Create Far Fields Report > 3D Polar Plot", lambda: self.create_report("3D Polar Plot"))
            m.addSeparator()
            m.addAction("Delete", self._delete_project_items)
        if not m.isEmpty():
            m.exec(pos)

    def _toggle_enabled(self, name: str) -> None:
        self.doc.checkpoint()
        a = self.des.assignment(name)
        a.enabled = not a.enabled
        self.doc.commit("boundaries", geometry=False)

    # ---- lumped components (Lumped Elements tab) ---------------------------------------------
    def _trace_points_mm(self, name: str) -> np.ndarray:
        t = self.doc.tess.get(name)
        if t is None:
            raise ValueError(f"'{name}' has no geometry")
        return np.asarray(t.points, float) * 1e3

    def insert_component(self, kind: str, package: str | None = None) -> None:
        """SMD component with its pads, element sheet and Lumped RLC boundary: placed by a click on the board,
        or in the gap between the two selected traces."""
        objs = self.doc.selected_objects() if self.doc.selection_mode == "object" else []
        if len(objs) == 2 and all(self.des.has_obj(o) and self.des.obj(o).dim >= 2 for o in objs):
            self._insert_between(kind, package, objs)
            return
        self._place_target = (kind, package)
        self.start_draw("PlaceComponent")

    def _component_placed(self, p) -> None:
        from ..model import lumped as LU
        from .lumped_dialogs import ComponentDialog
        kind, package = getattr(self, "_place_target", ("C", None))
        normal = PLANE_NORMAL[self.view.grid_plane]
        dlg = ComponentDialog(self, kind, None, package, normal)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.result_values()
        place = LU.Placement(tuple(float(x) * 1e3 for x in p), v["axis"], normal)
        self._add_component(v, None, None, None, place)

    def _insert_between(self, kind: str, package: str | None, objs: list[str]) -> None:
        from ..model import lumped as LU
        from .lumped_dialogs import ComponentDialog
        try:
            ends = LU.trace_ends(self._trace_points_mm(objs[0]), self._trace_points_mm(objs[1]))
        except ValueError as e:
            QMessageBox.warning(self, "Insert Component", str(e))
            return
        dlg = ComponentDialog(self, kind, ends.gap, package)
        if dlg.exec() != QDialog.Accepted:
            return
        self._add_component(dlg.result_values(), objs[0], objs[1], ends, None)

    def _add_component(self, v: dict, a, b, ends, place) -> None:
        from ..model import lumped as LU
        self.doc.checkpoint()
        try:
            bnd, made, warns = LU.insert_component(
                self.doc.project, self.des, a, b, ends, v["pkg"], v["land"], v["kind"], v["value"], v["name"],
                parasitics=v["parasitics"], terminations=v["terminations"], touchstone=v["touchstone"],
                mount=v["mount"], place=place)
        except Exception as e:  # noqa: BLE001
            self.doc.undo()
            QMessageBox.warning(self, "Insert Component", str(e))
            return
        self.doc.commit("model")
        self.view.show_assignments([bnd])
        for w in warns:
            self.msgs.add("warning", f"{bnd.name}: {w}")
        where = f"between {a} and {b}" if a else "at the clicked point (move it with its variables " \
            + ", ".join(k for k in self.des.variables if k.startswith(made[-1].name + "_")) + ")"
        self.msgs.add("info", f"Inserted {v['pkg'].name} '{made[-1].name}' {where}: "
                              f"{', '.join(o.name for o in made)}")

    def draw_integration_line(self, name: str) -> None:
        """Start and end point of the integration line picked in the 3D view (two clicks, with snapping)."""
        self._line_target = name
        self.start_draw("IntegrationLine")

    def _line_drawn(self, P: list) -> None:
        name, self._line_target = getattr(self, "_line_target", None), None
        if not name or len(P) < 2 or name not in {a.name for a in self.des.excitations + self.des.boundaries}:
            return
        if np.linalg.norm(P[1] - P[0]) < 1e-15:
            self.msgs.add("warning", "Integration line: start and end point are the same")
            return
        self._set_integration_line(name, f"{self._pt(P[0])};{self._pt(P[1])}")

    def auto_integration_line(self, name: str) -> None:
        """Integration line placed again between the conductors that touch the sheet."""
        a = self.des.assignment(name)
        line = self._auto_integration_line(a.faces, a.objects)
        if not line:
            self.msgs.add("warning", f"{name}: no integration line found, draw it or enter it in the properties")
            return
        self._set_integration_line(name, line)

    def _line_on_object(self, a, line: str) -> str:
        """Integration line picked in the model (global) in the coordinates of its object, so that it moves
        with the object."""
        from ..model.objframe import to_object_coords
        try:
            return to_object_coords(self.des, a, line, self.doc.evaluator())
        except Exception as e:  # noqa: BLE001
            self.msgs.add("warning", f"{a.name}: integration line kept in global coordinates ({e})")
            return line

    def _set_integration_line(self, name: str, line: str) -> None:
        self.doc.checkpoint()
        a = self.des.assignment(name)
        line = self._line_on_object(a, line)
        a.props["IntegrationLine"] = line
        self.doc.commit("boundaries" if any(b is a for b in self.des.boundaries) else "excitations", geometry=False)
        self.view.show_assignments([a])
        self.msgs.add("info", f"{name}: integration line {line}")

    def _reassign(self, name: str) -> None:
        faces, objs = self._selection_refs()
        if not faces and not objs:
            QMessageBox.information(self, "Reassign", "Select faces or objects first.")
            return
        self.doc.checkpoint()
        a = self.des.assignment(name)
        a.faces, a.objects = faces, objs
        self.doc.commit("boundaries", geometry=False)
        self.view.show_assignments([a])

    def _copy_setup(self, name: str) -> None:
        self.doc.checkpoint()
        s = copy.deepcopy(self.des.setup(name))
        s.name = self.des.unique_name(name + "_copy")
        self.des.setups.append(s)
        self.doc.commit("analysis", geometry=False)

    def _sweep(self, setup: str, name: str):
        return next(x for x in self.des.setup(setup).sweeps if x.name == name)

    def _toggle_sweep(self, setup: str, name: str) -> None:
        """a disabled sweep is skipped by Analyze (shown grey in the tree)."""
        sw = self._sweep(setup, name)
        self.doc.checkpoint()
        sw.props["Enabled"] = "False" if sw.props.get("Enabled", "True") == "True" else "True"
        self.doc.commit("analysis", geometry=False)
        self.project_tree.rebuild()
        self.msgs.add("info", f"Sweep '{setup} : {name}' {'enabled' if sw.props['Enabled'] == 'True' else 'disabled'}")

    def _delete_project_items(self) -> None:
        items = [it.data(0, Qt.UserRole + 1) for it in self.project_tree.selectedItems()]
        if not items:
            return
        self.doc.checkpoint()
        for d in items:
            if not d:
                continue
            k = d[0]
            if k == "boundary":
                self.des.boundaries = [a for a in self.des.boundaries if a.name != d[1]]
            elif k == "excitation":
                self.des.excitations = [a for a in self.des.excitations if a.name != d[1]]
            elif k == "meshop":
                self.des.mesh_ops = [a for a in self.des.mesh_ops if a.name != d[1]]
            elif k == "setup":
                self.des.setups = [s for s in self.des.setups if s.name != d[1]]
            elif k == "sweep":
                st = self.des.setup(d[1])
                st.sweeps = [s for s in st.sweeps if s.name != d[2]]
            elif k == "report":
                self.des.reports = [r for r in self.des.reports if r.name != d[1]]
                w = self.report_tabs.pop(d[1], None)
                if w is not None:
                    self.tabs.removeTab(self.tabs.indexOf(w))
            elif k == "fieldplot":
                self.des.field_plots = [p for p in self.des.field_plots if p.name != d[1]]
                self.view.remove_overlay(d[1])
            elif k == "equation":
                self.des.field_exprs = [e for e in self.des.field_exprs if e.name != d[1]]
            elif k == "eqfunc":
                self.des.field_funcs = [f for f in self.des.field_funcs if f.split("(", 1)[0].strip() != d[1]]
            elif k == "sphere":
                self.des.spheres = [s for s in self.des.spheres if s.name != d[1]]
            elif k == "optim":
                self.des.optimetrics = [o for o in self.des.optimetrics if o.name != d[1]]
            elif k == "param":
                self.des.parametrics = [o for o in self.des.parametrics if o.name != d[1]]
        self.view.show_assignments([])
        self.doc.commit("all", geometry=False)

    def insert_design(self) -> None:
        self.doc.checkpoint()
        names = {d.name for d in self.doc.project.designs}
        i = 1
        while f"Design{i}" in names:
            i += 1
        self.doc.project.designs.append(Design(name=f"Design{i}", units=self.settings.value("modeler/default_units", "mm")))
        self.doc.commit("all", geometry=False)
        self._close_reports()
        self.doc.set_design(f"Design{i}")

    def filter_hairpin(self) -> None:
        """Tools > Filter Design > Tapped Hairpin: inserts the parametrized filter as a new design."""
        from .filter_tool import HairpinFilterDialog
        self._insert_generated(HairpinFilterDialog(self, self.doc.project), "hairpin filter")

    def antenna_patch(self) -> None:
        """Tools > Antenna Design > Rectangular Patch: inserts the parametrized patch as a new design."""
        from .antenna_tool import PatchAntennaDialog
        self._insert_generated(PatchAntennaDialog(self, self.doc.project), "patch antenna")

    def _insert_generated(self, dlg, what: str) -> None:
        if dlg.exec() != QDialog.Accepted or dlg.design is None:
            return
        d = dlg.design
        names = {x.name for x in self.doc.project.designs}
        base, i = d.name, 1
        while d.name in names:
            i += 1
            d.name = f"{base}_{i}"
        self.doc.checkpoint()
        self.doc.project.designs.append(d)
        self.doc.commit("all", geometry=False)
        self._close_reports()
        self.doc.set_design(d.name)
        self.view.fit_all()
        opt = f" and the optimization {d.optimetrics[0].name}" if d.optimetrics else ""
        self.msgs.add("info", f"Created the {what} design {d.name} with Setup1 : Sweep{opt}")

    def copy_design(self) -> None:
        self.doc.checkpoint()
        d = design_from_dict(json.loads(json.dumps(self.doc.project.to_dict()["designs"][
            [x.name for x in self.doc.project.designs].index(self.doc.design_name)])))
        names = {x.name for x in self.doc.project.designs}
        base = d.name + "_copy"
        n = base
        i = 1
        while n in names:
            i += 1
            n = f"{base}{i}"
        d.name = n
        self.doc.project.designs.append(d)
        self.doc.commit("all", geometry=False)

    def rename_design(self) -> None:
        new, ok = QInputDialog.getText(self, "Rename Design", "Name:", text=self.doc.design_name)
        if ok and new.strip() and new not in {d.name for d in self.doc.project.designs}:
            self.doc.checkpoint()
            self.des.name = new.strip()
            self.doc.design_name = new.strip()
            self.doc.commit("all", geometry=False)

    def rename_project(self) -> None:
        new, ok = QInputDialog.getText(self, "Rename Project", "Name:", text=self.doc.project.name)
        if ok and new.strip():
            self.doc.project.name = new.strip()
            self.doc.commit("all", geometry=False)

    def delete_design(self) -> None:
        if len(self.doc.project.designs) < 2:
            QMessageBox.information(self, "Delete Design", "A project needs at least one design.")
            return
        if QMessageBox.question(self, "Delete Design", f"Delete design '{self.doc.design_name}'?") != QMessageBox.Yes:
            return
        self.doc.checkpoint()
        self.doc.project.designs = [d for d in self.doc.project.designs if d.name != self.doc.design_name]
        self._close_reports()
        self.doc.set_design(self.doc.project.designs[0].name)
        self.doc.commit("all")

    def edit_variables(self, project_level: bool) -> None:
        dlg = VariablesDialog(self.doc, self, project_level)

        def apply(vars_: dict) -> None:
            cur = self.doc.project.variables if project_level else self.des.variables
            if cur == vars_:
                return
            self.doc.checkpoint()
            if project_level:
                self.doc.project.variables = dict(vars_)
            else:
                self.des.variables = dict(vars_)
            self.doc.commit("variables")
        dlg.on_apply = apply
        if dlg.exec() == QDialog.Accepted:
            apply(dlg.vars)

    # ==========================================================================================
    # context menus
    # ==========================================================================================
    def _plot_fields_menu(self, m: QMenu) -> QMenu:
        """Plot Fields: the built-in quantities and the field equations of the Fields Calculator."""
        pf = m.addMenu("Plot Fields")
        self._fill_plot_fields(pf)
        return pf

    def _fill_plot_fields(self, pf: QMenu) -> None:
        pf.clear()
        for q in F.QUANTITIES:
            pf.addAction(q, lambda _=False, q=q: self.plot_fields(q))
        eq = pf.addMenu("Equations")
        from .calc_bridge import expression_kinds
        try:
            fields = [n for n, k in expression_kinds(self.doc).items() if k == "field"]
        except Exception:  # noqa: BLE001
            fields = []
        for n in fields:
            eq.addAction(n, lambda _=False, n=n: self.plot_fields(n))
        if fields:
            eq.addSeparator()
        eq.addAction("New Equation...", lambda: self.fields_calculator(for_plot=True))

    def _view_context(self, pos) -> None:
        m = QMenu(self)
        sel = self.doc.selection
        mode = self.doc.selection_mode
        if sel:
            if mode == "object":
                m.addAction(self.a_material)
                m.setToolTipsVisible(True)
            b = m.addMenu("Assign Boundary")
            for k in schema.BOUNDARIES:
                b.addAction(k, lambda _=False, k=k: self.assign("boundary", k))
            e = m.addMenu("Assign Excitation")
            for k in schema.EXCITATIONS:
                e.addAction(k, lambda _=False, k=k: self.assign("excitation", k))
            me = m.addMenu("Assign Mesh Operation")
            for k in schema.MESH_OPS:
                me.addAction(k, lambda _=False, k=k: self.assign("mesh", k))
            self._plot_fields_menu(m)
            if mode == "object":
                m.addAction("Plot Mesh", self.plot_mesh)
                m.addSeparator()
                ed = m.addMenu("Edit")
                ed.addAction(self.a_copy)
                ed.addAction(self.a_delete)
                self._arrange_menu(ed.addMenu("Arrange"), self._dup_menu(ed))
                bo = m.addMenu("Boolean")
                for a in (self.a_unite, self.a_subtract, self.a_intersect):
                    bo.addAction(a)
                self._group_actions(m)
                m.addAction("Hide Selection", lambda: self.set_visible(False))
                m.addAction("Properties...", self.edit_selected_properties)
            elif mode == "edge":
                m.addAction("Fillet...", self.fillet)
                m.addAction("Chamfer...", self.chamfer)
            elif mode == "vertex":
                m.addAction("Fillet...", self.fillet)
                m.addAction("Chamfer...", self.chamfer)
            elif mode == "face":
                m.addAction("Uncover Faces", lambda: self.face_op("UncoverFaces"))
                m.addAction("Detach Faces", self.detach_faces)
                m.addAction("Sweep Faces Along Normal...", lambda: self.face_op("SweepFacesAlongNormal"))
            m.addSeparator()
        m.addAction(self.a_paste)
        sm = m.addMenu("Selection Mode")
        for a in self.a_mode.values():
            sm.addAction(a)
        v = m.addMenu("View")
        v.addAction(self.a_fit)
        v.addAction("Show All", self.show_all)
        for w in ("iso", "top", "front", "right"):
            v.addAction(w.capitalize(), lambda _=False, w=w: self.view.view(w))
        self.radiation_menu(m)
        if not sel:
            self._plot_fields_menu(m)         # on a cut plane: fields need no selected object
        m.addAction("Clear Field Overlays", self.clear_overlays)
        m.exec(self.view.mapToGlobal(pos))

    def _model_tree_context(self, data, pos) -> None:
        m = QMenu(self)
        if data and data[0] == "object":
            name = data[1]
            if name not in self.doc.selected_objects():
                self.set_mode("object")
                self.doc.select([Pick(name)])
            m.addAction("Properties...", lambda: self.edit_object_dialog(name))
            m.addAction(self.a_material)
            m.setToolTipsVisible(True)
            m.addAction("Hide" if self.des.obj(name).visible else "Show",
                        lambda: self.set_visible(not self.des.obj(name).visible))
            m.addAction("Copy", self.copy_objects)
            m.addAction("Delete", self.delete_selection)
            self._group_actions(m)
            b = m.addMenu("Assign Boundary")
            for k in schema.BOUNDARIES:
                b.addAction(k, lambda _=False, k=k: self.assign("boundary", k))
            e = m.addMenu("Assign Excitation")
            for k in schema.EXCITATIONS:
                e.addAction(k, lambda _=False, k=k: self.assign("excitation", k))
        elif data and data[0] == "objgroup":
            name = data[1]
            names = G.members(self.des, name)
            if set(names) != set(self.doc.selected_objects()):
                self.set_mode("object")
                self.doc.select([Pick(n) for n in names])
            m.addAction("Ungroup", lambda: self.ungroup_selection([name]))
            m.addAction("Rename...", lambda: self.rename_group(name))
            m.addAction(self.a_material)
            m.setToolTipsVisible(True)
            m.addAction("Hide", lambda: self.set_visible(False))
            m.addAction("Show", lambda: self.set_visible(True))
            m.addAction("Copy", self.copy_objects)
            m.addAction("Delete", self.delete_selection)
        elif data and data[0] == "command":
            obj, idx = data[1], data[2]
            c = self.des.obj(obj).history[idx]
            m.addAction("Properties...", lambda: self.edit_command(obj, idx))
            if idx > 0:
                m.addAction("Unsuppress" if c.suppressed else "Suppress", lambda: self._suppress(obj, idx))
                m.addAction("Delete Command", lambda: self._delete_command(obj, idx))
        elif data and data[0] == "cs":
            n = data[1]
            m.addAction("Set Working CS", lambda: self.set_working_cs(n))
            if n != "Global":
                m.addAction("Properties...", lambda: self.edit_cs(n))
                m.addAction("Delete", lambda: self.delete_cs(n))
        elif data and data[0] == "group" and data[1] == "Coordinate Systems":
            m.addAction("Create Relative CS...", lambda: self.create_relative_cs())
            m.addAction("Create Face CS", self.create_face_cs)
            m.addAction("Create Object CS", self.create_object_cs)
        elif data and data[0] == "material":
            m.addAction("Select Objects", lambda: self.doc.select(
                [Pick(o.name) for o in self.des.live_objects() if o.material == data[1]]))
        else:
            m.addAction("Show All", self.show_all)
        m.exec(pos)

    def _group_actions(self, m) -> None:
        """Group / Ungroup entries of a context menu, as far as they apply to the selection."""
        objs = self.doc.selected_objects()
        if len(G.expand(self.des, objs)) > 1 and len({self.des.obj(n).group or n for n in objs}) > 1:
            m.addAction(self.a_group)
        if any(self.des.obj(n).group for n in objs if self.des.has_obj(n)):
            m.addAction(self.a_ungroup)

    def _suppress(self, obj: str, idx: int) -> None:
        self.doc.checkpoint()
        c = self.des.obj(obj).history[idx]
        c.suppressed = not c.suppressed
        self.doc.commit("model")

    def _delete_command(self, obj: str, idx: int) -> None:
        self.doc.checkpoint()
        o = self.des.obj(obj)
        c = o.history.pop(idx)
        if c.kind in ("Unite", "Subtract", "Intersect"):
            for t in [x.strip() for x in c.props.get("Tools", "").split(",") if x.strip()]:
                if self.des.has_obj(t):
                    self.des.obj(t).consumed_by = None
        self.doc.commit("model")

    def edit_selected_properties(self) -> None:
        objs = self.doc.selected_objects()
        if objs:
            self.edit_object_dialog(objs[0])

    # ==========================================================================================
    def clean_up_solutions(self, preselect: list[str] | None = None) -> None:
        """Results > Clean Up Solutions: delete solution data of chosen setups, either
        everything (meshes, S-parameters, fields) or only the field data."""
        from PySide6.QtWidgets import QCheckBox, QGroupBox, QRadioButton, QDialogButtonBox
        if not self.des.setups:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Clean Up Solutions")
        lay = QVBoxLayout(dlg)
        box = QGroupBox("Setups")
        v = QVBoxLayout(box)
        checks = {}
        for st in self.des.setups:
            d = self.solution_dir(st.name)
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0
            c = QCheckBox(f"{st.name}    ({size / 2**20:.1f} MB)" if size else f"{st.name}    (no solution)")
            c.setChecked(bool(size) and (preselect is None or st.name in preselect))
            c.setEnabled(bool(size))
            v.addWidget(c)
            checks[st.name] = c
        lay.addWidget(box)
        opt = QGroupBox("Delete")
        ov = QVBoxLayout(opt)
        r_all = QRadioButton("All solutions (meshes, convergence, S-parameters, fields)")
        r_fld = QRadioButton("Fields only (keep meshes and matrix data)")
        r_all.setChecked(True)
        ov.addWidget(r_all)
        ov.addWidget(r_fld)
        lay.addWidget(opt)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = [n for n, c in checks.items() if c.isChecked()]
        if not chosen:
            return
        busy = self.solver.job.setup if self.solver.job is not None else None
        from . import fields as F
        import shutil
        F._cache.clear()
        self.clear_overlays(hide=False)
        for n in chosen:
            if n == busy and self.solver.busy:
                self.msgs.add("warning", f"{n} is being solved; its solution was not deleted")
                continue
            d = self.solution_dir(n)
            if r_all.isChecked():
                shutil.rmtree(d, ignore_errors=True)
                self.msgs.add("info", f"Deleted all solutions of {n}")
            else:
                for f in list(d.glob("fields_*.npz")) + list(d.glob("solution_*.npz")) + list(d.glob("farfield*")):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                self.msgs.add("info", f"Deleted field solutions of {n}")
        self.doc.changed.emit("results")

    def solution_dir(self, setup: str) -> Path:
        return results_dir(self.doc.project.path, self.doc.design_name, setup)

    def about(self) -> None:
        QMessageBox.about(self, "About Hertzon",
                          f"<b>Hertzon {__version__}</b><br>Open-source 3D full-wave FEM workflow with adaptive meshing."
                          "<br>By Dominik Mair, built on <a href='https://github.com/FennisRobert/EMerge'>EMerge</a>, "
                          "the FEM solver by Robert Fennis (GPL v2+), which provides the finite-element core.<br>Geometry: gmsh/OpenCASCADE, display: VTK/PyVista.<br><br>"
                          "Licensed under the GNU GPL v2 or later. Provided without any warranty: check simulation "
                          "results yourself before relying on them.")

    def show_help(self) -> None:
        from .helpwin import HelpDialog, context_title
        dlg = getattr(self, "_help_dlg", None)
        if dlg is None:
            dlg = self._help_dlg = HelpDialog(self)
        dlg.show()
        dlg.raise_()
        dlg.goto(context_title(self))

    def show_feature_matrix(self) -> None:
        p = Path(__file__).resolve().parents[2] / "docs" / "FEATURE_MATRIX.md"
        txt = p.read_text(encoding="utf-8") if p.exists() else "docs/FEATURE_MATRIX.md not found"
        dlg = QDialog(self)
        dlg.setWindowTitle("Feature Overview")
        lay = QVBoxLayout(dlg)
        from PySide6.QtWidgets import QTextBrowser
        tb = QTextBrowser()
        tb.setMarkdown(txt)
        lay.addWidget(tb)
        dlg.resize(900, 700)
        dlg.exec()


def _attach_unit(text: str, unit: str) -> str:
    """Give every bare number in a coordinate expression an explicit unit."""
    import re
    if not text.strip():
        return text
    parts = []
    for item in text.split(";"):
        comps = []
        for c in item.split(","):
            c = c.strip()
            comps.append(f"({c})*1{unit}" if c and not re.search(r"[A-Za-z_]", c) else c)
        parts.append(",".join(comps))
    return ";".join(parts)
