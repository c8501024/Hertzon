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


def analyze(project: Project, design: Design, freqs: np.ndarray) -> CircuitResult:
    ev = design.evaluator(project)
    comps, wires = design.components, design.wires
    ids, n = CK.nets(comps, wires)
    ports = sorted([c for c in comps if c.type == "PORT"], key=lambda c: natural_key(c.name))
    if not ports:
        raise CircuitError("The circuit has no port. Add a Port and connect it.")
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
    values = {}
    for c in comps:
        p = c.props
        try:
            if c.type == "R":
                values[c.name] = ev.value(p["R"])
            elif c.type == "L":
                values[c.name] = ev.value(p["L"])
            elif c.type == "C":
                values[c.name] = ev.value(p["C"])
            elif c.type == "TLIN":
                values[c.name] = (ev.value(p["Z0"]), ev.angle(p["E"]), ev.freq(p["F"]))
        except Exception as e:  # noqa: BLE001
            raise CircuitError(f"{c.name}: {e}") from e

    nodes_of = {c.name: [ids[q] for q in CK.pins(c)] for c in comps}
    P = len(ports)
    S_out = np.zeros((len(freqs), P, P), complex)
    pidx = np.array(port_nodes) - 1
    inner = np.array(sorted(set(range(n)) - set(pidx.tolist())), int)
    sq = np.sqrt(np.array(z_ref))
    for k, f in enumerate(freqs):
        w = 2 * np.pi * f
        Y = np.zeros((n + 1, n + 1), complex)

        def stamp2(a, b, y):
            Y[a, a] += y
            Y[b, b] += y
            Y[a, b] -= y
            Y[b, a] -= y

        def stampN(nodes, Ym):
            for i, a in enumerate(nodes):
                for j, b in enumerate(nodes):
                    Y[a, b] += Ym[i, j]

        for c in comps:
            nd = nodes_of[c.name]
            if c.type == "R":
                stamp2(nd[0], nd[1], 1.0 / max(values[c.name], 1e-12))
            elif c.type == "L":
                stamp2(nd[0], nd[1], 1.0 / (1j * w * max(values[c.name], 1e-30)))
            elif c.type == "C":
                stamp2(nd[0], nd[1], 1j * w * values[c.name])
            elif c.type == "TLIN":
                z0, e, f0 = values[c.name]
                th = e * f / f0 - 1e-9j
                stampN(nd, np.array([[-1j / np.tan(th), 1j / np.sin(th)],
                                     [1j / np.sin(th), -1j / np.tan(th)]]) / z0)
            elif c.type in CK.NPORT_TYPES:
                Sb, zb, _ = blocks[c.name].at(f)
                m = Sb.shape[0]
                r = 1.0 / np.sqrt(zb)
                Yb = (r[:, None] * (np.eye(m) - Sb) @ np.linalg.inv(np.eye(m) + Sb)) * r[None, :]
                stampN(nd, Yb)
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
