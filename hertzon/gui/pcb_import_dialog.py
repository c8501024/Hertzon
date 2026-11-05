"""File > Import > PCB (Gerber, Excellon): the board files become copper, vias, holes and substrate.

Left the files with what each one is (changeable), right the top view of the selected file with the
board outline, the holes and the area to import (Shift + drag draws it). Below the stackup, the options
and what will be loaded and what not. Nothing is made before Import.
"""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..model import stackup as S
from ..pcbimport import board as B
from .stackup_dialog import LayerView

COLS = ["File", "Content", "Found by", "Notes"]
KINDS_FIXED = [B.PROFILE, B.DRILL, B.MASK, B.LEGEND, B.PASTE, B.OTHER]
FILE_FILTER = "PCB files (*.gbr *.ger *.gtl *.gbl *.g? *.g?? *.gko *.gm1 *.gml *.drl *.xln *.exc *.txt *.zip *.gbrjob " \
              "*.cmp *.sol *.ly* *.dim *.nc);;All files (*)"


class PreviewView(LayerView):
    """Top view of one file with the outline, the holes and the import area (Shift + drag)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.holes: list[tuple[float, float, float, bool]] = []
        self.crop: tuple[float, float, float, float] | None = None
        self.on_crop = None
        self._crop_start = None

    def mousePressEvent(self, e):
        if e.modifiers() & Qt.ShiftModifier and e.button() == Qt.LeftButton:
            self._crop_start = self.to_model(e.position())
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._crop_start is not None:
            q = self.to_model(e.position())
            a = self._crop_start
            self.crop = (min(a[0], q[0]), min(a[1], q[1]), max(a[0], q[0]), max(a[1], q[1]))
            self.update()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._crop_start is not None:
            self._crop_start = None
            if self.crop and (self.crop[2] - self.crop[0]) * (self.crop[3] - self.crop[1]) > 0 and self.on_crop:
                self.on_crop(self.crop)
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for x, y, d, plated in self.holes:
            c = self.to_screen((x, y))
            r = max(1.5, d / 2 * self.scale)
            p.setPen(QPen(QColor("#ffffff" if plated else "#ff8a80"), 1))
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawEllipse(c, r, r)
        if self.crop:
            a, b = self.to_screen(self.crop[:2]), self.to_screen(self.crop[2:])
            p.setPen(QPen(QColor("#4fc3f7"), 2, Qt.DashLine))
            p.setBrush(QColor(79, 195, 247, 30))
            p.drawRect(QRectF(QPointF(a.x(), b.y()), QPointF(b.x(), a.y())))


class PcbImportDialog(QDialog):
    def __init__(self, parent, doc, paths=None):
        super().__init__(parent)
        self.doc = doc
        self.project, self.des = doc.project, doc.design
        self.files: list[B.SourceFile] = []
        self.job: dict | None = None
        self.job_name = ""
        self.plan: B.Plan | None = None
        self.stackup_edited = False
        self.new_materials = []
        self._filling = False
        self.setWindowTitle("Import PCB (Gerber, Excellon)")
        self.resize(1250, 820)
        lay = QVBoxLayout(self)

        bar = QHBoxLayout()
        for text, fn in (("Add files...", self._add_files), ("Add folder...", self._add_folder),
                         ("Remove", self._remove), ("Clear", self._clear)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addWidget(QLabel("Gerber (X2 or older), Excellon drill files, a .gbrjob job file, or a zip of them."))
        bar.addStretch(1)
        lay.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for c, w in enumerate((220, 150, 100)):
            self.table.setColumnWidth(c, w)
        self.table.currentCellChanged.connect(lambda r, *_: self._show(r))
        ll.addWidget(self.table, 1)
        self.job_label = QLabel()
        ll.addWidget(self.job_label)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.view = PreviewView()
        self.view.on_crop = self._crop_drawn
        rl.addWidget(self.view, 1)
        rl.addWidget(QLabel("Shift + drag: area to import. Click twice to measure, double-click to fit, wheel to zoom."))
        split.addWidget(right)
        split.setSizes([560, 690])
        lay.addWidget(split, 3)

        low = QHBoxLayout()
        g = QGroupBox("Stackup")
        gl = QVBoxLayout(g)
        self.st_label = QLabel()
        self.st_label.setWordWrap(True)
        gl.addWidget(self.st_label)
        b = QPushButton("Edit stackup...")
        b.clicked.connect(self._edit_stackup)
        gl.addWidget(b)
        gl.addStretch(1)
        low.addWidget(g, 1)
        g = QGroupBox("Options")
        f = QFormLayout(g)
        self.segments = QSpinBox()
        self.segments.setRange(8, 360)
        self.segments.setValue(B.G.DEFAULT_SEGMENTS)
        self.segments.setToolTip("Arcs and circles of the files are made of this many straight segments per circle")
        self.segments.editingFinished.connect(self._reload)
        f.addRow("Segments per circle", self.segments)
        self.origin = QComboBox()
        self.origin.addItems(["Lower left of the board", "Origin of the files"])
        self.origin.currentIndexChanged.connect(self._update)
        f.addRow("Board origin", self.origin)
        self.crop_on = QCheckBox("Only this area (file coordinates, mm):")
        self.crop_on.toggled.connect(self._update)
        f.addRow(self.crop_on)
        hb = QHBoxLayout()
        self.crop_eds = [QLineEdit() for _ in range(4)]
        for lab, e in zip(("x0", "y0", "x1", "y1"), self.crop_eds):
            e.setMaximumWidth(80)
            e.editingFinished.connect(self._crop_typed)
            hb.addWidget(QLabel(lab))
            hb.addWidget(e)
        f.addRow(hb)
        low.addWidget(g, 1)
        g = QGroupBox("What will be made")
        vl = QVBoxLayout(g)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        vl.addWidget(self.report)
        low.addWidget(g, 2)
        lay.addLayout(low, 2)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = self.buttons.addButton("Import", QDialogButtonBox.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.paths: list[str] = []
        if paths:
            self.add_paths(paths)
        else:
            self._update()

    # ---- files --------------------------------------------------------------------------------
    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add PCB files", "", FILE_FILTER)
        if paths:
            self.add_paths(paths)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add a folder of PCB files")
        if path:
            self.add_paths([path])

    def _remove(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.files):
            del self.files[r]
            self._replan()

    def _clear(self) -> None:
        self.files, self.job, self.job_name, self.paths = [], None, "", []
        self._replan()

    def add_paths(self, paths) -> None:
        self.paths += [str(p) for p in paths]
        self._reload()

    def _reload(self) -> None:
        try:
            files, job, jn = B.load(self.paths, self.segments.value())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Import PCB", f"The files cannot be read:\n{e}")
            return
        self.files, self.job, self.job_name = files, job, jn
        self._replan()

    def _replan(self, keep_stackup: bool = False) -> None:
        old = self.plan.stackup if (self.plan and (keep_stackup or self.stackup_edited)) else None
        self.plan = B.plan(self.files, self.job, self.job_name, self.des.stackup, self.segments.value())
        if old is not None and len(old.copper()) == len(self.plan.stackup.copper()):
            self.plan.stackup = old
        ext = self.plan.extent()
        if ext and not any(e.text() for e in self.crop_eds):
            for e, v in zip(self.crop_eds, ext):
                e.setText(f"{v * 1e3:.6g}")
        self._fill()
        self._update()

    def _content_items(self) -> list[str]:
        n = len(self.plan.stackup.copper()) if self.plan else 2
        return [f"Copper L{k}" for k in range(1, max(n, 1) + 1)] + KINDS_FIXED

    def _fill(self) -> None:
        self._filling = True
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.files))
        items = self._content_items()
        for r, f in enumerate(self.files):
            it = QTableWidgetItem(f.name)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, it)
            cb = QComboBox()
            cb.addItems(items)
            for i, t in enumerate(items):
                if t in B.NOT_SIMULATED:
                    cb.setItemText(i, f"{t} (not loaded)")
            cur = f.label if f.kind == B.COPPER and f.layer else f.kind
            if cur in items:
                cb.setCurrentIndex(items.index(cur))
            elif f.kind == B.COPPER:
                cb.setCurrentIndex(items.index(B.OTHER))
            cb.currentIndexChanged.connect(lambda i, r=r: self._content_changed(r, i))
            self.table.setCellWidget(r, 1, cb)
            it = QTableWidgetItem(f.how)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 2, it)
            it = QTableWidgetItem(self._note(f))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            msgs = (f.image.messages if f.image else []) + (f.drill.messages if f.drill else [])
            if f.error or any(m.level != "info" for m in msgs):
                it.setForeground(QColor("#d68910"))
                it.setToolTip("\n".join([f.error] + [m.text for m in msgs]).strip())
            self.table.setItem(r, 3, it)
            if f.kind in B.NOT_SIMULATED:
                for c in (0, 2, 3):
                    self.table.item(r, c).setForeground(QColor("#909090"))
        self.job_label.setText(f"Job file: {self.job_name}" if self.job else "No job file (.gbrjob): the stackup is "
                               "assumed unless the design has one with the same number of copper layers.")
        self._filling = False
        if self.files:
            self.table.selectRow(next((i for i, f in enumerate(self.files) if f.kind == B.COPPER), 0))

    @staticmethod
    def _note(f: B.SourceFile) -> str:
        if f.error:
            return f.error
        if f.image is not None and f.kind != B.DRILL:
            return f"{len(f.image.polygons())} shapes"
        if f.drill is not None:
            n = len(f.drill.holes)
            pl = sum(1 for h in f.drill.holes if h.plated)
            return f"{n} holes ({pl} plated)" + (f", L{f.drill.span[0]}-L{f.drill.span[1]}" if f.drill.span else "")
        return ""

    def _content_changed(self, r: int, i: int) -> None:
        if self._filling:
            return
        t = self._content_items()[i]
        f = self.files[r]
        if t.startswith("Copper L"):
            f.kind, f.layer = B.COPPER, int(t[8:])
            for g in self.files:          # one file per copper layer: the one that had it is not loaded now
                if g is not f and g.kind == B.COPPER and g.layer == f.layer:
                    g.kind, g.layer, g.how = B.OTHER, None, f"replaced by {f.name}"
        else:
            f.kind, f.layer = t, None
            if t == B.DRILL and f.drill is None and f.image is not None:
                f.drill = B._drill_from_gerber(f.image)
        f.how = "set by you"
        self._replan(keep_stackup=True)
        self.table.selectRow(r)

    # ---- view ---------------------------------------------------------------------------------
    def _show(self, r: int) -> None:
        if not (0 <= r < len(self.files)) or self.plan is None:
            self.view.set_shapes([], [], "", "#ffffff")
            return
        f = self.files[r]
        polys = []
        if f.image is not None and f.kind != B.DRILL:
            polys = [(np.asarray(p.exterior.coords), [np.asarray(h.coords) for h in p.interiors])
                     for p in f.image.polygons()]
        prof = self.plan.profile()
        outline = []
        if prof is not None:
            outline = [np.asarray(p.exterior.coords) for p in B.G.outline_polygons(prof.image, self.plan.segments)]
        self.view.holes = [(h.x, h.y, h.d, h.plated) for d in self.plan.drills() for h in d.drill.holes]
        col = "#c8892b" if f.kind == B.COPPER else ("#9a9a9a" if f.kind in B.NOT_SIMULATED else "#8fb070")
        self.view.set_shapes(polys, outline, f"{f.name} ({f.label})", col)

    def _crop_drawn(self, c) -> None:
        for e, v in zip(self.crop_eds, c):
            e.setText(f"{v * 1e3:.6g}")
        self.crop_on.setChecked(True)
        self._update()

    def _crop_typed(self) -> None:
        self._update()

    def crop(self) -> tuple[float, float, float, float] | None:
        if not self.crop_on.isChecked():
            return None
        try:
            v = [float(e.text().replace(",", ".")) * 1e-3 for e in self.crop_eds]
        except ValueError:
            return None
        x0, y0, x1, y1 = v
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)) if x0 != x1 and y0 != y1 else None

    # ---- stackup ------------------------------------------------------------------------------
    def _edit_stackup(self) -> None:
        from .stackup_dialog import StackupDialog
        if self.plan is None:
            return
        dlg = StackupDialog(self, self.doc)
        dlg.st = copy.deepcopy(self.plan.stackup)
        dlg.nominal.setText(dlg.st.nominal_thickness)
        dlg.sel = 0
        dlg._refresh()
        if dlg.exec() == QDialog.Accepted:
            st, mats = dlg.result()
            self.plan.stackup = st
            self.new_materials += mats
            self.stackup_edited = True
            n_old = self.table.rowCount()
            self._fill() if n_old else None
            self._update()

    # ---- what will be made --------------------------------------------------------------------
    def _update(self, *_) -> None:
        if self.plan is None:
            self.st_label.setText("No files yet.")
            self.report.setPlainText("Add the Gerber and drill files of a board.")
            self.ok.setEnabled(False)
            return
        p = self.plan
        p.crop = self.crop()
        self.view.crop = p.crop
        self.view.update()
        ext = p.extent()
        if self.origin.currentIndex() == 1 or not ext:
            p.origin = (0.0, 0.0)
        else:
            shape = p.board_shape()
            b = B.shapely.union_all(shape).bounds if shape else ext
            p.origin = (b[0], b[1])
        st = p.stackup
        cu = st.copper()
        names = ", ".join(st.layers[i].name for i in cu)
        self.st_label.setText(f"{len(cu)} copper layers ({names}), {st.source}"
                              + (", edited" if self.stackup_edited else "") + ".\n" + "\n".join(p.notes[:4]))
        self.report.setPlainText(preview_text(p, self.des))
        self.ok.setEnabled(bool(p.copper_files()) or p.profile() is not None)

    def result(self) -> tuple[B.Plan, list]:
        return self.plan, list(self.new_materials)


def preview_text(p: B.Plan, des) -> str:
    """What the import will make and leave out, before it is made."""
    st = p.stackup
    cu = st.copper()
    out = []
    shape = p.board_shape()
    if shape:
        x0, y0, x1, y1 = B.shapely.union_all(shape).bounds
        out.append(f"Board {(x1 - x0) * 1e3:.4g} x {(y1 - y0) * 1e3:.4g} mm"
                   + (" (outline)" if p.profile() else " (extent of the copper, no outline file)")
                   + (", cropped" if p.crop else "") + f", origin at file point ({p.origin[0] * 1e3:.6g}, "
                   f"{p.origin[1] * 1e3:.6g}) mm.")
    out.append("Substrate: one slab per dielectric layer of the stackup, in the board shape.")
    cf = p.copper_files()
    for k in range(1, len(cu) + 1):
        L = st.layers[cu[k - 1]]
        f = cf.get(k)
        model = "solid copper" if L.representation == S.SOLID else "sheet, finite conductivity"
        out.append(f"{L.name} (L{k}): " + (f"{f.name}, {len(f.image.polygons())} shapes, {model}" if f else "no file, empty"))
    for f in p.files:
        if f.kind == B.COPPER and (not f.layer or f.layer > len(cu) or cf.get(f.layer) is not f):
            why = (f"L{f.layer}, but the stackup has {len(cu)} copper layers" if f.layer and f.layer > len(cu)
                   else "no copper layer" if not f.layer else f"a second file for L{f.layer}")
            out.append(f"NOT LOADED {f.name}: {why}")
    allh = [h for f in p.drills() for h in f.drill.holes]
    nh = [h.plated for h in allh if p.inside(h)]
    if allh:
        out.append(f"Holes: {sum(nh)} plated (copper cylinders), {len(nh) - sum(nh)} non-plated (cut out)"
                   + (f"; {len(allh) - len(nh)} not fully inside the area are left out" if p.crop else "") + ".")
    out.append(f"Arcs: {p.segments} segments per circle.")
    skipped = [f"{f.name} ({f.kind.lower()})" for f in p.files if f.kind in B.NOT_SIMULATED]
    skipped += [f"{n.get('name', S.MASK)} of the job file stackup" for n in st.not_simulated]
    if skipped:
        out += ["", "Not loaded, not simulated:"] + [f"  {x}" for x in skipped]
    if any(o.group.startswith(B.GROUP_PREFIX) for o in des.objects if o.group):
        out += ["", "Replaces the PCB import made before in this design."]
    return "\n".join(out)
