"""Fast unit tests (no FEM solve). Run: python -m pytest tests"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model.design import Project, Command  # noqa: E402
from hertzon.model.expr import Evaluator, ExprError  # noqa: E402
from hertzon.model.validate import validate  # noqa: E402
from hertzon.kernel import occ as K  # noqa: E402
from hertzon.solver import bridge as B  # noqa: E402
from hertzon.solver.script import design_to_script  # noqa: E402

EX = ROOT / "examples"


def test_expressions_and_units():
    ev = Evaluator({"w": "2mm", "h": "w/2"}, "mm")
    assert ev.length("w+1mm") == pytest.approx(3e-3)
    assert ev.length("3") == pytest.approx(3e-3)       # bare numbers are model units
    assert ev.length("h") == pytest.approx(1e-3)
    assert ev.freq("2.45") == pytest.approx(2.45e9)
    assert ev.freq("100MHz") == pytest.approx(1e8)
    assert ev.angle("90") == pytest.approx(np.pi / 2)
    assert ev.point("1,2,w") == pytest.approx((1e-3, 2e-3, 2e-3))
    with pytest.raises(ExprError):
        ev.length("undefined_var")


def test_project_roundtrip(tmp_path):
    p = Project.load(EX / "Microstrip.hzn")
    out = tmp_path / "copy.hzn"
    p.save(out)
    q = Project.load(out)
    assert json.dumps(p.to_dict(), sort_keys=True) == json.dumps(q.to_dict(), sort_keys=True)


@pytest.mark.parametrize("name", ["WaveguidePost", "Microstrip", "PatchAntenna"])
def test_examples_build_and_validate(name):
    p = Project.load(EX / f"{name}.hzn")
    d = p.designs[0]
    K.ensure_gmsh("test_" + name)
    ctx = K.build_design(d, d.evaluator(p))
    assert not ctx.errors
    tess = K.tessellate(ctx)
    assert all(len(t.tris) for t in tess.values())
    checks = validate(p, d, tess, ctx.errors)
    assert not [c for c in checks if c.level == "error"], checks


def test_booleans_keep_top_dimension():
    p = Project()
    d = p.designs[0]
    d.add_object("CreateSphere", CenterPosition="0,0,0", Radius="2")
    d.add_object("CreateCone", CenterPosition="10,0,0", Axis="Z", LowerRadius="2", UpperRadius="1", Height="4")
    d.obj("Cone1").consumed_by = "Sphere1"
    d.obj("Sphere1").history.append(Command.new("Unite", Tools="Cone1", KeepOriginals="False"))
    K.ensure_gmsh("test_bool")
    ctx = K.build_design(d, d.evaluator(p))
    assert {dim for dim, _ in ctx.built["Sphere1"]} == {3}
    assert len(ctx.built["Sphere1"]) == 2


def test_sweep_frequencies():
    ev = Evaluator({}, "mm")
    f = B.sweep_frequencies({"Distribution": "Linear Step", "Start": "1GHz", "Stop": "2GHz", "Step": "0.25GHz"}, ev)
    assert np.allclose(f, [1e9, 1.25e9, 1.5e9, 1.75e9, 2e9])
    f = B.sweep_frequencies({"Distribution": "Linear Count", "Start": "1GHz", "Stop": "2GHz", "Count": "11"}, ev)
    assert len(f) == 11
    f = B.sweep_frequencies({"Distribution": "Log Scale", "Start": "1GHz", "Stop": "100GHz",
                             "SamplesPerDecade": "10"}, ev)
    assert f[0] == pytest.approx(1e9) and f[-1] == pytest.approx(1e11) and len(f) == 21


def test_aligned_delta_ignores_port_sign_flip():
    S = np.array([[0.1 + 0.2j, 0.9j], [0.9j, 0.1 - 0.2j]])
    flip = np.diag([1, -1])
    d, *_ = B.aligned_delta(S, flip @ S @ flip)
    assert d == pytest.approx(0, abs=1e-12)


def test_renormalization_matches_z_matrix():
    S = np.array([[[0.2 + 0.1j, 0.7], [0.7, 0.1 - 0.3j]]])
    ports = [{"number": 1, "renorm_ohm": 75.0}, {"number": 2, "renorm_ohm": 75.0}]
    S2, z = B.postprocess_ports([1e9], S, [50, 50], ports)
    back, _ = B.postprocess_ports([1e9], S2, z, [{"number": 1, "renorm_ohm": 50.0}, {"number": 2, "renorm_ohm": 50.0}])
    assert np.allclose(back, S)
    # one-port check: open-circuit-like load keeps |S|=1
    S1 = np.array([[[1j]]])
    S1r, _ = B.postprocess_ports([1e9], S1, [50], [{"number": 1, "renorm_ohm": 20.0}])
    assert abs(S1r[0, 0, 0]) == pytest.approx(1)


def test_script_export_compiles():
    p = Project.load(EX / "WaveguidePost.hzn")
    src = design_to_script(p, p.designs[0].name)
    compile(src, "exported.py", "exec")
    assert "bridge.prepare(m)" in src


def test_rectangle_sizes_named_by_axis():
    """CreateRectangle takes XSize/YSize/ZSize (the two in the sheet plane); old Width/Height still load."""
    from hertzon.model import schema
    for axis, (a, b) in schema.RECT_SIZES.items():
        old = Command("CreateRectangle", {"Position": "0,0,0", "Axis": axis, "Width": "2", "Height": "5"})
        assert "Width" not in old.props and old.props[a] == "2" and old.props[b] == "5"
        vis = [p.name for p in schema.GEOMETRY["CreateRectangle"] if schema.is_visible(p, old.props)]
        assert [n for n in vis if n.endswith("Size")] == sorted([a, b])
    pr = Project()
    d = pr.designs[0]
    d.add_object("CreateRectangle", name="R", Position="0,0,0", Axis="Y", Width="2", Height="5")
    assert d.obj("R").history[0].props["ZSize"] == "2" and d.obj("R").history[0].props["XSize"] == "5"
    tess = K.tessellate(K.build_design(d, d.evaluator(pr)))
    P = np.asarray(tess["R"].points)
    assert np.ptp(P, axis=0) == pytest.approx([5e-3, 0, 2e-3], abs=1e-9)


def test_thicken_goes_to_positive_side():
    """Thicken sheet with a positive thickness grows to the positive side of the sheet's main axis, however
    the sheet was drawn (a rectangle dragged to -X has the opposite winding); negative thickness, Reverse
    direction and BothSides go to the other or both sides."""
    def zrange(kind, thick="1", both="False", rev="False", **props):
        pr = Project()
        d = pr.designs[0]
        d.add_object(kind, name="S", **props)
        if kind == "CreatePolyline":
            d.obj("S").history.append(Command("Cover", {}))
        d.obj("S").history.append(Command("ThickenSheet", {"Thickness": thick, "BothSides": both,
                                                                   "ReverseDirection": rev}))
        P = np.asarray(K.tessellate(K.build_design(d, d.evaluator(pr)))["S"].points)
        return P.min(axis=0) * 1e3, P.max(axis=0) * 1e3

    for xs, ys in (("2", "3"), ("-2", "3"), ("2", "-3"), ("-2", "-3")):
        lo, hi = zrange("CreateRectangle", Position="0,0,0", Axis="Z", XSize=xs, YSize=ys)
        assert (lo[2], hi[2]) == pytest.approx((0, 1), abs=1e-9)
    lo, hi = zrange("CreateRectangle", Position="0,0,0", Axis="X", YSize="-2", ZSize="3")
    assert (lo[0], hi[0]) == pytest.approx((0, 1), abs=1e-9)
    lo, hi = zrange("CreateRectangle", Position="0,0,0", Axis="Y", ZSize="-2", XSize="3")
    assert (lo[1], hi[1]) == pytest.approx((0, 1), abs=1e-9)
    lo, hi = zrange("CreatePolyline", Points="0,0,0;0,1,0;1,1,0;1,0,0", Closed="True")   # drawn clockwise, covered
    assert (lo[2], hi[2]) == pytest.approx((0, 1), abs=1e-9)
    lo, hi = zrange("CreateRectangle", "-1", Position="0,0,0", Axis="Z", XSize="-2", YSize="3")
    assert (lo[2], hi[2]) == pytest.approx((-1, 0), abs=1e-9)
    lo, hi = zrange("CreateRectangle", "1", "True", Position="0,0,0", Axis="Z", XSize="-2", YSize="3")
    assert (lo[2], hi[2]) == pytest.approx((-1, 1), abs=1e-9)
    lo, hi = zrange("CreateRectangle", rev="True", Position="0,0,0", Axis="Z", XSize="-2", YSize="3")
    assert (lo[2], hi[2]) == pytest.approx((-1, 0), abs=1e-9)



def test_thicken_old_files_keep_direction():
    """Files of version 1 thickened along the face normal, which follows how the sheet was drawn: loading them
    sets Reverse direction where that normal pointed to the negative side, so the sheets keep their shape."""
    from hertzon.model.design import project_from_dict
    pr = Project()
    d = pr.designs[0]
    cases = {"plain": ("CreateRectangle", dict(Position="0,0,0", Axis="Z", XSize="2", YSize="3"), 2, (0, 1)),
             "negx": ("CreateRectangle", dict(Position="0,0,0", Axis="Z", XSize="-2", YSize="3"), 2, (-1, 0)),
             "negxy": ("CreateRectangle", dict(Position="0,0,0", Axis="Z", XSize="-2", YSize="-3"), 2, (0, 1)),
             "yz": ("CreateRectangle", dict(Position="0,0,0", Axis="X", YSize="-2", ZSize="3"), 0, (-1, 0)),
             "cw": ("CreatePolyline", dict(Points="0,0,0;0,1,0;1,1,0;1,0,0", Closed="True"), 2, (-1, 0)),
             "ccw": ("CreatePolyline", dict(Points="0,0,0;1,0,0;1,1,0;0,1,0", Closed="True"), 2, (0, 1))}
    for name, (kind, props, _, _) in cases.items():
        d.add_object(kind, name=name, **props)
        if kind == "CreatePolyline":
            d.obj(name).history.append(Command("Cover", {}))
        d.obj(name).history.append(Command("ThickenSheet", {"Thickness": "1", "BothSides": "False"}))
    old = pr.to_dict()
    old["version"] = 1
    pr = project_from_dict(old)
    d = pr.designs[0]
    tess = K.tessellate(K.build_design(d, d.evaluator(pr)))
    for name, (_, _, axis, span) in cases.items():
        P = np.asarray(tess[name].points) * 1e3
        assert (P[:, axis].min(), P[:, axis].max()) == pytest.approx(span, abs=1e-9), name
    # files of the current version are left as they are
    assert project_from_dict(pr.to_dict()).designs[0].obj("negx").history[-1].props["ReverseDirection"] == "True"
    assert project_from_dict(pr.to_dict()).designs[0].obj("plain").history[-1].props["ReverseDirection"] == "False"

def test_thicken_preview_arrow():
    """The Thicken sheet dialog draws an arrow on each sheet in the direction the kernel thickens to."""
    import types
    import pyvista as pv
    from hertzon.gui import viewer as Vw
    View = type("View", (), {k: Vw.ModelerView.__dict__[k]
                             for k in ("show_thicken_arrows", "clear_thicken_arrows")})
    v = View()
    removed = []
    v.plotter = types.SimpleNamespace(add_mesh=lambda mesh, **kw: mesh,
                                      renderer=types.SimpleNamespace(RemoveActor=lambda a: None))
    top = types.SimpleNamespace(AddActor=lambda a: None, RemoveActor=removed.append)
    v._top_layer = lambda: top
    v.request_render = lambda: None
    v.direction_actors = []
    sheet = pv.Plane(center=(1, 2, 0), direction=(0, 0, -1), i_size=4, j_size=2)   # face normal -Z
    box = pv.Box()
    v.actors = {"S": Vw.ObjActors(mesh=sheet, dim=2), "B": Vw.ObjActors(mesh=box, dim=3)}

    def tips():
        return [np.asarray(m.points)[:, 2].max() - np.asarray(m.points)[:, 2].min() > 0
                and np.sign(np.asarray(m.points)[:, 2].sum()) for m in v.direction_actors]
    v.show_thicken_arrows(["S", "B"])
    assert tips() == [1]
    v.show_thicken_arrows(["S"], reverse=True)
    assert tips() == [-1] and len(removed) == 1
    v.show_thicken_arrows(["S"], both=True)
    assert sorted(tips()) == [-1, 1]
    v.clear_thicken_arrows()
    assert v.direction_actors == [] and len(removed) == 4


def test_antenna_report_expressions():
    """Antenna parameters: dB() of a gain or power is 10 log10, of the axial ratio 20 log10."""
    from hertzon.gui.reports import eval_expr
    data = {"freq": np.array([1.0, 2.0]), "xaxis": "Pass",
            "antenna": {"PeakGain": np.array([2.0, 4.0]), "AxialRatioAtMax": np.array([2.0, 1.0])}}
    assert eval_expr(data, "dB(PeakGain)") == pytest.approx(10 * np.log10([2.0, 4.0]))
    assert eval_expr(data, "dB(AxialRatioAtMax)") == pytest.approx(20 * np.log10([2.0, 1.0]))
    assert eval_expr(data, "PeakGain*2") == pytest.approx([4.0, 8.0])


def test_refinement_method_renamed():
    """Old setups keep their refinement method under the new names, and their solutions stay valid."""
    from hertzon.model.design import Setup
    from hertzon.model import schema
    from hertzon.solver.keys import setup_key
    assert schema.upgrade_values({"RefinementMethod": "Remesh"})["RefinementMethod"] == "Remesh (slow)"
    a, b = Setup("S"), Setup("S")
    a.props["RefinementMethod"] = "Local (Delaunay, experimental)"
    b.props["RefinementMethod"] = "Local (Delaunay)"
    assert setup_key(a) == setup_key(b)
    assert schema.defaults(schema.SETUP)["RefinementMethod"] == "hzAURA (EMerge)"
    # AURA was renamed to hzAURA: old projects open with the new name and keep their solutions
    assert schema.upgrade_values({"RefinementMethod": "AURA (EMerge)"})["RefinementMethod"] == "hzAURA (EMerge)"
    a.props["RefinementMethod"], b.props["RefinementMethod"] = "AURA (EMerge)", "hzAURA (EMerge)"
    assert setup_key(a) == setup_key(b)


def test_far_field_follows_edit_sources(tmp_path):
    """Gain patterns and antenna parameters use the excitation of Edit Sources, not always port 1."""
    import json as _json
    import types
    from hertzon.solver.results import ResultStore
    from hertzon.solver.ffquant import excited_quantities, farfield_quantities
    st = ResultStore(tmp_path)
    ports = [{"name": "P1", "number": 1, "kind": "Lumped Port"}, {"name": "P2", "number": 2, "kind": "Lumped Port"}]
    st.write_json("ports.json", ports)
    S = np.array([[[0.3 + 0.1j, 0.05], [0.05, 0.2j]]])
    st.save_sparams("LastAdaptive", np.array([1e9]), S, np.array([50.0, 50.0]), ports)
    th, ph = np.linspace(0, np.pi, 19), np.linspace(0, 2 * np.pi, 37)
    T, P = np.meshgrid(th, ph, indexing="ij")
    E1 = np.array([np.cos(T), 0 * T, -np.sin(T)]).astype(complex)          # broadside to +x/-x
    E2 = np.array([0 * T, np.cos(T) + 0.5, 0 * T]).astype(complex)
    ff = {"theta": th, "phi": ph, "E_0_1": E1, "E_0_2": E2, "freq_0": np.array([1e9]),
          "Prad_0_1": np.array([0.5]), "Prad_0_2": np.array([0.4])}
    des = types.SimpleNamespace(settings={}, excitations=[],
                                ports=lambda: [types.SimpleNamespace(name="P1"), types.SimpleNamespace(name="P2")])
    q0 = excited_quantities(st, des, ff, "LastAdaptive", 0)
    assert q0["GainTotal"] == pytest.approx(farfield_quantities(ff, 1, 0, pacc=1 - 0.1 - 0.05 ** 2)["GainTotal"])
    des.settings["Sources"] = _json.dumps({"P1": {"mag": 0, "phase": 0}, "P2": {"mag": 1, "phase": 0}})
    q2 = excited_quantities(st, des, ff, "LastAdaptive", 0)
    assert q2["GainTotal"] == pytest.approx(farfield_quantities(ff, 2, 0, pacc=1 - 0.04 - 0.05 ** 2)["GainTotal"])
    des.settings["Sources"] = _json.dumps({"P1": {"mag": 2, "phase": 0}, "P2": {"mag": 2, "phase": 90}})
    q12 = excited_quantities(st, des, ff, "LastAdaptive", 0)
    a = np.sqrt(2) * np.array([1, 1j])
    assert q12["_Pinc"][0] == pytest.approx(4.0)
    assert q12["_Pacc"][0] == pytest.approx(4.0 - np.linalg.norm(S[0] @ a) ** 2)
    assert q12["rETotal"] == pytest.approx(np.sqrt(np.abs(farfield_quantities(
        {**ff, "E_0_9": a[0] * E1 + a[1] * E2, "Prad_0_9": np.array([1.0])}, 9, 0)["rETotal"]) ** 2))


def test_edit_sources_apply_keeps_dialog_open():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    from hertzon.gui.dialogs import EditSourcesDialog
    from hertzon.model import sources as SRC
    app = QApplication.instance() or QApplication([])  # noqa: F841
    proj = Project.load(EX / "PatchAntenna.hzn")
    des = proj.designs[0]
    got = []
    dlg = EditSourcesDialog(None, des, on_apply=lambda s, f: got.append((s, f)))
    name = dlg.names[0]
    dlg.mag[name].setValue(2.5)
    dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Apply).click()
    assert got and got[0][0][name]["mag"] == 2.5 and not dlg.result()
    assert SRC.defaults(des)


def test_fast_assembly_sparse_helpers():
    """The upper triangle for the complex symmetric PARDISO factorization; EMerge's parallel removal of the
    PEC rows and columns (FennisRobert/EMerge#78), which Hertzon's sweeps use too, matches scipy."""
    import scipy.sparse as sp
    from hertzon.solver import fast_assembly as FA
    from emerge._emerge.mth.csc_submatrix import csc_submatrix
    rng = np.random.default_rng(0)
    M = sp.random(300, 300, density=0.05, random_state=1) + 1j * sp.random(300, 300, density=0.05, random_state=2)
    A = (M + M.T + sp.identity(300)).tocsc()
    A.sort_indices()
    ids = np.sort(rng.choice(300, 250, replace=False))
    S0, S1 = A[:, ids][ids, :], csc_submatrix(A, ids)
    S0.sort_indices()
    assert np.array_equal(S0.indptr, S1.indptr) and np.array_equal(S0.indices, S1.indices)
    assert np.array_equal(S0.data, S1.data)
    U = FA.upper_symmetric(A)
    assert U is not None and abs(U - sp.triu(A)).max() == 0
    assert FA.upper_symmetric((A + sp.random(300, 300, density=0.01, random_state=3)).tocsc()) is None


def test_curved_map_for_plots():
    """The solve uses EMerge's curved elements (FennisRobert/EMerge#77); the plots map the same geometry:
    without displacement the map is the straight tetrahedron, with one it moves that edge midpoint."""
    from hertzon.solver import curvilinear as CV
    from emerge._emerge.settings import Settings
    assert Settings().mw_curved_elements is False
    X = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]], float)
    lam = np.vstack([np.eye(4), [[0.5, 0.5, 0, 0]]])
    pos, D = CV.curved_map(X, np.zeros((1, 6, 3)), lam)
    assert np.allclose(pos[0], lam @ X[0]) and np.allclose(D, np.eye(3))
    d = np.zeros((1, 6, 3))
    d[0, 0] = [0, -0.1, 0]                      # edge 0-1 bulges outwards
    pos, D = CV.curved_map(X, d, lam)
    assert np.allclose(pos[0, 4], [0.5, -0.1, 0]) and np.allclose(pos[0, :4], X[0])
    assert np.all(np.linalg.det(D) > 0)


