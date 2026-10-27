"""Geometry kernel on top of the gmsh OpenCASCADE interface.

The same builder is used by the GUI (for display and picking) and by the solver
process (inside EMerge's gmsh model), so face indices mean the same thing in both.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import json
import math
from dataclasses import dataclass, field
from typing import Callable

import gmsh
import numpy as np

from ..model.design import Design, GeoObject, Command
from ..model import schema
from ..model.arcs import angular_arc, arc_center
from ..model.expr import Evaluator, ExprError

occ = None  # set lazily to gmsh.model.occ


class GeometryError(RuntimeError):
    pass


def ensure_gmsh(model_name: str = "hertzon") -> None:
    if not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.Verbosity", 1)
    try:
        gmsh.model.setCurrent(model_name)
    except Exception:  # noqa: BLE001
        gmsh.model.add(model_name)


def _occ():
    return gmsh.model.occ


AXIS_VEC = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
# in-plane (u, v) directions for sheets with a given normal axis
AXIS_UV = {"Z": ((1, 0, 0), (0, 1, 0)), "X": ((0, 1, 0), (0, 0, 1)), "Y": ((0, 0, 1), (1, 0, 0))}


def _orient_from_z(dimtags, axis: str, center) -> None:
    """Rotate geometry built along +Z so that it is aligned with ``axis`` about ``center``."""
    cx, cy, cz = center
    if axis == "X":
        _occ().rotate(dimtags, cx, cy, cz, 0, 1, 0, math.pi / 2)
    elif axis == "Y":
        _occ().rotate(dimtags, cx, cy, cz, 1, 0, 0, -math.pi / 2)


def _polygon_surface(pts: list[tuple[float, float, float]]) -> list[tuple[int, int]]:
    o = _occ()
    ptags = [o.addPoint(*p) for p in pts]
    ltags = [o.addLine(ptags[i], ptags[(i + 1) % len(ptags)]) for i in range(len(ptags))]
    loop = o.addCurveLoop(ltags)
    return [(2, o.addPlaneSurface([loop]))]


def _segment_types(spec: str, n: int) -> list[tuple[str, int, list[str]]]:
    """'Line,Arc,Spline:4,AngularArc:90:XY' -> [(kind, number of points incl. shared start, extra arguments)].

    AngularArc (center point arc) uses start, center and end point; its arguments are the sweep angle
    (unitless = degrees, counter-clockwise about the plane normal) and the plane (XY, YZ or ZX, default XY)."""
    out = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        kind, *args = tok.split(":")
        kind = {"line": "Line", "arc": "Arc", "3 point arc": "Arc", "spline": "Spline", "angulararc": "AngularArc",
                "center point arc": "AngularArc", "centerarc": "AngularArc"}.get(kind.strip().lower(), "Line")
        if kind == "AngularArc":
            out.append((kind, 3, [a.strip() for a in args]))
            continue
        default = {"Line": 2, "Arc": 3, "Spline": 3}[kind]
        try:
            num = int(args[0]) if args and args[0].strip() else default
        except ValueError:
            num = default
        out.append((kind, max(2, num), []))
    return out


def _collinear(a, b, c) -> bool:
    a, b, c = (np.asarray(x, float) for x in (a, b, c))
    L = max(np.linalg.norm(c - a), np.linalg.norm(b - a), 1e-30)
    return np.linalg.norm(np.cross(b - a, c - a)) < 1e-9 * L * L


def _axis_in_cs(p: dict, ev: Evaluator, ctx, kind: str):
    """Global centre and axis of Rotate / Sweep Around Axis (given in the command's coordinate system)."""
    from ..model import objframe
    try:
        c, ax = objframe.rotation_axis(ctx.design, p, ev)
    except (KeyError, ValueError) as e:
        raise GeometryError(f"{kind}: coordinate system: {e}") from e
    return [float(x) for x in c], [float(x) for x in ax]


def _axis_frame(loc: np.ndarray, axis: str) -> np.ndarray:
    """Points given in a local frame with +Z = axis -> global coordinates (same convention as _orient_from_z)."""
    x, y, z = loc[:, 0], loc[:, 1], loc[:, 2]
    if axis == "X":
        return np.c_[z, y, -x]
    if axis == "Y":
        return np.c_[x, z, -y]
    return loc.copy()


def _path_pieces(P: list, bend: float) -> list[tuple]:
    """Polyline through P with every corner rounded by radius ``bend``:
    [("line", a, b) | ("arc", a, centre, b, axis, angle)]."""
    Q = [np.asarray(P[0], float)]
    for q in P[1:]:
        q = np.asarray(q, float)
        if np.linalg.norm(q - Q[-1]) > 1e-9 * max(bend, 1e-12):
            Q.append(q)
    P = Q
    if len(P) < 2:
        raise GeometryError("Sweep path is degenerate")
    seg_len = [float(np.linalg.norm(P[i + 1] - P[i])) for i in range(len(P) - 1)]
    out, cur = [], P[0]
    for i in range(1, len(P) - 1):
        u1 = (P[i] - P[i - 1]) / seg_len[i - 1]
        u2 = (P[i + 1] - P[i]) / seg_len[i]
        half = math.acos(float(np.clip(-u1 @ u2, -1, 1))) / 2
        if half > math.pi / 2 - 1e-6:       # straight on: no corner
            continue
        tan_h = math.tan(half)
        R = min(bend, 0.45 * min(seg_len[i - 1], seg_len[i]) * tan_h)
        t = R / tan_h
        a1, a2 = P[i] - u1 * t, P[i] + u2 * t
        bis = (-u1 + u2)
        bis = bis / np.linalg.norm(bis)
        ctr = P[i] + bis * R / math.sin(half)
        axis = np.cross(a1 - ctr, a2 - ctr)
        axis = axis / np.linalg.norm(axis)
        out.append(("line", cur, a1))
        out.append(("arc", a1, ctr, a2, axis, math.pi - 2 * half))
        cur = a2
    out.append(("line", cur, P[-1]))
    return [x for x in out if x[0] == "arc" or np.linalg.norm(x[2] - x[1]) > 1e-12]


def _sweep_pieces(prof: list, pieces: list) -> list[tuple[int, int]]:
    """Sweep a profile along lines and arcs by extruding / revolving it piece by piece (exact cylinders and
    tori, unlike a BSpline pipe), then unite the pieces."""
    o = _occ()
    dim = max(d for d, _ in prof)
    cur = [d for d in prof if d[0] == dim]
    bodies = []
    for pc in pieces:
        if pc[0] == "line":
            ex = o.extrude(cur, *(pc[2] - pc[1]))
        else:
            _, a, c, b, axis, ang = pc
            ex = o.revolve(cur, *c, *axis, ang)
        top = []
        for i, d in enumerate(ex):
            if d[0] == dim + 1:
                bodies.append(d)
                if i > 0 and ex[i - 1][0] == dim:
                    top.append(ex[i - 1])
        cur = top
    o.remove([d for d in prof if d[0] == dim], recursive=True)
    if len(bodies) == 1:
        return bodies
    out, _ = o.fuse(bodies[:1], bodies[1:])
    return [d for d in out if d[0] == dim + 1]


def _round_wire(pts: list, r: float) -> list[tuple[int, int]]:
    """Round wire of radius r along straight segments whose corners are rounded (bondwires)."""
    o = _occ()
    P = [np.asarray(q, float) for q in pts]
    pieces = _path_pieces(P, 3 * r)
    tng = pieces[0][2] - pieces[0][1] if pieces[0][0] == "line" else np.cross(pieces[0][4], pieces[0][1] - pieces[0][2])
    circ = o.addCircle(*P[0], r, zAxis=list(tng / np.linalg.norm(tng)))
    disk = o.addPlaneSurface([o.addCurveLoop([circ])])
    return _sweep_pieces([(2, disk)], pieces)


def _sweep_along(dt: list, path: list, trihedron: str) -> list[tuple[int, int]]:
    """Sweep Along Path. A polyline path gets its corners rounded so the swept body stays valid."""
    o = _occ()
    o.synchronize()
    curves = [t for d, t in path if d == 1]
    types = {gmsh.model.getType(1, c) for c in curves}
    dim = max(d for d, _ in dt)
    prof = [d for d in dt if d[0] == dim]
    if types <= {"Line"}:
        # ordered vertices of the polyline
        pts = []
        for c in curves:
            vs = gmsh.model.getBoundary([(1, c)], oriented=True)
            ends = [np.asarray(gmsh.model.getValue(0, abs(t), []), float) for _, t in vs]
            if not pts:
                pts += ends
            else:
                pts.append(ends[1] if np.allclose(ends[0], pts[-1]) else ends[0])
        bx = _bbox(prof)
        size = max(bx[3] - bx[0], bx[4] - bx[1], bx[5] - bx[2])
        res = _sweep_pieces(prof, _path_pieces(pts, 0.75 * size))
    else:
        if dim == 2:
            _check_profile_crosses(prof, curves)
        out = o.addPipe(prof, o.addWire(curves), trihedron or "DiscreteTrihedron")
        res = [d for d in out if d[0] == dim + 1]
        o.remove(prof, recursive=True)      # the pipe is a copy, the profile would stay as a loose sheet
    o.remove(path, recursive=True)
    if not res:
        raise GeometryError("Sweep along path failed")
    return res


def _check_profile_crosses(prof: list, curves: list[int]) -> None:
    """A sheet lying along the path gives a degenerate OCC pipe whose display mesh never finishes.
    Such a profile (e.g. a circle drawn in XY at a helix along Z) is turned perpendicular to the path
    and moved to the path start, so the sweep is what the user meant."""
    o = _occ()
    n = _face_normal(prof)
    c = curves[0]
    lo, _ = gmsh.model.getParametrizationBounds(1, c)
    t = np.asarray(gmsh.model.getDerivative(1, c, [lo[0]]), float)
    t /= np.linalg.norm(t) + 1e-300
    if abs(float(n @ t)) >= 0.5:
        return
    start = np.asarray(gmsh.model.getValue(1, c, [lo[0]]), float)
    cen = np.asarray(o.getCenterOfMass(*prof[0]), float)
    ax = np.cross(n, t)
    s_ = np.linalg.norm(ax)
    if s_ > 1e-12:
        ang = math.atan2(s_, float(n @ t))
        o.rotate(prof, *cen, *(ax / s_), ang)
    o.translate(prof, *(start - cen))
    o.synchronize()


def _pipe(wire: int, path: np.ndarray, radius: float) -> list[tuple[int, int]]:
    """Solid round wire of ``radius`` swept along the curve ``wire`` (Sweep Along Path)."""
    o = _occ()
    o.synchronize()
    try:
        lo, _ = gmsh.model.getParametrizationBounds(1, wire)
        tng = np.asarray(gmsh.model.getDerivative(1, wire, [lo[0]]), float)[:3]
        start = np.asarray(gmsh.model.getValue(1, wire, [lo[0]]), float)[:3]
    except Exception:  # noqa: BLE001
        tng, start = path[1] - path[0], path[0]
    circ = o.addCircle(*start, radius, zAxis=list(tng / np.linalg.norm(tng)))
    disk = o.addPlaneSurface([o.addCurveLoop([circ])])
    w = o.addWire([wire])
    out = o.addPipe([(2, disk)], w, "DiscreteTrihedron")
    # the pipe is a copy: the profile disk and the path stayed behind as loose entities, and a loose
    # face in the solver model has no tetrahedra on either side (KeyError when the mesh is read)
    o.remove([(2, disk), (1, wire)], recursive=True)
    return [d for d in out if d[0] == 3]


# optional callable(fraction, text) for long geometry builds (STL import progress dialog)
PROGRESS = None


def _faceted_solid(V: np.ndarray, faces: list[list[int]]) -> list[tuple[int, int]]:
    """Closed polyhedron from vertices and planar faces (segmented cone / sphere)."""
    o = _occ()
    pt = [o.addPoint(*map(float, v)) for v in V]
    lines: dict[tuple[int, int], int] = {}

    def line(a, b):
        k = (min(a, b), max(a, b))
        if k not in lines:
            lines[k] = o.addLine(pt[k[0]], pt[k[1]])
        return lines[k] if a < b else -lines[k]
    surfs = []
    for k, f in enumerate(faces):
        loop = o.addCurveLoop([line(f[i], f[(i + 1) % len(f)]) for i in range(len(f))])
        surfs.append(o.addPlaneSurface([loop]))
        if PROGRESS is not None and k % 250 == 0:
            PROGRESS(k / len(faces), f"Building faces {k}/{len(faces)}")
    if PROGRESS is not None:
        PROGRESS(1.0, "Sewing the solid ...")
    sl = o.addSurfaceLoop(surfs)
    return [(3, o.addVolume([sl]))]


def sphere_polyhedron(n: int):
    """Unit sphere polyhedron with n corners spread evenly (no poles): exact Platonic solids for
    4/6/12, otherwise a Fibonacci point set; faces are the triangles of the convex hull."""
    from scipy.spatial import ConvexHull
    if n == 4:
        V = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float) / np.sqrt(3)
    elif n == 6:
        V = np.vstack([np.eye(3), -np.eye(3)])
    elif n == 12:
        g = (1 + 5 ** 0.5) / 2
        V = np.array([[0, s1, s2 * g] for s1 in (1, -1) for s2 in (1, -1)], float)
        V = np.vstack([V, np.roll(V, 1, 1), np.roll(V, 2, 1)])
        V /= np.linalg.norm(V[0])
    else:
        i = np.arange(n) + 0.5
        z = 1 - 2 * i / n
        t = np.pi * (1 + 5 ** 0.5) * i
        s = np.sqrt(1 - z * z)
        V = np.stack([s * np.cos(t), s * np.sin(t), z], 1)
    faces = []
    for f in ConvexHull(V).simplices:
        a, b, c = V[f]
        faces.append(list(f) if np.dot(np.cross(b - a, c - a), a + b + c) > 0 else [f[0], f[2], f[1]])
    return V, faces


