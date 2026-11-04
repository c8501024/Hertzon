"""Transmission line models of the circuit elements: characteristic impedance and propagation constant.

Every function returns (Z0, gamma) at the frequency f (Hz): Z0 in ohm (complex where the line is lossy only
through gamma), gamma = alpha + j beta in 1/m. All lengths are in metres.

Microstrip: static Z0 and effective permittivity after Hammerstad and Jensen (1980) with the strip thickness
correction, frequency dispersion of the effective permittivity after Kirschning and Jansen (1982), Z0 over
frequency after Bianco et al. Conductor loss from the surface resistance (smooth metal), dielectric loss from
the loss tangent. Coupled microstrip: even and odd mode capacitances after Garg and Bahl (1979), static.
Microstrip T junction: equivalent circuit after Hammerstad (1981): reference plane shifts on the three arms,
transformers on the main arms and a shunt susceptance at the junction. Corner and 50 % mitered bend after
Kirschning, Jansen and Koster (1983), impedance step, gap (with the open end capacitance after Kirschning and
Jansen) and cross junction from the closed forms collected in the Qucs technical documentation.
Stripline: exact zero-thickness impedance (Cohn), conductor loss after Pozar. Coaxial line: exact."""
from __future__ import annotations

import math

import numpy as np

C0 = 299792458.0
MU0 = 4e-7 * math.pi
EPS0 = 1.0 / (MU0 * C0 ** 2)
ETA0 = MU0 * C0


def surface_resistance(f: float, cond: float) -> float:
    if cond <= 0 or f <= 0:
        return 0.0
    return math.sqrt(math.pi * f * MU0 / cond)


# ---- microstrip ---------------------------------------------------------------------------------------
def _z01(u: float) -> float:
    """Impedance of a zero-thickness strip of width ratio u in air (Hammerstad-Jensen)."""
    fu = 6 + (2 * math.pi - 6) * math.exp(-((30.666 / u) ** 0.7528))
    return ETA0 / (2 * math.pi) * math.log(fu / u + math.sqrt(1 + 4 / u ** 2))


def _eeff0(u: float, er: float) -> float:
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def microstrip_static(w: float, h: float, t: float, er: float) -> tuple[float, float]:
    """(Z0, effective permittivity) at low frequency, with the strip thickness t."""
    u = w / h
    if t > 0:
        tn = t / h
        du1 = tn / math.pi * math.log(1 + 4 * math.e / (tn * (1 / math.tanh(math.sqrt(6.517 * u))) ** 2))
        dur = 0.5 * (1 + 1 / math.cosh(math.sqrt(max(er - 1, 0.0)))) * du1
    else:
        du1 = dur = 0.0
    u1, ur = u + du1, u + dur
    ee = _eeff0(ur, er)
    z0 = _z01(ur) / math.sqrt(ee)
    ee = ee * (_z01(u1) / _z01(ur)) ** 2
    return z0, ee


def microstrip_eeff(f: float, w: float, h: float, er: float, ee0: float) -> float:
    """Effective permittivity over frequency (Kirschning-Jansen)."""
    u = w / h
    fn = f * 1e-9 * h * 1e3                       # GHz * mm
    p1 = 0.27488 + (0.6315 + 0.525 / (1 + 0.0157 * fn) ** 20) * u - 0.065683 * math.exp(-8.7513 * u)
    p2 = 0.33622 * (1 - math.exp(-0.03442 * er))
    p3 = 0.0363 * math.exp(-4.6 * u) * (1 - math.exp(-((fn / 38.7) ** 4.97)))
    p4 = 1 + 2.751 * (1 - math.exp(-((er / 15.916) ** 8)))
    p = p1 * p2 * ((0.1844 + p3 * p4) * fn) ** 1.5763
    return er - (er - ee0) / (1 + p)


def microstrip(f: float, w: float, h: float, t: float, er: float, tand: float, cond: float):
    z0s, ee0 = microstrip_static(w, h, t, er)
    ee = microstrip_eeff(f, w, h, er, ee0)
    z0 = z0s * math.sqrt(ee0 / ee) * (ee - 1) / (ee0 - 1) if ee0 > 1 + 1e-9 else z0s
    beta = 2 * math.pi * f * math.sqrt(ee) / C0
    ad = math.pi * f / C0 * er * (ee - 1) / (math.sqrt(ee) * (er - 1)) * tand if er > 1 else 0.0
    ac = surface_resistance(f, cond) / (z0 * w)
    return z0, complex(ad + ac, beta)


