"""Hertzon Bench: suite definition, scores, what a submission contains, the system description and that the
comparison site checks the same models."""
import json
import platform
import socket
from pathlib import Path

import pytest

from hertzon.solver import bench_submit, suite, sysinfo

ROOT = Path(__file__).resolve().parents[1]


def test_suite_models_exist_and_load():
    from hertzon.model.design import Project
    assert len(suite.TEST_IDS) == len(set(suite.TEST_IDS))
    for tid, _title, file, design, setup, _what in suite.TESTS:
        p = Project.load(str(suite.SUITE_DIR / file))
        assert p.design(design).setup(setup) is not None, tid


def test_reference_covers_every_test():
    ref = suite.reference()
    for tid in suite.TEST_IDS:
        assert ref["times"][tid] > 0, tid
        assert ref["tets"][tid] > 0, tid
        if tid != "sphere_rcs":
            assert ref["sparams"][tid]["freq"], tid


def test_scores():
    assert suite.score(100.0, 50.0) == 2000
    assert suite.score(100.0, 100.0) == 1000
    assert suite.score(None, 10.0) is None
    assert suite.overall([1000, 4000]) == 2000          # geometric mean
    assert suite.overall([1000, None]) is None


def test_mie_reference():
    # a large sphere tends to its geometric cross section pi a^2
    import math
    a = 1.0
    s = suite.mie_rcs(a, 50 * 299792458.0 / (2 * math.pi))      # ka = 50
    assert abs(s / (math.pi * a * a) - 1) < 0.05


def _result():
    tests = [{"id": t, "file_hash": "x", "total": 10.123456, "start": 1.0, "mesh": 1.0, "adaptive": 5.0, "sweep": 3.0,
              "passes": 4, "tets": 1000, "memory_mb": 500.0, "converged": True, "notes": ["n"],
              "check": {"kind": "ds", "value": 0.001, "ok": True}, "score": 1000} for t in suite.TEST_IDS]
    return {"schema": 1, "suite": suite.SUITE, "run_id": "a" * 32, "cores": 4, "solver": "Auto", "score": 1000,
            "complete": True, "finished": 1.7e9, "tests": tests}


def test_payload_has_no_paths_or_names():
    data = bench_submit.payload(_result(), {"platform": "Linux", "cpu": "X"}, "  me  ", "c")
    text = json.dumps(data)
    assert data["name"] == "me"
    assert "notes" not in data["tests"][0]                  # free text of the solver log stays local
    assert data["tests"][0]["total"] == 10.12
    for secret in (str(Path.home()), socket.gethostname()):
        assert secret not in text


def test_can_submit():
    assert bench_submit.can_submit(_result()) == ""
    r = _result()
    r["tests"] = r["tests"][:2]
    assert bench_submit.can_submit(r)
    r = _result()
    r["complete"] = False
    assert bench_submit.can_submit(r)


def test_sysinfo_is_anonymous():
    info = sysinfo.collect(measure=False)
    assert info["platform"] in ("Windows", "macOS", "Linux")
    assert info["cpu"] and info["logical_cores"] >= 1
    assert info["software"]["hertzon"]
    text = json.dumps(info)
    host = socket.gethostname()
    if len(host) > 3:
        assert host not in text
    assert str(Path.home()) not in text
    assert sysinfo.summary(info)


def test_memory_bandwidth_measures():
    assert sysinfo.memory_bandwidth(0.3) > 0.1


def test_site_checks_the_same_models():
    """bench-site/functions/_lib/suite.js must be regenerated (make_suite_spec.py) when the suite changes."""
    js = (ROOT / "bench-site" / "functions" / "_lib" / "suite.js").read_text(encoding="utf-8")
    site = json.loads(js[js.index("{"):js.rindex("}") + 1])
    spec = suite.site_spec()[suite.SUITE]
    assert site[suite.SUITE]["tests"] == spec["tests"], "run python bench-site/make_suite_spec.py"


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux paths")
def test_linux_os_line():
    assert "kernel" in sysinfo.collect(measure=False)["os"]


def test_dialog_shows_suite_and_submits(tmp_path, monkeypatch):
    """The Hertzon Bench tab shows a finished suite with its score; Submit sends only after consent."""
    import types
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui import benchmark as G
    monkeypatch.setattr(G, "benchmark_root", lambda: tmp_path)
    monkeypatch.setattr(sysinfo, "memory_bandwidth", lambda seconds=1.5: 12.3)
    res = _result()
    (tmp_path / "hertzon_bench").mkdir()
    (tmp_path / "hertzon_bench" / "suite.json").write_text(json.dumps(res), encoding="utf-8")
    from PySide6.QtWidgets import QWidget
    msgs = []
    win = QWidget()
    win.doc = types.SimpleNamespace(project=types.SimpleNamespace(path=""))
    win.msgs = types.SimpleNamespace(add=lambda lvl, text: msgs.append(text))
    win.solver = types.SimpleNamespace(busy=False)
    dlg = G.BenchmarkDialog(win)
    assert dlg.tabs.currentWidget() is dlg.suite_page
    assert "1000" in dlg.suite_score.text() and dlg.b_submit.isEnabled()
    assert dlg.suite_table.rowCount() == len(suite.TESTS)
    assert not dlg.case.isEnabled()
    sent = []
    monkeypatch.setattr(bench_submit, "submit", lambda data: sent.append(data) or
                        {"id": "abc", "url": "https://x/#/result/abc", "delete_key": "k"})
    sd = G.SubmitDialog(dlg, res)
    preview = json.loads(sd.preview.toPlainText())
    assert preview["system"]["memory_bandwidth_gbs"] == 12.3 and preview["run_id"] == res["run_id"]
    assert not sd.b_send.isEnabled()                # nothing without consent
    sd.name.setText("tester")
    sd.agree.setChecked(True)
    sd.b_send.click()
    assert sent and sent[0]["name"] == "tester" and sd.answer["id"] == "abc"
    assert json.loads(sd.preview.toPlainText()) == sent[0]       # exactly what was shown
