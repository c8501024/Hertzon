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
