"""Remote service and client: upload, progress lines, abort, mirroring of the results folder, token and
path checks. A small stand-in replaces the solver, so no EMerge is needed."""
from __future__ import annotations

import json
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


def test_file_gone_during_sync(service, tmp_path):
    """The solver writes some files under a temporary name and renames them: a file of the listing can be
    gone when it is fetched. The sync skips it instead of failing the whole remote analysis."""
    svc, host = service
    proj, out = make_project(tmp_path)
    jid, _ = run_job(host, proj, out)
    ev = {"state": "queued"}
    while ev["state"] in ("queued", "running"):
        ev = host.events(jid, 0, wait=5)
    job = svc.jobs[jid]
    listed = host.files

    def files_then_remove(j):
        entries = listed(j)
        (job.out_dir / "convergence.json").unlink()      # renamed away after the listing
        return entries

    m = Mirror(host, jid, out)
    host.files = files_then_remove
    m.sync()
    host.files = listed
    assert (out / "sparams_Sweep.npz").exists() and not (out / "convergence.json").exists()
    m.sync(final=True)
    assert not (out / "convergence.json").exists()


def test_jobs_list_shows_sender_and_progress(service, tmp_path):
    """The progress window of the solving computer lists the analyses other computers sent to it."""
    svc, host = service
    proj, out = make_project(tmp_path)
    archive, rel = prepare(proj, out)
    jid = host.submit(archive, {"kind": "run", "project": proj.name, "design": "slow", "name": "Setup1",
                                "out": rel, "label": "Antenna - HFDesign - Setup1", "client": "LAPTOP"})["id"]
    deadline = time.time() + 20
    while time.time() < deadline:
        job = next(j for j in host.jobs() if j["id"] == jid)
        if job["progress"] > 0:
            break
        time.sleep(0.05)
    assert job["label"] == "Antenna - HFDesign - Setup1" and job["client"] == "LAPTOP"
    assert job["state"] == "running" and job["progress"] == 10 and job["text"] == "Adaptive pass 1"
    host.abort(jid)


def test_large_sweep_fields_come_last(service, tmp_path):
    """The small results arrive first (the analysis then counts as finished); large sweep fields after."""
    from hertzon.gui.remote_run import late
    svc, host = service
    proj, out = make_project(tmp_path)
    jid, _ = run_job(host, proj, out)
    ev = {"state": "queued"}
    while ev["state"] in ("queued", "running"):
        ev = host.events(jid, 0, wait=5)
    (svc.jobs[jid].out_dir / "fields_Sweep.npz").write_bytes(b"F" * 2_000_000)
    m = Mirror(host, jid, out)
    m.sync(skip=late)
    assert (out / "sparams_Sweep.npz").exists() and (out / "fields_LastAdaptive.npz").exists()
    assert not (out / "fields_Sweep.npz").exists()
    m.sync(final=True)
    assert (out / "fields_Sweep.npz").stat().st_size == 2_000_000


def test_discovery_and_pairing(service):
    """A GUI finds the service in the network and gets its token with the six-digit pairing code."""
    import socket as _socket

    from hertzon.remote.client import discover, pair
    from hertzon.remote.server import start_discovery
    svc, host = service
    svc.port = int(host.address.rsplit(":", 1)[1])
    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    udp_port = probe.getsockname()[1]
    probe.close()
    sock = start_discovery(svc, udp_port)
    try:
        found = discover(timeout=1.0, port=udp_port)
    finally:
        sock.close()
    assert any(f["address"].endswith(f":{svc.port}") and f["hertzon"] for f in found)
    assert "token" not in json.dumps(found)
    assert pair(host.address, svc.pairing) == "secret-token"
    for _ in range(5):
        with pytest.raises(RemoteError, match="wrong pairing code"):
            pair(host.address, "000000" if svc.pairing != "000000" else "111111")
    with pytest.raises(RemoteError, match="too many"):
        pair(host.address, svc.pairing)                  # locked, even with the right code


def test_hpc_settings_of_the_solving_computer(tmp_path):
    """Cores and sweep processes come from the solving computer (its Options, hpc.json), not the sender."""
    svc = Service(tmp_path / "w", "t")
    try:
        assert svc.sweep_workers == 2 and svc.cores >= 1
        (tmp_path / "w" / "hpc.json").write_text('{"cores": 3, "sweep_workers": 4}')
        assert svc.cores == 3 and svc.sweep_workers == 4
        assert svc.info()["cores"] == 3 and svc.info()["sweep_workers"] == 4
        fixed = Service(tmp_path / "w", "t", cores=5)          # --cores on the command line wins
        assert fixed.cores == 5
        fixed.stop()
    finally:
        svc.stop()


def test_token_from_environment(tmp_path, monkeypatch):
    """A cloud computer gets the token of the GUI that created it (HERTZON_TOKEN), none is generated."""
    from hertzon.remote.server import load_token
    monkeypatch.setenv("HERTZON_TOKEN", " given-token ")
    assert load_token(tmp_path / "w") == "given-token" and not (tmp_path / "w" / "token").exists()
    monkeypatch.delenv("HERTZON_TOKEN")
    assert load_token(tmp_path / "w") != "given-token"
