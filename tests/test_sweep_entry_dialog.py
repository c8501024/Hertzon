"""Add/Edit Sweep of a parametric analysis shows only the fields of its type (#577)."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _shown(dlg):
    return [(lab.text(), w.isVisibleTo(dlg)) for lab, w in
            ((dlg.start_label, dlg.start), (dlg.stop_label, dlg.stop), (dlg.step_label, dlg.step))]


def test_single_value_shows_one_value_field(app):
    from hertzon.gui.optimetrics import SweepEntryDialog
    dlg = SweepEntryDialog(None, {"L": "10mm"})
    dlg.type.setCurrentText("Single value")
    assert _shown(dlg)[0] == ("Value:", True)
    assert not dlg.stop.isVisibleTo(dlg) and not dlg.stop_label.isVisibleTo(dlg)
    assert not dlg.step.isVisibleTo(dlg) and not dlg.step_label.isVisibleTo(dlg)
    assert dlg.entry()["type"] == "Single value"
    assert dlg.preview.text() == "1: 9mm"            # the value field is the one that is swept


@pytest.mark.parametrize("kind, step_label", [("Linear step", "Step:"), ("Linear count", "Count:"),
                                              ("Decade count", "Points per decade:"),
                                              ("Octave count", "Points per octave:"),
                                              ("Exponential count", "Count:")])
def test_ranges_show_start_stop_and_step_again(app, kind, step_label):
    from hertzon.gui.optimetrics import SweepEntryDialog
    dlg = SweepEntryDialog(None, {"L": "10mm"}, {"variable": "L", "type": "Single value", "start": "10mm"})
    assert _shown(dlg)[0] == ("Value:", True)
    dlg.type.setCurrentText(kind)
    assert _shown(dlg) == [("Start:", True), ("Stop:", True), (step_label, True)]
