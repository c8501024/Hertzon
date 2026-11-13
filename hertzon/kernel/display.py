"""Display surfaces of tessellated objects, shared by the 3D viewer and the picture export (render_image), so
that both show the same thing."""
from __future__ import annotations

import numpy as np

# Display quality of the modeler unless the user chose another one (View › Display Quality).
DEFAULT_QUALITY = "normal"
QUALITIES = ("coarse", "normal", "fine")


def face_smooth_polydata(points, tris, tri_face):
    """PolyData of the triangles with one vertex per (point, CAD face) and point normals: smooth inside a
    curved face and sharp at its edges, so cylinders, spheres and cones look round instead of faceted.
    cell_data['face'] holds the CAD face of every triangle."""
    import pyvista as pv
    tris = np.asarray(tris)
    tf = np.asarray(tri_face)
    m = int(tf.max()) + 1 if len(tf) else 1
    key = tris.astype(np.int64) * m + tf[:, None]
    uk, inv = np.unique(key.ravel(), return_inverse=True)
    pts = np.asarray(points)[uk // m]
    tris2 = inv.reshape(-1, 3)
    mesh = pv.PolyData(pts, np.hstack([np.full((len(tris2), 1), 3), tris2]).ravel())
    mesh.cell_data["face"] = tf
    try:
        nm = mesh.compute_normals(cell_normals=False, split_vertices=False, consistent_normals=True,
                                  auto_orient_normals=False, feature_angle=180)
        if nm.n_cells == mesh.n_cells:
            mesh = nm
            mesh.point_data.active_normals_name = "Normals"
    except Exception:  # noqa: BLE001, S110 (no normals: flat shading, still a picture)
        pass
    return mesh
