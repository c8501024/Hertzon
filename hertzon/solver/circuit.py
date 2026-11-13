"""Linear frequency-domain analysis of a circuit design (nodal admittance matrix).

Every component adds its admittance matrix to the nodes of its pins; N-port blocks (Touchstone files and 3D
designs of the project) are converted from S to Y with their reference impedances, all ports against ground.
The internal nodes are eliminated (Kron reduction) and the admittance matrix at the circuit ports gives the
S-parameters in the ports' reference impedances. The results are written like those of a 3D design
(sparams_<sweep>.npz, Touchstone, ports.json, status.json), so the reports read them the same way."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..model import circuit as CK
from ..model.design import Design, Project, Setup
from .results import ResultStore, results_dir
from . import tlines as TL
from .sweeps import sweep_frequencies

GMIN = 1e-12            # conductance from every node to ground: floating nodes stay solvable


class CircuitError(RuntimeError):
    pass


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


@dataclass
class NPortData:
    freq: np.ndarray
    S: np.ndarray                     # (F, N, N)
    z0: np.ndarray                    # (F, N) real reference impedances
    names: list[str] = field(default_factory=list)
    dc: "CircuitModel | None" = None  # the circuit model that gives the DC point of a bias analysis

    def at(self, f: float) -> tuple[np.ndarray, np.ndarray, bool]:
        """S and z0 at f, linear in real and imaginary part; outside the data the nearest point (flag True)."""
        fr = self.freq
        if f == 0 and fr[0] > 0:
            # DC from data that start above it: the lowest point without its reactive part (an imaginary part at
            # DC is not physical, and a harmonic balance does not converge on it)
            return self.S[0].real.astype(complex), self.z0[0], True
        if f <= fr[0]:
            return self.S[0], self.z0[0], f < fr[0] * (1 - 1e-9)
        if f >= fr[-1]:
            return self.S[-1], self.z0[-1], f > fr[-1] * (1 + 1e-9)
        i = int(np.searchsorted(fr, f)) - 1
        t = (f - fr[i]) / (fr[i + 1] - fr[i])
        return (1 - t) * self.S[i] + t * self.S[i + 1], (1 - t) * self.z0[i] + t * self.z0[i + 1], False


def _z0_matrix(z0, nf: int, n: int) -> np.ndarray:
    z = np.real(np.asarray(z0, complex))
    if z.ndim == 0:
        return np.full((nf, n), float(z) or 50.0)
    if z.ndim == 1:
        if z.size == n:
            return np.tile(z, (nf, 1))
        if z.size == nf:
            return np.repeat(z[:, None], n, axis=1)
        return np.full((nf, n), float(z[0]))
    return z.reshape(nf, n)


def em_solutions(project: Project, design_name: str) -> list[str]:
    """'Setup : Sweep' solutions with S-parameters of a 3D design of the saved project."""
    if not project.path:
        return []
    try:
        des = project.design(design_name)
    except KeyError:
        return []
    out = []
    for s in des.setups:
        st = ResultStore(results_dir(project.path, design_name, s.name))
        for sol in st.solutions():
            out.append(f"{s.name} : {sol}")
    return out


def em_port_names(project: Project, design_name: str, solution: str = "") -> list[str]:
    """Port names of a 3D design: of the solution if solved, else of its enabled ports."""
    if project.path and " : " in solution:
        setup, sol = solution.split(" : ", 1)
        st = ResultStore(results_dir(project.path, design_name, setup))
        d = st.sparams(sol)
        if d is not None:
            from .results import s_labels
            names = [n for n, _ in s_labels(st)]
            if len(names) == d["S"].shape[1]:
                return names
            return [str(i + 1) for i in range(d["S"].shape[1])]
    try:
        return [p.name for p in project.design(design_name).ports()]
    except KeyError:
        return []


def load_em(project: Project, c: CK.Component) -> NPortData:
    name, sol = c.props.get("Design", ""), c.props.get("Solution", "")
    if not project.path:
        raise CircuitError("Save the project first: 3D design blocks read the saved solutions")
    if " : " not in sol:
        sols = em_solutions(project, name)
        if not sols:
            raise CircuitError(f"{c.name}: the 3D design '{name}' has no solved S-parameters yet. "
                               "Analyze it first.")
        sol = sols[0]
    setup, label = sol.split(" : ", 1)
    st = ResultStore(results_dir(project.path, name, setup))
    d = st.sparams(label)
    if d is None or d.get("ports_only"):
        raise CircuitError(f"{c.name}: no S-parameters for '{name}' {sol}. Analyze the 3D design first.")
    S = np.asarray(d["S"], complex)
    order = np.argsort(d["freq"])
    nf, n = S.shape[0], S.shape[1]
    return NPortData(np.asarray(d["freq"], float)[order], S[order], _z0_matrix(d["z0"], nf, n)[order],
                     em_port_names(project, name, sol))


def load_snp(c: CK.Component, base: Path | None) -> NPortData:
    from ..model.touchstone import read
    f = c.props.get("File", "")
    if not f:
        raise CircuitError(f"{c.name}: no Touchstone file chosen")
    p = Path(f)
    if not p.is_absolute() and base is not None:
        p = base / p
    if not p.exists():
        raise CircuitError(f"{c.name}: file not found: {p}")
    fr, S, z0 = read(p)
    order = np.argsort(fr)
    return NPortData(fr[order], S[order], np.full((len(fr), S.shape[1]), z0))


@dataclass
class CircuitResult:
    freq: np.ndarray
    S: np.ndarray
    z0: np.ndarray                    # (N,) port reference impedances
    ports: list[str]
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)          # e.g. the operating point of a circuit with devices


def _params(c: CK.Component, ev, subst: dict) -> object:
    """The evaluated values of a component (SI units), checked once before the frequency loop."""
    p = c.props
    t = c.type
    if t in ("R", "L", "C"):
        return ev.value(p[t])
    if t in ("SRLC", "PRLC"):
        return ev.value(p["R"]), ev.value(p["L"]), ev.value(p["C"])
    if t == "ATTEN":
        loss = ev.value(p.get("Loss", "3dB").replace("dB", "").strip() or "0")
        return loss, ev.value(p.get("Z0", "50ohm"))
    if t in ("TLIN", "TLOC", "TLSC"):
        return ev.value(p["Z0"]), ev.angle(p["E"]), ev.freq(p["F"])
    if t in CK.LINE_TYPES:
        name = p.get("Subst", "")
        if name not in subst:
            raise CircuitError(f"no substrate named '{name}'. Place a Substrate (Transmission Lines) and "
                               "enter its name.")
        q = {k: ev.length(p[k]) for k in ("W", "S", "L", "W1", "W2", "W3", "W4") if k in p}
        if any(v <= 0 for k, v in q.items() if k != "L") or q.get("L", 1) < 0:
            raise CircuitError("W and S must be positive, L not negative")
        return q, subst[name]
    if t == "MVIA":
        name = p.get("Subst", "")
        if name not in subst:
            raise CircuitError(f"no substrate named '{name}'. Place a Substrate (Transmission Lines) and "
                               "enter its name.")
        d, pitch, n = ev.length(p["D"]), ev.length(p["P"]), int(round(ev.value(p["N"])))
        if d <= 0 or n < 1 or (n > 1 and pitch < d):
            raise CircuitError("D must be positive, N at least 1 and the pitch P at least D")
        return d, n, pitch, subst[name]
    if t == "COAX":
        q = {k: ev.length(p[k]) for k in ("Di", "Do", "L")}
        q.update(er=ev.value(p["Er"]), tand=ev.value(p["TanD"]), cond=ev.value(p["Cond"]))
        if not 0 < q["Di"] < q["Do"]:
            raise CircuitError("the inner diameter must be smaller than the outer one")
        return q
    return None


def _substrates(comps, ev) -> dict:
    out = {}
    for c in comps:
        if c.type == "SUBST":
            p = c.props
            try:
                sub = dict(er=ev.value(p["Er"]), h=ev.length(p["H"]), t=ev.length(p["T"]),
                           tand=ev.value(p["TanD"]), cond=ev.value(p["Cond"]))
            except Exception as e:  # noqa: BLE001
                raise CircuitError(f"{c.name}: {e}") from e
            if sub["er"] < 1 or sub["h"] <= 0:
                raise CircuitError(f"{c.name}: Er must be at least 1 and H positive")
            out[c.name] = sub
    return out


def element_y(c: CK.Component, f: float, v, table=None) -> np.ndarray | None:
    """Admittance matrix of a component between its pins at frequency f; None for parts without one. table: the
    full-wave cross-section of a line (xsection.Table), else the closed-form model."""
    w = 2 * np.pi * f
    t = c.type
    if t in ("R", "L", "C", "SRLC", "PRLC", "DCBLOCK", "DCFEED"):
        if t == "R":
            y = 1.0 / max(v, 1e-12)
        elif t == "L":
            y = 1.0 / (1j * w * max(v, 1e-30) + 1e-12)
        elif t == "C":
            y = 1j * w * v
        elif t == "SRLC":
            r, l, cc = v
            z = r + 1j * w * l + (1 / (1j * w * cc) if cc > 0 and w > 0 else (0 if cc <= 0 else 1e12))
            y = 1 / (z + 1e-12)
        elif t == "PRLC":
            r, l, cc = v
            y = (1 / r if r > 0 else 1e12) + (1 / (1j * w * l) if l > 0 and w > 0 else (0 if l <= 0 else 1e12)) \
                + 1j * w * cc
        elif t == "DCBLOCK":
            y = 1e12 if f > 0 else 0.0
        else:
            y = 0.0 if f > 0 else 1e12
        return np.array([[y, -y], [-y, y]], complex)
    if t == "MVIA":
        d, n, pitch, s = v
        lv, rv = TL.via_array(d, s["h"], n, pitch, s["cond"], f)
        return np.array([[1 / (rv + 1j * w * lv + 1e-12)]])
    if t == "ATTEN":
        loss, z0 = v
        k = 10 ** (-loss / 20)
        S = np.array([[0, k], [k, 0]], complex)
        return (np.eye(2) - S) @ np.linalg.inv(np.eye(2) + S) / z0
    if t == "TLIN":
        z0, e, f0 = v
        return TL.line_y(z0, 1j * e * f / f0)
    if t in ("TLOC", "TLSC"):
        z0, e, f0 = v
        return np.array([[TL.stub_y(z0, 1j * e * f / f0, t == "TLOC")]])
    if t in ("MLIN", "MLOC", "MLSC"):
        q, s = v
        if table is not None:
            z0, g = table.at(f, s["cond"])
        else:
            z0, g = TL.microstrip(f, q["W"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
        if t == "MLIN":
            return TL.line_y(z0, g * q["L"])
        if t == "MLSC":                     # shorted by a via of the line width, as in the 3D layout
            zv = 1j * 2 * np.pi * f * TL.via_inductance(q["W"], s["h"])
            return np.array([[TL.shorted_line_y(z0, g * q["L"], zv)]])
        length = q["L"] + TL.open_end_extension(q["W"], s["h"], s["t"], s["er"])
        return np.array([[TL.stub_y(z0, g * length, True)]])
    if t == "CPWLIN":
        q, s = v
        if table is not None:
            z0, g = table.at(f, s["cond"])
        else:
            z0, g = TL.cpw(f, q["W"], q["S"], s["h"], s["t"], s["er"], s["tand"], s["cond"],
                           c.props.get("Backed", "False") == "True")
        return TL.line_y(z0, g * q["L"])
    if t == "MCLIN":
        q, s = v
        even, odd = TL.coupled_microstrip(f, q["W"], q["S"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
        return TL.coupled_y(even, odd, q["L"])
    if t == "MTEE":
        q, s = v
        return TL.tee_y(f, q["W1"], q["W2"], q["W3"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
    if t in ("MCORN", "MMBEND"):
        q, s = v
        return TL.corner_y(f, q["W"], s["h"], s["er"], t == "MMBEND")
    if t == "MSTEP":
        q, s = v
        return TL.step_y(f, q["W1"], q["W2"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
    if t == "MGAP":
        q, s = v
        return TL.gap_y(f, q["W1"], q["W2"], q["S"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
    if t == "MCROSS":
        q, s = v
        return TL.cross_y(f, (q["W1"], q["W2"], q["W3"], q["W4"]), s["h"], s["t"], s["er"], s["tand"], s["cond"])
    if t == "SLIN":
        q, s = v
        z0, g = TL.stripline(f, q["W"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
        return TL.line_y(z0, g * q["L"])
    if t == "COAX":
        z0, g = TL.coax(f, v["Di"], v["Do"], v["er"], v["tand"], v["cond"])
        return TL.line_y(z0, g * v["L"])
    return None


def full_wave(design: Design) -> bool:
    return design.settings.get(CK.LINE_MODEL, "Closed form") == "Full wave"


def cross_sections(project: Project, design: Design) -> dict[str, object]:
    """The line cross-section (xsection.XSec) of every line the full-wave line model applies to, by part name."""
    from .xsection import XSec
    ev = design.evaluator(project)
    comps = [c for c in design.components if c.type in CK.FULL_WAVE_TYPES or c.type == "SUBST"]
    subst = _substrates(comps, ev)
    out = {}
    for c in comps:
        if c.type == "SUBST":
            continue
        try:
            q, sb = _params(c, ev, subst)
        except Exception as e:  # noqa: BLE001
            raise CircuitError(f"{c.name}: {e}") from e
        if c.type == "CPWLIN":
            kind = "GCPW" if c.props.get("Backed", "False") == "True" else "CPW"
        else:
            kind = "MS"
        out[c.name] = XSec(kind, round(q["W"], 12), round(q.get("S", 0.0), 12) if kind != "MS" else 0.0,
                           round(sb["h"], 12), round(sb["t"], 12), round(sb["er"], 9), round(sb["tand"], 9))
    return out


def max_frequency(project: Project, design: Design) -> float:
    """The highest frequency of the enabled sweeps of the design's setups."""
    ev = design.evaluator(project)
    fm = 0.0
    for st in design.setups:
        for sw in st.sweeps:
            if sw.props.get("Enabled", "True") == "True":
                try:
                    fm = max(fm, float(np.max(sweep_frequencies(sw.props, ev))))
                except Exception:  # noqa: BLE001
                    pass
    return fm


