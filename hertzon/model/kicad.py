"""KiCad footprints (.kicad_mod) and symbols (.kicad_sym): a tolerant reader.

The library files of KiCad (version 6 and later), and the downloads of SamacSys (Component Search Engine), SnapEDA,
Ultra Librarian and the distributors, are S-expressions. Only what a layout needs is read:

- a footprint: its pads (number, SMD or plated through, shape, position, rotation, size, drill, layers, the
  polygon of a custom pad), the courtyard and fabrication outline, the 3D model reference and the description;
- a symbol: the pin number and name of every pin, of all units (a derived symbol takes the pins of the symbol it
  extends).

Unknown tokens are skipped, so newer file versions are read as long as these items keep their form.

Coordinates are converted to a y axis that points up (KiCad's y points down), in millimetres, seen from the board
top: x, y of a pad and the points of an outline are (x_kicad, -y_kicad); an angle stays the same number, it turns
counter-clockwise as seen from the top in both systems.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path


class KicadError(Exception):
    pass


# ---- S-expressions -------------------------------------------------------------------------------------------
class Sym(str):
    """A bare atom (not a quoted string)."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text: str) -> list:
    """The first S-expression of text as nested lists of atoms (Sym) and strings (str)."""
    stack: list[list] = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise KicadError(f"unreadable text at character {pos}")
            break
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            if len(stack) < 2:
                raise KicadError("a closing parenthesis without an opening one")
            done = stack.pop()
            stack[-1].append(done)
            if len(stack) == 1:
                break
        elif m.group(3) is not None:
            stack[-1].append(re.sub(r"\\(.)", lambda q: {"n": "\n", "t": "\t"}.get(q.group(1), q.group(1)),
                                    m.group(3)))
        else:
            stack[-1].append(Sym(m.group(4)))
    if len(stack) != 1 or not stack[0]:
        raise KicadError("the file ends inside an expression (a parenthesis is missing)")
    return stack[0][0]


def head(e) -> str:
    return e[0] if isinstance(e, list) and e and isinstance(e[0], str) else ""


def items(e: list, name: str) -> list[list]:
    return [x for x in e if head(x) == name]


def item(e: list, name: str):
    for x in e:
        if head(x) == name:
            return x
    return None


def _num(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _xy(e) -> tuple[float, float]:
    """(at x y ...) / (xy x y) / (start x y) in y-up millimetres."""
    return (_num(e[1]), -_num(e[2])) if e is not None and len(e) >= 3 else (0.0, 0.0)


# ---- footprints ----------------------------------------------------------------------------------------------
@dataclass
class Pad:
    number: str                    # "" for a pad without a number (mechanical, not connected)
    kind: str                      # smd, thru_hole, np_thru_hole, connect
    shape: str                     # rect, roundrect, oval, circle, trapezoid, custom
    x: float                       # mm, y up, footprint coordinates
    y: float
    rot: float                     # degrees, counter-clockwise seen from the top
    w: float                       # size along the pad's own x and y
    h: float
    drill: float = 0.0             # hole diameter (the smaller size of an oval hole)
    layers: list[str] = field(default_factory=list)
    rratio: float = 0.0            # corner radius / smaller size of a roundrect
    primitives: list[list[tuple[float, float]]] = field(default_factory=list)  # custom: polygons, pad coords

    @property
    def copper(self) -> bool:
        """The pad has copper on the top layer (or on all copper layers)."""
        return self.kind != "np_thru_hole" and any(l in ("F.Cu", "*.Cu") for l in self.layers)

    @property
    def plated_hole(self) -> bool:
        return self.kind == "thru_hole" and self.drill > 0

    def outline(self, arc_segments: int = 6) -> list[tuple[float, float]]:
        """The pad's copper outline in footprint coordinates (mm, y up), counter-clockwise. A custom pad gives
        the outline of its anchor; its primitives are in polygons()."""
        w, h = self.w, self.h
        if self.shape == "circle":
            r = w / 2
            local = [(r * math.cos(2 * math.pi * k / 24), r * math.sin(2 * math.pi * k / 24)) for k in range(24)]
        elif self.shape in ("oval", "roundrect"):
            r = min(w, h) / 2 if self.shape == "oval" else self.rratio * min(w, h)
            local = _rounded_rect(w, h, r, arc_segments)
        else:
            local = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        return [self._place(p) for p in local]

    def polygons(self) -> list[list[tuple[float, float]]]:
        """The copper of the pad as polygons in footprint coordinates: the outline, plus the primitives of a
        custom pad."""
        out = [self.outline()]
        for poly in self.primitives:
            out.append([self._place(p) for p in poly])
        return out

    def _place(self, p: tuple[float, float]) -> tuple[float, float]:
        a = math.radians(self.rot)
        c, s = math.cos(a), math.sin(a)
        return self.x + c * p[0] - s * p[1], self.y + s * p[0] + c * p[1]

    def bbox(self) -> tuple[float, float, float, float]:
        pts = [p for poly in self.polygons() for p in poly]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)


