"""The board of a circuit design (model/board.py): placement, ratsnest, overlaps, the 3D layout of a placed board."""
import json

import pytest
from test_circuit_layout import C, P, _box, _circuit, _line

from hertzon.model import board as BD
from hertzon.model import circuit_layout as CL


def _amp():
    """Port, line, a transistor on SOT-23 with its emitter line to a via, line, port."""
    return _circuit(P("Port1", 60, -10), _line("MS1", 100, -10, L="5mm", W="0.6mm"),
                    C("Q1", "DEVICE", 200, 0, 0, {"Kind": "Verilog-A", "Pins": "b c e", "Footprint": "SOT-23",
                                                  "Pads": "b=1; c=3; e=2"}),
                    _line("MS2", 300, 0, L="5mm", W="0.6mm"), P("Port2", 340, 0),
                    _line("MS3", 140, 50, rot=90, L="1mm", W="0.6mm"),
                    C("V1", "MVIA", 140, 110, 0, {"Subst": "Sub1", "D": "0.3mm", "N": "1", "P": "1mm"}))


def test_auto_place_follows_the_schematic_and_leaves_nothing_open():
    p, d = _amp()
    parts, nodes, notes = BD.board_parts(p, d)
    b, more = BD.auto_place(p, d)
    assert not notes and not more and set(b.placements) == set(parts)
    pl = b.placements
    assert (pl["Q1"].x, pl["Q1"].y) == pytest.approx((6.6, -0.95))      # pad 1 (b) at the end of MS1
    assert (pl["MS2"].x, pl["MS2"].y) == pytest.approx((8.2, -0.95))    # MS2 leaves pad 3 (c)
    assert pl["MS3"].rot == 270                                         # MS3 goes down from pad 2 (e)
    assert BD.ratsnest(parts, b, nodes) == [] and BD.overlaps(parts, b, nodes) == []
    assert BD.Board.from_json(b.to_json()).to_json() == b.to_json()
    BD.store(d, b)
    assert BD.load(d).placements["Q1"].x == pytest.approx(6.6)


def test_ratsnest_tracks_and_overlaps():
    p, d = _amp()
    parts, nodes, _ = BD.board_parts(p, d)
    b, _ = BD.auto_place(p, d)
    b.placements["MS2"].y -= 2
    b.placements["Port2"].y -= 2
    rats = BD.ratsnest(parts, b, nodes)
    assert len(rats) == 1 and rats[0][1:] == ((8.2, -0.95), (8.2, -2.95))   # pad 3 to the moved line
    b.tracks.append(BD.Track([(8.2, -0.95), (8.2, -2.95)], 0.6))
    assert BD.ratsnest(parts, b, nodes) == []                                # the track closes it
    assert BD.overlaps(parts, b, nodes) == []           # pad 1 (b) on the end of MS1: a line joins both its nets
    b.placements["Q1"].x, b.placements["Q1"].y = 2.5, 0.0
    assert ("MS1", "Q1") in BD.overlaps(parts, b, nodes)                     # pad 3 (c) on MS1


def test_complete_places_parts_added_to_the_schematic_and_drops_removed_ones():
    p, d = _amp()
    b, _ = BD.auto_place(p, d)
    b.placements["Gone"] = BD.Placement(1, 2, 0)
    del b.placements["V1"]
    c, _ = BD.complete(p, d, b)
    assert "Gone" not in c.placements and "V1" in c.placements


def test_placed_board_is_the_3d_layout_with_the_same_ports():
    """A circuit with a stored board is laid out as placed; the ports and the port map are those of the
    automatic layout, so Board Circuit works the same; the layout meshes."""
    from hertzon.mcp.tools import tessellate
    from hertzon.model.validate import validate
    p, d = _amp()
    b, _ = BD.auto_place(p, d)
    b.placements["MS2"].y -= 2
    b.placements["Port2"].y -= 2
    b.tracks.append(BD.Track([(8.2, -0.95), (8.2, -2.95)], 0.6))
    BD.store(d, b)
    L, warn = CL.layout_design(p, d, "L")
    p.designs.append(L)
    assert warn == ["1 track(s) drawn on the board are not in the schematic"]
    assert [e.name for e in L.ports()] == ["1", "2", "Q1_b_pad1", "Q1_e_pad2", "Q1_c_pad3"]
    pm = json.loads(L.settings[CL.PORT_MAP])
    assert pm["3"] == {"part": "Q1", "terminal": "b", "pad": "1"} and pm["5"]["pad"] == "3"
    assert _box(L, p, "MS2")[0] == pytest.approx(8.2)
    tess, err = tessellate(p, L)
    assert not err
    assert all(c.level == "ok" for c in validate(p, L, tess, err))
    _, warn = CL.board_circuit(p, L)
    assert not warn


