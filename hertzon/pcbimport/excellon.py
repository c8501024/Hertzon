"""Excellon drill file reader (as written by KiCad, Altium, Eagle, OrCAD, CAM tools).

Reads the header (METRIC/INCH, LZ/TZ, FILE_FORMAT, tool table), hits, G85 slots and routed slots
(G00/M15/G01/M16), modal coordinates and coordinates with or without a decimal point. Whether holes are
plated comes, in this order, from the tool comments (KiCad ``;TYPE=PLATED``), the X2 comment
(``TF.FileFunction,Plated`` / ``NonPlated``), the file name (``NPTH``) or is assumed (plated) and said so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .gerber import Msg

UNIT = {"METRIC": 1e-3, "INCH": 25.4e-3}


@dataclass
class Hole:
    x: float
    y: float
    d: float                      # metres
    plated: bool
    x1: float | None = None       # slot end (None: round hole)
    y1: float | None = None
    tool: str = ""

    @property
    def slot(self) -> bool:
        return self.x1 is not None


@dataclass
class Drill:
    holes: list[Hole] = field(default_factory=list)
    span: tuple[int, int] | None = None       # copper layers (from, to) of the X2 attribute, None = all
    function: list[str] = field(default_factory=list)
    messages: list[Msg] = field(default_factory=list)
    plated_known: bool = False


def looks_like_excellon(text: str) -> bool:
    head = text[:2000]
    return "M48" in head or bool(re.search(r"^T\d+C[\d.]+", head, re.M))


def read(text: str, name: str = "") -> Drill:
    out = Drill()
    msgs = out.messages
    unit = None
    zeros = None           # "LZ": leading zeros kept (trailing omitted), "TZ": trailing kept
    fmt: tuple[int, int] | None = None
    tools: dict[str, float] = {}
    tool_plated: dict[str, bool] = {}
    cur_plated: bool | None = None
    file_plated: bool | None = None
    tool = None
    x = y = 0.0
    incremental = False
    in_header = False
    rout = False
    plunged = False
    last = None
    lname = name.lower()
    if "npth" in lname or "non-plated" in lname or "nonplated" in lname:
        file_plated = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            c = line[1:].strip()
            m = re.search(r"FILE_FORMAT\s*=\s*(\d+):(\d+)", c)
            if m:
                fmt = (int(m.group(1)), int(m.group(2)))
            if re.search(r"TYPE\s*=\s*NON[_-]?PLATED", c, re.I):
                cur_plated = False
            elif re.search(r"TYPE\s*=\s*PLATED", c, re.I):
                cur_plated = True
            m = re.search(r"TF\.FileFunction,([^*]*)", c)
            if m:
                out.function = m.group(1).split(",")
                f = out.function
                if f and f[0] == "NonPlated":
                    file_plated = False
                elif f and f[0] == "Plated":
                    file_plated = True
                if len(f) >= 3 and f[1].isdigit() and f[2].isdigit():
                    out.span = (int(f[1]), int(f[2]))
            continue
        u = line.upper()
        if u.startswith("M48"):
            in_header = True
            continue
        if u in ("%", "M95"):
            in_header = False
            continue
        if u.startswith(("METRIC", "INCH")):
            unit = "METRIC" if u.startswith("METRIC") else "INCH"
            if ",LZ" in u:
                zeros = "LZ"
            elif ",TZ" in u:
                zeros = "TZ"
            m = re.search(r",(0+)\.(0+)", u)
            if m:
                fmt = (len(m.group(1)), len(m.group(2)))
            continue
        if u == "M71":
            unit = "METRIC"
            continue
        if u == "M72":
            unit = "INCH"
            continue
        if u.startswith(("FMAT", "VER", "ICI", "DETECT", "ATC", "BLKD", "SBK", "TCST", "R,", "G90",
                         "M47", "OFF", "/", "LOAD")) and not u.startswith("G05"):
            if u.startswith("ICI,ON") or u == "ICI":
                incremental = True
            continue
        if u == "G91":
            incremental = True
            continue
        m = re.match(r"^T0*(\d+)(.*)$", u)
        if m:
            t, rest = m.group(1), m.group(2)
            mc = re.search(r"C([\d.]+)", rest)
            if mc:
                tools[t] = float(mc.group(1))
                if cur_plated is not None:
                    tool_plated[t] = cur_plated
            if not in_header or not mc:
                tool = t if t != "0" else None
                if tool is not None and tool not in tools:
                    msgs.append(Msg("error", f"Tool T{t} is used but has no diameter: its holes are not loaded"))
            continue
        if u.startswith("M30") or u.startswith("M00"):
            break
        if u.startswith("M15"):
            plunged = True
            continue
        if u.startswith(("M16", "M17")):
            plunged = False
            continue
        if u.startswith("G00"):
            rout = True
            u = u[3:]
        elif u.startswith("G01"):
            u = u[3:]
        elif u.startswith("G05"):
            rout = False
            continue
        if u.startswith(("G02", "G03")):
            msgs.append(Msg("error", "Routed arcs (G02/G03) are not supported: that slot is not loaded"))
            continue
        if "X" not in u and "Y" not in u:
            if u and not u.startswith(("M", "G")):
                msgs.append(Msg("warning", f"Unknown drill statement '{line}' ignored"))
            continue
        if unit is None:
            unit = "INCH"
            msgs.append(Msg("warning", "No unit in the drill file: read in inch"))
        if fmt is None:
            fmt = (3, 3) if unit == "METRIC" else (2, 4)
        if zeros is None:
            zeros = "TZ"
            if not re.search(r"[XY][+-]?\d*\.\d", u):
                msgs.append(Msg("warning", "The drill file does not say how zeros are omitted: leading zeros "
                                           "read as omitted"))

        def num(s: str) -> float:
            if "." in s:
                return float(s)
            neg = s.startswith("-")
            dg = s.lstrip("+-")
            if zeros == "LZ":      # leading zeros present, trailing omitted: pad right
                dg = dg.ljust(fmt[0] + fmt[1], "0")
            v = int(dg or "0") / 10 ** fmt[1]
            return -v if neg else v

        seg = u.split("G85")
        pts = []
        for part in seg:
            mx = re.search(r"X([+-]?[\d.]+)", part)
            my = re.search(r"Y([+-]?[\d.]+)", part)
            nx = (x + num(mx.group(1)) if incremental else num(mx.group(1))) if mx else x
            ny = (y + num(my.group(1)) if incremental else num(my.group(1))) if my else y
            x, y = nx, ny
            pts.append((x, y))
        if tool is None or tool not in tools:
            if tool is None:
                msgs.append(Msg("error", "Holes before a tool is selected: not loaded"))
            continue
        s = UNIT[unit]
        d = tools[tool] * s
        plated = tool_plated.get(tool, file_plated if file_plated is not None else True)
        if len(pts) == 2:                       # G85 slot
            out.holes.append(Hole(pts[0][0] * s, pts[0][1] * s, d, plated, pts[1][0] * s, pts[1][1] * s, tool))
        elif rout:
            if plunged and last is not None:
                out.holes.append(Hole(last[0] * s, last[1] * s, d, plated, pts[0][0] * s, pts[0][1] * s, tool))
            last = pts[0]
        else:
            out.holes.append(Hole(pts[0][0] * s, pts[0][1] * s, d, plated, tool=tool))
    out.plated_known = bool(tool_plated) or file_plated is not None
    if not out.plated_known and out.holes:
        msgs.append(Msg("warning", "The drill file does not say whether its holes are plated: read as plated"))
    return out


def read_file(path) -> Drill:
    from pathlib import Path
    with open(path, encoding="utf-8", errors="replace") as fh:
        return read(fh.read(), Path(path).name)
