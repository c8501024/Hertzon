"""Footprints and symbols of circuit parts: where they are found, the built-in packages, the pin-to-pad assignment.

A part that goes on the board carries the properties

- Footprint: a KiCad library id "Library:Name" or a .kicad_mod file (relative to the project or absolute);
- Symbol: optional, "Library:Name" or "file.kicad_sym:Name": names the pins of the footprint's pads;
- Pads: which pads belong to which terminal of the part's model, "b=3; c=1; e1=2; e2=4" (several pads of one
  terminal separated by commas, "NC=5,6" for pads that are left open on purpose). Empty: assigned automatically
  (pad 1 and 2 of a two-terminal part; otherwise by the pin names of the symbol).

A library id is looked up in the KiCad library folders (KICAD*_FOOTPRINT_DIR / KICAD*_SYMBOL_DIR, the usual
installation folders, the folders in HERTZON_KICAD_LIBS and the ones set in the program) as Library.pretty/
Name.kicad_mod, a symbol in Library.kicad_sym or Library.kicad_symdir/Name.kicad_sym. Only where no file is found
a built-in package of the same kind is used (chip R, C, L 0201 to 1206, SOT-23, SOT-323, SOT-343, QFN); it says
so in its source, because its pads are a typical land pattern and not the manufacturer's.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .kicad import Footprint, KicadError, Pad, Symbol, read_footprint, read_symbols

BUILTIN = "built-in"
NC = "NC"

# folders set in the program (Tools > Options), in addition to the environment and the installation folders
user_dirs: list[str] = []


def _install_dirs(kind: str) -> list[Path]:
    """KiCad's own library folders: kind 'footprints' or 'symbols'."""
    out = []
    for k, v in os.environ.items():
        if re.fullmatch(rf"KICAD\d*_{'FOOTPRINT' if kind == 'footprints' else 'SYMBOL'}_DIR", k) and v:
            out.append(Path(v))
    pats = [f"/usr/share/kicad/{kind}", f"/usr/local/share/kicad/{kind}",
            f"/Applications/KiCad/KiCad.app/Contents/SharedSupport/{kind}",
            f"C:/Program Files/KiCad/*/share/kicad/{kind}", os.path.expanduser(f"~/.local/share/kicad/*/{kind}")]
    for p in pats:
        out += [Path(x) for x in sorted(glob.glob(p), reverse=True)]
    return out


def search_dirs(project_dir: str | Path | None = None, kind: str = "footprints") -> list[Path]:
    """Folders searched for a library id, in order: the project's own folder (and its 'footprints' or 'symbols'
    folder), the folders set in the program and in HERTZON_KICAD_LIBS, then KiCad's own."""
    out: list[Path] = []
    if project_dir:
        out += [Path(project_dir) / kind, Path(project_dir)]
    out += [Path(d) for d in user_dirs]
    out += [Path(d) for d in os.environ.get("HERTZON_KICAD_LIBS", "").split(os.pathsep) if d]
    out += _install_dirs(kind)
    seen, res = set(), []
    for d in out:
        k = str(d)
        if k not in seen:
            seen.add(k)
            res.append(d)
    return res


def _split_ref(ref: str) -> tuple[str, str]:
    """'Lib:Name' -> (Lib, Name); a path (with a drive letter or ending in a file suffix) -> ('', ref)."""
    ref = ref.strip()
    if re.match(r"^[A-Za-z]:[\\/]", ref) or ref.endswith((".kicad_mod", ".kicad_sym")) or "/" in ref.split(":")[0]:
        return "", ref
    if ":" in ref:
        lib, name = ref.split(":", 1)
        return lib.strip(), name.strip()
    return "", ref


