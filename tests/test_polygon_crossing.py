"""A covered polyline whose outline crosses itself is rejected with a clear message. OpenCASCADE builds a face
from it without complaint, but mapping the boundaries of the model then ran for 50 minutes and more (mesher and
window hung, meta benchmark run G)."""
import numpy as np

from hertzon.kernel import occ as K
from hertzon.model.design import Project

# antenna outline of run G: the edge x = 1.8 crosses the edge y = 5.15
CROSSING = ("10.6,3.6;11.2,3.6;11.2,6.555;1.5,6.555;1.5,6.055;8.6,6.055;8.6,5.65;1.8,5.65;1.8,4.74;-2.5,4.74;"
            "-2.5,3.95;8.0,3.95;8.0,3.6;-3.0,3.6;-3.0,4.25;1.3,4.25;1.3,5.15;8.6,5.15;8.6,4.2;9.4,4.2;9.4,6.055;"
            "10.6,6.055")
# the same kind of meander without the crossing
SIMPLE = ("-3.0,3.6;-2.2,3.6;-2.2,6.055;-1.4,6.055;-1.4,4.2;-0.6,4.2;-0.6,6.055;11.5,6.055;11.5,5.7;1.5,5.7;1.5,4.35;"
          "8.5,4.35;8.5,4.85;2.0,4.85;2.0,5.2;12.0,5.2;12.0,6.555;-3.0,6.555")


def _pts(text, z=0.0):
    return [tuple(float(v) for v in q.split(",")) + (z,) for q in text.split(";")]


def test_crossing_edges_are_found():
    assert K.polygon_crossing(_pts(CROSSING)) == (7, 16)
    assert K.polygon_crossing(_pts(SIMPLE)) is None
    assert K.polygon_crossing([(0, 0, 0), (1, 1, 0), (1, 0, 0), (0, 1, 0)]) == (0, 2)       # bow tie
    assert K.polygon_crossing([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]) is None
    # a vertex touching another edge, and an edge running back over the previous one
    assert K.polygon_crossing([(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 0, 0), (0, 1, 0)]) is not None
    assert K.polygon_crossing([(0, 0, 0), (2, 0, 0), (2, 1, 0), (3, 1, 0), (1, 1, 0), (0, 1, 0)]) is not None


def test_tilted_and_fine_polygons():
    t = np.linspace(0, 2 * np.pi, 4000, endpoint=False)
    ring = np.c_[np.cos(t), np.sin(t), 0.3 * np.cos(t)]           # a fine circle in a tilted plane
    assert K.polygon_crossing(ring) is None
    assert K.polygon_crossing(np.vstack([ring, ring[:1]])) is None  # closed by repeating the first point
    eight = np.c_[np.sin(t), np.sin(t) * np.cos(t), 0 * t]         # a figure eight
    assert K.polygon_crossing(eight) is not None


def test_a_crossing_polyline_is_a_geometry_error_not_a_hang():
    p = Project()
    d = p.designs[0]
    d.add_object("CreatePolyline", name="Ant", Points=";".join(f"{q},0" for q in CROSSING.split(";")),
                 Closed="True", Covered="True")
    d.add_object("CreatePolyline", name="Ok", Points=";".join(f"{q},1" for q in SIMPLE.split(";")),
                 Closed="True", Covered="True")
    # an open polyline may cross itself (a path, not a face)
    d.add_object("CreatePolyline", name="Path", Points="0,0,2;1,1,2;1,0,2;0,1,2", Closed="False")
    K.ensure_gmsh("test_crossing")
    ctx = K.build_design(d, d.evaluator(p))
    assert set(ctx.errors) == {"Ant"}
    msg = ctx.errors["Ant"]
    assert "crosses itself" in msg and "1.8,5.65,0" in msg and "8.6,5.15,0" in msg
