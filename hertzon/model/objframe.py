"""Points of a boundary or port that belong to its object (integration line, U vector).

They are given in the coordinates of the object as it was created: the object's orientation (coordinate
system) and its Move, Rotate, Mirror and Scale operations are applied to them, so the line follows the
object wherever it is moved. ``object_matrix`` is the affine map from those coordinates to global ones,
built the same way as the geometry kernel builds the object."""
from __future__ import annotations

import math

import numpy as np

from . import coordsys

# assignment properties holding points that follow their object
POINT_PROPS = ("IntegrationLine", "UVector")
TRANSFORMS = ("Move", "Rotate", "Mirror", "Scale")
_AXIS = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def _translate(v) -> np.ndarray:
    M = np.eye(4)
    M[:3, 3] = v
    return M


def _about(c, L: np.ndarray) -> np.ndarray:
    """Linear map L acting about the point c."""
    M = np.eye(4)
    M[:3, :3] = L
    c = np.asarray(c, float)
    M[:3, 3] = c - L @ c
    return M


def _rotation(axis, angle: float) -> np.ndarray:
    k = np.asarray(axis, float)
    k = k / np.linalg.norm(k)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)


def rotation_axis(design, p: dict, ev) -> tuple[np.ndarray, np.ndarray]:
    """Global centre (m) and axis direction of a Rotate / Sweep Around Axis command: Axis and Center are
    given in the command's coordinate system (Global when not set)."""
    O, R = coordsys.frame(design, p.get("CoordinateSystem", coordsys.GLOBAL) or coordsys.GLOBAL, ev)
    c = O + R @ np.asarray(ev.point(p.get("Center", "0,0,0") or "0,0,0"), float)
    return c, R @ np.asarray(_AXIS[p.get("Axis", "Z")], float)


def command_matrix(cmd, ev, design=None) -> np.ndarray | None:
    """Affine map of one Move / Rotate / Mirror / Scale command (None for other commands)."""
    p = cmd.props
    if cmd.kind == "Move":
        return _translate(ev.vector(p["Vector"]))
    if cmd.kind == "Rotate":
        c, ax = rotation_axis(design, p, ev)
        return _about(c, _rotation(ax, ev.angle(p["Angle"])))
    if cmd.kind == "Mirror":
        n = np.asarray(ev.vector(p["Normal"]), float)
        n = n / (np.linalg.norm(n) + 1e-300)
        return _about(ev.point(p["Base"]), np.eye(3) - 2 * np.outer(n, n))
    if cmd.kind == "Scale":
        return _about(ev.point(p.get("Center", "0,0,0") or "0,0,0"), ev.value(p["Factor"]) * np.eye(3))
    return None


def object_matrix(design, obj, ev) -> np.ndarray:
    """Affine 4x4 map from the coordinates of the object as created to its place in the model."""
    M = np.eye(4)
    if obj.orientation and obj.orientation != coordsys.GLOBAL and obj.history[0].kind not in ("Import", "CreateRegion"):
        O, R = coordsys.frame(design, obj.orientation, ev)
        M = coordsys.matrix(O, R)
    for cmd in obj.history[1:]:
        if cmd.suppressed or cmd.kind not in TRANSFORMS:
            continue
        M = command_matrix(cmd, ev, design) @ M
    return M


def has_transform(obj) -> bool:
    """True when the object is moved away from where it was created (cheap, no evaluation)."""
    if obj.orientation and obj.orientation != coordsys.GLOBAL and obj.history[0].kind not in ("Import", "CreateRegion"):
        return True
    return any(c.kind in TRANSFORMS and not c.suppressed for c in obj.history[1:])


def owner(design, a):
    """The object the points of an assignment belong to: its first face or object."""
    names = [f.obj for f in a.faces] + list(a.objects) + [e.obj for e in a.edges]
    for n in names:
        if design.has_obj(n):
            return design.obj(n)
    return None


def assignment_matrix(design, a, ev) -> np.ndarray:
    o = owner(design, a)
    return np.eye(4) if o is None or not has_transform(o) else object_matrix(design, o, ev)


def _apply(M: np.ndarray, pts) -> list[np.ndarray]:
    return [M[:3, :3] @ np.asarray(p, float) + M[:3, 3] for p in pts]


def global_points(design, a, key: str, ev) -> list[np.ndarray]:
    """Points of property ``key`` of assignment ``a`` in global coordinates (metres)."""
    text = a.props.get(key, "").strip()
    if not text:
        return []
    return _apply(assignment_matrix(design, a, ev), ev.points(text))


def _fmt(x: float) -> str:
    s = f"{x:.10g}"
    return "0" if s == "-0" else s


def to_object_text(design, a, pts, ev, fmt=None) -> str:
    """Property text of global points (metres) in the coordinates of the assignment's object, in model units.

    ``fmt`` formats one coordinate already in model units (default: up to 10 significant digits)."""
    fmt = fmt or _fmt
    M = np.linalg.inv(assignment_matrix(design, a, ev))
    s = ev.unit_scale
    return ";".join(",".join(fmt(x / s) for x in p) for p in _apply(M, pts))


def to_object_coords(design, a, text: str, ev) -> str:
    """Global points given as text turned into the coordinates of the assignment's object. The text stays as
    it is (expressions included) while the object is not moved."""
    o = owner(design, a)
    if not text.strip() or o is None or not has_transform(o):
        return text
    return to_object_text(design, a, ev.points(text), ev)


def migrate_global_points(design, ev) -> list[str]:
    """Older files stored the points in global coordinates: turn them into the coordinates of their object.
    Returns messages for points that could not be converted."""
    msgs = []
    for a in design.boundaries + design.excitations:
        for key in POINT_PROPS:
            text = a.props.get(key, "")
            if not text.strip():
                continue
            try:
                a.props[key] = to_object_coords(design, a, text, ev)
            except Exception as e:  # noqa: BLE001
                msgs.append(f"{a.name}: {key} kept as it is ({e})")
    return msgs
