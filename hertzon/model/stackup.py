"""PCB layer stack (stackup) of a design: layers, thicknesses, materials, checks and Gerber job files.

The stackup is the one source of thicknesses, heights and materials of a printed circuit board in a
design. It is plain data (round-trips through the project JSON) and holds no geometry.

Rules
-----
* Layers are listed from the top to the bottom. Kinds: copper and dielectric. Solder mask, legend
  (silkscreen) and solder paste are never loaded: everything that is loaded is also simulated, and
  those are not. A job file that lists them gets a note saying so.
* z = 0 is the bottom face of the lowest copper layer.
* Every value is an expression with a unit, as everywhere in Hertzon (``35um``, ``1.4mil``, ``h_core``).
  Copper thicknesses also accept ounces (``1oz`` = 34.79 um).
* Every value remembers where it came from (:data:`ORIGINS`): read from a file, assumed (a default
  because the file gave nothing), from a template, a suggestion the user accepted, or typed by the user.
* :func:`check` never changes anything. It returns :class:`Issue` objects; an issue may carry a
  :class:`Fix` that :func:`apply_fix` applies when the user accepts it.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .expr import Evaluator, ExprError

COPPER, DIELECTRIC = "Copper", "Dielectric"
KINDS = (COPPER, DIELECTRIC)
MASK = "Solder Mask"                         # shown in the editor, not simulated yet (not implemented)
CORE, PREPREG = "Core", "Prepreg"
SHEET, SOLID = "Sheet", "Solid"            # how a copper layer is modelled
ORIGINS = ("file", "assumed", "template", "suggestion", "user")
FIELDS = ("thickness", "material", "permittivity", "loss_tangent")

OZ = 34.79e-6                                # copper thickness of 1 oz/ft^2 in metres
DEFAULT_THICKNESS = {COPPER: "35um"}
DEFAULT_COPPER_MATERIAL = "copper"
DEFAULT_DIELECTRIC = "FR4_epoxy"

# plausible thickness in metres: (lowest, highest, typical values)
RANGES = {
    COPPER: (5e-6, 210e-6, (12e-6, 18e-6, 35e-6, 70e-6, 105e-6)),
    DIELECTRIC: (25e-6, 6.4e-3, (0.1e-3, 0.2e-3, 0.5e-3, 0.8e-3, 1.0e-3, 1.5e-3)),
}
ER_RANGE = (1.0, 15.0)
ER_UNUSUAL = (1.9, 12.0)
TAND_MAX = 0.05
TOTAL_WARN = 0.10                            # deviation of the sum from the stated thickness, shown in colour
# unit mix-ups: true value = read value * factor
UNIT_FACTORS = (1e-3, 1e3, 0.0254, 1 / 0.0254)  # um/mm both ways, mil read as mm, mm read as mil
# values a KiCad board gets without anybody opening its stackup editor
KICAD_DEFAULT_DIELECTRIC = (4.5, 0.02)


# ================================================================================================
# data
# ================================================================================================
@dataclass
class StackLayer:
    name: str
    kind: str = DIELECTRIC
    thickness: str = ""                  # expression with unit; "" = unknown
    material: str = ""                   # material of the project or the library; "" = values below
    permittivity: str = ""               # dielectric: overrides the material when not empty
    loss_tangent: str = ""
    subtype: str = ""                    # dielectric: "Core" or "Prepreg"
    representation: str = SHEET          # copper: "Sheet" (boundary) or "Solid" (body with thickness)
    fill: str = ""                       # copper as a solid: material between the traces; "" = the dielectric
                                         # next to it towards the board centre (prepreg resin), or air on an
                                         # outer layer
    origin: dict[str, str] = field(default_factory=dict)   # field -> one of ORIGINS
    file_material: str = ""              # material name as written in the file it was read from

    def set(self, name: str, value: str, origin: str = "user") -> None:
        setattr(self, name, value)
        self.origin[name] = origin

    def origin_of(self, name: str) -> str:
        return self.origin.get(name, "user")


@dataclass
class Stackup:
    layers: list[StackLayer] = field(default_factory=list)
    nominal_thickness: str = ""          # board thickness the file or the user states (for the check only)
    source: str = "manual"               # "manual", "template", "job file <name>", ...
    cs: str = "Global"                   # coordinate system and plane of the board
    plane: str = "XY"
    copper_files: int = -1               # copper layers in the files that came with it (-1: unknown)
    file_layer_count: int = -1           # layer count the file states (-1: unknown)
    generator: str = ""                  # application that wrote the file
    # solder mask layers the file lists; shown in the editor as "not implemented yet", never simulated:
    # {"name": ..., "side": "top" | "bottom", "thickness": expression or ""}
    not_simulated: list[dict] = field(default_factory=list)

    def copper(self) -> list[int]:
        return [i for i, L in enumerate(self.layers) if L.kind == COPPER]

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Stackup":
        st = Stackup(**{k: v for k, v in d.items() if k in Stackup.__dataclass_fields__ and k != "layers"})
        st.not_simulated = [dict(x) for x in d.get("not_simulated", [])]
        st.layers = [StackLayer(**{k: v for k, v in L.items() if k in StackLayer.__dataclass_fields__})
                     for L in d.get("layers", [])]
        for L in st.layers:
            L.origin = dict(L.origin)
        return st

    def save(self, path: str | Path) -> None:
        """Own file format (.hzstack, JSON) to keep and share stackups."""
        d = self.to_dict()
        d["format"] = "hertzon-stackup"
        Path(path).write_text(json.dumps(d, indent=1), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Stackup":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("format") != "hertzon-stackup":
            raise ValueError(f"'{path}' is no Hertzon stackup file")
        return Stackup.from_dict(d)


@dataclass
class Fix:
    """A change the user can accept: (layer index, field, new value) triples."""
    label: str
    changes: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass
class Issue:
    level: str                           # "error", "warning" or "info"
    code: str
    text: str
    layer: int = -1                      # index into Stackup.layers, -1: the whole stackup
    field: str = ""
    fix: Fix | None = None


@dataclass
class LayerZ:
    index: int
    layer: StackLayer
    thickness: float                     # metres (nan if unknown)
    z0: float                            # bottom, metres
    z1: float                            # top, metres


# ================================================================================================
# values
# ================================================================================================
_OZ_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*oz\s*$", re.I)
_BARE_RE = re.compile(r"^\s*[0-9]*\.?[0-9]+(e[-+]?[0-9]+)?\s*$", re.I)


def thickness_m(L: StackLayer, ev: Evaluator) -> float | None:
    """Thickness in metres, None if empty or not evaluable."""
    t = str(L.thickness or "").strip()
    if not t:
        return None
    m = _OZ_RE.match(t)
    try:
        return float(m.group(1)) * OZ if m else ev.length(t)
    except (ExprError, ValueError):
        return None


def fmt_thickness(v: float) -> str:
    """Thickness as an expression with a readable unit."""
    if v < 0.1e-3:
        return f"{v * 1e6:.4g}um"
    return f"{v * 1e3:.6g}mm"


def _num(text: str, ev: Evaluator) -> float | None:
    t = str(text or "").strip()
    if not t:
        return None
    try:
        return ev.value(t)
    except ExprError:
        return None


def layer_er_tand(L: StackLayer, ev: Evaluator, project=None) -> tuple[float | None, float | None]:
    """Permittivity and loss tangent of a dielectric: own values, else those of its material."""
    er, td = _num(L.permittivity, ev), _num(L.loss_tangent, ev)
    if (er is None or td is None) and L.material:
        mat = _find_material(L.material, project)
        if mat is not None:
            er = er if er is not None else _num(mat.permittivity, ev)
            td = td if td is not None else _num(mat.loss_tangent, ev)
    return er, td


def _find_material(name: str, project=None):
    if project is not None:
        try:
            return project.material(name)
        except KeyError:
            return None
    from .materials import library_material
    return library_material(name)


def evaluate(st: Stackup, ev: Evaluator) -> list[LayerZ]:
    """z of every layer; z = 0 at the bottom of the lowest copper layer (no copper: bottom of the stack)."""
    ts = [thickness_m(L, ev) for L in st.layers]
    tv = [t if t is not None else 0.0 for t in ts]
    cu = st.copper()
    ref = cu[-1] if cu else len(st.layers) - 1
    below = sum(tv[ref + 1:]) if st.layers else 0.0
    z = sum(tv) - below          # top of the stack
    out = []
    for i, L in enumerate(st.layers):
        out.append(LayerZ(i, L, ts[i] if ts[i] is not None else math.nan, z - tv[i], z))
        z -= tv[i]
    return out


def fill_neighbour(st: Stackup, i: int) -> int | None:
    """Index of the dielectric whose material fills copper layer ``i`` between the traces: the prepreg
    next to it (else the first dielectric neighbour); None on an outer layer (air)."""
    nb = [j for j in (i - 1, i + 1) if 0 <= j < len(st.layers) and st.layers[j].kind == DIELECTRIC]
    if len(nb) < 2:
        return None
    return next((j for j in nb if st.layers[j].subtype == PREPREG), nb[0])


def fill_of(st: Stackup, i: int) -> str:
    """What fills the gaps between the traces of copper layer ``i``: its own ``fill`` material, else the
    prepreg (or other dielectric) next to it, on an outer layer air.
    Returned as a material name, or as "layer:<name>" for a layer that carries its own values."""
    L = st.layers[i]
    if L.fill:
        return L.fill
    j = fill_neighbour(st, i)
    if j is None:
        return "air"
    pick = st.layers[j]
    return pick.material or f"layer:{pick.name}"


def total_deviation(st: Stackup, ev: Evaluator) -> float | None:
    """(sum - stated) / stated, None without a valid stated board thickness."""
    try:
        nom = ev.length(st.nominal_thickness) if str(st.nominal_thickness or "").strip() else None
    except ExprError:
        return None
    return (total_thickness(st, ev) - nom) / nom if nom else None


def total_thickness(st: Stackup, ev: Evaluator) -> float:
    """Sum of the known thicknesses."""
    return sum(t for L in st.layers for t in [thickness_m(L, ev)] if t is not None)


# ================================================================================================
# templates
# ================================================================================================
def template(n_copper: int = 2, total: str = "1.6mm", origin: str = "template",
             ev: Evaluator | None = None) -> Stackup:
    """A usual symmetric stack: prepreg next to the outer layers, cores inside, prepreg 0.2 mm,
    35 um copper; the cores take the rest of the board thickness."""
    ev = ev or Evaluator()
    n = max(1, int(n_copper))
    T = ev.length(total)
    cu = 35e-6
    layers: list[StackLayer] = []

    def add(name, kind, t, **kw):
        L = StackLayer(name, kind, t, **kw)
        for f in ("thickness", "material", "permittivity", "loss_tangent"):
            if getattr(L, f):
                L.origin[f] = origin
        layers.append(L)

    if n == 1:
        add("L1 Top", COPPER, "35um", material=DEFAULT_COPPER_MATERIAL)
        add("Core", DIELECTRIC, fmt_thickness(max(T - cu, 25e-6)), material=DEFAULT_DIELECTRIC, subtype=CORE)
    else:
        kinds = [PREPREG if (i % 2 == 0 and n > 2) else CORE for i in range(n - 1)]
        if n > 2:
            kinds[-1] = PREPREG
        n_pp, n_core = kinds.count(PREPREG), kinds.count(CORE)
        pp = 0.2e-3
        rest = T - n * cu - n_pp * pp
        core = rest / n_core if n_core else 0.0
        if n_core and core < 50e-6:            # thin board: share the dielectric evenly
            pp = core = (T - n * cu) / (n - 1)
        for i in range(n):
            name = "L1 Top" if i == 0 else ("L%d Bottom" % n if i == n - 1 else "L%d" % (i + 1))
            add(name, COPPER, "35um", material=DEFAULT_COPPER_MATERIAL)
            if i < n - 1:
                k = kinds[i]
                add(f"{k} {i + 1}-{i + 2}", DIELECTRIC, fmt_thickness(pp if k == PREPREG else core),
                    material=DEFAULT_DIELECTRIC, subtype=k)
    return Stackup(layers=layers, nominal_thickness=total, source="template")


# ================================================================================================
# fixes
# ================================================================================================
def apply_fix(st: Stackup, fix: Fix) -> None:
    for i, f, v in fix.changes:
        st.layers[i].set(f, v, "suggestion")


def scale_dielectrics(st: Stackup, ev: Evaluator, target: float) -> Fix | None:
    """Scale all dielectric thicknesses by one factor so the board gets ``target``."""
    fixed = sum(t for L in st.layers if L.kind == COPPER for t in [thickness_m(L, ev)] if t)
    diel = [(i, thickness_m(L, ev)) for i, L in enumerate(st.layers) if L.kind == DIELECTRIC]
    have = sum(t for _, t in diel if t)
    if have <= 0 or target <= fixed:
        return None
    k = (target - fixed) / have
    return Fix(f"Scale the dielectrics by {k:.3f} to {fmt_thickness(target)}",
               [(i, "thickness", fmt_thickness(t * k)) for i, t in diel if t])


def adjust_core(st: Stackup, ev: Evaluator, target: float) -> Fix | None:
    """Change only the thickest dielectric (normally the core) so the board gets ``target``."""
    diel = [(i, thickness_m(L, ev)) for i, L in enumerate(st.layers) if L.kind == DIELECTRIC]
    diel = [(i, t) for i, t in diel if t]
    if not diel:
        return None
    i, t = max(diel, key=lambda x: x[1])
    new = t + target - total_thickness(st, ev)
    if new < RANGES[DIELECTRIC][0]:
        return None
    return Fix(f"Set '{st.layers[i].name}' to {fmt_thickness(new)}", [(i, "thickness", fmt_thickness(new))])


# ================================================================================================
# checks
# ================================================================================================
def _unit_suggestion(kind: str, v: float, text: str) -> tuple[float, str] | None:
    lo, hi, typ = RANGES[kind]
    if kind == COPPER and _BARE_RE.match(text) and 0.25 <= float(text) <= 6:
        return float(text) * OZ, f"{float(text):g}oz"
    best = None
    for k in UNIT_FACTORS:
        c = v * k
        if lo <= c <= hi:
            d = min(abs(math.log(c / t)) for t in typ)
            if best is None or d < best[0]:
                best = (d, c)
    return (best[1], fmt_thickness(best[1])) if best else None


def check(st: Stackup, ev: Evaluator, project=None) -> list[Issue]:
    """All problems of a stackup; nothing is changed."""
    out: list[Issue] = []
    Ls = st.layers
    if not st.copper():
        out.append(Issue("error", "no-copper", "The stackup has no copper layer."))
    # ---- order --------------------------------------------------------------------------------
    for i in range(len(Ls) - 1):
        if Ls[i].kind == COPPER and Ls[i + 1].kind == COPPER:
            out.append(Issue("error", "copper-copper",
                             f"'{Ls[i].name}' and '{Ls[i + 1].name}' are copper layers without a dielectric "
                             "between them.", i + 1))
    for i in ([0, len(Ls) - 1] if Ls and len(st.copper()) > 1 else []):
        if Ls[i].kind == DIELECTRIC:
            out.append(Issue("warning", "outer-dielectric", f"The outermost layer '{Ls[i].name}' "
                             "is a dielectric, not copper.", i))
    # ---- per layer ----------------------------------------------------------------------------
    for i, L in enumerate(Ls):
        if L.kind not in KINDS:
            out.append(Issue("error", "kind", f"'{L.name}' has the unknown kind '{L.kind}'.", i))
            continue
        t = thickness_m(L, ev)
        if not str(L.thickness).strip():
            d = DEFAULT_THICKNESS.get(L.kind, "")
            out.append(Issue("error", "no-thickness", f"'{L.name}' has no thickness.", i, "thickness",
                             Fix(f"Assume {d}", [(i, "thickness", d)]) if d else None))
        elif t is None:
            out.append(Issue("error", "bad-thickness", f"The thickness '{L.thickness}' of '{L.name}' cannot be "
                             "evaluated.", i, "thickness"))
        elif t <= 0:
            out.append(Issue("error", "bad-thickness", f"'{L.name}' has a thickness of zero or less.", i, "thickness"))
        else:
            lo, hi, _ = RANGES[L.kind]
            if not lo <= t <= hi:
                sug = _unit_suggestion(L.kind, t, str(L.thickness))
                text = (f"'{L.name}' is {fmt_thickness(t)} thick; {L.kind.lower()} is usually "
                        f"{fmt_thickness(lo)} to {fmt_thickness(hi)}.")
                if sug:
                    text += f" Probably a unit mix-up: {sug[1]}?"
                out.append(Issue("warning", "thickness-range", text, i, "thickness",
                                 Fix(f"Use {sug[1]}", [(i, "thickness", sug[1])]) if sug else None))
        if L.origin_of("thickness") == "assumed":
            out.append(Issue("info", "assumed", f"The thickness of '{L.name}' is assumed ({L.thickness}); the "
                             "file gave none. Please confirm it.", i, "thickness"))
        if L.kind == COPPER:
            if L.material and _find_material(L.material, project) is None:
                out.append(Issue("warning", "unknown-material", f"Unknown material '{L.material}' of '{L.name}'.",
                                 i, "material", Fix("Use copper", [(i, "material", DEFAULT_COPPER_MATERIAL)])))
            if L.representation not in (SHEET, SOLID):
                out.append(Issue("error", "representation", f"'{L.name}': unknown representation "
                                 f"'{L.representation}'.", i))
            continue
        out += _check_dielectric(i, L, ev, project, st)
    # ---- whole stack --------------------------------------------------------------------------
    if str(st.nominal_thickness or "").strip():
        try:
            ev.length(st.nominal_thickness)
        except ExprError:
            out.append(Issue("error", "bad-nominal", f"The board thickness '{st.nominal_thickness}' cannot be "
                             "evaluated."))
    # the sum against the stated board thickness is no problem of the list: the editor shows both and the
    # deviation, and the user decides (see total_deviation, scale_dielectrics, adjust_core)
    ncu = len(st.copper())
    if st.copper_files >= 0 and st.copper_files != ncu:
        out.append(Issue("warning", "copper-files", f"The stackup has {ncu} copper layers, the files hold "
                         f"{st.copper_files} copper layers."))
    if st.file_layer_count >= 0 and st.file_layer_count != ncu:
        out.append(Issue("warning", "layer-count", f"The stackup has {ncu} copper layers, the file states "
                         f"{st.file_layer_count}."))
    return out


def _check_dielectric(i: int, L: StackLayer, ev: Evaluator, project, st: Stackup) -> list[Issue]:
    out: list[Issue] = []
    if L.material and _find_material(L.material, project) is None:
        sug = suggest_materials(L, ev, project)
        fx = Fix(f"Use '{sug[0][0]}'", [(i, "material", sug[0][0])]) if sug else None
        out.append(Issue("warning", "unknown-material", f"Unknown material '{L.material}' of '{L.name}'.",
                         i, "material", fx))
    er, td = layer_er_tand(L, ev, project)
    if er is None:
        out.append(Issue("error", "no-permittivity", f"'{L.name}' has neither a material nor a permittivity.",
                         i, "material", Fix(f"Use {DEFAULT_DIELECTRIC}", [(i, "material", DEFAULT_DIELECTRIC)])))
    elif not ER_RANGE[0] <= er <= ER_RANGE[1]:
        out.append(Issue("warning", "er-range", f"'{L.name}': er = {er:g} lies outside {ER_RANGE[0]:g} to "
                         f"{ER_RANGE[1]:g}.", i, "permittivity"))
    elif not ER_UNUSUAL[0] <= er <= ER_UNUSUAL[1]:
        out.append(Issue("info", "er-unusual", f"'{L.name}': er = {er:g} is unusual for a circuit board.", i,
                         "permittivity"))
    if td is not None:
        if td < 0:
            out.append(Issue("error", "tand-negative", f"'{L.name}': negative loss tangent.", i, "loss_tangent"))
        elif td > TAND_MAX:
            fx = (Fix(f"Use {td / 100:g} (value given in percent)", [(i, "loss_tangent", f"{td / 100:g}")])
                  if td / 100 <= TAND_MAX else None)
            out.append(Issue("warning", "tand-range", f"'{L.name}': loss tangent {td:g} is very high"
                             + (" - given in percent?" if fx else "."), i, "loss_tangent", fx))
    if (L.kind == DIELECTRIC and "kicad" in st.generator.lower() and L.origin_of("permittivity") == "file"
            and er is not None and td is not None
            and (round(er, 3), round(td, 4)) == KICAD_DEFAULT_DIELECTRIC):
        out.append(Issue("info", "kicad-default", f"'{L.name}' has KiCad's default values (er 4.5, tan d 0.02); "
                         "they are often never set. Compare them with the data sheet of the board maker.", i,
                         "permittivity"))
    if L.material and L.file_material and L.permittivity and er is not None:
        mat = _find_material(L.material, project)
        mer = _num(mat.permittivity, ev) if mat is not None else None
        if mer and abs(mer - er) / mer > 0.05 and _norm(L.file_material) and \
                _name_match(L.file_material, L.material):
            out.append(Issue("warning", "name-vs-values", f"'{L.name}': the file names '{L.file_material}' "
                             f"(er {mer:g}) but gives er {er:g}.", i, "permittivity",
                             Fix(f"Use the values of '{L.material}'", [(i, "permittivity", ""),
                                                                      (i, "loss_tangent", "")])))
    return out


# ================================================================================================
# material suggestions
# ================================================================================================
_DROP = re.compile(r"\(tm\)|rogers|isola|panasonic|taconic|arlon|shengyi|ventec|rt/duroid|duroid|epoxy|laminate|"
                   r"[^a-z0-9]")


def _norm(name: str) -> str:
    return _DROP.sub("", str(name).lower())


def _name_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    return len(na) >= 3 and len(nb) >= 3 and (na in nb or nb in na)


def suggest_materials(L: StackLayer, ev: Evaluator, project=None, n: int = 3) -> list[tuple[str, str]]:
    """Materials of the project and the library that fit the layer: [(name, reason)], best first.
    By name (as written in the file, or the material field) first, then by er and tan d."""
    from .materials import is_conductor, library_materials
    mats = list(project.all_materials()) if project is not None else library_materials()
    mats = [m for m in mats if not m.pec and not is_conductor(m) and m.name not in ("vacuum", "air")]
    er, td = _num(L.permittivity, ev), _num(L.loss_tangent, ev)
    out: list[tuple[str, str]] = []
    for key in (L.file_material, L.material):
        if key:
            for m in mats:
                if _name_match(key, m.name) and m.name not in [x[0] for x in out]:
                    out.append((m.name, f"name '{key}'"))
    if er is not None:
        scored = []
        for m in mats:
            mer, mtd = _num(m.permittivity, ev), _num(m.loss_tangent, ev)
            if not mer or mer <= 1.0:
                continue
            d = abs(mer - er) / er
            if d > 0.10:
                continue
            if td is not None and mtd is not None:
                d += 0.2 * abs(mtd - td) / max(td, 1e-3)
            scored.append((d, m.name, mer, mtd))
        for d, name, mer, mtd in sorted(scored)[:n]:
            if name not in [x[0] for x in out]:
                exact = "exact" if d < 1e-6 else f"er {mer:g}, tan d {mtd:g}"
                out.append((name, exact))
    return out[:n]


# ================================================================================================
# Gerber job files
# ================================================================================================
def _jnum(v) -> float | None:
    """A number of a job file: number, or text like '1.6', '1,6', '1.6mm' (job files are in mm)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(",", ".")
    m = re.match(r"^([-+]?[0-9]*\.?[0-9]+(?:e[-+]?[0-9]+)?)\s*(mm|um|µm|mil|in)?$", s)
    if not m:
        return None
    x = float(m.group(1))
    return x * {"um": 1e-3, "µm": 1e-3, "mil": 0.0254, "in": 25.4}.get(m.group(2) or "mm", 1.0)


