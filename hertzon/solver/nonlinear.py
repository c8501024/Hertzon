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
                     as compiled Verilog-A models or diode model cards; single tone, swept available power at the
                     drive port.
  mixer              circuitRF: two-tone harmonic balance (LO and RF) over an RF sweep with a fixed IF;
                     conversion loss and the isolations.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..model import circuit as CK
from ..model.design import Design, Project
from .circuit import (CircuitError, CircuitResult, LinearNetwork, NPortData, circuit_ports, kron, linear_network,
                      y_to_s)

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


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _path(text: str, base: Path | None) -> Path:
    """A file of a part, relative to the project. An example saved somewhere else still finds the library it
    ships with (relative to the examples folder) when there is no such file next to the project."""
    p = Path(text.strip().strip('"'))
    if not p.is_absolute():
        q = base / p if base is not None else p
        if not q.exists() and (EXAMPLES / p).is_file():
            return EXAMPLES / p
        return q
    return p


def gather(project: Project, design: Design, freqs: np.ndarray, progress=None, span: str = "") -> NonlinearCircuit:
    """Split the schematic into the linear network, devices, sources and libraries. Every analysis here also needs
    the linear network at DC (the bias point); span: see linear_network."""
    ports, port_nodes, z_ref = circuit_ports(project, design)
    net = linear_network(project, design, freqs, progress, dc_point=True, span=span)
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
        if c.type in CK.NPORT_TYPES:
            nd = net.blocks[c.name]
            if isinstance(nd, NPortData) and nd.freq[0] > 0 and nd.dc is None:
                net.warnings.append(f"{c.name}: the data start at {nd.freq[0] / 1e9:.6g} GHz; the bias point uses "
                                    "that point as DC. For DC paths through it add 0 Hz (or a low frequency) to "
                                    "its sweep, or give the block a circuit model")
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
    nl = gather(project, design, freqs, progress, span=f" (harmonics up to {harmonics} x {f0 / 1e9:.6g} GHz)")
    names = [c.name for c in nl.ports]
    if drive not in names:
        raise CircuitError(f"no port named {drive}; the ports are {', '.join(names)}")
    check_hb_devices(nl)
    return circuitrf.harmonic_balance(nl, f0, harmonics, drive, np.atleast_1d(np.asarray(power_dbm, float)))


def check_hb_devices(nl: NonlinearCircuit) -> None:
    """circuitRF's harmonic balance runs compiled Verilog-A models and diode model cards (its built-in junction
    diode with the card's parameters); read the cards now, so a missing one is reported before the run."""
    bad = []
    for d in nl.devices:
        if d.kind == "Model card":
            typ, _ = model_card(nl, d.model)
            if typ != "d":
                bad.append(d.name)
        elif d.kind != "Verilog-A":
            bad.append(d.name)
    if bad:
        raise EngineError(f"{', '.join(bad)}: harmonic balance runs compiled Verilog-A models and diode model "
                          "cards (Kind Verilog-A, or Model card of type D). SPICE subcircuits and other model "
                          "cards run in the bias and S-parameter analysis (ngspice)")


# ---- SPICE model cards -----------------------------------------------------------------------------------
_SPICE_SCALE = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "mil": 25.4e-6, "m": 1e-3, "u": 1e-6, "n": 1e-9,
                "p": 1e-12, "f": 1e-15, "a": 1e-18}


def spice_number(text: str) -> float:
    """A SPICE number: 0.7p, 2.2E-8, 1meg, 15V (letters after the scale factor are a unit and ignored)."""
    m = re.match(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)([a-zA-Z]*)", text)
    if not m:
        raise ValueError(f"not a number: {text}")
    v, suf = float(m.group(1)), m.group(2).lower()
    for k in ("meg", "mil", "t", "g", "k", "m", "u", "n", "p", "f", "a"):
        if suf.startswith(k):
            return v * _SPICE_SCALE[k]
    return v


def _cards(text: str) -> dict[str, tuple[str, dict[str, str]]]:
    """The .model cards of a SPICE text: name (lower case) -> (type, {PARAM: value text})."""
    text = re.sub(r"\n\s*\+", " ", text.replace("\r", ""))           # continuation lines
    out = {}
    for line in text.splitlines():
        line = line.split(";")[0].split("$")[0]
        m = re.match(r"\s*\.model\s+(\S+)\s+([A-Za-z]+)\s*\(?(.*?)\)?\s*$", line, re.I)
        if m:
            params = {k.upper(): v for k, v in re.findall(r"(\w+)\s*=\s*([^\s,()=]+)", m.group(3))}
            out[m.group(1).lower()] = (m.group(2).lower(), params)
    return out