def open_end_extension(w: float, h: float, t: float, er: float) -> float:
    """Length extension of an open microstrip end (Hammerstad)."""
    _, ee = microstrip_static(w, h, t, er)
    u = w / h
    return 0.412 * h * (ee + 0.3) * (u + 0.264) / ((ee - 0.258) * (u + 0.8))


def coupled_microstrip(f: float, w: float, s: float, h: float, t: float, er: float, tand: float, cond: float):
    """((Z0e, gamma_e), (Z0o, gamma_o)) of symmetric coupled microstrip (Garg-Bahl, static modes)."""
    from scipy.special import ellipk

    def caps(eps: float) -> tuple[float, float]:
        z0, ee = microstrip_static(w, h, t, eps)
        cp = EPS0 * eps * w / h
        cf = 0.5 * (math.sqrt(ee) / (C0 * z0) - cp)
        a = math.exp(-0.1 * math.exp(2.33 - 2.53 * w / h))
        cf2 = cf / (1 + a * (h / s) * math.tanh(8 * s / h)) * math.sqrt(eps / ee)
        k = (s / h) / (s / h + 2 * w / h)
        kp = math.sqrt(1 - k * k)
        cga = EPS0 * ellipk(kp * kp) / ellipk(k * k)
        cgd = EPS0 * eps / math.pi * math.log(1 / math.tanh(math.pi * s / (4 * h))) + \
            0.65 * cf * (0.02 * math.sqrt(eps) / (s / h) + 1 - eps ** -2)
        return cp + cf + cf2, cp + cf + cga + cgd
    ce, co = caps(er)
    ce1, co1 = caps(1.0)
    rs = surface_resistance(f, cond)
    out = []
    for c, c1 in ((ce, ce1), (co, co1)):
        z0 = 1 / (C0 * math.sqrt(c * c1))
        ee = c / c1
        beta = 2 * math.pi * f * math.sqrt(ee) / C0
        ad = math.pi * f / C0 * er * (ee - 1) / (math.sqrt(ee) * (er - 1)) * tand if er > 1 else 0.0
        out.append((z0, complex(ad + rs / (z0 * w), beta)))
    return out[0], out[1]


def tee_y(f: float, w1: float, w2: float, w3: float, h: float, t: float, er: float, tand: float,
          cond: float) -> np.ndarray:
    """3x3 Y matrix of a microstrip T junction, pins (main arm 1, main arm 2, branch 3) at the edges of the
    junction: main arms 0.5*w3 from its centre, the branch 0.5*max(w1, w2) (Hammerstad's equivalent circuit)."""
    f = max(f, 1.0)
    arms = []
    for w in (w1, w2, w3):
        z0, g = microstrip(f, w, h, t, er, tand, cond)
        ee = (g.imag * C0 / (2 * math.pi * f)) ** 2
        arms.append((z0, g, ee))
    (za, _, _), (zb, _, _), (z2, _, _) = arms
    d = [ETA0 / math.sqrt(e) * h / z for z, _, e in arms]           # equivalent parallel plate widths
    fp = [0.4e6 * z / h for z, _, _ in arms]                          # Hz, h in m
    lam = [C0 / math.sqrt(e) / f for _, _, e in arms]
    da = 0.055 * d[2] * za / z2 * (1 - 2 * za / z2 * (f / fp[0]) ** 2)
    db = 0.055 * d[2] * zb / z2 * (1 - 2 * zb / z2 * (f / fp[1]) ** 2)
    r = math.sqrt(za * zb) / z2
    q = f * f / (fp[0] * fp[1])
    d2 = math.sqrt(d[0] * d[1]) * (0.5 - r * (0.05 + 0.7 * math.exp(-1.6 * r) + 0.25 * r * q - 0.17 * math.log(r)))
    ta2 = max(1 - math.pi * (f / fp[0]) ** 2 * ((za / z2) ** 2 / 12 + (0.5 - d2 / d[0]) ** 2), 1e-6)
    tb2 = max(1 - math.pi * (f / fp[1]) ** 2 * ((zb / z2) ** 2 / 12 + (0.5 - d2 / d[1]) ** 2), 1e-6)
    bt = 5.5 * math.sqrt(d[0] * d[1] / lam[0] / lam[1]) * (er + 2) / er / z2 / math.sqrt(ta2 * tb2) * \
        math.sqrt(max(da * db, 0.0)) / d[2] * \
        (1 + 0.9 * math.log(r) + 4.5 * r * q - 4.4 * math.exp(-1.3 * r) - 20 * (z2 / ETA0) ** 2)
    if abs(bt) < 1e-15:
        bt = 1e-15
    # the ideal junction: all three arms on one node through the transformers, the susceptance to ground
    v = np.array([1 / math.sqrt(ta2), 1 / math.sqrt(tb2), 1.0])
    zc = np.outer(v, v) / (1j * bt)
    # arm lines (a negative length is a phase shift) between the pins and the junction
    lengths = (0.5 * w3 - da, 0.5 * w3 - db, 0.5 * max(w1, w2) - d2)
    a_, b_, c_, d_ = (np.zeros((3, 3), complex) for _ in range(4))
    for k, ((z, g, _), ln) in enumerate(zip(arms, lengths)):
        gl = _regular(g * ln)
        a_[k, k], b_[k, k] = np.cosh(gl), z * np.sinh(gl)
        c_[k, k], d_[k, k] = np.sinh(gl) / z, np.cosh(gl)
    return (c_ @ zc + d_) @ np.linalg.inv(a_ @ zc + b_)


