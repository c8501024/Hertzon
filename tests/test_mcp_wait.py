"""MCP server: no call waits longer than a client's time limit, and calls the client gave up on are not run.

A user on Linux drove Hertzon from the Claude app through Claude Desktop (4 min limit per call). The model called
get_status(wait_seconds=300): the server waited 5 min, the client gave up after 4, and the calls it sent next
(get_schema, run_python) queued behind the wait and timed out too, although each one takes milliseconds."""
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

from hertzon.mcp import protocol  # noqa: E402
from hertzon.mcp.protocol import max_wait  # noqa: E402


def test_max_wait_is_capped_per_client(monkeypatch):
    monkeypatch.delenv("HERTZON_MCP_MAX_WAIT", raising=False)
    srv = protocol.Server("t", "0", "", [])
    monkeypatch.setattr(protocol, "_SERVER", srv)
    srv.dispatch("initialize", {"clientInfo": {"name": "claude-code", "version": "2"}})
    assert max_wait(3600) == protocol.LONG_WAIT_S < 1800         # Claude Code: 30 min per call, few polls
    assert max_wait(10) == 10
    srv.dispatch("initialize", {"clientInfo": {"name": "codex-mcp-client"}})
    assert max_wait(3600) == protocol.MAX_WAIT_S < 600           # Codex as Hertzon registers it: 600 s
    assert max_wait(None) == 0 and max_wait("x") == 0 and max_wait(-5) == 0 and max_wait(float("nan")) == 0
    srv.dispatch("initialize", {"clientInfo": {"name": "claude-ai", "version": "0.1"}})
    assert max_wait(300) == protocol.SHORT_WAIT_S < 240           # Claude Desktop: 4 min per call
    monkeypatch.setenv("HERTZON_MCP_MAX_WAIT", "2")
    assert max_wait(600) == 2


def test_get_status_returns_within_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERTZON_MCP_MAX_WAIT", "1")
    from hertzon.mcp import tools as T
    path = str(tmp_path / "wg.hzn")
    T.t_create_project({"path": path, "solution_type": "Modal"})
    T.t_set_setup({"props": {"Frequency": "10GHz"}})
    ctx = T.load({})
    setup = ctx.des.setups[0].name
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        T.S.procs[str(T._out_dir(ctx, setup))] = sleeper           # an analysis that keeps running
        t0 = time.monotonic()
        r = T.t_get_status({"wait_seconds": 600})
        assert r["state"] == "running"
        assert time.monotonic() - t0 < 10
    finally:
        sleeper.kill()
        T.S.procs.clear()


_SERVER = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from hertzon.mcp.protocol import Server, Tool, pause
def slow(a):
    t0 = time.monotonic()
    done = pause(float(a.get("s", 30)))
    return {"completed": done, "waited": round(time.monotonic() - t0, 1)}
Server("t", "0", "", [Tool("slow", "", {"type": "object"}, slow),
                       Tool("quick", "", {"type": "object"}, lambda a: {"ok": True})]).serve()
'''


class _Client:
    def __init__(self, tmp_path):
        script = tmp_path / "srv.py"
        script.write_text(_SERVER, encoding="utf-8")
        env = dict(os.environ, HERTZON_HOME=str(tmp_path / "home"), HERTZON_MCP_LOG=str(tmp_path / "mcp.log"))
        self.log = tmp_path / "mcp.log"
        self.p = subprocess.Popen([sys.executable, str(script), str(ROOT)], env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.q: Queue = Queue()
        threading.Thread(target=lambda: [self.q.put(json.loads(x)) for x in self.p.stdout], daemon=True).start()

    def send(self, msg):
        self.p.stdin.write((json.dumps(msg) + "\n").encode())
        self.p.stdin.flush()

    def call(self, mid, tool, **args):
        self.send({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": tool, "arguments": args}})

    def cancel(self, mid):
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": mid}})

    def answers(self, timeout):
        out = {}
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                m = self.q.get(timeout=0.2)
            except Empty:
                continue
            if "id" in m:
                out[m["id"]] = m
        return out

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def test_cancelled_wait_ends_and_queued_cancelled_calls_are_skipped(tmp_path):
    c = _Client(tmp_path)
    try:
        c.send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
        c.call(1, "slow", s=30)          # a long wait holds the server
        c.call(2, "quick")               # queued behind it; the client gives up on both
        time.sleep(0.5)
        c.cancel(2)
        c.cancel(1)
        c.call(3, "quick")               # the next call is answered at once
        a = c.answers(5)
        assert 3 in a and json.loads(a[3]["result"]["content"][0]["text"]) == {"ok": True}
        assert 2 not in a                # cancelled while queued: not run, no answer
        if 1 in a:                       # the wait stopped at the cancellation, long before 30 s
            r = json.loads(a[1]["result"]["content"][0]["text"])
            assert r["completed"] is False and r["waited"] < 5
    finally:
        c.close()
    assert "skipped tools/call #2" in c.log.read_text(encoding="utf-8")
