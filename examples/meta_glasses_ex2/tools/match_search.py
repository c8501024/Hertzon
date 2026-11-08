"""Search L/C ladder matching (lossy 0201) for a 1-port s1p, maximizing mean total efficiency in both bands.
Usage: python match_search.py file.s1p eff.csv [Nmax]
Element model: L with Q 35 at 2.4 GHz rising as sqrt(f); C with Q 120. Ladder from the source; value bounds L 0.3-15 nH, C 0.1-10 pF."""
import sys, itertools, numpy as np
from scipy.optimize import differential_evolution
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metric import read
QL0, QC = 35., 120.
def zL(v,f): w=2*np.pi*f*1e9; X=w*v*1e-9; return X/(QL0*np.sqrt(f/2.4))+1j*X
def zC(v,f): w=2*np.pi*f*1e9; X=-1/(w*v*1e-12); return abs(X)/QC+1j*X
def evalnet(topo,x,f,Za,eta):
    A=np.ones_like(f,complex);B=0*A;C=0*A;D=A.copy()
    for (pos,k),v in zip(topo,x):
        z=zL(v,f) if k=='L' else zC(v,f)
        if pos=='ser': a,b,c,d=1,z,0,1
        else: a,b,c,d=1,0,1/z,1
        A,B,C,D=A*a+B*c,A*b+B*d,C*a+D*c,C*b+D*d
    # input impedance
    Zin=(A*Za+B)/(C*Za+D)
    G=(Zin-50)/(Zin+50)
    # power delivered to Za per incident power: from v,i at load
    Pin=1-abs(G)**2
    Vs=2*np.sqrt(50); 
    Iin=Vs/(50+Zin); Vin=Iin*Zin
    Iload=(A*Iin-C*Vin)/(A*D-B*C)
    Vload=Iload*Za

    # compute PL normalized: Vs rms convention: Pinc=|Vs|^2/(4*50)
    Pinc=abs(Vs)**2/(4*50)
    PLoad=np.real(Vload*np.conj(Iload))
    return eta*PLoad/Pinc, Pin
def score(topo,x,fL,zL0,etaL,fH,zH0,etaH):
    el=evalnet(topo,x,fL,zL0,etaL)[0]; eh=evalnet(topo,x,fH,zH0,etaH)[0]
    return 0.5*el.mean()+0.5*eh.mean(), el, eh
if __name__=='__main__':
    F,S=read(sys.argv[1]); Nmax=int(sys.argv[3]) if len(sys.argv)>3 else 3
    d=np.loadtxt(sys.argv[2],delimiter=',',skiprows=1); fe,re=d[:,0],d[:,1]/d[:,2]
    Z=50*(1+S)/(1-S)
    ml=(F>=2.40e9)&(F<=2.485e9); mh=(F>=5.17e9)&(F<=7.13e9)
    fL=F[ml]/1e9; fH=F[mh]/1e9; ZL=Z[ml]; ZH=Z[mh]; etaL=np.interp(fL,fe,re); etaH=np.interp(fH,fe,re)
    base=score([],[],fL,ZL,etaL,fH,ZH,etaH); print('no match',round(base[0],4),round(base[1].mean(),3),round(base[2].mean(),3))
    opts=[('ser','L'),('ser','C'),('sh','L'),('sh','C')]
    best=[]
    for N in range(1,Nmax+1):
        for topo in itertools.product(opts,repeat=N):
            b=[(0.3,15) if k=='L' else (0.1,10) for _,k in topo]
            r=differential_evolution(lambda x:-score(topo,x,fL,ZL,etaL,fH,ZH,etaH)[0],b,maxiter=120,popsize=12,tol=1e-3,seed=1,polish=False)
            best.append((-r.fun,topo,r.x))
    best.sort(key=lambda t:-t[0])
    for s,t,x in best[:6]:
        sc=score(t,x,fL,ZL,etaL,fH,ZH,etaH)
        print(round(s,4),'LB',round(sc[1].mean(),3),'HB',round(sc[2].mean(),3),[f'{p[0]}{p[1]}' for p in t],np.round(x,3))
