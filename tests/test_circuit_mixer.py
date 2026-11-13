"""Mixers: two-tone harmonic balance by circuitRF on Hertzon's linear network, with diode model cards of SPICE
libraries. The engine runs are skipped when circuitRF (and for the cross-check ngspice) are not installed."""
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from hertzon.model import circuit as CK
from hertzon.model import schema
from hertzon.model.design import Design, Project
from hertzon.solver import circuitrf
from hertzon.solver import nonlinear as NL
from hertzon.solver.circuit import CircuitError

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mixer"
CIRCUITRF = pytest.mark.skipif(not (os.environ.get("HERTZON_CIRCUITRF") or shutil.which("circuitrf")),
                               reason="needs circuitRF (HERTZON_CIRCUITRF or circuitrf on PATH)")
NGSPICE = pytest.mark.skipif(not (os.environ.get("HERTZON_NGSPICE") or shutil.which("ngspice")),
                             reason="needs ngspice")

CARD = """* test library
.model DX D(IS=2.2E-8 RS=6 N=1.08 CJO=0.7p
+ M=0.5 PB=0.65 EG=0.69 XTI=2 BV=15 IBV=1E-4 IKF=0.1) ; comment
.model QX NPN(IS=1e-16 BF=100)
"""


def build(parts, path=None):
    p = Project()
    if path:
        p.path = str(path)
    d = Design(name="C", kind="circuit")
    p.designs = [d]
    x = 0
    for name, t, props, nets in parts:
        c = CK.Component(name, t, x, 0, 0, {**CK.TYPES[t].props, **props})
        d.components.append(c)
        for k, ((px, py), net) in enumerate(zip(CK.pins(c), nets)):
            d.components.append(CK.Component(f"N_{name}_{k}", "LABEL", px, py, 0, {"Net": net}))
        x += 400
    return p, d


def diode_mixer(lib: Path, model="DX"):
    return build([("LIB1", "SPICELIB", {"File": str(lib)}, []),
                  ("LO", "PORT", {"Num": "1"}, ["lo"]), ("RF", "PORT", {"Num": "2"}, ["rf"]),
                  ("IF", "PORT", {"Num": "3"}, ["if"]),
                  ("R1", "R", {"R": "50ohm"}, ["lo", "a"]), ("R2", "R", {"R": "50ohm"}, ["rf", "a"]),
                  ("L1", "L", {"L": "10nH"}, ["a", "b"]), ("C1", "C", {"C": "3pF"}, ["b", "0"]),
                  ("R3", "R", {"R": "10ohm"}, ["b", "if"]),
                  ("D1", "DEVICE", {"Kind": "Model card", "Model": model, "Pins": "a k"}, ["a", "0"])])


@pytest.fixture
def lib(tmp_path):
    f = tmp_path / "d.lib"
    f.write_text(CARD)
    return f


def test_spice_numbers_and_cards():
    assert NL.spice_number("0.7p") == pytest.approx(0.7e-12)
    assert NL.spice_number("1meg") == 1e6 and NL.spice_number("15V") == 15 and NL.spice_number("2.2E-8") == 2.2e-8
    assert NL.spice_number("3mil") == pytest.approx(76.2e-6) and NL.spice_number("2m") == 2e-3
    cards = NL._cards(CARD)
    typ, p = cards["dx"]
    assert typ == "d" and p["CJO"] == "0.7p" and p["PB"] == "0.65" and p["IKF"] == "0.1"
    assert cards["qx"][0] == "npn"


def test_diode_card_to_circuitrf():
    text, left = circuitrf.diode_params(NL._cards(CARD)["dx"][1], "area=2")
    kv = dict(x.split("=") for x in text.split())
    assert float(kv["Cj0"]) == pytest.approx(0.7e-12) and float(kv["Vj"]) == 0.65 and float(kv["Is"]) == 2.2e-8
    assert float(kv["Xti"]) == 2 and float(kv["Area"]) == 2 and float(kv["Temp"]) == 27 == float(kv["Tnom"])
    assert left == ["IKF"]                    # circuitRF's diode has no high-injection knee: reported, not hidden


