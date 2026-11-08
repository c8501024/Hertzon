"""Final comparison plot and table: HFSS on Meta's STEP, HFSS on the Hertzon geometry, Hertzon (one- and two-sided sheets)."""
import csv, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
rd=lambda p:np.array(list(csv.reader(open(p)))[1:],float)
a=rd('hfss/out/Efficiency.csv'); b=rd('hfss/out_prim/Efficiency.csv')
e=np.loadtxt('results/A22_eff.csv',delimiter=',',skiprows=1); t=np.loadtxt('results/A22_2side_eff.csv',delimiter=',',skiprows=1)
f=e[:,0]
np.savetxt('results/Hertzon_vs_HFSS.csv',np.c_[f,e[:,1],t[:,1],a[:,1],b[:,1],e[:,2],a[:,2],b[:,2]],delimiter=',',fmt='%.4f',comments='',
           header='f_GHz,total_Hertzon,total_Hertzon_2sided_sheets,total_HFSS_MetaSTEP,total_HFSS_HertzonGeometry,accepted_Hertzon,accepted_HFSS_MetaSTEP,accepted_HFSS_HertzonGeometry')
s=np.array(list(csv.reader(open('hfss/out/S11.csv')))[1:],float); s2=np.array(list(csv.reader(open('hfss/out_prim/S11.csv')))[1:],float)
import sys; sys.path.insert(0,'tools')
from metric import read
Fh,Sh=read('results/A22.s1p')
fig,ax=plt.subplots(2,1,figsize=(8.5,6.5),sharex=True)
ax[0].plot(Fh/1e9,20*np.log10(abs(Sh)),label='Hertzon'); ax[0].plot(s[:,0],20*np.log10(abs(s[:,1]+1j*s[:,2])),label="HFSS, Meta's STEP"); ax[0].plot(s2[:,0],20*np.log10(abs(s2[:,1]+1j*s2[:,2])),'--',label='HFSS, Hertzon geometry')
ax[0].set_ylabel('S11 (dB)'); ax[0].set_ylim(-30,0); ax[0].grid(alpha=.3); ax[0].legend()
for x in ax:
    x.axvspan(2.401,2.483,color='g',alpha=.12); x.axvspan(5.17,7.125,color='g',alpha=.12)
ax[1].plot(f,e[:,1]*100,'o-',label='Hertzon'); ax[1].plot(f,t[:,1]*100,'^-',label='Hertzon, two-sided sheets'); ax[1].plot(f,a[:,1]*100,'s-',label="HFSS, Meta's STEP"); ax[1].plot(f,b[:,1]*100,'d--',label='HFSS, Hertzon geometry')
ax[1].set_xlabel('Frequency (GHz)'); ax[1].set_ylabel('Total efficiency (%)'); ax[1].set_ylim(0,100); ax[1].set_xlim(2,8); ax[1].grid(alpha=.3); ax[1].legend()
plt.tight_layout(); plt.savefig('results/Ex2_Run2_hertzon_vs_hfss.png',dpi=160)
