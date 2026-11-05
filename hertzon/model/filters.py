"""Filter design: a parametrized tapped hairpin band-pass filter (Tools > Filter Design).

The geometry is the one of the filterbench hairpin optimizer (tapped_hairpin.py): N hairpins in a row,
every second one flipped, sharp inner and miter-cut outer corners, both feed lines tapped directly onto
the outer arms at the same height. Everything is drawn with design variables, so the optimization that
comes with the design varies the same six parameters as the filterbench runs (L, w, b, g, yP, M), with
the same ranges and the start in the middle of every range. A second optimization minimizes the same goals
with the derivative tuning model (Derivative Trust Region) over L, g and yP.

No Qt here: used by the dialog, the example generator and scripts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import schema
from .design import Assignment, Command, Design, Optimization, Project, Setup, Sweep
from .microstrip import coupled_lines, engine, guided_wavelength, synthesize


@dataclass
class HairpinSpec:
    n: int = 3                                   # resonators
    passband: tuple[float, float] = (2.3, 2.6)   # GHz
    stopbands: list[tuple[float, float]] | None = None   # GHz; None: from the passband (auto_bands)
    insertion_loss: float = 2.0                  # dB
    return_loss: float = 10.0
    ripple: float = 1.0
    rejection: float = 20.0
    material: str = "Rogers RO4350B (tm)"
    er: float = 3.66
    tand: float = 0.0037
    h: float = 0.508                             # mm
    t: float = 35.0                              # um copper
    board: float = 23.0                          # mm, square
    region: float = 20.0                         # mm, room for the resonators
    z_res: float = 50.0                          # ohm, resonator line
    z_feed: float = 50.0                         # ohm, feed line
    gap: tuple[float, float] = (0.15, 0.75)      # coupling gap range, mm (start: middle)
    inner: float = 1.5                           # inner width of a hairpin, mm
    inner_tol: float = 0.1
    trace_tol: float = 0.2
    length_pct: tuple[float, float, float] = (42.5, 50.0, 57.5)   # % of lambda_g: min, start, max
    miter: tuple[float, float] = (0.5, 0.8)
    min_feature: float = 0.01                    # mm
    sweep: tuple[float, float, float] | None = None               # GHz start, stop, step; None: auto_bands
    optimization: bool = True
    name: str = ""
    f_design: float = 0.0                        # GHz, the resonators are synthesized at; 0 = passband centre
    sigma: float = 5.8e7                         # S/m copper
    synthesis: bool = True                       # gap and tap from the Chebyshev prototype; False: middle of
                                                 # their ranges (the filterbench start)

    def __post_init__(self) -> None:
        bands, sweep = auto_bands(self.passband)
        if self.stopbands is None:
            self.stopbands = bands
        if self.sweep is None:
            self.sweep = sweep

    @property
    def f0(self) -> float:
        return self.f_design or (self.passband[0] + self.passband[1]) / 2


def auto_bands(passband) -> tuple[list[tuple[float, float]], tuple[float, float, float]]:
    """Stopbands and sweep (GHz) around a passband: the sweep reaches one bandwidth beyond the passband
    edges, the stopbands start a third of the bandwidth away from them (2.3-2.6 GHz: stopbands 2.0-2.2 and
    2.7-2.9 GHz, sweep 2.0-2.9 GHz in 0.01 GHz steps)."""
    lo, hi = passband
    bw = max(hi - lo, 1e-6)
    step = float(f"{bw / 30:.1g}")
    r = lambda v: round(v / step) * step  # noqa: E731
    a, b = r(max(lo - bw, step)), r(hi + bw)
    bands = [(a, r(lo - bw / 3)), (r(hi + bw / 3), b)]
    return [(float(f"{x:.6g}"), float(f"{y:.6g}")) for x, y in bands], \
        (float(f"{a:.6g}"), float(f"{b:.6g}"), step)


def chebyshev_g(n: int, ripple_db: float) -> list[float]:
    """Element values g0 .. g_n+1 of the Chebyshev low-pass prototype with the passband ripple ripple_db."""
    beta = math.log(1 / math.tanh(ripple_db / 17.37))
    gam = math.sinh(beta / (2 * n))
    a = [math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)]
    g = [1.0, 2 * a[0] / gam]
    for k in range(2, n + 1):
        g.append(4 * a[k - 2] * a[k - 1] / ((gam ** 2 + math.sin((k - 1) * math.pi / n) ** 2) * g[-1]))
    g.append(1.0 if n % 2 else 1 / math.tanh(beta / 4) ** 2)
    return g


# the prototype is designed for a return loss this much better than the goal (margin for the model error)
RL_MARGIN = 3.0


def synthesis(spec: "HairpinSpec", trace: float, length: float, s_arm: float, gap_range, tap: float) -> dict:
    """Gap and tap of the Chebyshev band-pass (all lengths in mm): the prototype for the passband with the
    return loss goal + RL_MARGIN gives the couplings k = FBW / sqrt(g_i g_i+1) and the external Q
    Qe = g0 g1 / FBW (Hong & Lancaster, Microstrip Filters for RF/Microwave Applications, ch. 6).

    - gap: the arms of neighbouring hairpins form a coupled line of about a quarter wavelength, so
      k = (2 / pi) (Zoe - Zoo) / (Zoe + Zoo) with the even and odd mode impedances of the pair (Garg-Bahl);
      one gap for all resonators: the coupling of the middle pair (the largest of a symmetric filter);
    - tap: a half-wave resonator of impedance Zr loaded with the feed impedance R at the distance l from its
      open end has Qe = pi R / (2 Zr cos^2(pi l / L)); l is measured along the arm from the open end."""
    n = spec.n
    rl = spec.return_loss + RL_MARGIN
    ripple = -10 * math.log10(1 - 10 ** (-rl / 10))
    g = chebyshev_g(n, ripple)
    lo, hi = spec.passband
    fbw = (hi - lo) / spec.f0
    ks = [fbw / math.sqrt(g[i] * g[i + 1]) for i in range(1, n)]
    k = max(ks)
    qe = g[0] * g[1] / fbw
    h, er = spec.h, spec.er

    def kc(s):
        ze, zo, _, _ = coupled_lines(trace * 1e-3, s * 1e-3, h * 1e-3, er)
        return 2 / math.pi * (ze - zo) / (ze + zo)
    g_lo, g_hi = gap_range
    a, b = max(g_lo, 1e-4), max(g_hi, 1e-4)
    if kc(a) <= k:
        gap = a
    elif kc(b) >= k:
        gap = b
    else:
        for _ in range(100):                      # k falls with the gap
            m = 0.5 * (a + b)
            a, b = (m, b) if kc(m) > k else (a, m)
        gap = 0.5 * (a + b)
    c2 = math.pi * spec.z_feed / (2 * spec.z_res * qe)
    l_open = length / math.pi * math.acos(min(1.0, math.sqrt(min(max(c2, 0.0), 1.0))))
    yp = min(max(s_arm / 2 - l_open, -tap), tap)
    return {"gap": gap, "yP": yp, "k": ks, "Qe": qe, "g": g, "ripple": ripple}


def _r(v: float, nd: int = 4) -> float:
    return float(f"{v:.{nd}f}")


def nominal(spec: HairpinSpec) -> dict:
    """Start values and optimization ranges in mm (and warnings) for the specification."""
    if not 2 <= spec.n <= 9:
        raise ValueError("2 to 9 resonators")
    if not spec.passband[1] > spec.passband[0] > 0:
        raise ValueError("The passband needs start < stop")
    h, t, er, f0 = spec.h * 1e-3, spec.t * 1e-6, spec.er, spec.f0 * 1e9
    margin = spec.min_feature
    warnings = []
    loss = dict(tand=spec.tand, sigma=spec.sigma)
    feed = synthesize(spec.z_feed, h, er, t, f0, **loss) * 1e3
    trace = synthesize(spec.z_res, h, er, t, f0, **loss) * 1e3
    w_lo, w_hi = max(margin, trace * (1 - spec.trace_tol)), max(margin, trace * (1 + spec.trace_tol))
    g_lo, g_hi = spec.gap
    gap = (g_lo + g_hi) / 2
    n, r = spec.n, spec.region
    available = (r - 2 * margin - (n - 1) * gap) / n
    if available < 2 * trace + margin:
        raise ValueError(f"No room for {n} hairpins in {r:g} mm: make the region larger or use fewer resonators")
    cap = available - 2 * trace
    b_lo = min(cap, max(margin, spec.inner * (1 - spec.inner_tol)))
    b_hi = min(cap, max(b_lo, spec.inner * (1 + spec.inner_tol)))
    if b_hi < spec.inner * (1 + spec.inner_tol) - 1e-9:
        warnings.append(f"Inner width limited by the region: {b_lo:.3f} to {b_hi:.3f} mm")
    b = min(max(spec.inner, b_lo), b_hi)
    m_lo, m_hi = spec.miter
    miter = (m_lo + m_hi) / 2
    wo = b + 2 * trace
    me = min(miter, 1 - margin / (math.sqrt(2) * trace), (wo / 2 - margin) / (2 * trace))
    dep = max(trace, 2 * me * trace)
    bend = b + trace + 2 * dep - trace
    hmax, hmin = r - 2 * dep - 2 * margin, feed + 2 * margin
    if hmin > hmax:
        raise ValueError("The hairpins are too short for the feed lines and corners: make the region larger")
    lam = guided_wavelength(trace * 1e-3, h, er, t, f0, **loss) * 1e3
    p_lo, p0, p_hi = spec.length_pct
    lmin, lmax = 2 * hmin + bend, 2 * hmax + bend
    l_lo, l_hi = (min(max(lam * p / 100, lmin), lmax) for p in (p_lo, p_hi))
    if l_lo > lam * p_lo / 100 + 1e-9 or l_hi < lam * p_hi / 100 - 1e-9:
        warnings.append(f"Length range limited by the region, feed and corners: {l_lo:.3f} to {l_hi:.3f} mm "
                        f"instead of {lam * p_lo / 100:.3f} to {lam * p_hi / 100:.3f} mm")
    if l_hi <= l_lo:
        raise ValueError("No room for the hairpin length: make the region larger")
    length = min(max(lam * p0 / 100, l_lo), l_hi)
    if abs(length - lam * p0 / 100) > 1e-9:
        warnings.append(f"The start length {lam * p0 / 100:.3f} mm does not fit; starting at {length:.3f} mm")
    straight = (length - bend) / 2
    tap = max(0.0, straight / 2 - feed / 2 - margin)
    syn = None
    if spec.synthesis:
        # the arm of the start design: L - b - 2 * depth of the bend (variable s of hairpin_design)
        arm = (length - b - 2 * dep) / 2
        syn = synthesis(spec, trace, length, arm, (g_lo, g_hi), tap)
        gap = syn["gap"]
    row = n * wo + (n - 1) * g_hi
    if row > spec.board - 2 * margin:
        raise ValueError(f"The row of hairpins ({row:.2f} mm at the largest gap) does not fit on the {spec.board:g} mm board")
    return {"feed": _r(feed), "trace": _r(trace), "lambda_g": lam, "length": _r(length), "inner": _r(b),
            "gap": _r(gap), "miter": _r(miter), "tap": _r(tap), "yP": _r(syn["yP"]) if syn else 0.0,
            "prototype": syn,
            "ranges": {"L": (_r(l_lo), _r(l_hi)), "w": (_r(w_lo), _r(w_hi)), "b": (_r(b_lo), _r(b_hi)),
                       "g": (_r(g_lo), _r(g_hi)), "yP": (-_r(tap), _r(tap)), "M": (_r(m_lo), _r(m_hi))},
            "warnings": warnings, "synthesis": engine()}


def filter_mask_goals(passband, stopbands, il: float, rl: float, ripple: float, rejection: float,
                      ports=(1, 2)) -> list[dict]:
    """Optimization goals of a band-pass mask (filterbench): insertion loss, ripple and return loss in the
    passband, rejection in the stopbands; both directions with weight 1/2 each. Frequencies as text."""
    a, b = passband
    i, j = ports
    g = []
    for s in (f"dB(S({j},{i}))", f"dB(S({i},{j}))"):
        g.append({"calc": s, "start": a, "stop": b, "cond": ">=", "value": f"{-il:g}", "weight": "0.5"})
        g.append({"calc": s, "start": a, "stop": b, "cond": "Ripple <=", "value": f"{ripple:g}", "weight": "0.5"})
    for s in (f"dB(S({i},{i}))", f"dB(S({j},{j}))"):
        g.append({"calc": s, "start": a, "stop": b, "cond": "<=", "value": f"{-rl:g}", "weight": "0.5"})
    for lo, hi in stopbands:
        for s in (f"dB(S({j},{i}))", f"dB(S({i},{j}))"):
            g.append({"calc": s, "start": lo, "stop": hi, "cond": "<=", "value": f"{-rejection:g}", "weight": "0.5"})
    return g


# outline of one hairpin around its centre, opening to +y (filterbench polygon order)
_X = ["-wo/2", "-wo/2", "-wo/2+cut", "wo/2-cut", "wo/2", "wo/2", "wo/2-w", "wo/2-w", "-wo/2+w", "-wo/2+w"]
_Y = ["s/2", "-s/2-dep+cut", "-s/2-dep", "-s/2-dep", "-s/2-dep+cut", "s/2", "s/2", "-s/2-dep+w", "-s/2-dep+w", "s/2"]


# variables of the derivative trust region optimization that are included from the start
DTR_VARIABLES = ("L", "g", "yP")


def _mm(v: float) -> str:
    return f"{v:g}mm"


def hairpin_design(spec: HairpinSpec) -> tuple[Design, dict]:
    """The parametrized filter design and the nominal values it starts from."""
    nom = nominal(spec)
    n = spec.n
    d = Design(name=spec.name or f"Hairpin_N{n}", units="mm")
    d.variables.update({
        "h": _mm(spec.h), "t_cu": f"{spec.t:g}um", "Wsub": _mm(spec.board), "wf": _mm(nom["feed"]),
        "L": _mm(nom["length"]), "w": _mm(nom["trace"]), "b": _mm(nom["inner"]), "g": _mm(nom["gap"]),
        "yP": _mm(nom["yP"]), "M": f"{nom['miter']:g}", "dmin": _mm(spec.min_feature),
        # derived: outer width, effective miter, corner cut and depth, straight arm, pitch, row edge, tap
        "wo": "b+2*w", "Me": "min(M, 1-dmin/(sqrt(2)*w), (wo/2-dmin)/(2*w))", "cut": "2*Me*w",
        "dep": "max(w, cut)", "s": "(L-b-2*dep)/2", "pitch": "wo+g", "xe": f"({n}*wo+{n - 1}*g)/2",
        "ycap": "max(0mm, s/2-wf/2-dmin)", "yt": "max(-ycap, min(ycap, yP))",
    })
    d.settings["AutoOpen"] = "Auto Open - Filter"
    d.settings["FilterAirMargin"] = "12mm"
    d.add_object("CreateBox", name="Substrate", material=spec.material, Position="-Wsub/2,-Wsub/2,0",
                 XSize="Wsub", YSize="Wsub", ZSize="h")
    gnd = d.add_object("CreateRectangle", name="Ground", Position="-Wsub/2,-Wsub/2,0", Axis="Z", XSize="Wsub",
                       YSize="Wsub")
    pins = []
    for i in range(n):
        k = i - (n - 1) / 2
        cx = "" if k == 0 else f"{k:g}*pitch+"
        sign = "" if i % 2 == 0 else "-"
        pts = [f"{cx}({x}),{sign}({y}),h" for x, y in zip(_X, _Y)]
        if sign:                                 # mirrored: keep the outline counter-clockwise (+z normal)
            pts = pts[:1] + pts[:0:-1]
        pts = ";".join(pts)
        pins.append(d.add_object("CreatePolyline", name=f"Hairpin{i + 1}", material="vacuum", Points=pts,
                                 Closed="True", Covered="True"))
    for name, pos, pin in (("Feed1", "-Wsub/2,yt-wf/2,h", pins[0]), ("Feed2", "xe-w/2,yt-wf/2,h", pins[-1])):
        f = d.add_object("CreateRectangle", name=name, Position=pos, Axis="Z", XSize="Wsub/2-xe+w/2", YSize="wf")
        pin.history.append(Command.new("Unite", Tools=f.name))
        f.consumed_by = pin.name
    p1 = d.add_object("CreateRectangle", name="Port1", Position="-Wsub/2,yt-wf/2,0", Axis="X", YSize="wf", ZSize="h")
    p2 = d.add_object("CreateRectangle", name="Port2", Position="Wsub/2,yt-wf/2,0", Axis="X", YSize="wf", ZSize="h")

    def assign(lst, table, kind, name, objects, **props):
        p = schema.defaults(table[kind])
        p.update(props)
        lst.append(Assignment(name, kind, p, objects=list(objects)))

    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Top", [p.name for p in pins],
           UseThickness="True", Thickness="t_cu", TwoSided="True")
    assign(d.boundaries, schema.BOUNDARIES, "Finite Conductivity", "Copper_Ground", [gnd.name],
           UseThickness="True", Thickness="t_cu", TwoSided="True")
    assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "1", [p1.name], PortNumber="1",
           IntegrationLine="-Wsub/2,yt,0;-Wsub/2,yt,h")
    assign(d.excitations, schema.EXCITATIONS, "Lumped Port", "2", [p2.name], PortNumber="2",
           IntegrationLine="Wsub/2,yt,0;Wsub/2,yt,h")
    # a length based mesh on the copper gives a start mesh that resolves the coupling gaps: the adaptive
    # passes then converge in a few steps (needed for the many evaluations of the optimization)
    assign(d.mesh_ops, schema.MESH_OPS, "Length Based On Selection", "Length_Copper", [p.name for p in pins],
           MaxLength="w/2")
    st = Setup("Setup1")
    st.props.update(Frequency=f"{spec.f0:g}GHz", MaximumPasses="10", MaximumDeltaS="0.05")
    sw = Sweep("Sweep")
    a, b, step = spec.sweep
    sw.props.update(SweepType="Interpolating", Distribution="Linear Step", Start=f"{a:g}GHz", Stop=f"{b:g}GHz",
                    Step=f"{step:g}GHz", ErrorTolerance="1", MaxSolutions="40")
    st.sweeps.append(sw)
    d.setups.append(st)
    if spec.optimization:
        op = Optimization("HairpinCG", "Setup1 : Sweep")
        for var, (lo, hi) in nom["ranges"].items():
            unit = "" if var == "M" else "mm"
            op.variables.append({"name": var, "include": True, "min": f"{lo:g}{unit}", "max": f"{hi:g}{unit}"})
        op.goals = filter_mask_goals((f"{spec.passband[0]:g}GHz", f"{spec.passband[1]:g}GHz"),
                                     [(f"{lo:g}GHz", f"{hi:g}GHz") for lo, hi in spec.stopbands],
                                     spec.insertion_loss, spec.return_loss, spec.ripple, spec.rejection)
        d.optimetrics.append(op)
        # the same goals with the derivative tuning model: every round one solve and one tuning model on its
        # adaptive mesh (the mesh is morphed, not remeshed). Length, gap and tap are included: they set the
        # centre frequency, the coupling and the match. The trace width, inner width and miter can be added
        # where the memory allows (the volume matrices grow with the square of the number of variables)
        dtr = Optimization("HairpinDTR", "Setup1 : Sweep",
                           props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer="Derivative Trust Region"))
        for v in op.variables:
            dtr.variables.append(dict(v, include=v["name"] in DTR_VARIABLES))
        dtr.goals = [dict(g) for g in op.goals]
        d.optimetrics.append(dtr)
    return d, nom


def hairpin_project(spec: HairpinSpec) -> Project:
    d, _ = hairpin_design(spec)
    p = Project(f"HairpinFilter_N{spec.n}")
    p.designs = [d]
    return p


def outlines(design: Design, project: Project | None = None) -> dict[str, list[tuple[float, float]]]:
    """Top view (x, y in mm) of the hairpins and feeds of a design made by hairpin_design (preview)."""
    ev = design.evaluator(project)
    out = {}
    for o in design.objects:
        p = o.history[0].props if o.history else {}
        if o.name.startswith("Hairpin"):
            out[o.name] = [(x * 1e3, y * 1e3) for x, y, _ in ev.points(p["Points"])]
        elif o.name.startswith("Feed"):
            x, y, _ = ev.point(p["Position"])
            dx, dy = ev.length(p["XSize"]), ev.length(p["YSize"])
            out[o.name] = [(1e3 * a, 1e3 * b) for a, b in ((x, y), (x + dx, y), (x + dx, y + dy), (x, y + dy))]
    return out
