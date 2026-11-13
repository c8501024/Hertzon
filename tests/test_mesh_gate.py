"""Initial mesh size of the example projects against stored baselines (tests/data/mesh_baseline.json).

A change to the meshing (size rules, layers, ports, EMerge or gmsh) that makes an initial mesh much larger or
smaller fails here, even when the S-parameters stay right: every adaptive pass and the sweep start from this mesh,
so its size sets the simulation time and memory. A thin-layer limit made one user's model 24 times larger
(0.3.12 to 0.3.21) without any test noticing.

When a change of the mesh is intended, measure the full simulations against the last release (time, passes,
memory, S-parameters), get the change approved, then store the new counts with
HERTZON_UPDATE_MESH_BASELINE=1 python -m pytest tests/test_mesh_gate.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"
BASELINE = Path(__file__).resolve().parent / "data" / "mesh_baseline.json"
TOLERANCE = 0.25          # gmsh differs a little between platforms and versions

# (example, design, variable overrides): thin and large boards are the cases that hide a mesh blow-up
CASES = [
    ("Microstrip", "Microstrip50", {}),
    ("StubFilter", "StubFilter_Layout", {}),
    ("HairpinFilter", "Hairpin_N3", {}),
    ("WaveguidePost", "WR90_Post", {}),
    ("Coax", "Coax50", {}),
    ("DifferentialPair", "CoupledMicrostrip", {}),
    ("GPS_L1_RHCP", "GPS_L1", {}),
    ("PatchAntenna", "Patch245", {}),
    ("PatchAntenna", "Patch245", {"h": "0.25mm"}),
    ("PatchAntenna", "Patch245", {"h": "0.1mm"}),
]


def _key(example, design, overrides):
    extra = ",".join(f"{k}={v}" for k, v in sorted(overrides.items()))
    return f"{example}/{design}" + (f"[{extra}]" if extra else "")


def _initial_tets(tmp_path, example, design, overrides) -> int:
    src = json.loads((EX / f"{example}.hzn").read_text(encoding="utf-8"))
    src["designs"] = [d for d in src["designs"] if d["name"] == design]
    d = src["designs"][0]
    d["variables"].update(overrides)
    path = tmp_path / "p.hzn"
    path.write_text(json.dumps(src), encoding="utf-8")
    env = dict(os.environ, HERTZON_MESH_ONLY="1", PYTHONPATH=str(ROOT))
    out = subprocess.run([sys.executable, "-m", "hertzon.solver.run", str(path), design, d["setups"][0]["name"],
                          str(tmp_path / "out")], env=env, capture_output=True, text=True, timeout=900)
    mesh = tmp_path / "out" / "mesh.npz"
    assert mesh.exists(), out.stdout[-3000:] + out.stderr[-3000:]
    return int(len(np.load(mesh)["tets"]))


@pytest.mark.parametrize("case", CASES, ids=lambda c: _key(*c))
def test_initial_mesh_size_matches_the_baseline(tmp_path, case):
    key = _key(*case)
    n = _initial_tets(tmp_path, *case)
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    if os.environ.get("HERTZON_UPDATE_MESH_BASELINE") == "1":
        base[key] = n
        BASELINE.write_text(json.dumps(base, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert key in base, f"no baseline for {key} ({n} tetrahedra); see the module docstring"
    ref = base[key]
    assert abs(n - ref) <= TOLERANCE * ref, (
        f"{key}: initial mesh {n} tetrahedra, baseline {ref} (more than {TOLERANCE:.0%} apart). A changed mesh "
        "changes the time and memory of every simulation: measure against the last release before updating.")
