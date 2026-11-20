"""The formula editor's LaTeX and the text of the Fields Calculator language, both ways.

The calculator stores and evaluates the text (``flux(Port1, re(S))``); the formula editor shows and edits it
as typeset LaTeX (``\\iint_{\\mathrm{Port1}}\\operatorname{Re}\\left\\{S\\right\\}\\cdot\\mathbf{n}\\,\\mathrm{d}A``).

    to_latex(src, env)  text -> LaTeX the editor can load (and to_text reads back to the same text)
    to_text(tex, env)   LaTeX as the editor gives it -> text; LatexError says what is missing

Operations over a geometry are written with the geometry below the sign, as in a textbook: the integrals
(``\\int``, ``\\oint``, ``\\iint``, ``\\oiint``, ``\\iiint``; ``\\cdot\\mathbf{n}\\,dA`` makes a flux, ``\\cdot d\\mathbf{l}``
a tangential line integral), ``\\max``, ``\\min``, ``\\operatorname{avg}`` and the value at a point
``\\left. f \\right|_{P}``. In the subscript ``-Port2`` reverses the normal and ``Port1,Port2`` is a list."""
from __future__ import annotations

import re
from dataclasses import replace

from . import lang as L
from . import units as UN


class LatexError(ValueError):
    """The formula cannot be read as an expression yet (an empty box, a missing geometry, ...)."""


SUBMAP = {"eps_0": "eps0", "mu_0": "mu0", "k_0": "k0", "c_0": "c0", "Z_0": "Z0", "J_s": "Js"}
GREEK = {"omega": "omega", "pi": "pi", "sigma": "sigma", "mu": "mu", "varepsilon": "eps", "epsilon": "eps",
         "varphi": "phi", "phi": "phi", "theta": "theta", "vartheta": "theta", "rho": "rho", "delta": "delta"}
TEXTCMD = {"operatorname", "mathrm", "mathit", "text", "textrm", "mathbf", "boldsymbol", "vec", "mathsf",
           "mathnormal"}
SPACE = {",", ";", ":", "!", " ", "quad", "qquad", "limits", "nolimits", "displaystyle"}
INTEGRALS = {"int": 1, "oint": 1, "iint": 2, "oiint": 2, "iiint": 3, "oiiint": 3}
GEOMETRY_FNS = ("int_line", "circ", "int_surf", "flux", "int_vol", "max", "min", "avg", "at")
# field symbols are plain letters in the editor (with \mathbf one would keep typing in bold)
EDIT_SYM = {"E": "E", "H": "H", "D": "D", "B": "B", "J": "J", "Js": "J_s", "S": "S", "n": "n"}
_LETTER_CMDS = {"mathbf", "mathrm", "boldsymbol", "vec", "hat", "mathit", "text", "operatorname", "mathnormal"}


class _Tok:
    __slots__ = ("k", "v")

    def __init__(self, k: str, v: str):
        self.k, self.v = k, v


