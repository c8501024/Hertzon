"""Plots and tables of Run 4 from the files of this folder and of run2/ and run3/: total efficiency of Run 2, 3 and 4
in the reference solver, Run 3 against Run 4 (A28, A29), S11 of A29 against Run 3, and the A29 layout in mm."""
import csv, json, os, re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle, Circle

HERE = os.path.dirname(os.path.abspath(__file__))
RUN4 = os.path.join(HERE, "..")
EX2 = os.path.join(RUN4, "..")
RES = os.path.join(RUN4, "results")

BLUE, ORANGE, GRAY, INK, MUTED, SURF, GRID, BAND = ("#2a78d6", "#eb6834", "#8a8983", "#0b0b0b", "#52514e", "#fcfcfb",
                                                    "#e6e5e0", "#f0efe9")
plt.rcParams.update({"font.size": 10, "axes.edgecolor": "#c9c8c2", "text.color": INK, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "font.family": "DejaVu Sans"})


def eff(path):
    """Total efficiency in % (RadiatedPower / IncidentPower) at the ten band points of a reference export."""
    with open(path) as fh:
        rows = list(csv.reader(fh))[1:]
    return [float(r[0]) for r in rows], [100 * float(r[1]) / float(r[3]) for r in rows]


def mean(e):
    return sum(e[:3]) / 3, sum(e[3:]) / 7


F, RUN2 = eff(os.path.join(EX2, "run2", "results", "reference_efficiency.csv"))
_, RUN3 = eff(os.path.join(EX2, "run3", "results", "reference_efficiency.csv"))
_, A28 = eff(os.path.join(RES, "reference_efficiency_A28.csv"))
_, A29 = eff(os.path.join(RES, "reference_efficiency.csv"))

with open(os.path.join(RES, "reference_runs_efficiency.csv"), "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["f_GHz", "Run2_pct", "Run3_pct", "Run4_A28_pct", "Run4_A29_pct"])
    for row in zip(F, RUN2, RUN3, A28, A29):
        w.writerow([round(x, 3) for x in row])
for name, e in (("Run 2", RUN2), ("Run 3", RUN3), ("Run 4 A28", A28), ("Run 4 A29", A29)):
    lo, hi = mean(e)
    print("%-10s 2.4: %5.1f  high: %5.1f  equal: %5.1f" % (name, lo, hi, (lo + hi) / 2))


def two_panels(height):
    fig, (a, b) = plt.subplots(1, 2, sharey=True, figsize=(9.2, height), gridspec_kw={"width_ratios": [1, 3.4]},
                               facecolor=SURF)
    for p, (lo, hi), lim, title in ((a, (2.401, 2.483), (2.37, 2.515), "2.401–2.483 GHz"),
                                    (b, (5.17, 7.125), (5.0, 7.62), "5.17–7.125 GHz")):
        p.set_facecolor(SURF)
        p.axvspan(lo, hi, color=BAND, zorder=0)
        p.set_xlim(*lim)
        p.set_title(title, fontsize=10, loc="left", color=MUTED)
        p.set_xlabel("Frequency (GHz)")
        p.grid(axis="y", color=GRID, lw=0.8)
        p.set_axisbelow(True)
        for s in ("top", "right"):
            p.spines[s].set_visible(False)
    a.set_xticks([2.40, 2.44, 2.48])
    a.set_ylim(0, 100)
    a.set_ylabel("Total efficiency Prad / Pinc (%)")
    return fig, a, b


def draw(a, b, series):
    for e, color, ls, marker, label in series:
        a.plot(F[:3], e[:3], color=color, lw=2, ls=ls, marker=marker, ms=6, mec=SURF, mew=1.2, zorder=3)
        b.plot(F[3:], e[3:], color=color, lw=2, ls=ls, marker=marker, ms=6, mec=SURF, mew=1.2, label=label, zorder=3)


def label(name, e):
    lo, hi = mean(e)
    return "%s: %.1f / %.1f %% (%.1f %%)" % (name, lo, hi, (lo + hi) / 2)


# ---- Run 2, 3 and 4 ---------------------------------------------------------------------------------------------
fig, a, b = two_panels(5.8)
draw(a, b, [(RUN2, GRAY, (0, (4, 3)), "s", label("Run 2: rerun with the fixed MCP (faulty)", RUN2)),
            (RUN3, BLUE, "-", "o", label("Run 3: no band without a radiating mode", RUN3)),
            (A29, ORANGE, "-", "o", label("Run 4: extra resonance for 6–7 GHz (A29)", A29))])
b.annotate("Run 2 dip: %.0f %%" % RUN2[7], (F[7], RUN2[7]), xytext=(-100, -4), textcoords="offset points",
           fontsize=9, color=MUTED)
fig.legend(*b.get_legend_handles_labels(), loc="lower center", frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.0),
           title="Reference solver, Meta's STEP: mean 2.4 GHz / high band (equal weight)", title_fontsize=9)