def _get(d: dict, *names):
    """Value of the first key matching one of ``names``, case-insensitive."""
    if not isinstance(d, dict):
        return None
    low = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def read_gbrjob(src: str | Path | dict) -> tuple[Stackup, list[Issue]]:
    """Stackup of a Gerber job file. Missing values are filled with assumed defaults and marked as such;
    the returned notes say what the file lacked and what was not loaded. Run :func:`check` afterwards."""
    if isinstance(src, dict):
        data, fname = src, "job file"
    else:
        fname = Path(src).name
        text = Path(src).read_text(encoding="utf-8-sig", errors="replace")
        data = json.loads(text)
    notes: list[Issue] = []
    head = _get(data, "Header") or {}
    gen = _get(head, "GenerationSoftware") or {}
    generator = " ".join(str(x) for x in (_get(gen, "Vendor"), _get(gen, "Application"), _get(gen, "Version"))
                         if x)
    specs = _get(data, "GeneralSpecs") or {}
    board_t = _jnum(_get(specs, "BoardThickness"))
    n_layers = _get(specs, "LayerNumber")
    try:
        n_layers = int(n_layers) if n_layers is not None else -1
    except (TypeError, ValueError):
        n_layers = -1
    files = _get(data, "FilesAttributes") or []
    n_cu_files = sum(1 for f in files if str(_get(f, "FileFunction") or "").lower().startswith("copper"))
    stack = _get(data, "MaterialStackup")

    if not stack:
        n = n_layers if n_layers > 0 else (n_cu_files or 2)
        total = fmt_thickness(board_t * 1e-3) if board_t else "1.6mm"
        st = template(n, total, origin="assumed")
        notes.append(Issue("warning", "job-no-stackup", f"{fname} holds no layer stack. A usual {n}-layer stack of "
                           f"{total} is assumed; please check every value."))
        if not board_t:
            notes.append(Issue("warning", "job-no-thickness", f"{fname} states no board thickness; 1.6 mm assumed."))
    else:
        st = Stackup()
        skipped: list[str] = []
        for e in stack:
            typ = str(_get(e, "Type") or "").strip().lower().replace(" ", "")
            name = str(_get(e, "Name") or "").strip()
            t = _jnum(_get(e, "Thickness"))
            if typ in ("copper",):
                kind = COPPER
            elif typ in ("dielectric", "core", "prepreg"):
                kind = DIELECTRIC
            else:                                          # solder mask, legend, paste: not simulated
                skipped.append(name or typ or "?")
                if typ in ("soldermask", "mask"):
                    st.not_simulated.append({"name": name or MASK, "side": "bottom" if st.copper() else "top",
                                             "thickness": fmt_thickness(t * 1e-3) if t else ""})
                continue
            L = StackLayer(name or kind, kind)
            if t is not None and t > 0:
                L.set("thickness", fmt_thickness(t * 1e-3), "file")
            if kind == COPPER:
                L.set("material", DEFAULT_COPPER_MATERIAL, "assumed")
            else:
                er, td = _jnum(_get(e, "DielectricConstant", "Er")), _jnum(_get(e, "LossTangent", "TanD"))
                if er is not None:
                    L.set("permittivity", f"{er:g}", "file")
                if td is not None:
                    L.set("loss_tangent", f"{td:g}", "file")
                mat = str(_get(e, "Material") or "").strip()
                L.file_material = mat
                sub = " ".join(str(x) for x in (typ, name, mat, _get(e, "Notes") or "")).lower()
                L.subtype = PREPREG if "prepreg" in sub else (CORE if "core" in sub else "")
                if mat or er is not None:
                    sug = suggest_materials(L, Evaluator())
                    if mat and sug and sug[0][1].startswith("name"):
                        L.set("material", sug[0][0], "file")
                    elif er is None:
                        L.set("material", DEFAULT_DIELECTRIC, "assumed")
                else:
                    L.set("material", DEFAULT_DIELECTRIC, "assumed")
            st.layers.append(L)
        if skipped:
            notes.append(Issue("info", "job-not-loaded", "Not loaded (not simulated): " + ", ".join(skipped) + "."))
        _fill_missing(st, board_t)
        if not board_t:
            notes.append(Issue("info", "job-no-thickness", f"{fname} states no board thickness."))
    st.nominal_thickness = fmt_thickness(board_t * 1e-3) if board_t else st.nominal_thickness
    if not board_t and st.source == "template":
        st.nominal_thickness = ""
    st.source = f"job file {fname}"
    st.generator = generator
    st.copper_files = n_cu_files if files else -1
    st.file_layer_count = n_layers
    return st, notes


