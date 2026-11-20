"""Evaluation of Fields Calculator expressions on a solution.

A FieldSource gives the stored field of one solution, frequency and excitation (E and H at the nodes of
every tetrahedron) with the materials of the elements and the geometry of the model. Field expressions are
evaluated on Samples (points in elements); integrals, maxima and point values reduce them to a value.
Derivatives (curl, div, grad) differentiate the element's shape functions: exact for E and H, and for
derived expressions their values at the element nodes interpolated the same way."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import lang as L
from . import units as UN
from .geometry import Mesh, Samples, dshape, shape
from .lang import CalcError, EPS0, MU0, C0, Env, GeoItem, Node


class FieldSource:
    """Field data of one solution at one frequency and excitation (the context of an evaluation).

    Subclasses fill nodes, tets, npt, freq and implement nodal(), material() and the geometry hooks."""

    nodes: np.ndarray            # (N, 3) metres
    tets: np.ndarray             # (M, 4)
    npt: int = 10                # field nodes per element: 4 (linear) or 10 (quadratic)
    freq: float = 0.0

    def __init__(self):
        self._mesh = None

    curved = None                # (row in disp or -1 per element, mid-edge displacements (nc, 6, 3)) or None

    @property
    def mesh(self) -> Mesh:
        if self._mesh is None:
            self._mesh = Mesh(self.nodes, self.tets, self.curved)
        return self._mesh

    def nodal(self, which: str) -> np.ndarray:
        """'E' or 'H' at the field nodes of every element: (M, npt, 3) complex peak phasors."""
        raise NotImplementedError

    def material(self, prop: str) -> np.ndarray:
        """'eps_r', 'mu_r', 'sigma', 'tand' or 'tandm' (magnetic loss tangent) of every element (M,)."""
        raise NotImplementedError

    def network(self, kind: str, ports: list[int]) -> complex:
        """S(i,j), Y(i,j), Z(i,j) or VSWR(i) of the solution at this frequency."""
        raise CalcError(f"{kind}() needs the network data of a solution (S parameters)")

    def cs_frame(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        """Origin (m) and axes (columns) of a coordinate system in global coordinates."""
        if name in ("", "Global"):
            return np.zeros(3), np.eye(3)
        raise CalcError(f"Unknown coordinate system '{name}'")

    # geometry: objects are resolved by the application (the CAD shapes and the solver mesh)
    def object_samples(self, name: str, dim: int) -> Samples:
        raise CalcError(f"'{name}' is not part of this solution")

    def face_samples(self, name: str, index: int) -> Samples:
        raise CalcError(f"face({name}, {index}) is not part of this solution")

    def samples(self, item: GeoItem, dim: int) -> Samples:
        """Samples of a geometry used as dim (a solid as volume 3 or as its boundary 2)."""
        if item.kind == "obj":
            s = self.object_samples(item.name, dim)
        elif item.kind == "face":
            s = self.face_samples(item.name, item.index)
        elif item.kind == "cut":
            n = {"xy": (0, 0, 1.0), "yz": (1.0, 0, 0), "xz": (0, 1.0, 0)}[item.axis]
            try:
                s = self.mesh.plane_samples(n, item.value, item.label())
            except ValueError as e:
                raise CalcError(str(e)) from None
        else:
            s = self.mesh.point_samples(item.xyz, item.label())
        return s.flipped() if item.flip else s


@dataclass
class Result:
    """The value of an expression: a complex number or vector (3,) with its unit."""
    value: complex | np.ndarray
    unit: tuple
    info: L.Info
    scale: float = 0.0               # sum of the magnitudes of all contributions (for "approximately 0")
    notes: list[str] = field(default_factory=list)

    @property
    def vec(self) -> bool:
        return np.ndim(self.value) == 1

    def is_zero(self) -> bool:
        a = float(np.max(np.abs(self.value)))
        return self.scale > 0 and a < 1e-9 * self.scale

    def text(self, digits: int = 4) -> str:
        if self.vec:
            return "(" + ", ".join(_ctext(v, self.unit, digits, False) for v in self.value) + ")" + \
                (f" {UN.label(self.unit)}" if UN.label(self.unit) else "")
        if self.is_zero():
            return "≈ 0" + (f" {UN.label(self.unit)}" if UN.label(self.unit) else "")
        if self.info.log:
            return _ctext(complex(self.value), UN.ONE, digits, False) + " " + self.info.log
        return _ctext(complex(self.value), self.unit, digits, True)


def _ctext(v: complex, unit, digits: int, with_unit: bool) -> str:
    u = unit if with_unit else UN.ONE
    if abs(v.imag) <= 1e-12 * abs(v.real) or v.imag == 0:
        return UN.fmt(v.real, u, digits)
    if abs(v.real) <= 1e-12 * abs(v.imag):
        return UN.fmt(v.imag, u, digits).replace(" ", " j", 1) if " " in UN.fmt(v.imag, u, digits) else \
            UN.fmt(v.imag, u, digits) + "j"
    mag, ang = abs(v), np.degrees(np.angle(v))
    return f"{UN.fmt(mag, u, digits)} ∠ {ang:.4g}°"


def _mulsv(s, v):
    s = np.asarray(s)
    return (s[..., None] if s.ndim else s) * v


def _dot(a, b):
    return (a * b).sum(axis=-1)


def _cross(a, b):
    a, b = np.broadcast_arrays(a, b)
    return np.cross(a, b)


class Evaluator:
    """Evaluates expressions for one FieldSource (solution, frequency, excitation) at one phase."""

    CHUNK = 200_000

    def __init__(self, env: Env, source: FieldSource, phase_deg: float = 0.0):
        self.env = env
        self.src = source
        self.phase = np.radians(phase_deg)
        self._nodal: dict = {}
        self._values: dict = {}       # value expressions (named or reductions) computed once
        self._trees: dict = {}        # parsed named expressions and expanded functions (alive for the cache keys)
        self.notes: list[str] = []

    # ---- public ------------------------------------------------------------------------------
    def value(self, src: str | Node) -> Result:
        """A value expression (integral, maximum, point value or constant)."""
        node = self._top(src)
        info = L.analyze(node, self.env)
        if info.field:
            raise CalcError(f"This is a {info.kind.lower()}: reduce it to a value, e.g. max(<geometry>, |...|), "
                            "or plot it")
        v = self._eval(node, None)
        return Result(np.asarray(v).astype(complex) if np.ndim(v) else complex(v), info.unit, info,
                      scale=self._scale_of(node) or 0.0, notes=list(dict.fromkeys(self.notes)))

    def _top(self, src: str | Node) -> Node:
        key = ("src", src if isinstance(src, str) else id(src))
        if key not in self._trees:
            self._trees[key] = L.parse(src) if isinstance(src, str) else src
        return self._trees[key]

    def _scale_of(self, node: Node) -> float | None:
        """Sum of the magnitudes of the contributions to a sum of integrals (None: not such a sum)."""
        if node.t in ("par", "neg"):
            return self._scale_of(node.a)
        if node.t == "bin" and node.op in ("+", "-"):
            a, b = self._scale_of(node.a), self._scale_of(node.b)
            return None if a is None or b is None else a + b
        if node.t == "call" and (id(node), "scale") in self._values:
            return self._values[id(node), "scale"]
        if node.t == "id":
            src = self.env.lookup_named(node.name)
            if src is not None and (node.name, "scale") in self._values:
                return self._values[node.name, "scale"]
        return None

    def at(self, src: str | Node, S: Samples) -> np.ndarray:
        """A field expression at sample points: (n,) or (n, 3) complex."""
        node = self._top(src)
        info = L.analyze(node, self.env)
        out = np.zeros((len(S), 3) if info.vec else (len(S),), complex)
        i = 0
        for C in S.chunks(self.CHUNK):
            out[i:i + len(C)] = np.asarray(self._eval(node, C), complex)
            i += len(C)
        return out

    # ---- fields at samples -------------------------------------------------------------------
    def _nodal_of(self, which: str) -> np.ndarray:
        if which not in self._nodal:
            self._nodal[which] = np.asarray(self.src.nodal(which), np.complex128)
        return self._nodal[which]

    def _interp(self, vals: np.ndarray, tets: np.ndarray, lam: np.ndarray) -> np.ndarray:
        """Per-element nodal values (M, npt, ...) at (tets, lam); 0 outside the solution (tets -1)."""
        npt = vals.shape[1]
        out = np.zeros((len(tets),) + vals.shape[2:], vals.dtype)
        ok = tets >= 0
        if ok.any():
            N = shape(lam[ok], npt)
            out[ok] = np.einsum("nk,nk...->n...", N, vals[tets[ok]])
        return out

    def _at_sides(self, vals: np.ndarray, S: Samples) -> np.ndarray:
        v = self._interp(vals, S.tets, S.lam)
        if S.tets2 is not None:
            two = S.tets2 >= 0
            if two.any():
                v[two] = 0.5 * (v[two] + self._interp(vals, S.tets2[two], S.lam2[two]))
        return v

    def _per_element(self, arr: np.ndarray, S: Samples) -> np.ndarray:
        v = np.where(S.tets >= 0, arr[np.maximum(S.tets, 0)], 0.0)
        if S.tets2 is not None:
            two = S.tets2 >= 0
            v = v.astype(float)
            v[two] = 0.5 * (v[two] + arr[S.tets2[two]])
        return v

    def _need_points(self, S, name):
        if S is None:
            raise CalcError(f"'{name}' is a field: reduce it to a value first, e.g. max(<geometry>, |{name}|)")

    def _symbol(self, name: str, S: Samples | None):
        s = L.SYMBOLS[name]
        f = self.src.freq
        w = 2 * np.pi * f
        if not s.field:
            return {"j": 1j, "f": f, "omega": w, "k0": w / C0}.get(name, s.value)
        self._need_points(S, name)
        if name in ("x", "y", "z"):
            return S.P[:, "xyz".index(name)].astype(complex)
        if name in ("eps_r", "mu_r", "sigma", "tand", "tandm"):
            return self._per_element(np.asarray(self.src.material(name), float), S).astype(complex)
        if name == "E":
            return self._at_sides(self._nodal_of("E"), S)
        if name == "H":
            return self._at_sides(self._nodal_of("H"), S)
        if name == "D":
            return _mulsv(EPS0 * self._symbol("eps_r", S), self._symbol("E", S))
        if name == "B":
            return _mulsv(MU0 * self._symbol("mu_r", S), self._symbol("H", S))
        if name == "J":
            sig = self._symbol("sigma", S) + w * EPS0 * self._symbol("eps_r", S) * self._symbol("tand", S)
            return _mulsv(sig, self._symbol("E", S))
        if name == "S":
            return 0.5 * _cross(self._symbol("E", S), np.conj(self._symbol("H", S)))
        if name in ("n", "Js"):
            if S.N is None:
                raise CalcError(f"{name} only exists on faces (a face integral, or a field plot on faces)")
            if name == "n":
                return S.N.astype(complex)
            H = self._interp(self._nodal_of("H"), S.tets, S.lam)
            if S.tets2 is not None:
                two = S.tets2 >= 0
                if two.any():
                    H[two] = H[two] - self._interp(self._nodal_of("H"), S.tets2[two], S.lam2[two])
            return _cross(S.N, H)
        raise CalcError(f"'{name}' cannot be evaluated")

    # ---- the tree ----------------------------------------------------------------------------------
    def _eval(self, node: Node, S: Samples | None):
        t = node.t
        if t == "num":
            fac, _ = UN.NUMBER_UNITS.get(node.unit, (1.0, UN.ONE))
            return complex(float(node.v) * fac)
        if t == "par":
            return self._eval(node.a, S)
        if t == "neg":
            return -self._eval(node.a, S)
        if t == "id":
            return self._id(node.name, S)
        if t == "bin":
            return self._bin(node, S)
        if t == "call":
            return self._call(node, S)
        raise CalcError("Lists [ ] are only allowed as geometry argument", node.pos, node.end)

    def _id(self, name: str, S):
        if name in L.SYMBOLS:
            return self._symbol(name, S)
        src = self.env.lookup_named(name)
        if src is not None:
            info = L.analyze(src, self.env)
            n = self._tree(name, src)
            if not info.field:
                if name not in self._values:
                    self._values[name] = self._eval(n, None)
                    sc = self._scale_of(n)
                    if sc is not None:
                        self._values[name, "scale"] = sc
                return self._values[name]
            return self._eval(n, S)
        var = self.env.lookup_variable(name)
        if var is not None:
            return complex(var[0])
        raise CalcError(f"'{name}' is unknown")

    def _tree(self, name: str, src: str) -> Node:
        if name not in self._trees:
            self._trees[name] = L.parse(src)
        return self._trees[name]

    def _bin(self, node: Node, S):
        a, b = self._eval(node.a, S), self._eval(node.b, S)
        va, vb = self._is_vec(node.a), self._is_vec(node.b)
        op = node.op
        if op in ("·", "×") and not (va and vb):
            op = "*"
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "·":
            return _dot(a, b)
        if op == "×":
            return _cross(a, b)
        if op == "*":
            if va:
                return _mulsv(b, a)
            if vb:
                return _mulsv(a, b)
            return a * b
        if op == "/":
            if va:
                return a / (np.asarray(b)[..., None] if np.ndim(b) else b)
            with np.errstate(divide="ignore", invalid="ignore"):
                return a / b
        if op == "^":
            p = float(np.real(b))
            if p.is_integer():
                return a ** int(p)
            aa = np.asarray(a)
            if not np.iscomplexobj(aa) or np.all(np.imag(aa) == 0):
                r = np.real(aa)
                if np.all(r >= 0):
                    return (r ** p).astype(complex)
            return aa.astype(complex) ** p
        raise CalcError(f"Unknown operator '{op}'")

    def _is_vec(self, node: Node) -> bool:
        try:
            return L.analyze(node, self.env).vec
        except CalcError:
            return False

    def _call(self, node: Node, S):
        fn, A = node.fn, node.args
        uf = self.env.functions.get(fn)
        if uf is not None and fn not in L.SIGNATURES:
            key = (id(node), "fn")
            if key not in self._trees:      # kept: reductions inside are cached by node identity
                self._trees[key] = L.substitute(L.parse(uf.body), uf.params, A)
            return self._eval(self._trees[key], S)
        if fn in L.NET_FN:
            return complex(self.src.network(fn, L.port_numbers(node, self.env)))
        if fn in L.GEOM_FN:
            key = (id(node), "geo")
            if key not in self._values:
                self._values[key] = self._reduce(node)
            return self._values[key]

        def one():
            return self._eval(A[0], S)

        if fn in L.COMP_FN:
            return self._component(fn, A, S)
        if fn == "mag":
            a = one()
            return np.sqrt((np.abs(a) ** 2).sum(axis=-1)).astype(complex) if self._is_vec(A[0]) else \
                np.abs(a).astype(complex)
        if fn == "re":
            return np.real(one()).astype(complex)
        if fn == "im":
            return np.imag(one()).astype(complex)
        if fn == "conj":
            return np.conj(one())
        if fn == "rms":
            return one() / np.sqrt(2)
        if fn == "phase":
            return np.angle(one()).astype(complex)
        if fn in ("inst", "atphase"):
            ph = self.phase if fn == "inst" else float(np.real(self._eval(A[1], S)))
            return np.real(one() * np.exp(1j * ph)).astype(complex)
        if fn == "dot":
            return _dot(self._eval(A[0], S), self._eval(A[1], S))
        if fn == "cross":
            return _cross(self._eval(A[0], S), self._eval(A[1], S))
        if fn in ("curl", "div", "grad"):
            return self._derivative(fn, A[0], S)
        if fn == "unit":
            a = one()
            n = np.sqrt((np.abs(a) ** 2).sum(axis=-1))
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.nan_to_num(a / (n[..., None] if np.ndim(n) else n))
        if fn in ("normal", "tangent"):
            self._need_points(S, fn)
            if S.N is None:
                raise CalcError(f"{fn}() only exists on faces")
            a = one()
            dn = _dot(a, S.N)
            return dn if fn == "normal" else a - _mulsv(dn, S.N)
        if fn == "sqrt":
            a = np.asarray(one())
            if np.all(np.imag(a) == 0) and np.all(np.real(a) >= 0):
                return np.sqrt(np.real(a)).astype(complex)
            return np.sqrt(a.astype(complex))
        if fn in ("exp", "ln", "log10", "sin", "cos", "tan"):
            a = np.asarray(one(), complex)
            if fn in ("ln", "log10") and np.all(np.imag(a) == 0) and np.all(np.real(a) > 0):
                r = np.real(a)
                return (np.log(r) if fn == "ln" else np.log10(r)).astype(complex)
            return {"exp": np.exp, "ln": np.log, "log10": np.log10, "sin": np.sin, "cos": np.cos,
                    "tan": np.tan}[fn](a)
        if fn == "atan2":
            return np.arctan2(np.real(self._eval(A[0], S)), np.real(self._eval(A[1], S))).astype(complex)
        if fn in ("dB", "dB10"):
            a = np.abs(one())
            return ((20 if fn == "dB" else 10) * np.log10(np.maximum(a, 1e-300))).astype(complex)
        if fn == "rs":
            sig = float(np.real(one()))
            if sig <= 0:
                raise CalcError("rs() needs a positive conductivity")
            return complex(np.sqrt(2 * np.pi * self.src.freq * MU0 / (2 * sig)))
        raise CalcError(f"Unknown function '{fn}()'")

    def _component(self, fn: str, A: list, S):
        a = self._eval(A[0], S)
        cs = A[1].name if len(A) == 2 else "Global"
        O, R = self.src.cs_frame(cs)
        if fn in ("x", "y", "z"):
            return _dot(a, R[:, "xyz".index(fn)])
        if S is None:
            raise CalcError(f"{fn}() of a value needs a position; use x(), y() or z(), or take the component "
                            "inside the reduction, e.g. at(point(...), rho(E))")
        loc = (S.P - O) @ R
        ph = np.arctan2(loc[:, 1], loc[:, 0])
        th = np.arctan2(np.hypot(loc[:, 0], loc[:, 1]), loc[:, 2])
        c, s = np.cos(ph), np.sin(ph)
        z0 = np.zeros_like(ph)
        e = {"rho": np.c_[c, s, z0], "phi": np.c_[-s, c, z0],
             "r": np.c_[np.sin(th) * c, np.sin(th) * s, np.cos(th)],
             "theta": np.c_[np.cos(th) * c, np.cos(th) * s, -np.sin(th)]}[fn]
        return _dot(a, e @ R.T)

    def _derivative(self, fn: str, inner: Node, S: Samples | None):
        self._need_points(S, fn)
        mesh, npt = self.src.mesh, self.src.npt
        vec = self._is_vec(inner)

        def side(tets, lam):
            out = np.zeros((len(tets), 3) if fn != "div" else (len(tets),), complex)
            if fn == "curl":
                out = np.zeros((len(tets), 3), complex)
            ok = tets >= 0
            if not ok.any():
                return out
            t, lm = tets[ok], lam[ok]
            U, inv = np.unique(t, return_inverse=True)
            NS = mesh.node_samples(U, npt)
            try:
                F = np.asarray(self._eval(inner, NS), complex)
            except CalcError as e:
                raise CalcError(f"{fn}(): {e}") from None
            F = np.broadcast_to(F, (len(NS), 3) if vec else (len(NS),)).reshape((len(U), npt) + ((3,) if vec else ()))
            gN = np.einsum("nki,nid->nkd", dshape(lm, npt), mesh.grad_lambda(t, lm))   # (n, npt, 3)
            Fk = F[inv.ravel()]
            if fn == "grad":
                out[ok] = np.einsum("nk,nkd->nd", Fk, gN)
                return out
            Jm = np.einsum("nki,nkd->nid", Fk, gN)                              # dF_i / dx_d
            if fn == "div":
                out[ok] = Jm[:, 0, 0] + Jm[:, 1, 1] + Jm[:, 2, 2]
            else:
                out[ok] = np.stack([Jm[:, 2, 1] - Jm[:, 1, 2], Jm[:, 0, 2] - Jm[:, 2, 0],
                                    Jm[:, 1, 0] - Jm[:, 0, 1]], axis=1)
            return out

        v = side(S.tets, S.lam)
        if S.tets2 is not None:
            two = S.tets2 >= 0
            if two.any():
                v[two] = 0.5 * (v[two] + side(S.tets2[two], S.lam2[two]))
        return v

    # ---- geometry reductions ---------------------------------------------------------------------
    def _reduce(self, node: Node):
        fn, A = node.fn, node.args
        items = L.geometry_items(A[0], self.env)
        finfo = L.analyze(A[1], self.env)
        acc = 0j if not finfo.vec or fn in ("flux", "int_line", "circ") else np.zeros(3, complex)
        scale, meas, n_out = 0.0, 0.0, 0
        mx, mn = -np.inf, np.inf
        point_val = None
        for it in items:
            dims, _ = L.geometry_dims(it, self.env)
            want = {"int_vol": 3, "int_surf": 2, "flux": 2, "int_line": 1, "circ": 1, "at": 0}.get(fn, max(dims))
            if fn == "avg":
                want = max(dims)
            S_all = self.src.samples(it, want)
            n_out += int((S_all.tets < 0).sum())
            for S in S_all.chunks(self.CHUNK):
                r = np.asarray(self._eval(A[1], S), complex)
                if finfo.vec:
                    r = np.broadcast_to(r, (len(S), 3))
                else:
                    r = np.broadcast_to(r, (len(S),))
                if fn == "at":
                    point_val = r[0]
                    continue
                if fn in ("max", "min"):
                    v = np.real(r)[S.tets >= 0] if (S.tets >= 0).any() else np.real(r)
                    if len(v):
                        mx, mn = max(mx, float(np.nanmax(v))), min(mn, float(np.nanmin(v)))
                    continue
                if fn == "flux":
                    c = _dot(r, S.N) * S.W
                elif fn in ("int_line", "circ") and finfo.vec:
                    c = _dot(r, S.T) * S.W
                elif finfo.vec:
                    c = r * S.W[:, None]
                else:
                    c = r * S.W
                acc = acc + c.sum(axis=0)
                scale += float(np.abs(c).sum())
                meas += float(S.W.sum())
        if n_out and fn != "at":
            self.notes.append(f"{n_out} sample points lie outside the solution volume (inside a perfect "
                              "conductor or outside the region) and count as zero")
        if fn == "at":
            if point_val is None or n_out:
                raise CalcError("The point lies outside the solution volume")
            return point_val
        if fn == "max":
            return complex(mx)
        if fn == "min":
            return complex(mn)
        self._values[id(node), "scale"] = scale / meas if fn == "avg" and meas else scale
        if fn == "avg":
            return acc / meas if meas else 0j
        return acc


def evaluate(src: str, env: Env, source: FieldSource, phase_deg: float = 0.0) -> Result:
    return Evaluator(env, source, phase_deg).value(src)
