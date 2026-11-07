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


def test_wave_port_with_more_modes_than_the_face_carries_says_so(tmp_path):
    """A WR-90 port at 10 GHz carries TE10 only; asking for two TE modes fails with a clear message instead of
    a division by zero in the S-parameters."""
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
    with pytest.raises(RuntimeError, match="Wave port 'WP1': 2 modes requested, the port face carries only 1"):
        B.Bridge(p, d, st, tmp_path).run()


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
