"""PCB stackup: model, checks with their suggested fixes, Gerber job files (also broken ones)."""
from __future__ import annotations

import json
import math

import pytest

from hertzon.model import stackup as S
from hertzon.model.design import Design, Project
from hertzon.model.expr import Evaluator

EV = Evaluator()

# as KiCad 8 writes it (shortened), 4 layers, 1.6 mm
KICAD_JOB = {
    "Header": {"GenerationSoftware": {"Vendor": "KiCad", "Application": "Pcbnew", "Version": "8.0.4"},
               "CreationDate": "2026-10-30T10:00:00+01:00"},
    "GeneralSpecs": {"ProjectId": {"Name": "rf"}, "Size": {"X": 50.0, "Y": 40.0}, "LayerNumber": 4,
                     "BoardThickness": 1.6},
    "FilesAttributes": [
        {"Path": "rf-F_Cu.gbr", "FileFunction": "Copper,L1,Top", "FilePolarity": "Positive"},
        {"Path": "rf-In1_Cu.gbr", "FileFunction": "Copper,L2,Inr", "FilePolarity": "Positive"},
        {"Path": "rf-In2_Cu.gbr", "FileFunction": "Copper,L3,Inr", "FilePolarity": "Positive"},
        {"Path": "rf-B_Cu.gbr", "FileFunction": "Copper,L4,Bot", "FilePolarity": "Positive"},
        {"Path": "rf-F_Mask.gbr", "FileFunction": "SolderMask,Top", "FilePolarity": "Negative"},
        {"Path": "rf-F_Silkscreen.gbr", "FileFunction": "Legend,Top", "FilePolarity": "Positive"}],
    "MaterialStackup": [
        {"Type": "Legend", "Name": "Top Silk Screen"},
        {"Type": "SolderPaste", "Name": "Top Solder Paste"},
        {"Type": "SolderMask", "Thickness": 0.01, "Name": "Top Solder Mask"},
        {"Type": "Copper", "Thickness": 0.035, "Name": "F.Cu"},
        {"Type": "Dielectric", "Thickness": 0.1, "Material": "FR4", "Name": "F.Cu/In1.Cu",
         "DielectricConstant": 4.5, "LossTangent": 0.02, "Notes": "Type: dielectric layer 1 (from F.Cu to In1.Cu)"},
        {"Type": "Copper", "Thickness": 0.035, "Name": "In1.Cu"},
        {"Type": "Dielectric", "Thickness": 1.24, "Material": "FR4", "Name": "In1.Cu/In2.Cu",
         "DielectricConstant": 4.5, "LossTangent": 0.02, "Notes": "Type: dielectric layer 2 (from In1.Cu to In2.Cu)"},
        {"Type": "Copper", "Thickness": 0.035, "Name": "In2.Cu"},
        {"Type": "Dielectric", "Thickness": 0.1, "Material": "FR4", "Name": "In2.Cu/B.Cu",
         "DielectricConstant": 4.5, "LossTangent": 0.02},
        {"Type": "Copper", "Thickness": 0.035, "Name": "B.Cu"},
        {"Type": "SolderMask", "Thickness": 0.01, "Name": "Bottom Solder Mask"},
        {"Type": "SolderPaste", "Name": "Bottom Solder Paste"},
        {"Type": "Legend", "Name": "Bottom Silk Screen"}],
}


def codes(issues):
    return [i.code for i in issues]


def by_code(issues, code):
    return next(i for i in issues if i.code == code)


# ---- model ------------------------------------------------------------------------------------
@pytest.mark.parametrize("n", [1, 2, 4, 6, 8])
def test_templates_add_up_and_pass(n):
    st = S.template(n, "1.6mm")
    assert len(st.copper()) == n
    assert S.total_thickness(st, EV) == pytest.approx(1.6e-3, rel=1e-6)
    assert [i for i in S.check(st, EV) if i.level != "info"] == []


