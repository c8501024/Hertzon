"""Band metric from an s1p: mean mismatch efficiency 1-|S11|^2 in 2401-2483 and 5170-7125 MHz."""
import sys, re, numpy as np
def read(f):
    F,S=[],[]; unit=1e9; fmt='MA'; z0=50
    for l in open(f):
        s=l.strip()
        if not s or s[0]=='!': continue
        if s[0]=='#':
            t=s[1:].upper().split(); unit={'HZ':1,'KHZ':1e3,'MHZ':1e6,'GHZ':1e9}[t[0]]; fmt=t[2]; z0=float(t[4]) if len(t)>4 else 50; continue
        a=[float(x) for x in s.split()]; F.append(a[0]*unit)
        if fmt=='DB': S.append(10**(a[1]/20)*np.exp(1j*np.deg2rad(a[2])))
        elif fmt=='MA': S.append(a[1]*np.exp(1j*np.deg2rad(a[2])))
        else: S.append(a[1]+1j*a[2])
    return np.array(F),np.array(S)
def bands(F,S):
    me=1-abs(S)**2
    lb=(F>=2.401e9)&(F<=2.483e9); hb=(F>=5.17e9)&(F<=7.125e9)
    return me[lb].mean(),me[hb].mean()
if __name__=='__main__':
    F,S=read(sys.argv[1]); l,h=bands(F,S)
    print(f"LB mean me={l:.3f}  HB mean me={h:.3f}  score={(l+h)/2:.3f}")
    print('S11dB @',' '.join(f"{f/1e9:.2f}:{20*np.log10(abs(S[np.argmin(abs(F-f))])):.1f}" for f in np.array([2.0,2.4,2.45,2.5,3,3.5,4,5.17,5.5,6,6.5,7.125,8])*1e9))
