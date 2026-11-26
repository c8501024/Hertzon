"""Tools > Solver Benchmark: Hertzon Bench (a fixed suite with one score, comparable between computers and
submittable to the public comparison site), how the solve time scales with the cores on this computer and
which HPC settings to use (Tools > Options > HPC and Analysis), and how the matrix solvers compare at full
cores."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QStandardPaths
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QLineEdit, QCheckBox,
                               QPushButton, QLabel, QProgressBar, QTableWidget, QTableWidgetItem, QSplitter,
                               QAbstractItemView, QMessageBox, QFileDialog, QTabWidget, QWidget, QGridLayout)

from ..solver import solver_choice
from ..solver import bench_submit, suite
from ..solver.benchmark import CASES, CASES_DIR, default_cores, machine
from ..solver.results import _safe
from .solve import solver_python

TAG = "@@HZN "
COLUMNS = ["Run", "Cores", "Sweep processes", "Total (s)", "Initial mesh (s)", "Adaptive passes (s)", "Sweep (s)",
           "Passes", "Tetrahedra", "Peak memory (MB)", "Speedup", "Efficiency"]
SOLVER_COLUMNS = ["Solver", "Cores", "Parallel frequencies", "Total (s)", "Solver start (s)", "Initial mesh (s)",
                  "Adaptive passes (s)", "Sweep (s)", "Sweep solves", "Passes", "Tetrahedra", "Peak memory (MB)",
                  "Without start vs. first",
                  "Sweep on same mesh (s)", "Solves on same mesh", "Largest |dS|", "Note"]
# the solvers the comparison offers ("Auto" is one of them, chosen per problem; the ones this platform cannot
# run are left out), and the ones it starts with (on a Mac Apple Accelerate instead of PARDISO)
COMPARE = [s for s in solver_choice.offered() if s != "Auto"]
COMPARE_DEFAULT = ("PARDISO", solver_choice.ACCELERATE, "SPARTA (CPU)", "SPARTA (GPU)")
SUITE_COLUMNS = ["Test", "Total (s)", "Initial mesh (s)", "Adaptive passes (s)", "Sweep (s)", "Passes",
                 "Tetrahedra", "Peak memory (GB)", "Result check", "Score"]


def benchmark_root() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) or str(Path.home() / ".hertzon")
    return Path(base) / "benchmark"


def recommendation(result: dict) -> tuple[str, int | None, int | None]:
    """(text, cores, sweep processes) from the runs of a benchmark."""
    runs = [r for r in result.get("runs", []) if r.get("kind") == "cores" and not r.get("solver")]
    if not runs:
        return "", None, None
    ncpu = result.get("machine", {}).get("logical_cores") or os.cpu_count() or 1
    runs.sort(key=lambda r: r["cores"])
    best = min(runs, key=lambda r: r["total"])
    knee = next(r for r in runs if r["total"] <= 1.1 * best["total"])
    one = next((r for r in runs if r["cores"] == 1), None)
    lines = [f"Fastest with <b>{best['cores']} cores</b>: {best['total']:.0f} s"
             + (f", {one['total'] / best['total']:.1f}x faster than 1 core." if one and best is not one else ".")]
    # the fewest cores that are as fast within the measuring accuracy are the ones to use
    same = next(r for r in runs if r["total"] <= 1.02 * best["total"])
    if same is not best:
        lines.append(f"<b>{same['cores']} cores</b> are just as fast ({same['total']:.0f} s); more cores bring nothing.")
    if knee is not same:
        slower = 100 * (knee['total'] / best['total'] - 1)
        lines.append(f"With {knee['cores']} cores it is only {slower:.0f} % slower. More cores hardly help for a "
                     f"problem of this size ({best['tets']} tetrahedra); when you solve several designs at once "
                     f"(parallel variations or evaluations), give each about {knee['cores']} cores.")
    if best["cores"] == max(r["cores"] for r in runs) and best["cores"] < ncpu:
        lines.append(f"Not tested above {best['cores']} cores; more may still be faster.")
    workers = None
    sw = [r for r in result.get("runs", []) if r.get("kind") == "sweep"]
    if sw:
        base = next((r for r in sw if r["workers"] == 1), sw[0])
        fast = min(sw, key=lambda r: r["sweep"])
        if fast["workers"] > 1 and fast["sweep"] < 0.9 * base["sweep"]:
            workers = fast["workers"]
            lines.append(f"Parallel sweep: <b>{workers} processes</b> make the sweep "
                         f"{base['sweep'] / fast['sweep']:.1f}x faster (switch on 'Parallel' in the sweep).")
        else:
            workers = 1
            lines.append("Parallel sweep processes do not make the sweep faster here: keep the sweep serial "
                         "and give it all cores.")
    return "<br>".join(lines), same["cores"], workers


def solver_runs(result: dict) -> tuple[list[dict], dict, list[dict]]:
    """(runs per solver at the most cores, in the order measured; sweeps on the reference mesh one frequency at
    a time, by solver; sweeps on that mesh with parallel frequencies, in the order measured)."""
    runs = result.get("runs", [])
    cr = [r for r in runs if r.get("kind") == "cores" and r.get("solver")]
    if not cr:
        return [], {}, []
    top = max(r["cores"] for r in cr)
    return [r for r in cr if r["cores"] == top], \
        {r["solver"]: r for r in runs if r.get("kind") == "same_mesh"}, \
        [r for r in runs if r.get("kind") == "parallel"]


def solve_time(r: dict) -> float:
    """Total time of a run without starting the solver process: the first run of a benchmark loads the
    libraries from disk, which says nothing about its solver."""
    return r["total"] - r.get("start", 0.0)


def same_mesh_sweeps(result: dict) -> list[tuple[str, int, dict]]:
    """(solver, parallel frequencies, run) of every sweep on the reference mesh. Results measured before the
    reference solver solved its sweeps there once more take its sweep time from its own run."""
    top, same, par = solver_runs(result)
    if not top or not (same or par):
        return []
    first = top[0]
    out = [] if first["solver"] in same else [(first["solver"], 1, first)]
    out += [(sv, 1, r) for sv, r in same.items()]
    out += [(r["solver"], r["workers"], r) for r in par]
    return out


def solves_text(r: dict | None) -> str:
    """'direct + reduced' solutions of a run's sweeps."""
    sv = (r or {}).get("solves")
    if not sv:
        return ""
    return f"{sv['direct']} + {sv['reduced']}" if sv.get("reduced") else str(sv["direct"])


