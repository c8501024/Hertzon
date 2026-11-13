"""3D design blocks of a schematic in its 3D layout.

A 3D design block (circuit part "EM") stands for the S-parameters of a 3D design of the project. When the
schematic is laid out (circuit_layout.layout_design), the geometry of that design is copied into the layout:
turned in steps of 90 degrees and moved so that the port a line connects to sits exactly where the line ends.

- the ports of the block are found in the built geometry: a port is a vertical sheet or face (Lumped Port or
  Wave Port) on the edge of the design; its centre is where the line attaches, its normal pointing out of the
  design is the direction the line leaves in, its lower edge is the ground plane (z of the layout's ground)
- a port that a line or part connects to is removed with its port sheet; a port that only a Power Port of the
  schematic connects to stays a port of the layout (numbered like that Power Port); an open or grounded pin
  loses its port (reported)
- objects, coordinate systems, boundaries, mesh operations and the design variables are copied with the block
  name as prefix (EM1_Patch, EM1_W); the air region and the open boundaries (Radiation, PML, symmetry and
  periodic boundaries) are left out: the layout has its own open boundary
- the layout's substrate and ground plane reach over the block; the ground plane has a hole where the block has
  its own ground (a horizontal conductor sheet at the ground level), so the two grounds do not lie on each other
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from . import circuit as CK
from .design import Assignment, Command, Design, FaceRef, Project

DROP_BOUNDARIES = ("Radiation", "PML", "Symmetry", "Primary", "Secondary", "Layered Impedance")
CONDUCTOR_BOUNDARIES = ("Perfect E", "Finite Conductivity")
TOL = 1e-9


class EmbedError(Exception):
    pass


@dataclass
class PortGeo:
    """A port of the 3D design, in its own coordinates (m)."""
    name: str
    centre: tuple[float, float]           # where a line attaches (x, y)
    out: tuple[int, int]                  # unit axis direction out of the design
    width: float                          # extent of the port across out
    zmin: float                           # ground level
    zmax: float                           # top of the port (a lumped port: the trace height)
    kind: str
    normal: tuple[float, float] = (0.0, 0.0)   # horizontal unit normal out of the design (slanted ports too)


@dataclass
class EmGeometry:
    design: Design
    ports: list[PortGeo]                  # in the order of the block pins
    bbox: tuple[float, float, float, float]   # x0, y0, x1, y1 of the design without the air region
    grounds: list[tuple[float, float, float, float]] = field(default_factory=list)  # ground sheet boxes at zmin
    # top view of the conductors above the ground and of the Lumped RLC sheets: (outline, holes) in m
    copper: list[tuple[list, list]] = field(default_factory=list)
    lumped: list[tuple[list, list]] = field(default_factory=list)


def source(project: Project, c: CK.Component) -> Design:
    name = c.props.get("Design", "")
    try:
        s = project.design(name)
    except KeyError:
        raise EmbedError(f"{c.name}: the 3D design '{name}' is not in the project") from None
    if getattr(s, "kind", "3d") != "3d":
        raise EmbedError(f"{c.name}: '{name}' is not a 3D design")
    return s


def pin_ports(s: Design, c: CK.Component) -> list[Assignment]:
    """The port assignment of each pin of the block (pin order = port names of the block)."""
    ports = s.ports()
    by_name = {p.name: p for p in ports}
    names = [n.strip() for n in c.props.get("PortNames", "").split(",") if n.strip()]
    n = CK.nport_count(c)
    out = []
    for i in range(n):
        p = by_name.get(names[i]) if i < len(names) else None
        if p is None and i < len(ports):
            p = ports[i]
        if p is None:
            raise EmbedError(f"{c.name}: the 3D design '{s.name}' has no port for pin {i + 1}")
        out.append(p)
    return out


def geometry(project: Project, c: CK.Component, slanted: bool = False) -> EmGeometry:
    """Build the 3D design of the block and find its ports and extent. slanted: vertical port sheets at any angle
    are accepted (their direction is PortGeo.normal; out is then the nearest axis), else only sheets that face
    along x or y."""
    import gmsh

    from ..kernel import occ as K
    s = source(project, c)
    ports = pin_ports(s, c)
    prev = None
    if gmsh.isInitialized():
        try:
            prev = gmsh.model.getCurrent()
        except Exception:  # noqa: BLE001
            prev = None
    K.ensure_gmsh("hertzon_embed")
    gmsh.model.remove()
    gmsh.model.add("hertzon_embed")
    try:
        return _geometry(project, s, c, ports, slanted)
    finally:
        try:
            gmsh.model.remove()
        except Exception:  # noqa: BLE001
            pass
        if prev and prev != "hertzon_embed":
            try:
                gmsh.model.setCurrent(prev)
            except Exception:  # noqa: BLE001
                pass


def _geometry(project: Project, s: Design, c: CK.Component, ports: list[Assignment], slanted: bool = False) \
        -> EmGeometry:
    import gmsh

    from ..kernel import occ as K
    ev = s.evaluator(project)
    ctx = K.build_design(s, ev)
    if ctx.errors:
        name, err = next(iter(ctx.errors.items()))
        raise EmbedError(f"{c.name}: the object {name} of '{s.name}' could not be built: {err}")
    gmsh.model.occ.synchronize()

    def box(dts):
        """Bounding box of entities; OpenCASCADE pads the boxes by its tolerance, taken off again."""
        bb = [gmsh.model.occ.getBoundingBox(d, t) for d, t in dts]
        pad = 1e-7
        lo = [min(b[i] for b in bb) + pad for i in range(3)]
        hi = [max(b[i + 3] for b in bb) - pad for i in range(3)]
        return tuple(round(v, 10) for v in (*lo, *hi))

    regions = {o.name for o in s.live_objects() if o.history[0].kind == "CreateRegion"}
    port_objs = {n for p in ports for n in p.objects}
    body = [dt for n, lst in ctx.built.items() if n not in regions and n not in port_objs for dt in lst]
    if not body:
        raise EmbedError(f"{c.name}: the 3D design '{s.name}' has no geometry")
    bb = box(body)
    cx, cy = (bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2
    geos = []
    for p in ports:
        dts = [dt for n in p.objects for dt in ctx.built.get(n, [])]
        for f in p.faces:
            faces = K.object_faces(ctx.built.get(f.obj, []))
            if f.face < 0:
                dts += [(2, t) for t in faces]
            elif f.face < len(faces):
                dts.append((2, faces[f.face]))
        dts = [dt for dt in dts if dt[0] == 2] or dts
        if not dts:
            raise EmbedError(f"{c.name}: the port {p.name} of '{s.name}' has no geometry")
        x0, y0, z0, x1, y1, z1 = box(dts)
        sx, sy, sz = x1 - x0, y1 - y0, z1 - z0
        scale = max(sx, sy, sz)
        px, py = (x0 + x1) / 2, (y0 + y1) / 2
        nrm = _sheet_normal(dts) if slanted and sz > 1e-3 * scale else None
        if nrm is not None:
            nx, ny = nrm
            if nx * (px - cx) + ny * (py - cy) < 0:
                nx, ny = -nx, -ny
            out = (1 if nx > 0 else -1, 0) if abs(nx) >= abs(ny) else (0, 1 if ny > 0 else -1)
            geos.append(PortGeo(p.name, (px, py), out, (sx * sx + sy * sy) ** 0.5, z0, z1, p.kind, (nx, ny)))
            continue
        if sz <= 1e-3 * scale or min(sx, sy) > 1e-3 * scale + 1e-6:
            raise EmbedError(f"{c.name}: the port {p.name} of '{s.name}' is not a vertical sheet on the edge of "
                             "the design; only such ports (a line entering from the side) can be connected")
        if sx <= sy:                                  # the port faces along x
            out = (1 if px >= cx else -1, 0)
            width = sy
        else:
            out = (0, 1 if py >= cy else -1)
            width = sx
        geos.append(PortGeo(p.name, (px, py), out, width, z0, z1, p.kind, (float(out[0]), float(out[1]))))
    # the design's own ground: horizontal conductor sheets at the ground level of the ports
    zg = min((g.zmin for g in geos), default=bb[2])
    cond = _conductor_objects(s)
    grounds = []
    for n, lst in ctx.built.items():
        if n in regions or n in port_objs or n not in cond:
            continue
        sheets = [dt for dt in lst if dt[0] == 2]
        if not sheets or len(sheets) != len(lst):
            continue
        x0, y0, z0, x1, y1, z1 = box(sheets)
        if abs(z1 - z0) < 1e-6 and abs(z0 - zg) < 1e-6:
            grounds.append((x0, y0, x1, y1))
    rlc = {n for a in s.boundaries if a.kind == "Lumped RLC" and a.enabled for n in a.objects}
    copper = _top_rings(ctx, [n for n in ctx.built if n in cond and n not in regions and n not in port_objs], zg)
    lumped = _top_rings(ctx, [n for n in ctx.built if n in rlc], zg)
    return EmGeometry(s, geos, (bb[0], bb[1], bb[3], bb[4]), grounds, copper, lumped)


def _sheet_normal(dts) -> tuple[float, float] | None:
    """Horizontal unit normal of a flat vertical sheet (its first face); None if it is not vertical."""
    import gmsh
    faces = [t for d, t in dts if d == 2]
    if not faces:
        return None
    lo, hi = gmsh.model.getParametrizationBounds(2, faces[0])
    n = gmsh.model.getNormal(faces[0], [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2])
    nx, ny, nz = n[0], n[1], n[2]
    h = (nx * nx + ny * ny) ** 0.5
    if h < 1e-9 or abs(nz) > 1e-6 * max(h, 1.0) + 1e-9:
        return None
    return nx / h, ny / h


def _curve_points(tag: int) -> list[tuple[float, float, float]]:
    import gmsh
    lo, hi = gmsh.model.getParametrizationBounds(1, tag)
    n = 2 if gmsh.model.getType(1, tag) == "Line" else 33
    ts = [lo[0] + (hi[0] - lo[0]) * i / (n - 1) for i in range(n)]
    xyz = gmsh.model.getValue(1, tag, ts)
    return [tuple(xyz[3 * i:3 * i + 3]) for i in range(n)]


def _top_rings(ctx, names: list[str], zg: float) -> list[tuple[list, list]]:
    """The horizontal faces of the objects names that lie above the ground level zg, seen from the top, joined:
    (outline, holes) per piece, x, y in m."""
    import gmsh
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union

    from ..kernel import occ as K
    polys = []
    for n in names:
        for f in K.object_faces(ctx.built.get(n, [])):
            x0, y0, z0, x1, y1, z1 = gmsh.model.occ.getBoundingBox(2, f)
            if z1 - z0 > 1e-6 or z0 < zg + 1e-6:
                continue
            lines = []
            for _d, e in gmsh.model.getBoundary([(2, f)], combined=False, oriented=False, recursive=False):
                pts = [(round(p[0], 9), round(p[1], 9)) for p in _curve_points(abs(e))]   # ends meet exactly
                if len(pts) >= 2:
                    lines.append(LineString(pts))
            faces = list(polygonize(unary_union(lines))) if lines else []
            if not faces:
                continue
            # polygonize fills the holes with faces of their own: the face is made of the pieces that lie inside
            # an even number of outlines of the other pieces
            outer = [Polygon(q.exterior) for q in faces]
            for i, q in enumerate(faces):
                pt = q.representative_point()
                if sum(1 for j, o in enumerate(outer) if j != i and o.contains(pt)) % 2 == 0:
                    polys.append(q)
    if not polys:
        return []
    out = []
    geo = unary_union(polys).buffer(0)
    for g in getattr(geo, "geoms", [geo]):
        if isinstance(g, Polygon) and not g.is_empty:
            out.append(([tuple(map(float, p)) for p in list(g.exterior.coords)[:-1]],
                        [[tuple(map(float, p)) for p in list(r.coords)[:-1]] for r in g.interiors]))
    return out


def _conductor_objects(s: Design) -> set[str]:
    out = set()
    for a in s.boundaries:
        if a.kind in CONDUCTOR_BOUNDARIES and a.enabled:
            out.update(a.objects)
            out.update(f.obj for f in a.faces if f.face < 0)
    out.update(o.name for o in s.objects if str(o.material).lower() in ("pec", "copper", "gold", "silver",
                                                                          "aluminum", "aluminium"))
    return out


# ---- turning -------------------------------------------------------------------------------------------------
def turn(v, q: int):
    """(x, y) turned by q quarter turns counter-clockwise."""
    x, y = v
    for _ in range(q % 4):
        x, y = -y, x
    return x, y


def quarter_turns(a: tuple[int, int], b: tuple[int, int]) -> int:
    """The quarter turns that take the axis direction a to b."""
    for q in range(4):
        if turn(a, q) == tuple(b):
            return q
    return 0


# ---- copying -------------------------------------------------------------------------------------------------
_NAME_KEYS = ("Tools",)


def _renamer(names: dict[str, str]):
    """Replaces whole-word identifiers (not $project variables, not inside numbers like 1e3)."""
    if not names:
        return lambda t: t
    pat = re.compile(r"(?<![\w$.])(" + "|".join(re.escape(k) for k in sorted(names, key=len, reverse=True))
                     + r")(?![\w])")
    return lambda t: pat.sub(lambda m: names[m.group(1)], t) if isinstance(t, str) else t


def embed(d: Design, g: EmGeometry, c: CK.Component, q: int, move: str, keep: dict[str, tuple[str, str]]) \
        -> tuple[list[str], list[str]]:
    """Copy the 3D design of the block into the layout d, turned by q quarter turns about the z axis and then
    moved by the vector expression move. keep: {port name: (new port name, Power Port impedance)} of the ports
    that stay ports. Returns (names of the copied objects, warnings)."""
    s = g.design
    prefix = f"{c.name}_"
    warnings: list[str] = []
    regions = {o.name for o in s.objects if o.history and o.history[0].kind == "CreateRegion"}
    dropped_ports = [p for p in s.ports() if p.name not in keep]
    # port sheets that nothing but a removed port uses go with it
    used_elsewhere = set()
    for a in s.boundaries + s.mesh_ops + [e for e in s.excitations if e not in dropped_ports]:
        used_elsewhere.update(a.objects)
        used_elsewhere.update(f.obj for f in a.faces)
    drop_objs = set(regions)
    for p in dropped_ports:
        for n in p.objects:
            if n not in used_elsewhere and s.has_obj(n) and s.obj(n).dim == 2:
                drop_objs.add(n)
    # tools of dropped objects go too
    changed = True
    while changed:
        changed = False
        for o in s.objects:
            if o.consumed_by in drop_objs and o.name not in drop_objs:
                drop_objs.add(o.name)
                changed = True

    taken = d.all_names() | {cs.name for cs in d.coord_systems}
    names: dict[str, str] = {}
    for o in s.objects:
        if o.name not in drop_objs:
            names[o.name] = d.unique_name(prefix + o.name, taken)
            taken.add(names[o.name])
    cs_names = {}
    for cs in s.coord_systems:
        n = prefix + cs.name
        i = 2
        while n in taken:
            n = f"{prefix}{cs.name}_{i}"
            i += 1
        cs_names[cs.name] = n
        taken.add(n)
    var_names = {}
    for v in s.variables:
        n = prefix + v
        if n in d.variables:
            warnings.append(f"{c.name}: the design variable {n} existed already and was replaced")
        var_names[v] = n
    rv = _renamer(var_names)

    def props(p: dict[str, str]) -> dict[str, str]:
        out = {}
        for k, v in p.items():
            if k in _NAME_KEYS:
                out[k] = ",".join(names.get(x.strip(), x.strip()) for x in v.split(",") if x.strip())
            elif k in ("CoordinateSystem", "Reference"):
                out[k] = cs_names.get(v, v)
            else:
                out[k] = rv(v)
        return out

    for v, val in s.variables.items():
        d.variables[var_names[v]] = rv(val)
    for cs in s.coord_systems:
        n = copy.deepcopy(cs)
        n.name = cs_names[cs.name]
        n.reference = cs_names.get(cs.reference, cs.reference)
        n.props = props(n.props)
        d.coord_systems.append(n)

    made = []
    for o in s.objects:
        if o.name in drop_objs:
            continue
        n = copy.deepcopy(o)
        n.name = names[o.name]
        n.history = [Command(cmd.kind, props(cmd.props), cmd.suppressed) for cmd in o.history]
        n.consumed_by = names.get(o.consumed_by) if o.consumed_by else None
        n.orientation = cs_names.get(o.orientation, o.orientation)
        n.group = c.name
        if n.consumed_by is None:
            if q % 4:
                n.history.append(Command.new("Rotate", Axis="Z", Angle=f"{90 * (q % 4)}deg", Center="0,0,0"))
            n.history.append(Command.new("Move", Vector=move))
        d.objects.append(n)
        made.append(n.name)

    def refs(a: Assignment) -> Assignment | None:
        objs = [names[x] for x in a.objects if x in names]
        faces = [FaceRef(names[f.obj], f.face, list(f.sig)) for f in a.faces if f.obj in names]
        edges = [FaceRef(names[f.obj], f.face, list(f.sig)) for f in a.edges if f.obj in names]
        if not objs and not faces and not edges:
            return None
        return Assignment(d_unique(prefix + a.name), a.kind, props(a.props), faces, objs, edges, a.enabled)

    taken_a = {a.name for a in d.boundaries + d.excitations + d.mesh_ops}

    def d_unique(n: str) -> str:
        base, i = n, 2
        while n in taken_a:
            n = f"{base}_{i}"
            i += 1
        taken_a.add(n)
        return n

    for a in s.boundaries:
        if a.kind in DROP_BOUNDARIES:
            continue
        r = refs(a)
        if r is not None:
            d.boundaries.append(r)
    for a in s.mesh_ops:
        r = refs(a)
        if r is not None:
            d.mesh_ops.append(r)
    for a in s.excitations:
        if a.kind in ("Wave Port", "Lumped Port", "Floquet Port"):
            if a.name not in keep:
                continue
            r = refs(a)
            if r is None:
                continue
            new_name, z0 = keep[a.name]
            taken_a.discard(r.name)
            r.name = new_name
            r.props["PortNumber"] = new_name
            if a.kind == "Lumped Port" and z0:
                r.props["Impedance"] = z0
            d.excitations.append(r)
        elif a.kind != "Incident Plane Wave":
            r = refs(a)
            if r is not None:
                d.excitations.append(r)
    return made, warnings


# ---- 3D designs from another project file ------------------------------------------------------------------
def import_3d_designs(project: Project, path: str) -> tuple[list[str], list[str]]:
    """Copy the 3D designs with ports of the project file at path into project (renamed where the name is
    taken), with the project materials and project variables they need and their solved results (when both
    projects are saved). Returns (names of the new designs, warnings)."""
    import json
    import shutil
    from pathlib import Path

    from ..solver.results import results_dir
    from .design import design_from_dict
    src = Project.load(path)
    warnings: list[str] = []
    names = []
    taken = {d.name for d in project.designs}
    data = src.to_dict()["designs"]
    for i, s in enumerate(src.designs):
        if getattr(s, "kind", "3d") != "3d" or not s.objects:
            continue
        if not s.ports():
            warnings.append(f"{s.name} has no ports and was left out")
            continue
        d = design_from_dict(json.loads(json.dumps(data[i])))
        n, k = d.name, 2
        while n in taken:
            n = f"{d.name}_{k}"
            k += 1
        d.name = n
        taken.add(n)
        project.designs.append(d)
        names.append(n)
        if project.path:
            a = results_dir(path, s.name, "x").parent
            b = results_dir(project.path, n, "x").parent
            if a.is_dir() and not b.exists():
                try:
                    shutil.copytree(a, b)
                except OSError as e:
                    warnings.append(f"the results of {s.name} could not be copied: {e}")
    if not names:
        return names, [f"{Path(path).name} has no 3D design"]
    if not project.path:
        warnings.append("this project is not saved yet: the imported designs need to be analyzed here")
    have = {m.name for m in project.materials}
    for m in src.materials:
        if m.name not in have:
            project.materials.append(copy.deepcopy(m))
    for k, v in src.variables.items():
        if k not in project.variables:
            project.variables[k] = v
        elif project.variables[k].strip() != v.strip():
            warnings.append(f"the project variable {k} is {project.variables[k]} here and {v} in "
                            f"{Path(path).name}: this project's value is used")
    return names, warnings
