"""Minimal Model Context Protocol server over stdio (JSON-RPC 2.0, one message per line).

Only what the tool clients need: initialize, ping, tools/list and tools/call. No third-party
package, so it runs in the Hertzon environment as it is.

The pipes of the client are used for the protocol only. Everything the server and its children print (gmsh,
EMerge, numba, tracebacks, progress lines) goes to a log file, ~/.hertzon/logs/mcp-<pid>.log, never into the
client's stderr pipe: a client that does not read that pipe lets it fill up, and the next write would block the
server until the client gives up on the call. The standard input of the process is the null device for the same
reason, and on Windows the reader thread reads the pipe only when data is there (_lines_windows): a waiting
synchronous read held DLL loads and child starts until the client wrote again.
"""
from __future__ import annotations

import faulthandler
import json
import math
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
KEEP_LOGS = 20                 # server logs kept in ~/.hertzon/logs
WATCHDOG_S = 300.0             # a request running longer: the stacks of all threads go to the log (and again)


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Any]
    read_only: bool = False


class Image:
    """Tool result part: a PNG image (shown to the model)."""

    def __init__(self, png: bytes, text: str = ""):
        self.png = png
        self.text = text


class ToolError(Exception):
    """An error the model should read (bad arguments, missing object ...), not a crash."""


# the server of this process and the request it works on: long tools report progress and see cancellations
_SERVER: "Server | None" = None


def progress(done: float, total: float | None = None, message: str = "") -> None:
    """Progress of the running tool call: an MCP progress notification when the client asked for one (its
    progressToken), and a line on stderr (the server log) in any case."""
    s = _SERVER
    if message:
        print(f"[hertzon mcp] {message}", file=sys.stderr, flush=True)
    if s is None or s.token is None:
        return
    p = {"progressToken": s.token, "progress": done}
    if total is not None:
        p["total"] = total
    if message:
        p["message"] = message
    s._send({"jsonrpc": "2.0", "method": "notifications/progress", "params": p})


def cancelled() -> bool:
    """The client cancelled the running tool call (notifications/cancelled)."""
    s = _SERVER
    return bool(s is not None and s.current is not None and s.current in s.cancelled_ids)


def _content(result: Any) -> list[dict]:
    import base64
    parts = result if isinstance(result, list) and any(isinstance(r, Image) for r in result) else [result]
    out = []
    for r in parts:
        if isinstance(r, Image):
            if r.text:
                out.append({"type": "text", "text": r.text})
            out.append({"type": "image", "data": base64.b64encode(r.png).decode("ascii"), "mimeType": "image/png"})
        elif isinstance(r, str):
            out.append({"type": "text", "text": r})
        else:
            out.append({"type": "text", "text": json.dumps(r, indent=1, default=_default, ensure_ascii=False)})
    return out


