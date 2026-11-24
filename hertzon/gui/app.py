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
    from ..blas_threads import load_with_few_threads
    load_with_few_threads()   # before numpy, scipy and gmsh load: about 1 GB less committed memory on 16 cores
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


def _preload_solver() -> None:
    """Start python -m hertzon.solver.preload: it loads EMerge and its libraries once, so the first Analyze after
    a restart or an update does not wait (about 50 s on a Windows laptop) for them to be read and checked by
    the virus scanner. HERTZON_NO_PRELOAD=1 switches it off."""
    import os
    import subprocess
    from pathlib import Path
    if os.environ.get("HERTZON_NO_PRELOAD") == "1":
        return
    try:
        from .solve import solver_python
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([solver_python(), "-m", "hertzon.solver.preload"], env=env, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


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


def _application(argv):
    """The QApplication. On macOS a subclass that takes Finder's open-file events in its own event() (see
    _finder_files)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        return app
    if sys.platform != "darwin":
        return QApplication(argv)
    from PySide6.QtCore import QEvent

    class HertzonApplication(QApplication):
        """Finder's open-file events arrive at the application object itself. They are taken here and not
        with an event filter on the application: such a filter written in Python sees every event of every
        object, also the ChildAdded event of a Qt WebEngine object that is still being constructed, and
        PySide crashed (segmentation fault) when it wrapped that object for Python. That happened on macOS
        only, where the filter was installed, as soon as the formula editor of the Fields Calculator
        loaded its page (#547)."""
        open_file = None            # callback(path) for a FileOpen event

        def event(self, ev):  # noqa: D102
            if ev.type() == QEvent.FileOpen and self.open_file is not None:
                self.open_file(ev.file())
                return True
            return super().event(ev)

    return HertzonApplication(argv)


def _finder_files(app):
    """macOS: a .hzn opened in Finder (double click, Open With, dropped on the Dock icon) reaches Hertzon as an
    open-file event, not as a command-line argument. Until the main window exists the paths are kept; later
    ones open in the window. Never an event filter on the whole application (#547): see _application."""
    from PySide6.QtCore import QTimer

    class FinderFiles:
        def __init__(self):
            self.pending: list[str] = []
            self.win = None

        def __call__(self, path: str) -> None:
            if not path.lower().endswith(".hzn"):
                return
            if self.win is None:
                self.pending.append(path)
            else:
                QTimer.singleShot(0, lambda: self.win.file_open(path))

    f = FinderFiles()
    if hasattr(app, "open_file"):
        app.open_file = f
    return f


def _mac_app_name(name: str = "Hertzon") -> bool:
    """macOS: the application menu shows the name of the running program's bundle, which is the Python that
    Hertzon.app starts ("Python", "About Python", "Quit Python", issue #543). Setting CFBundleName in the
    main bundle's info dictionary before Qt creates the application makes it show `name`. Uses the
    Objective-C runtime through ctypes (no extra package). Returns True when the name is set."""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        vp, cp = ctypes.c_void_p, ctypes.c_char_p
        objc.objc_getClass.restype, objc.objc_getClass.argtypes = vp, [cp]
        objc.sel_registerName.restype, objc.sel_registerName.argtypes = vp, [cp]
        addr = ctypes.cast(objc.objc_msgSend, vp).value

        def send(obj, sel: bytes, *args, argtypes=(), restype=vp):
            # objc_msgSend must be called with the exact prototype (arm64 passes variadic arguments differently)
            fn = ctypes.CFUNCTYPE(restype, vp, vp, *argtypes)(addr)
            return fn(obj, objc.sel_registerName(sel), *args)

        def nsstring(s: str):
            return send(objc.objc_getClass(b"NSString"), b"stringWithUTF8String:", s.encode(), argtypes=(cp,))

        bundle = send(objc.objc_getClass(b"NSBundle"), b"mainBundle")
        done = False
        for getter in (b"localizedInfoDictionary", b"infoDictionary"):
            info = send(bundle, getter) if bundle else None
            # only a mutable dictionary takes the name; calling setObject:forKey: on another one would raise
            # an Objective-C exception and end the process
            if not info or not send(info, b"respondsToSelector:", objc.sel_registerName(b"setObject:forKey:"),
                                    argtypes=(vp,), restype=ctypes.c_bool):
                continue
            send(info, b"setObject:forKey:", nsstring(name), nsstring("CFBundleName"), argtypes=(vp, vp))
            got = send(info, b"objectForKey:", nsstring("CFBundleName"), argtypes=(vp,))
            text = send(got, b"UTF8String", restype=cp) if got else None
            done = done or text == name.encode()
        return done
    except Exception:  # noqa: BLE001     (the name is cosmetic: Hertzon starts in any case)
        return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    _keep = _crash_log()  # noqa: F841
    from pathlib import Path
    from PySide6.QtGui import QIcon
    if sys.platform == "win32":   # own taskbar icon instead of the Python one
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hertzon.Hertzon")
        except Exception:  # noqa: BLE001
            pass
    _mac_app_name()           # before Qt starts the Cocoa application, which reads the name for its menu
    _web_engine_setup()
    app = _application(argv)
    app.setApplicationName("Hertzon")
    app.setOrganizationName("Hertzon")
    app.setStyle("Fusion")
    finder = _finder_files(app) if sys.platform == "darwin" else None
    from .theme import apply as _theme
    _theme(app)
    # the Dock on macOS shows the window icon; the PNG is sharper there than the Windows icon file
    ico = Path(__file__).with_name("hertzon.png" if sys.platform == "darwin" else "hertzon.ico")
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    sp = _splash(app)
    _status(sp, app, "Loading 3D viewer, modeler and solver interface ...")
    _load_in_background(app, _heavy_imports)
    _status(sp, app, "Opening the main window ...")
    from .main_window import MainWindow
    win = MainWindow()
    win.prepare_equations()   # before the window is shown: opening the Equations tab later does not rebuild it
    #                           (the tab and its web engine are made when it is first opened)
    files = [a for a in argv[1:] if a.lower().endswith(".hzn")]
    # always maximized: a restored maximized geometry (or a resize on top of it) left the window a bit wider
    # than the screen on Windows, so its right edge was cut off until it was restored and maximized again
    win.showMaximized()
    app.processEvents()
    win.view.warm_up()        # the first hover or click in the 3D view then does not wait for the graphics driver
    _load_in_thread(_material_library)
    _preload_solver()
    if sp is not None:
        sp.finish(win)
    if finder is not None:
        app.processEvents()
        files += [f for f in finder.pending if f not in files]
        finder.win = win
    if files:
        # opened in the visible window (own progress dialog): a question while opening (e.g. a newer
        # autosave) appears in front instead of waiting unseen behind the loading screen
        app.processEvents()
        win.file_open(files[0])
    if "--ai-link" in argv:          # started by the MCP server for an AI assistant
        win.ai_link.start(auto_register=False)
    elif not files:
        # the start page (3D FEM or Circuit), unless switched off with its "Show at startup" box; not when an
        # AI assistant started Hertzon, which would wait behind the dialog
        from PySide6.QtCore import QTimer
        from .start_page import SHOW_AT_STARTUP
        if win.settings.value(SHOW_AT_STARTUP, True, type=bool):
            QTimer.singleShot(0, win.start_page)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
