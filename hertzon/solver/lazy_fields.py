"""Field data on demand (the solution is saved, fields are computed when they are plotted).

After every adaptive pass and for every saved sweep the solver writes ``solution_<label>.npz``: the
FEM coefficients of every excitation and the mesh connectivity of the basis (about a second for a
few 100k elements). ``fields_<label>.npz`` (E and H at the corners or the 10 nodes of every
tetrahedron, as the plots use them) is computed from it the first time a plot or a derived quantity
needs it and then kept as a cache. Interpolating the fields of every port in every pass cost about
15 s per pass on a 250k element model.
"""
from __future__ import annotations

import numpy as np

TET10_EDGES = [(0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3)]   # VTK quadratic tetra mid-edge order


def field_layout(npt: int) -> np.ndarray:
    """The "layout" entry of fields_<solution>.npz: how E_<k>_<port> and H_<k>_<port> are ordered."""
    pts = "4 corners" if npt == 4 else "4 corners + 6 edge midpoints (VTK quadratic tetra order)"
    return np.array([f"per tetrahedron (tet-major): row t*{npt}+i is point i of tetrahedron t of mesh.npz, "
                     f"{pts}; reshape to (n_tets, {npt}, 3). Values are taken from that tetrahedron "
                     "(discontinuous across material interfaces), not averaged at shared nodes."])
MU0 = 1.25663706212e-6

# the GUI replaces this with a runner that computes in a worker thread and shows a progress bar
RUN = lambda text, fn: fn(None)  # noqa: E731

_INDEX = ("tets", "tris", "edges", "tet_to_edge", "tet_to_tri", "tet_to_field", "dofcodes")


def solution_arrays(m, fields: list, port_map: list, excite, quad: bool, curved: dict) -> dict:
    """What fields_from_solution needs: per frequency k and port n the coefficient vector sol_k_n."""
    mesh = m.mesh
    basis = fields[0].basis
    out = {"nodes": np.asarray(mesh.nodes, np.float64), "npt": np.array([10 if quad else 4]),
           "stamp": np.array([__import__("time").time_ns()])}
    for name, arr in (("tets", mesh.tets), ("tris", mesh.tris), ("edges", mesh.edges),
                      ("tet_to_edge", mesh.tet_to_edge), ("tet_to_tri", mesh.tet_to_tri),
                      ("tet_to_field", basis.tet_to_field), ("dofcodes", basis.dofcodes3d)):
        out[name] = np.asarray(arr).astype(np.int32)
    out.update(curved)
    for k, fld in enumerate(fields):
        out[f"freq_{k}"] = np.array([float(getattr(fld, "freq", 0.0))])
        out[f"dur_{k}"] = np.asarray(fld._dur)
        for port in port_map:
            excite(fld, port)
            out[f"sol_{k}_{port['number']}"] = np.asarray(fld._field).astype(np.complex64)
    return out


