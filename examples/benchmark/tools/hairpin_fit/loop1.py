import subprocess, sys, numpy as np, json
PY = "/tmp/claude-0/venv/bin/python"
F0, QE = 2.45, 10.3238
def run(L, yP, tag):
    subprocess.run([PY, "mk1.py", f"{tag}.hzn", f"{L:.4f}mm", f"{yP:.4f}mm"], check=True, capture_output=True)
    subprocess.run([PY, "-m", "hertzon.solver.run", f"{tag}.hzn", "Tap1", "Setup1", tag], check=True, capture_output=True)
    z = np.load(f"{tag}/sparams_Sweep.npz"); f = z["freq"]; s = z["S"].reshape(len(f), -1)[:, 0]
    w = 2*np.pi*f; tau = -np.gradient(np.unwrap(np.angle(s)), w); i = int(np.argmax(tau))
    a, b, c = tau[i-1:i+2]; di = 0.5*(a-c)/(a-2*b+c); f0 = f[i] + di*(f[1]-f[0]); t0 = b - 0.25*(a-c)*di
    i0 = int(np.argmin(np.abs(f - f0))); r = abs(s[i0]); x = (1 - r)/(1 + r)   # Qe/Qu, overcoupled
    qe = 2*np.pi*f0*t0/4 * (1 - x*x)
    print(tag, f"L {L:.4f} yP {yP:.4f} -> f0 {f0/1e9:.5f} Qe {qe:.3f} |S11| {r:.3f}", flush=True)
    return np.array([f0/1e9, qe])
p = np.array([36.6324, -5.5636]); y = run(*p, "a0")
J = np.zeros((2, 2))
for j, h in enumerate((0.5, 0.5)):
    q = p.copy(); q[j] += h; J[:, j] = (run(*q, f"d{j}") - y) / h
tgt = np.array([F0, QE])
for it in range(1, 7):
    dp = np.linalg.solve(J, tgt - y); p2 = p + dp; y2 = run(*p2, f"n{it}")
    J += np.outer((y2 - y) - J @ dp, dp) / (dp @ dp)   # Broyden
    p, y = p2, y2
    if abs(y[0] - F0) < 0.002 and abs(y[1] - QE) < 0.05: break
print("RESULT", p.tolist(), y.tolist(), J.tolist())
