"""Efficiency table (CSV) from a Hertzon results folder: export_tables.py <results folder> <out.csv>

Columns: frequency, S11, mismatch, radiation and total efficiency, power balance (accepted - radiated - loss,
as a fraction of the incident power). Total efficiency = far-field radiated power / incident power at 50 ohm.
"""
import sys, math, numpy as np

d = sys.argv[1].rstrip("/\\") + "/"
ff = np.load(d + "farfield_FF_Eff.npz", allow_pickle=True)
ls = np.load(d + "losses_Eff.npz", allow_pickle=True)
sp = np.load(d + "sparams_Eff.npz", allow_pickle=True)
S = {round(f / 1e9, 4): s[0, 0] for f, s in zip(sp["freq"], sp["S"])}
rows, i = [], 0
while "freq_%d" % i in ff.files:
    f = round(float(ff["freq_%d" % i][0]) / 1e9, 4)
    prad = float(ff["Prad_%d_1" % i][0])
    loss = float(ls["Ploss_%d" % i].real.ravel()[0])
    mis = 1 - abs(S[f]) ** 2
    rows.append((f, 20 * math.log10(abs(S[f])), mis, prad / mis, prad, mis - prad - loss))
    i += 1
with open(sys.argv[2], "w") as out:
    out.write("freq_GHz,S11_dB,mismatch_eff,radiation_eff,total_eff,power_balance\n")
    for r in sorted(rows):
        out.write("%.4f,%.3f,%.4f,%.4f,%.4f,%+.4f\n" % r)