def _fill_missing(st: Stackup, board_t_mm: float | None) -> None:
    """Missing thicknesses: defaults for copper; the dielectrics share what is left of the board
    thickness (or 1.5 mm). All marked as assumed."""
    ev = Evaluator()
    for L in st.layers:
        if not L.thickness and L.kind in DEFAULT_THICKNESS:
            L.set("thickness", DEFAULT_THICKNESS[L.kind], "assumed")
    missing = [L for L in st.layers if not L.thickness]
    if not missing:
        return
    T = board_t_mm * 1e-3 if board_t_mm else 1.6e-3
    rest = T - total_thickness(st, ev)
    each = rest / len(missing) if rest > len(missing) * RANGES[DIELECTRIC][0] else 1.5e-3 / len(missing)
    for L in missing:
        L.set("thickness", fmt_thickness(each), "assumed")


# ================================================================================================
# coupling to the geometry: variables, board coordinate systems, substrate
# ================================================================================================
VAR_PREFIX = "stk_"
BOARD_CS = "stk_Board"
SUBSTRATE_GROUP = "Stackup"
PLANE_AXES = {"XY": ("1,0,0", "0,1,0"), "YZ": ("0,1,0", "0,0,1"), "ZX": ("0,0,1", "1,0,0")}


def slugs(st: Stackup) -> list[str]:
    """A unique identifier per layer, made from its name ('F.Cu/In1.Cu' -> 'F_Cu_In1_Cu')."""
    out: list[str] = []
    for i, L in enumerate(st.layers):
        s = re.sub(r"[^0-9A-Za-z]+", "_", L.name).strip("_") or f"L{i + 1}"
        if s[0].isdigit():
            s = "L" + s
        base, k = s, 2
        while s in out:
            s, k = f"{base}_{k}", k + 1
        out.append(s)
    return out


