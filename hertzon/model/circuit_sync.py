"""A circuit recognized from a 3D design (Create Circuit) and its 3D design as one linked pair.

- calibrate(): fits correction factors of the circuit (Cal_Er on the substrate permittivity, Cal_L on the line
  lengths, Cal_W on the line widths) to the solved S-parameters of the 3D design. The factors are design
  variables that multiply the dimensions in the part properties (``MS3_L*Cal_L``), so the dimension variables
  stay the physical sizes shared with the 3D design, and tuning in the circuit keeps the correction.
- compare(): what differs between the circuit and its 3D design now: variables of the same name with other
  values, dimensions of the same element, elements only on one side (a changed topology).
- apply(): takes each difference in the chosen direction (to the circuit or to the 3D design); a topology
  difference is taken to the circuit by recognizing the circuit again (rebuild()).
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field

import numpy as np

from . import circuit as CK
from . import circuit_from_3d as CF
from .design import Design, Project

CAL = {"Cal_Er": "substrate permittivity", "Cal_L": "line lengths", "Cal_W": "line widths"}
DEFAULT_CAL = ("Cal_L", "Cal_W")     # well determined together; Cal_Er and Cal_W overlap (both set impedance and
#                                     effective permittivity), measured on the Design Wizard filters
LENGTH_PROPS = ("L",)
WIDTH_PROPS = ("W", "W1", "W2", "W3", "W4")
TO_CIRCUIT, TO_3D = "to_circuit", "to_3d"


class SyncError(Exception):
    pass


def source(project: Project, circuit: Design) -> Design:
    """The 3D design the circuit was recognized from."""
    name = circuit.settings.get(CF.SOURCE, "")
    if not name:
        raise SyncError(f"{circuit.name} was not made with Create Circuit from a 3D design")
    try:
        d = project.design(name)
    except KeyError:
        raise SyncError(f"{circuit.name}: its 3D design '{name}' is not in the project") from None
    if getattr(d, "kind", "3d") == "circuit":
        raise SyncError(f"'{name}' is not a 3D design")
    return d


def circuits_of(project: Project, design: Design) -> list[Design]:
    """The circuits recognized from a 3D design, the newest last."""
    return [d for d in project.designs if getattr(d, "kind", "3d") == "circuit"
            and d.settings.get(CF.SOURCE) == design.name]


# ------------------------------------------------------------------------------------------------ factors
def _factor_of(comp: CK.Component, prop: str) -> str | None:
    if comp.type == "SUBST" and prop == "Er":
        return "Cal_Er"
    if comp.type in CK.LINE_TYPES and prop in LENGTH_PROPS:
        return "Cal_L"
    if comp.type in CK.LINE_TYPES and prop in WIDTH_PROPS:
        return "Cal_W"
    return None


def _wrap(text: str, factor: str) -> str:
    t = text.strip()
    return f"{t}*{factor}" if re.fullmatch(r"[\w.]+", t) else f"({t})*{factor}"


def strip_factor(text: str) -> str:
    """The property without its correction factor: 'MS3_L*Cal_L' -> 'MS3_L'."""
    t = text.strip()
    m = re.fullmatch(r"\((.*)\)\*Cal_(?:Er|L|W)", t) or re.fullmatch(r"(.*)\*Cal_(?:Er|L|W)", t)
    return m.group(1).strip() if m else t


def add_factors(circuit: Design, names=tuple(CAL)) -> list[str]:
    """Multiply the substrate permittivity, line lengths and widths by the factors (design variables, 1 if new);
    properties that have their factor already stay. Returns the factors added."""
    added = []
    for k in names:
        if k not in circuit.variables:
            circuit.variables[k] = "1"
            added.append(k)
    for c in circuit.components:
        for prop, v in list(c.props.items()):
            f = _factor_of(c, prop)
            if f in names and isinstance(v, str) and v.strip() and f not in v:
                c.props[prop] = _wrap(v, f)
    return added


def _fem(project: Project, design: Design, solution: str | None):
    from ..solver import circuit as SC
    if not project.path:
        raise SyncError("Save the project first: the calibration reads the saved 3D solution")
    sols = SC.em_solutions(project, design.name)
    if not sols:
        raise SyncError(f"{design.name} has no solved S-parameters yet: analyze it first")
    sol = solution or next((s for s in sols if "LastAdaptive" not in s), sols[0])
    probe = CK.Component("FEM", "EM", 0, 0, 0, {"Design": design.name, "Solution": sol})
    try:
        return SC.load_em(project, probe), sol
    except SC.CircuitError as e:
        raise SyncError(str(e)) from None


def _port_map(project: Project, circuit: Design, design: Design, fem_names: list[str]) -> list[int]:
    """For each power port of the circuit (in its S order) the index of the 3D port with the same number."""
    from ..solver import circuit as SC
    ports, _, _ = SC.circuit_ports(project, circuit)
    number = {}
    for k, e in enumerate(design.ports()):
        number[e.name] = str(e.props.get("PortNumber", k + 1)).strip()
    idx = []
    for c in ports:
        num = str(c.props.get("Num", "")).strip()
        j = next((j for j, n in enumerate(fem_names) if number.get(n, n) == num), None)
        if j is None:
            raise SyncError(f"{c.name} (port {num}) has no port of the same number in the 3D solution "
                            f"({', '.join(fem_names)})")
        idx.append(j)
    return idx


@dataclass
class Calibration:
    solution: str
    values: dict
    rms_before: float
    rms_after: float
    points: int
    added: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def calibrate(project: Project, circuit: Design, names=DEFAULT_CAL, solution: str | None = None,
              bounds=(0.7, 1.3), max_points: int = 101, data=None) -> Calibration:
    """Fit the correction factors of the circuit to the solved S-parameters of its 3D design (least squares
    on the complex S-parameters over the solved frequencies); the factors are set as design variables.
    data: other S-parameters of the 3D design (an object with freq, S and the port names), e.g. a Touchstone
    file of it."""
    from scipy.optimize import least_squares

    from ..solver import circuit as SC
    design = source(project, circuit)
    if data is not None:
        nd, sol = data, "given data"
    else:
        nd, sol = _fem(project, design, solution)
    idx = _port_map(project, circuit, design, list(nd.names))
    f = np.asarray(nd.freq, float)
    keep = np.unique(np.linspace(0, len(f) - 1, min(max_points, len(f))).round().astype(int))
    f = f[keep]
    S_fem = nd.S[keep][:, idx][:, :, idx]
    added = add_factors(circuit, names)
    names = list(names)
    ev = circuit.evaluator(project)
    x0 = np.array([min(max(ev.value(k), bounds[0]), bounds[1]) for k in names])

    def model(x):
        for k, v in zip(names, x):
            circuit.variables[k] = f"{v:.8g}"
        return SC.analyze(project, circuit, f).S

    def resid(x):
        d = (model(x) - S_fem).ravel()
        return np.concatenate([d.real, d.imag])

    def rms(x):
        return float(np.sqrt(np.mean(np.abs(model(x) - S_fem) ** 2)))
    try:
        before = rms(np.ones(len(names)))
        r = least_squares(resid, x0, bounds=(np.full(len(names), bounds[0]), np.full(len(names), bounds[1])),
                          diff_step=1e-3, x_scale=0.05, max_nfev=60 * len(names))
        x = r.x
        after = rms(x)
        if after > before:                   # never worse than no correction
            x, after = np.ones(len(names)), before
    except SC.CircuitError as e:
        raise SyncError(f"{circuit.name}: {e}") from None
    for k, v in zip(names, x):
        circuit.variables[k] = f"{v:.6g}"
    warns = [f"{k} = {v:.4g} is at the limit of the fit ({bounds[0]:g} to {bounds[1]:g}): the correction stands in "
             "for something the circuit models do not have (see the notes of Create Circuit, e.g. neglected "
             f"coupling), not only for the {CAL[k]}" for k, v in zip(names, x) if min(v - bounds[0], bounds[1] - v) < 1e-3]
    return Calibration(sol, {k: float(v) for k, v in zip(names, x)}, before, after, len(f), added, warns)


# ------------------------------------------------------------------------------------------------ compare
@dataclass
class Difference:
    kind: str                  # "variable", "dimension", "topology"
    name: str                  # variable, "Element.Prop" or element
    circuit: str               # value on the circuit side ("" if none)
    model: str                 # value on the 3D side
    directions: tuple          # the directions it can be taken in
    note: str = ""
    target: str = ""           # dimension: the 3D variable the dimension is (to_3d writes it)


def _value(ev, text: str, length: bool) -> float | None:
    try:
        return ev.length(text) * 1e3 if length else ev.value(text)
    except Exception:  # noqa: BLE001
        return None


def _neutral(ev_design: Design, project: Project):
    """An evaluator of the design with the correction factors at 1."""
    d = copy.copy(ev_design)
    d.variables = {**ev_design.variables, **{k: "1" for k in CAL}}
    return d.evaluator(project)


def recognized(project: Project, circuit: Design) -> tuple[Design, list[Design]]:
    """The circuit as recognized from the 3D design now and the 3D design blocks it needs (neither is added to
    the project)."""
    design = source(project, circuit)
    board = CF.extract(project, design)
    rec = CF.recognize(board)
    d, table = CF.to_circuit(project, design, rec, circuit.name, blocks=True)
    d.settings[CF.SOURCE] = design.name
    d.settings[CF.TABLE] = json.dumps(table)
    return d, rec.blocks


def _parts(d: Design) -> dict[str, CK.Component]:
    return {c.name: c for c in d.components if c.type not in ("LABEL", "GND", "SUBST")}


def compare(project: Project, circuit: Design, again: Design | None = None) -> list[Difference]:
    """The differences between the circuit and its 3D design (again: the circuit recognized now, if known)."""
    design = source(project, circuit)
    again = again or recognized(project, circuit)[0]
    out: list[Difference] = []
    evc = _neutral(circuit, project)
    ev3 = design.evaluator(project)
    for k in sorted(set(circuit.variables) & set(design.variables)):
        if k in CAL:
            continue
        a, b = evc.value(k) if _ok(evc, k) else None, ev3.value(k) if _ok(ev3, k) else None
        if a is None or b is None or _same(a, b):
            continue
        out.append(Difference("variable", k, circuit.variables[k], design.variables[k], (TO_CIRCUIT, TO_3D)))
    mine, theirs = _parts(circuit), _parts(again)
    for name, c in mine.items():
        r = theirs.get(name)
        if r is None or r.type != c.type:
            continue
        for prop, tr in r.props.items():
            tc = c.props.get(prop)
            if tc is None or prop in ("Subst", "Thumbnail") or not isinstance(tr, str):
                continue
            if c.type == "EM":
                continue
            plain = strip_factor(tc)
            if plain == tr.strip():
                continue                       # the same text: a shared variable (compared above) or number
            length = c.type in CK.LINE_TYPES or c.type == "MVIA"
            vc, vr = _value(evc, plain, length), _value(ev3, tr, length)
            if vc is None or vr is None:
                continue
            target = tr.strip() if tr.strip() in design.variables else ""
            unit = " mm" if length else ""
            if _same(vc, vr):
                if target:                     # the same size, but no longer the shared variable
                    out.append(Difference("dimension", f"{name}.{prop}", f"{plain} = {vc:.6g}{unit}",
                                          f"{tr} = {vr:.6g}{unit}", (TO_CIRCUIT,),
                                          "the same size; to the circuit links it to the 3D variable again", target))
                continue
            out.append(Difference("dimension", f"{name}.{prop}", f"{plain} = {vc:.6g}{unit}",
                                  f"{tr} = {vr:.6g}{unit}", (TO_CIRCUIT, TO_3D) if target else (TO_CIRCUIT,),
                                  "" if target else "the 3D size is no variable: only to the circuit",
                                  target))
    for name in sorted(set(mine) - set(theirs)):
        c = mine[name]
        if c.type in ("PORT",) or c.type in CK.NONLINEAR_TYPES:
            continue
        out.append(Difference("topology", name, c.type, "", (TO_CIRCUIT,),
                              "only in the circuit: the 3D design has no copper for it; to the circuit removes it "
                              "(recognized again), Create 3D Layout builds a new 3D design from the circuit"))
    for name in sorted(set(theirs) - set(mine)):
        out.append(Difference("topology", name, "", theirs[name].type, (TO_CIRCUIT,),
                              "only in the 3D design: taken to the circuit by recognizing it again"))
    for name in sorted(set(mine) & set(theirs)):
        if mine[name].type != theirs[name].type:
            out.append(Difference("topology", name, mine[name].type, theirs[name].type, (TO_CIRCUIT,),
                                  "another element in the 3D design: taken to the circuit by recognizing it again"))
    return out


def _ok(ev, k) -> bool:
    try:
        ev.value(k)
        return True
    except Exception:  # noqa: BLE001
        return False


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def apply(project: Project, circuit: Design, choices: list[tuple[Difference, str]]) -> list[str]:
    """Take each difference in its chosen direction. A topology difference to the circuit recognizes the
    circuit again (after the other changes to the 3D design). Returns what was done."""
    design = source(project, circuit)
    done = []
    rebuild_needed = False
    for diff, direction in choices:
        if direction not in diff.directions:
            continue
        if diff.kind == "variable":
            if direction == TO_CIRCUIT:
                circuit.variables[diff.name] = design.variables[diff.name]
            else:
                design.variables[diff.name] = circuit.variables[diff.name]
            done.append(f"{diff.name} = {(design if direction == TO_CIRCUIT else circuit).variables[diff.name]} "
                        f"{'in the circuit' if direction == TO_CIRCUIT else 'in the 3D design'}")
        elif diff.kind == "dimension":
            part, prop = diff.name.rsplit(".", 1)
            c = next(x for x in circuit.components if x.name == part)
            if direction == TO_CIRCUIT:
                new = diff.model.split(" = ")[0]
                f = _factor_of(c, prop)
                c.props[prop] = _wrap(new, f) if f and f in circuit.variables else new
                done.append(f"{diff.name} = {new} in the circuit")
            else:
                evc = _neutral(circuit, project)
                v = _value(evc, strip_factor(c.props[prop]), True)
                design.variables[diff.target] = f"{v:.9g}mm"
                done.append(f"{diff.target} = {v:.9g}mm in the 3D design")
        elif diff.kind == "topology" and direction == TO_CIRCUIT:
            rebuild_needed = True
    if rebuild_needed:
        done += rebuild(project, circuit)
    return done


def rebuild(project: Project, circuit: Design) -> list[str]:
    """Recognize the circuit again from its 3D design in place: its parts and connections are replaced, its
    variables (also the correction factors), setups and reports stay. 3D design blocks with the same copper and
    ports as an existing block of the 3D design are reused."""
    design = source(project, circuit)
    again, blocks = recognized(project, circuit)
    existing = [d for d in project.designs if d.settings.get(CF.EM_OF) == design.name]
    rename = {}
    added = []
    for b in blocks:
        same = next((d for d in existing if _block_key(d) == _block_key(b)), None)
        if same is not None:
            rename[b.name] = same.name
        else:
            project.designs.append(b)
            added.append(b.name)
    for c in again.components:
        if c.type == "EM" and c.props.get("Design") in rename:
            c.props["Design"] = rename[c.props["Design"]]
    cal = [k for k in CAL if k in circuit.variables]
    for k, v in again.variables.items():
        circuit.variables.setdefault(k, v)
    circuit.components = again.components
    circuit.wires = []
    circuit.settings[CF.TABLE] = again.settings[CF.TABLE]
    if cal:
        add_factors(circuit, tuple(cal))
    out = [f"{circuit.name}: recognized again from {design.name}"]
    if added:
        out.append("new 3D design blocks (analyze them): " + ", ".join(added))
    return out


def _block_key(d: Design):
    def props(o):
        return tuple(sorted(o.history[0].props.items())) if o.history else ()
    return (tuple((o.name, props(o)) for o in d.objects), tuple(e.props.get("IntegrationLine", "") for e in
                                                                 d.excitations))
