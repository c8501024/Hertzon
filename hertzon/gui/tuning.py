"""Derivative Tuning: sliders for design variables, the reports follow live.

The tuning model of a sweep (hertzon.solver.tuning) is computed once by the solver from the converged
adaptive mesh of the setup; moving a slider then only evaluates the small reduced model. The slider panel
(a dock) holds the state (TuningState); every open report on the tuned solution draws the tuned curves over
the solved ones (dashed), with all its quantities, markers and exports.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton, QSlider,
                               QSpinBox, QListWidget, QListWidgetItem, QGroupBox, QWidget, QMessageBox,
                               QDoubleSpinBox, QAbstractItemView, QScrollArea)

from ..solver.optimetrics import split_value, fmt_value

STEPS = 1000                    # slider resolution over the tuning range
WARN_FRACTION = 0.6             # beyond this part of the range the accuracy drops noticeably
TUNED = "<tuned>"               # the 'variation' of a report that shows the tuned design


def tunable_variables(design) -> list[str]:
    """Design variables with a plain value (number and unit)."""
    return [k for k, v in design.variables.items() if split_value(v) is not None]


def same_solution(a: str, b: str) -> bool:
    return a.replace(" ", "") == b.replace(" ", "")


def slider_range(model, i: int) -> tuple[float, float]:
    """(lo, hi) offsets of variable i of the tuning model: its range, a positive length stays positive."""
    v0 = model.values[i]
    lo, hi = model.ranges[i]
    if v0 > 0:
        lo = max(lo, -0.9 * v0)
    return float(lo), float(hi)


class TuningState(QObject):
    """The tuning model of one solution and the slider offsets: what the reports draw as tuned curves."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = None
        self.solution = ""              # "Setup1 : Sweep" / "Setup1 : LastAdaptive"
        self.dp: np.ndarray | None = None
        self.texts: list[str] = []      # "h = 1.8 mm" of the variables off their solved value
        self._data = None

    def set_model(self, model, solution: str) -> None:
        self.model, self.solution = model, solution
        self.dp = None if model is None else np.zeros(len(model.variables))
        self.texts, self._data = [], None
        self.changed.emit()

    def set_offsets(self, dp: np.ndarray, texts: list[str]) -> None:
        self.dp, self.texts, self._data = np.asarray(dp, float), texts, None
        self.changed.emit()

    def active(self, solution: str) -> bool:
        """Does a report on `solution` show tuned curves (a slider off its solved value)?"""
        return (self.model is not None and self.dp is not None and bool(np.any(self.dp != 0))
                and same_solution(solution, self.solution))

    def label(self) -> str:
        return "  (tuned: " + ", ".join(self.texts) + ")"

    def data(self, base: dict | None) -> dict:
        """Solution data of the tuned design, like the stored data of the solution (base: its ports, pairs)."""
        if self._data is None:
            S, z = self.model.sparams(self.dp)
            d = {k: v for k, v in (base or {}).items()
                 if k not in ("freq", "S", "z0", "solved", "gamma", "zo")}
            d.update(freq=np.asarray(self.model.freq_out, float), S=S, z0=z,
                     solved=np.asarray(self.model.freq, float))
            if "ports" not in d:
                d["ports"] = self.model.meta.get("ports") or []
            self._data = d
        return self._data


def tuning_state(doc) -> TuningState:
    """The tuning state of a document (one slider panel per design window)."""
    st = getattr(doc, "_tuning_state", None)
    if st is None:
        st = TuningState()
        doc._tuning_state = st
    return st


