"""Design validation check (the "Validation Check" dialog)."""
from __future__ import annotations

from dataclasses import dataclass

from .design import Project, Design
from .expr import ExprError
from . import schema


@dataclass
class Check:
    category: str
    level: str          # ok | warning | error
    message: str


CATEGORIES = ["Design Settings", "3D Model", "Boundaries and Excitations", "Mesh Operations", "Analysis Setup",
              "Radiation"]


@dataclass
class Overlap:
    a: str
    b: str
    winner: str             # the object whose material applies in the overlap
    loser: str              # resolving subtracts the winner from it
    contained: bool         # the loser lies completely inside the winner
    level: str
    message: str


def classify_overlaps(project: Project, design: Design, overlaps) -> list[Overlap]:
    """Overlapping solids (kernel.occ.solid_overlaps) checked as the reference tool does. An object completely
    inside another wins (substrate in an air box) and is not listed. With the design setting "Material
    override" (default) a metal also overrides a dielectric, so a dielectric inside a metal vanishes (warning).
    Every other partial overlap of different materials is an error: two dielectrics, two metals, and without
    material override any two materials. Overlaps of the same material change nothing."""
    from .materials import is_conductor
    override = schema.material_override(design)
    out = []
    for a, b, v, va, vb in overlaps:
        oa, ob = design.obj(a), design.obj(b)
        try:
            ca, cb = is_conductor(project.material(oa.material)), is_conductor(project.material(ob.material))
        except KeyError:
            continue
        if override:
            win = a if (ca, -va) > (cb, -vb) else b
        else:
            win = a if va < vb else b
        lose = b if win == a else a
        mw, ml = design.obj(win).material, design.obj(lose).material
        if mw == ml:
            continue
        if v >= (1 - 1e-6) * min(va, vb):              # one object completely inside the other
            if win == (a if va < vb else b):
                continue                                # the inner object wins, as everywhere
            out.append(Overlap(a, b, win, lose, True, "warning",
                               f"'{lose}' ({ml}) lies completely inside conductor '{win}' ({mw}) and has no effect: "
                               "the conductor wins in the overlap (material override)"))
            continue
        pct = 100 * v / min(va, vb)
        what = f"'{a}' ({oa.material}) and '{b}' ({ob.material}) partially overlap ({pct:.3g}% of the smaller)"
        if override and ca != cb:
            out.append(Overlap(a, b, win, lose, False, "ok",
                               f"{what}: {mw} of '{win}' applies there (material override: the metal wins)"))
        else:
            why = ("two different metals" if ca and cb else "two different dielectrics" if override
                   else "different materials without material override")
            out.append(Overlap(a, b, win, lose, False, "error",
                               f"{what}: {why} must not intersect. Resolve the overlap (subtracts '{win}' from "
                               f"'{lose}') or change the model"))
    return out


def _check_touchstone(project: Project, a, add) -> None:
    from pathlib import Path
    from .touchstone import ComponentImpedance
    path = Path(a.props.get("TouchstoneFile", "").strip())
    if not str(path) or str(path) == ".":
        add("Boundaries and Excitations", "error", f"'{a.name}': no Touchstone file given")
        return
    if not path.is_absolute() and project.path:
        path = Path(project.path).parent / path
    try:
        ComponentImpedance(path, a.props.get("TouchstoneMount", "Series"))
    except Exception as e:  # noqa: BLE001
        add("Boundaries and Excitations", "error", f"'{a.name}': Touchstone file {path}: {e}")


