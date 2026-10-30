"""Fields Calculator: the language (units, errors), evaluation on element quadrature against an analytic
TE10 waveguide mode, cut planes on element faces, curved elements, and the design round trip."""
from __future__ import annotations

import numpy as np
import pytest
import pyvista as pv

from hertzon.post.calc import lang as L
from hertzon.post.calc import units as UN
from hertzon.post.calc.evaluate import Evaluator, FieldSource
from hertzon.post.calc.geometry import Mesh, node_lambdas

A, B, LEN, ER, F0 = 22.86e-3, 10.16e-3, 30e-3, 2.2, 9e9


def te10(P, f):
    """TE10 mode in a guide filled with ER, normalised to 1 W."""
    w = 2 * np.pi * f
    k, kc = w * np.sqrt(ER) / L.C0, np.pi / A
    beta = np.sqrt(k * k - kc * kc)
    E0 = np.sqrt(4 * w * L.MU0 / (A * B * beta))
    x, z = P[:, 0], P[:, 2]
    e = np.exp(-1j * beta * z)
    E, H = np.zeros((len(P), 3), complex), np.zeros((len(P), 3), complex)
    E[:, 1] = E0 * np.sin(kc * x) * e
    H[:, 0] = -(beta / (w * L.MU0)) * E0 * np.sin(kc * x) * e
    H[:, 2] = 1j * (kc / (w * L.MU0)) * E0 * np.cos(kc * x) * e
    return E, H, E0, beta


class Waveguide(FieldSource):
    def __init__(self, n=(10, 5, 14), npt=10):
        super().__init__()
        g = pv.ImageData(dimensions=(n[0] + 1, n[1] + 1, n[2] + 1),
                         spacing=(A / n[0], B / n[1], LEN / n[2])).triangulate()
        self.nodes = np.asarray(g.points, float)
        self.tets = np.asarray(g.cells_dict[pv.CellType.TETRA], np.int64)
        self.npt, self.freq = npt, F0
        P = np.einsum("ki,mid->mkd", node_lambdas(npt), self.nodes[self.tets])
        E, H, *_ = te10(P.reshape(-1, 3), F0)
        self._E, self._H = E.reshape(len(self.tets), npt, 3), H.reshape(len(self.tets), npt, 3)

    def nodal(self, which):
        return self._E if which == "E" else self._H

    def material(self, prop):
        return np.full(len(self.tets), {"eps_r": ER, "mu_r": 1.0, "sigma": 0.0, "tand": 1e-3}[prop])

    def object_samples(self, name, dim):
        every = np.arange(len(self.tets))
        if dim == 3:
            return self.mesh.volume_samples(every, name)
        o, loc, n = self.mesh.boundary_faces(every)
        return self.mesh.triangle_samples(self.mesh.face_triangles(o, loc), o, n, label=name)


@pytest.fixture(scope="module")
def wg():
    env = L.Env(geometry={"Guide": L.GeoInfo(frozenset({3, 2}), True)})
    return Evaluator(env, Waveguide())


def value(ev, src):
    v = complex(ev.value(src).value)
    assert abs(v.imag) <= 1e-9 * max(abs(v), 1e-30)       # integrals of real densities stay real
    return v.real


# ---- language ------------------------------------------------------------------------------------
def test_units_and_kinds():
    env = L.Env(geometry={"Guide": L.GeoInfo(frozenset({3, 2}), True)})
    cases = {"E": ("V/m", True, True), "|E|": ("V/m", False, True), "re(S)": ("W/m²", True, True),
             "flux(Guide, re(S))": ("W", False, False), "int_vol(Guide, 0.25*eps0*eps_r*|E|^2)": ("J", False, False),
             "curl(E)": ("V/m²", True, True), "2mm*3GHz": ("Hz·m", False, False)}
    for src, (unit, vec, field) in cases.items():
        i = L.analyze(src, env)
        assert UN.label(i.unit) == unit, src
        assert (i.vec, i.field) == (vec, field), src
    assert UN.fmt(9.634e-3, UN.U(V=1, A=1)) == "9.634 mW"


