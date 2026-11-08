"""Fingerprints of a solved problem: equal keys mean a stored solution still matches the project."""
from __future__ import annotations

import json

# setup properties that only steer convergence: changing them keeps the adaptive mesh (continues)
CONVERGENCE_KEYS = {"MaximumPasses", "MaximumDeltaS", "MinimumPasses", "MinimumConvergedPasses",
                    "UseMatrixConvergence", "MaxDeltaMag", "MaxDeltaPhase", "MaximumRefinement", "SaveFields",
                    "UsePowerBalance", "MaxPowerBalance", "MaxDeltaEfficiency"}


def _legacy(c: dict) -> dict:
    """A command as it was stored before property renames, so solutions of older projects stay valid
    (CreateRectangle: the two in-plane sizes were Width/Height, the third size did not exist)."""
    if c.get("kind") != "CreateRectangle":
        return c
    from ..model.schema import RECT_SIZES
    p = dict(c.get("props", {}))
    a, b = RECT_SIZES.get(str(p.get("Axis", "Z")), RECT_SIZES["Z"])
    w, h = p.get(a), p.get(b)
    for k in ("XSize", "YSize", "ZSize"):
        p.pop(k, None)
    p["Width"], p["Height"] = w, h
    return {**c, "props": p}


def _legacy_values(props: dict) -> dict:
    """Setup values under their former names (renamed choices must not invalidate solutions)."""
    from ..model.schema import RENAMED_VALUES
    out = dict(props)
    for k, ren in RENAMED_VALUES.items():
        back = {new: old for old, new in ren.items()}
        if out.get(k) in back:
            out[k] = back[out[k]]
    return out


def model_key(project, design, setup) -> str:
    """Hash of everything that defines the solved problem and its meshing, except the convergence
    settings, sweeps and post-processing. Equal keys allow continuing the adaptive passes."""
    import hashlib
    from dataclasses import asdict
    d = asdict(design)
    for k in ("reports", "field_plots", "spheres", "notes", "name", "setups", "optimetrics", "parametrics"):
        d.pop(k, None)
    # equations of the Fields Calculator are post-processing (and left out, the keys of solutions of 0.2.3 and
    # earlier stay valid)
    d.pop("field_exprs", None)
    d.pop("field_funcs", None)
    if isinstance(d.get("settings"), dict):
        d["settings"] = {k: v for k, v in d["settings"].items() if k not in ("OutputVariables", "Sources", "FieldType",
                                                                       "DifferentialPairs")}
    objs = []
    for o in d.get("objects", []):
        if not o.get("model", True):
            continue
        o = {k: v for k, v in o.items() if k not in ("color", "transparency", "wireframe", "visible", "group")}
        o["history"] = [_legacy(c) for c in o.get("history", [])]
        objs.append(o)
    d["objects"] = objs
    # coordinate systems only matter through the objects defined in them
    d.pop("working_cs", None)
    # TwoSided was added later; where it equals the older meaning of UseThickness it changes nothing
    for b in d.get("boundaries", []):
        p = b.get("props", {})
        if b.get("kind") == "Finite Conductivity" and p.get("TwoSided") == p.get("UseThickness", "False"):
            b["props"] = {k: v for k, v in p.items() if k != "TwoSided"}
        elif b.get("kind") == "Finite Conductivity" and p.get("TwoSided") == "True":
            # two-sided without a thickness was one-sided up to 0.3.6: the solutions of then are outdated
            b["props"] = {**p, "_TwoSidedSheet": "v2"}
    used = {o.get("orientation", "Global") for o in objs}
    if used <= {"Global"}:
        d.pop("coord_systems", None)
    sp = _legacy_values({k: v for k, v in setup.props.items() if k not in CONVERGENCE_KEYS})
    mats = [asdict(m) for m in getattr(project, "materials", [])]
    key = {"d": d, "setup": sp, "eigen": setup.eigen, "mats": mats, "pvars": getattr(project, "variables", {})}
    f_open = open_region_frequency(project, design, setup)
    if f_open is not None:
        key["open_region_f"] = f"{f_open:.9g}"
    blob = json.dumps(key, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def open_region_frequency(project, design, setup) -> float | None:
    """The lowest frequency of the switched-on sweeps when it sizes the automatic open region (or a PML region) below the solution
    frequency, else None. The sweeps are not part of the model key, but this frequency is: switching on or
    lowering a sweep that way enlarges the region and changes the model (the solution becomes outdated and the
    adaptive passes run again). None keeps the keys of all other designs."""
    st = design.settings if isinstance(design.settings, dict) else {}
    mode = st.get("AutoOpen", "Off")
    pml = any(b.kind == "PML" and b.enabled for b in design.boundaries)
    if (mode == "Off" and not pml) or mode == "Auto Open - Filter" or setup.eigen:
        return None
    if not pml and str(st.get("AutoOpenPadding", "")).strip() and st.get("AutoOpenBoundary", "Auto") != "PML":
        return None                          # a given padding: the region does not depend on the frequency
    try:
        from .bridge import adaptive_frequencies, lowest_frequency
        ev = design.evaluator(project)
        f_low = lowest_frequency(design, setup, ev)
        return f_low if f_low < min(adaptive_frequencies(setup, ev)) else None
    except Exception:  # noqa: BLE001
        return None


def setup_key(setup) -> str:
    """Hash of the setup and its sweeps (a change marks the solution outdated)."""
    import hashlib
    from dataclasses import asdict
    d = asdict(setup)
    d.pop("name", None)
    d["props"] = _legacy_values(d.get("props", {}))
    d.get("props", {}).pop("SaveFields", None)
    for sw in d.get("sweeps", []):
        if "Enabled" in sw.get("props", {}):
            sw["props"]["Enabled"] = "True"    # enabling / disabling a sweep keeps the solution valid
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()
