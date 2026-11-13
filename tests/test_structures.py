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
        ST.structure("slot").create()
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
                                              ("wilkinson", {}, 3), ("coupled", {}, 4),
                                              ("coupled", {"c": 20}, 4)])
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


def test_rat_race_splits_in_phase_and_isolates():
    p, r = _made("ratrace")
    circ, d3 = r.designs
    s = _s(p, circ, 2.45)[0]
    assert abs(db(s[1, 0]) + 3.2) < 0.3 and abs(db(s[2, 0]) + 3.2) < 0.3
    assert db(s[0, 0]) < -30 and db(s[3, 0]) < -30 and db(s[3, 3]) < -30
    assert abs(np.angle(s[2, 0] / s[1, 0], deg=True)) < 3
    assert {"Wr", "Rm"} <= set(d3.variables) and len(d3.excitations) == 4
    board, copper = ST.base.top_view(d3, p)
    assert len(copper) == 5 and len(copper[0]) == 146          # the ring (outer + inner + slit) and four feeds
    # the feeds leave the ring radially at 180, 240, 300 and 0 degrees and end on the board edge, square to it
    ev = d3.evaluator(p)
    rp = ev.length("Rp") * 1e3
    for a in d3.excitations:
        x, y = (v * 1e3 for v in ev.points(a.props["IntegrationLine"])[0][:2])
        ang = {"2": 180, "1": 240, "3": 300, "4": 0}[a.name]
        assert (x, y) == pytest.approx((rp * math.cos(math.radians(ang)), rp * math.sin(math.radians(ang))), abs=1e-6)
        assert any(_on_segment((x, y), board[i - 1], board[i]) for i in range(len(board)))


def _on_segment(q, a, b, tol=1e-6):
    (x, y), (x1, y1), (x2, y2) = q, a, b
    cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
    dot = (x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)
    return abs(cross) < tol * math.hypot(x2 - x1, y2 - y1) and 0 <= dot <= (x2 - x1) ** 2 + (y2 - y1) ** 2


def test_coupled_line_coupler_couples_as_asked():
    from hertzon.model.structures.couplers import CoupledLineCoupler
    ze, zo = CoupledLineCoupler.impedances(50, 10)               # Pozar example 7.7: 69.4 and 36.0 ohm
    assert ze == pytest.approx(69.37, abs=0.05) and zo == pytest.approx(36.04, abs=0.05)
    p, r = _made("coupled")
    s = _s(p, r.designs[0], 2.45)[0]
    assert abs(db(s[2, 0]) + 15) < 0.5 and db(s[0, 0]) < -20 and db(s[3, 0]) < -20


def test_printed_ifa():
    from hertzon.model.structures.antennas import ifa_eeff
    assert ifa_eeff(4.4) == pytest.approx(1.51)
    p, r = _made("ifa")
    (d3,) = r.designs
    op = d3.optimetrics[0]
    assert [v["name"] for v in op.variables if v["include"]] == ["L", "s"]
    assert r.materials[0].name == "IFA_Er3.66" and len(d3.excitations) == 1
    board, copper = ST.base.top_view(d3, p)
    assert board and len(copper) == 4
    lam = 299.792458 / 2.45
    assert float(d3.variables["L"].removesuffix("mm")) + 4 == pytest.approx(lam / 4 / math.sqrt(ifa_eeff(3.66)))


def test_circular_patch_radius_and_probe():
    from scipy.special import jv
    from hertzon.model.structures.antennas import circular_patch
    sub = ST.Substrate(er=2.2, h=1.588, tand=0.0009)
    c = circular_patch(10, sub)
    # TM11 resonance of the effective radius: f = 1.8412 c0 / (2 pi ae sqrt(er))
    assert 1.84118 * 299.792458 / (2 * math.pi * c["ae"] * math.sqrt(2.2)) == pytest.approx(10, rel=0.015)
    assert c["a"] < c["ae"] < 1.2 * c["a"] and c["efficiency"] > 0.9
    p, r = _made("cpatch")
    (d3,) = r.designs
    op = d3.optimetrics[0]
    assert [v["name"] for v in op.variables if v["include"]] == ["a", "rho"]
    assert r.materials[0].name == "Patch_Er3.66" and op.props["Optimizer"] == "Derivative Trust Region"
    c = circular_patch(2.45, ST.Substrate())
    rho = float(d3.variables["rho"].removesuffix("mm"))
    assert c["r_edge"] * (jv(1, 1.84118 * rho / c["ae"]) / jv(1, 1.84118)) ** 2 == pytest.approx(50, rel=1e-3)
    board, copper = ST.base.top_view(d3, p)
    assert board and len(copper) == 1 and len(copper[0]) == 72


