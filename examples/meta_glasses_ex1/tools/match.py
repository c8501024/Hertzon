"""Matching-network search for a 1-port antenna (s1p), lossy 0201-like L/C.
Usage: python match.py file.s1p eta_lb eta_hb [maxN]
eta_lb/eta_hb: radiation efficiency of the bare antenna in each band (from Hertzon far field).
Element models: L with Q_L (at 2.4 GHz, Q ~ sqrt(f)), C with Q_C. Ladder from the source side.
Objective: 0.5*mean(total eff in 2401-2483) + 0.5*mean(total eff in 5170-7125).
"""
import sys, itertools, warnings, numpy as np
warnings.filterwarnings("ignore")
from scipy.optimize import minimize
sys.path.insert(0, __file__.rsplit('\\', 1)[0])
from s1p import read

QL0, QC = 30.0, 150.0
Z0 = 50.0

def zl(L, f):  # L in nH, f in GHz
    w = 2*np.pi*f*1e9; X = w*L*1e-9; Q = QL0*np.sqrt(f/2.4)
    return X/Q + 1j*X
def zc(C, f):  # C in pF
    w = 2*np.pi*f*1e9; X = -1/(w*C*1e-12)
    return abs(X)/QC + 1j*X

def deliver(topo, vals, f, Za):
    # cascade ABCD, first element at the source side
    A = np.ones_like(f, complex); B = np.zeros_like(f, complex)
    C = np.zeros_like(f, complex); D = np.ones_like(f, complex)
    for (pos, kind), v in zip(topo, vals):
        z = zl(v, f) if kind == 'L' else zc(v, f)
        if pos == 'ser':
            a, b, c, d = 1, z, 0, 1
        else:
            a, b, c, d = 1, 0, 1/z, 1
        A, B, C, D = A*a + B*c, A*b + B*d, C*a + D*c, C*b + D*d
    # network ABCD (source side first): V1 = A V2 + B I2 ; I2 into antenna, V2 = Za I2
    I2 = 1/(A*Za + B + Z0*(C*Za + D))  # V source = 1 V (source with Z0)
    Pant = 0.5*abs(I2)**2*Za.real
    Pav = 1/(8*Z0)
    return Pant/Pav

def score(topo, vals, f, Za, eta, lb, hb):
    e = deliver(topo, vals, f, Za)*eta
    return 0.5*e[lb].mean() + 0.5*e[hb].mean(), e

def main():
    fn = sys.argv[1]; eta_lb = float(sys.argv[2]); eta_hb = float(sys.argv[3])
    maxN = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    f, s = read(fn)
    Za = Z0*(1+s)/(1-s)
    lb = (f >= 2.401) & (f <= 2.483); hb = (f >= 5.17) & (f <= 7.125)
    eta = np.where(f < 4, eta_lb, eta_hb)
    s0, e0 = score([], [], f, Za, eta, lb, hb)
    print(f"no network: score {s0*100:.1f}%  LB {e0[lb].mean()*100:.1f}  HB {e0[hb].mean()*100:.1f}")
    elems = [(p, k) for p in ('ser', 'sh') for k in ('L', 'C')]
    best = []
    rng = np.random.default_rng(1)
    for N in range(1, maxN+1):
        for topo in itertools.product(elems, repeat=N):
            if any(topo[i][0] == topo[i+1][0] and topo[i][1] == topo[i+1][1] for i in range(N-1)):
                continue
            def obj(x):
                return -score(topo, np.exp(np.clip(x, np.log(0.1), np.log(30))), f, Za, eta, lb, hb)[0]
            bx, bv = None, 1e9
            for _ in range(6):
                x0 = rng.uniform(np.log(0.2), np.log(15), N)
                r = minimize(obj, x0, method='Nelder-Mead', options={'maxiter': 600, 'xatol': 1e-3, 'fatol': 1e-5})
                if r.fun < bv: bv, bx = r.fun, r.x
            vals = np.clip(np.exp(bx), 0.1, 30)
            sc, e = score(topo, vals, f, Za, eta, lb, hb)
            best.append((sc, topo, vals, e[lb].mean(), e[hb].mean(), e[lb].min(), e[hb].min()))
    best.sort(key=lambda t: -t[0])
    for sc, topo, vals, ml, mh, nl, nh in best[:8]:
        desc = ' -> '.join(f"{p} {k}={v:.2f}{'nH' if k=='L' else 'pF'}" for (p, k), v in zip(topo, vals))
        print(f"{sc*100:5.1f}%  LB {ml*100:5.1f} (min {nl*100:4.1f})  HB {mh*100:5.1f} (min {nh*100:4.1f})  src: {desc}")

if __name__ == '__main__':
    main()