def _tokens(tex: str) -> list[_Tok]:
    out, i = [], 0
    while i < len(tex):
        c = tex[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", tex[i:], re.S)
            if not m:
                break
            out.append(_Tok("cmd", m.group(1)))
            i += len(m.group(0))
        elif c.isspace():
            i += 1
        else:
            out.append(_Tok("ch", c))
            i += 1
    return out


def _known(env: L.Env | None):
    """(is a name, is a function) for splitting juxtaposed letters (EH = E*H) and for calls."""
    env = env or L.Env()

    def name(w: str) -> bool:
        return (w in L.SYMBOLS or w in L.SIGNATURES or w in env.functions or w in env.cs_names
                or env.lookup_named(w) is not None or env.lookup_geometry(w) is not None
                or env.lookup_variable(w) is not None)

    def fn(w: str) -> bool:
        return w in L.SIGNATURES or w in env.functions
    return name, fn


def to_text(tex: str, env: L.Env | None = None) -> str:
    """The expression text of the editor's LaTeX ("" for an empty formula); LatexError if it is incomplete."""
    T = _tokens(tex)
    known, is_fn = _known(env)
    p = 0

    def fail(msg):
        raise LatexError(msg)

    def tok(q):
        return T[q] if 0 <= q < len(T) else None

    def is_ch(v, q=None):
        t = tok(p if q is None else q)
        return t is not None and t.k == "ch" and t.v == v

    def is_cmd(v, q=None):
        t = tok(p if q is None else q)
        return t is not None and t.k == "cmd" and t.v == v

    def skip_space(q):
        while tok(q) is not None and T[q].k == "cmd" and T[q].v in SPACE:
            q += 1
        return q

    def letter_at(q):
        """One letter at q: x, {x}, \\mathbf{x}, \\hat{\\mathbf{n}}, \\differentialD -> (letter, end)."""
        t = tok(q)
        if t is None:
            return None
        if t.k == "ch" and t.v.isascii() and t.v.isalpha():
            return t.v, q + 1
        if t.k == "cmd" and t.v == "differentialD":
            return "d", q + 1
        if (t.k == "cmd" and t.v in _LETTER_CMDS and is_ch("{", q + 1)) or (t.k == "ch" and t.v == "{"):
            r = letter_at(q + (2 if t.k == "cmd" else 1))
            if r and is_ch("}", r[1]):
                return r[0], r[1] + 1
        return None

    def dmeasure(q):
        d = letter_at(q)
        if not d or d[0] != "d":
            return None
        v = letter_at(skip_space(d[1]))
        if not v or v[0] not in "AVlSs":
            return None
        return v[1]

    def measure_at(q0):
        """The end of an integral: dA, dl, \\cdot n dA (flux), \\cdot dl -> (end, normal) or None."""
        q = skip_space(q0)
        if is_cmd("cdot", q):
            q1 = skip_space(q + 1)
            n = letter_at(q1)
            if n and n[0] == "n":
                e = dmeasure(skip_space(n[1]))
                if e:
                    return e, True
            e = dmeasure(q1)
            return (e, False) if e else None
        e = dmeasure(q)
        return (e, False) if e else None

    def raw_text():
        nonlocal p
        if not is_ch("{"):
            t = tok(p)
            p += 1
            if t is None:
                fail("The formula ends too early")
            return ("_" if t.v == "_" else "") if t.k == "cmd" else t.v
        p += 1
        s, d = "", 1
        while p < len(T):
            t = T[p]
            p += 1
            if t.k == "ch" and t.v == "{":
                d += 1
            elif t.k == "ch" and t.v == "}":
                d -= 1
                if d == 0:
                    break
            elif t.k == "cmd":
                if t.v in ("_", "{", "}"):
                    s += t.v
                elif t.v == "placeholder":
                    s += "▢"
            else:
                s += t.v
        return s

    def geo_sub(what):
        """The geometry below a sign: Port1, -Port2 (reversed), Port1,Port2 -> text."""
        nonlocal p
        p = skip_space(p)
        if not is_ch("_"):
            fail(f"Choose the {what} below the sign (Pick from 3D)")
        p += 1
        raw = re.sub(r"\s+", "", raw_text()).replace("−", "-")
        if not raw or "▢" in raw:
            fail(f"Choose the {what} in the box below the sign (Pick from 3D)")
        items, depth, cur = [], 0, ""
        for c in raw:                  # commas inside face(Box, 3) do not separate
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            if c == "," and depth == 0:
                items.append(cur)
                cur = ""
            else:
                cur += c
        items.append(cur)
        items = [f"flip({g[1:]})" if g.startswith("-") else g for g in items if g]
        return items[0] if len(items) == 1 else "[" + ", ".join(items) + "]"

    def group_lr():                    # \left( ... \right)
        nonlocal p
        p += 2
        q, d = p, 1
        while q < len(T):
            if is_cmd("left", q):
                d += 1
            if is_cmd("right", q):
                d -= 1
                if d == 0:
                    break
            q += 1
        atoms = []
        while p < q:
            atom(atoms)
        p = q + 2
        return join(atoms)

    def op_geo(fn):                    # max_G(f), avg_G(f)
        nonlocal p
        geo = geo_sub("point" if fn == "at" else "geometry")
        p = skip_space(p)
        body = group_lr() if is_cmd("left") else arg()
        if not body.strip():
            fail(f"Fill in the quantity after {fn}")
        return f"{fn}({geo}, {body})"

    def arg():
        nonlocal p
        if is_ch("{"):
            p += 1
            s = seq("}")
            p += 1
            return s
        t = tok(p)
        if t is None:
            fail("The formula ends too early")
        if t.k == "ch" and t.v.isascii() and t.v.isalnum():
            p += 1
            return t.v
        tmp = []
        atom(tmp)
        return join(tmp)

    def seq(close=None):
        atoms = []
        while p < len(T) and not (close and is_ch(close)):
            atom(atoms)
        return join(atoms)

    def push_word(atoms, w):
        nonlocal p
        w = {"Re": "re", "Im": "im"}.get(w, w)
        prev = atoms[-1] if atoms else None
        if prev and prev.get("join"):
            prev["s"] += w
            prev["join"] = False
            return
        if prev and prev.get("num") and not prev.get("unit") and "e" not in prev["s"]:
            # 5.8e7, 5.8e-7: the exponent of the number, maybe with its unit right after (5.8e7S)
            m = re.fullmatch(r"e(\d+)(\w*)", w)
            if m:
                prev["s"] += "e" + m.group(1)
                if not m.group(2):
                    return
                w = m.group(2)
            elif w == "e" and (is_ch("-") or is_ch("+")) and tok(p + 1) is not None \
                    and T[p + 1].k == "ch" and T[p + 1].v.isdigit():
                prev["s"] += "e-" if T[p].v == "-" else "e"
                p += 1
                while tok(p) is not None and T[p].k == "ch" and T[p].v.isdigit():
                    prev["s"] += T[p].v
                    p += 1
                return
        if prev and prev.get("num") and not prev.get("unit") and w in UN.NUMBER_UNITS:
            prev["s"] += w
            prev["unit"] = w
            return
        # 2S/m, 3 V/m: the unit of a number goes on after the slash
        num = atoms[-2] if len(atoms) > 1 else None
        if prev and prev["t"] == "op" and prev["s"].strip() == "/" and num and num.get("num") \
                and isinstance(num.get("unit"), str) and num["unit"] + "/" + w in UN.NUMBER_UNITS:
            atoms.pop()
            num["s"] += "/" + w
            num["unit"] += "/" + w
            return
        atoms.append({"t": "v", "s": w, "word": True})

    def ten_power(atoms):
        """5.8\\cdot 10^{7} after a number: the number 5.8e7, so that a unit can follow it."""
        nonlocal p
        prev = atoms[-1] if atoms else None
        if not prev or not prev.get("num") or prev.get("unit") or "e" in prev["s"]:
            return False
        q = skip_space(p)
        if not (is_ch("1", q) and is_ch("0", q + 1) and is_ch("^", q + 2)):
            return False
        q += 3
        braced = is_ch("{", q)
        q += braced
        sign = ""
        if is_ch("-", q) or is_ch("+", q):
            sign = T[q].v
            q += 1
        digits = ""
        while tok(q) is not None and T[q].k == "ch" and T[q].v.isdigit() and (braced or not digits):
            digits += T[q].v
            q += 1
        if not digits or (braced and not is_ch("}", q)):
            return False
        p = q + braced
        prev["s"] += "e" + ("-" if sign == "-" else "") + digits
        return True

    def sub_follows():
        return is_ch("_", skip_space(p))

    def degree(atoms):
        prev = atoms[-1] if atoms else None
        if prev and prev.get("num"):
            prev["s"] += "deg"
            return
        fail("° only after a number")

    def atom(atoms):
        nonlocal p
        t = T[p]
        if t.k == "ch":
            c = t.v
            if c.isdigit() or c == ".":
                s = ""
                while p < len(T) and T[p].k == "ch" and (T[p].v.isdigit() or T[p].v == "."):
                    s += T[p].v
                    p += 1
                atoms.append({"t": "v", "s": s, "num": True})
                return
            if c.isascii() and c.isalpha():
                s = ""
                while p < len(T) and ((T[p].k == "ch" and T[p].v.isascii() and T[p].v.isalnum())
                                      or (T[p].k == "cmd" and T[p].v == "_")):
                    s += "_" if T[p].k == "cmd" else T[p].v
                    p += 1
                push_word(atoms, s)
                return
            p += 1
            if c == "^":
                if is_cmd("circ") or is_cmd("degree"):
                    p += 1
                    degree(atoms)
                    return
                if is_ch("{") and (is_cmd("circ", p + 1) or is_cmd("degree", p + 1)) and is_ch("}", p + 2):
                    p += 3
                    degree(atoms)
                    return
                atoms.append({"t": "sup", "s": "^(" + arg() + ")"})
                return
            if c == "_":
                e = raw_text()
                prev = atoms[-1] if atoms else None
                if not prev or not prev.get("word") or not re.fullmatch(r"[A-Za-z0-9_]+", e):
                    fail("Subscripts are only used in names, e.g. ε_r or P_in")
                n = prev["s"] + "_" + e
                prev["s"] = SUBMAP.get(n, n)
                return
            if c in "([":
                atoms.append({"t": "open", "s": c})
                return
            if c in ")]":
                atoms.append({"t": "close", "s": c})
                return
            if c == "|":
                atoms.append({"t": "bar"})
                return
            if c in "+-/":
                atoms.append({"t": "op", "s": f" {c} "})
                return
            if c == ",":
                atoms.append({"t": "op", "s": ", "})
                return
            if c == "*":
                atoms.append({"t": "op", "s": "*"})
                return
            if c == "{":
                s = seq("}")
                p += 1
                atoms.append({"t": "v", "s": "(" + s + ")"})
                return
            if c in "~&":
                return
            if c in "·×":
                atoms.append({"t": "op", "s": f" {c} "})
                return
            fail(f"“{c}” is not supported in the formula")
        p += 1
        v = t.v
        if v in TEXTCMD:
            w = raw_text()
            if not w:
                return
            if "▢" in w:
                fail("Fill in the empty box")
            if w in ("avg", "max", "min") and sub_follows():
                atoms.append({"t": "v", "s": op_geo(w)})
            elif re.fullmatch(r"[0-9.]+", w):
                atoms.append({"t": "v", "s": w, "num": True})
            else:
                push_word(atoms, w)
            return
        if v in GREEK:
            push_word(atoms, GREEK[v])
            return
        if v in ("frac", "dfrac", "tfrac"):
            a, b = arg(), arg()
            prev = atoms[-1] if atoms else None
            if prev and prev.get("num") and not prev.get("unit") and f"{a}/{b}" in UN.NUMBER_UNITS:
                prev["s"] += f"{a}/{b}"          # 2\\frac{S}{m}: a unit after the number
                prev["unit"] = f"{a}/{b}"
                return

            def w(s):
                return s if re.fullmatch(r"[\w.]+", s) else f"({s})"
            atoms.append({"t": "v", "s": w(a) + "/" + w(b)})
            return
        if v == "sqrt":
            if is_ch("["):
                fail("Only square roots are supported")
            if is_cmd("left") and tok(p + 1) is not None and tok(p + 1).v == "(":
                # \sqrt\left(x\right): the parentheses are the argument
                q, depth = p + 2, 1
                while q < len(T):
                    if is_cmd("left", q):
                        depth += 1
                    elif is_cmd("right", q):
                        depth -= 1
                        if depth == 0:
                            break
                    q += 1
                p += 2
                inner = []
                while p < q:
                    atom(inner)
                p = q + 2
                atoms.append({"t": "v", "s": "sqrt(" + join(inner) + ")"})
                return
            atoms.append({"t": "v", "s": "sqrt(" + arg() + ")"})
            return
        if v in ("left", "right", "bigl", "bigr", "Bigl", "Bigr", "big", "Big"):
            d = tok(p)
            p += 1
            if d is None:
                fail("The formula ends too early")
            dv = d.v
            if dv in ("|", "vert", "lvert", "rvert", "mid"):
                atoms.append({"t": "bar"})
                return
            if dv == "." and v == "left":
                q, depth = p, 1
                while q < len(T):
                    if is_cmd("left", q):
                        depth += 1
                    if is_cmd("right", q):
                        depth -= 1
                        if depth == 0:
                            break
                    q += 1
                de = tok(q + 1)
                if q < len(T) and de is not None and de.v in ("|", "vert", "rvert") and is_ch("_", q + 2):
                    inner = []
                    while p < q:
                        atom(inner)
                    p = q + 2
                    atoms.append({"t": "v", "s": "at(" + geo_sub("point") + ", " + join(inner) + ")"})
                return
            if dv == ".":
                return
            opening = v == "left" or v.endswith("l")
            if opening:
                atoms.append({"t": "open", "s": "[" if dv == "[" else "("})
            else:
                atoms.append({"t": "close", "s": "]" if dv == "]" else ")"})
            return
        if v in ("vert", "lvert", "rvert", "mid"):
            atoms.append({"t": "bar"})
            return
        if v in ("lbrace", "{"):
            atoms.append({"t": "open", "s": "("})
            return
        if v in ("rbrace", "}"):
            atoms.append({"t": "close", "s": ")"})
            return
        if v == "_":
            prev = atoms[-1] if atoms else None
            if not prev or not prev.get("word"):
                fail("A name cannot start with _")
            prev["join"] = True
            prev["s"] += "_"
            return
        if v in ("cdot", "times") and ten_power(atoms):
            return
        if v == "cdot":
            atoms.append({"t": "op", "s": " · "})
            return
        if v == "times":
            atoms.append({"t": "op", "s": " × "})
            return
        if v == "ast":
            atoms.append({"t": "op", "s": "*"})
            return
        if v == "div":
            atoms.append({"t": "op", "s": " / "})
            return
        if v == "nabla":
            if is_cmd("times"):
                p += 1
                atoms.append({"t": "pre", "s": "curl"})
            elif is_cmd("cdot"):
                p += 1
                atoms.append({"t": "pre", "s": "div"})
            else:
                atoms.append({"t": "pre", "s": "grad"})
            return
        if v == "tan":
            if is_cmd("delta"):
                p += 1
                if is_ch("_"):                        # \tan\delta_m: the magnetic loss tangent
                    q = p
                    p += 1
                    if raw_text() == "m":
                        push_word(atoms, "tandm")
                        return
                    p = q
                push_word(atoms, "tand")
                return
            fail("tan() is not available, only tan δ (tand)")
        if v in ("sin", "cos", "exp", "ln"):
            push_word(atoms, v)
            return
        if v == "log":
            if is_ch("_"):
                p += 1
                if raw_text() != "10":
                    fail("Only log₁₀ is supported")
                push_word(atoms, "log10")
            else:
                push_word(atoms, "ln")
            return
        if v in ("Re", "Im"):
            push_word(atoms, v.lower())
            return
        if v in ("max", "min"):
            if sub_follows():
                atoms.append({"t": "v", "s": op_geo(v)})
            else:
                push_word(atoms, v)
            return
        if v == "angle":
            push_word(atoms, "phase")
            return
        if v in SPACE:
            return
        if v in ("degree", "circ"):
            degree(atoms)
            return
        if v == "backslash":
            fail("Type the name after \\ and press Space, e.g. \\times or \\iint")
        if v == "placeholder":
            raw_text()
            fail("Fill in the empty box")
        if v in INTEGRALS:
            dim = INTEGRALS[v]
            geo = geo_sub(["", "line", "face", "volume"][dim])
            inner, m = [], None
            while p < len(T) and not is_ch("}"):
                m = measure_at(p)
                if m:
                    break
                atom(inner)
            body = join(inner)
            if not body.strip():
                fail("Fill in what to integrate")
            if not m:
                fail("The integral needs d" + "xlAV"[dim] + " at its end")
            p = m[0]
            fn = "int_vol" if dim == 3 else ("flux" if m[1] else "int_surf") if dim == 2 else \
                "circ" if v == "oint" else "int_line"
            atoms.append({"t": "v", "s": f"{fn}({geo}, {body})"})
            return
        fail(f"“\\{v}” is not supported in the formula")

    def join(atoms):
        out, end_val, bars = "", False, 0
        for k, a in enumerate(atoms):
            t = a["t"]
            if t == "bar":
                if end_val and bars > 0:
                    out += "|"
                    bars -= 1
                    end_val = True
                else:
                    if end_val:
                        out += "*"
                    out += "|"
                    bars += 1
                    end_val = False
                continue
            if t == "op":
                out += a["s"]
                end_val = False
                continue
            if t in ("sup", "close"):
                out += a["s"]
                end_val = True
                continue
            prev = atoms[k - 1] if k else None
            call_paren = t == "open" and prev is not None and prev.get("word") and is_fn(prev["s"])
            if end_val and not call_paren:
                out += "*"
            if t == "open":
                out += a["s"]
                end_val = False
                continue
            if t == "pre":
                out += a["s"] + " "
                end_val = False
                continue
            s = a["s"]
            if a.get("word") and not known(s) and re.fullmatch(r"[A-Za-z]{2,}", s) and all(known(ch) for ch in s):
                s = "*".join(s)
            out += s
            end_val = True
        return out

    s = seq()
    s = re.sub(r"\s+", " ", s).replace("( ", "(").replace(" )", ")").strip()
    return s


# ------------------------------------------------------------------------------------------------
# text -> LaTeX
# ------------------------------------------------------------------------------------------------
def _esc(s: str) -> str:
    return s.replace("_", r"\_")


def _geo_tex(node: L.Node, src: str) -> str | None:
    """A geometry argument as the subscript of a sign: Port1, -Port2, Port1,Port2 (None: not simple)."""
    if node.t == "list":
        parts = [_geo_tex(it, src) for it in node.items]
        return None if any(x is None for x in parts) else ",".join(parts)
    if node.t == "call" and node.fn == "flip" and len(node.args) == 1:
        inner = _geo_tex(node.args[0], src)
        return None if inner is None or inner.startswith("-") or node.args[0].t == "list" else "-" + inner
    if node.t == "id":
        return r"\mathrm{" + _esc(node.name) + "}"
    if node.t == "call" and node.fn in ("face", "cut_xy", "cut_yz", "cut_xz", "point"):
        return r"\mathrm{" + _esc(re.sub(r"\s+", "", src[node.pos:node.end])) + "}"
    return None


def geometry_tex(text: str) -> str:
    """The geometry text of the pick bar (Port1, flip(Port2), [A, B], face(Box, 3)) as a subscript."""
    node = L.parse(text)
    t = _geo_tex(node, text)
    return t if t is not None else r"\mathrm{" + _esc(text) + "}"


def _num_tex(v: str, unit: str) -> str:
    m = re.fullmatch(r"([\d.]+)[eE]([-+]?\d+)", v)
    s = f"{m.group(1)}\\cdot 10^{{{int(m.group(2))}}}" if m else v
    return s + (r"\,\mathrm{" + unit + "}" if unit else "")


def to_latex(src: str, env: L.Env | None = None) -> str:
    """LaTeX of the expression text for the formula editor ("" for an empty text)."""
    if not src.strip():
        return ""
    env = env or L.Env()
    return _tex(L.parse(src), src, env)


def _tex(node: L.Node, src: str, env: L.Env) -> str:
    def T(n):
        return _tex(n, src, env)

    def P(n, s):
        return r"\left(" + s + r"\right)" if (n.t == "bin" and n.op in "+-") or n.t == "neg" else s

    def inner(n):
        return T(n.a) if n.t == "par" else T(n)

    t = node.t
    if t == "num":
        return _num_tex(node.v, node.unit)
    if t == "id":
        if node.name in EDIT_SYM:
            return EDIT_SYM[node.name]
        sym = L.SYMBOLS.get(node.name)
        if sym is not None:
            return sym.tex
        return r"\mathrm{" + _esc(node.name) + "}"
    if t == "par":
        return r"\left(" + T(node.a) + r"\right)"
    if t == "neg":
        return "-" + P(node.a, T(node.a))
    if t == "list":
        return r"\left[" + ",".join(T(i) for i in node.items) + r"\right]"
    if t == "bin":
        a, b, op = T(node.a), T(node.b), node.op
        if op == "+":
            return a + "+" + b
        if op == "-":
            return a + "-" + P(node.b, b)
        if op in ("*", "·"):
            return P(node.a, a) + r"\cdot " + P(node.b, b)
        if op == "×":
            return P(node.a, a) + r"\times " + P(node.b, b)
        if op == "/":
            return r"\frac{" + inner(node.a) + "}{" + inner(node.b) + "}"
        if op == "^":
            base = r"\left(" + a + r"\right)" if node.a.t in ("bin", "neg") else a
            return base + "^{" + inner(node.b) + "}"
        return a + op + b
    if t == "call":
        fn, args = node.fn, node.args
        if fn in GEOMETRY_FNS and len(args) == 2 and fn not in env.functions:
            g = _geo_tex(args[0], src)
            if g is not None:
                b = args[1]
                bt = P(b, T(b))
                try:
                    vec = L.analyze(b, replace(env, inner=True)).vec
                except L.CalcError:
                    vec = False
                closed = _closed(args[0], env)
                surf = r"\oiint_{" if closed else r"\iint_{"
                if fn == "int_line":
                    return r"\int_{" + g + "}" + bt + (r"\cdot\mathrm{d}\mathbf{l}" if vec else r"\,\mathrm{d}l")
                if fn == "circ":
                    return r"\oint_{" + g + "}" + bt + r"\cdot\mathrm{d}\mathbf{l}"
                if fn == "int_surf":
                    return surf + g + "}" + bt + r"\,\mathrm{d}A"
                if fn == "flux":
                    return surf + g + "}" + bt + r"\cdot\mathbf{n}\,\mathrm{d}A"
                if fn == "int_vol":
                    return r"\iiint_{" + g + "}" + bt + r"\,\mathrm{d}V"
                if fn in ("max", "min"):
                    return "\\" + fn + "_{" + g + r"}\left(" + T(b) + r"\right)"
                if fn == "avg":
                    return r"\operatorname{avg}_{" + g + r"}\left(" + T(b) + r"\right)"
                if fn == "at":
                    return r"\left." + T(b) + r"\right|_{" + g + "}"
        if fn in ("re", "im") and len(args) == 1:
            return r"\operatorname{" + ("Re" if fn == "re" else "Im") + r"}\left\{" + T(args[0]) + r"\right\}"
        if fn == "mag" and len(args) == 1:
            return r"\left|" + T(args[0]) + r"\right|"
        if fn == "sqrt" and len(args) == 1:
            return r"\sqrt{" + T(args[0]) + "}"
        if fn in ("curl", "div", "grad") and len(args) == 1:
            a = args[0]
            at = T(a) if a.t in ("id", "par") else r"\left(" + T(a) + r"\right)"
            return {"curl": r"\nabla\times ", "div": r"\nabla\cdot ", "grad": r"\nabla "}[fn] + at
        return r"\operatorname{" + _esc(fn) + r"}\left(" + ",".join(T(a) for a in args) + r"\right)"
    return ""


def _closed(node: L.Node, env: L.Env) -> bool:
    """All geometries of the argument are closed surfaces (solids: their boundary)."""
    try:
        items = L.geometry_items(node, env)
    except L.CalcError:
        return False
    for it in items:
        if it.kind != "obj":
            return False
        g = env.lookup_geometry(it.name)
        if g is None or 3 not in g.dims and not g.closed:
            return False
    return bool(items)


# ---- "Name = formula" ----------------------------------------------------------------------------
_NAMED = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*=(?!=)\s*")


def split_named(text: str) -> tuple[str, str, int]:
    """'P_in = flux(Port1, re(S))' -> ('P_in', 'flux(Port1, re(S))', offset of the formula); no name: ''."""
    m = _NAMED.match(text)
    return (m.group(1), text[m.end():], m.end()) if m else ("", text, 0)


def name_tex(name: str) -> str:
    """The LaTeX of the name left of = (P_in -> P_{\\mathrm{in}})."""
    def part(w):
        return w if len(w) == 1 else r"\mathrm{" + w + "}"
    base, _, sub = name.partition("_")
    return part(base) + ("_{" + part(sub) + "}" if sub else "")


def split_named_tex(tex: str) -> tuple[str | None, str]:
    """The editor's LaTeX at its first = outside braces: (name, formula); (None, tex) without =. LatexError if
    the left side is not a name."""
    depth = 0
    for i, c in enumerate(tex):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "=" and depth == 0:
            left = tex[:i]
            w = re.sub(r"\\(mathrm|mathit|operatorname|text|mathbf|mathnormal)\b", "", left)
            w = re.sub(r"[{}\s]|\\[,;:! ]", "", w)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)?", w):
                raise LatexError("Left of = goes the name of the equation: letters, digits and _, e.g. P_in")
            return w, tex[i + 1:]
    return None, tex
