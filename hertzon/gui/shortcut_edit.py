"""Shortcut capture field for the Keyboard Shortcuts dialog.

A plain QKeySequenceEdit records every key, so Enter and Esc became shortcuts instead of closing the dialog.
Here a bare Enter/Return always confirms (OK) and a bare Esc always cancels, also while the field has focus.
With a modifier (e.g. Ctrl+Return) they are still recorded as shortcuts.
"""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QKeySequenceEdit

_CONFIRM = (Qt.Key_Return, Qt.Key_Enter)
_CANCEL = (Qt.Key_Escape,)


def _dialog_key(ev) -> int | None:
    """Qt.Key_Return / Key_Escape if the event is a bare Enter or Esc (keypad flag allowed), else None."""
    mods = ev.modifiers() & ~Qt.KeypadModifier
    if mods != Qt.NoModifier:
        return None
    k = ev.key()
    if k in _CONFIRM:
        return Qt.Key_Return
    if k in _CANCEL:
        return Qt.Key_Escape
    return None


class ShortcutEdit(QKeySequenceEdit):
    """QKeySequenceEdit that leaves bare Enter (OK) and Esc (Cancel) to the dialog it sits in."""

    def event(self, ev):
        if ev.type() == QEvent.ShortcutOverride and _dialog_key(ev) is not None:
            ev.ignore()   # not a shortcut for this field; the key press below closes the dialog
            return True
        return super().event(ev)

    def keyPressEvent(self, ev):
        k = _dialog_key(ev)
        if k is None:
            super().keyPressEvent(ev)
            return
        ev.accept()
        dlg = self.window()
        if isinstance(dlg, QDialog):
            if k == Qt.Key_Return:
                dlg.accept()
            else:
                dlg.reject()

    def keyReleaseEvent(self, ev):
        if _dialog_key(ev) is None:
            super().keyReleaseEvent(ev)
