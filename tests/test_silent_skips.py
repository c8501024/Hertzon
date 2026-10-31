"""Assignments and mesh operations that miss their target say so instead of disappearing silently, and a NaN in
the error estimate leaves those tetrahedra out of the refinement instead of ending the analysis."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.model.design import Assignment, Project  # noqa: E402
from hertzon.model.validate import validate  # noqa: E402
from hertzon.solver import bridge as B  # noqa: E402

EX = ROOT / "examples"


def _logs(out: str, level: str = "warning") -> list[str]:
    msgs = []
    for line in out.splitlines():
        if line.startswith(B.TAG):
            d = json.loads(line[len(B.TAG):])
            if d.get("type") == "log" and d.get("level") == level:
                msgs.append(d["msg"])
    return msgs


def test_validation_warns_about_an_edge_operation_without_edges():
    p = Project.load(EX / "WaveguidePost.hzn")
    d = p.designs[0]
    d.mesh_ops.append(Assignment("EdgeOp", "Length Based On Edges", {"MaxLength": "0.5mm"}, objects=["Waveguide"]))
    checks = [c for c in validate(p, d) if c.category == "Mesh Operations"]
    assert any(c.level == "warning" and "'EdgeOp'" in c.message and "no edges" in c.message for c in checks), checks


def test_finite_error_leaves_nan_tets_out_and_names_them(capsys):
    class Field:
        _der = np.zeros((3, 3, 5), complex)
        _dur = np.zeros((3, 3, 5), complex)
    for i in range(3):
        Field._der[i, i] = 4.5 - 0.0945j
        Field._dur[i, i] = 1
    err = B.finite_error(np.array([1.0, np.nan, 2.0, np.inf, 3.0]), Field(), 1)
    assert np.array_equal(err, [1.0, 0.0, 2.0, 0.0, 3.0])
    msg = _logs(capsys.readouterr().out)
    assert len(msg) == 1 and "2 of 5" in msg[0] and "tet 1" in msg[0] and "4.5-0.0945j" in msg[0]
    # nothing to refine: the caller stops the adaptive loop with the pass it has
    assert B.finite_error(np.full(3, np.nan), Field(), 2) is None
    assert "stops" in _logs(capsys.readouterr().out)[0]
    # a finite estimate passes through unchanged and quietly
    e = np.array([0.1, 0.2])
    assert B.finite_error(e) is e or np.array_equal(B.finite_error(e), e)
    assert not _logs(capsys.readouterr().out)


def test_mesh_operations_and_boundaries_that_miss_their_target_warn(tmp_path, capsys):
    em = pytest.importorskip("emerge")
    p = Project.load(EX / "WaveguidePost.hzn")
    d = p.designs[0]
    d.mesh_ops[:] = [
        Assignment("EdgeOnObjects", "Length Based On Edges", {"MaxLength": "0.5mm"}, objects=["Waveguide"]),
        Assignment("Gone", "Length Based On Selection", {"MaxLength": "1mm"}, objects=["NoSuchObject"]),
        Assignment("GoneInside", "Length Based Inside Selection", {"MaxLength": "1mm"}, objects=["NoSuchObject"]),
        Assignment("Good", "Length Based On Selection", {"MaxLength": "2mm"}, objects=["Waveguide"]),
    ]
    st = d.setups[0]
    st.props.update(LambdaRefinement="False", CurvedElements="False")
    b = B.Bridge(p, d, st, tmp_path)
    with em.Simulation("silent_skips", save_file=False) as m:
        b.prepare(m)
        sel, tags = b.selection_for(Assignment("Bnd", "Perfect E", {}, objects=["Waveguide", "NoSuchObject"]))
    out = capsys.readouterr().out
    warns, infos = _logs(out), _logs(out, "info")
    assert any("EdgeOnObjects" in w and "no edges assigned" in w and "only objects" in w for w in warns), warns
    assert any(w.startswith("Gone:") and "not applied" in w for w in warns), warns
    assert any(w.startswith("GoneInside:") and "not applied" in w for w in warns), warns
    assert any("Bnd: object 'NoSuchObject' is not part of the solve" in w for w in warns), warns
    assert tags            # the object that exists is still selected
    applied = [i for i in infos if i.startswith("Mesh operation")]
    assert applied == ["Mesh operation 'Good' applied"], applied
