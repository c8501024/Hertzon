"""Derivative tuning: S-parameters of nearby designs from one solve, for live sliders.

For the selected design variables p the solver mesh is morphed (hertzon.solver.morph: the connectivity
stays, the nodes move with the variables), so the FEM matrix A(p) changes smoothly with p. At every
frequency of the sweep:

1. A is assembled on the nominal mesh and on slightly morphed meshes; central differences give its first
   and second derivatives with respect to every variable (and the mixed second derivatives), so that
   A(p0 + dp) ~ A0 + sum dp_i A_i + 1/2 sum dp_i dp_j A_ij;
2. the nominal system is factorized once, and the same factorization gives the solution x0 and its first
   and second derivatives (A0 x_i = -A_i x0, A0 x_ij = -(A_ij x0 + A_i x_j + A_j x_i));
3. these vectors span a small basis W; the matrix terms are projected onto it (W^T A W) and the
   S-parameter functionals of the ports are evaluated on its columns.

A slider position dp then costs one small dense solve per frequency: (W^T A(p0+dp) W) y = W^T b and
S = L W y. The approximation is applied to the matrix, not to the S-parameters, so resonances move with
the variables instead of being smeared by a Taylor series of S. At dp = 0 the result is the nominal
solution exactly.

The model is valid while the morphed mesh stays reasonable: the range of every variable is limited to the
offsets at which no tetrahedron shrinks below 30 % of its volume (morph.valid_range), and the further a
slider is from the solved value, the larger the error. A full solve at the new value is the reference.

Saved as tuning_<sweep>.npz in the result folder of the setup; tuning_model() reads it and evaluate()
gives the S-parameters for slider positions (no solver needed, used by the GUI).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import morph as MO


# ------------------------------------------------------------------------------------------------
# solver side
# ------------------------------------------------------------------------------------------------
def set_nodes(mesh, X: np.ndarray) -> None:
    """Move the mesh nodes to X (3, N) and update the quantities EMerge derives from them."""
    mesh.nodes[...] = X
    n, t, tr, e = mesh.nodes, mesh.tets, mesh.tris, mesh.edges
    mesh.centers = (n[:, t[0]] + n[:, t[1]] + n[:, t[2]] + n[:, t[3]]) / 4
    mesh.tri_centers = (n[:, tr[0]] + n[:, tr[1]] + n[:, tr[2]]) / 3
    mesh.edge_centers = (n[:, e[0]] + n[:, e[1]]) / 2
    mesh.edge_lengths = np.linalg.norm(n[:, e[0]] - n[:, e[1]], axis=0)
    mesh.areas = np.linalg.norm(np.cross((n[:, tr[1]] - n[:, tr[0]]).T, (n[:, tr[2]] - n[:, tr[0]]).T), axis=1) / 2


class Assembly:
    """FEM systems of the EMerge model m at given node positions (the nominal mesh is restored after).

    Every node position (state) keeps its own sparsity pattern object with the frequency independent volume
    matrices and boundary matrices cached on it, as long as memory allows, so that the next frequency of a
    state only adds the frequency dependent terms."""

    def __init__(self, m, keep_states: bool | None = None):
        self.m = m
        self.mw = m.mw
        self.mesh = m.mesh
        self.X0 = np.array(m.mesh.nodes, copy=True)
        self._at = None                 # key of the node positions in the mesh (None: nominal)
        self._systems: dict = {}        # state key -> EMerge SystemPattern of that state
        self.keep_states = keep_states

    _ANON = ("anonymous",)

    def _move(self, X, key) -> None:
        if key == self._at and key != self._ANON:
            return
        asy = self.mw.assembler
        if self.keep_states and asy._system is not None and self._at != self._ANON:
            self._systems[self._at] = asy._system
        set_nodes(self.mesh, self.X0 if X is None else X)
        asy.reset_cache()
        if self.keep_states and key != self._ANON:
            asy._system = self._systems.pop(key, None)
        self._at = key

    def system(self, freq: float, X=None, key=None):
        """(A, b, solve_ids, mats) at frequency freq with the nodes at X (None: nominal); key names the node
        positions (a state), so that its cached matrices are reused at the next frequency. Without a key the
        system at X is assembled anew."""
        if X is not None and key is None:
            key = self._ANON
        self._move(X, key)
        mw = self.mw
        job, mats = mw.assembler.assemble_freq_matrix(mw.basis, mw.mat_assy, mw.bc.boundary_conditions, freq,
                                                      cache_matrices=True)
        if self.keep_states is None:
            self.keep_states = _memory_allows(job.A.nnz)
        # one column per port mode, in the order of the S-matrix (iter_port_modes)
        order = [job.b_ind_map[si] for _, si, _ in mw.bc.iter_port_modes()]
        return job.A, job.b_vectors[:, order], np.asarray(job.solve_ids), mats

    def restore(self) -> None:
        self._move(None, None)

    def close(self) -> None:
        self.restore()
        self._systems.clear()


def _memory_allows(nnz: int, states: int = 8) -> bool:
    """Room for the cached matrices of all states (two complex arrays of nnz entries each)?"""
    need = states * 2 * 16 * nnz
    try:
        import psutil
        return need < 0.3 * psutil.virtual_memory().available
    except Exception:  # noqa: BLE001
        return need < 2 * 2 ** 30


def s_functionals(mw, freq: float, W: np.ndarray, NF: int, ids: np.ndarray, mats, powers: dict):
    """S-parameter functionals of the ports on the basis W (solve dofs x k): S_ij = c_ij + L_ij . y_j for
    a solution x_j = W y_j of excitation j. Returns (Lp, La, c0): Lp[i] (passive port i), La[i] (port i
    excited, linear part) and c0[i] (port i excited, constant part), exactly as EMerge's post-processing."""
    k0 = 2 * np.pi * freq / 299792458
    mesh, basis = mw.mesh, mw.basis
    er, ur, cond = mats
    ertri, urtri, _ = mw._materials_to_tris(er, ur, cond)
    pm = list(mw.bc.iter_port_modes())
    n, k = len(pm), W.shape[1]
    Lp = np.zeros((n, k), complex)
    La = np.zeros((n, k), complex)
    c0 = np.zeros(n, complex)
    full = np.zeros(NF, complex)
    for i, (port, si, mi) in enumerate(pm):
        tets = mesh.get_face_tets(port.tags)
        tris = mesh.get_triangles(port.tags)
        tv = mesh.tris[:, tris]
        er_i, ur_i = ertri[:, :, tris], urtri[:, :, tris]
        pw = abs(powers[si])

        def value(x, active):
            ff = basis.interpolate_Ef(x, tetids=tets)
            a, e = mw._compute_s_data(port, mi, active, ff, tv, k0, er_i, ur_i)
            return a / e * pw

        full[:] = 0
        c0[i] = value(full, True)
        for j in range(k):
            full[:] = 0
            full[ids] = W[:, j]
            Lp[i, j] = value(full, False)
            La[i, j] = value(full, True) - c0[i]
    return Lp, La, c0


