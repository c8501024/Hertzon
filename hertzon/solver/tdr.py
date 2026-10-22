"""Time domain responses from frequency sweeps: TDR impedance, step and impulse responses.

The usual low-pass transform (as in HFSS, ADS or scikit-rf): the sweep is resampled on a harmonic grid
0, df, 2 df, ... fmax, the missing band down to DC is extrapolated (S(0) is real), a window limits the
ringing of the band edge, and an inverse real FFT gives the impulse response; its running sum is the
step response, and Z0 (1 + rho) / (1 - rho) of the reflected step the impedance profile.

Without data near DC only the band-pass impulse response (its envelope) is meaningful.

No Qt here: used by the report windows and by tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

C0 = 299792458.0

WINDOWS = ("Kaiser", "Hann", "Rectangular", "Gaussian")
FUNCTIONS = ("TDRZ", "Step", "Impulse", "ImpulseBandPass")


@dataclass
class Options:
    window: str = "Kaiser"
    beta: float = 6.0             # Kaiser parameter
    rise_time: float = 0.0        # Gaussian window: 10-90 % rise time of the step [s]; 0: from the bandwidth
    t_max: float = 0.0            # shown time span [s]; 0: up to 2 * longest delay estimate (whole period at most)
    pad: int = 8                  # zero padding factor (smooth curves, no extra information)

    @classmethod
    def from_props(cls, p: dict) -> "Options":
        def num(k, d):
            try:
                return float(p.get(k, "") or d)
            except ValueError:
                return d
        return cls(window=p.get("tdr_window", "Kaiser") or "Kaiser", beta=num("tdr_beta", 6.0),
                   rise_time=num("tdr_rise_ps", 0.0) * 1e-12, t_max=num("tdr_tmax_ns", 0.0) * 1e-9,
                   pad=int(num("tdr_pad", 8)))


def lowpass_data(freq: np.ndarray, s: np.ndarray, n_low: int = 6) -> tuple[np.ndarray, np.ndarray, float]:
    """S on the harmonic grid 0, df, ..., fmax; returns (f, s, gap) with gap = fmin / fmax of the data.

    Below the lowest sweep point Re S is fitted with an even and Im S with an odd polynomial in f
    (S(0) real, S(-f) = conj S(f)); inside the sweep the real and imaginary parts are interpolated with cubic splines."""
    from scipy.interpolate import CubicSpline
    freq = np.asarray(freq, float)
    s = np.asarray(s, complex)
    order = np.argsort(freq)
    freq, s = freq[order], s[order]
    keep = np.concatenate([[True], np.diff(freq) > 0])
    freq, s = freq[keep], s[keep]
    if len(freq) < 4:
        raise ValueError("A time domain transform needs at least 4 frequency points")
    fmax = freq[-1]
    df = float(np.median(np.diff(freq)))
    df = min(df, freq[0]) if freq[0] > 0 else df       # the first harmonic must not skip the lowest point
    n = int(round(fmax / df))
    if n > 200000:
        n = 200000
    df = fmax / n
    f = np.arange(n + 1) * df
    # low-frequency fit: even polynomial for Re S, odd for Im S, over the lowest points (up to 3 f_min)
    m = int(max(min(n_low, len(freq)), np.sum(freq <= 3 * freq[0])))
    m = min(m, len(freq))
    fl, sl = freq[:m] / fmax, s[:m]                     # normalized: f^5 in Hz ruins the least squares
    deg = 3 if m >= 8 else (2 if m >= 4 else 1)
    A_re = np.vstack([fl ** (2 * k) for k in range(deg)]).T
    A_im = np.vstack([fl ** (2 * k + 1) for k in range(deg)]).T
    a = np.linalg.lstsq(A_re, sl.real, rcond=None)[0]
    b = np.linalg.lstsq(A_im, sl.imag, rcond=None)[0]
    out = np.empty(n + 1, complex)
    low = f < freq[0]
    fl2 = f[low] / fmax
    re_ = sum(a[k] * fl2 ** (2 * k) for k in range(len(a)))
    im_ = sum(b[k] * fl2 ** (2 * k + 1) for k in range(len(b)))
    out[low] = re_ + 1j * im_
    out[~low] = CubicSpline(freq, s.real)(f[~low]) + 1j * CubicSpline(freq, s.imag)(f[~low])
    out[0] = out[0].real
    return f, out, float(freq[0] / fmax)


def _window(f: np.ndarray, opt: Options) -> np.ndarray:
    n = len(f)
    if opt.window == "Rectangular":
        return np.ones(n)
    if opt.window == "Hann":
        return 0.5 * (1 + np.cos(np.pi * np.arange(n) / max(n - 1, 1)))
    if opt.window == "Gaussian":
        tr = opt.rise_time or 0.7 / f[-1]                # 10-90 % of a Gaussian step = 2.563 sigma
        sigma = tr / 2.5631
        return np.exp(-0.5 * (2 * np.pi * f * sigma) ** 2)
    return np.kaiser(2 * n - 1, opt.beta)[n - 1:]


def lowpass_impulse(freq, s, opt: Options | None = None):
    """(t, impulse, step, gap): impulse normalized so that an ideal reflection rho peaks at rho."""
    opt = opt or Options()
    f, sh, gap = lowpass_data(freq, s)
    w = _window(f, opt)
    nfft = 2 * opt.pad * (len(f) - 1)
    h = np.fft.irfft(sh * w, nfft)
    ref = np.fft.irfft(w, nfft).max()
    dt = 1.0 / (nfft * (f[1] - f[0]))
    # the windowed impulse of an event at t = 0 is symmetric: start a few rise times before 0 so that the
    # step (running sum) contains all of it; the samples before that wrap around from the period's end
    shift = min(int(round(10.0 / f[-1] / dt)), nfft // 4)
    h = np.roll(h, shift)
    t = (np.arange(nfft) - shift) * dt
    step = np.cumsum(h)
    return t, h / ref, step, gap


def bandpass_impulse(freq, s, opt: Options | None = None):
    """(t, |impulse|): envelope of the band-pass impulse response (no DC data needed)."""
    from scipy.interpolate import CubicSpline
    opt = opt or Options()
    freq = np.asarray(freq, float)
    s = np.asarray(s, complex)
    n = max(len(freq), 2)
    fu = np.linspace(freq[0], freq[-1], n)
    su = CubicSpline(freq, s.real)(fu) + 1j * CubicSpline(freq, s.imag)(fu)
    if opt.window == "Rectangular":
        w = np.ones(n)
    elif opt.window == "Hann":
        w = np.hanning(n)
    else:
        w = np.kaiser(n, opt.beta)
    nfft = opt.pad * n
    h = np.fft.ifft(su * w, nfft)
    ref = np.abs(np.fft.ifft(w, nfft)).max()
    dt = 1.0 / (nfft * (fu[1] - fu[0]))
    return np.arange(nfft) * dt, np.abs(h) / ref


_TD = re.compile(r"^\s*(TDRZ|Step|Impulse|ImpulseBandPass)\(\s*(?:S\(\s*(\d+)\s*,\s*(\d+)\s*\)|(\d+))\s*\)\s*$")


def parse(expr: str) -> tuple[str, int, int]:
    """'TDRZ(1)' -> ('TDRZ', 0, 0); 'Step(S(2,1))' -> ('Step', 1, 0)."""
    m = _TD.match(expr)
    if not m:
        raise ValueError(f"Unknown time domain trace '{expr}'; use TDRZ(i), Step(S(i,j)), Impulse(S(i,j)) "
                         "or ImpulseBandPass(S(i,j))")
    fn = m.group(1)
    if m.group(4) is not None:
        i = j = int(m.group(4))
    else:
        i, j = int(m.group(2)), int(m.group(3))
    if fn == "TDRZ" and i != j:
        raise ValueError("TDRZ needs a reflection: TDRZ(i) or TDRZ(S(i,i))")
    return fn, i - 1, j - 1


def max_delay(freq: np.ndarray, S: np.ndarray) -> float:
    """Largest group delay of the sweep (90th percentile over frequency, all S(i,j)) [s]."""
    f = np.asarray(freq, float)
    tmax = 0.0
    if len(f) > 2:
        for i in range(S.shape[1]):
            for j in range(S.shape[2]):
                tg = -np.gradient(np.unwrap(np.angle(S[:, i, j])), 2 * np.pi * f)
                tmax = max(tmax, float(np.percentile(np.abs(tg), 90)))
    return tmax


def eval_time(data: dict, expr: str, opt: Options | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Evaluate a time domain trace on S-parameter data {'freq', 'S', 'z0'}: (t [s], y, info)."""
    opt = opt or Options()
    fn, i, j = parse(expr)
    S = np.asarray(data["S"])
    if not (0 <= i < S.shape[1] and 0 <= j < S.shape[2]):
        raise ValueError("Port index out of range")
    f = np.asarray(data["freq"], float)
    s = S[:, i, j]
    if fn == "ImpulseBandPass":
        t, y = bandpass_impulse(f, s, opt)
        gap = f[0] / f[-1]
    else:
        t, h, step, gap = lowpass_impulse(f, s, opt)
        if fn == "Impulse":
            y = h
        elif fn == "Step":
            y = step
        else:
            z0 = np.real(np.ravel(data.get("z0", [50.0])))
            z = z0[i] if z0.size > i else (z0[0] if z0.size else 50.0)
            rho = np.clip(step, -0.999999, 0.999999)
            y = z * (1 + rho) / (1 - rho)
    tau = max_delay(f, S)
    tmax = opt.t_max or min(max(2.5 * tau, 20.0 / f[-1]), t[-1])
    k = (t <= tmax) & (t >= -3.0 / f[-1])               # a short lead-in before t = 0
    # DC extrapolation is only trustworthy if the lowest sweep frequency is small against the rate at
    # which S turns (one phase period = 1 / (2 tau) for a round trip of tau): fmin * 2 tau < 5 %
    dc_quality = float(f[0] * 2 * tau)
    info = {"gap": gap, "dc_quality": dc_quality, "dt": t[1] - t[0],
            "lowpass_ok": fn == "ImpulseBandPass" or dc_quality < 0.05}
    return t[k], y[k], info
