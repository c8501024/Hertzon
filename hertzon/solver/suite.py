"""Hertzon Bench: a fixed suite of models that measures how fast this computer solves typical problems, as
one overall score and a score per test, comparable between computers and Hertzon versions.

python -m hertzon.solver.suite OUTDIR [--solver NAME] [--cores N] [--only id1,id2]

Every test solves its whole setup (initial mesh, adaptive passes, sweep or far field) once, in a fresh solver
process, with all cores (or --cores) and the setup's matrix solver (or --solver). Each result is checked
against a reference (S-parameters, or the exact Mie solution for the sphere): a run with a wrong result gets
no score. Score of a test = reference time / measured time x 1000; the overall score is the geometric mean
of the test scores, so no single test dominates. The result is OUTDIR/suite.json; OUTDIR/ABORT stops it.

The models and reference values belong to the suite version (SUITE): when a test changes, the version goes
up and results of different versions are not compared.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

from .benchmark import Benchmark, emit, log, run_summary
from .results import ResultStore, running_status

ROOT = Path(__file__).resolve().parents[2]
SUITE_DIR = ROOT / "examples" / "benchmark" / "suite"
SUITE = "Hertzon Bench 1"
SCHEMA = 1

# id, title, file, design, setup, what it tests
TESTS = [
    ("microstrip", "Microstrip line", "microstrip.hzn", "Microstrip50", "Setup1",
     "Two lumped ports, first order, 1-6 GHz sweep: start-up and small matrices"),
    ("hairpin", "Hairpin filter", "hairpin.hzn", "Hairpin_N3", "Setup1",
     "Three resonators on 0.508 mm, second order, interpolating sweep"),
    ("patch", "Patch antenna", "patch.hzn", "Patch245", "Setup1",
     "Radiation boundary, far field, broadband 1.2-3.7 GHz sweep"),
    ("patch_thin", "Patch on 0.1 mm", "patch_thin.hzn", "Patch245", "Setup1",
     "Very thin substrate: the mesh of thin layers"),
    ("waveguide", "Waveguide post", "waveguide.hzn", "WR90_Post", "Setup1",
     "WR90 wave ports, 8.2-12.4 GHz sweep"),
    ("sphere_rcs", "Sphere RCS", "sphere_rcs.hzn", "PECSphere", "Setup1",
     "Incident plane wave, monostatic RCS of a PEC sphere (checked against the exact Mie solution)"),
]
TEST_IDS = [t[0] for t in TESTS]

# reference time (s) of every test = score 1000, measured on the reference computer (REFERENCE_SYSTEM)
# with Hertzon 0.4.6; and the reference result each run is checked against
REFERENCE_SYSTEM = "4-core cloud server (Intel Xeon 2.1 GHz, 16 GB), Linux, Hertzon 0.4.6"
REFERENCE_FILE = SUITE_DIR / "reference.json"
DS_LIMIT = 0.05          # largest |S - S_ref| over the reference frequencies
RCS_LIMIT_DB = 0.5       # largest RCS error against the Mie solution


def file_hash(path: Path) -> str:
    """Hash of a model file, the same with Windows and Unix line ends (git may check out either)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def reference() -> dict:
    try:
        return json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mie_rcs(radius: float, freq: float) -> float:
    """Monostatic RCS (m^2) of a PEC sphere, Mie series (Bohren & Huffman 4.82)."""
    import numpy as np
    from scipy.special import spherical_jn, spherical_yn
    x = 2 * np.pi * freq / 299792458.0 * radius
    n = np.arange(1, int(x + 4 * x ** (1 / 3) + 20))
    j, y = spherical_jn(n, x), spherical_yn(n, x)
    jd, yd = spherical_jn(n, x, True), spherical_yn(n, x, True)
    psi, xi = x * j, x * (j + 1j * y)
    psid, xid = j + x * jd, (j + 1j * y) + x * (jd + 1j * yd)
    an, bn = psid / xid, psi / xi
    qb = abs(np.sum((2 * n + 1) * (-1) ** n * (an - bn))) ** 2 / x ** 2
    return float(qb * np.pi * radius ** 2)


def check_rcs(folder: Path, project: Path, design: str) -> dict:
    """RCS error (dB) of the sphere against Mie."""
    from ..model.design import Project
    from .ffquant import monostatic_rcs
    p = Project.load(str(project))
    d = p.design(design)
    radius = d.evaluator(p).value("R")               # m
    st = ResultStore(folder)
    sph = d.spheres[0].name
    r = monostatic_rcs(st, "LastAdaptive", sph)
    f, s = float(r["freq"][0]), float(r["MonostaticRCSTotal"][0])
    err = 10 * math.log10(s / mie_rcs(radius, f)) if s > 0 else float("inf")
    return {"kind": "rcs_db", "value": round(err, 4), "limit": RCS_LIMIT_DB, "ok": abs(err) <= RCS_LIMIT_DB}


