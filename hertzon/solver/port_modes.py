"""Port modes of wave ports: which mode is which, and the same mode from pass to pass.

* Labels (``describe_mode``): mode type, the closest mode of a rectangular guide over the port face
  ("TE10-like"), the symmetry about the two principal axes of the port face (even = the axis is a magnetic
  wall, odd = an electric wall) and the propagation constant. They name the excitations of field plots,
  Edit Sources and the MCP port data ("P1 M2 (TE10-like, beta 72.3 rad/m)").
* Tracking between adaptive passes (``ModeTracker``): the mesh of every pass gives a new port eigen solution,
  and a mode is only defined up to its sign (a phase for lossy ports), and modes with close propagation
  constants may swap places. The tracker samples every mode at fixed points of the port face and matches it
  to the mode of the previous pass with the largest field overlap, so Delta S compares the same modes in the
  same gauge (``gauge_aligned``).
"""
from __future__ import annotations

import numpy as np

C0 = 299792458.0
SYM_LIMIT = 0.9          # |symmetry correlation| above which a mode is called even or odd
RECT_LIMIT = 0.75        # correlation with a rectangular guide mode above which it is named after it
RECT_ORDERS = 4          # TE/TM mn with m, n <= this are tried


# ------------------------------------------------------------------------------------------------
# geometry of a port face
# ------------------------------------------------------------------------------------------------
def face_frame(nodes: np.ndarray, tris: np.ndarray) -> dict:
    """Centroid, normal and principal in-plane axes (u along the larger extent) of a planar port face given
    by its triangles (nodes 3 x N, tris 3 x M), plus the centroids and areas of the triangles."""
    P = np.asarray(nodes, float)[:, np.asarray(tris)]                  # (3, 3, M)
    a, b, c = P[:, 0], P[:, 1], P[:, 2]
    cr = np.cross((b - a).T, (c - a).T).T
    area = 0.5 * np.linalg.norm(cr, axis=0)
    cent = (a + b + c) / 3
    A = float(np.sum(area))
    c0 = cent @ area / A
    nrm = cr.sum(axis=1)
    nrm = nrm / (np.linalg.norm(nrm) or 1.0)
    d = cent - c0[:, None]
    # in-plane second moment (from the corners as well: a face of two triangles needs them)
    pts = np.concatenate([a, b, c, cent], axis=1) - c0[:, None]
    w = np.concatenate([area, area, area, 3 * area])
    M = (pts * w) @ pts.T
    Pn = np.eye(3) - np.outer(nrm, nrm)
    vals, vecs = np.linalg.eigh(Pn @ M @ Pn)
    u, v = vecs[:, 2], vecs[:, 1]
    u, v = _canonical_axis(u), _canonical_axis(v)
    ext_u = float(np.ptp(np.concatenate([u @ (x - c0[:, None]) for x in (a, b, c)])))
    ext_v = float(np.ptp(np.concatenate([v @ (x - c0[:, None]) for x in (a, b, c)])))
    lo_u = float(min(np.min(u @ (x - c0[:, None])) for x in (a, b, c)))
    lo_v = float(min(np.min(v @ (x - c0[:, None])) for x in (a, b, c)))
    return {"center": c0, "normal": nrm, "u": u, "v": v, "area": A, "points": cent, "weights": area,
            "extent": (ext_u, ext_v), "low": (lo_u, lo_v), "offsets": d}


def _canonical_axis(x: np.ndarray) -> np.ndarray:
    """The axis with its largest component positive (a fixed orientation, independent of the mesh)."""
    i = int(np.argmax(np.abs(x)))
    return x if x[i] >= 0 else -x


def axis_name(x: np.ndarray) -> str:
    """'x', 'y', 'z' for an axis along a global axis, else the direction rounded."""
    i = int(np.argmax(np.abs(x)))
    if abs(x[i]) > 0.999:
        return "xyz"[i]
    return "(" + ", ".join(f"{c:.2f}" for c in x) + ")"


