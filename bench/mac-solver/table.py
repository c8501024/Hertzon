"""Markdown table of all <label>_<proj>.json in the folder, |dS| against the new Auto run."""
import glob, json, sys
import numpy as np

def read_snp(fn):
    f, rows = [], []
    nums = []
    for l in open(fn):
        l = l.split("!")[0].strip()
        if not l or l.startswith("#"):
            continue
        nums += [float(x) for x in l.split()]
    return np.array(nums)

R = [json.load(open(f)) for f in sorted(glob.glob("*.json"))]
ref = {r["proj"]: r for r in R if r["label"] == "new"}
print("| Modell | Lauf | Pässe | Tets | Zeit (s) | RAM (GB) | max dS vs neu | Solver laut Log |")
print("|---|---|---|---|---|---|---|---|")
for r in sorted(R, key=lambda r: (r["proj"], r["label"])):
    d = ""
    try:
        a, b = read_snp(r["sNp"]), read_snp(ref[r["proj"]]["sNp"])
        if a.shape == b.shape:
            d = f"{np.max(np.abs(a - b)):.1e}"
    except Exception:  # noqa: BLE001
        pass
    print(f"| {r['proj']} | {r['label']} | {r.get('passes')} | {r.get('tets')} | {r['time_s']} | {r['peak_gb']} | {d} | "
          f"{'; '.join(r.get('solver_lines', []))[:200]} |")
