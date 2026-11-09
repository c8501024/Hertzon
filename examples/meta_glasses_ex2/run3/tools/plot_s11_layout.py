"""S11 of A13 in Hertzon against the reference solver, and the antenna layout in mm, both from the files of this
folder (results/A13.s1p, results/reference_S11.csv, Meta_Ex2_Run3.hzn)."""
import csv, json, os, re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle, Circle

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "results")
HZN = os.path.join(HERE, "..", "Meta_Ex2_Run3.hzn")

BLUE, ORANGE, GRAY, INK, MUTED, SURF = "#2a78d6", "#eb6834", "#8a8983", "#0b0b0b", "#52514e", "#fcfcfb"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": "#c9c8c2", "text.color": INK, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "font.family": "DejaVu Sans"})


def style(ax):
    ax.set_facecolor(SURF)
    ax.grid(color="#e6e5e0", lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


# ---- S11 -------------------------------------------------------------------------------------------------------
hz = np.loadtxt(os.path.join(RES, "A13.s1p"), comments=["!", "#"])
f_hz = hz[:, 0] / 1e9
s_hz = 20 * np.log10(np.abs(hz[:, 1] + 1j * hz[:, 2]))
with open(os.path.join(RES, "reference_S11.csv")) as fh:
    ref = np.array([[float(x) for x in r] for r in list(csv.reader(fh))[1:]])
f_ref, s_ref = ref[:, 0], ref[:, 3]

fig, ax = plt.subplots(figsize=(9.2, 4.0), facecolor=SURF)
style(ax)
for lo, hi in ((2.401, 2.483), (5.17, 7.125)):
    ax.axvspan(lo, hi, color="#eeede8", lw=0)
ax.plot(f_hz, s_hz, color=ORANGE, lw=2, label="Run 3 (A13), Hertzon")
ax.plot(f_ref, s_ref, color=BLUE, lw=2, label="Run 3 (A13), reference solver")
ax.set_xlim(2, 8)
ax.set_ylim(-20, 0)
ax.set_xlabel("Frequency (GHz)")
ax.set_ylabel("|S11| (dB)")
ax.legend(loc="center left", bbox_to_anchor=(0.12, 0.4), frameon=False, fontsize=9)
ax.text(2.442, -19.3, "2.4 GHz band", ha="center", fontsize=9, color=MUTED)
ax.text(6.15, -19.3, "5.17-7.125 GHz", ha="center", fontsize=9, color=MUTED)
fig.suptitle("S11 of the final design A13, no matching parts", x=0.01, ha="left", fontsize=12, fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_Run3_S11.png"), dpi=200, facecolor=SURF)
plt.close(fig)

# ---- layout ----------------------------------------------------------------------------------------------------
des = json.load(open(HZN))["designs"][0]
var = {k: float(v.replace("mm", "")) for k, v in des["variables"].items()}


def ev(expr):
    return float(eval(re.sub(r"(?<=\d)mm", "", expr), {}, dict(var)))


obj = {o["name"]: o for o in des["objects"]}


def pts(name):
    p = obj[name]["history"][0]["props"]["Points"]
    return [tuple(float(c) for c in q.split(",")[:2]) for q in p.split(";")]


fig, ax = plt.subplots(figsize=(9.2, 3.0), facecolor=SURF)
style(ax)
ax.add_patch(Polygon(pts("FPC"), closed=True, fc="#d9e6d2", ec="#7c9a6e", lw=1, label="FPC"))
ax.add_patch(Polygon(pts("FPC_slot"), closed=True, fc=SURF, ec="#7c9a6e", lw=1))
c = [float(x) for x in obj["FPC_hole"]["history"][0]["props"]["CenterPosition"].split(",")]
ax.add_patch(Circle(c[:2], float(obj["FPC_hole"]["history"][0]["props"]["Radius"]), fc=SURF, ec="#7c9a6e", lw=1))
first = True
for name in ["Ant", "Ant_stem", "Ant_rdrop", "Ant_rlob", "Ant_rfat", "Ant_lleg", "Ant_larm", "Ant_stub", "Ant_br",
             "Ant_rs"]:
    p = obj[name]["history"][0]["props"]
    x0, y0, _ = (ev(s) for s in p["Position"].split(","))
    ax.add_patch(Rectangle((x0, y0), ev(p["XSize"]), ev(p["YSize"]), fc="#c98a3c", ec="none",
                           label="Antenna copper (top layer)" if first else None))
    first = False
for name, col, lab in (("FeedPin", "#e8c22c", "Feed pin"), ("GndPin", "#3b3b3b", "Ground pin")):
    p = obj[name]["history"][0]["props"]
    x, y, _ = (ev(s) for s in p["CenterPosition"].split(","))
    ax.add_patch(Circle((x, y), ev(p["Radius"]), fc=col, ec=INK, lw=0.6, label=lab, zorder=3))
ax.set_aspect("equal")
ax.set_xlim(-4.2, 15.2)
ax.set_ylim(2.8, 6.9)
ax.set_xlabel("x (mm)")
ax.set_ylabel("y (mm)")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=4, frameon=False, fontsize=9)
fig.suptitle("Antenna A13 on the FPC, CAD coordinates of Meta's model", x=0.01, ha="left", fontsize=12,
             fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(os.path.join(RES, "Ex2_Run3_layout_mm.png"), dpi=200, facecolor=SURF, bbox_inches="tight", pad_inches=0.15)
