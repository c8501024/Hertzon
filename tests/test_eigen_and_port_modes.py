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
