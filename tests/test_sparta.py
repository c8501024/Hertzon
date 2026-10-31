import sys
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import sparta_setup  # noqa: E402


class _Solver:
    """Records what configure() sets, like EMerge's SolverSparta."""
    def set_options(self, mode):
        self.mode = mode
        return self

    def set_symmetry(self, sym):
        self.sym = sym


def test_choices_reach_the_setup_and_options():
    from hertzon.model import schema
    solver = next(p for p in schema.SETUP if p.name == "Solver")
    for name in sparta_setup.CHOICES:
        assert name in schema.SOLVERS and name in solver.choices and sparta_setup.is_sparta(name)
    assert not sparta_setup.is_sparta("PARDISO") and not sparta_setup.is_sparta("Auto")


def test_release_wheel_per_platform_and_source_elsewhere():
    v = sparta_setup.VERSION
    win = sparta_setup.requirement("win32", "AMD64")
    assert win.endswith(f"sparta_solver-{v}-cp39-abi3-win_amd64.whl") and f"/v{v}/" in win
    assert sparta_setup.requirement("linux", "x86_64").endswith("manylinux2014_x86_64.whl")
    assert sparta_setup.requirement("darwin", "arm64").endswith("macosx_11_0_arm64.whl")
    assert sparta_setup.requirement("win32", "ARM64") == sparta_setup.SOURCE
    args = sparta_setup.install_args("py")
    assert args[:4] == ["py", "-m", "pip", "install"] and args[-1] == sparta_setup.requirement()


def test_gpu_without_a_usable_gpu_factorizes_on_the_cpu(monkeypatch):
    fake = type(sys)("sparta")
    fake.gpu_available = lambda: False
    monkeypatch.setitem(sys.modules, "sparta", fake)
    s = _Solver()
    assert sparta_setup.configure(s, "GPU", symmetric=False) == "CPU"
    assert s.mode == "CPU" and s.sym is False
    fake.gpu_available = lambda: True
    assert sparta_setup.configure(s, "GPU") == "GPU" and s.mode == "GPU" and s.sym is True
    assert sparta_setup.configure(s, "CPU") == "CPU"


def _emerge_sparta():
    try:
        from emerge._emerge import solver as S
    except Exception:  # noqa: BLE001
        pytest.skip("EMerge not importable")
    if not S._SPARTA_AVAILABLE:
        pytest.skip("SPARTA not installed")
    return S


@pytest.mark.parametrize("symmetric", [True, False])
def test_emerge_sparta_cpu_solves_symmetric_and_periodic_matrices(symmetric):
    """The FEM matrix is complex symmetric (LDL^T); a periodic cell's is only structurally symmetric (LU)."""
    S = _emerge_sparta()
    m = 60
    T = sp.diags([-1.0, 4.0, -1.0], [-1, 0, 1], shape=(m, m))
    A = (sp.kron(sp.identity(m), T) + sp.kron(sp.diags([-1.0, -1.0], [-1, 1], shape=(m, m)), sp.identity(m)))
    A = (A + 0.3j * sp.identity(m * m)).tocsc().astype(complex)
    if not symmetric:
        U = sp.triu(A, 1).tocsc()
        A = (A + 0.2j * U - 0.2j * U.T).tocsc()        # same pattern, A != A^T
        assert abs(A - A.T).max() > 0.1
    b = np.ones((m * m, 1), complex)
    solver = S.SolverSparta("")
    assert sparta_setup.configure(solver, "CPU", symmetric=symmetric) == "CPU"
    solver.initialize()
    x, report = solver.solve(A, b, None)
    solver.reset()
    assert report.exit_code == 0
    assert np.linalg.norm(A @ x - b) / np.linalg.norm(b) < 1e-9


def test_sparta_uses_mkl_when_this_environment_has_it(monkeypatch, tmp_path):
    """OpenBLAS crashes SPARTA's CPU solver with many threads; MKL (PARDISO's library) does not."""
    if sys.platform == "darwin":
        pytest.skip("SPARTA links Accelerate on Apple")
    lib = tmp_path / ("Library/bin/mkl_rt.2.dll" if sys.platform == "win32" else "lib/libmkl_rt.so.2")
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.delenv("SPARTA_BLAS", raising=False)
    sparta_setup.prefer_mkl()
    import os
    assert os.environ["SPARTA_BLAS"] == str(lib)
    monkeypatch.setenv("SPARTA_BLAS", "mine")
    sparta_setup.prefer_mkl()
    assert os.environ["SPARTA_BLAS"] == "mine"


