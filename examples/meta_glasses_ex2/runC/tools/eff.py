"""Compact efficiency table from a Hertzon results folder: eff.py <folder> [solution]"""
import sys, numpy as np
d = sys.argv[1].rstrip("/\\") + "/"
sol = sys.argv[2] if len(sys.argv) > 2 else "Eff"
ff = np.load(d + "farfield_FF_%s.npz" % sol, allow_pickle=True)
ls = np.load(d + "losses_%s.npz" % sol, allow_pickle=True)
sp = np.load(d + "sparams_%s.npz" % sol, allow_pickle=True)
S = {round(f / 1e9, 4): s[0, 0] for f, s in zip(sp["freq"], sp["S"])}
rows = []
i = 0
while "freq_%d" % i in ff.files:
    f = round(float(ff["freq_%d" % i][0]) / 1e9, 4)
    prad = float(ff["Prad_%d_1" % i][0])
    loss = float(ls["Ploss_%d" % i].real.ravel()[0]) if "Ploss_%d" % i in ls.files else float("nan")
    mis = 1 - abs(S[f]) ** 2
    rows.append((f, mis, prad / mis, prad, mis - prad - loss))
    i += 1
rows.sort()
print(" f/GHz   mism   rad    total  balance")
for r in rows:
    print("%6.3f  %5.3f  %5.3f  %5.3f  %+6.3f" % r)
for name, lo, hi in (("LB", 2.39, 2.49), ("HB", 5.16, 7.13)):
    t = [r[3] for r in rows if lo <= r[0] <= hi]
    if t:
        print("%s: min %.3f  mean %.3f  (n=%d)" % (name, min(t), sum(t) / len(t), len(t)))
