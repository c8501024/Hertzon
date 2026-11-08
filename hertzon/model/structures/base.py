"""Building blocks of the design wizard catalogue: specification fields, the substrate, the synthesized variables
and the Structure base class that turns them into designs.

A structure of the catalogue knows how to
  * describe its specification (fields with unit, default and limits),
  * synthesize start values from closed formulas (synthesis),
  * build a circuit design with named design variables (circuit),
  * build the 3D design with the same variable names (design3d; by default the generic 3D layout of the circuit),
  * state its goals (goals) and the sweep they are evaluated on (sweep).
create() puts these together: the circuit, its 3D layout and an optimization on the circuit with multi-fidelity
switched on (the circuit is the coarse model, the 3D layout the fine one, the same variable names).

No Qt here: used by the wizard dialog, the MCP server and scripts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import schema
from ..design import Design, Optimization, Project, circuit_setup
from ..microstrip import C0, analyze, synthesize

AVAILABLE, IN_PROGRESS, PLANNED = "available", "in progress", "planned"


@dataclass
class Field:
    """One entry of the specification."""
    key: str
    label: str
    default: float
    unit: str = ""                      # shown next to the value: "GHz", "dB", "ohm", "mm", "%", ""
    lo: float = 0.0
    hi: float = 1e9
    decimals: int = 3
    integer: bool = False
    help: str = ""


@dataclass
class Substrate:
    """The board every planar structure is built on (shared by all structures, remembered by the wizard)."""
    material: str = "Rogers RO4350B (tm)"
    er: float = 3.66
    tand: float = 0.0037
    h: float = 0.508                    # mm
    t: float = 35.0                     # um copper
    sigma: float = 5.8e7                # S/m
    min_feature: float = 0.1            # mm, smallest width or gap the optimizer may reach

    def width(self, z0: float, f_ghz: float) -> float:
        """Microstrip width (mm) of the impedance z0 at f_ghz."""
        return 1e3 * synthesize(z0, self.h * 1e-3, self.er, self.t * 1e-6, f_ghz * 1e9, self.tand, self.sigma)

    def eeff(self, w_mm: float, f_ghz: float) -> float:
        return analyze(w_mm * 1e-3, self.h * 1e-3, self.er, self.t * 1e-6, f_ghz * 1e9, self.tand, self.sigma)[1]

    def lambda_g(self, w_mm: float, f_ghz: float) -> float:
        """Guided wavelength (mm) of a strip of width w_mm at f_ghz."""
        return C0 / (f_ghz * 1e9) / math.sqrt(self.eeff(w_mm, f_ghz)) * 1e3


@dataclass
class Variable:
    """A synthesized design variable: start value, range of the optimization and what it is."""
    name: str
    value: float
    meaning: str
    unit: str = "mm"
    lo: float | None = None
    hi: float | None = None
    include: bool = True                # optimized by default

    def text(self, v: float | None = None) -> str:
        v = self.value if v is None else v
        return f"{v:.6g}{self.unit}" if self.unit else f"{v:.6g}"


@dataclass
class Synthesis:
    variables: list[Variable] = field(default_factory=list)
    note: str = ""                      # how the start values came about (shown under the table)
    warnings: list[str] = field(default_factory=list)

    def value(self, name: str) -> float:
        return next(v.value for v in self.variables if v.name == name)


@dataclass
class Result:
    """What create() made: the designs (the one to show first), the optimization and the synthesis."""
    designs: list[Design]
    main: str
    optimization: str
    synthesis: Synthesis
    warnings: list[str] = field(default_factory=list)
    materials: list = field(default_factory=list)       # substrate materials the designs need, new to the project


def goal(calc: str, start: float, stop: float, cond: str, value: float, weight: float = 1.0) -> dict:
    """An optimization goal on a frequency range in GHz."""
    return {"calc": calc, "start": f"{start:.6g}GHz", "stop": f"{stop:.6g}GHz", "cond": cond, "value": f"{value:g}",
            "weight": f"{weight:g}"}


def band(f0: float, bw_pct: float) -> tuple[float, float]:
    return f0 * (1 - bw_pct / 200), f0 * (1 + bw_pct / 200)


class Structure:
    """Base class of a catalogue entry. Subclasses set the class attributes and override synthesis, circuit and,
    where the generic layout of the circuit does not fit, design3d."""
    key = ""
    title = ""
    category = ""                       # one of CATEGORIES (catalog.py)
    description = ""                    # one line on the card
    use = ""                            # typical use (detail panel)
    status = PLANNED
    ports = 2
    fields: tuple[Field, ...] = ()
    needs = ""                          # planned: what is missing before it can be built
    designer = ""                       # an existing dialog that builds it (Tools menu action), see catalog.py
    coarse = "Circuit"                  # coarse model of the multi-fidelity optimization (shown in the wizard)
    optimizer = "Conjugate Gradient"    # algorithm of the optimization (a sampled start for rugged goals)
    kind = "circuit"                    # "circuit": circuit + 3D layout; "3d": a 3D design only (antennas)

    # ---- specification
    def defaults(self) -> dict[str, float]:
        return {f.key: f.default for f in self.fields}

    def spec(self, values: dict | None = None) -> dict[str, float]:
        """The specification with defaults filled in, checked against the field limits."""
        out = self.defaults()
        for k, v in (values or {}).items():
            fld = next((f for f in self.fields if f.key == k), None)
            if fld is None:
                raise ValueError(f"{self.title}: unknown field '{k}' (fields: {', '.join(self.defaults())})")
            v = float(v)
            if not fld.lo <= v <= fld.hi:
                raise ValueError(f"{self.title}: {fld.label} must be between {fld.lo:g} and {fld.hi:g} {fld.unit}")
            out[k] = round(v) if fld.integer else v
        return out

    # ---- the parts a structure provides
    def synthesis(self, spec: dict, sub: Substrate) -> Synthesis:
        raise NotImplementedError

    def circuit(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        raise NotImplementedError

    def goals(self, spec: dict) -> list[dict]:
        raise NotImplementedError

    def adapt(self, spec: dict) -> float | None:
        """Frequency (GHz) of the adaptive mesh of the 3D design, None for the top of the sweep. A filter refines
        in its passband: in the stopband the fields do not reach the resonators."""
        return None

    def sweep(self, spec: dict) -> tuple[float, float, int]:
        """(start GHz, stop GHz, points) of the analysis; the goals lie inside it."""
        f0 = spec["f0"]
        return 0.5 * f0, 1.5 * f0, 201

    def model3d(self, spec: dict, sub: Substrate, syn: Synthesis, name: str) -> Design:
        """The 3D design of a structure without a circuit (kind "3d"), with its setup and variables."""
        raise NotImplementedError

    def design3d(self, project: Project, circuit: Design, spec: dict, sub: Substrate, name: str
                 ) -> tuple[Design, list[str]]:
        """The 3D design: by default the generic 3D layout of the circuit (straight lines, junctions and ports
        on the board edge), linked to the circuit so that 'Update 3D Layout' and 'Update Schematic' work."""
        from ..circuit_layout import layout_design
        d, warnings = layout_design(project, circuit, name)
        return d, warnings

    # ---- putting it together
    def create(self, values: dict | None = None, sub: Substrate | None = None, name: str = "",
               range_pct: float = 20.0, multi_fidelity: bool = True, project: Project | None = None) -> Result:
        """Circuit, 3D design and the optimization (on the circuit, multi-fidelity with the 3D design as the fine
        model). The names are made unique against project; nothing is added to it."""
        if self.status != AVAILABLE:
            raise ValueError(f"{self.title} is not available yet" + (f": {self.needs}" if self.needs else ""))
        sub = sub or Substrate()
        spec = self.spec(values)
        syn = self.synthesis(spec, sub)
        taken = {d.name for d in project.designs} if project else set()
        base = unique(name or self.default_name(spec), taken)
        if self.kind == "3d":
            return self._create3d(spec, sub, syn, base, range_pct)
        circ = self.circuit(spec, sub, syn, unique(f"{base}_Circuit", taken | {base}))
        apply_ranges(syn, sub, range_pct)
        tmp = Project(base)
        before = list(project.materials) if project is not None else []
        tmp.materials = list(before)
        tmp.designs = [circ]
        d3, warnings = self.design3d(tmp, circ, spec, sub, base)
        d3.name = base
        a, b, n = self.sweep(spec)
        st = circ.setups[0]
        st.sweeps[0].props.update(Distribution="Linear Count", Start=f"{a:.6g}GHz", Stop=f"{b:.6g}GHz", Count=str(n))
        fine_sweep(d3, spec, a, b, self.adapt(spec))
        op = Optimization("Optimization1", "Setup1 : Sweep",
                          props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer=self.optimizer))
        if multi_fidelity:
            op.name = "MultiFidelity1"
            op.props.update(MultiFidelity="True", FineModel=d3.name, FineSolution="Setup1 : Sweep")
        for v in syn.variables:
            op.variables.append({"name": v.name, "include": v.include, "min": v.text(v.lo), "max": v.text(v.hi)})
        op.goals = self.goals(spec)
        circ.optimetrics.append(op)
        known = {m.name for m in before}
        return Result([circ, d3], circ.name, op.name, syn, list(syn.warnings) + list(warnings),
                      [m for m in tmp.materials if m.name not in known])

    def _create3d(self, spec: dict, sub: Substrate, syn: Synthesis, base: str, range_pct: float) -> Result:
        """A structure without a circuit: the 3D design and an optimization on its adaptive solution at the design
        frequency (one solve per evaluation; the start values are close, so the derivative tuning model needs a
        few). Multi-fidelity does not apply."""
        d3 = self.model3d(spec, sub, syn, base)
        d3.name = base
        apply_ranges(syn, sub, range_pct)
        op = Optimization("Optimization1", "Setup1 : LastAdaptive",
                          props=dict(schema.defaults(schema.OPTIMIZATION), Optimizer=self.optimizer))
        for v in syn.variables:
            op.variables.append({"name": v.name, "include": v.include, "min": v.text(v.lo), "max": v.text(v.hi)})
        op.goals = self.goals(spec)
        d3.optimetrics.append(op)
        return Result([d3], d3.name, op.name, syn, list(syn.warnings), [])

    def default_name(self, spec: dict) -> str:
        f = spec.get("f0")
        return f"{self.key.capitalize()}_{f:g}GHz" if f else self.key.capitalize()


def unique(name: str, taken: set[str]) -> str:
    out, i = name, 1
    while out in taken:
        i += 1
        out = f"{name}_{i}"
    return out


def apply_ranges(syn: Synthesis, sub: Substrate, pct: float) -> None:
    """Fill the open ranges: start value +- pct %, a length or width never below the smallest feature."""
    for v in syn.variables:
        lo = v.value * (1 - pct / 100) if v.value >= 0 else v.value * (1 + pct / 100)
        hi = v.value * (1 + pct / 100) if v.value >= 0 else v.value * (1 - pct / 100)
        if v.unit == "mm":
            lo = max(lo, sub.min_feature)
            hi = max(hi, lo * 1.01)
        if v.lo is None:
            v.lo = lo
        if v.hi is None:
            v.hi = hi


def fine_sweep(d3: Design, spec: dict, start: float, stop: float, adapt: float | None = None) -> None:
    """The 3D design sweeps the same range as the circuit (interpolating); it is solved adaptively at adapt
    (GHz), by default the top of the range (as every generated layout)."""
    if not d3.setups:
        return
    st = d3.setups[0]
    st.props["Frequency"] = f"{adapt or stop:.6g}GHz"
    for sw in st.sweeps:
        sw.props.update(SweepType="Interpolating", Distribution="Linear Count", Start=f"{start:.6g}GHz",
                        Stop=f"{stop:.6g}GHz", Count="201")


def new_circuit(name: str, sub: Substrate, syn: Synthesis, extra: dict[str, str] | None = None) -> Design:
    """An empty circuit design with the substrate variables and the synthesized variables."""
    d = Design(name=name, kind="circuit")
    d.variables = {"h": f"{sub.h:g}mm", "t_cu": f"{sub.t:g}um"}
    d.variables.update({v.name: v.text() for v in syn.variables})
    d.variables.update(extra or {})
    d.setups.append(circuit_setup())
    return d


def substrate_part(sub: Substrate, x: int = -100, y: int = -200):
    from ..circuit import Component as C
    return C("Sub1", "SUBST", x, y, 0, {"Er": f"{sub.er:g}", "H": "h", "T": "t_cu", "TanD": f"{sub.tand:g}",
                                        "Cond": f"{sub.sigma:g}"})


def top_view(design: Design, project: Project | None = None) -> tuple[list, list]:
    """Top view of a planar 3D design (mm) for the wizard preview: (board outline, copper rectangles), from the
    rectangles in the xy plane (the traces of a layout); the ground plane is the board."""
    ev = design.evaluator(project)
    board, copper = [], []
    for o in design.objects:
        if not o.history or o.history[0].kind != "CreateRectangle" or o.history[0].props.get("Axis") != "Z":
            continue
        p = o.history[0].props
        try:
            x, y, _ = ev.point(p["Position"])
            dx, dy = ev.length(p["XSize"]), ev.length(p["YSize"])
        except Exception:  # noqa: BLE001
            continue
        rect = [(1e3 * a, 1e3 * b) for a, b in ((x, y), (x + dx, y), (x + dx, y + dy), (x, y + dy))]
        if o.name.startswith("Ground"):
            board = rect
        else:
            copper.append(rect)
    return board, copper


def side_view(design: Design, project: Project | None = None) -> tuple[list, list]:
    """Side view (x, z in mm) of a wire antenna for the wizard preview: (nothing, conductor rectangles) from the
    cylinders along z and the planes in the xy plane (drawn a little thick)."""
    ev = design.evaluator(project)
    shapes, planes = [], []
    for o in design.objects:
        if not o.history:
            continue
        h = o.history[0]
        p = h.props
        try:
            if h.kind == "CreateCylinder" and p.get("Axis") == "Z":
                x, _, z = ev.point(p["CenterPosition"])
                r, ht = ev.length(p["Radius"]), ev.length(p["Height"])
                shapes.append((x - r, x + r, min(z, z + ht), max(z, z + ht)))
            elif h.kind == "CreateRectangle" and p.get("Axis") == "Z":
                x, _, z = ev.point(p["Position"])
                planes.append((x, x + ev.length(p["XSize"]), z))
        except Exception:  # noqa: BLE001, S112  (an expression the preview cannot evaluate: left out)
            continue
    zs = [v for s in shapes for v in s[2:]] + [p[2] for p in planes]
    th = 0.01 * (max(zs) - min(zs)) if len(zs) > 1 else 0.001
    shapes += [(a, b, z - th, z) for a, b, z in planes]
    rects = [[(1e3 * a, 1e3 * c), (1e3 * b, 1e3 * c), (1e3 * b, 1e3 * d), (1e3 * a, 1e3 * d)] for a, b, c, d in shapes]
    return [], rects
