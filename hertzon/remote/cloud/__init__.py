"""Cloud computers: Hertzon creates a server at a cloud provider for the analyses of a session, reaches its
remote service through an SSH tunnel, and the server deletes itself when it is no longer needed.

    session = CloudSession(Hetzner(api_token), server_type="ccx33", location="fsn1")
    host = CloudHost(session)          # a RemoteHost: RemoteRun calls host.ensure() before the first request

The SSH keys come from the ssh-keygen and ssh programs of the operating system (Windows 10 and later, Linux,
macOS have them); no extra Python package is needed. The state of the server (id, address, token, host key)
is kept in <work folder>/cloud/<provider>.json, so a restarted GUI finds its server again. All Hertzon
windows of this computer share that server; each holds a lease while it uses it (lease.py), and
<work folder>/cloud/events.log records what happened to the servers.
"""
from __future__ import annotations

import json
import math
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ..client import RemoteError, RemoteHost
from ..server import default_workdir
from . import cloudinit
from .lease import EventLog, Leases

PORT = cloudinit.PORT
LOST_HINT = ("deleted outside this Hertzon: by its watchdog (idle, solver without progress, no Hertzon "
             "connected, or the service stopped answering, for example after running out of memory), in "
             "the provider's console, or by another computer with the same account")


class ServerLost(RemoteError):
    """The cloud server an analysis ran on no longer exists; REASON says why, as far as known."""

    def __init__(self, name: str, reason: str):
        super().__init__(f"The cloud server {name} no longer exists ({reason})")
        self.name, self.reason = name, reason


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if os.name == "nt" else {}      # CREATE_NO_WINDOW


def tool(name: str) -> str:
    """Path of the OpenSSH program NAME (ssh, ssh-keygen)."""
    exe = shutil.which(name)
    if not exe and os.name == "nt":
        cand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / f"{name}.exe"
        exe = str(cand) if cand.exists() else None
    if not exe:
        raise RemoteError(f"The program '{name}' (OpenSSH) was not found. Windows: Settings > System > Optional "
                          "features > OpenSSH Client; Linux: install openssh-client")
    return exe


