"""Families against a swept variable or another trace (Results > Rectangular Plot / Data Table, X axis): the
coax lab plots alpha over ratio and over re(Zo(1)) at one frequency, one curve per value of epsr."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

EX = Path(__file__).resolve().parents[1] / "examples"
RATIOS = ["1.5", "3.59", "10"]
EPSR = ["1", "2.1"]


def _zo(ratio, epsr):
    return 60 / np.sqrt(float(epsr)) * np.log(float(ratio))


def _alpha(ratio, epsr, f):
    return 0.01 * (1 + float(ratio) / 10) * np.sqrt(float(epsr)) * f / 2.45e9


def _setup(tmp_path):
    from hertzon.gui.doc import Document
    from hertzon.model.design import ParametricSetup, Project
    from hertzon.solver.parametric import parametric_dir
    from hertzon.solver.results import ResultStore
    proj = Project.load(EX / "Coax.hzn")
    des = proj.designs[0]
    setup = des.setups[0].name
    des.parametrics.append(ParametricSetup("Sweep_ratio", setups=[setup]))
    proj.save(tmp_path / "c.hzn")
    doc = Document(proj)
    out = parametric_dir(str(tmp_path / "c.hzn"), des.name, "Sweep_ratio")
    ports = [{"number": 1, "name": "1", "kind": "Wave Port", "modes": 1},
             {"number": 2, "name": "2", "kind": "Wave Port", "modes": 1}]
    f = np.array([2e9, 2.45e9, 3e9])
    table, i = [], 0
    for e in EPSR:
        for r in RATIOS:
            st = ResultStore(out / "variations" / f"v{i}" / setup)
            S = np.zeros((len(f), 2, 2), complex)
            S[:, 1, 0] = S[:, 0, 1] = np.exp(-_alpha(r, e, f) * 0.1)
            zo = np.full((len(f), 2), _zo(r, e), complex)
            st.save_sparams("Sweep", f, S, zo.real, ports, zo=zo)
            table.append({"id": f"v{i}", "variables": {"ratio": r, "epsr": e}, "state": {setup: "done"}})
            i += 1
    out.mkdir(parents=True, exist_ok=True)
    (out / "table.json").write_text(json.dumps({"variables": ["ratio", "epsr"], "variations": table}))
    return doc, f"{setup} : Sweep"


def test_families_against_a_variable_and_a_trace(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.reports import ReportWindow
    from hertzon.model.design import Report
    QApplication.instance() or QApplication([])
    doc, sol = _setup(tmp_path)
    alpha, zo = "-ln(mag(S(2,1)))/0.1", "re(Zo(1))"
    rep = Report("A", "Rectangular", sol, [zo, alpha], props={"families": "Sweep_ratio"})
    w = ReportWindow(doc, rep)
    items = [w.xvar.itemData(i) for i in range(w.xvar.count())]
    assert items == ["", "var:ratio", "var:epsr", "expr:" + zo, "expr:" + alpha]
    assert [w.xfreq.itemData(i) for i in range(w.xfreq.count())] == [2e9, 2.45e9, 3e9]

    # alpha (and Zo) over ratio at 2.45 GHz: one curve per epsr
    w.xfreq.setCurrentIndex(1)
    w.xvar.setCurrentIndex(1)
    assert rep.props["family_x"] == "var:ratio" and float(rep.props["family_freq"]) == 2.45e9
    ax = w.fig.axes[0]
    assert ax.get_xlabel() == "ratio"
    lines = {ln.get_label(): ln for ln in ax.get_lines() if not ln.get_label().startswith("_")}
    assert set(lines) == {f"{t}  epsr='{e}'" for t in (zo, alpha) for e in EPSR}
    ln = lines[f"{alpha}  epsr='2.1'"]
    assert list(ln.get_xdata()) == [1.5, 3.59, 10]
    assert ln.get_ydata() == pytest.approx([_alpha(r, "2.1", 2.45e9) for r in RATIOS], rel=1e-9)
    assert "2.45 GHz" in ax.get_title()

    # alpha over re(Zo(1)): ratio runs along the curves, epsr makes the families; Zo is the x axis, no curve
    w.xvar.setCurrentIndex(3)
    ax = w.fig.axes[0]
    assert ax.get_xlabel().startswith(zo)
    lines = {ln.get_label(): ln for ln in ax.get_lines() if not ln.get_label().startswith("_")}
    assert set(lines) == {f"{alpha}  epsr='{e}'" for e in EPSR}
    ln = lines[f"{alpha}  epsr='1'"]
    assert ln.get_xdata() == pytest.approx([_zo(r, "1") for r in RATIOS], rel=1e-9)
    assert ln.get_ydata() == pytest.approx([_alpha(r, "1", 2.45e9) for r in RATIOS], rel=1e-9)
    xl, curves = w._export_curves()
    assert xl.startswith(zo) and len(curves) == 2

    # another frequency
    w.xfreq.setCurrentIndex(2)
    ln = {ln.get_label(): ln for ln in w.fig.axes[0].get_lines()}[f"{alpha}  epsr='1'"]
    assert ln.get_ydata() == pytest.approx([_alpha(r, "1", 3e9) for r in RATIOS], rel=1e-9)

    # Values... filter: only air, no family label left
    rep.props["family_values"] = json.dumps({"epsr": ["1"]})
    w.refresh()
    assert [ln.get_label() for ln in w.fig.axes[0].get_lines()] == [alpha]

    # back to frequency: one curve per variation as before
    rep.props["family_values"] = ""
    w.xvar.setCurrentIndex(0)
    assert len([ln for ln in w.fig.axes[0].get_lines() if not ln.get_label().startswith("_")]) == 12

    # the data table of the same choice
    t = ReportWindow(doc, Report("T", "Data Table", sol, [alpha],
                                 props={"families": "Sweep_ratio", "family_x": "var:ratio",
                                        "family_freq": "2450000000.0"}))
    assert t.table.columnCount() == 3 and t.table.rowCount() == 3
    assert [t.table.item(r, 0).text() for r in range(3)] == ["1.5", "3.59", "10"]
    assert float(t.table.item(1, 2).text()) == pytest.approx(_alpha("3.59", "2.1", 2.45e9), rel=1e-5)


def test_parametric_results_window_has_no_calculation_plot(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.optimetrics import ParametricResults
    from hertzon.solver.parametric import parametric_dir
    QApplication.instance() or QApplication([])
    doc, _ = _setup(tmp_path)
    w = ParametricResults(None, "Sweep_ratio", parametric_dir(str(tmp_path / "c.hzn"), doc.design_name, "Sweep_ratio"))
    heads = [w.table.horizontalHeaderItem(c).text() for c in range(w.table.columnCount())]
    assert heads == ["#", "ratio", "epsr"] and w.table.rowCount() == 6       # no calculation columns
    assert not hasattr(w, "ax")
    w.timer.stop()
    w.close()
