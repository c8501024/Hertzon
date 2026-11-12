# Compact efficiency table from Hertzon result files (same numbers as get_results kind=efficiency):
#   python eff.py <design> [solution] [setup]
import sys, os
import numpy as np

design = sys.argv[1]
sol = sys.argv[2] if len(sys.argv) > 2 else 'Eff'
setup = sys.argv[3] if len(sys.argv) > 3 else 'S1'
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Meta_Ex2_RunB.hznresults', design, setup)
ff = np.load(os.path.join(root, 'farfield_FF_%s.npz' % sol), allow_pickle=True)
ls = np.load(os.path.join(root, 'losses_%s.npz' % sol), allow_pickle=True)
sp = np.load(os.path.join(root, 'sparams_%s.npz' % sol), allow_pickle=True)
rows = []
i = 0
while 'freq_%d' % i in ff.files:
    f = float(ff['freq_%d' % i][0])
    prad = float(ff['Prad_%d_1' % i][0])
    k = int(np.argmin(abs(sp['freq'] - f)))
    s = sp['S'][k, 0, 0]
    mis = 1 - abs(s) ** 2
    pl = float(ls['Ploss_%d' % i][0, 0].real) if 'Ploss_%d' % i in ls.files else float('nan')
    z = 50 * (1 + s) / (1 - s)
    rows.append((f / 1e9, mis, prad / mis, prad, pl, prad + pl - mis, z))
    i += 1
print(' f/GHz  mism   rad   total  loss  balance   Z')
for r in rows:
    print(' %.3f  %5.1f %5.1f  %5.1f  %4.1f  %+5.1f   %.0f%+.0fj' % (
        r[0], 100 * r[1], 100 * r[2], 100 * r[3], 100 * r[4], 100 * r[5], r[6].real, r[6].imag))
for name, lo, hi in (('LB', 2.3, 2.6), ('HB', 5.0, 7.3)):
    t = [r[3] for r in rows if lo <= r[0] <= hi]
    if t:
        # trapezoidal band mean over the sampled points
        fs = [r[0] for r in rows if lo <= r[0] <= hi]
        mean = np.trapezoid(t, fs) / (fs[-1] - fs[0]) if len(t) > 1 else t[0]
        print('%s: mean(trapz) %.1f %%  mean(points) %.1f %%  min %.1f %%' % (
            name, 100 * mean, 100 * np.mean(t), 100 * min(t)))
