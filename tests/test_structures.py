"""Design wizard catalogue (model/structures): synthesis, circuit, 3D design and optimization of each structure,
the MCP tools and the dialog. No FEM solve."""
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model import structures as ST  # noqa: E402
from hertzon.model.design import Project  # noqa: E402
from hertzon.model.structures.couplers import Branchline  # noqa: E402
from hertzon.model.structures.matching import binomial_impedances  # noqa: E402


def _made(key, values=None, **kw):
    p = Project("t")
    r = ST.create_structure(key, values or {}, project=p, **kw)
    p.designs += r.designs
    p.materials += r.materials
    return p, r


def _s(p, circ, f_ghz):
    from hertzon.solver.circuit import analyze
    return analyze(p, circ, np.atleast_1d(f_ghz) * 1e9).S


def db(x):
    return 20 * np.log10(np.abs(x))


def test_catalogue_is_consistent():
    keys = [s.key for s in ST.CATALOG]
    assert len(keys) == len(set(keys))
    cats = dict(ST.CATEGORIES)
    for s in ST.CATALOG:
        assert s.category in cats, s.key
        assert ST.icon(s).startswith("<svg"), s.key
        assert s.title and s.description and s.use
    avail = {s.key for s in ST.CATALOG if s.status == ST.AVAILABLE}
    assert {"qwt", "branchline", "patch", "hairpin"} <= avail
    with pytest.raises(ValueError, match="not available"):
        ST.structure("ratrace").create()
    with pytest.raises(ValueError, match="own designer"):
        ST.create_structure("patch")


def test_binomial_transformer_impedances():
    # Pozar example 5.6: three sections from 50 to 100 ohm
    z = binomial_impedances(50, 100, 3)
    assert np.allclose(z, [54.5, 70.7, 91.7], atol=0.1)
    assert binomial_impedances(50, 12.5, 1) == pytest.approx([25.0])


def test_branchline_impedances():
    zs, zp = Branchline.impedances(50, 3.0103)
    assert zs == pytest.approx(50 / math.sqrt(2), rel=1e-4) and zp == pytest.approx(50, rel=1e-4)
    zs, zp = Branchline.impedances(50, 6.0206)                   # quarter of the power to the coupled port
    assert zs == pytest.approx(50 * math.sqrt(0.75), rel=1e-4) and zp == pytest.approx(zs * 2, rel=1e-4)


@pytest.mark.parametrize("key,values,ports", [("qwt", {}, 2), ("qwt", {"n": 4, "zl": 100}, 2),
                                              ("branchline", {}, 4), ("branchline", {"c": 6}, 4),
                                              ("tee", {}, 3), ("tee", {"k": 2}, 3), ("stepped", {}, 2),
                                              ("stepped", {"n": 3}, 2), ("stub", {}, 1),
                                              ("stub", {"xl": -30}, 1), ("stubbp", {}, 2), ("stubbp", {"n": 4}, 2),
                                              ("edge", {}, 2), ("edge", {"n": 4}, 2), ("stubbs", {}, 2),
                                              ("stubbs", {"n": 5}, 2),
                                              ("wilkinson", {}, 3)])
def test_circuit_3d_and_optimization_share_the_variables(key, values, ports):
    p, r = _made(key, values)
    circ, d3 = r.designs
    assert circ.kind == "circuit" and d3.kind == "3d"
    assert len(d3.excitations) == ports
    op = circ.optimetrics[0]
    assert op.name == r.optimization == "MultiFidelity1"
    assert op.props["MultiFidelity"] == "True" and op.props["FineModel"] == d3.name
    for v in op.variables:
        assert v["name"] in circ.variables and v["name"] in d3.variables
        assert float(v["min"].removesuffix("mm")) >= 0.1 - 1e-9          # never below the smallest feature
    assert r.materials and r.materials[0].name in {m.name for m in p.materials}
    assert not [w for w in r.warnings if "does not close" in w]


def test_start_values_are_close():
    p, r = _made("qwt")
    s = _s(p, r.designs[0], [2.2, 2.45, 2.7])
    assert (db(s[:, 0, 0]) < -25).all()
    p, r = _made("branchline")
    s = _s(p, r.designs[0], 2.45)[0]
    assert abs(db(s[1, 0]) + 3.01) < 0.6 and abs(db(s[2, 0]) + 3.01) < 0.6
    assert db(s[0, 0]) < -15 and db(s[3, 0]) < -15
    assert abs(np.degrees(np.angle(s[2, 0] / s[1, 0])) + 90) < 3


