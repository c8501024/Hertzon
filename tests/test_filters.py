import copy
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.kernel import occ as K  # noqa: E402
from hertzon.model.design import Project, project_from_dict  # noqa: E402
from hertzon.model.filters import HairpinSpec, hairpin_project, nominal, outlines  # noqa: E402
from hertzon.model.microstrip import analyze, synthesize  # noqa: E402
from hertzon.model.validate import validate  # noqa: E402
from hertzon.solver.optimetrics import Variables  # noqa: E402


@pytest.mark.parametrize("builtin", [True, False])
def test_microstrip_50_ohm_on_ro4350b(builtin):
    if not builtin:
        pytest.importorskip("hfsynpy")
    w = synthesize(50, 0.508e-3, 3.66, 35e-6, 2.45e9, 0.0037, builtin=builtin)
    assert abs(w - 1.0814e-3) < 0.02e-3                   # hfsynpy (filterbench): 1.0814 mm
    z, e = analyze(w, 0.508e-3, 3.66, 35e-6, 2.45e9, 0.0037, builtin=builtin)
    assert np.isclose(z, 50, atol=0.05) and 2.7 < e < 2.9


@pytest.mark.parametrize("n", [2, 3, 5])
def test_hairpin_builds_and_validates(n):
    p = hairpin_project(HairpinSpec(n=n))
    d = p.designs[0]
    K.ensure_gmsh(f"test_hairpin{n}")
    ctx = K.build_design(d, d.evaluator(p))
    assert not ctx.errors
    for i in range(1, n + 1):
        assert len(ctx.built[f"Hairpin{i}"]) == 1             # the feeds are united into one sheet
    tess = K.tessellate(ctx)
    assert not [c for c in validate(p, d, tess, ctx.errors) if c.level == "error"]
    ol = outlines(d, p)
    assert len(ol) == n + 2 and all(len(ol[f"Hairpin{i}"]) == 10 for i in range(1, n + 1))
    xs = [x for pts in ol.values() for x, _ in pts]
    assert max(xs) <= 11.5 and min(xs) >= -11.5               # on the 23 mm board


def test_hairpin_optimization_matches_filterbench():
    p = hairpin_project(HairpinSpec(n=3, synthesis=False))
    d = p.designs[0]
    op = d.optimetrics[0]
    assert op.solution == "Setup1 : Sweep" and len(op.goals) == 10
    v = Variables(d, op, d.evaluator(p))
    assert v.names == ["L", "w", "b", "g", "yP", "M"]
    x0 = dict(zip(v.names, v.x0()))
    assert all(np.isclose(x0[k], 0.5, atol=1e-3) for k in ("w", "b", "g", "yP", "M"))
    assert 0 < x0["L"] < 1
    q = project_from_dict(p.to_dict())
    assert q.designs[0].optimetrics[0].variables == op.variables


def test_hairpin_derivative_trust_region_optimization():
    """The second optimization has the same goals and ranges, with the derivative trust region on L, g, yP."""
    p = hairpin_project(HairpinSpec(n=3))
    d = p.designs[0]
    cg, dtr, _ = d.optimetrics
    assert dtr.name == "HairpinDTR" and dtr.props["Optimizer"] == "Derivative Trust Region"
    assert dtr.solution == cg.solution and dtr.goals == cg.goals and dtr.goals is not cg.goals
    assert [(v["name"], v["min"], v["max"]) for v in dtr.variables] == [(v["name"], v["min"], v["max"]) for v in cg.variables]
    v = Variables(d, dtr, d.evaluator(p))
    assert v.names == ["L", "dL", "g", "yP"]
    assert all(cg.props[k] == dtr.props[k] for k in cg.props if k != "Optimizer")


