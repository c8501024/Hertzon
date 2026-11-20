"""Failed analyses report their own cause: a crash in an adaptive pass is not turned into a port message, the
step of the failure is named, the mesh is checked after a local refinement, the memory guard counts what other
programs use, and a solver killed for lack of memory says so."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import bridge as B  # noqa: E402
from hertzon.solver.results import exit_reason  # noqa: E402


def _guide(freq="10GHz", passes="3", method=None):
    pytest.importorskip("emerge")
    from hertzon.model import schema
    from hertzon.model.design import Assignment, Project, Setup
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", "Guide", Position="0,0,0", XSize="22.86mm", YSize="40mm", ZSize="10.16mm",
                 material="vacuum")
    for i, y in enumerate(("0", "40mm"), 1):
        d.add_object("CreateRectangle", f"P{i}", Position=f"0,{y},0", Axis="Y", XSize="22.86mm", ZSize="10.16mm")
        props = schema.defaults(schema.EXCITATIONS["Wave Port"])
        props.update(PortNumber=str(i))
        d.excitations.append(Assignment(f"WP{i}", "Wave Port", props, objects=[f"P{i}"]))
    st = Setup("Setup1")
    st.props.update(Frequency=freq, MaximumPasses=passes, MinimumPasses=passes, MaximumDeltaS="1e-9")
    if method:
        st.props["RefinementMethod"] = method
    d.setups.append(st)
    return p, d, st


def test_failure_message_names_the_step():
    B._PROGRESS["text"] = "Pass 3: refining the mesh"
    try:
        msg = B.failure_message(RuntimeError("hzmesh produced an invalid mesh"))
    finally:
        B._PROGRESS["text"] = ""
    assert msg == "hzmesh produced an invalid mesh (in step 'Pass 3: refining the mesh')"


def test_crash_in_a_pass_is_not_reported_as_a_port_without_modes(tmp_path, monkeypatch):
    """The ports of a WR-90 at 10 GHz carry their mode. A crash in the second pass (here: the error estimate,
    standing for a crash of the mesh refinement) used to come out as "no mode propagates" for every port, as
    the port modes had been cleared; it has to be reported as it is, with its step."""
    p, d, st = _guide()
    calls = {"n": 0}
    real = B.finite_error

    def crash(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("refinement crashed")
        return real(*a, **k)

    monkeypatch.setattr(B, "finite_error", crash)
    with pytest.raises(RuntimeError) as e:
        B.Bridge(p, d, st, tmp_path).run()
    assert "refinement crashed" in str(e.value)
    assert "no mode" not in str(e.value)
    assert B.failure_message(e.value).endswith("(in step 'Pass 2: estimating solution error')")


def test_defective_local_refinement_is_re_meshed(tmp_path, monkeypatch, capfd):
    """A local refinement (hzAURA) that leaves a mesh the solver cannot use is replaced by a re-mesh of that
    pass; the analysis goes on."""
    p, d, st = _guide(passes="2", method="hzAURA (EMerge)")
    calls = {"n": 0}
    real = B.mesh_defects

    def defects(m):
        calls["n"] += 1
        return "wave port 2: face nodes up to 0.1 mm off the port plane" if calls["n"] == 2 else real(m)

    monkeypatch.setattr(B, "mesh_defects", defects)
    B.Bridge(p, d, st, tmp_path).run()
    out = capfd.readouterr().out
    assert "Pass 1: the locally refined mesh is not usable (wave port 2: face nodes" in out
    assert "stable re-mesh" in out
    assert (tmp_path / "sparams_LastAdaptive.npz").exists()


def test_mesh_defects_finds_a_port_node_off_its_plane(tmp_path, monkeypatch):
    p, d, st = _guide(passes="1")
    seen = {}
    real = B.mesh_defects

    def spy(m):
        from emerge._emerge.physics.microwave.bcs.port_bcs import ModalPort
        seen["clean"] = real(m)
        bc = m.mw.bc.oftype(ModalPort)[-1]
        node = m.mesh.get_nodes(list(bc.selection.tags))[0]
        keep = m.mesh.nodes
        X = np.array(keep)
        X[:, node] += 1e-4
        m.mesh.nodes = X
        seen["moved"], seen["port"] = real(m), bc.port_number
        m.mesh.nodes = keep
        return seen["clean"]

    monkeypatch.setattr(B, "mesh_defects", spy)
    B.Bridge(p, d, st, tmp_path).run()
    assert seen["clean"] == ""
    assert f"wave port {seen['port']}: face nodes up to 0.0" in seen["moved"]
    assert "mm off the port plane" in seen["moved"]


def test_usable_memory_leaves_out_what_others_use(monkeypatch):
    monkeypatch.setattr(B, "available_memory", lambda: 32 * 2 ** 30)
    monkeypatch.setattr(B, "_current_rss", lambda: 2 * 2 ** 30)
    monkeypatch.setattr(B, "_system_free_memory", lambda: 10 * 2 ** 30)
    assert B.usable_memory() == pytest.approx(12 * 2 ** 30)
    monkeypatch.setattr(B, "_system_free_memory", lambda: 0.0)      # unknown: the whole memory, as before
    assert B.usable_memory() == pytest.approx(32 * 2 ** 30)


def test_warning_before_a_solve_that_does_not_fit(monkeypatch, capfd):
    monkeypatch.setattr(B, "memory_forecast", lambda n, c: (40 * 2 ** 30, 30 * 2 ** 30))
    assert B.warn_memory_before_solve("Pass 4", 320000, 250000)
    assert "Pass 4: solving 320000 tetrahedra needs about 40.0 GB of memory, 30.0 GB are free" in capfd.readouterr().out
    monkeypatch.setattr(B, "memory_forecast", lambda n, c: (20 * 2 ** 30, 30 * 2 ** 30))
    assert not B.warn_memory_before_solve("Pass 4", 320000, 250000)


@pytest.mark.parametrize("code", [-9, 137])
def test_killed_solver_is_reported_as_out_of_memory(code):
    assert "memory ran out" in exit_reason(code)


def test_access_violation_is_not_called_out_of_memory():
    assert "access violation" in exit_reason(-1073741819)
    assert exit_reason(1, crashed=False) == ""