def test_emerge_material_library():
    """EMerge's material library (emsutil) appears next to Hertzon's own one; frequency dependent
    materials keep EMerge's data for the solve."""
    from hertzon.model.materials import EMERGE_DISPERSION, EMERGE_LIBRARY, emerge_library, emerge_object
    lib = emerge_library()
    if not lib:
        pytest.skip("emsutil not installed")
    assert len(lib) > 100
    ro = lib["Rogers RO4350B"]
    assert ro.library == EMERGE_LIBRARY and float(ro.permittivity) == pytest.approx(3.48)
    assert float(ro.loss_tangent) == pytest.approx(0.0037) and ro.emsutil_key == "DIEL_RO4350B"
    assert not {"pec", "copper", "air"} & {n.lower() for n in lib}       # Hertzon's own ones win
    p = Project()
    assert p.material("Rogers RO4350B") is ro and ro in p.all_materials()
    disp = [m for m in lib.values() if m.dispersion == EMERGE_DISPERSION]
    for m in disp:
        assert emerge_object(m.emsutil_key) is not None


def test_finite_conductivity_thickness_without_two_sided():
    """Older files: a thickness meant the two-sided thin conductor; loading keeps that and the result key."""
    from hertzon.model.design import project_from_dict
    from hertzon.solver.keys import model_key
    p = Project.load(EX / "Microstrip.hzn")
    d = p.to_dict()
    for b in d["designs"][0]["boundaries"]:
        b["props"].pop("TwoSided", None)
    old = project_from_dict(json.loads(json.dumps(d)))
    des = old.designs[0]
    cu = next(b for b in des.boundaries if b.kind == "Finite Conductivity")
    assert cu.props["UseThickness"] == "True" and cu.props["TwoSided"] == "True"
    key = model_key(old, des, des.setups[0])
    cu.props.pop("TwoSided")
    assert model_key(old, des, des.setups[0]) == key
    # one-sided with thickness is a different problem
    cu.props.update(TwoSided="False")
    assert model_key(old, des, des.setups[0]) != key


