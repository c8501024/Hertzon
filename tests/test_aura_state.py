"""hzAURA: the adaptive mesh is stored with the refinement state and rebuilt from it (analyze one sweep,
continue an interrupted run)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

em = pytest.importorskip("emerge")
pytest.importorskip("emerge._emerge.aura.emerge_tools")

from hertzon.solver import bridge as B  # noqa: E402


def _model():
    m = em.Simulation("aura_state", save_file=False)
    em.geo.Box(0.02, 0.01, 0.03)
    m.mw.set_frequency(5e9)
    m.commit_geometry()
    m.generate_mesh()
    return m


def _refine(m, step):
    rng = np.random.default_rng(step)
    assert B._refine_aura(m, rng.random(m.mesh.n_tets), 40.0, step) is not None


def test_aura_mesh_is_stored_and_restored(tmp_path):
    m = _model()
    m._local_mesh = None
    m._aura_mesh = None
    m._aura_initial = np.asarray(m.mesh.nodes).T.copy()
    n_init = m.mesh.n_tets
    for step in (1, 2):
        _refine(m, step)
    assert m.mesh.n_tets > n_init
    nodes, tets = np.asarray(m.mesh.nodes).copy(), np.asarray(m.mesh.tets).copy()
    ftags = {k: sorted(map(tuple, np.sort(m.mesh.tris[:, v].T, axis=1).tolist())) for k, v in m.mesh.ftag_to_tri.items()}
    B._save_amr_state(tmp_path, "k", B._amr(m), 0.6, [{"pass": 1}, {"pass": 2}], [1e9], m)

    resume = B.load_amr_state(tmp_path, "k", [1e9])
    assert resume is not None and resume["aura"] is not None
    m2 = _model()                                  # a new solver process: only the initial mesh
    assert m2.mesh.n_tets == n_init
    B.restore_amr_mesh(m2, resume, 1.6)
    assert np.allclose(m2.mesh.nodes, nodes) and np.array_equal(m2.mesh.tets, tets)
    assert {k: sorted(map(tuple, np.sort(m2.mesh.tris[:, v].T, axis=1).tolist()))
            for k, v in m2.mesh.ftag_to_tri.items()} == ftags
    assert m2._aura_mesh is m2.mesh


def test_aura_state_needs_the_same_initial_mesh(tmp_path):
    m = _model()
    m._local_mesh = None
    m._aura_initial = np.asarray(m.mesh.nodes).T.copy() + 1e-3   # as if the model had changed
    _refine(m, 1)
    B._save_amr_state(tmp_path, "k", B._amr(m), 0.6, [{"pass": 1}], [1e9], m)
    with pytest.raises(RuntimeError, match="does not fit the initial mesh"):
        B.restore_amr_mesh(_model(), B.load_amr_state(tmp_path, "k", [1e9]), 1.6)


def test_stale_aura_state_leaves_the_model_untouched(tmp_path):
    m = _model()
    m._local_mesh = None
    m._aura_initial = np.asarray(m.mesh.nodes).T.copy() + 1e-3
    _refine(m, 1)
    B._save_amr_state(tmp_path, "k", B._amr(m), 0.6, [{"pass": 1}], [1e9], m)
    m2 = _model()
    nodes = np.asarray(m2.mesh.nodes).copy()
    with pytest.raises(B.StaleMesh):
        B.restore_amr_mesh(m2, B.load_amr_state(tmp_path, "k", [1e9]), 1.6)
    assert np.array_equal(np.asarray(m2.mesh.nodes), nodes)       # the adaptive passes can start from it


def test_a_sweep_that_enlarges_the_open_region_outdates_the_solution():
    """Switching on or lowering a sweep below the solution frequency enlarges the automatic open region: the
    model key changes (the setup is shown outdated and adapts again); switching it off again restores it."""
    import copy
    from hertzon.model.design import Project
    from hertzon.solver.keys import model_key
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "PatchAntenna.hzn")
    d = p.designs[0]
    st = d.setups[0]
    ev = d.evaluator(p)
    k0, f0 = model_key(p, d, st), B.lowest_frequency(d, st, ev)
    wide = copy.deepcopy(st.sweeps[0])
    wide.name = "Wide"
    wide.props.update(Start="0.8GHz", Stop="4GHz", Step="0.1GHz", Enabled="False")
    st.sweeps.append(wide)
    assert model_key(p, d, st) == k0 and B.lowest_frequency(d, st, ev) == f0
    wide.props["Enabled"] = "True"
    k1 = model_key(p, d, st)
    assert k1 != k0 and B.lowest_frequency(d, st, ev) == pytest.approx(0.8e9)
    wide.props["Start"] = "2GHz"                 # above the other sweep: the region is the one of before
    assert model_key(p, d, st) == k0
    d.settings["AutoOpenPadding"] = "50mm"       # a given padding: the sweeps do not size the region
    k2 = model_key(p, d, st)
    wide.props["Start"] = "0.5GHz"
    assert model_key(p, d, st) == k2


def test_optimization_evaluations_keep_the_sweeps_of_the_design(tmp_path):
    """The evaluations keep the sweeps switched on or off as in the design (same open region, so Apply to
    Design takes the adaptive mesh over) and solve only the goal sweep."""
    import copy
    from hertzon.model.design import Optimization, Project
    from hertzon.solver import optimize as O
    from hertzon.solver.keys import model_key
    p = Project.load(Path(__file__).resolve().parents[1] / "examples" / "PatchAntenna.hzn")
    d = p.designs[0]
    st = d.setups[0]
    wide = copy.deepcopy(st.sweeps[0])
    wide.name = "Wide"
    wide.props.update(Start="0.8GHz", Stop="4GHz", Step="0.1GHz")
    st.sweeps.append(wide)
    d.optimetrics.append(Optimization("TR", "Setup1 : Sweep",
                                      variables=[{"name": "Lp", "include": True, "min": "26mm", "max": "31mm"}],
                                      goals=[], props={"Optimizer": "Derivative Trust Region"}))
    p.save(tmp_path / "p.hzn")
    opt = O.Optimizer(str(tmp_path / "p.hzn"), d.name, "TR", tmp_path / "out")
    q = opt._project_for({"Lp": "28.5mm"})
    qd = q.design(d.name)
    assert [s.props.get("Enabled", "True") for s in qd.setup("Setup1").sweeps] == ["True", "True"]
    assert model_key(q, qd, qd.setup("Setup1")) == model_key(p, d, st)
    assert opt._env(1)["HERTZON_GOAL_SWEEP"] == "Sweep"
