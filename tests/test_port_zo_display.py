"""Port impedance Zo and propagation constant Gamma of the wave port modes in the Solution Data window (last
adaptive pass, every pass, sweeps), and the frequencies an interpolating sweep sampled in the Message Manager."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from hertzon.model.design import Project


def _project(tmp_path):
    ex = Path(__file__).resolve().parents[1] / "examples"
    p = Project.load(ex / "PatchAntenna.hzn")
    p.save(tmp_path / "p.hzn")
    return Project.load(tmp_path / "p.hzn")


def _solution(p):
    """A solved setup with one wave port of two modes: Gamma and Zo at the adaptive frequency and per pass."""
    from hertzon.solver.results import ResultStore, results_dir
    d = p.designs[0]
    st = ResultStore(results_dir(p.path, d.name, "Setup1"))
    st.write_json("ports.json", [{"number": 1, "name": "WP1", "kind": "Wave Port", "modes": 2}])
    st.write_json("status.json", {"state": "done"})
    f = np.array([2.45e9])
    S = np.eye(2, dtype=complex)[None] * 0.1
    gamma = np.array([[0.5 + 80j, 2.0 + 60j]])
    zo = np.array([[50.3 + 0.2j, 377.0 + 0j]])
    st.save_sparams("LastAdaptive", f, S, np.array([50.0, 50.0]), [{"number": 1}], gamma=gamma, zo=zo)

    def row(n, z):
        return {"pass": n, "tets": 1000 * n, "S": [[[0.1, 0.0], [0, 0], [0, 0], [0.1, 0.0]]],
                "zo": [[[z, 0.0], [377.0, 0.0]]], "gamma": [[[0.5, 80.0], [2.0, 60.0]]]}
    st.write_json("convergence.json", {"converged": True, "max_delta_s": 0.02, "frequencies": [2.45e9],
                                       "passes": [row(1, 47.0), row(2, 50.3)]})
    return st


def test_adaptive_pass_data_has_zo_and_gamma_of_every_pass(tmp_path):
    from hertzon.gui.reports import adaptive_pass_data
    st = _solution(_project(tmp_path))
    d = adaptive_pass_data(st, "AdaptivePasses")
    assert d["zo"].shape == (2, 2) and d["gamma"].shape == (2, 2)
    assert d["zo"][0, 0] == pytest.approx(47.0) and d["zo"][1, 0] == pytest.approx(50.3)
    assert d["gamma"][1, 1] == pytest.approx(2.0 + 60j)
    # solutions of older versions have no port modes per pass: none, not zeros
    conv = st.read_json("convergence.json")
    for r in conv["passes"]:
        r.pop("zo")
    st.write_json("convergence.json", conv)
    assert "zo" not in adaptive_pass_data(st, "AdaptivePasses")


def test_solution_data_lists_zo_and_gamma_of_the_port_modes(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QTableWidget
    from hertzon.gui.doc import Document
    from hertzon.gui.panels import SolutionDataDialog
    app = QApplication.instance() or QApplication([])  # noqa: F841
    p = _project(tmp_path)
    _solution(p)
    dlg = SolutionDataDialog(Document(p), "Setup1")
    tab = dlg.tabs.widget(2)
    sol, fsel, kind, fmt = tab.findChildren(QComboBox)[:4]
    table = tab.findChildren(QTableWidget)[0]
    assert [kind.itemText(i) for i in range(kind.count())] == ["S", "Y", "Z", "Gamma", "Zo"]
    assert sol.currentText() == "LastAdaptive"
    kind.setCurrentText("Zo")
    fmt.setCurrentText("Real/Imag")
    hdr = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert hdr == ["Freq [GHz]", "Zo WP1_M1 [ohm]", "Zo WP1_M2 [ohm]"]
    assert table.rowCount() == 1 and table.item(0, 1).text() == "50.3 + j0.2"
    kind.setCurrentText("Gamma")
    assert table.item(0, 2).text() == "2 + j60"
    # every adaptive pass: how the port impedance settles
    sol.setCurrentText("Adaptive Passes")
    assert kind.currentText() == "Gamma"
    kind.setCurrentText("Zo")
    fsel.setCurrentIndex(0)
    assert table.rowCount() == 2
    assert [table.item(r, 1).text() for r in range(2)] == ["47 + j0", "50.3 + j0"]
    # back to S: the matrix again
    kind.setCurrentText("S")
    sol.setCurrentText("LastAdaptive")
    assert table.rowCount() == 2 and table.columnCount() == 2
    dlg.close()


def test_interpolating_sweep_lists_its_sampled_frequencies():
    pytest.importorskip("emerge")
    from hertzon.solver.bridge import solved_frequencies_text
    txt = solved_frequencies_text("Sweep", [3e9, 1e9, 2.5e9])
    assert txt == "Sweep 'Sweep': 3 adaptively sampled frequencies (GHz): 1, 2.5, 3"
    assert "1 adaptively sampled frequency " in solved_frequencies_text("S", [1e9])
