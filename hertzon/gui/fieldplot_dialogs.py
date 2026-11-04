"""Create / modify field plots and their attributes, after the reference tool's
"Modify Plot" and "Modify Attributes" (Color map / Scale / Marker-Arrow / Plots) dialogs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from . import fields as F

ATTR_KEYS = list(F.ATTR_DEFAULTS)


# ================================================================================================
# Create / Modify Plot
# ================================================================================================
class FieldPlotDialog(QDialog):
    """Quantity, solution, intrinsic variables (frequency, phase), excited port and where to plot."""

    def __init__(self, win, quantity: str, fp=None, has_selection: bool = False):
        super().__init__(win)
        self.win, self.fp = win, fp
        self.setWindowTitle(f"Modify Field Plot: {fp.name}" if fp else f"Create Field Plot: {quantity}")
        f = QFormLayout(self)
        self.name = QLineEdit(fp.name if fp else "")
        self.name.setVisible(fp is not None)
        if fp is not None:
            f.addRow("Name", self.name)
        self.qty = QComboBox()
        self.qty.addItems(F.QUANTITIES)
        exprs = [e.name for e in getattr(win.doc.design, "field_exprs", None) or []]
        if exprs:
            self.qty.insertSeparator(self.qty.count())
            self.qty.addItems(exprs)
        self.qty.setCurrentText(fp.quantity if fp else quantity)
        qrow = QHBoxLayout()
        qrow.addWidget(self.qty, 1)
        calc = QPushButton("Fields Calculator...")
        calc.setToolTip("Define a new quantity as an expression of E, H and J")
        calc.clicked.connect(self._calculator)
        qrow.addWidget(calc)
        f.addRow("Quantity", qrow)
        self.sol = QComboBox()
        sols = F.field_solutions(win.doc)
        self.sol.addItems(sols)
        if fp and fp.solution not in sols:
            self.sol.addItem(fp.solution)
        if fp:
            self.sol.setCurrentText(fp.solution)
        f.addRow("Solution", self.sol)
        self.freq = QComboBox()
        self.sol.currentTextChanged.connect(self._load_freqs)
        self._load_freqs(self.sol.currentText())
        f.addRow("Frequency", self.freq)
        self.phase = QDoubleSpinBox()
        self.phase.setRange(-360, 360)
        self.phase.setSuffix(" deg")
        f.addRow("Phase", self.phase)
        self.port = QComboBox()
        ports = win.des.ports() if hasattr(win.des, "ports") else []
        nports = max(1, len(ports))
        for i in range(1, nports + 1):
            self.port.addItem(str(i))
        f.addRow("Excited port", self.port)
        self._load_freqs(self.sol.currentText())     # again, now with its row: names it for an eigenmode solution
        self.where = QComboBox()
        self.where.addItems(["(selection)", "(selection, inside solids)", "XY", "YZ", "XZ"])
        self.where.setItemData(1, "Selected solids: the field inside them, as coloured dots (scalars) or arrows",
                               Qt.ToolTipRole)
        self.pval = QLineEdit("")
        self.pval.setPlaceholderText("cut position (empty = model centre)")
        self.keep = QCheckBox("Keep the plotted objects / faces")
        self.has_selection = has_selection
        self.had_targets = bool(fp is not None and (fp.faces or fp.objects))
        if fp is not None:
            p = fp.props
            self.freq.setCurrentIndex(min(int(p.get("freq", 0)), self.freq.count() - 1))
            self.phase.setValue(float(p.get("phase", 0)))
            self.port.setCurrentText(str(p.get("port", 1)))
            if fp.plane:
                ax, _, val = fp.plane.partition("@")
                self.where.setCurrentText(ax)
                self.pval.setText(val)
            elif F.attributes(fp.props)["Volume"]:
                self.where.setCurrentIndex(1)
            targets = [*(fp.objects or []), *[f"{r.obj}:face{r.face}" for r in (fp.faces or [])]]
            self.keep.setText(f"Keep: {', '.join(targets)[:60] or '(cut plane)'}")
            self.keep.setChecked(not has_selection)
        elif not has_selection:
            self.where.setCurrentText("XY")
        f.addRow("Plot on", self.where)
        f.addRow("Cut position", self.pval)
        self.pval_label = f.labelForField(self.pval)
        if fp is not None:
            f.addRow("", self.keep)
        self.pick_hint = QLabel()
        self.pick_hint.setWordWrap(True)
        self.pick_hint.setStyleSheet("color:#2a5db0")
        f.addRow("", self.pick_hint)
        self.where.currentIndexChanged.connect(self._where_changed)
        self.keep.toggled.connect(self._where_changed)
        self._where_changed()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def needs_pick(self) -> bool:
        """Plot on a selection with nothing selected (and nothing kept): the targets are picked after OK."""
        if self.where.currentIndex() > 1 or self.has_selection:
            return False
        return not (self.had_targets and self.keep.isChecked())

    def _where_changed(self, *_) -> None:
        sel = self.where.currentIndex() <= 1
        self.pval.setVisible(not sel)
        if self.pval_label is not None:
            self.pval_label.setVisible(not sel)
        self.keep.setVisible(sel and self.had_targets)
        what = "the solids" if self.where.currentIndex() == 1 else "the faces or objects"
        self.pick_hint.setText(f"After OK you pick {what} in the 3D view." if self.needs_pick() else "")
        self.pick_hint.setVisible(self.needs_pick())
        self.adjustSize()

    def _calculator(self) -> None:
        """Leave this dialog for the calculator; saving the new expression there opens it again."""
        self.reject()
        self.win.fields_calculator(for_plot=True)

    def _load_freqs(self, text: str) -> None:
        self.freq.clear()
        try:
            st, sl = F.store_for(self.win.doc, text)
            from ..solver.lazy_fields import frequencies
            eigen = bool(st.read_json("eigenmodes.json"))
            # an eigenmode solution has one frequency per mode: its eigenfrequency
            self.freq.addItems([(f"Mode {i + 1}: " if eigen else "") + f"{fr / 1e9:.6g} GHz"
                                for i, fr in enumerate(frequencies(st, sl))])
        except Exception:  # noqa: BLE001
            eigen = False
        if self.freq.count() == 0:
            self.freq.addItem("(first)")
        self._eigen(eigen)

    def _eigen(self, eigen: bool) -> None:
        """An eigenmode solution: the mode is chosen as its frequency, there is no port to excite."""
        lbl = self.layout().labelForField(self.freq)
        if lbl is not None:
            lbl.setText("Eigenmode" if eigen else "Frequency")
        if hasattr(self, "port"):
            self.port.setEnabled(not eigen)
            self.port.setToolTip("An eigenmode has no excitation" if eigen else "")

    def values(self) -> dict:
        sel = self.where.currentIndex() <= 1
        pl = "" if sel else f"{self.where.currentText()}@{self.pval.text()}"
        return {"name": self.name.text().strip(), "quantity": self.qty.currentText(),
                "solution": self.sol.currentText(), "plane": pl, "keep": self.keep.isChecked(),
                "props": {"port": self.port.currentText(), "freq": str(max(0, self.freq.currentIndex())),
                          "phase": f"{self.phase.value():g}", "Volume": str(self.where.currentIndex() == 1)}}


class PickTargets(QDialog):
    """Shown over the 3D Modeler while the faces or objects of a plot are picked there: what to pick, what is
    selected, and Plot / Cancel."""

    def __init__(self, win, what: str, on_use):
        super().__init__(win, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.win, self.doc, self.on_use, self.what = win, win.doc, on_use, what
        title, how = PICK_HOW[what]
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{title}</b>"))
        lay.addWidget(QLabel(how))
        self.sel = QLabel()
        self.sel.setStyleSheet("font-family:Consolas,monospace")
        self.sel.setWordWrap(True)
        lay.addWidget(self.sel)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Plot")
        bb.accepted.connect(self._use)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.mode0 = self.doc.selection_mode
        mode = "object" if what != "faces" else self.mode0 if self.mode0 in ("face", "object", "edge") else "face"
        if mode != self.mode0:
            win.set_mode(mode)
        win.tabs.setCurrentIndex(0)
        self.doc.selectionChanged.connect(self._show)
        self.finished.connect(self._done)
        self._show()
        g = win.geometry()
        self.setMinimumWidth(360)
        self.adjustSize()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 90)

    def targets(self) -> tuple[list, list]:
        if self.what == "solids":
            return [], [o for o in self.doc.selected_objects() if getattr(self.doc.tess.get(o), "dim", 3) == 3]
        if self.what == "objects":
            return [], self.doc.selected_objects()
        return self.win._plot_selection()

    def _show(self, *_) -> None:
        faces, objs = self.targets()
        names = [*objs, *[f"{r.obj}:face{r.face}" for r in faces]]
        self.sel.setText("Selected: " + (", ".join(names)[:80] or "—"))

    def _use(self) -> None:
        faces, objs = self.targets()
        if not faces and not objs:
            self.sel.setText("<span style='color:#b00020'>Select " +
                             {"solids": "a solid", "objects": "an object"}.get(self.what, "a face or an object") + " first</span>")
            return
        self.accept()
        self.on_use(faces, objs)

    def _done(self, *_) -> None:
        if self.mode0 is None:
            return
        self.doc.selectionChanged.disconnect(self._show)
        if self.doc.selection_mode != self.mode0:
            self.win.set_mode(self.mode0)
        self.mode0 = None


PICK_HOW = {
    "faces": ("Pick faces or objects", "Click a face, a sheet or an object in the 3D view (Ctrl+click: "
              "several). F: face mode, O: object mode."),
    "solids": ("Pick solids", "Click the solids to plot inside in the 3D view (Ctrl+click: several)."),
    "objects": ("Pick objects", "Click the objects in the 3D view (Ctrl+click: several)."),
}


# ================================================================================================
# Modify Attributes
# ================================================================================================
class PlotAttributesDialog(QDialog):
    """Non-modal like the reference tool: change attributes and see the plot update (real time mode)."""

    def __init__(self, win, name: str):
        super().__init__(win)
        self.win = win
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Plot:"))
        self.plot = QComboBox()
        self.plot.addItems([p.name for p in win.des.field_plots])
        self.plot.setCurrentText(name)
        top.addWidget(self.plot, 1)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.w: dict[str, QWidget] = {}
        self._colormap_tab()
        self._scale_tab()
        self._arrow_tab()
        self._plots_tab()
        bottom = QHBoxLayout()
        self.realtime = QCheckBox("Real time mode")
        self.realtime.setChecked(True)
        bottom.addWidget(self.realtime)
        bottom.addStretch(1)
        save_def = QPushButton("Save as default")
        save_def.setToolTip("Use these attributes for new field plots")
        save_def.clicked.connect(self._save_default)
        bottom.addWidget(save_def)
        ap = QPushButton("Apply")
        ap.clicked.connect(self.apply)
        bottom.addWidget(ap)
        cl = QPushButton("Close")
        cl.clicked.connect(self.close)
        bottom.addWidget(cl)
        lay.addLayout(bottom)
        self.plot.currentTextChanged.connect(self._load)
        self._load(name)
        for wdg in self.w.values():
            for sig in ("valueChanged", "currentTextChanged", "toggled"):
                if hasattr(wdg, sig):
                    getattr(wdg, sig).connect(self._changed)
                    break
        self.resize(380, 520)

    # ---- tabs ------------------------------------------------------------------------------
    def _colormap_tab(self) -> None:
        t = QWidget()
        f = QFormLayout(t)
        cm = QComboBox()
        cm.addItems(list(F.COLOR_MAPS))
        self.w["ColorMap"] = cm
        f.addRow("Spectrum type", cm)
        self.w["Invert"] = QCheckBox("Invert")
        f.addRow("", self.w["Invert"])
        n = QSpinBox()
        n.setRange(0, 256)
        n.setSpecialValueText("continuous")
        self.w["Colors"] = n
        f.addRow("Number of colors", n)
        self.tabs.addTab(t, "Color map")

    def _scale_tab(self) -> None:
        t = QWidget()
        v = QVBoxLayout(t)
        row = QHBoxLayout()
        row.addWidget(QLabel("Num. Division"))
        nd = QSpinBox()
        nd.setRange(1, 64)
        self.w["Divisions"] = nd
        row.addWidget(nd)
        row.addStretch(1)
        v.addLayout(row)
        g = QGroupBox()
        gl = QGridLayout(g)
        self._auto = QRadioButton("Auto")
        self._lim = QRadioButton("Use Limits")
        bg = QButtonGroup(self)
        bg.addButton(self._auto)
        bg.addButton(self._lim)
        self.w["Scale"] = self._lim            # toggled -> Use Limits
        mn, mx = QDoubleSpinBox(), QDoubleSpinBox()
        for sb in (mn, mx):
            sb.setRange(-1e12, 1e12)
            sb.setDecimals(6)
        self.w["Min"], self.w["Max"] = mn, mx
        gl.addWidget(self._auto, 0, 0)
        gl.addWidget(QLabel("Min:"), 0, 1)
        gl.addWidget(mn, 0, 2)
        gl.addWidget(self._lim, 1, 0)
        gl.addWidget(QLabel("Max:"), 1, 1)
        gl.addWidget(mx, 1, 2)
        self.w["dB"] = QCheckBox("dB")
        gl.addWidget(self.w["dB"], 2, 0)
        gl.addWidget(QLabel("Units"), 2, 1)
        un = QComboBox()
        self.w["Units"] = un
        gl.addWidget(un, 2, 2)
        v.addWidget(g)
        g2 = QGroupBox()
        h = QHBoxLayout(g2)
        self._linear = QRadioButton("Linear")
        self._log = QRadioButton("Log")
        bg2 = QButtonGroup(self)
        bg2.addButton(self._linear)
        bg2.addButton(self._log)
        self.w["Log"] = self._log
        h.addWidget(self._linear)
        h.addWidget(self._log)
        v.addWidget(g2)
        g3 = QGroupBox("Auto Scale Options")
        g3l = QGridLayout(g3)
        self.w["LimitPrecision"] = QCheckBox("Limit Max/Min precision to")
        pr = QSpinBox()
        pr.setRange(1, 12)
        self.w["Precision"] = pr
        g3l.addWidget(self.w["LimitPrecision"], 0, 0)
        g3l.addWidget(pr, 0, 1)
        g3l.addWidget(QLabel("digits"), 0, 2)
        self.w["AnimScale"] = QCheckBox("Use Current Scale For Animation")
        g3l.addWidget(self.w["AnimScale"], 1, 0, 1, 3)
        v.addWidget(g3)
        g4 = QGroupBox("Number Format")
        g4l = QHBoxLayout(g4)
        fm = QComboBox()
        fm.addItems(["Automatic", "Decimal", "Scientific"])
        self.w["Format"] = fm
        g4l.addWidget(QLabel("Type:"))
        g4l.addWidget(fm)
        g4l.addStretch(1)
        v.addWidget(g4)
        v.addStretch(1)
        self.tabs.addTab(t, "Scale")

    def _arrow_tab(self) -> None:
        t = QWidget()
        f = QFormLayout(t)
        sz = QSlider(Qt.Horizontal)
        sz.setRange(10, 400)            # percent
        sz.setTracking(False)
        self.w["ArrowSize"] = sz
        f.addRow("Arrow size", sz)
        th = QSlider(Qt.Horizontal)
        th.setRange(25, 400)            # percent
        th.setTracking(False)
        self.w["ArrowThickness"] = th
        f.addRow("Arrow thickness", th)
        self.w["MapSize"] = QCheckBox("Map size (length follows magnitude)")
        f.addRow("", self.w["MapSize"])
        self.w["ArrowTail"] = QCheckBox("Arrow tail")
        f.addRow("", self.w["ArrowTail"])
        cnt = QSpinBox()
        cnt.setRange(20, 20000)
        cnt.setSingleStep(100)
        self.w["ArrowCount"] = cnt
        f.addRow("Number of arrows", cnt)
        self._arrow_form, self._line_cells = f, []
        sp = QDoubleSpinBox()
        sp.setRange(0, 1e6)
        sp.setDecimals(4)
        sp.setSpecialValueText("auto (from number)")
        sp.setSuffix(f" {self.win.des.units}")
        self.w["ArrowSpacing"] = sp
        f.addRow("Arrow spacing", sp)

        def count_set(_v):         # on a line: a typed number of arrows replaces the spacing
            if self._line_cells and not getattr(self, "_loading", False):
                sp.blockSignals(True)
                sp.setValue(0)
                sp.blockSignals(False)

        def spacing_set(_v):       # on a line: show how many arrows the spacing gives
            if self._line_cells and not getattr(self, "_loading", False):
                cnt.blockSignals(True)
                cnt.setValue(self._line_count(sp.value(), cnt.value()))
                cnt.blockSignals(False)
        cnt.valueChanged.connect(count_set)
        sp.valueChanged.connect(spacing_set)
        f.addRow(QLabel("Applies to Vector plots (E, H, Jsurf, J)."))
        self.tabs.addTab(t, "Marker/Arrow")

    def _plots_tab(self) -> None:
        t = QWidget()
        f = QFormLayout(t)
        tr = QSlider(Qt.Horizontal)
        tr.setRange(0, 100)
        tr.setTracking(False)
        self.w["Transparency"] = tr
        f.addRow("Transparency", tr)
        pt = QComboBox()
        pt.addItems(F.PLOT_TYPES)
        self.w["PlotType"] = pt
        f.addRow("Plot type", pt)
        self.w["ShowGrid"] = QCheckBox("Show grid (solver mesh lines)")
        f.addRow("", self.w["ShowGrid"])
        self.w["Volume"] = QCheckBox("Inside the selected solids (volume plot)")
        self.w["Volume"].setToolTip("Scalars as coloured dots through the volume, vectors as arrows; "
                                    "off: on the surface of the solids")
        f.addRow("", self.w["Volume"])
        self.tabs.addTab(t, "Plots")

    # ---- load / store ------------------------------------------------------------------------
    def _fp(self):
        return next((p for p in self.win.des.field_plots if p.name == self.plot.currentText()), None)

    def _load(self, _name: str = "") -> None:
        fp = self._fp()
        if fp is None:
            return
        self.setWindowTitle(f"{fp.name} -- {fp.quantity}")
        a = F.attributes(fp.props)
        self._loading = True
        try:
            calc = F.calc_expression(self.win.doc, fp.quantity) is not None
            base = "" if calc else F.base_unit(fp.quantity)    # an expression shows its own unit
            un = self.w["Units"]
            un.clear()
            un.addItems([u for u, _ in F.UNITS.get(base, [])] or ["(expression)"])
            un.setCurrentText(a["Units"] or base)
            for k, wdg in self.w.items():
                v = a[k]
                if k == "Scale":
                    (self._lim if v == "Use Limits" else self._auto).setChecked(True)
                elif k == "Log":
                    (self._log if v else self._linear).setChecked(True)
                elif k == "Units":
                    continue
                elif k in ("ArrowSize", "ArrowThickness"):
                    wdg.setValue(int(round(v * 100)))
                elif k == "Transparency":
                    wdg.setValue(int(round(v * 100)))
                elif isinstance(wdg, QCheckBox):
                    wdg.setChecked(bool(v))
                elif isinstance(wdg, QComboBox):
                    wdg.setCurrentText(str(v))
                else:
                    wdg.setValue(v)
            if a["Scale"] != "Use Limits":
                # show the current auto range in the (disabled) limit boxes like the reference tool
                lo, hi = self._current_range(fp)
                if lo is not None:
                    self.w["Min"].setValue(lo)
                    self.w["Max"].setValue(hi)
            self._sync_line(fp, a)
            self._sync_enabled()
        finally:
            self._loading = False

    def _unit_m(self) -> float:
        from ..model.expr import LENGTH_UNITS
        return LENGTH_UNITS.get(self.win.des.units, 1e-3)

    def _line_count(self, spacing: float, count: int) -> int:
        h = F.line_spacing(self._line_cells, spacing * self._unit_m(), count)
        return F.line_arrow_count(self._line_cells, h)

    def _sync_line(self, fp, a: dict) -> None:
        """A plot on polylines: "Arrows on line" is the real number of arrows on them."""
        try:
            self._line_cells = F.plot_line_cells(self.win.view, fp.objects, fp.faces, fp.plane)
        except Exception:  # noqa: BLE001
            self._line_cells = []
        cnt = self.w["ArrowCount"]
        lab = self._arrow_form.labelForField(cnt)
        if self._line_cells:
            lab.setText("Arrows on line")
            cnt.setRange(1, 20000)
            cnt.setSingleStep(1)
            cnt.setValue(self._line_count(a["ArrowSpacing"], a["LineArrows"]))
        else:
            lab.setText("Number of arrows")
            cnt.setRange(20, 20000)
            cnt.setSingleStep(100)

    def _current_range(self, fp):
        r = getattr(self.win, "field_ranges", {}).get(fp.name)
        return (float(r[0]), float(r[1])) if r else (None, None)

    def _sync_enabled(self) -> None:
        lim = self._lim.isChecked()
        self.w["Min"].setEnabled(lim)
        self.w["Max"].setEnabled(lim)
        self.w["Precision"].setEnabled(self.w["LimitPrecision"].isChecked())

    def values(self) -> dict:
        out = {}
        for k, wdg in self.w.items():
            if k == "Scale":
                out[k] = "Use Limits" if self._lim.isChecked() else "Auto"
            elif k == "Log":
                out[k] = str(self._log.isChecked())
            elif k in ("ArrowSize", "ArrowThickness", "Transparency"):
                out[k] = f"{wdg.value() / 100:g}"
            elif isinstance(wdg, QCheckBox):
                out[k] = str(wdg.isChecked())
            elif isinstance(wdg, QComboBox):
                out[k] = wdg.currentText()
            else:
                out[k] = f"{wdg.value():g}"
        if self._line_cells:
            fp = self._fp()
            out["LineArrows"] = out.pop("ArrowCount")    # the surface count of the plot stays as it was
            if fp is not None and "ArrowCount" in fp.props:
                out["ArrowCount"] = fp.props["ArrowCount"]
        return out

    def _changed(self, *_a) -> None:
        if getattr(self, "_loading", False):
            return
        self._sync_enabled()
        if self.realtime.isChecked():
            self.apply()

    def apply(self) -> None:
        fp = self._fp()
        if fp is None:
            return
        fp.props.update(self.values())
        fp.props.pop("log", None)
        self.win.doc.dirty = True
        self.win.show_field_plot(fp.name)

    def _save_default(self) -> None:
        from PySide6.QtCore import QSettings
        import json
        QSettings("Hertzon", "Hertzon").setValue("fields/default_attributes", json.dumps(self.values()))
        self.win.msgs.add("info", "Field plot attributes saved as default for new plots")


def default_attributes() -> dict:
    from PySide6.QtCore import QSettings
    import json
    try:
        return dict(json.loads(QSettings("Hertzon", "Hertzon").value("fields/default_attributes", "{}") or "{}"))
    except (ValueError, TypeError):
        return {}
