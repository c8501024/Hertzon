"""Automatic connections on the board (model/routing.py): paths round copper, bends, tapers, equal phase, the routes
in the circuit simulation, and 3D design blocks (Design Wizard structures) as parts of the board."""
import math

import numpy as np
import pytest
from test_circuit_layout import C, P, _circuit, _line

from hertzon.model import board as BD
from hertzon.model import circuit as CK
from hertzon.model import routing as RT
from hertzon.solver import circuit as SC


def _two_ports():
    """Port1, a 10 mm line, Port2: placed by the schematic, then Port2 is moved away so a connection is missing."""
    p, d = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0, L="10mm", W="1.08mm"), P("Port2", 80, 0))
    d.components[0].props.update(Er="3.66", H="0.508mm", T="35um", TanD="0.0037")
    b, _ = BD.auto_place(p, d)
    return p, d, b


def test_straight_route_is_a_line_of_z0_in_the_simulation():
    p, d, b = _two_ports()
    b.placements["Port2"] = BD.Placement(25.0, 0.0, 180)      # 15 mm straight on from the end of MS1
    parts, nodes, _ = BD.board_parts(p, d)
    assert len(BD.ratsnest(parts, b, nodes)) == 1
    b, warnings = RT.connect(p, d, b, 50.0, "Arc", f=2e9)
    assert not warnings and len(b.routes) == 1
    r = b.routes[0]
    assert r.pts == [(10.0, 0.0), (25.0, 0.0)] and r.radii == []
    _, sub = RT.substrate(p, d)
    from hertzon.solver import tlines as TL
    assert TL.microstrip(2e9, r.w * 1e-3, sub["h"], sub["t"], sub["er"], sub["tand"], sub["cond"])[0].real == \
        pytest.approx(50.0, abs=0.05)
    assert BD.ratsnest(parts, b, nodes) == [] and BD.overlaps(parts, b, nodes) == []
    BD.store(d, b)
    f = np.array([1e9, 3e9])
    res = SC.analyze(p, d, f)
    assert any("routed on the board" in w for w in res.warnings)
    # the same as the schematic with that line drawn in it
    p2, d2 = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0, L="10mm", W="1.08mm"),
                      _line("MS2", 120, 0, L="15mm", W=f"{r.w:.9g}mm"), P("Port2", 160, 0))
    d2.components[0].props.update(Er="3.66", H="0.508mm", T="35um", TanD="0.0037")
    d2.settings[CK.AUTO] = "False"            # the route has no step model at the 2 % width change either
    ref = SC.analyze(p2, d2, f)
    assert np.allclose(res.S, ref.S, atol=1e-6)
    # switched off, the ideal connection of the schematic again
    b.opts["simulate"] = False
    BD.store(d, b)
    p3, d3 = _circuit(P("Port1", 0, 0), _line("MS1", 40, 0, L="10mm", W="1.08mm"), P("Port2", 80, 0))
    d3.components[0].props.update(Er="3.66", H="0.508mm", T="35um", TanD="0.0037")
    assert np.allclose(SC.analyze(p, d, f).S, SC.analyze(p3, d3, f).S, atol=1e-12)


def test_route_goes_round_copper_with_bends_and_keeps_the_gap():
    p, d, b = _two_ports()
    # Port2 above and behind the line's end, a block of copper (a stub of another part) in the way
    b.placements["Port2"] = BD.Placement(4.0, 12.0, 0)
    b, warnings = RT.connect(p, d, b, 50.0, "Arc", f=2e9)
    r = b.routes[0]
    assert len(r.pts) >= 4 and all(x > 0 for x in r.radii)              # leaves MS1 forward, turns back
    assert all(abs(a[0] - c[0]) < 1e-9 or abs(a[1] - c[1]) < 1e-9 for a, c in zip(r.pts, r.pts[1:]))
    parts, nodes, _ = BD.board_parts(p, d)
    assert BD.overlaps(parts, b, nodes) == []
    # the copper keeps 3 line widths from the line it does not connect to... here the line it starts at is its own
    # part: only the far side of the route is checked against MS1
    poly = RT.polygon(r)
    from shapely.geometry import Polygon
    ms1 = Polygon([b.placements["MS1"].world(q) for q in parts["MS1"].copper[0].pts])
    assert poly.intersection(ms1).area < 1e-3
    # the circuit model has a piece per straight run and an arc per bend, the same length as the drawing
    els = RT.elements(r)
    assert sum(q["L"] for t, q in els if t == "MLIN") == pytest.approx(RT.length(r), rel=1e-3)
    # mitered: an MMBEND per corner, the miter cut from the copper
    b2 = BD.Board(dict(b.placements))
    b2, _ = RT.connect(p, d, b2, 50.0, "Miter", f=2e9)
    r2 = b2.routes[0]
    assert r2.radii and all(x == 0 for x in r2.radii)
    assert sum(1 for t, _ in RT.elements(r2) if t == "MMBEND") == len(r2.pts) - 2
    # centre line length x w counts every corner square once from each arm; the 50 % miter leaves half of it
    band = sum(math.hypot(c[0] - a[0], c[1] - a[1]) for a, c in zip(r2.pts, r2.pts[1:])) * r2.w
    assert RT.polygon(r2).area == pytest.approx(band - (len(r2.pts) - 2) * r2.w ** 2 / 2, rel=1e-3)


