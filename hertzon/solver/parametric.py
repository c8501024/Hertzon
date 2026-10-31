"""Optimization > Parametric (parametric sweep): the variations of the sweep definitions and the process
that solves them.

python -m hertzon.solver.parametric PROJECT DESIGN NAME OUTDIR

Every variation is a copy of the project with the swept variables set, in OUTDIR/variations/<id>/ (design.hzn
and one result folder per setup, the same layout as the nominal results). A variation whose stored solution
still matches (model and setup keys) is not solved again. The table with the state and the calculations of
every variation is OUTDIR/table.json. A file OUTDIR/ABORT stops the run.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from ..model.design import Project
from .optimetrics import split_value, in_unit, fmt_value
from .results import ResultStore, results_dir, _safe, running_status

SHORT = {"Linear step": "LIN", "Linear count": "LINC", "Decade count": "DEC", "Octave count": "OCT",
         "Exponential count": "EXP"}


def parametric_dir(project_path: str, design: str, name: str) -> Path:
    """Results of a parametric setup (next to the analysis setups of the design)."""
    from .results import studies_dir
    return studies_dir(project_path, design) / _safe(name)


def sweepable(project, design) -> dict[str, str]:
    """Variables with a plain value (number and unit): design variables, then project variables ($name)."""
    out = {}
    for src in (design.variables, getattr(project, "variables", {}) or {}):
        for k, v in src.items():
            if split_value(v) is not None and k not in out:
                out[k] = v
    return out


# ---------------------------------------------------------------------------------------------
# sweep definitions -> values -> table
# ---------------------------------------------------------------------------------------------
def describe(e: dict) -> str:
    """description column: '10mm' or 'LIN 8mm 12mm 1mm'."""
    t = e.get("type", "Linear step")
    if t == "Single value":
        return str(e.get("start", ""))
    return f"{SHORT.get(t, t)} {e.get('start', '')} {e.get('stop', '')} {e.get('step', '')}"


def entry_values(e: dict, ev=None) -> list[str]:
    """Values of one sweep definition, in the unit of its start value."""
    name = e.get("variable", "")
    sv = split_value(e.get("start", ""))
    if sv is None:
        raise ValueError(f"{name}: start '{e.get('start', '')}' must be a number (with unit)")
    a, unit = sv
    kind = e.get("type", "Linear step")
    if kind == "Single value":
        return [fmt_value(a, unit)]
    b = in_unit(e.get("stop", ""), unit, ev)
    if kind == "Linear step":
        step = in_unit(e.get("step", ""), unit, ev)
        if step == 0 or (b - a) * step < 0:
            raise ValueError(f"{name}: the step {e.get('step')} does not lead from {e.get('start')} to {e.get('stop')}")
        n = int(np.floor((b - a) / step + 1e-9))
        xs = a + step * np.arange(n + 1)
    else:
        try:
            n = int(float(e.get("step", "")))
        except ValueError:
            raise ValueError(f"{name}: the count '{e.get('step')}' must be a whole number") from None
        if n < 1:
            raise ValueError(f"{name}: the count must be at least 1")
        if kind == "Linear count":
            xs = np.linspace(a, b, n) if n > 1 else np.array([a])
        else:
            if a <= 0 or b <= 0:
                raise ValueError(f"{name}: {kind} needs a positive start and stop")
            if kind == "Exponential count":
                xs = np.geomspace(a, b, n) if n > 1 else np.array([a])
            else:                                  # n points per decade / octave
                base = 10.0 if kind == "Decade count" else 2.0
                k = int(np.floor(np.log(b / a) / np.log(base) * n + 1e-9))
                xs = a * base ** (np.arange(k + 1) / n)
    return [fmt_value(float(f"{x:.12g}"), unit) for x in xs]


def variable_values(ps, ev=None) -> dict[str, list[str]]:
    """Values of every swept variable (several definitions of one variable are joined, in order)."""
    out: dict[str, list[str]] = {}
    for e in ps.sweeps:
        vals = out.setdefault(e["variable"], [])
        for v in entry_values(e, ev):
            if v not in vals:
                vals.append(v)
    return out


def variation_table(ps, ev=None) -> list[dict[str, str]]:
    """All variations: every combination of the variables; variables with the same Sync # step together."""
    values = variable_values(ps, ev)
    groups: list[list[str]] = []
    sync_of: dict[str, list[str]] = {}
    for e in ps.sweeps:
        name, s = e["variable"], str(e.get("sync", "") or "").strip()
        if any(name in g for g in groups):
            continue
        if s and s in sync_of:
            sync_of[s].append(name)
            continue
        g = [name]
        groups.append(g)
        if s:
            sync_of[s] = g
    axes = []
    for g in groups:
        n = {len(values[v]) for v in g}
        if len(n) > 1:
            raise ValueError("Synchronized variables need the same number of values: " +
                             ", ".join(f"{v} ({len(values[v])})" for v in g))
        axes.append([{v: values[v][i] for v in g} for i in range(n.pop())])
    if not axes:
        return []
    rows = []
    for combo in itertools.product(*axes):
        row = {}
        for part in combo:
            row.update(part)
        rows.append(row)
    return rows


