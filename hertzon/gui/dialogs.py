"""Dialogs: boundaries/excitations/mesh operations, setups, sweeps, variables, materials,
solution type, validation check, far field setup, object properties and Subtract."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QDialogButtonBox, QLabel,
                               QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QComboBox, QCheckBox,
                               QTabWidget, QWidget, QMessageBox, QTreeWidget, QTreeWidgetItem, QGroupBox,
                               QRadioButton, QAbstractItemView, QDoubleSpinBox, QListWidget)

from ..model import schema
from ..model.design import Assignment, Setup, Sweep, InfiniteSphere, GeoObject
from ..model.materials import EMERGE_DISPERSION, EMERGE_LIBRARY, Material, normalize_color
from ..model.validate import validate, CATEGORIES, worst
from .doc import object_attributes
from .props import PropertyGrid, TabbedPropertyGrid


class NamedPropsDialog(QDialog):
    """Name field + tabbed property grid."""

    def __init__(self, title: str, name: str, props: list[schema.Prop], values: dict, ev=None, materials=None,
                 parent=None, info: str = "", extra_widget: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.values = dict(values)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        form.addRow("Name:", self.name_edit)
        lay.addLayout(form)
        if info:
            lab = QLabel(info)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        self.grid = TabbedPropertyGrid(self)
        self.grid.set(props, self.values, ev, materials=materials or [])
        lay.addWidget(self.grid)
        if extra_widget is not None:
            lay.addWidget(extra_widget)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.RestoreDefaults).setText("Use Defaults")
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(lambda: self._defaults(props, ev, materials))
        lay.addWidget(bb)
        self.resize(560, 460)

    def _defaults(self, props, ev, materials) -> None:
        self.values.clear()
        self.values.update(schema.defaults(props))
        self.grid.set(props, self.values, ev, materials=materials or [])

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()


def edit_assignment(parent, doc, family: str, a: Assignment) -> bool:
    props = {"boundary": schema.BOUNDARIES, "excitation": schema.EXCITATIONS, "mesh": schema.MESH_OPS}[family][a.kind]
    sel = ", ".join([f"{f.obj}{'' if f.face < 0 else f' face {f.face}'}" for f in a.faces] + a.objects)
    info = f"<b>{a.kind}</b> on: {sel or '(nothing)'}"
    if family == "boundary" and a.kind in schema.UNSUPPORTED_BOUNDARIES:
        info += f"<br><span style='color:#b35c00'>{schema.UNSUPPORTED_BOUNDARIES[a.kind]}</span>"
    dlg = NamedPropsDialog(f"{a.kind} {'Boundary' if family == 'boundary' else ''}".strip(), a.name, props, a.props,
                           doc.evaluator(), [m.name for m in doc.project.all_materials()], parent, info)
    view = getattr(parent, "view", None)
    if view is not None and family == "excitation":
        import copy

        def preview(*_):
            # updates the k/E vectors and port lines while the dialog is open
            tmp = copy.copy(a)
            tmp.props = dict(dlg.values)
            try:
                view.show_assignments([tmp])
            except Exception:  # noqa: BLE001
                pass
        dlg.grid.changed.connect(preview)
    try:
        ok = dlg.exec() == QDialog.Accepted
    finally:
        if view is not None and family == "excitation" and not ok:
            try:
                view.show_assignments([a])          # cancelled: back to the stored excitation
            except Exception:  # noqa: BLE001
                pass
    if not ok:
        return False
    new_name = dlg.name or a.name
    if new_name != a.name and new_name in doc.design.all_names():
        QMessageBox.warning(parent, "Name in use", f"'{new_name}' is already used.")
        return False
    a.name = new_name
    a.props = dlg.values
    if view is not None and family == "excitation":
        try:
            view.show_assignments([a])
        except Exception:  # noqa: BLE001
            pass
    return True


def edit_setup(parent, doc, s: Setup) -> bool:
    props = schema.EIGEN_SETUP if s.eigen else schema.SETUP
    des = doc.design
    pw = not des.ports() and any(e.kind == "Incident Plane Wave" and e.enabled for e in des.excitations)
    drop = ({"MaximumDeltaS", "UseMatrixConvergence", "MaxDeltaMag", "MaxDeltaPhase"} if pw
            else {"MaximumDeltaEnergy"})
    props = [p for p in props if p.name not in drop]
    dlg = NamedPropsDialog("Driven Solution Setup" if not s.eigen else "Eigenmode Solution Setup", s.name, props,
                           s.props, doc.evaluator(), parent=parent,
                           info="Adaptive mesh refinement: the mesh is refined at the solution frequency until "
                                + ("the change of the scattered field energy between two passes (Delta Energy) "
                                   "is below the limit." if pw else
                                   "the maximum change of the S-parameters between two passes (Delta S) is below "
                                   "the limit."))
    def order_changed(name, value):
        #: a lambda target at the default of the previous order follows the new order
        if name != "BasisOrder":
            return
        old = order_changed.order
        order_changed.order = value
        cur = dlg.values.get("LambdaTarget", "")
        new = schema.LAMBDA_TARGETS.get(value)
        try:
            at_default = old in schema.LAMBDA_TARGETS and abs(float(cur) - float(schema.LAMBDA_TARGETS[old])) < 1e-6
        except ValueError:
            at_default = False
        if new and at_default and cur != new:
            dlg.values["LambdaTarget"] = new
            from PySide6.QtCore import QTimer
            import shiboken6

            def refresh():
                for g in dlg.grid.grids.values():
                    if shiboken6.isValid(g):
                        g.rebuild()
            QTimer.singleShot(0, refresh)
    order_changed.order = s.props.get("BasisOrder", "First Order")
    if not s.eigen:
        dlg.grid.changed.connect(order_changed)
    if dlg.exec() != QDialog.Accepted:
        return False
    s.name = dlg.name or s.name
    s.props = {**s.props, **dlg.values}
    return True


def edit_sweep(parent, doc, sw: Sweep) -> bool:
    preview = QLabel()
    preview.setWordWrap(True)

    def upd(*_):
        try:
            from ..solver.bridge import sweep_frequencies
            f = sweep_frequencies(dlg.values, doc.evaluator())
            preview.setText(f"{len(f)} frequency points from {f.min() / 1e9:.6g} GHz to {f.max() / 1e9:.6g} GHz")
        except Exception as e:  # noqa: BLE001
            preview.setText(f"<span style='color:#c0392b'>{e}</span>")
    dlg = NamedPropsDialog("Edit Frequency Sweep", sw.name, schema.SWEEP, sw.props, doc.evaluator(), parent=parent,
                           extra_widget=preview,
                           info="Discrete solves every point. Interpolating solves a few points and fits a "
                                "rational model until the error tolerance is met.")
    dlg.grid.changed.connect(upd)
    upd()
    if dlg.exec() != QDialog.Accepted:
        return False
    sw.name = dlg.name or sw.name
    sw.props = dlg.values
    return True


def edit_sphere(parent, doc, sp: InfiniteSphere) -> bool:
    dlg = NamedPropsDialog("Far Field Radiation Sphere Setup", sp.name, schema.INFINITE_SPHERE, sp.props,
                           doc.evaluator(), parent=parent,
                           info="Infinite sphere for far field calculation (theta measured from +Z).")
    if dlg.exec() != QDialog.Accepted:
        return False
    sp.name = dlg.name or sp.name
    sp.props = dlg.values
    return True


class SubtractDialog(QDialog):
    """Modeler > Boolean > Subtract: Blank Parts and Tool Parts. The arrow buttons move the selected
    objects from one list to the other; several objects are selected by dragging, Ctrl or Shift."""

    def __init__(self, blanks: list[str], tools: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Subtract")
        lay = QHBoxLayout(self)
        self.blank_list, self.tool_list = QListWidget(), QListWidget()
        for w, names in ((self.blank_list, blanks), (self.tool_list, tools)):
            w.setSelectionMode(QAbstractItemView.ExtendedSelection)
            w.addItems(names)
            w.itemSelectionChanged.connect(self._update)
        self.to_tool, self.to_blank = QPushButton("\u2192"), QPushButton("\u2190")
        self.to_tool.setToolTip("Move the selected blank parts to the tool parts")
        self.to_blank.setToolTip("Move the selected tool parts to the blank parts")
        self.to_tool.clicked.connect(lambda: self._move(self.blank_list, self.tool_list))
        self.to_blank.clicked.connect(lambda: self._move(self.tool_list, self.blank_list))
        arrows = QVBoxLayout()
        arrows.addStretch(1)
        for b in (self.to_tool, self.to_blank):
            b.setFixedWidth(36)
            arrows.addWidget(b)
        arrows.addStretch(1)
        for title, w in (("Blank Parts", self.blank_list), ("Tool Parts", self.tool_list)):
            box = QVBoxLayout()
            box.addWidget(QLabel(title))
            box.addWidget(w)
            lay.addLayout(box)
            if w is self.blank_list:
                lay.addLayout(arrows)
        side = QVBoxLayout()
        swap = QPushButton("Swap")
        swap.setToolTip("Exchange all blank parts with all tool parts")
        swap.clicked.connect(self.swap)
        self.clone = QCheckBox("Clone tool objects before operation")
        side.addWidget(swap)
        side.addWidget(self.clone)
        side.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        side.addWidget(self.buttons)
        lay.addLayout(side)
        self._update()

    @staticmethod
    def _names(w: QListWidget) -> list[str]:
        return [w.item(i).text() for i in range(w.count())]

    def blanks(self) -> list[str]:
        return self._names(self.blank_list)

    def tools(self) -> list[str]:
        return self._names(self.tool_list)

    def _move(self, src: QListWidget, dst: QListWidget) -> None:
        rows = sorted((src.row(it) for it in src.selectedItems()), reverse=True)
        moved = [src.takeItem(r).text() for r in rows][::-1]
        dst.clearSelection()
        for name in moved:
            dst.addItem(name)
            dst.item(dst.count() - 1).setSelected(True)
        self._update()

    def swap(self) -> None:
        b, t = self.blanks(), self.tools()
        self.blank_list.clear()
        self.tool_list.clear()
        self.blank_list.addItems(t)
        self.tool_list.addItems(b)
        self._update()

    def _update(self) -> None:
        self.to_tool.setEnabled(bool(self.blank_list.selectedItems()))
        self.to_blank.setEnabled(bool(self.tool_list.selectedItems()))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.blanks()) and bool(self.tools()))


class ObjectPropertiesDialog(QDialog):
    """Command and Attribute tabs for a newly drawn primitive."""

    def __init__(self, doc, obj: GeoObject, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Properties: {doc.project.name} - {doc.design.name} - Modeler")
        self.obj = obj
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        self.cmd_values = dict(obj.history[0].props)
        self.cmd = PropertyGrid()
        self.cmd.set(schema.GEOMETRY[obj.history[0].kind], self.cmd_values, doc.evaluator())
        tabs.addTab(self.cmd, "Command")
        self.attr_values = {"Name": obj.name, "Material": obj.material, "SolveInside": str(obj.solve_inside),
                            "Model": str(obj.model), "Color": obj.color, "Transparency": str(obj.transparency),
                            "DisplayWireframe": str(obj.wireframe), "Orientation": obj.orientation}
        self.attr = PropertyGrid(show_evaluated=False)
        self.attr.set(object_attributes(doc, obj.name), self.attr_values, None,
                      materials=[m.name for m in doc.project.all_materials()])
        tabs.addTab(self.attr, "Attribute")
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(520, 420)


class VariablesDialog(QDialog):
    """Design properties: local variables (and project variables prefixed with $)."""

    def __init__(self, doc, parent=None, project_level: bool = False):
        super().__init__(parent)
        self.doc = doc
        self.project_level = project_level
        self.vars = dict(doc.project.variables if project_level else doc.design.variables)
        self.setWindowTitle("Project Variables" if project_level else f"Design Properties: {doc.design.name}")
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Value", "Evaluated Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemChanged.connect(self._changed)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add...")
        add.clicked.connect(self._add)
        rem = QPushButton("Remove")
        rem.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        # Apply updates the model with the current values and keeps the dialog open
        bb.button(QDialogButtonBox.Apply).clicked.connect(lambda: self.on_apply and self.on_apply(dict(self.vars)))
        lay.addWidget(bb)
        self.on_apply = None
        self._loading = False
        self._fill()
        self.resize(560, 380)

    def _ev(self):
        from ..model.expr import Evaluator
        v = dict(self.doc.project.variables)
        if not self.project_level:
            v.update(self.vars)
        else:
            v = dict(self.vars)
        return Evaluator(v, self.doc.design.units)

    def _fill(self) -> None:
        self._loading = True
        self.table.setRowCount(len(self.vars))
        ev = self._ev()
        for r, (k, v) in enumerate(self.vars.items()):
            a = QTableWidgetItem(k)
            a.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, 0, a)
            self.table.setItem(r, 1, QTableWidgetItem(v))
            try:
                val, has_unit = ev._eval(k)
                txt = f"{val * 1e3:.6g}mm" if has_unit and abs(val) < 1 else f"{val:.6g}"
                c = QTableWidgetItem(txt)
            except Exception as e:  # noqa: BLE001
                c = QTableWidgetItem(str(e))
                c.setForeground(QBrush(QColor("#c0392b")))
            c.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 2, c)
        self._loading = False

    def _changed(self, item) -> None:
        if self._loading or item.column() != 1:
            return
        k = self.table.item(item.row(), 0).text()
        self.vars[k] = item.text()
        self._fill()

    def _add(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Property")
        f = QFormLayout(dlg)
        n = QLineEdit("$var1" if self.project_level else "var1")
        v = QLineEdit("1mm")
        f.addRow("Name", n)
        f.addRow("Value", v)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() == QDialog.Accepted and n.text().strip():
            name = n.text().strip().lstrip("$") if not self.project_level else n.text().strip().lstrip("$")
            if not name.isidentifier():
                QMessageBox.warning(self, "Invalid name", "Variable names must be identifiers.")
                return
            self.vars[name] = v.text()
            self._fill()

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            k = self.table.item(r, 0).text()
            self.vars.pop(k, None)
            self._fill()


class MaterialEditDialog(QDialog):
    """View / Edit Material: simple, anisotropic and frequency dependent definitions."""

    def __init__(self, mat: Material, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"View / Edit Material: {mat.name}")
        self.mat = copy.deepcopy(mat)
        f = QFormLayout(self)
        self.name = QLineEdit(mat.name)
        self.er = QLineEdit(mat.permittivity)
        self.ur = QLineEdit(mat.permeability)
        self.sig = QLineEdit(mat.conductivity)
        self.tand = QLineEdit(mat.loss_tangent)
        self.mtand = QLineEdit(mat.magnetic_loss_tangent)
        self.pec = QCheckBox("Perfect conductor")
        self.pec.setChecked(mat.pec)
        self.color = QLineEdit(mat.color)
        for lab, w in (("Material name", self.name), ("Relative permittivity", self.er),
                       ("Relative permeability", self.ur), ("Bulk conductivity (S/m)", self.sig),
                       ("Dielectric loss tangent", self.tand), ("Magnetic loss tangent", self.mtand),
                       ("", self.pec), ("Color", self.color)):
            f.addRow(lab, w)
        # anisotropy (Relative Permittivity type "Anisotropic", T(1,1) T(2,2) T(3,3))
        self.aniso = QCheckBox("Anisotropic (diagonal tensor)")
        self.aniso.setChecked(mat.anisotropic)
        f.addRow("", self.aniso)
        self.et = QLineEdit(mat.permittivity_tensor or f"{mat.permittivity},{mat.permittivity},{mat.permittivity}")
        self.ut = QLineEdit(mat.permeability_tensor or f"{mat.permeability},{mat.permeability},{mat.permeability}")
        self.tt = QLineEdit(mat.loss_tangent_tensor or f"{mat.loss_tangent},{mat.loss_tangent},{mat.loss_tangent}")
        self._aniso_rows = []
        for lab, w in (("Permittivity xx,yy,zz", self.et), ("Permeability xx,yy,zz", self.ut),
                       ("Loss tangent xx,yy,zz", self.tt)):
            f.addRow(lab, w)
            self._aniso_rows.append(w)
        # frequency dependence
        self.disp = QComboBox()
        self.disp.addItems(["None", "Djordjevic-Sarkar", "Debye"])
        if mat.dispersion == EMERGE_DISPERSION and mat.emsutil_key:
            self.disp.addItem(EMERGE_DISPERSION)      # the frequency dependent data of EMerge's library
        self.disp.setCurrentText(mat.dispersion or "None")
        f.addRow("Frequency dependence", self.disp)
        self.fm = QLineEdit(mat.measured_frequency)
        self.es = QLineEdit(mat.eps_static)
        self.einf = QLineEdit(mat.eps_infinity)
        self.frel = QLineEdit(mat.relaxation_frequency)
        self._disp_rows = {"Djordjevic-Sarkar": [self.fm], "Debye": [self.es, self.einf, self.frel]}
        for lab, w in (("Measurement frequency (er, tan d given there)", self.fm), ("Static permittivity", self.es),
                       ("Optical permittivity (eps inf)", self.einf), ("Relaxation frequency", self.frel)):
            f.addRow(lab, w)
        self.aniso.toggled.connect(self._update)
        self.disp.currentTextChanged.connect(self._update)
        if mat.library in ("System", EMERGE_LIBRARY):
            f.addRow(QLabel("Library materials are read only; the edit is stored as a project material."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._form = f
        self._update()

    def _set_row_visible(self, w, on: bool) -> None:
        w.setVisible(on)
        lab = self._form.labelForField(w)
        if lab is not None:
            lab.setVisible(on)

    def _update(self, *_) -> None:
        for w in self._aniso_rows:
            self._set_row_visible(w, self.aniso.isChecked())
        for k, ws in self._disp_rows.items():
            for w in ws:
                self._set_row_visible(w, self.disp.currentText() == k)
        self.disp.setEnabled(not self.aniso.isChecked())

    def result_material(self) -> Material:
        return Material(name=self.name.text().strip(), permittivity=self.er.text(), permeability=self.ur.text(),
                        conductivity=self.sig.text(), loss_tangent=self.tand.text(),
                        magnetic_loss_tangent=self.mtand.text() or "0", pec=self.pec.isChecked(),
                        color=normalize_color(self.color.text(), self.mat.color), library="Project", description=self.mat.description,
                        dispersion=self.disp.currentText(), measured_frequency=self.fm.text(),
                        eps_static=self.es.text(), eps_infinity=self.einf.text(),
                        relaxation_frequency=self.frel.text(), anisotropic=self.aniso.isChecked(),
                        permittivity_tensor=self.et.text() if self.aniso.isChecked() else "",
                        permeability_tensor=self.ut.text() if self.aniso.isChecked() else "",
                        loss_tangent_tensor=self.tt.text() if self.aniso.isChecked() else "",
                        emsutil_key=self.mat.emsutil_key)


class MaterialsDialog(QDialog):
    """Select / define materials."""

    def __init__(self, doc, parent=None, current: str = ""):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Select Definition")
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Search by Name:"))
        self.search = QLineEdit()
        self.search.textChanged.connect(self._fill)
        top.addWidget(self.search)
        top.addWidget(QLabel("Libraries:"))
        self.libs = QComboBox()
        self.libs.addItems(["All", "Project", "SysLibrary", "EMerge Library"])
        self.libs.currentTextChanged.connect(self._fill)
        top.addWidget(self.libs)
        lay.addLayout(top)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Location", "Rel. Permittivity", "Rel. Permeability",
                                              "Bulk Conductivity", "Dielectric Loss Tangent"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.accept)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        for text, fn in (("Add Material...", self._add), ("Clone Material", self._clone), ("View/Edit Material...", self._edit),
                         ("Remove Material", self._remove), ("Import Library...", self._import),
                         ("Export Library...", self._export)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.current = current
        self._fill()
        self.resize(820, 520)

    def _fill(self) -> None:
        q = self.search.text().lower()
        lib = self.libs.currentText()
        want = {"Project": ("Project",), "SysLibrary": ("System",), "EMerge Library": (EMERGE_LIBRARY,)}.get(lib)
        mats = [m for m in self.doc.project.all_materials() if q in m.name.lower() and (want is None or m.library in want)]
        self.table.setRowCount(len(mats))
        for r, m in enumerate(mats):
            er = m.permittivity_tensor if m.anisotropic and m.permittivity_tensor else m.permittivity
            if m.dispersion and m.dispersion != "None":
                er = f"{er} ({m.dispersion})"
            loc = {"Project": "Project", EMERGE_LIBRARY: "EMerge Library"}.get(m.library, "SysLibrary")
            for c, v in enumerate([m.name, loc, er,
                                   m.permeability, "PEC" if m.pec else m.conductivity, m.loss_tangent]):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
            if m.name == self.current:
                self.table.selectRow(r)

    def selected(self) -> str | None:
        r = self.table.currentRow()
        return self.table.item(r, 0).text() if r >= 0 else None

    def _store(self, mat: Material) -> None:
        mats = self.doc.project.materials
        for i, m in enumerate(mats):
            if m.name == mat.name:
                mats[i] = mat
                break
        else:
            mats.append(mat)
        self.doc.dirty = True
        self._fill()

    def _add(self) -> None:
        dlg = MaterialEditDialog(Material(name="Material1"), self)
        if dlg.exec() == QDialog.Accepted:
            self._store(dlg.result_material())

    def _clone(self) -> None:
        n = self.selected()
        if n:
            m = copy.deepcopy(self.doc.project.material(n))
            m.name = n + "_clone"
            m.library = "Project"
            dlg = MaterialEditDialog(m, self)
            if dlg.exec() == QDialog.Accepted:
                self._store(dlg.result_material())

    def _edit(self) -> None:
        n = self.selected()
        if n:
            dlg = MaterialEditDialog(self.doc.project.material(n), self)
            if dlg.exec() == QDialog.Accepted:
                self._store(dlg.result_material())

    def _import(self) -> None:
        """Import materials from a Hertzon library (.json) or an third-party material library (.amat)."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from ..model.materials import import_library
        path, _ = QFileDialog.getOpenFileName(self, "Import Material Library", "",
                                              "Material libraries (*.json *.amat);;All files (*)")
        if not path:
            return
        try:
            mats = import_library(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Import Library", f"Cannot read {path}: {e}")
            return
        for m in mats:
            self._store(m)
        QMessageBox.information(self, "Import Library", f"{len(mats)} material(s) imported as project materials")

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from ..model.materials import export_library
        path, _ = QFileDialog.getSaveFileName(self, "Export Material Library", "materials.json", "JSON (*.json)")
        if path:
            export_library(path, self.doc.project.materials)

    def _remove(self) -> None:
        n = self.selected()
        mats = self.doc.project.materials
        if n and any(m.name == n for m in mats):
            self.doc.project.materials = [m for m in mats if m.name != n]
            self._fill()


class SolutionTypeDialog(QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Solution Type")
        self.values = dict(doc.design.settings)
        lay = QVBoxLayout(self)
        grp = QGroupBox("Driven")
        g = QVBoxLayout(grp)
        self.radios = {}
        for t in ("Modal", "Terminal"):
            rb = QRadioButton(t)
            self.radios[t] = rb
            g.addWidget(rb)
        lay.addWidget(grp)
        rb = QRadioButton("Eigenmode")
        self.radios["Eigenmode"] = rb
        lay.addWidget(rb)
        self.radios.get(self.values.get("SolutionType", "Modal"), self.radios["Modal"]).setChecked(True)
        box = QGroupBox("Auto Open")
        bl = QVBoxLayout(box)
        self.grid = PropertyGrid(self)
        self.grid.set([p for p in schema.DESIGN_SETTINGS if p.name != "SolutionType"], self.values, doc.evaluator())
        bl.addWidget(QLabel("Auto Open surrounds the model with an air region and an absorbing outer boundary.\n"
                            "Auto uses a first order radiation boundary lambda/3 away ('Create Open Region');\n"
                            "choose PML explicitly if needed. 'Auto Open - Filter' uses a fixed air margin with a\n"
                            "radiation boundary, as in filterbench."))
        bl.addWidget(self.grid)
        lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(560, 460)

    def result(self) -> dict:
        for t, rb in self.radios.items():
            if rb.isChecked():
                self.values["SolutionType"] = t
        return self.values


class ValidationDialog(QDialog):
    """Validation check; clicking a problem selects the object or assignment it is about."""
    from PySide6.QtCore import Signal as _Signal
    messageActivated = _Signal(str)
    ICON = {"ok": "✔", "warning": "⚠", "error": "✖"}
    COLOR = {"ok": "#1e8449", "warning": "#b9770e", "error": "#c0392b"}

    def __init__(self, doc, parent=None, overlaps=None, resolve=None):
        """overlaps: classified overlapping solids (model.validate.Overlap) from the exact geometry check;
        resolve(list of Overlap) subtracts the winner of each from the loser."""
        super().__init__(parent)
        self.setWindowTitle(f"Validation Check: {doc.project.name} - {doc.design.name}")
        checks = validate(doc.project, doc.design, doc.tess, doc.errors, overlaps)
        fixable = {ov.message: ov for ov in overlaps or [] if not ov.contained}
        self.ok = not any(c.level == "error" for c in checks)
        lay = QHBoxLayout(self)
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        for cat in CATEGORIES:
            lv = worst(checks, cat)
            top = QTreeWidgetItem([f"{self.ICON[lv]}  {cat}"])
            top.setForeground(0, QBrush(QColor(self.COLOR[lv])))
            for c in checks:
                if c.category == cat:
                    it = QTreeWidgetItem([f"{self.ICON[c.level]}  {c.message}"])
                    it.setData(0, Qt.UserRole, c.message)
                    it.setForeground(0, QBrush(QColor(self.COLOR[c.level])))
                    top.addChild(it)
            tree.addTopLevelItem(top)
            top.setExpanded(lv != "ok")
        tree.itemClicked.connect(lambda it, _c: self.messageActivated.emit(it.data(0, Qt.UserRole) or ""))
        lay.addWidget(tree, 1)
        side = QVBoxLayout()
        side.addWidget(QLabel("Validation check completed." if self.ok else
                              "Validation check found errors.\nPlease fix them before analyzing."))
        side.addStretch()
        if fixable and resolve is not None:
            # resolve overlaps the way the solver treats them: subtract the winning object from the other one
            one = QPushButton("Resolve Overlap")
            one.setToolTip("Subtract the object whose material applies in the selected overlap from the other "
                           "object (Subtract with Clone tool objects), so the model shows what is solved")
            one.setEnabled(False)
            tree.currentItemChanged.connect(lambda it, _p: one.setEnabled(
                it is not None and (it.data(0, Qt.UserRole) or "") in fixable))

            def run(ovs):
                self.accept()
                resolve(ovs)
            one.clicked.connect(lambda: run([fixable[tree.currentItem().data(0, Qt.UserRole)]]))
            every = QPushButton(f"Resolve All Overlaps ({len(fixable)})")
            every.clicked.connect(lambda: run(list(fixable.values())))
            side.addWidget(one)
            side.addWidget(every)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        side.addWidget(close)
        lay.addLayout(side)
        self.resize(760, 420)


class GridDialog(QDialog):
    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Grid Settings")
        self.view = view
        f = QFormLayout(self)
        self.plane = QComboBox()
        self.plane.addItems(["XY", "YZ", "XZ"])
        self.plane.setCurrentText(view.grid_plane)
        self.auto = QCheckBox("Automatic spacing")
        self.auto.setChecked(view.grid_spacing is None)
        self.spacing = QDoubleSpinBox()
        self.spacing.setDecimals(4)
        self.spacing.setRange(1e-4, 1e5)
        self.spacing.setSuffix(" mm")
        self.spacing.setValue((view.grid_spacing or view.auto_spacing()) * 1e3)
        self.show = QCheckBox("Show grid")
        self.show.setChecked(view.show_grid)
        for lab, w in (("Working plane", self.plane), ("", self.auto), ("Spacing", self.spacing), ("", self.show)):
            f.addRow(lab, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def apply(self) -> None:
        v = self.view
        v.grid_plane = self.plane.currentText()
        v.grid_spacing = None if self.auto.isChecked() else self.spacing.value() * 1e-3
        v.show_grid = self.show.isChecked()
        v.update_grid()
        v.plotter.render()


class SnapModeDialog(QDialog):
    """Modeler > Snap Mode: which object snaps are active while drawing."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QGroupBox, QSpinBox
        from .viewer import SNAP_LABELS, SNAP_MODES
        self.setWindowTitle("Snap Mode")
        self.view = view
        lay = QVBoxLayout(self)
        box = QGroupBox("Snap to")
        v = QVBoxLayout(box)
        self.checks = {}
        for m in SNAP_MODES:
            c = QCheckBox(SNAP_LABELS[m])
            c.setChecked(m in view.snap_modes)
            v.addWidget(c)
            self.checks[m] = c
        lay.addWidget(box)
        f = QFormLayout()
        self.tol = QSpinBox()
        self.tol.setRange(2, 60)
        self.tol.setSuffix(" pixels")
        self.tol.setValue(int(view.snap_tolerance))
        f.addRow("Snap tolerance", self.tol)
        lay.addLayout(f)
        info = QLabel("Symbols: + grid, \u25a1 vertex, \u25b3 edge center, \u25cb face center, "
                      "\u25c7 quadrant, \u2295 arc center")
        info.setWordWrap(True)
        info.setStyleSheet("color: #606060;")
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply(self) -> None:
        self.view.snap_modes = {m for m, c in self.checks.items() if c.isChecked()}
        self.view.snap_tolerance = self.tol.value()


class OptionsDialog(QDialog):
    """Tools > Options: HPC and analysis, modeler and general settings (stored per user)."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        import os
        from PySide6.QtWidgets import QSpinBox
        self.setWindowTitle("Options")
        self.win = win
        s = win.settings
        ncpu = os.cpu_count() or 1
        tabs = QTabWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

        hpc = QWidget()
        f = QFormLayout(hpc)
        self.all_cores = QCheckBox(f"Use all cores ({ncpu})")
        self.cores = QSpinBox()
        self.cores.setRange(1, ncpu)
        n = int(s.value("hpc/cores", 0))
        self.all_cores.setChecked(n <= 0)
        self.cores.setValue(n if n > 0 else ncpu)
        self.cores.setEnabled(n > 0)
        self.all_cores.toggled.connect(lambda v: self.cores.setEnabled(not v))
        self.workers = QSpinBox()
        self.workers.setRange(1, max(1, ncpu))
        self.workers.setValue(int(s.value("hpc/sweep_workers", 2)))
        self.solver = QComboBox()
        self.solver.addItems(list(schema.SOLVERS))
        self.solver.setCurrentText(s.value("hpc/default_solver", "Auto"))
        f.addRow("Number of cores", self.all_cores)
        f.addRow("", self.cores)
        f.addRow("Parallel sweep processes", self.workers)
        f.addRow("Default matrix solver (new setups)", self.solver)
        self.sparta_state = QLabel()
        self.sparta_install = QPushButton()
        self.sparta_install.setToolTip("Installs the GPU/CPU sparse direct solver SPARTA (AGPL-3.0) of EMerge's\n"
                                       "author from its GitHub release into Hertzon's Python environment.\n"
                                       "Select it as 'SPARTA (GPU)' or 'SPARTA (CPU)' in a setup's Options.")
        self.sparta_install.clicked.connect(self._install_sparta)
        self._sparta_proc = None
        row = QHBoxLayout()
        row.addWidget(self.sparta_state, 1)
        row.addWidget(self.sparta_install)
        f.addRow("SPARTA solver", row)
        self._sparta_status()
        f.addRow(QLabel("Cores are used by the matrix solver, meshing and field post-processing.\n"
                        "A value in a setup's 'Solver threads' overrides this for that setup.\n"
                        "Analyses other computers send here (Remote) use these settings of this computer."))
        tabs.addTab(hpc, "HPC and Analysis")

        rem = QWidget()
        f = QFormLayout(rem)
        self.remote_on = QCheckBox("Solve on a remote computer")
        self.remote_on.setToolTip("Analyses go to the Hertzon remote service on another computer; results come\n"
                                  "back into this project's results folder. Off: solve on this computer.")
        self.remote_on.setChecked(s.value("remote/enabled", False, type=bool))
        self.remote_addr = QLineEdit(s.value("remote/address", ""))
        self.remote_addr.setPlaceholderText("simstation02 or 192.168.1.20:47820")
        self.remote_token = QLineEdit(s.value("remote/token", ""))
        self.remote_token.setEchoMode(QLineEdit.Password)
        self.remote_find = QPushButton("Find in Network ...")
        self.remote_find.setToolTip("Looks for computers in the local network that share themselves "
                                    "(Share this computer below)\nand connects with the pairing code shown there")
        self.remote_find.clicked.connect(self._find_remote)
        self.remote_test = QPushButton("Test Connection")
        self.remote_state = QLabel()
        self.remote_state.setWordWrap(True)
        self.remote_test.clicked.connect(self._test_remote)
        f.addRow("", self.remote_on)
        crow = QHBoxLayout()
        crow.addWidget(self.remote_addr, 1)
        crow.addWidget(self.remote_find)
        f.addRow("Computer", crow)
        f.addRow("Token", self.remote_token)
        row = QHBoxLayout()
        row.addWidget(self.remote_test)
        row.addWidget(self.remote_state, 1)
        f.addRow("", row)
        hint = QLabel("<i>Find in Network</i> lists the computers that share themselves (<i>Share this "
                      "computer</i> below, on the other computer) and connects with the six-digit pairing code "
                      "shown there. Address and token can also be entered by hand.")
        hint.setWordWrap(True)
        f.addRow(hint)

        from PySide6.QtWidgets import QApplication
        share = QGroupBox("Share this computer: let other Hertzon installations solve here")
        g = QFormLayout(share)
        self.share_state = QLabel()
        self.share_state.setWordWrap(True)
        self.share_state.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.share_btn = QPushButton()
        self.share_btn.clicked.connect(self._toggle_share)
        row = QHBoxLayout()
        row.addWidget(self.share_state, 1)
        row.addWidget(self.share_btn)
        g.addRow(row)
        self.share_addr = QLabel()
        self.share_addr.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.share_token = QLineEdit()
        self.share_token.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.share_token.text()))
        trow = QHBoxLayout()
        trow.addWidget(self.share_token, 1)
        trow.addWidget(copy)
        self.share_code = QLabel()
        self.share_code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.share_code.setToolTip("Enter this code on the other computer after Find in Network; it changes "
                                   "with each start of the service")
        g.addRow("Computer", self.share_addr)
        g.addRow("Pairing code", self.share_code)
        g.addRow("Token", trow)
        self.share_auto = QCheckBox("Start the service at login (also without Hertzon open)")
        g.addRow("", self.share_auto)
        note = QLabel("The service keeps running when Hertzon is closed. The connection is meant for the "
                      "local network; the token is sent unencrypted. Windows may ask once whether Python "
                      "may accept connections: allow it for private networks.")
        note.setWordWrap(True)
        g.addRow(note)
        f.addRow(share)
        self._share_status()
        tabs.addTab(rem, "Remote")

        mod = QWidget()
        f = QFormLayout(mod)
        self.units = QComboBox()
        self.units.addItems(["um", "mm", "cm", "meter", "mil", "in"])
        self.units.setCurrentText(s.value("modeler/default_units", "mm"))
        self.edit_new = QCheckBox("Edit properties of new primitives")
        self.edit_new.setChecked(win.edit_new_primitives)
        self.quality = QComboBox()
        self.quality.addItems(["coarse", "normal", "fine"])
        self.quality.setCurrentText(win.doc.display_quality)
        f.addRow("Default units of new designs", self.units)
        f.addRow("Display quality", self.quality)
        f.addRow("", self.edit_new)
        tabs.addTab(mod, "Modeler")

        gen = QWidget()
        f = QFormLayout(gen)
        self.emlog = QCheckBox("Show EMerge solver log in the Message Manager")
        self.emlog.setChecked(win.solver.show_emerge_log)
        f.addRow("", self.emlog)
        from .update_check import SETTING as UPDATE_SETTING
        self.update_check = QCheckBox("Check for a new Hertzon version at start")
        self.update_check.setToolTip("Asks GitHub for the newest release and, when one is available, shows its\n"
                                     "release notes with Install, Skip this version and Later. Nothing is installed\n"
                                     "without a click on Install, nothing is sent but the request itself, and\n"
                                     "without network nothing happens. Help > Check for Updates checks on request.")
        self.update_check.setChecked(s.value(UPDATE_SETTING, True, type=bool))
        f.addRow("", self.update_check)
        tabs.addTab(gen, "General")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _sparta_status(self, note: str = "") -> None:
        from ..solver import sparta_setup
        v = sparta_setup.installed_version()
        self.sparta_state.setText(note or (f"SPARTA {v} installed" if v else "Not installed"))
        self.sparta_install.setText("Update SPARTA" if v and v != sparta_setup.VERSION else
                                    "Reinstall SPARTA" if v else "Install SPARTA")

    def _install_sparta(self) -> None:
        """pip installs the SPARTA release wheel in a QProcess; the next solve uses it."""
        from PySide6.QtCore import QProcess
        from ..solver import sparta_setup
        args = sparta_setup.install_args()
        p = QProcess(self.win)        # keeps running when the dialog is closed
        p.setProcessChannelMode(QProcess.MergedChannels)
        out: list[bytes] = []
        p.readyReadStandardOutput.connect(lambda: out.append(bytes(p.readAllStandardOutput())))

        def done(code, _status):
            text = b"".join(out).decode("utf-8", "replace").strip()
            if code == 0:
                self.win.msgs.add("info", f"SPARTA {sparta_setup.installed_version() or ''} installed; "
                                          "select it as the matrix solver of a setup")
            else:
                self.win.msgs.add("error", f"Installing SPARTA failed: {text[-2000:]}")
            try:
                self._sparta_proc = None
                self.sparta_install.setEnabled(True)
                self._sparta_status("" if code == 0 else "Installation failed (see Message Manager)")
            except RuntimeError:      # the dialog is gone
                pass
        p.finished.connect(done)
        self.sparta_install.setEnabled(False)
        self.sparta_state.setText("Installing ...")
        self._sparta_proc = p
        p.start(args[0], args[1:])

    def _share_status(self, info: dict | None = None, checked: bool = False) -> None:
        from ..remote import local
        from ..remote.__main__ import autostart_enabled
        if not checked:
            info = local.status()
        self._share_running = info is not None
        self.share_state.setText(f"Running: Hertzon {info.get('hertzon')}, {info.get('cores')} cores"
                                 + (", solving" if info.get("busy") else "") if info else "Not running")
        self.share_btn.setText("Stop Service" if info else "Start Service")
        self.share_addr.setText("  or  ".join(local.share_addresses()))
        self.share_token.setText(local.token())
        code = local.pairing_code() if info else ""
        self.share_code.setText(f"<b style='font-size:14pt; letter-spacing:3px'>{code[:3]} {code[3:]}</b>"
                                if code else "(shown while the service runs)")
        if not hasattr(self, "_share_auto0"):
            self._share_auto0 = autostart_enabled()
            self.share_auto.setChecked(self._share_auto0)

    def _toggle_share(self) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication
        from ..remote import local
        QApplication.setOverrideCursor(_Qt.WaitCursor)
        try:
            if self._share_running:
                local.stop()
                info = local.status()
            else:
                info = local.start()
                if info is None:
                    self.share_state.setText("The service did not start; see service.log in "
                                             f"{local.default_workdir()}")
                    return
        finally:
            QApplication.restoreOverrideCursor()
        self._share_status(info, checked=True)

    def _find_remote(self) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication, QInputDialog
        from ..remote.client import RemoteError, discover, pair
        QApplication.setOverrideCursor(_Qt.WaitCursor)
        try:
            found = discover()
        finally:
            QApplication.restoreOverrideCursor()
        try:
            from ..remote import local
            own = local.status()
        except Exception:  # noqa: BLE001
            own = None
        if own:                                       # this computer itself is no remote computer for us
            found = [f for f in found if f.get("host") != own.get("host")]
        if not found:
            self.remote_state.setText("No computer in the network shares itself. On the other computer: "
                                      "Tools > Options > Remote > Start Service.")
            return
        names = [f"{f.get('host')}  ({f['address']}, Hertzon {f.get('hertzon')}, {f.get('cores')} cores"
                 + (", busy)" if f.get("busy") else ")") for f in found]
        name, ok = QInputDialog.getItem(self, "Find in Network", "Computer:", names, 0, False)
        if not ok:
            return
        sel = found[names.index(name)]
        code, ok = QInputDialog.getText(self, "Pairing Code",
                                        f"Pairing code shown on {sel.get('host')}\n(Tools > Options > Remote):")
        if not ok or not code.strip():
            return
        try:
            tok = pair(sel["address"], code.replace(" ", ""))
        except RemoteError as ex:
            self.remote_state.setText(str(ex))
            return
        self.remote_addr.setText(f"{sel.get('host')}:{sel['address'].rsplit(':', 1)[1]}")
        self.remote_token.setText(tok)
        self.remote_on.setChecked(True)
        self._test_remote()
        if self.remote_state.text().startswith("Connected"):
            return
        self.remote_addr.setText(sel["address"])          # the name does not resolve here: use the address
        self._test_remote()

    def _test_remote(self) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication
        from .. import __version__
        from ..remote.client import RemoteError, RemoteHost
        QApplication.setOverrideCursor(_Qt.WaitCursor)
        try:
            info = RemoteHost(self.remote_addr.text(), self.remote_token.text(), timeout=10).info()
        except RemoteError as ex:
            self.remote_state.setText(str(ex))
            return
        finally:
            QApplication.restoreOverrideCursor()
        text = (f"Connected: {info.get('host')}, Hertzon {info.get('hertzon')}, {info.get('cores')} cores"
                + (", busy" if info.get("busy") else "") + (f", {info['queued']} queued" if info.get("queued") else ""))
        if info.get("hertzon") != __version__:
            text += f". This computer runs {__version__}; install the same version on both."
        self.remote_state.setText(text)

    def apply(self) -> None:
        s, w = self.win.settings, self.win
        s.setValue("hpc/cores", 0 if self.all_cores.isChecked() else self.cores.value())
        s.setValue("hpc/sweep_workers", self.workers.value())
        s.setValue("hpc/default_solver", self.solver.currentText())
        s.setValue("remote/enabled", self.remote_on.isChecked())
        s.setValue("remote/address", self.remote_addr.text().strip())
        s.setValue("remote/token", self.remote_token.text().strip())
        if self.share_auto.isChecked() != self._share_auto0:
            import contextlib
            import io
            from ..remote.__main__ import autostart
            with contextlib.redirect_stdout(io.StringIO()):
                autostart(self.share_auto.isChecked(), [])
        s.setValue("modeler/default_units", self.units.currentText())
        w._toggle_edit_new(self.edit_new.isChecked())
        w.a_editnew.setChecked(self.edit_new.isChecked())
        if self.quality.currentText() != w.doc.display_quality:
            w.set_quality(self.quality.currentText())
        w.a_emlog.setChecked(self.emlog.isChecked())
        from .update_check import SETTING as UPDATE_SETTING
        s.setValue(UPDATE_SETTING, self.update_check.isChecked())
        w.apply_hpc_settings()


class EditSourcesDialog(QDialog):
    """Edit Sources: magnitude and phase of every excitation for post processing, and for an
    incident wave the field that is shown (Total, Scattered or Incident)."""

    def __init__(self, parent, design, on_apply=None, names: list[str] | None = None):
        super().__init__(parent)
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QComboBox, QDoubleSpinBox, QLabel, QHBoxLayout
        from ..model import sources as SRC
        self.design = design
        self.setWindowTitle("Edit Sources")
        self.resize(560, 320)
        lay = QVBoxLayout(self)
        # names: the terminals of a solved terminal design (its fields are stored per terminal)
        cur = SRC.get(design, names) or SRC.defaults(design, names)
        kinds = {e.name: e.kind for e in design.excitations}
        self.names = list(cur)
        t = QTableWidget(len(self.names), 5)
        t.setHorizontalHeaderLabels(["Source", "Type", "Magnitude", "Unit", "Phase (deg)"])
        t.verticalHeader().setVisible(False)
        self.mag, self.ph = {}, {}
        for r, n in enumerate(self.names):
            pw = kinds.get(n) == "Incident Plane Wave"
            for c, txt in ((0, n), (1, "Incident Wave" if pw else "Terminal" if names else "Port"),
                           (3, "V/m" if pw else "W")):
                it = QTableWidgetItem(txt)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, c, it)
            m = QDoubleSpinBox()
            m.setRange(0, 1e9)
            m.setDecimals(6)
            m.setValue(cur[n]["mag"])
            p = QDoubleSpinBox()
            p.setRange(-360, 360)
            p.setDecimals(3)
            p.setValue(cur[n]["phase"])
            t.setCellWidget(r, 2, m)
            t.setCellWidget(r, 4, p)
            self.mag[n], self.ph[n] = m, p
        t.resizeColumnsToContents()
        lay.addWidget(t)
        self.ftype = QComboBox()
        self.ftype.addItems(SRC.FIELD_TYPES)
        self.ftype.setCurrentText(SRC.field_type(design))
        if any(kinds.get(n) == "Incident Plane Wave" for n in self.names):
            row = QHBoxLayout()
            row.addWidget(QLabel("Incident wave fields to plot:"))
            row.addWidget(self.ftype)
            row.addStretch(1)
            lay.addLayout(row)
        lay.addWidget(QLabel("Field plots and animations use these sources; no new solve is needed. "
                             "Port fields are for 1 W incident power, scaled by sqrt(P)."
                             + ("\nTerminals: the other terminals are terminated in their reference "
                                "impedance. Differential mode with 1 W: 0.5 W on the positive terminal "
                                "at 0 deg and 0.5 W on the negative terminal at 180 deg." if names else "")))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        # Apply: plots and reports follow the sources at once, the dialog stays open
        if on_apply is not None:
            bb.button(QDialogButtonBox.Apply).clicked.connect(lambda: on_apply(*self.values()))
        else:
            bb.button(QDialogButtonBox.Apply).hide()
        lay.addWidget(bb)

    def values(self) -> tuple[dict, str]:
        return ({n: {"mag": self.mag[n].value(), "phase": self.ph[n].value()} for n in self.names},
                self.ftype.currentText())
