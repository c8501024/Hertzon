"""Derivative tuning: mesh morphing, the reduced model of the matrix derivatives, and the whole chain on a
microstrip with an open stub (the stub length moves the notch) against exact solves on the morphed mesh."""
import sys
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import morph as MO  # noqa: E402
from hertzon.solver import tuning as TU  # noqa: E402


def _square_with_hole():
    outer = np.array([[0, 0, 1], [4, 0, 1], [4, 3, 1], [0, 3, 1]], float)
    hole = np.array([[1, 1, 1], [1, 2, 1], [2, 2, 1], [2, 1, 1]], float)
    return MO.Face("P", 0, True, np.array([0, 0, 1.0]), np.array([0, 0, 1.0]), [outer, hole],
                   np.array([[0, 0, 1], [4, 3, 1]], float))


def test_mean_value_coordinates_reproduce_an_affine_motion():
    f = _square_with_hole()
    M = np.array([[0.3, -0.1, 0.0], [0.2, 0.5, 0.0], [0.0, 0.0, 0.0]])
    t = np.array([0.1, -0.2, 0.0])
    motion = lambda X: X @ M.T + t      # noqa: E731
    vel = [motion(P) for P in f.loops]
    rng = np.random.default_rng(3)
    X = np.c_[rng.uniform(0, 4, 200), rng.uniform(0, 3, 200), np.ones(200)]
    X = X[MO.inside_polygon(f, X, 1e-9)]
    X = np.vstack([X, [[2, 0, 1], [4, 1.5, 1], [1.5, 2, 1], [0, 0, 1]]])   # boundary points and a corner
    got = MO.mvc_interpolate(f, X, vel, 1e-9)
    assert np.abs(got - motion(X)).max() < 1e-9


def test_points_on_a_segment_interpolate_its_end_values_only():
    f = _square_with_hole()
    vel = [np.zeros((4, 3)), np.zeros((4, 3))]
    vel[0][1] = [1.0, 0, 0]                 # only the corner (4, 0) moves
    got = MO.mvc_interpolate(f, np.array([[3.0, 0, 1], [0.5, 3, 1]]), vel, 1e-9)
    assert np.allclose(got[0], [0.75, 0, 0]) and np.allclose(got[1], 0)


def test_valid_range_of_a_vertex_moving_into_its_tetrahedron():
    nodes = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])
    tets = np.array([[0], [1], [2], [3]])
    V = np.zeros((3, 4))
    V[2, 3] = 1.0                           # the apex moves along z: volume ~ (1 + dp)
    lo, hi = MO.valid_range(nodes, tets, V, 0.3)
    assert abs(lo + 0.7) < 1e-6 and hi > 1e3


class _LU:
    def __init__(self, A):
        from scipy.sparse.linalg import splu
        self.lu = splu(sp.csc_matrix(A))

    def solve(self, b):
        return self.lu.solve(np.asarray(b, complex))


def test_reduced_model_of_a_parametrized_system():
    """A(p) quadratic in two parameters: exact at p = 0, close to the direct solution nearby (the basis holds
    the solution and its derivatives, so the error grows with the third power of the offset)."""
    rng = np.random.default_rng(7)
    n = 300
    K = sp.diags([2 + rng.random(n), -np.ones(n - 1), -np.ones(n - 1)], [0, -1, 1]).tocsr().astype(complex)
    M = sp.diags(0.4 + 0.1 * rng.random(n)).tocsr().astype(complex)
    A0 = K - 1.1 * M + 0.02j * sp.identity(n, dtype=complex, format="csr")
    def sym(s):                             # complex symmetric perturbations, as the FEM matrix
        D = sp.random(n, n, density=0.01, random_state=s) * 0.05
        return (D + D.T).tocsr()
    Ai = [sym(1) + sp.diags(0.05 * rng.random(n)).tocsr(), sym(2) - 0.3 * M]
    Aii = [sym(3), sym(4)]
    Aij = {(0, 1): sym(5)}
    b = np.zeros((n, 2), complex)
    b[3, 0] = b[n - 5, 1] = 1.0
    terms, W = TU.reduce_system(A0, b, Ai, Aii, Aij, _LU)

    def direct(dp):
        A = A0 + dp[0] * Ai[0] + dp[1] * Ai[1] + 0.5 * (dp[0] ** 2 * Aii[0] + dp[1] ** 2 * Aii[1]) \
            + dp[0] * dp[1] * Aij[(0, 1)]
        return _LU(A).solve(b)

    def reduced(dp):
        fm = dict(terms, Lp=np.zeros((2, W.shape[1])), La=np.zeros((2, W.shape[1])), c0=np.zeros(2))
        A = fm["A0"] + sum(d * fm["Ai"][i] + 0.5 * d * d * fm["Aii"][i] for i, d in enumerate(dp)) \
            + dp[0] * dp[1] * fm["Aij"][0]
        return W @ np.linalg.solve(A, fm["b"])

    x0 = direct([0, 0])
    assert np.abs(reduced([0, 0]) - x0).max() < 1e-10 * np.abs(x0).max()
    errs = []
    for h in (0.01, 0.02, 0.04):
        x = direct([h, -h])
        errs.append(np.abs(reduced([h, -h]) - x).max() / np.abs(x).max())
    assert errs[0] < 1e-4 and errs[1] > 4 * errs[0] and errs[2] > 4 * errs[1]   # third order or better


