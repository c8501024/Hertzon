"""Mesh morphing for derivative tuning: how every node of the solver mesh moves when a design variable
changes, without remeshing.

The model is built twice more in scratch gmsh models, with the variable a small step below and above its
value, and the vertices and boundary curves of every object face are compared with the nominal build. That
gives the motion of the CAD boundary per unit of the variable (its velocity). The mesh nodes follow it:

- a node on a face that moves follows the face: on a plane face the motion of its boundary is spread over
  the face with mean value coordinates (exact for any affine motion of the face, linear along every
  boundary segment, so neighbouring faces agree on their common edges); a curved face may only translate;
- a node on a face that stays where it is may slide within that face (in the plane, or along an edge of
  it), so the mesh next to a moving edge stretches over the whole face instead of one element layer;
- nodes on ports, on the outer boundary of the solution region and on curved faces that do not move
  stay where they are;
- all other nodes (inside the volumes) follow a smooth extension of the boundary motion: a vector Laplace
  problem on the tetrahedra, stiffened in small elements (weight 1/volume) so that the fine mesh near
  conductors moves almost rigidly and the large elements absorb the deformation.

The result is a velocity field V (3 x nodes, metres per unit of the variable) for every variable; the
morphed mesh at a variable offset dp is nodes + dp * V. The connectivity of the mesh never changes, so the
FEM matrix changes smoothly with the variables.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .optimetrics import split_value


class MorphError(RuntimeError):
    """The variable changes the model in a way the mesh cannot follow (topology, curved faces, ports)."""


# ------------------------------------------------------------------------------------------------
# CAD side: faces of the objects and how their boundaries move
# ------------------------------------------------------------------------------------------------
@dataclass
class Face:
    obj: str
    index: int                      # face index within the object (order of occ.object_faces)
    planar: bool
    origin: np.ndarray              # a point of the plane (planar faces)
    normal: np.ndarray              # unit normal (planar faces)
    loops: list = field(default_factory=list)     # boundary loops, each (M, 3) points in order
    bbox: np.ndarray = None         # (2, 3) min / max corner
    velocity: list = field(default_factory=list)  # per loop (M, 3) velocity of the loop points
    vertex_velocity: np.ndarray = None            # (K, 3) velocity of the CAD vertices of the face

    @property
    def moving(self) -> bool:
        return any(np.abs(v).max() > 0 for v in self.velocity) or (
            self.vertex_velocity is not None and len(self.vertex_velocity)
            and np.abs(self.vertex_velocity).max() > 0)


_CURVE_SAMPLES = 24


def _curve_points(tag: int, n: int) -> np.ndarray:
    import gmsh
    lo, hi = gmsh.model.getParametrizationBounds(1, tag)
    ts = np.linspace(lo[0], hi[0], n)
    return np.asarray(gmsh.model.getValue(1, tag, ts.tolist())).reshape(-1, 3)


def _is_line(tag: int) -> bool:
    import gmsh
    try:
        return gmsh.model.getType(1, tag) == "Line"
    except Exception:  # noqa: BLE001
        return False


def _face_loops(tag: int, plan: list | None = None) -> tuple[list[np.ndarray], list]:
    """Boundary loops of a face as ordered point lists. plan (from the nominal build) fixes the curve order,
    orientation and sample count, so that the same points are taken in a perturbed build."""
    import gmsh
    occ = gmsh.model.occ
    _, curve_loops = occ.getCurveLoops(tag)
    loops, new_plan = [], []
    for li, curves in enumerate(curve_loops):
        curves = [abs(int(c)) for c in curves]
        if plan is not None:
            order = plan[li]
            if len(order) != len(curves):
                raise MorphError("the face topology changes with the variable")
        else:
            # chain the curves by their end points
            segs = {}
            for c in curves:
                n = 2 if _is_line(c) else _CURVE_SAMPLES
                P = _curve_points(c, n)
                segs[c] = (n, P)
            order, used = [], set()
            cur = curves[0]
            n, P = segs[cur]
            order.append((cur, n, False))
            used.add(cur)
            end = P[-1]
            for _ in range(len(curves) - 1):
                best = None
                for c in curves:
                    if c in used:
                        continue
                    _, Q = segs[c]
                    for rev, q in ((False, Q[0]), (True, Q[-1])):
                        d = np.linalg.norm(q - end)
                        if best is None or d < best[0]:
                            best = (d, c, rev)
                if best is None:
                    break
                _, c, rev = best
                n, Q = segs[c]
                order.append((c, n, rev))
                used.add(c)
                end = Q[0] if rev else Q[-1]
        pts = []
        for k, (c_nominal, n, rev) in enumerate(order):
            c = curves[k] if plan is not None else c_nominal
            P = _curve_points(c, n)
            if rev:
                P = P[::-1]
            pts.append(P[:-1])            # the last point is the first of the next curve
        loops.append(np.vstack(pts) if pts else np.zeros((0, 3)))
        new_plan.append([(c, n, rev) for c, n, rev in order])
    return loops, new_plan


def _plane_of(tag: int, loops: list[np.ndarray]) -> tuple[bool, np.ndarray, np.ndarray]:
    import gmsh
    planar = gmsh.model.getType(2, tag) == "Plane"
    P = np.vstack([lp for lp in loops if len(lp)]) if loops else np.zeros((0, 3))
    if not planar or len(P) < 3:
        return False, np.zeros(3), np.zeros(3)
    c = P.mean(axis=0)
    _, _, vt = np.linalg.svd(P - c)
    n = vt[2] / np.linalg.norm(vt[2])
    return True, c, n


def build_faces(design, ev, plans: dict | None = None) -> tuple[dict, dict]:
    """Faces of every live object, built in a scratch gmsh model (the current model is restored).
    Returns ({(obj, index): (loops, vertices, planar, origin, normal, bbox)}, plans)."""
    import gmsh
    from ..kernel import occ as K
    if not gmsh.isInitialized():
        K.ensure_gmsh()
    cur = gmsh.model.getCurrent()
    gmsh.model.add("hertzon_morph")
    try:
        ctx = K.build_design(design, ev)
        if ctx.errors:
            n, e = next(iter(ctx.errors.items()))
            raise MorphError(f"object '{n}' cannot be built: {e}")
        out, new_plans = {}, {}
        for name, dt in ctx.built.items():
            faces = K.object_faces(dt)
            if plans is not None and len([k for k in plans if k[0] == name]) != len(faces):
                raise MorphError(f"object '{name}' changes its number of faces with the variable")
            for i, f in enumerate(faces):
                plan = plans.get((name, i)) if plans is not None else None
                loops, plan = _face_loops(f, plan)
                verts = []
                for d, t in gmsh.model.getBoundary([(2, f)], combined=False, oriented=False, recursive=True):
                    if d == 0:
                        verts.append(gmsh.model.getValue(0, abs(t), []))
                verts = np.asarray(verts, float).reshape(-1, 3)
                planar, c, n = _plane_of(f, loops)
                bb = np.asarray(gmsh.model.getBoundingBox(2, f), float).reshape(2, 3)
                out[(name, i)] = (loops, verts, planar, c, n, bb)
                new_plans[(name, i)] = plan
        return out, new_plans
    finally:
        gmsh.model.remove()
        gmsh.model.setCurrent(cur)


def perturbed(design, name: str, delta: float):
    """Copy of the design with variable name changed by delta (in the unit of its value)."""
    import copy
    d = copy.deepcopy(design)
    sv = split_value(d.variables[name])
    if sv is None:
        raise MorphError(f"variable '{name}' is an expression; only variables with a value can be tuned")
    v, unit = sv
    if abs(delta) > 0:
        d.variables[name] = f"{v + delta:.17g}{unit}"
    return d


def face_velocities(project, design, name: str, step: float) -> list[Face]:
    """Faces of the nominal model with the velocity of their boundary points for variable name
    (metres per unit of the variable), from central differences with the given step."""
    nominal, plans = build_faces(design, design.evaluator(project))
    lo, _ = build_faces(perturbed(design, name, -step), perturbed(design, name, -step).evaluator(project), plans)
    hi, _ = build_faces(perturbed(design, name, +step), perturbed(design, name, +step).evaluator(project), plans)
    faces = []
    for key, (loops, verts, planar, c, n, bb) in nominal.items():
        if key not in lo or key not in hi:
            raise MorphError(f"object '{key[0]}' changes its faces with variable '{name}'")
        vel = []
        for k, P in enumerate(loops):
            a, b = lo[key][0][k], hi[key][0][k]
            if a.shape != P.shape or b.shape != P.shape:
                raise MorphError(f"object '{key[0]}' changes its face boundaries with variable '{name}'")
            vel.append((b - a) / (2 * step))
        va, vb = lo[key][1], hi[key][1]
        vv = (vb - va) / (2 * step) if va.shape == verts.shape == vb.shape else None
        f = Face(key[0], key[1], planar, c, n, loops, bb, vel, vv)
        faces.append(f)
    return faces


# ------------------------------------------------------------------------------------------------
# mean value coordinates on a plane polygon (with holes)
# ------------------------------------------------------------------------------------------------
def _frame(n: np.ndarray) -> np.ndarray:
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return np.vstack([u, v])


def mvc_interpolate(face: Face, X: np.ndarray, values: list[np.ndarray], tol: float) -> np.ndarray:
    """Values at the points X (N, 3) on the plane of face, interpolated from the values at the loop points
    (per loop (M, k)) with mean value coordinates. Points on a boundary segment get the linear
    interpolation of its end values, points at a loop point its value."""
    F = _frame(face.normal)
    x = (X - face.origin) @ F.T                       # (N, 2)
    N = len(x)
    k = values[0].shape[1]
    num = np.zeros((N, k))
    den = np.zeros(N)
    exact = np.zeros(N, bool)
    out = np.zeros((N, k))
    for P3, val in zip(face.loops, values):
        P = (P3 - face.origin) @ F.T                  # (M, 2)
        M = len(P)
        if M < 2:
            continue
        D = P[None, :, :] - x[:, None, :]             # (N, M, 2) vectors to the loop points
        r = np.linalg.norm(D, axis=2)                 # (N, M)
        # at a loop point
        at = r < tol
        hit = at.any(axis=1) & ~exact
        if hit.any():
            j = np.argmax(at[hit], axis=1)
            out[hit] = val[j]
            exact[hit] = True
        Dn = np.roll(D, -1, axis=1)
        rn = np.roll(r, -1, axis=1)
        cross = D[..., 0] * Dn[..., 1] - D[..., 1] * Dn[..., 0]
        dot = (D * Dn).sum(axis=2)
        seg = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)[None, :]
        # on a segment: collinear and between the end points
        on = (np.abs(cross) <= tol * np.maximum(seg, tol)) & (dot < 0)
        hit = on.any(axis=1) & ~exact
        if hit.any():
            j = np.argmax(on[hit], axis=1)
            idx = np.nonzero(hit)[0]
            t = r[idx, j] / np.maximum(r[idx, j] + rn[idx, j], 1e-300)
            out[hit] = (1 - t)[:, None] * val[j] + t[:, None] * val[(j + 1) % M]
            exact[hit] = True
        alpha = np.arctan2(cross, dot)                # signed angle at x between consecutive points
        tanh = np.tan(alpha / 2)
        w = (np.roll(tanh, 1, axis=1) + tanh) / np.maximum(r, 1e-300)
        num += w @ val
        den += w.sum(axis=1)
    ok = ~exact
    out[ok] = num[ok] / np.where(np.abs(den[ok]) > 1e-300, den[ok], 1.0)[:, None]
    return out


def inside_polygon(face: Face, X: np.ndarray, tol: float) -> np.ndarray:
    """Points X (on the plane of face) inside the face or on its boundary (even-odd rule over all loops)."""
    F = _frame(face.normal)
    x = (X - face.origin) @ F.T
    inside = np.zeros(len(x), bool)
    near = np.zeros(len(x), bool)
    for P3 in face.loops:
        P = (P3 - face.origin) @ F.T
        Q = np.roll(P, -1, axis=0)
        for (ax, ay), (bx, by) in zip(P, Q):
            cond = (ay > x[:, 1]) != (by > x[:, 1])
            xi = ax + (x[:, 1] - ay) * (bx - ax) / np.where(by != ay, by - ay, 1e-300)
            inside ^= cond & (x[:, 0] < xi)
            # distance to the segment
            d = np.array([bx - ax, by - ay])
            L2 = max(d @ d, 1e-300)
            t = np.clip(((x[:, 0] - ax) * d[0] + (x[:, 1] - ay) * d[1]) / L2, 0, 1)
            dist = np.hypot(x[:, 0] - (ax + t * d[0]), x[:, 1] - (ay + t * d[1]))
            near |= dist < tol
    return inside | near


# ------------------------------------------------------------------------------------------------
# mesh side
# ------------------------------------------------------------------------------------------------
def _laplacian(nodes: np.ndarray, tets: np.ndarray):
    """P1 Laplace stiffness on the tetrahedra, every element weighted with 1/volume (stiff small elements)."""
    import scipy.sparse as sp
    X = nodes.T[tets.T]                               # (T, 4, 3)
    J = (X[:, 1:, :] - X[:, :1, :])                   # (T, 3, 3) rows: edges from vertex 0
    det = np.linalg.det(J)
    vol = np.abs(det) / 6.0
    vol = np.maximum(vol, 1e-30)
    Jinv = np.linalg.inv(J)                           # columns: gradients of barycentric 1..3
    G = np.concatenate([-Jinv.sum(axis=2, keepdims=True), Jinv], axis=2)   # (T, 3, 4)
    Ke = np.einsum("tki,tkj->tij", G, G) * vol[:, None, None]             # (T, 4, 4)
    w = vol.max() / vol
    Ke *= w[:, None, None]
    rows = np.repeat(tets.T, 4, axis=1).ravel()
    cols = np.tile(tets.T, (1, 4)).ravel()
    n = nodes.shape[1]
    return sp.csr_matrix((Ke.ravel(), (rows, cols)), shape=(n, n))


@dataclass
class NodeClass:
    """Node constraints shared by all variables: fixed nodes, sliding nodes with their constraint normals."""
    fixed: np.ndarray                 # bool (N,)
    normals: dict                     # node -> list of unit normals the motion must stay perpendicular to
    on_face: dict                     # face key -> node indices on it (planar probe faces)


def classify(mesh, faces: list[Face], fixed_ftags: set, tol: float):
    """Which nodes lie on which object face; nodes on fixed faces (ports, outer boundary) and nodes on no
    plane object face (but on a mesh face) are fixed."""
    nodes = mesh.nodes
    N = nodes.shape[1]
    tris = mesh.tris
    fixed = np.zeros(N, bool)
    on_face: dict = {}
    surf_nodes = set()
    for ftag, tri_ids in mesh.ftag_to_tri.items():
        tri_ids = np.asarray(tri_ids, int)
        if not len(tri_ids):
            continue
        nid = np.unique(tris[:, tri_ids])
        surf_nodes.update(nid.tolist())
        if ftag in fixed_ftags:
            fixed[nid] = True
            continue
        C = nodes[:, tris[:, tri_ids[: min(len(tri_ids), 5)]]].mean(axis=1).T     # a few triangle centroids
        lo, hi = nodes[:, nid].min(axis=1), nodes[:, nid].max(axis=1)
        matched = False
        for f in faces:
            if np.any(lo < f.bbox[0] - tol) or np.any(hi > f.bbox[1] + tol):
                continue
            if f.planar:
                if np.abs((nodes[:, nid].T - f.origin) @ f.normal).max() > tol:
                    continue
                if not inside_polygon(f, C, tol).all():
                    continue
            else:
                # curved face: only exact containment by bounding box; it may only translate
                pass
            on_face.setdefault((f.obj, f.index), set()).update(nid.tolist())
            matched = True
        if not matched:
            fixed[nid] = True
    on_face = {k: np.array(sorted(v), int) for k, v in on_face.items()}
    return fixed, on_face, np.array(sorted(surf_nodes), int)


def velocity_fields(mesh, faces_per_var: dict[str, list[Face]], fixed_ftags: set, tol: float,
                    log=None) -> dict[str, np.ndarray]:
    """Velocity field (3, N) of the mesh nodes for every variable."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import spsolve
    nodes = mesh.nodes
    N = nodes.shape[1]
    any_faces = next(iter(faces_per_var.values()))
    fixed0, on_face, _ = classify(mesh, any_faces, fixed_ftags, tol)
    # Dirichlet values per variable; nodes on a moving face of any variable are fixed for all
    moving_nodes = np.zeros(N, bool)
    d0 = {v: np.zeros((N, 3)) for v in faces_per_var}
    count = {v: np.zeros(N) for v in faces_per_var}
    for var, faces in faces_per_var.items():
        for f in faces:
            nid = on_face.get((f.obj, f.index))
            if nid is None or not len(nid) or not f.moving:
                continue
            X = nodes[:, nid].T
            if f.planar:
                vel = mvc_interpolate(f, X, f.velocity, tol)
            else:
                vv = f.vertex_velocity
                if vv is None or not len(vv) or np.abs(vv - vv[0]).max() > 1e-6 * max(np.abs(vv).max(), 1e-30):
                    raise MorphError(f"variable '{var}' deforms the curved face {f.index + 1} of '{f.obj}'; "
                                     "only plane faces may change shape (curved faces may move)")
                vel = np.repeat(vv[:1], len(nid), axis=0)
            if fixed0[nid].any() and np.abs(vel[fixed0[nid]]).max() > 1e-9:
                raise MorphError(f"variable '{var}' moves '{f.obj}' at a port or at the outer boundary")
            d0[var][nid] += vel
            count[var][nid] += 1
            moving_nodes[nid] = True
    for var in d0:
        c = count[var]
        d0[var][c > 0] /= c[c > 0][:, None]
    # sliding constraints from the faces that do not move (same for all variables: union of moving faces)
    normals: dict[int, list] = {}
    for f in any_faces:
        nid = on_face.get((f.obj, f.index))
        if nid is None or not len(nid):
            continue
        if not f.planar:
            fixed0[nid] = True
            continue
        X = nodes[:, nid].T
        # nodes inside the face slide in its plane, nodes on its boundary along the boundary segment
        # (the in-plane normal of the segment is a second constraint)
        for i in nid:
            normals.setdefault(int(i), []).append(f.normal)
        for P in f.loops:
            Q = np.roll(P, -1, axis=0)
            for a, b in zip(P, Q):
                d = b - a
                L = np.linalg.norm(d)
                if L < tol:
                    continue
                t = d / L
                s = np.clip((X - a) @ t, 0, L)
                dist = np.linalg.norm(X - (a + s[:, None] * t), axis=1)
                on = dist < tol
                if on.any():
                    en = np.cross(f.normal, t)
                    for i in nid[on]:
                        normals.setdefault(int(i), []).append(en)
    fixed = fixed0 | moving_nodes
    # free directions per node: three for a node inside the volumes, the null space of its constraint
    # normals for a node on a face
    cols_r, cols_c, cols_v = [], [], []
    inner = np.flatnonzero(~fixed & ~np.isin(np.arange(N), np.fromiter(normals, int, len(normals))))
    nz = 3 * len(inner)
    cols_r.append((3 * inner[:, None] + np.arange(3)[None, :]).ravel())
    cols_c.append(np.arange(nz))
    cols_v.append(np.ones(nz))
    for i, ns in normals.items():
        if fixed[i]:
            continue
        _, sv, vt = np.linalg.svd(np.asarray(ns))
        for b in vt[int((sv > 1e-6).sum()):]:
            k = np.flatnonzero(b != 0)
            cols_r.append(3 * i + k)
            cols_c.append(np.full(len(k), nz))
            cols_v.append(b[k])
            nz += 1
    cols_r, cols_c, cols_v = (np.concatenate(a) for a in (cols_r, cols_c, cols_v))
    L = _laplacian(nodes, mesh.tets)
    K3 = sp.kron(L, sp.identity(3), format="csr")      # node-major: 3*i + k
    out = {}
    if nz == 0:
        return {v: d0[v].T.copy() for v in d0}
    Q = sp.csr_matrix((cols_v, (cols_r, cols_c)), shape=(3 * N, nz))
    Kr = (Q.T @ K3 @ Q).tocsc()
    rhs = np.column_stack([-(Q.T @ (K3 @ d0[v].ravel())) for v in d0])
    Z = spsolve(Kr, rhs)
    Z = Z.reshape(nz, -1)
    for j, v in enumerate(d0):
        d = d0[v].ravel() + Q @ Z[:, j]
        out[v] = d.reshape(N, 3).T.copy()
    if log is not None:
        log(f"morphing: {int(fixed.sum())} fixed nodes, {int(moving_nodes.sum())} on moving faces, {nz} free "
            f"directions")
    return out