def test_levels_in_db_keep_their_unit():
    """dB() is dimensionless but shown as dB: in the Equations tab, on report axes, colour bars and values; sums
    and multiples of a level stay in dB, other functions of it do not."""
    from hertzon.post.calc.evaluate import Evaluator
    from hertzon.gui.calc_bridge import NetSource
    env = L.Env(named={"RL": "-dB(S(1,1))"})
    for src in ("RL", "dB(S(2,1))", "dB10(VSWR(1))", "RL + 3", "2*RL", "RL - dB(S(2,1))", "|RL|"):
        assert L.unit_label(L.analyze(src, env)) == "dB", src
    for src in ("RL*RL", "sqrt(RL)", "|S(1,1)|", "10^2"):
        assert L.unit_label(L.analyze(src, env)) == "", src
    assert L.unit_label(L.analyze("flux(Guide, re(S))", L.Env(geometry={"Guide": L.GeoInfo(frozenset({3, 2}), True)}))) \
        == "W"
    data = {"freq": np.array([1e9]), "S": np.array([[[0.1]]], complex), "z0": np.array([50.0])}
    assert Evaluator(env, NetSource(data, 0)).value("RL").text() == "20 dB"


@pytest.mark.parametrize("src, where", [("E + H", "E + H"), ("flux(Nowhere, re(S))", "Nowhere"),
                                         ("max(Guide |E|)", "|E|"), ("E · ", None)])
def test_errors_point_at_the_text(src, where):
    env = L.Env(geometry={"Guide": L.GeoInfo(frozenset({3, 2}), True)})
    with pytest.raises(L.CalcError) as e:
        L.analyze(src, env)
    if where is not None:
        assert e.value.pos is not None and e.value.pos <= src.index(where) + len(where)


def test_user_functions_rename_and_aliases():
    f = L.parse_function("power(A) = flux(A, re(S))")
    env = L.Env(geometry={"Guide": L.GeoInfo(frozenset({3, 2}), True)}, functions={"power": f},
                named={"Pin": "power(Guide)", "twice": "2*Pin"})
    assert UN.label(L.analyze("twice", env).unit) == "W"
    assert L.rename("Pin + Pin2 + Pin", "Pin", "P_in") == "P_in + Pin2 + P_in"
    assert L.analyze("Re(E)", env).vec                      # Re/Im/abs are accepted spellings
    with pytest.raises(L.CalcError):
        L.analyze("a", L.Env(named={"a": "b", "b": "a"}))     # cycle


# ---- values against the analytic TE10 mode -------------------------------------------------------
def test_power_flux_through_cuts(wg):
    assert value(wg, "flux(cut_xy(15mm), re(S))") == pytest.approx(1.0, rel=0.02)
    # planes on the end faces lie on element faces; flip() reverses the direction
    assert value(wg, "flux(cut_xy(0), re(S))") == pytest.approx(1.0, rel=0.02)
    assert value(wg, "flux(flip(cut_xy(30mm)), re(S))") == pytest.approx(-1.0, rel=0.02)
    r = wg.value("flux(cut_xy(0), re(S)) - flux(cut_xy(30mm), re(S))")
    assert r.is_zero                                         # displayed as approximately 0


def test_energies_loss_and_peak(wg):
    _, _, E0, beta = te10(np.zeros((1, 3)), F0)
    w = 2 * np.pi * F0
    we = 0.25 * L.EPS0 * ER * E0 ** 2 * A * B * LEN / 2
    assert value(wg, "int_vol(Guide, 0.25*eps0*eps_r*|E|^2)") == pytest.approx(we, rel=0.02)
    assert value(wg, "int_vol(Guide, 0.25*mu0*|H|^2)") == pytest.approx(we, rel=0.03)
    assert value(wg, "int_vol(Guide, 0.5*omega*eps0*eps_r*tand*|E|^2)") == pytest.approx(
        2 * w * 1e-3 * we, rel=0.02)
    assert value(wg, "max(Guide, |E|)") == pytest.approx(E0, rel=0.03)
    assert abs(complex(wg.value("at(point(11.43mm, 5.08mm, 0), y(E))").value)) == pytest.approx(E0, rel=0.01)
    assert value(wg, "int_vol(Guide, 1)") == pytest.approx(A * B * LEN, rel=1e-6)
    assert value(wg, "int_surf(Guide, 1)") == pytest.approx(2 * (A * B + A * LEN + B * LEN), rel=1e-6)


def test_derivatives_satisfy_maxwell(wg):
    res = abs(wg.value("max(Guide, |curl(E) + j*omega*mu0*H|) / max(Guide, |omega*mu0*H|)").value)
    assert res < 0.1


def test_phase(wg):
    _, _, E0, beta = te10(np.zeros((1, 3)), F0)
    # the instantaneous field at phase 0 is the real part, a quarter period later the imaginary part
    assert value(wg, "at(point(11.43mm, 5.08mm, 0), inst(y(E)))") == pytest.approx(E0, rel=0.01)
    ev90 = Evaluator(wg.env, wg.src, 90.0)
    assert abs(value(ev90, "at(point(11.43mm, 5.08mm, 0), inst(y(E)))")) < 0.02 * E0


