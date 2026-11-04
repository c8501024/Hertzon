"""Start page (File > New, program start) and Insert Design: 3D FEM or Circuit, solution type, design kind."""
from __future__ import annotations

import os
from pathlib import Path

from hertzon.model.design import Design, Project, project_from_dict


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_design_kind_is_saved_and_old_files_are_3d(tmp_path):
    p = Project()
    p.designs.append(Design(name="Match", kind="circuit"))
    p.save(tmp_path / "mixed.hzn")
    q = Project.load(tmp_path / "mixed.hzn")
    assert [d.kind for d in q.designs] == ["3d", "circuit"]
    old = p.to_dict()
    for d in old["designs"]:
        d.pop("kind")
    assert [d.kind for d in project_from_dict(old).designs] == ["3d", "3d"]


def test_start_page_offers_only_what_is_built(tmp_path):
    _app()
    from PySide6.QtCore import QSettings
    from hertzon.gui.start_page import SHOW_AT_STARTUP, StartPageDialog
    s = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    dlg = StartPageDialog(None, "new", s, recent=[str(tmp_path / "a.hzn")], examples=[Path("x/PatchAntenna.hzn")])
    assert dlg.kind() == "3d" and dlg.solution_type() == "Modal"
    assert not dlg.cards["circuit"].isEnabled()
    for name in ("Modal", "Terminal", "Eigenmode"):
        assert dlg.type_radio[name].isEnabled()
    for name in ("Electrostatic", "DC Conduction", "Magnetostatic", "Eddy Current"):
        assert not dlg.type_radio[name].isEnabled()
    assert dlg.recent.count() == 1 and dlg.examples.count() == 1 and dlg.templates.count() == 2
    dlg.type_radio["Eigenmode"].setChecked(True)
    dlg.units.setCurrentText("um")
    dlg.startup.setChecked(False)
    dlg._create()
    c = dlg.choice
    assert (c.action, c.kind, c.solution_type, c.units, c.name) == ("create", "3d", "Eigenmode", "um", "Project1")
    assert s.value(SHOW_AT_STARTUP, True, type=bool) is False


def test_start_page_lists_and_insert_mode(tmp_path):
    _app()
    from hertzon.gui.start_page import StartPageDialog
    dlg = StartPageDialog(None, "new", recent=[str(tmp_path / "a.hzn")], examples=[Path("x/PatchAntenna.hzn")])
    dlg._item(dlg.examples.item(0))
    assert (dlg.choice.action, Path(dlg.choice.path).name) == ("example", "PatchAntenna.hzn")
    dlg = StartPageDialog(None, "new")
    dlg._item(dlg.templates.item(1))
    assert (dlg.choice.action, dlg.choice.template) == ("template", "hairpin")
    ins = StartPageDialog(None, "insert", default_name="Design3")
    assert ins.side.isHidden() and ins.startup.isHidden() and ins.empty_btn.isHidden()
    assert ins.name.text() == "Design3" and ins.create_btn.text() == "Insert"