def _thickness_expr(L: StackLayer, ev: Evaluator) -> str:
    """The thickness as an expression the evaluator understands (ounces become um)."""
    t = str(L.thickness or "").strip()
    if _OZ_RE.match(t) or (t and ev.check(t)):
        v = thickness_m(L, ev)
        return fmt_thickness(v) if v else "0"
    return t or "0"


def variables(st: Stackup, ev: Evaluator) -> dict[str, str]:
    """Design variables of the stackup: per layer the thickness ``stk_<layer>_t`` and the height of its
    bottom ``stk_<layer>_z`` (z = 0 at the bottom of the lowest copper layer), for copper layers the height
    of the copper sheet ``stk_<layer>_zs``, and ``stk_total``."""
    names = slugs(st)
    tv = [f"{VAR_PREFIX}{s}_t" for s in names]
    out = {v: _thickness_expr(L, ev) for v, L in zip(tv, st.layers)}
    cu = st.copper()
    ref = cu[-1] if cu else len(st.layers) - 1
    for i, s in enumerate(names):
        if i < ref:
            z = " + ".join(tv[i + 1:ref + 1])
        elif i == ref:
            z = "0"
        else:
            z = "-(" + " + ".join(tv[ref + 1:i + 1]) + ")"
        out[f"{VAR_PREFIX}{s}_z"] = z
    out[f"{VAR_PREFIX}total"] = " + ".join(tv) if tv else "0"
    # the copper sheet lies on the face towards the dielectric it does not share its layer with: on an
    # outer layer the board, on an inner layer the side away from the fill (the fill takes the copper slab)
    for i in cu:
        s = names[i]
        j = fill_neighbour(st, i)
        if j is None:
            other = [k for k in (i - 1, i + 1) if 0 <= k < len(st.layers) and st.layers[k].kind == DIELECTRIC]
            below = bool(other) and other[0] > i
        else:
            below = j < i                     # fill above: sheet on the bottom face
        out[f"{VAR_PREFIX}{s}_zs"] = (f"{VAR_PREFIX}{s}_z" if below
                                      else f"{VAR_PREFIX}{s}_z + {VAR_PREFIX}{s}_t")
    return out


