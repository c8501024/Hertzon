"""Remote service and client: upload, progress lines, abort, mirroring of the results folder, token and
path checks. A small stand-in replaces the solver, so no EMerge is needed."""
from __future__ import annotations

import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from hertzon.remote.client import Mirror, RemoteError, RemoteHost, normalize_address, prepare
from hertzon.remote.protocol import safe_rel, unpack
from hertzon.remote.server import Service, make_server

TAG = "@@HZN "

FAKE = r'''
import json, sys, time
from pathlib import Path
out = Path(sys.argv[1]); mode = sys.argv[2]
def emit(kind, **d): print("@@HZN " + json.dumps({"type": kind, **d}), flush=True)
emit("progress", value=10, text="Adaptive pass 1")
(out / "convergence.json").write_text('{"passes": 1}')
(out / "fields_LastAdaptive.npz").write_bytes(b"F" * 300000)
emit("passdata", step=1)
print("plain EMerge log line", flush=True)
if mode == "slow":
    for _ in range(600):
        if (out / "ABORT").exists():
            sys.exit(3)
        time.sleep(0.05)
(out / "sparams_Sweep.npz").write_bytes(b"S" * 1000)
(out / "stale.txt").unlink(missing_ok=True)
emit("done", elapsed=0.1)
'''


@pytest.fixture
def service(tmp_path):
    script = tmp_path / "fake_solver.py"
    script.write_text(FAKE, encoding="utf-8")

    def command(job):
        mode = "slow" if job.design == "slow" else "fast"
        return [sys.executable, str(script), str(job.out_dir), mode]

    svc = Service(tmp_path / "work", "secret-token", cores=2, command=command)
    srv = make_server(svc, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host = RemoteHost(f"127.0.0.1:{srv.server_address[1]}", "secret-token", timeout=10)
    yield svc, host
    svc.stop()
    srv.shutdown()
    srv.server_close()


def make_project(tmp_path) -> tuple[Path, Path]:
    proj = tmp_path / "local" / "Antenna.hzn"
    proj.parent.mkdir(parents=True)
    proj.write_text('{"format": "hertzon-project"}', encoding="utf-8")
    out = proj.with_suffix(".hznresults") / "HFDesign" / "Setup1"
    out.mkdir(parents=True)
    (out / "amr_state.npz").write_bytes(b"A" * 5000)      # kept from an earlier run, uploaded with the job
    (out / "stale.txt").write_text("removed by the solver")
    return proj, out


def run_job(host, proj, out, design="HFDesign"):
    archive, rel = prepare(proj, out)
    job = host.submit(archive, {"kind": "run", "project": proj.name, "design": design, "name": "Setup1",
                                "out": rel, "env": {"HERTZON_ONLY_SWEEP": "", "EVIL": "1"}})
    archive.unlink()
    return job["id"], rel


def test_address():
    assert normalize_address("simstation02") == "http://simstation02:47820"
    assert normalize_address("10.0.0.5:9000/") == "http://10.0.0.5:9000"
    with pytest.raises(RemoteError):
        normalize_address(" ")


def test_info_and_token(service):
    _svc, host = service
    info = host.info()
    assert info["service"] == "hertzon-remote" and info["cores"] == 2
    with pytest.raises(RemoteError, match="token"):
        RemoteHost(host.address, "wrong").info()


def test_job_mirrors_results(service, tmp_path):
    svc, host = service
    proj, out = make_project(tmp_path)
    jid, rel = run_job(host, proj, out)
    assert rel == "Antenna.hznresults/HFDesign/Setup1"
    job = svc.jobs[jid]
    assert "EVIL" not in job.env                         # only the solver's own job variables pass
    lines, since, state = [], 0, "queued"
    while state in ("queued", "running"):
        ev = host.events(jid, since, wait=5)
        lines += ev["lines"]
        since, state = ev["next"], ev["state"]
    assert state == "done" and ev["code"] == 0
    assert any(line.startswith(TAG) and '"passdata"' in line for line in lines)
    assert "plain EMerge log line" in lines
    files = {e["path"]: e for e in host.files(jid)}
    assert files["amr_state.npz"]["uploaded"] and not files["sparams_Sweep.npz"]["uploaded"]
    sizes = []
    m = Mirror(host, jid, out)
    m.sync(final=True, progress=lambda d, t: sizes.append(d))
    assert (out / "sparams_Sweep.npz").read_bytes() == b"S" * 1000
    assert (out / "fields_LastAdaptive.npz").stat().st_size == 300000
    assert (out / "amr_state.npz").read_bytes() == b"A" * 5000
    assert not (out / "stale.txt").exists()              # removed remotely -> removed here
    assert sizes == sorted(sizes) and sizes[0] < 300000   # small files first
    host.delete(jid)
    assert jid not in svc.jobs and not job.folder.exists()


def test_abort(service, tmp_path):
    svc, host = service
    proj, out = make_project(tmp_path)
    jid, _ = run_job(host, proj, out, design="slow")
    deadline = time.time() + 20
    while svc.jobs[jid].state != "running" and time.time() < deadline:
        time.sleep(0.05)
    host.abort(jid)
    ev = {"state": "running"}
    since = 0
    while ev["state"] in ("queued", "running") and time.time() < deadline:
        ev = host.events(jid, since, wait=2)
        since = ev["next"]
    assert ev["state"] == "aborted" and ev["aborted"]


def test_queue_and_abort_queued(service, tmp_path):
    _svc, host = service
    proj, out = make_project(tmp_path)
    first, _ = run_job(host, proj, out, design="slow")
    second, _ = run_job(host, proj, out, design="slow")
    assert host.events(second, 0, wait=0)["state"] == "queued"
    host.abort(second)
    assert host.events(second, 0, wait=0)["state"] == "aborted"
    host.abort(first)


def test_paths_are_checked(tmp_path):
    for bad in ("../x", "/etc/passwd", "C:/x", "a/../../b", ""):
        with pytest.raises(ValueError):
            safe_rel(bad)
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../outside.txt", "x")
    with pytest.raises(ValueError):
        unpack(z, tmp_path / "dest")
    assert not (tmp_path / "outside.txt").exists()


def test_bad_job_rejected(service, tmp_path):
    _svc, host = service
    proj, out = make_project(tmp_path)
    archive, rel = prepare(proj, out)
    with pytest.raises(RemoteError, match="kind"):
        host.submit(archive, {"kind": "shell", "project": proj.name, "out": rel})
    with pytest.raises(RemoteError):
        host.submit(archive, {"kind": "run", "project": proj.name, "out": "../../x"})


def test_shutdown(service, tmp_path):
    svc, host = service
    proj, out = make_project(tmp_path)
    jid, _ = run_job(host, proj, out, design="slow")
    assert host.shutdown()["stopping"]
    deadline = time.time() + 20
    while svc.jobs[jid].state in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
    assert svc.jobs[jid].state == "aborted"            # a stopped service leaves no solver running
    with pytest.raises(RemoteError):
        host.info()