def test_sweeps_with_sparta_keep_the_solver_between_batches(monkeypatch):
    """An interpolating sweep solves in batches; with SPARTA each batch would repeat its symbolic analysis."""
    from types import SimpleNamespace as NS
    from hertzon.solver import bridge
    calls = []
    entry = NS(freq=1e9, Sp=np.zeros((1, 1)), Z0=None, beta=None, _portnumbers=[1])

    class Scalar:
        n = 0

        def __getitem__(self, i):
            return entry

    data = NS(scalar=Scalar())

    def run_sweep(**kw):
        calls.append(kw)
        data.scalar.n = 1
        return data

    m = NS(mw=NS(data=NS(scalar=Scalar()), set_frequency=lambda f: None, run_sweep=run_sweep,
                 bc=NS(oftype=lambda t: [])))
    monkeypatch.setattr(bridge, "KEEP_SOLVER", False)
    bridge._solve_at(m, [1e9])
    monkeypatch.setattr(bridge, "KEEP_SOLVER", True)
    data.scalar.n = 0
    bridge._solve_at(m, [1e9])
    assert "_reset_solvers" not in calls[0] and calls[1]["_reset_solvers"] is False


def test_pardiso_passes_and_sparta_sweeps_is_a_setup_choice():
    from hertzon.model import schema
    for name, sweep in sparta_setup.SWEEP_ONLY.items():
        assert name in schema.SOLVERS and "," not in name      # the benchmark's --solvers splits at commas
        assert sparta_setup.is_sparta(sweep) and not sparta_setup.is_sparta(name)


def test_solver_comparison_marks_runs_without_their_solver_and_names_the_fastest(tmp_path):
    import json
    from hertzon.gui.benchmark import solver_recommendation, solver_runs
    from hertzon.solver.benchmark import solver_notes
    gpu = "Solver SPARTA (GPU): no GPU usable by SPARTA in this process; factorizing on the CPU"
    (tmp_path / "solver.log").write_text(
        "plain line\n@@HZN " + json.dumps({"type": "log", "level": "warning", "msg": gpu}) + "\n"
        + "@@HZN " + json.dumps({"type": "log", "level": "info", "msg": "Solver start: x"}) + "\n", encoding="utf-8")
    assert solver_notes(tmp_path) == [gpu]

    def run(sv, cores, total, notes=()):
        return {"kind": "cores", "solver": sv, "cores": cores, "total": total, "sweep": total / 2,
                "notes": list(notes)}
    res = {"runs": [run("PARDISO", 4, 90.0), run("PARDISO", 16, 80.0), run("SPARTA (CPU)", 16, 60.0),
                    run("SPARTA (GPU)", 16, 70.0, [gpu]),
                    {"kind": "same_mesh", "solver": "SPARTA (GPU)", "sweep": 30.0, "ds_max": {"S": 2e-3}}]}
    top, same, par = solver_runs(res)
    assert [r["solver"] for r in top] == ["PARDISO", "SPARTA (CPU)", "SPARTA (GPU)"] and list(same) == ["SPARTA (GPU)"]
    assert par == []
    text, best = solver_recommendation(res)
    assert best == "SPARTA (CPU)" and "25 % faster than PARDISO" in text
    assert "single precision" in text and "Note column" in text