def model_card(nl: NonlinearCircuit, name: str) -> tuple[str, dict[str, str]]:
    """Type and parameters of the .model card `name` in the SPICE libraries of the circuit (and the files they
    include)."""
    seen = set()

    def scan(f: Path, depth: int):
        if f in seen or depth > 3 or not f.is_file():
            return None
        seen.add(f)
        text = f.read_text(errors="replace")
        card = _cards(text).get(name.lower())
        if card:
            return card
        for inc in re.findall(r"^\s*\.(?:include|inc|lib)\s+['\"]?([^'\"\s]+)", text, re.I | re.M):
            c = scan((f.parent / inc).resolve(), depth + 1)
            if c:
                return c
        return None
    for lib in nl.libraries:
        c = scan(lib.file, 0)
        if c:
            return c
    raise EngineError(f"no .model {name} in the SPICE libraries")


@dataclass
class MixerResult:
    rf_freq: np.ndarray             # (F,) RF frequency of each point
    lo_freq: np.ndarray             # (F,)
    if_freq: float
    lo_power_dbm: np.ndarray        # (L,) available LO power
    rf_power_dbm: float
    port_names: list[str]
    lo: str
    rf: str
    if_port: str
    products: list[str]             # "(k1,k2)": k1 f_LO + k2 f_RF
    port_power_dbm: np.ndarray      # (L, F, ports, products) power into each port's termination
    spectra: np.ndarray             # (L, F, ports, products) complex port voltages, peak
    converged: np.ndarray           # (L, F)
    warnings: list[str] = field(default_factory=list)

    def power(self, port: str, product: str) -> np.ndarray:
        """(L, F) power of a product at a port, dBm."""
        return self.port_power_dbm[:, :, self.port_names.index(port), self.products.index(product)]

    def conversion_loss_db(self) -> np.ndarray:
        """(L, F) available RF power over the IF power delivered to the IF port (product LO - RF)."""
        return self.rf_power_dbm - self.power(self.if_port, "(1,-1)")

    def isolation_db(self, source: str, port: str) -> np.ndarray:
        """(L, F) available power of the LO (source "LO") or the RF over its power at another port."""
        if source == "LO":
            return self.lo_power_dbm[:, None] - self.power(port, "(1,0)")
        return self.rf_power_dbm - self.power(port, "(0,1)")


