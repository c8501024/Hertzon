import sys, numpy as np
from scipy.optimize import least_squares
z = np.load(sys.argv[1]); f = z["freq"] / 1e9; S = z["S"].reshape(len(f), 2, 2)
def model(x):
    fo, fi, k, qe, qu = x
    out11, out21 = [], []
    for fk in f:
        Wo = fk / fo - fo / fk; Wi = fk / fi - fi / fk
        A = np.array([[1/qe + 1/qu + 1j*Wo, -1j*k, 0], [-1j*k, 1/qu + 1j*Wi, -1j*k], [0, -1j*k, 1/qe + 1/qu + 1j*Wo]])
        Ai = np.linalg.inv(A)
        out21.append(2/qe*Ai[2, 0]); out11.append(1 - 2/qe*Ai[0, 0])
    return np.array(out11), np.array(out21)
def res(x):
    s11, s21 = model(x)
    return np.concatenate([np.abs(s21) - np.abs(S[:, 1, 0]), np.abs(s11) - np.abs(S[:, 0, 0])])
best = None
for k0 in (0.05, 0.1):
  for q0 in (5, 10):
    r = least_squares(res, [2.45, 2.45, k0, q0, 100], bounds=([2, 2, 0.005, 1, 10], [3, 3, 0.5, 100, 2000]))
    if best is None or r.cost < best.cost: best = r
print("f0 outer %.4f inner %.4f k %.4f Qe %.2f Qu %.0f" % tuple(best.x), "rms %.4f" % np.sqrt(best.cost / len(f)))
s11, s21 = model(best.x)
print("data S11 min dB %.1f  S21 max dB %.2f" % (20*np.log10(abs(S[:,0,0]).min()), 20*np.log10(abs(S[:,1,0]).max())))