# ---- geometry ------------------------------------------------------------------------------------
def test_curved_elements_integrate_the_true_volume():
    """A tet whose mid-edge nodes are pushed outwards holds more volume than the straight one; zero
    displacement gives the straight volume."""
    nodes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    tets = np.array([[0, 1, 2, 3]])
    from hertzon.post.calc.geometry import TET10_EDGES
    mid = np.array([(nodes[i] + nodes[j]) / 2 for i, j in TET10_EDGES])
    assert Mesh(nodes, tets).volume_samples(np.arange(1)).W.sum() == pytest.approx(1 / 6)
    flat = Mesh(nodes, tets, curved=(np.array([0]), np.zeros((1, 6, 3))))
    assert flat.volume_samples(np.arange(1)).W.sum() == pytest.approx(1 / 6, rel=1e-6)
    bulged = Mesh(nodes, tets, curved=(np.array([0]), 0.05 * (mid - nodes.mean(axis=0))[None]))
    assert bulged.volume_samples(np.arange(1)).W.sum() > 1 / 6 * 1.01


# ---- formula editor ------------------------------------------------------------------------------
def _formula_env():
    G = L.GeoInfo
    return L.Env(named={"P_in": "flux(Port1, re(S))"},
                 geometry={"Port1": G(frozenset({2})), "Port2": G(frozenset({2})), "Box": G(frozenset({2, 3}), True),
                           "Line1": G(frozenset({1})), "Loop": G(frozenset({1}), True), "P1": G(frozenset({0}))})


@pytest.mark.parametrize("src", [
    "flux(Port1, re(S))", "flux(flip(Port2), re(S))", "flux([Port1, Port2], re(S))",
    "int_vol(Box, 0.5*omega*eps0*eps_r*tand*|E|^2)", "int_line(Line1, E)", "circ(Loop, H)", "max(Box, |E|)",
    "avg(Port1, mag(E))", "at(P1, E)", "E × H", "re(E·conj(H))", "sqrt(eps_r)/2", "curl(E)",
    "curl E + j*omega*mu0*mu_r*H", "x(E, Global)", "10mm*f/c0", "1e-3*|E|^2", "P_in*2", "-(x+y)", "(x+y)^2/z",
    "flux(face(Box, 3), re(S))", "max(cut_xy(2mm), |E|)", "dB(|E|)", "45deg", "int_surf(Box, |Js|^2)",
    "dB(S(2,1))", "1-|S(1,1)|^2", "re(Z(1,1))", "VSWR(1)", "0.5*rs(5.8e7S/m)*|Js|^2", "2.5e-3*E", "1.5e3mm"])
def test_formula_editor_round_trip(src):
    """Text -> typeset formula -> text gives the same expression (same formula, kind and unit)."""
    from hertzon.post.calc import latex as TX
    env = _formula_env()
    tex = TX.to_latex(src, env)
    back = TX.to_text(tex, env)
    assert TX.to_latex(back, env) == tex
    a, b = L.analyze(src, env), L.analyze(back, env)
    assert (a.unit, a.field, a.vec, a.cx, a.num) == (b.unit, b.field, b.vec, b.cx, b.num)


@pytest.mark.parametrize("tex, src", [
    (r"E\times H", "E × H"),
    (r"\iint_{\mathrm{Port1}}S\cdot\mathbf{n}\,\mathrm{d}A", "flux(Port1, S)"),
    (r"\iint_{Port1}S\,dA", "int_surf(Port1, S)"),
    (r"\oiint_{-Port1,Port2}S\cdot\mathbf{n}\,\mathrm{d}A", "flux([flip(Port1), Port2], S)"),
    (r"\int_{Line1}E\cdot\mathrm{d}\mathbf{l}", "int_line(Line1, E)"),
    (r"\iiint_{Box}\frac{1}{2}\left|E\right|^2\,\mathrm{d}V", "int_vol(Box, 1/2*|E|^(2))"),
    (r"\max_{Box}\left(\left|E\right|\right)", "max(Box, |E|)"),
    (r"\left.E\right|_{P1}", "at(P1, E)"),
    (r"\nabla\times H", "curl H"),
    (r"EH", "E*H"),
    (r"P_{in}", "P_in"),
    (r"\varepsilon_0\varepsilon_r", "eps0*eps_r"),
    (r"10\,\mathrm{mm}", "10mm"),
    (r"45^{\circ}", "45deg"),
    (r"\operatorname{Re}\left\{S\right\}", "re(S)"),
    (r"\sqrt\left(x\right)", "sqrt(x)"),
    (r"\sqrt{x}", "sqrt(x)"),
    (r"5.8\cdot 10^{7}\,\mathrm{S/m}", "5.8e7S/m"),
    (r"5.8\times10^7\frac{S}{m}", "5.8e7S/m"),
    (r"5.8e7S/m", "5.8e7S/m"),
    (r"5.8e-7\,\mathrm{S/m}", "5.8e-7S/m"),
    (r"3V/m", "3V/m"),
    (r"2\cdot 10^{x}", "2 · 10^(x)"),
])
def test_formula_editor_reads_what_is_typed(tex, src):
    from hertzon.post.calc import latex as TX
    assert TX.to_text(tex, _formula_env()) == src


