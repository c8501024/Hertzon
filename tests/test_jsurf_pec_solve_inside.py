"""Jsurf on a perfect conductor whose Solve Inside is on: the solver treats it as PEC all the same (no field
inside), so the plot must take the faces from the outside. It took them from the inside and showed 0 A/m
everywhere (VDipole_1GHz_mesh_repair.hzn: PEC objects made by a script with Solve Inside left on)."""
from __future__ import annotations

import types

import numpy as np
import pytest
import pyvista as pv

from hertzon.gui import fields as F
from hertzon.model.materials import Material, surface_conductor
from hertzon.solver.results import ResultStore

H_AIR = 2.0                     # |H| outside the metal block (A/m)


def test_surface_conductor():
    pec, cu, air = Material("pec", pec=True), Material("copper", conductivity="5.8e7"), Material("vacuum")
    assert surface_conductor(pec, True) and surface_conductor(pec, False)
    assert surface_conductor(cu, False) and not surface_conductor(cu, True)
    assert not surface_conductor(air, False) and not surface_conductor(air, True)


@pytest.mark.parametrize("solve_inside", [False, True])
def test_jsurf_on_pec_with_solve_inside(tmp_path, monkeypatch, solve_inside):
    cube = pv.ImageData(dimensions=(9, 9, 9), spacing=(0.25e-3,) * 3).triangulate()
    nodes = np.asarray(cube.points, float)
    tets = np.asarray(cube.cells_dict[pv.CellType.TETRA], np.int64)
    c = nodes[tets].mean(axis=1)
    metal = (np.abs(c - 1e-3) < 0.5e-3).all(axis=1)
    H = np.repeat(np.where(metal[:, None], 0.0, [[H_AIR, 0.0, 0.0]]), 4, axis=0).astype(complex)
    st = ResultStore(tmp_path)
    st.save_npz("mesh.npz", nodes=nodes, tets=tets, tet_obj=metal.astype(np.int64),
                obj_names=np.array(["Air", "Metal"]))
    st.save_npz("fields_LastAdaptive.npz", tet4=np.array([1]), freq_0=np.array([1e9]), E_0_1=H * 377, H_0_1=H)
    monkeypatch.setattr(F, "store_for", lambda doc, solution: (ResultStore(tmp_path), "LastAdaptive"))
    monkeypatch.setattr(F, "source_vector", lambda doc, st, fields, grid, which, fi, port:
                        fields[f"{which}_{fi}_{port}"].astype(np.complex128))
    F._cache.clear()
    ob = types.SimpleNamespace(name="Metal", material="pec", solve_inside=solve_inside, model=True, dim=3)
    mats = {"pec": Material("pec", pec=True)}
    doc = types.SimpleNamespace(
        design=types.SimpleNamespace(units="mm", obj=lambda n: ob, has_obj=lambda n: n == "Metal"),
        project=types.SimpleNamespace(material=lambda n: mats[n]))
    box = pv.Box(bounds=(0.5e-3, 1.5e-3) * 3).triangulate()
    box.cell_data["face"] = np.zeros(box.n_cells, np.int64)
    view = types.SimpleNamespace(actors={"Metal": types.SimpleNamespace(mesh=box, edge_mesh=None)})
    meshes, info = F.make_overlay(doc, view, "Mag_Jsurf", "Setup1 : LastAdaptive", objects=["Metal"],
                                  attrs={"PlotType": "Flat Shaded"})
    s = meshes[0][0]
    J = np.asarray(s.point_data["Mag_Jsurf"])
    assert s.n_cells and "failed" not in info
    # |n x H| = H_AIR on the four faces parallel to H, 0 on the two faces normal to it
    assert J.max() == pytest.approx(H_AIR, rel=1e-6)
    assert np.mean(J > 0.5 * H_AIR) > 0.5
