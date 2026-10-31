"""Optimizer process: python -m hertzon.solver.optimize PROJECT DESIGN OPTIMIZATION OUTDIR

Every evaluation is a copy of the project with the variables set, solved by its own solver process
(hertzon.solver.run) in OUTDIR/evals/<n>. The gradient samples run in parallel (ParallelEvaluations,
the cores are shared). Progress goes to stdout in the solver's @@HZN protocol; the evaluations are in
OUTDIR/history.json, the best one in OUTDIR/best.json. A file OUTDIR/ABORT stops the run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from ..model.design import Project
from .expressions import output_variables
from .optimetrics import ConjugateGradient, Variables, antenna_data, check_antenna_goals, evaluate_goals, uses_antenna
from .results import ResultStore

TAG = "@@HZN "
ROOT = Path(__file__).resolve().parents[2]
_LOCK = threading.Lock()


def emit(kind: str, **data) -> None:
    with _LOCK:
        sys.stdout.write(TAG + json.dumps({"type": kind, **data}, default=str) + "\n")
        sys.stdout.flush()


def log(msg: str, level: str = "info") -> None:
    emit("log", level=level, msg=msg)


class Evaluation:
    """One candidate design solved in its own solver process."""

    def __init__(self, n: int, x, texts: dict, folder: Path, cmd: list[str], env: dict):
        self.n, self.x, self.texts, self.folder = n, list(x), texts, folder
        self.progress = 0.0
        self.done_seen = False
        self.error = ""
        self.log = open(folder / "solver.log", "w", encoding="utf-8", errors="replace")
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env)
        if os.name == "nt":
            kw["creationflags"] = 0x08000000                   # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(cmd, **kw)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            self.log.write(line)
            if not line.startswith(TAG):
                continue
            try:
                msg = json.loads(line[len(TAG):])
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t == "progress":
                self.progress = float(msg.get("value", 0))
            elif t == "done":
                self.done_seen = True
            elif t == "failed":
                self.error = str(msg.get("error", "failed"))
        self.log.close()

    def finished(self) -> bool:
        return self.proc.poll() is not None and not self.reader.is_alive()

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


class Optimizer:
    def __init__(self, project_path: str, design: str, name: str, out: Path):
        self.proj = Project.load(project_path)
        self.des = self.proj.design(design)
        self.opt = next(o for o in self.des.optimetrics if o.name == name)
        self.out = out
        self.ev = self.des.evaluator(self.proj)
        self.vars = Variables(self.des, self.opt, self.ev)
        setup, _, sol = [x.strip() for x in self.opt.solution.partition(":")]
        if not setup or not sol:
            raise ValueError("Choose the solution the goals are evaluated on (setup and sweep)")
        self.setup, self.sol = setup, sol
        st = self.des.setup(setup)
        if sol != "LastAdaptive" and not any(sw.name == sol for sw in st.sweeps):
            raise ValueError(f"Sweep '{sol}' not found in {setup}")
        self.props = self.opt.props
        self.max_evals = max(1, int(float(self.props.get("MaxEvaluations", 3000))))
        self.parallel = max(1, int(float(self.props.get("ParallelEvaluations", 6))))
        self.cores = max(1, int(os.environ.get("HERTZON_CORES") or os.cpu_count() or 1))
        self.outvars = output_variables(self.des)
        self.antenna_goals = any(uses_antenna(g.get("calc", ""), self.outvars) for g in self.opt.goals)
        if self.antenna_goals:
            check_antenna_goals(self.des, setup, sol)
        self.history: list[dict] = []
        self.cache: dict[tuple, float] = {}
        self.best: dict | None = None
        self.n = 0
        self.t0 = time.time()

    # ---- one evaluation ---------------------------------------------------------------------
    def _project_for(self, texts: dict) -> Project:
        p = self.proj.clone()
        d = p.design(self.des.name)
        d.variables.update(texts)
        d.optimetrics = []
        d.reports, d.field_plots = [], []
        for st in d.setups:
            if st.name != self.setup:
                continue
            st.props["SaveFields"] = "False"
            for sw in st.sweeps:
                on = sw.name == self.sol
                sw.props["Enabled"] = "True" if on else "False"
                sw.props["SaveFields"] = "False"
        return p

    def start(self, x, workers: int) -> Evaluation:
        self.n += 1
        texts = self.vars.texts(x)
        folder = self.out / "evals" / f"{self.n:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        p = self._project_for(texts)
        path = folder / "design.hzn"
        p.save(path)
        res = folder / "results"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        per = str(max(1, self.cores // max(1, workers)))
        env["HERTZON_CORES"] = per
        for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            env[v] = per
        env["HERTZON_SWEEP_WORKERS"] = "1" if workers > 1 else env.get("HERTZON_SWEEP_WORKERS", "2")
        env["HERTZON_MESH_ONLY"] = "0"
        env["HERTZON_ONLY_SWEEP"] = ""
        cmd = [sys.executable, "-m", "hertzon.solver.run", str(path), self.des.name, self.setup, str(res)]
        return Evaluation(self.n, x, texts, folder, cmd, env)

    def finish(self, e: Evaluation, phase: str) -> float:
        st = ResultStore(e.folder / "results")
        row = {"eval": e.n, "phase": phase, "variables": e.texts, "x": e.x, "time": time.time() - self.t0}
        cost = float("inf")
        try:
            data = st.sparams(self.sol)
            if data is None:
                raise RuntimeError(e.error or "no result (the solver stopped early; see solver.log)")
            from .expressions import with_pairs
            antenna = None
            if self.antenna_goals:
                try:
                    antenna = antenna_data(st, self.des, self.sol, self.ev)
                except Exception as ex:  # noqa: BLE001
                    antenna = str(ex)
            cost, goals = evaluate_goals(self.opt, with_pairs(data, self.des), self.ev, self.outvars, antenna)
            row.update(cost=cost, goals=goals)
        except Exception as ex:  # noqa: BLE001
            row.update(cost=None, error=str(ex))
            log(f"Evaluation {e.n} failed: {ex}", "warning")
        for f in list(st.folder.glob("solution_*.npz")) + list(st.folder.glob("fields_*.npz")) + \
                [st.folder / "mesh.npz", st.folder / "amr_state.npz"]:
            try:
                f.unlink(missing_ok=True)          # keep S-parameters, convergence and the log only
            except OSError:
                pass
        self.history.append(row)
        if np.isfinite(cost) and (self.best is None or cost < self.best["cost"]):
            self.best = row
            (self.out / "best.json").write_text(json.dumps(row, indent=1), encoding="utf-8")
        (self.out / "history.json").write_text(json.dumps({"variables": self.vars.names, "evaluations": self.history},
                                                          indent=1, default=str), encoding="utf-8")
        emit("optim", eval=e.n, cost=row.get("cost"), best=self.best["cost"] if self.best else None)
        msg = f"Evaluation {e.n} ({phase}): cost {cost:.6g}" if np.isfinite(cost) else None
        if msg:
            log(msg + "   " + ", ".join(f"{k}={v}" for k, v in e.texts.items()))
        return cost

    # ---- loop -------------------------------------------------------------------------------
    def aborted(self) -> bool:
        return (self.out / "ABORT").exists()

    def run(self) -> bool:
        cg = ConjugateGradient(self.vars.x0(), self.props)
        log(f"Optimization '{self.opt.name}': conjugate gradient over {len(self.vars)} variable(s) "
            f"({', '.join(self.vars.names)}), goals on {self.opt.solution}, up to {self.parallel} design(s) at once")
        (self.out / "ABORT").unlink(missing_ok=True)
        while not cg.done and self.n < self.max_evals:
            phase = cg.s["phase"]
            pts = cg.propose()[: self.max_evals - self.n]
            todo = []
            for p in pts:
                key = tuple(np.round(p, 9))
                if key in self.cache:
                    cg.observe(p, self.cache[key])
                else:
                    todo.append(p)
            if not todo:
                continue
            costs = self._solve_batch(todo, phase, cg.s.get("iteration", 0))
            if costs is None:
                break
            for p, c in zip(todo, costs):
                self.cache[tuple(np.round(p, 9))] = c
                cg.observe(p, c)
        stopped = self.aborted()
        reason = "Aborted" if stopped else (cg.reason or "Maximum number of evaluations reached")
        summary = {"reason": reason, "evaluations": self.n, "best": self.best, "elapsed": time.time() - self.t0,
                   "variables": self.vars.names}
        (self.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
        if self.best:
            log(f"Optimization '{self.opt.name}' finished: {reason}. Best cost {self.best['cost']:.6g} "
                f"(evaluation {self.best['eval']}): " + ", ".join(f"{k}={v}" for k, v in self.best["variables"].items()))
        else:
            log(f"Optimization '{self.opt.name}' finished: {reason}; no design could be evaluated", "warning")
        ResultStore(self.out).write_json("status.json", {"state": "aborted" if stopped else "done",
                                                         "finished": time.time(), "reason": reason})
        if stopped:
            return False
        emit("progress", value=100, text=f"Done: {reason}")
        emit("done", elapsed=time.time() - self.t0)
        return True

    def _solve_batch(self, points, phase: str, iteration: int):
        """Solve the points (at most ParallelEvaluations at once); None when aborted."""
        workers = min(self.parallel, len(points))
        queue = list(enumerate(points))
        running: list[tuple[int, Evaluation]] = []
        costs: list[float] = [float("inf")] * len(points)
        label = {"base": "start design", "gradient": "gradient", "line": "line search"}.get(phase, phase)
        done = 0
        last = 0.0
        while queue or running:
            if self.aborted():
                for _, e in running:
                    e.kill()
                return None
            while queue and len(running) < workers:
                i, p = queue.pop(0)
                running.append((i, self.start(p, workers)))
            for item in list(running):
                i, e = item
                if e.finished():
                    running.remove(item)
                    costs[i] = self.finish(e, label)
                    done += 1
            now = time.time()
            if now - last > 0.5:
                last = now
                frac = (done + sum(e.progress / 100 for _, e in running)) / len(points)
                best = f", best cost {self.best['cost']:.4g}" if self.best else ""
                emit("progress", value=100 * frac,
                     text=f"Iteration {iteration + 1}, {label}: {done} of {len(points)} design(s) "
                          f"(evaluation {self.n}){best}")
            time.sleep(0.2)
        return costs


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    if len(sys.argv) != 5:
        print("usage: python -m hertzon.solver.optimize PROJECT DESIGN OPTIMIZATION OUTDIR", file=sys.stderr)
        return 2
    out = Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    try:
        ResultStore(out).write_json("status.json", {"state": "running", "started": time.time()})
        return 0 if Optimizer(sys.argv[1], sys.argv[2], sys.argv[3], out).run() else 3
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
