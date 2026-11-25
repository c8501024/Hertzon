"""Workarounds for imported CAD geometry that breaks the initial mesh.

Both run only after the mesher failed (bridge.Bridge.raise_mesh_retry), so every model that meshes keeps
exactly its geometry and mesh.

- A body whose faces and edges disagree by micrometres (exported CAD: a hole wall standing a few micrometres
  out of a flex board) can lose its whole volume in the mesh: gmsh warns "No elements in volume" and the
  solver stops on a missing triangle. Rebuilding the body through a general transformation (all faces
  re-approximated as B-spline surfaces, edges recomputed on them) makes it consistent; the shape stays the same.
- Two metal bodies whose faces meet at a tiny angle (a board edge against a block, tilted by a fraction of a
  degree) leave an air wedge of 0 to some 10 um over millimetres. gmsh cannot mesh it and stops with "Invalid
  boundary mesh (overlapping facets) on surface A surface B". Such a gap between two conductors carries no field
  worth solving: the convex hull of the two faces, cut back to the gap, is fused to one of the bodies."""
from __future__ import annotations

import math
import re

import numpy as np

# widest gap between the two faces that is closed (m); a wider one is a real gap and stays an error
MAX_GAP = 50e-6
# the two faces must be parallel within this angle
MAX_ANGLE_DEG = 1.0

_FACETS = re.compile(r"Invalid boundary mesh \(overlapping facets\) on surface (\d+) surface (\d+)")


def empty_volumes() -> list[int]:
    """Volumes of the current gmsh model without tetrahedra while others have some (a volume lost in the mesh);
    empty when nothing was meshed at all (the mesher stopped before the volumes)."""
    import gmsh
    count = {t: sum(len(e) for e in gmsh.model.mesh.getElements(3, t)[1]) for _, t in gmsh.model.getEntities(3)}
    if not any(count.values()):
        return []
    return [t for t, n in count.items() if n == 0]


def rebuild_bodies(built: dict, names: list[str]) -> None:
    """Rebuild the bodies of the objects ``names`` in ``built`` (object name -> dimtags) through a general
    transformation (identity): OpenCASCADE approximates every face anew and recomputes the edges on it."""
    import gmsh
    occ = gmsh.model.occ
    for name in names:
        dt = built.get(name)
        if dt:
            occ.affineTransform(dt, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0])
    occ.synchronize()


def failing_surfaces(message: str) -> tuple[int, int] | None:
    """The two surface tags of gmsh's "overlapping facets" error, None for any other error."""
    hit = _FACETS.search(message or "")
    return (int(hit.group(1)), int(hit.group(2))) if hit else None


