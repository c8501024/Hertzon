"""The wave port dialog opens again on its Modes tab after an Integration Line was drawn (Kwang #13)."""
from __future__ import annotations

import types
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def _opened_tab(monkeypatch, **kw) -> str:
    from PySide6.QtWidgets import QApplication, QDialog
    from hertzon.gui import dialogs as D
    from hertzon.model.design import Assignment, Project
    QApplication.instance() or QApplication([])
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "Microstrip.hzn")
    d = p.designs[0]
    a = Assignment("WP1", "Wave Port", {"NumberOfModes": "1"}, objects=[])
    doc = types.SimpleNamespace(evaluator=lambda: d.evaluator(p), project=p, design=d)
    seen = {}

    def exec_(dlg):
        seen["tab"] = dlg.grid.tabText(dlg.grid.currentIndex())
        return QDialog.Rejected
    monkeypatch.setattr(D.NamedPropsDialog, "exec", exec_)
    D.edit_assignment(None, doc, "excitation", a, **kw)
    return seen["tab"]


def test_port_dialog_reopens_on_the_modes_tab(monkeypatch):
    assert _opened_tab(monkeypatch, tab="Modes") == "Modes"


def test_port_dialog_opens_on_general_by_default(monkeypatch):
    assert _opened_tab(monkeypatch) != "Modes"