# ---- stripline and coax -------------------------------------------------------------------------------
def stripline(f: float, w: float, b: float, t: float, er: float, tand: float, cond: float):
    from scipy.special import ellipk
    k = 1 / math.cosh(math.pi * w / (2 * b))
    kp = math.tanh(math.pi * w / (2 * b))
    z0 = ETA0 / (4 * math.sqrt(er)) * ellipk(k * k) / ellipk(kp * kp)
    beta = 2 * math.pi * f * math.sqrt(er) / C0
    ad = math.pi * f * math.sqrt(er) / C0 * tand
    rs = surface_resistance(f, cond)
    tt = min(max(t, 1e-3 * b), 0.5 * b)
    if math.sqrt(er) * z0 < 120:
        a = 1 + 2 * w / (b - tt) + (b + tt) / (math.pi * (b - tt)) * math.log((2 * b - tt) / tt)
        ac = 2.7e-3 * rs * er * z0 / (30 * math.pi * (b - tt)) * a
    else:
        bb = 1 + b / (0.5 * w + 0.7 * tt) * (0.5 + 0.414 * tt / w + math.log(4 * math.pi * w / tt) / (2 * math.pi))
        ac = 0.16 * rs / (z0 * b) * bb
    return z0, complex(ad + ac, beta)


def coax(f: float, di: float, do: float, er: float, tand: float, cond: float):
    eta = ETA0 / math.sqrt(er)
    z0 = eta / (2 * math.pi) * math.log(do / di)
    beta = 2 * math.pi * f * math.sqrt(er) / C0
    ad = math.pi * f * math.sqrt(er) / C0 * tand
    ac = surface_resistance(f, cond) / (2 * eta) * (2 / di + 2 / do) / math.log(do / di)
    return z0, complex(ad + ac, beta)


# ---- microstrip discontinuities ---------------------------------------------------------------------------
def _reduce(y: np.ndarray, keep: int) -> np.ndarray:
    """Kron reduction to the first `keep` nodes."""
    a, b = y[:keep, :keep], y[:keep, keep:]
    c, d = y[keep:, :keep], y[keep:, keep:]
    return a - b @ np.linalg.solve(d, c)


def _tee_net(z1: complex, z2: complex, zs: complex) -> np.ndarray:
    """Y of a T network: z1 from pin 1 and z2 from pin 2 to a node, zs from that node to ground."""
    z = np.array([[z1 + zs, zs], [zs, z2 + zs]], complex)
    return np.linalg.inv(z)


def corner_y(f: float, w: float, h: float, er: float, mitered: bool = False) -> np.ndarray:
    """2x2 Y of a 90 degree microstrip corner (unmitered or 50 % mitered), pins at the edges of the corner
    square: a series inductance on each arm and a shunt capacitance."""
    om = 2 * math.pi * max(f, 1.0)
    u = w / h
    if mitered:
        c = w * ((3.93 * er + 0.62) * u + (7.6 * er + 3.80)) * 1e-12
        ind = 440 * h * (1 - 1.062 * math.exp(-0.177 * u ** 0.947)) * 1e-9
    else:
        c = w * ((10.35 * er + 2.5) * u + (2.6 * er + 5.64)) * 1e-12
        ind = 220 * h * (1 - 1.35 * math.exp(-0.18 * u ** 1.39)) * 1e-9
    zl = 1j * om * ind                      # Z11 = jwL + 1/(jwC): the inductance L in each arm
    return _tee_net(zl, zl, 1 / (1j * om * max(c, 1e-18)))