def _rounded_rect(w: float, h: float, r: float, n: int) -> list[tuple[float, float]]:
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 1e-12:
        return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    pts = []
    for cx, cy, a0 in ((w / 2 - r, -h / 2 + r, -90), (w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90),
                       (-w / 2 + r, -h / 2 + r, 180)):
        for k in range(n + 1):
            a = math.radians(a0 + 90 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


@dataclass
class Footprint:
    name: str
    pads: list[Pad] = field(default_factory=list)
    description: str = ""
    courtyard: tuple[float, float, float, float] | None = None    # bbox of F.CrtYd (mm, y up)
    fab: list[list[tuple[float, float]]] = field(default_factory=list)   # F.Fab lines and polygons
    model: str = ""                # 3D model reference (not used by the solver)
    source: str = ""               # where it came from: a file path or "built-in"
    smd: bool = True

    def numbers(self) -> list[str]:
        """The pad numbers in the order of the file, each once."""
        seen = []
        for p in self.pads:
            if p.number and p.number not in seen and p.kind != "np_thru_hole":
                seen.append(p.number)
        return seen

    def pads_of(self, number: str) -> list[Pad]:
        return [p for p in self.pads if p.number == number]

    def bbox(self) -> tuple[float, float, float, float]:
        if self.courtyard:
            return self.courtyard
        boxes = [p.bbox() for p in self.pads] or [(0, 0, 0, 0)]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes))


def _shape_points(e) -> list[tuple[float, float]]:
    """Points of a graphic item (fp_line, fp_rect, fp_poly, gr_poly, fp_circle) in y-up mm."""
    h_ = head(e)
    if h_ in ("fp_line", "gr_line"):
        return [_xy(item(e, "start")), _xy(item(e, "end"))]
    if h_ in ("fp_rect", "gr_rect"):
        (x0, y0), (x1, y1) = _xy(item(e, "start")), _xy(item(e, "end"))
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    if h_ in ("fp_poly", "gr_poly"):
        pts = item(e, "pts")
        return [_xy(p) for p in items(pts, "xy")] if pts else []
    if h_ in ("fp_circle", "gr_circle"):
        (cx, cy), (ex, ey) = _xy(item(e, "center")), _xy(item(e, "end"))
        r = math.hypot(ex - cx, ey - cy)
        return [(cx + r * math.cos(2 * math.pi * k / 24), cy + r * math.sin(2 * math.pi * k / 24))
                for k in range(25)]
    if h_ in ("fp_arc", "gr_arc"):
        a, m, b = (_xy(item(e, k)) for k in ("start", "mid", "end"))
        if item(e, "mid") is None:
            return [a, b]
        return _arc3(a, m, b)
    return []


def _arc3(a, m, b, n: int = 12) -> list[tuple[float, float]]:
    """Points of the circular arc through a, m, b."""
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [a, b]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    t0, tm, t1 = (math.atan2(p[1] - uy, p[0] - ux) for p in (a, m, b))

    def ccw(x, y):  # angle from x to y counter-clockwise in [0, 2pi)
        return (y - x) % (2 * math.pi)
    span = ccw(t0, t1)
    if ccw(t0, tm) > span:      # the mid point is on the other side: clockwise
        span -= 2 * math.pi
    return [(ux + r * math.cos(t0 + span * k / n), uy + r * math.sin(t0 + span * k / n)) for k in range(n + 1)]


