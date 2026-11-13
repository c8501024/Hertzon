"""Circuits with nonlinear devices: Hertzon's linear network handed to ngspice (bias point and small-signal
admittance) and circuitRF (harmonic balance). The engine runs are skipped when the programs are not installed."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from hertzon.model import circuit as CK
from hertzon.model import touchstone
from hertzon.model.design import Design, Project
from hertzon.solver.circuit import CircuitError, analyze
from hertzon.solver import nonlinear as NL

DATA = Path(__file__).parent / "data"


def _have(*names):
    return all(shutil.which(n) for n in names)


def _ngspice_ok():
    exe = os.environ.get("HERTZON_NGSPICE") or shutil.which("ngspice")
    if not exe:
        return False
    out = subprocess.run([exe, "-v"], capture_output=True, text=True).stdout
    m = re.search(r"ngspice-(\d+)", out)
    return bool(m and int(m.group(1)) >= 43)


NGSPICE = pytest.mark.skipif(not (_ngspice_ok() and _have("openvaf-r")),
                             reason="needs ngspice 43 or later and openvaf-r on PATH")
CIRCUITRF = pytest.mark.skipif(not ((os.environ.get("HERTZON_CIRCUITRF") or _have("circuitrf"))
                                    and _have("openvaf-r")),
                               reason="needs circuitRF (HERTZON_CIRCUITRF or circuitrf on PATH) and openvaf-r")


def build(parts, path=None):
    """parts: (name, type, props, nets of the pins); a net label at every pin connects them, '0' is ground."""
    p = Project()
    if path:
        p.path = str(path)
    d = Design(name="C", kind="circuit")
    p.designs.append(d)
    x = 0
    for name, t, props, nets in parts:
        c = CK.Component(name, t, x, 0, 0, {**CK.TYPES[t].props, **props})
        d.components.append(c)
        for k, ((px, py), net) in enumerate(zip(CK.pins(c), nets)):
            d.components.append(CK.Component(f"N_{name}_{k}", "LABEL", px, py, 0, {"Net": net}))
        x += 400
    return p, d


def amplifier(va: Path, line: bool = True):
    """Common-emitter stage: bias through Rb and Rc from a 3 V supply, coupling capacitors, optional 50 ohm
    quarter-wave line at the output."""
    out = "nl" if line else "nout"
    parts = [("Port1", "PORT", {"Num": "1"}, ["nin"]),
             ("Cin", "C", {"C": "100pF"}, ["nin", "nb"]),
             ("Rb", "R", {"R": "150kohm"}, ["nvcc", "nb"]),
             ("Rc", "R", {"R": "500ohm"}, ["nvcc", "nc"]),
             ("Cout", "C", {"C": "100pF"}, ["nc", out]),
             ("Port2", "PORT", {"Num": "2"}, ["nout"]),
             ("VCC", "VDC", {"V": "3V"}, ["nvcc", "0"]),
             ("Q1", "DEVICE", {"Kind": "Verilog-A", "File": str(va), "Pins": "c b e", "Params": "is=1e-16 bf=100"},
              ["nc", "nb", "0"])]
    if line:
        parts.append(("TL1", "TLIN", {"Z0": "50ohm", "E": "90deg", "F": "1GHz"}, ["nl", "nout"]))
    return build(parts)


@pytest.fixture
def va(tmp_path):
    f = tmp_path / "hz_bjt.va"
    shutil.copy(DATA / "hz_bjt.va", f)
    return f


def test_nonlinear_parts_are_blocks_with_named_pins():
    c = CK.Component("Q1", "DEVICE", 0, 0, 0, {**CK.TYPES["DEVICE"].props, "Pins": "d g s b"})
    assert CK.nport_count(c) == 4 and len(CK.pins(c)) == 4
    assert "Nonlinear" in CK.CATEGORIES and CK.TYPES["VDC"].category == "Nonlinear"


def test_touchstone_of_the_linear_network_reads_back(tmp_path):
    rng = np.random.default_rng(1)
    for n in (1, 2, 3, 5):
        f = np.array([0.0, 1e9, 2e9])
        S = rng.normal(size=(3, n, n)) + 1j * rng.normal(size=(3, n, n))
        NL.write_touchstone(tmp_path / f"x.s{n}p", f, S)
        fr, Sr, z0 = touchstone.read(tmp_path / f"x.s{n}p")
        assert z0 == 50 and np.allclose(fr, f) and np.allclose(Sr, S, rtol=1e-14, atol=1e-15)


def test_terminal_nodes_and_circuitrf_netlist(tmp_path):
    osdi = tmp_path / "m.osdi"
    osdi.write_bytes(b"")
    p, d = amplifier(osdi)
    nl = NL.gather(p, d, np.array([0.0, 1e9, 2e9]))
    # ports, device pins and the supply: nin, nout, nc, nb, nvcc
    assert len(nl.terminals) == 5 and [x.name for x in nl.devices] == ["Q1"] and nl.sources[0].value == 3
    from hertzon.solver import circuitrf
    text = circuitrf.netlist(nl, 1e9, 5, "Port1", np.array([-30.0, -20.0]), "lin.s5p")
    assert "SnP:HZLIN" in text and "NumPorts=5" in text and "P1Tone:PS_Port1" in text
    assert "R:RT_Port2" in text and "V_1Tone:VS_VCC" in text and "VerilogA:Q1" in text
    assert "Values=-30,-20" in text
    # the reduced network at 0 Hz: the capacitors are open, the line a short between nl and nout... and
    # the supply node sees Rb and Rc
    Y0 = nl.reduced(0.0).real
    k = nl.terminals.index(nl.net.nodes_of["VCC"][0])
    assert abs(Y0[k, k] - (1 / 150e3 + 1 / 500)) < 1e-9


def test_errors_name_what_is_missing(tmp_path):
    p, d = build([("Port1", "PORT", {}, ["a"]), ("Q1", "DEVICE", {"Kind": "Subcircuit", "Model": "npn"},
                                                 ["a", "b", "0"])])
    with pytest.raises(CircuitError, match="SPICE Library"):
        NL.gather(p, d, np.array([1e9]))
    lib = tmp_path / "m.lib"
    lib.write_text("* empty\n")
    d.components.append(CK.Component("LIB1", "SPICELIB", 0, 400, 0, {"File": str(lib), "Section": "", "OSDI": ""}))
    with pytest.raises(NL.EngineError, match="Verilog-A"):
        NL.harmonic_balance(p, d, 1e9, 3, "Port1", np.array([-30.0]))


def _ngspice_reference(va_osdi: Path, tmp: Path, freqs: np.ndarray) -> np.ndarray:
    """S21 and S11 of the same amplifier from one ngspice AC analysis of the whole circuit (ideal line as T)."""
    deck = f"""* reference
