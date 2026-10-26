"""Terminal solution: terminal S-parameters from the port modes, differential pairs, Zo and Gamma as report
quantities, Solve Ports Only and the stored port fields."""
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import terminals as T  # noqa: E402
from hertzon.solver.expressions import eval_expr  # noqa: E402


def _random_passive(n, seed=0):
    rng = np.random.default_rng(seed)
    A = rng.normal(size=(n, n)) + 1j * rng.normal(size=(n, n))
    A = A + A.T                                   # reciprocal
    return 0.9 * A / np.linalg.norm(A, 2)          # passive


def test_impedance_round_trip():
    S = _random_passive(3)[None]
    zr = [50.0, 75.0, 20.0]
    assert np.allclose(T.s_from_z(T.z_from_s(S, zr), zr), S)


def test_one_mode_per_terminal_is_a_renormalization():
    """One conductor per port: D = sqrt(Z0) makes the terminal S-parameters the modal ones renormalized."""
    S = _random_passive(2, 1)
    z0 = np.array([36.0, 81.0])
    St, Zt = T.terminal_s(S, np.diag(np.sqrt(z0)), [50.0, 50.0])
    assert np.allclose(Zt, T.z_from_s(S[None], z0)[0])
    assert np.allclose(St, T.s_from_z(T.z_from_s(S[None], z0), [50.0, 50.0])[0])


def test_terminal_s_does_not_depend_on_the_mode_basis():
    """Mixing or flipping the modes of a port (degenerate modes) leaves the terminal S-parameters unchanged."""
    S = _random_passive(2, 2)
    N = np.array([[12.0, 3.0], [-4.0, 11.0]]) / np.sqrt(2)
    St, _ = T.terminal_s(S, N, 50.0)
    c, s = np.cos(0.7), np.sin(0.7)
    R = np.array([[c, -s], [s, c]]) @ np.diag([1.0, -1.0])       # orthogonal mode transformation
    St2, _ = T.terminal_s(R.T @ S @ R, N @ R, 50.0)
    assert np.allclose(St, St2)


def test_mixed_mode_of_two_uncoupled_lines():
    """Two matched, uncoupled 50 ohm lines: differential and common mode pass unchanged at 100 / 25 ohm,
    with no mode conversion."""
    S = np.zeros((1, 4, 4), complex)
    S[0, 0, 2] = S[0, 2, 0] = S[0, 1, 3] = S[0, 3, 1] = np.exp(-1j * 0.3)
    Smm, refs, labels = T.mixed_mode(S, 50.0, [(0, 1), (2, 3)], [100.0, 100.0], [25.0, 25.0])
    i = {lab: k for k, lab in enumerate(labels)}
    assert np.isclose(Smm[0, i["Diff", 1], i["Diff", 0]], np.exp(-1j * 0.3))
    assert np.isclose(Smm[0, i["Comm", 1], i["Comm", 0]], np.exp(-1j * 0.3))
    assert abs(Smm[0, i["Comm", 1], i["Diff", 0]]) < 1e-12
    assert abs(Smm[0, i["Diff", 0], i["Diff", 0]]) < 1e-12
    assert np.allclose(refs, [100, 25, 100, 25])


def test_report_quantities():
    f = np.array([1e9, 2e9])
    S = np.zeros((2, 3, 3), complex)
    S[:, 0, 1] = S[:, 1, 0] = 0.5
    S[:, 2, 2] = 0.2
    data = {"freq": f, "S": S, "z0": np.full(3, 50.0), "zo": np.array([[49.0, 51.0, 50.0]] * 2),
            "gamma": np.array([[0.1 + 20j, 0.1 + 20j, 0.2 + 30j]] * 2),
            "diffpairs": [{"pos": 1, "neg": 2, "zdiff": "100ohm"}]}
    assert np.allclose(eval_expr(data, "Zo(2)"), 51.0)
    assert np.allclose(eval_expr(data, "im(Gamma(3))"), 30.0)
    # an even/odd split of a single pair: S_dd = -S12, S_cc = S12 with 50 ohm terminals
    assert np.allclose(eval_expr(data, "S(Diff1,Diff1)"), -0.5)
    assert np.allclose(eval_expr(data, "S(Comm1,Comm1)"), 0.5)
    assert np.allclose(eval_expr(data, "S(3,3)"), 0.2)
    assert np.allclose(eval_expr(data, "S(Diff1,3)"), 0.0)
    assert np.allclose(eval_expr(data, "VSWR(Diff1)"), 3.0)
    with pytest.raises(ValueError):
        eval_expr(data, "S(Diff1,1)")          # terminal 1 belongs to the pair


def test_lumped_ports_keep_their_s_parameters():
    """A lumped port is one terminal with its own impedance as reference: the S-parameters stay as solved."""
    t = T.Terminals()
    t.ports = [{"name": "P1", "number": 1, "kind": "lumped", "z0": 50.0, "terminals": ["P1"], "zref": [50.0]},
               {"name": "P2", "number": 2, "kind": "lumped", "z0": 75.0, "terminals": ["P2"], "zref": [75.0]}]
    S = _random_passive(2, 3)[None]
    assert np.allclose(t.transform([1e9], S, [1, 2]), S)
    g, z = T.interp_records(t.records, [1e9])
    assert np.allclose(z, [[50.0, 75.0]])