def footprint_from_sexpr(e: list, source: str = "") -> Footprint:
    if head(e) not in ("footprint", "module"):
        raise KicadError(f"not a KiCad footprint ({head(e) or 'empty'})")
    name = str(e[1]) if len(e) > 1 else ""
    fp = Footprint(name=name.split(":")[-1], source=source)
    descr = item(e, "descr")
    fp.description = str(descr[1]) if descr and len(descr) > 1 else ""
    attr = item(e, "attr")
    fp.smd = not (attr and "through_hole" in attr)
    model = item(e, "model")
    fp.model = str(model[1]) if model and len(model) > 1 else ""
    crt = []
    for g in e:
        h_ = head(g)
        if not h_.startswith(("fp_", "gr_")):
            continue
        layer = item(g, "layer")
        lname = str(layer[1]) if layer and len(layer) > 1 else ""
        if lname == "F.CrtYd":
            crt += _shape_points(g)
        elif lname == "F.Fab":
            pts = _shape_points(g)
            if pts:
                fp.fab.append(pts)
    if crt:
        xs, ys = [p[0] for p in crt], [p[1] for p in crt]
        fp.courtyard = (min(xs), min(ys), max(xs), max(ys))
    for p in items(e, "pad"):
        if len(p) < 4:
            continue
        at = item(p, "at")
        x, y = _xy(at)
        rot = _num(at[3]) if at is not None and len(at) > 3 else 0.0
        size = item(p, "size")
        w, h = (_num(size[1]), _num(size[2])) if size and len(size) > 2 else (0.0, 0.0)
        drill = item(p, "drill")
        dval = 0.0
        if drill:
            nums = [_num(v) for v in drill[1:] if not isinstance(v, list) and _is_number(v)]
            dval = min(nums) if nums else 0.0
        layers = item(p, "layers")
        rr = item(p, "roundrect_rratio")
        pad = Pad(number=str(p[1]), kind=str(p[2]), shape=str(p[3]), x=x, y=y, rot=rot, w=w, h=h, drill=dval,
                  layers=[str(v) for v in layers[1:]] if layers else [],
                  rratio=_num(rr[1]) if rr and len(rr) > 1 else 0.25)
        prims = item(p, "primitives")
        if prims:
            for g in prims:
                pts = _shape_points(g)
                if head(g) in ("gr_poly", "gr_rect", "gr_circle") and len(pts) >= 3:
                    pad.primitives.append(pts)
                elif head(g) == "gr_line" and len(pts) == 2:
                    wd = item(g, "width") or item(item(g, "stroke") or [], "width")
                    pad.primitives.append(_thick_line(pts[0], pts[1], _num(wd[1]) if wd else 0.0))
        fp.pads.append(pad)
    return fp


def _thick_line(a, b, w):
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln * w / 2, dx / ln * w / 2
    return [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def read_footprint(path: str | Path) -> Footprint:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as ex:
        raise KicadError(f"cannot read the footprint {path}: {ex}") from ex
    try:
        return footprint_from_sexpr(parse(text), str(path))
    except KicadError as ex:
        raise KicadError(f"{path.name}: {ex}") from ex


# ---- symbols -------------------------------------------------------------------------------------------------
@dataclass
class Symbol:
    name: str
    pins: list[tuple[str, str]] = field(default_factory=list)   # (number, name), each number once
    footprint: str = ""            # the Footprint property of the symbol (a library id), if set

    def name_of(self, number: str) -> str:
        for n, nm in self.pins:
            if n == number:
                return nm
        return ""


def _collect_pins(e: list, out: list[tuple[str, str]]):
    for x in e:
        if head(x) == "pin":
            nm, num = item(x, "name"), item(x, "number")
            number = str(num[1]) if num and len(num) > 1 else ""
            if number and all(number != n for n, _ in out):
                out.append((number, str(nm[1]) if nm and len(nm) > 1 else ""))
        elif head(x) == "symbol":
            _collect_pins(x, out)


def symbols_from_sexpr(e: list) -> dict[str, Symbol]:
    if head(e) != "kicad_symbol_lib":
        raise KicadError(f"not a KiCad symbol library ({head(e) or 'empty'})")
    out: dict[str, Symbol] = {}
    parents: dict[str, str] = {}
    for s in items(e, "symbol"):
        name = str(s[1]) if len(s) > 1 else ""
        sym = Symbol(name=name)
        _collect_pins(s, sym.pins)
        for p in items(s, "property"):
            if len(p) > 2 and p[1] == "Footprint":
                sym.footprint = str(p[2])
        ext = item(s, "extends")
        if ext and len(ext) > 1:
            parents[name] = str(ext[1])
        out[name] = sym
    for name, parent in parents.items():
        if not out[name].pins and parent in out:
            out[name].pins = list(out[parent].pins)
    return out


def read_symbols(path: str | Path) -> dict[str, Symbol]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as ex:
        raise KicadError(f"cannot read the symbol library {path}: {ex}") from ex
    try:
        return symbols_from_sexpr(parse(text))
    except KicadError as ex:
        raise KicadError(f"{path.name}: {ex}") from ex
