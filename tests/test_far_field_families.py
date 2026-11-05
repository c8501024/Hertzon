"""Families for far field reports (#221): Radiation Pattern and Antenna Parameters draw one curve per variation
of a parametric setup, a 3D Polar Plot shows the variation chosen under Variation. The variations hold the
surface fields of a z-directed Hertzian dipole, the second one with twice the current."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

EX = Path(__file__).resolve().parents[1] / "examples"
F0 = 1e9


def _hertzian(st, il: float):
    """Surface fields of a Hertzian dipole (current moment il along z) on a box around the origin, as the
    solver stores them (huygens_LastAdaptive.npz), with S11 = 0 (all incident power accepted)."""
    from hertzon.solver.bridge import C0, box_quadrature
    eta, k = 376.730313668, 2 * np.pi * F0 / C0
    vis, wns = box_quadrature(np.array([-0.1, -0.1, -0.1, 0.1, 0.1, 0.1]), 0.02)
    x, y, z = vis
    r = np.sqrt(x ** 2 + y ** 2 + z ** 2)
    th, ph = np.arccos(z / r), np.arctan2(y, x)
    g = np.exp(-1j * k * r)
    a = 1 + 1 / (1j * k * r)
    Er = eta * il * np.cos(th) / (2 * np.pi * r ** 2) * a * g
    Et = 1j * eta * k * il * np.sin(th) / (4 * np.pi * r) * (a - 1 / (k * r) ** 2) * g
    Hp = 1j * k * il * np.sin(th) / (4 * np.pi * r) * a * g
    rh = np.array([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)])
    tz = np.array([np.cos(th) * np.cos(ph), np.cos(th) * np.sin(ph), -np.sin(th)])
    pz = np.array([-np.sin(ph), np.cos(ph), np.zeros_like(ph)])
    E, H = Er * rh + Et * tz, Hp * pz
    prad = float(np.real(np.sum(0.5 * np.cross(E.T, np.conj(H).T).T * wns)))
    ports = [{"name": "1", "number": 1, "kind": "Lumped Port"}]
    st.write_json("ports.json", ports)
    st.save_sparams("LastAdaptive", np.array([F0]), np.zeros((1, 1, 1), complex), np.array([50.0]), ports)
    st.save_npz("huygens_LastAdaptive.npz", vis=vis, wns=wns, stamp=np.array([1]), freq_0=np.array([F0]),
                E_0_1=E, H_0_1=H, Prad_0_1=np.array([prad]))


def test_far_field_reports_show_the_families(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.doc import Document
    from hertzon.gui.reports import ReportWindow, family_solution_list
    from hertzon.model.design import ParametricSetup, Project, Report
    from hertzon.solver.parametric import parametric_dir
    from hertzon.solver.results import ResultStore
    app = QApplication.instance() or QApplication([])
    proj = Project.load(EX / "Dipole.hzn")
    proj.designs = [proj.design("HalfWave1GHz")]
    des = proj.designs[0]
    des.parametrics.append(ParametricSetup("Par1", setups=["Setup1"]))
    proj.save(tmp_path / "d.hzn")
    doc = Document(proj)
    out = parametric_dir(str(tmp_path / "d.hzn"), des.name, "Par1")
    rows = []
    for i, (larm, il) in enumerate((("70mm", 1.0), ("75mm", 2.0))):
        folder = out / "variations" / f"v{i}"
        _hertzian(ResultStore(folder / "Setup1"), il)
        rows.append({"id": f"v{i}", "variables": {"Larm": larm}, "state": {"Setup1": "done"}})
    out.mkdir(parents=True, exist_ok=True)
    (out / "table.json").write_text(json.dumps({"variables": ["Larm"], "variations": rows}))
    assert family_solution_list(doc, "Par1", "Radiation Pattern") == ["Setup1 : LastAdaptive"]

    # Radiation Pattern: a cut per variation, the second with twice the field
    w = ReportWindow(doc, Report("RP", "Radiation Pattern", "Setup1 : LastAdaptive", ["rETotal"],
                                 props={"families": "Par1", "plot": "Rectangular", "cut": "Phi", "cut_value": "0"}))
    labels = [ln.get_label() for ln in w.fig.axes[0].get_lines()]
    assert labels == ["rETotal  Larm='70mm'", "rETotal  Larm='75mm'"]
    _, curves = w._export_curves()
    assert [c[0].split("  ")[-1] for c in curves] == ["Larm='70mm'", "Larm='75mm'"]
    assert np.nanmax(curves[1][2]) == pytest.approx(2 * np.nanmax(curves[0][2]), rel=1e-6)

    # Antenna Parameters: a curve per variation; a Hertzian dipole has a directivity of 1.5
    a = ReportWindow(doc, Report("AP", "Antenna Parameters Table", "Setup1 : LastAdaptive", ["PeakDirectivity"],
                                 props={"families": "Par1"}))
    _, curves = a._export_curves()
    assert len(curves) == 2 and all(c[2][0] == pytest.approx(1.5, rel=0.02) for c in curves)

    # 3D Polar Plot: one variation at a time, chosen under Variation
    p = ReportWindow(doc, Report("3D", "3D Polar Plot", "Setup1 : LastAdaptive", ["rETotal"],
                                 props={"families": "Par1"}))
    assert [p.ff_var.itemText(i) for i in range(p.ff_var.count())] == ["Larm='70mm'", "Larm='75mm'"]
    m1 = float(p.info.text().split("=")[1].split()[0])
    p.ff_var.setCurrentText("Larm='75mm'")
    app.processEvents()
    assert p.report.props["variation"] == "Larm='75mm'"
    assert float(p.info.text().split("=")[1].split()[0]) == pytest.approx(2 * m1, rel=1e-3)
