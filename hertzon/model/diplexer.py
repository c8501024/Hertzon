"""Diplexer design: two tapped hairpin band-pass filters joined by a T-junction (Tools > Filter Design).

Layout (top view): channel A to the left of the junction, channel B to the right, all ports on one line.

                                 Port 1 (common, at the top edge)
                                       │
    Port 2 ── [hairpins of A] ── j_A ──┴── j_B ── [hairpins of B] ── Port 3

Each channel is the hairpin row of filters.hairpin_design with its own length L, outer length offset dL, gaps
(g, g2, ...), tap yP and junction line j; the trace width w, inner width b and miter M are shared. The variables of
channel X carry the suffix _X (L_A, g_A, yP_A, j_A ...). Each row is shifted vertically by its own tap height so
that both taps and the three ports lie on y = 0. The junction lines start at the length that makes each filter look
like an open circuit at the junction in the band of the other channel (computed with the circuit model).

The project has the 3D design and the same diplexer as a circuit with the same variables: the coarse model of the
space mapping optimization DiplexerSM.

No Qt here: used by the dialog, MCP and scripts.
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field

from . import schema
from .design import Assignment, Command, Design, Optimization, Project, Setup, Sweep
from .filters import _MIN_LINE, _X, _Y, HairpinSpec, _mm, _r, gap_names, hairpin_parts, nominal
from .microstrip import engine, guided_wavelength, synthesize

CHANNELS = ("A", "B")


@dataclass
class ChannelSpec:
    n: int = 3                                   # resonators
    passband: tuple[float, float] = (2.25, 2.45)  # GHz
    insertion_loss: float = 2.0                  # dB
    return_loss: float = 12.0
    ripple: float = 1.0
    rejection: float = 15.0                      # dB, in the passband of the other channel and the outer stopband


@dataclass
class DiplexerSpec:
    channels: list[ChannelSpec] = field(default_factory=lambda: [ChannelSpec(3, (2.25, 2.45)),
                                                                 ChannelSpec(3, (2.65, 2.85))])
    isolation: float = 25.0                      # dB, between ports 2 and 3 in both passbands
    material: str = "Rogers RO4350B (tm)"
    er: float = 3.66
    tand: float = 0.0037
    h: float = 0.508                             # mm
    t: float = 35.0                              # um copper
    sigma: float = 5.8e7
    z_res: float = 50.0
    z_feed: float = 50.0
    gap: tuple[float, float] = (0.1, 1.5)        # mm, range of every coupling gap
    inner: float = 1.5                           # mm
    inner_tol: float = 0.1
    trace_tol: float = 0.2
    length_pct: tuple[float, float, float] = (42.5, 50.0, 57.5)
    miter: tuple[float, float] = (0.5, 0.8)
    min_feature: float = 0.01
    feed: float = 3.0                            # mm, feed line from the outer ports to the hairpins
    margin: float = 3.0                          # mm, board around the hairpins (top, bottom)
    sweep: tuple[float, float, float] | None = None     # GHz; None: from the passbands
    synthesis: bool = True
    optimization: bool = True
    name: str = ""
    junction: str = "tee"                        # "tee": T-junction and lines; "resonator": common resonator

    def __post_init__(self) -> None:
        if self.sweep is None:
            self.sweep = auto_sweep(self.channels)


def auto_sweep(channels) -> tuple[float, float, float]:
    """From one bandwidth below the lowest to one bandwidth above the highest passband (as auto_bands does for one
    filter), in steps of about a twentieth of the narrower bandwidth (every frequency is a circuit evaluation of the
    space mapping); the ends on the step grid."""
    bw = min(c.passband[1] - c.passband[0] for c in channels)
    step = float(f"{max(bw, 1e-6) / 20:.1g}")
    lo = min(c.passband[0] - (c.passband[1] - c.passband[0]) for c in channels)
    hi = max(c.passband[1] + (c.passband[1] - c.passband[0]) for c in channels)
    a, b = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    return (float(f"{max(a, step):.6g}"), float(f"{b:.6g}"), step)


def stopbands(spec: DiplexerSpec, k: int) -> list[tuple[float, float]]:
    """Stopbands of channel k: the outer one on the side away from the other channel, from a third of the
    bandwidth beyond the passband to the end of the sweep (as auto_bands), and the passband of the other channel."""
    c, o = spec.channels[k], spec.channels[1 - k]
    lo, hi = c.passband
    bw = hi - lo
    a, b, step = spec.sweep
    outer = (a, lo - bw / 3) if o.passband[0] > hi else (hi + bw / 3, b)
    outer = tuple(float(f"{round(x / step) * step:.6g}") for x in outer)
    bands = [outer] if outer[1] > outer[0] else []
    return bands + [tuple(o.passband)]


def _var(x: str, ch: str, n: int) -> str:
    """Name of the single-filter variable x for channel ch: shared ones unchanged, the others with _ch."""
    alias = {"si": "s", "sx": "so"}
    x = alias.get(x, x)
    if x in ("w", "b", "dep", "wf", "wo", "cut", "Me", "M", "h", "dmin"):
        return x
    return f"{x}_{ch}"


def channel_spec(spec: DiplexerSpec, k: int) -> HairpinSpec:
    """The single filter of channel k (for the synthesis and the ranges of filters.nominal)."""
    c = spec.channels[k]
    f0 = sum(c.passband) / 2
    loss = {"tand": spec.tand, "sigma": spec.sigma}
    h, t = spec.h * 1e-3, spec.t * 1e-6
    trace = synthesize(spec.z_res, h, spec.er, t, f0 * 1e9, **loss) * 1e3
    lam = guided_wavelength(trace * 1e-3, h, spec.er, t, f0 * 1e9, **loss) * 1e3
    g_hi = spec.gap[1]
    wo = spec.inner * (1 + spec.inner_tol) + 2 * trace * (1 + spec.trace_tol)
    # room for the longest hairpin and for the row at the largest gaps
    region = max(lam * spec.length_pct[2] / 100 / 2 + 4 * trace + 2, c.n * wo + (c.n - 1) * g_hi + 1)
    return HairpinSpec(n=c.n, passband=tuple(c.passband), stopbands=stopbands(spec, k),
                       insertion_loss=c.insertion_loss, return_loss=c.return_loss, ripple=c.ripple,
                       rejection=c.rejection, material=spec.material, er=spec.er, tand=spec.tand, h=spec.h, t=spec.t,
                       board=10 * region + 100, region=region, z_res=spec.z_res, z_feed=spec.z_feed, gap=spec.gap,
                       inner=spec.inner, inner_tol=spec.inner_tol, trace_tol=spec.trace_tol,
                       length_pct=spec.length_pct, miter=spec.miter, min_feature=spec.min_feature,
                       sweep=spec.sweep, optimization=False, sigma=spec.sigma, synthesis=spec.synthesis)


def diplexer_goals(spec: DiplexerSpec) -> list[dict]:
    """Goals of the two channel masks on the three-port: insertion loss and ripple of S(k+2,1) in the passband
    of channel k, rejection in its stopbands, return loss at the common port in both passbands and at the channel
    port in its own passband, isolation S(3,2) in both passbands."""
    ghz = lambda v: f"{v:g}GHz"
    g = []
    for k, c in enumerate(spec.channels):
        p = k + 2
        a, b = ghz(c.passband[0]), ghz(c.passband[1])
        s = f"dB(S({p},1))"
        g.append({"calc": s, "start": a, "stop": b, "cond": ">=", "value": f"{-c.insertion_loss:g}", "weight": "1"})
        g.append({"calc": s, "start": a, "stop": b, "cond": "Ripple <=", "value": f"{c.ripple:g}", "weight": "1"})
        g.append({"calc": "dB(S(1,1))", "start": a, "stop": b, "cond": "<=", "value": f"{-c.return_loss:g}",
                  "weight": "1"})
        g.append({"calc": f"dB(S({p},{p}))", "start": a, "stop": b, "cond": "<=", "value": f"{-c.return_loss:g}",
                  "weight": "0.5"})
        for lo, hi in stopbands(spec, k):
            g.append({"calc": s, "start": ghz(lo), "stop": ghz(hi), "cond": "<=", "value": f"{-c.rejection:g}",
                      "weight": "1"})
    for c in spec.channels:
        g.append({"calc": "dB(S(3,2))", "start": ghz(c.passband[0]), "stop": ghz(c.passband[1]), "cond": "<=",
                  "value": f"{-spec.isolation:g}", "weight": "1"})
    return g


def _check(spec: DiplexerSpec) -> None:
    if len(spec.channels) != 2:
        raise ValueError("A diplexer has two channels")
    a, b = (tuple(c.passband) for c in spec.channels)
    for lo, hi in (a, b):
        if not hi > lo > 0:
            raise ValueError("Every passband needs start < stop")
    if not (a[1] < b[0] or b[1] < a[0]):
        raise ValueError("The passbands of the two channels overlap")
    s = spec.sweep
    if not s[1] > s[0] or s[0] > min(a[0], b[0]) + 1e-9 or s[1] < max(a[1], b[1]) - 1e-9:
        raise ValueError("The sweep must cover both passbands (the goals are evaluated on it)")


def nominals(spec: DiplexerSpec) -> list[dict]:
    """filters.nominal of each channel (start values and ranges in mm)."""
    _check(spec)
    return [nominal(channel_spec(spec, k)) for k in range(2)]


def _variables(spec: DiplexerSpec, noms: list[dict], j0: tuple[float, float] = (0.0, 0.0)) -> dict[str, str]:
    a = noms[0]
    v = {"h": _mm(spec.h), "t_cu": f"{spec.t:g}um", "wf": _mm(a["feed"]), "w": _mm(a["trace"]),
         "b": _mm(a["inner"]), "M": f"{a['miter']:g}", "dmin": _mm(spec.min_feature), "fl": _mm(spec.feed),
         "mb": _mm(spec.margin),
         "wo": "b+2*w", "Me": "min(M, 1-dmin/(sqrt(2)*w), (wo/2-dmin)/(2*w))", "cut": "2*Me*w",
         "dep": "max(w, cut)"}
    for k, ch in enumerate(CHANNELS):
        nom, n = noms[k], spec.channels[k].n
        gn = gap_names(n)
        x = lambda s, ch=ch, n=n: _var(s, ch, n)
        v.update({x("L"): _mm(nom["length"]), x("dL"): "0mm", x("yP"): _mm(nom["yP"]), x("j"): _mm(j0[k])})
        for name in sorted(set(gn)):
            v[x(name)] = _mm(nom["gaps"][name])
        v.update({x("s"): f"({x('L')}-b-2*dep)/2", x("so"): f"({x('L')}+{x('dL')}-b-2*dep)/2",
                  x("xe"): f"({n}*wo+{'+'.join(x(g) for g in gn)})/2",
                  x("ycap"): f"max(0mm, {x('so')}/2-wf/2-dmin)",
                  x("yt"): f"max(-{x('ycap')}, min({x('ycap')}, {x('yP')}))"})
    # left edge of each row (junction at x = 0), board edges
    v["xl_A"] = "-(wf/2+j_A)-2*xe_A"
    v["xl_B"] = "wf/2+j_B"
    v["Wl"] = "fl-xl_A"
    v["Wr"] = "xl_B+2*xe_B+fl"
    v["Wy"] = "2*(max(so_A/2+dep+abs(yt_A), so_B/2+dep+abs(yt_B))+mb)"
    return v


def _ranges(spec: DiplexerSpec, noms: list[dict], j0, jmin, lam_o) -> dict[str, tuple[float, float]]:
    r = {}
    for k, ch in enumerate(CHANNELS):
        n, nr = spec.channels[k].n, noms[k]["ranges"]
        for name in ("L", "dL", *sorted(set(gap_names(n))), "yP"):
            r[_var(name, ch, n)] = nr[name]
        r[f"j_{ch}"] = (_r(min(jmin, j0[k])), _r(max(j0[k], jmin) + lam_o[k] / 4))
    a = noms[0]["ranges"]
    r.update({"w": a["w"], "b": a["b"], "M": a["M"]})
    return r


# variables of the space mapping (and of the conjugate gradient): length, outer length offset, gaps and tap of
# both channels and the junction lines; w, b and M are offered but not included
def _sm_variable(name: str) -> bool:
    return name not in ("w", "b", "M")


def junction_lines(spec: DiplexerSpec, noms: list[dict], jmin: float) -> tuple[tuple[float, float], list[float]]:
    """Start length of j_A and j_B (mm, >= jmin): the line from the junction to channel k turns the input
    reflection of channel k at the centre of the other channel into an open circuit (phase 0). The reflection
    comes from the circuit model of channel k alone. Also the guided wavelength of the feed line at the other
    channel's centre (mm)."""
    import numpy as np

    from ..solver import tlines as TL
    from ..solver.circuit import analyze
    out, lams = [], []
    v = _variables(spec, noms)
    for k, ch in enumerate(CHANNELS):
        fo = sum(spec.channels[1 - k].passband) / 2 * 1e9
        d = _channel_circuit(spec, k, v)
        p = Project("tmp")
        p.designs = [d]
        S = analyze(p, d, np.array([fo])).S
        gam = S[0, 0, 0]
        wf = noms[0]["feed"] * 1e-3
        _, g = TL.microstrip(fo, wf, spec.h * 1e-3, spec.t * 1e-6, spec.er, spec.tand, spec.sigma)
        beta = g.imag
        lam = 2 * math.pi / beta * 1e3
        phi = cmath.phase(gam) % (2 * math.pi)
        length = phi / (2 * beta) * 1e3                   # e^{-2j beta l} turns the phase to 0
        while length < jmin:
            length += lam / 2
        out.append(_r(length))
        lams.append(lam)
    return (out[0], out[1]), lams


