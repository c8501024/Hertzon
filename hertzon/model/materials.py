"""Material definitions and the built-in system library."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, asdict

_NAMED = {"red": "#ff0000", "green": "#008000", "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff",
          "magenta": "#ff00ff", "black": "#000000", "white": "#ffffff", "gray": "#808080", "grey": "#808080",
          "orange": "#ffa500"}


def parse_color(color) -> str | None:
    """'#rrggbb' for '#rrggbb', '#rgb', 'rrggbb', '(r g b)', 'r,g,b' (0-255) or a few names; None if unreadable."""
    if isinstance(color, (list, tuple)):
        color = ",".join(str(c) for c in color)
    s = str(color or "").strip().lower()
    if s in _NAMED:
        return _NAMED[s]
    h = s.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", h):
        return "#" + h
    if s.startswith("#") and re.fullmatch(r"[0-9a-f]{3}", h):
        return "#" + "".join(c * 2 for c in h)
    parts = [x for x in re.split(r"[\s,;()\[\]]+", s.replace("rgb", "")) if x]
    if len(parts) == 3:
        try:
            v = [float(x) for x in parts]
        except ValueError:
            return None
        if all(0 <= x <= 1 for x in v) and any("." in x for x in parts):
            v = [x * 255 for x in v]
        if all(0 <= x <= 255 for x in v):
            return "#" + "".join(f"{int(round(x)):02x}" for x in v)
    return None


def normalize_color(color, default: str = "#8f8f8f") -> str:
    return parse_color(color) or default


@dataclass
class Material:
    name: str
    permittivity: str = "1"
    permeability: str = "1"
    conductivity: str = "0"           # S/m
    loss_tangent: str = "0"           # dielectric loss tangent
    magnetic_loss_tangent: str = "0"
    color: str = "#bebebe"
    pec: bool = False
    library: str = "Project"          # "System" or "Project"
    description: str = ""
    # frequency dependent models ("None", "Djordjevic-Sarkar", "Debye")
    dispersion: str = "None"
    measured_frequency: str = "1GHz"  # Djordjevic-Sarkar: permittivity / loss tangent given at this frequency
    eps_static: str = "4.6"           # Debye: low frequency permittivity
    eps_infinity: str = "4.2"         # Debye / reference value at high frequency
    relaxation_frequency: str = "1GHz"
    # anisotropic (diagonal tensor) permittivity / permeability / loss tangent, "xx,yy,zz"
    anisotropic: bool = False
    permittivity_tensor: str = ""
    permeability_tensor: str = ""
    loss_tangent_tensor: str = ""
    # material of EMerge's library (emsutil.lib) this one was taken from, e.g. "DIEL_RO4350B"
    emsutil_key: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Material":
        m = Material(**{k: v for k, v in d.items() if k in Material.__dataclass_fields__})
        m.color = normalize_color(m.color, "#bebebe")
        return m


DS_F1, DS_F2 = 1e3, 1e12   # Djordjevic-Sarkar corner frequencies (Hz)


def djordjevic_sarkar(er_m: float, tand_m: float, f_m: float):
    """Wideband Debye (Djordjevic-Sarkar) model fitted to er' and tan(delta) at f_m.

    Returns eps(f) (complex, e^{jwt}: eps' - j eps'')."""
    import math
    w1, w2, wm = 2 * math.pi * DS_F1, 2 * math.pi * DS_F2, 2 * math.pi * f_m
    L = math.log(w2 / w1)

    def A(w):
        return (complex(math.log(abs(complex(w2, w))), math.atan2(w, w2))
                - complex(math.log(abs(complex(w1, w))), math.atan2(w, w1))) / L
    am = A(wm)
    d_eps = er_m * tand_m / max(-am.imag, 1e-300)
    e_inf = er_m - d_eps * am.real

    def eps(f):
        return e_inf + d_eps * A(2 * math.pi * f)
    return eps


def debye(e_s: float, e_inf: float, f_r: float):
    def eps(f):
        return e_inf + (e_s - e_inf) / complex(1, f / f_r)
    return eps


def _m(name, er="1", ur="1", cond="0", tand="0", color="#bebebe", pec=False, desc=""):
    return Material(name=name, permittivity=er, permeability=ur, conductivity=cond, loss_tangent=tand,
                    color=color, pec=pec, library="System", description=desc)


SYSTEM_LIBRARY: list[Material] = [
    _m("vacuum", color="#e6f2ff"),
    _m("air", er="1.0006", ur="1.0000004", color="#e6f2ff"),
    _m("pec", color="#d6a45c", pec=True, desc="Perfect electric conductor"),
    _m("copper", cond="58000000", ur="0.999991", color="#d98c4a"),
    _m("aluminum", cond="38000000", ur="1.000021", color="#c0c6cc"),
    _m("gold", cond="41000000", color="#e5c100"),
    _m("silver", cond="61000000", ur="0.99998", color="#d9d9d9"),
    _m("brass", cond="15000000", color="#c9ae5d"),
    _m("nickel", cond="14500000", ur="600", color="#a7a7a7"),
    _m("stainless_steel", cond="1100000", color="#9aa3aa"),
    _m("tin", cond="8670000", color="#b5b5b5"),
    _m("FR4_epoxy", er="4.4", tand="0.02", color="#5fb3a6"),
    _m("Rogers RO4003C (tm)", er="3.55", tand="0.0027", color="#5f7f3f"),
    _m("Rogers RO4350B (tm)", er="3.66", tand="0.0037", color="#6a7f3a"),
    _m("Rogers RT/duroid 5880 (tm)", er="2.2", tand="0.0009", color="#c8b88a"),
    _m("Rogers RT/duroid 5870 (tm)", er="2.33", tand="0.0012", color="#c8b080"),
    _m("Rogers RT/duroid 6002 (tm)", er="2.94", tand="0.0012", color="#b8a070"),
    _m("Rogers RT/duroid 6010/6010LM (tm)", er="10.2", tand="0.0023", color="#a89060"),
    _m("Rogers TMM 10i (tm)", er="9.8", tand="0.002", color="#988050"),
    _m("Teflon (tm)", er="2.1", tand="0.001", color="#f0f0f0"),
    _m("polyethylene", er="2.25", tand="0.0002", color="#f5f5dc"),
    _m("polystyrene", er="2.6", tand="0.0003", color="#f0ead6"),
    _m("polyimide", er="3.5", tand="0.008", color="#c88a00"),
    _m("Alumina_92pct", er="9.2", tand="0.008", color="#f2f2e6"),
    _m("Al2_O3_ceramic", er="9.8", tand="0.0001", color="#f7f7ef"),
    _m("quartz", er="3.78", tand="0.0001", color="#e8f4f8"),
    _m("glass", er="5.5", tand="0.0005", color="#cfe8ef"),
    _m("silicon", er="11.9", cond="0.01", color="#6d6d7d"),
    _m("silicon_dioxide", er="4", color="#d5e5f5"),
    _m("gallium_arsenide", er="12.9", tand="0.0006", color="#7d6d7d"),
    _m("water_distilled", er="81", tand="0.0001", color="#3a7bd5"),
    _m("water_sea", er="81", cond="4", color="#2a5bb5"),
    _m("ABS_plastic", er="2.8", tand="0.01", color="#e0d0c0"),
    _m("PLA", er="2.75", tand="0.01", color="#e0e0c0"),
    _m("Rogers RO3003 (tm)", er="3", tand="0.001", color="#c8b090"),
    _m("Rogers RO3006 (tm)", er="6.15", tand="0.002", color="#b8a080"),
    _m("Rogers RO3010 (tm)", er="10.2", tand="0.0022", color="#a89070"),
    _m("Rogers RO3035 (tm)", er="3.5", tand="0.0015", color="#c0a888"),
    _m("Rogers RO4360G2 (tm)", er="6.15", tand="0.0038", color="#6a7f4a"),
    _m("Rogers RO4835 (tm)", er="3.48", tand="0.0037", color="#6f8040"),
    _m("Rogers RT/duroid 5880LZ (tm)", er="1.96", tand="0.0019", color="#d0c090"),
    _m("Rogers RT/duroid 6006 (tm)", er="6.15", tand="0.0027", color="#b09868"),
    _m("Rogers TMM 3 (tm)", er="3.27", tand="0.002", color="#a08858"),
    _m("Rogers TMM 4 (tm)", er="4.5", tand="0.002", color="#a08858"),
    _m("Rogers TMM 6 (tm)", er="6", tand="0.0023", color="#a08858"),
    _m("Rogers Kappa 438 (tm)", er="4.38", tand="0.005", color="#6f9050"),
    _m("Isola I-Tera MT40", er="3.45", tand="0.0031", color="#5f8f5f"),
    _m("Isola Astra MT77", er="3", tand="0.0017", color="#5f8f6f"),
    _m("Isola 370HR", er="4.04", tand="0.021", color="#4f8f4f"),
    _m("Panasonic Megtron 6", er="3.71", tand="0.002", color="#4f7f5f"),
    _m("Panasonic Megtron 7", er="3.4", tand="0.001", color="#4f7f6f"),
    _m("Taconic TLY-5", er="2.2", tand="0.0009", color="#d8c8a0"),
    _m("Taconic RF-35", er="3.5", tand="0.0018", color="#c8b898"),
    _m("Arlon AD255C", er="2.55", tand="0.0014", color="#c8c0a0"),
    _m("LTCC Ferro A6M", er="5.9", tand="0.002", color="#e8e0d0"),
    _m("LTCC DuPont 951", er="7.8", tand="0.006", color="#e0d8c8"),
    _m("aluminum_nitride", er="8.8", tand="0.0005", color="#efefe0"),
    _m("beryllium_oxide", er="6.7", tand="0.0003", color="#f0f0e8"),
    _m("sapphire", er="10.06", tand="0.0001", color="#dfefff", desc="isotropic approximation"),
    _m("silicon_nitride", er="7.5", tand="0.001", color="#d0d0e0"),
    _m("indium_phosphide", er="12.4", tand="0.0005", color="#8d7d8d"),
    _m("silicon_high_resistivity", er="11.9", cond="0.0005", color="#6d6d8d"),
    _m("PEEK", er="3.2", tand="0.003", color="#d8c8a8"),
    _m("Ultem 1000 (tm)", er="3.15", tand="0.0025", color="#c8732a",
       desc="polyetherimide; support beads of precision connectors (2.92 mm, 2.4 mm)"),
    _m("Kel-F (PCTFE)", er="2.35", tand="0.005", color="#f4f4f4", desc="polychlorotrifluoroethylene"),
    _m("Rexolite (tm)", er="2.53", tand="0.0005", color="#f0f0f0"),
    _m("nylon", er="3.0", tand="0.02", color="#f0e8d8"),
    _m("PTFE_glass", er="2.5", tand="0.0015", color="#e8e0d0"),
    _m("ferrite", er="12", ur="1000", tand="0.0002", color="#404040"),
    _m("titanium", cond="2380000", color="#a0a0a8"),
    _m("tungsten", cond="18200000", color="#909098"),
    _m("platinum", cond="9300000", color="#d0d0d8"),
    _m("solder", cond="7000000", color="#b0b0b0"),
    _m("iron", cond="10300000", ur="4000", color="#707070"),
    _m("graphite", cond="70000", color="#303030"),
]

SYSTEM_BY_NAME = {m.name: m for m in SYSTEM_LIBRARY}

# ------------------------------------------------------------------------------------------------
# EMerge material library (emsutil.lib, CC0): about 270 metals, semiconductors, PCB laminates (Rogers,
# Isola/Nelco, Taconic, Arlon, ...), solder masks and foams. Read from the installed emsutil on first
# use (importing emsutil takes about a second), shown as library "EMerge".
# ------------------------------------------------------------------------------------------------

EMERGE_LIBRARY = "EMerge"
EMERGE_DISPERSION = "EMerge library"        # frequency dependent data of an emsutil material
_EMERGE_COLORS = {"EMERGE-DIEL": "#5f9f6f", "EMERGE-COPPER": "#b87333", "EMERGE-PEC": "#d6a45c",
                  "EMERGE-AIR": "#e6f2ff"}
_EMERGE_CACHE: dict[str, Material] | None = None
_EMERGE_FREQ = 1e9          # scalar values shown for frequency dependent properties


def _num(v) -> str:
    v = complex(v)
    x = v.real if abs(v.imag) <= 1e-12 * max(abs(v.real), 1.0) else v
    return f"{x:.6g}" if isinstance(x, float) else str(x)


def emerge_object(key: str):
    """The emsutil Material for a library key (None when emsutil or the key is missing)."""
    try:
        import emsutil.lib as lib
        from emsutil.material import Material as EMat
    except Exception:  # noqa: BLE001
        return None
    obj = getattr(lib, key, None)
    return obj if isinstance(obj, EMat) else None


def _from_emsutil(key: str, m) -> Material | None:
    name = (getattr(m, "name", "") or "").strip()
    if not name or name.startswith("UnnamedMaterial"):
        return None
    props = {}
    freq_dep = []
    for attr in ("er", "ur", "tand", "cond"):
        p = getattr(m, attr)
        try:
            if getattr(p, "_freq_dependent", False):
                freq_dep.append(attr)
            if getattr(p, "_coord_dependent", False):
                return None         # coordinate dependent: no scalar equivalent
            props[attr] = complex(p.scalar(_EMERGE_FREQ))
        except Exception:  # noqa: BLE001
            return None
    pec = name.upper() == "PEC" or props["cond"].real >= 1e20
    color = getattr(m, "color", "") or ""
    color = _EMERGE_COLORS.get(color.upper(), color)
    desc = "EMerge material library"
    if freq_dep:
        desc += f" (frequency dependent; values shown at {_EMERGE_FREQ / 1e9:g} GHz)"
    return Material(name=name, permittivity=_num(props["er"]), permeability=_num(props["ur"]),
                    conductivity="0" if pec else _num(props["cond"]), loss_tangent=_num(props["tand"]),
                    color=normalize_color(color, "#8f9fa0"), pec=pec, library=EMERGE_LIBRARY, description=desc,
                    dispersion=EMERGE_DISPERSION if freq_dep else "None", emsutil_key=key)


_EMERGE_LOCK = threading.Lock()
_EMERGE_CACHE_FORMAT = 1        # raise when _from_emsutil changes what it stores


def _emerge_cache_file():
    """~/.hertzon/cache/emerge_materials-<emsutil version>-<hash>.json, or None when emsutil is not installed.
    The version is read from the package metadata, which does not import emsutil."""
    import hashlib
    from importlib import metadata
    from pathlib import Path
    try:
        ver = metadata.version("emsutil")
    except Exception:  # noqa: BLE001
        return None
    sig = hashlib.sha1(("|".join(sorted(SYSTEM_BY_NAME)) + f"|{_EMERGE_FREQ}|{_EMERGE_CACHE_FORMAT}")
                       .encode()).hexdigest()[:10]
    return Path.home() / ".hertzon" / "cache" / f"emerge_materials-{ver}-{sig}.json"


def _read_emerge_cache(path) -> dict[str, Material] | None:
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)["materials"]
        return {m.name: m for m in (Material.from_dict(r) for r in rows)}
    except Exception:  # noqa: BLE001   (missing or damaged: read from emsutil again)
        return None


