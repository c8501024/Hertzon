"""The shipped example projects use the current features and file format, and projects saved by older versions
still open (no FEM solve)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model.design import FILE_VERSION, Project  # noqa: E402
from hertzon.model import schema  # noqa: E402

EX = ROOT / "examples"
LEGACY = Path(__file__).resolve().parent / "data" / "legacy_v1"
EXAMPLES = sorted(EX.glob("*.hzn"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_examples_use_the_current_format_and_refinement(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == FILE_VERSION
    p = Project.load(path)
    for d in p.designs:
        for s in d.setups:
            if s.eigen:
                continue            # an eigenmode setup has no refinement method or curved element option
            assert s.props["RefinementMethod"] == "hzAURA (EMerge)", (d.name, s.name)
            assert s.props["CurvedElements"] == "True", (d.name, s.name)
        if any(e.kind == "Incident Plane Wave" for e in d.excitations) and d.settings["AutoOpen"] != "Off":
            assert not d.settings.get("AutoOpenPadding", "").strip()     # one wavelength of air, set by Hertzon
    # nothing is lost or changed by a save
    out = Project.load(path)
    assert json.dumps(p.to_dict(), sort_keys=True) == json.dumps(out.to_dict(), sort_keys=True)


def _build(p, d):
    from hertzon.kernel import occ as K
    from hertzon.model.validate import validate
    K.ensure_gmsh("test_examples")
    ctx = K.build_design(d, d.evaluator(p))
    assert not ctx.errors
    tess = K.tessellate(ctx)
    checks = validate(p, d, tess, ctx.errors)
    assert not [c for c in checks if c.level == "error"], checks
    return tess


def test_dipole_half_model_with_a_symmetry_plane():
    """The half dipole: arms cut at x = 0, a Perfect H symmetry plane on the region face there, radiation on
    the other faces, and the port at twice the impedance of the full model (half the current, same voltage)."""
    p = Project.load(EX / "Dipole.hzn")
    full, half = p.design("HalfWave1GHz"), p.design("HalfWave1GHz_Half")
    tess = _build(p, half)
    sym = half.assignment("Sym_PerfectH")
    rad = half.assignment("Rad1")
    assert sym.kind == "Symmetry" and sym.props["Type"] == "Perfect H"
    t = tess["AirBox"]
    assert [round(t.face_center[f.face][0], 9) for f in sym.faces] == [0.0]
    assert sorted(f.face for f in sym.faces + rad.faces) == list(range(len(t.face_area)))
    assert half.settings["AutoOpen"] == "Off"
    ev = p.design("HalfWave1GHz").evaluator(p)
    assert ev.value(half.excitations[0].props["Impedance"]) == pytest.approx(
        2 * ev.value(full.excitations[0].props["Impedance"]))


def test_microstrip_dc_block_is_a_grouped_chip_capacitor():
    p = Project.load(EX / "Microstrip.hzn")
    d = p.design("Microstrip50_DCBlock")
    _build(p, d)
    assert sorted(o.name for o in d.objects if o.group == "C1") == ["C1", "C1_PadA", "C1_PadB"]
    rlc = d.assignment("C1_RLC")
    assert rlc.kind == "Lumped RLC" and rlc.objects == ["C1"]
    assert (rlc.props["Model"], rlc.props["Capacitance"], rlc.props["Component"]) == ("Series RLC", "2.2pF", "0603")
    # the pads conduct like the traces they join
    assert {"TraceA", "TraceB", "C1_PadA", "C1_PadB"} <= set(d.assignment("Copper_Trace").objects)


def test_examples_have_cut_plane_field_plots():
    for name, design, plot in (("WaveguidePost", "WR90_Post", "Mag_E_Cut"), ("Coax", "Coax50", "Vector_E_Cross"),
                               ("Dipole", "HalfWave1GHz", "Mag_E_XZ"), ("SphereRCS", "PECSphere", "Mag_E_XZ"),
                               ("PatchAntenna", "Patch245", "Mag_E_YZ")):
        d = Project.load(EX / f"{name}.hzn").design(design)
        fp = next(f for f in d.field_plots if f.name == plot)
        assert fp.plane and not fp.faces and not fp.objects


@pytest.mark.parametrize("name", ["Dipole", "Microstrip", "PlaneWave", "GPS_L1_RHCP"])
def test_projects_of_format_1_still_open(name):
    """Copies of the examples as saved by 0.2.2 and earlier (format 1, Windows line ends in part): they load with
    the renamed refinement methods, the rectangle sizes of old files and their integration lines, and the
    updated example keeps the same model."""
    raw = json.loads((LEGACY / f"{name}.hzn").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    old = Project.load(LEGACY / f"{name}.hzn")
    choices = next(x for x in schema.SETUP if x.name == "RefinementMethod").choices
    for d in old.designs:
        for s in d.setups:
            if s.eigen:
                continue            # an eigenmode setup has no refinement method or curved element option
            assert s.props["RefinementMethod"] in choices
        for o in d.objects:
            if o.history[0].kind == "CreateRectangle":
                assert "Width" not in o.history[0].props and "Height" not in o.history[0].props
    new = Project.load(EX / f"{name}.hzn")
    for d in old.designs:
        n = new.design(d.name)
        assert d.variables == n.variables
        assert [(o.name, [(c.kind, c.props) for c in o.history]) for o in d.objects if o.name != "Rectangle1"] == \
            [(o.name, [(c.kind, c.props) for c in o.history]) for o in n.objects if o.name != "Rectangle1"]
        for a in d.excitations + d.boundaries:
            b = n.assignment(a.name)
            assert (a.kind, a.props, a.objects) == (b.kind, b.props, b.objects)
    saved = Project.load(LEGACY / f"{name}.hzn").to_dict()
    assert saved["version"] == FILE_VERSION


BENCH = EX / "benchmark"


@pytest.mark.parametrize("path", sorted(BENCH.glob("*/*.hzn")), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_benchmark_projects_use_the_current_format(path):
    """Tools > Solver Benchmark times the current default refinement; the comparison projects carry their method
    in the name (_aura, _local, _remesh, _stable) and have an hzAURA variant next to every local one."""
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == FILE_VERSION
    methods = {s.props["RefinementMethod"] for d in Project.load(path).designs for s in d.setups}
    if path.parent.name == "scaling" or path.stem.endswith("_aura"):
        assert methods == {"hzAURA (EMerge)"}
    if path.stem.endswith("_local"):
        assert methods == {"Local (Delaunay)"}
        assert path.with_name(path.name.replace("_local", "_aura")).exists()


def test_showcase_builds_and_passes_the_validation_check():
    """The feature showcase: every design builds and has no validation errors (edge mesh operations included)."""
    p = Project.load(EX / "Showcase_AllFeatures.hzn")
    assert len(p.designs) == 8
    for d in p.designs:
        _build(p, d)
    a = p.design("A_PatchArray")
    edge_op = a.assignment("Mesh_FeedEdges")
    assert edge_op.edges and not edge_op.faces and not edge_op.objects
    # features added since: Derivative Trust Region, terminal pairs, two-mode ports, ports-only setups
    assert {o.props["Optimizer"] for o in a.optimetrics} == {"Conjugate Gradient", "Derivative Trust Region"}
    g = p.design("G_DiffPair_Terminal")
    assert g.settings["SolutionType"] == "Terminal" and json.loads(g.settings["DifferentialPairs"])
    assert {e.props["ReferenceConductor"] for e in g.excitations} == {"Outer"}
    h = p.design("H_TwoModeWaveguide")
    assert {e.props["NumberOfModes"] for e in h.excitations} == {"2"}
    assert any(s.props.get("PortsOnly") == "True" for d in p.designs for s in d.setups)