def analysis_span(project: Project, design: Design) -> tuple[float, float] | None:
    """Lowest and highest positive frequency the enabled analyses of a circuit design need: the S-parameter sweeps,
    and the fundamental to the last harmonic of a harmonic balance. None without one."""
    ev = design.evaluator(project)
    lo, hi = np.inf, 0.0
    for st in design.setups:
        try:
            if st.props.get("CircuitAnalysis", "S-Parameters") == "Harmonic Balance":
                f0 = ev.freq(st.props.get("Tone", "1GHz"))
                k = int(float(st.props.get("Harmonics", "5") or 5))
                fr = f0 * np.arange(1, max(k, 1) + 1, dtype=float)
            else:
                fr = np.concatenate([np.atleast_1d(sweep_frequencies(sw.props, ev)) for sw in st.sweeps
                                     if sw.props.get("Enabled", "True") == "True"] or [np.zeros(0)])
        except Exception:  # noqa: BLE001 (an incomplete setup: no span from it)
            continue
        fr = fr[fr > 0]
        if fr.size:
            lo, hi = min(lo, float(fr.min())), max(hi, float(fr.max()))
    return (lo, hi) if hi > 0 else None


_RANGE_CACHE: dict = {}


def data_range(project: Project, c: CK.Component) -> tuple[float, float] | None:
    """First and last frequency of a block's data (None if there are none), cached by file and time stamp."""
    try:
        if c.type == "EM":
            name, sol = c.props.get("Design", ""), c.props.get("Solution", "")
            if not project.path or " : " not in sol:
                return None
            setup, label = sol.split(" : ", 1)
            from .results import _safe
            path = results_dir(project.path, name, setup) / f"sparams_{_safe(label)}.npz"
        else:
            path = Path(c.props.get("File", ""))
            if not path.is_absolute() and project.path:
                path = Path(project.path).parent / path
        key = (str(path), path.stat().st_mtime_ns)
    except (OSError, ValueError):
        return None
    if key not in _RANGE_CACHE:
        try:
            if c.type == "EM":
                with np.load(path, allow_pickle=False) as d:
                    fr = np.asarray(d["freq"], float)
            else:
                from ..model.touchstone import read
                fr = np.asarray(read(path)[0], float)
            _RANGE_CACHE[key] = (float(fr.min()), float(fr.max())) if fr.size else None
        except Exception:  # noqa: BLE001
            _RANGE_CACHE[key] = None
    return _RANGE_CACHE[key]