def variation_id(texts: dict[str, str]) -> str:
    label = _safe("_".join(f"{k}{v}" for k, v in texts.items()).replace("$", ""))[:48]
    return f"{label}-{hashlib.sha1(json.dumps(texts, sort_keys=True).encode()).hexdigest()[:8]}"


def variation_label(texts: dict[str, str]) -> str:
    return " ".join(f"{k}='{v}'" for k, v in texts.items())


def variation_project(project: Project, design: str, texts: dict[str, str], save_fields: bool) -> Project:
    p = project.clone()
    d = p.design(design)
    for k, v in texts.items():
        if k in d.variables or k not in p.variables:
            d.variables[k] = v
        else:
            p.variables[k] = v
    d.optimetrics, d.parametrics = [], []
    d.reports, d.field_plots = [], []
    if not save_fields:
        for st in d.setups:
            st.props["SaveFields"] = "False"
            for sw in st.sweeps:
                sw.props["SaveFields"] = "False"
    return p


def calc_value(c: dict, data: dict, ev, outvars=None) -> float:
    """A calculation (Calculations tab): an expression reduced over its frequency range."""
    from .expressions import eval_expr
    f = np.asarray(data["freq"], float)
    order = np.argsort(f)
    f = f[order]
    y = eval_expr(data, c["calc"], outvars)
    y = (np.real(y) if np.iscomplexobj(y) else np.asarray(y, float))[order]
    func = c.get("func", "Value")
    lo = float(ev.freq(c["start"])) if str(c.get("start", "")).strip() else f[0]
    if func == "Value":
        return float(np.interp(lo, f, y)) if len(f) > 1 else float(y[0])
    hi = float(ev.freq(c["stop"])) if str(c.get("stop", "")).strip() else f[-1]
    lo, hi = min(lo, hi), max(lo, hi)
    grid = np.r_[lo, f[(f > lo) & (f < hi)], hi] if hi > lo else np.array([lo])
    v = np.interp(grid, f, y) if len(f) > 1 else y
    if func == "Max":
        return float(v.max())
    if func == "Min":
        return float(v.min())
    if func == "Peak to Peak":
        return float(v.max() - v.min())
    if len(grid) > 1:                            # mean over frequency
        trap = getattr(np, "trapezoid", None) or np.trapz
        return float(trap(v, grid) / (grid[-1] - grid[0]))
    return float(v.mean())


def calc_name(c: dict) -> str:
    rng = c.get("start", "") if c.get("func", "Value") == "Value" else f"{c.get('start', '')}-{c.get('stop', '')}"
    rng = rng.strip("-")
    return f"{c.get('func', 'Value')}({c['calc']})" + (f" @ {rng}" if rng else "")


