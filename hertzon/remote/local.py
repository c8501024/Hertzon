"""This computer as a remote computer for others: start, stop and query the remote service in the
background (Tools > Options > Remote), without a terminal."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import DEFAULT_PORT
from .client import RemoteError, RemoteHost
from .server import ROOT, addresses, default_workdir, load_token


def token() -> str:
    return load_token(default_workdir())


def pairing_code() -> str:
    """The pairing code of the running service (it changes with each start of the service)."""
    try:
        return (default_workdir() / "pairing").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def local_host(timeout: float = 1.5) -> RemoteHost:
    return RemoteHost(f"127.0.0.1:{DEFAULT_PORT}", token(), timeout=timeout)


def hosted_jobs() -> list[dict] | None:
    """Analyses other computers sent to the service on this computer (queued or running); None when no
    service runs here. Never creates a token: without one no service has run on this computer yet."""
    if not (default_workdir() / "token").is_file():
        return None
    try:
        jobs = RemoteHost(f"127.0.0.1:{DEFAULT_PORT}", token(), timeout=2).jobs()
    except RemoteError:
        return None
    return [j for j in jobs if j.get("state") in ("queued", "running")]


def abort_hosted(jid: str) -> None:
    local_host(timeout=5).abort(jid)


def status() -> dict | None:
    """The info of the service running on this computer, None when none answers."""
    try:
        return local_host().info()
    except RemoteError:
        return None


def share_addresses() -> list[str]:
    """What other computers enter as 'Computer' to reach this one."""
    return [f"{n}:{DEFAULT_PORT}" for n in [socket.gethostname()] + addresses()]


def start(wait: float = 10.0) -> dict | None:
    """Start the service as a background process that keeps running when Hertzon is closed; returns its
    info once it answers (None if it did not come up within WAIT seconds; see service.log)."""
    workdir = default_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    token()                                             # created before the service starts
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")              # no console window
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    log = open(workdir / "service.log", "ab")           # noqa: SIM115 (handed to the child process)
    kw = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen([str(exe), "-m", "hertzon.remote", "serve"], **kw)
    finally:
        log.close()
    end = time.monotonic() + wait
    while time.monotonic() < end:
        info = status()
        if info is not None:
            return info
        time.sleep(0.3)
    return None


def stop(wait: float = 10.0) -> bool:
    """Stop the service on this computer (running analyses are aborted); True once it no longer answers."""
    try:
        local_host().shutdown()
    except RemoteError:
        return status() is None
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if status() is None:
            return True
        time.sleep(0.3)
    return False
