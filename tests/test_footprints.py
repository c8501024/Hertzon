"""KiCad footprints and symbols of circuit parts: reading, lookup, built-in packages, pin-to-pad assignment."""
import pytest

from hertzon.model import circuit as CK
from hertzon.model import footprints as F
from hertzon.model import kicad as K

FOOTPRINT = r'''(footprint "Test_SOT"
  (version 20240108) (generator "pcbnew")
  (descr "test \"package\"")
  (attr smd)
  (fp_line (start -1 -1.2) (end 1 -1.2) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
  (fp_rect (start -1.5 -1.5) (end 1.5 1.5) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (pad "1" smd roundrect (at -1 -0.65) (size 0.9 0.45) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
  (pad "2" smd rect (at -1 0.65 90) (size 0.9 0.45) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "3" smd custom (at 1 0) (size 0.5 0.5) (layers "F.Cu" "F.Mask")
    (primitives (gr_poly (pts (xy 0 -0.5) (xy 0.6 -0.5) (xy 0.6 0.5) (xy 0 0.5)) (width 0) (fill yes))))
  (pad "" smd roundrect (at 1 0) (size 0.3 0.3) (layers "F.Paste"))
  (pad "3" thru_hole circle (at 1.2 0) (size 0.5 0.5) (drill 0.25) (layers "*.Cu"))
  (model "${KICAD9_3DMODEL_DIR}/x.wrl")
)
'''

SYMBOLS = r'''(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")
  (symbol "Q_NPN" (property "Footprint" "Lib:Test_SOT" (at 0 0 0))
    (symbol "Q_NPN_0_1" (polyline (pts (xy 0 0) (xy 1 1))))
    (symbol "Q_NPN_1_1"
      (pin input line (at -5 0 0) (length 2) (name "B" (effects (font (size 1 1)))) (number "3"))
      (pin passive line (at 2 5 270) (length 2) (name "C") (number "1"))
      (pin passive line (at 2 -5 90) (length 2) (name "E") (number "2"))
      (pin passive line (at 3 -5 90) (length 2) (name "E") (number "2"))))
  (symbol "Q_DERIVED" (extends "Q_NPN"))
)
'''


def test_reader_converts_to_y_up_and_reads_every_pad_kind(tmp_path):
    f = tmp_path / "Test_SOT.kicad_mod"
    f.write_text(FOOTPRINT)
    fp = K.read_footprint(f)
    assert fp.name == "Test_SOT" and fp.description == 'test "package"' and fp.smd and fp.model.endswith("x.wrl")
    assert fp.courtyard == (-1.5, -1.5, 1.5, 1.5)
    p1, p2, p3 = fp.pads[:3]
    assert (p1.x, p1.y, p1.shape, p1.rratio) == (-1, 0.65, "roundrect", 0.25)      # KiCad y down -> y up
    xs = [q[0] for q in p2.outline()]
    assert max(xs) - min(xs) == pytest.approx(0.45)                                # turned by 90 degrees
    assert p3.shape == "custom" and len(p3.polygons()) == 2 and p3.bbox()[2] == pytest.approx(1.6)
    assert not fp.pads[3].copper                                                   # paste only
    assert fp.pads[4].plated_hole and fp.pads[4].copper and fp.pads[4].drill == 0.25
    assert fp.numbers() == ["1", "2", "3"] and len(fp.pads_of("3")) == 2


def test_symbol_pins_units_and_derived_symbols(tmp_path):
    f = tmp_path / "Lib.kicad_sym"
    f.write_text(SYMBOLS)
    s = K.read_symbols(f)
    assert s["Q_NPN"].pins == [("3", "B"), ("1", "C"), ("2", "E")]            # a repeated number counts once
    assert s["Q_NPN"].footprint == "Lib:Test_SOT"
    assert s["Q_DERIVED"].pins == s["Q_NPN"].pins


def test_parser_reports_broken_files():
    with pytest.raises(K.KicadError):
        K.parse('(footprint "x" (pad "1"')
    with pytest.raises(K.KicadError):
        K.footprint_from_sexpr(K.parse("(kicad_pcb)"))


