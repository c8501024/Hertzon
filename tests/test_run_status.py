"""status.json of a run whose solver process ended without finishing is not reported as running forever."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.solver.results import ResultStore, run_status, running_status  # noqa: E402


def test_running_status_names_the_process():
    st = running_status()
    assert st["state"] == "running" and st["pid"] == os.getpid() and st["host"]


def test_a_live_run_stays_running(tmp_path):
    ResultStore(tmp_path).write_json("status.json", running_status())
    assert run_status(tmp_path)["state"] == "running"


def test_a_run_whose_process_is_gone_reads_as_aborted(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    ResultStore(tmp_path).write_json("status.json", {**running_status(), "pid": p.pid})
    st = run_status(tmp_path)
    assert st["state"] == "aborted" and st["stale"] and "ended without finishing" in st["error"]
    # the file itself is left as it is
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "running"


def test_old_status_files_and_other_hosts_are_taken_as_they_are(tmp_path):
    ResultStore(tmp_path).write_json("status.json", {"state": "running", "started": 0})      # no pid (older Hertzon)
    assert run_status(tmp_path)["state"] == "running"
    ResultStore(tmp_path).write_json("status.json", {"state": "running", "pid": 1 << 30, "host": "elsewhere"})
    assert run_status(tmp_path)["state"] == "running"     # remote solve: its process cannot be checked here
    assert run_status(tmp_path / "missing") == {}


def test_far_field_and_field_files_describe_their_layout():
    import numpy as np
    from hertzon.solver.farfield_post import sampling_text
    from hertzon.solver.lazy_fields import field_layout
    th, ph = np.radians(np.arange(0, 181, 2)), np.radians(np.arange(0, 361, 2))
    assert sampling_text(th, ph) == "theta 0..180 deg step 2 (91), phi 0..360 deg step 2 (181)"
    assert sampling_text(np.array([np.pi / 2]), ph[:1]) == "theta 90..90 deg (1), phi 0..0 deg (1)"
    assert "(n_tets, 10, 3)" in str(field_layout(10)[0]) and "(n_tets, 4, 3)" in str(field_layout(4)[0])