class TuningPanel(QWidget):
    """Dock panel: the tuning model of a setup and sweep, one slider per variable."""

    def __init__(self, win, setup: str | None = None, sweep: str | None = None):
        super().__init__(win)
        self.win = win
        self.state = tuning_state(win.doc)
        self.model = None
        self.rows: list[dict] = []
        lay = QVBoxLayout(self)

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
        g.addWidget(QLabel("Sweep"), 1, 0)
        g.addWidget(self.sweep, 1, 1)
        g.addWidget(QLabel("Points"), 2, 0)
        g.addWidget(self.points, 2, 1)
        g.addWidget(QLabel("Variables"), 3, 0, Qt.AlignTop)
        g.addWidget(self.vars, 3, 1)
        g.addWidget(self.b_compute, 4, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        g.addWidget(self.info, 5, 0, 1, 2)
        lay.addWidget(top)

        self.slider_box = QGroupBox("Variables")
        self.slider_grid = QGridLayout(self.slider_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.slider_box)
        lay.addWidget(scroll, 1)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #b36b00;")
        lay.addWidget(self.warn)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #777;")
        lay.addWidget(self.hint)
        bl = QHBoxLayout()
        self.b_reset = QPushButton("Reset All")
        self.b_apply = QPushButton("Apply to Design")
        self.b_apply.setToolTip("Set the design variables to the slider values (analyze again to confirm the "
                                "tuned response)")
        bl.addWidget(self.b_reset)
        bl.addWidget(self.b_apply)
        lay.addLayout(bl)

        self._pending = ""
        self._timer = QTimer(self)                      # coalesce slider moves into one update
        self._timer.setSingleShot(True)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._publish)
        self.setup.currentTextChanged.connect(lambda *_: (self._fill_sweeps(None), self.load()))
        self.sweep.currentIndexChanged.connect(lambda *_: self.load())
        self.b_compute.clicked.connect(self.compute)
        self.b_reset.clicked.connect(self.reset)
        self.b_apply.clicked.connect(self.apply)
        win.solver.finished.connect(self._solver_finished)
        self.select(setup, sweep)

    # ---- model ---------------------------------------------------------------------------------
    def select(self, setup: str | None, sweep: str | None) -> None:
        """Show the tuning model of setup and sweep (None: keep the current choice)."""
        des = self.win.des
        self.setup.blockSignals(True)
        keep = self.setup.currentText()
        self.setup.clear()
        self.setup.addItems([s.name for s in des.setups if not s.eigen])
        self.setup.setCurrentText(setup or keep)
        self.setup.blockSignals(False)
        self._fill_sweeps(sweep or self.sweep_name() or None)
        self._fill_vars()
        self.load()

    def _fill_sweeps(self, want: str | None) -> None:
        from ..solver.tuning import ADAPTIVE
        self.sweep.blockSignals(True)
        self.sweep.clear()
        try:
            st = self.win.des.setup(self.setup.currentText())
            for sw in st.sweeps:
                self.sweep.addItem(sw.name, sw.name)
                if sw.props.get("Enabled", "True") != "True":
                    # a disabled sweep is not analyzed: no solution to tune and plot
                    n = self.sweep.count() - 1
                    self.sweep.setItemText(n, f"{sw.name} (disabled)")
                    item = self.sweep.model().item(n)
                    item.setEnabled(False)
                    item.setToolTip("Disabled sweeps are not analyzed: enable it (right click > Enable) and analyze "
                                    "to tune it")
        except Exception:  # noqa: BLE001
            pass
        self.sweep.addItem("Solution frequency", ADAPTIVE)
        k = self.sweep.findData(want) if want else -1
        if k < 0 or not self.sweep.model().item(k).isEnabled():
            k = next(i for i in range(self.sweep.count()) if self.sweep.model().item(i).isEnabled())
        self.sweep.setCurrentIndex(k)
        self.sweep.blockSignals(False)

    def sweep_name(self) -> str:
        return self.sweep.currentData() or ""

    def solution(self) -> str:
        return f"{self.setup.currentText()} : {self.sweep_name()}"

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
        self.info.setText("")
        if p is not None and p.exists():
            try:
                self.model = TuningModel(p)
            except Exception as e:  # noqa: BLE001
                self.info.setText(f"The tuning model cannot be read ({e}); compute it again.")
        if self.model is None:
            if not self.info.text():
                self.info.setText("No tuning model for this solution yet: check the variables to tune and click "
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
        self.state.set_model(self.model, self.solution())
        self._show_hint()

    def _show_hint(self) -> None:
        if self.model is None:
            self.hint.setText("")
            return
        open_on = [w for w in getattr(self.win, "report_tabs", {}).values()
                   if same_solution(w.sol.currentText(), self.solution())]
        self.hint.setText("The reports on " + self.solution() + " show the tuned curves (solid) over the solved "
                          "ones (dashed)." if open_on else
                          "Open a report on " + self.solution() + " to see the tuned curves.")

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
        problem = self._not_tunable()
        if problem:
            QMessageBox.information(self, "Derivative Tuning", problem)
            return
        key = self.win.compute_tuning(self.setup.currentText(), self.sweep_name(), names, self.points.value())
        if key:
            self._pending = key
            self.info.setText("Computing the derivatives... (progress in the Progress window)")

    def _not_tunable(self) -> str:
        """Why the chosen sweep cannot be tuned ('' when it can): the reports draw the tuned curves over the
        stored solution, so the sweep must be enabled and analyzed."""
        from ..solver.results import ResultStore, results_dir, _safe
        from ..solver.tuning import ADAPTIVE
        setup, name = self.setup.currentText(), self.sweep_name()
        try:
            st = self.win.des.setup(setup)
        except Exception:  # noqa: BLE001
            return f"No setup '{setup}'."
        sw = next((x for x in st.sweeps if x.name == name), None)
        if sw is not None and sw.props.get("Enabled", "True") != "True":
            return f"The sweep '{name}' is disabled, so it is not analyzed: enable it and analyze the setup first."
        doc = self.win.doc
        if not doc.project.path:
            return "Save and analyze the project first."
        sols = ResultStore(results_dir(doc.project.path, doc.design_name, setup)).solutions()
        if _safe(name if name != ADAPTIVE else "LastAdaptive") not in sols:
            what = f"the sweep '{name}'" if name != ADAPTIVE else f"the setup '{setup}'"
            return f"There is no solution of {what} yet: analyze it first (the reports show the tuned curves over it)."
        return ""

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
            lo, hi = slider_range(m, i)
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
        for r in range(self.slider_grid.rowCount()):
            self.slider_grid.setRowStretch(r, 0)
        self.slider_grid.setRowStretch(2 * len(m.variables), 1)    # the sliders at the top, not spread out

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

    def _publish(self) -> None:
        dp = self.offsets()
        self._warn(dp)
        texts = [f"{r['name']} = {fmt_value(r['spin'].value(), r['unit'])}" for r, d in zip(self.rows, dp) if d]
        self.state.set_offsets(dp, texts)
        self._show_hint()

    def _warn(self, dp) -> None:
        far = [r["name"] for r, d in zip(self.rows, dp)
               if (d > 0 and r["hi"] > 0 and d > WARN_FRACTION * r["hi"])
               or (d < 0 and r["lo"] < 0 and d < WARN_FRACTION * r["lo"])]
        self.warn.setText(f"{', '.join(far)} far from the solved value: the accuracy decreases. Apply to Design "
                          "and analyze to confirm." if far else "")

    def reset(self) -> None:
        for r in self.rows:
            r["spin"].setValue(r["v0"])

    def apply(self) -> None:
        if not self.rows:
            return
        vals = {r["name"]: fmt_value(r["spin"].value(), r["unit"]) for r in self.rows}
        self.win.apply_variables(vals, "derivative tuning")

    def closeEvent(self, e) -> None:  # noqa: N802
        self.state.set_model(None, "")
        try:
            self.win.solver.finished.disconnect(self._solver_finished)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)