def test_object_colours_in_other_notations_are_read():
    from hertzon.model.materials import parse_color
    from hertzon.model.design import project_from_dict
    assert parse_color("0,128,96") == parse_color("(0 128 96)") == parse_color("#008060") == "#008060"
    assert parse_color("#abc") == "#aabbcc" and parse_color("nonsense") is None
    p = Project.load(EX / next(f.name for f in EX.glob("*.hzn")))
    d = p.to_dict()
    d["designs"][0]["objects"][0]["color"] = "0,128,96"
    d["materials"] = [{"name": "M", "color": "(10 20 30)"}]
    q = project_from_dict(d)
    assert q.designs[0].objects[0].color == "#008060"
    assert q.materials[0].color == "#0a141e"


@pytest.mark.parametrize("boxes", [0, 10])
def test_circles_in_flat_faces_are_drawn_round(boxes):
    # thin ring (rat race): without curvature sizing its circles had a dozen segments and a zigzag outline
    p = Project()
    d = p.designs[0]
    d.add_object("CreateCircle", name="Ring", CenterPosition="0,0,0", Axis="Z", Radius="18")
    d.add_object("CreateCircle", name="Hole", CenterPosition="0,0,0", Axis="Z", Radius="17")
    d.obj("Hole").consumed_by = "Ring"
    d.obj("Ring").history.append(Command.new("Subtract", Tools="Hole", KeepOriginals="False"))
    for i in range(boxes):      # many flat faces and a cylinder: the two pass display mesh
        d.add_object("CreateBox", Position=f"{30 + 3 * i},0,0", XSize="1", YSize="1", ZSize="1")
    if boxes:
        d.add_object("CreateCylinder", CenterPosition="-40,0,0", Axis="Z", Radius="3", Height="2")
    K.ensure_gmsh("test_ring")
    ctx = K.build_design(d, d.evaluator(p))
    tess = K.tessellate(ctx)
    circles = [e for e in tess["Ring"].edges if len(e) > 2]
    assert len(circles) == 2 and all(len(e) >= 60 for e in circles)
    if boxes:
        assert len(tess["Cylinder1"].tris) < 400


def test_results_dir_stays_inside_results_folder():
    from hertzon.solver.results import results_dir
    proj = Path("/work/demo/filter.hzn")
    base = proj.with_suffix(".hznresults").resolve()
    for design, setup in [("..", ".."), (".", ".."), ("", ""), ("../..", "x"), ("a/../..", "...")]:
        d = results_dir(proj, design, setup).resolve()
        assert d.parent.parent == base, (design, setup, d)


def test_worker_routines_are_independent():
    """EMerge 3's AutomaticRoutine is a singleton: the parallel sweep threads must still get routines and
    PARDISO handles of their own (shared ones crashed in MKL now and then)."""
    from hertzon.solver import emerge_patches
    emerge_patches.apply()
    from emerge._emerge import solver as S
    r = S.AutomaticRoutine()
    a, b = r.duplicate(), r.duplicate()
    assert a is not r and b is not a
    for key, s in r.solvers.items():
        assert a.solvers[key] is not s and a.solvers[key] is not b.solvers[key]


def test_subtract_dialog_moves_single_objects():
    """Subtract: the arrow buttons move the selected objects between Blank Parts and Tool Parts."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialogButtonBox
    from hertzon.gui.dialogs import SubtractDialog
    app = QApplication.instance() or QApplication([])  # noqa: F841
    dlg = SubtractDialog(["Cylinder1"], ["Cylinder2", "Cylinder3"])
    ok = dlg.buttons.button(QDialogButtonBox.Ok)
    assert dlg.blank_list.selectionMode() == QAbstractItemView.ExtendedSelection
    assert dlg.tool_list.selectionMode() == QAbstractItemView.ExtendedSelection
    assert not dlg.to_tool.isEnabled() and not dlg.to_blank.isEnabled() and ok.isEnabled()
    dlg.tool_list.item(1).setSelected(True)                     # Cylinder3 <- back to the blank parts
    dlg.to_blank.click()
    assert dlg.blanks() == ["Cylinder1", "Cylinder3"] and dlg.tools() == ["Cylinder2"]
    dlg.blank_list.clearSelection()
    for i in range(2):                                          # both blanks -> tools: no blank part left
        dlg.blank_list.item(i).setSelected(True)
    dlg.to_tool.click()
    assert dlg.blanks() == [] and dlg.tools() == ["Cylinder2", "Cylinder1", "Cylinder3"] and not ok.isEnabled()
    dlg.swap()
    assert dlg.blanks() == ["Cylinder2", "Cylinder1", "Cylinder3"] and dlg.tools() == [] and not ok.isEnabled()


def test_subtract_with_several_blank_parts():
    """Every blank part is cut by all tool parts; the tools are cloned for all but the last blank."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import types
    from PySide6.QtWidgets import QApplication, QDialog
    from hertzon.gui import main_window as MW
    app = QApplication.instance() or QApplication([])  # noqa: F841
    proj = Project()
    des = proj.designs[0]
    for name, x in (("A", 0), ("B", 20), ("T", 5)):
        des.add_object("CreateBox", name, Position=f"{x},0,0", XSize="10", YSize="10", ZSize="10")

    class Dlg:
        def __init__(self, blanks, tools, parent=None):
            self.clone = types.SimpleNamespace(isChecked=lambda: False)

        def exec(self):
            return QDialog.Accepted

        def blanks(self):
            return ["A", "B"]

        def tools(self):
            return ["T"]
    win = types.SimpleNamespace(des=des, doc=types.SimpleNamespace(checkpoint=lambda: None, commit=lambda w: None,
                                                                   select=lambda p: None),
                                selected_objects=lambda n: ["A", "T"], _drop_refs=lambda objs: None)
    old = MW.SubtractDialog
    MW.SubtractDialog = Dlg
    try:
        MW.MainWindow.boolean(win, "Subtract")
    finally:
        MW.SubtractDialog = old
    cuts = {o.name: [c for c in o.history if c.kind == "Subtract"] for o in des.objects}
    assert cuts["A"][0].props["Tools"] == "T_tool" and des.obj("T_tool").consumed_by == "A"
    assert cuts["B"][0].props["Tools"] == "T" and des.obj("T").consumed_by == "B"


