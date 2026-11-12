# Combined plot of the Run 7 designs: python plot_all.py <outfile.png>
import sys, os, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
here = os.path.dirname(os.path.abspath(__file__))
def load(d, wide=None):
    r = os.path.join(here, '..', 'Meta_Ex2_RunB.hznresults', d, 'S1'); rw = os.path.join(here, '..', 'Meta_Ex2_RunB.hznresults', wide or d, 'S1')
    w = np.load(os.path.join(rw, 'sparams_Wide.npz'), allow_pickle=True); fw = w['freq'] / 1e9; s = w['S'][:, 0, 0].copy()
    ff = np.load(os.path.join(r, 'farfield_FF_Eff.npz'), allow_pickle=True); f = []; t = []; i = 0
    while 'freq_%d' % i in ff.files:
        f.append(float(ff['freq_%d' % i][0]) / 1e9); t.append(100 * float(ff['Prad_%d_1' % i][0])); i += 1
    return fw, s, np.array(f), np.array(t)
runs = [('Final', None, '#8a8f98', 'Final: loop left, arm right (40 % target)', 1.6, '-'),
        ('B1', None, '#e8a33b', 'B1: arm left, wide arm', 1.6, '-'),
        ('B2', None, '#3a9a5c', 'B2: arm left, 1.2 mm more edge', 1.6, '-'),
        ('B3_final', 'B3', '#1f6fd1', 'B3: B2 retuned, 343k tetrahedra', 2.6, '-')]
fig = plt.figure(figsize=(12, 8.2)); gs = fig.add_gridspec(2, 2, width_ratios=[1, 2.6], hspace=0.28, wspace=0.08)
a_s = fig.add_subplot(gs[0, :]); a_l = fig.add_subplot(gs[1, 0]); a_h = fig.add_subplot(gs[1, 1], sharey=a_l)
for a in (a_s, a_l, a_h):
    a.grid(color='#dddddd')
    for sp in ('top', 'right'): a.spines[sp].set_visible(False)
for lo, hi in ((2.401, 2.483), (5.17, 7.125)): a_s.axvspan(lo, hi, color='#f1efe9', zorder=0)
a_l.axvspan(2.401, 2.483, color='#f1efe9', zorder=0); a_h.axvspan(5.17, 7.125, color='#f1efe9', zorder=0)
for d, wd, c, lab, lw, ls in runs:
    fw, s, f, t = load(d, wd)
    a_s.plot(fw, 20 * np.log10(abs(s)), color=c, lw=lw, label=lab)
    m = f < 3; a_l.plot(f[m], t[m], '-o', color=c, lw=lw, ms=3.5)
    m = f > 5; a_h.plot(f[m], t[m], '-o', color=c, lw=lw, ms=3.5, label=lab)
a_s.set_xlim(2, 8); a_s.set_ylim(-35, 0); a_s.set_ylabel('|S11| (dB)'); a_s.set_xlabel('Frequency (GHz)')
a_s.legend(frameon=False, fontsize=9, loc='lower left', bbox_to_anchor=(0.12, 0.02))
for a in (a_l, a_h):
    a.axhline(50, color='#b00020', lw=1, ls=':'); a.axhline(40, color='#b00020', lw=0.8, ls=':', alpha=0.5); a.set_ylim(0, 100); a.set_xlabel('Frequency (GHz)')
a_l.set_xlim(2.37, 2.51); a_h.set_xlim(5.1, 7.2); a_l.set_ylabel('Total efficiency (%)'); plt.setp(a_h.get_yticklabels(), visible=False)
a_l.set_title('2.401-2.483 GHz', loc='left', fontsize=10); a_h.set_title('5.17-7.125 GHz', loc='left', fontsize=10)
a_h.text(6.0, 51.5, '50 %', color='#b00020', fontsize=8); a_h.text(6.0, 41.5, '40 %', color='#b00020', fontsize=8, alpha=0.7)
fig.suptitle('Meta Example 2, Run 7: S11 and total efficiency of all designs (Hertzon 0.3.18, free space, no matching)',
             x=0.01, ha='left', fontsize=11.5, fontweight='bold')
fig.savefig(sys.argv[1], dpi=160, bbox_inches='tight')
