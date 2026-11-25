"""Initial mesh of imported CAD bodies that only mesh with a workaround (bridge.Bridge.raise_mesh_retry).

Two metal blocks whose faces meet at 0.075 degrees leave an air wedge of 0 to 13 um: gmsh stops with
"Invalid boundary mesh (overlapping facets)". The solver closes the wedge and meshes again; the analysis says so
in a warning. A model that meshes at once must not be touched by the workaround."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


_BLOCKS = """
import math, sys, gmsh
gmsh.initialize()
gmsh.option.setNumber("General.Terminal", 0)
occ = gmsh.model.occ
occ.addBox(0, 0, 0, 20, 10, 0.6)
b = occ.addBox(20, 0, 0.3, 20, 10, 2.0)
occ.rotate([(3, b)], 20, 0, 0, 0, 0, 1, -math.radians(float(sys.argv[2])))
occ.synchronize()
gmsh.write(sys.argv[1])
gmsh.finalize()
"""


def _write_blocks(path: Path, angle_deg: float) -> None:
    """Two blocks (mm) in a STEP file: a board 20 x 10 x 0.6 and, beside its end face, a block turned about
    the z axis through (20, 0) so that the gap between them grows from 0 to 10 mm * tan(angle). Written in a
    process of its own: gmsh options other tests leave behind (the STEP unit) must not change the file."""
    subprocess.run([sys.executable, "-c", _BLOCKS, str(path), str(angle_deg)], check=True, capture_output=True,
                   timeout=300)


def _project(step: Path) -> dict:
    def body(name, idx, material, color):
        return {"name": name, "history": [{"kind": "Import", "props": {"File": str(step), "Body": str(idx),
                                                                        "Dim": "3"}, "suppressed": False}],
                "material": material, "solve_inside": False, "model": True, "color": color, "transparency": 0.0,
                "wireframe": False, "visible": True, "consumed_by": None, "group": "", "orientation": "Global"}
    design = {"name": "Blocks", "units": "mm", "kind": "3d",
              "settings": {"SolutionType": "Modal", "AutoOpen": "Auto Open", "AutoOpenBoundary": "Auto",
                           "AutoOpenPadding": "", "MaterialOverride": "True"},
              "variables": {}, "objects": [body("Board", 0, "copper", "#c98a3c"),
                                           body("Block", 1, "stainless_steel", "#9aa0a6")],
              "boundaries": [], "excitations": [], "mesh_ops": [],
              "setups": [{"name": "S1", "props": {"Frequency": "6GHz", "MaximumPasses": "2"}, "sweeps": [],
                          "eigen": False}],
              "spheres": [], "reports": [], "field_plots": [], "coord_systems": [], "field_exprs": [],
              "field_funcs": [], "optimetrics": [], "parametrics": [], "working_cs": "Global", "notes": "",
              "stackup": None, "components": [], "wires": []}
    return {"name": "blocks", "designs": [design], "variables": {}, "materials": [], "format": "hertzon-project",
            "version": 2}


def _mesh(tmp_path: Path, angle_deg: float):
    step = tmp_path / "blocks.step"
    _write_blocks(step, angle_deg)
    path = tmp_path / "blocks.hzn"
    path.write_text(json.dumps(_project(step)), encoding="utf-8")
    env = dict(os.environ, HERTZON_MESH_ONLY="1", PYTHONPATH=str(ROOT))
    out = subprocess.run([sys.executable, "-m", "hertzon.solver.run", str(path), "Blocks", "S1",
                          str(tmp_path / "out")], env=env, capture_output=True, text=True, timeout=900)
    mesh = tmp_path / "out" / "mesh.npz"
    return out, (int(len(np.load(mesh)["tets"])) if mesh.exists() else 0)


def test_air_wedge_between_two_metal_blocks_is_closed(tmp_path):
    out, tets = _mesh(tmp_path, 0.075)
    log = out.stdout + out.stderr
    assert tets > 0, log[-4000:]
    assert "the gap was filled with the metal of 'Board'" in log or "the gap was filled with the metal of 'Block'" \
        in log, log[-4000:]


def test_blocks_that_mesh_at_once_are_not_changed(tmp_path):
    out, tets = _mesh(tmp_path, 3.0)        # a gap of 0.5 mm: meshes without the workaround
    log = out.stdout + out.stderr
    assert tets > 0, log[-4000:]
    assert "gap was filled" not in log and "were rebuilt" not in log, log[-4000:]


@pytest.mark.parametrize("message, pair", [
    ("Invalid boundary mesh (overlapping facets) on surface 164 surface 275", (164, 275)),
    ("PLC Error:  A segment and a facet intersect at point", None),
])
def test_failing_surfaces_are_read_from_the_gmsh_message(message, pair):
    from hertzon.solver import cad_repair
    assert cad_repair.failing_surfaces(message) == pair
