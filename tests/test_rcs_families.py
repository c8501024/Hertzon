"""Families for the monostatic RCS report: with one frequency per variation the RCS is drawn over the swept
variable (sphere radius), with several frequencies one curve per variation. The variations hold the
backscattered far field of spheres of known RCS."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

EX = Path(__file__).resolve().parents[1] / "examples"


def _variation(st, sphere, freqs, sigmas):
    """Far field of a solved plane-wave setup with the monostatic RCS sigmas (m^2) at freqs, as the solver
    stores it (boundary fields + cached far field of the sphere)."""
    st.save_npz("huygens_LastAdaptive.npz", stamp=np.array([1]), freq_0=np.array([freqs[0]]))
    sig = json.dumps({k: str(v) for k, v in sorted(sphere.props.items())}) + "1"
    ff = {"sig": np.array([sig]), "mono_dir": np.array([np.pi, 0.0]), "E0": np.array([1.0])}
    for k, (f, s) in enumerate(zip(freqs, sigmas)):
        ff[f"freq_{k}"] = np.array([f])
        ff[f"Emono_{k}"] = np.array([np.sqrt(s / (4 * np.pi)), 0, 0], complex)   # theta part at the backscatter
    st.save_npz(f"farfield_{sphere.name}_LastAdaptive.npz", **ff)


def _setup(tmp_path, rows):
    from hertzon.gui.doc import Document
    from hertzon.model.design import ParametricSetup, Project
    from hertzon.solver.parametric import parametric_dir
    from hertzon.solver.results import ResultStore
    proj = Project.load(EX / "SphereRCS.hzn")
    des = proj.designs[0]
    des.parametrics.append(ParametricSetup("ParR", setups=["Setup1"]))
    proj.save(tmp_path / "s.hzn")
    doc = Document(proj)
    out = parametric_dir(str(tmp_path / "s.hzn"), des.name, "ParR")
    table = []
    for i, (texts, freqs, sigmas) in enumerate(rows):
        _variation(ResultStore(out / "variations" / f"v{i}" / "Setup1"), des.spheres[0], freqs, sigmas)
        table.append({"id": f"v{i}", "variables": texts, "state": {"Setup1": "done"}})
    out.mkdir(parents=True, exist_ok=True)
    (out / "table.json").write_text(json.dumps({"variables": list(rows[0][0]), "variations": table}))
    return doc


def test_monostatic_rcs_over_the_swept_variable(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.reports import ReportWindow, family_solution_list
    from hertzon.model.design import Report
    QApplication.instance() or QApplication([])
    # unsorted on purpose: the curve is drawn in the order of the variable
    sig = {"100mm": 0.0448, "30mm": 0.00353, "250mm": 0.154}
    doc = _setup(tmp_path, [({"R": r}, [1e9], [s]) for r, s in sig.items()])
    assert family_solution_list(doc, "ParR", "Monostatic RCS") == ["Setup1 : LastAdaptive"]

    w = ReportWindow(doc, Report("M", "Monostatic RCS", "Setup1 : LastAdaptive", ["dBsm(MonostaticRCSTotal)"],
                                 props={"families": "ParR"}))
    assert w.families is not None and w.families.currentText() == "ParR"
    ax = w.fig.axes[0]
    assert ax.get_xlabel() == "R [mm]"
    (line,) = ax.get_lines()
    assert list(line.get_xdata()) == [30, 100, 250]
    assert line.get_ydata() == pytest.approx(10 * np.log10([0.00353, 0.0448, 0.154]), abs=1e-9)
    xl, curves = w._export_curves()
    assert xl == "R [mm]" and len(curves) == 1 and list(curves[0][1]) == [30, 100, 250]

    t = ReportWindow(doc, Report("T", "Monostatic RCS Table", "Setup1 : LastAdaptive", ["MonostaticRCSTotal"],
                                 props={"families": "ParR"}))
    assert [t.table.item(r, 0).text() for r in range(t.table.rowCount())] == ["30", "100", "250"]
    assert float(t.table.item(1, 1).text()) == pytest.approx(0.0448)


def test_monostatic_rcs_families_over_frequency(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.reports import ReportWindow
    from hertzon.model.design import Report
    QApplication.instance() or QApplication([])
    doc = _setup(tmp_path, [({"R": "50mm"}, [1e9, 2e9], [0.01, 0.02]),
                            ({"R": "60mm"}, [1e9, 2e9], [0.03, 0.04])])
    w = ReportWindow(doc, Report("M", "Monostatic RCS", "Setup1 : LastAdaptive", ["MonostaticRCSTotal"],
                                 props={"families": "ParR"}))
    labels = [ln.get_label() for ln in w.fig.axes[0].get_lines()]
    assert labels == ["MonostaticRCSTotal  R='50mm'", "MonostaticRCSTotal  R='60mm'"]
    assert w.fig.axes[0].get_xlabel() == "Freq [GHz]"
    assert list(w.fig.axes[0].get_lines()[1].get_ydata()) == pytest.approx([0.03, 0.04])