def block_state(project: Project, design: Design, c: CK.Component) -> tuple[str, str]:
    """Which model the next analysis of the circuit design takes for the block c, for the schematic:
    "data" (full-wave or Touchstone data), "circuit" (chosen), "auto" (data chosen, but they do not cover the
    analyses: the circuit model is used) or "error" (no usable model), with a sentence why."""
    cm = circuit_model_name(project, c)
    if uses_circuit_model(c):
        return ("circuit", f"Circuit model: {cm}") if cm else ("error", "Circuit model chosen, but none set")
    kind = "full-wave FEM" if c.type == "EM" else "Touchstone"
    rng = data_range(project, c)
    if rng is None:
        return "error", f"No {kind} data yet" + (f"; the circuit model {cm} is not used automatically" if cm else "")
    span = analysis_span(project, design)
    lo, hi = rng
    if span is None or (span[0] >= lo * (1 - 1e-9) and span[1] <= hi * (1 + 1e-9)):
        return "data", f"{kind[0].upper() + kind[1:]} data, {_ghz(lo)} to {_ghz(hi)} GHz"
    text = (f"The analyses need {_ghz(span[0])} to {_ghz(span[1])} GHz, the {kind} data cover {_ghz(lo)} to "
            f"{_ghz(hi)} GHz")
    if cm:
        return "auto", text + f": the circuit model {cm} is used"
    return "error", text + " and there is no circuit model"


