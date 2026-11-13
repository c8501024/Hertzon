"""PCB edge-launch (end-launch) connectors: SMA, 2.92 mm and 2.4 mm.

The library (data/edge_launch_connectors.json, generated in the hertzon-connectors repository) lists the parts of
the vendors with interface, maximum frequency, mounting, board thickness, launch pin, materials and links to the
data sheets and 3D files. ``insert_connector`` adds a simulation model of a part at a board edge:

* the coaxial line from the mating plane (wave port) to the board edge, with the support bead and the necked
  centre conductor that keeps the bead section at 50 ohm, in the interface dimensions of the standard
  (IEEE 287, MIL-STD-348, IEC 61169),
* the connector body around it (only the faces that touch the field matter), and for a flange connector the
  clamping block with its channel over the launch,
* the launch: pin or flat tab on a pad, a taper to the trace, a grounded coplanar section with vias at the edge;
  pad width, pad length, launch gap and taper length are design variables (<name>_wp, _lp, _gl, _lt), so the
  transition can be tuned or optimized (``add_launch_optimization``).

``replace_lumped_port`` puts a connector where a lumped port sits at a board edge: board edge, direction, trace
and board thickness come from the port sheet, the connector's wave port takes the number of the lumped port.

Body, block and bead positions are typical values (the vendors publish them only as drawings); the interface
dimensions are those of the standards. All lengths in this module are millimetres."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

LIBRARY_FILE = Path(__file__).with_name("data") / "edge_launch_connectors.json"
INTERFACES = ("SMA", "2.92mm", "2.4mm")
IN = 25.4
C0_MM_GHZ = 299.792458          # c in mm*GHz

# Hertzon materials for the parts of a connector
MATERIAL = {"PTFE": "Teflon (tm)", "Ultem 1000 (PEI)": "Ultem 1000 (tm)", "Kel-F (PCTFE)": "Kel-F (PCTFE)",
            "PEEK": "PEEK", "air": "vacuum"}
PIN_MATERIAL = "gold"           # BeCu with >= 1.27 um gold: more than 1.6 skin depths from 10 GHz on


@lru_cache(maxsize=1)
def library() -> dict:
    return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))


def parts(interface: str | None = None) -> list[dict]:
    return [p for p in library()["parts"] if interface is None or p["interface"] == interface]


def part(pid: str) -> dict:
    """A part by its id or part number."""
    for p in library()["parts"]:
        if pid in (p["id"], p["part_number"]):
            return p
    raise KeyError(f"Unknown connector '{pid}'")


def label(p: dict) -> str:
    f = f", {p['f_max_GHz']:g} GHz" if p.get("f_max_GHz") else ""
    return f"{p['manufacturer']} {p['part_number']} ({p['interface']}, {p['gender'].split()[0]}{f})"


def f_max(p: dict) -> float:
    return float(p.get("f_max_GHz") or library()["interfaces"][p["interface"]]["f_max_GHz"])


# ------------------------------------------------------------------------------------------------------------
# coaxial line and launch dimensions
# ------------------------------------------------------------------------------------------------------------
def z_coax(d_outer: float, d_inner: float, er: float) -> float:
    return 59.9585 / math.sqrt(er) * math.log(d_outer / d_inner)


def d_inner_for_z(d_outer: float, er: float, z: float = 50.0) -> float:
    return d_outer / math.exp(z * math.sqrt(er) / 59.9585)


def _ellipk(k: float) -> float:
    a, b = 1.0, math.sqrt(max(1 - k * k, 0.0))
    for _ in range(40):
        a, b = (a + b) / 2, math.sqrt(a * b)
    return math.pi / (2 * a)


def _kk(k: float) -> float:
    return _ellipk(k) / _ellipk(math.sqrt(1 - k * k))


def cpwg_z0(w: float, g: float, h: float, er: float) -> float:
    """Characteristic impedance of a conductor-backed coplanar waveguide (quasi-static, conformal mapping)."""
    k = w / (w + 2 * g)
    k3 = math.tanh(math.pi * w / (4 * h)) / math.tanh(math.pi * (w + 2 * g) / (4 * h))
    r = _kk(k3) / _kk(k)
    eeff = (1 + er * r) / (1 + r)
    return 60 * math.pi / math.sqrt(eeff) / (_kk(k) + _kk(k3))


def cpwg_gap(w: float, h: float, er: float, z0: float = 50.0) -> float:
    """Gap of a 50 ohm grounded coplanar line of width w (bisection); large when even g -> inf stays below z0."""
    lo, hi = 0.02, 5.0
    if cpwg_z0(w, hi, h, er) < z0:
        return hi
    for _ in range(60):
        m = (lo + hi) / 2
        if cpwg_z0(w, m, h, er) > z0:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2


@dataclass
class Launch:
    """Dimensions of the connector model (mm). Values ending in _est are typical, not from a data sheet."""
    interface: str
    template: str                   # "legs_tab" or "flange_block"
    b: float                        # outer conductor inner diameter
    a: float                        # centre conductor diameter
    coax_material: str
    bead_material: str
    neck: float                     # centre conductor diameter inside the bead (50 ohm)
    thread_d: float
    body_material: str
    f_max: float
    bead_len_est: float = 0.9
    bead_z_est: float = 3.25        # bead centre behind the mating plane
    coax_len_est: float = 7.6       # mating plane to the flange (flange_block) or to the board edge (legs_tab)
    # legs_tab
    tab_w_est: float = 0.76
    tab_t_est: float = 0.30
    tab_len_est: float = 3.0
    body_len_est: float = 3.0
    body_w_est: float = 6.35
    leg_w_est: float = 1.0
    leg_t_est: float = 0.5
    leg_len_est: float = 3.8
    leg_x_est: float = 2.4
    # flange_block
    launch_pin: float = 0.254
    launch_diel: float = 1.613
    wall_est: float = 0.3           # launch dielectric length (block wall ahead of the edge)
    over_est: float = 0.4           # launch pin length on the pad
    block_w_est: float = 12.7
    block_len_est: float = 6.35
    block_top_est: float = 3.2
    block_bot_est: float = 1.6
    flange_t_est: float = 2.0
    flange_h_est: float = 7.6
    notes: list[str] = field(default_factory=list)

    @property
    def pin(self) -> float:
        """Diameter (pin) or width (tab) of the contact on the pad."""
        return self.launch_pin if self.template == "flange_block" else self.tab_w_est

    @property
    def z_axis(self) -> float:
        """Height of the connector axis above the pad (the contact rests on it)."""
        return self.launch_pin / 2 if self.template == "flange_block" else self.tab_t_est / 2


def launch_for(p: dict, board_h: float) -> Launch:
    lib = library()
    f = lib["interfaces"][p["interface"]]
    diel = lib["dielectrics"]
    b, a = f["outer_conductor_id_mm"], f["center_conductor_od_mm"]
    mats = p.get("materials") or {}
    bead = "Ultem 1000 (PEI)"
    if "PEEK" in str(mats):
        bead = "PEEK"
    neck = d_inner_for_z(b, diel[bead]["er"])
    body = "stainless_steel" if "stainless" in json.dumps(mats).lower() or "CRES" in json.dumps(mats) else "gold"
    L = Launch(p["interface"], p["template"], b, a, MATERIAL[f["dielectric"]], MATERIAL[bead], neck,
               f["thread_major_mm"], body, f_max(p))
    sock = 1.9 if p["interface"] == "2.4mm" else 2.3
    L.bead_z_est = sock + 0.5 + L.bead_len_est / 2
    if p["interface"] == "2.4mm":
        L.body_w_est, L.leg_x_est = 7.9, 2.9
    if L.template == "flange_block":
        dp, dd = p.get("launch_pin_od_mm"), p.get("launch_dielectric_od_mm")
        if not dp:
            opt = ".005" if board_h <= 0.15 else (".007" if p["interface"] == "2.4mm" else ".010")
            dp_in, dd_in = lib["swm_launch_options_in"][opt]
            dp, dd = dp_in * IN, dd_in * IN
            L.notes.append(f"launch pin {opt} in (typical for a {board_h:g} mm board)")
        L.launch_pin, L.launch_diel = dp, dd
        L.coax_len_est = L.flange_t_est + 5.6
        if "narrow" in p.get("mounting", ""):
            L.block_w_est = 0.350 * IN
    else:
        L.coax_len_est = L.body_len_est + (5.0 if p["interface"] == "2.4mm" else 5.6)
    return L


# ------------------------------------------------------------------------------------------------------------
# placement
# ------------------------------------------------------------------------------------------------------------
@dataclass
class EdgePlacement:
    """Where the connector goes: ``point`` is the end of the trace at the board edge on the top of the
    substrate (mm, global), ``direction`` the outward direction of the connector axis ("+X", "-X", "+Y", "-Y",
    "+Z", "-Z"), ``normal`` the board normal axis (0, 1, 2; pointing from the substrate to the trace)."""
    point: tuple[float, float, float]
    direction: str = "-Y"
    normal: int = 2
    board_h: float = 0.508
    trace_w: float = 0.0            # width of the trace that ends at the edge (0: unknown)
    er: float = 3.55                # substrate permittivity (for the launch gap)

    @property
    def u(self) -> int:
        return "XYZ".index(self.direction[1])

    @property
    def sign(self) -> int:
        return 1 if self.direction[0] == "+" else -1

    @property
    def lat(self) -> int:
        return 3 - self.u - self.normal


class _Frame:
    """Local coordinates of the connector -> Hertzon expressions: s outward from the edge along the axis,
    t lateral, n along the board normal from the top of the substrate. Every expression refers to the
    position variables <name>_x, _y, _z so the whole connector moves with them."""

    def __init__(self, place: EdgePlacement, var: list[str]):
        if place.u == place.normal:
            raise ValueError("the connector axis has to lie in the board plane")
        self.p, self.var = place, var

    @staticmethod
    def _e(x) -> str:
        if isinstance(x, (int, float)):
            return f"{x:.6g}mm"
        return f"({x})"

    def _coord(self, axis_vals: dict[int, str]) -> str:
        return ",".join(axis_vals[i] for i in range(3))

    def point(self, s, t, n) -> str:
        p, e = self.p, self._e
        vals = {p.u: f"{self.var[p.u]}{'+' if p.sign > 0 else '-'}{e(s)}",
                p.lat: f"{self.var[p.lat]}+{e(t)}", p.normal: f"{self.var[p.normal]}+{e(n)}"}
        return self._coord(vals)

    def box(self, s0, s1, t0, t1, n0, n1) -> dict:
        """CreateBox props of the local box s0..s1, t0..t1, n0..n1 (lower values first)."""
        p, e = self.p, self._e
        lo = {p.u: f"{self.var[p.u]}+{e(s0)}" if p.sign > 0 else f"{self.var[p.u]}-{e(s1)}",
              p.lat: f"{self.var[p.lat]}+{e(t0)}", p.normal: f"{self.var[p.normal]}+{e(n0)}"}
        size = {p.u: f"{e(s1)}-{e(s0)}", p.lat: f"{e(t1)}-{e(t0)}", p.normal: f"{e(n1)}-{e(n0)}"}
        return {"Position": self._coord(lo), "XSize": size[0], "YSize": size[1], "ZSize": size[2]}

    def rect(self, s0, s1, t0, t1, n=0.0) -> dict:
        """CreateRectangle in the board plane at height n."""
        p, e = self.p, self._e
        lo = {p.u: f"{self.var[p.u]}+{e(s0)}" if p.sign > 0 else f"{self.var[p.u]}-{e(s1)}",
              p.lat: f"{self.var[p.lat]}+{e(t0)}", p.normal: f"{self.var[p.normal]}+{e(n)}"}
        size = {p.u: f"{e(s1)}-{e(s0)}", p.lat: f"{e(t1)}-{e(t0)}"}
        axis = "XYZ"[p.normal]
        out = {"Position": self._coord(lo), "Axis": axis}
        for i in range(3):
            if i != p.normal:
                out["XYZ"[i] + "Size"] = size[i]
        return out

    def polygon(self, pts) -> dict:
        """CreatePolyline (closed, covered) in the board plane through local (s, t) points."""
        return {"Points": ";".join(self.point(s, t, 0.0) for s, t in pts), "Closed": "True", "Covered": "True"}

    def cyl(self, s0, s1, r, n=0.0) -> dict:
        """CreateCylinder along the connector axis from s0 to s1 (s0 < s1), axis at height n."""
        p, e = self.p, self._e
        base = s0 if p.sign > 0 else s1
        c = {p.u: f"{self.var[p.u]}{'+' if p.sign > 0 else '-'}{e(base)}", p.lat: f"{self.var[p.lat]}",
             p.normal: f"{self.var[p.normal]}+{e(n)}"}
        return {"CenterPosition": self._coord(c), "Axis": "XYZ"[p.u], "Radius": e(r), "Height": f"{e(s1)}-{e(s0)}"}

    def cone(self, s0, s1, r0, r1, n=0.0) -> dict:
        """CreateCone along the axis: radius r0 at s0, r1 at s1."""
        p, e = self.p, self._e
        base, rl, ru = (s0, r0, r1) if p.sign > 0 else (s1, r1, r0)
        c = {p.u: f"{self.var[p.u]}{'+' if p.sign > 0 else '-'}{e(base)}", p.lat: f"{self.var[p.lat]}",
             p.normal: f"{self.var[p.normal]}+{e(n)}"}
        return {"CenterPosition": self._coord(c), "Axis": "XYZ"[p.u], "LowerRadius": e(rl), "UpperRadius": e(ru),
                "Height": f"{e(s1)}-{e(s0)}"}

    def via(self, s, t, r, h) -> dict:
        """Via cylinder through the substrate (n from -h to 0)."""
        p = self.p
        c = {p.u: f"{self.var[p.u]}{'+' if p.sign > 0 else '-'}{self._e(s)}",
             p.lat: f"{self.var[p.lat]}+{self._e(t)}", p.normal: f"{self.var[p.normal]}-{self._e(h)}"}
        return {"CenterPosition": self._coord(c), "Axis": "XYZ"[p.normal], "Radius": self._e(r),
                "Height": self._e(h)}

    def circle(self, s, r, n=0.0) -> dict:
        p = self.p
        c = {p.u: f"{self.var[p.u]}{'+' if p.sign > 0 else '-'}{self._e(s)}", p.lat: f"{self.var[p.lat]}",
             p.normal: f"{self.var[p.normal]}+{self._e(n)}"}
        return {"CenterPosition": self._coord(c), "Axis": "XYZ"[p.u], "Radius": self._e(r)}


# ------------------------------------------------------------------------------------------------------------
# footprint (launch) defaults
# ------------------------------------------------------------------------------------------------------------
@dataclass
class Footprint:
    """Launch footprint at the edge (mm), local coordinates s (outward, the edge at 0) and t (lateral):

    * pad (width wp, length lp from the edge) and taper (length lt) from the pad to the trace width,
    * a grounded coplanar line: gap gl at the launch, gap gc (50 ohm) along the trace,
    * a top ground pour of half width ``half`` under the clamp block or the solder legs, length ``lf`` from the
      edge, stitched to the bottom ground by vias: a fence along both gaps and a grid in the pour (the pour, the
      block and the ground plane would form parallel-plate cavities with resonances in band otherwise)."""
    wp: float          # pad width at the edge
    lp: float          # straight pad length from the edge
    gl: float          # gap at the launch
    lt: float          # taper length from the pad to the trace width
    gc: float          # gap along the trace (50 ohm grounded coplanar)
    lf: float          # footprint length from the edge
    half: float        # half width of the ground pour
    via_d: float
    via_pitch: float
    via_first: float   # centre of the first via from the board edge
    rows: int          # via rows per side (the fence row and the grid rows outward)

    @property
    def via_t0(self) -> float:
        """Lateral position of the fence row (clear of both gaps by 0.15 mm)."""
        return max(self.wp / 2 + self.gl, self.tw_half + self.gc) + 0.15 + self.via_d / 2

    tw_half: float = 0.0


def fence_gap(f_ghz: float, er: float, margin: float = 1.25) -> float:
    """Largest edge-to-edge gap of a via fence whose quarter-wave resonance c / (4 gap sqrt(er)) lies at
    ``margin`` times f (mm)."""
    return C0_MM_GHZ / (4 * math.sqrt(er) * margin * f_ghz)


def footprint_for(L: Launch, place: EdgePlacement) -> Footprint:
    """Defaults after the launch design rules of Southwest Microwave (application notes "Optimizing Test Boards
    for 50 GHz End Launch Connectors", 2007, and "The Design and Test of Broadband Launches up to 50 GHz on Thin
    and Thick Substrates", 2011):

    * the contact adds capacitance, so the line is narrowed at the launch (inductance) instead of widened to the
      pin: the pad is about 0.73 of the trace (their optimized 30 mil board: 0.033 in at the edge for a 0.045 in
      trace) but at least 1.5 pin diameters (a tab: its width plus 0.1 mm), tapered to the trace over the length
      the contact rests on the line,
    * a grounded coplanar launch: the ground-to-ground width at the edge about the launch dielectric diameter of
      the connector (clamp connectors), else a 50 ohm coplanar gap; along the trace the 50 ohm coplanar gap,
    * a via fence along both gaps and a via grid in the ground pour under the block or the legs, openings below
      a quarter wavelength in the substrate up to 1.25 f_max, vias close to the gap and the first one close to the
      edge.

    All values are starting points: the pad width and length, both gaps and the taper are design variables, and
    ``add_launch_optimization`` tunes them in the transition model."""
    h = place.board_h
    tw = place.trace_w or max(L.pin * 3, 0.3)
    if L.template == "flange_block":
        wp = min(tw, max(0.73 * tw, 1.5 * L.pin))
        lp = 0.1
        lt = L.over_est + 0.2 if abs(tw - wp) > 1e-3 else 0.0
        gl = max(0.1, (L.launch_diel - wp) / 2)
    else:
        wp = max(L.tab_w_est + 0.1, min(tw, L.tab_w_est + 0.3))
        lp = L.tab_len_est + 0.2
        lt = round(max(0.3, 2.0 * abs(tw - wp)), 3) if abs(tw - wp) > 1e-3 else 0.0
        gl = cpwg_gap(wp, h, place.er)
    gl = round(min(max(gl, 0.1), 3 * h + 0.2), 3)
    gc = round(min(max(cpwg_gap(tw, h, place.er), 0.1), 3 * h + 0.2), 3)
    via_d = 0.2 if h <= 0.3 else (0.3 if h <= 0.9 else 0.4)
    gap = fence_gap(L.f_max, place.er)
    pitch = round(max(2 * via_d, min(via_d + gap, 4 * via_d)), 3)
    if via_d + gap < 2 * via_d:
        pitch = round(via_d + gap, 3)          # a dense fence before the manufacturing rule of thumb
    first = round(via_d / 2 + 0.15, 3)
    if L.template == "flange_block":
        lf = max(lp + lt + 0.5, L.block_len_est + 0.3)
        half = L.block_w_est / 2
    else:
        lf = max(lp + lt + 0.5, L.leg_len_est + 0.3)
        half = L.leg_x_est + L.leg_w_est / 2 + 0.3
    fp = Footprint(wp=round(wp, 3), lp=round(lp, 3), gl=gl, lt=round(lt, 3), gc=gc, lf=round(lf, 3), half=0.0,
                   via_d=via_d, via_pitch=pitch, via_first=first, rows=1, tw_half=tw / 2)
    fp.half = round(max(half, fp.via_t0 + via_d / 2 + 0.15), 3)
    fp.rows = max(1, min(6, 1 + int((fp.half - 0.15 - via_d / 2 - fp.via_t0) // pitch)))
    return fp


# ------------------------------------------------------------------------------------------------------------
# connector objects
# ------------------------------------------------------------------------------------------------------------
MODES = ("Ideal", "Transition", "3D")
MODE_LABELS = {"Ideal": "Ideal port (connector shown)", "Transition": "Transition model (circuit)",
               "3D": "Full 3D connector"}
PREVIEW_TRANSPARENCY = 0.75
OPEN_STATE = "_ConnectorOpen"          # design setting: what the connectors changed at the air region
TRANSITION_SIG = "_TransitionSignature"   # design setting of a transition design
CASCADE_OF = "_ConnectorCascadeOf"     # design setting of a generated cascade circuit design
TRANSITION_LINE = "_TransitionLine"    # design setting of a transition design: the line to remove (JSON)
DIELECTRIC_ROLES = ("CblFront", "Bead", "CblRear", "RearIns", "LaunchDiel")


@dataclass
class Parts:
    """The objects and assignments of one connector."""
    body: list[str]                 # connector (coax, centre conductor, body, block, legs, wave port sheet)
    footprint: list[str]            # pad, taper, coplanar grounds, vias
    keepout: str                    # tool that cuts the board conductors (never in the model)
    cuts: list[str]                 # board conductors that got the keepout subtracted
    wave_port: str
    footprint_boundary: str
    variables: list[str]
    warnings: list[str] = field(default_factory=list)


def _unique_prefix(design, base: str) -> str:
    names = {*design.variables, *design.all_names(), *(c.name for c in design.connectors)}
    k = 1
    stem = base.rstrip("0123456789") or base
    while True:
        nm = base if k == 1 else f"{stem}{k}"
        if not any(x == nm or x.startswith(nm + "_") for x in names):
            return nm
        k += 1


def _bbox_hits(bb, lo, hi, tol=1e-6) -> bool:
    return all(bb[i] <= hi[i] + tol and bb[i + 3] >= lo[i] - tol for i in range(3))


def _conductor_boundaries(design):
    return [b for b in design.boundaries if b.kind in ("Perfect E", "Finite Conductivity")]


def _place_parts(design, p: dict, L: Launch, fp: Footprint, place: EdgePlacement, name: str,
                 bboxes: dict[str, tuple] | None, port_number: int, port_name: str, z_ref: str = "50ohm",
                 position_vars: bool = True) -> Parts:
    """Draws the connector ``p`` at ``place``: coax with bead and wave port, body, block or legs, and the launch
    footprint. Conductor sheets of the board in the board plane whose bounding box (``bboxes``, mm) reaches into
    the footprint get the footprint area cut out (a Subtract of the keepout box)."""
    from .design import Assignment, Command
    from . import schema
    warns = []
    var = [f"{name}_{c}" for c in "xyz"]
    if position_vars:
        for v, c in zip(var, place.point):
            design.variables[v] = f"{c + 0.0:.6g}mm"
    V = lambda k: f"{name}_{k}"  # noqa: E731
    design.variables.update({V("wp"): f"{fp.wp:g}mm", V("lp"): f"{fp.lp:g}mm", V("gl"): f"{fp.gl:g}mm",
                             V("lt"): f"{fp.lt:g}mm", V("gc"): f"{fp.gc:g}mm", V("lf"): f"{fp.lf:g}mm",
                             V("h"): f"{place.board_h:g}mm"})
    tw = place.trace_w or fp.wp
    F = _Frame(place, var)
    body, foot = [], []

    def add(kind, nm, material="vacuum", color=None, into=body, **props):
        o = design.add_object(kind, name=design.unique_name(f"{name}_{nm}"), material=material, **props)
        o.group = name
        if color:
            o.color = color
        into.append(o)
        return o

    zax = L.z_axis
    b, a = L.b, L.a
    s_rear = L.wall_est if L.template == "flange_block" else 0.0
    s_if = s_rear + L.coax_len_est                  # mating plane (wave port)
    zb0, zb1 = s_if - L.bead_z_est - L.bead_len_est / 2, s_if - L.bead_z_est + L.bead_len_est / 2
    coax = []
    rear_ins = L.template == "legs_tab" and L.interface != "SMA"
    s_ins = s_rear + (0.9 if rear_ins else 0.0)
    coax.append(add("CreateCylinder", "CblFront", L.coax_material, "#e6f2ff", **F.cyl(zb1, s_if, b / 2, zax)))
    coax.append(add("CreateCylinder", "Bead", L.bead_material, "#c8732a", **F.cyl(zb0, zb1, b / 2, zax)))
    coax.append(add("CreateCylinder", "CblRear", L.coax_material, "#e6f2ff", **F.cyl(s_ins, zb0, b / 2, zax)))
    if rear_ins:
        coax.append(add("CreateCylinder", "RearIns", "Teflon (tm)", "#f4f4f4", **F.cyl(s_rear, s_ins, b / 2, zax)))
    pins = [add("CreateCylinder", "Pin", PIN_MATERIAL, "#e5c100", **F.cyl(zb1, s_if, a / 2, zax)),
            add("CreateCylinder", "PinNeck", PIN_MATERIAL, "#e5c100", **F.cyl(zb0, zb1, L.neck / 2, zax))]
    if L.template == "flange_block":
        pins.append(add("CreateCylinder", "PinRear", PIN_MATERIAL, "#e5c100",
                        **F.cyl(s_rear + 0.3, zb0, a / 2, zax)))
        pins.append(add("CreateCone", "PinTaper", PIN_MATERIAL, "#e5c100",
                        **F.cone(s_rear, s_rear + 0.3, L.launch_pin / 2, a / 2, zax)))
        coax.append(add("CreateCylinder", "LaunchDiel", "Teflon (tm)", "#f4f4f4",
                        **F.cyl(0.0, s_rear, L.launch_diel / 2, zax)))
        pins.append(add("CreateCylinder", "LaunchPin", PIN_MATERIAL, "#e5c100",
                        **F.cyl(-L.over_est, s_rear, L.launch_pin / 2, zax)))
    else:
        pins.append(add("CreateCylinder", "PinRear", PIN_MATERIAL, "#e5c100", **F.cyl(s_rear, zb0, a / 2, zax)))
        pins.append(add("CreateBox", "Tab", PIN_MATERIAL, "#e5c100",
                        **F.box(-L.tab_len_est, 0.0, -L.tab_w_est / 2, L.tab_w_est / 2, 0.0, L.tab_t_est)))
    for o in pins:
        o.solve_inside = False
    tools = ",".join(o.name for o in coax)
    # ---- body / block ---------------------------------------------------------------------------------------
    r_body = L.thread_d / 2 - 0.6
    if L.template == "flange_block":
        Wb, Lb, Ht, Hb = L.block_w_est, L.block_len_est, L.block_top_est, L.block_bot_est
        h = place.board_h
        hc = max(0.4, 2.5 * h)
        blk = add("CreateBox", "Block", "brass", "#b9b9b0", **F.box(-Lb, s_rear, -Wb / 2, Wb / 2, 0.0, Ht))
        low = add("CreateBox", "BlockWall", "brass", "#b9b9b0",
                  **F.box(0.0, s_rear, -Wb / 2, Wb / 2, -h - Hb, 0.0))
        bot = add("CreateBox", "BlockPlate", "brass", "#b9b9b0", **F.box(-Lb, 0.0, -Wb / 2, Wb / 2, -h - Hb, -h))
        cw = f"max({V('wp')}/2+{V('gl')}, {tw / 2:g}mm+{V('gc')})+0.15mm"
        ch = add("CreateBox", "Channel", "vacuum", "#e6f2ff", **F.box(-(Lb + 0.1), 0.0, f"-({cw})", cw, 0.0, hc))
        blk.history.append(Command.new("Unite", Tools=f"{low.name},{bot.name}"))
        low.consumed_by = bot.consumed_by = blk.name
        blk.history.append(Command.new("Subtract", Tools=f"{ch.name},{name}_LaunchDiel", KeepOriginals="True"))
        blk.solve_inside = False
        housing = add("CreateBox", "Housing", L.body_material, "#9aa3aa",
                      **F.box(s_rear, s_rear + L.flange_t_est, -Wb / 2, Wb / 2, zax - L.flange_h_est / 2,
                              zax + L.flange_h_est / 2))
        barrel = add("CreateCylinder", "Barrel", L.body_material, "#9aa3aa",
                     **F.cyl(s_rear + L.flange_t_est, s_if, r_body, zax))
    else:
        W = max(L.body_w_est, 2 * (zax + place.board_h + L.leg_t_est) + 1.0)
        housing = add("CreateBox", "Housing", L.body_material, "#c9ae5d",
                      **F.box(0.0, L.body_len_est, -W / 2, W / 2, zax - W / 2, zax + W / 2))
        barrel = add("CreateCylinder", "Barrel", L.body_material, "#c9ae5d", **F.cyl(L.body_len_est, s_if, r_body, zax))
        for sd, x in (("L", -L.leg_x_est), ("R", L.leg_x_est)):
            o = add("CreateBox", f"Leg{sd}", "gold", "#e5c100",
                    **F.box(-L.leg_len_est, 0.0, x - L.leg_w_est / 2, x + L.leg_w_est / 2, 0.0, L.leg_t_est))
            o.solve_inside = False
            o = add("CreateBox", f"LegB{sd}", "gold", "#e5c100",
                    **F.box(-L.leg_len_est, 0.0, x - L.leg_w_est / 2, x + L.leg_w_est / 2,
                            -place.board_h - L.leg_t_est, -place.board_h))
            o.solve_inside = False
    housing.history.append(Command.new("Unite", Tools=barrel.name))
    barrel.consumed_by = housing.name
    housing.history.append(Command.new("Subtract", Tools=tools, KeepOriginals="True"))
    housing.solve_inside = False
    # ---- footprint --------------------------------------------------------------------------------------------
    gin_l = f"{V('wp')}/2+{V('gl')}"                       # inner edge of the ground at the launch
    gin_c = f"{tw / 2:g}mm+{V('gc')}"                      # ... along the trace
    half = f"{fp.half:g}mm"
    s_tp = f"{V('lp')}+{V('lt')}"                          # end of the taper
    keep = add("CreateBox", "Keepout", "vacuum", None, into=[],
               **F.box(f"-{V('lf')}", 0.0, f"-{half}", half, -0.05, 0.05))
    keep.model = False
    keep.visible = False
    cuts = []
    if bboxes:
        lo, hi = [0.0] * 3, [0.0] * 3
        pt = place.point
        lo[place.u], hi[place.u] = sorted((pt[place.u], pt[place.u] - place.sign * fp.lf))
        lo[place.lat], hi[place.lat] = pt[place.lat] - fp.half, pt[place.lat] + fp.half
        lo[place.normal], hi[place.normal] = pt[place.normal] - 1e-3, pt[place.normal] + 1e-3
        conductors = {o for bd in _conductor_boundaries(design) for o in bd.objects}
        for o in design.live_objects():
            if o.group == name or o.name not in bboxes or o.dim != 2 or o.name not in conductors:
                continue
            bb = bboxes[o.name]
            if abs(bb[place.normal] - bb[place.normal + 3]) < 1e-6 and _bbox_hits(bb, lo, hi):
                o.history.append(Command.new("Subtract", Tools=keep.name, KeepOriginals="True"))
                cuts.append(o.name)
    if not cuts:
        warns.append("no board conductor reaches into the footprint: the trace is not cut to the launch")
    sig = [add("CreateRectangle", "Pad", "vacuum", "#d4a017", into=foot,
               **F.rect(f"-({V('lp')})", 0.0, f"-{V('wp')}/2", f"{V('wp')}/2"))]
    if fp.lt > 0:
        sig.append(add("CreatePolyline", "Taper", "vacuum", "#d4a017", into=foot, **F.polygon([
            (f"-({V('lp')})", f"-{V('wp')}/2"), (f"-({s_tp})", f"-{tw / 2:g}mm"),
            (f"-({s_tp})", f"{tw / 2:g}mm"), (f"-({V('lp')})", f"{V('wp')}/2")])))
    sig.append(add("CreateRectangle", "Line", "vacuum", "#d4a017", into=foot,
                   **F.rect(f"-{V('lf')}", f"-({s_tp})", f"-{tw / 2:g}mm", f"{tw / 2:g}mm")))
    gnd = []
    for sd, k in (("L", -1), ("R", 1)):
        sg = "-" if k < 0 else ""
        pts = [("0mm", f"{sg}({gin_l})"), ("0mm", f"{sg}{half}"), (f"-{V('lf')}", f"{sg}{half}"),
               (f"-{V('lf')}", f"{sg}({gin_c})"), (f"-({s_tp})", f"{sg}({gin_c})")]
        if fp.lt > 0:
            pts.append((f"-({V('lp')})", f"{sg}({gin_l})"))
        else:
            pts.append((f"-({s_tp})", f"{sg}({gin_l})"))
        gnd.append(add("CreatePolyline", f"Gnd{sd}", "vacuum", "#d4a017", into=foot, **F.polygon(pts)))
    # via fence along the gaps and a grid in the pour, to the end of the footprint
    t0 = f"max({gin_l}, {gin_c})+{0.15 + fp.via_d / 2:g}mm"
    n_s = max(1, int((fp.lf - fp.via_first - fp.via_d / 2 - 0.1) // fp.via_pitch) + 1)
    for j in range(fp.rows):
        for i in range(n_s):
            sv = -(fp.via_first + i * fp.via_pitch)
            for sd, sg in (("L", "-"), ("R", "")):
                o = add("CreateCylinder", f"Via{sd}{j + 1}_{i + 1}", "pec", "#9a9a9a", into=foot,
                        **F.via(sv, f"{sg}({t0}+{j * fp.via_pitch:g}mm)", fp.via_d / 2, V("h")))
                o.solve_inside = False
    # the footprint conducts like the trace it replaces, in a boundary of its own (switched with the mode)
    like = next((bd for bd in _conductor_boundaries(design) if any(c in bd.objects for c in cuts)), None)
    fb = Assignment(design.unique_name(f"{name}_Footprint"), like.kind if like else "Perfect E",
                    dict(like.props) if like else {}, objects=[o.name for o in sig + gnd])
    design.boundaries.append(fb)
    # ---- wave port on the mating plane ------------------------------------------------------------------------
    sheet = add("CreateCircle", "Port", "vacuum", "#8080ff", **F.circle(s_if, b / 2, zax))
    pr = schema.defaults(schema.EXCITATIONS["Wave Port"])
    pr.update({"PortNumber": str(port_number), "Renormalize": "True", "RenormImpedance": z_ref})
    wp_ = Assignment(design.unique_name(port_name), "Wave Port", pr, objects=[sheet.name])
    design.excitations.append(wp_)
    if L.template == "legs_tab" and L.interface != "SMA" and place.board_h > 0.8:
        warns.append(f"{L.interface} with legs on a {place.board_h:g} mm board: the generic tab launch is good to "
                     "about 20 GHz; for 40/50 GHz use the vendor footprint or a thin board")
    pmax = [x for x in (p.get("pcb_thickness_mm") or []) if x]
    if pmax and place.board_h > max(pmax) + 0.05:
        warns.append(f"board {place.board_h:g} mm is thicker than the part allows ({max(pmax):g} mm)")
    return Parts([o.name for o in body], [o.name for o in foot], keep.name, cuts, wp_.name, fb.name,
                 ([*var] if position_vars else []) + [V(k) for k in ("wp", "lp", "gl", "lt", "gc", "lf", "h")], warns)


# ------------------------------------------------------------------------------------------------------------
# connector at a lumped port
# ------------------------------------------------------------------------------------------------------------
def _csv(text: str) -> list[str]:
    return [x for x in (text or "").split(",") if x]


def instance(design, name: str):
    for c in design.connectors:
        if c.name == name:
            return c
    raise KeyError(f"No connector '{name}' in {design.name}")


def connector_at(design, port: str):
    """The connector standing at the lumped port ``port`` (None if there is none)."""
    return next((c for c in design.connectors if c.port == port), None)


def placement(inst) -> EdgePlacement:
    q = inst.props
    return EdgePlacement(tuple(float(x) for x in q["Point"].split(",")), q["Direction"], int(q["Normal"]),
                         float(q["BoardH"]), float(q.get("TraceW", "0") or 0), float(q.get("Er", "3.55") or 3.55))


def parts_of(inst) -> Parts:
    q = inst.props
    return Parts(_csv(q.get("Body")), _csv(q.get("Footprint")), q.get("Keepout", ""), _csv(q.get("Cuts")),
                 q.get("WavePort", ""), q.get("FootprintBoundary", ""), _csv(q.get("Variables")))


def geometry_bboxes(project, design) -> dict[str, tuple]:
    """Bounding boxes (mm) of the live objects of the design, built with the geometry kernel."""
    import gmsh
    from ..kernel import occ as K
    K.ensure_gmsh("hertzon_connectors")
    gmsh.model.remove()
    gmsh.model.add("hertzon_connectors")
    ctx = K.build_design(design, design.evaluator(project))
    out = {}
    pad = 1e-4                  # the OCC bounding boxes are enlarged by the tolerance (1e-7 m)
    for n, dts in ctx.built.items():
        if dts:
            b = [float(x) * 1e3 for x in K._bbox(dts)]
            for i in range(3):
                if b[i + 3] - b[i] > 2.5 * pad:
                    b[i], b[i + 3] = b[i] + pad, b[i + 3] - pad
                else:
                    b[i] = b[i + 3] = (b[i] + b[i + 3]) / 2
            out[n] = tuple(round(x, 6) for x in b)
    gmsh.model.remove()
    return out


def bboxes_from_tess(tess: dict) -> dict[str, tuple]:
    """Bounding boxes (mm) from the display tessellation ({name: Tess}, metres)."""
    return {n: tuple(float(x) * 1e3 for x in t.bbox) for n, t in tess.items()}


def port_placement(project, design, port: str, bboxes: dict[str, tuple], er: float | None = None
                   ) -> tuple[EdgePlacement, list[str], str]:
    """Where a connector goes for the lumped port ``port`` at a board edge: the port sheet gives the edge plane,
    the trace end (top of the sheet), the board thickness (the integration line) and the trace width; the
    substrate (the largest solid that ends at the sheet) gives the outward direction and its permittivity.
    Returns (placement, warnings, substrate material)."""
    a = design.assignment(port)
    if a.kind != "Lumped Port":
        raise ValueError(f"'{port}' is not a lumped port")
    sheets = list(a.objects) + [f.obj for f in a.faces if f.face < 0]
    if not sheets or sheets[0] not in bboxes:
        raise ValueError(f"'{port}' has to be a lumped port on a sheet at the board edge (a rectangle from the "
                         "ground to the trace)")
    bb = bboxes[sheets[0]]
    ext = [bb[i + 3] - bb[i] for i in range(3)]
    u = min(range(3), key=lambda i: ext[i])
    if ext[u] > 1e-6:
        raise ValueError(f"'{port}': its sheet is not plane along an axis")
    ev = design.evaluator(project)
    normal = None
    try:
        line = ev.points(a.props.get("IntegrationLine", ""))
    except Exception:  # noqa: BLE001
        line = []
    if len(line) == 2:
        d = [abs(line[1][i] - line[0][i]) for i in range(3)]
        normal = max(range(3), key=lambda i: d[i])
    if normal is None or normal == u:
        normal = min((i for i in range(3) if i != u), key=lambda i: ext[i])
    h = ext[normal]
    lat = 3 - u - normal
    u0 = (bb[u] + bb[u + 3]) / 2
    centre = [0.0] * 3
    warns = []
    best, sign = None, -1
    for o in design.live_objects():
        if o.name in sheets or o.name not in bboxes or o.dim != 3 or o.history[0].kind == "CreateRegion" \
                or not o.model or o.group in {c.name for c in design.connectors}:
            continue
        ob = bboxes[o.name]
        if ob[normal] > bb[normal] + 1e-6 or ob[normal + 3] < bb[normal + 3] - 1e-6:
            continue
        if not (ob[lat] - 1e-6 <= (bb[lat] + bb[lat + 3]) / 2 <= ob[lat + 3] + 1e-6):
            continue
        if abs(ob[u + 3] - u0) < 1e-4:
            cand = +1
        elif abs(ob[u] - u0) < 1e-4:
            cand = -1
        else:
            continue
        vol = (ob[3] - ob[0]) * (ob[4] - ob[1]) * (ob[5] - ob[2])
        if best is None or vol > best[0]:
            best, sign = (vol, o), cand
    # the trace end lies on the top of the substrate, i.e. the side of the sheet away from the ground
    top_high = True
    if best is not None:
        sub = bboxes[best[1].name]
        top_high = abs(sub[normal + 3] - bb[normal + 3]) <= abs(sub[normal] - bb[normal])
    else:
        warns.append(f"'{port}': no substrate ends at the port sheet; the connector points to "
                     f"{'-' if sign < 0 else '+'}{'XYZ'[u]}, check it")
    if not top_high:
        raise ValueError(f"'{port}': the trace lies below the substrate; boards seen from below are not supported")
    centre[u], centre[lat], centre[normal] = u0, (bb[lat] + bb[lat + 3]) / 2, bb[normal + 3]
    material = best[1].material if best is not None else ""
    if er is None:
        er = 3.55
        if material:
            try:
                er = float(ev.value(project.material(material).permittivity))
            except Exception:  # noqa: BLE001
                warns.append(f"permittivity of '{material}' not readable, 3.55 assumed")
    # trace width: the conductor sheet that ends at the port sheet on its top edge, else the sheet width
    tw = ext[lat]
    conductors = {o for bd in _conductor_boundaries(design) for o in bd.objects}
    for o in design.live_objects():
        if o.name in conductors and o.name in bboxes and o.dim == 2:
            ob = bboxes[o.name]
            if abs(ob[normal] - centre[normal]) < 1e-6 and abs(ob[normal + 3] - centre[normal]) < 1e-6 \
                    and (abs(ob[u] - u0) < 1e-4 or abs(ob[u + 3] - u0) < 1e-4) \
                    and ob[lat] - 1e-6 <= centre[lat] <= ob[lat + 3] + 1e-6:
                w = ob[lat + 3] - ob[lat]
                if w < 5 * max(tw, h):
                    tw = w
                break
    place = EdgePlacement(tuple(round(x, 6) for x in centre), ("+" if sign > 0 else "-") + "XYZ"[u], normal,
                          round(h, 4), round(tw, 4), er)
    return place, warns, material


def attach_connector(project, design, port: str, pid: str, bboxes: dict[str, tuple] | None = None,
                     name: str = "J1", mode: str = "Ideal", er: float | None = None):
    """Puts connector ``pid`` at the lumped port ``port`` (at a board edge) and returns the ConnectorInstance.
    Nothing of the design is removed: in mode Ideal the connector is a transparent preview outside the model and
    the lumped port is solved as before; ``set_mode`` switches to the transition model or the full 3D connector
    and back."""
    from .design import ConnectorInstance
    if connector_at(design, port) is not None:
        raise ValueError(f"'{port}' already has the connector '{connector_at(design, port).name}'")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if bboxes is None:
        bboxes = geometry_bboxes(project, design)
    place, warns, material = port_placement(project, design, port, bboxes, er)
    p = part(pid)
    L = launch_for(p, place.board_h)
    fp = footprint_for(L, place)
    lump = design.assignment(port)
    name = _unique_prefix(design, name or "J1")
    number = int(float(lump.props.get("PortNumber", "0") or 0)) or design.next_port_number()
    parts_ = _place_parts(design, p, L, fp, place, name, bboxes, number, f"{port}_{name}",
                          lump.props.get("Impedance", "50ohm") or "50ohm")
    sheets = list(lump.objects) + [f.obj for f in lump.faces if f.face < 0]
    inst = ConnectorInstance(name, p["id"], port, "Ideal", {
        "Point": ",".join(f"{x:.6g}" for x in place.point), "Direction": place.direction,
        "Normal": str(place.normal), "BoardH": f"{place.board_h:g}", "TraceW": f"{place.trace_w:g}",
        "Er": f"{place.er:g}", "Substrate": material, "PortSheet": sheets[0] if sheets else "",
        "Body": ",".join(parts_.body), "Footprint": ",".join(parts_.footprint), "Keepout": parts_.keepout,
        "Cuts": ",".join(parts_.cuts), "WavePort": parts_.wave_port, "FootprintBoundary": parts_.footprint_boundary,
        "Variables": ",".join(parts_.variables), "Vias": f"{fp.via_d:g},{fp.via_pitch:g},{fp.via_first:g},{fp.rows}",
        "GroundHalf": f"{fp.half:g}", "Launch": ",".join(L.notes)})
    design.connectors.append(inst)
    inst.mode = mode
    warns += L.notes + parts_.warnings
    warns += apply_modes(project, design)
    inst.props["Warnings"] = " | ".join(warns)
    return inst


def _side_key(place: EdgePlacement) -> str:
    return ("+" if place.sign > 0 else "-") + "XYZ"[place.u]


def _apply_instance(design, inst) -> list[str]:
    """Switches the objects and assignments of one connector to its mode."""
    on = inst.mode == "3D"
    pa = parts_of(inst)
    warns = []
    for nm in pa.body + pa.footprint:
        if not design.has_obj(nm):
            warns.append(f"{inst.name}: object '{nm}' is missing")
            continue
        o = design.obj(nm)
        o.model = on
        role = nm[len(inst.name) + 1:]
        o.transparency = (0.6 if role in DIELECTRIC_ROLES or role == "Port" else 0.0) if on else PREVIEW_TRANSPARENCY
    if pa.keepout and design.has_obj(pa.keepout):
        design.obj(pa.keepout).model = False
    for nm in pa.cuts:
        if design.has_obj(nm):
            for c in design.obj(nm).history[1:]:
                if c.kind == "Subtract" and c.props.get("Tools") == pa.keepout:
                    c.suppressed = not on
    lump = next((e for e in design.excitations if e.name == inst.port), None)
    wave = next((e for e in design.excitations if e.name == pa.wave_port), None)
    if lump is None:
        warns.append(f"{inst.name}: the lumped port '{inst.port}' is missing")
    if wave is not None:
        wave.enabled = on
        if lump is not None:
            wave.props["PortNumber"] = lump.props.get("PortNumber", wave.props.get("PortNumber", "1"))
            wave.props["Renormalize"] = "True"
            wave.props["RenormImpedance"] = lump.props.get("Impedance", "50ohm") or "50ohm"
    if lump is not None:
        lump.enabled = not on
    sheet = inst.props.get("PortSheet", "")
    if sheet and design.has_obj(sheet):
        others = [a for a in design.boundaries + design.excitations if a.name != inst.port and a.enabled
                  and (sheet in a.objects or any(f.obj == sheet for f in a.faces))]
        if not others:
            design.obj(sheet).model = not on
    for b in design.boundaries:
        if b.name == pa.footprint_boundary:
            b.enabled = on
    return warns


def apply_modes(project, design) -> list[str]:
    """Brings the design in line with the modes of its connectors (idempotent)."""
    warns = []
    for inst in design.connectors:
        warns += _apply_instance(design, inst)
    warns += _sync_open(project, design)
    return warns


def set_mode(project, design, mode: str, names: list[str] | None = None) -> list[str]:
    """Sets the mode of the connectors ``names`` (all: None). Mode Transition also creates or updates the
    transition designs and the cascade circuit design. Returns warnings."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    sel = [c for c in design.connectors if names is None or c.name in names]
    if names is not None and len(sel) != len(set(names)):
        missing = set(names) - {c.name for c in sel}
        raise KeyError(f"No connector {', '.join(sorted(missing))} in {design.name}")
    for c in sel:
        c.mode = mode
    warns = apply_modes(project, design)
    if any(c.mode == "Transition" for c in design.connectors):
        for c in design.connectors:
            if c.mode == "Transition":
                ensure_transition(project, design, c)
        build_cascade(project, design)
    return warns


# ------------------------------------------------------------------------------------------------------------
# air region: the wave port of a 3D connector lies on the outer boundary
# ------------------------------------------------------------------------------------------------------------
def _lowest_frequency(design, ev) -> float:
    fs = []
    for st in design.setups:
        try:
            fs.append(ev.freq(st.props.get("Frequency", "") or "1GHz"))
        except Exception:  # noqa: BLE001
            pass
        for sw in st.sweeps:
            if sw.props.get("Enabled", "True") != "True":
                continue
            try:
                from ..solver.sweeps import sweep_frequencies
                f = sweep_frequencies(sw.props, ev)
                if len(f):
                    fs.append(float(min(f)))
            except Exception:  # noqa: BLE001
                pass
    fs = [f for f in fs if f > 0]
    return min(fs) if fs else 1e9


def _sync_open(project, design) -> list[str]:
    """A wave port has to lie on the outer boundary. For every side with a 3D connector: a region object gets no
    padding there and its open boundary (Radiation, PML) leaves that face (Perfect E, like the connector body);
    with Auto Open, an explicit region with the same padding and a Radiation boundary takes its place. What was
    changed is kept in a design setting and restored as soon as no 3D connector needs it."""
    from .design import Assignment, FaceRef
    from . import schema
    state = json.loads(design.settings.get(OPEN_STATE, "") or "{}")
    # ---- restore
    for reg, pads in state.get("pads", {}).items():
        if design.has_obj(reg):
            design.obj(reg).history[0].props.update(pads)
    for bname, sel in state.get("faces", {}).items():
        b = next((x for x in design.boundaries if x.name == bname), None)
        if b is not None:
            b.objects = list(sel["objects"])
            b.faces = [FaceRef(f[0], int(f[1])) for f in sel["faces"]]
    auto = state.get("auto")
    if auto:
        design.objects = [o for o in design.objects if o.name != auto["region"]]
        design.boundaries = [b for b in design.boundaries if b.name != auto["boundary"]]
        design.settings["AutoOpen"] = auto["AutoOpen"]
    flat = list(state.get("flat", []))
    design.settings.pop(OPEN_STATE, None)
    sides = sorted({_side_key(placement(c)) for c in design.connectors if c.mode == "3D"} | set(flat))
    if not sides:
        return []
    new = {"flat": flat, "pads": {}, "faces": {}}
    warns = []
    idx = {k: 2 * "XYZ".index(k[1]) + (1 if k[0] == "+" else 0) for k in sides}
    regions = [o for o in design.live_objects() if o.history[0].kind == "CreateRegion"]
    if regions:
        for r in regions:
            props = r.history[0].props
            new["pads"][r.name] = {k: props.get(k, "0") for k in sides}
            for k in sides:
                props[k] = "0"
            for b in design.boundaries:
                if not b.enabled or b.kind not in ("Radiation", "PML", "Layered Impedance", "Finite Conductivity",
                                                   "Perfect H", "Impedance"):
                    continue
                whole = r.name in b.objects
                listed = [f.face for f in b.faces if f.obj == r.name]
                if not whole and not listed:
                    continue
                new["faces"][b.name] = {"objects": list(b.objects), "faces": [[f.obj, f.face] for f in b.faces]}
                keep = set(range(6)) if (whole or -1 in listed) else set(listed)
                keep -= set(idx.values())
                b.objects = [o for o in b.objects if o != r.name]
                b.faces = [f for f in b.faces if f.obj != r.name] + [FaceRef(r.name, i) for i in sorted(keep)]
        warns.append(f"air region '{regions[0].name}': no padding toward the 3D connector(s) ({', '.join(sides)}), "
                     "the open boundary leaves that face")
    elif design.settings.get("AutoOpen", "Off") != "Off":
        ev = design.evaluator(project)
        mode = design.settings["AutoOpen"]
        if mode == "Auto Open - Filter":
            margin = ev.length(design.settings.get("FilterAirMargin", "12mm") or "12mm")
        else:
            pad = design.settings.get("AutoOpenPadding", "")
            margin = ev.length(pad) if pad.strip() else C0_MM_GHZ * 1e6 / _lowest_frequency(design, ev) / 3 / 1e3
        pr = {"PaddingType": "Absolute Offset"}
        for ax in "XYZ":
            for sgn in "+-":
                pr[sgn + ax] = "0" if sgn + ax in sides else f"{margin * 1e3:.6g}mm"
        reg = design.add_object("CreateRegion", name=design.unique_name("ConnectorRegion"), **pr)
        rad = Assignment(design.unique_name("ConnectorRad"), "Radiation", schema.defaults(schema.BOUNDARIES["Radiation"]),
                         faces=[FaceRef(reg.name, i) for i in range(6) if i not in idx.values()])
        design.boundaries.append(rad)
        new["auto"] = {"AutoOpen": mode, "region": reg.name, "boundary": rad.name}
        design.settings["AutoOpen"] = "Off"
        warns.append(f"Auto Open: replaced by the region '{reg.name}' (padding {margin * 1e3:.3g} mm, none toward "
                     f"the 3D connector) with a Radiation boundary while a connector is solved in 3D")
    design.settings[OPEN_STATE] = json.dumps(new)
    return warns


# ------------------------------------------------------------------------------------------------------------
# flatten / remove
# ------------------------------------------------------------------------------------------------------------
def flatten(project, design, name: str) -> list[str]:
    """Makes connector ``name`` plain geometry of the design: it is solved in 3D from now on, the lumped port
    and its sheet are removed. The only mode change that is not switched back (undo is)."""
    inst = instance(design, name)
    inst.mode = "3D"
    warns = apply_modes(project, design)
    side = _side_key(placement(inst))
    state = json.loads(design.settings.get(OPEN_STATE, "") or "{}")
    state["flat"] = sorted(set(state.get("flat", [])) | {side})
    design.settings[OPEN_STATE] = json.dumps(state)
    sheet = inst.props.get("PortSheet", "")
    design.excitations = [e for e in design.excitations if e.name != inst.port]
    used = {o for a in design.excitations + design.boundaries for o in a.objects} | \
           {f.obj for a in design.excitations + design.boundaries for f in a.faces}
    if sheet and sheet not in used:
        design.objects = [o for o in design.objects if o.name != sheet]
    design.connectors = [c for c in design.connectors if c.name != name]
    return warns + apply_modes(project, design)


def remove_connector(project, design, name: str) -> None:
    """Removes connector ``name`` with all its objects, cuts, assignments and variables; the lumped port is
    solved as before."""
    inst = instance(design, name)
    inst.mode = "Ideal"
    apply_modes(project, design)
    pa = parts_of(inst)
    gone = set(pa.body + pa.footprint + [pa.keepout])
    for nm in pa.cuts:
        if design.has_obj(nm):
            o = design.obj(nm)
            o.history = [c for c in o.history if not (c.kind == "Subtract" and c.props.get("Tools") == pa.keepout)]
    design.objects = [o for o in design.objects if o.name not in gone]
    design.boundaries = [b for b in design.boundaries if b.name != pa.footprint_boundary]
    design.excitations = [e for e in design.excitations if e.name != pa.wave_port]
    for v in pa.variables:
        design.variables.pop(v, None)
    design.connectors = [c for c in design.connectors if c.name != name]
    apply_modes(project, design)


# ------------------------------------------------------------------------------------------------------------
# transition model and cascade
# ------------------------------------------------------------------------------------------------------------
FOOTPRINT_VARS = ("wp", "lp", "gl", "lt", "gc", "lf")


def _footprint_values(design, inst) -> dict[str, float]:
    ev = design.evaluator()
    out = {}
    for k in FOOTPRINT_VARS:
        try:
            out[k] = round(ev.length(f"{inst.name}_{k}") * 1e3, 6)
        except Exception:  # noqa: BLE001
            out[k] = float("nan")
    return out


def _sweeps_of(design) -> list:
    return [(st, sw) for st in design.setups for sw in st.sweeps if sw.props.get("Enabled", "True") == "True"]


def _frequency_span(design) -> tuple[float, float]:
    """(lowest, highest) frequency of the enabled sweeps (GHz); (0, 0) without sweeps."""
    ev = design.evaluator()
    lo, hi = float("inf"), 0.0
    for st, sw in _sweeps_of(design):
        try:
            from ..solver.sweeps import sweep_frequencies
            f = sweep_frequencies(sw.props, ev)
            lo, hi = min(lo, float(min(f)) / 1e9), max(hi, float(max(f)) / 1e9)
        except Exception:  # noqa: BLE001
            pass
    return (lo, hi) if hi > 0 else (0.0, 0.0)


def transition_signature(project, design, inst) -> str:
    """What the transition model of a connector depends on: part, board, line, footprint, conductor and the
    frequency span of the design."""
    pl = placement(inst)
    like = next((b for b in _conductor_boundaries(design) if b.name == inst.props.get("FootprintBoundary")), None)
    lo, hi = _frequency_span(design)
    lump = next((e for e in design.excitations if e.name == inst.port), None)
    return json.dumps({"part": inst.part, "h": pl.board_h, "w": pl.trace_w, "er": pl.er,
                       "substrate": inst.props.get("Substrate", ""), "fp": _footprint_values(design, inst),
                       "metal": [like.kind, sorted(like.props.items())] if like else [],
                       "z": lump.props.get("Impedance", "50ohm") if lump else "50ohm",
                       "f": [round(lo, 6), round(hi, 6)]}, sort_keys=True)


def transition_state(project, design, inst) -> str:
    """'missing', 'stale' (the board, footprint or frequencies changed since it was made) or 'current'."""
    name = inst.props.get("Transition", "")
    try:
        t = project.design(name)
    except KeyError:
        return "missing"
    return "current" if t.settings.get(TRANSITION_SIG) == transition_signature(project, design, inst) else "stale"


def _tand(project, material: str) -> float:
    try:
        return float(project.material(material).loss_tangent)
    except Exception:  # noqa: BLE001
        return 0.0


def _cond(project, boundary) -> float:
    """Conductivity of the trace (S/m): of its Finite Conductivity boundary, a Perfect E trace ~ lossless."""
    if boundary is None or boundary.kind != "Finite Conductivity":
        return 1e12
    p = boundary.props
    try:
        if p.get("UseMaterial", "True") == "True":
            return float(project.material(p.get("Material", "copper")).conductivity)
        return float(p.get("Conductivity", "58000000"))
    except Exception:  # noqa: BLE001
        return 5.8e7


def ensure_transition(project, design, inst):
    """The 3D model of the connector-to-board transition of ``inst``: the connector with its footprint on a piece
    of the board, a wave port on the coax and one on the line, de-embedded to the board edge (the plane of the
    lumped port). Connectors with the same part, board, line and footprint share one transition design. It is
    rebuilt when stale."""
    from .design import Assignment, Design, Setup, Sweep
    from . import schema
    sig = transition_signature(project, design, inst)
    for d in project.designs:
        if d.kind == "3d" and d.settings.get(TRANSITION_SIG) == sig:
            inst.props["Transition"] = d.name
            return d
    old = inst.props.get("Transition", "")
    shared = any(c is not inst and c.props.get("Transition") == old for d in project.designs for c in d.connectors)
    p = part(inst.part)
    pl = placement(inst)
    if old and not shared and any(d.name == old for d in project.designs):
        t = project.design(old)
        project.designs.remove(t)
        name = old
    else:
        taken = {d.name for d in project.designs}
        base = f"Transition_{p['part_number']}_{pl.board_h:g}mm".replace(" ", "").replace(".", "p")
        name, k = base, 2
        while name in taken:
            name, k = f"{base}_{k}", k + 1
    t = Design(name=name, units="mm")
    t.notes = (f"Transition of {label(p)} to a {pl.board_h:g} mm board ({inst.props.get('Substrate', '')}), trace "
               f"{pl.trace_w:g} mm. Made by Hertzon for the connector {inst.name} of {design.name}: port 1 the coax "
               "at the mating plane, port 2 the line, de-embedded to the board edge. Solve it once; the cascade "
               "circuit design uses it.")
    L = launch_for(p, pl.board_h)
    fpv = _footprint_values(design, inst)
    fp = footprint_for(L, pl)
    fp.wp, fp.lp, fp.gl, fp.lt, fp.gc, fp.lf = (fpv[k] for k in FOOTPRINT_VARS)
    via = inst.props.get("Vias", "")
    if via:
        d_, pitch, first, n = via.split(",")
        fp.via_d, fp.via_pitch, fp.via_first, fp.rows = float(d_), float(pitch), float(first), int(n)
    fp.half = float(inst.props.get("GroundHalf", fp.half))
    h, tw = pl.board_h, pl.trace_w or fp.wp
    width = L.block_w_est if L.template == "flange_block" else max(L.body_w_est, 2 * L.leg_x_est + L.leg_w_est)
    Wb = round(max(width + 2.0, tw + 20 * h, 2 * fp.half + 1.0), 3)
    Lb = round(fp.lf + max(2.0, 5 * h), 3)        # the footprint, then plain line to the port
    Hair = round(max(10 * h, (L.block_top_est + 1.0) if L.template == "flange_block" else 0.0,
                     (L.z_axis + L.body_w_est / 2 + 0.5) if L.template == "legs_tab" else 0.0, 2.0), 3)
    t.variables.update({"Wb": f"{Wb:g}mm", "Lb": f"{Lb:g}mm", "Hair": f"{Hair:g}mm", "h": f"{h:g}mm",
                        "w": f"{tw:g}mm"})
    sub_mat = inst.props.get("Substrate", "") or "Rogers RO4003C (tm)"
    t.add_object("CreateBox", "Substrate", sub_mat, Position="-Wb/2,0,-h", XSize="Wb", YSize="Lb", ZSize="h")
    t.add_object("CreateBox", "Air", "vacuum", Position="-Wb/2,0,0", XSize="Wb", YSize="Lb", ZSize="Hair")
    t.obj("Air").transparency = 0.9
    trace = t.add_object("CreateRectangle", "Trace", Position="-w/2,0,0", Axis="Z", XSize="w", YSize="Lb")
    like = next((b for b in _conductor_boundaries(design) if b.name == inst.props.get("FootprintBoundary")), None)
    t.boundaries.append(Assignment("Trace", like.kind if like else "Perfect E", dict(like.props) if like else {},
                                   objects=[trace.name]))
    place = EdgePlacement((0.0, 0.0, 0.0), "-Y", 2, h, tw, pl.er)
    bbs = {trace.name: (-tw / 2, 0.0, 0.0, tw / 2, Lb, 0.0)}
    lump = next((e for e in design.excitations if e.name == inst.port), None)
    z_ref = (lump.props.get("Impedance", "50ohm") if lump else "50ohm") or "50ohm"
    pa = _place_parts(t, p, L, fp, place, "J", bbs, 1, "Coax", z_ref, position_vars=False)
    for v in "xyz":
        t.variables[f"J_{v}"] = "0mm"
    # in this model the connector is solved: everything on, the cuts active
    for nm in pa.body + pa.footprint:
        t.obj(nm).model = True
    for b in t.boundaries:
        b.enabled = True
    # the line port at the far end, de-embedded to the board edge: a box of a few line widths around the line,
    # inside the Perfect E end face (its edges must not touch the radiation boundary: the mode would be lossy
    # and the de-embedding would amplify)
    wpt = round(min(Wb - 0.2, max(5 * tw, tw + 10 * h)), 3)
    hpt = round(min(Hair - 0.1, max(6 * h, 3 * tw)), 3)
    t.variables.update({"wpt": f"{wpt:g}mm", "hpt": f"{hpt:g}mm"})
    p2 = t.add_object("CreateRectangle", "LinePort", Position="-wpt/2,Lb,-h", Axis="Y", XSize="wpt", ZSize="h+hpt")
    pr = schema.defaults(schema.EXCITATIONS["Wave Port"])
    pr.update({"PortNumber": "2", "Renormalize": "True", "RenormImpedance": z_ref})
    t.excitations.append(Assignment("Board", "Wave Port", pr, objects=[p2.name]))
    # the cascade removes the line Lb (to the board edge) with a microstrip line of length -Lb: closed-form, with
    # dispersion and loss at every frequency (the wave-port de-embedding extrapolates the propagation constant
    # of a dispersive line badly below the solved frequencies)
    t.settings[TRANSITION_LINE] = json.dumps({"Lb": Lb, "w": tw, "h": h, "er": pl.er, "tand": _tand(project, sub_mat),
                                              "cond": _cond(project, like)})
    rads = [t.add_object("CreateRectangle", "RadTop", Position="-Wb/2,0,Hair", Axis="Z", XSize="Wb", YSize="Lb"),
            t.add_object("CreateRectangle", "RadSideL", Position="-Wb/2,0,-h", Axis="X", YSize="Lb", ZSize="h+Hair"),
            t.add_object("CreateRectangle", "RadSideR", Position="Wb/2,0,-h", Axis="X", YSize="Lb", ZSize="h+Hair")]
    t.boundaries.append(Assignment("Rad", "Radiation", schema.defaults(schema.BOUNDARIES["Radiation"]),
                                   objects=[o.name for o in rads]))
    lo, hi = _frequency_span(design)
    if hi <= 0:
        lo, hi = 0.5, L.f_max
    st = Setup("Setup1")
    st.props.update({"Frequency": f"{hi:g}GHz", "MaximumPasses": "12", "MaximumDeltaS": "0.02"})
    sw = Sweep("Sweep")
    n = int(max(21, min(401, round((hi - lo) * 4) + 1)))
    sw.props.update({"SweepType": "Interpolating", "Distribution": "Linear Count", "Start": f"{max(lo, 0.01):g}GHz",
                     "Stop": f"{hi:g}GHz", "Count": str(n)})
    st.sweeps.append(sw)
    t.setups.append(st)
    t.settings[TRANSITION_SIG] = sig
    project.designs.append(t)
    inst.props["Transition"] = t.name
    return t


def _transition_solution(project, name: str) -> str:
    try:
        sw = _sweeps_of(project.design(name))
    except KeyError:
        return ""
    return f"{sw[0][0].name} : {sw[0][1].name}" if sw else ""


def cascade_name(design) -> str:
    return f"{design.name}_Connectors"


def build_cascade(project, design):
    """The circuit design that puts the transition models in front of the lumped ports of ``design``: a 3D
    design block of ``design`` (its lumped port solution), at each port with a connector in mode Transition the
    block of its transition design (port 2 at the board edge), and a Power Port with the number of the port.
    Created or rebuilt in place (its setups are kept)."""
    from .design import Design, Setup, Sweep
    from . import circuit as CK
    name = cascade_name(design)
    c = next((d for d in project.designs if d.name == name), None)
    if c is None:
        c = Design(name=name, kind="circuit")
        project.designs.append(c)
    c.settings[CASCADE_OF] = design.name
    c.notes = (f"Made by Hertzon from the connectors of {design.name}: its lumped port solution with the solved "
               "transition models in front of the ports whose connector is in mode Transition. Rebuilt when the "
               "connectors change.")
    ports = design.ports()
    trans = {ci.port: ci for ci in design.connectors if ci.mode == "Transition"}
    comps, wires = [], []
    sw = _sweeps_of(design)
    main_sol = f"{sw[0][0].name} : {sw[0][1].name}" if sw else ""
    main = CK.Component("EM1", "EM", 0, 0, 0, {"Design": design.name, "Solution": main_sol, "Model": "Data",
                                              "CircuitModel": "", "Ports": str(len(ports)),
                                              "PortNames": ",".join(e.name for e in ports)})
    comps.append(main)
    pins = CK.pins(main)
    left_k = right_k = 0
    for k, (e, (px, py)) in enumerate(zip(ports, pins)):
        num = e.props.get("PortNumber", str(k + 1))
        side = -1 if px < 0 else 1
        j = left_k if side < 0 else right_k
        if side < 0:
            left_k += 1
        else:
            right_k += 1
        ci = trans.get(e.name)
        x_end, py_end = px, py
        if ci is not None:
            tname = ci.props.get("Transition", "")
            try:
                line = json.loads(project.design(tname).settings.get(TRANSITION_LINE, "") or "{}")
            except KeyError:
                line = {}
            x0 = px + side * 300 * j
            if j:
                wires.append(CK.Wire([[px, py], [x0, py]]))
            # de-embedding line (length -Lb) between the main block and the transition's board port
            if line:
                sub = CK.Component(f"Sub_{ci.name}", "SUBST", x0 + side * 60, py - 60 - 40 * (j % 2), 0,
                                   {"Er": f"{line['er']:g}", "H": f"{line['h']:g}mm", "T": "1um",
                                    "TanD": f"{line['tand']:g}", "Cond": f"{line['cond']:g}"})
                ml = CK.Component(f"DE_{ci.name}", "MLIN", x0 + side * 60, py, 0,
                                  {"Subst": sub.name, "W": f"{line['w']:g}mm", "L": f"{-line['Lb']:g}mm"})
                comps += [sub, ml]
                a, b = CK.pins(ml)
                near, far = (b, a) if side < 0 else (a, b)
                wires.append(CK.Wire([[x0, py], [near[0], near[1]]]))
                x1 = far[0]
            else:
                x1 = x0
            blk = CK.Component(f"EM_{ci.name}", "EM", x1 + side * 80, py, 0 if side < 0 else 180,
                               {"Design": tname, "Solution": _transition_solution(project, tname), "Model": "Data",
                                "CircuitModel": "", "Ports": "2", "PortNames": "Coax,Board"})
            comps.append(blk)
            bp = CK.pins(blk)              # [coax, board]
            wires.append(CK.Wire([[bp[1][0], bp[1][1]], [x1, py]]))
            x_end, py_end = bp[0]
        port_x = x_end + side * 60
        comps.append(CK.Component(f"Port{num}", "PORT", port_x, py_end + 40, 0, {"Num": num, "Z0": "50ohm"}))
        wires.append(CK.Wire([[x_end, py_end], [port_x, py_end]]))
    c.components, c.wires = comps, wires
    if not c.setups:
        for st in design.setups:
            s = Setup(st.name)
            s.sweeps = []
            for sw in st.sweeps:
                n = Sweep(sw.name)
                n.props.update(sw.props)
                n.props["SweepType"] = "Discrete"
                s.sweeps.append(n)
            if s.sweeps:
                c.setups.append(s)
    return c


def cascade_problems(project, design) -> list[str]:
    """Why the cascade of ``design`` would be wrong or cannot run yet: transition designs missing or stale, the
    3D design solved with other ports than its lumped ports, and so on."""
    out = []
    for ci in design.connectors:
        if ci.mode != "Transition":
            continue
        st = transition_state(project, design, ci)
        if st != "current":
            out.append(f"{ci.name}: the transition model is {st}; update it (set the mode again) and solve it")
    if project.path:
        try:
            from ..solver.circuit import em_solutions, em_port_names
            sols = em_solutions(project, design.name)
            want = [e.name for e in design.ports()]
            sw = _sweeps_of(design)
            sol = f"{sw[0][0].name} : {sw[0][1].name}" if sw else ""
            if sol not in sols:
                out.append(f"{design.name} has no solution {sol or '(no sweep)'} yet")
            elif em_port_names(project, design.name, sol) != want:
                out.append(f"the solution of {design.name} has other ports than {', '.join(want)}: solve it again "
                           "with the current connector modes")
            for ci in design.connectors:
                t = ci.props.get("Transition", "")
                if ci.mode == "Transition" and t and _transition_solution(project, t) not in em_solutions(project, t):
                    out.append(f"{t} is not solved yet")
        except Exception as e:  # noqa: BLE001
            out.append(str(e))
    return out


def designs_to_solve(project, design) -> list[str]:
    """The 3D designs a cascade needs solved, transition designs first."""
    names = []
    for ci in design.connectors:
        t = ci.props.get("Transition", "")
        if ci.mode == "Transition" and t and t not in names:
            names.append(t)
    return names + [design.name]


# ------------------------------------------------------------------------------------------------------------
# optimization of the launch (in the transition model)
# ------------------------------------------------------------------------------------------------------------
def add_launch_optimization(tdesign, f_max_ghz: float | None = None, rl_db: float = 20.0, prefix: str = "J",
                            solution: str = "Setup1 : Sweep", name: str = "Launch"):
    """An optimization of the launch footprint (pad width and length, launch gap, taper length) for return loss
    better than ``rl_db`` up to f_max at the coax port of a transition design (or any design with a connector
    of variable prefix ``prefix``)."""
    from .design import Optimization
    from . import schema
    ev = tdesign.evaluator()
    if f_max_ghz is None:
        f_max_ghz = _frequency_span(tdesign)[1] or 40.0
    op = Optimization(tdesign.unique_name(name), solution, props=dict(schema.defaults(schema.OPTIMIZATION)))
    ranges = {"wp": (0.6, 1.5), "lp": (0.5, 2.0), "gl": (0.5, 2.0), "lt": (0.0, 2.5), "gc": (0.5, 2.0)}
    lf = ev.length(tdesign.variables.get(f"{prefix}_lf", "1e9mm")) * 1e3
    for k, (lo, hi) in ranges.items():
        v = f"{prefix}_{k}"
        if v not in tdesign.variables:
            continue
        x = ev.length(tdesign.variables[v]) * 1e3
        if k == "lt" and x <= 0:
            continue                    # no taper drawn (pad as wide as the trace)
        lo_mm, hi_mm = x * lo, x * hi
        if k in ("lt", "lp"):
            lo_mm, hi_mm = 0.05, max(1.0, hi_mm)
            hi_mm = min(hi_mm, max(0.1, (lf - 0.3) / 2))     # pad and taper stay inside the footprint
        if k in ("gl", "gc"):
            lo_mm = max(lo_mm, 0.08)
        op.variables.append({"name": v, "include": True, "min": f"{lo_mm:.4g}mm", "max": f"{hi_mm:.4g}mm"})
    op.goals.append({"calc": "dB(S(1,1))", "start": "0.5GHz", "stop": f"{f_max_ghz:g}GHz", "cond": "<=",
                     "value": f"{-rl_db:g}", "weight": "1"})
    tdesign.optimetrics = [o for o in tdesign.optimetrics if o.name != op.name] + [op]
    return op


def pull_footprint(project, design, inst) -> dict[str, str]:
    """Copies the footprint of the transition design (after an optimization) into the connector ``inst`` of
    ``design``; the transition design stays current. Returns the new values."""
    t = project.design(inst.props["Transition"])
    out = {}
    for k in FOOTPRINT_VARS:
        v = t.variables.get(f"J_{k}")
        if v is not None:
            design.variables[f"{inst.name}_{k}"] = v
            out[f"{inst.name}_{k}"] = v
    t.settings[TRANSITION_SIG] = transition_signature(project, design, inst)
    return out
