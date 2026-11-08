"""Filters of the design wizard (the hairpin filter has its own designer, model/filters.py)."""
from __future__ import annotations

import math

from ..filters import chebyshev_g
from ..microstrip import C0
from .base import (
    AVAILABLE,
    Field,
    Structure,
    Substrate,
    Synthesis,
    Variable,
    goal,
    new_circuit,
    substrate_part,
)
from .circuits import Schematic


def ripple_return_loss(ripple_db: float) -> float:
    """Return loss (dB) of a Chebyshev response with the passband ripple ripple_db."""
    return -10 * math.log10(1 - 10 ** (-ripple_db / 10))


class SteppedImpedanceLowpass(Structure):
    """Chebyshev lowpass of alternating low-impedance (shunt C) and high-impedance (series L) sections
    (Pozar 8.6): beta l = g Z_low / Z0 for a capacitor, g Z0 / Z_high for an inductor, at the cutoff."""
    key = "stepped"
    title = "Stepped-impedance lowpass"
    category = "filter"
    description = "Chebyshev, high and low impedance sections"
    use = "Harmonic suppression after an amplifier or oscillator"
    status = AVAILABLE
    ports = 2
    fields = (
        Field("fc", "Cutoff frequency", 2.5, "GHz", 0.05, 60),
        Field("n", "Order (sections)", 7, "", 3, 11, 0, True),
        Field("ripple", "Passband ripple", 0.1, "dB", 0.01, 1, 3),
        Field("zhi", "High impedance", 110.0, "ohm", 70, 160, 1, help="Series inductors: as high as the "
              "smallest line width allows"),
        Field("zlo", "Low impedance", 20.0, "ohm", 8, 40, 1, help="Shunt capacitors: as low as the line "
              "length allows (wide lines)"),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("fs", "Stopband from", 1.6, "x fc", 1.1, 3, 2),
        Field("rej", "Rejection in the stopband", 25.0, "dB", 5, 80, 1),
    )

    def default_name(self, spec: dict) -> str:
        return f"Lowpass_{spec['fc']:g}GHz_N{int(spec['n'])}"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        fc, n, z0 = spec["fc"], int(spec["n"]), spec["z0"]
        g = chebyshev_g(n, spec["ripple"])
        whi, wlo = sub.width(spec["zhi"], fc), sub.width(spec["zlo"], fc)
        syn = Synthesis([Variable("Whi", whi, f"high impedance width, {spec['zhi']:g} ohm", include=False),
                         Variable("Wlo", wlo, f"low impedance width, {spec['zlo']:g} ohm", include=False)])
        for k in range(1, n + 1):
            cap = k % 2 == 1                     # starts and ends with a shunt capacitor
            bl = g[k] * spec["zlo"] / z0 if cap else g[k] * z0 / spec["zhi"]
            length = bl / (2 * math.pi) * sub.lambda_g(wlo if cap else whi, fc)
            syn.variables.append(Variable(f"L{k}", length, f"section {k}: {'shunt C (low' if cap else 'series L (high'} "
                                          f"impedance), g{k} = {g[k]:.4f}"))
        wf = sub.width(z0, fc)
        syn.variables.append(Variable("Wf", wf, f"feed width, {z0:g} ohm", include=False))
        syn.variables.append(Variable("lf", max(sub.lambda_g(wf, fc) / 8, 2 * sub.h), "feed line length",
                                      include=False))
        syn.note = (f"Chebyshev {spec['ripple']:g} dB, N = {n}: g = " + ", ".join(f"{x:.4f}" for x in g[1:n + 1]) +
                    ". beta l = g Z_low/Z0 (shunt C) or g Z0/Z_high (series L) at the cutoff. The widths stay; the "
                    "optimization moves the section lengths.")
        if whi < sub.min_feature:
            syn.warnings.append(f"The {spec['zhi']:g} ohm line ({whi:.3f} mm) is narrower than the smallest width")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        n = int(spec["n"])
        d = new_circuit(name, sub, syn)
        s = Schematic()
        s.parts.append(substrate_part(sub))
        lines = [("Feed1", "Wf", "lf")] + [(f"MS{k}", "Wlo" if k % 2 else "Whi", f"L{k}") for k in range(1, n + 1)] \
            + [("Feed2", "Wf", "lf")]
        prev, x = None, 0
        for nm, w, length in lines:
            c = s.add(nm, "MLIN", x + 40, 0, 0, Subst="Sub1", W=w, L=length)
            if prev is not None:
                s.net(f"N{nm}", s.pin(prev, "right"), s.pin(c, "left"))
            prev, x = c, x + 120
        s.port(1, f"{spec['z0']:g}ohm", s.pin(s.parts[1], "left"), "left")
        s.port(2, f"{spec['z0']:g}ohm", s.pin(prev, "right"), "right")
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        fc, fs = spec["fc"], spec["fs"] * spec["fc"]
        rl = ripple_return_loss(spec["ripple"])
        return [goal("dB(S(1,1))", 0.05 * fc, 0.95 * fc, "<=", -min(rl - 1, 25)),
                goal("dB(S(2,1))", fs, 1.25 * fs, "<=", -spec["rej"])]

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.05 * spec["fc"], 1.35 * spec["fs"] * spec["fc"], 241


