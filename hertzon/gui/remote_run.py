"""One analysis on a remote computer: uploads the job, relays the solver's lines like a local solver process,
keeps the local results folder in step (each adaptive pass, so the field plots of the passes work as
locally) and mirrors the final results back. Runs the network part in a background thread."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import __version__
from ..remote.client import Mirror, RemoteError, RemoteHost, prepare

TAG = "@@HZN "
SYNC_ON = ("pass", "passdata", "convergence", "optim", "param")   # lines whose handlers read the results folder
RETRY_FOR = 600.0             # a lost connection is retried this long (s) before the job counts as failed


class RemoteRun(QObject):
    line = Signal(str)                    # a solver output line, as from a local process
    note = Signal(str, str)               # level, text for the Message Manager
    status = Signal(float, str)           # percent, text for the progress window
    ended = Signal(int)                   # exit code of the remote solver process (-1: no result)

    def __init__(self, host: RemoteHost, kind: str, project: str, design: str, name: str, out: Path,
                 env: dict, parent=None):
        super().__init__(parent)
        self.host, self.kind, self.project, self.design, self.name = host, kind, project, design, name
        self.out, self.env = Path(out), env
        self.jid = ""
        self._abort = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hertzon-remote-job", daemon=True)

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
        try:
            code = self._job()
        except RemoteError as ex:
            self.note.emit("error", str(ex))
            self._give_up()
        except Exception as ex:  # noqa: BLE001
            self.note.emit("error", f"Remote analysis on {self.host.address} failed: {ex}")
            self._give_up()
        self.ended.emit(code)

    def _give_up(self) -> None:
        """This GUI no longer follows the job: stop it on the service instead of letting it run unseen."""
        if self.jid:
            try:
                self.host.abort(self.jid)
            except RemoteError:
                pass

    def _job(self) -> int:
        host = self.host
        self.status.emit(0, f"Connecting to {host.address}")
        info = host.info()
        if info.get("hertzon") != __version__:
            self.note.emit("warning", f"{host.address} runs Hertzon {info.get('hertzon')}, this computer "
                                      f"{__version__}; results can differ. Install the same version on both.")
        self.status.emit(0, f"Uploading the project to {host.address}")
        archive, rel = prepare(Path(self.project), self.out)
        try:
            meta = {"kind": self.kind, "project": Path(self.project).name, "design": self.design,
                    "name": self.name, "out": rel, "env": self.env}
            self.jid = host.submit(archive, meta)["id"]
        finally:
            archive.unlink(missing_ok=True)
        if self._abort.is_set():
            self._send_abort()
        self.note.emit("info", f"Solving on {info.get('host', host.address)} with {info.get('cores')} core(s)")
        mirror = Mirror(host, self.jid, self.out)
        since, state, code, waiting = 0, "queued", -1, None
        lost = None
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
                if text.startswith(TAG) and self._needs_files(text):
                    try:
                        mirror.sync()
                    except RemoteError as ex:     # the pass plots miss these files; the final sync brings them
                        self.note.emit("debug", f"Results of the pass not fetched yet: {ex}")
                self.line.emit(text)
            since = ev.get("next", since)
            state, code = ev.get("state", state), ev.get("code")
            if state == "queued" and ev.get("ahead") != waiting:
                waiting = ev.get("ahead")
                self.status.emit(0, f"Waiting on {host.address}: {waiting} job(s) ahead")
            if state not in ("queued", "running"):
                break
        self.status.emit(99, "Downloading results")
        for attempt in range(3):
            try:
                mirror.sync(final=True, progress=lambda d, t: self.status.emit(
                    99, f"Downloading results {d / 1e6:.1f} of {t / 1e6:.1f} MB"))
                break
            except RemoteError:
                if attempt == 2:
                    raise
                time.sleep(2)
        try:
            host.delete(self.jid)
        except RemoteError:
            pass                          # the service removes old job folders itself after a few days
        if code is None:
            from .solve import NO_RESULT
            return NO_RESULT
        return int(code)

    @staticmethod
    def _needs_files(text: str) -> bool:
        try:
            return json.loads(text[len(TAG):]).get("type") in SYNC_ON
        except (json.JSONDecodeError, AttributeError):
            return False
