"""Optimizer process: python -m hertzon.solver.optimize PROJECT DESIGN OPTIMIZATION OUTDIR

Every evaluation is a copy of the project with the variables set, solved by its own solver process
(hertzon.solver.run) in OUTDIR/evals/<n>. The gradient samples run in parallel (ParallelEvaluations,
the cores are shared). Progress goes to stdout in the solver's @@HZN protocol; the evaluations are in
OUTDIR/history.json, the best one in OUTDIR/best.json. A file OUTDIR/ABORT stops the run.

Every evaluation keeps its S-parameters, convergence and log; the best one also keeps its mesh and the
fields at the adaptive frequency, so that 'Apply to Design' shows its solution without solving again
(hertzon.solver.parametric.matching_evaluation).

The optimizer "Latin Hypercube + Nelder-Mead" (optimetrics.SampledNelderMead) solves a Latin hypercube sample of
the variable box and then runs Nelder-Mead chains from the best sample points side by side.

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
from .optimetrics import (ConjugateGradient, SampledNelderMead, Variables, antenna_data, check_antenna_goals,
                          evaluate_goals, uses_antenna)
from .results import ResultStore, running_status
from . import trust_region as TR

TAG = "@@HZN "
TRUST_REGION = "Derivative Trust Region"
SPACE_MAPPING = "Space Mapping"
NELDER_MEAD = "Latin Hypercube + Nelder-Mead"
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


class CircuitEvaluation:
    """One candidate circuit design, analyzed at once in this process (same interface as Evaluation)."""

    def __init__(self, n: int, x, texts: dict, folder: Path, data: dict | None, error: str = ""):
        self.n, self.x, self.texts, self.folder = n, list(x), texts, folder
        self.data, self.error = data, error
        self.progress = 100.0

    def finished(self) -> bool:
        return True

    def kill(self) -> None:
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
        self.circuit = getattr(self.des, "kind", "3d") == "circuit"
        if self.circuit and sol == "LastAdaptive":
            raise ValueError("A circuit has no adaptive solution: choose a frequency sweep for the goals")
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
        self.multi = False
        self.history: list[dict] = []
        self.cache: dict[tuple, float] = {}
        self.best: dict | None = None
        self.n = 0
        self.t0 = time.time()
        self.folders: dict[int, Path] = {}
        self.keep_mesh = False          # the trust region computes the tuning model on an evaluation's mesh
        self.algorithm = self.props.get("Optimizer", "Conjugate Gradient")
        self.trust = self.algorithm == TRUST_REGION
        self.mapping = self.algorithm == SPACE_MAPPING
        self.coarse_name = str(self.props.get("CoarseModel", "")).strip()
        self.layout_sync: dict[str, str] = {}
        if self.circuit and self.props.get("MultiFidelity") == "True":
            self._to_layout()          # multi-fidelity: the circuit is the coarse model of its 3D layout
        elif self.mapping and self.circuit:
            raise ValueError("Space Mapping optimizes a 3D design with a circuit as its coarse model: add the "
                             "optimization to the 3D design, or switch on Multi-fidelity in the optimization of "
                             "the circuit")
        if self.trust and self.circuit:
            raise ValueError("Derivative Trust Region needs the tuning model of a 3D mesh: use Conjugate Gradient "
                             "or Latin Hypercube + Nelder-Mead for a circuit (every evaluation takes milliseconds)")
        if self.circuit:
            from .sweeps import sweep_frequencies
            sw = next(sw for sw in st.sweeps if sw.name == sol)
            self.freqs = sweep_frequencies(sw.props, self.ev)
            self.parallel = 1
        self._written = 0.0
        if self.trust and len(self.vars) > TUNING_MAX_VARIABLES:
            raise ValueError(f"Derivative Trust Region: at most {TUNING_MAX_VARIABLES} variables (the tuning "
                             f"model), {len(self.vars)} are included")

    def _to_layout(self) -> None:
        """Multi-fidelity optimization of a circuit: its generated 3D layout (the same variables, see
        circuit_layout) is the fine model, the circuit the coarse one of a space mapping. The evaluations solve
        the layout; the goals, variables and ranges are those of the circuit's optimization."""
        from ..model import circuit_layout as CL
        circuit = self.des
        name = str(self.props.get("FineModel", "")).strip()
        if name:
            try:
                layout = self.proj.design(name)
            except KeyError:
                raise ValueError(f"Multi-fidelity: no design named '{name}'") from None
            if getattr(layout, "kind", "3d") == "circuit":
                raise ValueError(f"Multi-fidelity: '{name}' is a circuit; enter its 3D layout")
        else:
            found = CL.layouts_of(self.proj, circuit)
            if not found:
                raise ValueError(f"Multi-fidelity: {circuit.name} has no 3D layout. Create it with 'Create 3D "
                                 "Layout' (Simulation tab of the schematic) and set up its analysis")
            layout = found[0]
        missing = [v for v in self.vars.names if v not in layout.variables]
        if missing:
            raise ValueError(f"Multi-fidelity: the 3D design {layout.name} has no variable {', '.join(missing)}; "
                             "update the layout from the schematic ('Update 3D Layout')")
        sol = str(self.props.get("FineSolution", "")).strip()
        if sol:
            setup, _, sweep = [x.strip() for x in sol.partition(":")]
        else:
            st = next((s for s in layout.setups if not s.eigen and s.sweeps), None)
            if st is None:
                raise ValueError(f"Multi-fidelity: the 3D design {layout.name} has no setup with a frequency sweep")
            sw = next((w for w in st.sweeps if w.name == self.sol), None) or \
                next((w for w in st.sweeps if w.props.get("Enabled", "True") == "True"), st.sweeps[0])
            setup, sweep = st.name, sw.name
        try:
            st = layout.setup(setup)
        except KeyError:
            raise ValueError(f"Multi-fidelity: {layout.name} has no setup {setup}") from None
        if not any(w.name == sweep for w in st.sweeps):
            raise ValueError(f"Multi-fidelity: sweep '{sweep}' not found in {layout.name} {setup}")
        for w in CL.layout_differences(self.proj, layout):
            log(f"Multi-fidelity: {w}", "warning")
        # the other circuit variables (not optimized) take their circuit values in the layout as well
        self.layout_sync = {k: v for k, v in circuit.variables.items()
                            if k in layout.variables and k not in self.vars.names}
        self.coarse_name, self.des, self.setup, self.sol = circuit.name, layout, setup, sweep
        self.ev = layout.evaluator(self.proj)
        self.circuit, self.mapping, self.trust = False, True, False
        self.parallel = 1
        self.multi = True
        log(f"Multi-fidelity: {circuit.name} is the coarse model, its 3D layout {layout.name} ({setup} : "
            f"{sweep}) the fine one")

    # ---- one evaluation ---------------------------------------------------------------------
    def _project_for(self, texts: dict) -> Project:
        p = self.proj.clone()
        d = p.design(self.des.name)
        d.variables.update(self.layout_sync)
        d.variables.update(texts)
        d.optimetrics = []
        d.reports, d.field_plots = [], []
        for st in d.setups:
            if st.name != self.setup:
                continue
            # the setup keeps its own Save fields (the fields at the adaptive frequency): the best evaluation
            # keeps them, the others are removed after their cost is known (cleanup). The sweeps stay switched
            # on or off as in the design: they size the open region, and the adaptive mesh of an evaluation is
            # taken over by Apply to Design. Only the goal sweep is solved (HERTZON_GOAL_SWEEP, _env). Goals on
            # the setup alone (LastAdaptive): all sweeps off, the open region is the one of the setup.
            # Goals on antenna parameters over a sweep need the fields of that sweep (huygens_<sweep>.npz,
            # check_antenna_goals): the goal sweep keeps Save fields on; finish() reads the antenna data
            # before cleanup() removes the fields of the evaluations that are not the best.
            for sw in st.sweeps:
                if not (self.antenna_goals and sw.name == self.sol):
                    sw.props["SaveFields"] = "False"
                if self.sol == "LastAdaptive":
                    # goals on the setup alone: no sweep is solved, so none sizes the open region either
                    sw.props["Enabled"] = "False"
        return p

    def start(self, x, workers: int) -> Evaluation:
        self.n += 1
        texts = self.vars.texts(x)
        folder = self.out / "evals" / f"{self.n:04d}"
        if self.circuit:
            return self._analyze_circuit(x, texts, folder)
        folder.mkdir(parents=True, exist_ok=True)
        p = self._project_for(texts)
        path = folder / "design.hzn"
        p.save(path)
        res = folder / "results"
        cmd = [sys.executable, "-m", "hertzon.solver.run", str(path), self.des.name, self.setup, str(res)]
        return Evaluation(self.n, x, texts, folder, cmd, self._env(workers))

    def _analyze_circuit(self, x, texts: dict, folder: Path) -> CircuitEvaluation:
        from .circuit import analyze
        p = self._project_for(texts)
        try:
            r = analyze(p, p.design(self.des.name), self.freqs)
        except Exception as ex:  # noqa: BLE001
            return CircuitEvaluation(self.n, x, texts, folder, None, str(ex))
        ports = [{"name": n, "number": i + 1, "kind": "Circuit Port", "modes": 1} for i, n in enumerate(r.ports)]
        data = {"freq": r.freq, "S": r.S, "z0": r.z0, "solved": r.freq, "ports": ports}
        return CircuitEvaluation(self.n, x, texts, folder, data)

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
        env["HERTZON_GOAL_SWEEP"] = self.sol
        return env

    def finish(self, e: Evaluation, phase: str) -> float:
        row = {"eval": e.n, "phase": phase, "variables": e.texts, "x": e.x, "time": time.time() - self.t0}
        self.folders[e.n] = e.folder
        cost = float("inf")
        try:
            if isinstance(e, CircuitEvaluation):
                data = e.data
            else:
                st = ResultStore(e.folder / "results")
                data = st.sparams(self.sol)
            if data is None:
                raise RuntimeError(e.error or "no result (the solver stopped early; see solver.log)")
            from .expressions import with_pairs
            antenna = None
            if self.antenna_goals and not isinstance(e, CircuitEvaluation):
                try:
                    antenna = antenna_data(st, self.des, self.sol, self.ev)
                except Exception as ex:  # noqa: BLE001
                    antenna = str(ex)
            cost, goals = evaluate_goals(self.opt, with_pairs(data, self.des), self.ev, self.outvars, antenna)
            row.update(cost=cost, goals=goals)
        except Exception as ex:  # noqa: BLE001
            row.update(cost=None, error=str(ex))
            log(f"Evaluation {e.n} failed: {ex}", "warning")
        self.history.append(row)
        previous = self.best
        if np.isfinite(cost) and (self.best is None or cost < self.best["cost"]):
            self.best = row
            (self.out / "best.json").write_text(json.dumps(row, indent=1), encoding="utf-8")
        if not self.keep_mesh:
            # mesh and fields of the best evaluation only (its solution is shown after Apply to Design)
            if self.best is row:
                if previous is not None:
                    self.discard(previous["eval"])
            else:
                self.cleanup(e.folder)
        self._write_history(force=not self.circuit)
        emit("optim", eval=e.n, cost=row.get("cost"), best=self.best["cost"] if self.best else None)
        msg = f"Evaluation {e.n} ({phase}): cost {cost:.6g}" if np.isfinite(cost) else None
        if msg and (not self.circuit or self.best is row):
            log(msg + "   " + ", ".join(f"{k}={v}" for k, v in e.texts.items()))
        return cost

    def _write_history(self, force: bool = True) -> None:
        """history.json; a circuit evaluation takes milliseconds, so then at most twice a second (and at the end)."""
        now = time.time()
        if not force and now - self._written < 0.5:
            return
        self._written = now
        (self.out / "history.json").write_text(json.dumps({"variables": self.vars.names, "evaluations": self.history},
                                                          indent=1, default=str), encoding="utf-8")

    def discard(self, n: int) -> None:
        """Remove mesh and fields of evaluation n unless it is the best one."""
        if n in self.folders and not (self.best and self.best.get("eval") == n):
            self.cleanup(self.folders[n])

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
        if self.mapping:
            return self.run_space_mapping()
        if self.algorithm == NELDER_MEAD:
            alg = SampledNelderMead(self.vars.x0(), self.props)
            log(f"Optimization '{self.opt.name}': Latin hypercube sample of {alg.p['samples']} design(s) and the "
                f"start design, then up to {alg.p['chains']} Nelder-Mead chain(s) of up to {alg.p['budget']} "
                f"evaluations from the best ones, over {len(self.vars)} variable(s) ({', '.join(self.vars.names)}), "
                f"goals on {self.opt.solution}, up to {self.parallel} design(s) at once")
        else:
            alg = ConjugateGradient(self.vars.x0(), self.props)
            log(f"Optimization '{self.opt.name}': conjugate gradient over {len(self.vars)} variable(s) "
                f"({', '.join(self.vars.names)}), goals on {self.opt.solution}, up to {self.parallel} design(s) "
                "at once")
        (self.out / "ABORT").unlink(missing_ok=True)
        while not alg.done and self.n < self.max_evals:
            phase = alg.phase
            pts = alg.propose()[: self.max_evals - self.n]
            if not pts:
                break
            todo = []
            for p in pts:
                key = tuple(np.round(p, 9))
                if key in self.cache:
                    alg.observe(p, self.cache[key])
                else:
                    todo.append(p)
            if not todo:
                continue
            costs = self._solve_batch(todo, phase, alg.iteration)
            if costs is None:
                break
            for p, c in zip(todo, costs):
                self.cache[tuple(np.round(p, 9))] = c
                alg.observe(p, c)
        return self._end(alg.reason or "Maximum number of evaluations reached")

    def _end(self, reason: str) -> bool:
        stopped = self.aborted()
        self._write_history()
        for n in list(self.folders):
            self.discard(n)
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
            if not taken:
                radius = TR.shrink_below(radius, dp, model.m.ranges)
            log(f"Round {it + 1}: solved cost {new:.6g}, predicted {pred_cost:.6g} (actual / predicted "
                f"improvement {rho:.2f}): step {'taken' if taken else 'rejected'}, region now {radius:.0%}")
            if taken:
                self.discard(cur)
                x, cost, cur, model = xn, new, self.n, None
            else:
                self.discard(self.n)
            if radius < TR.MIN_RADIUS:
                reason = "The trust region became too small (the model no longer predicts the solves)"
                break
        else:
            reason = f"Maximum number of rounds ({rounds}) reached"
        if cost == 0:
            reason = "All goals are met (cost 0)"
        return self._end(reason)

    # ---- space mapping ------------------------------------------------------------------------------
    def run_space_mapping(self) -> bool:
        from scipy.optimize import least_squares
        p = self.props
        coarse = CoarseModel(self, self.coarse_name)
        rounds = max(1, int(float(p.get("MappingRounds", 6))))
        tol = max(1e-6, float(p.get("MappingTolerance", 0.01)))
        (self.out / "ABORT").unlink(missing_ok=True)
        names = ", ".join(self.vars.names)
        log(f"Optimization '{self.opt.name}': space mapping over {len(self.vars)} variable(s) ({names}) with the "
            f"circuit {coarse.name} as the coarse model, goals on {self.opt.solution}")
        # 1. the goals on the circuit
        emit("progress", value=0, text=f"Optimizing the circuit {coarse.name} on the goals")
        t = time.time()
        u, c_cost, nev = TR.minimize_box(coarse.cost, np.zeros(len(self.vars)), np.ones(len(self.vars)), starts=2)
        target = np.clip(u, 0, 1)
        log(f"Circuit optimum: cost {c_cost:.6g} after {nev} circuit evaluations in {time.time() - t:.1f} s: "
            + ", ".join(f"{k}={v}" for k, v in self.vars.texts(target).items()))
        if self.aborted():
            return self._end("Aborted")
        # 2. rounds of 3D solve, extraction and quasi-Newton step
        x = self.vars.x0() if p.get("StartFromDesign") == "True" else target.copy()
        B = np.eye(len(x))
        xc_prev, h = x.copy(), None
        best_x, best_cost, best_f, halvings = None, float("inf"), None, 0
        reason = f"Maximum number of rounds ({rounds}) reached"
        for it in range(rounds):
            costs = self._solve_batch([x], f"round {it + 1}", it)
            if costs is None:
                return self._end("Aborted")
            cost = costs[0]
            if not np.isfinite(cost):
                reason = f"The 3D design of round {it + 1} could not be solved"
                break
            if cost == 0:
                reason = "All goals are met (cost 0)"
                break
            if best_x is not None and cost > best_cost and h is not None:
                # the step made the 3D design worse: back to the best one with half the step (the mapping is
                # kept; it is only trusted less far)
                halvings += 1
                if halvings > 3:
                    reason = f"No 3D improvement after {halvings - 1} shorter steps"
                    break
                h = 0.5 * (x - best_x)
                x = np.clip(best_x + h, 0, 1)
                log(f"Round {it + 1}: 3D cost {cost:.6g} is above the best {best_cost:.6g}: half the step")
                continue
            fine = ResultStore(self.folders[self.n] / "results").sparams(self.sol)
            freq, Sf = np.asarray(fine["freq"], float), np.asarray(fine["S"], complex)
            pairs = mapping_pairs(Sf.shape[1])
            reg = 1e-2             # a little pull to the previous extraction: unique where the response is flat

            def residual(v):
                Sc = coarse.response(v, freq)
                if Sc is None or Sc.shape[1] != Sf.shape[1]:
                    return np.full(2 * len(pairs) * len(freq) + len(v), 10.0)
                d = np.concatenate([Sc[:, i, j] - Sf[:, i, j] for i, j in pairs])
                return np.concatenate([d.real, d.imag, reg * (np.asarray(v) - xc_prev)])
            t = time.time()
            r = least_squares(residual, xc_prev, bounds=(-1.0, 2.0), x_scale=1.0, diff_step=1e-3, max_nfev=200)
            xc = r.x
            f = xc - target
            rms = np.sqrt(np.mean(r.fun[:-len(xc)] ** 2))
            log(f"Round {it + 1}: 3D cost {cost:.6g}; circuit fitted to the 3D response (rms error "
                f"{rms:.3g}, {r.nfev} circuit evaluations, {time.time() - t:.1f} s): "
                + ", ".join(f"{n}={v:.5g}" for n, v in zip(self.vars.names, self.vars.lo + xc * (self.vars.hi - self.vars.lo)))
                + f"; distance to the circuit optimum {np.max(np.abs(f)):.3g} of the ranges")
            self.history[-1].update(extracted=xc.tolist())
            if np.max(np.abs(f)) < tol:
                reason = "The 3D design maps onto the circuit optimum"
                break
            dh = x - best_x if best_x is not None else None
            if best_f is not None and float(dh @ dh) > 0:
                B = B + np.outer(f - best_f - B @ dh, dh) / float(dh @ dh)     # Broyden update of the mapping
            best_x, best_cost, best_f, halvings = x.copy(), cost, f, 0
            try:
                h = np.linalg.solve(B, -f)
            except np.linalg.LinAlgError:
                h = -f
            xn = np.clip(x + h, 0, 1)
            h = xn - x
            if np.max(np.abs(h)) < 1e-6:
                reason = "The step is blocked by the variable ranges"
                break
            x, xc_prev = xn, xc
            if self.aborted():
                return self._end("Aborted")
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
        label = {"base": "start design", "gradient": "gradient", "line": "line search", "sampling": "sample",
                 "simplex": "Nelder-Mead"}.get(phase, phase)
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
            if not self.circuit:
                time.sleep(0.2)
        return costs