def find_footprint(ref: str, project_dir: str | Path | None = None) -> Footprint:
    """The footprint a Footprint property names (see the module notes)."""
    ref = (ref or "").strip()
    if not ref:
        raise KicadError("no footprint")
    lib, name = _split_ref(ref)
    if not lib:
        p = Path(name)
        if not p.is_absolute() and project_dir:
            p = Path(project_dir) / p
        if p.suffix == ".kicad_mod":
            if p.is_file():
                return read_footprint(p)
            raise KicadError(f"the footprint file {p} does not exist")
        fp = builtin(name)
        if fp:
            return fp
        raise KicadError(f"'{ref}' is neither a library id (Library:Name) nor a .kicad_mod file")
    if lib != "Hertzon":
        for d in search_dirs(project_dir, "footprints"):
            f = d / f"{lib}.pretty" / f"{name}.kicad_mod"
            if f.is_file():
                return read_footprint(f)
    fp = builtin(name)
    if fp:
        if lib != "Hertzon":
            fp.source = f"{BUILTIN} (KiCad library '{lib}' not found: typical land pattern of the same package)"
        return fp
    raise KicadError(f"the footprint {ref} was not found (KiCad library folders searched: "
                     f"{', '.join(str(d) for d in search_dirs(project_dir)[:4]) or 'none'}; set the folder in "
                     "Tools > Options or give the .kicad_mod file)")


def find_symbol(ref: str, project_dir: str | Path | None = None) -> Symbol:
    """The symbol a Symbol property names: 'Library:Name', 'file.kicad_sym:Name' or a .kicad_sym file with one
    symbol."""
    ref = (ref or "").strip()
    if not ref:
        raise KicadError("no symbol")
    m = re.match(r"^(.*\.kicad_sym)(?::(.+))?$", ref)
    if m:
        p = Path(m.group(1))
        if not p.is_absolute() and project_dir:
            p = Path(project_dir) / p
        syms = _read_with_parents(p)
        if m.group(2):
            if m.group(2) not in syms:
                raise KicadError(f"{p.name} has no symbol '{m.group(2)}'")
            return syms[m.group(2)]
        if len(syms) != 1:
            raise KicadError(f"{p.name} holds {len(syms)} symbols: name one as {p.name}:Name")
        return next(iter(syms.values()))
    lib, name = _split_ref(ref)
    if not lib:
        raise KicadError(f"'{ref}' is neither a library id (Library:Name) nor a .kicad_sym file")
    for d in search_dirs(project_dir, "symbols"):
        f = d / f"{lib}.kicad_symdir" / f"{name}.kicad_sym"
        if f.is_file():
            syms = _read_with_parents(f)
            if name in syms:
                return syms[name]
        f = d / f"{lib}.kicad_sym"
        if f.is_file():
            syms = _read_with_parents(f)
            if name in syms:
                return syms[name]
    raise KicadError(f"the symbol {ref} was not found in the KiCad library folders")


def _read_with_parents(p: Path) -> dict[str, Symbol]:
    """The symbols of a file; a symbol that extends one of another file of the same .kicad_symdir takes its
    pins."""
    syms = read_symbols(p)
    for s in syms.values():
        if not s.pins:
            parent = _parent_name(p, s.name)
            f = p.parent / f"{parent}.kicad_sym" if parent else None
            if f and f.is_file() and f != p:
                ps = read_symbols(f).get(parent)
                if ps:
                    s.pins = list(ps.pins)
    return syms


def _parent_name(p: Path, name: str) -> str:
    from .kicad import head, item, items, parse
    e = parse(p.read_text(encoding="utf-8", errors="replace"))
    for s in items(e, "symbol"):
        if len(s) > 1 and s[1] == name:
            ext = item(s, "extends")
            return str(ext[1]) if ext and len(ext) > 1 else ""
    return ""


# ---- built-in packages ---------------------------------------------------------------------------------------
# chip parts, reflow: gap between the pads, pad length (along the part), pad width, body length, body width (mm);
# middle values of the land patterns R, C and L manufacturers recommend
CHIPS = {"0201": (0.25, 0.25, 0.30, 0.6, 0.3), "0402": (0.40, 0.40, 0.50, 1.0, 0.5),
         "0603": (0.70, 0.70, 0.80, 1.6, 0.8), "0805": (1.00, 0.80, 1.20, 2.0, 1.25),
         "1206": (2.20, 0.90, 1.30, 3.2, 1.6)}
METRIC = {"0201": "0603", "0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216"}
NOTE = "typical land pattern; for production use the manufacturer's footprint"