def test_z_zero_is_bottom_of_lowest_copper():
    st = S.template(2, "1.6mm")
    zs = S.evaluate(st, EV)
    cu = [z for z in zs if z.layer.kind == S.COPPER]
    assert cu[-1].z0 == pytest.approx(0.0, abs=1e-15)
    assert cu[0].z1 == pytest.approx(1.6e-3)


def test_ounces_and_expressions():
    L = S.StackLayer("L1", S.COPPER, "1oz")
    assert S.thickness_m(L, EV) == pytest.approx(34.79e-6)
    ev = Evaluator({"h": "0.508mm"})
    assert S.thickness_m(S.StackLayer("c", S.DIELECTRIC, "2*h"), ev) == pytest.approx(1.016e-3)


def test_round_trip_through_project(tmp_path):
    p = Project()
    st = S.template(4, "1.0mm")
    st.layers[2].set("thickness", "0.11mm", "suggestion")
    p.designs[0].stackup = st
    f = tmp_path / "p.hertzon"
    p.save(f)
    q = Project.load(f)
    assert q.designs[0].stackup == st
    assert q.designs[0].stackup.layers[2].origin_of("thickness") == "suggestion"
    assert Project.load(f).clone().designs[0].stackup == st
    # projects without a board stay without
    assert Design().stackup is None
    s2 = tmp_path / "x.hzstack"
    st.save(s2)
    assert S.Stackup.load(s2) == st


# ---- checks -----------------------------------------------------------------------------------
def test_unit_mixups_get_a_fix():
    st = S.template(2, "1.6mm")
    st.layers[0].thickness = "35mm"                 # copper in mm instead of um
    st.layers[2].thickness = "1"                    # bare number: ounces
    st.layers[1].thickness = "63"                   # 63 mil typed into a mm field
    iss = [i for i in S.check(st, EV) if i.code == "thickness-range"]
    fixes = {i.layer: i.fix.changes[0][2] for i in iss}
    assert fixes[0] == "35um"
    assert fixes[2] == "1oz"
    assert fixes[1] == "1.6002mm"


def test_fix_changes_only_after_apply_and_marks_origin():
    st = S.template(2, "1.6mm")
    st.layers[0].thickness = "35mm"
    iss = S.check(st, EV)
    assert st.layers[0].thickness == "35mm"        # check never changes the stackup
    S.apply_fix(st, by_code(iss, "thickness-range").fix)
    assert st.layers[0].thickness == "35um" and st.layers[0].origin_of("thickness") == "suggestion"


def test_total_mismatch_offers_scaling_and_core():
    st = S.template(4, "1.6mm")
    st.nominal_thickness = "2.0mm"
    iss = S.check(st, EV)
    tot = by_code(iss, "total")
    assert tot.level == "warning"
    S.apply_fix(st, tot.fix)
    assert S.total_thickness(st, EV) == pytest.approx(2.0e-3, rel=1e-4)
    st2 = S.template(4, "1.6mm")
    st2.nominal_thickness = "1.7mm"
    S.apply_fix(st2, by_code(S.check(st2, EV), "total-core").fix)
    assert S.total_thickness(st2, EV) == pytest.approx(1.7e-3, rel=1e-4)


def test_order_errors():
    st = S.Stackup([S.StackLayer("a", S.COPPER, "35um", "copper"), S.StackLayer("b", S.COPPER, "35um", "copper"),
                    S.StackLayer("c", S.DIELECTRIC, "1mm", "FR4_epoxy")])
    c = codes(S.check(st, EV))
    assert "copper-copper" in c and "outer-dielectric" in c
    assert "no-copper" in codes(S.check(S.Stackup([S.StackLayer("c", S.DIELECTRIC, "1mm", "FR4_epoxy")]), EV))


