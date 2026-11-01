"""Cloud computers: Hetzner API client (against a stand-in of the API), first-boot configuration, the
watchdog's decisions and the activity the remote service reports for it. The SSH tunnel needs a real sshd
and is not tested here."""
from __future__ import annotations

import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from hertzon.remote.client import RemoteError
from hertzon.remote.cloud import cloudinit, watchdog
from hertzon.remote.cloud.hetzner import Hetzner


class FakeHetzner(BaseHTTPRequestHandler):
    """The few endpoints of api.hetzner.cloud/v1 Hertzon uses."""
    servers: ClassVar[dict] = {}
    keys: ClassVar[list] = []
    created: ClassVar[list] = []
    sold_out: ClassVar[set] = set()

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self):
        if self.headers.get("Authorization") != "Bearer good":
            self._send(401, {"error": {"code": "unauthorized", "message": "unable to authenticate"}})
            return False
        return True

    def do_GET(self):
        if not self._ok():
            return
        path = self.path.split("?")[0]
        if path == "/server_types":
            return self._send(200, {"server_types": [
                {"name": "cpx31", "cores": 4, "memory": 8.0, "cpu_type": "shared", "architecture": "x86",
                 "deprecated": False, "prices": [{"location": "fsn1", "price_hourly": {"gross": "0.0200"}}]},
                {"name": "ccx33", "cores": 8, "memory": 32.0, "cpu_type": "dedicated", "architecture": "x86",
                 "deprecated": False, "prices": [{"location": "fsn1", "price_hourly": {"gross": "0.1000"}},
                                                 {"location": "nbg1", "price_hourly": {"gross": "0.1000"}}]},
                {"name": "cax41", "cores": 16, "memory": 32.0, "cpu_type": "shared", "architecture": "arm",
                 "deprecated": False, "prices": [{"location": "fsn1", "price_hourly": {"gross": "0.05"}}]}]})
        if path == "/locations":
            return self._send(200, {"locations": [{"name": "fsn1", "city": "Falkenstein", "country": "DE"}]})
        if path == "/ssh_keys":
            return self._send(200, {"ssh_keys": self.keys})
        if path == "/servers":
            return self._send(200, {"servers": list(self.servers.values())})
        if path.startswith("/servers/"):
            s = self.servers.get(int(path.rsplit("/", 1)[1]))
            return self._send(200, {"server": s}) if s else self._send(404, {"error": {"code": "not_found",
                                                                                       "message": "not found"}})
        self._send(404, {"error": {"code": "not_found", "message": path}})

    def do_POST(self):
        if not self._ok():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/ssh_keys":
            k = {"id": len(self.keys) + 1, **body}
            self.keys.append(k)
            return self._send(201, {"ssh_key": k})
        if self.path == "/servers":
            if body["location"] in self.sold_out:
                return self._send(412, {"error": {"code": "resource_unavailable",
                                                  "message": "error during placement"}})
            sid = 100 + len(self.created)
            self.created.append(body)
            s = {"id": sid, "name": body["name"], "status": "initializing", "labels": body["labels"],
                 "server_type": {"name": body["server_type"]},
                 "public_net": {"ipv4": {"ip": f"203.0.113.{sid % 250}"}}}
            self.servers[sid] = s
            return self._send(201, {"server": s})

    def do_DELETE(self):
        if not self._ok():
            return
        sid = int(self.path.rsplit("/", 1)[1])
        if self.servers.pop(sid, None) is None:
            return self._send(404, {"error": {"code": "not_found", "message": "not found"}})
        self._send(200, {"action": {"id": 1}})


