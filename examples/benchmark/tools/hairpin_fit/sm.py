# fit based correction of L, dL, g, yP: the full filter's coupled resonator fit -> f_outer, f_inner, k, Qe
import subprocess, numpy as np, sys
sys.argv = ["x", "x"]; exec(open("fit3.py").read().split("best = None")[0].replace('z = np.load(sys.argv[1]); f = z["freq"] / 1e9; S = z["S"].reshape(len(f), 2, 2)', ''))
from scipy.optimize import least_squares
PY = "/tmp/claude-0/venv/bin/python"
TGT = np.array([2.45, 2.45, 0.10156, 10.324])
def evaluate(p, tag):
    global f, S
    L, dL, g, yP = p
    subprocess.run([PY, "mkf.py", f"{tag}.hzn", f"{L:.4f}mm", f"{g:.4f}mm", f"{yP:.4f}mm", f"{dL:.4f}mm"], check=True)
    subprocess.run([PY, "-m", "hertzon.solver.run", f"{tag}.hzn", "Full", "Setup1", tag], check=True, capture_output=True)
    z = np.load(f"{tag}/sparams_Sweep.npz"); f = z["freq"] / 1e9; S = z["S"].reshape(len(f), 2, 2)
    best = None
    for k0 in (0.07, 0.1):
        for q0 in (8, 12):
            r = least_squares(res, [2.45, 2.45, k0, q0, 100], bounds=([2, 2, 0.005, 1, 10], [3, 3, 0.5, 100, 2000]))
            if best is None or r.cost < best.cost: best = r
    m = (f >= 2.3) & (f <= 2.6)
    print(tag, "p", np.round(p, 4).tolist(), "fit fo %.4f fi %.4f k %.4f Qe %.2f Qu %.0f" % tuple(best.x),
          "| S11max %.1f dB S21min %.2f dB" % (20*np.log10(abs(S[m,0,0]).max()), 20*np.log10(abs(S[m,1,0]).min())), flush=True)
    return best.x[:4]
p = np.array([37.67, 0.0, 0.28, -5.21])
J = np.array([[-0.065, -0.065, 0, 0], [-0.065, 0, 0, 0], [0, 0, -0.193, 0], [0, 0, 0, -5.3]])
y = evaluate(p, "s0")
for it in range(1, 6):
    dp = np.linalg.solve(J, TGT - y); p2 = p + dp; y2 = evaluate(p2, f"s{it}")
    J += np.outer((y2 - y) - J @ dp, dp) / (dp @ dp); p, y = p2, y2
    if np.all(np.abs(y - TGT) / TGT < [0.001, 0.001, 0.02, 0.02]): break
print("RESULT", p.tolist())
