"""The Fields Calculator on Hertzon projects: named expressions of the design, geometry of the model and
the stored fields of a solution (post.calc does the computing, without Qt).

A geometry name is an object of the design: a solid is a volume (and, in face integrals, its closed boundary
with the normal pointing out), a sheet a face, a polyline a line. face(Obj, k) is one face as in face
selection. Objects of the solver mesh are integrated on its elements; non-model sheets and lines are
placed into the mesh (a flat sheet is cut from the elements and cropped to its outline)."""
from __future__ import annotations

import re

import numpy as np
import pyvista as pv

from ..model import coordsys
from ..model.expr import ALL_UNITS, ANGLE_UNITS, FREQ_UNITS, LENGTH_UNITS, ExprError
from ..post.calc import lang as L
from ..post.calc import units as UN
from ..post.calc.evaluate import Evaluator, FieldSource, Result
from ..post.calc.geometry import Samples, tri_rule
from . import fields as F

_VAR_UNITS = {**{u: UN.U(m=1) for u in LENGTH_UNITS}, **{u: UN.U(s=-1) for u in FREQ_UNITS},
              **{u: UN.ONE for u in ANGLE_UNITS}, "ohm": UN.U(V=1, A=-1), "Ohm": UN.U(V=1, A=-1),
              "kOhm": UN.U(V=1, A=-1), "MOhm": UN.U(V=1, A=-1), "W": UN.U(V=1, A=1), "mW": UN.U(V=1, A=1),
              "s": UN.U(s=1), "ns": UN.U(s=1), "ps": UN.U(s=1)}
_UNIT_RE = re.compile(r"(?<![A-Za-z_\d.])(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*(" +
                      "|".join(sorted((re.escape(u) for u in ALL_UNITS), key=len, reverse=True)) + r")(?![A-Za-z_\d])")


# ------------------------------------------------------------------------------------------------
# the language environment of a design
# ------------------------------------------------------------------------------------------------
def functions(design) -> dict[str, L.UserFunction]:
    out = {}
    for text in design.field_funcs:
        try:
            f = L.parse_function(text)
            out[f.name] = f
        except L.CalcError:
            pass
    return out


def _closed_line(t) -> bool:
    pts = [np.asarray(e, float) for e in (t.edges or []) if len(e) > 1]
    if not pts:
        return False
    a, b = pts[0][0], pts[-1][-1]
    span = float(np.ptp(np.vstack(pts), axis=0).max()) or 1.0
    return float(np.linalg.norm(a - b)) < 1e-6 * span


def geometry_names(doc) -> dict[str, L.GeoInfo]:
    """Objects that can be used as geometry, with the dimensions they stand for."""
    out = {}
    for o in doc.design.live_objects():
        t = doc.tess.get(o.name)
        dim = int(getattr(t, "dim", o.dim)) if t is not None else o.dim
        if dim == 3:
            out[o.name] = L.GeoInfo(frozenset({3, 2}), True, "solid: volume, or its closed surface in face integrals")
        elif dim == 2:
            out[o.name] = L.GeoInfo(frozenset({2}), False, "sheet")
        elif dim == 1:
            closed = t is not None and _closed_line(t)
            out[o.name] = L.GeoInfo(frozenset({1}), closed, "closed line" if closed else "line")
        else:
            out[o.name] = L.GeoInfo(frozenset({0}), False, "point")
    return out


def variable_unit(ev, name: str, _seen=None) -> tuple:
    """Unit of a design variable from the units written in it (or in the variables it uses)."""
    _seen = _seen or set()
    if name in _seen or name not in ev.variables:
        return UN.ONE
    _seen.add(name)
    text = str(ev.variables[name])
    m = _UNIT_RE.search(text)
    if m:
        return _VAR_UNITS.get(m.group(1), UN.ONE)
    for w in re.findall(r"[A-Za-z_]\w*", text):
        if w in ev.variables:
            u = variable_unit(ev, w, _seen)
            if not UN.same(u, UN.ONE):
                return u
    return UN.ONE


def make_env(doc) -> L.Env:
    des = doc.design
    exprs = {e.name: e.expr for e in des.field_exprs}
    ev = doc.evaluator()
    geo = geometry_names(doc)

    def variable(name):
        if name not in ev.variables or name in exprs or name in geo:
            return None
        try:
            return ev.value(name), variable_unit(ev, name)
        except ExprError:
            return None
    return L.Env(named=exprs, functions=functions(des), geometry=geo, cs_names=frozenset(des.cs_names()),
                 variable=variable, model_unit=LENGTH_UNITS.get(des.units, 1e-3))


