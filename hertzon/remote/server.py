"""The remote service: runs the analyses other Hertzon GUIs send to this computer.

    python -m hertzon.remote serve [--port 47820] [--cores N] [--workdir DIR]

Every request carries the service's token (``Authorization: Bearer <token>``); the token is created on the
first start, stored in the work folder and printed at start. Jobs run one after the other in the order they
arrive, each in its own folder below ``<workdir>/jobs``, with the same solver processes the GUI starts
locally. The connection is plain HTTP and meant for a local network; across the internet use an SSH tunnel.

API (JSON unless noted):
    GET    /api/info                         version, cores, queue
    POST   /api/jobs                         body: job archive (zip), header X-Hertzon-Job: job JSON -> {id}
    GET    /api/jobs/<id>/events?since=N     solver lines from N on (waits up to 15 s for new ones), state
    POST   /api/jobs/<id>/abort
    POST   /api/shutdown                     stop the service (running jobs are aborted)
    GET    /api/jobs/<id>/files              files of the job's results folder
    GET    /api/jobs/<id>/file?path=P        one file of the results folder (bytes)
    DELETE /api/jobs/<id>                    remove the job and its folder
"""
from __future__ import annotations

import hmac
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import DEFAULT_PORT, PROTOCOL
from .protocol import JOB_ENV, KINDS, file_list, local_target, safe_rel, unpack

ROOT = Path(__file__).resolve().parents[2]
TAG = "@@HZN "                   # solver lines for the GUI (hertzon.solver.bridge.emit)
WAIT = 15.0                      # longest wait of an events request for new lines (s)
KEEP_DAYS = 7                    # job folders the GUI did not delete are removed after this many days
MAX_LINES = 200_000              # solver lines kept per job (older ones are dropped, the count goes on)


def default_workdir() -> Path:
    home = os.environ.get("HERTZON_HOME")
    return Path(home) / "remote" if home else Path.home() / ".hertzon" / "remote"


