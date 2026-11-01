"""One analysis on a remote computer: uploads the job, relays the solver's lines like a local solver process,
keeps the local results folder in step (each adaptive pass, so the field plots of the passes work as
locally) and mirrors the final results back. Runs the network part in background threads.

Fast where it matters:
- progress and log lines are relayed at once; the results of a pass are fetched by a second thread, and only
  the lines whose handlers read those files wait for them;
- at the end the small results (S-parameters, convergence, mesh, last adaptive fields) come first and the
  analysis counts as finished; the large field files of the sweeps follow in the background (``downloaded``).
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import __version__
from ..remote.client import Mirror, RemoteError, RemoteHost, prepare

TAG = "@@HZN "
SYNC_ON = ("pass", "passdata", "convergence", "optim", "param")   # lines whose handlers read the results folder
RETRY_FOR = 600.0             # a lost connection is retried this long (s) before the job counts as failed
LATE_PREFIXES = ("fields_", "solution_", "farfield_")             # large per-sweep data, fetched after the end
LATE_SIZE = 1_000_000


def late(entry: dict) -> bool:
    """A file fetched after the analysis counts as finished: large field data of a sweep."""
    name = entry["path"].rsplit("/", 1)[-1]
    return entry["size"] > LATE_SIZE and name.startswith(LATE_PREFIXES) and "LastAdaptive" not in name


class RemoteRun(QObject):
    line = Signal(str)                    # a solver output line, as from a local process
    note = Signal(str, str)               # level, text for the Message Manager
    status = Signal(float, str)           # percent, text for the progress window
    ended = Signal(int)                   # exit code of the remote solver; the small results are here
    downloaded = Signal()                 # the large field files are here too (or failed); the run is over

    def __init__(self, host: RemoteHost, kind: str, project: str, design: str, name: str, out: Path,
                 env: dict, parent=None):
        super().__init__(parent)
        self.host, self.kind, self.project, self.design, self.name = host, kind, project, design, name
        self.out, self.env = Path(out), env
        self.label = ""                    # the job's key, shown in the progress window of the solving computer
        self.jid = ""
        self._abort = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hertzon-remote-job", daemon=True)
        # pass results: lines waiting for the files their handlers read, fetched by the sync thread
        self._pending: list[str] = []
        self._sync_cond = threading.Condition()
        self._sync_stop = False

    def start(self) -> None:
        self._thread.start()

    def abort(self) -> None:
        self._abort.set()
        if self.jid:
            threading.Thread(target=self._send_abort, daemon=True).start()

    def _send_abort(self) -> None:
        try:
            self.host.abort(self.jid)
        except RemoteError as ex:
            self.note.emit("warning", f"Abort on {self.host.address} not confirmed: {ex}")

    # ---- background thread -------------------------------------------------------------------------
    def _run(self) -> None:
        from .solve import NO_RESULT
        code = NO_RESULT
        mirror = None
        try:
            code, mirror = self._job()
        except RemoteError as ex:
            self.note.emit("error", str(ex))
            self._give_up()
        except Exception as ex:  # noqa: BLE001
            self.note.emit("error", f"Remote analysis on {self.host.address} failed: {ex}")
            self._give_up()
        self.ended.emit(code)
        if mirror is not None:
            self._fetch_rest(mirror)
        self.downloaded.emit()

    def _give_up(self) -> None:
        """This GUI no longer follows the job: stop it on the service instead of letting it run unseen."""
        if self.jid:
            try:
                self.host.abort(self.jid)
            except RemoteError:
                pass

    def _job(self) -> tuple[int, Mirror]:
        host = self.host
        if hasattr(host, "ensure"):          # a cloud computer: created (or found again) on the first analysis
            host.ensure(lambda text: self.status.emit(0, text), self._abort)
        self.status.emit(0, f"Connecting to {host.address}")
        info = host.info()
        if info.get("hertzon") != __version__:
            self.note.emit("warning", f"{host.address} runs Hertzon {info.get('hertzon')}, this computer "
                                      f"{__version__}; results can differ. Install the same version on both.")
        self.status.emit(0, f"Uploading the project to {host.address}")
        archive, rel = prepare(Path(self.project), self.out)
        try:
            meta = {"kind": self.kind, "project": Path(self.project).name, "design": self.design,
                    "name": self.name, "out": rel, "env": self.env, "label": self.label,
                    "client": socket.gethostname()}
            self.jid = host.submit(archive, meta)["id"]
        finally:
            archive.unlink(missing_ok=True)
        if self._abort.is_set():
            self._send_abort()
        self.note.emit("info", f"Solving on {info.get('host', host.address)} with {info.get('cores')} core(s)")
        mirror = Mirror(host, self.jid, self.out)
        syncer = threading.Thread(target=self._sync_passes, args=(mirror,), name="hertzon-remote-sync",
                                  daemon=True)
        syncer.start()
        since, state, code, waiting = 0, "queued", -1, None
        lost = None
        try:
            while True:
                try:
                    ev = host.events(self.jid, since)
                    lost = None
                except RemoteError as ex:
                    lost = lost or time.monotonic()
                    if time.monotonic() - lost > RETRY_FOR:
                        raise RemoteError(f"Connection to {host.address} lost: {ex}") from None
                    time.sleep(5)
                    continue
                for text in ev.get("lines", []):
                    self._relay(text)
                since = ev.get("next", since)
                state, code = ev.get("state", state), ev.get("code")
                if state == "queued" and ev.get("ahead") != waiting:
                    waiting = ev.get("ahead")
                    self.status.emit(0, f"Waiting on {host.address}: {waiting} job(s) ahead")
                if state not in ("queued", "running"):
                    break
        finally:
            with self._sync_cond:
                self._sync_stop = True
                self._sync_cond.notify_all()
            syncer.join()
        self.status.emit(99, "Downloading results")
        self._retry(lambda: mirror.sync(skip=late, progress=lambda d, t: self.status.emit(
            99, f"Downloading results {d / 1e6:.1f} of {t / 1e6:.1f} MB")))
        if code is None:
            from .solve import NO_RESULT
            return NO_RESULT, mirror
        return int(code), mirror

    def _fetch_rest(self, mirror: Mirror) -> None:
        """The large field files of the sweeps, after the analysis counts as finished."""
        try:
            rest = [e for e in self.host.files(self.jid) if late(e)]
            total = sum(e["size"] for e in rest if not e.get("uploaded"))
            if total:
                self.note.emit("info", f"Field data of the sweeps ({total / 1e6:.0f} MB) is downloading in the "
                                       "background")
            self._retry(lambda: mirror.sync(final=True))
            if total:
                self.note.emit("info", "Field data of the sweeps downloaded")
        except RemoteError as ex:
            self.note.emit("warning", f"Field data not downloaded from {self.host.address}: {ex}")
            return
        try:
            self.host.delete(self.jid)
        except RemoteError:
            pass                          # the service removes old job folders itself after a few days

    @staticmethod
    def _retry(fn, attempts: int = 3) -> None:
        for attempt in range(attempts):
            try:
                fn()
                return
            except RemoteError:
                if attempt == attempts - 1:
                    raise
                time.sleep(2)

    # ---- lines and the results of the passes ---------------------------------------------------------
    def _relay(self, text: str) -> None:
        """Lines whose handlers read the results folder wait (in order) for the sync thread; progress and log
        lines go on at once, so the progress window never waits for a download."""
        with self._sync_cond:
            if text.startswith(TAG) and self._needs_files(text):
                self._pending.append(text)
                self._sync_cond.notify_all()
                return
        self.line.emit(text)

    def _sync_passes(self, mirror: Mirror) -> None:
        while True:
            with self._sync_cond:
                while not self._pending and not self._sync_stop:
                    self._sync_cond.wait()
                if not self._pending and self._sync_stop:
                    return
            try:
                mirror.sync(skip=late)
            except RemoteError as ex:     # the pass plots miss these files; the final sync brings them
                self.note.emit("debug", f"Results of the pass not fetched yet: {ex}")
            with self._sync_cond:
                lines, self._pending = self._pending, []
            for text in lines:
                self.line.emit(text)

    @staticmethod
    def _needs_files(text: str) -> bool:
        try:
            return json.loads(text[len(TAG):]).get("type") in SYNC_ON
        except (json.JSONDecodeError, AttributeError):
            return False