@pytest.mark.parametrize("tex, msg", [
    (r"\iint_{\placeholder{}}\placeholder{}\,\mathrm{d}A", "Choose the face"),
    (r"\iint_{Port1}\placeholder{}\,\mathrm{d}A", "Fill in"),
    (r"\iint_{Port1}S", "needs dA"),
    (r"\frac{E}{\placeholder{}}", "Fill in"),
])
def test_formula_editor_says_what_is_missing(tex, msg):
    from hertzon.post.calc import latex as TX
    with pytest.raises(TX.LatexError, match=msg):
        TX.to_text(tex, _formula_env())


def test_formula_editor_name_in_front():
    from hertzon.post.calc import latex as TX
    assert TX.split_named("P_in = flux(Port1, re(S))") == ("P_in", "flux(Port1, re(S))", 7)
    assert TX.split_named("flux(Port1, re(S))")[0] == ""
    assert TX.split_named_tex(TX.name_tex("P_in") + r"=\iint") == ("P_in", r"\iint")
    assert TX.split_named_tex(r"S=E\times H") == ("S", r"E\times H")
    with pytest.raises(TX.LatexError):
        TX.split_named_tex(r"\frac{a}{b}=E")


def test_picked_geometry_goes_below_the_sign():
    from hertzon.post.calc import latex as TX
    assert TX.geometry_tex("Port1") == r"\mathrm{Port1}"
    assert TX.geometry_tex("flip(Port2)") == r"-\mathrm{Port2}"
    assert TX.geometry_tex("[Port1, flip(face(Box, 2))]") == r"\mathrm{Port1},-\mathrm{face(Box,2)}"
    env = _formula_env()
    tex = r"\iint_{" + TX.geometry_tex("[Port1, flip(face(Box, 2))]") + r"}S\cdot\mathbf{n}\,\mathrm{d}A"
    assert TX.to_text(tex, env) == "flux([Port1, flip(face(Box,2))], S)"


# ---- design --------------------------------------------------------------------------------------
def test_expressions_are_saved_with_the_design():
    from hertzon.model.design import Design, FieldExpression, design_from_dict
    from dataclasses import asdict
    d = Design("D")
    d.field_exprs.append(FieldExpression("Pin", "flux(Port1, re(S))", "input power"))
    d.field_funcs.append("power(A) = flux(A, re(S))")
    back = design_from_dict(asdict(d))
    assert back.field_exprs[0] == d.field_exprs[0]
    assert back.field_funcs == d.field_funcs


def test_equations_keep_the_solution_current():
    """Equations are post-processing: saving one does not mark the solution outdated or restart the adaptive
    passes (the model key is the one of a design without them, as in 0.2.3)."""
    from hertzon.model.design import FieldExpression, Project, Setup
    from hertzon.solver.keys import model_key
    p = Project()
    d = p.designs[0]
    d.setups = [Setup("Setup1")]
    k = model_key(p, d, d.setups[0])
    d.field_exprs.append(FieldExpression("RL", "-dB(S(1,1))"))
    d.field_funcs.append("power(A) = flux(A, re(S))")
    assert model_key(p, d, d.setups[0]) == k


def test_report_traces_follow_a_rename():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui import calc_bridge as CB
    from hertzon.gui.doc import Document
    from hertzon.model.design import FieldExpression, Project, Report
    doc = Document(Project())
    d = doc.design
    d.add_object("CreateBox", "Box", Position="0,0,0", XSize="1", YSize="1", ZSize="1")
    doc.rebuild()
    d.field_exprs += [FieldExpression("Pin", "flux(Box, re(S))"), FieldExpression("magE", "|E|"),
                      FieldExpression("Evec", "E")]
    d.reports.append(Report("R", "Rectangular", "Setup1 : Sweep", ["Pin @ Setup1 : Sweep", "2*Pin + dB(S(1,1))"]))
    assert CB.value_names(doc) == ["Pin"]                   # only values go into reports
    CB.rename_everywhere(d, "Pin", "P_in")
    assert d.reports[-1].traces == ["P_in @ Setup1 : Sweep", "2*P_in + dB(S(1,1))"]
    assert CB.field_names_in(d, d.reports[-1].traces[1]) == ["P_in"]


