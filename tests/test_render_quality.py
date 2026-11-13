"""render_image shows round faces as the modeler does: the modeler's display quality and smooth normals inside
a curved face (an exported picture is WYSIWYG, docs/PRINCIPLES.md)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_mcp import call, server  # noqa: F401

from hertzon.kernel.display import DEFAULT_QUALITY, face_smooth_polydata
from hertzon.mcp import tools


def _rims(tess, name):
    """Point counts of the closed edges (the rim circles)."""
    return sorted({len(e) for e in tess[name].edges if len(e) > 2 and np.allclose(e[0], e[-1])})


def test_render_uses_modeler_quality(server, tmp_path, monkeypatch):  # noqa: F811
    call(server, "create_project", path=str(tmp_path / "c.hzn"), units="mm")
    call(server, "add_object", kind="CreateBox", name="Board", props={"XSize": "40", "YSize": "40", "ZSize": "1"})
    call(server, "add_object", kind="CreateCylinder", name="Pin", material="pec",
         props={"CenterPosition": "20,20,1", "Radius": "0.15", "Height": "2"})
    seen = []
    real = tools.tessellate

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append((k.get("quality", "coarse"), out[0]))
        return out
    monkeypatch.setattr(tools, "tessellate", spy)
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "render_image", "arguments": {"width": 200, "height": 150}}})
    q, tess = seen[-1]
    assert q == DEFAULT_QUALITY == "normal"
    assert _rims(tess, "Pin") == [37]                 # 36 segments per turn, as in the viewer
    ctx = tools.load({})
    coarse, _ = real(ctx.proj, ctx.des)               # checks and face lists keep the cheap coarse mesh
    assert _rims(coarse, "Pin") == [19]
    assert tools.tessellate(ctx.proj, ctx.des, quality="normal")[0]["Pin"] is not coarse["Pin"]   # own cache


def test_face_smooth_normals():
    import pyvista as pv
    cyl = pv.Cylinder(radius=1, height=2, resolution=12).triangulate().clean()
    T = cyl.faces.reshape(-1, 4)[:, 1:]
    n = cyl.compute_normals(cell_normals=True, point_normals=False)["Normals"]
    face = np.where(np.abs(n[:, 0]) > 0.99, 1, 0)                 # caps: face 1, mantle: face 0
    m = face_smooth_polydata(cyl.points, T, face)
    N = m.point_data["Normals"]
    side = np.abs(N[:, 0]) < 1e-6                                   # mantle vertices: radial normals
    assert side.sum() >= 24 and np.allclose(np.linalg.norm(N[side][:, 1:], axis=1), 1, atol=1e-6)
    assert m.n_points > cyl.n_points                                # rim points split between mantle and caps
