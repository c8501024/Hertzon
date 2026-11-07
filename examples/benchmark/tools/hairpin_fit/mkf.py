# two tapped hairpins (a 2nd order filter): fit of the coupled resonator circuit -> f0, k, Qe, Qu
import sys
from hertzon.model.design import Project
from hertzon.model.filters import HairpinSpec, hairpin_design
out, L, g, yP = sys.argv[1:5]; dL = sys.argv[5] if len(sys.argv) > 5 else "0mm"
d, nom = hairpin_design(HairpinSpec(n=3, optimization=False))
d.variables.update(L=L, g=g, yP=yP, dL=dL)
d.setups[0].props.update(Frequency="2.45GHz", MaximumDeltaS="0.0001", MaximumPasses="12", MinimumConvergedPasses="2")
d.setups[0].sweeps[0].props.update(Start="2.0GHz", Stop="2.9GHz", Step="0.005GHz", MaxSolutions="60", ErrorTolerance="0.5")
d.name = "Full"
p = Project("pair"); p.designs = [d]; p.save(out)
