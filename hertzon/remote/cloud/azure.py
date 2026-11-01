"""Microsoft Azure: create, find and delete the servers Hertzon solves on (Resource Manager REST API with a
service principal, standard library only).

Everything lives in the resource group "hertzon" of the subscription; the service principal has the role
Contributor on that resource group only (docs/REMOTE.md). Azure bills a stopped but not deallocated virtual
machine, so the server cannot remove itself by shutting down: like on Hetzner it gets the credentials of the
service principal, which can do nothing outside the resource group.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..client import RemoteError
from .hetzner import PlacementError

NAME = "Microsoft Azure"
GROUP = "hertzon"
LABEL = "hertzon"
DEFAULT_TYPE = "Standard_D8as_v5"       # 8 vCPUs (4 AMD cores), 32 GB
DEFAULT_LOCATION = "germanywestcentral"
ARM = "https://management.azure.com"
LOGIN = "https://login.microsoftonline.com"
PRICES = "https://prices.azure.com/api/retail/prices"
COMPUTE_API, NETWORK_API, RG_API, SUB_API = "2024-07-01", "2024-05-01", "2021-04-01", "2022-12-01"
IMAGE = {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": "latest"}
DISK_GB = 30
USER = "hertzon"

# sizes for solving: D (4 GB per vCPU), E (8 GB), F (2 GB) with AMD ("a") or Intel, and the HPC series HB
SIZES = re.compile(r"^Standard_(?:(?:D|E)(?:4|8|16|32|48|64|96)as_v[56]|F(?:4|8|16|32|48|64|72)s_v2|"
                   r"F(?:4|8|16|32|48|64)as_v6|HB\d+(?:-\d+)?rs_v[234])$")

PLACEMENT = ("SkuNotAvailable", "AllocationFailed", "ZonalAllocationFailed", "OverconstrainedAllocationRequest",
             "OverconstrainedZonalAllocationRequest")


def credentials_from(text: str) -> dict:
    """The service principal from TEXT: the JSON that `az ad sp create-for-rbac` prints (appId, password,
    tenant), or the path of a file holding it."""
    text = (text or "").strip()
    if text and not text.startswith("{"):
        try:
            text = Path(text).read_text(encoding="utf-8")
        except OSError as ex:
            raise RemoteError(f"Azure: the credentials file {text} cannot be read ({ex})") from None
    try:
        d = json.loads(text) if text else {}
    except ValueError:
        raise RemoteError("Azure: the credentials are not JSON with appId, password and tenant") from None
    cred = {"tenant": d.get("tenant") or d.get("tenantId", ""), "client": d.get("appId") or d.get("clientId", ""),
            "secret": d.get("password") or d.get("clientSecret", "")}
    if not all(cred.values()):
        raise RemoteError("Azure: the credentials need appId, password and tenant (as `az ad sp create-for-rbac` "
                          "prints them)")
    return cred


class Azure:
    name = NAME
    key = "azure"
    ssh_user = USER
    install_docker = True

    def __init__(self, credentials: str = "", subscription: str = "", location: str = DEFAULT_LOCATION,
                 arm: str = ARM, login: str = LOGIN, prices_url: str = PRICES):
        self.credentials, self.subscription = (credentials or "").strip(), (subscription or "").strip()
        self.location = (location or DEFAULT_LOCATION).strip()
        self.arm, self.login, self.prices_url = arm.rstrip("/"), login.rstrip("/"), prices_url
        self._cred: dict | None = None
        self._access: tuple[str, float] | None = None

    @property
    def cred(self) -> dict:
        if self._cred is None:
            self._cred = credentials_from(self.credentials)
        return self._cred

    @property
    def token(self) -> str:
        """What the server gets to delete itself: the service principal and the subscription (JSON)."""
        try:
            return json.dumps({**self.cred, "subscription": self.subscription, "group": GROUP})
        except RemoteError:
            return ""

    # ---- requests -----------------------------------------------------------------------------------
    def _bearer(self) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        c = self.cred
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": c["client"],
                                       "client_secret": c["secret"], "scope": f"{ARM}/.default"}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"{self.login}/{c['tenant']}/oauth2/v2.0/token", data=body), timeout=30) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as ex:
            raise RemoteError(f"Azure: the service principal was not accepted ({_message(ex.read()) or ex.reason})") from None
        except (urllib.error.URLError, OSError) as ex:
            raise RemoteError(f"Azure not reachable ({getattr(ex, 'reason', ex)})") from None
        self._access = (tok["access_token"], time.time() + float(tok.get("expires_in", 3600)))
        return self._access[0]

    def _call(self, method: str, path: str, api: str, body: dict | None = None) -> dict:
        if not self.subscription:
            raise RemoteError("Azure: no subscription ID is set (Tools > Options > Cloud)")
        sep = "&" if "?" in path else "?"
        url = f"{self.arm}/subscriptions/{self.subscription}/{path.lstrip('/')}{sep}api-version={api}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._bearer()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as ex:
            raise _translate(ex.read(), ex.code) from None
        except (urllib.error.URLError, OSError) as ex:
            raise RemoteError(f"Azure not reachable ({getattr(ex, 'reason', ex)})") from None
        return json.loads(raw) if raw else {}

    def _rg(self, path: str) -> str:
        return f"resourceGroups/{GROUP}/providers/{path}"

    def _put(self, path: str, api: str, body: dict, timeout: float = 180) -> dict:
        """PUT a resource and wait until it is provisioned (or failed)."""
        r = self._call("PUT", path, api, body)
        end = time.monotonic() + timeout
        while (r.get("properties") or {}).get("provisioningState") not in ("Succeeded", "Failed", None):
            if time.monotonic() > end:
                break
            time.sleep(2)
            r = self._call("GET", path, api)
        if (r.get("properties") or {}).get("provisioningState") == "Failed":
            raise RemoteError(f"Azure: creating {path.rsplit('/', 1)[-1]} failed")
        return r

    # ---- catalogue ----------------------------------------------------------------------------------
    def locations(self) -> list[dict]:
        out = []
        for loc in self._call("GET", "locations", SUB_API).get("value", []):
            meta = loc.get("metadata") or {}
            if meta.get("regionType", "Physical") != "Physical":
                continue
            out.append({"name": loc["name"], "city": loc.get("displayName", ""), "country": ""})
        return sorted(out, key=lambda x: x["name"])

    def server_types(self, location: str = "") -> list[dict]:
        loc = location or self.location
        sizes = [s for s in self._call("GET", f"providers/Microsoft.Compute/locations/{loc}/vmSizes",
                                       COMPUTE_API).get("value", []) if SIZES.match(s.get("name", ""))]
        prices = self.prices(loc, [s["name"] for s in sizes])
        out = []
        for s in sizes:
            vcpu = int(s.get("numberOfCores", 0))
            smt = not s["name"].startswith("Standard_HB")
            price = prices.get(s["name"])
            out.append({"name": s["name"], "cores": vcpu, "physical": vcpu // 2 if smt else vcpu,
                        "memory": round(s.get("memoryInMB", 0) / 1024, 1), "dedicated": True, "price": price,
                        "prices": {loc: price}, "locations": [loc], "currency": "EUR"})
        order = {"D": 0, "E": 1, "F": 2, "H": 3}
        return sorted(out, key=lambda t: (order.get(t["name"][9], 9), "as_" not in t["name"], t["cores"], t["name"]))

    def prices(self, location: str, sizes: list[str]) -> dict[str, float]:
        """Pay-as-you-go Linux prices per hour (EUR) of SIZES at LOCATION (public price list, no sign-in)."""
        out: dict[str, float] = {}
        for i in range(0, len(sizes), 15):
            names = " or ".join(f"armSkuName eq '{n}'" for n in sizes[i:i + 15])
            flt = (f"serviceName eq 'Virtual Machines' and armRegionName eq '{location}' and "
                   f"priceType eq 'Consumption' and ({names})")
            url = f"{self.prices_url}?currencyCode='EUR'&$filter={urllib.parse.quote(flt)}"
            try:
                while url:
                    with urllib.request.urlopen(url, timeout=30) as r:
                        page = json.loads(r.read())
                    for it in page.get("Items", []):
                        sku = it.get("skuName", "") + " " + it.get("productName", "")
                        if "Windows" in sku or "Spot" in sku or "Low Priority" in sku:
                            continue
                        out[it["armSkuName"]] = float(it.get("retailPrice") or it.get("unitPrice") or 0) or None
                    url = page.get("NextPageLink")
            except (urllib.error.URLError, OSError, ValueError):
                return {k: v for k, v in out.items() if v}
        return {k: v for k, v in out.items() if v}

    def locations_for(self, server_type: str) -> list[str]:
        return [self.location]

    # ---- servers ------------------------------------------------------------------------------------
    def ssh_key(self, public: str, client: str) -> str:
        return public.strip()            # Azure requires a key for the admin user

    def _network(self, loc: str) -> str:
        """The subnet (and its SSH rule) of LOCATION, created once; returns the subnet's id."""
        nsg = self._put(self._rg(f"Microsoft.Network/networkSecurityGroups/hertzon-ssh-{loc}"), NETWORK_API, {
            "location": loc, "tags": {LABEL: "1"}, "properties": {"securityRules": [{
                "name": "ssh", "properties": {"priority": 100, "direction": "Inbound", "access": "Allow",
                                              "protocol": "Tcp", "sourceAddressPrefix": "*", "sourcePortRange": "*",
                                              "destinationAddressPrefix": "*", "destinationPortRange": "22"}}]}})
        vnet = self._put(self._rg(f"Microsoft.Network/virtualNetworks/hertzon-{loc}"), NETWORK_API, {
            "location": loc, "tags": {LABEL: "1"}, "properties": {
                "addressSpace": {"addressPrefixes": ["10.47.0.0/16"]},
                "subnets": [{"name": "default", "properties": {"addressPrefix": "10.47.0.0/24",
                                                               "networkSecurityGroup": {"id": nsg["id"]}}}]}})
        return vnet["properties"]["subnets"][0]["id"]

    def create(self, name: str, server_type: str, location: str, user_data: str, ssh_key, client: str) -> dict:
        loc = location or self.location
        tags = {LABEL: "1", "hertzon-client": client[:256]}
        subnet = self._network(loc)
        pip = self._put(self._rg(f"Microsoft.Network/publicIPAddresses/{name}-ip"), NETWORK_API, {
            "location": loc, "tags": tags, "sku": {"name": "Standard"},
            "properties": {"publicIPAllocationMethod": "Static"}})
        nic = self._put(self._rg(f"Microsoft.Network/networkInterfaces/{name}-nic"), NETWORK_API, {
            "location": loc, "tags": tags, "properties": {"ipConfigurations": [{"name": "ip", "properties": {
                "subnet": {"id": subnet},
                "publicIPAddress": {"id": pip["id"], "properties": {"deleteOption": "Delete"}}}}]}})
        body = {"location": loc, "tags": tags, "properties": {
            "hardwareProfile": {"vmSize": server_type},
            "storageProfile": {"imageReference": IMAGE, "osDisk": {
                "createOption": "FromImage", "deleteOption": "Delete", "diskSizeGB": DISK_GB,
                "managedDisk": {"storageAccountType": "StandardSSD_LRS"}}},
            "osProfile": {"computerName": name, "adminUsername": USER,
                          "customData": base64.b64encode(user_data.encode("utf-8")).decode("ascii"),
                          "linuxConfiguration": {"disablePasswordAuthentication": True, "ssh": {"publicKeys": [
                              {"path": f"/home/{USER}/.ssh/authorized_keys", "keyData": ssh_key}]}}},
            "networkProfile": {"networkInterfaces": [{"id": nic["id"], "properties": {
                "primary": True, "deleteOption": "Delete"}}]}}}
        path = self._rg(f"Microsoft.Compute/virtualMachines/{name}")
        try:
            self._call("PUT", path, COMPUTE_API, body)
        except RemoteError:
            self._cleanup(name)
            raise
        return {"id": name, "name": name, "status": "initializing", "ip": pip["properties"].get("ipAddress", ""),
                "type": server_type, "created": "", "client": client}

    def _cleanup(self, name: str) -> None:
        """Remove the network parts of a server that could not be created."""
        for p in (f"Microsoft.Network/networkInterfaces/{name}-nic", f"Microsoft.Network/publicIPAddresses/{name}-ip"):
            try:
                self._call("DELETE", self._rg(p), NETWORK_API)
            except (RemoteError, LookupError):
                pass

    def get(self, sid) -> dict | None:
        try:
            vm = self._call("GET", self._rg(f"Microsoft.Compute/virtualMachines/{sid}?$expand=instanceView"),
                            COMPUTE_API)
        except LookupError:
            return None
        s = self._server(vm)
        if not s["ip"]:
            try:
                pip = self._call("GET", self._rg(f"Microsoft.Network/publicIPAddresses/{sid}-ip"), NETWORK_API)
                s["ip"] = (pip.get("properties") or {}).get("ipAddress", "")
            except (RemoteError, LookupError):
                pass
        return s

    def delete(self, sid) -> None:
        try:
            self._call("DELETE", self._rg(f"Microsoft.Compute/virtualMachines/{sid}?forceDeletion=true"), COMPUTE_API)
        except LookupError:
            self._cleanup(str(sid))      # no machine (any more): its network parts, if left

    def servers(self) -> list[dict]:
        try:
            vms = self._call("GET", self._rg("Microsoft.Compute/virtualMachines"), COMPUTE_API).get("value", [])
        except LookupError:
            return []
        return [self._server(v) for v in vms if (v.get("tags") or {}).get(LABEL) == "1"]

    @staticmethod
    def _server(vm: dict) -> dict:
        p = vm.get("properties") or {}
        prov = p.get("provisioningState", "")
        power = next((s.get("code", "") for s in (p.get("instanceView") or {}).get("statuses", [])
                      if s.get("code", "").startswith("PowerState/")), "")
        if prov == "Deleting":
            status = "deleting"
        elif prov == "Failed" or power in ("PowerState/stopped", "PowerState/deallocated", "PowerState/deallocating"):
            status = "off"
        elif power == "PowerState/running" and prov == "Succeeded":
            status = "running"
        else:
            status = "initializing"
        return {"id": vm["name"], "name": vm["name"], "status": status, "ip": "",
                "type": (p.get("hardwareProfile") or {}).get("vmSize", ""), "created": p.get("timeCreated", ""),
                "client": (vm.get("tags") or {}).get("hertzon-client", "")}


