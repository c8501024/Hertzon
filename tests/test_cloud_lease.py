"""Cloud server shared by several Hertzon windows: leases, a server that disappears (never replaced behind an
analysis, with the reason), costs, the watchdog's announcement and the secrets in the OS key store."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from test_cloud import FakeHetzner, api  # noqa: F401  (fixture)

from hertzon.remote.cloud import (
    LOST_HINT,
    CloudHost,
    CloudSession,
    ServerLost,
    watchdog,
)
from hertzon.remote.cloud.hetzner import Hetzner
from hertzon.remote.cloud.lease import EventLog, Leases, process_alive

needs_keygen = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs OpenSSH's ssh-keygen")


def test_leases_of_running_processes_only(tmp_path):
    leases = Leases(tmp_path)
    leases.hold("me", jobs=1)
    leases.hold("other window")
    assert {d["owner"] for d in leases.all()} == {"me", "other window"}
    assert [d["owner"] for d in leases.others("me")] == ["other window"]
    # a lease of a process that ended does not count (and is removed)
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    (leases.dir / "dead.json").write_text(json.dumps({"owner": "dead", "pid": p.pid, "jobs": 3}))
    assert not process_alive(p.pid)
    assert "dead" not in {d["owner"] for d in leases.all()} and not (leases.dir / "dead.json").exists()
    leases.drop("other window")
    assert leases.others("me") == []
    assert process_alive(os.getpid())


def test_event_log(tmp_path):
    log = EventLog(tmp_path)
    log.add("created", id=7, name="hertzon-a")
    log.add("deleted", id=7, name="hertzon-a", reason="window closed")
    log.add("deleted", id=8, name="hertzon-b", reason="other")
    assert log.last(7)["reason"] == "window closed" and log.last(9) is None
    assert [r["event"] for r in log.read()] == ["created", "deleted", "deleted"]


@needs_keygen
def test_lost_server_is_reported_not_replaced(api, tmp_path):  # noqa: F811
    s = CloudSession(Hetzner("good", api), "ccx33", "fsn1", workdir=tmp_path)
    st = s._create(lambda text: None)
    assert s.state()["price"] == {"hourly": 0.1, "currency": "EUR"}
    assert s.log.last(st["id"], ("created",))["type"] == "ccx33"
    # deleted outside Hertzon (watchdog, console): an analysis gets ServerLost, no new server is created
    FakeHetzner.servers.pop(st["id"])
    with pytest.raises(ServerLost) as ex:
        s.ensure(create=False)
    assert ex.value.reason == LOST_HINT and st["name"] in str(ex.value)
    assert len(FakeHetzner.created) == 1 and not s.state()
    assert s.log.read()[-1]["event"] == "lost"


@needs_keygen
def test_new_server_only_with_a_note(api, tmp_path, monkeypatch):  # noqa: F811
    s = CloudSession(Hetzner("good", api), "ccx33", "fsn1", workdir=tmp_path)
    st = s._create(lambda text: None)
    s.note_watchdog("idle")
    FakeHetzner.servers.pop(st["id"])
    notes = []
    monkeypatch.setattr(s, "_connect", lambda st, say, abort: None)
    monkeypatch.setattr(s.tunnel, "alive", lambda: True)
    s.ensure(note=lambda level, text: notes.append((level, text)))
    assert len(FakeHetzner.created) == 2
    assert notes[0][0] == "warning" and "the server itself" in notes[0][1] and "idle" in notes[0][1]
    assert "creating a new one" in notes[0][1]


@needs_keygen
def test_analysis_learns_why_another_window_deleted_the_server(api, tmp_path):  # noqa: F811
    mine = CloudSession(Hetzner("good", api), "ccx33", "fsn1", workdir=tmp_path)
    other = CloudSession(Hetzner("good", api), "ccx33", "fsn1", workdir=tmp_path)    # another window
    st = mine._create(lambda text: None)
    host = CloudHost(mine)
    host.server_id, host._host = st["id"], SimpleNamespace(url="http://127.0.0.1:1", token="t")
    assert other.delete(reason="the last Hertzon window that used it closed")
    with pytest.raises(ServerLost) as ex:
        host._request("GET", "/api/info")
    assert "the last Hertzon window that used it closed" in ex.value.reason


def test_costs(tmp_path):
    s = CloudSession(Hetzner("x"), "ccx33", "fsn1", workdir=tmp_path)
    now = 1_000_000.0
    st = {"created": now - 1.5 * 3600, "price": {"hourly": 0.1, "currency": "EUR"}}
    c = s.cost(st, now)
    assert c["hours"] == 1.5 and c["cost"] == pytest.approx(0.2)          # Hetzner: every started hour
    st["price"]["monthly"] = 0.15
    assert s.cost(st, now)["cost"] == pytest.approx(0.15)                  # up to the monthly price
    assert "cost" not in s.cost({"created": now - 3600}, now)               # price unknown
    s.provider.key = "aws"
    st = {"created": now - 1.5 * 3600, "price": {"hourly": 0.1, "currency": "USD"}}
    assert s.cost(st, now)["cost"] == pytest.approx(0.15)


def test_watchdog_announces_why(tmp_path, monkeypatch):
    from hertzon.remote.server import Service
    svc = Service(tmp_path / "w", "t")
    try:
        assert "deleting" not in svc.info()
        cfg = {"announce": str(tmp_path / "w" / "deleting.json"), "announce_s": 0}
        watchdog.announce(cfg, "the solver made no progress (hung)", busy=True)
        assert svc.info()["deleting"] == "the solver made no progress (hung)"
        job = SimpleNamespace(dropped=0, lines=[], state="running", code=None, aborted=False)
        assert svc.events(job, 0, wait=0)["deleting"].endswith("(hung)")
    finally:
        svc.stop()


# ---- secrets ---------------------------------------------------------------------------------------
class MemoryKeyring:
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        self.store.pop((service, user))


@pytest.fixture
def settings(tmp_path):
    from PySide6.QtCore import QSettings
    return QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)


def test_secrets_move_into_the_key_store(settings, monkeypatch):
    from hertzon.remote import keystore
    kr = MemoryKeyring()
    monkeypatch.setattr(keystore, "_keyring", lambda: SimpleNamespace(
        get_keyring=lambda: kr, get_password=kr.get_password, set_password=kr.set_password,
        delete_password=kr.delete_password))
    keystore.reset()
    settings.setValue("cloud/aws_secret", "wJalrSECRET")
    settings.setValue("cloud/hetzner_token", "htok")
    settings.setValue("cloud/aws_key_id", "AKIA1")
    assert sorted(keystore.migrate(settings)) == ["cloud/aws_secret", "cloud/hetzner_token"]
    assert not settings.contains("cloud/aws_secret") and not settings.contains("cloud/hetzner_token")
    assert settings.value("cloud/aws_key_id") == "AKIA1"                        # no secret: stays
    assert kr.store[("Hertzon", "cloud/aws_secret")] == "wJalrSECRET"
    get = keystore.getter(settings)
    from hertzon.remote.cloud import providers
    settings.setValue("cloud/provider", "aws")
    assert providers.credentials(get) == ("AKIA1", "wJalrSECRET") and providers.configured(get)
    assert keystore.put(settings, "cloud/aws_secret", "")                       # cleared: removed from the store
    assert ("Hertzon", "cloud/aws_secret") not in kr.store and not providers.configured(get)
    keystore.reset()


def test_secrets_without_key_store_stay_in_the_settings(settings, monkeypatch):
    from hertzon.remote import keystore
    monkeypatch.setenv("HERTZON_KEYRING", "0")
    keystore.reset()
    settings.setValue("remote/token", "rtok")
    assert keystore.migrate(settings) == [] and settings.value("remote/token") == "rtok"
    assert not keystore.put(settings, "remote/token", "new") and keystore.get(settings, "remote/token") == "new"
    assert "no key store" in keystore.where()
    keystore.reset()


# ---- the analysis whose server was lost -----------------------------------------------------------------
def test_lost_server_ends_the_analysis_and_can_queue_it_again(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from hertzon.gui import solve
    QApplication.instance() or QApplication([])
    sm = solve.SolveManager()
    msgs = []
    sm.message.connect(lambda level, text: msgs.append((level, text)))
    job = solve.Job(str(tmp_path / "p.hzn"), "D", "Setup1")
    monkeypatch.setattr(solve.Job, "out", property(lambda self: tmp_path / "out"))
    (tmp_path / "out").mkdir()
    sm.job, sm._aborted, sm.requeue_lost = job, False, True
    monkeypatch.setattr(sm, "_next", lambda: None)
    sm._downloading = SimpleNamespace(lost="deleted by its watchdog: idle")
    sm._done(solve.SERVER_LOST, QProcess.NormalExit)
    assert any(level == "error" and "cloud server was lost (deleted by its watchdog: idle)" in text
               for level, text in msgs)
    st = json.loads((tmp_path / "out" / "status.json").read_text())
    assert st["error"] == "aborted: cloud server lost"
    assert [j.requeued for j in sm.queue] == [1]
    sm.job = sm.queue.pop()                                    # lost again: not queued a third time
    sm._done(solve.SERVER_LOST, QProcess.NormalExit)
    assert sm.queue == []


def test_previous_need(tmp_path):
    from hertzon.gui.solve import BYTES_PER_TET, previous_need
    assert previous_need(tmp_path) == (0.0, 0)
    (tmp_path / "convergence.json").write_text(json.dumps({"passes": [{"tets": 1000}, {"tets": 300_000}],
                                                           "final_tets": 290_000}))
    need, tets = previous_need(tmp_path)
    assert tets == 300_000 and need == 300_000 * BYTES_PER_TET and need > 32 * 2 ** 30


# ---- closing a window --------------------------------------------------------------------------------------
@needs_keygen
def test_closing_window_keeps_server_of_other_windows(api, tmp_path):  # noqa: F811
    from hertzon.gui.main_window import MainWindow
    session = CloudSession(Hetzner("good", api), "ccx33", "fsn1", workdir=tmp_path)
    st = session._create(lambda text: None)
    win = SimpleNamespace(_cloud=(None, session), _cloud_owner=lambda: "w1", _cloud_jobs=lambda: 0,
                          settings=SimpleNamespace(value=lambda k, d=None, **kw: d))
    session.leases.hold("w2", jobs=1)                       # another window runs an analysis there
    assert MainWindow._cloud_close_decision(win) == "keep"
    MainWindow._release_cloud(win, "keep")
    assert st["id"] in FakeHetzner.servers and session.log.read()[-1]["event"] == "kept"
    session.leases.drop("w2")
    assert MainWindow._cloud_close_decision(win) == "delete"            # the last window, nothing runs
    MainWindow._release_cloud(win, "delete")
    deadline = time.monotonic() + 5
    while st["id"] in FakeHetzner.servers and time.monotonic() < deadline:
        time.sleep(0.05)
    assert st["id"] not in FakeHetzner.servers
    assert session.log.last(st["id"])["reason"] == "the last Hertzon window that used it closed"


def test_remote_run_ends_with_server_lost(tmp_path):
    """The events of a job fail with ServerLost: the run ends at once (no 10 minutes of retries) with SERVER_LOST."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from hertzon import __version__
    from hertzon.gui import solve
    from hertzon.gui.remote_run import RemoteRun
    QApplication.instance() or QApplication([])

    class Host:
        address = "cloud"

        def info(self):
            return {"hertzon": __version__, "cores": 8}

        def submit(self, archive, meta):
            return {"id": "j1"}

        def events(self, jid, since):
            raise ServerLost("hertzon-abc", "deleted by its watchdog: idle")

        def abort(self, jid):
            raise AssertionError("a lost server is not asked to abort")

    project = tmp_path / "p.hzn"
    project.write_text("{}")
    r = RemoteRun(Host(), "run", str(project), "D", "Setup1", tmp_path / "p.hzn_results" / "D" / "Setup1", {})
    notes, codes = [], []
    r.note.connect(lambda level, text: notes.append((level, text)))
    r.ended.connect(codes.append)
    t0 = time.monotonic()
    r._run()
    assert codes == [solve.SERVER_LOST] and time.monotonic() - t0 < 10
    assert r.lost == "deleted by its watchdog: idle"
    assert any(level == "error" and "hertzon-abc" in text for level, text in notes)
