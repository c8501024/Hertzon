"""Who uses the cloud server of this computer, and what happened to it.

Every Hertzon window that uses the cloud server holds a lease: a small file in <work folder>/cloud/leases-<provider>/
with its process, its window and the number of its analyses on the server. Windows of other Hertzon
processes on the same computer share the server (the state file <provider>.json), so a window that closes
deletes the server only when no other window holds a lease (a lease of a process that no longer runs does
not count).

events.log in the same folder records what happened to the servers (created, deleted and by whom or why,
lost while analyses ran), one JSON object per line; secrets never go there.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LOG_KEEP = 2000                   # events.log keeps the last lines


def _process_started(pid: int) -> float:
    """Start time of process PID (0: unknown); tells a running process from a later one with the same id."""
    try:
        import psutil
        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001
        return 0.0


def process_alive(pid: int, started: float = 0.0) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
        try:
            p = psutil.Process(pid)
            return p.is_running() and (not started or abs(p.create_time() - started) < 2.0)
        except psutil.NoSuchProcess:
            return False
        except Exception:  # noqa: BLE001
            pass
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Leases:
    """The leases on the cloud server in FOLDER (<work folder>/cloud)."""

    def __init__(self, folder: Path, provider: str = ""):
        self.dir = Path(folder) / (f"leases-{provider}" if provider else "leases")

    def _file(self, owner: str) -> Path:
        return self.dir / f"{''.join(c if c.isalnum() or c in '-_' else '_' for c in owner)}.json"

    def hold(self, owner: str, jobs: int = 0, label: str = "") -> None:
        """OWNER (a window of this process) uses the server, with JOBS analyses on it now."""
        self.dir.mkdir(parents=True, exist_ok=True)
        pid = os.getpid()
        data = {"owner": owner, "pid": pid, "started": _process_started(pid), "jobs": int(jobs),
                "label": label, "time": time.time()}
        f = self._file(owner)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, f)

    def drop(self, owner: str) -> None:
        self._file(owner).unlink(missing_ok=True)

    def all(self) -> list[dict]:
        """The leases of running processes; files of ended processes are removed."""
        out = []
        if not self.dir.is_dir():
            return out
        for f in self.dir.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if process_alive(int(d.get("pid", 0)), float(d.get("started", 0) or 0)):
                out.append(d)
            else:
                f.unlink(missing_ok=True)
        return out

    def others(self, owner: str) -> list[dict]:
        return [d for d in self.all() if d.get("owner") != owner]


class EventLog:
    """<work folder>/cloud/events.log: what happened to the cloud servers of this computer."""

    def __init__(self, folder: Path):
        self.path = Path(folder) / "events.log"

    def add(self, event: str, **data) -> dict:
        rec = {"time": time.time(), "when": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event,
               "pid": os.getpid(), **data}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            if self.path.stat().st_size > 400_000:
                lines = self.path.read_text(encoding="utf-8").splitlines()[-LOG_KEEP:]
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass
        return rec

    def read(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def last(self, server_id, events: tuple[str, ...] = ("deleted", "deleting")) -> dict | None:
        """The last record of EVENTS for the server SERVER_ID."""
        for rec in reversed(self.read()):
            if str(rec.get("id")) == str(server_id) and rec.get("event") in events:
                return rec
        return None
