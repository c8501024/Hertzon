"""MCP server: geometry builds run in a child process with a time limit, progress and cancellation, so a build
that hangs in the kernel never blocks the server (a 4-layer board with 85 vias through all substrate boxes,
imported with import_pcb and drawn with render_image)."""
import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from test_pcb_import import _rect, _write  # noqa: E402
from test_stackup import KICAD_JOB  # noqa: E402


def _board(folder: Path) -> Path:
    random.seed(3)
    holes = "".join(f"X{random.uniform(102, 148):.3f}Y{random.uniform(102, 138):.3f}\n" for _ in range(85))
    # top copper with a net tie: two parts that meet in a corner, 5 nm apart, in one region
    tie = ("%FSLAX46Y46*%\n%MOMM*%\nG36*\nX100000000Y100000000D02*\nX125000000Y119999995D01*\n"
           "X150000000Y100000000D01*\nX150000000Y140000000D01*\nX125000000Y120000005D01*\nX100000000Y140000000D01*\n"
           "X100000000Y100000000D01*\nG37*\nM02*\n")
    return _write(folder, {
        "rf-F_Cu.gbr": tie, "rf-In1_Cu.gbr": _rect(100, 100, 150, 140), "rf-In2_Cu.gbr": _rect(100, 100, 150, 140),
        "rf-B_Cu.gbr": _rect(100, 100, 150, 140), "rf.gbrjob": json.dumps(KICAD_JOB),
        "rf-PTH.drl": "M48\n; #@! TF.FileFunction,Plated,1,4,PTH\nMETRIC\nT1C0.3\n%\nT1\n" + holes + "M30\n"})


class _Client:
    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, "-m", "hertzon.mcp"], cwd=str(ROOT), env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.q: Queue = Queue()
        threading.Thread(target=lambda: [self.q.put(json.loads(x)) for x in self.p.stdout], daemon=True).start()
        self.seen: list[dict] = []

    def send(self, msg):
        self.p.stdin.write((json.dumps(msg) + "\n").encode())
        self.p.stdin.flush()

    def call(self, mid, tool, meta=None, **args):
        params = {"name": tool, "arguments": args}
        if meta:
            params["_meta"] = meta
        self.send({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": params})

    def wait(self, mid, timeout=120):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                m = self.q.get(timeout=1)
            except Empty:
                continue
            self.seen.append(m)
            if m.get("id") == mid:
                return m
        raise AssertionError(f"no answer to {mid} in {timeout} s")

    def result(self, mid, timeout=120):
        r = self.wait(mid, timeout)["result"]
        text = "\n".join(c["text"] for c in r["content"] if c["type"] == "text")
        return r, text

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.p.kill()


def test_render_of_an_imported_board_is_bounded_and_the_server_stays_usable(tmp_path):
    env = dict(os.environ, HERTZON_HOME=str(tmp_path / "home"), HERTZON_MCP_MODE="file",
               PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    c = _Client(env)
    try:
        c.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        c.wait(1)
        c.call(2, "create_project", path=str(tmp_path / "board.hzn"), units="mm")
        assert not c.result(2)[0]["isError"]
        c.call(3, "import_pcb", files=[str(_board(tmp_path / "rf"))], arc_segments=24)
        r, text = c.result(3, 300)
        assert not r["isError"], text
        imp = json.loads(text)
        assert "geometry_errors" not in imp
        assert not any("MISSING" in w for w in imp["warnings"]), imp["warnings"]
        assert any("touching rings repaired" in x for x in imp["report"])
        assert any("24 straight segments" in x for x in imp["report"])

        # a time limit that cannot be met: a clear message, and the server answers right after it
        c.call(4, "render_image", view="top", timeout_s=0.3)
        r, text = c.result(4, 60)
        assert r["isError"] and "longer than 0.3 s and was stopped" in text and "still available" in text
        # a long build answers pings meanwhile and stops when the client cancels it
        c.call(5, "render_image", view="iso")
        c.send({"jsonrpc": "2.0", "id": 6, "method": "ping"})
        c.wait(6, 10)
        c.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 5}})
        r, text = c.result(5, 60)
        assert r["isError"] and "cancelled" in text
        c.call(7, "get_context")
        assert not c.result(7, 10)[0]["isError"]

        # the picture of the whole board (5 substrate boxes, each minus the 85 vias) in well under a minute
        t0 = time.monotonic()
        c.call(8, "render_image", meta={"progressToken": "p8"}, view="top")
        r, text = c.result(8, 60)
        assert time.monotonic() - t0 < 60
        assert not r["isError"], text
        assert any(x["type"] == "image" for x in r["content"])
        assert "geometry errors" not in text
        assert any(m.get("method") == "notifications/progress" and m["params"]["progressToken"] == "p8"
                   for m in c.seen)
        # the fast picture leaves out the drill holes (which the via cylinders fill) and says so
        c.call(9, "render_image", view="top", detail="fast")
        r, text = c.result(9, 60)
        assert not r["isError"] and "detail fast" in text
    finally:
        c.close()


def test_import_geometry_reads_the_file_in_the_geometry_process_with_a_time_limit(tmp_path):
    """A STEP file is read in the child process: the bodies come back, and a file the kernel reads for too
    long is stopped with a clear message while the server stays available."""
    import gmsh
    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("step")
        gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.addSphere(3, 0, 0, 0.5)
        gmsh.model.occ.synchronize()
        step = tmp_path / "two.step"
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    env = dict(os.environ, HERTZON_HOME=str(tmp_path / "home"), HERTZON_MCP_MODE="file",
               PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    c = _Client(env)
    try:
        c.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        c.wait(1)
        c.call(2, "create_project", path=str(tmp_path / "p.hzn"), units="mm")
        assert not c.result(2)[0]["isError"]
        c.call(3, "import_geometry", file=str(step), timeout_s=0.001, check=False)
        r, text = c.result(3, 60)
        assert r["isError"] and "was stopped" in text and "still available" in text
        c.call(4, "import_geometry", file=str(step), name="part")
        r, text = c.result(4, 120)
        assert not r["isError"], text
        assert json.loads(text)["objects"] == ["part_1", "part_2"]
    finally:
        c.close()