def auto_junctions(design: Design) -> bool:
    return design.settings.get(CK.AUTO, "True") != "False"


@dataclass
class LinearNetwork:
    """The linear parts of a circuit, evaluated once: assemble(f) gives the nodal admittance matrix at f."""
    comps: list
    nodes_of: dict[str, list[int]]
    n: int                                   # nodes without ground
    values: dict
    blocks: dict[str, NPortData]
    tables: dict
    open_ends: dict
    where: dict
    warnings: list[str]
    repeated: dict = field(default_factory=dict)  # parts with two or more pins on the same node

    def block_y(self, name: str, f: float, short: float | None = None) -> np.ndarray:
        """Admittance matrix of an N-port block at f: from its data, or from its circuit model (exact at every
        frequency; the data give way to it only below their first point, for the DC point of a bias analysis)."""
        blk = self.blocks[name]
        if isinstance(blk, NPortData) and blk.dc is not None and f < blk.freq[0] * (1 - 1e-9):
            blk = blk.dc
        if isinstance(blk, CircuitModel):
            return blk.y_at(f, short)
        Sb, zb, _ = blk.at(f)
        m = Sb.shape[0]
        r = 1.0 / np.sqrt(zb)
        return (r[:, None] * (np.eye(m) - Sb) @ np.linalg.inv(np.eye(m) + Sb)) * r[None, :]

    def assemble(self, f: float, short: float | None = None) -> np.ndarray:
        """Nodal admittance matrix (n x n, ground removed) of the linear parts at frequency f, GMIN included.
        short: largest admittance of a one- or two-terminal part (an ideal short at DC is 1e12 S, which is too
        stiff for a bias point solved in double precision; 1e6 S, 1 micro-ohm, is as good a short there)."""
        n, nodes_of = self.n, self.nodes_of
        Y = np.zeros((n + 1, n + 1), complex)
        for c in self.comps:
            nd = nodes_of[c.name]
            if c.type in CK.NPORT_TYPES:
                Ym = self.block_y(c.name, f, short)
            elif c.type == "MLIN" and 0 in nd and nd[0] != nd[1]:
                # a microstrip line to ground ends in a via (as laid out): a shorted stub at its other pin
                q, sb = self.values[c.name]
                z0, g = (self.tables[c.name].at(f, sb["cond"]) if c.name in self.tables else
                         TL.microstrip(f, q["W"], sb["h"], sb["t"], sb["er"], sb["tand"], sb["cond"]))
                y = TL.shorted_line_y(z0, g * q["L"], 1j * 2 * np.pi * f * TL.via_inductance(q["W"], sb["h"]))
                Ym = np.diag([y, 0.0]) if nd[0] != 0 else np.diag([0.0, y])
            else:
                Ym = element_y(c, f, self.values[c.name], self.tables.get(c.name))
            if Ym is None:
                continue
            if short is not None and Ym.shape[0] <= 2 and c.type not in CK.NPORT_TYPES:
                big = np.max(np.abs(Ym))
                if big > short:
                    Ym = Ym * (short / big)
            if self.open_ends.get(c.name):
                q, sb = self.values[c.name]
                om = 2 * np.pi * max(f, 1.0)
                z0, g = TL.microstrip(max(f, 1.0), q["W"], sb["h"], sb["t"], sb["er"], sb["tand"], sb["cond"])
                ce = g.imag / om / z0 * TL.open_end_dl(f, q["W"], sb["h"], sb["t"], sb["er"])
                Ym = Ym.copy()
                for j in self.open_ends[c.name]:
                    Ym[j, j] += 1j * om * ce
            if self.repeated.get(c.name):
                np.add.at(Y, self.where[c.name], Ym)    # two pins on one node: += would add only one of them
            else:
                Y[self.where[c.name]] += Ym
        return Y[1:, 1:] + GMIN * np.eye(n)


