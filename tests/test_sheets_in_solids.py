"""PEC sheets in or at solids: a via fence of vertical Perfect E sheets with pec cylinders through a FR4 board
(solved with the default matrix solver and with MUMPS), and a battery clip of Perfect E sheets that ends in air
on the ground sheet next to a disabled lumped RLC in a 0.5 mm gap of the top layer. Each must mesh and solve.
Sheets that run through a conductor are named by the validation check and in the error message of a failed
solve."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model.design import Assignment, Project, Setup  # noqa: E402


def _board(case: str, solver: str = "Auto"):
    """FR4 board 40 x 28 x 1.6 mm with a ground sheet, a trace and a lumped port, in an air region."""
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    A = d.add_object
    A("CreateBox", "Sub", Position="0,0,0", XSize="40", YSize="28", ZSize="1.6").material = "FR4_epoxy"
    A("CreateRectangle", "Gnd", Position="0,0,0", Axis="Z", XSize="40", YSize="28")
    A("CreateRectangle", "Trace", Position="19.5,6,1.6", Axis="Z", XSize="1", YSize="16")
    A("CreateRectangle", "PortSheet", Position="19.5,6,0", Axis="Y", XSize="1", ZSize="1.6")
    metal = ["Gnd", "Trace"]
    if case == "fence":
        # vertical sheets at x = 0.3 / 39.7 and y = 0.3 through the substrate, pec vias on them
        A("CreateRectangle", "FenceL", Position="0.3,0,0", Axis="X", YSize="28", ZSize="1.6")
        A("CreateRectangle", "FenceR", Position="39.7,0,0", Axis="X", YSize="28", ZSize="1.6")
        A("CreateRectangle", "FenceB", Position="0,0.3,0", Axis="Y", XSize="40", ZSize="1.6")
        metal += ["FenceL", "FenceR", "FenceB"]
        pts = [(x, y) for y in range(2, 28, 4) for x in (0.3, 39.7)] + [(x, 0.3) for x in range(2, 40, 4)]
        for k, (x, y) in enumerate(pts):
            A("CreateCylinder", f"Via{k + 1}", CenterPosition=f"{x},{y},0", Axis="Z", Radius="0.2",
              Height="1.6").material = "pec"
    else:
        # top layer with a 0.5 mm gap that holds a disabled lumped RLC; a clip of sheets under the board: its
        # top at z = -3.7 and two legs that end in the air on the ground sheet
        A("CreateRectangle", "TopPad", Position="5,20,1.6", Axis="Z", XSize="4", YSize="4")
        A("CreateRectangle", "TopPad2", Position="9.5,20,1.6", Axis="Z", XSize="4", YSize="4")
        A("CreateRectangle", "RLCSheet", Position="9,21,1.6", Axis="Z", XSize="0.5", YSize="2")
        A("CreatePolyline", "ClipTop", Points="10,5,-3.7;30,5,-3.7;30,12,-3.7;10,12,-3.7", Closed="True",
          Covered="True")
        A("CreateRectangle", "ClipLegL", Position="10,5,-3.7", Axis="X", YSize="7", ZSize="3.7")
        A("CreateRectangle", "ClipLegR", Position="30,5,-3.7", Axis="X", YSize="7", ZSize="3.7")
        metal += ["TopPad", "TopPad2", "ClipTop", "ClipLegL", "ClipLegR"]
        rlc = Assignment("RLC1", "Lumped RLC", {"Model": "Parallel RLC", "UseResistance": "True",
                                                "Resistance": "50ohm"}, objects=["RLCSheet"])
        rlc.enabled = False
        d.boundaries.append(rlc)
    A("CreateRegion", "Region", **{"PaddingType": "Absolute Offset", "+X": "20mm", "-X": "20mm", "+Y": "20mm",
                                   "-Y": "20mm", "+Z": "20mm", "-Z": "20mm"})
    d.boundaries.append(Assignment("PEC", "Perfect E", {}, objects=metal))
    d.boundaries.append(Assignment("Rad", "Radiation", {}, objects=["Region"]))
    d.excitations.append(Assignment("1", "Lumped Port", {"PortNumber": "1", "Impedance": "50ohm"},
                                    objects=["PortSheet"]))
    st = Setup("Setup1")
    st.props.update(Frequency="0.9GHz", MaximumPasses="2", CurvedElements="False", Solver=solver)
    d.setups.append(st)
    return p, d, st


def _placement(p, d):
    from hertzon.kernel import occ as K
    from hertzon.model.materials import is_conductor
    K.ensure_gmsh()
    return K.sheet_placement(d, d.evaluator(p), lambda n: is_conductor(p.material(d.obj(n).material)))


def _solve(tmp_path, p, d, st):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    from hertzon.solver.results import ResultStore
    b = B.Bridge(p, d, st, tmp_path / "out")
    with em.Simulation(f"sheets_{tmp_path.name}", save_file=False) as m:
        b._run_in(m)
    return b, ResultStore(tmp_path / "out")


def test_sheets_through_conductors_are_named():
    from hertzon.model.validate import validate
    p, d, _ = _board("fence")
    pl = _placement(p, d)
    through = {(s, v) for s, v, how in pl if how == "along-conductor"}
    assert ("FenceL", "Via1") in through and ("FenceB", "Via15") in through
    assert not any(s == "Trace" or v.startswith("Via") and s == "Gnd" for s, v, how in pl if how != "through")
    # an inner sheet in a dielectric is normal, the port sheet between two conductors too
    msgs = [c.message for c in validate(p, d, sheets=pl) if c.level == "warning"]
    for s in ("FenceL", "FenceR", "FenceB"):
        assert any(m.startswith(f"Sheet '{s}' runs along the inside of the conductor") for m in msgs), msgs
    assert not any("PortSheet" in m for m in msgs)
    p, d, _ = _board("clip")
    assert not [m for m in (c.message for c in validate(p, d, sheets=_placement(p, d))) if "Sheet '" in m]


@pytest.mark.parametrize("solver", ["Auto", "MUMPS"])
def test_via_fence_of_sheets_and_cylinders_in_the_substrate_solves(tmp_path, solver):
    p, d, st = _board("fence", solver)
    b, store = _solve(tmp_path, p, d, st)
    assert not hasattr(b, "sheet_hints")         # the sheet check runs only after a failure
    S = store.sparams("LastAdaptive")["S"]
    assert S.shape[1:] == (1, 1) and abs(S[0, 0, 0]) <= 1.0 + 1e-6


def test_clip_of_sheets_on_the_ground_sheet_with_a_disabled_lumped_rlc_solves(tmp_path):
    p, d, st = _board("clip")
    _, store = _solve(tmp_path, p, d, st)
    S = store.sparams("LastAdaptive")["S"]
    assert S.shape[1:] == (1, 1) and abs(S[0, 0, 0]) <= 1.0 + 1e-6


def test_a_failed_solve_names_the_sheets(tmp_path, monkeypatch):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    p, d, st = _board("fence")
    b = B.Bridge(p, d, st, tmp_path / "out")

    def broken(self):
        raise ValueError("index 106 is out of bounds for axis 0 with size 106")
    monkeypatch.setattr(B.Bridge, "solve", broken)
    monkeypatch.setattr(em, "Simulation", lambda *a, **k: _Ctx())
    monkeypatch.setattr(B.Bridge, "prepare", lambda self, m: None)
    with pytest.raises(RuntimeError) as e:
        b.run()
    text = str(e.value)
    assert "index 106" in text and "Possible cause in the model" in text and "'FenceL'" in text


class _Ctx:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


def test_mcp_validate_names_a_sheet_through_a_conductor(tmp_path, monkeypatch):
    import json
    from hertzon.mcp import tools
    from hertzon.mcp.protocol import Server
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    tools.S.default_path = None
    srv = Server("hertzon", "test", tools.INSTRUCTIONS, tools.TOOLS)
    p, _, _ = _board("fence")
    p.save(tmp_path / "fence.hzn")

    def call(name, **args):
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return json.loads(r["result"]["content"][0]["text"])
    call("open_project", path=str(tmp_path / "fence.hzn"))
    checks = call("validate")["checks"]
    assert any(c["level"] == "warning" and "Sheet 'FenceL' runs along" in c["message"] for c in checks), checks


def test_normal_models_are_not_reported(tmp_path):
    """Inner copper planes of an imported board crossed by its vias, and the walls of a via fence on the faces
    of the substrate (no cylinders): nothing to report."""
    from hertzon.model.validate import sheet_messages
    from hertzon.pcbimport import board as B
    sys.path.insert(0, str(Path(__file__).parent))
    from test_mcp_build_worker import _board as _gerbers
    files, job, jn = B.load([_gerbers(tmp_path / "rf")])
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    B.build(p, d, B.plan(files, job, jn))
    pl = _placement(p, d)
    assert any(how == "across-conductor" for _, _, how in pl)
    assert sheet_messages(d, pl) == []
    p, d, _ = _board("clip")
    d.add_object("CreateRectangle", "WallL", Position="0,0,0", Axis="X", YSize="28", ZSize="1.6")
    d.add_object("CreateRectangle", "WallB", Position="0,0,0", Axis="Y", XSize="40", ZSize="1.6")
    d.add_object("CreateBox", "Clip", Position="10,5,-3.7", XSize="20", YSize="7", ZSize="0.2").material = "pec"
    assert sheet_messages(d, _placement(p, d)) == []
