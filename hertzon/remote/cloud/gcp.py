"""Google Cloud (Compute Engine): create, find and delete the servers Hertzon solves on (REST API with a
service account key, standard library only; the key's RS256 signature is computed here).

The service account needs the roles "Compute Instance Admin (v1)" and "Service Account User" in a project only
for Hertzon (docs/REMOTE.md). The key stays on this computer: the server deletes itself with the token of the
project's default compute service account, which Google gives it through its metadata server.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..client import RemoteError
from .hetzner import PlacementError

NAME = "Google Cloud"
LABEL = "hertzon"
DEFAULT_TYPE = "c3d-standard-8"         # 8 vCPUs (4 AMD cores), 32 GB
DEFAULT_ZONE = "europe-west3-a"         # Frankfurt
IMAGE = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
DISK_GB = 30
FIREWALL = "hertzon-ssh"
SCOPE = "https://www.googleapis.com/auth/compute"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://compute.googleapis.com/compute/v1"

# x86 types for solving: c3d/c2d compute (AMD), n2d general (AMD), c3 compute (Intel); 2 vCPUs per core
FAMILIES = ("c3d-standard", "c3d-highmem", "c2d-standard", "c2d-highmem", "n2d-standard", "n2d-highmem",
            "c3-standard", "c3-highmem")
SIZES = (4, 8, 16, 30, 32, 60, 64, 90, 112, 180)

CITIES = {"europe-west1": "Belgium", "europe-west2": "London", "europe-west3": "Frankfurt",
          "europe-west4": "Netherlands", "europe-west6": "Zurich", "europe-west8": "Milan", "europe-west9": "Paris",
          "europe-west10": "Berlin", "europe-west12": "Turin", "europe-north1": "Finland",
          "europe-central2": "Warsaw", "europe-southwest1": "Madrid", "us-central1": "Iowa",
          "us-east1": "South Carolina", "us-east4": "N. Virginia", "us-east5": "Columbus", "us-west1": "Oregon",
          "us-west2": "Los Angeles", "us-west3": "Salt Lake City", "us-west4": "Las Vegas",
          "us-south1": "Dallas", "northamerica-northeast1": "Montreal", "northamerica-northeast2": "Toronto",
          "asia-east1": "Taiwan", "asia-northeast1": "Tokyo", "asia-south1": "Mumbai",
          "asia-southeast1": "Singapore", "australia-southeast1": "Sydney", "southamerica-east1": "Sao Paulo"}

# ---- RS256 (RSASSA-PKCS1-v1_5 with SHA-256) for the service account's PEM key ------------------------


def _der(data: bytes, i: int = 0) -> tuple[int, bytes, int]:
    """(tag, content, next index) of the DER element at I."""
    tag, ln = data[i], data[i + 1]
    i += 2
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(data[i:i + n], "big")
        i += n
    return tag, data[i:i + ln], i + ln


def _seq(data: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 0
    while i < len(data):
        tag, content, i = _der(data, i)
        out.append((tag, content))
    return out


def rsa_key(pem: str) -> tuple[int, int, int, int, int, int, int]:
    """(n, e, p, q, dp, dq, qinv) of an RSA private key in PEM (PKCS#8, as in Google's key files, or PKCS#1)."""
    body = "".join(ln for ln in pem.strip().splitlines() if not ln.startswith("-----"))
    der = base64.b64decode(body)
    _tag, content, _ = _der(der)
    items = _seq(content)
    if len(items) == 3 and items[1][0] == 0x30:                 # PKCS#8: version, algorithm, OCTET STRING
        items = _seq(_der(items[2][1])[1])
    ints = [int.from_bytes(c, "big") for t, c in items if t == 0x02]
    _, n, e, _d, p, q, dp, dq, qinv = ints[:9]
    return n, e, p, q, dp, dq, qinv


_SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def rs256(pem: str, message: bytes) -> bytes:
    n, e, p, q, dp, dq, qinv = rsa_key(pem)
    k = (n.bit_length() + 7) // 8
    t = _SHA256_INFO + hashlib.sha256(message).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    m = int.from_bytes(em, "big")
    m1, m2 = pow(m, dp, p), pow(m, dq, q)                       # Chinese remainder theorem
    s = m2 + q * ((qinv * (m1 - m2)) % p)
    if pow(s, e, n) != m:
        raise RemoteError("Google Cloud: the service account key is damaged")
    return s.to_bytes(k, "big")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _label(text: str) -> str:
    """A Google label value: lower case letters, digits, '-' and '_', at most 63 characters."""
    v = "".join(c if c.isalnum() or c in "-_" else "-" for c in text.lower())[:63].strip("-_")
    return v or "unknown"


class AccessDenied(RemoteError):
    """The service account lacks a permission."""


class GCP:
    name = NAME
    key = "gcp"
    ssh_user = "ubuntu"
    install_docker = True
    own_host_key = False                # Google's guest agent makes the host keys; they are read from the API

    def __init__(self, key_file: str = "", zone: str = DEFAULT_ZONE, info: dict | None = None, api: str = API,
                 token_url: str = TOKEN_URL):
        self.key_file = (key_file or "").strip()
        self.zone = (zone or DEFAULT_ZONE).strip()
        self._info = info
        self.api, self.token_url = api.rstrip("/"), token_url
        self.token = ""                 # nothing for the server: it deletes itself with its own account
        self._access: tuple[str, float] | None = None

    @property
    def info(self) -> dict:
        if self._info is None:
            if not self.key_file:
                raise RemoteError("No Google Cloud service account key is set (Tools > Options > Cloud)")
            try:
                self._info = json.loads(Path(self.key_file).read_text(encoding="utf-8"))
            except (OSError, ValueError) as ex:
                raise RemoteError(f"Google Cloud: the key file {self.key_file} cannot be read ({ex})") from None
            if self._info.get("type") != "service_account" or "private_key" not in self._info:
                raise RemoteError("Google Cloud: the key file is not a service account key (JSON)")
        return self._info

    @property
    def project(self) -> str:
        return self.info["project_id"]

    # ---- requests -----------------------------------------------------------------------------------
    def _token(self) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        info, now = self.info, int(time.time())
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": info.get("private_key_id", "")}).encode())
        claim = _b64(json.dumps({"iss": info["client_email"], "scope": SCOPE, "aud": self.token_url,
                                 "iat": now, "exp": now + 3600}).encode())
        jwt = f"{head}.{claim}.{_b64(rs256(info['private_key'], f'{head}.{claim}'.encode()))}"
        body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": jwt}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.token_url, data=body), timeout=30) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as ex:
            raise RemoteError("Google Cloud: the service account key was not accepted "
                              f"({_message(ex.read()) or ex.reason})") from None
        except (urllib.error.URLError, OSError) as ex:
            raise RemoteError(f"Google Cloud not reachable ({getattr(ex, 'reason', ex)})") from None
        self._access = (tok["access_token"], time.time() + float(tok.get("expires_in", 3600)))
        return self._access[0]

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{self.api}/projects/{self.project}/{path.lstrip('/')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as ex:
            raw = ex.read()
            msg, reason = _message(raw), _reason(raw)
            if ex.code == 404:
                raise LookupError(msg) from None
            if ex.code in (401, 403):
                if "has not been used" in msg or "is disabled" in msg:
                    raise RemoteError("Google Cloud: the Compute Engine API is not enabled for the project; "
                                      "enable it in the console (APIs & Services > Compute Engine API)") from None
                raise AccessDenied(f"Google Cloud: the service account may not do this ({msg}); see the roles "
                                  "in the Hertzon documentation (docs/REMOTE.md)") from None
            if reason in ("ZONE_RESOURCE_POOL_EXHAUSTED", "resourceExhausted") or "does not have enough resources" in msg:
                raise PlacementError(f"Google Cloud: {msg}") from None
            if "QUOTA" in msg.upper():
                raise RemoteError(f"Google Cloud: {msg} (raise the quota in the console: IAM & Admin > Quotas)") from None
            raise RemoteError(f"Google Cloud: {msg or ex.reason} (HTTP {ex.code})") from None
        except (urllib.error.URLError, OSError) as ex:
            raise RemoteError(f"Google Cloud not reachable ({getattr(ex, 'reason', ex)})") from None
        return json.loads(raw) if raw else {}

    def _wait(self, op: dict, timeout: float = 120) -> None:
        """Wait for a zone operation; its error (for example no capacity in the zone) is raised."""
        end = time.monotonic() + timeout
        while op.get("status") != "DONE" and time.monotonic() < end:
            time.sleep(1)
            op = self._call("GET", op["selfLink"])
        errs = (op.get("error") or {}).get("errors") or []
        if errs:
            code, msg = errs[0].get("code", ""), errs[0].get("message", "")
            if code in ("ZONE_RESOURCE_POOL_EXHAUSTED", "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS",
                        "RESOURCE_AVAILABILITY_ERROR") or "does not have enough resources" in msg:
                raise PlacementError(f"Google Cloud: {msg}")
            if "QUOTA" in code:
                raise RemoteError(f"Google Cloud: {msg} (raise the quota in the console: IAM & Admin > Quotas)")
            raise RemoteError(f"Google Cloud: {msg or code}")

    # ---- catalogue ----------------------------------------------------------------------------------
    def locations(self) -> list[dict]:
        out = []
        for z in self._call("GET", "zones?maxResults=500").get("items", []):
            if z.get("status") != "UP":
                continue
            region = z["name"].rsplit("-", 1)[0]
            out.append({"name": z["name"], "city": CITIES.get(region, ""), "country": ""})
        return sorted(out, key=lambda x: x["name"])

    def server_types(self, location: str = "") -> list[dict]:
        """The machine types of FAMILIES in the zone: vCPUs, cores, memory (GB). Without prices (Google's
        price list is not part of the Compute Engine API)."""
        zone = location or self.zone
        want = {f"{f}-{s}" for f in FAMILIES for s in SIZES}
        out = []
        for t in self._call("GET", f"zones/{zone}/machineTypes?maxResults=1000").get("items", []):
            if t["name"] not in want or t.get("deprecated"):
                continue
            vcpu = int(t.get("guestCpus", 0))
            out.append({"name": t["name"], "cores": vcpu, "physical": max(1, vcpu // 2),
                        "memory": round(t.get("memoryMb", 0) / 1024, 1), "dedicated": True, "price": None,
                        "prices": {zone: None}, "locations": [zone], "currency": "USD"})
        return sorted(out, key=lambda t: (FAMILIES.index(t["name"].rsplit("-", 1)[0]), t["cores"]))

    def locations_for(self, server_type: str) -> list[str]:
        """The other zones of the region (tried when the zone has no capacity)."""
        region = self.zone.rsplit("-", 1)[0]
        try:
            return [z["name"] for z in self.locations() if z["name"].startswith(region + "-")]
        except RemoteError:
            return []

    # ---- servers ------------------------------------------------------------------------------------
    def ssh_key(self, public: str, client: str) -> str:
        return public.strip()            # goes into the instance metadata (ssh-keys) as well

    def _firewall(self) -> None:
        """The rule hertzon-ssh, created once. The roles of docs/REMOTE.md may not read or create firewall
        rules; then the rule default-allow-ssh of the project's default network lets SSH in."""
        try:
            self._call("GET", f"global/firewalls/{FIREWALL}")
            return
        except LookupError:
            pass
        except AccessDenied:
            return
        try:
            self._wait(self._call("POST", "global/firewalls", {
                "name": FIREWALL, "network": "global/networks/default", "direction": "INGRESS",
                "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}], "sourceRanges": ["0.0.0.0/0"],
                "targetTags": [LABEL], "description": "Hertzon: SSH to the solver"}))
        except AccessDenied:
            pass

    def create(self, name: str, server_type: str, location: str, user_data: str, ssh_key, client: str) -> dict:
        zone = location or self.zone
        self._firewall()
        body = {
            "name": name, "machineType": f"zones/{zone}/machineTypes/{server_type}",
            "labels": {LABEL: "1", "hertzon-client": _label(client)}, "tags": {"items": [LABEL]},
            "disks": [{"boot": True, "autoDelete": True, "initializeParams": {
                "sourceImage": IMAGE, "diskSizeGb": str(DISK_GB), "diskType": f"zones/{zone}/diskTypes/pd-balanced"}}],
            "networkInterfaces": [{"network": "global/networks/default",
                                   "accessConfigs": [{"type": "ONE_TO_ONE_NAT", "name": "External NAT"}]}],
            "metadata": {"items": [{"key": "user-data", "value": user_data},
                                   {"key": "ssh-keys", "value": f"{self.ssh_user}:{ssh_key}"} if ssh_key else None,
                                   {"key": "enable-guest-attributes", "value": "TRUE"},
                                   {"key": "enable-oslogin", "value": "FALSE"}]},
            "serviceAccounts": [{"email": "default", "scopes": [SCOPE]}],
            "scheduling": {"automaticRestart": True, "onHostMaintenance": "MIGRATE"}}
        body["metadata"]["items"] = [i for i in body["metadata"]["items"] if i]
        self._wait(self._call("POST", f"zones/{zone}/instances", body))
        return self.get(f"{zone}/{name}") or {"id": f"{zone}/{name}", "name": name, "status": "initializing",
                                                 "ip": "", "type": server_type, "created": "", "client": client}

    def get(self, sid) -> dict | None:
        zone, name = str(sid).split("/", 1)
        try:
            return self._server(self._call("GET", f"zones/{zone}/instances/{name}"))
        except LookupError:
            return None

    def host_key(self, sid) -> str:
        """The server's ed25519 host key as Google's guest agent published it ('' until it did)."""
        zone, name = str(sid).split("/", 1)
        try:
            r = self._call("GET", f"zones/{zone}/instances/{name}/getGuestAttributes?queryPath=hostkeys/")
        except LookupError:
            return ""
        for item in (r.get("queryValue") or {}).get("items", []):
            if item.get("key") == "ssh-ed25519":
                parts = item.get("value", "").split()
                b64 = parts[1] if parts and parts[0].startswith("ssh-") and len(parts) > 1 else (parts or [""])[0]
                return f"ssh-ed25519 {b64}" if b64 else ""
        return ""

    def delete(self, sid) -> None:
        zone, name = str(sid).split("/", 1)
        try:
            self._call("DELETE", f"zones/{zone}/instances/{name}")
        except LookupError:
            pass

    def servers(self) -> list[dict]:
        r = self._call("GET", f"aggregated/instances?filter={urllib.parse.quote(f'labels.{LABEL}=1')}")
        out = []
        for scope in (r.get("items") or {}).values():
            out += [self._server(i) for i in scope.get("instances", [])]
        return out

    @staticmethod
    def _server(i: dict) -> dict:
        zone = i.get("zone", "").rsplit("/", 1)[-1]
        nat = [a.get("natIP", "") for n in i.get("networkInterfaces", []) for a in n.get("accessConfigs", [])]
        status = {"PROVISIONING": "initializing", "STAGING": "initializing", "RUNNING": "running"}.get(
            i.get("status", ""), "off")
        return {"id": f"{zone}/{i['name']}", "name": i["name"], "status": status, "ip": next((x for x in nat if x), ""),
                "type": i.get("machineType", "").rsplit("/", 1)[-1], "created": i.get("creationTimestamp", ""),
                "client": (i.get("labels") or {}).get("hertzon-client", "")}


def _message(raw: bytes) -> str:
    try:
        d = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")[:200]
    e = d.get("error")
    if isinstance(e, dict):
        return e.get("message", "")
    return d.get("error_description") or str(e or "")


def _reason(raw: bytes) -> str:
    try:
        errs = json.loads(raw).get("error", {}).get("errors") or [{}]
        return errs[0].get("reason", "")
    except (ValueError, AttributeError):
        return ""
