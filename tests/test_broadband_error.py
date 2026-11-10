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
