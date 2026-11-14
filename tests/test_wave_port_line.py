"""Voltage line of a wave port in a modal solution: the Integration Line of the port, or, with more than two
conductors on the port face (a grounded coplanar line whose top grounds are not joined to the ground plane in the
port plane), a line from the narrowest to the largest conductor instead of no line impedance at all."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_terminal import _wire_design  # noqa: E402


def _solve(tmp_path, wires, line=None, name="s"):
    em = pytest.importorskip("emerge")
    from hertzon.solver import bridge as B
    from hertzon.solver.results import ResultStore
    p, d, st = _wire_design("Modal", wires=wires)
    for e, y in zip(d.excitations, ("0", "20mm")):
        e.props["NumberOfModes"] = "1"
        e.props["Renormalize"] = "False"
        if line is not None:
            e.props["IntegrationLine"] = line.format(y=y)
    logs = []
    emit = B.emit
    B.emit = lambda kind, **kw: logs.append((kind, kw)) or emit(kind, **kw)
    try:
        with em.Simulation(f"waveline_{name}", save_file=False) as m:
            b = B.Bridge(p, d, st, tmp_path / name)
            b.prepare(m)
            b.run_adaptive()
    finally:
        B.emit = emit
    msgs = [kw.get("msg", "") for k, kw in logs if k == "log"]
    return ResultStore(tmp_path / name).sparams("LastAdaptive"), msgs


def test_integration_line_sets_the_line_impedance(tmp_path):
    """A wire in a box (two conductors): the drawn line from the wire down to the wall gives the impedance of the
    line EMerge finds by itself (TEM: one potential per conductor), and it is reported."""
    auto, _ = _solve(tmp_path, ("1mm",), name="auto")
    drawn, msgs = _solve(tmp_path, ("1mm",), line="1mm,{y},-1mm;1mm,{y},-5mm", name="drawn")
    assert any("(Integration Line)" in m for m in msgs)
    assert np.all(np.isfinite(auto["zo"])) and np.all(auto["zo"].real > 20)
    assert np.allclose(drawn["zo"].real, auto["zo"].real, rtol=0.03)
    assert np.abs(drawn["S"] - auto["S"]).max() < 1e-9          # the line only sets Zo, not the modal S


def test_more_than_two_conductors_get_a_line_and_a_warning(tmp_path):
    """Two wires in a box with one mode: three conductors on the port face. EMerge alone finds no conductor pair
    (no Zo, 'PEC islands without a terminal definition'); Hertzon takes a line from a wire to the box and warns
    that the wires float in the port mode."""
    r, msgs = _solve(tmp_path, ("-2mm", "2mm"), name="three")
    assert any("3 separate conductors on the port face" in m for m in msgs)
    assert not any("PEC islands without a terminal definition" in m for m in msgs)
    z = r["zo"].real
    assert np.all(np.isfinite(z)) and np.all((z > 20) & (z < 500)) and not np.allclose(z, 50.0)