# a component of a vector value in a report trace: mag(Spp), x(Spp), ...
VECTOR_PARTS = ("mag", "x", "y", "z")


def value_names(doc, fields: bool | None = None) -> list[str]:
    """Named expressions that are values (integrals, maxima, point values): report quantities; a vector
    value as its magnitude and components (mag(Spp), x(Spp), ...). fields True: only the values computed
    from the fields (Fields reports), False: only those without fields, e.g. of S parameters (the Equations
    of the solution data reports), None: all."""
    env, out = make_env(doc), []
    for e in doc.design.field_exprs:
        try:
            i = L.analyze(e.name, env)
        except L.CalcError:
            continue
        if i.field or not (fields is None or fields == ("fields" in i.uses)):
            continue
        out += [f"{c}({e.name})" for c in VECTOR_PARTS] if i.vec else [e.name]
    return out


def vector_parts(doc, expr: str) -> tuple[str, dict[str, str]]:
    """A report trace with components of vector values (mag(Spp), x(Spp)): each component replaced by a
    name of its own -> (trace, {name: expression of the calculator})."""
    vecs = [n for n, k in expression_kinds(doc).items() if k == "vector"]
    parts: dict[str, str] = {}
    if not vecs:
        return expr, parts

    def sub(m):
        key = f"_part{len(parts)}"
        parts[key] = m.group(0)
        return key
    alt = "|".join(map(re.escape, vecs))
    expr = re.sub(r"\b(?:" + "|".join(L.COMP_FN + ("mag",)) + r")\(\s*(?:" + alt + r")\s*\)", sub, expr)
    return expr, parts


def uses_fields(doc, names) -> bool:
    """Any of the named expressions needs the fields (else it is computed from the network data alone)."""
    env = make_env(doc)
    for n in names:
        try:
            if "fields" in L.analyze(n, env).uses:
                return True
        except L.CalcError:
            pass
    return False


def expression_kinds(doc) -> dict[str, str]:
    """name -> 'field', 'value', 'vector' (a vector value) or 'error' for the named expressions of the design."""
    env, out = make_env(doc), {}
    for e in doc.design.field_exprs:
        try:
            i = L.analyze(e.name, env)
        except L.CalcError:
            out[e.name] = "error"
            continue
        out[e.name] = "field" if i.field else "vector" if i.vec else "value"
    return out


def field_names_in(design, expr: str) -> list[str]:
    """The named expressions of the Fields Calculator a report trace uses."""
    names = {e.name for e in design.field_exprs}
    return [n for n in dict.fromkeys(re.findall(r"[A-Za-z_]\w*", expr)) if n in names]


def dependents(design, name: str) -> list[str]:
    """Named expressions and functions that use name."""
    out = [e.name for e in design.field_exprs if e.name != name and name in L.names_in(e.expr)]
    for text in design.field_funcs:
        try:
            f = L.parse_function(text)
        except L.CalcError:
            continue
        if f.name != name and name in L.names_in(f.body):
            out.append(f.name + "()")
    return out


def rename_everywhere(design, old: str, new: str) -> None:
    """Rename a named expression or function and every use of it in expressions, functions and report traces."""
    for e in design.field_exprs:
        if e.name == old:
            e.name = new
        e.expr = L.rename(e.expr, old, new)
    funcs = []
    for text in design.field_funcs:
        try:
            f = L.parse_function(text)
        except L.CalcError:
            funcs.append(text)
            continue
        f.name = new if f.name == old else f.name
        f.body = L.rename(f.body, old, new)
        funcs.append(f.text)
    design.field_funcs = funcs
    for r in design.reports:
        r.traces = [L.rename(e, old, new) + sep + sol for e, sep, sol in (t.partition(" @ ") for t in r.traces)]