def load_token(workdir: Path) -> str:
    """The service token; created on the first start (readable only by this user where the OS allows).
    HERTZON_TOKEN sets it instead (a cloud computer gets the token of the GUI that created it)."""
    env = os.environ.get("HERTZON_TOKEN", "").strip()
    if env:
        return env
    f = workdir / "token"
    if f.is_file():
        tok = f.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    workdir.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    f.write_text(tok + "\n", encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return tok


@dataclass
class Job:
    id: str
    kind: str
    project: str                 # project file name inside the job folder
    design: str
    name: str                    # setup, optimization or parametric setup
    out: str                     # results folder relative to the job folder
    env: dict
    folder: Path
    state: str = "queued"        # queued, running, done, failed, aborted
    code: int | None = None
    lines: list = field(default_factory=list)
    dropped: int = 0             # lines removed from the front of LINES
    aborted: bool = False
    proc: subprocess.Popen | None = None
    created: float = field(default_factory=time.time)
    uploaded: dict = field(default_factory=dict)   # results files as uploaded: path -> mtime
    label: str = ""              # the analysis as the sending GUI names it
    client: str = ""             # computer name of the sending GUI
    progress: float = 0.0        # last progress line of the solver
    text: str = ""

    def files(self) -> list[dict]:
        """The results folder; files the solver has not touched since the upload are marked."""
        files = file_list(self.out_dir)
        for e in files:
            e["uploaded"] = self.uploaded.get(e["path"]) == e["mtime"]
        return files

    @property
    def out_dir(self) -> Path:
        return local_target(self.folder, self.out)

    def summary(self) -> dict:
        return {"id": self.id, "state": self.state, "code": self.code, "kind": self.kind,
                "project": self.project, "design": self.design, "name": self.name, "label": self.label,
                "client": self.client, "progress": self.progress, "text": self.text, "created": self.created}


class Service:
    def __init__(self, workdir: Path, token: str, cores: int = 0, sweep_workers: int = 0,
                 command=None):
        self.workdir = Path(workdir)
        self.jobs_dir = self.workdir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.token = token
        self._cores, self._workers = cores, sweep_workers      # 0: Tools > Options of this computer (hpc.json)
        self.command = command or self.solver_command
        self.port = DEFAULT_PORT
        self.jobs: dict[str, Job] = {}
        self.queue: list[Job] = []
        self.cond = threading.Condition()
        self.stopping = False
        # activity, for the watchdog of a cloud computer (idle, GUI gone, solver hung): see cloud/watchdog.py
        self.last_contact = self.last_done = self.last_output = time.time()
        # pairing: another GUI gets the token with this short code (shown in Options > Remote on this computer)
        self.pairing = f"{secrets.randbelow(1_000_000):06d}"
        self._pair_failures: list[float] = []
        try:
            (self.workdir / "pairing").write_text(self.pairing + "\n", encoding="utf-8")
        except OSError:
            pass
        self.cleanup()
        self.worker = threading.Thread(target=self._work, name="hertzon-remote-worker", daemon=True)
        self.worker.start()

    # ---- jobs ------------------------------------------------------------------------------------
    def submit(self, meta: dict, archive: Path) -> Job:
        kind = meta.get("kind", "run")
        if kind not in KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        project = safe_rel(meta["project"])
        if len(project.parts) != 1:
            raise ValueError("the project must lie at the top of the job archive")
        out = safe_rel(meta["out"])
        env = {k: str(v) for k, v in (meta.get("env") or {}).items() if k in JOB_ENV}
        jid = uuid.uuid4().hex[:12]
        folder = self.jobs_dir / jid
        folder.mkdir(parents=True)
        try:
            unpack(archive, folder)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if not (folder / project.name).is_file():
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError("the job archive holds no project file")
        job = Job(jid, kind, project.name, str(meta.get("design", "")), str(meta.get("name", "")), out.as_posix(),
                  env, folder)
        job.label = str(meta.get("label", ""))[:200]
        job.client = str(meta.get("client", ""))[:100]
        job.out_dir.mkdir(parents=True, exist_ok=True)
        (job.out_dir / "ABORT").unlink(missing_ok=True)
        job.uploaded = {e["path"]: e["mtime"] for e in file_list(job.out_dir)}
        with self.cond:
            self.jobs[jid] = job
            self.queue.append(job)
            self.cond.notify_all()
        return job

    def ahead(self, job: Job) -> int:
        with self.cond:
            return self.queue.index(job) + (1 if self.running() else 0) if job in self.queue else 0

    def running(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.state == "running"), None)

    def events(self, job: Job, since: int, wait: float = WAIT) -> dict:
        end = time.monotonic() + max(0.0, wait)
        with self.cond:
            while job.dropped + len(job.lines) <= since and job.state in ("queued", "running") and not self.stopping:
                left = end - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            start = max(0, since - job.dropped)
            lines = job.lines[start:]
            nxt = job.dropped + len(job.lines)
            return {"lines": lines, "next": nxt, "state": job.state, "code": job.code,
                    "aborted": job.aborted, "ahead": self.ahead(job) if job.state == "queued" else 0}

    def abort(self, job: Job) -> None:
        with self.cond:
            job.aborted = True
            if job.state == "queued":
                self.queue.remove(job)
                job.state = "aborted"
                self.cond.notify_all()
                return
            proc = job.proc
        try:
            (job.out_dir / "ABORT").write_text("1", encoding="utf-8")
        except OSError:
            pass
        if proc is None:
            return
        if job.kind == "run":
            proc.kill()
        else:
            # the optimizer stops its own solver processes when it sees ABORT (as in the GUI)
            threading.Timer(15.0, lambda: proc.poll() is None and proc.kill()).start()

    def delete(self, job: Job) -> None:
        if job.state in ("queued", "running"):
            self.abort(job)
            if job.proc is not None:
                try:
                    job.proc.wait(30)
                except subprocess.TimeoutExpired:
                    pass
        with self.cond:
            self.jobs.pop(job.id, None)
        shutil.rmtree(job.folder, ignore_errors=True)

    def cleanup(self, days: float = KEEP_DAYS) -> None:
        """Remove job folders older than DAYS that no running job uses (left by GUIs that went away)."""
        limit = time.time() - days * 86400
        active = {j.folder for j in self.jobs.values() if j.state in ("queued", "running")}
        for d in self.jobs_dir.iterdir():
            try:
                if d.is_dir() and d not in active and d.stat().st_mtime < limit:
                    shutil.rmtree(d, ignore_errors=True)
                    self.jobs.pop(d.name, None)
            except OSError:
                pass

    # ---- solver processes -------------------------------------------------------------------------
    def solver_command(self, job: Job) -> list[str]:
        return [sys.executable, "-m", KINDS[job.kind], str(job.folder / job.project), job.design, job.name,
                str(job.out_dir)]

    def _hpc(self) -> dict:
        """The HPC settings of Tools > Options on this computer (the GUI writes them to hpc.json), so the
        solving computer's own cores and sweep processes are used, whoever sends the analysis."""
        try:
            return json.loads((self.workdir / "hpc.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @property
    def cores(self) -> int:
        n = self._cores or int(self._hpc().get("cores", 0) or 0)
        return n if n > 0 else (os.cpu_count() or 1)

    @property
    def sweep_workers(self) -> int:
        return self._workers or int(self._hpc().get("sweep_workers", 0) or 0) or 2

    def solver_env(self, job: Job) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        cores = self.cores
        env["HERTZON_CORES"] = str(cores)
        env["HERTZON_SWEEP_WORKERS"] = str(self.sweep_workers)
        for k in JOB_ENV:
            env[k] = job.env.get(k, "0" if k == "HERTZON_MESH_ONLY" else "")
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            env[var] = str(cores)
        return env

    def _work(self) -> None:
        while True:
            with self.cond:
                while not self.queue and not self.stopping:
                    self.cond.wait(60)
                    if not self.queue:
                        self.cleanup()
                if self.stopping:
                    return
                job = self.queue.pop(0)
                job.state = "running"
                self.last_output = time.time()
                self.cond.notify_all()
            self._run(job)

    def _add_line(self, job: Job, line: str) -> None:
        if line.startswith(TAG) and '"progress"' in line:
            try:
                msg = json.loads(line[len(TAG):])
                job.progress, job.text = float(msg.get("value", 0)), str(msg.get("text", ""))
            except (ValueError, AttributeError):
                pass
        self.last_output = time.time()
        with self.cond:
            job.lines.append(line)
            if len(job.lines) > MAX_LINES:
                cut = len(job.lines) - MAX_LINES
                del job.lines[:cut]
                job.dropped += cut
            self.cond.notify_all()

    def _run(self, job: Job) -> None:
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT),
                  env=self.solver_env(job))
        if os.name == "nt":
            kw["creationflags"] = 0x08000000                   # CREATE_NO_WINDOW
        code = -1
        try:
            with open(job.folder / "solver.log", "w", encoding="utf-8", errors="replace") as log:
                proc = subprocess.Popen(self.command(job), **kw)
                with self.cond:
                    job.proc = proc
                for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    log.write(line + "\n")
                    self._add_line(job, line)
                code = proc.wait()
        except Exception as ex:  # noqa: BLE001
            self._add_line(job, f"remote service: could not run the solver: {ex}")
        with self.cond:
            job.code = code
            job.proc = None
            job.state = "aborted" if job.aborted else ("done" if code == 0 else "failed")
            self.last_done = time.time()
            self.cond.notify_all()

    def pair(self, code: str) -> str | None:
        """The token for the right pairing code; None otherwise. Five wrong codes in ten minutes lock pairing
        for ten minutes (a six-digit code cannot be guessed that way)."""
        now = time.time()
        with self.cond:
            self._pair_failures = [t for t in self._pair_failures if now - t < 600]
            if len(self._pair_failures) >= 5:
                raise PermissionError("too many wrong pairing codes; try again in ten minutes")
            if hmac.compare_digest(str(code).strip().encode(), self.pairing.encode()):
                return self.token
            self._pair_failures.append(now)
            return None

    def info(self) -> dict:
        from .. import __version__
        with self.cond:
            run = self.running()
            return {"service": "hertzon-remote", "protocol": PROTOCOL, "hertzon": __version__,
                    "host": socket.gethostname(), "platform": platform.platform(), "cores": self.cores,
                    "sweep_workers": self.sweep_workers, "cpus": os.cpu_count() or 1,
                    "python": platform.python_version(), "busy": run is not None, "queued": len(self.queue),
                    **self.activity(run)}

    def activity(self, run: Job | None = None) -> dict:
        """Seconds without work (idle), without a GUI request (contact) and without solver output (quiet)."""
        now = time.time()
        work = run is not None or bool(self.queue)
        return {"idle": 0.0 if work else now - max(self.last_contact, self.last_done),
                "contact": now - self.last_contact,
                "quiet": now - self.last_output if run is not None else 0.0}

    def stop(self) -> None:
        with self.cond:
            self.stopping = True
            active = [j for j in self.jobs.values() if j.state in ("queued", "running")]
            self.cond.notify_all()
        for job in active:
            self.abort(job)


