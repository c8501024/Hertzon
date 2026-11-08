"""Band total efficiency from Hertzon's stored far-field results: Prad at each Eff point (1 W incident)."""
import sys, numpy as np
sys.path.insert(0,'tools')
from metric import read
base=sys.argv[1]
d=np.load(base+'/farfield_FF_Eff.npz')
F,S=read(base+'/Eff.s1p')
n=len([k for k in d.files if k.startswith('freq_')])
f=np.array([float(d[f'freq_{i}'][0])/1e9 for i in range(n)]); p=np.array([float(d[f'Prad_{i}_1'][0]) for i in range(n)])
a=1-abs(S)**2
if len(sys.argv)>2:
    np.savetxt(sys.argv[2],np.c_[f,p,a],delimiter=',',header='f_GHz,Prad_over_Pinc,Pacc_over_Pinc',comments='',fmt='%.4f')
for name,lo,hi in (('LB',2.4,2.49),('HB',5.1,7.2)):
    m=(f>lo)&(f<hi)
    print(name,'total',np.round(p[m],3),'radeff',np.round(p[m]/a[m],2),'mean',round(p[m].mean(),3),'trapz',round(np.trapezoid(p[m],f[m])/(f[m][-1]-f[m][0]),3))
