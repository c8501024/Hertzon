"""Microstrip through line: FETD against EMerge's frequency domain solve on the same mesh.

Usage: python run_microstrip.py [steps_per_period]
"""
import sys
import time

import emerge as em
import numpy as np

import fetd
from ms_model import build

fetd.fix_robin_quadrature()
f1, f2, nf = 1e9, 6e9, 21
freqs = np.linspace(f1, f2, nf)
ppw = int(sys.argv[1]) if len(sys.argv) > 1 else 40
with em.Simulation("ms_td", save_file=False) as m:
    build(m, f1, f2, nf)
    t = time.time()
    dfd = m.mw.run_sweep()
    tfd = time.time() - t
    Sfd = np.array([[dfd.scalar.grid.S(i, j) for j in (1, 2)] for i in (1, 2)])
    t = time.time()
    res, ids, info, meta = fetd.run(m, f1, f2, freqs, ppw=ppw)
    dtd = fetd.inject(m, freqs, res, ids)
    ttd = time.time() - t
    Std = np.array([[dtd.scalar.grid.S(i, j) for j in (1, 2)] for i in (1, 2)])
print(f"RESULT FD {tfd:.1f}s  TD {ttd:.1f}s  dtau {meta['dtau'] * 1e3:.3f} mm")
for f, a, b, c, d in zip(freqs / 1e9, Sfd[0, 0], Std[0, 0], Sfd[1, 0], Std[1, 0]):
    print(f"RESULT {f:5.2f} GHz  S11 fd {20 * np.log10(abs(a)):7.2f} td {20 * np.log10(abs(b)):7.2f} dB   "
          f"S21 fd {abs(c):.4f}<{np.angle(c, 1):7.1f} td {abs(d):.4f}<{np.angle(d, 1):7.1f}")
print("RESULT max |dS|", np.abs(Sfd - Std).max())