def sparams_points(folder: Path, sweep: str, n: int = 11) -> dict | None:
    """S-parameters of a sweep at n frequencies spread over it, for the reference file."""
    import numpy as np
    d = ResultStore(folder).sparams(sweep)
    if not d:
        return None
    f = np.asarray(d["freq"], float)
    idx = np.unique(np.linspace(0, len(f) - 1, n).round().astype(int))
    S = np.asarray(d["S"])[idx]
    return {"freq": f[idx].tolist(), "re": S.real.round(6).tolist(), "im": S.imag.round(6).tolist()}


def check_sparams(folder: Path, sweep: str, ref: dict | None) -> dict:
    """Largest |S - S_ref| at the reference frequencies."""
    import numpy as np
    if not ref:
        return {"kind": "ds", "value": None, "limit": DS_LIMIT, "ok": True, "note": "no reference"}
    d = ResultStore(folder).sparams(sweep)
    if not d:
        return {"kind": "ds", "value": None, "limit": DS_LIMIT, "ok": False, "note": "no S-parameters"}
    f = np.asarray(d["freq"], float)
    S = np.asarray(d["S"])
    Sref = np.asarray(ref["re"]) + 1j * np.asarray(ref["im"])
    worst = 0.0
    for k, fr in enumerate(ref["freq"]):
        i = int(np.argmin(abs(f - fr)))
        if abs(f[i] - fr) > 1e-6 * fr:
            # interpolate between the neighbours (a sweep with other points)
            Si = np.array([[np.interp(fr, f, S[:, a, b].real) + 1j * np.interp(fr, f, S[:, a, b].imag)
                            for b in range(S.shape[2])] for a in range(S.shape[1])])
        else:
            Si = S[i]
        if Si.shape != Sref[k].shape:
            return {"kind": "ds", "value": None, "limit": DS_LIMIT, "ok": False, "note": "other ports"}
        worst = max(worst, float(np.max(np.abs(Si - Sref[k]))))
    return {"kind": "ds", "value": round(worst, 5), "limit": DS_LIMIT, "ok": worst <= DS_LIMIT}


def score(ref_time: float | None, t: float) -> int | None:
    return int(round(1000 * ref_time / t)) if ref_time and t > 0 else None


def overall(scores: list[int | None]) -> int | None:
    """Geometric mean of the test scores; None unless every test has one."""
    if not scores or any(s is None or s <= 0 for s in scores):
        return None
    return int(round(math.exp(sum(math.log(s) for s in scores) / len(scores))))