def _finite(o):
    """The object with NaN and infinite floats replaced by null: json.dumps writes them as NaN / Infinity,
    which is not JSON. The client cannot parse such an answer, drops it and waits for the call until its
    time limit (half an hour in Claude Code)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def _encode(obj: dict) -> bytes:
    """One protocol line: valid JSON (no NaN), plain ASCII (lone surrogates of odd file names cannot break the
    UTF-8 encoding)."""
    try:
        text = json.dumps(obj, default=_default, allow_nan=False)
    except ValueError:
        text = json.dumps(_finite(json.loads(json.dumps(obj, default=_default))), allow_nan=False)
    return text.encode("ascii") + b"\n"


def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, complex):
        return [o.real, o.imag]
    return str(o)


class Server:
    def __init__(self, name: str, version: str, instructions: str, tools: list[Tool]):
        self.name, self.version, self.instructions = name, version, instructions
        self.tools = {t.name: t for t in tools}
        self.out = None
        self.token = None              # progressToken of the running call
        self.current = None            # id of the running call
        self.cancelled_ids: set = set()
        self._lock = threading.Lock()
        self._inbox: queue.Queue = queue.Queue()

    # ---- transport ----------------------------------------------------------------------------
    def serve(self) -> None:
        # The client's pipes carry the protocol and nothing else: private handles on the real stdin and
        # stdout, fd 0 on the null device, fd 1 and fd 2 on the log file (children inherit those).
        inp = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0 if os.name == "nt" else -1)
        self.out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
        sys.stdout.flush()
        sys.stderr.flush()
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
        log = open_log()
        if log is not None:
            os.dup2(log.fileno(), 2)
        os.dup2(2, 1)
        sys.stdin = open(os.devnull, encoding="utf-8")
        sys.stderr = log if log is not None else sys.stderr
        sys.stdout = sys.stderr
        try:
            faulthandler.enable(file=sys.stderr, all_threads=True)
        except (OSError, ValueError, AttributeError):
            pass
        print(f"[hertzon mcp] {self.name} {self.version} started, pid {os.getpid()}, python {sys.version.split()[0]}",
              file=sys.stderr, flush=True)
        global _SERVER
        _SERVER = self
        # stdin is read by a thread: while a long tool runs, pings are answered and cancellations seen
        threading.Thread(target=self._read, args=(inp,), daemon=True).start()
        while True:
            m = self._inbox.get()
            if m is None:
                break
            self.current = m.get("id")
            self.token = ((m.get("params") or {}).get("_meta") or {}).get("progressToken") \
                if isinstance(m.get("params"), dict) else None
            what = _describe(m)
            t0 = time.monotonic()
            print(f"[hertzon mcp] {time.strftime('%H:%M:%S')} -> {what}", file=sys.stderr, flush=True)
            _watchdog(True)
            try:
                resp = self.handle(m)
            finally:
                _watchdog(False)
                self.cancelled_ids.discard(self.current)
                self.current = self.token = None
            if resp is not None:
                self._send(resp)
            print(f"[hertzon mcp] {time.strftime('%H:%M:%S')} <- {what} ({time.monotonic() - t0:.1f} s)",
                  file=sys.stderr, flush=True)

    def _read(self, inp) -> None:
        lines = _lines_windows(inp) if os.name == "nt" else None
        while True:
            line = next(lines, b"") if lines is not None else inp.readline()
            if not line:
                self._inbox.put(None)
                return
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            for m in (msg if isinstance(msg, list) else [msg]):
                if not isinstance(m, dict):
                    continue
                if m.get("method") == "notifications/cancelled":
                    rid = (m.get("params") or {}).get("requestId")
                    if rid is not None:
                        self.cancelled_ids.add(rid)
                    continue
                if m.get("method") == "ping" and m.get("id") is not None:
                    self._send({"jsonrpc": "2.0", "id": m["id"], "result": {}})
                    continue
                self._inbox.put(m)

    def _send(self, obj: dict) -> None:
        try:
            data = _encode(obj)
        except Exception as e:  # noqa: BLE001
            # never leave a request without an answer: the client would wait for it until its time limit
            traceback.print_exc(file=sys.stderr)
            if obj.get("id") is None:
                return
            data = _encode({"jsonrpc": "2.0", "id": obj["id"],
                            "error": {"code": -32603, "message": f"answer not serializable: {type(e).__name__}: {e}"}})
        with self._lock:
            self.out.write(data)
            self.out.flush()

    # ---- dispatch -----------------------------------------------------------------------------
    def handle(self, msg: dict) -> dict | None:
        mid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if mid is None:          # notification (initialized, cancelled ...): no answer
            return None
        try:
            result = self.dispatch(method, params)
        except _RpcError as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": str(e)}}
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            want = params.get("protocolVersion", PROTOCOL_VERSIONS[0])
            return {"protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                    "instructions": self.instructions}
        if method == "ping":
            return {}
        if method == "tools/list":
            tools = []
            for t in self.tools.values():
                d = {"name": t.name, "description": t.description, "inputSchema": t.schema}
                if t.read_only:
                    d["annotations"] = {"readOnlyHint": True}
                tools.append(d)
            return {"tools": tools}
        if method == "tools/call":
            t = self.tools.get(params.get("name", ""))
            if t is None:
                raise _RpcError(-32602, f"Unknown tool {params.get('name')!r}")
            try:
                res = t.handler(dict(params.get("arguments") or {}))
                return {"content": _content(res), "isError": False}
            except ToolError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
            except Exception as e:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                return {"content": [{"type": "text", "text": f"Error ({type(e).__name__}): {e}"}], "isError": True}
        if method in ("resources/list", "resources/templates/list"):
            return {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
        if method == "prompts/list":
            return {"prompts": []}
        raise _RpcError(-32601, f"Method not found: {method}")


def _lines_windows(inp):
    """The lines of the client's pipe, read only when data is there (PeekNamedPipe).

    A synchronous ReadFile that waits on the pipe holds every other use of it on Windows. While the reader
    thread waited in readline(), the main thread hung in LoadLibrary of a DLL (gmsh, scipy, EMerge) and a child
    with an inherited stdin hung at its start, until the client wrote the next byte. Claude Code writes
    nothing while it waits for a call: its abort after 30 min was that byte (import_geometry, list_materials
    and run_python in the Meta benchmark, each the first call that loaded such a DLL). Not a pipe (a file in
    tests): plain reads."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    fd = inp.fileno()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.PeekNamedPipe.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                  ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
    k32.PeekNamedPipe.restype = wintypes.BOOL
    h = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
    avail = wintypes.DWORD()
    if not k32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None):
        yield from iter(inp.readline, b"")
        return
    buf = b""
    while True:
        if not k32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None):
            break                                   # the client closed the pipe
        if avail.value == 0:
            time.sleep(0.01)
            continue
        data = os.read(fd, avail.value)
        if not data:
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line + b"\n"
    if buf:
        yield buf


def _describe(m: dict) -> str:
    p = m.get("params") if isinstance(m.get("params"), dict) else {}
    what = f"{m.get('method')} #{m.get('id')}"
    if m.get("method") == "tools/call":
        what += f" {p.get('name')}"
    return what


def _watchdog(on: bool) -> None:
    """While a request runs: after WATCHDOG_S (HERTZON_MCP_WATCHDOG) the stacks of all threads go to the log,
    and again every WATCHDOG_S, so that a call that does not come back shows where it waits."""
    try:
        if on:
            t = float(os.environ.get("HERTZON_MCP_WATCHDOG", WATCHDOG_S))
            if t > 0:
                faulthandler.dump_traceback_later(t, repeat=True, file=sys.stderr)
        else:
            faulthandler.cancel_dump_traceback_later()
    except (OSError, ValueError, AttributeError, RuntimeError):
        pass


def log_dir() -> Path:
    return Path(os.environ.get("HERTZON_HOME") or (Path.home() / ".hertzon")) / "logs"


def open_log():
    """The log file of this server (HERTZON_MCP_LOG: another path; '-' keeps stderr). Old logs are removed."""
    want = os.environ.get("HERTZON_MCP_LOG", "")
    if want == "-":
        return None
    try:
        if want:
            path = Path(want)
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            d = log_dir()
            d.mkdir(parents=True, exist_ok=True)
            old = sorted(d.glob("mcp-*.log"), key=lambda f: f.stat().st_mtime)
            for f in old[:max(0, len(old) - (KEEP_LOGS - 1))]:
                try:
                    f.unlink()
                except OSError:
                    pass
            path = d / f"mcp-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
        return open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        # no log file: the null device rather than the client's pipe
        try:
            return open(os.devnull, "w", encoding="utf-8")
        except OSError:
            return None


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