BW_WIDEN = 1.4


class ShortedStubBandpass(Structure):
    """Wideband bandpass of quarter-wave short-circuited stubs joined by quarter-wave lines of the port impedance
    (Pozar 8.8): Z_stub,n = pi Z0 FBW / (4 g_n). Symmetric: stub k and N+1-k share their variables."""
    key = "stubbp"
    title = "Shorted-stub bandpass"
    category = "filter"
    description = "Quarter-wave stubs with vias, wideband"
    use = "Bandwidths of 30 to 80 %"
    status = AVAILABLE
    ports = 2
    fields = (
        Field("f0", "Centre frequency", 3.0, "GHz", 0.05, 60),
        Field("bw", "Bandwidth", 50.0, "%", 20, 100, 1),
        Field("n", "Stubs", 3, "", 2, 7, 0, True),
        Field("ripple", "Passband ripple", 0.1, "dB", 0.01, 1, 3),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("rej", "Rejection", 15.0, "dB", 5, 60, 1),
        Field("guard", "Stopband distance", 25.0, "%", 2, 60, 1, help="From the passband edges to the "
              "stopbands, in % of the centre frequency"),
    )

    def default_name(self, spec: dict) -> str:
        return f"StubBandpass_{spec['f0']:g}GHz"

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        f0, n, z0 = spec["f0"], int(spec["n"]), spec["z0"]
        # the stubs are designed for a wider band: the quarter-wave connecting lines narrow the response
        # (with the factor the band edges land within a few % for 30 to 80 % bandwidth)
        fbw = min(BW_WIDEN * spec["bw"] / 100, 1.5)
        g = chebyshev_g(n, spec["ripple"])
        wc = sub.width(z0, f0)
        syn = Synthesis()
        for k in range(1, (n + 1) // 2 + 1):
            z = math.pi * z0 * fbw / (4 * g[k])
            w = sub.width(z, f0)
            # the stub reaches from the tee edge to the via (MLSC: shorted by a via of its width)
            syn.variables.append(Variable(f"Ws{k}", w, f"stub {k}{f' and {n + 1 - k}' if n + 1 - k != k else ''}: "
                                          f"width, {z:.1f} ohm"))
            syn.variables.append(Variable(f"Ls{k}", sub.lambda_g(w, f0) / 4 - wc / 2, f"stub {k}: length, lambda_g/4"))
        for k in range(1, n // 2 + 1):
            ws = [syn.value(f"Ws{min(k, n + 1 - k)}"), syn.value(f"Ws{min(k + 1, n - k)}")]
            syn.variables.append(Variable(f"Lc{k}", sub.lambda_g(wc, f0) / 4 - sum(ws) / 2,
                                          f"line {k}{f' and {n - k}' if n - k != k else ''}: length, lambda_g/4 "
                                          "between the stub centres"))
        syn.variables.append(Variable("Wc", wc, f"line width, {z0:g} ohm", include=False))
        syn.variables.append(Variable("lf", max(sub.lambda_g(wc, f0) / 8, 2 * sub.h), "feed line length",
                                      include=False))
        syn.note = (f"Chebyshev {spec['ripple']:g} dB, N = {n}, FBW = {fbw:.2f}: stubs pi Z0 FBW/(4 g) = "
                    + ", ".join(f"{math.pi * z0 * fbw / (4 * g[k]):.1f}" for k in range(1, n + 1)) +
                    f" ohm, joined by {z0:g} ohm lines; each a quarter guided wavelength. The stubs are shorted by a "
                    "via of their width.")
        if any(v.value < sub.min_feature for v in syn.variables if v.name.startswith("Ws")):
            syn.warnings.append("A stub is narrower than the smallest width: widen the bandwidth")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        from ..circuit import AUTO
        n = int(spec["n"])
        d = new_circuit(name, sub, syn)
        d.settings[AUTO] = "False"
        s = Schematic()
        s.parts.append(substrate_part(sub, -200, -220))
        sp = {"Subst": "Sub1"}
        feed1 = s.add("Feed1", "MLIN", 0, 0, 0, **sp, W="Wc", L="lf")
        prev, x = feed1, 110
        for k in range(1, n + 1):
            j = min(k, n + 1 - k)
            t = s.add(f"T{k}", "MTEE", x, 0, 0, True, **sp, W1="Wc", W2="Wc", W3=f"Ws{j}")
            st = s.add(f"Stub{k}", "MLSC", x, -70, 180, **sp, W=f"Ws{j}", L=f"Ls{j}")
            s.net(f"a{k}", s.pin(prev, "right"), s.pin(t, 0))
            s.net(f"s{k}", s.pin(t, 2), s.pin(st, 0))
            if k < n:
                c = s.add(f"MS{k}", "MLIN", x + 110, 0, 0, **sp, W="Wc", L=f"Lc{min(k, n - k)}")
                s.net(f"b{k}", s.pin(t, 1), s.pin(c, "left"))
                prev, x = c, x + 220
            else:
                feed2 = s.add("Feed2", "MLIN", x + 110, 0, 0, **sp, W="Wc", L="lf")
                s.net(f"b{k}", s.pin(t, 1), s.pin(feed2, "left"))
        s.port(1, f"{spec['z0']:g}ohm", s.pin(feed1, "left"), "left")
        s.port(2, f"{spec['z0']:g}ohm", s.pin(feed2, "right"), "right")
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        f0, fbw, gd = spec["f0"], spec["bw"] / 100, spec["guard"] / 100
        lo, hi = f0 * (1 - fbw / 2), f0 * (1 + fbw / 2)
        rl = ripple_return_loss(spec["ripple"])
        out = [goal("dB(S(1,1))", lo + 0.1 * f0 * fbw, hi - 0.1 * f0 * fbw, "<=", -min(rl - 2, 25))]
        if lo - gd * f0 > 0.1 * f0:
            out.append(goal("dB(S(2,1))", 0.1 * f0, lo - gd * f0, "<=", -spec["rej"]))
        if hi + gd * f0 < 1.9 * f0:
            out.append(goal("dB(S(2,1))", hi + gd * f0, 1.9 * f0, "<=", -spec["rej"]))
        return out

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.1 * spec["f0"], 1.9 * spec["f0"], 241


def coupled_lines(sub: Substrate, z0e: float, z0o: float, f_ghz: float) -> tuple[float, float, float]:
    """Width, gap (mm) and mean effective permittivity of symmetric coupled microstrip with the even and odd mode
    impedances z0e, z0o (the same model as the circuit, solved for w and s)."""
    from scipy.optimize import fsolve

    from ...solver.tlines import coupled_microstrip
    h, t, f = sub.h * 1e-3, sub.t * 1e-6, f_ghz * 1e9

    def modes(x):
        w, s = math.exp(x[0]), math.exp(x[1])
        return coupled_microstrip(f, w, s, h, t, sub.er, 0.0, sub.sigma)

    def err(x):
        (ze, _), (zo, _) = modes(x)
        return [math.log(ze / z0e), math.log(zo / z0o)]

    x0 = [math.log(sub.width(math.sqrt(z0e * z0o), f_ghz) * 1e-3), math.log(0.5 * h)]
    x = fsolve(err, x0, xtol=1e-10)
    (_, ge), (_, go) = modes(x)
    beta = (ge.imag + go.imag) / 2
    ee = (beta * C0 / (2 * math.pi * f)) ** 2
    return 1e3 * math.exp(x[0]), 1e3 * math.exp(x[1]), ee


class EdgeCoupledBandpass(Structure):
    """Parallel coupled half-wave resonators (Pozar 8.8): N+1 coupled sections of a quarter wavelength with
    Z0e,o = Z0 (1 +- J Z0 + (J Z0)^2). Symmetric: section k and N+2-k share their variables."""
    key = "edge"
    title = "Edge-coupled bandpass"
    category = "filter"
    description = "Parallel coupled half-wave resonators"
    use = "The textbook bandpass, 5 to 20 % bandwidth"
    status = AVAILABLE
    ports = 2
    fields = (
        Field("f0", "Centre frequency", 5.0, "GHz", 0.3, 60),
        Field("bw", "Bandwidth", 6.0, "%", 2, 30, 1),
        Field("n", "Resonators", 3, "", 2, 7, 0, True),
        Field("ripple", "Passband ripple", 0.1, "dB", 0.01, 1, 3),
        Field("z0", "Port impedance", 50.0, "ohm", 5, 300, 1),
        Field("rej", "Rejection", 20.0, "dB", 5, 80, 1),
        Field("guard", "Stopband distance", 10.0, "%", 1, 50, 1, help="From the passband edges to the "
              "stopbands, in % of the centre frequency"),
    )

    def default_name(self, spec: dict) -> str:
        return f"EdgeBandpass_{spec['f0']:g}GHz"

    @staticmethod
    def inverters(n: int, ripple: float, fbw: float) -> list[float]:
        """J Z0 of the N+1 coupled sections."""
        g = chebyshev_g(n, ripple)
        j = [math.sqrt(math.pi * fbw / (2 * g[1]))]
        j += [math.pi * fbw / (2 * math.sqrt(g[k - 1] * g[k])) for k in range(2, n + 1)]
        j.append(math.sqrt(math.pi * fbw / (2 * g[n] * g[n + 1])))
        return j

    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        f0, n, z0 = spec["f0"], int(spec["n"]), spec["z0"]
        js = self.inverters(n, spec["ripple"], spec["bw"] / 100)
        syn = Synthesis()
        zs = []
        for k in range(1, (n + 2) // 2 + 1):
            jz = js[k - 1]
            ze, zo = z0 * (1 + jz + jz * jz), z0 * (1 - jz + jz * jz)
            zs.append((ze, zo))
            w, s, ee = coupled_lines(sub, ze, zo, f0)
            # a quarter wavelength of the mean phase constant (the open ends are corrected by the 3D solves)
            lq = C0 / (f0 * 1e9) / math.sqrt(ee) * 1e3 / 4
            pair = f" and {n + 2 - k}" if n + 2 - k != k else ""
            syn.variables.append(Variable(f"W{k}", w, f"section {k}{pair}: width, Z0e/Z0o {ze:.1f}/{zo:.1f} ohm"))
            syn.variables.append(Variable(f"S{k}", s, f"section {k}: gap"))
            syn.variables.append(Variable(f"L{k}", lq, f"section {k}: length, a quarter wavelength"))
            if s < sub.min_feature:
                syn.warnings.append(f"S{k} = {s:.3f} mm is below the smallest gap: use fewer resonators, a smaller "
                                    "bandwidth or a thinner substrate")
        wf = sub.width(z0, f0)
        syn.variables.append(Variable("Wf", wf, f"feed width, {z0:g} ohm", include=False))
        syn.variables.append(Variable("lf", max(sub.lambda_g(wf, f0) / 8, 2 * sub.h), "feed line length",
                                      include=False))
        syn.note = (f"Chebyshev {spec['ripple']:g} dB, N = {n}: J Z0 = " + ", ".join(f"{j:.3f}" for j in js) +
                    "; Z0e/Z0o = " + ", ".join(f"{a:.1f}/{b:.1f}" for a, b in zs) + " ohm (the outer sections "
                    "first). Each section is a quarter wavelength at f0 of the mean of the even and odd mode; the "
                    "width steps to the feed lines are left to the 3D layout.")
        return syn

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str):
        n = int(spec["n"])
        d = new_circuit(name, sub, syn)
        s = Schematic()
        s.parts.append(substrate_part(sub, -200, -160))
        sp = {"Subst": "Sub1"}
        feed1 = s.add("Feed1", "MLIN", 0, 0, 0, **sp, W="Wf", L="lf")
        prev, x, y = s.pin(feed1, "right"), 140, 10
        for k in range(1, n + 2):
            j = min(k, n + 2 - k)
            c = s.add(f"CL{k}", "MCLIN", x, y, 0, **sp, W=f"W{j}", S=f"S{j}", L=f"L{j}")
            s.net(f"a{k}", prev, s.pin(c, 0))
            prev, x, y = s.pin(c, 3), x + 120, y + 20
        feed2 = s.add("Feed2", "MLIN", x, y - 10, 0, **sp, W="Wf", L="lf")
        s.net("out", prev, s.pin(feed2, "left"))
        s.port(1, f"{spec['z0']:g}ohm", s.pin(feed1, "left"), "left")
        s.port(2, f"{spec['z0']:g}ohm", s.pin(feed2, "right"), "right")
        s.build(d)
        return d

    def goals(self, spec: dict) -> list[dict]:
        f0, fbw, gd = spec["f0"], spec["bw"] / 100, spec["guard"] / 100
        lo, hi = f0 * (1 - fbw / 2), f0 * (1 + fbw / 2)
        rl = ripple_return_loss(spec["ripple"])
        out = [goal("dB(S(1,1))", lo + 0.1 * f0 * fbw, hi - 0.1 * f0 * fbw, "<=", -min(rl - 2, 25))]
        if lo - gd * f0 > 0.5 * f0:
            out.append(goal("dB(S(2,1))", 0.5 * f0, lo - gd * f0, "<=", -spec["rej"]))
        if hi + gd * f0 < 1.6 * f0:
            out.append(goal("dB(S(2,1))", hi + gd * f0, 1.6 * f0, "<=", -spec["rej"]))
        return out

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        return 0.5 * spec["f0"], 1.6 * spec["f0"], 241
