"""Result reports: rectangular, Smith, polar, radiation pattern, 3D polar and data table."""
from __future__ import annotations

import re

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import json

from PySide6.QtCore import Signal, Qt, QSettings
from PySide6.QtGui import QCursor, QColor
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QListWidget, QPushButton, QLabel,
                               QTableWidget, QTableWidgetItem, QFileDialog, QSplitter, QFormLayout, QLineEdit,
                               QAbstractItemView, QHeaderView, QMenu, QApplication, QDialog, QDialogButtonBox,
                               QCheckBox, QInputDialog, QColorDialog, QDoubleSpinBox, QPlainTextEdit, QMessageBox,
                               QListWidgetItem)

from ..model.design import Report
from ..solver.results import ResultStore, results_dir
from .doc import result_dir
from ..solver.ffquant import monostatic_rcs, farfield_quantities, antenna_parameters  # noqa: F401
from ..solver.ffquant import ANTENNA_QUANTITIES, ANTENNA_POWER, ANTENNA_PASSES, antenna_series, antenna_pass_series
from ..solver.ffquant import RCS_PASSES, rcs_pass_series
from ..solver.expressions import (network, split_trace, output_variables, _FUNCS, _ast_eval,  # noqa: F401
                                  eval_expr, eval_trace)
from ..solver import tdr

ANTENNA_KINDS = ("Antenna Parameters", "Antenna Parameters Table")
TIME_KIND = "Time Domain"
# category -> trace pattern of the time domain report (TDR from the frequency sweep)
TD_CATEGORIES = {"TDR Impedance": "TDRZ({i})", "Step Response": "Step(S({i},{j}))",
                 "Impulse Response": "Impulse(S({i},{j}))", "Impulse (Band Pass)": "ImpulseBandPass(S({i},{j}))"}

ETA0 = 376.730313668
FUNCS = ["dB", "mag", "ang_deg", "ang_rad", "re", "im", "dB10"]
CATEGORIES = ["S Parameter", "Y Parameter", "Z Parameter", "VSWR", "Gamma", "Group Delay"]
FF_QUANTITIES = ["GainTotal", "RealizedGainTotal", "DirTotal", "GainTheta", "GainPhi", "GainLHCP", "GainRHCP",
                 "DirLHCP", "DirRHCP", "rETotal", "rETheta", "rEPhi", "rELHCP", "rERHCP", "AxialRatioValue",
                 "BistaticRCSTotal", "BistaticRCSTheta", "BistaticRCSPhi"]
# far field categories; with only an incident wave there is no gain, only RCS and rE
FF_CATEGORIES = {
    "Gain": (["GainTotal", "GainTheta", "GainPhi", "GainLHCP", "GainRHCP"], ["dB", "mag"]),
    "Realized Gain": (["RealizedGainTotal"], ["dB", "mag"]),
    "Directivity": (["DirTotal", "DirLHCP", "DirRHCP"], ["dB", "mag"]),
    "rE": (["rETotal", "rETheta", "rEPhi", "rELHCP", "rERHCP"], ["dB", "mag"]),
    "Axial Ratio": (["AxialRatioValue"], ["dB", "mag"]),
    "Bistatic RCS": (["BistaticRCSTotal", "BistaticRCSTheta", "BistaticRCSPhi"], ["dBsm", "m^2"]),
}


def ff_categories(design) -> list[str]:
    pw = not design.ports() and any(e.kind == "Incident Plane Wave" and e.enabled for e in design.excitations)
    return ["Bistatic RCS", "rE"] if pw else ["Gain", "Realized Gain", "Directivity", "rE", "Axial Ratio"]


def ff_trace(tr: str) -> tuple[str, str]:
    """'dB(GainTotal)' -> ('dB', 'GainTotal'); a plain quantity (RCS in m^2) -> ('', name)."""
    m = re.match(r"(\w+)\((\w+)\)", tr)
    return m.groups() if m else ("", tr.strip())


NF_QUANTITIES = ["NearETotal", "NearEX", "NearEY", "NearEZ", "NearHTotal", "NearHX", "NearHY", "NearHZ"]


def solution_list(doc) -> list[str]:
    out = []
    if not doc.project.path:
        return out
    for s in doc.design.setups:
        st = ResultStore(result_dir(doc, s.name))
        sols = st.solutions()
        for sol in sols:
            out.append(f"{s.name} : {sol}")
        if sols:
            out += [f"{s.name} : {x}" for x in adaptive_pass_solutions(st)]
    return out


def adaptive_pass_solutions(st: ResultStore) -> list[str]:
    conv = st.read_json("convergence.json", {}) or {}
    passes = [p for p in conv.get("passes", []) if p.get("S")]
    if not passes:
        return []
    fa = conv.get("frequencies") or [0.0]
    if len(fa) == 1:
        return ["AdaptivePasses"]
    return [f"AdaptivePasses @ {f / 1e9:.6g}GHz" for f in fa]


def adaptive_pass_data(st: ResultStore, sol: str) -> dict | None:
    """S-parameters of every adaptive pass, with the pass number as x axis."""
    conv = st.read_json("convergence.json", {}) or {}
    passes = [p for p in conv.get("passes", []) if p.get("S")]
    if not passes:
        return None
    fa = conv.get("frequencies") or [0.0]
    k = 0
    if "@" in sol:
        target = sol.split("@")[1].strip().lower().replace("ghz", "")
        k = min(range(len(fa)), key=lambda i: abs(fa[i] / 1e9 - float(target)))
    S = []
    for p in passes:
        flat = np.asarray(p["S"][min(k, len(p["S"]) - 1)], float)
        v = flat[:, 0] + 1j * flat[:, 1]
        n = int(round(np.sqrt(len(v))))
        S.append(v.reshape(n, n))
    last = st.sparams("LastAdaptive")
    z0 = last["z0"] if last is not None else np.array([50.0])
    x = np.array([p["pass"] for p in passes], float)
    return {"freq": x, "S": np.array(S), "z0": z0, "solved": x, "ports": last["ports"] if last is not None else [],
            "xaxis": "Pass", "adaptive_freq": fa[k]}


def antenna_solution_list(doc) -> list[str]:
    """Solutions with far fields (antenna parameters over frequency) and the adaptive passes."""
    out = []
    if not doc.project.path:
        return out
    for s in doc.design.setups:
        st = ResultStore(result_dir(doc, s.name))
        out += [f"{s.name} : {sol}" for sol in st.solutions() if (st.folder / f"huygens_{sol}.npz").exists()]
        rows = [r for v in (st.read_json(ANTENNA_PASSES, {}) or {}).values() for r in v]
        if rows:
            fa = rows[-1].get("freq") or [0.0]
            out += [f"{s.name} : AdaptivePasses"] if len(fa) == 1 else \
                [f"{s.name} : AdaptivePasses @ {f / 1e9:.6g}GHz" for f in fa]
    return out


def rcs_solution_list(doc) -> list[str]:
    """Solutions with far fields of an incident wave (monostatic RCS over frequency) and the adaptive passes."""
    out = []
    if not doc.project.path:
        return out
    for s in doc.design.setups:
        st = ResultStore(result_dir(doc, s.name))
        labels = sorted(p.stem[len("huygens_"):] for p in st.folder.glob("huygens_*.npz")) \
            if st.folder.exists() else []
        if "LastAdaptive" in labels:
            labels.remove("LastAdaptive")
            labels.insert(0, "LastAdaptive")
        out += [f"{s.name} : {x}" for x in labels]
        rows = [r for v in (st.read_json(RCS_PASSES, {}) or {}).values() for r in v]
        if rows:
            fa = rows[-1].get("freq") or [0.0]
            out += [f"{s.name} : AdaptivePasses"] if len(fa) == 1 else \
                [f"{s.name} : AdaptivePasses @ {f / 1e9:.6g}GHz" for f in fa]
    return out


def report_solution_list(doc, kind: str) -> list[str]:
    """The solutions a report of this kind can show."""
    if kind in ANTENNA_KINDS:
        return antenna_solution_list(doc)
    if kind.startswith("Monostatic RCS"):
        return rcs_solution_list(doc)
    return solution_list(doc)


def load_rcs(st, label: str, sphere: str, design) -> dict:
    """Monostatic RCS over frequency, or over the adaptive passes ("freq" then holds the pass numbers)."""
    if label.startswith("AdaptivePasses"):
        k = 0
        if "@" in label:
            rows = (st.read_json(RCS_PASSES, {}) or {}).get(sphere) or [{}]
            fa = rows[-1].get("freq") or [0.0]
            target = float(label.split("@")[1].strip().lower().replace("ghz", ""))
            k = min(range(len(fa)), key=lambda i: abs(fa[i] / 1e9 - target))
        d = rcs_pass_series(st, sphere, k)
        if d is None:
            raise RuntimeError(f"No monostatic RCS per pass for '{sphere}' (analyze again with this version)")
        return d
    return monostatic_rcs(st, label or "LastAdaptive", sphere, design)


