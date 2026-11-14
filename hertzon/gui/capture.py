"""Pictures of what Hertzon shows, for AI assistants (MCP show_result): a report, a field plot or the model in
the 3D view, or a tab of the Solutions window (convergence, profile, matrix data, mesh statistics).

Every picture is drawn by the same widgets as on screen: an open report tab or the 3D view is taken as it is
shown (same size, colours, scales and view), so the assistant and the user see the same thing. A report that
is not open is drawn in a report window of the given size that is never shown.
"""
from __future__ import annotations
import io
import os
import tempfile

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

DEFAULT_SIZE = (900, 600)
SOLUTION_TABS = ("Convergence", "Profile", "Matrix Data", "Mesh Statistics", "Eigenmode Data")


def _settle(n: int = 30) -> None:
    app = QApplication.instance()
    for _ in range(n):
        app.processEvents()


def _widget_png(w) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    w.grab().save(buf, "PNG")
    return bytes(buf.data())


def _plotter_png(plotter) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        plotter.render()
        plotter.screenshot(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _figure_png(fig, size: tuple[int, int] | None) -> bytes:
    """A report's figure as PNG. Without a size: the size it has on screen (pixel for pixel)."""
    if size:
        fig.set_size_inches(size[0] / fig.dpi, size[1] / fig.dpi)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=fig.dpi)
    return buf.getvalue()


def report_png(doc, name: str, open_tab=None, size: tuple[int, int] | None = None) -> bytes:
    """A report as PNG. open_tab: the ReportWindow showing it in the window (taken as shown), else it is
    drawn in a hidden report window of the given size (default 900 x 600 px)."""
    from .reports import ReportWindow
    r = next((x for x in doc.design.reports if x.name == name), None)
    if r is None:
        raise RuntimeError(f"no report named {name!r}; reports: {[x.name for x in doc.design.reports]}")
    w = open_tab
    temp = w is None
    if temp:
        w = ReportWindow(doc, r)
        w.resize(*(size or DEFAULT_SIZE))
        size = size or DEFAULT_SIZE
    else:
        w.refresh()
    _settle()
    try:
        if getattr(w, "fig", None) is not None:
            visible = not temp and w.canvas.isVisible()
            if visible and size is None:
                return _widget_png(w.canvas)
            return _figure_png(w.fig, size)
        if getattr(w, "p3", None) is not None:              # 3D polar plot
            if temp:
                w.p3.window_size = list(size)
            return _plotter_png(w.p3)
        table = getattr(w, "table", None)                   # data tables
        if table is not None:
            if temp:
                table.resize(*size)
            return _widget_png(table)
        return _widget_png(w)
    finally:
        if temp:
            w.deleteLater()


def solutions_png(doc, setup: str | None, tab: str = "Convergence", size: tuple[int, int] | None = None) -> bytes:
    """A tab of the Solutions window (Convergence, Profile, Matrix Data, Mesh Statistics) of a setup."""
    from .panels import SolutionDataDialog
    setups = [s.name for s in doc.design.setups]
    if not setups:
        raise RuntimeError("the design has no analysis setup")
    setup = setup or setups[0]
    if setup not in setups:
        raise RuntimeError(f"no setup named {setup!r}; setups: {setups}")
    dlg = SolutionDataDialog(doc, setup)
    try:
        dlg._timer.stop()
        dlg.resize(*(size or (960, 620)))
        names = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        if tab not in names:
            raise RuntimeError(f"no tab {tab!r}; tabs: {names}")
        dlg.tabs.setCurrentIndex(names.index(tab))
        _settle()
        return _widget_png(dlg.tabs.currentWidget())
    finally:
        dlg.deleteLater()


def view_png(win, field_plot: str | None = None, size: tuple[int, int] | None = None) -> bytes:
    """The 3D view of the window as shown; with field_plot that plot is shown first (in the window too)."""
    if field_plot:
        if not any(p.name == field_plot for p in win.des.field_plots):
            raise RuntimeError(f"no field plot named {field_plot!r}; field plots: "
                               f"{[p.name for p in win.des.field_plots]}")
        win.show_field_plot(field_plot)
    else:
        win.tabs.setCurrentIndex(0)
    _settle(60)
    pl = win.view.plotter
    if size:
        old = list(pl.window_size)
        pl.window_size = list(size)
        try:
            return _plotter_png(pl)
        finally:
            pl.window_size = old
    return _plotter_png(pl)


def main(argv: list[str] | None = None) -> int:
    """File mode of MCP show_result (no Hertzon window): python -m hertzon.gui.capture '<json>' OUT.png draws a
    report or a Solutions tab of a saved project with Qt off screen."""
    import json
    import sys
    argv = sys.argv[1:] if argv is None else argv
    a, out = json.loads(argv[0]), argv[1]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from ..model.design import Project
    from .doc import Document
    proj = Project.load(a["project"])
    doc = Document(proj)
    if a.get("design"):
        doc.set_design(a["design"])
    size = tuple(a["size"]) if a.get("size") else None
    try:
        if a["what"] == "report":
            png = report_png(doc, a["name"], None, size)
        else:
            png = solutions_png(doc, a.get("setup"), a.get("tab") or "Convergence", size)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2
    with open(out, "wb") as f:
        f.write(png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