def mirror_points(fr: dict, axis: str) -> np.ndarray:
    """The triangle centroids mirrored at the plane through the face centre normal to axis 'u' or 'v'."""
    ax = fr[axis]
    P = fr["points"]
    return P - 2 * np.outer(ax, ax @ (P - fr["center"][:, None]))


def symmetry(E: np.ndarray, Em: np.ndarray, fr: dict, axis: str) -> float:
    """Correlation of the transverse field with its mirror image at the plane normal to axis: +1 even (the plane
    is a magnetic wall, E parallel to it), -1 odd (an electric wall, E normal to it), 0 neither. E is the field
    at the triangle centroids, Em at the mirrored centroids (3 x M)."""
    ax, n, w = fr[axis], fr["normal"], fr["weights"]
    Et = E - np.outer(n, n @ E)
    Emt = Em - np.outer(n, n @ Em)
    R = np.eye(3) - 2 * np.outer(ax, ax)          # the mirror applied to a vector
    num = np.sum(np.sum(np.conj(R @ Et) * Emt, axis=0) * w)
    den = np.sqrt(np.sum(np.sum(np.abs(Et) ** 2, axis=0) * w) * np.sum(np.sum(np.abs(Emt) ** 2, axis=0) * w))
    return float(np.real(num) / den) if den > 0 else 0.0


def rectangular_match(E: np.ndarray, fr: dict) -> tuple[str, float]:
    """The TEmn or TMmn mode of a rectangular guide over the bounding rectangle of the face (principal axes)
    whose transverse E correlates best with E, and that correlation (0..1)."""
    n, w = fr["normal"], fr["weights"]
    Et = E - np.outer(n, n @ E)
    eu, ev = fr["u"] @ Et, fr["v"] @ Et
    a, b = fr["extent"]
    if a <= 0 or b <= 0:
        return "", 0.0
    x = fr["u"] @ fr["offsets"] - fr["low"][0]
    y = fr["v"] @ fr["offsets"] - fr["low"][1]
    norm_e = np.sqrt(np.sum((np.abs(eu) ** 2 + np.abs(ev) ** 2) * w))
    if norm_e == 0:
        return "", 0.0
    best, cbest = "", 0.0
    for m in range(RECT_ORDERS + 1):
        for k in range(RECT_ORDERS + 1):
            if m == k == 0:
                continue
            kx, ky = m * np.pi / a, k * np.pi / b
            # transverse E of TEmn ~ (ky cos(kx x) sin(ky y), -kx sin(kx x) cos(ky y)), of TMmn
            # ~ (kx cos sin, ky sin cos); TM needs m, n >= 1
            cs, sc = np.cos(kx * x) * np.sin(ky * y), np.sin(kx * x) * np.cos(ky * y)
            cand = [("TE", ky * cs, -kx * sc)]
            if m and k:
                cand.append(("TM", kx * cs, ky * sc))
            for typ, pu, pv in cand:
                nb = np.sqrt(np.sum((pu ** 2 + pv ** 2) * w))
                if nb == 0:
                    continue
                c = abs(np.sum((np.conj(eu) * pu + np.conj(ev) * pv) * w)) / (norm_e * nb)
                if c > cbest:
                    best, cbest = f"{typ}{m}{k}", float(c)
    return best, cbest