# ------------------------------------------------------------------------------------------------
# the field of a solution
# ------------------------------------------------------------------------------------------------
def cad_mesh(t) -> pv.PolyData:
    """Display mesh of an object (one vertex per point and CAD face, cell data "face", point normals), as
    the 3D view draws it."""
    tris = np.asarray(t.tris)
    tf = np.asarray(t.tri_face)
    if not len(tris):
        return pv.PolyData()
    m = int(tf.max()) + 1 if len(tf) else 1
    key = tris.astype(np.int64) * m + tf[:, None]
    uk, inv = np.unique(key.ravel(), return_inverse=True)
    pts = np.asarray(t.points, float)[uk // m]
    mesh = pv.PolyData(pts, np.hstack([np.full((len(tris), 1), 3), inv.reshape(-1, 3)]).ravel())
    mesh.cell_data["face"] = tf
    try:
        nm = mesh.compute_normals(cell_normals=False, split_vertices=False, consistent_normals=True,
                                  auto_orient_normals=False, feature_angle=180)
        if nm.n_cells == mesh.n_cells:
            mesh = nm
    except Exception:  # noqa: BLE001
        pass
    return mesh


def _material_values(mat, ev, freq: float) -> dict:
    def num(text, default):
        try:
            return float(ev.value(text or default))
        except Exception:  # noqa: BLE001
            return float(default)
    er, tand = num(mat.permittivity, "1"), num(mat.loss_tangent, "0")
    if mat.anisotropic and mat.permittivity_tensor:
        try:
            er = float(np.mean([ev.value(x) for x in mat.permittivity_tensor.split(",")]))
        except Exception:  # noqa: BLE001
            pass
    try:
        from ..model.materials import debye, djordjevic_sarkar
        eps = None
        if mat.dispersion == "Djordjevic-Sarkar":
            eps = djordjevic_sarkar(er, tand, ev.freq(mat.measured_frequency))(freq)
        elif mat.dispersion == "Debye":
            eps = debye(num(mat.eps_static, "4.6"), num(mat.eps_infinity, "4.2"),
                        ev.freq(mat.relaxation_frequency))(freq)
        if eps is not None:
            er, tand = eps.real, -eps.imag / eps.real
    except Exception:  # noqa: BLE001
        pass
    return {"eps_r": er, "mu_r": num(mat.permeability, "1"), "sigma": num(mat.conductivity, "0"), "tand": tand}


class DocFieldSource(FieldSource):
    """The stored field of a solution ("Setup1 : LastAdaptive") at frequency index fi for an excitation:
    port, or the Edit Sources superposition as in the field plots."""

    def __init__(self, doc, solution: str, fi: int = 0, port: int = 1, store=None):
        super().__init__()
        self.doc = doc
        self.solution = solution
        if store is None:
            st, sol = F.store_for(doc, solution)
        else:
            st, sol = store, solution.partition(":")[2].strip()
        grid, fields = F.load_grid(st, sol)
        npt = int(grid.field_data["npt"][0]) if "npt" in grid.field_data else 0
        if not npt:
            raise L.CalcError("This solution has the old field format: analyze again")
        self.st, self.grid, self.fields = st, grid, fields
        self.m = F._mesh_of(grid)
        self.nodes = np.asarray(self.m["nodes"], float)
        self.tets = np.asarray(self.m["tets"], np.int64)
        self.npt = npt
        self.curved = F._curv(self.m)
        if f"freq_{fi}" not in fields:
            raise L.CalcError(f"The solution has no frequency index {fi}")
        self.freq = float(fields[f"freq_{fi}"][0])
        self.fi, self.port = fi, port
        self.names = [str(n) for n in self.m["obj_names"]] if "obj_names" in self.m else []
        self._mat = None
        self._cad: dict = {}

    def network(self, kind: str, ports: list[int]) -> complex:
        if not hasattr(self, "_net"):
            self._net = self.st.sparams(self.solution.partition(":")[2].strip())
        if self._net is None:
            raise L.CalcError(f"No S parameters for '{self.solution}'")
        k = int(np.argmin(np.abs(np.asarray(self._net["freq"], float) - self.freq)))
        return network_value(self._net, kind, ports, k)

    def nodal(self, which: str) -> np.ndarray:
        if f"{which}_{self.fi}_{self.port}" not in self.fields:
            raise L.CalcError(f"No {which} field for port {self.port} at this frequency")
        v = F.source_vector(self.doc, self.st, self.fields, self.grid, which, self.fi, self.port)
        return np.asarray(v, np.complex128).reshape(len(self.tets), self.npt, 3)

    def material(self, prop: str) -> np.ndarray:
        if self._mat is None:
            from ..model.materials import is_conductor
            ev = self.doc.evaluator()
            rows = []
            des = self.doc.design
            for name in self.names:
                vals = {"eps_r": 1.0, "mu_r": 1.0, "sigma": 0.0, "tand": 0.0}
                ob = des.obj(name) if name and des.has_obj(name) else None
                if ob is not None:
                    try:
                        mat = self.doc.project.material(ob.material)
                        if not (mat.pec or (is_conductor(mat) and not ob.solve_inside)):
                            vals = _material_values(mat, ev, self.freq)
                    except Exception:  # noqa: BLE001
                        pass
                rows.append(vals)
            rows.append({"eps_r": 1.0, "mu_r": 1.0, "sigma": 0.0, "tand": 0.0})     # tet_obj -1
            idx = np.asarray(self.m["tet_obj"], np.int64) if "tet_obj" in self.m else np.full(len(self.tets), -1)
            self._mat = {k: np.array([r[k] for r in rows])[idx] for k in rows[0]}
        return self._mat[prop]

    def cs_frame(self, name: str):
        if name in ("", "Global"):
            return np.zeros(3), np.eye(3)
        try:
            return coordsys.frame(self.doc.design, name, self.doc.evaluator())
        except Exception as e:  # noqa: BLE001
            raise L.CalcError(f"Coordinate system '{name}': {e}") from None

    # ---- geometry ----------------------------------------------------------------------------------
    def _object(self, name: str):
        des = self.doc.design
        if not des.has_obj(name):
            raise L.CalcError(f"'{name}' is not an object of this design")
        t = self.doc.tess.get(name)
        if t is None:
            raise L.CalcError(f"'{name}' has no geometry (check the model)")
        if name not in self._cad:
            self._cad[name] = cad_mesh(t)
        return des.obj(name), t, self._cad[name]

    def _pec(self, ob) -> bool:
        from ..model.materials import is_conductor
        try:
            return is_conductor(self.doc.project.material(ob.material)) and not ob.solve_inside
        except Exception:  # noqa: BLE001
            return str(ob.material).lower() == "pec"

    def _faces(self, owner, local, outward_owner: bool, label: str) -> Samples:
        """Element faces with the normal pointing away from the owner element (or toward it)."""
        mesh = self.mesh
        tri = mesh.face_triangles(owner, local)
        c = self.nodes[self.tets[owner]].mean(axis=1)
        d = (tri.mean(axis=1) - c) * (1 if outward_owner else -1)
        return mesh.triangle_samples(tri, owner, d, label=label, faces=True)

    def _sheet(self, keys, owner, local, cad, label: str) -> Samples:
        """Faces of a sheet: both elements of a face (owner2), the normal as the sheet's own normal."""
        order = np.argsort(keys, kind="stable")
        k, o, lc = keys[order], owner[order], local[order]
        idx = np.where(np.r_[True, k[1:] != k[:-1]])[0]
        nxt = np.minimum(idx + 1, len(k) - 1)
        two = (idx + 1 < len(k)) & (k[nxt] == k[idx])
        o1, o2 = o[idx], np.where(two, o[nxt], -1)
        tri = self.mesh.face_triangles(o1, lc[idx])
        cent = tri.mean(axis=1)
        cid, _ = cad.find_closest_cell(cent, return_closest_point=True)
        cn = cad.compute_normals(cell_normals=True, point_normals=False, auto_orient_normals=False,
                                 consistent_normals=False).cell_data["Normals"]
        nrm = np.asarray(cn)[np.asarray(cid, np.int64)]
        # the element on the side the normal points to comes first
        c1 = self.nodes[self.tets[o1]].mean(axis=1)
        swap = two & (np.einsum("kd,kd->k", c1 - cent, nrm) < 0)
        o1, o2 = np.where(swap, o2, o1), np.where(swap, o1, o2)
        return self.mesh.triangle_samples(tri, o1, nrm, owner2=o2, label=label, faces=True)

    def _non_model_sheet(self, cad, label: str) -> Samples:
        pts = np.asarray(cad.points, float)
        c = pts.mean(axis=0)
        _, sv, vt = np.linalg.svd(pts - c, full_matrices=False)
        size = float(np.ptp(pts, axis=0).max()) or 1.0
        cn = cad.compute_normals(cell_normals=True, point_normals=False, auto_orient_normals=False,
                                 consistent_normals=False).cell_data["Normals"]
        if sv[-1] <= 1e-6 * size * np.sqrt(len(pts)):
            n = vt[-1] if float(np.mean(np.asarray(cn) @ vt[-1])) >= 0 else -vt[-1]
            tri, owner, other = self.mesh.plane_triangles(n, float(n @ c))
            if len(tri):
                poly = pv.PolyData(tri.reshape(-1, 3), np.hstack([np.full((len(tri), 1), 3),
                                                                   np.arange(3 * len(tri)).reshape(-1, 3)]).ravel())
                poly.cell_data["_tet"] = owner
                poly.cell_data["_tet2"] = other
                crop = F._crop_to_sheet(poly, cad, c, vt[:2], size)
                if crop is not None and crop.n_cells:
                    ct = np.asarray(crop.faces).reshape(-1, 4)[:, 1:]
                    T = np.asarray(crop.points, float)[ct]
                    o2 = np.asarray(crop.cell_data["_tet2"], np.int64)
                    return self.mesh.triangle_samples(T, np.asarray(crop.cell_data["_tet"], np.int64),
                                                      np.broadcast_to(n, (len(T), 3)),
                                                      owner2=o2 if (o2 >= 0).any() else None, label=label)
            raise L.CalcError(f"{label} lies outside the solution volume")
        # curved: the display triangles refined to the element size, located in the mesh
        ref = F._refined(cad, self.m)
        rt = np.asarray(ref.faces).reshape(-1, 4)[:, 1:]
        T = np.asarray(ref.points, float)[rt]
        nrm = np.asarray(ref.compute_normals(cell_normals=True, point_normals=False, auto_orient_normals=False,
                                             consistent_normals=False).cell_data["Normals"])
        lam_q, w_q = tri_rule()
        P = np.einsum("qi,kid->kqd", lam_q, T).reshape(-1, 3)
        area = np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1) / 2
        tets, lam = self.mesh.locate(P)
        return Samples(P, (area[:, None] * w_q[None, :]).ravel(), tets, lam, 2, N=np.repeat(nrm, len(w_q), axis=0),
                       label=label)

    def object_samples(self, name: str, dim: int) -> Samples:
        ob, t, cad = self._object(name)
        idx = self.names.index(name) if name in self.names else -1
        if dim == 3:
            if idx < 0:
                raise L.CalcError(f"'{name}' is not meshed (a perfect conductor, or not part of the model): no "
                                  "volume to integrate over")
            return self.mesh.volume_samples(F._object_tets(self.m, idx), name)
        if dim == 2:
            if ob.dim == 3 or getattr(t, "dim", 3) == 3:
                if idx >= 0:
                    pec = self._pec(ob)
                    _, owner, local = F._object_faces(self.m, idx, outside=pec)
                    return self._faces(owner, local, not pec, name)
                if not ob.model:
                    raise L.CalcError(f"'{name}' is a non-model solid: draw a sheet to integrate over")
                _, owner, local = F._sheet_faces(self.m, cad, solid=True)
                if not len(owner):
                    raise L.CalcError(f"No element faces found on '{name}'")
                return self._faces(owner, local, False, name)        # field outside a hole: normal out of it
            if ob.model:
                keys, owner, local = F._sheet_faces(self.m, cad, False)
                if len(owner):
                    s = self._sheet(keys, owner, local, cad, name)
                    if s.W.sum() >= 0.5 * float(cad.area):
                        return s
            return self._non_model_sheet(cad, name)
        if dim == 1:
            lines = [np.asarray(e, float) for e in (t.edges or []) if len(e) > 1]
            if not lines:
                raise L.CalcError(f"'{name}' has no edges")
            return self.mesh.line_samples(lines, name)
        verts = np.asarray(t.vertices, float).reshape(-1, 3)
        if not len(verts):
            raise L.CalcError(f"'{name}' has no point")
        return self.mesh.point_samples(verts[:1], name)

    def face_samples(self, name: str, index: int) -> Samples:
        ob, t, cad = self._object(name)
        label = f"face({name}, {index})"
        if "face" not in cad.cell_data or index not in set(np.asarray(cad.cell_data["face"]).tolist()):
            raise L.CalcError(f"'{name}' has no face {index}")
        idx = self.names.index(name) if name in self.names else -1
        if idx >= 0 and (ob.dim == 3 or getattr(t, "dim", 3) == 3):
            pec = self._pec(ob)
            sel = F._restrict_faces(self.m, F._object_faces(self.m, idx, outside=pec), cad, index)
            if not len(sel[1]):
                raise L.CalcError(f"No element faces found on {label}")
            return self._faces(sel[1], sel[2], not pec, label)
        solid = ob.dim == 3
        keys, owner, local = F._sheet_faces(self.m, cad, solid, index)
        if not len(owner):
            sub = cad.extract_cells(np.where(cad.cell_data["face"] == index)[0]).extract_surface()
            return self._non_model_sheet(sub, label)
        return self._faces(owner, local, False, label) if solid else self._sheet(keys, owner, local, cad, label)