def _write_emerge_cache(path, mats: dict[str, Material]) -> None:
    import json
    import os
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"materials": [m.to_dict() for m in mats.values()]}, fh)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001   (read-only home: the library is simply read from emsutil next time)
        pass


def _emerge_from_emsutil() -> dict[str, Material]:
    out: dict[str, Material] = {}
    try:
        import emsutil.lib as lib
        from emsutil.material import Material as EMat
        items = [(k, v) for k, v in vars(lib).items() if isinstance(v, EMat) and not k.startswith("_")]
    except Exception:  # noqa: BLE001
        items = []
    taken = {n.lower() for n in SYSTEM_BY_NAME}
    for key, obj in items:
        try:
            m = _from_emsutil(key, obj)
        except Exception:  # noqa: BLE001
            m = None
        if m is not None and m.name.lower() not in taken:
            taken.add(m.name.lower())
            out[m.name] = m
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def emerge_library() -> dict[str, Material]:
    """EMerge's material library by name (empty when emsutil is not installed). Names already used by
    Hertzon's own library (in any case: "copper", "air", "pec") keep the Hertzon material.

    Importing emsutil also loads matplotlib, pyvista and scipy: seconds on Windows, and the material dialogs
    and the Properties panel waited for it. The table is therefore kept in a small file per emsutil version
    and read from there (milliseconds); emsutil is imported only to build that file once."""
    global _EMERGE_CACHE
    if _EMERGE_CACHE is not None:
        return _EMERGE_CACHE
    with _EMERGE_LOCK:                      # the start-up thread and the window must not both build it
        if _EMERGE_CACHE is not None:
            return _EMERGE_CACHE
        path = _emerge_cache_file()
        mats = _read_emerge_cache(path) if path is not None else None
        if mats is None:
            mats = _emerge_from_emsutil()
            if path is not None and mats:
                _write_emerge_cache(path, mats)
        _EMERGE_CACHE = mats
        return _EMERGE_CACHE