# --------------------------------------------------------------------------------------------
# the whole chain with EMerge
# --------------------------------------------------------------------------------------------
def _stub_design():
    from hertzon.model.design import Project
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "Microstrip.hzn")
    d = p.designs[0]
    d.variables.update(Wsub="40mm", Ls="12mm", ws="2mm")
    d.add_object("CreateRectangle", "Stub", Position="w/2,Lsub/2-ws/2,h", Axis="Z", XSize="Ls",
                 YSize="ws").material = "vacuum"
    next(b for b in d.boundaries if b.name == "Copper_Trace").objects.append("Stub")
    st = d.setups[0]
    st.props.update(Frequency="3.4GHz", LambdaRefinement="False", CurvedElements="False")
    return p, d, st


def test_tuning_model_matches_exact_solves_on_the_morphed_mesh(tmp_path):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    from hertzon.solver.fast_sweep import _Factor
    from emerge._emerge.mth.csc_submatrix import csc_submatrix
    p, d, st = _stub_design()
    b = B.Bridge(p, d, st, tmp_path)
    freqs = np.array([3.2e9, 3.4e9])
    with em.Simulation("tuning_stub", save_file=False) as m:
        b.prepare(m)
        b.port_map = b.store.read_json("ports.json", None) or getattr(b, "port_map", [])
        f, S_ref, _, _ = B.solve_at(m, list(freqs))
        path = tmp_path / "tuning_Sweep.npz"
        meta = TU.build(b, ["Ls"], freqs, path)
        lo, hi = meta["ranges"][0]
        assert lo < -0.5 and hi > 0.5                     # the stub end may move by half a millimetre
        model = TU.TuningModel(path)
        assert np.abs(model.raw([0.0]) - S_ref).max() < 1e-9   # the solved response, exactly
        # exact solve on the mesh morphed by +0.5 mm
        faces = {"Ls": MO.face_velocities(p, d, "Ls", TU.variable_step(d.variables["Ls"]))}
        fixed, slide = TU.morph_constraints(m)
        diag = float(np.linalg.norm(np.ptp(m.mesh.nodes, axis=1)))
        V = MO.velocity_fields(m.mesh, faces, fixed, 1e-6 * diag, slide_ftags=slide)["Ls"]
        asm = TU.Assembly(m)
        S_tuned = model.raw([0.5])
        Sv, _ = model.sweep_variable(1, 0, [0.0, 0.5], [0.0])  # the response against Ls at one frequency
        assert np.allclose(Sv[0], model.sparams([0.0])[0][1]) and np.allclose(Sv[1], model.sparams([0.5])[0][1])
        for k, fk in enumerate(freqs):
            m.mw._compute_modes(fk)
            A, bb, ids, mats = asm.system(fk, asm.X0 + 0.5 * V)
            x = _Factor(csc_submatrix(A, ids).tocsr()).solve(bb[ids])
            W = x / np.linalg.norm(x, axis=0)       # S of the solutions from the functionals of the ports
            m.mw.frequencies = [fk]
            Lp, La, c0 = TU.s_functionals(m.mw, fk, W, A.shape[0], ids, mats, m.mw._check_port_powers())
            y = np.linalg.norm(x, axis=0)
            S = np.empty((2, 2), complex)
            for j in range(2):
                S[:, j] = Lp[:, j] * y[j]
                S[j, j] = La[j, j] * y[j] + c0[j]
            assert np.abs(S_tuned[k] - S).max() < 0.01
            assert np.abs(S_tuned[k] - S_ref[k]).max() > 0.05        # and the stub length matters
        asm.restore()


