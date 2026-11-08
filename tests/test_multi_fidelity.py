"""Multi-fidelity optimization of a circuit: its generated 3D layout is the fine model, the circuit the coarse one
of a space mapping (solver/optimize.py Optimizer._to_layout)."""
import json

import numpy as np
import pytest

from hertzon.model import circuit_layout as CL
from hertzon.model import schema
from hertzon.model.circuit import TYPES
from hertzon.model.circuit import Component as C
from hertzon.model.design import Design, Optimization, Project, circuit_setup


def _project(tmp_path, multi=True, layout=True):
    """A microstrip quarter-wave transformer from 50 to 100 ohm with its width and length as variables, its 3D
    layout and a multi-fidelity optimization on the circuit."""
    p = Project()
    d = Design(name="Match", kind="circuit")
    d.variables = {"wt": "0.8mm", "lt": "22mm", "unused": "3mm"}
    d.components = [C("Sub1", "SUBST", -200, 200, props=dict(TYPES["SUBST"].props)),
                    C("Port1", "PORT", 0, 40, 0, {"Z0": "50ohm"}),
                    C("MS1", "MLIN", 40, 0, 0, {"Subst": "Sub1", **TYPES["MLIN"].props, "W": "wt", "L": "lt"}),
                    C("Port2", "PORT", 80, 40, 0, {"Z0": "100ohm"})]
    st = circuit_setup()
    st.sweeps[0].props.update(Start="1.8GHz", Stop="2.2GHz", Count="21")
    d.setups.append(st)
    d.optimetrics.append(Optimization(
        "Opt1", "Setup1 : Sweep",
        [{"name": "wt", "include": True, "min": "0.3mm", "max": "1.5mm"},
         {"name": "lt", "include": True, "min": "18mm", "max": "28mm"}],
        [{"calc": "dB(S(1,1))", "start": "1.95GHz", "stop": "2.05GHz", "cond": "<=", "value": "-35", "weight": "1"}],
        {**schema.defaults(schema.OPTIMIZATION), "MultiFidelity": str(multi), "MappingRounds": "6"}))
    p.designs.append(d)
    if layout:
        L, _ = CL.layout_design(p, d)
        p.designs.append(L)
    path = tmp_path / "match.hzn"
    p.save(path)
    return p, path


def test_the_generated_layout_uses_the_circuit_variables(tmp_path):
    p, _ = _project(tmp_path)
    circ, lay = p.design("Match"), p.design("Match_Layout")
    assert CL.layouts_of(p, circ) == [lay]
    assert lay.variables["MS1_L"] == "lt" and lay.variables["MS1_W"] == "wt" and lay.variables["lt"] == "22mm"
    assert CL.layout_differences(p, lay) == []
    circ.components[2].props["L"] = "25mm"                  # the schematic edited after Create 3D Layout
    diff = CL.layout_differences(p, lay)
    assert any("MS1.L" in w for w in diff) and any("Update 3D Layout" in w for w in diff)


def test_the_optimizer_solves_the_layout_with_the_circuit_as_coarse_model(tmp_path):
    from hertzon.solver.optimize import Optimizer
    p, path = _project(tmp_path)
    opt = Optimizer(str(path), "Match", "Opt1", tmp_path / "opt")
    assert opt.multi and opt.mapping and not opt.circuit
    assert opt.des.name == "Match_Layout" and opt.coarse_name == "Match"
    assert (opt.setup, opt.sol) == ("Setup1", "Sweep")
    assert opt.layout_sync == {"unused": "3mm"}              # the other circuit variables go into the 3D solves
    proj = opt._project_for({"wt": "0.7mm", "lt": "21mm"})
    v = proj.design("Match_Layout").variables
    assert v["wt"] == "0.7mm" and v["lt"] == "21mm" and v["unused"] == "3mm"


def test_multi_fidelity_needs_a_layout(tmp_path):
    from hertzon.solver.optimize import Optimizer
    _, path = _project(tmp_path, layout=False)
    with pytest.raises(ValueError, match="Create 3D"):
        Optimizer(str(path), "Match", "Opt1", tmp_path / "opt")


def test_space_mapping_on_a_circuit_without_multi_fidelity_is_refused(tmp_path):
    from hertzon.solver.optimize import Optimizer
    p, path = _project(tmp_path, multi=False)
    p.design("Match").optimetrics[0].props["Optimizer"] = "Space Mapping"
    p.save(path)
    with pytest.raises(ValueError, match="Multi-fidelity"):
        Optimizer(str(path), "Match", "Opt1", tmp_path / "opt")


