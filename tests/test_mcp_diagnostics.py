"""Diagnostics in the MCP results: efficiency split, Q and resonances from Z, convergence warnings, hints."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.mcp import diagnostics as dg  # noqa: E402
from hertzon.mcp import tools  # noqa: E402
from hertzon.mcp.protocol import Server  # noqa: E402


def _rlc(f, R=50.0, f0=2.45e9, Q0=30.0):
    """Series RLC with unloaded Q0 at f0."""
    w0 = 2 * np.pi * f0
    L = Q0 * R / w0
    C = 1 / (w0 ** 2 * L)
    w = 2 * np.pi * f
    return R + 1j * (w * L - 1 / (w * C))


def _data(f, Z, z0=50.0):
    S = (Z - z0) / (Z + z0)
    return {"freq": f, "S": S.reshape(-1, 1, 1), "z0": np.array([z0]), "ports": []}


def test_q_and_resonance_of_series_rlc():
    f = np.linspace(2.0e9, 2.9e9, 181)
    Z = _rlc(f, Q0=30.0)
    q = dg.q_yaghjian(f, Z)
    k = int(np.argmin(np.abs(f - 2.45e9)))
    assert q[k] == pytest.approx(30.0, rel=0.02)
    res = dg.resonances(f, Z)
    assert len(res) == 1 and res[0]["type"] == "series" and res[0]["freq_GHz"] == pytest.approx(2.45, abs=0.005)
    ff, Zp, Qp, z0 = dg.port_impedance(_data(f, Z), 0)
    assert np.allclose(Zp, Z) and np.allclose(z0, 50.0)


def test_parallel_resonance_detected():
    f = np.linspace(1e9, 3e9, 201)
    Y = 1 / _rlc(f, R=2000.0, Q0=10.0) * 0 + (1 / 500 + 1j * (2 * np.pi * f * 1e-12 - 1 / (2 * np.pi * f * 4.2e-9)))
    res = dg.resonances(f, 1 / Y)
    assert any(r["type"] == "parallel" for r in res)


def test_efficiency_breakdown_names_the_limit():
    e = dg.efficiency_breakdown(1.0, 0.25, 0.23)       # strong reflection, radiates well
    assert e["limited_by"] == "reflection" and "symptom" in e["note"]
    assert e["total_efficiency"] == pytest.approx(0.23) and e["limited_by"] and e["total_efficiency"] == pytest.approx(0.23)
    e = dg.efficiency_breakdown(1.0, 0.9, 0.3)         # matched, radiates poorly
    assert e["limited_by"] == "radiation efficiency" and e["radiation_efficiency"] == pytest.approx(1 / 3, rel=1e-3)
    e = dg.efficiency_breakdown(1.0, 0.6, 0.42)        # both
    assert e["limited_by"] == "both"
    assert "warning" in dg.efficiency_breakdown(1.0, 0.5, 0.6)


def test_hint_high_q_without_resonance():
    # off-resonance band of a high-Q series RLC: strong reflection, no X = 0 in it
    f = np.linspace(3.0e9, 3.4e9, 41)
    Z = _rlc(f, R=20.0, f0=2.45e9, Q0=60.0)
    d = _data(f, Z)
    ff, Zp, Q, _ = dg.port_impedance(d, 0)
    hints = dg.band_hints(ff, d["S"][:, 0, 0], Zp, Q, dg.resonances(ff, Zp))
    assert hints and "No resonance" in hints[0] and "radiating mode" in hints[0]
    # near the resonance: resonance named, no "no radiating mode" claim
    f = np.linspace(2.3e9, 2.6e9, 61)
    Z = _rlc(f, R=5.0, f0=2.45e9, Q0=60.0)
    d = _data(f, Z)
    ff, Zp, Q, _ = dg.port_impedance(d, 0)
    hints = dg.band_hints(ff, d["S"][:, 0, 0], Zp, Q, dg.resonances(ff, Zp))
    assert hints and "Resonance(s) in the range" in hints[0]


def test_convergence_note():
    conv = {"passes": [{"pass": 1, "delta_s": 0.3}, {"pass": 2, "delta_s": 0.095}], "converged": False}
    n = dg.convergence_note(conv, 0.02)
    assert n["last_delta_s"] == pytest.approx(0.095) and "did NOT converge" in n["warning"] and "0.02" in n["warning"]
    assert "warning" not in dg.convergence_note({"passes": [{"delta_s": 0.01}], "converged": True}, 0.02)


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    tools.S.default_path = None
    tools.S.designs.clear()
    return Server("hertzon", "test", tools.INSTRUCTIONS, tools.TOOLS)


def call(srv, _tool, **args):
    res = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": _tool, "arguments": args}})["result"]
    text = "\n".join(c["text"] for c in res["content"] if c["type"] == "text")
    assert not res["isError"], text
    return json.loads(text)


def test_instructions_and_tool_mention_the_diagnostics():
    assert "Interpreting results" in tools.INSTRUCTIONS and "lossless" in tools.INSTRUCTIONS
    t = next(t for t in tools.TOOLS if t.name == "get_results")
    assert "impedance" in t.description and "efficiency" in t.description


def test_impedance_and_summary_on_a_circuit(server, tmp_path):
    call(server, "create_project", path=str(tmp_path / "c.hzn"))
    call(server, "create_circuit", name="Line")
    call(server, "edit_circuit", add=[
        {"type": "PORT", "name": "P1", "x": 0, "y": 0},
        {"type": "MLIN", "name": "MS1", "attach": "P1:1", "props": {"W": "1.1mm", "L": "20mm"}},
        {"type": "PORT", "name": "P2", "attach": "MS1:2"}])
    call(server, "run_simulation")
    z = call(server, "get_results", kind="impedance", max_points=5)
    assert z["port"] == 1 and len(z["rows"]) == 5
    assert {"R_ohm", "X_ohm", "Q_Z", "mismatch_efficiency"} <= set(z["rows"][0])
    assert z["hints"] == []                       # a matched line reflects nothing
    s = call(server, "get_results")
    assert "port1" in s["port_diagnostics"] and "reading_guide" in s


def test_power_balance_and_delta_s_drift():
    e = dg.efficiency_breakdown(1.0, 0.34, 0.20, Ploss=0.04)
    assert e["power_balance"] == pytest.approx(-0.10) and "balance_warning" not in e
    e = dg.efficiency_breakdown(1.0, 0.34, 0.20, Ploss=0.30)
    assert "port" in e["balance_warning"]
    conv = {"passes": [{"delta_s": 0.3}, {"delta_s": 0.04}, {"delta_s": 0.28}], "converged": False}
    n = dg.convergence_note(conv, 0.02)
    assert "drift_warning" in n and n["delta_s_history"] == [0.3, 0.04, 0.28]
    conv = {"passes": [{"delta_s": 0.3}, {"delta_s": 0.1}, {"delta_s": 0.05}], "converged": False}
    assert "drift_warning" not in dg.convergence_note(conv, 0.02)
