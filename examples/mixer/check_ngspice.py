"""Independent check of Hertzon's mixer analysis (two-tone harmonic balance by circuitRF) with an ngspice transient
analysis of the same circuit and the same diode card (examples/mixer/hsms282x.lib).

The circuit is a single diode of the HSMS-282x family with a resistive LO/RF combiner and an L-C low-pass to the
IF port. ngspice integrates it in time for 60 ns and the last 20 ns are Fourier-analysed; circuitRF solves the
steady state directly. Run with ngspice and circuitRF installed (HERTZON_CIRCUITRF or circuitrf on PATH):

    python examples/mixer/check_ngspice.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from hertzon.model import circuit as CK  # noqa: E402
from hertzon.model.design import Design, Project  # noqa: E402
from hertzon.solver import nonlinear as NL  # noqa: E402
from hertzon.solver.ngspice import executable  # noqa: E402

F_LO, F_RF, P_RF = 3.0e9, 3.2e9, -30.0
LIB = HERE / "hsms282x.lib"
PARTS = [  # name, type, properties, nets of the pins
    ("LIB1", "SPICELIB", {"File": str(LIB)}, []),
    ("LO", "PORT", {"Num": "1"}, ["lo"]), ("RF", "PORT", {"Num": "2"}, ["rf"]), ("IF", "PORT", {"Num": "3"}, ["if"]),
    ("R1", "R", {"R": "50ohm"}, ["lo", "a"]), ("R2", "R", {"R": "50ohm"}, ["rf", "a"]),
    ("L1", "L", {"L": "10nH"}, ["a", "b"]), ("C1", "C", {"C": "3pF"}, ["b", "0"]), ("R3", "R", {"R": "10ohm"},
                                                                                    ["b", "if"]),
    ("D1", "DEVICE", {"Kind": "Model card", "Model": "HSMS282X", "Pins": "a k"}, ["a", "0"]),
]


def circuit() -> tuple[Project, Design]:
    p = Project()
    d = Design(name="Check", kind="circuit")
    p.designs = [d]
    x = 0
    for name, t, props, nets in PARTS:
        c = CK.Component(name, t, x, 0, 0, {**CK.TYPES[t].props, **props})
        d.components.append(c)
        for k, ((px, py), net) in enumerate(zip(CK.pins(c), nets)):
            d.components.append(CK.Component(f"N_{name}_{k}", "LABEL", px, py, 0, {"Net": net}))
        x += 400
    return p, d


def hertzon(p_lo: list[float]) -> np.ndarray:
    p, d = circuit()
    m = NL.mixer(p, d, "LO", "RF", "IF", np.array([F_RF]), F_RF - F_LO, "Low", np.array(p_lo), P_RF)
    return np.stack([m.power("IF", "(1,-1)")[:, 0], m.power("IF", "(1,0)")[:, 0], m.power("RF", "(1,0)")[:, 0]], 1)


def ngspice(p_lo: float) -> np.ndarray:
    v = {k: np.sqrt(8 * 50 * 10 ** (pw / 10) / 1e3) for k, pw in (("lo", p_lo), ("rf", P_RF))}
    deck = f"""HSMS-282x mixer check
.include "{LIB}"
VLO slo 0 SIN(0 {v['lo']:.12g} {F_LO:.12g})
RSLO slo lo 50
VRF srf 0 SIN(0 {v['rf']:.12g} {F_RF:.12g})
RSRF srf rf 50
RIF if 0 50
R1 lo a 50
R2 rf a 50
L1 a b 10n
C1 b 0 3p
R3 b if 10
D1 a 0 HSMS282X
.options reltol=1e-6 abstol=1e-15 vntol=1e-9 method=gear
.tran 0.25p 60n 40n 0.25p
.control
run
wrdata out.txt v(if) v(rf)
.endc
.end
"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "deck.cir").write_text(deck)
        subprocess.run([executable(), "-b", "deck.cir"], cwd=tmp, capture_output=True, text=True, check=True)
        data = np.loadtxt(Path(tmp) / "out.txt")
    t = np.linspace(40e-9, 60e-9, 80001)[:-1]
    vif, vrf = (np.interp(t, data[:, 0], data[:, j]) for j in (1, 3))

    def dbm(x, f):
        a = 2 * abs(np.mean(x * np.exp(-2j * np.pi * f * t)))
        return 10 * np.log10(a * a / 2 / 50 * 1e3)
    return np.array([dbm(vif, F_RF - F_LO), dbm(vif, F_LO), dbm(vrf, F_LO)])


if __name__ == "__main__":
    powers = [0.0, 4.0, 7.0, 10.0]
    hz = hertzon(powers)
    print("LO dBm | IF at IF port (dBm): Hertzon+circuitRF  ngspice | LO at IF port | LO at RF port")
    for pl, h in zip(powers, hz):
        n = ngspice(pl)
        print(f"{pl:5.1f}  | {h[0]:9.3f} {n[0]:9.3f} ({h[0] - n[0]:+.3f}) | {h[1]:8.3f} {n[1]:8.3f} | "
              f"{h[2]:8.3f} {n[2]:8.3f}")
