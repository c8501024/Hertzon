"""Temporary (removed before the PR): projects of the Mac fast sweep comparison."""
import json, sys
from pathlib import Path
EX = Path(sys.argv[1]) / "examples"
out = Path(sys.argv[2]); out.mkdir(exist_ok=True)

def ex(name, design, over, fn, sweep=None, keep=None):
    s = json.load(open(EX / f"{name}.hzn"))
    s["designs"] = [d for d in s["designs"] if d["name"] == design]
    s["designs"][0]["variables"].update(over)
    for st in s["designs"][0]["setups"]:
        sws = st.get("sweeps", [])
        if keep:
            st["sweeps"] = sws = [sw for sw in sws if sw.get("name") == keep]
        if sweep:
            for sw in sws:
                sw.setdefault("props", {})["SweepType"] = sweep
    json.dump(s, open(out / fn, "w"))

ex("Dipole", "HalfWave1GHz", {}, "dipole.hzn", keep="FastFields")
ex("PatchAntenna", "Patch245", {"h": "0.25mm"}, "patch025fast.hzn", sweep="Fast")
ex("PatchAntenna", "Patch245", {"h": "0.1mm"}, "patch01fast.hzn", sweep="Fast")
ex("GPS_L1_RHCP", "GPS_L1", {}, "gps.hzn")