def _frame_of(axis: str):
    u, v = (np.asarray(a, float) for a in AXIS_UV[axis])
    return u, v, np.asarray(AXIS_VEC[axis], float)


def _regular_polygon(center, start, n: int, axis: str) -> list[tuple[float, float, float]]:
    c = np.asarray(center, float)
    s = np.asarray(start, float) - c
    a = np.asarray(AXIS_VEC[axis])
    s = s - a * (s @ a)
    if np.linalg.norm(s) < 1e-15:
        raise GeometryError("Start position must not coincide with the center")
    pts = []
    for i in range(n):
        ang = 2 * math.pi * i / n
        # Rodrigues rotation of s about a
        r = s * math.cos(ang) + np.cross(a, s) * math.sin(ang) + a * (a @ s) * (1 - math.cos(ang))
        pts.append(tuple(c + r))
    return pts


@dataclass
class BuildContext:
    design: Design
    ev: Evaluator
    imports: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    used_imports: set[tuple[int, int]] = field(default_factory=set)
    built: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    stack: list[str] = field(default_factory=list)


def _create(cmd: Command, ctx: BuildContext) -> list[tuple[int, int]]:
    ev, p, o = ctx.ev, cmd.props, _occ()
    k = cmd.kind
    if k == "CreateBox":
        x, y, z = ev.point(p["Position"])
        dx, dy, dz = ev.length(p["XSize"]), ev.length(p["YSize"]), ev.length(p["ZSize"])
        if min(abs(dx), abs(dy), abs(dz)) <= 0:
            raise GeometryError("Box sizes must be non-zero")
        x, dx = (x + dx, -dx) if dx < 0 else (x, dx)
        y, dy = (y + dy, -dy) if dy < 0 else (y, dy)
        z, dz = (z + dz, -dz) if dz < 0 else (z, dz)
        return [(3, o.addBox(x, y, z, dx, dy, dz))]
    if k in ("CreateCylinder", "CreateCone"):
        c = ev.point(p["CenterPosition"])
        h = ev.length(p["Height"])
        ax = np.asarray(AXIS_VEC[p["Axis"]]) * h
        if k == "CreateCylinder":
            r = ev.length(p["Radius"])
            nseg = ev.integer(p.get("NumberOfSegments", "0") or "0")
            if r <= 0 or h == 0:
                raise GeometryError("Radius and height must be non-zero")
            if nseg >= 3:
                start = np.asarray(c) + np.asarray(AXIS_UV[p["Axis"]][0]) * r
                surf = _polygon_surface(_regular_polygon(c, start, nseg, p["Axis"]))
                out = o.extrude(surf, *ax)
                return [d for d in out if d[0] == 3]
            return [(3, o.addCylinder(*c, *ax, r))]
        r1, r2 = ev.length(p["LowerRadius"]), ev.length(p["UpperRadius"])
        if r1 < 0 or r2 < 0 or (r1 == 0 and r2 == 0) or h == 0:
            raise GeometryError("Invalid cone dimensions")
        nseg = ev.integer(p.get("NumberOfSegments", "0") or "0")
        if nseg >= 3:
            u, v, _ = _frame_of(p["Axis"])
            c0 = np.asarray(c, float)
            ang = 2 * np.pi * np.arange(nseg) / nseg
            ring = lambda r, z0: [c0 + z0 + r * (np.cos(a) * u + np.sin(a) * v) for a in ang]  # noqa: E731
            V, faces = [], []
            if r1 > 0:
                V += ring(r1, 0)
            else:
                V.append(c0)
            n0 = len(V)
            if r2 > 0:
                V += ring(r2, ax)
            else:
                V.append(c0 + ax)
            for i in range(nseg):
                j = (i + 1) % nseg
                a = [i, j] if r1 > 0 else [0]
                b = [n0 + j, n0 + i] if r2 > 0 else [n0]
                faces.append(a + b)
            if r1 > 0:
                faces.append(list(range(nseg))[::-1])
            if r2 > 0:
                faces.append(list(range(n0, n0 + nseg)))
            return _faceted_solid(np.array(V), faces)
        return [(3, o.addCone(*c, *ax, r1, r2))]
    if k == "CreateSphere":
        r = ev.length(p["Radius"])
        if r <= 0:
            raise GeometryError("Radius must be positive")
        nv = ev.integer(p.get("NumberOfVertices", "0") or "0")
        nseg = ev.integer(p.get("NumberOfSegments", "0") or "0")
        # old projects without NumberOfVertices: same corner count as the former UV sphere. Once the
        # property exists (the dialog only offers that one), 0 means the true sphere.
        if "NumberOfVertices" not in p and nseg >= 3:
            nv = nseg * (max(2, nseg // 2) - 1) + 2
        if nv > 0:
            if nv < 4:
                raise GeometryError("A faceted sphere needs at least 4 vertices (tetrahedron)")
            V, faces = sphere_polyhedron(nv)
            V = np.asarray(ev.point(p["CenterPosition"]), float) + r * V
            return _faceted_solid(np.array(V), faces)
        return [(3, o.addSphere(*ev.point(p["CenterPosition"]), r))]
    if k == "CreateTorus":
        c = ev.point(p["CenterPosition"])
        return [(3, o.addTorus(*c, ev.length(p["MajorRadius"]), ev.length(p["MinorRadius"]),
                               zAxis=list(AXIS_VEC[p["Axis"]])))]
    if k == "CreateRegularPolyhedron":
        c = ev.point(p["CenterPosition"])
        pts = _regular_polygon(c, ev.point(p["StartPosition"]), max(3, ev.integer(p["NumberOfSides"])), p["Axis"])
        surf = _polygon_surface(pts)
        ax = np.asarray(AXIS_VEC[p["Axis"]]) * ev.length(p["Height"])
        return [d for d in o.extrude(surf, *ax) if d[0] == 3]
    if k == "CreateRectangle":
        x0 = np.asarray(ev.point(p["Position"]))
        u, v = (np.asarray(a, float) for a in AXIS_UV[p["Axis"]])
        sa, sb = schema.RECT_SIZES[p["Axis"]]
        p = schema.upgrade_props(k, dict(p))
        w, h = ev.length(p.get(sa, "1")), ev.length(p.get(sb, "1"))
        if w == 0 or h == 0:
            raise GeometryError("Rectangle sizes must be non-zero")
        return _polygon_surface([tuple(x0), tuple(x0 + u * w), tuple(x0 + u * w + v * h), tuple(x0 + v * h)])
    if k in ("CreateCircle", "CreateEllipse"):
        c = ev.point(p["CenterPosition"])
        if k == "CreateCircle":
            r1 = r2 = ev.length(p["Radius"])
            nseg = ev.integer(p.get("NumberOfSegments", "0") or "0")
            if nseg >= 3:
                start = np.asarray(c) + np.asarray(AXIS_UV[p["Axis"]][0]) * r1
                return _polygon_surface(_regular_polygon(c, start, nseg, p["Axis"]))
        else:
            r1 = ev.length(p["MajorRadius"])
            r2 = r1 * ev.value(p["Ratio"])
            if r2 > r1:
                r1, r2 = r2, r1
        if r1 <= 0 or r2 <= 0:
            raise GeometryError("Radius must be positive")
        dt = [(2, o.addDisk(*c, r1, r2))]
        _orient_from_z(dt, p["Axis"], c)
        return dt
    if k == "CreateRegularPolygon":
        c = ev.point(p["CenterPosition"])
        return _polygon_surface(_regular_polygon(c, ev.point(p["StartPosition"]),
                                                 max(3, ev.integer(p["NumberOfSides"])), p["Axis"]))
    if k == "CreatePolyline":
        pts = ev.points(p["Points"])
        if len(pts) < 2:
            raise GeometryError("A polyline needs at least two points")
        closed = p.get("Closed") == "True"
        ptags = [o.addPoint(*q) for q in pts]
        n = len(ptags)
        ends_on_start = n > 2 and np.allclose(pts[-1], pts[0])
        if ends_on_start:   # curved closing segment: the last point is the start point
            o.remove([(0, ptags[-1])])
            ptags[-1] = ptags[0]
        segs = []
        i = 0
        # segment types: Line (2 points), 3 Point Arc (3), Spline (n points), Center Point Arc (start, center,
        # end); points are shared
        for kind, cnt, args in _segment_types(p.get("SegmentTypes", ""), n):
            j = min(n - 1, i + cnt - 1)
            if j <= i:
                break
            if kind == "AngularArc" and j - i == 2:
                # AngularArc: the angle decides the end point, the stored end point is only a copy of it
                ang = ev.angle(args[0]) if args and args[0] else 0.0
                plane = args[1] if len(args) > 1 else "XY"
                arc = angular_arc(pts[i], pts[i + 1], ang, plane)
                ctr = arc_center(pts[i], pts[i + 1], plane)
                tol = 1e-9 * max(np.abs(arc).max(), 1e-30)
                if abs(ang) < 1e-12 or np.linalg.norm(arc[0] - ctr) < tol:
                    raise GeometryError("A center point arc needs a start point off its center and an angle")
                if not np.allclose(arc[-1], pts[j], rtol=0, atol=tol):
                    ptags[j] = o.addPoint(*arc[-1])
                    pts[j] = tuple(arc[-1])
                    if j == n - 1:
                        ends_on_start = np.allclose(arc[-1], pts[0], rtol=0, atol=tol)
                        if ends_on_start:
                            ptags[j] = ptags[0]
                c = o.addPoint(*ctr)
                tags = [ptags[i]] + [o.addPoint(*q) for q in arc[1:-1]] + [ptags[j]]
                segs += [o.addCircleArc(p0, c, p1) for p0, p1 in zip(tags, tags[1:])]
            elif kind == "Arc" and j - i == 2:
                if _collinear(pts[i], pts[i + 1], pts[j]):
                    segs.append(o.addLine(ptags[i], ptags[j]))
                else:
                    segs.append(o.addCircleArc(ptags[i], ptags[i + 1], ptags[j], center=False))
            elif kind == "Spline" and j - i >= 2:
                segs.append(o.addSpline(ptags[i:j + 1]))
            else:
                segs += [o.addLine(ptags[q], ptags[q + 1]) for q in range(i, j)]
            i = j
        if i < n - 1:
            segs += [o.addLine(ptags[q], ptags[q + 1]) for q in range(i, n - 1)]
        if closed and not ends_on_start:
            segs.append(o.addLine(ptags[-1], ptags[0]))
        if closed and p.get("Covered") == "True":
            loop = o.addCurveLoop(segs)
            return [(2, o.addPlaneSurface([loop]))]
        return [(1, s) for s in segs]
    if k == "CreateBondwire":
        # bondwire, JEDEC 4-point profile: up by H1, over at the top, down to the end pad
        a = np.asarray(ev.point(p["StartPosition"]), float)
        b = np.asarray(ev.point(p["EndPosition"]), float)
        h1 = ev.length(p["H1"])
        h2 = ev.length(p["H2"])
        d = ev.length(p["Diameter"])
        hor = b - a
        hor[2] = 0.0
        L = float(np.linalg.norm(hor))
        if L <= 0 or d <= 0:
            raise GeometryError("Bondwire needs distinct end points and a positive diameter")
        u = hor / L
        top = max(a[2] + h1, b[2] + h2)
        up = np.array([0, 0, top - a[2]])
        pts = [a, a + up, a + u * L * 0.125 + up, b]
        if p.get("Type", "JEDEC 4-Point") == "JEDEC 5-Point":
            pts = [pts[0], pts[1], pts[2], b - u * L * 0.25 + np.array([0, 0, (top - b[2]) * 0.6]), b]
        pts = [np.asarray(q, float) for q in pts]
        # straight segments with the bends rounded (tangent continuous path for the sweep)
        return _round_wire(pts, d / 2)
    if k == "CreateHelix":
        c = np.asarray(ev.point(p["CenterPosition"]), float)
        r = ev.length(p["Radius"])
        pitch = ev.length(p["Pitch"])
        turns = ev.value(p["Turns"])
        wr = ev.length(p.get("WireRadius", "0") or "0")
        if r <= 0 or turns <= 0:
            raise GeometryError("Radius and turns must be positive")
        sgn = 1.0 if p.get("RightHanded", "True") == "True" else -1.0
        nseg = ev.integer(p.get("NumberOfSegmentsPerTurn", "0") or "0")
        if nseg >= 3:
            # segmented helix: straight segments, nseg per turn
            m = max(1, int(math.ceil(turns * nseg)))
            t = np.linspace(0, 2 * math.pi * turns, m + 1)
        else:
            m = max(32, int(math.ceil(turns * 64)))
            t = np.linspace(0, 2 * math.pi * turns, m + 1)
        loc = np.c_[r * np.cos(sgn * t), r * np.sin(sgn * t), pitch * t / (2 * math.pi)]
        P3 = _axis_frame(loc, p["Axis"]) + c
        if nseg >= 3 and wr > 0:
            # one pipe along the polyline with slightly rounded corners (piecewise fusing is far slower)
            pieces = _path_pieces(list(P3), 1.5 * wr)
            curves = []
            last = o.addPoint(*pieces[0][1])    # consecutive pieces share their end points
            for pc in pieces:
                end = o.addPoint(*(pc[2] if pc[0] == "line" else pc[3]))
                if pc[0] == "line":
                    curves.append(o.addLine(last, end))
                else:
                    curves.append(o.addCircleArc(last, o.addPoint(*pc[2]), end))
                last = end
            tng = P3[1] - P3[0]
            circ = o.addCircle(*P3[0], wr, zAxis=list(tng / np.linalg.norm(tng)))
            disk = o.addPlaneSurface([o.addCurveLoop([circ])])
            try:
                out = o.addPipe([(2, disk)], o.addWire(curves), "DiscreteTrihedron")
                o.remove([(2, disk)] + [(1, c_) for c_ in curves], recursive=True)
                return [d for d in out if d[0] == 3]
            except Exception:  # noqa: BLE001
                # OCC cannot pipe some many-corner paths: the smooth curve through the corners is
                # within a fraction of the wire radius of the segmented one
                o.remove([(2, disk)], recursive=True)
                o.remove([(1, c_) for c_ in curves], recursive=True)
        if nseg >= 3 and wr <= 0:
            pts = [o.addPoint(*q) for q in P3]
            lines = [(1, o.addLine(pts[i], pts[i + 1])) for i in range(len(pts) - 1)]
            return lines
        wire = o.addSpline([o.addPoint(*q) for q in P3])
        if wr <= 0:
            return [(1, wire)]
        return _pipe(wire, P3, wr)
    if k == "CreateSpiral":
        c = np.asarray(ev.point(p["CenterPosition"]), float)
        r0 = ev.length(p["InnerRadius"])
        pitch = ev.length(p["Pitch"])
        turns = ev.value(p["Turns"])
        w = ev.length(p.get("Width", "0") or "0")
        if turns <= 0 or pitch <= 0:
            raise GeometryError("Pitch and turns must be positive")
        if w >= pitch:
            raise GeometryError("Width must be smaller than the pitch")
        sgn = 1.0 if p.get("RightHanded", "True") == "True" else -1.0
        m = max(16, int(math.ceil(turns * 32)))
        t = np.linspace(0, 2 * math.pi * turns, m + 1)

        def curve(dr):
            rr = r0 + pitch * t / (2 * math.pi) + dr
            if np.any(rr <= 0):
                raise GeometryError("Inner radius too small for the width")
            return _axis_frame(np.c_[rr * np.cos(sgn * t), rr * np.sin(sgn * t), 0 * t], p["Axis"]) + c
        if w <= 0:
            return [(1, o.addSpline([o.addPoint(*q) for q in curve(0.0)]))]
        a, b = curve(-w / 2), curve(w / 2)
        pa = [o.addPoint(*q) for q in a]
        pb = [o.addPoint(*q) for q in b]
        loop = o.addCurveLoop([o.addSpline(pa), o.addLine(pa[-1], pb[-1]), o.addSpline(pb[::-1]),
                               o.addLine(pb[0], pa[0])])
        return [(2, o.addPlaneSurface([loop]))]
    if k == "Import":
        path = p["File"]
        simp = int(float(p.get("Simplify", "0") or 0))
        key = (path, simp)
        if key not in ctx.imports:
            try:
                if path.lower().endswith(".stl"):
                    out = import_stl(path, ev.unit_scale, simplify=simp)
                else:
                    out = import_cad(path, ev.unit_scale)
            except Exception as e:  # noqa: BLE001
                raise GeometryError(f"Cannot import '{path}': {e}") from e
            ctx.imports[key] = out
        bodies = ctx.imports[key]
        idx = ev.integer(p.get("Body", "0") or "0")
        if idx >= len(bodies):
            raise GeometryError(f"Body {idx} not found in '{path}' ({len(bodies)} bodies)")
        dt = bodies[idx]
        if dt in ctx.used_imports:
            dt = o.copy([dt])[0]
        ctx.used_imports.add(dt)
        return [dt]
    raise GeometryError(f"Unknown create command {k}")


def import_cad(path: str, unit_scale: float) -> list[tuple[int, int]]:
    """STEP/IGES/BREP bodies in metres (the model is built in SI units). STEP and IGES carry their
    length unit, OpenCASCADE converts it; BREP has none and is read in model units (as STL)."""
    ext = Path(path).suffix.lower()
    old = gmsh.option.getString("Geometry.OCCTargetUnit")
    gmsh.option.setString("Geometry.OCCTargetUnit", "M" if ext in (".step", ".stp", ".iges", ".igs") else "")
    try:
        out = _occ().importShapes(path, highestDimOnly=True)
    finally:
        gmsh.option.setString("Geometry.OCCTargetUnit", old)
    if ext not in (".step", ".stp", ".iges", ".igs") and out and unit_scale != 1.0:
        _occ().dilate(out, 0, 0, 0, unit_scale, unit_scale, unit_scale)
    return out


def read_stl(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (merged) and triangles of an ASCII or binary STL file."""
    data = open(path, "rb").read()
    n = int.from_bytes(data[80:84], "little") if len(data) >= 84 else -1
    if n >= 0 and len(data) == 84 + 50 * n:
        rec = np.frombuffer(data[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
        P = rec["v"].reshape(-1, 3).astype(float)
    else:
        txt = data.decode("latin-1")
        import re as _re
        nums = _re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", txt)
        if not nums:
            raise GeometryError("No triangles found in the STL file")
        P = np.array(nums, float)
    if len(P) % 3:
        raise GeometryError("Corrupt STL file")
    span = float(np.ptp(P, axis=0).max()) or 1.0
    key = np.round(P / (span * 1e-9)).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    V = P[first]
    T = inv.reshape(-1, 3)
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    return V, T


def _stl_faces(V: np.ndarray, T: np.ndarray) -> list[list[int]]:
    """Merge edge-adjacent coplanar triangles into polygons (a CAD box becomes 6 faces, not 12 triangles).
    Regions whose boundary is not one simple loop keep their triangles."""
    nrm = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-300
    dist = np.einsum("ij,ij->i", nrm, V[T[:, 0]])
    span = float(np.ptp(V, axis=0).max()) or 1.0
    parent = list(range(len(T)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    edge_tri: dict = {}
    for t, tri in enumerate(T):
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            edge_tri.setdefault((min(a, b), max(a, b)), []).append(t)
    for ts in edge_tri.values():
        if len(ts) == 2:
            i, j = ts
            if nrm[i] @ nrm[j] > 1 - 1e-9 and abs(dist[i] - dist[j]) < 1e-7 * span:
                parent[find(i)] = find(j)
    groups: dict = {}
    for t in range(len(T)):
        groups.setdefault(find(t), []).append(t)
    faces = []
    for ts in groups.values():
        if len(ts) == 1:
            faces.append([int(x) for x in T[ts[0]]])
            continue
        cnt: dict = {}
        for t in ts:
            tri = T[t]
            for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                k = (min(a, b), max(a, b))
                cnt[k] = cnt.get(k, 0) + 1
        nxt = {}
        ok = True
        for t in ts:
            tri = T[t]
            for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                if cnt[(min(a, b), max(a, b))] == 1:
                    if a in nxt:
                        ok = False
                    nxt[a] = b
        loop = []
        if ok and nxt:
            start = next(iter(nxt))
            cur = start
            while True:
                loop.append(int(cur))
                cur = nxt[cur]
                if cur == start or len(loop) > len(nxt):
                    break
            ok = len(loop) == len(nxt)
        if ok:
            faces.append(loop)
        else:
            faces += [[int(x) for x in T[t]] for t in ts]
    return faces


def simplify_stl(V: np.ndarray, T: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    """Shape-preserving decimation (quadric error metric, fast-simplification, MIT): coplanar and
    nearly coplanar triangles are merged first, sharp features are kept."""
    import fast_simplification
    P, F = fast_simplification.simplify(np.asarray(V, float), np.asarray(T, np.int32), target_count=int(target))
    return np.asarray(P, float), np.asarray(F, np.int64)


def stl_deviation(V, T, V2, T2) -> float:
    """Largest distance of the original STL vertices from the simplified surface (same units)."""
    import pyvista as pv
    a = pv.PolyData(np.asarray(V, float), np.hstack([np.full((len(T), 1), 3), T]).ravel())
    b = pv.PolyData(np.asarray(V2, float), np.hstack([np.full((len(T2), 1), 3), T2]).ravel())
    return float(np.abs(a.compute_implicit_distance(b)["implicit_distance"]).max())


def _stl_cache_file(path: str, unit_scale: float, simplify: int = 0):
    """BREP cache of a converted STL (building thousands of planar faces is slow; reading BREP is fast)."""
    import hashlib
    from pathlib import Path
    p = Path(path).resolve()
    st = p.stat()
    key = hashlib.sha1(f"v1|{p}|{st.st_size}|{st.st_mtime_ns}|{unit_scale!r}|{simplify}".encode()).hexdigest()[:20]
    d = Path.home() / ".hertzon" / "stl_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{p.stem}_{key}.brep"


def import_stl(path: str, unit_scale: float, count_only: bool = False, simplify: int = 0) -> list[tuple[int, int]]:
    """STL import (imports STL as faceted solids). Coordinates are in model units; each
    connected shell becomes one body. The converted solids are cached as BREP, so opening a project
    again (or re-importing the same file) only reads the cache."""
    if not count_only:
        try:
            cache = _stl_cache_file(path, unit_scale, simplify)
        except Exception:  # noqa: BLE001
            cache = None
        if cache is not None and cache.exists():
            if PROGRESS is not None:
                PROGRESS(0.5, "Reading cached STL geometry ...")
            out = gmsh.model.occ.importShapes(str(cache), highestDimOnly=True)
            return [(d, t) for d, t in out if d == 3]
        if cache is not None:
            cur = gmsh.model.getCurrent()
            gmsh.model.add("_hertzon_stl_cache")
            try:
                _import_stl_build(path, unit_scale, simplify=simplify)
                gmsh.model.occ.synchronize()
                tmp = cache.with_suffix(".tmp.brep")
                gmsh.write(str(tmp))
                tmp.replace(cache)
            finally:
                gmsh.model.remove()
                gmsh.model.setCurrent(cur)
            out = gmsh.model.occ.importShapes(str(cache), highestDimOnly=True)
            return [(d, t) for d, t in out if d == 3]
    return _import_stl_build(path, unit_scale, count_only, simplify)


def _import_stl_build(path: str, unit_scale: float, count_only: bool = False,
                      simplify: int = 0) -> list[tuple[int, int]]:
    V, T = read_stl(path)
    if simplify and 0 < simplify < len(T):
        if PROGRESS is not None:
            PROGRESS(0.001, f"Simplifying {len(T)} -> {simplify} triangles ...")
        V, T = simplify_stl(V, T, simplify)
    if len(T) > 40000:
        raise GeometryError(f"STL has {len(T)} triangles; more than 40000 are too many for a CAD solid. "
                            "Decimate it first (e.g. MeshLab / Blender).")
    V = V * unit_scale
    # connected components over shared vertices
    parent = list(range(len(V)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b, c in T:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    comp: dict = {}
    for t, tri in enumerate(T):
        comp.setdefault(find(tri[0]), []).append(t)
    if count_only:
        return [(3, 0)] * len(comp)
    out = []
    for ts in comp.values():
        Tc = T[ts]
        used = np.unique(Tc)
        remap = np.full(len(V), -1)
        remap[used] = np.arange(len(used))
        Vc, Tc = V[used], remap[Tc]
        faces = _stl_faces(Vc, Tc)
        edges: dict = {}
        for tri in Tc:
            for x, y in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                k = (min(x, y), max(x, y))
                edges[k] = edges.get(k, 0) + 1
        if any(c_ != 2 for c_ in edges.values()):
            raise GeometryError("STL shell is not closed (watertight); only closed shells can become solids")
        out += _faceted_solid(Vc, faces)
    return out


def _face_normal(dimtags) -> np.ndarray:
    gmsh.model.occ.synchronize()
    d, t = dimtags[0]
    lo, hi = gmsh.model.getParametrizationBounds(2, t)
    uv = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2]
    n = np.asarray(gmsh.model.getNormal(t, uv))
    return n / (np.linalg.norm(n) + 1e-300)


def thicken_direction(normal, reverse: bool = False) -> np.ndarray:
    """Direction of Thicken sheet for a sheet normal: pointing to the positive side of the sheet's main axis
    (+Z for a sheet in XY), opposite with ``reverse``. The face normal itself follows the winding of the
    outline, i.e. how the sheet was drawn (a rectangle dragged to -X, a polyline drawn clockwise), so its
    sign is not used. Also used by the direction preview of the GUI."""
    n = np.asarray(normal, float)
    n = n / (np.linalg.norm(n) + 1e-300)
    main = int(np.flatnonzero(np.abs(n) > np.abs(n).max() - 1e-9)[0])   # first axis on a tie
    n = -n if n[main] < 0 else n
    return -n if reverse else n


def migrate_thicken_direction(design: Design, ev: Evaluator) -> list[str]:
    """Older files thickened along the face normal, whose sign follows how the sheet was drawn. Set Reverse
    direction where that normal pointed to the negative side, so these sheets keep their old shape. The
    sheets are built up to each Thicken sheet step in a separate gmsh model. Returns messages for steps that
    could not be checked (they keep the new default direction)."""
    from dataclasses import replace
    steps = [(obj, i) for obj in design.objects for i, c in enumerate(obj.history)
             if c.kind == "ThickenSheet" and c.props.get("BothSides") != "True"]
    if not steps:
        return []
    prev = gmsh.model.getCurrent() if gmsh.isInitialized() else None
    msgs = []
    for obj, i in steps:
        ensure_gmsh("hertzon-migrate")
        try:
            dt = _build_object(replace(obj, history=obj.history[:i]), BuildContext(design, ev))
            n = _face_normal(dt)
            obj.history[i].props["ReverseDirection"] = str(bool(thicken_direction(n) @ n < 0))
        except Exception as e:  # noqa: BLE001
            msgs.append(f"{obj.name}: Thicken sheet direction not checked ({e})")
        finally:
            gmsh.model.remove()
    if prev:
        gmsh.model.setCurrent(prev)
    return msgs


def _apply(cmd: Command, dt: list[tuple[int, int]], ctx: BuildContext, owner: str) -> list[tuple[int, int]]:
    ev, p, o = ctx.ev, cmd.props, _occ()
    k = cmd.kind
    if k == "Move":
        o.translate(dt, *ev.vector(p["Vector"]))
        return dt
    if k == "Rotate":
        c, ax = _axis_in_cs(p, ev, ctx, k)
        o.rotate(dt, *c, *ax, ev.angle(p["Angle"]))
        return dt
    if k == "Mirror":
        b = np.asarray(ev.point(p["Base"]))
        n = np.asarray(ev.vector(p["Normal"]), float)
        n = n / (np.linalg.norm(n) + 1e-300)
        o.mirror(dt, n[0], n[1], n[2], float(-(n @ b)))
        return dt
    if k == "Scale":
        f = ev.value(p["Factor"])
        o.dilate(dt, *ev.point(p.get("Center", "0,0,0") or "0,0,0"), f, f, f)
        return dt
    if k in ("ThickenSheet", "SweepAlongVector", "SweepAroundAxis", "Cover"):
        dim = max(d for d, _ in dt)
        if k == "Cover":
            if dim != 1:
                return dt
            loop = o.addCurveLoop([t for d, t in dt if d == 1])
            o.synchronize()
            return [(2, o.addPlaneSurface([loop]))]
        if k == "ThickenSheet":
            if dim != 2:
                raise GeometryError("Thicken sheet needs a sheet object")
            t = ev.length(p["Thickness"])
            n = thicken_direction(_face_normal(dt), p.get("ReverseDirection") == "True")
            if p.get("BothSides") == "True":
                o.translate(dt, *(-n * t))
                t *= 2
            out = o.extrude(dt, *(n * t))
        elif k == "SweepAlongVector":
            out = o.extrude(dt, *ev.vector(p["Vector"]))
        else:
            c, ax = _axis_in_cs(p, ev, ctx, k)
            ang = ev.angle(p["Angle"])
            ang = min(ang, 2 * math.pi)
            out = o.revolve(dt, *c, *ax, ang)
        res = [d for d in out if d[0] == dim + 1]
        o.remove([d for d in out if d[0] == dim and d not in res], recursive=True)
        return res
    if k in ("Unite", "Subtract", "Intersect"):
        tools: list[tuple[int, int]] = []
        for tname in [t.strip() for t in p.get("Tools", "").split(",") if t.strip()]:
            if not ctx.design.has_obj(tname):
                raise GeometryError(f"Tool object '{tname}' does not exist")
            tools += _build_object(ctx.design.obj(tname), ctx)
        if not tools:
            return dt
        if k == "Unite":
            out, _ = o.fuse(dt, tools)
        elif k == "Subtract":
            out, _ = o.cut(dt, tools)
        else:
            out, _ = o.intersect(dt, tools)
        top = max((d for d, _ in out), default=0)
        out = [d for d in out if d[0] == top]
        if not out:
            raise GeometryError(f"{k} produced an empty object")
        return out
    if k == "Split":
        # the plane lies in the coordinate system of the command (XY, YZ, XZ or Custom Base/Normal);
        # the kept half space is a slab on the chosen side that reaches over the whole object
        from ..model import coordsys
        try:
            O, R = coordsys.frame(ctx.design, p.get("CoordinateSystem", "Global") or "Global", ev)
        except (KeyError, ValueError) as e:
            raise GeometryError(f"Split: coordinate system: {e}") from e
        plane = p.get("Plane", "XY")
        if plane == "Custom":
            b = O + R @ np.asarray(ev.point(p.get("Base", "0,0,0") or "0,0,0"), float)
            n = R @ _plane_normal(p, ev)
        else:
            b, n = O, R[:, {"XY": 2, "YZ": 0, "XZ": 1}[plane]]
        if p.get("Keep", "Positive") != "Positive":
            n = -n
        xmin, ymin, zmin, xmax, ymax, zmax = _bbox(dt)
        lo, hi = np.array([xmin, ymin, zmin]), np.array([xmax, ymax, zmax])
        c = (lo + hi) / 2
        dist = (c - b) @ n
        size = 2 * (np.linalg.norm(hi - lo) + abs(dist)) + 1e-6
        disk = o.addDisk(*(c - n * dist), size, size, zAxis=list(n))
        half = o.extrude([(2, disk)], *(n * size))
        box = [d for d in half if d[0] == 3][0][1]
        out, _ = o.intersect(dt, [(3, box)])
        if not out:
            raise GeometryError("Split removed the whole object (it lies completely on the other side of the plane)")
        return out
    if k in ("SweepAlongPath", "Connect", "Imprint"):
        tools = _tools(p, ctx)
        if not tools:
            raise GeometryError(f"{k} needs a second object")
        if k == "SweepAlongPath":
            # the profile (this object) is swept along the path object, the path is consumed
            if not any(d == 1 for d, _ in tools):
                raise GeometryError("The path must be a line or polyline")
            return _sweep_along(dt, tools, p.get("Trihedron", "DiscreteTrihedron"))
        if k == "Connect":
            # Connect: loft between two sheets (-> solid) or two lines (-> sheet)
            o.synchronize()
            wires = []
            for part in (dt, tools):
                dim = max(d for d, _ in part)
                if dim == 2:
                    bnd = gmsh.model.getBoundary([d for d in part if d[0] == 2], combined=True, oriented=True)
                    curves = [abs(t) for d, t in bnd]
                else:
                    curves = [t for d, t in part if d == 1]
                wires.append(o.addWire(curves))
            solid = max(d for d, _ in dt) == 2
            out = o.addThruSections(wires, makeSolid=solid, makeRuled=p.get("Ruled", "False") == "True")
            o.remove(dt + tools, recursive=True)
            top = max(d for d, _ in out)
            return [d for d in out if d[0] == top]
        # Imprint: the tool's intersection lines/faces are imprinted on the blank, the tool disappears
        out, mp = o.fragment(dt, tools)
        keep = [x for m in mp[:len(dt)] for x in m]
        drop = [x for m in mp[len(dt):] for x in m if x not in keep]
        if drop:
            o.remove(drop, recursive=True)
        top = max(d for d, _ in keep)
        # the blank keeps the pieces as one object: same material, faces split along the tool
        return [d for d in keep if d[0] == top]
    if k == "Chamfer":
        o.synchronize()
        d = ev.length(p["Distance"])
        if d <= 0:
            raise GeometryError("Chamfer distance must be positive")
        solid = any(dd == 3 for dd, _ in dt)
        vids = [int(e) for e in p.get("Vertices", "").split(",") if e.strip()]
        if vids:
            if solid:
                raise GeometryError("Vertices can be chamfered on sheets; select the edges of a solid")
            verts = object_vertices(dt)
            corners = {verts[i] for i in vids if 0 <= i < len(verts)}
            if not corners:
                raise GeometryError("No valid vertices selected for chamfer")
            return _fillet_sheet(dt, corners, d, "Chamfer")
        edges = object_edges(dt)
        faces = set(object_faces(dt))
        ids = [int(e) for e in p.get("Edges", "").split(",") if e.strip()]
        curves = [edges[i] for i in ids if 0 <= i < len(edges)]
        if not curves:
            raise GeometryError("No valid edges selected for chamfer")
        if not solid:
            return _fillet_sheet(dt, {abs(v) for c in curves
                                      for _, v in gmsh.model.getBoundary([(1, c)], oriented=False)}, d, "Chamfer")
        surfs = []
        for c in curves:
            adj = [s_ for s_ in gmsh.model.getAdjacencies(1, c)[0] if s_ in faces]
            if not adj:
                raise GeometryError("Edge has no face on this object")
            surfs.append(adj[0])
        try:
            out = o.chamfer([t for dd, t in dt if dd == 3], curves, surfs, [d])
        except Exception as e:  # noqa: BLE001
            raise GeometryError(f"Chamfer failed, the distance is probably too large for the selected edges ({e})")
        if not out:
            raise GeometryError("Chamfer failed, the distance is probably too large for the selected edges")
        return out
    if k in ("UncoverFaces", "KeepFaces", "SweepFacesAlongNormal"):
        o.synchronize()
        faces = object_faces(dt)
        ids = {int(e) for e in p.get("Faces", "").split(",") if e.strip()}
        sel = [faces[i] for i in sorted(ids) if 0 <= i < len(faces)]
        if not sel:
            raise GeometryError("No valid faces selected")
        if k == "SweepFacesAlongNormal":
            # extrude the faces along their normals and unite the result with the object
            dist = ev.length(p["Distance"])
            new = []
            for f in sel:
                cp = o.copy([(2, f)])
                o.synchronize()
                pc = gmsh.model.getParametrizationBounds(2, f)
                uv = [(pc[0][0] + pc[1][0]) / 2, (pc[0][1] + pc[1][1]) / 2]
                nrm = np.asarray(gmsh.model.getNormal(f, uv), float)
                ctr = np.asarray(gmsh.model.occ.getCenterOfMass(2, f))
                # outward: point the normal away from the body centre
                body = np.asarray(gmsh.model.occ.getCenterOfMass(*[d for d in dt if d[0] == 3][0]))
                if nrm @ (ctr - body) < 0:
                    nrm = -nrm
                ex = o.extrude(cp, *(nrm * dist))
                new += [d for d in ex if d[0] == 3]
            fused, _ = o.fuse([d for d in dt if d[0] == 3], new)
            return [d for d in fused if d[0] == 3]
        keep = sel if k == "KeepFaces" else [f for f in faces if f not in sel]
        cps = o.copy([(2, f) for f in keep])
        o.remove(dt, recursive=True)
        return cps
    if k == "KeepBody":
        idx = ev.integer(p.get("Body", "0") or "0")
        top = max(d for d, _ in dt)
        bodies = [d for d in dt if d[0] == top]
        if not 0 <= idx < len(bodies):
            raise GeometryError(f"Body {idx} does not exist ({len(bodies)} bodies)")
        o.remove([b for b in bodies if b != bodies[idx]], recursive=True)
        return [bodies[idx]]
    if k == "Section":
        # Section: cross section of the object with a plane -> sheet
        b = np.asarray(ev.point(p.get("Base", "0,0,0") or "0,0,0"), float)
        n = _plane_normal(p, ev)
        xmin, ymin, zmin, xmax, ymax, zmax = _bbox(dt)
        big = 4 * max(xmax - xmin, ymax - ymin, zmax - zmin, 1e-6)
        c = np.array([(xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2])
        c = c - n * ((c - b) @ n)
        disk = o.addDisk(*c, big, big, zAxis=list(n))
        out, _ = o.intersect([d for d in dt if d[0] == 3], [(2, disk)])
        res = [d for d in out if d[0] == 2]
        if not res:
            raise GeometryError("The plane does not cut the object")
        return res
    if k == "Fillet":
        o.synchronize()
        r = ev.length(p["Radius"])
        if r <= 0:
            raise GeometryError("Fillet radius must be positive")
        solid = any(d == 3 for d, _ in dt)
        vids = [int(e) for e in p.get("Vertices", "").split(",") if e.strip()]
        if vids:
            if solid:
                raise GeometryError("Vertices can be filleted on sheets; select the edges of a solid")
            verts = object_vertices(dt)
            corners = {verts[i] for i in vids if 0 <= i < len(verts)}
            if not corners:
                raise GeometryError("No valid vertices selected for fillet")
            return _fillet_sheet(dt, corners, r)
        edges = object_edges(dt)
        ids = [int(e) for e in p.get("Edges", "").split(",") if e.strip()]
        curves = [edges[i] for i in ids if 0 <= i < len(edges)]
        if not curves:
            raise GeometryError("No valid edges selected for fillet")
        if not solid:
            # edges of a sheet (projects of 0.2.3): the corners at both ends of each edge
            return _fillet_sheet(dt, {abs(v) for c in curves
                                      for _, v in gmsh.model.getBoundary([(1, c)], oriented=False)}, r)
        try:
            out = o.fillet([t for d, t in dt if d == 3], curves, [r])
        except Exception as e:  # noqa: BLE001
            raise GeometryError(f"Fillet failed, the radius is probably too large for the selected edges ({e})")
        if not out:
            raise GeometryError("Fillet failed, the radius is probably too large for the selected edges")
        return out
    raise GeometryError(f"Unknown operation {k}")


def _fillet_sheet(dt: list[tuple[int, int]], corners: set[int], r: float,
                  kind: str = "Fillet") -> list[tuple[int, int]]:
    """Fillet (or Chamfer) of a planar sheet: rounds (or bevels) the corners (vertex tags). Each face is
    extruded to a temporary solid, its edges through those corners are filleted (chamfered) and the face in
    the original plane is kept."""
    o = _occ()
    what = "radius" if kind == "Fillet" else "distance"
    faces = [t for d, t in dt if d == 2]
    if not faces:
        raise GeometryError(f"{kind} needs a solid or a sheet")
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(dt)
    diag = max(math.dist((xmin, ymin, zmin), (xmax, ymax, zmax)), 1e-12)
    tol = 1e-6 * diag
    h = 0.1 * diag
    out: list[tuple[int, int]] = []
    done = []
    for f in faces:
        verts = {abs(v) for _, v in gmsh.model.getBoundary([(2, f)], oriented=False, recursive=True)}
        sel = [v for v in verts if v in corners]
        if not sel:
            out.append((2, f))
            continue
        lo, hi = gmsh.model.getParametrizationBounds(2, f)
        uv = [(lo[0] + (hi[0] - lo[0]) * s, lo[1] + (hi[1] - lo[1]) * t)
              for s in (0.25, 0.5, 0.75) for t in (0.25, 0.5, 0.75)]
        nrms = np.asarray(gmsh.model.getNormal(f, [x for p in uv for x in p]), float).reshape(-1, 3)
        n = nrms[4] / np.linalg.norm(nrms[4])
        if np.any(np.abs(nrms @ n) < 1 - 1e-6):
            raise GeometryError(f"{kind} of a sheet works only on flat sheets")
        tops = {v: np.asarray(gmsh.model.getValue(0, v, [])) for v in sel}
        cp = o.copy([(2, f)])
        ex = o.extrude(cp, *(n * h))
        vols = [d for d in ex if d[0] == 3]
        o.synchronize()
        edges = []
        vcurves = {abs(t) for fc in gmsh.model.getBoundary(vols, oriented=False)
                   for _, t in gmsh.model.getBoundary([fc], oriented=False)}
        for c in sorted(vcurves):
            ends = [np.asarray(gmsh.model.getValue(0, abs(v), []))
                    for _, v in gmsh.model.getBoundary([(1, c)], oriented=False)]
            if len(ends) != 2:
                continue
            for p0 in tops.values():
                if any(np.linalg.norm(a - p0) < tol and np.linalg.norm(b - p0 - n * h) < tol
                       for a, b in (ends, ends[::-1])):
                    edges.append(c)
        if len(edges) != len(sel):
            o.remove(vols, recursive=True)
            raise GeometryError(f"{kind} of a sheet: the selected corners were not found")
        try:
            if kind == "Fillet":
                filleted = o.fillet([t for _, t in vols], edges, [r])
            else:
                # each edge runs along the extrusion, so both its faces are side faces: equal distances
                side = [gmsh.model.getAdjacencies(1, c)[0][0] for c in edges]
                filleted = o.chamfer([t for _, t in vols], edges, side, [r])
        except Exception as e:  # noqa: BLE001
            o.remove(vols, recursive=True)
            raise GeometryError(f"{kind} failed, the {what} is probably too large for the selected corners ({e})")
        o.synchronize()
        p0 = np.asarray(o.getCenterOfMass(2, f))
        keep = []
        for _, s_ in gmsh.model.getBoundary(filleted, oriented=False):
            s_ = abs(s_)
            c = np.asarray(o.getCenterOfMass(2, s_))
            lo, hi = gmsh.model.getParametrizationBounds(2, s_)
            ns = np.asarray(gmsh.model.getNormal(s_, [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2]), float)
            if abs((c - p0) @ n) < tol and abs(ns @ n) > 1 - 1e-6:
                keep.append(s_)
        if len(keep) != 1:
            o.remove(filleted, recursive=True)
            raise GeometryError(f"{kind} of a sheet failed")
        new = o.copy([(2, keep[0])])
        o.remove(filleted, recursive=True)
        done.append((2, f))
        out += new
    if done:
        o.remove(done, recursive=True)
    return out


def _tools(p: dict, ctx: BuildContext) -> list[tuple[int, int]]:
    tools: list[tuple[int, int]] = []
    for tname in [t.strip() for t in p.get("Tools", "").split(",") if t.strip()]:
        if not ctx.design.has_obj(tname):
            raise GeometryError(f"Object '{tname}' does not exist")
        tools += _build_object(ctx.design.obj(tname), ctx)
    return tools


def _plane_normal(p: dict, ev) -> np.ndarray:
    n = np.asarray(ev.vector(p.get("Normal", "0,0,1") or "0,0,1"), float)
    if np.linalg.norm(n) < 1e-300:
        raise GeometryError("The plane normal must not be zero")
    return n / np.linalg.norm(n)


def _bbox(dt) -> tuple[float, float, float, float, float, float]:
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for d, t in dt:
        b = _occ().getBoundingBox(d, t)
        lo = np.minimum(lo, b[:3])
        hi = np.maximum(hi, b[3:])
    return (*lo, *hi)  # type: ignore[return-value]


def _place(dt, O, R) -> None:
    """Moves dt from a coordinate system (origin O, axes R) to global. A rotation and a translation keep the
    exact surfaces (planes, cylinders ...); a general affine transform would turn them into B-splines, on
    which fillets and chamfers fail."""
    o = _occ()
    R = np.asarray(R, float)
    if abs(np.linalg.det(R) - 1) > 1e-9:
        from ..model import coordsys
        o.affineTransform(dt, list(coordsys.matrix(O, R)[:3, :].ravel()))
        return
    ang = math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))
    if ang > 1e-12:
        if math.pi - ang > 1e-6:
            axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
        else:                                   # half turn: the axis from the symmetric part
            B = (R + np.eye(3)) / 2
            axis = B[:, int(np.argmax(np.diag(B)))]
        o.rotate(dt, 0, 0, 0, *(axis / np.linalg.norm(axis)), ang)
    if np.linalg.norm(O) > 0:
        o.translate(dt, *map(float, O))


def _build_object(obj: GeoObject, ctx: BuildContext) -> list[tuple[int, int]]:
    if obj.name in ctx.stack:
        raise GeometryError(f"Circular boolean reference through '{obj.name}'")
    ctx.stack.append(obj.name)
    try:
        dt = _create(obj.history[0], ctx)
        if obj.orientation and obj.orientation != "Global" and obj.history[0].kind not in ("Import", "CreateRegion"):
            # Orientation: the creation command is given in this coordinate system
            from ..model import coordsys
            O, R = coordsys.frame(ctx.design, obj.orientation, ctx.ev)
            _place(dt, O, R)
        for cmd in obj.history[1:]:
            if cmd.suppressed:
                continue
            dt = _apply(cmd, dt, ctx, obj.name)
        return dt
    finally:
        ctx.stack.pop()


def region_box(design: Design, ctx: BuildContext, cmd: Command, bbox) -> list[tuple[int, int]]:
    p = cmd.props
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    ext = np.array([xmax - xmin, ymax - ymin, zmax - zmin])
    big = max(ext.max(), 1e-9)
    pads = {}
    for key, i in (("X", 0), ("Y", 1), ("Z", 2)):
        for s in "+-":
            raw = p.get(s + key, "0")
            if p.get("PaddingType") == "Absolute Offset":
                pads[s + key] = ctx.ev.length(raw)
            else:
                pads[s + key] = ctx.ev.value(raw) / 100.0 * (ext[i] if ext[i] > 1e-12 else big)
    x0, y0, z0 = xmin - pads["-X"], ymin - pads["-Y"], zmin - pads["-Z"]
    dx = xmax + pads["+X"] - x0
    dy = ymax + pads["+Y"] - y0
    dz = zmax + pads["+Z"] - z0
    return [(3, _occ().addBox(x0, y0, z0, dx, dy, dz))]


def model_bbox(built: dict[str, list[tuple[int, int]]], design: Design):
    dts = []
    for o in design.live_objects():
        if o.model and o.name in built and o.history[0].kind != "CreateRegion":
            dts += built[o.name]
    if not dts:
        return (-0.5e-3, -0.5e-3, -0.5e-3, 0.5e-3, 0.5e-3, 0.5e-3)
    return _bbox(dts)


def build_design(design: Design, ev: Evaluator, names: list[str] | None = None,
                 extra_bbox=None, on_progress: Callable[[str], None] | None = None) -> BuildContext:
    """Build live objects into the current gmsh model. Errors are collected per object."""
    ctx = BuildContext(design, ev)
    live = [o for o in design.live_objects() if names is None or o.name in names]
    regions = [o for o in live if o.history[0].kind == "CreateRegion"]
    for obj in live:
        if obj in regions:
            continue
        if on_progress:
            on_progress(obj.name)
        try:
            ctx.built[obj.name] = _build_object(obj, ctx)
        except (GeometryError, ExprError, Exception) as e:  # noqa: BLE001
            ctx.errors[obj.name] = str(e)
    for obj in regions:
        try:
            bb = model_bbox(ctx.built, design) if extra_bbox is None else extra_bbox
            dt = region_box(design, ctx, obj.history[0], bb)
            for cmd in obj.history[1:]:
                if not cmd.suppressed:
                    dt = _apply(cmd, dt, ctx, obj.name)
            ctx.built[obj.name] = dt
        except Exception as e:  # noqa: BLE001
            ctx.errors[obj.name] = str(e)
    # drop import bodies nobody referenced
    unused = [dt for lst in ctx.imports.values() for dt in lst if dt not in ctx.used_imports]
    if unused:
        _occ().remove(unused, recursive=True)
    _occ().synchronize()
    return ctx


def solid_overlaps(design: Design, ev: Evaluator, names: list[str]) -> list[tuple[str, str, float, float, float]]:
    """Pairs of the named solids that share volume: (a, b, overlap volume, volume of a, volume of b), in m^3.
    Built in a scratch model (the current model is restored); pairs whose bounding boxes do not overlap are
    skipped without a boolean operation."""
    if not gmsh.isInitialized():
        ensure_gmsh()
    cur = gmsh.model.getCurrent()
    gmsh.model.add("hertzon_overlap")
    try:
        ctx = build_design(design, ev, names=names)
        occ = _occ()
        solids = {n: [d for d in ctx.built.get(n, []) if d[0] == 3] for n in names}
        solids = {n: dt for n, dt in solids.items() if dt}
        vol = {n: sum(occ.getMass(3, t) for _, t in dt) for n, dt in solids.items()}
        box = {n: _bbox(dt) for n, dt in solids.items()}
        out = []
        ns = list(solids)
        for i, a in enumerate(ns):
            for b in ns[i + 1:]:
                if not all(box[a][k] < box[b][k + 3] and box[b][k] < box[a][k + 3] for k in range(3)):
                    continue
                try:
                    r, _ = occ.intersect(solids[a], solids[b], removeObject=False, removeTool=False)
                except Exception:  # noqa: BLE001
                    continue
                v = sum(occ.getMass(3, t) for d, t in r if d == 3)
                if r:
                    occ.remove(r, recursive=True)
                if v > 1e-6 * min(vol[a], vol[b]):
                    out.append((a, b, v, vol[a], vol[b]))
        return out
    finally:
        gmsh.model.remove()
        try:
            gmsh.model.setCurrent(cur)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------------------------------------
# Topology enumeration (stable order = gmsh boundary order)
# ------------------------------------------------------------------------------------------------
def object_faces(dt: list[tuple[int, int]]) -> list[int]:
    dim = max(d for d, _ in dt) if dt else 0
    if dim == 3:
        faces = gmsh.model.getBoundary([d for d in dt if d[0] == 3], combined=True, oriented=False, recursive=False)
        out, seen = [], set()
        for d, t in faces:
            if t not in seen:
                seen.add(t)
                out.append(abs(t))
        return out
    if dim == 2:
        return [t for d, t in dt if d == 2]
    return []


def object_edges(dt: list[tuple[int, int]]) -> list[int]:
    dim = max(d for d, _ in dt) if dt else 0
    if dim == 1:
        return [t for d, t in dt if d == 1]
    faces = object_faces(dt)
    out, seen = [], set()
    for f in faces:
        for d, t in gmsh.model.getBoundary([(2, f)], combined=False, oriented=False):
            t = abs(t)
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def object_vertices(dt: list[tuple[int, int]]) -> list[int]:
    out, seen = [], set()
    for e in object_edges(dt):
        for d, t in gmsh.model.getBoundary([(1, e)], combined=False, oriented=False):
            t = abs(t)
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


# ------------------------------------------------------------------------------------------------
# Tessellation for display / picking / face matching
# ------------------------------------------------------------------------------------------------
@dataclass
class Tess:
    points: np.ndarray            # (N,3) float32
    tris: np.ndarray              # (M,3) int32
    tri_face: np.ndarray          # (M,) face index within the object
    edges: list[np.ndarray]       # polyline per edge index
    vertices: np.ndarray          # (K,3)
    dim: int
    bbox: tuple
    face_area: list[float] = field(default_factory=list)
    face_normal: list[tuple] = field(default_factory=list)
    face_center: list[tuple] = field(default_factory=list)
    volume: float = 0.0


def configure_display_mesh(diag: float, quality: str = "normal") -> None:
    # elements per full turn on curved faces: enough for a round outline (spheres, cylinders)
    n = {"coarse": 18, "normal": 36, "fine": 64}[quality]
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", n)
    gmsh.option.setNumber("Mesh.MeshSizeMax", max(diag / 4, 1e-9))
    gmsh.option.setNumber("Mesh.MeshSizeMin", 0)
    set_apex_floor(diag / 200)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    gmsh.option.setNumber("Mesh.Optimize", 0)


def _cone_apexes() -> np.ndarray:
    """Tips of cone surfaces (zero radius). Curvature there is infinite, so curvature-based
    sizing would refine without end and the mesher hangs."""
    pts = []
    for _, s in gmsh.model.getEntities(2):
        if gmsh.model.getType(2, s) != "Cone":
            continue
        verts = {abs(t) for _, t in gmsh.model.getBoundary([(2, s)], combined=False, oriented=False, recursive=True)}
        on_curve = set()
        for _, c in gmsh.model.getBoundary([(2, s)], combined=False, oriented=False):
            if gmsh.model.getType(1, abs(c)) != "Line":
                on_curve |= {abs(t) for _, t in gmsh.model.getBoundary([(1, abs(c))], oriented=False)}
        pts += [gmsh.model.getValue(0, v, []) for v in verts - on_curve]
    return np.asarray(pts, float).reshape(-1, 3)


def set_apex_floor(floor: float) -> None:
    """Minimum element size near cone tips (only there; a global MeshSizeMin upsets OCC spheres)."""
    global _APEX_CB
    apex = _cone_apexes()
    gmsh.model.mesh.removeSizeCallback()
    _APEX_CB = None
    if not len(apex):
        return

    pts = [tuple(map(float, a)) for a in apex]
    r2 = (4 * floor) ** 2

    def cb(dim, tag, x, y, z, lc):  # plain Python: called for every size query
        if lc >= floor:
            return lc
        for ax, ay, az in pts:
            if (x - ax) ** 2 + (y - ay) ** 2 + (z - az) ** 2 < r2:
                return floor
        return lc
    _APEX_CB = cb
    gmsh.model.mesh.setSizeCallback(cb)


_APEX_CB = None


_ALL_NODES: list = []     # [lut, coords] of the whole display mesh, filled lazily per tessellation


def _all_nodes():
    if not _ALL_NODES:
        ntags, coords, _ = gmsh.model.mesh.getNodes()
        X = coords.reshape(-1, 3)
        lut = np.full(int(ntags.max()) + 1 if len(ntags) else 1, -1, np.int64)
        lut[ntags.astype(np.int64)] = np.arange(len(ntags))
        _ALL_NODES[:] = [lut, X]
    return _ALL_NODES


def _entity_tris(dim: int, tag: int):
    if (dim, tag) in _PASS_CACHE:
        return _PASS_CACHE[(dim, tag)]
    types, _, enodes = gmsh.model.mesh.getElements(dim, tag)
    if len(types) and _ALL_NODES:
        # fast path: one bulk node table instead of a getNodes call per face (STL imports: 10k faces)
        glut, GX = _ALL_NODES
        used = np.unique(np.concatenate([e.astype(np.int64) for e in enodes]))
        X = GX[glut[used]]
        lut = np.full(int(used.max()) + 1, -1, np.int64)
        lut[used] = np.arange(len(used))
        conn = []
        for t, en in zip(types, enodes):
            if t == 2:
                conn.append(lut[en.astype(np.int64)].reshape(-1, 3))
            elif t == 3:
                q = lut[en.astype(np.int64)].reshape(-1, 4)
                conn.append(np.vstack([q[:, [0, 1, 2]], q[:, [0, 2, 3]]]))
        return X, (np.vstack(conn) if conn else np.zeros((0, 3), np.int64))
    ntags, coords, _ = gmsh.model.mesh.getNodes(dim, tag, includeBoundary=True)
    X = coords.reshape(-1, 3)
    if len(ntags) == 0:
        return X, np.zeros((0, 3), np.int64)
    lut = np.full(int(ntags.max()) + 1, -1, np.int64)
    lut[ntags] = np.arange(len(ntags))
    conn = []
    for t, en in zip(types, enodes):
        if t == 2:     # 3-node triangle
            conn.append(lut[en.astype(np.int64)].reshape(-1, 3))
        elif t == 3:   # quad -> two triangles
            q = lut[en.astype(np.int64)].reshape(-1, 4)
            conn.append(np.vstack([q[:, [0, 1, 2]], q[:, [0, 2, 3]]]))
    tris = np.vstack(conn) if conn else np.zeros((0, 3), np.int64)
    return X, tris


def _curve_polyline(tag: int) -> np.ndarray:
    if (1, tag) in _PASS_CACHE:
        return _PASS_CACHE[(1, tag)]
    if _ALL_NODES:
        types, _, enodes = gmsh.model.mesh.getElements(1, tag)
        if len(types) == 1 and types[0] == 1 and len(enodes[0]):
            # 2-node line elements come ordered along the curve
            e = enodes[0].astype(np.int64).reshape(-1, 2)
            if np.all(e[1:, 0] == e[:-1, 1]):
                glut, GX = _ALL_NODES
                return GX[glut[np.r_[e[:, 0], e[-1, 1]]]]
    _, coords, params = gmsh.model.mesh.getNodes(1, tag, includeBoundary=False, returnParametricCoord=True)
    X = coords.reshape(-1, 3)
    lo, hi = gmsh.model.getParametrizationBounds(1, tag)
    a = np.asarray(gmsh.model.getValue(1, tag, [lo[0]])).reshape(1, 3)
    b = np.asarray(gmsh.model.getValue(1, tag, [hi[0]])).reshape(1, 3)
    if len(X):
        X = X[np.argsort(params[: len(X)])]
    return np.vstack([a, X, b])


def tessellate(ctx: BuildContext, quality: str = "normal") -> dict[str, Tess]:
    all_dt = [dt for lst in ctx.built.values() for dt in lst]
    if not all_dt:
        return {}
    bb = _bbox(all_dt)
    diag = float(np.linalg.norm(np.array(bb[3:]) - np.array(bb[:3])))
    configure_display_mesh(diag, quality)
    if PROGRESS is not None:
        PROGRESS(1.0, "Building the display mesh ...")
    gmsh.model.mesh.clear()
    _PASS_CACHE.clear()
    try:
        _generate_planar_first()
    except Exception:  # noqa: BLE001
        if not mesh_empty_faces(diag / 40):
            _tessellate_fallbacks(ctx, diag)
    out: dict[str, Tess] = {}
    _ALL_NODES.clear()
    try:
        _all_nodes()
    except Exception:  # noqa: BLE001
        _ALL_NODES.clear()
    for name, dt in ctx.built.items():
        if name in ctx.errors:
            continue
        try:
            out[name] = _tess_object(dt)
        except Exception as e:  # noqa: BLE001
            ctx.errors[name] = f"Display tessellation failed: {e}"
    _ALL_NODES.clear()
    return out


def empty_faces() -> list[int]:
    """Surfaces of the current model without triangles (their meshing failed)."""
    out = []
    for _, s in gmsh.model.getEntities(2):
        if (2, s) in _PASS_CACHE:
            continue
        _, _, enodes = gmsh.model.mesh.getElements(2, s)
        if not any(len(e) for e in enodes):
            out.append(s)
    return out


def mesh_empty_faces(hmax: float) -> bool:
    """Second try for the surfaces a failed 2D mesh left empty. Trimmed B-spline faces from CAD
    exports (degenerate poles, edges of a few micrometres) often break the frontal mesher only
    with curvature based sizing; without it, and with a size limit instead, they mesh. The display
    mesh of everything meshed so far is kept (as in _generate_planar_first), then only the empty
    faces are meshed again. True when no surface is left empty."""
    bad = empty_faces()
    if not bad:
        return False          # the failure was elsewhere (curves, volumes)
    _ALL_NODES.clear()
    for _, s in gmsh.model.getEntities(2):
        if s not in bad and (2, s) not in _PASS_CACHE:
            _PASS_CACHE[(2, s)] = _entity_tris(2, s)
    for _, c in gmsh.model.getEntities(1):
        if (1, c) not in _PASS_CACHE:
            try:
                _PASS_CACHE[(1, c)] = _curve_polyline(c)
            except Exception:  # noqa: BLE001
                pass
    keep = {k: gmsh.option.getNumber(k) for k in ("Mesh.MeshSizeFromCurvature", "Mesh.MeshSizeMax",
                                                    "Mesh.MeshOnlyVisible")}
    everything = gmsh.model.getEntities()
    try:
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.option.setNumber("Mesh.MeshSizeMax", min(keep["Mesh.MeshSizeMax"], max(hmax, 1e-9)))
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 1)
        gmsh.model.setVisibility(everything, 0)
        cl = _closure([(2, s) for s in bad])
        gmsh.model.setVisibility(cl, 1)
        gmsh.model.mesh.clear()
        try:
            gmsh.model.mesh.generate(2)
        except Exception:  # noqa: BLE001
            pass
        for e in cl:      # curves of the re-meshed faces: take the new discretisation
            if e[0] == 1:
                _PASS_CACHE.pop(e, None)
    finally:
        for k, v in keep.items():
            gmsh.option.setNumber(k, v)
        gmsh.model.setVisibility(everything, 1)
    return not empty_faces()


def _tessellate_fallbacks(ctx: BuildContext, diag: float) -> None:
    _PASS_CACHE.clear()
    try:
        # free-form faces (lofts) often need a finer display mesh
        gmsh.option.setNumber("Mesh.MeshSizeMax", max(diag / 20, 1e-9))
        gmsh.model.mesh.clear()
        gmsh.model.mesh.generate(2)
    except Exception:  # noqa: BLE001
        if not mesh_empty_faces(diag / 40):
            _PASS_CACHE.clear()
            _mesh_skipping_bad(ctx)


# display mesh of entities meshed in an earlier pass (see _generate_planar_first)
_PASS_CACHE: dict[tuple[int, int], object] = {}


def _closure(ents: list) -> list:
    out = set(ents)
    out |= {(d, abs(t)) for d, t in gmsh.model.getBoundary(ents, combined=False, oriented=False, recursive=True)}
    out |= {(d, abs(t)) for d, t in gmsh.model.getBoundary(ents, combined=False, oriented=False)}
    return list(out)


def _resolve_planar_curves(ents, n_turn: float, skip=()) -> None:
    """Without curvature sizing (the plane pass) a circle in a flat face (ring, circular sheet, polygon
    with a round hole) got only the global size: a dozen segments, and inner and outer circle of a thin
    ring not matching. Give every curved edge its segments by how far its tangent turns, twice as many
    as curvature sizing per turn: in a flat face they cost nothing, and the inner and outer circle of a
    thin ring (rat race, coupled lines) then show no zigzag. Edges of curved faces (skip) keep the
    curvature sizing of the curved pass."""
    if n_turn <= 0:
        return
    skip = set(skip)
    for d, t in ents:
        if d != 1 or (d, t) in skip or gmsh.model.getType(1, t) == "Line":
            continue
        try:
            lo, hi = gmsh.model.getParametrizationBounds(1, t)
            u = np.linspace(lo[0], hi[0], 65)
            T = np.asarray(gmsh.model.getDerivative(1, t, list(u))).reshape(-1, 3)
            T = T / np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-300)
            turn = float(np.sum(np.arccos(np.clip(np.sum(T[1:] * T[:-1], axis=1), -1.0, 1.0))))
        except Exception:  # noqa: BLE001
            continue
        n = int(np.ceil(2 * n_turn * turn / (2 * np.pi)))
        if n > 1:
            gmsh.model.mesh.setTransfiniteCurve(t, n + 1)


def _generate_planar_first() -> None:
    """Display mesh in passes. Swept pipes (helix wire, BSpline surfaces) get a coarser curvature
    sizing: it is isotropic, so the thin tube radius would also set the size along the whole length
    -> tens of thousands of display triangles. That limit holds only for those faces; cylinders and
    spheres, also ones fused with the pipe, keep the full sizing (a single helix in the model used
    to make every cylinder coarse)."""
    _PASS_CACHE.clear()
    S = gmsh.model.getEntities(2)
    curv = gmsh.option.getNumber("Mesh.MeshSizeFromCurvature")
    swept = [e for e in S if gmsh.model.getType(*e) == "BSpline surface"]
    rest = [e for e in S if e not in swept]
    # lines and points outside every face (wires, helix lines) go with the last pass
    in_faces = set(_closure(S)) if S else set()
    loose = [e for e in gmsh.model.getEntities(1) + gmsh.model.getEntities(0) if e not in in_faces]
    try:
        if swept and rest:
            _mesh_faces(swept, min(curv, 8), last=False)
            # edges shared with the pipe keep its node count, so both sides of the seam match
            seam = [e for e in set(_closure(swept)) & set(_closure(rest))
                    if e[0] == 1 and e in _PASS_CACHE and gmsh.model.getType(*e) != "Line"]
            for _, t in seam:
                gmsh.model.mesh.setTransfiniteCurve(t, len(_PASS_CACHE[(1, t)]))
            _mesh_faces(rest, curv, last=True, loose=loose, fixed=seam)
        else:
            _mesh_faces(S, min(curv, 8) if swept else curv, last=True, loose=loose, alone=True)
    finally:
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", curv)
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 0)
        gmsh.model.setVisibility(gmsh.model.getEntities(), 1)
        if _APEX_CB is not None:
            gmsh.model.mesh.setSizeCallback(_APEX_CB)


def _mesh_faces(faces: list, curv: float, last: bool, loose: list = (), fixed: list = (),
                alone: bool = False) -> None:
    """Display mesh of some faces. Curvature sizing costs time for every face, also for flat ones
    (a 50-segment sphere has 1250 of them), so with many plane faces these are meshed first without
    it. The triangles of every pass but the last one are kept in _PASS_CACHE (gmsh holds one mesh)."""
    planes = [e for e in faces if gmsh.model.getType(*e) == "Plane"]
    curved = [e for e in faces if e not in planes]
    if len(planes) >= 50 and curved:
        passes = [(planes, 0.0), (curved, curv)]
    else:
        passes = [(faces, curv if curved else 0.0)]
    skip = (_closure(curved) if curved else []) + list(fixed)
    for i, (grp, c) in enumerate(passes):
        final = last and i == len(passes) - 1
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", c)
        cl = (_closure(grp) if grp else []) + (list(loose) if final else [])
        if c == 0:
            gmsh.model.mesh.removeSizeCallback()    # apex floor only matters with curvature sizing
            _resolve_planar_curves(cl, curv, skip=skip)
        elif _APEX_CB is not None:
            gmsh.model.mesh.setSizeCallback(_APEX_CB)
        if alone and len(passes) == 1:
            gmsh.model.mesh.generate(2)       # the whole model in one go
            return
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 1)
        gmsh.model.setVisibility(gmsh.model.getEntities(), 0)
        gmsh.model.setVisibility(cl, 1)
        gmsh.model.mesh.clear()
        gmsh.model.mesh.generate(2)
        for d, t in cl:
            if final:
                _PASS_CACHE.pop((d, t), None)    # shared edges: take the (finer) last-pass discretisation
            elif d == 2:
                _PASS_CACHE[(2, t)] = _entity_tris(2, t)
            elif d == 1:
                _PASS_CACHE[(1, t)] = _curve_polyline(t)


def _mesh_skipping_bad(ctx: BuildContext) -> None:
    """One object breaks the display mesh: find it (mesh each object alone) and mesh the others."""
    def closure(dt):
        ents = set(dt)
        for d in gmsh.model.getBoundary(dt, combined=False, oriented=False, recursive=True):
            ents.add((d[0], abs(d[1])))
        for dim in (1, 2):
            for d in gmsh.model.getBoundary([x for x in dt if x[0] > dim], combined=False, oriented=False):
                ents.add((d[0], abs(d[1])))
        return list(ents)
    gmsh.option.setNumber("Mesh.MeshOnlyVisible", 1)
    try:
        everything = gmsh.model.getEntities()
        bad = []
        for name, dt in ctx.built.items():
            gmsh.model.setVisibility(everything, 0)
            gmsh.model.setVisibility(closure(dt), 1)
            gmsh.model.mesh.clear()
            try:
                gmsh.model.mesh.generate(2)
            except Exception as e:  # noqa: BLE001
                bad.append(name)
                ctx.errors[name] = f"Display tessellation failed: {e}"
        gmsh.model.setVisibility(everything, 0)
        good = [x for n, dt in ctx.built.items() if n not in bad for x in closure(dt)]
        gmsh.model.setVisibility(good, 1)
        gmsh.model.mesh.clear()
        gmsh.model.mesh.generate(2)
    finally:
        gmsh.option.setNumber("Mesh.MeshOnlyVisible", 0)
        gmsh.model.setVisibility(gmsh.model.getEntities(), 1)


def _tess_object(dt: list[tuple[int, int]]) -> Tess:
    dim = max(d for d, _ in dt)
    faces = object_faces(dt)
    pts, tris, tf = [], [], []
    off = 0
    areas, normals, centers = [], [], []
    for i, f in enumerate(faces):
        X, T = _entity_tris(2, f)
        pts.append(X)
        tris.append(T + off)
        tf.append(np.full(len(T), i, np.int32))
        off += len(X)
        try:
            areas.append(gmsh.model.occ.getMass(2, f))
            centers.append(tuple(gmsh.model.occ.getCenterOfMass(2, f)))
        except Exception:  # noqa: BLE001
            areas.append(0.0)
            centers.append((0.0, 0.0, 0.0))
        if len(T):
            a, b, c = X[T[:, 0]], X[T[:, 1]], X[T[:, 2]]
            n = np.cross(b - a, c - a).sum(axis=0)
            nn = np.linalg.norm(n)
            normals.append(tuple(n / nn) if nn > 0 else (0.0, 0.0, 0.0))
        else:
            normals.append((0.0, 0.0, 0.0))
    edges = [_curve_polyline(e) for e in object_edges(dt)]
    verts = []
    for v in object_vertices(dt):
        verts.append(gmsh.model.getValue(0, v, []))
    if dim == 1:
        verts = []
        for e in edges:
            if len(e):
                verts += [e[0], e[-1]]
    vol = 0.0
    if dim == 3:
        vol = sum(gmsh.model.occ.getMass(3, t) for d, t in dt if d == 3)
    P = np.vstack(pts).astype(np.float64) if pts else np.zeros((0, 3))
    allp = np.vstack([P] + [e for e in edges if len(e)]) if (len(P) or edges) else np.zeros((1, 3))
    bbox = (*allp.min(axis=0), *allp.max(axis=0)) if len(allp) else (0,) * 6
    return Tess(points=P, tris=np.vstack(tris).astype(np.int64) if tris else np.zeros((0, 3), np.int64),
                tri_face=np.concatenate(tf) if tf else np.zeros(0, np.int32), edges=edges,
                vertices=np.asarray(verts, float).reshape(-1, 3), dim=dim, bbox=tuple(bbox),
                face_area=areas, face_normal=normals, face_center=centers, volume=vol)


def object_key(design: Design, obj: GeoObject, ev: Evaluator) -> str:
    """Cache key covering everything an object's shape depends on."""
    def hist(o: GeoObject, seen: set) -> list:
        res = [o.name, o.orientation, [(c.kind, c.props, c.suppressed) for c in o.history]]
        for c in o.history:
            if c.kind in ("Unite", "Subtract", "Intersect"):
                for t in c.props.get("Tools", "").split(","):
                    t = t.strip()
                    if t and design.has_obj(t) and t not in seen:
                        seen.add(t)
                        res.append(hist(design.obj(t), seen))
        return res
    payload = [hist(obj, {obj.name}), design.units, sorted(ev.variables.items()),
               [(c.name, c.reference, c.props) for c in design.coord_systems]]
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ---- display tessellation cache for imported (STL) objects ---------------------------------------
def _tess_cache_file(design: Design, obj: GeoObject, ev: Evaluator, quality: str) -> Path | None:
    """Imported faceted objects take long to tessellate (thousands of faces); their display mesh is
    kept on disk, keyed by the object definition and the imported file's size and date."""
    imports = [c for c in obj.history if c.kind == "Import"]
    if not imports:
        return None
    stamp = []
    for c in imports:
        try:
            st = Path(c.props.get("File", "")).stat()
            stamp.append((st.st_size, st.st_mtime_ns))
        except OSError:
            return None
    key = hashlib.sha1((object_key(design, obj, ev) + quality + str(stamp) + "v2").encode()).hexdigest()[:24]
    return Path.home() / ".hertzon" / "tess_cache" / f"{key}.npz"


def save_tess(path: Path, t: Tess) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lens = np.array([len(e) for e in t.edges], np.int64)
        E = np.vstack([e for e in t.edges if len(e)]) if len(lens) and lens.sum() else np.zeros((0, 3))
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, points=t.points, tris=t.tris, tri_face=t.tri_face, edges=E, edge_len=lens,
                 vertices=t.vertices, dim=np.array([t.dim]), bbox=np.array(t.bbox, float),
                 face_area=np.array(t.face_area, float), face_normal=np.array(t.face_normal, float).reshape(-1, 3),
                 face_center=np.array(t.face_center, float).reshape(-1, 3), volume=np.array([t.volume]))
        tmp.replace(path)
    except OSError:
        pass


def load_tess(path: Path) -> Tess | None:
    try:
        with np.load(path) as d:
            off = np.r_[0, np.cumsum(d["edge_len"])]
            E = d["edges"]
            return Tess(points=d["points"], tris=d["tris"], tri_face=d["tri_face"],
                        edges=[E[a:b] for a, b in zip(off[:-1], off[1:])], vertices=d["vertices"],
                        dim=int(d["dim"][0]), bbox=tuple(float(x) for x in d["bbox"]),
                        face_area=[float(x) for x in d["face_area"]],
                        face_normal=[tuple(x) for x in d["face_normal"]],
                        face_center=[tuple(x) for x in d["face_center"]], volume=float(d["volume"][0]))
    except (OSError, KeyError, ValueError):
        return None
