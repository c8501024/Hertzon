"""Expression evaluation with units and design variables.

Values are written like in the reference workflow: ``10mm``, ``w/2 + 0.5mm``,
``2.4GHz``, ``45deg``. A plain number in a length field means model units.
"""
from __future__ import annotations

import ast
import math
import re

LENGTH_UNITS = {
    "fm": 1e-15, "pm": 1e-12, "nm": 1e-9, "um": 1e-6, "mm": 1e-3, "cm": 1e-2,
    "dm": 1e-1, "meter": 1.0, "m": 1.0, "km": 1e3, "mil": 25.4e-6, "in": 25.4e-3,
    "ft": 0.3048,
}
FREQ_UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}
ANGLE_UNITS = {"deg": math.pi / 180.0, "rad": 1.0, "degree": math.pi / 180.0}
OTHER_UNITS = {
    "ohm": 1.0, "Ohm": 1.0, "kOhm": 1e3, "MOhm": 1e6,
    "nH": 1e-9, "pH": 1e-12, "uH": 1e-6, "mH": 1e-3, "H": 1.0,
    "fF": 1e-15, "pF": 1e-12, "nF": 1e-9, "uF": 1e-6, "F": 1.0,
    "S_per_m": 1.0, "siemens/m": 1.0, "W": 1.0, "mW": 1e-3, "s": 1.0, "ns": 1e-9, "ps": 1e-12,
}
ALL_UNITS: dict[str, float] = {**LENGTH_UNITS, **FREQ_UNITS, **ANGLE_UNITS, **OTHER_UNITS}

# Longest unit names first so that "mm" wins over "m".
_UNIT_PATTERN = "|".join(sorted((re.escape(u) for u in ALL_UNITS), key=len, reverse=True))
_NUM_UNIT = re.compile(
    r"(?<![A-Za-z_\d.])((?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(" + _UNIT_PATTERN + r")(?![A-Za-z_\d])"
)

_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "atan2": math.atan2, "sqrt": math.sqrt, "exp": math.exp, "ln": math.log,
    "log": math.log10, "log10": math.log10, "abs": abs, "min": min, "max": max, "pow": pow,
    "floor": math.floor, "ceil": math.ceil, "round": round, "if": lambda c, a, b: a if c else b,
    "even": lambda x: int(x) % 2 == 0, "odd": lambda x: int(x) % 2 == 1, "sinh": math.sinh,
    "cosh": math.cosh, "tanh": math.tanh, "sign": lambda x: (x > 0) - (x < 0),
}
_CONSTS = {"pi": math.pi, "PI": math.pi, "e0": 8.8541878128e-12, "u0": 4e-7 * math.pi, "c0": 299792458.0}

# What the Design Properties dialog lists under "Constants & Functions...": (name, insert text, description).
# Every entry of _CONSTS and _FUNCS is listed here; tests/test_expr_reference.py keeps the two in step.
CONSTANT_INFO: list[tuple[str, str, str]] = [
    ("pi", "pi", "3.14159265...  (also PI)"),
    ("PI", "PI", "3.14159265...  (same as pi)"),
    ("c0", "c0", "Speed of light in vacuum, 299792458 m/s"),
    ("e0", "e0", "Vacuum permittivity, 8.8541878128e-12 F/m"),
    ("u0", "u0", "Vacuum permeability, 4*pi*1e-7 H/m"),
]
FUNCTION_INFO: list[tuple[str, str, str]] = [
    ("sin(x)", "sin()", "Sine, x in radians (45deg is converted)"),
    ("cos(x)", "cos()", "Cosine, x in radians"),
    ("tan(x)", "tan()", "Tangent, x in radians"),
    ("asin(x)", "asin()", "Arc sine, result in radians"),
    ("acos(x)", "acos()", "Arc cosine, result in radians"),
    ("atan(x)", "atan()", "Arc tangent, result in radians"),
    ("atan2(y, x)", "atan2(, )", "Arc tangent of y/x in the right quadrant, radians"),
    ("sinh(x)", "sinh()", "Hyperbolic sine"),
    ("cosh(x)", "cosh()", "Hyperbolic cosine"),
    ("tanh(x)", "tanh()", "Hyperbolic tangent"),
    ("sqrt(x)", "sqrt()", "Square root"),
    ("exp(x)", "exp()", "e to the power x"),
    ("ln(x)", "ln()", "Natural logarithm"),
    ("log(x)", "log()", "Base-10 logarithm (same as log10)"),
    ("log10(x)", "log10()", "Base-10 logarithm"),
    ("pow(x, y)", "pow(, )", "x to the power y (also x^y or x**y)"),
    ("abs(x)", "abs()", "Absolute value"),
    ("sign(x)", "sign()", "-1, 0 or 1"),
    ("min(a, b, ...)", "min(, )", "Smallest argument"),
    ("max(a, b, ...)", "max(, )", "Largest argument"),
    ("floor(x)", "floor()", "Round down to an integer"),
    ("ceil(x)", "ceil()", "Round up to an integer"),
    ("round(x)", "round()", "Round to the nearest integer"),
    ("if(cond, a, b)", "if(, , )", "a when cond is true, otherwise b, e.g. if(w > 2mm, 1mm, 0.5mm)"),
    ("even(n)", "even()", "1 when n is even, otherwise 0"),
    ("odd(n)", "odd()", "1 when n is odd, otherwise 0"),
]
UNIT_GROUPS: list[tuple[str, dict[str, float]]] = [
    ("Length", LENGTH_UNITS), ("Frequency", FREQ_UNITS), ("Angle", ANGLE_UNITS), ("Other", OTHER_UNITS),
]

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.FloorDiv,
    ast.Compare, ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq, ast.NotEq, ast.BoolOp, ast.And, ast.Or,
    ast.IfExp, ast.Not,
)