def _orth(cols: list[np.ndarray], tol: float = 1e-10) -> np.ndarray:
    """Orthonormal basis (Gram-Schmidt twice) of the columns, dropping dependent ones."""
    V = []
    for v in cols:
        v = np.array(v, dtype=np.complex128)
        n0 = np.linalg.norm(v)
        if n0 == 0:
            continue
        for _ in range(2):
            for u in V:
                v -= u * (u.conj() @ v)
        n = np.linalg.norm(v)
        if n > tol * n0:
            V.append(v / n)
    return np.column_stack(V)


def reduce_system(A0, b, Ai: list, Aii: list, Aij: dict, factor) -> tuple[dict, np.ndarray]:
    """Reduced model of A(dp) x = b with A(dp) = A0 + sum dp_i Ai + 1/2 sum dp_i^2 Aii + sum_{i<j} dp_i dp_j
    Aij: the basis W of the solution and its first and second derivatives at dp = 0 (one factorization of
    A0, factor(A0).solve), and the matrix terms projected onto it. Returns (terms, W)."""
    n = len(Ai)
    fac = factor(A0)
    try:
        x0 = fac.solve(b)
        xi = [fac.solve(-(Ai[i] @ x0)) for i in range(n)]
        xii = [fac.solve(-(Aii[i] @ x0 + 2 * (Ai[i] @ xi[i]))) for i in range(n)]
        xij = {(i, j): fac.solve(-(Aij[(i, j)] @ x0 + Ai[i] @ xi[j] + Ai[j] @ xi[i])) for (i, j) in Aij}
    finally:
        if hasattr(fac, "close"):
            fac.close()
    cols = [x0[:, e] for e in range(x0.shape[1])]
    for group in (xi, xii, [xij[k] for k in sorted(xij)]):
        for x in group:
            cols += [x[:, e] for e in range(x.shape[1])]
    W = _orth(cols)
    proj = lambda M: W.T @ (M @ W)      # noqa: E731  (complex symmetric: W^T keeps the reduced matrix symmetric)
    k = W.shape[1]
    out = {
        "A0": proj(A0), "b": W.T @ b,
        "Ai": np.array([proj(M) for M in Ai]).reshape(n, k, k),
        "Aii": np.array([proj(M) for M in Aii]).reshape(n, k, k),
        "Aij": np.array([proj(Aij[key]) for key in sorted(Aij)]).reshape(len(Aij), k, k),
    }
    return out, W