def load_antenna(doc, solution: str, sphere: str) -> dict:
    """Antenna parameters of a solution: over frequency, or over the adaptive passes."""
    setup, _, sol = [x.strip() for x in solution.partition(":")]
    st = ResultStore(result_dir(doc, setup))
    if not sphere:
        raise RuntimeError("Add a far field setup (Radiation > Insert Far Field Setup).")
    if sol.startswith("AdaptivePasses"):
        k = 0
        if "@" in sol:
            rows = (st.read_json(ANTENNA_PASSES, {}) or {}).get(sphere) or [{}]
            fa = rows[-1].get("freq") or [0.0]
            target = float(sol.split("@")[1].strip().lower().replace("ghz", ""))
            k = min(range(len(fa)), key=lambda i: abs(fa[i] / 1e9 - target))
        res = antenna_pass_series(st, sphere, k)
        if res is None:
            raise RuntimeError(f"No antenna parameters per pass for '{sphere}' (analyze again with this version)")
        x, vals = res
        return {"freq": x, "xaxis": "Pass", "solved": x, "antenna": vals}
    f, vals = antenna_series(st, doc.design, sphere, sol, doc.evaluator())
    return {"freq": f, "solved": f, "antenna": vals}


FAMILY_KINDS = ("Rectangular", "Smith Chart", "Data Table", TIME_KIND)


def family_solution_list(doc, name: str) -> list[str]:
    """Solutions of the solved variations of a parametric setup."""
    from ..solver.parametric import solved_variations
    from ..solver.results import _safe
    out = []
    for s in doc.design.setups:
        for _, folder in solved_variations(doc.project.path, doc.design_name, name):
            st = ResultStore(folder / _safe(s.name))
            sols = st.solutions()
            if sols:
                out += [f"{s.name} : {x}" for x in sols] + [f"{s.name} : {x}" for x in adaptive_pass_solutions(st)]
                break
    return out


def load_solution(doc, solution: str, root=None) -> tuple[ResultStore, str, dict | None]:
    """Solution data of the nominal design, or of a parametric variation (root: its folder)."""
    from ..solver.results import _safe
    setup, _, sol = [x.strip() for x in solution.partition(":")]
    st = ResultStore(result_dir(doc, setup) if root is None else root / _safe(setup))
    if sol.startswith("AdaptivePasses"):
        return st, sol, adaptive_pass_data(st, sol)
    return st, sol, st.sparams(sol)


def x_axis(data: dict) -> tuple[np.ndarray, str, float]:
    """(x values for plotting, axis label, scale from data['freq'] to x)."""
    if data.get("xaxis") == "Pass":
        return data["freq"], "Pass", 1.0
    return data["freq"] / 1e9, "Freq [GHz]", 1e-9


def apply_func(func: str, v: np.ndarray) -> np.ndarray:
    if func == "dB":
        return 20 * np.log10(np.maximum(np.abs(v), 1e-300))
    if func == "dB10":
        return 10 * np.log10(np.maximum(np.abs(v), 1e-300))
    if func == "mag":
        return np.abs(v)
    if func == "ang_deg":
        return np.degrees(np.angle(v))
    if func == "ang_rad":
        return np.angle(v)
    if func == "re":
        return np.real(v)
    if func == "im":
        return np.imag(v)
    return np.real(v)


_TRACE = re.compile(r"^\s*(?:(\w+)\()?\s*([SYZ])\((\d+),(\d+)\)\s*\)?\s*$")


def peaks(y: np.ndarray, minimum: bool = False) -> np.ndarray:
    yy = -y if minimum else y
    i = np.where((yy[1:-1] >= yy[:-2]) & (yy[1:-1] > yy[2:]))[0] + 1
    return i


def bandwidth(x: np.ndarray, y: np.ndarray, level: float) -> tuple[float, float, float] | None:
    """Band around the minimum of y where y <= level (e.g. dB(S(1,1)) <= -10): (low, high, centre)."""
    k = int(np.argmin(y))
    if y[k] > level:
        return None
    lo = k
    while lo > 0 and y[lo - 1] <= level:
        lo -= 1
    hi = k
    while hi < len(y) - 1 and y[hi + 1] <= level:
        hi += 1

    def cross(a, b):
        if y[b] == y[a]:
            return x[a]
        return x[a] + (level - y[a]) * (x[b] - x[a]) / (y[b] - y[a])
    xl = cross(lo - 1, lo) if lo > 0 else x[0]
    xh = cross(hi, hi + 1) if hi < len(y) - 1 else x[-1]
    return float(xl), float(xh), float(x[k])


def _templates() -> dict:
    try:
        return json.loads(QSettings("Hertzon", "Hertzon").value("report_templates", "{}") or "{}")
    except (ValueError, TypeError):
        return {}


# ----------------------------------------------------------------------------------------------
# far field
# ----------------------------------------------------------------------------------------------
RCS_QUANTITIES = ["MonostaticRCSTotal", "MonostaticRCSTheta", "MonostaticRCSPhi"]


def near_field_line(doc, solution: str, p1, p2, n: int, freq_index: int = 0, port: int = 1) -> dict:
    """near field line: E and H sampled from the stored solution along p1 -> p2."""
    import pyvista as pv
    from . import fields as F
    st, sol = F.store_for(doc, solution)
    grid, f = F.load_grid(st, sol)
    P = np.linspace(np.asarray(p1, float), np.asarray(p2, float), max(2, int(n)))
    work = grid.copy(deep=False)
    out = {"distance": np.linalg.norm(P - P[0], axis=1)}
    for w in ("E", "H"):
        key = f"{w}_{freq_index}_{port}"
        if key not in f:
            continue
        v = f[key].astype(np.complex128)
        work.point_data["re"] = np.real(v)
        work.point_data["im"] = np.imag(v)
        smp = pv.PolyData(P).sample(work)
        valid = np.asarray(smp.point_data.get("vtkValidPointMask", np.ones(len(P))), bool)
        c = smp.point_data["re"] + 1j * smp.point_data["im"]
        c[~valid] = np.nan
        out[f"Near{w}X"], out[f"Near{w}Y"], out[f"Near{w}Z"] = c[:, 0], c[:, 1], c[:, 2]
        out[f"Near{w}Total"] = np.sqrt((np.abs(c) ** 2).sum(axis=1))
    return out


def ff_db(func: str, name: str, v: np.ndarray) -> np.ndarray:
    """dB of a far field quantity: 10 log10 for power-like (gain, directivity), 20 log10 for fields."""
    if func in ("dB10", "dBsm") or (func == "dB" and not name.startswith("rE") and name != "AxialRatioValue"):
        return 10 * np.log10(np.maximum(v, 1e-30))
    if func == "dB":
        return 20 * np.log10(np.maximum(v, 1e-30))
    return v