def _box(fp: Footprint, bw: float, bh: float, margin: float = 0.25):
    xs = [b for p in fp.pads for b in (p.bbox()[0], p.bbox()[2])] + [-bw / 2, bw / 2]
    ys = [b for p in fp.pads for b in (p.bbox()[1], p.bbox()[3])] + [-bh / 2, bh / 2]
    fp.courtyard = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
    fp.fab = [[(-bw / 2, -bh / 2), (bw / 2, -bh / 2), (bw / 2, bh / 2), (-bw / 2, bh / 2), (-bw / 2, -bh / 2)]]


def _smd(number, x, y, w, h) -> Pad:
    return Pad(number=str(number), kind="smd", shape="rect", x=x, y=y, rot=0.0, w=w, h=h,
               layers=["F.Cu", "F.Paste", "F.Mask"])


def chip(code: str, kind: str = "R") -> Footprint:
    gap, ln, w, bl, bw = CHIPS[code]
    xc = gap / 2 + ln / 2
    fp = Footprint(name=f"{kind}_{code}_{METRIC[code]}Metric", source=BUILTIN,
                   description=f"Chip {kind} {code} (inch), {NOTE}")
    fp.pads = [_smd(1, -xc, 0.0, ln, w), _smd(2, xc, 0.0, ln, w)]
    _box(fp, bl, bw)
    return fp


def two_row(name: str, pins: list[tuple[int, float, float]], pad_len: float, pad_w: float, body: tuple[float, float],
            descr: str) -> Footprint:
    """SOT-style package: pads lying along x (the leads point left and right), pins (number, x, y)."""
    fp = Footprint(name=name, source=BUILTIN, description=f"{descr}, {NOTE}")
    fp.pads = [_smd(n, x, y, pad_len, pad_w) for n, x, y in pins]
    _box(fp, *body)
    return fp


def qfn(n: int, body: float, pitch: float, pad_len: float, pad_w: float, ep: float) -> Footprint:
    """Square QFN with n pins (n/4 per side) and an exposed pad (number n + 1): pin 1 at the top of the left side,
    numbered counter-clockwise seen from the top (down the left side, along the bottom, up the right side, along
    the top), as in KiCad. The pads reach 0.2 mm beyond the body."""
    k = n // 4
    c = body / 2 + 0.2 - pad_len / 2
    off = [(i - (k - 1) / 2) * pitch for i in range(k)]
    fp = Footprint(name=f"QFN-{n}-1EP_{body:g}x{body:g}mm_P{pitch:g}mm_EP{ep:g}x{ep:g}mm", source=BUILTIN,
                   description=f"QFN-{n}, {body:g} x {body:g} mm, pitch {pitch:g} mm, exposed pad {ep:g} mm, {NOTE}")
    num = 1
    for side in range(4):
        for i in range(k):
            if side == 0:
                p = _smd(num, -c, -off[i], pad_len, pad_w)
            elif side == 1:
                p = _smd(num, off[i], -c, pad_w, pad_len)
            elif side == 2:
                p = _smd(num, c, off[i], pad_len, pad_w)
            else:
                p = _smd(num, -off[i], c, pad_w, pad_len)
            fp.pads.append(p)
            num += 1
    fp.pads.append(_smd(num, 0.0, 0.0, ep, ep))
    _box(fp, body, body)
    return fp


def builtin(name: str) -> Footprint | None:
    """The built-in package of a footprint name (KiCad's standard names and short ones like 'C_0402')."""
    n = name.split(":")[-1]
    m = re.match(r"^(R|C|L)_(0201|0402|0603|0805|1206)(?:_\d{4}Metric)?$", n)
    if m:
        return chip(m.group(2), m.group(1))
    key = n.upper().replace("_", "-")
    if key.startswith("SOT-23") and key in ("SOT-23", "SOT-23-3"):
        return two_row("SOT-23", [(1, -1.0, 0.95), (2, -1.0, -0.95), (3, 1.0, 0.0)], 1.2, 0.6, (1.3, 2.9),
                       "SOT-23 (TO-236), pin 1 top left")
    if key in ("SOT-323", "SOT-323-SC-70", "SC-70", "SC-70-3"):
        return two_row("SOT-323_SC-70", [(1, -0.95, 0.65), (2, -0.95, -0.65), (3, 0.95, 0.0)], 0.9, 0.45,
                       (1.25, 2.0), "SOT-323 (SC-70), pin 1 top left")
    if key in ("SOT-343", "SOT-343-SC-70-4", "SC-70-4"):
        return two_row("SOT-343_SC-70-4", [(1, -0.95, 0.65), (2, -0.95, -0.65), (3, 0.95, -0.65), (4, 0.95, 0.65)],
                       0.9, 0.45, (1.25, 2.0), "SOT-343 (SC-70-4), pin 1 top left")
    m = re.match(r"^QFN-(\d+)(?:-1EP)?-(\d+(?:\.\d+)?)X\2MM-P(\d+(?:\.\d+)?)MM(?:-EP(\d+(?:\.\d+)?)X\4MM)?$", key)
    if m and int(m.group(1)) % 4 == 0:
        npin, body, pitch = int(m.group(1)), float(m.group(2)), float(m.group(3))
        ep = float(m.group(4)) if m.group(4) else round(body * 0.55, 2)
        return qfn(npin, body, pitch, 0.8 if pitch >= 0.5 else 0.7, round(pitch * 0.5, 3), ep)
    return None


