"""Broadband / Multi-Frequencies: every adaptive frequency refines its own worst tetrahedra. Before, the
plain maximum let the highest frequency (larger error everywhere in a plain waveguide) choose all of them, and
the mesh equalled the one of a single solution at the highest frequency."""
import numpy as np

from hertzon.solver.bridge import combine_frequency_errors, finite_error
from hertzon.solver.emerge_compat import select_refinement_indices


def test_single_frequency_is_unchanged():
    e = np.array([0.3, 0.1, 0.7])
    assert np.array_equal(combine_frequency_errors(e[:, None]), e)
    assert np.array_equal(combine_frequency_errors(e), e)


def test_low_frequency_gets_its_own_tetrahedra():
    hi = np.array([10.0, 9.0, 8.0, 7.0, 6.0, 5.0])     # high frequency: larger error everywhere
    lo = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 2.0])      # low frequency: worst in the last tetrahedron
    assert np.argmax(np.maximum(hi, lo)) == 0 and np.maximum(hi, lo)[5] == 5.0
    comb = combine_frequency_errors(np.stack([lo, hi], axis=1))
    assert comb[5] == comb[0] == 10.0                  # both worst tetrahedra rank first, scale of the largest
    top2 = set(np.argsort(comb)[-2:])
    assert top2 == {0, 5}


def test_nan_stays_for_finite_error_and_zero_column_is_kept():
    e = np.array([[1.0, 0.0], [np.nan, 0.0], [0.5, 0.0]])
    comb = combine_frequency_errors(e)
    assert np.isnan(comb[1]) and comb[0] == 1.0 and comb[2] == 0.5
    out = finite_error(comb)
    assert out is not None and out[1] == 0.0
    assert len(select_refinement_indices(out, 0.5)) > 0


# ---- adaptive frequencies and convergence limits -------------------------------------------------------
from hertzon.model import schema  # noqa: E402
from hertzon.model.design import Project, Setup  # noqa: E402
from hertzon.model.validate import validate  # noqa: E402
from hertzon.solver import bridge as B  # noqa: E402


def _setup(**props):
    p = Project()
    d = p.designs[0]
    st = Setup("Setup1", props=schema.defaults(schema.SETUP))
    st.props.update(props)
    d.setups.append(st)
    return p, d, st


def test_broadband_adapts_low_middle_high_by_default():
    p, d, st = _setup(SolutionFrequencyMode="Broadband", LowFrequency="8GHz", HighFrequency="12GHz")
    assert B.adaptive_frequencies(st, d.evaluator(p)) == [8e9, 10e9, 12e9]
    st.props.pop("BroadbandFrequencies")                    # projects saved before the setting: 3 as well
    assert B.adaptive_frequencies(st, d.evaluator(p)) == [8e9, 10e9, 12e9]
    st.props["BroadbandFrequencies"] = "5"
    assert B.adaptive_frequencies(st, d.evaluator(p)) == [8e9, 9e9, 10e9, 11e9, 12e9]
    st.props["BroadbandFrequencies"] = "1"                  # at least the two edges
    assert B.adaptive_frequencies(st, d.evaluator(p)) == [8e9, 12e9]
    assert B.adaptive_delta_s(st, d.evaluator(p)) == [0.02, 0.02]


def test_multi_frequencies_with_own_max_delta_s():
    p, d, st = _setup(SolutionFrequencyMode="Multi-Frequencies", MaximumDeltaS="0.02",
                      MultiFrequencies="12GHz:0.001; 8GHz, 10GHz:0.005")
    ev = d.evaluator(p)
    assert B.adaptive_frequencies(st, ev) == [8e9, 10e9, 12e9]
    assert B.adaptive_delta_s(st, ev) == [0.02, 0.005, 0.001]          # in the order of the frequencies
    assert schema.frequency_list(st.props["MultiFrequencies"]) == ["12GHz", "8GHz", "10GHz"]
    assert not [c for c in validate(p, d) if c.category == "Analysis Setup" and c.level == "error"]
    st.props["MultiFrequencies"] = "8GHz:abc, 12GHz:-1"
    errs = [c.message for c in validate(p, d) if c.category == "Analysis Setup" and c.level == "error"]
    assert len(errs) == 2 and all("Max Delta S must be a number > 0" in e for e in errs)
