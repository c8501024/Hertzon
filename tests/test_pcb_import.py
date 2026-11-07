"""PCB import (File > Import PCB): Gerber, Excellon and job files become copper, vias, holes and substrate.

The Gerber reader is checked against the Gerber export of Hertzon (round trip with known areas), against
an independent renderer (pygerber) on a KiCad file, and on hand written files with old and rare parts of
the format. The import is checked on the model it makes (OCC build: areas, volumes, heights)."""
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from hertzon.model import stackup as S  # noqa: E402
from hertzon.model.design import Project  # noqa: E402
from hertzon.pcbimport import board as B  # noqa: E402
from hertzon.pcbimport import excellon as E  # noqa: E402
from hertzon.pcbimport import gerber as R  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

IN2 = 0.0254 ** 2
MM2 = 1e-6

LEGACY = {
    # inch, trailing zeros omitted, G54 prefixes, a macro with a variable and a clear primitive, a region with
    # a clear flash on it, step and repeat, a single quadrant arc
    "board.GTL": """G04 legacy top layer, no X2 attributes*
%FSTAX24Y24*%
%MOIN*%
%AMDONUT*
0 outer diameter $1, the hole is half of it*
1,1,$1,0,0*
$2=$1x0.5*
1,0,$2,0,0*%
%ADD10C,0.010*%
%ADD11R,0.100X0.050*%
%ADD12DONUT,0.2*%
%ADD13C,0.2*%
G54D11*
X01Y01D03*
G54D12*
X02Y01D03*
G36*
X03Y005D02*
X035Y005D01*
X035Y01D01*
X03Y01D01*
X03Y005D01*
G37*
%LPC*%
G54D13*
X0325Y0075D03*
%LPD*%
%SRX2Y1I0.5J0*%
G54D11*
X01Y02D03*
%SR*%
G54D10*
G74*
X01Y03D02*
G03X015Y025I005J0D01*
M02*
""",
    # metric, a region with a cut-in (as CAD tools write planes with holes)
    "board.GBL": """%FSLAX46Y46*%
%MOMM*%
G36*
X0Y0D02*
X10000000Y0D01*
X10000000Y10000000D01*
X0Y10000000D01*
X0Y5000000D01*
X4000000Y5000000D01*
X4000000Y6000000D01*
X6000000Y6000000D01*
X6000000Y4000000D01*
X4000000Y4000000D01*
X4000000Y5000000D01*
X0Y5000000D01*
X0Y0D01*
G37*
M02*
""",
    "board.GKO": """%FSLAX24Y24*%
%MOIN*%
%ADD10C,0.01*%
D10*
X0Y0D02*
X40000Y0D01*
X40000Y40000D01*
X0Y40000D01*
X0Y0D01*
M02*
""",
    "board.GTO": """%FSLAX24Y24*%
%MOIN*%
%ADD10C,0.01*%
D10*
X1000Y1000D02*
X3000Y1000D01*
M02*
""",
    # Protel drill file: inch, leading zeros kept, no decimal point, a G85 slot, plating not stated
    "board.TXT": """M48
INCH,LZ
T1C0.0120
T2C0.1250
%
T1
X010000Y035000
T2
X035000Y035000
X020000Y035000G85X030000Y035000
M30
""",
    # KiCad drill file: metric with decimals, a routed slot
    "board-NPTH.drl": """M48
; DRILL file {KiCad 8.0} date 2026-11-05
; FORMAT={-:-/ absolute / metric / decimal}
; #@! TF.FileFunction,NonPlated,1,2,NPTH
FMAT,2
METRIC
T1C3.200
%
G90
G05
T1
X10.0Y10.0
G00X20.0Y10.0
M15
G01X25.0Y10.0
M16
G05
M30
""",
}
LEGACY_TOP_IN2 = 0.005 + math.pi * (0.01 - 0.0025) + (0.25 - math.pi * 0.01) + 0.01 + (math.pi / 4 * 0.01 + math.pi * 0.005 ** 2)