def test_substrate_height_stretches_the_lumped_port(tmp_path):
    """The substrate height of the patch antenna moves the substrate faces on the outer boundary (they
    slide along it) and stretches the lumped port across the substrate: the tuned response matches an
    exact solve on the morphed mesh with the stretched port."""
    em = pytest.importorskip("emerge")
    from hertzon.model.design import Project
    from hertzon.solver import bridge as B
    from hertzon.solver.fast_sweep import _Factor
    from emerge._emerge.mth.csc_submatrix import csc_submatrix
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "PatchAntenna.hzn")
    d = p.designs[0]
    st = d.setups[0]
    st.props.update(LambdaRefinement="False", CurvedElements="False")
    b = B.Bridge(p, d, st, tmp_path)
    fk = 2.45e9
    with em.Simulation("tuning_patch", save_file=False) as m:
        b.prepare(m)
        b.port_map = b.store.read_json("ports.json", None) or getattr(b, "port_map", [])
        _, S_ref, _, _ = B.solve_at(m, [fk])
        path = tmp_path / "tuning_h.npz"
        TU.build(b, ["h"], np.array([fk]), path)
        model = TU.TuningModel(path)
        assert np.abs(model.raw([0.0]) - S_ref).max() < 1e-9
        faces = {"h": MO.face_velocities(p, d, "h", TU.variable_step(d.variables["h"]))}
        fixed, slide = TU.morph_constraints(m)
        diag = float(np.linalg.norm(np.ptp(m.mesh.nodes, axis=1)))
        V = MO.velocity_fields(m.mesh, faces, fixed, 1e-6 * diag, slide_ftags=slide)["h"]
        asm = TU.Assembly(m)
        port = asm.lumped.items[0]["bc"]
        m.mw._compute_modes(fk)
        A, bb, ids, mats = asm.system(fk, asm.X0 + 0.2 * V)
        assert abs(port.height - 1.8e-3) < 1e-9                  # the port grew with the substrate
        x = _Factor(csc_submatrix(A, ids).tocsr()).solve(bb[ids])
        y = np.linalg.norm(x, axis=0)
        m.mw.frequencies = [fk]
        _, La, c0 = TU.s_functionals(m.mw, fk, x / y, A.shape[0], ids, mats, m.mw._check_port_powers())
        S = La[0, 0] * y[0] + c0[0]
        asm.restore()
        assert abs(port.height - 1.6e-3) < 1e-9
        S_tuned = model.raw([0.2])[0, 0, 0]
        assert abs(S_tuned - S) < 3e-3
        assert abs(S_tuned - S_ref[0, 0, 0]) > 0.03           # and the height matters


