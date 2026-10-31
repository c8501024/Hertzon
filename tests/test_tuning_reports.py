"""Derivative tuning in the reports: the tuned overlay, the plot against a design variable and their export."""
import os
from pathlib import Path

import numpy as np
import pytest

EX = Path(__file__).resolve().parents[1] / "examples"


class _Model:
    """A tuning model with S11 = a (1 + dp) at every frequency (a = 0.1 ... 0.3 over the frequencies)."""

    def __init__(self, freq):
        self.freq = self.freq_out = np.asarray(freq, float)
        self.variables, self.values, self.units = ["h"], np.array([2.0]), ["mm"]
        self.ranges = [(-1.0, 1.0)]
        self.meta = {"ports": [{"name": "1", "number": 1, "kind": "Lumped Port"}]}
        self.ff = None
        self.a = np.linspace(0.1, 0.3, len(self.freq))

    def sparams(self, dp):
        return (self.a * (1 + dp[0])).reshape(-1, 1, 1).astype(complex), np.full((len(self.freq), 1), 50.0)

    def sweep_variable(self, k, i, values, dp):
        S = np.array([self.a[k] * (1 + v) for v in values]).reshape(-1, 1, 1).astype(complex)
        return S, np.full((len(S), 1), 50.0)


def _csv(path):
    import csv
    with open(path) as fh:
        head = next(csv.reader(fh))
    return head, np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)


