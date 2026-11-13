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
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .nonlinear import (DC_SHORT, EngineError, HBResult, NonlinearCircuit, compiled, model_card, node_name, safe,
                        spice_number, write_touchstone, y_matrix_to_s)

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


# SPICE diode card -> circuitRF's junction diode (Diode); ngspice and circuitRF then compute the same card
DIODE_PARAMS = {"IS": "Is", "JS": "Is", "RS": "Rs", "N": "N", "TT": "Tt", "CJO": "Cj0", "CJ0": "Cj0", "CJ": "Cj0",
                "VJ": "Vj", "PB": "Vj", "M": "M", "MJ": "M", "FC": "Fc", "BV": "Bv", "IBV": "Ibv", "NBV": "Nbv",
                "ISR": "Isr", "NR": "Nr", "EG": "Eg", "XTI": "Xti", "KF": "Kf", "AF": "Af", "TNOM": "Tnom",
                "TREF": "Tnom", "AREA": "Area"}
DIODE_IGNORED = {"LEVEL"}           # card entries that do not change the model


def diode_params(card: dict[str, str], instance: str = "") -> tuple[str, list[str]]:
    """circuitRF Diode parameters of a SPICE D card (and instance parameters such as area=2); returns the text
    and the card parameters circuitRF has no term for. Temperatures as ngspice: 27 C, Tnom 27 C."""
    vals = {"Temp": 27.0, "Tnom": 27.0}
    left = []
    inst = {k.upper(): v for k, v in re.findall(r"(\w+)\s*=\s*(\S+)", instance)}
    for k, v in {**card, **inst}.items():
        if k in DIODE_PARAMS:
            vals[DIODE_PARAMS[k]] = spice_number(v)
        elif k not in DIODE_IGNORED:
            left.append(k)
    return " ".join(f"{k}={v:.17g}" for k, v in vals.items()), left


def device_lines(nl: NonlinearCircuit) -> list[str]:
    """Netlist lines of the devices: compiled Verilog-A models, and diode cards as circuitRF's Diode (anode,
    cathode)."""
    lines = []
    for d in nl.devices:
        nodes = " ".join(node_name(x) for x in d.nodes)
        if d.kind == "Model card":
            typ, card = model_card(nl, d.model)
            if typ != "d" or len(d.nodes) != 2:
                raise EngineError(f"{d.name}: harmonic balance takes model cards of diodes (type D, pins a k)")
            text, left = diode_params(card, d.params)
            if left:
                nl.net.warnings.append(f"{d.name}: circuitRF's diode has no term for {', '.join(left)} of "
                                       f"{d.model} (ngspice uses them in the bias point)")
            lines.append(f"Diode:{safe(d.name)} {nodes} {text}")
        else:
            model = f" Model={d.model}" if d.model else ""
            lines.append(f"VerilogA:{safe(d.name)} {nodes} File=\"{compiled(d.file).as_posix()}\"{model} "
                         f"{d.params}".rstrip())
    return lines


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
    lines += device_lines(nl)
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
        if not (tmp / "result.npy").is_file():         # a point that does not converge still writes its result
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


# ---- two-tone harmonic balance: mixers ---------------------------------------------------------------------
def mix_products(order: int) -> list[tuple[int, int]]:
    """(k1, k2) with |k1| + |k2| <= order: the products k1 f_LO + k2 f_RF the harmonic balance keeps."""
    return [(a, b) for a in range(-order, order + 1) for b in range(-order, order + 1) if abs(a) + abs(b) <= order]