def step_y(f: float, w1: float, w2: float, h: float, t: float, er: float, tand: float, cond: float) -> np.ndarray:
    """2x2 Y of a microstrip width step from w1 (pin 1) to w2 (pin 2) at one reference plane."""
    om = 2 * math.pi * max(f, 1.0)
    flip = w1 < w2
    wa, wb = (w2, w1) if flip else (w1, w2)            # the wide side first
    r = wa / wb
    if r < 1 + 1e-6:
        return np.array([[1e9, -1e9], [-1e9, 1e9]], complex)
    lg = math.log10(er)
    cs = max(math.sqrt(wa * wb) * (r * (10.1 * lg + 2.33) - 12.6 * lg - 3.17), 0.0) * 1e-12
    ls = max(h * ((r - 1) * (40.5 + 0.2 * (r - 1)) - 75 * math.log10(r)), 0.0) * 1e-9
    lw = []
    for w in (wa, wb):
        z0, g = microstrip(max(f, 1.0), w, h, t, er, tand, cond)
        lw.append(z0 * g.imag / om)                    # Z0 sqrt(eeff) / c0, inductance per length
    la, lb = (ls * lw[0] / sum(lw), ls * lw[1] / sum(lw))
    za, zb = 1j * om * la + 1e-9, 1j * om * lb + 1e-9
    zs = 1 / (1j * om * cs) if cs > 0 else 1e12
    y = _tee_net(za, zb, zs)
    return y[::-1, ::-1].copy() if flip else y


def open_end_dl(f: float, w: float, h: float, t: float, er: float) -> float:
    """Length extension of an open microstrip end after Kirschning, Jansen and Koster (dispersive eeff)."""
    _, ee0 = microstrip_static(w, h, t, er)
    ee = microstrip_eeff(max(f, 1.0), w, h, er, ee0)
    u = w / h
    q6 = ee ** 0.81
    q7 = u ** 0.8544
    q1 = 0.434907 * (q6 + 0.26) / (q6 - 0.189) * (q7 + 0.236) / (q7 + 0.87)
    q2 = u ** 0.371 / (2.358 * er + 1) + 1
    q3 = math.atan(0.084 * u ** (1.9413 / q2)) * 0.5274 / ee ** 0.9236 + 1
    q4 = 0.0377 * (6 - 5 * math.exp(0.036 * (1 - er))) * math.atan(0.067 * u ** 1.456) + 1
    q5 = 1 - 0.218 * math.exp(-7.5 * u)
    return h * q1 * q3 * q5 / q4


def gap_y(f: float, w1: float, w2: float, s: float, h: float, t: float, er: float, tand: float,
          cond: float) -> np.ndarray:
    """2x2 Y of a gap s between the ends of two microstrip lines (pi network of capacitances)."""
    om = 2 * math.pi * max(f, 1.0)
    flip = w2 < w1
    wa, wb = (w2, w1) if flip else (w1, w2)            # the narrow side first (1 <= wb/wa)
    a, b, sn = wa / h, wb / h, s / h
    q5 = 1.23 / (1 + 0.12 * (b / a - 1) ** 0.9)
    q1 = 0.04598 * (0.03 + a ** q5) * (0.272 + 0.07 * er)
    q2 = 0.107 * (a + 9) * sn ** 3.23 + 2.09 * sn ** 1.05 * (1.5 + 0.3 * a) / (1 + 0.6 * a)
    q3 = math.exp(-0.5978 * (b / a) ** 1.35) - 0.55
    q4 = math.exp(-0.5978 * (a / b) ** 1.35) - 0.55
    cs = 500e-12 * h * math.exp(-1.86 * sn) * q1 * (1 + 4.19 * (1 - math.exp(-0.785 * math.sqrt(1 / a) * b / a)))
    cend = []
    for w in (wa, wb):
        z0, g = microstrip(max(f, 1.0), w, h, t, er, tand, cond)
        cend.append(g.imag / om / z0 * open_end_dl(f, w, h, t, er))    # sqrt(eeff)/(c0 Z0) * dl
    cp1 = cend[0] * (q2 + q3) / (q2 + 1)
    cp2 = cend[1] * (q2 + q4) / (q2 + 1)
    y = 1j * om * np.array([[cp1 + cs, -cs], [-cs, cp2 + cs]], complex)
    return y[::-1, ::-1].copy() if flip else y