def frequency_model(asm: Assembly, freq: float, V: list[np.ndarray], steps: list[float], log=None) -> dict:
    """Reduced model of one frequency (see the module docstring). V: velocity fields (3, N) per variable,
    steps: finite difference step of every variable (in its unit)."""
    from .fast_sweep import _Factor
    from emerge._emerge.mth.csc_submatrix import csc_submatrix as submatrix
    mw = asm.mw
    n = len(V)
    mw._compute_modes(freq)
    keep = list(mw.frequencies) if hasattr(mw, "frequencies") else None
    try:
        mw.frequencies = [freq]
        powers = mw._check_port_powers()
    finally:
        if keep is not None:
            mw.frequencies = keep
    A, b, ids, mats = asm.system(freq)
    NF = A.shape[0]
    A0 = submatrix(A, ids).tocsr()
    bs = np.asarray(b[ids], dtype=np.complex128)

    def at(c: dict):
        X = asm.X0 + sum(ci * steps[i] * V[i] for i, ci in c.items())
        Ac, bc, idc, _ = asm.system(freq, X, key=tuple(sorted(c.items())))
        if not np.array_equal(idc, ids):
            raise MO.MorphError("the morphed mesh changes the unknowns of the solve")
        return submatrix(Ac, ids).tocsr()

    t = time.time()
    Ai, Aii, Ap = [], [], []
    for i in range(n):
        ap, am = at({i: +1}), at({i: -1})
        Ai.append((ap - am) / (2 * steps[i]))
        Aii.append((ap - 2 * A0 + am) / steps[i] ** 2)
        Ap.append(ap)
    Aij = {}
    for i in range(n):
        for j in range(i + 1, n):
            apij = at({i: +1, j: +1})
            Aij[(i, j)] = (apij - Ap[i] - Ap[j] + A0) / (steps[i] * steps[j])
    asm.restore()
    t_asm = time.time() - t
    t = time.time()
    out, W = reduce_system(A0, bs, Ai, Aii, Aij, _Factor)
    t_fac = time.time() - t
    Lp, La, c0 = s_functionals(mw, freq, W, NF, ids, mats, powers)
    out.update(Lp=Lp, La=La, c0=c0)
    if log is not None:
        log(f"{freq / 1e9:.6g} GHz: basis {W.shape[1]}, assembly {t_asm:.1f} s, solves {t_fac:.1f} s")
    return out


# ------------------------------------------------------------------------------------------------
# evaluation (no solver)
# ------------------------------------------------------------------------------------------------
def pairs(n: int) -> list[tuple[int, int]]:
    return [(i, j) for i in range(n) for j in range(i + 1, n)]


