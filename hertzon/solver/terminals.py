"""Terminal solution: the terminals of the ports and the step from modal to terminal S-parameters.

A wave port gets one terminal per signal conductor that touches its face; the remaining conductor (the
largest one, or the one named in the port's ``ReferenceConductor``) is the reference. A lumped port is one
terminal. The solver still works with port modes (as many modes as terminals). After every solve the
modal S-parameters become terminal S-parameters:

    V_t = N v,  I_t = N^-T i      (N: terminal voltages of the power normalized modes)

so that the power stays the same, and the terminal impedance matrix is Z_t = D Z_n D^T with
Z_n = (1 + S)(1 - S)^-1 of the modal waves and D = N / sqrt(2) (a mode of 1 W has V^2 = 2 Z0, as the
modal analysis defines the port impedance). Lumped ports keep their wave reference: D = sqrt(Z0). The
terminal S-parameters refer to the terminal reference impedances (50 ohm by default).

The terminal S-parameters do not depend on the order, sign or mixing of the modes, so ports with several
modes of the same propagation constant (lines in a homogeneous dielectric) give unique results.

The pure numerics (``terminal_s``, ``mixed_mode``) need no EMerge and are tested on their own.
"""
from __future__ import annotations

import numpy as np

C0 = 299792458.0


# ---- numerics ------------------------------------------------------------------------------------
def s_from_z(Z: np.ndarray, zref) -> np.ndarray:
    """S-parameters (power waves, real reference impedances) of an impedance matrix Z[..., n, n]."""
    Z = np.asarray(Z, complex)
    n = Z.shape[-1]
    zr = np.resize(np.asarray(zref, complex).ravel(), n)
    sq = np.diag(1 / np.sqrt(zr))
    I = np.eye(n)
    out = np.empty_like(Z)
    for k in np.ndindex(Z.shape[:-2]):
        Zn = sq @ Z[k] @ sq
        out[k] = np.linalg.solve((Zn + I).T, (Zn - I).T).T
    return out


def z_from_s(S: np.ndarray, zref) -> np.ndarray:
    """Impedance matrix of S-parameters with the reference impedances zref (one per port)."""
    S = np.asarray(S, complex)
    n = S.shape[-1]
    zr = np.resize(np.asarray(zref, complex).ravel(), n)
    sq = np.diag(np.sqrt(zr))
    I = np.eye(n)
    out = np.empty_like(S)
    for k in np.ndindex(S.shape[:-2]):
        out[k] = sq @ np.linalg.solve((I - S[k]).T, (I + S[k]).T).T @ sq
    return out


def terminal_s(S: np.ndarray, D: np.ndarray, zref) -> tuple[np.ndarray, np.ndarray]:
    """Terminal S-parameters and terminal impedance matrix of the modal S-parameters S[n_modes, n_modes]
    (unit-normalized waves) with the mode-to-terminal matrix D[n_terminals, n_modes]."""
    S = np.asarray(S, complex)
    M = S.shape[-1]
    I = np.eye(M)
    Zn = np.linalg.solve((I - S).T, (I + S).T).T
    Zt = D @ Zn @ D.T
    return s_from_z(Zt, zref), Zt


def mixed_mode_matrix(n: int, pairs: list[tuple[int, int]]) -> tuple[np.ndarray, list[tuple[str, int]]]:
    """Voltage transformation of the terminals 0..n-1 to differential/common pairs (pairs of 0-based
    terminal indices) and the single-ended terminals left over: V_mm = M V, I_mm = M^-T I (same power).
    Differential voltage V_p - V_n, common voltage (V_p + V_n) / 2. Returns M and the label of every row:
    ("Diff", k), ("Comm", k) for pair k (0-based) or ("T", terminal)."""
    rows, labels = [], []
    used = set()
    for k, (p, q) in enumerate(pairs):
        if p == q or not (0 <= p < n and 0 <= q < n) or p in used or q in used:
            raise ValueError(f"Differential pair {k + 1}: invalid or repeated terminals {p + 1}, {q + 1}")
        used |= {p, q}
        d = np.zeros(n)
        d[p], d[q] = 1.0, -1.0
        c = np.zeros(n)
        c[p] = c[q] = 0.5
        rows += [d, c]
        labels += [("Diff", k), ("Comm", k)]
    for t in range(n):
        if t not in used:
            e = np.zeros(n)
            e[t] = 1.0
            rows.append(e)
            labels.append(("T", t))
    return np.array(rows), labels


