"""Gerber reader (RS-274X and X2, also the deprecated parts still found in older CAM output).

The image of a file is built as 2D polygons with shapely, in metres, in the coordinates of the file:

* apertures: the standard ones (C, R, O, P, each with an optional round hole), aperture macros
  (primitives 1, 2/20, 21, 22, 4, 5, 6, 7 with arithmetic and variables) and block apertures (AB);
* D03 flashes, D01 draws with a round (or, as some old files have, a rectangular) aperture, G02/G03 arcs in
  multi quadrant (G75) and single quadrant (G74) mode, G36/G37 regions with several contours and cut-ins;
* dark and clear polarity (LP), the object transformations LM, LR and LS, step and repeat (SR);
* deprecated: G70/G71 units, G90/G91, G54/G55 prefixes, the old MI/OF/SF/IP image parameters.

Arcs and circles are approximated by straight segments (``segments`` per full circle); the import report
says so. Nothing of the file is silently dropped: what cannot be read becomes a message.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field

import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

UNIT = {"MM": 1e-3, "IN": 25.4e-3}
DEFAULT_SEGMENTS = 48


class GerberError(ValueError):
    pass


@dataclass
class Msg:
    level: str          # "info", "warning", "error"
    text: str


@dataclass
class Image:
    """What a Gerber file holds: its polygons (metres, file coordinates) and what the file says of itself."""
    geometry: BaseGeometry
    attributes: dict[str, list[str]] = field(default_factory=dict)   # file attributes (TF), values split
    flashes: list[tuple[float, float, float]] = field(default_factory=list)   # round flashes (x, y, d), m
    messages: list[Msg] = field(default_factory=list)
    unit: str = "MM"
    comments: list[str] = field(default_factory=list)
    line_width: float = 0.0     # the most used width of drawn lines (m)

    @property
    def function(self) -> list[str]:
        return self.attributes.get(".FileFunction", [])

    def polygons(self) -> list[Polygon]:
        return polygons_of(self.geometry)


def polygons_of(g: BaseGeometry | None) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    if hasattr(g, "geoms"):
        return [p for q in g.geoms for p in polygons_of(q)]
    return []


# ------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------
def _circle(x: float, y: float, d: float, n: int) -> Polygon:
    r = d / 2
    if r <= 0:
        return Polygon()
    return Polygon([(x + r * math.cos(2 * math.pi * k / n), y + r * math.sin(2 * math.pi * k / n))
                    for k in range(n)])


def _clean(g: BaseGeometry) -> BaseGeometry:
    if g.is_empty:
        return g
    if not g.is_valid:
        g = shapely.make_valid(g)
    return shapely.union_all(polygons_of(g)) if not isinstance(g, (Polygon, MultiPolygon)) else g


def _arc_points(p0, p1, c, ccw: bool, n: int) -> list[tuple[float, float]]:
    """Points from p0 to p1 around centre c (p0 excluded, p1 included); equal ends = full circle."""
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    r = math.hypot(p0[0] - c[0], p0[1] - c[1])
    sweep = a1 - a0
    if ccw:
        while sweep <= 1e-12:
            sweep += 2 * math.pi
        if abs(p0[0] - p1[0]) + abs(p0[1] - p1[1]) > 1e-12 and sweep > 2 * math.pi - 1e-9:
            sweep -= 2 * math.pi
    else:
        while sweep >= -1e-12:
            sweep -= 2 * math.pi
        if abs(p0[0] - p1[0]) + abs(p0[1] - p1[1]) > 1e-12 and sweep < -2 * math.pi + 1e-9:
            sweep += 2 * math.pi
    k = max(1, int(math.ceil(abs(sweep) / (2 * math.pi) * n)))
    pts = [(c[0] + r * math.cos(a0 + sweep * i / k), c[1] + r * math.sin(a0 + sweep * i / k)) for i in range(1, k)]
    return pts + [tuple(p1)]


_NUM = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _macro_eval(expr: str, var: dict[int, float]) -> float:
    s = expr.strip().replace("x", "*").replace("X", "*")
    s = re.sub(r"\$(\d+)", lambda m: repr(var.get(int(m.group(1)), 0.0)), s)
    if not s:
        return 0.0
    tree = ast.parse(s, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub,
                                 ast.Mult, ast.Div, ast.USub, ast.UAdd)):
            raise GerberError(f"Unsupported expression in aperture macro: '{expr}'")
    return float(eval(compile(tree, "<macro>", "eval"), {"__builtins__": {}}, {}))


def _rot(g: BaseGeometry, deg: float) -> BaseGeometry:
    return affinity.rotate(g, deg, origin=(0, 0)) if deg else g


# ------------------------------------------------------------------------------------------------
# apertures
# ------------------------------------------------------------------------------------------------
@dataclass
class Aperture:
    shape: BaseGeometry                  # at the origin, file units
    kind: str                            # C, R, O, P, macro name or "block"
    diameter: float = 0.0                # C: diameter; R: width
    height: float = 0.0                  # R: height
    block: list | None = None            # block aperture: its objects


def _hole(shape: BaseGeometry, d: float, n: int) -> BaseGeometry:
    return shape.difference(_circle(0, 0, d, n)) if d > 0 else shape


def standard_aperture(kind: str, params: list[float], n: int) -> Aperture:
    p = params + [0.0] * 6
    if kind == "C":
        return Aperture(_hole(_circle(0, 0, p[0], n), p[1], n), "C", p[0])
    if kind == "R":
        return Aperture(_hole(box(-p[0] / 2, -p[1] / 2, p[0] / 2, p[1] / 2), p[2], n), "R", p[0], p[1])
    if kind == "O":
        w, h = p[0], p[1]
        if abs(w - h) < 1e-12:
            s = _circle(0, 0, w, n)
        elif w > h:
            s = LineString([(-(w - h) / 2, 0), ((w - h) / 2, 0)]).buffer(h / 2, quad_segs=max(2, n // 4))
        else:
            s = LineString([(0, -(h - w) / 2), (0, (h - w) / 2)]).buffer(w / 2, quad_segs=max(2, n // 4))
        return Aperture(_hole(s, p[2], n), "O", w, h)
    if kind == "P":
        d, nv, rot = p[0], int(round(p[1])), p[2]
        if nv < 3:
            raise GerberError(f"A polygon aperture needs 3 or more vertices, got {nv}")
        s = Polygon([(d / 2 * math.cos(2 * math.pi * k / nv + math.radians(rot)),
                      d / 2 * math.sin(2 * math.pi * k / nv + math.radians(rot))) for k in range(nv)])
        return Aperture(_hole(s, p[3], n), "P", d)
    raise GerberError(f"Unknown aperture type '{kind}'")


def macro_aperture(name: str, body: list[str], params: list[float], n: int, msgs: list[Msg]) -> Aperture:
    var = {i + 1: v for i, v in enumerate(params)}
    shape: BaseGeometry = Polygon()
    for blk in body:
        blk = blk.strip()
        if not blk or blk.startswith("G04") or (blk[0] == "0" and not blk[1:2].isdigit() and blk[1:2] != "."):
            continue        # empty or a comment (primitive 0)
        m = re.match(r"^\$(\d+)\s*=(.*)$", blk)
        if m:
            var[int(m.group(1))] = _macro_eval(m.group(2), var)
            continue
        parts = [s.strip() for s in blk.split(",")]
        try:
            code = int(parts[0])
        except ValueError:
            msgs.append(Msg("warning", f"Aperture macro {name}: cannot read '{blk}', ignored"))
            continue
        a = [_macro_eval(x, var) for x in parts[1:]]
        prim: BaseGeometry
        exposure = 1
        if code == 1:                      # circle: exposure, diameter, x, y[, rotation]
            a += [0.0] * (5 - len(a))
            exposure = int(a[0])
            prim = _rot(_circle(a[2], a[3], a[1], n), a[4])
        elif code in (2, 20):              # vector line: exposure, width, xs, ys, xe, ye, rotation
            a += [0.0] * (7 - len(a))
            exposure = int(a[0])
            w, xs, ys, xe, ye = a[1:6]
            L = math.hypot(xe - xs, ye - ys)
            if L <= 0 or w <= 0:
                continue
            ux, uy = (ye - ys) / L * w / 2, -(xe - xs) / L * w / 2
            prim = _rot(Polygon([(xs + ux, ys + uy), (xe + ux, ye + uy), (xe - ux, ye - uy), (xs - ux, ys - uy)]), a[6])
        elif code == 21:                   # centre line: exposure, width, height, x, y, rotation
            a += [0.0] * (6 - len(a))
            exposure = int(a[0])
            w, h, x, y = a[1:5]
            prim = _rot(box(x - w / 2, y - h / 2, x + w / 2, y + h / 2), a[5])
        elif code == 22:                   # lower left line (old): exposure, width, height, x, y, rotation
            a += [0.0] * (6 - len(a))
            exposure = int(a[0])
            w, h, x, y = a[1:5]
            prim = _rot(box(x, y, x + w, y + h), a[5])
        elif code == 4:                    # outline: exposure, n, x0, y0, ..., xn, yn, rotation
            exposure = int(a[0])
            k = int(round(a[1]))
            pts = [(a[2 + 2 * i], a[3 + 2 * i]) for i in range(k + 1)]
            rot = a[4 + 2 * k] if len(a) > 4 + 2 * k else 0.0
            prim = _rot(_clean(Polygon(pts)), rot)
        elif code == 5:                    # polygon: exposure, vertices, x, y, diameter, rotation
            a += [0.0] * (6 - len(a))
            exposure = int(a[0])
            k = max(3, int(round(a[1])))
            x, y, d = a[2], a[3], a[4]
            prim = _rot(Polygon([(x + d / 2 * math.cos(2 * math.pi * i / k), y + d / 2 * math.sin(2 * math.pi * i / k))
                                 for i in range(k)]), a[5])
        elif code == 6:                    # moire: x, y, outer d, ring thickness, gap, rings, cross t, cross l, rot
            a += [0.0] * (9 - len(a))
            x, y, d, t, gap, rings, ct, cl, rot = a[:9]
            g: BaseGeometry = Polygon()
            for i in range(int(round(rings))):
                do = d - 2 * i * (t + gap)
                if do <= 0:
                    break
                g = g.union(_circle(x, y, do, n).difference(_circle(x, y, max(0.0, do - 2 * t), n)))
            g = g.union(box(x - cl / 2, y - ct / 2, x + cl / 2, y + ct / 2)).union(
                box(x - ct / 2, y - cl / 2, x + ct / 2, y + cl / 2))
            prim = _rot(g, rot)
        elif code == 7:                    # thermal: x, y, outer d, inner d, gap, rotation
            a += [0.0] * (6 - len(a))
            x, y, do, di, gap, rot = a[:6]
            ring = _circle(x, y, do, n).difference(_circle(x, y, di, n))
            cut = box(x - do, y - gap / 2, x + do, y + gap / 2).union(box(x - gap / 2, y - do, x + gap / 2, y + do))
            prim = _rot(ring.difference(cut), rot)
        else:
            msgs.append(Msg("warning", f"Aperture macro {name}: unknown primitive {code}, ignored"))
            continue
        shape = shape.union(prim) if exposure else shape.difference(prim)
    return Aperture(_clean(shape), name)


# ------------------------------------------------------------------------------------------------
# reader
# ------------------------------------------------------------------------------------------------
def _blocks(text: str):
    """Yield ("ext", [words]) for each %...% statement and ("data", word) for each data word."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \r\n\t":
            i += 1
            continue
        if ch == "%":
            j = text.find("%", i + 1)
            if j < 0:
                raise GerberError("Unterminated extended command (missing '%')")
            body = text[i + 1:j]
            words = [w.strip() for w in body.replace("\r", "").replace("\n", "").split("*")]
            yield "ext", [w for w in words if w]
            i = j + 1
            continue
        j = text.find("*", i)
        if j < 0:
            rest = text[i:].strip()
            if rest:
                yield "data", rest
            return
        word = text[i:j].replace("\r", "").replace("\n", "").strip()
        if word.startswith("G04") or word.startswith("G4 "):
            yield "comment", word
        elif word:
            yield "data", word
        i = j + 1


