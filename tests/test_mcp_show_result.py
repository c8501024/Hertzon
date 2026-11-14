"""MCP show_result: pictures of reports and Solutions tabs drawn by Hertzon's own widgets, in file mode (off screen,
in a worker process) and through the live-link command of a window; every picture is kept in ~/.hertzon/shown
with a preview small enough for the Claude plugin's results pane."""
import base64
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hertzon.mcp import tools  # noqa: E402
from hertzon.mcp.protocol import Server  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))      # no live Hertzon window
    monkeypatch.setenv("HERTZON_MCP_MODE", "file")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    tools.S.default_path = None
    tools.S.designs.clear()
    return Server("hertzon", "test", tools.INSTRUCTIONS, tools.TOOLS)


def raw(srv, _tool, **args):
    return srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": _tool, "arguments": args}})["result"]


def call(srv, _tool, **args):
    res = raw(srv, _tool, **args)
    text = "\n".join(c["text"] for c in res["content"] if c["type"] == "text")
    assert not res["isError"], text
    return json.loads(text)


def _line(srv, tmp_path):
    call(srv, "create_project", path=str(tmp_path / "c.hzn"))
    call(srv, "create_circuit", name="Line")
    call(srv, "edit_circuit", add=[
        {"type": "PORT", "name": "P1", "x": 0, "y": 0},
        {"type": "MLIN", "name": "MS1", "attach": "P1:1", "props": {"W": "1.1mm", "L": "20mm"}},
        {"type": "PORT", "name": "P2", "attach": "MS1:2"}])
    assert call(srv, "run_simulation")["Setup1"]["state"] == "done"
    call(srv, "set_report", name="S21", kind="Rectangular", traces=["dB(S(2,1))", "dB(S(1,1))"])


def test_show_result_draws_a_report_and_keeps_it(server, tmp_path):
    _line(server, tmp_path)
    res = raw(server, "show_result", what="report", name="S21")
    assert not res["isError"], res["content"]
    text, img = res["content"][0]["text"], res["content"][-1]
    assert img["type"] == "image" and base64.b64decode(img["data"]).startswith(PNG)
    assert text.startswith("Line: report 'S21'") and "as shown" not in text      # no window: not as shown
    saved = Path(text.split("Saved: ")[1])
    assert saved.parent == tmp_path / "home" / "shown" and saved.read_bytes() == base64.b64decode(img["data"])
    previews = [p for p in saved.parent.iterdir() if ".preview." in p.name]
    assert len(previews) == 1 and previews[0].stat().st_size <= tools.PREVIEW_BYTES
    # a given size is drawn at that size
    res = raw(server, "show_result", what="report", name="S21", width=500, height=300)
    from PIL import Image
    from io import BytesIO
    assert Image.open(BytesIO(base64.b64decode(res["content"][-1]["data"]))).size == (500, 300)


def test_show_result_explains_what_is_missing(server, tmp_path):
    _line(server, tmp_path)
    res = raw(server, "show_result", what="report", name="Nope")
    assert res["isError"] and "set_report creates one" in res["content"][0]["text"]
    res = raw(server, "show_result", what="view")
    assert res["isError"] and "need a Hertzon window" in res["content"][0]["text"]


def test_kept_pictures_are_limited(tmp_path, monkeypatch):
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path))
    monkeypatch.setattr(tools, "SHOWN_KEEP", 3)
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    paths = [tools.keep_picture(buf.getvalue(), f"pic {i}") for i in range(5)]
    left = sorted(p.name for p in (tmp_path / "shown").glob("*.png") if ".preview." not in p.name)
    assert len(left) == 3 and Path(paths[-1]).name in left
    assert len(list((tmp_path / "shown").glob("*.preview.*"))) == 3


def test_window_capture_of_a_report_tab(tmp_path, monkeypatch):
    """The live-link command takes an open report tab as it is shown, and draws one that is not open."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HERTZON_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hertzon.gui.ai_link import AiLink
    from hertzon.gui.main_window import MainWindow
    from hertzon.model.circuit import TYPES
    from hertzon.model.circuit import Component as C
    from hertzon.model.design import Design, Report, circuit_setup
    w = MainWindow()
    link = AiLink(w)
    net = Design(name="Net", kind="circuit")
    net.setups.append(circuit_setup())
    net.components = [C("P1", "PORT", 0, 40, 0, dict(TYPES["PORT"].props)),
                      C("R1", "R", 30, 0, 0, {"R": "50ohm"}), C("P2", "PORT", 60, 40, 0, dict(TYPES["PORT"].props))]
    net.reports.append(Report("S", "Rectangular", "Setup1 : Sweep", ["dB(S(2,1))"]))
    w.doc.project.designs.append(net)
    path = str(tmp_path / "w.hzn")
    w.doc.project.save(path)
    w.doc.project.path = path
    link.handle("analyze_circuit", {"design": "Net", "setups": ["Setup1"]})
    r = link.handle("capture", {"design": "Net", "what": "report", "name": "S"})
    assert base64.b64decode(r["png"]).startswith(PNG) and r["shown"] is False
    w.open_report("S")
    r = link.handle("capture", {"design": "Net", "what": "report", "name": "S"})
    assert base64.b64decode(r["png"]).startswith(PNG) and r["shown"] is True
    with pytest.raises(RuntimeError, match="no report named"):
        link.handle("capture", {"design": "Net", "what": "report", "name": "X"})
    with pytest.raises(RuntimeError, match="what is report"):
        link.handle("capture", {"design": "Net", "what": "nothing"})