def test_hairpin_start_from_the_chebyshev_prototype():
    """Gap and tap from the coupling and external Q of the prototype (return loss 10 + 3 dB). A derivative
    trust region run from the middle of the ranges ended at g = 0.372 mm, yP = -5.37 mm (cost 6.83 -> 0.64);
    the synthesis starts within 0.02 mm and 0.2 mm of that."""
    from hertzon.model.filters import chebyshev_g, nominal
    g = chebyshev_g(3, 0.5)                                   # textbook table, 0.5 dB ripple
    assert np.allclose(g, [1.0, 1.5963, 1.0967, 1.5963, 1.0], atol=2e-4)
    assert np.allclose(chebyshev_g(2, 0.5)[-1], 1.9841, atol=2e-4)
    nom = nominal(HairpinSpec(n=3))
    assert abs(nom["gap"] - 0.372) < 0.02 and abs(nom["yP"] + 5.37) < 0.2
    assert np.isclose(nom["prototype"]["Qe"], 10.32, atol=0.01)
    d = hairpin_project(HairpinSpec(n=3)).designs[0]
    assert d.variables["g"] == f"{nom['gap']:g}mm" and d.variables["yP"] == f"{nom['yP']:g}mm"
    mid = nominal(HairpinSpec(n=3, synthesis=False))
    assert mid["gap"] == 0.45 and mid["yP"] == 0.0


def test_coupled_microstrip_against_the_charts():
    from hertzon.model.microstrip import coupled_lines
    ze, zo, _, _ = coupled_lines(1e-3, 0.5e-3, 1e-3, 10.0)  # w/h = 1, s/h = 0.5, er = 10: about 60 / 38 ohm
    assert abs(ze - 60) < 2 and abs(zo - 38) < 2
    zs = [coupled_lines(1e-3, s * 1e-3, 0.5e-3, 3.66)[:2] for s in (0.1, 0.3, 1.0, 5.0)]
    assert all(a[0] > b[0] and a[1] < b[1] for a, b in zip(zs, zs[1:]))   # coupling falls with the gap
    z1 = __import__("hertzon.model.microstrip", fromlist=["analyze"]).analyze(1e-3, 0.5e-3, 3.66, builtin=True)[0]
    assert abs(zs[-1][0] - z1) < 0.03 * z1 and abs(zs[-1][1] - z1) < 0.03 * z1   # far apart: single lines


def test_auto_bands():
    from hertzon.model.filters import auto_bands
    assert auto_bands((2.3, 2.6)) == ([(2.0, 2.2), (2.7, 2.9)], (2.0, 2.9, 0.01))


def test_optimization_dialog_apply():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hertzon.gui.doc import Document
    from hertzon.gui.optimetrics import OptimizationDialog
    doc = Document(hairpin_project(HairpinSpec(n=2)))
    op = doc.design.optimetrics[0]
    seen = []
    dlg = OptimizationDialog(None, doc, op, on_apply=seen.append)
    dlg.name.setText("Renamed")
    dlg._apply()
    assert seen == [op] and op.name == "Renamed" and dlg.isVisible() is False
    dlg.close()
    work = copy.deepcopy(op)                                   # Edit: a working copy keeps its own name
    dlg = OptimizationDialog(None, doc, work, on_apply=seen.append, original=op)
    dlg._apply()
    assert seen[-1] is work and work.name == "Renamed"
    dlg.close()
    del app


def test_hairpin_too_many_resonators():
    with pytest.raises(ValueError):
        nominal(HairpinSpec(n=9))


def test_hairpin_dialog():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hertzon.gui.filter_tool import HairpinFilterDialog
    dlg = HairpinFilterDialog(None, Project())
    assert dlg.design is not None and dlg.design.name == "Hairpin_N3"
    dlg.n.setValue(2)
    dlg.refresh()
    assert len([o for o in dlg.design.objects if o.name.startswith("Hairpin")]) == 2
    assert dlg.f_lo.value() == 2.0 and dlg.f_hi.value() == 2.9 and dlg.sb.text() == "2-2.2, 2.7-2.9"
    dlg.auto.setChecked(False)
    dlg.f_lo.setValue(2.1)                                     # sweep no longer covers the stopband
    dlg.refresh()
    assert dlg.design is None
    dlg.close()
    del app