def _message(raw: bytes) -> str:
    try:
        d = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")[:200]
    e = d.get("error")
    if isinstance(e, dict):
        return e.get("message", "")
    return d.get("error_description") or str(e or "")


def _translate(raw: bytes, http: int) -> Exception:
    try:
        err = json.loads(raw).get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    code, msg = err.get("code", ""), err.get("message", "") or raw.decode("utf-8", "replace")[:200]
    detail = " ".join(d.get("code", "") for d in err.get("details") or [])
    if http == 404 and code in ("ResourceNotFound", "NotFound", "ResourceGroupNotFound", ""):
        if code == "ResourceGroupNotFound":
            return RemoteError("Azure: the resource group 'hertzon' does not exist; create it in the portal "
                               "(see the Hertzon documentation, docs/REMOTE.md)")
        return LookupError(msg)
    if code == "ResourceGroupNotFound":
        return RemoteError("Azure: the resource group 'hertzon' does not exist; create it in the portal "
                           "(see the Hertzon documentation, docs/REMOTE.md)")
    if code in ("AuthorizationFailed", "LinkedAuthorizationFailed"):
        return RemoteError(f"Azure: the service principal may not do this ({msg}); it needs the role Contributor "
                           "on the resource group 'hertzon' (docs/REMOTE.md)")
    if code == "MissingSubscriptionRegistration":
        return RemoteError(f"Azure: {msg} Register it in the portal: Subscriptions > Resource providers")
    if code in PLACEMENT or any(c in detail for c in PLACEMENT):
        return PlacementError(f"Azure: {msg}")
    if code in ("OperationNotAllowed", "QuotaExceeded") and "quota" in msg.lower():
        return RemoteError(f"Azure: {msg} Request more in the portal: Subscriptions > Usage + quotas")
    if code in ("InvalidAuthenticationToken", "ExpiredAuthenticationToken", "InvalidAuthenticationTokenTenant"):
        return RemoteError("Azure: the service principal does not belong to this subscription's directory")
    if code == "SubscriptionNotFound":
        return RemoteError("Azure: the subscription ID is wrong or the service principal cannot see it")
    return RemoteError(f"Azure: {msg or code} (HTTP {http})")
