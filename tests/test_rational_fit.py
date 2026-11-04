"""Vector fitting model of the interpolating sweep (results.fit_sparams): order selection."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

C0 = 299792458.0


def _guide(f, a=22.86e-3, L=40e-3):
    """Two-mode waveguide-like S: through transmission e^-jbL of TE10 and TE01, smooth over the band."""
    out = np.zeros((len(f), 2, 2), complex)
    for i, fc in enumerate((C0 / (2 * a), C0 / (2 * 15e-3))):
        beta = 2 * np.pi * np.sqrt(f ** 2 - fc ** 2) / C0
        out[:, i, i] = 0.9 * np.exp(-1j * beta * L)
    out[:, 0, 1] = out[:, 1, 0] = 0.1 * np.exp(-1j * 2 * np.pi * f * 1.1e-10)
    return out


def _warnings():
    from emerge._emerge.physics.microwave import adaptive_freq  # noqa: F401  (EMerge's import resets loguru)
    from loguru import logger
    seen = []
    hid = logger.add(lambda m: seen.append(m.record["message"]), level="WARNING")
    return seen, lambda: logger.remove(hid)


def test_smooth_response_gets_few_poles_and_no_ill_conditioned_fit():
    """Many samples of a smooth response: a pole count from the number of samples left the surplus poles far
    off the band, the residue system singular (EMerge's "Ill Conditioned Matrix" warning, condition 1e17+)."""
    from hertzon.solver.results import fit_sparams
    ff = np.linspace(10.3e9, 11.8e9, 41)
    seen, done = _warnings()
    try:
        model = fit_sparams(ff, _guide(ff))
    finally:
        done()
    assert not [m for m in seen if "Ill Conditioned" in m]
    fm = (ff[1:] + ff[:-1]) / 2
    assert np.abs(model(fm) - _guide(fm)).max() < 1e-4


def test_long_line_still_gets_the_poles_its_phase_turns_need():
    from hertzon.solver.results import _rational_fit
    f = np.linspace(1e9, 3e9, 101)
    y = 0.9 * np.exp(-2j * np.pi * f * 8e-9)                  # 16 phase turns over the band
    fm = (f[1:] + f[:-1]) / 2
    assert np.abs(_rational_fit(f, y)(fm) - 0.9 * np.exp(-2j * np.pi * fm * 8e-9)).max() < 1e-3


def test_resonance_is_fitted_with_its_own_pole_pair():
    from hertzon.solver.results import _rational_fit
    f = np.linspace(2e9, 2.4e9, 15)
    x = 300 * (f / 2.2e9 - 2.2e9 / f)
    y = 0.5j * x / (1 + 1j * x)
    fm = np.linspace(2e9, 2.4e9, 401)
    xm = 300 * (fm / 2.2e9 - 2.2e9 / fm)
    assert np.abs(_rational_fit(f, y)(fm) - 0.5j * xm / (1 + 1j * xm)).max() < 1e-6
