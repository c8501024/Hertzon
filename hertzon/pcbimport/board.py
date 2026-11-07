"""PCB import: a set of Gerber, Excellon and job files becomes copper, vias, holes and substrate of a design.

Steps (each can be checked and changed in the import dialog before anything is made):

1. :func:`load` reads every file (folders and zip archives are opened) and finds what it is: from its X2
   attributes, the job file, and the usual file names of KiCad, Altium/Protel and Eagle, in this order.
2. :func:`plan` matches the copper files to the copper layers of the stackup (job file, the design's
   stackup, or a usual stack for the number of copper layers) and finds the board shape and its extent.
3. :func:`build` makes the design: the stackup with its ``stk_`` variables and coordinate systems, the
   substrate in the board shape, copper on the layer coordinate systems (sheets with a finite
   conductivity, or solids, as each layer of the stackup says), plated holes as copper cylinders between
   the copper layers they connect and non-plated holes cut out of the board.

The rule of the import: what is loaded is simulated. What is not simulated (solder mask, legend, paste,
files that cannot be matched) is not loaded, and the report names it.
"""
from __future__ import annotations

import json
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

from ..model import stackup as S
from ..model.expr import LENGTH_UNITS
from . import excellon as E
from . import gerber as G

COPPER, PROFILE, DRILL, MASK, LEGEND, PASTE, OTHER, JOB = (
    "Copper", "Outline", "Drill", "Solder mask", "Legend", "Paste", "Other", "Job file")
NOT_SIMULATED = (MASK, LEGEND, PASTE, OTHER)
GROUP_PREFIX = "PCB "
VIA_GROUP = "PCB Vias"
HOLE_GROUP = "PCB Holes"
SIMPLIFY = 0.05e-6           # m: points closer to a straight line than this are dropped


@dataclass
class SourceFile:
    name: str
    text: str
    kind: str = OTHER
    layer: int | None = None          # copper: 1 = top
    side: str = ""                    # Top / Bot / Inr
    how: str = ""                     # how the kind was found
    image: G.Image | None = None
    drill: E.Drill | None = None
    error: str = ""

    @property
    def label(self) -> str:
        if self.kind == COPPER:
            return f"Copper L{self.layer}" if self.layer else "Copper (layer unknown)"
        return self.kind


# ------------------------------------------------------------------------------------------------
# reading the files
# ------------------------------------------------------------------------------------------------
def _texts(paths) -> list[tuple[str, str]]:
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += _texts(sorted(q for q in p.iterdir() if q.is_file()))
        elif p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    if not n.endswith("/") and not Path(n).name.startswith("."):
                        out.append((Path(n).name, z.read(n).decode("utf-8", errors="replace")))
        else:
            out.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
    return out


_NAMES = [   # (regex on the lower case file name, kind, layer: 1 top, -1 bottom, "in": inner from group 1)
    (r"[-_.]f[_.]cu\b|\.gtl$|\.cmp$|[-_]top(layer)?\.(gbr|ger)$|copper_top", COPPER, 1),
    (r"[-_.]b[_.]cu\b|\.gbl$|\.sol$|[-_]bot(tom)?(layer)?\.(gbr|ger)$|copper_bot", COPPER, -1),
    (r"[-_.]in(\d+)[_.]cu\b", COPPER, "in"),
    (r"\.g(\d+)$|\.gp(\d+)$|\.ly(\d+)$", COPPER, "in"),
    (r"edge[_.]cuts|\.gko$|\.gm1$|\.gml$|\.gm$|\.dim$|\.mil$|outline|profile", PROFILE, None),
    (r"[fb][_.]mask|\.gts$|\.gbs$|\.stc$|\.sts$|soldermask|solder_mask", MASK, None),
    (r"[fb][_.]silks?|silkscreen|\.gto$|\.gbo$|\.plc$|\.pls$|legend", LEGEND, None),
    (r"[fb][_.]paste|\.gtp$|\.gbp$|\.crc$|\.crs$|paste", PASTE, None),
    (r"\.drl$|\.xln$|\.exc$|\.drd$|\.txt$|drill|\.nc$", DRILL, None),
]


