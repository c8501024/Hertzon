"""Solve Inside of conductors. The solver ignores it: PEC and every conductivity above 1e4 S/m are solved by the
surface (PEC or surface impedance) and their inside is removed. The Jsurf plot followed the flag, took the
faces from the inside of a PEC with Solve Inside on and showed 0 A/m everywhere (VDipole_1GHz_mesh_repair.hzn,
PEC objects made by a script). Plots and the field calculator now use the solver's rule, and the flag is off
and locked for conductors."""
from __future__ import annotations

import types

import numpy as np
import pytest
import pyvista as pv

from hertzon.gui import fields as F
from hertzon.model.materials import SURFACE_CONDUCTOR_LIMIT, Material, is_conductor
from hertzon.solver.results import ResultStore

H_AIR = 2.0                     # |H| outside the metal block (A/m)


def test_conductor_rule_is_the_solvers():
    from emerge._emerge.settings import Settings
    assert SURFACE_CONDUCTOR_LIMIT == Settings().mw_3d_surfimplim
    assert is_conductor(Material("pec", pec=True)) and is_conductor(Material("copper", conductivity="5.8e7"))
    assert is_conductor(Material("graphite", conductivity="7e4"))          # surface impedance in the solver
    assert not is_conductor(Material("lossy", conductivity="1e4")) and not is_conductor(Material("vacuum"))


def test_mcp_conductor_has_no_solve_inside():
    from hertzon.mcp.tools import _apply_attrs
    from hertzon.model.design import Project
    p = Project()
    d = p.designs[0]
    o = d.add_object("CreateBox", material="pec")
    assert o.solve_inside                                   # the model default
    _apply_attrs(o, {"solve_inside": True}, d, p)
    assert not o.solve_inside
    o2 = d.add_object("CreateBox", material="vacuum")
    _apply_attrs(o2, {}, d, p)
    assert o2.solve_inside


@pytest.mark.parametrize("material,solve_inside", [("pec", False), ("pec", True), ("copper", True)])
def test_jsurf_on_conductor_with_solve_inside(tmp_path, monkeypatch, material, solve_inside):
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
    ob = types.SimpleNamespace(name="Metal", material=material, solve_inside=solve_inside, model=True, dim=3)
    mats = {"pec": Material("pec", pec=True), "copper": Material("copper", conductivity="5.8e7")}
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


def test_properties_panel_locks_solve_inside_of_conductors():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox
    QApplication.instance() or QApplication([])
    from hertzon.gui.doc import Document
    from hertzon.gui.panels import PropertiesPanel
    from hertzon.model.design import Project
    p = Project()
    d = p.designs[0]
    metal = d.add_object("CreateBox", name="Metal", material="pec")       # Solve Inside left on, as a script does
    d.add_object("CreateBox", name="Sub", material="vacuum")
    doc = Document(p)
    panel = PropertiesPanel(doc)

    def solve_inside_box(name):
        panel.show_object(name)
        g = next(panel.tabs.widget(i) for i in range(panel.tabs.count()) if panel.tabs.tabText(i) == "Attribute")
        t = g.table
        row = next(r for r in range(t.rowCount()) if t.item(r, 0) and t.item(r, 0).data(256) == "SolveInside")
        return g, t.cellWidget(row, 1).findChild(QCheckBox)

    g, cb = solve_inside_box("Metal")
    assert not cb.isEnabled() and not cb.isChecked()
    g.changed.emit("SolveInside", "True")
    assert not metal.solve_inside
    _, cb = solve_inside_box("Sub")
    assert cb.isEnabled() and cb.isChecked()
