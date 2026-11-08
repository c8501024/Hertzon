"""The design wizard catalogue (Tools > Design Wizard): planar structures and antennas that are synthesized from a
specification, built as a circuit and a 3D design with the same variables, and optimized with multi-fidelity.

CATALOG lists every structure, the ones that are built (status "available") and the planned ones, which the
wizard shows greyed out. structure(key) returns one; create_structure() is what the dialog and the MCP tool call.
"""
from __future__ import annotations

from .base import (
    AVAILABLE,
    IN_PROGRESS,
    PLANNED,
    Field,
    Result,
    Structure,
    Substrate,
    Synthesis,
    Variable,
)
from .couplers import Branchline
from .dividers import TeeDivider
from .filters import ShortedStubBandpass, SteppedImpedanceLowpass
from .icons import ICONS
from .matching import QuarterWaveTransformer, StubMatch

CATEGORIES = (("antenna", "Antennas"), ("match", "Matching"), ("coupler", "Couplers & hybrids"),
              ("divider", "Power dividers"), ("filter", "Filters"), ("feed", "Feed networks"),
              ("other", "Bias & miscellaneous"))


class Designer(Structure):
    """A structure that has its own designer dialog already (Tools menu); the wizard opens it."""
    status = AVAILABLE

    def __init__(self, key, title, category, description, use, designer, icon, ports=1):
        self.key, self.title, self.category, self.description, self.use = key, title, category, description, use
        self.designer, self.icon, self.ports = designer, icon, ports


class Planned(Structure):
    """A structure of the plan, shown on its card but not built yet."""

    def __init__(self, key, title, category, description, use, icon, status=PLANNED, priority=1, needs="",
                 ports=2):
        self.key, self.title, self.category, self.description, self.use = key, title, category, description, use
        self.icon, self.status, self.priority, self.needs, self.ports = icon, status, priority, needs, ports


def _built(cls, icon):
    s = cls()
    s.icon = icon
    return s