@dataclass
class _Obj:
    dark: bool
    geom: BaseGeometry


class _Reader:
    def __init__(self, segments: int):
        self.n = max(8, int(segments))
        self.msgs: list[Msg] = []
        self.unit: str | None = None
        self.fs: tuple[str, int, int, int, int, bool] | None = None   # zeros, xi, xd, yi, yd, incremental
        self.aps: dict[int, Aperture] = {}
        self.macros: dict[str, list[str]] = {}
        self.attrs: dict[str, list[str]] = {}
        self.comments: list[str] = []
        self.flashes: list[tuple[float, float, float]] = []
        self.x = self.y = 0.0
        self.ap: int | None = None
        self.interp = "G01"
        self.quadrant = "G75"
        self.op = "D02"
        self.dark = True
        self.mirror = "N"
        self.rotation = 0.0
        self.scale = 1.0
        self.region: list[list[tuple[float, float]]] | None = None
        self.contour: list[tuple[float, float]] = []
        self.out: list[list[_Obj]] = [[]]     # stack: image, then open SR / AB blocks
        self.block_ids: list[int | None] = [None]
        self.sr: tuple[int, int, float, float] | None = None
        self.ended = False
        self.image_neg = False
        self.legacy_offset = (0.0, 0.0)
        self.legacy_scale = (1.0, 1.0)
        self.legacy_mirror = (False, False)
        self.used_legacy: set[str] = set()
        self.widths: dict[float, int] = {}

    # ---- coordinates ---------------------------------------------------------------------------
    def _coord(self, s: str, axis: str) -> float:
        if self.fs is None:
            self.fs = ("L", 3, 6, 3, 6, False)
            self.msgs.append(Msg("warning", "No format statement (FS) before the first coordinate: read as 3.6"))
        zeros, xi, xd, yi, yd, _ = self.fs
        ip, dp = (xi, xd) if axis in "XI" else (yi, yd)
        if "." in s:
            return float(s)
        neg = s.startswith("-")
        digits = s.lstrip("+-")
        if zeros == "T":
            digits = digits.ljust(ip + dp, "0")
        v = int(digits or "0") / 10 ** dp
        return -v if neg else v

    def _u(self) -> str:
        if self.unit is None:
            self.unit = "IN"
            self.msgs.append(Msg("warning", "No unit statement (MO): the file is read in inch, the Gerber default"))
        return self.unit

    # ---- output --------------------------------------------------------------------------------
    def _emit(self, g: BaseGeometry) -> None:
        if not g.is_empty:
            self.out[-1].append(_Obj(self.dark, g))

    def _transform_ap(self, g: BaseGeometry) -> BaseGeometry:
        if self.mirror != "N":
            g = affinity.scale(g, -1 if "X" in self.mirror else 1, -1 if "Y" in self.mirror else 1, origin=(0, 0))
        if self.rotation:
            g = affinity.rotate(g, self.rotation, origin=(0, 0))
        if self.scale != 1.0:
            g = affinity.scale(g, self.scale, self.scale, origin=(0, 0))
        return g

    def _flash(self, x: float, y: float) -> None:
        ap = self._aperture()
        if ap is None:
            return
        if ap.block is not None:
            for o in ap.block:
                g = affinity.translate(self._transform_ap(o.geom), x, y)
                # a clear object of the block stays clear, the block as a whole takes the current polarity
                self.out[-1].append(_Obj(o.dark == self.dark, g))
            return
        self._emit(affinity.translate(self._transform_ap(ap.shape), x, y))
        if ap.kind == "C" and len(self.out) == 1:
            self.flashes.append((x, y, ap.diameter * self.scale))

    def _aperture(self) -> Aperture | None:
        if self.ap is None:
            self.msgs.append(Msg("error", "Draw or flash without a selected aperture: ignored"))
            return None
        ap = self.aps.get(self.ap)
        if ap is None:
            self.msgs.append(Msg("error", f"Aperture D{self.ap} is not defined: object ignored"))
        return ap

    def _draw(self, pts: list[tuple[float, float]]) -> None:
        ap = self._aperture()
        if ap is None:
            return
        if ap.kind == "C":
            d = ap.diameter * self.scale
            if d <= 0:
                self.msgs.append(Msg("warning", "Draw with a zero size aperture (no copper): ignored"))
                return
            if len(pts) < 2 or all(abs(p[0] - pts[0][0]) + abs(p[1] - pts[0][1]) < 1e-15 for p in pts):
                self._emit(_circle(pts[0][0], pts[0][1], d, self.n))
            else:
                self._emit(LineString(pts).buffer(d / 2, quad_segs=max(2, self.n // 4)))
                self.widths[d] = self.widths.get(d, 0) + 1
            return
        if ap.kind == "R":
            # rectangle drawn along a path (deprecated, but found in old files): the swept area
            w, h = ap.diameter * self.scale, ap.height * self.scale
            parts = []
            for a, b in zip(pts, pts[1:]):
                r = [box(a[0] - w / 2, a[1] - h / 2, a[0] + w / 2, a[1] + h / 2),
                     box(b[0] - w / 2, b[1] - h / 2, b[0] + w / 2, b[1] + h / 2)]
                parts.append(shapely.union_all(r).convex_hull)
            self._emit(shapely.union_all(parts) if parts else box(pts[0][0] - w / 2, pts[0][1] - h / 2,
                                                                   pts[0][0] + w / 2, pts[0][1] + h / 2))
            self.used_legacy.add("draws with a rectangular aperture")
            return
        self.msgs.append(Msg("error", f"Draw with aperture D{self.ap} of type {ap.kind}: only round apertures "
                                      "can draw lines. The draw is ignored."))

    # ---- statements ----------------------------------------------------------------------------
    def ext(self, words: list[str]) -> None:
        w0 = words[0]
        code = w0[:2]
        if code == "FS":
            m = re.match(r"FS([LTD]?)([AI]?)(?:N\d+)?(?:G\d+)?X(\d)(\d)Y(\d)(\d)", w0)
            if not m:
                raise GerberError(f"Cannot read the format statement '{w0}'")
            zeros = m.group(1) or "L"
            if zeros == "D":
                zeros = "L"
            self.fs = (zeros, int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)), m.group(2) == "I")
            if m.group(2) == "I":
                self.used_legacy.add("incremental coordinates")
            if zeros == "T":
                self.used_legacy.add("trailing zero omission")
        elif code == "MO":
            self.unit = "MM" if "MM" in w0 else "IN"
        elif code == "AD":
            m = re.match(r"ADD(\d+)([A-Za-z_.$][^,]*)(?:,(.*))?$", w0)
            if not m:
                self.msgs.append(Msg("error", f"Cannot read the aperture definition '{w0}'"))
                return
            num, kind, par = int(m.group(1)), m.group(2), m.group(3)
            params = [float(v) for v in par.split("X")] if par else []
            try:
                if kind in ("C", "R", "O", "P"):
                    self.aps[num] = standard_aperture(kind, params, self.n)
                elif kind in self.macros:
                    self.aps[num] = macro_aperture(kind, self.macros[kind], params, self.n, self.msgs)
                else:
                    self.msgs.append(Msg("error", f"Aperture D{num} uses the undefined macro '{kind}'"))
            except (GerberError, SyntaxError, ValueError) as e:
                self.msgs.append(Msg("error", f"Aperture D{num}: {e}"))
        elif code == "AM":
            self.macros[w0[2:]] = words[1:]
        elif code == "AB":
            if len(w0) > 2:
                self.out.append([])
                self.block_ids.append(int(w0[3:]))
            elif len(self.out) > 1 and self.block_ids[-1] is not None:
                objs = self.out.pop()
                num = self.block_ids.pop()
                self.aps[num] = Aperture(shapely.union_all([o.geom for o in objs if o.dark]) if objs else Polygon(),
                                         "block", block=objs)
        elif code == "SR":
            if self.sr is not None:        # closes the open step and repeat
                nx, ny, dx, dy = self.sr
                objs = self.out.pop()
                self.block_ids.pop()
                for i in range(nx):
                    for j in range(ny):
                        for o in objs:
                            self.out[-1].append(_Obj(o.dark, affinity.translate(o.geom, i * dx, j * dy)))
                self.sr = None
            m = re.match(r"SR(?:X(\d+))?(?:Y(\d+))?(?:I([\d.+-]+))?(?:J([\d.+-]+))?", w0)
            if m and any(m.groups()) and (int(m.group(1) or 1) > 1 or int(m.group(2) or 1) > 1):
                self.sr = (int(m.group(1) or 1), int(m.group(2) or 1), float(m.group(3) or 0), float(m.group(4) or 0))
                self.out.append([])
                self.block_ids.append(None)
        elif code == "LP":
            self.dark = w0[2:3] != "C"
        elif code == "LM":
            self.mirror = w0[2:] or "N"
        elif code == "LR":
            self.rotation = float(w0[2:] or 0)
        elif code == "LS":
            self.scale = float(w0[2:] or 1)
        elif code in ("TF", "TA", "TO", "TD"):
            if code == "TF":
                parts = w0[2:].split(",")
                self.attrs[parts[0]] = parts[1:]
        elif code == "IP":
            self.image_neg = "NEG" in w0
        elif code == "OF":
            m = re.match(r"OF(?:A([\d.+-]+))?(?:B([\d.+-]+))?", w0)
            self.legacy_offset = (float(m.group(1) or 0), float(m.group(2) or 0)) if m else (0.0, 0.0)
            if any(self.legacy_offset):
                self.used_legacy.add("image offset (OF)")
        elif code == "SF":
            m = re.match(r"SF(?:A([\d.+-]+))?(?:B([\d.+-]+))?", w0)
            self.legacy_scale = (float(m.group(1) or 1), float(m.group(2) or 1)) if m else (1.0, 1.0)
            if self.legacy_scale != (1.0, 1.0):
                self.used_legacy.add("scale factor (SF)")
        elif code == "MI":
            m = re.match(r"MI(?:A(\d))?(?:B(\d))?", w0)
            self.legacy_mirror = (bool(int(m.group(1) or 0)), bool(int(m.group(2) or 0))) if m else (False, False)
            if any(self.legacy_mirror):
                self.used_legacy.add("image mirroring (MI)")
        elif code in ("IN", "LN", "IR", "AS", "KO", "IJ", "IO", "PF", "RO"):
            if code in ("IR", "AS", "KO", "IJ", "IO") and w0[2:] not in ("", "0", "AXBY"):
                self.msgs.append(Msg("warning", f"Old image parameter '{w0}' is not supported and ignored"))
        else:
            self.msgs.append(Msg("warning", f"Unknown extended command '{w0}' ignored"))

    def data(self, word: str) -> None:
        if self.ended:
            return
        w = word
        # G codes in front (a block may carry several: G01X..Y..D01, G54D10, G75 ...)
        for g in re.findall(r"G0*(\d+)", w.split("X")[0].split("Y")[0].split("D")[0]):
            self._g(int(g))
        m = re.search(r"D0*(\d+)$", w)
        d = int(m.group(1)) if m else None
        if d is not None and d >= 10:
            self.ap = d
            return
        if re.search(r"M0*2$", w) or w in ("M00", "M01", "M0", "M1"):
            self.ended = True
            return
        coords = dict(re.findall(r"([XYIJ])([+-]?[\d.]+)", w))
        if d is None and not coords:
            return
        op = f"D0{d}" if d in (1, 2, 3) else self.op
        if d is None:
            self.used_legacy.add("coordinates without an operation code")
        self.op = op
        nx, ny = self.x, self.y
        inc = self.fs[5] if self.fs else False
        if "X" in coords:
            v = self._coord(coords["X"], "X")
            nx = self.x + v if inc else v
        if "Y" in coords:
            v = self._coord(coords["Y"], "Y")
            ny = self.y + v if inc else v
        i = self._coord(coords["I"], "I") if "I" in coords else 0.0
        j = self._coord(coords["J"], "J") if "J" in coords else 0.0
        self._u()
        if op == "D02":
            if self.region is not None:
                self._close_contour()
                self.contour = [(nx, ny)]
        elif op == "D01":
            pts = self._path(nx, ny, i, j)
            if self.region is not None:
                if not self.contour:
                    self.contour = [(self.x, self.y)]
                self.contour += pts
            else:
                self._draw([(self.x, self.y)] + pts)
        elif op == "D03":
            if self.region is not None:
                self.msgs.append(Msg("error", "Flash inside a region statement: ignored"))
            else:
                self._flash(nx, ny)
        self.x, self.y = nx, ny

    def _path(self, nx, ny, i, j) -> list[tuple[float, float]]:
        if self.interp == "G01":
            return [(nx, ny)]
        ccw = self.interp == "G03"
        p0, p1 = (self.x, self.y), (nx, ny)
        if self.quadrant == "G75":
            return _arc_points(p0, p1, (self.x + i, self.y + j), ccw, self.n)
        # single quadrant: offsets without sign, the arc is at most 90 degrees
        best = None
        for sx in (1, -1):
            for sy in (1, -1):
                c = (self.x + sx * abs(i), self.y + sy * abs(j))
                r0, r1 = math.hypot(p0[0] - c[0], p0[1] - c[1]), math.hypot(p1[0] - c[0], p1[1] - c[1])
                a0, a1 = math.atan2(p0[1] - c[1], p0[0] - c[0]), math.atan2(p1[1] - c[1], p1[0] - c[0])
                sw = (a1 - a0) % (2 * math.pi) if ccw else (a0 - a1) % (2 * math.pi)
                if sw <= math.pi / 2 + 1e-6:
                    err = abs(r0 - r1)
                    if best is None or err < best[0]:
                        best = (err, c)
        if best is None:
            self.msgs.append(Msg("warning", "Single quadrant arc without a valid centre: drawn as a line"))
            return [(nx, ny)]
        if abs(p0[0] - p1[0]) + abs(p0[1] - p1[1]) < 1e-15:
            return [(nx, ny)]
        return _arc_points(p0, p1, best[1], ccw, self.n)

    def _close_contour(self) -> None:
        if len(self.contour) >= 3 and self.region is not None:
            self.region.append(self.contour)
        self.contour = []

    def _g(self, g: int) -> None:
        if g in (1, 2, 3):
            self.interp = f"G0{g}"
        elif g == 74:
            self.quadrant = "G74"
        elif g == 75:
            self.quadrant = "G75"
        elif g == 36:
            self.region = []
            self.contour = []
        elif g == 37:
            if self.region is not None:
                self._close_contour()
                polys = []
                for c in self.region:
                    pg = Polygon(c)
                    polys += polygons_of(_clean(pg) if not pg.is_valid else pg)
                if polys:
                    self._emit(shapely.union_all(polys))
            self.region = None
        elif g == 70:
            self.unit = "IN"
            self.used_legacy.add("G70/G71 units")
        elif g == 71:
            self.unit = "MM"
            self.used_legacy.add("G70/G71 units")
        elif g == 91:
            if self.fs:
                self.fs = self.fs[:5] + (True,)
            self.used_legacy.add("incremental coordinates")
        elif g == 90:
            if self.fs:
                self.fs = self.fs[:5] + (False,)
        elif g in (4, 54, 55, 10, 11, 12):
            if g in (10, 11, 12):
                self.interp = "G01"
                self.used_legacy.add("scaled linear interpolation (G10-G12, read as G01)")
        else:
            self.msgs.append(Msg("warning", f"Unknown G code G{g:02d} ignored"))

    # ---- result --------------------------------------------------------------------------------
    def image(self) -> BaseGeometry:
        while len(self.out) > 1:
            self.msgs.append(Msg("warning", "Unclosed step and repeat or block aperture at the end of the file"))
            objs = self.out.pop()
            self.block_ids.pop()
            self.out[-1].extend(objs)
        cur: BaseGeometry = Polygon()
        run: list[BaseGeometry] = []
        dark = True
        for o in self.out[0] + [_Obj(not dark, Polygon())]:
            if o.dark != dark and run:
                g = shapely.union_all(run)
                cur = cur.union(g) if dark else cur.difference(g)
                run = []
            dark = o.dark
            run.append(o.geom)
        if run:
            g = shapely.union_all(run)
            cur = cur.union(g) if dark else cur.difference(g)
        return _clean(cur)


def read(text: str, segments: int = DEFAULT_SEGMENTS) -> Image:
    """The image of a Gerber file (text) in metres."""
    r = _Reader(segments)
    for kind, val in _blocks(text):
        if kind == "ext":
            r.ext(val)
        elif kind == "comment":
            r.comments.append(val[3:].strip())
        else:
            r.data(val)
    if not r.ended:
        r.msgs.append(Msg("warning", "The file has no end statement (M02): it may be cut off"))
    geom = r.image()
    unit = r.unit or "IN"
    s = UNIT[unit]
    ox, oy = r.legacy_offset
    sx, sy = r.legacy_scale
    mx, my = r.legacy_mirror
    geom = affinity.scale(geom, s * sx * (-1 if mx else 1), s * sy * (-1 if my else 1), origin=(0, 0))
    if ox or oy:
        geom = affinity.translate(geom, ox * s, oy * s)
    if r.image_neg:
        r.msgs.append(Msg("error", "The file is a negative image (IPNEG): it is not loaded"))
        geom = Polygon()
    flashes = [(x * s * sx * (-1 if mx else 1) + ox * s, y * s * sy * (-1 if my else 1) + oy * s, d * s)
               for x, y, d in r.flashes]
    for what in sorted(r.used_legacy):
        r.msgs.append(Msg("info", f"The file uses deprecated Gerber: {what}"))
    lw = max(r.widths, key=r.widths.get) * s if r.widths else 0.0
    return Image(geom, r.attrs, flashes, r.msgs, unit, r.comments, lw)


def read_file(path, segments: int = DEFAULT_SEGMENTS) -> Image:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return read(fh.read(), segments)


def looks_like_gerber(text: str) -> bool:
    head = text[:4000]
    return ("%FS" in head or "%MO" in head or "%ADD" in head or "G04" in head) and "M48" not in head[:200]


def outline_polygons(img: Image, segments: int = DEFAULT_SEGMENTS) -> list[Polygon]:
    """The board shape of a profile (outline) layer, in metres. Profiles are drawn as lines: the board edge
    is the centre of the drawn line. The areas the lines enclose are nested even-odd (board, cut-out,
    island in a cut-out, ...). A profile drawn as filled regions (no enclosed areas) is the board itself."""
    strokes = img.polygons()
    holes = [Polygon(r) for p in strokes for r in p.interiors]
    if not holes:
        return polygons_of(_clean(shapely.union_all(strokes))) if strokes else []
    w = img.line_width
    holes = [h.buffer(w / 2, quad_segs=max(2, segments // 4), join_style="mitre") if w > 0 else h for h in holes]
    out: BaseGeometry = Polygon()
    for h in sorted(holes, key=lambda q: -q.area):
        out = out.symmetric_difference(h)
    return polygons_of(_clean(out))
