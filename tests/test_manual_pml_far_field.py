"""PML assigned by hand on the faces of an air box: the faces under the PML are the far-field surface (as with
Auto Open), so a far-field sphere gets its far field."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

em = pytest.importorskip("emerge")

from hertzon.kernel import occ as K  # noqa: E402
from hertzon.model import schema  # noqa: E402
from hertzon.model.design import Assignment, FaceRef, InfiniteSphere, Project, Setup  # noqa: E402
from hertzon.solver import bridge as B  # noqa: E402


def test_manual_pml_faces_are_the_far_field_surface(tmp_path):
    p = Project()
    d = p.designs[0]
    d.add_object("CreateCylinder", "Wire", CenterPosition="0,0,0", Axis="Z", Radius="1mm", Height="40mm",
                 material="pec")
    d.add_object("CreateBox", "Air", Position="-40mm,-40mm,0", XSize="80mm", YSize="80mm", ZSize="80mm",
                 material="air")
    K.ensure_gmsh("test_manual_pml")
    t = K.tessellate(K.build_design(d, d.evaluator(p)))["Air"]
    up = [FaceRef("Air", i) for i, c in enumerate(t.face_center) if c[2] > 1e-6]      # all but the bottom
    assert len(up) == 5
    props = schema.defaults(schema.BOUNDARIES["PML"])
    props.update(Thickness="10mm", Layers="2")
    d.boundaries.append(Assignment("PML1", "PML", props, faces=up))
    d.spheres.append(InfiniteSphere("FF"))
    st = Setup("Setup1")
    st.props.update(Frequency="3GHz", LambdaRefinement="False", CurvedElements="False")
    d.setups.append(st)
    b = B.Bridge(p, d, st, tmp_path)
    with em.Simulation("manual_pml", save_file=False) as m:
        b.prepare(m)
        assert b.open_faces is not None
        # the five faces under the PML; the bottom face (a ground) is not part of the far-field surface
        assert len(b.open_faces.tags) == 5
