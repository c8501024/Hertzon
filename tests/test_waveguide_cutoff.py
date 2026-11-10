"""Wave ports below the cutoff of their mode: the port mode decays (gamma = alpha), the adaptive frequency has to
lie above the cutoff, and an interpolating sweep across the cutoff is fitted on each side of it."""
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.solver import bridge as B  # noqa: E402

A_WR90, L = 22.86e-3, 40e-3
FC = B.C0 / (2 * A_WR90)                  # TE10 cutoff of WR-90: 6.557 GHz


def test_decaying_root_takes_the_branch_of_a_wave_leaving_the_port():
    """beta from beta^2: positive above cutoff, -j alpha below it (gamma = j beta = alpha > 0), whatever the sign
    of the zero imaginary part of beta^2."""
    b = B.decaying_root(np.array([complex(100.0, 0.0), complex(-100.0, 0.0), complex(-100.0, -0.0),
                                  (10.0 - 1.4j) ** 2]))
    assert b == pytest.approx([10.0, -10j, -10j, 10.0 - 1.4j])


def test_split_at_puts_the_frequencies_between_the_cutoffs():
    fr = np.linspace(4e9, 10e9, 13)
    bands = B.split_at(fr, [6.557e9])
    assert [len(b) for b in bands] == [6, 7]
    assert bands[0].max() < 6.557e9 < bands[1].min()
    assert [len(b) for b in B.split_at(fr, [3e9, 6.557e9])] == [6, 7]     # no empty band


def test_start_frequencies_keep_clear_of_a_cutoff():
    """A grid point just above a cutoff (13.12 GHz for the TE20 cutoff 13.11 GHz of WR-90 in an 8-40 GHz sweep
    of 201 points) is no start frequency: beta is almost zero there. The band edge moves 1 % into the band."""
    fc = 13.11e9
    fr = np.linspace(8e9, 40e9, 201)
    below, above = B.split_at(fr, [fc])
    assert B.clear_of_cutoffs(above, [fc]) == pytest.approx((1.01 * fc, 40e9))
    assert B.clear_of_cutoffs(below, [fc]) == (8e9, below.max())             # 12.96 GHz is 1.1 % below fc
    assert B.clear_of_cutoffs(np.array([13.12e9, 13.14e9]), [fc]) == (13.12e9, 13.14e9)   # no room: unchanged


def test_port_beta_decays_below_cutoff():
    """De-embedding moves the reference plane with exp(j beta d): below cutoff that is exp(alpha d)."""
    k = lambda f: 2 * np.pi * f / B.C0                                          # noqa: E731
    kc = np.pi / A_WR90
    saved = dict(B.BETA)
    B.BETA.clear()
    try:
        for f in (8e9, 10e9):
            B.BETA[f] = np.array([np.sqrt(k(f) ** 2 - kc ** 2)])
        beta = B.port_beta(np.array([5e9, 9e9]), 1)[:, 0]
    finally:
        B.BETA.clear()
        B.BETA.update(saved)
    alpha = np.sqrt(kc ** 2 - k(5e9) ** 2)
    assert beta[0] == pytest.approx(-1j * alpha, rel=1e-6)
    assert beta[1] == pytest.approx(np.sqrt(k(9e9) ** 2 - kc ** 2), rel=1e-6)


def _guide(freq, sweep=None):
    pytest.importorskip("emerge")
    from hertzon.model import schema
    from hertzon.model.design import Assignment, Project, Setup, Sweep
    p = Project()
    d = p.designs[0]
    d.add_object("CreateBox", "Guide", Position="0,0,0", XSize="22.86mm", YSize="40mm", ZSize="10.16mm",
                 material="vacuum")
    for i, y in enumerate(("0", "40mm"), 1):
        d.add_object("CreateRectangle", f"P{i}", Position=f"0,{y},0", Axis="Y", XSize="22.86mm", ZSize="10.16mm")
        props = schema.defaults(schema.EXCITATIONS["Wave Port"])
        props.update(PortNumber=str(i))
        d.excitations.append(Assignment(f"WP{i}", "Wave Port", props, objects=[f"P{i}"]))
    st = Setup("Setup1")
    st.props.update(Frequency=freq, MaximumPasses="2")
    if sweep:
        sw = Sweep("Sweep")
        sw.props.update(sweep)
        st.sweeps.append(sw)
    d.setups.append(st)
    return p, d, st