def sync_variables(des, st: Stackup | None, ev: Evaluator) -> list[str]:
    """Write the stackup variables into the design and remove stackup variables that no longer exist.
    The ``stk_`` variables belong to the stackup: it overwrites them. Returns the names written."""
    new = variables(st, ev) if st else {}
    for k in [k for k in des.variables if k.startswith(VAR_PREFIX) and k not in new]:
        del des.variables[k]
    des.variables.update(new)
    return list(new)


def sync_coordinate_systems(des, st: Stackup) -> list[str]:
    """The board coordinate system (``stk_Board``, the stackup's plane as its XY plane) and one coordinate
    system per copper layer at the height of its copper sheet, to draw on. Existing ones are updated."""
    from .design import CoordSys
    x, y = PLANE_AXES.get(st.plane, PLANE_AXES["XY"])
    want = [(BOARD_CS, st.cs, {"Mode": "Axis/Position", "Origin": "0,0,0", "XAxis": x, "YPoint": y})]
    names = slugs(st)
    for i in st.copper():
        want.append((f"{VAR_PREFIX}{names[i]}", BOARD_CS,
                     {"Mode": "Axis/Position", "Origin": f"0,0,{VAR_PREFIX}{names[i]}_zs",
                      "XAxis": "1,0,0", "YPoint": "0,1,0"}))
    keep = {n for n, _, _ in want}
    des.coord_systems = [c for c in des.coord_systems
                         if not (c.name.startswith(VAR_PREFIX) and c.name not in keep
                                 and c.name != BOARD_CS)]
    for name, ref, props in want:
        cs = next((c for c in des.coord_systems if c.name == name), None)
        if cs is None:
            cs = CoordSys(name, "Relative", ref)
            des.coord_systems.append(cs)
        cs.reference = ref
        cs.props.update(props)
    return [n for n, _, _ in want]


