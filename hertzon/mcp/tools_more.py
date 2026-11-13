"""MCP tools for the design assistants, the optimizers, derivative tuning and circuit designs.

- create_patch_antenna / create_hairpin_filter / create_hairpin_diplexer: the rectangular patch, hairpin filter
  and hairpin diplexer of Tools > Design Wizard, with setup, sweep and optimization, as a new design of the project
- apply_optimization: the best (or a chosen) evaluation of an optimization into the design variables, its
  solution taken over as the setup's solution (as 'Apply to Design')
- compute_tuning / get_tuning: the derivative tuning model of a solved setup and the S-parameters at other
  variable values from it (as the Derivative Tuning sliders)
- create_circuit / get_circuit / edit_circuit / circuit_layout / import_3d_designs: schematics (parts, wires,
  3D design blocks), their analysis (run_simulation) and the way to the 3D layout and back
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np

from ..model.design import Design
from . import live
from .protocol import Image, ToolError, max_wait, pause
from .tools import (ROOT, S, Ctx, _abspath, _r, _s, _schema, commit, load, sp_table)


def _new_design_ctx(ctx: Ctx, d: Design, label: str) -> dict:
    """Add design d to the project (renamed if the name is taken), make it the shown design and commit."""
    taken = {x.name for x in ctx.proj.designs}
    base, i = d.name, 1
    while d.name in taken:              # as Insert Design: Name, Name_2, Name_3 ...
        i += 1
        d.name = f"{base}_{i}"
    ctx.proj.designs.append(d)
    errors = commit(Ctx(ctx.proj, d, ctx.gui), label)
    if ctx.proj.path:
        S.designs[_abspath(ctx.proj.path)] = d.name       # the new design is shown, and used next by default
    return errors


def _substrate(ctx: Ctx, name: str | None, default: str) -> tuple[str, float, float]:
    from ..model.materials import is_conductor
    mats = {m.name: m for m in ctx.proj.all_materials()}
    name = name or default
    m = mats.get(name)
    if m is None:
        close = [k for k in mats if name.lower() in k.lower()][:10]
        raise ToolError(f"Unknown material '{name}'" + (f"; similar: {close}" if close else "; see list_materials"))
    try:
        er, tand = float(m.permittivity), float(m.loss_tangent)
    except ValueError:
        raise ToolError(f"Material '{name}': permittivity and loss tangent must be numbers") from None
    if m.pec or is_conductor(m) or er <= 1:
        raise ToolError(f"'{name}' is not a substrate (dielectric with eps_r > 1)")
    return name, er, tand


def _pair(v, what: str) -> tuple[float, float]:
    try:
        a, b = (float(x) for x in v)
    except (TypeError, ValueError):
        raise ToolError(f"{what}: give [min, max]") from None
    return a, b


# ------------------------------------------------------------------------------------------------
# design assistants
# ------------------------------------------------------------------------------------------------
def t_create_patch_antenna(args):
    from ..model.antennas import PatchSpec, patch_design
    ctx = load(args, design_required=False)
    mat, er, tand = _substrate(ctx, args.get("material"), PatchSpec.material)
    spec = PatchSpec(f0=float(args["frequency_ghz"]), match_f0=float(args.get("return_loss_db", 20.0)),
                     material=mat, er=er, tand=tand, h=float(args.get("height_mm", PatchSpec.h)),
                     t=float(args.get("copper_um", PatchSpec.t)), z_feed=float(args.get("feed_impedance", 50.0)),
                     margin=float(args.get("margin_mm", 0.0)), optimization=bool(args.get("optimization", True)),
                     name=args.get("name") or "")
    try:
        d, nom = patch_design(spec)
    except ValueError as e:
        raise ToolError(str(e)) from None
    errors = _new_design_ctx(ctx, d, f"patch antenna {d.name}")
    return {"design": d.name, "variables": d.variables,
            "synthesis": {k: _r(v, 5) for k, v in nom.items() if isinstance(v, float)},
            "warnings": nom["warnings"], **({"geometry_errors": errors} if errors else {}),
            "optimization": ({"name": d.optimetrics[0].name, "optimizer": "Derivative Trust Region",
                              "variables": d.optimetrics[0].variables, "goals": d.optimetrics[0].goals}
                             if d.optimetrics else None),
            "next": "run_simulation(design=...) for the start design, then run_simulation(optimization='PatchOpt') "
                    "and apply_optimization; switch the sweep on (set_sweep props Enabled=True) to see the "
                    "resonance afterwards"}


def t_create_hairpin_filter(args):
    from ..model.filters import HairpinSpec, hairpin_design
    ctx = load(args, design_required=False)
    mat, er, tand = _substrate(ctx, args.get("material"), HairpinSpec.material)
    kw = dict(n=int(args.get("resonators", 3)), passband=_pair(args["passband_ghz"], "passband_ghz"),
              insertion_loss=float(args.get("insertion_loss_db", 2.0)),
              return_loss=float(args.get("return_loss_db", 10.0)), ripple=float(args.get("ripple_db", 1.0)),
              rejection=float(args.get("rejection_db", 20.0)), material=mat, er=er, tand=tand,
              optimization=bool(args.get("optimization", True)), name=args.get("name") or "",
              f_design=float(args.get("design_frequency_ghz", 0.0)))
    if args.get("stopbands_ghz") is not None:
        kw["stopbands"] = [_pair(b, "stopbands_ghz") for b in args["stopbands_ghz"]]
    for key, attr in (("height_mm", "h"), ("copper_um", "t"), ("board_mm", "board"), ("region_mm", "region"),
                      ("resonator_impedance", "z_res"), ("feed_impedance", "z_feed"), ("inner_width_mm", "inner"),
                      ("min_feature_mm", "min_feature")):
        if args.get(key) is not None:
            kw[attr] = float(args[key])
    if args.get("gap_mm") is not None:
        kw["gap"] = _pair(args["gap_mm"], "gap_mm")
    if args.get("miter") is not None:
        kw["miter"] = _pair(args["miter"], "miter")
    if args.get("length_pct") is not None:
        lp = [float(x) for x in args["length_pct"]]
        if len(lp) != 3 or not lp[0] <= lp[1] <= lp[2]:
            raise ToolError("length_pct: [min, start, max] in % of the guided wavelength")
        kw["length_pct"] = tuple(lp)
    if args.get("sweep_ghz") is not None:
        sw = [float(x) for x in args["sweep_ghz"]]
        if len(sw) != 3 or not sw[1] > sw[0] or sw[2] <= 0:
            raise ToolError("sweep_ghz: [start, stop, step] with start < stop")
        kw["sweep"] = tuple(sw)
    try:
        spec = HairpinSpec(**kw)
        if spec.sweep[0] > min([spec.passband[0]] + [a for a, _ in spec.stopbands]) + 1e-9 or \
                spec.sweep[1] < max([spec.passband[1]] + [b for _, b in spec.stopbands]) - 1e-9:
            raise ValueError("The sweep must cover the passband and the stopbands (the goals are evaluated on it)")
        d, nom = hairpin_design(spec)
    except ValueError as e:
        raise ToolError(str(e)) from None
    circ = None
    if spec.optimization:
        # the circuit model of the filter, the coarse model of the space mapping optimization HairpinSM; it is
        # added first, so that the 3D design stays the shown one
        from ..model.filters import circuit_name, hairpin_circuit
        taken = {x.name for x in ctx.proj.designs}
        base, i = d.name, 1
        while d.name in taken:
            i += 1
            d.name = f"{base}_{i}"
        circ = hairpin_circuit(spec, circuit_name(d.name))
        k, stem = 1, circ.name
        while circ.name in taken:
            k += 1
            circ.name = f"{stem}_{k}"
        for o in d.optimetrics:
            if o.props.get("CoarseModel"):
                o.props["CoarseModel"] = circ.name
        ctx.proj.designs.append(circ)
    errors = _new_design_ctx(ctx, d, f"hairpin filter {d.name}")
    return {"design": d.name, **({"circuit_model": circ.name} if circ else {}), "variables": d.variables, "stopbands_ghz": spec.stopbands, "sweep_ghz": spec.sweep,
            "ranges_mm": nom["ranges"], "warnings": nom["warnings"],
            **({"geometry_errors": errors} if errors else {}),
            "optimizations": [{"name": o.name, "optimizer": o.props.get("Optimizer", "Conjugate Gradient"),
                               "variables": [v["name"] for v in o.variables if v.get("include")],
                               "goals": len(o.goals)} for o in d.optimetrics],
            "next": "run_simulation for the start design; run_simulation(optimization=...) and apply_optimization. "
                    "Fastest: HairpinSM (space mapping: the circuit model is optimized in seconds, then a few 3D "
                    "solves correct it). Near the goal, the Derivative Trust Region on 3 to 4 variables needs far "
                    "fewer solves than the conjugate gradient on all six"}


def t_create_hairpin_diplexer(args):
    from ..model.diplexer import ChannelSpec, DiplexerSpec, circuit_name, diplexer_circuit, diplexer_design
    ctx = load(args, design_required=False)
    mat, er, tand = _substrate(ctx, args.get("material"), DiplexerSpec.material)
    chans = []
    for key in ("channel_a", "channel_b", "channel_c"):
        c = args.get(key) or {}
        if key == "channel_c" and not c:
            continue
        if "passband_ghz" not in c:
            raise ToolError(f"{key}: give passband_ghz [start, stop]")
        chans.append(ChannelSpec(n=int(c.get("resonators", 3)), passband=_pair(c["passband_ghz"], "passband_ghz"),
                                 insertion_loss=float(c.get("insertion_loss_db", 2.0)),
                                 return_loss=float(c.get("return_loss_db", 12.0)),
                                 ripple=float(c.get("ripple_db", 1.0)), rejection=float(c.get("rejection_db", 15.0))))
    junction = args.get("junction", "tee")
    if junction not in ("tee", "resonator"):
        raise ToolError("junction must be 'tee' or 'resonator'")
    kw = dict(channels=chans, isolation=float(args.get("isolation_db", 25.0)), material=mat, er=er, tand=tand,
              optimization=bool(args.get("optimization", True)), name=args.get("name") or "", junction=junction)
    for key, attr in (("height_mm", "h"), ("copper_um", "t"), ("resonator_impedance", "z_res"),
                      ("feed_impedance", "z_feed"), ("inner_width_mm", "inner"), ("feed_mm", "feed"),
                      ("margin_mm", "margin"), ("min_feature_mm", "min_feature")):
        if args.get(key) is not None:
            kw[attr] = float(args[key])
    if args.get("gap_mm") is not None:
        kw["gap"] = _pair(args["gap_mm"], "gap_mm")
    if args.get("sweep_ghz") is not None:
        sw = [float(x) for x in args["sweep_ghz"]]
        if len(sw) != 3 or not sw[1] > sw[0] or sw[2] <= 0:
            raise ToolError("sweep_ghz: [start, stop, step] with start < stop")
        kw["sweep"] = tuple(sw)
    try:
        spec = DiplexerSpec(**kw)
        d, nom = diplexer_design(spec)
    except ValueError as e:
        raise ToolError(str(e)) from None
    circ = None
    if spec.optimization:
        # the diplexer as a circuit, the coarse model of DiplexerSM; added first, so that the 3D design is shown
        taken = {x.name for x in ctx.proj.designs}
        base, i = d.name, 1
        while d.name in taken:
            i += 1
            d.name = f"{base}_{i}"
        circ = diplexer_circuit(spec, circuit_name(d.name))
        k, stem = 1, circ.name
        while circ.name in taken:
            k += 1
            circ.name = f"{stem}_{k}"
        for o in d.optimetrics:
            if o.props.get("CoarseModel"):
                o.props["CoarseModel"] = circ.name
        ctx.proj.designs.append(circ)
    errors = _new_design_ctx(ctx, d, f"hairpin {'triplexer' if len(chans) == 3 else 'diplexer'} {d.name}")
    return {"design": d.name, **({"circuit_model": circ.name} if circ else {}), "variables": d.variables,
            "sweep_ghz": spec.sweep, **({"junction_lines_mm": nom["j"]} if "j" in nom else {}),
            "ranges_mm": nom["ranges"],
            "warnings": [w for n in nom["channels"] for w in n["warnings"]],
            **({"geometry_errors": errors} if errors else {}),
            "optimizations": [{"name": o.name, "optimizer": o.props.get("Optimizer", "Conjugate Gradient"),
                               "variables": [v["name"] for v in o.variables if v.get("include")],
                               "goals": len(o.goals)} for o in d.optimetrics],
            "next": "run_simulation for the start design; run_simulation(optimization='DiplexerSM') (space mapping: "
                    "the circuit is optimized first, then a few 3D solves correct it) and apply_optimization"}


# ------------------------------------------------------------------------------------------------
# design wizard (model/structures)
# ------------------------------------------------------------------------------------------------
def t_list_structures(args):
    from ..model import structures as ST
    out = []
    for st in ST.CATALOG:
        row = {"key": st.key, "title": st.title, "category": st.category, "status": st.status,
               "description": st.description, "ports": st.ports}
        if st.designer:
            row["tool"] = {"antenna_patch": "create_patch_antenna",
                           "filter_hairpin": "create_hairpin_filter",
                           "filter_diplexer": "create_hairpin_diplexer"}.get(st.designer, "")
        elif st.status == ST.AVAILABLE:
            row["fields"] = [{"key": f.key, "label": f.label, "default": f.default, "unit": f.unit,
                              "min": f.lo, "max": f.hi} | ({"choices": list(f.choices)} if f.choices else {})
                             for f in st.fields]
        if st.needs:
            row["needs"] = st.needs
        out.append(row)
    return {"categories": dict(ST.CATEGORIES), "structures": out,
            "next": "create_structure(key=..., spec={field: value}) for an available structure; the ones with a "
                    "'tool' have their own tool"}


def t_create_structure(args):
    from ..model import structures as ST
    ctx = load(args, design_required=False)
    try:
        st = ST.structure(str(args["key"]))
    except KeyError as e:
        raise ToolError(str(e)) from None
    if st.designer:
        raise ToolError(f"{st.title} has its own tool: see list_structures")
    sub = ST.Substrate()
    mat, er, tand = _substrate(ctx, args.get("material"), sub.material)
    sub = ST.Substrate(material=mat, er=er, tand=tand, h=float(args.get("height_mm", sub.h)),
                       t=float(args.get("copper_um", sub.t)), min_feature=float(args.get("min_feature_mm",
                                                                                           sub.min_feature)))
    try:
        r = st.create(args.get("spec") or {}, sub, name=args.get("name") or "",
                      range_pct=float(args.get("range_pct", 20.0)),
                      multi_fidelity=bool(args.get("multi_fidelity", True)), project=ctx.proj)
    except ValueError as e:
        raise ToolError(str(e)) from None
    known = {m.name for m in ctx.proj.materials}
    ctx.proj.materials.extend(m for m in r.materials if m.name not in known)
    if len(r.designs) == 1:                    # an antenna: the 3D design holds the optimization
        d3 = r.designs[0]
        errors = _new_design_ctx(ctx, d3, f"{st.title} {d3.name}")
        return {"design_3d": d3.name, "optimization": r.optimization,
                "variables": [{"name": v.name, "start": v.text(), "min": v.text(v.lo), "max": v.text(v.hi),
                               "meaning": v.meaning, "optimized": v.include} for v in r.synthesis.variables],
                "synthesis": r.synthesis.note, "goals": d3.optimetrics[0].goals, "warnings": r.warnings,
                **({"geometry_errors": errors} if errors else {}),
                "next": f"run_simulation(design='{d3.name}', optimization='{r.optimization}') tunes the design on the "
                        "adaptive solution at the frequency (a few 3D solves); then apply_optimization"}
    circ, d3 = r.designs
    ctx.proj.designs.append(d3)                # the circuit is added last: it holds the optimization, shown next
    errors = _new_design_ctx(ctx, circ, f"{st.title} {circ.name}")
    return {"circuit": circ.name, "design_3d": d3.name, "optimization": r.optimization,
            "variables": [{"name": v.name, "start": v.text(), "min": v.text(v.lo), "max": v.text(v.hi),
                           "meaning": v.meaning, "optimized": v.include} for v in r.synthesis.variables],
            "synthesis": r.synthesis.note, "goals": circ.optimetrics[0].goals, "warnings": r.warnings,
            **({"geometry_errors": errors} if errors else {}),
            "next": f"run_simulation(design='{circ.name}') for the circuit start values; "
                    f"run_simulation(design='{circ.name}', optimization='{r.optimization}') optimizes on the "
                    "circuit and corrects with a few 3D solves of the 3D design (multi-fidelity); then "
                    "apply_optimization"}


# ------------------------------------------------------------------------------------------------
# optimization result into the design
# ------------------------------------------------------------------------------------------------
def t_apply_optimization(args):
    from ..solver.parametric import adopt_evaluation, evaluation_dir, optimization_setup
    from ..solver.results import _safe, studies_dir
    ctx = load(args)
    d = ctx.des
    name = args["optimization"]
    op = next((o for o in d.optimetrics if o.name == name), None)
    if op is None:
        raise ToolError(f"No optimization named '{name}'; optimizations: {[o.name for o in d.optimetrics]}")
    if not ctx.proj.path:
        raise ToolError("The project is not saved yet")
    hist = studies_dir(ctx.proj.path, d.name) / _safe(name) / "history.json"
    try:
        rows = json.loads(hist.read_text(encoding="utf-8")).get("evaluations", [])
    except (OSError, ValueError):
        rows = []
    ok = [r for r in rows if r.get("cost") is not None]
    if not ok:
        raise ToolError(f"{name} has no evaluated design yet (run_simulation(optimization=...))")
    if args.get("evaluation") is not None:
        row = next((r for r in rows if r.get("eval") == int(args["evaluation"])), None)
        if row is None or row.get("cost") is None:
            raise ToolError(f"Evaluation {args['evaluation']} of {name} has no result")
    else:
        row = min(ok, key=lambda r: r["cost"])
    if ctx.gui is not None:
        ctx.gui.call("apply_evaluation", design=d.name, optimization=name, row=row)
        return {"applied": row["variables"], "evaluation": row["eval"], "cost": row["cost"], "in_window": True}
    pv = ctx.proj.variables
    for k, v in row["variables"].items():
        if k in pv and k not in d.variables:
            pv[k] = v
        else:
            d.variables[k] = v
    commit(ctx, f"apply {name}")
    res = {"applied": row["variables"], "evaluation": row["eval"], "cost": row["cost"]}
    setup = optimization_setup(op)
    if d.kind == "circuit":                     # analyzing the circuit again takes milliseconds
        from ..solver.circuit import CircuitError, run_setup
        if any(s.name == setup for s in d.setups):
            try:
                run_setup(ctx.proj, d, d.setup(setup))
                res["solution"] = f"{setup} analyzed with the applied values"
            except CircuitError as e:
                res["solution"] = f"{setup} could not be analyzed: {e}"
        return res
    folder = evaluation_dir(ctx.proj.path, d.name, name, row["eval"])
    if any(s.name == setup for s in d.setups) and folder.exists():
        try:
            adopt_evaluation(ctx.proj, d, setup, folder)
            res["solution"] = f"the solution of evaluation {row['eval']} is now the solution of {setup}"
        except Exception as e:  # noqa: BLE001
            res["solution"] = f"not taken over ({e}); analyze {setup} again"
    return res


# ------------------------------------------------------------------------------------------------
# derivative tuning
# ------------------------------------------------------------------------------------------------
def _tuning_target(ctx: Ctx, args) -> tuple[str, str, Path]:
    from ..solver.results import results_dir
    from ..solver.tuning import ADAPTIVE, tuning_path
    d = ctx.des
    if not d.setups:
        raise ToolError("The design has no analysis setup")
    if not ctx.proj.path:
        raise ToolError("The project is not saved yet")
    setup = args.get("setup") or d.setups[0].name
    st = next((s for s in d.setups if s.name == setup), None)
    if st is None:
        raise ToolError(f"Setup '{setup}' not found")
    sweep = args.get("sweep") or ADAPTIVE
    if sweep != ADAPTIVE and not any(w.name == sweep for w in st.sweeps):
        raise ToolError(f"Sweep '{sweep}' not found; sweeps: {[w.name for w in st.sweeps]} or {ADAPTIVE}")
    out = results_dir(ctx.proj.path, d.name, setup)
    return setup, sweep, tuning_path(out, sweep)


def t_compute_tuning(args):
    from ..solver.optimetrics import split_value
    from ..solver.results import results_dir, run_status
    ctx = load(args)
    d = ctx.des
    setup, sweep, path = _tuning_target(ctx, args)
    variables = list(args.get("variables") or [])
    if not 1 <= len(variables) <= 6:
        raise ToolError("Give 1 to 6 design variables")
    for v in variables:
        if v not in d.variables or split_value(d.variables[v]) is None:
            ok = [k for k, x in d.variables.items() if split_value(x) is not None]
            raise ToolError(f"'{v}' is not a design variable with a plain value; tunable: {ok}")
    points = int(args.get("points") or 51)
    if run_status(results_dir(ctx.proj.path, d.name, setup)).get("state") != "done":
        raise ToolError(f"Analyze {setup} first (with the current design): the derivatives are computed on its "
                        "adaptive mesh")
    tune = json.dumps({"sweep": sweep, "variables": variables, "points": points})
    if ctx.gui is not None:
        r = ctx.gui.call("tuning", design=d.name, setup=setup, tuning=tune)
        return {"started": True, "in_window": True, **(r or {}),
                "next": "get_tuning with wait_seconds until the model is there, then get_tuning(values=...)"}
    p = S.procs.get(str(path))
    if p is not None and p.poll() is None:
        raise ToolError("This tuning model is being computed already")
    path.unlink(missing_ok=True)
    out = path.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1", HERTZON_TUNING=tune,
               HERTZON_MESH_ONLY="0", HERTZON_ONLY_SWEEP="")
    n = int(args.get("cores") or 0) or (os.cpu_count() or 1)
    env["HERTZON_CORES"] = str(n)
    from .clients import python_exe
    log = open(out / "tuning.log", "wb")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
    S.procs[str(path)] = subprocess.Popen([python_exe(), "-m", "hertzon.solver.run", ctx.proj.path, d.name, setup,
                                           str(out)], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                          env=env, cwd=str(Path(ctx.proj.path).parent), **kw)
    log.close()
    return {"started": True, "in_window": False, "setup": setup, "sweep": sweep, "variables": variables,
            "next": "get_tuning with wait_seconds until the model is there, then get_tuning(values=...)"}


def _tuning_state(ctx: Ctx, setup: str, sweep: str, path: Path) -> str:
    p = S.procs.get(str(path))
    if p is not None and p.poll() is None:
        return "running"
    if ctx.gui is not None:
        try:
            q = ctx.gui.call("queue", timeout=10)
        except live.LinkError:
            q = {}
        key = f"{Path(ctx.proj.path).stem} - {ctx.des.name} - {setup} : tuning {sweep}"
        if q.get("running") == key:
            return "running"
        if key in (q.get("queued") or []):
            return "queued"
    if path.exists():
        return "done"
    return "failed" if p is not None else "not computed"


def t_get_tuning(args):
    from ..solver.optimetrics import in_unit
    from ..solver.tuning import TuningModel
    ctx = load(args)
    d = ctx.des
    setup, sweep, path = _tuning_target(ctx, args)
    wait = max_wait(args.get("wait_seconds"))
    t0 = time.time()
    while True:
        state = _tuning_state(ctx, setup, sweep, path)
        if state not in ("running", "queued") or time.time() - t0 >= wait:
            break
        if not pause(min(3.0, max(0.2, wait - (time.time() - t0)))):
            break
    if state != "done":
        res = {"setup": setup, "sweep": sweep, "state": state}
        logf = path.parent / "tuning.log"
        if state == "failed" and logf.exists():
            res["log"] = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        return res
    try:
        m = TuningModel(path)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"The tuning model could not be read ({e}); compute it again") from None
    ev = d.evaluator(ctx.proj)
    dp = np.zeros(len(m.variables))
    notes = []
    vals = dict(args.get("values") or {})
    for k, v in vals.items():
        if k not in m.variables:
            raise ToolError(f"'{k}' is not in the tuning model; its variables: {m.variables}")
        i = m.variables.index(k)
        try:
            x = in_unit(_s(v), m.units[i], ev)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"{k}: {e}") from None
        dp[i] = x - m.values[i]
        lo, hi = m.ranges[i]
        if not lo - 1e-12 <= dp[i] <= hi + 1e-12:
            notes.append(f"{k} = {_s(v)} is outside the tuning range: the result is an extrapolation")
    Sm, _ = m.sparams(dp)
    out = {"setup": setup, "sweep": sweep, "state": "done",
           "variables": [{"name": n, "solved": f"{m.values[i]:.6g}{m.units[i]}",
                          "range": f"{m.values[i] + m.ranges[i][0]:.6g} .. {m.values[i] + m.ranges[i][1]:.6g} "
                                   f"{m.units[i]}",
                          "value": f"{m.values[i] + dp[i]:.6g}{m.units[i]}"} for i, n in enumerate(m.variables)],
           "rows": sp_table(m.freq_out, np.asarray(Sm), args, ev)}
    cur = [n for n in m.variables if d.variables.get(n) != f"{m.values[m.variables.index(n)]:g}{m.units[m.variables.index(n)]}"]
    if cur and not vals:
        out["note"] = "the design variables differ from the solved values of the model"
    if notes:
        out["warnings"] = notes
    out["hint"] = "set_design(variables=...) applies values; analyze again to confirm them"
    return out


# ------------------------------------------------------------------------------------------------
# circuits
# ------------------------------------------------------------------------------------------------
def _circuit(ctx: Ctx) -> Design:
    if getattr(ctx.des, "kind", "3d") != "circuit":
        circs = [x.name for x in ctx.proj.designs if getattr(x, "kind", "3d") == "circuit"]
        raise ToolError(f"'{ctx.des.name}' is not a circuit design; circuit designs: {circs} (create_circuit)")
    return ctx.des


def _pin_ref(d: Design, ref: str) -> tuple[int, int]:
    from ..model import circuit as CK
    name, _, k = str(ref).partition(":")
    c = next((x for x in d.components if x.name == name), None)
    if c is None:
        raise ToolError(f"No part '{name}' (pin references are 'Part:pin', e.g. 'MS1:2')")
    ps = CK.pins(c)
    try:
        i = int(k or 1) - 1
    except ValueError:
        raise ToolError(f"'{ref}': the pin is a number from 1") from None
    if not 0 <= i < len(ps):
        raise ToolError(f"{name} has {len(ps)} pin(s)")
    return ps[i]


def _snap(v) -> int:
    from ..model.circuit import GRID
    return int(round(float(v) / GRID)) * GRID


def circuit_summary(proj, d: Design, library: bool = False) -> dict:
    from ..model import circuit as CK
    from ..model.circuit_layout import LINK
    ids, _ = CK.nets(d.components, d.wires)
    nets: dict[int, list[str]] = {}
    parts = []
    for c in d.components:
        ps = CK.pins(c)
        pn = []
        for k, q in enumerate(ps):
            node = ids.get(q)
            pn.append(node)
            if node is not None:
                nets.setdefault(node, []).append(f"{c.name}:{k + 1}")
        parts.append({"name": c.name, "type": c.type, "x": c.x, "y": c.y, "rot": c.rot,
                      **({"mirror": True} if c.mirror else {}), "props": c.props, "pins": [list(q) for q in ps],
                      "pin_nets": pn})
    open_pins = [p for p in sum((v for v in nets.values() if len(v) == 1), [])
                 if not p.split(":")[0].startswith(("GND",))]
    out = {"design": d.name, "kind": "circuit", "settings": d.settings, "variables": d.variables,
           "parts": parts, "wires": [{"index": i, "points": w.points} for i, w in enumerate(d.wires)],
           "nets": {("ground" if k == 0 else f"n{k}"): v for k, v in sorted(nets.items())},
           "unconnected_pins": open_pins,
           "ports": [c.name for c in CK.port_order(d.components)],
           "setups": [{"name": s.name, "sweeps": [{"name": w.name, "props": w.props} for w in s.sweeps]}
                      for s in d.setups],
           "layouts": [x.name for x in proj.designs if x.settings.get(LINK) == d.name],
           "em_designs": [x.name for x in proj.designs if getattr(x, "kind", "3d") == "3d" and x.excitations]}
    if library:
        out["library"] = circuit_library()
    return out


def circuit_library() -> dict:
    from ..model import circuit as CK
    return {k: {"label": t.label, "category": t.category, "props": t.props,
                "pins": [list(p) for p in t.pins] or "one per port (N-port block)", "help": t.help}
            for k, t in CK.TYPES.items()}


def t_create_circuit(args):
    from ..model import circuit as CK
    from ..model.design import circuit_setup
    ctx = load(args, design_required=False)
    d = Design(name=args.get("name") or "Circuit1", kind="circuit")
    d.setups = [circuit_setup()]
    if args.get("substrate", True):
        d.components.append(CK.Component("Sub1", "SUBST", -200, -150, 0, dict(CK.TYPES["SUBST"].props)))
    _new_design_ctx(ctx, d, f"circuit {d.name}")
    return {**circuit_summary(ctx.proj, d, library=bool(args.get("include_library", True))),
            "next": "edit_circuit to place parts and connect pins; run_simulation; get_results kind=sparameters"}


def t_get_circuit(args):
    ctx = load(args)
    return circuit_summary(ctx.proj, _circuit(ctx), bool(args.get("include_library")))


def _part_props(ctype: str, props: dict | None, base: dict) -> dict:
    from ..model import circuit as CK
    out = dict(base)
    allowed = set(CK.TYPES[ctype].props) | ({"Ports"} if ctype in CK.NPORT_TYPES else set())
    for k, v in (props or {}).items():
        if k not in allowed:
            raise ToolError(f"{ctype} has no property '{k}'. Properties: {sorted(allowed)}")
        out[k] = _s(v)
    return out


def _em_ports(ctx: Ctx, c) -> None:
    """A 3D design block gets one pin per port of its design."""
    if c.type != "EM":
        return
    name = c.props.get("Design", "")
    src = next((x for x in ctx.proj.designs if x.name == name and getattr(x, "kind", "3d") == "3d"), None)
    if src is None:
        raise ToolError(f"{c.name}: Design must name a 3D design of the project; 3D designs: "
                        f"{[x.name for x in ctx.proj.designs if getattr(x, 'kind', '3d') == '3d']}")
    n = len(src.ports())
    if not n:
        raise ToolError(f"{c.name}: {name} has no ports")
    c.props["Ports"] = str(n)


def _place(d: Design, c, spec: dict) -> None:
    """Position of a part: x/y, or 'attach' = 'Part:pin' puts its pin 'pin' (default 1) on that pin."""
    from ..model import circuit as CK
    if spec.get("attach"):
        tx, ty = _pin_ref(d, spec["attach"])
        offs = [(dx, -dy if c.mirror else dy) for dx, dy in CK.pin_offsets(c)]
        k = int(spec.get("pin", 1)) - 1
        if not 0 <= k < len(offs):
            raise ToolError(f"{c.name} has {len(offs)} pin(s)")
        rx, ry = CK.rotate(*offs[k], c.rot)
        c.x, c.y = tx - rx, ty - ry
    else:
        if "x" in spec:
            c.x = _snap(spec["x"])
        if "y" in spec:
            c.y = _snap(spec["y"])


def _route(a: tuple[int, int], b: tuple[int, int], via: list | None = None) -> list[list[int]]:
    pts = [list(a)]
    for q in via or []:
        pts.append([_snap(q[0]), _snap(q[1])])
    last = pts[-1]
    if last[0] != b[0] and last[1] != b[1]:
        pts.append([b[0], last[1]])
    pts.append(list(b))
    out = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
    return out


def t_edit_circuit(args):
    from ..model import circuit as CK
    ctx = load(args)
    d = _circuit(ctx)
    changed = []
    for name in args.get("remove") or []:
        c = next((x for x in d.components if x.name == name), None)
        if c is None:
            raise ToolError(f"No part '{name}'")
        d.components.remove(c)
        changed.append(f"removed {name}")
    if args.get("remove_wires"):
        idx = sorted({int(i) for i in args["remove_wires"]}, reverse=True)
        for i in idx:
            if not 0 <= i < len(d.wires):
                raise ToolError(f"No wire {i}; wires 0..{len(d.wires) - 1}")
            del d.wires[i]
        changed.append(f"removed wire(s) {sorted(idx)}")
    for spec in args.get("add") or []:
        ctype = spec.get("type")
        if ctype not in CK.TYPES:
            raise ToolError(f"Unknown part type '{ctype}'; types: {list(CK.TYPES)} (get_circuit include_library)")
        name = spec.get("name") or CK.next_name(d.components, ctype)
        if any(x.name == name for x in d.components):
            raise ToolError(f"A part named '{name}' exists")
        c = CK.Component(name, ctype, 0, 0, int(spec.get("rot", 0)) % 360, {}, bool(spec.get("mirror", False)))
        if c.rot % 90:
            raise ToolError("rot is 0, 90, 180 or 270")
        c.props = _part_props(ctype, spec.get("props"), dict(CK.TYPES[ctype].props))
        _em_ports(ctx, c)
        if ctype == "SNP":
            f = c.props.get("File", "")
            if f:
                c.props["File"] = _abspath(f)
                import re
                m = re.search(r"\.s(\d+)p$", f.lower())
                if m:
                    c.props["Ports"] = m.group(1)
        _place(d, c, spec)
        d.components.append(c)
        changed.append(f"added {name}")
    for spec in args.get("modify") or []:
        c = next((x for x in d.components if x.name == spec.get("name")), None)
        if c is None:
            raise ToolError(f"No part '{spec.get('name')}'")
        if "rot" in spec:
            c.rot = int(spec["rot"]) % 360
        if "mirror" in spec:
            c.mirror = bool(spec["mirror"])
        if spec.get("props"):
            c.props = _part_props(c.type, spec["props"], c.props)
            _em_ports(ctx, c)
        _place(d, c, spec)
        if spec.get("new_name"):
            if any(x.name == spec["new_name"] for x in d.components):
                raise ToolError(f"A part named '{spec['new_name']}' exists")
            c.name = spec["new_name"]
        changed.append(f"changed {c.name}")
    for w in args.get("connect") or []:
        a, b = _pin_ref(d, w["from"]), _pin_ref(d, w["to"])
        if a != b:
            d.wires.append(CK.Wire(_route(a, b, w.get("via"))))
        changed.append(f"connected {w['from']} - {w['to']}")
    for w in args.get("wires") or []:
        pts = [[_snap(p[0]), _snap(p[1])] for p in w]
        if len(pts) < 2:
            raise ToolError("A wire needs at least two points")
        d.wires.append(CK.Wire(pts))
        changed.append("added a wire")
    for k, v in (args.get("settings") or {}).items():
        if k == CK.AUTO:
            d.settings[k] = "False" if str(v).lower() in ("false", "0", "no") else "True"
        elif k == CK.LINE_MODEL:
            if v not in ("Closed form", "Full wave"):
                raise ToolError("LineModel is 'Closed form' or 'Full wave'")
            d.settings[k] = v
        else:
            raise ToolError(f"Circuit settings: {CK.AUTO}, {CK.LINE_MODEL}")
        changed.append(f"{k} = {d.settings[k]}")
    for k, v in (args.get("variables") or {}).items():
        if v is None:
            d.variables.pop(k, None)
        else:
            d.variables[k] = _s(v)
    commit(ctx, "circuit edit")
    res = circuit_summary(ctx.proj, d)
    res["changed"] = changed
    return res


def analyze_circuit_file(ctx: Ctx, setups: list[str]) -> dict:
    """Solve a circuit design in this process (a linear network is solved at once) and write its results."""
    from ..solver import circuit as SC
    from ..solver import xsection as XS
    d = ctx.des
    if not ctx.proj.path:
        raise ToolError("Save the project first")
    ctx.proj.save(ctx.proj.path)
    if SC.full_wave(d):
        try:
            xs = list(SC.cross_sections(ctx.proj, d).values())
            fm = SC.max_frequency(ctx.proj, d)
        except SC.CircuitError as e:
            raise ToolError(str(e)) from None
        todo = XS.missing(xs, fm, ctx.proj.path) if xs and fm > 0 else []
        if todo:
            from .clients import python_exe
            XS.solve(todo, fm, ctx.proj.path, python=python_exe())
    out = {}
    for s in setups:
        t = time.time()
        try:
            _, warns = SC.run_setup(ctx.proj, d, d.setup(s))
        except SC.CircuitError as e:
            raise ToolError(f"{s}: {e}") from None
        out[s] = {"state": "done", "seconds": round(time.time() - t, 2), **({"warnings": warns} if warns else {})}
    return out


def t_circuit_layout(args):
    from ..model import circuit_layout as CL
    ctx = load(args)
    action = args.get("action", "create")
    proj = ctx.proj
    if action == "create":
        circ = _circuit(ctx)
        try:
            d, warns = CL.layout_design(proj, circ, args.get("name") or None)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"No layout could be made: {e}") from None
        errors = _new_design_ctx(ctx, d, f"3D layout of {circ.name}")
        return {"layout": d.name, "of": circ.name, "warnings": warns, "variables": d.variables,
                **({"geometry_errors": errors} if errors else {}),
                "next": "get_project(design=layout) / render_image; run_simulation(design=layout); after changing "
                        "the layout variables circuit_layout(action='to_schematic') copies them back"}
    if ctx.des.kind == "circuit":
        circ = ctx.des
        names = [x.name for x in proj.designs if x.settings.get(CL.LINK) == circ.name]
        lname = args.get("layout") or (names[-1] if names else None)
        if lname is None:
            raise ToolError(f"{circ.name} has no 3D layout yet (action='create')")
    else:
        lname = args.get("layout") or ctx.des.name
    try:
        lay = proj.design(lname)
    except KeyError:
        raise ToolError(f"No design '{lname}'") from None
    if action == "update":
        try:
            warns = CL.update_layout(proj, lay)
        except Exception as e:  # noqa: BLE001
            raise ToolError(str(e)) from None
        errors = commit(Ctx(proj, lay, ctx.gui), f"rebuild {lname}")
        return {"layout": lname, "rebuilt": True, "warnings": warns, "note": "its solutions are out of date",
                **({"geometry_errors": errors} if errors else {})}
    if action == "to_schematic":
        try:
            circ, changes = CL.update_schematic(proj, lay)
        except CL.LayoutError as e:
            raise ToolError(str(e)) from None
        commit(Ctx(proj, circ, ctx.gui), f"update {circ.name} from {lname}")
        S.designs[_abspath(proj.path)] = circ.name if proj.path else None
        return {"circuit": circ.name, "from": lname, "changes": changes}
    raise ToolError("action is 'create', 'update' or 'to_schematic'")


def t_import_3d_designs(args):
    from ..model.circuit_embed import import_3d_designs
    ctx = load(args, design_required=False)
    path = _abspath(args["file"])
    if not Path(path).exists():
        raise ToolError(f"File not found: {path}")
    try:
        names, warns = import_3d_designs(ctx.proj, path)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"{type(e).__name__}: {e}") from None
    if not names:
        raise ToolError("The file has no 3D design with ports")
    commit(ctx, f"3D designs from {Path(path).name}")
    return {"imported": names, "warnings": warns,
            "next": "edit_circuit add [{type: 'EM', props: {Design: name}}] places one as a block"}


# ------------------------------------------------------------------------------------------------
# edge-launch connectors at lumped ports
def _connector_summary(ctx: Ctx) -> dict:
    from ..model import connectors as CN
    out = []
    for c in ctx.des.connectors:
        row = {"name": c.name, "part": c.part, "label": CN.label(CN.part(c.part)), "port": c.port, "mode": c.mode,
               "variables": {v: ctx.des.variables.get(v) for v in CN.parts_of(c).variables},
               "wave_port": c.props.get("WavePort")}
        if c.mode == "Transition":
            row["transition_design"] = c.props.get("Transition")
            row["transition_state"] = CN.transition_state(ctx.proj, ctx.des, c)
        out.append(row)
    res = {"design": ctx.des.name, "connectors": out, "modes": CN.MODE_LABELS}
    if any(c.mode == "Transition" for c in ctx.des.connectors):
        res["cascade_design"] = CN.cascade_name(ctx.des)
        res["solve_first"] = CN.designs_to_solve(ctx.proj, ctx.des)
        res["cascade_problems"] = CN.cascade_problems(ctx.proj, ctx.des)
    return res


def t_list_edge_connectors(args):
    from ..model import connectors as CN
    it = args.get("interface")
    if it and it not in CN.INTERFACES:
        raise ToolError(f"interface: one of {list(CN.INTERFACES)}")
    q = str(args.get("search") or "").lower()
    h = args.get("board_mm")
    rows = []
    for p in CN.parts(it):
        text = " ".join(str(p.get(k, "")) for k in ("manufacturer", "part_number", "mounting", "notes")).lower()
        if q and q not in text:
            continue
        rng = [x for x in (p.get("pcb_thickness_mm") or []) if x is not None]
        if h is not None and len(rng) >= 2 and not rng[0] - 0.05 <= float(h) <= rng[1] + 0.05:
            continue
        rows.append({"id": p["id"], "label": CN.label(p), "mounting": p.get("mounting"), "f_max_GHz": CN.f_max(p),
                     "board_mm": rng or None, "launch_pin_mm": p.get("launch_pin_od_mm"),
                     "datasheet": p.get("datasheet_url")})
    return {"count": len(rows), "parts": rows[:int(args.get("limit") or 60)],
            "next": "attach_edge_connector port=<lumped port> part=<id>"}


def t_attach_edge_connector(args):
    from ..model import connectors as CN
    from .tools import tessellate
    ctx = load(args)
    if ctx.des.kind != "3d":
        raise ToolError("Connectors go into a 3D design")
    tess, errors = tessellate(ctx.proj, ctx.des)
    try:
        inst = CN.attach_connector(ctx.proj, ctx.des, args["port"], args["part"], CN.bboxes_from_tess(tess),
                                   args.get("name") or "J1")
        warns = [w for w in inst.props.get("Warnings", "").split(" | ") if w]
        mode = args.get("mode") or "Ideal"
        if mode != "Ideal":
            warns += CN.set_mode(ctx.proj, ctx.des, mode, [inst.name])
    except (KeyError, ValueError) as e:
        raise ToolError(str(e)) from None
    errs = commit(ctx, f"connector {inst.name} at {args['port']}")
    return {**_connector_summary(ctx), "warnings": warns, "geometry_errors": errs,
            "next": "set_connector_mode to switch between Ideal, Transition and 3D"}


def t_set_connector_mode(args):
    from ..model import connectors as CN
    ctx = load(args)
    names = args.get("connectors") or None
    action = args.get("action") or "mode"
    try:
        warns = []
        if action == "mode":
            warns = CN.set_mode(ctx.proj, ctx.des, args.get("mode") or "Ideal", names)
        elif action == "flatten":
            for n in names or [c.name for c in ctx.des.connectors]:
                warns += CN.flatten(ctx.proj, ctx.des, n)
        elif action == "remove":
            for n in names or []:
                CN.remove_connector(ctx.proj, ctx.des, n)
        elif action == "optimize_launch":
            for n in names or [c.name for c in ctx.des.connectors]:
                t = CN.ensure_transition(ctx.proj, ctx.des, CN.instance(ctx.des, n))
                op = CN.add_launch_optimization(t, rl_db=float(args.get("return_loss_db") or 20))
                warns.append(f"{n}: optimization '{op.name}' in {t.name}; run it there, then action "
                             "apply_footprint")
        elif action == "apply_footprint":
            for n in names or [c.name for c in ctx.des.connectors]:
                vals = CN.pull_footprint(ctx.proj, ctx.des, CN.instance(ctx.des, n))
                warns.append(f"{n}: {vals}")
        else:
            raise ToolError("action: mode, flatten, remove, optimize_launch or apply_footprint")
    except (KeyError, ValueError) as e:
        raise ToolError(str(e)) from None
    errs = commit(ctx, f"connectors: {action}")
    return {**_connector_summary(ctx), "warnings": warns, "geometry_errors": errs}


def t_get_connectors(args):
    return _connector_summary(load(args))


def render_schematic(proj, d: Design, w: int, h: int) -> list:
    """Picture of a schematic: parts as labelled boxes with their pins, wires, nets of the pins."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from ..model import circuit as CK
    fig = Figure(figsize=(w / 100, h / 100), dpi=100)
    ax = fig.add_subplot(111)
    for wi, wr in enumerate(d.wires):
        xs, ys = zip(*wr.points)
        ax.plot(xs, [-y for y in ys], color="#2c7a2c", lw=1.4, zorder=1)
    for c in d.components:
        ps = CK.pins(c)
        if ps:
            xs = [p[0] for p in ps] + [c.x]
            ys = [p[1] for p in ps] + [c.y]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        else:
            x0, x1, y0, y1 = c.x - 30, c.x + 30, c.y - 15, c.y + 15
        pad = 8
        col = {"PORT": "#c0392b", "GND": "#555555", "SUBST": "#7f8c5a", "EM": "#2e6da4", "SNP": "#2e6da4"}.get(
            c.type, "#b9770e")
        ax.add_patch(matplotlib.patches.Rectangle((x0 - pad, -(y1 + pad)), x1 - x0 + 2 * pad, y1 - y0 + 2 * pad,
                                                  fill=False, ec=col, lw=1.2, zorder=2))
        ax.text((x0 + x1) / 2, -(y0 - pad - 4), f"{c.name} ({c.type})", ha="center", va="bottom", fontsize=7,
                color=col)
        for k, q in enumerate(ps):
            ax.plot([q[0]], [-q[1]], "o", ms=3.5, color=col, zorder=3)
            ax.annotate(str(k + 1), (q[0], -q[1]), xytext=(3, 3), textcoords="offset points", fontsize=6, color=col)
    ax.set_aspect("equal")
    ax.autoscale()
    ax.margins(0.08)
    ax.set_title(f"{d.name} (schematic, y up = -y of the grid)", fontsize=9)
    ax.tick_params(labelsize=7)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return [Image(buf.getvalue(), f"schematic of {d.name}: {len(d.components)} parts, {len(d.wires)} wires")]


