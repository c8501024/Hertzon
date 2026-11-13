"""ngspice as the engine for the DC operating point and the small-signal admittance of devices.

ngspice (BSD) runs as a separate program in batch mode; nothing is linked. It reads SPICE libraries of process
design kits, model cards and compiled Verilog-A models (OSDI, built with OpenVAF). Version 43 or later is needed
for models compiled by current OpenVAF releases. The program is found through HERTZON_NGSPICE, then on PATH.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .nonlinear import Device, EngineError, NonlinearCircuit, compiled, node_name, safe

TIMEOUT = 600
DC_FREQ = 1.0            # Hz: the small-signal admittance "at DC" is evaluated here (ngspice AC needs f > 0)

_MODEL_LETTER = {"d": "D", "npn": "Q", "pnp": "Q", "nmos": "M", "pmos": "M", "vdmos": "M", "njf": "J",
                 "pjf": "J", "nmf": "Z", "pmf": "Z"}


def executable() -> str:
    env = os.environ.get("HERTZON_NGSPICE", "").strip()
    if env:
        if not Path(env).is_file():
            raise EngineError(f"HERTZON_NGSPICE points to {env}, which does not exist")
        return env
    for name in ("ngspice_con", "ngspice"):
        p = shutil.which(name)
        if p:
            return p
    for p in (Path("C:/Spice64/bin/ngspice_con.exe"), Path("C:/Program Files/Spice64/bin/ngspice_con.exe"),
              Path("/opt/homebrew/bin/ngspice"), Path("/usr/local/bin/ngspice")):
        if p.is_file():
            return str(p)
    raise EngineError("ngspice was not found. Install ngspice 43 or later (ngspice.sourceforge.io) and put it on "
                      "PATH, or set HERTZON_NGSPICE to the program")


def _va_module(d: Device) -> str:
    if d.model:
        return d.model
    if d.file and d.file.suffix.lower() in (".va", ".vams"):
        mods = re.findall(r"^\s*module\s+(\w+)", d.file.read_text(errors="replace"), re.M)
        if len(mods) == 1:
            return mods[0]
    raise EngineError(f"{d.name}: enter the Verilog-A module name in Model")


def _model_letter(nl: NonlinearCircuit, d: Device) -> str:
    """SPICE element letter of a model card, from its type in the libraries."""
    pat = re.compile(r"^\s*\.model\s+" + re.escape(d.model) + r"\s+\(?\s*(\w+)", re.I | re.M)
    seen = set()

    def scan(f: Path, depth: int) -> str | None:
        if f in seen or depth > 3 or not f.is_file():
            return None
        seen.add(f)
        text = f.read_text(errors="replace")
        m = pat.search(text)
        if m:
            return m.group(1).lower()
        for inc in re.findall(r"^\s*\.(?:include|inc|lib)\s+['\"]?([^'\"\s]+)", text, re.I | re.M):
            t = scan((f.parent / inc).resolve(), depth + 1)
            if t:
                return t
        return None
    for lib in nl.libraries:
        t = scan(lib.file, 0)
        if t:
            if t not in _MODEL_LETTER:
                raise EngineError(f"{d.name}: model {d.model} is of type {t}; use Kind Subcircuit or Verilog-A")
            return _MODEL_LETTER[t]
    raise EngineError(f"{d.name}: no .model {d.model} in the SPICE libraries")


def _header(nl: NonlinearCircuit) -> tuple[list[str], list[Path]]:
    """Library lines, device models and the compiled models to load."""
    lines, osdi = [], []
    for lib in nl.libraries:
        lines.append(f'.lib "{lib.file}" {lib.section}' if lib.section else f'.include "{lib.file}"')
        osdi += lib.osdi
    for d in nl.devices:
        if d.kind == "Verilog-A":
            osdi.append(compiled(d.file))
            lines.append(f".model m_{safe(d.name)} {_va_module(d)} {d.params}")
    return lines, list(dict.fromkeys(osdi))


def _instance(nl: NonlinearCircuit, d: Device, nodes: list[str]) -> str:
    if d.kind == "Verilog-A":
        return f"N{safe(d.name)} {' '.join(nodes)} m_{safe(d.name)}"
    if d.kind == "Subcircuit":
        return f"X{safe(d.name)} {' '.join(nodes)} {d.model} {d.params}"
    return f"{_model_letter(nl, d)}{safe(d.name)} {' '.join(nodes)} {d.model} {d.params}"


def run(deck: str, osdi: list[Path], workdir: Path) -> str:
    """Run a deck in batch mode in workdir and return the output; errors raise EngineError."""
    init = ["set ngbehavior=hsa"] + [f"osdi '{p}'" for p in osdi]
    (workdir / ".spiceinit").write_text("\n".join(init) + "\n")
    (workdir / "deck.cir").write_text(deck)
    try:
        r = subprocess.run([executable(), "-b", "deck.cir"], cwd=workdir, capture_output=True, text=True,
                           timeout=TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise EngineError(f"ngspice did not finish within {TIMEOUT} s") from e
    out = r.stdout + "\n" + r.stderr
    bad = [ln for ln in out.splitlines() if re.search(r"error|couldn't be loaded|only supports OSDI|"
                                                       r"unknown model type|could not find", ln, re.I)
           and "no error" not in ln.lower()]
    if r.returncode != 0 or bad or "simulation interrupted" in out.lower():
        tail = "\n".join(bad[:12]) or out.strip()[-1500:]
        raise EngineError(f"ngspice failed:\n{tail}")
    return out


@dataclass
class OperatingPoint:
    voltages: dict[int, float]                 # node -> volts
    source_current: dict[str, float]           # DC voltage source -> current out of its top pin (A)
    port_current: dict[str, float]             # port -> DC current into the port termination (A)

    def summary(self, nl: NonlinearCircuit) -> dict:
        pins = {d.name: {p: round(self.voltages.get(n, 0.0), 9) for p, n in zip(d.pins, d.nodes)}
                for d in nl.devices}
        return {"device_pin_voltages": pins,
                "supply_currents": {k: v for k, v in self.source_current.items()},
                "port_dc_currents": self.port_current}


def _num(s: str) -> float:
    return float(s.replace("D", "e"))


def operating_point(nl: NonlinearCircuit) -> OperatingPoint:
    """DC operating point: the linear network at 0 Hz as voltage-controlled current sources, the ports as their
    reference resistance to ground, the DC sources and the devices."""
    head, osdi = _header(nl)
    from .nonlinear import DC_SHORT
    Y0 = nl.reduced(0.0, DC_SHORT).real
    names = [node_name(t) for t in nl.terminals]
    body = ["* Hertzon circuit: operating point"] + head
    for i, ni in enumerate(names):
        for j, nj in enumerate(names):
            if Y0[i, j] != 0.0:
                body.append(f"Glin_{i}_{j} {ni} 0 {nj} 0 {Y0[i, j]:.17g}")
    for c, node, z in zip(nl.ports, nl.port_nodes, nl.z_ref):
        body.append(f"Rport_{safe(c.name)} {node_name(node)} 0 {z:.17g}")
    for s in nl.sources:
        a, b = (node_name(x) for x in s.nodes)
        if s.kind == "V":
            body.append(f"Vsrc_{safe(s.name)} {a} {b} dc {s.value:.17g}")
        else:
            body.append(f"Isrc_{safe(s.name)} {b} {a} dc {s.value:.17g}")
    for d in nl.devices:
        body.append(_instance(nl, d, [node_name(x) for x in d.nodes]))
    body += [".control", "set numdgt=15", "op", "print all", ".endc", ".end"]
    with tempfile.TemporaryDirectory(prefix="hertzon-ngspice-") as tmp:
        out = run("\n".join(body) + "\n", osdi, Path(tmp))
    vals = {}
    for m in re.finditer(r"^\s*([^\s=]+)\s*=\s*([-+0-9.eEdD]+)\s*$", out, re.M):
        vals[m.group(1).lower()] = _num(m.group(2))
    volts = {}
    for t in nl.terminals:
        key = node_name(t).lower()
        if key not in vals:
            raise EngineError(f"ngspice gave no operating point for node {key}")
        volts[t] = vals[key]
    cur = {s.name: -vals.get(f"vsrc_{safe(s.name)}#branch".lower(), 0.0) for s in nl.sources if s.kind == "V"}
    pc = {c.name: volts[n] / z for c, n, z in zip(nl.ports, nl.port_nodes, nl.z_ref)}
    return OperatingPoint(volts, cur, pc)


def _is_linear(freqs: np.ndarray) -> bool:
    if len(freqs) < 3:
        return True
    d = np.diff(freqs)
    return bool(np.all(d > 0) and np.allclose(d, d[0], rtol=1e-9, atol=0))


def device_admittance(nl: NonlinearCircuit, d: Device, op: OperatingPoint, freqs: np.ndarray
                      ) -> tuple[list[int], np.ndarray]:
    """Small-signal admittance of a device about the operating point: every pin not on ground held at its DC
    voltage by a source, one source driven at a time. Returns (nodes of those pins, Y (F, p, p))."""
    head, osdi = _header(nl)
    live = [(k, n) for k, n in enumerate(d.nodes) if n != 0]
    pins = []
    body = [f"* Hertzon circuit: small-signal admittance of {d.name}"] + head
    for k, n in enumerate(d.nodes):
        if n == 0:
            pins.append("0")
        else:
            pins.append(f"p{k}")
            body.append(f"Vp{k} p{k} 0 dc {op.voltages[n]:.17g} ac 0")
    body.append(_instance(nl, d, pins))
    fr = np.where(np.asarray(freqs, float) > 0, freqs, DC_FREQ)
    runs: list[tuple[str, list[float]]] = []
    if _is_linear(fr) and len(fr) > 1 and fr[0] < fr[-1]:
        runs.append((f"ac lin {len(fr)} {fr[0]:.17g} {fr[-1]:.17g}", list(fr)))
    else:
        runs += [(f"ac lin 1 {f:.17g} {f:.17g}", [f]) for f in fr]
    ctl = [".control", "set wr_singlescale", "set wr_vecnames", "option numdgt=15", "set wr_precision=15"]
    files = []
    for a, (k, _) in enumerate(live):
        ctl += [f"alter @Vp{kk}[acmag] = {1 if kk == k else 0}" for kk, _ in live]
        for r, (cmd, _) in enumerate(runs):
            fn = f"y_{a}_{r}.txt"
            files.append((a, r, fn))
            ctl += [cmd, f"wrdata {fn} " + " ".join(f"i(Vp{kk})" for kk, _ in live)]
    body += ctl + [".endc", ".end"]
    p = len(live)
    Y = np.zeros((len(fr), p, p), complex)
    with tempfile.TemporaryDirectory(prefix="hertzon-ngspice-") as tmp:
        run("\n".join(body) + "\n", osdi, Path(tmp))
        start = np.cumsum([0] + [len(fs) for _, fs in runs])
        for a, r, fn in files:
            data = np.loadtxt(Path(tmp) / fn, skiprows=1, ndmin=2)
            f_got = data[:, 0]
            want = runs[r][1]
            if len(f_got) != len(want) or not np.allclose(f_got, want, rtol=1e-6):
                raise EngineError(f"ngspice returned other frequencies than asked for {d.name}")
            # columns: frequency, then real and imaginary part of each current; the current of a source flows
            # into its positive pin from outside, so the current into the device is its negative
            for b in range(p):
                Y[start[r]:start[r + 1], b, a] = -(data[:, 1 + 2 * b] + 1j * data[:, 2 + 2 * b])
    return [n for _, n in live], Y