def _layer_material(project, st: Stackup, i: int, slug: str, ev: Evaluator) -> str:
    """The material of a dielectric layer: its own material, or a project material ``stk_<layer>`` that
    carries the layer's own er / tan d (created or updated)."""
    from .materials import Material
    L = st.layers[i]
    if L.material and not L.permittivity and not L.loss_tangent:
        return L.material
    er, td = layer_er_tand(L, ev, project)
    name = f"{VAR_PREFIX}{slug}"
    m = next((m for m in project.materials if m.name == name), None)
    if m is None:
        m = Material(name=name, color="#8fb070", description=f"Stackup layer {L.name}")
        project.materials.append(m)
    m.permittivity = L.permittivity or (f"{er:g}" if er is not None else "1")
    m.loss_tangent = L.loss_tangent or (f"{td:g}" if td is not None else "0")
    return name


def _at_z(loop2d: str, z: str) -> str:
    """'x,y;x,y' -> 'x,y,z;x,y,z'."""
    return ";".join(f"{q.strip()},{z}" for q in loop2d.split(";") if q.strip())


def _slab(des, obj_name: str, mat: str, x0, y0, xsize, ysize, z: str, t: str, outline) -> "object":
    """Box (rectangle) or swept outline (board shape) of one substrate slab; an existing object keeps the
    rest of its history (holes subtracted by the PCB import)."""
    from .design import Command
    if outline is None:
        first = [Command.new("CreateBox", Position=f"{x0},{y0},{z}", XSize=xsize, YSize=ysize, ZSize=t)]
    else:
        pts, holes = outline
        first = [Command.new("CreatePolyline", Points=_at_z(pts, z), Closed="True", Covered="True",
                             Holes="|".join(_at_z(h, z) for h in holes.split("|") if h.strip())),
                 Command.new("SweepAlongVector", Vector=f"0,0,{t}")]
    if des.has_obj(obj_name):
        o = des.obj(obj_name)
        rest = o.history[1:]
        if o.history[0].kind == "CreatePolyline" and rest and rest[0].kind == "SweepAlongVector":
            rest = rest[1:]
        o.history = first + rest
        o.material = mat
        return o, False
    o = des.add_object(first[0].kind, obj_name, material=mat, **first[0].props)
    o.history += first[1:]
    return o, True


