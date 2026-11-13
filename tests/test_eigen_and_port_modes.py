"""Eigenmode setups and wave ports that ask for more modes than the port face carries."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

em = pytest.importorskip("emerge")

from hertzon.model import schema  # noqa: E402
from hertzon.model.design import Assignment, Project, Setup  # noqa: E402
from hertzon.solver import bridge as B  # noqa: E402

A, BB, C = 22.86e-3, 10.16e-3, 30e-3      # WR-90 cavity: TE101 at 8.25 GHz, TE102 at 11.9 GHz


def _cavity():
    p = Project()
    d = p.designs[0]
    d.settings["SolutionType"] = "Eigenmode"
    d.add_object("CreateBox", "Cav", Position="0,0,0", XSize="22.86mm", YSize="10.16mm", ZSize="30mm",
                 material="vacuum")
    return p, d


def test_eigen_setup_sizes_the_mesh_at_its_minimum_frequency():
    p, d = _cavity()
    st = Setup("Eigen1", props=schema.defaults(schema.EIGEN_SETUP), eigen=True)
    st.props["MinimumFrequency"] = "4GHz"
    assert B.adaptive_frequencies(st, d.evaluator(p)) == [4e9]


def test_eigenmodes_found_with_the_minimum_frequency_below_the_first_mode(tmp_path):
    """Searching at a minimum frequency far below the first resonance returns only the static null space
    (f = 0); the search frequency has to climb until the wanted modes are found."""
    p, d = _cavity()
    st = Setup("Eigen1", props=schema.defaults(schema.EIGEN_SETUP), eigen=True)
    st.props.update(MinimumFrequency="2GHz", NumberOfModes="2")
    d.setups.append(st)
    B.Bridge(p, d, st, tmp_path).run()
    modes = json.loads((tmp_path / "eigenmodes.json").read_text())
    f = [m["freq"] for m in modes]
    c0 = 299792458.0
    te101 = c0 / 2 * ((1 / A) ** 2 + (1 / C) ** 2) ** 0.5
    assert len(f) == 2 and f == sorted(f)
    assert abs(f[0] / te101 - 1) < 0.02
    assert all(m["Q"] == float("inf") for m in modes)          # lossless walls and filling


def test_eigenmode_fields_are_saved_one_frequency_per_mode(tmp_path):
    """Field plots of an eigenmode solution: every mode is a frequency of solution LastAdaptive, excitation 1,
    and the TE101 field is E_y ~ sin(pi x / a) sin(pi z / c)."""
    import numpy as np

    from hertzon.solver.results import ResultStore
    p, d = _cavity()
    st = Setup("Eigen1", props=schema.defaults(schema.EIGEN_SETUP), eigen=True)
    st.props.update(MinimumFrequency="2GHz", NumberOfModes="2")
    d.setups.append(st)
    B.Bridge(p, d, st, tmp_path).run()
    modes = json.loads((tmp_path / "eigenmodes.json").read_text())
    f = ResultStore(tmp_path).npz("fields_LastAdaptive.npz")
    assert [float(f[f"freq_{k}"][0]) for k in range(2)] == pytest.approx([m["freq"] for m in modes])
    m = np.load(tmp_path / "mesh.npz")
    npt = 10 if "tet10" in f else 4
    P = m["nodes"][m["tets"]].reshape(-1, 3)
    E = np.asarray(f["E_0_1"]).reshape(len(m["tets"]), npt, 3)[:, :4].reshape(-1, 3)
    ref = np.sin(np.pi * P[:, 0] / A) * np.sin(np.pi * P[:, 2] / C)
    assert abs(np.vdot(ref, E[:, 1])) / np.linalg.norm(ref) / np.linalg.norm(E[:, 1]) > 0.98
    assert np.linalg.norm(E.imag) < 1e-6 * np.linalg.norm(E.real)     # phase 0 shows the real mode


def test_wave_port_mode_below_cutoff_decays_and_says_so(tmp_path, capsys):
    """A WR-90 port at 10 GHz carries TE10 only; a second TE mode asked for is TE20 below cutoff. Since EMerge
    767fb7b it decays at the port (gamma = alpha = sqrt(kc^2 - k0^2), no power) instead of failing the
    S-parameters, and the log says so."""
    import numpy as np

    from hertzon.solver.results import ResultStore
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", "Guide", Position="0,0,0", XSize="22.86mm", YSize="20mm", ZSize="10.16mm",
                 material="vacuum")
    for i, y in enumerate(("0", "20mm"), 1):
        d.add_object("CreateRectangle", f"P{i}", Position=f"0,{y},0", Axis="Y", XSize="22.86mm", ZSize="10.16mm")
        props = schema.defaults(schema.EXCITATIONS["Wave Port"])
        props.update(PortNumber=str(i), NumberOfModes="2" if i == 1 else "1", ModeType="TE")
        d.excitations.append(Assignment(f"WP{i}", "Wave Port", props, objects=[f"P{i}"]))
    st = Setup("Setup1")
    st.props.update(Frequency="10GHz", MaximumPasses="1", MinimumPasses="1")
    d.setups.append(st)
    B.Bridge(p, d, st, tmp_path).run()
    s = ResultStore(tmp_path).sparams("LastAdaptive")
    S, gamma = np.asarray(s["S"])[0], np.asarray(s["gamma"])[0]
    k0, kc20 = 2 * np.pi * 10e9 / B.C0, 2 * np.pi / 22.86e-3
    assert gamma[1].real == pytest.approx(np.sqrt(kc20 ** 2 - k0 ** 2), rel=0.02)     # TE20 decays
    assert abs(gamma[1].imag) < 1e-6 * abs(gamma[1].real)
    assert abs(S[2, 0]) == pytest.approx(1, abs=0.01)                                  # TE10 passes
    assert abs(S[2, 1]) < 0.01 and abs(S[1, 0]) < 0.01                                 # TE20 carries nothing
    assert "Wave port 'WP1': mode 2 is below cutoff at the adaptive frequency 10 GHz" in capsys.readouterr().out


def test_two_mode_wave_port_absorbs_modes_with_different_propagation_constants(tmp_path):
    """A 22.86 x 15 mm guide at 12 GHz carries TE10 (beta 210.6 rad/m) and TE01 (beta 139.3 rad/m). An empty
    guide between two two-mode wave ports reflects neither mode, and each S-matrix row has its own gamma."""
    import numpy as np
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
    z = np.load(tmp_path / "sparams_LastAdaptive.npz")
    S = np.abs(z["S"][0])
    assert np.all(np.diag(S) < 0.05)                      # no reflection of either mode
    assert S[2, 0] > 0.98 and S[3, 1] > 0.98              # both modes pass
    k0 = 2 * np.pi * 12e9 / 299792458.0
    beta = [np.sqrt(k0 ** 2 - (np.pi / 22.86e-3) ** 2), np.sqrt(k0 ** 2 - (np.pi / 15e-3) ** 2)]
    assert np.allclose(np.imag(z["gamma"][0]), beta * 2, rtol=0.01)


def test_eigenmode_table_report_lists_the_modes(tmp_path, monkeypatch):
    """Results > Eigenmode Parameters Report > Data Table: one row per mode, frequency in GHz and Q."""
    import numpy as np

    from hertzon.gui import reports as R
    folder = tmp_path / "Eigen1"
    folder.mkdir()
    (folder / "eigenmodes.json").write_text(json.dumps([{"freq": 8.25e9, "Q": float("inf")},
                                                        {"freq": 11.9e9, "Q": 2000.0}]))
    monkeypatch.setattr(R, "result_dir", lambda doc, setup: tmp_path / setup)
    p, d = _cavity()
    p.path = str(tmp_path / "p.hzn")
    d.setups.append(Setup("Eigen1", props=schema.defaults(schema.EIGEN_SETUP), eigen=True))
    doc = type("Doc", (), {"project": p, "design": d})()
    assert R.eigen_solution_list(doc) == ["Eigen1 : LastAdaptive"]
    modes = R.eigen_table(doc, "Eigen1 : LastAdaptive")
    assert [m["freq"] for m in modes] == [8.25e9, 11.9e9] and np.isinf(modes[0]["Q"])


def test_cutoff_of_a_two_mode_wave_port_between_solved_frequencies():
    """EMerge's WavePort returns the beta of the nearest solved frequency; the interpolating sweep finds the TE20
    cutoff of WR-90 (13.114 GHz) between two solved frequencies from the extrapolated beta (mode_beta_at)."""
    em.cleanup()
    sim = em.Simulation("wg_cutoff", loglevel="ERROR")
    box = em.geo.Box(A, BB, 0.1)
    sim.commit_geometry()
    sim.mw.set_frequency(40e9)
    sim.mw.set_resolution(0.2)
    sim.generate_mesh()
    for i, face in enumerate(("-z", "+z")):
        sim.mw.bc.WavePort(box.face(face), i + 1, number_of_modes=2, modetype=None)
    sim.mw.set_frequency([20e9, 30e9])
    sim.mw.run_sweep()
    cuts = B.wave_port_cutoffs(sim, 8e9, 40e9)
    em.cleanup()
    fc = 299792458 / A                      # TE20
    assert len(cuts) == 1 and cuts[0][0] == pytest.approx(fc, rel=2e-3)
    assert sorted(cuts[0][1]) == ["wave port 1 mode 2", "wave port 2 mode 2"]


def test_fields_are_stored_per_port_mode_for_edit_sources(tmp_path):
    """#437: with wave ports of several modes the fields are stored per port mode (field_sources.json, one entry
    per S row), so Edit Sources lists every mode and a field plot can excite TE01 instead of TE10."""
    import numpy as np

    from hertzon.model import sources as SRC
    from hertzon.solver.ffquant import excitation_weights
    from hertzon.solver.results import ResultStore
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", "Guide", Position="-11.43mm,0,0", XSize="22.86mm", YSize="30mm", ZSize="15mm",
                 material="vacuum")
    for i, y in enumerate(("0", "30mm"), 1):
        d.add_object("CreateRectangle", f"P{i}", Position=f"-11.43mm,{y},0", Axis="Y", XSize="22.86mm", ZSize="15mm")
        props = schema.defaults(schema.EXCITATIONS["Wave Port"])
        props.update(PortNumber=str(i), NumberOfModes="2" if i == 1 else "1")
        d.excitations.append(Assignment(f"WP{i}", "Wave Port", props, objects=[f"P{i}"]))
    st = Setup("Setup1")
    st.props.update(Frequency="12GHz", MaximumPasses="1", MinimumPasses="1", SaveFields="True")
    d.setups.append(st)
    B.Bridge(p, d, st, tmp_path).run()
    store = ResultStore(tmp_path)
    fs = store.read_json("field_sources.json")
    assert [(q["name"], q["number"], q["port"], q["mode"]) for q in fs] == \
        [("WP1_M1", 1, 1, 1), ("WP1_M2", 2, 1, 2), ("WP2", 3, 2, 1)]
    assert SRC.field_names(store) == ["WP1_M1", "WP1_M2", "WP2"]
    f = store.npz("fields_LastAdaptive.npz")
    assert all(f"E_0_{n}" in f for n in (1, 2, 3))
    # the two modes of WP1 are different fields: the guide is 22.86 mm along x and 15 mm along z, so TE10 has
    # E along z and TE01 along x
    e1, e2 = np.abs(f["E_0_1"]), np.abs(f["E_0_2"])
    assert np.sum(e1[:, 2] ** 2) > 10 * np.sum(e1[:, 0] ** 2)
    assert np.sum(e2[:, 0] ** 2) > 10 * np.sum(e2[:, 2] ** 2)
    # Edit Sources: power on the second mode of WP1 only
    SRC.set_(d, {"WP1_M1": {"mag": 0.0, "phase": 0.0}, "WP1_M2": {"mag": 2.0, "phase": 90.0},
                 "WP2": {"mag": 0.0, "phase": 0.0}}, "Total")
    w = excitation_weights(d, fs)
    assert set(w) == {2} and w[2] == pytest.approx(np.sqrt(2) * 1j)
