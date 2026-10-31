"""Derivative Tuning: sliders for design variables and the S-parameters of the tuned design, live.

The tuning model of a sweep (hertzon.solver.tuning) is computed once by the solver from the converged
adaptive mesh of the setup; moving a slider then only evaluates the small reduced model, so the plot
follows the slider without solving again. The solved response stays in the plot (dashed) as reference.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
                               QSlider, QSpinBox, QListWidget, QListWidgetItem, QGroupBox, QSplitter, QWidget,
                               QMessageBox, QDoubleSpinBox, QAbstractItemView)

from ..solver.optimetrics import split_value, fmt_value

STEPS = 1000                    # slider resolution over the tuning range
WARN_FRACTION = 0.6             # beyond this part of the range the accuracy drops noticeably


def tunable_variables(design) -> list[str]:
    """Design variables with a plain value (number and unit)."""
    return [k for k, v in design.variables.items() if split_value(v) is not None]


class TuningDialog(QDialog):
    def __init__(self, win, setup: str | None = None, sweep: str | None = None):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Derivative Tuning")
        self.model = None
        self.rows: list[dict] = []
        lay = QVBoxLayout(self)

        # ---- what to tune ---------------------------------------------------------------------
        top = QGroupBox("Tuning model")
        g = QGridLayout(top)
        self.setup = QComboBox()
        self.sweep = QComboBox()
        self.points = QSpinBox()
        self.points.setRange(5, 201)
        self.points.setValue(51)
        self.points.setToolTip("Frequencies of the tuning model (a discrete sweep uses its own points). Every "
                               "point costs one matrix factorization.")
        self.vars = QListWidget()
        self.vars.setSelectionMode(QAbstractItemView.NoSelection)
        self.vars.setMaximumHeight(110)
        self.b_compute = QPushButton("Compute Derivatives")
        self.b_compute.setToolTip("Solve the derivatives of the checked variables on the adaptive mesh of the "
                                  "setup (the setup must be analyzed)")
        g.addWidget(QLabel("Setup"), 0, 0)
        g.addWidget(self.setup, 0, 1)
        g.addWidget(QLabel("Sweep"), 0, 2)
        g.addWidget(self.sweep, 0, 3)
        g.addWidget(QLabel("Points"), 0, 4)
        g.addWidget(self.points, 0, 5)
        g.addWidget(QLabel("Variables"), 1, 0, Qt.AlignTop)
        g.addWidget(self.vars, 1, 1, 1, 5)
        g.addWidget(self.b_compute, 2, 5)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        g.addWidget(self.info, 2, 0, 1, 5)
        lay.addWidget(top)

        split = QSplitter(Qt.Horizontal)
        # ---- sliders ----------------------------------------------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.slider_box = QGroupBox("Variables")
        self.slider_grid = QGridLayout(self.slider_box)
        ll.addWidget(self.slider_box)
        tr = QGroupBox("Traces")
        tl = QVBoxLayout(tr)
        self.quantity = QComboBox()
        self.quantity.addItems(["dB", "Magnitude", "Phase (deg)"])
        tl.addWidget(self.quantity)
        self.traces = QListWidget()
        self.traces.setSelectionMode(QAbstractItemView.NoSelection)
        tl.addWidget(self.traces)
        ll.addWidget(tr, 1)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #b36b00;")
        ll.addWidget(self.warn)
        split.addWidget(left)
        # ---- plot -------------------------------------------------------------------------------
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(6, 4), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        split.addWidget(self.canvas)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)

        bl = QHBoxLayout()
        self.b_reset = QPushButton("Reset All")
        self.b_apply = QPushButton("Apply to Design")
        self.b_apply.setToolTip("Set the design variables to the slider values (analyze again to confirm the "
                                "tuned response)")
        close = QPushButton("Close")
        bl.addWidget(self.b_reset)
        bl.addWidget(self.b_apply)
        bl.addStretch(1)
        bl.addWidget(close)
        lay.addLayout(bl)

        des = win.des
        self.setup.addItems([s.name for s in des.setups if not s.eigen])
        if setup:
            self.setup.setCurrentText(setup)
        self._fill_sweeps(sweep)
        self._fill_vars()
        self.setup.currentTextChanged.connect(lambda *_: (self._fill_sweeps(None), self.load()))
        self.sweep.currentTextChanged.connect(lambda *_: self.load())
        self.quantity.currentTextChanged.connect(lambda *_: self.plot())
        self.traces.itemChanged.connect(lambda *_: self.plot())
        self.b_compute.clicked.connect(self.compute)
        self.b_reset.clicked.connect(self.reset)
        self.b_apply.clicked.connect(self.apply)
        close.clicked.connect(self.close)
        self._pending = ""
        win.solver.finished.connect(self._solver_finished)
        self._timer = QTimer(self)                      # coalesce slider moves into one redraw
        self._timer.setSingleShot(True)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self.plot)
        self.resize(1100, 680)
        self.load()

    # ---- model ---------------------------------------------------------------------------------
    def _fill_sweeps(self, want: str | None) -> None:
        self.sweep.blockSignals(True)
        self.sweep.clear()
        try:
            st = self.win.des.setup(self.setup.currentText())
            self.sweep.addItems([s.name for s in st.sweeps])
        except Exception:  # noqa: BLE001
            pass
        if want:
            self.sweep.setCurrentText(want)
        self.sweep.blockSignals(False)

    def _fill_vars(self, checked: list[str] | None = None) -> None:
        self.vars.blockSignals(True)
        self.vars.clear()
        for name in tunable_variables(self.win.des):
            it = QListWidgetItem(f"{name} = {self.win.des.variables[name]}")
            it.setData(Qt.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if checked and name in checked else Qt.Unchecked)
            self.vars.addItem(it)
        self.vars.blockSignals(False)

    def _path(self) -> Path | None:
        from ..solver.results import results_dir
        from ..solver.tuning import tuning_path
        doc = self.win.doc
        if not doc.project.path or not self.setup.currentText() or not self.sweep.currentText():
            return None
        return tuning_path(results_dir(doc.project.path, doc.design_name, self.setup.currentText()),
                           self.sweep.currentText())

    def load(self) -> None:
        from ..solver.tuning import TuningModel
        p = self._path()
        self.model = None
        if p is not None and p.exists():
            try:
                self.model = TuningModel(p)
            except Exception as e:  # noqa: BLE001
                self.info.setText(f"The tuning model cannot be read ({e}); compute it again.")
        if self.model is None:
            if not self.info.text() or p is None or not p.exists():
                self.info.setText("No tuning model for this sweep yet: check the variables to tune and click "
                                  "Compute Derivatives.")
            self._fill_vars(self._checked())
        else:
            m = self.model
            from .trees import solution_state
            state = solution_state(self.win.doc, self.setup.currentText())
            note = ("  The design or setup changed since; the model shows the analyzed design."
                    if state == "outdated" else "")
            self.info.setText(f"Tuning model of {', '.join(m.variables)} at {len(m.freq)} frequencies "
                              f"({m.meta.get('tets', 0)} tetrahedra).{note}")
            self._fill_vars(m.variables)
        self._build_sliders()
        self._fill_traces()
        self.plot()

    def _checked(self) -> list[str]:
        return [self.vars.item(i).data(Qt.UserRole) for i in range(self.vars.count())
                if self.vars.item(i).checkState() == Qt.Checked]

    def compute(self) -> None:
        names = self._checked()
        if not names:
            QMessageBox.information(self, "Derivative Tuning", "Check the variables to tune.")
            return
        if len(names) > 6:
            QMessageBox.information(self, "Derivative Tuning", "Tune at most 6 variables at a time.")
            return
        key = self.win.compute_tuning(self.setup.currentText(), self.sweep.currentText(), names,
                                      self.points.value())
        if key:
            self._pending = key
            self.info.setText("Computing the derivatives... (progress in the Progress window)")

    def _solver_finished(self, key: str, ok: bool) -> None:
        if key != self._pending:
            return
        self._pending = ""
        if ok:
            self.load()
        else:
            self.info.setText("Computing the derivatives failed; the message window says why.")

    # ---- sliders -------------------------------------------------------------------------------
    def _build_sliders(self) -> None:
        while self.slider_grid.count():
            w = self.slider_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.rows = []
        m = self.model
        if m is None:
            self.slider_grid.addWidget(QLabel("(no tuning model)"), 0, 0)
            return
        for i, name in enumerate(m.variables):
            v0, unit = m.values[i], m.units[i]
            lo, hi = m.ranges[i]
            if v0 > 0:                       # a positive length stays positive
                lo = max(lo, -0.9 * v0)
            lab = QLabel(name)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, STEPS)
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(v0 + lo, v0 + hi)
            spin.setSuffix(f" {unit}" if unit else "")
            spin.setSingleStep(max((hi - lo) / 100, 1e-6))
            spin.setValue(v0)
            spin.setKeyboardTracking(False)
            rng = QLabel(f"{v0 + lo:.4g} … {v0 + hi:.4g}")
            rng.setStyleSheet("color: #777;")
            row = {"name": name, "v0": v0, "unit": unit, "lo": lo, "hi": hi, "slider": sl, "spin": spin}
            sl.setValue(self._pos(row, 0.0))
            sl.valueChanged.connect(lambda pos, r=row: self._slid(r, pos))
            spin.valueChanged.connect(lambda val, r=row: self._typed(r, val))
            self.slider_grid.addWidget(lab, 2 * i, 0)
            self.slider_grid.addWidget(spin, 2 * i, 1)
            self.slider_grid.addWidget(rng, 2 * i, 2)
            self.slider_grid.addWidget(sl, 2 * i + 1, 0, 1, 3)
            self.rows.append(row)

    @staticmethod
    def _pos(row: dict, dp: float) -> int:
        return int(round((dp - row["lo"]) / (row["hi"] - row["lo"]) * STEPS))

    def _slid(self, row: dict, pos: int) -> None:
        dp = row["lo"] + (row["hi"] - row["lo"]) * pos / STEPS
        if abs(dp) < 0.5 * (row["hi"] - row["lo"]) / STEPS:
            dp = 0.0                          # the solved value is reachable exactly
        row["spin"].blockSignals(True)
        row["spin"].setValue(row["v0"] + dp)
        row["spin"].blockSignals(False)
        self._timer.start()

    def _typed(self, row: dict, value: float) -> None:
        row["slider"].blockSignals(True)
        row["slider"].setValue(self._pos(row, value - row["v0"]))
        row["slider"].blockSignals(False)
        self._timer.start()

    def offsets(self) -> np.ndarray:
        return np.array([r["spin"].value() - r["v0"] for r in self.rows], float)

    def reset(self) -> None:
        for r in self.rows:
            r["spin"].setValue(r["v0"])

    def apply(self) -> None:
        if not self.rows:
            return
        vals = {r["name"]: fmt_value(r["spin"].value(), r["unit"]) for r in self.rows}
        self.win.apply_variables(vals, "derivative tuning")

    # ---- plot ----------------------------------------------------------------------------------
    def _fill_traces(self) -> None:
        keep = {self.traces.item(i).text() for i in range(self.traces.count())
                if self.traces.item(i).checkState() == Qt.Checked}
        self.traces.blockSignals(True)
        self.traces.clear()
        m = self.model
        if m is not None:
            n = m.nominal_raw.shape[1]
            labels = self._labels(n)
            for j in range(n):
                for i in range(n):
                    if i < j:
                        continue                  # reciprocal: S(i,j) = S(j,i)
                    t = f"S({labels[i]},{labels[j]})"
                    it = QListWidgetItem(t)
                    it.setData(Qt.UserRole, (i, j))
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    on = t in keep if keep else (i == j == 0 or (i == 1 and j == 0))
                    it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                    self.traces.addItem(it)
        self.traces.blockSignals(False)

    def _labels(self, n: int) -> list[str]:
        ports = (self.model.meta.get("ports") if self.model is not None else None) or []
        labels = [str(q.get("label", q.get("name", i + 1))) for i, q in enumerate(ports)][:n]
        return labels + [str(i + 1) for i in range(len(labels), n)]

    def _y(self, s: np.ndarray) -> np.ndarray:
        q = self.quantity.currentText()
        if q == "dB":
            return 20 * np.log10(np.maximum(np.abs(s), 1e-15))
        if q == "Magnitude":
            return np.abs(s)
        return np.degrees(np.angle(s))

    def plot(self) -> None:
        self.ax.clear()
        m = self.model
        if m is None:
            self.ax.text(0.5, 0.5, "No tuning model", ha="center", va="center", transform=self.ax.transAxes,
                         color="#777")
            self.canvas.draw_idle()
            return
        dp = self.offsets()
        S0, _ = m.nominal()
        S, _ = m.sparams(dp)
        f = m.freq / 1e9
        sel = [(self.traces.item(k).data(Qt.UserRole), self.traces.item(k).text())
               for k in range(self.traces.count()) if self.traces.item(k).checkState() == Qt.Checked]
        for k, ((i, j), lab) in enumerate(sel):
            c = f"C{k % 10}"
            self.ax.plot(f, self._y(S0[:, i, j]), "--", color=c, alpha=0.5, lw=1.2, label=f"{lab} solved")
            self.ax.plot(f, self._y(S[:, i, j]), "-", color=c, lw=1.8, label=f"{lab} tuned")
        self.ax.set_xlabel("Frequency [GHz]")
        self.ax.set_ylabel(self.quantity.currentText())
        self.ax.grid(True, alpha=0.3)
        if sel:
            self.ax.legend(loc="best", fontsize=8)
        self.canvas.draw_idle()
        far = [r["name"] for r, d in zip(self.rows, dp)
               if (d > 0 and r["hi"] > 0 and d > WARN_FRACTION * r["hi"])
               or (d < 0 and r["lo"] < 0 and d < WARN_FRACTION * r["lo"])]
        self.warn.setText(f"{', '.join(far)} far from the solved value: the accuracy decreases. Apply to Design "
                          "and analyze to confirm." if far else "")

    def closeEvent(self, e) -> None:  # noqa: N802
        try:
            self.win.solver.finished.disconnect(self._solver_finished)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)
