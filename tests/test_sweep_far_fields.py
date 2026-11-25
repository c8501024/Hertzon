"""Save far fields of a sweep: a Discrete or Fast sweep stores the far field data (fields on the radiation
boundary) at every frequency without the volume fields, in a run from the command line as in the window.
Save fields includes it; an Interpolating sweep and a sweep with both off store none."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model import schema  # noqa: E402
from hertzon.model.design import Setup, Sweep  # noqa: E402
from hertzon.solver.keys import setup_key  # noqa: E402


def test_which_sweeps_save_far_fields():
    base = schema.defaults(schema.SWEEP)
    assert base["SaveFarFields"] == "False" and not schema.sweep_saves_far_fields(base)
    for kind in ("Discrete", "Fast"):
        assert schema.sweep_saves_far_fields({**base, "SweepType": kind, "SaveFarFields": "True"})
        assert schema.sweep_saves_far_fields({**base, "SweepType": kind, "SaveFields": "True"})
        assert not schema.sweep_saves_far_fields({**base, "SweepType": kind})
    assert not schema.sweep_saves_far_fields({**base, "SweepType": "Interpolating", "SaveFarFields": "True"})


def test_the_option_off_keeps_solutions_of_older_projects_valid():
    old = Setup("Setup1", sweeps=[Sweep("Sweep", {k: v for k, v in schema.defaults(schema.SWEEP).items()
                                                  if k != "SaveFarFields"})])
    new = Setup("Setup1", sweeps=[Sweep("Sweep", schema.defaults(schema.SWEEP))])
    assert setup_key(old) == setup_key(new)
    new.sweeps[0].props["SaveFarFields"] = "True"
    assert setup_key(old) != setup_key(new)


def test_headless_discrete_sweep_saves_far_fields_only(tmp_path):
    pytest.importorskip("emerge")
    d = json.loads((ROOT / "examples" / "Dipole.hzn").read_text(encoding="utf-8"))
    design = next(x for x in d["designs"] if x["name"] == "HalfWave1GHz")
    d["designs"] = [design]
    setup = design["setups"][0]
    setup["props"].update(MaximumPasses="2", MinimumPasses="2", MinimumConvergedPasses="1", SaveFields="False")
    sw = dict(setup["sweeps"][0], name="FF")
    sw["props"] = dict(sw["props"], SweepType="Discrete", Distribution="Linear Count", Start="0.9GHz",
                       Stop="1.1GHz", Count="2", SaveFields="False", SaveFarFields="True")
    setup["sweeps"] = [sw]
    proj = tmp_path / "dipole.hzn"
    proj.write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / "out"
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "hertzon.solver.run", str(proj), design["name"], setup["name"],
                        str(out)], env=env, capture_output=True, text=True, timeout=1200)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    h = np.load(out / "huygens_FF.npz")
    assert sorted(round(float(h[f"freq_{k}"][0]) / 1e9, 6) for k in range(2)) == [0.9, 1.1]
    sphere = design["spheres"][0]["name"]
    ff = np.load(out / f"farfield_{sphere}_FF.npz")
    assert any(k.startswith("freq_") for k in ff.files)
    # the volume fields of the sweep are not stored
    assert not list(out.glob("fields_FF*")) and not list(out.glob("solution_FF*"))