Vs nsrc 0 dc 0 ac 1
Rs nsrc nin 50
Cin nin nb 100p
Rb nvcc nb 150k
Rc nvcc nc 500
Cout nc nl 100p
T1 nl 0 nout 0 z0=50 td=0.25n
RL nout 0 50
Vcc nvcc 0 3
N1 nc nb 0 qm
.model qm hz_bjt is=1e-16 bf=100
.control
ac lin {len(freqs)} {freqs[0]} {freqs[-1]}
wrdata ref.txt v(nin) v(nout)
.endc
.end
"""
    (tmp / ".spiceinit").write_text(f"osdi '{va_osdi}'\n")
    (tmp / "ref.cir").write_text(deck)
    subprocess.run([os.environ.get("HERTZON_NGSPICE") or "ngspice", "-b", "ref.cir"], cwd=tmp, check=True,
                   capture_output=True)
    d = np.loadtxt(tmp / "ref.txt")
    vin, vout = d[:, 1] + 1j * d[:, 2], d[:, 4] + 1j * d[:, 5]
    return 2 * vout, 2 * vin - 1


@NGSPICE
def test_bias_and_s_parameters_match_one_ngspice_run(va, tmp_path):
    p, d = amplifier(va)
    fr = np.linspace(0.1e9, 5e9, 50)
    r = analyze(p, d, fr)
    op = r.extra["operating_point"]
    assert 2.0 < op["device_pin_voltages"]["Q1"]["c"] < 2.6 and op["supply_currents"]["VCC"] > 1e-3
    s21, s11 = _ngspice_reference(NL.compiled(va), tmp_path, fr)
    assert np.allclose(r.S[:, 1, 0], s21, rtol=1e-6, atol=1e-9)
    assert np.allclose(r.S[:, 0, 0], s11, rtol=1e-6, atol=1e-9)


@CIRCUITRF
@NGSPICE
def test_harmonic_balance_at_low_drive_is_the_small_signal_gain(va):
    p, d = amplifier(va)
    r = analyze(p, d, np.array([1e9]))
    h = NL.harmonic_balance(p, d, 1e9, 5, "Port1", np.array([-60.0, -10.0]))
    assert np.all(h.converged > 0.5)
    g = h.gain_db(1)
    assert abs(g[0] - 20 * np.log10(abs(r.S[0, 1, 0]))) < 0.02        # linear at -60 dBm
    assert g[1] < g[0] - 1                                            # compressed at -10 dBm
    # the same bias point in both engines (no drive: the DC supply current)
    assert abs(h.supply_current["VCC"][0] - r.extra["operating_point"]["supply_currents"]["VCC"]) < 1e-6
