"""Far field quantities (gain, directivity, RCS, antenna parameters) from the stored far field data.

Plain numpy, shared by the report windows and the MCP server."""
from __future__ import annotations

import numpy as np

ETA0 = 376.730313668


def monostatic_rcs(st, label: str, sphere: str, design=None) -> dict[str, np.ndarray]:
    """monostatic RCS (m^2) over frequency from the backscatter far field stored by the solver."""
    from .farfield_post import farfield
    ff = farfield(st, design, sphere, label) if design is not None else st.npz(f"farfield_{sphere}_{label}.npz")
    if ff is None or "mono_dir" not in ff:
        raise RuntimeError("No monostatic far field for this solution. Assign an incident plane wave, add an "
                           "infinite sphere and analyze again.")
    th, ph = [float(x) for x in ff["mono_dir"]]
    E0 = float(np.ravel(ff.get("E0", [1.0]))[0]) or 1.0
    ks = sorted(int(k.split("_")[1]) for k in ff if k.startswith("Emono_"))
    f = np.array([float(ff[f"freq_{k}"][0]) for k in ks])
    E = np.array([ff[f"Emono_{k}"] for k in ks])
    t_hat = np.array([np.cos(th) * np.cos(ph), np.cos(th) * np.sin(ph), -np.sin(th)])
    p_hat = np.array([-np.sin(ph), np.cos(ph), 0.0])
    et, ep = E @ t_hat, E @ p_hat
    o = np.argsort(f)
    rt, rp = 4 * np.pi * np.abs(et) ** 2 / E0 ** 2, 4 * np.pi * np.abs(ep) ** 2 / E0 ** 2
    return {"freq": f[o], "MonostaticRCSTheta": rt[o], "MonostaticRCSPhi": rp[o],
            "MonostaticRCSTotal": (rt + rp)[o]}


RCS_PASSES = "rcs_passes.json"


def record_rcs_pass(st, design, step: int) -> None:
    """Store the monostatic RCS of adaptive pass `step` (far field of 'LastAdaptive' just saved), so it can
    be plotted over the passes."""
    data = st.read_json(RCS_PASSES, {}) or {}
    for sph in design.spheres:
        try:
            d = monostatic_rcs(st, "LastAdaptive", sph.name, design)
        except Exception:  # noqa: BLE001
            continue
        rows = [r for r in data.get(sph.name, []) if r.get("pass", 0) < step]     # a new run restarts
        rows.append({"pass": int(step), **{k: [float(x) for x in v] for k, v in d.items()}})
        data[sph.name] = rows
    st.write_json(RCS_PASSES, data)


def rcs_pass_series(st, sphere: str, k: int = 0) -> dict[str, np.ndarray] | None:
    """Monostatic RCS of every adaptive pass at adaptive frequency index k ("freq" holds the pass numbers)."""
    rows = [r for r in (st.read_json(RCS_PASSES, {}) or {}).get(sphere) or [] if r.get("freq")]
    if not rows:
        return None
    out = {"freq": np.array([r["pass"] for r in rows], float), "xaxis": "Pass",
           "adaptive_freq": float(rows[-1]["freq"][min(k, len(rows[-1]["freq"]) - 1)])}
    for n in ("MonostaticRCSTotal", "MonostaticRCSTheta", "MonostaticRCSPhi"):
        out[n] = np.array([r[n][min(k, len(r[n]) - 1)] for r in rows], float)
    return out


