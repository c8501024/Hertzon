# Compact S11 summary of a Hertzon touchstone file: python s11.py <design> [solution]
import sys, math, os

def load(path):
    f, s = [], []
    fmt, unit = 'MA', 1e9
    for ln in open(path):
        ln = ln.strip()
        if not ln or ln.startswith('!'):
            continue
        if ln.startswith('#'):
            t = ln.upper().split()
            unit = {'HZ': 1, 'KHZ': 1e3, 'MHZ': 1e6, 'GHZ': 1e9}[t[1]]
            fmt = t[3]
            continue
        v = [float(x) for x in ln.split()]
        f.append(v[0] * unit / 1e9)
        a, b = v[1], v[2]
        if fmt == 'MA':
            s.append(complex(a * math.cos(math.radians(b)), a * math.sin(math.radians(b))))
        elif fmt == 'DB':
            m = 10 ** (a / 20)
            s.append(complex(m * math.cos(math.radians(b)), m * math.sin(math.radians(b))))
        else:
            s.append(complex(a, b))
    return f, s

design = sys.argv[1]
sol = sys.argv[2] if len(sys.argv) > 2 else 'Wide'
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Meta_Ex2_RunB.hznresults', design)
setup = 'S1'
f, s = load(os.path.join(root, setup, sol + '.s1p'))
db = [20 * math.log10(max(abs(x), 1e-9)) for x in s]
z = [50 * (1 + x) / (1 - x) for x in s]
print('local S11 minima (GHz, dB, Z):')
for i in range(1, len(f) - 1):
    if db[i] < db[i - 1] and db[i] <= db[i + 1] and db[i] < -2:
        lo = i
        while lo > 0 and db[lo] < -6: lo -= 1
        hi = i
        while hi < len(f) - 1 and db[hi] < -6: hi += 1
        bw = ' | -6 dB: %.3f-%.3f (%.1f %%)' % (f[lo], f[hi], 100 * (f[hi] - f[lo]) / f[i]) if db[i] < -6 else ''
        print('  %.3f  %6.1f dB  Z=%.0f%+.0fj%s' % (f[i], db[i], z[i].real, z[i].imag, bw))
print('X=0 crossings (GHz, R; ser = dX/df>0, par = dX/df<0):')
for i in range(len(f) - 1):
    if z[i].imag * z[i + 1].imag < 0:
        print('  %.3f  R=%.0f  %s' % (f[i], z[i].real, 'ser' if z[i + 1].imag > z[i].imag else 'par'))
def band(lo, hi):
    mm = [(1 - abs(x) ** 2, fr) for fr, x in zip(f, s) if lo - 1e-6 <= fr <= hi + 1e-6]
    if mm:
        print('  %.3f-%.3f: mismatch eff mean %.1f %%, min %.1f %% at %.3f' % (
            lo, hi, 100 * sum(m for m, _ in mm) / len(mm), 100 * min(mm)[0], min(mm)[1]))
print('bands:')
band(2.401, 2.483)
band(5.17, 7.125)
if '-t' in sys.argv:
    for fr in [2.3, 2.4, 2.44, 2.48, 2.6, 3, 4, 5, 5.17, 5.4, 5.6, 5.8, 6, 6.2, 6.4, 6.6, 6.8, 7, 7.125, 7.4, 7.8]:
        i = min(range(len(f)), key=lambda k: abs(f[k] - fr))
        print('  %.3f  %6.1f dB  Z=%.0f%+.0fj' % (f[i], db[i], z[i].real, z[i].imag))