def keypair(path: Path) -> tuple[str, str]:
    """An ed25519 key pair at PATH (private) and PATH.pub; created when missing."""
    path = Path(path)
    if not (path.is_file() and path.with_suffix(".pub").is_file()):
        path.parent.mkdir(parents=True, exist_ok=True)
        for p in (path, path.with_suffix(".pub")):
            p.unlink(missing_ok=True)
        subprocess.run([tool("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-C", f"hertzon@{socket.gethostname()}",
                        "-f", str(path)], check=True, capture_output=True, **_no_window())
    return path.read_text(encoding="utf-8"), path.with_suffix(".pub").read_text(encoding="utf-8").strip()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tunnel:
    """ssh -L <local port>:127.0.0.1:47820 <user>@<server>, checked against the known host key."""

    def __init__(self, folder: Path, key: Path, user: str = "root"):
        self.folder, self.key, self.user = Path(folder), Path(key), user
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.ip = ""
        self.ssh_port = 22

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def open(self, ip: str, host_public: str, timeout: float = 20) -> None:
        self.close()
        known = self.folder / "known_hosts"
        name = ip if self.ssh_port == 22 else f"[{ip}]:{self.ssh_port}"
        known.write_text(f"{name} {host_public.strip()}\n", encoding="utf-8")
        self.port, self.ip = free_port(), ip
        cmd = [tool("ssh"), "-N", "-i", str(self.key), "-L", f"127.0.0.1:{self.port}:127.0.0.1:{PORT}",
               "-o", f"UserKnownHostsFile={known}", "-o", "StrictHostKeyChecking=yes", "-p", str(self.ssh_port), "-o", "BatchMode=yes",
               "-o", "IdentitiesOnly=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=4", "-o", "ConnectTimeout=10", f"{self.user}@{ip}"]
        with open(self.folder / "ssh.log", "wb") as log:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **_no_window())
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                time.sleep(0.3)
        err = (self.folder / "ssh.log").read_text(encoding="utf-8", errors="replace").strip()
        self.close()
        raise RemoteError(f"SSH connection to {ip} failed" + (f": {err.splitlines()[-1]}" if err else ""))

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class CloudSession:
    """One server at PROVIDER for the analyses of this computer, created on demand."""

    def __init__(self, provider, server_type: str, location: str, idle_min: float = 30, contact_h: float = 48,
                 quiet_h: float = 2, workdir: Path | None = None, version: str | None = None, slot: str = ""):
        from ... import __version__
        self.provider = provider
        self.server_type, self.location = server_type, location
        self.idle_min, self.contact_h, self.quiet_h = idle_min, contact_h, quiet_h
        self.version = version or __version__
        self.folder = Path(workdir or default_workdir()) / "cloud"
        self.folder.mkdir(parents=True, exist_ok=True)
        # SLOT: a server of its own (a window that runs its analyses in parallel to the others); "" is the one
        # server the windows of this computer share
        self.slot = "".join(ch for ch in slot if ch.isalnum() or ch in "-_")
        self.state_file = self.folder / (f"{provider.key}-{self.slot}.json" if self.slot else f"{provider.key}.json")
        tfolder = self.folder / self.slot if self.slot else self.folder
        tfolder.mkdir(parents=True, exist_ok=True)
        self.tunnel = Tunnel(tfolder, self.folder / "id_ed25519", getattr(provider, "ssh_user", "root"))
        self.lock = threading.RLock()
        self.client = socket.gethostname()
        self.leases = Leases(self.folder, f"{provider.key}-{self.slot}" if self.slot else provider.key)
        self.log = EventLog(self.folder)
        self._price: dict | None = None          # price of the server type (cached, see price())

    @property
    def label(self) -> str:
        return f"{self.provider.name} ({self.server_type})"

    # ---- state --------------------------------------------------------------------------------------
    def state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.state_file)

    def _clear(self) -> None:
        self.tunnel.close()
        self.state_file.unlink(missing_ok=True)

    # ---- life cycle ---------------------------------------------------------------------------------
    def ensure(self, status=None, abort: threading.Event | None = None, create: bool = True,
               note=None) -> RemoteHost:
        """The remote service on this session's server: reuses the running server, else creates one and
        waits until its service answers (about one to three minutes). STATUS(text) reports the steps.

        A server that disappeared is never replaced silently: with CREATE false (an analysis runs on it)
        ServerLost is raised; else NOTE(level, text) is told why it is gone before a new one is created."""
        say = status or (lambda text: None)
        with self.lock:
            st = self.state()
            if st:
                srv = self.provider.get(st["id"])
                if srv is None or srv.get("status") in ("deleting", "off"):
                    reason = self.loss_reason(st, srv)
                    self.log.add("lost", id=st["id"], name=st.get("name"), reason=reason,
                                 status=(srv or {}).get("status", "gone"))
                    if srv is not None:
                        self.provider.delete(st["id"])
                    self._clear()
                    if not create:
                        raise ServerLost(st.get("name", "?"), reason)
                    if note is not None:
                        note("warning", f"The cloud server {st.get('name')} no longer exists ({reason}); "
                                        "creating a new one")
                    st = {}
            if not st:
                if not create:
                    raise ServerLost("?", "it was deleted before this analysis could reach it")
                st = self._create(say)
            if not self.tunnel.alive():
                self._connect(st, say, abort)
            return RemoteHost(f"127.0.0.1:{self.tunnel.port}", st["token"], timeout=30)

    def _create(self, say) -> dict:
        _, client_pub = keypair(self.folder / "id_ed25519")
        with tempfile.TemporaryDirectory() as tmp:
            host_priv, host_pub = keypair(Path(tmp) / "host")
        token = secrets.token_urlsafe(24)
        image, fallback = cloudinit.image_for(self.version)
        ud = cloudinit.user_data(provider=self.provider.key, token=token, api_token=self.provider.token,
                                 host_private=host_priv, host_public=host_pub, client_public=client_pub,
                                 image=image, fallback=fallback, idle_min=self.idle_min,
                                 contact_h=self.contact_h, quiet_h=self.quiet_h,
                                 install_docker=getattr(self.provider, "install_docker", False),
                                 host_key=getattr(self.provider, "own_host_key", True))
        key_id = self.provider.ssh_key(client_pub, self.client)
        name = f"hertzon-{secrets.token_hex(3)}"
        srv, where = self._place(name, ud, key_id, say)
        st = {"id": srv["id"], "name": name, "ip": srv.get("ip", ""), "token": token, "host_key": host_pub,
              "type": self.server_type, "location": where, "created": time.time(), "image": image}
        price = self.price(where)
        if price:
            st["price"] = price
        self._save(st)
        self.log.add("created", id=st["id"], name=name, type=self.server_type, location=where,
                     provider=self.provider.key, price=price)
        return st

    def _place(self, name: str, ud: str, key_id, say) -> tuple[dict, str]:
        """Create the server at the chosen location; when the provider has none of the type free there
        (sold out), at the other locations that offer it."""
        from .hetzner import PlacementError
        tried = [self.location]
        try:
            say(f"Creating a {self.server_type} server at {self.provider.name} ({self.location})")
            return self.provider.create(name, self.server_type, self.location, ud, key_id, self.client), self.location
        except PlacementError:
            pass
        others = getattr(self.provider, "locations_for", lambda t: [])(self.server_type)
        for loc in others:
            if loc in tried:
                continue
            tried.append(loc)
            say(f"No {self.server_type} free in {tried[0]}; trying {loc}")
            try:
                return self.provider.create(name, self.server_type, loc, ud, key_id, self.client), loc
            except PlacementError:
                continue
        raise RemoteError(f"{self.provider.name} has no {self.server_type} server free right now (tried "
                          f"{', '.join(tried)}). Choose another server type in Tools > Options > Cloud or try "
                          "again later.")

    def _connect(self, st: dict, say, abort, timeout: float = 900) -> None:
        """Wait for the server, open the tunnel and wait for the service (the first start pulls the image)."""
        end = time.monotonic() + timeout
        t0 = time.monotonic()

        def check():
            if abort is not None and abort.is_set():
                raise RemoteError("aborted while the cloud server started")
            if time.monotonic() > end:
                raise RemoteError(f"The cloud server {st.get('name')} did not start within {timeout / 60:.0f} "
                                  "minutes; it deletes itself (or use Delete Cloud Servers in Tools > Options)")
        while not st.get("ip") or (self.provider.get(st["id"]) or {}).get("status") != "running":
            check()
            say(f"Starting the cloud server {st.get('name')} ({time.monotonic() - t0:.0f} s)")
            srv = self.provider.get(st["id"])
            if srv is None:
                self._clear()
                raise RemoteError(f"The cloud server {st.get('name')} was deleted")
            if srv.get("ip") and srv["ip"] != st.get("ip"):
                st["ip"] = srv["ip"]
                self._save(st)
            if srv.get("status") == "running":
                break
            time.sleep(3)
        host_key = getattr(self.provider, "host_key", None)
        if host_key is not None and not st.get("host_key_checked"):
            while True:                         # the provider made the host key: read it through its API
                check()
                key = host_key(st["id"])
                if key:
                    st["host_key"], st["host_key_checked"] = key, True
                    self._save(st)
                    break
                say(f"Waiting for the host key of {st.get('name')} ({time.monotonic() - t0:.0f} s)")
                time.sleep(5)
        while True:                             # sshd comes up with the host key cloud-init installs
            check()
            try:
                self.tunnel.open(st["ip"], st["host_key"])
                break
            except RemoteError:
                say(f"Waiting for the cloud server {st.get('name')} ({time.monotonic() - t0:.0f} s)")
                time.sleep(5)
        host = RemoteHost(f"127.0.0.1:{self.tunnel.port}", st["token"], timeout=10)
        while True:
            check()
            try:
                host.info()
                return
            except RemoteError:
                if not self.tunnel.alive():
                    self.tunnel.open(st["ip"], st["host_key"])
                    host = RemoteHost(f"127.0.0.1:{self.tunnel.port}", st["token"], timeout=10)
                say(f"Loading the solver on {st.get('name')} ({time.monotonic() - t0:.0f} s)")
                time.sleep(5)

    def delete(self, reason: str = "") -> bool:
        """Delete this session's server (if any); True if there was one. REASON goes to events.log, so a
        window whose analysis ran there can tell why the server is gone."""
        with self.lock:
            st = self.state()
            self.tunnel.close()
            if not st:
                return False
            self.log.add("deleting", id=st["id"], name=st.get("name"), reason=reason or "deleted by Hertzon",
                         **self.cost(st))
            self.provider.delete(st["id"])
            self._clear()
            self.log.add("deleted", id=st["id"], name=st.get("name"), reason=reason or "deleted by Hertzon")
            return True

    def forget(self, reason: str) -> None:
        """The server was deleted elsewhere (Delete Cloud Servers): drop the state, record why."""
        with self.lock:
            st = self.state()
            if st:
                self.log.add("deleted", id=st["id"], name=st.get("name"), reason=reason, **self.cost(st))
            self._clear()

    # ---- why a server is gone -----------------------------------------------------------------------
    def note_watchdog(self, reason: str) -> None:
        """The server's watchdog announced that it deletes the server (the service reports it)."""
        with self.lock:
            st = self.state()
            if st and st.get("watchdog") != reason:
                st["watchdog"] = reason
                self._save(st)
                self.log.add("deleting", id=st["id"], name=st.get("name"), reason=f"its watchdog: {reason}",
                             by="watchdog")

    def loss_reason(self, st: dict, srv: dict | None = None) -> str:
        """Why the server of state ST is gone (or switched off), as far as this computer knows."""
        rec = self.log.last(st.get("id"))
        if rec is not None:
            who = "the server itself" if rec.get("by") == "watchdog" else \
                ("Hertzon" if rec.get("pid") == os.getpid() else f"another Hertzon (process {rec.get('pid')})")
            return f"deleted by {who} at {rec.get('when', '?')}: {rec.get('reason', '?')}"
        if st.get("watchdog"):
            return f"deleted by its watchdog: {st['watchdog']}"
        if srv is not None and srv.get("status") == "off":
            return "the server was switched off (crashed, or shut down by its watchdog)"
        if srv is not None and srv.get("status") == "deleting":
            return "the provider is deleting it; " + LOST_HINT
        return LOST_HINT

    # ---- costs --------------------------------------------------------------------------------------
    def price(self, location: str = "") -> dict | None:
        """{"hourly", "monthly", "currency"} of this session's server type at LOCATION (from the provider's
        API, cached), or None when the provider tells no price."""
        location = location or self.location
        if self._price is not None and self._price.get("location") == location:
            return {k: v for k, v in self._price.items() if k != "location"} or None
        out: dict = {}
        try:
            for t in self.provider.server_types(location):
                if t["name"] == self.server_type:
                    hourly = (t.get("prices") or {}).get(location, t.get("price"))
                    if hourly:
                        out = {"hourly": float(hourly), "currency": t.get("currency", "EUR")}
                        monthly = (t.get("monthly") or {}).get(location)
                        if monthly:
                            out["monthly"] = float(monthly)
                    break
        except Exception:  # noqa: BLE001
            return None                         # not cached: asked again next time
        self._price = {**out, "location": location}
        return out or None

    def cost(self, st: dict | None = None, now: float | None = None) -> dict:
        """Running time and costs so far of the server of ST: {"hours", "cost", "hourly", "currency"} (cost
        only with a known price). Hetzner bills every started hour, up to the monthly price; the others bill
        by the second or minute."""
        st = self.state() if st is None else st
        if not st or not st.get("created"):
            return {}
        hours = max(0.0, ((now or time.time()) - float(st["created"])) / 3600.0)
        out = {"hours": round(hours, 3)}
        p = st.get("price") or {}
        if p.get("hourly"):
            billed = math.ceil(hours) if self.provider.key == "hetzner" else hours
            cost = billed * float(p["hourly"])
            if p.get("monthly"):
                cost = min(cost, float(p["monthly"]))
            out.update(cost=round(cost, 4), hourly=float(p["hourly"]), currency=p.get("currency", "EUR"))
        return out