def evaluate_freq(fm: dict, dp: np.ndarray, caps: tuple[bool, bool] = (True, True)) -> np.ndarray:
    """Raw S-matrix (ports x ports) of one frequency model at the variable offsets dp."""
    A = fm["A0"].copy()
    for i, d in enumerate(dp):
        if d:
            A += d * fm["Ai"][i] + 0.5 * d * d * fm["Aii"][i]
    for k, (i, j) in enumerate(pairs(len(dp))):
        if dp[i] and dp[j]:
            A += dp[i] * dp[j] * fm["Aij"][k]
    Y = np.linalg.solve(A, fm["b"])
    n = len(fm["c0"])
    S = fm["Lp"] @ Y
    S[np.diag_indices(n)] = np.einsum("ik,ki->i", fm["La"], Y) + fm["c0"]
    col, single = caps
    if col:
        for j in range(n):
            S[:, j] /= max(1.0, float(np.sum(np.abs(S[:, j]) ** 2)))
    if single:
        S /= np.maximum(1.0, np.abs(S))
    return S


# ------------------------------------------------------------------------------------------------
# the tuning run (solver process) and the saved model
# ------------------------------------------------------------------------------------------------
MAX_POINTS = 201
ADAPTIVE = "LastAdaptive"       # sweep name of the tuning model at the solution frequency of the setup


def tuning_path(out: Path, sweep: str) -> Path:
    from .results import _safe
    return Path(out) / f"tuning_{_safe(sweep)}.npz"


def tuning_frequencies(props: dict, ev, points: int) -> np.ndarray:
    """Frequencies of the tuning model: the points of a discrete sweep, else `points` equally spaced over
    the sweep (an interpolating or fast sweep has many points, and every tuning point costs one
    factorization)."""
    from .bridge import sweep_frequencies
    fr = np.unique(np.asarray(sweep_frequencies(props, ev), float))
    if props.get("SweepType") == "Discrete" and len(fr) <= MAX_POINTS:
        return fr
    n = max(2, min(int(points), MAX_POINTS))
    return np.linspace(fr.min(), fr.max(), n) if len(fr) > 1 else fr


def variable_step(text: str) -> float:
    """Finite difference step of the CAD build for a variable value (in its unit)."""
    sv = MO.split_value(text)
    if sv is None:
        raise MO.MorphError(f"'{text}' is an expression; only variables with a value can be tuned")
    return max(abs(sv[0]), 1.0) * 1e-4


def build(bridge, variables: list[str], freqs: np.ndarray, path: Path, log=None, progress=None) -> dict:
    """Tuning model of the EMerge model of bridge (prepared, final adaptive mesh) for the design
    variables, saved to path."""
    from . import bridge as B
    from .results import port_entries
    m, design = bridge.m, bridge.design
    if B.TERMINALS is not None:
        raise MO.MorphError("derivative tuning does not support terminal designs yet")
    if not variables:
        raise MO.MorphError("select at least one variable")
    log = log or (lambda s: None)
    mesh = m.mesh
    # EMerge state of a regular solve (field, boundary data, materials, port modes)
    fc = 0.5 * (float(freqs.min()) + float(freqs.max()))
    B.solve_at(m, [fc])
    t = time.time()
    fixed = set(mesh.exterior_face_tags)
    for port, _, _ in m.mw.bc.iter_port_modes():
        fixed.update(port.tags)
    diag = float(np.linalg.norm(mesh.nodes.max(axis=1) - mesh.nodes.min(axis=1)))
    tol = 1e-6 * diag
    faces = {}
    for v in variables:
        if v not in design.variables:
            raise MO.MorphError(f"'{v}' is not a design variable")
        faces[v] = MO.face_velocities(bridge.project, design, v, variable_step(design.variables[v]))
        if not any(f.moving for f in faces[v]):
            raise MO.MorphError(f"variable '{v}' does not change the geometry of the model")
    fields = MO.velocity_fields(mesh, faces, fixed, tol, log=log)
    V = [fields[v] for v in variables]
    log(f"Mesh morphing for {', '.join(variables)}: {time.time() - t:.1f} s")
    X0 = np.array(mesh.nodes)
    ranges = [MO.valid_range(X0, mesh.tets, Vi, 0.3) for Vi in V]
    hmed = float(np.median(mesh.edge_lengths))
    steps = [1e-3 * hmed / max(float(np.abs(Vi).max()), 1e-300) for Vi in V]
    settings = m.mw._settings
    caps = (bool(settings.mw_cap_sp_col), bool(settings.mw_cap_sp_single))
    asm = Assembly(m)
    data: dict = {}
    beta, z0 = [], None
    nfreq = len(freqs)
    try:
        for k, f in enumerate(freqs):
            if (Path(bridge.out) / "ABORT").exists():
                raise B.SolveAbort("aborted")
            fm = frequency_model(asm, float(f), V, steps, log=log)
            for key, val in fm.items():
                data[f"f{k}_{key}"] = val
            k0 = 2 * np.pi * float(f) / 299792458
            pm = list(m.mw.bc.iter_port_modes())
            beta.append([complex(p.get_beta(k0) or 0) for p, _, _ in pm])
            if z0 is None:
                z0 = np.array([complex(p.portZ0(k0) or 50.0) for p, _, _ in pm])
            if progress is not None:
                progress(k + 1, nfreq)
    finally:
        asm.close()
    nominal = np.array([evaluate_freq({kk[len(f"f{k}_"):]: vv for kk, vv in data.items() if kk.startswith(f"f{k}_")},
                                      np.zeros(len(variables)), caps) for k in range(nfreq)])
    meta = {
        "variables": variables,
        "values": [design.variables[v] for v in variables],
        "units": [MO.split_value(design.variables[v])[1] for v in variables],
        "ranges": [[float(a), float(b)] for a, b in ranges],
        "steps": steps,
        "caps": list(caps),
        "ports": port_entries(bridge.port_map or []),
        "model_key": getattr(bridge, "keys", {}).get("model_key", ""),
        "tets": int(mesh.n_tets),
        "created": time.time(),
    }
    np.savez_compressed(path, freq=np.asarray(freqs, float), beta=np.array(beta), z0=z0, nominal=nominal,
                        meta=np.array(json.dumps(meta)), **data)
    return meta


