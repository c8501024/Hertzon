"""Port voltage of a lumped port: samples outside the port's tetrahedra and the Integration Line."""
import numpy as np
import pytest

pytest.importorskip("emerge")

from hertzon.solver.bridge import line_on_sheet  # noqa: E402


def test_samples_outside_the_port_tets_are_looked_up_in_the_whole_mesh(monkeypatch):
    from emerge._emerge.elements.nedelec2 import Nedelec2
    from hertzon.solver import emerge_patches as EP

    calls = []

    def plain(self, xs, ys, zs, tetids=None, usenan=True):
        calls.append(tetids)
        if tetids is None:                      # the whole mesh finds every point
            return np.full(xs.shape, 7, dtype=np.int64)
        out = np.arange(xs.size, dtype=np.int64)
        out[1::2] = -1                          # every second sample is outside the port tets
        return out
    monkeypatch.setattr(Nedelec2, "interpolate_index", plain)
    EP.apply_port_field_lookup()
    x = np.zeros(4)
    got = Nedelec2.interpolate_index(None, x, x, x, tetids=np.array([0, 1]), usenan=False)
    assert got.tolist() == [0, 7, 2, 7]
    assert calls[1] is None and len(calls) == 2


def test_no_second_lookup_when_every_sample_is_found(monkeypatch):
    from emerge._emerge.elements.nedelec2 import Nedelec2
    from hertzon.solver import emerge_patches as EP

    calls = []

    def plain(self, xs, ys, zs, tetids=None, usenan=True):
        calls.append(tetids)
        return np.arange(xs.size, dtype=np.int64)
    monkeypatch.setattr(Nedelec2, "interpolate_index", plain)
    EP.apply_port_field_lookup()
    x = np.zeros(3)
    assert Nedelec2.interpolate_index(None, x, x, x, tetids=np.array([0])).tolist() == [0, 1, 2]
    assert len(calls) == 1


def test_line_on_sheet():
    lo, hi = (0, 0, 0), (1e-3, 2e-3, 0)                 # flat box of a sheet in the xy plane
    assert line_on_sheet((0.5e-3, 0, 0), (0.5e-3, 2e-3, 0), lo, hi)
    assert line_on_sheet((0.5e-3, 0, 1e-5), (0.5e-3, 2e-3, 1e-5), lo, hi)     # slightly off the sheet: margin
    assert not line_on_sheet((5e-3, 0, 0), (5e-3, 2e-3, 0), lo, hi)