def test_tuned_far_field_of_the_patch_matches_the_solved_one(tmp_path):
    """The far field of a band model: the surface fields of the reduced solution reproduce the solved
    surface fields, so the antenna parameters at the solved design are those of the solve; the substrate
    height changes them."""
    em = pytest.importorskip("emerge")
    from hertzon.model.design import Project
    from hertzon.solver import bridge as B
    from hertzon.solver.farfield_post import mirrored, stratton_chu, symmetry_planes
    from hertzon.solver.ffquant import farfield_quantities
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "PatchAntenna.hzn")
    d = p.designs[0]
    st = d.setups[0]
    st.props.update(LambdaRefinement="False", CurvedElements="False")
    b = B.Bridge(p, d, st, tmp_path)
    F = np.array([2.4e9, 2.5e9])
    th, ph = np.radians(np.arange(0, 181, 5.0)), np.radians([0.0, 90.0, 180.0, 270.0])
    with em.Simulation("tuning_ff", save_file=False) as m:
        b.prepare(m)
        b.port_map = b.store.read_json("ports.json", None) or getattr(b, "port_map", [])
        _, S, _, _ = B.solve_at(m, list(F))
        assert b.save_huygens(list(m.mw.data.field)[-len(F):], "chk")
        path = tmp_path / "tuning_h.npz"
        TU.build(b, ["h"], F, path)
        single = tmp_path / "tuning_single.npz"           # per frequency (a setup without a sweep)
        TU.build(b, ["h"], F[1:], single, band=False)
    huy = b.store.npz("huygens_chk.npz")
    model = TU.TuningModel(path)
    assert model.ff is not None
    k = 1
    E, H = model.surface_fields(k, 0, [0.0])
    assert np.abs(E - huy[f"E_{k}_1"]).max() < 1e-5 * np.abs(huy[f"E_{k}_1"]).max()
    assert np.abs(H - huy[f"H_{k}_1"]).max() < 1e-5 * np.abs(huy[f"H_{k}_1"]).max()
    planes = symmetry_planes(huy)
    v2, w2, Ein, Hin = mirrored(huy["vis"], huy["wns"], [huy[f"E_{k}_1"]], [huy[f"H_{k}_1"]], planes)
    T, P = np.meshgrid(th, ph, indexing="ij")
    Ef = stratton_chu(Ein[0], Hin[0], v2, w2, T.ravel(), P.ravel(), 2 * np.pi * F[k] / 299792458.0)
    ref = farfield_quantities({"theta": th, "phi": ph, "Prad_0_0": huy[f"Prad_{k}_1"],
                               "E_0_0": (Ef * (0.5 ** len(planes)) ** 0.5).reshape((3,) + T.shape)},
                              0, 0, None, pacc=float(1 - np.sum(np.abs(S[k][:, 0]) ** 2)))
    got = model.antenna(k, 0, [0.0], th, ph)
    rg = lambda q: 10 * np.log10(q["RealizedGainTotal"].max())    # noqa: E731
    assert abs(rg(got) - rg(ref)) < 1e-5
    assert abs(got["_eff"][0] - ref["_eff"][0]) < 1e-6
    tuned = model.antenna(k, 0, [0.2], th, ph)
    assert tuned["_eff"][0] - got["_eff"][0] > 0.01               # a thicker substrate radiates better
    # antenna parameters over frequency (the Antenna Parameters report): the model frequencies only
    f, vals = model.antenna_series([F[k], 3.0e9], 0, [0.2], th, ph)
    assert np.allclose(f, [F[k]])
    assert abs(vals["RadiationEfficiency"][0] - tuned["_eff"][0]) < 1e-12
    assert abs(vals["PeakRealizedGain"][0] - tuned["RealizedGainTotal"].max()) < 1e-12
    assert model.has_farfield(k, th, ph) and not model.has_farfield(0, th, ph)
    # a model of one frequency has its own far field basis: the same far field
    one = TU.TuningModel(single)
    assert one.ff is not None and "E_0" in one.ff
    got1 = one.antenna(0, 0, [0.0], th, ph)
    assert abs(rg(got1) - rg(ref)) < 1e-4
    t1 = one.antenna(0, 0, [0.2], th, ph)
    assert abs(t1["_eff"][0] - tuned["_eff"][0]) < 2e-3


def test_band_model_matches_the_model_of_every_frequency(tmp_path):
    """One basis for the band (few factorizations, boundary terms per frequency) gives the response of the
    reduction of every frequency on its own."""
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    p, d, st = _stub_design()
    b = B.Bridge(p, d, st, tmp_path)
    freqs = np.linspace(2.8e9, 4.0e9, 11)
    with em.Simulation("tuning_band", save_file=False) as m:
        b.prepare(m)
        b.port_map = b.store.read_json("ports.json", None) or getattr(b, "port_map", [])
        B.solve_at(m, [3.4e9])
        TU.build(b, ["Ls"], freqs, tmp_path / "band.npz")
        TU.build(b, ["Ls"], freqs, tmp_path / "each.npz", band=False)
        TU.build(b, ["Ls"], freqs[::2], tmp_path / "grid.npz", grid=freqs)     # as for an interpolating sweep
    band, each = TU.TuningModel(tmp_path / "band.npz"), TU.TuningModel(tmp_path / "each.npz")
    grid = TU.TuningModel(tmp_path / "grid.npz")
    assert grid.stack_out is not None                                      # evaluated on the grid, no fit
    for dp in ([0.0], [0.7], [-0.7]):
        assert np.abs(band.raw(dp) - each.raw(dp)).max() < 1e-4
        assert np.abs(grid.sparams(dp)[0] - each.sparams(dp)[0]).max() < 1e-3
    assert np.abs(each.raw([0.7]) - each.raw([0.0])).max() > 0.05


