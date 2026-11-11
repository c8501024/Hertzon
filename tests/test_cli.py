"""Headless command line (hertzon/cli.py): describe, solve to Touchstone, reuse of solutions, exit codes."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from hertzon import cli
from hertzon.model.design import Project
from hertzon.model import touchstone
from hertzon.solver.parametric import _setup_keys
from hertzon.solver.results import ResultStore, results_dir

EX = Path(__file__).resolve().parents[1] / "examples"


def _copy(tmp_path, name):
    dst = tmp_path / name
    shutil.copy(EX / name, dst)
    return dst


def test_version(capsys):
    from hertzon import __version__
    assert cli.main(["version", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hertzon"] == __version__ and out["cli_schema"] == cli.CLI_SCHEMA


def test_describe_3d_and_circuit(capsys):
    assert cli.main(["describe", str(EX / "Microstrip.hzn"), "--design", "Microstrip50"]) == 0
    d = json.loads(capsys.readouterr().out)
    des = d["designs"][0]
    assert d["file_format"] == {"format": "hertzon-project", "version": 2}
    assert des["kind"] == "3d"
    assert [(p["number"], p["kind"], p["z0_ohm"]) for p in des["ports"]] == [(1, "Lumped Port", 50.0),
                                                                          (2, "Lumped Port", 50.0)]
    w = next(v for v in des["variables"] if v["name"] == "w")
    assert w["value"] == pytest.approx(3e-3) and w["unit"] == "m"
    sw = des["setups"][0]["sweeps"][0]
    assert sw["start_hz"] == pytest.approx(1e9) and sw["points"] == 101
    assert cli.main(["describe", str(EX / "StubFilter.hzn")]) == 0
    kinds = {x["name"]: x["kind"] for x in json.loads(capsys.readouterr().out)["designs"]}
    assert kinds["StubFilter"] == "circuit"


def test_solve_circuit_to_touchstone(tmp_path, capsys):
    out = tmp_path / "f.s2p"
    code = cli.main(["solve", str(EX / "StubFilter.hzn"), "--design", "StubFilter", "--freq", "1e9:2GHz:11",
                     "-o", str(out), "--progress", "none", "--json"])
    assert code == 0
    s = json.loads(capsys.readouterr().out)
    assert s["touchstone"] == str(out.resolve()) and s["frequencies"]["count"] == 11
    f, S, z0 = touchstone.read(out)
    assert len(f) == 11 and f[0] == pytest.approx(1e9) and f[-1] == pytest.approx(2e9)
    assert S.shape == (11, 2, 2) and z0 == 50


def test_input_errors_have_exit_code_3(tmp_path, capsys):
    ms = str(EX / "Microstrip.hzn")
    assert cli.main(["solve", str(tmp_path / "missing.hzn")]) == cli.BAD_INPUT
    assert cli.main(["solve", ms]) == cli.BAD_INPUT                           # two designs, none chosen
    assert cli.main(["solve", ms, "--design", "Microstrip50", "--set", "nope=1mm"]) == cli.BAD_INPUT
    assert cli.main(["solve", ms, "--design", "Microstrip50", "--setup", "X"]) == cli.BAD_INPUT
    assert cli.main(["solve", ms, "--design", "Microstrip50", "--freq", "5e9:1e9:3"]) == cli.BAD_INPUT
    assert cli.main(["solve", ms, "--unknown-flag"]) == cli.USAGE
    capsys.readouterr()
    assert cli.main(["solve", ms, "--design", "Nope", "--progress", "json"]) == cli.BAD_INPUT
    ev = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert ev["event"] == "error" and ev["code"] == cli.BAD_INPUT


def _fake_result(folder: Path, keys: dict, solution: str, freq, value: float) -> None:
    st = ResultStore(folder)
    S = np.full((len(freq), 2, 2), value, complex)
    ports = [{"number": 1, "name": "1", "kind": "Lumped Port", "modes": 1},
             {"number": 2, "name": "2", "kind": "Lumped Port", "modes": 1}]
    st.save_sparams(solution, freq, S, np.array([50.0, 50.0]), ports)
    st.write_json("convergence.json", {"converged": True, "passes": [{}, {}], "final_tets": 1234})
    st.write_json("status.json", {"state": "done", **keys})


def test_solution_of_the_window_is_reused(tmp_path, capsys, monkeypatch):
    path = _copy(tmp_path, "Microstrip.hzn")
    p = Project.load(path)
    keys = _setup_keys(p, "Microstrip50", "Setup1")
    _fake_result(results_dir(path, "Microstrip50", "Setup1"), keys, "Sweep", np.linspace(1e9, 6e9, 5), 0.25)
    monkeypatch.setattr(cli, "run_solver", lambda *a, **k: pytest.fail("must not solve"))
    out = tmp_path / "o"
    out.mkdir()
    assert cli.main(["solve", str(path), "--design", "Microstrip50", "-o", str(out), "--json",
                     "--progress", "none"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["cached"] and s["converged"] is True and s["tetrahedra"] == 1234
    f, S, _ = touchstone.read(out / "Microstrip50_Setup1_Sweep.s2p")
    assert len(f) == 5 and np.allclose(S, 0.25)
    assert json.loads(path.read_text())["designs"][0]["variables"]["w"] == "3mm"   # project unchanged


def test_set_and_freq_solve_a_copy_and_reuse_it(tmp_path, capsys, monkeypatch):
    path = _copy(tmp_path, "Microstrip.hzn")
    calls = []

    def fake_solver(project_file, design, setup, out, cores, rep):
        calls.append(project_file)
        cp = Project.load(project_file)
        d = cp.design(design)
        assert d.variables["w"] == "2.5mm"
        sw = d.setup(setup).sweeps[0]
        assert sw.props["Distribution"] == "Linear Count" and sw.props["Count"] == "7"
        assert all(s.props["SaveFields"] == "False" for s in d.setup(setup).sweeps)
        _fake_result(out, _setup_keys(cp, design, setup), sw.name, np.linspace(2e9, 3e9, 7), 0.5)
        return {"code": 0, "error": "", "done": True, "converged": True}

    monkeypatch.setattr(cli, "run_solver", fake_solver)
    args = ["solve", str(path), "--design", "Microstrip50", "--set", "w=2.5mm", "--freq", "2GHz:3GHz:7",
            "-o", str(tmp_path / "a.s2p"), "--json", "--progress", "none"]
    assert cli.main(args) == 0
    s = json.loads(capsys.readouterr().out)
    assert not s["cached"] and s["variables"] == {"w": "2.5mm"} and len(calls) == 1
    assert Path(s["results"]).is_relative_to(results_dir(path, "Microstrip50", "CLI"))
    assert cli.main(args) == 0                     # same call again: answered from the copy's solution
    assert json.loads(capsys.readouterr().out)["cached"] and len(calls) == 1
    assert cli.main(args + ["--force"]) == 0
    capsys.readouterr()
    assert len(calls) == 2
    assert json.loads(path.read_text())["designs"][0]["variables"]["w"] == "3mm"


def test_solver_failure_has_exit_code_1(tmp_path, capsys, monkeypatch):
    path = _copy(tmp_path, "Microstrip.hzn")
    monkeypatch.setattr(cli, "run_solver", lambda *a, **k: {"code": 1, "error": "mesh failed", "done": False,
                                                            "converged": None})
    assert cli.main(["solve", str(path), "--design", "Microstrip50", "--force"]) == cli.SOLVER_FAILED
    assert "mesh failed" in capsys.readouterr().err


def test_python_m_hertzon_dispatches_to_the_cli():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "hertzon", "version"], capture_output=True, text=True,
                       cwd=str(EX.parent))
    assert r.returncode == 0 and r.stdout.startswith("Hertzon ")
