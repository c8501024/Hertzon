import sys, glob, numpy as np, os
def read(fn):
    f=[];s=[];unit=1e9;fmt='MA'
    for l in open(fn):
        l=l.strip()
        if not l or l.startswith('!'): continue
        if l.startswith('#'):
            t=l.upper().split(); unit={'HZ':1,'KHZ':1e3,'MHZ':1e6,'GHZ':1e9}[t[1]]; fmt=t[3]; continue
        v=[float(x) for x in l.split()[:3]]
        f.append(v[0]*unit/1e9)
        if fmt=='DB': s.append(10**(v[1]/20)*np.exp(1j*np.radians(v[2])))
        elif fmt=='MA': s.append(v[1]*np.exp(1j*np.radians(v[2])))
        else: s.append(v[1]+1j*v[2])
    return np.array(f),np.array(s)
def report(files):
  for fn in files:
    f,s=read(fn); m=1-abs(s)**2
    lb=(f>=2.401)&(f<=2.483); hb=(f>=5.17)&(f<=7.125)
    i=np.argmin(abs(s[(f>1.5)&(f<4)])); fl=f[(f>1.5)&(f<4)][i]
    print(f"{fn[-60:]:60s} res_low {fl:.3f} GHz  mismatchEff LB {m[lb].mean()*100:5.1f}% (min {m[lb].min()*100:4.1f})  HB {m[hb].mean()*100:5.1f}% (min {m[hb].min()*100:4.1f})")
if __name__=="__main__": report(sys.argv[1:])