def _channel_circuit(spec: DiplexerSpec, k: int, variables: dict) -> Design:
    """Channel k alone as a circuit: Port1 at the junction-side tap, Port2 at the outer tap."""
    from .circuit import AUTO
    from .circuit import Component as C
    ch, n = CHANNELS[k], spec.channels[k].n
    d = Design(name=f"Channel_{ch}", kind="circuit")
    d.variables = dict(variables)
    d.settings[AUTO] = "False"
    comps = [C("Sub1", "SUBST", 0, -120, 0, _subst(spec))]
    place = _placer(comps)
    sub = {"Subst": "Sub1"}

    def port(num):
        def feed(tag, net):
            place("PORT", f"Port{num}", {"Num": str(num), "Z0": f"{spec.z_feed:g}ohm"}, [net])
        return feed
    # A: junction on the right (last hairpin), B: on the left (first hairpin)
    taps = ((n - 1, "R", "J", port(1)), (0, "L", "O", port(2))) if k == 0 else \
        ((0, "L", "J", port(1)), (n - 1, "R", "O", port(2)))
    hairpin_parts(place, sub, n, lambda x: _var(x, ch, n), f"{ch}_", taps)
    d.components = comps
    return d


def _subst(spec: DiplexerSpec) -> dict:
    return {"Er": f"{spec.er:g}", "H": "h", "T": "t_cu", "TanD": f"{spec.tand:g}", "Cond": f"{spec.sigma:g}"}