def _from_function(f: list[str]) -> tuple[str, int | None, str] | None:
    if not f:
        return None
    k = f[0].lower()
    if k == "copper":
        n = int(f[1][1:]) if len(f) > 1 and f[1][:1] in "Ll" and f[1][1:].isdigit() else None
        return COPPER, n, f[2] if len(f) > 2 else ""
    if k == "profile":
        return PROFILE, None, ""
    if k == "soldermask":
        return MASK, None, f[1] if len(f) > 1 else ""
    if k == "legend":
        return LEGEND, None, f[1] if len(f) > 1 else ""
    if k == "paste":
        return PASTE, None, f[1] if len(f) > 1 else ""
    if k in ("plated", "nonplated"):
        return DRILL, None, ""
    return OTHER, None, f[0]


def _job_functions(job: dict) -> dict[str, list[str]]:
    out = {}
    for f in S._get(job, "FilesAttributes") or []:
        p, fn = S._get(f, "Path"), S._get(f, "FileFunction")
        if p and fn:
            out[Path(str(p)).name.lower()] = str(fn).split(",")
    return out


def load(paths, segments: int = G.DEFAULT_SEGMENTS) -> tuple[list[SourceFile], dict | None, str]:
    """Read and classify the files. Returns the files, the job file data (or None) and its name."""
    files: list[SourceFile] = []
    job, job_name = None, ""
    for name, text in _texts(paths):
        if name.lower().endswith(".gbrjob") or (text.lstrip().startswith("{") and '"Header"' in text[:2000]):
            try:
                job, job_name = json.loads(text), name
            except json.JSONDecodeError as e:
                files.append(SourceFile(name, "", OTHER, error=f"job file cannot be read: {e}"))
            continue
        files.append(SourceFile(name, text))
    jobf = _job_functions(job) if job else {}
    for f in files:
        if f.error:
            continue
        low = f.name.lower()
        try:
            if E.looks_like_excellon(f.text):
                f.drill = E.read(f.text, f.name)
                f.kind, f.how = DRILL, "Excellon file"
                continue
            if not G.looks_like_gerber(f.text):
                f.kind, f.how = OTHER, "not a Gerber or drill file"
                continue
            f.image = G.read(f.text, segments)
        except Exception as e:  # noqa: BLE001
            f.kind, f.error = OTHER, str(e)
            continue
        got = _from_function(f.image.function)
        how = "X2 attribute"
        if got is None and low in jobf:
            got, how = _from_function(jobf[low]), "job file"
        if got is None:
            for rx, kind, layer in _NAMES:
                m = re.search(rx, low)
                if m:
                    n = None
                    if layer == "in":
                        n = 1 + int(next(g for g in m.groups() if g))
                    elif layer == 1:
                        n = 1
                    elif layer == -1:
                        n = -1
                    got, how = (kind, n, ""), "file name"
                    break
        if got is None:
            got, how = (OTHER, None, ""), "not recognised"
        f.kind, f.layer, f.side = got
        f.how = how
        if f.kind == DRILL and f.image is not None:     # X2 drill file written as Gerber
            f.drill = _drill_from_gerber(f.image)
    # bottom copper (-1) becomes the last layer
    n = copper_count(files, job)
    for f in files:
        if f.kind == COPPER and f.layer == -1:
            f.layer = n
    return files, job, job_name


def _drill_from_gerber(img: G.Image) -> E.Drill:
    f = img.function
    plated = not (f and f[0].lower() == "nonplated")
    d = E.Drill([E.Hole(x, y, dd, plated) for x, y, dd in img.flashes], function=f, plated_known=bool(f))
    if len(f) >= 3 and f[1].isdigit() and f[2].isdigit():
        d.span = (int(f[1]), int(f[2]))
    return d


def copper_count(files: list[SourceFile], job: dict | None) -> int:
    """Copper layers: the most of the copper files, the job file's layer count and the highest layer named
    (one more when a bottom layer is known only by its file name)."""
    known = [f.layer for f in files if f.kind == COPPER and f.layer and f.layer > 0]
    bottom_by_name = any(f.kind == COPPER and f.layer == -1 for f in files)
    n_files = sum(1 for f in files if f.kind == COPPER)
    n_job = -1
    if job:
        try:
            n_job = int(S._get(S._get(job, "GeneralSpecs") or {}, "LayerNumber") or -1)
        except (TypeError, ValueError):
            n_job = -1
    n_named = max(known, default=0) + (1 if bottom_by_name else 0)
    return max(1, n_files, n_job, n_named)