@dataclass
class CircuitModel:
    """An N-port block modelled by a circuit design of the project: the sub-circuit's admittance matrix at its power
    ports, in the order of the block's pins, exact at every frequency (DC included)."""
    design: str
    net: LinearNetwork
    port_idx: np.ndarray                     # 0-based nodes of the sub-circuit's ports, in the block's pin order

    def y_at(self, f: float, short: float | None = None) -> np.ndarray:
        return kron(self.net.assemble(f, short), self.port_idx)


def uses_circuit_model(c: CK.Component) -> bool:
    """The block is set to its circuit model (instead of its full-wave or Touchstone data)."""
    return c.props.get(CK.MODEL, CK.MODEL_DATA) == CK.MODEL_CIRCUIT


def circuit_model_name(project: Project, c: CK.Component) -> str:
    """The circuit design that models the block c ("" for none): its CircuitModel property. A 3D design block
    saved before that property existed takes the circuit its 3D design was laid out from (Create 3D Layout)."""
    if CK.CIRCUIT_MODEL in c.props:
        return c.props[CK.CIRCUIT_MODEL].strip()
    if c.type != "EM":
        return ""
    from ..model.circuit_layout import LINK
    try:
        name = project.design(c.props.get("Design", "")).settings.get(LINK, "")
        return name if name and getattr(project.design(name), "kind", "3d") == "circuit" else ""
    except KeyError:
        return ""


