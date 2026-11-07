"""STEP/IGES files are translated once and read from a BREP cache afterwards: the same bodies in the same
order and in metres, a new translation when the file changes."""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gmsh

from hertzon.kernel import occ as K


def _bodies(fn):
    gmsh.model.add("t")
    try:
        out = fn()
        gmsh.model.occ.synchronize()
        return [tuple(np.round(gmsh.model.occ.getCenterOfMass(d, t), 9)) for d, t in out]
    finally:
        gmsh.model.remove()


def test_step_import_reads_the_brep_cache_with_the_same_bodies(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    K.ensure_gmsh("gen")
    o = gmsh.model.occ
    o.addBox(0, 0, 0, 2, 2, 2)
    o.addSphere(5, 0, 0, 1)
    o.addCylinder(10, 0, 0, 0, 0, 3, 0.5)
    o.synchronize()
    gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
    step = tmp_path / "part.step"
    gmsh.write(str(step))
    gmsh.option.setString("Geometry.OCCTargetUnit", "")
    gmsh.model.remove()

    direct = _bodies(lambda: K._import_cad_file(str(step), ".step", 1.0))
    first = _bodies(lambda: K.import_cad(str(step), 1e-3))
    cache = list((tmp_path / "home" / ".hertzon" / "cad_cache").glob("part_*.brep"))
    assert len(cache) == 1
    again = _bodies(lambda: K.import_cad(str(step), 1e-3))
    assert len(direct) == 3 and direct == first == again
    assert np.allclose(direct[1], (5e-3, 0, 0))          # metres

    st = step.stat()
    os.utime(step, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))   # a changed file is translated again
    assert _bodies(lambda: K.import_cad(str(step), 1e-3)) == direct
    assert len(list((tmp_path / "home" / ".hertzon" / "cad_cache").glob("part_*.brep"))) == 2
