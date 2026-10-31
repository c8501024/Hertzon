"""The Hertzon MCP tools: build, solve and evaluate designs from an AI assistant.

Every tool works on a project. With the live link on (Tools > AI Assistants in Hertzon) and the
project open in a window, edits go through that window (visible at once, undo with Ctrl+Z) and
analyses run in its solve queue. Otherwise the tools read and write the .hzn file and run the
solver as a background process.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..model import schema
from ..model.design import (Project, Design, Command, Assignment, FaceRef, Setup, Sweep, InfiniteSphere, CoordSys,
                            Report, FieldPlot, FieldExpression, Optimization, ParametricSetup, project_from_dict)
from ..model.expr import LENGTH_UNITS
from ..model.materials import parse_color
from . import live
from .protocol import Tool, ToolError, Image

ROOT = Path(__file__).resolve().parents[2]


def window_mode() -> bool:
    """Default: the assistant always works in a Hertzon window (started if none is open), so the user sees
    every change and every analysis. HERTZON_MCP_MODE=file works on the .hzn files without a window
    (servers, tests)."""
    return os.environ.get("HERTZON_MCP_MODE", "window").lower() != "file"

INSTRUCTIONS = """Hertzon is a 3D full-wave FEM simulator (local workflow, EMerge solver). These tools build
and run simulations in Hertzon projects (.hzn files).

Workflow
1. create_project (or open_project). The project opens in a Hertzon window (one is started if none is
   open); every change shows up there at once (the user can undo it) and analyses run in that window's
   solve queue. Do everything through these tools: do not edit .hzn files or start the solver yourself.
2. set_design: units, variables, solution settings (AutoOpen for antennas and scattering).
3. add_object for solids and sheets, add_operation for booleans and transforms, import_geometry for CAD.
4. get_geometry to see face indices, centres and normals; render_image to look at the model.
5. assign boundaries, excitations (ports, plane wave) and mesh operations to objects or faces.
6. set_setup (solution frequency, passes) with sweeps; set_far_field_sphere for gain/RCS.
7. validate, then run_simulation; get_status(wait_seconds=...) until done; get_results.
8. Optional: set_report and set_field_plot (shown in the window), set_equation (Fields Calculator),
   set_parametric / set_optimization, run with run_simulation(parametric=... or optimization=...).

Conventions
- All values are expression strings with units: "10mm", "2.45GHz", "a/2+1mm", "-w/2,0,h". Lengths
  without a unit are in the design units. Variables are defined with set_design and used by name.
- Points and vectors are "x,y,z"; integration lines "x1,y1,z1;x2,y2,z2".
- get_schema lists every kind with its properties, defaults and choices. Only pass the properties you
  want to change; the rest keep their defaults.
- Sheets (CreateRectangle, CreateCircle ...) model ports and thin metal: assign "Perfect E" or
  "Finite Conductivity" to a sheet object for traces, patches and ground planes.
- Ports: a "Lumped Port" is a sheet between two conductors with an IntegrationLine from one to the other;
  the line is in the coordinates of the sheet as created (before its Move/Rotate/Mirror/Scale) and follows it;
  a "Wave Port" is a sheet (or outer face) covering the cross section of a waveguide or line at the
  boundary of the solution region. Port numbers set S(i,j).
- Metal volumes: material "pec" or "copper" (not meshed inside). Air/vacuum boxes are solved inside.
- Open problems: set_design settings AutoOpen="Auto Open" adds an air region with a radiation boundary
  automatically (far fields need an Infinite Sphere). Closed problems (waveguides, cavities) have PEC
  walls at the outer faces of the solution region by default.
- Scattering/RCS: an "Incident Plane Wave" excitation and no ports, AutoOpen on, a far-field sphere.
- Solution frequency: the operating frequency (antennas) or the highest frequency of interest (filters).
  Keep MaximumPasses and MaximumRefinement moderate at first; large models take minutes to hours.
- Objects consumed by a boolean (Subtract tools ...) disappear from the model but stay in the history.
- Fillet and Chamfer take edge indices (Edges) of solids, or vertex indices (Vertices) of sheets:
  get_geometry(include_edges=true) lists them; render_image(label_faces_of=..., label_what="edges") shows them.
