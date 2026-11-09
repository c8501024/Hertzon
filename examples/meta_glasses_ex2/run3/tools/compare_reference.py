"""Hertzon A13 against the reference solver on Meta's STEP, plus the Run 2 reference curve with its dip."""
import csv, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
F = [2.401, 2.442, 2.483, 5.17, 5.5, 5.85, 6.2, 6.55, 6.85, 7.125]
HZ = [62.16, 74.27, 76.48, 52.92, 77.23, 86.27, 79.66, 57.70, 45.57, 36.77]
HZ_RAD = [81.1, 83.4, 84.4, 86.1, 92.2, 94.9, 94.5, 92.5, 90.2, 87.2]
RUN2 = [61.4, 80.0, 80.7, 46.2, 89.7, 67.3, 39.4, 15.8, 41.6, 80.2]
REF, ACC = [], []
with open(os.path.join(RES, "reference_efficiency.csv")) as f:
    for r in list(csv.reader(f))[1:]:
        REF.append(float(r[1]) * 100)
        ACC.append(float(r[2]) * 100)

with open(os.path.join(RES, "Hertzon_vs_reference.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["f_GHz", "Hertzon_total_pct", "Hertzon_rad_pct", "reference_total_pct", "reference_accepted_pct", "Run2_reference_total_pct"])
    for row in zip(F, HZ, HZ_RAD, REF, ACC, RUN2):
        w.writerow([round(x, 3) for x in row])

BLUE, ORANGE, GRAY, INK, MUTED, SURF = "#2a78d6", "#eb6834", "#8a8983", "#0b0b0b", "#52514e", "#fcfcfb"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": "#c9c8c2", "text.color": INK, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "font.family": "DejaVu Sans"})
fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(9.2, 4.2), gridspec_kw={"width_ratios": [1, 3.2]}, facecolor=SURF)
for ax, idx, title in ((a, range(0, 3), "2.401-2.483 GHz"), (b, range(3, 10), "5.17-7.125 GHz")):
    ax.set_facecolor(SURF)
    fx = [F[i] for i in idx]
    ax.plot(fx, [RUN2[i] for i in idx], color=GRAY, lw=2, ls=(0, (4, 3)), marker="o", ms=5, label="Run 2, reference solver")
    ax.plot(fx, [HZ[i] for i in idx], color=ORANGE, lw=2, marker="s", ms=6, label="Run 3 (A13), Hertzon")
    ax.plot(fx, [REF[i] for i in idx], color=BLUE, lw=2, marker="o", ms=6, label="Run 3 (A13), reference solver")
    ax.set_title(title, fontsize=10, loc="left", color=MUTED)
    ax.grid(axis="y", color="#e6e5e0", lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_xlabel("Frequency (GHz)")
a.set_ylabel("Total efficiency Prad / Pinc (%)")
a.set_ylim(0, 100)
b.annotate("Run 2 dip: 15.8 %", (6.55, 15.8), xytext=(6.62, 7), fontsize=9, color=MUTED)
b.legend(loc="lower left", frameon=False, fontsize=9)
fig.suptitle("The dip around 6.55 GHz is gone: 64.8 % in the reference solver instead of 15.8 %", x=0.01, ha="left", fontsize=12, fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_Run3_hertzon_vs_reference.png"), dpi=200, facecolor=SURF)
