"""Cloud computers on Google Cloud: the RS256 signature of the service account key, the Compute Engine client
against a stand-in of the API, and a session that takes the host key from the API."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from hertzon.remote.client import RemoteError
from hertzon.remote.cloud import providers, watchdog
from hertzon.remote.cloud.gcp import GCP, rs256
from hertzon.remote.cloud.hetzner import PlacementError

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to make an RSA key")


@pytest.fixture(scope="module")
def rsa_pem(tmp_path_factory):
    if shutil.which("openssl") is None:
        pytest.skip("needs openssl")
    path = tmp_path_factory.mktemp("key") / "key.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(path)],
                   check=True, capture_output=True)
    return path


@needs_openssl
def test_rs256_verifies_with_openssl(rsa_pem, tmp_path):
    msg = b"header.claims"
    sig = rs256(rsa_pem.read_text(), msg)
    (tmp_path / "m").write_bytes(msg)
    (tmp_path / "s").write_bytes(sig)
    pub = tmp_path / "pub.pem"
    subprocess.run(["openssl", "pkey", "-in", str(rsa_pem), "-pubout", "-out", str(pub)], check=True)
    r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(tmp_path / "s"),
                        str(tmp_path / "m")], capture_output=True, text=True, check=False)
    assert "Verified OK" in r.stdout


class FakeGoogle(BaseHTTPRequestHandler):
    """The OAuth token endpoint and the Compute Engine calls Hertzon uses (project "proj")."""
    instances: ClassVar[dict] = {}
    firewalls: ClassVar[list] = []
    created: ClassVar[list] = []
    full_zones: ClassVar[set] = set()
    base: ClassVar[str] = ""
    host_key_after: ClassVar[int] = 0

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _op(self, error=None):
        op = {"status": "RUNNING", "selfLink": f"{self.base}/compute/v1/projects/proj/zones/z/operations/op1"}
        FakeGoogle.pending_error = error
        return self._send(200, op)

    def _auth(self):
        if self.headers.get("Authorization") != "Bearer tok-1":
            self._send(401, {"error": {"code": 401, "message": "Request had invalid authentication credentials."}})
            return False
        return True

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/token":
            form = dict(urllib.parse.parse_qsl(raw.decode()))
            _head, claim, sig = form["assertion"].split(".")
            c = json.loads(base64.urlsafe_b64decode(claim + "=="))
            assert form["grant_type"].endswith("jwt-bearer") and len(base64.urlsafe_b64decode(sig + "==")) == 256
            if c["iss"] != "hertzon@proj.iam.gserviceaccount.com":
                return self._send(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."})
            assert c["scope"].endswith("/auth/compute") and c["exp"] - c["iat"] == 3600
            return self._send(200, {"access_token": "tok-1", "expires_in": 3600})
        if not self._auth():
            return None
        body = json.loads(raw) if raw else {}
        path = self.path.split("?")[0].replace("/compute/v1/projects/proj/", "")
        if path == "global/firewalls":
            self.firewalls.append(body)
            return self._op()
        if path.endswith("/instances"):
            zone = path.split("/")[1]
            if zone in self.full_zones:
                return self._op({"errors": [{"code": "ZONE_RESOURCE_POOL_EXHAUSTED",
                                             "message": f"The zone '{zone}' does not have enough resources"}]})
            self.created.append(body)
            self.instances[body["name"]] = {
                "name": body["name"], "zone": f"https://x/projects/proj/zones/{zone}", "status": "PROVISIONING",
                "machineType": f"https://x/{body['machineType']}", "labels": body["labels"], "polls": 0,
                "networkInterfaces": [{"accessConfigs": [{"natIP": "203.0.113.9"}]}]}
            return self._op()
        return self._send(404, {"error": {"code": 404, "message": path}})

    def do_GET(self):
        if not self._auth():
            return None
        path = self.path.split("?")[0].replace("/compute/v1/projects/proj/", "")
        if path.endswith("operations/op1"):
            err = getattr(FakeGoogle, "pending_error", None)
            return self._send(200, {"status": "DONE", **({"error": err} if err else {})})
        if path == "zones":
            return self._send(200, {"items": [{"name": n, "status": "UP"} for n in
                                              ("europe-west3-a", "europe-west3-b", "us-central1-a")]
                                    + [{"name": "europe-west3-c", "status": "DOWN"}]})
        if path.endswith("/machineTypes"):
            return self._send(200, {"items": [
                {"name": "c3d-standard-8", "guestCpus": 8, "memoryMb": 32768},
                {"name": "n2d-standard-16", "guestCpus": 16, "memoryMb": 65536},
                {"name": "e2-micro", "guestCpus": 2, "memoryMb": 1024}]})
        if path.startswith("global/firewalls/"):
            if self.firewalls:
                return self._send(200, self.firewalls[0])
            return self._send(404, {"error": {"code": 404, "message": "not found"}})
        if path == "aggregated/instances":
            q = urllib.parse.unquote(self.path.split("filter=", 1)[1])
            assert q == "labels.hertzon=1"
            return self._send(200, {"items": {"zones/europe-west3-a": {"instances": list(self.instances.values())}}})
        if path.endswith("/getGuestAttributes"):
            i = self.instances.get(path.split("/")[3])
            i["polls"] += 1
            if i["polls"] <= self.host_key_after:
                return self._send(404, {"error": {"code": 404, "message": "The resource was not found"}})
            return self._send(200, {"queryValue": {"items": [
                {"namespace": "hostkeys", "key": "ssh-rsa", "value": "ssh-rsa AAAArsa"},
                {"namespace": "hostkeys", "key": "ssh-ed25519", "value": "ssh-ed25519 AAAAed25519key"}]}})
        if "/instances/" in path:
            i = self.instances.get(path.rsplit("/", 1)[1])
            return self._send(200, i) if i else self._send(404, {"error": {"code": 404, "message": "not found"}})
        return self._send(404, {"error": {"code": 404, "message": path}})

    def do_DELETE(self):
        if not self._auth():
            return None
        name = self.path.rsplit("/", 1)[1]
        if self.instances.pop(name, None) is None:
            return self._send(404, {"error": {"code": 404, "message": "not found"}})
        return self._op()


@pytest.fixture
def gcloud(rsa_pem):
    FakeGoogle.instances, FakeGoogle.firewalls, FakeGoogle.created, FakeGoogle.full_zones = {}, [], [], set()
    FakeGoogle.host_key_after = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeGoogle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    FakeGoogle.base = base

    def make(email="hertzon@proj.iam.gserviceaccount.com", zone="europe-west3-a"):
        info = {"type": "service_account", "project_id": "proj", "client_email": email,
                "private_key": rsa_pem.read_text(), "private_key_id": "k1"}
        return GCP("", zone, info=info, api=f"{base}/compute/v1", token_url=f"{base}/token")
    yield make
    srv.shutdown()
    srv.server_close()


def test_gcp_catalogue(gcloud):
    with pytest.raises(RemoteError, match="not accepted"):
        gcloud("other@proj.iam.gserviceaccount.com").locations()
    g = gcloud()
    zones = g.locations()
    assert [z["name"] for z in zones] == ["europe-west3-a", "europe-west3-b", "us-central1-a"]    # UP only
    assert zones[0]["city"] == "Frankfurt"
    types = g.server_types()
    assert [t["name"] for t in types] == ["c3d-standard-8", "n2d-standard-16"]                    # no e2-micro
    assert types[0]["physical"] == 4 and types[0]["memory"] == 32 and "europe-west3-a" in types[0]["prices"]
    assert g.locations_for("c3d-standard-8") == ["europe-west3-a", "europe-west3-b"]


def test_gcp_servers(gcloud):
    g = gcloud()
    s = g.create("hertzon-abc", "c3d-standard-8", "europe-west3-a", "#cloud-config\n{}", "ssh-ed25519 AAAAme x",
                 "LAPTOP 1")
    body = FakeGoogle.created[0]
    meta = {i["key"]: i["value"] for i in body["metadata"]["items"]}
    assert meta["user-data"] == "#cloud-config\n{}" and meta["ssh-keys"] == "ubuntu:ssh-ed25519 AAAAme x"
    assert meta["enable-guest-attributes"] == "TRUE" and body["labels"] == {"hertzon": "1", "hertzon-client": "laptop-1"}
    assert body["tags"]["items"] == ["hertzon"] and body["disks"][0]["autoDelete"]
    assert FakeGoogle.firewalls[0]["allowed"] == [{"IPProtocol": "tcp", "ports": ["22"]}]
    assert s["id"] == "europe-west3-a/hertzon-abc" and s["ip"] == "203.0.113.9" and s["status"] == "initializing"
    g.create("hertzon-def", "c3d-standard-8", "europe-west3-a", "#cloud-config\n{}", "", "x")
    assert len(FakeGoogle.firewalls) == 1                                  # the rule is made once
    assert g.host_key(s["id"]) == "ssh-ed25519 AAAAed25519key"
    assert sorted(x["name"] for x in g.servers()) == ["hertzon-abc", "hertzon-def"]
    g.delete(s["id"])
    g.delete(s["id"])                                                      # already gone: no error
    assert g.get(s["id"]) is None


def test_gcp_full_zone(gcloud):
    FakeGoogle.full_zones = {"europe-west3-a"}
    with pytest.raises(PlacementError, match="enough resources"):
        gcloud().create("hertzon-abc", "c3d-standard-8", "europe-west3-a", "#cloud-config\n{}", "", "x")


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs OpenSSH's ssh-keygen")
def test_gcp_session_takes_the_host_key_from_the_api(gcloud, tmp_path):
    from hertzon.remote.cloud import CloudSession
    FakeGoogle.full_zones = {"europe-west3-a"}                             # sold out: the next zone of the region
    FakeGoogle.host_key_after = 1
    said = []
    s = CloudSession(gcloud(), "c3d-standard-8", "europe-west3-a", workdir=tmp_path, version="9.9.9")
    st = s._create(said.append)
    assert st["location"] == "europe-west3-b" and s.tunnel.user == "ubuntu"
    cfg = json.loads(FakeGoogle.created[0]["metadata"]["items"][0]["value"].split("\n", 1)[1])
    assert "ssh_keys" not in cfg and cfg["packages"] == ["docker.io"]      # Google's agent makes the host keys
    wcfg = json.loads({f["path"]: f["content"] for f in cfg["write_files"]}["/etc/hertzon/watchdog.json"])
    assert wcfg["provider"] == "gcp" and wcfg["api_token"] == "" and "gcp" in watchdog.DELETE
    FakeGoogle.instances[st["name"]]["status"] = "RUNNING"
    s.tunnel.open = lambda ip, key, timeout=20: (_ for _ in ()).throw(SystemExit(key))   # stop at the tunnel
    with pytest.raises(SystemExit) as ex:
        s._connect(st, said.append, None)
    assert str(ex.value) == "ssh-ed25519 AAAAed25519key" and s.state()["host_key"] == str(ex.value)
    assert any("host key" in x for x in said)
    assert s.delete() and FakeGoogle.instances == {}


def test_gcp_settings(tmp_path):
    vals = {"cloud/provider": "gcp", "cloud/gcp_key_file": str(tmp_path / "missing.json")}
    get = lambda k, d=None: vals.get(k, d)
    prov, stype, loc = providers.from_settings(get)
    assert prov.key == "gcp" and (stype, loc) == ("c3d-standard-8", "europe-west3-a") and providers.configured(get)
    with pytest.raises(RemoteError, match="cannot be read"):
        prov.locations()
    (tmp_path / "k.json").write_text(json.dumps({"type": "authorized_user"}))
    vals["cloud/gcp_key_file"] = str(tmp_path / "k.json")
    with pytest.raises(RemoteError, match="not a service account key"):
        providers.from_settings(get)[0].locations()
