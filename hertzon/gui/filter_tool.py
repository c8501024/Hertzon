"""Design Wizard > Hairpin bandpass: specification dialog with a live preview of what the specification
allows (the ideal Chebyshev response with the loss of the board and the circuit of the start design), help with the
number of resonators, a top view of the layout, and Create and Optimize (the design, then its space mapping
optimization with the circuit as the fast model)."""
from __future__ import annotations

import dataclasses

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit, QComboBox,
                               QSpinBox, QDoubleSpinBox, QCheckBox, QLabel, QDialogButtonBox, QWidget, QScrollArea,
                               QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView)

from ..model.filters import (HairpinSpec, auto_bands, circuit_name, fit_region, hairpin_circuit, hairpin_design,
                             ideal_response, nominal, outlines, suggest_n, unloaded_q)
from ..model.materials import is_conductor


def _num(v: float, lo: float, hi: float, dec: int = 3, suffix: str = "", step: float | None = None) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setValue(v)
    if suffix:
        w.setSuffix(" " + suffix)
    if step:
        w.setSingleStep(step)
    return w


def _pair(a: QDoubleSpinBox, b: QDoubleSpinBox, c: QDoubleSpinBox | None = None) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in (a, b, c):
        if w is not None:
            lay.addWidget(w)
    return box


