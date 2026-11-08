"""Link between the MCP server and a running Hertzon window.

The window (Tools > AI Assistants) listens on 127.0.0.1 and writes its port and a random token to
``~/.hertzon/links/<pid>.json``. The MCP server sends one JSON request per connection:
``{"token", "cmd", "args"}`` and reads one JSON line back: ``{"ok": true, "result": ...}``.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path


def link_dir() -> Path:
    d = Path(os.environ.get("HERTZON_HOME") or (Path.home() / ".hertzon")) / "links"
    return d


def same_path(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


class LinkError(RuntimeError):
    pass


class GuiLink:
    def __init__(self, info: dict, file: Path):
        self.port = int(info["port"])
        self.token = str(info["token"])
        self.pid = int(info.get("pid", 0))
        self.file = file

    def call(self, cmd: str, timeout: float = 120.0, **args):
        req = json.dumps({"token": self.token, "cmd": cmd, "args": args}).encode("utf-8") + b"\n"
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
                s.sendall(req)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(1 << 20)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            raise LinkError(f"Hertzon window not reachable: {e}") from e
        if not buf:
            raise LinkError("Hertzon window closed the connection")
        resp = json.loads(buf.decode("utf-8"))
        if not resp.get("ok"):
            raise LinkError(resp.get("error", "request failed"))
        return resp.get("result")


def pid_alive(pid: int) -> bool | None:
    """Whether a process with this id runs (None: cannot tell)."""
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.get_last_error() == 5           # access denied: it exists
            try:
                code = wintypes.DWORD()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return None
                return code.value == 259                      # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def windows(timeout: float = 2.0) -> list[tuple[GuiLink, dict]]:
    """Running Hertzon windows with the live link on, newest first, with their 'hello' answer.

    Link files of windows that are gone (a crash leaves them behind) are removed, and the others are asked
    at the same time: a stale port that swallows the connection costs the time limit once, not once per file
    (seven stale files made every project tool call 12 s slower)."""
    d = link_dir()
    if not d.is_dir():
        return []
    links = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
            link = GuiLink(info, f)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid_alive(link.pid) is False:
            try:
                f.unlink()
            except OSError:
                pass
            continue
        links.append(link)
    if not links:
        return []
    answers: list = [None] * len(links)

    def ask(i: int, link: GuiLink) -> None:
        try:
            answers[i] = link.call("hello", timeout=timeout)
        except (OSError, ValueError, LinkError):
            pass
    if len(links) == 1:
        ask(0, links[0])
    else:
        threads = [threading.Thread(target=ask, args=(i, k), daemon=True) for i, k in enumerate(links)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout + 1.0)
    return [(k, a) for k, a in zip(links, answers) if a is not None]