class TuningModel:
    """A saved tuning model: S-parameters for variable offsets without a solver."""

    def __init__(self, path: Path):
        with np.load(path, allow_pickle=False) as z:
            self.freq = z["freq"]
            self.beta = z["beta"]
            self.z0 = z["z0"]
            self.nominal_raw = z["nominal"]
            self.meta = json.loads(str(z["meta"]))
            keys = ("A0", "b", "Ai", "Aii", "Aij", "Lp", "La", "c0")
            self.fms = [{key: z[f"f{k}_{key}"] for key in keys} for k in range(len(self.freq))]
        self.variables = self.meta["variables"]
        self.ranges = np.array(self.meta["ranges"], float)
        self.caps = tuple(self.meta.get("caps", (True, True)))
        from .optimetrics import split_value
        self.values = np.array([split_value(v)[0] for v in self.meta["values"]], float)
        self.units = self.meta["units"]

    def raw(self, dp) -> np.ndarray:
        dp = np.asarray(dp, float)
        return np.array([evaluate_freq(fm, dp, self.caps) for fm in self.fms])

    def sparams(self, dp) -> tuple[np.ndarray, np.ndarray]:
        """(S, reference impedance per row) at the variable offsets dp, after the same port
        post-processing (de-embedding, renormalization) as a solved sweep."""
        from .results import deembed_renormalize
        S = self.raw(dp)
        ports = self.meta.get("ports") or []
        beta = self.beta if any(q.get("deembed") for q in ports) else None
        return deembed_renormalize(S, self.z0, ports, beta)

    def sweep_variable(self, k: int, i: int, values, dp) -> tuple[np.ndarray, np.ndarray]:
        """(S per value, reference impedance per row) at frequency index k with variable i set to the
        offsets `values` and the other variables at their offsets in dp."""
        from .results import deembed_renormalize
        rows = []
        for v in values:
            d = np.array(dp, float)
            d[i] = v
            rows.append(evaluate_freq(self.fms[k], d, self.caps))
        S = np.array(rows)
        ports = self.meta.get("ports") or []
        beta = self.beta[[k] * len(S)] if any(q.get("deembed") for q in ports) else None
        return deembed_renormalize(S, self.z0, ports, beta)

    def nominal(self) -> tuple[np.ndarray, np.ndarray]:
        return self.sparams(np.zeros(len(self.variables)))
