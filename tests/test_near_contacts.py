"""Objects that almost touch (#Ex2 rerun): parallel faces a few micrometres apart and a via exactly tangent to the
edge of a trace are listed by the sheet placement check (kernel.occ.near_contacts) and named by the validation
check; thin solids (copper on a substrate) and clear overlaps or gaps are not."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_sheets_in_solids import _board, _placement  # noqa: E402

from hertzon.model.validate import validate  # noqa: E402


def _near(case: str, gap: float):
    p, d, _ = _board("clip")
    A = d.add_object
    if case == "side":
        A("CreateBox", "BlkA", Position="2,2,1.6", XSize="5", YSize="3", ZSize="1").material = "copper"
        A("CreateBox", "BlkB", Position=f"{7 + gap},2,1.6", XSize="5", YSize="3", ZSize="1").material = "copper"
    elif case == "sheet":
        A("CreateBox", "BlkA", Position="2,2,1.6", XSize="5", YSize="3", ZSize="1").material = "copper"
        A("CreateRectangle", "Sh", Position=f"4,3,{2.6 + gap}", Axis="Z", XSize="5", YSize="3")
    elif case == "foil":      # 18 um copper foil on a 0.1 mm flex layer: thin solids, not a gap
        A("CreateBox", "Flex", Position="2,2,1.6", XSize="5", YSize="3", ZSize="0.1").material = "FR4_epoxy"
        A("CreateBox", "Foil", Position="3,2.5,1.7", XSize="2", YSize="1", ZSize="0.018").material = "copper"
    else:                     # via of radius 0.5 next to the 1 mm wide trace (x = 19.5 .. 20.5)
        A("CreateCylinder", "ViaT", CenterPosition=f"{21.0 + gap},14,0", Axis="Z", Radius="0.5",
          Height="1.6").material = "copper"
    pl = _placement(p, d)
    msgs = [c.message for c in validate(p, d, sheets=pl) if c.level == "warning"]
    return [x for x in pl if ":" in x[2]], msgs


@pytest.mark.parametrize("case, gap", [("side", 0.003), ("side", -0.003), ("sheet", 0.003)])
def test_faces_a_few_micrometres_apart_are_named(case, gap):
    near, msgs = _near(case, gap)
    assert len(near) == 1 and near[0][2].startswith("gap:")
    assert float(near[0][2].split(":")[1]) == pytest.approx(3e-6, rel=1e-3)
    assert any("almost touch: parallel faces 3 µm apart" in m for m in msgs), msgs


@pytest.mark.parametrize("case, gap", [("side", 0.0), ("side", -0.05), ("side", 0.5), ("foil", 0.0)])
def test_touching_overlapping_separated_and_thin_solids_are_not_named(case, gap):
    near, msgs = _near(case, gap)
    assert near == [] and not any("almost touch" in m for m in msgs)


@pytest.mark.parametrize("gap", [0.0, 0.002])
def test_via_tangent_to_a_trace_edge_is_named(gap):
    near, msgs = _near("via", gap)
    assert [(a, b, how.split(":")[0]) for a, b, how in near] == [("Trace", "ViaT", "tangent")]
    assert any("'Trace' and 'ViaT' touch tangentially" in m for m in msgs), msgs


@pytest.mark.parametrize("gap", [0.1, -0.1])
def test_via_clearly_off_or_across_the_trace_edge_is_not_named(gap):
    near, _ = _near("via", gap)
    assert near == []


def test_the_via_fence_is_not_a_near_contact():
    p, d, _ = _board("fence")
    assert not [x for x in _placement(p, d) if ":" in x[2]]