def test_lookup_in_library_folders_and_built_in_fallback(tmp_path, monkeypatch):
    (tmp_path / "Lib.pretty").mkdir()
    (tmp_path / "Lib.pretty" / "Test_SOT.kicad_mod").write_text(FOOTPRINT)
    (tmp_path / "Sym.kicad_symdir").mkdir()
    (tmp_path / "Sym.kicad_symdir" / "Q_NPN.kicad_sym").write_text(SYMBOLS.replace('(symbol "Q_DERIVED" (extends "Q_NPN"))', ""))
    (tmp_path / "Sym.kicad_symdir" / "Q_D.kicad_sym").write_text(
        '(kicad_symbol_lib (symbol "Q_D" (extends "Q_NPN")))')
    monkeypatch.setenv("HERTZON_KICAD_LIBS", str(tmp_path))
    monkeypatch.setattr(F, "_install_dirs", lambda kind: [])
    assert F.find_footprint("Lib:Test_SOT").source.endswith("Test_SOT.kicad_mod")
    assert F.find_symbol("Sym:Q_D").pins[0] == ("3", "B")                  # parent in another file of the symdir
    fp = F.find_footprint("Capacitor_SMD:C_0402_1005Metric")
    assert fp.source.startswith(F.BUILTIN) and "not found" in fp.source
    assert [(p.x, p.w, p.h) for p in fp.pads] == [(-0.4, 0.4, 0.5), (0.4, 0.4, 0.5)]   # gap 0.4 mm
    with pytest.raises(K.KicadError):
        F.find_footprint("Lib:Missing_Package")
    # a file relative to the project folder
    assert F.find_footprint("Lib.pretty/Test_SOT.kicad_mod", tmp_path).name == "Test_SOT"


def test_qfn_numbering_is_counter_clockwise_from_the_top_of_the_left_side():
    fp = F.builtin("QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm")
    pos = {p.number: (round(p.x, 3), round(p.y, 3)) for p in fp.pads}
    assert pos["1"][0] < 0 and pos["1"][1] == 0.75 and pos["4"][1] == -0.75     # left side, top down
    assert pos["5"][1] < 0 and pos["5"][0] == -0.75                           # bottom, left to right
    assert pos["9"][0] > 0 and pos["9"][1] == -0.75                           # right side, bottom up
    assert pos["13"][1] > 0 and pos["13"][0] == 0.75                          # top, right to left
    assert pos["17"] == (0, 0) and fp.pads[-1].w == 1.7


def test_pads_from_symbol_names_with_two_emitters_and_an_exposed_pad(tmp_path):
    f = tmp_path / "Lib.kicad_sym"
    f.write_text(SYMBOLS)
    sym = K.read_symbols(f)["Q_NPN"]
    fp = F.builtin("SOT-343_SC-70-4")
    sym4 = K.Symbol("NSVF", [("1", "C"), ("2", "E"), ("3", "B"), ("4", "E")])
    m = F.auto_pads(["b", "c", "e1", "e2"], fp, sym4)
    assert m == {"b": ["3"], "c": ["1"], "e1": ["2"], "e2": ["4"]}
    assert F.check("Q1", ["b", "c", "e1", "e2"], fp, m).errors == []
    # one emitter terminal takes both emitter pads
    assert F.auto_pads(["c", "b", "e"], fp, sym4)["e"] == ["2", "4"]
    # a MOSFET in a QFN whose symbol does not name the exposed pad: it goes with the source
    qfn = F.builtin("QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm")
    msym = K.Symbol("M", [("1", "S"), ("2", "S"), ("3", "S"), ("4", "G")] + [(str(k), "D") for k in range(5, 17)])
    m = F.auto_pads(["d", "g", "s"], qfn, msym)
    assert m["s"] == ["1", "2", "3", "17"] and m["g"] == ["4"] and len(m["d"]) == 12
    assert F.auto_pads(["1", "2"], F.builtin("R_0603"), None) == {"1": ["1"], "2": ["2"]}
    assert sym.pins  # the test library reads


