"""A translucent closed surface (the 3D radiation pattern) must blend without a seam under SSAA: VTK's plain
translucent pass draws cells in mesh order, so the side where back cells come after front cells looks
different and a sharp meridian line splits the pattern."""
from __future__ import annotations

import numpy as np
import pyvista as pv
import pytest

viewer = pytest.importorskip("hertzon.gui.viewer")


def _render(fix: bool) -> np.ndarray:
    th = np.radians(np.arange(0, 181, 5.0))
    ph = np.radians(np.arange(0, 361, 10.0))
    T, P = np.meshgrid(th, ph, indexing="ij")
    grid = pv.StructuredGrid(np.sin(T) * np.cos(P), np.sin(T) * np.sin(P), np.cos(T))
    grid.point_data["v"] = np.cos(T).ravel(order="F")      # depends on z only: mirror symmetric in y
    p = pv.Plotter(off_screen=True, window_size=(240, 240))
    p.enable_anti_aliasing("ssaa")
    if fix:
        assert viewer.sorted_translucency(p)
    p.add_mesh(grid, scalars="v", cmap="jet", opacity=0.6, show_scalar_bar=False, lighting=False)
    p.camera_position = [(5, 0, 0), (0, 0, 0), (0, 0, 1)]   # looking along -x: y is left/right in the image
    img = p.screenshot().astype(float)
    p.close()
    return img


def _asymmetry(img: np.ndarray) -> float:
    return float(np.abs(img - img[:, ::-1]).mean())


def test_translucent_pattern_has_no_seam():
    try:
        before = _render(False)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no off-screen rendering here: {e}")
    after = _render(True)
    assert _asymmetry(after) < 1.0                       # the same on both sides of the view
    assert _asymmetry(before) > _asymmetry(after)        # the plain pass shows the seam this guards against
