"""Faces whose mesh uses nodes of embedded curves and points (internal edges that OCC keeps inside a face after a
boolean, e.g. the slot of an imported flex board). Their triangles use nodes that getNodes(2, face,
includeBoundary=True) does not return; the face sampling of the solver failed on them with "index N is out of
bounds for axis 0 with size M" before every mesh (Meta benchmark, Example 2)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gmsh  # noqa: E402

from hertzon.kernel import occ as K  # noqa: E402
from hertzon.solver import facematch  # noqa: E402


def _plate_with_internal_edge_and_point():
    K.ensure_gmsh("test_embedded")
    gmsh.model.remove()
    gmsh.model.add("test_embedded")
    box = gmsh.model.occ.addBox(0, 0, 0, 10, 4, 1)
    # a slit inside the top face that stops short of its edges: the fragment keeps it as an internal edge
    line = gmsh.model.occ.addLine(gmsh.model.occ.addPoint(3, 2, 1), gmsh.model.occ.addPoint(7, 2, 1))
    pt = gmsh.model.occ.addPoint(5, 1, 1)
    gmsh.model.occ.fragment([(3, box)], [(1, line), (0, pt)])
    gmsh.model.occ.synchronize()
    top = [t for _, t in gmsh.model.getEntities(2)
           if abs(gmsh.model.occ.getCenterOfMass(2, t)[2] - 1) < 1e-9][0]
    # OCC keeps the slit and the point as an internal edge and vertex of the top face; gmsh meshes them as
    # embedded entities (they are not in the face's boundary)
    gmsh.option.setNumber("Mesh.MeshSizeMax", 1.0)
    return top


def test_face_samples_use_the_nodes_of_embedded_edges(monkeypatch):
    top = _plate_with_internal_edge_and_point()
    gmsh.model.mesh.generate(2)
    types, _, enodes = gmsh.model.mesh.getElements(2, top)
    own, _, _ = gmsh.model.mesh.getNodes(2, top, includeBoundary=True)
    assert set(np.concatenate(enodes).tolist()) - set(own.tolist()), "the face has no embedded nodes"

    # keep the sampling mesh (final_face_samples clears it when done) to check the samples against it
    clear = gmsh.model.mesh.clear
    calls = []
    monkeypatch.setattr(gmsh.model.mesh, "clear", lambda *a: (calls.append(1), clear(*a) if len(calls) == 1 else None))
    samples = facematch.final_face_samples(max_pts=10 ** 6)
    monkeypatch.setattr(gmsh.model.mesh, "clear", clear)
    types, _, enodes = gmsh.model.mesh.getElements(2, top)
    tri = np.concatenate([e for t, e in zip(types, enodes) if t == 2]).astype(np.int64).reshape(-1, 3)
    ntags, coords, _ = gmsh.model.mesh.getNodes()
    pos = dict(zip(ntags.tolist(), coords.reshape(-1, 3)))
    ref = np.array([np.mean([pos[n] for n in t], axis=0) for t in tri])
    assert samples[top].shape == ref.shape
    assert np.allclose(samples[top], ref)


def test_display_triangles_of_a_face_with_embedded_nodes():
    top = _plate_with_internal_edge_and_point()
    gmsh.model.mesh.generate(2)
    K._ALL_NODES.clear()
    K._PASS_CACHE.clear()
    X, T = K._entity_tris(2, top)          # the path without the bulk node table
    assert len(T)
    P = X[T]
    assert np.allclose(P[..., 2], 1.0)
    area = 0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1).sum()
    assert abs(area - 40.0) < 1e-6
