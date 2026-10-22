"""Hertzon example PatchAntenna.hzn rebuilt in openEMS (EC-FDTD), S11 and run time."""
import os, sys, time, tempfile
import numpy as np
from CSXCAD import ContinuousStructure
from CSXCAD.SmoothMeshLines import SmoothMeshLines
from openEMS import openEMS
from openEMS.physical_constants import C0, EPS0

res_div = float(sys.argv[1]) if len(sys.argv) > 1 else 20     # cells per wavelength at f_max (in the medium)
endc = float(sys.argv[2]) if len(sys.argv) > 2 else 1e-5
unit = 1e-3
h, Wp, Lp, Ws, Ls, wf, inset, gap = 1.6, 37.3, 28.5, 60.0, 60.0, 3.0, 8.0, 1.0
er, tand = 4.4, 0.02
f0, fc = 2.5e9, 1.0e9
fmax = f0 + fc
lam_min = C0 / fmax / unit
air = C0 / 1.5e9 / unit / 4                                     # lambda/4 at the lowest frequency

fdtd = openEMS(EndCriteria=endc)
fdtd.SetGaussExcite(f0, fc)
fdtd.SetBoundaryCond(["PML_8"] * 6)
csx = ContinuousStructure()
fdtd.SetCSX(csx)
mesh = csx.GetGrid()
mesh.SetDeltaUnit(unit)

sub = csx.AddMaterial("FR4", epsilon=er, kappa=2 * np.pi * 2.45e9 * EPS0 * er * tand)
sub.AddBox([-Ws / 2, -Ls / 2, 0], [Ws / 2, Ls / 2, h], priority=0)
pec = csx.AddMetal("PEC")
pec.AddBox([-Ws / 2, -Ls / 2, 0], [Ws / 2, Ls / 2, 0], priority=10)                    # ground
y0, y1, yn = -Lp / 2, Lp / 2, -Lp / 2 + inset
xn = wf / 2 + gap
for b in ([-Wp / 2, y0, h], [-xn, y1, h]), ([xn, y0, h], [Wp / 2, y1, h]), ([-xn, yn, h], [xn, y1, h]), \
         ([-wf / 2, -Ls / 2, h], [wf / 2, yn, h]):
    pec.AddBox(b[0], b[1], priority=10)
port = fdtd.AddLumpedPort(1, 50, [-wf / 2, -Ls / 2, 0], [wf / 2, -Ls / 2, h], "z", excite=1.0, priority=5)

# mesh: thirds rule at metal edges (1/3 inside, 2/3 outside), smoothing with growth 1.3,
# board resolution lambda_min/(sqrt(er) res_div), air lambda_min/res_div
d_sub = lam_min / np.sqrt(er) / res_div
d_air = lam_min / res_div
d = min(d_sub, gap)


def edge(e, metal_below):
    return [e - d / 3, e + 2 * d / 3] if metal_below else [e + d / 3, e - 2 * d / 3]


xl = [-Ws / 2, Ws / 2, 0.0]
for x, inside_below in ((Wp / 2, True), (xn, False), (wf / 2, True)):
    xl += edge(x, inside_below) + [-v for v in edge(x, inside_below)]
yl = [-Ls / 2, Ls / 2] + edge(y0, False) + edge(y1, True) + edge(yn, False)
zl = list(np.linspace(0, h, 5))


def lines(fixed, lo, hi):
    inner = SmoothMeshLines(sorted(set(np.round(fixed, 6))), d_sub, 1.3)
    return SmoothMeshLines(np.r_[lo, inner, hi].tolist(), d_air, 1.3)


mesh.AddLine("x", lines(xl, -Ws / 2 - air, Ws / 2 + air))
mesh.AddLine("y", lines(yl, -Ls / 2 - air / 4, Ls / 2 + air))
mesh.AddLine("z", lines(zl, -air, h + air))
nx, ny, nz = (len(mesh.GetLines(a)) for a in "xyz")
print(f"cells {nx}x{ny}x{nz} = {nx * ny * nz / 1e3:.0f}k", flush=True)

sim = tempfile.mkdtemp()
t = time.time()
fdtd.Run(sim, verbose=0, cleanup=True, numThreads=4)
trun = time.time() - t
f = np.linspace(1.5e9, 3.5e9, 41)
port.CalcPort(sim, f)
s11 = port.uf_ref / port.uf_inc
print(f"RESULT res_div {res_div} cells {nx * ny * nz} run {trun:.1f}s")
np.save(f"oems_res{int(res_div)}.npy", np.vstack([f, s11]))
for fi, s in zip(f / 1e9, s11):
    print(f"RESULT {fi:5.2f} {20 * np.log10(abs(s)):7.2f} {np.angle(s, 1):7.1f}")