# ----------------------------------------------------------------------------------------------
# widgets
# ----------------------------------------------------------------------------------------------
class ReportWindow(QWidget):
    """One report (tab in the main area) with a trace editor on the left."""

    changed = Signal()

    def __init__(self, doc, report: Report, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.report = report
        self.markers: list[float] = [float(x) for x in report.props.get("markers", "").split(",") if x.strip()]
        lay = QHBoxLayout(self)
        split = QSplitter()
        lay.addWidget(split)
        left = QWidget()
        ll = QVBoxLayout(left)
        form = QFormLayout()
        self.sol = QComboBox()
        self.sol.addItems(solution_list(doc) or [report.solution])
        self.sol.setCurrentText(report.solution)
        self.sol.currentTextChanged.connect(self._sol_changed)
        form.addRow("Solution:", self.sol)
        self.families = None
        if report.kind in FAMILY_KINDS and doc.design.parametrics:
            # Families: one curve per variation of a parametric setup
            fr = QHBoxLayout()
            self.families = QComboBox()
            self.families.addItems(["Nominal"] + [ps.name for ps in doc.design.parametrics])
            fam = report.props.get("families", "")
            self.families.setCurrentText(fam if fam in [ps.name for ps in doc.design.parametrics] else "Nominal")
            self.families.currentTextChanged.connect(self._families_changed)
            fv = QPushButton("Values...")
            fv.setToolTip("Choose the variable values shown (Families tab)")
            fv.clicked.connect(self.family_values)
            fr.addWidget(self.families, 1)
            fr.addWidget(fv)
            form.addRow("Families:", fr)
            if self._family():
                self._fill_solutions()
        self.cat = QComboBox()
        self.qty = QComboBox()
        self.func = QComboBox()
        if report.kind in ("Radiation Pattern", "3D Polar Plot"):
            cats = ff_categories(doc.design)
            self.cat.addItems(cats)
            cur = ff_trace(report.traces[0])[1] if report.traces else ""
            start = next((c for c in cats if cur in FF_CATEGORIES[c][0]), cats[0])
            self.cat.setCurrentText(start)
            self._ff_cat(start)
            self.cat.currentTextChanged.connect(self._ff_cat)
            self.cut = QComboBox()
            self.cut.addItems(["Phi", "Theta"])
            self.cut.setCurrentText(report.props.get("cut", "Phi"))
            self.cut_val = QLineEdit(report.props.get("cut_value", "0"))
            self.cut.currentTextChanged.connect(lambda *_: self._save_props())
            self.cut_val.editingFinished.connect(self._save_props)
            form.addRow("Constant:", self.cut)
            form.addRow("Value (deg):", self.cut_val)
        elif report.kind.startswith("Monostatic RCS"):
            self.sol.blockSignals(True)
            self.sol.clear()
            self.sol.addItems(rcs_solution_list(doc) or [report.solution])
            self.sol.setCurrentText(report.solution)
            self.sol.blockSignals(False)
            self.cat.addItems(["Monostatic RCS"])
            self.qty.addItems(RCS_QUANTITIES)
            self.func.addItems(["dBsm", "m^2"])
        elif report.kind in ANTENNA_KINDS:
            self.sol.blockSignals(True)
            self.sol.clear()
            self.sol.addItems(antenna_solution_list(doc) or [report.solution])
            self.sol.setCurrentText(report.solution)
            self.sol.blockSignals(False)
            self.cat.addItems(["Antenna Parameters"])
            self.qty.addItems(ANTENNA_QUANTITIES)
            self.func.addItems(["dB", "<none>"])
            self.sphere = QComboBox()
            self.sphere.addItems([sp.name for sp in doc.design.spheres])
            if report.props.get("sphere"):
                self.sphere.setCurrentText(report.props["sphere"])
            self.sphere.currentTextChanged.connect(self._sphere_changed)
            form.addRow("Far field setup:", self.sphere)
        elif report.kind == "Near Field Line":
            self.cat.addItems(["Near Fields"])
            self.qty.addItems(NF_QUANTITIES)
            self.func.addItems(["mag", "dB", "re", "im", "ang_deg"])
            self.nf = {}
            for key, label, default in (("start", "Start (x,y,z):", "0,0,0"), ("end", "End (x,y,z):", "10,0,0"),
                                        ("points", "Points:", "101")):
                e = QLineEdit(report.props.get(key, default))
                e.editingFinished.connect(self._save_nf)
                form.addRow(label, e)
                self.nf[key] = e
        elif report.kind == TIME_KIND:
            self.cat.addItems(list(TD_CATEGORIES))
            self.func.addItems(["<none>"])
            self.cat.currentTextChanged.connect(self._fill_qty)
            self.td = {}
            win = QComboBox()
            win.addItems(list(tdr.WINDOWS))
            win.setCurrentText(report.props.get("tdr_window", "Kaiser"))
            win.currentTextChanged.connect(lambda *_: self._save_td())
            self.td["tdr_window"] = win
            form.addRow("Window:", win)
            for key, label, tip in (
                    ("tdr_beta", "Kaiser beta:", "Larger: less ringing, slower rise (default 6)"),
                    ("tdr_rise_ps", "Rise time (ps):", "Gaussian window: 10-90 % rise time of the step; "
                                                        "empty: from the highest frequency"),
                    ("tdr_tmax_ns", "Max. time (ns):", "Empty: 2.5 times the longest delay of the sweep")):
                e = QLineEdit(report.props.get(key, ""))
                e.setToolTip(tip)
                e.editingFinished.connect(self._save_td)
                form.addRow(label, e)
                self.td[key] = e
        else:
            self.cat.addItems(CATEGORIES)
            self.func.addItems(FUNCS + ["<none>"])
            self.cat.currentTextChanged.connect(self._fill_qty)
        form.addRow("Category:", self.cat)
        form.addRow("Quantity:", self.qty)
        form.addRow("Function:", self.func)
        ll.addLayout(form)
        if report.kind not in ("Radiation Pattern", "3D Polar Plot"):
            er = QHBoxLayout()
            self.expr = QLineEdit()
            self.expr.setPlaceholderText("Expression, e.g. mag(S(2,1))^2 or an output variable")
            self.expr.returnPressed.connect(self._add_expr)
            eb = QPushButton("Add")
            eb.clicked.connect(self._add_expr)
            er.addWidget(self.expr)
            er.addWidget(eb)
            ll.addWidget(QLabel("Expression:"))
            ll.addLayout(er)
            ov = QPushButton("Output Variables...")
            ov.clicked.connect(self.edit_output_variables)
            ll.addWidget(ov)
        row = QHBoxLayout()
        add = QPushButton("New Trace")
        add.clicked.connect(self._add)
        app = QPushButton("Apply Trace")
        app.setToolTip("Apply Trace: replace the selected trace with the current quantity")
        app.clicked.connect(self._apply)
        rem = QPushButton("Remove")
        rem.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(app)
        row.addWidget(rem)
        ll.addLayout(row)
        ll.addWidget(QLabel("Traces:"))
        self.traces = QListWidget()
        self.traces.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.traces.setContextMenuPolicy(Qt.CustomContextMenu)
        self.traces.customContextMenuRequested.connect(self._trace_menu)
        self.traces.itemDoubleClicked.connect(lambda *_: self.trace_properties())
        ll.addWidget(self.traces)
        exp = QPushButton("Export Data...")
        exp.clicked.connect(self.export)
        ll.addWidget(exp)
        clr = QPushButton("Clear Markers")
        clr.clicked.connect(self._clear_markers)
        ll.addWidget(clr)
        self.info = QLabel()
        self.info.setWordWrap(True)
        ll.addWidget(self.info)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        if report.kind in ("Data Table", "Monostatic RCS Table", "Antenna Parameters Table"):
            self.table = QTableWidget()
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            rl.addWidget(self.table)
            self.fig = None
        elif report.kind == "3D Polar Plot":
            from pyvistaqt import QtInteractor
            self.p3 = QtInteractor(right)
            self.p3.set_background("white")
            rl.addWidget(self.p3.interactor)
            self.fig = None
        else:
            self.fig = Figure(figsize=(6, 4), tight_layout=True)
            self.canvas = FigureCanvasQTAgg(self.fig)
            rl.addWidget(NavigationToolbar2QT(self.canvas, right))
            rl.addWidget(self.canvas, 1)
            self.canvas.mpl_connect("button_press_event", self._click)
            self.canvas.mpl_connect("motion_notify_event", self._drag_move)
            self.canvas.mpl_connect("button_release_event", self._drag_end)
            self.canvas.mpl_connect("key_press_event", self._key)
            self.canvas.setFocusPolicy(Qt.ClickFocus)
            # marker table under the plot
            self.mtable = QTableWidget()
            self.mtable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            self.mtable.setMaximumHeight(150)
            self.mtable.setVisible(False)
            rl.addWidget(self.mtable)
        split.addWidget(right)
        split.setSizes([260, 800])
        self._fill_qty()
        self.refresh()

    # ---- trace editing ---------------------------------------------------------------------------
    def _ports(self) -> int:
        try:
            _, _, data = load_solution(self.doc, self.sol.currentText())
            return data["S"].shape[1] if data else len(self.doc.design.ports())
        except Exception:  # noqa: BLE001
            return max(1, len(self.doc.design.ports()))

    def _save_td(self) -> None:
        for k, w in self.td.items():
            self.report.props[k] = w.currentText() if isinstance(w, QComboBox) else w.text().strip()
        self.changed.emit()
        self.refresh()

    def _save_nf(self) -> None:
        for k, e in self.nf.items():
            self.report.props[k] = e.text()
        self.changed.emit()
        self.refresh()

    def _ff_cat(self, cat: str) -> None:
        qs, fs = FF_CATEGORIES.get(cat, ([], []))
        self.qty.clear()
        self.qty.addItems(qs)
        self.func.clear()
        self.func.addItems(fs)

    def _sphere_changed(self, name: str) -> None:
        self.report.props["sphere"] = name
        self.changed.emit()
        self.refresh()

    def _fill_qty(self, *_):
        if self.report.kind in ("Radiation Pattern", "3D Polar Plot", "Near Field Line") + ANTENNA_KINDS \
                or self.report.kind.startswith("Monostatic RCS"):
            return
        self.qty.clear()
        n = self._ports()
        cat = self.cat.currentText()
        if self.report.kind == TIME_KIND:
            pat = TD_CATEGORIES.get(cat, "TDRZ({i})")
            if "{j}" in pat:
                self.qty.addItems([pat.format(i=i, j=j) for i in range(1, n + 1) for j in range(1, n + 1)])
            else:
                self.qty.addItems([pat.format(i=i) for i in range(1, n + 1)])
        elif cat == "VSWR":
            self.qty.addItems([f"VSWR({i})" for i in range(1, n + 1)])
        elif cat == "Gamma":
            self.qty.addItems([f"S({i},{i})" for i in range(1, n + 1)])
        elif cat == "Group Delay":
            self.qty.addItems([f"GroupDelay(S({i},{j}))" for i in range(1, n + 1) for j in range(1, n + 1)])
        else:
            letter = cat[0]
            self.qty.addItems([f"{letter}({i},{j})" for i in range(1, n + 1) for j in range(1, n + 1)])

    def _apply(self) -> None:
        expr = self._expr()
        sel = [it.text() for it in self.traces.selectedItems() if it.text() in self.report.traces]
        if not sel and self.report.traces:
            sel = [self.report.traces[-1]]
        if not sel:
            return self._add()
        i = self.report.traces.index(sel[0])
        self.report.traces[i] = expr
        for t in sel[1:]:
            self.report.traces.remove(t)
        self.report.traces = list(dict.fromkeys(self.report.traces))
        self.changed.emit()
        self.refresh()

    def _expr(self) -> str:
        q = self.qty.currentText()
        if self.func.currentText() == "m^2":      # RCS in square metres: the plain quantity
            return q
        if self.report.kind in ("Radiation Pattern", "3D Polar Plot", "Near Field Line") \
                or self.report.kind.startswith("Monostatic RCS"):
            return f"{self.func.currentText()}({q})"
        if self.cat.currentText() in ("VSWR", "Group Delay") or self.func.currentText() == "<none>" \
                or self.report.kind == TIME_KIND:
            return q
        return f"{self.func.currentText()}({q})"

    def _add(self) -> None:
        expr = self._expr()
        if expr not in self.report.traces:
            self.report.traces.append(expr)
            self.changed.emit()
        self.refresh()

    def _remove(self) -> None:
        for it in self.traces.selectedItems():
            if it.text() in self.report.traces:
                self.report.traces.remove(it.text())
        self.changed.emit()
        self.refresh()

    # ---- families (parametric variations) ----------------------------------------------------------
    def _family(self) -> str:
        if self.families is None or self.families.currentText() == "Nominal":
            return ""
        return self.families.currentText()

    def _fill_solutions(self) -> None:
        fam = self._family()
        sols = family_solution_list(self.doc, fam) if fam else solution_list(self.doc)
        cur = self.report.solution
        self.sol.blockSignals(True)
        self.sol.clear()
        self.sol.addItems(sols or [cur])
        self.sol.setCurrentText(cur)
        self.sol.blockSignals(False)

    def _families_changed(self, name: str) -> None:
        self.report.props["families"] = self._family()
        self._fill_solutions()
        self.changed.emit()
        self.refresh()

    def _variations(self) -> list[tuple[str, object]]:
        """(legend suffix, folder) of the curves: the nominal design, or the chosen variations."""
        fam = self._family()
        if not fam:
            return [("", None)]
        from ..solver.parametric import solved_variations, variation_label
        try:
            keep = json.loads(self.report.props.get("family_values", "") or "{}")
        except ValueError:
            keep = {}
        out = []
        for texts, folder in solved_variations(self.doc.project.path, self.doc.design_name, fam):
            if all(texts.get(k) in v for k, v in keep.items() if k in texts and v):
                out.append(("  " + variation_label(texts), folder))
        if not out:
            raise RuntimeError(f"{fam}: no solved variation" + (" with the chosen values" if keep else
                                                                 " yet (analyze the parametric setup)"))
        return out

    def family_values(self) -> None:
        """Families tab: the values of every swept variable that are shown."""
        fam = self._family()
        if not fam:
            QMessageBox.information(self, "Families", "Choose a parametric setup under Families first.")
            return
        from ..solver.parametric import solved_variations
        vals: dict[str, list[str]] = {}
        for texts, _ in solved_variations(self.doc.project.path, self.doc.design_name, fam):
            for k, v in texts.items():
                if v not in vals.setdefault(k, []):
                    vals[k].append(v)
        try:
            keep = json.loads(self.report.props.get("family_values", "") or "{}")
        except ValueError:
            keep = {}
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Families: {fam}")
        lay = QHBoxLayout(dlg)
        lists = {}
        for k, vs in vals.items():
            col = QVBoxLayout()
            col.addWidget(QLabel(k))
            lw = QListWidget()
            for v in vs:
                it = QListWidgetItem(v)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if not keep.get(k) or v in keep[k] else Qt.Unchecked)
                lw.addItem(it)
            col.addWidget(lw)
            lay.addLayout(col)
            lists[k] = lw
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Vertical)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        sel = {}
        for k, lw in lists.items():
            on = [lw.item(i).text() for i in range(lw.count()) if lw.item(i).checkState() == Qt.Checked]
            if len(on) < lw.count():
                sel[k] = on
        self.report.props["family_values"] = json.dumps(sel) if sel else ""
        self.changed.emit()
        self.refresh()

    def _sol_changed(self, s: str) -> None:
        self.report.solution = s
        self.changed.emit()
        self._fill_qty()
        self.refresh()

    def _save_props(self) -> None:
        self.report.props["cut"] = self.cut.currentText()
        self.report.props["cut_value"] = self.cut_val.text()
        self.changed.emit()
        self.refresh()

    def _marker_near(self, ev, px: float = 8.0):
        """Index of the marker line within px pixels of the mouse, or None."""
        if ev.inaxes is None or not self.markers or not self._xy_plot():
            return None
        best, bd = None, px
        for i, mk in enumerate(self.markers):
            d = abs(ev.inaxes.transData.transform((mk, 0.0))[0] - ev.x)
            if d <= bd:
                best, bd = i, d
        return best

    def _store_markers(self) -> None:
        self.report.props["markers"] = ",".join(f"{m:.10g}" for m in self.markers)
        self.changed.emit()
        self.refresh()

    def delete_marker(self, i: int) -> None:
        if 0 <= i < len(self.markers):
            self.markers.pop(i)
            self._sel_marker = None
            self._store_markers()

    def _drag_move(self, ev) -> None:
        i = getattr(self, "_drag", None)
        if i is None:
            if self._marker_near(ev) is not None:
                self.canvas.setCursor(Qt.SizeHorCursor)
            else:
                self.canvas.unsetCursor()
            return
        if ev.inaxes is None or ev.xdata is None:
            return
        self.markers[i] = float(ev.xdata)
        ln = getattr(self, "_drag_line", None)
        if ln is not None:
            ln.set_xdata([ev.xdata, ev.xdata])
            self.canvas.draw_idle()

    def _drag_end(self, ev) -> None:
        if getattr(self, "_drag", None) is None:
            return
        self._drag = None
        self._drag_line = None
        self._store_markers()

    def _key(self, ev) -> None:
        if ev.key in ("delete", "backspace") and getattr(self, "_sel_marker", None) is not None:
            self.delete_marker(self._sel_marker)

    def _click(self, ev) -> None:
        near = self._marker_near(ev)
        if ev.button == 3 and near is not None:
            m = QMenu(self)
            m.addAction(f"Delete Marker m{near + 1}", lambda i=near: self.delete_marker(i))
            m.addAction("Clear All Markers", self._clear_markers)
            m.exec(QCursor.pos())
            return
        if ev.button == 3 and self.fig is not None and self.report.kind != "3D Polar Plot":
            self._plot_menu(ev)
            return
        if ev.button == 1 and near is not None and not ev.dblclick:
            # drag marker m(n) along the x axis; Del removes the selected marker
            self._drag = self._sel_marker = near
            self._drag_line = ev.inaxes.axvline(self.markers[near], color="#c0392b", lw=1.2)
            self.canvas.draw_idle()
            return
        if ev.inaxes is None or not self._xy_plot() or ev.button != 1 or ev.dblclick is False:
            return
        self.markers.append(float(ev.xdata))
        self.report.props["markers"] = ",".join(f"{m:g}" for m in self.markers)
        self.changed.emit()
        self.refresh()

    def _clear_markers(self) -> None:
        self.markers = []
        self.report.props["markers"] = ""
        self.report.props.pop("bandwidth", None)
        self.changed.emit()
        self.refresh()

    # ---- expressions, trace and axis properties (report editing) --------------------------------
    def _outvars(self) -> dict[str, str]:
        return output_variables(self.doc.design)

    def _add_expr(self) -> None:
        e = self.expr.text().strip()
        if not e:
            return
        try:
            _, _, data = self._data()
            if self.report.kind == TIME_KIND:
                tdr.eval_time(data, e, tdr.Options.from_props(self.report.props))
            else:
                eval_expr(data, e, self._outvars())
        except Exception as ex:  # noqa: BLE001
            self.info.setText(f"<span style='color:#c0392b'>{ex}</span>")
            return
        if e not in self.report.traces:
            self.report.traces.append(e)
            self.changed.emit()
        self.refresh()

    def edit_output_variables(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Output Variables")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("One variable per line: name = expression, e.g.  IL = -dB(S(2,1))"))
        ed = QPlainTextEdit(self.doc.design.settings.get("OutputVariables", ""))
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(460, 300)
        if dlg.exec() == QDialog.Accepted:
            self.doc.design.settings["OutputVariables"] = ed.toPlainText().strip()
            self.changed.emit()
            self.refresh()

    def _style(self, tr: str) -> dict:
        try:
            return json.loads(self.report.props.get(f"trace:{tr}", "{}"))
        except ValueError:
            return {}

    def _selected_trace(self) -> str | None:
        it = self.traces.currentItem()
        if it is not None:
            return it.text()
        return self.report.traces[0] if self.report.traces else None

    def _trace_menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction("Trace Properties...", self.trace_properties)
        m.addAction("Edit Expression...", self.edit_trace)
        m.addAction("Change Solution...", self.trace_solution)
        m.addSeparator()
        m.addAction("Delete", self._remove)
        m.exec(self.traces.mapToGlobal(pos))

    def edit_trace(self) -> None:
        tr = self._selected_trace()
        if tr is None:
            return
        txt, ok = QInputDialog.getText(self, "Edit Trace", "Expression:", text=tr)
        if ok and txt.strip() and txt.strip() != tr:
            i = self.report.traces.index(tr)
            self.report.traces[i] = txt.strip()
            st = self.report.props.pop(f"trace:{tr}", None)
            if st:
                self.report.props[f"trace:{txt.strip()}"] = st
            self.changed.emit()
            self.refresh()

    def trace_solution(self) -> None:
        """several solutions in one report; each trace may name its own solution."""
        tr = self._selected_trace()
        if tr is None:
            return
        expr, cur = split_trace(tr)
        items = ["<report solution>"] + report_solution_list(self.doc, self.report.kind)
        sol, ok = QInputDialog.getItem(self, "Trace Solution", "Solution:", items,
                                       items.index(cur) if cur in items else 0, False)
        if not ok:
            return
        new = expr if sol == "<report solution>" else f"{expr} @ {sol}"
        i = self.report.traces.index(tr)
        self.report.traces[i] = new
        st = self.report.props.pop(f"trace:{tr}", None)
        if st:
            self.report.props[f"trace:{new}"] = st
        self.changed.emit()
        self.refresh()

    def trace_properties(self) -> None:
        tr = self._selected_trace()
        if tr is None:
            return
        st = self._style(tr)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Trace Properties: {tr}")
        form = QFormLayout(dlg)
        color = [st.get("color", "")]
        cb = QPushButton(color[0] or "Automatic")

        def pick():
            c = QColorDialog.getColor(QColor(color[0] or "#1f77b4"), dlg)
            if c.isValid():
                color[0] = c.name()
                cb.setText(color[0])
        cb.clicked.connect(pick)
        form.addRow("Color:", cb)
        ls = QComboBox()
        ls.addItems(["Solid", "Dashed", "Dotted", "Dash-Dot"])
        ls.setCurrentText(st.get("style", "Solid"))
        form.addRow("Line style:", ls)
        lw = QDoubleSpinBox()
        lw.setRange(0.5, 8)
        lw.setSingleStep(0.5)
        lw.setValue(float(st.get("width", 1.5)))
        form.addRow("Line width:", lw)
        sym = QComboBox()
        sym.addItems(["None", "Circle", "Square", "Triangle", "Cross"])
        sym.setCurrentText(st.get("symbol", "None"))
        form.addRow("Symbol:", sym)
        y2 = QCheckBox("Plot on Y2 axis (right)")
        y2.setChecked(bool(st.get("y2", False)))
        form.addRow(y2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        self.report.props[f"trace:{tr}"] = json.dumps({"color": color[0], "style": ls.currentText(),
                                                       "width": lw.value(), "symbol": sym.currentText(),
                                                       "y2": y2.isChecked()})
        self.changed.emit()
        self.refresh()

    def axis_properties(self) -> None:
        p = self.report.props
        dlg = QDialog(self)
        dlg.setWindowTitle("Axis Properties")
        form = QFormLayout(dlg)
        edits = {}
        for key, label in (("xmin", "X min"), ("xmax", "X max"), ("xdiv", "X division"), ("ymin", "Y min"),
                           ("ymax", "Y max"), ("ydiv", "Y division"), ("y2min", "Y2 min"), ("y2max", "Y2 max"),
                           ("xlabel", "X label"), ("ylabel", "Y label"), ("y2label", "Y2 label")):
            e = QLineEdit(p.get(key, ""))
            e.setPlaceholderText("auto")
            form.addRow(label + ":", e)
            edits[key] = e
        checks = {}
        for key, label in (("xlog", "Logarithmic X"), ("ylog", "Logarithmic Y"), ("legend", "Show legend")):
            c = QCheckBox(label)
            c.setChecked(p.get(key, "True" if key == "legend" else "False") == "True")
            form.addRow(c)
            checks[key] = c
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        for k, e in edits.items():
            v = e.text().strip()
            if k not in ("xlabel", "ylabel", "y2label") and v:
                try:
                    float(v)
                except ValueError:
                    QMessageBox.warning(self, "Axis Properties", f"{k}: '{v}' is not a number")
                    return
            if v:
                p[k] = v
            else:
                p.pop(k, None)
        for k, c in checks.items():
            p[k] = str(c.isChecked())
        self.changed.emit()
        self.refresh()

    def _xy_plot(self) -> bool:
        """Line plot over frequency or passes: markers, axis and trace properties."""
        return self.report.kind in ("Rectangular", "Monostatic RCS", "Antenna Parameters")

    def _plot_menu(self, ev) -> None:
        m = QMenu(self)
        if self._xy_plot():
            mk = m.addMenu("Marker")
            mk.addAction("Add Marker at Max", lambda: self.marker_at("max"))
            mk.addAction("Add Marker at Min", lambda: self.marker_at("min"))
            mk.addAction("Next Max", lambda: self.marker_at("next_max"))
            mk.addAction("Next Min", lambda: self.marker_at("next_min"))
            if ev.xdata is not None:
                mk.addAction("Add Marker Here", lambda x=float(ev.xdata): self._add_marker(x))
            a = mk.addAction("Delta Markers (relative to m1)")
            a.setCheckable(True)
            a.setChecked(self.report.props.get("delta", "False") == "True")
            a.toggled.connect(lambda on: self._set_prop("delta", str(on)))
            mk.addAction("Bandwidth...", self.bandwidth_marker)
            mk.addSeparator()
            mk.addAction("Clear All Markers", self._clear_markers)
            m.addAction("Axis Properties...", self.axis_properties)
            m.addAction("Trace Properties...", self.trace_properties)
        m.addSeparator()
        m.addAction("Copy Image", self.copy_image)
        m.addAction("Copy Data", self.copy_data)
        m.addAction("Export...", self.export)
        m.addSeparator()
        m.addAction("Save As Template...", self.save_template)
        tm = m.addMenu("Apply Template")
        for name in sorted(_templates()):
            tm.addAction(name, lambda _=False, n=name: self.apply_template(n))
        if not _templates():
            tm.setEnabled(False)
        m.exec(QCursor.pos())

    def _set_prop(self, k: str, v: str) -> None:
        self.report.props[k] = v
        self.changed.emit()
        self.refresh()

    def _add_marker(self, x: float) -> None:
        self.markers.append(float(x))
        self.report.props["markers"] = ",".join(f"{m:.10g}" for m in self.markers)
        self.changed.emit()
        self.refresh()

    def marker_at(self, what: str) -> None:
        """Add Marker > Max / Min / Next Max / Next Min on the selected trace."""
        tr = self._selected_trace()
        if tr is None:
            return
        xs, y = self._trace_xy(tr)
        if what in ("max", "min"):
            k = int(np.argmax(y) if what == "max" else np.argmin(y))
        else:
            cand = peaks(y, minimum=(what == "next_min"))
            last = self.markers[-1] if self.markers else -np.inf
            cand = [i for i in cand if xs[i] > last + 1e-12 * max(1.0, abs(last) if np.isfinite(last) else 1.0)]
            if not cand:
                self.info.setText("No further peak to the right of the last marker")
                return
            k = int(cand[0])
        self._add_marker(float(xs[k]))

    def bandwidth_marker(self) -> None:
        tr = self._selected_trace()
        if tr is None:
            return
        lvl, ok = QInputDialog.getDouble(self, "Bandwidth", f"Level for {split_trace(tr)[0]}:",
                                         float(self.report.props.get("bw_level", "-10")), -1e9, 1e9, 3)
        if not ok:
            return
        self.report.props["bandwidth"] = tr
        self.report.props["bw_level"] = f"{lvl:g}"
        self.changed.emit()
        self.refresh()

    def copy_image(self) -> None:
        if self.fig is None:
            return
        QApplication.clipboard().setImage(self.canvas.grab().toImage())
        self.info.setText("Plot copied to the clipboard")

    def _columns(self):
        _, _, data = self._data()
        xs, xlabel, _ = x_axis(data)
        if self.report.kind == TIME_KIND and self.report.traces:
            xs, xlabel = self._trace_xy(self.report.traces[0], self._variations()[0][1])[0], "Time [ns]"
        return xs, xlabel, self._family_columns()

    def copy_data(self) -> None:
        xs, xlabel, cols = self._columns()
        lines = ["\t".join([xlabel] + [c[0] for c in cols])]
        for r in range(len(xs)):
            lines.append("\t".join([f"{xs[r]:.10g}"] + [f"{c[1][r]:.10g}" if r < len(c[1]) else "" for c in cols]))
        QApplication.clipboard().setText("\n".join(lines))
        self.info.setText("Data copied to the clipboard (tab separated)")

    def save_template(self) -> None:
        name, ok = QInputDialog.getText(self, "Report Template", "Template name:", text=self.report.name)
        if not ok or not name.strip():
            return
        t = _templates()
        t[name.strip()] = {"kind": self.report.kind, "traces": [split_trace(x)[0] for x in self.report.traces],
                           "props": {k: v for k, v in self.report.props.items() if k not in ("markers",)}}
        QSettings("Hertzon", "Hertzon").setValue("report_templates", json.dumps(t))
        self.info.setText(f"Template '{name.strip()}' saved")

    def apply_template(self, name: str) -> None:
        t = _templates().get(name)
        if not t:
            return
        self.report.traces = list(t.get("traces", []))
        self.report.props.update(t.get("props", {}))
        self.changed.emit()
        self.refresh()

    def _trace_xy(self, tr: str, root=None):
        if self.report.kind.startswith("Monostatic RCS"):
            f, _, _, cols, _ = self._rcs_series()
            y = dict(cols).get(tr)
            if y is None:
                raise RuntimeError(f"No data for '{tr}'")
            return f, y
        expr, sol = split_trace(tr)
        data = self._sol_data(sol, root)
        if self.report.kind == TIME_KIND:
            t, y, info = tdr.eval_time(data, expr, tdr.Options.from_props(self.report.props))
            self.__dict__.setdefault("_td_info", {})[tr] = info
            return t * 1e9, y
        xs, _, _ = x_axis(data)
        v = eval_expr(data, expr, self._outvars())
        return xs, (np.real(v) if np.iscomplexobj(v) else v.astype(float))

    def _sol_data(self, sol: str | None, root=None):
        cache = self.__dict__.setdefault("_cache", {})
        key = sol or self.sol.currentText()
        if (key, root) not in cache:
            if self.report.kind in ANTENNA_KINDS:
                cache[key, root] = load_antenna(self.doc, key, self._sphere_name())
                return cache[key, root]
            _, _, data = load_solution(self.doc, key, root)
            if data is None:
                raise RuntimeError(f"No solution data for '{key}'")
            cache[key, root] = data
        return cache[key, root]

    def _update_marker_table(self, rows: list[list[str]], header: list[str]) -> None:
        self.mtable.setVisible(bool(rows))
        self.mtable.clear()
        self.mtable.setColumnCount(len(header))
        self.mtable.setHorizontalHeaderLabels(header)
        self.mtable.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                self.mtable.setItem(r, c, QTableWidgetItem(v))

    # ---- drawing ------------------------------------------------------------------------------------
    def refresh(self) -> None:
        self._cache = {}
        self.traces.clear()
        self.traces.addItems(self.report.traces)
        try:
            if self.report.kind in ("Radiation Pattern", "3D Polar Plot"):
                self._draw_ff()
            elif self.report.kind.startswith("Monostatic RCS"):
                self._draw_rcs()
            elif self.report.kind == "Near Field Line":
                self._draw_near()
            elif self.report.kind in ("Data Table", "Antenna Parameters Table"):
                self._draw_table()
            elif self.report.kind == "Smith Chart":
                self._draw_smith()
            else:
                self._draw_rect()
        except Exception as e:  # noqa: BLE001
            self.info.setText(f"<span style='color:#c0392b'>{e}</span>")
            return
        self._mark_outdated()

    def _mark_outdated(self) -> None:
        from .trees import solution_state
        setup = self.sol.currentText().split(" : ")[0].strip()
        if setup and not self._family() and solution_state(self.doc, setup) == "outdated":
            self.info.setText("<span style='color:#b36b00'><b>Outdated:</b> the model or setup changed after this "
                              "solution was computed. Analyze again.</span>  " + self.info.text())
            try:
                for ax in self.fig.axes[:1]:
                    ax.set_title(ax.get_title() + "  [outdated]", color="#b36b00", fontsize=10)
                self.canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass

    def _sphere_name(self) -> str:
        sph = getattr(self, "sphere", None)
        return (sph.currentText() if sph is not None else "") or self.report.props.get("sphere") or \
            (self.doc.design.spheres[0].name if self.doc.design.spheres else "")

    def _data(self):
        if not self.doc.project.path:
            raise RuntimeError("Save and analyze the project first")
        if self.report.kind in ANTENNA_KINDS:
            setup, _, sol = [x.strip() for x in self.sol.currentText().partition(":")]
            st = ResultStore(result_dir(self.doc, setup))
            return st, sol, self._sol_data(None)
        st, sol, data = load_solution(self.doc, self.sol.currentText(), self._variations()[0][1])
        if data is None:
            raise RuntimeError(f"No solution data for '{self.sol.currentText()}'")
        return st, sol, data

    def _draw_markers(self, ax, curves: list, unit: str, xlabel: str) -> None:
        """markers m1, m2, ... (delta markers relative to m1, bandwidth) on the curves
        (trace, x, y, axes, colour), with the marker table."""
        p = self.report.props
        delta = p.get("delta", "False") == "True"
        rows = []
        for n, mk in enumerate(self.markers, start=1):
            row = [f"m{n}", f"{mk:.6g}{unit}"]
            for tr, xs, y, target, col in curves:
                if len(xs) == 0:
                    row.append("")
                    continue
                yi = float(np.interp(mk, xs, y))
                target.plot([mk], [yi], "v", color=col, ms=7)
                row.append(f"{yi:.5g}")
                if tr == curves[0][0]:
                    target.annotate(f"m{n}", (mk, yi), textcoords="offset points", xytext=(4, 6), fontsize=8,
                                    bbox=dict(boxstyle="round", fc="#ffffe0", ec="#999"))
            ax.axvline(mk, color="#888", lw=0.6, ls=":")
            rows.append(row)
        if delta and len(rows) > 1:
            base = self.markers[0]
            for n, mk in enumerate(self.markers[1:], start=2):
                row = [f"d(m{n},m1)", f"{mk - base:.6g}{unit}"]
                for tr, xs, y, target, col in curves:
                    row.append(f"{np.interp(mk, xs, y) - np.interp(base, xs, y):.5g}" if len(xs) else "")
                rows.append(row)
        bw = p.get("bandwidth")
        if bw:
            for tr, xs, y, target, col in curves:
                if tr != bw:
                    continue
                lvl = float(p.get("bw_level", "-10"))
                res = bandwidth(xs, y, lvl)
                target.axhline(lvl, color="#c0392b", lw=0.8, ls="--")
                if res:
                    lo, hi, c = res
                    target.axvspan(lo, hi, color="#c0392b", alpha=0.08)
                    rel = (hi - lo) / c * 100 if c else 0.0
                    rows.append([f"BW @ {lvl:g}", f"{lo:.6g} .. {hi:.6g}{unit}",
                                 f"BW {hi - lo:.5g}{unit}  ({rel:.3g} %)", f"centre {c:.6g}{unit}"])
                else:
                    rows.append([f"BW @ {lvl:g}", "level not reached"])
        hdr = ["Name", xlabel] + [c[0] for c in curves]
        if rows and len(rows[-1]) != len(hdr):
            hdr = hdr + [""] * max(0, max(len(r) for r in rows) - len(hdr))
        self._update_marker_table(rows, hdr)

    def _draw_rect(self) -> None:
        st, sol, data = self._data()
        p = self.report.props
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax2 = None
        xs0, xlabel, scale = x_axis(data)
        passes = data.get("xaxis") == "Pass"
        unit = "" if passes else " GHz"
        timedomain = self.report.kind == TIME_KIND
        if timedomain:
            xlabel, unit = "Time [ns]", " ns"
            self._td_info = {}
        LS = {"Solid": "-", "Dashed": "--", "Dotted": ":", "Dash-Dot": "-."}
        SYM = {"None": None, "Circle": "o", "Square": "s", "Triangle": "^", "Cross": "x"}
        curves = []
        variations = self._variations()
        for tr, suffix, root in [(t, s, r) for t in self.report.traces for s, r in variations]:
            xs, y = self._trace_xy(tr, root)
            stl = self._style(tr)
            if len(variations) > 1:
                stl = {k: v for k, v in stl.items() if k != "color"}     # one colour per variation
            target = ax
            if stl.get("y2"):
                if ax2 is None:
                    ax2 = ax.twinx()
                target = ax2
            kw = dict(label=tr + suffix + ("  [Y2]" if stl.get("y2") else ""), linestyle=LS.get(stl.get("style", "Solid"), "-"),
                      linewidth=float(stl.get("width", 1.5)))
            if stl.get("color"):
                kw["color"] = stl["color"]
            mk = SYM.get(stl.get("symbol", "None"))
            if mk is None and (len(xs) < 3 or passes):
                mk = "o"
            line, = target.plot(xs, y, marker=mk, **kw)
            curves.append((tr + suffix, xs, y, target, line.get_color()))
        self._draw_markers(ax, curves, unit, xlabel)
        if passes:
            from matplotlib.ticker import MaxNLocator
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        self._apply_axes(ax, ax2)
        ax.set_xlabel(p.get("xlabel") or xlabel)
        if p.get("ylabel"):
            ax.set_ylabel(p["ylabel"])
        if ax2 is not None and p.get("y2label"):
            ax2.set_ylabel(p["y2label"])
        ax.grid(True, alpha=0.4)
        if p.get("legend", "True") == "True":
            hs, ls = ax.get_legend_handles_labels()
            if ax2 is not None:
                h2, l2 = ax2.get_legend_handles_labels()
                hs, ls = hs + h2, ls + l2
            ax.legend(hs, ls, loc="best", fontsize=8)
        ax.set_title(f"{self.report.name}   ({self.sol.currentText()})", fontsize=10)
        self.canvas.draw_idle()
        solved = data.get("solved")
        msg = (f"{len(data['freq'])} points, {len(solved) if solved is not None else 0} solved. "
               "Double-click: marker. Right-click: markers, axes, traces, copy, templates.")
        if timedomain:
            f = data["freq"]
            bad = [tr for tr, i in getattr(self, "_td_info", {}).items() if not i["lowpass_ok"]]
            msg = (f"Low-pass transform of {len(f)} points, {f[0] / 1e9:.4g} to {f[-1] / 1e9:.4g} GHz "
                   f"(resolution about {0.7 / f[-1] * 1e12:.0f} ps). " + msg)
            if bad:
                msg = ("<span style='color:#c0392b'>The sweep starts too high for a reliable DC extrapolation "
                       f"({', '.join(bad)}): start the sweep lower (e.g. at its step size) or use "
                       "ImpulseBandPass.</span> " + msg)
        self.info.setText(msg)

    def _apply_axes(self, ax, ax2) -> None:
        p = self.report.props
        f = lambda k: float(p[k]) if p.get(k) not in (None, "") else None  # noqa: E731
        if p.get("xlog") == "True":
            ax.set_xscale("log")
        if p.get("ylog") == "True":
            ax.set_yscale("log")
        lo, hi = f("xmin"), f("xmax")
        if lo is not None or hi is not None:
            ax.set_xlim(left=lo, right=hi)
        lo, hi = f("ymin"), f("ymax")
        if lo is not None or hi is not None:
            ax.set_ylim(bottom=lo, top=hi)
        if ax2 is not None:
            lo, hi = f("y2min"), f("y2max")
            if lo is not None or hi is not None:
                ax2.set_ylim(bottom=lo, top=hi)
        from matplotlib.ticker import MultipleLocator
        if f("xdiv"):
            ax.xaxis.set_major_locator(MultipleLocator(f("xdiv")))
        if f("ydiv"):
            ax.yaxis.set_major_locator(MultipleLocator(f("ydiv")))

    def _rcs_series(self):
        """(x, x label, over passes, [(trace, values)], data) of the monostatic RCS traces."""
        cache = self.__dict__.setdefault("_cache", {})
        key = ("rcs", self.sol.currentText())
        if key not in cache:
            setup, _, label = [x.strip() for x in self.sol.currentText().partition(":")]
            st = ResultStore(result_dir(self.doc, setup))
            cache[key] = load_rcs(st, label, self._sphere_name(), self.doc.design)
        d = cache[key]
        passes = d.get("xaxis") == "Pass"
        f = d["freq"] if passes else d["freq"] / 1e9
        cols = []
        for tr in self.report.traces:
            m = re.match(r"(\w+)\((\w+)\)", tr)
            func, name = m.groups() if m else ("", tr)
            if name not in d:
                continue
            v = d[name]
            cols.append((tr, 10 * np.log10(np.maximum(v, 1e-300)) if func in ("dB10", "dBsm") else v))
        return f, ("Pass" if passes else "Freq [GHz]"), passes, cols, d

    def _draw_rcs(self) -> None:
        f, xl, passes, cols, d = self._rcs_series()
        if self.report.kind == "Monostatic RCS Table":
            self.table.setColumnCount(len(cols) + 1)
            self.table.setHorizontalHeaderLabels([xl] + [c[0] for c in cols])
            self.table.setRowCount(len(f))
            for r in range(len(f)):
                self.table.setItem(r, 0, QTableWidgetItem(f"{int(f[r])}" if passes else f"{f[r]:.6g}"))
                for c, (_, y) in enumerate(cols):
                    self.table.setItem(r, c + 1, QTableWidgetItem(f"{y[r]:.6g}"))
            return
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        curves = []
        for tr, y in cols:
            line, = ax.plot(f, y, marker="o" if len(f) < 3 or passes else None, label=tr)
            curves.append((tr, f, y, ax, line.get_color()))
        self._draw_markers(ax, curves, "" if passes else " GHz", xl)
        ax.set_xlabel(xl)
        if passes:
            from matplotlib.ticker import MaxNLocator
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        db = [c[0].startswith(("dB10", "dBsm")) for c in cols]
        ax.set_ylabel(self.report.props.get("ylabel") or
                      ("RCS [dBsm]" if db and all(db) else ("RCS [m²]" if not any(db) else "RCS [dBsm] / [m²]")))
        self._apply_axes(ax, None)
        if self.report.props.get("xlabel"):
            ax.set_xlabel(self.report.props["xlabel"])
        ax.grid(True, alpha=0.4)
        ax.legend(loc="best", fontsize=8)
        ax.set_title(f"{self.report.name}   ({self.sol.currentText()})", fontsize=10)
        self.canvas.draw_idle()
        self.info.setText(f"Monostatic RCS: backscatter towards the incident wave source, " +
                          (f"{len(f)} adaptive passes at {d['adaptive_freq'] / 1e9:.6g} GHz" if passes
                           else f"{len(f)} frequencies") + ". Double-click: marker. Right-click: markers, axes.")

    def _draw_near(self) -> None:
        ev = self.doc.evaluator()
        p = self.report.props
        p1 = ev.point(p.get("start", "0,0,0"))
        p2 = ev.point(p.get("end", "10,0,0"))
        n = int(float(p.get("points", "101") or 101))
        nf = near_field_line(self.doc, self.sol.currentText(), p1, p2, n)
        from ..model.expr import LENGTH_UNITS
        s = LENGTH_UNITS.get(self.doc.design.units, 1e-3)
        x = nf["distance"] / s
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        for tr in self.report.traces:
            m = re.match(r"(\w+)\((\w+)\)", tr)
            if not m or m.group(2) not in nf:
                continue
            func, name = m.groups()
            v = nf[name]
            y = apply_func("dB" if func == "dB" else func, v) if func != "mag" else np.abs(v)
            ax.plot(x, y, label=tr)
        ax.set_xlabel(f"Distance along line [{self.doc.design.units}]")
        ax.grid(True, alpha=0.4)
        ax.legend(loc="best", fontsize=8)
        ax.set_title(f"{self.report.name}   ({self.sol.currentText()})", fontsize=10)
        self.canvas.draw_idle()
        bad = int(np.isnan(nf.get("NearETotal", np.zeros(1))).sum())
        self.info.setText(f"{n} points from ({p.get('start')}) to ({p.get('end')})" +
                          (f"; {bad} points outside the solution volume" if bad else ""))

    def _draw_smith(self) -> None:
        st, sol, data = self._data()
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_aspect("equal")
        ax.axis("off")
        th = np.linspace(0, 2 * np.pi, 400)
        ax.plot(np.cos(th), np.sin(th), color="#555", lw=1)
        for r in (0.2, 0.5, 1, 2, 5):
            c, rad = r / (1 + r), 1 / (1 + r)
            ax.plot(c + rad * np.cos(th), rad * np.sin(th), color="#bbb", lw=0.6)
            ax.text(c - rad, 0.02, f"{r:g}", fontsize=7, color="#777")
        for x in (0.2, 0.5, 1, 2, 5):
            for s in (1, -1):
                xx = s * x
                zz = 1j * xx + np.linspace(0, 50, 2000)
                g = (zz - 1) / (zz + 1)
                ax.plot(g.real, g.imag, color="#bbb", lw=0.6)
        ax.plot([-1, 1], [0, 0], color="#bbb", lw=0.6)
        variations = self._variations()
        for tr, suffix, root in [(t, s, r) for t in self.report.traces for s, r in variations]:
            m = re.match(r"^\s*(?:\w+\()?S\((\d+),(\d+)\)\)?\s*$", tr)
            if not m:
                continue
            i, j = int(m.group(1)) - 1, int(m.group(2)) - 1
            g = (data if root is None else self._sol_data(None, root))["S"][:, i, j]
            ax.plot(g.real, g.imag, label=f"S({i + 1},{j + 1})" + suffix)
            ax.plot(g.real[:1], g.imag[:1], "o", ms=3, color="k")
        ax.legend(loc="upper right", fontsize=8)
        ax.set_title(f"{self.report.name}   ({self.sol.currentText()})", fontsize=10)
        self.canvas.draw_idle()

    def _family_columns(self) -> list[tuple[str, np.ndarray]]:
        """(name, values) of every trace, for each variation of the families (interpolated onto the first)."""
        variations = self._variations()
        xs0 = self._trace_xy(self.report.traces[0], variations[0][1])[0] if self.report.traces else None
        cols = []
        for tr in self.report.traces:
            for suffix, root in variations:
                xs, y = self._trace_xy(tr, root)
                if xs0 is not None and (len(xs) != len(xs0) or not np.allclose(xs, xs0)):
                    y = np.interp(xs0, xs, y, left=np.nan, right=np.nan)
                cols.append((tr + suffix, y))
        return cols

    def _draw_table(self) -> None:
        st, sol, data = self._data()
        cols = self._family_columns()
        f, xlabel, _ = x_axis(data)
        self.table.setColumnCount(1 + len(cols))
        self.table.setHorizontalHeaderLabels([xlabel] + [c[0] for c in cols])
        self.table.setRowCount(len(f))
        for r in range(len(f)):
            self.table.setItem(r, 0, QTableWidgetItem(f"{f[r]:.6g}"))
            for c, (_, y) in enumerate(cols):
                self.table.setItem(r, c + 1, QTableWidgetItem(f"{y[r]:.6g}"))

    def _ff(self):
        setup, _, sol = [x.strip() for x in self.sol.currentText().partition(":")]
        st = ResultStore(result_dir(self.doc, setup))
        sph = self.report.props.get("sphere") or (self.doc.design.spheres[0].name if self.doc.design.spheres else "")
        from ..solver.farfield_post import farfield
        if not sph:
            raise RuntimeError("Add a far field setup (Radiation > Insert Far Field Setup).")
        ff = farfield(st, self.doc.design, sph, "LastAdaptive", self.doc.evaluator())
        if ff is None:
            raise RuntimeError("No far field data: the solution has no radiation/PML boundary, or it was "
                               "solved with an older version (analyze once more).")
        from ..solver.ffquant import excited_quantities
        return excited_quantities(st, self.doc.design, ff, "LastAdaptive", 0)

    def _draw_ff(self) -> None:
        q = self._ff()
        th, ph = q["_theta"], q["_phi"]
        if self.report.kind == "3D Polar Plot":
            import pyvista as pv
            tr = self.report.traces[0] if self.report.traces else "dB(GainTotal)"
            func, name = ff_trace(tr)
            v = q[name]
            vv = ff_db(func, name, v)
            vmin = vv.max() - 40 if func in ("dB", "dB10", "dBsm") else 0
            r = np.clip(vv - vmin, 0, None)
            r = r / (r.max() or 1)
            T, P = np.meshgrid(th, ph, indexing="ij")
            X = r * np.sin(T) * np.cos(P)
            Y = r * np.sin(T) * np.sin(P)
            Z = r * np.cos(T)
            grid = pv.StructuredGrid(X, Y, Z)
            grid.point_data[tr] = vv.ravel(order="F")
            cam = self.p3.camera_position if getattr(self, "_p3_drawn", False) else None
            self.p3.clear()
            self.p3.add_mesh(grid, scalars=tr, cmap="jet", show_edges=False)
            self.p3.add_axes()
            if cam is None:                 # first draw; a redraw (Edit Sources, new solution) keeps the view
                self.p3.reset_camera()
                self._p3_drawn = True
            else:
                self.p3.camera_position = cam
            self.info.setText(f"Max {tr} = {vv.max():.3f}")
            return
        self.fig.clear()
        ax = self.fig.add_subplot(111, projection="polar")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        cut = self.report.props.get("cut", "Phi")
        val = np.radians(float(self.report.props.get("cut_value", "0") or 0))
        for tr in self.report.traces:
            func, name = ff_trace(tr)
            if name not in q:
                continue
            v = q[name]
            if cut == "Phi":
                k = int(np.argmin(np.abs(((ph - val + np.pi) % (2 * np.pi)) - np.pi)))
                ang, y = th, v[:, k]
                # include the opposite half plane (phi + 180) as negative theta
                k2 = int(np.argmin(np.abs(((ph - val - np.pi + np.pi) % (2 * np.pi)) - np.pi)))
                ang = np.r_[-th[::-1], th]
                y = np.r_[v[::-1, k2], y]
            else:
                k = int(np.argmin(np.abs(th - val)))
                ang, y = ph, v[k, :]
            yy = ff_db(func, name, y)
            ax.plot(ang, yy, label=tr)
        ax.legend(loc="lower right", fontsize=8)
        ax.set_title(f"{self.report.name}: {cut} = {np.degrees(val):.0f} deg", fontsize=10)
        self.canvas.draw_idle()
        self.info.setText(f"Radiated power {q['_Prad'][0]:.4g} W ({q.get('_Pinc', [1.0])[0]:.4g} W incident, Edit Sources)")

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Data", f"{self.report.name}.csv",
                                              "CSV (*.csv);;Touchstone (*.s*p);;PNG image (*.png)")
        if not path:
            return
        if path.endswith(".png") and self.fig is not None:
            self.fig.savefig(path, dpi=150)
            return
        st, sol, data = self._data()
        if ".s" in path[-5:]:
            if "S" not in data:
                self.info.setText("Touchstone needs S-parameters; export the antenna parameters as CSV")
                return
            if data.get("xaxis") == "Pass":
                raise_msg = "Touchstone needs a frequency solution, not adaptive passes"
                self.info.setText(raise_msg)
                return
            st.write_touchstone(sol, data["freq"], data["S"], data["z0"], path)
            return
        fc = self._family_columns()
        arr = np.column_stack([data["freq"]] + [c[1] for c in fc]) if self.report.kind != TIME_KIND else \
            np.column_stack([self._columns()[0] * 1e-9] + [c[1] for c in fc])
        xh = "Pass" if data.get("xaxis") == "Pass" else ("Time [s]" if self.report.kind == TIME_KIND else "Freq [Hz]")
        np.savetxt(path, arr, delimiter=",", header=",".join([xh] + [c[0] for c in fc]), comments="")
