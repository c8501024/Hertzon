"""A computer reached over SSH (a workstation, a rented server, a login node): Hertzon logs in with the
user's own SSH access, starts the remote service there if it does not run yet, and talks to it through an
SSH tunnel. Nothing listens on the network except SSH, and no pairing is needed.

    session = SSHSession("anna@simbox.example.org", start="python")
    host = SSHHost(session)          # a RemoteHost: RemoteRun calls host.ensure() before the first request

The remote computer runs Linux or macOS and has either Hertzon (COMMAND starts its remote module) or Docker
(the solver image of this Hertzon version is pulled and started). The login has to work without a password
prompt: a key in the SSH agent or one without passphrase, or the key file given here. Host keys follow the
user's own known_hosts; an unknown host is added on the first connection (as ``ssh`` with accept-new).
"""
from __future__ import annotations

import shlex
import subprocess
import threading
import time
from pathlib import Path

from .client import RemoteError, RemoteHost
from .cloud import PORT, _no_window, free_port, tool
from .cloud.cloudinit import image_for

DEFAULT_COMMAND = "~/.local/share/hertzon/.venv/bin/python -m hertzon.remote"    # the Linux installer's place
CONTAINER = "hertzon-ssh"
MARK = "HERTZON-TOKEN "


def split_target(target: str) -> tuple[str, str]:
    """(destination for ssh, port or '') of 'user@host', 'user@host:2222', 'host' or an alias of
    ~/.ssh/config. An IPv6 address keeps its colons ([::1]:2222 for a port)."""
    t = (target or "").strip().removeprefix("ssh://")
    user, _, rest = t.rpartition("@")
    if rest.startswith("["):                          # [IPv6] or [IPv6]:port
        host, _, port = rest[1:].partition("]")
        port = port.lstrip(":")
    elif rest.count(":") == 1:
        host, port = rest.split(":")
    else:
        host, port = rest, ""
    if port and not port.isdigit():
        raise RemoteError(f"SSH: '{target}' has no valid port")
    if not host:
        raise RemoteError("SSH: enter the computer (user@host)")
    return (f"{user}@{host}" if user else host), port


def start_script(start: str, command: str, version: str, port: int = PORT) -> str:
    """The shell script that starts the remote service unless it runs and prints its token (MARK + token)."""
    if start == "docker":
        image, fallback = image_for(version)
        q = shlex.quote
        return f"""set -e
D="$HOME/.hertzon"; mkdir -p "$D/work"; F="$D/ssh-token"
[ -s "$F" ] || ( umask 077; head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \\n' > "$F" )
TOK=$(cat "$F")
IMG={q(image)}; FALLBACK={q(fallback)}
CUR=$(docker inspect -f '{{{{.Config.Image}}}}' {CONTAINER} 2>/dev/null || true)
RUN=$(docker inspect -f '{{{{.State.Running}}}}' {CONTAINER} 2>/dev/null || true)
if [ "$RUN" != true ] || {{ [ "$CUR" != "$IMG" ] && [ "$CUR" != "$FALLBACK" ]; }}; then
  docker pull "$IMG" >&2 || {{ IMG="$FALLBACK"; docker pull "$IMG" >&2; }}
  docker rm -f {CONTAINER} >/dev/null 2>&1 || true
  docker run -d --name {CONTAINER} --hostname "$(hostname)" -p 127.0.0.1:{port}:{PORT} \\
      -e HERTZON_TOKEN="$TOK" -v "$D/work:/work" "$IMG" >&2
fi
echo "{MARK}$TOK"
"""
    cmd = (command or DEFAULT_COMMAND).strip()
    return f"""set -e
D="$HOME/.hertzon"; mkdir -p "$D"
TOK=$({cmd} token) || {{ echo {shlex.quote("Hertzon not found: '" + cmd + " token' failed")} >&2; exit 3; }}
TOK=$(printf '%s\n' "$TOK" | tail -n 1)
PID=$(cat "$D/ssh-serve.pid" 2>/dev/null || true)
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
  nohup {cmd} serve --host 127.0.0.1 --port {port} > "$D/ssh-serve.log" 2>&1 < /dev/null &
  echo $! > "$D/ssh-serve.pid"
fi
echo "{MARK}$TOK"
"""


