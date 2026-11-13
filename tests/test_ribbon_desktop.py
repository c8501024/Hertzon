"""The Desktop page of the circuit ribbon offers the tools of the 3D ribbon (Design Wizard, Options, Materials, Help)."""
import os

from PySide6.QtWidgets import QToolButton


def _labels(rb, title):
    for i in range(rb.count()):
        if rb.tabText(i) == title:
            return [b.text().replace("\n", " ") for b in rb.widget(i).findChildren(QToolButton)]
    raise AssertionError(f"no page {title}")


def test_circuit_desktop_has_the_tools_of_the_3d_desktop():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.main_window import MainWindow
    w = MainWindow()
    d3 = _labels(w.ribbon, "Desktop")
    dc = _labels(w.circuit_ribbon, "Desktop")
    for t in ("Save", "New", "Open", "Save As", "Undo", "Redo", "Delete", "Copy", "Paste", "Insert Design",
              "Copy Design", "Project Variables", "Design Properties", "Options", "Materials", "Design Wizard", "Help"):
        assert t in d3 and t in dc, t
    assert "Import 3D Designs" in dc and "Export Touchstone" in dc
    assert "Export STEP" in d3 and "Export STEP" not in dc
    for t in ("Filter Design", "Antenna Design"):         # in the Design Wizard
        assert t not in d3 and t not in dc