def test_wilkinson_isolates_the_outputs():
    p, r = _made("wilkinson")
    s = _s(p, r.designs[0], 2.45)[0]
    assert abs(db(s[1, 0]) + 3.01) < 0.3 and abs(db(s[2, 0]) + 3.01) < 0.3
    assert db(s[0, 0]) < -25 and db(s[1, 1]) < -25 and db(s[2, 1]) < -25
    d3 = r.designs[1]
    assert d3.variables["R1_Gap"] == "2*La + Wa" and any(b.name == "R1" for b in d3.boundaries)


def test_tee_divider_splits_as_asked():
    from hertzon.model.structures.dividers import TeeDivider
    assert TeeDivider.impedances(50, 1) == pytest.approx((50 * math.sqrt(2), 50 * math.sqrt(2)))
    p, r = _made("tee", {"k": 2})
    s = _s(p, r.designs[0], 2.45)[0]
    assert db(s[0, 0]) < -25
    assert abs(db(s[1, 0]) - 10 * np.log10(1 / 3)) < 0.4 and abs(db(s[2, 0]) - 10 * np.log10(2 / 3)) < 0.4


def test_stepped_lowpass_prototype():
    from hertzon.model.structures.filters import ripple_return_loss
    assert ripple_return_loss(0.1) == pytest.approx(16.43, abs=0.01)
    p, r = _made("stepped", {"n": 5, "fc": 2.5})
    s = _s(p, r.designs[0], [0.5, 1.5, 5.0])
    assert (db(s[:2, 0, 0]) < -15).all() and db(s[2, 1, 0]) < -20


def test_single_stub_matches_the_load():
    from hertzon.model.structures.matching import single_stub
    for rl, xl in [(25, 15), (100, -40), (50, 30), (10, 0)]:
        d, length = single_stub(50, rl, xl)
        b2 = 2 * math.pi
        zl = complex(rl, xl)
        t = math.tan(b2 * d)
        z = 50 * (zl + 1j * 50 * t) / (50 + 1j * zl * t)        # load seen through the line d
        y = 1 / z + 1j * math.tan(b2 * length) / 50              # plus the open stub
        assert abs(y * 50 - 1) < 1e-6, (rl, xl)
    p, r = _made("stub")
    assert db(_s(p, r.designs[0], 2.45)[0, 0, 0]) < -15


def test_edge_coupled_bandpass_synthesis():
    from hertzon.model.structures.base import Substrate
    from hertzon.model.structures.filters import EdgeCoupledBandpass, coupled_lines
    from hertzon.solver.tlines import coupled_microstrip
    # Pozar example 8.8: N = 3, 0.5 dB, 10 %: J Z0 = 0.3137, 0.1187, 0.1187, 0.3137
    assert np.allclose(EdgeCoupledBandpass.inverters(3, 0.5, 0.1), [0.3137, 0.1187, 0.1187, 0.3137], atol=2e-4)
    sub = Substrate()
    w, s, _ = coupled_lines(sub, 70.0, 37.0, 5.0)
    (ze, _), (zo, _) = coupled_microstrip(5e9, w * 1e-3, s * 1e-3, sub.h * 1e-3, sub.t * 1e-6, sub.er, 0, sub.sigma)
    assert ze == pytest.approx(70, rel=1e-4) and zo == pytest.approx(37, rel=1e-4)
    p, r = _made("edge")
    s = _s(p, r.designs[0], [4.0, 5.0, 6.0])
    assert db(s[1, 0, 0]) < -12 and db(s[0, 1, 0]) < -20 and db(s[2, 1, 0]) < -20


def test_open_stub_bandstop_blocks_the_band():
    from hertzon.model.structures.filters import chebyshev_edge
    assert chebyshev_edge(3, 0.1, 20) == pytest.approx(2.63, abs=0.01)
    p, r = _made("stubbs")
    assert r.designs[0].optimetrics[0].props["Optimizer"] == "Latin Hypercube + Nelder-Mead"
    s = _s(p, r.designs[0], [0.9, 3.0, 5.1])
    assert db(s[1, 1, 0]) < -40 and db(s[0, 1, 0]) > -1 and db(s[2, 1, 0]) > -1


def test_shorted_stub_bandpass_passes_the_band():
    p, r = _made("stubbp")
    s = _s(p, r.designs[0], [1.0, 3.0, 5.0])
    assert db(s[1, 0, 0]) < -12 and db(s[0, 1, 0]) < -10 and db(s[2, 1, 0]) < -10


