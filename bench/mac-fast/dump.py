"""Temporary (removed before the PR): solve a design with hertzon.solver.run and save the first matrix the
fast sweep factorizes. python dump.py OUT.npz PROJECT DESIGN SETUP RESULTS_DIR"""
import runpy, sys
import scipy.sparse as sp
import hertzon.solver.fast_sweep as fs
out = sys.argv[1]
orig = fs._Factor.__init__
done = []
def init(self, A):
    if not done:
        sp.save_npz(out, A.tocsr())
        print(f"DUMPED {out} n={A.shape[0]} nnz={A.nnz}", flush=True)
        done.append(1)
    orig(self, A)
fs._Factor.__init__ = init
sys.argv = ["run"] + sys.argv[2:6]
runpy.run_module("hertzon.solver.run", run_name="__main__")
