"""Update hint at start: which release counts as newer, silence without network, skipped versions, the
release notes over several versions, the installer download and the update dialog."""
from __future__ import annotations

from typing import ClassVar

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


def test_still_there(tmp_path):
    f = tmp_path / "Hertzon-Setup-0.3.5.exe"
    assert not U.still_there(f, 3, wait_s=0)
    f.write_bytes(b"abc")
    assert U.still_there(f, 3, wait_s=0)
    assert U.still_there(f, 0, wait_s=0)
    assert not U.still_there(f, 4, wait_s=0)      # cut short


def test_download_dir_not_in_temp_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(U.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert U.download_dir() == tmp_path / "Hertzon" / "Updates"
    monkeypatch.delenv("LOCALAPPDATA")
    assert U.download_dir().name == "Hertzon-Update"


class _FakeProc:
    """subprocess.Popen stand-in: `alive` says whether the started installer is still running after the check."""
    calls: ClassVar[list] = []

    def __init__(self, args, alive=True, **kw):
        self.args, self.kw, self.alive, self.pid = args, kw, alive, 4242
        _FakeProc.calls.append(self)

    def wait(self, timeout=None):
        # the installer waits for Hertzon: Hertzon must never wait for it without a short limit (deadlock)
        assert timeout is not None and 0 < timeout <= 5
        if self.alive:
            raise U.subprocess.TimeoutExpired(self.args, timeout)
        return 1


def _windows(monkeypatch, alive=True, refuse=0):
    """Pretend to run on Windows; the first `refuse` starts fail like a job object that forbids breakaway."""
    monkeypatch.setattr(U.sys, "platform", "win32")
    _FakeProc.calls = []
    left = [refuse]

    def popen(args, **kw):
        if left[0]:
            left[0] -= 1
            raise PermissionError(5, "Access is denied")
        return _FakeProc(args, alive=alive, **kw)
    monkeypatch.setattr(U.subprocess, "Popen", popen)
    monkeypatch.setattr(U, "_allow_foreground", lambda: None)


def _installer(tmp_path):
    inst = tmp_path / "Updates" / "Hertzon-Setup-9.9.9.exe"
    inst.parent.mkdir()
    inst.write_bytes(b"x")
    return inst


def test_installer_command_waits_for_hertzon():
    # the installer itself waits until Hertzon has ended; /D= (install folder) must stay the last argument
    cmd = U.installer_command(U.Path("C:/x/Hertzon-Setup-9.9.9.exe"), 4321, ("/S", "/D=C:\\H"))
    assert cmd[1:] == ["/WAITPID=4321", "/S", "/D=C:\\H"]


def test_installer_started_directly_without_powershell(monkeypatch, tmp_path):
    # Hertzon closed and nothing happened: the hidden PowerShell that was to start the installer ended at
    # once on some computers. The installer is now started directly, as a process of its own
    _windows(monkeypatch)
    inst = _installer(tmp_path)
    assert U.start_installer_after_exit(inst, pid=77) == "process"
    (p,) = _FakeProc.calls
    assert p.args == [str(inst), "/WAITPID=77"]
    assert not any("powershell" in str(a).lower() for a in p.args)
    flags = p.kw["creationflags"]
    assert flags & 0x00000008 and flags & 0x00000200 and flags & 0x01000000   # detached, own group, breakaway
    assert p.kw["close_fds"] and "stdin" not in p.kw and "stdout" not in p.kw    # no handles of Hertzon
    log = (inst.parent / "update.log").read_text(encoding="utf-8")
    assert "starting Hertzon-Setup-9.9.9.exe /WAITPID=77" in log and "running as process 4242" in log


def test_installer_waits_for_this_process_by_default(monkeypatch, tmp_path):
    # Hertzon.exe runs Python in its own process: without `pid` the installer waits for the running Hertzon
    _windows(monkeypatch)
    inst = _installer(tmp_path)
    assert U.start_installer_after_exit(inst) == "process"
    assert _FakeProc.calls[0].args[1] == f"/WAITPID={U.os.getpid()}"


def test_breakaway_refused(monkeypatch, tmp_path):
    # in a job object that does not allow breaking away, CreateProcess refuses the flag: started without it
    _windows(monkeypatch, refuse=1)
    inst = _installer(tmp_path)
    assert U.start_installer_after_exit(inst, pid=77) == "process"
    assert not _FakeProc.calls[0].kw["creationflags"] & 0x01000000
    assert "direct start refused" in (inst.parent / "update.log").read_text(encoding="utf-8")


def test_started_through_the_shell_when_refused(monkeypatch, tmp_path):
    _windows(monkeypatch, refuse=2)
    inst = _installer(tmp_path)
    started = []
    monkeypatch.setattr(U.os, "startfile", lambda path, **kw: started.append((path, kw)), raising=False)
    assert U.start_installer_after_exit(inst, pid=77) == "shell"
    assert started == [(str(inst), {"arguments": "/WAITPID=77", "cwd": str(inst.parent)})]

    def fail(path, **kw):
        raise OSError(1223, "cancelled")
    monkeypatch.setattr(U.os, "startfile", fail, raising=False)
    _windows(monkeypatch, refuse=2)
    with pytest.raises(OSError):
        U.start_installer_after_exit(inst, pid=77)


def test_installer_that_ends_at_once(monkeypatch, tmp_path):
    # it waits for Hertzon, so it must still run a second later; if not, Hertzon stays open and says so
    _windows(monkeypatch, alive=False)
    inst = _installer(tmp_path)
    with pytest.raises(OSError, match="ended right away"):
        U.start_installer_after_exit(inst, pid=77)
    assert "ended right away" in (inst.parent / "update.log").read_text(encoding="utf-8")


def test_missing_installer_is_an_error(monkeypatch, tmp_path):
    _windows(monkeypatch)
    with pytest.raises(OSError):
        U.start_installer_after_exit(tmp_path / "gone.exe", pid=1)
    assert _FakeProc.calls == []


def test_only_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(U.sys, "platform", "linux")
    with pytest.raises(OSError):
        U.start_installer_after_exit(_installer(tmp_path), pid=1)


def test_update_log_waits_while_the_installer_writes(monkeypatch, tmp_path):
    # the installer writes to the same update.log; while it has the file open, Windows refuses a second writer
    real, tries = open, []

    def busy_once(*a, **k):
        tries.append(1)
        if len(tries) == 1:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real(*a, **k)
    monkeypatch.setattr(U, "open", busy_once, raising=False)
    U.update_log(tmp_path, "starting")
    U.update_log(tmp_path, "running")
    lines = (tmp_path / "update.log").read_text(encoding="utf-8").splitlines()
    assert len(tries) == 3 and [ln.split(": ", 1)[1] for ln in lines] == ["starting", "running"]
    assert all(f"Hertzon (process {U.os.getpid()})" in ln for ln in lines)


def test_installer_script_waits_for_hertzon():
    # the other half of the update start: the installer waits for /WAITPID before it shows anything, and it
    # starts PowerShell (stop_background.ps1, setup.ps1) by its full path
    from pathlib import Path
    nsi = (Path(__file__).resolve().parents[1] / "packaging" / "installer.nsi").read_text(encoding="utf-8")
    assert '"' + U.WAIT_OPTION + '"' in nsi
    assert "kernel32::OpenProcess(i 0x00100000" in nsi and f"WaitForSingleObject(p $R1, i {U.WAIT_S * 1000})" in nsi
    assert nsi.index("Call WaitForHertzon") < nsi.index("MUI_LANGDLL_DISPLAY\nFunctionEnd")
    code = [ln.split(";")[0] for ln in nsi.splitlines()]
    assert not any("'powershell.exe" in ln or '"powershell.exe' in ln for ln in code)