def _placer(comps: list):
    from .circuit import Component as C
    from .circuit import pins
    col = [0]

    def place(ctype, nm, props, nets, rot=0):
        """A part in the next free cell of the schematic, a net label on each pin."""
        x, y = 200 * (col[0] % 8), 200 * (col[0] // 8)
        col[0] += 1
        c = C(nm, ctype, x, y, rot, dict(props))
        comps.append(c)
        for (px, py), net in zip(pins(c), nets):
            if net:
                comps.append(C(f"N_{nm}_{px}_{py}", "LABEL", px, py, 0, {"Net": net}))
        return c
    return place


def circuit_name(design_name: str) -> str:
    return f"{design_name}_Circuit"


def diplexer_design(spec: DiplexerSpec) -> tuple[Design, dict]:
    """The parametrized 3D diplexer and its nominal values {"channels": [nominal A, nominal B], "j": (j_A, j_B),
    "ranges": {...}}. With junction "resonator" the common-resonator diplexer (_resonator_design)."""
    if spec.junction == "resonator":
        return _resonator_design(spec)
    noms = nominals(spec)
    jmin = max(3 * spec.h, noms[0]["feed"], 1.0)
    j0, lam_o = junction_lines(spec, noms, jmin)
    na, nb = (c.n for c in spec.channels)
    d = Design(name=spec.name or f"Diplexer_N{na}_N{nb}", units="mm")
    d.variables.update(_variables(spec, noms, j0))
    d.settings["AutoOpen"] = "Auto Open - Filter"
    d.settings["FilterAirMargin"] = "12mm"
    d.add_object("CreateBox", name="Substrate", material=spec.material, Position="-Wl,-Wy/2,0",
                 XSize="Wl+Wr", YSize="Wy", ZSize="h")
    gnd = d.add_object("CreateRectangle", name="Ground", Position="-Wl,-Wy/2,0", Axis="Z", XSize="Wl+Wr",
                       YSize="Wy")
    rows = []
    for k, ch in enumerate(CHANNELS):
        n = spec.channels[k].n
        x = lambda s, ch=ch, n=n: _var(s, ch, n)
        gn = [x(g) for g in gap_names(n)]
        pins = []
        for i in range(n):
            cx = f"{x('xl')}+{2 * i + 1:g}*wo/2" + "".join(f"+{g}" for g in gn[:i]) + "+"
            flip = i % 2 == 1
            arm = x("s") if 0 < i < n - 1 else x("so")
            ys = [y.replace("s/2", f"{arm}/2") for y in _Y]
            pts = [f"{cx}({px}),-{x('yt')}{'-' if flip else '+'}({py}),h" for px, py in zip(_X, ys)]
            if flip:                                     # mirrored: keep the outline counter-clockwise (+z normal)
                pts = pts[:1] + pts[:0:-1]
            pins.append(d.add_object("CreatePolyline", name=f"Hairpin{ch}{i + 1}", material="vacuum",
                                     Points=";".join(pts), Closed="True", Covered="True"))
        rows.append(pins)
    a_out, a_in = rows[0][0], rows[0][-1]
    b_in, b_out = rows[1][0], rows[1][-1]
    feeds = [
        ("Feed2", "-Wl,-wf/2,h", "xl_A+w/2+Wl", a_out),
        ("Feed3", "xl_B+2*xe_B-w/2,-wf/2,h", "Wr-(xl_B+2*xe_B-w/2)", b_out),
    ]
    for name, pos, xs, pin in feeds:
        f = d.add_object("CreateRectangle", name=name, Position=pos, Axis="Z", XSize=xs, YSize="wf")
        pin.history.append(Command.new("Unite", Tools=f.name))
        f.consumed_by = pin.name
    # the junction: the line from the last hairpin of A to the first of B, the common feed up to the top edge
    jl = d.add_object("CreateRectangle", name="Junction", Position="xl_A+2*xe_A-w/2,-wf/2,h", Axis="Z",
                      XSize="xl_B+w/2-(xl_A+2*xe_A-w/2)", YSize="wf")
    f1 = d.add_object("CreateRectangle", name="Feed1", Position="-wf/2,-wf/2,h", Axis="Z", XSize="wf",
                      YSize="Wy/2+wf/2")
    a_in.history.append(Command.new("Unite", Tools=f"{jl.name},{f1.name},{b_in.name}"))
    for o in (jl, f1, b_in):
        o.consumed_by = a_in.name
    p1 = d.add_object("CreateRectangle", name="Port1", Position="-wf/2,Wy/2,0", Axis="Y", XSize="wf", ZSize="h")
    p2 = d.add_object("CreateRectangle", name="Port2", Position="-Wl,-wf/2,0", Axis="X", YSize="wf", ZSize="h")
    p3 = d.add_object("CreateRectangle", name="Port3", Position="Wr,-wf/2,0", Axis="X", YSize="wf", ZSize="h")

    copper = [p.name for row in rows for p in row if p is not b_in]
    ports = ((1, p1, "0,Wy/2,0;0,Wy/2,h"), (2, p2, "-Wl,0,0;-Wl,0,h"), (3, p3, "Wr,0,0;Wr,0,h"))
    ranges = _ranges(spec, noms, j0, jmin, lam_o)
    _finish(d, spec, copper, gnd, ports, ranges)
    return d, {"channels": noms, "j": j0, "jmin": jmin, "ranges": ranges, "synthesis": engine()}


def _assign(lst, table, kind, name, objects, **props):
    p = schema.defaults(table[kind])
    p.update(props)
    lst.append(Assignment(name, kind, p, objects=list(objects)))


def _finish(d: Design, spec: DiplexerSpec, copper: list[str], gnd, ports, ranges: dict) -> None:
    """Boundaries, ports, mesh operation, setup and sweep, and the optimizations DiplexerSM and DiplexerCG."""
    _assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Top", copper,
            UseThickness="True", Thickness="t_cu", TwoSided="True")
    _assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Ground", [gnd.name],
            UseThickness="True", Thickness="t_cu", TwoSided="True")
    for num, port, line in ports:
        _assign(d.excitations, schema.EXCITATIONS, "Lumped Port", str(num), [port.name], PortNumber=str(num),
                IntegrationLine=line)
    _assign(d.mesh_ops, schema.MESH_OPS, "Length Based On Selection", "Length_Copper", copper, MaxLength="w/2")
    st = Setup("Setup1")
    f_mid = (min(c.passband[0] for c in spec.channels) + max(c.passband[1] for c in spec.channels)) / 2
    st.props.update(Frequency=f"{f_mid:g}GHz", MaximumPasses="10", MaximumDeltaS="0.05")
    sw = Sweep("Sweep")
    a, b, step = spec.sweep
    sw.props.update(SweepType="Interpolating", Distribution="Linear Step", Start=f"{a:g}GHz", Stop=f"{b:g}GHz",
                    Step=f"{step:g}GHz", ErrorTolerance="1", MaxSolutions="60")
    st.sweeps.append(sw)
    d.setups.append(st)
    if spec.optimization:
        goals = diplexer_goals(spec)
        sm = Optimization("DiplexerSM", "Setup1 : Sweep",
                          props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer="Space Mapping",
                                     CoarseModel=circuit_name(d.name)))
        cg = Optimization("DiplexerCG", "Setup1 : Sweep")
        for o in (sm, cg):
            for var, (lo, hi) in ranges.items():
                unit = "" if var == "M" else "mm"
                o.variables.append({"name": var, "include": _sm_variable(var), "min": f"{lo:g}{unit}",
                                    "max": f"{hi:g}{unit}"})
            o.goals = [dict(g) for g in goals]
            d.optimetrics.append(o)