CATALOG: list[Structure] = [
    # ---- antennas (see the antenna study: P1 easy, P2 medium, P3 hard)
    Designer("patch", "Rectangular patch", "antenna", "Inset fed, transmission line model",
             "WLAN, GNSS, ISM; optimization of length and inset", "antenna_patch", "patch"),
    Planned("cpatch", "Circular patch", "antenna", "Probe or inset fed", "Compact, symmetric pattern", "cpatch",
            ports=1),
    Planned("cppatch", "Circularly polarized patch", "antenna", "Truncated corners, one feed",
            "GNSS, satellite links", "cppatch", ports=1),
    Planned("dipole", "Dipole", "antenna", "Wire or printed strip, centre fed", "Reference antenna, measurements",
            "dipole", ports=1),
    Planned("monopole", "Monopole", "antenna", "Quarter wave over a finite ground plane", "Vehicles, IoT",
            "monopole", ports=1),
    Planned("ifa", "Inverted-F (IFA)", "antenna", "Printed on the PCB edge, shorting via", "Bluetooth, Wi-Fi in devices",
            "ifa", ports=1),
    Planned("uwb", "UWB disc monopole", "antenna", "Planar disc, partial ground", "Ultra-wideband 3.1-10.6 GHz",
            "uwb", priority=2, ports=1),
    Planned("slot", "Microstrip-fed slot", "antenna", "Slot in the ground plane", "Low profile, bidirectional",
            "slot", priority=2, ports=1),
    Planned("array", "Patch array", "antenna", "Patches with a corporate feed (circuit and 3D blocks)",
            "Higher gain, radar, point-to-point links", "array", priority=2, ports=1),
    Planned("yagi", "Printed quasi-Yagi", "antenna", "Dipole, ground reflector, director, balun", "Directional links",
            "yagi", priority=2, ports=1),
    Planned("horn", "Pyramidal horn", "antenna", "Waveguide fed, gain from the aperture", "Reference gain antenna",
            "horn", priority=2, ports=1),
    Planned("helix", "Axial-mode helix", "antenna", "Wire helix over ground, circular polarization",
            "Satellite, telemetry", "helix", priority=2, ports=1),
    Planned("vivaldi", "Vivaldi", "antenna", "Exponentially tapered slot", "Wideband end-fire, test ranges",
            "vivaldi", priority=3, needs="a radial stub (microstrip to slotline transition)", ports=1),
    # ---- matching
    _built(QuarterWaveTransformer, "qwt"),
    _built(StubMatch, "stub"),
    Planned("lumped", "Lumped L / Pi match", "match", "SMD parts, values from the E series",
            "Small boards, antennas in devices", "lump", priority=2, needs="discrete values in the optimizer"),
    # ---- couplers
    _built(Branchline, "branch"),
    Planned("ratrace", "Rat-race coupler", "coupler", "180 degree hybrid, 1.5 wavelength ring",
            "Balanced mixers, sum and difference", "ratrace", priority=2, needs="a curved line part", ports=4),
    Planned("coupled", "Coupled-line coupler", "coupler", "Quarter-wave directional coupler, 6-25 dB",
            "Power monitoring, sampling ports", "cpl", priority=2, ports=4),
    Planned("lange", "Lange coupler", "coupler", "Interdigitated 3 dB, bond wires", "Octave-band 3 dB quadrature",
            "lange", priority=3, needs="multi-conductor coupled lines and bond wires", ports=4),
    # ---- dividers
    Planned("wilkinson", "Wilkinson divider", "divider", "Equal or unequal, 1 to 4 sections",
            "Splitting with isolated, matched outputs", "wilk", priority=2, ports=3),
    _built(TeeDivider, "tee"),
    Planned("gysel", "Gysel divider", "divider", "Loads to ground, high power", "High-power combining", "gysel",
            priority=3, needs="a curved line part", ports=3),
    # ---- filters
    Designer("hairpin", "Hairpin bandpass", "filter", "Tapped, N resonators, gap per pair",
             "Compact narrowband bandpass; space mapping with its circuit", "filter_hairpin", "hairpin", ports=2),
    Planned("edge", "Edge-coupled bandpass", "filter", "Parallel coupled half-wave resonators",
            "The textbook bandpass, 5-20 % bandwidth", "edge", priority=2),
    _built(SteppedImpedanceLowpass, "stepped"),
    _built(ShortedStubBandpass, "stubbp"),
    Planned("interdigital", "Interdigital bandpass", "filter", "Quarter-wave fingers",
            "Compact, clean second harmonic", "inter", priority=3, needs="multi-conductor coupled lines"),
    Planned("diplexer", "Hairpin diplexer", "filter", "Two hairpin channels at a junction", "Two bands on one antenna",
            "diplex", status=IN_PROGRESS, ports=3),
    # ---- feed networks
    Planned("corporate", "Corporate feed 1:N", "feed", "Equal phase tree for patch arrays", "Feeding 2-16 elements",
            "corp", priority=2, ports=5),
    Planned("butler", "Butler matrix 4x4", "feed", "Hybrids, crossovers, phase shifters", "Switched-beam antennas",
            "butler", priority=3, ports=8),
    # ---- misc
    Planned("biastee", "Bias tee", "other", "Quarter-wave line with a radial stub", "DC feed of amplifiers",
            "bias", priority=2, needs="a radial stub part", ports=3),
]


def structure(key: str) -> Structure:
    for s in CATALOG:
        if s.key == key:
            return s
    raise KeyError(f"No structure '{key}'; available: {', '.join(s.key for s in CATALOG)}")


def icon(s: Structure) -> str:
    return ICONS.get(getattr(s, "icon", ""), "")


def create_structure(key: str, values: dict | None = None, substrate: Substrate | None = None, **kw) -> Result:
    s = structure(key)
    if s.designer:
        raise ValueError(f"{s.title} has its own designer (Tools menu); use it instead")
    return s.create(values, substrate, **kw)


__all__ = [
    "AVAILABLE",
    "CATALOG",
    "CATEGORIES",
    "IN_PROGRESS",
    "PLANNED",
    "Field",
    "Result",
    "Structure",
    "Substrate",
    "Synthesis",
    "Variable",
    "create_structure",
    "icon",
    "structure",
]
