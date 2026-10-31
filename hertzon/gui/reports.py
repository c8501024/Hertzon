"""Result reports: rectangular, Smith, polar, radiation pattern, 3D polar and data table."""
from __future__ import annotations

import re

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
import json

from PySide6.QtCore import Signal, Qt, QSettings
from PySide6.QtGui import QCursor, QColor
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QListWidget, QPushButton, QLabel,
                               QTableWidget, QTableWidgetItem, QFileDialog, QSplitter, QFormLayout, QLineEdit,
                               QAbstractItemView, QHeaderView, QMenu, QApplication, QDialog, QDialogButtonBox,
                               QCheckBox, QInputDialog, QColorDialog, QDoubleSpinBox, QPlainTextEdit, QMessageBox,
                               QListWidgetItem, QScrollArea, QFrame)

from ..model.design import Report
from ..solver.results import ResultStore, results_dir
from .doc import result_dir
from ..solver.ffquant import monostatic_rcs, farfield_quantities, antenna_parameters  # noqa: F401
from ..solver.ffquant import ANTENNA_QUANTITIES, ANTENNA_POWER, ANTENNA_PASSES, antenna_series, antenna_pass_series
from ..solver.ffquant import RCS_PASSES, rcs_pass_series
from ..solver.expressions import (network, split_trace, output_variables, _FUNCS, _ast_eval,  # noqa: F401
                                  eval_expr, eval_trace, with_pairs)

ANTENNA_KINDS = ("Antenna Parameters", "Antenna Parameters Table")

ETA0 = 376.730313668
FUNCS = ["dB", "mag", "ang_deg", "ang_rad", "re", "im", "dB10"]
CATEGORIES = ["S Parameter", "Y Parameter", "Z Parameter", "VSWR", "Gamma", "Group Delay", "Port Zo",
              "Propagation Constant"]
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


# units of the report quantities (linear); dB of them is "dB", of an area "dBsm"
_QTY_UNITS = {"S": "", "Y": "S", "Z": "Ω", "VSWR": "", "Zo": "Ω", "Gamma": "1/m", "Freq": "Hz", "F": "Hz",
              "RadiatedPower": "W", "AcceptedPower": "W", "IncidentPower": "W", "MaxU": "W/sr",
              "ThetaAtMax": "deg", "PhiAtMax": "deg"}
_PREFIX_UNITS = (("rE", "V"), ("NearE", "V/m"), ("NearH", "A/m"), ("BistaticRCS", "m²"), ("MonostaticRCS", "m²"))


