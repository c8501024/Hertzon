"""FETD prototype on top of EMerge: Newmark-beta time stepping of the Nedelec system.

EMerge assembles A(k0) = K + j k0 C - k0^2 M for frequency independent materials, lumped ports and
first-order absorbing boundaries. We sample A at a few frequencies, recover K, C, M, and integrate

    M e'' + C e' + K e = f(tau),   tau = c0 t (metres),

with the average acceleration Newmark scheme (beta = 1/4, gamma = 1/2: unconditionally stable,
second order, no numerical damping). A running DFT of e(tau) divided by the DFT of the pulse gives the
frequency domain solution X(f), which goes back into EMerge's own post processing (S-parameters,
fields), so FD and TD results are compared with identical post processing.
"""
from __future__ import annotations

import time

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import splu

C0 = 299792458.0

# Dunavant degree-4 rule (6 points, all weights positive); rows: weight, barycentric l1, l2, l3
_a, _b = 0.445948490915965, 0.091576213509771
_wa, _wb = 0.223381589678011, 0.109951743655322
DPTS6 = np.array([
    [_wa, _wa, _wa, _wb, _wb, _wb],
    [1 - 2 * _a, _a, _a, 1 - 2 * _b, _b, _b],
    [_a, 1 - 2 * _a, _a, _b, 1 - 2 * _b, _b],
    [_a, _a, 1 - 2 * _a, _b, _b, 1 - 2 * _b],
])


def fix_robin_quadrature() -> None:
    """Assemble EMerge's Robin (port, ABC, impedance) surface matrices with a positive-weight rule.

    EMerge 2.8.9 integrates the boundary Gram matrix  int (n x N_i).(n x N_j) dS  with the 4-point
    degree-3 triangle rule, whose centre weight is negative (-27/48). The matrix is then indefinite;
    in the frequency domain that is a small accuracy error, in the time domain it is negative damping
    that makes the scheme blow up exponentially. The 6-point degree-4 rule is exact for the quadratic
    Nedelec products and keeps the matrix positive semidefinite. Call before assembling (FD and TD).
    """
    import inspect
    import textwrap
    from emerge._emerge.physics.microwave.assembly import robinbc as r
    if getattr(r, "_fetd_fixed", False):
        return
    ns = dict(r.__dict__)
    ns["DPTS"] = DPTS6
    for name in ("ned2_tri_stiff", "compute_bc_entries"):
        src = textwrap.dedent(inspect.getsource(getattr(r, name).py_func))
        src = src.replace("cache=True", "cache=False")
        exec(compile(src, f"<fetd:{name}>", "exec"), ns)
    r.ned2_tri_stiff = ns["ned2_tri_stiff"]
    r.compute_bc_entries = ns["compute_bc_entries"]
    r._fetd_fixed = True


class _Pardiso:
    """Factor once, then forward/back substitution per time step (MKL PARDISO via pypardiso)."""

    def __init__(self, S):
        import pypardiso
        self.S = sp.csr_matrix(S)
        self.ps = pypardiso.PyPardisoSolver(mtype=11)
        self.ps.factorize(self.S)
        self.ps.set_phase(33)

    def solve(self, b):
        return self.ps._call_pardiso(self.S, np.asarray(b, dtype=np.float64)).ravel()


def _factor(S):
    try:
        return _Pardiso(S)
    except ImportError:
        return splu(S)


class _Capture:
    """Stand-in for SolveRoutine.solve: records A, b, solve_ids or returns stored solutions."""

    def __init__(self, solutions=None):
        self.records = []
        self.solutions = solutions

    def __call__(self, A, b, solve_ids, direct=True, id=-1, matrix_type=None):
        from emerge._emerge.solver import SolveReport
        if b.ndim == 1:
            b = b.reshape((-1, 1))
        i = len(self.records)
        self.records.append((A.tocsc(copy=True), np.array(b), np.array(solve_ids)))
        if self.solutions is None:
            x = np.zeros((A.shape[0], b.shape[1]), dtype=np.complex128)
        else:
            x = self.solutions[i]
        return x, SolveReport(solver="FETD", exit_code=0)


