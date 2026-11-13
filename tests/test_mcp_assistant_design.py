"""Design assistants of the MCP server and the empty design of create_project (file mode, no solve)."""
from test_mcp_more import call, server  # noqa: F401  (fixture)


def test_assistant_fills_the_empty_design_of_create_project(server, tmp_path):
    """create_project starts with an empty design; the first assistant replaces it instead of adding its
    design next to it. A design with content stays and the next assistant adds its own."""
    call(server, "create_project", path=str(tmp_path / "e.hzn"))
    call(server, "create_patch_antenna", frequency_ghz=2.45)
    assert [d if isinstance(d, str) else d["name"] for d in call(server, "get_project")["designs"]] == ["Patch_2.45GHz"]
    call(server, "create_patch_antenna", frequency_ghz=2.45)
    assert [d if isinstance(d, str) else d["name"] for d in call(server, "get_project")["designs"]] == \
        ["Patch_2.45GHz", "Patch_2.45GHz_2"]
    call(server, "create_project", path=str(tmp_path / "f.hzn"))
    call(server, "set_design", variables={"a": "1mm"})          # not empty any more: kept
    call(server, "create_patch_antenna", frequency_ghz=2.45)
    assert [d if isinstance(d, str) else d["name"] for d in call(server, "get_project")["designs"]] == \
        ["Design1", "Patch_2.45GHz"]