- Coordinate systems: set_coordinate_system makes one the working CS; new objects are created in it.
"""


# ------------------------------------------------------------------------------------------------
# Session state and project access
# ------------------------------------------------------------------------------------------------
class _Session:
    default_path: str | None = None
    procs: dict[str, subprocess.Popen] = {}
    tess: dict[str, tuple] = {}            # object key -> Tess (built in this process)


S = _Session()


@dataclass
class Ctx:
    proj: Project
    des: Design
    gui: live.GuiLink | None

    @property
    def path(self) -> str | None:
        return self.proj.path


def _abspath(p: str) -> str:
    return str(Path(os.path.expanduser(p)).resolve())


def _gui_for(path: str | None):
    """The live Hertzon window showing this project (without a path: the window of the current project, the
    file of the current project when no window shows it, else the newest window)."""
    wins = live.windows()
    if path:
        for link, hello in wins:
            if live.same_path(hello.get("path"), path):
                return link, hello
        return None, None
    if S.default_path:
        for link, hello in wins:
            if live.same_path(hello.get("path"), S.default_path):
                return link, hello
        if Path(S.default_path).exists():
            return None, None           # the current project is not open in a window: work on the file
    return wins[0] if wins else (None, None)


def _launch_window(path: str | None):
    """Start a Hertzon window with the live link on (and the project open) and wait until it answers."""
    before = {link.pid for link, _ in live.windows()}
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    cmd = [str(pyw if pyw.exists() else exe), "-m", "hertzon", "--ai-link"] + ([path] if path else [])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT), env=env)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kw)
    except OSError as e:
        raise ToolError(f"Hertzon could not be started ({e})") from None
    t0 = time.time()
    while time.time() - t0 < 180:
        for link, hello in live.windows():
            if link.pid not in before and (not path or live.same_path(hello.get("path"), path)):
                return link, hello
        if proc.poll() not in (None, 0):
            raise ToolError(f"Hertzon could not be started (exit code {proc.returncode}); see the crash log in "
                            "~/.hertzon")
        time.sleep(0.5)
    raise ToolError("Hertzon did not open within 3 minutes")


def _window_with(path: str):
    """The window showing this project: an open one, else an idle window without unsaved changes opens it,
    else a new Hertzon window is started for it."""
    wins = live.windows()
    for link, hello in wins:
        if live.same_path(hello.get("path"), path):
            return link, hello
    for link, hello in wins:
        if not hello.get("dirty") and not hello.get("solving") and not hello.get("queue"):
            try:
                link.call("open_project", path=path, timeout=180)
                return link, link.call("hello")
            except live.LinkError:
                continue
    return _launch_window(path)


def load(args: dict, design_required: bool = True) -> Ctx:
    path = args.get("project")
    path = _abspath(path) if path else None
    gui, hello = _gui_for(path)
    if gui is None and window_mode():
        target = path or S.default_path
        if target is None:
            wins = live.windows()
            if not wins:
                raise ToolError("No project yet: call create_project or open_project (a Hertzon window opens).")
            gui, hello = wins[0]
        else:
            if not Path(target).exists():
                raise ToolError(f"Project file not found: {target}")
            gui, hello = _window_with(target)
    if gui is None and path is None:
        path = S.default_path
        if path is None:
            raise ToolError("No project given. Pass 'project' (path of a .hzn file), call create_project / "
                            "open_project, or open the project in Hertzon with the live link on "
                            "(Tools > AI Assistants).")
    if gui is not None:
        r = gui.call("get_project")
        proj = project_from_dict(r["project"])
        proj.path = r.get("path")
        dname = args.get("design") or r.get("design")
    else:
        if not Path(path).exists():
            raise ToolError(f"Project file not found: {path}")
        proj = Project.load(path)
        dname = args.get("design") or proj.designs[0].name
    try:
        des = proj.design(dname)
    except KeyError:
        raise ToolError(f"Design '{dname}' not found; designs: {[d.name for d in proj.designs]}") from None
    if proj.path:
        S.default_path = proj.path
    return Ctx(proj, des, gui)


def commit(ctx: Ctx, label: str) -> dict:
    """Write the changed project back (window or file). Returns geometry errors reported by the window."""
    if ctx.gui is not None:
        r = ctx.gui.call("set_project", project=ctx.proj.to_dict(), design=ctx.des.name, label=label) or {}
        return r.get("errors") or {}
    if not ctx.proj.path:
        raise ToolError("The project has no file yet")
    ctx.proj.save(ctx.proj.path)
    return {}


# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------
def _props_in(kind: str, plist: list, values: dict | None, base: dict | None = None) -> dict:
    names = {p.name for p in plist}
    out = dict(base) if base is not None else schema.defaults(plist)
    values = dict(values or {})
    if "Width" in values or "Height" in values:     # older names (CreateRectangle)
        values.setdefault("Axis", out.get("Axis", "Z"))
        schema.upgrade_props(kind, values)
    schema.upgrade_values(values)
    for k, v in values.items():
        if k not in names:
            raise ToolError(f"{kind} has no property '{k}'. Properties: {sorted(names)}")
        out[k] = _s(v)
    return out


def _s(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (list, tuple)):
        return ",".join(_s(x) for x in v)
    return str(v)


def _faces(items) -> list[FaceRef]:
    out = []
    for f in items or []:
        if isinstance(f, str):
            out.append(FaceRef.parse(f))
        else:
            out.append(FaceRef(str(f["object"]), int(f.get("face", -1))))
    return out


def _check_objects(des: Design, names) -> None:
    for n in names or []:
        if not des.has_obj(n):
            raise ToolError(f"Object '{n}' does not exist")


def _unit(des: Design) -> float:
    return LENGTH_UNITS.get(des.units, 1e-3)


def _r(x, n=6):
    return float(f"{float(x):.{n}g}")


def _obj_summary(o) -> dict:
    d = {"name": o.name, "dim": o.dim, "material": o.material,
         "history": [{"kind": c.kind, "props": c.props, **({"suppressed": True} if c.suppressed else {})}
                     for c in o.history]}
    if o.consumed_by:
        d["consumed_by"] = o.consumed_by
    if not o.model:
        d["model"] = False
    if not o.solve_inside:
        d["solve_inside"] = False
    if o.orientation != "Global":
        d["orientation"] = o.orientation
    return d


def _assign_summary(a: Assignment) -> dict:
    d = {"name": a.name, "kind": a.kind, "props": a.props}
    if a.objects:
        d["objects"] = a.objects
    if a.faces:
        d["faces"] = [{"object": f.obj, "face": f.face} for f in a.faces]
    if a.edges:
        d["edges"] = [{"object": f.obj, "edge": f.face} for f in a.edges]
    if not a.enabled:
        d["enabled"] = False
    return d


def _solution_state(ctx: Ctx, setup: str) -> str:
    if not ctx.proj.path:
        return "not saved"
    from ..solver.results import results_dir, run_status
    return run_status(results_dir(ctx.proj.path, ctx.des.name, setup)).get("state", "not solved")


def summary(ctx: Ctx) -> dict:
    p, d = ctx.proj, ctx.des
    return {
        "project": p.path, "name": p.name, "live_window": ctx.gui is not None,
        "designs": [x.name for x in p.designs], "design": d.name, "units": d.units,
        "settings": d.settings, "project_variables": p.variables, "variables": d.variables,
        "objects": [_obj_summary(o) for o in d.objects],
        "boundaries": [_assign_summary(a) for a in d.boundaries],
        "excitations": [_assign_summary(a) for a in d.excitations],
        "mesh_operations": [_assign_summary(a) for a in d.mesh_ops],
        "setups": [{"name": s.name, "eigen": s.eigen, "props": s.props, "state": _solution_state(ctx, s.name),
                    "sweeps": [{"name": w.name, "props": w.props} for w in s.sweeps]} for s in d.setups],
        "far_field_spheres": [{"name": s.name, "props": s.props} for s in d.spheres],
        "project_materials": [m.name for m in p.materials],
        **({"coordinate_systems": [{"name": c.name, "kind": c.kind, "reference": c.reference, "props": c.props}
                                   for c in d.coord_systems], "working_cs": d.working_cs} if d.coord_systems else {}),
        **(_equations_summary(d) if d.field_exprs or d.field_funcs else {}),
        **({"reports": [{"name": r.name, "kind": r.kind, "solution": r.solution, "traces": r.traces,
                         **({"props": r.props} if r.props else {})} for r in d.reports]} if d.reports else {}),
        **({"field_plots": [{"name": f.name, "quantity": f.quantity, "solution": f.solution,
                             **({"plane": f.plane} if f.plane else {"objects": f.objects,
                                                                    "faces": [{"object": x.obj, "face": x.face}
                                                                              for x in f.faces]})}
                            for f in d.field_plots]} if d.field_plots else {}),
        **({"parametrics": [{"name": o.name, "sweeps": o.sweeps, "setups": o.setups, "calcs": o.calcs,
                             "props": o.props} for o in d.parametrics]} if d.parametrics else {}),
        **({"optimizations": [{"name": o.name, "solution": o.solution, "variables": o.variables, "goals": o.goals,
                               "props": o.props} for o in d.optimetrics]} if d.optimetrics else {}),
    }


# ------------------------------------------------------------------------------------------------
# geometry (built in this process with gmsh)
# ------------------------------------------------------------------------------------------------
def tessellate(proj: Project, des: Design, names: list[str] | None = None) -> tuple[dict, dict]:
    """Display tessellation of the live objects: ({name: Tess}, {name: error})."""
    import gmsh
    from ..kernel import occ as K
    ev = des.evaluator(proj)
    live_objs = des.live_objects()
    if names is not None:
        live_objs = [o for o in live_objs if o.name in names]
    keys = {o.name: K.object_key(des, o, ev) for o in live_objs}
    regions = [o.name for o in live_objs if o.history[0].kind == "CreateRegion"]
    out, errors = {}, {}
    todo = []
    for n, k in keys.items():
        hit = S.tess.get(k)
        if hit is not None and n not in regions:
            out[n] = hit
        else:
            todo.append(n)
    if todo:
        K.ensure_gmsh("hertzon_mcp")
        gmsh.model.remove()
        gmsh.model.add("hertzon_mcp")
        # regions need the bounding box of all model objects
        build = None if regions and names is None else todo
        ctx = K.build_design(des, ev, names=build)
        tess = K.tessellate(ctx, "coarse") if ctx.built else {}
        for n in todo:
            if n in ctx.errors:
                errors[n] = ctx.errors[n]
            elif n in tess:
                out[n] = tess[n]
                if n not in regions:
                    S.tess[keys[n]] = tess[n]
    return out, errors


def _face_list(t, scale: float, limit: int) -> list[dict]:
    from ..model.facetrack import _flatness
    faces = []
    for i in range(min(len(t.face_area), limit)):
        c = np.asarray(t.face_center[i]) / scale
        faces.append({"face": i, "center": [_r(x, 5) for x in c],
                      "normal": [_r(x, 3) for x in t.face_normal[i]],
                      "area": _r(t.face_area[i] / scale ** 2, 4),
                      "planar": bool(_flatness(t, i) > 0.999)})
    return faces


def _edge_list(t, scale: float, limit: int) -> list[dict]:
    """Edges in the order of the Edges property of Fillet and Chamfer: end points, midpoint and length."""
    out = []
    for i, e in enumerate(t.edges[:limit]):
        P = np.asarray(e, float) / scale
        if len(P) == 0:
            continue
        L = float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1))) if len(P) > 1 else 0.0
        out.append({"edge": i, "start": [_r(x, 5) for x in P[0]], "end": [_r(x, 5) for x in P[-1]],
                    "mid": [_r(x, 5) for x in P[len(P) // 2]], "length": _r(L, 5)})
    return out


def _vertex_list(t, scale: float, limit: int) -> list[dict]:
    """Vertices in the order of the Vertices property of Fillet and Chamfer on sheets."""
    V = np.asarray(t.vertices, float).reshape(-1, 3) / scale
    return [{"vertex": i, "position": [_r(x, 5) for x in v]} for i, v in enumerate(V[:limit])]


def geometry_report(proj: Project, des: Design, names: list[str] | None, max_faces: int = 60,
                    edges: bool = False) -> dict:
    tess, errors = tessellate(proj, des, names)
    sc = _unit(des)
    objs = {}
    for n, t in tess.items():
        bb = np.asarray(t.bbox, float) / sc
        d = {"dim": t.dim, "bbox_min": [_r(x, 5) for x in bb[:3]], "bbox_max": [_r(x, 5) for x in bb[3:]],
             "faces_total": len(t.face_area)}
        if t.dim == 3:
            d["volume"] = _r(t.volume / sc ** 3, 5)
        d["faces"] = _face_list(t, sc, max_faces)
        if len(t.face_area) > max_faces:
            d["note"] = f"only the first {max_faces} faces listed; raise max_faces for more"
        if edges:
            d["edges"] = _edge_list(t, sc, max_faces * 3)
            d["vertices"] = _vertex_list(t, sc, max_faces * 3)
            if len(t.edges) > max_faces * 3:
                d["edges_note"] = f"only the first {max_faces * 3} edges and vertices listed; raise max_faces"
        objs[n] = d
    return {"units": des.units, "objects": objs, "errors": errors}


# ------------------------------------------------------------------------------------------------
# tools: project
# ------------------------------------------------------------------------------------------------
def t_get_context(args):
    wins = []
    for link, hello in live.windows():
        wins.append({k: hello.get(k) for k in ("path", "name", "design", "designs", "dirty", "solving", "queue")})
    return {"hertzon_windows_with_live_link": wins, "session_project": S.default_path,
            "mode": "window" if window_mode() else "file",
            "hint": ("Every project is worked on in a Hertzon window: create_project / open_project open it "
                     "there (a window is started if none is open), changes appear at once and analyses run "
                     "in the window's solve queue." if window_mode() else
                     "File mode: the tools work on .hzn files and run the solver in the background.")}


def t_create_project(args):
    path = _abspath(args["path"])
    if not path.lower().endswith(".hzn"):
        path += ".hzn"
    if Path(path).exists() and not args.get("overwrite"):
        raise ToolError(f"{path} exists; pass overwrite=true or choose another path")
    p = Project(args.get("name") or Path(path).stem)
    d = p.designs[0]
    d.name = args.get("design") or "Design1"
    d.units = args.get("units") or "mm"
    if d.units not in LENGTH_UNITS:
        raise ToolError(f"Unknown units '{d.units}'; use one of {sorted(LENGTH_UNITS)}")
    if args.get("solution_type"):
        d.settings["SolutionType"] = args["solution_type"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    p.save(path)
    S.default_path = path
    note = ""
    if window_mode():
        _window_with(path)
        note = "opened in a Hertzon window"
    elif args.get("open_in_gui", False):
        note = _open_in_window(path)
    return {"project": path, "design": d.name, "units": d.units, "window": note}


def _open_in_window(path: str) -> str:
    wins = live.windows()
    if not wins:
        return "no Hertzon window with the live link; working on the file"
    for link, hello in wins:
        if live.same_path(hello.get("path"), path):
            return "already open"
    link, hello = wins[0]
    if hello.get("dirty"):
        return ("not opened: the Hertzon window has unsaved changes in another project; working on the file. "
                "Ask the user to save there, then call open_project with open_in_gui=true.")
    try:
        link.call("open_project", path=path)
    except live.LinkError as e:
        return f"not opened: {e}"
    return "opened in Hertzon"


def t_open_project(args):
    path = _abspath(args["path"])
    if not Path(path).exists():
        raise ToolError(f"Project file not found: {path}")
    S.default_path = path
    note = _open_in_window(path) if args.get("open_in_gui", False) and not window_mode() else ""
    ctx = load({"project": path, "design": args.get("design")})
    if ctx.gui is not None:
        note = "open in a Hertzon window"
    out = summary(ctx)
    if note:
        out["window"] = note
    return out


def t_get_project(args):
    return summary(load(args))


def t_save_project(args):
    ctx = load(args)
    if ctx.gui is not None:
        r = ctx.gui.call("save", path=_abspath(args["path"]) if args.get("path") else None)
        return {"saved": r}
    if args.get("path"):
        newp = _abspath(args["path"])
        ctx.proj.save(newp)
        S.default_path = newp
        return {"saved": newp}
    ctx.proj.save(ctx.proj.path)
    return {"saved": ctx.proj.path}


def t_get_schema(args):
    from ..model.help import help_for
    sec = args.get("section", "")
    tables = {
        "geometry": {k: v for k, v in schema.GEOMETRY.items() if k in schema.CREATE_COMMANDS},
        "operations": {k: v for k, v in schema.GEOMETRY.items() if k not in schema.CREATE_COMMANDS},
        "boundaries": schema.BOUNDARIES, "excitations": schema.EXCITATIONS,
        "mesh_operations": schema.MESH_OPS,
        "setup": {"Setup": schema.SETUP}, "eigen_setup": {"Eigen Setup": schema.EIGEN_SETUP},
        "sweep": {"Sweep": schema.SWEEP}, "design_settings": {"Design": schema.DESIGN_SETTINGS},
        "far_field_sphere": {"Infinite Sphere": schema.INFINITE_SPHERE},
        "coordinate_system": {"Relative CS": schema.COORDINATE_SYSTEM},
        "parametric": {"Parametric": schema.PARAMETRIC}, "optimization": {"Optimization": schema.OPTIMIZATION},
    }
    if sec not in tables:
        raise ToolError(f"section must be one of {sorted(tables)}")
    kind = args.get("kind")
    tab = tables[sec]
    if kind is None and sec in ("geometry", "operations", "boundaries", "excitations", "mesh_operations"):
        return {"section": sec, "kinds": list(tab), "hint": "pass kind=... for the properties of one kind"}
    kinds = [kind] if kind else list(tab)
    out = {}
    for k in kinds:
        if k not in tab:
            raise ToolError(f"Unknown kind '{k}' in {sec}; kinds: {list(tab)}")
        props = []
        for p in tab[k]:
            d = {"name": p.name, "type": p.type, "default": p.default}
            if p.choices:
                d["choices"] = list(p.choices)
            h = help_for(p, k)
            if h:
                d["help"] = h
            if p.visible_if:
                d["used_if"] = f"{p.visible_if[0]} {'!=' if p.visible_if[1].startswith('!') else '=='} " \
                               f"{p.visible_if[1].lstrip('!')}"
            props.append(d)
        out[k] = props
        if sec == "parametric":
            out["sweep_types"] = list(schema.PARAMETRIC_SWEEP_TYPES)
            out["calc_functions"] = list(schema.CALC_FUNCTIONS)
        if sec == "optimization":
            out["goal_conditions"] = list(schema.GOAL_CONDITIONS)
            out["cost_functions"] = list(schema.COST_FUNCTIONS)
        if sec == "boundaries" and k in schema.UNSUPPORTED_BOUNDARIES:
            out[k + " (note)"] = schema.UNSUPPORTED_BOUNDARIES[k]
    return out


def t_list_materials(args):
    try:
        ctx = load(args)
        mats = ctx.proj.all_materials()
    except ToolError:
        from ..model.materials import library_materials
        mats = library_materials()
    flt = (args.get("filter") or "").lower()
    out = []
    for m in mats:
        if flt and flt not in m.name.lower() and flt not in m.description.lower():
            continue
        d = {"name": m.name, "eps_r": m.permittivity, "mu_r": m.permeability, "sigma": m.conductivity,
             "tan_delta": m.loss_tangent}
        if m.pec:
            d["pec"] = True
        if m.library != "System":
            d["library"] = m.library
        out.append(d)
    return {"materials": out}


def t_add_material(args):
    from ..model.materials import Material
    ctx = load(args)
    name = args["name"]
    fields = {"permittivity": "eps_r", "permeability": "mu_r", "conductivity": "sigma",
              "loss_tangent": "tan_delta"}
    m = next((x for x in ctx.proj.materials if x.name == name), None) or Material(name)
    for f, a in fields.items():
        if a in args:
            setattr(m, f, _s(args[a]))
    if "description" in args:
        m.description = args["description"]
    if m not in ctx.proj.materials:
        ctx.proj.materials.append(m)
    commit(ctx, f"material {name}")
    return {"material": name}


def t_set_design(args):
    ctx = load(args)
    d, p = ctx.des, ctx.proj
    if args.get("new_design"):
        nd = Design(name=args["new_design"], units=args.get("units") or d.units)
        if any(x.name == nd.name for x in p.designs):
            raise ToolError(f"Design '{nd.name}' exists")
        p.designs.append(nd)
        ctx.des = d = nd
    if args.get("units"):
        if args["units"] not in LENGTH_UNITS:
            raise ToolError(f"Unknown units; use one of {sorted(LENGTH_UNITS)}")
        d.units = args["units"]
    if args.get("rename"):
        d.name = args["rename"]
    if args.get("settings"):
        d.settings = _props_in("design settings", schema.DESIGN_SETTINGS, args["settings"], d.settings)
    for key, target in (("variables", d.variables), ("project_variables", p.variables)):
        for k, v in (args.get(key) or {}).items():
            if v is None:
                target.pop(k, None)
            else:
                target[k] = _s(v)
    ev = d.evaluator(p)
    bad = {k: ev.check(v) for k, v in {**p.variables, **d.variables}.items() if ev.check(v)}
    if "notes" in args:
        d.notes = args["notes"]
    commit(ctx, "design settings")
    return {"design": d.name, "units": d.units, "settings": d.settings, "variables": d.variables,
            "project_variables": p.variables, **({"variable_errors": bad} if bad else {})}


# ------------------------------------------------------------------------------------------------
# tools: geometry
# ------------------------------------------------------------------------------------------------
_ATTRS = {"color": "color", "transparency": "transparency", "solve_inside": "solve_inside", "model": "model",
          "group": "group", "wireframe": "wireframe", "visible": "visible", "orientation": "orientation"}


def _apply_attrs(o, a: dict | None, d: Design) -> None:
    for k, v in (a or {}).items():
        if k not in _ATTRS:
            raise ToolError(f"Unknown attribute '{k}'; use {sorted(_ATTRS)}")
        if k in ("solve_inside", "model", "wireframe", "visible"):
            v = bool(v) if not isinstance(v, str) else v == "True"
        elif k == "transparency":
            v = float(v)
        elif k == "orientation":
            if v not in d.cs_names():
                raise ToolError(f"Unknown coordinate system '{v}'; coordinate systems: {d.cs_names()}")
        elif k == "color":
            c = parse_color(v)
            if c is None:
                raise ToolError(f"Unreadable color '{v}'; use '#rrggbb' or 'r,g,b' (0-255)")
            v = c
        setattr(o, _ATTRS[k], v)


def _append_operation(d: Design, obj: str, kind: str, props: dict | None) -> None:
    if kind not in schema.GEOMETRY or kind in schema.CREATE_COMMANDS:
        raise ToolError(f"Unknown operation '{kind}'. Operations: "
                        f"{[k for k in schema.GEOMETRY if k not in schema.CREATE_COMMANDS]}")
    o = d.obj(obj)
    p = _props_in(kind, schema.GEOMETRY[kind], props)
    tools = [t.strip() for t in p.get("Tools", "").split(",") if t.strip()]
    _check_objects(d, tools)
    if obj in tools:
        raise ToolError("An object cannot be its own tool")
    keep = p.get("KeepOriginals", "False") == "True"
    if tools and kind in ("Unite", "Subtract", "Intersect") and keep:
        clones = []
        import copy
        for t in tools:
            c = copy.deepcopy(d.obj(t))
            c.name = d.unique_name(t + "_tool")
            c.consumed_by = obj
            d.objects.append(c)
            clones.append(c.name)
        p["Tools"] = ",".join(clones)
    elif tools:
        for t in tools:
            d.obj(t).consumed_by = obj
        _drop_refs(d, tools)
    o.history.append(Command(kind, p))


def _drop_refs(d: Design, objs) -> list[str]:
    objs = set(objs)
    removed = []
    for lst in (d.boundaries, d.excitations, d.mesh_ops):
        for a in list(lst):
            a.objects = [x for x in a.objects if x not in objs]
            a.faces = [f for f in a.faces if f.obj not in objs]
            if not a.objects and not a.faces and a.kind != "Incident Plane Wave":
                lst.remove(a)
                removed.append(a.name)
    return removed


def _check_built(ctx: Ctx, names: list[str], max_faces: int = 30) -> dict:
    try:
        rep = geometry_report(ctx.proj, ctx.des, names, max_faces)
    except Exception as e:  # noqa: BLE001
        return {"geometry_check": f"not built: {e}"}
    return {"geometry": rep["objects"], **({"errors": rep["errors"]} if rep["errors"] else {})}


def t_add_object(args):
    ctx = load(args)
    d = ctx.des
    kind = args["kind"]
    if kind not in schema.CREATE_COMMANDS or kind == "Import":
        raise ToolError(f"Unknown create command '{kind}'. Use one of "
                        f"{[k for k in schema.CREATE_COMMANDS if k != 'Import']} (import_geometry for CAD files)")
    props = _props_in(kind, schema.GEOMETRY[kind], args.get("props"))
    mat = args.get("material") or ("air" if kind == "CreateRegion" else "vacuum")
    try:
        ctx.proj.material(mat)
    except KeyError:
        raise ToolError(f"Unknown material '{mat}' (list_materials)") from None
    name = args.get("name")
    if name and d.has_obj(name):
        raise ToolError(f"An object named '{name}' exists")
    o = d.add_object(kind, name=name, material=mat)
    o.history[0].props = props
    o.orientation = d.working_cs               # as in the window: new objects are made in the working CS
    _apply_attrs(o, args.get("attributes"), d)
    for op in args.get("operations") or []:
        _append_operation(d, o.name, op["kind"], op.get("props"))
    errors = commit(ctx, f"add {o.name}")
    out = {"name": o.name}
    if args.get("check", True):
        out.update(_check_built(ctx, [o.name]))
    if errors.get(o.name):
        out["error"] = errors[o.name]
    return out


def t_import_geometry(args):
    import gmsh
    from ..kernel import occ as K
    ctx = load(args)
    d = ctx.des
    f = _abspath(args["file"])
    if not Path(f).exists():
        raise ToolError(f"File not found: {f}")
    K.ensure_gmsh("hertzon_mcp_probe")
    gmsh.model.remove()
    gmsh.model.add("hertzon_mcp_probe")
    try:
        if f.lower().endswith(".stl"):
            bodies = K.import_stl(f, _unit(d), count_only=True)
        else:
            bodies = K.import_cad(f, _unit(d))
        gmsh.model.occ.synchronize()
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Import failed: {e}") from e
    base = args.get("name") or Path(f).stem
    mat = args.get("material") or "vacuum"
    names = []
    for i, (dim, _) in enumerate(bodies):
        o = d.add_object("Import", name=d.unique_name(f"{base}_{i + 1}"), material=mat, File=f, Body=str(i))
        o.history[0].props["Dim"] = str(dim)
        o.color = "#a0a0c0"
        names.append(o.name)
    commit(ctx, f"import {Path(f).name}")
    out = {"objects": names}
    if args.get("check", True):
        out.update(_check_built(ctx, names, 20))
    return out


def t_modify_object(args):
    ctx = load(args)
    d = ctx.des
    name = args["name"]
    if not d.has_obj(name):
        raise ToolError(f"Object '{name}' does not exist")
    o = d.obj(name)
    i = int(args.get("history_index", 0))
    if args.get("props"):
        if not 0 <= i < len(o.history):
            raise ToolError(f"history_index out of range (0..{len(o.history) - 1})")
        c = o.history[i]
        c.props = _props_in(c.kind, schema.GEOMETRY.get(c.kind, []), args["props"], c.props)
    if "suppress" in args:
        if i == 0:
            raise ToolError("The creation command cannot be suppressed")
        o.history[i].suppressed = bool(args["suppress"])
    if args.get("remove_operation"):
        if i == 0:
            raise ToolError("history_index 0 is the creation command; delete the object instead")
        c = o.history.pop(i)
        for t in [x.strip() for x in c.props.get("Tools", "").split(",") if x.strip()]:
            if d.has_obj(t) and d.obj(t).consumed_by == name:
                d.obj(t).consumed_by = None
    if args.get("material"):
        try:
            ctx.proj.material(args["material"])
        except KeyError:
            raise ToolError(f"Unknown material '{args['material']}'") from None
        o.material = args["material"]
    _apply_attrs(o, args.get("attributes"), d)
    if args.get("new_name"):
        new = args["new_name"]
        if d.has_obj(new):
            raise ToolError(f"'{new}' exists")
        _rename(d, name, new)
        name = new
    commit(ctx, f"modify {name}")
    out = {"name": name}
    if args.get("check", True):
        out.update(_check_built(ctx, [name]))
    return out


def _rename(d: Design, old: str, new: str) -> None:
    d.obj(old).name = new
    for o in d.objects:
        if o.consumed_by == old:
            o.consumed_by = new
        for c in o.history:
            if "Tools" in c.props:
                c.props["Tools"] = ",".join(new if t.strip() == old else t.strip()
                                            for t in c.props["Tools"].split(",") if t.strip())
    for lst in (d.boundaries, d.excitations, d.mesh_ops):
        for a in lst:
            a.objects = [new if x == old else x for x in a.objects]
            for f in a.faces + a.edges:
                if f.obj == old:
                    f.obj = new


def t_add_operation(args):
    ctx = load(args)
    d = ctx.des
    objs = args["objects"] if isinstance(args.get("objects"), list) else [args["object"]]
    _check_objects(d, objs)
    for o in objs:
        _append_operation(d, o, args["kind"], args.get("props"))
    errors = commit(ctx, f"{args['kind']} on {', '.join(objs)}")
    out = {"objects": objs, "operation": args["kind"]}
    if args.get("check", True):
        out.update(_check_built(ctx, objs))
    bad = {k: v for k, v in errors.items() if k in objs}
    if bad:
        out["errors"] = bad
    return out


def t_delete(args):
    ctx = load(args)
    d = ctx.des
    done, missing = [], []
    for n in args["names"]:
        if d.has_obj(n):
            gone = [n] + [o.name for o in d.objects if o.consumed_by == n]
            d.objects = [o for o in d.objects if o.name not in gone]
            for o in d.objects:
                for c in o.history:
                    if "Tools" in c.props:
                        c.props["Tools"] = ",".join(t.strip() for t in c.props["Tools"].split(",")
                                                    if t.strip() and t.strip() not in gone)
            dropped = _drop_refs(d, gone)
            done.append(n + (f" (also removed: {', '.join(dropped)})" if dropped else ""))
            continue
        hit = False
        if any(c.name == n for c in d.coord_systems):
            users = [o.name for o in d.objects if o.orientation == n] + \
                    [c.name for c in d.coord_systems if c.reference == n]
            if users:
                raise ToolError(f"Coordinate system '{n}' is used by {', '.join(users)}")
            if d.working_cs == n:
                d.working_cs = "Global"
        for attr in ("boundaries", "excitations", "mesh_ops", "setups", "spheres", "reports", "field_plots",
                     "coord_systems", "field_exprs", "parametrics", "optimetrics"):
            lst = getattr(d, attr)
            if any(x.name == n for x in lst):
                setattr(d, attr, [x for x in lst if x.name != n])
                hit = True
        fn = n.removesuffix("()")
        if any(_fname(f) == fn for f in d.field_funcs):
            d.field_funcs = [f for f in d.field_funcs if _fname(f) != fn]
            hit = True
        for s in d.setups:
            if any(w.name == n for w in s.sweeps) and args.get("setup") in (None, s.name):
                s.sweeps = [w for w in s.sweeps if w.name != n]
                hit = True
        (done if hit else missing).append(n)
    commit(ctx, "delete " + ", ".join(args["names"]))
    return {"deleted": done, **({"not_found": missing} if missing else {})}


def t_get_geometry(args):
    ctx = load(args)
    names = args.get("objects")
    if names:
        _check_objects(ctx.des, names)
    return geometry_report(ctx.proj, ctx.des, names, int(args.get("max_faces", 60)),
                           bool(args.get("include_edges")))


# ------------------------------------------------------------------------------------------------
# tools: boundaries, excitations, mesh operations
# ------------------------------------------------------------------------------------------------
_CATS = {"boundary": ("boundaries", schema.BOUNDARIES), "excitation": ("excitations", schema.EXCITATIONS),
         "mesh_operation": ("mesh_ops", schema.MESH_OPS)}


def t_assign(args):
    ctx = load(args)
    d = ctx.des
    cat = args["category"]
    if cat not in _CATS:
        raise ToolError(f"category must be one of {sorted(_CATS)}")
    attr, table = _CATS[cat]
    kind = args["kind"]
    if kind not in table:
        raise ToolError(f"Unknown {cat} kind '{kind}'; kinds: {list(table)}")
    objs = list(args.get("objects") or [])
    faces = _faces(args.get("faces"))
    edges = [FaceRef(str(e["object"]), int(e["edge"])) for e in args.get("edges") or []]
    _check_objects(d, objs + [f.obj for f in faces] + [e.obj for e in edges])
    for f in faces:
        if d.obj(f.obj).consumed_by:
            raise ToolError(f"'{f.obj}' was consumed by '{d.obj(f.obj).consumed_by}'; use the faces of that object")
    if not (objs or faces or edges) and kind != "Incident Plane Wave":
        raise ToolError("Give objects, faces or edges to assign to")
    props = _props_in(kind, table[kind], args.get("props"))
    if kind in schema.FACE_EXCITATIONS and "PortNumber" not in (args.get("props") or {}):
        props["PortNumber"] = str(d.next_port_number())
    lst = getattr(d, attr)
    name = args.get("name")
    if name and any(a.name == name for a in lst):
        if not args.get("replace"):
            raise ToolError(f"'{name}' exists; pass replace=true to redefine it")
        setattr(d, attr, [a for a in lst if a.name != name])
        lst = getattr(d, attr)
    if not name:
        base = {"Wave Port": "Port", "Lumped Port": "Port", "Floquet Port": "FloquetPort",
                "Incident Plane Wave": "IncPWave"}.get(kind, kind.replace(" ", ""))
        name = props["PortNumber"] if kind in schema.FACE_EXCITATIONS else d.unique_name(base + "1")
        if kind in schema.FACE_EXCITATIONS and name in d.all_names():
            name = d.unique_name(base + name)
    a = Assignment(name, kind, props, faces=faces, objects=objs, edges=edges)
    lst.append(a)
    commit(ctx, f"assign {kind} {name}")
    out = {"name": name, "kind": kind}
    if kind in schema.FACE_EXCITATIONS:
        out["port_number"] = props["PortNumber"]
    if cat == "boundary" and kind in schema.UNSUPPORTED_BOUNDARIES:
        out["warning"] = schema.UNSUPPORTED_BOUNDARIES[kind]
    return out


def t_modify_assignment(args):
    ctx = load(args)
    d = ctx.des
    try:
        a = d.assignment(args["name"])
    except KeyError:
        raise ToolError(f"No boundary, excitation or mesh operation named '{args['name']}'") from None
    table = {**schema.BOUNDARIES, **schema.EXCITATIONS, **schema.MESH_OPS}
    if args.get("props"):
        a.props = _props_in(a.kind, table.get(a.kind, []), args["props"], a.props)
    if "objects" in args:
        _check_objects(d, args["objects"])
        a.objects = list(args["objects"])
    if "faces" in args:
        a.faces = _faces(args["faces"])
        _check_objects(d, [f.obj for f in a.faces])
    if "enabled" in args:
        a.enabled = bool(args["enabled"])
    if args.get("new_name"):
        if args["new_name"] in d.all_names():
            raise ToolError(f"'{args['new_name']}' exists")
        a.name = args["new_name"]
    commit(ctx, f"modify {a.name}")
    return _assign_summary(a)


# ------------------------------------------------------------------------------------------------
# tools: analysis setup
# ------------------------------------------------------------------------------------------------
def t_set_setup(args):
    ctx = load(args)
    d = ctx.des
    name = args.get("name") or "Setup1"
    s = next((x for x in d.setups if x.name == name), None)
    created = s is None
    if created:
        eigen = bool(args.get("eigen")) or d.settings.get("SolutionType") == "Eigenmode"
        s = Setup(name, eigen=eigen)
        if eigen:
            s.props = schema.defaults(schema.EIGEN_SETUP)
        d.setups.append(s)
    plist = schema.EIGEN_SETUP if s.eigen else schema.SETUP
    s.props = _props_in("Setup", plist + [schema.P("_BasisNames", "text")], args.get("props"), s.props)
    for sw in args.get("sweeps") or []:
        _set_sweep(s, sw.get("name") or "Sweep", sw.get("props"))
    commit(ctx, f"setup {name}")
    return {"setup": name, "created": created, "props": s.props,
            "sweeps": [{"name": w.name, "props": w.props} for w in s.sweeps]}


def _set_sweep(s: Setup, name: str, props: dict | None) -> Sweep:
    w = next((x for x in s.sweeps if x.name == name), None)
    if w is None:
        w = Sweep(name)
        s.sweeps.append(w)
    w.props = _props_in("Sweep", schema.SWEEP, props, w.props)
    return w


def t_set_sweep(args):
    ctx = load(args)
    try:
        s = ctx.des.setup(args.get("setup") or "Setup1")
    except KeyError:
        raise ToolError(f"Setup '{args.get('setup')}' not found") from None
    w = _set_sweep(s, args.get("name") or "Sweep", args.get("props"))
    commit(ctx, f"sweep {w.name}")
    return {"setup": s.name, "sweep": w.name, "props": w.props}


def t_set_far_field_sphere(args):
    ctx = load(args)
    d = ctx.des
    name = args.get("name") or "Infinite Sphere1"
    sp = next((x for x in d.spheres if x.name == name), None)
    if sp is None:
        sp = InfiniteSphere(name)
        d.spheres.append(sp)
    sp.props = _props_in("Infinite Sphere", schema.INFINITE_SPHERE, args.get("props"), sp.props)
    commit(ctx, f"far field {name}")
    return {"name": name, "props": sp.props}


# ------------------------------------------------------------------------------------------------
# tools: coordinate systems
# ------------------------------------------------------------------------------------------------
def t_set_coordinate_system(args):
    ctx = load(args)
    d = ctx.des
    name = args.get("name") or d.unique_name("RelativeCS1", set(d.cs_names()) | d.all_names())
    if name == "Global":
        if not args.get("set_working") or args.get("props") or args.get("reference"):
            raise ToolError("The Global coordinate system cannot be changed; set_working=true works in it again")
        d.working_cs = "Global"
        commit(ctx, "working coordinate system Global")
        return {"working_cs": "Global"}
    cs = next((c for c in d.coord_systems if c.name == name), None)
    created = cs is None
    ref = args.get("reference")
    if ref is not None and ref not in d.cs_names():
        raise ToolError(f"Unknown reference '{ref}'; coordinate systems: {d.cs_names()}")
    if ref == name:
        raise ToolError("A coordinate system cannot be its own reference")
    if created:
        if name in d.all_names():
            raise ToolError(f"'{name}' exists")
        cs = CoordSys(name, "Relative", ref or d.working_cs)
        d.coord_systems.append(cs)
    elif ref is not None:
        cs.reference = ref
    cs.props = _props_in("Coordinate System", schema.COORDINATE_SYSTEM, args.get("props"), cs.props)
    if args.get("set_working", created):
        d.working_cs = name
    commit(ctx, f"coordinate system {name}")
    return {"name": name, "created": created, "kind": cs.kind, "reference": cs.reference, "props": cs.props,
            "working_cs": d.working_cs}


# ------------------------------------------------------------------------------------------------
# tools: equations (Fields Calculator)
# ------------------------------------------------------------------------------------------------
def _fname(text: str) -> str:
    return text.split("(", 1)[0].strip()


def _check_equation(name: str, expr: str) -> None:
    from ..post.calc import lang as L
    if not name.isidentifier():
        raise ToolError(f"Equation name '{name}' must be a name (letters, digits, _), e.g. P_loss")
    try:
        L.parse(expr)
    except L.CalcError as e:
        raise ToolError(f"{name}: {e}") from None


def _check_function(text: str) -> None:
    from ..post.calc import lang as L
    try:
        L.parse_function(text)
    except L.CalcError as e:
        raise ToolError(f"{text}: {e}") from None


def _equations_summary(d: Design) -> dict:
    return {"equations": [{"name": e.name, "expr": e.expr, **({"description": e.description} if e.description else {})}
                          for e in d.field_exprs], "functions": list(d.field_funcs)}


def t_set_equation(args):
    ctx = load(args)
    d = ctx.des
    eqs = list(args.get("equations") or [])
    if args.get("name"):
        eqs.append({"name": args["name"], "expr": args.get("expr", ""), "description": args.get("description")})
    funcs = [str(f) for f in args.get("functions") or []]
    if args.get("import_file"):
        f = _abspath(args["import_file"])
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
            eqs += [{"name": str(e["name"]), "expr": str(e["expr"]), "description": str(e.get("description", ""))}
                    for e in data.get("equations", [])]
            funcs += [str(x) for x in data.get("functions", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            raise ToolError(f"{f} is not a file of equations: {e}") from None
    replace = args.get("replace", True)
    changed, kept = [], []
    for e in eqs:
        name, expr = str(e.get("name", "")).strip(), str(e.get("expr", "")).strip()
        if "=" in expr and not name:                       # "P_in = flux(Port1, re(S))"
            name, _, expr = (x.strip() for x in expr.partition("="))
        _check_equation(name, expr)
        old = next((x for x in d.field_exprs if x.name == name), None)
        if old is None:
            if name in d.all_names() or name in {_fname(x) for x in d.field_funcs}:
                raise ToolError(f"'{name}' is already used in the design")
            d.field_exprs.append(FieldExpression(name, expr, str(e.get("description") or "")))
        elif replace:
            old.expr = expr
            if e.get("description") is not None:
                old.description = str(e["description"])
        else:
            kept.append(name)
            continue
        changed.append(name)
    for f in funcs:
        _check_function(f)
        n = _fname(f)
        if n in {_fname(x) for x in d.field_funcs}:
            if not replace:
                kept.append(n + "()")
                continue
            d.field_funcs = [x for x in d.field_funcs if _fname(x) != n]
        d.field_funcs.append(f)
        changed.append(n + "()")
    if changed:
        commit(ctx, "equations " + ", ".join(changed))
    out = {"changed": changed, **({"kept_existing": kept} if kept else {})}
    if args.get("export_file"):
        f = _abspath(args["export_file"])
        data = {"format": "hertzon-equations", "version": 1,
                "equations": [{"name": e.name, "expr": e.expr, "description": e.description} for e in d.field_exprs],
                "functions": list(d.field_funcs)}
        try:
            Path(f).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            raise ToolError(f"Cannot write {f}: {e}") from None
        out["exported"] = f
    return {**out, **_equations_summary(d)}


# ------------------------------------------------------------------------------------------------
# tools: reports and field plots
# ------------------------------------------------------------------------------------------------
REPORT_KINDS = {"Rectangular": "XY Plot", "Smith Chart": "Smith Chart", "Data Table": "Data Table",
                "Radiation Pattern": "Radiation Pattern", "3D Polar Plot": "3D Polar Plot",
                "Near Field Line": "Near Field", "Monostatic RCS": "Monostatic RCS",
                "Monostatic RCS Table": "Monostatic RCS Table", "Antenna Parameters": "Antenna Parameters Plot",
                "Antenna Parameters Table": "Antenna Parameters Table"}


def _solution(d: Design, sol: str | None, fallback_sweep: bool = True) -> str:
    """'Setup1 : Sweep' (or 'Setup1 : LastAdaptive'); default: the first sweep of the first setup."""
    if not sol:
        if not d.setups:
            raise ToolError("The design has no analysis setup (set_setup)")
        s = d.setups[0]
        return f"{s.name} : {s.sweeps[0].name}" if fallback_sweep and s.sweeps else f"{s.name} : LastAdaptive"
    setup, _, label = (x.strip() for x in sol.partition(":"))
    if not any(s.name == setup for s in d.setups) or not label:
        raise ToolError(f"Solution '{sol}' must be 'Setup : Sweep' or 'Setup : LastAdaptive' of an existing setup")
    return f"{setup} : {label}"


def _next_name(taken, prefix: str, sep: str = " ") -> str:
    i = 1
    while f"{prefix}{sep}{i}" in taken:
        i += 1
    return f"{prefix}{sep}{i}"


def t_set_report(args):
    ctx = load(args)
    d = ctx.des
    name = args.get("name")
    r = next((x for x in d.reports if x.name == name), None) if name else None
    created = r is None
    fields = bool(args.get("fields"))
    if created:
        kind = args.get("kind") or "Rectangular"
        if kind not in REPORT_KINDS:
            raise ToolError(f"kind must be one of {list(REPORT_KINDS)}")
        if fields and kind not in ("Rectangular", "Data Table"):
            raise ToolError("A Fields report is a Rectangular plot or a Data Table")
        prefix = ("Fields Plot" if kind == "Rectangular" else "Fields Table") if fields else REPORT_KINDS[kind]
        name = name or _next_name({x.name for x in d.reports}, prefix)
        sol = _solution(d, args.get("solution"))
        r = Report(name, kind, sol, [])
        if fields:
            r.props.update(fields="1", port="1")
        d.reports.append(r)
    elif args.get("kind") and args["kind"] != r.kind:
        raise ToolError(f"'{r.name}' is a {r.kind} report; delete it and create a new one to change the kind")
    if args.get("solution"):
        r.solution = _solution(d, args["solution"])
    if "traces" in args:
        r.traces = [str(t) for t in args["traces"]]
    elif created and not fields:
        n = max(1, len(d.ports()))
        r.traces = ["dB(GainTotal)"] if r.kind in ("Radiation Pattern", "3D Polar Plot") else \
            ["dBsm(MonostaticRCSTotal)"] if r.kind.startswith("Monostatic") else \
            ["dB(PeakGain)", "dB(PeakRealizedGain)", "dB(PeakDirectivity)"] if r.kind.startswith("Antenna") else \
            ["mag(NearETotal)"] if r.kind == "Near Field Line" else [f"dB(S({i},1))" for i in range(1, n + 1)][:4]
    if created and fields and not r.traces:
        r.traces = [e.name for e in d.field_exprs][:1]
    if args.get("port") is not None:
        r.props["port"] = str(int(args["port"]))
    for k, v in (args.get("props") or {}).items():
        if v is None:
            r.props.pop(k, None)
        else:
            r.props[k] = _s(v)
    if args.get("new_name"):
        if any(x.name == args["new_name"] for x in d.reports):
            raise ToolError(f"A report '{args['new_name']}' exists")
        r.name = args["new_name"]
    commit(ctx, f"report {r.name}")
    return {"name": r.name, "created": created, "kind": r.kind, "solution": r.solution, "traces": r.traces,
            "props": r.props, "note": "Open the report in the Hertzon window to see it (it is evaluated there)."}


def t_set_field_plot(args):
    from ..gui.fields import ATTR_DEFAULTS, QUANTITIES
    ctx = load(args)
    d = ctx.des
    name = args.get("name")
    fp = next((x for x in d.field_plots if x.name == name), None) if name else None
    created = fp is None
    eqs = [e.name for e in d.field_exprs]
    q = args.get("quantity")
    if q is not None and q not in QUANTITIES and q not in eqs:
        raise ToolError(f"quantity must be one of {QUANTITIES} or an equation of the design {eqs}")
    if created:
        q = q or "Mag_E"
        name = name or _next_name({x.name for x in d.field_plots}, q, "")
        fp = FieldPlot(name, q, _solution(d, args.get("solution"), fallback_sweep=False))
        d.field_plots.append(fp)
    elif q:
        fp.quantity = q
    if args.get("solution"):
        fp.solution = _solution(d, args["solution"], fallback_sweep=False)
    if "objects" in args or "faces" in args or "plane" in args:
        objs = list(args.get("objects") or [])
        faces = _faces(args.get("faces"))
        _check_objects(d, objs + [f.obj for f in faces])
        plane = (args.get("plane") or "").strip()
        if plane:
            ax, _, pos = plane.partition("@")
            if ax.strip().upper() not in ("XY", "YZ", "XZ"):
                raise ToolError("plane is 'XY', 'YZ' or 'XZ', optionally with a position: 'XY@1.5mm'")
            plane = f"{ax.strip().upper()}@{pos.strip()}"
            objs, faces = [], []
        elif not objs and not faces:
            raise ToolError("Give objects, faces or a cut plane to plot on")
        fp.objects, fp.faces, fp.plane = objs, faces, plane
    elif created:
        fp.plane = "XY@"
    if "volume" in args:
        fp.props["Volume"] = str(bool(args["volume"]))
    for key, prop in (("port", "port"), ("freq_index", "freq"), ("phase_deg", "phase")):
        if args.get(key) is not None:
            fp.props[prop] = f"{float(args[key]):g}" if key == "phase_deg" else str(int(args[key]))
    for k, v in (args.get("attributes") or {}).items():
        if k not in ATTR_DEFAULTS:
            raise ToolError(f"Unknown plot attribute '{k}'; attributes: {sorted(ATTR_DEFAULTS)}")
        fp.props[k] = _s(v)
    if args.get("new_name"):
        if any(x.name == args["new_name"] for x in d.field_plots):
            raise ToolError(f"A field plot '{args['new_name']}' exists")
        fp.name = args["new_name"]
    commit(ctx, f"field plot {fp.name}")
    out = {"name": fp.name, "created": created, "quantity": fp.quantity, "solution": fp.solution,
           "props": fp.props, "note": "The plot is drawn in the Hertzon window (project tree > Field Overlays)."}
    if fp.plane:
        out["plane"] = fp.plane
    else:
        out["objects"], out["faces"] = fp.objects, [{"object": f.obj, "face": f.face} for f in fp.faces]
    return out


# ------------------------------------------------------------------------------------------------
# tools: parametric sweeps and optimizations
# ------------------------------------------------------------------------------------------------
_SWEEP_KEYS = ("variable", "type", "start", "stop", "step", "sync")
_CALC_KEYS = ("calc", "solution", "func", "start", "stop")
_VAR_KEYS = ("name", "include", "min", "max")
_GOAL_KEYS = ("calc", "start", "stop", "cond", "value", "weight")


def _rows(rows, keys: tuple, what: str) -> list[dict]:
    out = []
    for r in rows or []:
        bad = [k for k in r if k not in keys]
        if bad:
            raise ToolError(f"{what}: unknown keys {bad}; keys: {list(keys)}")
        out.append({k: (v if isinstance(v, bool) else _s(v)) for k, v in r.items()})
    return out


def _study_names(d: Design) -> set[str]:
    return {o.name for o in d.parametrics} | {o.name for o in d.optimetrics}


def t_set_parametric(args):
    from ..solver.parametric import sweepable, variation_table
    ctx = load(args)
    d = ctx.des
    name = args.get("name") or _next_name(_study_names(d), "ParametricSetup", "")
    ps = next((x for x in d.parametrics if x.name == name), None)
    created = ps is None
    if created:
        if name in _study_names(d):
            raise ToolError(f"'{name}' is an optimization")
        ps = ParametricSetup(name, setups=[s.name for s in d.setups if not s.eigen][:1])
        d.parametrics.append(ps)
    if "sweeps" in args:
        ps.sweeps = _rows(args["sweeps"], _SWEEP_KEYS, "sweeps")
        ok = sweepable(ctx.proj, d)
        for e in ps.sweeps:
            if e.get("variable") not in ok:
                raise ToolError(f"'{e.get('variable')}' is not a variable with a plain value; sweepable: {sorted(ok)}")
            if e.get("type", "Linear step") not in schema.PARAMETRIC_SWEEP_TYPES:
                raise ToolError(f"type must be one of {list(schema.PARAMETRIC_SWEEP_TYPES)}")
    if "setups" in args:
        bad = [s for s in args["setups"] if not any(x.name == s for x in d.setups)]
        if bad:
            raise ToolError(f"Unknown setups {bad}")
        ps.setups = list(args["setups"])
    if "calcs" in args:
        ps.calcs = _rows(args["calcs"], _CALC_KEYS, "calcs")
        for c in ps.calcs:
            if c.get("func", "Value") not in schema.CALC_FUNCTIONS:
                raise ToolError(f"func must be one of {list(schema.CALC_FUNCTIONS)}")
    ps.props = _props_in("Parametric", schema.PARAMETRIC, args.get("props"), ps.props)
    try:
        n = len(variation_table(ps, d.evaluator(ctx.proj)))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Sweep definitions: {e}") from None
    commit(ctx, f"parametric {name}")
    return {"name": name, "created": created, "sweeps": ps.sweeps, "setups": ps.setups, "calcs": ps.calcs,
            "props": ps.props, "variations": n,
            "next": "run_simulation(parametric=name), then get_results(parametric=name)"}


def t_set_optimization(args):
    from ..solver.optimetrics import Variables
    ctx = load(args)
    d = ctx.des
    name = args.get("name") or _next_name(_study_names(d), "Optimization", "")
    op = next((x for x in d.optimetrics if x.name == name), None)
    created = op is None
    if created:
        if name in _study_names(d):
            raise ToolError(f"'{name}' is a parametric setup")
        op = Optimization(name)
        d.optimetrics.append(op)
    if args.get("solution") or created:
        op.solution = _solution(d, args.get("solution"))
    if "variables" in args:
        op.variables = _rows(args["variables"], _VAR_KEYS, "variables")
        for v in op.variables:
            v.setdefault("include", True)
    if "goals" in args:
        op.goals = _rows(args["goals"], _GOAL_KEYS, "goals")
        for g in op.goals:
            if g.get("cond", "<=") not in schema.GOAL_CONDITIONS:
                raise ToolError(f"cond must be one of {list(schema.GOAL_CONDITIONS)}")
            g.setdefault("cond", "<=")
            g.setdefault("weight", "1")
    op.props = _props_in("Optimization", schema.OPTIMIZATION, args.get("props"), op.props)
    try:
        Variables(d, op, d.evaluator(ctx.proj))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Variables: {e}") from None
    commit(ctx, f"optimization {name}")
    return {"name": name, "created": created, "solution": op.solution, "variables": op.variables,
            "goals": op.goals, "props": op.props,
            "next": "run_simulation(optimization=name), then get_results(optimization=name)"}


def t_validate(args):
    from ..model.validate import validate
    ctx = load(args)
    tess, errors = tessellate(ctx.proj, ctx.des)
    checks = validate(ctx.proj, ctx.des, tess, errors)
    out = [{"category": c.category, "level": c.level, "message": c.message} for c in checks]
    return {"ok": not any(c.level == "error" for c in checks), "checks": out}


# ------------------------------------------------------------------------------------------------
# tools: solving
# ------------------------------------------------------------------------------------------------
def _out_dir(ctx: Ctx, setup: str) -> Path:
    from ..solver.results import results_dir
    return results_dir(ctx.proj.path, ctx.des.name, setup)


def _job_key(ctx: Ctx, setup: str, sweep: str = "") -> str:
    return f"{Path(ctx.proj.path).stem} - {ctx.des.name} - {setup}" + (f" : {sweep}" if sweep else "")


def _study(ctx: Ctx, args) -> tuple[str, str] | None:
    """(name, 'parametric' | 'optimization') when the call is about a parametric setup or an optimization."""
    for kind, attr in (("parametric", "parametrics"), ("optimization", "optimetrics")):
        name = args.get(kind)
        if name:
            if not any(o.name == name for o in getattr(ctx.des, attr)):
                raise ToolError(f"No {kind} named '{name}'; {kind}s: {[o.name for o in getattr(ctx.des, attr)]}")
            return name, kind
    return None


def _study_dir(ctx: Ctx, name: str) -> Path:
    from ..solver.results import _safe, studies_dir
    return studies_dir(ctx.proj.path, ctx.des.name) / _safe(name)


def _run_study(ctx: Ctx, name: str, kind: str, args) -> dict:
    """Solve the variations of a parametric setup or run an optimization (window queue or background)."""
    from ..model.validate import validate
    d = ctx.des
    ev = d.evaluator(ctx.proj)
    try:
        if kind == "parametric":
            from ..solver.parametric import variation_table
            ps = next(o for o in d.parametrics if o.name == name)
            if not variation_table(ps, ev):
                raise ValueError("Add a sweep definition first (set_parametric sweeps)")
            if not any(any(s.name == x for s in d.setups) for x in ps.setups):
                raise ValueError("Choose a setup to simulate (set_parametric setups)")
        else:
            from ..solver.optimetrics import Variables
            op = next(o for o in d.optimetrics if o.name == name)
            Variables(d, op, ev)
            if not op.goals:
                raise ValueError("The optimization has no goal (set_optimization goals)")
    except ValueError as e:
        raise ToolError(f"{name}: {e}") from None
    if ctx.gui is not None:
        r = ctx.gui.call("solve", design=d.name, setup="", **{kind: name})
        return {"started": True, "in_window": True, **(r or {}),
                "next": f"get_status({kind}=...) with wait_seconds; get_results({kind}=...)"}
    if not ctx.proj.path:
        raise ToolError("Save the project first")
    if not args.get("skip_validation"):
        tess, errors = tessellate(ctx.proj, d)
        bad = [c.message for c in validate(ctx.proj, d, tess, errors) if c.level == "error"]
        if bad:
            raise ToolError("Validation failed:\n- " + "\n- ".join(bad))
    out = _study_dir(ctx, name)
    p = S.procs.get(str(out))
    if p is not None and p.poll() is None:
        raise ToolError(f"'{name}' is already running (abort_simulation to stop it)")
    out.mkdir(parents=True, exist_ok=True)
    (out / "ABORT").unlink(missing_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    n = int(args.get("cores") or 0) or (os.cpu_count() or 1)
    env["HERTZON_CORES"] = str(n)
    from .clients import python_exe
    log = open(out / "solver.log", "wb")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
    mod = "hertzon.solver.parametric" if kind == "parametric" else "hertzon.solver.optimize"
    S.procs[str(out)] = subprocess.Popen([python_exe(), "-m", mod, ctx.proj.path, d.name, name, str(out)],
                                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                         cwd=str(Path(ctx.proj.path).parent), **kw)
    log.close()
    return {"started": True, "in_window": False, kind: name, "results": str(out), "cores": n,
            "next": f"get_status({kind}=...) with wait_seconds; get_results({kind}=...)"}


def t_run_simulation(args):
    from ..model.validate import validate
    ctx = load(args)
    d = ctx.des
    study = _study(ctx, args)
    if study:
        return _run_study(ctx, *study, args)
    if not d.setups:
        raise ToolError("The design has no analysis setup (set_setup)")
    setup = args.get("setup") or d.setups[0].name
    try:
        d.setup(setup)
    except KeyError:
        raise ToolError(f"Setup '{setup}' not found") from None
    sweep = args.get("sweep") or ""
    mesh_only = bool(args.get("mesh_only"))
    if ctx.gui is not None:
        r = ctx.gui.call("solve", design=d.name, setup=setup, sweep=sweep, mesh_only=mesh_only)
        return {"started": True, "in_window": True, **(r or {}),
                "next": "get_status with wait_seconds to follow the passes"}
    if not ctx.proj.path:
        raise ToolError("Save the project first")
    if not args.get("skip_validation"):
        tess, errors = tessellate(ctx.proj, d)
        bad = [c.message for c in validate(ctx.proj, d, tess, errors) if c.level == "error"]
        if bad:
            raise ToolError("Validation failed:\n- " + "\n- ".join(bad))
    out = _out_dir(ctx, setup)
    key = str(out)
    p = S.procs.get(key)
    if p is not None and p.poll() is None:
        raise ToolError("This setup is already running (abort_simulation to stop it)")
    if sweep:
        if not (out / "amr_state.npz").exists():
            raise ToolError("Analyze the setup first; a single sweep reuses its adaptive mesh")
        for f in list(out.glob(f"sparams_{sweep}.npz")) + list(out.glob(f"fields_{sweep}.npz")) + \
                list(out.glob(f"solution_{sweep}.npz")):
            f.unlink(missing_ok=True)
    elif out.exists():
        keep = None
        try:
            keep = (out / "amr_state.npz").read_bytes()
        except OSError:
            pass
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        if keep:
            (out / "amr_state.npz").write_bytes(keep)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ABORT").unlink(missing_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    n = int(args.get("cores") or 0) or (os.cpu_count() or 1)
    env["HERTZON_CORES"] = str(n)
    env.setdefault("HERTZON_SWEEP_WORKERS", "2")
    env["HERTZON_MESH_ONLY"] = "1" if mesh_only else "0"
    env["HERTZON_ONLY_SWEEP"] = sweep
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
        env[var] = str(n)
    from .clients import python_exe
    log = open(out / "solver.log", "wb")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
    S.procs[key] = subprocess.Popen([python_exe(), "-m", "hertzon.solver.run", ctx.proj.path, d.name, setup, str(out)],
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                    cwd=str(Path(ctx.proj.path).parent), **kw)
    log.close()
    return {"started": True, "in_window": False, "setup": setup, "results": str(out), "cores": n,
            "next": "get_status with wait_seconds to follow the passes"}


def _log_tail(out: Path, n: int = 12) -> list[str]:
    p = out / "solver.log"
    if not p.exists():
        return []
    try:
        lines = p.read_bytes()[-200_000:].decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    msgs = []
    for ln in lines:
        if ln.startswith("@@HZN "):
            try:
                m = json.loads(ln[6:])
            except json.JSONDecodeError:
                continue
            if m.get("type") == "log":
                msgs.append(f"[{m.get('level', 'info')}] {m.get('msg', '')}")
            elif m.get("type") == "failed":
                msgs.append(f"[error] {m.get('error')}")
        elif "Traceback" in ln or "Error" in ln:
            msgs.append(ln.strip())
    return msgs[-n:]


def _status(ctx: Ctx, setup: str, sweep: str = "") -> dict:
    from ..solver.results import ResultStore, run_status
    out = _out_dir(ctx, setup)
    st = ResultStore(out) if out.exists() else None
    status = run_status(out) if st else {}
    state = status.get("state", "not solved")
    res = {"setup": setup}
    p = S.procs.get(str(out))
    if p is not None:
        rc = p.poll()
        if rc is None:
            state = "running"
        elif state == "running":
            state = "failed" if rc != 0 else state
            res["exit_code"] = rc
    if ctx.gui is not None:
        try:
            q = ctx.gui.call("queue", timeout=10)
        except live.LinkError:
            q = {}
        key = _job_key(ctx, setup, sweep)
        if q.get("running") == key:
            state = "running"
        elif key in (q.get("queued") or []):
            state = "queued"
    res["state"] = state
    if status.get("error"):
        res["error"] = status["error"]
    if status.get("elapsed"):
        res["elapsed_s"] = round(float(status["elapsed"]), 1)
    conv = (st.read_json("convergence.json") if st else None) or {}
    if conv.get("passes"):
        res["passes"] = [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in row.items()
                          if k in ("pass", "tets", "delta_s", "delta_mag", "delta_phase", "passed")}
                         for row in conv["passes"]]
        res["converged"] = bool(conv.get("converged"))
        if conv.get("final_tets"):
            res["final_tets"] = conv["final_tets"]
    if st is not None:
        res["solutions"] = st.solutions()
        if p is not None or state in ("failed", "running"):
            res["log"] = _log_tail(out)
    return res


def _study_status(ctx: Ctx, name: str, kind: str) -> dict:
    from ..solver.results import ResultStore, run_status
    out = _study_dir(ctx, name)
    st = ResultStore(out) if out.exists() else None
    status = run_status(out) if st else {}
    state = status.get("state", "not solved")
    res = {kind: name}
    p = S.procs.get(str(out))
    if p is not None:
        rc = p.poll()
        if rc is None:
            state = "running"
        elif state == "running":
            state = "failed" if rc != 0 else state
            res["exit_code"] = rc
    if ctx.gui is not None:
        try:
            q = ctx.gui.call("queue", timeout=10)
        except live.LinkError:
            q = {}
        key = f"{Path(ctx.proj.path).stem} - {ctx.des.name} - {name}"
        if q.get("running") == key:
            state = "running"
        elif key in (q.get("queued") or []):
            state = "queued"
    res["state"] = state
    if status.get("error"):
        res["error"] = status["error"]
    if st is not None:
        if kind == "parametric":
            rows = (st.read_json("table.json") or {}).get("variations", [])
            res["variations"] = len(rows)
            res["variations_done"] = sum(1 for r in rows if (r.get("state") or {})
                                         and all(v == "done" for v in r["state"].values()))
        else:
            hist = (st.read_json("history.json") or {}).get("evaluations", [])
            best = st.read_json("best.json")
            res["evaluations"] = len(hist)
            if best:
                res["best_cost"] = best.get("cost")
        if p is not None or state in ("failed", "running"):
            res["log"] = _log_tail(out)
    return res


def t_get_status(args):
    ctx = load(args)
    d = ctx.des
    study = _study(ctx, args)
    if study:
        if not ctx.proj.path:
            raise ToolError("The project is not saved yet")
        wait = min(float(args.get("wait_seconds") or 0), 600.0)
        t0 = time.time()
        while True:
            s = _study_status(ctx, *study)
            if s["state"] not in ("running", "queued") or time.time() - t0 >= wait:
                return s
            time.sleep(min(3.0, max(0.2, wait - (time.time() - t0))))
    if not d.setups:
        raise ToolError("The design has no analysis setup")
    if not ctx.proj.path:
        raise ToolError("The project is not saved yet")
    setup = args.get("setup") or d.setups[0].name
    wait = min(float(args.get("wait_seconds") or 0), 600.0)
    t0 = time.time()
    while True:
        s = _status(ctx, setup, args.get("sweep") or "")
        if s["state"] not in ("running", "queued") or time.time() - t0 >= wait:
            return s
        time.sleep(min(3.0, max(0.2, wait - (time.time() - t0))))


def t_abort_simulation(args):
    ctx = load(args)
    study = _study(ctx, args)
    if study:
        if ctx.gui is not None:
            return ctx.gui.call("abort", key=f"{Path(ctx.proj.path).stem} - {ctx.des.name} - {study[0]}")
        out = _study_dir(ctx, study[0])
        out.mkdir(parents=True, exist_ok=True)
        (out / "ABORT").write_text("1", encoding="utf-8")      # the run stops its solver processes itself
        return {"aborting": True}
    setup = args.get("setup") or ctx.des.setups[0].name
    if ctx.gui is not None:
        return ctx.gui.call("abort", key=_job_key(ctx, setup, args.get("sweep") or ""))
    out = _out_dir(ctx, setup)
    p = S.procs.get(str(out))
    (out / "ABORT").write_text("1", encoding="utf-8")
    if p is not None and p.poll() is None:
        p.kill()
        return {"aborted": True}
    return {"aborted": False, "note": "no running solver started from this session; ABORT file written"}


# ------------------------------------------------------------------------------------------------
# tools: results
# ------------------------------------------------------------------------------------------------
def _db(v):
    return 20 * np.log10(np.maximum(np.abs(v), 1e-30))


def _segments(f, y, level):
    """Frequency ranges where y < level (linear interpolation of the crossings)."""
    below = y < level
    out = []
    i = 0
    n = len(f)
    while i < n:
        if not below[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and below[j + 1]:
            j += 1
        a = f[i] if i == 0 else f[i - 1] + (level - y[i - 1]) * (f[i] - f[i - 1]) / (y[i] - y[i - 1])
        b = f[j] if j == n - 1 else f[j] + (level - y[j]) * (f[j + 1] - f[j]) / (y[j + 1] - y[j])
        out.append([_r(a / 1e9, 6), _r(b / 1e9, 6)])
        i = j + 1
    return out


def _sp_summary(data: dict) -> dict:
    f = np.asarray(data["freq"], float)
    Sm = np.asarray(data["S"])
    n = Sm.shape[1]
    res = {"ports": [p.get("name") for p in data.get("ports", [])] or list(range(1, n + 1)),
           "freq_GHz": [_r(f.min() / 1e9), _r(f.max() / 1e9)], "points": len(f)}
    params = {}
    for i in range(n):
        for j in range(n):
            y = _db(Sm[:, i, j])
            key = f"S({i + 1},{j + 1})"
            if len(f) == 1:
                params[key] = {"dB": _r(y[0], 4), "phase_deg": _r(np.degrees(np.angle(Sm[0, i, j])), 4)}
            elif i == j:
                k = int(np.argmin(y))
                params[key] = {"min_dB": _r(y[k], 4), "at_GHz": _r(f[k] / 1e9), "below_-10dB_GHz": _segments(f, y, -10)}
            else:
                k = int(np.argmax(y))
                params[key] = {"max_dB": _r(y[k], 4), "at_GHz": _r(f[k] / 1e9), "min_dB": _r(y.min(), 4)}
    res["S"] = params
    return res


def _parse_param(p: str, n: int) -> tuple[int, int]:
    s = p.replace(" ", "").upper()
    if s.startswith("S(") and s.endswith(")"):
        a, b = s[2:-1].split(",")
    elif s.startswith("S") and len(s) == 3:
        a, b = s[1], s[2]
    else:
        raise ToolError(f"Parameter '{p}': use S11, S21 or S(1,2)")
    i, j = int(a) - 1, int(b) - 1
    if not (0 <= i < n and 0 <= j < n):
        raise ToolError(f"{p}: the solution has {n} port(s)")
    return i, j


def _ff_quantities(st, des, ev, sphere: str, label: str, port: int, k: int):
    from ..solver.farfield_post import farfield
    from ..solver.ffquant import excited_quantities
    ff = farfield(st, des, sphere, label, ev)
    if ff is None:
        raise ToolError(f"No far field '{sphere}' for {label}; it needs an open boundary (AutoOpen or "
                        "Radiation) and a solved setup")
    keys = sorted({int(x.split("_")[1]) for x in ff if x.startswith("E_")})
    if not keys:
        raise ToolError("The far field file holds no data")
    kk = keys[min(k, len(keys) - 1)]
    try:
        q = excited_quantities(st, des, ff, label, kk, port)    # port None: the excitation of Edit Sources
    except RuntimeError as e:
        raise ToolError(str(e)) from None
    return ff, q, float(ff[f"freq_{kk}"][0])


def _cut(q: dict, name: str, phi_deg: float, to_db, n: int):
    th, ph = q["_theta"], q["_phi"]
    j = int(np.argmin(np.abs(((ph - math.radians(phi_deg)) + np.pi) % (2 * np.pi) - np.pi)))
    v = q[name][:, j]
    idx = np.unique(np.linspace(0, len(th) - 1, min(n, len(th))).astype(int))
    return {"phi_deg": _r(math.degrees(ph[j]), 4), "theta_deg": [_r(math.degrees(th[i]), 4) for i in idx],
            name + "_dB": [_r(to_db(v[i]), 4) for i in idx]}


def _study_results(ctx: Ctx, name: str, kind: str, args) -> dict:
    from ..solver.results import ResultStore
    st = ResultStore(_study_dir(ctx, name))
    n = int(args.get("max_points", 50))
    if kind == "parametric":
        t = st.read_json("table.json")
        if not t:
            raise ToolError(f"No results for '{name}' yet (run_simulation(parametric=...))")
        rows = t.get("variations", [])
        return {"parametric": name, "variables": t.get("variables"), "setups": t.get("setups"),
                "calcs": t.get("calcs"), "variations": rows[:n],
                **({"note": f"first {n} of {len(rows)} variations; raise max_points"} if len(rows) > n else {})}
    hist = (st.read_json("history.json") or {}).get("evaluations", [])
    if not hist:
        raise ToolError(f"No results for '{name}' yet (run_simulation(optimization=...))")
    return {"optimization": name, "best": st.read_json("best.json"), "summary": st.read_json("summary.json"),
            "evaluations": len(hist), "last": hist[-min(n, 10):],
            "note": "Apply the best values with set_design variables when they are good."}


def t_get_results(args):
    from ..solver.results import ResultStore
    ctx = load(args)
    d = ctx.des
    if not ctx.proj.path:
        raise ToolError("The project is not saved yet")
    study = _study(ctx, args)
    if study:
        return _study_results(ctx, *study, args)
    setup = args.get("setup") or (d.setups[0].name if d.setups else None)
    if setup is None:
        raise ToolError("No setup")
    out = _out_dir(ctx, setup)
    st = ResultStore(out)
    sols = st.solutions()
    kind = args.get("kind", "summary")
    ev = d.evaluator(ctx.proj)
    if kind == "summary":
        res = {"setup": setup, "state": _status(ctx, setup)["state"], "results_folder": str(out),
               "solutions": {}}
        for sol in sols:
            data = st.sparams(sol)
            if data is not None:
                res["solutions"][sol] = _sp_summary(data)
                n = np.asarray(data["S"]).shape[1]
                ts = out / f"{sol}.s{n}p"
                if ts.exists():
                    res["solutions"][sol]["touchstone"] = str(ts)
        conv = st.read_json("convergence.json") or {}
        if conv:
            res["convergence"] = {"passes": len(conv.get("passes", [])), "converged": bool(conv.get("converged")),
                                  "final_tets": conv.get("final_tets")}
        pw = any(e.kind == "Incident Plane Wave" and e.enabled for e in d.excitations) and not d.ports()
        for sph in d.spheres:
            try:
                if pw:
                    from ..solver.ffquant import monostatic_rcs
                    r = monostatic_rcs(st, "LastAdaptive", sph.name, d)
                    res.setdefault("far_field", {})[sph.name] = {
                        "monostatic_rcs_dBsm": [_r(10 * np.log10(max(x, 1e-30)), 4) for x in r["MonostaticRCSTotal"]],
                        "freq_GHz": [_r(x / 1e9) for x in r["freq"]]}
                else:
                    from ..solver.ffquant import antenna_parameters
                    _, q, fq = _ff_quantities(st, d, ev, sph.name, "LastAdaptive", None, 0)
                    res.setdefault("far_field", {})[sph.name] = {"freq_GHz": _r(fq / 1e9),
                                                                 **dict(antenna_parameters(q))}
            except Exception as e:  # noqa: BLE001
                res.setdefault("far_field", {})[sph.name] = f"not available: {e}"
        return res
    if kind == "sparameters":
        sol = args.get("solution") or next((s for s in sols if s != "LastAdaptive"), "LastAdaptive")
        data = st.sparams(sol)
        if data is None:
            raise ToolError(f"No S-parameters for '{sol}'; solutions: {sols}")
        f = np.asarray(data["freq"], float)
        Sm = np.asarray(data["S"])
        n = Sm.shape[1]
        params = args.get("params") or [f"S{i + 1}{1}" for i in range(n)]
        ij = [_parse_param(p, n) for p in params]
        if args.get("frequencies"):
            fq = np.array([ev.freq(_s(x)) for x in args["frequencies"]])
            rows = []
            for x in fq:
                k = int(np.argmin(np.abs(f - x)))
                rows.append((x, k))
        else:
            m = int(args.get("max_points", 41))
            rows = [(f[k], k) for k in np.unique(np.linspace(0, len(f) - 1, min(m, len(f))).astype(int))]
        table = []
        for x, k in rows:
            r = {"freq_GHz": _r(f[k] / 1e9)}
            for p, (i, j) in zip(params, ij):
                v = Sm[k, i, j]
                r[p + "_dB"] = _r(_db(v), 4)
                r[p + "_deg"] = _r(np.degrees(np.angle(v)), 4)
            table.append(r)
        return {"solution": sol, "ports": n, "rows": table}
    if kind == "convergence":
        return _status(ctx, setup)
    if kind in ("farfield", "rcs"):
        sph = args.get("sphere") or (d.spheres[0].name if d.spheres else None)
        if sph is None:
            raise ToolError("No far-field sphere (set_far_field_sphere), then analyze again or just re-read: "
                            "spheres added after the solve are computed from the stored boundary fields")
        label = args.get("solution") or "LastAdaptive"
        n = int(args.get("max_points", 37))
        if kind == "rcs":
            from ..solver.ffquant import monostatic_rcs
            out_ = {}
            try:
                r = monostatic_rcs(st, label, sph, d)
                out_["monostatic"] = {"freq_GHz": [_r(x / 1e9) for x in r["freq"]],
                                      "rcs_dBsm": [_r(10 * np.log10(max(x, 1e-30)), 4) for x in r["MonostaticRCSTotal"]]}
            except Exception as e:  # noqa: BLE001
                out_["monostatic"] = f"not available: {e}"
            _, q, fq = _ff_quantities(st, d, ev, sph, label, 1, int(args.get("freq_index", 0)))
            to_db = lambda v: 10 * math.log10(max(float(v), 1e-30))  # noqa: E731
            out_["bistatic_freq_GHz"] = _r(fq / 1e9)
            out_["bistatic_cuts"] = [_cut(q, "BistaticRCSTotal", ph, to_db, n) for ph in args.get("phi_cuts", [0, 90])]
            out_["unit"] = "dBsm"
            return out_
        from ..solver.ffquant import antenna_parameters
        _, q, fq = _ff_quantities(st, d, ev, sph, label, int(args["port"]) if args.get("port") else None,
                             int(args.get("freq_index", 0)))
        to_db = lambda v: 10 * math.log10(max(float(v), 1e-30))  # noqa: E731
        qty = args.get("quantity", "GainTotal")
        if qty not in q:
            raise ToolError(f"quantity must be one of {[k for k in q if not k.startswith('_')]}")
        return {"sphere": sph, "freq_GHz": _r(fq / 1e9), "antenna_parameters": dict(antenna_parameters(q)),
                "cuts": [_cut(q, qty, ph, to_db, n) for ph in args.get("phi_cuts", [0, 90])],
                "unit": "dBi" if qty.startswith(("Gain", "Dir", "Realized")) else "dB"}
    raise ToolError("kind must be summary, sparameters, convergence, farfield or rcs")


# ------------------------------------------------------------------------------------------------
# tools: picture of the model
# ------------------------------------------------------------------------------------------------
def t_render_image(args):
    import pyvista as pv
    ctx = load(args)
    d = ctx.des
    tess, errors = tessellate(ctx.proj, d)
    sc = _unit(d)
    w, h = int(args.get("width", 1000)), int(args.get("height", 700))
    pl = pv.Plotter(off_screen=True, window_size=(w, h))
    pl.set_background("white")
    label = args.get("label_faces_of")
    shown = args.get("objects")
    for o in d.live_objects():
        t = tess.get(o.name)
        if t is None or (shown and o.name not in shown) or not o.visible:
            continue
        is_region = o.history[0].kind == "CreateRegion"
        if len(t.tris):
            m = pv.PolyData(np.asarray(t.points) / sc, np.c_[np.full(len(t.tris), 3), t.tris].ravel())
            op = 0.08 if is_region else max(0.15, 1.0 - float(o.transparency))
            pl.add_mesh(m, color=o.color, opacity=op, show_edges=False, smooth_shading=False)
        lines = [e for e in t.edges if len(e) > 1]
        for e in lines[:4000]:
            pl.add_mesh(pv.lines_from_points(np.asarray(e) / sc), color="#202020", line_width=1)
        if label == o.name:
            what = args.get("label_what", "faces")
            if what == "edges":
                idx = [i for i, e in enumerate(t.edges) if len(e)]
                C = np.array([np.asarray(t.edges[i])[len(t.edges[i]) // 2] for i in idx]).reshape(-1, 3) / sc
            elif what == "vertices":
                C = np.asarray(t.vertices, float).reshape(-1, 3) / sc
                idx = list(range(len(C)))
            else:
                C = np.asarray(t.face_center).reshape(-1, 3) / sc
                idx = list(range(len(C)))
            if len(C):
                pl.add_point_labels(C, [str(i) for i in idx], font_size=14, point_size=6,
                                    text_color="black", shape_opacity=0.6, always_visible=True)
    for a in d.excitations + d.boundaries:
        if not args.get("show_assignments", True):
            break
        col = {**schema.BOUNDARY_COLORS, **schema.EXCITATION_COLORS}.get(a.kind, "#ff0000")
        for f in a.faces:
            t = tess.get(f.obj)
            if t is None or f.face < 0:
                continue
            T = t.tris[t.tri_face == f.face]
            if len(T):
                pl.add_mesh(pv.PolyData(np.asarray(t.points) / sc, np.c_[np.full(len(T), 3), T].ravel()),
                            color=col, opacity=0.9)
    pl.add_axes()
    view = args.get("view", "iso")
    {"iso": pl.view_isometric, "top": pl.view_xy, "bottom": lambda: pl.view_xy(negative=True),
     "front": pl.view_xz, "back": lambda: pl.view_xz(negative=True), "right": pl.view_yz,
     "left": lambda: pl.view_yz(negative=True)}.get(view, pl.view_isometric)()
    if args.get("azimuth"):
        pl.camera.azimuth = float(args["azimuth"])
    if args.get("elevation"):
        pl.camera.elevation = float(args["elevation"])
    img = pl.screenshot(return_img=True)
    pl.close()
    from io import BytesIO
    from PIL import Image as PILImage
    buf = BytesIO()
    PILImage.fromarray(img).save(buf, format="PNG")
    note = f"{d.name}, view {view}, units {d.units}"
    if errors:
        note += f"; geometry errors: {errors}"
    return [Image(buf.getvalue(), note)]


# ------------------------------------------------------------------------------------------------
# tools: scripted edit
# ------------------------------------------------------------------------------------------------
def t_run_python(args):
    import contextlib
    import io
    ctx = load(args)
    d = ctx.des
    buf = io.StringIO()
    env = {"project": ctx.proj, "design": d, "Command": Command, "FaceRef": FaceRef, "Assignment": Assignment,
           "Setup": Setup, "Sweep": Sweep, "InfiniteSphere": InfiniteSphere, "schema": schema, "np": np,
           "math": math, "add_object": d.add_object,
           "operation": lambda obj, kind, **props: _append_operation(d, obj, kind, props)}
    before = {o.name for o in d.objects}
    with contextlib.redirect_stdout(buf):
        exec(compile(args["code"], "<hertzon edit>", "exec"), env)  # noqa: S102
    commit(ctx, "script edit")
    added = [o.name for o in d.objects if o.name not in before]
    return {"output": buf.getvalue()[-20000:], "objects_added": added}


# ------------------------------------------------------------------------------------------------
# registry
# ------------------------------------------------------------------------------------------------
P = {"type": "string", "description": "Path of the .hzn project; default: the project open in the Hertzon "
                                       "window with the live link, else the last project used"}
D = {"type": "string", "description": "Design name; default: the design shown in the window or the first one"}
OBJ = {"type": "object", "description": "Property name -> value (expression strings)",
       "additionalProperties": {"type": ["string", "number", "boolean"]}}
FACES = {"type": "array", "description": "Faces as {object, face} (face index from get_geometry)",
         "items": {"type": "object", "properties": {"object": {"type": "string"}, "face": {"type": "integer"}},
                   "required": ["object", "face"]}}


def _schema(props: dict, required: list[str] | None = None, project: bool = True) -> dict:
    p = ({"project": P, "design": D} if project else {}) | props
    return {"type": "object", "properties": p, "required": required or []}


TOOLS = [
    Tool("get_context", "Which Hertzon windows have the live link on and which project this session uses.",
         _schema({}, project=False), t_get_context, True),
    Tool("create_project", "Create a new .hzn project (one empty design) and make it the session project. "
         "Opens it in the Hertzon window when one has the live link on.",
         _schema({"path": {"type": "string"}, "name": {"type": "string"}, "design": {"type": "string"},
                  "units": {"type": "string", "description": "mm (default), um, cm, m, mil, in ..."},
                  "solution_type": {"type": "string", "enum": list(schema.SOLUTION_TYPES)},
                  "overwrite": {"type": "boolean"}, "open_in_gui": {"type": "boolean"}}, ["path"], False),
         t_create_project),
    Tool("open_project", "Use an existing .hzn project; returns its full description.",
         _schema({"path": {"type": "string"}, "design": D, "open_in_gui": {"type": "boolean"}}, ["path"], False),
         t_open_project),
    Tool("get_project", "Full description of a design: units, variables, settings, objects with their "
         "history, boundaries, excitations, mesh operations, setups and solution states.",
         _schema({}), t_get_project, True),
    Tool("save_project", "Save the project (in the window, or to a new path).",
         _schema({"path": {"type": "string", "description": "optional: save as"}}), t_save_project),
    Tool("get_schema", "Available kinds and their properties (type, default, choices, help).",
         _schema({"section": {"type": "string", "enum": ["geometry", "operations", "boundaries", "excitations",
                                                         "mesh_operations", "setup", "eigen_setup", "sweep",
                                                         "design_settings", "far_field_sphere", "coordinate_system",
                                                         "parametric", "optimization"]},
                  "kind": {"type": "string"}}, ["section"], False), t_get_schema, True),
    Tool("list_materials", "Materials of the project and the system library.",
         _schema({"filter": {"type": "string"}}), t_list_materials, True),
    Tool("add_material", "Add or change a project material.",
         _schema({"name": {"type": "string"}, "eps_r": {"type": "string"}, "mu_r": {"type": "string"},
                  "sigma": {"type": "string", "description": "S/m"}, "tan_delta": {"type": "string"},
                  "description": {"type": "string"}}, ["name"]), t_add_material),
    Tool("set_design", "Change units, variables (null deletes), design settings (SolutionType, AutoOpen, "
         "AutoOpenBoundary, AutoOpenPadding, DifferentialPairs ...), rename, or add a new design. "
         "DifferentialPairs: JSON list of {\"pos\": terminal, \"neg\": terminal, \"zdiff\": \"100ohm\", "
         "\"zcomm\": \"25ohm\"} (terminals from 1); pair k gives the report quantities Diffk and Commk, "
         "e.g. dB(S(Diff1,Diff1)).",
         _schema({"units": {"type": "string"}, "variables": {"type": "object"},
                  "project_variables": {"type": "object", "description": "names start with $"},
                  "settings": OBJ, "rename": {"type": "string"},
                  "new_design": {"type": "string", "description": "create a design with this name instead"},
                  "notes": {"type": "string"}}), t_set_design),
    Tool("add_object", "Create a solid or sheet (CreateBox, CreateCylinder, CreateSphere, CreateRectangle, "
         "CreatePolyline, CreateRegion ...) with optional operations. Returns its faces (index, centre, normal).",
         _schema({"kind": {"type": "string"}, "name": {"type": "string"}, "material": {"type": "string"},
                  "props": OBJ, "attributes": {"type": "object", "description": "color, transparency, "
                                                "solve_inside, model, group"},
                  "operations": {"type": "array", "items": {"type": "object", "properties": {
                      "kind": {"type": "string"}, "props": OBJ}, "required": ["kind"]}},
                  "check": {"type": "boolean", "description": "build and report the geometry (default true)"}},
                 ["kind"]), t_add_object),
    Tool("import_geometry", "Import a STEP, IGES, BREP or STL file; one object per body.",
         _schema({"file": {"type": "string"}, "name": {"type": "string"}, "material": {"type": "string"},
                  "check": {"type": "boolean"}}, ["file"]), t_import_geometry),
    Tool("modify_object", "Change creation or operation properties (history_index), material, attributes, "
         "name; suppress or remove an operation.",
         _schema({"name": {"type": "string"}, "props": OBJ, "history_index": {"type": "integer"},
                  "material": {"type": "string"}, "attributes": {"type": "object"},
                  "new_name": {"type": "string"}, "suppress": {"type": "boolean"},
                  "remove_operation": {"type": "boolean"}, "check": {"type": "boolean"}}, ["name"]),
         t_modify_object),
    Tool("add_operation", "Append an operation to objects: Subtract/Unite/Intersect (props Tools='A,B'; tools "
         "are consumed unless KeepOriginals), Move, Rotate, Mirror, Scale, ThickenSheet, SweepAlongVector, "
         "Split, Fillet, Chamfer ...",
         _schema({"object": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}},
                  "kind": {"type": "string"}, "props": OBJ, "check": {"type": "boolean"}}, ["kind"]),
         t_add_operation),
    Tool("delete", "Delete objects (with their consumed tools), boundaries, excitations, mesh operations, "
         "setups, sweeps, far-field spheres, coordinate systems, equations, functions, reports, field plots, "
         "parametric setups or optimizations by name.",
         _schema({"names": {"type": "array", "items": {"type": "string"}}, "setup": {"type": "string"}},
                 ["names"]), t_delete),
    Tool("get_geometry", "Build the model and list per object: dimension, bounding box, volume and faces "
         "(index, centre, normal, area, planar) in design units. Use the face indices in assign.",
         _schema({"objects": {"type": "array", "items": {"type": "string"}},
                  "max_faces": {"type": "integer"},
                  "include_edges": {"type": "boolean", "description": "also list edges (start, end, mid, length) "
                                    "and vertices with the indices of Fillet/Chamfer Edges and Vertices"}}),
         t_get_geometry, True),
    Tool("assign", "Assign a boundary (Perfect E, Finite Conductivity, Radiation, PML, Symmetry ...), an "
         "excitation (Wave Port, Lumped Port, Incident Plane Wave ...) or a mesh operation to objects or faces.",
         _schema({"category": {"type": "string", "enum": list(_CATS)}, "kind": {"type": "string"},
                  "name": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}},
                  "faces": FACES, "edges": {"type": "array", "items": {"type": "object"}},
                  "props": OBJ, "replace": {"type": "boolean"}}, ["category", "kind"]), t_assign),
    Tool("modify_assignment", "Change properties, selection, name or enabled state of a boundary, "
         "excitation or mesh operation.",
         _schema({"name": {"type": "string"}, "props": OBJ, "objects": {"type": "array", "items": {"type": "string"}},
                  "faces": FACES, "enabled": {"type": "boolean"}, "new_name": {"type": "string"}}, ["name"]),
         t_modify_assignment),
    Tool("set_setup", "Create or change an analysis setup (Frequency, MaximumPasses, MaximumDeltaS, "
         "MaximumRefinement, BasisOrder ...) and its sweeps.",
         _schema({"name": {"type": "string"}, "props": OBJ, "eigen": {"type": "boolean"},
                  "sweeps": {"type": "array", "items": {"type": "object", "properties": {
                      "name": {"type": "string"}, "props": OBJ}}}}), t_set_setup),
    Tool("set_sweep", "Create or change one frequency sweep of a setup (SweepType, Distribution, Start, Stop, "
         "Step/Count ...).",
         _schema({"setup": {"type": "string"}, "name": {"type": "string"}, "props": OBJ}), t_set_sweep),
    Tool("set_far_field_sphere", "Create or change an infinite sphere for far fields (gain, RCS).",
         _schema({"name": {"type": "string"}, "props": OBJ}), t_set_far_field_sphere),
    Tool("set_coordinate_system", "Create or change a relative coordinate system (Origin, XAxis, YPoint or "
         "Euler angles, relative to reference). A new one becomes the working CS: objects created afterwards are "
         "defined in it (set_working=false keeps the current one; name 'Global' with set_working goes back).",
         _schema({"name": {"type": "string"}, "reference": {"type": "string"}, "props": OBJ,
                  "set_working": {"type": "boolean"}}), t_set_coordinate_system),
    Tool("set_equation", "Fields Calculator: define or change named equations of the design (fields such as "
         "loss densities, values such as flux(Port1, re(S)), int_vol(obj, ...), max(obj, |E|), or network "
         "values like -dB(S(1,1))) and functions 'name(a, b) = body'; import or export them as a file. They are "
         "evaluated in the window (field plots, reports).",
         _schema({"name": {"type": "string"}, "expr": {"type": "string"}, "description": {"type": "string"},
                  "equations": {"type": "array", "items": {"type": "object", "properties": {
                      "name": {"type": "string"}, "expr": {"type": "string"}, "description": {"type": "string"}}}},
                  "functions": {"type": "array", "items": {"type": "string"}},
                  "replace": {"type": "boolean", "description": "replace equations of the same name (default true)"},
                  "import_file": {"type": "string"}, "export_file": {"type": "string"}}), t_set_equation),
    Tool("set_report", "Create or change a report (kind Rectangular, Smith Chart, Data Table, Radiation Pattern, "
         "3D Polar Plot, Antenna Parameters, Monostatic RCS ...) with traces like dB(S(2,1)) or an equation; "
         "fields=true makes a Fields report of value equations over frequency. Shown in the window.",
         _schema({"name": {"type": "string"}, "kind": {"type": "string", "enum": list(REPORT_KINDS)},
                  "solution": {"type": "string", "description": "'Setup1 : Sweep' or 'Setup1 : LastAdaptive'"},
                  "traces": {"type": "array", "items": {"type": "string"}}, "fields": {"type": "boolean"},
                  "port": {"type": "integer", "description": "excited port of a Fields report"},
                  "props": OBJ, "new_name": {"type": "string"}}), t_set_report),
    Tool("set_field_plot", "Create or change a field plot (Mag_E, Vector_E, Mag_H, Mag_Jsurf, Vector_J ... or a "
         "field equation) on objects, faces or a cut plane; drawn in the window.",
         _schema({"name": {"type": "string"}, "quantity": {"type": "string"},
                  "solution": {"type": "string", "description": "'Setup1 : LastAdaptive' or a sweep with fields"},
                  "objects": {"type": "array", "items": {"type": "string"}}, "faces": FACES,
                  "plane": {"type": "string", "description": "cut plane 'XY', 'YZ' or 'XZ' with position: 'XY@1mm'"},
                  "volume": {"type": "boolean", "description": "inside the objects (dots or arrows), not on "
                                                               "their surface"},
                  "port": {"type": "integer"}, "freq_index": {"type": "integer"}, "phase_deg": {"type": "number"},
                  "attributes": {"type": "object", "description": "ColorMap, Scale ('Use Limits'), Min, Max, dB, "
                                                                  "Log, Transparency, PlotType, ArrowCount ..."},
                  "new_name": {"type": "string"}}), t_set_field_plot),
    Tool("set_parametric", "Create or change a parametric sweep: sweeps [{variable, type (Linear step, Linear "
         "count, Single value ...), start, stop, step, sync}], setups to solve, calcs [{calc, solution, func, "
         "start, stop}] and props (SaveFields, ParallelVariations).",
         _schema({"name": {"type": "string"}, "sweeps": {"type": "array", "items": {"type": "object"}},
                  "setups": {"type": "array", "items": {"type": "string"}},
                  "calcs": {"type": "array", "items": {"type": "object"}}, "props": OBJ}), t_set_parametric),
    Tool("set_optimization", "Create or change an optimization: variables [{name, include, min, max}], goals "
         "[{calc like dB(S(1,1)), start, stop (frequencies), cond (<=, >=, =, Ripple <=), value, weight}] on a "
         "solution, and optimizer props.",
         _schema({"name": {"type": "string"}, "solution": {"type": "string"},
                  "variables": {"type": "array", "items": {"type": "object"}},
                  "goals": {"type": "array", "items": {"type": "object"}}, "props": OBJ}), t_set_optimization),
    Tool("validate", "Run the validation check (geometry, materials, boundaries, ports, setup).",
         _schema({}), t_validate, True),
    Tool("run_simulation", "Analyze a setup (adaptive passes, sweeps, far fields). Runs in the Hertzon window "
         "when linked, otherwise as a background process. Returns at once; follow with get_status.",
         _schema({"setup": {"type": "string"}, "sweep": {"type": "string", "description": "only this sweep, "
                                                         "reusing the adaptive mesh"},
                  "mesh_only": {"type": "boolean"}, "cores": {"type": "integer"},
                  "skip_validation": {"type": "boolean"}, "parametric": {"type": "string", "description": "a parametric setup instead of a setup"},
                  "optimization": {"type": "string", "description": "an optimization instead of a setup"}}), t_run_simulation),
    Tool("get_status", "State of a setup's analysis (queued, running, done, failed), adaptive passes with "
         "tetrahedra and Delta S, and the last log lines. wait_seconds waits (up to 600 s) while it runs.",
         _schema({"setup": {"type": "string"}, "sweep": {"type": "string"},
                  "wait_seconds": {"type": "number"}, "parametric": {"type": "string", "description": "a parametric setup instead of a setup"},
                  "optimization": {"type": "string", "description": "an optimization instead of a setup"}}), t_get_status, True),
    Tool("abort_simulation", "Stop a running analysis.",
         _schema({"setup": {"type": "string"}, "sweep": {"type": "string"}, "parametric": {"type": "string", "description": "a parametric setup instead of a setup"},
                  "optimization": {"type": "string", "description": "an optimization instead of a setup"}}), t_abort_simulation),
    Tool("get_results", "Results of a setup. kind: summary (all solutions: resonances, -10 dB bands, peak "
         "gain or RCS), sparameters (table in dB/deg; params like ['S11','S21'], frequencies or max_points), "
         "convergence, farfield (antenna parameters and pattern cuts; quantity GainTotal, DirTotal, "
         "RealizedGainTotal ...), rcs (monostatic over frequency and bistatic cuts). With parametric=...: the "
         "variations and their calculations; with optimization=...: best values, cost and the last evaluations.",
         _schema({"setup": {"type": "string"}, "kind": {"type": "string", "enum": ["summary", "sparameters",
                                                                                 "convergence", "farfield", "rcs"]},
                  "solution": {"type": "string"}, "params": {"type": "array", "items": {"type": "string"}},
                  "frequencies": {"type": "array", "items": {"type": ["string", "number"]}},
                  "max_points": {"type": "integer"}, "sphere": {"type": "string"},
                  "quantity": {"type": "string"}, "port": {"type": "integer"}, "freq_index": {"type": "integer"},
                  "phi_cuts": {"type": "array", "items": {"type": "number"}}, "parametric": {"type": "string", "description": "a parametric setup instead of a setup"},
                  "optimization": {"type": "string", "description": "an optimization instead of a setup"}}), t_get_results, True),
    Tool("render_image", "Picture of the model (PNG): objects in their colours, ports and boundaries on faces, "
         "optionally the face indices of one object.",
         _schema({"view": {"type": "string", "enum": ["iso", "top", "bottom", "front", "back", "left", "right"]},
                  "objects": {"type": "array", "items": {"type": "string"}},
                  "label_faces_of": {"type": "string", "description": "object whose indices are drawn"},
                  "label_what": {"type": "string", "enum": ["faces", "edges", "vertices"]},
                  "show_assignments": {"type": "boolean"},
                  "azimuth": {"type": "number"}, "elevation": {"type": "number"},
                  "width": {"type": "integer"}, "height": {"type": "integer"}}), t_render_image, True),
    Tool("run_python", "Edit the design with Python for repetitive work (arrays, parametric layouts). Names: "
         "project, design, add_object(kind, name=None, material='vacuum', **props), operation(obj, kind, "
         "**props), Command, Assignment, FaceRef, Setup, Sweep, schema, np, math. Changes are committed "
         "afterwards; print() output is returned.",
         _schema({"code": {"type": "string"}}, ["code"]), t_run_python),
]
