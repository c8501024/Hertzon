"""Docked panels: Properties, Message Manager, Progress; and the Solution Data dialog."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QTabWidget, QDialog, QTableWidget, QTableWidgetItem, QComboBox,
                               QHeaderView, QDialogButtonBox, QFormLayout, QAbstractItemView)

from ..model import schema
from ..model.materials import normalize_color
from .props import PropertyGrid
from ..solver.results import ResultStore, results_dir
from .doc import object_attributes, result_dir


class PropertiesPanel(QWidget):
    """Shows the properties of whatever is selected (object, command, boundary, setup...)."""

    edited = Signal(str)          # what changed ("model", "boundaries", ...)

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.tabs = QTabWidget(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight:bold; padding:2px;")
        lay.addWidget(self.title)
        lay.addWidget(self.tabs)
        self.target = None
        self._snap_done = False

    def clear(self) -> None:
        self.tabs.clear()
        self.title.setText("")
        self.target = None

    def _grid(self, title: str, props, values: dict, on_change, ev=True, readonly=()) -> PropertyGrid:
        g = PropertyGrid(show_evaluated=ev)
        g.set(props, values, self.doc.evaluator() if ev else None,
              materials=[m.name for m in self.doc.project.all_materials()], readonly=readonly)
        g.changed.connect(on_change)
        self.tabs.addTab(g, title)
        return g

    def show_object(self, name: str, command_index: int | None = None) -> None:
        self.clear()
        des = self.doc.design
        if not des.has_obj(name):
            return
        o = des.obj(name)
        self.target = ("object", name)
        self.title.setText(f"{name}")
        for i, c in enumerate(o.history):
            if command_index is not None and i != command_index and command_index >= 0:
                continue
            vals = dict(c.props)

            def ch(k, v, i=i, vals=vals):
                self.doc.checkpoint()
                self.doc.design.obj(name).history[i].props[k] = v
                self.doc.commit("model")
            self._grid(f"{c.kind}" if i else f"Command: {c.kind}", schema.GEOMETRY.get(c.kind, []), vals, ch)
        attrs = {"Name": o.name, "Material": o.material, "SolveInside": str(o.solve_inside), "Model": str(o.model),
                 "Color": o.color, "Transparency": f"{o.transparency:g}", "DisplayWireframe": str(o.wireframe),
                 "Orientation": o.orientation}

        def ach(k, v):
            self.doc.checkpoint()
            ob = self.doc.design.obj(name)
            geometry = False
            if k == "Name":
                v = v.strip()
                if not v or (v != ob.name and v in self.doc.design.all_names()):
                    self.doc.message.emit("error", f"Cannot rename to '{v}'")
                    return
                rename_object(self.doc.design, ob.name, v)
                geometry = True
            elif k == "Material":
                ob.material = v
                try:
                    from ..model.materials import is_conductor
                    m = self.doc.project.material(v)
                    ob.color = m.color
                    ob.solve_inside = not is_conductor(m) and ob.model
                except KeyError:
                    pass
            elif k == "SolveInside":
                ob.solve_inside = v == "True"
            elif k == "Orientation":
                ob.orientation = v      # the creation command is read in this coordinate system
                geometry = True
            elif k == "Model":
                ob.model = v == "True"
                if ob.model:
                    try:
                        from ..model.materials import is_conductor
                        ob.solve_inside = not is_conductor(self.doc.project.material(ob.material))
                    except KeyError:
                        ob.solve_inside = True
                else:
                    ob.solve_inside = False      # a non-model object is not solved at all
                geometry = True
            elif k == "Color":
                ob.color = normalize_color(v, ob.color)
            elif k == "Transparency":
                try:
                    ob.transparency = max(0.0, min(1.0, float(v)))
                except ValueError:
                    return
            elif k == "DisplayWireframe":
                ob.wireframe = v == "True"
            self.doc.commit("model" if geometry else "attributes", geometry=geometry)
        g = self._grid("Attribute", object_attributes(self.doc, name), attrs, ach, ev=False)
        if command_index is None:
            self.tabs.setCurrentWidget(g)

    def show_assignment(self, family: str, name: str) -> None:
        self.clear()
        try:
            a = self.doc.design.assignment(name)
        except KeyError:
            return
        self.target = (family, name)
        props = {"boundary": schema.BOUNDARIES, "excitation": schema.EXCITATIONS, "meshop": schema.MESH_OPS}[family][a.kind]
        self.title.setText(f"{a.name} ({a.kind})")
        vals = dict(a.props)

        def ch(k, v):
            self.doc.checkpoint()
            self.doc.design.assignment(name).props[k] = v
            self.doc.commit({"boundary": "boundaries", "excitation": "excitations", "meshop": "mesh"}[family],
                            geometry=False)
        self._grid("General", props, vals, ch)

    def show_setup(self, name: str) -> None:
        self.clear()
        s = self.doc.design.setup(name)
        self.target = ("setup", name)
        self.title.setText(name)
        vals = dict(s.props)

        def ch(k, v):
            self.doc.checkpoint()
            self.doc.design.setup(name).props[k] = v
            self.doc.commit("analysis", geometry=False)
        props = schema.EIGEN_SETUP if s.eigen else schema.SETUP
        des = self.doc.design
        pw = not des.ports() and any(e.kind == "Incident Plane Wave" and e.enabled for e in des.excitations)
        # an incident wave design converges on Delta Energy, a port design on Delta S
        drop = ({"MaximumDeltaS", "UseMatrixConvergence", "MaxDeltaMag", "MaxDeltaPhase"} if pw
                else {"MaximumDeltaEnergy"})
        props = [p for p in props if p.name not in drop]
        for tab in dict.fromkeys(p.tab for p in props):
            self._grid(tab, [p for p in props if p.tab == tab], vals, ch)

    def show_sweep(self, setup: str, sweep: str) -> None:
        self.clear()
        s = self.doc.design.setup(setup)
        sw = next(x for x in s.sweeps if x.name == sweep)
        self.target = ("sweep", setup, sweep)
        self.title.setText(f"{setup} : {sweep}")
        vals = dict(sw.props)

        def ch(k, v):
            self.doc.checkpoint()
            next(x for x in self.doc.design.setup(setup).sweeps if x.name == sweep).props[k] = v
            self.doc.commit("analysis", geometry=False)
        self._grid("General", schema.SWEEP, vals, ch)

    def show_design(self) -> None:
        self.clear()
        des = self.doc.design
        self.target = ("design", des.name)
        self.title.setText(des.name)
        vals = dict(des.variables)
        props = [schema.Prop(k, "expr", v, help="Design variable") for k, v in des.variables.items()]

        def ch(k, v):
            self.doc.checkpoint()
            self.doc.design.variables[k] = v
            self.doc.commit("variables")
        self._grid("Variables", props, vals, ch)
        svals = dict(des.settings)

        def sch(k, v):
            self.doc.checkpoint()
            self.doc.design.settings[k] = v
            self.doc.commit("settings", geometry=False)
        self._grid("Design Settings", schema.DESIGN_SETTINGS, svals, sch)


def rename_object(des, old: str, new: str) -> None:
    for o in des.objects:
        if o.name == old:
            o.name = new
        if o.consumed_by == old:
            o.consumed_by = new
        for c in o.history:
            if "Tools" in c.props:
                c.props["Tools"] = ",".join(new if t.strip() == old else t.strip() for t in c.props["Tools"].split(",")
                                            if t.strip())
    for lst in (des.boundaries, des.excitations, des.mesh_ops):
        for a in lst:
            a.objects = [new if x == old else x for x in a.objects]
            for f in a.faces:
                if f.obj == old:
                    f.obj = new
            for e in a.edges:
                if e.obj == old:
                    e.obj = new
    for fp in des.field_plots:
        fp.objects = [new if x == old else x for x in fp.objects]
        for f in fp.faces:
            if f.obj == old:
                f.obj = new


class MessageManager(QListWidget):
    """Message list; clicking a message selects the object / boundary / setup it is about."""
    COLORS = {"info": "#1b4f72", "warning": "#b9770e", "error": "#c0392b", "debug": "#7f8c8d"}
    ICONS = {"info": "ⓘ", "warning": "⚠", "error": "✖", "debug": "·"}
    messageActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.itemClicked.connect(lambda it: self.messageActivated.emit(it.data(Qt.UserRole) or ""))
        self.setToolTip("Click a message to select the object, boundary or setup it refers to.\n"
                        "Right-click: copy or clear messages.")
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def _menu(self, pos) -> None:
        from PySide6.QtWidgets import QApplication, QMenu
        m = QMenu(self)
        sel = self.selectedItems()
        a = m.addAction("Copy", lambda: QApplication.clipboard().setText("\n".join(i.text() for i in sel)))
        a.setEnabled(bool(sel))
        a = m.addAction("Clear Selected Messages", lambda: [self.takeItem(self.row(i)) for i in sel])
        a.setEnabled(bool(sel))
        m.addSeparator()
        a = m.addAction("Clear All Messages", self.clear)
        a.setEnabled(self.count() > 0)
        m.exec(self.viewport().mapToGlobal(pos))

    def add(self, level: str, text: str) -> None:
        if level == "debug" and not getattr(self, "show_debug", False):
            return
        stamp = time.strftime("%H:%M:%S")
        it = QListWidgetItem(f"{self.ICONS.get(level, '')} [{stamp}] {text}")
        it.setData(Qt.UserRole, text)
        it.setForeground(QBrush(QColor(self.COLORS.get(level, "#000"))))
        self.addItem(it)
        if self.count() > 3000:
            self.takeItem(0)
        self.scrollToBottom()


def job_kind(j) -> str:
    if j.optimization:
        return "Optimization"
    if getattr(j, "parametric", ""):
        return "Parametric"
    if getattr(j, "tuning", ""):
        return "Tuning"
    return "Generate Mesh" if j.mesh_only else ("Sweep only" if j.sweep else "Analyze")


class ProgressPanel(QWidget):
    """Progress window with the analysis queue: every running or queued job is a row with its own
    progress bar and state, estimate from the last run; abort, remove from queue, reorder. When this
    computer shares itself (Tools > Options > Remote), the analyses other computers sent here are rows too."""
    abortRequested = Signal()
    hostedFetched = Signal(object)          # list of the jobs of the remote service on this computer
    HOSTED_POLL_MS = 3000
    COLS = ["Analysis", "Progress", "State", "Estimate (last run)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solver = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 220)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table, 1)
        btns = QVBoxLayout()
        self.abort = QPushButton("Abort")
        self.abort.setEnabled(False)
        self.abort.clicked.connect(self.abortRequested.emit)
        btns.addWidget(self.abort)
        for text, fn in (("Remove", self._remove), ("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1))):
            bt = QPushButton(text)
            bt.clicked.connect(fn)
            btns.addWidget(bt)
        btns.addStretch()
        lay.addLayout(btns)
        self._progress: dict[str, tuple[float, str]] = {}      # running job -> (percent, text)
        self._post: tuple[str, float] | None = None
        self.abort.clicked.disconnect()
        self.abort.clicked.connect(self._abort_clicked)
        self._hosted: list[dict] = []                           # analyses of other computers solving here
        self._hosted_rows: dict[int, str] = {}                  # table row -> job id on the service
        self._fetching = False
        self.hostedFetched.connect(self._set_hosted)
        from PySide6.QtCore import QTimer
        self._hosted_timer = QTimer(self)
        self._hosted_timer.timeout.connect(self._poll_hosted)
        self._hosted_timer.start(self.HOSTED_POLL_MS)

    # ---- analyses other computers sent to this one ---------------------------------------------------
    def _poll_hosted(self) -> None:
        if self._fetching:
            return
        self._fetching = True
        import threading

        def fetch():
            try:
                from ..remote import local
                jobs = local.hosted_jobs() or []
            except Exception:  # noqa: BLE001
                jobs = []
            self.hostedFetched.emit(jobs)
        threading.Thread(target=fetch, daemon=True).start()

    def _set_hosted(self, jobs: list) -> None:
        self._fetching = False
        if jobs != self._hosted:
            self._hosted = jobs
            self.refresh()

    def _abort_clicked(self) -> None:
        jid = self._hosted_rows.get(self.table.currentRow())
        if jid is None:
            self.abortRequested.emit()
            return
        import threading

        def abort():
            try:
                from ..remote import local
                local.abort_hosted(jid)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=abort, daemon=True).start()

    def _bar(self, row: int, value: float | None) -> None:
        """Progress bar in the row (None: queued, no bar)."""
        if value is None:
            self.table.removeCellWidget(row, 1)
            return
        bar = self.table.cellWidget(row, 1)
        if not isinstance(bar, QProgressBar):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(True)
            self.table.setCellWidget(row, 1, bar)
        bar.setValue(int(max(0.0, min(100.0, value))))

    def _set(self, row: int, col: int, text: str) -> None:
        it = self.table.item(row, col)
        if it is None:
            self.table.setItem(row, col, QTableWidgetItem(text))
        elif it.text() != text:
            it.setText(text)

    def post_progress(self, text: str | None, value: float = 0.0) -> None:
        """A row in the queue for post-processing in the GUI (field data for a plot, far fields ...);
        text None removes it."""
        old = self._post
        self._post = (text, value) if text is not None else None
        if (old is None) != (self._post is None) or (old and self._post and old[0] != text):
            self.refresh()
        elif self._post and self.table.rowCount():
            self._bar(0, value)

    def set_manager(self, solver) -> None:
        self.solver = solver
        solver.queueChanged.connect(self.refresh)
        self.refresh()

    @staticmethod
    def estimate(job) -> str:
        """Time and memory of the last run of this setup (shows them in the profile)."""
        try:
            prof = ResultStore(job.out).read_json("profile.json", None)
        except Exception:  # noqa: BLE001
            prof = None
        if not prof:
            return "no previous run"
        tot = max((float(x.get("total", 0)) for x in prof), default=0.0)
        mem = max((float(x.get("memory", 0) or 0) for x in prof), default=0.0)
        txt = f"~{tot:.0f} s" if tot < 120 else f"~{tot / 60:.1f} min"
        if mem > 0:
            txt += f", ~{mem / 1024:.2f} GB" if mem > 1024 else f", ~{mem:.0f} MB"
        return txt

    def refresh(self) -> None:
        if self.solver is None:
            return
        jobs = ([(self.solver.job, "Running")] if self.solver.job else []) + [(j, "Queued") for j in self.solver.queue]
        off = 1 if self._post else 0
        self.table.setRowCount(len(jobs) + off + len(self._hosted))
        if self._post:
            text, value = self._post
            for c, v in ((0, "Post-processing"), (2, text), (3, "")):
                self._set(0, c, v)
            self._bar(0, value)
        running = {j.key for j, st in jobs if st == "Running"}
        self._progress = {k: v for k, v in self._progress.items() if k in running}
        for r, (j, state) in enumerate(jobs, start=off):
            kind = job_kind(j)
            value, text = self._progress.get(j.key, (0.0, "")) if state == "Running" else (None, "")
            self._set(r, 0, j.key)
            self._set(r, 2, (f"{kind}: {text}" if text else f"{state} ({kind})"))
            self.table.item(r, 2).setToolTip(self.table.item(r, 2).text())
            self._set(r, 3, self.estimate(j))
            self._bar(r, value)
        self._hosted_rows = {}
        for r, h in enumerate(self._hosted, start=off + len(jobs)):
            who = h.get("client") or "another computer"
            name = h.get("label") or f"{h.get('project', '')} - {h.get('design', '')} - {h.get('name', '')}"
            self._hosted_rows[r] = h.get("id", "")
            self._set(r, 0, f"{name} (from {who})")
            if h.get("state") == "running":
                self._set(r, 2, f"Solving for {who}" + (f": {h['text']}" if h.get("text") else ""))
                self._bar(r, float(h.get("progress", 0.0)))
            else:
                self._set(r, 2, f"Queued for {who}")
                self._bar(r, None)
            self.table.item(r, 2).setToolTip("Sent by another computer to the remote service on this one; "
                                             "select it and press Abort to stop it")
            self._set(r, 3, "")
        self.abort.setEnabled(bool(running) or bool(self._hosted))

    def _sel_key(self) -> str | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.text() if it else None

    def _remove(self) -> None:
        k = self._sel_key()
        if k and self.solver:
            if self.solver.job and self.solver.job.key == k:
                self.abortRequested.emit()
            else:
                self.solver.remove(k)

    def _move(self, d: int) -> None:
        k = self._sel_key()
        if k and self.solver:
            self.solver.move(k, d)

    def update_progress(self, key: str, value: float, text: str) -> None:
        self._progress[key] = (value, text)
        self.abort.setEnabled(0 <= value < 100 and text not in ("Done", "Failed"))
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.text() == key and self.solver is not None and self.solver.job is not None \
                    and self.solver.job.key == key:
                j = self.solver.job
                kind = job_kind(j)
                self._set(r, 2, f"{kind}: {text}")
                self.table.item(r, 2).setToolTip(self.table.item(r, 2).text())
                self._bar(r, value)
                return


class SolutionDataDialog(QDialog):
    """Solutions window: Profile / Convergence / Matrix Data / Mesh Statistics (/ Eigenmode Data)."""

    def __init__(self, doc, setup: str, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle(f"Solutions: {doc.project.name} - {doc.design.name}")
        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.sim = QComboBox()
        self.sim.addItems([s.name for s in doc.design.setups])
        self.sim.setCurrentText(setup)
        self.sim.currentTextChanged.connect(self._load)
        top.addRow("Simulation:", self.sim)
        # the current design, every solved parametric variation and every optimization evaluation with
        # results: their passes, matrix data and mesh statistics without changing the design
        self.var = QComboBox()
        self.var.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.var.setMinimumContentsLength(40)
        self.var.activated.connect(lambda *_: self._load(self.sim.currentText(), keep_variation=True))
        top.addRow("Design Variation:", self.var)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        lay.addWidget(bb)
        self.resize(960, 620)
        self._load(setup)

    def reload(self, *_args) -> None:
        self._load(self.sim.currentText(), keep_variation=True)

    def _variations(self, setup: str, keep: bool) -> Path | None:
        """Fill the Design Variation list for `setup`; the result folder of the chosen entry. The first entry
        is the current design (the solution the model window shows)."""
        from ..solver.parametric import solution_variations
        doc = self.doc
        if not doc.project.path:
            self.var.clear()
            self.var.setEnabled(False)
            return None
        shown = result_dir(doc, setup)
        try:
            others = solution_variations(doc.project, doc.design, setup)
        except Exception:  # noqa: BLE001  (a run is writing its table right now)
            others = []
        nominal = results_dir(doc.project.path, doc.design.name, setup)
        first = "Nominal" if shown == nominal else \
            "Current design: " + next((lbl for lbl, f in others if f == shown), str(shown))
        old = self.var.currentData() if keep and self.var.currentIndex() > 0 else None
        self.var.blockSignals(True)
        self.var.clear()
        self.var.addItem(first, None)
        self.var.setItemData(0, first, Qt.ToolTipRole)
        for lbl, f in others:
            self.var.addItem(lbl, str(f))
            self.var.setItemData(self.var.count() - 1, lbl, Qt.ToolTipRole)
        i = self.var.findData(old) if old else 0
        self.var.setCurrentIndex(max(0, i))
        self.var.setEnabled(self.var.count() > 1)
        self.var.blockSignals(False)
        data = self.var.currentData()
        return Path(data) if data else shown

    def _load(self, setup: str, keep_variation: bool = False) -> None:
        folder = self._variations(setup, keep_variation)
        self.st = ResultStore(folder) if folder is not None else None
        cur = self.tabs.currentIndex()
        old = [self.tabs.widget(i) for i in range(self.tabs.count())]
        self.tabs.clear()
        for wdg in old:
            wdg.deleteLater()
        self.tabs.addTab(self._profile(), "Profile")
        self.tabs.addTab(self._convergence(), "Convergence")
        self.tabs.addTab(self._matrix(), "Matrix Data")
        self.tabs.addTab(self._mesh(), "Mesh Statistics")
        eig = self.st.read_json("eigenmodes.json") if self.st else None
        if eig:
            self.tabs.addTab(self._eigen(eig), "Eigenmode Data")
        # a long line (solver error, many ports or adaptive frequencies, sweep points) must not set the
        # window's minimum width: labels wrap, combo boxes elide
        for lbl in self.tabs.findChildren(QLabel):
            lbl.setWordWrap(True)
            lbl.setMinimumWidth(80)          # also for one long word (a path): clipped, full text selectable
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for cb in self.tabs.findChildren(QComboBox):
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(min(28, max(6, max((len(cb.itemText(i)) for i in range(cb.count())),
                                                           default=6))))
        self.tabs.setCurrentIndex(max(0, cur))

    def _table(self, headers, rows) -> QTableWidget:
        t = QTableWidget(len(rows), len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(str(v)))
        return t

    def _profile(self) -> QWidget:
        prof = self.st.read_json("profile.json", []) if self.st else []
        from ..solver.results import run_status
        status = run_status(self.st.folder) if self.st else {}
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(f"Status: {status.get('state', 'no solution')}"
                             + (f"   Error: {status['error']}" if status.get("error") else "")))
        lay.addWidget(self._table(["Task", "Real Time", "Elapsed Total", "Memory (peak)"],
                                  [[p["task"], f"{p['elapsed']:.2f} s", f"{p['total']:.2f} s",
                                    f"{p['memory']:.0f} MB" if p.get("memory") else ""] for p in prof]))
        ao = self.st.read_json("auto_open.json") if self.st else None
        if ao:
            lay.addWidget(QLabel(f"Auto Open: {ao['kind']}, padding {ao['padding'] * 1e3:.4g} mm "
                                 f"(lambda at lowest frequency {ao['lambda_low'] * 1e3:.4g} mm)"))
        return w

    # ---- convergence ---------------------------------------------------------------------------
    CONV_QTY = {"Max Mag. Delta S": "delta_s", "Number of Tetrahedra": "tets", "% Delta Tets": "delta_tets",
                "Max Delta |S|": "delta_mag", "Max Delta Phase (deg)": "delta_phase", "Solve Time (s)": "time"}

    def _convergence(self) -> QWidget:
        conv = self.st.read_json("convergence.json", {}) if self.st else {}
        passes = conv.get("passes", []) or []
        w = QWidget()
        lay = QVBoxLayout(w)
        state = "Converged" if conv.get("converged") else ("Not converged" if passes else "No data")
        fa = conv.get("frequencies") or []
        pw = bool(conv.get("plane_wave") or conv.get("quantity") == "Delta Energy")
        dq = "Delta Energy" if pw else "Delta S"
        qmap = dict(self.CONV_QTY)
        if pw:  # incident wave: no S-parameters, the pass criterion is Delta Energy
            qmap = {"Delta Energy": "delta_s", "Number of Tetrahedra": "tets", "% Delta Tets": "delta_tets",
                    "Solve Time (s)": "time"}
        lay.addWidget(QLabel(f"Solved Passes: {len(passes)}    {state}    Target Max {dq}: "
                             f"{conv.get('max_delta_s', '')}    Adaptive frequency: "
                             + ", ".join(f"{f / 1e9:.6g} GHz" for f in fa)))
        row = QHBoxLayout()
        view = QComboBox()
        view.addItems(["Table", "Plot"])
        qty = QComboBox()
        qty.addItems(list(qmap))
        row.addWidget(QLabel("View:"))
        row.addWidget(view)
        row.addWidget(QLabel("Plot quantity:"))
        row.addWidget(qty)
        row.addStretch(1)
        lay.addLayout(row)
        rows = []
        for p in passes:
            r = [p["pass"], p["tets"], f"{p['delta_tets']:.1f}" if "delta_tets" in p else "N/A",
                 f"{p['delta_s']:.5f}" if "delta_s" in p else "N/A"]
            if not pw:
                r += [f"{p['delta_mag']:.5f}" if "delta_mag" in p else "N/A",
                      f"{p['delta_phase']:.3f}" if "delta_phase" in p else "N/A"]
            rows.append(r + ["Yes" if p.get("passed") else ("No" if "passed" in p else "N/A"),
                             f"{p.get('time', 0):.1f} s"])
        cols = ["Pass Number", "# Tetrahedra", "% Delta Tets", "Delta Energy" if pw else "Max Mag. Delta S"]
        if not pw:
            cols += ["Max Delta |S|", "Max Delta Phase (deg)"]
        table = self._table(cols + ["Converged", "Solve Time"], rows)
        lay.addWidget(table)
        fig = Figure(figsize=(6, 3), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        lay.addWidget(canvas)
        canvas.hide()

        def draw():
            fig.clear()
            ax = fig.add_subplot(111)
            key = qmap[qty.currentText()]
            pts = [(p["pass"], p[key]) for p in passes if key in p]
            if pts:
                x, y = zip(*pts)
                if key == "delta_s":
                    ax.semilogy(x, y, "o-")
                    ax.axhline(conv.get("max_delta_s", 0.02), color="r", ls="--", label="Target")
                    ax.legend(fontsize=8)
                else:
                    ax.plot(x, y, "o-")
            from matplotlib.ticker import MaxNLocator
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
            ax.set_xlabel("Pass")
            ax.set_ylabel(qty.currentText())
            ax.grid(True, alpha=0.3)
            canvas.draw_idle()

        def switch():
            plot = view.currentText() == "Plot"
            table.setVisible(not plot)
            canvas.setVisible(plot)
            if plot:
                draw()
        view.currentTextChanged.connect(switch)
        qty.currentTextChanged.connect(lambda *_: draw())
        return w

    # ---- matrix data ---------------------------------------------------------------------------
    def _matrix(self) -> QWidget:
        from .reports import adaptive_pass_data, network
        w = QWidget()
        lay = QVBoxLayout(w)
        if not self.st:
            return w
        top = QFormLayout()
        sol = QComboBox()
        sols = self.st.solutions()
        sol.addItems(sols)
        conv = self.st.read_json("convergence.json", {}) or {}
        npass = len([p for p in conv.get("passes", []) if p.get("S")])
        if npass:
            sol.addItem("Adaptive Passes")
        fsel = QComboBox()
        kind = QComboBox()
        kind.addItems(["S", "Y", "Z"])
        fmt = QComboBox()
        fmt.addItems(["Mag/Phase", "dB/Phase", "Real/Imag"])
        top.addRow("Solution", sol)
        top.addRow("Frequency / Pass", fsel)
        top.addRow("Matrix", kind)
        top.addRow("Format", fmt)
        lay.addLayout(top)
        table = QTableWidget()
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(table)
        btns = QHBoxLayout()
        exp_ts = QPushButton("Export Touchstone...")
        exp_csv = QPushButton("Export Matrix Data (CSV)...")
        btns.addStretch(1)
        btns.addWidget(exp_ts)
        btns.addWidget(exp_csv)
        lay.addLayout(btns)

        def data():
            if sol.currentText() == "Adaptive Passes":
                return adaptive_pass_data(self.st, "AdaptivePasses")
            return self.st.sparams(sol.currentText())

        def load_freqs():
            d = data()
            fsel.blockSignals(True)
            fsel.clear()
            if d is not None:
                passes = d.get("xaxis") == "Pass"
                fsel.addItem("All Passes" if passes else "All Frequencies")
                if passes:
                    fsel.addItems([f"Pass {int(x)}" for x in d["freq"]])
                else:
                    fsel.addItems([f"{f / 1e9:.6g} GHz" for f in d["freq"]])
                # a sweep opens as a table over frequency, a single point / passes as the matrix
                fsel.setCurrentIndex(0 if len(d["freq"]) > 1 else 1)
            fsel.blockSignals(False)
            exp_ts.setEnabled(d is not None and d.get("xaxis") != "Pass")
            fill()

        def fmt_val(v):
            if fmt.currentText() == "Mag/Phase":
                return f"{abs(v):.5f}, {np.degrees(np.angle(v)):.2f}°"
            if fmt.currentText() == "dB/Phase":
                return f"{20 * np.log10(max(abs(v), 1e-300)):.3f} dB, {np.degrees(np.angle(v)):.2f}°"
            return f"{v.real:.5g} {'+' if v.imag >= 0 else '-'} j{abs(v.imag):.5g}"

        def fill():
            d = data()
            if d is None or fsel.currentIndex() < 0:
                table.setRowCount(0)
                return
            cat = {"S": "S Parameter", "Y": "Y Parameter", "Z": "Z Parameter"}[kind.currentText()]
            MM = network(d, cat)
            if fsel.currentIndex() == 0:
                # all frequencies: one row per frequency, columns S11 S21 S12 S22 ... (column-major)
                n = MM.shape[1]
                k = kind.currentText()
                pairs = [(i, j) for j in range(n) for i in range(n)]
                passes = d.get("xaxis") == "Pass"
                table.setRowCount(len(d["freq"]))
                table.setColumnCount(1 + len(pairs))
                table.setHorizontalHeaderLabels(["Pass" if passes else "Freq [GHz]"]
                                                + [f"{k}{i + 1}{j + 1}" if n < 10 else f"{k}({i + 1},{j + 1})"
                                                   for i, j in pairs])
                table.setVerticalHeaderLabels([str(r + 1) for r in range(len(d["freq"]))])
                for r, f in enumerate(d["freq"]):
                    table.setItem(r, 0, QTableWidgetItem(str(int(f)) if passes else f"{f / 1e9:.6g}"))
                    for c, (i, j) in enumerate(pairs):
                        table.setItem(r, c + 1, QTableWidgetItem(fmt_val(MM[r, i, j])))
                table.resizeColumnsToContents()
                return
            M = MM[fsel.currentIndex() - 1]
            n = M.shape[0]
            table.setRowCount(n)
            table.setColumnCount(n)
            hdr = [str(p.get("name", i + 1)) for i, p in enumerate(self._ports(d))] or [str(i + 1) for i in range(n)]
            hdr = (hdr + [str(i + 1) for i in range(n)])[:n]
            table.setHorizontalHeaderLabels(hdr)
            table.setVerticalHeaderLabels(hdr)
            for i in range(n):
                for j in range(n):
                    table.setItem(i, j, QTableWidgetItem(fmt_val(M[i, j])))
            table.resizeColumnsToContents()

        def export_ts():
            from PySide6.QtWidgets import QFileDialog
            d = data()
            if d is None:
                return
            n = d["S"].shape[1]
            path, _ = QFileDialog.getSaveFileName(self, "Export Touchstone",
                                                  f"{self.doc.design.name}_{sol.currentText()}.s{n}p",
                                                  f"Touchstone (*.s{n}p)")
            if path:
                self.st.write_touchstone(sol.currentText(), d["freq"], d["S"], d["z0"], path)

        def export_csv():
            from PySide6.QtWidgets import QFileDialog
            d = data()
            if d is None:
                return
            path, _ = QFileDialog.getSaveFileName(self, "Export Matrix Data", f"{self.doc.design.name}_matrix.csv",
                                                  "CSV (*.csv)")
            if not path:
                return
            cat = {"S": "S Parameter", "Y": "Y Parameter", "Z": "Z Parameter"}[kind.currentText()]
            M = network(d, cat)
            n = M.shape[1]
            k = kind.currentText()
            xh = "Pass" if d.get("xaxis") == "Pass" else "Freq [Hz]"
            hdr = [xh] + [f"{part}({k}({i + 1},{j + 1}))" for j in range(n) for i in range(n)
                          for part in ("re", "im")]
            cols = [d["freq"]]
            for j in range(n):
                for i in range(n):
                    cols += [M[:, i, j].real, M[:, i, j].imag]
            np.savetxt(path, np.column_stack(cols), delimiter=",", header=",".join(hdr), comments="")

        sol.currentTextChanged.connect(load_freqs)
        fsel.currentIndexChanged.connect(fill)
        kind.currentTextChanged.connect(fill)
        fmt.currentTextChanged.connect(fill)
        exp_ts.clicked.connect(export_ts)
        exp_csv.clicked.connect(export_csv)
        load_freqs()
        from ..solver.results import s_labels
        # one entry per row of S: a port with several modes (Floquet TE00 / TM00) has a row per mode, a terminal
        # solution a row per terminal
        rows = s_labels(self.st)
        if rows:
            term = rows[0][1] == "Terminal"
            lay.addWidget(QLabel(("Terminals: " if term else "Ports: ") + ", ".join(
                f"{i + 1}: {n}" if term else f"{i + 1}: {n} ({k})" for i, (n, k) in enumerate(rows))))
        return w

    def _ports(self, d) -> list:
        import json
        try:
            p = d.get("ports")
            if isinstance(p, np.ndarray):
                p = json.loads(str(p))
            return list(p or [])
        except Exception:  # noqa: BLE001
            return []

    # ---- mesh statistics -----------------------------------------------------------------------
    def _mesh(self) -> QWidget:
        from ..model.expr import LENGTH_UNITS
        ms = self.st.read_json("mesh_stats.json", {}) if self.st else {}
        units = self.doc.design.units
        s = LENGTH_UNITS.get(units, 1e-3)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(f"Total: {ms.get('tets', 'N/A')} tetrahedra, {ms.get('nodes', 'N/A')} nodes. "
                             f"Lengths in {units}, volumes in {units}^3."))
        rows = []
        for o in ms.get("objects", []):
            if "min_edge" in o:
                rows.append([o["object"], o["tets"], f"{o['min_edge'] / s:.4g}", f"{o['max_edge'] / s:.4g}",
                             f"{o['rms_edge'] / s:.4g}", f"{o['min_vol'] / s ** 3:.4g}", f"{o['max_vol'] / s ** 3:.4g}",
                             f"{o['mean_vol'] / s ** 3:.4g}", f"{o['std_vol'] / s ** 3:.4g}"])
            else:
                rows.append([o["object"], o.get("tets", "")] + [""] * 7)
        lay.addWidget(self._table(["Name", "Num Tets", "Min edge length", "Max edge length", "RMS edge length",
                                   "Min tet vol", "Max tet vol", "Mean tet vol", "Std Devn (vol)"], rows))
        return w

    def _eigen(self, eig) -> QWidget:
        return self._table(["Eigenmode", "Frequency (GHz)", "Q"],
                           [[f"Mode {i + 1}", f"{m['freq'] / 1e9:.6g}", f"{m['Q']:.4g}"] for i, m in enumerate(eig)])