def _block_port_names(c: CK.Component) -> list[str]:
    return [n.strip() for n in c.props.get("PortNames", "").split(",") if n.strip()]


def load_circuit_model(project: Project, c: CK.Component, freqs: np.ndarray, stack: tuple = (),
                       progress=None) -> CircuitModel:
    """The circuit model of block c, evaluated for freqs. stack: the circuit designs above it (no cycles)."""
    name = circuit_model_name(project, c)
    if not name:
        raise CircuitError(f"{c.name}: no circuit model chosen. Choose a circuit design of the project as its "
                           "circuit model (Properties), or use its data")
    try:
        des = project.design(name)
    except KeyError:
        raise CircuitError(f"{c.name}: the circuit model '{name}' is not a design of this project") from None
    if getattr(des, "kind", "3d") != "circuit":
        raise CircuitError(f"{c.name}: the circuit model '{name}' is not a circuit design")
    if name in stack:
        raise CircuitError(f"{c.name}: the circuit model '{name}' contains itself "
                           f"({' > '.join(list(stack) + [name])})")
    if any(o.type in CK.NONLINEAR_TYPES for o in des.components):
        raise CircuitError(f"{c.name}: the circuit model '{name}' has nonlinear parts; a block model must be linear")
    try:
        ports, port_nodes, _ = circuit_ports(project, des)
        net = linear_network(project, des, freqs, progress, _stack=tuple(stack) + (name,))
    except CircuitError as e:
        raise CircuitError(f"{c.name}: circuit model '{name}': {e}") from e
    n = CK.nport_count(c)
    if len(ports) != n:
        raise CircuitError(f"{c.name}: the block has {n} ports, its circuit model '{name}' {len(ports)} power ports")
    sub = [p.name for p in ports]
    mine = _block_port_names(c)
    if len(mine) == n and set(mine) == set(sub):           # the same port names: matched by name
        order = [sub.index(m) for m in mine]
    else:                                                  # else by number (a generated layout numbers them 1..N)
        order = list(range(n))
    net.warnings = [f"{c.name} ({name}): {w}" for w in net.warnings]
    return CircuitModel(name, net, np.array([port_nodes[k] - 1 for k in order]))


def _ghz(f: float) -> str:
    return f"{f / 1e9:.6g}"