def test_cp_patch_cuts_two_corners():
    from hertzon.model.structures.antennas import square_patch
    p, r = _made("cppatch")
    (d3,) = r.designs
    op = d3.optimetrics[0]
    assert [v["name"] for v in op.variables if v["include"]] == ["L", "c", "xf"]
    assert [g["calc"] for g in op.goals] == ["dB(S(1,1))", "dB(AxialRatioAtMax)"]
    s = square_patch(2.45, ST.Substrate())
    L, c = (float(d3.variables[k].removesuffix("mm")) for k in ("L", "c"))
    assert c * c / (L * L) == pytest.approx(1 / (2 * s["q"]), rel=1e-3)
    board, copper = ST.base.top_view(d3, p)
    (poly,) = copper
    area = 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))
    assert area == pytest.approx(L * L - c * c, rel=1e-6)     # counter-clockwise, two triangles cut


@pytest.mark.parametrize("key,feed,radiator", [("uwb", 0, ["a"]), ("uwb", 1, ["a"]),
                                               ("uwbrect", "microstrip", ["W", "L", "c"]),
                                               ("uwbrect", "coplanar", ["W", "L", "c"])])
def test_uwb_monopoles(key, feed, radiator):
    from hertzon.model.structures.uwb import CPW, cpw_z0, substrate_factor
    assert substrate_factor(4.4) == pytest.approx(1.15, abs=0.002)
    p, r = _made(key, {"feed": feed})
    (d3,) = r.designs
    op = d3.optimetrics[0]
    assert op.solution == "Setup1 : Sweep" and op.props["Optimizer"] == "Derivative Trust Region"
    assert [v["name"] for v in op.variables if v["include"]] == radiator + ["p", "Lg"]
    assert op.goals == [{"calc": "dB(S(1,1))", "start": "3.1GHz", "stop": "10.6GHz", "cond": "<=", "value": "-10",
                         "weight": "1"}]
    st = d3.setups[0]
    assert st.props["Frequency"] == "10.6GHz" and st.sweeps[0].props["Enabled"] != "False"
    assert st.sweeps[0].props["Start"] == "2.48GHz" and st.sweeps[0].props["Stop"] == "11.66GHz"
    v = {k: float(x.removesuffix("mm")) for k, x in d3.variables.items() if x.endswith("mm")}
    k = substrate_factor(3.66)
    height = 2 * v["a"] + v["a"] / 4 if key == "uwb" else v["L"] + v["W"] / (2 * math.pi)
    assert height + v["p"] == pytest.approx(72 / (3.1 * k), rel=1e-5)          # lowest frequency of the planar monopole
    cpw = ST.structure(key).spec({"feed": feed})["feed"] == CPW
    board, _top, bottom = ST.base.top_view(d3, p, bottom=True)
    assert board and len(d3.excitations) == 1
    if cpw:
        assert cpw_z0(v["wf"], v["s"], ST.Substrate()) == pytest.approx(50, rel=1e-3) and not bottom
        assert d3.excitations[0].props["IntegrationLine"] == "0,gb,h;0,gb + gp,h"
    else:
        assert len(bottom) == 1 and d3.excitations[0].props["IntegrationLine"] == "0,0,0;0,0,h"
        xs, ys = zip(*bottom[0])
        assert max(ys) == pytest.approx(v["Lg"]) and max(xs) - min(xs) == pytest.approx(v["Bx"])
    assert {o.name for o in d3.objects if not o.consumed_by} >= {"Substrate", "Radiator", "PortSheet"}


