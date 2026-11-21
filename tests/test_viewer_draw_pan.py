"""Shift + left drag pans the 3D view while an object is drawn (Kwang #14); a plain click still enters a point."""
from __future__ import annotations

import types

import pytest

pytest.importorskip("vtk")
pytest.importorskip("PySide6")


def _view(shift: bool):
    from hertzon.gui import viewer as V
    calls, clicks = [], []
    style = types.SimpleNamespace(**{n: (lambda n=n: calls.append(n)) for n in
                                     ("OnMiddleButtonDown", "OnMiddleButtonUp", "OnMouseMove")})
    pos = {"p": (100, 100)}
    iren = types.SimpleNamespace(GetShiftKey=lambda: shift, GetControlKey=lambda: False,
                                 GetEventPosition=lambda: pos["p"])
    v = types.SimpleNamespace(plotter=types.SimpleNamespace(iren=types.SimpleNamespace(interactor=iren)),
                              style=style, draw=types.SimpleNamespace(current=None), _navigating=set(),
                              _band=None, _press_pos=None, _rot_last=None, snap_kind="",
                              _draw_point=lambda p: p, _show_snap_marker=lambda *a: None,
                              _update_preview=lambda: None, _show_dimensions=lambda *a: None,
                              cursorMoved=types.SimpleNamespace(emit=lambda *a: None),
                              _draw_click=lambda p: clicks.append(p))
    v._event_pos = lambda: pos["p"]
    for name in ("_on_left_press", "_on_left_release", "_on_move"):
        setattr(v, name, getattr(V.ModelerView, name).__get__(v))
    return v, pos, calls, clicks


def test_shift_drag_pans_while_drawing(monkeypatch):
    from hertzon.gui import viewer as V
    monkeypatch.setattr(V, "_alt_key", lambda: False)
    v, pos, calls, clicks = _view(shift=True)
    v._on_left_press(None, None)
    pos["p"] = (160, 130)
    v._on_move(None, None)
    v._on_left_release(None, None)
    assert calls == ["OnMiddleButtonDown", "OnMouseMove", "OnMiddleButtonUp"]
    assert not clicks and not v._navigating and v.draw is not None      # still drawing, no point entered


def test_plain_click_still_enters_a_point(monkeypatch):
    from hertzon.gui import viewer as V
    monkeypatch.setattr(V, "_alt_key", lambda: False)
    v, pos, calls, clicks = _view(shift=False)
    v._on_left_press(None, None)
    v._on_left_release(None, None)
    assert clicks == [(100, 100)] and not calls