# ------------------------------------------------------------------------------------------------
# plan: matching, board shape, extent
# ------------------------------------------------------------------------------------------------
@dataclass
class Plan:
    files: list[SourceFile]
    stackup: S.Stackup
    crop: tuple[float, float, float, float] | None = None     # x0, y0, x1, y1 in file coordinates (m)
    origin: tuple[float, float] = (0.0, 0.0)                  # file point that becomes the board origin
    segments: int = G.DEFAULT_SEGMENTS
    job_name: str = ""
    notes: list[str] = field(default_factory=list)

    def copper_files(self) -> dict[int, SourceFile]:
        out: dict[int, SourceFile] = {}
        for f in self.files:
            if f.kind == COPPER and f.layer and f.layer not in out:
                out[f.layer] = f
        return out

    def profile(self) -> SourceFile | None:
        return next((f for f in self.files if f.kind == PROFILE and f.image is not None), None)

    def drills(self) -> list[SourceFile]:
        return [f for f in self.files if f.kind == DRILL and f.drill is not None]

    def inside(self, h: E.Hole) -> bool:
        """The hole lies fully inside the import area (always without one)."""
        if not self.crop:
            return True
        foot = (shapely.geometry.LineString([(h.x, h.y), (h.x1, h.y1)]) if h.slot
                else shapely.geometry.Point(h.x, h.y)).buffer(h.d / 2, quad_segs=8)
        return box(*self.crop).contains(foot)

    def board_shape(self) -> list[Polygon]:
        """Board polygons in file coordinates, cropped: the outline, else the extent of the copper."""
        prof = self.profile()
        polys = G.outline_polygons(prof.image, self.segments) if prof else []
        if not polys:
            ext = self.extent(outline=False)
            polys = [box(*ext)] if ext else []
        if self.crop:
            c = box(*self.crop)
            polys = [q for p in polys for q in G.polygons_of(p.intersection(c))]
        return polys

    def extent(self, outline: bool = True) -> tuple[float, float, float, float] | None:
        geo = []
        prof = self.profile()
        if outline and prof:
            geo += G.outline_polygons(prof.image, self.segments)
        if not geo:
            geo += [p for f in self.copper_files().values() for p in f.image.polygons()]
        if not geo:
            return None
        return shapely.union_all(geo).bounds


def plan(files: list[SourceFile], job: dict | None = None, job_name: str = "", stackup: S.Stackup | None = None,
         segments: int = G.DEFAULT_SEGMENTS) -> Plan:
    """Stackup and matching: the job file's stackup, else the given one (the design's) if its copper count
    fits, else a usual stack for the copper files. The board origin is the lower left of the board."""
    notes: list[str] = []
    n = copper_count(files, job)
    if job:
        st, issues = S.read_gbrjob(job)
        st.source = f"job file {job_name}" if job_name else st.source
        notes += [i.text for i in issues]
    elif stackup is not None and len(stackup.copper()) == n:
        st = S.Stackup.from_dict(stackup.to_dict())
        notes.append("Stackup of the design used (no job file).")
    else:
        st = S.template(n, "1.6mm", origin="assumed")
        st.source = "assumed"
        notes.append(f"No job file: a usual {n}-layer stack of 1.6 mm is assumed. Check it in the stackup editor.")
    p = Plan(files, st, segments=segments, job_name=job_name, notes=notes)
    ext = p.extent()
    if ext:
        p.origin = (ext[0], ext[1])
    return p


# ------------------------------------------------------------------------------------------------
# building the design
# ------------------------------------------------------------------------------------------------
def _fmt(v: float) -> str:
    s = f"{v:.9f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


MIN_EDGE = 0.1e-6            # m: shorter edges are merged (the kernel cannot make them)


def _loop(coords, u: float, z: str | None = None) -> str:
    pts = []
    for q in coords:
        if not pts or math.hypot(q[0] - pts[-1][0], q[1] - pts[-1][1]) >= MIN_EDGE:
            pts.append(q)
    while len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < MIN_EDGE:
        pts = pts[:-1]
    if len(pts) < 3:
        return ""
    if z is None:
        return ";".join(f"{_fmt(x / u)},{_fmt(y / u)}" for x, y in pts)
    return ";".join(f"{_fmt(x / u)},{_fmt(y / u)},{z}" for x, y in pts)


TOUCH = 1e-6                 # m: parts of a copper shape closer than this touch for the kernel
REPAIR_MAX = 64e-6           # m: largest disc that joins touching rings before the shape is reported


