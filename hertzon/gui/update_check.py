"""Update hint: at start Hertzon asks GitHub for the newest release and, when it is newer than the running
version, says so in the Message Manager and the status bar and shows the update dialog (version, release
notes, Install / Skip this version / Later), at most once every PAUSE_DAYS days because releases are frequent. Runs in a background thread, never blocks the start, and stays
silent when there is no network or GitHub does not answer. Switched off in Tools > Options > General or with
the environment variable HERTZON_NO_UPDATE_CHECK=1 (which also blocks Help > Check for Updates).
Nothing is installed without a click on Install."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)

REPO = "c8501024/Hertzon"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
SETTING = "updates/check_on_start"
SKIP_SETTING = "updates/skipped_version"   # "Skip this version": no dialog until a newer one is out
SHOWN_SETTING = "updates/dialog_shown"     # when the dialog came up by itself last (ISO date)
PAUSE_DAYS = 7   # Hertzon is released often; the dialog comes at most this often, the status bar always says it
TIMEOUT = 5.0

# Hertzon releases are tagged v<major>.<minor>.<patch>; the same repository also publishes other releases
# (the EMerge wheels, tag emerge-<sha>), so GitHub's "latest release" is not necessarily a Hertzon version.
_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """(major, minor, patch) of a release tag or version string, None for anything else."""
    m = _TAG.match((text or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def newest_release(releases: list[dict]) -> dict | None:
    """The release with the highest Hertzon version among published, non-pre-releases."""
    best, best_v = None, None
    for r in releases:
        if r.get("draft") or r.get("prerelease"):
            continue
        v = parse_version(r.get("tag_name", ""))
        if v is not None and (best_v is None or v > best_v):
            best, best_v = r, v
    return best


def newer_release(releases: list[dict], current: str) -> dict | None:
    """The newest release if it is newer than the version `current`, else None."""
    r = newest_release(releases)
    cur = parse_version(current)
    if r is None or cur is None:
        return None
    return r if parse_version(r["tag_name"]) > cur else None


def notes_since(releases: list[dict], current: str, newest: dict) -> str:
    """Release notes (Markdown) of every Hertzon release after `current` up to `newest`, newest first, so
    an update over several versions shows everything that changes."""
    cur, top = parse_version(current), parse_version(newest.get("tag_name", ""))
    rs = []
    for r in releases:
        v = parse_version(r.get("tag_name", ""))
        if r.get("draft") or r.get("prerelease") or v is None or top is None or v > top:
            continue
        if (cur is None and r is not newest) or (cur is not None and v <= cur):
            continue
        rs.append((v, r))
    parts = [f"## Hertzon {'.'.join(map(str, v))}\n\n{(r.get('body') or '').strip()}"
             for v, r in sorted(rs, key=lambda t: t[0], reverse=True)]
    return "\n\n".join(parts)


def is_skipped(version: str, skipped: str) -> bool:
    """True when the user chose "Skip this version" for `version` or a newer one."""
    v, sk = parse_version(version), parse_version(skipped or "")
    return v is not None and sk is not None and v <= sk


def dialog_due(last_shown: str, today: date, days: int = PAUSE_DAYS) -> bool:
    """True when the dialog at start may come up again: never shown, or at least `days` days ago."""
    try:
        return (today - date.fromisoformat(last_shown)).days >= days
    except (TypeError, ValueError):
        return True


def installer_asset(release: dict) -> dict | None:
    """The Windows installer (Hertzon-Setup-<version>.exe) among the release's files."""
    for a in release.get("assets") or []:
        if re.fullmatch(r"Hertzon-Setup-[\d.]+\.exe", a.get("name", "")):
            return a
    return None


def installed_copy() -> Path | None:
    """Folder of the installed Hertzon (Windows installer: Hertzon.exe and Uninstall.exe next to the
    package), None for a pip installation, a source checkout or Linux, where Install opens the release page."""
    if sys.platform != "win32":
        return None
    root = Path(__file__).resolve().parents[2]
    return root if (root / "Hertzon.exe").is_file() and (root / "Uninstall.exe").is_file() else None