def test_patch_synthesis_and_build():
    from hertzon.model.antennas import PatchSpec, nominal as patch_nominal, patch_project
    n = patch_nominal(PatchSpec())
    assert abs(n["Wp"] - 37.26) < 0.1 and abs(n["Lp"] - 28.8) < 0.3     # Balanis: 2.45 GHz on FR4, 1.6 mm
    assert 0 < n["inset"] < n["Lp"] / 2
    p = patch_project(PatchSpec())
    d = p.designs[0]
    K.ensure_gmsh("test_patch_tool")
    ctx = K.build_design(d, d.evaluator(p))
    assert not ctx.errors and len(ctx.built["Patch"]) == 1
    tess = K.tessellate(ctx)
    assert not [c for c in validate(p, d, tess, ctx.errors) if c.level == "error"]
    v = Variables(d, d.optimetrics[0], d.evaluator(p))
    op = d.optimetrics[0]
    assert op.props["Optimizer"] == "Derivative Trust Region" and len(v) <= 6
    # one frequency on the adaptive solution: no sweep is solved while optimizing
    assert op.solution == "Setup1 : LastAdaptive" and len(op.goals) == 1
    assert op.goals[0]["start"] == op.goals[0]["stop"] == "2.45GHz" and op.goals[0]["value"] == "-20"
    assert v.names == ["Lp", "inset"] and np.allclose(v.x0(), [0.5, 0.5], atol=0.01)
    # the sweep is there to look at the resonance afterwards: off, so that it does not enlarge the open region
    # of the design beyond the one of the (setup only) optimization
    assert [sw.props["Enabled"] for sw in d.setups[0].sweeps] == ["False"]


def test_patch_dialog():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hertzon.gui.antenna_tool import PatchAntennaDialog
    dlg = PatchAntennaDialog(None, Project())
    assert dlg.design is not None and dlg.design.name == "Patch_2.45GHz"
    dlg.f0.setValue(5.8)
    dlg.refresh()
    assert dlg.design.name == "Patch_5.8GHz" and dlg.design.spheres
    dlg.close()
    del app


def test_hairpin_circuit_model_and_space_mapping():
    """The project has the filter as a circuit with the same variables, the coarse model of HairpinSM; the
    circuit has the passband of the 3D design (2.45 GHz centre, 3 resonators)."""
    from hertzon.solver.circuit import analyze
    p = hairpin_project(HairpinSpec(n=3))
    d, c = p.designs
    sm = d.optimetrics[2]
    assert sm.name == "HairpinSM" and sm.props["Optimizer"] == "Space Mapping"
    assert sm.props["CoarseModel"] == c.name == "Hairpin_N3_Circuit" and c.kind == "circuit"
    assert [v["name"] for v in sm.variables if v["include"]] == ["L", "dL", "g", "yP"] and sm.goals == d.optimetrics[0].goals
    assert all(c.variables[k] == d.variables[k] for k in d.variables)
    f = np.array([2.0, 2.45, 2.9]) * 1e9
    S = analyze(p, c, f).S
    db = 20 * np.log10(np.abs(S[:, 1, 0]))
    assert db[1] > -2 and db[0] < -20 and db[2] < -20              # passband in the middle, stopbands outside
    q = project_from_dict(p.to_dict())
    assert q.designs[1].components and q.designs[0].optimetrics[2].props["CoarseModel"] == c.name


def test_one_gap_per_pair_of_neighbours():
    """Symmetric filters: g in the middle, g2, g3 ... outwards, each from its own coupling of the prototype;
    the hairpins sit at those gaps, the circuit uses them and the optimizations include them."""
    from hertzon.model.filters import gap_names
    assert gap_names(3) == ["g", "g"] and gap_names(4) == ["g2", "g", "g2"]
    assert gap_names(5) == ["g2", "g", "g", "g2"] and gap_names(6) == ["g3", "g2", "g", "g2", "g3"]
    spec = HairpinSpec(n=5, region=32.5, board=38.5)
    p = hairpin_project(spec)
    d, c = p.designs
    ev = d.evaluator(p)
    g, g2 = ev.length("g") * 1e3, ev.length("g2") * 1e3
    assert g2 < g                        # the outer pairs couple more (Chebyshev k12 > k23)
    ol = outlines(d, p)
    # inner edges of neighbouring hairpins: the gaps of the row, symmetric
    xs = [(min(x for x, _ in ol[f"Hairpin{i}"]), max(x for x, _ in ol[f"Hairpin{i}"])) for i in range(1, 6)]
    gaps = [xs[i + 1][0] - xs[i][1] for i in range(4)]
    assert np.allclose(gaps, [g2, g, g, g2], atol=1e-6)
    assert np.isclose(xs[0][0], -xs[-1][1])                       # centred row
    assert [x.props["S"] for x in c.components if x.type == "MCLIN"] == ["g2", "g", "g", "g2"]
    for o in d.optimetrics:
        names = [v["name"] for v in o.variables if v["include"]]
        assert "g2" in names and "g" in names
