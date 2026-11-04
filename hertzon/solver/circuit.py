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

    def at(self, f: float) -> tuple[np.ndarray, np.ndarray, bool]:
        """S and z0 at f, linear in real and imaginary part; outside the data the nearest point (flag True)."""
        fr = self.freq
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
        q = {k: ev.length(p[k]) for k in ("W", "S", "L", "W1", "W2", "W3") if k in p}
        if any(v <= 0 for k, v in q.items() if k != "L") or q.get("L", 1) < 0:
            raise CircuitError("W and S must be positive, L not negative")
        return q, subst[name]
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


def element_y(c: CK.Component, f: float, v) -> np.ndarray | None:
    """Admittance matrix of a component between its pins at frequency f; None for parts without one."""
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
        z0, g = TL.microstrip(f, q["W"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
        if t == "MLIN":
            return TL.line_y(z0, g * q["L"])
        length = q["L"] + (TL.open_end_extension(q["W"], s["h"], s["t"], s["er"]) if t == "MLOC" else 0.0)
        return np.array([[TL.stub_y(z0, g * length, t == "MLOC")]])
    if t == "MCLIN":
        q, s = v
        even, odd = TL.coupled_microstrip(f, q["W"], q["S"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
        return TL.coupled_y(even, odd, q["L"])
    if t == "MTEE":
        q, s = v
        return TL.tee_y(f, q["W1"], q["W2"], q["W3"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
    if t == "SLIN":
        q, s = v
        z0, g = TL.stripline(f, q["W"], s["h"], s["t"], s["er"], s["tand"], s["cond"])
        return TL.line_y(z0, g * q["L"])
    if t == "COAX":
        z0, g = TL.coax(f, v["Di"], v["Do"], v["er"], v["tand"], v["cond"])
        return TL.line_y(z0, g * v["L"])
    return None


def analyze(project: Project, design: Design, freqs: np.ndarray) -> CircuitResult:
    ev = design.evaluator(project)
    comps, wires = design.components, design.wires
    ids, n = CK.nets(comps, wires)
    ports = CK.port_order(comps)
    if not ports:
        raise CircuitError("The circuit has no port. Add a Power Port and connect it.")
    warnings: list[str] = []
    port_nodes, z_ref = [], []
    for c in ports:
        node = ids[CK.pins(c)[0]]
        if node == 0:
            raise CircuitError(f"{c.name} is connected to ground")
        if node in port_nodes:
            raise CircuitError(f"{c.name} is on the same node as another port")
        port_nodes.append(node)
        z = ev.value(c.props.get("Z0", "50ohm"))
        if z <= 0:
            raise CircuitError(f"{c.name}: Z0 must be positive")
        z_ref.append(z)
    base = Path(project.path).parent if project.path else None
    blocks: dict[str, NPortData] = {}
    for c in comps:
        if c.type == "EM":
            blocks[c.name] = load_em(project, c)
        elif c.type == "SNP":
            blocks[c.name] = load_snp(c, base)
        if c.type in CK.NPORT_TYPES:
            nd = blocks[c.name]
            if nd.S.shape[1] != CK.nport_count(c):
                raise CircuitError(f"{c.name}: the data have {nd.S.shape[1]} ports, the symbol "
                                   f"{CK.nport_count(c)}. Update the block (right-click > Update ports).")
            lo, hi = nd.freq[0], nd.freq[-1]
            if freqs.min() < lo * (1 - 1e-9) or freqs.max() > hi * (1 + 1e-9):
                warnings.append(f"{c.name}: data cover {lo / 1e9:.6g} to {hi / 1e9:.6g} GHz; outside, the "
                                "nearest point is used")
    subst = _substrates(comps, ev)
    values = {}
    for c in comps:
        try:
            values[c.name] = _params(c, ev, subst)
        except CircuitError as e:
            raise CircuitError(f"{c.name}: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise CircuitError(f"{c.name}: {e}") from e

    nodes_of = {c.name: [ids[q] for q in CK.pins(c)] for c in comps}
    P = len(ports)
    S_out = np.zeros((len(freqs), P, P), complex)
    pidx = np.array(port_nodes) - 1
    inner = np.array(sorted(set(range(n)) - set(pidx.tolist())), int)
    sq = np.sqrt(np.array(z_ref))
    for k, f in enumerate(freqs):
        Y = np.zeros((n + 1, n + 1), complex)
        for c in comps:
            nd = nodes_of[c.name]
            if c.type in CK.NPORT_TYPES:
                Sb, zb, _ = blocks[c.name].at(f)
                m = Sb.shape[0]
                r = 1.0 / np.sqrt(zb)
                Ym = (r[:, None] * (np.eye(m) - Sb) @ np.linalg.inv(np.eye(m) + Sb)) * r[None, :]
            else:
                Ym = element_y(c, f, values[c.name])
            if Ym is None:
                continue
            Y[np.ix_(nd, nd)] += Ym
        Y = Y[1:, 1:] + GMIN * np.eye(n)
        Ypp = Y[np.ix_(pidx, pidx)]
        if inner.size:
            Ypi, Yip, Yii = Y[np.ix_(pidx, inner)], Y[np.ix_(inner, pidx)], Y[np.ix_(inner, inner)]
            Ypp = Ypp - Ypi @ np.linalg.solve(Yii, Yip)
        Yn = sq[:, None] * Ypp * sq[None, :]
        S_out[k] = (np.eye(P) - Yn) @ np.linalg.inv(np.eye(P) + Yn)
    return CircuitResult(np.asarray(freqs, float), S_out, np.array(z_ref), [c.name for c in ports], warnings)


def run_setup(project: Project, design: Design, setup: Setup) -> tuple[Path, list[str]]:
    """Analyze every enabled sweep of the setup and write the results. Returns (folder, warnings)."""
    if not project.path:
        raise CircuitError("Save the project first")
    ev = design.evaluator(project)
    out = results_dir(project.path, design.name, setup.name)
    out.mkdir(parents=True, exist_ok=True)
    st = ResultStore(out)
    for old in out.glob("sparams_*.npz"):
        old.unlink()
    warns: list[str] = []
    sweeps = [sw for sw in setup.sweeps if sw.props.get("Enabled", "True") == "True"]
    if not sweeps:
        raise CircuitError(f"{setup.name} has no enabled frequency sweep")
    res = None
    for sw in sweeps:
        fr = sweep_frequencies(sw.props, ev)
        res = analyze(project, design, fr)
        ports = [{"name": n, "number": i + 1, "kind": "Circuit Port", "modes": 1}
                 for i, n in enumerate(res.ports)]
        st.save_sparams(sw.name, res.freq, res.S, res.z0, ports)
        warns += res.warnings
    st.write_json("ports.json", ports)
    st.write_json("status.json", {"state": "done", "finished": time.time(), "circuit": True})
    return out, list(dict.fromkeys(warns))