def test_choice_fields():
    s = ST.structure("uwb")
    assert s.spec({"feed": "Coplanar"})["feed"] == 1 and s.spec({"feed": "micro"})["feed"] == 0
    assert s.spec({"feed": 1})["feed"] == 1
    with pytest.raises(ValueError, match="choose one of"):
        s.spec({"feed": "stripline"})
    with pytest.raises(ValueError, match="between"):
        s.spec({"feed": 2})


@pytest.mark.parametrize("key,var", [("dipole", "Larm"), ("monopole", "L")])
def test_wire_antennas_are_3d_designs_with_a_tuning(key, var):
    from hertzon.model.structures.antennas import resonant_fraction
    assert resonant_fraction(100) == pytest.approx(0.474, abs=0.002)
    p, r = _made(key)
    (d3,) = r.designs
    assert d3.kind == "3d" and r.main == d3.name and len(d3.excitations) == 1
    op = d3.optimetrics[0]
    assert op.solution == "Setup1 : LastAdaptive" and op.props["Optimizer"] == "Derivative Trust Region"
    assert [v["name"] for v in op.variables if v["include"]] == [var]
    assert op.goals[0]["start"] == op.goals[0]["stop"] == "1GHz"
    lam = 299.792458
    assert 0.23 * lam < float(d3.variables[var].removesuffix("mm")) < 0.245 * lam
    board, rects = ST.base.side_view(d3, p)
    assert len(rects) == 2


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


@pytest.mark.parametrize("key", ["branchline", "tee", "stepped", "stub", "stubbp", "edge", "stubbs", "wilkinson", "coupled"])
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
    err, text = _call(server, "create_structure", key="dipole", spec={"f0": 2.4})
    assert not err and json.loads(text)["design_3d"] == "Dipole_2.4GHz"
    err, text = _call(server, "create_structure", key="slot")
    assert err and "not available" in text
    assert rows["uwb"]["fields"][0]["choices"][1].startswith("Coplanar")
    err, text = _call(server, "create_structure", key="uwb", spec={"feed": "coplanar"})
    assert not err and json.loads(text)["design_3d"] == "UWBDisc_CPW_3.1-10.6GHz"


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
    dlg = DesignWizard(None, Project("t"), None, start="slot")
    assert not dlg.next_btn.isEnabled()
    dlg = DesignWizard(None, Project("t"), None, start="uwbrect")
    dlg._next()
    assert dlg.values()["feed"] == 0 and "sweep of the band" in dlg.creates.text()
    dlg.fields["feed"].setCurrentIndex(1)
    dlg.refresh()
    assert dlg.preview.designs[0].name == "UWBRect_CPW_3.1-10.6GHz"
    dlg = DesignWizard(None, Project("t"), None, start="dipole")
    dlg._next()
    assert not dlg.sub_box.isVisibleTo(dlg) and "adaptive" in dlg.creates.text()
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


def test_the_wizard_reaches_every_designer_and_the_ribbon_has_no_separate_ones():
    """The patch, hairpin filter and hairpin diplexer designers are only reached through the Design Wizard."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QAbstractButton, QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.design_wizard import DesignWizard
    from hertzon.gui.main_window import MainWindow
    designers = {s.designer for s in ST.CATALOG if s.designer}
    assert designers == {"antenna_patch", "filter_hairpin", "filter_diplexer"}
    dlg = DesignWizard(None, Project("t"), None, start="diplexer")
    assert dlg.next_btn.isEnabled()
    dlg._next()
    assert dlg.designer == "filter_diplexer"
    w = MainWindow()
    assert all(callable(getattr(w, d)) for d in designers)
    texts = {b.text().replace("&", "") for b in w.findChildren(QAbstractButton)}
    texts |= {a.text().replace("&", "") for a in w.findChildren(QAction)}
    assert not {"Filter Design", "Antenna Design", "Tapped Hairpin...", "Hairpin Diplexer...",
                "Rectangular Patch..."} & texts
    assert "Design Wizard" in texts
    w.doc.dirty = False
    w.close()


def test_every_card_has_a_well_formed_picture():
    import xml.etree.ElementTree as ET
    from hertzon.model.structures import CATALOG, icon
    for s in CATALOG:
        pic = icon(s)
        assert pic, f"{s.key} has no picture"
        root = ET.fromstring(pic)
        assert root.get("viewBox") == "0 0 120 80"
