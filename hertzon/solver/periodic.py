"""Periodic unit cells: Primary / Secondary boundaries as an EMerge periodic cell.

Every Secondary boundary is linked to a Primary boundary; its faces are the Primary faces moved by one
lattice vector. EMerge ties the mesh and the field of each face pair together and puts the Floquet phase
exp(-j k0 u.d) between them, where u is the direction of the incident wave (the scan direction) and d
the lattice vector. The scan direction comes from the scan angles of the Secondary boundaries, or from
their phase delays at the solution frequency.
"""
from __future__ import annotations

import math

import numpy as np

C0 = 299792458.0


def lattice_vector(faces1: list[tuple[int, np.ndarray, float]], faces2: list[tuple[int, np.ndarray, float]]) -> np.ndarray:
    """Translation from the Primary faces to the Secondary faces (area weighted centres)."""
    def centre(faces):
        a = np.array([f[2] for f in faces], float)
        c = np.array([f[1] for f in faces], float)
        return (c * a[:, None]).sum(0) / a.sum()
    return centre(faces2) - centre(faces1)


def pair_faces(faces1: list[tuple[int, np.ndarray, float]], faces2: list[tuple[int, np.ndarray, float]],
               dv: np.ndarray, name: str) -> list[tuple[int, int]]:
    """Match every Primary face (tag, centre, area) with the Secondary face at centre + dv of equal area."""
    size = max(float(np.linalg.norm(dv)), max(math.sqrt(f[2]) for f in faces1 + faces2))
    tol = 1e-6 * size
    pairs, used = [], set()
    for t1, c1, a1 in faces1:
        hit = [t2 for t2, c2, a2 in faces2 if t2 not in used and np.linalg.norm(c1 + dv - c2) < tol
               and abs(a1 - a2) <= 1e-6 * max(a1, a2)]
        if not hit:
            raise RuntimeError(f"{name}: the Primary and Secondary faces are not translated copies of each other "
                               f"(no Secondary face found {dv * 1e3} mm from a Primary face). Periodic boundaries "
                               "need identical, parallel faces on both sides of the cell")
        used.add(hit[0])
        pairs.append((t1, hit[0]))
    if len(used) != len(faces2):
        raise RuntimeError(f"{name}: the Secondary boundary has faces without a Primary partner")
    return pairs


def scan_direction(theta_deg: float, phi_deg: float) -> np.ndarray:
    """Unit vector of the incident wave for scan angles theta (from +z) and phi (from +x)."""
    t, p = math.radians(theta_deg), math.radians(phi_deg)
    return np.array([math.sin(t) * math.cos(p), math.sin(t) * math.sin(p), math.cos(t)])


def scan_from_phases(lattice: list[np.ndarray], delays_deg: list[float], freq: float) -> tuple[np.ndarray, float, float]:
    """Transverse scan direction u_t (|u_t| = sin theta) that gives the phase delays at freq.

    The phase between a Primary and its Secondary is k0 u.d, so the delays fix u along the lattice
    vectors (minimum norm solution). Returns u_t and the equivalent scan angles theta, phi in degrees."""
    k0 = 2 * math.pi * freq / C0
    D = np.array(lattice, float)
    psi = np.radians(np.asarray(delays_deg, float))
    u, *_ = np.linalg.lstsq(D, psi / k0, rcond=None)
    s = float(np.linalg.norm(u[:2]))
    if s > 1 + 1e-9:
        raise RuntimeError(f"The phase delays need a transverse wave number of {s:.3g} k0 at {freq / 1e9:g} GHz: "
                           "larger than k0, no propagating plane wave has these delays")
    theta = math.degrees(math.asin(min(1.0, s)))
    phi = math.degrees(math.atan2(u[1], u[0])) if s > 0 else 0.0
    return u, theta, phi


def make_cell(pairs: list[tuple[list[int], list[int], np.ndarray]]):
    """EMerge periodic cell with explicit face pairs (Primary tags, Secondary tags, lattice vector)."""
    import emerge as em
    from emerge._emerge.periodic import PeriodicCell

    class AssignedCell(PeriodicCell):
        """A periodic cell whose face pairs come from the Primary / Secondary assignments."""

        def __init__(self, pairs):
            super().__init__([np.zeros(3) for _ in pairs], [np.asarray(d, float) for _, _, d in pairs])
            self.pairs = pairs

        def cell_data(self):
            for t1, t2, d in self.pairs:
                yield em.FaceSelection(list(t1)), em.FaceSelection(list(t2)), tuple(float(x) for x in d)

    return AssignedCell(pairs)


def pardiso_transposes() -> bool:
    """True when EMerge's PARDISO solver returns the solution of A^T x = b. Its interface hands the arrays
    of the CSC matrix to PARDISO as CSR arrays; that only matters for a matrix that is not symmetric,
    which is the case with periodic boundaries."""
    from .fast_assembly import FACTOR
    seen = dict(FACTOR)                 # the probe is no factorization of the model (memory statistics)
    try:
        from scipy.sparse import csc_matrix
        from emerge._emerge.solver import SolverPardiso
        A = csc_matrix(np.array([[4, 1, 0], [3, 5, 2], [0, 1, 6]], complex))   # structurally symmetric
        b = np.array([[1], [1], [1]], complex)
        s = SolverPardiso("")
        s.initialize()
        x, report = s.solve(A, b, None)
        s.reset()
        return report.exit_code != 0 or not np.allclose(A @ x, b)
    except Exception:  # noqa: BLE001
        return True
    finally:
        FACTOR.clear()
        FACTOR.update(seen)
