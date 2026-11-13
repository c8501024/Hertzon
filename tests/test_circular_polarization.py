"""Sense of circular polarization (#220): IEEE definition with the time convention e^{+jwt}. A right-hand wave
going out along r is (theta - j phi) / sqrt(2): its field turns from theta to phi, with the thumb along r."""
import numpy as np
import pytest

from hertzon.solver.ffquant import farfield_quantities


def _ff(sign):
    """A circularly polarised far field (Eth, Eph) = (1, sign * j) / sqrt(2) everywhere, as cartesian r E."""
    th = np.linspace(0, np.pi, 19)
    ph = np.linspace(0, 2 * np.pi, 37)
    T, P = np.meshgrid(th, ph, indexing="ij")
    eth, eph = np.ones_like(T) / np.sqrt(2), sign * 1j * np.ones_like(T) / np.sqrt(2)
    ct, st, cp, sp = np.cos(T), np.sin(T), np.cos(P), np.sin(P)
    E = np.array([eth * ct * cp - eph * sp, eth * ct * sp + eph * cp, -eth * st])
    return {"theta": th, "phi": ph, "E_0_1": E, "Prad_0_1": np.array([1.0]), "freq_0": np.array([1e9])}


def test_theta_minus_j_phi_is_rhcp():
    q = farfield_quantities(_ff(-1), 1, 0, None)
    assert np.allclose(q["DirLHCP"], 0, atol=1e-12)
    assert np.allclose(q["DirRHCP"], q["DirTotal"])


def test_theta_plus_j_phi_is_lhcp():
    q = farfield_quantities(_ff(+1), 1, 0, None)
    assert np.allclose(q["DirRHCP"], 0, atol=1e-12)
    assert np.allclose(q["GainLHCP"], q["GainTotal"])


def test_rotation_in_time_follows_the_right_hand():
    """Re{(x - j y) e^{jwt}} = x cos wt + y sin wt turns from x to y: right-handed about +z (theta = 0, phi = 0:
    theta = x, phi = y)."""
    t = np.linspace(0, 0.25, 5)
    v = np.real(np.outer(np.array([1, -1j]), np.exp(2j * np.pi * t)))
    assert v[1, 1] > 0 and v[0, -1] == pytest.approx(0, abs=1e-12)
    q = farfield_quantities(_ff(-1), 1, 0, None)
    assert q["DirRHCP"][0, 0] == pytest.approx(q["DirTotal"][0, 0])


def test_realized_gain_has_the_same_components_as_gain():
    """#439: RealizedGainTheta/Phi/LHCP/RHCP are the component gains times accepted / incident power, so they
    add up to RealizedGainTotal like the gains add up to GainTotal."""
    q = farfield_quantities(_ff(-1), 1, 0, None, pacc=0.75)
    r = 0.75 / 1.0
    for c in ("Theta", "Phi", "LHCP", "RHCP"):
        assert np.allclose(q[f"RealizedGain{c}"], q[f"Gain{c}"] * r)
    assert np.allclose(q["RealizedGainTheta"] + q["RealizedGainPhi"], q["RealizedGainTotal"])
    assert np.allclose(q["RealizedGainLHCP"] + q["RealizedGainRHCP"], q["RealizedGainTotal"])
    assert np.allclose(q["RealizedGainRHCP"], q["RealizedGainTotal"])     # a right-hand wave


def test_realized_gain_components_are_offered_in_reports():
    from hertzon.gui.reports import FF_CATEGORIES, FF_QUANTITIES
    for c in ("Theta", "Phi", "LHCP", "RHCP"):
        assert f"RealizedGain{c}" in FF_CATEGORIES["Realized Gain"][0]
        assert f"RealizedGain{c}" in FF_QUANTITIES