def describe_mode(E_function, fr: dict, modetype: str, beta: complex) -> dict:
    """Label and symmetry of one port mode (E_function: the mode's E callable in global coordinates)."""
    P = fr["points"]
    E = np.asarray(E_function(*P), complex)
    out = {"type": str(modetype), "beta": [float(np.real(beta)), float(np.imag(beta))]}
    sym = {}
    for axis in ("u", "v"):
        try:
            Em = np.asarray(E_function(*mirror_points(fr, axis)), complex)
            s = symmetry(E, np.nan_to_num(Em), fr, axis)
        except Exception:  # noqa: BLE001
            continue
        sym[axis_name(fr[axis])] = {"correlation": round(s, 3),
                                    "parity": "even" if s >= SYM_LIMIT else "odd" if s <= -SYM_LIMIT else "none"}
    out["symmetry"] = sym
    name, c = rectangular_match(E, fr)
    out["rect_match"] = {"mode": name, "correlation": round(c, 3)}
    if str(modetype).upper() == "TEM":
        out["short"] = "TEM"
    elif name and c >= RECT_LIMIT:
        out["short"] = f"{name}-like"
    else:
        out["short"] = str(modetype) if modetype else "mode"
        par = [f"{p['parity']} in {ax}" for ax, p in sym.items() if p["parity"] != "none"]
        if par:
            out["short"] += ", " + ", ".join(par)
    return out


def excitation_label(port: int, mode: int, info: dict | None) -> str:
    """'P1 M2 (TE10-like, beta 72.3 rad/m)'."""
    s = f"P{port} M{mode}"
    if info:
        b = info.get("beta") or [0, 0]
        extra = info.get("short", "")
        if abs(b[0]) > 0 or abs(b[1]) > 0:
            bt = f"beta {b[0]:.4g} rad/m" if abs(b[0]) >= abs(b[1]) else f"alpha {abs(b[1]):.4g} Np/m"
            extra = f"{extra}, {bt}" if extra else bt
        if extra:
            s += f" ({extra})"
    return s


# ------------------------------------------------------------------------------------------------
# tracking the port modes from pass to pass
# ------------------------------------------------------------------------------------------------
class ModeTracker:
    """Samples the port modes of every S-matrix row at fixed points of each port face (the triangle centroids
    of the first pass) and matches them with the modes of the previous pass."""

    def __init__(self):
        self.points: dict[int, np.ndarray] = {}
        self.prev: list | None = None

    def sample(self, m, freq: float) -> list:
        """Per S-matrix row (port, mode, field at the sample points or None for a lumped port)."""
        from emerge._emerge.physics.microwave.bcs.port_bcs import ModalPort
        k0 = 2 * np.pi * float(freq) / C0
        out = []
        for port, _, mode_nr in m.mw.bc.iter_port_modes():
            num = int(port.port_number)
            vec = None
            if isinstance(port, ModalPort):
                try:
                    if num not in self.points:
                        mesh = m.mesh
                        tris = mesh.tris[:, mesh.get_triangles(port.tags)]
                        P = np.asarray(mesh.nodes, float)[:, tris]
                        self.points[num] = P.mean(axis=1)
                    md = port.get_modes(k0)[mode_nr - 1]
                    vec = np.nan_to_num(np.asarray(md.E_function(*self.points[num]), complex)).ravel()
                except Exception:  # noqa: BLE001
                    vec = None
            out.append((num, int(mode_nr), vec))
        return out

    def match(self, cur: list) -> tuple[np.ndarray, np.ndarray, list[str]] | None:
        """(perm, phase, notes) of the current modes against the previous pass: current row i is row perm[i] of
        the previous pass, and its mode is phase[i] times that previous mode. None for the first pass or when
        the rows do not fit. The current samples become the reference of the next pass."""
        prev, self.prev = self.prev, cur
        if prev is None or len(prev) != len(cur):
            return None
        n = len(cur)
        perm, phase, notes = np.arange(n), np.ones(n, complex), []
        ports: dict[int, list[int]] = {}
        for i, (num, _, _) in enumerate(cur):
            ports.setdefault(num, []).append(i)
        for num, rows in ports.items():
            if any(prev[i][0] != num for i in rows):
                return None
            if any(cur[i][2] is None or prev[i][2] is None for i in rows):
                continue
            A = np.array([prev[i][2] for i in rows])
            B = np.array([cur[i][2] for i in rows])
            O = np.conj(A) @ B.T                                        # O[p, c] = <prev p, cur c>
            nrm = np.outer(np.linalg.norm(A, axis=1), np.linalg.norm(B, axis=1))
            with np.errstate(all="ignore"):
                C = np.where(nrm > 0, np.abs(O) ** 2 / np.where(nrm > 0, nrm, 1) ** 2, 0.0)
            if len(rows) > 1:
                from scipy.optimize import linear_sum_assignment
                pr, cc = linear_sum_assignment(-C)
                assign = dict(zip(cc, pr))
            else:
                assign = {0: 0}
            for c_i, p_i in assign.items():
                perm[rows[c_i]] = rows[p_i]
                o = O[p_i, c_i]
                if abs(o) > 0 and C[p_i, c_i] > 0.25:
                    phase[rows[c_i]] = o / abs(o)
            if any(assign[c] != c for c in assign):
                order = ", ".join(f"mode {c + 1} = previous mode {assign[c] + 1}" for c in sorted(assign)
                                  if assign[c] != c)
                notes.append(f"port {num}: the modes changed their order ({order})")
        return perm, phase, notes