def diplexer_circuit(spec: DiplexerSpec, name: str = "") -> Design:
    """The diplexer of diplexer_design as a circuit with the same variables: the two hairpin rows of
    filters.hairpin_parts, the junction lines j_A and j_B, a T-junction (MTEE) with the common feed to Port1 and
    the outer feeds fl to Port2 and Port3."""
    from .circuit import AUTO
    from .circuit import Component as C
    from .design import circuit_setup
    if spec.junction == "resonator":
        return _resonator_circuit(spec, name)
    d3, _ = diplexer_design(spec)
    d = Design(name=name or circuit_name(d3.name), kind="circuit")
    d.variables = dict(d3.variables)
    d.settings[AUTO] = "False"
    comps = [C("Sub1", "SUBST", 0, -120, 0, _subst(spec))]
    place = _placer(comps)
    sub = {"Subst": "Sub1"}

    def outer(num):
        def feed(tag, net):
            place("MLIN", f"F{num}", {**sub, "W": "wf", "L": "fl"}, [f"P{num}", net])
            place("PORT", f"Port{num}", {"Num": str(num), "Z0": f"{spec.z_feed:g}ohm"}, [f"P{num}"])
        return feed

    def junction(ch):
        def feed(tag, net):
            place("MLIN", f"J{ch}", {**sub, "W": "wf", "L": f"j_{ch}"}, [net, f"TJ_{ch}"])
        return feed
    for k, ch in enumerate(CHANNELS):
        n = spec.channels[k].n
        taps = ((0, "L", "O", outer(2)), (n - 1, "R", "J", junction(ch))) if k == 0 else \
            ((0, "L", "J", junction(ch)), (n - 1, "R", "O", outer(3)))
        hairpin_parts(place, sub, n, lambda x, ch=ch, n=n: _var(x, ch, n), f"{ch}_", taps)
    place("MTEE", "TJ", {**sub, "W1": "wf", "W2": "wf", "W3": "wf"}, ["TJ_A", "TJ_B", "TJ_1"])
    place("MLIN", "F1", {**sub, "W": "wf", "L": f"max({_MIN_LINE}, Wy/2-wf/2)"}, ["TJ_1", "P1"])
    place("PORT", "Port1", {"Num": "1", "Z0": f"{spec.z_feed:g}ohm"}, ["P1"])
    d.components = comps
    st = circuit_setup()
    a, b, step = spec.sweep
    st.sweeps[0].props.update(Distribution="Linear Step", Start=f"{a:g}GHz", Stop=f"{b:g}GHz", Step=f"{step:g}GHz")
    d.setups.append(st)
    if spec.optimization:
        op = Optimization("DiplexerCircuitOpt", "Setup1 : Sweep",
                          props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer="Conjugate Gradient"))
        op.variables = [dict(v) for v in d3.optimetrics[0].variables]
        op.goals = [dict(g) for g in d3.optimetrics[0].goals]
        d.optimetrics.append(op)
    return d


