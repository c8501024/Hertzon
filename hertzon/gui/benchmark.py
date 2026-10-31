"""Tools > Solver Benchmark: how the solve time scales with the cores on this computer, and which HPC
settings to use (Tools > Options > HPC and Analysis); and how the matrix solvers compare at full cores."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QStandardPaths
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QLineEdit, QCheckBox,
                               QPushButton, QLabel, QProgressBar, QTableWidget, QTableWidgetItem, QSplitter,
                               QAbstractItemView, QMessageBox, QFileDialog, QTabWidget, QWidget, QGridLayout)

from ..model import schema
from ..solver import sparta_setup
from ..solver.benchmark import CASES, CASES_DIR, default_cores, machine, parallel_sweeps
from ..solver.results import _safe
from .solve import solver_python

TAG = "@@HZN "
COLUMNS = ["Run", "Cores", "Sweep processes", "Total (s)", "Initial mesh (s)", "Adaptive passes (s)", "Sweep (s)",
           "Passes", "Tetrahedra", "Peak memory (MB)", "Speedup", "Efficiency"]
SOLVER_COLUMNS = ["Solver", "Cores", "Total (s)", "Solver start (s)", "Initial mesh (s)", "Adaptive passes (s)", "Sweep (s)", "Passes",
                  "Tetrahedra", "Peak memory (MB)", "Without start vs. first", "Sweep on same mesh (s)", "Largest |dS|",
                  "Note"]
# the solvers the comparison offers ("Auto" is one of them, chosen per problem), and the ones it starts with
COMPARE = [s for s in schema.SOLVERS if s != "Auto"]
COMPARE_DEFAULT = ("PARDISO", "SPARTA (CPU)", "SPARTA (GPU)")


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


def sweep_label(r: dict) -> str:
    """'PARDISO', or 'PARDISO, 4 parallel' for a sweep with 4 frequencies at once."""
    w = int(r.get("workers", 1) or 1)
    return r["solver"] if w <= 1 else f"{r['solver']}, {w} parallel"


def solver_runs(result: dict) -> tuple[list[dict], dict]:
    """(runs per solver at the most cores, in the order measured; same-mesh sweep runs by sweep_label)."""
    cr = [r for r in result.get("runs", []) if r.get("kind") == "cores" and r.get("solver")]
    if not cr:
        return [], {}
    top = max(r["cores"] for r in cr)
    return [r for r in cr if r["cores"] == top], \
        {sweep_label(r): r for r in result.get("runs", []) if r.get("kind") == "same_mesh"}


def solve_time(r: dict) -> float:
    """Total time of a run without starting the solver process: the first run of a benchmark loads the
    libraries from disk, which says nothing about its solver."""
    return r["total"] - r.get("start", 0.0)


def solver_recommendation(result: dict) -> tuple[str, str | None]:
    """(text, fastest solver) from a solver comparison."""
    runs, same = solver_runs(result)
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
    if same:
        ref = first["sweep"]
        lines.append(f"Sweeps on the final mesh of {first['solver']} (the same matrices): {first['solver']} "
                     f"{ref:.0f} s, " + ", ".join(f"{sv} {r['sweep']:.0f} s" for sv, r in same.items()) + ".")
        sweeps = {first["solver"]: ref, **{sv: r["sweep"] for sv, r in same.items()}}
        fastest = min(sweeps, key=sweeps.get)
        if any(r.get("workers", 1) > 1 for r in same.values()) or fastest != first["solver"]:
            lines.append(f"Fastest sweep: <b>{fastest}</b>"
                         + (f", {ref / sweeps[fastest]:.1f}x faster than {first['solver']} one frequency at a "
                            f"time." if fastest != first["solver"] else "."))
        ds = {sv: max(r.get("ds_max", {}).values(), default=float("nan")) for sv, r in same.items()}
        if any(d > 1e-3 for d in ds.values()):
            lines.append("A difference of the S-parameters above 1e-3 comes from single precision "
                         "(SPARTA on the GPU); check whether it matters for your design.")
    if any(r.get("notes") for r in runs):
        lines.append("<span style='color:#c0392b'>Some runs did not use the chosen solver; see the Note "
                     "column.</span>")
    return "<br>".join(lines), best["solver"]


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
        self.tabs.addTab(page, "Core Scaling")

        # ---- solver comparison
        page = QWidget()
        pl = QVBoxLayout(page)
        f = QFormLayout()
        grid = QGridLayout()
        self.solver_boxes: dict[str, QCheckBox] = {}
        sparta = sparta_setup.installed_version()
        for i, name in enumerate(COMPARE):
            cb = QCheckBox(name)
            cb.setChecked(name in COMPARE_DEFAULT)
            if "SPARTA" in name and not sparta:
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setToolTip("SPARTA is not installed (Tools > Options > Install SPARTA)")
            self.solver_boxes[name] = cb
            grid.addWidget(cb, i // 3, i % 3)
        f.addRow("Solvers:", grid)
        self.solver_cores = QLineEdit(str(ncpu))
        f.addRow("Cores:", self.solver_cores)
        wr = QHBoxLayout()
        self.solver_par = QCheckBox("Also sweep with parallel frequencies (PARDISO):")
        self.solver_par.setToolTip("SPARTA solves the frequencies of a sweep one after another; PARDISO can solve "
                                   "several at once, each on a share of the cores")
        self.solver_workers = QLineEdit(", ".join(str(w) for w in (2, 4, 8) if w <= max(2, ncpu // 2)))
        wr.addWidget(self.solver_par)
        wr.addWidget(self.solver_workers, 1)
        f.addRow("", wr)
        pl.addLayout(f)
        hint = QLabel("Every solver solves the whole setup in a fresh solver process with the same cores; the "
                      "first solver checked is the reference. Each solver refines its own mesh, so the sweeps of "
                      "the others are then solved once more on the reference's final mesh: the same matrices, "
                      "so these sweep times and the S-parameter differences compare the solvers alone. With parallel "
                      "frequencies, PARDISO's sweeps are solved there again with that many frequencies at once.")
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
        close.clicked.connect(self.close)
        self.case.currentIndexChanged.connect(lambda *_: self.load())
        self.resize(1040, 820)
        self.load()

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

    def _comparing(self) -> bool:
        return self.tabs.currentIndex() == 1

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
        top, same = solver_runs(res)
        first = top[0] if top else None
        rows = []
        for r in cr:
            sm = same.get(r["solver"]) if top and r["cores"] == top[0]["cores"] else None
            ref_row = first is not None and r is first
            vs = f"{solve_time(r) / solve_time(first):.2f}x" if first and solve_time(first) > 0 \
                and r["cores"] == first["cores"] else ""
            ds = max(sm.get("ds_max", {}).values(), default=float("nan")) if sm else None
            rows.append([r["solver"], str(r["cores"]), f"{r['total']:.1f}", f"{r.get('start', 0.0):.1f}",
                         f"{r['mesh']:.1f}",
                         f"{r['adaptive']:.1f}", f"{r['sweep']:.1f}", str(r["passes"]), str(r["tets"]),
                         f"{r['memory_mb']:.0f}", vs,
                         f"{sm['sweep']:.1f}" if sm else (f"{r['sweep']:.1f}" if ref_row and same else ""),
                         f"{ds:.1e}" if ds is not None else ("reference" if ref_row and same else ""),
                         "; ".join(r.get("notes", []))])
        for lab, sm in same.items():
            if sm.get("workers", 1) > 1:
                ds = max(sm.get("ds_max", {}).values(), default=float("nan"))
                rows.append([lab, str(sm["cores"]), "", "", "", "", "", "", "", "",
                             f"{sm['sweep'] / first['sweep']:.2f}x sweep" if first and first["sweep"] else "",
                             f"{sm['sweep']:.1f}", f"{ds:.1e}", ""])
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
            ax1 = self.sfig.add_subplot(121 if same else 111)
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
            if same:
                sw = [(first["solver"], first["sweep"])] + [(sv, r["sweep"]) for sv, r in same.items()]
                y2 = list(range(len(sw)))[::-1]
                ax2 = self.sfig.add_subplot(122)
                ax2.barh(y2, [v for _, v in sw], height=0.6, color="#2ca02c")
                for yi, (_, v) in zip(y2, sw):
                    ax2.text(v, yi, f" {v:.0f} s", va="center", fontsize=8)
                ax2.set_yticks(y2)
                ax2.set_yticklabels([n for n, _ in sw], fontsize=8)
                ax2.set_xlabel("Time (s)")
                ax2.set_xlim(0, 1.15 * max(v for _, v in sw))
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
        if comparing and workers and not any(parallel_sweeps(sv) for sv in solvers):
            QMessageBox.warning(self, "Solver Benchmark", "Parallel frequencies need PARDISO among the solvers; "
                                                          "the others solve one frequency at a time.")
            return
        project, design, setup, _ = self.case.currentData()
        self.out = self._folder(comparing)
        self.running_comparison = comparing
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "ABORT").unlink(missing_ok=True)
        args = ["-m", "hertzon.solver.benchmark", project, design, setup, str(self.out),
                "--cores", ",".join(map(str, cores))]
        if workers:
            args += ["--workers", ",".join(map(str, workers))]
        if solvers:
            args += ["--solvers", ",".join(solvers)]
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
        self.bar.setValue(0)
        self.status.setText("Starting")
        self.win.msgs.add("info", f"Solver benchmark started: {self.case.currentText()}"
                          + (f" ({', '.join(solvers)})" if solvers else ""))
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
        self.case.setEnabled(True)
        self.tabs.tabBar().setEnabled(True)
        self.load()
        if code == 0:
            self.bar.setValue(100)
            self.status.setText("Done")
            self.win.msgs.add("info", "Solver benchmark finished")
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
