"""An idle window does not render again and again, and the central area does not stop the docks from being
widened (reports for 0.3.1: about 4 % CPU with an empty project; Project Manager, part library and schematic
could not be widened enough)."""
import os

import pytest


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_scene_stamp_changes_only_with_the_scene():
    import pyvista as pv
    from hertzon.gui.viewer import scene_stamp
    p = pv.Plotter(off_screen=True)
    mesh = pv.Sphere()
    act = p.add_mesh(mesh, color="red")
    s0 = scene_stamp(p)
    assert scene_stamp(p) == s0                          # nothing changed: nothing to draw
    act.prop.color = "blue"
    s1 = scene_stamp(p)
    assert s1 != s0
    p.camera.azimuth = 30
    s2 = scene_stamp(p)
    assert s2 != s1
    mesh.points[:] *= 2
    mesh.Modified()
    s3 = scene_stamp(p)
    assert s3 != s2
    act.visibility = False
    assert scene_stamp(p) != s3
    p.close()


def test_auto_update_renders_only_after_a_change():
    _app()
    from pyvistaqt import QtInteractor
    from hertzon.gui.viewer import render_on_change
    p = QtInteractor()
    calls = []
    p._auto_render = lambda: calls.append(1)             # what pyvistaqt's timer would call
    p.render_timer.timeout.disconnect()
    p.render_timer.timeout.connect(p._auto_render)
    render_on_change(p)
    for _ in range(5):
        p.render_timer.timeout.emit()
    assert len(calls) == 1                               # the first tick draws, idle ticks do not
    p.camera.azimuth = 10
    p.render_timer.timeout.emit()
    p.render_timer.timeout.emit()
    assert len(calls) == 2
    p.close()


def test_central_area_lets_the_docks_be_widened():
    _app()
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    try:
        # the 3D modeler and the circuit editor share the central area: neither may need much width
        assert w.tabs.minimumSizeHint().width() < 400
        assert w.circuit.minimumSizeHint().width() < 400
    finally:
        w.close()
