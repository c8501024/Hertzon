"""The optimization setup dialog (goals, variables, optimizer) and the results window."""
from __future__ import annotations

import json

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox, QTabWidget,
                               QWidget, QTableWidget, QTableWidgetItem, QPushButton, QDialogButtonBox, QLabel,
                               QHeaderView, QMessageBox, QCheckBox, QDoubleSpinBox, QAbstractItemView, QSplitter)

from ..model import schema
from ..model.design import Optimization
from ..model.filters import filter_mask_goals
from ..solver.optimetrics import split_value, fmt_value
from .props import TabbedPropertyGrid

GOAL_COLUMNS = ["Calculation", "Start", "Stop", "Condition", "Goal", "Weight", "Margin"]
MARGIN_COL = 6                  # read-only: the margin of the goal on the current solution


def goal_solutions(design) -> list[str]:
    out = []
    for s in design.setups:
        if s.eigen:
            continue
        out += [f"{s.name} : {sw.name}" for sw in s.sweeps] + [f"{s.name} : LastAdaptive"]
    return out


def new_optimization(design) -> Optimization:
    names = {o.name for o in design.optimetrics}
    i = 1
    while f"Optimization{i}" in names:
        i += 1
    op = Optimization(f"Optimization{i}")
    sols = goal_solutions(design)
    op.solution = sols[0] if sols else ""
    for name, val in design.variables.items():
        sv = split_value(val)
        if sv is None:
            continue
        v, unit = sv
        d = abs(v) * 0.1 or 1.0
        op.variables.append({"name": name, "include": False, "min": fmt_value(v - d, unit),
                             "max": fmt_value(v + d, unit)})
    return op


