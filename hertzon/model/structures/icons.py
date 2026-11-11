"""Top-view pictures of the structures for the cards of the design wizard (SVG, 120 x 80).

Every picture is built from a few primitives so that lines join cleanly: all copper is first drawn
as a slightly wider dark outline and then filled, so traces that meet merge into one shape without
seams. Lines are given by their centre line and width, like a microstrip layout.
"""

BG, FRAME = "#e9f1fb", "#9cc3ee"
CU, CU_EDGE = "#c9952f", "#9a6c14"
GND, GND_EDGE = "#9aa3b2", "#6b7385"
DARK, PORT = "#2b3340", "#1d4f91"
EDGE = 1.2          # width of the dark outline around copper


def _f(v: float) -> str:
    return f"{v:g}"


def line(d: str, w: float = 4) -> tuple:
    """A trace along the path d (centre line) with width w."""
    return ("t", d, w)


def shape(d: str) -> tuple:
    """A filled copper polygon."""
    return ("p", d)


def disc(cx: float, cy: float, r: float) -> tuple:
    return ("c", cx, cy, r)


def ring(cx: float, cy: float, r: float, w: float = 4) -> tuple:
    return ("r", cx, cy, r, w)


def hole(d: str) -> tuple:
    """An opening in the copper (slot, notch)."""
    return ("h", d)


def ground(x: float, y: float, w: float, h: float) -> tuple:
    return ("g", x, y, w, h)


def resistor(x: float, y: float, w: float, h: float) -> tuple:
    """A lumped part (resistor, capacitor, inductor) seen from the top."""
    return ("R", x, y, w, h)


def via(x: float, y: float) -> tuple:
    return ("V", x, y)


def feed(x: float, y: float) -> tuple:
    """The feed point of an antenna."""
    return ("F", x, y)


def wire(d: str) -> tuple:
    """A bond wire."""
    return ("W", d)


def raw(svg: str) -> tuple:
    """Plain SVG drawn under the copper."""
    return ("X", svg)


def top(svg: str) -> tuple:
    """Plain SVG drawn over everything (markers, labels)."""
    return ("O", svg)


def svg(*items) -> str:
    under, outline, fill, over = [], [], [], []
    for it in items:
        k = it[0]
        if k == "g":
            x, y, w, h = map(_f, it[1:])
            under.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{GND}" '
                         f'stroke="{GND_EDGE}" stroke-width=".6"/>')
        elif k == "X":
            under.append(it[1])
        elif k == "t":
            d, w = it[1], it[2]
            outline.append(f'<path d="{d}" fill="none" stroke="{CU_EDGE}" stroke-width="{_f(w + EDGE)}" '
                           f'stroke-linejoin="miter" stroke-linecap="butt"/>')
            fill.append(f'<path d="{d}" fill="none" stroke="{CU}" stroke-width="{_f(w)}" '
                        f'stroke-linejoin="miter" stroke-linecap="butt"/>')
        elif k == "p":
            outline.append(f'<path d="{it[1]}" fill="{CU_EDGE}" stroke="{CU_EDGE}" stroke-width="{_f(EDGE)}" '
                           f'stroke-linejoin="miter"/>')
            fill.append(f'<path d="{it[1]}" fill="{CU}"/>')
        elif k == "c":
            cx, cy, r = map(_f, it[1:])
            outline.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{CU_EDGE}" stroke="{CU_EDGE}" '
                           f'stroke-width="{_f(EDGE)}"/>')
            fill.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{CU}"/>')
        elif k == "r":
            cx, cy, r = map(_f, it[1:4])
            w = it[4]
            outline.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{CU_EDGE}" '
                           f'stroke-width="{_f(w + EDGE)}"/>')
            fill.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{CU}" stroke-width="{_f(w)}"/>')
        elif k == "h":
            over.append(f'<path d="{it[1]}" fill="{BG}" stroke="{CU_EDGE}" stroke-width=".6"/>')
        elif k == "R":
            x, y, w, h = map(_f, it[1:])
            over.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="1" fill="{DARK}"/>')
        elif k == "V":
            x, y = map(_f, it[1:])
            over.append(f'<circle cx="{x}" cy="{y}" r="2.2" fill="#ffffff" stroke="{DARK}" stroke-width=".8"/>')
        elif k == "F":
            x, y = map(_f, it[1:])
            over.append(f'<circle cx="{x}" cy="{y}" r="2.4" fill="#ffffff" stroke="{PORT}" stroke-width="1.4"/>')
        elif k == "O":
            over.append(it[1])
        elif k == "W":
            over.append(f'<path d="{it[1]}" fill="none" stroke="{DARK}" stroke-width=".9"/>')
        else:      # pragma: no cover
            raise ValueError(k)
    # holes sit on top of the copper but under parts and markers
    holes = [o for o in over if f'fill="{BG}"' in o]
    marks = [o for o in over if f'fill="{BG}"' not in o]
    body = "".join(under + outline + fill + holes + marks)
    return ('<svg viewBox="0 0 120 80" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
            f'<rect x="2" y="2" width="116" height="76" rx="3" fill="{BG}" stroke="{FRAME}"/>{body}</svg>')


