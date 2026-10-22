"""Build a Hertzon example (.hzn) as an EMerge model via Hertzon's own Bridge."""
import json
import tempfile
from pathlib import Path

from hertzon.model.design import project_from_dict
from hertzon.solver.bridge import Bridge


def build(m, hzn, adaptive=False, setup="Setup1"):
    proj = project_from_dict(json.loads(Path(hzn).read_text()))
    proj.path = str(Path(hzn).resolve())
    des = proj.designs[0]
    st = des.setup(setup)
    out = Path(tempfile.mkdtemp())
    br = Bridge(proj, des, st, out)
    br.prepare(m)
    if adaptive:
        br.run_adaptive()
    return br