def test_equations_tab_picks_the_geometry_in_the_3d_view():
    """The calculator is a tab next to the 3D Modeler with its own ribbon page: an integral switches to the
    modeler, the face or object picked there goes into the equation around the field typed so far, and the
    Equations tab comes back. Saved equations are listed in the project tree."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hertzon.gui.doc import Pick
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.design import Project
    win = MainWindow(Project())
    win.des.add_object("CreateBox", "Box", Position="0,0,0", XSize="1", YSize="1", ZSize="1")
    win.doc.rebuild()
    win.fields_calculator()
    calc = win.calc_window
    rb, page = win.ribbon, win._eq_page
    assert win.tabs.currentWidget() is calc and win.tabs.indexOf(calc) == 1
    assert rb.isTabVisible(page) and rb.currentIndex() == page
    calc.set_mode("lin")                                 # the text line (the formula editor is a web view)
    calc.edit.setPlainText("0.25*eps0*eps_r*|E|^2")
    calc.pick("int_vol", 3)
    assert win.tabs.currentIndex() == 0 and win.doc.selection_mode == "object"
    assert not rb.isTabVisible(page) and rb.tabText(rb.currentIndex()) == "Results"
    win.doc.select([Pick("Box")])
    calc._bar._use()
    app.processEvents()
    assert win.tabs.currentWidget() is calc and rb.isTabVisible(page)
    assert calc.edit.toPlainText() == "int_vol(Box, 0.25*eps0*eps_r*|E|^2)"
    assert calc.info is not None and not calc.info.field
    calc.edit.setPlainText("We = " + calc.edit.toPlainText())    # the name in front, as typed
    assert calc.typed_name() == "We" and calc.info is not None
    assert calc.save()
    assert "Equations: We" in win.tabs.tabText(1)
    assert win.des.field_exprs[-1].expr == "int_vol(Box, 0.25*eps0*eps_r*|E|^2)"

    def tree_items(it):
        yield it
        for i in range(it.childCount()):
            yield from tree_items(it.child(i))
    data = [it.data(0, Qt.UserRole + 1) for i in range(win.project_tree.topLevelItemCount())
            for it in tree_items(win.project_tree.topLevelItem(i))]
    assert ("group", "Equations") in data and ("equation", "We") in data
    calc.new()
    calc.edit.setPlainText("re(S)")
    calc.pick("flux", 2)
    assert win.doc.selection_mode == "face"
    win.doc.select([Pick("Box", 2)])
    calc._bar.flip.setChecked(True)
    calc._bar._use()
    assert calc.edit.toPlainText() == "flux(flip(face(Box, 2)), re(S))"
    assert win.doc.selection_mode == "object"           # the selection mode is restored
    win._close_tab(win.tabs.indexOf(calc))
    assert win.tabs.indexOf(calc) < 0 and not rb.isTabVisible(page)
    # export and import the equations of a design
    import json
    import tempfile
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    f = Path(tempfile.mkdtemp()) / "eq.json"
    save, ask = QFileDialog.getSaveFileName, QMessageBox.question
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(f), ""))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        win.export_equations()
        assert json.loads(f.read_text(encoding="utf-8"))["equations"][0]["name"] == "We"
        win.des.field_exprs[0].expr = "1"
        win.import_equations(str(f))
    finally:
        QFileDialog.getSaveFileName, QMessageBox.question = save, ask
    assert [(e.name, e.expr) for e in win.des.field_exprs] == [("We", "int_vol(Box, 0.25*eps0*eps_r*|E|^2)")]
    win.doc.dirty = False
    win.close()


def test_plot_fields_menu_lists_field_equations():
    """Plot Fields (right click in the 3D view, More in the ribbon) has the field equations of the design."""
    from PySide6.QtWidgets import QApplication, QMenu
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.design import FieldExpression, Project
    win = MainWindow(Project())
    win.des.field_exprs += [FieldExpression("p_loss", "0.5*|E|^2"), FieldExpression("P_in", "1")]
    top = QMenu()
    pf = win._plot_fields_menu(top)
    eq = next(a.menu() for a in pf.actions() if a.text() == "Equations")
    assert [a.text() for a in eq.actions() if a.text()] == ["p_loss", "New Equation..."]
    win.doc.dirty = False
    win.close()


def test_changed_equation_redraws_its_plots():
    """Saving an equation draws its field plots on screen again, and the plots of equations that use it."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.design import FieldExpression, FieldPlot, Project
    win = MainWindow(Project())
    win.des.field_exprs += [FieldExpression("w", "|E|^2"), FieldExpression("w2", "2*w"),
                            FieldExpression("h", "|H|")]
    win.des.field_plots += [FieldPlot(n, q, "Setup1 : LastAdaptive")
                            for n, q in [("W", "w"), ("W2", "w2"), ("H", "h"), ("Off", "w")]]
    for n in ("W", "W2", "H"):
        win.view.overlay_actors[n] = []
    drawn = []
    win.show_field_plot = lambda name, focus=True: drawn.append((name, focus))
    win.redraw_equation_plots(["w"])
    assert drawn == [("W", False), ("W2", False)]       # "Off" is not on screen, "h" does not use w
    drawn.clear()
    win.redraw_equation_plots()
    assert [n for n, _ in drawn] == ["W", "W2", "H"]
    win.des.field_exprs[0].expr = "1"                     # w is a value now: its plots and W2's go
    drawn.clear()
    win.redraw_equation_plots(["w"])
    assert [p.name for p in win.des.field_plots] == ["H"] and drawn == []
    assert "W" not in win.view.overlay_actors
    win.view.overlay_actors.clear()
    win.doc.dirty = False
    win.close()