def farfield_quantities(ff: dict, port: int = 1, k: int = 0, s11: complex | None = None, pin: float = 1.0,
                        pacc: float | None = None) -> dict[str, np.ndarray]:
    """Far field quantities over the sphere grid (n_theta, n_phi). Gain, directivity and realized gain are
    linear power ratios (a half-wave dipole: DirTotal 1.64 = 2.15 dBi), rE* field magnitudes in V; see
    docs/RESULT_FILES.md."""
    E = ff[f"E_{k}_{port}"]                # (3, T, P) cartesian
    th, ph = ff["theta"], ff["phi"]
    T, P = np.meshgrid(th, ph, indexing="ij")
    st, ct, sp, cp = np.sin(T), np.cos(T), np.sin(P), np.cos(P)
    Eth = E[0] * ct * cp + E[1] * ct * sp - E[2] * st
    Eph = -E[0] * sp + E[1] * cp
    U = (np.abs(Eth) ** 2 + np.abs(Eph) ** 2) / (2 * ETA0)
    Prad_solver = float(ff.get(f"Prad_{k}_{port}", np.array([0]))[0])
    full = len(th) > 2 and len(ph) > 2 and th.min() <= 1e-6 and th.max() >= np.pi - 1e-6 and \
        (ph.max() - ph.min()) >= 2 * np.pi - 1e-3
    if full:
        Uint = float(np.trapezoid(np.trapezoid(U * st, ph, axis=1), th))
        D = 4 * np.pi * U / max(Uint, 1e-300)
    else:
        D = 4 * np.pi * U / max(Prad_solver, 1e-300)
    Prad = Prad_solver if Prad_solver > 0 else 1.0
    Pin = pin                                   # incident power (1 W at one port unless Edit Sources says else)
    Pacc = pacc if pacc is not None else (Pin * (1 - abs(s11) ** 2) if s11 is not None else Prad)
    eff = min(1.0, Prad / max(Pacc, 1e-30)) if Prad_solver > 0 else 1.0
    Dth = D * np.abs(Eth) ** 2 / np.maximum(np.abs(Eth) ** 2 + np.abs(Eph) ** 2, 1e-300)
    out = {
        "DirTotal": D,
        "GainTotal": D * eff,
        "RealizedGainTotal": D * eff * Pacc / Pin,
        "GainTheta": Dth * eff,
        "GainPhi": (D - Dth) * eff,
        "rETotal": np.sqrt(np.abs(Eth) ** 2 + np.abs(Eph) ** 2),
        "rETheta": np.abs(Eth),
        "rEPhi": np.abs(Eph),
    }
    # axial ratio from circular components
    El = (Eth + 1j * Eph) / np.sqrt(2)
    Er = (Eth - 1j * Eph) / np.sqrt(2)
    a, b = np.abs(El), np.abs(Er)
    out["AxialRatioValue"] = (a + b) / np.maximum(np.abs(a - b), 1e-12)
    tot = np.maximum(np.abs(Eth) ** 2 + np.abs(Eph) ** 2, 1e-300)
    out["DirLHCP"] = D * a ** 2 / tot
    out["DirRHCP"] = D * b ** 2 / tot
    out["GainLHCP"] = out["DirLHCP"] * eff
    out["GainRHCP"] = out["DirRHCP"] * eff
    out["rELHCP"] = a
    out["rERHCP"] = b
    # bistatic RCS (m^2) of an incident plane wave: 4 pi |rE_s|^2 / |E0|^2
    E0 = float(np.ravel(ff.get("E0", [1.0]))[0]) or 1.0
    out["BistaticRCSTheta"] = 4 * np.pi * np.abs(Eth) ** 2 / E0 ** 2
    out["BistaticRCSPhi"] = 4 * np.pi * np.abs(Eph) ** 2 / E0 ** 2
    out["BistaticRCSTotal"] = out["BistaticRCSTheta"] + out["BistaticRCSPhi"]
    out["_eff"] = np.array([eff])
    out["_Pacc"] = np.array([Pacc])
    out["_Pinc"] = np.array([Pin])
    out["_U"] = U
    out["_theta"] = th
    out["_phi"] = ph
    out["_Prad"] = np.array([Prad])
    return out


