"""Temporary (removed before the PR): time the fast sweep factorization of a saved matrix with one backend.
python micro.py A.npz BACKEND   (BACKEND: SuperLU, Accelerate, AccelerateLoop, SPARTA)
python micro.py A.npz all       (each backend in its own process, with a time limit) -> JSON lines"""
import json, os, platform, resource, subprocess, sys, time
import numpy as np
import scipy.sparse as sp

def one(path, backend):
    A = sp.load_npz(path).tocsr()
    n = A.shape[0]
    rng = np.random.default_rng(1)
    B = rng.standard_normal((n, 32)) + 1j * rng.standard_normal((n, 32))
    os.environ["HERTZON_FAST_FACTOR"] = {"AccelerateLoop": "Accelerate"}.get(backend, backend)
    from hertzon.solver.fast_sweep import _Factor
    t = time.time(); f = _Factor(A); tf = time.time() - t
    def solve(X):
        if backend == "AccelerateLoop":    # the earlier attempt: one Accelerate call per column
            return np.column_stack([np.asarray(f._acc.solve(np.ascontiguousarray(X[:, i]))[0]) for i in range(X.shape[1])])
        return f.solve(X)
    solve(B[:, :1])                        # first call (workspaces)
    t = time.time()
    for i in range(5):
        x1 = solve(B[:, i:i + 1])
    t1 = (time.time() - t) / 5
    t = time.time(); x2 = solve(B[:, :2]); t2 = time.time() - t
    t = time.time(); x8 = solve(B[:, :8]); t8 = time.time() - t
    t = time.time(); x32 = solve(B); t32 = time.time() - t
    res = float(np.max(np.linalg.norm(A @ x32 - B, axis=0) / np.linalg.norm(B, axis=0)))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**30 if sys.platform == "darwin" else 2**20)
    extra = {}
    try:
        from emerge_aasds import _interface as I
        extra = dict(cs_ldlt=I.COMPLEX_SYMMETRIC_AVAILABLE, mtmetis=I.MTMETIS_AVAILABLE)
    except Exception:
        pass
    return dict(matrix=os.path.basename(path), backend=backend, kind=f.kind, n=n, nnz=A.nnz, factor_s=round(tf, 3),
                solve1_s=round(t1, 4), solve2_s=round(t2, 4), solve8_s=round(t8, 4), solve32_s=round(t32, 4),
                resid=float(f"{res:.2e}"), peak_gb=round(rss, 2), macos=platform.mac_ver()[0], **extra)

if __name__ == "__main__":
    path, backend = sys.argv[1:3]
    if backend != "all":
        print("RESULT " + json.dumps(one(path, backend)), flush=True)
        sys.exit(0)
    for b in ["SuperLU", "Accelerate", "AccelerateLoop", "SPARTA"]:
        try:
            r = subprocess.run([sys.executable, __file__, path, b], capture_output=True, text=True, timeout=900)
            lines = [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
            print(lines[-1][7:] if lines else json.dumps(dict(matrix=path, backend=b, rc=r.returncode,
                                                             err=(r.stderr or r.stdout)[-600:])), flush=True)
        except subprocess.TimeoutExpired:
            print(json.dumps(dict(matrix=path, backend=b, timeout=900)), flush=True)