def test_missing_values_and_percent_loss_tangent():
    st = S.template(2, "1.6mm")
    st.layers[0].thickness = ""
    st.layers[1].material = ""
    st.layers[2].thickness = "35um"
    iss = S.check(st, EV)
    assert by_code(iss, "no-thickness").fix.changes == [(0, "thickness", "35um")]
    assert by_code(iss, "no-permittivity").level == "error"
    st.layers[1].permittivity, st.layers[1].loss_tangent = "4.3", "2"
    fx = by_code(S.check(st, EV), "tand-range").fix
    assert fx.changes == [(1, "loss_tangent", "0.02")]


def test_layer_counts_against_files():
    st = S.template(2, "1.6mm")
    st.copper_files, st.file_layer_count = 4, 4
    c = codes(S.check(st, EV))
    assert "copper-files" in c and "layer-count" in c


# ---- material suggestions ---------------------------------------------------------------------
def test_material_suggestions_by_name_and_values():
    L = S.StackLayer("c", S.DIELECTRIC, "0.508mm", file_material="RO4350B")
    assert S.suggest_materials(L, EV)[0][0] == "Rogers RO4350B (tm)"
    L = S.StackLayer("c", S.DIELECTRIC, "0.508mm", permittivity="3.66", loss_tangent="0.0037")
    sug = S.suggest_materials(L, EV)
    assert sug[0] == ("Rogers RO4350B (tm)", "exact")
    L = S.StackLayer("c", S.DIELECTRIC, "1mm", file_material="FR-4")
    assert any(n == "FR4_epoxy" for n, _ in S.suggest_materials(L, EV))


def test_name_and_values_disagree():
    L = S.StackLayer("c", S.DIELECTRIC, "0.508mm", material="Rogers RO4350B (tm)", permittivity="4.5",
                     file_material="RO4350B")
    st = S.Stackup([S.StackLayer("t", S.COPPER, "35um", "copper"), L, S.StackLayer("b", S.COPPER, "35um", "copper")])
    iss = S.check(st, EV)
    S.apply_fix(st, by_code(iss, "name-vs-values").fix)
    assert S.layer_er_tand(st.layers[1], EV) == (3.66, 0.0037)


# ---- job files --------------------------------------------------------------------------------
def test_kicad_job_file(tmp_path):
    f = tmp_path / "rf-job.gbrjob"
    f.write_text(json.dumps(KICAD_JOB), encoding="utf-8")
    st, notes = S.read_gbrjob(f)
    assert [L.kind for L in st.layers] == [S.COPPER, S.DIELECTRIC] * 3 + [S.COPPER]
    assert st.copper_files == 4 and st.file_layer_count == 4
    nl = by_code(notes, "job-not-loaded").text                            # said, not silently dropped
    assert nl.count("Silk") == 2 and nl.count("Solder Mask") == 2
    assert st.layers[0].thickness == "35um" and st.layers[0].origin_of("thickness") == "file"
    assert st.layers[1].material == "FR4_epoxy" and st.layers[1].permittivity == "4.5"
    assert S.total_thickness(st, EV) == pytest.approx(1.58e-3)
    iss = S.check(st, EV)
    assert "kicad-default" in codes(iss)
    assert "total" not in codes(iss)                                      # -1.25 %: within fab tolerance
    assert not [i for i in iss if i.level == "error"]


def test_job_file_with_wrong_and_missing_values():
    job = json.loads(json.dumps(KICAD_JOB))
    ms = job["MaterialStackup"]
    ms[3]["Thickness"] = 35                    # copper in um written as mm
    del ms[5]["Thickness"]                     # inner copper without thickness
    ms[6]["Thickness"] = "1,24"                # decimal comma as text
    ms[4]["LossTangent"] = 2                   # percent
    st, _ = S.read_gbrjob(job)
    # layers: 0 F.Cu, 1 diel, 2 In1.Cu, 3 diel, ...
    assert st.layers[2].thickness == "35um" and st.layers[2].origin_of("thickness") == "assumed"
    assert S.thickness_m(st.layers[3], EV) == pytest.approx(1.24e-3)
    iss = S.check(st, EV)
    rng = [i for i in iss if i.code == "thickness-range"]
    assert rng and rng[0].layer == 0 and rng[0].fix.changes[0][2] == "35um"
    assert "assumed" in codes(iss) and "tand-range" in codes(iss)