def field_solutions(doc) -> list[str]:
    return F.field_solutions(doc)


def frequencies(doc, solution: str) -> list[float]:
    from ..solver.lazy_fields import frequencies as freqs
    st, sol = F.store_for(doc, solution)
    return freqs(st, sol)


def evaluate(doc, src: str, solution: str, fi: int = 0, port: int = 1, phase_deg: float = 0.0,
             env: L.Env | None = None) -> Result:
    """Value of an expression on a solution (Fields Calculator, reports, scripts)."""
    return Evaluator(env or make_env(doc), DocFieldSource(doc, solution, fi, port), phase_deg).value(src)


def network_value(data: dict, kind: str, ports: list[int], k: int) -> complex:
    """S(i,j), Y(i,j), Z(i,j) or VSWR(i) of solution data at frequency index k."""
    from ..solver.expressions import network
    S = data["S"]
    if any(not 1 <= p <= S.shape[1] for p in ports):
        raise L.CalcError(f"{kind}({', '.join(map(str, ports))}): the solution has {S.shape[1]} port(s)")
    i = ports[0] - 1
    if kind == "VSWR":
        g = abs(S[k, i, i])
        return complex((1 + g) / max(1 - g, 1e-12))
    cat = {"S": "S Parameter", "Y": "Y Parameter", "Z": "Z Parameter"}[kind]
    one = {**data, "S": S[k:k + 1]}
    return complex(network(one, cat)[0, i, ports[1] - 1])


