"""Element sizes for thin curved walls and narrow flat rings (curved elements).

Curvature sizing gives a circle about ten segments. On a 12 mm balun sleeve with a 1 mm wall that is 7.5 mm:

- the straight facets of the inner and the outer wall dip 0.6 mm each towards the other, so they can cross
  (gmsh "PLC Error: A segment and a facet intersect") as soon as the nodes of the two walls do not line up;
- the flat 1 mm rim between the two circles is cut into triangles 7 mm long and 1 mm wide. With the edges bent onto
  the circles (curved elements) they fold over, so EMerge's validity check straightened the rim edges to a facet
  0.4 mm deep. Bisecting those tets afterwards does not repair the ring triangulation.

A curved face that comes within t of another, non-adjacent face gets elements of at most sqrt(8 R t / 3) (R its
smallest radius), so its facets stay within t / 3 of the true surface. A curved edge of a flat face that comes within
w of another, non-adjacent edge of that face gets segments of at most sqrt(R w): the tangent then turns by less than
half the angle of a triangle across the face. Thick walls and wide faces keep the curvature sizing (only sizes below
0.9 x the curvature size are set).
"""
from __future__ import annotations

import numpy as np


def _gap(G, dim: int, pts: np.ndarray, others, box) -> float:
    """Smallest distance from the points to the other entities of dimension dim (only points landing on them)."""
    lo, hi = pts.min(0), pts.max(0)
    span = float(np.ptp(pts, axis=0).max())
    best = np.inf
    for o in others:
        b = box(dim, o)
        reach = min(best, span) + 1e-30
        if np.any(b[:3] > hi + reach) or np.any(b[3:] < lo - reach):
            continue
        q = np.asarray(G.getClosestPoint(dim, o, pts.ravel())[0], float).reshape(-1, 3)
        if q.shape != pts.shape:
            continue
        dist = np.linalg.norm(q - pts, axis=1)
        if dim == 2:
            # the projection goes onto the untrimmed surface: keep the points that land on the face
            dist = dist[np.array([G.isInside(2, o, list(p)) > 0 for p in q], bool)]
        if len(dist):
            best = min(best, float(dist.min()))
    return best


def sizes(nseg: float = 10.0) -> tuple[dict[int, float], dict[int, float]]:
    """(face tag -> size, curve tag -> size) for the current gmsh model, in model units; nseg is the curvature
    sizing (segments per full turn)."""
    import gmsh
    G = gmsh.model
    ends: dict[int, set] = {}
    boxes: dict[tuple, np.ndarray] = {}

    def corners(c):
        if c not in ends:
            ends[c] = {abs(p) for _, p in G.getBoundary([(1, c)], combined=False, oriented=False)}
        return ends[c]

    def box(d, t):
        if (d, t) not in boxes:
            boxes[(d, t)] = np.asarray(G.getBoundingBox(d, t), float)
        return boxes[(d, t)]

    curve_size: dict[int, float] = {}
    face_size: dict[int, float] = {}
    faces = [t for _, t in G.getEntities(2)]
    fcurves = {f: {abs(c) for _, c in G.getBoundary([(2, f)], combined=False, oriented=False)} for f in faces}
    for f in faces:
        if G.getType(2, f) == "Plane":
            bnd = sorted(fcurves[f])
            for c in bnd:
                if G.getType(1, c) == "Line":
                    continue
                lo, hi = G.getParametrizationBounds(1, c)
                u = list(np.linspace(lo[0], hi[0], 18)[1:-1])
                k = float(np.max(G.getCurvature(1, c, u)))
                if not np.isfinite(k) or k <= 0:
                    continue
                R = 1.0 / k
                pts = np.asarray(G.getValue(1, c, u), float).reshape(-1, 3)
                w = _gap(G, 1, pts, [c2 for c2 in bnd if c2 != c and not corners(c2) & corners(c)], box)
                h = float(np.sqrt(R * w)) if np.isfinite(w) and w > 0 else np.inf
                # a straight edge stored as a spline has a tiny curvature: nothing to resolve
                if h < 0.9 * 2 * np.pi * R / nseg and h < G.occ.getMass(1, c):
                    curve_size[c] = min(curve_size.get(c, np.inf), h)
            continue
        try:
            lo, hi = G.getParametrizationBounds(2, f)
        except Exception:  # noqa: BLE001
            continue
        uv = np.array([(a, b) for a in np.linspace(lo[0], hi[0], 9)[1:-1]
                       for b in np.linspace(lo[1], hi[1], 7)[1:-1]], float)
        k = np.abs(np.asarray(G.getCurvature(2, f, list(uv.ravel())), float))
        k = k[np.isfinite(k)]
        if not len(k) or k.max() <= 0:
            continue
        R = 1.0 / float(k.max())
        pts = np.asarray(G.getValue(2, f, list(uv.ravel())), float).reshape(-1, 3)
        pts = pts[np.array([G.isInside(2, f, list(p)) > 0 for p in pts], bool)]
        if not len(pts):
            continue
        t = _gap(G, 2, pts, [g for g in faces if g != f and not fcurves[g] & fcurves[f]], box)
        h = float(np.sqrt(8 * R * t / 3)) if np.isfinite(t) and t > 0 else np.inf
        b = box(2, f)
        if h < 0.9 * 2 * np.pi * R / nseg and h < float(np.linalg.norm(b[3:] - b[:3])):
            face_size[f] = min(face_size.get(f, np.inf), h)
    return face_size, curve_size


def apply(mesher, faces: dict[int, float], curves: dict[int, float]) -> None:
    """Constant size fields on the faces and curves (EMerge combines its mesh fields by their minimum)."""
    import gmsh
    for c, h in curves.items():
        mesher._set_size_on_edge([c], h)
    for f, h in faces.items():
        tag = gmsh.model.mesh.field.add("Constant")
        gmsh.model.mesh.field.setNumbers(tag, "SurfacesList", [f])
        gmsh.model.mesh.field.setNumber(tag, "VIn", h)
        mesher.mesh_fields.append(tag)
