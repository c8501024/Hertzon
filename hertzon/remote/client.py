"""Client of the remote service (standard library only; the GUI runs it in a background thread)."""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from . import DEFAULT_PORT, PROTOCOL
from .protocol import changed, file_list, local_target, pack, replace_file


class RemoteError(RuntimeError):
    pass


def normalize_address(address: str) -> str:
    """'simstation02', 'simstation02:47820' or 'http://10.0.0.5:47820/' -> base URL with port."""
    a = (address or "").strip().rstrip("/")
    if not a:
        raise RemoteError("No remote computer is set (Tools > Options > Remote)")
    if "://" not in a:
        a = "http://" + a
    scheme, rest = a.split("://", 1)
    host = rest.split("/", 1)[0]
    if ":" not in host.rsplit("]", 1)[-1]:
        host += f":{DEFAULT_PORT}"
    return f"{scheme}://{host}"


@dataclass
class RemoteHost:
    address: str
    token: str
    timeout: float = 30.0

    @property
    def url(self) -> str:
        return normalize_address(self.address)

    # ---- transport ----------------------------------------------------------------------------------
    def _request(self, method: str, path: str, data=None, headers: dict | None = None, timeout: float | None = None):
        h = {"Authorization": f"Bearer {self.token.strip()}"}
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        # no proxy: the service is in the local network
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            return opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as ex:
            try:
                msg = json.loads(ex.read().decode("utf-8")).get("error", "")
            except Exception:  # noqa: BLE001
                msg = ""
            if ex.code == 401:
                raise RemoteError(f"{self.address}: the token is wrong (see the start message of the "
                                  "remote service)") from None
            raise RemoteError(f"{self.address}: {msg or ex.reason} (HTTP {ex.code})") from None
        except (urllib.error.URLError, OSError) as ex:
            reason = getattr(ex, "reason", ex)
            raise RemoteError(f"{self.address}: no connection to the remote service ({reason})") from None

    def _json(self, method: str, path: str, data=None, headers=None, timeout=None) -> dict:
        with self._request(method, path, data, headers, timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    # ---- API -------------------------------------------------------------------------------------------
    def info(self) -> dict:
        info = self._json("GET", "/api/info", timeout=10)
        if info.get("service") != "hertzon-remote":
            raise RemoteError(f"{self.address} is not a Hertzon remote service")
        if int(info.get("protocol", 0)) != PROTOCOL:
            raise RemoteError(f"{self.address} runs Hertzon {info.get('hertzon')}, which cannot work with this "
                              "version; install the same Hertzon version on both computers")
        return info

    def submit(self, archive: Path, meta: dict) -> dict:
        size = Path(archive).stat().st_size
        with open(archive, "rb") as f:
            return self._json("POST", "/api/jobs", data=f, timeout=max(self.timeout, size / 2e6),
                              headers={"Content-Type": "application/zip", "Content-Length": str(size),
                                       "X-Hertzon-Job": json.dumps(meta)})

    def events(self, jid: str, since: int, wait: float = 15.0) -> dict:
        q = urlencode({"since": since, "wait": wait})
        return self._json("GET", f"/api/jobs/{jid}/events?{q}", timeout=wait + self.timeout)

    def abort(self, jid: str) -> dict:
        return self._json("POST", f"/api/jobs/{jid}/abort", data=b"")

    def shutdown(self) -> dict:
        return self._json("POST", "/api/shutdown", data=b"")

    def delete(self, jid: str) -> dict:
        return self._json("DELETE", f"/api/jobs/{jid}")

    def files(self, jid: str) -> list[dict]:
        return self._json("GET", f"/api/jobs/{jid}/files").get("files", [])

    def fetch(self, jid: str, rel: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".remote-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f, self._request("GET", f"/api/jobs/{jid}/file?path={quote(rel)}",
                                                          timeout=max(self.timeout, 120)) as r:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            replace_file(Path(tmp), target)
        finally:
            Path(tmp).unlink(missing_ok=True)


class Mirror:
    """Keeps a local results folder equal to the remote one: fetches new and changed files (smallest
    first) and, at the end, removes local files the solver removed remotely."""

    def __init__(self, host: RemoteHost, jid: str, folder: Path):
        self.host, self.jid, self.folder = host, jid, Path(folder)
        self.seen: dict[str, tuple[int, int]] = {}

    def sync(self, final: bool = False, progress=None) -> int:
        remote = self.host.files(self.jid)
        todo = changed(remote, self.seen)
        total = sum(e["size"] for e in todo)
        done = 0
        for e in todo:
            if e["path"] == "ABORT" or e.get("uploaded"):      # unchanged since the upload: already here
                self.seen[e["path"]] = (e["size"], e["mtime"])
                continue
            self.host.fetch(self.jid, e["path"], local_target(self.folder, e["path"]))
            self.seen[e["path"]] = (e["size"], e["mtime"])
            done += e["size"]
            if progress is not None:
                progress(done, total)
        if final:
            keep = {e["path"] for e in remote}
            for e in file_list(self.folder):
                if e["path"] not in keep:
                    try:
                        local_target(self.folder, e["path"]).unlink()
                    except OSError:
                        pass
        return len(todo)


def prepare(project: Path, out: Path, workdir: Path | None = None) -> tuple[Path, str]:
    """The job archive of PROJECT with the results folder OUT, in a temporary file."""
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="hertzon-job-", dir=workdir)
    os.close(fd)
    rel = pack(Path(project), Path(out), Path(tmp))
    return Path(tmp), rel
