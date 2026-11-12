# Plots for the Run 7 report: python plots.py <design> <outdir>
import sys, os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

design, out = sys.argv[1], sys.argv[2]
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Meta_Ex2_RunB.hznresults', design, 'S1')
ff = np.load(os.path.join(root, 'farfield_FF_Eff.npz'), allow_pickle=True)
ls = np.load(os.path.join(root, 'losses_Eff.npz'), allow_pickle=True)
sp = np.load(os.path.join(root, 'sparams_Eff.npz'), allow_pickle=True)
f, tot, mis, rad = [], [], [], []
i = 0
while 'freq_%d' % i in ff.files:
    fr = float(ff['freq_%d' % i][0]); k = int(np.argmin(abs(sp['freq'] - fr)))
    m = 1 - abs(sp['S'][k, 0, 0]) ** 2; p = float(ff['Prad_%d_1' % i][0])
    f.append(fr / 1e9); tot.append(100 * p); mis.append(100 * m); rad.append(100 * p / m); i += 1
f, tot, mis, rad = map(np.array, (f, tot, mis, rad))
ink, grid = '#222222', '#dddddd'
c_tot, c_mis, c_rad = '#1f6fd1', '#e8743b', '#3a9a5c'

fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), gridspec_kw={'width_ratios': [1, 2.6]}, sharey=True)
for a, (lo, hi), name in zip(ax, ((2.401, 2.483), (5.17, 7.125)), ('2.401-2.483 GHz', '5.17-7.125 GHz')):
    m = (f >= lo - 0.03) & (f <= hi + 0.03)
    a.axhline(40, color='#b00020', lw=1, ls=':')
    a.axvspan(lo, hi, color='#f1efe9', zorder=0)
    a.plot(f[m], mis[m], '-', color=c_mis, lw=1.5, marker='s', ms=3, label='Mismatch efficiency')
    a.plot(f[m], rad[m], '-', color=c_rad, lw=1.5, marker='^', ms=3, label='Radiation efficiency')
    a.plot(f[m], tot[m], '-', color=c_tot, lw=2.4, marker='o', ms=4, label='Total efficiency')
    a.set_title(name, loc='left', fontsize=10, color=ink)
    a.set_xlabel('Frequency (GHz)'); a.set_ylim(0, 100); a.grid(axis='y', color=grid)
    for s in ('top', 'right'):
        a.spines[s].set_visible(False)
ax[0].set_ylabel('Efficiency (%)')
ax[1].text(5.2, 41.5, '40 % requirement', color='#b00020', fontsize=8)
ax[1].legend(frameon=False, fontsize=9, loc='lower center')
fig.suptitle('Run 7 final design: efficiency in Hertzon (free space, 50 ohm, no matching)', x=0.01, ha='left',
             fontsize=11, fontweight='bold')
fig.tight_layout(); fig.savefig(os.path.join(out, 'Run7_Total_Effizienz.png'), dpi=160); plt.close(fig)

w = np.load(os.path.join(root, 'sparams_Wide.npz'), allow_pickle=True)
fw = w['freq'] / 1e9; s = w['S'][:, 0, 0]; z = 50 * (1 + s) / (1 - s)
fig, ax = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True)
for a in ax:
    for lo, hi in ((2.401, 2.483), (5.17, 7.125)):
        a.axvspan(lo, hi, color='#f1efe9', zorder=0)
    a.grid(color=grid)
    for sp_ in ('top', 'right'):
        a.spines[sp_].set_visible(False)
ax[0].plot(fw, 20 * np.log10(abs(s)), color=c_tot, lw=2)
ax[0].set_ylabel('|S11| (dB)'); ax[0].set_ylim(-30, 0)
ax[1].plot(fw, z.real, color=c_tot, lw=2, label='R')
ax[1].plot(fw, z.imag, color=c_mis, lw=2, label='X')
ax[1].axhline(0, color=ink, lw=0.6); ax[1].set_ylim(-300, 400)
ax[1].set_ylabel('Input impedance (ohm)'); ax[1].set_xlabel('Frequency (GHz)'); ax[1].legend(frameon=False)
fig.suptitle('Run 7 final design: S11 and input impedance (interpolating sweep)', x=0.01, ha='left',
             fontsize=11, fontweight='bold')
fig.tight_layout(); fig.savefig(os.path.join(out, 'Run7_S11_Impedanz.png'), dpi=160); plt.close(fig)
print('plots written')