def note_text(r: dict, same: dict | None = None) -> str:
    """The Note column: what a run did not do as asked, and how long it took to start the GPU (part of every
    analysis, so it stays in the times)."""
    notes = list(dict.fromkeys(r.get("notes", []) + (same or {}).get("notes", [])))
    gpu = [f"{x['gpu_start']:.1f} s" for x in (r, same) if x and x.get("gpu_start")]
    if gpu:
        notes.append("GPU start " + (" / ".join(gpu) if len(gpu) > 1 else gpu[0])
                     + " (included in the times)")
    return "; ".join(notes)


def solver_recommendation(result: dict) -> tuple[str, str | None]:
    """(text, fastest solver) from a solver comparison."""
    runs, same, par = solver_runs(result)
    runs = [r for r in runs if solve_time(r) > 0]
    if not runs:
        return "", None
    first, best = runs[0], min(runs, key=solve_time)
    lines = [f"Fastest with {best['cores']} cores: <b>{best['solver']}</b>, {solve_time(best):.0f} s"
             + (f", {100 * (1 - solve_time(best) / solve_time(first)):.0f} % faster than {first['solver']}"
                if best is not first else "")
             + " (without starting the solver process, which the first run pays for loading the libraries)."]
    others = [r for r in runs if r is not best]
    if others:
        lines.append("Others: " + ", ".join(f"{r['solver']} {solve_time(r):.0f} s" for r in others) + ".")
    sweeps = same_mesh_sweeps(result)
    seq = [(sv, r) for sv, w, r in sweeps if w == 1]
    if seq:
        lines.append(f"Sweeps on the final mesh of {first['solver']} (the same matrices): "
                     + ", ".join(f"{sv} {r['sweep']:.0f} s" for sv, r in seq) + ".")
    if par:
        fast = min(sweeps, key=lambda t: t[2]["sweep"])
        alone = next((r["sweep"] for sv, w, r in sweeps if sv == fast[0] and w == 1), None)
        lines.append(f"Fastest sweep: <b>{fast[0]}</b>"
                     + (f" with <b>{fast[1]} parallel frequencies</b>" if fast[1] > 1 else ", one frequency at a time")
                     + f", {fast[2]['sweep']:.0f} s"
                     + (f" ({alone:.0f} s one at a time)" if alone and fast[1] > 1 else "") + ".")
    ds = [max(r.get("ds_max", {}).values(), default=float("nan")) for _, _, r in sweeps if r is not first]
    if any(d > 1e-3 for d in ds):
        lines.append("A difference of the S-parameters above 1e-3 comes from single precision "
                     "(SPARTA on the GPU); check whether it matters for your design.")
    direct = {sv: r["solves"]["direct"] for sv, w, r in sweeps if w == 1 and r.get("solves")}
    if direct and max(direct.values()) > 1.2 * max(1, min(direct.values())):
        lines.append("The interpolating sweep solved directly at different numbers of frequencies ("
                     + ", ".join(f"{sv} {n}" for sv, n in direct.items()) + "): it checks its points with a "
                     "reduced basis and solves directly where that is not accurate enough, so part of a "
                     "difference in sweep time comes from that, not from the factorization.")
    if any(r.get("notes") for r in runs) or any(r.get("notes") for _, _, r in sweeps if r is not first):
        lines.append("<span style='color:#c0392b'>Some runs did not do what was asked (another solver, fewer "
                     "parallel frequencies); see the Note column.</span>")
    return "<br>".join(lines), best["solver"]


def scaling_text(sc: dict | None) -> str:
    """The scaling benchmark of a suite result below the scores: per part the times per core count and the
    single-core score."""
    if not sc or not sc.get("parts"):
        return ""
    titles = dict((t[0], t[1]) for t in suite.TESTS)
    lines = []
    for k, title, _ids in suite.PARTS:
        part = sc["parts"].get(k)
        if not part:
            continue
        runs = sorted(part.get("runs", []), key=lambda r: r["cores"])
        times = ", ".join(f"{r['cores']}: {r['total']:.0f} s" + (f" ({r['speedup']:.1f}x)" if r.get("speedup")
                                                                 and r["cores"] > 1 else "") for r in runs)
        lines.append(f"{title} single-core <b>{part['single_core_score']}</b>" if part.get("single_core_score")
                     else f"<span style='color:#c0392b'>{title}: no single-core score, {part.get('reason', '')}</span>")
        lines[-1] += f" ({titles.get(part.get('test'), part.get('test'))}, cores {times})"
    head = (f"<span style='font-size:14pt'><b>{sc['single_core_score']}</b></span> single-core score &nbsp; "
            if sc.get("single_core_score") else "")
    return "<br>" + head + "<br>".join(lines)


class BenchmarkDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Solver Benchmark")
        self.proc: QProcess | None = None
        self.out: Path | None = None
        self.result: dict = {}
        self.sresult: dict = {}
        self.best_solver: str | None = None
        self.running_comparison = False
        self.mode = ""                      # what runs: "suite", "cores" or "solvers"
        self.suite_result: dict = {}
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.case = QComboBox()
        for title, file, design, setup in CASES:
            self.case.addItem(title, (str(CASES_DIR / file), design, setup, Path(file).stem))
        doc = win.doc
        if doc.project.path:
            for s in doc.design.setups:
                if not s.eigen:
                    self.case.addItem(f"This project: {doc.design_name} : {s.name}",
                                      (doc.project.path, doc.design_name, s.name,
                                       _safe(f"{Path(doc.project.path).stem}_{doc.design_name}_{s.name}")))
        f.addRow("Benchmark:", self.case)
        m = machine()
        f.addRow("Computer:", QLabel(f"{m['cpu']}, {m['logical_cores']} logical cores"
                                     + (f" ({m['physical_cores']} physical)" if m.get("physical_cores") else "")
                                     + (f", {m['memory_gb']} GB memory" if m.get("memory_gb") else "")))
        lay.addLayout(f)
        ncpu = os.cpu_count() or 1
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # ---- Hertzon Bench
        page = QWidget()
        pl = QVBoxLayout(page)
        parts = "; ".join(f"<b>{t}</b>: " + ", ".join(dict((x[0], x[1]) for x in suite.TESTS)[i] for i in ids)
                          for _k, t, ids in suite.PARTS)
        hint = QLabel(f"<b>{suite.SUITE}</b> in two parts with their own score ({parts}), each model solved once "
                      "with all cores and checked against a reference, plus a large test (Drone RCS) when there is "
                      "enough memory for it; it has its own score. A test counts only when it converged with the "
                      "asked solver and a correct result: a stop by the memory guard or a fallback to another solver "
                      "is marked with its reason, and its part gets no score, while the other part stays valid. "
                      "Score per test = reference time / your time x 1000 "
                      f"(reference: {suite.REFERENCE_SYSTEM}); a part score is the geometric mean of its tests, the "
                      "overall score that of the part scores (only when both parts ran and count). "
                      "Takes about 5 to 15 minutes and up to 9 GB of memory (16 GB recommended). Close other programs meanwhile. "
                      "Submit the result to compare it with other computers and Hertzon versions.")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        f = QFormLayout()
        self.suite_solver = QComboBox()
        self.suite_solver.addItems(["Auto"] + [n for n in COMPARE if n not in suite.EXCLUDED_SOLVERS])
        for i in range(self.suite_solver.count()):          # greyed out with the reason when it cannot run here
            why = solver_choice.unavailable(self.suite_solver.itemText(i))
            if why:
                item = self.suite_solver.model().item(i)
                item.setEnabled(False)
                item.setToolTip(why)
        self.suite_solver.setToolTip("Matrix solver of every test; Auto chooses per problem")
        f.addRow("Matrix solver:", self.suite_solver)
        titles = dict((t[0], t[1]) for t in suite.TESTS)
        self.suite_full = QCheckBox("Full benchmark (multi-core): every test with all cores, about 10 minutes on 4 cores")
        self.suite_full.setChecked(True)
        self.suite_full.toggled.connect(self._suite_choice)
        f.addRow("Benchmarks:", self.suite_full)
        self.suite_scaling = QCheckBox(
            "Scaling benchmark (single- to multi-core): " + " and ".join(titles[t] for t in suite.SCALING_TESTS.values())
            + " with 1, 2, 4, ... cores; single-core scores and speedup; about 10 minutes more on 4 cores")
        self.suite_scaling.setChecked(False)
        self.suite_scaling.toggled.connect(self._suite_choice)
        f.addRow("", self.suite_scaling)
        row = QHBoxLayout()
        self.suite_parts = {}
        for k, title, ids in suite.PARTS:
            cb = QCheckBox(f"{title} ({len(ids)} tests)")
            cb.setChecked(True)
            cb.toggled.connect(self._suite_choice)
            self.suite_parts[k] = cb
            row.addWidget(cb)
        self.suite_large = QCheckBox("Drone RCS (large, only with enough memory)")
        self.suite_large.setChecked(True)
        self.suite_large.setToolTip("Part of the full benchmark; runs only when the computer has the memory for it; "
                                    "its own score")
        row.addWidget(self.suite_large)
        row.addStretch(1)
        f.addRow("Parts:", row)
        pl.addLayout(f)
        self.suite_score = QLabel("")
        self.suite_score.setTextFormat(Qt.RichText)
        pl.addWidget(self.suite_score)
        self.suite_table = QTableWidget(0, len(SUITE_COLUMNS))
        self.suite_table.setHorizontalHeaderLabels(SUITE_COLUMNS)
        self.suite_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.suite_table.verticalHeader().setVisible(False)
        pl.addWidget(self.suite_table, 1)
        bl = QHBoxLayout()
        self.b_submit = QPushButton("Submit Results...")
        self.b_submit.setToolTip("Shows what would be sent and asks before sending it")
        self.b_submit.setEnabled(False)
        self.b_site = QPushButton("Compare Online")
        bl.addWidget(self.b_submit)
        bl.addWidget(self.b_site)
        bl.addStretch(1)
        pl.addLayout(bl)
        self.suite_page = page
        self.tabs.addTab(page, "Hertzon Bench")

        # ---- core scaling
        page = QWidget()
        pl = QVBoxLayout(page)
        f = QFormLayout()
        self.cores = QLineEdit(", ".join(map(str, default_cores(ncpu))))
        f.addRow("Cores to test:", self.cores)
        wr = QHBoxLayout()
        self.do_sweep = QCheckBox("Also test parallel sweep processes:")
        self.workers = QLineEdit(", ".join(str(w) for w in (1, 2, 4, 8) if w <= max(1, ncpu // 2)))
        wr.addWidget(self.do_sweep)
        wr.addWidget(self.workers, 1)
        f.addRow("", wr)
        pl.addLayout(f)
        hint = QLabel("Every core count solves the whole setup (adaptive passes to convergence, at most 50 in the "
                      "built-in cases, and the sweep) in a fresh solver process, starting with 1 core. "
                      "Do not run other analyses or heavy programs meanwhile; they distort the times.")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        self.fig, self.canvas, self.table, split = self._plot_and_table(COLUMNS)
        pl.addWidget(split, 1)
        self.advice = QLabel("")
        self.advice.setWordWrap(True)
        self.advice.setTextFormat(Qt.RichText)
        pl.addWidget(self.advice)
        self.b_apply = QPushButton("Apply Recommended Settings")
        self.b_apply.setEnabled(False)
        bl = QHBoxLayout()
        bl.addWidget(self.b_apply)
        bl.addStretch(1)
        pl.addLayout(bl)
        self.cores_page = page
        self.tabs.addTab(page, "Core Scaling")

        # ---- solver comparison
        page = QWidget()
        pl = QVBoxLayout(page)
        f = QFormLayout()
        grid = QGridLayout()
        self.solver_boxes: dict[str, QCheckBox] = {}
        for i, name in enumerate(COMPARE):
            cb = QCheckBox(name)
            cb.setChecked(name in COMPARE_DEFAULT)
            self.solver_boxes[name] = cb
            grid.addWidget(cb, i // 3, i % 3)
        self.refresh_solvers()
        f.addRow("Solvers:", grid)
        self.solver_cores = QLineEdit(str(ncpu))
        f.addRow("Cores:", self.solver_cores)
        wr = QHBoxLayout()
        self.solver_par = QCheckBox("Also solve the sweeps with parallel frequencies:")
        self.solver_workers = QLineEdit(", ".join(str(w) for w in (2, 4, 8) if w <= max(2, ncpu // 2)))
        self.solver_par.setToolTip("Every solver solves the sweeps once more on the reference's final mesh with "
                                   "each of these numbers of frequencies at a time")
        wr.addWidget(self.solver_par)
        wr.addWidget(self.solver_workers, 1)
        f.addRow("", wr)
        pl.addLayout(f)
        hint = QLabel("Every solver solves the whole setup in a fresh solver process with the same cores; the "
                      "first solver checked is the reference. Each solver refines its own mesh, so the sweeps of "
                      "all of them are then solved once more on the reference's final mesh, each in a fresh "
                      "process: the same matrices, so these sweep times and the S-parameter differences compare "
                      "the solvers alone. Sweep solves: direct solutions + points checked from the reduced basis "
                      "of an interpolating sweep.")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        self.sfig, self.scanvas, self.stable, split = self._plot_and_table(SOLVER_COLUMNS)
        pl.addWidget(split, 1)
        self.sadvice = QLabel("")
        self.sadvice.setWordWrap(True)
        self.sadvice.setTextFormat(Qt.RichText)
        pl.addWidget(self.sadvice)
        self.b_default = QPushButton("Make Fastest the Default Solver")
        self.b_default.setToolTip("Tools > Options > Default matrix solver: used by new setups")
        self.b_default.setEnabled(False)
        bl = QHBoxLayout()
        bl.addWidget(self.b_default)
        bl.addStretch(1)
        pl.addLayout(bl)
        self.solvers_page = page
        self.tabs.addTab(page, "Solver Comparison")

        br = QHBoxLayout()
        self.b_start = QPushButton("Start")
        self.b_abort = QPushButton("Abort")
        self.b_abort.setEnabled(False)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.status = QLabel("")
        br.addWidget(self.b_start)
        br.addWidget(self.b_abort)
        br.addWidget(self.bar, 1)
        lay.addLayout(br)
        lay.addWidget(self.status)
        bl = QHBoxLayout()
        self.b_export = QPushButton("Export CSV...")
        close = QPushButton("Close")
        bl.addWidget(self.b_export)
        bl.addStretch(1)
        bl.addWidget(close)
        lay.addLayout(bl)
        self.b_start.clicked.connect(self.start)
        self.b_abort.clicked.connect(self.abort)
        self.b_apply.clicked.connect(self.apply)
        self.b_default.clicked.connect(self.make_default)
        self.b_export.clicked.connect(self.export)
        self.b_submit.clicked.connect(self.submit)
        self.b_site.clicked.connect(lambda: self._open_url(bench_submit.BENCH_URL))
        self.tabs.currentChanged.connect(lambda *_: self._tab_changed())
        close.clicked.connect(self.close)
        self.case.currentIndexChanged.connect(lambda *_: self.load())
        self.resize(1040, 820)
        self.load()
        self._tab_changed()

    @staticmethod
    def _plot_and_table(columns: list[str]):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        split = QSplitter(Qt.Vertical)
        fig = Figure(figsize=(7, 3), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        split.addWidget(canvas)
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        split.addWidget(table)
        return fig, canvas, table, split

    def refresh_solvers(self) -> None:
        """Grey out the solvers that cannot run (e.g. SPARTA not installed) with the reason. Runs again each
        time the dialog is shown and after Tools > Options installed SPARTA, so a solver installed while
        Hertzon runs can be checked without restarting it."""
        for name, cb in self.solver_boxes.items():
            why = solver_choice.unavailable(name)
            if why:          # greyed out, so no run falls back to another solver under its name
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setToolTip(why)
            elif not cb.isEnabled():     # just installed: as if it had been there when the dialog opened
                cb.setEnabled(True)
                cb.setToolTip("")
                cb.setChecked(name in COMPARE_DEFAULT)

    def showEvent(self, ev) -> None:
        self.refresh_solvers()
        super().showEvent(ev)

    def _comparing(self) -> bool:
        return self.tabs.currentWidget() is self.solvers_page

    def _suite_choice(self) -> None:
        """At least one benchmark and one part; the drone belongs to the full benchmark and RCS."""
        sender = self.sender()
        if not self.suite_full.isChecked() and not self.suite_scaling.isChecked():
            (self.suite_scaling if sender is self.suite_full else self.suite_full).setChecked(True)
            return
        if not any(cb.isChecked() for cb in self.suite_parts.values()):
            next(cb for cb in self.suite_parts.values() if cb is not sender).setChecked(True)
            return
        self.suite_large.setEnabled(self.suite_full.isChecked() and self.suite_parts["rcs"].isChecked())

    def _suite_tab(self) -> bool:
        return self.tabs.currentWidget() is self.suite_page

    def _tab_changed(self) -> None:
        """The case and the CSV export belong to the other tabs; the suite has its own models."""
        self.case.setEnabled(not self._suite_tab() and self.proc is None)
        self.b_export.setEnabled(not self._suite_tab())

    @staticmethod
    def _open_url(url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def suite_folder() -> Path:
        return benchmark_root() / "hertzon_bench"

    # ---- results ------------------------------------------------------------------------------
    def _folder(self, comparing: bool | None = None) -> Path:
        comparing = self._comparing() if comparing is None else comparing
        return benchmark_root() / (self.case.currentData()[3] + ("_solvers" if comparing else ""))

    @staticmethod
    def _read_result(folder: Path) -> dict:
        try:
            return json.loads((folder / "benchmark.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def load(self) -> None:
        """Show the last results of the chosen case."""
        self.result = self._read_result(self._folder(False))
        self.sresult = self._read_result(self._folder(True))
        self.show_result()
        self.show_solvers()
        self.load_suite()

    def load_suite(self) -> None:
        try:
            self.suite_result = json.loads((self.suite_folder() / "suite.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.suite_result = {}
        self.show_suite()

    def show_suite(self) -> None:
        res = self.suite_result
        titles = {t[0]: t[1] for t in suite.ALL_TESTS}
        rows = []
        for t in res.get("tests", []):
            chk = t.get("check") or {}
            name = titles.get(t["id"], t["id"])
            if t.get("skipped"):
                rows.append(([name] + [""] * 7 + [f"not run: {t['skipped']}", ""], True))
                continue
            if "total" not in t:                       # ended without a result
                rows.append(([name] + [""] * 7 + [f"NOT COUNTED: {t.get('reason', 'failed')}", ""], True))
                continue
            if chk.get("value") is None:
                ctext = "ok" if chk.get("ok") else chk.get("note", "failed")
            elif chk.get("kind") == "rcs_db":
                ctext = f"{'ok' if chk['ok'] else 'WRONG'}: {chk['value']:+.2f} dB vs. Mie"
            elif chk.get("kind") == "rcs_ref_db":
                ctext = f"{'ok' if chk['ok'] else 'WRONG'}: {chk['value']:+.2f} dB vs. reference"
            else:
                ctext = f"{'ok' if chk['ok'] else 'WRONG'}: |dS| {chk['value']:.1e}"
            bad = t.get("counted") is False
            if bad:
                ctext = f"NOT COUNTED: {t.get('reason', '')}"
            score = str(t.get("score") or "") + (" (own score)" if t["id"] in suite.LARGE_IDS and t.get("score") else "")
            rows.append(([name, f"{t['total']:.1f}", f"{t['mesh']:.1f}", f"{t['adaptive']:.1f}", f"{t['sweep']:.1f}",
                          str(t["passes"]), str(t["tets"]), f"{t['memory_mb'] / 1024:.1f}", ctext, score], bad))
        self.suite_table.setRowCount(len(rows))
        for i, (row, bad) in enumerate(rows):
            for c, v in enumerate(row):
                it = QTableWidgetItem(v)
                if c == 8 and (bad or "WRONG" in v):
                    it.setForeground(Qt.red if bad or "WRONG" in v else Qt.gray)
                    it.setToolTip(v)
                self.suite_table.setItem(i, c, it)
        self.suite_table.resizeColumnsToContents()
        parts = res.get("parts") or {}
        single = any(p.get("single_core_score") for p in ((res.get("scaling") or {}).get("parts") or {}).values())
        if any(p.get("score") for p in parts.values()) or single:
            import time
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(res.get("finished", 0)))
            big = (f"<span style='font-size:20pt'><b>{res['score']}</b></span> {res.get('suite', '')} score&nbsp;&nbsp; "
                   if res.get("score") else "")
            each = " &nbsp; ".join(
                f"<span style='font-size:14pt'><b>{p['score']}</b></span> {title}" if p.get("score") else
                f"<span style='color:#c0392b'>{title}: no score, {p['counted']} of {p['tests']} tests count "
                f"(see Result check)</span>"
                for k, title, _ids in suite.PARTS if (p := parts.get(k)))
            self.suite_score.setText(big + each + f"<br>{res.get('cores')} cores, solver {res.get('solver')}, "
                                     f"measured {when}"
                                     + ("" if res.get("score") or not parts else " &nbsp;(no overall score: it needs every part)")
                                     + scaling_text(res.get("scaling"))
                                     + (" &nbsp;<i>(submitted)</i>" if (self.suite_folder() / "submitted.json").exists()
                                        and json.loads((self.suite_folder() / "submitted.json").read_text(
                                            encoding="utf-8")).get("run_id") == res.get("run_id") else ""))
        elif res.get("tests"):
            self.suite_score.setText("No score: " + (bench_submit.can_submit(res) or "not finished."))
        else:
            self.suite_score.setText("No result yet.")
        self.b_submit.setEnabled(self.proc is None and not bench_submit.can_submit(res))

    def show_result(self) -> None:
        runs = self.result.get("runs", [])
        cr = sorted([r for r in runs if r.get("kind") == "cores" and not r.get("solver")], key=lambda r: r["cores"])
        ref = cr[0] if cr else None
        rows = []
        # 1 core first, then the sweep runs from 1 process up, also for results measured largest first
        order = cr + sorted([r for r in runs if r.get("kind") == "sweep"], key=lambda r: r["workers"])
        for i, r in enumerate(order, start=1):
            if r.get("kind") == "cores":
                sp = ref["total"] * ref["cores"] / r["total"] if ref and r["total"] else 0.0
                rows.append([str(i), str(r["cores"]), "1", f"{r['total']:.1f}", f"{r['mesh']:.1f}",
                             f"{r['adaptive']:.1f}", f"{r['sweep']:.1f}", str(r["passes"]), str(r["tets"]),
                             f"{r['memory_mb']:.0f}", f"{sp:.2f}", f"{100 * sp / r['cores']:.0f} %"])
            else:
                rows.append([str(i), str(r["cores"]), str(r["workers"]), "", "", "", f"{r['sweep']:.1f}",
                             "", "", "", "", ""])
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, v in enumerate(row):
                self.table.setItem(i, c, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        self.fig.clear()
        if cr:
            ax1, ax2 = self.fig.add_subplot(121), self.fig.add_subplot(122)
            x = [r["cores"] for r in cr]
            for key, lab in (("total", "Total"), ("adaptive", "Adaptive passes"), ("sweep", "Sweep"),
                             ("mesh", "Initial mesh")):
                ax1.plot(x, [r[key] for r in cr], "o-", ms=4, label=lab)
            ax1.set_xscale("log", base=2)
            ax1.set_xlabel("Cores")
            ax1.set_ylabel("Time (s)")
            ax1.set_title("Solve time", fontsize=10)
            ax1.legend(fontsize=8)
            ax1.grid(True, alpha=0.3)
            sp = [ref["total"] * ref["cores"] / r["total"] for r in cr]
            ax2.plot(x, sp, "o-", ms=4, label="Measured (total)")
            ax2.plot(x, [ref["adaptive"] * ref["cores"] / r["adaptive"] if r["adaptive"] else 0 for r in cr],
                     "s--", ms=3, label="Adaptive passes")
            ax2.plot(x, x, ":", color="#888", label="Ideal")
            ax2.set_xscale("log", base=2)
            ax2.set_yscale("log", base=2)
            ax2.set_xlabel("Cores")
            ax2.set_ylabel("Speedup")
            ax2.set_title("Speedup", fontsize=10)
            ax2.legend(fontsize=8)
            ax2.grid(True, alpha=0.3)
            from matplotlib.ticker import ScalarFormatter
            ax2.yaxis.set_major_formatter(ScalarFormatter())
            for ax in (ax1, ax2):
                ax.set_xticks(x)
                ax.set_xticklabels([str(v) for v in x])
        self.canvas.draw_idle()
        text, cores, workers = recommendation(self.result)
        when = ""
        if self.result.get("finished"):
            import time
            when = f"Measured {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.result['finished']))}. "
        self.advice.setText((when + text) if text else ("No result yet for this benchmark." if not runs else ""))
        self.rec = (cores, workers)
        self.b_apply.setEnabled(cores is not None and self.proc is None and bool(self.result.get("finished")))

    def show_solvers(self) -> None:
        res = self.sresult
        cr = [r for r in res.get("runs", []) if r.get("kind") == "cores" and r.get("solver")]
        top, same, par = solver_runs(res)
        first = top[0] if top else None
        sweeps = same_mesh_sweeps(res)
        old_ref = bool(sweeps) and first is not None and first["solver"] not in same
        rows = []
        for r in cr:
            at_top = bool(top) and r["cores"] == top[0]["cores"]
            sm = same.get(r["solver"]) if at_top else None
            ref_row = first is not None and r is first
            if sm is None and ref_row and old_ref:
                sm = r                              # older results: the reference's own sweep
            vs = f"{solve_time(r) / solve_time(first):.2f}x" if first and solve_time(first) > 0 and at_top else ""
            if ref_row and sweeps:
                ds = "reference"
            elif sm is not None and sm is not r:
                ds = f"{max(sm.get('ds_max', {}).values(), default=float('nan')):.1e}"
            else:
                ds = ""
            rows.append([r["solver"], str(r["cores"]), "1", f"{r['total']:.1f}", f"{r.get('start', 0.0):.1f}",
                         f"{r['mesh']:.1f}", f"{r['adaptive']:.1f}", f"{r['sweep']:.1f}", solves_text(r), str(r["passes"]),
                         str(r["tets"]), f"{r['memory_mb']:.0f}", vs,
                         f"{sm['sweep']:.1f}" if sm else "", solves_text(sm) if sm is not r else "", ds,
                         note_text(r, sm if sm is not r else None)])
        for r in par:
            ds = max(r.get("ds_max", {}).values(), default=float("nan"))
            rows.append([r["solver"], str(r["cores"]), str(r["workers"]), "", "", "", "", "", "", "", "", "", "",
                         f"{r['sweep']:.1f}", solves_text(r), f"{ds:.1e}", note_text(r)])
        self.stable.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, v in enumerate(row):
                it = QTableWidgetItem(v)
                if c == len(row) - 1 and v:
                    it.setToolTip(v)
                self.stable.setItem(i, c, it)
        self.stable.resizeColumnsToContents()
        self.sfig.clear()
        if top:
            names = [r["solver"] for r in top]
            y = list(range(len(top)))[::-1]
            ax1 = self.sfig.add_subplot(121 if sweeps else 111)
            left = [0.0] * len(top)
            # the rest of the total: geometry, ports, writing the results
            start = [r.get("start", 0.0) for r in top]
            rest = [max(0.0, r["total"] - r.get("start", 0.0) - r["mesh"] - r["adaptive"] - r["sweep"]) for r in top]
            for vals, lab, col in ((start, "Solver start", "#e0e0e0"), ([r["mesh"] for r in top], "Initial mesh", None),
                                   ([r["adaptive"] for r in top], "Adaptive passes", None),
                                   ([r["sweep"] for r in top], "Sweep", None), (rest, "Other", "#bbbbbb")):
                ax1.barh(y, vals, left=left, height=0.6, label=lab, color=col)
                left = [a + b for a, b in zip(left, vals)]
            for yi, r in zip(y, top):
                ax1.text(r["total"], yi, f" {r['total']:.0f} s", va="center", fontsize=8)
            ax1.set_yticks(y)
            ax1.set_yticklabels(names, fontsize=8)
            ax1.set_xlabel("Time (s)")
            ax1.set_xlim(0, 1.15 * max(r["total"] for r in top))
            ax1.set_ylim(-0.6, len(top) + 0.2)        # room for the legend above the bars
            ax1.set_title(f"Whole setup, {top[0]['cores']} cores", fontsize=10)
            ax1.legend(fontsize=7, loc="upper center", ncol=5, frameon=False, columnspacing=0.8)
            ax1.grid(True, axis="x", alpha=0.3)
            if sweeps:
                # one frequency at a time first, then each solver's parallel runs below it
                order = sorted(sweeps, key=lambda t: ([s for s, _, _ in sweeps].index(t[0]), t[1]))
                sw = [(sv if w == 1 else f"{sv}, {w} parallel", r["sweep"], w) for sv, w, r in order]
                y2 = list(range(len(sw)))[::-1]
                ax2 = self.sfig.add_subplot(122)
                ax2.barh(y2, [v for _, v, _ in sw], height=0.6,
                         color=["#2ca02c" if w == 1 else "#98df8a" for _, _, w in sw])
                for yi, (_, v, _) in zip(y2, sw):
                    ax2.text(v, yi, f" {v:.0f} s", va="center", fontsize=8)
                ax2.set_yticks(y2)
                ax2.set_yticklabels([n for n, _, _ in sw], fontsize=8)
                ax2.set_xlabel("Time (s)")
                ax2.set_xlim(0, 1.15 * max(v for _, v, _ in sw))
                ax2.set_title(f"Sweeps on the mesh of {first['solver']}", fontsize=10)
                ax2.grid(True, axis="x", alpha=0.3)
        self.scanvas.draw_idle()
        text, best = solver_recommendation(res)
        when = ""
        if res.get("finished"):
            import time
            when = f"Measured {time.strftime('%Y-%m-%d %H:%M', time.localtime(res['finished']))}. "
        self.sadvice.setText((when + text) if text else ("No solver comparison yet for this benchmark."
                                                         if not cr else ""))
        self.best_solver = best
        self.b_default.setEnabled(best is not None and self.proc is None and bool(res.get("finished")))

    # ---- running ------------------------------------------------------------------------------
    def start(self) -> None:
        if self.proc is not None:
            return
        if getattr(self.win.solver, "busy", False):
            QMessageBox.information(self, "Solver Benchmark", "Wait until the running analysis has finished; "
                                                              "it would distort the times.")
            return
        if self._suite_tab():
            self.start_suite()
            return
        comparing = self._comparing()
        solvers = [n for n, cb in self.solver_boxes.items() if cb.isChecked()] if comparing else []
        if comparing and not solvers:
            QMessageBox.warning(self, "Solver Benchmark", "Check at least one solver.")
            return
        try:
            text = self.solver_cores.text() if comparing else self.cores.text()
            cores = [int(x) for x in text.replace(";", ",").split(",") if x.strip()]
            if comparing:
                workers = [int(x) for x in self.solver_workers.text().replace(";", ",").split(",") if x.strip()] \
                    if self.solver_par.isChecked() else []
            else:
                workers = [int(x) for x in self.workers.text().replace(";", ",").split(",") if x.strip()] \
                    if self.do_sweep.isChecked() else []
            if not cores or min(cores + (workers or [1])) < 1:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Solver Benchmark", "Enter the core counts as whole numbers, e.g. 1, 2, 4, 8.")
            return
        project, design, setup, _ = self.case.currentData()
        self.out = self._folder(comparing)
        self.running_comparison = comparing
        self.mode = "solvers" if comparing else "cores"
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "ABORT").unlink(missing_ok=True)
        args = ["-m", "hertzon.solver.benchmark", project, design, setup, str(self.out),
                "--cores", ",".join(map(str, cores))]
        if workers:
            args += ["--workers", ",".join(map(str, workers))]
        if solvers:
            args += ["--solvers", ",".join(solvers)]
        p = self._process()
        if comparing:
            self.sresult = {"runs": []}
            self.show_solvers()
        else:
            self.result = {"runs": []}
            self.show_result()
        self.b_start.setEnabled(False)
        self.b_abort.setEnabled(True)
        self.case.setEnabled(False)
        self.tabs.tabBar().setEnabled(False)
        self._running_ui()
        self.win.msgs.add("info", f"Solver benchmark started: {self.case.currentText()}"
                          + (f" ({', '.join(solvers)})" if solvers else ""))
        p.start(solver_python(), args)

    def _process(self) -> QProcess:
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        root = str(Path(__file__).resolve().parents[2])
        env.insert("PYTHONPATH", root + os.pathsep + env.value("PYTHONPATH", ""))
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        p.setProcessEnvironment(env)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._finished)
        self._buf = b""
        self.proc = p
        return p

    def _running_ui(self) -> None:
        self.b_start.setEnabled(False)
        self.b_abort.setEnabled(True)
        self.b_submit.setEnabled(False)
        self.case.setEnabled(False)
        self.tabs.tabBar().setEnabled(False)
        self.bar.setValue(0)
        self.status.setText("Starting")

    def start_suite(self) -> None:
        self.out = self.suite_folder()
        self.mode = "suite"
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "ABORT").unlink(missing_ok=True)
        args = ["-m", "hertzon.solver.suite", str(self.out)]
        sv = self.suite_solver.currentText()
        if sv != "Auto":
            args += ["--solver", sv]
        parts = [k for k, cb in self.suite_parts.items() if cb.isChecked()]
        if len(parts) < len(self.suite_parts):
            args += ["--parts", ",".join(parts)]
        if not self.suite_large.isChecked() or "rcs" not in parts:
            args.append("--no-large")
        if not self.suite_full.isChecked():
            args.append("--no-full")
        if self.suite_scaling.isChecked():
            args.append("--scaling")
        p = self._process()
        self.suite_result = {}
        self.show_suite()
        self._running_ui()
        self.win.msgs.add("info", f"{suite.SUITE} started")
        p.start(solver_python(), args)

    def _read(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            line = raw.decode("utf-8", errors="replace")
            if not line.startswith(TAG):
                continue
            try:
                msg = json.loads(line[len(TAG):])
            except ValueError:
                continue
            t = msg.get("type")
            if t == "progress":
                self.bar.setValue(int(msg.get("value", 0)))
                self.status.setText(msg.get("text", ""))
            elif t == "log":
                self.win.msgs.add(msg.get("level", "info"), f"Benchmark: {msg.get('msg', '')}")
                self.load_running()
            elif t == "failed":
                self.status.setText(f"<span style='color:#c0392b'>Failed: {msg.get('error')}</span>")

    def load_running(self) -> None:
        if self.mode == "suite":
            self.load_suite()
            return
        res = self._read_result(self.out)
        if not res:
            return
        if self.running_comparison:
            self.sresult = res
            self.show_solvers()
        else:
            self.result = res
            self.show_result()

    def _finished(self, code, _status) -> None:
        self._read()
        self.proc = None
        self.b_start.setEnabled(True)
        self.b_abort.setEnabled(False)
        self.tabs.tabBar().setEnabled(True)
        self.load()
        self._tab_changed()
        if code == 0:
            self.bar.setValue(100)
            self.status.setText("Done")
            self.win.msgs.add("info", "Solver benchmark finished"
                              + (f": {suite.SUITE} score {self.suite_result['score']}"
                                 if self.mode == "suite" and self.suite_result.get("score") else ""))
        elif not self.status.text().startswith("<span"):
            self.status.setText("Aborted" if (self.out / "ABORT").exists() else f"Ended with code {code}")

    def abort(self) -> None:
        if self.proc is None or self.out is None:
            return
        (self.out / "ABORT").write_text("1", encoding="utf-8")
        self.status.setText("Aborting")
        proc = self.proc
        from PySide6.QtCore import QTimer
        QTimer.singleShot(15000, lambda: proc.kill() if self.proc is proc else None)

    def closeEvent(self, ev) -> None:
        if self.proc is not None:
            if QMessageBox.question(self, "Solver Benchmark", "Abort the running benchmark?") != QMessageBox.Yes:
                ev.ignore()
                return
            self.abort()
        super().closeEvent(ev)

    # ---- using the result -----------------------------------------------------------------------
    def apply(self) -> None:
        cores, workers = self.rec
        if cores is None:
            return
        s = self.win.settings
        ncpu = os.cpu_count() or 1
        s.setValue("hpc/cores", 0 if cores >= ncpu else cores)
        if workers:
            s.setValue("hpc/sweep_workers", workers)
        self.win.apply_hpc_settings()
        self.win.msgs.add("info", f"HPC settings: {'all' if cores >= ncpu else cores} cores"
                          + (f", {workers} parallel sweep process(es)" if workers else ""))

    def make_default(self) -> None:
        if not self.best_solver:
            return
        self.win.settings.setValue("hpc/default_solver", self.best_solver)
        self.win.msgs.add("info", f"Default matrix solver for new setups: {self.best_solver} "
                                  "(existing setups keep theirs)")

    def submit(self) -> None:
        why = bench_submit.can_submit(self.suite_result)
        if why:
            QMessageBox.information(self, "Submit Results", why)
            return
        dlg = SubmitDialog(self, self.suite_result)
        if dlg.exec() and dlg.answer:
            (self.suite_folder() / "submitted.json").write_text(json.dumps(
                {"run_id": self.suite_result.get("run_id"), **dlg.answer}, indent=1), encoding="utf-8")
            self.win.msgs.add("info", f"{suite.SUITE} result submitted: {dlg.answer.get('url', '')}")
            self.show_suite()

    def export(self) -> None:
        """The table of the shown tab, as shown."""
        comparing = self._comparing()
        table, columns = (self.stable, SOLVER_COLUMNS) if comparing else (self.table, COLUMNS)
        if not table.rowCount():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Benchmark",
                                              "solver_comparison.csv" if comparing else "benchmark.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        lines = [";".join(columns)]
        for r in range(table.rowCount()):
            lines.append(";".join(table.item(r, c).text() if table.item(r, c) else ""
                                  for c in range(table.columnCount())))
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


class SubmitDialog(QDialog):
    """Shows exactly what is sent, asks for consent, sends it and shows the link to the result."""

    def __init__(self, parent, result: dict):
        super().__init__(parent)
        from PySide6.QtGui import QFontDatabase, QGuiApplication
        from PySide6.QtWidgets import QPlainTextEdit
        from ..solver import sysinfo
        self.setWindowTitle("Submit Results")
        self.result = result
        self.answer: dict = {}
        lay = QVBoxLayout(self)
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.system = sysinfo.collect()          # with the memory bandwidth, about 2 s
        finally:
            QGuiApplication.restoreOverrideCursor()
        intro = QLabel(f"Your result goes to <a href='{bench_submit.BENCH_URL}'>{bench_submit.BENCH_URL}</a>, "
                       "where everyone can see and compare it. Sent is exactly the text below: the times, "
                       "scores and checks of the tests, and the description of this computer "
                       f"({sysinfo.summary(self.system)}). Not sent: your name (unless you enter one), "
                       "user or computer name, paths, projects. The site does not store your IP address.")
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        lay.addWidget(intro)
        f = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(bench_submit.MAX_NAME)
        self.name.setPlaceholderText("optional, shown with the result")
        f.addRow("Display name:", self.name)
        self.comment = QLineEdit()
        self.comment.setMaxLength(200)
        self.comment.setPlaceholderText("optional, e.g. 'laptop on battery', 'water-cooled'")
        f.addRow("Comment:", self.comment)
        lay.addLayout(f)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        lay.addWidget(self.preview, 1)
        self.agree = QCheckBox("I agree that this result is published on the comparison site")
        lay.addWidget(self.agree)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        self.status.setOpenExternalLinks(True)
        self.status.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lay.addWidget(self.status)
        bl = QHBoxLayout()
        self.b_send = QPushButton("Submit")
        self.b_send.setEnabled(False)
        self.b_close = QPushButton("Cancel")
        bl.addStretch(1)
        bl.addWidget(self.b_send)
        bl.addWidget(self.b_close)
        lay.addLayout(bl)
        self.agree.toggled.connect(lambda on: self.b_send.setEnabled(on and not self.answer))
        self.name.textChanged.connect(self._update)
        self.comment.textChanged.connect(self._update)
        self.b_send.clicked.connect(self.send)
        self.b_close.clicked.connect(lambda: self.done(1 if self.answer else 0))
        self._update()
        self.resize(760, 640)

    def data(self) -> dict:
        return bench_submit.payload(self.result, self.system, self.name.text(), self.comment.text())

    def _update(self) -> None:
        self.preview.setPlainText(json.dumps(self.data(), indent=2))

    def send(self) -> None:
        from PySide6.QtGui import QGuiApplication
        self.b_send.setEnabled(False)
        self.status.setText("Sending...")
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ans = bench_submit.submit(self.data())
        except RuntimeError as ex:
            self.status.setText(f"<span style='color:#c0392b'>Not sent: {ex}</span>")
            self.b_send.setEnabled(self.agree.isChecked())
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.answer = ans
        url = ans.get("url", "")
        self.status.setText(f"Submitted. Your result: <a href='{url}'>{url}</a><br>To remove it later, keep "
                            f"this key: <code>{ans.get('delete_key', '')}</code> (also saved with the result "
                            "on this computer).")
        for w in (self.name, self.comment, self.agree):
            w.setEnabled(False)
        self.b_close.setText("Close")