@pytest.mark.parametrize("key", ["branchline", "tee", "stepped", "stub", "stubbp", "edge", "stubbs", "wilkinson"])
def test_circuit_optimization_meets_the_goals(tmp_path, key):
    from hertzon.solver.optimize import Optimizer
    p, r = _made(key, multi_fidelity=False)
    path = tmp_path / "b.hzn"
    p.save(path)
    out = tmp_path / "o"
    out.mkdir()
    Optimizer(str(path), r.main, r.optimization, out).run()
    assert json.loads((out / "best.json").read_text())["cost"] == 0


def test_spec_limits_and_names():
    with pytest.raises(ValueError, match="between"):
        ST.structure("qwt").spec({"n": 7})
    with pytest.raises(ValueError, match="unknown field"):
        ST.structure("qwt").spec({"nope": 1})
    p = Project("t")
    a = ST.create_structure("qwt", project=p)
    p.designs += a.designs
    b = ST.create_structure("qwt", project=p)
    assert {d.name for d in a.designs}.isdisjoint(d.name for d in b.designs)
    assert b.designs[0].optimetrics[0].props["FineModel"] == b.designs[1].name


@pytest.fixture()
def server(tmp_path, monkeypatch):
    from hertzon.mcp import tools
    from hertzon.mcp.protocol import Server
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    tools.S.default_path = None
    tools.S.designs.clear()
    return Server("hertzon", "test", tools.INSTRUCTIONS, tools.TOOLS)


def _call(srv, tool, **args):
    res = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": tool, "arguments": args}})["result"]
    return res["isError"], "\n".join(c["text"] for c in res["content"] if c["type"] == "text")


def test_mcp_list_and_create(server, tmp_path):
    err, text = _call(server, "list_structures")
    assert not err
    rows = {s["key"]: s for s in json.loads(text)["structures"]}
    assert rows["patch"]["tool"] == "create_patch_antenna" and rows["branchline"]["fields"]
    _call(server, "create_project", path=str(tmp_path / "a.hzn"))
    err, text = _call(server, "create_structure", key="branchline", spec={"f0": 5.8, "c": 3})
    assert not err, text
    out = json.loads(text)
    assert out["circuit"] == "Branchline_5.8GHz_Circuit" and out["design_3d"] == "Branchline_5.8GHz"
    err, text = _call(server, "get_project")
    assert {"Branchline_5.8GHz_Circuit", "Branchline_5.8GHz"} <= set(json.loads(text)["designs"])
    err, text = _call(server, "create_structure", key="ratrace")
    assert err and "not available" in text


def test_wizard_dialog_creates_a_structure(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.design_wizard import DesignWizard, goal_text
    dlg = DesignWizard(None, Project("t"), None, start="qwt")
    assert dlg.next_btn.isEnabled()
    dlg._next()
    assert dlg.stack.currentIndex() == 1 and dlg.table.rowCount() == 7
    dlg.fields["n"].setValue(3)
    dlg.refresh()
    assert dlg.table.rowCount() == 9
    dlg._create(True)
    assert dlg.result is not None and dlg.run == "MultiFidelity1"
    dlg = DesignWizard(None, Project("t"), None, start="ratrace")
    assert not dlg.next_btn.isEnabled()
    dlg = DesignWizard(None, Project("t"), None, start="patch")
    dlg._next()
    assert dlg.designer == "antenna_patch"
    text = goal_text(ST.structure("branchline").goals(ST.structure("branchline").spec()))
    assert "≤ <code>dB(S(2,1))</code> ≤" in text and "&lt;" not in text


def test_main_window_inserts_the_wizard_designs(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog
    QApplication.instance() or QApplication([])
    from hertzon.gui import design_wizard as DW
    from hertzon.gui.main_window import MainWindow

    def fake_exec(self):
        self.select(ST.structure("branchline"))
        self._next()
        self._create(False)
        return QDialog.Accepted
    monkeypatch.setattr(DW.DesignWizard, "exec", fake_exec)
    w = MainWindow()
    w.design_wizard()
    names = [d.name for d in w.doc.project.designs]
    assert "Branchline_2.45GHz_Circuit" in names and "Branchline_2.45GHz" in names
    assert w.doc.design_name == "Branchline_2.45GHz_Circuit"
    assert any(m.name.startswith("Sub1") for m in w.doc.project.materials)
    w.doc.dirty = False
    w.close()
