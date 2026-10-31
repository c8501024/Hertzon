r"""Fields Calculator (Results > Fields > Calculator): the Equations tab next to the 3D Modeler.

The tab only defines equations; they are used in Plot Fields (fields) and in reports (values). In the middle the
equation with its name, typed into the typeset formula editor (Formula: \times + Space gives ×, \iint + Space a
surface integral) or as one line of text (Text: the same function names as the report expressions); right its
type (scalar or vector, field or value), unit and where it is used. The building blocks (fields, symbols,
structures) are on the Equation page of the main ribbon, shown while the tab is open. An operation over a
geometry (integral, maximum, ...) switches to the 3D Modeler and asks for the face, object or line; the pick
goes below the sign. Equations are saved with the design (always as text) and are listed in the project tree
under Equations."""
from __future__ import annotations

import io
import re

from PySide6.QtCore import QRectF, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
                               QSplitter, QStackedWidget, QTextEdit, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..post.calc import lang as L
from ..post.calc import latex as TX
from ..post.calc import library as LIB
from . import calc_bridge as CB
from . import fields as F
from . import formula_editor as FE

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
ERR = "#c0392b"
MUTED = "#6b7280"


def render_tex(tex: str, px: int = 18) -> QPixmap | None:
    """The formula typeset with matplotlib's mathtext as a pixmap (None if it cannot be drawn)."""
    try:
        from matplotlib import mathtext
        from matplotlib.font_manager import FontProperties
        buf = io.BytesIO()
        mathtext.math_to_image("$" + tex + "$", buf, prop=FontProperties(size=px), dpi=100, format="png",
                               color="#1f2937")
        pm = QPixmap()
        pm.loadFromData(buf.getvalue(), "PNG")
        return pm if not pm.isNull() else None
    except Exception:  # noqa: BLE001
        return None


def call_at(text: str, pos: int) -> tuple[str, int] | None:
    """(function, index of the argument) the cursor at pos is in, or None."""
    try:
        toks = L.tokenize(text[:pos])
    except L.CalcError:
        return None
    stack: list[list] = []
    for i, t in enumerate(toks):
        if t.t == "op" and t.v in "([":
            fn = toks[i - 1].v if t.v == "(" and i > 0 and toks[i - 1].t == "id" else None
            stack.append([fn, 0])
        elif t.t == "op" and t.v in ")]":
            if stack:
                stack.pop()
        elif t.t == "op" and t.v == "," and stack:
            stack[-1][1] += 1
    for fn, k in reversed(stack):
        if fn:
            return fn, k
    return None


class ExprEdit(QPlainTextEdit):
    """The expression line: errors are underlined, names complete as you type."""

    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(11)
        self.setFont(f)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 14)
        self.setPlaceholderText("e.g.  flux(Port1, re(S))   or   0.5*omega*eps0*eps_r*tand*|E|^2")
        self.completer = QCompleter(self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.activated.connect(self._complete)
        self.model = QStringListModel(self)
        self.completer.setModel(self.model)

    def set_words(self, words: list[str]) -> None:
        self.model.setStringList(sorted(set(words), key=str.lower))

    def _prefix(self) -> str:
        c = self.textCursor()
        text = self.toPlainText()[:c.position()]
        m = re.search(r"[A-Za-z_][A-Za-z_0-9]*$", text)
        return m.group(0) if m else ""

    def _complete(self, word: str) -> None:
        c = self.textCursor()
        c.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(self._prefix()))
        c.insertText(word)
        self.setTextCursor(c)

    def insert(self, text: str) -> None:
        c = self.textCursor()
        c.insertText(text)
        self.setTextCursor(c)
        self.setFocus()

    def underline(self, start: int | None, end: int | None) -> None:
        if start is None:
            self.setExtraSelections([])
            return
        n = len(self.toPlainText())
        start = max(0, min(start, n))
        end = max(start + 1, min(end if end is not None else start + 1, n))
        if start >= n:
            start, end = max(0, n - 1), n
        sel = QTextEdit.ExtraSelection()
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.WaveUnderline)
        fmt.setUnderlineColor(QColor(ERR))
        sel.format = fmt
        c = self.textCursor()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        sel.cursor = c
        self.setExtraSelections([sel])

    def keyPressEvent(self, e):
        pop = self.completer.popup()
        if pop.isVisible() and e.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Tab, Qt.Key_Escape):
            e.ignore()
            return
        if e.key() in (Qt.Key_Enter, Qt.Key_Return):
            self.submitted.emit()
            return
        super().keyPressEvent(e)
        p = self._prefix()
        if len(p) >= 2 and e.text() and (e.text().isalnum() or e.text() == "_"):
            self.completer.setCompletionPrefix(p)
            if self.completer.completionCount():
                r = self.cursorRect()
                r.setWidth(220)
                self.completer.complete(r)
                return
        pop.hide()


STYLE = """
QWidget#Right { background:#fbfbfc; }
QLabel[role="h"] { color:#5a6476; font-size:11px; font-weight:600; letter-spacing:0.5px; }
QLabel[role="muted"] { color:#6b7280; }
QLineEdit#Name { font-size:20px; font-weight:600; border:none; border-bottom:1px solid #c3c8d2;
                 background:transparent; padding:2px; }
QLineEdit#Desc { border:none; border-bottom:1px dashed #c3c8d2; background:transparent; color:#4b5563;
                 padding:2px; }
QFrame#Group { background:#ffffff; border:1px solid #d6dae1; border-radius:4px; }
QFrame#Group QLabel { color:#7a8496; font-size:10px; font-weight:600; border:none; }
QLabel#Formula { background:#ffffff; border:1px solid #c3c8d2; border-radius:4px; }
QLabel#Value { font-size:30px; font-weight:600; }
QPushButton#Primary { background:#2f5bd3; color:white; border:1px solid #2a50bb; border-radius:4px;
                      padding:5px 14px; font-weight:600; }
QPushButton#Primary:hover { background:#2a50bb; }
QLabel#Banner { background:#fff7e0; border:1px solid #e6c766; border-radius:4px; padding:6px; }
QLabel#AsText { color:#4b5563; font-family:Consolas,monospace; }
"""
# the keys of the Equation page of the main ribbon
KEY_STYLE = """
QToolButton[role="sym"] { font-family:"Cambria Math","STIX Two Math","Times New Roman",serif; font-size:15px;
                          min-width:26px; max-width:46px; min-height:24px; border:1px solid #e1e4ea;
                          border-radius:3px; background:#fafbfc; padding:1px 4px; }
QToolButton[role="sym"]:hover { background:#e8eefc; border-color:#9fb4ee; }
QToolButton[role="sym"][field="1"] { font-weight:bold; }
QToolButton[role="sym"]::menu-indicator { width:0px; }
"""
MATH_FONT = ("Cambria Math", "STIX Two Math", "Latin Modern Math", "DejaVu Serif", "Times New Roman")


def glyph_icon(text: str, px: int = 22) -> QIcon:
    """A ribbon icon drawn from a formula glyph (∬, √x, a/b) in a math font."""
    pm = QPixmap(96, 64)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont()
    f.setFamilies(list(MATH_FONT))
    f.setPixelSize(px)
    p.setFont(f)
    p.setPen(QColor("#1f2633"))
    p.drawText(QRectF(0, 0, 48, 32), Qt.AlignCenter, text)
    p.end()
    return QIcon(pm)


