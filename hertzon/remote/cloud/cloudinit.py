"""The first-boot configuration (cloud-init user data) of a cloud server that solves for Hertzon.

The server gets a host key the GUI generated (so the GUI knows the server's key before the first SSH
connection and nobody can step in between), the watchdog that deletes it when it is no longer needed, and
starts the solver image with the remote service, reachable only through the SSH tunnel of the GUI.
cloud-init reads JSON as well as YAML, so the configuration is written as JSON (no quoting pitfalls).
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

IMAGE = "ghcr.io/c8501024/hertzon-solver"
PORT = 47820

WATCHDOG_UNIT = """[Unit]
Description=Hertzon watchdog: deletes this server when it is no longer needed
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /etc/hertzon/watchdog.py /etc/hertzon/watchdog.json
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"""


def image_for(version: str) -> tuple[str, str]:
    """The solver image of this Hertzon VERSION and the one to use when it was not published (yet)."""
    return f"{IMAGE}:{version}", f"{IMAGE}:dev"


def start_script(image: str, fallback: str, token: str, args: list[str] | None = None) -> str:
    q = shlex.quote
    cmd = " ".join(q(a) for a in (args or []))
    return f"""#!/bin/sh
# pull the solver image of this Hertzon version (or the development one) and start the remote service
IMG={q(image)}
docker pull "$IMG" || {{ IMG={q(fallback)}; docker pull "$IMG"; }}
docker rm -f hertzon 2>/dev/null
exec docker run -d --name hertzon --hostname "$(hostname)" --restart unless-stopped -p 127.0.0.1:{PORT}:{PORT} \\
    -e HERTZON_TOKEN={q(token)} -v /var/lib/hertzon:/work "$IMG" {cmd}
"""


def user_data(*, provider: str, token: str, api_token: str, host_private: str, host_public: str,
              client_public: str, image: str, fallback: str, idle_min: float = 30, contact_h: float = 48,
              quiet_h: float = 2, start_min: float = 20, args: list[str] | None = None,
              install_docker: bool = False) -> str:
    watchdog = (Path(__file__).with_name("watchdog.py")).read_text(encoding="utf-8")
    wcfg = {"provider": provider, "token": token, "api_token": api_token, "port": PORT, "idle_min": idle_min,
            "contact_h": contact_h, "quiet_h": quiet_h, "start_min": start_min}
    cfg = {
        "ssh_deletekeys": True,
        "ssh_genkeytypes": [],
        "ssh_keys": {"ed25519_private": host_private.strip() + "\n", "ed25519_public": host_public.strip()},
        "ssh_authorized_keys": [client_public.strip()],
        "disable_root": False,
        "ssh_pwauth": False,
        "write_files": [
            {"path": "/etc/hertzon/watchdog.py", "permissions": "0700", "content": watchdog},
            {"path": "/etc/hertzon/watchdog.json", "permissions": "0600", "content": json.dumps(wcfg)},
            {"path": "/etc/systemd/system/hertzon-watchdog.service", "permissions": "0644",
             "content": WATCHDOG_UNIT},
            {"path": "/etc/hertzon/start.sh", "permissions": "0700",
             "content": start_script(image, fallback, token, args)},
        ],
        # the watchdog first: it also removes a server whose service never comes up
        "runcmd": [["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", "hertzon-watchdog"],
                   ["/etc/hertzon/start.sh"]],
    }
    if install_docker:                  # a plain Ubuntu image: Docker from its packages (before runcmd)
        cfg["package_update"] = True
        cfg["packages"] = ["docker.io"]
    return "#cloud-config\n" + json.dumps(cfg, indent=1) + "\n"
