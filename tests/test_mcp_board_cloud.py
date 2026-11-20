"""MCP tools board (placement, routing, equal phase of a circuit board) and cloud (file mode, no solve)."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_mcp_more import call, error, server  # noqa: E402,F401

EXAMPLE = ROOT / "examples" / "VNA_TestSet_2G4.hzn"


def test_board_routes_again_and_matches_phase(server, tmp_path):  # noqa: F811
    path = tmp_path / "vna.hzn"
    shutil.copy(EXAMPLE, path)
    b = call(server, "board", project=str(path), design="TestSet")
    assert b["unrouted"] == [] and len(b["routes"]) >= 3 and not b["overlaps"]
    recv = [r["route"] for r in b["routes"] if {r["from"].split(":")[0], r["to"].split(":")[0]} & {"R", "A", "B"}]
    assert len(recv) == 3
    # take the receiver routes out and lay them again
    b = call(server, "board", project=str(path), design="TestSet", action="remove_routes", routes=recv)
    assert len(b["unrouted"]) == 3
    b = call(server, "board", project=str(path), design="TestSet", action="connect", z0=50)
    assert b["unrouted"] == []
    recv = [r["route"] for r in b["routes"] if {r["from"].split(":")[0], r["to"].split(":")[0]} & {"R", "A", "B"}]
    b = call(server, "board", project=str(path), design="TestSet", action="match_phase", routes=recv, f0_ghz=2.45)
    ph = [r["phase_deg"] for r in b["routes"] if r["route"] in recv]
    assert max(ph) - min(ph) < 0.5 and len({r["group"] for r in b["routes"] if r["route"] in recv}) == 1
    # with an offset: the first route lags 30 degrees more than the others
    b = call(server, "board", project=str(path), design="TestSet", action="match_phase", routes=recv,
             f0_ghz=2.45, offsets_deg={str(recv[0]): 30})
    ph = {r["route"]: r["phase_deg"] for r in b["routes"] if r["route"] in recv}
    assert abs((ph[recv[0]] - ph[recv[1]]) - 30) < 1.0 and abs(ph[recv[1]] - ph[recv[2]]) < 0.5
    # stored in the file: a new look sees the same routes
    again = call(server, "board", project=str(path), design="TestSet")
    assert [r["length_mm"] for r in again["routes"]] == [r["length_mm"] for r in b["routes"]]
    assert "two or more" in error(server, "board", project=str(path), design="TestSet", action="match_phase",
                                  routes=[1])


def test_board_needs_a_circuit_and_cloud_a_window(server, tmp_path):  # noqa: F811
    path = tmp_path / "vna.hzn"
    shutil.copy(EXAMPLE, path)
    assert "not a circuit design" in error(server, "board", project=str(path), design=_a_3d_design(path))
    assert "window" in error(server, "cloud", project=str(path))


def _a_3d_design(path) -> str:
    from hertzon.model.design import Project
    return next(d.name for d in Project.load(path).designs if getattr(d, "kind", "3d") != "circuit")


class _Prov:
    key, name, ssh_user = "hetzner", "Hetzner Cloud", "root"

    def server_types(self, location=""):
        return [{"name": "ccx33", "cores": 8, "memory": 32, "dedicated": True, "price": 0.11}]


def test_cloud_session_slot_has_its_own_state(tmp_path):
    from hertzon.remote.cloud import CloudSession
    shared = CloudSession(_Prov(), "ccx33", "fsn1", workdir=tmp_path, version="9.9.9")
    own = CloudSession(_Prov(), "ccx33", "fsn1", workdir=tmp_path, version="9.9.9", slot="w123")
    assert shared.state_file.name == "hetzner.json" and own.state_file.name == "hetzner-w123.json"
    assert own.tunnel.folder == tmp_path / "cloud" / "w123" and shared.tunnel.folder == tmp_path / "cloud"
    own._save({"id": 1, "name": "hertzon-a"})
    assert own.state() and not shared.state()


def test_window_cloud_command(monkeypatch):
    """ai_link 'cloud': status, offers and a target with a server of the window's own."""
    import types

    from hertzon.gui.ai_link import AiLink
    from hertzon.remote.cloud import providers
    vals = {"cloud/provider": "hetzner", "cloud/hetzner_token": "tok", "cloud/server_type": "ccx33",
            "cloud/location": "fsn1"}
    applied = []

    class Settings:
        def value(self, k, d=None, type=None):  # noqa: A002
            return vals.get(k, d)

    win = types.SimpleNamespace(settings=Settings(), solver=types.SimpleNamespace(busy=False, queue=[], remote=None),
                                _remote_label=lambda: "", msgs=types.SimpleNamespace(add=lambda *a: None))
    win.apply_hpc_settings = lambda: applied.append((win.solve_target, win.cloud_slot, win.cloud_type))
    link = types.SimpleNamespace(win=win)
    st = AiLink._cloud(link, {"action": "status"})
    assert st["solves_on"] == "local" and st["cloud"]["configured"] and st["cloud"]["server_type"] == "ccx33"
    monkeypatch.setattr(providers, "from_settings", lambda get: (_Prov(), "ccx33", "fsn1"))
    off = AiLink._cloud(link, {"action": "offers"})
    assert off["offers"][0] == {"name": "ccx33", "cores": 8, "memory_gb": 32, "dedicated": True,
                                "price_per_hour": 0.11, "currency": "EUR"}
    AiLink._cloud(link, {"action": "use", "target": "cloud", "own_server": True, "server_type": "ccx43"})
    assert applied[-1][0] == "cloud" and applied[-1][1].startswith("w") and applied[-1][2] == "ccx43"
    win.solver.busy = True
    import pytest
    with pytest.raises(RuntimeError, match="solving"):
        AiLink._cloud(link, {"action": "use", "target": "local"})
