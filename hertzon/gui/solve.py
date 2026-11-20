"""Runs analysis setups in a separate solver process and relays progress."""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QProcess, QProcessEnvironment

from ..solver.results import results_dir


def solver_python() -> str:
    """python.exe, not pythonw.exe: Qt starts a console program from this window without a visible
    console (CREATE_NO_WINDOW), and the solver's own worker processes share that hidden console instead
    of each opening a terminal window on Windows."""
    from ..mcp.clients import python_exe
    return python_exe()

TAG = "@@HZN "
NO_RESULT = -999_999          # end code of a remote analysis that produced no solver exit code
from ..solver.results import OOM_CODES, exit_reason  # noqa: E402,F401  (plain words for exit codes)
SERVER_LOST = -999_998        # end code of a cloud analysis whose server disappeared while it ran


BYTES_PER_TET = 140e3         # second-order elements + direct solver (solver.bridge.memory_forecast)


def free_memory() -> float:
    """Memory free for a new solver process on this computer (bytes; 0 if unknown)."""
    try:
        import psutil
        return float(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001
        from ..solver.bridge import available_memory
        return available_memory()


def previous_need(out: Path) -> tuple[float, int]:
    """(bytes, tetrahedra) the last run of a setup needed, from its convergence.json ((0, 0): no run)."""
    try:
        conv = json.loads((Path(out) / "convergence.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0, 0
    tets = max([int(conv.get("final_tets", 0) or 0)] + [int(p.get("tets", 0) or 0) for p in conv.get("passes", [])])
    return tets * BYTES_PER_TET, tets


@dataclass
class Job:
    project_path: str
    design: str
    setup: str
    mesh_only: bool = False
    sweep: str = ""               # analyze only this sweep (reuses the adaptive mesh)
    optimization: str = ""        # Optimization: run this optimization (setup is unused)
    parametric: str = ""          # Optimization: solve the variations of this parametric setup
    tuning: str = ""              # derivative tuning model of a sweep (JSON: sweep, variables, points)
    env: dict = field(default_factory=dict)     # environment of this run (remote.protocol.JOB_ENV keys)
    requeued: int = 0             # times the job was queued again after its cloud server was lost

    @property
    def key(self) -> str:
        if self.optimization or self.parametric:
            return f"{Path(self.project_path).stem} - {self.design} - {self.optimization or self.parametric}"
        if self.tuning:
            return f"{Path(self.project_path).stem} - {self.design} - {self.setup} : tuning {json.loads(self.tuning)['sweep']}"
        return f"{Path(self.project_path).stem} - {self.design} - {self.setup}" + (f" : {self.sweep}" if self.sweep else "")

    @property
    def out(self) -> Path:
        if self.optimization or self.parametric:
            return optimization_dir(self.project_path, self.design, self.optimization or self.parametric)
        return results_dir(self.project_path, self.design, self.setup)


def optimization_dir(project_path: str, design: str, name: str) -> Path:
    """Evaluations and history of an optimization (next to the setups of the design)."""
    from ..solver.results import _safe, studies_dir
    return studies_dir(project_path, design) / _safe(name)


class SolveManager(QObject):
    progress = Signal(str, float, str)       # key, percent, text
    message = Signal(str, str)               # level, text
    passUpdate = Signal(str, dict)
    passData = Signal(str, dict)             # mesh/fields of the current pass were saved
    optimUpdate = Signal(str, dict)          # an optimization evaluation finished
    started = Signal(str)
    filesArrived = Signal(str)               # remote: the large field files came after the analysis finished
    finished = Signal(str, bool)
    queueChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: list[Job] = []
        self.proc: QProcess | None = None
        self.job: Job | None = None
        self._buf = b""
        self.show_emerge_log = False
        self.cores = 0              # 0 = all cores
        self.sweep_workers = 2
        self.remote = None          # RemoteHost: solve on that computer (Tools > Options > Remote)
        self.requeue_lost = False   # queue an analysis again whose cloud server was lost (Tools > Options > Cloud)
        self.cloud_off_hint = ""    # set while a cloud is configured but analyses run here (for the memory warning)
        self._downloading = None    # RemoteRun still fetching field files of a finished remote analysis

    @property
    def busy(self) -> bool:
        return self.proc is not None or self._downloading is not None

    def submit(self, job: Job) -> None:
        if any(j.key == job.key for j in self.queue) or (self.job and self.job.key == job.key):
            self.message.emit("warning", f"{job.key} is already queued")
            return
        self.queue.append(job)
        self.message.emit("info", f"{job.key}: queued for analysis")
        self.queueChanged.emit()
        self._next()

    def remove(self, key: str) -> None:
        n = len(self.queue)
        self.queue = [j for j in self.queue if j.key != key]
        if len(self.queue) != n:
            self.message.emit("info", f"{key}: removed from the queue")
            self.queueChanged.emit()

    def move(self, key: str, delta: int) -> None:
        i = next((k for k, j in enumerate(self.queue) if j.key == key), None)
        if i is None:
            return
        k = max(0, min(len(self.queue) - 1, i + delta))
        self.queue.insert(k, self.queue.pop(i))
        self.queueChanged.emit()

    def _next(self) -> None:
        if self.busy or not self.queue:
            return
        job = self.queue.pop(0)
        self.job = job
        self.queueChanged.emit()
        out = job.out
        keep = None
        if self.remote is None and not job.tuning:
            self._memory_warning(job, out)
        if job.tuning:
            pass                    # reads the adaptive mesh and writes only its tuning model
        elif job.sweep and out.exists():
            # single sweep: keep the adaptive solution and the other sweeps
            for f in list(out.glob(f"sparams_{job.sweep}.npz")) + list(out.glob(f"fields_{job.sweep}.npz")) + \
                    list(out.glob(f"solution_{job.sweep}.npz")):
                f.unlink(missing_ok=True)
        elif job.parametric:
            pass                    # solved variations are reused while their keys match
        elif out.exists():
            # the adaptive mesh state lets the solver continue the passes (more passes, tighter Delta S)
            try:
                keep = (out / "amr_state.npz").read_bytes()
            except OSError:
                keep = None
            # Windows: files of a just killed solver can stay locked for a moment; retry, because a
            # stale ABORT or convergence.json would stop or hide the passes of the new run
            for _ in range(20):
                shutil.rmtree(out, ignore_errors=True)
                if not out.exists():
                    break
                time.sleep(0.1)
        out.mkdir(parents=True, exist_ok=True)
        try:
            (out / "ABORT").unlink(missing_ok=True)
        except OSError:
            pass
        if keep:
            (out / "amr_state.npz").write_bytes(keep)
        self._buf = b""
        self._failed = False
        self._completed = False
        self._aborted = False
        if self.remote is not None:
            self._start_remote(job, out)
            return
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        root = str(Path(__file__).resolve().parents[2])
        env.insert("PYTHONPATH", root + os.pathsep + env.value("PYTHONPATH", ""))
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        n = self.cores if self.cores > 0 else (os.cpu_count() or 1)
        env.insert("HERTZON_CORES", str(n))
        env.insert("HERTZON_SWEEP_WORKERS", str(self.sweep_workers))
        env.insert("HERTZON_MESH_ONLY", "1" if job.mesh_only else "0")
        env.insert("HERTZON_ONLY_SWEEP", job.sweep)
        env.insert("HERTZON_TUNING", job.tuning)
        for k, v in job.env.items():
            env.insert(k, str(v))
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            env.insert(var, str(n))
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.SeparateChannels)
        p.readyReadStandardOutput.connect(self._read)
        p.readyReadStandardError.connect(self._read_err)
        p.finished.connect(self._done)
        self.proc = p
        self.started.emit(job.key)
        self.progress.emit(job.key, 0, "Starting solver")
        self.message.emit("info", f"{job.key}: solving with {n} core(s)"
                          + (" on this computer (cloud solving is off)" if self.cloud_off_hint else ""))
        if job.parametric:
            p.start(solver_python(), ["-m", "hertzon.solver.parametric", job.project_path, job.design, job.parametric,
                                     str(out)])
        elif job.optimization:
            p.start(solver_python(), ["-m", "hertzon.solver.optimize", job.project_path, job.design, job.optimization,
                                     str(out)])
        else:
            p.start(solver_python(), ["-m", "hertzon.solver.run", job.project_path, job.design, job.setup, str(out)])

    def _memory_warning(self, job: Job, out: Path) -> None:
        """A local analysis that will likely not fit: the last run of the setup needed more memory than is free."""
        need, tets = previous_need(out)
        free = free_memory()
        if need <= 0 or free <= 0 or need < 0.8 * free:
            return
        self.message.emit("warning", f"{job.key}: runs on this computer, but its last run reached {tets:,} "
                                     f"tetrahedra (about {need / 2 ** 30:.0f} GB) and only {free / 2 ** 30:.1f} GB "
                                     "are free; it will likely run out of memory. "
                                     + (self.cloud_off_hint or "Solve on a computer with more memory (status bar: "
                                                               "Solve on)."))

    def _start_remote(self, job: Job, out: Path) -> None:
        """Solve JOB on the remote computer; its lines and end go through the same handlers as locally."""
        from .remote_run import RemoteRun
        if job.parametric:
            kind, name = "parametric", job.parametric
        elif job.optimization:
            kind, name = "optimize", job.optimization
        else:
            kind, name = "run", job.setup
        env = {"HERTZON_MESH_ONLY": "1" if job.mesh_only else "0", "HERTZON_ONLY_SWEEP": job.sweep,
               "HERTZON_TUNING": job.tuning, **job.env}
        r = RemoteRun(self.remote, kind, job.project_path, job.design, name, out, env, self)
        r.label = job.key
        r.line.connect(self._handle_line)
        r.note.connect(lambda level, text, key=job.key: self.message.emit(level, f"{key}: {text}"))
        r.status.connect(lambda v, text, key=job.key: self.progress.emit(key, v, text))
        r.ended.connect(lambda code, r=r: self._remote_ended(r, code))
        r.downloaded.connect(lambda key=job.key, r=r: self._remote_downloaded(r, key))
        self.proc = r
        self.started.emit(job.key)
        self.progress.emit(job.key, 0, f"Starting solver on {self.remote.address}")
        self.message.emit("info", f"{job.key}: sending to {self.remote.address}")
        r.start()

    def _remote_ended(self, run, code: int) -> None:
        """The small results are here: the analysis is finished; the queue waits for the field files, so the
        next analysis does not clear a results folder that is still filling."""
        self._downloading = run
        self._done(code, QProcess.NormalExit)

    def _remote_downloaded(self, run, key: str) -> None:
        if self._downloading is run:
            self._downloading = None
        self.filesArrived.emit(key)
        self.queueChanged.emit()
        self._next()

    def abort(self) -> None:
        if self.proc is None or self.job is None:
            return
        self._aborted = True
        if not isinstance(self.proc, QProcess):           # remote: the service stops its solver
            self.message.emit("warning", f"{self.job.key}: aborting")
            self.proc.abort()
            return
        (self.job.out / "ABORT").write_text("1", encoding="utf-8")
        self.message.emit("warning", f"{self.job.key}: aborting")
        if self.job.optimization or self.job.parametric:
            # the optimizer stops its solver processes itself (killing it would leave them running)
            proc = self.proc
            from PySide6.QtCore import QTimer
            QTimer.singleShot(15000, lambda: proc.kill() if self.proc is proc else None)
            return
        self.proc.kill()

    def _read_err(self) -> None:
        if not isinstance(self.proc, QProcess):
            return
        data = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            if self.show_emerge_log and line.strip():
                self.message.emit("debug", line)

    def _read(self) -> None:
        if not isinstance(self.proc, QProcess):
            return
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            self._handle_line(raw.decode("utf-8", errors="replace"))

    def _handle_line(self, line: str) -> None:
        if self.job is None:
            return
        line = line.rstrip("\r")
        if not line.startswith(TAG):
            if self.show_emerge_log and line.strip():
                self.message.emit("debug", line)
            return
        try:
            msg = json.loads(line[len(TAG):])
        except json.JSONDecodeError:
            return
        self._handle(msg)

    def _handle(self, msg: dict) -> None:
        key = self.job.key if self.job else ""
        t = msg.get("type")
        if t == "progress":
            self.progress.emit(key, float(msg.get("value", 0)), msg.get("text", ""))
        elif t == "log":
            self.message.emit(msg.get("level", "info"), f"{key}: {msg.get('msg', '')}")
        elif t == "emerge":
            if self.show_emerge_log:
                self.message.emit("debug", msg.get("msg", ""))
        elif t == "pass":
            self.passUpdate.emit(key, msg)
            ds = msg.get("delta_s")
            txt = f"Adaptive pass {msg['pass']}: {msg.get('tets', 0)} tetrahedra"
            if ds is not None:
                txt += f", {'Delta Energy' if msg.get('energy') else 'Delta S'} = {ds:.4g}"
            self.message.emit("info", f"{key}: {txt}")
        elif t == "passdata":
            self.passData.emit(key, msg)
        elif t == "failed":
            self._failed = True
            self.message.emit("error", f"{key}: analysis failed: {msg.get('error')}")
        elif t == "traceback":
            self.message.emit("debug", msg.get("text", ""))
        elif t in ("optim", "param"):
            self.optimUpdate.emit(key, msg)
        elif t == "done":
            self._completed = True
            self.message.emit("info", f"{key}: normal completion of simulation ({msg.get('elapsed', 0):.1f} s)")

    def _done(self, code, status) -> None:
        self._read()
        key = self.job.key if self.job else ""
        ok = code == 0 and not getattr(self, "_failed", False)
        if not ok and getattr(self, "_completed", False) and not getattr(self, "_failed", False):
            # the results were complete and written; only the process teardown crashed
            self.message.emit("warning", f"{key}: solver process ended with code {code} after the solution "
                                         "was saved; the results are complete.")
            ok = True
        if not ok and self._aborted and not getattr(self, "_failed", False):
            # killed by Abort (on Windows Qt ends the process with code 0xF291 = 62097): not a failure
            self.message.emit("warning", f"{key}: aborted by user")
            if self.job is not None and not self.job.tuning:
                try:
                    (self.job.out / "status.json").write_text(json.dumps({"state": "aborted"}), encoding="utf-8")
                except OSError:
                    pass
        elif not ok and code == SERVER_LOST:
            reason = getattr(self._downloading, "lost", "") or "the server is gone"
            self.message.emit("error", f"{key}: aborted because the cloud server was lost ({reason})")
            if self.job is not None and not self.job.tuning:
                try:
                    (self.job.out / "status.json").write_text(json.dumps(
                        {"state": "failed", "error": "aborted: cloud server lost", "reason": reason}), encoding="utf-8")
                except OSError:
                    pass
            if self.job is not None and self.requeue_lost and self.job.requeued < 1:
                import dataclasses
                again = dataclasses.replace(self.job, requeued=self.job.requeued + 1)
                self.queue.insert(0, again)
                self.message.emit("warning", f"{key}: queued again; the next analysis creates a new cloud server")
        elif not ok and not getattr(self, "_failed", False):
            crashed = (status == QProcess.CrashExit or code < 0 or code > 128) and not self._aborted
            why = exit_reason(code, crashed)
            hint = f" {why}" if why else ""
            self.message.emit("error", f"{key}: the remote analysis did not finish" if code == NO_RESULT else
                              f"{key}: solver process ended with code {code}.{hint}")
            if self.job is not None and not self.job.tuning:
                try:
                    (self.job.out / "status.json").write_text(json.dumps({
                        "state": "failed", "error": f"exit {code}" + (f": {why}" if why else "")}), encoding="utf-8")
                except OSError:
                    pass
        self.progress.emit(key, 100 if ok else 0, "Done" if ok else (
            "Aborted" if self._aborted else ("Cloud server lost" if code == SERVER_LOST else "Failed")))
        self.proc = None
        self.job = None
        self.finished.emit(key, ok)
        self.queueChanged.emit()
        self._next()
