"""Headless command line interface: solve a Hertzon design and get its S-parameters as Touchstone, without the
window, for scripts and for other programs that use Hertzon as their EM engine.

    hertzon-cli version [--json]
    hertzon-cli describe PROJECT.hzn [--design NAME]
    hertzon-cli solve PROJECT.hzn [--design NAME] [--setup NAME] [--sweep NAME] [--set VAR=VALUE ...]
                      [--freq START:STOP:COUNT | --points F1,F2,...] [-o OUT.sNp] [--progress text|json|none]

``python -m hertzon solve ...`` and ``python -m hertzon.cli ...`` do the same. The project file is never
changed: a solve with other variable values or frequencies runs on a copy in
``<project>.hznresults/<design>/CLI/<id>/`` and an unchanged call reuses that solution (or the one the window
has) while its model and setup are the same. See docs/CLI.md for the JSON of ``describe`` and the exit codes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

CLI_SCHEMA = 1          # version of the JSON this module prints (describe, solve summary, progress events)

# exit codes (stable, documented in docs/CLI.md)
OK = 0
SOLVER_FAILED = 1       # the solver ran and failed (meshing, solve, ...)
USAGE = 2               # wrong command line (argparse uses 2 as well)
BAD_INPUT = 3           # project not found or not readable, unknown design/setup/sweep/variable, bad value
INVALID_MODEL = 4       # the design does not pass the model check (--validate)
NOT_CONVERGED = 5       # adaptive meshing did not converge and --require-convergence was given
INTERRUPTED = 130       # Ctrl+C

COMMANDS = ("version", "describe", "solve")
TAG = "@@HZN "          # prefix of the event lines of the solver process (hertzon.solver.bridge.TAG)


class CliError(Exception):
    def __init__(self, msg: str, code: int = BAD_INPUT):
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------------------------------------
# output
# ------------------------------------------------------------------------------------------------
class Reporter:
    """Progress and messages on stderr (stdout stays for the result): text lines, JSON lines or nothing."""

    def __init__(self, mode: str = "text"):
        self.mode = mode
        self._last = None

    def event(self, kind: str, **data) -> None:
        if self.mode == "json":
            sys.stderr.write(json.dumps({"event": kind, **data}, default=_json_default) + "\n")
            sys.stderr.flush()
            return
        if self.mode != "text":
            return
        if kind == "progress":
            pct = data.get("percent")
            line = f"[{pct:5.1f}%] {data.get('message', '')}" if pct is not None else str(data.get("message", ""))
            if line == self._last:
                return
            self._last = line
        elif kind == "log":
            if data.get("level") not in ("warning", "error"):
                return
            line = f"{data['level']}: {data.get('message', '')}"
        elif kind == "pass":
            ds = data.get("delta_s")
            line = f"pass {data.get('pass')}: {data.get('tets')} tetrahedra" + \
                (f", delta S {ds:.4g}" if isinstance(ds, (int, float)) else "")
        else:
            line = f"{kind}: " + ", ".join(f"{k}={v}" for k, v in data.items())
        sys.stderr.write(line + "\n")
        sys.stderr.flush()


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    return str(o)


def _print_json(data) -> None:
    sys.stdout.write(json.dumps(data, indent=1, default=_json_default) + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------------------------------------------
# project, design, values
# ------------------------------------------------------------------------------------------------
def _version() -> str:
    from . import __version__
    return __version__


def load_project(path: str):
    from .model.design import Project
    p = Path(path)
    if not p.is_file():
        raise CliError(f"Project file not found: {path}")
    try:
        return Project.load(p.resolve())
    except Exception as e:  # noqa: BLE001
        raise CliError(f"Cannot read {path}: {type(e).__name__}: {e}") from None


def pick_design(proj, name: str | None):
    if name:
        try:
            return proj.design(name)
        except KeyError:
            raise CliError(f"No design '{name}'; designs: {[d.name for d in proj.designs]}") from None
    if len(proj.designs) == 1:
        return proj.designs[0]
    solvable = [d for d in proj.designs if d.setups]
    if len(solvable) == 1:
        return solvable[0]
    raise CliError(f"The project has several designs, choose one with --design: {[d.name for d in proj.designs]}")


def pick_setup(des, name: str | None):
    if not des.setups:
        raise CliError(f"Design '{des.name}' has no analysis setup")
    if name:
        try:
            return des.setup(name)
        except KeyError:
            raise CliError(f"No setup '{name}'; setups: {[s.name for s in des.setups]}") from None
    on = [s for s in des.setups if s.props.get("Enabled", "True") == "True"]
    return (on or des.setups)[0]


def parse_sets(items: list[str] | None) -> dict[str, str]:
    out = {}
    for it in items or []:
        k, eq, v = it.partition("=")
        k, v = k.strip(), v.strip()
        if not eq or not k or not v:
            raise CliError(f"--set needs VAR=VALUE, got '{it}'")
        out[k] = v
    return out


def apply_sets(proj, des, sets: dict[str, str]) -> None:
    """Set design variables (and project variables, $name) in place; the values are checked."""
    for k, v in sets.items():
        if k in des.variables:
            des.variables[k] = v
        elif k in proj.variables:
            proj.variables[k] = v
        else:
            known = list(des.variables) + list(proj.variables)
            raise CliError(f"No variable '{k}' in design '{des.name}'; variables: {known}")
    ev = des.evaluator(proj)
    for k in sets:
        try:
            ev.value(k)
        except Exception as e:  # noqa: BLE001
            raise CliError(f"--set {k}={sets[k]}: {e}") from None


def freq_hz(text: str, ev) -> float:
    """A frequency: a plain number is in Hz, otherwise with its unit (2.4GHz) or an expression of variables."""
    t = text.strip()
    try:
        return float(t)
    except ValueError:
        pass
    try:
        v, has_unit = ev._eval(t)
    except Exception as e:  # noqa: BLE001
        raise CliError(f"Bad frequency '{text}': {e}") from None
    return float(v)


def _fmt_hz(f: float) -> str:
    return f"{f:.12g}Hz"


def apply_frequencies(setup, sweep_name: str | None, freq: str | None, points: str | None,
                      sweep_type: str | None, ev, circuit: bool):
    """Choose the sweep (and change its frequencies on request). Returns the sweep or None (LastAdaptive).
    Every other sweep of the setup is switched off: the copy solves only what was asked for."""
    from .model.design import Sweep
    sweeps = setup.sweeps
    if sweep_name:
        sw = next((s for s in sweeps if s.name == sweep_name), None)
        if sw is None:
            if sweep_name != "LastAdaptive" or circuit:
                raise CliError(f"No sweep '{sweep_name}' in setup '{setup.name}'; sweeps: "
                               f"{[s.name for s in sweeps]}" + ("" if circuit else " (or LastAdaptive)"))
            if freq or points:
                raise CliError("--freq/--points need a frequency sweep, not LastAdaptive")
            for s in sweeps:
                s.props["Enabled"] = "False"
            return None
    else:
        on = [s for s in sweeps if s.props.get("Enabled", "True") == "True"]
        sw = on[0] if on else None
    if (freq or points) and sw is None:
        sw = Sweep("CLI")
        sw.props.update({"SweepType": "Discrete" if circuit else "Interpolating"})
        sweeps.append(sw)
    if sw is None:
        if circuit:
            raise CliError(f"Setup '{setup.name}' has no frequency sweep; give --freq")
        return None
    for s in sweeps:
        s.props["Enabled"] = "True" if s is sw else "False"
    if freq:
        parts = freq.split(":")
        if len(parts) != 3:
            raise CliError(f"--freq needs START:STOP:COUNT, got '{freq}'")
        f1, f2 = freq_hz(parts[0], ev), freq_hz(parts[1], ev)
        try:
            n = int(parts[2])
        except ValueError:
            raise CliError(f"--freq: COUNT must be an integer, got '{parts[2]}'") from None
        if not (0 < f1 < f2) or n < 2:
            raise CliError("--freq needs 0 < START < STOP and COUNT >= 2")
        sw.props.update({"Distribution": "Linear Count", "Start": _fmt_hz(f1), "Stop": _fmt_hz(f2), "Count": str(n)})
    elif points:
        fs = sorted(freq_hz(x, ev) for x in points.split(",") if x.strip())
        if not fs or fs[0] <= 0:
            raise CliError("--points needs positive frequencies")
        sw.props.update({"Distribution": "Single Points", "Points": ",".join(_fmt_hz(f) for f in fs)})
    if sweep_type:
        sw.props["SweepType"] = {"discrete": "Discrete", "interpolating": "Interpolating", "fast": "Fast"}[sweep_type]
    return sw


# ------------------------------------------------------------------------------------------------
# describe
# ------------------------------------------------------------------------------------------------
def _num(ev, text: str, kind: str = "value"):
    try:
        return getattr(ev, kind)(text)
    except Exception:  # noqa: BLE001
        return None


def _ports_3d(des, ev) -> list[dict]:
    out = []
    for p in des.ports():
        q = {"number": int(p.props.get("PortNumber", "0") or 0), "name": p.name, "kind": p.kind}
        if p.kind == "Lumped Port":
            q["modes"] = 1
            q["z0_ohm"] = _num(ev, p.props.get("Impedance", "50ohm"))
        else:
            q["modes"] = int(p.props.get("NumberOfModes", "1") or 1)
            if p.kind == "Wave Port" and p.props.get("Renormalize") == "True":
                q["z0_ohm"] = _num(ev, p.props.get("RenormImpedance", "50ohm"))
            else:
                q["z0_ohm"] = None       # S is normalized to the impedance of the port mode
            if p.kind == "Wave Port" and p.props.get("Deembed") == "True":
                q["deembed_m"] = _num(ev, p.props.get("DeembedDistance", "0"), "length")
        out.append(q)
    return out


def _ports_circuit(des, ev) -> list[dict]:
    from .model.circuit import port_order as circuit_ports
    out = []
    for i, c in enumerate(circuit_ports(des.components)):
        out.append({"number": i + 1, "name": c.name, "kind": "Circuit Port", "modes": 1,
                    "z0_ohm": _num(ev, c.props.get("Z0", "50ohm"))})
    return out


def _sweep_info(sw, ev) -> dict:
    from .solver.sweeps import sweep_frequencies
    q = {"name": sw.name, "enabled": sw.props.get("Enabled", "True") == "True",
         "type": sw.props.get("SweepType", ""), "distribution": sw.props.get("Distribution", "")}
    try:
        f = sweep_frequencies(sw.props, ev)
        q.update(start_hz=float(f[0]), stop_hz=float(f[-1]), points=int(len(f)))
    except Exception as e:  # noqa: BLE001
        q["error"] = str(e)
    return q


def _setup_info(s, ev, circuit: bool) -> dict:
    q = {"name": s.name, "enabled": s.props.get("Enabled", "True") == "True"}
    if not circuit:
        mode = s.props.get("SolutionFrequencyMode", "Single")
        q["adaptive"] = {"mode": mode}
        if mode == "Single":
            q["adaptive"]["frequency_hz"] = _num(ev, s.props.get("Frequency", "5GHz"), "freq")
        elif mode == "Broadband":
            q["adaptive"].update(low_hz=_num(ev, s.props.get("LowFrequency", "1GHz"), "freq"),
                                 high_hz=_num(ev, s.props.get("HighFrequency", "10GHz"), "freq"))
        else:
            q["adaptive"]["frequencies"] = s.props.get("MultiFrequencies", "")
        q["max_delta_s"] = _num(ev, s.props.get("MaximumDeltaS", "0.02"))
        q["max_passes"] = _num(ev, s.props.get("MaximumPasses", "10"), "integer")
    q["sweeps"] = [_sweep_info(sw, ev) for sw in s.sweeps]
    return q


def _variables(proj, des, ev) -> list[dict]:
    out = []
    for scope, src in (("design", des.variables), ("project", proj.variables or {})):
        for k, v in src.items():
            if scope == "project" and k in des.variables:
                continue
            q = {"name": k, "expression": v, "scope": scope, "value": _num(ev, k)}
            try:
                dim = ev.dimension(k)
            except Exception:  # noqa: BLE001
                dim = None
            if dim is not None:
                q["unit"] = _unit_of(dim)
            out.append(q)
    return out


def _unit_of(dim: dict) -> str:
    n, f = dim.get("length", 0), dim.get("freq", 0)
    if not n and not f:
        return ""
    parts = ([f"m^{n}" if n != 1 else "m"] if n else []) + ([f"Hz^{f}" if f != 1 else "Hz"] if f else [])
    return "*".join(parts)


def describe_design(proj, des) -> dict:
    ev = des.evaluator(proj)
    circuit = getattr(des, "kind", "3d") == "circuit"
    return {
        "name": des.name,
        "kind": getattr(des, "kind", "3d"),
        "units": des.units,
        "ports": _ports_circuit(des, ev) if circuit else _ports_3d(des, ev),
        "variables": _variables(proj, des, ev),
        "setups": [_setup_info(s, ev, circuit) for s in des.setups],
    }


def describe(proj, designs) -> dict:
    from .model.design import FILE_VERSION
    return {
        "cli_schema": CLI_SCHEMA,
        "hertzon": _version(),
        "project": str(proj.path),
        "file_format": {"format": "hertzon-project", "version": FILE_VERSION},
        "designs": [describe_design(proj, d) for d in designs],
    }


# ------------------------------------------------------------------------------------------------
# solve
# ------------------------------------------------------------------------------------------------
def _absolute_files(proj, base: Path) -> None:
    """Touchstone files of lumped components given relative to the project file: absolute in the copy."""
    for d in proj.designs:
        for a in list(d.boundaries) + list(d.excitations):
            f = a.props.get("TouchstoneFile", "").strip()
            if f and not Path(f).is_absolute():
                a.props["TouchstoneFile"] = str(base / f)
        for c in getattr(d, "components", []):
            f = c.props.get("File", "").strip() if c.type == "SNP" else ""
            if f and not Path(f).is_absolute():
                c.props["File"] = str(base / f)


def _solution_ok(folder: Path, keys: dict, solution: str) -> bool:
    from .solver.parametric import _is_current
    from .solver.results import _safe
    return _is_current(folder, keys) and (folder / f"sparams_{_safe(solution)}.npz").exists()


def run_solver(project_file: Path, design: str, setup: str, out: Path, cores: int, rep: Reporter) -> dict:
    """Run the solver process on a saved project; the progress goes to the reporter. Returns
    {"code", "error", "converged"}."""
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    n = cores or (os.cpu_count() or 1)
    env["HERTZON_CORES"] = str(n)
    env.setdefault("HERTZON_SWEEP_WORKERS", "2")
    env["HERTZON_MESH_ONLY"] = "0"
    env["HERTZON_ONLY_SWEEP"] = ""
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
        env[var] = str(n)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ABORT").unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "hertzon.solver.run", str(project_file), design, setup, str(out)]
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, cwd=str(project_file.parent), **kw)
    state = {"error": "", "done": False, "converged": None}

    def read():
        with open(out / "solver.log", "w", encoding="utf-8", errors="replace") as log:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace")
                log.write(line)
                if not line.startswith(TAG):
                    continue
                try:
                    msg = json.loads(line[len(TAG):])
                except json.JSONDecodeError:
                    continue
                t = msg.pop("type", "")
                if t == "progress":
                    rep.event("progress", percent=float(msg.get("value", 0)), message=msg.get("text", ""))
                elif t == "log":
                    rep.event("log", level=msg.get("level", "info"), message=msg.get("msg", ""))
                elif t == "pass":
                    rep.event("pass", **{"pass": msg.get("pass", msg.get("step")), "tets": msg.get("tets"),
                                         "delta_s": msg.get("delta_s")})
                elif t == "convergence":
                    state["converged"] = bool(msg.get("converged"))
                elif t == "done":
                    state["done"] = True
                elif t == "failed":
                    state["error"] = str(msg.get("error", "failed"))

    th = threading.Thread(target=read, daemon=True)
    th.start()
    try:
        while proc.poll() is None:
            time.sleep(0.2)
    except KeyboardInterrupt:
        (out / "ABORT").write_text("cli", encoding="utf-8")
        try:
            proc.terminate()
            proc.wait(10)
        except Exception:  # noqa: BLE001
            proc.kill()
        raise
    th.join(5)
    code = proc.returncode
    if code == 0 and not state["done"] and not state["error"]:
        state["error"] = "the solver ended without a result (see solver.log)"
    if code != 0 and not state["error"]:
        state["error"] = f"the solver ended with code {code} (see {out / 'solver.log'})"
    return {"code": code, **state}


def _work_id(sets: dict, setup: str, sweep, extra: dict) -> str:
    text = json.dumps({"set": sets, "setup": setup, "sweep": sweep, **extra}, sort_keys=True)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _write_output(freq, S, z0, out_path: str | None, default_name: str) -> str:
    """Touchstone the same way as File > Export Touchstone in the window."""
    from .solver.results import ResultStore
    import tempfile
    n = S.shape[1]
    if out_path == "-":
        with tempfile.TemporaryDirectory() as tmp:
            p = ResultStore(Path(tmp)).write_touchstone("cli", freq, S, z0, Path(tmp) / f"cli.s{n}p")
            sys.stdout.write(p.read_text(encoding="utf-8"))
            sys.stdout.flush()
        return "-"
    p = Path(out_path) if out_path else Path.cwd() / f"{default_name}.s{n}p"
    if p.is_dir():
        p = p / f"{default_name}.s{n}p"
    p.parent.mkdir(parents=True, exist_ok=True)
    ResultStore(p.parent).write_touchstone("cli", freq, S, z0, p)
    return str(p.resolve())


def solve(args, rep: Reporter) -> dict:
    import numpy as np
    from .solver.results import results_dir, ResultStore, _safe
    from .solver.parametric import _setup_keys
    proj = load_project(args.project)
    original = Path(proj.path)
    des = pick_design(proj, args.design)
    setup = pick_setup(des, args.setup)
    circuit = getattr(des, "kind", "3d") == "circuit"
    sets = parse_sets(args.set)
    apply_sets(proj, des, sets)
    ev = des.evaluator(proj)
    if not circuit and (des.settings.get("SolutionType") == "Eigenmode" or setup.eigen):
        raise CliError("Eigenmode setups have no S-parameters")
    if not circuit and not des.ports() and any(e.kind == "Incident Plane Wave" and e.enabled for e in des.excitations):
        raise CliError("Incident plane wave designs have no S-parameters (no ports); solve them in the window")
    sw = apply_frequencies(setup, args.sweep, args.freq, args.points, args.sweep_type, ev, circuit)
    solution = sw.name if sw is not None else "LastAdaptive"
    t0 = time.time()
    summary = {"cli_schema": CLI_SCHEMA, "hertzon": _version(), "project": str(original), "design": des.name,
               "setup": setup.name, "solution": solution, "variables": sets}

    if circuit:
        from .solver import circuit as SC
        from .solver.sweeps import sweep_frequencies
        rep.event("progress", percent=0.0, message=f"Analyzing circuit {des.name}")

        def prog(done, total, text=""):
            rep.event("progress", percent=100.0 * done / max(1, total), message=text or "Line cross-sections")
        try:
            res = SC.analyze(proj, des, sweep_frequencies(sw.props, ev), prog)
        except SC.CircuitError as e:
            raise CliError(str(e), SOLVER_FAILED) from None
        for w in res.warnings:
            rep.event("log", level="warning", message=w)
        freq, S, z0 = np.asarray(res.freq, float), np.asarray(res.S, complex), np.asarray(res.z0)
        ports = [{"number": i + 1, "name": n, "kind": "Circuit Port", "modes": 1} for i, n in enumerate(res.ports)]
        summary.update(cached=False, converged=None)
        rep.event("progress", percent=100.0, message="Circuit analyzed")
    else:
        # 3D: the solver runs in its own process on a saved copy; the solution of the window is used as it is
        # when model and setup are still the same
        nominal = results_dir(original, des.name, setup.name)
        wid = _work_id(sets, setup.name, solution, {"freq": args.freq, "points": args.points,
                                                     "type": args.sweep_type})
        work = results_dir(original, des.name, "CLI") / wid
        copy_file = work / "design.hzn"
        out = results_dir(copy_file, des.name, setup.name)
        cp = proj.clone()
        d = cp.design(des.name)
        d.optimetrics, d.parametrics, d.reports, d.field_plots = [], [], [], []
        if not args.fields:
            for st in d.setups:
                st.props["SaveFields"] = "False"
                for s in st.sweeps:
                    s.props["SaveFields"] = "False"
        _absolute_files(cp, original.parent)
        folder = None
        if not args.force:
            for cand, keys in ((nominal, _setup_keys(proj, des.name, setup.name)),
                               (out, _setup_keys(cp, des.name, setup.name))):
                if _solution_ok(cand, keys, solution):
                    folder = cand
                    break
        cached = folder is not None
        if not cached:
            if args.validate:
                _validate(proj, des)
            if out.exists():
                keep = (out / "amr_state.npz").read_bytes() if (out / "amr_state.npz").exists() else None
                shutil.rmtree(out, ignore_errors=True)
                out.mkdir(parents=True, exist_ok=True)
                if keep:
                    (out / "amr_state.npz").write_bytes(keep)
            work.mkdir(parents=True, exist_ok=True)
            cp.save(copy_file)
            rep.event("progress", percent=0.0, message=f"Solving {des.name} / {setup.name}")
            r = run_solver(copy_file, des.name, setup.name, out, args.cores, rep)
            if r["code"] == 130:
                raise KeyboardInterrupt
            if r["code"] != 0 or r["error"]:
                raise CliError(f"Solver failed: {r['error']} (log: {out / 'solver.log'})", SOLVER_FAILED)
            folder = out
        store = ResultStore(folder)
        data = store.sparams(solution)
        if data is None:
            raise CliError(f"The solver wrote no solution '{solution}' (log: {folder / 'solver.log'})",
                           SOLVER_FAILED)
        if data.get("ports_only"):
            raise CliError("The setup solves the ports only; there are no S-parameters", SOLVER_FAILED)
        conv = store.read_json("convergence.json", {}) or {}
        freq, S, z0, ports = data["freq"], data["S"], data["z0"], data.get("ports", [])
        summary.update(cached=cached, results=str(folder), converged=conv.get("converged"),
                       passes=len(conv.get("passes", []) or []), tetrahedra=conv.get("final_tets"))
    from .solver.results import port_entries
    rows = port_entries(ports) if ports else []
    z = np.real(np.ravel(z0)[:S.shape[1]]) if np.size(z0) else np.array([50.0])
    summary.update(
        ports=[{"row": i + 1, "number": q.get("number"), "name": q.get("label", q.get("name")),
                "kind": q.get("kind"), "mode": q.get("mode", 1)} for i, q in enumerate(rows)],
        frequencies={"count": int(len(freq)), "start_hz": float(freq[0]), "stop_hz": float(freq[-1])},
        reference_impedance_ohm=float(z[0]) if len(z) else 50.0,
    )
    if len(z) > 1 and np.ptp(z) > 1e-6 * max(1.0, abs(z[0])):
        rep.event("log", level="warning", message="The ports have different reference impedances; the "
                  f"Touchstone file states {z[0]:g} ohm for all of them, as the export of the window does")
        summary["port_impedances_ohm"] = [float(x) for x in z]
    default = f"{_safe(des.name)}_{_safe(setup.name)}_{_safe(solution)}"
    summary["touchstone"] = _write_output(freq, np.asarray(S), z0, args.output, default)
    summary["seconds"] = round(time.time() - t0, 2)
    if args.require_convergence and summary.get("converged") is False:
        raise CliError("Adaptive meshing did not converge (Touchstone written: "
                       f"{summary['touchstone']})", NOT_CONVERGED)
    return summary


def _validate(proj, des) -> None:
    from .model.validate import validate
    from .mcp.tools import tessellate, ToolError
    try:
        tess, errors = tessellate(proj, des)
    except ToolError as e:
        raise CliError(str(e), INVALID_MODEL) from None
    bad = [c.message for c in validate(proj, des, tess, errors) if c.level == "error"]
    if bad:
        raise CliError("The model check failed:\n- " + "\n- ".join(bad), INVALID_MODEL)


# ------------------------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------------------------
def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hertzon-cli", description="Hertzon without the window: describe a design "
                                 "and solve it to a Touchstone file. Frequencies are in Hz unless a unit is given.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("version", help="Hertzon version")
    v.add_argument("--json", action="store_true", help="as JSON")
    d = sub.add_parser("describe", help="designs, ports, variables and setups of a project as JSON")
    d.add_argument("project", help="project file (.hzn)")
    d.add_argument("--design", help="only this design (default: all)")
    s = sub.add_parser("solve", help="solve a design and write its S-parameters as Touchstone")
    s.add_argument("project", help="project file (.hzn); it is not changed")
    s.add_argument("--design", help="design (default: the only one, or the only one with a setup)")
    s.add_argument("--setup", help="analysis setup (default: the first enabled one)")
    s.add_argument("--sweep", help="frequency sweep of the setup, or LastAdaptive (default: its first enabled "
                                   "sweep, LastAdaptive when it has none)")
    s.add_argument("--set", action="append", metavar="VAR=VALUE",
                   help="value of a design or project variable for this run, e.g. --set w=1.2mm (repeatable)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--freq", metavar="START:STOP:COUNT", help="linear sweep, e.g. 1e9:5e9:401 or 1GHz:5GHz:401")
    g.add_argument("--points", metavar="F1,F2,...", help="single frequencies, e.g. 2.4GHz,2.45GHz")
    s.add_argument("--sweep-type", choices=("discrete", "interpolating", "fast"),
                   help="sweep method for this run (default: the one of the sweep)")
    s.add_argument("-o", "--output", help="Touchstone file or folder, '-' for stdout "
                                          "(default: <design>_<setup>_<solution>.sNp in the current folder)")
    s.add_argument("--cores", type=int, default=0, help="CPU cores for the solver (default: all)")
    s.add_argument("--force", action="store_true", help="solve again even when a matching solution exists")
    s.add_argument("--fields", action="store_true", help="also keep the field solutions (default: S-parameters only)")
    s.add_argument("--validate", action="store_true", help="check the model first (builds the geometry)")
    s.add_argument("--require-convergence", action="store_true",
                   help=f"exit with code {NOT_CONVERGED} when adaptive meshing did not converge")
    s.add_argument("--progress", choices=("text", "json", "none"), default="text",
                   help="progress on stderr: text lines, JSON lines or nothing (default: text)")
    s.add_argument("--json", action="store_true", help="print a JSON summary on stdout when done")
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    try:
        args = parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0) if not isinstance(e.code, str) else USAGE
    rep = Reporter(getattr(args, "progress", "text"))
    try:
        if args.cmd == "version":
            if args.json:
                _print_json({"cli_schema": CLI_SCHEMA, "hertzon": _version(), "python": sys.version.split()[0]})
            else:
                print(f"Hertzon {_version()}")
            return OK
        if args.cmd == "describe":
            proj = load_project(args.project)
            _print_json(describe(proj, [pick_design(proj, args.design)] if args.design else proj.designs))
            return OK
        if args.output == "-" and args.json:
            raise CliError("--json and -o - both write to stdout; choose one", USAGE)
        summary = solve(args, rep)
        rep.event("done", **{k: summary[k] for k in ("touchstone", "seconds", "cached")})
        if args.json:
            _print_json(summary)
        return OK
    except CliError as e:
        if rep.mode == "json":
            rep.event("error", code=e.code, message=str(e))
        else:                                # errors are shown with --progress none as well
            sys.stderr.write(f"error: {e}\n")
        return e.code
    except KeyboardInterrupt:
        if rep.mode == "json":
            rep.event("error", code=INTERRUPTED, message="interrupted")
        else:
            sys.stderr.write("interrupted\n")
        return INTERRUPTED


if __name__ == "__main__":
    if os.name != "nt":
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main())