class CloudHost(RemoteHost):
    """A RemoteHost whose server is created on demand: RemoteRun calls ensure() before its first request."""

    def __init__(self, session: CloudSession):
        super().__init__(address=session.label, token="", timeout=30.0)
        self.session = session
        self._host: RemoteHost | None = None

    def ensure(self, status=None, abort=None, note=None) -> None:
        """Before an analysis: the server (found again, or created)."""
        self._host = self.session.ensure(status, abort, note=note)
        self.token = self._host.token
        self.server_id = self.session.state().get("id")

    def _request(self, *args, **kw):
        if self._host is None or not self.session.tunnel.alive() or \
                self.session.state().get("id") != getattr(self, "server_id", None):
            # reconnect after a dropped tunnel (laptop slept, network changed); never onto a new server
            # behind the back of an analysis: a server that is gone raises ServerLost
            if getattr(self, "server_id", None) is not None and self.session.state().get("id") != self.server_id:
                rec = self.session.log.last(self.server_id)
                raise ServerLost(rec.get("name", "?") if rec else "?",
                                 self.session.loss_reason({"id": self.server_id}))
            self._host = self.session.ensure(create=False)
            self.token = self._host.token
        return super()._request(*args, **kw)

    @property
    def url(self) -> str:
        if self._host is None:
            self.ensure()
        return self._host.url
