"""Layout of B3_final in mm and B3_final in Hertzon against the first (preliminary) reference-solver run.

python make_plots.py   (writes into ../results)
"""
import os, csv, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle

here = os.path.dirname(os.path.abspath(__file__))
res = os.path.join(here, "..", "results")
ref = os.path.join(here, "..", "reference")

# FPC outline and holes of the benchmark (mm), PCB edge under the FPC
fpc = [(-3.4, 3.5), (10.23, 3.5), (10.71, 3.26), (11.3, 2.94), (12, 2.94), (12.64, 3.26), (13.12, 3.5), (14.4, 3.5),
       (14.63, 3.73), (14.63, 4.6), (14.4, 4.83), (12.88, 4.85), (12.45, 5.03), (12.29, 5.46), (12.3, 6.42),
       (12.07, 6.655), (-3.17, 6.655), (-3.41, 6.42), (-3.647, 3.774)]
slot = [(11.441, 3.49), (11.839, 3.444), (11.926, 4.188), (11.529, 4.235)]
pcb = [(-4.12, -1.34), (26.39, -3.81), (36.54, -3.81), (36.54, 6.55), (22.2, 6.53), (21.69, 6.31), (18.86, 6.31),
       (17.51, 6.06), (17.29, 5.53), (16.89, 5.13), (16.36, 4.91), (16.19, 4.91), (16.19, 4.04), (-3.5, 3.93)]


def layout(name, title, out):
    g = json.load(open(os.path.join(ref, "hfss_%s.json" % name)))
    r = g["via_d"] / 2
    fig, ax = plt.subplots(figsize=(13, 4.2))
    ax.add_patch(Polygon(fpc, fc="#c9d9ee", ec="#35527a", lw=1, label="FPC"))
    ax.add_patch(Polygon(pcb, fc="#b9b9b9", ec="k", lw=1, alpha=.45, label="PCB (1.2 mm above the FPC)"))
    for i, poly in enumerate(g["copper"]):
        ax.add_patch(Polygon(poly[0], fc="#d4871c", ec="#6b3f00", lw=.8, label="Antenna copper, one FPC layer" if i == 0 else None))
    ax.add_patch(Polygon(g["pad"], fc="#f2c94c", ec="k", lw=.8, alpha=.8, label="Signal pad"))
    ax.add_patch(Polygon(g["port"], fc="#d62728", ec="k", lw=.5, label="50 ohm port (pad to PCB edge)"))
    ax.add_patch(Polygon(slot, fc="w", ec="#35527a"))
    ax.add_patch(Circle((0.525, 5.315), 0.372, fc="w", ec="#35527a"))
    for i, v in enumerate(g["gvias"]):
        ax.add_patch(Circle(v, r, fc="k", label="Ground vias, %.2f mm" % g["via_d"] if i == 0 else None))
    ax.add_patch(Circle(g["feed"], r, fc="#1f4fd6", label="Feed via, %.2f mm" % g["via_d"]))
    ax.set_xlim(-4.5, 17); ax.set_ylim(2.7, 7.4); ax.set_aspect("equal")
    ax.set_xlabel("x (mm)"); ax.set_ylabel("y (mm)"); ax.grid(alpha=.3)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), fontsize=8, ncol=4)
    ax.set_title(title)
    fig.tight_layout(); fig.savefig(os.path.join(res, out), dpi=170); plt.close(fig)


def hertzon_table(fn):
    rows = []
    for line in open(os.path.join(res, fn)):
        t = line.split()
        if t and t[0][0].isdigit():
            rows.append([float(x) for x in t[:4]])  # f, mismatch, radiation, total (%)
    return np.array(rows)


def reference_table():
    with open(os.path.join(res, "reference_prelim", "Efficiency.csv")) as f:
        rows = [(float(r[0]), 100 * float(r[1]) / float(r[3])) for r in list(csv.reader(f))[1:]]
    return np.array(rows)


layout("B3_final", "Run B, design B3_final: layout on the FPC (top view)", "RunB_B3_final_layout_mm.png")
layout("Final", "Run B, design Final (40 % target): layout on the FPC (top view)", "RunB_Final_layout_mm.png")

h, rf = hertzon_table("B3_final_efficiency.txt"), reference_table()
fig, axs = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1, 2.6]}, sharey=True)
for ax, (lo, hi) in zip(axs, ((2.37, 2.51), (5.1, 7.2))):
    m, n = (h[:, 0] > lo) & (h[:, 0] < hi), (rf[:, 0] > lo) & (rf[:, 0] < hi)
    ax.plot(h[m, 0], h[m, 3], "-o", color="#1f6fd1", ms=3.5, lw=2, label="Hertzon 0.3.18 (344k tetrahedra)")
    ax.plot(rf[n, 0], rf[n, 1], "--s", color="#c0392b", ms=3.5, lw=1.5,
            label="Reference solver, first run (no mesh seeding, preliminary)")
    ax.axhline(50, color="#b00020", lw=1, ls=":")
    ax.set_xlim(lo, hi); ax.set_ylim(0, 105); ax.grid(color="#dddddd"); ax.set_xlabel("Frequency (GHz)")
axs[0].set_ylabel("Total efficiency (%)")
axs[0].set_title("2.401-2.483 GHz (disputed)", loc="left", fontsize=10)
axs[1].set_title("5.17-7.125 GHz", loc="left", fontsize=10)
axs[1].legend(frameon=False, fontsize=9, loc="lower center")
fig.suptitle("Run B, B3_final: Hertzon against the first reference-solver run", x=0.01, ha="left", fontsize=11.5,
             fontweight="bold")
fig.tight_layout(); fig.savefig(os.path.join(res, "RunB_B3_final_hertzon_vs_reference_prelim.png"), dpi=160)
