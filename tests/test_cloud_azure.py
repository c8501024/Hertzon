"""Cloud computers on Microsoft Azure: the Resource Manager client against a stand-in of the API (sign-in,
regions, sizes with prices, servers in the resource group "hertzon") and the session's first-boot data."""
from __future__ import annotations

import base64
import json
import shutil
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from hertzon.remote.client import RemoteError
from hertzon.remote.cloud import providers, watchdog
from hertzon.remote.cloud.azure import Azure, credentials_from
from hertzon.remote.cloud.hetzner import PlacementError

CRED = json.dumps({"appId": "app-1", "displayName": "hertzon", "password": "pw", "tenant": "ten-1"})
SUB = "sub-1"


class FakeAzure(BaseHTTPRequestHandler):
    res: ClassVar[dict] = {}            # resource path -> resource
    vm_puts: ClassVar[list] = []
    full: ClassVar[bool] = False
    no_group: ClassVar[bool] = False

    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code, ecode, msg, details=()):
        return self._send(code, {"error": {"code": ecode, "message": msg, "details": [{"code": d} for d in details]}})

    def _path(self):
        u = urllib.parse.urlsplit(self.path)
        assert "api-version=" in u.query
        return u.path.replace(f"/subscriptions/{SUB}/", ""), dict(urllib.parse.parse_qsl(u.query))

    def _auth(self):
        if self.headers.get("Authorization") != "Bearer arm-tok":
            self._err(401, "InvalidAuthenticationToken", "bad token")
            return False
        return True

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/ten-1/oauth2/v2.0/token":
            f = dict(urllib.parse.parse_qsl(raw.decode()))
            if f["client_secret"] != "pw":
                return self._send(401, {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret"})
            assert f["scope"] == "https://management.azure.com/.default" and f["client_id"] == "app-1"
            return self._send(200, {"access_token": "arm-tok", "expires_in": 3599})
        return self._send(404, {})

    def do_GET(self):
        if self.path.startswith("/prices"):
            q = urllib.parse.unquote(self.path)
            assert "armRegionName eq 'germanywestcentral'" in q and "currencyCode='EUR'" in q
            items = [{"armSkuName": "Standard_D8as_v5", "retailPrice": 0.398, "skuName": "D8as v5",
                      "productName": "Virtual Machines Dasv5 Series"},
                     {"armSkuName": "Standard_D8as_v5", "retailPrice": 0.75, "skuName": "D8as v5",
                      "productName": "Virtual Machines Dasv5 Series Windows"},
                     {"armSkuName": "Standard_D8as_v5", "retailPrice": 0.08, "skuName": "D8as v5 Spot",
                      "productName": "Virtual Machines Dasv5 Series"}]
            return self._send(200, {"Items": items, "NextPageLink": None})
        if not self._auth():
            return None
        path, q = self._path()
        if path == "locations":
            return self._send(200, {"value": [
                {"name": "germanywestcentral", "displayName": "Germany West Central", "metadata": {"regionType": "Physical"}},
                {"name": "westeurope", "displayName": "West Europe", "metadata": {"regionType": "Physical"}},
                {"name": "europe", "displayName": "Europe", "metadata": {"regionType": "Logical"}}]})
        if path.endswith("/vmSizes"):
            return self._send(200, {"value": [
                {"name": "Standard_D8as_v5", "numberOfCores": 8, "memoryInMB": 32768},
                {"name": "Standard_HB120rs_v3", "numberOfCores": 120, "memoryInMB": 458752},
                {"name": "Standard_B2s", "numberOfCores": 2, "memoryInMB": 4096}]})
        if self.no_group:
            return self._err(404, "ResourceGroupNotFound", "Resource group 'hertzon' could not be found.")
        if path.endswith("Microsoft.Compute/virtualMachines"):
            return self._send(200, {"value": [r for p, r in self.res.items() if "/virtualMachines/" in p]})
        r = self.res.get(path)
        if r is None:
            return self._err(404, "ResourceNotFound", "not found")
        if "/virtualMachines/" in path:
            r = json.loads(json.dumps(r))
            assert q.get("$expand") == "instanceView"
        return self._send(200, r)

    def do_PUT(self):
        if not self._auth():
            return None
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        path, _ = self._path()
        if self.no_group:
            return self._err(404, "ResourceGroupNotFound", "Resource group 'hertzon' could not be found.")
        name = path.rsplit("/", 1)[1]
        res = {"id": f"/subscriptions/{SUB}/{path}", "name": name, **body,
               "properties": {**body.get("properties", {}), "provisioningState": "Succeeded"}}
        if "/virtualMachines/" in path:
            self.vm_puts.append(body)
            if self.full:
                return self._err(409, "SkuNotAvailable", "The requested size is currently not available")
            res["properties"]["provisioningState"] = "Creating"
            res["properties"]["instanceView"] = {"statuses": [{"code": "ProvisioningState/creating"}]}
        if "/virtualNetworks/" in path:
            res["properties"]["subnets"][0]["id"] = res["id"] + "/subnets/default"
        if "/publicIPAddresses/" in path:
            res["properties"]["ipAddress"] = "20.0.0.7"
        self.res[path] = res
        return self._send(201, res)

    def do_DELETE(self):
        if not self._auth():
            return None
        path, q = self._path()
        if self.res.pop(path, None) is None:
            return self._send(204)
        if "/virtualMachines/" in path:
            assert q.get("forceDeletion") == "true"
            for k in [k for k in self.res if k.endswith((path.rsplit("/", 1)[1] + "-nic", path.rsplit("/", 1)[1] + "-ip"))]:
                self.res.pop(k)              # deleteOption: Delete
        return self._send(202)


@pytest.fixture
def az():
    FakeAzure.res, FakeAzure.vm_puts, FakeAzure.full, FakeAzure.no_group = {}, [], False, False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAzure)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def make(cred=CRED, sub=SUB):
        return Azure(cred, sub, "germanywestcentral", arm=base, login=base, prices_url=f"{base}/prices")
    yield make
    srv.shutdown()
    srv.server_close()


def test_credentials(tmp_path):
    assert credentials_from(CRED) == {"tenant": "ten-1", "client": "app-1", "secret": "pw"}
    (tmp_path / "sp.json").write_text(CRED)
    assert credentials_from(str(tmp_path / "sp.json"))["client"] == "app-1"
    with pytest.raises(RemoteError, match="appId, password and tenant"):
        credentials_from('{"appId": "x"}')
    with pytest.raises(RemoteError, match="cannot be read"):
        credentials_from(str(tmp_path / "missing.json"))


def test_azure_catalogue(az):
    with pytest.raises(RemoteError, match="not accepted"):
        az(CRED.replace('"pw"', '"wrong"')).locations()
    a = az()
    assert [x["name"] for x in a.locations()] == ["germanywestcentral", "westeurope"]       # physical only
    types = {t["name"]: t for t in a.server_types()}
    assert set(types) == {"Standard_D8as_v5", "Standard_HB120rs_v3"}                       # no B-series
    d8 = types["Standard_D8as_v5"]
    assert d8["price"] == pytest.approx(0.398) and d8["physical"] == 4 and d8["memory"] == 32   # Linux, on demand
    assert types["Standard_HB120rs_v3"]["physical"] == 120 and types["Standard_HB120rs_v3"]["price"] is None


def test_azure_servers(az):
    a = az()
    s = a.create("hertzon-abc", "Standard_D8as_v5", "germanywestcentral", "#cloud-config\n{}", "ssh-ed25519 AAAAme",
                 "LAPTOP 1")
    vm = FakeAzure.vm_puts[0]["properties"]
    assert base64.b64decode(vm["osProfile"]["customData"]) == b"#cloud-config\n{}"
    assert vm["osProfile"]["linuxConfiguration"]["ssh"]["publicKeys"][0]["keyData"] == "ssh-ed25519 AAAAme"
    assert vm["storageProfile"]["osDisk"]["deleteOption"] == "Delete"
    assert vm["networkProfile"]["networkInterfaces"][0]["properties"]["deleteOption"] == "Delete"
    nic = FakeAzure.res["resourceGroups/hertzon/providers/Microsoft.Network/networkInterfaces/hertzon-abc-nic"]
    assert nic["properties"]["ipConfigurations"][0]["properties"]["publicIPAddress"]["properties"]["deleteOption"] == "Delete"
    nsg = FakeAzure.res["resourceGroups/hertzon/providers/Microsoft.Network/networkSecurityGroups/hertzon-ssh-germanywestcentral"]
    assert nsg["properties"]["securityRules"][0]["properties"]["destinationPortRange"] == "22"
    assert s == {**s, "id": "hertzon-abc", "ip": "20.0.0.7", "status": "initializing"}
    g = a.get("hertzon-abc")
    assert g["status"] == "initializing" and g["ip"] == "20.0.0.7"
    vmres = FakeAzure.res["resourceGroups/hertzon/providers/Microsoft.Compute/virtualMachines/hertzon-abc"]
    vmres["properties"].update(provisioningState="Succeeded", instanceView={"statuses": [
        {"code": "ProvisioningState/succeeded"}, {"code": "PowerState/running"}]})
    assert a.get("hertzon-abc")["status"] == "running"
    assert [x["name"] for x in a.servers()] == ["hertzon-abc"] and a.servers()[0]["client"] == "LAPTOP 1"
    a.delete("hertzon-abc")
    a.delete("hertzon-abc")                                                 # already gone: no error
    assert a.get("hertzon-abc") is None and a.servers() == []
    assert not any(k.endswith(("-nic", "-ip")) for k in FakeAzure.res)


def test_azure_errors(az):
    FakeAzure.full = True
    with pytest.raises(PlacementError):
        az().create("hertzon-x", "Standard_D8as_v5", "germanywestcentral", "#cloud-config\n{}", "k", "x")
    assert not any(k.endswith(("hertzon-x-nic", "hertzon-x-ip")) for k in FakeAzure.res)     # cleaned up
    FakeAzure.no_group = True
    with pytest.raises(RemoteError, match="resource group 'hertzon' does not exist"):
        az().servers()


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs OpenSSH's ssh-keygen")
def test_azure_session(az, tmp_path):
    from hertzon.remote.cloud import CloudSession
    s = CloudSession(az(), "Standard_D8as_v5", "germanywestcentral", workdir=tmp_path, version="9.9.9")
    st = s._create(lambda text: None)
    assert s.tunnel.user == "hertzon" and st["ip"] == "20.0.0.7"
    ud = base64.b64decode(FakeAzure.vm_puts[0]["properties"]["osProfile"]["customData"]).decode()
    cfg = json.loads(ud.split("\n", 1)[1])
    assert cfg["packages"] == ["docker.io"] and cfg["ssh_keys"]["ed25519_public"] == st["host_key"]
    wcfg = json.loads({f["path"]: f["content"] for f in cfg["write_files"]}["/etc/hertzon/watchdog.json"])
    assert wcfg["provider"] == "azure" and json.loads(wcfg["api_token"])["client"] == "app-1"
    assert "azure" in watchdog.DELETE
    assert s.delete() and FakeAzure.res.get(
        "resourceGroups/hertzon/providers/Microsoft.Compute/virtualMachines/" + st["name"]) is None


def test_azure_settings():
    vals = {"cloud/provider": "azure", "cloud/azure_credentials": CRED}
    get = lambda k, d=None: vals.get(k, d)
    assert not providers.configured(get)                                   # no subscription yet
    vals["cloud/azure_subscription"] = SUB
    prov, stype, loc = providers.from_settings(get)
    assert providers.configured(get) and prov.key == "azure" and (stype, loc) == ("Standard_D8as_v5", "germanywestcentral")
