"""circuitRF as the engine for harmonic balance.

circuitRF (MIT, github.com/potatobeanradio/circuitRF) runs as a separate program through its command line
(circuitrf hb); nothing is linked. Hertzon writes the circuit as a circuitRF netlist (.cnl): its own linear network
as one Touchstone N-port at DC and the harmonics, the devices as compiled Verilog-A models, the DC sources and the
ports. circuitRF solves the harmonic balance and writes the spectra (.npy), which Hertzon reads back.
The program is found through HERTZON_CIRCUITRF, then on PATH (circuitrf), then in the usual install folders.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .nonlinear import (DC_SHORT, EngineError, HBResult, NonlinearCircuit, compiled, node_name, safe, write_touchstone,
                        y_matrix_to_s)

TIMEOUT = 1800
Z_TS = 50.0              # reference impedance of the Touchstone file of the linear network


def executable() -> str:
    env = os.environ.get("HERTZON_CIRCUITRF", "").strip()
    if env:
        if not Path(env).is_file():
            raise EngineError(f"HERTZON_CIRCUITRF points to {env}, which does not exist")
        return env
    for name in ("circuitrf", "circuitRF"):
        p = shutil.which(name)
        if p:
            return p
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    for p in (home / ".local" / "bin" / "circuitrf", local / "Programs" / "circuitRF" / "circuitRF.exe",
              local / "circuitRF" / "circuitRF.exe", Path("/Applications/circuitRF.app/Contents/MacOS/circuitRF")):
        if p.is_file():
            return str(p)
    raise EngineError("circuitRF was not found. Install it (github.com/potatobeanradio/circuitRF, releases) and "
                      "put circuitrf on PATH, or set HERTZON_CIRCUITRF to the program")


def netlist(nl: NonlinearCircuit, f0: float, harmonics: int, drive: str, power_dbm: np.ndarray,
            touchstone: str) -> str:
    """The circuitRF netlist: the Hertzon network as an N-port, probes at the ports and DC sources."""
    nt = len(nl.terminals)
    lines = ["; Hertzon circuit for circuitRF harmonic balance",
             "; linear network (Hertzon): Touchstone N-port at the terminal nodes, against ground",
             f"Pdrive_dbm = {power_dbm[0]:.17g}",
             f"SnP:HZLIN {' '.join(node_name(t) for t in nl.terminals)} NumPorts={nt} File=\"{touchstone}\" "
             'Type="touchstone" InterpMode="linear" InterpDomain="RI" ExtrapMode="clamp"']
    for c, node, z in zip(nl.ports, nl.port_nodes, nl.z_ref):
        n, nm = node_name(node), safe(c.name)
        if c.name == drive:
            lines.append(f"P1Tone:PS_{nm} ps_{nm} 0 Num=1 Pavl=Pdrive_dbm Z={z:.17g} Freq={f0:.17g}")
        else:
            lines.append(f"R:RT_{nm} ps_{nm} 0 R={z:.17g}")
        lines.append(f"IProbe:IP_{nm} ps_{nm} {n}")
    for s in nl.sources:
        a, b = (node_name(x) for x in s.nodes)
        nm = safe(s.name)
        if s.kind == "V":
            lines.append(f"V_1Tone:VS_{nm} vs_{nm} {b} Vdc={s.value:.17g}")
            lines.append(f"IProbe:IS_{nm} vs_{nm} {a}")
        else:
            lines.append(f"I_1Tone:IS_{nm} {b} {a} Idc={s.value:.17g}")
    for d in nl.devices:
        model = f" Model={d.model}" if d.model else ""
        lines.append(f"VerilogA:{safe(d.name)} {' '.join(node_name(x) for x in d.nodes)} "
                     f"File=\"{compiled(d.file).as_posix()}\"{model} {d.params}".rstrip())
    lines.append(f"analysis HB1 type=hb Tone={f0:.17g} MaxHarm={harmonics}")
    if len(power_dbm) > 1:
        lines.append("analysis SW1 type=parametric_sweep Var=Pdrive_dbm Inner=HB1 Values="
                     + ",".join(f"{v:.17g}" for v in power_dbm))
    return "\n".join(lines) + "\n"


def _meta(arr) -> dict:
    return json.loads(bytes(arr["__meta__"][0]).decode())


def _labels(meta: dict, cube: str) -> list[str]:
    for ax in meta[cube]["axes"]:
        if "labels" in ax:
            return [str(x) for x in ax["labels"]]
    return []


def harmonic_balance(nl: NonlinearCircuit, f0: float, harmonics: int, drive: str, power_dbm: np.ndarray
                     ) -> HBResult:
    freqs = f0 * np.arange(harmonics + 1, dtype=float)
    S = np.array([y_matrix_to_s(nl.reduced(f, DC_SHORT if f == 0 else None), Z_TS) for f in freqs])
    with tempfile.TemporaryDirectory(prefix="hertzon-circuitrf-") as tmp:
        tmp = Path(tmp)
        ts = f"hertzon_linear.s{len(nl.terminals)}p"
        write_touchstone(tmp / ts, freqs, S, Z_TS)
        (tmp / "circuit.cnl").write_text(netlist(nl, f0, harmonics, drive, power_dbm, ts))
        try:
            r = subprocess.run([executable(), "hb", "circuit.cnl", "-o", "result.npy"], cwd=tmp,
                               capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise EngineError(f"circuitRF did not finish within {TIMEOUT} s") from e
        out = (r.stdout + "\n" + r.stderr).strip()
        if r.returncode != 0 or not (tmp / "result.npy").is_file():
            err = [ln for ln in out.splitlines() if "rror" in ln] or out.splitlines()[-15:]
            raise EngineError("circuitRF failed:\n" + "\n".join(err[:15]))
        data = np.load(tmp / "result.npy", allow_pickle=False)
    meta = _meta(data)
    nodes = _labels(meta, "V")
    branches = _labels(meta, "I")
    # one record; a swept run carries the sweep as the first axis of every cube
    V, I = np.asarray(data["V"][0]), np.asarray(data["I"][0])
    if V.ndim == 2:
        V, I = V[None], I[None]                       # (points, nodes, K+1), (points, branches, K+1)
    npts = V.shape[0]
    if npts != len(power_dbm):
        raise EngineError(f"circuitRF returned {npts} sweep points, {len(power_dbm)} were asked for")
    P = len(nl.ports)
    spectra = np.zeros((npts, P, harmonics + 1), complex)
    pw = np.zeros((npts, P, harmonics + 1))
    for p, (c, node, z) in enumerate(zip(nl.ports, nl.port_nodes, nl.z_ref)):
        v = V[:, nodes.index(node_name(node)), :]
        spectra[:, p] = v
        i = _branch(I, branches, f"IP_{safe(c.name)}")       # from the source/termination into the circuit
        # power into the port's termination: -Re(V I*)/2 (DC: -V I); at the drive port this is negative
        # (power flows into the circuit) at the fundamental
        pw_w = -np.real(v * np.conj(i)) / 2
        pw_w[:, 0] = -np.real(v[:, 0] * i[:, 0])
        with np.errstate(divide="ignore"):
            pw[:, p] = 10 * np.log10(np.maximum(np.abs(pw_w), 1e-30)) + 30
    supply = {s.name: np.real(_branch(I, branches, f"IS_{safe(s.name)}")[:, 0]) for s in nl.sources
              if s.kind == "V"}
    conv = np.asarray(data["Converged"][0], float).reshape(-1)
    if conv.size == 1 and npts > 1:
        conv = np.repeat(conv, npts)
    warns = list(nl.net.warnings)
    warns += list(dict.fromkeys(ln.strip()[5:].strip() for ln in out.splitlines() if ln.strip().startswith("[HB]")))
    if np.any(conv < 0.5):
        warns.append(f"harmonic balance did not converge at {int(np.sum(conv < 0.5))} of {npts} points")
    return HBResult(np.asarray(power_dbm, float), f0, harmonics, [c.name for c in nl.ports], spectra, pw, supply,
                    conv, warns)


def _branch(I: np.ndarray, labels: list[str], name: str) -> np.ndarray:
    """Current of a probe (points, K+1)."""
    want = name.lower()
    for k, lab in enumerate(labels):
        if lab.lower() == want or lab.lower().startswith(want + ":"):
            return I[:, k, :]
    raise EngineError(f"circuitRF returned no current for {name}")
