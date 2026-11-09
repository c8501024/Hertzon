"""Hairpin diplexer (Tools > Filter Design > Hairpin Diplexer): two hairpin filters on a T-junction."""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.kernel import occ as K  # noqa: E402
from hertzon.model.design import project_from_dict  # noqa: E402
from hertzon.model.diplexer import (ChannelSpec, DiplexerSpec, diplexer_design, diplexer_goals,  # noqa: E402
                                    diplexer_project, outlines)
from hertzon.model.validate import validate  # noqa: E402


def _db(x):
    return 20 * np.log10(np.abs(x))


def test_diplexer_geometry_builds_and_validates():
    """Two rows of hairpins, the junction and three feeds: every port on y = 0, the common one at the top edge,
    the copper of the junction united with the last hairpin of A and the first of B."""
    p = diplexer_project(DiplexerSpec())
    d, c = p.designs
    assert d.name == "Diplexer_N3_N3" and c.name == "Diplexer_N3_N3_Circuit" and c.kind == "circuit"
    ev = d.evaluator(p)
    K.ensure_gmsh("test_diplexer")
    ctx = K.build_design(d, ev)
    assert not ctx.errors
    for name in ("HairpinA1", "HairpinA3", "HairpinB2", "HairpinB3", "Port1", "Port2", "Port3"):
        assert len(ctx.built[name]) == 1                     # feeds, junction and HairpinB1 united into HairpinA3
    assert not ctx.built.get("HairpinB1") and not ctx.built.get("Junction")
    tess = K.tessellate(ctx)
    assert not [c for c in validate(p, d, tess, ctx.errors) if c.level == "error"]
    ol = outlines(d, p)
    a_right = max(x for x, _ in ol["HairpinA3"])
    b_left = min(x for x, _ in ol["HairpinB1"])
    assert np.isclose(b_left - a_right, ev.length("j_A") * 1e3 + ev.length("j_B") * 1e3 + ev.length("wf") * 1e3)
    # the taps of both rows are on the line of the ports (rows shifted by their tap height)
    for name in ("Feed2", "Feed3", "Junction"):
        ys = [y for _, y in ol[name]]
        assert np.isclose(min(ys) + max(ys), 0, atol=1e-9)
    assert all(c.variables[k] == d.variables[k] for k in d.variables)
    q = project_from_dict(p.to_dict())
    assert q.designs[0].optimetrics[0].props["CoarseModel"] == c.name


def test_diplexer_circuit_separates_the_channels():
    """The circuit of the start design: channel A passes to Port 2 and is blocked at Port 3, channel B the
    other way round; the junction lines start where each filter looks open in the band of the other one."""
    from hertzon.solver.circuit import analyze
    spec = DiplexerSpec()
    p = diplexer_project(spec)
    d, c = p.designs
    f = np.array([2.35, 2.75]) * 1e9
    S = analyze(p, c, f).S
    assert _db(S[0, 1, 0]) > -4 and _db(S[0, 2, 0]) < -20         # A: through to 2, not to 3
    assert _db(S[1, 2, 0]) > -4 and _db(S[1, 1, 0]) < -20         # B: through to 3, not to 2
    assert (_db(S[:, 2, 1]) < -25).all()                          # isolation


def test_diplexer_goals_and_optimizations():
    spec = DiplexerSpec(channels=[ChannelSpec(5, (1.8, 1.9)), ChannelSpec(3, (2.1, 2.2))], isolation=30)
    d, nom = diplexer_design(spec)
    sm, cg = d.optimetrics
    assert sm.name == "DiplexerSM" and sm.props["Optimizer"] == "Space Mapping" and cg.name == "DiplexerCG"
    inc = [v["name"] for v in sm.variables if v["include"]]
    assert inc == ["L_A", "dL_A", "g_A", "g2_A", "yP_A", "j_A", "L_B", "dL_B", "g_B", "yP_B", "j_B"]
    assert sm.goals == diplexer_goals(spec) == cg.goals
    calcs = {(g["calc"], g["start"], g["cond"]) for g in sm.goals}
    assert ("dB(S(2,1))", "1.8GHz", ">=") in calcs and ("dB(S(3,1))", "2.1GHz", ">=") in calcs
    assert ("dB(S(2,1))", "2.1GHz", "<=") in calcs and ("dB(S(3,1))", "1.8GHz", "<=") in calcs   # the other channel
    assert ("dB(S(3,2))", "1.8GHz", "<=") in calcs and ("dB(S(3,2))", "2.1GHz", "<=") in calcs
    for k in range(2):
        lo, hi = nom["ranges"][f"j_{'AB'[k]}"]
        assert lo <= nom["j"][k] <= hi and nom["j"][k] >= nom["jmin"]
    ev = d.evaluator()
    assert ev.length("g2_A") < ev.length("g_A")                    # one gap per pair of neighbours


def test_diplexer_rejects_overlapping_channels():
    with pytest.raises(ValueError, match="overlap"):
        diplexer_design(DiplexerSpec(channels=[ChannelSpec(3, (2.3, 2.5)), ChannelSpec(3, (2.4, 2.6))]))