def test_taper_where_the_pin_is_narrower():
    p, d, b = _two_ports()
    d.components[2].props["W"] = "0.5mm"
    parts, nodes, _ = BD.board_parts(p, d)
    b.placements["Port2"] = BD.Placement(30.0, 0.0, 180)
    b, _ = RT.connect(p, d, b, 50.0, "Arc", f=2e9)
    r = b.routes[0]
    assert r.wa == pytest.approx(0.5) and r.la == pytest.approx(RT.TAPER_W * r.w) and r.wb == 0
    els = RT.elements(r)
    ws = [q["W"] for t, q in els[:RT.TAPER_STEPS]]
    assert ws == sorted(ws) and 0.5 < ws[0] < ws[-1] < r.w
    poly = RT.polygon(r)
    from shapely.geometry import box
    assert poly.intersection(box(10.0, -1, 10.05, 1)).bounds[3] == pytest.approx(0.25 + 0.05 * (r.w - 0.5) / 2 /
                                                                                 r.la, abs=0.01)


def test_match_phase_puts_a_meander_into_the_shorter_route():
    p, d = _circuit(P("Port1", 0, 0), P("Port2", 300, 0), P("Port3", 0, 200), P("Port4", 300, 200),
                    C("N1", "LABEL", 0, 0, 0, {"Net": "a"}), C("N2", "LABEL", 300, 0, 0, {"Net": "a"}),
                    C("N3", "LABEL", 0, 200, 0, {"Net": "b"}), C("N4", "LABEL", 300, 200, 0, {"Net": "b"}))
    d.components[0].props.update(Er="3.66", H="0.508mm", T="35um", TanD="0.0037")
    b = BD.Board({"Port1": BD.Placement(0, 0, 0), "Port2": BD.Placement(30, 0, 180),
                  "Port3": BD.Placement(0, -20, 0), "Port4": BD.Placement(30, -26, 180)})
    b, warnings = RT.connect(p, d, b, 50.0, "Arc", f=2.4e9)
    assert len(b.routes) == 2, warnings
    _, sub = RT.substrate(p, d)
    ph = [RT.phase(r, sub, 2.4e9) for r in b.routes]
    assert abs(ph[0] - ph[1]) > 10
    b, warnings, res = RT.match_phase(p, d, b, [0, 1], 2.4e9)
    assert not warnings
    ph = [RT.phase(r, sub, 2.4e9) for r in b.routes]
    assert ph[0] == pytest.approx(ph[1], abs=0.3)
    assert b.routes[0].group == b.routes[1].group != ""
    short = min(range(2), key=lambda i: len(b.routes[i].plain))
    assert any(len(r.plain) for r in b.routes)
    # matching again starts from the plain route (no meander on a meander)
    b2, _, _ = RT.match_phase(p, d, BD.Board.from_json(b.to_json()), [0, 1], 2.4e9)
    assert [len(r.pts) for r in b2.routes] == [len(r.pts) for r in b.routes]
    assert short in (0, 1)
    # stored and read back, the circuit simulation sees the same phases
    BD.store(d, b)
    res = SC.analyze(p, d, np.array([2.4e9]))
    S = res.S[0]
    assert np.degrees(np.angle(S[1, 0] / S[3, 2])) == pytest.approx(0.0, abs=0.3)


def test_moved_part_makes_its_route_stale():
    p, d, b = _two_ports()
    b.placements["Port2"] = BD.Placement(25.0, 0.0, 180)
    b, _ = RT.connect(p, d, b, 50.0, "Arc", f=2e9)
    parts, nodes, _ = BD.board_parts(p, d)
    assert BD.route_ok(parts, b, b.routes[0])
    b.placements["Port2"].y += 3
    assert not BD.route_ok(parts, b, b.routes[0])
    assert len(BD.ratsnest(parts, b, nodes)) == 1


def test_routes_survive_json():
    r = BD.Route(("A", 0), ("B", 1), [(0, 0), (5, 0), (5, 5)], 1.1, [3.3], 0.5, 0, 3.3, 0, 50, "G1", 90,
                 [("A", 0)], [("B", 1), ("C", 0)], [(0, 0), (5, 5)])
    b = BD.Board({"A": BD.Placement()}, [], [r], {"z0": 50, "style": "Arc"})
    b2 = BD.Board.from_json(b.to_json())
    assert b2.routes[0] == r and b2.opts == b.opts


