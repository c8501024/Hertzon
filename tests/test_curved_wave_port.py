"""Curved elements behind a wave port: EMerge kept every curved face connected to a wave port straight, on a coax
line the whole inner conductor and outer wall. Only the port face itself stays straight now; the walls behind it
are curved, which removes the polygon error of the line (Coax50 after one pass: S11 -47.9 dB instead of -42.2 dB,
same mesh)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from hertzon.model.design import Project  # noqa: E402
from test_curved_thin_conductor import PAIRS, _curved_share  # noqa: E402


def test_coax_walls_behind_wave_ports_are_curved(tmp_path):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    from hertzon.solver.results import ResultStore
    p = Project.load(ROOT / "examples" / "Coax.hzn")
    d = p.designs[0]
    st = d.setups[0]
    st.props.update(MaximumPasses="1", MinimumPasses="1", SolutionFrequencyMode="Single")
    b = B.Bridge(p, d, st, tmp_path / "out")
    with em.Simulation(f"coax_{tmp_path.name}", save_file=False) as m:
        b._run_in(m)
    mesh = np.load(tmp_path / "out" / "mesh.npz")
    assert _curved_share(mesh, 0.5) > 0.7 and _curved_share(mesh, 1.675) > 0.7
    # the port faces (z = 0 and z = L) keep straight edges: their mode is solved on the flat triangles
    X, T = mesh["nodes"] * 1e3, np.asarray(mesh["tets"], np.int64)
    ct = np.asarray(mesh["curv_tets"], np.int64)
    pairs = np.array(PAIRS)
    a, b2 = T[ct][:, pairs[:, 0]].ravel(), T[ct][:, pairs[:, 1]].ravel()
    disp = np.linalg.norm(np.asarray(mesh["curv_disp"]).reshape(-1, 3), axis=1)
    for z in (0.0, 20.0):
        on = (np.abs(X[a, 2] - z) < 1e-6) & (np.abs(X[b2, 2] - z) < 1e-6)
        assert on.any() and not disp[on].any()
    S = ResultStore(tmp_path / "out").sparams("LastAdaptive")["S"][0]
    assert 20 * np.log10(abs(S[0, 0])) < -45      # -47.9 dB curved, -42.2 dB with straight walls