def _block(project: Project, c: CK.Component, freqs: np.ndarray, base, stack: tuple, warnings: list[str],
           progress, dc_point: bool, span: str):
    """The model of an N-port block for the frequencies freqs: its data, or its circuit model when the block is set
    to it or when the data do not cover freqs (with a warning; without a circuit model that is an error, the data are
    never extrapolated). dc_point: a bias analysis also needs the block at DC, below the data, which its circuit model
    gives when it has one."""
    if uses_circuit_model(c):
        return load_circuit_model(project, c, freqs, stack, progress)
    nd = load_em(project, c) if c.type == "EM" else load_snp(c, base)
    if nd.S.shape[1] != CK.nport_count(c):
        raise CircuitError(f"{c.name}: the data have {nd.S.shape[1]} ports, the symbol "
                           f"{CK.nport_count(c)}. Update the block (right-click > Update ports).")
    lo, hi = nd.freq[0], nd.freq[-1]
    need = np.asarray(freqs, float)
    if dc_point:
        need = need[need > 0]
    if need.size and (need.min() < lo * (1 - 1e-9) or need.max() > hi * (1 + 1e-9)):
        what = (f"full-wave FEM data of '{c.props.get('Design', '')}'" if c.type == "EM"
                else "Touchstone data")
        text = (f"{c.name}: the analysis needs {_ghz(need.min())} to {_ghz(need.max())} GHz{span}, the {what} "
                f"cover {_ghz(lo)} to {_ghz(hi)} GHz")
        if not circuit_model_name(project, c):
            raise CircuitError(text + ". The block has no circuit model to switch to. Extend the frequency range of "
                               "the data, narrow the analysis or choose a circuit model for the block")
        cm = load_circuit_model(project, c, freqs, stack, progress)
        warnings.append(text + f". Switched to its circuit model '{cm.design}'")
        warnings += cm.net.warnings
        return cm
    if dc_point and lo > 0 and circuit_model_name(project, c):
        nd.dc = load_circuit_model(project, c, np.array([0.0]), stack, progress)
        warnings.append(f"{c.name}: the data start at {_ghz(lo)} GHz; the DC point of the bias comes from its "
                        f"circuit model '{nd.dc.design}'")
    return nd


