"""Design Properties: the Constants & Functions list and insert-on-click into a value."""
from __future__ import annotations

import os
from types import SimpleNamespace
import pytest

from hertzon.model import expr
from hertzon.gui.expr_reference import insert_into


def test_reference_lists_every_constant_and_function():
    assert {n for n, _s, _d in expr.CONSTANT_INFO} == set(expr._CONSTS)
    assert {s.split("(")[0] for _n, s, _d in expr.FUNCTION_INFO} == set(expr._FUNCS)
    ev = expr.Evaluator({}, "mm")
    for _n, snippet, _d in expr.CONSTANT_INFO:
        ev.value(snippet)                       # every listed constant evaluates as written
    listed = {u for _t, units in expr.UNIT_GROUPS for u in units}
    assert listed == set(expr.ALL_UNITS)


def test_insert_into_at_cursor_selection_and_function():
    assert insert_into("w/2 + ", 6, 0, "pi") == ("w/2 + pi", 8)
    assert insert_into("2*", 2, 0, "pi") == ("2*pi", 4)
    assert insert_into("a + b", 4, 1, "c0") == ("a + c0", 6)
    # a function puts the cursor in its brackets, a selection becomes its argument
    assert insert_into("1 + ", 4, 0, "sqrt()") == ("1 + sqrt()", 9)
    assert insert_into("w/2", 0, 3, "sin()") == ("sin(w/2)", 7)
    assert insert_into("", 5, 0, "atan2(, )") == ("atan2(, )", 6)


def test_variables_dialog_inserts_constant_at_cursor():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from hertzon.gui.dialogs import VariablesDialog
    app = QApplication.instance() or QApplication([])
    doc = SimpleNamespace(project=SimpleNamespace(variables={"h": "1.6mm"}),
                          design=SimpleNamespace(name="D", units="mm", variables={"w": "10mm", "f": "2.4GHz"}))
    dlg = VariablesDialog(doc)
    dlg.show()
    app.processEvents()
    dlg._show_reference()
    ref = dlg.ref_dlg
    names = {ref.tree.topLevelItem(i).text(0) for i in range(ref.tree.topLevelItemCount())}
    assert {"Constants", "Functions", "Units: Length", "Variables"} <= names
    var_group = ref._groups["Variables"]
    assert {var_group.child(i).text(0) for i in range(var_group.childCount())} == {"h", "w", "f"}
    # cursor at the start of w = "10mm" -> the text goes there, and the value still evaluates
    dlg.table.setCurrentCell(0, 1)
    dlg.delegate.last = (0, 0, 0)
    ref.inserted.emit("pi*")
    assert dlg.vars["w"] == "pi*10mm"
    assert abs(expr.Evaluator(dlg.vars).length("w") - 0.01 * 3.141592653589793) < 1e-12
    assert dlg.table.item(0, 2).text() == "31.4159mm"
    # no remembered cursor on that row: append at the end
    dlg.table.setCurrentCell(1, 1)
    ref.inserted.emit("/c0")
    assert dlg.vars["f"] == "2.4GHz/c0"
    # search filters the list
    ref.search.setText("light")
    consts = ref._groups["Constants"]
    shown = [consts.child(i).text(0) for i in range(consts.childCount()) if not consts.child(i).isHidden()]
    assert shown == ["c0"] and ref._groups["Functions"].isHidden()
    ref.close()
    dlg.close()


def test_ratio_of_lengths_has_no_unit():
    """Design Properties: c = a/b of two lengths is a plain number, whichever is larger (a user report:
    a = 2mm, b = 6mm showed c as 333.333mm)."""
    from hertzon.model.expr import Evaluator
    for a, b, c in (("6mm", "2mm", "3"), ("2mm", "6mm", "0.333333")):
        ev = Evaluator({"a": a, "b": b, "c": "a/b", "w": "a/2", "A": "a*b", "f": "2.4GHz", "lam": "c0/f",
                        "fr": "c0/(2*b)", "n": "4", "L": "2m"})
        assert ev.display("c") == c
        assert ev.dimension("c") == {"length": 0, "freq": 0}
        assert ev.length("c") == pytest.approx(float(c) * 1e-3, rel=1e-5)   # a plain number: model units
        assert ev.display("w") == f"{float(a[:-2]) / 2:g}mm"
        assert ev.display("A") == "12mm^2"
        assert ev.display("f") == "2.4GHz"
        assert ev.display("lam") == "124.914mm" and ev.length("lam") == pytest.approx(0.124914, rel=1e-5)
        assert ev.display("fr").endswith("GHz")
        assert ev.display("n") == "4" and ev.display("L") == "2000mm"
    assert Evaluator({"z": "1mm-1mm"}).dimension("z") is None          # zero: dimension unknown