class SSHSession:
    """The SSH access to one computer and the tunnel to its remote service."""

    def __init__(self, target: str, key: str = "", start: str = "python", command: str = DEFAULT_COMMAND,
                 version: str | None = None, workdir: Path | None = None, remote_port: int = PORT):
        from .. import __version__
        from .server import default_workdir
        self.target, self.key = target.strip(), (key or "").strip()
        self.start = "docker" if start == "docker" else "python"
        self.command = (command or DEFAULT_COMMAND).strip()
        self.version = version or __version__
        self.dest, self.port = split_target(self.target)
        self.remote_port = remote_port
        self.folder = Path(workdir or default_workdir()) / "ssh"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen | None = None
        self.local_port = 0
        self.token = ""
        self.lock = threading.RLock()

    @property
    def label(self) -> str:
        return self.target

    def _options(self) -> list[str]:
        opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
        if self.key:
            opts += ["-i", str(Path(self.key).expanduser()), "-o", "IdentitiesOnly=yes"]
        if self.port:
            opts += ["-p", self.port]
        return opts

    @staticmethod
    def _why(err: str) -> str:
        lines = [ln for ln in (err or "").strip().splitlines() if ln.strip() and not ln.startswith("Warning:")]
        last = lines[-1] if lines else ""
        if "Permission denied" in last:
            last += " (the login must work without a password prompt: add the key to the SSH agent, use a key " \
                    "without passphrase or choose its file)"
        return last

    def run(self, script: str, timeout: float = 900) -> str:
        """Run the shell SCRIPT on the computer; its standard output."""
        cmd = [tool("ssh"), *self._options(), self.dest, "sh -s"]
        try:
            r = subprocess.run(cmd, input=script.encode(), capture_output=True, timeout=timeout, check=False,
                               **_no_window())
        except subprocess.TimeoutExpired:
            raise RemoteError(f"SSH: {self.target} did not answer within {timeout:.0f} s") from None
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", "replace")
            raise RemoteError(f"SSH to {self.target} failed: {self._why(err) or f'exit code {r.returncode}'}")
        return r.stdout.decode("utf-8", "replace")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _tunnel(self, timeout: float = 30) -> None:
        import socket
        self.close()
        self.local_port = free_port()
        log = self.folder / "ssh.log"
        cmd = [tool("ssh"), "-N", *self._options(), "-L", f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}",
               "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
               self.dest]
        with open(log, "wb") as f:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=f, **_no_window())
        end = time.monotonic() + timeout
        while time.monotonic() < end and self.proc.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=1):
                    return
            except OSError:
                time.sleep(0.3)
        err = log.read_text(encoding="utf-8", errors="replace")
        self.close()
        raise RemoteError(f"SSH tunnel to {self.target} failed" + (f": {self._why(err)}" if err.strip() else ""))

    def ensure(self, status=None, abort: threading.Event | None = None) -> RemoteHost:
        """The remote service on the computer: started there if needed, reached through the tunnel."""
        say = status or (lambda text: None)
        with self.lock:
            if self.alive() and self.token:
                return RemoteHost(f"127.0.0.1:{self.local_port}", self.token, timeout=30)
            say(f"Connecting to {self.target} (SSH)")
            out = self.run(start_script(self.start, self.command, self.version, self.remote_port))
            toks = [ln[len(MARK):].strip() for ln in out.splitlines() if ln.startswith(MARK)]
            if not toks or not toks[-1]:
                raise RemoteError(f"SSH: the Hertzon service on {self.target} did not start (see "
                                  "~/.hertzon/ssh-serve.log there)")
            self.token = toks[-1]
            self._tunnel()
            host = RemoteHost(f"127.0.0.1:{self.local_port}", self.token, timeout=10)
            t0 = time.monotonic()
            limit = 900 if self.start == "docker" else 120
            while True:
                if abort is not None and abort.is_set():
                    raise RemoteError(f"aborted while connecting to {self.target}")
                try:
                    host.info()
                    return RemoteHost(host.address, self.token, timeout=30)
                except RemoteError as ex:
                    if time.monotonic() - t0 > limit:
                        self.close()
                        raise RemoteError(f"The Hertzon service on {self.target} does not answer ({ex}); see "
                                          + ("'docker logs " + CONTAINER + "'" if self.start == "docker"
                                             else "~/.hertzon/ssh-serve.log") + " there") from None
                    if not self.alive():
                        self._tunnel()
                say(f"Starting the Hertzon service on {self.target} ({time.monotonic() - t0:.0f} s)")
                time.sleep(2)

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class SSHHost(RemoteHost):
    """A RemoteHost reached over SSH: RemoteRun calls ensure() before its first request."""

    def __init__(self, session: SSHSession):
        super().__init__(address=session.label, token="", timeout=30.0)
        self.session = session
        self._host: RemoteHost | None = None

    def ensure(self, status=None, abort=None) -> None:
        self._host = self.session.ensure(status, abort)
        self.token = self._host.token

    def _request(self, *args, **kw):
        if self._host is None or not self.session.alive():
            self.ensure()                   # reconnects after a dropped tunnel (laptop slept, network changed)
        return super()._request(*args, **kw)

    @property
    def url(self) -> str:
        if self._host is None:
            self.ensure()
        return self._host.url
