"""Print a compact S11 table from a Touchstone .s1p file (any format RI/MA/DB)."""
import sys, math, cmath

def read(path):
    fmt, unit, z0 = "MA", 1e9, 50.0
    rows = []
    for line in open(path):
        line = line.split("!")[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            t = line[1:].upper().split()
            unit = {"HZ": 1, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[t[0]]
            fmt = [x for x in t if x in ("MA", "RI", "DB")][0]
            continue
        v = [float(x) for x in line.split()]
        f, a, b = v[0] * unit / 1e9, v[1], v[2]
        if fmt == "RI":
            s = complex(a, b)
        elif fmt == "MA":
            s = cmath.rect(a, math.radians(b))
        else:
            s = cmath.rect(10 ** (a / 20), math.radians(b))
        rows.append((f, s))
    return rows

rows = read(sys.argv[1])
step = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
last = -1
# local minima
mins = [rows[i] for i in range(1, len(rows) - 1)
        if abs(rows[i][1]) < abs(rows[i - 1][1]) and abs(rows[i][1]) <= abs(rows[i + 1][1])]
print("minima:", ", ".join("%.3fGHz %.1fdB" % (f, 20 * math.log10(abs(s))) for f, s in mins))
for f, s in rows:
    if f - last >= step - 1e-9 or 2.39 < f < 2.5 and f - last >= 0.02 - 1e-9:
        z = 50 * (1 + s) / (1 - s)
        print("%.3f  S11 %6.2f dB  mism %.2f  Z %7.1f %+7.1fj" % (f, 20 * math.log10(abs(s)), 1 - abs(s) ** 2, z.real, z.imag))
        last = f