@pytest.fixture
def api():
    FakeHetzner.servers, FakeHetzner.keys, FakeHetzner.created, FakeHetzner.sold_out = {}, [], [], set()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeHetzner)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_hetzner_catalogue_and_servers(api):
    with pytest.raises(RemoteError, match="token is wrong"):
        Hetzner("bad", api).locations()
    h = Hetzner("good", api)
    types = h.server_types("fsn1")
    assert [t["name"] for t in types] == ["ccx33", "cpx31"]          # x86 only, dedicated first
    assert types[0]["price"] == 0.1 and types[0]["memory"] == 32.0 and types[0]["dedicated"]
    k1 = h.ssh_key("ssh-ed25519 AAAAkey hertzon@laptop", "laptop")
    assert h.ssh_key("ssh-ed25519 AAAAkey other comment", "laptop") == k1        # added once
    s = h.create("hertzon-abc", "ccx33", "fsn1", "#cloud-config\n{}", k1, "LAPTOP 1")
    assert s["ip"] and FakeHetzner.created[0]["labels"] == {"hertzon": "1", "hertzon-client": "LAPTOP-1"}
    assert FakeHetzner.created[0]["image"] == "docker-ce" and FakeHetzner.created[0]["ssh_keys"] == [k1]
    assert h.get(s["id"])["status"] == "initializing" and [x["name"] for x in h.servers()] == ["hertzon-abc"]
    h.delete(s["id"])
    h.delete(s["id"])                                                  # already gone: no error
    assert h.get(s["id"]) is None and h.servers() == []


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs OpenSSH's ssh-keygen")
def test_session_creates_a_server_with_known_host_key(api, tmp_path):
    from hertzon.remote.cloud import CloudSession
    s = CloudSession(Hetzner("good", api), "ccx33", "fsn1", idle_min=15, workdir=tmp_path, version="9.9.9")
    st = s._create(lambda text: None)
    assert st["id"] in FakeHetzner.servers and s.state()["token"] == st["token"]
    ud = FakeHetzner.created[0]["user_data"]
    assert ud.startswith("#cloud-config\n") and len(ud.encode()) < 32 * 1024     # Hetzner's limit
    cfg = json.loads(ud.split("\n", 1)[1])
    assert cfg["ssh_keys"]["ed25519_public"] == st["host_key"]
    assert "PRIVATE KEY" in cfg["ssh_keys"]["ed25519_private"]
    assert cfg["ssh_authorized_keys"] == [(tmp_path / "cloud" / "id_ed25519.pub").read_text().strip()]
    files = {f["path"]: f["content"] for f in cfg["write_files"]}
    wcfg = json.loads(files["/etc/hertzon/watchdog.json"])
    assert wcfg == {**wcfg, "provider": "hetzner", "token": st["token"], "api_token": "good", "idle_min": 15}
    assert "def decide" in files["/etc/hertzon/watchdog.py"]
    start = files["/etc/hertzon/start.sh"]
    assert "hertzon-solver:9.9.9" in start and "hertzon-solver:dev" in start and "127.0.0.1:47820:47820" in start
    assert s.delete() and not s.state() and FakeHetzner.servers == {}


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs OpenSSH's ssh-keygen")
def test_sold_out_location_falls_back(api, tmp_path):
    """Hetzner answers 412 when the type is sold out at a location: the other locations are tried."""
    from hertzon.remote.cloud import CloudSession
    FakeHetzner.sold_out = {"fsn1"}
    said = []
    s = CloudSession(Hetzner("good", api), "ccx33", "fsn1", workdir=tmp_path)
    st = s._create(said.append)
    assert st["location"] == "nbg1" and any("trying nbg1" in x for x in said)
    s.delete()
    FakeHetzner.sold_out = {"fsn1", "nbg1"}
    with pytest.raises(RemoteError, match="no ccx33 server free"):
        s._create(said.append)


def test_watchdog_decisions():
    cfg = {"idle_min": 30, "contact_h": 48, "quiet_h": 2, "start_min": 20}
    idle = {"busy": False, "queued": 0, "idle": 0, "contact": 0, "quiet": 0}
    assert watchdog.decide(idle, cfg, 0) == ""
    assert watchdog.decide({**idle, "idle": 31 * 60}, cfg, 0) == "idle"
    run = {**idle, "busy": True, "idle": 0}
    assert watchdog.decide({**run, "contact": 47 * 3600, "quiet": 60}, cfg, 0) == ""      # long but alive
    assert "cost limit" in watchdog.decide({**run, "contact": 49 * 3600}, cfg, 0)
    assert "hung" in watchdog.decide({**run, "quiet": 3 * 3600}, cfg, 0)
    assert watchdog.decide({**idle, "queued": 1, "idle": 0, "contact": 40 * 60}, cfg, 0) == ""
    assert watchdog.decide(None, cfg, 5 * 60) == ""                     # still pulling the image
    assert "did not answer" in watchdog.decide(None, cfg, 21 * 60)


def test_service_reports_activity(tmp_path):
    from hertzon.remote.server import Service
    svc = Service(tmp_path / "w", "t")
    try:
        svc.last_contact = svc.last_done = time.time() - 100
        a = svc.info()
        assert 99 < a["idle"] < 110 and 99 < a["contact"] < 110 and a["quiet"] == 0
    finally:
        svc.stop()


def test_start_script_quotes():
    s = cloudinit.start_script("img:1", "img:dev", "to'ken", ["python", "-m", "hertzon.remote", "serve"])
    assert "HERTZON_TOKEN='to'\"'\"'ken'" in s and "python -m hertzon.remote serve" in s