def test_equations_of_s_parameters():
    """S(i,j), Y, Z and VSWR in an equation: a value from the network data alone (no fields needed), next
    to S, the Poynting vector."""
    from hertzon.gui.calc_bridge import NetSource
    env = L.Env(geometry={"Port1": L.GeoInfo(frozenset({2}))})
    assert L.analyze("1-|S(1,1)|^2", env).uses == {"net"}
    assert L.analyze("flux(Port1, re(S))", env).uses == {"fields"}
    assert L.analyze("flux(Port1, re(S))/(1-|S(1,1)|^2)", env).uses == {"fields", "net"}
    assert UN.label(L.analyze("Z(1,1)", env).unit) == UN.label(UN.U(V=1, A=-1))
    with pytest.raises(L.CalcError, match="port as a number"):
        L.analyze("S(1.5, 1)", env)
    S = np.array([[[0.5, 0.1j], [0.1j, 0.0]], [[0.2, 0.9], [0.9, 0.2]]], complex)
    data = {"freq": np.array([1e9, 2e9]), "S": S, "z0": np.array([50.0, 50.0])}
    ev = Evaluator(env, NetSource(data, 1))
    assert ev.value("1-|S(1,1)|^2").value == pytest.approx(0.96)
    assert ev.value("VSWR(1)").value == pytest.approx(1.2 / 0.8)
    assert ev.value("Z(1,1)").value == pytest.approx(50 * np.linalg.solve(np.eye(2) - S[1], np.eye(2) + S[1])[0, 0])
    with pytest.raises(L.CalcError, match="Fields report"):
        ev.value("max(Port1, |E|)")