def to_previous_gauge(S: np.ndarray, perm: np.ndarray, phase: np.ndarray) -> np.ndarray:
    """S of the current pass in the mode order and gauge of the previous pass: current mode i = phase[i] x
    previous mode perm[i]; the waves of a mode scaled by c scale by 1/c, so S_ij -> S_ij c_i / c_j."""
    S = np.asarray(S, complex)
    T = S * phase[:, None] / phase[None, :]
    out = np.empty_like(T)
    out[np.ix_(perm, perm)] = T
    return out


def sign_gauge(S_old: np.ndarray, S_new: np.ndarray, rel: float = 0.05) -> np.ndarray:
    """Sign of every mode (+1/-1) that best maps S_new onto S_old when the modes themselves are unknown:
    S_new_ij = s_i s_j S_old_ij. Only couplings clearly above the noise (both |S| > rel x max |S|) decide,
    strongest first (a spanning forest); a mode without such a coupling keeps its sign (the solver's own gauge
    fixes it). Comparing only with row 1, as before, flipped modes that do not couple to port 1 on noise."""
    n = S_new.shape[0]
    W = np.minimum(np.abs(S_old), np.abs(S_new))
    thr = rel * max(float(np.max(np.abs(S_new))), 1e-30)
    edges = sorted(((W[i, j], i, j) for i in range(n) for j in range(i + 1, n) if W[i, j] > thr), reverse=True)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    adj: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
    for _, i, j in edges:
        ri, rj = root(i), root(j)
        if ri == rj:
            continue
        parent[ri] = rj
        rel_s = 1.0 if np.real(S_new[i, j] * np.conj(S_old[i, j])) >= 0 else -1.0
        adj[i].append((j, rel_s))
        adj[j].append((i, rel_s))
    s = np.zeros(n)
    for start in range(n):
        if s[start]:
            continue
        comp, s[start], stack = [start], 1.0, [start]
        while stack:
            i = stack.pop()
            for j, r in adj[i]:
                if not s[j]:
                    s[j] = s[i] * r
                    comp.append(j)
                    stack.append(j)
        if np.sum(s[comp]) < 0:          # the sign of a whole group is free: keep most modes as they are
            s[comp] *= -1
    return s


def gauge_aligned(S_old: np.ndarray, S_new: np.ndarray, track=None) -> np.ndarray:
    """S_new in the gauge (mode order, signs) closest to S_old: the candidates are S_new as it is, matched by
    the mode fields (track = (perm, phase) of ModeTracker.match) and by the S-parameters (sign_gauge); the one
    with the smallest max |S_new - S_old| is taken. All of them describe the same solution, a different
    sign or order of a port mode is no change of the model."""
    S_old, S_new = np.asarray(S_old, complex), np.asarray(S_new, complex)
    cands = [S_new]
    if track is not None:
        cands.append(to_previous_gauge(S_new, *track[:2]))
    for base in list(cands):
        s = sign_gauge(S_old, base)
        if np.any(s < 0):
            cands.append(base * np.outer(s, s))
    return min(cands, key=lambda X: float(np.max(np.abs(X - S_old))))