def test_tuned_overlay_and_plot_against_a_variable(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.doc import Document, result_dir
    from hertzon.gui.reports import ReportWindow
    from hertzon.gui.tuning import tuning_state
    from hertzon.model.design import Project, Report
    from hertzon.solver.results import ResultStore
    app = QApplication.instance() or QApplication([])
    proj = Project.load(EX / "PatchAntenna.hzn")
    proj.save(tmp_path / "p.hzn")
    doc = Document(proj)
    st = ResultStore(result_dir(doc, "Setup1"))
    m = _Model([2.0e9, 2.5e9, 3.0e9])
    st.write_json("ports.json", m.meta["ports"])
    st.save_sparams("Sweep", m.freq, m.sparams([0.0])[0], np.array([50.0]), m.meta["ports"])
    ts = tuning_state(doc)
    ts.set_model(m, "Setup1 : Sweep")

    # over frequency: the solved curve, and the tuned one once a slider is off its value
    w = ReportWindow(doc, Report("S11", "Rectangular", "Setup1 : Sweep", ["mag(S(1,1))"]))
    w.show()                                   # a hidden report draws the tuned curves when it is shown
    w.xvar.setCurrentIndex(0)
    assert [w.xvar.itemText(i) for i in range(w.xvar.count())] == ["Frequency", "h"]
    assert len(w.fig.axes[0].get_lines()) == 1
    ts.set_offsets(np.array([0.5]), ["h = 2.5 mm"])
    app.processEvents()
    lines = {ln.get_label(): ln for ln in w.fig.axes[0].get_lines()}
    tuned = lines["mag(S(1,1))  (tuned: h = 2.5 mm)"]
    assert tuned.get_ydata() == pytest.approx(m.a * 1.5)
    assert lines["mag(S(1,1))"].get_linestyle() == "--"
    w.export_to(str(tmp_path / "f.csv"))
    head, a = _csv(tmp_path / "f.csv")
    assert len(head) == 3 and head[2].endswith("(tuned: h = 2.5 mm)")
    assert a[:, 2] == pytest.approx(m.a * 1.5)

    # against the variable at one frequency: the tuning range, the solved and the slider value
    w.xvar.setCurrentIndex(1)
    w.xfreq.setCurrentIndex(1)
    assert w.report.props["x_var"] == "h" and float(w.report.props["x_freq"]) == 2.5e9
    ax = w.fig.axes[0]
    curve = next(ln for ln in ax.get_lines() if ln.get_label() == "mag(S(1,1))")
    x = curve.get_xdata()
    assert x[0] == pytest.approx(1.0) and x[-1] == pytest.approx(3.0)       # 2 - 1 ... 2 + 1
    assert curve.get_ydata() == pytest.approx(m.a[1] * (1 + x - 2.0))
    assert ax.get_xlabel() == "h [mm]" and "2.5 GHz" in ax.get_title()
    vlines = [ln.get_xdata()[0] for ln in ax.get_lines() if ln.get_label().startswith("_")]
    assert 2.0 in vlines and 2.5 in vlines                                    # solved and slider value
    w.export_to(str(tmp_path / "x.csv"))
    head, a = _csv(tmp_path / "x.csv")
    assert head[0] == "h [mm]" and a[:, 0] == pytest.approx(x)
    w.export_to(str(tmp_path / "x.s1p"))
    assert not (tmp_path / "x.s1p").exists() and "Touchstone" in w.info.text()

    # a new tuning model with another variable: the open report offers it at once
    m2 = _Model(m.freq)
    m2.variables, m2.values, m2.units, m2.ranges = ["h", "w"], np.array([2.0, 5.0]), ["mm", "mm"], [(-1, 1), (-2, 2)]
    m2.sweep_variable = lambda k, i, values, dp: m.sweep_variable(k, 0, values, dp)
    ts.set_model(m2, "Setup1 : Sweep")
    assert [w.xvar.itemText(i) for i in range(w.xvar.count())] == ["Frequency", "h", "w"]
    assert w.xvar.currentText() == "h"
    w.xvar.setCurrentIndex(2)
    assert w.fig.axes[0].get_xlabel() == "w [mm]"

    # without the slider panel the model is read from the results; a table shows the same values
    ts.set_model(None, "")
    from hertzon.solver import tuning as TU
    import unittest.mock as um
    with um.patch.object(TU, "TuningModel", lambda path: m):
        (result_dir(doc, "Setup1") / "tuning_Sweep.npz").write_bytes(b"")
        t = ReportWindow(doc, Report("T", "Data Table", "Setup1 : Sweep", ["mag(S(1,1))"],
                                     props={"x_var": "h", "x_freq": "3e9"}))
        assert t.table.rowCount() == 101 and t.table.horizontalHeaderItem(0).text() == "h [mm]"
        assert float(t.table.item(100, 1).text()) == pytest.approx(m.a[2] * 2.0)


def test_a_disabled_or_unanalyzed_sweep_is_not_tuned(tmp_path):
    """The tuning panel greys out disabled sweeps and refuses a sweep without a solution: the reports draw
    the tuned curves over the stored solution, so derivatives of it could not be shown anywhere."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
    from hertzon.gui.doc import Document, result_dir
    from hertzon.gui.tuning import TuningPanel
    from hertzon.model.design import Project
    from hertzon.solver.results import ResultStore
    import unittest.mock as um
    app = QApplication.instance() or QApplication([])  # noqa: F841
    proj = Project.load(EX / "PatchAntenna.hzn")
    proj.save(tmp_path / "p.hzn")
    doc = Document(proj)
    setup = doc.design.setups[0]
    sw = setup.sweeps[0]
    sw.props["Enabled"] = "False"

    class Solver(QObject):
        finished = Signal(str, bool)

    class Win(QWidget):
        def __init__(self):
            super().__init__()
            self.doc, self.des, self.solver = doc, doc.design, Solver()
            self.compute_tuning = um.Mock(return_value="job")

    win = Win()
    panel = TuningPanel(win, setup.name, sw.name)
    item = panel.sweep.model().item(0)
    assert not item.isEnabled() and "(disabled)" in panel.sweep.itemText(0)
    assert panel.sweep_name() != sw.name                     # falls back to an enabled entry
    panel.vars.item(0).setCheckState(Qt_checked())
    with um.patch.object(QMessageBox, "information") as box:
        panel.compute()                                      # solution frequency: no solution stored yet
    assert "no solution" in box.call_args[0][2] and not win.compute_tuning.called
    sw.props["Enabled"] = "True"
    panel.select(setup.name, sw.name)
    assert panel.sweep_name() == sw.name
    panel.vars.item(0).setCheckState(Qt_checked())
    st = ResultStore(result_dir(doc, setup.name))
    import numpy as np
    st.save_sparams(sw.name, np.array([1e9]), np.zeros((1, 1, 1), complex), np.array([50.0]), [])
    with um.patch.object(QMessageBox, "information") as box:
        panel.compute()
    assert not box.called, box.call_args
    assert win.compute_tuning.called


def Qt_checked():
    from PySide6.QtCore import Qt
    return Qt.Checked