def _segments(p: Polygon):
    """All edges of all rings of p as (x0, y0, x1, y1), the ring of each and its vertex counts."""
    rings = [p.exterior, *p.interiors]
    P, ring = [], []
    for r, R in enumerate(rings):
        c = np.asarray(R.coords)[:-1]
        P.append(np.c_[c, np.roll(c, -1, axis=0)])
        ring.append(np.full(len(c), r))
    return np.concatenate(P), np.concatenate(ring)


def pinches(p: Polygon, tol: float = TOUCH) -> list[tuple[float, float]]:
    """Places where p touches itself for the kernel: a corner of a ring closer than tol to an edge that does
    not end in it (of the same ring or another one). The kernel merges such points, its curve loops then do
    not close ("Curve loop is not closed") or a hole runs into the outside, although p is a valid polygon."""
    seg, ring = _segments(p)
    n = len(seg)
    if n < 4:
        return []
    starts = np.r_[0, np.flatnonzero(np.diff(ring)) + 1]
    first = starts[ring]                                      # first edge of the ring of each edge
    size = np.bincount(ring)[ring]
    idx = np.arange(n)
    prev = first + (idx - first - 1) % size                   # the edge that ends in corner i
    nxt = first + (idx - first + 1) % size
    lines = shapely.linestrings(seg.reshape(-1, 2, 2))
    pts = shapely.points(seg[:, :2])
    i, j = shapely.STRtree(lines).query(pts, predicate="dwithin", distance=tol)
    # the edges at corner i and their neighbours (a short edge brings corner i near the next one)
    keep = (j != i) & (j != prev[i]) & (j != nxt[i]) & (j != prev[prev[i]])
    out = []
    for a, b in zip(i[keep], j[keep]):
        q = shapely.shortest_line(pts[a], lines[b]).coords
        out.append(((q[0][0] + q[1][0]) / 2, (q[0][1] + q[1][1]) / 2))
    return out


def _clean(p: Polygon) -> Polygon:
    """Repeated points and points on a straight line dropped, rings oriented (outside counter-clockwise)."""
    return orient(p.simplify(0, preserve_topology=True), 1.0)


def _disc(x: tuple[float, float], r: float) -> Polygon:
    """Small 12-gon turned off the axes (its corners do not land exactly on the usual 0/45/90° edges)."""
    t = np.radians(7.0) + np.arange(12) * np.pi / 6
    return Polygon(np.c_[x[0] + r * np.cos(t), x[1] + r * np.sin(t)])


def normalize(p: Polygon, tol: float = TOUCH) -> tuple[list[Polygon], str]:
    """Copper shape the kernel can build.

    Every shape is cleaned (repeated and collinear points dropped, rings oriented). An invalid shape is made
    valid. Where rings touch or nearly touch (parts that meet in a point, a hole on the outline or on another
    hole, a slit narrower than a few µm), gaps narrower than 2·tol are closed (buffer out and back in) and a
    small copper disc is added at each touch that is left, so parts that meet in a point stay one connected
    conductor and holes get a clear distance from the rest. Returns the polygons and what was done ('' if
    nothing)."""
    notes = []
    parts = [p]
    if not p.is_valid:
        notes.append("made valid")
        parts = [q for q in G.polygons_of(shapely.make_valid(p)) if q.area > 0]
        if len(parts) > 1:
            # pieces that touch in points are joined; pieces apart from each other stay separate
            parts = G.polygons_of(shapely.union_all(
                parts + [_disc(x, 4 * tol) for x in _touch_points(parts, tol)]))
    out = []
    for q in parts:
        r, note = _untouch(_clean(q), tol)
        out += r
        notes += [note] if note else []
    return out, ", ".join(dict.fromkeys(notes))


def _untouch(p: Polygon, tol: float) -> tuple[list[Polygon], str]:
    if not pinches(p, tol):
        return [p], ""
    note = "touching rings repaired"
    q = p.buffer(2 * tol, join_style="mitre").buffer(-2 * tol, join_style="mitre")
    qs = [x for x in G.polygons_of(q) if x.area > 0]
    if len(qs) == 1 and qs[0].is_valid:
        p = _clean(qs[0])
    e = 4 * tol                      # the disc has its own corners: well clear of the edges near it
    while True:
        where = pinches(p, tol)
        if not where:
            return [p], note
        if e > REPAIR_MAX:
            return [p], f"still touches itself at {len(where)} points"
        q = shapely.union_all([p] + [_disc(x, e) for x in where])
        qs = [x for x in G.polygons_of(q) if x.area > 0]
        if len(qs) == 1 and qs[0].is_valid:
            p = _clean(qs[0])
        e *= 2


