"""Tools > Antenna Design > Rectangular Patch: frequency and substrate in, parametrized patch design out."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit, QComboBox,
                               QDoubleSpinBox, QCheckBox, QLabel, QDialogButtonBox)

from ..model.antennas import PatchSpec, outlines, patch_design
from ..model.materials import is_conductor
from .filter_tool import _float, _num


class PatchAntennaDialog(QDialog):
    """Inset-fed rectangular patch from the transmission line model, with setup, sweep, far-field sphere and an
    optimization (Derivative Trust Region) of the patch length and the inset on the return loss at the frequency."""

    def __init__(self, parent, project):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Antenna Design: Rectangular Patch")
        d = PatchSpec()
        top = QHBoxLayout(self)
        left = QVBoxLayout()
        top.addLayout(left)

        g = QGroupBox("Antenna")
        f = QFormLayout(g)
        self.f0 = _num(d.f0, 0.01, 300, 5, "GHz", 0.01)
        self.rl0 = _num(d.match_f0, 0, 60, 1, "dB")
        self.zf = _num(d.z_feed, 10, 200, 2, "ohm")
        f.addRow("Frequency", self.f0)
        f.addRow("Return loss at the frequency >=", self.rl0)
        self.rl0.setToolTip("Goal of the optimization: dB(S(1,1)) at the frequency")
        f.addRow("Feed line impedance", self.zf)
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
        self.h, self.t = _num(d.h, 0.01, 50, 4, "mm", 0.1), _num(d.t, 0, 500, 1, "um")
        self.margin = _num(0.0, 0.0, 1000, 2, "mm")
        self.margin.setSpecialValueText("Automatic")
        self.margin.setToolTip("Ground and substrate beyond the patch on every side; automatic: max(6 h, lambda0/10)")
        f.addRow("Material", self.mat)
        f.addRow("", self.mat_info)
        f.addRow("Height h", self.h)
        f.addRow("Copper thickness", self.t)
        f.addRow("Ground margin", self.margin)
        left.addWidget(g)

        g = QGroupBox("Analysis")
        f = QFormLayout(g)
        self.opt = QCheckBox("Add the optimization of patch length and inset at the frequency (Derivative Trust Region)")
        self.opt.setChecked(True)
        self.name = QLineEdit("")
        f.addRow(self.opt)
        f.addRow("Design name", self.name)
        left.addWidget(g)
        left.addStretch(1)

        right = QVBoxLayout()
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(4.6, 4.6), tight_layout=True)
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
        for w in self.findChildren(QDoubleSpinBox):
            w.valueChanged.connect(self.timer.start)
        self.mat.currentTextChanged.connect(self.timer.start)
        self.design = None
        self.resize(960, 600)
        self.refresh()

    def spec(self) -> PatchSpec:
        m = self.mats.get(self.mat.currentText())
        if m is None:
            raise ValueError("Choose a substrate material")
        return PatchSpec(f0=self.f0.value(), match_f0=self.rl0.value(),
                         material=m.name, er=_float(m.permittivity), tand=_float(m.loss_tangent), h=self.h.value(),
                         t=self.t.value(), z_feed=self.zf.value(), margin=self.margin.value(),
                         optimization=self.opt.isChecked(), name=self.name.text().strip())

    def refresh(self) -> None:
        ax = self.ax
        ax.clear()
        m = self.mats.get(self.mat.currentText())
        self.mat_info.setText(f"er = {m.permittivity}, tan d = {m.loss_tangent}" if m else "")
        self.name.setPlaceholderText(f"Patch_{self.f0.value():g}GHz")
        try:
            s = self.spec()
            self.design, nom = patch_design(s)
        except Exception as e:  # noqa: BLE001
            self.design = None
            self.info.setText(f"<span style='color:#c0392b'>{e}</span>")
            self.bb.button(QDialogButtonBox.Ok).setEnabled(False)
            self.canvas.draw_idle()
            return
        self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
        ws, ls = nom["Ws"] / 2, nom["Ls"] / 2
        ax.fill([-ws, ws, ws, -ws], [-ls, -ls, ls, ls], color="#c9d6a3", zorder=0)
        for pts in outlines(self.design, self.project).values():
            xs, ys = zip(*pts)
            ax.fill(xs, ys, color="#c8892b", ec="#7a5216", lw=0.6, zorder=2)
        ax.plot([0], [-ls], "s", color="#c0392b", ms=6, zorder=3)
        ax.annotate("Port 1", (0, -ls), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8,
                    color="#c0392b")
        ax.set_aspect("equal")
        ax.set_xlim(-ws * 1.05, ws * 1.05)
        ax.set_ylim(-ls * 1.05, ls * 1.05)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        self.canvas.draw_idle()
        txt = (f"Synthesis: transmission line model, feed: {nom['synthesis']}.  Wp = {nom['Wp']:.3f} mm, "
               f"Lp = {nom['Lp']:.3f} mm (eps_eff {nom['eeff']:.3f}), edge resistance {nom['rin']:.0f} ohm, "
               f"inset {nom['inset']:.3f} mm, feed wf = {nom['wf']:.3f} mm, board {nom['Ws']:.1f} x {nom['Ls']:.1f} mm.")
        if nom["warnings"]:
            txt += "<br><span style='color:#b9770e'>" + "<br>".join(nom["warnings"]) + "</span>"
        self.info.setText(txt)
