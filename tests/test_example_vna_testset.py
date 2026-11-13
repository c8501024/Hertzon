"""The shipped example VNA_TestSet_2G4.hzn: wizard blocks on the board, connected by the router."""
import sys
from pathlib import Path

import numpy as np

from hertzon.model import board as BD
from hertzon.model import routing as RT
from hertzon.model.design import Project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "VNA_TestSet_2G4.hzn"


def test_example_is_current(tmp_path):
    sys.path.insert(0, str(ROOT / "examples"))
    import make_vna_testset
    made = make_vna_testset.vna_testset()
    made.save(tmp_path / "x.hzn")
    fresh, shipped = Project.load(tmp_path / "x.hzn"), Project.load(EXAMPLE)
    assert [d.name for d in fresh.designs] == [d.name for d in shipped.designs]
    a, b = BD.load(fresh.design("TestSet")), BD.load(shipped.design("TestSet"))
    assert len(a.routes) == len(b.routes)
    for ra, rb in zip(a.routes, b.routes):
        assert (ra.a, ra.b) == (rb.a, rb.b) and np.allclose(ra.pts, rb.pts, atol=1e-4)


def test_board_is_complete_and_the_receivers_are_phase_matched():
    p = Project.load(EXAMPLE)
    c = p.design("TestSet")
    b = BD.load(c)
    parts, nodes, notes = BD.board_parts(p, c)
    assert not notes
    assert not BD.ratsnest(parts, b, nodes) and not BD.overlaps(parts, b, nodes)
    assert all(BD.route_ok(parts, b, r) for r in b.routes)
    _, sub = RT.substrate(p, c)
    recv = [r for r in b.routes if {r.a[0], r.b[0]} & {"R", "A", "B"}]
    ph = [RT.phase(r, sub, 2.45e9) for r in recv]
    assert len(recv) == 3 and len({r.group for r in recv}) == 1 and max(ph) - min(ph) < 0.2
    # a resistor and its via face each other: one straight piece, no detour
    via = next(r for r in b.routes if {r.a[0], r.b[0]} == {"R_iso1", "V_iso1"})
    assert len(via.pts) == 2


def test_test_set_paths():
    from hertzon.solver.circuit import analyze
    p = Project.load(EXAMPLE)
    r = analyze(p, p.design("TestSet"), np.array([2.45e9]))
    S = r.S[0]
    db = lambda x: 20 * np.log10(abs(x))  # noqa: E731
    # ports 1 SRC, 2 R, 3 A, 4 B, 5 DUT1, 6 DUT2, 7 TERM2
    assert -4.5 < db(S[1, 0]) < -3.2 and -4.5 < db(S[4, 0]) < -3.2      # source to R and to the test port
    assert abs(db(S[2, 4]) + 15) < 1 and abs(db(S[3, 5]) + 15) < 1       # reflected to A, transmitted to B
    assert db(S[2, 0]) < db(S[2, 4]) + db(S[4, 0]) - 3                    # the forward wave stays out of A
    assert db(S[0, 0]) < -20 and db(S[4, 4]) < -20 and db(S[5, 5]) < -20


def test_match_phase_with_a_set_difference():
    """Match Phase with an offset: the route to A lags the others by 90 degrees at f0."""
    p = Project.load(EXAMPLE)
    c = p.design("TestSet")
    b = BD.load(c)
    recv = {r.b[0] if r.b[0] in "RAB" else r.a[0]: i for i, r in enumerate(b.routes)
            if {r.a[0], r.b[0]} & {"R", "A", "B"}}
    b, warnings, ph = RT.match_phase(p, c, b, list(recv.values()), 2.45e9, {recv["A"]: 90.0})
    assert not warnings
    assert abs(ph[recv["A"]] - ph[recv["R"]] - 90) < 0.2 and abs(ph[recv["B"]] - ph[recv["R"]]) < 0.2
    assert b.routes[recv["A"]].offset == 90.0