def antenna_parameters(q: dict) -> list[tuple[str, str]]:
    """'Antenna Parameters' table from far field quantities (1 W incident at port 1)."""
    th, ph = q["_theta"], q["_phi"]
    D, G, RG = q["DirTotal"], q["GainTotal"], q["RealizedGainTotal"]
    k = np.unravel_index(int(np.argmax(D)), D.shape)
    db = lambda v: 10 * np.log10(max(float(v), 1e-300))  # noqa: E731
    rows = [("Peak Directivity", f"{float(D.max()):.4g}  ({db(D.max()):.3f} dBi)"),
            ("Peak Gain", f"{float(G.max()):.4g}  ({db(G.max()):.3f} dBi)"),
            ("Peak Realized Gain", f"{float(RG.max()):.4g}  ({db(RG.max()):.3f} dBi)"),
            ("Max U", f"{float(q['_U'].max()):.4g} W/sr"),
            ("Theta / Phi of max", f"{np.degrees(th[k[0]]):.1f} deg / {np.degrees(ph[k[1]]):.1f} deg"),
            ("Radiated Power", f"{float(q['_Prad'][0]):.4g} W"),
            ("Accepted Power", f"{float(q['_Pacc'][0]):.4g} W"),
            ("Incident Power", "1 W"),
            ("Radiation Efficiency", f"{float(q['_eff'][0]):.4g}")]
    # front to back: direction of max versus the opposite direction
    t0, p0 = th[k[0]], ph[k[1]]
    tb = np.pi - t0
    pb = (p0 + np.pi) % (2 * np.pi)
    ib = int(np.argmin(np.abs(th - tb)))
    jb = int(np.argmin(np.abs(((ph - pb + np.pi) % (2 * np.pi)) - np.pi)))
    if abs(th[ib] - tb) < 0.1 and abs(((ph[jb] - pb + np.pi) % (2 * np.pi)) - np.pi) < 0.1:
        fb = float(D[k]) / max(float(D[ib, jb]), 1e-300)
        rows.append(("Front to Back Ratio", f"{fb:.4g}  ({db(fb):.2f} dB)"))
    else:
        rows.append(("Front to Back Ratio", "back direction not on the sphere"))
    ar = q["AxialRatioValue"][k]
    rows.append(("Axial Ratio at max", f"{float(ar):.4g}  ({20 * np.log10(max(float(ar), 1e-300)):.2f} dB)"))
    rows.append(("LHCP / RHCP gain at max", f"{db(q['GainLHCP'][k]):.2f} / {db(q['GainRHCP'][k]):.2f} dBi"))
    return rows


# antenna parameters as numbers (report quantities). Power-like ones take 10 log10 in dB(),
# AxialRatioAtMax 20 log10 (like dB(AxialRatioValue)); angles are in degrees.
ANTENNA_QUANTITIES = ["PeakDirectivity", "PeakGain", "PeakRealizedGain", "MaxU", "RadiatedPower",
                      "AcceptedPower", "IncidentPower", "RadiationEfficiency", "FrontToBackRatio",
                      "ThetaAtMax", "PhiAtMax", "AxialRatioAtMax", "GainRHCPAtMax", "GainLHCPAtMax"]
ANTENNA_POWER = {"PeakDirectivity", "PeakGain", "PeakRealizedGain", "MaxU", "RadiatedPower", "AcceptedPower",
                 "IncidentPower", "RadiationEfficiency", "FrontToBackRatio", "GainRHCPAtMax", "GainLHCPAtMax"}
ANTENNA_PASSES = "antenna_passes.json"


