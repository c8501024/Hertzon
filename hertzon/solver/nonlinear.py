"""Circuits with nonlinear parts (transistors, diodes): Hertzon solves the linear parts itself and hands an external
circuit engine only what it cannot do.

All linear parts of the schematic (lumped elements, lines, junctions, Touchstone files and 3D designs) are reduced
by Kron elimination to one admittance matrix at the "terminal nodes": the nodes of the ports, of the device pins
and of the DC sources. The engine sees that network, the devices, the DC sources and the ports, nothing else, so
its adapter stays small and every engine sees the same linear network.

Analyses:
  bias_sparams       ngspice: the DC operating point (linear network at 0 Hz as conductances), then the
                     small-signal admittance of every device about that point; Hertzon adds them to its own
                     network and gives the S-parameters of the whole circuit.
  harmonic_balance   circuitRF: the linear network as a Touchstone N-port at DC and the harmonics, the devices
                     as compiled Verilog-A models; single tone, swept available power at the drive port.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..model import circuit as CK
from ..model.design import Design, Project
from .circuit import (CircuitError, CircuitResult, LinearNetwork, circuit_ports, kron, linear_network, y_to_s)

AC_SHORT = 1e12          # admittance of an ideal DC voltage source for the RF signal (as Hertzon's DC Feed)
DC_SHORT = 1e6           # largest admittance of a part in the bias point (1 micro-ohm)


class EngineError(CircuitError):
    """An external circuit engine is missing or failed; the message says what to install or fix."""


@dataclass
class Device:
    name: str
    kind: str                       # "Subcircuit", "Model card" or "Verilog-A"
    model: str
    pins: list[str]
    nodes: list[int]                # 0 = ground
    params: str
    file: Path | None = None        # Verilog-A source or compiled model


@dataclass
class Source:
    name: str
    kind: str                       # "V" or "I"
    nodes: tuple[int, int]          # (top pin, bottom pin): V is top minus bottom, I flows out of the top pin
    value: float


@dataclass
class Library:
    file: Path
    section: str
    osdi: list[Path] = field(default_factory=list)


@dataclass
class NonlinearCircuit:
    net: LinearNetwork
    ports: list                     # Power Port components in S-parameter order
    port_nodes: list[int]
    z_ref: list[float]
    devices: list[Device]
    sources: list[Source]
    libraries: list[Library]
    terminals: list[int]            # nodes (1-based) the linear network is reduced to
    base: Path | None

    def reduced(self, f: float, short: float | None = None) -> np.ndarray:
        """Admittance matrix of the linear parts at the terminal nodes at frequency f (short: see
        LinearNetwork.assemble)."""
        return kron(self.net.assemble(f, short), np.array(self.terminals) - 1)


def _path(text: str, base: Path | None) -> Path:
    p = Path(text.strip().strip('"'))
    if not p.is_absolute() and base is not None:
        p = base / p
    return p


def gather(project: Project, design: Design, freqs: np.ndarray, progress=None) -> NonlinearCircuit:
    """Split the schematic into the linear network, devices, sources and libraries."""
    ports, port_nodes, z_ref = circuit_ports(project, design)
    net = linear_network(project, design, freqs, progress)
    ev = design.evaluator(project)
    base = Path(project.path).parent if project.path else None
    devices, sources, libs = [], [], []
    for c in net.comps:
        p = c.props
        nodes = net.nodes_of.get(c.name, [])
        if c.type == "DEVICE":
            kind = p.get("Kind", "Subcircuit").strip() or "Subcircuit"
            pins = CK.device_pins(c)
            if not pins:
                raise CircuitError(f"{c.name}: enter the terminal names in Pins, e.g. c b e")
            model = p.get("Model", "").strip()
            file = _path(p["File"], base) if p.get("File", "").strip() else None
            if kind == "Verilog-A":
                if file is None:
                    raise CircuitError(f"{c.name}: a Verilog-A device needs File (.va source or compiled .osdi)")
                if not file.is_file():
                    raise CircuitError(f"{c.name}: {file} not found")
            elif kind in ("Subcircuit", "Model card"):
                if not model:
                    raise CircuitError(f"{c.name}: enter the {'subcircuit' if kind == 'Subcircuit' else 'model'} "
                                       "name in Model")
            else:
                raise CircuitError(f"{c.name}: Kind must be Subcircuit, Model card or Verilog-A")
            devices.append(Device(c.name, kind, model, pins, list(nodes), p.get("Params", "").strip(), file))
        elif c.type in ("VDC", "IDC"):
            try:
                v = ev.value(p["V" if c.type == "VDC" else "I"])
            except Exception as e:  # noqa: BLE001
                raise CircuitError(f"{c.name}: {e}") from e
            if nodes[0] == nodes[1]:
                raise CircuitError(f"{c.name}: both pins are on the same node")
            sources.append(Source(c.name, "V" if c.type == "VDC" else "I", (nodes[0], nodes[1]), v))
        elif c.type == "SPICELIB":
            if not p.get("File", "").strip():
                raise CircuitError(f"{c.name}: enter the model file in File")
            f = _path(p["File"], base)
            if not f.is_file():
                raise CircuitError(f"{c.name}: {f} not found")
            osdi = [_path(x, base) for x in p.get("OSDI", "").split(";") if x.strip()]
            for o in osdi:
                if not o.is_file():
                    raise CircuitError(f"{c.name}: {o} not found")
            libs.append(Library(f, p.get("Section", "").strip(), osdi))
    if not devices:
        raise CircuitError("The circuit has no nonlinear device")
    uses: dict[int, int] = {}
    for c in net.comps:
        for x in net.nodes_of.get(c.name, []):
            uses[x] = uses.get(x, 0) + 1
    for d in devices:
        loose = [pin for pin, x in zip(d.pins, d.nodes) if x != 0 and uses.get(x, 0) < 2]
        if loose:
            raise CircuitError(f"{d.name}: pin {', '.join(loose)} is not connected")
    if any(d.kind != "Verilog-A" for d in devices) and not libs:
        raise CircuitError("Subcircuits and model cards need a SPICE Library part with the model file")
    term = sorted({x for x in port_nodes} | {x for d in devices for x in d.nodes if x}
                  | {x for s in sources for x in s.nodes if x})
    for c in net.comps:
        if c.type in ("SNP", "EM"):
            nd = net.blocks[c.name]
            if nd.freq[0] > 0:
                net.warnings.append(f"{c.name}: the data start at {nd.freq[0] / 1e9:.6g} GHz; the bias point uses "
                                    "that point as DC. Add 0 Hz (or a low frequency) to its sweep for DC paths "
                                    "through it")
    return NonlinearCircuit(net, ports, port_nodes, z_ref, devices, sources, libs, term, base)


def bias_sparams(project: Project, design: Design, freqs: np.ndarray, progress=None) -> CircuitResult:
    """S-parameters of a circuit with transistors: the DC operating point with ngspice, the small-signal admittance
    of each device about it, combined with the linear network by Hertzon."""
    from . import ngspice
    freqs = np.asarray(freqs, float)
    nl = gather(project, design, freqs, progress)
    op = ngspice.operating_point(nl)
    ydev = {d.name: ngspice.device_admittance(nl, d, op, freqs) for d in nl.devices}
    P = len(nl.ports)
    S = np.zeros((len(freqs), P, P), complex)
    pidx = np.array(nl.port_nodes) - 1
    for k, f in enumerate(freqs):
        Y = nl.net.assemble(f)
        for d in nl.devices:
            nodes, Yd = ydev[d.name]
            for a, na in enumerate(nodes):
                for b, nb in enumerate(nodes):
                    Y[na - 1, nb - 1] += Yd[k, a, b]
        for s in nl.sources:
            if s.kind == "V":
                _stamp(Y, s.nodes, AC_SHORT)
        S[k] = y_to_s(kron(Y, pidx), nl.z_ref)
    res = CircuitResult(freqs, S, np.array(nl.z_ref), [c.name for c in nl.ports], nl.net.warnings)
    res.extra = {"operating_point": op.summary(nl)}
    return res


def _stamp(Y: np.ndarray, nodes: tuple[int, int], y: complex) -> None:
    a, b = nodes
    if a:
        Y[a - 1, a - 1] += y
    if b:
        Y[b - 1, b - 1] += y
    if a and b:
        Y[a - 1, b - 1] -= y
        Y[b - 1, a - 1] -= y


def write_touchstone(path: Path, freqs: np.ndarray, S: np.ndarray, z0: float = 50.0) -> None:
    """S-parameters (F, N, N) as Touchstone v1, real and imaginary parts, full precision."""
    n = S.shape[1]
    lines = [f"! linear network of a Hertzon circuit, {n} ports", f"# Hz S RI R {z0:.17g}"]
    for f, s in zip(freqs, S):
        vals = []
        for i in range(n):
            for j in range(n):
                v = s[j, i] if n == 2 else s[i, j]          # Touchstone 2-ports list S11 S21 S12 S22
                vals += [f"{v.real:.17g}", f"{v.imag:.17g}"]
        if n <= 2:
            lines.append(f"{f:.17g} " + " ".join(vals))
        else:
            row = n * 2
            for i in range(n):
                chunk = vals[i * row:(i + 1) * row]
                for q in range(0, len(chunk), 8):          # at most 4 complex values per line
                    lines.append(((f"{f:.17g} " if i == 0 and q == 0 else "  ") + " ".join(chunk[q:q + 8])))
    path.write_text("\n".join(lines) + "\n")


def y_matrix_to_s(Y: np.ndarray, z0: float = 50.0) -> np.ndarray:
    return y_to_s(Y, [z0] * Y.shape[0])


@dataclass
class HBResult:
    power_dbm: np.ndarray           # available power at the drive port, per sweep point
    f0: float
    harmonics: int
    port_names: list[str]
    spectra: np.ndarray             # (points, ports, harmonics + 1) complex port voltages, peak
    port_power_dbm: np.ndarray      # (points, ports, harmonics + 1) power into the port's termination
    supply_current: dict[str, np.ndarray]   # DC current of each DC voltage source, out of its top pin
    converged: np.ndarray
    warnings: list[str] = field(default_factory=list)

    def gain_db(self, port: int) -> np.ndarray:
        return self.port_power_dbm[:, port, 1] - self.power_dbm


def harmonic_balance(project: Project, design: Design, f0: float, harmonics: int, drive: str,
                     power_dbm: np.ndarray, progress=None) -> HBResult:
    """Single-tone harmonic balance with circuitRF: the drive port is a source of available power power_dbm
    (swept) at f0, the other ports terminate in their Z0."""
    from . import circuitrf
    freqs = f0 * np.arange(harmonics + 1, dtype=float)
    nl = gather(project, design, freqs, progress)
    names = [c.name for c in nl.ports]
    if drive not in names:
        raise CircuitError(f"no port named {drive}; the ports are {', '.join(names)}")
    bad = [d.name for d in nl.devices if d.kind != "Verilog-A"]
    if bad:
        raise EngineError(f"{', '.join(bad)}: harmonic balance runs compiled Verilog-A models only (Kind "
                          "Verilog-A). SPICE subcircuits and model cards run in the bias and S-parameter "
                          "analysis (ngspice)")
    return circuitrf.harmonic_balance(nl, f0, harmonics, drive, np.atleast_1d(np.asarray(power_dbm, float)))


def openvaf() -> str:
    for name in ("openvaf-r", "openvaf"):
        p = shutil.which(name)
        if p:
            return p
    raise EngineError("A Verilog-A source (.va) must be compiled first, and no compiler was found. Install "
                      "OpenVAF-Reloaded (openvaf-r) and put it on PATH, or give the compiled .osdi file")


def compiled(va: Path) -> Path:
    """The compiled model of a Verilog-A source, built next to it (in a cache folder) when older than the source."""
    if va.suffix.lower() == ".osdi":
        return va
    out = va.parent / ".hertzon-osdi" / (va.stem + ".osdi")
    if out.is_file() and out.stat().st_mtime >= va.stat().st_mtime:
        return out
    out.parent.mkdir(exist_ok=True)
    r = subprocess.run([openvaf(), "-I", str(va.parent), str(va), "-o", str(out)], capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0 or not out.is_file():
        raise EngineError(f"Compiling {va.name} failed:\n{(r.stdout + r.stderr).strip()[-2000:]}")
    return out


def power_list(text: str) -> np.ndarray:
    """Drive powers in dBm: one value, a list (-30, -20) or start:step:stop."""
    t = text.replace("dBm", "").strip()
    try:
        if ":" in t:
            a, st, b = (float(x) for x in t.split(":"))
            if st == 0 or (b - a) / st < 0:
                raise ValueError
            return np.round(np.arange(a, b + st / 2, st), 12)
        return np.array([float(x) for x in re.split(r"[,;\s]+", t) if x])
    except ValueError:
        raise CircuitError(f"Available power '{text}': give a value, a list or start:step:stop in dBm") from None


def run_harmonic_balance(project: Project, design: Design, setup, store) -> list[str]:
    """The harmonic balance of a circuit setup, written to its results folder (harmonic_balance.npz, .csv and
    .json). Returns the warnings."""
    import time
    ev = design.evaluator(project)
    p = setup.props
    f0 = ev.freq(p.get("Tone", "1GHz"))
    k = int(float(p.get("Harmonics", "5") or 5))
    if f0 <= 0 or k < 1:
        raise CircuitError("The fundamental must be positive and at least one harmonic kept")
    pw = power_list(p.get("Power", "-30"))
    if not len(pw):
        raise CircuitError("Enter the available power of the drive port")
    drive = p.get("DrivePort", "Port1").strip()
    h = harmonic_balance(project, design, f0, k, drive, pw)
    store_path = store.folder
    np.savez(store_path / "harmonic_balance.npz", power_dbm=h.power_dbm, f0=h.f0, spectra=h.spectra,
             port_power_dbm=h.port_power_dbm, converged=h.converged, ports=np.array(h.port_names),
             **{f"supply_{n}": v for n, v in h.supply_current.items()})
    head = ["Pavl_dBm"] + [f"{n}_H{j}_dBm" for n in h.port_names for j in range(1, k + 1)] \
        + [f"Gain_{n}_dB" for n in h.port_names if n != drive] + [f"Idc_{n}_A" for n in h.supply_current]
    rows = []
    for i, pin in enumerate(h.power_dbm):
        r = [pin] + [h.port_power_dbm[i, q, j] for q in range(len(h.port_names)) for j in range(1, k + 1)] \
            + [h.port_power_dbm[i, q, 1] - pin for q, n in enumerate(h.port_names) if n != drive] \
            + [v[i] for v in h.supply_current.values()]
        rows.append(",".join(f"{x:.9g}" for x in r))
    (store_path / "harmonic_balance.csv").write_text(",".join(head) + "\n" + "\n".join(rows) + "\n")
    store.write_json("harmonic_balance.json", {
        "engine": "circuitRF (harmonic balance); circuit, linear network and N-port reduction: Hertzon",
        "fundamental_Hz": f0, "harmonics": k, "drive_port": p.get("DrivePort", "Port1"),
        "converged": h.converged.tolist(), "warnings": h.warnings})
    store.write_json("status.json", {"state": "done", "finished": time.time(), "circuit": True,
                                     "analysis": "Harmonic Balance"})
    return h.warnings


def node_name(k: int) -> str:
    return "0" if k == 0 else f"n{k}"


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)
