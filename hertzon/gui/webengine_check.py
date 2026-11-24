"""Open the Fields Calculator (Results > Calculator) in the real macOS window system and report whether the
process survives (#547). Run with the Python of an installed Hertzon:

    python -m hertzon.gui.webengine_check            # every variant in its own process, a table at the end
    python -m hertzon.gui.webengine_check VARIANT    # one variant in this process

Variants differ in how Qt WebEngine is set up before the application exists:
  app        as Hertzon starts (hertzon.gui.app._web_engine_setup)
  no-opengl  without forcing OpenGL for Qt Quick / WebEngine
  no-flag    without --disable-gpu
  plain      neither (only the shared OpenGL contexts)
The exit code of each variant process is 0 when the calculator opened and the window kept running."""
from __future__ import annotations

import faulthandler
import os
import subprocess
import sys
import time

VARIANTS = ("app", "no-opengl", "no-flag", "plain")


def setup(variant: str) -> None:
    from PySide6.QtCore import QCoreApplication, Qt
    if variant == "app":
        from hertzon.gui.app import _web_engine_setup
        _web_engine_setup()
        return
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    if variant == "no-opengl":
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --disable-gpu").strip()
    if variant == "no-flag":
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)
    import PySide6.QtWebEngineWidgets  # noqa: F401


def run_one(variant: str, seconds: float = 12.0) -> int:
    faulthandler.enable(all_threads=True)
    setup(variant)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    print(f"[{variant}] platform: {app.platformName()}", flush=True)
    from hertzon.gui.main_window import MainWindow
    win = MainWindow()
    if hasattr(win, "prepare_equations"):
        win.prepare_equations()
    win.showMaximized()
    t0 = time.time()
    while time.time() - t0 < 2:
        app.processEvents()
    print(f"[{variant}] window shown, opening the calculator", flush=True)
    win.fields_calculator()
    fe = getattr(win.calc_window, "fe", None)
    print(f"[{variant}] calculator opened, formula editor: {type(fe).__name__ if fe is not None else None}",
          flush=True)
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)
    ready = getattr(fe, "is_ready", None)
    print(f"[{variant}] still running after {seconds:g} s, formula page ready: {ready}", flush=True)
    win.close()
    app.processEvents()
    return 0


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        return run_one(argv[1])
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)            # the real window system, not offscreen
    results = {}
    for v in VARIANTS:
        try:
            p = subprocess.run([sys.executable, "-m", "hertzon.gui.webengine_check", v], env=env, timeout=180)
            results[v] = p.returncode
        except subprocess.TimeoutExpired:
            results[v] = "timeout"
    print("\nvariant     exit code (0 = calculator opened, negative = killed by that signal)")
    for v, rc in results.items():
        print(f"{v:<11} {rc}")
    return 0 if results.get("app") == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