def test_perturbed_face_loops_are_aligned_to_the_nominal_points():
    """gmsh may start or orient the boundary loop of a face differently in a perturbed build: the points
    are matched to the nominal ones (the side face of the GCPW substrate gave the bottom corners the
    velocity of the top ones)."""
    P = np.array([[0, 0, 0], [0, 0, 1], [0, 2, 1], [0, 2, 0]], float)
    Q = (P + [0, 0, 0.001] * (P[:, 2:] > 0))[[2, 1, 0, 3]]          # another start, reversed
    got = MO._align_loop(P, Q)
    assert np.abs(got - P).max() <= 0.001 + 1e-12 and np.allclose(got[[0, 3]], P[[0, 3]])
    assert MO._align_loop(P, Q[:3]) is None


def _gcpw_model(tmp_path, var, freqs):
    em = pytest.importorskip("emerge")
    from hertzon.model.design import Project
    from hertzon.solver import bridge as B
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "GroundedCPW.hzn")
    d = p.designs[0]
    b = B.Bridge(p, d, d.setups[0], tmp_path)
    m = em.Simulation(f"tuning_gcpw_{var}", save_file=False).__enter__()
    b.prepare(m)
    b.port_map = b.store.read_json("ports.json", None) or getattr(b, "port_map", [])
    _, S_ref, _, _ = B.solve_at(m, list(freqs))
    path = tmp_path / f"tuning_{var}.npz"
    TU.build(b, [var], freqs, path)
    faces = {var: MO.face_velocities(p, d, var, TU.variable_step(d.variables[var]))}
    fixed, slide = TU.morph_constraints(m)
    diag = float(np.linalg.norm(np.ptp(m.mesh.nodes, axis=1)))
    V = MO.velocity_fields(m.mesh, faces, fixed, 1e-6 * diag, slide_ftags=slide)[var]
    return m, TU.TuningModel(path), S_ref, V


def _exact_on_morphed_mesh(m, asm, freqs, X, key):
    from hertzon.solver.fast_sweep import _Factor
    from emerge._emerge.mth.csc_submatrix import csc_submatrix
    out, z0 = [], None
    for fk in freqs:
        A, bb, ids, mats = asm.system(fk, X, key=key)
        x = _Factor(csc_submatrix(A, ids).tocsr()).solve(bb[ids])
        y = np.linalg.norm(x, axis=0)
        Lp, La, c0 = TU.s_functionals(m.mw, fk, x / y, A.shape[0], ids, mats, TU._port_powers(m.mw, fk))
        S = np.empty((2, 2), complex)
        for j in range(2):
            S[:, j] = Lp[:, j] * y[j]
            S[j, j] = La[j, j] * y[j] + c0[j]
        out.append(S)
        if z0 is None:
            k0 = 2 * np.pi * fk / 299792458
            z0 = np.array([complex(p.portZ0(k0)) for p, _, _ in m.mw.bc.iter_port_modes()])
    return np.array(out), z0


def test_trace_width_at_wave_ports_solves_the_port_modes_of_every_state(tmp_path):
    """The trace width of the grounded CPW changes the cross-section of both wave ports: the port faces
    deform with the mesh and the port modes are solved on the morphed faces. The tuned response and the
    tuned port impedance match an exact solve on the morphed mesh with its own port modes."""
    freqs = np.array([5e9, 10e9])
    m, model, S_ref, V = _gcpw_model(tmp_path, "w", freqs)
    try:
        assert np.abs(model.raw([0.0]) - S_ref).max() < 1e-9
        asm = TU.Assembly(m)
        dp = 0.1                                                    # w 0.8 -> 0.9 mm
        S, z0 = _exact_on_morphed_mesh(m, asm, freqs, asm.X0 + dp * V, ("exact",))
        asm.restore()
        assert np.abs(model.raw([dp]) - S).max() < 2e-3
        _, z_tuned = model.port_constants([dp])
        assert np.abs(z_tuned - z0).max() < 0.01 * abs(z0[0])
        assert abs(z0[0] - model.z0[0]) > 2.0                      # the wider trace lowers Zo by ~3 ohm
        assert np.abs(model.sparams([dp])[1] - z_tuned).max() < 1e-9  # without renormalization: Zo of the state
    finally:
        m.__exit__(None, None, None)