def diplexer_project(spec: DiplexerSpec) -> Project:
    d, _ = diplexer_design(spec)
    p = Project(d.name)
    p.designs = [d, diplexer_circuit(spec, circuit_name(d.name))]
    return p


def outlines(design: Design, project: Project | None = None) -> dict[str, list[tuple[float, float]]]:
    """Top view (x, y in mm) of the hairpins, feeds and junction of a diplexer design (preview)."""
    ev = design.evaluator(project)
    out = {}
    for o in design.objects:
        p = o.history[0].props if o.history else {}
        if o.name.startswith("Hairpin"):
            out[o.name] = [(x * 1e3, y * 1e3) for x, y, _ in ev.points(p["Points"])]
        elif o.name.startswith("Feed") or o.name == "Junction":
            x, y, _ = ev.point(p["Position"])
            dx, dy = ev.length(p["XSize"]), ev.length(p["YSize"])
            out[o.name] = [(1e3 * a, 1e3 * b) for a, b in ((x, y), (x + dx, y), (x + dx, y + dy), (x, y + dy))]
    return out



# ---------------------------------------------------------------------------------------------
# common resonator instead of the T-junction
# ---------------------------------------------------------------------------------------------
# One hairpin R0 between the channels couples to the first resonator of each and is tapped by the common port
# from inside its U (the feed comes down from the top edge and turns to the left arm). The rows of A, R0 and B
# form one row of hairpins centred on y = 0; Port 2 and Port 3 sit at the tap heights of their channels.
#
#                      Port 1
#                        |
#     Port 2 -- A1 A2 A3 R0 B3 B2 B1 -- Port 3        (A3 and B3 couple to R0 at the gaps g_0A and g_0B)

