"""Watchdog of a cloud computer: removes the server when nobody needs it any more.

Runs on the cloud server itself (outside the solver container, Python standard library only; cloud-init
installs this file as /etc/hertzon/watchdog.py with its settings in /etc/hertzon/watchdog.json). Every minute
it asks the remote service for its activity and deletes the server (through the provider's API, or on AWS
by shutting down an instance that terminates on shutdown) when

- no analysis ran or waited and no GUI asked anything for IDLE_MIN minutes,
- an analysis runs but no GUI asked anything for CONTACT_H hours (the GUI is gone: cost limit),
- the running solver printed nothing for QUIET_H hours (hung),
- the service did not answer for START_MIN minutes (image not pulled, service crashed).

The GUI removes its server itself (when Hertzon closes, or with Delete Cloud Servers); this is the safety
net for a GUI that crashed or lost its network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONFIG = "/etc/hertzon/watchdog.json"


def decide(info: dict | None, cfg: dict, unreachable: float) -> str:
    """The reason to delete the server now, or ''. INFO: the service's /api/info (None: no answer);
    UNREACHABLE: seconds the service has not answered."""
    if info is None:
        return "the remote service did not answer" if unreachable > cfg.get("start_min", 20) * 60 else ""
    busy = bool(info.get("busy")) or int(info.get("queued", 0) or 0) > 0
    if not busy and float(info.get("idle", 0)) > cfg.get("idle_min", 30) * 60:
        return "idle"
    if busy and float(info.get("contact", 0)) > cfg.get("contact_h", 48) * 3600:
        return "no Hertzon GUI connected (cost limit)"
    if busy and float(info.get("quiet", 0)) > cfg.get("quiet_h", 2) * 3600:
        return "the solver made no progress (hung)"
    return ""


def _info(cfg: dict) -> dict | None:
    req = urllib.request.Request(f"http://127.0.0.1:{cfg.get('port', 47820)}/api/info",
                                 headers={"Authorization": f"Bearer {cfg['token']}", "X-Hertzon-Watchdog": "1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _hetzner_delete(cfg: dict) -> None:
    with urllib.request.urlopen("http://169.254.169.254/hetzner/v1/metadata/instance-id", timeout=10) as r:
        sid = r.read().decode().strip()
    req = urllib.request.Request(f"https://api.hetzner.cloud/v1/servers/{sid}", method="DELETE",
                                 headers={"Authorization": f"Bearer {cfg['api_token']}"})
    urllib.request.urlopen(req, timeout=30).close()


def _shutdown(cfg: dict) -> None:
    """AWS: the instance was started with "terminate on shutdown"; no API key on the server."""
    if subprocess.call(["shutdown", "-h", "now"]) != 0:
        raise OSError("shutdown failed")


DELETE = {"hetzner": _hetzner_delete, "aws": _shutdown}


def delete(cfg: dict, reason: str) -> None:
    print(f"hertzon watchdog: deleting this server: {reason}", flush=True)
    for _ in range(5):
        try:
            DELETE[cfg["provider"]](cfg)
            return
        except Exception as ex:  # noqa: BLE001
            print(f"hertzon watchdog: delete failed: {ex}", flush=True)
            time.sleep(60)
    subprocess.call(["shutdown", "-h", "now"])    # at least stop the computer (and the solver)


def main() -> int:
    with open(sys.argv[1] if len(sys.argv) > 1 else CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    last_ok = time.time()
    while True:
        info = _info(cfg)
        if info is not None:
            last_ok = time.time()
        reason = decide(info, cfg, time.time() - last_ok)
        if reason:
            delete(cfg, reason)
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
