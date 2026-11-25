"""The material list of the ribbon (Draw > Material, under Model / Non Model): opening it lists every material
of the project and the library with the material for new objects selected; picking one is used for new
objects."""
import os


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_material_list_opens_with_every_material():
    app = _app()  # noqa: F841
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.materials import Material
    w = MainWindow()
    try:
        w.doc.project.materials.append(Material("my_substrate", permittivity="3.3"))
        cb = w.mat_combo
        assert cb.currentText() == "vacuum"
        cb.showPopup()
        cb.hidePopup()
        names = [cb.itemText(i) for i in range(cb.count())]
        assert {"vacuum", "copper", "FR4_epoxy", "my_substrate"} <= set(names), names
        assert cb.currentText() == "vacuum"            # still selected, not blank
        cb.setCurrentText("my_substrate")
        assert w.new_material == "my_substrate"
        cb.showPopup()                                  # opened again: the choice stays
        cb.hidePopup()
        assert cb.currentText() == "my_substrate" and w.new_material == "my_substrate"
    finally:
        w.close()
        w.deleteLater()