# ------------------------------------------------------------------------------------------------
# registry
# ------------------------------------------------------------------------------------------------
from .protocol import Tool  # noqa: E402

NUM = {"type": "number"}
RANGE = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
VALUES = {"type": "object", "description": "variable -> value ('1.2mm' or a number in the variable's unit)",
          "additionalProperties": {"type": ["string", "number"]}}
PART = {"type": "object", "properties": {
    "type": {"type": "string", "description": "part type (get_circuit include_library): PORT, GND, R, L, C, MLIN, "
                                              "MLOC, MTEE, SUBST, TLIN, EM (3D design), SNP (Touchstone) ..."},
    "name": {"type": "string"}, "x": NUM, "y": NUM, "rot": {"type": "integer", "enum": [0, 90, 180, 270]},
    "mirror": {"type": "boolean"}, "props": {"type": "object"},
    "attach": {"type": "string", "description": "'Part:pin': place the part so that its pin 'pin' sits on this "
                                               "pin (connected without a wire)"},
    "pin": {"type": "integer", "description": "pin of this part for attach (default 1)"},
    "new_name": {"type": "string"}}}

TOOLS = [
    Tool("create_patch_antenna", "Tools > Design Wizard > Rectangular patch: an inset-fed rectangular microstrip patch from the "
         "transmission line model as a new parametrized design (variables Wp, Lp, inset, wf ...), with setup, "
         "far-field sphere, a switched-off sweep and the optimization 'PatchOpt' of Lp and inset on the return loss "
         "at the frequency (Derivative Trust Region: a few solves with the derivative tuning model).",
         _schema({"frequency_ghz": NUM, "return_loss_db": {"type": "number", "description": "goal at the frequency "
                                                                                          "(default 20)"},
                  "material": {"type": "string", "description": "substrate (default FR4_epoxy)"},
                  "height_mm": NUM, "copper_um": NUM, "feed_impedance": NUM,
                  "margin_mm": {"type": "number", "description": "ground beyond the patch; 0 automatic"},
                  "optimization": {"type": "boolean"}, "name": {"type": "string"}}, ["frequency_ghz"]),
         t_create_patch_antenna),
    Tool("create_hairpin_filter", "Tools > Design Wizard > Hairpin bandpass: a tapped hairpin band-pass filter of N resonators as a new "
         "parametrized design (L, w, b, g, yP, M), with setup, sweep and the optimization 'HairpinCG' on the "
         "filter mask (insertion loss, ripple, return loss in the passband, rejection in the stopbands).",
         _schema({"passband_ghz": RANGE, "resonators": {"type": "integer", "description": "2 to 9 (default 3)"},
                  "stopbands_ghz": {"type": "array", "items": RANGE,
                                    "description": "default: from the passband"},
                  "insertion_loss_db": NUM, "return_loss_db": NUM, "ripple_db": NUM, "rejection_db": NUM,
                  "material": {"type": "string", "description": "substrate (default Rogers RO4350B (tm))"},
                  "height_mm": NUM, "copper_um": NUM, "board_mm": NUM, "region_mm": NUM,
                  "resonator_impedance": NUM, "feed_impedance": NUM, "inner_width_mm": NUM, "gap_mm": RANGE,
                  "miter": RANGE, "length_pct": {"type": "array", "items": NUM,
                                                 "description": "[min, start, max] % of the guided wavelength"},
                  "min_feature_mm": NUM, "sweep_ghz": {"type": "array", "items": NUM,
                                                       "description": "[start, stop, step]; default from the passband"},
                  "design_frequency_ghz": NUM, "optimization": {"type": "boolean"}, "name": {"type": "string"}},
                 ["passband_ghz"]), t_create_hairpin_filter),
    Tool("create_hairpin_diplexer", "Tools > Design Wizard > Hairpin diplexer: two tapped hairpin filters on a "
         "T-junction (or a common resonator) as a new parametrized design, or with channel_c a triplexer on a star "
         "junction (common Port 1; channel A to Port 2, B to Port 3, C to Port 4; variables L_A, g_A, yP_A, j_A ... "
         "per channel), with the same design as a circuit and the space mapping optimization 'DiplexerSM' on the "
         "channel masks and the isolation.",
         _schema({"channel_a": {"type": "object", "description": "passband_ghz [start, stop] (required), resonators, "
                                "insertion_loss_db, return_loss_db, ripple_db, rejection_db"},
                  "channel_b": {"type": "object", "description": "as channel_a"},
                  "channel_c": {"type": "object", "description": "as channel_a; makes a triplexer (star junction, "
                                                                 "channel C to Port 4)"},
                  "isolation_db": NUM,
                  "junction": {"type": "string", "enum": ["tee", "resonator"],
                               "description": "tee (default): T-junction and lines; resonator: a common hairpin "
                                              "between the channels, tapped by Port 1 (smaller)"},
                  "material": {"type": "string", "description": "substrate (default Rogers RO4350B (tm))"},
                  "height_mm": NUM, "copper_um": NUM, "resonator_impedance": NUM, "feed_impedance": NUM,
                  "inner_width_mm": NUM, "gap_mm": RANGE, "feed_mm": NUM, "margin_mm": NUM, "min_feature_mm": NUM,
                  "sweep_ghz": {"type": "array", "items": NUM,
                                "description": "[start, stop, step]; default from the passbands"},
                  "optimization": {"type": "boolean"}, "name": {"type": "string"}},
                 ["channel_a", "channel_b"]), t_create_hairpin_diplexer),
    Tool("list_structures", "Tools > Design Wizard: the catalogue of structures (couplers, dividers, matching "
         "networks, filters, antennas) with their status, and for the available ones the fields of the "
         "specification.", _schema({}), t_list_structures),
    Tool("create_structure", "Tools > Design Wizard: an available structure of list_structures from its "
         "specification, as a circuit with named design variables, its 3D design with the same variables and an "
         "optimization on the circuit with multi-fidelity (the circuit is the coarse model, the 3D design the fine "
         "one: a few 3D solves).",
         _schema({"key": {"type": "string", "description": "e.g. 'branchline', 'qwt' (see list_structures)"},
                  "spec": {"type": "object", "description": "field: value, e.g. {'f0': 2.45, 'c': 3}; missing "
                                                            "fields keep their defaults; a field with choices "
                                                            "takes the index or the text, e.g. {'feed': 'coplanar'}"},
                  "material": {"type": "string", "description": "substrate (default Rogers RO4350B (tm))"},
                  "height_mm": NUM, "copper_um": NUM, "min_feature_mm": NUM,
                  "range_pct": {"type": "number", "description": "variable ranges +- % (default 20)"},
                  "multi_fidelity": {"type": "boolean", "description": "default true"},
                  "name": {"type": "string"}}, ["key"]), t_create_structure),
    Tool("apply_optimization", "Set the design variables to the best evaluation of an optimization (or the "
         "evaluation given) and take its stored solution over as the solution of the setup (no new solve), as "
         "'Apply to Design'.",
         _schema({"optimization": {"type": "string"}, "evaluation": {"type": "integer"}}, ["optimization"]),
         t_apply_optimization),
    Tool("compute_tuning", "Derivative tuning: compute the tuning model of up to 6 design variables on the adaptive "
         "mesh of a solved setup (the mesh is morphed, no new mesh). Afterwards get_tuning gives the S-parameters "
         "at other variable values in milliseconds. sweep: a sweep of the setup or 'LastAdaptive' (the solution "
         "frequency).",
         _schema({"setup": {"type": "string"}, "sweep": {"type": "string"},
                  "variables": {"type": "array", "items": {"type": "string"}},
                  "points": {"type": "integer", "description": "frequencies of the model for a fast or "
                                                               "interpolating sweep (default 51)"},
                  "cores": {"type": "integer"}}, ["variables"]), t_compute_tuning),
    Tool("get_tuning", "State of a tuning model and, when it is there, the S-parameters at the variable values "
         "given (default: the solved values), with the tuning range of every variable. wait_seconds waits (up to 25 min in Claude Code, 2 min in Claude Desktop, 9 min otherwise) while "
         "it is computed.",
         _schema({"setup": {"type": "string"}, "sweep": {"type": "string"}, "values": VALUES,
                  "params": {"type": "array", "items": {"type": "string"}},
                  "frequencies": {"type": "array", "items": {"type": ["string", "number"]}},
                  "max_points": {"type": "integer"}, "wait_seconds": NUM}), t_get_tuning, True),
    Tool("create_circuit", "Create a circuit design (schematic: linear network of lumped parts, ideal and "
         "microstrip/stripline/coplanar lines, Touchstone and 3D design blocks) with a substrate Sub1 and a "
         "1-10 GHz S-parameter setup. Returns the part library (types, props, pin offsets).",
         _schema({"name": {"type": "string"}, "substrate": {"type": "boolean", "description": "add Sub1 (default "
                                                                                              "true)"},
                  "include_library": {"type": "boolean"}}), t_create_circuit),
    Tool("get_circuit", "A circuit design: parts with position, rotation, props and pin coordinates, wires, the "
         "nets (which pins are connected), unconnected pins, ports, setups and its 3D layouts.",
         _schema({"include_library": {"type": "boolean"}}), t_get_circuit, True),
    Tool("edit_circuit", "Edit a schematic: add parts (at x/y on the 10-unit grid, or attach='MS1:2' to put a pin "
         "on another part's pin), modify (move, rotate, props, rename), remove parts, connect pins with wires "
         "(connect [{from: 'Port1:1', to: 'MS1:1'}]), draw wires by points, remove wires by index, settings "
         "AutoJunctions (True/False) and LineModel ('Closed form' or 'Full wave'), design variables. Pins on the "
         "same grid point are connected; part values may use variables. A 3D design block is type EM with "
         "props {Design: name} (one pin per port); a Touchstone block SNP with props {File: path}.",
         _schema({"add": {"type": "array", "items": PART}, "modify": {"type": "array", "items": PART},
                  "remove": {"type": "array", "items": {"type": "string"}},
                  "connect": {"type": "array", "items": {"type": "object", "properties": {
                      "from": {"type": "string"}, "to": {"type": "string"},
                      "via": {"type": "array", "items": {"type": "array", "items": NUM}}},
                      "required": ["from", "to"]}},
                  "wires": {"type": "array", "items": {"type": "array", "items": {"type": "array", "items": NUM}}},
                  "remove_wires": {"type": "array", "items": {"type": "integer"}},
                  "settings": {"type": "object"}, "variables": {"type": "object"}}), t_edit_circuit),
    Tool("circuit_layout", "Circuit and 3D: action 'create' turns the schematic into a new 3D design (substrate, "
         "ground, traces end to end, lumped parts, ports, 3D design blocks with their geometry; every dimension a "
         "variable like MS1_L); 'update' rebuilds an existing layout from the schematic; 'to_schematic' copies the "
         "layout's dimensions back into the schematic.",
         _schema({"action": {"type": "string", "enum": ["create", "update", "to_schematic"]},
                  "layout": {"type": "string", "description": "layout design (default: the newest of the "
                                                              "circuit)"},
                  "name": {"type": "string", "description": "name of a new layout"}}), t_circuit_layout),
    Tool("import_3d_designs", "Copy the 3D designs with ports (and their results) of another .hzn file into the "
         "project, to use them as blocks in a circuit.",
         _schema({"file": {"type": "string"}}, ["file"]), t_import_3d_designs),
    Tool("list_edge_connectors", "The library of PCB edge-launch connectors (SMA, 2.92 mm, 2.4 mm; 131 parts of "
         "Southwest Microwave, Cinch, Amphenol, HUBER+SUHNER, Rosenberger, Samtec, Molex): id, interface, mounting, maximum "
         "frequency, board thickness range, launch pin, data sheet. Filter by interface, text, board thickness.",
         _schema({"interface": {"type": "string", "enum": ["SMA", "2.92mm", "2.4mm"]}, "search": {"type": "string"},
                  "board_mm": NUM, "limit": {"type": "integer"}}), t_list_edge_connectors, True),
    Tool("attach_edge_connector", "Simulate with Real Connector: put a connector of the library at a lumped port "
         "at a board edge (the port sheet from ground to trace gives edge, direction, board thickness and trace "
         "width). It brings its launch footprint (pad, taper, grounded coplanar gap, via fence; variables "
         "<name>_wp, _lp, _gl, _lt). Nothing is deleted: mode Ideal (default) solves the lumped port and shows the "
         "connector transparent; Transition cascades a solved 3D model of the connector-to-board transition in a "
         "circuit design; 3D solves the connector with a wave port on the coax.",
         _schema({"port": {"type": "string"}, "part": {"type": "string", "description": "id or part number"},
                  "name": {"type": "string"}, "mode": {"type": "string", "enum": ["Ideal", "Transition", "3D"]}},
                 ["port", "part"]), t_attach_edge_connector),
    Tool("set_connector_mode", "Switch connectors (all, or the given names) between Ideal, Transition and 3D "
         "without losing anything; or action flatten (plain geometry, lumped port deleted), remove, "
         "optimize_launch (optimization of the footprint in the transition design) or apply_footprint (take the "
         "optimized footprint of the transition design).",
         _schema({"mode": {"type": "string", "enum": ["Ideal", "Transition", "3D"]},
                  "connectors": {"type": "array", "items": {"type": "string"}},
                  "action": {"type": "string", "enum": ["mode", "flatten", "remove", "optimize_launch",
                                                        "apply_footprint"]},
                  "return_loss_db": NUM}), t_set_connector_mode),
    Tool("get_connectors", "The connectors of a 3D design: part, lumped port, mode, footprint variables, transition "
         "design and its state, the cascade circuit design and what it still needs.",
         _schema({}), t_get_connectors, True),
]
