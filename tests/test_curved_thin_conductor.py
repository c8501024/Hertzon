"""Curved elements on a thin PEC wall (#476 follow-up): a balun sleeve 1 mm thick around a dipole. EMerge
straightens curved edges until every curved tetrahedron stays valid; it also checked the flat tetrahedra inside
the PEC wall, which the assembly leaves out, and straightened the sleeve faces to nearly the polygon (28 of 370
edges curved on the sleeve of a coax-fed V-dipole). The check now skips the conductor tetrahedra."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model.design import Assignment, Command, Project, Setup  # noqa: E402

PAIRS = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]


def _sleeve_dipole():
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    A = d.add_object
    A("CreateCylinder", "ArmLo", CenterPosition="0,0,-30", Axis="Z", Radius="1", Height="29.5").material = "pec"
    A("CreateCylinder", "ArmHi", CenterPosition="0,0,0.5", Axis="Z", Radius="1", Height="29.5").material = "pec"
    A("CreateRectangle", "PortSheet", Position="-1,0,-0.5", Axis="Y", XSize="2", ZSize="1")
    A("CreateCylinder", "Sleeve", CenterPosition="0,0,-30", Axis="Z", Radius="13", Height="25").material = "pec"
    A("CreateCylinder", "SleeveHole", CenterPosition="0,0,-30", Axis="Z", Radius="12", Height="25")
    d.obj("SleeveHole").consumed_by = "Sleeve"
    d.obj("Sleeve").history.append(Command.new("Subtract", Tools="SleeveHole", KeepOriginals="False"))
    A("CreateRegion", "Region", **{"PaddingType": "Absolute Offset", "+X": "25mm", "-X": "25mm", "+Y": "25mm",
                                   "-Y": "25mm", "+Z": "25mm", "-Z": "25mm"})
    d.boundaries.append(Assignment("Rad", "Radiation", {}, objects=["Region"]))
    d.excitations.append(Assignment("1", "Lumped Port", {"PortNumber": "1", "Impedance": "50ohm"},
                                    objects=["PortSheet"]))
    st = Setup("Setup1")
    st.props.update(Frequency="2GHz", MaximumPasses="1", MinimumPasses="1", CurvedElements="True")
    d.setups.append(st)
    return p, d, st


def _curved_share(mesh, radius_mm: float) -> float:
    """Share of the mesh edges on the cylinder of this radius (about z) whose midpoint follows the arc."""
    X, T = mesh["nodes"] * 1e3, np.asarray(mesh["tets"], np.int64)
    n = len(X)
    pairs = np.array(PAIRS)
    ct = np.asarray(mesh["curv_tets"], np.int64)
    a, b = T[ct][:, pairs[:, 0]].ravel(), T[ct][:, pairs[:, 1]].ravel()
    ck = np.minimum(a, b) * n + np.maximum(a, b)
    cd = np.linalg.norm(np.asarray(mesh["curv_disp"]).reshape(-1, 3), axis=1) * 1e3
    a, b = T[:, pairs[:, 0]].ravel(), T[:, pairs[:, 1]].ravel()
    on = np.abs(np.hypot(X[:, 0], X[:, 1]) - radius_mm) < 1e-3 * radius_mm
    keys = np.unique((np.minimum(a, b) * n + np.maximum(a, b))[on[a] & on[b]])
    i, j = keys // n, keys % n
    mid = (X[i] + X[j]) / 2
    sag = radius_mm - np.hypot(mid[:, 0], mid[:, 1])
    keys, sag = keys[sag > 1e-3], sag[sag > 1e-3]
    o = np.argsort(ck)
    pos = np.clip(np.searchsorted(ck[o], keys), 0, len(ck) - 1)
    d = np.where(ck[o][pos] == keys, cd[o][pos], 0.0)
    return float(np.mean(d > 0.9 * sag)) if len(keys) else 0.0


def test_thin_pec_sleeve_keeps_its_curved_edges(tmp_path):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    p, d, st = _sleeve_dipole()
    b = B.Bridge(p, d, st, tmp_path / "out")
    with em.Simulation(f"curved_{tmp_path.name}", save_file=False) as m:
        b._run_in(m)
    mesh = np.load(tmp_path / "out" / "mesh.npz")
    assert len(mesh["curv_tets"])
    assert _curved_share(mesh, 12.0) > 0.7      # 0.85 with the fix, 0.04 without
    assert _curved_share(mesh, 13.0) > 0.7