def _row(spec: DiplexerSpec) -> list[dict]:
    """The hairpins of the common-resonator diplexer from left to right: arm length, outer width, inner width,
    flipped (open end at the bottom); R0 opens to the top, its neighbours alternate from there."""
    na, nb = (c.n for c in spec.channels)
    row = []
    for i in range(na):
        row.append({"name": f"A{i + 1}", "arm": "so_A" if i == 0 else "s_A", "wo": "wo", "b": "b"})
    row.append({"name": "R0", "arm": "s_0", "wo": "wo0", "b": "b0"})
    for i in range(nb):
        row.append({"name": f"B{nb - i}", "arm": "so_B" if i == nb - 1 else "s_B", "wo": "wo", "b": "b"})
    for k, h in enumerate(row):
        h["flip"] = (k - na) % 2 == 1
    return row


def _row_gaps(spec: DiplexerSpec) -> list[str]:
    na, nb = (c.n for c in spec.channels)
    return [f"{g}_A" for g in gap_names(na)] + ["g_0A", "g_0B"] + [f"{g}_B" for g in gap_names(nb)][::-1]


def _resonator_start(spec: DiplexerSpec, noms: list[dict], b0: float) -> dict:
    """Start of the common resonator: half a wavelength at the centre between the channels, tapped for the
    external Q of a filter over both channels (Qe = g0 g1 / FBW with g1 of the channel prototype), coupled to
    each channel at the gap of its first pair."""
    lo = min(c.passband[0] for c in spec.channels)
    hi = max(c.passband[1] for c in spec.channels)
    f0 = (lo + hi) / 2
    loss = {"tand": spec.tand, "sigma": spec.sigma}
    h, t = spec.h * 1e-3, spec.t * 1e-6
    trace = noms[0]["trace"]
    lam = guided_wavelength(trace * 1e-3, h, spec.er, t, f0 * 1e9, **loss) * 1e3
    length = lam * spec.length_pct[1] / 100
    dep = max(trace, 2 * noms[0]["miter"] * trace)
    arm = (length - b0 - 2 * dep) / 2
    proto = [n.get("prototype") for n in noms]
    g1 = min(p["g"][1] for p in proto) if all(proto) else 1.0
    qe = g1 / ((hi - lo) / f0)
    c2 = math.pi * spec.z_feed / (2 * spec.z_res * qe)
    l_open = length / math.pi * math.acos(min(1.0, math.sqrt(min(max(c2, 0.0), 1.0))))
    tap = max(0.0, arm / 2 - noms[0]["feed"] / 2 - spec.min_feature)
    yp = min(max(arm / 2 - l_open, -tap), tap)
    gaps = [noms[k]["gaps"][gap_names(spec.channels[k].n)[0]] for k in range(2)]
    p_lo, _, p_hi = spec.length_pct
    return {"L": _r(length), "yP": _r(yp), "g": gaps, "range_L": (_r(lam * p_lo / 100), _r(lam * p_hi / 100)),
            "tap": _r(tap), "f0": f0, "Qe": qe}


def _resonator_variables(spec: DiplexerSpec, noms: list[dict], r0: dict, b0: float) -> dict[str, str]:
    v = _variables(spec, noms)
    for k in ("j_A", "j_B", "xl_A", "xl_B", "xe_A", "xe_B", "Wl", "Wr", "Wy"):
        v.pop(k, None)
    v.update({"b0": _mm(b0), "wo0": "b0+2*w", "L_0": _mm(r0["L"]), "yP_0": _mm(r0["yP"]),
              "g_0A": _mm(r0["g"][0]), "g_0B": _mm(r0["g"][1]),
              "s_0": "(L_0-b0-2*dep)/2", "ycap_0": "max(0mm, s_0/2-wf/2-dmin)",
              "yt_0": "max(-ycap_0, min(ycap_0, yP_0))"})
    row, gaps = _row(spec), _row_gaps(spec)
    # x of the left edge of each hairpin: R0 centred on x = 0
    k0 = spec.channels[0].n
    for k, hp in enumerate(row):
        if k < k0:
            parts = ["-wo0/2"] + [f"-{gaps[j]}-{row[j]['wo']}" for j in range(k, k0)]
        elif k == k0:
            parts = ["-wo0/2"]
        else:
            parts = ["wo0/2"] + [f"+{gaps[j]}" for j in range(k0, k)] + [f"+{row[j]['wo']}" for j in range(k0 + 1, k)]
        v[f"x_{hp['name']}"] = "".join(parts).lstrip("+")
    last = row[-1]
    v["Wl"] = f"fl-x_{row[0]['name']}"
    v["Wr"] = f"x_{last['name']}+wo+fl"
    v["Wy"] = "2*(max(so_A, s_A, s_0, so_B, s_B)/2+dep+mb)"
    return v