def test_reports_of_equations():
    """Results: equations of the fields go into Fields reports, equations of S parameters into the solution
    data reports (category Equations)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui import calc_bridge as CB
    from hertzon.gui.main_window import MainWindow
    from hertzon.gui.reports import ReportWindow, is_fields_report
    from hertzon.model.design import FieldExpression, Project
    win = MainWindow(Project())
    d = win.des
    d.add_object("CreateBox", "Box", Position="0,0,0", XSize="1", YSize="1", ZSize="1")
    win.doc.rebuild()
    d.field_exprs += [FieldExpression("Pin", "flux(Box, re(S))"), FieldExpression("RL", "-dB(S(1,1))"),
                      FieldExpression("magE", "|E|")]
    assert CB.value_names(win.doc, fields=True) == ["Pin"]
    assert CB.value_names(win.doc, fields=False) == ["RL"]
    win.field_report("RL")
    r = d.reports[-1]
    assert r.traces == ["RL"] and not is_fields_report(win.doc, r)
    w = ReportWindow(win.doc, r)
    assert [w.cat.itemText(i) for i in range(w.cat.count())][-1] == "Equations"
    assert w.cat.currentText() == "Equations" and [w.qty.itemText(i) for i in range(w.qty.count())] == ["RL"]
    win.field_report()
    r = d.reports[-1]
    assert r.name.startswith("Fields Plot") and r.traces == ["Pin"] and is_fields_report(win.doc, r)
    w = ReportWindow(win.doc, r)
    assert [w.cat.itemText(i) for i in range(w.cat.count())] == ["Equations"]
    assert [w.qty.itemText(i) for i in range(w.qty.count())] == ["Pin"]
    win.doc.dirty = False
    win.close()


def test_s_parameter_equation_in_a_report(tmp_path):
    """An equation of S parameters is drawn at every frequency of the solution, without fields."""
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui.doc import Document, result_dir
    from hertzon.gui.reports import ReportWindow
    from hertzon.model.design import FieldExpression, Project, Report
    from hertzon.solver.results import ResultStore
    proj = Project.load(Path(__file__).parent.parent / "examples" / "PatchAntenna.hzn")
    proj.save(tmp_path / "p.hzn")
    doc = Document(proj)
    st = ResultStore(result_dir(doc, "Setup1"))
    ports = [{"name": "1", "number": 1, "kind": "Lumped Port"}]
    st.write_json("ports.json", ports)
    S = np.array([0.5, 0.1, 0.5j]).reshape(3, 1, 1)
    st.save_sparams("Sweep", np.array([1e9, 2e9, 3e9]), S, np.array([50.0]), ports)
    doc.design.field_exprs.append(FieldExpression("RL", "-dB(S(1,1))"))
    r = Report("R", "Rectangular", "Setup1 : Sweep", ["RL", "RL + dB(S(1,1))"])
    w = ReportWindow(doc, r)
    x, y = w._trace_xy("RL")
    assert list(x) == [1, 2, 3] and y == pytest.approx(-20 * np.log10([0.5, 0.1, 0.5]))
    assert w._trace_xy("RL + dB(S(1,1))")[1] == pytest.approx([0, 0, 0], abs=1e-9)


def test_plot_on_selection_picks_in_the_3d_view(monkeypatch):
    """Plot on (selection) or (selection, inside solids) with nothing selected: after OK a box over the 3D view
    says what to pick, and Plot makes the plot on what was picked. Plot Mesh without a selection likewise."""
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui import fields as Fm
    from hertzon.gui import fieldplot_dialogs as FD
    from hertzon.gui.doc import Pick
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.design import Project, Setup
    win = MainWindow(Project())
    d = win.des
    d.add_object("CreateBox", "Box", Position="0,0,0", XSize="1", YSize="1", ZSize="1")
    win.doc.rebuild()
    monkeypatch.setattr(Fm, "field_solutions", lambda doc: ["Setup1 : LastAdaptive"])
    drawn = []
    monkeypatch.setattr(win, "show_field_plot", lambda name, focus=True: drawn.append(name))
    win.doc.select([])
    seen = {}

    def run(self):
        self.where.setCurrentIndex(seen["where"])
        seen["hint"] = self.pick_hint.text()
        return QDialog.Accepted
    monkeypatch.setattr(FD.FieldPlotDialog, "exec", run)
    for where, what in ((1, "Pick solids"), (0, "Pick faces or objects")):
        seen["where"] = where
        win.doc.select([])
        n = len(d.field_plots)
        win.plot_fields("Mag_E")
        assert "After OK you pick" in seen["hint"]
        bar = win._pick_bar
        assert bar.windowTitle() == what and len(d.field_plots) == n   # nothing plotted yet
        bar._use()
        assert bar.isVisible() and "first" in bar.sel.text()              # nothing picked: stays open
        win.doc.select([Pick("Box")])
        bar._use()
        assert d.field_plots[-1].objects == ["Box"] and drawn[-1] == d.field_plots[-1].name
    assert d.field_plots[-2].props["Volume"] == "True"
    d.setups.append(Setup("Setup1"))
    win.doc.select([])
    win.plot_mesh()
    assert win._pick_bar.windowTitle() == "Pick objects"
    win.doc.select([Pick("Box")])
    win._pick_bar._use()
    assert d.field_plots[-1].quantity == "Mesh" and d.field_plots[-1].objects == ["Box"]
    win.doc.dirty = False
    win.close()


def test_report_axes_show_units():
    """The y axis of a report, the radial axis of a polar plot and the table headers name the unit of what
    is plotted."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui.doc import Document
    from hertzon.gui.reports import trace_unit, unit_label, with_unit
    from hertzon.model.design import FieldExpression, Project
    doc = Document(Project())
    doc.design.add_object("CreateBox", "Box", Position="0,0,0", XSize="1", YSize="1", ZSize="1")
    doc.rebuild()
    doc.design.field_exprs += [FieldExpression("Pin", "flux(Box, re(S))"), FieldExpression("RL", "-dB(S(1,1))")]
    cases = {"dB(S(1,1))": "dB", "mag(Z(1,1))": "Ω", "re(Y(2,1))": "S", "ang_deg(S(2,1))": "deg",
             "VSWR(1)": "", "GroupDelay(S(2,1))": "s", "Pin": "W", "Pin @ Setup1 : Sweep": "W", "2*Pin": "W",
             "dB(Pin)": "dB", "RL": "dB", "dB(GainTotal)": "dB", "mag(rETotal)": "V", "dBsm(MonostaticRCSTotal)": "dBsm",
             "MonostaticRCSTotal": "m²", "mag(NearETotal)": "V/m", "RadiatedPower": "W", "Pin + dB(S(1,1))": None,
             "myvar": None}
    for tr, u in cases.items():
        assert trace_unit(doc, tr) == u, tr
    assert unit_label(["dB", "dB"]) == "[dB]" and unit_label(["dB", "Ω", None]) == "[dB] / [Ω]"
    assert unit_label(["", None]) == ""
    assert with_unit(doc, "Pin") == "Pin [W]" and with_unit(doc, "VSWR(1)") == "VSWR(1)"