BUILTIN_NAMES = ([f"{k}_{c}" for k in "RCL" for c in CHIPS] + ["SOT-23", "SOT-323_SC-70", "SOT-343_SC-70-4"]
                 + ["QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", "QFN-20-1EP_4x4mm_P0.5mm_EP2.6x2.6mm",
                    "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm", "QFN-16-1EP_4x4mm_P0.65mm_EP2.1x2.1mm"])


def default_footprint(ctype: str, size: str) -> str:
    """The Footprint a chip part gets from a size code such as 0402 (KiCad's library id)."""
    lib = {"R": "Resistor_SMD", "C": "Capacitor_SMD", "L": "Inductor_SMD"}.get(ctype, "Capacitor_SMD")
    kind = ctype if ctype in ("R", "C", "L") else "C"
    return f"{lib}:{kind}_{size}_{METRIC[size]}Metric" if size in METRIC else ""


# ---- terminals and pads --------------------------------------------------------------------------------------
SYNONYMS = [{"d", "drain"}, {"g", "gate"}, {"s", "source"}, {"b", "base"}, {"c", "collector"}, {"e", "emitter"},
            {"a", "anode"}, {"k", "cathode", "c_k"}, {"in", "input", "rfin"}, {"out", "output", "rfout"},
            {"gnd", "ground", "vss", "ep", "pad", "epad"}]


def _norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    for group in SYNONYMS:
        if n in group:
            return sorted(group, key=len)[0]
    return n


def _base(name: str) -> str:
    """A terminal name without a trailing number (e1 -> e) and in its short form."""
    return _norm(re.sub(r"\d+$", "", name) or name)


def parse_pads(text: str) -> dict[str, list[str]]:
    """'b=3; c=1; e1=2,4' -> {'b': ['3'], 'c': ['1'], 'e1': ['2', '4']}."""
    out: dict[str, list[str]] = {}
    for part in re.split(r"[;\n]", text or ""):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k:
            out.setdefault(k, []).extend(x.strip() for x in v.split(",") if x.strip())
    return out


def format_pads(m: dict[str, list[str]]) -> str:
    return "; ".join(f"{k}={','.join(v)}" for k, v in m.items() if v)


def auto_pads(terminals: list[str], fp: Footprint, symbol: Symbol | None = None) -> dict[str, list[str]]:
    """The pads of each terminal where it can be told: a two-terminal part takes pad 1 and 2; otherwise a pad
    whose symbol pin is named like the terminal (D, Drain; B, Base; ...); a terminal named like a pad number takes
    that pad. Several terminals of one name (e1, e2) share its pads in order; pads left over go with the last of
    them. Pads that cannot be told stay unassigned (check() reports them)."""
    nums = fp.numbers()
    out: dict[str, list[str]] = {t: [] for t in terminals}
    if len(terminals) == 2 and not symbol and set(nums) >= {"1", "2"}:
        out[terminals[0]], out[terminals[1]] = ["1"], ["2"]
        return out
    taken: set[str] = set()
    for t in terminals:
        if t in nums:
            out[t] = [t]
            taken.add(t)
    if symbol:
        by_name: dict[str, list[str]] = {}
        for num, nm in symbol.pins:
            if num in nums and num not in taken:
                by_name.setdefault(_norm(nm), []).append(num)
        # pads of the footprint the symbol does not name (exposed pad): with the symbol's ground-like name, if any
        unnamed = [n for n in nums if n not in taken and not symbol.name_of(n)]
        groups: dict[str, list[str]] = {}
        for t in terminals:
            if out[t]:
                continue
            key = _norm(t) if _norm(t) in by_name else _base(t)
            groups.setdefault(key, []).append(t)
        for key, ts in groups.items():
            pads = list(by_name.get(key, []))
            if not pads:
                continue
            if len(ts) == 1:
                out[ts[0]] = pads
            else:
                for i, t in enumerate(ts):
                    out[t] = pads[i:i + 1] if i < len(ts) - 1 else pads[i:]
            taken.update(pads)
        if unnamed and len(terminals) > 2:
            # an exposed pad without a symbol pin: with the terminal whose pins are ground-like (source, emitter)
            for pref in ("s", "e", "gnd"):
                ts = [t for t in terminals if _base(t) == pref or _norm(t) == pref]
                if ts:
                    out[ts[-1]] += unnamed
                    break
    return out


