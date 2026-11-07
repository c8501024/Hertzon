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
