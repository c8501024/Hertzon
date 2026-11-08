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
    dlg.ch[1]["lo"].setValue(2.4)                                 # overlaps channel A
    dlg.refresh()
    assert dlg.design is None
    dlg.close()
    del app
