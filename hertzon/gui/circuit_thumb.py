"""Picture of a 3D design for its block in a schematic: drawn from the display tessellation, seen from above at
an angle, so the block looks like the structure it stands for."""
from __future__ import annotations

import base64

import numpy as np
from PySide6.QtCore import QBuffer, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

THUMB = 76                                    # size of the picture in a block (pixels)


def design_thumbnail(doc, name: str, size: int = 2 * THUMB) -> str:
    """PNG (base64) of a 3D design seen from above at an angle, drawn from its display tessellation, so the
    block looks like the structure it stands for. '' when no tessellation of the design is available yet
    (the design was not shown in this session and has no cached tessellation)."""
    try:
        des = doc.project.design(name)
    except KeyError:
        return ""
    tess = {}
    cache = doc.cache if name == doc.design_name else (doc.design_caches.get(name, ({}, {}))[0])
    for o in des.live_objects():
        if not o.model or o.history[0].kind == "CreateRegion":
            continue
        e = cache.get(o.name)
        t = getattr(e, "tess", None) if e is not None else None
        if t is None:
            try:
                from ..kernel import occ as K
                f = K._tess_cache_file(des, o, des.evaluator(doc.project), getattr(doc, "display_quality", "normal"))
                t = K.load_tess(f) if f is not None and f.exists() else None
            except Exception:  # noqa: BLE001
                t = None
        if t is not None and len(t.tris):
            tess[o.name] = (t, o)
    if not tess:
        return ""
    # leave out air boxes (vacuum solids) and the port sheets; conductor sheets are drawn as copper on top
    ports = {n for e in des.excitations for n in e.objects}
    keep = {k: v for k, v in tess.items()
            if k not in ports and not (v[1].dim == 3 and str(v[1].material).lower() in ("vacuum", "air"))}
    tess = keep or tess
    # a sheet under an opaque solid (the ground plane under the substrate) is hidden: leave it out, the rest of the
    # sheets lie on top of the solids
    def box(t):
        q = np.asarray(t.points, float)
        return q.min(axis=0), q.max(axis=0)
    solids = [box(t) for t, o in tess.values() if o.dim == 3 and float(getattr(o, "transparency", 0) or 0) < 0.5]

    def hidden(t):
        lo, hi = box(t)
        tol = 1e-6 * max(1e-12, float(np.max(hi - lo)))
        return any(np.all(lo[:2] < shi[:2]) and np.all(hi[:2] > slo[:2]) and shi[2] > hi[2] + tol
                   for slo, shi in solids)
    tess = {k: v for k, v in tess.items() if v[1].dim == 3 or not hidden(v[0])} or tess
    # view: azimuth -30 deg, elevation 55 deg (mostly from above: planar structures keep their shape)
    az, el = np.radians(-30), np.radians(55)
    R = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    E = np.array([[1, 0, 0], [0, np.cos(el), -np.sin(el)], [0, np.sin(el), np.cos(el)]])
    M = E @ R
    light = np.array([0.3, -0.5, 0.8])
    light /= np.linalg.norm(light)
    polys = []
    allp = []
    for t, o in tess.values():
        P = np.asarray(t.points, float) @ M.T
        allp.append(P)
        sheet = o.dim < 3
        col = QColor("#c9952f") if sheet and str(o.material).lower() in ("vacuum", "air") else \
            QColor(getattr(o, "color", "#b0b0b0") or "#b0b0b0")
        tr = np.asarray(t.tris, int)
        a, b, c = P[tr[:, 0]], P[tr[:, 1]], P[tr[:, 2]]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n, axis=1)
        ok = ln > 0
        n[ok] /= ln[ok, None]
        shade = 0.45 + 0.55 * np.abs(n @ (M @ light))
        depth = (a[:, 1] + b[:, 1] + c[:, 1]) / 3          # y after the view rotation points into the screen
        alpha = 255 if float(getattr(o, "transparency", 0) or 0) < 0.5 else 110
        for k in range(len(tr)):
            polys.append((int(sheet), depth[k], (a[k], b[k], c[k]), col, shade[k], alpha))
    allp = np.vstack(allp)
    x0, x1 = allp[:, 0].min(), allp[:, 0].max()
    z0, z1 = allp[:, 2].min(), allp[:, 2].max()
    span = max(x1 - x0, z1 - z0) or 1.0
    s = (size - 8) / span
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    polys.sort(key=lambda q: (q[0], -q[1]))              # solids back to front, then the sheets on them
    for _, _, tri, col, sh, alpha in polys:
        c = QColor(int(col.red() * sh), int(col.green() * sh), int(col.blue() * sh), alpha)
        p.setPen(QPen(c, 0.6))
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(size / 2 + (v[0] - cx) * s, size / 2 - (v[2] - cz) * s) for v in tri]))
    p.end()
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode()