def mixed_mode(S: np.ndarray, zref, pairs: list[tuple[int, int]], zdiff, zcomm) -> tuple[np.ndarray, np.ndarray, list]:
    """Mixed-mode S-parameters S_mm[nf, n, n] of terminal S-parameters S[nf, n, n] with references zref.
    zdiff / zcomm: reference impedance of each pair's differential and common mode. Returns
    (S_mm, references, labels) in the row order of ``mixed_mode_matrix``."""
    S = np.asarray(S, complex)
    n = S.shape[-1]
    zr = np.resize(np.asarray(zref, complex).ravel(), n)
    M, labels = mixed_mode_matrix(n, pairs)
    Z = z_from_s(S, zr)
    Zmm = M @ Z @ M.T
    refs = []
    for kind, k in labels:
        refs.append(np.resize(zdiff, len(pairs))[k] if kind == "Diff"
                    else np.resize(zcomm, len(pairs))[k] if kind == "Comm" else zr[k])
    refs = np.asarray(refs, complex)
    return s_from_z(Zmm, refs), refs, labels


# ---- conductors of a port face (gmsh geometry of the solver model) -------------------------------
def _curve_points(c: int, n: int = 97) -> np.ndarray:
    import gmsh
    lo, hi = gmsh.model.getParametrizationBounds(1, c)
    t = np.linspace(lo[0], hi[0], n)
    return np.asarray(gmsh.model.getValue(1, c, t), float).reshape(-1, 3).T


def _curve_length(c: int) -> float:
    p = _curve_points(c, 33)
    return float(np.sum(np.linalg.norm(np.diff(p, axis=1), axis=0)))


def port_conductors(face_tags: list[int], conductor_faces: set[int]) -> list[dict]:
    """Groups of connected conductor curves on a port face: the curves of the face (boundary and embedded
    ones, e.g. the end of a zero-thickness trace) that touch a conductor face. Each group: curves, sample
    points (3, n) and total length."""
    import gmsh
    curves: set[int] = set()
    for f in face_tags:
        curves |= {abs(t) for d, t in gmsh.model.getBoundary([(2, f)], combined=False, oriented=False) if d == 1}
        try:
            curves |= {t for d, t in gmsh.model.mesh.getEmbedded(2, f) if d == 1}
        except Exception:  # noqa: BLE001
            pass
    cond = []
    for c in sorted(curves):
        up, _ = gmsh.model.getAdjacencies(1, c)
        if any(int(s) in conductor_faces for s in up):
            cond.append(c)
    # connected groups through shared end points
    ends = {c: {abs(t) for d, t in gmsh.model.getBoundary([(1, c)], combined=False, oriented=False)} for c in cond}
    groups: list[set[int]] = []
    for c in cond:
        hit = [g for g in groups if any(ends[c] & ends[o] for o in g)]
        merged = {c}.union(*hit) if hit else {c}
        groups = [g for g in groups if g not in hit] + [merged]
    out = []
    for g in groups:
        pts = np.hstack([_curve_points(c) for c in sorted(g)])
        out.append({"curves": sorted(g), "points": pts, "length": sum(_curve_length(c) for c in g)})
    out.sort(key=lambda d: tuple(np.round(d["points"].mean(axis=1), 9)))
    return out


