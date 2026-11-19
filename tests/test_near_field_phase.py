"""Near Field Line: instantaneous value at a phase, envelope over all phases, skin depth fit."""
import numpy as np
import pytest

from hertzon.gui.reports import near_values, skin_depth_fit


def _nf(c):
    return {"NearEX": c[:, 0], "NearEY": c[:, 1], "NearEZ": c[:, 2],
            "NearETotal": np.sqrt((np.abs(c) ** 2).sum(axis=1))}


def _brute_envelope(c):
    ph = np.radians(np.arange(0, 360, 0.25))
    return np.max(np.linalg.norm(np.real(c[:, None, :] * np.exp(1j * ph)[None, :, None]), axis=2), axis=1)


def test_envelope_linear_elliptic_circular():
    c = np.array([[1.0, 0, 0],                          # linear: envelope = magnitude
                  [1.0, 1j, 0],                         # circular: magnitude = sqrt(2) envelope
                  [2.0 * np.exp(0.3j), 0.5j, 0.2]])     # elliptic
    nf = _nf(c)
    env = near_values(nf, "envelope", "NearETotal")
    assert np.allclose(env, _brute_envelope(c), rtol=1e-5)
    assert env[0] == pytest.approx(1.0)
    assert env[1] == pytest.approx(1.0)
    assert near_values(nf, "mag", "NearETotal")[1] == pytest.approx(np.sqrt(2))


def test_instantaneous_value():
    c = np.array([[1.0 + 1j, 0, 0], [0, 2j, 0]])
    nf = _nf(c)
    assert near_values(nf, "inst", "NearEX", 0) == pytest.approx([1.0, 0.0])
    assert near_values(nf, "inst", "NearEX", 90) == pytest.approx([-1.0, 0.0])
    assert near_values(nf, "inst", "NearETotal", 90) == pytest.approx([1.0, 2.0])
    assert near_values(nf, "envelope", "NearEY") == pytest.approx([0.0, 2.0])
    # the envelope is the largest instantaneous value
    ph = np.arange(0, 360, 1.0)
    inst = np.max([near_values(nf, "inst", "NearETotal", p) for p in ph], axis=0)
    assert inst == pytest.approx(near_values(nf, "envelope", "NearETotal"), rel=1e-4)


def test_skin_depth_fit():
    x = np.linspace(0, 50, 401)
    delta = 3.7
    # free space with a standing wave up to x = 20, then exponential decay in the wall
    y = np.where(x < 20, 1.0 + 0.3 * np.cos(x), (1.0 + 0.3 * np.cos(20)) * np.exp(-(x - 20) / delta))
    fit = skin_depth_fit(x, y, 20, 50)
    assert fit["delta"] == pytest.approx(delta, rel=1e-3)
    assert fit["r2"] > 0.999
    assert fit["start"] == pytest.approx(20, abs=0.2)
    # the floor 60 dB down is not fitted
    y2 = np.maximum(y, 1e-7)
    assert skin_depth_fit(x, y2, 20, 50)["delta"] == pytest.approx(delta, rel=1e-3)
    # decay towards smaller x
    fit = skin_depth_fit(-x, y, -50, -20)
    assert fit["delta"] == pytest.approx(delta, rel=1e-3)
    with pytest.raises(ValueError):
        skin_depth_fit(x, np.ones_like(x))
