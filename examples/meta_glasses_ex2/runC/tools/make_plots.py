"""Layout of T_opp in mm, efficiency and S11 of Run C, and Run 4 / Run B / Run C side by side in Hertzon.

python make_plots.py   (reads the CSV files in ../results and ../../run4, ../../runB; writes into ../results)
"""
import os, csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle

here = os.path.dirname(os.path.abspath(__file__))
res = os.path.join(here, "..", "results")
import sys
sys.path.insert(0, here)
import drc  # noqa: E402  (prints the design-rule numbers, provides the polygons)

pcb = [(-4.12, -1.34), (26.39, -3.81), (36.54, -3.81), (36.54, 6.55), (22.2, 6.53), (21.69, 6.31), (18.86, 6.31),
       (17.51, 6.06), (17.29, 5.53), (16.89, 5.13), (16.36, 4.91), (16.19, 4.91), (16.19, 4.04), (-3.5, 3.93)]
ye = lambda x: 3.93 + 0.0055866 * (x + 3.5)
xf, gp = drc.V["xf"], 0.3
pad = [(xf - .5, ye(xf - .5) + gp), (xf + .5, ye(xf + .5) + gp), (xf + .5, 5.0), (xf - .5, 5.0)]
port = [(xf - .5, ye(xf - .5)), (xf + .5, ye(xf + .5)), (xf + .5, ye(xf + .5) + gp), (xf - .5, ye(xf - .5) + gp)]

fig, ax = plt.subplots(figsize=(13, 4.2))
ax.add_patch(Polygon(drc.fpc, fc="#c9d9ee", ec="#35527a", lw=1, label="FPC (z = -0.265..0)"))
ax.add_patch(Polygon(pcb, fc="#b9b9b9", ec="k", lw=1, alpha=.45, label="PCB (z = 1.2, above the FPC)"))
ax.add_patch(Polygon(drc.ant, fc="#d4871c", ec="#6b3f00", lw=.8, label="Antenna copper at z = 0"))
ax.add_patch(Polygon(pad, fc="#f2c94c", ec="k", lw=.8, alpha=.8, label="Signal pad (z = 1.2)"))
ax.add_patch(Polygon(port, fc="#d62728", ec="k", lw=.5, label="50 ohm port (pad to PCB edge)"))
ax.add_patch(Polygon(drc.slot, fc="w", ec="#35527a"))
ax.add_patch(Circle(drc.hole_c, drc.hole_r, fc="w", ec="#35527a"))
ax.add_patch(Circle((drc.V["xsv"], 3.8), .125, fc="k", label="Ground via 0.25 mm"))
ax.add_patch(Circle((xf, 4.65), .125, fc="#1f4fd6", label="Feed via 0.25 mm"))
for txt, xy in (("Low-band arm (2.44 GHz, third harmonic 5.45 GHz)", (-2.5, 6.9)), ("High-band arm (6.8 GHz)", (9.4, 6.9)),
                ("Short", (5.6, 3.15)), ("Feed", (8.2, 3.15))):
    ax.text(*xy, txt, fontsize=9)
ax.set_xlim(-4.5, 17); ax.set_ylim(2.7, 7.4); ax.set_aspect("equal")
ax.set_xlabel("x (mm)"); ax.set_ylabel("y (mm)"); ax.grid(alpha=.3)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), fontsize=8, ncol=4)
ax.set_title("Run C, design T_opp: layout on the FPC (top view from +z)")
fig.tight_layout(); fig.savefig(os.path.join(res, "RunC_layout_mm.png"), dpi=170); plt.close(fig)


def load(fn):
    with open(os.path.join(res, fn)) as f:
        return np.array([[float(x) for x in r] for r in list(csv.reader(f))[1:]])


runs = [("T_opp, 249k tetrahedra (12 passes, final)", load("T_opp_12passes_249k.csv"), "#1f6fd1", 2.2),
        ("T_opp, 144k tetrahedra (10 passes)", load("T_opp_10passes_144k.csv"), "#8a8f98", 1.4),
        ("T_thick: 18 um thick copper (143k)", load("T_thick_18um_copper.csv"), "#3a9a5c", 1.4)]