def voltage_line(term_pts: np.ndarray, ref_pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shortest straight line from a terminal conductor to the reference conductor."""
    d = np.linalg.norm(term_pts[:, :, None] - ref_pts[:, None, :], axis=0)
    i, j = np.unravel_index(np.argmin(d), d.shape)
    return term_pts[:, i].copy(), ref_pts[:, j].copy()


def line_voltage(efunc, p1: np.ndarray, p2: np.ndarray, n: int = 101) -> complex:
    """V = integral of E . dl from p1 (terminal) to p2 (reference): the terminal's voltage."""
    t = (np.arange(n) + 0.5) / n
    pts = p1[:, None] + (p2 - p1)[:, None] * t[None, :]
    E = np.asarray(efunc(pts[0], pts[1], pts[2]))
    return complex(np.sum(E.T @ ((p2 - p1) / n)))


# ---- the terminals of a design in the solver -----------------------------------------------------
class Terminals:
    """Terminal data of the ports of a solve: built by the bridge after the excitations are assigned."""

    def __init__(self):
        self.ports: list[dict] = []        # per port (by port number): kind, bc, terminals, lines, zref
        self.records: dict[float, tuple[np.ndarray, np.ndarray]] = {}     # f -> (gamma per mode, Zo per terminal)

    @property
    def names(self) -> list[str]:
        return [t for p in self.ports for t in p["terminals"]]

    @property
    def zref(self) -> np.ndarray:
        return np.array([z for p in self.ports for z in p["zref"]], complex)

    def info(self) -> list[dict]:
        return [{"port": p["name"], "number": p["number"], "terminals": p["terminals"],
                 "reference": p.get("reference", ""), "zref": [float(np.real(z)) for z in p["zref"]]}
                for p in self.ports]

    def _blocks(self, k0: float, portnumbers: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Mode-to-terminal matrix D (all ports), the modal betas and the terminal Zo at k0. ``portnumbers``:
        EMerge's S-matrix labels (1, 2 or 1.1, 1.2 for ports with several modes)."""
        cols = []
        for pn in portnumbers:
            s = f"{pn}"
            port, mode = (int(s.split(".")[0]), int(s.split(".")[1])) if "." in s and not float(pn).is_integer() \
                else (int(float(pn)), 1)
            cols.append((port, mode))
        T = sum(len(p["terminals"]) for p in self.ports)
        D = np.zeros((T, len(cols)), complex)
        beta = np.zeros(len(cols), complex)
        row = 0
        rows_of = {}
        for p in self.ports:
            rows_of[p["number"]] = row
            row += len(p["terminals"])
        cache = {}
        for j, (pn, mode) in enumerate(cols):
            p = next((q for q in self.ports if q["number"] == pn), None)
            if p is None:
                continue
            r0 = rows_of[pn]
            if p["kind"] == "lumped":
                D[r0, j] = np.sqrt(complex(p["z0"]))
                beta[j] = k0
                continue
            if pn not in cache:
                cache[pn] = p["bc"].get_modes(k0)
            modes = cache[pn]
            if mode - 1 >= len(modes):
                continue
            md = modes[mode - 1]
            beta[j] = complex(md.beta) * k0 / md.k0 if md.modetype == "TEM" else complex(md.beta)
            if len(p["lines"]) == 1 and np.real(md.Z0) > 0:
                # one signal conductor: the impedance of the mode as the port's impedance definition gives
                # it (Zpv, Zpi, Zvi), so a terminal port agrees with the same modal port renormalized; the
                # voltage line only gives the polarity
                v = line_voltage(md.E_function, *p["lines"][0])
                D[r0, j] = (1.0 if np.real(v) >= 0 else -1.0) * np.sqrt(np.real(md.Z0))
                continue
            for t, (a, b) in enumerate(p["lines"]):
                D[r0 + t, j] = line_voltage(md.E_function, a, b) / np.sqrt(2)
        return D, beta, cols

    def transform(self, freqs, S, portnumbers: list, deembed: dict | None = None) -> np.ndarray:
        """Terminal S-parameters [nf, T, T] of modal S-parameters [nf, M, M]; records gamma and Zo."""
        S = np.asarray(S, complex)
        out = np.zeros((len(S), len(self.names), len(self.names)), complex)
        zr = self.zref
        for k, f in enumerate(np.atleast_1d(freqs)):
            k0 = 2 * np.pi * float(f) / C0
            D, beta, cols = self._blocks(k0, portnumbers)
            Sk = S[k]
            if deembed:
                dist = np.array([deembed.get(pn, 0.0) for pn, _ in cols])
                if np.any(dist):
                    ph = np.exp(1j * beta * dist)
                    Sk = Sk * ph[:, None] * ph[None, :]
            out[k], _ = terminal_s(Sk, D, zr)
            zc = np.real(np.diag(D @ D.T))
            self.records[float(f)] = (1j * beta, zc)
        return out


def interp_records(records: dict, freqs) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Gamma and Zo at the frequencies freqs, interpolated linearly between the solved frequencies."""
    if not records:
        return None, None
    fs = np.array(sorted(records))
    n_g = max(len(records[f][0]) for f in fs)
    n_z = max(len(records[f][1]) for f in fs)
    G = np.array([np.resize(np.asarray(records[f][0], complex), n_g) for f in fs])
    Z = np.array([np.resize(np.asarray(records[f][1], complex), n_z) for f in fs])
    x = np.asarray(freqs, float).ravel()

    def lin(A):
        if len(fs) == 1:
            return np.repeat(A, len(x), axis=0)
        return np.array([np.interp(x, fs, A[:, i].real) + 1j * np.interp(x, fs, A[:, i].imag)
                         for i in range(A.shape[1])]).T.reshape(len(x), A.shape[1])
    return lin(G), lin(Z)
