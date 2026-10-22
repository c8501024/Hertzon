import sys, time, json, numpy as np, emerge as em
import hz_model, fetd
fetd.fix_robin_quadrature()
f1, f2, nf = 1.5e9, 3.5e9, 41
freqs = np.linspace(f1, f2, nf)
ppw = int(sys.argv[2]) if len(sys.argv) > 2 else 40
with em.Simulation("patch", save_file=False) as m:
    hz_model.build(m, sys.argv[1], adaptive="--adaptive" in sys.argv)
    ntets = m.mesh.n_tets
    m.mw.set_frequency(list(freqs))
    t = time.time(); dfd = m.mw.run_sweep(); tfd = time.time() - t
    Sfd = np.array(dfd.scalar.grid.S(1, 1))
    t = time.time()
    res, ids, info, meta = fetd.run(m, f1, f2, freqs, ppw=ppw)
    tsolve = time.time() - t
    dtd = fetd.inject(m, freqs, res, ids)
    ttd = time.time() - t
    Std = np.array(dtd.scalar.grid.S(1, 1))
print("RESULT tets", ntets, "dofs", info["n"], "FD", round(tfd, 1), "TD", round(ttd, 1), "TDsolve", round(tsolve, 1), "dtau", meta["dtau"])
for f, a, b in zip(freqs/1e9, Sfd, Std):
    print(f"RESULT {f:5.2f}  S11 fd {20*np.log10(abs(a)):7.2f}<{np.angle(a,1):7.1f} td {20*np.log10(abs(b)):7.2f}<{np.angle(b,1):7.1f}")
print("RESULT max |dS| ", np.abs(Sfd - Std).max())
np.savez(f"patch_ppw{ppw}.npz", f=freqs, Sfd=Sfd, Std=Std, sig=np.array(meta["signals"][0]), dtau=meta["dtau"])
