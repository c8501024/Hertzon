"""Fingerprints of a solved problem: equal keys mean a stored solution still matches the project."""
from __future__ import annotations

import json

# setup properties that only steer convergence: changing them keeps the adaptive mesh (continues)
CONVERGENCE_KEYS = {"MaximumPasses", "MaximumDeltaS", "MinimumPasses", "MinimumConvergedPasses",
                    "UseMatrixConvergence", "MaxDeltaMag", "MaxDeltaPhase", "MaximumRefinement", "SaveFields"}


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
    used = {o.get("orientation", "Global") for o in objs}
    if used <= {"Global"}:
        d.pop("coord_systems", None)
    sp = _legacy_values({k: v for k, v in setup.props.items() if k not in CONVERGENCE_KEYS})
    mats = [asdict(m) for m in getattr(project, "materials", [])]
    blob = json.dumps({"d": d, "setup": sp, "eigen": setup.eigen, "mats": mats,
                       "pvars": getattr(project, "variables", {})}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


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