def _normal(s: int) -> np.ndarray:
    import gmsh
    p = gmsh.model.getParametrizationBounds(2, s)
    return np.asarray(gmsh.model.getNormal(s, [(p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2]), float)


def _face_points(s: int, n_curve: int = 8) -> np.ndarray:
    """Points on the boundary of face s: the ends of its edges and a few points along each edge."""
    import gmsh
    pts = []
    for _, c in gmsh.model.getBoundary([(2, s)], oriented=False):
        lo, hi = gmsh.model.getParametrizationBounds(1, abs(c))
        u = np.linspace(lo[0], hi[0], n_curve + 1)
        pts.append(np.asarray(gmsh.model.getValue(1, abs(c), u.tolist()), float).reshape(-1, 3))
    return np.unique(np.round(np.vstack(pts), 12), axis=0)


def find_gap(a: int, b: int, owner: dict[int, str]) -> dict | None:
    """The gap between the faces a and b of the current (fragmented, synchronized) gmsh model, or None when it
    is no hairline gap between two conductors. ``owner`` maps the volume tags of conductor objects to their
    names."""
    import gmsh
    M = gmsh.model
    if M.getType(2, a) != "Plane" or M.getType(2, b) != "Plane":
        return None
    va, vb = (set(int(v) for v in M.getAdjacencies(2, s)[0]) for s in (a, b))
    shared = va & vb
    metal_a, metal_b = [v for v in va - shared if v in owner], [v for v in vb - shared if v in owner]
    # both faces bound the same non-conducting volume (the gap) and one conductor each
    if len(va) != 2 or len(vb) != 2 or len(shared) != 1 or shared & set(owner) or not metal_a or not metal_b:
        return None
    angle = math.degrees(math.acos(min(1.0, abs(float(np.dot(_normal(a), _normal(b)))))))
    if angle > MAX_ANGLE_DEG:
        return None
    # the gap lies in front of the face that stands entirely within MAX_GAP of the other one (usually the
    # smaller face: a board edge in front of a larger block); the fill reaches from that face to its projection
    # on the other face and so covers no more than the gap
    for near, far, own, other in ((a, b, metal_a[0], metal_b[0]), (b, a, metal_b[0], metal_a[0])):
        pts = _face_points(near)
        foot = np.asarray(M.getClosestPoint(2, far, pts.ravel().tolist())[0], float).reshape(-1, 3)
        dist = np.linalg.norm(foot - pts, axis=1)
        gap = float(dist.max())
        if gap <= MAX_GAP:
            # where the faces touch, the foot is the point itself (a hull edge of zero length)
            hull = np.vstack([pts, foot[dist > 1e-9]])
            return {"object": owner[own], "other": owner[other], "points": hull.tolist(), "gap": gap,
                    "angle": angle}
    return None


def hull_volume(points) -> list[tuple[int, int]]:
    """A solid of the convex hull of ``points`` (m) in the current OCC model."""
    import gmsh
    from scipy.spatial import ConvexHull
    occ = gmsh.model.occ
    P = np.asarray(points, float)
    # points closer than a nanometre are one point (a hull edge between them could not be built)
    _, keep = np.unique(np.round(P / 1e-9), axis=0, return_index=True)
    P = P[np.sort(keep)]
    hull = ConvexHull(P)
    ptag = {i: occ.addPoint(*P[i]) for i in np.unique(hull.simplices)}
    lines: dict[tuple[int, int], int] = {}

    def line(i: int, j: int) -> int:
        key = (min(i, j), max(i, j))
        if key not in lines:
            lines[key] = occ.addLine(ptag[key[0]], ptag[key[1]])
        return lines[key] if (i, j) == key else -lines[key]
    faces = []
    for (i, j, k), eq in zip(hull.simplices, hull.equations):
        # every triangle turned to the outward normal of its facet: a shell of mixed orientation gave a solid
        # of negative volume after the fusion, which the solver model cannot take
        if np.dot(np.cross(P[j] - P[i], P[k] - P[i]), eq[:3]) < 0:
            j, k = k, j
        loop = occ.addCurveLoop([line(i, j), line(j, k), line(k, i)])
        faces.append(occ.addPlaneSurface([loop]))
    shell = occ.addSurfaceLoop(faces, sewing=True)
    vol = occ.addVolume([shell])
    # sewing builds the solid from new faces: the triangles it was made of would stay behind as free sheets,
    # and a free sheet in the model breaks the mesh; a copy of the solid alone is kept
    out = occ.copy([(3, vol)])
    occ.remove([(3, vol)], recursive=True)
    occ.remove([(2, f) for f in faces], recursive=True)
    return out


def apply_fills(built: dict, fills: list[dict]) -> None:
    """Fuse the hull of every gap to its object in ``built`` (object name -> dimtags of the unfragmented build)."""
    import gmsh
    occ = gmsh.model.occ
    for f in fills:
        dt = built.get(f["object"])
        if not dt:
            continue
        # the hull only fills the gap: whatever of it reaches into another solid is cut off, so that no new
        # overlap (and no sliver volume of the fragmentation) appears
        others = [x for name, lst in built.items() if name != f["object"] for x in lst if x[0] == 3]
        fill = hull_volume(f["points"])
        if others:
            fill, _ = occ.cut(fill, others, removeObject=True, removeTool=False)
        if fill:
            built[f["object"]], _ = occ.fuse(dt, fill)
    occ.synchronize()


def describe(f: dict) -> str:
    return (f"a gap of up to {f['gap'] * 1e6:.3g} um between '{f['object']}' and '{f['other']}' (faces "
            f"{f['angle']:.3g} deg apart)")