class Handler(BaseHTTPRequestHandler):
    server_version = "HertzonRemote"
    protocol_version = "HTTP/1.1"
    service: Service                      # set on the subclass made by make_server

    def log_message(self, fmt, *args) -> None:   # quiet; errors are answered to the client
        pass

    # ---- helpers -----------------------------------------------------------------------------------
    def _send(self, code: int, body: dict | bytes, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, msg: str) -> None:
        self._send(code, {"error": msg})

    def _authorized(self) -> bool:
        got = self.headers.get("Authorization", "")
        want = "Bearer " + self.service.token
        if hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8")):
            if not self.headers.get("X-Hertzon-Watchdog"):       # the watchdog's own polls are no activity
                self.service.last_contact = time.time()
            return True
        self._drain()
        self._error(401, "wrong or missing token")
        return False

    def _drain(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        while n > 0:
            chunk = self.rfile.read(min(n, 1 << 20))
            if not chunk:
                break
            n -= len(chunk)

    def _job(self, jid: str) -> Job | None:
        job = self.service.jobs.get(jid)
        if job is None:
            self._error(404, f"no job {jid}")
        return job

    def _route(self) -> tuple[list[str], dict]:
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        return parts, {k: v[-1] for k, v in parse_qs(u.query).items()}

    # ---- verbs -------------------------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._authorized():
            return
        parts, q = self._route()
        try:
            if parts == ["api", "info"]:
                return self._send(200, self.service.info())
            if parts == ["api", "jobs"]:
                return self._send(200, {"jobs": [j.summary() for j in self.service.jobs.values()]})
            if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
                job = self._job(parts[2])
                if job is None:
                    return
                rest = parts[3:]
                if rest == []:
                    return self._send(200, job.summary())
                if rest == ["events"]:
                    wait = min(WAIT, float(q.get("wait", WAIT)))
                    return self._send(200, self.service.events(job, int(q.get("since", 0)), wait))
                if rest == ["files"]:
                    return self._send(200, {"files": job.files()})
                if rest == ["file"]:
                    return self._file(local_target(job.out_dir, q.get("path", "")))
            self._error(404, "unknown request")
        except ValueError as ex:
            self._error(400, str(ex))

    def _file(self, path: Path) -> None:
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            return self._error(404, "no such file")
        except OSError:                       # Windows: the solver is writing it right now
            return self._error(409, "file busy")
        with f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            left = size
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_POST(self) -> None:
        parts, _ = self._route()
        if parts == ["api", "pair"]:                 # the only request without the token
            return self._pair()
        if not self._authorized():
            return
        try:
            if parts == ["api", "jobs"]:
                return self._submit()
            if parts == ["api", "shutdown"]:          # Tools > Options > Remote: Stop Service
                self._drain()
                self._send(200, {"stopping": True})
                self.service.stop()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "abort":
                self._drain()
                job = self._job(parts[2])
                if job is not None:
                    self.service.abort(job)
                    self._send(200, job.summary())
                return
            self._drain()
            self._error(404, "unknown request")
        except (ValueError, KeyError) as ex:
            self._error(400, str(ex))

    def _pair(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            code = json.loads(self.rfile.read(min(n, 4096)).decode("utf-8") or "{}").get("code", "")
            tok = self.service.pair(code)
        except PermissionError as ex:
            return self._error(429, str(ex))
        except (ValueError, AttributeError):
            return self._error(400, "bad pairing request")
        if tok is None:
            return self._error(403, "wrong pairing code")
        self._send(200, {"token": tok})

    def _submit(self) -> None:
        meta = json.loads(self.headers.get("X-Hertzon-Job") or "{}")
        n = int(self.headers.get("Content-Length") or 0)
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=self.service.workdir)
        try:
            with os.fdopen(fd, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise ValueError("upload ended early")
                    f.write(chunk)
                    left -= len(chunk)
            job = self.service.submit(meta, Path(tmp))
        finally:
            Path(tmp).unlink(missing_ok=True)
        self._send(200, job.summary())

    def do_DELETE(self) -> None:
        if not self._authorized():
            return
        parts, _ = self._route()
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            job = self._job(parts[2])
            if job is not None:
                self.service.delete(job)
                self._send(200, {"deleted": job.id})
            return
        self._error(404, "unknown request")


def make_server(service: Service, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"service": service})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


DISCOVER = b"HERTZON-DISCOVER"


def start_discovery(service: Service, port: int = DEFAULT_PORT) -> socket.socket | None:
    """Answer the GUIs that look for remote services in the network (UDP broadcast on PORT) with this
    service's name, port and version. Nothing secret is sent; the token needs the pairing code."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
    except OSError:
        return None

    def answer():
        while not service.stopping:
            try:
                data, addr = sock.recvfrom(256)
            except OSError:
                return
            if data.strip() == DISCOVER:
                info = service.info()
                reply = {k: info[k] for k in ("service", "host", "hertzon", "cores", "busy", "queued")}
                reply["port"] = service.port
                try:
                    sock.sendto(json.dumps(reply).encode("utf-8"), addr)
                except OSError:
                    pass
    threading.Thread(target=answer, name="hertzon-remote-discovery", daemon=True).start()
    return sock


def addresses() -> list[str]:
    """This computer's IPv4 addresses in the local network (best effort, for the start message)."""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))         # no packet is sent; picks the interface of the default route
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def serve(host: str = "0.0.0.0", port: int = DEFAULT_PORT, cores: int = 0, sweep_workers: int = 0,
          workdir: Path | None = None) -> int:
    from .. import __version__
    workdir = Path(workdir) if workdir else default_workdir()
    token = load_token(workdir)
    service = Service(workdir, token, cores, sweep_workers)
    srv = make_server(service, host, port)
    service.port = port
    disc = start_discovery(service, port)
    names = [socket.gethostname()] + addresses()
    print(f"Hertzon {__version__} remote service, {service.cores} cores, work folder {workdir}")
    print("Address for Tools > Options > Remote: " + "  or  ".join(f"{n}:{port}" for n in names))
    print(f"Token: {token}    Pairing code: {service.pairing}")
    print("Stop with Ctrl+C.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        srv.server_close()
        if disc is not None:
            disc.close()
    return 0
