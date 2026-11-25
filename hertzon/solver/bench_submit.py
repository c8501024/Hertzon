"""Submitting a Hertzon Bench result to the public comparison site (BENCH_URL), and nothing else: the payload
is built here so the dialog can show exactly what is sent before anything leaves the computer.

Sent: the suite result (times, passes, tetrahedra, memory, checks, scores), the system description of
sysinfo.collect() and an optional display name. Not sent: paths, user or computer name, project files.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .suite import SCHEMA, SUITE, TEST_IDS

BENCH_URL = os.environ.get("HERTZON_BENCH_URL", "https://hertzon-bench.pages.dev")
MAX_NAME = 40
TEST_KEYS = ("id", "file_hash", "total", "start", "mesh", "adaptive", "sweep", "passes", "tets", "memory_mb",
             "converged", "check", "score", "failed")


def payload(result: dict, system: dict, name: str = "", comment: str = "") -> dict:
    """What is sent, from a suite.json and the system description."""
    tests = []
    for t in result.get("tests", []):
        row = {k: t[k] for k in TEST_KEYS if k in t}
        for k in ("total", "start", "mesh", "adaptive", "sweep", "memory_mb"):
            if isinstance(row.get(k), float):
                row[k] = round(row[k], 2)
        tests.append(row)
    return {"schema": SCHEMA, "suite": result.get("suite", SUITE), "run_id": result.get("run_id", ""),
            "measured": int(result.get("finished") or 0), "cores": result.get("cores"),
            "solver": result.get("solver", "Auto"), "score": result.get("score"), "tests": tests,
            "system": system, "name": (name or "").strip()[:MAX_NAME], "comment": (comment or "").strip()[:200]}


def can_submit(result: dict) -> str:
    """'' when the result can be submitted, else why not."""
    if result.get("suite") != SUITE:
        return "This result is from another suite version."
    ids = [t.get("id") for t in result.get("tests", [])]
    if sorted(ids) != sorted(TEST_IDS):
        return "Only a run of the whole suite can be submitted."
    if not result.get("complete"):
        return "Not every test finished with a correct result."
    return ""


def submit(data: dict, timeout: float = 30.0) -> dict:
    """POST the payload; returns the site's answer ({"id", "url", "delete_key"}) or raises RuntimeError."""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(BENCH_URL.rstrip("/") + "/api/submit", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Hertzon"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        try:
            msg = json.loads(ex.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            msg = ""
        raise RuntimeError(msg or f"The site answered {ex.code}") from ex
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise RuntimeError(f"Cannot reach {BENCH_URL}: {getattr(ex, 'reason', ex)}") from ex
