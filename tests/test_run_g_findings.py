"""Findings of the meta benchmark run G: the phase of a running solve in get_status, an error for misspelt
property names in run_python and zero loss matrices of a lossless model."""
import json
import time

import pytest

from hertzon.mcp import tools as T
from hertzon.mcp.protocol import ToolError
from hertzon.model.design import Project
from hertzon.solver import bridge as B


def test_progress_file_names_the_phase_and_when_it_started(tmp_path, monkeypatch, capsys):
    p = tmp_path / "progress.json"
    monkeypatch.setitem(B._PROGRESS, "file", p)
    monkeypatch.setitem(B._PROGRESS, "phase", None)
    B.emit("progress", value=8, text="Mapping boundaries to the solver model")
    d = json.loads(p.read_text())
    assert d["phase"] == "Mapping boundaries to the solver model"
    t0 = d["phase_started"]
    time.sleep(0.01)
    B.emit("progress", value=97, text="Computing field data (LastAdaptive): 50%")
    d = json.loads(p.read_text())
    assert d["phase"].startswith("Computing field data") and d["phase_started"] > t0
    capsys.readouterr()


def test_unknown_property_names_are_found():
    p = Project()
    d = p.designs[0]
    d.add_object("CreateCylinder", CenterPosition="1,2,3", Axis="Z", Radius="1", Height="2")
    assert T._unknown_props(d) == set()
    d.add_object("CreateCylinder", Center="1,2,3", Axis="Z", Radius="1", Height="2")      # misspelt
    assert T._unknown_props(d) == {("Cylinder2", "CreateCylinder", "Center")}


def test_run_python_refuses_a_misspelt_property(tmp_path, monkeypatch):
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))      # no live Hertzon window
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    p = Project()
    path = tmp_path / "p.hzn"
    p.save(path)
    monkeypatch.setattr(T, "commit", lambda *a, **k: pytest.fail("must not write"))
    with pytest.raises(ToolError, match="Center"):
        T.t_run_python({"project": str(path), "code": "add_object('CreateCylinder', Center='1,2,3', Axis='Z', "
                                                      "Radius='1', Height='2')"})