class ExprError(ValueError):
    pass


def _strip_units(text: str) -> tuple[str, bool]:
    found = False

    def repl(m: re.Match) -> str:
        nonlocal found
        found = True
        return f"({m.group(1)}*{ALL_UNITS[m.group(2)]!r})"

    return _NUM_UNIT.sub(repl, text), found


class Evaluator:
    """Evaluates expressions against variables. Variables are evaluated lazily
    and may reference each other. Results are SI values."""

    def __init__(self, variables: dict[str, str] | None = None, model_unit: str = "mm"):
        self.variables = dict(variables or {})
        self.model_unit = model_unit
        self._cache: dict[str, tuple[float, bool]] = {}
        self._stack: list[str] = []

    @property
    def unit_scale(self) -> float:
        return LENGTH_UNITS[self.model_unit]

    def _var(self, name: str) -> tuple[float, bool]:
        if name in self._cache:
            return self._cache[name]
        if name in self._stack:
            raise ExprError(f"Circular variable reference: {' -> '.join(self._stack + [name])}")
        if name not in self.variables:
            raise ExprError(f"Unknown variable '{name}'")
        self._stack.append(name)
        try:
            res = self._eval(self.variables[name])
        finally:
            self._stack.pop()
        self._cache[name] = res
        return res

    def _eval(self, text: str) -> tuple[float, bool]:
        """Returns (value, carries_unit)."""
        if isinstance(text, (int, float)):
            return float(text), False
        text = str(text).strip()
        if not text:
            raise ExprError("Empty expression")
        code, has_unit = _strip_units(text)
        try:
            tree = ast.parse(code.replace("^", "**"), mode="eval")
        except SyntaxError as e:
            raise ExprError(f"Invalid expression '{text}'") from e
        names: dict[str, float] = {}
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise ExprError(f"Unsupported syntax in '{text}'")
            if isinstance(node, ast.Name) and node.id not in _FUNCS and node.id not in _CONSTS:
                v, u = self._var(node.id)
                names[node.id] = v
                has_unit = has_unit or u
        try:
            val = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, {**_FUNCS, **_CONSTS, **names})
        except ZeroDivisionError as e:
            raise ExprError(f"Division by zero in '{text}'") from e
        except Exception as e:  # noqa: BLE001
            raise ExprError(f"Cannot evaluate '{text}': {e}") from e
        if isinstance(val, bool):
            val = float(val)
        if not isinstance(val, (int, float)):
            raise ExprError(f"Expression '{text}' is not a number")
        return float(val), has_unit

    def value(self, text: str) -> float:
        """Raw SI value (no model-unit scaling)."""
        return self._eval(text)[0]

    def length(self, text: str) -> float:
        """Length in metres. Unitless values are in model units."""
        v, u = self._eval(text)
        return v if u else v * self.unit_scale

    def freq(self, text: str) -> float:
        v, u = self._eval(text)
        return v if u else v * 1e9

    def angle(self, text: str) -> float:
        """Angle in radians. Unitless values are degrees."""
        v, u = self._eval(text)
        return v if u else math.radians(v)

    def point(self, text: str) -> tuple[float, float, float]:
        parts = split_list(text)
        if len(parts) != 3:
            raise ExprError(f"A point needs three coordinates, got '{text}'")
        return tuple(self.length(p) for p in parts)  # type: ignore[return-value]

    def vector(self, text: str) -> tuple[float, float, float]:
        return self.point(text)

    def points(self, text: str) -> list[tuple[float, float, float]]:
        return [self.point(p) for p in str(text).split(";") if p.strip()]

    def integer(self, text: str) -> int:
        return int(round(self.value(text)))

    def check(self, text: str) -> str | None:
        try:
            self._eval(text)
        except ExprError as e:
            return str(e)
        return None


def split_list(text: str) -> list[str]:
    """Split a comma separated list while respecting parentheses."""
    out, depth, cur = [], 0, ""
    for ch in str(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def fmt_length(value_m: float, unit: str = "mm") -> str:
    v = value_m / LENGTH_UNITS[unit]
    return f"{v:.6g}{unit}"


def fmt_freq(value_hz: float) -> str:
    for u in ("THz", "GHz", "MHz", "kHz"):
        if abs(value_hz) >= FREQ_UNITS[u]:
            return f"{value_hz / FREQ_UNITS[u]:.6g}{u}"
    return f"{value_hz:.6g}Hz"