def _wizard_block(key):
    from hertzon.model import structures as ST
    from hertzon.model.design import Design, Project, circuit_setup
    p = Project()
    res = ST.create_structure(key)
    p.designs += res.designs
    p.materials += res.materials
    d3 = next(x for x in res.designs if getattr(x, "kind", "3d") == "3d")
    dc = next(x for x in res.designs if getattr(x, "kind", "3d") == "circuit")
    d = Design(name="System", kind="circuit")
    d.setups = [circuit_setup()]
    sub = C("Sub1", "SUBST", -200, 200, props=dict(CK.TYPES["SUBST"].props))
    sub.props.update(Er="3.66", H="0.508mm", T="35um", TanD="0.0037")
    em = C("EM1", "EM", 0, 0, 0, {"Design": d3.name, "Ports": str(len(d3.ports())), CK.MODEL: CK.MODEL_CIRCUIT,
                                  CK.CIRCUIT_MODEL: dc.name})
    d.components = [sub, em]
    p.designs.append(d)
    return p, d, em


def test_rat_race_block_is_a_board_part_with_slanted_ports_and_its_ring():
    p, d, em = _wizard_block("ratrace")
    for k, (px, py) in enumerate(CK.pins(em)):
        d.components.append(C(f"Port{k + 1}", "PORT", px, py + 40, 0, {"Z0": "50ohm", "Num": str(k + 1)}))
    parts, nodes, notes = BD.board_parts(p, d)
    bp = parts["EM1"]
    assert not notes and bp.type == "EM" and len(bp.pins) == 4
    slanted = [q.d for q in bp.pins if not isinstance(q.d[0], int)]
    assert len(slanted) == 2 and all(q[1] == pytest.approx(-math.sqrt(3) / 2) for q in slanted)
    assert len(bp.copper) == 1 and len(bp.copper[0].holes) == 1               # the ring and its inside
    # placed apart, connected and laid out in 3D: the block's own copper comes with its variables
    b = BD.Board({"EM1": BD.Placement(0, 0, 90)})
    for k in range(4):
        x, y = b.placements["EM1"].world((bp.pins[k].x, bp.pins[k].y))
        dx, dy = b.placements["EM1"].wvec(bp.pins[k].d)
        b.placements[f"Port{k + 1}"] = BD.Placement(round(x + 12 * dx), round(y + 12 * dy), 0)
    b, warnings = RT.connect(p, d, b, 50.0, "Arc", f=2.45e9)
    assert len(b.routes) == 4 and BD.ratsnest(parts, b, nodes) == []
    assert BD.overlaps(parts, b, nodes) == []
    lay, w = BD.layout_3d(p, d, b)
    names = {o.name for o in lay.objects}
    assert {"EM1_Ring", "Route1", "Route4"} <= names and "EM1_Port1" not in names
    assert [a.name for a in lay.excitations] == ["1", "2", "3", "4"]
    assert "EM1_Rm" in lay.variables


def test_overlapping_blocks_from_auto_place_are_spread():
    p, d, em = _wizard_block("stepped")
    em2 = C("EM2", "EM", 0, 200, 0, dict(em.props))
    d.components.append(em2)
    x1, y1 = CK.pins(em)[1]
    x2, y2 = CK.pins(em2)[0]
    d.components += [C("Port1", "PORT", CK.pins(em)[0][0], CK.pins(em)[0][1] + 40, 0, {"Z0": "50ohm"}),
                     C("N1", "LABEL", x1, y1, 0, {"Net": "m"}), C("N2", "LABEL", x2, y2, 0, {"Net": "m"}),
                     C("Port2", "PORT", CK.pins(em2)[1][0], CK.pins(em2)[1][1] + 40, 0, {"Z0": "50ohm"})]
    parts, nodes, _ = BD.board_parts(p, d)
    b, _ = BD.auto_place(p, d)
    assert BD.overlaps(parts, b, nodes) == []


def test_bend_radius_and_miter_set_by_hand():
    p, d, b = _two_ports()
    b.placements["Port2"] = BD.Placement(30.0, 12.0, 180)
    b, _ = RT.connect(p, d, b, 50.0, "Arc", f=2e9)
    r = b.routes[0]
    assert len(r.radii) == 2 and RT.corner_at(r, r.pts[1]) == 0
    assert RT.set_bend(r, [0], 2.0) == [] and r.radii[0] == pytest.approx(2.0)
    assert RT.set_bend(r, [1], 0.0) == [] and r.radii[1] == 0.0                     # mitered
    assert sum(1 for t, _ in RT.elements(r) if t == "MMBEND") == 1
    w = RT.set_bend(r, [0], 50.0)                                                    # no room: made smaller
    assert w and r.radii[0] < 50.0 and all(p_ >= -1e-6 for p_ in RT.pieces(r))
