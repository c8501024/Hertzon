"""Hertzon Bench: a fixed suite of models that measures how fast this computer solves typical problems, as
a score per test, per part (RF: ports and S-parameters; RCS: incident plane wave) and overall, comparable
between computers and Hertzon versions.

python -m hertzon.solver.suite OUTDIR [--solver NAME] [--cores N] [--parts rf,rcs] [--no-large] [--scaling]
                              [--no-full] [--only id1,id2] [--make-reference]

Two benchmarks, run alone or together: the full benchmark (multi-core; --no-full leaves it out) solves every test
of the parts with all cores; the scaling benchmark (--scaling) solves one test per part with 1, 2, 4, ... up to all
cores, for the single-core scores, speedup and parallel efficiency.

Every test solves its whole setup (initial mesh, adaptive passes, sweep or far field) once, in a fresh solver
process, with all cores (or --cores) and the setup's matrix solver (or --solver). Each result is checked
against a reference (S-parameters, or the exact Mie solution for the sphere): a run with a wrong result gets
no score. Score of a test = reference time / measured time x 1000; a part score is the geometric mean of its
test scores (so no single test dominates) and needs every one of them, the overall score is the geometric mean
of the part scores and needs every part. The result is OUTDIR/suite.json; OUTDIR/ABORT stops it.

The models and reference values belong to the suite version (SUITE): when a test changes, the version goes
up and results of different versions are not compared.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

from .benchmark import Benchmark, _log_messages, default_cores, emit, log, run_summary
from .results import ResultStore, running_status

ROOT = Path(__file__).resolve().parents[2]
SUITE_DIR = ROOT / "examples" / "benchmark" / "suite"
SUITE = "Hertzon Bench 2"
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
    ("dielectric_rcs", "Dielectric sphere RCS", "dielectric_sphere_rcs.hzn", "DielectricSphere", "Setup1",
     "Incident plane wave, monostatic RCS of a lossless dielectric sphere (er = 4), fields inside and outside "
     "(checked against the exact Mie solution)"),
    ("drone_medium_rcs", "Drone RCS (medium)", "drone_medium_rcs.hzn", "Design1", "Setup1",
     "Imported 3D surface of a delta-wing drone, PEC, incident plane wave at 0.5 GHz, monostatic RCS: "
     "a realistic model that fits a computer with 16 GB"),
]
# workstation tests (more than 16 GB of memory): run only when the computer has the memory for them, with their
# own score, not in the part or overall score, so that these stay reachable for a computer with 16 GB. Every
# scored test fits 16 GB (the medium drone, the largest, needs about 6.5 GB).
LARGE_TESTS = [
    ("drone_rcs", "Drone RCS (workstation)", "drone_rcs.hzn", "Design1", "Setup1",
     "Imported 3D surface of a delta-wing drone, PEC, incident plane wave, monostatic RCS: large meshes"),
    ("rotman_array", "Rotman lens array (workstation)", "rotman_array.hzn", "RotmanArray", "Setup1",
     "24 GHz Rotman lens beamformer with its patch array on RO4350B, 11 lumped ports, second order, far field "
     "and a discrete sweep at 22, 24 and 26 GHz: a large antenna system"),
]
MIE_TESTS = ("sphere_rcs", "dielectric_rcs")          # checked against the exact Mie solution
REF_RCS_TESTS = ("drone_medium_rcs", "drone_rcs")     # checked against the RCS of the reference run
# the suite in parts, each with its own score and run on its own if wanted: RF (ports and S-parameters:
# circuits, filters, antennas, waveguides) and RCS (incident plane wave, scattering). A part has a score only
# when every one of its tests counts; the overall score needs every part, so a failed RCS test leaves the RF
# score valid. Large tests belong to a part but stay outside its score.
PARTS = [
    ("rf", "RF", ["microstrip", "hairpin", "patch", "patch_thin", "waveguide"]),
    ("rcs", "RCS", ["sphere_rcs", "dielectric_rcs", "drone_medium_rcs"]),
]
PART_IDS = [k for k, _t, _ids in PARTS]
PART_OF = {**{tid: k for k, _t, ids in PARTS for tid in ids}, "drone_rcs": "rcs", "rotman_array": "rf"}
TEST_IDS = [t[0] for t in TESTS]                       # the tests of the overall score
LARGE_IDS = [t[0] for t in LARGE_TESTS]
ALL_TESTS = TESTS + LARGE_TESTS
# matrix solvers the suite does not offer: far too slow for models of this size (SuperLU, the last fallback)
EXCLUDED_SOLVERS = ("SuperLU",)
# scaling benchmark (single- to multi-core): one test per part is solved with 1, 2, 4, ... up to all cores; the
# 1-core times give the single-core scores, the others speedup and parallel efficiency
SCALING_TESTS = {"rf": "hairpin", "rcs": "sphere_rcs"}
SCALING_TEST = SCALING_TESTS["rf"]
MEMORY_MARGIN = 1.15     # a large test runs when the usable memory is this much above its reference peak

# reference time (s) of every test = score 1000, measured on the reference computer (REFERENCE_SYSTEM)
# with Hertzon 0.4.6; and the reference result each run is checked against
REFERENCE_SYSTEM = "4-core cloud server (Intel Xeon 2.1 GHz, 16 GB), Linux, Hertzon 0.4.6"
# the large tests need more memory than that server has: their 1000 is this computer
REFERENCE_SYSTEM_LARGE = "AMD Ryzen Threadripper 3970X (32 cores, 64 threads used, 256 GB), Windows, Hertzon 0.4.6"
REFERENCE_FILE = SUITE_DIR / "reference.json"
DS_LIMIT = 0.05          # largest |S - S_ref| over the reference frequencies
RCS_LIMIT_DB = 0.5       # largest RCS error against the Mie solution
RCS_REF_LIMIT_DB = 1.0   # largest RCS difference of a drone to the reference run


def file_hash(path: Path) -> str:
    """Hash of a model file and the files it imports (e.g. an STL), the same with Windows and Unix line ends
    (git may check out either)."""
    path = Path(path)
    text = path.read_bytes().replace(b"\r\n", b"\n")
    h = hashlib.sha256(text)
    for f in sorted(set(re.findall(r'"File":\s*"([^"]+)"', text.decode("utf-8", "replace")))):
        p = path.parent / f
        if p.is_file():
            h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def reference() -> dict:
    try:
        return json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mie_rcs(radius: float, freq: float, eps: float | None = None) -> float:
    """Monostatic RCS (m^2) of a sphere, Mie series (Bohren & Huffman 4.53, 4.82): PEC without eps, else a
    lossless dielectric with relative permittivity eps (mu = 1)."""
    import numpy as np
    from scipy.special import spherical_jn, spherical_yn
    x = 2 * np.pi * freq / 299792458.0 * radius
    mx = x * (np.sqrt(eps) if eps else 1.0)
    n = np.arange(1, int(max(x, mx) + 4 * max(x, mx) ** (1 / 3) + 20))
    j, y = spherical_jn(n, x), spherical_yn(n, x)
    jd, yd = spherical_jn(n, x, True), spherical_yn(n, x, True)
    psi, xi = x * j, x * (j + 1j * y)
    psid, xid = j + x * jd, (j + 1j * y) + x * (jd + 1j * yd)
    if eps:
        m = np.sqrt(eps)
        jm, jmd = spherical_jn(n, mx), spherical_jn(n, mx, True)
        pm, pmd = mx * jm, jm + mx * jmd
        an = (m * pm * psid - psi * pmd) / (m * pm * xid - xi * pmd)
        bn = (pm * psid - m * psi * pmd) / (pm * xid - m * xi * pmd)
    else:
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
    mat = next((m for m in p.all_materials() if m.name == d.obj("Sphere").material), None)
    eps = None if mat is None or mat.pec or mat.name.lower() == "pec" else float(mat.permittivity)
    st = ResultStore(folder)
    sph = d.spheres[0].name
    r = monostatic_rcs(st, "LastAdaptive", sph)
    f, s = float(r["freq"][0]), float(r["MonostaticRCSTotal"][0])
    err = 10 * math.log10(s / mie_rcs(radius, f, eps)) if s > 0 else float("inf")
    return {"kind": "rcs_db", "value": round(err, 4), "limit": RCS_LIMIT_DB, "ok": abs(err) <= RCS_LIMIT_DB}


def rcs_dbsm(folder: Path, project: Path, design: str) -> float:
    """Monostatic RCS (dBsm) at the adaptive frequency."""
    from ..model.design import Project
    from .ffquant import monostatic_rcs
    d = Project.load(str(project)).design(design)
    r = monostatic_rcs(ResultStore(folder), "LastAdaptive", d.spheres[0].name)
    return 10 * math.log10(float(r["MonostaticRCSTotal"][0]))


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


def _this_system(cores: int) -> str:
    """The computer of a reference run, for reference.json."""
    try:
        from .sysinfo import collect, summary
        info = collect(measure=False)
        return f"{summary(info)}, {cores} cores used, Hertzon {info['software']['hertzon']}"
    except Exception:  # noqa: BLE001
        return f"{cores} cores used"


class Suite:
    def __init__(self, out: Path, solver: str = "", cores: int = 0, only: list[str] | None = None,
                 make_reference: bool = False, scaling: bool = False, parts: list[str] | None = None,
                 large: bool = True, full: bool = True):
        """full: the full (multi-core) benchmark, every test of the parts with all cores; scaling: the scaling
        benchmark (single- to multi-core), the scaling test of each part with 1, 2, 4, ... cores."""
        self.out = out
        self.solver = solver
        self.cores = cores or os.cpu_count() or 1
        self.parts = [k for k in PART_IDS if not parts or k in parts]
        self.tests = [t for t in ALL_TESTS if full and (not only or t[0] in only) and PART_OF[t[0]] in self.parts
                      and (large or t[0] not in LARGE_IDS)]
        self.scaling_parts = [k for k in self.parts if scaling and (not only or SCALING_TESTS[k] in only)]
        self.parts = [k for k in self.parts if any(PART_OF[t[0]] == k for t in self.tests)]
        self.make_reference = make_reference
        self.scaling = bool(self.scaling_parts)
        self.ref = reference()
        self.t0 = time.time()
        self.result = {"schema": SCHEMA, "suite": SUITE, "run_id": uuid.uuid4().hex, "cores": self.cores,
                       "solver": solver or "Auto", "started": time.time(), "complete": False,
                       "parts_run": self.parts, "tests": []}

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
        log(f"{SUITE}: " + " and ".join(
            x for x in (f"full benchmark, {len(self.tests)} tests with {self.cores} cores" if self.tests else "",
                        f"scaling benchmark, 1 to {self.cores} cores" if self.scaling else "") if x)
            + (f", solver {self.solver}" if self.solver else ""))
        n = len(self.tests)
        new_ref = {"suite": SUITE, "system": _this_system(self.cores), "times": {}, "tets": {}, "sparams": {}} \
            if self.make_reference else None
        for k, (tid, title, file, design, setup, _what) in enumerate(self.tests, start=1):
            src = SUITE_DIR / file
            if tid in LARGE_IDS and not self.make_reference:
                why = too_large(tid, self.ref)
                if why:
                    self.result["tests"].append({"id": tid, "file_hash": file_hash(src), "skipped": why})
                    self._write()
                    log(f"{title}: not run, {why}")
                    continue
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
                row = {"id": tid, "file_hash": file_hash(src), "counted": False,
                       "reason": failure_reason(folder, str(ex))}
                self.result["tests"].append(row)
                self._write()
                log(f"{title}: not counted, {row['reason']}", "warning")
                continue
            s = run_summary(folder, b.sweeps)
            sweep = b.sweeps[0] if b.sweeps else ""
            row = {"id": tid, "file_hash": file_hash(src),
                   **{key: s[key] for key in ("total", "start", "mesh", "adaptive", "sweep", "passes", "tets",
                                              "memory_mb", "converged", "notes")}}
            if tid in MIE_TESTS:
                try:
                    row["check"] = check_rcs(folder, path, design)
                except Exception as ex:  # noqa: BLE001
                    row["check"] = {"kind": "rcs_db", "value": None, "ok": False, "note": str(ex)[:200]}
            elif tid in REF_RCS_TESTS:
                try:
                    rcs = rcs_dbsm(folder, path, design)
                    ref_rcs = (self.ref.get("rcs_dbsm") or {}).get(tid)
                    err = rcs - ref_rcs if ref_rcs is not None else None
                    row["check"] = {"kind": "rcs_ref_db", "value": None if err is None else round(err, 3),
                                    "limit": RCS_REF_LIMIT_DB, "ok": err is None or abs(err) <= RCS_REF_LIMIT_DB,
                                    **({"note": "no reference"} if err is None else {}), "rcs_dbsm": round(rcs, 3)}
                except Exception as ex:  # noqa: BLE001
                    row["check"] = {"kind": "rcs_ref_db", "value": None, "ok": False, "note": str(ex)[:200]}
            else:
                row["check"] = check_sparams(folder, sweep, (self.ref.get("sparams") or {}).get(tid))
            reason = not_counted_reason(folder, s, row["check"], fixed_passes(src, design, setup))
            row["counted"] = not reason
            if reason:
                row["reason"] = reason
            if new_ref is not None and reason:
                log(f"{title}: no reference written, {reason}", "warning")
            elif new_ref is not None:
                # a reference only from a run that counts (converged, as asked, no memory guard)
                new_ref["times"][tid] = round(s["total"], 2)
                new_ref["tets"][tid] = s["tets"]
                new_ref.setdefault("memory_mb", {})[tid] = round(s["memory_mb"])
                if "rcs_dbsm" in row["check"]:
                    new_ref.setdefault("rcs_dbsm", {})[tid] = row["check"]["rcs_dbsm"]
                if tid not in MIE_TESTS + REF_RCS_TESTS and sweep:
                    new_ref["sparams"][tid] = sparams_points(folder, sweep)
            ref_t = (self.ref.get("times") or {}).get(tid)
            row["score"] = score(ref_t, s["total"]) if row["counted"] else None
            self.result["tests"].append(row)
            self._write()
            log(f"{title}: {s['total']:.1f} s, {s['passes']} passes, {s['tets']} tetrahedra, "
                f"{s['memory_mb'] / 1024:.1f} GB" + (f", score {row['score']}" if row["score"] else "")
                + (f"; not counted: {reason}" if reason else ""), "warning" if reason else "info")
        if self.scaling and not self._scaling(new_ref):
            return self._stop()
        if self.scaling:
            sc = self.result["scaling"]
            for k, title, _ids in PARTS:
                part = sc["parts"].get(k)
                if part:
                    log(f"{title} single-core score: {part['single_core_score']}" if part.get("single_core_score")
                        else f"{title}: no single-core score, {part.get('reason', 'no 1-core run')}")
            if sc.get("single_core_score"):
                log(f"{SUITE} single-core score: {sc['single_core_score']}")
        summarize(self.result)
        self.result["finished"] = time.time()
        self._write()
        if new_ref is not None:
            (self.out / "reference.json").write_text(json.dumps(new_ref, indent=1), encoding="utf-8")
        ResultStore(self.out).write_json("status.json", {"state": "done", "finished": time.time()})
        for k, title, _ids in PARTS:
            part = self.result["parts"].get(k)
            if part:
                log(f"{title} score: {part['score']}" if part["score"] else
                    f"{title}: no score, {part['counted']} of {part['tests']} tests count")
        if self.result.get("score"):
            log(f"{SUITE} score: {self.result['score']}")
        emit("progress", value=100, text="Done")
        emit("done", elapsed=time.time() - self.t0)
        return True

    def _scaling(self, new_ref: dict | None) -> bool:
        """Scaling benchmark: the scaling test of each part with 1, 2, 4, ... up to all cores (the all-core run
        is taken from the full benchmark when it ran): speedup and parallel efficiency, and the single-core
        score of the part from the 1-core time, when that run counts (converged, as asked, correct)."""
        cores = default_cores(self.cores)
        jobs = [(k, c) for k in self.scaling_parts for c in cores]
        sc = {"cores": self.cores, "parts": {}}
        self.result["scaling"] = sc
        done = 0
        for k in self.scaling_parts:
            tid = SCALING_TESTS[k]
            _t, title, file, design, setup, _ = next(t for t in TESTS if t[0] == tid)
            full = next((t for t in self.result["tests"] if t["id"] == tid and "total" in t), None)
            src = SUITE_DIR / file
            b = Benchmark(str(src), design, setup, self.out, [self.cores], [], [self.solver] if self.solver else [])
            b.aborted = self.aborted
            path = b._project(f"test_{tid}", solver=self.solver)
            runs, one_reason = [], "the 1-core run failed"
            for c in cores:
                done += 1
                if c == self.cores and full is not None:
                    runs.append({"cores": c, **{key: round(full[key], 2) for key in ("total", "adaptive", "sweep")},
                                 "passes": full["passes"], "tets": full["tets"], "counted": full.get("counted", False)})
                    continue
                folder = self.out / "runs" / f"{tid}_cores_{c}"
                shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True)
                try:
                    if not b._solve(path, folder, b._env(c, 1), f"Scaling: {title}, {c} core(s)", done, len(jobs)):
                        return False
                except RuntimeError as ex:
                    log(f"Scaling: {title} with {c} core(s) failed: {ex}", "warning")
                    if c == 1:
                        one_reason = failure_reason(folder, str(ex))
                    continue
                s = run_summary(folder, b.sweeps)
                check = (check_rcs(folder, path, design) if tid in MIE_TESTS else
                         check_sparams(folder, b.sweeps[0] if b.sweeps else "", (self.ref.get("sparams") or {}).get(tid)))
                reason = not_counted_reason(folder, s, check, fixed_passes(src, design, setup))
                if c == 1 and reason:
                    one_reason = reason
                runs.append({"cores": c, **{key: round(s[key], 2) for key in ("total", "adaptive", "sweep")},
                             "passes": s["passes"], "tets": s["tets"], "counted": not reason})
                log(f"Scaling: {title}, {c} core(s) {s['total']:.1f} s" + (f"; not counted: {reason}" if reason else ""),
                    "warning" if reason else "info")
            one = next((r for r in runs if r["cores"] == 1), None)
            for r in runs:
                if one:
                    r["speedup"] = round(one["total"] / r["total"], 2)
                    r["efficiency"] = round(one["total"] / r["total"] / r["cores"], 3)
            part = {"test": tid, "runs": runs}
            ref1 = (self.ref.get("single_core") or {}).get(tid)
            if one and one["counted"]:
                part["single_core_score"] = score(ref1, one["total"])
                if not ref1:
                    part["reason"] = "no single-core reference for this test yet"
                if new_ref is not None:
                    new_ref.setdefault("single_core", {})[tid] = one["total"]
            else:
                part["single_core_score"] = None
                part["reason"] = one_reason
            sc["parts"][k] = part
            self._write()
        sc["single_core_score"] = overall([p["single_core_score"] for p in sc["parts"].values()]) \
            if len(sc["parts"]) == len(PARTS) else None
        self._write()
        return True

    def _stop(self) -> bool:
        self._write()
        ResultStore(self.out).write_json("status.json", {"state": "aborted"})
        log("Benchmark aborted", "warning")
        return False


def summarize(result: dict) -> None:
    """Scores and completeness of a suite result. Per part (RF, RCS): the geometric mean of its tests, only
    when every one of them ran and counts. Overall: the geometric mean of the part scores, only when every
    part ran and has a score (complete). A test that does not count (memory guard, another solver than asked,
    not converged, wrong result) has no score and keeps its reason; the other parts stay valid."""
    tests = {t.get("id"): t for t in result.get("tests", [])}
    run = [k for k in PART_IDS if k in (result.get("parts_run")
                                        or [PART_OF.get(i) for i in tests if i in TEST_IDS])]
    parts = {}
    for k, _title, ids in PARTS:
        if k not in run:
            continue
        scores = [(tests.get(i) or {}).get("score") for i in ids]
        counted = sum(1 for x in scores if x)
        parts[k] = {"score": overall(scores), "counted": counted, "tests": len(ids), "complete": counted == len(ids)}
    result["parts"] = parts
    result["counted"] = sum(p["counted"] for p in parts.values())
    result["complete"] = len(parts) == len(PARTS) and all(p["complete"] for p in parts.values())
    result["score"] = overall([p["score"] for p in parts.values()]) if result["complete"] else None


def too_large(tid: str, ref: dict) -> str:
    """Why a large test cannot run here ('' when it can): its reference peak memory against the usable memory."""
    from .bridge import usable_memory
    if not (ref.get("times") or {}).get(tid):
        return "no reference for this test yet"
    need = ((ref.get("memory_mb") or {}).get(tid) or 0) * 2 ** 20 * MEMORY_MARGIN
    try:
        have = usable_memory()
    except Exception:  # noqa: BLE001
        return ""
    if need and have and have < need:
        return f"needs about {need / 2 ** 30:.0f} GB of memory, {have / 2 ** 30:.1f} GB usable on this computer"
    return ""


GUARD_TEXT = "Stopping refinement: the next pass"
OOM_TEXT = "may end the solver for lack of memory"


def fixed_passes(project: Path, design: str, setup: str) -> int:
    """The number of adaptive passes a test always solves (Minimum = Maximum Passes), else 0. A fixed number
    gives every computer the same work: its own initial mesh (which differs slightly between operating systems)
    must not let it stop a pass earlier or later than the reference run."""
    try:
        d = json.loads(Path(project).read_text(encoding="utf-8"))
        des = next(x for x in d["designs"] if x.get("name", design) == design)
        pr = next(x for x in des["setups"] if x["name"] == setup)["props"]
        lo, hi = int(pr.get("MinimumPasses", 0)), int(pr.get("MaximumPasses", 0))
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        return 0
    return hi if lo == hi else 0


def not_counted_reason(folder: Path, s: dict, check: dict, fixed: int = 0) -> str:
    """Why a finished test does not count ('' when it does): the memory guard stopped the refinement, it did
    not converge (with a fixed number of passes: did not solve them all), the matrix solver fell back to another
    one (e.g. no usable GPU), or the result is wrong."""
    warnings = [str(m.get("msg", "")) for m in _log_messages(folder)
                if m.get("type") == "log" and m.get("level") == "warning"]
    guard = next((w for w in warnings if w.startswith(GUARD_TEXT)), "")
    if guard:
        need = guard.split("would need about ", 1)[-1].split(". Reduce")[0]
        return f"too little memory: the memory guard stopped the refinement (needs about {need})"
    if fixed and (s.get("passes") or 0) < fixed:
        return f"stopped after {s.get('passes')} of {fixed} passes"
    if not fixed and not s.get("converged"):
        maxt = next((w for w in warnings if w.startswith("Maximum number of tetrahedra")), "")
        return "did not converge: " + (maxt or f"stopped after {s.get('passes')} passes")
    if s.get("notes"):
        return "the solver did not run as asked: " + "; ".join(s["notes"])[:300]
    if not check.get("ok"):
        if check.get("kind") == "rcs_ref_db" and check.get("value") is not None:
            return f"wrong result: RCS {check['value']:+.2f} dB from the reference (limit {check['limit']} dB)"
        if check.get("kind") == "rcs_db" and check.get("value") is not None:
            return f"wrong result: RCS {check['value']:+.2f} dB from the Mie solution (limit {check['limit']} dB)"
        if check.get("value") is not None:
            return f"wrong result: |dS| {check['value']:.3f} from the reference (limit {check['limit']})"
        return "wrong result: " + str(check.get("note", "no result"))
    return ""


def failure_reason(folder: Path, error: str) -> str:
    """Why a test ended without a result: the operating system ended it (usually for lack of memory) or the
    solver failed."""
    warnings = [str(m.get("msg", "")) for m in _log_messages(folder) if m.get("type") == "log"]
    if any(OOM_TEXT in w for w in warnings) or "code -9" in error or "code 137" in error or "MemoryError" in error:
        return "too little memory: the solver ended for lack of memory"
    return "the solver failed: " + error.split(" (see ")[0][:250]


def site_spec() -> dict:
    """What the comparison site checks a submission of this suite against: the model of every test (its file
    hash), the reference time the score is computed from and the plausible range of the final mesh (a third
    to three times the reference, for other versions and solvers)."""
    ref = reference()
    tests = []
    for tid, _title, file, *_ in ALL_TESTS:
        tets = (ref.get("tets") or {}).get(tid) or 0
        mem = (ref.get("memory_mb") or {}).get(tid)
        tests.append({"id": tid, "large": tid in LARGE_IDS, "hashes": [file_hash(SUITE_DIR / file)],
                      "time": (ref.get("times") or {}).get(tid), "memory_gb": round(mem / 1024, 1) if mem else None,
                      "tets": [int(tets / 3), int(tets * 3)] if tets else [100, 10_000_000]})
    return {SUITE: {"tests": tests, "scaling_test": SCALING_TEST, "scaling_tests": SCALING_TESTS, "single_core": ref.get("single_core") or {},
                    "titles": {t[0]: t[1] for t in ALL_TESTS},
                    "about": {t[0]: t[5] for t in ALL_TESTS}, "reference": REFERENCE_SYSTEM,
                    "reference_large": REFERENCE_SYSTEM_LARGE,
                    "parts": [{"id": k, "title": t, "tests": ids} for k, t, ids in PARTS]}}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    args = sys.argv[1:]
    opts = {}
    for key in ("--solver", "--cores", "--only", "--parts"):
        if key in args:
            i = args.index(key)
            opts[key] = args[i + 1]
            del args[i:i + 2]
    scaling = "--scaling" in args
    full = "--no-full" not in args
    for flag in ("--scaling", "--no-scaling", "--no-full"):
        if flag in args:
            args.remove(flag)
    make_ref = "--make-reference" in args
    if make_ref:
        args.remove("--make-reference")
    large = "--no-large" not in args
    if not large:
        args.remove("--no-large")
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    try:
        ResultStore(out).write_json("status.json", running_status())
        only = [x.strip() for x in opts.get("--only", "").split(",") if x.strip()]
        if opts.get("--solver") in EXCLUDED_SOLVERS:
            print(f"{opts['--solver']} is not offered by {SUITE}: far too slow for these models", file=sys.stderr)
            return 2
        parts = [x.strip() for x in opts.get("--parts", "").split(",") if x.strip()]
        bad = [x for x in parts if x not in PART_IDS]
        if bad:
            print(f"Unknown part {', '.join(bad)}; the parts are {', '.join(PART_IDS)}", file=sys.stderr)
            return 2
        ok = Suite(out, opts.get("--solver", ""), int(opts.get("--cores", 0) or 0), only, make_ref, scaling,
                   parts, large, full).run()
        return 0 if ok else 3
    except Exception as ex:  # noqa: BLE001
        emit("log", level="error", msg=f"{type(ex).__name__}: {ex}")
        emit("traceback", text=traceback.format_exc())
        ResultStore(out).write_json("status.json", {"state": "failed", "error": str(ex)})
        emit("failed", error=str(ex))
        return 1


if __name__ == "__main__":
    sys.exit(main())
