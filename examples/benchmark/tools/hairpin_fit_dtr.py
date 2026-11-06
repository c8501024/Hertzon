"""Hairpin filter: start values from the prototype, a few fit correction steps, then the derivative trust region.

python hairpin_fit_dtr.py WORKDIR [--passes N] [--skip-dtr]
PYTHONPATH must point at the Hertzon source.

1. the filter assistant's design (Chebyshev start values, N fixed adaptive passes: the same mesh size every solve)
2. fit correction: every step one full analysis; the coupled resonator circuit (centre frequencies of the outer
   and the inner resonators, coupling k, external Q, unloaded Q) is fitted to |S11| and |S21|, and L, dL, g, yP
   are corrected towards the prototype's f0, k and Qe (Broyden update of the sensitivities)
3. the HairpinDTR optimization (L, dL, g, yP) from the corrected values
Times, values and the passband figures go to WORKDIR/summary.json.
"""
import json, subprocess, sys, time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from hertzon.model.design import Project
from hertzon.model.filters import HairpinSpec, hairpin_design

W = Path(sys.argv[1]).resolve()
W.mkdir(parents=True, exist_ok=True)
PY = sys.executable
PASSES = int(sys.argv[sys.argv.index("--passes") + 1]) if "--passes" in sys.argv else 7
SUM = W / "summary.json"
summary = {"passes": PASSES, "steps": []}


def save():
    SUM.write_text(json.dumps(summary, indent=1, default=str))


spec = HairpinSpec(n=3)
design, nom = hairpin_design(spec)
st = design.setups[0]
st.props.update(MaximumDeltaS="0.0001", MaximumPasses=str(PASSES), MinimumConvergedPasses="2")
proto = nom["prototype"]
TGT = np.array([spec.f0, spec.f0, max(proto["k"]), proto["Qe"]])
NAMES = ("L", "dL", "g", "yP")
summary["target"] = dict(zip(("f_outer", "f_inner", "k", "Qe"), TGT.tolist()))


def project(tag, p):
    pr = Project(f"Hairpin_{tag}")
    design.variables.update({n: f"{v:.4f}mm" for n, v in zip(NAMES, p)})
    pr.designs = [design]
    path = W / f"{tag}.hzn"
    pr.save(str(path))
    return path


def circuit(x, f):
    fo, fi, k, qe, qu = x
    s11, s21 = [], []
    for fk in f:
        wo, wi = fk / fo - fo / fk, fk / fi - fi / fk
        a = np.array([[1 / qe + 1 / qu + 1j * wo, -1j * k, 0], [-1j * k, 1 / qu + 1j * wi, -1j * k],
                      [0, -1j * k, 1 / qe + 1 / qu + 1j * wo]])
        ai = np.linalg.inv(a)
        s21.append(2 / qe * ai[2, 0])
        s11.append(1 - 2 / qe * ai[0, 0])
    return np.array(s11), np.array(s21)


def evaluate(p, tag):
    t = time.time()
    path = project(tag, p)
    with open(W / f"{tag}.log", "w", encoding="utf-8") as log:
        subprocess.run([PY, "-m", "hertzon.solver.run", str(path), design.name, st.name, str(W / tag)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    z = np.load(W / tag / "sparams_Sweep.npz")
    f = z["freq"] / 1e9
    S = z["S"].reshape(len(f), 2, 2)

    def res(x):
        s11, s21 = circuit(x, f)
        return np.concatenate([np.abs(s21) - np.abs(S[:, 1, 0]), np.abs(s11) - np.abs(S[:, 0, 0])])
    best = None
    for k0 in (0.05, 0.1):
        for q0 in (6, 12):
            r = least_squares(res, [spec.f0, spec.f0, k0, q0, 100], bounds=([0.5 * spec.f0] * 2 + [0.005, 1, 10],
                                                                         [1.5 * spec.f0] * 2 + [0.5, 100, 2000]))
            best = r if best is None or r.cost < best.cost else best
    m = (f >= spec.passband[0]) & (f <= spec.passband[1])
    db = lambda v: float(20 * np.log10(max(v, 1e-12)))  # noqa: E731
    row = {"tag": tag, **dict(zip(NAMES, map(float, p))),
           **dict(zip(("f_outer", "f_inner", "k", "Qe", "Qu"), map(float, best.x))),
           "fit_rms": float(np.sqrt(best.cost / len(f))), "S11_max_dB": db(np.abs(S[m, 0, 0]).max()),
           "S21_min_dB": db(np.abs(S[m, 1, 0]).min()), "seconds": round(time.time() - t, 1)}
    summary["steps"].append(row)
    save()
    print(row, flush=True)
    return best.x[:4]


t0 = time.time()
p = np.array([float(design.variables[n].replace("mm", "")) for n in NAMES])
# start sensitivities (GHz/mm, 1/mm): length -> both centre frequencies, dL -> outer ones, gap -> k, tap -> Qe
J = np.array([[-0.065, -0.065, 0, 0], [-0.065, 0, 0, 0], [0, 0, -0.19, 0], [0, 0, 0, -5.3]])
y = evaluate(p, "fit0")
TOL = np.array([0.003, 0.003, 0.02, 0.02])     # relative: the fit itself scatters by a few MHz on a fixed mesh
err = lambda v: float(np.max(np.abs(v - TGT) / TGT / TOL))  # noqa: E731
best_e = err(y)
for it in range(1, 6):
    if best_e < 1:
        break
    dp = np.linalg.solve(J, TGT - y)
    p2 = p + dp
    y2 = evaluate(p2, f"fit{it}")
    J += np.outer((y2 - y) - J @ dp, dp) / (dp @ dp)
    p, y = p2, y2
    if err(y) < best_e:
        best_e = err(y)
# the circuit does not describe the field solution exactly (fit rms ~0.05 in |S|): the DTR starts from the step
# with the best measured passband match, not from the one closest to the circuit targets
p = np.array([min(summary["steps"], key=lambda r: r["S11_max_dB"])[n] for n in NAMES])
summary["fit"] = {"values": dict(zip(NAMES, p.tolist())), "seconds": round(time.time() - t0, 1)}
save()

if "--skip-dtr" not in sys.argv:
    path = project("opt", p)
    t = time.time()
    with open(W / "opt.log", "w", encoding="utf-8") as log:
        rc = subprocess.run([PY, "-m", "hertzon.solver.optimize", str(path), design.name, "HairpinDTR",
                             str(W / "opt")], stdout=log, stderr=subprocess.STDOUT).returncode
    sm = W / "opt" / "summary.json"
    summary["dtr"] = {"rc": rc, "seconds": round(time.time() - t, 1),
                      "summary": json.loads(sm.read_text()) if sm.exists() else None}
    summary["total_seconds"] = round(time.time() - t0, 1)
    save()
print("FERTIG")
(W / "FERTIG.txt").write_text("FERTIG")