def test_projection_keeps_the_model_size():
    """View > Orthographic: switching projection keeps the visible height (the parallel scale matches)."""
    import math
    import types
    from hertzon.gui.viewer import ModelerView
    from vtkmodules.vtkRenderingCore import vtkCamera
    cam = vtkCamera()
    cam.SetPosition(0, 0, 100)
    cam.SetFocalPoint(0, 0, 0)
    cam.SetViewAngle(30)
    got = []
    v = types.SimpleNamespace(plotter=types.SimpleNamespace(camera=cam, render=lambda: None), on_projection=got.append,
                              request_render=lambda: None)
    ModelerView.set_orthographic(v, True)
    assert cam.GetParallelProjection() and got == [True]
    assert cam.GetParallelScale() == pytest.approx(100 * math.tan(math.radians(15)))
    ModelerView.set_orthographic(v, False)
    assert not cam.GetParallelProjection() and got == [True, False]


def test_report_tab_does_not_raise_the_window_minimum_height():
    """Opening a report tab must not raise the main window's minimum height: the trace editor scrolls, so a
    maximized window on a 1080p screen with display scaling keeps its status bar on screen."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QScrollArea
    from hertzon.gui.doc import Document
    from hertzon.gui.reports import ReportWindow
    from hertzon.model.design import Report
    app = QApplication.instance() or QApplication([])  # noqa: F841
    doc = Document(Project.load(EX / "PatchAntenna.hzn"))
    w = ReportWindow(doc, Report("XY Plot 1", "Rectangular Plot", "Setup1 : LastAdaptive",
                                 ["dB(S(1,1))", "dB(S(2,1))"]))
    editor = w.findChild(QScrollArea)
    assert editor is not None and editor.widget().minimumSizeHint().height() > 300
    assert w.minimumSizeHint().height() < 200, w.minimumSizeHint()


def test_radiation_pattern_of_a_sweep_frequency_and_rectangular(tmp_path):
    """Radiation Pattern of a sweep with fields: the frequency is chosen in the report, and the cut can be
    drawn over the angle (rectangular) with markers."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.doc import Document, result_dir
    from hertzon.gui.reports import ReportWindow, ff_solution_list
    from hertzon.model.design import Report
    from hertzon.solver.results import ResultStore
    app = QApplication.instance() or QApplication([])  # noqa: F841
    proj = Project.load(EX / "PatchAntenna.hzn")
    proj.save(tmp_path / "p.hzn")
    doc = Document(proj)
    st = ResultStore(result_dir(doc, "Setup1"))
    ports = [{"name": "1", "number": 1, "kind": "Lumped Port"}]
    st.write_json("ports.json", ports)
    st.save_sparams("Sweep", np.array([1e9, 2e9]), np.zeros((2, 1, 1), complex), np.array([50.0]), ports)
    rng = np.random.default_rng(1)
    n = 40
    vis = rng.normal(size=(3, n)) * 0.01
    wns = rng.normal(size=(3, n)) * 1e-4
    E = (rng.normal(size=(3, n)) + 1j * rng.normal(size=(3, n)))
    H = (rng.normal(size=(3, n)) + 1j * rng.normal(size=(3, n))) / 377
    st.save_npz("huygens_Sweep.npz", vis=vis, wns=wns, stamp=np.array([1]),
                freq_0=np.array([1e9]), E_0_1=E, H_0_1=H, Prad_0_1=np.array([0.5]),
                freq_1=np.array([2e9]), E_1_1=2 * E, H_1_1=2 * H, Prad_1_1=np.array([2.0]))
    assert ff_solution_list(doc) == ["Setup1 : Sweep"]
    rep = Report("Radiation Pattern 1", "Radiation Pattern", "Setup1 : Sweep", ["dB(GainTotal)"])
    w = ReportWindow(doc, rep)
    assert "color:#c0392b" not in w.info.text(), w.info.text()
    assert [w.ff_freq.itemText(i) for i in range(w.ff_freq.count())] == ["1 GHz", "2 GHz"]
    assert "1 GHz" in w.info.text()
    w.ff_freq.setCurrentIndex(1)
    assert rep.props["freq"] == "2000000000" and "2 GHz" in w.info.text()
    assert w._ff()["_Prad"][0] == pytest.approx(2.0)
    # polar plot: markers are angles in deg on the polar axes, the beamwidth rows go to the marker table
    assert w._polar() and w.fig.axes[0].name == "polar"
    x, y = w._trace_xy("dB(GainTotal)")
    w.marker_at("max")
    assert w.markers == [pytest.approx(x[np.argmax(y)])]
    assert w._from_ax(np.radians(350)) == pytest.approx(-10)
    # the Phi cut is labelled over theta -180..180 (as defined on the sphere), the Theta cut over phi 0..360
    fmt = w.fig.axes[0].xaxis.get_major_formatter()
    assert [fmt(np.radians(a)) for a in (0, 90, 180, 270)] == ["0\u00b0", "90\u00b0", "\u00b1180\u00b0", "-90\u00b0"]
    rep.props["cut"] = "Theta"
    w.refresh()
    assert w.fig.axes[0].xaxis.get_major_formatter()(np.radians(270)) == "270\u00b0"
    rep.props["cut"] = "Phi"
    w.refresh()
    rep.props.update(bandwidth="dB(GainTotal)", bw_level="-3")
    w.refresh()
    rows = [w.mtable.item(r, 0).text() for r in range(w.mtable.rowCount())]
    assert rows[0] == "m1" and "BW @ -3 dB" in rows
    w._clear_markers()
    w.ff_plot.setCurrentText("Rectangular")
    assert rep.props["plot"] == "Rectangular" and w._xy_plot()
    ax = w.fig.axes[0]
    assert ax.name == "rectilinear" and ax.get_xlabel() == "Theta [deg]"
    x, y = w._trace_xy("dB(GainTotal)")
    assert x.min() == pytest.approx(-180) and x.max() == pytest.approx(180)
    w.marker_at("max")
    assert len(w.markers) == 1 and w.markers[0] == pytest.approx(x[np.argmax(y)])
    assert w.mtable.isVisibleTo(w) and w.mtable.rowCount() == 1


def test_beamwidth_and_side_lobes():
    from hertzon.gui.reports import beamwidth
    x = np.linspace(-180, 180, 361)                       # Phi cut over the full circle
    y = 10 * np.log10(np.maximum(np.cos(np.radians(x) / 2) ** 4, 1e-9))
    b = beamwidth(x, y, -3)
    half = 2 * np.degrees(np.arccos(0.5 ** 0.25))         # cos^4(t/2) = 1/2
    assert b["width"] == pytest.approx(2 * half, abs=0.5) and b["lo"] == pytest.approx(-half, abs=0.3)
    assert not b["open"] and "sll" not in b
    x = np.linspace(0, 360, 361)                           # Theta cut 0..360 with the beam across 0/360
    b = beamwidth(x, 10 * np.log10(np.maximum(np.cos(np.radians(x) / 2) ** 4, 1e-9)), -3)
    assert b["width"] == pytest.approx(2 * half, abs=0.5) and b["max_at"] == pytest.approx(0)
    assert b["lo"] == pytest.approx(360 - half, abs=0.3) and b["hi"] == pytest.approx(half, abs=0.3)
    x = np.linspace(-90, 90, 181)                          # uniform array: first side lobe -13.26 dB
    u = 4 * np.sin(np.radians(x))
    b = beamwidth(x, 20 * np.log10(np.abs(np.sinc(u)) + 1e-12), -3)
    assert b["sll"] == pytest.approx(-13.26, abs=0.1) and abs(b["sll_at"]) == pytest.approx(21, abs=1)


