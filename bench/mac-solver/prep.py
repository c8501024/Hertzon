"""Projects of the Mac solver comparison (temporary, removed before the pull request)."""
import json, sys
from pathlib import Path
EX = Path(sys.argv[1]) / "examples"
out = Path(sys.argv[2]); out.mkdir(exist_ok=True)

def ex(name, design, over, fn, sweep=None):
    s = json.load(open(EX / f"{name}.hzn"))
    s["designs"] = [d for d in s["designs"] if d["name"] == design]
    s["designs"][0]["variables"].update(over)
    if sweep:
        for st in s["designs"][0]["setups"]:
            for sw in st.get("sweeps", []):
                sw.setdefault("props", {})["SweepType"] = sweep
    json.dump(s, open(out / fn, "w"))

ex("PatchAntenna", "Patch245", {"h": "0.25mm"}, "patch025.hzn")
ex("PatchAntenna", "Patch245", {"h": "0.1mm"}, "patch01.hzn")
ex("PatchAntenna", "Patch245", {"h": "0.25mm"}, "patch025fast.hzn", sweep="Fast")
ex("HairpinFilter", "Hairpin_N3", {}, "hairpin.hzn")
ex("Microstrip", "Microstrip50", {}, "microstrip.hzn")
sys.path.insert(0, sys.argv[1])
from hertzon.model.design import Project
from hertzon.model import structures as ST
p = Project("edge"); r = ST.create_structure("edge", {}, project=p); p.designs += r.designs; p.materials += r.materials
p.designs.sort(key=lambda d: d.name != "EdgeBandpass_5GHz")   # run.py solves the first design
p.save(str(out / "edge.hzn"))
print("edge designs", [d.name for d in p.designs])
