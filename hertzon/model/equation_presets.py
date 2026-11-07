"""Starting points for the Equation Based Curve dialog: (name, command properties). Plain numbers are model
units, the parameter is t (the same as _t)."""
from __future__ import annotations

EQUATION_PRESETS: list[tuple[str, dict[str, str]]] = [
    ("Circle", dict(CoordinateSystem="Cartesian", XtFunction="10*cos(t)", YtFunction="10*sin(t)", ZtFunction="0",
                    tStart="0", tEnd="2*pi", NumOfPointsOnCurve="73", Closed="True", Covered="False")),
    ("Arc (90 deg)", dict(CoordinateSystem="Cartesian", XtFunction="10*cos(t)", YtFunction="10*sin(t)",
                          ZtFunction="0", tStart="0", tEnd="pi/2", NumOfPointsOnCurve="25", Closed="False")),
    ("Ellipse", dict(CoordinateSystem="Cartesian", XtFunction="10*cos(t)", YtFunction="5*sin(t)", ZtFunction="0",
                     tStart="0", tEnd="2*pi", NumOfPointsOnCurve="73", Closed="True", Covered="False")),
    ("Helix", dict(CoordinateSystem="Cylindrical", XtFunction="5", YtFunction="t", ZtFunction="2*t/(2*pi)",
                   tStart="0", tEnd="6*pi", NumOfPointsOnCurve="150", Closed="False")),
    ("Archimedean spiral", dict(CoordinateSystem="Cylindrical", XtFunction="1 + 1*t/(2*pi)", YtFunction="t",
                                ZtFunction="0", tStart="0", tEnd="8*pi", NumOfPointsOnCurve="200", Closed="False")),
    ("Exponential taper", dict(CoordinateSystem="Y = f(X)", XtFunction="", YtFunction="0.5*exp(0.15*t)",
                               ZtFunction="0", tStart="0", tEnd="20", NumOfPointsOnCurve="60", Closed="False")),
    ("Sine meander", dict(CoordinateSystem="Y = f(X)", XtFunction="", YtFunction="2*sin(2*pi*t/5)", ZtFunction="0",
                          tStart="0", tEnd="20", NumOfPointsOnCurve="120", Closed="False")),
]

# what the three function fields mean in each coordinate system: (label, enabled)
FIELD_LABELS: dict[str, tuple[tuple[str, bool], ...]] = {
    "Cartesian": (("X(t)", True), ("Y(t)", True), ("Z(t)", True)),
    "Cylindrical": (("r(t)", True), ("phi(t)", True), ("z(t)", True)),
    "Y = f(X)": (("X = t", False), ("Y(t)", True), ("Z(t)", True)),
}
