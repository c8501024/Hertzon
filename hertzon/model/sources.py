"""Edit Sources: magnitude and phase per excitation used for post processing (no re-solve), and
for incident waves which field is shown (total, scattered or incident)."""
from __future__ import annotations

import json

FIELD_TYPES = ("Total", "Scattered", "Incident")


def defaults(design, names: list[str] | None = None) -> dict:
    """Sources of the design: its ports, or the given excitation names (the terminals of a solved terminal
    design whose fields are stored per terminal)."""
    out = {}
    for i, n in enumerate(names if names else [e.name for e in design.ports()]):
        out[n] = {"mag": 1.0 if i == 0 else 0.0, "phase": 0.0}
    for e in design.excitations:
        if e.kind == "Incident Plane Wave" and e.enabled:
            out[e.name] = {"mag": 1.0, "phase": 0.0}
    return out


def get(design, names: list[str] | None = None) -> dict | None:
    """Edited sources, or None while the user never opened Edit Sources (plots then show one port)."""
    raw = design.settings.get("Sources", "")
    if not raw:
        return None
    try:
        saved = json.loads(raw)
    except ValueError:
        return None
    out = defaults(design, names)
    for k, v in saved.items():
        if k in out:
            out[k] = {"mag": float(v.get("mag", 0)), "phase": float(v.get("phase", 0))}
    return out


def field_names(store) -> list[str] | None:
    """Names of the excitations whose fields a result folder stores, when they are terminals (the solver's
    field_sources.json); None when the fields are stored per port."""
    fs = store.read_json("field_sources.json", None) if store is not None else None
    return [q["name"] for q in fs] if fs else None


def set_(design, sources: dict, field_type: str) -> None:
    design.settings["Sources"] = json.dumps(sources)
    design.settings["FieldType"] = field_type


def field_type(design) -> str:
    t = design.settings.get("FieldType", "Total")
    return t if t in FIELD_TYPES else "Total"