# Equation page: (label, LaTeX for the formula editor, text for the Text view, tooltip). In the LaTeX #@ is the
# selection (or an empty box) and #? an empty box.
FIELD_KEYS = [("E", "E", "E", "electric field (V/m)"), ("H", "H", "H", "magnetic field (A/m)"),
              ("J", "J", "J", "volume current density (A/m²)"),
              ("Jₛ", "J_s", "Js", "surface current density n×H (A/m), faces only"),
              ("S", "S", "S", "complex Poynting vector ½ E×H* (W/m²); S parameters: S Parameter under Network"), ("D", "D", "D", "electric flux density"),
              ("B", "B", "B", "magnetic flux density"), ("n", r"\mathbf{n}", "n", "unit normal of the face, faces only")]
SYMBOL_KEYS = [("·", r"\cdot ", "·", "dot product   (\\cdot)"), ("×", r"\times ", "×", "cross product   (\\times)"),
               ("ε₀", r"\varepsilon_0", "eps0", "vacuum permittivity   (\\eps0)"),
               ("μ₀", r"\mu_0", "mu0", "vacuum permeability   (\\mu0)"),
               ("ω", r"\omega", "omega", "angular frequency 2πf   (\\omega)"),
               ("k₀", "k_0", "k0", "free-space wave number"), ("π", r"\pi", "pi", "pi   (\\pi)"),
               ("j", "j", "j", "imaginary unit"),
               ("εᵣ", r"\varepsilon_r", "eps_r", "relative permittivity of the material   (\\epsr)"),
               ("μᵣ", r"\mu_r", "mu_r", "relative permeability of the material   (\\mur)"),
               ("tanδ", r"\tan\delta", "tand", "loss tangent of the material   (\\tand)"),
               ("σ", r"\sigma", "sigma", "conductivity of the material   (\\sigma)"),
               ("c₀", "c_0", "c0", "speed of light"), ("Z₀", "Z_0", "Z0", "free-space impedance"),
               ("f", "f", "f", "frequency of the solution"), ("°", r"^{\circ}", "deg", "degree, after a number")]
# Structures: (button, glyph, [(label, LaTeX, text, tooltip)])
STRUCTURES = [
    ("Fraction", "a⁄b", [("a⁄b   Fraction", r"\frac{#@}{#?}", "/", "stacked fraction"),
                         ("½   One half", r"\frac{1}{2}", "1/2", "")]),
    ("Script", "xⁿ", [("xⁿ   Superscript", "#@^{#?}", "^", "power"), ("x²   Square", "#@^2", "^2", ""),
                      ("x₀   Subscript", "#@_{#?}", "_", "names with an index, e.g. P_in")]),
    ("Radical", "√x", [("√x   Square root", r"\sqrt{#@}", "sqrt(", "")]),
    ("Bracket", "|x|", [("|x|   Magnitude", r"\left|#@\right|", "|", "magnitude; of a vector the length of the "
                                                                     "peak phasor"),
                        ("(x)   Parentheses", r"\left(#@\right)", "(", "")]),
    ("Function", "Re", [("Re{ }   Real part", r"\operatorname{Re}\left\{#@\right\}", "re(", "   (\\Re)"),
                        ("Im{ }   Imaginary part", r"\operatorname{Im}\left\{#@\right\}", "im(", "   (\\Im)"),
                        ("x*   Complex conjugate", r"\operatorname{conj}\left(#@\right)", "conj(", ""),
                        ("rms   RMS value", r"\operatorname{rms}\left(#@\right)", "rms(", "peak / √2"),
                        ("inst   Instantaneous value", r"\operatorname{inst}\left(#@\right)", "inst(",
                         "at the phase of the context"),
                        ("∠   Phase angle", r"\operatorname{phase}\left(#@\right)", "phase(", ""),
                        ("dB   20·log₁₀|x|", r"\operatorname{dB}\left(#@\right)", "dB(", ""),
                        ("eˣ   Exponential", r"\exp\left(#@\right)", "exp(", ""),
                        ("Rₛ   Surface resistance", r"\operatorname{rs}\left(#@\right)", "rs(",
                         "√(ωμ₀/2σ) of a good conductor in Ω, e.g. rs(5.8e7S/m) for copper"),
                        ("sin   Sine", r"\sin\left(#@\right)", "sin(", ""),
                        ("cos   Cosine", r"\cos\left(#@\right)", "cos(", ""),
                        ("F̂   Unit vector F/|F|", r"\operatorname{unit}\left(#@\right)", "unit(", "")]),
    ("Operator", "∇×", [("∇×   Curl", r"\nabla\times #@", "curl(", "   (\\curl)"),
                        ("∇·   Divergence", r"\nabla\cdot #@", "div(", "   (\\divg)"),
                        ("∇   Gradient", r"\nabla #@", "grad(", "   (\\grad)")]),
]
# integrals and large operators over a geometry: (label, function, dimension, tooltip, LaTeX, command)
INTEGRAL_MENU = [
    ("∫   Line integral", "int_line", 1, "∫ F·dl along a line", r"\int_{\placeholder{}}#@\cdot\mathrm{d}\mathbf{l}", "int"),
    ("∮   Circulation", "circ", 1, "∮ H·dl around a closed line", r"\oint_{\placeholder{}}#@\cdot\mathrm{d}\mathbf{l}",
     "oint"),
    ("∬   Surface integral", "int_surf", 2, "∬ f dA over a face", r"\iint_{\placeholder{}}#@\,\mathrm{d}A", "iint"),
    ("∬·n   Flux through a face", "flux", 2, "∬ F·n dA, along the normal of the face",
     r"\iint_{\placeholder{}}#@\cdot\mathbf{n}\,\mathrm{d}A", "flux"),
    ("∯   Flux out of a closed surface", "flux", 2, "∯ F·n dA, outward",
     r"\oiint_{\placeholder{}}#@\cdot\mathbf{n}\,\mathrm{d}A", "oiint"),
    ("∭   Volume integral", "int_vol", 3, "∭ f dV over a volume", r"\iiint_{\placeholder{}}#@\,\mathrm{d}V", "iiint")]
LARGE_OPS = [("max   Maximum", "max", -1, "maximum over a geometry", r"\max_{\placeholder{}}\left(#@\right)", "max"),
             ("min   Minimum", "min", -1, "minimum over a geometry", r"\min_{\placeholder{}}\left(#@\right)", "min"),
             ("avg   Average", "avg", -1, "mean value over a line, face or volume",
              r"\operatorname{avg}_{\placeholder{}}\left(#@\right)", "avg"),
             ("f|ₚ   Value at a point", "at", 0, "value at a point", r"\left.#@\right|_{\placeholder{}}", "at")]
# operations over a geometry: (label, function, dimension of the geometry, tooltip)
GEOMETRY_OPS = [("∫dl", "int_line", 1, "line integral (vectors: tangential F·dl)"),
                ("∮dl", "circ", 1, "circulation around a closed line"),
                ("∬dA", "int_surf", 2, "surface integral"),
                ("∯·n", "flux", 2, "flux of a vector through a face, along its normal"),
                ("∭dV", "int_vol", 3, "volume integral"),
                ("max", "max", -1, "maximum over a geometry"), ("avg", "avg", -1, "mean value over a geometry"),
                ("@pt", "at", 0, "value at a point")]
WHAT = {0: "a point", 1: "a line", 2: "a face", 3: "a volume", -1: "a geometry"}


