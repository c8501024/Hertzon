"""A discrete sweep assembles the matrices of one batch of frequencies at a time, not of the whole sweep: EMerge's
default frequency group is every frequency, which held all 71 matrices of a 250k-unknown antenna sweep in memory
at once (14 GB, killed for lack of memory)."""
from types import SimpleNamespace as NS

import numpy as np


def _model(calls):
    entry = NS(freq=1e9, Sp=np.zeros((1, 1)), Z0=None, beta=None, _portnumbers=[1])

    class Scalar:
        n = 0

        def __getitem__(self, i):
            return entry

    data = NS(scalar=Scalar())

    def run_sweep(**kw):
        calls.append(kw)
        data.scalar.n = 1
        return data

    return NS(mw=NS(data=NS(scalar=Scalar()), set_frequency=lambda f: None, run_sweep=run_sweep,
                    bc=NS(oftype=lambda t: [])), mesh=NS(n_tets=1000))


def test_sweep_assembles_one_batch_of_frequencies_at_a_time(monkeypatch):
    from hertzon.solver import bridge
    calls = []
    m = _model(calls)
    freqs = list(np.linspace(0.3e9, 1e9, 71))
    bridge._solve_at(m, freqs)
    assert calls[-1]["frequency_groups"] == 1
    monkeypatch.setattr(bridge, "_parallel_workers", lambda m, n, want: 2)
    bridge._solve_at(m, freqs, parallel=True, n_workers=2)
    assert calls[-1]["parallel"] and calls[-1]["frequency_groups"] == 2
    bridge._solve_at(m, freqs, frequency_groups=7)            # an explicit choice (fast sweep) is kept
    assert calls[-1]["frequency_groups"] == 7