def test_check_reports_missing_double_and_open_pads():
    fp = F.builtin("SOT-23")
    r = F.check("Q1", ["c", "b", "e"], fp, {"c": ["3"], "b": ["1", "9"], "x": ["2"], "e": []})
    text = " ".join(r.errors)
    assert "no pad '9'" in text and "no terminal 'x'" in text and "terminal e has no pad" in text
    r = F.check("Q1", ["c", "b", "e"], fp, {"c": ["3"], "b": ["1"], "e": ["3"]})
    assert any("both c and e" in e for e in r.errors) and "pad 2" in r.warnings[0]
    r = F.check("Q1", ["c", "b"], fp, F.parse_pads("c=3; b=1; NC=2"))
    assert not r.errors and not r.warnings


def test_part_on_board_and_project_paths(tmp_path):
    (tmp_path / "fp").mkdir()
    (tmp_path / "fp" / "Test_SOT.kicad_mod").write_text(FOOTPRINT)
    c = CK.Component("Q1", "DEVICE", props={"Pins": "c b e", "Footprint": "fp/Test_SOT.kicad_mod",
                                             "Pads": "c=1; b=3; e=2"})
    b = F.on_board(c, tmp_path)
    assert b and not b.automatic and b.terminal_of("3") == "b" and not b.check.errors
    assert F.on_board(CK.Component("R1", "R", props={"R": "1ohm"})) is None
    # a relative footprint file is made absolute when the project is opened
    from hertzon.model.design import Design, Project
    p = Project()
    d = Design(name="C", kind="circuit")
    d.components.append(c)
    p.designs.append(d)
    p.save(tmp_path / "x.hzn")
    q = Project.load(tmp_path / "x.hzn").design("C").components[0]
    assert q.props["Footprint"] == str(tmp_path / "fp" / "Test_SOT.kicad_mod")


def test_old_module_format_and_numbered_pin_names_of_vendor_libraries(tmp_path):
    """Library downloads often come in the KiCad 5 format (module, unquoted pad numbers) and name the pins of a
    transistor with two emitters EMITTER_1 and EMITTER_2."""
    f = tmp_path / "Q.kicad_mod"
    f.write_text('(module "Q" (layer F.Cu) (attr smd)\n'
                 '  (fp_line (start -2 -2.35) (end 2 -2.35) (layer F.CrtYd) (width 0.1))\n'
                 '  (pad 1 smd rect (at -0.650 1.05 0) (size 0.400 0.600) (layers F.Cu F.Paste F.Mask))\n'
                 '  (pad 2 smd rect (at 0.650 1.05 0) (size 0.400 0.600) (layers F.Cu F.Paste F.Mask))\n'
                 '  (pad 3 smd rect (at 0.650 -1.05 0) (size 0.400 0.600) (layers F.Cu F.Paste F.Mask))\n'
                 '  (pad 4 smd rect (at -0.650 -1.05 0) (size 0.400 0.600) (layers F.Cu F.Paste F.Mask)))\n')
    fp = K.read_footprint(f)
    assert fp.numbers() == ["1", "2", "3", "4"] and (fp.pads[0].x, fp.pads[0].y) == (-0.65, -1.05)
    sym = K.Symbol("Q", [("1", "COLLECTOR"), ("2", "EMITTER_1"), ("3", "BASE"), ("4", "EMITTER_2")])
    assert F.auto_pads(["b", "c", "e"], fp, sym) == {"b": ["3"], "c": ["1"], "e": ["2", "4"]}
    assert F.auto_pads(["b", "c", "e1", "e2"], fp, sym) == {"b": ["3"], "c": ["1"], "e1": ["2"], "e2": ["4"]}


def test_sc82fl_is_onsemis_recommended_footprint():
    """SC-82FL / MCPH4 (onsemi case 419AR): pads 0.40 x 0.60 mm, 1.30 mm and 2.10 mm apart (centre to centre),
    pin 1 bottom left seen from the top, counter-clockwise."""
    fp = F.builtin("SC-82FL_MCPH4")
    assert [(p.number, p.x, p.y, p.w, p.h) for p in fp.pads] == [
        ("1", -0.65, -1.05, 0.4, 0.6), ("2", 0.65, -1.05, 0.4, 0.6), ("3", 0.65, 1.05, 0.4, 0.6),
        ("4", -0.65, 1.05, 0.4, 0.6)]
    assert F.builtin("MCPH4").name == fp.name and "SC-82FL_MCPH4" in F.BUILTIN_NAMES
