"""Mouse-wheel zoom in the 3D view: it closes in on the object under the cursor and the scale bar follows."""
from __future__ import annotations

import types

import numpy as np
import pytest

vtk = pytest.importorskip("vtk")


def _scene():
    """A board 10 cm x 2 cm seen from 30 cm above, with the focal plane 20 cm below it (as after a rotate or
    fit on a larger model): zooming toward a point of that plane used to miss the board."""
    from hertzon.gui.viewer import ModelerView, keep_focal_plane_in_view
    src = vtk.vtkCubeSource()
    src.SetBounds(-0.05, 0.05, -0.01, 0.01, -0.001, 0.0)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(src.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    ren = vtk.vtkRenderer()
    rw = vtk.vtkRenderWindow()
    rw.SetOffScreenRendering(1)
    rw.AddRenderer(ren)
    rw.SetSize(800, 600)
    ren.AddActor(actor)
    cam = ren.GetActiveCamera()
    cam.SetPosition(0, 0, 0.3)
    cam.SetFocalPoint(0, 0, -0.2)
    ren.ResetCameraClippingRange()
    ren.AddObserver("ResetCameraClippingRangeEvent", lambda *a: keep_focal_plane_in_view(ren))
    rw.Render()

    def surface(pos):
        pk = vtk.vtkCellPicker()
        pk.SetTolerance(0.0005)
        return np.array(pk.GetPickPosition()) if pk.Pick(pos[0], pos[1], 0, ren) and pk.GetActor() else None

    v = types.SimpleNamespace(plotter=types.SimpleNamespace(renderer=ren), draw=None, request_render=lambda: None,
                              view_bbox=lambda: (-0.05, -0.01, -0.001, 0.05, 0.01, 0.0), _surface_point=surface,
                              MIN_ZOOM_DISTANCE=ModelerView.MIN_ZOOM_DISTANCE,
                              MAX_ZOOM_DISTANCE=ModelerView.MAX_ZOOM_DISTANCE, _limit_zoom=ModelerView._limit_zoom)
    for name in ("_focal_plane_point", "_zoom_limits"):
        setattr(v, name, getattr(ModelerView, name).__get__(v))
    return v, ren, rw, cam, surface


def test_wheel_zoom_closes_in_on_the_surface_under_the_cursor():
    from hertzon.gui.viewer import ModelerView
    v, _ren, rw, cam, surface = _scene()
    pos = (500, 310)
    target = surface(pos)
    assert target is not None
    for _ in range(300):
        ModelerView.zoom_at_cursor(v, 1.2, pos)
    rw.Render()
    # the camera reached the board (not a point of empty space) and stopped at 1e-4 of the model size
    gap = cam.GetPosition()[2] - target[2]
    size = np.linalg.norm([0.1, 0.02, 0.001])
    assert gap == pytest.approx(1e-4 * size, rel=1e-6)
    # the focal point lies on the board, so the scale bar (focal distance) measures there
    assert cam.GetDistance() == pytest.approx(gap, rel=1e-6)
    # the zoomed-to point stays under the cursor and the board is not clipped away
    assert np.allclose(surface(pos), target, atol=1e-7)
    near, far = cam.GetClippingRange()
    assert near < gap < far
    for _ in range(300):
        ModelerView.zoom_at_cursor(v, 1 / 1.2, pos)
    assert np.isfinite(cam.GetDistance()) and cam.GetDistance() <= 1e4 * size * (1 + 1e-9)


def test_focal_plane_point_matches_vtk():
    """The cursor point on the focal plane, from the camera alone, equals VTK's display-to-world mapping."""
    from hertzon.gui.viewer import focal_plane_point
    ren = vtk.vtkRenderer()
    rw = vtk.vtkRenderWindow()
    rw.SetOffScreenRendering(1)
    rw.AddRenderer(ren)
    rw.SetSize(800, 600)
    cam = ren.GetActiveCamera()
    cam.SetPosition(0.1, -0.2, 0.3)
    cam.SetFocalPoint(0.02, 0.01, -0.05)
    cam.SetViewUp(0.3, 1, 0.2)
    cam.OrthogonalizeViewUp()
    cam.SetParallelScale(0.07)
    ren.ResetCameraClippingRange((-0.05, 0.05, -0.05, 0.05, -0.05, 0.05))
    for parallel in (False, True):
        cam.SetParallelProjection(parallel)
        fp = np.array(cam.GetFocalPoint())
        ren.SetWorldPoint(*fp, 1.0)
        ren.WorldToDisplay()
        zf = ren.GetDisplayPoint()[2]
        for pos in ((500, 350), (10, 590), (400, 300)):
            ren.SetDisplayPoint(pos[0], pos[1], zf)
            ren.DisplayToWorld()
            ref = np.array(ren.GetWorldPoint()[:3])
            got = focal_plane_point(cam, ren.GetSize(), ren.GetOrigin(), fp, pos)
            assert np.allclose(got, ref, atol=1e-12)
