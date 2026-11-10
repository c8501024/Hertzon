"""Antenna layouts of the final designs of Run 2, Run 3 and Run 4 on the FPC, to the same scale, from their Hertzon
projects (CAD coordinates of Meta's model, mm)."""
import json, os, re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle, Circle

HERE = os.path.dirname(os.path.abspath(__file__))
EX2 = os.path.join(HERE, "..", "..")
RUNS = [("Run 2 (faulty): dual-branch IFA, 64.2 %", os.path.join(EX2, "run2", "Meta_Ex2_DualBranchIFA.hzn")),
        ("Run 3: IFA with a wide branch on the feed stem, 68.6 %", os.path.join(EX2, "run3", "Meta_Ex2_Run3.hzn")),
        ("Run 4: Run 3 retuned plus a grounded parasitic, 70.2 %", os.path.join(EX2, "run4", "Meta_Ex2_Run4.hzn"))]

COPPER, PARASITIC, INK, MUTED, SURF, GRID = "#c98a3c", "#eb6834", "#0b0b0b", "#52514e", "#fcfcfb", "#e6e5e0"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": "#c9c8c2", "text.color": INK, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "font.family": "DejaVu Sans"})


def evaluator(variables):
    raw = {k: re.sub(r"(?<=\d)mm", "", v) for k, v in variables.items()}
    val = {}
    for _ in range(len(raw)):
        for k, v in raw.items():
            if k not in val:
                try:
                    val[k] = float(eval(v, {}, dict(val)))
                except NameError:
                    pass
    return lambda expr: float(eval(re.sub(r"(?<=\d)mm", "", expr), {}, dict(val)))


fig, axes = plt.subplots(3, 1, sharex=True, figsize=(9.2, 7.6), facecolor=SURF)
for ax, (title, path) in zip(axes, RUNS):
    des = json.load(open(path, encoding="utf-8"))["designs"][0]
    ev = evaluator(des["variables"])
    obj = {o["name"]: o for o in des["objects"]}
    ax.set_facecolor(SURF)
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fpc = [tuple(float(c) for c in q.split(",")[:2]) for q in obj["FPC"]["history"][0]["props"]["Points"].split(";")]
    slot = [tuple(float(c) for c in q.split(",")[:2])
            for q in obj["FPC_slot"]["history"][0]["props"]["Points"].split(";")]
    hole = obj["FPC_hole"]["history"][0]["props"]
    ax.add_patch(Polygon(fpc, closed=True, fc="#d9e6d2", ec="#7c9a6e", lw=1, label="FPC"))
    ax.add_patch(Polygon(slot, closed=True, fc=SURF, ec="#7c9a6e", lw=1))
    ax.add_patch(Circle([float(c) for c in hole["CenterPosition"].split(",")[:2]], float(hole["Radius"]), fc=SURF,
                        ec="#7c9a6e", lw=1))
    seen = set()
    for o in des["objects"]:
        first = o["history"][0]
        if o["material"] != "copper" or first["kind"] != "CreateRectangle":
            continue
        p = first["props"]
        x0, y0, z0 = (ev(s) for s in p["Position"].split(","))
        if abs(z0) > 0.3:
            continue
        par = o["name"].startswith("Par")
        lab = ("Grounded parasitic" if par else "Antenna copper")
        ax.add_patch(Rectangle((x0, y0), ev(p["XSize"]), ev(p["YSize"]), fc=PARASITIC if par else COPPER, ec="none",
                               label=None if lab in seen else lab))
        seen.add(lab)
    for o in des["objects"]:
        if o["material"] == "copper" and o["history"][0]["kind"] == "CreateCylinder" and "Pin" in o["name"]:
            p = o["history"][0]["props"]
            x, y, _ = (ev(s) for s in p["CenterPosition"].split(","))
            feed = o["name"] == "FeedPin"
            lab = "Feed pin" if feed else "Ground pin"
            ax.add_patch(Circle((x, y), ev(p["Radius"]), fc="#e8c22c" if feed else "#3b3b3b", ec=INK, lw=0.6,
                                zorder=3, label=None if lab in seen else lab))
            seen.add(lab)
    ax.set_aspect("equal")
    ax.set_xlim(-4.2, 15.2)
    ax.set_ylim(2.8, 6.9)
    ax.set_title(title, fontsize=10, loc="left", color=MUTED)
    ax.set_ylabel("y (mm)")
axes[-1].set_xlabel("x (mm)")
handles, labels = [], []
for ax in axes:
    for h, l in zip(*ax.get_legend_handles_labels()):
        if l not in labels:
            handles.append(h)
            labels.append(l)
fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False, fontsize=9)
fig.suptitle("Final antennas of Runs 2 to 4 on the FPC (equal-weight total efficiency, reference solver)", x=0.01,
             ha="left", fontsize=12, fontweight="bold")
fig.tight_layout(rect=(0, 0.05, 1, 0.96))
fig.savefig(os.path.join(EX2, "run4", "results", "Ex2_layouts_run2_run4.png"), dpi=200, facecolor=SURF)