def _sweep(m, freqs, cap):
    mw = m.mw
    mw.set_frequency(list(freqs))
    orig = mw.solveroutine.solve
    mw.solveroutine.solve = cap
    from emerge._emerge.physics.microwave.microwave_data import MWData
    mw._state.data.mw = MWData()          # fresh result container, FD results of the user stay intact
    try:
        data = mw.run_sweep()
    finally:
        mw.solveroutine.solve = orig
    return data


def extract(m, f1, f2):
    """K, C, M (real, on the free DoFs), excitation shapes and solve ids."""
    fs = np.array([f1, 0.5 * (f1 + f2), f2, 0.37 * f1 + 0.63 * f2])
    cap = _Capture()
    _sweep(m, fs, cap)
    ks = 2 * np.pi * fs / C0
    ids = cap.records[0][2]
    As = [r[0][ids][:, ids].tocsc() for r in cap.records]
    for A in As:
        A.sum_duplicates(); A.sort_indices()
    # values of all samples on the union pattern (keys r * n + c)
    n = As[0].shape[0]
    coos = [A.tocoo() for A in As]
    keys = [c.row.astype(np.int64) * n + c.col for c in coos]
    allk = np.unique(np.concatenate(keys))
    Y = np.zeros((len(As), len(allk)), dtype=np.complex128)
    for i, (c, k) in enumerate(zip(coos, keys)):
        np.add.at(Y[i], np.searchsorted(allk, k), c.data)
    rows, cols = allk // n, allk % n
    V = np.vstack([np.ones(3), ks[:3], ks[:3] ** 2]).T   # A = A0 + k A1 + k^2 A2
    coef = np.linalg.solve(V, Y[:3])
    pred = coef[0] + ks[3] * coef[1] + ks[3] ** 2 * coef[2]
    fit_err = np.linalg.norm(pred - Y[3]) / np.linalg.norm(Y[3])

    def mat(d):
        return sp.csc_matrix((d, (rows, cols)), shape=(n, n))

    K, C, M = mat(coef[0]), mat(coef[1] / 1j), mat(-coef[2])
    imag = [np.abs(X.data.imag).max() / max(np.abs(X.data).max(), 1e-300) for X in (K, C, M)]
    # excitation: b(k) = j k b'
    bs = np.array([r[1][ids] for r in cap.records])     # (4, n, nport)
    bp = bs / (1j * ks[:, None, None])
    b_err = np.linalg.norm(bp - bp[0]) / np.linalg.norm(bp[0])
    info = dict(fit_err=fit_err, imag_KCM=imag, b_err=b_err, n=len(ids), nnz=len(allk))
    # Dielectric loss tan(delta) enters as -k^2 j er'' M: constant tan(delta) is not causal, so it is
    # turned into an equivalent conductivity at the band centre: -k^2 j Mi ~ j k (-kc Mi).
    kc = np.pi * (f1 + f2) / C0
    C_eff = C.real - kc * M.imag
    info["loss_from_M"] = float(np.abs(M.imag).max() / np.abs(M.real).max())
    return K.real.tocsc(), C_eff.tocsc(), M.real.tocsc(), bp[0].real, ids, info


def pulse(f1, f2):
    """Gaussian modulated sine covering [f1, f2] at about -20 dB; g(tau), g'(tau), tau0."""
    kc = np.pi * (f1 + f2) / C0
    kb = np.pi * (f2 - f1) / C0                          # half bandwidth in k
    s = np.sqrt(np.log(10.0)) * 2 / kb                   # exp(-(kb s/2)^2) = 0.1  (-20 dB)
    tau0 = 4.5 * s

    def g(t):
        u = t - tau0
        return np.exp(-(u / s) ** 2) * np.sin(kc * u)

    def dg(t):
        u = t - tau0
        e = np.exp(-(u / s) ** 2)
        return e * (kc * np.cos(kc * u) - 2 * u / s ** 2 * np.sin(kc * u))

    return g, dg, tau0