def download(url: str, dest: Path, digest: str = "", progress=None, cancelled=lambda: False,
             timeout: float = 30.0) -> Path | None:
    """Download `url` to `dest`; `progress(done, total)` while it runs. Checks the sha256 that GitHub
    publishes for the file (`digest` "sha256:..."); returns None when cancelled, raises on errors."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Hertzon update"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as f:
        total, done = int(resp.headers.get("Content-Length") or 0), 0
        while True:
            if cancelled():
                f.close()
                part.unlink(missing_ok=True)
                return None
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)
    if digest.startswith("sha256:") and h.hexdigest() != digest.split(":", 1)[1].lower():
        part.unlink(missing_ok=True)
        raise ValueError("the downloaded installer does not match the checksum of the release")
    part.replace(dest)
    return dest


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def installer_waiter(installer: Path, pid: int, timeout_s: int = 120, page: str = RELEASES_PAGE) -> str:
    """PowerShell script that waits until the Hertzon process `pid` has exited (at most `timeout_s`
    seconds) and then starts `installer`. Started by Hertzon itself, the installer would otherwise come up
    while Hertzon is still closing and still holds files of .venv open, and it would run as a child of
    that process.

    The script runs without a window, so it writes what it does to update.log next to the installer. When
    the installer is gone (virus protection may remove a freshly downloaded program) or cannot be started,
    it says so in a message box with the path and offers the release page; before, Hertzon simply closed
    and nothing happened."""
    path = _ps_quote(installer)
    return (f"$inst = {path}; $log = Join-Path (Split-Path -Parent $inst) 'update.log'; "
            "function L($m) { try { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $m) } catch {} }; "
            f"L ('waiting for Hertzon (process {int(pid)}) to close'); "
            f"$p = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
            f"if ($p) {{ $null = $p.WaitForExit({int(timeout_s) * 1000}) }}; "
            "$err = $null; "
            "if (-not (Test-Path -LiteralPath $inst)) { "
            "$err = 'The downloaded installer is no longer there (virus protection may have removed it):' } "
            "else { try { Start-Process -FilePath $inst -ErrorAction Stop; L ('started ' + $inst) } "
            "catch { $err = 'The installer could not be started: ' + $_.Exception.Message } }; "
            "if ($err) { L ($err + ' ' + $inst); "
            "$msg = $err + [char]10 + $inst + [char]10 + [char]10 + "
            "'Open the release page to download the installer there?'; "
            "try { $a = (New-Object -ComObject WScript.Shell).Popup($msg, 0, 'Hertzon Update', 0x34) } "
            "catch { $a = 6 }; "
            f"if ($a -eq 6) {{ Start-Process {_ps_quote(page)} }} }}")


def start_installer_after_exit(installer: Path, pid: int | None = None, page: str = RELEASES_PAGE) -> None:
    """Start `installer` once this process has exited, through a hidden PowerShell that does not belong
    to Hertzon (raises OSError when it cannot be started)."""
    script = installer_waiter(installer, os.getpid() if pid is None else pid, page=page)
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                      "-EncodedCommand", enc], **kw)


def download_dir() -> Path:
    """Where the installer is downloaded to: %LOCALAPPDATA%\\Hertzon\\Updates on Windows (cleaning tools and
    Storage Sense empty the temporary folder), otherwise the temporary folder."""
    base = os.environ.get("LOCALAPPDATA", "") if sys.platform == "win32" else ""
    return Path(base) / "Hertzon" / "Updates" if base else Path(tempfile.gettempdir()) / "Hertzon-Update"


def still_there(path: Path, size: int, wait_s: float = 1.5) -> bool:
    """True when the downloaded installer is still in place after `wait_s` seconds with its full size.
    Virus protection scans a new program when it is written and may remove it at once; Hertzon must not
    close for an installer that is gone."""
    import time
    time.sleep(wait_s)
    try:
        return path.is_file() and (size <= 0 or path.stat().st_size == size)
    except OSError:
        return False


def fetch_releases(timeout: float = TIMEOUT) -> list[dict]:
    req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                        "User-Agent": "Hertzon update check"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def disabled_by_env() -> bool:
    return os.environ.get("HERTZON_NO_UPDATE_CHECK", "").strip() not in ("", "0")


class UpdateChecker(QObject):
    """Checks once in a daemon thread; the signals arrive in the GUI thread. `available(release)` carries
    the newest release with "version", "url" and "notes" added; `up_to_date()` and `failed(reason)` are
    only of interest for Help > Check for Updates (the check at start stays silent)."""
    available = Signal(dict)
    up_to_date = Signal()
    failed = Signal(str)

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self.current = current

    def start(self) -> None:
        threading.Thread(target=self._run, name="hertzon-update-check", daemon=True).start()

    def _run(self) -> None:
        try:
            rs = fetch_releases()
        except Exception as e:  # noqa: BLE001 - offline, proxy, rate limit, bad answer: no hint
            self.failed.emit(str(e) or type(e).__name__)
            return
        r = newer_release(rs, self.current)
        if r is None:
            self.up_to_date.emit()
            return
        self.available.emit(dict(r, version=r["tag_name"].lstrip("v"), url=r.get("html_url") or RELEASES_PAGE,
                                 notes=notes_since(rs, self.current, r)))


class UpdateDialog(QDialog):
    """New version: version, release notes and Install / Skip this version / Later. Not modal, so it never
    holds up the start or the work; `choice` is "install", "skip" or "later" once a button is clicked."""
    chosen = Signal(str)

    def __init__(self, release: dict, current: str, can_install: bool, parent=None):
        super().__init__(parent)
        self.release = release
        self.setWindowTitle("Hertzon Update")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(620, 480)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>Hertzon {release['version']} is available.</b> You are using {current}.")
        lay.addWidget(head)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.get("notes") or "No release notes.")
        lay.addWidget(notes, 1)
        how = ("Install downloads the installer, asks to save open changes, closes Hertzon and starts the "
               "installer. Your settings and the Python environment are kept."
               if can_install else
               "Download opens the release page with the installers; install the new version from there.")
        foot = QLabel(f"{how}<br><a href='{release['url']}'>Release page</a> · The check at start can be "
                      "switched off in Tools &gt; Options &gt; General.")
        foot.setWordWrap(True)
        foot.setOpenExternalLinks(True)
        lay.addWidget(foot)
        bb = QDialogButtonBox()
        self.b_install = bb.addButton("Install" if can_install else "Download", QDialogButtonBox.AcceptRole)
        self.b_skip = bb.addButton("Skip this version", QDialogButtonBox.DestructiveRole)
        self.b_later = bb.addButton("Later", QDialogButtonBox.RejectRole)
        self.b_skip.setToolTip("No more reminders for this version; a newer one is shown again.")
        self.b_later.setToolTip(f"Reminds you again in {PAUSE_DAYS} days.")
        self.b_install.setDefault(True)
        for b, c in ((self.b_install, "install"), (self.b_skip, "skip"), (self.b_later, "later")):
            b.clicked.connect(lambda _=False, c=c: self._choose(c))
        lay.addWidget(bb)
        self.choice = "later"

    def _choose(self, c: str) -> None:
        self.choice = c
        self.chosen.emit(c)
        self.accept() if c == "install" else self.reject()