@dataclass
class PadCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check(name: str, terminals: list[str], fp: Footprint, m: dict[str, list[str]]) -> PadCheck:
    """A terminal without a pad is an error, so is a pad that does not exist or belongs to two terminals; a pad
    of the footprint that no terminal has (and that is not NC) is a warning: it is left open in the layout."""
    r = PadCheck()
    nums = fp.numbers()
    owner: dict[str, str] = {}
    for t, pads in m.items():
        if t != NC and t not in terminals:
            r.errors.append(f"{name}: the model has no terminal '{t}' (it has {', '.join(terminals)})")
        for p in pads:
            if p not in nums:
                r.errors.append(f"{name}: the footprint {fp.name} has no pad '{p}'")
            elif p in owner and owner[p] != t:
                r.errors.append(f"{name}: pad {p} is assigned to both {owner[p]} and {t}")
            owner[p] = t
    for t in terminals:
        if not m.get(t):
            r.errors.append(f"{name}: terminal {t} has no pad of {fp.name} (set Pads, e.g. {t}=1)")
    left = [p for p in nums if p not in owner]
    if left:
        r.warnings.append(f"{name}: pad{'s' if len(left) > 1 else ''} {', '.join(left)} of {fp.name} "
                          f"{'are' if len(left) > 1 else 'is'} not assigned and left open (NC={','.join(left)} "
                          "says so on purpose)")
    return r


# ---- a circuit part on the board -----------------------------------------------------------------------------
BOARD_TYPES = ("R", "L", "C", "SRLC", "PRLC", "DEVICE", "SNP")
PROPS = ("Footprint", "Symbol", "Pads")


def terminals(c) -> list[str]:
    """The terminals of a circuit part that the pads are assigned to: 1 and 2 of a two-terminal part, the model's
    terminal names of a nonlinear device, the port numbers of a Touchstone block."""
    from . import circuit as CK
    if c.type == "DEVICE":
        return CK.device_pins(c)
    if c.type == "SNP":
        return [str(k + 1) for k in range(CK.nport_count(c))]
    return ["1", "2"]


@dataclass
class OnBoard:
    """A part's footprint with its pads assigned to the terminals."""
    footprint: Footprint
    symbol: Symbol | None
    pads: dict[str, list[str]]           # terminal -> pad numbers (and NC -> pads left open on purpose)
    check: PadCheck
    automatic: bool                      # the Pads property was empty: assigned by auto_pads()

    def terminal_of(self, number: str) -> str:
        for t, ps in self.pads.items():
            if number in ps:
                return t
        return ""


def on_board(c, project_dir: str | Path | None = None) -> OnBoard | None:
    """The footprint of a part with its pads, or None when the part has no Footprint. A footprint or symbol that
    cannot be read raises KicadError."""
    ref = c.props.get("Footprint", "").strip()
    if not ref:
        return None
    fp = find_footprint(ref, project_dir)
    sym = find_symbol(c.props["Symbol"], project_dir) if c.props.get("Symbol", "").strip() else None
    ts = terminals(c)
    given = parse_pads(c.props.get("Pads", ""))
    pads = given or auto_pads(ts, fp, sym)
    return OnBoard(fp, sym, pads, check(c.name, ts, fp, pads), automatic=not given)