def _touch_points(parts: list[Polygon], tol: float) -> list[tuple[float, float]]:
    out = []
    for k, a in enumerate(parts):
        for b in parts[k + 1:]:
            if a.distance(b) <= tol:
                q = shapely.shortest_line(a.boundary, b.boundary).coords
                out.append(((q[0][0] + q[1][0]) / 2, (q[0][1] + q[1][1]) / 2))
    return out


def _prepare(polys: list[Polygon], ox: float, oy: float, notes: list[tuple[Polygon, str]] | None = None
             ) -> list[Polygon]:
    out = []
    for p in polys:
        p = affinity.translate(p, -ox, -oy).simplify(SIMPLIFY, preserve_topology=True)
        for q in G.polygons_of(p):
            if q.area <= 0:
                continue
            qs, note = normalize(q)
            for r in qs:
                if r.area > 0:
                    out.append(r)
                    if notes is not None and note:
                        notes.append((r, note))
    return out


@dataclass
class Report:
    made: dict[str, int] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    not_loaded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    shapes: dict[str, tuple[str, float]] = field(default_factory=dict)   # copper object: layer, area (mm²)

    def check_built(self, errors: dict) -> list[str]:
        """Warnings for copper shapes the kernel could not build (errors: object -> message); also added
        to the warnings of the report. A missing shape is missing copper, which is easy to overlook."""
        out = [f"{n} on {self.shapes[n][0]} ({self.shapes[n][1]:.4g} mm²) could not be built: {errors[n]}. "
               "This copper is MISSING from the model." for n in sorted(errors) if n in self.shapes]
        self.warnings += [w for w in out if w not in self.warnings]
        return out

    def text(self) -> str:
        out = ["PCB import", ""] + self.lines
        if self.not_loaded:
            out += ["", "Not loaded (not simulated):"] + [f"  - {x}" for x in self.not_loaded]
        if self.warnings:
            out += ["", "Check:"] + [f"  - {x}" for x in self.warnings]
        return "\n".join(out)


def _remove_previous(des) -> None:
    """Objects and assignments of an earlier import (groups 'PCB ...')."""
    gone = {o.name for o in des.objects if o.group and o.group.startswith(GROUP_PREFIX)}
    if not gone:
        return
    des.objects = [o for o in des.objects if o.name not in gone]
    des.boundaries = [b for b in des.boundaries if not (b.objects and set(b.objects) <= gone)]
    for o in des.objects:
        o.history = [c for c in o.history if not (c.kind == "Subtract" and set(
            t.strip() for t in c.props.get("Tools", "").split(",") if t.strip()) <= gone)]


