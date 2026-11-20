"""MCP tools for post-processing on the saved fields: Fields Calculator expressions (Poynting flux through any
surface, closed or open, volume integrals, maxima, point values) and the power budget of an excitation (accepted,
radiated and lost power, the losses per object and material split into dielectric/conduction and magnetic).

The evaluation is the one of the Fields Calculator in the window (gui.calc_bridge): the same names, units and
geometry (a solid is its volume, or in flux() its closed boundary with the normal pointing out; a sheet is a
face, drawn sheets outside the model are cut from the solver mesh)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from .protocol import Tool, ToolError
from .tools import _out_dir, _r, _schema, load, tessellate


# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------
def _doc(ctx):
    """What the Fields Calculator needs of a window document: project, design, the display geometry."""
    tess, errors = tessellate(ctx.proj, ctx.des)
    return SimpleNamespace(project=ctx.proj, design=ctx.des, design_name=ctx.des.name, tess=tess,
                           evaluator=lambda: ctx.des.evaluator(ctx.proj)), errors


def _store(ctx, args):
    from ..solver.lazy_fields import frequencies
    from ..solver.results import ResultStore
    d = ctx.des
    if not ctx.proj.path:
        raise ToolError("The project is not saved yet")
    setup = args.get("setup") or (d.setups[0].name if d.setups else None)
    if setup is None:
        raise ToolError("The design has no setup")
    st = ResultStore(_out_dir(ctx, setup))
    sol = args.get("solution") or "LastAdaptive"
    fr = frequencies(st, sol)
    if not fr:
        have = sorted({p.stem.split("_", 1)[1] for pat in ("fields_*.npz", "solution_*.npz")
                       for p in st.folder.glob(pat)} - {""}) if st.folder.exists() else []
        raise ToolError(f"No saved fields for '{setup} : {sol}' (Save fields of the setup or a Discrete/Fast sweep). "
                        f"Solutions with fields: {have or 'none'}")
    return setup, sol, st, fr


def _freq_indices(args, fr: list[float], ev) -> list[int]:
    if args.get("all_frequencies"):
        return list(range(len(fr)))
    if args.get("frequency") is not None:
        f = args["frequency"]
        f = float(f) if isinstance(f, (int, float)) else ev.freq(str(f))
        return [int(np.argmin(np.abs(np.asarray(fr) - f)))]
    k = int(args.get("freq_index", 0))
    if not 0 <= k < len(fr):
        raise ToolError(f"freq_index {k} out of range: {len(fr)} frequencies with fields")
    return [k]


def _value(res) -> dict:
    from ..post.calc import units as UN
    v = res.value
    out = {"text": res.text(6), "unit": UN.label(res.unit)}
    if np.ndim(v) == 1:
        out["value"] = [{"re": _r(complex(x).real), "im": _r(complex(x).imag)} for x in v]
    else:
        c = complex(v)
        out["value"] = _r(c.real) if abs(c.imag) <= 1e-12 * max(abs(c), 1e-300) else \
            {"re": _r(c.real), "im": _r(c.imag)}
    if res.notes:
        out["notes"] = list(res.notes)
    return out


# ------------------------------------------------------------------------------------------------
# evaluate_fields
# ------------------------------------------------------------------------------------------------
def t_evaluate_fields(args):
    from ..gui import calc_bridge as CB
    from ..post.calc import lang as L
    from ..post.calc.evaluate import Evaluator
    ctx = load(args)
    exprs = args.get("expressions") or ([args["expr"]] if args.get("expr") else [])
    if not exprs:
        raise ToolError("Give expressions, e.g. ['flux(Box1, re(S))', 'int_vol(Sub, 0.5*omega*eps0*eps_r*tand*|E|^2)']")
    setup, sol, st, fr = _store(ctx, args)
    doc, _ = _doc(ctx)
    env = CB.make_env(doc)
    ev = ctx.des.evaluator(ctx.proj)
    port = int(args.get("port", 1))
    phase = float(args.get("phase_deg", 0.0))
    ks = _freq_indices(args, fr, ev)
    rows = []
    for k in ks:
        try:
            source = CB.DocFieldSource(doc, f"{setup} : {sol}", k, port, store=st)
        except L.CalcError as e:
            raise ToolError(str(e)) from None
        row = {"freq_GHz": _r(fr[k] / 1e9), "values": {}}
        evaluator = Evaluator(env, source, phase)
        for src in exprs:
            try:
                info = L.analyze(src, env)
                if info.field:
                    row["values"][src] = {"error": "a field, not a value: integrate it (int_vol, flux, int_surf), "
                                                   "take max(...) or a point value at(point(...), ...)"}
                    continue
                row["values"][src] = _value(evaluator.value(src))
            except L.CalcError as e:
                row["values"][src] = {"error": str(e)}
        rows.append(row)
    from ..model import sources as SRC
    out = {"setup": setup, "solution": sol, "port": port, "rows": rows}
    if SRC.get(ctx.des, SRC.field_names(st)) is not None:
        out["note"] = ("Edit Sources is set for this design: the fields are its superposition of the ports, as in the "
                       "field plots of the window, not port " + str(port) + " alone")
    return out


# ------------------------------------------------------------------------------------------------
# power budget
# ------------------------------------------------------------------------------------------------
def _port_source(doc, label: str, k: int, port: int, store):
    """DocFieldSource of one port alone (1 W incident), whatever Edit Sources is set to."""
    from ..gui import calc_bridge as CB
    from ..post.calc.lang import CalcError

    class PortSource(CB.DocFieldSource):
        def nodal(self, which):
            key = f"{which}_{self.fi}_{self.port}"
            if key not in self.fields:
                raise CalcError(f"No {which} field for port {self.port} at this frequency")
            return np.asarray(self.fields[key], np.complex128).reshape(len(self.tets), self.npt, 3)
    return PortSource(doc, label, k, port, store=store)


def _index_at(data, f: float):
    """Index k of freq_k in a stored npz (huygens_, losses_) at frequency f, or None."""
    if data is None:
        return None
    for key in data:
        if key.startswith("freq_") and abs(float(data[key][0]) - f) <= 1e-6 * f:
            return int(key.split("_")[1])
    return None


DIELECTRIC = "int_vol({o}, 0.5*(sigma + omega*eps0*eps_r*tand)*|E|^2)"
MAGNETIC = "int_vol({o}, 0.5*omega*mu0*mu_r*tandm*|H|^2)"


def _loss_objects(ctx):
    """Meshed objects of a lossy dielectric or magnetic material (conductors dissipate on their surface)."""
    from ..model.materials import is_conductor
    ev = ctx.des.evaluator(ctx.proj)
    out = []
    for o in ctx.des.live_objects():
        if not o.model or o.dim != 3:
            continue
        try:
            m = ctx.proj.material(o.material)
        except Exception:  # noqa: BLE001
            continue
        if is_conductor(m):
            continue

        def num(t):
            try:
                return abs(float(ev.value(t or "0")))
            except Exception:  # noqa: BLE001
                return 0.0
        if num(m.loss_tangent) or num(m.conductivity) or num(m.magnetic_loss_tangent) or \
                getattr(m, "dispersion", "") in ("Djordjevic-Sarkar", "Debye"):
            out.append((o.name, o.material))
    return out


def _surface_loss_sources(ctx) -> list[str]:
    """Parts that dissipate on a surface: finite conductors (not PEC), finite conductivity and impedance
    boundaries, lumped RLC with a resistance."""
    out = []
    for o in ctx.des.live_objects():
        if not o.model:
            continue
        try:
            m = ctx.proj.material(o.material)
        except Exception:  # noqa: BLE001
            continue
        from ..model.materials import is_conductor
        if is_conductor(m) and not m.pec:
            out.append(o.name)
    out += [b.name for b in ctx.des.boundaries
            if b.enabled and b.kind in ("Finite Conductivity", "Impedance", "Lumped RLC")]
    return out


def t_get_power_budget(args):
    from ..gui import calc_bridge as CB
    from ..post.calc import lang as L
    from ..post.calc.evaluate import Evaluator
    ctx = load(args)
    setup, sol, st, fr = _store(ctx, args)
    ev = ctx.des.evaluator(ctx.proj)
    ks = _freq_indices(args, fr, ev)
    sp = st.sparams(sol)
    pmap = st.read_json("ports.json", []) or []
    ports = [int(p["number"]) for p in pmap if p.get("kind") != "Incident Plane Wave"]
    if args.get("port") is not None:
        ports = [int(args["port"])]
    if not ports:
        raise ToolError("The solution has no port excitation (a plane wave has no accepted power)")
    huy = st.npz(f"huygens_{sol}.npz")
    loss = st.npz(f"losses_{sol}.npz")
    doc, _ = _doc(ctx)
    env = CB.make_env(doc)
    objs = _loss_objects(ctx)
    surface = _surface_loss_sources(ctx)
    rows = []
    for k in ks:
        f = fr[k]
        for port in ports:
            row = {"freq_GHz": _r(f / 1e9), "port": port, "P_incident_W": 1.0}
            if sp is not None:
                kk = int(np.argmin(np.abs(np.asarray(sp["freq"], float) - f)))
                S = np.asarray(sp["S"])[kk]
                if port - 1 < S.shape[1] and abs(float(sp["freq"][kk]) - f) <= 1e-6 * f:
                    row["P_accepted_W"] = _r(1.0 - float(np.sum(np.abs(S[:, port - 1]) ** 2)))
                    refl = float(abs(S[port - 1, port - 1]) ** 2)
                    row["P_reflected_W"] = _r(refl)
                    row["P_to_other_ports_W"] = _r(1.0 - row["P_accepted_W"] - refl)
            kh = _index_at(huy, f)
            if kh is not None and f"Prad_{kh}_{port}" in huy:
                row["P_radiated_W"] = _r(float(huy[f"Prad_{kh}_{port}"][0]))
            kl = _index_at(loss, f) if loss is not None and int(loss["ok"][0]) else None
            if kl is not None and f"Ploss_{kl}" in loss:
                pl = list(np.asarray(loss["ports"]).tolist())
                if port in pl:
                    i = pl.index(port)
                    row["P_loss_W"] = _r(max(0.0, float(np.real(loss[f"Ploss_{kl}"][i, i]))))
            # losses per object from the fields (the same field the window shows for this port alone)
            per_obj, per_mat = {}, {}
            if objs:
                try:
                    source = _port_source(doc, f"{setup} : {sol}", k, port, st)
                    E = Evaluator(env, source)
                    for name, mat in objs:
                        pd = float(np.real(E.value(DIELECTRIC.format(o=name)).value))
                        pm = float(np.real(E.value(MAGNETIC.format(o=name)).value))
                        per_obj[name] = {"material": mat, "dielectric_W": _r(pd), "magnetic_W": _r(pm)}
                        a = per_mat.setdefault(mat, {"dielectric_W": 0.0, "magnetic_W": 0.0})
                        a["dielectric_W"] += pd
                        a["magnetic_W"] += pm
                except L.CalcError as e:
                    row["volume_losses"] = f"not available: {e}"
            vol = sum(v["dielectric_W"] + v["magnetic_W"] for v in per_mat.values())
            if per_obj:
                row["volume_loss_by_object"] = per_obj
                row["volume_loss_by_material"] = {m: {k2: _r(v) for k2, v in a.items()} for m, a in per_mat.items()}
                row["P_volume_loss_W"] = _r(vol)
                if "P_loss_W" in row:
                    row["P_surface_and_lumped_loss_W"] = _r(max(0.0, row["P_loss_W"] - vol))
            if "P_loss_W" not in row and not surface and "volume_losses" not in row:
                # no far field surface, so the solver stored no total: without surface losses it is the volume sum
                row["P_loss_W"] = _r(vol)
            if {"P_accepted_W", "P_loss_W"} <= set(row) and ("P_radiated_W" in row or huy is None):
                row["balance"] = _r(row.get("P_radiated_W", 0.0) + row["P_loss_W"] - row["P_accepted_W"], 4)
            rows.append(row)
    out = {"setup": setup, "solution": sol, "rows": rows,
           "reading": "Incident 1 W at the port. P_accepted = 1 - sum |S_m,port|^2 = reflected and transmitted "
                      "power removed; P_radiated through the far field surface (radiation boundary or PML, none "
                      "for a closed model); P_loss from the solver (volume, finite conductivity, impedance and "
                      "lumped RLC). balance = P_radiated + P_loss - P_accepted (0 when converged). Volume losses "
                      "per object: dielectric/conduction 1/2 (sigma + omega eps0 eps_r tan d)|E|^2, magnetic "
                      "1/2 omega mu0 mu_r tan d_m |H|^2; the rest of P_loss is on surfaces and lumped elements."}
    if huy is None:
        out["note"] = "No far field surface in this solution: no radiated power (closed model or no open boundary)"
        if surface:
            out["note"] += ("; the solver's total loss is stored with the far field surface only, so the losses on "
                            f"surfaces and lumped elements ({', '.join(surface[:8])}) are not in this budget")
    return out


TOOLS = [
    Tool("evaluate_fields", "Fields Calculator on the saved fields of a solution: value expressions such as "
         "flux(Obj, re(S)) (Poynting flux, W, through a sheet, face(Obj, k), a cut plane cut_xy(2mm) or the closed "
         "boundary of a solid, normal out; flip() reverses), int_vol(Obj, ...) (volume integrals, e.g. "
         "0.5*omega*eps0*eps_r*tand*|E|^2 or 0.5*omega*mu0*mu_r*tandm*|H|^2 for the losses), int_surf, "
         "int_line, max(Obj, |E|), at(point(x, y, z), E), or names of the design's equations. At one frequency "
         "(freq_index / frequency) or all frequencies with fields; port = the excitation (as in the field plots).",
         _schema({"setup": {"type": "string"}, "solution": {"type": "string", "description": "default LastAdaptive"},
                  "expressions": {"type": "array", "items": {"type": "string"}}, "expr": {"type": "string"},
                  "port": {"type": "integer"}, "freq_index": {"type": "integer"},
                  "frequency": {"type": ["string", "number"]}, "all_frequencies": {"type": "boolean"},
                  "phase_deg": {"type": "number"}}), t_evaluate_fields, True),
    Tool("get_power_budget", "Power budget of each port excitation (1 W incident) on the saved fields: accepted, "
         "reflected, transmitted to other ports, radiated (far field surface), lost (solver total) and the balance; "
         "volume losses per object and per material split into dielectric/conduction (eps'', sigma) and magnetic "
         "(mu''), the rest on surfaces and lumped elements.",
         _schema({"setup": {"type": "string"}, "solution": {"type": "string", "description": "default LastAdaptive"},
                  "port": {"type": "integer"}, "freq_index": {"type": "integer"},
                  "frequency": {"type": ["string", "number"]}, "all_frequencies": {"type": "boolean"}}),
         t_get_power_budget, True),
]
