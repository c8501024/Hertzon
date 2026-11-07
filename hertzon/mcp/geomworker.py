"""Geometry builds of the MCP server in a separate process (render_image, get_geometry, validate ...).

The OCC kernel cannot be interrupted inside a call: a boolean or a surface mesh that runs for half an hour
would hold the whole server, and every later call (get_context too) would wait behind it. The build runs in a
child process instead; the server reads its progress, answers pings meanwhile, and stops the child when the
time limit is reached or the client cancels the call. The server stays usable either way.

The child stays between calls (gmsh is loaded once). Jobs come on its stdin as ``J <hex pickle>`` lines;
answers go to its private stdout handle, one frame per line: ``P <json>`` progress, ``R <hex pickle>`` the
result ({name: Tess}, {name: error}). Everything gmsh prints goes to stderr.
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

DEFAULT_TIMEOUT = 300.0          # s: HERTZON_MCP_BUILD_TIMEOUT or the timeout_s argument of the tool


class BuildTimeout(Exception):
    pass


class BuildCancelled(Exception):
    pass


def default_timeout() -> float:
    try:
        return float(os.environ.get("HERTZON_MCP_BUILD_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


class _Worker:
    """The child process, kept between calls (gmsh and Hertzon load once); a new one after a stop."""

    def __init__(self):
        root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.p = subprocess.Popen([sys.executable, "-m", "hertzon.mcp.geomworker"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE,
                                  stderr=sys.stderr if _has_fd(sys.stderr) else subprocess.DEVNULL,
                                  env=env, creationflags=flags)
        self.frames: Queue = Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.frames.put(line)
        self.frames.put(None)

    def alive(self) -> bool:
        return self.p.poll() is None

    def stop(self):
        if self.p.poll() is None:
            self.p.kill()
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


_WORKER: _Worker | None = None
_LOCK = threading.Lock()


def stop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.stop()
        _WORKER = None


def warm() -> None:
    """Start the child in the background when the server starts, so that the first render_image, validate or
    run_simulation does not wait for a new Python to load gmsh and Hertzon (tens of seconds on Windows with
    cold DLLs). HERTZON_NO_PRELOAD=1 turns it off."""
    if os.environ.get("HERTZON_NO_PRELOAD") == "1":
        return

    def start():
        global _WORKER
        with _LOCK:
            if _WORKER is None or not _WORKER.alive():
                try:
                    _WORKER = _Worker()
                except Exception:  # noqa: BLE001
                    _WORKER = None
    threading.Thread(target=start, daemon=True).start()


def build(project_dict: dict, path: str | None, design: str, names: list[str] | None, timeout: float,
          on_progress=None, should_stop=None, kind: str = "tess"):
    """Build and tessellate the named live objects (all: None) of a design in the child process. Raises
    BuildTimeout or BuildCancelled after stopping the child; the next call starts a new one."""
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.alive():
            _WORKER = _Worker()
        w = _WORKER
        job = pickle.dumps({"project": project_dict, "path": path, "design": design, "names": names,
                            "kind": kind})
        t0 = time.monotonic()
        last = ""
        try:
            w.p.stdin.write(b"J " + job.hex().encode("ascii") + b"\n")
            w.p.stdin.flush()
            while True:
                left = timeout - (time.monotonic() - t0)
                if left <= 0:
                    raise BuildTimeout(last)
                if should_stop is not None and should_stop():
                    raise BuildCancelled(last)
                try:
                    line = w.frames.get(timeout=min(0.25, left))
                except Empty:
                    continue
                if line is None:
                    raise RuntimeError(f"The geometry process ended (exit code {w.p.poll()})"
                                       + (f" while {last}" if last else ""))
                tag, _, body = line.decode("utf-8", "replace").rstrip("\r\n").partition(" ")
                if tag == "P":
                    msg = json.loads(body)
                    last = msg.get("message", last)
                    if on_progress is not None:
                        on_progress(msg.get("done", 0), msg.get("total"), last)
                elif tag == "R":
                    result = pickle.loads(bytes.fromhex(body))
                    break
        except BaseException:
            # the kernel cannot be interrupted: the child goes, the next call starts a fresh one
            w.stop()
            _WORKER = None
            raise
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(result["error"])
    return result


def _has_fd(stream) -> bool:
    try:
        stream.fileno()
        return True
    except (AttributeError, OSError, ValueError):
        return False


# ------------------------------------------------------------------------------------------------
# the child
# ------------------------------------------------------------------------------------------------
def main() -> int:
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr

    def send(tag: str, body: str) -> None:
        out.write(f"{tag} {body}\n".encode("utf-8"))

    # load the heavy modules now, while the server waits for its first call, not when the first job comes
    try:
        from ..kernel import occ as K
        from ..model import design as _design  # noqa: F401
        K.ensure_gmsh("hertzon_mcp")
    except Exception as e:  # noqa: BLE001
        print(f"[hertzon geometry] preload failed: {e}", file=sys.stderr, flush=True)

    for line in sys.stdin.buffer:
        tag, _, body = line.decode("ascii", "replace").strip().partition(" ")
        if tag != "J":
            continue
        try:
            job = pickle.loads(bytes.fromhex(body))
            from ..model.design import project_from_dict
            proj = project_from_dict(job["project"])
            proj.path = job["path"]
            des = proj.design(job["design"])
            if job.get("kind") == "sheets":
                send("R", pickle.dumps(sheets_here(proj, des)).hex())
                continue
            result = tessellate_here(
                proj, des, job["names"],
                lambda i, n, msg: send("P", json.dumps({"done": i, "total": n, "message": msg})))
            send("R", pickle.dumps(result).hex())
        except Exception as e:  # noqa: BLE001
            send("R", pickle.dumps({"error": f"{type(e).__name__}: {e}"}).hex())
    return 0


def sheets_here(proj, des) -> list:
    """Where the sheets lie in the solids (kernel.occ.sheet_placement)."""
    from ..kernel import occ as K
    from ..model.materials import is_conductor
    K.ensure_gmsh("hertzon_mcp")
    return K.sheet_placement(des, des.evaluator(proj), lambda n: is_conductor(proj.material(des.obj(n).material)))


def tessellate_here(proj, des, names, progress=None) -> tuple[dict, dict]:
    """Build and tessellate in this process: ({name: Tess}, {name: error})."""
    import gmsh
    from ..kernel import occ as K
    ev = des.evaluator(proj)
    K.ensure_gmsh("hertzon_mcp")
    gmsh.model.remove()
    gmsh.model.add("hertzon_mcp")
    live = [o.name for o in des.live_objects() if names is None or o.name in names]
    n = len(live) + 1
    count = [0]

    def step(name):
        count[0] += 1
        if progress is not None:
            progress(count[0], n, f"building {name} ({count[0]} of {n - 1} objects)")
    ctx = K.build_design(des, ev, names=names, on_progress=step)
    if progress is not None:
        progress(n - 1, n, "display mesh of the built objects")
    tess = K.tessellate(ctx, "coarse") if ctx.built else {}
    return tess, dict(ctx.errors)


if __name__ == "__main__":
    sys.exit(main())