# ------------------------------------------------------------------------------------------------
# port data of a solution (MCP, reports)
# ------------------------------------------------------------------------------------------------
def port_mode_table(store, label: str) -> list[dict]:
    """Per wave port of solution `label`: its modes with label, type, gamma, beta, alpha, Zo, symmetry and the
    closest rectangular guide mode, from portfields_<label>.npz (the frequencies stored there)."""
    import json
    z = store.npz(f"portfields_{label}.npz")
    if z is None:
        return []
    meta = json.loads(str(z["meta"]))
    freqs = np.atleast_1d(np.asarray(z["freq"], float))
    out = []
    for q in meta:
        modes = []
        for r in q["modes"]:
            g = complex(*r["gamma"])
            k = int(r.get("freq_index", 0))
            modes.append({"mode": r["mode"], "freq_index": k, "freq_GHz": float(f"{freqs[k] / 1e9:.6g}") if k < len(freqs) else None,
                          "label": r.get("label") or excitation_label(q["number"], r["mode"], None),
                          "type": r.get("type"), "beta_rad_per_m": float(f"{g.imag:.6g}"),
                          "alpha_Np_per_m": float(f"{g.real:.4g}"), "zo_ohm": float(f"{r.get('zo', float('nan')):.5g}"),
                          "propagating": bool(abs(g.imag) > 10 * abs(g.real)),
                          **({"symmetry": r["symmetry"]} if "symmetry" in r else {}),
                          **({"rect_match": r["rect_match"]} if "rect_match" in r else {})})
        out.append({"name": q["name"], "number": q["number"], **({"axes": q["axes"]} if "axes" in q else {}),
                    "modes": modes})
    return out


def mode_picture(store, label: str, port: int, mode: int, freq_index: int = 0, title: str = "") -> bytes | None:
    """PNG of the transverse E of one port mode on the port face: |E_t| in colour and Re(E_t) as arrows, in the
    principal axes of the face (as listed by port_mode_table)."""
    z = store.npz(f"portfields_{label}.npz")
    key = f"E_{port}_{mode}_{freq_index}"
    if z is None or key not in z:
        return None
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.tri import Triangulation
    xyz = np.asarray(z[f"nodes_{port}"], float)                         # (N, 3)
    tris = np.asarray(z[f"tris_{port}"], int)                           # (M, 3)
    E = np.asarray(z[key], complex)                                     # (N, 3)
    fr = face_frame(xyz.T, tris.T)
    c = fr["center"]
    U, V = (xyz - c) @ fr["u"], (xyz - c) @ fr["v"]
    eu, ev = E @ fr["u"], E @ fr["v"]
    mag = np.sqrt(np.abs(eu) ** 2 + np.abs(ev) ** 2)
    scale = 1e3                                                          # mm
    fig, ax = plt.subplots(figsize=(6, 6 * max(0.35, min(2.5, np.ptp(V) / max(np.ptp(U), 1e-30))) + 0.6), dpi=110)
    tri = Triangulation(U * scale, V * scale, tris)
    pc = ax.tripcolor(tri, mag, shading="gouraud", cmap="viridis")
    step = max(1, len(U) // 400)
    ax.quiver(U[::step] * scale, V[::step] * scale, np.real(eu)[::step], np.real(ev)[::step], color="white",
              scale_units="xy", angles="xy", width=0.004,
              scale=float(np.max(mag) or 1.0) / (0.06 * max(np.ptp(U), np.ptp(V)) * scale))
    ax.set_aspect("equal")
    ax.set_xlabel(f"u = {axis_name(fr['u'])} (mm, from the face centre)")
    ax.set_ylabel(f"v = {axis_name(fr['v'])} (mm)")
    ax.set_title(title or f"Port {port} mode {mode}: |E_t| and Re(E_t)", fontsize=10)
    fig.colorbar(pc, ax=ax, shrink=0.8, label="|E_t| (V/m, 1 W)")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
