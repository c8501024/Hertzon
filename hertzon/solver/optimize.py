"""Optimizer process: python -m hertzon.solver.optimize PROJECT DESIGN OPTIMIZATION OUTDIR

Every evaluation is a copy of the project with the variables set, solved by its own solver process
(hertzon.solver.run) in OUTDIR/evals/<n>. The gradient samples run in parallel (ParallelEvaluations,
the cores are shared). Progress goes to stdout in the solver's @@HZN protocol; the evaluations are in
OUTDIR/history.json, the best one in OUTDIR/best.json. A file OUTDIR/ABORT stops the run.

The optimizer "Derivative Trust Region" (hertzon.solver.trust_region) computes the derivative tuning model
of a solved design in its evaluation folder and minimizes the goals on the model; each step is checked
with a solve.
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
from .results import ResultStore, running_status
from . import trust_region as TR

TAG = "@@HZN "
TRUST_REGION = "Derivative Trust Region"
TUNING_MAX_VARIABLES = 6
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
        self.folders: dict[int, Path] = {}
        self.keep_mesh = False          # the trust region computes the tuning model on an evaluation's mesh
        self.trust = self.props.get("Optimizer", "Conjugate Gradient") == TRUST_REGION
        if self.trust and len(self.vars) > TUNING_MAX_VARIABLES:
            raise ValueError(f"Derivative Trust Region: at most {TUNING_MAX_VARIABLES} variables (the tuning "
                             f"model), {len(self.vars)} are included")

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
        cmd = [sys.executable, "-m", "hertzon.solver.run", str(path), self.des.name, self.setup, str(res)]
        return Evaluation(self.n, x, texts, folder, cmd, self._env(workers))

    def _env(self, workers: int, tuning: str = "") -> dict:
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
        env["HERTZON_TUNING"] = tuning
        return env

    def finish(self, e: Evaluation, phase: str) -> float:
        st = ResultStore(e.folder / "results")
        row = {"eval": e.n, "phase": phase, "variables": e.texts, "x": e.x, "time": time.time() - self.t0}
        self.folders[e.n] = e.folder
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
        if not self.keep_mesh:
            self.cleanup(e.folder)
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

    @staticmethod
    def cleanup(folder: Path) -> None:
        """Keep the S-parameters, the convergence and the log of an evaluation only."""
        res = folder / "results"
        for f in list(res.glob("solution_*.npz")) + list(res.glob("fields_*.npz")) + \
                list(res.glob("huygens_*.npz")) + list(res.glob("tuning_*.npz")) + \
                [res / "mesh.npz", res / "amr_state.npz"]:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

    # ---- loop -------------------------------------------------------------------------------
    def aborted(self) -> bool:
        return (self.out / "ABORT").exists()

    def run(self) -> bool:
        if self.trust:
            return self.run_trust_region()
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
        return self._end(cg.reason or "Maximum number of evaluations reached")

    def _end(self, reason: str) -> bool:
        stopped = self.aborted()
        reason = "Aborted" if stopped else reason
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

    # ---- derivative trust region ----------------------------------------------------------------
    def run_trust_region(self) -> bool:
        p = self.props
        radius = min(TR.MAX_RADIUS, max(TR.MIN_RADIUS, float(p.get("TrustRadius", 0.6))))
        rounds = max(1, int(float(p.get("MaxRounds", 10))))
        points = max(3, int(float(p.get("TuningPoints", 51))))
        self.keep_mesh = True
        log(f"Optimization '{self.opt.name}': derivative trust region over {len(self.vars)} variable(s) "
            f"({', '.join(self.vars.names)}), goals on {self.opt.solution}: every round solves the design, "
            "computes its tuning model and minimizes the goals on the model")
        (self.out / "ABORT").unlink(missing_ok=True)
        x = self.vars.x0()
        costs = self._solve_batch([x], "start design", 0)
        if costs is None:
            return self._end("Aborted")
        cost, cur = costs[0], self.n
        if not np.isfinite(cost):
            raise ValueError("The starting design could not be evaluated")
        model = None
        reason = f"{rounds} round(s) done"
        for it in range(rounds):
            if cost == 0:
                reason = "All goals are met (cost 0)"
                break
            if self.n >= self.max_evals:
                reason = "Maximum number of evaluations reached"
                break
            if model is None:
                try:
                    model = self._tuning_model(cur, it, points)
                except Exception as ex:  # noqa: BLE001
                    log(f"Round {it + 1}: {ex}", "warning")
                    reason = f"The tuning model of evaluation {cur} failed (see the log); the best design so far"
                    break
                if model is None:
                    return self._end("Aborted")
                base = model.cost(np.zeros(len(self.vars)))
            v = self.vars.values(x)
            blo, bhi = TR.step_bounds(model.m.ranges, radius, self.vars.lo, self.vars.hi, v)
            t = time.time()
            dp, pred_cost, nev = TR.minimize_box(model.cost, blo, bhi)
            predicted = base - pred_cost
            log(f"Round {it + 1}: model optimum in the region {radius:.0%} of the tuning range: cost "
                f"{pred_cost:.6g} (solved {cost:.6g}, model at the solved design {base:.6g}), {nev} model "
                f"evaluations in {time.time() - t:.1f} s; step " +
                ", ".join(f"{n} {d:+.4g} {u}" for n, d, u in zip(self.vars.names, dp, self.vars.units)))
            if predicted <= 1e-9 * max(base, 1e-30):
                if radius > TR.MIN_RADIUS and at_any_bound(dp, blo, bhi):
                    radius *= 0.5
                    continue
                reason = "The tuning model finds no further improvement"
                break
            xn = np.clip((v + dp - self.vars.lo) / (self.vars.hi - self.vars.lo), 0, 1)
            costs = self._solve_batch([xn], f"round {it + 1}", it + 1)
            if costs is None:
                return self._end("Aborted")
            new = costs[0]
            self.history[-1].update(predicted=pred_cost, radius=radius)
            taken, radius, rho = TR.update(radius, cost - new if np.isfinite(new) else -np.inf, predicted,
                                           TR.at_edge(dp, blo, bhi))
            log(f"Round {it + 1}: solved cost {new:.6g}, predicted {pred_cost:.6g} (actual / predicted "
                f"improvement {rho:.2f}): step {'taken' if taken else 'rejected'}, region now {radius:.0%}")
            if taken:
                self.cleanup(self.folders[cur])
                x, cost, cur, model = xn, new, self.n, None
            else:
                self.cleanup(self.folders[self.n])
            if radius < TR.MIN_RADIUS:
                reason = "The trust region became too small (the model no longer predicts the solves)"
                break
        else:
            reason = f"Maximum number of rounds ({rounds}) reached"
        if cost == 0:
            reason = "All goals are met (cost 0)"
        return self._end(reason)

    def _tuning_model(self, n: int, it: int, points: int):
        """Compute the tuning model of evaluation n on its adaptive mesh; None when aborted."""
        from .tuning import TuningModel, tuning_path
        folder = self.folders[n]
        res = folder / "results"
        work = folder / "tuning"
        work.mkdir(exist_ok=True)
        spec = json.dumps({"sweep": self.sol, "variables": self.vars.names, "points": points})
        cmd = [sys.executable, "-m", "hertzon.solver.run", str(folder / "design.hzn"), self.des.name, self.setup,
               str(res)]
        e = Evaluation(n, [], {}, work, cmd, self._env(1, spec))
        last = 0.0
        while not e.finished():
            if self.aborted():
                e.kill()
                return None
            now = time.time()
            if now - last > 0.5:
                last = now
                emit("progress", value=e.progress,
                     text=f"Round {it + 1}: derivatives of evaluation {n} (tuning model)")
            time.sleep(0.2)
        path = tuning_path(res, self.sol)
        if e.proc.returncode != 0 or not path.exists():
            why = e.error or (f"the solver process ended with code {e.proc.returncode}, for example out of "
                              f"memory; see {work / 'solver.log'}")
            raise RuntimeError(f"The tuning model of evaluation {n} failed: {why}")
        log(f"Round {it + 1}: tuning model of evaluation {n} ready")
        return SurrogateCost(self, TuningModel(path), ResultStore(res))

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


def at_any_bound(dp, blo, bhi) -> bool:
    return TR.at_edge(np.asarray(dp, float), np.asarray(blo, float), np.asarray(bhi, float))


class SurrogateCost:
    """The cost of the goals on a tuning model at variable offsets (the same goals and cost function as
    for a solved design)."""

    def __init__(self, opt: Optimizer, model, st: ResultStore):
        from .expressions import with_pairs
        self.o, self.m = opt, model
        self.base = with_pairs(st.sparams(opt.sol) or {}, opt.des)
        self.grid = None
        self.f_antenna = None
        if opt.antenna_goals:
            from .farfield_post import farfield
            if model.ff is None:
                raise ValueError("Goals on antenna parameters: the tuning model has no far field")
            ff = farfield(st, opt.des, opt.des.spheres[0].name, opt.sol, opt.ev)
            if ff is None:
                raise ValueError("Goals on antenna parameters: the solved design has no far field")
            self.grid = (np.asarray(ff["theta"], float), np.asarray(ff["phi"], float))
            self.f_antenna = antenna_data(st, opt.des, opt.sol, opt.ev)["freq"]

    def __call__(self, dp) -> float:
        return self.cost(dp)

    def cost(self, dp) -> float:
        from .tuning import tuned_data
        dp = np.asarray(dp, float)
        data = tuned_data(self.m, dp, self.base)
        antenna = None
        if self.grid is not None:
            f, vals = self.m.antenna_series(self.f_antenna, 0, dp, *self.grid)
            antenna = {"freq": f, "solved": f, "antenna": vals}
        try:
            return evaluate_goals(self.o.opt, data, self.o.ev, self.o.outvars, antenna)[0]
        except Exception:  # noqa: BLE001
            return float("inf")


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
        ResultStore(out).write_json("status.json", running_status())
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
