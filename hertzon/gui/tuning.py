"""Derivative Tuning: sliders for design variables and the S-parameters of the tuned design, live.

The tuning model of a sweep (hertzon.solver.tuning) is computed once by the solver from the converged
adaptive mesh of the setup; moving a slider then only evaluates the small reduced model, so the plot
follows the slider without solving again. The solved response stays in the plot (dashed) as reference.
The response can also be plotted against one variable over its tuning range, at one frequency (a setup
without a sweep is tuned at its solution frequency).
"""
from __future__ import annotations

import html
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
                               QSlider, QSpinBox, QListWidget, QListWidgetItem, QGroupBox, QSplitter, QWidget,
                               QMessageBox, QDoubleSpinBox, QAbstractItemView)

from ..solver.optimetrics import split_value, fmt_value

STEPS = 1000                    # slider resolution over the tuning range
CURVE_POINTS = 101              # points of a curve over a variable
WARN_FRACTION = 0.6             # beyond this part of the range the accuracy drops noticeably
PATTERN = "pattern"             # x axis item of the radiation pattern
PATTERN_THETA = np.radians(np.arange(0.0, 180.1, 2.0))
PATTERN_PHI = np.radians([0.0, 90.0, 180.0, 270.0])
PATTERN_RANGE = 40.0            # dB shown below the peak of the pattern


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
        self.points.setToolTip("Frequencies of the tuning model of a fast sweep, or of a sweep not solved yet. "
                               "A discrete sweep uses its own points, an analyzed interpolating sweep the "
                               "frequencies it solved. Every point costs one matrix factorization.")
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
        xl = QGridLayout()
        self.xaxis = QComboBox()
        self.xaxis.setToolTip("Plot against frequency, or against one variable over its tuning range (the other "
                              "variables at their slider values), or the radiation pattern (models with an "
                              "open boundary)")
        self.fpick = QComboBox()
        self.fpick.setToolTip("Frequency of the plot against a variable")
        xl.addWidget(QLabel("X axis"), 0, 0)
        xl.addWidget(self.xaxis, 0, 1)
        self.fpick_label = QLabel("Frequency")
        xl.addWidget(self.fpick_label, 1, 0)
        xl.addWidget(self.fpick, 1, 1)
        tl.addLayout(xl)
        self.traces = QListWidget()
        self.traces.setSelectionMode(QAbstractItemView.NoSelection)
        tl.addWidget(self.traces)
        ll.addWidget(tr, 1)
        gb = QGroupBox("Goals")
        gl = QVBoxLayout(gb)
        self.goalset = QComboBox()
        self.goalset.setToolTip("Goals of an optimization: drawn as masks in the plot, with their margin at the "
                                "slider values (the optimizer works on the same goals)")
        self.goal_text = QLabel("")
        self.goal_text.setWordWrap(True)
        self.goal_text.setTextFormat(Qt.RichText)
        gl.addWidget(self.goalset)
        gl.addWidget(self.goal_text)
        ll.addWidget(gb)
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
        self.sweep.currentIndexChanged.connect(lambda *_: self.load())
        self.quantity.currentTextChanged.connect(lambda *_: self.plot())
        self.xaxis.currentIndexChanged.connect(lambda *_: self._xaxis_changed())
        self.fpick.currentIndexChanged.connect(lambda *_: self.plot())
        self.traces.itemChanged.connect(lambda *_: self.plot())
        self.goalset.currentIndexChanged.connect(lambda *_: self.plot())
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
        self._sig, self._dyn, self._bg = None, [], None
        self.canvas.mpl_connect("draw_event", self._on_draw)
        self.resize(1100, 680)
        self.load()

    # ---- model ---------------------------------------------------------------------------------
    def _fill_sweeps(self, want: str | None) -> None:
        from ..solver.tuning import ADAPTIVE
        self.sweep.blockSignals(True)
        self.sweep.clear()
        try:
            st = self.win.des.setup(self.setup.currentText())
            for sw in st.sweeps:
                self.sweep.addItem(sw.name, sw.name)
        except Exception:  # noqa: BLE001
            pass
        self.sweep.addItem("Solution frequency", ADAPTIVE)
        k = self.sweep.findData(want) if want else -1
        self.sweep.setCurrentIndex(max(k, 0))
        self.sweep.blockSignals(False)

    def sweep_name(self) -> str:
        return self.sweep.currentData() or ""

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
        if not doc.project.path or not self.setup.currentText() or not self.sweep_name():
            return None
        return tuning_path(results_dir(doc.project.path, doc.design_name, self.setup.currentText()),
                           self.sweep_name())

    def load(self) -> None:
        from ..solver.tuning import TuningModel, ADAPTIVE
        self.points.setEnabled(self.sweep_name() != ADAPTIVE)
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
            at = (f"{len(m.freq)} frequencies" if len(m.freq) > 1 else f"{m.freq[0] / 1e9:.6g} GHz")
            if m.grid is not None:
                at += f" (the solved points, interpolated over {len(m.grid)})"
            self.info.setText(f"Tuning model of {', '.join(m.variables)} at {at} "
                              f"({m.meta.get('tets', 0)} tetrahedra).{note}")
            self._fill_vars(m.variables)
        self._build_sliders()
        self._fill_traces()
        self._fill_axes()
        self._fill_goals()
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
        key = self.win.compute_tuning(self.setup.currentText(), self.sweep_name(), names,
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
    def _fill_axes(self) -> None:
        keep = self.xaxis.currentText()
        m = self.model
        self.xaxis.blockSignals(True)
        self.fpick.blockSignals(True)
        self.xaxis.clear()
        self.fpick.clear()
        if m is not None:
            if len(m.freq) > 1:
                self.xaxis.addItem("Frequency", None)
            for i, name in enumerate(m.variables):
                self.xaxis.addItem(name, i)
            if m.ff is not None:
                self.xaxis.addItem("Pattern (realized gain)", PATTERN)
            self.xaxis.setCurrentIndex(max(self.xaxis.findText(keep), 0))
            for f in m.freq:
                self.fpick.addItem(f"{f / 1e9:.6g} GHz")
            self.fpick.setCurrentIndex(len(m.freq) // 2)
        self.xaxis.blockSignals(False)
        self.fpick.blockSignals(False)
        self._show_fpick()

    def _show_fpick(self) -> None:
        on = self.model is not None and len(self.model.freq) > 1 and self.xaxis.currentData() is not None
        self.fpick.setVisible(on)
        self.fpick_label.setVisible(on)
        self.quantity.setEnabled(self.xaxis.currentData() != PATTERN)

    def _xaxis_changed(self) -> None:
        self._show_fpick()
        self.plot()

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

    def _on_draw(self, _event) -> None:
        """After a full draw: keep the background without the tuned curves, then draw them on it (slider
        moves only redraw the curves: blitting)."""
        self._bg = self.canvas.copy_from_bbox(self.ax.bbox)
        for line in self._dyn:
            self.ax.draw_artist(line)
        self.canvas.blit(self.ax.bbox)

    def _fast_update(self, m, dp: np.ndarray, sel: list) -> bool:
        """New tuned curves on the same axes, if they fit into the current y range."""
        if self._bg is None:
            return False
        if self.xaxis.currentData() == PATTERN:
            ys, text = self._pattern(m, dp, sel)
        else:
            S, _ = m.sparams(dp)
            ys, text = [self._y(S[:, i, j]) for (i, j), _ in sel], None
        if len(self._dyn) != len(ys) + (text is not None):
            return False
        lo, hi = self.ax.get_ylim()
        if any(np.nanmin(y) < lo or np.nanmax(y) > hi for y in ys if len(y)):
            return False
        self.canvas.restore_region(self._bg)
        for line, y in zip(self._dyn, ys):
            line.set_ydata(y)
            self.ax.draw_artist(line)
        if text is not None:
            self._dyn[-1].set_text(text)
            self.ax.draw_artist(self._dyn[-1])
        self.canvas.blit(self.ax.bbox)
        return True

    def plot(self) -> None:
        m = self.model
        dp = self.offsets() if m is not None else None
        sel = [(self.traces.item(k).data(Qt.UserRole), self.traces.item(k).text())
               for k in range(self.traces.count()) if self.traces.item(k).checkState() == Qt.Checked]
        sig = (id(m), self.xaxis.currentData(), self.quantity.currentText(), tuple(t for _, t in sel),
               self.fpick.currentIndex(), self.goalset.currentIndex())
        self._warn(dp)
        self._show_margins(m, dp)
        mode = self.xaxis.currentData()
        if m is not None and mode in (None, PATTERN) and sig == self._sig and self._fast_update(m, dp, sel):
            return
        self._sig = sig
        self._dyn = []
        self._bg = None
        self.ax.clear()
        if m is None:
            self.ax.text(0.5, 0.5, "No tuning model", ha="center", va="center", transform=self.ax.transAxes,
                         color="#777")
            self.canvas.draw_idle()
            return
        if mode is None:
            self._plot_frequency(m, dp, sel)
        elif mode == PATTERN:
            self._plot_pattern(m, dp, sel)
        else:
            self._plot_variable(m, dp, sel, int(mode))
        if mode != PATTERN:
            self.ax.set_ylabel(self.quantity.currentText())
        self.ax.grid(True, alpha=0.3)
        if sel or mode == PATTERN:
            self.ax.legend(loc="best", fontsize=8)
        self.canvas.draw_idle()

    def _warn(self, dp) -> None:
        if dp is None:
            self.warn.setText("")
            return
        far = [r["name"] for r, d in zip(self.rows, dp)
               if (d > 0 and r["hi"] > 0 and d > WARN_FRACTION * r["hi"])
               or (d < 0 and r["lo"] < 0 and d < WARN_FRACTION * r["lo"])]
        self.warn.setText(f"{', '.join(far)} far from the solved value: the accuracy decreases. Apply to Design "
                          "and analyze to confirm." if far else "")

    def _plot_frequency(self, m, dp: np.ndarray, sel: list) -> None:
        S0, _ = m.nominal()
        S, _ = m.sparams(dp)
        f = m.freq_out / 1e9
        for k, ((i, j), lab) in enumerate(sel):
            c = f"C{k % 10}"
            self.ax.plot(f, self._y(S0[:, i, j]), "--", color=c, alpha=0.5, lw=1.2, label=f"{lab} solved")
            line, = self.ax.plot(f, self._y(S[:, i, j]), "-", color=c, lw=1.8, label=f"{lab} tuned",
                                 animated=True)
            self._dyn.append(line)
        marks = self._masks(sel)
        for lo, hi, value, cond in marks:
            self._draw_mask(lo / 1e9, hi / 1e9, value, cond)
        # the y range with some room, so that the tuned curves can move without a full redraw
        self.ax.relim()
        ys = [self._y(S0[:, i, j]) for (i, j), _ in sel] + [self._y(S[:, i, j]) for (i, j), _ in sel] + \
            [np.array([v]) for _, _, v, _ in marks]
        if ys:
            lo, hi = float(min(np.nanmin(y) for y in ys)), float(max(np.nanmax(y) for y in ys))
            pad = 0.15 * max(hi - lo, 1e-6)
            self.ax.set_ylim(lo - pad, hi + pad)
        self.ax.set_xlabel("Frequency [GHz]")

    def _plot_variable(self, m, dp: np.ndarray, sel: list, v: int) -> None:
        """The response at one frequency against variable v over its tuning range: the curve, the slider
        value (filled marker), the solved design (open marker) and the ranges of lower accuracy (shaded)."""
        row = self.rows[v]
        k = max(self.fpick.currentIndex(), 0) if len(m.freq) > 1 else 0
        lo, hi, v0 = row["lo"], row["hi"], row["v0"]
        offs = np.linspace(lo, hi, CURVE_POINTS)
        S, _ = m.sweep_variable(k, v, np.r_[offs, dp[v]], dp)
        S0, _ = m.nominal()
        x = v0 + offs
        for kk, ((i, j), lab) in enumerate(sel):
            c = f"C{kk % 10}"
            self.ax.plot(x, self._y(S[:-1, i, j]), "-", color=c, lw=1.8, label=lab)
            self.ax.plot([v0 + dp[v]], self._y(S[-1:, i, j]), "o", color=c, ms=7)
            self.ax.plot([v0], self._y(S0[k:k + 1, i, j]), "o", mfc="none", mec=c, ms=8, mew=1.5)
        fk = float(m.freq[k])
        for glo, ghi, value, cond in self._masks(sel):
            if glo - 1e-6 * fk <= fk <= ghi + 1e-6 * fk:
                self._draw_mask(x[0], x[-1], value, cond)
        for a, b in ((lo, WARN_FRACTION * lo), (WARN_FRACTION * hi, hi)):
            if a != b:
                self.ax.axvspan(v0 + min(a, b), v0 + max(a, b), color="#b36b00", alpha=0.07, lw=0)
        self.ax.axvline(v0 + dp[v], color="#777", lw=0.8, alpha=0.6)
        unit = f" [{row['unit']}]" if row["unit"] else ""
        self.ax.set_xlabel(f"{row['name']}{unit}")
        others = [f"{r['name']} = {r['spin'].value():.4g}{(' ' + r['unit']) if r['unit'] else ''}"
                  for n, r in enumerate(self.rows) if n != v]
        self.ax.set_title(f"{m.freq[k] / 1e9:.6g} GHz" + (f"   ({', '.join(others)})" if others else "")
                          + "   ● slider  ○ solved", fontsize=9)

    # ---- goals ----------------------------------------------------------------------------------
    def _fill_goals(self) -> None:
        """The optimizations with goals; preselected: the one on this setup and sweep."""
        keep = self.goalset.currentText()
        des = self.win.des
        self.goalset.blockSignals(True)
        self.goalset.clear()
        self.goalset.addItem("(none)", None)
        want = f"{self.setup.currentText()} : {self.sweep_name()}"
        pick = 0
        for i, op in enumerate(getattr(des, "optimetrics", []) or []):
            if not any(str(g.get("calc", "")).strip() for g in op.goals):
                continue
            self.goalset.addItem(op.name, i)
            if op.name == keep or (not keep and pick == 0 and op.solution.replace(" ", "") == want.replace(" ", "")):
                pick = self.goalset.count() - 1
        self.goalset.setCurrentIndex(pick)
        self.goalset.blockSignals(False)
        self._goal_env = None

    def _goals(self) -> list[dict]:
        i = self.goalset.currentData()
        ops = getattr(self.win.des, "optimetrics", []) or []
        if i is None or i >= len(ops):
            return []
        return [g for g in ops[i].goals if str(g.get("calc", "")).strip()]

    def _env(self):
        """(evaluator, output variables) of the design for the goals."""
        if getattr(self, "_goal_env", None) is None:
            from ..solver.expressions import output_variables
            des = self.win.des
            self._goal_env = (des.evaluator(None), output_variables(des))
        return self._goal_env

    def _show_margins(self, m, dp) -> None:
        """The margin of every goal at the slider values (green: met, red: violated)."""
        goals = self._goals()
        if m is None or not goals:
            self.goal_text.setText("" if m is None else "No goals: define them in an optimization.")
            return
        from ..solver.expressions import with_pairs
        from ..solver.optimetrics import goal_margins
        S, z = m.sparams(dp)
        data = with_pairs({"freq": m.freq_out, "S": S, "z0": z, "ports": m.meta.get("ports") or []}, self.win.des)
        ev, outvars = self._env()
        lines = []
        for r in goal_margins(goals, data, ev, outvars):
            g = r["goal"]
            stop = g.get("stop") or g["start"]
            span = f"{g['start']}" if str(stop) == str(g["start"]) else f"{g['start']} … {stop}"
            what = html.escape(f"{g['calc']} {g.get('cond', '<=')} {g.get('value', 0)} ({span})")
            if "error" in r:
                lines.append(f"<span style='color:#777'>{what}: {html.escape(r['error'])}</span>")
            elif r["margin"] >= 0:
                lines.append(f"<span style='color:#1a7f37'>&#10003; {what}: {r['margin']:.3g} margin</span>")
            else:
                lines.append(f"<span style='color:#c62828'>&#10007; {what}: violated by {-r['margin']:.3g}</span>")
        self.goal_text.setText("<br>".join(lines))

    def _masks(self, sel: list) -> list[tuple[float, float, float, str]]:
        """Goals drawn as masks: those on a plotted trace in dB, as (low Hz, high Hz, value, condition)."""
        goals = self._goals()
        if not goals or self.quantity.currentText() != "dB":
            return []
        exprs = {f"dB(S({i + 1},{j + 1}))" for (i, j), _ in sel} | {f"dB(S({j + 1},{i + 1}))" for (i, j), _ in sel}
        from ..solver.optimetrics import goal_range
        ev, _ = self._env()
        out = []
        for g in goals:
            cond = g.get("cond", "<=")
            if str(g["calc"]).replace(" ", "") not in exprs or cond not in ("<=", ">=", "="):
                continue
            try:
                lo, hi = goal_range(g, ev)
                out.append((lo, hi, float(g.get("value", 0)), cond))
            except Exception:  # noqa: BLE001
                continue
        return out

    def _draw_mask(self, x0: float, x1: float, value: float, cond: str) -> None:
        """A goal line from x0 to x1, the forbidden side shaded."""
        self.ax.plot([x0, x1], [value, value], "-", color="#c62828", lw=2.2, alpha=0.8, solid_capstyle="butt")
        if cond in ("<=", ">="):
            far = 1e6 if cond == "<=" else -1e6
            keep = self.ax.dataLim.frozen()             # the shading does not count for the y range
            self.ax.fill_between([x0, x1], value, value + far, color="#c62828", alpha=0.06, lw=0)
            self.ax.dataLim.set_points(keep.get_points())

    # ---- antenna parameters ---------------------------------------------------------------------
    def _excited(self, sel: list) -> int:
        """Port index of the pattern: the excited port (column) of the first checked trace."""
        return int(sel[0][0][1]) if sel else 0

    def _pattern(self, m, dp: np.ndarray, sel: list) -> tuple[list, str]:
        """Realized gain (dBi) over theta -180..180 deg of the cuts phi = 0 and 90 deg, and the antenna
        numbers, at the picked frequency."""
        k = max(self.fpick.currentIndex(), 0) if len(m.freq) > 1 else 0
        q = m.antenna(k, self._excited(sel), dp, PATTERN_THETA, PATTERN_PHI)
        rg = 10 * np.log10(np.maximum(q["RealizedGainTotal"], 1e-30))
        ys = [np.r_[rg[:0:-1, c + 2], rg[:, c]] for c in (0, 1)]     # phi + 180 deg as negative theta
        peak = max(float(y.max()) for y in ys)
        eff, pacc = float(q["_eff"][0]), float(q["_Pacc"][0])
        text = (f"peak realized gain on the cuts {peak:.2f} dBi   radiation efficiency {100 * eff:.1f} %   "
                f"total efficiency {100 * eff * pacc:.1f} %")
        return ys, text

    def _plot_pattern(self, m, dp: np.ndarray, sel: list) -> None:
        """Realized gain of the solved design (dashed) and at the slider values (solid) on two cuts, with the
        peak gain and the efficiencies of the slider values."""
        x = np.r_[-np.degrees(PATTERN_THETA[:0:-1]), np.degrees(PATTERN_THETA)]
        try:
            y0, _ = self._pattern(m, np.zeros(len(m.variables)), sel)
            ys, text = self._pattern(m, dp, sel)
        except Exception as ex:  # noqa: BLE001
            self.ax.text(0.5, 0.5, f"No far field: {ex}", ha="center", va="center", transform=self.ax.transAxes,
                         color="#777")
            return
        for c, name in enumerate(("phi = 0 deg", "phi = 90 deg")):
            self.ax.plot(x, y0[c], "--", color=f"C{c}", alpha=0.5, lw=1.2, label=f"{name} solved")
            line, = self.ax.plot(x, ys[c], "-", color=f"C{c}", lw=1.8, label=f"{name} tuned", animated=True)
            self._dyn.append(line)
        top = max(float(np.max(y)) for y in y0 + ys)
        self.ax.set_ylim(top - PATTERN_RANGE, top + 0.15 * PATTERN_RANGE)
        self.ax.set_xlim(-180, 180)
        self.ax.set_xticks(np.arange(-180, 181, 45))
        k = max(self.fpick.currentIndex(), 0) if len(m.freq) > 1 else 0
        labels = self._labels(m.nominal_raw.shape[1])
        self.ax.set_title(f"{m.freq[k] / 1e9:.6g} GHz, port {labels[self._excited(sel)]} excited", fontsize=9)
        self._dyn.append(self.ax.text(0.01, 0.01, text, transform=self.ax.transAxes, fontsize=8, va="bottom",
                                      animated=True))
        self.ax.set_xlabel("Theta [deg]")
        self.ax.set_ylabel("Realized gain [dBi]")

    def closeEvent(self, e) -> None:  # noqa: N802
        try:
            self.win.solver.finished.disconnect(self._solver_finished)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)