def test_keyboard_shortcuts_dialog_opens_and_saves(monkeypatch):
    """Tools > Keyboard Shortcuts: the dialog opens, a new key is set on the command and kept in the settings,
    and it is applied again at the next start."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QApplication, QDialog, QKeySequenceEdit, QMainWindow
    from hertzon.gui import main_window as MW
    app = QApplication.instance() or QApplication([])  # noqa: F841
    win = QMainWindow()
    win.settings = QSettings(QSettings.IniFormat, QSettings.UserScope, "hertzon-test", "shortcuts")
    win.settings.clear()
    m = win.menuBar().addMenu("&Tools")
    act = m.addAction("Edit Materials...")

    def accept(dlg):
        for e in dlg.findChildren(QKeySequenceEdit):
            e.setKeySequence(QKeySequence("Ctrl+Shift+M"))
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", accept)
    MW.MainWindow.edit_shortcuts(win)
    assert act.shortcut().toString() == "Ctrl+Shift+M"
    act.setShortcut(QKeySequence())
    MW.MainWindow._apply_shortcuts(win)
    assert act.shortcut().toString() == "Ctrl+Shift+M"
    win.settings.clear()


def test_center_point_arc_polyline():
    """AngularArc segments: start, center and angle; the angle decides the end point (the stored one is a copy)."""
    import gmsh
    p = Project()
    d = p.designs[0]
    d.add_object("CreatePolyline", Points="1,0,0;0,0,0;0,1,0;-2,1,0", SegmentTypes="AngularArc:90:XY,Line")
    d.add_object("CreatePolyline", Points="1,0,0;0,0,0;1,0,0", SegmentTypes="AngularArc:360deg",
                 Closed="True", Covered="True")
    # half circle in the ZX plane with a wrong stored end point, closed by a line
    d.add_object("CreatePolyline", Points="0,0,2;0,0,0;5,5,5", SegmentTypes="AngularArc:-180:ZX",
                 Closed="True", Covered="True")
    K.ensure_gmsh("test_angular_arc")
    ctx = K.build_design(d, d.evaluator(p))
    assert not ctx.errors
    size = lambda name: sum(gmsh.model.occ.getMass(*x) for x in ctx.built[name])  # noqa: E731
    assert size("Polyline1") == pytest.approx((np.pi / 2 + 2) * 1e-3)
    assert size("Polyline2") == pytest.approx(np.pi * 1e-6)
    assert size("Polyline3") == pytest.approx(2 * np.pi * 1e-6)
    lo = gmsh.model.occ.getBoundingBox(*ctx.built["Polyline3"][0])
    assert lo[0] == pytest.approx(-2e-3, abs=1e-6) and lo[3] == pytest.approx(0, abs=1e-6)   # -180 about +Y: -X


def test_center_point_arc_drawing():
    """Draw > Arc > Center Point: start, center, then the end angle follows the cursor around the center."""
    import types
    from hertzon.gui import viewer as Vw
    names = ("add_draw_point", "finish_polyline", "_track_sweep", "_arc_plane", "_phase_hint")
    View = type("View", (), {k: Vw.ModelerView.__dict__[k] for k in names})
    got = []
    v = View()
    v.grid_plane, v.axis_lock = "XY", None
    v.drawFinished = types.SimpleNamespace(emit=lambda kind, pts: got.append((kind, pts)))
    v.drawPhaseChanged = types.SimpleNamespace(emit=lambda s: None)
    v._update_preview = lambda: None
    v.cancel_draw = lambda: setattr(v, "draw", None)
    v.draw = Vw.DrawState("CreatePolyline", 3, seg_mode="CenterArc")
    v.add_draw_point(np.array([1.0, 0, 0]))
    v.add_draw_point(np.array([0.0, 0, 0]))
    assert "end angle" in v._phase_hint()
    for a in np.radians([60, 120, 179, 200, 270]):     # past 180 degrees counter-clockwise
        v._track_sweep(np.array([np.cos(a), np.sin(a), 0]))
    v.add_draw_point(np.array([0.0, -3.0, 0]))           # 270 deg; only the direction counts
    kind, pts = got[0]
    assert kind == "CreatePolyline" and v.last_segment_types == "AngularArc:270:XY"
    assert np.allclose(pts[-1], [0, -1, 0])
    # the spec and the preview of a clockwise arc
    modes = ["CenterArc", "CenterArc:-90", "Line"]
    assert Vw.segment_spec(modes, "XY") == "AngularArc:-90:XY,Line"
    P = Vw.densify([np.array([1.0, 0, 0]), np.zeros(3), np.array([0, -1.0, 0]), np.array([0, -2.0, 0])], modes)
    assert np.allclose(np.linalg.norm(P[:-1], axis=1), 1) and np.allclose(P[-1], [0, -2, 0])


def test_first_start_note(monkeypatch):
    """Solver kernels not compiled yet: the main window says so in the Message Manager and the status bar."""
    import types
    from hertzon.gui.main_window import MainWindow
    from hertzon.solver import bridge
    msgs, status = [], []
    win = types.SimpleNamespace(msgs=types.SimpleNamespace(add=lambda kind, text: msgs.append((kind, text))),
                                statusBar=lambda: types.SimpleNamespace(showMessage=lambda t, ms=0: status.append(t)))
    monkeypatch.setattr(bridge, "_first_run", lambda: False)
    MainWindow._first_start_note(win)
    assert not msgs and not status
    monkeypatch.setattr(bridge, "_first_run", lambda: True)
    MainWindow._first_start_note(win)
    assert msgs[0][0] == "warning" and "First start detected" in msgs[0][1]
    assert "compiles the solver kernels" in status[0]


def test_solid_overlaps_and_resolve():
    """Exact overlap check as the reference tool: with material override a metal partially in a dielectric is
    fine, two different dielectrics (or metals) partially overlapping are an error, contained objects are only
    listed when a conductor swallows them; without material override every partial overlap of different
    materials is an error and the inner object always wins. Resolve subtracts the winner from the loser."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import types
    from PySide6.QtWidgets import QApplication
    from hertzon.gui import main_window as MW
    from hertzon.model.validate import classify_overlaps
    app = QApplication.instance() or QApplication([])  # noqa: F841
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", "Sub", Position="0,0,0", XSize="20", YSize="20", ZSize="2").material = "FR4_epoxy"
    d.add_object("CreateBox", "Glue", Position="15,0,0", XSize="10", YSize="10", ZSize="2").material = "Teflon (tm)"
    d.add_object("CreateCylinder", "Via", CenterPosition="5,5,-1", Axis="Z", Radius="0.5", Height="4").material = "copper"
    d.add_object("CreateBox", "Shield", Position="40,0,0", XSize="10", YSize="10", ZSize="10").material = "pec"
    d.add_object("CreateBox", "Inner", Position="42,2,2", XSize="2", YSize="2", ZSize="2").material = "FR4_epoxy"
    d.add_object("CreateBox", "Air", Position="-5,-5,-5", XSize="70", YSize="30", ZSize="30").material = "vacuum"
    ev = d.evaluator(p)
    names = ["Sub", "Glue", "Via", "Shield", "Inner", "Air"]
    ovs = classify_overlaps(p, d, K.solid_overlaps(d, ev, names))
    got = {(o.winner, o.loser): o for o in ovs}
    assert set(got) == {("Glue", "Sub"), ("Via", "Sub"), ("Shield", "Inner")}, [o.message for o in ovs]
    assert got["Glue", "Sub"].level == "error" and got["Via", "Sub"].level == "ok"
    assert "two different dielectrics" in got["Glue", "Sub"].message
    assert got["Shield", "Inner"].contained and "no effect" in got["Shield", "Inner"].message
    checks = validate(p, d, None, {}, ovs)
    assert sum(c.message in {o.message for o in ovs} for c in checks) == 3
    assert [c.message for c in checks if c.level == "error" and c.category == "3D Model"] == [got["Glue", "Sub"].message]
    d.settings["MaterialOverride"] = "False"
    strict = {(o.winner, o.loser): o.level for o in classify_overlaps(p, d, K.solid_overlaps(d, ev, names))}
    assert strict == {("Glue", "Sub"): "error", ("Via", "Sub"): "error"}
    del d.settings["MaterialOverride"]
    win = types.SimpleNamespace(des=d, msgs=types.SimpleNamespace(add=lambda *a: None), validation_check=lambda: None,
                                doc=types.SimpleNamespace(checkpoint=lambda: None, commit=lambda w: None,
                                                          select=lambda picks: None))
    MW.MainWindow.resolve_overlaps(win, [o for o in ovs if not o.contained])
    assert d.obj("Sub").history[-1].kind == "Subtract" and d.obj("Sub").history[-2].kind == "Subtract"
    assert {d.obj(n).consumed_by for n in ("Glue_tool", "Via_tool")} == {"Sub"}
    names = [o.name for o in d.live_objects()]
    left = classify_overlaps(p, d, K.solid_overlaps(d, ev, names))
    assert [(o.winner, o.loser) for o in left] == [("Shield", "Inner")]
    from PySide6.QtWidgets import QPushButton
    from hertzon.gui.dialogs import ValidationDialog
    done = []
    dlg = ValidationDialog(types.SimpleNamespace(project=p, design=d, tess=None, errors={}), None, ovs, done.append)
    every = next(b for b in dlg.findChildren(QPushButton) if b.text().startswith("Resolve All"))
    assert every.text() == "Resolve All Overlaps (2)"
    every.click()
    assert len(done) == 1 and {(o.winner, o.loser) for o in done[0]} == {("Glue", "Sub"), ("Via", "Sub")}


def test_solver_rejects_partial_overlaps():
    """The solver refuses partial overlaps of different materials the same way (script and MCP runs)."""
    import types
    import pytest
    from hertzon.solver.bridge import Bridge
    obj = lambda n, m: types.SimpleNamespace(name=n, material=m)  # noqa: E731
    geo = lambda *t: types.SimpleNamespace(tags=list(t))  # noqa: E731
    vols = [(obj("Sub", "FR4_epoxy"), geo(1, 2, 3), False), (obj("Glue", "Teflon (tm)"), geo(3, 4), False),
            (obj("Via", "copper"), geo(2, 5), True), (obj("Core", "FR4_epoxy"), geo(1), False)]
    fake = types.SimpleNamespace(design=types.SimpleNamespace(settings={}), overlap_vols=vols)
    with pytest.raises(RuntimeError) as e:
        Bridge.check_overlaps(fake)
    assert "'Sub' (FR4_epoxy) and 'Glue' (Teflon (tm))" in str(e.value) and "Via" not in str(e.value)
    Bridge.check_overlaps(types.SimpleNamespace(design=fake.design, overlap_vols=vols[2:]))
    fake.design.settings["MaterialOverride"] = "False"
    with pytest.raises(RuntimeError) as e:
        Bridge.check_overlaps(types.SimpleNamespace(design=fake.design, overlap_vols=[vols[0], vols[2]]))
    assert "Material override" in str(e.value)


def test_maximum_delta_energy_default():
    """Incident wave convergence: Maximum Delta Energy is 0.001 by default."""
    from hertzon.model import schema
    p = next(p for p in schema.SETUP if p.name == "MaximumDeltaEnergy")
    assert p.default == "0.001"