_TABLES: dict[str, tuple[float, dict]] = {}


def read_table(folder: Path) -> dict:
    f = Path(folder) / "table.json"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return {}
    hit = _TABLES.get(str(f))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        t = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    _TABLES[str(f)] = (mt, t)
    return t


def current_values(project, design) -> dict[str, str]:
    return {**(getattr(project, "variables", {}) or {}), **design.variables}


def design_variations(project, design) -> list[tuple[str, dict, Path]]:
    """(parametric setup, variables, folder) of every variation with at least one solved setup."""
    out = []
    if not getattr(project, "path", None):
        return out
    for ps in design.parametrics:
        for texts, folder in solved_variations(project.path, design.name, ps.name):
            out.append((ps.name, texts, folder))
    return out


def _variation_keys(project, design, setup: str, save_fields: bool) -> dict:
    """Keys of the current design as the variation run stored them (fields not saved in its setup copy)."""
    import copy
    from .keys import model_key, setup_key
    s = design.setup(setup)
    mk = model_key(project, design, s)
    if not save_fields:
        s = copy.deepcopy(s)
        s.props["SaveFields"] = "False"
        for sw in s.sweeps:
            sw.props["SaveFields"] = "False"
    return {"model_key": mk, "setup_key": setup_key(s)}


def matching_variation(project, design, setup: str) -> Path | None:
    """Result folder of `setup` in a solved parametric variation that is the current design: the swept
    variables have the current values and the stored keys match (shows the solution of the current
    variation without solving again)."""
    if not getattr(project, "path", None) or not design.parametrics:
        return None
    cur = current_values(project, design)
    for ps in design.parametrics:
        out = parametric_dir(project.path, design.name, ps.name)
        for r in read_table(out).get("variations", []):
            if r.get("state", {}).get(setup) != "done" or any(cur.get(k) != v for k, v in r["variables"].items()):
                continue
            folder = out / "variations" / r["id"] / _safe(setup)
            try:
                keys = _variation_keys(project, design, setup, ps.props.get("SaveFields", "False") == "True")
            except KeyError:
                return None
            if _is_current(folder, keys):
                return folder
    return None


def solution_dir(project, design, setup: str) -> Path:
    """Where the results of `setup` for the current design are: the nominal folder, or the folder of the
    parametric variation the design is set to when the nominal solution does not match it."""
    nominal = results_dir(project.path, design.name, setup)
    var = matching_variation(project, design, setup)
    if var is None:
        return nominal
    try:
        from .keys import model_key, setup_key
        s = design.setup(setup)
        if _is_current(nominal, {"model_key": model_key(project, design, s), "setup_key": setup_key(s)}):
            return nominal
    except KeyError:
        pass
    return var


def solved_variations(project_path: str, design: str, name: str) -> list[tuple[dict, Path]]:
    """(variables, folder) of the variations of a parametric setup that have results."""
    out = parametric_dir(project_path, design, name)
    rows = read_table(out).get("variations", [])
    res = []
    for r in rows:
        folder = out / "variations" / r["id"]
        if any(s == "done" for s in r.get("state", {}).values()) and folder.exists():
            res.append((r["variables"], folder))
    return res


# ---------------------------------------------------------------------------------------------
# the process
# ---------------------------------------------------------------------------------------------
def _setup_keys(project, design: str, setup: str) -> dict:
    from .keys import model_key, setup_key
    d = project.design(design)
    s = d.setup(setup)
    return {"model_key": model_key(project, d, s), "setup_key": setup_key(s)}


def _is_current(folder: Path, keys: dict) -> bool:
    try:
        st = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return st.get("state") == "done" and st.get("model_key") == keys["model_key"] and \
        st.get("setup_key", "") == keys["setup_key"]