def cross_y(f: float, w: tuple, h: float, t: float, er: float, tand: float, cond: float) -> np.ndarray:
    """4x4 Y of a microstrip cross junction, pins 1 and 3 opposite, 2 and 4 opposite, at the edges of the
    crossing (inductances of the arms, a centre inductance and a capacitance at each arm, corrected from the
    er = 9.9 data to the substrate)."""
    om = 2 * math.pi * max(f, 1.0)
    wn = [x / h for x in w]

    def cap(wa_h, wb_h, width):
        x = math.log10(wa_h) * (86.6 * wb_h - 30.9 * math.sqrt(wb_h) + 367) + wb_h ** 3 + 74 * wb_h + 130
        return 1e-12 * width * (0.25 * x * wa_h ** (-1 / 3) - 60 + 1 / (2 * wb_h) - 0.375 * wa_h * (1 - wb_h))

    def ind(wa_h, wb_h):
        yy = 165.6 * wb_h + 31.2 * math.sqrt(wb_h) - 11.8 * wb_h ** 2
        return 1e-9 * h * (yy * wa_h - 32 * wb_h + 3) * wa_h ** -1.5

    def correction(width):
        z99, g99 = microstrip(max(f, 1.0), width, h, t, 9.9, 0.0, 0.0)
        z, g = microstrip(max(f, 1.0), width, h, t, er, 0.0, 0.0)
        return z99 / z * g.imag / g99.imag                 # Z0(9.9)/Z0(er) * sqrt(eeff(er)/eeff(9.9))

    across = [(wn[1] + wn[3]) / 2, (wn[0] + wn[2]) / 2, (wn[1] + wn[3]) / 2, (wn[0] + wn[2]) / 2]
    cs = [max(cap(wn[k], across[k], w[k]) * correction(w[k]), 0.0) for k in range(4)]
    ls = [ind(wn[k], across[k]) for k in range(4)]
    w13, w24 = (wn[0] + wn[2]) / 2, (wn[1] + wn[3]) / 2
    l5 = 0.8 * 1e-9 * h * (5 * w24 * math.cos(math.pi / 2 * (1.5 - w13)) - (1 + 7 / w13) / w24 - 337.5)
    y = np.zeros((6, 6), complex)                      # pins 0..3, centre nodes 4 (arms 1, 3) and 5 (arms 2, 4)

    def stamp(a, b, yy):
        y[a, a] += yy
        y[b, b] += yy
        y[a, b] -= yy
        y[b, a] -= yy
    for k in range(4):
        y[k, k] += 1j * om * cs[k]
        stamp(k, 4 if k in (0, 2) else 5, 1 / (1j * om * ls[k]))
    stamp(4, 5, 1 / (1j * om * l5))
    return _reduce(y, 4)


def line_z0_gamma(kind: str, f: float, w: float, h: float, t: float, er: float, tand: float, cond: float):
    """(Z0, gamma) of a microstrip ("MLIN") or stripline ("SLIN") of width w."""
    fn = stripline if kind == "SLIN" else microstrip
    return fn(f, w, h, t, er, tand, cond)


def synthesize(kind: str, z0: float, f: float, h: float, t: float, er: float, tand: float = 0.0,
               cond: float = 5.8e7) -> float:
    """Width of a microstrip ("MLIN") or stripline ("SLIN") with the impedance z0 at f (bisection)."""
    lo, hi = math.log(1e-3), math.log(1e2)
    for _ in range(80):
        mid = (lo + hi) / 2
        if line_z0_gamma(kind, f, h * math.exp(mid), h, t, er, tand, cond)[0].real > z0:
            lo = mid
        else:
            hi = mid
    return h * math.exp((lo + hi) / 2)


# ---- admittance matrices ------------------------------------------------------------------------------
def _regular(gl: complex) -> complex:
    """A lossless line of n half wavelengths has no Y matrix: a tiny loss keeps it finite (and well
    conditioned; the error it adds is about 1e-8)."""
    gl = complex(gl)
    return complex(1e-8, gl.imag) if abs(gl.real) < 1e-8 else gl


def line_y(z0: complex, gl: complex) -> np.ndarray:
    """Y matrix of a two-port line section of propagation gamma*length gl."""
    gl = _regular(gl)
    a = 1 / (np.tanh(gl) * z0)
    b = -1 / (np.sinh(gl) * z0)
    return np.array([[a, b], [b, a]], complex)


def stub_y(z0: complex, gl: complex, open_end: bool) -> complex:
    """Input admittance of a stub, open or shorted at its far end."""
    gl = _regular(gl)
    return np.tanh(gl) / z0 if open_end else 1 / (np.tanh(gl) * z0)


def coupled_y(even: tuple, odd: tuple, length: float) -> np.ndarray:
    """4x4 Y matrix of symmetric coupled lines, pins (line A in, A out, line B in, B out)."""
    ye = line_y(even[0], even[1] * length)
    yo = line_y(odd[0], odd[1] * length)
    s, d = (ye + yo) / 2, (ye - yo) / 2
    return np.block([[s, d], [d, s]])
