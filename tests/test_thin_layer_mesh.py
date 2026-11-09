"""Mesh of a thin substrate: the elements in the layer are at most a few times its thickness, so the tetrahedra
through it are not flat and none is without volume (a microstrip filter on 0.508 mm had 109 tetrahedra of no
volume and a third below quality 0.01 before)."""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model.design import Project  # noqa: E402
from hertzon.model.structures import create_structure  # noqa: E402


def _quality(X, T):
    a, b, c, d = (X[T[:, i]] for i in range(4))
    V = np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6
    L2 = sum(((X[T[:, i]] - X[T[:, j]]) ** 2).sum(1) for i in range(4) for j in range(i + 1, 4))
    return 12 * (3 * V) ** (2 / 3) / L2


def test_thin_substrate_mesh_has_no_flat_tetrahedra(tmp_path):
    p = Project("t")
    r = create_structure("edge", {}, project=p)
    p.designs += r.designs
    p.materials += r.materials
    d3 = r.designs[-1]
    # the evaluation that failed with NaN before the limit
    d3.variables.update(W="0.751462mm", S1="0.126304mm", L1="8.65112mm", S2="0.70461mm", L2="8.67554mm")
    p.save(tmp_path / "p.hzn")
    env = dict(os.environ, HERTZON_MESH_ONLY="1", PYTHONPATH=str(ROOT))
    out = subprocess.run([sys.executable, "-m", "hertzon.solver.run", str(tmp_path / "p.hzn"), d3.name, "Setup1",
                          str(tmp_path / "out")], env=env, capture_output=True, text=True, timeout=600)
    assert "Thin layer 'Substrate'" in out.stdout
    m = np.load(tmp_path / "out" / "mesh.npz")
    q = _quality(m["nodes"], m["tets"])
    assert q.min() > 1e-6 and (q < 1e-2).mean() < 0.01