def shortcuts(env: L.Env) -> dict:
    """Words the formula editor replaces as they are typed: functions (flux -> flux), names of the model and
    of saved expressions, and a few symbols. A word that begins another one is left out (it would fire
    before the longer one can be typed)."""
    fns = [n for n in list(L.SIGNATURES) + list(env.functions)
           if len(n) > 2 and "_" not in n and n not in L.COMP_FN and n not in ("max", "min", "avg")]
    names = [n for n in list(env.geometry or {}) + list(env.named or {}) + list(env.cs_names)
             if len(n) > 1 and "_" not in n and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", n)]
    words = set(fns) | set(names) | set(L.SYMBOLS)

    def free(w):
        return not any(o != w and o.startswith(w) for o in words)
    sc = {n: r"\operatorname{" + n + "}" for n in fns if free(n)}
    sc.update({n: r"\mathrm{" + n + "}" for n in names if free(n) and n not in L.SIGNATURES})
    sc.update({"omega": r"\omega", "pi": r"\pi", "sigma": r"\sigma", "eps0": r"\varepsilon_0", "mu0": r"\mu_0",
               "tand": r"\tan\delta"})
    return sc


def _h(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "h")
    return lab


class PickBar(QDialog):
    """Shown over the 3D Modeler: asks for the geometry of an integral (or any name) to be picked in the 3D
    view; Use puts it into the equation and goes back to the Equations tab."""

    def __init__(self, calc: "EquationsTab", fn: str | None, dim: int):
        super().__init__(calc.win, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.calc, self.fn, self.dim = calc, fn, dim
        self.doc = calc.doc
        self.setWindowTitle("Fields Calculator: pick")
        self.setAttribute(Qt.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        op = next((o for o in GEOMETRY_OPS if o[1] == fn), None)
        title = QLabel(f"<b>{op[0] + '  ' + op[3] if op else 'Insert a name'}</b>")
        lay.addWidget(title)
        how = {2: "Click a face in the 3D view (Ctrl+click: several faces).",
               3: "Click an object in the 3D view (Ctrl+click: several).",
               1: "Click a line (polyline) in the 3D view.",
               0: "Choose a point object or type a point below.",
               -1: "Click an object or a face in the 3D view."}[dim]
        lay.addWidget(QLabel(f"Select {WHAT[dim]}: {how}"))
        self.sel = QLabel("Selected: —")
        self.sel.setStyleSheet("font-family:Consolas,monospace")
        lay.addWidget(self.sel)
        row = QHBoxLayout()
        row.addWidget(QLabel("or choose"))
        self.names = QComboBox()
        self.names.setEditable(True)
        self.names.setMinimumWidth(220)
        env = CB.make_env(self.doc)
        dims = {0, 1, 2, 3} if dim < 0 else {dim}
        items = [n for n, g in env.geometry.items() if set(g.dims) & dims and (fn != "circ" or g.closed)]
        if dims & {2}:
            items += ["cut_xy(0)", "cut_yz(0)", "cut_xz(0)"]
        if dims & {0}:
            items += ["point(0, 0, 0)"]
        self.names.addItem("")
        self.names.addItems(items)
        self.names.currentTextChanged.connect(self._show)
        row.addWidget(self.names, 1)
        lay.addLayout(row)
        self.flip = QCheckBox("Reverse the normal" if dim == 2 else "Reverse the direction")
        self.flip.setVisible(dim in (1, 2))
        lay.addWidget(self.flip)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Use")
        bb.accepted.connect(self._use)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.mode0 = self.doc.selection_mode
        mode = {2: "face", 3: "object", 1: "object", 0: "object", -1: self.mode0}[dim]
        if mode != self.mode0:
            calc.win.set_mode(mode)
        self.doc.selectionChanged.connect(self._show)
        self.finished.connect(self._back)
        self._show()
        g = calc.win.geometry()
        self.adjustSize()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 90)

    def geometry_text(self) -> str:
        typed = self.names.currentText().strip()
        names = [typed] if typed else []
        if not names:
            tess = self.doc.tess
            for s in self.doc.selection:
                t = tess.get(s.obj)
                sheet = t is not None and getattr(t, "dim", 3) == 2
                n = f"face({s.obj}, {s.index})" if self.doc.selection_mode == "face" and s.index >= 0 \
                    and not sheet else s.obj
                if n not in names:
                    names.append(n)
        if self.flip.isChecked() and self.dim in (1, 2):
            names = [f"flip({n})" for n in names]
        return "" if not names else names[0] if len(names) == 1 else "[" + ", ".join(names) + "]"

    def _show(self, *_):
        t = self.geometry_text()
        self.sel.setText(f"Selected: {t or '—'}")

    def _use(self) -> None:
        geo = self.geometry_text()
        if not geo:
            self.sel.setText(f"<span style='color:{ERR}'>Select {WHAT[self.dim]} first</span>")
            return
        self.calc.insert_geometry(self.fn, geo)
        self.accept()

    def _back(self, *_):
        try:
            self.doc.selectionChanged.disconnect(self._show)
        except (RuntimeError, TypeError):
            pass
        if self.doc.selection_mode != self.mode0:
            self.calc.win.set_mode(self.mode0)
        tabs = self.calc.win.tabs
        if tabs.indexOf(self.calc) >= 0:
            tabs.setCurrentWidget(self.calc)
        self.calc.focus_editor()


class EquationsTab(QWidget):
    """The Fields Calculator as the Equations tab next to the 3D Modeler: in the middle the formula with its
    name, right its type, unit and uses. The building blocks are on
    the Equation page of the main ribbon (build_ribbon), shown while this tab is open; the saved equations
    are in the project tree."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.doc = win.doc
        self.setObjectName("FieldsCalculator")
        self.setStyleSheet(STYLE)
        self.current: str | None = None        # name of the loaded project expression
        self.saved_text = ""
        self.info: L.Info | None = None
        self.for_plot = False
        self._last: tuple | None = None       # (text, context) of the shown result
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._analyze)
        self.mode = "lin"                     # "pro": typeset formula editor, "lin": one line of text
        self.tex_error: str | None = None     # why the formula of the editor is not an expression yet
        self._from_tex = False
        self.b_formula: QToolButton | None = None     # Formula | Text on the Equation page of the ribbon
        self.b_text: QToolButton | None = None
        self.fe: FE.FormulaEditor | None = None
        if FE.available():
            try:
                self.fe = FE.FormulaEditor()
            except Exception:  # noqa: BLE001   (no web engine on this system: the text line only)
                self.fe = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._work())
        split.addWidget(self._right())
        split.setStretchFactor(0, 1)
        split.setSizes([900, 330])
        lay.addWidget(split, 1)
        self.doc.changed.connect(self._doc_changed)
        st = getattr(win, "settings", None)
        mode = st.value("calc/mode", "pro") if st is not None else "pro"
        self.refresh()
        self.set_mode(mode if self.fe is not None else "lin")
        self.new()

    # ---- the Equation page of the main ribbon ----------------------------------------------------
    def build_ribbon(self, page) -> None:
        g = page.group("Tools")
        b = self._big(g, "Equation", "π", None, "A new equation, or one from the library")
        m = QMenu(b)
        m.addAction("New Equation", self.new)
        m.addSeparator()
        m.addSection("Library")
        for name, _text, desc in LIB.EXPRESSIONS:
            m.addAction(desc.split(" (")[0], lambda _=False, k=name: self._load_library(k))
        m.addSeparator()
        m.addAction("Functions...", self.functions_dialog)
        b.setMenu(m)
        b.setPopupMode(QToolButton.InstantPopup)
        self.b_formula = g.small("Formula", None, lambda: self.set_mode("pro"),
                                 tip="Type the formula typeset, as in a formula editor (\\iint + Space: ∬)",
                                 checkable=True)
        self.b_text = g.small("Text", None, lambda: self.set_mode("lin"),
                              tip="Type the formula as one line of text: flux(Port1, re(S))", checkable=True)
        g.small("Pick from 3D", None, lambda: self.pick(None, -1),
                tip="Click an object or face in the 3D Modeler and insert its name")
        if self.fe is None:
            self.b_formula.setEnabled(False)
            self.b_formula.setToolTip("The formula editor needs Qt WebEngine (part of PySide6)")
        self.b_formula.setChecked(self.mode == "pro")
        self.b_text.setChecked(self.mode == "lin")
        g = page.group("Fields")
        g.row.addWidget(self._keys(FIELD_KEYS, 4, field=True))
        g = page.group("Symbols")
        g.row.addWidget(self._keys(SYMBOL_KEYS, 8))
        g = page.group("Network")
        b = self._big(g, "S Parameter", "S₁₁", None,
                      "S, Y and Z parameters and VSWR between the ports of the model, e.g. S(2, 1)")
        m = QMenu(b)
        m.setToolTipsVisible(True)
        m.aboutToShow.connect(lambda mm=m: self._network_menu(mm))
        self._network_menu(m)
        b.setMenu(m)
        b.setPopupMode(QToolButton.InstantPopup)
        g = page.group("Structures")
        for name, glyph, items in STRUCTURES[:3]:
            self._struct(g, name, glyph, items)
        self._geo_menu(g, "Integral", "∬", INTEGRAL_MENU)
        self._geo_menu(g, "Large\nOperator", "max", LARGE_OPS)
        for name, glyph, items in STRUCTURES[3:]:
            self._struct(g, name, glyph, items)
        self._big(g, "Component", "F_φ", self.component_dialog,
                  "a component of a vector in a coordinate system (x, ρ, φ, r, θ, …)")
        page.setStyleSheet(KEY_STYLE)

    def _ports(self) -> tuple[int, dict[int, str]]:
        """(number of ports, port number -> name) for the S Parameter menu: the ports of the solved data if
        there is a solution, otherwise the enabled ports of the design."""
        des = self.doc.design
        names = {}
        for k, e in enumerate(des.ports(), 1):
            try:
                names[int(e.props.get("PortNumber", "") or k)] = e.name
            except ValueError:
                names[k] = e.name
        n = len(names)
        try:
            from .reports import load_solution, solution_list
            for sol in solution_list(self.doc):
                if "AdaptivePass" in sol:
                    continue
                data = load_solution(self.doc, sol)[2]
                if data is not None and "S" in data:
                    n = int(data["S"].shape[1])
                    break
            if n != len(names):           # ports with several modes: the columns are not the port numbers
                names = {}
        except Exception:  # noqa: BLE001   (no or unreadable results: the ports of the design)
            pass
        return n, names

    def _network_menu(self, m: QMenu) -> None:
        """The S Parameter menu of the ribbon, built from the ports of the model each time it opens."""
        for sub in m.findChildren(QMenu):
            sub.deleteLater()
        m.clear()
        n, names = self._ports()
        if not n:
            a = m.addAction("No ports in the design")
            a.setEnabled(False)
            a.setToolTip("Assign a wave or lumped port; then S(i, j) between the ports can be inserted here")
            return

        def label(i):
            return f" ({names[i]})" if i in names and names[i] != str(i) else ""

        def matrix(menu: QMenu, kind: str) -> None:
            for i in range(1, n + 1):
                sub = menu if n <= 3 else menu.addMenu(f"{kind}({i}, ·){label(i)}")
                sub.setToolTipsVisible(True)
                for j in range(1, n + 1):
                    text = f"{kind}({i}, {j})"
                    if kind == "S":
                        tip = (f"reflection at port {i}{label(i)}" if i == j else
                               f"transmission from port {j}{label(j)} to port {i}{label(i)}")
                    else:
                        tip = (f"{'admittance' if kind == 'Y' else 'impedance'} parameter of port {i}{label(i)}"
                               + ("" if i == j else f" and port {j}{label(j)}"))
                    a = sub.addAction(text, lambda _=False, t=text: self.insert_text(t))
                    a.setToolTip(tip + "; complex: dB, |x| or ∠ from Function")

        m.addSection("S parameters")
        matrix(m, "S")
        m.addSeparator()
        for kind in ("Y", "Z"):
            matrix(m.addMenu(f"{kind} parameters"), kind)
        sub = m.addMenu("VSWR")
        sub.setToolTipsVisible(True)
        for i in range(1, n + 1):
            a = sub.addAction(f"VSWR({i}){label(i)}", lambda _=False, t=f"VSWR({i})": self.insert_text(t))
            a.setToolTip(f"voltage standing wave ratio at port {i}")

    # ---- layout ----------------------------------------------------------------------------------
    def _big(self, g, text: str, glyph: str, slot, tip: str) -> QToolButton:
        g.end_column()
        b = QToolButton()
        b.setObjectName("RibbonLarge")
        b.setAutoRaise(True)
        b.setText(text)
        b.setIcon(glyph_icon(glyph))
        b.setIconSize(QSize(48, 32))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        b.setToolTip(tip)
        if slot is not None:
            b.clicked.connect(lambda _=False: slot())
        g.row.addWidget(b)
        g._col = None
        return b

    def _keys(self, keys, per_row: int, field: bool = False) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(2)
        for k, (lab, tex, text, tip) in enumerate(keys):
            b = QToolButton()
            b.setText(lab)
            b.setToolTip(tip)
            b.setProperty("role", "sym")
            if field:
                b.setProperty("field", "1")
            b.clicked.connect(lambda _=False, t=tex, x=text: self.put(t, x))
            grid.addWidget(b, k // per_row, k % per_row)
        return w

    def _struct(self, g, name: str, glyph: str, items) -> None:
        if len(items) == 1:
            lab, tex, text, tip = items[0]
            self._big(g, name, glyph, lambda t=tex, x=text: self.put(t, x), lab.split("   ")[1] + tip)
            return
        b = self._big(g, name, glyph, None, name)
        m = QMenu(b)
        for lab, tex, text, tip in items:
            a = m.addAction(lab, lambda t=tex, x=text: self.put(t, x))
            a.setToolTip(tip)
        m.setToolTipsVisible(True)
        b.setMenu(m)
        b.setPopupMode(QToolButton.InstantPopup)

    def _geo_menu(self, g, name: str, glyph: str, items) -> None:
        b = self._big(g, name, glyph, None, name.replace("\n", " ") + " over a geometry: pick it in the 3D view")
        m = QMenu(b)
        for lab, fn, dim, tip, tex, cmd in items:
            a = m.addAction(lab, lambda f=fn, d=dim, t=tex: self.geometry_op(f, d, t))
            a.setToolTip(f"{tip}   (\\{cmd} and Space)")
        m.setToolTipsVisible(True)
        b.setMenu(m)
        b.setPopupMode(QToolButton.InstantPopup)

    def _work(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.setSpacing(10)
        self.banner = QLabel()
        self.banner.setObjectName("Banner")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        lay.addWidget(self.banner)
        nrow = QHBoxLayout()
        self.desc = QLineEdit()
        self.desc.setObjectName("Desc")
        self.desc.setPlaceholderText("Description (optional)")
        self.desc.textChanged.connect(self._dirty_changed)
        self.unsaved = QLabel("")
        self.unsaved.setProperty("role", "muted")
        self.unsaved.setMinimumWidth(self.unsaved.fontMetrics().horizontalAdvance("unsaved") + 6)
        nrow.addWidget(self.desc, 1)
        nrow.addWidget(self.unsaved)
        b = QPushButton("Save")
        b.setToolTip("Save the equation in the design: it appears in the project tree under Equations")
        b.clicked.connect(self.save)
        nrow.addWidget(b)
        self.a_del = QPushButton("Delete")
        self.a_del.setToolTip("Delete the saved equation")
        self.a_del.clicked.connect(self.delete)
        nrow.addWidget(self.a_del)
        lay.addLayout(nrow)

        # Formula: the formula editor; Text: the typeset preview and the text line
        self.stack = QStackedWidget()
        if self.fe is not None:
            self.fe.setMinimumHeight(220)
            self.fe.latex_changed.connect(self._tex_changed)
            self.fe.submitted.connect(self._analyze)
            self.fe.pick_requested.connect(self.pick)
            self.stack.addWidget(self.fe)
        linear = QWidget()
        ll = QVBoxLayout(linear)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        self.formula = QLabel()
        self.formula.setObjectName("Formula")
        self.formula.setAlignment(Qt.AlignCenter)
        self.formula.setMinimumHeight(150)
        self.formula.setMaximumHeight(280)
        self.formula.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ll.addWidget(self.formula, 1)
        self.edit = ExprEdit()
        self.edit.setPlaceholderText("Name = formula, e.g. P_in = flux(Port1, re(S))")
        self.edit.setFixedHeight(self.edit.fontMetrics().lineSpacing() * 2 + 16)
        self.edit.textChanged.connect(self._text_changed)
        self.edit.cursorPositionChanged.connect(self._signature)
        self.edit.submitted.connect(self._analyze)
        ll.addWidget(self.edit)
        self.stack.addWidget(linear)
        lay.addWidget(self.stack, 1)
        self.astext = QLabel()
        self.astext.setObjectName("AsText")
        self.astext.setWordWrap(True)
        self.astext.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.astext)
        self.sig = QLabel()
        self.sig.setWordWrap(True)
        self.sig.setTextFormat(Qt.RichText)
        self.sig.setProperty("role", "muted")
        self.sig.linkActivated.connect(self.insert_text)
        lay.addWidget(self.sig)
        lay.addStretch(0)
        srow = QHBoxLayout()
        self.status = QLabel()
        srow.addWidget(self.status, 1)
        keys = QLabel("Name = formula, then Save · \\ and a name, then Space: symbol or structure · Tab: next box")
        keys.setProperty("role", "muted")
        srow.addWidget(keys)
        lay.addLayout(srow)
        return w

    def _right(self) -> QWidget:
        w = QWidget()
        w.setObjectName("Right")
        w.setMinimumWidth(280)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.setSpacing(8)
        lay.addWidget(_h("TYPE"))
        self.kind = QLabel("—")
        self.kind.setObjectName("Value")
        lay.addWidget(self.kind)
        self.kind_note = QLabel()
        self.kind_note.setWordWrap(True)
        self.kind_note.setProperty("role", "muted")
        lay.addWidget(self.kind_note)
        lay.addSpacing(6)
        lay.addWidget(_h("UNIT"))
        self.unit = QLabel("—")
        self.unit.setObjectName("Value")
        self.unit.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.unit)
        lay.addSpacing(6)
        lay.addWidget(_h("DETAILS"))
        self.details = QLabel("—")
        self.details.setWordWrap(True)
        self.details.setProperty("role", "muted")
        lay.addWidget(self.details)
        lay.addSpacing(6)
        lay.addWidget(_h("USED BY"))
        self.used = QLabel("—")
        self.used.setWordWrap(True)
        self.used.setProperty("role", "muted")
        lay.addWidget(self.used)
        lay.addStretch(1)
        return w

    # ---- context ---------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Names for completion and for the formula editor."""
        env = CB.make_env(self.doc)
        words = list(L.SYMBOLS) + list(L.SIGNATURES) + list(env.named) + list(env.functions) + \
            list(env.geometry) + [c for c in env.cs_names]
        self.edit.set_words(words)
        if self.fe is not None:
            self.fe.set_shortcuts(shortcuts(env))
        self._analyze()

    def _doc_changed(self, what: str) -> None:
        if what in ("results", "analysis", "fields", "model", "all", "design"):
            cur = self.current
            if cur and not any(e.name == cur for e in self.doc.design.field_exprs):
                self.current = None
                self._dirty_changed()
            self._used_by()

    # ---- equations -----------------------------------------------------------------------------
    def _load_library(self, key: str) -> None:
        name, text, desc = next(x for x in LIB.EXPRESSIONS if x[0] == key)
        self.new()
        self.desc.setText(desc)
        free = not any(e.name == name for e in self.doc.design.field_exprs)
        self.edit.setPlainText(f"{name} = {text}" if free else text)

    def load(self, name: str) -> None:
        e = next((x for x in self.doc.design.field_exprs if x.name == name), None)
        if e is None:
            return
        self.current = name
        self.desc.setText(e.description)
        self.edit.setPlainText(f"{e.name} = {e.expr}")
        self.saved_text = self._state()
        self._dirty_changed()
        self._used_by()

    def new(self) -> None:
        self.current = None
        self.desc.clear()
        self.edit.clear()
        self.saved_text = self._state()
        self._dirty_changed()
        self._used_by()
        self.focus_editor()

    def _state(self) -> str:
        return f"{self.desc.text()}\n{self.edit.toPlainText()}"

    def typed_name(self) -> str:
        """The name of the equation: what is left of = (P_in = flux(Port1, re(S)))."""
        return TX.split_named(self.edit.toPlainText())[0]

    def formula_text(self) -> str:
        """The formula right of the name."""
        return TX.split_named(self.edit.toPlainText())[1]

    def set_name(self, name: str) -> None:
        self.edit.setPlainText(f"{name} = {self.formula_text().strip()}")

    def _dirty_changed(self, *_):
        dirty = self._state() != self.saved_text and bool(self.edit.toPlainText().strip())
        self.unsaved.setText("unsaved" if dirty else ("saved" if self.current else ""))
        self.a_del.setEnabled(self.current is not None)
        tabs = getattr(self.win, "tabs", None)
        if tabs is not None and tabs.indexOf(self) >= 0:
            tabs.setTabText(tabs.indexOf(self), f"Equations: {self.typed_name() or 'new'}{' *' if dirty else ''}")

    def _used_by(self) -> None:
        if not self.current:
            self.used.setText("—")
            return
        des = self.doc.design
        uses = CB.dependents(des, self.current)
        uses += [f"plot {p.name}" for p in des.field_plots if p.quantity == self.current]
        uses += [f"report {r.name}" for r in des.reports
                 if any(self.current in CB.field_names_in(des, t) for t in r.traces)]
        self.used.setText(" · ".join(uses) or "not used yet")

    # ---- typing ----------------------------------------------------------------------------------
    def _text_changed(self) -> None:
        if not self._from_tex:
            self.tex_error = None
            if self.mode == "pro":
                self._push_tex()
        self._dirty_changed()
        self._timer.start()

    # ---- the two views: typeset formula (Formula) and one line of text (Text) ----------------------
    def set_mode(self, mode: str) -> None:
        if mode == "pro" and self.fe is None:
            mode = "lin"
        if mode == "pro" and self.mode != "pro" and not self._push_tex():
            self.status.setText(f"<span style='color:{ERR}'>Fix the error in the text first; then the formula "
                                "can be shown typeset</span>")
            mode = "lin"
        if mode == "lin" and self.mode == "pro" and self.tex_error:
            self.status.setText(f"<span style='color:{ERR}'>The formula was not complete: {self.tex_error}</span>")
        self.mode = mode
        self.stack.setCurrentIndex(0 if mode == "pro" and self.fe is not None else self.stack.count() - 1)
        if self.b_formula is not None:
            self.b_formula.setChecked(mode == "pro")
            self.b_text.setChecked(mode == "lin")
        self.astext.setVisible(mode == "pro")
        st = getattr(self.win, "settings", None)
        if st is not None:
            st.setValue("calc/mode", mode)
        self._analyze()
        self.focus_editor()

    def _push_tex(self) -> bool:
        """The text into the formula editor; False if the text is not an expression (it stays as text)."""
        if self.fe is None:
            return False
        name, text, _ = TX.split_named(self.edit.toPlainText())
        try:
            tex = TX.to_latex(text, CB.make_env(self.doc))
        except L.CalcError:
            return False
        if name:
            tex = TX.name_tex(name) + "=" + tex
        self.tex_error = None
        self.fe.set_latex(tex)
        return True

    def _tex_changed(self, tex: str) -> None:
        """An edit in the formula editor: its text, or why it is not an expression yet."""
        try:
            name, rest = TX.split_named_tex(tex)
            text = TX.to_text(rest, CB.make_env(self.doc))
            if name:
                text = f"{name} = {text}"
            err = None
        except TX.LatexError as e:
            text, err = "", str(e)
        self.tex_error = err
        self._from_tex = True
        try:
            if text != self.edit.toPlainText():
                self.edit.setPlainText(text)
            else:
                self._dirty_changed()
                self._timer.start()
        finally:
            self._from_tex = False

    def focus_editor(self) -> None:
        if self.mode == "pro" and self.fe is not None:
            self.fe.focus_formula()
        else:
            self.edit.setFocus()

    def put(self, tex: str, text: str) -> None:
        """A key of the Equation page: LaTeX into the formula editor, text into the text line."""
        if self.mode == "pro" and self.fe is not None:
            self.fe.insert(tex)
        else:
            self.edit.insert(text)

    def insert_text(self, text: str) -> None:
        """A name or an expression at the cursor, in either view."""
        if self.mode == "pro" and self.fe is not None:
            try:
                self.fe.insert(TX.to_latex(text, CB.make_env(self.doc)))
                return
            except L.CalcError:
                pass
        self.edit.insert(text)

    def geometry_op(self, fn: str, dim: int, tex: str) -> None:
        """An integral or operator over a geometry from the ribbon: around the selection (or the whole field
        expression), then the geometry is picked in the 3D view."""
        if self.mode == "pro" and self.fe is not None:
            self._analyze()
            i = self.info
            whole = i is not None and i.field
            if whole and fn in ("max", "min", "avg") and (i.vec or i.cx):
                tex = tex.replace("#@", r"\left|#@\right|")
            self.fe.wrap(tex, whole)
        self.pick(fn, dim)

    def _analyze(self) -> None:
        full = self.edit.toPlainText().strip()
        _, text, off = TX.split_named(self.edit.toPlainText())
        text = text.strip()
        self.info = None
        self.edit.underline(None, None)
        self.astext.setText(f"as text:  {full}" if full else "")
        if self.mode == "pro" and self.tex_error:
            self._show_kind(None)
            self.status.setText(f"<span style='color:{ERR}'>⚠ {self.tex_error}</span>")
            self.astext.setText(f"<span style='color:{ERR}'>⚠ {self.tex_error}</span>")
            return
        if not text:
            self.formula.clear()
            self.formula.setText("<span style='color:#98a0ae;font-size:15px'>The formula appears here as you "
                                 "type or click the building blocks</span>")
            self._show_kind(None)
            self.status.setText("")
            return
        env = CB.make_env(self.doc)
        try:
            self.info = L.analyze(self.formula_text(), env)
        except L.CalcError as e:
            self.edit.underline(None if e.pos is None else e.pos + off, None if e.end is None else e.end + off)
            self._show_kind(None)
            self.status.setText(f"<span style='color:{ERR}'>⚠ {e}</span>")
            if self.mode == "pro":
                self.astext.setText(f"as text:  {full}   <span style='color:{ERR}'>⚠ {e}</span>")
            self.formula.clear()
            self.formula.setText(f"<span style='color:{ERR};font-size:14px'>{e}</span>")
            return
        i = self.info
        pm = render_tex(i.tex, px=30)
        if pm is not None:
            self.formula.setPixmap(pm)
        else:
            self.formula.setText(text)

        self._show_kind(i)
        self.status.setText("✓ No errors")

    def _show_kind(self, i: L.Info | None) -> None:
        """Right: scalar or vector, field or value, and the unit of the equation."""
        if i is None:
            for w in (self.kind, self.unit):
                w.setText("—")
            self.kind_note.setText("")
            self.details.setText("—")
            return
        self.kind.setText(f"{'Vector' if i.vec else 'Scalar'} {'field' if i.field else 'value'}")
        integral = re.search(r"\b(int_line|int_surf|int_vol|circ|flux|max|min|avg|at)\s*\(", self.formula_text())
        where = ("a Fields report (Results > Create Fields Report)" if "fields" in i.uses else
                 "a solution data report (category Equations)")
        self.kind_note.setText(
            "A value at every point: plot it with Plot Fields (Quantity)." if i.field else
            ("A vector, one per frequency" + (" (an integral is a value, not a field plot)" if integral else "")
             + f": show its magnitude or a component (mag, x, y, z) in {where}."
             + (" For the field over the model, plot the integrand without the integral." if integral else ""))
            if i.vec else
            "One number per frequency" + (" (an integral is a value, not a field plot)" if integral else "")
            + f": show it in {where}.")
        self.unit.setText(L.unit_label(i) or "1")
        det = ["complex" if i.cx else "real"] + list(L.conventions_text(i))
        if i.normals:
            det.append("direction: " + ", ".join(dict.fromkeys(g.label() for g in i.normals)))
        if i.deps:
            det.append("uses " + ", ".join(dict.fromkeys(i.deps)))
        if i.surf and i.field:
            det.append("on faces only")
        self.details.setText("<br>".join(det))

    def _signature(self) -> None:
        self.sig.setText(self._signature_html())
        self.sig.setVisible(bool(self.sig.text()))

    def _signature_html(self) -> str:
        text, pos = self.edit.toPlainText(), self.edit.textCursor().position()
        c = call_at(text, pos)
        env = CB.make_env(self.doc)
        if not c:
            return ""
        fn, k = c
        if fn in L.SIGNATURES:
            params, doc = L.SIGNATURES[fn]
        elif fn in env.functions:
            params, doc = env.functions[fn].params, env.functions[fn].body
        else:
            return ""
        ps = [f"<b>{p}</b>" if j == k else p for j, p in enumerate(params)]
        html = f"{fn}({', '.join(ps)}) — {doc}"
        if k == 0 and fn in L.GEOM_FN:
            dims = L.GEOM_FN[fn]
            names = [n for n, g in env.geometry.items() if set(g.dims) & dims and (fn != "circ" or g.closed)]
            if names:
                html += "<br>" + " ".join(f"<a href='{n}'>{n}</a>" for n in names[:24])
        return html

    # ---- geometry from the 3D view ---------------------------------------------------------------
    def pick(self, fn: str | None, dim: int) -> None:
        """Switch to the 3D Modeler and ask for the geometry there (PickBar); the Equations tab comes back
        afterwards."""
        self._pick_sel = self.edit.textCursor().selectedText() if self.mode == "lin" else ""
        self.win.tabs.setCurrentIndex(0)
        bar = PickBar(self, fn, dim)
        bar.show()
        self._bar = bar

    def insert_geometry(self, fn: str | None, geo: str) -> None:
        """fn(geo, |) at the cursor; a selected text, or a whole field expression, becomes the integrand. In the
        formula editor the operation is there already (geometry_op, or typed): geo goes below its sign."""
        if self.mode == "pro" and self.fe is not None:
            g = TX.geometry_tex(geo)
            if fn is None:
                self.fe.insert(g)
            else:
                self.fe.fill_geometry(g)
            return
        if fn is None:
            self.edit.insert(geo)
            self._analyze()
            return
        sel = getattr(self, "_pick_sel", "") or ""
        _, text, off = TX.split_named(self.edit.toPlainText())
        text = text.strip()
        self._analyze()
        if not sel and text and self.info is not None and self.info.field:
            c = self.edit.textCursor()
            c.setPosition(off)
            c.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            self.edit.setTextCursor(c)
            sel = text
        if sel:
            inner = sel
            if fn in ("max", "min", "avg") and self.info is not None and (self.info.vec or self.info.cx) \
                    and not (sel.startswith("|") and sel.endswith("|")):
                inner = f"|{sel}|"
            self.edit.insert(f"{fn}({geo}, {inner})")
        else:
            self.edit.insert(f"{fn}({geo}, )")
            c = self.edit.textCursor()
            c.movePosition(QTextCursor.Left)
            self.edit.setTextCursor(c)
        self._analyze()

    def closing(self) -> None:
        """The tab is closed."""
        self._timer.stop()

    # ---- lifecycle -------------------------------------------------------------------------------
    def _check_name(self, name: str) -> str | None:
        env = CB.make_env(self.doc)
        if not _IDENT.match(name):
            return "A name starts with a letter and has letters, digits and _ only"
        if name in L.SYMBOLS or name in L.SIGNATURES or name in L.ALIASES:
            return f"'{name}' is a built-in name"
        if name in env.functions:
            return f"'{name}' is a function"
        if name in env.geometry:
            return f"'{name}' is an object of the model"
        if name in env.cs_names:
            return f"'{name}' is a coordinate system"
        if name in F.QUANTITIES:
            return f"'{name}' is a built-in field plot quantity"
        if name != self.current and any(e.name == name for e in self.doc.design.field_exprs):
            return f"An expression named '{name}' exists already"
        return None

    def save(self) -> bool:
        name, text, _ = TX.split_named(self.edit.toPlainText())
        text = text.strip()
        if not text:
            return False
        if not name:
            self.focus_editor()
            self.status.setText(f"<span style='color:{ERR}'>Start the equation with its name to save it, e.g. "
                                "P_in = flux(Port1, re(S))</span>")
            return False
        msg = self._check_name(name)
        if msg:
            QMessageBox.warning(self, "Fields Calculator", msg)
            return False
        env = CB.make_env(self.doc)
        env.named = {k: v for k, v in env.named.items() if k != (self.current or name)}
        try:
            L.analyze(text, env)
        except L.CalcError as e:
            if "Cycle" in str(e) or (self.current and self.current in L.names_in(text)):
                QMessageBox.warning(self, "Fields Calculator", f"Cannot save: {e}")
                return False
            if QMessageBox.question(self, "Fields Calculator", f"The expression has an error:\n{e}\n\nSave anyway?") \
                    != QMessageBox.Yes:
                return False
        des = self.doc.design
        self.doc.checkpoint()
        if self.current and name != self.current:
            deps = CB.dependents(des, self.current)
            if deps and QMessageBox.question(
                    self, "Rename", f"Rename '{self.current}' to '{name}'?\nThe uses in {', '.join(deps)} and in "
                    "reports are renamed too.") != QMessageBox.Yes:
                return False
            CB.rename_everywhere(des, self.current, name)
            for fp in des.field_plots:
                if fp.quantity == self.current:
                    fp.quantity = name
            self.current = name                  # the expression itself has the new name now
        e = next((x for x in des.field_exprs if x.name == (self.current or name)), None)
        if e is None:
            from ..model.design import FieldExpression
            e = FieldExpression(name, text, self.desc.text().strip())
            des.field_exprs.append(e)
        e.name, e.expr, e.description = name, text, self.desc.text().strip()
        self.current = name
        self.doc.dirty = True
        self.doc.changed.emit("fields")
        self.saved_text = self._state()
        self.refresh()
        self._dirty_changed()
        self._used_by()
        self.status.setText(f"Saved {name}")
        self.win.msgs.add("info", f"Fields Calculator: saved {name} = {text}")
        self.win.redraw_equation_plots([name])
        if self.for_plot and self.info is not None and self.info.field:
            self.for_plot = False
            self.banner.setVisible(False)
            self.win.plot_fields(name)
        return True

    def delete(self) -> None:
        if not self.current:
            return
        des = self.doc.design
        deps = CB.dependents(des, self.current)
        plots = [p.name for p in des.field_plots if p.quantity == self.current]
        reps = [r.name for r in des.reports if any(self.current in CB.field_names_in(des, t) for t in r.traces)]
        more = ""
        if deps or plots or reps:
            more = "\n\nIt is used by: " + ", ".join(deps + [f"plot {p}" for p in plots] + [f"report {r}" for r in reps]) + \
                   "\nThey will show an error until you change them."
        if QMessageBox.question(self, "Delete", f"Delete '{self.current}'?{more}") != QMessageBox.Yes:
            return
        self.doc.checkpoint()
        des.field_exprs = [e for e in des.field_exprs if e.name != self.current]
        self.doc.dirty = True
        self.doc.changed.emit("fields")
        self.new()
        self.refresh()

    def open_for_plot(self) -> None:
        self.for_plot = True
        self.banner.setText("<b>New quantity for a field plot.</b> Build the expression, give it a name and "
                            "Save: the field plot dialog opens with it.")
        self.banner.setVisible(True)
        self.new()
        self.focus_editor()

    # ---- dialogs ---------------------------------------------------------------------------------
    def component_dialog(self) -> None:
        dlg = ComponentDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.insert_text(dlg.text())

    def functions_dialog(self, select: str | None = None) -> None:
        before = list(self.doc.design.field_funcs)
        dlg = FunctionsDialog(self, select)
        dlg.exec()
        self.refresh()
        changed = set(before) ^ set(self.doc.design.field_funcs)
        if changed:
            self.win.redraw_equation_plots([f.split("(", 1)[0].strip() for f in changed])


class ComponentDialog(QDialog):
    """A component of a vector field in a coordinate system of the model."""

    COMPS = {"Cartesian": [("x", "x"), ("y", "y"), ("z", "z")],
             "Cylindrical": [("ρ (rho)", "rho"), ("φ (phi)", "phi"), ("z", "z")],
             "Spherical": [("r", "r"), ("θ (theta)", "theta"), ("φ (phi)", "phi")]}

    def __init__(self, panel: EquationsTab):
        super().__init__(panel)
        self.panel = panel
        self.setWindowTitle("Insert Component")
        f = QFormLayout(self)
        self.vec = QComboBox()
        self.vec.setEditable(True)
        items = ["E", "H", "D", "B", "J", "S", "Js"]
        cur = panel.formula_text().strip()
        if cur and panel.info is not None and panel.info.vec and cur not in items:
            items.insert(0, cur)
        self.vec.addItems(items)
        f.addRow("Vector", self.vec)
        self.cs = QComboBox()
        self.cs.addItems(panel.doc.design.cs_names())
        f.addRow("Coordinate system", self.cs)
        self.sys = QComboBox()
        self.sys.addItems(list(self.COMPS))
        f.addRow("System", self.sys)
        self.comp = QComboBox()
        f.addRow("Component", self.comp)
        self.rep = QComboBox()
        self.rep.addItems(["Complex (peak phasor)", "Real part", "Imaginary part", "Magnitude", "Phase",
                           "Instantaneous (context phase)"])
        f.addRow("As", self.rep)
        self.preview = QLabel()
        self.preview.setStyleSheet(f"color:{MUTED}")
        f.addRow("", self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Insert")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.sys.currentTextChanged.connect(self._comps)
        for w in (self.vec, self.cs, self.comp, self.rep):
            w.currentTextChanged.connect(lambda *_: self.preview.setText(self.text()))
        self._comps(self.sys.currentText())

    def _comps(self, s: str) -> None:
        self.comp.clear()
        for label, key in self.COMPS[s]:
            self.comp.addItem(label, key)
        self.preview.setText(self.text())

    def text(self) -> str:
        v = self.vec.currentText().strip() or "E"
        if not re.match(r"^[A-Za-z_]\w*$", v):
            v = f"({v})"
        cs = self.cs.currentText()
        c = f"{self.comp.currentData() or 'x'}({v}{'' if cs == 'Global' else ', ' + cs})"
        return [c, f"re({c})", f"im({c})", f"|{c}|", f"phase({c})", f"inst({c})"][self.rep.currentIndex()]


class LibraryDialog(QDialog):
    """Equations > Open Library: ready-made equations and functions, ticked to import them into the design."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Equation Library")
        self.resize(820, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Tick the equations and functions to import into the design. They can be changed "
                             "there afterwards."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Formula", "Description"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        tri = Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsAutoTristate
        self.groups = {}
        for group, rows in (("Equations", [(n, t, d) for n, t, d in LIB.EXPRESSIONS]),
                            ("Functions", [(f.split("=", 1)[0].strip(), f.split("=", 1)[1].strip(), d)
                                           for f, d in LIB.FUNCTIONS])):
            top = QTreeWidgetItem(self.tree, [group])
            top.setFlags(tri)
            top.setCheckState(0, Qt.Unchecked)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            for name, text, desc in rows:
                it = QTreeWidgetItem(top, [name, text, desc])
                it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setCheckState(0, Qt.Unchecked)
                it.setToolTip(1, text)
                it.setToolTip(2, desc)
            top.setExpanded(True)
            self.groups[group] = top
        for c in range(3):
            self.tree.resizeColumnToContents(c)
        self.tree.itemChanged.connect(lambda *_: self._count())
        # a double click ticks or unticks the row
        self.tree.itemDoubleClicked.connect(
            lambda it, _c: it.setCheckState(0, Qt.Unchecked if it.checkState(0) == Qt.Checked else Qt.Checked))
        lay.addWidget(self.tree, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = bb.addButton("Import", QDialogButtonBox.AcceptRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._count()

    def _ticked(self, group: str) -> list[QTreeWidgetItem]:
        top = self.groups[group]
        return [top.child(i) for i in range(top.childCount()) if top.child(i).checkState(0) == Qt.Checked]

    def _count(self) -> None:
        n = len(self._ticked("Equations")) + len(self._ticked("Functions"))
        self.ok.setEnabled(n > 0)
        self.ok.setText(f"Import ({n})" if n else "Import")

    def tick(self, names: list[str]) -> None:
        for top in self.groups.values():
            for i in range(top.childCount()):
                it = top.child(i)
                if it.text(0) in names or it.text(0).split("(")[0] in names:
                    it.setCheckState(0, Qt.Checked)

    def chosen(self) -> tuple[list[tuple[str, str, str]], list[str]]:
        """(equations as (name, expression, description), functions as "name(params) = body")."""
        eqs = [(it.text(0), it.text(1), it.text(2)) for it in self._ticked("Equations")]
        funcs = [f"{it.text(0)} = {it.text(1)}" for it in self._ticked("Functions")]
        return eqs, funcs


class FunctionsDialog(QDialog):
    """Your own functions name(a, b) = expression: expanded where they are used; the parameters may also
    be geometries."""

    def __init__(self, panel: EquationsTab, select: str | None = None):
        super().__init__(panel)
        self.panel = panel
        self.doc = panel.doc
        self.setWindowTitle("Functions")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Functions of this design. Parameters can be fields, values or geometries, e.g.\n"
                             "voltage(L) = int_line(L, E)      ratio(a, b) = a/b"))
        self.list = QListWidget()
        self.list.currentTextChanged.connect(lambda t: self.edit.setText(t))
        lay.addWidget(self.list, 1)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("name(a, b) = expression")
        self.edit.textChanged.connect(self._check)
        lay.addWidget(self.edit)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        row = QHBoxLayout()
        add = QPushButton("Add / Update")
        add.clicked.connect(self._add)
        rem = QPushButton("Delete")
        rem.clicked.connect(self._delete)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Library:"))
        self.lib = QListWidget()
        for text, desc in LIB.FUNCTIONS:
            self.lib.addItem(text)
            self.lib.item(self.lib.count() - 1).setToolTip(desc)
        self.lib.itemDoubleClicked.connect(lambda it: self.edit.setText(it.text()))
        self.lib.setMaximumHeight(120)
        lay.addWidget(self.lib)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()
        if select:
            hits = [i for i in range(self.list.count()) if self.list.item(i).text().split("(")[0].strip() == select
                    or self.list.item(i).text() == select]
            if hits:
                self.list.setCurrentRow(hits[0])
            else:
                self.edit.setText(select)

    def _fill(self) -> None:
        self.list.clear()
        self.list.addItems(self.doc.design.field_funcs)

    def _check(self, text: str) -> bool:
        try:
            f = L.parse_function(text)
        except L.CalcError as e:
            self.msg.setText(f"<span style='color:{ERR}'>{e}</span>" if text.strip() else "")
            return False
        env = CB.make_env(self.doc)
        if f.name in env.named or f.name in env.geometry or f.name in env.cs_names:
            self.msg.setText(f"<span style='color:{ERR}'>'{f.name}' is used by an expression, object or "
                             "coordinate system</span>")
            return False
        self.msg.setText(f"<span style='color:{MUTED}'>{f.name}({', '.join(f.params)})</span>")
        return True

    def _add(self) -> None:
        text = self.edit.text().strip()
        if not self._check(text):
            return
        f = L.parse_function(text)
        des = self.doc.design
        self.doc.checkpoint()
        des.field_funcs = [x for x in des.field_funcs if x.split("(", 1)[0].strip() != f.name] + [f.text]
        self.doc.dirty = True
        self.doc.changed.emit("fields")
        self._fill()

    def _delete(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        name = it.text().split("(", 1)[0].strip()
        deps = CB.dependents(self.doc.design, name)
        more = f"\n\nIt is used by: {', '.join(deps)}" if deps else ""
        if QMessageBox.question(self, "Delete", f"Delete the function {name}()?{more}") != QMessageBox.Yes:
            return
        self.doc.checkpoint()
        self.doc.design.field_funcs = [x for x in self.doc.design.field_funcs if x != it.text()]
        self.doc.dirty = True
        self.doc.changed.emit("fields")
        self._fill()