def test_diplexer_dialog():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hertzon.gui.filter_tool import DiplexerDialog
    from hertzon.model.design import Project
    dlg = DiplexerDialog(None, Project())
    assert dlg.design is not None and dlg.design.name == "Diplexer_N3_N3"
    assert [x.name for x in dlg.extra_designs()] == ["Diplexer_N3_N3_Circuit"]
    dlg.junction.setCurrentIndex(1)                               # common resonator
    dlg.refresh()
    assert dlg.design.name == "Diplexer_N3_N3_R" and "HairpinR0" in {o.name for o in dlg.design.objects}
    assert [x.name for x in dlg.extra_designs()] == ["Diplexer_N3_N3_R_Circuit"]
    dlg.junction.setCurrentIndex(0)
    dlg.third.setChecked(True)                                    # triplexer
    dlg.refresh()
    assert dlg.design.name == "Triplexer_N3_N3_N3" and "Port4" in {o.name for o in dlg.design.objects}
    dlg.third.setChecked(False)
    dlg.ch[1]["lo"].setValue(2.4)                                 # overlaps channel A
    dlg.refresh()
    assert dlg.design is None
    dlg.close()
    del app


def test_common_resonator_diplexer():
    """Junction "resonator": one row A1..A3 R0 B3..B1, R0 tapped by Port 1 from inside its U, the outer ports at
    the tap heights of their channels; much smaller than the T-junction; the circuit has the same variables."""
    from hertzon.solver.circuit import analyze
    p = diplexer_project(DiplexerSpec(junction="resonator"))
    d, c = p.designs
    assert d.name == "Diplexer_N3_N3_R" and c.name == "Diplexer_N3_N3_R_Circuit"
    ev = d.evaluator(p)
    K.ensure_gmsh("test_diplexer_r")
    ctx = K.build_design(d, ev)
    assert not ctx.errors
    names = ["HairpinA1", "HairpinA2", "HairpinA3", "HairpinR0", "HairpinB3", "HairpinB2", "HairpinB1"]
    assert all(len(ctx.built[n]) == 1 for n in names)
    ol = outlines(d, p)
    xs = [(min(x for x, _ in ol[n]), max(x for x, _ in ol[n])) for n in names]
    gaps = [xs[i + 1][0] - xs[i][1] for i in range(6)]
    want = [ev.length(g) * 1e3 for g in ("g_A", "g_A", "g_0A", "g_0B", "g_B", "g_B")]
    assert np.allclose(gaps, want, atol=1e-6)
    area_r = (ev.length("Wl") + ev.length("Wr")) * ev.length("Wy")
    t = diplexer_project(DiplexerSpec())
    et = t.designs[0].evaluator(t)
    area_t = (et.length("Wl") + et.length("Wr")) * et.length("Wy")
    assert area_r < 0.6 * area_t
    sm = d.optimetrics[0]
    inc = [v["name"] for v in sm.variables if v["include"]]
    assert {"L_0", "g_0A", "g_0B", "yP_0", "L_A", "g_A", "yP_B"} <= set(inc) and "j_A" not in inc
    assert all(c.variables[k] == d.variables[k] for k in d.variables)
    S = analyze(p, c, np.array([2.35e9])).S
    assert np.all(np.isfinite(S)) and S.shape == (1, 3, 3)


def test_triplexer_star_junction():
    """Three channels: A left (Port 2), B right (Port 3), C turned downwards (Port 4) on a cross junction; the
    circuit already separates the three bands at the start values; isolation goals for every pair of ports."""
    from hertzon.solver.circuit import analyze
    spec = DiplexerSpec(channels=[ChannelSpec(3, (2.0, 2.15)), ChannelSpec(3, (2.6, 2.75)),
                                  ChannelSpec(3, (2.3, 2.45))])
    p = diplexer_project(spec)
    d, c = p.designs
    assert d.name == "Triplexer_N3_N3_N3" and c.name == "Triplexer_N3_N3_N3_Circuit"
    ev = d.evaluator(p)
    K.ensure_gmsh("test_triplexer")
    ctx = K.build_design(d, ev)
    assert not ctx.errors
    assert all(len(ctx.built[n]) == 1 for n in ("HairpinA3", "HairpinC2", "HairpinC3", "Port4"))
    assert not ctx.built.get("HairpinB1") and not ctx.built.get("HairpinC1")      # united into the junction
    ol = outlines(d, p)
    ys = [y for _, y in ol["HairpinC1"]]
    rows_ab = [y for n in ("HairpinA1", "HairpinA2", "HairpinA3") for _, y in ol[n]]
    assert max(ys) < min(rows_ab)                                 # C hangs below the rows of A and B
    calcs = {(g["calc"], g["start"]) for g in d.optimetrics[0].goals}
    for s in ("dB(S(3,2))", "dB(S(4,2))", "dB(S(4,3))"):
        assert any(cc == s for cc, _ in calcs)
    assert "j_C" in [v["name"] for v in d.optimetrics[0].variables if v["include"]]
    f = np.array([2.075, 2.675, 2.375]) * 1e9
    S = analyze(p, c, f).S
    for k in range(3):
        assert _db(S[k, k + 1, 0]) > -4                            # each channel through to its port
        assert all(_db(S[k, j + 1, 0]) < -20 for j in range(3) if j != k)
    with pytest.raises(ValueError, match="two channels"):
        diplexer_design(DiplexerSpec(channels=spec.channels, junction="resonator"))
