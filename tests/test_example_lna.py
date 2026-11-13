"""The shipped example LNA_2G4.hzn (2.4 GHz amplifier with the onsemi NSVF4009SG4) and its device models."""
import subprocess
import sys
from pathlib import Path

import numpy as np

from hertzon.model import circuit as CK
from hertzon.model.design import Project
from hertzon.solver import nonlinear as NL

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "LNA_2G4.hzn"
MODELS = ROOT / "examples" / "models"
sys.path.insert(0, str(ROOT / "tests"))
from test_circuit_nonlinear import NGSPICE, CIRCUITRF  # noqa: E402


def test_example_is_current_and_finds_its_models(tmp_path):
    """The .hzn is what make_lna.py writes, and opening it makes the model paths absolute, so that a copy opened
    untitled (File > Open Example) still finds them."""
    sys.path.insert(0, str(ROOT / "examples"))
    import make_lna
    made = make_lna.lna()
    made.save(tmp_path / "LNA_2G4.hzn")
    shipped = Project.load(EXAMPLE)
    fresh = Project.load(tmp_path / "LNA_2G4.hzn")
    for a, b in zip(shipped.designs, fresh.designs):
        assert [(c.name, c.type, c.x, c.y) for c in a.components] == [(c.name, c.type, c.x, c.y) for c in b.components]
    lna = shipped.design("LNA")
    q = next(c for c in lna.components if c.name == "Q1")
    assert Path(q.props["File"]).is_absolute() and Path(q.props["File"]).is_file()
    snp = next(c for c in shipped.design("LNA_Measured").components if c.name == "Q1")
    assert Path(snp.props["File"]).is_file()
    # every pin is connected: no part hangs in the air
    comps, nodes_of, _, _ = CK.netlist(lna.components, lna.wires, [])
    uses = {}
    for c in comps:
        for n in nodes_of.get(c.name, []):
            uses[n] = uses.get(n, 0) + 1
    assert all(k == 0 or v >= 2 for k, v in uses.items())


def test_measured_block_reproduces_the_data_sheet():
    """The 4-pin block of the measured S-parameters, with both emitter pins on ground, is the data sheet's
    common-emitter two-port."""
    from hertzon.model import touchstone
    f4, S4, z4 = touchstone.read(MODELS / "NSVF4009SG4_3V_10mA_4pin.s4p")
    f2, S2, _ = touchstone.read(MODELS / "NSVF4009SG4_3V_10mA.s2p")
    Y = np.linalg.solve(np.eye(4) + S4, np.eye(4) - S4) / z4
    y = Y[:, :2, :2]
    S = np.linalg.solve((np.eye(2) + z4 * y).transpose(0, 2, 1), (np.eye(2) - z4 * y).transpose(0, 2, 1))
    S = S.transpose(0, 2, 1)
    assert np.allclose(f4, f2) and np.max(np.abs(S - S2)) < 2e-4


def test_compiled_model_goes_to_temp_when_the_folder_is_read_only(tmp_path, monkeypatch):
    va = tmp_path / "m.va"
    va.write_text("// test\n")
    calls = []

    def fake_run(cmd, **kw):
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_bytes(b"osdi")
        calls.append(out)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(NL, "openvaf", lambda: "openvaf-r")
    monkeypatch.setattr(NL.subprocess, "run", fake_run)
    real_mkdir = Path.mkdir

    def no_write(self, *a, **k):
        if self.name == ".hertzon-osdi":
            raise PermissionError("read-only")
        return real_mkdir(self, *a, **k)
    monkeypatch.setattr(Path, "mkdir", no_write)
    out = NL.compiled(va)
    assert out.is_file() and ".hertzon-osdi" not in str(out)
    assert NL.compiled(va) == out and len(calls) == 1          # cached


@NGSPICE
def test_lna_bias_gain_match_and_stability(tmp_path):
    from hertzon.solver.circuit import analyze
    p = Project.load(EXAMPLE)
    d = p.design("LNA")
    fr = np.concatenate([[2.4e9, 2.45e9, 2.5e9], np.geomspace(0.3e9, 12e9, 40)])
    r = analyze(p, d, fr)
    op = r.extra["operating_point"]
    assert abs(op["device_pin_voltages"]["Q1"]["c"] - 3.0) < 0.1           # VCE = 3 V
    assert abs(op["supply_currents"]["VCC"] - 10.3e-3) < 0.5e-3            # IC = 10 mA (+ base and R2)
    S = r.S
    assert np.all(20 * np.log10(np.abs(S[:3, 1, 0])) > 12.5)
    assert np.all(20 * np.log10(np.abs(S[:3, 0, 0])) < -13)
    assert np.all(20 * np.log10(np.abs(S[:3, 1, 1])) < -13)
    s11, s12, s21, s22 = S[:, 0, 0], S[:, 0, 1], S[:, 1, 0], S[:, 1, 1]
    delta = s11 * s22 - s12 * s21
    mu = (1 - np.abs(s11) ** 2) / (np.abs(s22 - delta * np.conj(s11)) + np.abs(s12 * s21))
    assert mu.min() > 1.03
    # the same board with the measured transistor data: within 1 dB of the model in the band
    m = analyze(p, p.design("LNA_Measured"), fr[:3])
    assert np.all(np.abs(20 * np.log10(np.abs(m.S[:, 1, 0] / S[:3, 1, 0]))) < 1)


@CIRCUITRF
@NGSPICE
def test_lna_compression(tmp_path):
    p = Project.load(EXAMPLE)
    h = NL.harmonic_balance(p, p.design("LNA"), 2.45e9, 5, "RF_in", np.array([-30.0, -9.0, -3.0]))
    assert np.all(h.converged > 0.5)
    g = h.gain_db(1)
    assert 12.5 < g[0] < 13.5 and g[0] - g[1] < 1 and g[0] - g[2] > 1          # OP1dB between -9 and -3 dBm in


def test_lna_board_is_placed_without_open_connections_and_lays_out_with_a_port_per_pad():
    from hertzon.model import board as BD
    from hertzon.model import circuit_layout as CL
    p = Project.load(EXAMPLE)
    d = p.design("LNA")
    parts, nodes, _ = BD.board_parts(p, d)
    b = BD.load(d)
    assert b is not None and set(b.placements) == set(parts)
    assert BD.ratsnest(parts, b, nodes) == [] and BD.overlaps(parts, b, nodes) == []
    L, warn = CL.layout_design(p, d, "LNA_Layout")
    assert [e.name for e in L.ports()] == ["1", "2", "Q1_c_pad1", "Q1_e2_pad2", "Q1_b_pad3", "Q1_e1_pad4",
                                           "VCC_supply"]
    assert any("C4: its ground pad" in w for w in warn)
