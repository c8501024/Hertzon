"""Filter design: a parametrized tapped hairpin band-pass filter (Tools > Filter Design).

The geometry is the one of the filterbench hairpin optimizer (tapped_hairpin.py): N hairpins in a row,
every second one flipped, sharp inner and miter-cut outer corners, both feed lines tapped directly onto
the outer arms at the same height. Everything is drawn with design variables, so the optimization that
comes with the design varies the same six parameters as the filterbench runs (L, w, b, g, yP, M), with
the same ranges and the start in the middle of every range. A second optimization minimizes the same goals
with the derivative tuning model (Derivative Trust Region) over L, g and yP, a third one by space mapping
with the filter as a circuit (hairpin_circuit) as the fast coarse model.

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
      one gap per pair of neighbours (symmetric: see gap_names); `gap` is the one of the middle pair (the
      largest coupling of a symmetric filter);
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

    def gap_for(k):
        a, b = max(g_lo, 1e-4), max(g_hi, 1e-4)
        if kc(a) <= k:
            return a
        if kc(b) >= k:
            return b
        for _ in range(100):                      # k falls with the gap
            m = 0.5 * (a + b)
            a, b = (m, b) if kc(m) > k else (a, m)
        return 0.5 * (a + b)
    gap = gap_for(k)
    gaps = [gap_for(kk) for kk in ks]             # one per neighbouring pair (gap_names)
    c2 = math.pi * spec.z_feed / (2 * spec.z_res * qe)
    l_open = length / math.pi * math.acos(min(1.0, math.sqrt(min(max(c2, 0.0), 1.0))))
    yp = min(max(s_arm / 2 - l_open, -tap), tap)
    return {"gap": gap, "gaps": gaps, "yP": yp, "k": ks, "Qe": qe, "g": g, "ripple": ripple}


# ---------------------------------------------------------------------------------------------
# what the specification allows: the Chebyshev filter of N resonators with the unloaded Q of the board
# ---------------------------------------------------------------------------------------------
def unloaded_q(spec: HairpinSpec) -> float:
    """Unloaded Q of a half-wave resonator of the resonator line at the design frequency: Q = beta / (2 alpha)
    with the conductor and dielectric loss of the microstrip (no radiation: a real hairpin comes out somewhat
    lower)."""
    from ..solver.tlines import microstrip
    h, t, f0 = spec.h * 1e-3, spec.t * 1e-6, spec.f0 * 1e9
    w = synthesize(spec.z_res, h, spec.er, t, f0, tand=spec.tand, sigma=spec.sigma)
    _, gamma = microstrip(f0, w, h, t, spec.er, spec.tand, spec.sigma)
    return gamma.imag / (2 * gamma.real) if gamma.real > 0 else float("inf")


def prototype_ripple(return_loss: float) -> float:
    """Passband ripple (dB) of the Chebyshev prototype the filter is synthesized for: the return loss goal plus
    RL_MARGIN."""
    rl = return_loss + RL_MARGIN
    return -10 * math.log10(1 - 10 ** (-rl / 10))


def ideal_response(spec: HairpinSpec, freqs, n: int | None = None, qu: float | None = None):
    """S21 and S11 (complex, at freqs in GHz) of the ideal Chebyshev band-pass of n coupled resonators for the
    passband and return loss of the spec, with the unloaded Q qu (None: unloaded_q): the coupling matrix
    response of Hong & Lancaster (eq. 7.7), A = [q] + p [U] - j [m], p = j (f/f0 - f0/f) / FBW + 1 / (FBW Qu).
    What the specification allows at best with this board; the hairpins approach it after the optimization."""
    import numpy as np
    n = n or spec.n
    qu = unloaded_q(spec) if qu is None else qu
    g = chebyshev_g(n, prototype_ripple(spec.return_loss))
    lo, hi = spec.passband
    f0 = math.sqrt(lo * hi)
    fbw = (hi - lo) / f0
    m = np.zeros((n, n))
    for i in range(n - 1):
        m[i, i + 1] = m[i + 1, i] = 1 / math.sqrt(g[i + 1] * g[i + 2])      # normalized couplings k / FBW
    q1, qn = g[0] * g[1], g[n] * g[n + 1]                                  # normalized external Q (Qe FBW)
    f = np.asarray(freqs, float)
    s21, s11 = np.zeros(len(f), complex), np.zeros(len(f), complex)
    loss = 1 / (fbw * qu) if qu and math.isfinite(qu) else 0.0
    for k, fk in enumerate(f):
        p = 1j * (fk / f0 - f0 / fk) / fbw + loss
        A = p * np.eye(n) - 1j * m
        A[0, 0] += 1 / q1
        A[-1, -1] += 1 / qn
        inv = np.linalg.inv(A)
        s21[k] = 2 / math.sqrt(q1 * qn) * inv[-1, 0]
        s11[k] = 1 - 2 / q1 * inv[0, 0]
    return s21, s11


def order_table(spec: HairpinSpec, n_max: int = 9, qu: float | None = None) -> list[dict]:
    """For N = 2 .. n_max the ideal filter of ideal_response: the worst rejection in the stopbands, the insertion
    loss in the passband (worst point, with the unloaded Q), the frequencies where S21 falls below the rejection
    goal on both sides, and whether it meets the goals."""
    import numpy as np
    qu = unloaded_q(spec) if qu is None else qu
    lo, hi = spec.passband
    bw = hi - lo
    a, b = spec.sweep[0], spec.sweep[1]
    a, b = min(a, lo - 3 * bw, *(x for x, _ in spec.stopbands)), max(b, hi + 3 * bw, *(y for _, y in spec.stopbands))
    f = np.linspace(max(a, 1e-3), b, 1201)
    pb = np.linspace(lo, hi, 61)
    rows = []
    for n in range(2, n_max + 1):
        s21, _ = ideal_response(spec, f, n, qu)
        db = 20 * np.log10(np.maximum(np.abs(s21), 1e-12))
        p21, p11 = ideal_response(spec, pb, n, qu)
        il = float(-20 * np.log10(np.abs(p21)).min())
        rl = float(-20 * np.log10(np.maximum(np.abs(p11), 1e-12)).max())
        rej = min((float(-db[(f >= x) & (f <= y)].max()) for x, y in spec.stopbands if np.any((f >= x) & (f <= y))),
                  default=float("inf"))
        below = db < -spec.rejection
        left = f[(f < lo) & below]
        right = f[(f > hi) & below]
        rows.append({"n": n, "rejection": rej, "il": il, "rl": rl,
                     "edges": (float(left.max()) if left.size else None, float(right.min()) if right.size else None),
                     "ok": rej >= spec.rejection and il <= spec.insertion_loss})
    return rows


def suggest_n(spec: HairpinSpec, n_max: int = 9, qu: float | None = None) -> tuple[int, str, list[dict]]:
    """The number of resonators to use, why, and the order_table: the smallest N whose ideal filter meets the
    rejection and insertion loss goals; where none does (the board loss and the rejection pull apart), the
    smallest N that meets the rejection (its insertion loss is then above the goal), else the largest N."""
    rows = order_table(spec, n_max, qu)
    ok = next((r for r in rows if r["ok"]), None)
    if ok:
        return ok["n"], f"N = {ok['n']} is the smallest number of resonators that meets the rejection and the " \
            "insertion loss goals", rows
    rej = next((r for r in rows if r["rejection"] >= spec.rejection), None)
    if rej:
        return rej["n"], (f"No N meets both goals with this board (unloaded Q): N = {rej['n']} reaches the rejection, "
                          f"but its insertion loss is about {rej['il']:.1f} dB. Widen the passband or move the "
                          "stopbands away to need fewer resonators, or relax the insertion loss"), rows
    return rows[-1]["n"], (f"Even N = {rows[-1]['n']} does not reach the rejection: move the stopbands away from "
                           "the passband or lower the rejection goal"), rows


def fit_region(spec: HairpinSpec, step: float = 0.5) -> tuple[float, float]:
    """The smallest resonator region (and the board, the same margin around it as in the spec) in which the
    hairpins of spec.n fit without limiting their length and inner width ranges (mm)."""
    import dataclasses
    margin = max(spec.board - spec.region, 2.0)
    r = 4.0
    for _ in range(2000):
        s = dataclasses.replace(spec, region=r, board=r + margin)
        try:
            nom = nominal(s)
        except ValueError:
            r += step
            continue
        if not any(w.startswith(("Length range", "Inner width", "The start length")) for w in nom["warnings"]):
            row = s.n * (nom["inner"] + 2 * nom["trace"]) + (s.n - 1) * s.gap[1] + 2 * s.min_feature
            return r, max(r, row) + margin
        r += step
    return spec.region, spec.board


# range of dL, the length offset of the outer (tapped) resonators, in % of the length: the tap pulls them about
# 2 % below the inner ones
DL_PCT = 3.0


def _r(v: float, nd: int = 4) -> float:
    return float(f"{v:.{nd}f}")


def gap_names(n: int) -> list[str]:
    """Design variable of the gap between hairpin j and j+1 (j = 0 .. n-2). The filter is symmetric, so pairs at
    the same distance from the middle share a gap: g in the middle, g2, g3 ... outwards (n = 3: g, g; n = 4:
    g2, g, g2; n = 5: g2, g, g, g2)."""
    m = (n - 1 + 1) // 2
    out = []
    for j in range(n - 1):
        c = m - 1 - min(j, n - 2 - j)
        out.append("g" if c == 0 else f"g{c + 1}")
    return out


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
    names = gap_names(n)
    gaps = {name: _r(syn["gaps"][j] if syn else gap) for j, name in enumerate(names)}
    gap = gaps["g"]
    row = n * wo + (n - 1) * g_hi
    if row > spec.board - 2 * margin:
        raise ValueError(f"The row of hairpins ({row:.2f} mm at the largest gap) does not fit on the {spec.board:g} mm board")
    return {"feed": _r(feed), "trace": _r(trace), "lambda_g": lam, "length": _r(length), "inner": _r(b),
            "gap": _r(gap), "gaps": gaps, "miter": _r(miter), "tap": _r(tap), "yP": _r(syn["yP"]) if syn else 0.0,
            "prototype": syn,
            "ranges": {"L": (_r(l_lo), _r(l_hi)), "dL": (-_r(DL_PCT / 100 * length), _r(DL_PCT / 100 * length)), "w": (_r(w_lo), _r(w_hi)), "b": (_r(b_lo), _r(b_hi)),
                       "g": (_r(g_lo), _r(g_hi)),
                       **{name: (_r(g_lo), _r(g_hi)) for name in sorted(set(names) - {"g"})},
                       "yP": (-_r(tap), _r(tap)), "M": (_r(m_lo), _r(m_hi))},
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
DTR_VARIABLES = ("L", "dL", "g", "yP")


def _mm(v: float) -> str:
    return f"{v:g}mm"


def hairpin_design(spec: HairpinSpec) -> tuple[Design, dict]:
    """The parametrized filter design and the nominal values it starts from."""
    nom = nominal(spec)
    n = spec.n
    gn = gap_names(n)
    d = Design(name=spec.name or f"Hairpin_N{n}", units="mm")
    d.variables.update({
        "h": _mm(spec.h), "t_cu": f"{spec.t:g}um", "Wsub": _mm(spec.board), "wf": _mm(nom["feed"]),
        "L": _mm(nom["length"]), "dL": "0mm", "w": _mm(nom["trace"]), "b": _mm(nom["inner"]), "g": _mm(nom["gap"]),
        "yP": _mm(nom["yP"]), "M": f"{nom['miter']:g}", "dmin": _mm(spec.min_feature),
        # derived: outer width, effective miter, corner cut and depth, straight arm, row edge, tap
        "wo": "b+2*w", "Me": "min(M, 1-dmin/(sqrt(2)*w), (wo/2-dmin)/(2*w))", "cut": "2*Me*w",
        "dep": "max(w, cut)", "s": "(L-b-2*dep)/2", "so": "(L+dL-b-2*dep)/2",
        "xe": f"({n}*wo+{'+'.join(gn)})/2", "ycap": "max(0mm, so/2-wf/2-dmin)", "yt": "max(-ycap, min(ycap, yP))",
    })
    for name in sorted(set(gn) - {"g"}):                 # the gaps of the outer pairs (n >= 4)
        d.variables[name] = _mm(nom["gaps"][name])
    d.settings["AutoOpen"] = "Auto Open - Filter"
    d.settings["FilterAirMargin"] = "12mm"
    d.add_object("CreateBox", name="Substrate", material=spec.material, Position="-Wsub/2,-Wsub/2,0",
                 XSize="Wsub", YSize="Wsub", ZSize="h")
    gnd = d.add_object("CreateRectangle", name="Ground", Position="-Wsub/2,-Wsub/2,0", Axis="Z", XSize="Wsub",
                       YSize="Wsub")
    pins = []
    for i in range(n):
        # centre: from the left edge of the row, i hairpins and the gaps between them
        cx = f"-xe+{2 * i + 1:g}*wo/2" + "".join(f"+{x}" for x in gn[:i]) + "+"
        sign = "" if i % 2 == 0 else "-"
        ys = _Y if 0 < i < n - 1 else [y.replace("s/2", "so/2") for y in _Y]   # the outer, tapped ones: L+dL
        pts = [f"{cx}({x}),{sign}({y}),h" for x, y in zip(_X, ys)]
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
            # dL is not one of the filterbench variables: offered, not included
            op.variables.append({"name": var, "include": var != "dL", "min": f"{lo:g}{unit}", "max": f"{hi:g}{unit}"})
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
        outer_gaps = sorted(set(gap_names(n)) - {"g"})
        for v in op.variables:
            # the gaps of the outer pairs too, as far as the tuning model allows (at most 6 variables)
            extra = v["name"] in outer_gaps and len(DTR_VARIABLES) + len(outer_gaps) <= 6
            dtr.variables.append(dict(v, include=v["name"] in DTR_VARIABLES or extra))
        dtr.goals = [dict(g) for g in op.goals]
        d.optimetrics.append(dtr)
        # the same goals by space mapping with the circuit of the filter (hairpin_circuit) as the coarse model:
        # the circuit is optimized in seconds, then a few 3D solves correct its error
        sm = Optimization("HairpinSM", "Setup1 : Sweep",
                          props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer="Space Mapping",
                                     CoarseModel=circuit_name(d.name)))
        for v in op.variables:
            sm.variables.append(dict(v, include=v["name"] in SM_VARIABLES or v["name"] in outer_gaps))
        sm.goals = [dict(g) for g in op.goals]
        d.optimetrics.append(sm)
    return d, nom


# variables of the space mapping: those the circuit models well (length, outer length offset, gap, tap)
SM_VARIABLES = ("L", "dL", "g", "yP")


def circuit_name(design_name: str) -> str:
    """Name of the circuit model of a hairpin design (the coarse model of its space mapping)."""
    return f"{design_name}_Circuit"


def hairpin_project(spec: HairpinSpec) -> Project:
    d, _ = hairpin_design(spec)
    p = Project(f"HairpinFilter_N{spec.n}")
    p.designs = [d, hairpin_circuit(spec, circuit_name(d.name))]
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


# ---------------------------------------------------------------------------------------------
# the same filter as a circuit: a fast model for the start design (Circuit optimization, space mapping)
# ---------------------------------------------------------------------------------------------
# lines shorter than this are drawn at this length (a line of length zero has no admittance matrix)
_MIN_LINE = "1um"


# schematic of the circuit: distance of the two arms of a hairpin and of the two lines of a coupled pair
_HP_W, _HP_PAIR = 280, 20


def _join(comps: list, where: dict[str, list]) -> list:
    """Wires between the pins of each net (straight, or with one corner); a net label on each pin where a wire
    would touch another pin."""
    from .circuit import Component as C, Wire, _on_segment, pins
    every = [p for c in comps for p in pins(c)]
    wires = []
    for net, pts in where.items():
        a = pts[0]
        for b in pts[1:]:
            if a == b:
                continue
            for route in ([a, b] if a[0] == b[0] or a[1] == b[1] else None,
                          [a, (b[0], a[1]), b], [a, (a[0], b[1]), b]):
                if route is None:
                    continue
                hit = any(_on_segment(p, s, t) and p not in (a, b)
                          for s, t in zip(route, route[1:]) for p in every)
                if not hit:
                    wires.append(Wire([list(p) for p in route]))
                    break
            else:
                for p in (a, b):
                    comps.append(C(f"N_{net}_{p[0]}_{p[1]}", "LABEL", p[0], p[1], 0, {"Net": net}))
    return wires


def hairpin_circuit(spec: HairpinSpec, name: str = "") -> Design:
    """The tapped hairpin filter of hairpin_design as a circuit, with the same design variables (L, dL, w, b, g,
    yP, ... and the derived ones), so that values move between the two designs unchanged.

    Every arm next to another hairpin is one line of a coupled microstrip pair (MCLIN) with the arm of the
    neighbour at the gap g; the arms of the outer hairpins on the feed side are single lines with the tap (MTEE)
    and an open stub to the open end (MLOC, with the open-end extension). The bend of a hairpin is two mitered
    bends (MMBEND) joined by a line of the inner width b, the arms reach the bends through a line of the corner
    depth. The coupling of the two arms of one hairpin (at the inner width b) and the coupling beyond the nearest
    neighbour are not modelled: the circuit is a fast approximation (space mapping corrects it with full-wave
    solves). The schematic is drawn like the filter seen from above (the U of every hairpin, the coupled
    arms side by side, the feeds at the taps), the parts joined by wires."""
    from .circuit import AUTO, Component as C
    from .design import circuit_setup
    d3, nom = hairpin_design(spec)
    n = spec.n
    d = Design(name=name or circuit_name(spec.name or f"Hairpin_N{n}"), kind="circuit")
    d.variables = {k: v for k, v in d3.variables.items()}
    d.variables.update({"si": "s", "sx": "so"})     # arm length of the inner and of the outer (tapped) hairpins
    # lengths of the short lines (named, so the schematic stays readable): beyond a coupled part where one arm is
    # longer (outer next to inner hairpin), the line to the bend, the arm of a tapped hairpin above and below the tap
    d.variables.update({"lxi": f"max({_MIN_LINE}, (sx-si)/2)", "lix": f"max({_MIN_LINE}, (si-sx)/2)",
                        "lv": f"max({_MIN_LINE}, dep-w)", "lfeed": "Wsub/2-xe"})
    d.settings[AUTO] = "False"                       # the junctions are explicit parts
    comps: list = [C("Sub1", "SUBST", -40, -260, 0, {"Er": f"{spec.er:g}", "H": "h", "T": "t_cu",
                                                     "TanD": f"{spec.tand:g}", "Cond": f"{spec.sigma:g}"})]
    sub = {"Subst": "Sub1"}
    where: dict[str, list] = {}                      # net: pin positions, joined by wires below

    def place(ctype, nm, props, nets, x, y, rot=0, mirror=False, flip=False, left=None):
        """A part at (x, y) of the schematic; flip mirrors its placement top-bottom (y -> -y), for the hairpins
        that open to the bottom. left: an upright line with its text on the left (True) or right (False) side,
        the text side of an upright part follows its mirror flag (the pins stay where they are)."""
        from .circuit import TYPES, pins
        if flip:
            y, rot, mirror = -y, (-rot) % 360, not mirror
        if left is not None and mirror != left:
            if all(dx == 0 for dx, _ in TYPES[ctype].pins):     # pins on the axis: mirrored and turned by 180
                rot = (rot + 180) % 360                          # degrees is the same part flipped sideways
            mirror = left
        c = C(nm, ctype, x, y, rot, dict(props), mirror)
        comps.append(c)
        for p, net in zip(pins(c), nets):
            if net:
                where.setdefault(net, []).append(p)
        return c

    def arm(i):
        return "sx" if i in (0, n - 1) else "si"
    # The schematic is drawn as the filter looks from above: hairpin i has its arms at x = xl(i) and xl(i) + _HP_W,
    # its open ends at the top for even i (bend at the bottom), at the bottom for odd i; the coupled lines of
    # neighbouring arms stand upright between them, _HP_PAIR apart (the two lines of an MCLIN)
    def xl(i):
        return i * (_HP_W + _HP_PAIR)
    for i in range(n - 1):
        a, b = arm(i), arm(i + 1)
        lc = f"min({a},{b})"
        # line 1: right arm of hairpin i (left), line 2: left arm of hairpin i+1 (right); pins 1/3 at the top
        place("MCLIN", f"C{i + 1}", {**sub, "W": "w", "S": gap_names(n)[i], "L": lc},
              [f"H{i}R_t", f"H{i}R_b", f"H{i + 1}L_t", f"H{i + 1}L_b"], xl(i) + _HP_W + _HP_PAIR // 2, 0, 90, True)
        for j, side, other, x in ((i, "R", b, xl(i) + _HP_W), (i + 1, "L", a, xl(i + 1))):
            # the longer arm reaches beyond the coupled part by half the difference at both ends
            ext = {("sx", "si"): "lxi", ("si", "sx"): "lix"}.get((arm(j), other), _MIN_LINE)
            top_open = j % 2 == 0
            for end in ("t", "b"):
                is_open = (end == "t") == top_open
                if is_open:          # drawn below the coupled line, flipped for the top end
                    place("MLOC", f"E{j}{side}{end}", {**sub, "W": "w", "L": ext}, [f"H{j}{side}_{end}"],
                          x, 80, flip=end == "t", left=side == "R")
                else:
                    place("MLIN", f"E{j}{side}{end}", {**sub, "W": "w", "L": ext},
                          [f"H{j}{side}_{end}", f"H{j}{side}_bend"], x, 80, 90, flip=end == "t", left=side == "R")
    # the tapped arms of the outer hairpins and the feeds (drawn for a hairpin open at the top, flipped otherwise)
    for i, side, port in ((0, "L", 1), (n - 1, "R", 2)):
        top_open = i % 2 == 0
        x = xl(i) + (0 if side == "L" else _HP_W)
        out = -1 if side == "L" else 1                # the feed leaves to the left (port 1) or right (port 2)
        # tap height from the open end (yt is measured from the arm centre, +y up; a flipped hairpin is mirrored)
        y_tap = "yt" if top_open else "(-yt)"
        d.variables[f"lo{port}"] = f"max({_MIN_LINE}, {arm(i)}/2-{y_tap}-wf/2)"     # tap to the open end
        d.variables[f"lb{port}"] = f"max({_MIN_LINE}, {arm(i)}/2+{y_tap}-wf/2)"     # tap to the bend
        place("MLOC", f"A{i}{side}o", {**sub, "W": "w", "L": f"lo{port}"},
              [f"T{port}_open"], x, -80, 0, True, flip=not top_open, left=side == "R")
        place("MLIN", f"A{i}{side}b", {**sub, "W": "w", "L": f"lb{port}"},
              [f"T{port}_bend", f"H{i}{side}_bend"], x, 80, 90, flip=not top_open, left=side == "R")
        place("MTEE", f"T{port}", {**sub, "W1": "w", "W2": "w", "W3": "wf"},
              [f"T{port}_open", f"T{port}_bend", f"T{port}_feed"], x, 0, 90, side == "R", flip=not top_open)
        place("MLIN", f"F{port}", {**sub, "W": "wf", "L": "lfeed"}, [f"P{port}", f"T{port}_feed"],
              x + out * 180, 0, 0 if side == "L" else 180)
        place("PORT", f"Port{port}", {"Num": str(port), "Z0": f"{spec.z_feed:g}ohm"}, [f"P{port}"],
              x + out * 220, 40, left=side == "L")
    # the bends: arm end - line of the corner depth - mitered bend - line b - mitered bend - line - arm end
    for i in range(n):
        x0, x1, flip = xl(i), xl(i) + _HP_W, i % 2 == 1
        place("MLIN", f"V{i}L", {**sub, "W": "w", "L": "lv"}, [f"H{i}L_bend", f"B{i}_l"],
              x0, 160, 90, flip=flip, left=False)
        # (the bend is symmetric: its pins are taken in the order that keeps it upright, the text off the lines)
        place("MMBEND", f"M{i}L", {**sub, "W": "w"}, [f"B{i}_lh", f"B{i}_l"], x0, 230, 180, flip=flip)
        place("MLIN", f"U{i}", {**sub, "W": "w", "L": "b"}, [f"B{i}_lh", f"B{i}_rh"], (x0 + x1) // 2, 230,
              flip=flip)
        place("MMBEND", f"M{i}R", {**sub, "W": "w"}, [f"B{i}_rh", f"B{i}_r"], x1, 230, 0, True, flip=flip)
        place("MLIN", f"V{i}R", {**sub, "W": "w", "L": "lv"}, [f"B{i}_r", f"H{i}R_bend"],
              x1, 160, 270, flip=flip, left=True)
    d.wires = _join(comps, where)
    d.components = comps
    st = circuit_setup()
    a, b, step = spec.sweep
    st.sweeps[0].props.update(Distribution="Linear Step", Start=f"{a:g}GHz", Stop=f"{b:g}GHz", Step=f"{step:g}GHz")
    d.setups.append(st)
    if spec.optimization:
        op = Optimization("HairpinCircuitOpt", "Setup1 : Sweep",
                          props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer="Conjugate Gradient"))
        for v in d3.optimetrics[2].variables:
            op.variables.append(dict(v))
        op.goals = [dict(g) for g in d3.optimetrics[0].goals]
        d.optimetrics.append(op)
    return d