def _split_box(cs=None, **split):
    """Box 0..4 x 0..2 x 50..52 (mm), split; returns the bounding box of the kept part in mm."""
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", Position="0,0,50", XSize="4", YSize="2", ZSize="2")
    if cs is not None:
        d.coord_systems.append(cs)
        split["CoordinateSystem"] = cs.name
    d.obj("Box1").history.append(Command.new("Split", **split))
    K.ensure_gmsh("test_split")
    ctx = K.build_design(d, d.evaluator(p))
    assert "Box1" not in ctx.errors, ctx.errors.get("Box1")
    return np.asarray(K._bbox(ctx.built["Box1"])) * 1e3


def test_split_in_object_and_face_coordinate_systems():
    from hertzon.model.design import CoordSys
    # global plane far from the object (the kept half space must still reach it)
    assert _split_box(Plane="XY", Keep="Positive") == pytest.approx([0, 0, 50, 4, 2, 52], abs=1e-3)
    # object CS at the box centre: XY cuts it in half along z
    ocs = CoordSys("ObjectCS1", "Object", "Global")
    ocs.props.update(Origin="2,1,51", Object="Box1")
    assert _split_box(ocs, Plane="XY", Keep="Positive") == pytest.approx([0, 0, 51, 4, 2, 52], abs=1e-3)
    assert _split_box(ocs, Plane="YZ", Keep="Negative") == pytest.approx([0, 0, 50, 2, 2, 52], abs=1e-3)
    # face CS on the side face x=4 (local z = global +x), moved in by 1: XY is the plane x=3
    fcs = CoordSys("FaceCS1", "Face", "Global")
    fcs.props.update(Mode="Axis/Position", Origin="3,1,51", XAxis="0,1,0", YPoint="0,0,1", Face="Box1:1")
    assert _split_box(fcs, Plane="XY", Keep="Positive") == pytest.approx([3, 0, 50, 4, 2, 52], abs=1e-3)
    assert _split_box(fcs, Plane="XY", Keep="Negative") == pytest.approx([0, 0, 50, 3, 2, 52], abs=1e-3)
    # custom plane: Base and Normal are local to the CS (local x = global y)
    assert _split_box(fcs, Plane="Custom", Base="0.5,0,0", Normal="1,0,0", Keep="Positive") == \
        pytest.approx([0, 1.5, 50, 4, 2, 52], abs=1e-3)


def _rotated_box(kind="Rotate", cs=None, **rot):
    """Box 0..4 x 0..2 x 0..2 (mm) rotated or swept about an axis; returns the design and the bounding box in mm."""
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", Position="0,0,0", XSize="4", YSize="2", ZSize="2")
    if cs is not None:
        d.coord_systems.append(cs)
        rot["CoordinateSystem"] = cs.name
    d.obj("Box1").history.append(Command.new(kind, **rot))
    K.ensure_gmsh("test_rotate")
    ctx = K.build_design(d, d.evaluator(p))
    assert "Box1" not in ctx.errors, ctx.errors.get("Box1")
    return d, p, np.asarray(K._bbox(ctx.built["Box1"])) * 1e3


def test_rotate_and_sweep_around_axis_in_object_and_face_coordinate_systems():
    from hertzon.model import objframe
    from hertzon.model.design import CoordSys
    # global: 90deg about z through the origin
    _, _, bb = _rotated_box(Axis="Z", Angle="90deg")
    assert bb == pytest.approx([-2, 0, 0, 0, 4, 2], abs=1e-3)
    # object CS at the box centre: the box turns in place
    ocs = CoordSys("ObjectCS1", "Object", "Global")
    ocs.props.update(Origin="2,1,1", Object="Box1")
    d, p, bb = _rotated_box(cs=ocs, Axis="Z", Angle="90deg")
    assert bb == pytest.approx([1, -1, 0, 3, 3, 2], abs=1e-3)
    # the object frame (integration lines) follows the same rotation
    M = objframe.object_matrix(d, d.obj("Box1"), d.evaluator(p))
    assert M[:3, :3] @ [0.004, 0, 0] + M[:3, 3] == pytest.approx([0.003, 0.003, 0], abs=1e-9)
    # face CS on the face x=4 (local x = global y, local z = global x): local Z is the global x axis
    fcs = CoordSys("FaceCS1", "Face", "Global")
    fcs.props.update(Mode="Axis/Position", Origin="4,1,1", XAxis="0,1,0", YPoint="0,0,1", Face="Box1:1")
    _, _, bb = _rotated_box(cs=fcs, Axis="Z", Angle="90deg")
    assert bb == pytest.approx([0, 0, 0, 4, 2, 2], abs=1e-3)     # turned in place about its own long axis
    _, _, bb = _rotated_box(cs=fcs, Axis="X", Angle="180deg")    # local X = global y through (4, 1, 1)
    assert bb == pytest.approx([4, 0, 0, 8, 2, 2], abs=1e-3)
    # Sweep Around Axis: a sheet x 0..1, y 0..1 at z=0 revolved about the object CS z axis at x=3
    q = Project()
    e = q.designs[0]
    e.add_object("CreateRectangle", Position="0,0,0", Axis="Z", Width="1", Height="1")
    cs = CoordSys("RelativeCS1", "Relative", "Global")
    cs.props.update(Origin="3,0,0", XAxis="0,1,0", YPoint="0,0,1")    # local z = global x
    e.coord_systems.append(cs)
    e.obj("Rectangle1").history.append(Command.new("SweepAroundAxis", Axis="Z", Angle="90deg",
                                                    CoordinateSystem="RelativeCS1"))
    ctx = K.build_design(e, e.evaluator(q))
    assert "Rectangle1" not in ctx.errors, ctx.errors.get("Rectangle1")
    # revolved about the global x axis: y/z sweep a quarter disc of radius 1, x stays 0..1
    assert np.asarray(K._bbox(ctx.built["Rectangle1"])) * 1e3 == pytest.approx([0, 0, 0, 1, 1, 1], abs=1e-3)


def test_assign_material_only_for_solids(monkeypatch):
    """Sheets carry no material (they get boundaries such as Finite Conductivity): Assign Material is greyed
    out for a sheet selection, a mixed selection assigns the material to the solids only, and the object
    properties of a sheet have no Material row."""
    import os
    import types
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QToolButton
    from hertzon.gui import main_window as MW
    from hertzon.gui.doc import Document, Pick
    app = QApplication.instance() or QApplication([])  # noqa: F841
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", "Sub", Position="0,0,0", XSize="10", YSize="10", ZSize="1")
    d.add_object("CreateRectangle", "Trace", Position="0,0,1", XSize="5", YSize="1", ZSize="0", WhichAxis="Z")
    doc = Document(p)
    doc.rebuild()
    assert MW.material_targets(doc, ["Sub", "Trace"]) == (["Sub"], ["Trace"])
    from hertzon.gui.doc import object_attributes
    assert "Material" in [a.name for a in object_attributes(doc, "Sub")]
    assert "Material" not in [a.name for a in object_attributes(doc, "Trace")]   # properties panel and dialog

    infos, logs = [], []
    win = types.SimpleNamespace(doc=doc, des=d, a_material=QAction("Assign Material..."),
                                _mat_buttons=[QToolButton()], msgs=types.SimpleNamespace(add=lambda *a: logs.append(a)))
    win.selected_objects = lambda need=1: doc.selected_objects()
    doc.select([Pick("Trace")])
    MW.MainWindow._update_sel_buttons(win)
    assert not win.a_material.isEnabled() and not win._mat_buttons[0].isEnabled()
    assert "Finite Conductivity" in win.a_material.toolTip()
    monkeypatch.setattr(QMessageBox, "information", lambda *a: infos.append(a))
    MW.MainWindow.assign_material(win)                  # e.g. through a custom shortcut: nothing changes
    assert infos and d.obj("Trace").material == "vacuum"

    class Dlg:
        def __init__(self, *a):
            pass

        def exec(self):
            return QDialog.Accepted

        def selected(self):
            return "copper"
    monkeypatch.setattr(MW, "MaterialsDialog", Dlg)
    doc.select([Pick("Sub"), Pick("Trace")])
    MW.MainWindow._update_sel_buttons(win)
    assert win.a_material.isEnabled() and win._mat_buttons[0].isEnabled()
    MW.MainWindow.assign_material(win)
    assert d.obj("Sub").material == "copper" and d.obj("Trace").material == "vacuum"
    assert any("Trace" in a[1] for a in logs)