def validate(project: Project, design: Design, tess: dict | None = None, errors: dict | None = None,
             overlaps: list[Overlap] | None = None) -> list[Check]:
    out: list[Check] = []
    ev = design.evaluator(project)
    add = lambda c, l, m: out.append(Check(c, l, m))  # noqa: E731

    # Design settings / variables
    bad = False
    for name, expr in {**project.variables, **design.variables}.items():
        err = ev.check(expr)
        if err:
            add("Design Settings", "error", f"Variable '{name}': {err}")
            bad = True
    if not bad:
        add("Design Settings", "ok", f"Solution type {design.settings.get('SolutionType', 'Modal')}, "
                                     f"units {design.units}")

    # 3D model
    live = [o for o in design.live_objects() if o.model]
    if not live:
        add("3D Model", "error", "The model contains no objects")
    for o in live:
        try:
            project.material(o.material)
        except KeyError:
            add("3D Model", "error", f"'{o.name}': unknown material '{o.material}'")
        for c in o.history:
            for k, v in c.props.items():
                p = next((p for p in schema.GEOMETRY.get(c.kind, []) if p.name == k), None)
                if p and p.type in ("length", "expr", "int", "angle", "float") and str(v).strip():
                    err = ev.check(v)
                    if err:
                        add("3D Model", "error", f"'{o.name}' {c.kind}.{k}: {err}")
    for n, e in (errors or {}).items():
        add("3D Model", "error", f"'{n}': {e}")
    if overlaps is not None:
        for ov in overlaps:
            add("3D Model", ov.level, ov.message)
    elif tess is not None:
        solids = [(o.name, tess[o.name]) for o in live if o.name in tess and tess[o.name].dim == 3
                  and o.history[0].kind != "CreateRegion"]
        for i in range(len(solids)):
            for j in range(i + 1, len(solids)):
                a, ta = solids[i]
                b, tb = solids[j]
                if _boxes_overlap(ta.bbox, tb.bbox) and not _contains(ta.bbox, tb.bbox) and not _contains(tb.bbox, ta.bbox):
                    add("3D Model", "warning", f"Objects '{a}' and '{b}' may intersect; partial overlaps of "
                                               "different materials are only allowed for a metal in a dielectric "
                                               "(material override)")
    if not any(c.level == "error" for c in out if c.category == "3D Model"):
        add("3D Model", "ok", f"{len(live)} objects")

    # boundaries and excitations
    names = {o.name for o in design.live_objects()}
    for a in design.boundaries + design.excitations:
        refs = {f.obj for f in a.faces} | set(a.objects)
        missing = refs - names
        if missing:
            add("Boundaries and Excitations", "error", f"'{a.name}' references missing object(s): {', '.join(missing)}")
        if not refs and a.kind != "Incident Plane Wave":
            add("Boundaries and Excitations", "error", f"'{a.name}' has no assignment")
        if a.kind in schema.UNSUPPORTED_BOUNDARIES:
            add("Boundaries and Excitations", "warning", f"'{a.name}': {schema.UNSUPPORTED_BOUNDARIES[a.kind]}")
        if a.kind == "Lumped RLC" and a.enabled and a.props.get("Model") == "Touchstone":
            _check_touchstone(project, a, add)
        for k, v in a.props.items():
            p = next((p for p in schema.BOUNDARIES.get(a.kind, []) + schema.EXCITATIONS.get(a.kind, [])
                      if p.name == k), None)
            if p and p.type in ("length", "expr", "angle", "float") and str(v).strip() and ev.check(v):
                add("Boundaries and Excitations", "error", f"'{a.name}'.{k}: {ev.check(v)}")
        if a.kind in ("Radiation", "PML"):
            for o in sorted(refs & names):
                try:
                    mat = project.material(design.obj(o).material)
                    cond = float(mat.conductivity or 0)
                except Exception:  # noqa: BLE001
                    continue
                if mat.pec or cond > 1e4:
                    add("Boundaries and Excitations", "error",
                        f"'{a.name}' is on '{o}' whose material is {mat.name}: the radiation region must be a "
                        "dielectric (air or vacuum), a conductor leaves nothing to solve")
    primaries = {b.name for b in design.boundaries if b.kind == "Primary" and b.enabled}
    secondaries = [b for b in design.boundaries if b.kind == "Secondary" and b.enabled]
    for b in secondaries:
        pname = b.props.get("Primary", "").strip()
        if pname not in primaries:
            add("Boundaries and Excitations", "error", f"'{b.name}': its Primary boundary '{pname}' does not exist "
                                                       "or is disabled")
    if not secondaries and any(e.kind == "Floquet Port" and e.enabled for e in design.excitations):
        add("Boundaries and Excitations", "error", "Floquet ports need a periodic cell: assign Primary / Secondary "
                                                   "boundary pairs to the side faces")
    if tess and not design.ports() and any(e.kind == "Incident Plane Wave" and e.enabled
                                           for e in design.excitations):
        for b in design.boundaries:
            if b.kind != "Radiation" or not b.enabled:
                continue
            for o in {f.obj for f in b.faces}:
                t = tess.get(o)
                if t is None:
                    continue
                n = len(t.face_normal)
                have = {f.face for f in b.faces if f.obj == o}
                if len(have) < n:
                    add("Boundaries and Excitations", "warning",
                        f"'{b.name}' covers {len(have)} of {n} faces of '{o}'. The incident wave enters only "
                        "through radiation faces; the other faces are Perfect E and reflect it. Assign the "
                        "radiation boundary to all outer faces (or use Auto Open).")
    pws = [e for e in design.excitations if e.kind == "Incident Plane Wave" and e.enabled]
    if pws and not design.ports():
        rads = [b for b in design.boundaries if b.kind == "Radiation" and b.enabled]
        if design.settings.get("AutoOpen", "Off") == "Off" and not rads:
            add("Boundaries and Excitations", "error", f"'{pws[0].name}' needs a radiation boundary (or Auto Open): "
                                                       "the incident wave enters through it")
        elif tess and rads:
            from . import planewave
            region = [o for b in rads for o in {f.obj for f in b.faces} | set(b.objects) if o in tess]
            for e in pws:
                try:
                    _, _, origin = planewave.vectors(e.props, ev)
                except Exception:  # noqa: BLE001
                    continue
                inside = any(all(tess[o].bbox[i] - 1e-9 <= origin[i] <= tess[o].bbox[i + 3] + 1e-9 for i in range(3))
                             for o in region)
                if region and not inside:
                    add("Boundaries and Excitations", "warning",
                        f"'{e.name}': the zero phase position lies outside the radiation boundary region "
                        f"({', '.join(sorted(set(region)))}). Place it inside the region.")
    ports = design.ports()
    nums = []
    for e in ports:
        try:
            nums.append(ev.integer(e.props["PortNumber"]))
        except ExprError:
            pass
    if design.settings.get("SolutionType") != "Eigenmode":
        if not ports and not any(e.kind == "Incident Plane Wave" for e in design.excitations):
            add("Boundaries and Excitations", "error", "No excitations defined")
        if sorted(nums) != list(range(1, len(nums) + 1)):
            add("Boundaries and Excitations", "error", f"Port numbers must be 1..N without gaps, found {sorted(nums)}")
    open_bc = design.settings.get("AutoOpen", "Off") != "Off" or any(
        b.kind in ("Radiation", "PML") for b in design.boundaries)
    if not open_bc:
        add("Boundaries and Excitations", "ok", "Outer boundary is Perfect E (closed problem)")
    if not any(c.level == "error" for c in out if c.category == "Boundaries and Excitations"):
        add("Boundaries and Excitations", "ok", f"{len(design.boundaries)} boundaries, {len(design.excitations)} excitations")

    # mesh operations
    for m in design.mesh_ops:
        refs = {f.obj for f in m.faces + m.edges} | set(m.objects)     # Length Based On Edges: edges only
        if refs - names:
            add("Mesh Operations", "error", f"'{m.name}' references missing objects")
        if not refs:
            add("Mesh Operations", "error", f"'{m.name}' has no assignment")
    if not any(c.category == "Mesh Operations" for c in out):
        add("Mesh Operations", "ok", f"{len(design.mesh_ops)} mesh operations")

    # analysis
    if not design.setups:
        add("Analysis Setup", "error", "No solution setup defined")
    for s in design.setups:
        props = schema.EIGEN_SETUP if s.eigen else schema.SETUP
        for p in props:
            v = s.props.get(p.name, p.default)
            if p.type in ("freq",) and schema.is_visible(p, s.props):
                err = ev.check(v)
                if err:
                    add("Analysis Setup", "error", f"{s.name}.{p.name}: {err}")
        for sw in s.sweeps:
            for p in schema.SWEEP:
                if p.type == "freq" and schema.is_visible(p, sw.props):
                    err = ev.check(sw.props.get(p.name, p.default))
                    if err:
                        add("Analysis Setup", "error", f"{s.name}:{sw.name}.{p.name}: {err}")
    if design.setups and not any(c.level == "error" for c in out if c.category == "Analysis Setup"):
        add("Analysis Setup", "ok", f"{len(design.setups)} setup(s)")

    # radiation
    if design.spheres and not open_bc:
        add("Radiation", "warning", "Far field setup without radiation/PML boundary or Auto Open")
    elif design.spheres:
        add("Radiation", "ok", f"{len(design.spheres)} far field setup(s)")
    return out


def _boxes_overlap(a, b) -> bool:
    return all(a[i] < b[i + 3] - 1e-12 and b[i] < a[i + 3] - 1e-12 for i in range(3))


def _contains(a, b) -> bool:
    return all(a[i] <= b[i] + 1e-12 and b[i + 3] <= a[i + 3] + 1e-12 for i in range(3))


def worst(checks: list[Check], category: str) -> str:
    lv = [c.level for c in checks if c.category == category]
    if "error" in lv:
        return "error"
    if "warning" in lv:
        return "warning"
    return "ok"
