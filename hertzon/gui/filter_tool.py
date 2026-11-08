"""Tools > Filter Design > Tapped Hairpin: specification dialog with a live top view of the layout."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit, QComboBox,
                               QSpinBox, QDoubleSpinBox, QCheckBox, QLabel, QDialogButtonBox, QWidget, QScrollArea)

from ..model.filters import HairpinSpec, auto_bands, circuit_name, hairpin_circuit, hairpin_design, outlines
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
        scroll.setMinimumWidth(400)
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
        f.addRow("Resonators N", self.n)
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
        self.syn = QCheckBox("Gap and tap from the Chebyshev prototype (otherwise the middle of their ranges)")
        self.syn.setChecked(d.synthesis)
        self.syn.setToolTip("Couplings and external Q of the prototype for the passband with the return loss goal "
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
        self.opt = QCheckBox("Add the optimizations on the filter mask (conjugate gradient, derivative trust region, space mapping with the filter as a circuit)")
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
        self.fig = Figure(figsize=(5.2, 5.2), tight_layout=True)
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
        self.resize(1080, 720)
        self.refresh()

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
            self.canvas.draw_idle()
            return
        self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
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


def _float(text) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0