def _resonator_design(spec: DiplexerSpec) -> tuple[Design, dict]:
    noms = nominals(spec)
    b0 = _r(noms[0]["feed"] + 2 * max(1.0, 2 * spec.h))       # room for the common feed inside R0
    r0 = _resonator_start(spec, noms, b0)
    na, nb = (c.n for c in spec.channels)
    d = Design(name=spec.name or f"Diplexer_N{na}_N{nb}_R", units="mm")
    d.variables.update(_resonator_variables(spec, noms, r0, b0))
    d.settings["AutoOpen"] = "Auto Open - Filter"
    d.settings["FilterAirMargin"] = "12mm"
    d.add_object("CreateBox", name="Substrate", material=spec.material, Position="-Wl,-Wy/2,0",
                 XSize="Wl+Wr", YSize="Wy", ZSize="h")
    gnd = d.add_object("CreateRectangle", name="Ground", Position="-Wl,-Wy/2,0", Axis="Z", XSize="Wl+Wr",
                       YSize="Wy")
    pins = {}
    for hp in _row(spec):
        xs = [x.replace("wo", hp["wo"]) for x in _X]
        ys = [y.replace("s/2", f"{hp['arm']}/2") for y in _Y]
        cx = f"x_{hp['name']}+{hp['wo']}/2+"
        pts = [f"{cx}({px}),{'-' if hp['flip'] else ''}({py}),h" for px, py in zip(xs, ys)]
        if hp["flip"]:
            pts = pts[:1] + pts[:0:-1]
        pins[hp["name"]] = d.add_object("CreatePolyline", name=f"Hairpin{hp['name']}", material="vacuum",
                                        Points=";".join(pts), Closed="True", Covered="True")
    row = _row(spec)
    a_out, b_out = pins[row[0]["name"]], pins[row[-1]["name"]]
    for name, pos, xs, pin in (
            ("Feed2", "-Wl,yt_A-wf/2,h", f"x_{row[0]['name']}+w/2+Wl", a_out),
            ("Feed3", f"x_{row[-1]['name']}+wo-w/2,yt_B-wf/2,h", f"Wr-(x_{row[-1]['name']}+wo-w/2)", b_out)):
        f = d.add_object("CreateRectangle", name=name, Position=pos, Axis="Z", XSize=xs, YSize="wf")
        pin.history.append(Command.new("Unite", Tools=f.name))
        f.consumed_by = pin.name
    # the common feed: down from the top edge in the middle of R0, then to its left arm at the tap height
    r = pins["R0"]
    f1 = d.add_object("CreateRectangle", name="Feed1", Position="-wf/2,yt_0-wf/2,h", Axis="Z", XSize="wf",
                      YSize="Wy/2-yt_0+wf/2")
    st = d.add_object("CreateRectangle", name="Feed1Tap", Position="-wo0/2+w/2,yt_0-wf/2,h", Axis="Z",
                      XSize="wo0/2-w/2", YSize="wf")
    r.history.append(Command.new("Unite", Tools=f"{f1.name},{st.name}"))
    for o in (f1, st):
        o.consumed_by = r.name
    p1 = d.add_object("CreateRectangle", name="Port1", Position="-wf/2,Wy/2,0", Axis="Y", XSize="wf", ZSize="h")
    p2 = d.add_object("CreateRectangle", name="Port2", Position="-Wl,yt_A-wf/2,0", Axis="X", YSize="wf", ZSize="h")
    p3 = d.add_object("CreateRectangle", name="Port3", Position="Wr,yt_B-wf/2,0", Axis="X", YSize="wf", ZSize="h")
    ports = ((1, p1, "0,Wy/2,0;0,Wy/2,h"), (2, p2, "-Wl,yt_A,0;-Wl,yt_A,h"), (3, p3, "Wr,yt_B,0;Wr,yt_B,h"))
    ranges = {}
    for k, ch in enumerate(CHANNELS):
        n, nr = spec.channels[k].n, noms[k]["ranges"]
        for name in ("L", "dL", *sorted(set(gap_names(n))), "yP"):
            ranges[_var(name, ch, n)] = nr[name]
    ranges.update({"L_0": r0["range_L"], "g_0A": spec.gap, "g_0B": spec.gap, "yP_0": (-r0["tap"], r0["tap"])})
    a = noms[0]["ranges"]
    ranges.update({"w": a["w"], "b": a["b"], "M": a["M"]})
    _finish(d, spec, [p.name for p in pins.values()], gnd, ports, ranges)
    return d, {"channels": noms, "resonator": r0, "b0": b0, "ranges": ranges, "synthesis": engine()}


