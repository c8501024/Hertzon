"""Application entry point: ``python -m hertzon [project.hzn]``."""
from __future__ import annotations

import sys


def _crash_log():
    """Hard crashes (native code) and uncaught Python errors go to Hertzon_crash.log in the user's home,
    so a window that just disappears still leaves a trace."""
    import faulthandler
    import time
    import traceback
    from pathlib import Path
    path = Path.home() / "Hertzon_crash.log"
    try:
        f = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    f.write(f"\n=== Hertzon started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    faulthandler.enable(file=f, all_threads=True)
    old = sys.excepthook

    def hook(t, v, tb):
        f.write(f"--- uncaught {time.strftime('%H:%M:%S')} ---\n")
        traceback.print_exception(t, v, tb, file=f)
        old(t, v, tb)
    sys.excepthook = hook
    return f


def _splash(app):
    """Loading screen while the 3D viewer and the solver interface are imported."""
    from pathlib import Path
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap, QGuiApplication
    from PySide6.QtWidgets import QSplashScreen
    img = Path(__file__).with_name("splash.jpg")
    pm = QPixmap(str(img))
    if pm.isNull():
        return None
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    width = min(960.0, 0.5 * screen.availableGeometry().width()) if screen else 800.0
    pm = pm.scaledToWidth(int(width * dpr), Qt.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    class Splash(QSplashScreen):
        """Clicks do nothing: Qt's splash screen would hide on a click while loading goes on behind it."""

        def mousePressEvent(self, ev):
            ev.accept()

        def mouseDoubleClickEvent(self, ev):
            ev.accept()

    sp = Splash(pm)                    # not on top: it must never hide a question or another Hertzon window
    sp.show()
    app.processEvents()
    return sp


def _status(sp, app, text: str) -> None:
    if sp is not None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        sp.showMessage(text + "  ", Qt.AlignRight | Qt.AlignBottom, QColor(200, 215, 240))
        app.processEvents()


def _load_in_background(app, work):
    """Run the slow imports in a thread while the window system keeps getting answers, so Windows never
    marks the starting Hertzon as not responding (clicks on the loading screen then made it hang)."""
    import threading
    box: dict = {}

    def run():
        try:
            box["value"] = work()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e
    t = threading.Thread(target=run, name="hertzon-startup", daemon=True)
    t.start()
    while t.is_alive():
        app.processEvents()
        t.join(0.02)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _heavy_imports():
    import pyvista as pv
    pv.global_theme.allow_empty_mesh = True
    from . import main_window  # noqa: F401   (VTK, solver interface, dialogs)


def _material_library():
    """EMerge's material library: importing emsutil (with scipy and matplotlib) took seconds on a cold start,
    and the first click on an object waited for it (the Properties list every material)."""
    from ..model.materials import emerge_library
    emerge_library()


def _load_in_thread(work) -> None:
    """Run work in the background once the window is open, so it is ready before it is first needed."""
    import threading
    threading.Thread(target=work, name="hertzon-preload", daemon=True).start()


def _web_engine_setup() -> None:
    """The formula editor of the Fields Calculator is a web view in the main window, next to the 3D view.
    Qt WebEngine has to be loaded before the application object exists. The main window can draw with one
    graphics interface only: the web view uses OpenGL like the 3D view (with its default, Direct3D on
    Windows, it stayed black), and its page is drawn without the GPU (a formula needs none, and some drivers
    left it black)."""
    import os
    from PySide6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--disable-gpu" not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --disable-gpu").strip()
    try:
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception:  # noqa: BLE001   (without it the calculator has the text line only)
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    _keep = _crash_log()  # noqa: F841
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    if sys.platform == "win32":   # own taskbar icon instead of the Python one
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hertzon.Hertzon")
        except Exception:  # noqa: BLE001
            pass
    _web_engine_setup()
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Hertzon")
    app.setOrganizationName("Hertzon")
    app.setStyle("Fusion")
    from .theme import apply as _theme
    _theme(app)
    ico = Path(__file__).with_name("hertzon.ico")
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    sp = _splash(app)
    _status(sp, app, "Loading 3D viewer, modeler and solver interface ...")
    _load_in_background(app, _heavy_imports)
    _status(sp, app, "Opening the main window ...")
    from .main_window import MainWindow
    win = MainWindow()
    win.prepare_equations()   # before the window is shown: opening the Equations tab later does not rebuild it
    files = [a for a in argv[1:] if a.lower().endswith(".hzn")]
    # always maximized: a restored maximized geometry (or a resize on top of it) left the window a bit wider
    # than the screen on Windows, so its right edge was cut off until it was restored and maximized again
    win.showMaximized()
    app.processEvents()
    win.view.warm_up()        # the first hover or click in the 3D view then does not wait for the graphics driver
    _load_in_thread(_material_library)
    if sp is not None:
        sp.finish(win)
    if files:
        # opened in the visible window (own progress dialog): a question while opening (e.g. a newer
        # autosave) appears in front instead of waiting unseen behind the loading screen
        app.processEvents()
        win.file_open(files[0])
    if "--ai-link" in argv:          # started by the MCP server for an AI assistant
        win.ai_link.start(auto_register=False)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
