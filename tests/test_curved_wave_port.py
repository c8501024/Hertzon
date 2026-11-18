"""Curved wave ports: EMerge solved the port mode on the flat triangles of the port face and kept every curved face
connected to a wave port straight. The port face is curved now like the walls behind it (hertzon/solver/curved_ports.py):
the 2D mode is solved with the pulled-back materials, every port integral carries the metric of the curved face.

Coax50 after one pass: port Z0 49.96 Ohm (line 50.05 Ohm; flat port 50.56 Ohm), S11 -50.7 dB (flat port -47.9 dB).
Semicircular waveguide r = 12 mm at 10 GHz: beta 142.744 1/m (analytic 142.774; flat port 141.847)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from hertzon.model.design import Assignment, Project, Setup  # noqa: E402
from test_curved_thin_conductor import PAIRS, _curved_share  # noqa: E402


def _run(p, tmp_path):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    from hertzon.solver.results import ResultStore
    d = p.designs[0]
    st = d.setups[0]
    st.props.update(MaximumPasses="1", MinimumPasses="1", SolutionFrequencyMode="Single")
    b = B.Bridge(p, d, st, tmp_path / "out")
    with em.Simulation(f"cport_{tmp_path.name}", save_file=False) as m:
        b._run_in(m)
    return np.load(tmp_path / "out" / "mesh.npz"), ResultStore(tmp_path / "out").sparams("LastAdaptive")


def test_coax_wave_ports_are_curved(tmp_path):
    mesh, sp = _run(Project.load(ROOT / "examples" / "Coax.hzn"), tmp_path)
    assert _curved_share(mesh, 0.5) > 0.7 and _curved_share(mesh, 1.675) > 0.7
    # the rim edges of the port faces (z = 0 and z = L) are curved too
    X, T = mesh["nodes"] * 1e3, np.asarray(mesh["tets"], np.int64)
    ct = np.asarray(mesh["curv_tets"], np.int64)
    pairs = np.array(PAIRS)
    a, b2 = T[ct][:, pairs[:, 0]].ravel(), T[ct][:, pairs[:, 1]].ravel()
    disp = np.linalg.norm(np.asarray(mesh["curv_disp"]).reshape(-1, 3), axis=1)
    for z in (0.0, 20.0):
        on = (np.abs(X[a, 2] - z) < 1e-6) & (np.abs(X[b2, 2] - z) < 1e-6)
        assert on.any() and disp[on].any()
    S = sp["S"][0]
    assert 20 * np.log10(abs(S[0, 0])) < -49       # -50.7 dB curved port, -47.9 dB flat port
    z_line = 60 / np.sqrt(2.1) * np.log(1.675 / 0.5)
    zo = np.real(np.asarray(sp["zo"]).ravel())
    assert np.all(np.abs(zo / z_line - 1) < 0.004), zo     # 49.96 Ohm; the flat port gave 50.56 Ohm (+1 %)


def test_semicircular_waveguide_port_beta(tmp_path):
    """TE11 of a semicircular guide (half of a circular guide, kc = 1.8412 / r): the port's beta is the curved one."""
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    A = d.add_object
    A("CreateCylinder", "Guide", CenterPosition="0,0,0", Axis="Z", Radius="12", Height="40").material = "vacuum"
    A("CreateBox", "Cut", Position="-20,-20,-1", XSize="40", YSize="20", ZSize="42")
    op = type(d.obj("Guide").history[0])
    d.obj("Guide").history.append(op(kind="Subtract", props={"Tools": "Cut", "KeepOriginals": "False"}))
    for k, z in ((1, 0), (2, 40)):
        A("CreateRectangle", f"P{k}", Position=f"-12,0,{z}", Axis="Z", XSize="24", YSize="12")
        A("CreateCylinder", f"C{k}", CenterPosition=f"0,0,{z - 1}", Axis="Z", Radius="12", Height="2")
        d.obj(f"P{k}").history.append(op(kind="Intersect", props={"Tools": f"C{k}", "KeepOriginals": "False"}))
        d.excitations.append(Assignment(str(k), "Wave Port", {"PortNumber": str(k), "NumberOfModes": "1"},
                                        objects=[f"P{k}"]))
    for n, c in (("Cut", "Guide"), ("C1", "P1"), ("C2", "P2")):
        d.obj(n).consumed_by = c
    st = Setup("Setup1")
    st.props.update(Frequency="10GHz", MaximumDeltaS="0.0001")
    d.setups.append(st)
    _, sp = _run(p, tmp_path)
    k0 = 2 * np.pi * 10e9 / 299792458.0
    beta = np.sqrt(k0 ** 2 - (1.8412 / 0.012) ** 2)
    g = np.asarray(sp["gamma"]).ravel()
    assert np.all(np.abs(np.abs(g) / beta - 1) < 5e-4), g     # 142.744 against 142.774; flat port 141.847 (-0.65 %)
    assert 20 * np.log10(abs(sp["S"][0][0, 0])) < -44
