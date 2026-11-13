"""Hertzon look: light, flat, with a navy menu bar and a gold accent. Only colours and a few frames change;
layout, menus and workflow stay as they are."""
from __future__ import annotations

NAVY = "#14213a"
GOLD = "#c9952f"
GOLD_BG = "#f6e7c8"
INK = "#1f2633"
MUTED = "#6b7385"
LINE = "#e2e5ea"
WINDOW = "#f4f5f7"
# which model a circuit block uses: full-wave FEM in cobalt blue (the complement of the gold), the circuit model in
# brick red; switched automatically (dashed, gold mark) or not usable (grey, crimson mark)
MODEL_FEM, MODEL_FEM_BG = "#2f6db5", "#e3ecf8"
MODEL_CIRCUIT, MODEL_CIRCUIT_BG = "#c4473a", "#f8e4e1"
MODEL_NONE, MODEL_NONE_BG = "#8a909c", "#eef0f3"
MODEL_ERROR = "#a3123a"

STYLE = f"""
QMenuBar {{ background: {NAVY}; color: #dfe5ef; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 4px; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background: #24345a; color: #ffffff; }}
QLabel#Brand {{ color: #ffffff; font-weight: 600; font-size: 13px; padding: 0px 10px 0px 4px; }}
QMainWindow::separator {{ background: {LINE}; width: 1px; height: 1px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {WINDOW}; color: {MUTED}; padding: 5px 8px; border-bottom: 1px solid {LINE}; }}
QStatusBar {{ background: #ffffff; border-top: 1px solid {LINE}; color: {MUTED}; }}
QProgressBar {{ border: 1px solid {LINE}; border-radius: 3px; background: #eceef2; text-align: center; color: {INK}; }}
QProgressBar::chunk {{ background: {GOLD}; border-radius: 2px; }}
QTabBar::tab:selected {{ color: {INK}; }}
QHeaderView::section {{ background: {WINDOW}; color: {MUTED}; border: none; border-right: 1px solid {LINE};
    border-bottom: 1px solid {LINE}; padding: 3px 6px; }}
"""


def apply(app) -> None:
    from PySide6.QtGui import QColor, QPalette
    pal = app.palette()
    for role, c in ((QPalette.Window, WINDOW), (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f7f8fa"),
                    (QPalette.Button, "#fafbfc"), (QPalette.WindowText, INK), (QPalette.Text, INK),
                    (QPalette.ButtonText, INK), (QPalette.ToolTipBase, "#ffffff"), (QPalette.ToolTipText, INK),
                    (QPalette.Link, "#9a6c14")):
        pal.setColor(role, QColor(c))
    for group in (QPalette.Active, QPalette.Inactive):
        pal.setColor(group, QPalette.Highlight, QColor(GOLD_BG))
        pal.setColor(group, QPalette.HighlightedText, QColor(INK))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)


def brand(window) -> None:
    """Logo and name at the left end of the menu bar."""
    from pathlib import Path
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget
    w = QWidget(window.menuBar())
    lay = QHBoxLayout(w)
    lay.setContentsMargins(6, 0, 0, 0)
    lay.setSpacing(6)
    png = Path(__file__).with_name("hertzon.png")
    if png.exists():
        logo = QLabel()
        logo.setPixmap(QPixmap(str(png)).scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(logo)
    name = QLabel("Hertzon")
    name.setObjectName("Brand")
    lay.addWidget(name)
    window.menuBar().setCornerWidget(w, Qt.TopLeftCorner)
    window._brand = w
