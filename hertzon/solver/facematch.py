"""Map faces of the modeller objects onto the fragmented faces of the solver model.

EMerge fragments all objects before meshing, which renumbers and splits faces.
A face picked in the modeller is identified again by geometry: every fragment
whose sample points lie on the original face belongs to it.
"""
from __future__ import annotations

import gmsh
import numpy as np


def point_triangle_distance(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Minimum distance of each point in P (m,3) to a triangle soup T (n,3,3)."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    ab, ac = b - a, c - a
    d00 = (ab * ab).sum(1)
    d01 = (ab * ac).sum(1)
    d11 = (ac * ac).sum(1)
    den = d00 * d11 - d01 * d01
    den[den == 0] = 1e-300
    res = np.empty(len(P))
    for i, p in enumerate(P):
        ap = p - a
        d1 = (ab * ap).sum(1)
        d2 = (ac * ap).sum(1)
        v = (d11 * d1 - d01 * d2) / den
        w = (d00 * d2 - d01 * d1) / den
        # clamp to the triangle (approximate on edges, exact inside)
        v = np.clip(v, 0, 1)
        w = np.clip(w, 0, 1)
        s = v + w
        over = s > 1
        v[over] /= s[over]
        w[over] /= s[over]
        q = a + ab * v[:, None] + ac * w[:, None]
        res[i] = np.sqrt(((q - p) ** 2).sum(1)).min()
    return res


class FaceSignature:
    """Triangulation of an original face plus tolerances."""

    def __init__(self, tris: np.ndarray, normal):
        self.tris = tris
        pts = tris.reshape(-1, 3)
        self.lo = pts.min(0)
        self.hi = pts.max(0)
        self.diag = float(np.linalg.norm(self.hi - self.lo)) or 1e-9
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        nn = np.linalg.norm(n, axis=1)
        ok = nn > 0
        n = n[ok] / nn[ok, None]
        self.planar = bool(len(n) and np.all(np.abs(n @ n[0]) > 0.9999))
        self.normal = n[0] if len(n) else np.zeros(3)
        # tolerance: tessellation sagitta for curved faces, numerical noise for planar ones
        self.tol = 1e-6 * self.diag if self.planar else 0.03 * self.diag


def final_face_samples(max_pts: int = 24) -> dict[int, np.ndarray]:
    """Sample points on every surface of the current (fragmented) model via a coarse 2D mesh."""
    gmsh.model.mesh.clear()
    ents = gmsh.model.getEntities(2)
    if not ents:
        return {}
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
    from ..kernel.occ import set_apex_floor, _bbox, mesh_empty_faces, _PASS_CACHE
    bb = _bbox(gmsh.model.getEntities(3) or ents)
    diag = float(np.linalg.norm(np.subtract(bb[3:], bb[:3])))
    set_apex_floor(diag / 200)
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    _PASS_CACHE.clear()
    try:
        gmsh.model.mesh.generate(2)
    except Exception:  # noqa: BLE001
        # CAD faces that break the curvature sizing are meshed again without it (MeshAdapt, the
        # former fallback, crashes on such faces)
        if not mesh_empty_faces(diag / 40):
            gmsh.option.setNumber("Mesh.Algorithm", 5)
            _PASS_CACHE.clear()
            gmsh.model.mesh.clear()
            gmsh.model.mesh.generate(2)
    from ..kernel.occ import node_table, element_points
    glut, GX = node_table()
    out = {}
    for _, tag in ents:
        if (2, tag) in _PASS_CACHE:
            X, T = _PASS_CACHE[(2, tag)]
            if len(T):
                C = X[T].mean(axis=1)
                if len(C) > max_pts:
                    C = C[np.linspace(0, len(C) - 1, max_pts).astype(int)]
                out[tag] = C
            continue
        # element nodes from the table of the whole mesh: a face's own getNodes(includeBoundary=True) leaves
        # out the nodes of its embedded (internal) edges and vertices, which its triangles use
        types, _, enodes = gmsh.model.mesh.getElements(2, tag)
        cents = []
        for t, en in zip(types, enodes):
            if t == 2:
                cents.append(element_points(glut, GX, en.astype(np.int64).reshape(-1, 3)).mean(axis=1))
        if not cents:
            continue
        C = np.vstack(cents)
        if len(C) > max_pts:
            C = C[np.linspace(0, len(C) - 1, max_pts).astype(int)]
        out[tag] = C
    gmsh.model.mesh.clear()
    _PASS_CACHE.clear()
    return out


def match_faces(sig: FaceSignature, samples: dict[int, np.ndarray]) -> list[int]:
    res = []
    margin = max(sig.tol * 2, 1e-12)
    for tag, C in samples.items():
        # quick bounding box rejection
        if np.any(C.max(0) < sig.lo - margin) or np.any(C.min(0) > sig.hi + margin):
            continue
        inside = np.all((C >= sig.lo - margin) & (C <= sig.hi + margin), axis=1)
        if inside.mean() < 0.5:
            continue
        d = point_triangle_distance(C[inside], sig.tris)
        if np.median(d) <= sig.tol and (d <= sig.tol).mean() >= 0.6:
            res.append(tag)
    return res