def _resonator_circuit(spec: DiplexerSpec, name: str = "") -> Design:
    """The common-resonator diplexer as a circuit: the row of hairpins with coupled lines between neighbours
    (the pair A-R0 split at the tap of R0), the outer taps to Port 2 and Port 3 and the common feed (line, corner,
    line) to the tap on the left arm of R0."""
    from .circuit import AUTO
    from .circuit import Component as C
    from .design import circuit_setup
    d3, _ = _resonator_design(spec)
    d = Design(name=name or circuit_name(d3.name), kind="circuit")
    d.variables = dict(d3.variables)
    d.settings[AUTO] = "False"
    comps = [C("Sub1", "SUBST", 0, -120, 0, _subst(spec))]
    place = _placer(comps)
    sub = {"Subst": "Sub1"}
    row, gaps = _row(spec), _row_gaps(spec)
    k0 = spec.channels[0].n
    m = _MIN_LINE

    def end(k, side, top):
        """Net at the top or bottom end of an arm; the end at the open side is left open by an MLOC."""
        return f"H{k}{side}_{'t' if top else 'b'}"

    def open_top(k):
        return not row[k]["flip"]

    def close_end(k, side, top, ext):
        """The arm beyond the coupled part: open stub at the open end, line to the bend at the other."""
        net = end(k, side, top)
        if top == open_top(k):
            place("MLOC", f"E{k}{side}{'t' if top else 'b'}", {**sub, "W": "w", "L": ext}, [net])
        else:
            place("MLIN", f"E{k}{side}{'t' if top else 'b'}", {**sub, "W": "w", "L": ext}, [net, f"H{k}{side}_bend"])

    for k in range(len(row) - 1):
        a, b = row[k]["arm"], row[k + 1]["arm"]
        lc = f"min({a},{b})"
        if k == k0 - 1:
            # A's last hairpin and the left arm of R0 (open at the top), split at the tap of R0
            up, lo = f"max({m}, {lc}/2-yt_0-wf/2)", f"max({m}, {lc}/2+yt_0-wf/2)"
            place("MCLIN", f"C{k + 1}u", {**sub, "W": "w", "S": gaps[k], "L": up},
                  [end(k, "R", True), "PA_u", end(k + 1, "L", True), "PR_u"])
            place("MLIN", f"C{k + 1}m", {**sub, "W": "w", "L": "wf"}, ["PA_u", "PA_l"])
            place("MTEE", "T1", {**sub, "W1": "w", "W2": "w", "W3": "wf"}, ["PR_u", "PR_l", "T1_feed"])
            place("MCLIN", f"C{k + 1}l", {**sub, "W": "w", "S": gaps[k], "L": lo},
                  ["PA_l", end(k, "R", False), "PR_l", end(k + 1, "L", False)])
        else:
            place("MCLIN", f"C{k + 1}", {**sub, "W": "w", "S": gaps[k], "L": lc},
                  [end(k, "R", True), end(k, "R", False), end(k + 1, "L", True), end(k + 1, "L", False)])
        for j, side, other in ((k, "R", b), (k + 1, "L", a)):
            ext = f"max({m}, ({row[j]['arm']}-{other})/2)"
            for top in (True, False):
                close_end(j, side, top, ext)
    # the common feed: from the tap to the middle of R0, corner, up to the top edge
    place("MLIN", "F1h", {**sub, "W": "wf", "L": f"max({m}, wo0/2-w-wf/2)"}, ["T1_feed", "F1c"])
    place("MCORN", "F1k", {**sub, "W": "wf"}, ["F1c", "F1v"])
    place("MLIN", "F1", {**sub, "W": "wf", "L": f"max({m}, Wy/2-yt_0-wf/2)"}, ["F1v", "P1"])
    place("PORT", "Port1", {"Num": "1", "Z0": f"{spec.z_feed:g}ohm"}, ["P1"])
    # the outer taps of the first and the last hairpin
    for k, side, num, yt in ((0, "L", 2, "yt_A"), (len(row) - 1, "R", 3, "yt_B")):
        arm = row[k]["arm"]
        y_tap = yt if open_top(k) else f"(-{yt})"
        place("MLOC", f"A{k}{side}o", {**sub, "W": "w", "L": f"max({m}, {arm}/2-{y_tap}-wf/2)"}, [f"T{num}_open"])
        place("MLIN", f"A{k}{side}b", {**sub, "W": "w", "L": f"max({m}, {arm}/2+{y_tap}-wf/2)"},
              [f"T{num}_bend", f"H{k}{side}_bend"])
        place("MTEE", f"T{num}", {**sub, "W1": "w", "W2": "w", "W3": "wf"},
              [f"T{num}_open", f"T{num}_bend", f"T{num}_feed"])
        place("MLIN", f"F{num}", {**sub, "W": "wf", "L": "fl"}, [f"P{num}", f"T{num}_feed"])
        place("PORT", f"Port{num}", {"Num": str(num), "Z0": f"{spec.z_feed:g}ohm"}, [f"P{num}"])
    # the bends
    for k, hp in enumerate(row):
        place("MLIN", f"V{k}L", {**sub, "W": "w", "L": f"max({m}, dep-w)"}, [f"H{k}L_bend", f"B{k}_l"])
        place("MMBEND", f"M{k}L", {**sub, "W": "w"}, [f"B{k}_l", f"B{k}_lh"])
        place("MLIN", f"U{k}", {**sub, "W": "w", "L": hp["b"]}, [f"B{k}_lh", f"B{k}_rh"])
        place("MMBEND", f"M{k}R", {**sub, "W": "w"}, [f"B{k}_rh", f"B{k}_r"])
        place("MLIN", f"V{k}R", {**sub, "W": "w", "L": f"max({m}, dep-w)"}, [f"B{k}_r", f"H{k}R_bend"])
    d.components = comps
    st = circuit_setup()
    a, b, step = spec.sweep
    st.sweeps[0].props.update(Distribution="Linear Step", Start=f"{a:g}GHz", Stop=f"{b:g}GHz", Step=f"{step:g}GHz")
    d.setups.append(st)
    if spec.optimization:
        op = Optimization("DiplexerCircuitOpt", "Setup1 : Sweep",
                          props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer="Conjugate Gradient"))
        op.variables = [dict(v) for v in d3.optimetrics[0].variables]
        op.goals = [dict(g) for g in d3.optimetrics[0].goals]
        d.optimetrics.append(op)
    return d
