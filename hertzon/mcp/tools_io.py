"""MCP tools for files and boards: exports (Touchstone, STEP, Gerber, EMerge script), PCB import, the PCB stackup,
copying and deleting designs, and the example projects."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from .protocol import Tool, ToolError
from .tools import ROOT, S, Ctx, _abspath, _schema, commit, load


# ------------------------------------------------------------------------------------------------
# exports
# ------------------------------------------------------------------------------------------------
def t_export(args):
    ctx = load(args)
    d = ctx.des
    kind = args["kind"]
    path = _abspath(args["path"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if kind == "touchstone":
        from ..solver.results import ResultStore, results_dir
        if not ctx.proj.path:
            raise ToolError("The project is not saved yet")
        if not d.setups:
            raise ToolError("The design has no setup")
        setup = args.get("setup") or d.setups[0].name
        st = ResultStore(results_dir(ctx.proj.path, d.name, setup))
        sols = st.solutions()
        sol = args.get("solution") or next((s for s in sols if s != "LastAdaptive"), "LastAdaptive")
        data = st.sparams(sol)
        if data is None or data.get("ports_only"):
            raise ToolError(f"No S-parameters for '{sol}' of {setup}; solutions: {sols}")
        n = np.asarray(data["S"]).shape[1]
        if not path.lower().endswith(f".s{n}p"):
            path += f".s{n}p"
        out = st.write_touchstone(sol, data["freq"], data["S"], data["z0"], path)
        return {"written": str(out), "solution": f"{setup} : {sol}", "ports": n, "points": len(data["freq"])}
    if kind == "step":
        if getattr(d, "kind", "3d") == "circuit":
            raise ToolError("A circuit design has no geometry (circuit_layout action='create' makes a 3D layout)")
        import gmsh
        from ..kernel import occ as K
        if not path.lower().endswith((".step", ".stp")):
            path += ".step"
        K.ensure_gmsh("hertzon_export")
        gmsh.model.remove()
        gmsh.model.add("hertzon_export")
        K.build_design(d, d.evaluator(ctx.proj))
        gmsh.write(path)
        gmsh.model.remove()
        return {"written": path}
    if kind == "gerber":
        from ..export import gerber as G
        opts = G.Options(units=args.get("units") or "mm", cs=args.get("cs") or "Global",
                         plane=args.get("plane") or "XY")
        try:
            res = G.run(ctx.proj, d, opts)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"Gerber export: {e}") from None
        msgs = [{"level": m.level, "text": m.text} for m in res.messages]
        if not res.ok:
            return {"written": [], "messages": msgs}
        files = G.write(res, path, args.get("base") or d.name)
        return {"written": [str(p) for p in files], "messages": msgs}
    if kind == "script":
        from ..solver.script import design_to_script
        if not path.lower().endswith(".py"):
            path += ".py"
        Path(path).write_text(design_to_script(ctx.proj, d.name), encoding="utf-8")
        return {"written": path, "note": "stand-alone Python script driving EMerge for this design"}
    raise ToolError("kind is touchstone, step, gerber or script")


# ------------------------------------------------------------------------------------------------
# PCB: import and stackup
# ------------------------------------------------------------------------------------------------
def _stackup_summary(d) -> dict | None:
    st = d.stackup
    if st is None:
        return None
    return {"source": st.source, "cs": st.cs, "plane": st.plane,
            "layers": [{"name": L.name, "kind": L.kind, "thickness": L.thickness, "material": L.material,
                        **({"permittivity": L.permittivity} if L.permittivity else {}),
                        **({"loss_tangent": L.loss_tangent} if L.loss_tangent else {}),
                        **({"representation": L.representation} if L.kind == "Copper" else {})}
                       for L in st.layers]}


def t_import_pcb(args):
    from ..pcbimport import board as B
    ctx = load(args)
    paths = [_abspath(p) for p in args["files"]]
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        raise ToolError(f"Not found: {missing}")
    seg = int(args.get("arc_segments") or B.G.DEFAULT_SEGMENTS)
    if not 8 <= seg <= 720:
        raise ToolError("arc_segments is 8 to 720 (straight segments per full circle)")
    try:
        files, job, jn = B.load(paths, segments=seg)
        pl = B.plan(files, job, jn, stackup=ctx.des.stackup, segments=seg)
        if args.get("crop_mm"):
            x0, y0, x1, y1 = (float(v) * 1e-3 for v in args["crop_mm"])
            pl.crop = (pl.origin[0] + x0, pl.origin[1] + y0, pl.origin[0] + x1, pl.origin[1] + y1)
        rep = B.build(ctx.proj, ctx.des, pl)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"The board cannot be made: {e}") from None
    errors = commit(ctx, "pcb import")
    if ctx.gui is None and rep.shapes:
        # no window built it: build the copper here, a shape the kernel cannot make is missing copper
        from .tools import tessellate
        try:
            _, errors = tessellate(ctx.proj, ctx.des, list(rep.shapes), timeout=args.get("timeout_s"))
        except ToolError as e:
            rep.warnings.append(f"The copper could not be checked: {e}")
    rep.check_built(errors)
    return {"report": rep.lines, "warnings": rep.warnings, "not_loaded": rep.not_loaded, "notes": pl.notes,
            "files": [{"name": f.name, "kind": f.kind, **({"layer": f.layer} if f.layer else {}),
                       **({"error": f.error} if f.error else {})} for f in files],
            "stackup": _stackup_summary(ctx.des), **({"geometry_errors": errors} if errors else {})}


def t_set_stackup(args):
    from ..model import stackup as SK
    ctx = load(args)
    d = ctx.des
    ev = d.evaluator(ctx.proj)
    if args.get("remove"):
        SK.apply_to_design(ctx.proj, d, None)
        commit(ctx, "stackup removed")
        return {"stackup": None}
    if args.get("layers"):
        layers = []
        for L in args["layers"]:
            kind = str(L.get("kind", "dielectric")).capitalize()
            if kind not in (SK.COPPER, SK.DIELECTRIC):
                raise ToolError("layer kind is 'copper' or 'dielectric' (solder mask and legend are not simulated)")
            kw = {k: str(v) for k, v in L.items() if k in SK.StackLayer.__dataclass_fields__ and k != "origin"}
            kw["kind"] = kind
            layers.append(SK.StackLayer(**kw))
        st = SK.Stackup(layers=layers, source="manual")
    else:
        st = SK.template(int(args.get("copper_layers", 2)), str(args.get("thickness", "1.6mm")), ev=ev)
        if args.get("material"):
            for L in st.layers:
                if L.kind == "dielectric":
                    L.set("material", args["material"])
    st.cs = args.get("cs") or st.cs
    st.plane = args.get("plane") or st.plane
    rect = args.get("substrate")
    sub = tuple(str(x) for x in rect) if rect else None
    if sub is not None and len(sub) != 4:
        raise ToolError("substrate: [x0, y0, xsize, ysize] in the board plane")
    try:
        made = SK.apply_to_design(ctx.proj, d, st, sub)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Stackup: {e}") from None
    errors = commit(ctx, "stackup")
    return {"stackup": _stackup_summary(d), "objects_made": made,
            "variables": {k: v for k, v in d.variables.items() if k.startswith("stk_")},
            "coordinate_systems": [c.name for c in d.coord_systems],
            **({"geometry_errors": errors} if errors else {})}


# ------------------------------------------------------------------------------------------------
# designs and examples
# ------------------------------------------------------------------------------------------------
def t_manage_design(args):
    from ..model.design import design_from_dict
    ctx = load(args)
    action = args["action"]
    p, d = ctx.proj, ctx.des
    if action == "copy":
        i = [x.name for x in p.designs].index(d.name)
        nd = design_from_dict(json.loads(json.dumps(p.to_dict()["designs"][i])))
        names = {x.name for x in p.designs}
        nd.name = args.get("new_name") or f"{d.name}_copy"
        k = 1
        base = nd.name
        while nd.name in names:
            k += 1
            nd.name = f"{base}{k}"
        p.designs.append(nd)
        commit(Ctx(p, nd, ctx.gui), f"copy of {d.name}")
        if p.path:
            S.designs[_abspath(p.path)] = nd.name
        return {"copied": d.name, "design": nd.name}
    if action == "delete":
        if len(p.designs) < 2:
            raise ToolError("A project needs at least one design")
        p.designs = [x for x in p.designs if x.name != d.name]
        commit(Ctx(p, p.designs[0], ctx.gui), f"delete {d.name}")
        if p.path:
            S.designs[_abspath(p.path)] = p.designs[0].name
        return {"deleted": d.name, "designs": [x.name for x in p.designs]}
    raise ToolError("action is 'copy' or 'delete' (set_design renames)")


def _examples_dir() -> Path:
    return ROOT / "examples"


def t_list_examples(args):
    out = []
    for f in sorted(_examples_dir().glob("*.hzn")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            des = [x.get("name") for x in data.get("designs", [])]
            notes = next((x.get("notes") for x in data.get("designs", []) if x.get("notes")), "")
        except (OSError, ValueError):
            des, notes = [], ""
        out.append({"name": f.stem, "designs": des, **({"notes": notes[:300]} if notes else {})})
    return {"examples": out, "next": "open_example(name, path) copies one and opens it"}


def t_open_example(args):
    src = _examples_dir() / f"{Path(args['name']).stem}.hzn"
    if not src.exists():
        raise ToolError(f"No example '{args['name']}'; list_examples shows them")
    dst = _abspath(args["path"])
    if not dst.lower().endswith(".hzn"):
        dst += ".hzn"
    if Path(dst).exists() and not args.get("overwrite"):
        raise ToolError(f"{dst} exists; pass overwrite=true or choose another path")
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    S.default_path = dst
    from .tools import t_open_project
    return t_open_project({"path": dst, "open_in_gui": True})


TOOLS = [
    Tool("export", "Export: kind touchstone (S-parameters of a solution, .sNp), step (geometry), gerber (copper "
         "layers as Gerber X2, outline and Excellon drills into the folder 'path', board plane from cs/plane) or "
         "script (stand-alone EMerge Python script of the design).",
         _schema({"kind": {"type": "string", "enum": ["touchstone", "step", "gerber", "script"]},
                  "path": {"type": "string"}, "setup": {"type": "string"}, "solution": {"type": "string"},
                  "units": {"type": "string", "enum": ["mm", "inch"]}, "cs": {"type": "string"},
                  "plane": {"type": "string", "enum": ["XY", "YZ", "ZX"]},
                  "base": {"type": "string", "description": "file name base of the Gerber files"}},
                 ["kind", "path"]), t_export, True),
    Tool("import_pcb", "Import a PCB from Gerber, Excellon and job files (files, folders or zip archives): the "
         "stackup (from the job file, the design's stackup or a usual stack), the substrate in the board shape, "
         "copper on its layers, plated vias and holes. crop_mm [x0, y0, x1, y1] from the board's lower left "
         "imports only a part. arc_segments: straight segments per full circle for arcs, round pads and outlines "
         "(default 48; fewer make a lighter model).",
         _schema({"files": {"type": "array", "items": {"type": "string"}},
                  "crop_mm": {"type": "array", "items": {"type": "number"}},
                  "arc_segments": {"type": "integer"},
                  "timeout_s": {"type": "number", "description": "time limit of the copper check build (s)"}},
                 ["files"]), t_import_pcb),
    Tool("set_stackup", "PCB stackup of the design: a usual stack (copper_layers, thickness, material of the "
         "dielectrics) or explicit layers top to bottom [{name, kind copper|dielectric, thickness, material, "
         "permittivity, loss_tangent, representation Sheet|Solid}]. Makes the stk_ variables and a coordinate "
         "system per copper layer; substrate [x0, y0, xsize, ysize] builds the board boxes. remove=true removes it.",
         _schema({"copper_layers": {"type": "integer"}, "thickness": {"type": "string"},
                  "material": {"type": "string"}, "layers": {"type": "array", "items": {"type": "object"}},
                  "cs": {"type": "string"}, "plane": {"type": "string", "enum": ["XY", "YZ", "ZX"]},
                  "substrate": {"type": "array", "items": {"type": ["string", "number"]}},
                  "remove": {"type": "boolean"}}), t_set_stackup),
    Tool("manage_design", "Copy the design (action copy, new_name) or delete it (action delete).",
         _schema({"action": {"type": "string", "enum": ["copy", "delete"]}, "new_name": {"type": "string"}},
                 ["action"]), t_manage_design),
    Tool("list_examples", "The example projects that come with Hertzon (antennas, filters, RCS, FSS, PCB ...).",
         _schema({}, project=False), t_list_examples, True),
    Tool("open_example", "Copy an example project to path and open it.",
         _schema({"name": {"type": "string"}, "path": {"type": "string"}, "overwrite": {"type": "boolean"}},
                 ["name", "path"], False), t_open_example),
]
