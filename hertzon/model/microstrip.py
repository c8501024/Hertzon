"""Microstrip line analysis and synthesis.

With hfsynpy installed (as in the filterbench), its synthesis and analysis are used; otherwise the same
closed forms as the Qucs / KiCad transmission line calculators (Hammerstad-Jensen with strip thickness,
Kirschning-Jansen dispersion). All lengths in metres.
"""
from __future__ import annotations

import math

ZF0 = 376.730313668          # free space wave impedance (ohm)
C0 = 299792458.0


def _z01(u: float) -> float:
    """Impedance of the line in air (Hammerstad)."""
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    return ZF0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))


def _eeff0(u: float, er: float) -> float:
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def _hfsynpy():
    try:
        import hfsynpy
        return hfsynpy
    except Exception:  # noqa: BLE001
        return None


def engine() -> str:
    """Name of the synthesis used (shown in the design dialogs)."""
    lib = _hfsynpy()
    if lib is None:
        return "built-in Hammerstad-Jensen (install hfsynpy for the filterbench synthesis)"
    try:
        from importlib.metadata import version
        return f"hfsynpy {version('hfsynpy')}"
    except Exception:  # noqa: BLE001
        return "hfsynpy"


def _args(h, er, t, f, tand, sigma) -> dict:
    return dict(eps_r=er, tand=tand, h=h, t=t, rough=0.0, sigma=sigma, mur=1.0, murc=1.0, frequency=f or 1e9)


def analyze(w: float, h: float, er: float, t: float = 0.0, f: float = 0.0, tand: float = 0.0,
            sigma: float = 5.8e7, builtin: bool = False) -> tuple[float, float]:
    """(Z0 in ohm, effective permittivity) of a strip of width w on a substrate of height h at frequency f."""
    lib = None if builtin else _hfsynpy()
    if lib is not None:
        r = lib.analyze_microstrip(**_args(h, er, t, f, tand, sigma), width=w)
        if math.isfinite(r.Z0_0) and math.isfinite(r.epsilon_eff) and r.epsilon_eff > 1:
            return float(r.Z0_0), float(r.epsilon_eff)
    u = w / h
    t_h = t / h
    u1 = ur = u
    if t_h > 0:
        coth = 1 / math.tanh(math.sqrt(6.517 * u))
        du1 = t_h / math.pi * math.log(1 + 4 * math.e / (t_h * coth ** 2))
        u1 = u + du1
        ur = u + 0.5 * (1 + 1 / math.cosh(math.sqrt(er - 1))) * du1
    e_r = _eeff0(ur, er)
    z0 = _z01(ur) / math.sqrt(e_r)
    e0 = e_r * (_z01(u1) / _z01(ur)) ** 2
    if f > 0:                                            # Kirschning-Jansen, fn in GHz*mm
        fn = f * h / 1e6
        p1 = 0.27488 + (0.6315 + 0.525 / (1 + 0.0157 * fn) ** 20) * u - 0.065683 * math.exp(-8.7513 * u)
        p2 = 0.33622 * (1 - math.exp(-0.03442 * er))
        p3 = 0.0363 * math.exp(-4.6 * u) * (1 - math.exp(-(fn / 38.7) ** 4.97))
        p4 = 1 + 2.751 * (1 - math.exp(-(er / 15.916) ** 8))
        p = p1 * p2 * ((0.1844 + p3 * p4) * fn) ** 1.5763
        ef = er - (er - e0) / (1 + p)
        z0 *= math.sqrt(e0 / ef) * (ef - 1) / (e0 - 1)    # quasi-TEM scaling of the impedance
        e0 = ef
    return z0, e0


def synthesize(z0: float, h: float, er: float, t: float = 0.0, f: float = 0.0, tand: float = 0.0,
               sigma: float = 5.8e7, builtin: bool = False) -> float:
    """Strip width (m) for the impedance z0."""
    lib = None if builtin else _hfsynpy()
    if lib is not None:
        try:
            r = lib.synthesize_microstrip(**_args(h, er, t, f, tand, sigma), z0_target=z0, ang_l_target=180.0)
            if math.isfinite(r.width) and r.width > 0:
                return float(r.width)
        except Exception:  # noqa: BLE001
            pass
    lo, hi = math.log(1e-3), math.log(1e2)               # bisection on log(w/h)
    for _ in range(200):
        mid = (lo + hi) / 2
        if analyze(h * math.exp(mid), h, er, t, f, builtin=True)[0] > z0:
            lo = mid
        else:
            hi = mid
    return h * math.exp((lo + hi) / 2)


def guided_wavelength(w: float, h: float, er: float, t: float, f: float, tand: float = 0.0,
                      sigma: float = 5.8e7) -> float:
    return C0 / f / math.sqrt(analyze(w, h, er, t, f, tand, sigma)[1])


def _kk(k: float) -> float:
    """K(k') / K(k) of the complete elliptic integrals (Hilberg's approximation, error below 3e-6)."""
    kp = math.sqrt(max(1 - k * k, 0.0))
    if k * k <= 0.5:
        return math.log(2 * (1 + math.sqrt(kp)) / (1 - math.sqrt(kp))) / math.pi
    return math.pi / math.log(2 * (1 + math.sqrt(k)) / (1 - math.sqrt(k)))


def _coupled_caps(w: float, s: float, h: float, er: float) -> tuple[float, float]:
    """Even and odd mode capacitances per length (F/m) of two strips of width w at the gap s (Garg-Bahl)."""
    e0 = 8.8541878128e-12
    z0, ee = analyze(w, h, er, builtin=True)
    cp = e0 * er * w / h
    cf = 0.5 * (math.sqrt(ee) / (C0 * z0) - cp)
    a = math.exp(-0.1 * math.exp(2.33 - 2.53 * w / h))
    cf2 = cf / (1 + a * (h / s) * math.tanh(8 * s / h)) * math.sqrt(er / ee)
    k = (s / h) / (s / h + 2 * w / h)
    cga = e0 * _kk(k)
    cgd = e0 * er / math.pi * math.log(1 / math.tanh(math.pi * s / (4 * h))) + \
        0.65 * cf * (0.02 * math.sqrt(er) / (s / h) + 1 - er ** -2)
    return cp + cf + cf2, cp + cf + cga + cgd


def coupled_lines(w: float, s: float, h: float, er: float) -> tuple[float, float, float, float]:
    """(Zoe, Zoo, eeff_e, eeff_o) of a symmetric coupled microstrip pair (quasi-static, Garg-Bahl):
    even and odd mode impedances in ohm and effective permittivities."""
    ce, co = _coupled_caps(w, s, h, er)
    ce1, co1 = _coupled_caps(w, s, h, 1.0)
    return 1 / (C0 * math.sqrt(ce * ce1)), 1 / (C0 * math.sqrt(co * co1)), ce / ce1, co / co1