def test_board_editor_moves_snaps_turns_and_draws_tracks_with_undo():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.board_editor import BoardEditor, to_scene
    from hertzon.gui.doc import Document
    p, d = _amp()
    doc = Document(p)
    doc.set_design("Net")
    ed = BoardEditor(doc)
    ed.rebuild()
    assert set(ed.items) == {"Port1", "MS1", "Q1", "MS2", "Port2", "MS3", "V1"}
    assert "All connections made" in ed.status.text()
    # MS2 dragged 2 mm down: both its ends open, stored as one undo step
    ed.items["MS2"].setPos(to_scene(8.2, -2.95))
    ed.release()
    assert BD.load(d).placements["MS2"].y == pytest.approx(-2.95)
    assert "2 connection(s) missing" in ed.status.text()          # to pad 3 and to Port2
    # dropped near its pad again it snaps onto it
    ed.items["MS2"].setPos(to_scene(8.35, -1.1))
    assert ed.items["MS2"].placement().x == pytest.approx(8.2)
    assert ed.items["MS2"].placement().y == pytest.approx(-0.95)
    ed.release()
    # a track
    ed.track_btn.setChecked(True)
    ed.toggle_track()
    ed._track_pts = [(8.2, -0.95), (8.2, -2.0)]
    ed.track_finish()
    assert BD.load(d).tracks[0].pts == [(8.2, -0.95), (8.2, -2.0)] and len(ed.board.tracks) == 1
    # turning
    ed.items["V1"].setSelected(True)
    ed.rotate_selected()
    assert BD.load(d).placements["V1"].rot == 180
    doc.undo()
    ed.rebuild()
    assert BD.load(doc.design).placements["V1"].rot == 90 and ed.items["V1"].placement().rot == 90


def test_shunt_part_on_its_footprint_gets_ground_vias_and_board_circuit_a_dc_model():
    """A capacitor to ground on a footprint gets vias in its ground pad (reported); Board Circuit makes the
    schematic without its devices the circuit model of the layout block, with a port where each layout port is,
    so that the bias point has the DC paths of the board."""
    import numpy as np
    from hertzon.model import circuit as CK
    from hertzon.solver.circuit import circuit_ports
    p, d = _amp()
    d.components.append(C("C9", "C", 140, -80, 90, {"C": "1pF", "Footprint": "C_0603"}))
    d.components.append(C("G9", "GND", 140, -40))
    d.components.append(_line("MS9", 140, -140, rot=90, L="1mm", W="0.6mm"))
    parts, nodes, notes = BD.board_parts(p, d)
    assert len(parts["C9"].vias) == 1 and any("C9: its ground pad has 1 via of 0.3 mm" in n for n in notes)
    assert nodes["C9"][1] == 0 and parts["C9"].vias[0][3] == 1                # in the pad of pin 2
    L, _ = CL.layout_design(p, d, "L")
    p.designs.append(L)
    B, _ = CL.board_circuit(p, L)
    model = p.design(B.components[0].props[CK.CIRCUIT_MODEL])
    ports, nodes_, _ = circuit_ports(p, model)
    assert [c.props["Num"] for c in ports] == [e.props["PortNumber"] for e in L.ports()]
    assert not any(c.type in CK.NONLINEAR_TYPES for c in model.components) and len(set(nodes_)) == len(nodes_)
    from hertzon.solver.circuit import linear_network
    assert linear_network(p, model, np.array([0.0])) is not None                   # it evaluates at DC
