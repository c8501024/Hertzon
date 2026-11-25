"""An interpolating sweep's support points (the frequencies it solved) are marked on its curves in a
rectangular report, as open circles in the trace colour; Axis Properties switches them off. A sweep that solved
every point (discrete) gets no extra marks."""
import os

import numpy as np
from pathlib import Path


def _window(tmp_path, solved):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.doc import Document, result_dir
    from hertzon.gui.reports import ReportWindow
    from hertzon.model.design import Project, Report
    from hertzon.solver.results import ResultStore
    app = QApplication.instance() or QApplication([])  # noqa: F841
    proj = Project.load(Path(__file__).resolve().parents[1] / "examples" / "PatchAntenna.hzn")
    proj.save(tmp_path / "p.hzn")
    doc = Document(proj)
    st = ResultStore(result_dir(doc, "Setup1"))
    ports = [{"name": "1", "number": 1, "kind": "Lumped Port"}]
    st.write_json("ports.json", ports)
    f = np.linspace(2e9, 3e9, 41)
    S = (0.1 + 0.8 * np.abs(f - 2.45e9) / 1e9)[:, None, None].astype(complex)
    st.save_sparams("Sweep", f, S, np.array([50.0]), ports, solved=solved(f))
    rep = Report("XY Plot 1", "Rectangular Plot", "Setup1 : Sweep", ["dB(S(1,1))"])
    return ReportWindow(doc, rep), rep, f


def _marks(w):
    return [ln for ln in w.fig.axes[0].get_lines() if ln.get_linestyle() == "None" and ln.get_marker() == "o"]


def test_solved_frequencies_are_marked_on_the_curve(tmp_path):
    w, rep, f = _window(tmp_path, lambda f: np.array([2e9, 2.3e9, 2.45e9, 2.71e9, 3e9]))
    marks = _marks(w)
    assert len(marks) == 1
    x, y = marks[0].get_data()
    assert np.allclose(x, [2.0, 2.3, 2.45, 2.71, 3.0])
    curve = next(ln for ln in w.fig.axes[0].get_lines() if ln not in marks)
    assert np.allclose(y, np.interp(x, *curve.get_data()))          # on the drawn curve
    assert marks[0].get_color() == curve.get_color() and marks[0].get_markerfacecolor() == "none"
    assert "_nolegend_" == marks[0].get_label()
    rep.props["solved_points"] = "False"                             # Axis Properties: switched off
    w.refresh()
    assert _marks(w) == []
    w.close()


def test_discrete_sweep_gets_no_marks(tmp_path):
    w, _, _ = _window(tmp_path, lambda f: f)
    assert _marks(w) == []
    w.close()
