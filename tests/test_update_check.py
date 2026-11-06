"""Update hint at start: which release counts as newer, silence without network, skipped versions, the
release notes over several versions, the installer download and the update dialog."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from hertzon.gui import update_check as U  # noqa: E402


def rel(tag, draft=False, pre=False, body=""):
    return {"tag_name": tag, "draft": draft, "prerelease": pre, "html_url": f"https://example.org/{tag}",
            "body": body}


def test_parse_version():
    assert U.parse_version("v0.2.0") == (0, 2, 0)
    assert U.parse_version("0.10.3") == (0, 10, 3)
    assert U.parse_version("emerge-e03a085") is None
    assert U.parse_version("v0.2.0rc1") is None


def test_newest_skips_other_releases():
    # the EMerge wheel releases are published in the same repository and are often the most recent ones
    rs = [rel("emerge-e03a085"), rel("v0.3.0", draft=True), rel("v0.2.5", pre=True), rel("v0.1.25"),
          rel("v0.2.0"), rel("v0.1.9")]
    assert U.newest_release(rs)["tag_name"] == "v0.2.0"


def test_newer_release():
    rs = [rel("v0.1.25"), rel("v0.2.0"), rel("v0.10.0")]
    assert U.newer_release(rs, "0.2.0")["tag_name"] == "v0.10.0"
    assert U.newer_release(rs, "0.10.0") is None
    assert U.newer_release(rs, "0.11.0") is None
    assert U.newer_release([], "0.2.0") is None
    assert U.newer_release(rs, "0.2.0.dev3") is None


def test_offline_is_silent(monkeypatch):
    def fail(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(U, "fetch_releases", fail)
    c = U.UpdateChecker("0.2.0")
    got = []
    c.available.connect(lambda *a: got.append(a))
    c._run()
    assert got == []


def test_emits_newer(monkeypatch):
    monkeypatch.setattr(U, "fetch_releases", lambda: [rel("v0.2.0"), rel("v0.2.1", body="- fix")])
    c = U.UpdateChecker("0.2.0")
    got, ok = [], []
    c.available.connect(got.append)
    c.up_to_date.connect(lambda: ok.append(True))
    c._run()
    assert [(r["version"], r["url"]) for r in got] == [("0.2.1", "https://example.org/v0.2.1")]
    assert "- fix" in got[0]["notes"] and ok == []


def test_up_to_date_and_failed(monkeypatch):
    # Help > Check for Updates answers also when there is nothing new or GitHub cannot be reached
    monkeypatch.setattr(U, "fetch_releases", lambda: [rel("v0.2.0")])
    c = U.UpdateChecker("0.2.0")
    ok, failed = [], []
    c.up_to_date.connect(lambda: ok.append(True))
    c.failed.connect(failed.append)
    c._run()
    assert ok == [True] and failed == []

    def fail(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(U, "fetch_releases", fail)
    c._run()
    assert failed == ["no network"]


def test_notes_over_several_versions():
    rs = [rel("v0.2.3", body="three"), rel("v0.2.1", body="one"), rel("v0.2.2", body="two"),
          rel("v0.2.0", body="zero"), rel("v0.2.4", pre=True, body="pre"), rel("emerge-abc", body="wheels")]
    notes = U.notes_since(rs, "0.2.0", U.newest_release(rs))
    assert [ln for ln in notes.splitlines() if ln.startswith("## ")] == \
        ["## Hertzon 0.2.3", "## Hertzon 0.2.2", "## Hertzon 0.2.1"]
    assert "zero" not in notes and "pre" not in notes and "wheels" not in notes
    # a development version: only the newest release
    assert U.notes_since(rs, "0.2.0.dev1", rs[0]) == "## Hertzon 0.2.3\n\nthree"


def test_skipped_version():
    assert U.is_skipped("0.2.4", "0.2.4")
    assert not U.is_skipped("0.2.5", "0.2.4")    # a newer version asks again
    assert not U.is_skipped("0.2.4", "")
    assert U.is_skipped("0.2.3", "0.2.4")


def test_dialog_pause():
    from datetime import date
    today = date(2026, 10, 26)
    assert U.dialog_due("", today)                       # never shown
    assert U.dialog_due("garbage", today)
    assert not U.dialog_due("2026-10-26", today)         # several releases a day: one dialog
    assert not U.dialog_due("2026-10-20", today)
    assert U.dialog_due("2026-10-19", today)             # 7 days later again


def test_installer_asset():
    r = {"assets": [{"name": "Hertzon-0.2.3-linux.tar.gz"}, {"name": "Hertzon-Setup-0.2.3.exe", "x": 1}]}
    assert U.installer_asset(r)["x"] == 1
    assert U.installer_asset({"assets": [{"name": "emerge-3.0-win_amd64.whl"}]}) is None


def test_installed_copy_only_on_windows(monkeypatch):
    monkeypatch.setattr(U.sys, "platform", "linux")
    assert U.installed_copy() is None
    monkeypatch.setattr(U.sys, "platform", "win32")
    assert U.installed_copy() is None   # a source checkout has no Uninstall.exe next to the package


def test_download_checks_digest(tmp_path):
    import hashlib
    src = tmp_path / "Hertzon-Setup-9.9.9.exe"
    src.write_bytes(b"installer" * 1000)
    good = "sha256:" + hashlib.sha256(src.read_bytes()).hexdigest()
    seen = []
    out = U.download(src.as_uri(), tmp_path / "dl" / src.name, good, progress=lambda d, t: seen.append(d))
    assert out.read_bytes() == src.read_bytes() and seen[-1] == 9000
    with pytest.raises(ValueError):
        U.download(src.as_uri(), tmp_path / "bad" / src.name, "sha256:" + "0" * 64)
    assert not (tmp_path / "bad" / src.name).exists()
    assert U.download(src.as_uri(), tmp_path / "c" / src.name, cancelled=lambda: True) is None
    assert list((tmp_path / "c").iterdir()) == []


@pytest.mark.parametrize("button,choice", [("b_install", "install"), ("b_skip", "skip"), ("b_later", "later")])
def test_dialog_buttons(button, choice):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    r = dict(rel("v0.2.4"), version="0.2.4", url="https://example.org/v0.2.4", notes="## Hertzon 0.2.4\n\n- new")
    dlg = U.UpdateDialog(r, "0.2.3", can_install=True)
    assert dlg.b_install.text() == "Install"
    got = []
    dlg.chosen.connect(got.append)
    getattr(dlg, button).click()
    assert got == [choice]
    assert U.UpdateDialog(r, "0.2.3", can_install=False).b_install.text() == "Download"


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("HERTZON_NO_UPDATE_CHECK", "1")
    assert U.disabled_by_env()
    monkeypatch.setenv("HERTZON_NO_UPDATE_CHECK", "0")
    assert not U.disabled_by_env()


def test_installer_waits_for_hertzon_to_exit(tmp_path):
    # started by Hertzon itself the installer came up while Hertzon was still closing and still held files of
    # .venv open; it now starts only once the Hertzon process is gone (or after the timeout)
    inst = tmp_path / "it's here" / "Hertzon-Setup-0.2.14.exe"
    s = U.installer_waiter(inst, 4321, timeout_s=90)
    assert "Get-Process -Id 4321" in s and "WaitForExit(90000)" in s
    assert s.index("WaitForExit") < s.index("Start-Process")
    assert "'" + str(inst).replace("'", "''") + "'" in s     # quotes in the path are escaped
