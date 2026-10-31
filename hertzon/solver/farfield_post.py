"""Far fields as post-processing (an Infinite Sphere can be added or edited after the solve).

The solver stores the tangential E/H on the open boundary (radiation/PML faces) once per solution in
``huygens_<solution>.npz``. Every infinite sphere is then evaluated from that file with the
Stratton-Chu integral, without the field solution or a new analysis.

With symmetry planes the far field of the full structure is the integral over the stored surface and its
mirror images. The fields are scaled to 1 W into the full structure, as EMerge's ``farfield(syms=...)``
does (after EMerge #75); the radiated power over the simulated part already is that of the full structure
at 1 W.
"""
from __future__ import annotations

import numpy as np


def stratton_chu(E: np.ndarray, H: np.ndarray, vis: np.ndarray, wns: np.ndarray,
                 th: np.ndarray, ph: np.ndarray, k0: float, progress=None) -> np.ndarray:
    """Far field E (3, N) (r*exp(jkr) normalised) radiated by the surface fields (same formula as EMerge)."""
    return stratton_chu_multi([E], [H], vis, wns, th, ph, k0, progress)[0]


def stratton_chu_multi(Es: list, Hs: list, vis: np.ndarray, wns: np.ndarray, th: np.ndarray, ph: np.ndarray,
                       k0: float, progress=None) -> list:
    """stratton_chu for several excitations at one frequency with EMerge's kernel emr.sc_multi (the phase
    factor exp(jk r.x) is computed once for all excitations), in blocks of directions for the progress bar."""
    from emr.sc_multi import stratton_chu_ff_multi
    ne = len(Es)
    Ein = np.ascontiguousarray(np.concatenate([np.asarray(E, np.complex128) for E in Es], axis=0))
    Hin = np.ascontiguousarray(np.concatenate([np.asarray(H, np.complex128) for H in Hs], axis=0))
    vis = np.ascontiguousarray(vis, np.float64)
    wns = np.ascontiguousarray(wns, np.float64)
    tp = np.ascontiguousarray([np.ravel(th), np.ravel(ph)], np.float64)
    n = tp.shape[1]
    step = n if progress is None else max(1, -(-n // 20))
    out = np.empty((3 * ne, n), np.complex128)
    for a in range(0, n, step):
        out[:, a:a + step] = stratton_chu_ff_multi(Ein, Hin, vis, wns, np.ascontiguousarray(tp[:, a:a + step]),
                                                   np.float64(k0))[0]
        if progress is not None:
            progress(min(1.0, (a + step) / n))
    return [out[3 * i:3 * i + 3] for i in range(ne)]


def symmetry_planes(huy: dict) -> list[tuple[str, int, float]]:
    """Mirror planes stored with the surface fields: (E or H, axis 0/1/2, position in m)."""
    if "sym_axis" not in huy:
        return []
    return [(str(k), int(a), float(x)) for k, a, x in zip(huy["sym_kind"], huy["sym_axis"], huy["sym_pos"])]


def mirrored(vis: np.ndarray, wns: np.ndarray, Es: list, Hs: list, planes: list) -> tuple:
    """The surface and the fields with all mirror images at the symmetry planes appended (1, 2 or 4 ... copies).
    At a Perfect E plane normal to axis a, E_a and the other two H components keep their sign, the other two E
    components and H_a flip (tangential E and normal H vanish on the plane); at a Perfect H plane the other way
    round. Same rule as EMerge's _mirrored_sources."""
    V, W, EE, HH = [vis], [wns], [list(Es)], [list(Hs)]
    for kind, ax, pos in planes:
        sE = -np.ones((3, 1))
        sE[ax] = 1.0
        sH = -sE
        if kind == "H":
            sE, sH = sH, sE
        for i in range(len(V)):
            v, w = V[i].copy(), W[i].copy()
            v[ax] = 2.0 * pos - v[ax]
            w[ax] = -w[ax]
            V.append(v)
            W.append(w)
            EE.append([E * sE for E in EE[i]])
            HH.append([H * sH for H in HH[i]])
    cat = lambda arrs: np.concatenate(arrs, axis=1)  # noqa: E731
    return (cat(V), cat(W), [cat([e[j] for e in EE]) for j in range(len(Es))],
            [cat([h[j] for h in HH]) for j in range(len(Hs))])


def sampling_text(th: np.ndarray, ph: np.ndarray) -> str:
    """The angular grid of a far field file in words, e.g. 'theta 0..180 deg step 2 (91), phi 0..360 deg step 2
    (181)': stored with the data (key "sampling"), so a file tells which sampling it holds."""
    def one(name, a):
        a = np.degrees(np.asarray(a, float))
        step = f" step {np.round(a[1] - a[0], 6):g}" if len(a) > 1 else ""
        return f"{name} {np.round(a[0], 6):g}..{np.round(a[-1], 6):g} deg{step} ({len(a)})"
    return one("theta", th) + ", " + one("phi", ph)


def compute_sphere(huy: dict, props: dict, ev, design=None, plane_wave=None, progress=None) -> dict:
    """The far field file content (farfield_<sphere>_<solution>.npz) of one infinite sphere."""
    th = np.arange(ev.angle(props["ThetaStart"]), ev.angle(props["ThetaStop"]) + 1e-9,
                   max(ev.angle(props["ThetaStep"]), 1e-3))
    ph = np.arange(ev.angle(props["PhiStart"]), ev.angle(props["PhiStop"]) + 1e-9,
                   max(ev.angle(props["PhiStep"]), 1e-3))
    res = {"theta": th, "phi": ph, "sampling": np.array([sampling_text(th, ph)])}
    R = None
    csn = props.get("CoordinateSystem", "Global") or "Global"
    if csn != "Global" and design is not None:
        from ..model import coordsys
        try:
            O, R = coordsys.frame(design, csn, ev)
            res["cs_origin"], res["cs_R"] = O, R
        except (KeyError, ValueError):
            R = None
    T, P = np.meshgrid(th, ph, indexing="ij")
    Tg, Pg = T.ravel(), P.ravel()
    if R is not None:
        rl = np.stack([np.sin(Tg) * np.cos(Pg), np.sin(Tg) * np.sin(Pg), np.cos(Tg)])
        rg = R @ rl
        Tg, Pg = np.arccos(np.clip(rg[2], -1, 1)), np.arctan2(rg[1], rg[0])
    vis, wns = huy["vis"], huy["wns"]
    planes = symmetry_planes(huy)
    scale = (0.5 ** len(planes)) ** 0.5     # 1 W into the full structure instead of into the simulated part
    if "E0" in huy:
        res["E0"] = huy["E0"]
    keys = [k for k in huy if k.startswith("E_")]
    by_freq: dict[str, list] = {}
    for key in keys:
        by_freq.setdefault(key.split("_")[1], []).append(key)
    done = 0
    for k, group in by_freq.items():
        f = float(huy[f"freq_{k}"][0])
        k0 = 2 * np.pi * f / 299792458.0
        ports = [key.split("_")[2] for key in group]
        Hs = [huy[f"H_{k}_{p}"] for p in ports]
        v2, w2, Ein, Hin = mirrored(vis, wns, [huy[key] for key in group], Hs, planes)
        Es = stratton_chu_multi(Ein, Hin, v2, w2, Tg, Pg, k0, None if progress is None else
                                (lambda x, d=done, n=len(group): progress((d + x * n) / len(keys))))
        done += len(group)
        for key, port, E in zip(group, ports, Es):
            pw = f"pw_{port}" in huy    # a plane wave keeps its amplitude; its scattered power is all images'
            if R is not None:
                E = R.T @ E
            res[key] = (E if pw else E * scale).reshape((3,) + T.shape)
            prad = huy[f"Prad_{k}_{port}"]
            res[f"Prad_{k}_{port}"] = prad * 2 ** len(planes) if pw else prad
        res[f"freq_{k}"] = np.array([f])
        if "mono_dir" in huy:
            tb, pb = [float(x) for x in huy["mono_dir"]]
            key = group[-1]
            v2, w2, Ein, Hin = mirrored(vis, wns, [huy[key]], [huy[f"H_{k}_{ports[-1]}"]], planes)
            res[f"Emono_{k}"] = stratton_chu(Ein[0], Hin[0], v2, w2, np.array([tb]), np.array([pb]), k0).reshape(3)
            res["mono_dir"] = huy["mono_dir"]
    return res


# the GUI replaces this with a runner that computes in a worker thread and shows a progress bar
RUN = lambda text, fn: fn(None)  # noqa: E731


def farfield(st, design, sphere: str, label: str = "LastAdaptive", ev=None):
    """Far field data of a sphere: computed from the stored boundary fields when the sphere is new
    or was edited after the solve, cached in farfield_<sphere>_<label>.npz."""
    import json
    sph = next((s for s in design.spheres if s.name == sphere), None)
    huy = st.npz(f"huygens_{label}.npz")
    name = f"farfield_{sphere}_{label}.npz"
    if sph is None or huy is None:
        return st.npz(name)
    sig = json.dumps({k: str(v) for k, v in sorted(sph.props.items())}) + str(int(huy["stamp"][0]))
    old = st.npz(name)
    if old is not None and "sig" in old and str(old["sig"][0]) == sig:
        return old
    if ev is None:
        ev = design.evaluator(None)
    res = RUN(f"Computing far field '{sphere}'",
              lambda prog: compute_sphere(huy, sph.props, ev, design, progress=prog))
    res["sig"] = np.array([sig])
    try:
        st.save_npz(name, **res)
    except OSError:
        pass
    return res
