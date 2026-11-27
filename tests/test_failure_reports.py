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
    monkeypatch.setattr(B, "cgroup_memory", lambda: (0.0, 0.0))
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


def _cgroup_tree(tmp_path, proc: str, files: dict[str, str]):
    for rel, text in files.items():
        f = tmp_path / "cg" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    (tmp_path / "proc").write_text(proc)
    return tmp_path / "cg", tmp_path / "proc"


def test_memory_limit_of_the_own_cgroup_v1(tmp_path, monkeypatch):
    """Cloud container (cgroup v1): the root says unlimited, the solver's own group is limited to 14.0 GB;
    the guard planned with the 15.3 GB of the machine and the operating system ended the solver."""
    own = "memory/process_api/abc/claude-code-bash"
    root, proc = _cgroup_tree(tmp_path, "4:memory:/process_api/abc/claude-code-bash\n0::/\n", {
        "memory/memory.limit_in_bytes": "9223372036854771712",
        "memory/process_api/memory.limit_in_bytes": "9223372036854771712",
        f"{own}/memory.limit_in_bytes": "14338371584",
        f"{own}/memory.stat": "cache 100\nrss 2147483648\ntotal_rss 3221225472\n"})
    monkeypatch.setattr(B, "CGROUP_ROOT", root)
    monkeypatch.setattr(B, "PROC_CGROUP", proc)
    assert B.cgroup_memory() == (14338371584.0, 3221225472.0)


def test_memory_limit_of_a_parent_cgroup_v2(tmp_path, monkeypatch):
    root, proc = _cgroup_tree(tmp_path, "0::/user.slice/app/run\n", {
        "memory.max": "max",
        "user.slice/memory.max": "8589934592",
        "user.slice/memory.stat": "anon 1073741824\nfile 5\n",
        "user.slice/app/memory.max": "max",
        "user.slice/app/run/memory.max": "max"})
    monkeypatch.setattr(B, "CGROUP_ROOT", root)
    monkeypatch.setattr(B, "PROC_CGROUP", proc)
    assert B.cgroup_memory() == (8589934592.0, 1073741824.0)


def test_no_cgroup_limit(tmp_path, monkeypatch):
    root, proc = _cgroup_tree(tmp_path, "0::/\n", {"memory.max": "max"})
    monkeypatch.setattr(B, "CGROUP_ROOT", root)
    monkeypatch.setattr(B, "PROC_CGROUP", proc)
    assert B.cgroup_memory() == (0.0, 0.0)


def test_guard_stops_before_a_pass_that_does_not_fit(monkeypatch, capfd):
    monkeypatch.setattr(B, "memory_forecast", lambda n, c: (n * 1000.0, 30 * 2 ** 30))
    assert not B.next_pass_too_large(20000, 30, sweep=True)
    monkeypatch.setattr(B, "memory_forecast", lambda n, c: (28 * 2 ** 30, 30 * 2 ** 30))
    assert not B.next_pass_too_large(20000, 30, sweep=False)            # 28 of 30 GB fits without a sweep
    assert B.next_pass_too_large(20000, 30, sweep=True)                  # x1.15 for the sweep does not
    assert "Stopping refinement: the next pass and the frequency sweep on its mesh would need about 32.2 GB" \
        in capfd.readouterr().out


def test_guard_leaves_room_for_the_solutions_of_a_sweep_with_many_ports(monkeypatch, capfd):
    """Rotman lens array, 11 ports, Fast sweep with 17 points, second order (about 8.8 unknowns per
    tetrahedron): pass 4 (48k tetrahedra) peaked at 8.5 GB on a 15.3 GB cloud, the guard planned pass 5 plus
    1.25 x for the sweep and let it run, the sweep on 48k then outgrew the memory. Now the solutions the sweep
    keeps are planned on top of 1.15 x the pass; a sweep with one port and few points still fits."""
    from hertzon.solver import fast_assembly
    monkeypatch.setitem(fast_assembly.FACTOR, "unknowns", 425502)
    monkeypatch.setattr(B, "memory_forecast", lambda n, c: (11.0 * 2 ** 30, 15.3 * 2 ** 30))
    assert not B.next_pass_too_large(48244, 30, sweep=True)                                  # 12.65 GB fits
    assert not B.next_pass_too_large(48244, 30, sweep=True, n_exc=1, points=11, fast=True)   # + 1.05 GB
    assert B.next_pass_too_large(48244, 30, sweep=True, n_exc=11, points=17, fast=True)
    assert "would need about 19.3 GB of memory, 15.3 GB available" in capfd.readouterr().out
    extra = B.sweep_memory(62717, 48244, 11, 17, True)
    assert 6 * 2 ** 30 < extra < 7.5 * 2 ** 30
    assert B.sweep_memory(62717, 48244, 11, 0, False) == 0.0                # interpolating sweep: x1.15 only


def test_memory_exponent_is_learned_from_the_last_two_passes(monkeypatch):
    """Hairpin filter, passes 7 and 8 (0.4.8, cloud): 7.78 GB at 38505 and 10.22 GB at 50645 tetrahedra grow
    about linearly; with 4/3 the forecast for the next pass was about 10 % too high per pass and stopped the
    filter a pass early under the real memory limit."""
    monkeypatch.setattr(B, "PASS_PEAKS", [(29226, 5.89e9), (38505, 7.78e9)])
    e = B.memory_exponent(38505)
    assert 1.0 < e < 1.1
    monkeypatch.setattr(B, "_peak_rss", lambda: 7.78e9)
    need, _ = B.memory_forecast(38505 * 1.3, 38505)
    assert need < 7.78e9 * 1.3 ** 1.1                       # actual pass 8: 10.22 GB; 4/3 forecast 11.0 GB
    monkeypatch.setattr(B, "PASS_PEAKS", [(38505, 7.78e9)])
    assert B.memory_exponent(38505) == pytest.approx(4 / 3)       # one pass: nested dissection
    monkeypatch.setattr(B, "PASS_PEAKS", [(29226, 7.78e9), (38505, 7.78e9)])
    assert B.memory_exponent(38505) == pytest.approx(4 / 3)       # peak from an earlier, larger model


def test_plane_wave_passes_stop_when_memory_runs_short(tmp_path, monkeypatch, capfd):
    """The RCS passes (incident plane wave) have the memory guard of the port driven passes: the refinement
    stops on the last mesh that fits, and that pass is the solution (drone RCS on 15 GB: ended by the
    operating system in pass 2 before)."""
    pytest.importorskip("emerge")
    import json
    from hertzon.model.design import Project
    from hertzon.solver import suite
    p = Project.load(str(suite.SUITE_DIR / "sphere_rcs.hzn"))
    tid, _t, _f, design, setup, _w = next(t for t in suite.TESTS if t[0] == "sphere_rcs")
    d = p.design(design)
    st = d.setup(setup)
    st.props.update(MaximumPasses="4", SaveFields="False")
    first = {}

    def forecast(n, c):            # the first pass fits, a refined one does not
        first.setdefault("tets", c)
        return (40 * 2 ** 30 if n > first["tets"] else 2 ** 30), 30 * 2 ** 30
    monkeypatch.setattr(B, "memory_forecast", forecast)
    B.Bridge(p, d, st, tmp_path).run()
    conv = json.loads((tmp_path / "convergence.json").read_text())
    assert len(conv["passes"]) == 1 and not conv["converged"]
    assert "Stopping refinement: the next pass would need about" in capfd.readouterr().out
    assert (tmp_path / f"farfield_{d.spheres[0].name}_LastAdaptive.npz").exists()