def fields_from_solution(sol: dict, progress=None) -> dict:
    """The content of fields_<label>.npz (same as the solver wrote it before) from solution_<label>.npz."""
    from emerge._emerge.compiled import MATHLIB
    idx = {k: np.ascontiguousarray(sol[k], np.int64) for k in _INDEX}
    nodes = np.ascontiguousarray(sol["nodes"], np.float64)
    tets = idx["tets"]
    nt = tets.shape[1]
    npt = int(sol["npt"][0])
    C = nodes[:, tets]                                            # (3, 4, N)
    if npt == 10:
        C = np.concatenate([C, np.stack([(C[:, i] + C[:, j]) / 2 for i, j in TET10_EDGES], axis=1)], axis=1)
    P = np.ascontiguousarray(C.transpose(0, 2, 1).reshape(3, -1))   # tet-major
    mapping = np.repeat(np.arange(nt, dtype=np.int64), npt)
    tetids = np.arange(nt, dtype=np.int64)
    out = {"tet4" if npt == 4 else "tet10": np.array([1]), "stamp": sol["stamp"], "layout": field_layout(npt)}
    curve = _curver(sol, nodes, tets, C, P, npt)
    keys = sorted((k for k in sol if k.startswith("sol_")), key=lambda s: tuple(int(x) for x in s.split("_")[1:]))
    for i, key in enumerate(keys):
        _, k, n = key.split("_")
        f = float(sol[f"freq_{k}"][0])
        vec = np.ascontiguousarray(sol[key], np.complex128)
        args = (idx["tets"], idx["tris"], idx["edges"], nodes, idx["tet_to_field"], idx["tet_to_edge"],
                idx["tet_to_tri"])
        E = MATHLIB.ned2_tet_interp(P, vec, *args, tetids, mapping, idx["dofcodes"])
        c = 1 / (-1j * 2 * np.pi * f * (np.asarray(sol[f"dur_{k}"]) * MU0))
        H = MATHLIB.ned2_tet_interp_curl(P, vec, *args, np.ascontiguousarray(c, np.complex128), tetids, mapping,
                                         idx["dofcodes"])
        Ev, Hv = curve(np.nan_to_num(E).T, np.nan_to_num(H).T)
        out[f"E_{k}_{n}"] = Ev.astype(np.complex64)
        out[f"H_{k}_{n}"] = Hv.astype(np.complex64)
        out[f"freq_{k}"] = np.array([f])
        if progress is not None:
            progress((i + 1) / len(keys))
    return out


def _curver(sol, nodes, tets, C, P, npt):
    """Covariant Piola map of the curved tetrahedra (as the solver's save_fields did)."""
    if "curv_tets" not in sol:
        return lambda Ev, Hv: (Ev, Hv)
    from . import curvilinear as CV
    ct = np.asarray(sol["curv_tets"], np.int64)
    lam = np.eye(4)
    if npt == 10:
        lam = np.vstack([lam] + [(lam[i] + lam[j]) / 2 for i, j in TET10_EDGES])
    _, D = CV.curved_map(np.transpose(C[:, :4, ct], (2, 1, 0)), sol["curv_disp"], lam)
    DiT = np.linalg.inv(D).transpose(0, 1, 3, 2)
    DdD = D / np.linalg.det(D)[..., None, None]
    rows = (ct[:, None] * npt + np.arange(npt)).ravel()

    def curve(Ev, Hv):
        Ec = np.einsum("tpij,tpj->tpi", DiT, Ev[rows].reshape(len(ct), npt, 3)).reshape(-1, 3)
        Hc = np.einsum("tpij,tpj->tpi", DdD, Hv[rows].reshape(len(ct), npt, 3)).reshape(-1, 3)
        Ev, Hv = Ev.copy(), Hv.copy()
        Ev[rows], Hv[rows] = Ec, Hc
        return Ev, Hv
    return curve


def _stamp(path) -> int | None:
    try:
        with np.load(path, allow_pickle=False) as d:
            return int(d["stamp"][0]) if "stamp" in d.files else None
    except (OSError, ValueError, EOFError, KeyError):
        return None


def fields_npz(st, label: str, load) -> dict | None:
    """fields_<label>.npz, computed from solution_<label>.npz first when that is newer."""
    fp, sp = st.folder / f"fields_{label}.npz", st.folder / f"solution_{label}.npz"
    if not sp.exists():
        return load(fp)
    s = _stamp(sp)
    if fp.exists() and s is not None and _stamp(fp) == s:
        return load(fp)
    sol = load(sp)
    if sol is None:
        return load(fp)
    res = RUN(f"Computing field data ({label})", lambda prog: fields_from_solution(sol, prog))
    try:
        st.save_npz(f"fields_{label}.npz", **res)
    except OSError:
        pass
    return res


def frequencies(st, label: str) -> list[float]:
    """Frequencies with field data of a solution, without computing the fields."""
    for name in (f"solution_{label}.npz", f"fields_{label}.npz"):
        p = st.folder / name
        if p.exists():
            try:
                with np.load(p, allow_pickle=False) as d:
                    ks = sorted((k for k in d.files if k.startswith("freq_")), key=lambda k: int(k.split("_")[1]))
                    return [float(d[k][0]) for k in ks]
            except (OSError, ValueError, EOFError):
                pass
    return []