def _write(folder: Path, files: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for n, t in files.items():
        (folder / n).write_text(t, encoding="utf-8")
    return folder


def _area(dt) -> float:
    import gmsh
    return sum(gmsh.model.occ.getMass(d, t) for d, t in dt)


def _bbox(dt):
    import gmsh
    bb = [gmsh.model.occ.getBoundingBox(d, t) for d, t in dt]
    return [min(b[i] for b in bb) for i in range(3)] + [max(b[i] for b in bb) for i in range(3, 6)]


def _build(d, p):
    from hertzon.kernel import occ as K
    K.ensure_gmsh()
    ctx = K.build_design(d, d.evaluator(p))
    assert not ctx.errors, ctx.errors
    return ctx.built


# ---- reader -----------------------------------------------------------------------------------
def test_legacy_gerber_features(tmp_path):
    files, job, _ = B.load([_write(tmp_path / "legacy", LEGACY)], segments=360)
    by = {f.name: f for f in files}
    assert job is None
    assert (by["board.GTL"].kind, by["board.GTL"].layer, by["board.GTL"].how) == (B.COPPER, 1, "file name")
    assert (by["board.GBL"].kind, by["board.GBL"].layer) == (B.COPPER, 2)      # bottom = last layer
    assert by["board.GKO"].kind == B.PROFILE and by["board.GTO"].kind == B.LEGEND
    assert by["board.GTL"].image.geometry.area / IN2 == pytest.approx(LEGACY_TOP_IN2, rel=2e-4)
    assert by["board.GBL"].image.geometry.area / MM2 == pytest.approx(96, rel=1e-9)
    assert len(by["board.GBL"].image.polygons()[0].interiors) == 1               # the cut-in is a hole
    assert any("trailing zero" in m.text for m in by["board.GTL"].image.messages)
    out = R.outline_polygons(by["board.GKO"].image)
    assert len(out) == 1 and out[0].area / IN2 == pytest.approx(16, rel=1e-9)    # centre of the drawn line
    import zipfile
    z = tmp_path / "board.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n, t in LEGACY.items():
            zf.writestr(f"gerber/{n}", t)
    zfiles, _, _ = B.load([z], segments=360)
    assert sorted((f.name, f.label) for f in zfiles) == sorted((f.name, f.label) for f in files)


def test_excellon_variants(tmp_path):
    d = E.read(LEGACY["board.TXT"], "board.TXT")
    assert [(round(h.x / 0.0254, 6), round(h.y / 0.0254, 6), round(h.d / 0.0254, 6)) for h in d.holes] == [
        (1.0, 3.5, 0.012), (3.5, 3.5, 0.125), (2.0, 3.5, 0.125)]
    assert d.holes[2].slot and d.holes[2].x1 == pytest.approx(3 * 0.0254)
    assert all(h.plated for h in d.holes) and not d.plated_known
    assert any("plated" in m.text for m in d.messages)
    n = E.read(LEGACY["board-NPTH.drl"], "board-NPTH.drl")
    assert [h.plated for h in n.holes] == [False, False] and n.plated_known and n.span == (1, 2)
    assert (n.holes[1].x, n.holes[1].x1) == pytest.approx((20e-3, 25e-3))         # routed slot
    k = E.read("M48\n;TYPE=PLATED\nT1C0.3\n;TYPE=NON_PLATED\nT2C1.0\n%\nMETRIC\nT1\nX1.0Y1.0\nT2\nX2.0Y2.0\nM30\n")
    assert [h.plated for h in k.holes] == [True, False]


def test_kicad_file_against_independent_renderer():
    """A KiCad 7 copper layer (round rect macros, ovals, regions): area as rendered by pygerber."""
    ex = pytest.importorskip("pygerber")
    f = Path(ex.__file__).parent / "examples" / "simple_2layer-F_Cu.gbr"
    if not f.exists():
        pytest.skip("pygerber examples not installed")
    img = R.read_file(f, 128)
    assert img.function == ["Copper", "L1", "Top"]
    assert not [m for m in img.messages if m.level != "info"]
    # pygerber raster (40 px/mm) gives 1654.96 mm^2, the difference is its pixel edge
    assert img.geometry.area / MM2 == pytest.approx(1655.0, rel=0.005)


def test_macro_primitives_and_transforms():
    text = """%FSLAX46Y46*%
%MOMM*%
%AMTH*7,0,0,4,2,0.5,0*%
%AMOUT*4,1,4,0,0,2,0,2,1,0,1,0,0,0*%
%AMVL*20,1,1,0,0,4,0,0*21,1,1,3,2,0,0*%
%ADD10TH*%
%ADD11OUT*%
%ADD12VL*%
%ADD13P,2X4X0*%
%ADD14O,3X1*%
D10*
X0Y0D03*
D11*
X10000000Y0D03*
%LR90*%
X20000000Y0D03*
%LR0*%
D12*
X30000000Y0D03*
D13*
X40000000Y0D03*
D14*
X50000000Y0D03*
M02*
"""
    img = R.read(text, 720)
    polys = sorted(img.polygons(), key=lambda p: p.bounds[0])
    ring = math.pi * (4 - 1)
    thermal = ring - 4 * 0.5 * 1                                    # four gaps about 0.5 x 1 cut from the ring
    areas = [p.area / MM2 for p in polys]
    assert sum(areas[:4]) == pytest.approx(thermal, rel=0.01)       # the thermal has four parts
    assert areas[4] == pytest.approx(2.0) and areas[5] == pytest.approx(2.0)
    rot = polys[5].bounds
    assert (rot[2] - rot[0]) * 1e3 == pytest.approx(1.0) and (rot[3] - rot[1]) * 1e3 == pytest.approx(2.0)
    assert areas[6] == pytest.approx(4 + 1 * 3 - 1 * 1)             # vector + centre line, overlap once
    assert areas[7] == pytest.approx(2.0)                            # square of diagonal 2
    assert areas[8] == pytest.approx(2 * 1 + math.pi * 0.25, rel=1e-4)


# ---- import -----------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    from test_gerber import board_project
    from hertzon.export import gerber as G
    p = board_project()
    out = tmp_path_factory.mktemp("export")
    G.write(G.run(p, p.designs[0], G.Options()), out, "board")
    return out


def test_round_trip_of_the_gerber_export(exported):
    from test_gerber import BOTTOM_AREA, TOP_AREA
    files, job, jn = B.load([exported], segments=360)
    pl = B.plan(files, job, jn)
    assert [f.kind for f in files].count(B.COPPER) == 2 and pl.profile() is not None
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    rep = B.build(p, d, pl)
    assert rep.made == {"substrate": 1, "L1 Top": 8, "L2 Bottom": 1, "vias": 1, "holes": 1}
    assert any("export-report" in x for x in rep.not_loaded)
    built = _build(d, p)
    ev = d.evaluator(p)
    top = sum(_area(built[o.name]) for o in d.objects if o.group == "PCB L1 Top")
    bottom = sum(_area(built[o.name]) for o in d.objects if o.group == "PCB L2 Bottom")
    assert top / MM2 == pytest.approx(TOP_AREA, rel=5e-4)
    assert bottom / MM2 == pytest.approx(BOTTOM_AREA, rel=5e-4)
    t = ev.length("stk_Core_1_2_t")
    sub = (40 * 30 - 2 * 6 - math.pi * 1.6 ** 2 - math.pi * 0.4 ** 2) * MM2 * t
    assert _area(built["Sub_Core_1_2"]) == pytest.approx(sub, rel=1e-4)    # outline slot, NPTH and via cut out
    vb = _bbox(built["Via1"])
    assert vb[2] == pytest.approx(ev.length("stk_L2_Bottom_zs"), abs=1e-6)
    assert vb[5] == pytest.approx(ev.length("stk_L1_Top_zs"), abs=1e-6)
    assert (vb[0] + vb[3]) / 2 == pytest.approx(28e-3, abs=1e-6)
    assert _bbox(built["L1_Top_1"])[2] == pytest.approx(ev.length("stk_L1_Top_zs"), abs=1e-6)
    cu = [b for b in d.boundaries if b.kind == "Finite Conductivity"]
    assert {b.name for b in cu} == {"Cu_L1_Top", "Cu_L2_Bottom"} and cu[0].props["Thickness"] == "stk_L1_Top_t"
    assert not d.obj("Hole1").model


def test_import_replaces_the_earlier_one_and_follows_the_stackup(exported):
    files, job, jn = B.load([exported])
    p = Project()
    d = p.designs[0]
    B.build(p, d, B.plan(files, job, jn))
    n = len(d.objects)
    B.build(p, d, B.plan(files, job, jn))
    assert len(d.objects) == n and len(d.boundaries) == 2
    st = S.Stackup.from_dict(d.stackup.to_dict())
    st.layers[1].set("thickness", "0.8mm", "user")
    S.apply_to_design(p, d, st)
    sub = d.obj("Sub_Core_1_2")
    assert sub.history[0].kind == "CreatePolyline" and sub.history[-1].kind == "Subtract"
    built = _build(d, p)
    assert _bbox(built["Sub_Core_1_2"])[5] - _bbox(built["Sub_Core_1_2"])[2] == pytest.approx(0.8e-3, abs=1e-6)
    assert _bbox(built["L1_Top_1"])[2] == pytest.approx(0.835e-3, abs=1e-6)


def _rect(x0, y0, x1, y1) -> str:
    def c(v):
        return str(int(round(v * 1e6)))
    return (f"%FSLAX46Y46*%\n%MOMM*%\nG36*\nX{c(x0)}Y{c(y0)}D02*\nX{c(x1)}Y{c(y0)}D01*\nX{c(x1)}Y{c(y1)}D01*\n"
            f"X{c(x0)}Y{c(y1)}D01*\nX{c(x0)}Y{c(y0)}D01*\nG37*\nM02*\n")


def _four_layer(folder: Path) -> Path:
    from test_stackup import KICAD_JOB
    job = json.loads(json.dumps(KICAD_JOB))
    files = {"rf-F_Cu.gbr": _rect(100, 100, 110, 101), "rf-In1_Cu.gbr": _rect(100, 100, 150, 140),
             "rf-In2_Cu.gbr": _rect(120, 100, 150, 140), "rf-B_Cu.gbr": _rect(100, 100, 150, 140),
             "rf-F_Mask.gbr": _rect(100, 100, 101, 101), "rf-F_Silkscreen.gbr": _rect(100, 100, 101, 101),
             "rf-PTH.drl": "M48\n; #@! TF.FileFunction,Plated,1,4,PTH\nMETRIC\nT1C0.3\n%\nT1\nX105.0Y100.5\n"
                           "X148.0Y139.0\nM30\n",
             "rf-L1-L2-blind.drl": "M48\n; #@! TF.FileFunction,Plated,1,2,Blind\nMETRIC\nT1C0.2\n%\nT1\n"
                                   "X109.0Y100.5\nM30\n",
             "rf.gbrjob": json.dumps(job)}
    return _write(folder, files)


def test_four_layer_board_with_job_file_blind_vias_and_crop(tmp_path):
    files, job, jn = B.load([_four_layer(tmp_path / "rf")])
    by = {f.name: f for f in files}
    assert job is not None and jn == "rf.gbrjob"
    assert [(by[n].layer, by[n].how) for n in ("rf-F_Cu.gbr", "rf-In1_Cu.gbr", "rf-In2_Cu.gbr", "rf-B_Cu.gbr")] == [
        (1, "job file"), (2, "job file"), (3, "job file"), (4, "job file")]
    pl = B.plan(files, job, jn)
    assert len(pl.stackup.copper()) == 4 and pl.stackup.source == "job file rf.gbrjob"
    assert pl.origin == pytest.approx((0.1, 0.1))         # no outline: lower left of the copper
    pl.crop = (0.1, 0.1, 0.13, 0.12)                       # leaves out the through via at (148, 139)
    st = pl.stackup
    st.layers[st.copper()[0]].representation = S.SOLID      # top copper as a solid
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    rep = B.build(p, d, pl)
    assert rep.made["vias"] == 2 and any("not fully inside" in x for x in rep.not_loaded)
    assert any("rf-F_Mask.gbr" in x for x in rep.not_loaded) and any("Solder Mask" in x for x in rep.not_loaded)
    assert any("legend" in x for x in rep.not_loaded)
    built = _build(d, p)
    ev = d.evaluator(p)
    # cropped and moved: the board is 30 x 20 mm from the origin
    bb = _bbox(built["Sub_F_Cu_In1_Cu"])
    assert bb[:2] == pytest.approx([0, 0], abs=1e-6) and bb[3:5] == pytest.approx([0.03, 0.02], abs=1e-6)
    assert _area(built["In2_Cu_1"]) == pytest.approx(10e-3 * 20e-3, rel=1e-9)
    top = d.obj("F_Cu_1")
    assert top.material == "copper" and top.history[-1].kind == "ThickenSheet"
    tb = _bbox(built["F_Cu_1"])
    assert tb[2] == pytest.approx(ev.length("stk_F_Cu_z"), abs=1e-6)
    assert tb[5] - tb[2] == pytest.approx(35e-6, abs=1e-6)
    assert not any(b.objects == ["F_Cu_1"] for b in d.boundaries)          # solid: no sheet boundary
    vias = {o.name: _bbox(built[o.name]) for o in d.objects if o.group == B.VIA_GROUP}
    through = next(v for v in vias.values() if v[0] < 0.006)
    blind = next(v for v in vias.values() if v[0] > 0.008)
    assert through[2] == pytest.approx(ev.length("stk_B_Cu_zs"), abs=1e-6)
    assert blind[2] == pytest.approx(ev.length("stk_In1_Cu_zs"), abs=1e-6)
    assert blind[5] == pytest.approx(ev.length("stk_F_Cu_zs"), abs=1e-6)
    from hertzon.kernel import occ as K
    names = [o.name for o in d.objects if o.history[0].kind != "CreateRegion" and o.model
             and max(dd for dd, _ in built[o.name]) == 3]
    bad = [(a, b) for a, b, v, va, vb in K.solid_overlaps(d, ev, names)
           if d.obj(a).material != d.obj(b).material and v < min(va, vb) * (1 - 1e-6)]
    assert not bad, bad


def test_slot_holes_and_legacy_board(tmp_path):
    files, job, jn = B.load([_write(tmp_path / "legacy", LEGACY)], segments=96)
    pl = B.plan(files, job, jn)
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    rep = B.build(p, d, pl)
    assert rep.made["vias"] == 3 and rep.made["holes"] == 2
    assert any("plated" in w for w in rep.warnings)
    built = _build(d, p)
    slot = next(o for o in d.objects if o.group == B.VIA_GROUP and o.history[0].kind == "CreatePolyline")
    ev = d.evaluator(p)
    h = ev.length("stk_L1_Top_zs") - ev.length("stk_L2_Bottom_zs")
    r = 0.125 * 0.0254 / 2
    assert _area(built[slot.name]) == pytest.approx((math.pi * r * r + 2 * r * 0.0254) * h, rel=1e-6)


def test_import_dialog(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.pcb_import_dialog import PcbImportDialog
    app = QApplication.instance() or QApplication([])  # noqa: F841

    class Doc:
        def __init__(self, p):
            self.project, self.design = p, p.designs[0]

    p = Project()
    dlg = PcbImportDialog(None, Doc(p), [str(_four_layer(tmp_path / "rf"))])
    assert dlg.table.rowCount() == 8 and dlg.ok.isEnabled()
    text = dlg.report.toPlainText()
    assert "F.Cu (L1): rf-F_Cu.gbr" in text and "rf-F_Mask.gbr (solder mask)" in text
    # change what a file is: the silkscreen as copper of layer 3 replaces In2.Cu
    r = next(i for i, f in enumerate(dlg.files) if f.name == "rf-F_Silkscreen.gbr")
    cb = dlg.table.cellWidget(r, 1)
    cb.setCurrentIndex(2)
    assert dlg.plan.copper_files()[3].name == "rf-F_Silkscreen.gbr"
    old = next(f for f in dlg.files if f.name == "rf-In2_Cu.gbr")
    assert old.kind == B.OTHER and old.how == "replaced by rf-F_Silkscreen.gbr"
    dlg.crop_on.setChecked(True)
    for e, v in zip(dlg.crop_eds, ("100", "100", "130", "120")):
        e.setText(v)
    dlg._update()
    assert dlg.plan.crop == pytest.approx((0.1, 0.1, 0.13, 0.12))
    assert "cropped" in dlg.report.toPlainText()
    dlg.resize(1250, 820)
    dlg.show()
    app.processEvents()
    dlg.grab().save(str(tmp_path / "pcb_import.png"))
    dlg.close()


# ---- copper shapes that touch themselves --------------------------------------------------------
def _touching_shapes(eps: float) -> dict:
    """Valid polygons (shapely) that the kernel cannot close as they are: rings whose corners come closer
    than its tolerance (a net tie: two parts that meet in a point), a hole on the outline, two holes that
    touch, a slit."""
    m = 1e-3
    sq = [(0, 0), (10 * m, 0), (10 * m, 10 * m), (0, 10 * m)]
    return {
        "pinch": Polygon([(0, 0), (5 * m, 5 * m - eps), (10 * m, 0), (10 * m, 10 * m), (5 * m, 5 * m + eps),
                          (0, 10 * m)]),
        "slit": Polygon([(0, 0), (10 * m, 0), (10 * m, 10 * m), (5 * m + eps, 10 * m), (5 * m, 5 * m),
                         (5 * m - eps, 10 * m), (0, 10 * m)]),
        "hole_on_outline": Polygon(sq, [[(0, 5 * m), (3 * m, 4 * m), (3 * m, 6 * m)]]),
        "holes_touch": Polygon(sq, [[(2 * m, 2 * m), (5 * m, 2 * m), (5 * m, 5 * m)],
                                    [(5 * m + eps, 5 * m), (8 * m, 5 * m), (8 * m, 8 * m)]]),
    }


def _build_shape(q: Polygon):
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    d.add_object("CreatePolyline", "Cu", material="vacuum", Points=B._loop(q.exterior.coords, 1e-3, "0"),
                 Closed="True", Covered="True",
                 Holes="|".join(B._loop(h.coords, 1e-3, "0") for h in q.interiors))
    return _build(d, p)["Cu"]


@pytest.mark.parametrize("eps", [0.0, 1e-9, 5e-9, 1e-7])
def test_copper_shapes_that_touch_themselves_build(eps):
    for name, p in _touching_shapes(eps).items():
        qs, note = B.normalize(p)
        assert len(qs) == 1, name                        # parts that meet in a point stay one conductor
        q = qs[0]
        assert q.is_valid and not B.pinches(q), (name, note)
        assert len(q.interiors) == len(p.interiors) or name == "slit", name
        assert _area(_build_shape(q)) == pytest.approx(p.area, rel=1e-4), (name, note)


def test_touching_copper_in_a_gerber_file_is_imported(tmp_path):
    # top copper: two squares that meet in a corner (5 nm apart, as a net tie leaves them) in one region
    g = ("%FSLAX46Y46*%\n%MOMM*%\nG36*\nX0Y0D02*\nX5000000Y4999995D01*\nX10000000Y0D01*\nX10000000Y10000000D01*\n"
         "X5000000Y5000005D01*\nX0Y10000000D01*\nX0Y0D01*\nG37*\nM02*\n")
    files, job, jn = B.load([_write(tmp_path / "tie", {"tie-F_Cu.gbr": g, "tie-B_Cu.gbr": _rect(0, 0, 10, 10)})])
    pl = B.plan(files, job, jn)
    p = Project()
    d = p.designs[0]
    d.units = "mm"
    rep = B.build(p, d, pl)
    top = [n for n, (layer, _) in rep.shapes.items() if layer == pl.stackup.layers[pl.stackup.copper()[0]].name]
    assert len(top) == 1
    assert any("touching rings repaired" in x for x in rep.lines)
    built = _build(d, p)
    assert _area(built[top[0]]) == pytest.approx(50e-6, rel=1e-4)
    # a shape the kernel still cannot build is reported loudly, with its layer and area
    w = rep.check_built({top[0]: "Curve loop is not closed"})
    assert w and "MISSING" in w[0] and "50 mm²" in w[0] and w[0] in rep.warnings