def test_multi_fidelity_corrects_a_shifted_3d_model(tmp_path, monkeypatch):
    """The 3D solve is stood in for by the circuit with the line 1.5 mm longer and 0.15 mm wider than the
    variables say (a model error of the circuit). The rounds find the values whose 3D response is the circuit
    optimum, and fit all S-parameters of the two ports."""
    from hertzon.solver import optimize as O
    from hertzon.solver.circuit import analyze
    from hertzon.solver.optimetrics import evaluate_goals
    from hertzon.solver.results import ResultStore
    from hertzon.solver.sweeps import sweep_frequencies
    p, path = _project(tmp_path)
    out = tmp_path / "opt"
    out.mkdir()
    opt = O.Optimizer(str(path), "Match", "Opt1", out)
    circ = p.design("Match")
    freqs = sweep_frequencies(p.design("Match_Layout").setups[0].sweeps[0].props, opt.ev)
    solved = []

    def fake_solve(self, points, phase, iteration):
        costs = []
        for x in points:
            self.n += 1
            texts = self.vars.texts(x)
            v = dict(zip(self.vars.names, self.vars.values(x)))
            circ.variables.update(lt=f"{v['lt'] + 1.5:.6g}mm", wt=f"{v['wt'] + 0.15:.6g}mm")
            r = analyze(p, circ, freqs)
            folder = self.out / "evals" / f"{self.n:04d}"
            ports = [{"name": n, "number": i + 1, "kind": "Lumped Port", "modes": 1} for i, n in enumerate(r.ports)]
            ResultStore(folder / "results").save_sparams(self.sol, r.freq, r.S, r.z0, ports)
            self.folders[self.n] = folder
            cost, _ = evaluate_goals(self.opt, {"freq": r.freq, "S": r.S, "z0": r.z0, "ports": ports}, self.ev)
            self.history.append({"eval": self.n, "variables": texts, "cost": cost})
            solved.append((v, cost))
            costs.append(cost)
        return costs
    monkeypatch.setattr(O.Optimizer, "_solve_batch", fake_solve)
    monkeypatch.setattr(O.Optimizer, "discard", lambda self, n: None)
    assert opt.run()
    summary = json.loads((out / "summary.json").read_text())
    assert "circuit optimum" in summary["reason"] or "cost 0" in summary["reason"], summary["reason"]
    assert len(solved) <= 6
    first, last = solved[0], solved[-1]
    assert last[1] < first[1]
    assert abs((first[0]["lt"] - last[0]["lt"]) - 1.5) < 0.2
    assert abs((first[0]["wt"] - last[0]["wt"]) - 0.15) < 0.05


def test_mapping_fits_every_s_parameter_of_a_reciprocal_network():
    from hertzon.solver.optimize import mapping_pairs
    assert mapping_pairs(1) == [(0, 0)]
    assert mapping_pairs(2) == [(0, 0), (1, 0), (1, 1)]
    assert len(mapping_pairs(4)) == 10


def test_the_dialog_shows_multi_fidelity_for_circuits_only():
    circuit = {p.name: p for p in schema.optimization_props(True)}
    three_d = {p.name: p for p in schema.optimization_props(False)}
    assert "MultiFidelity" in circuit and "MultiFidelity" not in three_d
    assert "Space Mapping" not in circuit["Optimizer"].choices and "CoarseModel" not in circuit
    assert circuit["MappingRounds"].visible_if == ("MultiFidelity", "True")
    assert three_d["MappingRounds"].visible_if == ("Optimizer", "Space Mapping")


def test_apply_to_design_sets_the_circuit_and_its_layout(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.main_window import MainWindow
    from hertzon.gui.solve import optimization_dir
    _, path = _project(tmp_path)
    out = optimization_dir(str(path), "Match", "Opt1")
    out.mkdir(parents=True, exist_ok=True)
    (out / "history.json").write_text(json.dumps({"variables": ["wt", "lt"], "evaluations": [
        {"eval": 1, "variables": {"wt": "0.9mm", "lt": "24mm"}, "cost": 2.0},
        {"eval": 2, "variables": {"wt": "0.6mm", "lt": "23mm"}, "cost": 0.1}]}))
    w = MainWindow()
    w.file_open(str(path))
    w.doc.set_design("Match")
    w.apply_best_variables("Opt1")
    lay = w.doc.project.design("Match_Layout")
    assert w.des.variables["wt"] == "0.6mm" and w.des.variables["lt"] == "23mm"
    assert lay.variables["wt"] == "0.6mm" and lay.variables["lt"] == "23mm"
