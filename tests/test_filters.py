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
    p = hairpin_project(HairpinSpec(n=3))
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
