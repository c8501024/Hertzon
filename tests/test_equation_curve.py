"""Equation based curve (community request #1): X(_t), Y(_t), Z(_t) sampled from _t start to _t end."""
import math

import gmsh
import numpy as np
import pytest

from hertzon.kernel import occ as K
from hertzon.model.design import Project


def _build(**props):
    p = Project()
    d = p.designs[0]
    d.variables["r"] = "5mm"
    o = d.add_object("CreateEquationCurve", "C", **props)
    K.ensure_gmsh("test_eq_curve")
    ctx = K.build_design(d, d.evaluator(p))
    return d, o, ctx


def test_points_follow_the_equations_with_variables_and_units():
    p = Project()
    d = p.designs[0]
    d.variables["r"] = "5mm"
    P = K.equation_curve_points({"XtFunction": "r*cos(_t)", "YtFunction": "r*sin(_t)", "ZtFunction": "_t",
                                 "tStart": "0", "tEnd": "pi", "NumOfPointsOnCurve": "5"}, d.evaluator(p))
    t = np.linspace(0, math.pi, 5)
    assert np.allclose(P, np.c_[5e-3 * np.cos(t), 5e-3 * np.sin(t), 1e-3 * t])   # plain _t: model units (mm)


def test_open_spline_curve_is_one_line_object():
    d, o, ctx = _build(XtFunction="r*cos(_t)", YtFunction="r*sin(_t)", ZtFunction="0.5*_t", tStart="0",
                       tEnd="4*pi", NumOfPointsOnCurve="40")
    assert not ctx.errors and o.dim == 1
    ents = [e for e in ctx.built["C"]]
    assert ents and all(dim == 1 for dim, _ in ents)
    xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(*ents[0])
    assert xmax == pytest.approx(5e-3, rel=1e-2) and zmax == pytest.approx(0.5e-3 * 4 * math.pi, rel=1e-3)


@pytest.mark.parametrize("ctype", ["Spline", "Polyline"])
def test_closed_covered_curve_is_a_sheet(ctype):
    # a circle that comes back to its start, covered: a disk of area pi r^2
    d, o, ctx = _build(XtFunction="r*cos(_t)", YtFunction="r*sin(_t)", ZtFunction="0", tStart="0",
                       tEnd="2*pi", NumOfPointsOnCurve="73", CurveType=ctype, Closed="True", Covered="True")
    assert not ctx.errors and o.dim == 2
    (dim, tag), = ctx.built["C"]
    assert dim == 2
    assert gmsh.model.occ.getMass(2, tag) == pytest.approx(math.pi * 25e-6, rel=2e-3)


def test_bad_function_names_the_parameter_value():
    d, o, ctx = _build(XtFunction="1/(_t-1)", YtFunction="0", ZtFunction="0", tStart="0", tEnd="2",
                       NumOfPointsOnCurve="3")
    assert ctx.errors and "_t = 1" in str(ctx.errors)


def _ev(**variables):
    p = Project()
    d = p.designs[0]
    d.variables.update(variables)
    return d.evaluator(p)


def test_cylindrical_helix_and_t_alias():
    P = K.equation_curve_points({"CoordinateSystem": "Cylindrical", "XtFunction": "5", "YtFunction": "t",
                                 "ZtFunction": "t", "tStart": "0", "tEnd": "2*pi",
                                 "NumOfPointsOnCurve": "9"}, _ev())
    t = np.linspace(0, 2 * math.pi, 9)
    assert np.allclose(P, np.c_[5e-3 * np.cos(t), 5e-3 * np.sin(t), 1e-3 * t])   # phi without unit: radians


def test_cylindrical_phi_with_degrees():
    P = K.equation_curve_points({"CoordinateSystem": "Cylindrical", "XtFunction": "2mm", "YtFunction": "90deg*t",
                                 "ZtFunction": "0", "tStart": "0", "tEnd": "1", "NumOfPointsOnCurve": "2"}, _ev())
    assert np.allclose(P, [[2e-3, 0, 0], [0, 2e-3, 0]], atol=1e-12)


def test_y_of_x_mode_uses_t_as_x():
    P = K.equation_curve_points({"CoordinateSystem": "Y = f(X)", "XtFunction": "", "YtFunction": "t*t",
                                 "ZtFunction": "0", "tStart": "0", "tEnd": "3", "NumOfPointsOnCurve": "4"}, _ev())
    assert np.allclose(P, 1e-3 * np.array([[0, 0, 0], [1, 1, 0], [2, 4, 0], [3, 9, 0]]))


def test_design_variable_named_t_is_not_overridden():
    P = K.equation_curve_points({"XtFunction": "t", "YtFunction": "_t", "ZtFunction": "0", "tStart": "0",
                                 "tEnd": "1", "NumOfPointsOnCurve": "2"}, _ev(t="7mm"))
    assert np.allclose(P[:, 0], 7e-3) and np.allclose(P[:, 1], [0, 1e-3])


def test_error_names_the_parameter_value():
    with pytest.raises(K.GeometryError, match=r"_t = 10"):
        K.equation_curve_points({"XtFunction": "1/(t-10)", "YtFunction": "0", "ZtFunction": "0", "tStart": "0",
                                 "tEnd": "20", "NumOfPointsOnCurve": "21"}, _ev())


@pytest.mark.parametrize("preset", [name for name, _ in __import__(
    "hertzon.model.equation_presets", fromlist=["EQUATION_PRESETS"]).EQUATION_PRESETS])
def test_every_preset_builds(preset):
    from hertzon.model.equation_presets import EQUATION_PRESETS
    props = dict(EQUATION_PRESETS)[preset]
    d, o, ctx = _build(**props)
    assert not ctx.errors and ctx.built["C"]
    want = 2 if props.get("Closed") == "True" and props.get("Covered") == "True" else 1
    assert all(dim == want for dim, _ in ctx.built["C"])


def test_dialog_preview_presets_and_error(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from hertzon.gui.dialogs import EquationCurveDialog
    from hertzon.gui.doc import Document
    app = QApplication.instance() or QApplication([])
    doc = Document()
    o = doc.design.add_object("CreateEquationCurve", "C")
    dlg = EquationCurveDialog(doc, o)
    dlg._update()
    assert dlg._ok.isEnabled() and "points" in dlg.info.text()
    i = [n for n, _ in __import__("hertzon.model.equation_presets", fromlist=["x"]).EQUATION_PRESETS].index("Helix")
    dlg._apply_preset(i + 1)
    v = dlg.values()
    assert v["CoordinateSystem"] == "Cylindrical" and v["YtFunction"] == "t" and v["Covered"] == "False"
    dlg.f_edits[1].setText("1/(t-10)")
    dlg.t0.setText("0")
    dlg.t1.setText("20")
    dlg.npts.setValue(21)
    dlg._update()
    assert not dlg._ok.isEnabled() and "_t = 10" in dlg.info.text()
    assert dlg.f_edits[1].styleSheet() and not dlg.f_edits[0].styleSheet()
    dlg.f_edits[1].setText("t")
    dlg._update()
    dlg.accept()
    assert dlg.cmd_values["YtFunction"] == "t"
    app.processEvents()
