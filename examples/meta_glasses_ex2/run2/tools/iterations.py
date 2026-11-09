"""Per-iteration table and progress plot: mismatch efficiency per run, far-field totals where available."""
import sys, os, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0,'tools')
from metric import read, bands
desc={'A2':'IFA, short left of feed','A3':'+ inner return arm','A4':'+ arm around the hole','A5':'+ bottom-layer tail 5 mm','A6':'tail 3.1 mm, LB on frequency','A7':'short moved right of feed (classic IFA)','A8':'+ feed branch for high band','A9':'tail removed, shorter arm','A10':'inner arm shortened','A11':'LB arm 0.6 mm shorter','A12':'high-band arm on bottom layer','A13':'bottom arm raised','A14':'short on bottom layer, top HB arm','A15':'+ grounded parasitic','A16':'parasitic removed, HB branch moved up','A17':'LB retuned (best design)','A18':'right tip shortened','A19':'+ bottom HB arm','A20':'HB branch shortened','A21':'right branch removed','A22':'best design, 0.1 mm rule met (final)'}
rows=[]
for k in sorted(desc,key=lambda s:int(s[1:])):
    p='results/%s.s1p'%k
    if not os.path.exists(p): continue
    F,S=read(p); l,h=bands(F,S)
    e='results/%s_eff.csv'%k
    tl=th=float('nan')
    if os.path.exists(e):
        d=np.loadtxt(e,delimiter=',',skiprows=1); f,t=d[:,0],d[:,1]
        tl=t[f<3].mean(); th=t[f>3].mean()
    rows.append((k,desc[k],l,h,tl,th))
with open('results/iterations.csv','w') as fh:
    fh.write('run,change,mismatch_eff_LB,mismatch_eff_HB,total_eff_LB,total_eff_HB\n')
    for r in rows: fh.write('%s,%s,%.3f,%.3f,%.3f,%.3f\n'%r)
x=np.arange(len(rows)); fig,ax=plt.subplots(figsize=(9,3.8))
ax.plot(x,[r[2]*100 for r in rows],'o-',label='2.4 GHz, mismatch eff.'); ax.plot(x,[r[3]*100 for r in rows],'s-',label='5.17-7.125 GHz, mismatch eff.')
ax.set_xticks(x); ax.set_xticklabels([r[0] for r in rows],fontsize=8); ax.set_ylabel('%'); ax.set_ylim(0,100); ax.grid(alpha=.3); ax.legend(); plt.tight_layout(); plt.savefig('results/Ex2_Run2_progress.png',dpi=160)
for r in rows: print(r[0],r[2].__round__(3),r[3].__round__(3),'' if r[4]!=r[4] else (round(r[4],3),round(r[5],3)))