def test_line_length_moves_the_wave_port(tmp_path):
    """L moves the second wave port along the line (the end walls of the box move with it): the
    transmission phase follows beta * dL."""
    freqs = np.array([10e9])
    m, model, S_ref, V = _gcpw_model(tmp_path, "L", freqs)
    try:
        asm = TU.Assembly(m)
        S, _ = _exact_on_morphed_mesh(m, asm, freqs, asm.X0 + 0.5 * V, ("exact",))
        asm.restore()
        assert np.abs(model.raw([0.5]) - S).max() < 1e-3
        dphi = np.angle(model.raw([0.5])[0, 1, 0] / model.raw([-0.5])[0, 1, 0])
        assert abs(-dphi - model.beta[0, 0].real * 1e-3) < 0.01      # 1 mm more line
    finally:
        m.__exit__(None, None, None)


def test_a_wider_substrate_leaves_the_patch_and_the_port_in_place(tmp_path):
    """The substrate width changes the shape of the top and bottom faces of the substrate. The patch and
    the lumped port lie on these faces and do not depend on the width: they must stay where they are, only
    the mesh between them and the substrate edge stretches (the faces were stretched as a whole before,
    so the patch got wider with the substrate)."""
    em = pytest.importorskip("emerge")
    from hertzon.model.design import Project
    from hertzon.solver import bridge as B
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "PatchAntenna.hzn")
    d = p.designs[0]
    st = d.setups[0]
    st.props.update(LambdaRefinement="False", CurvedElements="False")
    b = B.Bridge(p, d, st, tmp_path)
    with em.Simulation("tuning_patch_ws", save_file=False) as m:
        b.prepare(m)
        faces = {v: MO.face_velocities(p, d, v, TU.variable_step(d.variables[v])) for v in ("Ws", "Ls")}
        fixed, slide = TU.morph_constraints(m)
        diag = float(np.linalg.norm(np.ptp(m.mesh.nodes, axis=1)))
        V = MO.velocity_fields(m.mesh, faces, fixed, 1e-6 * diag, slide_ftags=slide)
        X = m.mesh.nodes * 1e3                                    # mm
        top = np.abs(X[2] - 1.6) < 1e-6
        patch = top & (np.abs(X[0]) <= 37.3 / 2 + 1e-6) & (np.abs(X[1]) <= 28.5 / 2 + 1e-6)
        port = (np.abs(X[1] + 30) < 1e-6) & (np.abs(X[0]) <= 1.5 + 1e-6) & (X[2] > -1e-6) & (X[2] < 1.6 + 1e-6)
        edge = (np.abs(np.abs(X[0]) - 30) < 1e-6) & (X[2] <= 1.6 + 1e-6)
        assert patch.sum() > 20 and port.sum() > 4 and edge.sum() > 4
        Ws, Ls = V["Ws"] * 1e3, V["Ls"] * 1e3                     # mm per mm
        # the patch outline stays (nodes inside the patch may slide within it)
        side = patch & (np.abs(np.abs(X[0]) - 37.3 / 2) < 1e-6)
        assert side.sum() > 2 and np.abs(Ws[0, side]).max() < 1e-6   # was 0.31 (it may slide along it)
        assert np.abs(Ws[2, patch]).max() < 1e-6
        ends = port & (np.abs(np.abs(X[0]) - 1.5) < 1e-6)                 # the sides of the port
        assert np.abs(Ws[0, ends]).max() < 1e-6 and np.abs(Ws[[1, 2]][:, port]).max() < 1e-6   # was 0.025
        assert np.allclose(Ws[0, edge] * np.sign(X[0, edge]), 0.5)  # the substrate edges move by half
        far = patch & (np.abs(X[1] - 28.5 / 2) < 1e-6)                # the far edge of the patch
        assert far.sum() > 2 and np.abs(Ls[1, far]).max() < 1e-6
        assert np.allclose(Ls[1, port], -0.5) and np.abs(Ls[[0, 2]][:, port]).max() < 1e-6