class ParametricRun:
    def __init__(self, project_path: str, design: str, name: str, out: Path):
        from .expressions import output_variables
        self.proj = Project.load(project_path)
        self.des = self.proj.design(design)
        self.ps = next(o for o in self.des.parametrics if o.name == name)
        self.out = out
        self.ev = self.des.evaluator(self.proj)
        self.rows = variation_table(self.ps, self.ev)
        if not self.rows:
            raise ValueError("The parametric setup has no sweep definition")
        setups = [s for s in self.ps.setups if any(x.name == s for x in self.des.setups)]
        if not setups:
            raise ValueError("Choose at least one setup to simulate (General tab)")
        self.setups = setups
        self.save_fields = self.ps.props.get("SaveFields", "False") == "True"
        self.parallel = max(1, int(float(self.ps.props.get("ParallelVariations", "1") or 1)))
        self.cores = max(1, int(os.environ.get("HERTZON_CORES") or os.cpu_count() or 1))
        self.outvars = output_variables(self.des)
        self.t0 = time.time()
        self.table = {"name": name, "variables": list(self.rows[0].keys()), "setups": setups,
                      "calcs": [calc_name(c) for c in self.ps.calcs], "variations": []}

    def aborted(self) -> bool:
        return (self.out / "ABORT").exists()

    def _write(self) -> None:
        ResultStore(self.out).write_json("table.json", self.table)

    def _env(self, workers: int) -> dict:
        from .optimize import ROOT
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        per = str(max(1, self.cores // max(1, workers)))
        env["HERTZON_CORES"] = per
        for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            env[v] = per
        if workers > 1:
            env["HERTZON_SWEEP_WORKERS"] = "1"
        env["HERTZON_MESH_ONLY"] = "0"
        env["HERTZON_ONLY_SWEEP"] = ""
        return env

    def _calcs(self, row: dict) -> None:
        folder = self.out / "variations" / row["id"]
        vals = {}
        for c in self.ps.calcs:
            name = calc_name(c)
            try:
                setup, _, sol = [x.strip() for x in str(c.get("solution", "")).partition(":")]
                data = ResultStore(folder / _safe(setup)).sparams(sol)
                if data is None:
                    raise RuntimeError(f"no solution '{c.get('solution')}'")
                from .expressions import with_pairs
                vals[name] = calc_value(c, with_pairs(data, self.des), self.ev, self.outvars)
            except Exception as ex:  # noqa: BLE001
                vals[name] = None
                log(f"{variation_label(row['variables'])}: {name}: {ex}", "warning")
        row["calcs"] = vals

    def _strip(self, folder: Path) -> None:
        if self.save_fields:
            return
        for f in list(folder.glob("solution_*.npz")) + list(folder.glob("fields_*.npz")) + \
                [folder / "mesh.npz", folder / "amr_state.npz"]:
            try:
                f.unlink(missing_ok=True)          # keep S-parameters, convergence and the log only
            except OSError:
                pass

    def run(self) -> bool:
        from .optimize import Evaluation
        (self.out / "ABORT").unlink(missing_ok=True)
        n = len(self.rows)
        log(f"Parametric '{self.ps.name}': {n} variation(s) of {', '.join(self.table['variables'])}, "
            f"setup(s) {', '.join(self.setups)}")
        tasks = []                                        # (row, setup, folder, design path)
        for texts in self.rows:
            vid = variation_id(texts)
            vfold = self.out / "variations" / vid
            vfold.mkdir(parents=True, exist_ok=True)
            p = variation_project(self.proj, self.des.name, texts, self.save_fields)
            path = vfold / "design.hzn"
            p.save(path)
            row = {"id": vid, "variables": texts, "state": {}, "error": {}, "calcs": {}}
            self.table["variations"].append(row)
            for s in self.setups:
                folder = vfold / _safe(s)
                if _is_current(folder, _setup_keys(p, self.des.name, s)):
                    row["state"][s] = "done"
                else:
                    row["state"][s] = "pending"
                    tasks.append((row, s, folder, path))
            if not any(v == "pending" for v in row["state"].values()):
                self._calcs(row)
        self._write()
        reused = n * len(self.setups) - len(tasks)
        if reused:
            log(f"{reused} solution(s) of earlier runs are still valid and are reused")
        workers = max(1, min(self.parallel, len(tasks)))
        running: list[tuple[tuple, Evaluation]] = []
        queue = list(tasks)
        done, total, last = 0, max(1, len(tasks)), 0.0
        k = 0
        while queue or running:
            if self.aborted():
                for _, e in running:
                    e.kill()
                for row in self.table["variations"]:
                    for s, v in row["state"].items():
                        if v in ("pending", "running"):
                            row["state"][s] = "aborted"
                self._write()
                break
            while queue and len(running) < workers:
                task = queue.pop(0)
                row, s, folder, path = task
                shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True, exist_ok=True)
                k += 1
                cmd = [sys.executable, "-m", "hertzon.solver.run", str(path), self.des.name, s, str(folder)]
                running.append((task, Evaluation(k, [], row["variables"], folder, cmd, self._env(workers))))
                row["state"][s] = "running"
                log(f"Variation {self.table['variations'].index(row) + 1} of {n}: {variation_label(row['variables'])}"
                    + (f", {s}" if len(self.setups) > 1 else ""))
                self._write()
            for item in list(running):
                (row, s, folder, path), e = item
                if not e.finished():
                    continue
                running.remove(item)
                done += 1
                ok = e.proc.returncode == 0 and e.done_seen and not e.error
                row["state"][s] = "done" if ok else "failed"
                if not ok:
                    row["error"][s] = e.error or f"solver ended with code {e.proc.returncode} (see solver.log)"
                    log(f"{variation_label(row['variables'])}, {s}: {row['error'][s]}", "warning")
                self._strip(folder)
                if not any(v in ("pending", "running") for v in row["state"].values()):
                    self._calcs(row)
                self._write()
                emit("param", done=done, total=len(tasks))
            now = time.time()
            if now - last > 0.5:
                last = now
                frac = (done + sum(e.progress / 100 for _, e in running)) / total
                emit("progress", value=100 * frac, text=f"Parametric {self.ps.name}: {done} of {len(tasks)} solved")
            time.sleep(0.2)
        stopped = self.aborted()
        failed = sum(1 for r in self.table["variations"] for v in r["state"].values() if v == "failed")
        state = "aborted" if stopped else "done"
        ResultStore(self.out).write_json("status.json", {"state": state, "finished": time.time(), "failed": failed})
        if stopped:
            log(f"Parametric '{self.ps.name}' aborted", "warning")
            return False
        log(f"Parametric '{self.ps.name}' finished: {n} variation(s)" + (f", {failed} failed" if failed else ""))
        emit("progress", value=100, text="Done")
        emit("done", elapsed=time.time() - self.t0)
        return True


def emit(kind: str, **data) -> None:
    from .optimize import emit as _emit
    _emit(kind, **data)


def log(msg: str, level: str = "info") -> None:
    emit("log", level=level, msg=msg)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    if len(sys.argv) != 5:
        print("usage: python -m hertzon.solver.parametric PROJECT DESIGN NAME OUTDIR", file=sys.stderr)
        return 2
    out = Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    try:
        ResultStore(out).write_json("status.json", running_status())
        return 0 if ParametricRun(sys.argv[1], sys.argv[2], sys.argv[3], out).run() else 3
    except Exception as ex:  # noqa: BLE001
        emit("log", level="error", msg=f"{type(ex).__name__}: {ex}")
        emit("traceback", text=traceback.format_exc())
        try:
            ResultStore(out).write_json("status.json", {"state": "failed", "error": str(ex)})
        except Exception:  # noqa: BLE001
            pass
        emit("failed", error=str(ex))
        return 1


if __name__ == "__main__":
    sys.exit(main())
