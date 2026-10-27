"""Project and design data model.

The structure mirrors the reference project tree:
Project > Design > {Model, Boundaries, Excitations, Mesh, Analysis, Radiation, Results}.
Everything is plain data so it round-trips through JSON and undo snapshots.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import schema
from .expr import Evaluator
from .materials import Material, default_color, library_material, library_materials, normalize_color

FILE_VERSION = 2     # 2: integration lines and U vectors in the coordinates of their object


@dataclass
class Command:
    kind: str
    props: dict[str, str] = field(default_factory=dict)
    suppressed: bool = False

    def __post_init__(self) -> None:
        schema.upgrade_props(self.kind, self.props)

    @staticmethod
    def new(kind: str, **props) -> "Command":
        p = schema.defaults(schema.GEOMETRY[kind])
        p.update({k: str(v) for k, v in props.items()})
        return Command(kind, p)


@dataclass
class GeoObject:
    name: str
    history: list[Command]
    material: str = "vacuum"
    solve_inside: bool = True
    model: bool = True
    color: str = "#8f8f8f"
    transparency: float = 0.0
    wireframe: bool = False
    visible: bool = True
    consumed_by: str | None = None     # set when used as a boolean tool
    group: str = ""
    orientation: str = "Global"      # coordinate system the object is defined in (Orientation)

    @property
    def create(self) -> Command:
        return self.history[0]

    @property
    def dim(self) -> int:
        """Topological dimension estimated from history: 3 solid, 2 sheet, 1 line."""
        d = 3
        c = self.history[0]
        if c.kind in schema.SHEET_COMMANDS:
            d = 2
        elif c.kind == "CreatePolyline":
            d = 2 if (c.props.get("Closed") == "True" and c.props.get("Covered") == "True") else 1
        elif c.kind == "CreateBondwire":
            d = 3
        elif c.kind == "CreateHelix":
            d = 3 if _nonzero(c.props.get("WireRadius", "0")) else 1
        elif c.kind == "CreateSpiral":
            d = 2 if _nonzero(c.props.get("Width", "0")) else 1
        elif c.kind == "Import":
            d = int(c.props.get("Dim", "3") or 3)
        for cmd in self.history[1:]:
            if cmd.suppressed:
                continue
            if cmd.kind in ("ThickenSheet", "SweepAlongVector", "SweepAroundAxis", "SweepAlongPath",
                            "Connect") and d == 2:
                d = 3
            elif cmd.kind in ("SweepAlongVector", "SweepAroundAxis", "SweepAlongPath", "Connect") and d == 1:
                d = 2
            elif cmd.kind in ("UncoverFaces", "KeepFaces", "Section"):
                d = 2
            elif cmd.kind == "Cover" and d == 1:
                d = 2
        return d


def _nonzero(expr: str) -> bool:
    """True unless the expression is a literal zero (used for the topological dimension guess)."""
    import re
    m = re.match(r"^\s*([-+0-9.eE]+)\s*[a-zA-Z]*\s*$", expr or "0")
    if not m:
        return True
    try:
        return float(m.group(1)) != 0
    except ValueError:
        return True


@dataclass
class FaceRef:
    """A face of an object (index in its boundary) or the whole object (face=-1).

    For sheets ``face=-1`` means the sheet itself; for solids it means all faces."""
    obj: str
    face: int = -1
    # geometric signature (centre, unit normal, area) of the face when it was last seen: lets the
    # assignment follow its face when a change of dimensions renumbers the faces (face IDs)
    sig: list[float] = field(default_factory=list)

    def key(self) -> str:
        return f"{self.obj}:{self.face}"

    @staticmethod
    def parse(text: str) -> "FaceRef":
        o, _, f = text.rpartition(":")
        return FaceRef(o, int(f))


@dataclass
class Assignment:
    """A boundary, excitation or mesh operation on a selection."""
    name: str
    kind: str
    props: dict[str, str]
    faces: list[FaceRef] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    edges: list[FaceRef] = field(default_factory=list)
    enabled: bool = True


@dataclass
class Sweep:
    name: str
    props: dict[str, str] = field(default_factory=lambda: schema.defaults(schema.SWEEP))


@dataclass
class Setup:
    name: str
    props: dict[str, str] = field(default_factory=lambda: {**schema.defaults(schema.SETUP), "_BasisNames": "v2"})
    sweeps: list[Sweep] = field(default_factory=list)
    eigen: bool = False


@dataclass
class InfiniteSphere:
    name: str
    props: dict[str, str] = field(default_factory=lambda: schema.defaults(schema.INFINITE_SPHERE))


@dataclass
class CoordSys:
    name: str
    kind: str = "Relative"          # "Relative" or "Face" (Relative CS / Face CS)
    reference: str = "Global"
    props: dict[str, str] = field(default_factory=lambda: schema.defaults(schema.COORDINATE_SYSTEM))


@dataclass
class Report:
    name: str
    kind: str                       # "Rectangular", "Smith", "Polar", "Radiation Pattern", "3D Polar", "Data Table"
    solution: str                   # "Setup1 : Sweep" or "Setup1 : LastAdaptive"
    traces: list[str] = field(default_factory=lambda: ["dB(S(1,1))"])
    props: dict[str, str] = field(default_factory=dict)


@dataclass
class FieldPlot:
    name: str
    quantity: str                   # "Mag_E", "Vector_E", "Mag_H", "Mag_Jsurf", "Vector_J", ...
    solution: str
    faces: list[FaceRef] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    plane: str = ""                 # "XY@0" style cut plane when no faces
    props: dict[str, str] = field(default_factory=dict)


@dataclass
class FieldExpression:
    """A named expression of the Fields Calculator (a field such as a loss density, or a value such as a
    flux or a maximum); it belongs to the design and is evaluated on any solution with saved fields."""
    name: str
    expr: str
    description: str = ""


@dataclass
class Optimization:
    """Optimization: variables with ranges, goals on a solution and the optimizer settings."""
    name: str
    solution: str = ""              # "Setup1 : Sweep" or "Setup1 : LastAdaptive"
    variables: list[dict] = field(default_factory=list)   # {"name", "include", "min", "max"}
    goals: list[dict] = field(default_factory=list)       # {"calc", "start", "stop", "cond", "value", "weight"}
    props: dict[str, str] = field(default_factory=lambda: schema.defaults(schema.OPTIMIZATION))


@dataclass
class ParametricSetup:
    """Optimization > Parametric: sweep definitions of variables, the setups solved for every variation
    and calculations on the solutions."""
    name: str
    sweeps: list[dict] = field(default_factory=list)      # {"variable", "type", "start", "stop", "step", "sync"}
    setups: list[str] = field(default_factory=list)       # General tab: setups solved for every variation
    calcs: list[dict] = field(default_factory=list)       # {"calc", "solution", "func", "start", "stop"}
    props: dict[str, str] = field(default_factory=lambda: schema.defaults(schema.PARAMETRIC))


@dataclass
class Design:
    name: str = "Design1"
    units: str = "mm"
    settings: dict[str, str] = field(default_factory=lambda: schema.defaults(schema.DESIGN_SETTINGS))
    variables: dict[str, str] = field(default_factory=dict)
    objects: list[GeoObject] = field(default_factory=list)
    boundaries: list[Assignment] = field(default_factory=list)
    excitations: list[Assignment] = field(default_factory=list)
    mesh_ops: list[Assignment] = field(default_factory=list)
    setups: list[Setup] = field(default_factory=list)
    spheres: list[InfiniteSphere] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)
    field_plots: list[FieldPlot] = field(default_factory=list)
    coord_systems: list[CoordSys] = field(default_factory=list)
    field_exprs: list[FieldExpression] = field(default_factory=list)
    field_funcs: list[str] = field(default_factory=list)     # Fields Calculator functions "name(a, b) = body"
    optimetrics: list[Optimization] = field(default_factory=list)
    parametrics: list[ParametricSetup] = field(default_factory=list)
    working_cs: str = "Global"
    notes: str = ""

    def cs(self, name: str) -> CoordSys:
        for c in self.coord_systems:
            if c.name == name:
                return c
        raise KeyError(name)

    def cs_names(self) -> list[str]:
        return ["Global"] + [c.name for c in self.coord_systems]

    # ---- lookups -----------------------------------------------------------------------------
    def obj(self, name: str) -> GeoObject:
        for o in self.objects:
            if o.name == name:
                return o
        raise KeyError(name)

    def has_obj(self, name: str) -> bool:
        return any(o.name == name for o in self.objects)

    def live_objects(self) -> list[GeoObject]:
        """Objects that exist in the model (not consumed by a boolean)."""
        return [o for o in self.objects if o.consumed_by is None]

    def unique_name(self, base: str, taken: set[str] | None = None) -> str:
        taken = taken if taken is not None else self.all_names()
        if base not in taken:
            return base
        m = re.match(r"^(.*?)(\d+)$", base)
        stem = m.group(1) if m else base
        i = int(m.group(2)) if m else 1
        while f"{stem}{i}" in taken:
            i += 1
        return f"{stem}{i}"

    def all_names(self) -> set[str]:
        names = {o.name for o in self.objects} | {o.group for o in self.objects if o.group}
        for lst in (self.boundaries, self.excitations, self.mesh_ops):
            names |= {a.name for a in lst}
        names |= {s.name for s in self.setups}
        return names

    def evaluator(self, project: "Project | None" = None) -> Evaluator:
        vars_ = dict(project.variables) if project else {}
        vars_.update(self.variables)
        return Evaluator(vars_, self.units)

    def assignment(self, name: str) -> Assignment:
        for lst in (self.boundaries, self.excitations, self.mesh_ops):
            for a in lst:
                if a.name == name:
                    return a
        raise KeyError(name)

    def setup(self, name: str) -> Setup:
        for s in self.setups:
            if s.name == name:
                return s
        raise KeyError(name)

    def ports(self) -> list[Assignment]:
        return sorted([e for e in self.excitations if e.kind in ("Wave Port", "Lumped Port", "Floquet Port")
                       and e.enabled], key=lambda e: int(e.props.get("PortNumber", "0") or 0))

    def next_port_number(self) -> int:
        nums = [int(e.props.get("PortNumber", "0") or 0) for e in self.excitations
                if e.kind in schema.FACE_EXCITATIONS]
        return max(nums, default=0) + 1

    # ---- object creation helpers --------------------------------------------------------------
    def add_object(self, kind: str, name: str | None = None, material: str = "vacuum", **props) -> GeoObject:
        base = name or {"CreateBox": "Box", "CreateCylinder": "Cylinder", "CreateSphere": "Sphere",
                        "CreateCone": "Cone", "CreateTorus": "Torus", "CreateRegularPolyhedron": "Polyhedron",
                        "CreateRectangle": "Rectangle", "CreateCircle": "Circle", "CreateEllipse": "Ellipse",
                        "CreateRegularPolygon": "Polygon", "CreatePolyline": "Polyline",
                        "CreateHelix": "Helix", "CreateSpiral": "Spiral", "CreateBondwire": "Bondwire",
                        "CreateRegion": "Region", "Import": "Imported"}.get(kind, "Object")
        nm = self.unique_name(base if name else base + "1")
        o = GeoObject(nm, [Command.new(kind, **props)], material=material, color=default_color(material))
        if kind in schema.SHEET_COMMANDS or kind == "CreatePolyline" or (o.dim < 3 and kind != "CreateRegion"):
            o.color = "#8080ff"
            o.transparency = 0.0
        if kind == "CreateRegion":
            o.material = "air"
            o.color = "#e6f2ff"
            o.transparency = 0.85
            o.wireframe = False
        self.objects.append(o)
        return o


@dataclass
class Project:
    name: str = "Project1"
    designs: list[Design] = field(default_factory=lambda: [Design()])
    variables: dict[str, str] = field(default_factory=dict)       # project variables ($name)
    materials: list[Material] = field(default_factory=list)       # project-local materials
    path: str | None = None

    def material(self, name: str) -> Material:
        for m in self.materials:
            if m.name == name:
                return m
        m = library_material(name)
        if m is not None:
            return m
        raise KeyError(f"Unknown material '{name}'")

    def all_materials(self) -> list[Material]:
        local = {m.name for m in self.materials}
        return list(self.materials) + [m for m in library_materials() if m.name not in local]

    def design(self, name: str) -> Design:
        for d in self.designs:
            if d.name == name:
                return d
        raise KeyError(name)

    # ---- serialisation -------------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        d["format"] = "hertzon-project"
        d["version"] = FILE_VERSION
        return d

    def save(self, path: str | Path) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        tmp.replace(path)
        self.path = str(path)

    @staticmethod
    def load(path: str | Path) -> "Project":
        p = project_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        p.path = str(path)
        # imported CAD/STL files given relative to the project file (as in the shipped examples)
        base = Path(path).resolve().parent
        for d in p.designs:
            for o in d.objects:
                for c in o.history:
                    f = c.props.get("File", "") if c.kind == "Import" else ""
                    if f and not Path(f).is_absolute():
                        c.props["File"] = str(base / f)
        return p

    def clone(self) -> "Project":
        c = project_from_dict(copy.deepcopy(self.to_dict()))
        c.path = self.path
        return c


def _assign(d: dict) -> Assignment:
    return Assignment(name=d["name"], kind=d["kind"], props=dict(d.get("props", {})),
                      faces=[FaceRef(**f) for f in d.get("faces", [])], objects=list(d.get("objects", [])),
                      edges=[FaceRef(**f) for f in d.get("edges", [])], enabled=d.get("enabled", True))


def design_from_dict(d: dict) -> Design:
    des = Design(name=d.get("name", "Design1"), units=d.get("units", "mm"))
    des.settings.update(d.get("settings", {}))
    des.variables = dict(d.get("variables", {}))
    for o in d.get("objects", []):
        hist = [Command(c["kind"], dict(c.get("props", {})), c.get("suppressed", False)) for c in o["history"]]
        kw = {k: v for k, v in o.items() if k in GeoObject.__dataclass_fields__ and k != "history"}
        if "color" in kw:
            kw["color"] = normalize_color(kw["color"])
        des.objects.append(GeoObject(history=hist, **kw))
    des.boundaries = [_assign(a) for a in d.get("boundaries", [])]
    des.excitations = [_assign(a) for a in d.get("excitations", [])]
    for e in des.excitations:
        if e.kind == "Incident Plane Wave":
            e.props.setdefault("VectorInput", "Spherical")     # older files: angles only
    for b in des.boundaries:
        if b.kind == "Finite Conductivity":
            b.props.setdefault("TwoSided", b.props.get("UseThickness", "False"))  # older files: thickness = two-sided
        if b.kind == "Secondary" and "PhaseMode" not in b.props:     # older files: a phase delay only
            delay = str(b.props.get("PhaseDelay", "0")).strip()
            b.props["PhaseMode"] = "Scan Angles" if delay in ("", "0", "0deg") else "Input Phase Delay"
    des.mesh_ops = [_assign(a) for a in d.get("mesh_ops", [])]
    for s in d.get("setups", []):
        st = Setup(s["name"], eigen=s.get("eigen", False))
        st.props = schema.defaults(schema.EIGEN_SETUP if st.eigen else schema.SETUP)
        saved = dict(s.get("props", {}))
        if "_BasisNames" not in saved and "BasisOrder" in saved:
            # older files: "Second Order" had 20 functions = today's "First Order"; complete spaces -> "Second Order"
            saved["BasisOrder"] = {"Second Order": "First Order", "First Order Complete": "Second Order",
                                   "Second Order Complete": "Second Order"}.get(saved["BasisOrder"], saved["BasisOrder"])
        st.props.update(schema.upgrade_values(saved))
        st.props["_BasisNames"] = "v2"
        for sw in s.get("sweeps", []):
            swp = Sweep(sw["name"])
            swp.props.update(sw.get("props", {}))
            st.sweeps.append(swp)
        des.setups.append(st)
    for s in d.get("spheres", []):
        sp = InfiniteSphere(s["name"])
        sp.props.update(s.get("props", {}))
        des.spheres.append(sp)
    des.reports = [Report(**r) for r in d.get("reports", [])]
    for f in d.get("field_plots", []):
        f = dict(f)
        f["faces"] = [FaceRef(**x) for x in f.get("faces", [])]
        des.field_plots.append(FieldPlot(**f))
    des.field_exprs = [FieldExpression(f["name"], f.get("expr", ""), f.get("description", ""))
                       for f in d.get("field_exprs", [])]
    des.field_funcs = [str(x) for x in d.get("field_funcs", [])]
    for c in d.get("coord_systems", []):
        cs = CoordSys(c["name"], c.get("kind", "Relative"), c.get("reference", "Global"))
        cs.props.update(c.get("props", {}))
        des.coord_systems.append(cs)
    for o in d.get("optimetrics", []):
        op = Optimization(o["name"], o.get("solution", ""), [dict(v) for v in o.get("variables", [])],
                          [dict(g) for g in o.get("goals", [])])
        op.props.update(o.get("props", {}))
        des.optimetrics.append(op)
    for o in d.get("parametrics", []):
        ps = ParametricSetup(o["name"], [dict(s) for s in o.get("sweeps", [])], list(o.get("setups", [])),
                             [dict(c) for c in o.get("calcs", [])])
        ps.props.update(o.get("props", {}))
        des.parametrics.append(ps)
    des.working_cs = d.get("working_cs", "Global")
    des.notes = d.get("notes", "")
    return des


def project_from_dict(d: dict) -> Project:
    p = Project(name=d.get("name", "Project1"), designs=[])
    p.variables = dict(d.get("variables", {}))
    p.materials = [Material.from_dict(m) for m in d.get("materials", [])]
    p.designs = [design_from_dict(x) for x in d.get("designs", [])] or [Design()]
    if int(d.get("version", 1) or 1) < 2:
        from .objframe import migrate_global_points
        from ..kernel.occ import migrate_thicken_direction
        for des in p.designs:
            migrate_global_points(des, des.evaluator(p))
            migrate_thicken_direction(des, des.evaluator(p))
    return p