def test_box_selection_takes_only_what_lies_completely_inside():
    """Left drag in the 3D view: objects (or faces) are selected only when all their points are inside the box."""
    import types
    from hertzon.gui import viewer as Vw
    from hertzon.gui.doc import Pick
    names = ("box_picks", "_box_parts")
    View = type("View", (), {k: Vw.ModelerView.__dict__[k] for k in names})

    def square(x0, size, faces=False):
        P = np.array([[x0, 0, 0], [x0 + size, 0, 0], [x0 + size, size, 0], [x0, size, 0]], float)
        tris = np.array([[0, 1, 2], [0, 2, 3]])
        return types.SimpleNamespace(points=P, tris=tris, tri_face=np.array([0, 1]) if faces else np.zeros(2, int),
                                     edges=[P[[0, 1]], P[[1, 2]]], vertices=P, dim=2)

    shown = types.SimpleNamespace(GetVisibility=lambda: True)
    hidden = types.SimpleNamespace(GetVisibility=lambda: False)
    v = View()
    v.doc = types.SimpleNamespace(selection_mode="object",
                                  tess={"A": square(0, 1), "B": square(5, 1), "C": square(1, 1), "H": square(0, 1)})
    v.actors = {n: types.SimpleNamespace(surface=hidden if n == "H" else shown, edges=None) for n in v.doc.tess}
    objs = {n: types.SimpleNamespace(name=n, group="") for n in "ABCH"}
    v.doc.design = types.SimpleNamespace(has_obj=objs.__contains__, obj=objs.__getitem__,
                                         live_objects=lambda: list(objs.values()))
    v._display_coords = lambda X: (np.asarray(X)[:, :2] * 10, np.ones(len(X), bool))   # 10 px per unit, top view
    assert v.box_picks((-1, -1), (15, 15)) == [Pick("A", -1)]          # C sticks out, B is far away, H is hidden
    assert v.box_picks((25, 15), (-1, -1)) == [Pick("A", -1), Pick("C", -1)]    # any drag direction
    assert v.box_picks((2, 2), (8, 8)) == []                            # a box inside the object takes nothing
    # a group counts only when all its shown members are inside: A and C grouped, the box holds only A
    objs["A"].group = objs["C"].group = "G1"
    assert v.box_picks((-1, -1), (15, 15)) == []
    assert v.box_picks((25, 15), (-1, -1)) == [Pick("A", -1), Pick("C", -1)]
    objs["H"].group = "G2"
    objs["B"].group = "G2"                                              # H is hidden: B alone decides
    assert v.box_picks((45, -1), (65, 15)) == [Pick("B", -1)]
    v._display_coords = lambda X: (np.asarray(X)[:, :2] * 10, np.zeros(len(X), bool))   # behind the camera
    assert v.box_picks((-100, -100), (100, 100)) == []
    v._display_coords = lambda X: (np.asarray(X)[:, :2] * 10, np.ones(len(X), bool))
    v.doc.tess = {"A": square(0, 1, faces=True)}
    v.doc.selection_mode = "face"
    assert v.box_picks((-1, -1), (11, 11)) == [Pick("A", 0), Pick("A", 1)]
    v.doc.selection_mode = "edge"
    assert v.box_picks((-1, -1), (11, 1)) == [Pick("A", 0)]          # the bottom edge only
    v.doc.selection_mode = "vertex"
    assert v.box_picks((5, -1), (11, 11)) == [Pick("A", 1), Pick("A", 2)]


def _edge_view(Vw, actors):
    """A ModelerView stand-in for screen-space edge picking: identity camera on a 100 x 100 px view,
    so world x, y in [-1, 1] land on pixels 0 to 100."""
    import types
    import vtk
    names = ("_edge_segments", "_nearest_edge")
    View = type("View", (), {k: Vw.ModelerView.__dict__[k] for k in names} | {"PICK_PX": 8})
    v = View()
    cam = types.SimpleNamespace(GetCompositeProjectionTransformMatrix=lambda *a: vtk.vtkMatrix4x4())
    v.plotter = types.SimpleNamespace(renderer=types.SimpleNamespace(GetSize=lambda: (100, 100),
                                                                     GetActiveCamera=lambda: cam))
    v.actors, v._edge_cache = actors, None
    return v


def test_edge_pick_by_screen_distance():
    """Edge mode and polylines: the edge nearest to the cursor on screen within PICK_PX pixels, hidden objects
    and other dimensions skipped, the nearer one in depth on a tie, the picking arrays rebuilt when an object
    changes."""
    import types
    import pyvista as pv
    from hertzon.gui import viewer as Vw

    def obj(polylines, dim=3, z=0.0, shown=True):
        pts, lines, off = [], [], 0
        for P in polylines:
            P = np.c_[np.asarray(P, float), np.full(len(P), z)]
            pts.append(P)
            lines.append(np.r_[len(P), np.arange(off, off + len(P))])
            off += len(P)
        em = pv.PolyData(np.vstack(pts), lines=np.concatenate(lines))
        em.cell_data["edge"] = np.arange(len(polylines)) + 10
        return types.SimpleNamespace(edge_mesh=em, dim=dim,
                                     edges=types.SimpleNamespace(GetVisibility=lambda: shown))

    # A: two edges (10 along y = 0, 11 a bent line through x = 0.5); L: a polyline (dim 1) along y = 0.6
    A = obj([[(-0.8, 0.0), (0.8, 0.0)], [(0.5, -0.8), (0.5, -0.4), (0.6, 0.4)]])
    L = obj([[(-0.8, 0.6), (0.0, 0.6), (0.8, 0.6)]], dim=1)
    v = _edge_view(Vw, {"A": A, "L": L})
    assert v._nearest_edge((20, 52)) == ("A", 10)           # 2 px above y = 0
    assert v._nearest_edge((76, 30)) == ("A", 11)           # next to the bent line, near its second segment
    assert v._nearest_edge((40, 81)) == ("L", 10)
    assert v._nearest_edge((40, 65)) is None                # more than 8 px from everything
    assert v._nearest_edge((20, 52), dims=(0, 1)) is None   # only polylines
    assert v._nearest_edge((40, 81), dims=(0, 1)) == ("L", 10)
    # a coincident edge nearer to the camera wins, a hidden one is never picked
    v.actors["B"] = obj([[(-0.8, 0.0), (0.8, 0.0)]], z=-0.5)
    assert v._nearest_edge((20, 52)) == ("B", 10)
    v.actors["B"] = obj([[(-0.8, 0.0), (0.8, 0.0)]], z=-0.5, shown=False)
    assert v._nearest_edge((20, 52)) == ("A", 10)
    # an object rebuilt with new geometry: the cached segments follow
    v.actors["A"] = obj([[(-0.8, -0.5), (0.8, -0.5)]])
    assert v._nearest_edge((20, 52)) is None
    assert v._nearest_edge((20, 26)) == ("A", 10)


def test_selection_restyles_only_what_changed():
    """A click restyles the objects whose selection changed; the colours are the same as after a full restyle."""
    import types
    import pyvista as pv
    import vtk
    from hertzon.gui import viewer as Vw
    from hertzon.gui.doc import Pick
    names = ("_plain_actor", "_drop_actor", "_objects", "_style_obj", "restyle_all", "refresh_selection",
             "_clear_highlight", "_highlight_color", "_add_highlight_mesh", "request_render")
    View = type("View", (), {k: Vw.ModelerView.__dict__[k] for k in names})
    v = View()
    ren = vtk.vtkRenderer()
    v.plotter = types.SimpleNamespace(renderer=ren, interactor=types.SimpleNamespace(isVisible=lambda: True,
                                                                                     update=lambda: None))
    objs = [types.SimpleNamespace(name=f"O{i}", color="#3060a0", transparency=0.6 if i == 2 else 0.0,
                                  visible=i != 3, wireframe=False, material="vacuum",
                                  history=[types.SimpleNamespace(kind="CreateBox")]) for i in range(5)]
    v.doc = types.SimpleNamespace(selection_mode="object", selection=[], tess={},
                                  design=types.SimpleNamespace(objects=objs, obj=lambda n: objs[int(n[1:])]),
                                  project=types.SimpleNamespace(material=lambda m: None))
    v.actors, v.highlight_actors, v.overlay_actors = {}, [], {}
    v._styled_sel, v._styled_mode = set(), None
    for o in objs:
        m = pv.Box()
        m.cell_data["face"] = np.arange(m.n_cells)
        v.actors[o.name] = Vw.ObjActors(surface=v._plain_actor(m, "#999999", pickable=True), mesh=m,
                                        edges=v._plain_actor(m.extract_all_edges(), "#202020", line_width=1))

    def look():
        return {n: (a.surface.GetProperty().GetColor(), a.surface.GetVisibility(), a.edges.GetProperty().GetLineWidth())
                for n, a in v.actors.items()}, ren.GetActors().GetNumberOfItems()
    styled = []
    real = v._style_obj
    v._style_obj = lambda n, o=None: (styled.append(n), real(n, o))
    v.refresh_selection()
    for sel in (["O1"], ["O2", "O4"], ["O3"], [], ["O0"]):
        v.doc.selection = [Pick(n) for n in sel]
        before = set(v._styled_sel)
        styled.clear()
        v.refresh_selection()
        assert set(styled) == before | set(sel)              # only what was or is selected
        got = look()
        v.restyle_all()
        assert look() == got                                 # same as restyling everything
    assert v.actors["O0"].surface.GetProperty().GetColor() == Vw._rgb(Vw.HIGHLIGHT)
    assert v.actors["O1"].surface.GetProperty().GetColor() == Vw._rgb("#3060a0")


def test_grid_and_triad_stay_at_the_origin_of_a_far_model():
    """A model far from the working CS origin: the grid still reaches the origin (with a spacing that keeps the
    line count bounded), and the triad at the origin stays inside the camera clipping range."""
    import types
    import numpy as np
    from hertzon.gui.viewer import ModelerView, WorkingCSActor
    from vtkmodules.vtkRenderingCore import vtkCamera
    box = (0.100, 0.050, 0.0, 0.103, 0.052, 0.0005)          # 3 mm part 100 mm away from the origin
    v = types.SimpleNamespace(view_bbox=lambda: box, wcs=(np.zeros(3), np.eye(3)), grid_spacing=None,
                              GRID_MAX_LINES=ModelerView.GRID_MAX_LINES)
    v._grid_bounds = lambda: ModelerView._grid_bounds(v)
    lo, hi = np.array(v._grid_bounds()[:3]), np.array(v._grid_bounds()[3:])
    assert (lo <= 0).all() and (hi >= 0).all() and hi[0] == pytest.approx(0.103)
    s = ModelerView.auto_spacing(v)
    assert 0.103 / s <= ModelerView.GRID_MAX_LINES and s == pytest.approx(1e-3)
    # an offset working CS at the part: the grid is taken in that frame and still holds its origin
    v.wcs = (np.array([0.1, 0.05, 0.0]), np.eye(3))
    g = v._grid_bounds()
    assert g[0] == pytest.approx(0.0) and g[3] == pytest.approx(0.003)
    # the camera looks at the part only: the clipping range is widened to take in the triad at the origin
    cam = vtkCamera()
    cam.SetFocalPoint(0.1015, 0.051, 0.0)
    cam.SetPosition(0.1015, 0.051 + 0.01, 0.01)
    cam.SetClippingRange(0.012, 0.016)
    t = types.SimpleNamespace(origin=np.zeros(3))
    WorkingCSActor._keep_in_depth(t, cam, 0.001)
    near, far = cam.GetClippingRange()
    d = float(np.dot(-np.array(cam.GetPosition()), np.array(cam.GetDirectionOfProjection())))
    assert d > 0.016 and near <= d - 0.001 and far >= d + 0.001