class NetSource(FieldSource):
    """The network data (S parameters) of a solution at frequency index k, without fields: for equations
    of S, Y, Z and VSWR."""

    def __init__(self, data: dict, k: int):
        super().__init__()
        self.data, self.k = data, k
        self.freq = float(data["freq"][k])

    def network(self, kind: str, ports: list[int]) -> complex:
        return network_value(self.data, kind, ports, self.k)

    def nodal(self, *_) -> np.ndarray:
        raise L.CalcError("This equation needs the fields: report it in a Fields report")

    material = samples = nodal


def over_frequency(doc, src: str, solution: str, port: int = 1, store=None, progress=None):
    """(frequencies (Hz), complex values) of a value expression at every frequency with saved fields; one
    without fields (S parameters) at every frequency of the solution."""
    env = make_env(doc)
    info = L.analyze(src, env)
    if info.field:
        raise L.CalcError(f"'{src}' is a field; a report needs a value (an integral, maximum or point value)")
    if info.vec:
        raise L.CalcError(f"'{src}' is a vector; report its magnitude or one component, e.g. mag({src}) or x({src})")
    st, sol = (store, solution.partition(":")[2].strip()) if store is not None else F.store_for(doc, solution)
    if "fields" not in info.uses:
        data = st.sparams(sol)
        if data is None:
            raise L.CalcError(f"No S parameters for '{solution}'")
        vals = [complex(Evaluator(env, NetSource(data, k)).value(src).value) for k in range(len(data["freq"]))]
        return np.asarray(data["freq"], float), np.asarray(vals, complex)
    from ..solver.lazy_fields import frequencies as freqs
    fr = freqs(st, sol)
    if not fr:
        raise L.CalcError(f"No saved fields for '{solution}' (enable 'Save fields' in the setup or sweep)")
    vals = []
    for k in range(len(fr)):
        source = DocFieldSource(doc, solution, k, port, store=store)
        vals.append(complex(Evaluator(env, source).value(src).value))
        if progress is not None:
            progress((k + 1) / len(fr))
    return np.asarray(fr, float), np.asarray(vals, complex)


def field_values_at_grid(doc, src: str, solution: str, fi: int, port: int, phase_deg: float = 0.0):
    """A field expression at every field node of the solution grid (for field plots): (values, Info)."""
    env = make_env(doc)
    info = L.analyze(src, env)
    if not info.field:
        raise L.CalcError(f"'{src}' is a value, not a field: there is nothing to plot over the model")
    if info.surf:
        raise L.CalcError("Expressions with Js, n, normal() or tangent() exist on faces only and cannot be "
                          "plotted yet; plot Mag_Jsurf or integrate them")
    source = DocFieldSource(doc, solution, fi, port)
    S = source.mesh.node_samples(np.arange(len(source.tets)), source.npt)
    vals = Evaluator(env, source, phase_deg).at(src, S)
    return vals, info, source