def min_quality_factor(nodes: np.ndarray, tets: np.ndarray, V: np.ndarray, dp: float) -> float:
    """Smallest ratio of morphed to nominal tetrahedron volume at the offset dp (below 0: inverted)."""
    def vol(X):
        P = X.T[tets.T]
        return np.linalg.det(P[:, 1:, :] - P[:, :1, :])
    v0 = vol(nodes)
    v1 = vol(nodes + dp * V)
    return float(np.min(v1 / v0))


def valid_range(nodes: np.ndarray, tets: np.ndarray, V: np.ndarray, limit: float = 0.3) -> tuple[float, float]:
    """Offsets (negative, positive) of the variable up to which no tetrahedron shrinks below `limit` of
    its volume (the volume of a morphed tetrahedron is cubic in the offset, so this is found by bisection)."""
    moving = np.abs(V).max(axis=0) > 0
    if not moving.any():
        return -np.inf, np.inf
    sel = moving[tets].any(axis=0)
    T = tets[:, sel]
    P = nodes.T[T.T]
    W = V.T[T.T]
    J0 = P[:, 1:, :] - P[:, :1, :]
    Jv = W[:, 1:, :] - W[:, :1, :]
    v0 = np.linalg.det(J0)

    def ratio(dp):
        return np.min(np.linalg.det(J0 + dp * Jv) / v0)

    scale = np.abs(J0).max() / max(np.abs(Jv).max(), 1e-300)
    out = []
    for sgn in (-1, 1):
        hi = scale
        while ratio(sgn * hi) > limit and hi < 1e6 * scale:
            hi *= 2
        lo = 0.0
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            if ratio(sgn * mid) > limit:
                lo = mid
            else:
                hi = mid
        out.append(sgn * lo)
    return out[0], out[1]