class CoarseModel:
    """A circuit design of the project with the optimization's variables: its S-parameters and goal cost at
    variable values (unit box of the optimization), analyzed in this process."""

    def __init__(self, opt: "Optimizer", name: str):
        from .sweeps import sweep_frequencies
        if not name:
            raise ValueError("Space Mapping: enter the circuit design of the coarse model (Optimizer tab)")
        try:
            d = opt.proj.design(name)
        except KeyError:
            raise ValueError(f"Space Mapping: no design named '{name}'") from None
        if getattr(d, "kind", "3d") != "circuit":
            raise ValueError(f"Space Mapping: '{name}' is not a circuit design")
        missing = [v for v in opt.vars.names if v not in d.variables and v not in opt.proj.variables]
        if missing:
            raise ValueError(f"Space Mapping: the circuit {name} has no variable {', '.join(missing)}")
        self.o, self.name, self.design = opt, name, d
        self.proj = opt.proj.clone()
        self.d = self.proj.design(name)
        self.base = dict(self.d.variables)
        st = opt.des.setup(opt.setup)
        sw = next(sw for sw in st.sweeps if sw.name == opt.sol)
        self.freqs = sweep_frequencies(sw.props, opt.ev)
        self.evals = 0

    def response(self, x, freqs=None):
        """S (F, P, P) at the unit-box values x (may lie outside 0..1), None where the circuit fails."""
        from .circuit import analyze
        from .optimetrics import fmt_value
        v = self.o.vars.lo + np.asarray(x, float) * (self.o.vars.hi - self.o.vars.lo)
        self.d.variables = dict(self.base)
        self.d.variables.update({n: fmt_value(val, u) for n, val, u in zip(self.o.vars.names, v, self.o.vars.units)})
        self.evals += 1
        try:
            r = analyze(self.proj, self.d, self.freqs if freqs is None else freqs)
        except Exception:  # noqa: BLE001
            return None
        self.z0 = r.z0
        return r.S

    def cost(self, x) -> float:
        S = self.response(np.clip(x, 0, 1))
        if S is None:
            return float("inf")
        ports = [{"name": f"Port{i + 1}", "number": i + 1, "kind": "Circuit Port", "modes": 1}
                 for i in range(S.shape[1])]
        data = {"freq": self.freqs, "S": S, "z0": self.z0, "solved": self.freqs, "ports": ports}
        try:
            c, _ = evaluate_goals(self.o.opt, data, self.o.ev, self.o.outvars)
        except Exception:  # noqa: BLE001
            return float("inf")
        if self.evals % 25 == 0:
            emit("progress", value=0, text=f"Optimizing the circuit {self.name}: {self.evals} evaluations, "
                                            f"cost {c:.4g}")
        return c


def mapping_pairs(ports: int) -> list[tuple[int, int]]:
    """The S-parameters the circuit is fitted to in space mapping: all of a reciprocal network (Sij with
    i >= j), for any number of ports."""
    return [(i, j) for j in range(ports) for i in range(j, ports)]


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
