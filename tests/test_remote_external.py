"""Files a project reads from elsewhere (imported CAD/STL, Touchstone, Verilog-A) travel with a remote job
(#584), and Windows paths stay absolute on a Linux or macOS server."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from hertzon.model.design import Project, is_absolute_path
from hertzon.remote.client import RemoteError, prepare
from hertzon.remote.protocol import EXTERNAL, unpack


def _import(path, suppressed=False):
    return {"kind": "Import", "props": {"File": str(path), "Body": "0"}, "suppressed": suppressed}


def _project(tmp_path, designs) -> tuple[Path, Path]:
    proj = tmp_path / "local" / "Job.hzn"
    proj.parent.mkdir(parents=True, exist_ok=True)
    proj.write_text(json.dumps({"format": "hertzon-project", "version": 2, "name": "Job", "designs": designs}),
                    encoding="utf-8")
    out = proj.with_suffix(".hznresults") / "A" / "Setup1"
    out.mkdir(parents=True)
    return proj, out


def test_external_files_are_packed_and_found_on_the_server(tmp_path):
    cad = tmp_path / "elsewhere" / "connector.step"          # absolute path outside the project folder
    cad.parent.mkdir()
    cad.write_text("STEP data")
    (tmp_path / "local" / "models").mkdir(parents=True)
    stl = tmp_path / "local" / "models" / "skin.stl"         # relative to the project, as in the examples
    stl.write_text("STL data")
    s2p = tmp_path / "elsewhere" / "part.s2p"
    s2p.write_text("! touchstone")
    va = tmp_path / "elsewhere" / "fet.va"
    va.write_text("// verilog-a")
    proj, out = _project(tmp_path, [
        {"name": "A", "objects": [
            {"name": "Conn", "history": [_import(cad)]},
            {"name": "Skin", "history": [_import("models/skin.stl")]},
            {"name": "Again", "history": [_import(cad)]},                      # the same file once more
            {"name": "Old", "history": [_import(tmp_path / "gone.step", suppressed=True)]}],
         "boundaries": [{"name": "R1", "kind": "Lumped RLC", "props": {"Model": "Touchstone", "TouchstoneFile": str(s2p)}}]},
        {"name": "B", "objects": [{"name": "X", "history": [_import(tmp_path / "missing.step")]}]},
        {"name": "C", "kind": "circuit", "components": [
            {"type": "SNP", "name": "S1", "props": {"File": str(s2p)}},
            {"type": "DEVICE", "name": "Q1", "props": {"File": f"{va}; {s2p}"}}]},
    ])
    archive, rel = prepare(proj, out, design="A")
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    externals = sorted(n for n in names if n.startswith(EXTERNAL + "/"))
    assert len(externals) == 4                               # connector, skin, part, fet: each once
    dest = tmp_path / "server" / "job"
    unpack(archive, dest)
    p = Project.load(dest / proj.name)
    a, b, c = p.designs

    def read(f):
        return Path(f).read_text()

    assert read(a.objects[0].history[0].props["File"]) == "STEP data"
    assert read(a.objects[1].history[0].props["File"]) == "STL data"
    assert a.objects[0].history[0].props["File"] == a.objects[2].history[0].props["File"]
    assert read(a.boundaries[0].props["TouchstoneFile"]) == "! touchstone"
    assert read(c.components[0].props["File"]) == "! touchstone"
    files = c.components[1].props["File"].split(";")
    assert [read(f) for f in files] == ["// verilog-a", "! touchstone"]
    for f in [a.objects[0].history[0].props["File"], a.boundaries[0].props["TouchstoneFile"], *files]:
        assert Path(f).resolve().is_relative_to(dest.resolve())
    # missing files the solve of A does not read are left as they were
    assert a.objects[3].history[0].props["File"].endswith("gone.step")
    assert b.objects[0].history[0].props["File"] == str(tmp_path / "missing.step")
    # the local project file is not changed
    assert json.loads(proj.read_text())["designs"][0]["objects"][0]["history"][0]["props"]["File"] == str(cad)
    assert rel == "Job.hznresults/A/Setup1"


def test_a_missing_file_of_the_solved_design_stops_before_upload(tmp_path):
    proj, out = _project(tmp_path, [
        {"name": "A", "objects": [{"name": "Conn", "history": [_import(tmp_path / "connector.step")]}]}])
    before = set((tmp_path).rglob("hertzon-job-*.zip"))
    with pytest.raises(RemoteError, match="connector.step"):
        prepare(proj, out, workdir=tmp_path, design="A")
    assert set(tmp_path.rglob("hertzon-job-*.zip")) == before        # no archive left behind


def test_windows_paths_stay_absolute_everywhere(tmp_path):
    for text in ("C:\\Users\\me\\models\\connector.step", "D:/cad/a.stp", "\\\\server\\share\\a.step"):
        assert is_absolute_path(text)
    assert not is_absolute_path("models/a.step")
    assert not is_absolute_path("a.step")
    proj, _ = _project(tmp_path, [
        {"name": "A", "objects": [{"name": "Conn", "history": [_import("C:\\Users\\me\\connector.step")]}]}])
    p = Project.load(proj)
    assert p.designs[0].objects[0].history[0].props["File"] == "C:\\Users\\me\\connector.step"