class HairpinFilterDialog(QDialog):
    """Band-pass filter of N directly tapped hairpins (the filterbench geometry), as a parametrized design
    with setup, sweep, a conjugate gradient optimization of L, w, b, g, yP and M on the filter mask and a
    derivative trust region optimization of L, g and yP on the same mask; with the optimizations also the filter
    as a circuit (extra_designs), the coarse model of a space mapping optimization."""

    def __init__(self, parent, project):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Filter Design: Tapped Hairpin")
        d = HairpinSpec()
        top = QHBoxLayout(self)
        left = QVBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setLayout(left)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(560)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        top.addWidget(scroll)

        g = QGroupBox("Filter")
        f = QFormLayout(g)
        self.n = QSpinBox()
        self.n.setRange(2, 9)
        self.n.setValue(d.n)
        self.pb_lo, self.pb_hi = _num(d.passband[0], 0.01, 300, 4, "GHz"), _num(d.passband[1], 0.01, 300, 4, "GHz")
        self.sb = QLineEdit(", ".join(f"{a:g}-{b:g}" for a, b in d.stopbands))
        self.sb.setToolTip("Stopbands in GHz: start-stop, separated by commas (empty: no rejection goals)")
        self.il, self.rl = _num(d.insertion_loss, 0, 100, 2, "dB"), _num(d.return_loss, 0, 100, 2, "dB")
        self.rip, self.rej = _num(d.ripple, 0, 100, 2, "dB"), _num(d.rejection, 0, 200, 2, "dB")
        self.fd = _num(0.0, 0.0, 300, 4, "GHz", 0.01)
        self.fd.setSpecialValueText("Passband center")
        self.fd.setToolTip("Frequency the resonators are synthesized at (half wavelength); "
                           "at the minimum the passband center is used")
        self.suggest = QPushButton("Suggest N")
        self.suggest.setToolTip("The smallest number of resonators whose ideal filter meets the rejection in the "
                                "stopbands and the insertion loss in the passband with the loss of this board; "
                                "the region and board grow where the hairpins need more room")
        self.suggest.clicked.connect(self.apply_suggestion)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.n)
        rl.addWidget(self.suggest)
        f.addRow("Resonators N", row)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        f.addRow(self.hint)
        f.addRow("Passband", _pair(self.pb_lo, self.pb_hi))
        f.addRow("Design frequency", self.fd)
        f.addRow("Insertion loss <=", self.il)
        f.addRow("Return loss >=", self.rl)
        f.addRow("Ripple <=", self.rip)
        self.auto = QCheckBox("Stopbands and sweep from the passband")
        self.auto.setToolTip("Sweep one bandwidth beyond the passband edges, stopbands from a third of the "
                             "bandwidth away from the passband to the sweep ends")
        self.auto.setChecked(True)
        f.addRow(self.auto)
        f.addRow("Stopbands (GHz)", self.sb)
        f.addRow("Rejection >=", self.rej)
        left.addWidget(g)

        g = QGroupBox("Substrate")
        f = QFormLayout(g)
        self.mat = QComboBox()
        mats = [m for m in project.all_materials() if not m.pec and not is_conductor(m) and _float(m.permittivity) > 1]
        self.mats = {m.name: m for m in mats}
        self.mat.addItems(sorted(self.mats))
        if d.material in self.mats:
            self.mat.setCurrentText(d.material)
        self.mat_info = QLabel("")
        self.h, self.t = _num(d.h, 0.01, 50, 4, "mm", 0.01), _num(d.t, 0, 500, 1, "um")
        self.board, self.region = _num(d.board, 1, 1000, 2, "mm"), _num(d.region, 1, 1000, 2, "mm")
        f.addRow("Material", self.mat)
        f.addRow("", self.mat_info)
        f.addRow("Height h", self.h)
        f.addRow("Copper thickness", self.t)
        f.addRow("Board (square)", self.board)
        f.addRow("Resonator region", self.region)
        left.addWidget(g)

        g = QGroupBox("Hairpins")
        f = QFormLayout(g)
        self.zr, self.zf = _num(d.z_res, 10, 200, 2, "ohm"), _num(d.z_feed, 10, 200, 2, "ohm")
        self.b, self.b_tol = _num(d.inner, 0.01, 100, 3, "mm"), _num(100 * d.inner_tol, 0, 90, 1, "%")
        self.w_tol = _num(100 * d.trace_tol, 0, 90, 1, "%")
        self.g_lo, self.g_hi = _num(d.gap[0], 0.001, 50, 3, "mm"), _num(d.gap[1], 0.001, 50, 3, "mm")
        self.l_lo, self.l0, self.l_hi = (_num(v, 1, 200, 1, "%") for v in d.length_pct)
        self.m_lo, self.m_hi = _num(d.miter[0], 0, 0.99, 2), _num(d.miter[1], 0, 0.99, 2)
        self.dmin = _num(d.min_feature, 0.001, 5, 3, "mm")
        f.addRow("Resonator line impedance", self.zr)
        f.addRow("Feed line impedance", self.zf)
        f.addRow("Trace width w tolerance", self.w_tol)
        f.addRow("Inner width b", _pair(self.b, self.b_tol))
        f.addRow("Coupling gap g (min, max)", _pair(self.g_lo, self.g_hi))
        self.syn = QCheckBox("Gap and tap from the Chebyshev prototype")
        self.syn.setChecked(d.synthesis)
        self.syn.setToolTip("Otherwise the middle of their ranges. Couplings and external Q of the prototype for the passband with the return loss goal "
                            "+ 3 dB; the gap from the coupled line impedances, the tap height from the external Q")
        f.addRow(self.syn)
        f.addRow("Length L of lambda_g (min, start, max)", _pair(self.l_lo, self.l0, self.l_hi))
        f.addRow("Miter M (min, max)", _pair(self.m_lo, self.m_hi))
        f.addRow("Minimum feature", self.dmin)
        left.addWidget(g)

        g = QGroupBox("Analysis")
        f = QFormLayout(g)
        self.f_lo, self.f_hi = _num(d.sweep[0], 0.001, 300, 4, "GHz"), _num(d.sweep[1], 0.001, 300, 4, "GHz")
        self.f_step = _num(d.sweep[2], 0.0001, 10, 4, "GHz")
        self.opt = QCheckBox("Add the optimizations on the filter mask")
        self.opt.setToolTip("Conjugate gradient, derivative trust region, and space mapping with the filter as a "
                            "circuit (HairpinSM, the fast one: Create and Optimize starts it)")
        self.opt.setChecked(True)
        self.name = QLineEdit("")
        self.name.setPlaceholderText("Hairpin_N3")
        f.addRow("Sweep (start, stop, step)", _pair(self.f_lo, self.f_hi, self.f_step))
        f.addRow(self.opt)
        f.addRow("Design name", self.name)
        left.addWidget(g)
        left.addStretch(1)

        right = QVBoxLayout()
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.tabs = QTabWidget()
        self.rfig = Figure(figsize=(5.6, 4.0), tight_layout=True)
        self.rax = self.rfig.add_subplot(111)
        self.rcanvas = FigureCanvasQTAgg(self.rfig)
        self.tabs.addTab(self.rcanvas, "Response")
        self.fig = Figure(figsize=(5.2, 5.2), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.tabs.addTab(self.canvas, "Layout")
        right.addWidget(self.tabs, 1)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["N", "Rejection (dB)", "Insertion loss (dB)", "Rejection reached at",
                                              "Goals"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(150)
        self.table.setToolTip("The ideal filter of every N with the unloaded Q of this board: worst rejection in "
                              "the stopbands, worst insertion loss in the passband, the frequencies where S21 "
                              "falls below the rejection goal. Double-click a row to use that N")
        self.table.cellDoubleClicked.connect(lambda r, _c: self.set_n(int(self.table.item(r, 0).text())))
        right.addWidget(self.table)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        right.addWidget(self.info)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setText("Create Design")
        self.go = self.bb.addButton("Create and Optimize", QDialogButtonBox.ActionRole)
        self.go.setToolTip("Create the design with its circuit model and start the space mapping optimization "
                           "(HairpinSM): the circuit is optimized in seconds, a few 3D solves correct it")
        self.go.clicked.connect(self._optimize_clicked)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        right.addWidget(self.bb)
        top.addLayout(right, 1)
        self.run_optimization = ""                  # the optimization the main window starts after inserting

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        for w in self.findChildren(QDoubleSpinBox) + self.findChildren(QSpinBox):
            w.valueChanged.connect(self.timer.start)
        self.sb.textChanged.connect(self.timer.start)
        self.auto.toggled.connect(self.timer.start)
        self.syn.toggled.connect(self.timer.start)
        self.mat.currentTextChanged.connect(self.timer.start)
        self.n.valueChanged.connect(lambda v: self.name.setPlaceholderText(f"Hairpin_N{v}"))
        self.design = None
        self.resize(1280, 820)
        self.refresh()

    # ---- number of resonators and optimization ---------------------------------------------------
    def set_n(self, n: int) -> None:
        """Use n resonators; the region and board grow where the hairpins do not fit."""
        self.n.setValue(n)
        try:
            s = self.spec()
            ok = True
            try:
                nom = nominal(s)
                ok = not any(w.startswith(("Length range", "Inner width", "The start length")) for w in nom["warnings"])
            except ValueError:
                ok = False
            if not ok:
                region, board = fit_region(s)
                self.region.setValue(max(region, self.region.value()))
                self.board.setValue(max(board, self.board.value()))
        except ValueError:
            pass

    def apply_suggestion(self) -> None:
        try:
            n, _why, _rows = suggest_n(self.spec())
        except ValueError:
            return
        self.set_n(n)

    def _optimize_clicked(self) -> None:
        self.opt.setChecked(True)
        self.refresh()
        if self.design is not None:
            self.run_optimization = "HairpinSM"
            self.accept()

    # ---- specification ------------------------------------------------------------------------
    def extra_designs(self) -> list:
        """The circuit model of the filter (coarse model of HairpinSM), made when the dialog is accepted."""
        if self.design is None or not getattr(self, "_spec", None) or not self._spec.optimization:
            return []
        return [hairpin_circuit(self._spec, circuit_name(self.design.name))]

    def spec(self) -> HairpinSpec:
        m = self.mats.get(self.mat.currentText())
        bands = []
        for band in [x for x in self.sb.text().split(",") if x.strip()]:
            lo, _, hi = band.partition("-")
            try:
                bands.append((float(lo), float(hi)))
            except ValueError:
                raise ValueError(f"Stopband '{band.strip()}': give start-stop in GHz") from None
        return HairpinSpec(
            n=self.n.value(), passband=(self.pb_lo.value(), self.pb_hi.value()), stopbands=bands,
            insertion_loss=self.il.value(), return_loss=self.rl.value(), ripple=self.rip.value(),
            rejection=self.rej.value(), material=m.name if m else "", er=_float(m.permittivity) if m else 1.0,
            tand=_float(m.loss_tangent) if m else 0.0, h=self.h.value(), t=self.t.value(), board=self.board.value(),
            region=self.region.value(), z_res=self.zr.value(), z_feed=self.zf.value(),
            gap=(self.g_lo.value(), self.g_hi.value()), inner=self.b.value(), inner_tol=self.b_tol.value() / 100,
            trace_tol=self.w_tol.value() / 100, length_pct=(self.l_lo.value(), self.l0.value(), self.l_hi.value()),
            miter=(self.m_lo.value(), self.m_hi.value()), min_feature=self.dmin.value(),
            sweep=(self.f_lo.value(), self.f_hi.value(), self.f_step.value()), optimization=self.opt.isChecked(),
            synthesis=self.syn.isChecked(),
            name=self.name.text().strip(), f_design=self.fd.value())

    def _check(self, s: HairpinSpec) -> None:
        if s.gap[1] < s.gap[0] or s.miter[1] < s.miter[0] or not s.length_pct[0] <= s.length_pct[1] <= s.length_pct[2]:
            raise ValueError("Every range needs min <= max (and the start length inside its range)")
        if not s.sweep[1] > s.sweep[0]:
            raise ValueError("The sweep needs start < stop")
        if s.sweep[0] > min([s.passband[0]] + [a for a, _ in s.stopbands]) + 1e-9 or \
                s.sweep[1] < max([s.passband[1]] + [b for _, b in s.stopbands]) - 1e-9:
            raise ValueError("The sweep must cover the passband and the stopbands (the goals are evaluated on it)")
        if not s.material:
            raise ValueError("Choose a substrate material")

    def _auto_bands(self) -> None:
        on = self.auto.isChecked()
        for w in (self.sb, self.f_lo, self.f_hi, self.f_step):
            w.setEnabled(not on)
        if not on or not self.pb_hi.value() > self.pb_lo.value():
            return
        bands, sweep = auto_bands((self.pb_lo.value(), self.pb_hi.value()))
        for w, v in ((self.f_lo, sweep[0]), (self.f_hi, sweep[1]), (self.f_step, sweep[2])):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.sb.blockSignals(True)
        self.sb.setText(", ".join(f"{a:g}-{b:g}" for a, b in bands))
        self.sb.blockSignals(False)

    def refresh(self) -> None:
        self._auto_bands()
        ax = self.ax
        ax.clear()
        m = self.mats.get(self.mat.currentText())
        self.mat_info.setText(f"er = {m.permittivity}, tan d = {m.loss_tangent}" if m else "")
        try:
            s = self.spec()
            self._check(s)
            self.design, nom = hairpin_design(s)
            self._spec = s
        except Exception as e:  # noqa: BLE001
            self.design = None
            self.info.setText(f"<span style='color:#c0392b'>{e}</span>")
            self.bb.button(QDialogButtonBox.Ok).setEnabled(False)
            self.go.setEnabled(False)
            self.canvas.draw_idle()
            self.rax.clear()
            self.rcanvas.draw_idle()
            return
        self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
        self.go.setEnabled(True)
        self._response(s)
        half, reg = s.board / 2, s.region / 2
        ax.fill([-half, half, half, -half], [-half, -half, half, half], color="#c9d6a3", zorder=0)
        ax.plot([-reg, reg, reg, -reg, -reg], [-reg, -reg, reg, reg, -reg], "--", color="#7f8c5a", lw=0.8, zorder=1)
        for name, pts in outlines(self.design, self.project).items():
            xs, ys = zip(*pts)
            ax.fill(xs, ys, color="#c8892b", ec="#7a5216", lw=0.6, zorder=2)
            if name.startswith("Hairpin"):
                ax.text(sum(xs) / len(xs), max(ys) + 0.4 if name[-1] in "13579" else min(ys) - 1.0,
                        name.replace("Hairpin", "R"), ha="center", fontsize=8, color="#5a3a10")
        ev = self.design.evaluator(self.project)
        yt = ev.length("yt") * 1e3
        for x, lab in ((-half, "1"), (half, "2")):
            ax.plot([x], [yt], "s", color="#c0392b", ms=6, zorder=3)
            ax.annotate(f"Port {lab}", (x, yt), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8,
                        color="#c0392b")
        ax.set_aspect("equal")
        ax.set_xlim(-half - 1, half + 1)
        ax.set_ylim(-half - 1, half + 1)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        self.canvas.draw_idle()
        r = nom["ranges"]
        txt = (f"Synthesis: {nom['synthesis']}.  lambda_g = {nom['lambda_g']:.2f} mm at {s.f0:g} GHz.  Start: L = {nom['length']:g} mm "
               f"({100 * nom['length'] / nom['lambda_g']:.1f}% lambda_g), w = {nom['trace']:g} mm, "
               f"b = {nom['inner']:g} mm, g = {nom['gap']:g} mm, yP = {nom['yP']:g} mm, M = {nom['miter']:g}; "
               f"feed wf = {nom['feed']:g} mm.  Ranges: L {r['L'][0]:g}..{r['L'][1]:g}, w {r['w'][0]:g}..{r['w'][1]:g}, "
               f"b {r['b'][0]:g}..{r['b'][1]:g}, g {r['g'][0]:g}..{r['g'][1]:g}, yP {r['yP'][0]:g}..{r['yP'][1]:g} mm, "
               f"M {r['M'][0]:g}..{r['M'][1]:g}.")
        if nom.get("prototype"):
            pr = nom["prototype"]
            txt += (f"<br>Chebyshev prototype ({pr['ripple']:.3g} dB ripple): k = "
                    + ", ".join(f"{k:.4f}" for k in pr["k"]) + f", Qe = {pr['Qe']:.2f}.")
        if nom["warnings"]:
            txt += "<br><span style='color:#b9770e'>" + "<br>".join(nom["warnings"]) + "</span>"
        self.info.setText(txt)

    def _response(self, s: HairpinSpec) -> None:
        """The response tab, the table of N and the hint: the ideal Chebyshev filter of N resonators (and of N - 1,
        N + 1) with the unloaded Q of the board, the circuit of the start design and the filter mask."""
        qu = unloaded_q(s)
        n_best, why, rows = suggest_n(s, qu=qu)
        lo, hi = s.passband
        bw = hi - lo
        a = min(s.sweep[0], lo - 1.5 * bw, *(x for x, _ in s.stopbands))
        b = max(s.sweep[1], hi + 1.5 * bw, *(y for _, y in s.stopbands))
        f = np.linspace(max(a, 1e-3), b, 601)
        ax = self.rax
        ax.clear()
        db = lambda v: 20 * np.log10(np.maximum(np.abs(v), 1e-12))  # noqa: E731
        for k, style in ((s.n - 1, ":"), (s.n + 1, "--")):
            if 2 <= k <= 9:
                ax.plot(f, db(ideal_response(s, f, k, qu)[0]), style, color="#9aa5b1", lw=1, label=f"Ideal S21, N = {k}")
        s21, s11 = ideal_response(s, f, s.n, qu)
        ax.plot(f, db(s21), color="#1f5fbf", lw=2, label=f"Ideal S21, N = {s.n} (Qu {qu:.0f})")
        ax.plot(f, db(s11), color="#1f5fbf", lw=1, alpha=0.45, label="Ideal S11")
        try:
            from ..model.design import Project
            from ..solver.circuit import analyze
            c = hairpin_circuit(dataclasses.replace(s, optimization=False), "preview")
            fc = np.linspace(s.sweep[0], s.sweep[1], 181)
            S = analyze(Project(), c, fc * 1e9).S
            ax.plot(fc, db(S[:, 1, 0]), color="#c8892b", lw=1.2, label="Start design (circuit), S21")
        except Exception:  # noqa: BLE001 (the preview of the start design is optional)
            pass
        # the mask
        ax.plot([lo, hi], [-s.insertion_loss] * 2, color="#27ae60", lw=2.5)
        ax.plot([lo, hi], [-s.return_loss] * 2, color="#27ae60", lw=1.5, ls="--")
        for x, y in s.stopbands:
            ax.plot([x, y], [-s.rejection] * 2, color="#c0392b", lw=2.5)
        ax.set_xlim(f[0], f[-1])
        ax.set_ylim(min(-s.rejection - 25, -40), 2)
        ax.set_xlabel("Frequency (GHz)")
        ax.set_ylabel("dB")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc="lower center")
        self.rcanvas.draw_idle()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            e0, e1 = r["edges"]
            edges = f"{e0:.4g} / {e1:.4g} GHz" if e0 is not None and e1 is not None else "-"
            cells = [str(r["n"]), f"{r['rejection']:.1f}", f"{r['il']:.2f}", edges,
                     "met" if r["ok"] else ("rejection only" if r["rejection"] >= s.rejection else "-")]
            for j, t in enumerate(cells):
                it = QTableWidgetItem(t)
                if r["n"] == s.n:
                    font = it.font()
                    font.setBold(True)
                    it.setFont(font)
                self.table.setItem(i, j, it)
        hint = f"Suggested: N = {n_best}. {why}."
        if n_best % 2 == 0:
            hint += " Odd N are easier for the tapped hairpin (even N: the taps sit on opposite sides)."
        met = any(r["ok"] and r["n"] == s.n for r in rows)
        self.hint.setText(f"<span style='color:{'#1e8449' if met else '#b9770e'}'>{hint}</span>")


def _float(text) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


class DiplexerDialog(QDialog):
    """Two tapped hairpin filters joined by a T-junction (model/diplexer.py): channel A to Port 2, channel B to
    Port 3, the common Port 1; as a parametrized design with setup, sweep, the space mapping optimization
    DiplexerSM (with the diplexer as a circuit, extra_designs) and a conjugate gradient on the same goals."""

    def __init__(self, parent, project):
        from ..model.diplexer import DiplexerSpec
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Filter Design: Hairpin Diplexer / Triplexer")
        d = DiplexerSpec()
        top = QHBoxLayout(self)
        left = QVBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setLayout(left)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(420)
        top.addWidget(scroll)

        self.ch = []
        from ..model.diplexer import ChannelSpec
        for k, c in enumerate(d.channels + [ChannelSpec(3, (2.95, 3.15))]):
            g = QGroupBox(f"Channel {'ABC'[k]} (Port {k + 2})")
            f = QFormLayout(g)
            if k == 2:
                self.third = QCheckBox("Third channel: triplexer (star junction, Port 4 at the bottom edge)")
                self.third.setChecked(False)
                f.addRow(self.third)
            n = QSpinBox()
            n.setRange(2, 9)
            n.setValue(c.n)
            w = {"n": n, "lo": _num(c.passband[0], 0.01, 300, 4, "GHz"), "hi": _num(c.passband[1], 0.01, 300, 4, "GHz"),
                 "il": _num(c.insertion_loss, 0, 100, 2, "dB"), "rl": _num(c.return_loss, 0, 100, 2, "dB"),
                 "rip": _num(c.ripple, 0, 100, 2, "dB"), "rej": _num(c.rejection, 0, 200, 2, "dB")}
            f.addRow("Resonators N", n)
            f.addRow("Passband", _pair(w["lo"], w["hi"]))
            f.addRow("Insertion loss <=", w["il"])
            f.addRow("Return loss >=", w["rl"])
            f.addRow("Ripple <=", w["rip"])
            rej = w["rej"]
            rej.setToolTip("In the passbands of the other channels and in the outer stopband (a third of the "
                           "bandwidth away from the passband)")
            f.addRow("Rejection >=", rej)
            self.ch.append(w)
            left.addWidget(g)

        g = QGroupBox("Junction and isolation")
        f = QFormLayout(g)
        self.junction = QComboBox()
        self.junction.addItems(["T-junction", "Common resonator"])
        self.junction.setToolTip("T-junction: the filters on two lines from a tee. Common resonator: one hairpin "
                                 "between the channels, tapped by Port 1 and coupled to both filters (smaller)")
        f.addRow("Junction", self.junction)
        self.iso = _num(d.isolation, 0, 200, 2, "dB")
        self.iso.setToolTip("Between every two channel ports (|S32|, ...) in both of their passbands")
        self.fl, self.mb = _num(d.feed, 0.1, 100, 2, "mm"), _num(d.margin, 0, 100, 2, "mm")
        f.addRow("Isolation >=", self.iso)
        f.addRow("Feed line to Ports 2, 3", self.fl)
        f.addRow("Board margin", self.mb)
        left.addWidget(g)

        g = QGroupBox("Substrate")
        f = QFormLayout(g)
        self.mat = QComboBox()
        mats = [m for m in project.all_materials() if not m.pec and not is_conductor(m) and _float(m.permittivity) > 1]
        self.mats = {m.name: m for m in mats}
        self.mat.addItems(sorted(self.mats))
        if d.material in self.mats:
            self.mat.setCurrentText(d.material)
        self.mat_info = QLabel("")
        self.h, self.t = _num(d.h, 0.01, 50, 4, "mm", 0.01), _num(d.t, 0, 500, 1, "um")
        f.addRow("Material", self.mat)
        f.addRow("", self.mat_info)
        f.addRow("Height h", self.h)
        f.addRow("Copper thickness", self.t)
        left.addWidget(g)

        g = QGroupBox("Hairpins")
        f = QFormLayout(g)
        self.zr, self.zf = _num(d.z_res, 10, 200, 2, "ohm"), _num(d.z_feed, 10, 200, 2, "ohm")
        self.b, self.b_tol = _num(d.inner, 0.01, 100, 3, "mm"), _num(100 * d.inner_tol, 0, 90, 1, "%")
        self.w_tol = _num(100 * d.trace_tol, 0, 90, 1, "%")
        self.g_lo, self.g_hi = _num(d.gap[0], 0.001, 50, 3, "mm"), _num(d.gap[1], 0.001, 50, 3, "mm")
        self.l_lo, self.l0, self.l_hi = (_num(v, 1, 200, 1, "%") for v in d.length_pct)
        self.m_lo, self.m_hi = _num(d.miter[0], 0, 0.99, 2), _num(d.miter[1], 0, 0.99, 2)
        self.dmin = _num(d.min_feature, 0.001, 5, 3, "mm")
        f.addRow("Resonator line impedance", self.zr)
        f.addRow("Feed line impedance", self.zf)
        f.addRow("Trace width w tolerance", self.w_tol)
        f.addRow("Inner width b", _pair(self.b, self.b_tol))
        f.addRow("Coupling gaps (min, max)", _pair(self.g_lo, self.g_hi))
        self.syn = QCheckBox("Gaps and taps from the Chebyshev prototype of each channel")
        self.syn.setChecked(d.synthesis)
        f.addRow(self.syn)
        f.addRow("Length L of lambda_g (min, start, max)", _pair(self.l_lo, self.l0, self.l_hi))
        f.addRow("Miter M (min, max)", _pair(self.m_lo, self.m_hi))
        f.addRow("Minimum feature", self.dmin)
        left.addWidget(g)

        g = QGroupBox("Analysis")
        f = QFormLayout(g)
        self.auto = QCheckBox("Sweep from the passbands")
        self.auto.setChecked(True)
        self.f_lo, self.f_hi = _num(d.sweep[0], 0.001, 300, 4, "GHz"), _num(d.sweep[1], 0.001, 300, 4, "GHz")
        self.f_step = _num(d.sweep[2], 0.0001, 10, 4, "GHz")
        self.opt = QCheckBox("Add the optimizations (space mapping with the diplexer as a circuit, conjugate gradient)")
        self.opt.setChecked(True)
        self.name = QLineEdit("")
        self.name.setPlaceholderText("Diplexer_N3_N3")
        f.addRow(self.auto)
        f.addRow("Sweep (start, stop, step)", _pair(self.f_lo, self.f_hi, self.f_step))
        f.addRow(self.opt)
        f.addRow("Design name", self.name)
        left.addWidget(g)
        left.addStretch(1)

        right = QVBoxLayout()
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(6.4, 4.6), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        right.addWidget(self.canvas, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        right.addWidget(self.info)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setText("Create Design")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        right.addWidget(self.bb)
        top.addLayout(right, 1)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        for w in self.findChildren(QDoubleSpinBox) + self.findChildren(QSpinBox):
            w.valueChanged.connect(self.timer.start)
        for c in (self.auto, self.syn, self.third):
            c.toggled.connect(self.timer.start)
        self.mat.currentTextChanged.connect(self.timer.start)
        self.junction.currentIndexChanged.connect(self.timer.start)
        self.junction.currentIndexChanged.connect(self._placeholder)
        for w in self.ch:
            w["n"].valueChanged.connect(self._placeholder)
        self.design = None
        self._spec = None
        self.resize(1180, 760)
        self.refresh()

    def _placeholder(self) -> None:
        ns = "_".join(f"N{w['n'].value()}" for w in self.channels())
        if self.third.isChecked():
            self.name.setPlaceholderText(f"Triplexer_{ns}")
        else:
            self.name.setPlaceholderText(f"Diplexer_{ns}{'_R' if self.junction.currentIndex() == 1 else ''}")

    def channels(self) -> list[dict]:
        return self.ch if self.third.isChecked() else self.ch[:2]

    def extra_designs(self) -> list:
        """The diplexer as a circuit (coarse model of DiplexerSM), made when the dialog is accepted."""
        from ..model.diplexer import circuit_name, diplexer_circuit
        if self.design is None or self._spec is None or not self._spec.optimization:
            return []
        return [diplexer_circuit(self._spec, circuit_name(self.design.name))]

    def spec(self):
        from ..model.diplexer import ChannelSpec, DiplexerSpec, auto_sweep
        m = self.mats.get(self.mat.currentText())
        chans = [ChannelSpec(n=w["n"].value(), passband=(w["lo"].value(), w["hi"].value()),
                             insertion_loss=w["il"].value(), return_loss=w["rl"].value(), ripple=w["rip"].value(),
                             rejection=w["rej"].value()) for w in self.channels()]
        if self.auto.isChecked() and all(c.passband[1] > c.passband[0] for c in chans):
            sweep = auto_sweep(chans)
            for w, v in ((self.f_lo, sweep[0]), (self.f_hi, sweep[1]), (self.f_step, sweep[2])):
                w.blockSignals(True)
                w.setValue(v)
                w.blockSignals(False)
        for w in (self.f_lo, self.f_hi, self.f_step):
            w.setEnabled(not self.auto.isChecked())
        if not m:
            raise ValueError("Choose a substrate material")
        if self.g_hi.value() < self.g_lo.value() or self.m_hi.value() < self.m_lo.value() or \
                not self.l_lo.value() <= self.l0.value() <= self.l_hi.value():
            raise ValueError("Every range needs min <= max (and the start length inside its range)")
        return DiplexerSpec(
            channels=chans, isolation=self.iso.value(), material=m.name, er=_float(m.permittivity),
            tand=_float(m.loss_tangent), h=self.h.value(), t=self.t.value(), z_res=self.zr.value(),
            z_feed=self.zf.value(), gap=(self.g_lo.value(), self.g_hi.value()), inner=self.b.value(),
            inner_tol=self.b_tol.value() / 100, trace_tol=self.w_tol.value() / 100,
            length_pct=(self.l_lo.value(), self.l0.value(), self.l_hi.value()),
            miter=(self.m_lo.value(), self.m_hi.value()), min_feature=self.dmin.value(), feed=self.fl.value(),
            margin=self.mb.value(), sweep=(self.f_lo.value(), self.f_hi.value(), self.f_step.value()),
            synthesis=self.syn.isChecked(), optimization=self.opt.isChecked(), name=self.name.text().strip(),
            junction="resonator" if self.junction.currentIndex() == 1 else "tee")

    def refresh(self) -> None:
        from ..model.diplexer import diplexer_design
        from ..model.diplexer import outlines as dx_outlines
        ax = self.ax
        ax.clear()
        m = self.mats.get(self.mat.currentText())
        self.mat_info.setText(f"er = {m.permittivity}, tan d = {m.loss_tangent}" if m else "")
        try:
            s = self.spec()
            self.design, nom = diplexer_design(s)
            self._spec = s
        except Exception as e:  # noqa: BLE001
            self.design = None
            self.info.setText(f"<span style='color:#c0392b'>{e}</span>")
            self.bb.button(QDialogButtonBox.Ok).setEnabled(False)
            self.canvas.draw_idle()
            return
        self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
        ev = self.design.evaluator(self.project)
        L = {k: ev.length(k) * 1e3 for k in ("Wl", "Wr", "Wy")}
        x0, x1 = -L["Wl"], L["Wr"]
        y1 = ev.length("Wu") * 1e3 if "Wu" in self.design.variables else L["Wy"] / 2
        y0 = y1 - L["Wy"]
        ax.fill([x0, x1, x1, x0], [y0, y0, y1, y1], color="#c9d6a3", zorder=0)
        for name, pts in dx_outlines(self.design, self.project).items():
            xs, ys = zip(*pts)
            ax.fill(xs, ys, color="#c8892b", ec="#7a5216", lw=0.6, zorder=2)
            if name.startswith("Hairpin"):
                ax.text(sum(xs) / len(xs), max(ys) + 0.4, name.replace("Hairpin", ""), ha="center", fontsize=7,
                        color="#5a3a10")
        marks = []
        for o in self.design.objects:                     # the ports where the design puts them
            if o.name.startswith("Port") and o.history:
                p = o.history[0].props
                x, y, _ = ev.point(p["Position"])
                dx = ev.length(p["XSize"]) if "XSize" in p else 0.0
                dy = ev.length(p["YSize"]) if "YSize" in p else 0.0
                marks.append((((x + dx / 2) * 1e3, (y + dy / 2) * 1e3), o.name[4:]))
        for (x, y), lab in marks:
            ax.plot([x], [y], "s", color="#c0392b", ms=6, zorder=3)
            ax.annotate(f"Port {lab}", (x, y), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8,
                        color="#c0392b")
        ax.set_aspect("equal")
        ax.set_xlim(x0 - 1, x1 + 1)
        ax.set_ylim(y0 - 1, y1 + 2)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        self.canvas.draw_idle()
        parts = []
        for k, (c, n) in enumerate(zip(s.channels, nom["channels"])):
            gaps = ", ".join(f"{a} = {b:g}" for a, b in sorted(n["gaps"].items()))
            line = f"Channel {'ABC'[k]}: L = {n['length']:g} mm, {gaps} mm, yP = {n['yP']:g} mm"
            if "j" in nom:
                line += f", junction line j = {nom['j'][k]:g} mm"
            parts.append(line)
        if "resonator" in nom:
            r0 = nom["resonator"]
            parts.append(f"Common resonator R0: L_0 = {r0['L']:g} mm at {r0['f0']:.4g} GHz, Qe = {r0['Qe']:.2f}, "
                         f"yP_0 = {r0['yP']:g} mm, g_0A = {r0['g'][0]:g} mm, g_0B = {r0['g'][1]:g} mm "
                         "(start; the circuit optimization of DiplexerSM sets them)")
        else:
            parts.append("The junction lines start where each filter looks like an open circuit at the junction in "
                         "the band of the other channel.")
        txt = (f"Synthesis: {nom['synthesis']}.  Board {L['Wl'] + L['Wr']:.1f} x {L['Wy']:.1f} mm.<br>"
               + "<br>".join(parts))
        warn = [w for n in nom["channels"] for w in n["warnings"]]
        if warn:
            txt += "<br><span style='color:#b9770e'>" + "<br>".join(warn) + "</span>"
        self.info.setText(txt)