def antenna_values(q: dict) -> dict[str, float]:
    """The antenna parameters of one far field (see antenna_parameters) as plain numbers."""
    th, ph = q["_theta"], q["_phi"]
    D = q["DirTotal"]
    k = np.unravel_index(int(np.argmax(D)), D.shape)
    t0, p0 = th[k[0]], ph[k[1]]
    tb, pb = np.pi - t0, (p0 + np.pi) % (2 * np.pi)
    ib = int(np.argmin(np.abs(th - tb)))
    jb = int(np.argmin(np.abs(((ph - pb + np.pi) % (2 * np.pi)) - np.pi)))
    fb = float(D[k]) / max(float(D[ib, jb]), 1e-300) \
        if abs(th[ib] - tb) < 0.1 and abs(((ph[jb] - pb + np.pi) % (2 * np.pi)) - np.pi) < 0.1 else float("nan")
    return {"PeakDirectivity": float(D.max()), "PeakGain": float(q["GainTotal"].max()),
            "PeakRealizedGain": float(q["RealizedGainTotal"].max()), "MaxU": float(q["_U"].max()),
            "RadiatedPower": float(q["_Prad"][0]), "AcceptedPower": float(q["_Pacc"][0]),
            "IncidentPower": float(q.get("_Pinc", [1.0])[0]),
            "RadiationEfficiency": float(q["_eff"][0]), "FrontToBackRatio": fb,
            "ThetaAtMax": float(np.degrees(t0)), "PhiAtMax": float(np.degrees(p0)),
            "AxialRatioAtMax": float(q["AxialRatioValue"][k]), "GainRHCPAtMax": float(q["GainRHCP"][k]),
            "GainLHCPAtMax": float(q["GainLHCP"][k])}


def excitation_weights(design, ports: list) -> dict[int, complex] | None:
    """Port number -> complex amplitude sqrt(P) e^(j phase) from Edit Sources; None while the sources
    were never edited (then port 1 is excited with 1 W)."""
    from ..model import sources as SRC
    names = [p["name"] for p in ports] if any(p.get("kind") == "Terminal" for p in ports) else None
    srcs = SRC.get(design, names) if design is not None else None
    if srcs is None or any(p.get("kind") == "Incident Plane Wave" for p in ports):
        return None
    w = {}
    for p in ports:
        s = srcs.get(p.get("name"))
        if s and s["mag"] > 0:
            w[int(p["number"])] = np.sqrt(s["mag"]) * np.exp(1j * np.radians(s["phase"]))
    if not w:
        raise RuntimeError("Edit Sources: every port has 0 W, so nothing radiates")
    return w


def excited_quantities(st, design, ff: dict, label: str, k: int, port: int | None = None) -> dict:
    """Far field quantities of the excitation set in Edit Sources: the far fields of the ports superposed
    with their amplitudes. Incident power = sum of the port powers, accepted power = a^H (I - S^H S) a,
    radiated power from the superposed fields on the radiation surface. port: one port with 1 W instead."""
    ports = st.read_json("field_sources.json", None) or st.read_json("ports.json", []) or []
    f = float(ff[f"freq_{k}"][0])
    sp = st.sparams(label)
    S = None
    if sp is not None and len(sp["freq"]):
        i = int(np.argmin(np.abs(np.asarray(sp["freq"], float) - f)))
        if abs(float(sp["freq"][i]) - f) <= 1e-6 * max(f, 1.0):
            S = np.asarray(sp["S"][i])
    w = None if port is not None else excitation_weights(design, ports)
    if w is None:
        n = port if port is not None else _first_port(ff)
        idx = next((j for j, p in enumerate(ports) if int(p.get("number", 0)) == n), 0)
        if S is None or idx >= S.shape[0]:
            return farfield_quantities(ff, n, k, None)
        # accepted = incident - everything leaving through the ports (reflection and coupling)
        return farfield_quantities(ff, n, k, None, pacc=float(1.0 - np.sum(np.abs(S[:, idx]) ** 2)))
    nums = [int(p["number"]) for p in ports]
    a = np.array([w.get(n, 0.0) for n in nums], complex)
    E = sum(w[n] * ff[f"E_{k}_{n}"] for n in w if f"E_{k}_{n}" in ff)
    pin = float(np.sum(np.abs(a) ** 2))
    pacc = float(np.real(pin - np.linalg.norm(S @ a) ** 2)) if S is not None and S.shape[0] == len(a) else None
    prad = 0.0
    huy = st.npz(f"huygens_{label}.npz") if len(w) > 1 else None
    if huy is not None and all(f"E_{k}_{n}" in huy for n in w):
        Eh = sum(w[n] * huy[f"E_{k}_{n}"] for n in w)
        Hh = sum(w[n] * huy[f"H_{k}_{n}"] for n in w)
        prad = float(np.real(np.sum(0.5 * np.cross(Eh.T, np.conj(Hh).T).T * huy["wns"])))
    else:            # one port, or no surface fields: the port powers add up
        prad = float(sum(abs(w[n]) ** 2 * float(ff.get(f"Prad_{k}_{n}", [0.0])[0]) for n in w))
    one = {key: ff[key] for key in ("theta", "phi", "E0") if key in ff}
    one.update({f"E_{k}_0": E, f"Prad_{k}_0": np.array([prad]), f"freq_{k}": ff[f"freq_{k}"]})
    return farfield_quantities(one, 0, k, None, pin=pin, pacc=pacc)


