"""Hetzner Cloud: create, find and delete the servers Hertzon solves on (REST API, standard library only).

The API token comes from the Hetzner Cloud Console: a project > Security > API tokens, with Read & Write.
A project used only for Hertzon is recommended: the token is also given to the server so it can delete
itself (watchdog.py), and it can do nothing outside its project.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..client import RemoteError

API = "https://api.hetzner.cloud/v1"
NAME = "Hetzner Cloud"
LABEL = "hertzon"                       # every server and SSH key Hertzon creates carries the label hertzon=1
DEFAULT_TYPE = "ccx33"                  # 8 dedicated vCPUs, 32 GB
DEFAULT_LOCATION = "fsn1"
IMAGE = "docker-ce"                     # Hetzner's Ubuntu image with Docker installed


class Hetzner:
    name = NAME
    key = "hetzner"

    def __init__(self, token: str, api: str = API):
        self.token, self.api = (token or "").strip(), api.rstrip("/")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self.token:
            raise RemoteError("No Hetzner API token is set (Tools > Options > Remote > Cloud)")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as ex:
            try:
                err = json.loads(ex.read().decode("utf-8")).get("error", {})
                msg = err.get("message") or err.get("code") or ex.reason
            except Exception:  # noqa: BLE001
                msg = ex.reason
            if ex.code == 401:
                raise RemoteError("Hetzner Cloud: the API token is wrong or was removed") from None
            if ex.code == 404:
                raise LookupError(msg) from None
            raise RemoteError(f"Hetzner Cloud: {msg} (HTTP {ex.code})") from None
        except (urllib.error.URLError, OSError) as ex:
            raise RemoteError(f"Hetzner Cloud not reachable ({getattr(ex, 'reason', ex)})") from None
        return json.loads(raw.decode("utf-8")) if raw else {}

    # ---- catalogue ----------------------------------------------------------------------------------
    def server_types(self, location: str = "") -> list[dict]:
        """Server types: name, cores, memory (GB), dedicated, price per hour (EUR incl. VAT) at LOCATION."""
        out = []
        for t in self._call("GET", "/server_types?per_page=50").get("server_types", []):
            if t.get("deprecated") or t.get("architecture", "x86") != "x86":
                continue
            prices = {p["location"]: float(p["price_hourly"]["gross"]) for p in t.get("prices", [])}
            if location and location not in prices:
                continue
            price = prices.get(location) if location else (min(prices.values()) if prices else None)
            out.append({"name": t["name"], "cores": t.get("cores"), "memory": t.get("memory"),
                        "dedicated": t.get("cpu_type") == "dedicated", "price": price, "prices": prices,
                        "locations": sorted(prices)})
        return sorted(out, key=lambda t: (not t["dedicated"], t["cores"] or 0, t["name"]))

    def locations(self) -> list[dict]:
        return [{"name": loc["name"], "city": loc.get("city", ""), "country": loc.get("country", "")}
                for loc in self._call("GET", "/locations").get("locations", [])]

    # ---- servers ------------------------------------------------------------------------------------
    def ssh_key(self, public: str, client: str) -> int:
        """The id of PUBLIC as a project SSH key (added once). Without a key, Hetzner mails a root password."""
        public = public.strip()
        for k in self._call("GET", f"/ssh_keys?label_selector={LABEL}&per_page=50").get("ssh_keys", []):
            if k.get("public_key", "").split()[:2] == public.split()[:2]:
                return int(k["id"])
        name = f"hertzon-{client}"[:60]
        try:
            return int(self._call("POST", "/ssh_keys", {"name": name, "public_key": public,
                                                         "labels": {LABEL: "1"}})["ssh_key"]["id"])
        except RemoteError as ex:              # the same key (or name) exists without the label
            if "uniqueness" not in str(ex) and "not unique" not in str(ex):
                raise
            for k in self._call("GET", "/ssh_keys?per_page=50").get("ssh_keys", []):
                if k.get("public_key", "").split()[:2] == public.split()[:2]:
                    return int(k["id"])
            name = f"{name}-{abs(hash(public)) % 10000}"
            return int(self._call("POST", "/ssh_keys", {"name": name, "public_key": public,
                                                         "labels": {LABEL: "1"}})["ssh_key"]["id"])

    def create(self, name: str, server_type: str, location: str, user_data: str, ssh_key: int,
               client: str) -> dict:
        body = {"name": name, "server_type": server_type, "location": location, "image": IMAGE,
                "user_data": user_data, "ssh_keys": [ssh_key], "start_after_create": True,
                "labels": {LABEL: "1", "hertzon-client": _label(client)},
                "public_net": {"enable_ipv4": True, "enable_ipv6": True}}
        return self._server(self._call("POST", "/servers", body)["server"])

    def get(self, sid) -> dict | None:
        try:
            return self._server(self._call("GET", f"/servers/{sid}")["server"])
        except LookupError:
            return None

    def delete(self, sid) -> None:
        try:
            self._call("DELETE", f"/servers/{sid}")
        except LookupError:
            pass                                # already gone

    def servers(self) -> list[dict]:
        """All servers Hertzon created in this project (of any computer)."""
        return [self._server(s) for s in
                self._call("GET", f"/servers?label_selector={LABEL}&per_page=50").get("servers", [])]

    @staticmethod
    def _server(s: dict) -> dict:
        ip = ((s.get("public_net") or {}).get("ipv4") or {}).get("ip", "")
        return {"id": s["id"], "name": s.get("name", ""), "status": s.get("status", ""), "ip": ip,
                "type": (s.get("server_type") or {}).get("name", ""), "created": s.get("created", ""),
                "client": (s.get("labels") or {}).get("hertzon-client", "")}


def _label(text: str) -> str:
    """A Hetzner label value: letters, digits, '-', '_', '.', at most 63 characters."""
    v = "".join(c if c.isalnum() or c in "-_." else "-" for c in text)[:63].strip("-_.")
    return v or "unknown"
