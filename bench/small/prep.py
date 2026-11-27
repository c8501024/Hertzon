"""Small example models for the small-matrix comparison (temporary, removed before the pull request)."""
import json, sys
from pathlib import Path
EX = Path(sys.argv[1]) / "examples"
out = Path(sys.argv[2]); out.mkdir(exist_ok=True)
for name, design, fn in [("Microstrip", "Microstrip50", "microstrip"), ("WaveguidePost", "WR90_Post", "wr90post"),
                         ("Coax", "Coax50", "coax"), ("GroundedCPW", "GCPW50", "gcpw"),
                         ("Dipole", "HalfWave1GHz_Half", "dipole"), ("DifferentialPair", "CoupledMicrostrip", "diffpair")]:
    s = json.load(open(EX / f"{name}.hzn"))
    s["designs"].sort(key=lambda d: d["name"] != design)      # run.py solves the first design
    json.dump(s, open(out / f"{fn}.hzn", "w"))