def build_substrate(project, des, st: Stackup, ev: Evaluator, x0: str = "0", y0: str = "0", xsize: str = "1",
                    ysize: str = "1", outline: tuple[str, str] | None = None) -> list[str]:
    """One box per dielectric layer (rectangle x0, y0, xsize, ysize in the board plane, model units or
    expressions), with the stackup variables for height and thickness, in the group "Stackup", and for each
    inner copper layer a box of its fill material over the copper thickness. Boxes made
    before (same name) are updated, boxes of layers that no longer exist are removed. Variables and
    coordinate systems are synced first. Copper is not created here: it comes from drawing or the import."""
    sync_variables(des, st, ev)
    sync_coordinate_systems(des, st)
    ev = des.evaluator(project)
    names = slugs(st)
    made: list[str] = []
    from .materials import default_color
    for i, L in enumerate(st.layers):
        if L.kind != DIELECTRIC:
            continue
        s = names[i]
        obj_name = f"Sub_{s}"
        mat = _layer_material(project, st, i, s, ev)
        o, new = _slab(des, obj_name, mat, x0, y0, xsize, ysize, f"{VAR_PREFIX}{s}_z", f"{VAR_PREFIX}{s}_t", outline)
        if new:
            o.color = default_color(mat) if mat != f"{VAR_PREFIX}{s}" else "#8fb070"
            o.transparency = 0.6
        o.orientation = BOARD_CS
        o.group = SUBSTRATE_GROUP
        made.append(obj_name)
    # inner copper layers: the slab of the copper layer is filled with the fill material (the copper sheet
    # lies on its face; solid copper from the import is cut out of it), so no air gap is left behind
    for i in st.copper():
        j = fill_neighbour(st, i)
        L = st.layers[i]
        if j is None and not L.fill:
            continue
        s = names[i]
        mat = L.fill or _layer_material(project, st, j, names[j], ev)
        obj_name = f"Fill_{s}"
        o, new = _slab(des, obj_name, mat, x0, y0, xsize, ysize, f"{VAR_PREFIX}{s}_z", f"{VAR_PREFIX}{s}_t", outline)
        if new:
            o.color = "#a9c48a"
            o.transparency = 0.6
        o.orientation = BOARD_CS
        o.group = SUBSTRATE_GROUP
        made.append(obj_name)
    for o in [o for o in des.objects if o.group == SUBSTRATE_GROUP and o.name.startswith(("Sub_", "Fill_"))
              and o.name not in made and o.consumed_by is None]:
        des.objects.remove(o)
    return made