def build(project, des, pl: Plan) -> Report:
    """Make the board in the design (an earlier PCB import of the design is replaced)."""
    from ..model import schema
    from ..model.design import Assignment, Command
    rep = Report()
    st = pl.stackup
    cu_idx = st.copper()
    names = S.slugs(st)
    u = LENGTH_UNITS.get(des.units, 1e-3)
    ox, oy = (round(v, 9) + 0.0 for v in pl.origin)      # nm grid: no -6e-18 in the report
    _remove_previous(des)

    # stackup, variables, coordinate systems, substrate in the board shape
    shape = _prepare(pl.board_shape(), ox, oy)
    des.stackup = st
    S.sync_variables(des, st, des.evaluator(project))
    S.sync_coordinate_systems(des, st)
    if not shape:
        raise ValueError("Nothing to import: no board outline and no copper in the selected area.")
    if len(shape) > 1:
        rep.warnings.append(f"The board has {len(shape)} separate parts; only the largest is used as the "
                            "substrate.")
    board = max(shape, key=lambda q: q.area)
    outline = (_loop(board.exterior.coords, u), "|".join(h for h in (_loop(h.coords, u) for h in board.interiors) if h))
    if len(board.exterior.coords) == 5 and not board.interiors and abs(board.area - box(*board.bounds).area) < 1e-15:
        x0, y0, x1, y1 = board.bounds
        made = S.build_substrate(project, des, st, des.evaluator(project), _fmt(x0 / u), _fmt(y0 / u),
                                 _fmt((x1 - x0) / u), _fmt((y1 - y0) / u))
    else:
        made = S.build_substrate(project, des, st, des.evaluator(project), outline=outline)
    rep.made["substrate"] = len(made)
    prof = pl.profile()
    rep.lines.append(f"Board: {(board.bounds[2] - board.bounds[0]) * 1e3:.4g} x "
                     f"{(board.bounds[3] - board.bounds[1]) * 1e3:.4g} mm"
                     + (f" from the outline in {prof.name}" if prof else " (no outline file: the extent of the copper)")
                     + (", cropped to the selected area" if pl.crop else "")
                     + (f", {len(board.interiors)} cut-outs" if board.interiors else "") + ".")
    rep.lines.append(f"Board origin: file point ({ox * 1e3:.6g}, {oy * 1e3:.6g}) mm.")
    rep.lines.append(f"Stackup: {len(cu_idx)} copper layers, {st.source}.")

    # copper
    clip = box(*pl.crop) if pl.crop else None
    copper_objs: dict[int, list[str]] = {}
    for k, f in sorted(pl.copper_files().items()):
        if k > len(cu_idx):
            rep.not_loaded.append(f"{f.name}: copper layer L{k}, but the stackup has {len(cu_idx)} copper layers")
            continue
        i = cu_idx[k - 1]
        L, s = st.layers[i], names[i]
        polys = f.image.polygons()
        if clip is not None:
            polys = [q for p in polys for q in G.polygons_of(p.intersection(clip))]
        fixed: list[tuple[Polygon, str]] = []
        polys = _prepare(polys, ox, oy, fixed)
        group = f"{GROUP_PREFIX}{L.name}"
        solid = L.representation == S.SOLID
        z = f"{S.VAR_PREFIX}{s}_z-{S.VAR_PREFIX}{s}_zs" if solid else "0"
        objs = []
        for p in polys:
            pts = _loop(p.exterior.coords, u, z)
            if not pts:
                continue
            o = des.add_object("CreatePolyline", des.unique_name(f"{s}_{len(objs) + 1}"),
                               material="copper" if solid else "vacuum", Points=pts, Closed="True", Covered="True",
                               Holes="|".join(h for h in (_loop(h.coords, u, z) for h in p.interiors) if h))
            o.orientation = f"{S.VAR_PREFIX}{s}"
            o.group = group
            if solid:
                o.history.append(Command.new("ThickenSheet", Thickness=f"{S.VAR_PREFIX}{s}_t"))
                o.color = "#d8a040"
                o.transparency = 0.0
                o.solve_inside = False
            else:
                o.color = "#d8a040"
            objs.append(o.name)
            note = next((t for q, t in fixed if q is p), "")
            rep.shapes[o.name] = (L.name, p.area * 1e6)
            if note:
                (rep.warnings if "still" in note else rep.lines).append(
                    f"{o.name} on {L.name} ({p.area * 1e6:.4g} mm², {len(p.interiors)} holes): {note}.")
        copper_objs[k] = objs
        if objs and not solid:
            props = schema.defaults(schema.BOUNDARIES["Finite Conductivity"])
            props.update(Material=L.material or "copper", UseThickness="True",
                         Thickness=f"{S.VAR_PREFIX}{s}_t", TwoSided="True")
            des.boundaries.append(Assignment(des.unique_name(f"Cu_{s}", {b.name for b in des.boundaries}),
                                             "Finite Conductivity", props, objects=objs))
        holes = sum(len(p.interiors) for p in polys)
        rep.lines.append(f"{L.name}: {len(objs)} copper shapes ({holes} holes) from {f.name}, "
                         + ("solid copper" if solid else "sheet with finite conductivity") + ".")
        rep.made[L.name] = len(objs)
    used = set(map(id, pl.copper_files().values()))
    for f in pl.files:
        if f.kind == COPPER and id(f) not in used:
            rep.not_loaded.append(f"{f.name}: copper, " + (f"a second file for L{f.layer}" if f.layer else
                                                            "no copper layer given"))
    for k in range(1, len(cu_idx) + 1):
        if k not in pl.copper_files():
            rep.warnings.append(f"No copper file for {st.layers[cu_idx[k - 1]].name} (L{k}): the layer is empty.")

    # holes
    vias, npth, outside = [], [], 0
    zs = [f"{S.VAR_PREFIX}{names[i]}_zs" for i in cu_idx]
    for f in pl.drills():
        d = f.drill
        a, b = d.span if d.span else (1, len(cu_idx))
        a, b = max(1, min(a, b)), min(len(cu_idx), max(a, b))
        for h in d.holes:
            if not pl.inside(h):
                outside += 1
                continue
            x, y = h.x - ox, h.y - oy
            if h.plated:
                top, bot = zs[a - 1], zs[b - 1]
                nm = des.unique_name(f"Via{len(vias) + 1}")
                o = _hole_object(des, nm, h, x, y, u, bot, f"{top}-{bot}", "copper")
                o.group = VIA_GROUP
                o.color = "#d8a040"
                o.solve_inside = False
                vias.append(o.name)
            else:
                nm = des.unique_name(f"Hole{len(npth) + 1}")
                o = _hole_object(des, nm, h, x, y, u, "0", f"{S.VAR_PREFIX}total", "vacuum")
                o.group = HOLE_GROUP
                o.model = False
                npth.append(o.name)
        rep.lines.append(f"{f.name}: {sum(1 for h in d.holes if h.plated)} plated, "
                         f"{sum(1 for h in d.holes if not h.plated)} non-plated holes"
                         + (f", copper layers L{a}-L{b}" if d.span else "") + ".")
        if not d.plated_known and d.holes:
            rep.warnings.append(f"{f.name} does not say whether its holes are plated: read as plated.")
    if outside:
        rep.not_loaded.append(f"{outside} holes that are not fully inside the selected area")
    tools = vias + npth
    if tools:
        for o in des.objects:
            if o.group == S.SUBSTRATE_GROUP and o.name.startswith(("Sub_", "Fill_")):
                o.history.append(Command.new("Subtract", Tools=",".join(tools), KeepOriginals="True"))
        rep.lines.append(f"Vias: {len(vias)} copper cylinders (filled, finished hole size) between their copper "
                         f"layers; non-plated holes: {len(npth)}, cut out of the board.")
    rep.made["vias"] = len(vias)
    rep.made["holes"] = len(npth)
    rep.lines.append(f"Arcs and circles are made of {pl.segments} straight segments per full circle.")

    for f in pl.files:
        if f.kind in NOT_SIMULATED:
            rep.not_loaded.append(f"{f.name}: {f.kind.lower()}" + (f" ({f.error})" if f.error else ""))
        elif f.kind == PROFILE and f is not prof:
            rep.not_loaded.append(f"{f.name}: a second outline file")
        for m in (f.image.messages if f.image else []) + (f.drill.messages if f.drill else []):
            if m.level != "info":
                rep.warnings.append(f"{f.name}: {m.text}")
    for n in st.not_simulated:
        rep.not_loaded.append(f"{n.get('name', S.MASK)} ({n.get('side', '')}) of the job file stackup")
    return rep