def test_open_library_imports_ticked_equations():
    """Equations > Open Library: the ticked equations and functions go into the design at once."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui import calculator as C
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.design import Project
    dlg = C.LibraryDialog()
    assert not dlg.ok.isEnabled()
    dlg.tick(["w_e", "p_wall_cu", "voltage"])
    assert dlg.ok.isEnabled() and dlg.ok.text() == "Import (3)"
    eqs, funcs = dlg.chosen()
    assert [e[0] for e in eqs] == ["w_e", "p_wall_cu"]
    assert funcs == ["voltage(L) = int_line(L, E)"]
    dlg.groups["Functions"].setCheckState(0, Qt.Checked)        # the whole group
    assert len(dlg.chosen()[1]) == len(C.LIB.FUNCTIONS)
    win = MainWindow(Project())
    exec_ = C.LibraryDialog.exec

    def fake_exec(self):
        self.tick(["w_e", "emax"])
        return QDialog.Accepted
    C.LibraryDialog.exec = fake_exec
    try:
        win.open_equation_library()
    finally:
        C.LibraryDialog.exec = exec_
    assert [(e.name, e.expr) for e in win.des.field_exprs] == [("w_e", "0.25*eps0*eps_r*|E|^2")]
    assert win.des.field_funcs == ["emax(G) = max(G, |E|)"]
    win.doc.dirty = False
    win.close()


def test_vector_value_in_a_fields_report(monkeypatch):
    """A vector value (an integral of a vector field) goes into Fields reports as its magnitude and components."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from hertzon.gui import calc_bridge as CB
    from hertzon.gui import reports as R
    from hertzon.gui.doc import Document
    from hertzon.model.design import FieldExpression, Project, Report
    doc = Document(Project())
    d = doc.design
    d.add_object("CreateBox", "Box", Position="0,0,0", XSize="1", YSize="1", ZSize="1")
    doc.rebuild()
    d.field_exprs += [FieldExpression("Spp", "int_surf(Box, E × H)"), FieldExpression("Pin", "flux(Box, re(S))")]
    assert CB.value_names(doc, fields=True) == ["mag(Spp)", "x(Spp)", "y(Spp)", "z(Spp)", "Pin"]
    assert CB.vector_parts(doc, "2*mag(Spp) + x( Spp )") == ("2*_part0 + _part1", {"_part0": "mag(Spp)",
                                                                                   "_part1": "x( Spp )"})
    assert R.trace_unit(doc, "mag(Spp)") == R.trace_unit(doc, "x(Spp)") == "W"
    r = Report("R", "Rectangular", "Setup1 : LastAdaptive", ["mag(Spp)"], {"fields": "1"})
    assert R.is_fields_report(doc, r)
    seen = []

    def over(doc_, src, *a, **k):
        seen.append(src)
        return np.array([2.45e9]), np.array([{"mag(Spp)": 3.0, "x(Spp)": 1 + 2j}[src.replace(" ", "")]])
    monkeypatch.setattr(CB, "over_frequency", over)
    monkeypatch.setattr(R, "load_solution", lambda *a, **k: (None, None, None))
    w = R.ReportWindow(doc, r)
    x, y = w._field_trace("mag(Spp) + x(Spp)", None)
    # complex without a function: drawn as its magnitude, like S(1,1) (#136)
    assert seen == ["mag(Spp)", "x(Spp)"] and list(x) == [2.45] and y == pytest.approx([abs(4 + 2j)])
    assert CB.uses_fields(doc, ["mag(Spp)"])          # Add to Report of a vector value: a Fields report
