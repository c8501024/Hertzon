"""MCP post-processing on the saved fields (evaluate_fields, get_power_budget): Poynting flux through sheets and
closed boundaries, volume losses per object and material split into dielectric and magnetic, and the power
balance of a closed lossy waveguide (accepted power = lost power, nothing radiated)."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.mcp import tools  # noqa: E402
from hertzon.mcp.protocol import Server  # noqa: E402


def _call(srv, _tool, **args):
    res = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": _tool, "arguments": args}})["result"]
    text = "\n".join(c["text"] for c in res["content"] if c["type"] == "text")
    assert not res["isError"], text
    return json.loads(text)


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    """WR-90, 40 mm, with a dielectric slab (tan d 0.02) and a magnetic slab (mu_r 2, tan d_m 0.05), at 10 GHz."""
    pytest.importorskip("emerge")
    from hertzon.model import schema
    from hertzon.model.design import Assignment, Project, Setup
    from hertzon.model.materials import Material
    from hertzon.solver import bridge as B
    from hertzon.solver.results import results_dir
    tmp = tmp_path_factory.mktemp("post")
    p = Project()
    p.materials += [Material(name="lossy_diel", permittivity="2.2", loss_tangent="0.02"),
                    Material(name="lossy_mag", permittivity="1", permeability="2", magnetic_loss_tangent="0.05")]
    d = p.designs[0]
    d.units = "mm"
    d.add_object("CreateBox", "Guide", Position="0,0,0", XSize="22.86", YSize="40", ZSize="10.16", material="vacuum")
    d.add_object("CreateBox", "Diel", Position="0,8,0", XSize="22.86", YSize="6", ZSize="10.16",
                 material="lossy_diel")
    d.add_object("CreateBox", "Mag", Position="0,24,0", XSize="22.86", YSize="4", ZSize="10.16", material="lossy_mag")
    for i, y in enumerate(("0", "40"), 1):
        d.add_object("CreateRectangle", f"P{i}", Position=f"0,{y},0", Axis="Y", XSize="22.86", ZSize="10.16")
        props = schema.defaults(schema.EXCITATIONS["Wave Port"])
        props.update(PortNumber=str(i))
        d.excitations.append(Assignment(f"WP{i}", "Wave Port", props, objects=[f"P{i}"]))
    st = Setup("Setup1")
    st.props.update(Frequency="10GHz", MaximumPasses="3")
    d.setups.append(st)
    path = str(tmp / "wg.hzn")
    p.save(path)
    p = Project.load(path)
    d = p.designs[0]
    B.Bridge(p, d, d.setup("Setup1"), results_dir(path, d.name, "Setup1")).run()
    return path


@pytest.fixture()
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    tools.S.default_path = None
    tools.S.designs.clear()
    return Server("hertzon", "test", tools.INSTRUCTIONS, tools.TOOLS)


def test_power_budget_of_a_lossy_closed_guide(solved, server):
    r = _call(server, "get_power_budget", project=solved, port=1)
    row = r["rows"][0]
    assert "P_radiated_W" not in row and "radiated" in r["note"]
    loss = row["P_loss_W"]
    assert loss > 0.01
    # closed guide with PEC walls: the stored total comes with a far field surface only, here it is the volume sum,
    # and it matches the accepted power from the S-parameters (energy conservation of the solution)
    assert row["P_accepted_W"] == pytest.approx(loss, rel=0.02)
    mat = row["volume_loss_by_material"]
    assert mat["lossy_diel"]["dielectric_W"] > 0 and mat["lossy_diel"]["magnetic_W"] == 0
    assert mat["lossy_mag"]["magnetic_W"] > 0 and mat["lossy_mag"]["dielectric_W"] == 0
    assert row["P_volume_loss_W"] == loss and abs(row["balance"]) < 0.002
    assert row["volume_loss_by_object"]["Diel"]["material"] == "lossy_diel"


def test_flux_through_sheets_and_a_closed_boundary(solved, server):
    budget = _call(server, "get_power_budget", project=solved, port=1)["rows"][0]
    r = _call(server, "evaluate_fields", project=solved, port=1,
              expressions=["flux(P1, re(S)) - flux(P2, re(S))", "flux(Diel, re(S))", "flux(Mag, re(S))",
                           "int_vol(Mag, 0.5*omega*mu0*mu_r*tandm*|H|^2)", "|E|"])
    v = r["rows"][0]["values"]
    assert v["flux(P1, re(S)) - flux(P2, re(S))"]["unit"] == "W"
    assert v["flux(P1, re(S)) - flux(P2, re(S))"]["value"] == pytest.approx(budget["P_loss_W"], rel=0.05)
    # the closed boundary of a lossy block, normal out: minus the power it absorbs
    diel = budget["volume_loss_by_object"]["Diel"]["dielectric_W"]
    mag = budget["volume_loss_by_object"]["Mag"]["magnetic_W"]
    assert -v["flux(Diel, re(S))"]["value"] == pytest.approx(diel, rel=0.1)
    assert -v["flux(Mag, re(S))"]["value"] == pytest.approx(mag, rel=0.1)
    assert v["int_vol(Mag, 0.5*omega*mu0*mu_r*tandm*|H|^2)"]["value"] == pytest.approx(mag, rel=1e-6)
    assert "error" in v["|E|"]                                # a field, not a value


def test_missing_fields_are_named(solved, server):
    msg = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "evaluate_fields", "arguments": {"project": solved, "solution": "Nope", "expr": "1"}}})["result"]
    assert msg["isError"] and "No saved fields for 'Setup1 : Nope'" in msg["content"][0]["text"]
