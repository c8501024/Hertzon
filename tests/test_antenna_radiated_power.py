"""Radiated power of an antenna in Auto Open (#231): integrated on a box of Gauss points close around the model,
not on the mesh faces of the absorbing boundary, where it came out up to ~10 % above the accepted power of
lossless antennas. A lossless (PEC) half-wave dipole radiates all the power it accepts."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_lossless_dipole_radiates_the_accepted_power(tmp_path, monkeypatch):
    from hertzon.solver import bridge
    d = json.loads((EXAMPLES / "Dipole.hzn").read_text(encoding="utf-8"))
    design = next(x for x in d["designs"] if x["name"] == "HalfWave1GHz")
    d["designs"] = [design]
    setup = design["setups"][0]
    setup["props"].update(MaximumPasses="3", MinimumPasses="3", MinimumConvergedPasses="1")
    for sw in setup.get("sweeps", []):
        sw["props"]["Enabled"] = "False"
    proj = tmp_path / "dipole.hzn"
    proj.write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / "out"
    assert bridge.run(str(proj), design["name"], setup["name"], str(out)) == 0
    h = np.load(out / "huygens_LastAdaptive.npz")
    vis, wns = h["vis"], h["wns"]
    box = json.loads((out / "auto_open.json").read_text())["box"]
    lo, hi = np.array(box[:3]), np.array(box[:3]) + np.array(box[3:])
    # a closed surface strictly inside the Auto Open region, away from the radiation boundary
    assert np.linalg.norm(wns.sum(axis=1)) < 1e-9
    assert np.all(vis.min(axis=1) > lo + 0.02) and np.all(vis.max(axis=1) < hi - 0.02)
    S = np.load(out / "sparams_LastAdaptive.npz")["S"]
    pacc = 1 - abs(S.ravel()[0]) ** 2
    assert abs(float(h["Prad_0_1"][0]) / pacc - 1) < 0.01

    # Plot Mesh on the Auto Open region (no model object): its open boundary, see-through
    from hertzon.gui import fields as F
    from hertzon.model.design import Project
    p = Project.load(proj)
    doc = SimpleNamespace(tess={}, design=p.design(design["name"]))
    monkeypatch.setattr(F, "result_dir", lambda _doc, _setup: out)
    meshes, info = F.mesh_overlay(doc, SimpleNamespace(actors={}), setup["name"], [F.AUTO_OPEN_REGION])
    poly, kw = meshes[0]
    assert kw["opacity"] < 1 and poly.n_cells > 0
    b = np.array(poly.bounds).reshape(3, 2)
    assert np.allclose(b[:, 0], lo, atol=1e-9) and np.allclose(b[:, 1], hi, atol=1e-9)
    assert "Auto Open region" in info