def test_job_file_without_stackup():
    job = {"GeneralSpecs": {"LayerNumber": 4, "BoardThickness": "0.8mm"},
           "FilesAttributes": [{"FileFunction": "Copper,L%d" % i} for i in range(1, 5)]}
    st, notes = S.read_gbrjob(job)
    assert "job-no-stackup" in codes(notes)
    assert len(st.copper()) == 4
    assert S.total_thickness(st, EV) == pytest.approx(0.8e-3, rel=1e-6)
    assert all(L.origin_of("thickness") == "assumed" for L in st.layers)
    st, notes = S.read_gbrjob({})
    assert len(st.copper()) == 2 and "job-no-thickness" in codes(notes)
    assert not math.isnan(S.evaluate(st, EV)[0].z1)


def test_fill_material_of_solid_copper():
    st = S.template(4, "1.6mm")
    cu = st.copper()
    assert S.fill_of(st, cu[1]) == "FR4_epoxy"                   # inner: the prepreg next to it
    assert S.fill_of(st, cu[0]) == "air"                          # outer layer
    st2 = S.template(2, "1.6mm")
    assert S.fill_of(st2, 0) == "air"
    st2.layers[0].fill = "Rogers RO4350B (tm)"
    assert S.fill_of(st2, 0) == "Rogers RO4350B (tm)"


# ---- editor -----------------------------------------------------------------------------------
def test_editor_dialog(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    from hertzon.gui.stackup_dialog import StackupDialog, nice_step, ruler_unit, size_text
    sys_path = __import__("sys").path
    sys_path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_gerber import board_project
    app = QApplication.instance() or QApplication([])

    class Doc:
        def __init__(self, p):
            self.project, self.design = p, p.designs[0]

    p = board_project()
    job = json.loads(json.dumps(KICAD_JOB))
    job["MaterialStackup"][3]["Thickness"] = 35          # top copper "35 mm"
    f = tmp_path / "b.gbrjob"
    f.write_text(json.dumps(job), encoding="utf-8")
    dlg = StackupDialog(None, Doc(p))
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    dlg.load_job(str(f))
    ok = dlg.buttons.button(QDialogButtonBox.Ok)
    assert dlg.table.rowCount() == 7
    row = next(r for r, i in enumerate(dlg.issues) if i.code == "thickness-range")
    dlg.issue_list.setCurrentRow(row)
    assert dlg.sel == 0 and dlg.apply_btn.isEnabled()
    dlg._apply_selected()
    assert dlg.st.layers[0].thickness == "35um"
    dlg.table.item(0, 2).setText("")                      # an error blocks OK
    assert not ok.isEnabled()
    dlg.table.item(0, 2).setText("1oz")
    assert ok.isEnabled()
    dlg._read_model()                                     # copper of the test board in the top view
    assert len(dlg.model_layers.layers) == 2
    dlg._select(2)
    dlg._select(0)
    assert dlg.view.polys
    dlg.resize(1250, 820)
    dlg.show()
    app.processEvents()
    dlg.grab().save(str(os.environ.get("HERTZON_SHOT_DIR", tmp_path)) + "/stackup.png")
    st, mats = dlg.result()
    assert st.layers[0].origin_of("thickness") == "user" and mats == []
    assert nice_step(40e-3) == 5e-3 and ruler_unit(5e-3)[0] == "mm" and ruler_unit(0.2)[0] == "m" and ruler_unit(1e-3)[0] == "mm"
    assert size_text(62e-3, 48.5e-3) == "62 × 48.5 mm"
    dlg.close()


def test_no_scaling_offered_around_a_wrong_layer():
    st = S.template(2, "1.6mm")
    st.layers[0].thickness = "35mm"
    tot = by_code(S.check(st, EV), "total")
    assert tot.fix is None and "first" in tot.text