def mixer(project: Project, design: Design, lo: str, rf: str, if_port: str, rf_freq: np.ndarray, if_freq: float,
          lo_side: str, lo_power: np.ndarray, rf_power: float, order: int = 13, workers: int | None = None
          ) -> MixerResult:
    """Two-tone harmonic balance of a mixer with circuitRF: at every RF frequency the LO sits IF below (lo_side
    "Low") or above it, the LO port is a source of the LO power (swept), the RF port of the small RF power; all
    other ports terminate in their Z0. One circuitRF run per frequency, run in parallel."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from . import circuitrf
    rf_freq = np.atleast_1d(np.asarray(rf_freq, float))
    lo_freq = rf_freq - if_freq if lo_side == "Low" else rf_freq + if_freq
    if np.any(lo_freq <= 0) or if_freq <= 0:
        raise CircuitError("The LO frequency must be positive: RF above the IF for a low-side LO")
    if order < 3:
        raise CircuitError("The mixing order must be at least 3")
    # every mixing product k1 f_LO + k2 f_RF must lie in the data of the blocks (they are never extrapolated)
    kk = np.array(circuitrf.mix_products(order), float)
    mix = np.abs(kk[:, :1] * lo_freq[None, :] + kk[:, 1:] * rf_freq[None, :]).ravel()
    mix = mix[mix > 1e-3]
    probe = np.unique(np.concatenate([rf_freq, lo_freq, [if_freq, mix.min(), mix.max()]]))
    nl = gather(project, design, probe, span=f" (mixing products up to order {order})")
    names = [c.name for c in nl.ports]
    for nm, what in ((lo, "LO"), (rf, "RF"), (if_port, "IF")):
        if nm not in names:
            raise CircuitError(f"{what} port: no port named {nm}; the ports are {', '.join(names)}")
    if len({lo, rf, if_port}) < 3:
        raise CircuitError("LO, RF and IF must be three different ports")
    check_hb_devices(nl)
    p_lo = np.atleast_1d(np.asarray(lo_power, float))
    workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))

    def one(k):
        return circuitrf.mixer_point(nl, lo, rf, lo_freq[k], rf_freq[k], p_lo, rf_power, order)
    with ThreadPoolExecutor(workers) as ex:
        runs = list(ex.map(one, range(len(rf_freq))))
    labels = runs[0][0]
    pw = np.stack([r[2] for r in runs], axis=1)
    sp = np.stack([r[1] for r in runs], axis=1)
    conv = np.stack([r[3] for r in runs], axis=1)
    warns = list(dict.fromkeys(nl.net.warnings + [m for r in runs for m in r[4]]))
    if np.any(conv < 0.5):
        warns.append(f"harmonic balance did not converge at {int(np.sum(conv < 0.5))} of {conv.size} points")
    return MixerResult(rf_freq, lo_freq, if_freq, p_lo, rf_power, names, lo, rf, if_port, labels, pw, sp, conv,
                       warns)


def run_mixer(project: Project, design: Design, setup, store) -> list[str]:
    """The mixer analysis of a circuit setup, written to its results folder (mixer.csv, .npz and .json)."""
    import time
    ev = design.evaluator(project)
    p = setup.props
    n = int(float(p.get("RFPoints", "21") or 21))
    f1, f2 = ev.freq(p.get("RFStart", "2GHz")), ev.freq(p.get("RFStop", "4GHz"))
    rf = np.linspace(f1, f2, max(1, n)) if n > 1 else np.array([f1])
    try:
        p_rf = float(p.get("RFPower", "-20").replace("dBm", ""))
    except ValueError:
        raise CircuitError("RF power: give one value in dBm") from None
    m = mixer(project, design, p.get("LOPort", "LO").strip(), p.get("RFPort", "RF").strip(),
              p.get("IFPort", "IF").strip(), rf, ev.freq(p.get("IFFreq", "200MHz")), p.get("LOSide", "Low"),
              power_list(p.get("LOPower", "7")), p_rf, int(float(p.get("MixOrder", "13") or 13)))
    write_mixer(m, store)
    store.write_json("status.json", {"state": "done", "finished": time.time(), "circuit": True,
                                     "analysis": "Mixer"})
    return m.warnings


def write_mixer(m: MixerResult, store) -> None:
    folder = store.folder
    np.savez(folder / "mixer.npz", rf_freq=m.rf_freq, lo_freq=m.lo_freq, if_freq=m.if_freq,
             lo_power_dbm=m.lo_power_dbm, rf_power_dbm=m.rf_power_dbm, ports=np.array(m.port_names),
             products=np.array(m.products), port_power_dbm=m.port_power_dbm, spectra=m.spectra,
             converged=m.converged)
    others = [x for x in m.port_names if x not in (m.lo, m.rf, m.if_port)]
    head = ["Plo_dBm", "RF_GHz", "LO_GHz", "IF_GHz", "ConvLoss_dB", f"LO_to_{m.rf}_dB", f"LO_to_{m.if_port}_dB",
            f"RF_to_{m.if_port}_dB", f"RF_to_{m.lo}_dB", f"IF_at_{m.if_port}_dBm", "Converged"]
    cl = m.conversion_loss_db()
    iso = [m.isolation_db("LO", m.rf), m.isolation_db("LO", m.if_port), m.isolation_db("RF", m.if_port),
           m.isolation_db("RF", m.lo)]
    pif = m.power(m.if_port, "(1,-1)")
    rows = []
    for a, plo in enumerate(m.lo_power_dbm):
        for k, f in enumerate(m.rf_freq):
            r = [plo, f / 1e9, m.lo_freq[k] / 1e9, m.if_freq / 1e9, cl[a, k]] + [x[a, k] for x in iso] \
                + [pif[a, k], m.converged[a, k]]
            rows.append(",".join(f"{x:.9g}" for x in r))
    (folder / "mixer.csv").write_text(",".join(head) + "\n" + "\n".join(rows) + "\n")
    store.write_json("mixer.json", {
        "engine": "circuitRF (two-tone harmonic balance); circuit, linear network and N-port reduction: Hertzon",
        "lo_port": m.lo, "rf_port": m.rf, "if_port": m.if_port, "other_ports": others, "if_Hz": m.if_freq,
        "rf_power_dBm": m.rf_power_dbm, "lo_power_dBm": m.lo_power_dbm.tolist(),
        "products": "k1 f_LO + k2 f_RF as (k1,k2); the IF is (1,-1)", "warnings": m.warnings})


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
    # the source and the files it may include from its folder
    newest = max([va.stat().st_mtime] + [f.stat().st_mtime for f in va.parent.glob("*.vams")])
    if out.is_file() and out.stat().st_mtime >= newest:
        return out
    try:
        out.parent.mkdir(exist_ok=True)
        probe = out.parent / ".write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:                    # a read-only folder (e.g. the installed examples): the user's temp folder
        key = hashlib.sha1(str(va.resolve()).encode()).hexdigest()[:12]
        out = Path(tempfile.gettempdir()) / "hertzon-osdi" / key / (va.stem + ".osdi")
        if out.is_file() and out.stat().st_mtime >= newest:
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
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