def _hairpins(x0: float, n: int, pitch: float, leg: float, top: float, bottom: float, w: float = 3) -> list:
    """n U-shaped resonators side by side, alternately open at the top and at the bottom."""
    out = []
    for k in range(n):
        a, b = x0 + k * pitch, x0 + k * pitch + leg
        if k % 2 == 0:
            out.append(line(f"M{_f(a)} {_f(top)} V{_f(bottom)} H{_f(b)} V{_f(top)}", w))
        else:
            out.append(line(f"M{_f(a)} {_f(bottom)} V{_f(top)} H{_f(b)} V{_f(bottom)}", w))
    return out


def _ratrace() -> str:
    import math
    cx, cy, r = 60, 34, 22
    items = [ring(cx, cy, r, 3)]
    # four ports 60 degrees apart (2 left, 1 and 3 below, 4 right); the arc over the top is 180 degrees
    # (3/4 wavelength). The short feeds leave the ring radially.
    for ang in (180, 240, 300, 0):
        a = math.radians(ang)
        x0, y0 = cx + (r - 1) * math.cos(a), cy - (r - 1) * math.sin(a)
        x1, y1 = cx + (r + 13) * math.cos(a), cy - (r + 13) * math.sin(a)
        items.append(line(f"M{x0:.2f} {y0:.2f} L{x1:.2f} {y1:.2f}", 4))
    return svg(*items)


def _edge() -> str:
    """Parallel-coupled filter: each section overlaps half of the next one."""
    items = []
    n, step = 6, 104 / 7
    for i in range(n):
        x = 8 + i * step
        y = 22 + i * 7.2
        items.append(line(f"M{x:.1f} {y:.1f} H{min(x + 2 * step, 112):.1f}", 4))
    return svg(*items)


def _inter() -> str:
    items = [line("M8 22 H24", 3), line("M96 58 H112", 3)]
    for k, x in enumerate(range(24, 97, 12)):
        if k % 2 == 0:
            items += [line(f"M{x} 70 V12", 4), via(x, 70)]
        else:
            items += [line(f"M{x} 10 V68", 4), via(x, 10)]
    return svg(*items)


def _butler() -> str:
    items = [
        line("M8 14 H112", 3), line("M8 66 H112", 3),
        line("M8 30 H46 L66 50 H112", 3), line("M8 50 H46 L66 30 H112", 3),
    ]
    for x in (18, 30, 82, 94):
        items += [line(f"M{x} 14 V30", 3), line(f"M{x} 50 V66", 3)]
    return svg(*items)


