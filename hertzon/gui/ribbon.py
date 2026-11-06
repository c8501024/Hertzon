"""Office style ribbon: tabs with titled groups of large and small icon buttons."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .icons import icon

STYLE = """
QTabWidget#Ribbon::pane { border: none; border-top: 1px solid #e2e5ea; border-bottom: 1px solid #e2e5ea;
    background: #ffffff; top: -1px; }
QTabWidget#Ribbon > QTabBar { background: #ffffff; }
QTabWidget#Ribbon > QTabBar::tab { padding: 6px 14px; border: none; border-bottom: 2px solid transparent;
    margin-right: 2px; background: transparent; color: #6b7385; }
QTabWidget#Ribbon > QTabBar::tab:selected { color: #1f2633; font-weight: 600; border-bottom: 2px solid #c9952f; }
QTabWidget#Ribbon > QTabBar::tab:hover:!selected { color: #1f2633; }
QWidget#RibbonPage { background: #ffffff; }
QFrame#RibbonGroup { background: transparent; }
QLabel#RibbonGroupTitle { color: #6b7385; font-size: 11px; }
QFrame#RibbonSep { color: #e2e5ea; }
QToolButton#RibbonLarge, QToolButton#RibbonSmall { border: 1px solid transparent; border-radius: 5px; padding: 1px 3px;
    background: transparent; color: #1f2633; }
QToolButton#RibbonLarge:hover, QToolButton#RibbonSmall:hover { background: #fbf3e2; border-color: #ecd7a8; }
QToolButton#RibbonLarge:pressed, QToolButton#RibbonSmall:pressed,
QToolButton#RibbonLarge:checked, QToolButton#RibbonSmall:checked { background: #f6e7c8; border-color: #c9952f; }
QToolButton#RibbonPrimary { border: 1px solid #b8862a; border-radius: 5px; padding: 1px 6px; background: #c9952f;
    color: #ffffff; font-weight: 600; }
QToolButton#RibbonPrimary:hover { background: #d6a43e; }
QToolButton#RibbonPrimary:pressed { background: #b8862a; }
QToolButton#RibbonLarge::menu-indicator, QToolButton#RibbonSmall::menu-indicator { subcontrol-position: bottom right; }
QScrollArea#RibbonScroll { border: none; background: #ffffff; }
QScrollArea#RibbonScroll QScrollBar:horizontal { height: 6px; background: #ffffff; }
QScrollArea#RibbonScroll QScrollBar::handle:horizontal { background: #c8cdd6; border-radius: 3px; min-width: 30px; }
QScrollArea#RibbonScroll QScrollBar::add-line, QScrollArea#RibbonScroll QScrollBar::sub-line { width: 0px; }
"""


class Group(QFrame):
    def __init__(self, title: str, owner):
        super().__init__()
        self.setObjectName("RibbonGroup")
        self.owner = owner
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 2, 4, 0)
        v.setSpacing(0)
        self.row = QHBoxLayout()
        self.row.setSpacing(2)
        self.row.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.row, 1)
        lab = QLabel(title)
        lab.setObjectName("RibbonGroupTitle")
        lab.setAlignment(Qt.AlignCenter)
        v.addWidget(lab)
        self._col: QVBoxLayout | None = None
        self._n = 0

    def _button(self, text, ic, slot, menu, large, tip, checkable=False) -> QToolButton:
        b = QToolButton()
        b.setObjectName("RibbonLarge" if large else "RibbonSmall")
        b.setAutoRaise(True)
        if isinstance(slot, QAction):
            b.setDefaultAction(slot)
            b.setText(text)
        elif slot is not None:
            b.clicked.connect(lambda _=False, s=slot: s())
        b.setText(text)
        if isinstance(ic, QIcon):
            b.setIcon(ic)
        elif ic:
            b.setIcon(icon(ic))
        if large and ic == "analyze":            # the one main action: gold button with a white icon
            b.setObjectName("RibbonPrimary")
            b.setIcon(icon(ic, (255, 255, 255)))
        if large:
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(32, 32))
            b.setMinimumWidth(48)
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        elif getattr(self, "icon_only", False) and ic:
            # Draw ribbon: compact icon-only buttons, the name is in the tooltip
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setIconSize(QSize(20, 20))
            b.setAutoRaise(True)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        else:
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setIconSize(QSize(16, 16))
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        if menu is not None:
            m = QMenu(b)
            for item in menu:
                if item is None:
                    m.addSeparator()
                else:
                    lab, fn = item
                    m.addAction(lab, fn)
            b.setMenu(m)
            b.setPopupMode(QToolButton.InstantPopup if slot is None else QToolButton.MenuButtonPopup)
        b.setToolTip(tip or text)
        b.setCheckable(checkable)
        return b

    def large(self, text: str, ic: str, slot=None, menu=None, tip: str | None = None) -> QToolButton:
        self._col = None
        b = self._button(text, ic, slot, menu, True, tip)
        self.row.addWidget(b)
        return b

    def small(self, text: str, ic: str | None, slot=None, menu=None, tip: str | None = None,
              checkable: bool = False) -> QToolButton:
        b = self._button(text, ic, slot, menu, False, tip, checkable)
        self.widget(b)
        return b

    def widget(self, w: QWidget) -> QWidget:
        """Put a widget in the current 3-row column (a new column starts every three rows)."""
        if self._col is None or self._n == 3:
            self._col = QVBoxLayout()
            self._col.setSpacing(1)
            self._col.setContentsMargins(0, 0, 0, 0)
            self.row.addLayout(self._col)
            self.row.setAlignment(self._col, Qt.AlignTop)
            self._n = 0
        self._col.addWidget(w)
        self._n += 1
        return w

    def end_column(self) -> None:
        if self._col is not None:
            while self._n < 3:
                self._col.addStretch(1)
                self._n += 1
        self._col = None


class Page(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("RibbonPage")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(2, 2, 2, 2)
        self.lay.setSpacing(2)
        self.lay.addStretch(1)
        # the height follows the ribbon (the buttons squeeze a little as before), only the width scrolls
        self.lay.setSizeConstraint(QHBoxLayout.SetNoConstraint)

    def minimumSizeHint(self) -> QSize:
        return QSize(self.lay.minimumSize().width(), 0)

    def group(self, title: str) -> Group:
        if self.lay.count() > 1:
            sep = QFrame()
            sep.setObjectName("RibbonSep")
            sep.setFrameShape(QFrame.VLine)
            self.lay.insertWidget(self.lay.count() - 1, sep)
        g = Group(title, self)
        self.lay.insertWidget(self.lay.count() - 1, g)
        return g


class Ribbon(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Ribbon")
        self.setDocumentMode(False)
        self.setStyleSheet(STYLE)
        self.setFixedHeight(118)                # room for the thin scroll bar of a page on a narrow screen
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def page(self, title: str) -> Page:
        p = Page()
        # in a scroll area: on a narrow screen the page scrolls sideways instead of making the main window
        # wider than the screen (a maximized window was cut off on the right)
        sc = QScrollArea()
        sc.setObjectName("RibbonScroll")
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sc.setWidget(p)
        self.addTab(sc, title)
        return p