fig, axs = plt.subplots(2, 2, figsize=(12, 7), gridspec_kw={"width_ratios": [1, 3]})
for lab, t, c, lw in runs:
    for col, (lo, hi) in enumerate(((2.39, 2.49), (5.1, 7.2))):
        m = (t[:, 0] > lo) & (t[:, 0] < hi)
        axs[0, col].plot(t[m, 0], 100 * t[m, 4], "o-", ms=3, color=c, lw=lw, label=lab)
        axs[1, col].plot(t[m, 0], t[m, 1], "o-", ms=3, color=c, lw=lw, label=lab)
for col in (0, 1):
    axs[0, col].axhline(50, color="#b00020", ls=":", lw=1); axs[0, col].set_ylim(0, 100)
    axs[0, col].set_ylabel("Total efficiency (%)"); axs[1, col].set_ylabel("|S11| (dB)")
    axs[1, col].set_xlabel("Frequency (GHz)")
    axs[0, col].grid(color="#dddddd"); axs[1, col].grid(color="#dddddd")
axs[0, 1].legend(fontsize=8, frameon=False, loc="lower center")
axs[0, 0].set_title("2401-2483 MHz", loc="left", fontsize=10)
axs[0, 1].set_title("5170-7125 MHz (dotted: 50 % target)", loc="left", fontsize=10)
fig.suptitle("Run C: total efficiency and S11 at the evaluation points (Hertzon 0.3.18, no matching)", x=0.01,
             ha="left", fontsize=11.5, fontweight="bold")
fig.tight_layout(); fig.savefig(os.path.join(res, "RunC_efficiency_S11.png"), dpi=150); plt.close(fig)

# Run 4, Run B and Run C in Hertzon
with open(os.path.join(here, "..", "..", "run4", "results", "hertzon_efficiency.csv")) as f:
    r4 = np.array([[float(r[0]), float(r[1])] for r in list(csv.reader(f))[1:]])
b3 = []
for line in open(os.path.join(here, "..", "..", "runB", "results", "B3_final_efficiency.txt")):
    t = line.split()
    if t and t[0][0].isdigit():
        b3.append((float(t[0]), float(t[3])))
b3 = np.array(b3)
rc = load("T_opp_12passes_249k.csv")
cmp_runs = [("Run 4, A29 (Hertzon 0.3.13)", r4, "#8a8f98", "-"),
            ("Run B, B3_final (Hertzon 0.3.18)", b3, "#e8a33b", "-"),
            ("Run C, T_opp (Hertzon 0.3.18)", np.c_[rc[:, 0], 100 * rc[:, 4]], "#1f6fd1", "-")]
fig, axs = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1, 2.6]}, sharey=True)
for lab, t, c, ls in cmp_runs:
    for ax, (lo, hi) in zip(axs, ((2.395, 2.49), (5.1, 7.2))):
        m = (t[:, 0] > lo) & (t[:, 0] < hi)
        ax.plot(t[m, 0], t[m, 1], ls, marker="o", ms=3.5, color=c, lw=2, label=lab)
for ax in axs:
    ax.axhline(50, color="#b00020", lw=1, ls=":"); ax.set_ylim(0, 100); ax.grid(color="#dddddd")
    ax.set_xlabel("Frequency (GHz)")
axs[0].set_ylabel("Total efficiency (%)")
axs[0].set_title("2.401-2.483 GHz", loc="left", fontsize=10); axs[1].set_title("5.17-7.125 GHz", loc="left", fontsize=10)
axs[1].legend(frameon=False, fontsize=9, loc="lower center")
fig.suptitle("Example 2: Runs 4, B and C in Hertzon (no matching; B and C not yet confirmed in the reference solver)",
             x=0.01, ha="left", fontsize=11, fontweight="bold")
fig.tight_layout(); fig.savefig(os.path.join(res, "Ex2_runs_4_B_C_hertzon.png"), dpi=160)