ICONS = {
    # ---- antennas -------------------------------------------------------------------------------
    "patch": svg(
        shape("M30 12 H90 V54 H66 V40 H54 V54 H30 Z"),
        line("M60 40 V78", 4),
    ),
    "cppatch": svg(
        shape("M44 14 H84 V54 L76 62 H36 V22 Z"),
        feed(68, 30),
    ),
    "cpatch": svg(
        disc(60, 40, 27),
        feed(60, 54),
    ),
    "dipole": svg(
        line("M10 40 H57", 6), line("M63 40 H110", 6),
        feed(60, 40),
    ),
    "monopole": svg(
        ground(10, 60, 100, 8),
        line("M60 10 V57", 4),
        feed(60, 59),
    ),
    "ifa": svg(
        ground(8, 52, 104, 22),
        line("M24 52 V20 H106", 4),
        line("M40 20 V47", 4),
        feed(40, 50),
    ),
    # UWB monopoles: the disc with a coplanar feed (grounds beside the strip on the same layer), the plate with a
    # microstrip over a partial ground on the bottom (drawn behind, dashed edge)
    "uwb": svg(
        ground(8, 56, 47, 20), ground(65, 56, 47, 20),
        disc(60, 29, 21),
        line("M60 48 V78", 4),
    ),
    "uwbrect": svg(
        raw(f'<rect x="8" y="58" width="104" height="18" fill="{GND}" fill-opacity=".55" stroke="{GND_EDGE}" '
            'stroke-width=".6" stroke-dasharray="2 1.5"/>'),
        shape("M46 52 H74 L84 42 V10 H36 V42 Z"),
        line("M60 50 V78", 4),
    ),
    "slot": svg(
        shape("M8 8 H112 V72 H8 Z"),
        hole("M20 36 H100 V44 H20 Z"),
        top(f'<path d="M72 72 V28" stroke="{PORT}" stroke-width="3" stroke-dasharray="3 2"/>'),
    ),
    "vivaldi": svg(
        shape("M8 8 H112 V72 H8 Z"),
        hole("M112 14 C 70 16, 40 36, 30 38.5 V41.5 C 40 44, 70 64, 112 66 Z"),
        hole("M18 40 A6 6 0 1 0 30 40 A6 6 0 1 0 18 40 Z"),
    ),
    "yagi": svg(
        line("M14 10 V70", 4),
        line("M34 13 V37", 4), line("M34 43 V67", 4), feed(34, 40),
        line("M56 17 V63", 4), line("M78 19 V61", 4), line("M100 21 V59", 4),
    ),
    "array": svg(
        shape("M14 8 H38 V30 H14 Z"), shape("M82 8 H106 V30 H82 Z"),
        shape("M14 50 H38 V72 H14 Z"), shape("M82 50 H106 V72 H82 Z"),
        line("M26 30 V50", 3), line("M94 30 V50", 3),
        line("M26 40 H94", 3), line("M60 40 V78", 3),
    ),
    "helix": svg(
        ground(10, 66, 100, 6),
        raw(f'<path d="M60 66 V60 C 40 58, 40 52, 60 50 C 80 48, 80 42, 60 40 C 40 38, 40 32, 60 30 '
            f'C 80 28, 80 22, 60 20 C 40 18, 40 12, 60 10" fill="none" stroke="{CU_EDGE}" stroke-width="4.2"/>'
            f'<path d="M60 66 V60 C 40 58, 40 52, 60 50 C 80 48, 80 42, 60 40 C 40 38, 40 32, 60 30 '
            f'C 80 28, 80 22, 60 20 C 40 18, 40 12, 60 10" fill="none" stroke="{CU}" stroke-width="3"/>'),
    ),
    "horn": svg(
        raw(f'<path d="M10 32 H40 L108 8 V72 L40 48 H10 Z" fill="#cfd5de" stroke="{GND_EDGE}"/>'
            '<path d="M10 32 H40 V48 H10 Z" fill="#b6bec9"/>'),
    ),
    # ---- matching -------------------------------------------------------------------------------
    "qwt": svg(
        line("M8 40 H34", 4), line("M34 40 H60", 8), line("M60 40 H86", 13), line("M86 40 H112", 20),
    ),
    "stub": svg(
        line("M8 52 H96", 4), line("M48 52 V12", 4),
        top(f'<circle cx="104" cy="52" r="8" fill="#ffffff" stroke="{PORT}" stroke-width="1.4"/>'
            f'<text x="104" y="55.5" font-family="sans-serif" font-size="10" text-anchor="middle" '
            f'fill="{PORT}">Z</text>'),
    ),
    "lump": svg(
        line("M8 34 H112", 4),
        line("M76 34 V46", 4), shape("M73 62 H79 V68 H73 Z"),
        resistor(38, 30, 14, 8), resistor(71.5, 46, 9, 16),
        via(76, 66),
    ),
    # ---- couplers -------------------------------------------------------------------------------
    "branch": svg(
        line("M36 22 H84", 7), line("M36 58 H84", 7),
        line("M36 18.5 V61.5", 4), line("M84 18.5 V61.5", 4),
        line("M8 22 H36", 4), line("M84 22 H112", 4), line("M8 58 H36", 4), line("M84 58 H112", 4),
    ),
    "ratrace": _ratrace(),
    "cpl": svg(
        line("M8 32 H112", 4),
        line("M8 66 H32 V40 H88 V66 H112", 4),
    ),
    "lange": svg(
        line("M8 28 H112", 3), line("M28 28 V44 H88", 3),
        line("M8 52 H92 V36 H32", 3), line("M92 52 H112", 3),
        wire("M50 28 C 44 34, 44 38, 50 44"), wire("M70 36 C 64 42, 64 46, 70 52"),
    ),
    # ---- dividers -------------------------------------------------------------------------------
    "wilk": svg(
        line("M8 40 H30", 4),
        line("M30 40 C 30 28, 40 22, 56 22 H112", 4),
        line("M30 40 C 30 52, 40 58, 56 58 H112", 4),
        line("M80 22 V30", 3), line("M80 50 V58", 3),
        resistor(76, 30, 8, 20),
    ),
    "tee": svg(
        line("M8 40 H34", 4),
        line("M34 17 V63", 4),
        line("M32 20 H74", 6), line("M32 60 H74", 6),
        line("M74 20 H112", 4), line("M74 60 H112", 4),
    ),
    "gysel": svg(
        line("M8 40 H28", 4),
        line("M96 18 H28 V62 H96 V18", 4),
        line("M56 18 V6", 4), line("M56 62 V74", 4),
        line("M96 18 H100", 3), line("M96 62 H100", 3),
        resistor(100, 15, 8, 6), resistor(100, 59, 8, 6),
        shape("M108 15 H113 V21 H108 Z"), shape("M108 59 H113 V65 H108 Z"),
        via(110.5, 18), via(110.5, 62),
    ),
    # ---- filters --------------------------------------------------------------------------------
    "hairpin": svg(
        line("M8 44 H24", 3), line("M92 44 H112", 3),
        *_hairpins(24, 4, 20, 8, 16, 64),
    ),
    "edge": _edge(),
    "stepped": svg(
        line("M8 40 H22", 4), line("M22 40 H32", 2), line("M32 40 H46", 22), line("M46 40 H56", 2),
        line("M56 40 H72", 26), line("M72 40 H82", 2), line("M82 40 H96", 22), line("M96 40 H112", 4),
    ),
    "stubbp": svg(
        line("M8 58 H112", 4),
        line("M30 58 V18", 4), line("M60 58 V18", 4), line("M90 58 V18", 4),
        via(30, 18), via(60, 18), via(90, 18),
    ),
    "inter": _inter(),
    "diplex": svg(
        line("M8 40 H22", 4), line("M22 16 V64", 4),
        line("M20 16 H40", 3), line("M20 64 H40", 3),
        *_hairpins(40, 3, 12, 5, 6, 30, 2.5),
        line("M69 16 H112", 3),
        *_hairpins(40, 3, 15, 7, 50, 74, 2.5),
        line("M77 64 H112", 3),
    ),
    "corp": svg(
        line("M8 40 H26", 4), line("M26 22 V58", 4),
        line("M24 22 H52", 3), line("M24 58 H52", 3),
        line("M52 12 V32", 3), line("M52 48 V68", 3),
        line("M50.5 12 H86", 3), line("M50.5 32 H86", 3), line("M50.5 48 H86", 3), line("M50.5 68 H86", 3),
        shape("M86 6 H104 V18 H86 Z"), shape("M86 26 H104 V38 H86 Z"),
        shape("M86 42 H104 V54 H86 Z"), shape("M86 62 H104 V74 H86 Z"),
    ),
    "butler": _butler(),
    "bias": svg(
        line("M8 58 H112", 5),
        line("M60 58 V30", 1.6),
        shape("M60 30 L46 12 A 23 23 0 0 1 74 12 Z"),
        line("M60 30 H100", 1.6),
        shape("M98 25 H108 V35 H98 Z"),
    ),
    "stubbs": svg(
        line("M8 64 H112", 4),
        line("M32 64 V14", 8), line("M60 64 V14", 8), line("M88 64 V14", 8),
    ),
}