def test_adaptive_frequency_below_cutoff_is_solved_or_names_the_cutoff(tmp_path):
    """No mode propagates in WR-90 at 5 GHz. An EMerge that takes the mode shape from a higher frequency solves
    the decaying mode (S21 = exp(-alpha L)); otherwise the message names the port and the cutoff."""
    p, d, st = _guide("5GHz")
    try:
        B.Bridge(p, d, st, tmp_path).run()
    except RuntimeError as e:
        assert re.search(r"Wave port 'WP1': no mode propagates at the adaptive frequency 5 GHz, its first mode "
                         r"propagates above 6\.5[56]", str(e)), e
        return
    S = np.load(tmp_path / "sparams_LastAdaptive.npz")["S"][0]
    alpha = np.sqrt((np.pi / A_WR90) ** 2 - (2 * np.pi * 5e9 / B.C0) ** 2)
    assert 20 * np.log10(abs(S[1, 0])) == pytest.approx(-8.686 * alpha * L, abs=0.3)


def test_interpolating_sweep_across_the_cutoff_is_fitted_on_each_side(tmp_path, capfd):
    """S of a waveguide has a square-root branch point at the cutoff; the sweep is split there. Above the
    cutoff the empty guide passes everything, below it S21 = exp(-alpha L)."""
    p, d, st = _guide("10GHz", dict(SweepType="Interpolating", Distribution="Linear Count", Start="4GHz",
                                    Stop="10GHz", Count="25"))
    B.Bridge(p, d, st, tmp_path).run()
    log = capfd.readouterr().out
    assert "wave port 1, wave port 2 are cut off at 6.55" in log
    z = np.load(tmp_path / "sparams_Sweep.npz")
    f, S = z["freq"], z["S"]
    above = f > FC
    assert np.all(np.abs(np.abs(S[above, 1, 0]) - 1) < 0.01)
    if np.all(np.real(z["gamma"][~above]) > 0):      # the solver absorbs a decaying mode at both ports
        k0 = 2 * np.pi * f[~above] / B.C0
        alpha = np.sqrt((np.pi / A_WR90) ** 2 - k0 ** 2)
        assert 20 * np.log10(np.abs(S[~above, 1, 0])) == pytest.approx(-8.686 * alpha * L, abs=0.2)


def test_fit_next_to_a_cutoff_follows_the_square_root_branch():
    """S of a mode just above its cutoff goes with sqrt(f^2 - fc^2): WR-90 100 mm long, TE10 and TE20 from 1 %
    above the TE20 cutoff to 40 GHz, 60 samples. In f the TE20 entry is off by about 1; fitted in
    sqrt(f^2 - fc^2) it is a delay, while the TE10 entry stays in f (it extrapolates badly in that variable)."""
    from hertzon.solver.results import fit_sparams
    fc = B.C0 / A_WR90                                                          # TE20 cutoff, 13.11 GHz

    def S(f, m):
        k = 2 * np.pi * f / B.C0
        return np.exp(-1j * np.sqrt(k ** 2 - (m * np.pi / A_WR90) ** 2 + 0j) * 0.1)
    ff = np.linspace(1.01 * fc, 40e9, 60)
    fd = np.linspace(1.001 * fc, 40e9, 1500)
    SS = np.zeros((len(ff), 2, 2), complex)
    SS[:, 0, 0], SS[:, 1, 1] = S(ff, 1), S(ff, 2)
    plain = fit_sparams(ff, SS)(fd)
    model = fit_sparams(ff, SS, ((fc, frozenset({1})),))(fd)
    assert np.max(np.abs(plain[:, 1, 1] - S(fd, 2))) > 0.05
    assert np.max(np.abs(model[:, 1, 1] - S(fd, 2))) < 1e-3
    assert np.max(np.abs(model[:, 0, 0] - S(fd, 1))) < 1e-3


def test_band_cutoffs_are_the_neighbours_of_the_band():
    a, b, c, d = (6.5e9, "a"), (13.10e9, "b"), (13.11e9, "c"), (30e9, "d")
    assert B.band_cutoffs(np.array([14e9, 20e9]), [a, b, c, d]) == (b, c, d)     # both ports' TE20 cutoff
    assert B.band_cutoffs(np.array([8e9, 12e9]), [b]) == (b,)
    assert B.band_cutoffs(np.array([8e9, 12e9]), []) == ()


def test_cutoff_rows_are_the_rows_of_the_modes():
    pm = [{"number": 1, "kind": "Wave Port", "modes": 2}, {"number": 2, "kind": "Wave Port", "modes": 2}]
    assert B.cutoff_rows(["wave port 1 mode 2", "wave port 2 mode 2"], pm) == {1, 3}
    assert B.cutoff_rows(["wave port 2"], [{"number": 1, "modes": 1}, {"number": 2, "modes": 1}]) == {1}
