"""Scale bar of the 3D view: measured in the plane of the model centre, units, 1-2-5 lengths, window size and
display scaling. A board of 40 x 40 x 1.6 mm seen from above: a bar of L mm is L/40 of the board width."""
from __future__ import annotations

import math

import numpy as np
import pytest

vtk = pytest.importorskip("vtk")

BOARD = (-0.02, 0.02, -0.02, 0.02, 0.0, 0.0016)        # metres
CENTER = np.array([0.0, 0.0, 0.0008])


def _scene(size=(800, 600), parallel=False, focal_z=None, dist=0.12, dpr=1.0, unit=("mm", 1e-3)):
    from hertzon.gui.viewer import ScaleBar
    src = vtk.vtkCubeSource()
    src.SetBounds(*BOARD)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(src.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    ren = vtk.vtkRenderer()
    rw = vtk.vtkRenderWindow()
    rw.SetOffScreenRendering(1)
    rw.AddRenderer(ren)
    rw.SetSize(*size)
    ren.AddActor(actor)
    cam = ren.GetActiveCamera()
    cam.SetPosition(0, 0, CENTER[2] + dist)
    # the focal point is not necessarily on the model (zoom toward the cursor, pan ...): the scale bar measured
    # there and was about twice too long when it sat at half the distance
    cam.SetFocalPoint(0, 0, CENTER[2] + dist / 2 if focal_z is None else focal_z)
    cam.SetViewUp(0, 1, 0)
    cam.SetParallelProjection(parallel)
    if parallel:
        cam.SetParallelScale(0.03)
    ren.ResetCameraClippingRange()
    sb = ScaleBar(ren, lambda: unit, lambda: CENTER, lambda: dpr)
    rw.Render()
    return ren, rw, cam, sb


def _board_px(ren, z=CENTER[2]):
    """Width of the board in display pixels at the depth z."""
    from hertzon.gui.viewer import world_to_display
    a = world_to_display(ren, (BOARD[0], 0, z))
    b = world_to_display(ren, (BOARD[1], 0, z))
    return abs(b[0] - a[0])


def _check(sb, ren, unit_scale, tol, z=CENTER[2]):
    length_m = sb.length * unit_scale
    expect = _board_px(ren, z) * length_m / 0.04
    assert sb.pixels == pytest.approx(expect, rel=tol)


@pytest.mark.parametrize("unit", [("mm", 1e-3), ("meter", 1.0), ("um", 1e-6)])
def test_orthographic_exact(unit):
    ren, rw, cam, sb = _scene(parallel=True, unit=unit)
    _check(sb, ren, unit[1], 0.02)
    _check(sb, ren, unit[1], 0.02, z=BOARD[5])          # the top face: same scale in an orthographic view
    shown = {"meter": "meter", "mm": "mm", "um": "µm"}[unit[0]]
    assert sb.label() == f"{sb.length:g} ({shown})"


def test_perspective_at_the_model_centre():
    ren, rw, cam, sb = _scene(parallel=False)
    _check(sb, ren, 1e-3, 0.05)
    _check(sb, ren, 1e-3, 0.05, z=BOARD[5])             # the visible top face
    assert sb.label() == f"{sb.length:g} (mm)"


def test_same_label_in_orthographic_and_perspective_without_ca():
    labels = []
    for parallel in (True, False):
        ren, rw, cam, sb = _scene(parallel=parallel)
        labels.append(sb.label())
        assert "ca." not in " ".join(t.GetInput() or "" for t in sb.labels)
        # the numbers are centred on their ticks: 0, half and the end of the bar; the unit follows the end number
        x0 = sb.end_x - sb.pixels
        ticks = [x0, x0 + sb.pixels / 2, sb.end_x]
        for t, x in zip(sb.labels[:3], ticks):
            assert t.GetTextProperty().GetJustificationAsString() == "Centered"
            assert t.GetPosition()[0] == pytest.approx(x, abs=1)
        assert sb.labels[3].GetPosition()[0] > sb.end_x
    assert labels[0] == labels[1]


@pytest.mark.parametrize("focal_z", [CENTER[2] + 0.11, CENTER[2] - 0.2])
def test_perspective_independent_of_the_focal_point(focal_z):
    ren, rw, cam, sb = _scene(parallel=False, focal_z=focal_z)
    _check(sb, ren, 1e-3, 0.05)


@pytest.mark.parametrize("parallel", [True, False])
def test_same_pixels_per_physical_length_in_every_unit(parallel):
    per_m = []
    for unit in (("mm", 1e-3), ("meter", 1.0), ("um", 1e-6)):
        ren, rw, cam, sb = _scene(parallel=parallel, unit=unit)
        per_m.append(sb.pixels / (sb.length * unit[1]))
    assert per_m[1] == pytest.approx(per_m[0], rel=1e-9)
    assert per_m[2] == pytest.approx(per_m[0], rel=1e-9)


@pytest.mark.parametrize("parallel", [True, False])
def test_window_size_and_display_scaling(parallel):
    for size, dpr in (((800, 600), 1.0), ((1400, 500), 1.0), ((1200, 900), 1.5)):
        ren, rw, cam, sb = _scene(size=size, parallel=parallel, dpr=dpr)
        _check(sb, ren, 1e-3, 0.05 if not parallel else 0.02)
        # the bar keeps about the same logical size: TARGET_PX logical pixels at most
        assert sb.pixels <= sb.TARGET_PX * dpr * (1 + 1e-9)
        assert sb.pixels > sb.TARGET_PX * dpr / 2.6


def test_resize_and_zoom_recompute():
    ren, rw, cam, sb = _scene(parallel=False)
    first = sb.pixels / sb.length
    cam.SetPosition(0, 0, CENTER[2] + 0.06)     # half the distance: twice the scale at the model
    cam.SetFocalPoint(0, 0, CENTER[2] + 0.03)
    ren.ResetCameraClippingRange()
    rw.Render()
    assert sb.pixels / sb.length == pytest.approx(2 * first, rel=0.01)
    _check(sb, ren, 1e-3, 0.05)
    rw.SetSize(400, 300)
    rw.Render()
    _check(sb, ren, 1e-3, 0.05)


def test_nice_length_1_2_5():
    from hertzon.gui.viewer import nice_length
    assert [nice_length(x) for x in (1, 1.9, 2, 2.6, 4.9, 5, 9.99, 10, 0.0049, 270)] == \
        pytest.approx([1, 1, 2, 2, 2, 5, 5, 10, 0.002, 200])
    for x in np.geomspace(1e-7, 1e4, 97):
        m = nice_length(x) / 10 ** math.floor(math.log10(nice_length(x)))
        assert round(m, 9) in (1, 2, 5)