def test_solver_comparison_with_parallel_frequencies_on_one_mesh(tmp_path, monkeypatch):
    """--solvers with --workers: every solver solves the sweeps on the reference mesh in a fresh process, one
    frequency at a time and with each number of parallel frequencies; the direct solutions of the
    interpolating sweep, the GPU start and a sweep that stayed sequential are recorded with them."""
    import json
    import numpy as np
    from hertzon.gui.benchmark import note_text, same_mesh_sweeps, solver_recommendation, solves_text
    from hertzon.solver import benchmark as B, bridge
    from hertzon.solver.results import ResultStore
    monkeypatch.setattr(bridge, "_first_run", lambda: False)
    calls = []

    def fake_solve(self, path, folder, env, label, k, n):
        from hertzon.model.design import Project
        sv = env.get("HERTZON_SOLVER") or \
            Project.load(path).design(B.CASES[0][2]).setup(B.CASES[0][3]).props.get("Solver", "")
        w = int(env["HERTZON_SWEEP_WORKERS"])
        calls.append((sv, w, env["HERTZON_ONLY_SWEEP"], Path(folder).name))
        gpu = "GPU" in sv
        direct = 30 if gpu else 12
        sweep = (27.0 if gpu else 14.0) / (1.5 if w > 1 and not sv.startswith("SPARTA") else 1)
        msgs = [{"type": "log", "level": "info", "msg": f"Sweep 'Sweep': {direct} direct solution(s), 40 from "
                                                        "the reduced basis of 30 vectors (worst residual 1e-5)"}]
        if gpu:
            msgs.append({"type": "log", "level": "info", "msg": "SPARTA: GPU started in 1.4 s"})
        if w > 1 and sv.startswith("SPARTA"):
            msgs.append({"type": "log", "level": "info",
                         "msg": "Sweep 'Sweep': SPARTA solves the frequencies one after another"})
        Path(folder, "solver.log").write_text("".join("@@HZN " + json.dumps(m) + "\n" for m in msgs))
        st = ResultStore(folder)
        st.write_json("profile.json", [{"task": "Adaptive meshing", "elapsed": 10.0, "total": 10.0},
                                       {"task": "Sweep Sweep", "elapsed": sweep, "total": 10 + sweep}])
        st.write_json("convergence.json", {"passes": [{"tets": 13000}], "final_tets": 13000, "converged": True})
        st.write_json("status.json", {"state": "done", "elapsed": 12 + sweep})
        f = np.linspace(1e9, 6e9, 5)
        S = np.full((5, 1, 1), 0.1 + (1e-4 if gpu else 0), complex)
        st.save_sparams("Sweep", f, S, np.array([50.0]), [], solved=f[:direct % 5 + 1])
        return True

    monkeypatch.setattr(B.Benchmark, "_solve", fake_solve)
    case = B.CASES_DIR / B.CASES[0][1]
    bm = B.Benchmark(str(case), B.CASES[0][2], B.CASES[0][3], tmp_path, [16], [1, 2],
                     ["PARDISO", "SPARTA (GPU)"])
    assert bm.run()
    res = json.loads((tmp_path / "benchmark.json").read_text())
    kinds = [(r["kind"], r.get("solver"), r.get("workers")) for r in res["runs"]]
    assert kinds == [("cores", "PARDISO", 1), ("cores", "SPARTA (GPU)", 1), ("same_mesh", "PARDISO", None),
                     ("same_mesh", "SPARTA (GPU)", None), ("parallel", "PARDISO", 2), ("parallel", "SPARTA (GPU)", 2)]
    # every sweep on the mesh of the first solver's run, the reference itself in a fresh process too
    assert all(c[3].endswith("_on_PARDISO_mesh") for c in calls[2:]) and all(c[2] == "Sweep" for c in calls[2:])
    same = {r["solver"]: r for r in res["runs"] if r["kind"] == "same_mesh"}
    assert same["SPARTA (GPU)"]["solves"] == {"direct": 30, "reduced": 40}
    assert same["SPARTA (GPU)"]["gpu_start"] == 1.4 and same["PARDISO"]["gpu_start"] == 0.0
    assert same["SPARTA (GPU)"]["ds_max"]["Sweep"] == pytest.approx(1e-4)
    par = {r["solver"]: r for r in res["runs"] if r["kind"] == "parallel"}
    assert par["PARDISO"]["sweep"] == pytest.approx(14 / 1.5) and not par["PARDISO"]["notes"]
    assert "one after another" in par["SPARTA (GPU)"]["notes"][0]
    assert solves_text(same["SPARTA (GPU)"]) == "30 + 40"
    assert "GPU start 1.4 s" in note_text(same["SPARTA (GPU)"])
    assert [(sv, w) for sv, w, _ in same_mesh_sweeps(res)] == [("PARDISO", 1), ("SPARTA (GPU)", 1), ("PARDISO", 2),
                                                               ("SPARTA (GPU)", 2)]
    text, _ = solver_recommendation(res)
    assert "Fastest sweep: <b>PARDISO</b> with <b>2 parallel frequencies</b>, 9 s (14 s one at a time)" in text
    assert "PARDISO 12, SPARTA (GPU) 30" in text and "Note column" in text
