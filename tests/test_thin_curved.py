"""Element sizes for thin curved walls and narrow flat rings (hertzon.solver.thin_curved)."""
import math

import gmsh
import pytest

from hertzon.solver import thin_curved


@pytest.fixture
def occ():
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("t")
    yield gmsh.model.occ
    gmsh.finalize()


def _cup(occ, r_in, r_out, h=72.0, floor=1.0):
    """A sleeve: a cup open at the top (z = 0) with wall r_in..r_out."""
    outer = occ.addCylinder(0, 0, -h, 0, 0, h, r_out)
    inner = occ.addCylinder(0, 0, -h + floor, 0, 0, h, r_in)
    occ.cut([(3, outer)], [(3, inner)])
    occ.synchronize()


def _faces_at(radius):
    out = []
    for _, f in gmsh.model.getEntities(2):
        if gmsh.model.getType(2, f) != "Cylinder":
            continue
        x0, y0, _, x1, y1, _ = gmsh.model.getBoundingBox(2, f)
        if abs((x1 - x0) / 2 - radius) < 1e-3:
            out.append(f)
    return out


def test_thin_sleeve_gets_short_elements_and_rim_segments(occ):
    _cup(occ, 12.0, 13.0)
    faces, curves = thin_curved.sizes(10)
    # walls 1 mm apart: facets within t/3 of the true surface, h = sqrt(8 R t / 3) (the curvature size is 7.5 mm)
    (f12,), (f13,) = _faces_at(12.0), _faces_at(13.0)
    assert faces[f12] == pytest.approx(math.sqrt(8 * 12 / 3), rel=0.02)
    assert faces[f13] == pytest.approx(math.sqrt(8 * 13 / 3), rel=0.02)
    # the flat 1 mm rim: its two circles get segments of sqrt(R w)
    assert sorted(curves.values()) == pytest.approx([math.sqrt(12), math.sqrt(13)], rel=0.02)


def test_thick_wall_keeps_curvature_sizing(occ):
    _cup(occ, 12.0, 20.0, floor=8.0)
    faces, curves = thin_curved.sizes(10)
    assert faces == {} and curves == {}


def test_flat_model_untouched(occ):
    occ.addBox(0, 0, 0, 30, 20, 0.5)
    occ.addBox(5, 5, 0.5, 10, 2, 0.035)
    occ.synchronize()
    assert thin_curved.sizes(10) == ({}, {})