def mixer_netlist(nl: NonlinearCircuit, lo: str, rf: str, f_lo: float, f_rf: float, p_lo: np.ndarray,
                  p_rf: float, order: int, touchstone: str) -> str:
    """circuitRF netlist of one mixer point: LO and RF ports are sources of available power at their tones (the
    LO power swept), every other port terminates in its Z0."""
    nt = len(nl.terminals)
    lines = ["; Hertzon mixer for circuitRF two-tone harmonic balance",
             "; linear network (Hertzon): Touchstone N-port at the terminal nodes, against ground",
             f"Plo_dbm = {p_lo[0]:.17g}",
             f"SnP:HZLIN {' '.join(node_name(t) for t in nl.terminals)} NumPorts={nt} File=\"{touchstone}\" "
             'Type="touchstone" InterpMode="linear" InterpDomain="RI" ExtrapMode="clamp"']
    num = 0
    for c, node, z in zip(nl.ports, nl.port_nodes, nl.z_ref):
        n, nm = node_name(node), safe(c.name)
        if c.name in (lo, rf):
            num += 1
            pw, f = ("Plo_dbm", f_lo) if c.name == lo else (f"{p_rf:.17g}", f_rf)
            lines.append(f"P1Tone:PS_{nm} ps_{nm} 0 Num={num} Pavl={pw} Z={z:.17g} Freq={f:.17g}")
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
    lines += device_lines(nl)
    lines.append(f"analysis HB1 type=hb NumFreqs=2 Tone[1]={f_lo:.17g} Tone[2]={f_rf:.17g} MaxMixOrder={order}")
    if len(p_lo) > 1:
        lines.append("analysis SW1 type=parametric_sweep Var=Plo_dbm Inner=HB1 Values="
                     + ",".join(f"{v:.17g}" for v in p_lo))
    return "\n".join(lines) + "\n"


def mixer_point(nl: NonlinearCircuit, lo: str, rf: str, f_lo: float, f_rf: float, p_lo: np.ndarray, p_rf: float,
                order: int) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """One LO/RF frequency pair, all LO powers: (product labels, port voltages (powers, ports, products),
    power into each port's termination in dBm (same shape), converged (powers,), engine messages)."""
    prods = mix_products(order)
    freqs = np.unique(np.round([abs(a * f_lo + b * f_rf) for a, b in prods], 3))
    S = np.array([y_matrix_to_s(nl.reduced(f, DC_SHORT if f == 0 else None), Z_TS) for f in freqs])
    with tempfile.TemporaryDirectory(prefix="hertzon-circuitrf-") as tmp:
        tmp = Path(tmp)
        ts = f"hertzon_linear.s{len(nl.terminals)}p"
        write_touchstone(tmp / ts, freqs, S, Z_TS)
        (tmp / "mixer.cnl").write_text(mixer_netlist(nl, lo, rf, f_lo, f_rf, p_lo, p_rf, order, ts))
        try:
            r = subprocess.run([executable(), "hb", "mixer.cnl", "-o", "result.npy"], cwd=tmp,
                               capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise EngineError(f"circuitRF did not finish within {TIMEOUT} s") from e
        out = (r.stdout + "\n" + r.stderr).strip()
        if not (tmp / "result.npy").is_file():         # a point that does not converge still writes its result
            err = [ln for ln in out.splitlines() if "rror" in ln] or out.splitlines()[-15:]
            raise EngineError("circuitRF failed:\n" + "\n".join(err[:15]))
        data = np.load(tmp / "result.npy", allow_pickle=False)
    meta = _meta(data)
    nodes = _labels(meta, "V")
    branches = _labels(meta, "I")
    labels = [ax["labels"] for ax in meta["V"]["axes"] if ax["name"] == "mixIndex"][0]
    V, I = np.asarray(data["V"][0]), np.asarray(data["I"][0])
    if V.ndim == 2:
        V, I = V[None], I[None]
    if V.shape[0] != len(p_lo):
        raise EngineError(f"circuitRF returned {V.shape[0]} sweep points, {len(p_lo)} were asked for")
    P = len(nl.ports)
    volt = np.zeros((len(p_lo), P, len(labels)), complex)
    pw = np.zeros((len(p_lo), P, len(labels)))
    dc = labels.index("(0,0)")
    for q, (c, node) in enumerate(zip(nl.ports, nl.port_nodes)):
        v = V[:, nodes.index(node_name(node)), :]
        i = _branch(I, branches, f"IP_{safe(c.name)}")
        w = -np.real(v * np.conj(i)) / 2
        w[:, dc] = -np.real(v[:, dc] * i[:, dc])
        volt[:, q] = v
        with np.errstate(divide="ignore"):
            pw[:, q] = 10 * np.log10(np.maximum(np.abs(w), 1e-30)) + 30
    conv = np.asarray(data["Converged"][0], float).reshape(-1)
    if conv.size == 1 and len(p_lo) > 1:
        conv = np.repeat(conv, len(p_lo))
    msgs = [ln.strip()[5:].strip() for ln in out.splitlines() if ln.strip().startswith("[HB]")]
    return [str(x) for x in labels], volt, pw, conv, msgs