def substrate_rect(des) -> tuple[str, str, str, str] | None:
    """Rectangle (x0, y0, xsize, ysize) of the substrate made before, None if there is none."""
    for o in des.objects:
        if o.group == SUBSTRATE_GROUP and o.name.startswith("Sub_") and o.history and o.history[0].kind == "CreateBox":
            p = o.history[0].props
            x0, y0 = [x.strip() for x in p.get("Position", "0,0,0").split(",")][:2]
            return x0, y0, p.get("XSize", "1"), p.get("YSize", "1")
    return None


def substrate_outline(des) -> tuple[str, str] | None:
    """Board shape (outline points, holes; 'x,y;...' in the board plane) of a substrate made from an
    outline before, None if there is none."""
    for o in des.objects:
        if o.group == SUBSTRATE_GROUP and o.name.startswith("Sub_") and o.history and o.history[0].kind == "CreatePolyline":
            p = o.history[0].props

            def flat(loop: str) -> str:
                return ";".join(",".join(q.split(",")[:2]) for q in loop.split(";") if q.strip())
            return flat(p.get("Points", "")), "|".join(flat(h) for h in p.get("Holes", "").split("|") if h.strip())
    return None


def apply_to_design(project, des, st: Stackup | None, substrate: tuple[str, str, str, str] | None = None) -> list[str]:
    """Store the stackup in the design and bring what depends on it up to date: the ``stk_`` variables, the
    board coordinate systems and, when asked for or made before, the substrate. Returns the objects made."""
    des.stackup = st
    ev = des.evaluator(project)
    if st is None:
        sync_variables(des, None, ev)
        return []
    sync_variables(des, st, ev)
    sync_coordinate_systems(des, st)
    rect = substrate or substrate_rect(des)
    if rect:
        return build_substrate(project, des, st, des.evaluator(project), *rect)
    shape = substrate_outline(des)
    return build_substrate(project, des, st, des.evaluator(project), outline=shape) if shape else []