def _hole_object(des, name: str, h: E.Hole, x: float, y: float, u: float, z: str, height: str, mat: str):
    from ..model.design import Command
    r = h.d / 2
    if not h.slot:
        o = des.add_object("CreateCylinder", name, material=mat, CenterPosition=f"{_fmt(x / u)},{_fmt(y / u)},{z}",
                           Axis="Z", Radius=_fmt(r / u), Height=height)
    else:
        x1, y1 = h.x1 - (h.x - x), h.y1 - (h.y - y)
        L = math.hypot(x1 - x, y1 - y)
        nx, ny = ((y1 - y) / L, -(x1 - x) / L) if L > 0 else (0.0, -1.0)
        ux, uy = ((x1 - x) / L, (y1 - y) / L) if L > 0 else (1.0, 0.0)
        pts = [(x + nx * r, y + ny * r), (x1 + nx * r, y1 + ny * r), (x1 + ux * r, y1 + uy * r),
               (x1 - nx * r, y1 - ny * r), (x - nx * r, y - ny * r), (x - ux * r, y - uy * r), (x + nx * r, y + ny * r)]
        o = des.add_object("CreatePolyline", name, material=mat, Points=";".join(
            f"{_fmt(px / u)},{_fmt(py / u)},{z}" for px, py in pts), SegmentTypes="Line,Arc,Line,Arc",
            Closed="True", Covered="True")
        o.history.append(Command.new("SweepAlongVector", Vector=f"0,0,{height}"))
    o.orientation = S.BOARD_CS
    o.transparency = 0.0
    return o
