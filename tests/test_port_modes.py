"""Port modes of wave ports with several modes: Delta S in a fixed mode gauge, mode tracking between passes,
mode labels (type, rectangular guide mode, symmetry) and the port data of the MCP."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import bridge as B  # noqa: E402
from hertzon.solver import port_modes as PM  # noqa: E402


def _two_mode_guide_S(rng):
    """S of two ports with two modes each (rows P1M1, P1M2, P2M1, P2M2): mode 1 and mode 2 do not couple (a
    symmetric structure), so S[0, 1] and S[0, 3] are only noise."""
    S = np.zeros((4, 4), complex)
    S[0, 0], S[2, 2] = 0.1 - 0.45j, 0.44
    S[0, 2] = S[2, 0] = 0.54 + 0.71j
    S[1, 1], S[3, 3] = 0.11 + 0.02j, -0.05 - 0.1j
    S[1, 3] = S[3, 1] = 0.79 + 0.6j
    noise = 1e-4 * (rng.standard_normal((4, 4)) + 1j * rng.standard_normal((4, 4)))
    S[[0, 0, 1, 1, 2, 3], [1, 3, 0, 2, 3, 2]] = 0
    return S, (noise + noise.T) / 2


def test_delta_s_ignores_a_sign_flip_of_a_mode_that_does_not_couple_to_port_1():
    """Before: the sign of every mode was matched on row 1 only, so a mode that does not couple to port 1 was
    flipped by noise, and Delta S jumped to 2 between two passes with the same S (seen in a two-mode run:
    pass 6 Delta S 1.99 with every |S| and phase unchanged)."""
    rng = np.random.default_rng(1)
    S, noise = _two_mode_guide_S(rng)
    for flip in ([1, 1, 1, 1], [1, -1, 1, 1], [1, 1, 1, -1], [1, -1, 1, -1], [-1, 1, -1, 1]):
        D = np.diag(flip)
        d, dm, dp = B.aligned_delta(S + noise, D @ S @ D - noise)
        assert d < 1e-3 and dm < 1e-3, flip
    # a real change is still seen in the gauge where it is smallest
    S2 = S.copy()
    S2[1, 3] = S2[3, 1] = 0.75 + 0.6j
    d, *_ = B.aligned_delta(S, S2)
    assert d == pytest.approx(0.04, abs=1e-9)


def test_tracker_matches_modes_that_swapped_and_flipped():
    """The modes of a port that come out in the other order and with another sign are matched by their fields;
    S is then compared in the order and gauge of the previous pass."""
    rng = np.random.default_rng(2)
    e1, e2, e3 = (rng.standard_normal(30) for _ in range(3))
    tr = PM.ModeTracker()
    assert tr.match([(1, 1, e1), (1, 2, e2), (2, 1, e3)]) is None
    perm, phase, notes = tr.match([(1, 1, -e2 * 1.01), (1, 2, e1), (2, 1, -e3)])
    assert list(perm) == [1, 0, 2] and np.allclose(phase, [-1, 1, -1])
    assert notes and "port 1" in notes[0]
    S_prev = np.array([[0.1, 0.02, 0.9j], [0.02, 0.3, 0.1], [0.9j, 0.1, 0.2]])
    # the same solution in the new order and gauge: cur mode i = phase_i * prev mode perm_i, S_ij scales c_j / c_i
    S_cur = np.empty_like(S_prev)
    for i in range(3):
        for j in range(3):
            S_cur[i, j] = S_prev[perm[i], perm[j]] * phase[j] / phase[i]
    assert np.allclose(PM.to_previous_gauge(S_cur, perm, phase), S_prev)
    d, *_ = B.aligned_delta(S_prev, S_cur, (perm, phase, notes))
    assert d == pytest.approx(0, abs=1e-12)


def _rect_face(a=22.86e-3, b=10.16e-3, n=12):
    """A rectangular port face a x b in the plane y = 0 (x along a, z along b), triangulated."""
    xs, zs = np.linspace(-a / 2, a / 2, n + 1), np.linspace(-b / 2, b / 2, n + 1)
    X, Z = np.meshgrid(xs, zs, indexing="ij")
    nodes = np.vstack([X.ravel(), np.zeros(X.size), Z.ravel()])
    idx = np.arange(X.size).reshape(X.shape)
    tris = []
    for i in range(n):
        for j in range(n):
            p, q, r, s = idx[i, j], idx[i + 1, j], idx[i + 1, j + 1], idx[i, j + 1]
            tris += [(p, q, r), (p, r, s)]
    return nodes, np.array(tris).T, a, b


def test_mode_labels_name_te10_te20_and_their_symmetry():
    nodes, tris, a, b = _rect_face()
    fr = PM.face_frame(nodes, tris)
    assert PM.axis_name(fr["u"]) == "x" and PM.axis_name(fr["v"]) == "z" and PM.axis_name(fr["normal"]) == "y"

    def te(m):
        def E(x, y, z):
            ez = np.sin(m * np.pi * (np.asarray(x) + a / 2) / a)
            return np.array([0 * ez, 0 * ez, ez], complex)
        return E
    d1 = PM.describe_mode(te(1), fr, "TE", 72.3)
    assert d1["short"] == "TE10-like" and d1["symmetry"]["x"]["parity"] == "even"
    assert d1["symmetry"]["z"]["parity"] == "odd"          # E normal to the mid-plane z = 0: an electric wall
    d2 = PM.describe_mode(te(2), fr, "TE", 85.5 - 0.0j)
    assert d2["short"] == "TE20-like" and d2["symmetry"]["x"]["parity"] == "odd"
    assert PM.excitation_label(1, 2, d2) == "P1 M2 (TE20-like, beta 85.5 rad/m)"
    assert PM.excitation_label(2, 1, {"short": "TE", "beta": [0, -12.0]}) == "P2 M1 (TE, alpha 12 Np/m)"


def test_two_mode_ports_label_their_modes_for_field_plots_and_the_mcp(tmp_path):
    """A 22.86 x 15 mm guide at 12 GHz between two two-mode wave ports: the excitations of the field plots are
    labelled with port, mode and the mode they resemble, and get_results kind=ports lists beta, symmetry and a
    picture per mode."""
    pytest.importorskip("emerge")
    from hertzon.model import schema
    from hertzon.model.design import Assignment, Project, Setup
    from hertzon.solver.results import ResultStore
    from hertzon.gui.fields import excitation_choices, excitation_text
    from hertzon.mcp.tools import _port_modes
    from hertzon.mcp.protocol import Image
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", "Guide", Position="-11.43mm,0,0", XSize="22.86mm", YSize="30mm", ZSize="15mm",
                 material="vacuum")
    for i, y in enumerate(("0", "30mm"), 1):
        d.add_object("CreateRectangle", f"P{i}", Position=f"-11.43mm,{y},0", Axis="Y", XSize="22.86mm", ZSize="15mm")
        props = schema.defaults(schema.EXCITATIONS["Wave Port"])
        props.update(PortNumber=str(i), NumberOfModes="2")
        d.excitations.append(Assignment(f"WP{i}", "Wave Port", props, objects=[f"P{i}"]))
    st = Setup("Setup1")
    st.props.update(Frequency="12GHz", MaximumPasses="2", MinimumPasses="2")
    d.setups.append(st)
    B.Bridge(p, d, st, tmp_path).run()
    store = ResultStore(tmp_path)
    labels = [q.get("label") for q in store.read_json("field_sources.json")]
    assert labels[0].startswith("P1 M1 (TE10-like, beta 210.") and labels[1].startswith("P1 M2 (TE01-like, beta 139.")
    assert labels[2].startswith("P2 M1 (TE10-like") and labels[3].startswith("P2 M2 (TE01-like")
    assert store.read_json("ports.json")[0]["mode_labels"] == labels[:2]
    ch = excitation_choices(store)
    assert [n for n, _ in ch] == [1, 2, 3, 4] and ch[1][1] == "2: " + labels[1]
    assert excitation_text(store, 2) == labels[1]
    out = _port_modes(store, [], {"image": True, "port": 1})
    info, pics = out[0], out[1:]
    m1, m2 = info["ports"][0]["modes"]
    assert m1["beta_rad_per_m"] == pytest.approx(210.6, rel=0.01) and m1["propagating"]
    assert m1["symmetry"]["x"]["parity"] == "even" and m2["symmetry"]["x"]["parity"] == "odd"
    assert [e["number"] for e in info["excitations"]] == [1, 2, 3, 4]
    assert len(pics) == 2 and all(isinstance(x, Image) and x.png[:4] == b"\x89PNG" for x in pics)
    # the second pass compared the same modes in the same gauge: the empty guide is converged
    import json
    conv = json.loads((tmp_path / "convergence.json").read_text())
    assert conv["passes"][-1]["delta_s"] < 0.05


def test_field_plot_dialog_offers_every_port_mode(tmp_path, monkeypatch):
    """The excitation of a field plot is chosen among the stored excitations with their labels: with two ports of
    two modes each that are four (before: '1' and '2', the number of ports, so port 2 could not be chosen)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui import fields as Fm
    from hertzon.gui import fieldplot_dialogs as FD
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.design import FieldPlot, Project
    from hertzon.solver.results import ResultStore
    store = ResultStore(tmp_path)
    labels = ["P1 M1 (TE20-like, beta 85.5 rad/m)", "P1 M2 (TE10-like, beta 72.3 rad/m)",
              "P2 M1 (TE20-like, beta 85.5 rad/m)", "P2 M2 (TE10-like, beta 72.3 rad/m)"]
    store.write_json("field_sources.json", [{"name": f"WP{p}_M{m}", "number": 2 * (p - 1) + m, "kind": "Wave Port",
                                             "port": p, "mode": m, "label": labels[2 * (p - 1) + m - 1]}
                                            for p in (1, 2) for m in (1, 2)])
    win = MainWindow(Project())
    monkeypatch.setattr(Fm, "field_solutions", lambda doc: ["Setup1 : LastAdaptive"])
    monkeypatch.setattr(Fm, "store_for", lambda doc, sol: (store, "LastAdaptive"))
    fp = FieldPlot("E1", "Vector_E", "Setup1 : LastAdaptive")
    fp.props["port"] = "4"
    dlg = FD.FieldPlotDialog(win, "Vector_E", fp)
    assert [dlg.port.itemText(i) for i in range(dlg.port.count())] == [f"{i + 1}: {t}" for i, t in enumerate(labels)]
    assert dlg.port.currentData() == 4 and dlg.values()["props"]["port"] == "4"
    dlg.port.setCurrentIndex(1)
    assert dlg.values()["props"]["port"] == "2"