class FilterMaskDialog(QDialog):
    """Goals of a band-pass filter mask (as in the filter optimizer): insertion loss, return loss and ripple
    in the passband, rejection in the stopbands, both directions with weight 1/2 each."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter Mask")
        f = QFormLayout(self)
        self.pb = QLineEdit("2.3GHz, 2.6GHz")
        self.sb = QLineEdit("1.8GHz-2.2GHz, 2.7GHz-3.2GHz")
        self.il, self.rl, self.rip, self.rej = (QDoubleSpinBox() for _ in range(4))
        for w, v in ((self.il, 2.0), (self.rl, 10.0), (self.rip, 1.0), (self.rej, 20.0)):
            w.setRange(0, 200)
            w.setDecimals(2)
            w.setSuffix(" dB")
            w.setValue(v)
        self.ports = QLineEdit("1, 2")
        f.addRow("Passband (start, stop)", self.pb)
        f.addRow("Insertion loss <=", self.il)
        f.addRow("Return loss >=", self.rl)
        f.addRow("Ripple <=", self.rip)
        f.addRow("Stopbands (start-stop, ...)", self.sb)
        f.addRow("Rejection >=", self.rej)
        f.addRow("Ports (in, out)", self.ports)
        self.replace = QCheckBox("Replace the existing goals")
        self.replace.setChecked(True)
        f.addRow(self.replace)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def goals(self) -> list[dict]:
        a, b = [p.strip() for p in self.pb.text().split(",")][:2]
        ports = [int(p) for p in self.ports.text().replace(";", ",").split(",")][:2]
        bands = [tuple(x.strip() for x in band.partition("-")[::2]) for band in self.sb.text().split(",")
                 if band.strip()]
        return filter_mask_goals((a, b), bands, self.il.value(), self.rl.value(), self.rip.value(),
                                 self.rej.value(), ports)


class OptimizationDialog(QDialog):
    """Setup Optimization: Goals, Variables and Optimizer tabs."""

    def __init__(self, parent, doc, op: Optimization, on_apply=None, original: Optimization | None = None):
        super().__init__(parent)
        self.doc, self.op, self.on_apply = doc, op, on_apply
        self.original = original                     # the entry op is a working copy of (Edit)
        des = doc.design
        self.setWindowTitle(f"Setup Optimization: {op.name}")
        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.name = QLineEdit(op.name)
        top.addRow("Name:", self.name)
        lay.addLayout(top)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ---- goals ----
        g = QWidget()
        gl = QVBoxLayout(g)
        f = QFormLayout()
        self.sol = QComboBox()
        sols = goal_solutions(des)
        self.sol.addItems(sols + ([op.solution] if op.solution and op.solution not in sols else []))
        self.sol.setCurrentText(op.solution)
        f.addRow("Solution:", self.sol)
        gl.addLayout(f)
        self._preview_timer = QTimer(self)               # redraw the preview once the editing pauses
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._preview)
        self.goals = QTableWidget(0, len(GOAL_COLUMNS))
        self.goals.setHorizontalHeaderLabels(GOAL_COLUMNS)
        self.goals.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.goals.verticalHeader().setVisible(False)
        self.goals.horizontalHeaderItem(MARGIN_COL).setToolTip(
            "Margin of the goal on the current solution: positive = reserve (green), negative = violated (red)")
        for row in op.goals:
            self._add_goal(row)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.goals)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.pfig = Figure(figsize=(6, 2.6), tight_layout=True)
        self.pax = self.pfig.add_subplot(111)
        self.pcanvas = FigureCanvasQTAgg(self.pfig)
        self.pcanvas.setToolTip("The goals as masks over the current solution of the chosen setup and sweep")
        split.addWidget(self.pcanvas)
        split.setStretchFactor(1, 1)
        gl.addWidget(split, 1)
        self.goals.itemChanged.connect(lambda *_: self._preview_timer.start())
        self.sol.currentTextChanged.connect(lambda *_: self._preview_timer.start())
        bl = QHBoxLayout()
        for txt, fn in (("Add Goal", lambda: self._add_goal()), ("Remove", self._remove_goal),
                        ("Filter Mask...", self._mask)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch(1)
        gl.addLayout(bl)
        hint = QLabel("Calculation: a report expression such as dB(S(2,1)), VSWR(1) or an output variable, or an "
                      "antenna parameter such as dB(PeakRealizedGain) or RadiationEfficiency (needs the fields of the "
                      "solution: LastAdaptive, or a sweep with Save fields, and a far field setup). Start / Stop: "
                      "frequency range of the goal. The cost is the weighted violation of all goals (0 = every goal "
                      "met).")
        hint.setWordWrap(True)
        gl.addWidget(hint)
        tabs.addTab(g, "Goals")

        # ---- variables ----
        v = QWidget()
        vl = QVBoxLayout(v)
        self.vars = QTableWidget(0, 5)
        self.vars.setHorizontalHeaderLabels(["Include", "Variable", "Starting value", "Min", "Max"])
        self.vars.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.vars.verticalHeader().setVisible(False)
        rows = {r["name"]: r for r in op.variables}
        for name, val in des.variables.items():
            r = self.vars.rowCount()
            self.vars.insertRow(r)
            sv = split_value(val)
            row = rows.get(name)
            if row is None and sv is not None:
                d = abs(sv[0]) * 0.1 or 1.0
                row = {"name": name, "include": False, "min": fmt_value(sv[0] - d, sv[1]),
                       "max": fmt_value(sv[0] + d, sv[1])}
            inc = QTableWidgetItem()
            inc.setFlags((Qt.ItemIsUserCheckable | Qt.ItemIsEnabled) if sv is not None else Qt.NoItemFlags)
            inc.setCheckState(Qt.Checked if row and row.get("include") and sv is not None else Qt.Unchecked)
            self.vars.setItem(r, 0, inc)
            for c, txt in ((1, name), (2, val)):
                it = QTableWidgetItem(txt)
                it.setFlags(Qt.ItemIsEnabled)
                self.vars.setItem(r, c, it)
            for c, k in ((3, "min"), (4, "max")):
                it = QTableWidgetItem(row.get(k, "") if row else "")
                if sv is None:
                    it.setFlags(Qt.NoItemFlags)
                    it.setToolTip("An expression cannot be optimized")
                self.vars.setItem(r, c, it)
        vl.addWidget(self.vars)
        lab = QLabel("Design variables with a number (and unit) can be optimized; the optimizer starts at the "
                     "current value and stays between Min and Max." if des.variables else
                     "The design has no variables. Define them in Design Properties and use them in the model.")
        lab.setWordWrap(True)
        vl.addWidget(lab)
        tabs.addTab(v, "Variables")

        # ---- optimizer ----
        self.values = {**schema.defaults(schema.OPTIMIZATION), **op.props}
        self.grid = TabbedPropertyGrid(self)
        self.grid.set(schema.OPTIMIZATION, self.values, doc.evaluator())
        tabs.addTab(self.grid, "Optimizer")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        bb.button(QDialogButtonBox.Apply).setEnabled(on_apply is not None)
        lay.addWidget(bb)
        self.resize(860, 680)
        self._preview()

    def _add_goal(self, row: dict | None = None) -> None:
        row = row or {"calc": "dB(S(1,1))", "start": "", "stop": "", "cond": "<=", "value": "-10", "weight": "1"}
        if not row.get("start"):
            try:
                from ..solver.bridge import sweep_frequencies
                setup, _, sw = [x.strip() for x in self.sol.currentText().partition(":")]
                st = self.doc.design.setup(setup)
                swp = next(s for s in st.sweeps if s.name == sw)
                fr = sweep_frequencies(swp.props, self.doc.evaluator())
                row = {**row, "start": f"{fr.min() / 1e9:.6g}GHz", "stop": f"{fr.max() / 1e9:.6g}GHz"}
            except Exception:  # noqa: BLE001
                row = {**row, "start": "1GHz", "stop": "2GHz"}
        r = self.goals.rowCount()
        self.goals.insertRow(r)
        for c, k in enumerate(("calc", "start", "stop")):
            self.goals.setItem(r, c, QTableWidgetItem(str(row.get(k, ""))))
        cond = QComboBox()
        cond.addItems(schema.GOAL_CONDITIONS)
        cond.setCurrentText(row.get("cond", "<="))
        cond.currentTextChanged.connect(lambda *_: self._preview_timer.start())
        self.goals.setCellWidget(r, 3, cond)
        self.goals.setItem(r, 4, QTableWidgetItem(str(row.get("value", "0"))))
        self.goals.setItem(r, 5, QTableWidgetItem(str(row.get("weight", "1"))))
        margin = QTableWidgetItem("")
        margin.setFlags(Qt.ItemIsEnabled)
        self.goals.setItem(r, MARGIN_COL, margin)
        self._preview_timer.start()

    def _remove_goal(self) -> None:
        for r in sorted({i.row() for i in self.goals.selectedIndexes()}, reverse=True):
            self.goals.removeRow(r)
        self._preview_timer.start()

    def _preview(self) -> None:
        """The goals over the current solution: every calculation as a curve, every goal as a line over its
        range in the colour of its calculation (the forbidden side shaded), and its margin in the table."""
        from ..solver.expressions import eval_expr, output_variables
        from ..solver.optimetrics import antenna_data, goal_margins, goal_range, uses_antenna
        from .reports import load_solution
        ax = self.pax
        ax.clear()
        ev = self.doc.evaluator()
        outvars = output_variables(self.doc.design)
        rows = [r for r in range(self.goals.rowCount())
                if self.goals.item(r, 0) is not None and self.goals.item(r, 0).text().strip()]
        goals = self._goal_rows()
        data = st = None
        try:
            st, sol, data = load_solution(self.doc, self.sol.currentText())
        except Exception:  # noqa: BLE001
            data = None
        if data is not None and not len(data.get("freq", [])):
            data = None
        antenna = None
        if data is not None and any(uses_antenna(g["calc"], outvars) for g in goals):
            try:
                antenna = antenna_data(st, self.doc.design, sol, ev)
            except Exception as e:  # noqa: BLE001
                antenna = str(e)
        margins = goal_margins(goals, data, ev, outvars, antenna) if data is not None else [{} for _ in goals]
        colors: dict[str, str] = {}
        for g in goals:
            colors.setdefault(g["calc"].replace(" ", ""), f"C{len(colors) % 10}")
        if data is not None:
            for g in goals:
                key = g["calc"].replace(" ", "")
                if key in {line.get_gid() for line in ax.lines}:
                    continue
                d = data
                if uses_antenna(g["calc"], outvars):
                    if not isinstance(antenna, dict):
                        continue
                    d = antenna
                f = np.asarray(d["freq"], float)
                order = np.argsort(f)
                try:
                    y = eval_expr(d, g["calc"], outvars)
                    y = np.broadcast_to(np.real(y) if np.iscomplexobj(y) else np.asarray(y, float), f.shape)
                except Exception:  # noqa: BLE001
                    continue
                ax.plot(f[order] / 1e9, y[order], "-" if len(f) > 1 else "o", color=colors[key], lw=1.6,
                        label=g["calc"], gid=key)
        for g in goals:
            try:
                lo, hi = goal_range(g, ev)
                value = float(g["value"])
            except Exception:  # noqa: BLE001
                continue
            if str(g.get("cond", "<=")).startswith("Ripple"):
                continue
            c = colors[g["calc"].replace(" ", "")]
            x0, x1 = lo / 1e9, hi / 1e9
            if x1 - x0 < 1e-9:
                ax.plot([x0], [value], "v" if g["cond"] == "<=" else "^" if g["cond"] == ">=" else "D", color=c,
                        ms=8)
            else:
                ax.plot([x0, x1], [value, value], "-", color=c, lw=2.6, alpha=0.75, solid_capstyle="butt")
            if g["cond"] in ("<=", ">=") and x1 > x0:
                keep = ax.dataLim.frozen()              # the shading does not count for the y range
                ax.fill_between([x0, x1], value, value + (1e6 if g["cond"] == "<=" else -1e6), color=c, alpha=0.07,
                                lw=0)
                ax.dataLim.set_points(keep.get_points())
        ax.autoscale_view()
        ax.grid(True, alpha=0.3)
        ax.set_xlabel("Frequency [GHz]")
        if data is None:
            ax.set_title(f"{self.sol.currentText()} is not solved yet: the goals without the response", fontsize=9,
                         color="#777")
        elif ax.lines and any(line.get_gid() for line in ax.lines):
            ax.legend(loc="best", fontsize=8)
        self.pcanvas.draw_idle()
        self.goals.blockSignals(True)
        for r, m in zip(rows, margins):
            it = self.goals.item(r, MARGIN_COL)
            if it is None:
                continue
            if "margin" in m:
                it.setText(f"{m['margin']:+.3g}")
                it.setForeground(Qt.darkGreen if m["margin"] >= 0 else Qt.red)
                it.setToolTip("met" if m["margin"] >= 0 else "violated")
            else:
                it.setText("–" if not m else "?")
                it.setForeground(Qt.gray)
                it.setToolTip(m.get("error", "no solution"))
        self.goals.blockSignals(False)

    def _mask(self) -> None:
        dlg = FilterMaskDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            rows = dlg.goals()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Filter Mask", f"Check the entries: {e}")
            return
        if dlg.replace.isChecked():
            self.goals.setRowCount(0)
        for row in rows:
            self._add_goal(row)

    def _goal_rows(self) -> list[dict]:
        out = []
        for r in range(self.goals.rowCount()):
            def t(c):
                it = self.goals.item(r, c)
                return it.text().strip() if it else ""
            if not t(0):
                continue
            out.append({"calc": t(0), "start": t(1), "stop": t(2) or t(1),
                        "cond": self.goals.cellWidget(r, 3).currentText(), "value": t(4) or "0", "weight": t(5) or "1"})
        return out

    def _var_rows(self) -> list[dict]:
        out = []
        for r in range(self.vars.rowCount()):
            if not (self.vars.item(r, 3).flags() & Qt.ItemIsEnabled):
                continue
            out.append({"name": self.vars.item(r, 1).text(),
                        "include": self.vars.item(r, 0).checkState() == Qt.Checked,
                        "min": self.vars.item(r, 3).text().strip(), "max": self.vars.item(r, 4).text().strip()})
        return out

    def _ok(self) -> None:
        if self._store():
            self.accept()

    def _apply(self) -> None:
        """Apply: store the settings in the design and keep the dialog open."""
        if self._store() and self.on_apply is not None:
            self.on_apply(self.op)

    def _store(self) -> bool:
        from ..solver.optimetrics import Variables
        ev = self.doc.evaluator()
        goals = self._goal_rows()
        try:
            for g in goals:
                ev.freq(g["start"])
                ev.freq(g["stop"])
                float(g["value"])
                float(g["weight"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Optimization", f"Goal '{g['calc']}': {e}")
            return False
        name = self.name.text().strip()
        others = [o for o in self.doc.design.optimetrics if o is not self.op and o is not self.original]
        if not name or any(o.name == name for o in others):
            QMessageBox.warning(self, "Optimization", "The name is empty or already used.")
            return False
        trial = Optimization(name, self.sol.currentText(), self._var_rows(), goals, dict(self.values))
        if any(v["include"] for v in trial.variables):
            try:
                Variables(self.doc.design, trial, ev)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Optimization", str(e))
                return False
        self.op.name, self.op.solution = name, trial.solution
        self.op.variables, self.op.goals, self.op.props = trial.variables, goals, trial.props
        return True


def read_history(folder) -> tuple[list[str], list[dict], dict | None]:
    try:
        h = json.loads((folder / "history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        h = {}
    try:
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = None
    return h.get("variables", []), h.get("evaluations", []), summary


class OptimizationResults(QDialog):
    """Cost of every evaluation (table and plot); Apply sets the design variables of the chosen evaluation."""

    def __init__(self, win, name: str, folder):
        super().__init__(win)
        self.win, self.name, self.folder = win, name, folder
        self.setWindowTitle(f"Optimization Results: {name}")
        lay = QVBoxLayout(self)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        split = QSplitter(Qt.Vertical)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(6, 2.6), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        split.addWidget(self.canvas)
        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        split.addWidget(self.table)
        lay.addWidget(split, 1)
        bl = QHBoxLayout()
        self.b_best = QPushButton("Apply Best to Design")
        self.b_sel = QPushButton("Apply Selected to Design")
        close = QPushButton("Close")
        for b in (self.b_best, self.b_sel):
            bl.addWidget(b)
        bl.addStretch(1)
        bl.addWidget(close)
        lay.addLayout(bl)
        self.b_best.clicked.connect(lambda: self._apply(None))
        self.b_sel.clicked.connect(self._apply_selected)
        close.clicked.connect(self.close)
        self._n = -1
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.resize(900, 640)
        self.refresh()

    def refresh(self) -> None:
        names, ev, summary = read_history(self.folder)
        if len(ev) == self._n and summary is None:
            return
        self._n = len(ev)
        ok = [e for e in ev if e.get("cost") is not None]
        best = min(ok, key=lambda e: e["cost"]) if ok else None
        self.best = best
        txt = f"{len(ev)} evaluation(s)"
        if best:
            txt += f"; best cost {best['cost']:.6g} (evaluation {best['eval']}): " + \
                   ", ".join(f"{k} = {v}" for k, v in best["variables"].items())
        if summary:
            txt += f".  Finished: {summary.get('reason', '')}"
        self.info.setText(txt)
        cols = ["Evaluation", "Step", "Cost"] + names
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(ev))
        for r, e in enumerate(ev):
            vals = [str(e["eval"]), e.get("phase", ""),
                    f"{e['cost']:.6g}" if e.get("cost") is not None else f"failed: {e.get('error', '')}"]
            vals += [e.get("variables", {}).get(n, "") for n in names]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if best is not None and e is best:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        self.ax.clear()
        if ok:
            x = [e["eval"] for e in ok]
            y = [e["cost"] for e in ok]
            self.ax.plot(x, y, "o", ms=3, color="#7f9cc0", label="Evaluation")
            self.ax.plot(x, np.minimum.accumulate(y), "-", color="#c0392b", lw=1.6, label="Best so far")
            if min(y) > 0 and max(y) / max(min(y), 1e-300) > 100:
                self.ax.set_yscale("log")
            self.ax.legend(loc="upper right", fontsize=8)
        self.ax.set_xlabel("Evaluation")
        self.ax.set_ylabel("Cost")
        self.ax.grid(True, alpha=0.3)
        self.canvas.draw_idle()
        self.b_best.setEnabled(best is not None)

    def _apply_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        names, ev, _ = read_history(self.folder)
        if rows[0] < len(ev) and ev[rows[0]].get("cost") is not None:
            self._apply(ev[rows[0]])

    def _apply(self, e: dict | None) -> None:
        e = e or self.best
        if not e:
            return
        self.win.apply_variables(e["variables"], f"evaluation {e['eval']} of {self.name}")


# ---------------------------------------------------------------------------------------------
# Optimization > Parametric
# ---------------------------------------------------------------------------------------------
def new_parametric(doc) -> "ParametricSetup":
    from ..model.design import ParametricSetup
    des = doc.design
    names = {o.name for o in des.optimetrics} | {o.name for o in des.parametrics}
    i = 1
    while f"ParametricSetup{i}" in names:
        i += 1
    return ParametricSetup(f"ParametricSetup{i}", setups=[s.name for s in des.setups])


class SweepEntryDialog(QDialog):
    """Add/Edit Sweep: one variable with Single value, Linear step / count, Decade / Octave / Exponential count."""

    def __init__(self, parent, variables: dict[str, str], entry: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add/Edit Sweep")
        self.variables = variables
        f = QFormLayout(self)
        self.var = QComboBox()
        self.var.addItems(list(variables))
        self.type = QComboBox()
        self.type.addItems(schema.PARAMETRIC_SWEEP_TYPES)
        self.start, self.stop, self.step = QLineEdit(), QLineEdit(), QLineEdit()
        self.step_label = QLabel("Step:")
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        f.addRow("Variable:", self.var)
        f.addRow("Type:", self.type)
        f.addRow("Start:", self.start)
        f.addRow("Stop:", self.stop)
        f.addRow(self.step_label, self.step)
        f.addRow("Values:", self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        e = entry or {}
        if e.get("variable") in variables:
            self.var.setCurrentText(e["variable"])
        self.type.setCurrentText(e.get("type", "Linear step"))
        self._fill(e)
        self.var.currentTextChanged.connect(lambda *_: self._fill({}))
        self.type.currentTextChanged.connect(self._type_changed)
        for w in (self.start, self.stop, self.step):
            w.textChanged.connect(self._update)
        self._type_changed()
        self.resize(420, 0)

    def _fill(self, e: dict) -> None:
        sv = split_value(self.variables.get(self.var.currentText(), ""))
        v, unit = sv if sv else (1.0, "")
        self.start.setText(e.get("start") or fmt_value(v * 0.9, unit))
        self.stop.setText(e.get("stop") or fmt_value(v * 1.1, unit))
        self.step.setText(e.get("step") or fmt_value(v * 0.05, unit))

    def _type_changed(self, *_):
        t = self.type.currentText()
        single = t == "Single value"
        self.stop.setEnabled(not single)
        self.step.setEnabled(not single)
        if t == "Linear step":
            self.step_label.setText("Step:")
            a, b, st = (split_value(w.text()) for w in (self.start, self.stop, self.step))
            if a and b and st and a[1] and not st[1]:            # a count from another type: a step in unit
                self.step.setText(fmt_value(abs(b[0] - a[0]) / 4 or 1.0, a[1]))
        else:
            self.step_label.setText({"Linear count": "Count:", "Exponential count": "Count:"}.get(
                t, "Points per decade:" if t == "Decade count" else "Points per octave:"))
            sv = split_value(self.step.text())
            if not sv or sv[1] or sv[0] != int(sv[0]):
                self.step.setText("5")
        self._update()

    def entry(self) -> dict:
        return {"variable": self.var.currentText(), "type": self.type.currentText(), "start": self.start.text().strip(),
                "stop": self.stop.text().strip(), "step": self.step.text().strip()}

    def _update(self, *_):
        from ..solver.parametric import entry_values
        try:
            vals = entry_values(self.entry())
            self.preview.setText(f"{len(vals)}: " + ", ".join(vals[:12]) + (" ..." if len(vals) > 12 else ""))
        except Exception as e:  # noqa: BLE001
            self.preview.setText(f"<span style='color:#c0392b'>{e}</span>")

    def _ok(self) -> None:
        from ..solver.parametric import entry_values
        try:
            if not self.var.currentText():
                raise ValueError("The design has no variable with a value")
            n = len(entry_values(self.entry()))
            if n > 1000:
                raise ValueError(f"{n} values: use a larger step")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Add/Edit Sweep", str(e))
            return
        self.accept()


class ParametricDialog(QDialog):
    """Setup Sweep Analysis: Sweep Definitions, Table, General, Calculations and Options tabs."""

    CALC_COLUMNS = ["Calculation", "Solution", "Function", "Start", "Stop"]

    def __init__(self, parent, doc, ps, on_apply=None, original=None):
        super().__init__(parent)
        from ..solver.parametric import sweepable
        self.doc, self.ps, self.on_apply, self.original = doc, ps, on_apply, original
        des = doc.design
        self.variables = sweepable(doc.project, des)
        self.sweeps = [dict(e) for e in ps.sweeps]
        self.setWindowTitle(f"Setup Sweep Analysis: {ps.name}")
        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.name = QLineEdit(ps.name)
        top.addRow("Name:", self.name)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # ---- sweep definitions ----
        w = QWidget()
        wl = QVBoxLayout(w)
        self.defs = QTableWidget(0, 3)
        self.defs.setHorizontalHeaderLabels(["Sync #", "Variable", "Description"])
        self.defs.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.defs.verticalHeader().setVisible(False)
        self.defs.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.defs.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.defs.doubleClicked.connect(lambda *_: self._edit())
        wl.addWidget(self.defs)
        bl = QHBoxLayout()
        for txt, fn in (("Add...", self._add), ("Edit...", self._edit), ("Delete", self._delete),
                        ("Sync", self._sync), ("Unsync", self._unsync), ("Up", lambda: self._move(-1)),
                        ("Down", lambda: self._move(1))):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch(1)
        wl.addLayout(bl)
        hint = QLabel("Every combination of the variables is solved. Variables with the same Sync # change "
                      "together (they need the same number of values). Several definitions of one variable "
                      "are joined." if self.variables else
                      "The design has no variable with a value. Define variables in Design Properties and "
                      "use them in the model.")
        hint.setWordWrap(True)
        wl.addWidget(hint)
        self.tabs.addTab(w, "Sweep Definitions")

        # ---- table ----
        t = QWidget()
        tl = QVBoxLayout(t)
        self.count = QLabel()
        tl.addWidget(self.count)
        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tl.addWidget(self.table)
        self.tabs.addTab(t, "Table")

        # ---- general ----
        g = QWidget()
        gl = QVBoxLayout(g)
        gl.addWidget(QLabel("Setups solved for every variation:"))
        self.setups = QTableWidget(0, 2)
        self.setups.setHorizontalHeaderLabels(["Simulate", "Setup"])
        self.setups.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.setups.verticalHeader().setVisible(False)
        for s in des.setups:
            r = self.setups.rowCount()
            self.setups.insertRow(r)
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if s.name in ps.setups else Qt.Unchecked)
            self.setups.setItem(r, 0, it)
            nm = QTableWidgetItem(s.name)
            nm.setFlags(Qt.ItemIsEnabled)
            self.setups.setItem(r, 1, nm)
        gl.addWidget(self.setups)
        self.tabs.addTab(g, "General")

        # ---- calculations ----
        c = QWidget()
        cl = QVBoxLayout(c)
        self.calcs = QTableWidget(0, len(self.CALC_COLUMNS))
        self.calcs.setHorizontalHeaderLabels(self.CALC_COLUMNS)
        self.calcs.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.calcs.verticalHeader().setVisible(False)
        self.calcs.setColumnWidth(1, 190)
        for row in ps.calcs:
            self._add_calc(row)
        cl.addWidget(self.calcs)
        bl = QHBoxLayout()
        for txt, fn in (("Add Calculation", lambda: self._add_calc()), ("Remove", self._remove_calc)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch(1)
        cl.addLayout(bl)
        hint = QLabel("Calculation: a report expression such as dB(S(1,1)). Function: Value at Start, or Max / "
                      "Min / Mean / Peak to Peak between Start and Stop (empty = whole sweep). The results are "
                      "in the table of View Results and can be plotted over a variable.")
        hint.setWordWrap(True)
        cl.addWidget(hint)
        self.tabs.addTab(c, "Calculations")

        # ---- options ----
        self.values = {**schema.defaults(schema.PARAMETRIC), **ps.props}
        self.grid = TabbedPropertyGrid(self)
        self.grid.set(schema.PARAMETRIC, self.values, doc.evaluator())
        self.tabs.addTab(self.grid, "Options")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        bb.button(QDialogButtonBox.Apply).setEnabled(on_apply is not None)
        lay.addWidget(bb)
        self.tabs.currentChanged.connect(lambda *_: self._fill_table())
        self._fill_defs()
        self.resize(760, 500)

    # ---- sweep definitions ----
    def _fill_defs(self) -> None:
        from ..solver.parametric import describe
        self.defs.setRowCount(len(self.sweeps))
        for r, e in enumerate(self.sweeps):
            for c, v in enumerate((str(e.get("sync", "") or ""), e["variable"], describe(e))):
                self.defs.setItem(r, c, QTableWidgetItem(v))
        self._fill_table()

    def _rows(self) -> list[int]:
        return sorted({i.row() for i in self.defs.selectedIndexes()})

    def _add(self) -> None:
        if not self.variables:
            QMessageBox.information(self, "Add Sweep", "The design has no variable with a value.")
            return
        dlg = SweepEntryDialog(self, self.variables)
        if dlg.exec() == QDialog.Accepted:
            self.sweeps.append(dlg.entry())
            self._fill_defs()

    def _edit(self) -> None:
        rows = self._rows()
        if not rows:
            return
        e = self.sweeps[rows[0]]
        dlg = SweepEntryDialog(self, self.variables, e)
        if dlg.exec() == QDialog.Accepted:
            new = dlg.entry()
            if new["variable"] == e["variable"] and e.get("sync"):
                new["sync"] = e["sync"]
            self.sweeps[rows[0]] = new
            self._fill_defs()

    def _delete(self) -> None:
        for r in reversed(self._rows()):
            self.sweeps.pop(r)
        self._fill_defs()

    def _sync(self) -> None:
        names = {self.sweeps[r]["variable"] for r in self._rows()}
        if len(names) < 2:
            QMessageBox.information(self, "Sync", "Select the definitions of at least two variables.")
            return
        used = {int(e["sync"]) for e in self.sweeps if str(e.get("sync", "")).isdigit()}
        n = next(i for i in range(1, len(used) + 2) if i not in used)
        for e in self.sweeps:
            if e["variable"] in names:
                e["sync"] = str(n)
        self._fill_defs()

    def _unsync(self) -> None:
        names = {self.sweeps[r]["variable"] for r in self._rows()}
        for e in self.sweeps:
            if e["variable"] in names:
                e.pop("sync", None)
        self._fill_defs()

    def _move(self, d: int) -> None:
        rows = self._rows()
        if len(rows) != 1 or not 0 <= rows[0] + d < len(self.sweeps):
            return
        r = rows[0]
        self.sweeps[r], self.sweeps[r + d] = self.sweeps[r + d], self.sweeps[r]
        self._fill_defs()
        self.defs.selectRow(r + d)

    def _trial(self):
        from ..model.design import ParametricSetup
        return ParametricSetup(self.name.text().strip(), [dict(e) for e in self.sweeps])

    def _fill_table(self) -> None:
        from ..solver.parametric import variation_table
        try:
            rows = variation_table(self._trial(), self.doc.evaluator())
        except Exception as e:  # noqa: BLE001
            self.count.setText(f"<span style='color:#c0392b'>{e}</span>")
            self.table.setRowCount(0)
            return
        names = list(rows[0]) if rows else []
        self.count.setText(f"{len(rows)} variation(s)")
        self.table.setColumnCount(len(names))
        self.table.setHorizontalHeaderLabels(names)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, n in enumerate(names):
                self.table.setItem(r, c, QTableWidgetItem(row[n]))
        self.table.resizeColumnsToContents()

    # ---- calculations ----
    def _add_calc(self, row: dict | None = None) -> None:
        sols = goal_solutions(self.doc.design)
        row = row or {"calc": "dB(S(1,1))", "solution": sols[0] if sols else "", "func": "Min", "start": "", "stop": ""}
        r = self.calcs.rowCount()
        self.calcs.insertRow(r)
        self.calcs.setItem(r, 0, QTableWidgetItem(row.get("calc", "")))
        sol = QComboBox()
        sol.addItems(sols + ([row["solution"]] if row.get("solution") and row["solution"] not in sols else []))
        sol.setCurrentText(row.get("solution", ""))
        self.calcs.setCellWidget(r, 1, sol)
        fn = QComboBox()
        fn.addItems(schema.CALC_FUNCTIONS)
        fn.setCurrentText(row.get("func", "Value"))
        self.calcs.setCellWidget(r, 2, fn)
        self.calcs.setItem(r, 3, QTableWidgetItem(row.get("start", "")))
        self.calcs.setItem(r, 4, QTableWidgetItem(row.get("stop", "")))

    def _remove_calc(self) -> None:
        for r in sorted({i.row() for i in self.calcs.selectedIndexes()}, reverse=True):
            self.calcs.removeRow(r)

    def _calc_rows(self) -> list[dict]:
        out = []
        for r in range(self.calcs.rowCount()):
            def t(c):
                it = self.calcs.item(r, c)
                return it.text().strip() if it else ""
            if not t(0):
                continue
            out.append({"calc": t(0), "solution": self.calcs.cellWidget(r, 1).currentText(),
                        "func": self.calcs.cellWidget(r, 2).currentText(), "start": t(3), "stop": t(4)})
        return out

    # ---- storing ----
    def _ok(self) -> None:
        if self._store():
            self.accept()

    def _apply(self) -> None:
        if self._store() and self.on_apply is not None:
            self.on_apply(self.ps)

    def _store(self) -> bool:
        from ..solver.parametric import variation_table
        des = self.doc.design
        ev = self.doc.evaluator()
        name = self.name.text().strip()
        others = [o.name for o in des.optimetrics] + \
                 [o.name for o in des.parametrics if o is not self.ps and o is not self.original]
        if not name or name in others:
            QMessageBox.warning(self, "Parametric", "The name is empty or already used.")
            return False
        try:
            rows = variation_table(self._trial(), ev)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Parametric", str(e))
            return False
        if len(rows) > 2000:
            QMessageBox.warning(self, "Parametric", f"{len(rows)} variations are too many.")
            return False
        calcs = self._calc_rows()
        try:
            for c in calcs:
                for k in ("start", "stop"):
                    if c[k]:
                        ev.freq(c[k])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Parametric", f"Calculation '{c['calc']}': {e}")
            return False
        setups = [self.setups.item(r, 1).text() for r in range(self.setups.rowCount())
                  if self.setups.item(r, 0).checkState() == Qt.Checked]
        self.ps.name, self.ps.sweeps, self.ps.setups = name, [dict(e) for e in self.sweeps], setups
        self.ps.calcs, self.ps.props = calcs, dict(self.values)
        return True


class ParametricResults(QDialog):
    """Optimization Results of a parametric setup: the table of the variations with their state and
    calculations, a plot of a calculation over a variable; Apply sets the design variables of a variation."""

    def __init__(self, win, name: str, folder):
        super().__init__(win)
        self.win, self.name, self.folder = win, name, folder
        self.setWindowTitle(f"Optimization Results: {name}")
        lay = QVBoxLayout(self)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        self.calc = QComboBox()
        self.xvar = QComboBox()
        row.addWidget(QLabel("Plot"))
        row.addWidget(self.calc, 1)
        row.addWidget(QLabel("over"))
        row.addWidget(self.xvar)
        row.addStretch(1)
        lay.addLayout(row)
        split = QSplitter(Qt.Vertical)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(6, 2.6), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        split.addWidget(self.canvas)
        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        split.addWidget(self.table)
        lay.addWidget(split, 1)
        bl = QHBoxLayout()
        self.b_sel = QPushButton("Show Selected Variation")
        self.b_sel.setToolTip("Set the design variables to the variation: geometry, fields and reports show its "
                              "solution without solving again")
        close = QPushButton("Close")
        bl.addWidget(self.b_sel)
        bl.addStretch(1)
        bl.addWidget(close)
        lay.addLayout(bl)
        self.b_sel.clicked.connect(self._apply_selected)
        close.clicked.connect(self.close)
        self.calc.currentTextChanged.connect(lambda *_: self._plot())
        self.xvar.currentTextChanged.connect(lambda *_: self._plot())
        self._stamp = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.resize(900, 640)
        self.refresh()

    def refresh(self) -> None:
        from ..solver.parametric import read_table
        f = self.folder / "table.json"
        try:
            stamp = f.stat().st_mtime
        except OSError:
            stamp = None
        if stamp == self._stamp and stamp is not None:
            return
        self._stamp = stamp
        self.t = read_table(self.folder)
        rows = self.t.get("variations", [])
        names, setups, calcs = self.t.get("variables", []), self.t.get("setups", []), self.t.get("calcs", [])
        states = [s for r in rows for s in r.get("state", {}).values()]
        from ..solver.results import run_status
        status = run_status(self.folder).get("state", "")
        self.info.setText(f"{len(rows)} variation(s): {states.count('done')} solved, {states.count('failed')} failed"
                          + (f", {states.count('pending') + states.count('running')} to go" if status == "running" else "")
                          + (f".  {status.capitalize()}" if status and status != "running" else "")
                          if rows else "Not analyzed yet")
        cols = ["#"] + names + [f"State {s}" if len(setups) > 1 else "State" for s in setups] + calcs
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [str(r + 1)] + [row["variables"].get(n, "") for n in names]
            vals += [row.get("state", {}).get(s, "") + (f": {row['error'][s]}" if row.get("error", {}).get(s) else "")
                     for s in setups]
            vals += ["" if row.get("calcs", {}).get(c) is None else f"{row['calcs'][c]:.6g}" for c in calcs]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        for box, items in ((self.calc, calcs), (self.xvar, names)):
            cur = box.currentText()
            box.blockSignals(True)
            box.clear()
            box.addItems(items)
            if cur in items:
                box.setCurrentText(cur)
            box.blockSignals(False)
        self._plot()

    def _plot(self) -> None:
        from ..solver.optimetrics import split_value as sv
        self.ax.clear()
        calc, xv = self.calc.currentText(), self.xvar.currentText()
        rows = [r for r in getattr(self, "t", {}).get("variations", [])
                if calc and xv and r.get("calcs", {}).get(calc) is not None]
        others = [n for n in self.t.get("variables", []) if n != xv] if rows else []
        fam: dict[tuple, list] = {}
        for r in rows:
            k = tuple(r["variables"].get(n, "") for n in others)
            x = sv(r["variables"].get(xv, ""))
            if x is not None:
                fam.setdefault(k, []).append((x[0], r["calcs"][calc]))
        for k, pts in fam.items():
            pts.sort()
            lab = " ".join(f"{n}='{v}'" for n, v in zip(others, k)) or calc
            self.ax.plot([p[0] for p in pts], [p[1] for p in pts], "o-", ms=3, label=lab)
        if fam:
            unit = (sv(rows[0]["variables"].get(xv, "")) or (0, ""))[1]
            self.ax.set_xlabel(f"{xv} [{unit}]" if unit else xv)
            self.ax.set_ylabel(calc)
            if 1 < len(fam) <= 12:
                self.ax.legend(loc="best", fontsize=8)
        else:
            self.ax.text(0.5, 0.5, "No calculation results yet (add calculations in the Calculations tab)",
                         ha="center", va="center", transform=self.ax.transAxes, color="#777")
        self.ax.grid(True, alpha=0.3)
        self.canvas.draw_idle()

    def _apply_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        vs = getattr(self, "t", {}).get("variations", [])
        if rows and rows[0] < len(vs):
            if hasattr(self.win, "show_variation"):
                self.win.show_variation(self.name, vs[rows[0]]["variables"])
            else:
                self.win.apply_variables(vs[rows[0]]["variables"], f"variation {rows[0] + 1} of {self.name}")
