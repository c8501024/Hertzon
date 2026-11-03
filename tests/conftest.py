"""The tests need the EMerge commit Hertzon is pinned to (packaging/emerge-commit). An environment set up
before the pin changed still has the old EMerge and fails in confusing places, so check it first."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("HERTZON_NO_UPDATE_CHECK", "1")   # no GitHub request from the main windows the tests open


def _pinned() -> str:
    lines = (ROOT / "packaging" / "emerge-commit").read_text(encoding="utf-8").splitlines()
    return next(s.strip() for s in lines if s.strip() and not s.startswith("#"))


def emerge_source() -> tuple[str, bool | None]:
    """Where the installed EMerge comes from, and whether that is the pinned commit (None: a local build,
    which cannot be checked)."""
    from importlib.metadata import PackageNotFoundError, distribution
    sha = _pinned()
    try:
        dist = distribution("emerge")
    except PackageNotFoundError:
        return "not installed", False
    try:
        url = json.loads(dist.read_text("direct_url.json") or "{}")
    except (OSError, ValueError):
        url = {}
    commit = (url.get("vcs_info") or {}).get("commit_id")
    if commit:
        return f"git {commit[:7]}", commit == sha
    if "dir_info" in url:
        return f"local build {url.get('url', '')}", None
    if url.get("url"):
        return url["url"].rsplit("/", 2)[-2], f"/emerge-{sha[:7]}/" in url["url"]
    return f"{dist.version} from PyPI", False


def pytest_report_header(config):
    src, ok = emerge_source()
    return f"EMerge: {src} (pinned {_pinned()[:7]})"


def pytest_sessionstart(session):
    src, ok = emerge_source()
    if ok is False and os.environ.get("HERTZON_ANY_EMERGE") != "1":
        pytest.exit(f"EMerge is {src}, not the pinned commit {_pinned()[:7]} (packaging/emerge-commit). "
                    'Update the environment with: pip install -e ".[test]"  '
                    "(HERTZON_ANY_EMERGE=1 runs the tests anyway)", returncode=4)


_exit_status = 0


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Leave without Python's own shutdown once pytest has reported. The main windows the tests open (Qt,
    VTK, the web view of the formula editor) can crash while the interpreter tears them down, which turns a
    green run into exit code 139. The report is complete at this point, so end with pytest's exit status."""
    if os.environ.get("HERTZON_TEST_SOFT_EXIT") == "1":
        return
    import sys
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)