def _first_port(ff) -> int:
    ports = sorted({int(k.split("_")[2]) for k in ff if k.startswith("E_") and k.count("_") == 2})
    return ports[0] if ports else 1


def antenna_series(st, design, sphere: str, label: str, ev=None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Antenna parameters over the frequencies of a solution with far fields (excitation of Edit Sources)."""
    from .farfield_post import farfield
    ff = farfield(st, design, sphere, label, ev)
    if ff is None:
        raise RuntimeError(f"No far field data for '{label}': the solution has no radiation/PML boundary "
                           "or no fields were saved for it")
    port = _first_port(ff)
    ks = sorted({int(k.split("_")[1]) for k in ff if k.startswith("E_") and k.endswith(f"_{port}")})
    f = np.array([float(ff[f"freq_{k}"][0]) for k in ks])
    rows = [antenna_values(excited_quantities(st, design, ff, label, k)) for k in ks]
    o = np.argsort(f)
    return f[o], {n: np.array([rows[i][n] for i in o], float) for n in ANTENNA_QUANTITIES}


def record_antenna_pass(st, design, ev, step: int) -> None:
    """Store the antenna parameters of adaptive pass `step` (far fields and S-parameters of 'LastAdaptive'
    just saved), so they can be plotted over the passes like the S-parameters."""
    from .farfield_post import farfield
    data = st.read_json(ANTENNA_PASSES, {}) or {}
    for sph in design.spheres:
        ff = farfield(st, design, sph.name, "LastAdaptive", ev)
        if ff is None:
            continue
        port = _first_port(ff)
        ks = sorted({int(k.split("_")[1]) for k in ff if k.startswith("E_") and k.endswith(f"_{port}")})
        vals = [antenna_values(excited_quantities(st, design, ff, "LastAdaptive", k)) for k in ks]
        rows = [r for r in data.get(sph.name, []) if r.get("pass", 0) < step]     # a new run restarts
        rows.append({"pass": int(step), "freq": [float(ff[f"freq_{k}"][0]) for k in ks], "values": vals})
        data[sph.name] = rows
    st.write_json(ANTENNA_PASSES, data)


def antenna_pass_series(st, sphere: str, k: int = 0) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """Antenna parameters of every adaptive pass at adaptive frequency index k."""
    rows = (st.read_json(ANTENNA_PASSES, {}) or {}).get(sphere) or []
    rows = [r for r in rows if r.get("values")]
    if not rows:
        return None
    x = np.array([r["pass"] for r in rows], float)
    pick = [r["values"][min(k, len(r["values"]) - 1)] for r in rows]
    return x, {n: np.array([float(v.get(n, np.nan)) for v in pick], float) for n in ANTENNA_QUANTITIES}
