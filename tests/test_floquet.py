"""Periodic unit cells: Primary / Secondary boundaries and Floquet ports, checked against the plane wave
transmission through a dielectric slab (exact solution)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import periodic as PER  # noqa: E402
from hertzon.solver.results import port_entries  # noqa: E402

C0 = 299792458.0


def slab(f: float, er: float, d: float, theta_deg: float, pol: str) -> tuple[complex, complex]:
    """S11 and S21 of a lossless slab (thickness d) in free space for a plane wave at theta, referred to
    the slab faces."""
    k0, t = 2 * np.pi * f / C0, np.radians(theta_deg)
    kz0, kz1 = k0 * np.cos(t), k0 * np.sqrt(er - np.sin(t) ** 2)
    z = kz0 / kz1 if pol == "TE" else kz1 / (er * kz0)       # wave impedance of the slab / free space
    b = kz1 * d
    A, B, C = np.cos(b), 1j * z * np.sin(b), 1j * np.sin(b) / z
    den = 2 * A + B + C
    return (B - C) / den, 2 / den


def test_scan_from_phases():
    a = 0.01
    f = 10e9
    k0 = 2 * np.pi * f / C0
    th, ph = 30.0, 60.0
    u = PER.scan_direction(th, ph)
    lat = [np.array([a, 0, 0]), np.array([0, a, 0])]
    delays = [np.degrees(k0 * u @ d) for d in lat]
    ut, t2, p2 = PER.scan_from_phases(lat, delays, f)
    assert np.isclose(t2, th) and np.isclose(p2, ph)
    assert np.allclose(ut[:2], u[:2])
    with pytest.raises(RuntimeError):
        PER.scan_from_phases(lat[:1], [np.degrees(1.5 * k0 * a)], f)       # faster than light


def test_pair_faces():
    f1 = [(1, np.array([-5.0, 0, 5]), 10.0), (2, np.array([-5.0, 0, 15]), 20.0)]
    f2 = [(7, np.array([5.0, 0, 15]), 20.0), (8, np.array([5.0, 0, 5]), 10.0)]
    dv = PER.lattice_vector(f1, f2)
    assert np.allclose(dv, [10, 0, 0])
    assert PER.pair_faces(f1, f2, dv, "S") == [(1, 8), (2, 7)]
    f2[0] = (7, np.array([5.0, 1, 15]), 20.0)
    with pytest.raises(RuntimeError, match="translated copies"):
        PER.pair_faces(f1, f2, dv, "S")


def test_port_entries():
    pm = [{"name": "FP2", "number": 2, "kind": "Floquet Port", "modes": 2, "mode_names": ["TE00", "TM00"]},
          {"name": "P1", "number": 1, "kind": "Wave Port"}]
    assert [q["label"] for q in port_entries(pm)] == ["P1", "FP2:TE00", "FP2:TM00"]


# ---- with EMerge ---------------------------------------------------------------------------------
def _cell_design(theta="0deg", phi="0deg", modes=1, delay=None, solver="Auto"):
    """10 mm x 10 mm cell, 40 mm high, an er 4 slab from z = 15 to 20 mm, Floquet ports at the bottom (1)
    and the top (2), periodic in x and y."""
    from hertzon.kernel import occ as K
    from hertzon.model.design import Assignment, FaceRef, Project, Setup
    from hertzon.model.facetrack import track_faces
    from hertzon.model.materials import Material
    p = Project()
    p.materials.append(Material("er4", permittivity="4"))
    d = p.designs[0]
    d.add_object("CreateBox", "Cell", Position="-5mm,-5mm,0", XSize="10mm", YSize="10mm", ZSize="40mm").material = "vacuum"
    d.add_object("CreateBox", "Slab", Position="-5mm,-5mm,15mm", XSize="10mm", YSize="10mm", ZSize="5mm").material = "er4"
    K.ensure_gmsh("floquet")
    t = K.tessellate(K.build_design(d, d.evaluator(p)))

    def faces(ax, s):
        out = []
        for body in ("Cell", "Slab"):
            n = np.asarray(t[body].face_normal)
            out += [FaceRef(body, i) for i in range(len(n)) if n[i][ax] * s > 0.99]
        return out
    for ax, name in ((0, "X"), (1, "Y")):
        d.boundaries.append(Assignment(f"Primary{name}", "Primary", {"UVector": ""}, faces=faces(ax, -1)))
        props = {"Primary": f"Primary{name}", "UVector": ""}
        if delay is None:
            props.update(PhaseMode="Scan Angles", ScanTheta=theta, ScanPhi=phi, PhaseDelay="0deg")
        else:
            props.update(PhaseMode="Input Phase Delay", PhaseDelay=delay[ax])
        d.boundaries.append(Assignment(f"Secondary{name}", "Secondary", props, faces=faces(ax, 1)))
    for k, s in enumerate((-1, 1)):
        cf = [FaceRef("Cell", i) for i, n in enumerate(t["Cell"].face_normal) if n[2] * s > 0.99]
        d.excitations.append(Assignment(f"FP{k + 1}", "Floquet Port", {
            "PortNumber": str(k + 1), "NumberOfModes": str(modes), "Permittivity": "1"}, faces=cf))
    track_faces(d, t)
    st = Setup("Setup1")
    st.props.update(Frequency="10GHz", MaximumPasses="8", MaximumDeltaS="0.002", MinimumPasses="3",
                    CurvedElements="False", Solver=solver)
    d.setups.append(st)
    return p, d, st


def _solve(tmp_path, **kw):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    from hertzon.solver.results import ResultStore
    p, d, st = _cell_design(**kw)
    b = B.Bridge(p, d, st, tmp_path)
    with em.Simulation("floquet", save_file=False) as m:
        b.prepare(m)
        b.run_adaptive()
    return ResultStore(tmp_path).sparams("LastAdaptive")


def _check(S, theta, pol, i, j):
    """|S| of the port pair (i, j) (indices into S) against the slab, and a lossless 2-port."""
    s11, s21 = slab(10e9, 4.0, 5e-3, theta, pol)
    sub = S[np.ix_([i, j], [i, j])]
    assert np.abs(np.abs(sub) - np.abs([[s11, s21], [s21, s11]])).max() < 5e-3
    assert np.abs(sub.conj().T @ sub - np.eye(2)).max() < 5e-3


def test_slab_normal_incidence_two_modes(tmp_path):
    r = _solve(tmp_path, modes=2)
    S = r["S"][-1]
    assert S.shape == (4, 4)                      # FP1:TE00, FP1:TM00, FP2:TE00, FP2:TM00
    _check(S, 0.0, "TE", 0, 2)
    _check(S, 0.0, "TM", 1, 3)
    assert abs(S[0, 1]) < 1e-2 and abs(S[0, 3]) < 1e-2      # no polarization conversion


def test_slab_oblique_incidence(tmp_path):
    # PARDISO as chosen solver: the periodic matrix is not symmetric, an EMerge whose PARDISO interface solves
    # the transposed system (|S11|^2 + |S21|^2 = 0.69 here) is replaced by SuperLU
    S = _solve(tmp_path, theta="30deg", phi="0deg", modes=1, solver="PARDISO")["S"][-1]
    _check(S, 30.0, "TE", 0, 1)


def test_slab_phase_delay(tmp_path):
    # the phase delays of a 30 degree scan in the xz plane at the solution frequency give the same result
    k0 = 2 * np.pi * 10e9 / C0
    delay = (f"{np.degrees(k0 * 0.5 * 0.01):.10g}deg", "0deg")
    S = _solve(tmp_path, delay=delay, modes=1)["S"][-1]
    _check(S, 30.0, "TE", 0, 1)