# ---- with EMerge ---------------------------------------------------------------------------------
def _wire_design(stype: str, ports_only: bool = False, wires=("1mm",)):
    """PEC wires (at these x) in an air-filled PEC box (a TEM line) with a wave port at both ends, one mode per
    wire."""
    from hertzon.kernel import occ as K
    from hertzon.model.design import Assignment, FaceRef, Project, Setup, Sweep
    from hertzon.model.facetrack import track_faces
    p = Project()
    d = p.designs[0]
    d.settings["SolutionType"] = stype
    d.add_object("CreateBox", "Air", Position="-5mm,0,-5mm", XSize="10mm", YSize="20mm", ZSize="10mm").material = "air"
    for k, x in enumerate(wires):
        d.add_object("CreateCylinder", "Wire" if k == 0 else f"Wire{k + 1}", CenterPosition=f"{x},0,0", Axis="Y",
                     Radius="1mm" if len(wires) == 1 else "0.6mm", Height="20mm").material = "pec"
    K.ensure_gmsh("terminal")
    t = K.tessellate(K.build_design(d, d.evaluator(p)))
    ta = t["Air"]
    c = np.asarray(ta.face_center)
    ends = sorted([i for i in range(len(c)) if abs(ta.face_normal[i][1]) > 0.99], key=lambda i: c[i][1])
    for k, i in enumerate(ends):
        d.excitations.append(Assignment(f"P{k + 1}", "Wave Port", {
            "PortNumber": str(k + 1), "NumberOfModes": str(len(wires)), "ModeType": "Auto", "ImpedanceDefinition": "Zpv",
            "Renormalize": "True", "RenormImpedance": "50ohm", "Deembed": "False", "DeembedDistance": "0"},
            faces=[FaceRef("Air", i)]))
    track_faces(d, t)
    st = Setup("Setup1")
    st.props.update(Frequency="3GHz", MaximumPasses="2", CurvedElements="False",
                    PortsOnly="True" if ports_only else "False")
    sw = Sweep("Sweep")
    sw.props.update(SweepType="Discrete", Distribution="Linear Count", Start="2GHz", Stop="4GHz", Count="2")
    st.sweeps.append(sw)
    d.setups.append(st)
    return p, d, st


def _solve(tmp_path, stype, ports_only=False, wires=("1mm",)):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    from hertzon.solver.results import ResultStore
    p, d, st = _wire_design(stype, ports_only, wires)
    out = tmp_path / stype
    b = B.Bridge(p, d, st, out)
    try:
        with em.Simulation(f"terminal_{stype}", save_file=False) as m:
            b.prepare(m)
            if ports_only:
                b.run_ports_only()
            else:
                b.run_adaptive()
    finally:
        B.TERMINALS = None
    return ResultStore(out)


def test_single_conductor_terminal_port_equals_the_renormalized_modal_port(tmp_path):
    modal = _solve(tmp_path, "Modal").sparams("LastAdaptive")
    term_store = _solve(tmp_path, "Terminal")
    term = term_store.sparams("LastAdaptive")
    assert term["terminals"][0]["terminals"] == ["P1_Wire"] and term["terminals"][0]["reference"] == "Outer"
    assert np.allclose(term["z0"], 50.0)
    assert np.abs(term["S"] - modal["S"]).max() < 1e-6
    assert np.allclose(term["zo"], modal["zo"], rtol=1e-6)
    # TEM line in air: Gamma = j k0, and the port fields are stored
    k0 = 2 * np.pi * term["freq"] / 299792458.0
    assert np.allclose(term["gamma"].imag / k0[:, None], 1.0, atol=2e-3)
    pf = term_store.npz("portfields_LastAdaptive.npz")
    assert pf is not None and pf["E_1_1_0"].shape == pf["nodes_1"].shape


def test_two_wires_lossless(tmp_path):
    # two TEM modes of the same propagation constant at each port: modal and terminal S of the lossless line
    # are unitary (with modes that are not orthogonal: 0.54 and 0.04 to 0.16)
    for stype, tol in (("Modal", 1e-4), ("Terminal", 1e-4)):
        S = _solve(tmp_path / stype, stype, wires=("-2mm", "2mm")).sparams("LastAdaptive")["S"]
        assert S.shape[1:] == (4, 4)
        for Sf in S:
            assert np.abs(Sf.conj().T @ Sf - np.eye(4)).max() < tol


def test_solve_ports_only(tmp_path, monkeypatch):
    st = _solve(tmp_path, "Modal", ports_only=True)
    r = st.sparams("Sweep")
    assert r["ports_only"] and np.isnan(r["S"]).all()
    assert r["zo"].shape == (2, 2) and np.all((r["zo"].real > 80) & (r["zo"].real < 120))
    assert not list(st.folder.glob("*.s2p"))       # no Touchstone file without S-parameters
    # Port Field Display of the stored mode
    from hertzon.gui import terminal_dialogs as TD
    import hertzon.gui.doc as D
    doc = types.SimpleNamespace(design=types.SimpleNamespace(setups=[types.SimpleNamespace(name="Setup1")]))
    monkeypatch.setattr(D, "result_dir", lambda doc, setup: st.folder)
    meshes, info = TD.port_field_meshes(doc, "P1", solution="Setup1 : Sweep")
    assert meshes[0][0].n_cells > 0 and "Zo" in info
