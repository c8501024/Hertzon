"""Solver benchmark: how the solve time scales with the number of cores (and the parallel sweep processes)
on this computer.

python -m hertzon.solver.benchmark PROJECT DESIGN SETUP OUTDIR [--cores 1,2,4] [--workers 1,2,4]
                                   [--solvers "PARDISO,SPARTA (CPU),SPARTA (GPU)"]

The setup is solved once per core count (1 core first), each time in a fresh solver process with
HERTZON_CORES set, as the GUI does. With --workers the sweeps are then solved again with that many
parallel sweep processes on the adaptive mesh of the fastest run (the fewest cores within 2 %). Every
run is in OUTDIR/runs/<name>; the times per phase are in OUTDIR/benchmark.json. A file OUTDIR/ABORT
stops it.

With --solvers every core count is solved with each matrix solver (the setup's own when not given).
Each solver refines its own mesh, so the sweeps of every further solver are then also solved on the
final mesh of the first solver's run with the most cores: the same matrices, so the sweep times and
the largest difference of the S-parameters to the first solver compare the solvers alone.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
import traceback
from pathlib import Path

from ..model.design import Project
from .results import ResultStore, _safe, running_status

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "examples" / "benchmark" / "scaling"
# built-in cases: (title, file, design, setup)
CASES = [("Microstrip line, 2 ports (about 13k tetrahedra)", "small_microstrip.hzn", "Microstrip50", "Setup1"),
         ("2.45 GHz patch antenna with far field (about 20k)", "large_patch.hzn", "Patch245", "Setup1"),
         ("Hairpin band-pass filter, 3 resonators (about 42k)", "medium_hairpin.hzn", "Hairpin_N3", "Setup1")]


def default_cores(n: int | None = None) -> list[int]:
    """1, 2, 4, ... up to all cores, and all cores."""
    n = n or os.cpu_count() or 1
    out, k = [], 1
    while k < n:
        out.append(k)
        k *= 2
    return out + [n]


def machine() -> dict:
    info = {"cpu": platform.processor() or platform.machine(), "logical_cores": os.cpu_count() or 1,
            "system": f"{platform.system()} {platform.release()}", "python": platform.python_version()}
    try:
        import psutil
        info["physical_cores"] = psutil.cpu_count(logical=False)
        info["memory_gb"] = round(psutil.virtual_memory().total / 2 ** 30, 1)
    except Exception:  # noqa: BLE001
        pass
    return info


def run_summary(folder: Path) -> dict:
    """Times per phase, passes, tetrahedra and peak memory of one solver run."""
    st = ResultStore(folder)
    prof = st.read_json("profile.json", []) or []
    conv = st.read_json("convergence.json", {}) or {}
    status = st.read_json("status.json", {}) or {}
    phases = {p["task"]: float(p["elapsed"]) for p in prof}
    passes = conv.get("passes", [])
    return {"total": float(status.get("elapsed") or (prof[-1]["total"] if prof else 0.0)),
            "mesh": phases.get("Initial meshing", 0.0),
            "adaptive": phases.get("Adaptive meshing", 0.0),
            "sweep": sum(v for k, v in phases.items() if k.startswith("Sweep ")),
            "passes": len(passes), "tets": int(conv.get("final_tets") or (passes[-1]["tets"] if passes else 0)),
            "pass_times": [float(p.get("time", 0.0)) for p in passes],
            "memory_mb": max((float(p.get("memory", 0.0)) for p in prof), default=0.0),
            "converged": bool(conv.get("converged", False)), "notes": solver_notes(folder)}


def solver_notes(folder: Path) -> list[str]:
    """The run's warnings about its matrix solver (not installed, no usable GPU): a solver comparison that
    silently measured another solver would mislead."""
    tag = "@@HZN "
    out = []
    try:
        lines = (folder / "solver.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.startswith(tag):
            continue
        try:
            msg = json.loads(line[len(tag):])
        except ValueError:
            continue
        text = str(msg.get("msg", ""))
        if msg.get("type") == "log" and msg.get("level") == "warning" and text.startswith("Solver ") \
                and text not in out:
            out.append(text)
    return out


class Benchmark:
    def __init__(self, project: str, design: str, setup: str, out: Path, cores: list[int], workers: list[int],
                 solvers: list[str] | None = None):
        self.out = out
        self.src = Project.load(project)
        self.design, self.setup = design, setup
        st = self.src.design(design).setup(setup)
        self.sweeps = [s.name for s in st.sweeps if s.props.get("Enabled", "True") == "True"]
        self.cores = sorted({max(1, int(c)) for c in cores})
        self.workers = sorted({max(1, int(w)) for w in workers})
        self.solvers = list(dict.fromkeys(solvers or []))      # [] : the setup's own solver
        self.t0 = time.time()
        self.result = {"project": str(project), "design": design, "setup": setup, "machine": machine(),
                       "started": time.time(), "runs": []}

    def aborted(self) -> bool:
        return (self.out / "ABORT").exists()

    def _project(self, name: str, parallel_sweeps: bool = False, solver: str = "") -> Path:
        p = self.src.clone()
        d = p.design(self.design)
        d.reports, d.field_plots, d.optimetrics, d.parametrics = [], [], [], []
        for st in d.setups:
            st.props["Threads"] = "0"                # the cores come from HERTZON_CORES
            st.props["SaveFields"] = "False"
            if solver:
                st.props["Solver"] = solver
            for sw in st.sweeps:
                sw.props["SaveFields"] = "False"
                if parallel_sweeps:
                    sw.props["Parallel"] = "True"
        path = self.out / f"{name}.hzn"
        p.save(path)
        return path

    def _env(self, cores: int, workers: int, only_sweep: str = "") -> dict:
        env = dict(os.environ)
        env.pop("HERTZON_SOLVER", None)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["HERTZON_CORES"] = str(cores)
        for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            env[v] = str(cores)
        env["HERTZON_SWEEP_WORKERS"] = str(workers)
        env["HERTZON_MESH_ONLY"] = "0"
        env["HERTZON_ONLY_SWEEP"] = only_sweep
        return env

    def _solve(self, path: Path, folder: Path, env: dict, label: str, k: int, n: int) -> bool:
        from .optimize import Evaluation
        cmd = [sys.executable, "-m", "hertzon.solver.run", str(path), self.design, self.setup, str(folder)]
        e = Evaluation(k, [], {}, folder, cmd, env)
        last = 0.0
        while not e.finished():
            if self.aborted():
                e.kill()
                return False
            now = time.time()
            if now - last > 0.5:
                last = now
                emit("progress", value=100 * (k - 1 + e.progress / 100) / n, text=f"Run {k} of {n}: {label}")
            time.sleep(0.2)
        if e.proc.returncode != 0 or not e.done_seen or e.error:
            raise RuntimeError(f"{label}: {e.error or f'solver ended with code {e.proc.returncode}'} "
                               f"(see {folder / 'solver.log'})")
        return True

    def _write(self) -> None:
        ResultStore(self.out).write_json("benchmark.json", self.result)

    def run(self) -> bool:
        from .bridge import _first_run
        (self.out / "ABORT").unlink(missing_ok=True)
        if _first_run():
            log("Compiling the solver kernels first, so that no run includes it")
            from .warmup import run as warmup
            warmup(lambda s: None)
        solvers = self.solvers or [""]
        n = len(self.cores) * len(solvers) + (len(self.workers) if self.sweeps else 0) \
            + ((len(solvers) - 1) if self.sweeps else 0)
        log(f"Benchmark {self.design} : {self.setup}: cores {', '.join(map(str, self.cores))}"
            + (f"; solvers {', '.join(solvers)}" if self.solvers else "")
            + (f"; parallel sweep processes {', '.join(map(str, self.workers))}" if self.workers and self.sweeps else ""))
        k = 0
        runs = self.out / "runs"
        for sv in solvers:
            path = self._project(f"case_{_safe(sv)}" if sv else "case", solver=sv)
            for c in self.cores:
                k += 1
                folder = runs / (f"{_safe(sv)}_cores_{c}" if sv else f"cores_{c}")
                shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True)
                label = f"{sv}, {c} core(s)" if sv else f"{c} core(s)"
                if not self._solve(path, folder, self._env(c, 1), label, k, n):
                    return self._stop()
                row = {"kind": "cores", "cores": c, "workers": 1, **run_summary(folder)}
                if sv:
                    row["solver"] = sv
                self.result["runs"].append(row)
                self._write()
                log(f"{label}: {row['total']:.1f} s (passes {row['adaptive']:.1f} s, sweep {row['sweep']:.1f} s), "
                    f"{row['passes']} passes, {row['tets']} tetrahedra")
        if len(solvers) > 1 and self.sweeps:
            # the sweeps of every further solver on the first solver's final mesh: the same matrices
            ref = runs / f"{_safe(solvers[0])}_cores_{self.cores[-1]}"
            for sv in solvers[1:]:
                k += 1
                folder = runs / f"{_safe(sv)}_on_{_safe(solvers[0])}_mesh"
                shutil.rmtree(folder, ignore_errors=True)
                shutil.copytree(ref, folder)
                # the first solver's project, so that its adaptive solution is reused; the solver from the
                # environment
                path = self.out / f"case_{_safe(solvers[0])}.hzn"
                total = 0.0
                for sw in self.sweeps:
                    for f in folder.glob(f"sparams_{_safe(sw)}.npz"):
                        f.unlink()
                    t = time.time()
                    if not self._solve(path, folder, dict(self._env(self.cores[-1], 1, sw), HERTZON_SOLVER=sv),
                                       f"{sv}: sweep on the mesh of {solvers[0]}", k, n):
                        return self._stop()
                    prof = ResultStore(folder).read_json("profile.json", []) or []
                    total += sum(float(p["elapsed"]) for p in prof if p["task"].startswith("Sweep ")) or \
                        (time.time() - t)
                diff = {sw: s_difference(ref, folder, sw) for sw in self.sweeps}
                row = {"kind": "same_mesh", "solver": sv, "reference": solvers[0], "cores": self.cores[-1],
                       "sweep": total, "ds_max": diff}
                self.result["runs"].append(row)
                self._write()
                log(f"{sv} on the mesh of {solvers[0]}: sweep {total:.1f} s, largest |dS| "
                    + ", ".join(f"{sw} {d:.2e}" for sw, d in diff.items()))
        if self.workers and self.sweeps:
            # the fewest cores that are as fast as the fastest run (within 2 %): on a computer with
            # hyperthreading the physical ones, so that the parallel frequencies do not share cores
            cr = sorted((r for r in self.result["runs"] if r["kind"] == "cores"), key=lambda r: r["cores"])
            fastest = min(r["total"] for r in cr)
            best = next(r for r in cr if r["total"] <= 1.02 * fastest)
            ppath = self._project("case_parallel", parallel_sweeps=True)
            for w in self.workers:
                k += 1
                folder = runs / f"sweep_{w}"
                shutil.rmtree(folder, ignore_errors=True)
                shutil.copytree(runs / f"cores_{best['cores']}", folder)
                total = 0.0
                for sw in self.sweeps:
                    for f in folder.glob(f"sparams_{_safe(sw)}.npz"):
                        f.unlink()
                    t = time.time()
                    if not self._solve(ppath, folder, self._env(best["cores"], w, sw),
                                       f"sweep with {w} parallel process(es), {best['cores']} cores", k, n):
                        return self._stop()
                    prof = ResultStore(folder).read_json("profile.json", []) or []
                    total += sum(float(p["elapsed"]) for p in prof if p["task"].startswith("Sweep ")) or \
                        (time.time() - t)
                row = {"kind": "sweep", "cores": best["cores"], "workers": w, "sweep": total}
                self.result["runs"].append(row)
                self._write()
                log(f"Sweep with {w} parallel process(es): {total:.1f} s")
        self.result["finished"] = time.time()
        self._write()
        ResultStore(self.out).write_json("status.json", {"state": "done", "finished": time.time()})
        emit("progress", value=100, text="Benchmark done")
        emit("done", elapsed=time.time() - self.t0)
        return True

    def _stop(self) -> bool:
        self._write()
        ResultStore(self.out).write_json("status.json", {"state": "aborted"})
        log("Benchmark aborted", "warning")
        return False


def s_difference(ref: Path, folder: Path, sweep: str) -> float:
    """Largest |S - S_ref| over the frequencies and port pairs both runs of a sweep solved."""
    import numpy as np
    a, b = ResultStore(ref).sparams(sweep), ResultStore(folder).sparams(sweep)
    if not a or not b:
        return float("nan")
    fa, fb = np.asarray(a["freq"], float), np.asarray(b["freq"], float)
    common = np.intersect1d(np.round(fa, 3), np.round(fb, 3))
    if not len(common):
        return float("nan")
    ia = [int(np.argmin(abs(fa - f))) for f in common]
    ib = [int(np.argmin(abs(fb - f))) for f in common]
    return float(np.max(np.abs(np.asarray(a["S"])[ia] - np.asarray(b["S"])[ib])))


def emit(kind: str, **data) -> None:
    from .optimize import emit as _emit
    _emit(kind, **data)


def log(msg: str, level: str = "info") -> None:
    emit("log", level=level, msg=msg)


def _ints(text: str) -> list[int]:
    return [int(x) for x in text.replace(";", ",").split(",") if x.strip()]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    args = sys.argv[1:]
    opts = {}
    for key in ("--cores", "--workers", "--solvers"):
        if key in args:
            i = args.index(key)
            opts[key] = args[i + 1]
            del args[i:i + 2]
    if len(args) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(args[3])
    out.mkdir(parents=True, exist_ok=True)
    try:
        ResultStore(out).write_json("status.json", running_status())
        cores = _ints(opts.get("--cores", "")) or default_cores()
        workers = _ints(opts.get("--workers", ""))
        solvers = [x.strip() for x in opts.get("--solvers", "").split(",") if x.strip()]
        return 0 if Benchmark(args[0], args[1], args[2], out, cores, workers, solvers).run() else 3
    except Exception as ex:  # noqa: BLE001
        emit("log", level="error", msg=f"{type(ex).__name__}: {ex}")
        emit("traceback", text=traceback.format_exc())
        ResultStore(out).write_json("status.json", {"state": "failed", "error": str(ex)})
        emit("failed", error=str(ex))
        return 1


if __name__ == "__main__":
    sys.exit(main())
