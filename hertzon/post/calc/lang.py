"""The Fields Calculator language: tokenizer, parser and the static analysis (type, unit, typeset formula).

Text syntax (the same names as the report expressions where they overlap)::

    0.5*omega*eps0*eps_r*tand*|E|^2           scalar field, W/m^3
    flux(Port1, re(S))                         Poynting flux through a face, W
    int_line(Line1, E)                         tangential line integral, V
    int_vol(Substrate, 0.5*sigma*|E|^2)        volume integral
    x(E, CS1), rho(H, CS1)                     components in a coordinate system
    max(Box1, |E|), at(point(1mm, 0, 2mm), E)  reductions
    flip(Port1), face(Box1, 3), cut_xy(2mm)    geometry: reversed normal, one face, a cut plane

Field symbols are complex peak phasors. The analysis checks types and units while typing and gives the
formula as LaTeX (mathtext subset) for the typeset preview."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from . import units as UN
from .units import ONE, U

EPS0, MU0, C0, Z0 = 8.8541878128e-12, 1.25663706212e-6, 299792458.0, 376.730313668


class CalcError(ValueError):
    """An error in an expression; pos/end: the characters of the source it refers to (or None)."""

    def __init__(self, msg: str, pos: int | None = None, end: int | None = None):
        super().__init__(msg)
        self.pos, self.end = pos, end


# ------------------------------------------------------------------------------------------------
# symbols and functions
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Sym:
    vec: bool
    field: bool
    unit: tuple
    cx: bool
    tex: str
    doc: str
    surf: bool = False          # only on faces (needs the face normal)
    value: float | None = None  # constants


SYMBOLS: dict[str, Sym] = {
    "E": Sym(True, True, U(V=1, m=-1), True, r"\mathbf{E}", "electric field (peak phasor)"),
    "H": Sym(True, True, U(A=1, m=-1), True, r"\mathbf{H}", "magnetic field (peak phasor)"),
    "D": Sym(True, True, U(A=1, s=1, m=-2), True, r"\mathbf{D}", "electric flux density eps0*eps_r*E"),
    "B": Sym(True, True, U(V=1, s=1, m=-2), True, r"\mathbf{B}", "magnetic flux density mu0*mu_r*H"),
    "J": Sym(True, True, U(A=1, m=-2), True, r"\mathbf{J}",
             "volume current (sigma + omega*eps0*eps_r*tand)*E, as in the J field plots"),
    "Js": Sym(True, True, U(A=1, m=-1), True, r"\mathbf{J}_s",
              "surface current n x H (on a sheet: n x (H+ - H-)), faces only", surf=True),
    "S": Sym(True, True, U(V=1, A=1, m=-2), True, r"\mathbf{S}", "Poynting vector 1/2 E x H* (time average)"),
    "n": Sym(True, True, ONE, False, r"\mathbf{n}", "unit normal of the face, faces only", surf=True),
    "x": Sym(False, True, U(m=1), False, "x", "x coordinate (global)"),
    "y": Sym(False, True, U(m=1), False, "y", "y coordinate (global)"),
    "z": Sym(False, True, U(m=1), False, "z", "z coordinate (global)"),
    "eps0": Sym(False, False, U(A=1, s=1, V=-1, m=-1), False, r"\varepsilon_0", "vacuum permittivity", value=EPS0),
    "mu0": Sym(False, False, U(V=1, s=1, A=-1, m=-1), False, r"\mu_0", "vacuum permeability", value=MU0),
    "c0": Sym(False, False, U(m=1, s=-1), False, "c_0", "speed of light", value=C0),
    "Z0": Sym(False, False, U(V=1, A=-1), False, "Z_0", "free-space impedance", value=Z0),
    "pi": Sym(False, False, ONE, False, r"\pi", "pi", value=3.141592653589793),
    "j": Sym(False, False, ONE, True, "j", "imaginary unit"),
    "f": Sym(False, False, U(s=-1), False, "f", "frequency of the solution"),
    "omega": Sym(False, False, U(s=-1), False, r"\omega", "angular frequency 2*pi*f"),
    "k0": Sym(False, False, U(m=-1), False, "k_0", "free-space wave number"),
    "sigma": Sym(False, True, U(A=1, V=-1, m=-1), False, r"\sigma", "bulk conductivity of the material"),
    "eps_r": Sym(False, True, ONE, False, r"\varepsilon_r", "relative permittivity of the material"),
    "mu_r": Sym(False, True, ONE, False, r"\mu_r", "relative permeability of the material"),
    "tand": Sym(False, True, ONE, False, r"\tan\delta", "dielectric loss tangent of the material"),
    "tandm": Sym(False, True, ONE, False, r"\tan\delta_m", "magnetic loss tangent of the material"),
}
PEAK_FIELDS = {"E", "H", "D", "B", "J", "Js"}
ALIASES = {"ε0": "eps0", "ε₀": "eps0", "μ0": "mu0", "μ₀": "mu0", "ω": "omega", "π": "pi", "σ": "sigma",
           "εr": "eps_r", "εᵣ": "eps_r", "μr": "mu_r", "abs": "mag",
           "Re": "re", "Im": "im"}

# function name -> (parameters, description); the parameter text is shown as signature help
SIGNATURES: dict[str, tuple[list[str], str]] = {
    "mag": (["x"], "magnitude |x|; for vectors sqrt(|x|^2+|y|^2+|z|^2) of the peak phasor"),
    "re": (["x"], "real part"), "im": (["x"], "imaginary part"), "conj": (["x"], "complex conjugate"),
    "phase": (["s: scalar"], "phase angle in rad"),
    "rms": (["x"], "RMS value = peak phasor / sqrt(2)"),
    "inst": (["x"], "instantaneous value Re{x e^(j phi)} at the phase of the context"),
    "atphase": (["x", "phi: number"], "Re{x e^(j phi)} at a fixed phase, e.g. 30deg"),
    "dot": (["a: vector", "b: vector"], "dot product a.b"), "cross": (["a: vector", "b: vector"], "cross product a x b"),
    "curl": (["F: vector field"], "curl of F"), "div": (["F: vector field"], "divergence of F"),
    "grad": (["f: scalar field"], "gradient of f"),
    "unit": (["F: vector"], "unit vector F/|F|"), "sqrt": (["x"], "square root"),
    "exp": (["x: dimensionless"], "e^x"), "ln": (["x: dimensionless"], "natural logarithm"),
    "log10": (["x: dimensionless"], "logarithm to base 10"),
    "sin": (["x: dimensionless"], "sine"), "cos": (["x: dimensionless"], "cosine"),
    "tan": (["x: dimensionless"], "tangent"), "atan2": (["y", "x"], "angle of the point (x, y) in rad"),
    "dB": (["x"], "20 log10|x| (field quantities)"), "dB10": (["x"], "10 log10|x| (power quantities)"),
    "rs": (["sigma: S/m"], "surface resistance sqrt(omega mu0 / (2 sigma)) of a good conductor"),
    "x": (["F: vector", "cs?: coordinate system"], "x component (default Global)"),
    "y": (["F: vector", "cs?: coordinate system"], "y component"),
    "z": (["F: vector", "cs?: coordinate system"], "z component"),
    "rho": (["F: vector", "cs?: coordinate system"], "rho component (cylindrical, about the z axis of cs)"),
    "phi": (["F: vector", "cs?: coordinate system"], "phi component (cylindrical and spherical)"),
    "r": (["F: vector", "cs?: coordinate system"], "r component (spherical)"),
    "theta": (["F: vector", "cs?: coordinate system"], "theta component (spherical)"),
    "normal": (["F: vector"], "normal part F.n (faces only)"),
    "tangent": (["F: vector"], "tangential part F - (F.n) n (faces only)"),
    "int_line": (["geometry: line", "f: field"], "line integral; vectors give the tangential integral F.dl"),
    "circ": (["geometry: closed line", "F: vector field"], "circulation, closed line integral F.dl"),
    "int_surf": (["geometry: face", "f: field"], "surface integral f dA; vectors integrate per component"),
    "flux": (["geometry: face", "F: vector field"], "flux F.n dA (direction: the face normal)"),
    "int_vol": (["geometry: volume", "f: field"], "volume integral f dV"),
    "max": (["geometry", "f: real scalar"], "maximum over the geometry"),
    "min": (["geometry", "f: real scalar"], "minimum over the geometry"),
    "avg": (["geometry: line/face/volume", "f: scalar"], "mean value"),
    "at": (["geometry: point", "f: field"], "value at a point"),
    "flip": (["geometry: face or line"], "reverses the normal (faces) or the direction (lines)"),
    "face": (["object", "index"], "one face of an object (face index as in face selection)"),
    "cut_xy": (["z"], "cut plane z = const through the whole solution"),
    "cut_yz": (["x"], "cut plane x = const through the whole solution"),
    "cut_xz": (["y"], "cut plane y = const through the whole solution"),
    "point": (["x", "y", "z"], "a point (numbers without unit are in model units)"),
    "S": (["i: port", "j: port"], "S parameter S_ij of the solution (S alone is the Poynting vector)"),
    "Y": (["i: port", "j: port"], "Y parameter Y_ij in S"),
    "Z": (["i: port", "j: port"], "Z parameter Z_ij in ohm"),
    "VSWR": (["i: port"], "voltage standing wave ratio at port i"),
}
GEOM_FN = {"int_vol": {3}, "int_surf": {2}, "flux": {2}, "int_line": {1}, "circ": {1}, "max": {0, 1, 2, 3},
           "min": {0, 1, 2, 3}, "avg": {1, 2, 3}, "at": {0}}
GEOM_CTOR = {"face", "cut_xy", "cut_yz", "cut_xz", "point", "flip"}
# network parameters of the solution: (number of port arguments, unit, complex)
NET_FN = {"S": (2, ONE, True), "Y": (2, U(A=1, V=-1), True), "Z": (2, U(V=1, A=-1), True), "VSWR": (1, ONE, False)}
COMP_FN = ("x", "y", "z", "rho", "phi", "r", "theta")
DIM_NAME = ["point", "line", "face", "volume"]
CONVENTIONS = {"peak": "peak phasor", "rms": "RMS", "inst": "instantaneous at the context phase",
               "fixedphase": "instantaneous at a fixed phase", "tavg": "time average"}


# ------------------------------------------------------------------------------------------------
# tokenizer and parser
# ------------------------------------------------------------------------------------------------
@dataclass
class Tok:
    t: str           # "num", "id", "op"
    v: str
    pos: int
    end: int
    unit: str = ""


_UNIT_ALT = "|".join(sorted((re.escape(u) for u in UN.NUMBER_UNITS), key=len, reverse=True))
_NUM = re.compile(r"(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)(?:(\s?)(" + _UNIT_ALT + r")(?![A-Za-z_0-9]))?")
_ID = re.compile(r"[A-Za-z_Ͱ-Ͽ][A-Za-z_0-9Ͱ-Ͽ₀-₉ᵣ]*")
_SPECIAL = (("∇×", "curl"), ("∇·", "div"), ("∇", "grad"), ("√", "sqrt"))
_OPS = "+-*/^(),|·×[]"


def tokenize(src: str) -> list[Tok]:
    out: list[Tok] = []
    i = 0
    while i < len(src):
        c = src[i]
        if c.isspace():
            i += 1
            continue
        sp = next((s for s in _SPECIAL if src.startswith(s[0], i)), None)
        if sp:
            out.append(Tok("id", sp[1], i, i + len(sp[0])))
            i += len(sp[0])
            continue
        if c in "²³":
            out += [Tok("op", "^", i, i + 1), Tok("num", "2" if c == "²" else "3", i, i + 1)]
            i += 1
            continue
        m = _NUM.match(src, i)
        if m:
            unit = m.group(3) or ""
            if unit and m.group(2) and unit in UN.SHORT_UNITS:   # "2 S": S is the Poynting vector, not siemens
                out.append(Tok("num", m.group(1), i, i + len(m.group(1))))
                i += len(m.group(1))
                continue
            out.append(Tok("num", m.group(1), i, m.end(), unit))
            i = m.end()
            continue
        m = _ID.match(src, i)
        if m:
            w = m.group(0)
            out.append(Tok("id", ALIASES.get(w, w), i, m.end()))
            i = m.end()
            continue
        if src.startswith("**", i):
            out.append(Tok("op", "^", i, i + 2))
            i += 2
            continue
        if c in _OPS:
            out.append(Tok("op", c, i, i + 1))
            i += 1
            continue
        raise CalcError(f"Unknown character '{c}'", i, i + 1)
    return out


@dataclass
class Node:
    t: str                       # num, id, par, neg, bin, call, list
    pos: int = 0
    end: int = 0
    v: str = ""                  # num: digits
    unit: str = ""               # num: unit
    name: str = ""               # id
    op: str = ""                 # bin
    a: "Node | None" = None
    b: "Node | None" = None
    fn: str = ""                 # call
    args: list = field(default_factory=list)
    items: list = field(default_factory=list)


def parse(src: str) -> Node:
    toks = tokenize(src)
    i = 0

    def is_op(v):
        return i < len(toks) and toks[i].t == "op" and toks[i].v == v

    def here():
        return (toks[i].pos, toks[i].end) if i < len(toks) else (len(src), len(src))

    def expect(v):
        nonlocal i
        if not is_op(v):
            found = f", found '{toks[i].v}'" if i < len(toks) else " at the end"
            raise CalcError(f"'{v}' expected{found}", *here())
        i += 1
        return toks[i - 1]

    def expr():
        nonlocal i
        a = term()
        while is_op("+") or is_op("-"):
            op = toks[i].v
            i += 1
            b = term()
            a = Node("bin", a.pos, b.end, op=op, a=a, b=b)
        return a

    def term():
        nonlocal i
        a = unary()
        while is_op("*") or is_op("/") or is_op("·") or is_op("×"):
            op = toks[i].v
            i += 1
            b = unary()
            a = Node("bin", a.pos, b.end, op=op, a=a, b=b)
        return a

    def unary():
        nonlocal i
        if is_op("-"):
            p0 = toks[i].pos
            i += 1
            a = unary()
            return Node("neg", p0, a.end, a=a)
        if is_op("+"):
            i += 1
            return unary()
        return pow_()

    def pow_():
        nonlocal i
        a = primary()
        if is_op("^"):
            i += 1
            b = unary()
            return Node("bin", a.pos, b.end, op="^", a=a, b=b)
        return a

    def primary():
        nonlocal i
        if i >= len(toks):
            raise CalcError("The expression ends too early", len(src), len(src))
        tk = toks[i]
        if tk.t == "num":
            i += 1
            return Node("num", tk.pos, tk.end, v=tk.v, unit=tk.unit)
        if is_op("("):
            i += 1
            e = expr()
            c = expect(")")
            return Node("par", tk.pos, c.end, a=e)
        if is_op("|"):
            i += 1
            e = expr()
            c = expect("|")
            return Node("call", tk.pos, c.end, fn="mag", args=[e])
        if is_op("["):
            i += 1
            items = [expr()]
            while is_op(","):
                i += 1
                items.append(expr())
            c = expect("]")
            return Node("list", tk.pos, c.end, items=items)
        if tk.t == "id":
            i += 1
            if is_op("("):
                i += 1
                args = []
                if not is_op(")"):
                    args.append(expr())
                    while is_op(","):
                        i += 1
                        args.append(expr())
                c = expect(")")
                return Node("call", tk.pos, c.end, fn=tk.v, args=args)
            if tk.v in ("curl", "div", "grad", "sqrt") and i < len(toks):
                a = pow_()
                return Node("call", tk.pos, a.end, fn=tk.v, args=[a])
            return Node("id", tk.pos, tk.end, name=tk.v)
        raise CalcError(f"Unexpected '{tk.v}'", tk.pos, tk.end)

    if not toks:
        raise CalcError("The expression is empty", 0, 0)
    e = expr()
    if i < len(toks):
        raise CalcError(f"Unexpected '{toks[i].v}'", toks[i].pos, toks[i].end)
    return e


def rename(src: str, old: str, new: str) -> str:
    """src with every use of the name old replaced by new (names only, not parts of other names)."""
    try:
        toks = tokenize(src)
    except CalcError:
        return src
    out, last = [], 0
    for t in toks:
        if t.t == "id" and src[t.pos:t.end] == old:
            out += [src[last:t.pos], new]
            last = t.end
    return "".join(out) + src[last:]


def names_in(src: str) -> set[str]:
    try:
        return {t.v for t in tokenize(src) if t.t == "id"}
    except CalcError:
        return set()


# ------------------------------------------------------------------------------------------------
# user functions:  name(a, b) = body
# ------------------------------------------------------------------------------------------------
@dataclass
class UserFunction:
    name: str
    params: list[str]
    body: str

    @property
    def text(self) -> str:
        return f"{self.name}({', '.join(self.params)}) = {self.body}"


_DEF = re.compile(r"^\s*([A-Za-z_]\w*)\s*\(\s*([^)]*)\)\s*=\s*(.+)$", re.S)


def parse_function(text: str) -> UserFunction:
    m = _DEF.match(text)
    if not m:
        raise CalcError("Write a function as name(a, b) = expression")
    name, params, body = m.group(1), [p.strip() for p in m.group(2).split(",") if p.strip()], m.group(3).strip()
    if name in SIGNATURES or name in SYMBOLS:
        raise CalcError(f"'{name}' is a built-in name")
    for p in params:
        if not re.fullmatch(r"[A-Za-z_]\w*", p):
            raise CalcError(f"'{p}' is not a valid parameter name")
    if len(set(params)) != len(params):
        raise CalcError("Parameter names must differ")
    parse(body)
    return UserFunction(name, params, body)


def substitute(body: Node, params: list[str], args: list[Node]) -> Node:
    def rec(n):
        if n is None:
            return None
        if n.t == "id" and n.name in params:
            return args[params.index(n.name)]
        return replace(n, a=rec(n.a), b=rec(n.b), args=[rec(x) for x in n.args], items=[rec(x) for x in n.items])
    return rec(body)


# ------------------------------------------------------------------------------------------------
# analysis
# ------------------------------------------------------------------------------------------------
@dataclass
class GeoInfo:
    """What a geometry name stands for: dims it can be used as (a solid is a volume and, as its closed
    boundary, a face), closed (lines: a loop; faces: a closed surface)."""
    dims: frozenset
    closed: bool = False
    doc: str = ""


@dataclass
class GeoItem:
    kind: str                    # "obj", "face", "cut", "point"
    name: str = ""               # obj / face: object name
    index: int = -1              # face index
    axis: str = ""               # cut: "xy", "yz", "xz"
    value: float = 0.0           # cut: position (m)
    xyz: tuple = ()              # point (m)
    flip: bool = False

    def label(self) -> str:
        if self.kind == "obj":
            s = self.name
        elif self.kind == "face":
            s = f"face({self.name}, {self.index})"
        elif self.kind == "cut":
            s = f"cut_{self.axis}({self.value * 1e3:g}mm)"
        else:
            s = "point(" + ", ".join(f"{v * 1e3:g}mm" for v in self.xyz) + ")"
        return f"flip({s})" if self.flip else s

    def key(self) -> tuple:
        return (self.kind, self.name, self.index, self.axis, round(self.value, 15), tuple(self.xyz), self.flip)


@dataclass
class Env:
    """What the analysis needs to know about the project. All callables may be left at their defaults."""
    named: object = None            # name -> source text of a named expression, or None
    functions: dict = field(default_factory=dict)  # name -> UserFunction
    geometry: object = None         # name -> GeoInfo or None
    cs_names: frozenset = frozenset({"Global"})
    variable: object = None         # name -> (SI value, unit) or None (design variables)
    model_unit: float = 1e-3        # metres per model unit: numbers without unit in point()/cut_*()
    stack: tuple = ()
    inner: bool = False

    def lookup_named(self, name):
        return self.named(name) if callable(self.named) else (self.named or {}).get(name)

    def lookup_geometry(self, name):
        return self.geometry(name) if callable(self.geometry) else (self.geometry or {}).get(name)

    def lookup_variable(self, name):
        return self.variable(name) if callable(self.variable) else (self.variable or {}).get(name)


@dataclass
class Info:
    vec: bool                       # vector (else scalar)
    field: bool                     # varies over space (else a value)
    unit: tuple
    cx: bool                        # complex
    tex: str
    p: int = 9                      # precedence of the outermost operator (for parentheses in tex)
    surf: bool = False              # needs a face normal
    deps: tuple = ()                # named expressions used
    conv: frozenset = frozenset()   # conventions (peak, rms, inst, fixedphase, tavg)
    normals: tuple = ()             # geometries whose normal / direction the result depends on
    num: float | None = None        # value of a constant
    inner: str = ""                 # tex without the outer parentheses
    geo: tuple = ()                 # GeoItems of a geometry reduction
    userfns: tuple = ()
    uses: frozenset = frozenset()   # the data it needs: "fields" (E, H, the mesh) and/or "net" (S, Y, Z)
    log: str = ""                   # "dB": a level in decibels (dimensionless, shown as dB)

    @property
    def kind(self) -> str:
        if not self.field:
            return "Vector value" if self.vec else "Value"
        return "Vector field" if self.vec else "Scalar field"


def _wrap(r: Info, p: int) -> Info:
    return replace(r, tex=r"\left(" + r.tex + r"\right)", p=9, inner=r.tex) if r.p < p else r


def _merge(a: Info, b: Info) -> dict:
    return dict(surf=a.surf or b.surf, deps=a.deps + b.deps, conv=a.conv | b.conv, normals=a.normals + b.normals,
                userfns=a.userfns + b.userfns, uses=a.uses | b.uses)


def unit_label(i: Info) -> str:
    """The unit of an expression as shown: 'W', 'V/m', 'dB' for a level in decibels, '' when dimensionless."""
    return i.log or UN.label(i.unit)


def _log(op: str, a: Info, b: Info) -> str:
    """dB of a sum or difference of levels, or of a level and a number (RL + 3, 2*RL); else no dB."""
    if op in ("+", "-"):
        return a.log if a.log == b.log or (a.log and b.num is not None) else b.log if a.num is not None else ""
    if op == "*":
        return a.log if b.num is not None and not b.log else b.log if a.num is not None and not a.log else ""
    if op == "/":
        return a.log if b.num is not None and not b.log else ""
    return ""


def _tex_name(n: str) -> str:
    return r"\mathrm{" + n.replace("_", r"\_") + "}"


def analyze(src: str | Node, env: Env | None = None) -> Info:
    env = env or Env()
    node = parse(src) if isinstance(src, str) else src
    return _an(node, env)


def _an(node: Node, env: Env) -> Info:
    try:
        return _an0(node, env)
    except CalcError as e:
        if e.pos is None and not env.inner:
            e.pos, e.end = node.pos, node.end
        raise


def _an0(node: Node, env: Env) -> Info:
    t = node.t
    if t == "num":
        fac, un = UN.NUMBER_UNITS.get(node.unit, (1.0, ONE))
        utex = r"\,\mathrm{" + ({"ohm": r"\Omega", "Ohm": r"\Omega", "um": r"\mu m"}.get(node.unit, node.unit)) + "}" \
            if node.unit else ""
        return Info(False, False, un, False, node.v + utex, num=float(node.v) * fac)
    if t == "id":
        return _an_id(node, env)
    if t == "par":
        r = _an(node.a, env)
        return replace(r, tex=r"\left(" + r.tex + r"\right)", p=9, inner=r.tex)
    if t == "list":
        raise CalcError("Lists [ ] are only allowed as geometry argument")
    if t == "neg":
        r = _wrap(_an(node.a, env), 2)
        return replace(r, tex="-" + r.tex, p=2, num=-r.num if r.num is not None else None)
    if t == "bin":
        a, b = _an(node.a, env), _an(node.b, env)
        return replace(_an_bin(node.op, a, b), log=_log(node.op, a, b))
    r = _an_call(node, env)
    # a level stays in dB through these, any other function of it (sqrt, exp, ...) is a plain number
    return r if not r.log or node.fn in ("dB", "dB10", "re", "im", "mag", "conj", "max", "min", "avg", "at") \
        else replace(r, log="")


def _an_id(node: Node, env: Env) -> Info:
    name = node.name
    s = SYMBOLS.get(name)
    if s is not None:
        conv = frozenset({"peak"}) if name in PEAK_FIELDS else frozenset({"tavg"}) if name == "S" else frozenset()
        return Info(s.vec, s.field, s.unit, s.cx, s.tex, surf=s.surf, conv=conv, num=s.value,
                    uses=frozenset({"fields"}) if s.field else frozenset())
    src = env.lookup_named(name)
    if src is not None:
        if name in env.stack:
            raise CalcError("Cycle: " + " -> ".join(env.stack + (name,)))
        try:
            r = analyze(src, replace(env, stack=env.stack + (name,), inner=True))
        except CalcError as e:
            raise CalcError(f"In {name}: {e}", node.pos, node.end) from None
        return replace(r, p=9, tex=_tex_name(name), deps=(name,) + r.deps, num=None, inner="")
    var = env.lookup_variable(name)
    if var is not None:
        val, un = var
        return Info(False, False, un, False, _tex_name(name), num=val)
    if env.lookup_geometry(name) is not None:
        raise CalcError(f"'{name}' is a geometry. Use it as the first argument, e.g. flux({name}, re(S))")
    if name in env.cs_names:
        raise CalcError(f"'{name}' is a coordinate system, e.g. x(E, {name})")
    if name in SIGNATURES or name in env.functions:
        raise CalcError(f"'{name}' is a function, write {name}(...)")
    raise CalcError(f"'{name}' is unknown")


def _an_bin(op: str, a: Info, b: Info) -> Info:
    m = _merge(a, b)
    vec_both = a.vec and b.vec
    fld = a.field or b.field
    if op in ("·", "×") and not vec_both:
        op = "*"                     # between a scalar and a vector: a product
    if op in ("+", "-"):
        if a.vec != b.vec:
            raise CalcError("Cannot add a vector and a scalar")
        if not UN.same(a.unit, b.unit):
            raise CalcError(f"Units do not match: {UN.describe(a.unit)} {op} {UN.describe(b.unit)}")
        rb = _wrap(b, 2) if op == "-" else b
        num = (a.num + b.num if op == "+" else a.num - b.num) if a.num is not None and b.num is not None else None
        return Info(a.vec, fld, a.unit, a.cx or b.cx, a.tex + op + rb.tex, p=1, num=num, **m)
    if op in ("·", "×"):
        return Info(op == "×", fld, UN.mul(a.unit, b.unit), a.cx or b.cx,
                    _wrap(a, 3).tex + (r"\cdot " if op == "·" else r"\times ") + _wrap(b, 3).tex, p=2, **m)
    if op == "*":
        if vec_both:
            raise CalcError("Two vectors: use a·b or dot(a, b), or a×b or cross(a, b)")
        bq = _wrap(b, 2)
        sep = r"\cdot " if re.match(r"^[0-9]", bq.tex) or a.num is not None and b.num is not None else r"\,"
        num = a.num * b.num if a.num is not None and b.num is not None else None
        return Info(a.vec or b.vec, fld, UN.mul(a.unit, b.unit), a.cx or b.cx, _wrap(a, 2).tex + sep + bq.tex, p=2,
                    num=num, **m)
    if op == "/":
        if b.vec:
            raise CalcError("Cannot divide by a vector")
        num = a.num / b.num if a.num is not None and b.num not in (None, 0) else None
        return Info(a.vec, fld, UN.mul(a.unit, b.unit, -1), a.cx or b.cx,
                    r"\frac{" + (a.inner or a.tex) + "}{" + (b.inner or b.tex) + "}", p=2, num=num, **m)
    if op == "^":
        if a.vec:
            raise CalcError("Vectors cannot be raised to a power, take |E| first")
        if b.num is None:
            raise CalcError("The exponent must be a number")
        if not UN.same(b.unit, ONE):
            raise CalcError("The exponent must be dimensionless")
        num = a.num ** b.num if a.num is not None and (a.num >= 0 or float(b.num).is_integer()) else None
        return Info(False, a.field, UN.power(a.unit, b.num), a.cx, _wrap(a, 4).tex + "^{" + (b.inner or b.tex) + "}",
                    p=3, num=num, **m)
    raise CalcError(f"Unknown operator '{op}'")


def _need(args, lo, hi, fn):
    if not lo <= len(args) <= hi:
        n = str(lo) if lo == hi else f"{lo} to {hi}"
        raise CalcError(f"{fn}() expects {n} argument{'s' if hi > 1 else ''}")


def _length(node: Node, env: Env, what: str) -> float:
    r = _an(node, replace(env, inner=True))
    if r.field or r.vec or r.num is None:
        raise CalcError(f"{what} must be a number, e.g. 2mm", node.pos, node.end)
    if UN.same(r.unit, ONE):
        return r.num * env.model_unit
    if not UN.same(r.unit, U(m=1)):
        raise CalcError(f"{what} must be a length, here {UN.describe(r.unit)}", node.pos, node.end)
    return r.num


def geometry_items(node: Node, env: Env, flip: bool = False) -> list[GeoItem]:
    """The geometries of a geometry argument: a name, face(), cut_*(), point(), flip(...) or a [list]."""
    if node.t == "list":
        return [g for it in node.items for g in geometry_items(it, env, flip)]
    if node.t == "par":
        return geometry_items(node.a, env, flip)
    if node.t == "id" and env.lookup_geometry(node.name) is not None:
        return [GeoItem("obj", node.name, flip=flip)]
    if node.t == "call" and node.fn in GEOM_CTOR and node.fn not in env.functions:
        a = node.args
        if node.fn == "flip":
            _need(a, 1, 1, "flip")
            return geometry_items(a[0], env, not flip)
        if node.fn == "face":
            _need(a, 2, 2, "face")
            if a[0].t != "id" or env.lookup_geometry(a[0].name) is None:
                raise CalcError("face(): the first argument must be an object", a[0].pos, a[0].end)
            k = _an(a[1], replace(env, inner=True))
            if k.num is None or k.field or not float(k.num).is_integer() or k.num < 0:
                raise CalcError("face(): the face index must be a whole number", a[1].pos, a[1].end)
            return [GeoItem("face", a[0].name, index=int(k.num), flip=flip)]
        if node.fn.startswith("cut_"):
            _need(a, 1, 1, node.fn)
            return [GeoItem("cut", axis=node.fn[4:], value=_length(a[0], env, "The position"), flip=flip)]
        if node.fn == "point":
            _need(a, 3, 3, "point")
            return [GeoItem("point", xyz=tuple(_length(x, env, "A coordinate") for x in a))]
    raise CalcError("Not a geometry", node.pos, node.end)


def geometry_dims(item: GeoItem, env: Env) -> tuple[set, bool]:
    """(dims, closed) of a geometry item."""
    if item.kind == "obj":
        g = env.lookup_geometry(item.name)
        return set(g.dims), g.closed
    if item.kind in ("face", "cut"):
        return {2}, False
    return {0}, False


def _arg_tex(n: Node, env: Env) -> str:
    try:
        items = geometry_items(n, env)
        return r",\,".join(("-" if g.flip else "") + _tex_name(g.label().replace("flip(", "", 1).rstrip(")")
                                                               if g.flip else g.label()) for g in items)
    except CalcError:
        pass
    if n.t == "id" and n.name in env.cs_names:
        return _tex_name(n.name)
    try:
        return _an(n, replace(env, inner=True)).tex
    except CalcError:
        return "?"


def _an_call(node: Node, env: Env) -> Info:
    fn, A = node.fn, node.args
    uf = env.functions.get(fn)
    if uf is not None and fn not in SIGNATURES:
        if len(A) != len(uf.params):
            n = len(uf.params)
            raise CalcError(f"{fn}() expects {n} argument{'' if n == 1 else 's'} ({', '.join(uf.params)})")
        if "fn:" + fn in env.stack:
            raise CalcError(f"Function {fn} calls itself")
        try:
            r = _an(substitute(parse(uf.body), uf.params, A), replace(env, stack=env.stack + ("fn:" + fn,), inner=True))
        except CalcError as e:
            raise CalcError(f"In {fn}(): {e}", node.pos, node.end) from None
        tex = r"\mathrm{" + fn.replace("_", r"\_") + r"}\left(" + r",\,".join(_arg_tex(a, env) for a in A) + r"\right)"
        return replace(r, p=9, tex=tex, userfns=r.userfns + (fn,), num=None, inner="")
    if fn in GEOM_CTOR:
        if fn == "flip":
            raise CalcError("flip() reverses a geometry inside an integral, e.g. flux(flip(Port1), re(S))")
        raise CalcError(f"{fn}() is a geometry, use it as the first argument, e.g. max({fn}(...), |E|)")
    if fn in GEOM_FN:
        return _an_geom(node, env)
    if fn in NET_FN:
        return _an_net(node, env)
    if fn not in SIGNATURES:
        raise CalcError(f"Unknown function '{fn}()'")

    def one():
        _need(A, 1, 1, fn)
        return _an(A[0], env)

    if fn in COMP_FN:
        _need(A, 1, 2, fn)
        a = _an(A[0], env)
        if not a.vec:
            raise CalcError(f"Components only exist for vectors, e.g. {fn}(E)")
        cs = "Global"
        if len(A) == 2:
            if A[1].t != "id" or A[1].name not in env.cs_names:
                raise CalcError("Unknown coordinate system. Available: " + ", ".join(sorted(env.cs_names)),
                                A[1].pos, A[1].end)
            cs = A[1].name
        sub = {"x": "x", "y": "y", "z": "z", "rho": r"\rho", "phi": r"\varphi", "r": "r", "theta": r"\vartheta"}[fn]
        tex = _wrap(a, 9).tex + "_{" + sub + "}" + ("" if cs == "Global" else "^{(" + _tex_name(cs) + ")}")
        return replace(a, vec=False, p=9, tex=tex, num=None, inner="")
    if fn == "mag":
        a = one()
        return replace(a, vec=False, cx=False, p=9, tex=r"\left|" + a.tex + r"\right|", inner="",
                       num=abs(a.num) if a.num is not None else None)
    if fn in ("re", "im"):
        a = one()
        return replace(a, cx=False, p=9, tex=r"\mathrm{" + ("Re" if fn == "re" else "Im") + r"}\left\{" + a.tex +
                       r"\right\}", inner="")
    if fn == "rms":
        a = one()
        conv = (a.conv - {"peak"}) | ({"rms"} if "peak" in a.conv else set())
        return replace(a, p=9, conv=frozenset(conv), tex=r"\mathrm{rms}\left(" + a.tex + r"\right)", inner="",
                       num=None)
    if fn == "inst":
        a = one()
        conv = (a.conv - {"peak"}) | ({"inst"} if "peak" in a.conv else set())
        return replace(a, cx=False, p=9, conv=frozenset(conv), inner="", num=None,
                       tex=r"\mathrm{Re}\left\{" + a.tex + r"\,e^{j\varphi}\right\}")
    if fn == "conj":
        a = one()
        return replace(a, p=4, tex=_wrap(a, 9).tex + "^{*}", inner="")
    if fn == "phase":
        a = one()
        if a.vec:
            raise CalcError("Take the phase of a component or a scalar, e.g. phase(y(E))")
        return replace(a, unit=ONE, cx=False, p=9, tex=r"\angle " + _wrap(a, 9).tex, inner="", num=None)
    if fn == "atphase":
        _need(A, 2, 2, fn)
        a, ph = _an(A[0], env), _an(A[1], env)
        if ph.num is None or ph.field:
            raise CalcError("Give the phase as a number, e.g. 30deg")
        conv = (a.conv - {"peak"}) | ({"fixedphase"} if "peak" in a.conv else set())
        return replace(a, cx=False, p=9, conv=frozenset(conv), inner="", num=None,
                       tex=r"\mathrm{Re}\left\{" + a.tex + r"\,e^{j" + ph.tex + r"}\right\}")
    if fn in ("dot", "cross"):
        _need(A, 2, 2, fn)
        a, b = _an(A[0], env), _an(A[1], env)
        if not (a.vec and b.vec):
            raise CalcError(f"{fn}() needs two vectors")
        return _an_bin("·" if fn == "dot" else "×", a, b)
    if fn in ("curl", "div", "grad"):
        a = one()
        if not a.field:
            raise CalcError(f"{fn}() needs a field, not a constant value")
        if fn == "grad" and a.vec:
            raise CalcError("grad() needs a scalar field")
        if fn != "grad" and not a.vec:
            raise CalcError(f"{fn}() needs a vector field")
        tex = {"curl": r"\nabla\times ", "div": r"\nabla\cdot ", "grad": r"\nabla "}[fn] + _wrap(a, 9).tex
        return replace(a, vec=fn != "div", unit=UN.mul(a.unit, U(m=-1)), p=3, tex=tex, inner="", num=None)
    if fn == "unit":
        a = one()
        if not a.vec:
            raise CalcError("unit() needs a vector")
        return replace(a, unit=ONE, p=9, tex=r"\hat{" + a.tex + "}", inner="")
    if fn in ("normal", "tangent"):
        a = one()
        if not a.vec:
            raise CalcError(f"{fn}() needs a vector field")
        tex = _wrap(a, 9).tex + (r"\cdot\mathbf{n}" if fn == "normal" else "_{t}")
        return replace(a, vec=fn == "tangent", field=True, surf=True, p=9, tex=tex, inner="")
    if fn == "sqrt":
        a = one()
        if a.vec:
            raise CalcError("Cannot take the square root of a vector")
        num = a.num ** 0.5 if a.num is not None and a.num >= 0 else None
        return replace(a, unit=UN.power(a.unit, 0.5), p=9, tex=r"\sqrt{" + a.tex + "}", num=num, inner="")
    if fn in ("exp", "ln", "log10", "sin", "cos", "tan"):
        a = one()
        if a.vec:
            raise CalcError(f"{fn}() needs a scalar")
        if not UN.same(a.unit, ONE):
            raise CalcError(f"{fn}() needs a dimensionless argument, here {UN.describe(a.unit)}")
        tex = {"exp": "e^{" + a.tex + "}", "log10": r"\log_{10}\left(" + a.tex + r"\right)"}.get(
            fn, "\\" + fn + r"\left(" + a.tex + r"\right)")
        import math
        f = {"exp": math.exp, "ln": math.log, "log10": math.log10, "sin": math.sin, "cos": math.cos,
             "tan": math.tan}[fn]
        try:
            num = f(a.num) if a.num is not None and not a.cx else None
        except ValueError:
            num = None
        return replace(a, p=9, tex=tex, num=num, inner="")
    if fn == "atan2":
        _need(A, 2, 2, fn)
        a, b = _an(A[0], env), _an(A[1], env)
        if a.vec or b.vec or a.cx or b.cx:
            raise CalcError("atan2() needs two real scalars")
        if not UN.same(a.unit, b.unit):
            raise CalcError(f"Units do not match: {UN.describe(a.unit)} and {UN.describe(b.unit)}")
        return Info(False, a.field or b.field, ONE, False, r"\mathrm{atan2}\left(" + a.tex + ",\\," + b.tex + r"\right)",
                    **_merge(a, b))
    if fn in ("dB", "dB10"):
        a = one()
        if a.vec:
            raise CalcError(f"{fn}() needs a scalar")
        return replace(a, unit=ONE, cx=False, p=9, num=None, inner="", log="dB",
                       tex=("20" if fn == "dB" else "10") + r"\log_{10}\left|" + a.tex + r"\right|")
    if fn == "rs":
        a = one()
        if a.vec or a.field:
            raise CalcError("rs() needs a conductivity value, e.g. rs(5.8e7S/m)")
        if not UN.same(a.unit, U(A=1, V=-1, m=-1)) and not UN.same(a.unit, ONE):
            raise CalcError(f"rs() needs a conductivity in S/m, here {UN.describe(a.unit)}")
        return replace(a, unit=U(V=1, A=-1), p=9, num=None, inner="", tex=r"R_s\left(" + a.tex + r"\right)")
    raise CalcError(f"Unknown function '{fn}()'")


def port_numbers(node: Node, env: Env) -> list[int]:
    """The port numbers of S(i, j), Y(i, j), Z(i, j) or VSWR(i)."""
    n = NET_FN[node.fn][0]
    _need(node.args, n, n, node.fn)
    out = []
    for a in node.args:
        r = _an(a, env)
        if r.num is None or r.field or not UN.same(r.unit, ONE) or r.num < 1 or not float(r.num).is_integer():
            raise CalcError("Give the port as a number, e.g. " + ("S(2, 1)" if n == 2 else "VSWR(1)"), a.pos, a.end)
        out.append(int(r.num))
    return out


def _an_net(node: Node, env: Env) -> Info:
    ports = port_numbers(node, env)
    _, unit, cx = NET_FN[node.fn]
    sub = "".join(map(str, ports)) if all(p < 10 for p in ports) else ",".join(map(str, ports))
    name = r"\mathrm{VSWR}" if node.fn == "VSWR" else node.fn
    return Info(False, False, unit, cx, name + "_{" + sub + "}", uses=frozenset({"net"}))


def _an_geom(node: Node, env: Env) -> Info:
    fn, A = node.fn, node.args
    _need(A, 2, 2, fn)
    try:
        items = geometry_items(A[0], env)
    except CalcError:
        raise CalcError(f"{fn}(): the first argument must be a geometry ({'/'.join(DIM_NAME[d] for d in sorted(GEOM_FN[fn]))})",
                        A[0].pos, A[0].end) from None
    for it in items:
        dims, closed = geometry_dims(it, env)
        if not dims & GEOM_FN[fn]:
            need = "/".join(DIM_NAME[d] for d in sorted(GEOM_FN[fn]))
            raise CalcError(f"{fn}() needs a {need}, '{it.label()}' is a {DIM_NAME[max(dims)]}", A[0].pos, A[0].end)
        if fn == "circ" and not closed:
            raise CalcError(f"circ() needs a closed line, '{it.label()}' is open", A[0].pos, A[0].end)
    f = _an(A[1], env)
    gtex = r",\,".join(("-" if g.flip else "") + _tex_name(replace(g, flip=False).label()) for g in items)
    dir_dep = fn == "flux" or (fn in ("int_line", "circ") and f.vec)
    base = dict(deps=f.deps, conv=f.conv, userfns=f.userfns, geo=tuple(items), cx=f.cx, p=9, field=False,
                uses=f.uses | {"fields"},
                normals=f.normals + (tuple(items) if dir_dep or f.surf else ()))
    if fn == "int_vol":
        return Info(f.vec, unit=UN.mul(f.unit, U(m=3)), tex=r"\int\int\int_{" + gtex + "} " + _wrap(f, 2).tex + r"\,dV",
                    **base)
    if fn == "int_surf":
        return Info(f.vec, unit=UN.mul(f.unit, U(m=2)), tex=r"\int\int_{" + gtex + "} " + _wrap(f, 2).tex + r"\,dA",
                    **base)
    if fn == "flux":
        if not f.vec:
            raise CalcError("flux() needs a vector field")
        return Info(False, unit=UN.mul(f.unit, U(m=2)),
                    tex=r"\int\int_{" + gtex + "} " + _wrap(f, 3).tex + r"\cdot\mathbf{n}\,dA", **base)
    if fn == "int_line":
        body = _wrap(f, 3).tex + r"\cdot d\mathbf{l}" if f.vec else _wrap(f, 2).tex + r"\,dl"
        return Info(False, unit=UN.mul(f.unit, U(m=1)), tex=r"\int_{" + gtex + "} " + body, **base)
    if fn == "circ":
        if not f.vec:
            raise CalcError("circ() needs a vector field")
        return Info(False, unit=UN.mul(f.unit, U(m=1)), tex=r"\oint_{" + gtex + "} " + _wrap(f, 3).tex +
                    r"\cdot d\mathbf{l}", **base)
    if fn in ("max", "min"):
        if f.vec or f.cx:
            raise CalcError(f"{fn}() needs a real scalar field, e.g. |E| or re(...)")
        return Info(False, unit=f.unit, tex="\\" + fn + "_{" + gtex + "} " + _wrap(f, 2).tex, **dict(base, cx=False))
    if fn == "avg":
        if f.vec:
            raise CalcError("avg() needs a scalar field")
        return Info(False, unit=f.unit, tex=r"\langle " + f.tex + r"\rangle_{" + gtex + "}", **base)
    # at
    return Info(f.vec, unit=f.unit, tex=_wrap(f, 9).tex + r"\,|_{" + gtex + "}", **base)


def conventions_text(info: Info) -> list[str]:
    """Short notes on what a result means: 'peak phasor', 'time average', 'normal of Port1: +z'."""
    out = [CONVENTIONS[c] for c in ("peak", "rms", "inst", "fixedphase", "tavg") if c in info.conv]
    return out