def trace_unit(doc, trace: str) -> str | None:
    """Unit of what a trace plots: 'dB', 'Ω', 'W', 'V/m', '' (dimensionless), None when unknown (an output
    variable, a sum of different units)."""
    import ast
    expr = split_trace(trace)[0]
    try:
        tree = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return None
    eqs = {e.name: e.expr for e in doc.design.field_exprs}

    def name_unit(n):
        if n in eqs:
            from ..post.calc import lang as L
            from .calc_bridge import make_env
            try:
                return L.unit_label(L.analyze(n, make_env(doc)))
            except L.CalcError:
                return None
        if n in _QTY_UNITS:
            return _QTY_UNITS[n]
        for pre, u in _PREFIX_UNITS:
            if n.startswith(pre):
                return u
        if n in ANTENNA_QUANTITIES or n.startswith(("Gain", "Dir", "RealizedGain", "AxialRatio", "Peak")) \
                or n in ("RadiationEfficiency", "FrontToBackRatio"):
            return ""
        return None

    def unit(node):
        if isinstance(node, ast.Constant):
            return ""
        if isinstance(node, ast.Name):
            return name_unit(node.id)
        if isinstance(node, ast.UnaryOp):
            return unit(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = unit(node.left), unit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                return a if a == b else None
            if isinstance(node.op, (ast.Mult, ast.Div)):
                if b == "":
                    return a
                if a == "" and isinstance(node.op, ast.Mult):
                    return b
            return None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn, args = node.func.id, node.args
            if fn in ("S", "Y", "Z", "VSWR", "Zo", "Gamma"):
                return _QTY_UNITS[fn]
            if fn == "GroupDelay":
                return "s"
            if not args:
                return None
            inner = unit(args[0])
            if fn in ("dB", "dB10", "dB20"):
                return "dBsm" if inner == "m²" else "dB"
            if fn == "dBsm":
                return "dBsm"
            if fn in ("ang_deg", "phase_deg"):
                return "deg"
            if fn in ("ang_rad", "phase", "angle"):
                return "rad"
            if fn in ("mag", "abs", "re", "im", "real", "imag", "conj", "x", "y", "z", "rho", "phi", "r", "theta"):
                return inner
        return None
    return unit(tree)


def unit_label(units: list) -> str:
    """Axis label from the units of its traces: '[dB]', '[Ω] / [S]'; '' when unknown or dimensionless."""
    known = [u for u in dict.fromkeys(units) if u]
    return " / ".join(f"[{u}]" for u in known)


def with_unit(doc, trace: str) -> str:
    """'dB(S(1,1)) [dB]': a trace name with its unit (table headers)."""
    u = trace_unit(doc, trace)
    return f"{trace} [{u}]" if u else trace


def is_fields_report(doc, report: Report) -> bool:
    """A Fields report (equations computed from the fields), also one made before reports had the flag."""
    if report.kind not in ("Rectangular", "Data Table"):
        return False
    if report.props.get("fields"):
        return True
    from .calc_bridge import field_names_in, uses_fields
    return bool(report.traces) and uses_fields(doc, field_names_in(doc.design, split_trace(report.traces[0])[0]))


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
    out = {"freq": x, "S": np.array(S), "z0": z0, "solved": x, "ports": last["ports"] if last is not None else [],
           "xaxis": "Pass", "adaptive_freq": fa[k]}
    if last is not None and last.get("terminals"):
        out["terminals"] = last["terminals"]
    return out


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


def ff_solution_list(doc) -> list[str]:
    """Solutions with stored far field surface fields (LastAdaptive and the sweeps that saved fields)."""
    out = []
    if not doc.project.path:
        return out
    for s in doc.design.setups:
        st = ResultStore(result_dir(doc, s.name))
        out += [f"{s.name} : {sol}" for sol in st.solutions() if (st.folder / f"huygens_{sol}.npz").exists()]
    return out


def sweeps_without_fields(doc) -> list[str]:
    """Solved sweeps without far field surface fields (Interpolating, or Save fields off)."""
    out = []
    if not doc.project.path:
        return out
    for s in doc.design.setups:
        st = ResultStore(result_dir(doc, s.name))
        out += [f"{s.name} : {sol}" for sol in st.solutions()
                if sol != "LastAdaptive" and not (st.folder / f"huygens_{sol}.npz").exists()]
    return out


def ff_frequencies(ff: dict) -> list[tuple[int, float]]:
    """(index k, frequency in Hz) of the far fields of a solution, sorted by frequency."""
    ks = {int(k.split("_")[1]) for k in ff if k.startswith("freq_")}
    return sorted(((k, float(ff[f"freq_{k}"][0])) for k in ks), key=lambda x: x[1])


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
    if kind in ("Radiation Pattern", "3D Polar Plot"):
        return ff_solution_list(doc)
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


FAMILY_KINDS = ("Rectangular", "Smith Chart", "Data Table")
XVAR_POINTS = 101                 # points of a plot against a design variable (tuning model)


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
        return st, sol, with_pairs(adaptive_pass_data(st, sol), doc.design)
    return st, sol, with_pairs(st.sparams(sol), doc.design)


def x_axis(data: dict) -> tuple[np.ndarray, str, float]:
    """(x values for plotting, axis label, scale from data['freq'] to x)."""
    if data.get("xaxis") == "Pass":
        return data["freq"], "Pass", 1.0
    if data.get("xaxis") == "Variable":
        return data["freq"], data["xlabel"], 1.0
    return data["freq"] / 1e9, "Freq [GHz]", 1e-9


def flat_ylim(ax, ys: list, rel: float = 1e-9) -> None:
    """y range of curves that are constant up to rounding (the Zo of a TEM port over a sweep): +-1 % around
    the value, not an autoscale onto the last digits that draws the rounding noise as a random curve."""
    v = np.concatenate([np.ravel(np.asarray(y, float)) for y in ys]) if ys else np.zeros(0)
    v = v[np.isfinite(v)]
    if not len(v):
        return
    lo, hi = float(v.min()), float(v.max())
    mid = 0.5 * (lo + hi)
    if hi - lo > rel * max(abs(lo), abs(hi)):
        return
    half = 0.01 * abs(mid) or 1.0
    ax.set_ylim(mid - half, mid + half)
    fmt = ax.yaxis.get_major_formatter()
    if hasattr(fmt, "set_useOffset"):
        fmt.set_useOffset(False)


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


def _signed_deg(x: float, _pos=None) -> str:
    """Polar tick label of an angle x (rad) on -180..180 deg."""
    d = round((float(np.degrees(x)) + 180) % 360 - 180, 6)
    return "\u00b1180\u00b0" if abs(d) == 180 else f"{d:g}\u00b0"


def beamwidth(x: np.ndarray, y: np.ndarray, rel: float) -> dict | None:
    """Main lobe of a pattern cut y(x) (x in deg): the angles where y falls rel dB (e.g. -3) below the
    maximum, the width between them and the side lobe level (highest other peak relative to the maximum).
    A cut over the full circle wraps around."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(y) < 3:
        return None
    circ = x[-1] - x[0] >= 360 - 1e-6
    if circ:                                    # the last point repeats the first one: unroll three turns
        x, y = x[:-1], y[:-1]
        n = len(y)
        x, y, k = np.r_[x - 360, x, x + 360], np.tile(y, 3), int(np.argmax(y)) + n
    else:
        n, k = len(y), int(np.argmax(y))
    level = y[k] + rel

    def edge(step: int) -> tuple[float, bool, int]:
        """(angle where the lobe falls to level, found, last index of the lobe down to its first minimum)."""
        i, found, lobe = k, None, None
        for _ in range(n - 1):
            j = i + step
            if not 0 <= j < len(y):
                break
            if found is None and y[j] < level:
                found = x[i] + (level - y[i]) * (x[j] - x[i]) / (y[j] - y[i])
            if lobe is None and y[j] > y[i]:
                lobe = i
            if found is not None and lobe is not None:
                break
            i = j
        return (x[i] if found is None else found), found is not None, (i if lobe is None else lobe)

    lo, ok_lo, l0 = edge(-1)
    hi, ok_hi, l1 = edge(1)
    wrap = (lambda v: (v + 180) % 360 - 180) if circ and x[n] < 0 <= x[2 * n - 1] else \
        (lambda v: v % 360) if circ else (lambda v: v)
    out = {"lo": float(wrap(lo)), "hi": float(wrap(hi)), "width": float(hi - lo), "max_at": float(wrap(x[k])),
           "max": float(y[k]), "open": not (ok_lo and ok_hi)}
    # side lobes: the highest peak outside the main lobe (between its first minima)
    rng = range(k - n + 1, k + n) if circ else range(n)
    side = [i for i in rng if 0 < i < len(y) - 1 and not l0 <= i <= l1 and y[i] >= y[i - 1] and y[i] > y[i + 1]]
    if side:
        j = max(side, key=lambda i: y[i])
        out["sll"], out["sll_at"] = float(y[j] - y[k]), float(wrap(x[j]))
    return out


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
            self.sol.blockSignals(True)
            self.sol.clear()
            self.sol.addItems(ff_solution_list(doc) or [report.solution])
            self.sol.setCurrentText(report.solution)
            self.sol.blockSignals(False)
            # a sweep with fields has a far field at every frequency: pick one (Families > Freq)
            self.ff_freq = QComboBox()
            self.ff_freq.currentIndexChanged.connect(self._ff_freq_changed)
            form.addRow("Frequency:", self.ff_freq)
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
            if report.kind == "Radiation Pattern":   # the 2D cut; the 3D polar plot shows the whole sphere
                form.addRow("Constant:", self.cut)
                form.addRow("Value (deg):", self.cut_val)
                self.ff_plot = QComboBox()
                self.ff_plot.addItems(["Polar", "Rectangular"])
                self.ff_plot.setCurrentText(report.props.get("plot", "Polar"))
                self.ff_plot.setToolTip("Rectangular: the cut over the angle, with markers")
                self.ff_plot.currentTextChanged.connect(lambda *_: self._save_props())
                form.addRow("Plot:", self.ff_plot)
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
        elif is_fields_report(doc, report):
            # Fields report: the equations computed from the fields, over the frequencies with saved fields
            from .fields import field_solutions
            sols = field_solutions(doc)
            if sols:
                self.sol.blockSignals(True)
                self.sol.clear()
                self.sol.addItems(sols)
                self.sol.setCurrentText(report.solution if report.solution in sols else sols[0])
                self.sol.blockSignals(False)
                report.solution = self.sol.currentText()
            self.cat.addItem("Equations")
            self.func.addItems(FUNCS + ["<none>"])
        else:
            self.cat.addItems(CATEGORIES)
            if report.kind in ("Rectangular", "Data Table"):
                self.cat.addItem("Equations")       # equations of S parameters (Fields Calculator)
                from .calc_bridge import field_names_in
                if report.traces and field_names_in(doc.design, report.traces[0]):
                    self.cat.setCurrentText("Equations")
            self.func.addItems(FUNCS + ["<none>"])
            self.cat.currentTextChanged.connect(self._fill_qty)
        form.addRow("Category:", self.cat)
        form.addRow("Quantity:", self.qty)
        form.addRow("Function:", self.func)
        self._form = form
        self.xvar = self.xfreq = None
        if report.kind in self.XVAR_KINDS and not is_fields_report(doc, report):
            # with a tuning model (Compute Derivatives): plot against a design variable at one frequency
            self.xvar = QComboBox()
            self.xvar.setToolTip("Plot against frequency, or against a design variable over its tuning range at "
                                 "one frequency (from the tuning model of the solution: Derivative Tuning)")
            self.xfreq = QComboBox()
            self.xfreq.setToolTip("Frequency of the plot against a variable")
            form.addRow("X axis:", self.xvar)
            form.addRow("At:", self.xfreq)
            self._fill_xaxis()
            self.xvar.currentIndexChanged.connect(lambda *_: self._xaxis_changed())
            self.xfreq.currentIndexChanged.connect(lambda *_: self._xaxis_changed())
        ll.addLayout(form)
        # what the numbers in S(i,j), Zo(i) ... are: the ports, or the terminals of a terminal solution
        self.numbering = QLabel()
        self.numbering.setWordWrap(True)
        self.numbering.setVisible(False)
        ll.addWidget(self.numbering)
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
        if report.kind in ("Radiation Pattern", "3D Polar Plot") + ANTENNA_KINDS:
            miss = sweeps_without_fields(doc)
            if miss:
                hint = QLabel(f"Not in the list (no fields saved): {', '.join(miss)}. Far fields over frequency "
                              "need a Discrete or Fast sweep with 'Save fields (all frequencies)'.")
                hint.setWordWrap(True)
                hint.setStyleSheet("color:#7a6a00")
                ll.addWidget(hint)
        # the trace editor scrolls instead of setting a minimum height: its ~430 px (more with display
        # scaling) would otherwise become the main window's minimum and push a maximized window off screen
        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(left.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width())
        split.addWidget(scroll)
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
        split.setCollapsible(0, False)
        self._fill_qty()
        from .tuning import tuning_state
        self._tuned_shown = False
        tuning_state(doc).changed.connect(self._tuning_changed)
        self.refresh()

    # ---- derivative tuning -----------------------------------------------------------------------
    TUNED_KINDS = ("Rectangular", "Smith Chart", "Data Table", "Radiation Pattern", "3D Polar Plot") + ANTENNA_KINDS
    FF_KINDS = ("Radiation Pattern", "3D Polar Plot")

    def _tuned(self) -> bool:
        """Draw the tuned curves: the slider panel tunes this report's solution, a slider is off its value."""
        from .tuning import tuning_state
        sol = self.sol.currentText()
        if self.report.kind not in self.TUNED_KINDS or self._family() or "AdaptivePasses" in sol or self._xvar():
            return False
        ts = tuning_state(self.doc)
        if self.report.kind in self.FF_KINDS + ANTENNA_KINDS:
            # a pattern of the same setup (LastAdaptive or a sweep with fields): the tuned design has a far
            # field wherever the tuning model has the pattern frequency (_ff_tuned says when it has not)
            return ts.active(ts.solution) and sol.split(":")[0].strip() == ts.solution.split(":")[0].strip()
        return ts.active(sol)

    # ---- plot against a design variable (tuning model) ---------------------------------------------
    XVAR_KINDS = ("Rectangular", "Data Table")

    def _tuning_model(self):
        """Tuning model of the report solution: the one of the slider panel, else read from the results."""
        from .tuning import tuning_state, same_solution
        from ..solver.tuning import TuningModel, tuning_path
        sol = self.sol.currentText()
        ts = tuning_state(self.doc)
        if ts.model is not None and same_solution(sol, ts.solution):
            return ts.model
        setup, _, sw = [x.strip() for x in sol.partition(":")]
        if not self.doc.project.path or not setup or not sw or "AdaptivePasses" in sw:
            return None
        path = tuning_path(result_dir(self.doc, setup), sw)
        try:
            key = (str(path), path.stat().st_mtime)
        except OSError:
            return None
        cached = getattr(self, "_tmodel", None)
        if cached is None or cached[0] != key:
            try:
                cached = (key, TuningModel(path))
            except Exception:  # noqa: BLE001
                cached = (key, None)
            self._tmodel = cached
        return cached[1]

    def _fill_xaxis(self) -> None:
        m = self._tuning_model()
        p = self.report.props
        for w in (self.xvar, self.xfreq):
            w.blockSignals(True)
            w.clear()
        self.xvar.addItem("Frequency", "")
        if m is not None:
            for name in m.variables:
                self.xvar.addItem(name, name)
            for f in m.freq:
                self.xfreq.addItem(f"{f / 1e9:.6g} GHz", float(f))
        self.xvar.setCurrentIndex(max(self.xvar.findData(p.get("x_var", "")), 0))
        want = float(p.get("x_freq", "") or "nan")
        if self.xfreq.count() and np.isfinite(want):
            self.xfreq.setCurrentIndex(min(range(self.xfreq.count()),
                                           key=lambda i: abs(self.xfreq.itemData(i) - want)))
        for w in (self.xvar, self.xfreq):
            w.blockSignals(False)
        self._form.setRowVisible(self.xvar, m is not None)
        self._form.setRowVisible(self.xfreq, m is not None and bool(self.xvar.currentData()))

    def _xaxis_changed(self) -> None:
        self.report.props["x_var"] = self.xvar.currentData() or ""
        if self.xfreq.count():
            self.report.props["x_freq"] = repr(self.xfreq.currentData())
        self._form.setRowVisible(self.xfreq, bool(self.xvar.currentData()))
        self.changed.emit()
        self.refresh()

    def _xvar(self):
        """(model, variable index, frequency index) of a plot against a variable, None for frequency."""
        name = self.report.props.get("x_var", "")
        if not name or self.report.kind not in self.XVAR_KINDS or self._family():
            return None
        m = self._tuning_model()
        if m is None or name not in m.variables:
            return None
        want = float(self.report.props.get("x_freq", "") or "nan")
        k = int(np.argmin(np.abs(m.freq - want))) if np.isfinite(want) else 0
        return m, m.variables.index(name), k

    def _xvar_data(self, key: str) -> dict:
        """Solution data over the tuning range of the x variable at one frequency, the other variables at
        their slider values (the solved values while the slider panel is closed)."""
        from .tuning import WARN_FRACTION, slider_range, tuning_state, same_solution
        m, i, k = self._xvar()
        ts = tuning_state(self.doc)
        dp = np.array(ts.dp, float) if (ts.model is m and ts.dp is not None and same_solution(key, ts.solution)) \
            else np.zeros(len(m.variables))
        lo, hi = slider_range(m, i)
        x = m.values[i] + np.linspace(lo, hi, XVAR_POINTS)
        S, z = m.sweep_variable(k, i, x - m.values[i], dp)
        _, _, base = load_solution(self.doc, key, None)
        d = {kk: v for kk, v in (base or {}).items() if kk not in ("freq", "S", "z0", "solved", "gamma", "zo")}
        unit = m.units[i]
        d.update(freq=x, S=S, z0=z, xaxis="Variable", xlabel=f"{m.variables[i]}" + (f" [{unit}]" if unit else ""),
                 xunit=unit, x0=float(m.values[i]), warn=[(m.values[i] + a, m.values[i] + WARN_FRACTION * a)
                                                          for a in (lo, hi) if a], xnow=float(m.values[i] + dp[i]), at=float(m.freq[k]),
                 others=[t for t in ts.texts if not t.startswith(m.variables[i] + " ")] if np.any(dp) else [])
        if "ports" not in d:
            d["ports"] = m.meta.get("ports") or []
        return with_pairs(d, self.doc.design)

    def _tuning_changed(self) -> None:
        if self._tuned() or self._tuned_shown or self._xvar():
            if self.isVisible():
                self.refresh(keep_cache=True)
            else:
                self._tuning_pending = True     # a tab in the background: drawn when it is shown

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if getattr(self, "_tuning_pending", False):
            self._tuning_pending = False
            self.refresh(keep_cache=True)

    # ---- trace editing ---------------------------------------------------------------------------
    def _ports(self) -> int:
        try:
            _, _, data = load_solution(self.doc, self.sol.currentText())
            return data["S"].shape[1] if data else len(self.doc.design.ports())
        except Exception:  # noqa: BLE001
            return max(1, len(self.doc.design.ports()))

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

    def _s_labels(self) -> list[tuple[str, str]]:
        try:
            from ..solver.results import s_labels
            st, _, _ = load_solution(self.doc, self.sol.currentText())
            return s_labels(st)
        except Exception:  # noqa: BLE001
            return []

    def _show_numbering(self, cat: str) -> None:
        """The names behind the numbers (Terminals: 1 = P1_TraceP, ...) under the quantity, and as tool tips of
        the quantities."""
        rows = [] if cat in ("Equations", "Propagation Constant") else self._s_labels()
        names = {str(i + 1): n for i, (n, _) in enumerate(rows)}
        term = bool(rows) and rows[0][1] == "Terminal"
        self.numbering.setVisible(len(rows) > 1)
        self.numbering.setText(("Terminals: " if term else "Ports: ")
                               + ", ".join(f"{i} = {n}" for i, n in names.items()))
        for k in range(self.qty.count()):
            idx = re.findall(r"[(,]\s*(\w+)", self.qty.itemText(k))
            tip = ", ".join(f"{i} = {names[i]}" for i in dict.fromkeys(idx) if i in names)
            if tip:
                self.qty.setItemData(k, tip, Qt.ToolTipRole)

    def _fill_qty(self, *_):
        if self.report.kind in ("Radiation Pattern", "3D Polar Plot", "Near Field Line") + ANTENNA_KINDS \
                or self.report.kind.startswith("Monostatic RCS"):
            return
        self.qty.clear()
        try:
            self._fill_qty_items()
        finally:
            if getattr(self, "numbering", None) is not None:
                self._show_numbering(self.cat.currentText())

    def _fill_qty_items(self) -> None:
        n = self._ports()
        cat = self.cat.currentText()
        if cat == "Equations":
            from .calc_bridge import value_names
            self.qty.addItems(value_names(self.doc, fields=is_fields_report(self.doc, self.report)))
            self.func.setCurrentText("<none>")       # values in their own unit (W, V, J, ...)
            return
        if cat in ("Port Zo", "Propagation Constant"):
            key, fn = ("zo", "Zo") if cat == "Port Zo" else ("gamma", "Gamma")
            k = self._port_quantity_count(key)
            self.qty.addItems([f"{fn}({i})" for i in range(1, k + 1)])
            return
        # with differential pairs: first the mixed-mode quantities, then the single-ended ones
        rows = self._row_names(n)
        for mm in ([self._mixed_ports(n)] if self._mixed_ports(n) else []) + [rows]:
            if cat == "VSWR":
                self.qty.addItems([f"VSWR({i})" for i in mm])
            elif cat == "Gamma":
                self.qty.addItems([f"S({i},{i})" for i in mm])
            elif cat == "Group Delay":
                self.qty.addItems([f"GroupDelay(S({i},{j}))" for i in mm for j in mm])
            else:
                letter = cat[0]
                self.qty.addItems([f"{letter}({i},{j})" for i in mm for j in mm])

    def _mixed_ports(self, n: int) -> list:
        """With differential pairs, the port indices of the mixed-mode quantities: the Diff/Comm modes and the
        terminals that are in no pair; without pairs an empty list."""
        from ..solver.expressions import diff_pairs
        pairs = diff_pairs(self.doc.design)
        if not pairs:
            return []
        used = {int(q["pos"]) for q in pairs} | {int(q["neg"]) for q in pairs}
        return ([f"Diff{k + 1}" for k in range(len(pairs))] + [f"Comm{k + 1}" for k in range(len(pairs))]
                + [r for i, r in enumerate(self._row_names(n), 1) if i not in used])

    def _row_names(self, n: int) -> list:
        """The rows of S as the quantities name them: terminal or port-mode names (S(P1_M1,P1_M2)), or the
        numbers when no port has several modes or terminals."""
        try:
            from ..solver.results import row_names
            _, _, data = load_solution(self.doc, self.sol.currentText())
            if data is not None:
                names = row_names(data.get("ports") or [], data.get("terminals"))
                if len(names) == n:
                    return names
        except Exception:  # noqa: BLE001
            pass
        return [str(i) for i in range(1, n + 1)]

    def _port_quantity_count(self, key: str) -> int:
        """Number of port impedances (one per port or terminal) or propagation constants (one per mode)."""
        try:
            _, _, data = load_solution(self.doc, self.sol.currentText())
            if data is not None and key in data:
                return int(np.asarray(data[key]).shape[1])
        except Exception:  # noqa: BLE001
            pass
        return self._ports()

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
        if self.cat.currentText() in ("VSWR", "Group Delay") or self.func.currentText() == "<none>":
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
            if self._tuned():
                from .tuning import TUNED, tuning_state
                return [("", None), (tuning_state(self.doc).label(), TUNED)]
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
        if getattr(self, "xvar", None) is not None:
            self._fill_xaxis()
        self.refresh()

    def _save_props(self) -> None:
        self.report.props["cut"] = self.cut.currentText()
        self.report.props["cut_value"] = self.cut_val.text()
        if getattr(self, "ff_plot", None) is not None:
            self.report.props["plot"] = self.ff_plot.currentText()
        self.changed.emit()
        self.refresh()

    def _ff_freq_changed(self, i: int) -> None:
        f = self.ff_freq.itemData(i)
        if f is None or getattr(self, "_filling_freq", False):
            return
        self.report.props["freq"] = f"{f:.12g}"
        self.changed.emit()
        self.refresh()

    def _fill_ff_freq(self, freqs: list[tuple[int, float]], k: int) -> None:
        self._filling_freq = True
        self.ff_freq.clear()
        for kk, f in freqs:
            self.ff_freq.addItem(f"{f / 1e9:.6g} GHz", f)
        self.ff_freq.setCurrentIndex(next((i for i, (kk, _) in enumerate(freqs) if kk == k), 0))
        self.ff_freq.setEnabled(len(freqs) > 1)
        self._filling_freq = False

    def _marker_near(self, ev, px: float = 8.0):
        """Index of the marker line within px pixels of the mouse, or None."""
        if ev.inaxes is None or not self.markers or not self._xy_plot():
            return None
        best, bd = None, px
        for i, mk in enumerate(self.markers):
            if self._polar():               # angle on the polar axes, compared in deg (3 deg ~ a few pixels)
                if ev.xdata is None:
                    return None
                d = abs((self._from_ax(ev.xdata) - mk + 180) % 360 - 180) * px / 3
            else:
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
        self.markers[i] = self._from_ax(ev.xdata)
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
            self._drag_line = ev.inaxes.axvline(self._to_ax(self.markers[near]), color="#c0392b", lw=1.2)
            self.canvas.draw_idle()
            return
        if ev.inaxes is None or not self._xy_plot() or ev.button != 1 or ev.dblclick is False:
            return
        self.markers.append(self._from_ax(ev.xdata))
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
        """Line plot over frequency, passes or angle: markers, axis and trace properties."""
        return self.report.kind in ("Rectangular", "Monostatic RCS", "Antenna Parameters", "Radiation Pattern")

    def _polar(self) -> bool:
        """Radiation Pattern on polar axes: markers are angles in deg, the axes work in rad."""
        return self.report.kind == "Radiation Pattern" and not self._ff_rect()

    def _to_ax(self, v):
        return np.radians(v) if self._polar() else v

    def _from_ax(self, x: float) -> float:
        """Marker position (deg on the cut's angle range) of an axes x value."""
        if not self._polar():
            return float(x)
        d = float(np.degrees(x)) % 360
        if self.report.props.get("cut", "Phi") == "Phi" and d > 180:   # the Phi cut runs over -180..180
            d -= 360
        return d

    def _ff_rect(self) -> bool:
        return self.report.kind == "Radiation Pattern" and self.report.props.get("plot") == "Rectangular"

    def _plot_menu(self, ev) -> None:
        m = QMenu(self)
        if self._xy_plot():
            mk = m.addMenu("Marker")
            mk.addAction("Add Marker at Max", lambda: self.marker_at("max"))
            mk.addAction("Add Marker at Min", lambda: self.marker_at("min"))
            mk.addAction("Next Max", lambda: self.marker_at("next_max"))
            mk.addAction("Next Min", lambda: self.marker_at("next_min"))
            if ev.xdata is not None:
                mk.addAction("Add Marker Here", lambda x=self._from_ax(ev.xdata): self._add_marker(x))
            a = mk.addAction("Delta Markers (relative to m1)")
            a.setCheckable(True)
            a.setChecked(self.report.props.get("delta", "False") == "True")
            a.toggled.connect(lambda on: self._set_prop("delta", str(on)))
            if self.report.kind == "Radiation Pattern":
                mk.addAction("Beamwidth / Side Lobes...", self.bandwidth_marker)
            else:
                mk.addAction("Bandwidth...", self.bandwidth_marker)
            mk.addSeparator()
            mk.addAction("Clear All Markers", self._clear_markers)
        if self._xy_plot() and not self._polar():
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
        if self.report.kind == "Radiation Pattern":
            # beamwidth (opening angle) of the main lobe: the level below the maximum, -3 dB = half power
            lvl, ok = QInputDialog.getDouble(self, "Beamwidth", f"Level below the maximum of {tr} (dB):",
                                             float(self.report.props.get("bw_level", "-3")), -1e3, 0, 2)
        else:
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

    def _export_curves(self) -> tuple[str, list[tuple[str, np.ndarray, np.ndarray]]]:
        """(x label, [(column name, x, y)]): exactly the curves of the report as drawn, in the units of
        its axes (GHz, deg, ...), for Copy Data and Export."""
        p = self.report.props
        kind = self.report.kind
        if kind == "3D Polar Plot":
            raise RuntimeError("A 3D polar plot has no curves to export; export a Radiation Pattern cut "
                               "or the plot as PNG")
        if kind == "Radiation Pattern":
            q = self._ff()
            other = "Theta" if p.get("cut", "Phi") == "Phi" else "Phi"
            curves = []
            qt = self._ff_tuned(q) if self._tuned() else None      # the tuned cut, as drawn
            for tr in self.report.traces:
                if ff_trace(tr)[1] in q:
                    ang, y = self._ff_cut(q, tr)
                    curves.append((with_unit(self.doc, tr), np.degrees(ang), y))
                if qt is not None and ff_trace(tr)[1] in qt:
                    from .tuning import tuning_state
                    ang, y = self._ff_cut(qt, tr)
                    curves.append((with_unit(self.doc, tr) + tuning_state(self.doc).label(), np.degrees(ang), y))
            return (p.get("xlabel") if self._ff_rect() else None) or f"{other} [deg]", curves
        if kind.startswith("Monostatic RCS"):
            f, xl, _, cols, _ = self._rcs_series()
            return ((p.get("xlabel") if kind != "Monostatic RCS Table" else None) or xl,
                    [(tr + (" [dBsm]" if tr.startswith(("dB10", "dBsm")) else " [m²]"), f, y) for tr, y in cols])
        if kind == "Near Field Line":
            x, xl, cols, _ = self._near_series()
            return xl, [(with_unit(self.doc, tr), x, y) for tr, y in cols]
        _, _, data = self._data()
        xlabel = x_axis(data)[1]
        variations = self._variations()
        curves = []
        if kind == "Smith Chart":
            for tr, suffix, root in [(t, s, r) for t in self.report.traces for s, r in variations]:
                m = re.match(r"^\s*(?:\w+\()?S\((\d+),(\d+)\)\)?\s*$", tr)
                if not m:
                    continue
                d = data if root is None else self._sol_data(None, root)
                g = d["S"][:, int(m.group(1)) - 1, int(m.group(2)) - 1]
                xs = x_axis(d)[0]
                name = f"S({m.group(1)},{m.group(2)})" + suffix
                curves += [(f"re({name})", xs, g.real), (f"im({name})", xs, g.imag)]
            return xlabel, curves
        from .tuning import TUNED
        for tr in self.report.traces:
            for suffix, root in variations:
                try:
                    xs, y = self._trace_xy(tr, root)
                except Exception:  # noqa: BLE001
                    if root != TUNED:
                        raise
                    continue
                curves.append((with_unit(self.doc, tr) + suffix, xs, y))
        if kind not in ("Data Table", "Antenna Parameters Table"):
            xlabel = p.get("xlabel") or xlabel
        return xlabel, curves

    def _export_rows(self) -> tuple[list[str], list[list[float]]]:
        """Header and rows of the exported data: one x column when all curves share their x values,
        an x column per curve otherwise (each curve is written with the points it is drawn with)."""
        xlabel, curves = self._export_curves()
        if not curves:
            raise RuntimeError("The report has no traces with data to export")
        x0 = curves[0][1]
        if all(len(c[1]) == len(x0) and np.allclose(c[1], x0, equal_nan=True) for c in curves):
            cols = [x0] + [c[2] for c in curves]
            header = [xlabel] + [c[0] for c in curves]
        else:
            cols = [a for c in curves for a in (c[1], c[2])]
            header = [h for c in curves for h in (f"{xlabel} ({c[0]})", c[0])]
        n = max(len(c) for c in cols)
        rows = [[float(np.real(c[r])) if r < len(c) else np.nan for c in cols] for r in range(n)]
        return header, rows

    def copy_data(self) -> None:
        try:
            header, rows = self._export_rows()
        except Exception as e:  # noqa: BLE001
            self.info.setText(f"<span style='color:#c0392b'>Copy Data: {e}</span>")
            return
        lines = ["\t".join(header)]
        lines += ["\t".join("" if np.isnan(v) else f"{v:.10g}" for v in r) for r in rows]
        QApplication.clipboard().setText("\n".join(lines))
        self.info.setText(f"{len(rows)} rows of {len(header)} columns copied to the clipboard")
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
        if self.report.kind == "Radiation Pattern":
            ang, y = self._ff_cut(self._ff(), tr)
            return np.degrees(ang), y
        if self.report.kind.startswith("Monostatic RCS"):
            f, _, _, cols, _ = self._rcs_series()
            y = dict(cols).get(tr)
            if y is None:
                raise RuntimeError(f"No data for '{tr}'")
            return f, y
        expr, sol = split_trace(tr)
        from .calc_bridge import field_names_in
        if field_names_in(self.doc.design, expr):
            from .tuning import TUNED
            if root == TUNED or self._xvar():
                raise RuntimeError("the tuning model has no field values")
            return self._field_trace(expr, sol, root)
        data = self._sol_data(sol, root)
        xs, _, _ = x_axis(data)
        v = eval_expr(data, expr, self._outvars())
        return xs, (np.abs(v) if np.iscomplexobj(v) else v.astype(float))

    def _field_trace(self, expr: str, sol: str | None, root=None):
        """A trace with values of the Fields Calculator: each named value at every frequency with saved
        fields (S, Y, Z of the trace are taken at those frequencies)."""
        from .calc_bridge import field_names_in, over_frequency, vector_parts
        cache = self.__dict__.setdefault("_cache", {})
        key = sol or self.sol.currentText()
        st, _, data = load_solution(self.doc, key, root)
        port = int(self.report.props.get("port", "1") or 1)
        names = dict(_FUNCS)
        f = None
        expr, parts = vector_parts(self.doc, expr)          # mag(Spp), x(Spp): computed by the calculator
        for n, src in [(n, n) for n in field_names_in(self.doc.design, expr)] + list(parts.items()):
            ck = ("field", src, key, root, port)
            if ck not in cache:
                cache[ck] = over_frequency(self.doc, src, key, port, store=st if root is not None else None)
            f, names[n] = cache[ck]
        names.update({"Freq": f, "pi": np.pi, "j": 1j})
        if data is not None and re.search(r"\b(S|Y|Z|VSWR)\s*\(", expr):
            idx = [int(np.argmin(np.abs(data["freq"] - x))) for x in f]
            sub = {**data, "freq": data["freq"][idx], **{k: v[idx] for k, v in data.items()
                                                       if isinstance(v, np.ndarray) and v.shape[:1] == data["freq"].shape}}
            for k in ("S", "Y", "Z", "VSWR"):
                names[k] = (lambda k=k: lambda *a: eval_expr(sub, f"{k}({','.join(map(str, a))})"))()
        v = np.asarray(_ast_eval(expr, names)) * np.ones(len(f))
        return f / 1e9, (np.abs(v) if np.iscomplexobj(v) else v.astype(float))

    def _sol_data(self, sol: str | None, root=None):
        cache = self.__dict__.setdefault("_cache", {})
        key = sol or self.sol.currentText()
        from .tuning import TUNED, tuning_state
        if root is None and self._xvar():
            if (key, TUNED, "x") not in cache:          # follows the sliders: dropped on a tuning refresh
                cache[key, TUNED, "x"] = self._xvar_data(key)
            return cache[key, TUNED, "x"]
        if root == TUNED:
            if (key, TUNED) not in cache:
                if self.report.kind in ANTENNA_KINDS:
                    cache[key, TUNED] = self._antenna_tuned(key, self._sol_data(sol))
                else:
                    cache[key, TUNED] = with_pairs(tuning_state(self.doc).data(self._sol_data(sol)), self.doc.design)
            return cache[key, TUNED]
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
    def refresh(self, keep_cache: bool = False) -> None:
        """Draw the report again; keep_cache: only the tuned curves changed (the stored data are kept)."""
        from .tuning import TUNED
        cache = getattr(self, "_cache", {}) if keep_cache else {}
        self._cache = {k: v for k, v in cache.items() if not (isinstance(k, tuple) and TUNED in k)}
        self._tuned_shown = self._tuned()
        self._tuned_err = ""
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
        if self._tuned_err:
            self.info.setText(self.info.text() + f"  <span style='color:#b36b00'>No tuned curve: {self._tuned_err}"
                              "</span>")
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
        if self._xvar():
            st, sol, _ = load_solution(self.doc, self.sol.currentText(), None)
            return st, sol, self._sol_data(None)
        st, sol, data = load_solution(self.doc, self.sol.currentText(), self._variations()[0][1])
        if data is None:
            raise RuntimeError(f"No solution data for '{self.sol.currentText()}'")
        return st, sol, data

    def _units_label(self, traces: list[str]) -> str:
        """y axis label: the unit of the traces ('[dB]'), the trace itself when it is the only one."""
        units = [trace_unit(self.doc, t) for t in traces]
        if len(traces) == 1 and units[0]:
            return f"{traces[0]} [{units[0]}]"
        return unit_label(units)

    def _draw_markers(self, ax, curves: list, unit: str, xlabel: str) -> None:
        """markers m1, m2, ... (delta markers relative to m1, bandwidth) on the curves
        (trace, x, y, axes, colour), with the marker table."""
        p = self.report.props
        delta = p.get("delta", "False") == "True"
        A = self._to_ax
        rows = []
        for n, mk in enumerate(self.markers, start=1):
            row = [f"m{n}", f"{mk:.6g}{unit}"]
            for tr, xs, y, target, col in curves:
                if len(xs) == 0:
                    row.append("")
                    continue
                yi = float(np.interp(mk, xs, y))
                target.plot([A(mk)], [yi], "v", color=col, ms=7)
                row.append(f"{yi:.5g}")
                if tr == curves[0][0]:
                    target.annotate(f"m{n}", (A(mk), yi), textcoords="offset points", xytext=(4, 6), fontsize=8,
                                    bbox=dict(boxstyle="round", fc="#ffffe0", ec="#999"))
            ax.axvline(A(mk), color="#888", lw=0.6, ls=":")
            rows.append(row)
        if delta and len(rows) > 1:
            base = self.markers[0]
            for n, mk in enumerate(self.markers[1:], start=2):
                row = [f"d(m{n},m1)", f"{mk - base:.6g}{unit}"]
                for tr, xs, y, target, col in curves:
                    row.append(f"{np.interp(mk, xs, y) - np.interp(base, xs, y):.5g}" if len(xs) else "")
                rows.append(row)
        bw = p.get("bandwidth")
        if bw and self.report.kind == "Radiation Pattern":
            for tr, xs, y, target, col in curves:
                if tr == bw:
                    rows += self._beam_rows(tr, xs, y, target, float(p.get("bw_level", "-3")))
        elif bw:
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

    def _beam_rows(self, tr: str, xs, y, ax, rel: float) -> list[list[str]]:
        """Beamwidth (opening angle) and side lobe level of one pattern trace, drawn and as table rows."""
        b = beamwidth(xs, y, rel)
        if b is None:
            return []
        A = self._to_ax
        lvl = b["max"] + rel
        if self._polar():
            ax.plot(np.linspace(0, 2 * np.pi, 361), np.full(361, lvl), color="#c0392b", lw=0.8, ls="--")
        else:
            ax.axhline(lvl, color="#c0392b", lw=0.8, ls="--")
        for a in (b["lo"], b["hi"]):
            ax.axvline(A(a), color="#c0392b", lw=0.9)
        rows = [[f"BW @ {rel:g} dB", f"{b['width']:.4g} deg" + ("  (lobe reaches the end of the cut)"
                                                               if b["open"] else ""),
                 f"{b['lo']:.4g} .. {b['hi']:.4g} deg", f"max {b['max']:.4g} at {b['max_at']:.4g} deg"]]
        if "sll" in b:
            rows.append(["Side lobe", f"{b['sll']:.4g} dB", f"at {b['sll_at']:.4g} deg", tr])
        return rows

    def _draw_rect(self) -> None:
        st, sol, data = self._data()
        p = self.report.props
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax2 = None
        xs0, xlabel, scale = x_axis(data)
        passes = data.get("xaxis") == "Pass"
        var = data.get("xaxis") == "Variable"
        unit = "" if passes else " GHz"
        if var:
            unit = f" {data['xunit']}" if data["xunit"] else ""
        LS = {"Solid": "-", "Dashed": "--", "Dotted": ":", "Dash-Dot": "-."}
        SYM = {"None": None, "Circle": "o", "Square": "s", "Triangle": "^", "Cross": "x"}
        curves = []
        variations = self._variations()
        from .tuning import TUNED
        tuned = any(r == TUNED for _, r in variations)
        colors: dict[str, str] = {}
        for tr, suffix, root in [(t, s, r) for t in self.report.traces for s, r in variations]:
            if root == TUNED:
                try:
                    xs, y = self._trace_xy(tr, root)
                except Exception as e:  # noqa: BLE001
                    self._tuned_err = str(e)    # a quantity the tuning model does not have (fields)
                    continue
            else:
                xs, y = self._trace_xy(tr, root)
            stl = self._style(tr)
            if tuned:                           # the solved curve dashed, the tuned one solid in its colour
                stl = dict(stl, style="Dashed" if root is None else "Solid")
                if root == TUNED and tr in colors:
                    stl["color"] = colors[tr]
            elif len(variations) > 1:
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
            if tuned and root is None:
                kw["alpha"] = 0.55
            line, = target.plot(xs, y, marker=mk, **kw)
            colors.setdefault(tr, line.get_color())
            curves.append((tr + suffix, xs, y, target, line.get_color()))
        self._draw_markers(ax, curves, unit, xlabel)
        if passes:
            from matplotlib.ticker import MaxNLocator
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        if self.report.props.get("ylog") != "True":
            for a in (ax, ax2):
                if a is not None:
                    flat_ylim(a, [c[2] for c in curves if c[3] is a])
        self._apply_axes(ax, ax2)
        ax.set_xlabel(p.get("xlabel") or xlabel)
        y1 = [t for t in self.report.traces if not self._style(t).get("y2")]
        y2 = [t for t in self.report.traces if self._style(t).get("y2")]
        ax.set_ylabel(p.get("ylabel") or self._units_label(y1))
        if ax2 is not None:
            ax2.set_ylabel(p.get("y2label") or self._units_label(y2))
        ax.grid(True, alpha=0.4)
        if p.get("legend", "True") == "True":
            hs, ls = ax.get_legend_handles_labels()
            if ax2 is not None:
                h2, l2 = ax2.get_legend_handles_labels()
                hs, ls = hs + h2, ls + l2
            ax.legend(hs, ls, loc="best", fontsize=8)
        title = f"{self.report.name}   ({self.sol.currentText()})"
        if var:      # the solved value (dashed) and the slider value (solid) of the variable
            for a, b in data["warn"]:     # the outer part of the range: lower accuracy (as on the slider)
                ax.axvspan(min(a, b), max(a, b), color="#b36b00", alpha=0.07, lw=0)
            ax.axvline(data["x0"], color="#777", lw=1.0, ls="--", alpha=0.7)
            if data["xnow"] != data["x0"]:
                ax.axvline(data["xnow"], color="#777", lw=1.0, alpha=0.7)
            title += f"   at {data['at'] / 1e9:.6g} GHz" + (f"   ({', '.join(data['others'])})" if data["others"] else "")
        ax.set_title(title, fontsize=10)
        self.canvas.draw_idle()
        solved = data.get("solved")
        what = ("tuning model; dashed line: solved value, solid line: slider value, shaded: lower accuracy. "
                if var else
                f"{len(solved) if solved is not None else 0} solved. ")
        self.info.setText(f"{len(data['freq'])} points, {what}"
                          "Double-click: marker. Right-click: markers, axes, traces, copy, templates.")

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

    def _near_series(self):
        """(distance, x label, [(trace, values)], near field) of the Near Field Line traces."""
        ev = self.doc.evaluator()
        p = self.report.props
        p1 = ev.point(p.get("start", "0,0,0"))
        p2 = ev.point(p.get("end", "10,0,0"))
        n = int(float(p.get("points", "101") or 101))
        nf = near_field_line(self.doc, self.sol.currentText(), p1, p2, n)
        from ..model.expr import LENGTH_UNITS
        s = LENGTH_UNITS.get(self.doc.design.units, 1e-3)
        cols = []
        for tr in self.report.traces:
            m = re.match(r"(\w+)\((\w+)\)", tr)
            if not m or m.group(2) not in nf:
                continue
            func, name = m.groups()
            v = nf[name]
            cols.append((tr, apply_func("dB" if func == "dB" else func, v) if func != "mag" else np.abs(v)))
        return nf["distance"] / s, f"Distance along line [{self.doc.design.units}]", cols, nf

    def _draw_near(self) -> None:
        p = self.report.props
        x, xl, cols, nf = self._near_series()
        n = len(x)
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        for tr, y in cols:
            ax.plot(x, y, label=tr)
        ax.set_xlabel(xl)
        ax.set_ylabel(self.report.props.get("ylabel") or self._units_label(self.report.traces))
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
        from .tuning import TUNED
        tuned = any(r == TUNED for _, r in variations)
        colors: dict[str, str] = {}
        for tr, suffix, root in [(t, s, r) for t in self.report.traces for s, r in variations]:
            m = re.match(r"^\s*(?:\w+\()?S\((\d+),(\d+)\)\)?\s*$", tr)
            if not m:
                continue
            i, j = int(m.group(1)) - 1, int(m.group(2)) - 1
            g = (data if root is None else self._sol_data(None, root))["S"][:, i, j]
            if tuned:
                kw = dict(linestyle="--", alpha=0.55) if root is None else dict(color=colors.get(tr))
            else:
                kw = {}
            line, = ax.plot(g.real, g.imag, label=f"S({i + 1},{j + 1})" + suffix, **kw)
            colors.setdefault(tr, line.get_color())
            ax.plot(g.real[:1], g.imag[:1], "o", ms=3, color="k")
        ax.legend(loc="upper right", fontsize=8)
        ax.set_title(f"{self.report.name}   ({self.sol.currentText()})", fontsize=10)
        self.canvas.draw_idle()

    def _family_columns(self) -> list[tuple[str, np.ndarray]]:
        """(name, values) of every trace, for each variation of the families (interpolated onto the first)."""
        variations = self._variations()
        xs0 = self._trace_xy(self.report.traces[0], variations[0][1])[0] if self.report.traces else None
        cols = []
        from .tuning import TUNED
        for tr in self.report.traces:
            for suffix, root in variations:
                try:
                    xs, y = self._trace_xy(tr, root)
                except Exception as e:  # noqa: BLE001
                    if root != TUNED:
                        raise
                    self._tuned_err = str(e)
                    continue
                if xs0 is not None and (len(xs) != len(xs0) or not np.allclose(xs, xs0)):
                    y = np.interp(xs0, xs, y, left=np.nan, right=np.nan)
                cols.append((tr + suffix, y))
        return cols

    def _draw_table(self) -> None:
        st, sol, data = self._data()
        cols = self._family_columns()
        f, xlabel, _ = x_axis(data)
        if self.report.traces and len(cols) and len(cols[0][1]) != len(f):
            f = self._trace_xy(self.report.traces[0], self._variations()[0][1])[0]   # field values: their freqs
        self.table.setColumnCount(1 + len(cols))
        self.table.setHorizontalHeaderLabels([xlabel] + [with_unit(self.doc, c[0]) for c in cols])
        self.table.setRowCount(len(f))
        for r in range(len(f)):
            self.table.setItem(r, 0, QTableWidgetItem(f"{f[r]:.6g}"))
            for c, (_, y) in enumerate(cols):
                self.table.setItem(r, c + 1, QTableWidgetItem(f"{y[r]:.6g}"))

    def _ff(self):
        """Far field quantities of the report solution at the chosen frequency (cached until refresh)."""
        cache = self.__dict__.setdefault("_cache", {})
        if "ff" in cache:
            return cache["ff"]
        setup, _, sol = [x.strip() for x in self.sol.currentText().partition(":")]
        label = sol or "LastAdaptive"
        st = ResultStore(result_dir(self.doc, setup))
        sph = self.report.props.get("sphere") or (self.doc.design.spheres[0].name if self.doc.design.spheres else "")
        from ..solver.farfield_post import farfield
        if not sph:
            raise RuntimeError("Add a far field setup (Radiation > Insert Far Field Setup).")
        ff = farfield(st, self.doc.design, sph, label, self.doc.evaluator())
        if ff is None:
            raise RuntimeError(f"No far field data for '{label}': the solution has no radiation/PML boundary, no "
                               "fields were saved for it, or it was solved with an older version (analyze once more).")
        freqs = ff_frequencies(ff)
        want = float(self.report.props.get("freq", "") or "nan")
        k = min(freqs, key=lambda x: abs(x[1] - want))[0] if freqs and np.isfinite(want) else \
            (freqs[0][0] if freqs else 0)
        self._fill_ff_freq(freqs, k)
        from ..solver.ffquant import excited_quantities
        q = excited_quantities(st, self.doc.design, ff, label, k)
        q["_freq"] = float(ff[f"freq_{k}"][0])
        cache["ff"] = q
        return q

    def _antenna_tuned(self, key: str, base: dict) -> dict:
        """Antenna parameters of the tuned design at the frequencies of the solved ones (`base`), on the grid
        of the far field setup; the far fields of the basis are computed once per frequency (with progress)."""
        from ..solver import farfield_post
        from .tuning import tuning_state
        ts = tuning_state(self.doc)
        m = ts.model
        if m is None or m.ff is None:
            raise RuntimeError("the tuning model has no far field (computed without a radiation boundary or by an "
                               "older version): click Compute Derivatives again")
        setup, _, sol = [x.strip() for x in key.partition(":")]
        st = ResultStore(result_dir(self.doc, setup))
        ff = farfield_post.farfield(st, self.doc.design, self._sphere_name(), sol or "LastAdaptive", self.doc.evaluator())
        if ff is None:
            raise RuntimeError("no far field data")
        th, ph = np.asarray(ff["theta"], float), np.asarray(ff["phi"], float)
        f = base["freq"]
        run = lambda prog: m.antenna_series(f, 0, ts.dp, th, ph, prog)  # noqa: E731
        ks = [int(np.argmin(np.abs(m.freq - x))) for x in f]
        if all(m.has_farfield(k, th, ph) for k in ks):
            f, vals = run(None)
        else:
            f, vals = farfield_post.RUN("Far fields of the tuning model", run)
        return {"freq": f, "solved": f, "antenna": vals}

    def _ff_tuned(self, q: dict) -> dict | None:
        """Far field quantities of the tuned design on the grid of q (the tuning model at the far field
        frequency, port 1 with 1 W); None when the model has no far field there."""
        from .tuning import TUNED, tuning_state
        cache = self.__dict__.setdefault("_cache", {})
        if ("ff", TUNED) in cache:
            return cache["ff", TUNED]
        ts = tuning_state(self.doc)
        m = ts.model
        out = None
        self._ff_tuned_note = ""
        if m is None:
            pass
        elif m.ff is None:
            self._ff_tuned_note = ("The tuning model has no far field (it was computed without a radiation boundary "
                                   "or by an older version): click Compute Derivatives again.")
        else:
            k = int(np.argmin(np.abs(m.freq - q["_freq"])))
            if abs(m.freq[k] - q["_freq"]) <= 1e-6 * q["_freq"]:
                out = m.antenna(k, 0, ts.dp, q["_theta"], q["_phi"])
            else:
                fs = ", ".join(f"{f / 1e9:.6g}" for f in m.freq[:8]) + (" ..." if len(m.freq) > 8 else "")
                self._ff_tuned_note = (f"No tuned pattern at {q['_freq'] / 1e9:.6g} GHz: the tuning model has the "
                                       f"far fields at {fs} GHz (choose one of them as Frequency).")
        cache["ff", TUNED] = out
        return out

    def _ff_cut(self, q: dict, tr: str):
        """(angle in rad, values) of one trace on the cut of the report (Phi or Theta constant)."""
        th, ph = q["_theta"], q["_phi"]
        func, name = ff_trace(tr)
        if name not in q:
            raise RuntimeError(f"No data for '{tr}'")
        v = q[name]
        cut = self.report.props.get("cut", "Phi")
        val = np.radians(float(self.report.props.get("cut_value", "0") or 0))
        if cut == "Phi":
            k = int(np.argmin(np.abs(((ph - val + np.pi) % (2 * np.pi)) - np.pi)))
            # include the opposite half plane (phi + 180) as negative theta
            k2 = int(np.argmin(np.abs(((ph - val - np.pi + np.pi) % (2 * np.pi)) - np.pi)))
            ang = np.r_[-th[::-1], th]
            y = np.r_[v[::-1, k2], v[:, k]]
        else:
            k = int(np.argmin(np.abs(th - val)))
            ang, y = ph, v[k, :]
        return ang, ff_db(func, name, y)

    def _draw_ff(self) -> None:
        q = self._ff()
        th, ph = q["_theta"], q["_phi"]
        ftxt = f"{q['_freq'] / 1e9:.6g} GHz"
        if self.report.kind == "3D Polar Plot":
            import pyvista as pv
            tr = self.report.traces[0] if self.report.traces else "dB(GainTotal)"
            func, name = ff_trace(tr)
            qt = self._ff_tuned(q) if self._tuned() else None
            solved = ff_db(func, name, q[name])
            v = (qt if qt is not None and name in qt else q)[name]   # the tuned pattern, while a slider is off
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
            u = trace_unit(self.doc, tr)
            self.p3.add_mesh(grid, scalars=tr, cmap="jet", show_edges=False,
                             scalar_bar_args={"title": f"{tr} [{u}]" if u else tr})
            self.p3.add_axes()
            if cam is None:                 # first draw; a redraw (Edit Sources, new solution) keeps the view
                self.p3.reset_camera()
                self._p3_drawn = True
            else:
                self.p3.camera_position = cam
            if v is not q[name]:
                from .tuning import tuning_state
                self.info.setText(f"Max {tr} = {vv.max():.3f} at {ftxt},{tuning_state(self.doc).label()}  "
                                  f"(solved: {solved.max():.3f})")
            else:
                self.info.setText(f"Max {tr} = {vv.max():.3f} at {ftxt}" + self._tuned_note_html())
            return
        cut = self.report.props.get("cut", "Phi")
        val = float(self.report.props.get("cut_value", "0") or 0)
        rect = self._ff_rect()
        self.fig.clear()
        if rect:
            ax = self.fig.add_subplot(111)
        else:
            ax = self.fig.add_subplot(111, projection="polar")
            ax.set_theta_zero_location("N")
            ax.set_theta_direction(-1)
            if cut == "Phi":                # the Phi cut runs over theta -180..180, as in the rectangular plot
                ax.xaxis.set_major_formatter(FuncFormatter(_signed_deg))
        curves = []
        qt = self._ff_tuned(q) if self._tuned() else None
        for tr in self.report.traces:
            func, name = ff_trace(tr)
            if name not in q:
                continue
            ang, yy = self._ff_cut(q, tr)
            x = np.degrees(ang)
            kw = dict(linestyle="--", alpha=0.55) if qt is not None else {}
            line, = ax.plot(x if rect else ang, yy, label=tr, **kw)
            curves.append((tr, x, yy, ax, line.get_color()))
            if qt is not None and name in qt:
                from .tuning import tuning_state
                ang, yt = self._ff_cut(qt, tr)
                xt = np.degrees(ang)
                label = tr + tuning_state(self.doc).label()
                ax.plot(xt if rect else ang, yt, "-", color=line.get_color(), label=label)
                curves.append((label, xt, yt, ax, line.get_color()))
        other = "Theta" if cut == "Phi" else "Phi"
        self._draw_markers(ax, curves, " deg", f"{other} [deg]")
        if rect:
            ax.set_xlabel(f"{other} [deg]")
            self._apply_axes(ax, None)
            if self.report.props.get("xlabel"):
                ax.set_xlabel(self.report.props["xlabel"])
            ax.set_ylabel(self.report.props.get("ylabel") or self._units_label(self.report.traces))
            ax.grid(True, alpha=0.4)
        else:
            units = [trace_unit(self.doc, t) for t in self.report.traces]
            if units and units[0] and all(u == units[0] for u in units):
                u = units[0]                    # the radial axis: values with their unit, e.g. -10 dB
                ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g} {u}"))
        ax.legend(loc="best" if rect else "lower right", fontsize=8)
        ax.set_title(f"{self.report.name}: {cut} = {val:g} deg, {ftxt}", fontsize=10)
        self.canvas.draw_idle()
        self.info.setText(f"{ftxt}: radiated power {q['_Prad'][0]:.4g} W ({q.get('_Pinc', [1.0])[0]:.4g} W "
                          "incident, Edit Sources). Double-click: marker. Right-click: markers, beamwidth" +
                          (", axes." if rect else ".") + (self._tuned_note_html() if qt is None else ""))

    def _tuned_note_html(self) -> str:
        """Why a tuned pattern is missing while the sliders are off their values ('' otherwise)."""
        note = getattr(self, "_ff_tuned_note", "") if self._tuned() else ""
        return f"  <span style='color:#b36b00'>{note}</span>" if note else ""

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Data", f"{self.report.name}.csv",
                                              "CSV (*.csv);;Touchstone (*.s*p);;PNG image (*.png)")
        if not path:
            return
        try:
            self.export_to(path)
        except Exception as e:  # noqa: BLE001
            self.info.setText(f"<span style='color:#c0392b'>Export failed: {e}</span>")

    def export_to(self, path: str) -> None:
        if path.endswith(".png") and self.fig is not None:
            self.fig.savefig(path, dpi=150)
            return
        if ".s" in path[-5:]:
            st, sol, data = self._data()
            if "S" not in data:
                self.info.setText("Touchstone needs S-parameters; export the antenna parameters as CSV")
                return
            if data.get("xaxis") in ("Pass", "Variable"):
                raise_msg = "Touchstone needs a frequency solution, not adaptive passes or a variable axis"
                self.info.setText(raise_msg)
                return
            st.write_touchstone(sol, data["freq"], data["S"], data["z0"], path)
            return
        header, rows = self._export_rows()
        np.savetxt(path, np.array(rows, float).reshape(len(rows), len(header)), delimiter=",", fmt="%.10g",
                   header=",".join(f'"{h}"' if "," in h else h for h in header), comments="")
        self.info.setText(f"Exported {len(rows)} rows to {path}")