def kron(Y: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Admittance matrix at the nodes keep (0-based) with all other nodes eliminated."""
    inner = np.array(sorted(set(range(Y.shape[0])) - set(np.asarray(keep).tolist())), int)
    Ykk = Y[np.ix_(keep, keep)]
    if inner.size:
        Ykk = Ykk - Y[np.ix_(keep, inner)] @ np.linalg.solve(Y[np.ix_(inner, inner)], Y[np.ix_(inner, keep)])
    return Ykk


def linear_network(project: Project, design: Design, freqs: np.ndarray, progress=None, dc_point: bool = False,
                   span: str = "", _stack: tuple = ()) -> LinearNetwork:
    """Evaluate the linear parts of a circuit for the frequencies freqs (data ranges are checked against them: a
    block whose data do not cover them switches to its circuit model, or the analysis stops). With the full-wave
    line model the cross-sections of the lines are solved first (or read from the cache); progress(done, total,
    text) reports those solves. dc_point: the analysis also needs DC (bias point), see _block; span: added to the
    range of a range message (e.g. the harmonics of a harmonic balance)."""
    ev = design.evaluator(project)
    comps, nodes_of, n, _ = CK.netlist(design.components, design.wires, auto_junctions(design))
    warnings: list[str] = []
    base = Path(project.path).parent if project.path else None
    stack = tuple(_stack) or (design.name,)
    blocks: dict[str, NPortData | CircuitModel] = {}
    for c in comps:
        if c.type in CK.NPORT_TYPES:
            blocks[c.name] = _block(project, c, np.asarray(freqs, float), base, stack, warnings, progress, dc_point,
                                    span)
    subst = _substrates(comps, ev)
    values = {}
    for c in comps:
        try:
            values[c.name] = _params(c, ev, subst)
        except CircuitError as e:
            raise CircuitError(f"{c.name}: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise CircuitError(f"{c.name}: {e}") from e

    tables: dict[str, object] = {}
    if full_wave(design):
        from . import xsection as XS
        xs = cross_sections(project, design)
        if xs:
            try:
                fm = max(float(np.max(freqs)), max_frequency(project, design))     # one grid for all sweeps
                solved = XS.solve(list(xs.values()), fm, project.path, progress=progress)
            except XS.XSecError as e:
                raise CircuitError(f"Full-wave line model: {e}") from e
            tables = {n: solved[x] for n, x in xs.items()}
    where = {c.name: np.ix_(nodes_of[c.name], nodes_of[c.name]) for c in comps}
    # the free ends of coupled lines (a pin on no other part) carry the open end capacitance, as in the layout
    uses: dict[int, int] = {}
    for c in comps:
        for x in nodes_of[c.name]:
            uses[x] = uses.get(x, 0) + 1
    open_ends = {c.name: [j for j, x in enumerate(nodes_of[c.name]) if x != 0 and uses[x] == 1]
                 for c in comps if c.type == "MCLIN"}
    repeated = {c.name: True for c in comps if len(set(nodes_of[c.name])) < len(nodes_of[c.name])}
    return LinearNetwork(comps, nodes_of, n, values, blocks, tables, open_ends, where, warnings, repeated)


def circuit_ports(project: Project, design: Design) -> tuple[list, list[int], list[float]]:
    """The power ports in S-parameter order, their nodes (1-based) and reference impedances."""
    ev = design.evaluator(project)
    comps, nodes_of, _, _ = CK.netlist(design.components, design.wires, auto_junctions(design))
    ports = CK.port_order(comps)
    if not ports:
        raise CircuitError("The circuit has no port. Add a Power Port and connect it.")
    port_nodes, z_ref = [], []
    for c in ports:
        node = nodes_of[c.name][0]
        if node == 0:
            raise CircuitError(f"{c.name} is connected to ground")
        if node in port_nodes:
            raise CircuitError(f"{c.name} is on the same node as another port")
        port_nodes.append(node)
        z = ev.value(c.props.get("Z0", "50ohm"))
        if z <= 0:
            raise CircuitError(f"{c.name}: Z0 must be positive")
        z_ref.append(z)
    return ports, port_nodes, z_ref


def y_to_s(Y: np.ndarray, z_ref) -> np.ndarray:
    sq = np.sqrt(np.asarray(z_ref, float))
    Yn = sq[:, None] * Y * sq[None, :]
    P = Y.shape[0]
    return (np.eye(P) - Yn) @ np.linalg.inv(np.eye(P) + Yn)


def analyze(project: Project, design: Design, freqs: np.ndarray, progress=None) -> CircuitResult:
    """S-parameters at freqs. With the full-wave line model the cross-sections of the lines are solved first
    (or read from the cache); progress(done, total, text) reports those solves. Circuits with nonlinear parts
    go to the external circuit engine (bias point, then the small-signal S-parameters about it)."""
    if any(c.type in CK.NONLINEAR_TYPES for c in design.components):
        from .nonlinear import bias_sparams
        return bias_sparams(project, design, freqs, progress)
    ports, port_nodes, z_ref = circuit_ports(project, design)
    net = linear_network(project, design, freqs, progress)
    P = len(ports)
    S_out = np.zeros((len(freqs), P, P), complex)
    pidx = np.array(port_nodes) - 1
    for k, f in enumerate(freqs):
        S_out[k] = y_to_s(kron(net.assemble(f), pidx), z_ref)
    return CircuitResult(np.asarray(freqs, float), S_out, np.array(z_ref), [c.name for c in ports], net.warnings)


def run_setup(project: Project, design: Design, setup: Setup, progress=None) -> tuple[Path, list[str]]:
    """Analyze every enabled sweep of the setup and write the results. Returns (folder, warnings)."""
    if not project.path:
        raise CircuitError("Save the project first")
    ev = design.evaluator(project)
    out = results_dir(project.path, design.name, setup.name)
    out.mkdir(parents=True, exist_ok=True)
    st = ResultStore(out)
    if setup.props.get("CircuitAnalysis", "S-Parameters") == "Harmonic Balance":
        from .nonlinear import run_harmonic_balance
        return out, run_harmonic_balance(project, design, setup, st)
    if setup.props.get("CircuitAnalysis", "S-Parameters") == "Mixer":
        from .nonlinear import run_mixer
        return out, run_mixer(project, design, setup, st)
    for old in out.glob("sparams_*.npz"):
        old.unlink()
    warns: list[str] = []
    sweeps = [sw for sw in setup.sweeps if sw.props.get("Enabled", "True") == "True"]
    if not sweeps:
        raise CircuitError(f"{setup.name} has no enabled frequency sweep")
    res = None
    for sw in sweeps:
        fr = sweep_frequencies(sw.props, ev)
        res = analyze(project, design, fr, progress)
        ports = [{"name": n, "number": i + 1, "kind": "Circuit Port", "modes": 1}
                 for i, n in enumerate(res.ports)]
        st.save_sparams(sw.name, res.freq, res.S, res.z0, ports)
        warns += res.warnings
        if res.extra.get("operating_point"):
            st.write_json("operating_point.json", {"engine": "ngspice (bias point and small-signal device "
                                                             "admittances); circuit: Hertzon",
                                                   **res.extra["operating_point"]})
    st.write_json("ports.json", ports)
    st.write_json("status.json", {"state": "done", "finished": time.time(), "circuit": True})
    return out, list(dict.fromkeys(warns))