def newmark(K, C, M, bvec, dg, dtau, nsteps, ks, probe=None, decay=1e-4, tau_min=0.0, window=None, log=print):
    """Integrate M e'' + C e' + K e = bvec g'(tau). Returns DFT of e at ks and probe signals."""
    t0 = time.time()
    d2 = dtau * dtau
    S = (M + 0.5 * dtau * C + 0.25 * d2 * K).tocsc()
    lu = _factor(S)
    A1 = (2 * M - 0.5 * d2 * K).tocsr()
    A0 = (M - 0.5 * dtau * C + 0.25 * d2 * K).tocsr()
    t_fac = time.time() - t0
    n = K.shape[0]
    e_prev = np.zeros(n); e = np.zeros(n)
    X = np.zeros((n, len(ks)), dtype=np.complex128)
    nb = 64                                              # DFT in blocks of time steps (one GEMM per block)
    buf = np.zeros((n, nb))
    taus_b = []
    f_prev, f_cur = 0.0, dg(0.0)
    sig = []
    peak = 0.0
    peak_src = 1.0
    sig_peak = 0.0
    window = window or int(2 * np.pi / ks.min() / dtau)   # one period of the lowest frequency
    for i in range(nsteps):
        tau = (i + 1) * dtau
        f_next = dg(tau)
        rhs = A1 @ e - A0 @ e_prev + 0.25 * d2 * (f_next + 2 * f_cur + f_prev) * bvec
        e_new = lu.solve(rhs)
        e_prev, e = e, e_new
        f_prev, f_cur = f_cur, f_next
        buf[:, len(taus_b)] = e
        taus_b.append(tau)
        if len(taus_b) == nb:
            X += buf @ np.exp(-1j * np.outer(taus_b, ks))
            taus_b = []
        nrm = np.abs(e).max()
        peak = max(peak, nrm)
        if tau < tau_min:
            peak_src = peak
        if probe is not None:
            sig.append(probe @ e)
            sig_peak = max(sig_peak, np.abs(sig[-1]).max())
        # stop on the port signals (the field norm keeps a static gradient part that never decays)
        if probe is not None and tau > tau_min and i >= window:
            recent = np.abs(np.array(sig[-window:])).max()
            if recent < decay * sig_peak:
                break
        elif probe is None and tau > tau_min and nrm < decay * peak:
            break
        if not np.isfinite(nrm) or nrm > 1e6 * max(peak_src, 1e-300):
            raise FloatingPointError(f"FETD diverged at step {i + 1}")
        if (i + 1) % 2000 == 0:
            log(f"  step {i + 1}: |e| = {nrm / peak:.2e} of peak, {time.time() - t0:.0f}s")
    if taus_b:
        X += buf[:, :len(taus_b)] @ np.exp(-1j * np.outer(taus_b, ks))
    X *= dtau
    log(f"newmark: n={n:,} steps={i + 1} factor {t_fac:.2f}s total {time.time() - t0:.2f}s")
    return X, np.array(sig), i + 1


def run(m, f1, f2, freqs, ppw=40, log=print):
    """Full FETD run: returns EMerge data object (S-parameters from FETD) and diagnostics."""
    K, C, M, Bp, ids, info = extract(m, f1, f2)
    log(f"extract: {info}")
    g, dg, tau0 = pulse(f1, f2)
    kmax = 2 * np.pi * f2 / C0
    dtau = 2 * np.pi / kmax / ppw
    ks = 2 * np.pi * np.asarray(freqs) / C0
    # DFT of the pulse with the same sampling (cancels the discrete-time transform error to first order)
    results, signals = [], []
    for p in range(Bp.shape[1]):
        X, sig, nst = newmark(K, C, M, Bp[:, p], dg, dtau, 200000, ks, probe=Bp.T, tau_min=2 * tau0, log=log)
        signals.append(sig)
        tt = np.arange(1, nst + 1) * dtau
        G = (g(tt)[:, None] * np.exp(-1j * np.outer(tt, ks))).sum(0) * dtau
        results.append(X / G[None, :])
    return results, ids, info, dict(dtau=dtau, tau0=tau0, signals=signals, g=g)


def inject(m, freqs, results, ids):
    """Run EMerge's post processing with the FETD solutions."""
    sols = []
    nport = len(results)
    cap = _Capture()
    _sweep(m, freqs, cap)
    for i, (A, b, _) in enumerate(cap.records):
        x = np.zeros((A.shape[0], b.shape[1]), dtype=np.complex128)
        for p in range(nport):
            x[ids, p] = results[p][:, i]
        sols.append(x)
    return _sweep(m, freqs, _Capture(solutions=sols))
