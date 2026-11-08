"""MCP server: the client's pipes carry the protocol only, so nothing a tool does can hold a call.

In the Meta benchmark (Example 2) three calls (import_geometry, list_materials + get_schema, run_python) got no
answer until Claude Code gave up after 30 min, and the server answered the next call at once. These tests run the
server as a client does, with a stderr pipe that nobody reads, and check that a call that prints a lot, a child
that reads its standard input and an answer with NaN all come back."""
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.mcp import live  # noqa: E402
from hertzon.mcp.protocol import Server, Tool  # noqa: E402


class _Client:
    """A client that never reads the server's stderr (as some MCP clients do)."""

    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, "-m", "hertzon.mcp"], cwd=str(ROOT), env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.q: Queue = Queue()
        threading.Thread(target=lambda: [self.q.put(json.loads(x)) for x in self.p.stdout], daemon=True).start()

    def send(self, msg):
        self.p.stdin.write((json.dumps(msg) + "\n").encode())
        self.p.stdin.flush()

    def call(self, mid, tool, timeout=60, **args):
        self.send({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                m = self.q.get(timeout=0.5)
            except Empty:
                continue
            if m.get("id") == mid:
                r = m["result"]
                return r, "\n".join(c["text"] for c in r["content"] if c["type"] == "text")
        raise AssertionError(f"no answer to {tool} in {timeout} s")

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.p.kill()


def _env(tmp_path):
    env = dict(os.environ, HERTZON_HOME=str(tmp_path / "home"), HERTZON_MCP_MODE="file", HERTZON_NO_PRELOAD="1",
               PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("HERTZON_MCP_LOG", None)
    return env


def test_output_and_children_never_touch_the_client_pipes(tmp_path):
    c = _Client(_env(tmp_path))
    try:
        c.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        r, text = c.call(2, "create_project", path=str(tmp_path / "p.hzn"))
        assert not r["isError"], text
        # 4 MB on stderr and on fd 2 directly (as gmsh and EMerge write): far more than any pipe holds
        code = ("import os, sys\nsys.stderr.write('x' * 2_000_000)\nsys.stderr.flush()\n"
                "os.write(2, b'y' * 2_000_000)\nprint('done')\n")
        r, text = c.call(3, "run_python", timeout=60, code=code)
        assert not r["isError"], text
        assert json.loads(text)["output"].strip() == "done"
        # a child with an inherited stdin reads the null device, not the protocol stream
        code = ("import subprocess, sys\n"
                "r = subprocess.run([sys.executable, '-c', 'import sys; print(len(sys.stdin.read()))'],"
                " stdout=subprocess.PIPE, timeout=30)\nprint(r.stdout.decode().strip())\n")
        r, text = c.call(4, "run_python", timeout=60, code=code)
        assert not r["isError"], text
        assert json.loads(text)["output"].strip() == "0"
        # the server keeps answering
        r, text = c.call(5, "get_context", timeout=20)
        assert not r["isError"], text
    finally:
        c.close()
    logs = list((tmp_path / "home" / "logs").glob("mcp-*.log"))
    assert len(logs) == 1
    log = logs[0].read_text(encoding="utf-8", errors="replace")
    assert "started" in log and "tools/call #3 run_python" in log and "xxxxxxxx" in log and "yyyyyyyy" in log


def _server(tools):
    s = Server("t", "0", "", tools)
    s.out = io.BytesIO()
    return s


def test_an_answer_with_nan_or_odd_text_is_still_valid_json():
    s = _server([Tool("nan", "", {"type": "object"}, lambda a: {"x": float("nan"), "y": [float("inf"), 1.5]})])
    resp = s.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nan", "arguments": {}}})
    s._send(resp)
    s._send({"jsonrpc": "2.0", "id": 8, "result": {"v": float("nan"), "name": "bad \udcff name"}})
    lines = s.out.getvalue().splitlines()
    assert len(lines) == 2
    for line in lines:
        line.decode("ascii")
        json.loads(line, parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(c)))
    assert json.loads(lines[1])["result"]["v"] is None


def test_stale_link_files_are_removed_and_windows_are_asked_together(tmp_path, monkeypatch):
    import socket
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path))
    d = live.link_dir()
    d.mkdir(parents=True)
    # a process that is gone: its file goes without a connection attempt
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    (d / "dead.json").write_text(json.dumps({"port": 9, "token": "t", "pid": p.pid}))
    # live processes whose ports accept but never answer: asked at the same time, the limit is paid once
    servers = []
    for i in range(4):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        servers.append(srv)
        (d / f"mute{i}.json").write_text(json.dumps({"port": srv.getsockname()[1], "token": "t", "pid": os.getpid()}))
    try:
        t0 = time.monotonic()
        assert live.windows(timeout=1.0) == []
        assert time.monotonic() - t0 < 2.5
        assert not (d / "dead.json").exists()
        assert len(list(d.glob("mute*.json"))) == 4
    finally:
        for srv in servers:
            srv.close()