fig.suptitle("Example 2 over three runs: equal-weight mean %.1f → %.1f → %.1f %%"
             % tuple(sum(mean(e)) / 2 for e in (RUN2, RUN3, A29)), x=0.01, ha="left", fontsize=12, fontweight="bold")
fig.tight_layout(rect=(0, 0.2, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_runs_reference.png"), dpi=200, facecolor=SURF)
plt.close(fig)

# ---- Run 3 against Run 4 A28 and A29 ----------------------------------------------------------------------------
fig, a, b = two_panels(5.6)
draw(a, b, [(A28, GRAY, (0, (4, 3)), "s", label("Run 4 A28, first design with the parasitic", A28)),
            (RUN3, BLUE, "-", "o", label("Run 3 A13", RUN3)),
            (A29, ORANGE, "-", "o", label("Run 4 A29, final", A29))])
for e, name in ((RUN3, "Run 3"), (A29, "A29")):
    b.annotate("%s: %.0f %%" % (name, e[-1]), (F[-1], e[-1]), xytext=(8, -3), textcoords="offset points", fontsize=9)
fig.legend(*b.get_legend_handles_labels(), loc="lower center", frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.0),
           title="Reference solver: mean 2.4 GHz / high band (equal weight)", title_fontsize=9)
fig.suptitle("The parasitic lifts 6.5–7.125 GHz; retuning A28 to A29 brings 2.4 GHz back", x=0.01, ha="left",
             fontsize=12, fontweight="bold")
fig.tight_layout(rect=(0, 0.2, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_Run3_vs_Run4_reference.png"), dpi=200, facecolor=SURF)
plt.close(fig)


# ---- S11 of A29 and Run 3 in the reference solver ---------------------------------------------------------------
def s11(path):
    with open(path) as fh:
        r = np.array([[float(x) for x in row] for row in list(csv.reader(fh))[1:]])
    return r[:, 0], r[:, 3]


fig, ax = plt.subplots(figsize=(9.2, 4.0), facecolor=SURF)
ax.set_facecolor(SURF)
ax.grid(color=GRID, lw=0.8)
ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
for lo, hi in ((2.401, 2.483), (5.17, 7.125)):
    ax.axvspan(lo, hi, color=BAND, lw=0)
f3, s3 = s11(os.path.join(EX2, "run3", "results", "reference_S11.csv"))
f4, s4 = s11(os.path.join(RES, "reference_S11.csv"))
ax.plot(f3, s3, color=BLUE, lw=2, label="Run 3 A13")
ax.plot(f4, s4, color=ORANGE, lw=2, label="Run 4 A29")
ax.set_xlim(2, 8)
ax.set_ylim(-35, 0)
ax.set_xlabel("Frequency (GHz)")
ax.set_ylabel("|S11| (dB)")
ax.legend(loc="lower left", frameon=False, fontsize=9)
fig.suptitle("S11 in the reference solver: the parasitic adds a second dip near 7.2 GHz", x=0.01, ha="left",
             fontsize=12, fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_Run4_S11_reference.png"), dpi=200, facecolor=SURF)
plt.close(fig)

# ---- S11 of Runs 2, 3 and 4 -------------------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(9.2, 4.4), facecolor=SURF)
ax.set_facecolor(SURF)
ax.grid(color=GRID, lw=0.8)
ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
for lo, hi in ((2.401, 2.483), (5.17, 7.125)):
    ax.axvspan(lo, hi, color=BAND, lw=0)
f2, s2 = s11(os.path.join(EX2, "run2", "results", "reference_S11.csv"))
ax.plot(f2, s2, color=GRAY, lw=2, ls=(0, (4, 3)), label="Run 2 (faulty)")
ax.plot(f3, s3, color=BLUE, lw=2, label="Run 3 A13")
ax.plot(f4, s4, color=ORANGE, lw=2, label="Run 4 A29")
ax.axhline(-6, color=MUTED, lw=0.8, ls=":")
ax.text(2.02, -5.6, "−6 dB", fontsize=8.5, color=MUTED)
ax.set_xlim(2, 8)
ax.set_ylim(-35, 0)
ax.set_xlabel("Frequency (GHz)")
ax.set_ylabel("|S11| (dB)")
ax.legend(loc="lower left", frameon=False, fontsize=9)
fig.suptitle("S11 of Runs 2 to 4 in the reference solver, no matching parts", x=0.01, ha="left", fontsize=12,
             fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_runs_S11_reference.png"), dpi=200, facecolor=SURF)
plt.close(fig)

# ---- layout of A29 ----------------------------------------------------------------------------------------------
des = json.load(open(os.path.join(RUN4, "Meta_Ex2_Run4.hzn"), encoding="utf-8"))["designs"][0]
var = {k: float(v.replace("mm", "")) for k, v in des["variables"].items()}
obj = {o["name"]: o for o in des["objects"]}


def ev(expr):
    return float(eval(re.sub(r"(?<=\d)mm", "", expr), {}, dict(var)))


def pts(name):
    p = obj[name]["history"][0]["props"]["Points"]
    return [tuple(float(c) for c in q.split(",")[:2]) for q in p.split(";")]


def rects(names, color, lab):
    first = True
    for name in names:
        p = obj[name]["history"][0]["props"]
        x0, y0, _ = (ev(s) for s in p["Position"].split(","))
        ax.add_patch(Rectangle((x0, y0), ev(p["XSize"]), ev(p["YSize"]), fc=color, ec="none",
                               label=lab if first else None))
        first = False


fig, ax = plt.subplots(figsize=(9.2, 3.0), facecolor=SURF)
ax.set_facecolor(SURF)
ax.grid(color=GRID, lw=0.8)
ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
ax.add_patch(Polygon(pts("FPC"), closed=True, fc="#d9e6d2", ec="#7c9a6e", lw=1, label="FPC"))
ax.add_patch(Polygon(pts("FPC_slot"), closed=True, fc=SURF, ec="#7c9a6e", lw=1))
c = [float(x) for x in obj["FPC_hole"]["history"][0]["props"]["CenterPosition"].split(",")]
ax.add_patch(Circle(c[:2], float(obj["FPC_hole"]["history"][0]["props"]["Radius"]), fc=SURF, ec="#7c9a6e", lw=1))
rects(["Ant", "Ant_stem", "Ant_rdrop", "Ant_rlob", "Ant_rfat", "Ant_lleg", "Ant_larm", "Ant_stub", "Ant_br"],
      "#c98a3c", "Antenna (Run 3 base, retuned)")
rects(["Par", "Par_h", "Par_d", "Par_f", "Par_t", "Par_n", "Par_t2"], ORANGE, "Grounded parasitic (new)")
for name, col, lab in (("FeedPin", "#e8c22c", "Feed pin"), ("GndPin", "#3b3b3b", "Ground pins"),
                       ("GndPin2", "#3b3b3b", None)):
    p = obj[name]["history"][0]["props"]
    x, y, _ = (ev(s) for s in p["CenterPosition"].split(","))
    ax.add_patch(Circle((x, y), ev(p["Radius"]), fc=col, ec=INK, lw=0.6, label=lab, zorder=3))
ax.set_aspect("equal")
ax.set_xlim(-4.2, 15.2)
ax.set_ylim(2.8, 6.9)
ax.set_xlabel("x (mm)")
ax.set_ylabel("y (mm)")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=5, frameon=False, fontsize=9)
fig.suptitle("Antenna A29 on the FPC, CAD coordinates of Meta's model", x=0.01, ha="left", fontsize=12,
             fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_Run4_layout_mm.png"), dpi=200, facecolor=SURF, bbox_inches="tight",
            pad_inches=0.15)