class Suite:
    def __init__(self, out: Path, solver: str = "", cores: int = 0, only: list[str] | None = None,
                 make_reference: bool = False):
        self.out = out
        self.solver = solver
        self.cores = cores or os.cpu_count() or 1
        self.tests = [t for t in TESTS if not only or t[0] in only]
        self.make_reference = make_reference
        self.ref = reference()
        self.t0 = time.time()
        self.result = {"schema": SCHEMA, "suite": SUITE, "run_id": uuid.uuid4().hex, "cores": self.cores,
                       "solver": solver or "Auto", "started": time.time(), "complete": False, "tests": []}

    def aborted(self) -> bool:
        return (self.out / "ABORT").exists()

    def _write(self) -> None:
        ResultStore(self.out).write_json("suite.json", self.result)

    def run(self) -> bool:
        from .bridge import _first_run
        (self.out / "ABORT").unlink(missing_ok=True)
        if _first_run():
            log("Compiling the solver kernels first, so that no test includes it")
            from .warmup import run as warmup
            warmup(lambda s: None)
        log(f"{SUITE}: {len(self.tests)} tests with {self.cores} cores"
            + (f", solver {self.solver}" if self.solver else ""))
        n = len(self.tests)
        new_ref = {"suite": SUITE, "system": REFERENCE_SYSTEM, "times": {}, "tets": {}, "sparams": {}} \
            if self.make_reference else None
        for k, (tid, title, file, design, setup, _what) in enumerate(self.tests, start=1):
            src = SUITE_DIR / file
            b = Benchmark(str(src), design, setup, self.out, [self.cores], [], [self.solver] if self.solver else [])
            b.aborted = self.aborted
            path = b._project(f"test_{tid}", solver=self.solver)
            folder = self.out / "runs" / tid
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            try:
                if not b._solve(path, folder, b._env(self.cores, 1), title, k, n):
                    return self._stop()
            except RuntimeError as ex:
                row = {"id": tid, "file_hash": file_hash(src), "failed": str(ex)[:300]}
                self.result["tests"].append(row)
                self._write()
                log(f"{title}: failed ({ex})", "error")
                continue
            s = run_summary(folder, b.sweeps)
            row = {"id": tid, "file_hash": file_hash(src),
                   **{key: s[key] for key in ("total", "start", "mesh", "adaptive", "sweep", "passes", "tets",
                                              "memory_mb", "converged", "notes")}}
            if tid == "sphere_rcs":
                try:
                    row["check"] = check_rcs(folder, path, design)
                except Exception as ex:  # noqa: BLE001
                    row["check"] = {"kind": "rcs_db", "value": None, "ok": False, "note": str(ex)[:200]}
            else:
                sweep = b.sweeps[0] if b.sweeps else ""
                if new_ref is not None and sweep:
                    new_ref["sparams"][tid] = sparams_points(folder, sweep)
                row["check"] = check_sparams(folder, sweep, (self.ref.get("sparams") or {}).get(tid))
            if new_ref is not None:
                new_ref["times"][tid] = round(s["total"], 2)
                new_ref["tets"][tid] = s["tets"]
            ref_t = (self.ref.get("times") or {}).get(tid)
            row["score"] = score(ref_t, s["total"]) if row["check"]["ok"] else None
            self.result["tests"].append(row)
            self._write()
            log(f"{title}: {s['total']:.1f} s, {s['passes']} passes, {s['tets']} tetrahedra, "
                f"{s['memory_mb'] / 1024:.1f} GB" + (f", score {row['score']}" if row["score"] else "")
                + ("" if row["check"]["ok"] else f"; result check failed ({row['check']})"))
        self.result["score"] = overall([t.get("score") for t in self.result["tests"]]) \
            if len(self.tests) == len(TESTS) else None
        self.result["complete"] = len(self.tests) == len(TESTS) and all(t.get("score") for t in self.result["tests"])
        self.result["finished"] = time.time()
        self._write()
        if new_ref is not None:
            (self.out / "reference.json").write_text(json.dumps(new_ref, indent=1), encoding="utf-8")
        ResultStore(self.out).write_json("status.json", {"state": "done", "finished": time.time()})
        if self.result.get("score"):
            log(f"{SUITE} score: {self.result['score']}")
        emit("progress", value=100, text="Done")
        emit("done", elapsed=time.time() - self.t0)
        return True

    def _stop(self) -> bool:
        self._write()
        ResultStore(self.out).write_json("status.json", {"state": "aborted"})
        log("Benchmark aborted", "warning")
        return False


def site_spec() -> dict:
    """What the comparison site checks a submission of this suite against: the model of every test (its file
    hash), the reference time the score is computed from and the plausible range of the final mesh (a third
    to three times the reference, for other versions and solvers)."""
    ref = reference()
    tests = []
    for tid, _title, file, *_ in TESTS:
        tets = (ref.get("tets") or {}).get(tid) or 0
        tests.append({"id": tid, "hashes": [file_hash(SUITE_DIR / file)], "time": (ref.get("times") or {}).get(tid),
                      "tets": [int(tets / 3), int(tets * 3)] if tets else [100, 10_000_000]})
    return {SUITE: {"tests": tests, "titles": {t[0]: t[1] for t in TESTS},
                    "about": {t[0]: t[5] for t in TESTS}, "reference": REFERENCE_SYSTEM}}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    args = sys.argv[1:]
    opts = {}
    for key in ("--solver", "--cores", "--only"):
        if key in args:
            i = args.index(key)
            opts[key] = args[i + 1]
            del args[i:i + 2]
    make_ref = "--make-reference" in args
    if make_ref:
        args.remove("--make-reference")
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    try:
        ResultStore(out).write_json("status.json", running_status())
        only = [x.strip() for x in opts.get("--only", "").split(",") if x.strip()]
        ok = Suite(out, opts.get("--solver", ""), int(opts.get("--cores", 0) or 0), only, make_ref).run()
        return 0 if ok else 3
    except Exception as ex:  # noqa: BLE001
        emit("log", level="error", msg=f"{type(ex).__name__}: {ex}")
        emit("traceback", text=traceback.format_exc())
        ResultStore(out).write_json("status.json", {"state": "failed", "error": str(ex)})
        emit("failed", error=str(ex))
        return 1


if __name__ == "__main__":
    sys.exit(main())
