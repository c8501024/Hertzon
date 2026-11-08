"""Final efficiency with and without the 3-part match, at the 10 far-field frequencies; plots."""
import sys, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0,'tools')
from metric import read
from match_search import evalnet
R='Ex2_Run2.hznresults/Ex2/Setup1'
Fe,Se=read(R+'/Eff.s1p'); d=np.load(R+'/farfield_FF_Eff.npz')
f=Fe/1e9; p=np.array([float(d[f'Prad_{i}_1'][0]) for i in range(len(f))])
acc=1-abs(Se)**2; eta=p/acc; Za=50*(1+Se)/(1-Se)
topo=[('sh','C'),('ser','C'),('sh','L')]; x=[0.241,1.723,8.181]
tot,_=evalnet(topo,x,f,Za,eta)
rows=np.c_[f,eta,p,acc,tot]
np.savetxt('results/FINAL_table.csv',rows,delimiter=',',header='f_GHz,rad_eff,total_unmatched,mismatch_eff_unmatched,total_matched',comments='',fmt='%.4f')
for name,lo,hi in (('LB',2.4,2.49),('HB',5.1,7.2)):
    m=(f>lo)&(f<hi); tr=lambda a:np.trapezoid(a[m],f[m])/(f[m][-1]-f[m][0])
    print(name,'unmatched plain %.3f trapz %.3f | matched plain %.3f trapz %.3f'%(p[m].mean(),tr(p),tot[m].mean(),tr(tot)))
print(np.round(rows,3))
Fs,Ss=read('results/A22.s1p'); Fm,Sm=read('results/FINAL_matched.s1p')
fig,ax=plt.subplots(2,1,figsize=(8,6),sharex=True)
ax[0].plot(Fs/1e9,20*np.log10(abs(Ss)),label='antenna only'); ax[0].plot(Fm/1e9,20*np.log10(abs(Sm)),label='with matching (Hertzon Circuits)')
ax[0].set_ylabel('S11 (dB)'); ax[0].set_ylim(-25,0); ax[0].grid(alpha=.3); ax[0].legend()
for a in ax:
    a.axvspan(2.401,2.483,color='g',alpha=.12); a.axvspan(5.17,7.125,color='g',alpha=.12)
ax[1].plot(f,p*100,'o-',label='total eff., no matching'); ax[1].plot(f,tot*100,'s-',label='total eff., matched (lossy 0201 model)'); ax[1].plot(f,eta*100,'k:',label='radiation eff.')
ax[1].set_xlabel('Frequency (GHz)'); ax[1].set_ylabel('Efficiency (%)'); ax[1].set_ylim(0,100); ax[1].grid(alpha=.3); ax[1].legend(); ax[1].set_xlim(2,8)
plt.tight_layout(); plt.savefig('results/Ex2_Run2_final_efficiency.png',dpi=160)
