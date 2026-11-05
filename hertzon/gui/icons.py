"""Vector icons for the ribbon, drawn with QPainter (no image files needed)."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

S = 64          # drawing canvas; Qt scales down for 16/32 px buttons

BLUE = QColor("#3b78c4")
LBLUE = QColor("#9cc3ee")
DBLUE = QColor("#1d4f91")
GREY = QColor("#6b6b6b")
LGREY = QColor("#c9c9c9")
GREEN = QColor("#3c9a3c")
RED = QColor("#c8372d")
ORANGE = QColor("#e48a1a")
YELLOW = QColor("#f1c232")
PURPLE = QColor("#7a4fb5")


def _pen(c, w=3.0):
    p = QPen(c, w)
    p.setJoinStyle(Qt.RoundJoin)
    p.setCapStyle(Qt.RoundCap)
    return p


def _grad(c1, c2, x0=0, y0=0, x1=S, y1=S):
    g = QLinearGradient(x0, y0, x1, y1)
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    return QBrush(g)


def _poly(*pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


# ---- shapes ------------------------------------------------------------------------------------
def _box(p, x=10, y=18, w=32, h=30, d=12, c1=LBLUE, c2=BLUE):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(c1, c2))
    p.drawPolygon(_poly((x, y), (x + w, y), (x + w, y + h), (x, y + h)))
    p.setBrush(c1.lighter(110))
    p.drawPolygon(_poly((x, y), (x + d, y - d), (x + w + d, y - d), (x + w, y)))
    p.setBrush(c2.darker(115))
    p.drawPolygon(_poly((x + w, y), (x + w + d, y - d), (x + w + d, y + h - d), (x + w, y + h)))


def _cylinder(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(LBLUE, BLUE, 12, 0, 52, 0))
    path = QPainterPath()
    path.moveTo(12, 16)
    path.lineTo(12, 48)
    path.arcTo(QRectF(12, 40, 40, 16), 180, 180)
    path.lineTo(52, 16)
    p.drawPath(path)
    p.setBrush(LBLUE.lighter(110))
    p.drawEllipse(QRectF(12, 8, 40, 16))


def _sphere(p):
    p.setPen(_pen(DBLUE, 2.5))
    g = QLinearGradient(16, 12, 48, 52)
    g.setColorAt(0, QColor("#e6f1fc"))
    g.setColorAt(0.5, LBLUE)
    g.setColorAt(1, BLUE)
    p.setBrush(QBrush(g))
    p.drawEllipse(QRectF(9, 9, 46, 46))
    p.setBrush(Qt.NoBrush)
    p.setPen(_pen(DBLUE.lighter(140), 1.5))
    p.drawEllipse(QRectF(9, 26, 46, 12))


def _cone(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(LBLUE, BLUE, 10, 0, 54, 0))
    path = QPainterPath()
    path.moveTo(32, 6)
    path.lineTo(10, 48)
    path.arcTo(QRectF(10, 40, 44, 16), 180, 180)
    path.closeSubpath()
    p.drawPath(path)


def _torus(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(LBLUE, BLUE))
    path = QPainterPath()
    path.addEllipse(QRectF(6, 18, 52, 28))
    path.addEllipse(QRectF(22, 27, 20, 10))
    path.setFillRule(Qt.OddEvenFill)
    p.drawPath(path)


def _polyhedron(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(LBLUE, BLUE))
    p.drawPolygon(_poly((32, 6), (56, 20), (56, 44), (32, 58), (8, 44), (8, 20)))
    p.drawLine(QPointF(8, 20), QPointF(32, 32))
    p.drawLine(QPointF(56, 20), QPointF(32, 32))
    p.drawLine(QPointF(32, 32), QPointF(32, 58))


def _rect(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawPolygon(_poly((6, 40), (24, 20), (58, 20), (40, 40)))


def _circle(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawEllipse(QRectF(6, 18, 52, 28))


def _ellipse(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawEllipse(QRectF(4, 22, 56, 20))


def _polygon(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawPolygon(_poly((32, 16), (56, 28), (48, 46), (16, 46), (8, 28)))


def _line(p):
    p.setPen(_pen(DBLUE, 3.5))
    p.drawLine(QPointF(10, 52), QPointF(54, 12))
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(YELLOW)
    for x, y in ((10, 52), (54, 12)):
        p.drawRect(QRectF(x - 4, y - 4, 8, 8))


def _arc3(p):
    p.setPen(_pen(DBLUE, 3.5))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(10, 26, 44, 44), 0, 180 * 16)
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(YELLOW)
    for x, y in ((10, 48), (32, 26), (54, 48)):
        p.drawRect(QRectF(x - 4, y - 4, 8, 8))


def _arc_center(p):
    p.setPen(QPen(DBLUE, 1.5, Qt.DashLine))
    p.drawLine(QPointF(14, 50), QPointF(50, 50))
    p.drawLine(QPointF(14, 50), QPointF(14, 14))
    p.setPen(_pen(DBLUE, 3.5))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(-22, 14, 72, 72), 0, 90 * 16)
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(YELLOW)
    for x, y in ((50, 50), (14, 14)):
        p.drawRect(QRectF(x - 4, y - 4, 8, 8))
    p.setBrush(DBLUE)
    p.drawEllipse(QPointF(14, 50), 3.5, 3.5)


def _polyline(p):
    p.setPen(_pen(DBLUE, 3.5))
    pts = [(8, 50), (22, 20), (40, 40), (56, 12)]
    for a, b in zip(pts, pts[1:]):
        p.drawLine(QPointF(*a), QPointF(*b))
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(YELLOW)
    for x, y in pts:
        p.drawRect(QRectF(x - 4, y - 4, 8, 8))


def _filter(p):
    # hairpin filter: substrate with three alternating copper U resonators and two feeds
    p.setPen(_pen(QColor("#6b7a3a"), 2))
    p.setBrush(QColor("#c9d6a3"))
    p.drawRect(QRectF(4, 8, 56, 48))
    p.setPen(_pen(QColor("#c8892b"), 4.5))
    for x, up in ((14, True), (28, False), (42, True)):
        a, b = (16, 46) if up else (48, 18)
        path = QPainterPath(QPointF(x, a))
        path.lineTo(x, b)
        path.lineTo(x + 8, b)
        path.lineTo(x + 8, a)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    p.drawLine(QPointF(4, 30), QPointF(14, 30))
    p.drawLine(QPointF(50, 30), QPointF(60, 30))


def _antenna(p):
    # patch antenna: substrate, copper patch with inset feed and radiated arcs
    p.setPen(_pen(QColor("#6b7a3a"), 2))
    p.setBrush(QColor("#c9d6a3"))
    p.drawRect(QRectF(6, 20, 40, 40))
    p.setPen(_pen(QColor("#7a5216"), 1.5))
    p.setBrush(QColor("#c8892b"))
    p.drawPolygon(_poly((14, 28), (38, 28), (38, 50), (29, 50), (29, 42), (23, 42), (23, 50), (14, 50)))
    p.drawRect(QRectF(24.5, 42, 3, 18))
    p.setBrush(Qt.NoBrush)
    p.setPen(_pen(BLUE, 2.5))
    for r in (8, 14):
        p.drawArc(QRectF(46 - r, 14 - r, 2 * r, 2 * r), 0 * 16, 90 * 16)


def _boolean(p, kind):
    a, b = QRectF(6, 14, 32, 32), QRectF(26, 18, 32, 32)
    pa, pb = QPainterPath(), QPainterPath()
    pa.addRect(a)
    pb.addEllipse(b)
    ghost = _pen(GREY, 1.5)
    ghost.setStyle(Qt.DashLine)
    if kind == "unite":
        r = pa.united(pb)
    elif kind == "subtract":
        r = pa.subtracted(pb)
    else:
        r = pa.intersected(pb)
    p.setPen(ghost)
    p.setBrush(Qt.NoBrush)
    p.drawPath(pa)
    p.drawPath(pb)
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(LBLUE, BLUE))
    p.drawPath(r)


def _split(p):
    _box(p, 8, 22, 34, 28, 12)
    pen = _pen(RED, 3)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawLine(QPointF(32, 2), QPointF(24, 62))


def _arrow(p, x0, y0, x1, y1, c=GREEN, w=4.0, head=10):
    import math
    p.setPen(_pen(c, w))
    p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
    ang = math.atan2(y1 - y0, x1 - x0)
    p.setBrush(c)
    p.setPen(_pen(c, 1))
    p.drawPolygon(_poly((x1, y1),
                        (x1 - head * math.cos(ang - 0.45), y1 - head * math.sin(ang - 0.45)),
                        (x1 - head * math.cos(ang + 0.45), y1 - head * math.sin(ang + 0.45))))


def _move(p):
    _box(p, 6, 30, 22, 22, 8, LGREY, GREY)
    _arrow(p, 26, 34, 54, 14)


def _rotate(p):
    _box(p, 18, 30, 24, 22, 8)
    p.setPen(_pen(GREEN, 4))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(6, 6, 52, 30), 30 * 16, 220 * 16)
    _arrow(p, 50, 18, 55, 22, GREEN, 3, 9)


def _mirror(p):
    pen = _pen(GREY, 2)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawLine(QPointF(32, 4), QPointF(32, 60))
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(LBLUE, BLUE))
    p.drawPolygon(_poly((6, 50), (26, 50), (26, 14)))
    p.setBrush(QColor(156, 195, 238, 120))
    pen = _pen(DBLUE, 2)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawPolygon(_poly((58, 50), (38, 50), (38, 14)))


def _dup_line(p):
    for i, x in enumerate((6, 22, 38)):
        _box(p, x, 34 - i * 6, 16, 16, 6, LBLUE if i == 0 else QColor(156, 195, 238, 160), BLUE)


def _dup_axis(p):
    import math
    p.setPen(_pen(GREY, 2))
    p.drawLine(QPointF(32, 4), QPointF(32, 60))
    for k in range(6):
        a = k * math.pi / 3
        x, y = 32 + 20 * math.cos(a), 32 + 9 * math.sin(a)
        p.setPen(_pen(DBLUE, 2))
        p.setBrush(LBLUE if k == 0 else QColor(156, 195, 238, 150))
        p.drawRect(QRectF(x - 6, y - 6, 12, 12))


def _fit(p, sel=False):
    p.setPen(_pen(GREY, 3))
    for (x, y, dx, dy) in ((6, 6, 1, 1), (58, 6, -1, 1), (6, 58, 1, -1), (58, 58, -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + 12 * dx, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + 12 * dy))
    if sel:
        _box(p, 18, 30, 20, 18, 8, QColor("#f7d774"), ORANGE)
    else:
        _box(p, 16, 30, 24, 20, 9)


def _zoom(p):
    p.setPen(_pen(GREY, 6))
    p.drawLine(QPointF(40, 40), QPointF(56, 56))
    p.setPen(_pen(DBLUE, 4))
    p.setBrush(QColor(220, 236, 252))
    p.drawEllipse(QRectF(6, 6, 38, 38))


def _pan(p):
    for x0, y0, x1, y1 in ((32, 32, 32, 6), (32, 32, 32, 58), (32, 32, 6, 32), (32, 32, 58, 32)):
        _arrow(p, x0, y0, x1, y1, DBLUE, 4, 12)


def _orient(p, which):
    _box(p, 14, 26, 26, 24, 10, LGREY, GREY)
    hi = {"top": _poly((14, 26), (24, 16), (50, 16), (40, 26)),
          "front": _poly((14, 26), (40, 26), (40, 50), (14, 50)),
          "right": _poly((40, 26), (50, 16), (50, 40), (40, 50))}.get(which)
    if hi is not None:
        p.setPen(_pen(DBLUE, 2.5))
        p.setBrush(QColor(255, 196, 64))
        p.drawPolygon(hi)
    elif which == "iso":
        p.setPen(_pen(DBLUE, 2.5))
        p.setBrush(QColor(255, 196, 64, 170))
        for poly in (_poly((14, 26), (24, 16), (50, 16), (40, 26)), _poly((14, 26), (40, 26), (40, 50), (14, 50)),
                     _poly((40, 26), (50, 16), (50, 40), (40, 50))):
            p.drawPolygon(poly)


def _eye(p, hidden=False):
    p.setPen(_pen(DBLUE, 3))
    p.setBrush(QColor(235, 244, 252))
    path = QPainterPath()
    path.moveTo(4, 32)
    path.quadTo(32, 6, 60, 32)
    path.quadTo(32, 58, 4, 32)
    p.drawPath(path)
    p.setBrush(DBLUE)
    p.drawEllipse(QRectF(24, 24, 16, 16))
    if hidden:
        p.setPen(_pen(RED, 5))
        p.drawLine(QPointF(10, 54), QPointF(54, 10))


def _wire(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(Qt.NoBrush)
    for poly in (_poly((10, 24), (42, 24), (42, 54), (10, 54)), _poly((10, 24), (22, 12), (54, 12), (42, 24)),
                 _poly((42, 24), (54, 12), (54, 42), (42, 54))):
        p.drawPolygon(poly)
    pen = _pen(DBLUE, 1.5)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawLine(QPointF(22, 12), QPointF(22, 42))
    p.drawLine(QPointF(22, 42), QPointF(10, 54))
    p.drawLine(QPointF(22, 42), QPointF(54, 42))


def _grid(p):
    p.setPen(_pen(GREY, 1.5))
    for i in range(5):
        v = 8 + i * 12
        p.drawLine(QPointF(8, v), QPointF(56, v))
        p.drawLine(QPointF(v, 8), QPointF(v, 56))
    p.setPen(_pen(BLUE, 3))
    p.drawLine(QPointF(8, 56), QPointF(56, 56))
    p.drawLine(QPointF(8, 56), QPointF(8, 8))


def _snap(p):
    _box(p, 8, 26, 30, 28, 10, LGREY, GREY)
    p.setPen(_pen(RED, 3))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(30, 6, 16, 16))
    p.setPen(_pen(DBLUE, 2))
    p.drawLine(QPointF(38, 2), QPointF(38, 26))
    p.drawLine(QPointF(26, 14), QPointF(50, 14))


def _nonmodel(p):
    p.setPen(QPen(GREY, 2.5, Qt.DashLine))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(10, 18, 34, 34))
    p.drawLine(QPointF(10, 18), QPointF(22, 8))
    p.drawLine(QPointF(44, 18), QPointF(56, 8))
    p.drawLine(QPointF(22, 8), QPointF(56, 8))
    p.drawLine(QPointF(56, 8), QPointF(56, 42))
    p.drawLine(QPointF(44, 52), QPointF(56, 42))


def _cursor(p, mode):
    if mode == "object":
        _box(p, 10, 24, 28, 26, 10, QColor("#f7d774"), ORANGE)
    elif mode == "face":
        _box(p, 10, 24, 28, 26, 10, LGREY, GREY)
        p.setPen(_pen(DBLUE, 2.5))
        p.setBrush(QColor(255, 196, 64))
        p.drawPolygon(_poly((10, 24), (38, 24), (38, 50), (10, 50)))
    elif mode == "edge":
        _box(p, 10, 24, 28, 26, 10, LGREY, GREY)
        p.setPen(_pen(ORANGE, 5))
        p.drawLine(QPointF(38, 24), QPointF(38, 50))
    else:
        _box(p, 10, 24, 28, 26, 10, LGREY, GREY)
        p.setPen(_pen(DBLUE, 2))
        p.setBrush(ORANGE)
        p.drawEllipse(QRectF(33, 19, 10, 10))
    # pointer
    p.setPen(_pen(QColor("#202020"), 1.5))
    p.setBrush(Qt.white)
    p.drawPolygon(_poly((40, 34), (40, 60), (46, 54), (50, 62), (54, 60), (50, 52), (58, 52)))


def _doc(p, c=Qt.white):
    p.setPen(_pen(GREY, 2.5))
    p.setBrush(c)
    p.drawPolygon(_poly((12, 4), (40, 4), (52, 16), (52, 60), (12, 60)))
    p.drawPolyline(_poly((40, 4), (40, 16), (52, 16)))


def _new(p):
    _doc(p)
    p.setPen(_pen(YELLOW.darker(120), 1))
    p.setBrush(YELLOW)
    p.drawEllipse(QRectF(36, 36, 24, 24))
    p.setPen(_pen(Qt.white, 3))
    p.drawLine(QPointF(48, 41), QPointF(48, 55))
    p.drawLine(QPointF(41, 48), QPointF(55, 48))


def _open(p):
    p.setPen(_pen(QColor("#b58a1e"), 2.5))
    p.setBrush(QColor("#f6d47a"))
    p.drawPolygon(_poly((4, 14), (22, 14), (28, 20), (52, 20), (52, 54), (4, 54)))
    p.setBrush(QColor("#fbe3a1"))
    p.drawPolygon(_poly((10, 28), (60, 28), (52, 54), (4, 54)))


def _save(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(BLUE)
    p.drawRoundedRect(QRectF(6, 6, 52, 52), 4, 4)
    p.setBrush(Qt.white)
    p.drawRect(QRectF(16, 6, 32, 20))
    p.setBrush(LGREY)
    p.drawRect(QRectF(14, 36, 36, 22))
    p.setBrush(DBLUE)
    p.drawRect(QRectF(38, 10, 6, 12))


def _undo(p, redo=False):
    p.save()
    if redo:
        p.translate(S, 0)
        p.scale(-1, 1)
    p.setPen(_pen(BLUE, 5))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(16, 26)
    path.cubicTo(36, 14, 62, 26, 50, 52)
    p.drawPath(path)
    p.setBrush(BLUE)
    p.setPen(_pen(BLUE, 1))
    p.drawPolygon(_poly((6, 26), (22, 12), (24, 36)))
    p.restore()


def _copy(p):
    p.save()
    p.translate(-6, -2)
    p.scale(0.8, 0.8)
    _doc(p)
    p.restore()
    p.save()
    p.translate(14, 12)
    p.scale(0.8, 0.8)
    _doc(p, QColor("#eaf3fc"))
    p.restore()


def _paste(p):
    p.setPen(_pen(QColor("#8a6d3b"), 2.5))
    p.setBrush(QColor("#d9b26f"))
    p.drawRoundedRect(QRectF(8, 10, 38, 48), 4, 4)
    p.setBrush(LGREY)
    p.drawRect(QRectF(18, 6, 18, 10))
    p.save()
    p.translate(24, 22)
    p.scale(0.6, 0.6)
    _doc(p)
    p.restore()


def _delete(p):
    p.setPen(_pen(RED, 7))
    p.drawLine(QPointF(12, 12), QPointF(52, 52))
    p.drawLine(QPointF(52, 12), QPointF(12, 52))


def _gear(p, c=GREY):
    import math
    p.setPen(_pen(c.darker(120), 2))
    p.setBrush(c)
    path = QPainterPath()
    n = 8
    for i in range(2 * n):
        a = i * math.pi / n
        r = 28 if i % 2 == 0 else 21
        for da in (-0.17, 0.17):
            x, y = 32 + r * math.cos(a + da), 32 + r * math.sin(a + da)
            if not path.elementCount():
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
    path.closeSubpath()
    hole = QPainterPath()
    hole.addEllipse(QRectF(22, 22, 20, 20))
    p.drawPath(path.subtracted(hole))


def _material(p):
    for i, c in enumerate((QColor("#c98b3a"), QColor("#9db7cf"), GREEN)):
        p.setPen(_pen(c.darker(140), 2))
        p.setBrush(c)
        p.drawEllipse(QRectF(6 + i * 14, 14 + (i % 2) * 14, 28, 28))


def _units(p):
    p.setPen(_pen(QColor("#8a6d3b"), 2))
    p.setBrush(QColor("#f3d27a"))
    p.drawRect(QRectF(4, 22, 56, 20))
    p.setPen(_pen(QColor("#5c4520"), 2))
    for i in range(1, 11):
        x = 4 + i * 5.1
        p.drawLine(QPointF(x, 22), QPointF(x, 22 + (10 if i % 5 == 0 else 6)))


def _measure(p):
    _units(p)
    _arrow(p, 30, 12, 8, 12, RED, 2.5, 7)
    _arrow(p, 30, 12, 56, 12, RED, 2.5, 7)


def _region(p):
    pen = _pen(GREY, 2)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.setBrush(QColor(200, 220, 240, 90))
    p.drawRect(QRectF(4, 10, 56, 48))
    _box(p, 20, 34, 20, 16, 8)


def _import(p):
    _doc(p)
    _arrow(p, 4, 40, 34, 40, GREEN, 5, 14)


def _fillet(p):
    p.setPen(_pen(DBLUE, 3))
    p.setBrush(_grad(LBLUE, BLUE))
    path = QPainterPath()
    path.moveTo(8, 56)
    path.lineTo(8, 8)
    path.lineTo(30, 8)
    path.quadTo(56, 8, 56, 34)
    path.lineTo(56, 56)
    path.closeSubpath()
    p.drawPath(path)


def _sweep(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawPolygon(_poly((6, 54), (6, 30), (22, 22), (22, 46)))
    _arrow(p, 24, 38, 58, 22, GREEN, 3.5, 11)


def _thicken(p):
    _box(p, 8, 30, 40, 10, 12)
    _arrow(p, 30, 26, 30, 4, GREEN, 3, 10)


def _cover(p):
    p.setPen(_pen(DBLUE, 3))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawPolygon(_poly((8, 44), (24, 16), (56, 22), (46, 50)))


def _solution_type(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor("#eaf3fc"))
    p.drawRoundedRect(QRectF(6, 8, 52, 48), 6, 6)
    p.setPen(_pen(RED, 3))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(10, 32)
    for i in range(1, 9):
        path.quadTo(10 + i * 5.5 - 2.75, 32 + (-16 if i % 2 else 16), 10 + i * 5.5, 32)
    p.drawPath(path)


def _setup(p):
    _gear(p, QColor("#7aa5d8"))
    p.setPen(_pen(DBLUE, 1))
    p.setBrush(DBLUE)
    p.drawEllipse(QRectF(28, 28, 8, 8))


def _sweep_f(p):
    p.setPen(_pen(GREY, 2))
    p.drawLine(QPointF(6, 56), QPointF(58, 56))
    p.drawLine(QPointF(6, 56), QPointF(6, 6))
    p.setPen(_pen(RED, 3))
    path = QPainterPath()
    path.moveTo(8, 14)
    path.cubicTo(24, 14, 26, 52, 34, 52)
    path.cubicTo(42, 52, 44, 14, 58, 14)
    p.drawPath(path)


def _boundary(p, kind=""):
    _box(p, 8, 24, 34, 30, 12, LGREY, GREY)
    c = {"rad": QColor("#ee7f2d"), "pml": PURPLE, "pec": QColor("#d9a400"), "auto": GREEN}.get(kind, ORANGE)
    p.setPen(_pen(c.darker(140), 2.5))
    p.setBrush(c)
    p.drawPolygon(_poly((8, 24), (20, 12), (54, 12), (42, 24)))
    if kind == "rad":
        p.setPen(_pen(c, 2.5))
        p.setBrush(Qt.NoBrush)
        for r in (6, 11):
            p.drawArc(QRectF(36 - r, 4 - r + 4, 2 * r, 2 * r), 20 * 16, 140 * 16)


def _port(p, lumped=False):
    _box(p, 8, 24, 34, 30, 12, LGREY, GREY)
    p.setPen(_pen(RED.darker(130), 2.5))
    p.setBrush(QColor(232, 90, 70))
    if lumped:
        p.drawRect(QRectF(20, 30, 10, 18))
    else:
        p.drawPolygon(_poly((8, 24), (42, 24), (42, 54), (8, 54)))
    _arrow(p, 30, 40, 60, 40, RED, 3, 9)


def _mesh(p, plot=False):
    p.setPen(_pen(DBLUE, 1.8))
    p.setBrush(QColor("#eaf3fc") if not plot else QColor("#fff2c4"))
    pts = [(6, 56), (32, 56), (58, 56), (18, 34), (44, 34), (32, 10)]
    tris = [(0, 1, 3), (1, 4, 3), (1, 2, 4), (3, 4, 5)]
    for t in tris:
        p.drawPolygon(_poly(*[pts[i] for i in t]))


def _validate(p):
    p.setPen(_pen(GREEN.darker(130), 2))
    p.setBrush(QColor("#dff3df"))
    p.drawEllipse(QRectF(6, 6, 52, 52))
    p.setPen(_pen(GREEN.darker(110), 7))
    p.drawPolyline(_poly((18, 33), (28, 44), (46, 20)))


def _analyze(p):
    p.setPen(_pen(GREEN.darker(140), 2))
    p.setBrush(_grad(QColor("#7fd67f"), GREEN))
    p.drawPolygon(_poly((14, 8), (56, 32), (14, 56)))


def _sphere_ff(p):
    _sphere(p)
    p.setPen(_pen(ORANGE, 2.5))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(22, 9, 20, 46))


def _report(p, kind="rect"):
    p.setPen(_pen(GREY, 2))
    p.setBrush(Qt.white)
    p.drawRect(QRectF(4, 6, 56, 52))
    if kind == "rect":
        p.setPen(_pen(LGREY, 1))
        for i in range(1, 4):
            p.drawLine(QPointF(4, 6 + i * 13), QPointF(60, 6 + i * 13))
        p.setPen(_pen(RED, 3))
        path = QPainterPath()
        path.moveTo(6, 16)
        path.cubicTo(24, 16, 26, 52, 34, 52)
        path.cubicTo(42, 52, 44, 18, 58, 18)
        p.drawPath(path)
    elif kind == "smith":
        p.setPen(_pen(GREY, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(8, 8, 48, 48))
        p.drawEllipse(QRectF(32, 20, 24, 24))
        p.drawLine(QPointF(8, 32), QPointF(56, 32))
        p.setPen(_pen(RED, 3))
        p.drawArc(QRectF(16, 16, 32, 32), 30 * 16, 250 * 16)
    elif kind == "table":
        p.setPen(_pen(GREY, 1.5))
        for i in range(1, 5):
            p.drawLine(QPointF(4, 6 + i * 10.4), QPointF(60, 6 + i * 10.4))
        p.drawLine(QPointF(22, 6), QPointF(22, 58))
        p.drawLine(QPointF(41, 6), QPointF(41, 58))
        p.setBrush(LBLUE)
        p.drawRect(QRectF(4, 6, 56, 10.4))
    elif kind == "pattern":
        p.setPen(_pen(GREY, 1.5))
        p.setBrush(Qt.NoBrush)
        for r in (8, 16, 24):
            p.drawEllipse(QPointF(32, 32), r, r)
        p.setPen(_pen(RED, 3))
        path = QPainterPath()
        path.moveTo(32, 32)
        path.cubicTo(10, 2, 54, 2, 32, 32)
        path.cubicTo(18, 44, 46, 44, 32, 32)
        p.drawPath(path)
    elif kind == "polar3d":
        g = QLinearGradient(10, 10, 54, 54)
        g.setColorAt(0, RED)
        g.setColorAt(0.5, YELLOW)
        g.setColorAt(1, BLUE)
        p.setPen(_pen(GREY, 1))
        p.setBrush(QBrush(g))
        path = QPainterPath()
        path.moveTo(32, 54)
        path.cubicTo(4, 20, 18, 4, 32, 10)
        path.cubicTo(46, 4, 60, 20, 32, 54)
        p.drawPath(path)


def _field(p, kind="mag"):
    g = QLinearGradient(4, 0, 60, 0)
    for t, c in ((0, QColor("#1f2bd6")), (0.35, QColor("#18c1d6")), (0.6, QColor("#c7e81a")),
                 (0.8, QColor("#f39a1d")), (1, QColor("#d61f1f"))):
        g.setColorAt(t, c)
    p.setPen(_pen(GREY, 1.5))
    p.setBrush(QBrush(g))
    p.drawPolygon(_poly((4, 46), (18, 18), (60, 18), (46, 46)))
    if kind == "vec":
        for x, y in ((18, 36), (30, 30), (42, 24)):
            _arrow(p, x, y, x + 10, y - 12, QColor("#202020"), 2, 6)
    if kind == "h":
        p.setPen(_pen(Qt.white, 3))
        p.drawLine(QPointF(26, 24), QPointF(24, 40))
        p.drawLine(QPointF(38, 24), QPointF(36, 40))
        p.drawLine(QPointF(25, 32), QPointF(37, 32))


def _animate(p):
    _field(p)
    p.setPen(_pen(Qt.white, 1))
    p.setBrush(QColor(0, 0, 0, 160))
    p.drawEllipse(QRectF(34, 34, 28, 28))
    p.setBrush(Qt.white)
    p.drawPolygon(_poly((44, 40), (56, 48), (44, 56)))


def _clear(p):
    _field(p)
    p.setPen(_pen(RED, 5))
    p.drawLine(QPointF(36, 36), QPointF(60, 60))
    p.drawLine(QPointF(60, 36), QPointF(36, 60))


def _soldata(p):
    _report(p, "table")
    p.setPen(_pen(GREEN.darker(120), 1))
    p.setBrush(GREEN)
    p.drawEllipse(QRectF(38, 38, 22, 22))
    p.setPen(_pen(Qt.white, 3))
    p.drawPolyline(_poly((43, 49), (48, 54), (55, 44)))


def _export(p):
    _doc(p)
    _arrow(p, 26, 40, 62, 40, BLUE, 5, 14)


def _design(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor("#eaf3fc"))
    p.drawRect(QRectF(6, 10, 52, 44))
    p.setBrush(BLUE)
    p.drawRect(QRectF(6, 10, 52, 9))
    _box(p, 20, 34, 18, 14, 6)


def _layout_back(p):
    """Update from Layout: the 3D design with an arrow back to the schematic."""
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor("#eaf3fc"))
    p.drawRect(QRectF(14, 6, 44, 38))
    p.setBrush(BLUE)
    p.drawRect(QRectF(14, 6, 44, 8))
    _box(p, 26, 28, 16, 11, 5)
    _arrow(p, 50, 54, 6, 54, GREEN, 5, 13)


def _layout_fwd(p):
    """Update 3D Layout: an arrow from the schematic into the 3D design."""
    _layout_back_frame(p)
    _arrow(p, 6, 54, 50, 54, GREEN, 5, 13)


def _layout_back_frame(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor("#eaf3fc"))
    p.drawRect(QRectF(14, 6, 44, 38))
    p.setBrush(BLUE)
    p.drawRect(QRectF(14, 6, 44, 8))
    _box(p, 26, 28, 16, 11, 5)


def _variables(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor("#eaf3fc"))
    p.drawRect(QRectF(6, 8, 52, 48))
    f = p.font()
    f.setPixelSize(26)
    f.setItalic(True)
    f.setBold(True)
    p.setFont(f)
    p.setPen(DBLUE)
    p.drawText(QRectF(6, 8, 52, 48), Qt.AlignCenter, "x=")


def _calculator(p):
    """Fields Calculator: a field patch with an integral sign."""
    _field(p)
    f = p.font()
    f.setPixelSize(34)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#202020"))
    p.drawText(QRectF(10, 4, 44, 52), Qt.AlignCenter, "∫")


def _screenshot(p):
    p.setPen(_pen(GREY.darker(130), 2))
    p.setBrush(GREY)
    p.drawRoundedRect(QRectF(4, 16, 56, 40), 6, 6)
    p.drawRect(QRectF(20, 8, 20, 10))
    p.setBrush(QColor("#d8e6f5"))
    p.drawEllipse(QRectF(20, 22, 26, 26))
    p.setBrush(DBLUE)
    p.drawEllipse(QRectF(27, 29, 12, 12))


def _help(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(BLUE)
    p.drawEllipse(QRectF(6, 6, 52, 52))
    f = p.font()
    f.setPixelSize(38)
    f.setBold(True)
    p.setFont(f)
    p.setPen(Qt.white)
    p.drawText(QRectF(6, 6, 52, 52), Qt.AlignCenter, "?")


def _projection(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor(156, 195, 238, 160))
    p.drawPolygon(_poly((6, 14), (58, 4), (58, 60), (6, 50)))
    p.setPen(_pen(GREY, 1.5))
    p.drawLine(QPointF(6, 32), QPointF(58, 32))


def _byname(p):
    p.setPen(_pen(GREY, 2))
    p.setBrush(Qt.white)
    p.drawRect(QRectF(4, 8, 56, 48))
    p.setPen(_pen(DBLUE, 3))
    for i, w in enumerate((40, 30, 44)):
        p.drawLine(QPointF(10, 18 + i * 14), QPointF(10 + w, 18 + i * 14))
    p.setPen(_pen(ORANGE, 3))
    p.setBrush(QColor(255, 196, 64, 120))
    p.drawRect(QRectF(6, 25, 52, 13))


def _selall(p):
    pen = _pen(GREY, 2)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(4, 4, 56, 56))
    _box(p, 10, 30, 16, 16, 6, QColor("#f7d774"), ORANGE)
    _box(p, 32, 40, 16, 14, 6, QColor("#f7d774"), ORANGE)


def _group(p, dissolve=False):
    pen = _pen(GREY if dissolve else DBLUE, 3)
    if dissolve:
        pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(4, 6, 56, 52), 6, 6)
    _box(p, 10, 24, 18, 18, 7)
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor("#f7d774"))
    p.drawEllipse(QRectF(32, 30, 20, 20))
    if not dissolve:
        p.setPen(_pen(ORANGE, 3))
        p.drawLine(QPointF(26, 36), QPointF(34, 40))


def _exit(p):
    _doc(p)
    p.setPen(_pen(RED, 5))
    p.drawLine(QPointF(30, 32), QPointF(60, 32))


def _cut(p):
    from PySide6.QtGui import QPen
    p.setPen(QPen(QColor("#5a6270"), S / 16))
    p.drawLine(int(S * .3), int(S * .1), int(S * .62), int(S * .62))
    p.drawLine(int(S * .7), int(S * .1), int(S * .38), int(S * .62))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#c0392b"), S / 14))
    r = int(S * .17)
    p.drawEllipse(int(S * .2), int(S * .62), r, r)
    p.drawEllipse(int(S * .63), int(S * .62), r, r)


def _mark(p, ok, x=36, y=34):
    if ok:
        p.setPen(_pen(GREEN, 6))
        p.drawPolyline(_poly((x, y + 14), (x + 8, y + 24), (x + 26, y + 2)))
    else:
        p.setPen(_pen(RED, 6))
        p.drawLine(QPointF(x + 2, y + 2), QPointF(x + 24, y + 24))
        p.drawLine(QPointF(x + 24, y + 2), QPointF(x + 2, y + 24))


def _eye_mark(p, ok, everything=False):
    p.save()
    p.translate(0, 8 if everything else 0)
    p.scale(0.85, 0.85)
    _eye(p)
    p.restore()
    if everything:
        p.setPen(_pen(DBLUE, 2))
        p.setBrush(LBLUE)
        for i in range(3):
            p.drawRect(QRectF(4 + 9 * i, 2, 7, 7))
    _mark(p, ok)


def _polyline_dlg(p):
    p.save()
    p.scale(0.75, 0.75)
    _polyline(p)
    p.restore()
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor("#ffffff"))
    p.drawRect(QRectF(34, 40, 28, 20))
    p.setBrush(DBLUE)
    for x in (40, 48, 56):
        p.drawEllipse(QRectF(x - 2, 48, 4, 4))


def _show_only(p):
    p.save()
    p.scale(0.85, 0.85)
    _eye(p)
    p.restore()
    p.setPen(_pen(PURPLE, 6))
    p.drawLine(QPointF(34, 46), QPointF(62, 46))
    p.drawLine(QPointF(34, 56), QPointF(62, 56))


def _overlay_vis(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor(156, 195, 238, 160))
    p.drawRect(QRectF(26, 2, 34, 26))
    p.setBrush(QColor(228, 138, 26, 170))
    p.drawRect(QRectF(34, 8, 18, 14))
    p.save()
    p.translate(0, 18)
    p.scale(0.7, 0.7)
    _eye(p)
    p.restore()
    _mark(p, True, 4, 4)


def _hf_eye(p, y=44):
    """eye: black outline, dark grey iris, lower half of the icon."""
    p.setPen(_pen(QColor("#111111"), 4))
    p.setBrush(QColor("#ffffff"))
    path = QPainterPath()
    path.moveTo(8, y)
    path.quadTo(32, y - 22, 56, y)
    path.quadTo(32, y + 22, 8, y)
    p.drawPath(path)
    p.setPen(_pen(QColor("#111111"), 2.5))
    p.setBrush(QColor("#4d5a5a"))
    p.drawEllipse(QRectF(22, y - 10, 20, 20))
    p.setBrush(QColor("#111111"))
    p.drawEllipse(QRectF(29, y - 3, 6, 6))


def _hf_check(p, c, x=4, y=6):
    p.setPen(_pen(c, 6))
    p.drawPolyline(_poly((x, y + 10), (x + 10, y + 20), (x + 34, y)))


def _hf_show(p, n=1, c=GREEN):
    _hf_eye(p)
    if n == 2:
        _hf_check(p, c, 4, 2)
        _hf_check(p, c, 18, 12)
    else:
        _hf_check(p, c)


def _hf_hide(p):
    _hf_eye(p)
    p.setPen(_pen(QColor("#e0262b"), 6))
    p.drawLine(QPointF(8, 4), QPointF(30, 26))
    p.drawLine(QPointF(30, 4), QPointF(8, 26))


def _hf_overlay(p):
    p.setPen(_pen(QColor("#3b6fb6"), 3))
    p.setBrush(QColor("#e9eef5"))
    p.drawRect(QRectF(4, 4, 56, 56))
    p.setBrush(QColor("#2f6db5"))
    p.drawRect(QRectF(4, 4, 56, 10))
    p.save()
    p.translate(6, 8)
    p.scale(0.85, 0.85)
    _hf_eye(p)
    p.restore()


def _triad(p, ox, oy, L, rot=0.0, w=3.5):
    import math
    for ang, c in ((rot, RED), (rot + 90, GREEN)):
        a = math.radians(ang)
        _arrow(p, ox, oy, ox + L * math.cos(a), oy - L * math.sin(a), c, w, 9)
    _arrow(p, ox, oy, ox - 0.55 * L, oy + 0.55 * L, BLUE, w, 9)


def _cs_rel(p):
    _triad(p, 14, 50, 22, 0, 2)
    _triad(p, 38, 30, 22, 25)


def _cs_face(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawPolygon(_poly((4, 50), (30, 62), (60, 40), (34, 28)))
    _triad(p, 32, 44, 22, 20)


def _cs_obj(p):
    _box(p, 6, 34, 30, 24, 12)
    _triad(p, 32, 40, 22, 15)


def _cs_global(p):
    _triad(p, 24, 40, 30)


def _helix(p):
    import math
    p.setPen(_pen(DBLUE, 3))
    pts = [(32 + 20 * math.cos(t / 6), 58 - 50 * t / 60 + 6 * math.sin(t / 6)) for t in range(0, 61)]
    p.drawPolyline(_poly(*pts))


def _spiral(p):
    import math
    p.setPen(_pen(DBLUE, 3))
    pts = [(32 + (2 + 26 * t / 60) * math.cos(t / 5), 32 + (2 + 26 * t / 60) * math.sin(t / 5)) for t in range(61)]
    p.drawPolyline(_poly(*pts))


def _bondwire(p):
    p.setPen(_pen(GREY, 2))
    p.setBrush(LGREY)
    p.drawRect(QRectF(2, 48, 18, 10))
    p.drawRect(QRectF(44, 48, 18, 10))
    p.setPen(_pen(ORANGE, 3.5))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(11, 48)
    path.cubicTo(11, 6, 40, 6, 53, 48)
    p.drawPath(path)


def _section(p):
    p.setPen(_pen(RED, 2))
    p.setBrush(QColor(200, 55, 45, 70))
    p.drawPolygon(_poly((6, 44), (30, 56), (58, 30), (34, 18)))
    _box(p, 22, 34, 20, 16, 8)


def _sweep_axis(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawPolygon(_poly((36, 50), (36, 26), (52, 22), (52, 46)))
    pen = _pen(GREY, 2)
    pen.setStyle(Qt.DashDotLine)
    p.setPen(pen)
    p.drawLine(QPointF(14, 4), QPointF(14, 62))
    p.setPen(_pen(GREEN, 3.5))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(2, 10, 26, 16), 200 * 16, 300 * 16)


def _sweep_path(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(QColor(156, 195, 238, 200))
    p.drawPolygon(_poly((4, 56), (4, 38), (16, 32), (16, 50)))
    pen = _pen(GREEN, 3.5)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(16, 44)
    path.cubicTo(36, 60, 30, 8, 58, 12)
    p.drawPath(path)


def _chamfer(p):
    p.setPen(_pen(DBLUE, 3))
    p.setBrush(_grad(LBLUE, BLUE))
    p.drawPolygon(_poly((8, 56), (8, 8), (34, 8), (56, 30), (56, 56)))


def _ruler(p):
    p.setPen(_pen(DBLUE, 2))
    p.setBrush(YELLOW)
    p.save()
    p.translate(32, 32)
    p.rotate(-35)
    p.drawRect(QRectF(-30, -9, 60, 18))
    for i in range(-24, 30, 8):
        p.drawLine(QPointF(i, -9), QPointF(i, -1 if i % 16 else 3))
    p.restore()


def _orbit(p):
    p.setPen(_pen(DBLUE, 2.5))
    p.setBrush(_grad(LBLUE, BLUE))
    p.drawEllipse(QRectF(20, 20, 24, 24))
    p.setBrush(Qt.NoBrush)
    p.setPen(_pen(GREEN, 3.5))
    p.drawArc(QRectF(4, 14, 56, 36), 30 * 16, 300 * 16)
    _arrow(p, 50, 18, 56, 24, GREEN, 3.5, 10)


def _chip_body(p, label_color=ORANGE):
    """SMD chip on two traces: the body with its two metal terminations."""
    p.setPen(_pen(GREY, 2))
    p.setBrush(LGREY)
    p.drawRect(QRectF(2, 44, 18, 8))
    p.drawRect(QRectF(44, 44, 18, 8))
    p.setPen(_pen(GREY.darker(130), 2))
    p.setBrush(QColor("#d8d8d8"))
    p.drawRect(QRectF(12, 30, 8, 18))
    p.drawRect(QRectF(44, 30, 8, 18))
    p.setBrush(label_color)
    p.drawRect(QRectF(20, 30, 24, 18))


def _resistor(p):
    _chip_body(p, QColor("#3a3a3a"))
    p.setPen(_pen(QColor("#f0f0f0"), 2))
    xs = [22, 25, 28, 31, 34, 37, 40, 42]
    pts = [(x, 39 + (4 if i % 2 else -4) * (0 < i < len(xs) - 1)) for i, x in enumerate(xs)]
    for a, b in zip(pts, pts[1:]):
        p.drawLine(QPointF(*a), QPointF(*b))


def _inductor(p):
    _chip_body(p, QColor("#f2e3c2"))
    p.setPen(_pen(QColor("#b5651d"), 2.2))
    p.setBrush(Qt.NoBrush)
    for i in range(4):
        p.drawArc(QRectF(21 + i * 5.5, 33, 7, 12), 0, 180 * 16)


def _capacitor(p):
    _chip_body(p, QColor("#c9a46a"))
    p.setPen(_pen(INK_C, 2.5))
    p.drawLine(QPointF(30, 33), QPointF(30, 45))
    p.drawLine(QPointF(34, 33), QPointF(34, 45))
    p.drawLine(QPointF(22, 39), QPointF(30, 39))
    p.drawLine(QPointF(34, 39), QPointF(42, 39))


def _chip(p):
    _chip_body(p, QColor("#5b7fa6"))
    p.setPen(_pen(QColor("#f0f0f0"), 1.6))
    p.drawText(QRectF(20, 30, 24, 18), Qt.AlignCenter, "S")


INK_C = QColor("#3b4558")


_DRAW = {
    "cs_rel": _cs_rel, "cs_face": _cs_face, "cs_global": _cs_global, "cs_obj": lambda p: _cs_obj(p),
    "show_sel": _hf_show, "hide_sel": _hf_hide,
    "show_all": lambda p: _hf_show(p, 2), "hide_all": lambda p: _eye_mark(p, False, True),
    "show_only": lambda p: _hf_show(p, 1, QColor("#b48ad0")), "overlay_vis": _hf_overlay, "polyline_dlg": _polyline_dlg, "helix": _helix, "spiral": _spiral, "bondwire": _bondwire, "section": _section,
    "sweep_axis": _sweep_axis, "sweep_path": _sweep_path, "chamfer": _chamfer, "ruler": _ruler, "orbit": _orbit,
    "cut": _cut,
    "box": _box, "cylinder": _cylinder, "sphere": _sphere, "cone": _cone, "torus": _torus,
    "polyhedron": _polyhedron, "rectangle": _rect, "circle": _circle, "ellipse": _ellipse, "polygon": _polygon,
    "line": _line, "polyline": _polyline, "arc3": _arc3, "arc_center": _arc_center,
    "unite": lambda p: _boolean(p, "unite"), "subtract": lambda p: _boolean(p, "subtract"),
    "intersect": lambda p: _boolean(p, "intersect"), "split": _split,
    "move": _move, "rotate": _rotate, "mirror": _mirror, "dup_line": _dup_line, "dup_axis": _dup_axis,
    "dup_mirror": _mirror, "fit": _fit, "fitsel": lambda p: _fit(p, True), "zoom": _zoom, "pan": _pan,
    "iso": lambda p: _orient(p, "iso"), "top": lambda p: _orient(p, "top"), "front": lambda p: _orient(p, "front"),
    "right": lambda p: _orient(p, "right"), "orient": lambda p: _orient(p, ""),
    "show": _eye, "hide": lambda p: _eye(p, True), "wire": _wire, "grid": _grid, "snap": _snap, "nonmodel": _nonmodel,
    "sel_object": lambda p: _cursor(p, "object"), "sel_face": lambda p: _cursor(p, "face"),
    "sel_edge": lambda p: _cursor(p, "edge"), "sel_vertex": lambda p: _cursor(p, "vertex"),
    "new": _new, "open": _open, "save": _save, "saveas": _save, "undo": _undo, "redo": lambda p: _undo(p, True),
    "copy": _copy, "paste": _paste, "delete": _delete, "options": _gear, "material": _material, "units": _units,
    "measure": _measure, "region": _region, "import": _import, "fillet": _fillet, "sweep": _sweep,
    "thicken": _thicken, "cover": _cover, "solution_type": _solution_type, "setup": _setup, "freq_sweep": _sweep_f,
    "boundary": _boundary, "radiation": lambda p: _boundary(p, "rad"), "pml": lambda p: _boundary(p, "pml"),
    "perfe": lambda p: _boundary(p, "pec"), "auto_open": lambda p: _boundary(p, "auto"),
    "waveport": _port, "lumpedport": lambda p: _port(p, True), "mesh": _mesh, "plot_mesh": lambda p: _mesh(p, True),
    "validate": _validate, "analyze": _analyze, "farfield": _sphere_ff,
    "rect_plot": _report, "smith": lambda p: _report(p, "smith"), "table": lambda p: _report(p, "table"),
    "pattern": lambda p: _report(p, "pattern"), "polar3d": lambda p: _report(p, "polar3d"),
    "field": _field, "calculator": _calculator, "field_vec": lambda p: _field(p, "vec"), "field_h": lambda p: _field(p, "h"),
    "animate": _animate, "clear": _clear, "soldata": _soldata, "export": _export, "design": _design, "layout_back": _layout_back, "layout_fwd": _layout_fwd,
    "variables": _variables, "screenshot": _screenshot, "help": _help, "projection": _projection,
    "byname": _byname, "selall": _selall, "group": _group, "ungroup": lambda p: _group(p, True), "exit": _exit, "filter": _filter, "antenna": _antenna,
    "resistor": _resistor, "inductor": _inductor, "capacitor": _capacitor, "chip": _chip,
}


INK = (0x3b, 0x45, 0x58)      # flat icon style: slate lines ...
PAPER = (0xe9, 0xed, 0xf3)    # ... light fills ...
ACCENT = (0xc9, 0x95, 0x2f)   # ... and gold where the drawing used a warm signal colour


def _flat(pm: QPixmap, ink=INK) -> QPixmap:
    """Turn a colourful drawing into the flat Hertzon style: dark parts become slate lines, light parts
    pale fills, saturated warm colours (red, orange, yellow) the gold accent."""
    import numpy as np
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4).copy()
    b, g, r = (a[..., i].astype(float) for i in range(3))
    alpha = a[..., 3].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        un = np.where(alpha > 0, 255.0 / alpha, 0.0)       # premultiplied -> straight colour
    r, g, b = r * un, g * un, b * un
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    warm = (sat > 0.45) & (r >= g) & (r > b + 40)
    t = np.clip((lum - 0.5) / 0.4, 0, 1)[..., None]
    out = (1 - t) * np.array(ink, float) + t * np.array(PAPER, float)
    out[warm] = ACCENT
    if tuple(ink) == (255, 255, 255):                  # icon on a coloured button: plain white
        out[:] = 255.0
    out *= (alpha / 255.0)[..., None]                   # back to premultiplied
    a[..., 2], a[..., 1], a[..., 0] = out[..., 0], out[..., 1], out[..., 2]
    res = QImage(a.data, img.width(), img.height(), img.bytesPerLine(), QImage.Format_ARGB32_Premultiplied).copy()
    return QPixmap.fromImage(res)


@lru_cache(maxsize=None)
def icon(name: str, ink: tuple | None = None) -> QIcon:
    fn = _DRAW.get(name)
    pm = QPixmap(S, S)
    pm.fill(Qt.transparent)
    if fn is not None:
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        fn(p)
        p.end()
        pm = _flat(pm, ink or INK)
    return QIcon(pm)


def names() -> list[str]:
    return sorted(_DRAW)
