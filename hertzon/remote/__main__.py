"""python -m hertzon.remote serve | token | autostart on|off

    serve        run the remote service in this window (options: --port, --host, --cores, --sweep-workers,
                 --workdir)
    token        print the service token (creates it on first use)
    autostart    start the service with this user's login (Windows: Startup folder; Linux: a systemd user
                 service that you enable once)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import DEFAULT_PORT
from .server import default_workdir, load_token, serve

AUTOSTART_NAME = "Hertzon Remote Service"


def _startup_file() -> Path:
    if os.name == "nt":
        return (Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Microsoft" / "Windows" /
                "Start Menu" / "Programs" / "Startup" / f"{AUTOSTART_NAME}.cmd")
    return Path.home() / ".config" / "systemd" / "user" / "hertzon-remote.service"


def autostart(on: bool, args: list[str]) -> int:
    f = _startup_file()
    if not on:
        f.unlink(missing_ok=True)
        print(f"Removed {f}")
        if os.name != "nt":
            print("Also run: systemctl --user disable --now hertzon-remote")
        return 0
    f.parent.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    extra = " ".join(args)
    if os.name == "nt":
        f.write_text(f'@echo off\r\nstart "{AUTOSTART_NAME}" /min "{py}" -m hertzon.remote serve {extra}\r\n',
                     encoding="utf-8")
        print(f"The service now starts minimized at login ({f}).")
    else:
        root = Path(__file__).resolve().parents[2]
        f.write_text("[Unit]\nDescription=Hertzon remote service\nAfter=network-online.target\n\n"
                     f"[Service]\nWorkingDirectory={root}\nExecStart={py} -m hertzon.remote serve {extra}\n"
                     "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n", encoding="utf-8")
        print(f"Wrote {f}. Enable it once with:\n  systemctl --user daemon-reload\n"
              "  systemctl --user enable --now hertzon-remote\n"
              "  sudo loginctl enable-linger $USER     # keeps it running without a login")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hertzon-serve", description="Hertzon remote service")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the service")
    s.add_argument("--host", default="0.0.0.0", help="interface to listen on (default: all)")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--cores", type=int, default=0, help="cores per solve (default: all)")
    s.add_argument("--sweep-workers", type=int, default=2)
    s.add_argument("--workdir", type=Path, default=None, help=f"work folder (default: {default_workdir()})")
    t = sub.add_parser("token", help="print the token")
    t.add_argument("--workdir", type=Path, default=None)
    a = sub.add_parser("autostart", help="start the service at login")
    a.add_argument("state", choices=["on", "off"])
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0].startswith("-"):
        argv = ["serve"] + argv                       # hertzon-serve [options] = serve
    if argv[0] == "autostart":
        rest = argv[2:] if len(argv) > 2 else []
        ns = ap.parse_args(argv[:2])
        return autostart(ns.state == "on", rest)
    ns = ap.parse_args(argv)
    if ns.cmd == "token":
        print(load_token(ns.workdir or default_workdir()))
        return 0
    return serve(ns.host, ns.port, ns.cores, ns.sweep_workers, ns.workdir)


if __name__ == "__main__":
    sys.exit(main())