def library_material(name: str) -> Material | None:
    """A read-only material of Hertzon's system library or of EMerge's library."""
    m = SYSTEM_BY_NAME.get(name)
    if m is not None:
        return m
    return emerge_library().get(name)


def library_materials() -> list[Material]:
    return list(SYSTEM_BY_NAME.values()) + list(emerge_library().values())


def is_conductor(mat: Material, threshold: float = 1e5) -> bool:
    """Solve-inside is off by default for good conductors, as in the reference tool."""
    if mat.pec:
        return True
    try:
        return float(mat.conductivity) >= threshold
    except ValueError:
        return False


def default_color(name: str) -> str:
    m = library_material(name)
    return m.color if m else "#bebebe"


def export_library(path: str, mats: list[Material]) -> None:
    import json
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"hertzon_materials": [m.to_dict() for m in mats]}, fh, indent=1)


def import_library(path: str) -> list[Material]:
    """Hertzon JSON library or an .amat material library (simple scalar properties)."""
    import json
    import re
    text = open(path, encoding="utf-8", errors="replace").read()
    if text.lstrip().startswith("{"):
        d = json.loads(text)
        out = []
        for x in d.get("hertzon_materials", d.get("materials", [])):
            m = Material.from_dict(x)
            m.library = "Project"
            out.append(m)
        return out
    out = []
    # .amat: $begin 'name' ... permittivity='4.4' ... dielectric_loss_tangent='0.02' ... $end 'name'
    for name, body in re.findall(r"\$begin '([^']+)'((?:(?!\$begin ').)*?)\$end '\1'", text, re.S):
        if "permittivity" not in body and "conductivity" not in body:
            continue

        def val(key, default):
            m = re.search(rf"\b{key}\s*=\s*'([^']*)'", body)
            return m.group(1) if m else default
        out.append(Material(name=name, permittivity=val("permittivity", "1"), permeability=val("permeability", "1"),
                            conductivity=val("conductivity", "0"),
                            loss_tangent=val("dielectric_loss_tangent", "0"),
                            magnetic_loss_tangent=val("magnetic_loss_tangent", "0"), library="Project",
                            description="imported from " + path.split("/")[-1].split("\\")[-1]))
    return out