def test_mixer_netlist_two_tones(lib):
    p, d = diode_mixer(lib)
    nl = NL.gather(p, d, np.array([1e9]))
    NL.check_hb_devices(nl)
    text = circuitrf.mixer_netlist(nl, "LO", "RF", 3.0e9, 3.2e9, np.array([0.0, 7.0]), -30.0, 13, "lin.s4p")
    assert "P1Tone:PS_LO ps_LO 0 Num=1 Pavl=Plo_dbm" in text and "Freq=3000000000" in text
    assert "P1Tone:PS_RF ps_RF 0 Num=2 Pavl=-30" in text and "R:RT_IF ps_IF 0 R=50" in text
    assert "NumFreqs=2 Tone[1]=3000000000 Tone[2]=3200000000 MaxMixOrder=13" in text
    assert "Var=Plo_dbm Inner=HB1 Values=0,7" in text
    dl = [ln for ln in text.splitlines() if ln.startswith("Diode:D1 ")]
    assert len(dl) == 1 and float(dl[0].split("Cj0=")[1].split()[0]) == pytest.approx(0.7e-12)
    assert any("IKF" in w for w in nl.net.warnings)
    assert len(circuitrf.mix_products(5)) == 2 * 25 + 2 * 5 + 1


def test_harmonic_balance_refuses_other_cards(lib):
    p2, d2 = build([("LIB1", "SPICELIB", {"File": str(lib)}, []), ("LO", "PORT", {"Num": "1"}, ["a"]),
                    ("Q1", "DEVICE", {"Kind": "Model card", "Model": "QX", "Pins": "c b e"}, ["a", "a", "0"])])
    nl = NL.gather(p2, d2, np.array([1e9]))
    with pytest.raises(NL.EngineError, match="diode model cards"):
        NL.check_hb_devices(nl)


def test_mixer_setup_and_port_checks(lib):
    assert "Mixer" in schema.CIRCUIT_SETUP[0].choices
    names = {q.name for q in schema.CIRCUIT_SETUP}
    assert {"LOPort", "RFPort", "IFPort", "LOPower", "RFPower", "IFFreq", "LOSide", "MixOrder"} <= names
    p, d = diode_mixer(lib)
    with pytest.raises(CircuitError, match="no port named"):
        NL.mixer(p, d, "LOx", "RF", "IF", np.array([3e9]), 0.2e9, "Low", np.array([7.0]), -30)
    with pytest.raises(CircuitError, match="LO frequency must be positive"):
        NL.mixer(p, d, "LO", "RF", "IF", np.array([0.1e9]), 0.2e9, "Low", np.array([7.0]), -30)


@CIRCUITRF
def test_mixer_runs_and_sweeps_lo_power(lib, tmp_path):
    p, d = diode_mixer(lib)
    m = NL.mixer(p, d, "LO", "RF", "IF", np.array([3.2e9, 3.4e9]), 0.2e9, "Low", np.array([0.0, 7.0]), -30, 9)
    assert m.port_power_dbm.shape[:3] == (2, 2, 3) and np.all(m.converged > 0.5)
    cl = m.conversion_loss_db()
    assert np.all(np.isfinite(cl)) and np.all(cl[1] < cl[0])          # more LO, less conversion loss
    assert np.allclose(m.lo_freq, [3.0e9, 3.2e9])
    high = NL.mixer(p, d, "LO", "RF", "IF", np.array([3.2e9]), 0.2e9, "High", np.array([7.0]), -30, 9)
    assert high.lo_freq[0] == pytest.approx(3.4e9)


@CIRCUITRF
@NGSPICE
def test_mixer_matches_ngspice_transient():
    """The example's check: the IF and the LO leakage of a diode mixer with the HSMS-282x card, circuitRF's
    harmonic balance against an ngspice transient analysis."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_ngspice", EXAMPLE / "check_ngspice.py")
    chk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chk)
    hz = chk.hertzon([7.0])[0]
    ng = chk.ngspice(7.0)
    assert abs(hz[0] - ng[0]) < 0.3 and abs(hz[1] - ng[1]) < 0.05 and abs(hz[2] - ng[2]) < 0.05


def test_nport_with_two_pins_on_one_net(tmp_path):
    """Both pins of a 2-port (each 100 ohm to ground) on one net are 50 ohm in parallel: before the fix one of
    the two stamps was lost (the cold ends of a balun tied to one IF node lost half of the balun)."""
    from hertzon.solver.circuit import analyze
    f = tmp_path / "shunts.s2p"
    f.write_text("# GHz S RI R 50\n1 0.3333333333333333 0 0 0 0 0 0.3333333333333333 0\n"
                 "2 0.3333333333333333 0 0 0 0 0 0.3333333333333333 0\n")
    p, d = build([("P1", "PORT", {"Num": "1"}, ["a"]), ("B1", "SNP", {"File": str(f), "Ports": "2"}, ["a", "a"])])
    r = analyze(p, d, np.array([1.5e9]))
    assert abs(r.S[0, 0, 0]) < 1e-6