def test_open_example_lists_the_shipped_examples(tmp_path):
    """File > Open Example: every shipped .hzn is listed, sorted by name; no folder gives an empty list."""
    from hertzon.gui.main_window import EXAMPLES_DIR, example_projects
    names = [p.stem for p in example_projects()]
    assert EXAMPLES_DIR == EX.resolve()
    assert {"PatchAntenna", "HairpinFilter", "WaveguidePost", "DroneRCS"} <= set(names)
    assert names == sorted(names, key=str.lower)
    assert example_projects(tmp_path / "missing") == []


def _fillet_area(kind, radius, edges="", vertices="", op="Fillet", **props):
    import gmsh
    p = Project()
    d = p.designs[0]
    o = d.add_object(kind, **props)
    size = {"Radius": radius} if op == "Fillet" else {"Distance": radius}
    o.history.append(Command.new(op, Edges=edges, Vertices=vertices, **size))
    K.ensure_gmsh("test_fillet")
    gmsh.model.remove()
    gmsh.model.add("test_fillet")
    ctx = K.build_design(d, d.evaluator(p))
    return sum(gmsh.model.occ.getMass(*x) for x in ctx.built.get(o.name, [])), ctx.errors.get(o.name)


def test_fillet_sheet_rounds_selected_vertices():
    # a patch is a sheet: one fillet at each selected vertex (was: object vanished)
    corner = 1 - np.pi / 4
    area, err = _fillet_area("CreateRectangle", "0.5", vertices="0", Position="0,0,1", Axis="Z", XSize="10",
                             YSize="8")
    assert err is None
    assert area == pytest.approx((80 - 0.25 * corner) * 1e-6, rel=1e-6)
    area, err = _fillet_area("CreateRectangle", "1", vertices="0,1,2,3", Position="0,0,0", Axis="X", YSize="10",
                             ZSize="8")
    assert err is None
    assert area == pytest.approx((80 - 4 * corner) * 1e-6, rel=1e-6)
    # projects of 0.2.3 with edges on a sheet: the corners at both ends of the edge
    area, err = _fillet_area("CreateRectangle", "0.5", edges="0", Position="0,0,0", Axis="Z", XSize="10",
                             YSize="8")
    assert err is None
    assert area == pytest.approx((80 - 2 * 0.25 * corner) * 1e-6, rel=1e-6)


def test_chamfer_sheet_cuts_selected_vertices():
    # a patch is a sheet: one chamfer at each selected vertex, each cuts off a triangle d*d/2
    area, err = _fillet_area("CreateRectangle", "0.5", vertices="0", op="Chamfer", Position="0,0,1", Axis="Z",
                             XSize="10", YSize="8")
    assert err is None
    assert area == pytest.approx((80 - 0.125) * 1e-6, rel=1e-6)
    area, err = _fillet_area("CreateRectangle", "1", vertices="0,1,2,3", op="Chamfer", Position="0,0,0",
                             Axis="X", YSize="10", ZSize="8")
    assert err is None
    assert area == pytest.approx((80 - 4 * 0.5) * 1e-6, rel=1e-6)
    _, err = _fillet_area("CreateRectangle", "6", vertices="0,1", op="Chamfer", Position="0,0,0", Axis="Z",
                          XSize="10", YSize="8")
    assert "distance is probably too large" in err
    # solids keep the edges
    vol, err = _fillet_area("CreateBox", "0.5", edges="0", op="Chamfer", Position="0,0,0", XSize="10", YSize="8",
                            ZSize="1")
    assert err is None and vol < 80e-9
    _, err = _fillet_area("CreateBox", "0.5", vertices="0", op="Chamfer", Position="0,0,0", XSize="10",
                          YSize="8", ZSize="1")
    assert "select the edges" in err


def test_fillet_too_large_is_reported():
    _, err = _fillet_area("CreateRectangle", "6", vertices="0,1", Position="0,0,0", Axis="Z", XSize="10",
                          YSize="8")
    assert "radius is probably too large" in err
    _, err = _fillet_area("CreateBox", "0.5", edges="0,1,2,3", Position="0,0,0", XSize="10", YSize="8",
                          ZSize="0.035")
    assert "radius is probably too large" in err
    _, err = _fillet_area("CreateBox", "0.5", vertices="0", Position="0,0,0", XSize="10", YSize="8", ZSize="1")
    assert "select the edges" in err


def test_failed_fillet_leaves_object_unchanged(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from hertzon.gui.doc import Document
    from hertzon.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])  # noqa: F841
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    p = Project()
    p.designs[0].add_object("CreateRectangle", name="Patch", Position="0,0,0", Axis="Z", XSize="10", YSize="8")
    doc = Document(p)
    doc.rebuild()
    class Win:  # only the command path of the main window, no widgets
        des = property(lambda self: doc.design)
        add_command_to_selection = MainWindow.add_command_to_selection
        _edge_op = MainWindow._edge_op
    win = Win()
    win.doc = doc
    win._edge_op("Fillet", {"Radius": "6", "Vertices": "0,1"}, "Patch")
    assert [c.kind for c in doc.design.obj("Patch").history] == ["CreateRectangle"]
    assert "Patch" in doc.tess and not doc.errors and not doc.redo_stack
    assert shown and "unchanged" in shown[0]
    win._edge_op("Fillet", {"Radius": "0.5", "Vertices": "0"}, "Patch")
    assert [c.kind for c in doc.design.obj("Patch").history] == ["CreateRectangle", "Fillet"]
    assert "Patch" in doc.tess and len(shown) == 1


def test_box_selection_rectangle_does_not_stay():
    """A selection rectangle whose mouse release went elsewhere (another tab or window) goes with the next
    move or press in the 3D view instead of staying on screen."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.design import Project
    win = MainWindow(Project())
    v, ren = win.view, win.view.plotter.renderer
    n0 = ren.GetViewProps().GetNumberOfItems()
    v._band, v._press_pos = ((10, 10), None, None), (10, 10)
    v._show_band((80, 60))
    assert ren.GetViewProps().GetNumberOfItems() == n0 + 2
    v._on_move(None, None)                       # no button is down: the release was lost
    assert v._band is None and ren.GetViewProps().GetNumberOfItems() == n0
    win.doc.dirty = False
    win.close()


@pytest.mark.parametrize("fused", [False, True])
def test_helix_does_not_coarsen_other_cylinders(fused):
    # a swept pipe (BSpline surface) limits the curvature sizing, but only for its own faces: with a
    # helix in the model every cylinder was drawn with 8 segments per turn, also one fused with it
    def base_circles(helix):
        p = Project()
        d = p.designs[0]
        d.add_object("CreateCylinder", "Base", CenterPosition="0,0,-3", Axis="Z", Radius="6", Height="3.1")
        if helix:
            d.add_object("CreateHelix", "Coil", CenterPosition="0,0,0", Axis="Z", Radius="4", Pitch="1",
                         Turns="5", WireRadius="0.2")
            if fused:
                d.obj("Coil").consumed_by = "Base"
                d.obj("Base").history.append(Command.new("Unite", Tools="Coil", KeepOriginals="False"))
        K.ensure_gmsh("test_helix_cyl")
        ctx = K.build_design(d, d.evaluator(p))
        assert not ctx.errors
        tess = K.tessellate(ctx)
        # the two rims of the base: the widest edges
        E = [e for e in tess["Base"].edges if len(e) > 2]
        w = max(np.ptp(e[:, 0]) for e in E)
        return sorted(len(e) for e in E if np.ptp(e[:, 0]) > 0.99 * w)
    assert base_circles(True) == base_circles(False)
    assert len(base_circles(True)) == 2 and min(base_circles(True)) >= 30


@pytest.mark.parametrize("nseg", ["0", "8"])
def test_helix_wire_leaves_no_loose_faces(nseg):
    # the pipe is a copy of the profile disk; the disk left behind had no volume on either side and
    # the solver mesh failed with a KeyError on its triangles
    import gmsh
    p = Project()
    d = p.designs[0]
    d.add_object("CreateHelix", "Coil", CenterPosition="0,0,0", Axis="Z", Radius="4", Pitch="1",
                 Turns="2", WireRadius="0.2", NumberOfSegmentsPerTurn=nseg)
    K.ensure_gmsh("test_helix_loose")
    ctx = K.build_design(d, d.evaluator(p))
    assert ctx.built["Coil"] and not ctx.errors
    assert all(len(gmsh.model.getAdjacencies(2, s)[0]) for _, s in gmsh.model.getEntities(2))
    assert len(gmsh.model.getEntities(1)) == len({abs(t) for _, t in gmsh.model.getBoundary(
        gmsh.model.getEntities(2), combined=False, oriented=False)})
