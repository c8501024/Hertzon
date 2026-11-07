# one tapped hairpin with its feed and port (driven): S11 group delay -> f0, Qe
import sys
from hertzon.model.design import Project
from hertzon.model.filters import HairpinSpec, hairpin_design
out, L, yP = sys.argv[1], sys.argv[2], sys.argv[3]
d, nom = hairpin_design(HairpinSpec(n=3, optimization=False))
d.variables["L"] = L; d.variables["yP"] = yP
keep = {"Substrate", "Ground", "Hairpin1", "Feed1", "Port1"}
d.objects = [o for o in d.objects if o.name in keep]
d.excitations = [e for e in d.excitations if e.name == "1"]
for b in list(d.boundaries) + list(d.mesh_ops):
    b.objects = [x for x in b.objects if x in keep]
sw = d.setups[0].sweeps[0]
sw.props.update(Start="2.1GHz", Stop="2.7GHz", Step="0.002GHz", MaxSolutions="60", ErrorTolerance="0.5")
d.setups[0].props.update(MaximumDeltaS="0.0001", MaximumPasses="13", MinimumConvergedPasses="2")
d.name = "Tap1"
p = Project("tap"); p.designs = [d]; p.save(out)
print(nom["length"], nom["yP"])
