"""Translate a design into an EMerge simulation and run one analysis setup.

Runs in its own process (``python -m hertzon.solver.run``). Progress and results
are reported as JSON lines on stdout prefixed with ``@@HZN``; result files are
written into the setup's results folder.
"""
from __future__ import annotations

import inspect

import json
import math
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..model.design import Project, Design, Setup, Assignment, FaceRef
from ..model.expr import Evaluator
from ..model.materials import EMERGE_DISPERSION, is_conductor
from ..model import schema
from ..kernel import occ as K
from . import facematch
from . import sparta_setup
from .results import ResultStore, port_entries, deembed_renormalize
from .emerge_compat import amr as _amr

C0 = 299792458.0
FLOQUET_MODES = ["TE00", "TM00"]     # modes of a Floquet port in S-parameter order
FF_OFFSET = 1 / 8     # far-field box of a scattering problem: distance from the scatterers in wavelengths
TAG = "@@HZN "


_EMIT_LOCK = threading.Lock()       # sweep progress is also reported from the solver threads


def emit(kind: str, **data) -> None:
    line = TAG + json.dumps({"type": kind, **data}, default=float) + "\n"
    with _EMIT_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def info(msg: str) -> None:
    emit("log", level="info", msg=msg)


def warn(msg: str) -> None:
    emit("log", level="warning", msg=msg)


class SolveAbort(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# Far field helpers
# --------------------------------------------------------------------------------------------
def box_quadrature(bounds, panel: float, skip=()) -> tuple[np.ndarray, np.ndarray]:
    """Gauss points and outward area vectors (3, N) on the faces of the box (x0, y0, z0, x1, y1, z1):
    4 x 4 Gauss-Legendre points on panels of at most `panel`; faces (axis, 0 low / 1 high) in skip are left out."""
    g, gw = np.polynomial.legendre.leggauss(4)
    pts, wts = [], []
    for ax in range(3):
        u, v = [i for i in range(3) if i != ax]
        rule = []
        for i in (u, v):
            a, b = float(bounds[i]), float(bounds[i + 3])
            n = max(1, math.ceil((b - a) / panel))
            e = np.linspace(a, b, n + 1)
            h = np.diff(e) / 2
            rule.append(((e[:-1] + h)[:, None] + h[:, None] * g).ravel())
            rule.append((h[:, None] * gw).ravel())
        U, V = np.meshgrid(rule[0], rule[2], indexing="ij")
        A = np.outer(rule[1], rule[3]).ravel()
        for side in (0, 1):
            if (ax, side) in skip:
                continue
            P = np.empty((3, U.size))
            P[u], P[v], P[ax] = U.ravel(), V.ravel(), bounds[ax + 3 * side]
            N = np.zeros((3, U.size))
            N[ax] = A if side else -A
            pts.append(P)
            wts.append(N)
    return np.concatenate(pts, axis=1), np.concatenate(wts, axis=1)


# --------------------------------------------------------------------------------------------
# Frequency helpers
# --------------------------------------------------------------------------------------------
def adaptive_frequencies(setup: Setup, ev: Evaluator) -> list[float]:
    p = setup.props
    mode = p.get("SolutionFrequencyMode", "Single")
    if mode == "Multi-Frequencies":
        return sorted(ev.freq(x) for x in p.get("MultiFrequencies", "").split(",") if x.strip())
    if mode == "Broadband":
        return [ev.freq(p["LowFrequency"]), ev.freq(p["HighFrequency"])]
    return [ev.freq(p["Frequency"])]


def sweep_frequencies(props: dict[str, str], ev: Evaluator) -> np.ndarray:
    dist = props.get("Distribution", "Linear Step")
    if dist == "Single Points":
        return np.array(sorted(ev.freq(x) for x in props.get("Points", "").split(",") if x.strip()))
    f1, f2 = ev.freq(props["Start"]), ev.freq(props["Stop"])
    if dist == "Linear Count":
        return np.linspace(f1, f2, max(2, ev.integer(props["Count"])))
    if dist == "Log Scale":
        n = max(2, int(round(math.log10(f2 / f1) * ev.integer(props["SamplesPerDecade"]))) + 1)
        return np.logspace(math.log10(f1), math.log10(f2), n)
    step = ev.freq(props["Step"])
    if step <= 0:
        raise ValueError("Sweep step must be positive")
    n = int(math.floor((f2 - f1) / step + 1e-9)) + 1
    return f1 + step * np.arange(n)


def lowest_frequency(design: Design, setup: Setup, ev: Evaluator) -> float:
    fs = adaptive_frequencies(setup, ev)
    for sw in setup.sweeps:
        if sw.props.get("Enabled", "True") == "True":
            try:
                fs.append(float(sweep_frequencies(sw.props, ev).min()))
            except Exception:  # noqa: BLE001
                pass
    return min(fs)


# --------------------------------------------------------------------------------------------
# Main runner
# --------------------------------------------------------------------------------------------
@dataclass
class Bridge:
    project: Project
    design: Design
    setup: Setup
    out: Path
    ev: Evaluator = None  # type: ignore[assignment]
    tess: dict = field(default_factory=dict)
    geos: dict = field(default_factory=dict)        # object name -> EMerge GeoObject
    voided: set = field(default_factory=set)        # PEC solids that back a wave port (made voids)
    region_geo: object = None
    open_faces: object = None                       # FaceSelection of open boundary (far field)
    samples: dict = field(default_factory=dict)
    store: ResultStore = None  # type: ignore[assignment]
    t0: float = field(default_factory=time.time)
    profile: list = field(default_factory=list)
    touchstone_elements: list = field(default_factory=list)     # (boundary name, ComponentImpedance)
    cell: object = None                             # EMerge periodic cell of the Primary / Secondary pairs
    scan: tuple = (0.0, 0.0)                        # scan angles theta, phi of the periodic cell (degrees)
    sparta: bool = False                            # the matrix solver is SPARTA (set_sparta)
    sweep_solver: str = ""                          # SPARTA choice for the sweeps only (set_solver)

    def __post_init__(self):
        self.ev = self.design.evaluator(self.project)
        self.store = ResultStore(self.out)

    def step(self, name: str, t_start: float) -> None:
        self.profile.append({"task": name, "elapsed": time.time() - t_start, "total": time.time() - self.t0,
                             "memory": peak_memory()})
        self.store.write_json("profile.json", self.profile)

    # ----------------------------------------------------------------------------------------
    def run(self) -> None:
        try:
            import psutil
            info(f"Solver start: Python process running for {time.time() - psutil.Process().create_time():.1f} s")
        except Exception:  # noqa: BLE001
            pass
        t = time.time()
        import emerge as em  # heavy import, keep inside the worker
        info(f"Solver start: EMerge loaded in {time.time() - t:.1f} s")
        t = time.time()
        from .emerge_patches import apply as _patch_emerge
        _patch_emerge()
        info(f"Solver start: patches applied in {time.time() - t:.1f} s")
        # fingerprints before anything (auto open etc.) touches the design in memory
        self.keys = {"model_key": model_key(self.project, self.design, self.setup), "setup_key": setup_key(self.setup)}
        try:
            (self.out / "ABORT").unlink(missing_ok=True)     # left over from an aborted earlier run
        except OSError:
            pass
        if not os.environ.get("HERTZON_TUNING"):    # a tuning run leaves the solution of the setup as it is
            self.store.write_json("status.json", {"state": "running", "started": time.time()})
        if _first_run():
            note = "First start detected: compiling the solver kernels (once, a few minutes)"
            info(note)
            emit("progress", value=1, text=note)
        else:
            emit("progress", value=1, text="Starting EMerge")
        t = time.time()
        with em.Simulation(f"hertzon_{os.getpid()}", save_file=False) as m:
            info(f"Solver start: EMerge simulation ready in {time.time() - t:.1f} s")
            self.prepare(m)
            tune = os.environ.get("HERTZON_TUNING", "")
            if tune:
                self.run_tuning(json.loads(tune))
                return
            only = os.environ.get("HERTZON_ONLY_SWEEP", "")
            if only:
                self.solve_one_sweep(only)
                return
            if os.environ.get("HERTZON_MESH_ONLY") == "1":
                self.save_mesh()
                self.store.write_json("status.json", {"state": "meshed", "finished": time.time()})
                emit("progress", value=100, text="Initial mesh generated")
                emit("done", elapsed=time.time() - self.t0)
                hard_exit()
                return
            self.solve()

    def prepare(self, m) -> None:
        """Build geometry, materials, boundaries, excitations and the initial mesh on ``m``."""
        import emerge as em
        from .emerge_patches import apply as _patch_emerge
        _patch_emerge()
        self.em = em
        self.m = m
        t = time.time()
        _install_log_sink()
        threads = solver_threads(self.setup)
        global SOLVER_THREADS
        SOLVER_THREADS = threads
        import gmsh
        # gmsh meshes differently on every run when it uses several threads (the order in which
        # surfaces/volumes finish changes); one thread gives reproducible meshes and pass times
        gmsh.option.setNumber("General.NumThreads", 1)
        try:
            import numba
            numba.set_num_threads(min(threads, numba.config.NUMBA_NUM_THREADS))
        except Exception:  # noqa: BLE001
            pass
        # SPARTA's thread pool (rayon) reads this when the first solve starts it
        os.environ["RAYON_NUM_THREADS"] = str(threads)
        info(f"Using {threads} core(s)")
        self.probe()
        self.build_model()
        self.step("Geometry", t)
        t = time.time()
        m.commit_geometry()
        self.check_overlaps()
        emit("progress", value=8, text="Mapping boundaries to the solver model")
        self.samples = facematch.final_face_samples()
        self.assign_mesh_ops()
        self.set_frequencies_for_mesh()
        self.assign_boundaries()
        self.assign_excitations()
        self.set_solver()
        self.set_basis()
        self.step("Boundary setup", t)
        t = time.time()
        emit("progress", value=10, text="Initial mesh (lambda refinement)")
        keep_global_max_size(m)
        m.generate_mesh()
        info(f"Initial mesh: {m.mesh.n_tets} tetrahedra")
        self.step("Initial meshing", t)

    def solve(self) -> None:
        if self.setup.eigen or self.design.settings.get("SolutionType") == "Eigenmode":
            self.run_eigen()
        elif self.plane_wave() is not None:
            self.run_plane_wave()
        elif self.setup.props.get("PortsOnly") == "True":
            self.run_ports_only()
        else:
            self.run_adaptive()
            self.run_sweeps()
        self.compute_fields()
        self.store.write_json("status.json", {"state": "done", "finished": time.time(),
                                              "elapsed": time.time() - self.t0,
                                              **getattr(self, "keys", {})})
        _mark_compiled()
        emit("progress", value=100, text="Solution complete")
        emit("done", elapsed=time.time() - self.t0)
        hard_exit()

    # ----------------------------------------------------------------------------------------
    def probe(self) -> None:
        """Tessellate the un-fragmented model in a scratch gmsh model for face matching."""
        import gmsh
        emit("progress", value=3, text="Building geometry")
        sim_model = gmsh.model.getCurrent()
        gmsh.model.add("hertzon_probe")
        ctx = K.build_design(self.design, self.ev)
        if ctx.errors:
            for n, e in ctx.errors.items():
                warn(f"Object '{n}': {e}")
        self.tess = K.tessellate(ctx, quality="normal")
        self.probe_bbox = K.model_bbox(ctx.built, self.design)
        # the far field surface of a scattering problem encloses the scatterers only: air or vacuum objects
        # (e.g. a mesh refinement box) are background, and a larger surface raises the far field noise floor
        dts = [dt for o in self.design.live_objects() if o.model and o.history[0].kind != "CreateRegion"
               and not self.free_space(o.material) for dt in ctx.built.get(o.name, [])]
        self.scatter_bbox = K._bbox(dts) if dts else self.probe_bbox
        gmsh.model.remove()
        gmsh.model.setCurrent(sim_model)

    def free_space(self, name: str) -> bool:
        """Material indistinguishable from the air background (vacuum, air)."""
        try:
            mat = self.project.material(name)
            ev = self.ev
            return not mat.pec and abs(ev.value(mat.permittivity) - 1) < 1e-3 and ev.value(mat.permeability) == 1 \
                and ev.value(mat.conductivity) == 0 and ev.value(mat.loss_tangent) == 0
        except Exception:  # noqa: BLE001
            return False

    def em_material(self, name: str):
        em = self.em
        mat = self.project.material(name)
        if mat.pec:
            return em.lib.PEC
        ev = self.ev
        er = ev.value(mat.permittivity)
        ur = ev.value(mat.permeability)
        cond = ev.value(mat.conductivity)
        tand = ev.value(mat.loss_tangent)
        mtand = ev.value(getattr(mat, "magnetic_loss_tangent", "0") or "0")
        if mtand:
            ur = complex(ur) * complex(1, -mtand)
        disp = getattr(mat, "dispersion", "None") or "None"
        if disp == EMERGE_DISPERSION:
            # frequency dependent material of EMerge's library: its own property functions
            from ..model.materials import emerge_object
            src = emerge_object(getattr(mat, "emsutil_key", ""))
            if src is not None:
                info(f"Material '{mat.name}': frequency dependent data of EMerge's material library")
                return em.Material(er=src.er, ur=src.ur, tand=src.tand, cond=src.cond, color=mat.color, name=mat.name)
            warn(f"Material '{mat.name}': EMerge library data not found; using the values shown")
            disp = "None"
        if getattr(mat, "anisotropic", False):
            # anisotropic material: diagonal tensors, loss tangent per axis
            def diag(text, default):
                vals = [ev.value(x) for x in (text or "").split(",") if x.strip()]
                return vals if len(vals) == 3 else [default] * 3
            e3 = diag(mat.permittivity_tensor, er)
            u3 = diag(mat.permeability_tensor, ur)
            t3 = diag(mat.loss_tangent_tensor, tand)
            E = np.diag([complex(e) * complex(1, -t) for e, t in zip(e3, t3)])
            U = np.diag([complex(u) for u in u3])
            info(f"Material '{mat.name}': anisotropic er = {e3}, tan d = {t3}, ur = {u3}")
            neff = float(np.sqrt(max(abs(x) for x in e3) * max(abs(complex(x)) for x in u3)))
            return em.Material(er=E, ur=U, cond=cond, color=mat.color, name=mat.name, _neff=neff)
        if disp in ("Djordjevic-Sarkar", "Debye"):
            from ..model.materials import djordjevic_sarkar, debye
            if disp == "Debye":
                fn = debye(ev.value(mat.eps_static), ev.value(mat.eps_infinity), ev.freq(mat.relaxation_frequency))
            else:
                fn = djordjevic_sarkar(er, tand, ev.freq(mat.measured_frequency))
            from emsutil.material import FreqDependent
            prop = FreqDependent(scalar=lambda f, fn=fn: fn(float(f)))
            f0 = ev.freq(mat.measured_frequency) if disp != "Debye" else ev.freq(mat.relaxation_frequency)
            e0 = fn(f0)
            info(f"Material '{mat.name}': {disp} model, er({f0 / 1e9:.4g} GHz) = {e0.real:.4g}, "
                 f"tan d = {-e0.imag / e0.real:.4g}")
            return em.Material(er=prop, ur=ur, cond=cond, color=mat.color, name=mat.name)
        return em.Material(er=er, ur=ur, tand=tand, cond=cond, color=mat.color, name=mat.name)

    def build_model(self) -> None:
        from emerge._emerge.geometry import GeoVolume, GeoSurface
        em = self.em
        ctx = K.build_design(self.design, self.ev)
        for n, e in ctx.errors.items():
            raise RuntimeError(f"Geometry error in '{n}': {e}")
        vols = []
        for obj in self.design.live_objects():
            dt = ctx.built.get(obj.name)
            if not dt or not obj.model:
                if dt and not obj.model:
                    K._occ().remove(dt, recursive=True)
                continue
            dim = max(d for d, _ in dt)
            if dim == 3:
                g = GeoVolume([t for d, t in dt if d == 3], name=obj.name)
                mat = self.project.material(obj.material)
                g.set_material(self.em_material(obj.material))
                if obj.history[0].kind == "CreateRegion":
                    g.background()
                vols.append((g, obj, sum(K._occ().getMass(3, t) for d, t in dt if d == 3), is_conductor(mat)))
                if not obj.solve_inside and not is_conductor(mat):
                    warn(f"'{obj.name}': solve inside is off but material is not a conductor; solving inside.")
                self.geos[obj.name] = g
            elif dim == 2:
                g = GeoSurface([t for d, t in dt if d == 2], name=obj.name)
                self.geos[obj.name] = g
            else:
                K._occ().remove(dt, recursive=True)
        # where solids overlap the smaller (inner) volume wins, with material override (as the reference
        # tool) conductors first win over dielectrics; background regions lose
        override = schema.material_override(self.design)
        vols.sort(key=lambda v: (v[3] and override, -v[2]))
        for prio, (g, obj, _, _) in enumerate(vols):
            if obj.history[0].kind != "CreateRegion":
                g.prio_set(10 + prio)
        self.overlap_vols = [(obj, g, cond) for g, obj, _, cond in vols if obj.history[0].kind != "CreateRegion"]
        self.build_auto_open()
        K._occ().synchronize()

    def check_overlaps(self) -> None:
        """Partial overlaps the reference tool rejects are errors: two dielectrics or two conductors of
        different materials, and without material override any two materials. One object inside the other,
        or with material override a conductor in a dielectric, is fine."""
        override = schema.material_override(self.design)
        vols = [(o, set(g.tags), c) for o, g, c in getattr(self, "overlap_vols", [])]
        bad = []
        for i, (a, ta, ca) in enumerate(vols):
            for b, tb, cb in vols[i + 1:]:
                if a.material == b.material or not ta & tb or ta <= tb or tb <= ta or (override and ca != cb):
                    continue
                bad.append(f"'{a.name}' ({a.material}) and '{b.name}' ({b.material})")
        if bad:
            raise RuntimeError("Objects of different materials partially overlap: " + "; ".join(bad) +
                               ". Resolve the overlaps in Validation Check (or subtract one object from the "
                               "other)" + ("" if override else ", or turn on Material override in the design "
                                           "settings to let metals override dielectrics"))

    # ----------------------------------------------------------------------------------------
    def plane_wave(self):
        """an Incident Plane Wave without ports makes the design a scattering problem."""
        if any(e.enabled for e in self.design.ports()):
            return None
        return next((e for e in self.design.excitations if e.kind == "Incident Plane Wave" and e.enabled), None)

    def build_auto_open(self) -> None:
        mode = self.design.settings.get("AutoOpen", "Off")
        pml_on_region = [b for b in self.design.boundaries if b.kind == "PML" and b.enabled]
        if mode == "Off" and not pml_on_region:
            return
        em = self.em
        f_low = lowest_frequency(self.design, self.setup, self.ev)
        lam = C0 / f_low
        xmin, ymin, zmin, xmax, ymax, zmax = self.probe_bbox
        if mode == "Off":
            # PML assigned manually on the faces of a region object: rebuild that region with PML layers
            self.manual_pml(pml_on_region, lam)
            return
        settings = self.design.settings
        if mode == "Auto Open - Filter":
            margin = self.ev.length(settings.get("FilterAirMargin", "12mm") or "12mm")
            kind = "Radiation"
        else:
            kind = settings.get("AutoOpenBoundary", "Auto")
            if kind == "Auto":
                # "Create Open Region" uses a radiation boundary at lambda/4 also for antennas;
                # an automatic PML put ~90% of all tetrahedra into the absorbing layers (slow solves)
                kind = "Radiation"
            if self.plane_wave() is not None:
                kind = "Radiation"   # the incident field enters through the scattered-field ABC
            pad = settings.get("AutoOpenPadding", "")
            margin = self.ev.length(pad) if pad.strip() else (lam / 4 if kind == "PML" else lam / 3)  # guidance
            if self.plane_wave() is not None and not pad.strip():
                # scattering problem: the first-order scattered-field ABC reflects less the farther out it is
                margin = lam
        W = xmax - xmin + 2 * margin
        D = ymax - ymin + 2 * margin
        H = zmax - zmin + 2 * margin
        pos = (xmin - margin, ymin - margin, zmin - margin)
        info(f"Auto open: {kind} boundary, padding {margin * 1e3:.3g} mm (lambda_low = {lam * 1e3:.4g} mm)")
        if kind == "PML":
            # local lean PML: ~lambda/6 thick, discretized like the air (lambda/12 elements); the former
            # lambda/4 with 5 mesh layers held ~90% of all tetrahedra
            thick = float(os.environ.get("HZN_PML_THICK", "0") or 0) * lam or lam / 6
            nl = int(os.environ.get("HZN_PML_LAYERS", "0") or 0) or 2
            boxes = em.geo.pmlbox(W, D, H, position=pos, material=em.lib.AIR, thickness=thick, Nlayers=1,
                                  N_mesh_layers=nl, exponent=2.0, deltamax=8.0, sides="TBLRFA")
            for b in boxes:
                b.background()
            self.region_geo = boxes[0]
            self.open_kind = "PML"
        else:
            box = em.geo.Box(W, D, H, position=pos).set_material(em.lib.AIR).background()
            self.region_geo = box
            self.open_kind = "Radiation"
            if self.plane_wave() is not None:
                # far-field integration surface: a box close around the scatterers, integrated with Gauss
                # points (not over mesh faces), where the mesh is fine and the ABC reflection is weak
                sb = np.asarray(self.scatter_bbox, np.float64)
                gap = min(min(sb[i] - pos[i], pos[i] + (W, D, H)[i] - sb[i + 3]) for i in range(3))
                d = min(FF_OFFSET * lam, gap / 2)
                self.ff_bounds = np.r_[sb[:3] - d, sb[3:] + d]
                self.ff_region = np.r_[pos, pos[0] + W, pos[1] + D, pos[2] + H]
                self.ff_panel = lam / 4
        self.store.write_json("auto_open.json", {"kind": kind, "padding": margin, "lambda_low": lam,
                                                 "box": [*pos, W, D, H]})

    def manual_pml(self, pmls: list[Assignment], lam: float) -> None:
        em = self.em
        for b in pmls:
            regions = {f.obj for f in b.faces} | set(b.objects)
            for rname in regions:
                if rname not in self.geos:
                    continue
                t = self.tess.get(rname)
                if t is None:
                    continue
                sides = ""
                for f in [f for f in b.faces if f.obj == rname] or [FaceRef(rname, i) for i in range(len(t.face_normal))]:
                    n = np.asarray(t.face_normal[f.face]) if f.face >= 0 else None
                    if n is None:
                        continue
                    c = np.asarray(t.face_center[f.face])
                    mid = (np.asarray(t.bbox[:3]) + np.asarray(t.bbox[3:])) / 2
                    if (c - mid) @ n < 0:
                        n = -n
                    ax = int(np.argmax(np.abs(n)))
                    sides += {(0, 1): "R", (0, -1): "L", (1, 1): "A", (1, -1): "F", (2, 1): "T", (2, -1): "B"}[
                        (ax, int(np.sign(n[ax])))]
                x0, y0, z0, x1, y1, z1 = t.bbox
                thick_raw = b.props.get("Thickness", "")
                thick = self.ev.length(thick_raw) if thick_raw.strip() else lam / 4
                g = self.geos.pop(rname)
                g.remove() if hasattr(g, "remove") else None
                boxes = em.geo.pmlbox(x1 - x0, y1 - y0, z1 - z0, position=(x0, y0, z0), material=em.lib.AIR,
                                      thickness=thick, Nlayers=1,
                                      N_mesh_layers=int(self.ev.integer(b.props.get("Layers", "5") or "5")),
                                      exponent=self.ev.value(b.props.get("Exponent", "2") or "2"),
                                      deltamax=self.ev.value(b.props.get("MaxAttenuation", "8") or "8"),
                                      sides="".join(sorted(set(sides))))
                for bx in boxes:
                    bx.background()
                self.geos[rname] = boxes[0]
                # far fields: the faces of the air box under the PML (not the ones left closed, e.g. a ground)
                names = {"T": "top", "B": "bottom", "L": "left", "R": "right", "F": "front", "A": "back"}
                for s in sorted(set(sides)):
                    sel = boxes[0].face(names[s])
                    self.open_faces = sel if self.open_faces is None else self.open_faces + sel
                info(f"PML on '{rname}' sides {''.join(sorted(set(sides)))}, thickness {thick * 1e3:.3g} mm")

    # ----------------------------------------------------------------------------------------
    def selection_for(self, a: Assignment):
        """FaceSelection for an assignment (faces of objects or whole sheet objects)."""
        em = self.em
        tags: list[int] = []
        for f in a.faces:
            g = self.geos.get(f.obj)
            t = self.tess.get(f.obj)
            if g is None or t is None:
                warn(f"{a.name}: object '{f.obj}' is not part of the solve")
                continue
            if f.face < 0:
                tags += list(g.selection.tags) if t.dim == 2 else list(g.boundary().tags)
                continue
            if t.dim == 2 and len(t.face_area) == 1:
                tags += list(g.selection.tags)
                continue
            mask = t.tri_face == f.face
            tris = t.points[t.tris[mask]]
            if len(tris) == 0:
                warn(f"{a.name}: face {f.face} of '{f.obj}' not found")
                continue
            found = facematch.match_faces(facematch.FaceSignature(tris, t.face_normal[f.face]), self.samples)
            if not found:
                warn(f"{a.name}: face {f.face} of '{f.obj}' could not be located in the solver model")
            tags += found
        for oname in a.objects:
            g = self.geos.get(oname)
            if g is None:
                continue
            t = self.tess.get(oname)
            tags += list(g.selection.tags) if (t and t.dim == 2) else list(g.boundary().tags)
        tags = sorted(set(int(x) for x in tags))
        return em.FaceSelection(tags), tags

    def assign_boundaries(self) -> None:
        em, m = self.em, self.m
        mw = m.mw
        # Auto open region outer faces
        if self.region_geo is not None:
            if getattr(self, "open_kind", "") == "Radiation":
                sel = self.region_geo.boundary()
                self.open_bc(sel, 1)
                self.open_faces = sel
            else:
                self.open_faces = self.region_geo.boundary()
        self.sym_faces = []
        periodic = []
        for b in self.design.boundaries:
            if not b.enabled:
                continue
            if b.kind in schema.UNSUPPORTED_BOUNDARIES:
                warn(f"{b.name}: {schema.UNSUPPORTED_BOUNDARIES[b.kind]} Skipped.")
                continue
            if b.kind == "PML":
                continue  # handled while building the region
            sel, tags = self.selection_for(b)
            if not tags:
                warn(f"{b.name}: empty selection, skipped")
                continue
            p, ev = b.props, self.ev
            if b.kind == "Symmetry":
                self.sym_faces.append(("E" if p.get("Type", "Perfect E") == "Perfect E" else "H", b.name, tags))
            if b.kind == "Perfect E" or (b.kind == "Symmetry" and p.get("Type") == "Perfect E"):
                mw.bc.PEC(sel)
            elif b.kind == "Perfect H" or (b.kind == "Symmetry" and p.get("Type") == "Perfect H"):
                mw.bc.PMC(sel)
            elif b.kind == "Finite Conductivity":
                if p.get("UseMaterial", "True") == "True":
                    mat = self.em_material(p.get("Material", "copper"))
                else:
                    mat = em.Material(cond=ev.value(p["Conductivity"]), ur=ev.value(p.get("Permeability", "1")),
                                      name=b.name)
                rough = ev.length(p.get("Roughness", "0") or "0")
                thick = ev.length(p["Thickness"]) if p.get("UseThickness") == "True" else None
                if thick is not None and p.get("TwoSided", "True") == "True":
                    mw.bc.ThinConductor(sel, material=mat, thickness=thick, surface_roughness=rough)
                else:
                    mw.bc.SurfaceImpedance(sel, material=mat, surface_roughness=rough, thickness=thick)
            elif b.kind == "Impedance":
                Zs = complex(ev.value(p["Resistance"]), ev.value(p["Reactance"]))
                mw.bc.SurfaceImpedance(sel, impedance_function=lambda f, Zs=Zs: Zs)
            elif b.kind == "Lumped RLC":
                w, h, _ = self.port_geometry(b, tags)
                zfun = self.lumped_impedance(b)
                mw.bc.LumpedElement(sel, impedance_function=zfun, width=w, height=h)
            elif b.kind == "Radiation":
                order = 2 if p.get("Order") == "Second order" else 1
                self.open_bc(sel, order)
                if p.get("FarFieldReference", "True") == "True":
                    self.open_faces = sel if self.open_faces is None else self.open_faces + sel
            elif b.kind in ("Primary", "Secondary"):
                periodic.append((b, tags))
            info(f"Boundary '{b.name}' ({b.kind}) on {len(tags)} face(s)")
        self.build_periodic_cell(periodic)

    def build_periodic_cell(self, assigned: list) -> None:
        """Primary / Secondary boundaries as an EMerge periodic cell: one face pair per Secondary face, the
        Floquet phase from the scan angles or the phase delays of the Secondary boundaries."""
        import gmsh
        from . import periodic as PER
        self.cell = None
        if not assigned:
            return
        primaries = {b.name: tags for b, tags in assigned if b.kind == "Primary"}
        secondaries = [(b, tags) for b, tags in assigned if b.kind == "Secondary"]

        def faces(tags):
            return [(t, np.asarray(gmsh.model.occ.getCenterOfMass(2, t)), gmsh.model.occ.getMass(2, t)) for t in tags]

        ev = self.ev
        pairs, lattice, delays, scans, linked = [], [], [], [], set()
        for b, tags in secondaries:
            p = b.props
            pname = p.get("Primary", "").strip()
            if pname not in primaries:
                raise RuntimeError(f"{b.name}: its Primary boundary '{pname}' does not exist or is disabled")
            linked.add(pname)
            f1, f2 = faces(primaries[pname]), faces(tags)
            dv = PER.lattice_vector(f1, f2)
            pairs += [([t1], [t2], dv) for t1, t2 in PER.pair_faces(f1, f2, dv, b.name)]
            lattice.append(dv)
            if p.get("PhaseMode", "Scan Angles") == "Scan Angles":
                scans.append((math.degrees(ev.angle(p.get("ScanTheta", "0deg") or "0")),
                              math.degrees(ev.angle(p.get("ScanPhi", "0deg") or "0")), b.name))
            else:
                delays.append(math.degrees(ev.angle(p.get("PhaseDelay", "0deg") or "0")))
            info(f"Periodic pair '{pname}' -> '{b.name}': {len(tags)} face(s), lattice vector "
                 f"({dv[0] * 1e3:.4g}, {dv[1] * 1e3:.4g}, {dv[2] * 1e3:.4g}) mm")
        for name in sorted(set(primaries) - linked):
            warn(f"{name}: no Secondary boundary is linked to this Primary boundary; its faces stay Perfect E")
        if not pairs:
            return
        if scans and delays:
            warn("Some Secondary boundaries use scan angles, others phase delays: the scan angles are used")
        if scans:
            theta, phi, first = scans[0]
            if any(abs(t - theta) > 1e-9 or abs(f - phi) > 1e-9 for t, f, _ in scans[1:]):
                warn(f"The Secondary boundaries have different scan angles; the angles of '{first}' are used")
            u = PER.scan_direction(theta, phi)
        else:
            f0 = adaptive_frequencies(self.setup, ev)[0]
            u, theta, phi = PER.scan_from_phases(lattice, delays, f0)
            info(f"Phase delays at {f0 / 1e9:g} GHz = scan angles theta {theta:.4g} deg, phi {phi:.4g} deg "
                 "(kept for all frequencies)")
        cell = PER.make_cell(pairs)
        self.m.set_periodic_cell(cell)
        for bc in cell.generate_bcs():
            bc.ux, bc.uy, bc.uz = (float(x) for x in u)
            self.m.mw.bc.assign(bc)
        self.cell, self.scan = cell, (theta, phi)
        self.store.write_json("periodic.json", {"scan_theta": theta, "scan_phi": phi,
                                                "lattice": [list(map(float, d)) for d in lattice]})
        info(f"Periodic cell: {len(pairs)} face pair(s), scan angles theta {theta:.4g} deg, phi {phi:.4g} deg")

    def open_bc(self, sel, order: int) -> None:
        """Radiation boundary; with an incident plane wave it is the scattered-field ABC that injects it."""
        mw, pw = self.m.mw, self.plane_wave()
        if pw is None:
            mw.bc.AbsorbingBoundary(sel, order=order)
            return
        from ..model import planewave
        ev, p = self.ev, pw.props
        k, e, origin = planewave.vectors(p, ev)
        th, ph, psi = planewave.incidence_angles(k, e)
        cs = self.em.CoordinateSystem((1, 0, 0), (0, 1, 0), (0, 0, 1), np.asarray(origin, float))
        kw = {}
        from emerge._emerge.physics.microwave.bcs.boundary_conditions import ScatteredField
        if "definition" in inspect.signature(ScatteredField).parameters:
            kw["definition"] = "EA"                     # EMerge 3 defaults to 'SPH'; the angles below are EA
        bc = mw.bc.ScatteredField(sel, cs=cs, **kw)          # cs origin = zero phase position
        bc.E0 = float(abs(ev.value(p.get("EMagnitude", "1") or "1")))
        # k = -r(theta, phi), E = cos(psi) theta^ + sin(psi) phi^  ->  EMerge elevation/azimuth angles
        bc.set_excitations(90.0 - th, ph + 180.0, psi + 180.0)
        info(f"Incident plane wave k = ({k[0]:.3g}, {k[1]:.3g}, {k[2]:.3g}), "
             f"E0 = ({e[0]:.3g}, {e[1]:.3g}, {e[2]:.3g}), zero phase at ({origin[0] * 1e3:.4g}, {origin[1] * 1e3:.4g}, {origin[2] * 1e3:.4g}) mm")
        self.pw_bcs = getattr(self, "pw_bcs", []) + [bc]

    def lumped_impedance(self, b: Assignment):
        """Z(f) of a lumped RLC boundary: parallel or series R, L, C, or a Touchstone component."""
        p, ev = b.props, self.ev
        model = p.get("Model", "Parallel RLC") or "Parallel RLC"
        if model == "Touchstone":
            from ..model.touchstone import ComponentImpedance
            path = Path(p.get("TouchstoneFile", "").strip())
            if not str(path) or str(path) == ".":
                raise ValueError(f"{b.name}: no Touchstone file given")
            if not path.is_absolute() and self.project.path:
                path = Path(self.project.path).parent / path
            z = ComponentImpedance(path, p.get("TouchstoneMount", "Series"))
            info(f"{b.name}: Touchstone {path.name}, {z.f[0] / 1e9:g} to {z.f[-1] / 1e9:g} GHz")
            try:
                f0 = ev.freq(self.setup.props.get("Frequency", ""))
                if not z.f[0] <= f0 <= z.f[-1]:
                    warn(f"{b.name}: the solution frequency {f0 / 1e9:g} GHz is outside the Touchstone data")
            except Exception:  # noqa: BLE001
                pass
            self.touchstone_elements.append((b.name, z))
            return z
        R = ev.value(p["Resistance"]) if p.get("UseResistance") == "True" else None
        L = ev.value(p["Inductance"]) if p.get("UseInductance") == "True" else None
        C = ev.value(p["Capacitance"]) if p.get("UseCapacitance") == "True" else None
        if model == "Series RLC":
            def zfun(f, R=R, L=L, C=C):
                w_ = 2 * np.pi * f
                z = 0j
                if R:
                    z += R
                if L:
                    z += 1j * w_ * L
                if C:
                    z += 1 / (1j * w_ * C)
                return z if z != 0 else 1e-6
            return zfun

        def zfun(f, R=R, L=L, C=C):
            # parallel RLC as in the reference tool
            w_ = 2 * np.pi * f
            y = 0j
            if R:
                y += 1 / R
            if L:
                y += 1 / (1j * w_ * L)
            if C:
                y += 1j * w_ * C
            return 1 / y if y != 0 else 1e12
        return zfun

    def port_geometry(self, a: Assignment, tags: list[int]):
        """width, height, direction of a lumped port/element from its integration line or bounding box."""
        import gmsh
        ev = self.ev
        line = a.props.get("IntegrationLine", "").strip()
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        area = 0.0
        for t in tags:
            b = gmsh.model.getBoundingBox(2, t)
            lo = np.minimum(lo, b[:3])
            hi = np.maximum(hi, b[3:])
            area += gmsh.model.occ.getMass(2, t)
        if line:
            from ..model.objframe import global_points
            pts = global_points(self.design, a, "IntegrationLine", ev)    # follows its moved object
            v = np.asarray(pts[1]) - np.asarray(pts[0])
            h = float(np.linalg.norm(v))
            d = v / h
            w = area / h if h > 0 else 0
            return w, h, tuple(d)
        ext = hi - lo
        order = np.argsort(ext)          # smallest extent = normal of a planar sheet
        # gmsh pads the bounding box of a flat face by its tolerance (about 1e-7 m), so the normal of a sheet
        # has a tiny but nonzero extent: count anything below 0.1 % of the sheet size as flat
        dims = [i for i in order if ext[i] > 1e-3 * max(ext.max(), 1e-30)]
        if len(dims) < 2:
            raise RuntimeError(f"{a.name}: cannot derive port size; please define an integration line")
        hdir, wdir = dims[0], dims[1]
        d = np.zeros(3)
        d[hdir] = 1.0
        h = float(ext[hdir])
        return area / h, h, tuple(d)

    def assign_excitations(self) -> None:
        em, mw = self.em, self.m.mw
        ev = self.ev
        self.port_map = []
        global TERMINALS, DEEMBED, MULTI_TERMINAL_PORTS
        TERMINALS, DEEMBED, MULTI_TERMINAL_PORTS = None, {}, False
        term = None
        if self.design.settings.get("SolutionType") == "Terminal" and self.plane_wave() is None:
            from .terminals import Terminals
            term = Terminals()
            cond_faces = self.terminal_conductor_faces()
        for e in self.design.ports():
            sel, tags = self.selection_for(e)
            if not tags:
                raise RuntimeError(f"Port '{e.name}' has no faces in the solver model")
            p = e.props
            num = ev.integer(p["PortNumber"])
            if e.kind == "Wave Port":
                self.back_port_with_void(e.name, tags)
                mt = p.get("ModeType", "Auto")
                nmodes = max(1, ev.integer(p.get("NumberOfModes", "1")))
                if term is not None:
                    tp = self.wave_port_terminals(e, tags, cond_faces)
                    if nmodes != len(tp["terminals"]):
                        info(f"Port '{e.name}': {len(tp['terminals'])} terminal(s), solving "
                             f"{len(tp['terminals'])} mode(s)")
                    nmodes = len(tp["terminals"])
                zdef = {"Zpv": "PV", "Zpi": "PI", "Zvi": "VI"}[p.get("ImpedanceDefinition", "Zpv")]
                if term is not None and nmodes > 1:
                    # several signal conductors: every mode is absorbed with its own propagation constant
                    # (even and odd mode of coupled lines), modes solved at every frequency
                    # without the end faces of the conductors: the port block couples all port unknowns,
                    # and those of a face inside metal would have nothing else (singular matrix)
                    sel = em.FaceSelection(self.faces_off_metal(tags))
                    MULTI_TERMINAL_PORTS = True
                    port = mw.bc.MultiModeWavePort(sel, num, number_of_modes=nmodes,
                                                   modetype=None if mt == "Auto" else mt,
                                                   impedance_definition=zdef)
                else:
                    port = mw.bc.ModalPort(sel, num, modetype=None if mt == "Auto" else mt,
                                           number_of_modes=nmodes,
                                           # several modes: solved once per solve (see fresh_port_modes)
                                           mixed_materials=nmodes == 1,
                                           impedance_definition=zdef)
            elif e.kind == "Lumped Port":
                w, h, d = self.port_geometry(e, tags)
                port = mw.bc.LumpedPort(sel, num, width=w, height=h, direction=d, Z0=ev.value(p["Impedance"]))
                info(f"Lumped port {num}: width {w * 1e3:.4g} mm, height {h * 1e3:.4g} mm, direction {d}")
            else:  # Floquet
                port = self.floquet_port(e, sel, tags, num)
            if term is not None:
                if e.kind == "Wave Port":
                    tp["bc"] = port
                    term.ports.append(tp)
                elif e.kind == "Lumped Port":
                    z = ev.value(p["Impedance"])
                    term.ports.append({"name": e.name, "number": num, "kind": "lumped", "z0": z,
                                       "terminals": [e.name], "zref": [z]})
                else:
                    raise RuntimeError(f"Port '{e.name}': {e.kind}s are not available in a terminal solution")
                if p.get("Deembed") == "True":
                    DEEMBED[num] = ev.length(p["DeembedDistance"])
            modes = nmodes if e.kind == "Wave Port" else len(getattr(port, "port_modes", None) or [1])
            self.port_map.append({"name": e.name, "number": num, "kind": e.kind, "modes": modes,
                                  "mode_names": FLOQUET_MODES[:modes] if e.kind == "Floquet Port" else None,
                                  "renorm": p.get("RenormImpedance") if p.get("Renormalize") == "True" else None,
                                  "renorm_ohm": ev.value(p["RenormImpedance"]) if p.get("Renormalize") == "True" else None,
                                  "deembed": ev.length(p["DeembedDistance"]) if p.get("Deembed") == "True" else 0.0})
            info(f"Excitation '{e.name}' ({e.kind}) port {num} on {len(tags)} face(s)")
        pw = self.plane_wave()
        if pw is not None:
            self.port_map = [{"name": pw.name, "number": 1, "kind": pw.kind, "renorm": None,
                              "renorm_ohm": None, "deembed": 0.0}]
            if not getattr(self, "pw_bcs", None):
                raise RuntimeError(f"{pw.name}: an incident plane wave needs an open region "
                                   "(Auto Open or a Radiation boundary)")
            others = [e.name for e in self.design.excitations if e.kind == "Incident Plane Wave" and e.enabled][1:]
            if others:
                warn(f"Only the first incident plane wave is solved; ignored: {', '.join(others)}")
            info(f"Excitation '{pw.name}' (incident plane wave, scattered-field formulation)")
        elif any(e.kind == "Incident Plane Wave" and e.enabled for e in self.design.excitations):
            warn("Incident plane waves are ignored while ports are present")
        if term is not None and term.ports:
            term.ports.sort(key=lambda q: q["number"])
            TERMINALS = term
            names = ", ".join(f"{i + 1}: {n}" for i, n in enumerate(term.names))
            info(f"Terminal solution: {len(term.names)} terminal(s) ({names})")
            self.store.write_json("terminals.json", term.info())
        else:
            (self.store.folder / "terminals.json").unlink(missing_ok=True)
        self.store.write_json("ports.json", self.port_map)
        # a port with several terminals: the fields are stored per terminal (1 W incident on the terminal, the
        # other terminals terminated in their reference impedances), so Edit Sources can drive a differential
        # pair; otherwise per port
        self.field_map = self.port_map
        if TERMINALS is not None and any(len(q["terminals"]) > 1 for q in TERMINALS.ports):
            self.field_map = [{"name": n, "number": i + 1, "kind": "Terminal"} for i, n in enumerate(TERMINALS.names)]
            self.store.write_json("field_sources.json", self.field_map)
        else:
            (self.store.folder / "field_sources.json").unlink(missing_ok=True)

    def terminal_conductor_faces(self) -> set[int]:
        """Faces of the solver model that are conductors for the terminals: Perfect E and finite
        conductivity boundaries, the surface of conductor solids, and outer faces without another boundary
        (the default outer boundary is a perfect conductor)."""
        import gmsh
        mw = self.m.mw
        cond = {int(t) for b in mw.bc.get_conductors() for t in b.tags}
        other = {int(t) for b in mw.bc.boundary_conditions for t in b.tags} - cond
        for obj in self.design.live_objects():
            g = self.geos.get(obj.name)
            if g is None or not obj.model:
                continue
            try:
                mat = self.project.material(obj.material)
            except Exception:  # noqa: BLE001
                continue
            if getattr(g, "dim", 0) == 3 and is_conductor(mat):
                cond |= {int(t) for t in g.boundary().tags}
        for _, f in gmsh.model.getEntities(2):
            up, _ = gmsh.model.getAdjacencies(2, f)
            if len(up) == 1 and f not in other:
                cond.add(int(f))
        return cond

    def conductor_name(self, curves: list[int], cond_faces: set[int]) -> str:
        """Name of a conductor touching the port face: a conductor object, else a Perfect E / finite
        conductivity boundary, else the outer boundary."""
        import gmsh
        faces = set()
        for c in curves:
            up, _ = gmsh.model.getAdjacencies(1, c)
            faces |= {int(s) for s in up} & cond_faces
        objs, bnds = [], []
        for obj in self.design.live_objects():
            g = self.geos.get(obj.name)
            if g is None or not obj.model or obj.history[0].kind == "CreateRegion":
                continue
            try:
                ftags = set(g.boundary().tags) if getattr(g, "dim", 0) == 3 else set(g.tags)
            except Exception:  # noqa: BLE001
                continue
            mat = self.project.material(obj.material) if getattr(g, "dim", 0) == 3 else None
            if ftags & faces and (mat is None or is_conductor(mat)):
                objs.append(obj.name)
        for b in self.design.boundaries:
            if b.enabled and b.kind in ("Perfect E", "Finite Conductivity"):
                try:
                    _, tags = self.selection_for(b)
                except Exception:  # noqa: BLE001
                    continue
                if set(int(t) for t in tags) & faces:
                    bnds.append(b.name)
        return (objs or bnds or ["Outer"])[0]

    def wave_port_terminals(self, e: Assignment, tags: list[int], cond_faces: set[int]) -> dict:
        """Terminals of a wave port: one per signal conductor on its face, the reference conductor is the
        one named in ReferenceConductor or else the largest one."""
        from .terminals import port_conductors, voltage_line
        cond_faces = cond_faces - {int(t) for t in tags}
        groups = port_conductors(list(tags), cond_faces)
        if len(groups) < 2:
            raise RuntimeError(f"Port '{e.name}': a terminal solution needs at least two conductors on the port "
                               f"face (found {len(groups)}); check the conductors and boundaries touching it")
        for g in groups:
            g["name"] = self.conductor_name(g["curves"], cond_faces)
        want = e.props.get("ReferenceConductor", "").strip()
        named = [i for i, g in enumerate(groups) if want and g["name"] == want]
        if want and not named:
            warn(f"Port '{e.name}': reference conductor '{want}' does not touch the port face; using the largest")
        ref = named[0] if named else int(np.argmax([g["length"] for g in groups]))
        sig = [g for i, g in enumerate(groups) if i != ref]
        seen: dict[str, int] = {}
        names = []
        for g in sig:
            seen[g["name"]] = seen.get(g["name"], 0) + 1
            names.append(f"{e.name}_{g['name']}" + (f"_{seen[g['name']]}" if seen[g["name"]] > 1 else ""))
        p = e.props
        zr = self.ev.value(p.get("RenormImpedance", "50ohm") or "50ohm") if p.get("Renormalize") == "True" else 50.0
        lines = [voltage_line(g["points"], groups[ref]["points"]) for g in sig]
        info(f"Port '{e.name}': reference conductor '{groups[ref]['name']}', terminals " + ", ".join(names))
        return {"name": e.name, "number": self.ev.integer(p["PortNumber"]), "kind": "wave", "terminals": names,
                "lines": lines, "reference": groups[ref]["name"], "zref": [zr] * len(sig)}

    def faces_off_metal(self, tags: list[int]) -> list[int]:
        """The faces of tags without those that only touch PEC solids (the end face of a conductor that ends
        on a port face)."""
        import gmsh
        metal = set()
        for name, g in self.geos.items():
            if g.dim == 3 and self.design.has_obj(name) and self.project.material(self.design.obj(name).material).pec:
                metal |= {int(v) for v in g.tags}
        keep = []
        for t in tags:
            vols = {int(v) for v in gmsh.model.getAdjacencies(2, t)[0]}
            if not vols or not vols <= metal:
                keep.append(t)
        return keep or list(tags)

    def floquet_port(self, e: Assignment, sel, tags: list[int], num: int):
        """Floquet port of the periodic cell: the plane waves TE00 (s polarization) and, with two modes, TM00
        (p polarization) in the scan direction of the cell. The port face has to be normal to z."""
        import gmsh
        if self.cell is None:
            raise RuntimeError(f"Floquet port '{e.name}' needs a periodic cell: assign Primary / Secondary "
                               "boundary pairs to the side faces")
        normals = [np.asarray(gmsh.model.getNormal(t, [0.5, 0.5])) for t in tags]
        if any(abs(n[2]) < 1 - 1e-6 for n in normals):
            raise RuntimeError(f"Floquet port '{e.name}': the port face has to be normal to the z axis (the scan "
                               "angles are measured from z)")
        p, ev = e.props, self.ev
        n = ev.integer(p.get("NumberOfModes", "2") or "2")
        if n > 2:
            warn(f"Floquet port '{e.name}': EMerge has the two zeroth order modes TE00 and TM00 only; "
                 f"{n - 2} higher mode(s) left out")
        er = ev.value(p.get("Permittivity", "1") or "1")
        if abs(er - 1) > 1e-9:
            warn(f"Floquet port '{e.name}': EMerge's Floquet port takes the propagation constant of free space; "
                 f"the medium permittivity {er:g} only scales the port H field. Put the port in air or vacuum")
        port = self.m.mw.bc.FloquetPort(sel, num, er=er)
        port.area = float(sum(gmsh.model.occ.getMass(2, t) for t in tags))
        port.scan_theta, port.scan_phi = (math.radians(a) for a in self.scan)
        if n >= 2:
            port.port_modes = {1: (1 + 0j, 0j), 2: (0j, 1 + 0j)}
            if self.scan[0] > 1e-6:
                warn(f"Floquet port '{e.name}': at oblique incidence EMerge absorbs both modes with the "
                     "propagation constant of TE00; the TM00 results are only exact at normal incidence")
        return port

    def back_port_with_void(self, port: str, tags: list[int]) -> None:
        """A wave port on a face of a PEC solid (e.g. the bottom of a coax bore in a metal body) has the
        metal on one side. EMerge zeroes every field DoF of a PEC volume, the port face included, and its
        mode solver takes the port material from the metal side: no modes ("k=-2 must be greater than
        0"). Such a solid is made a void instead (EMerge's VoidAttribute: no field inside, PEC on its
        boundary), which is the same metal for the solve and backs the port as in the reference tool."""
        import gmsh
        em = self.em
        for name, g in self.geos.items():
            if g.dim != 3 or name in self.voided or not self.design.has_obj(name) \
                    or not self.project.material(self.design.obj(name).material).pec:
                continue
            vt = set(g.tags)
            if any(vt & {int(v) for v in gmsh.model.getAdjacencies(2, t)[0]} for t in tags):
                g.set_material(em.lib.AIR)
                g.properties += em.VoidAttribute()
                # EMerge turns the attribute into these conditions when the geometry is committed; that
                # has happened already, the port faces are only known in the committed model
                self.m.mw.bc.no_overwrite().Void(g)
                self.m.mw.bc.no_overwrite().PEC(g.boundary())
                self.voided.add(name)
                info(f"Port '{port}' lies on the metal of '{name}': '{name}' is a void backing the port")

    def assign_mesh_ops(self) -> None:
        mesher = self.m.mesher
        for op in self.design.mesh_ops:
            if not op.enabled:
                continue
            p = op.props
            size = self.ev.length(p.get("MaxLength") or p.get("SurfaceTriangleLength", "1"))
            restrict = p.get("RestrictElements") == "True"
            nmax = max(1, int(float(p.get("MaxElements", "1000") or 1000)))
            if op.kind == "Length Based Inside Selection":
                for o in op.objects + [f.obj for f in op.faces]:
                    g = self.geos.get(o)
                    t = self.tess.get(o)
                    if g is None:
                        continue
                    s_o = size
                    if restrict and t is not None:
                        # regular tetrahedron volume ~ s^3/8.5: enlarge s so that at most nmax elements fit
                        vol = float(np.prod(np.ptp(np.asarray(t.points), axis=0)))
                        s_o = max(size, (8.5 * vol / nmax) ** (1 / 3))
                    mesher.set_domain_size(g, s_o)
            elif op.kind == "Length Based On Edges":
                curves = self._match_edges(op)
                if not curves:
                    warn(f"{op.name}: no edges could be located in the solver model")
                    continue
                if restrict:
                    total = sum(L for _, L in curves)
                    size = max(size, total / nmax)
                mesher._set_size_on_edge([c for c, _ in curves], size)
            else:
                sel, tags = self.selection_for(op)
                if tags and restrict:
                    import gmsh
                    area = sum(gmsh.model.occ.getMass(2, t) for t in tags)
                    size = max(size, (4 * area / (np.sqrt(3) * nmax)) ** 0.5)
                if tags:
                    mesher.set_face_size(sel, size)
            info(f"Mesh operation '{op.name}' applied")

    def _match_edges(self, op) -> list[tuple[int, float]]:
        """gmsh curve tags (with lengths) whose end points and midpoint lie on the selected tess edges."""
        import gmsh
        curves = []
        for c in gmsh.model.getEntities(1):
            tag = c[1]
            try:
                lo, hi = gmsh.model.getParametrizationBounds(1, tag)
                pts = np.asarray(gmsh.model.getValue(1, tag, [lo[0], 0.5 * (lo[0] + hi[0]), hi[0]])).reshape(3, 3)
                L = gmsh.model.occ.getMass(1, tag)
            except Exception:  # noqa: BLE001
                continue
            curves.append((tag, pts, L))
        found = []
        for e in op.edges:
            t = self.tess.get(e.obj)
            if t is None or e.face < 0 or e.face >= len(t.edges):
                warn(f"{op.name}: edge {e.face} of '{e.obj}' not found")
                continue
            E = np.asarray(t.edges[e.face], float)
            seg = np.linalg.norm(np.diff(E, axis=0), axis=1)
            tol = 1e-3 * max(seg.sum(), 1e-12)
            a, b = E[:-1], E[1:]
            for tag, pts, L in curves:
                ab = b - a
                ok = True
                for q in pts:
                    u = np.clip(np.einsum("ij,ij->i", q - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-30), 0, 1)
                    if np.min(np.linalg.norm(a + u[:, None] * ab - q, axis=1)) > tol:
                        ok = False
                        break
                if ok:
                    found.append((tag, L))
        return list({t: (t, L) for t, L in found}.values())

    def set_frequencies_for_mesh(self) -> None:
        mw = self.m.mw
        fa = adaptive_frequencies(self.setup, self.ev)
        mw.set_frequency(fa if len(fa) > 1 else fa[0])
        target = float(self.setup.props.get("LambdaTarget", "0.3333") or 0.3333)
        if self.setup.props.get("LambdaRefinement", "True") != "True":
            # without lambda refinement: the initial mesh only resolves the geometry (curvature,
            # small features, mesh operations); the adaptive passes add the rest
            mw.resolution = 1e3
            return
        mw.set_resolution(min(1.0, max(0.02, target)))

    def set_basis(self) -> None:
        name = self.setup.props.get("BasisOrder", "First Order") or "First Order"
        # names -> EMerge element spaces. Older Hertzon files: "Second Order" meant 20 functions,
        # "First Order Complete" / "Second Order Complete" the complete quadratic space.
        order, complete = {"Zero Order": (1, False), "First Order": (2, False), "Second Order": (2, True),
                           "First Order Complete": (2, True), "Second Order Complete": (2, True)}.get(name, (2, False))
        try:
            self.m.mw.set_basis_space(element_order=order, complete=complete)
            info(f"Order of basis functions: {name}")
        except Exception as ex:  # noqa: BLE001
            warn(f"Basis order '{name}' not available ({ex}); using second order")
        try:      # EMerge's curved elements at curved surfaces (setting "CurvedElements", default on)
            from . import curvilinear
            curvilinear.CTX["enabled"] = self.setup.props.get("CurvedElements", "True") == "True"
            self.m.settings.mw_curved_elements = curvilinear.enabled()
        except Exception:  # noqa: BLE001
            pass

    def set_solver(self) -> None:
        # HERTZON_SOLVER: the benchmark's solver comparison on one mesh (the setup's key stays the same)
        name = os.environ.get("HERTZON_SOLVER") or self.setup.props.get("Solver", "Auto")
        em = self.em
        if name in sparta_setup.SWEEP_ONLY:
            # PARDISO for the adaptive passes (a new mesh every pass, where SPARTA's analysis is slow),
            # SPARTA for the sweeps on the final mesh (run_sweeps)
            self.sweep_solver = sparta_setup.SWEEP_ONLY[name]
            name = "PARDISO"
        if self.cell is not None and name in ("Auto", "PARDISO"):
            from .periodic import pardiso_transposes
            if pardiso_transposes():
                # the periodic system matrix is not symmetric and this EMerge's PARDISO interface solves the
                # transposed system: SuperLU (exact, slower on large cells) until EMerge is fixed
                info("Periodic cell: solving with SuperLU (PARDISO of this EMerge version solves the "
                     "transposed system)")
                name = "SuperLU"
        if name == "Auto":
            return
        if sparta_setup.is_sparta(name):
            self.set_sparta(name)
            return
        try:
            self.m.set_solver({"PARDISO": em.EMSolver.PARDISO, "MUMPS": em.EMSolver.MUMPS,
                               "UMFPACK": em.EMSolver.UMFPACK, "SuperLU": em.EMSolver.SUPERLU}[name])
        except Exception as ex:  # noqa: BLE001
            warn(f"Solver {name} not available ({ex}); using automatic choice")

    def set_sparta(self, name: str) -> None:
        """SPARTA on the GPU or the CPU (sparta_setup). Not installed: EMerge's automatic choice."""
        em = self.em
        from emerge._emerge import solver as S
        if not S._SPARTA_AVAILABLE:
            warn(f"Solver {name}: SPARTA is not installed (Tools > Options > Install SPARTA); "
                 "using automatic choice")
            return
        try:
            solver = self.m.set_solver(em.EMSolver.SPARTA)
            # a periodic cell's matrix is not symmetric: LU instead of LDL^T
            mode = sparta_setup.configure(solver, sparta_setup.MODES[name], symmetric=self.cell is None)
        except Exception as ex:  # noqa: BLE001
            warn(f"Solver {name} not available ({ex}); using automatic choice")
            return
        self.sparta = True
        info(f"Matrix solver: {sparta_setup.describe()}; factorization on the {mode}")
        if mode != sparta_setup.MODES[name]:
            warn(f"Solver {name}: no GPU usable by SPARTA in this process; factorizing on the CPU")

    # ----------------------------------------------------------------------------------------
    def run_adaptive(self) -> None:
        m, p = self.m, self.setup.props
        fa = adaptive_frequencies(self.setup, self.ev)
        t = time.time()
        save_flds = p.get("SaveFields", "True") == "True"

        def on_pass(fields, step):
            self.save_mesh()
            if save_flds:
                self.save_fields(fields, "LastAdaptive", progress=False)
            self.save_farfields(fields, "LastAdaptive")
            self.antenna_pass(step)
            emit("passdata", step=step)

        key = self.keys["model_key"] if hasattr(self, "keys") else model_key(self.project, self.design, self.setup)
        resume = load_amr_state(self.out, key, fa)
        conv = adaptive_loop(m, fa, p, self.out, on_pass, key=key, resume=resume)
        last = conv.pop("_last", None)
        conv["final_tets"] = int(m.mesh.n_tets)
        conv["frequencies"] = fa
        self.store.write_json("convergence.json", conv)
        self.step("Adaptive meshing", t)
        status = "converged" if conv.get("converged") else "not converged"
        info(f"Adaptive meshing {status}: {conv['final_tets']} tetrahedra after {len(conv['passes'])} passes")
        emit("convergence", **conv)
        # Solve once on the final mesh at the adaptive frequencies: S-matrix + fields ("LastAdaptive")
        t = time.time()
        if last is not None:
            # the last pass already solved the final mesh (keeps it as LastAdaptive)
            f = np.array(last[0], float)
            order = np.argsort(f)
            f, S, z0 = f[order], np.array(last[1])[order], last[2]
            flds = [last[3][i] for i in order]
        else:
            emit("progress", value=55, text="Solving last adaptive mesh")
            f, S, z0, data = solve_at(m, fa)
            flds = list(data.field)[-len(f):]
        self.save_s("LastAdaptive", f, S, z0)
        self.save_port_fields("LastAdaptive", f[:1])
        self.save_mesh()
        if p.get("SaveFields", "True") == "True":
            self.save_fields(flds, "LastAdaptive")
        self.save_farfields(flds, "LastAdaptive")
        if conv.get("passes"):
            self.antenna_pass(int(conv["passes"][-1]["pass"]))
        self.step("Last adaptive solution", t)

    def run_plane_wave(self) -> None:
        """Scattering solution for the incident plane wave: total fields, and far fields of the
        scattered field (bistatic RCS). Without S-parameters the adaptive loop has no convergence
        quantity; the passes converge on the change of the scattered field energy."""
        m, p = self.m, self.setup.props
        t = time.time()
        fa = adaptive_frequencies(self.setup, self.ev)
        conv = self.plane_wave_adaptive(fa)
        fields = self._pw_fields if self._pw_fields is not None else self.solve_scattered(fa)
        self.save_mesh()
        if p.get("SaveFields", "True") == "True":
            self.save_fields(fields, "LastAdaptive")
        self.save_farfields(fields, "LastAdaptive")
        conv.update(final_tets=int(m.mesh.n_tets), frequencies=fa, plane_wave=True)
        self.store.write_json("convergence.json", conv)
        emit("convergence", **conv)
        self.step("Plane wave solution", t)
        for sw in [s for s in self.setup.sweeps if s.props.get("Enabled", "True") == "True"]:
            t = time.time()
            fr = sweep_frequencies(sw.props, self.ev)
            emit("progress", value=70, text=f"Plane wave sweep '{sw.name}' ({len(fr)} points)")
            fields = self.solve_scattered(fr)
            if sw.props.get("SaveFields") == "True":
                self.save_fields(fields, sw.name)
            self.save_farfields(fields, sw.name)
            self.step(f"Sweep {sw.name}", t)

    def plane_wave_adaptive(self, fa: list[float]) -> dict:
        """Adaptive passes for the incident plane wave (Maximum Delta Energy). The refinement
        uses the same element error estimate as the port driven passes."""
        from .emerge_compat import select_refinement_indices, tet_to_node
        from emerge._emerge.physics.microwave.bcs.boundary_conditions import PEC
        m, p = self.m, self.setup.props
        maxp = max(1, int(p.get("MaximumPasses", "6")))
        max_de = float(p.get("MaximumDeltaEnergy", "0.001") or 0.001)
        min_passes = max(2, int(p.get("MinimumPasses", "2")))
        min_conv = max(1, int(p.get("MinimumConvergedPasses", "1")))
        max_tets = int(float(p.get("MaximumRefinement", "1000000")))
        pct = float(p.get("PercentRefinement", "30"))
        incl = float(p.get("ErrorInclusion", "50")) / 100.0
        ratio = float(p.get("RefinementRatio", "0.6"))
        growth = float(p.get("GrowthRate", "1.6"))
        passes, prev, passed, converged = [], None, 0, False
        self._pw_fields = None
        _amr(m).reset()
        for step in range(1, maxp + 1):
            if (self.out / "ABORT").exists():
                warn("Adaptive meshing aborted by user")
                break
            emit("progress", value=12 + 40 * (step - 1) / maxp, text=f"Adaptive pass {step} of max {maxp}")
            t0 = time.time()
            fields = self.solve_scattered(fa)
            WT = np.array([scattered_energy(fl, m) for fl in fields])
            W, Wtot = WT[:, 0], WT[:, 1]
            row = {"pass": step, "tets": int(m.mesh.n_tets), "time": time.time() - t0}
            if passes:
                row["delta_tets"] = 100.0 * (m.mesh.n_tets / max(1, passes[-1]["tets"]) - 1)
            if prev is not None:
                # Delta Energy: change of the scattered energy relative to the total field energy
                row["delta_s"] = float(np.max(np.abs(W - prev) / np.maximum(np.abs(Wtot), 1e-300)))
                row["passed"] = row["delta_s"] <= max_de
                passed = passed + 1 if row["passed"] else 0
            prev = W
            passes.append(row)
            try:    # Solution Data (convergence) follows the passes while solving, like the port driven passes
                self.store.write_json("convergence.json", {
                    "passes": passes, "converged": False, "max_delta_s": max_de, "quantity": "Delta Energy",
                    "plane_wave": True, "frequencies": list(fa), "running": True})
            except Exception as e:  # noqa: BLE001
                warn(f"Could not store intermediate pass results: {e}")
            emit("pass", **row, energy=True)
            info(f"Pass {step}: {m.mesh.n_tets} tetrahedra" +
                 (f", Delta Energy = {row['delta_s']:.4g}" if "delta_s" in row else ""))
            self._pw_fields = fields
            # like the port driven passes: every pass leaves a plottable "LastAdaptive" solution
            self.save_mesh()
            if p.get("SaveFields", "True") == "True":
                self.save_fields(fields, "LastAdaptive", progress=False)
            self.save_farfields(fields, "LastAdaptive")
            if self.design.spheres:
                try:        # monostatic RCS over the passes (report solution "AdaptivePasses")
                    from .ffquant import record_rcs_pass
                    record_rcs_pass(self.store, self.design, step)
                except Exception as ex:  # noqa: BLE001
                    warn(f"Monostatic RCS of pass {step} not stored: {ex}")
            emit("passdata", step=step)
            if passed >= min_conv and step >= min_passes:
                converged = True
                break
            if step == maxp or m.mesh.n_tets >= max_tets:
                break
            self._pw_fields = None
            pec = [int(t) for bc in m.mw.bc.oftype(PEC) for t in m.mesh.get_triangles(bc.tags)]
            errors, lengths = [], None
            for fl in fields:
                fl.set_field_vector()
                err, lengths = fl._solution_quality(pec)
                errors.append(err)
            error = np.max(errors, axis=0)
            last_n = m.mesh.n_tets
            ids = select_refinement_indices(error, incl)[::-1]
            included = np.zeros(m.mesh.n_tets, dtype=np.bool_)
            included[ids] = True
            coords, sizes = tet_to_node(m.mesh.nodes, m.mesh.tets, lengths, included)
            method = str(p.get("RefinementMethod", ""))
            snap = (np.asarray(m.mesh.nodes), np.asarray(m.mesh.tets), m.mesh.n_tets)
            g = _refine_delaunay(m, error, lengths, pct, growth, step, self.out) if method.startswith("Local") else None
            if g is None and method.startswith(AURA_METHODS):
                g = _refine_aura(m, error, pct, step)
            if g is not None:
                grow = g
            elif method.startswith("Local") or method.startswith(AURA_METHODS):  # failed local pass: keep the refinement so far
                grow = _refine_stable(m, error, lengths, pct, growth, step, snap)
            elif _refine_local(m, p, coords, sizes, ratio, growth, pct, error, lengths):
                grow = (m.mesh.n_tets / last_n - 1) * 100
            else:
                for _ in range(5):
                    _amr(m).add_refinement_points(coords, sizes, ratio * np.ones_like(sizes)) if _ == 0 \
                        else _amr(m).set_ratio(ratio)
                    m._reset_mesh()
                    _amr(m).set_refinement_function(growth, 2.0)
                    m.generate_mesh(True)
                    grow = (m.mesh.n_tets / last_n - 1) * 100
                    if pct * 0.5 <= grow <= pct * 2.0:
                        break
                    ratio = ratio * (1.25 if grow > pct * 2.0 else 0.8)
            info(f"Pass {step + 1}: mesh refined to {m.mesh.n_tets} tetrahedra (+{grow:.1f}%)")
        return {"passes": passes, "converged": converged, "max_delta_s": max_de, "quantity": "Delta Energy",
                "plane_wave": True}

    def solve_scattered(self, freqs) -> list:
        m = self.m
        freqs = [float(f) for f in np.atleast_1d(freqs)]
        try:
            n0 = len(list(m.mw.data.field))
        except Exception:  # noqa: BLE001
            n0 = 0
        m.mw.set_frequency(freqs if len(freqs) > 1 else freqs[0])
        try:
            data = m.mw.run_scattered()
        except IndexError as ex:
            raise RuntimeError("Nothing to solve: every field unknown is fixed by a conductor. Check that the "
                               "radiation region is air/vacuum and not a metal") from ex
        fields = list(data.field)[n0:]
        fields.sort(key=lambda f: float(np.real(f.freq)))
        return fields

    def solve_one_sweep(self, name: str) -> None:
        """Analyze a single sweep. Rebuilds the converged adaptive mesh and solves only this sweep;
        the adaptive solution and the other sweeps stay as they are."""
        fa = adaptive_frequencies(self.setup, self.ev)
        resume = load_amr_state(self.out, self.keys["model_key"], fa)
        if not resume or not (len(resume["amr_sizes"]) or resume.get("mesh") or resume.get("aura")):
            raise RuntimeError("No adaptive solution to reuse; analyze the whole setup first")
        restore_amr_mesh(self.m, resume, float(self.setup.props.get("GrowthRate", "1.6")))
        info(f"Reusing the adaptive mesh: {self.m.mesh.n_tets} tetrahedra")
        self.port_map = self.store.read_json("ports.json", self.port_map) or self.port_map
        sweeps = [s for s in self.setup.sweeps if s.name == name]
        if not sweeps:
            raise RuntimeError(f"Sweep '{name}' not found")
        keep = self.setup.sweeps
        self.setup.sweeps = sweeps
        try:
            sweeps[0].props["Enabled"] = "True"
            self.run_sweeps()
        finally:
            self.setup.sweeps = keep
        self.compute_fields()
        self.store.write_json("status.json", {"state": "done", "finished": time.time(),
                                              "elapsed": time.time() - self.t0, **getattr(self, "keys", {})})
        emit("progress", value=100, text=f"Sweep '{name}' complete")
        emit("done", elapsed=time.time() - self.t0)
        hard_exit()

    def run_tuning(self, spec: dict) -> None:
        """Derivative tuning model of a sweep (hertzon.solver.tuning) on the converged adaptive mesh.
        spec: {"sweep": name, "variables": [names], "points": count}."""
        from . import tuning
        fa = adaptive_frequencies(self.setup, self.ev)
        resume = load_amr_state(self.out, self.keys["model_key"], fa)
        if not resume or not (len(resume["amr_sizes"]) or resume.get("mesh") or resume.get("aura")):
            raise RuntimeError("No adaptive solution to reuse; analyze the setup first")
        restore_amr_mesh(self.m, resume, float(self.setup.props.get("GrowthRate", "1.6")))
        info(f"Reusing the adaptive mesh: {self.m.mesh.n_tets} tetrahedra")
        self.port_map = self.store.read_json("ports.json", self.port_map) or self.port_map
        name = spec.get("sweep", "")
        sw = next((s for s in self.setup.sweeps if s.name == name), None)
        if sw is None:
            raise RuntimeError(f"Sweep '{name}' not found")
        freqs = tuning.tuning_frequencies(sw.props, self.ev, int(spec.get("points", 51)))
        variables = list(spec.get("variables", []))
        info(f"Derivative tuning of {', '.join(variables)} at {len(freqs)} frequencies of '{name}'")

        def progress(k, n):
            emit("progress", value=15 + 83 * k / n, text=f"Tuning '{name}': {k} of {n} frequencies")

        tmp = tuning.tuning_path(self.out, name).with_suffix(".tmp.npz")
        meta = tuning.build(self, variables, freqs, tmp, log=info, progress=progress)
        os.replace(tmp, tuning.tuning_path(self.out, name))
        for v, (lo, hi), u in zip(meta["variables"], meta["ranges"], meta["units"]):
            info(f"Tuning range of {v}: {lo:+.4g} to {hi:+.4g} {u}")
        emit("progress", value=100, text=f"Tuning '{name}' complete")
        emit("done", elapsed=time.time() - self.t0)
        hard_exit()

    def save_s(self, name: str, f, S, z0, solved=None, ports_only: bool = False) -> None:
        try:
            S, z0 = postprocess_ports(f, S, z0, self.port_map)
        except Exception as e:  # noqa: BLE001
            warn(f"Port de-embedding/renormalization skipped: {e}")
        gamma, zo = port_data(f)
        self.store.save_sparams(name, f, S, z0, self.port_map, solved=solved, gamma=gamma, zo=zo,
                                terminals=TERMINALS.info() if TERMINALS is not None else None,
                                ports_only=ports_only)

    def wave_ports(self) -> list[tuple[dict, object]]:
        """(port_map entry, EMerge port) of every wave port."""
        from emerge._emerge.physics.microwave.bcs.port_bcs import ModalPort
        bcs = {bc.port_number: bc for bc in self.m.mw.bc.oftype(ModalPort)}
        return [(q, bcs[q["number"]]) for q in self.port_map if q.get("kind") == "Wave Port" and q["number"] in bcs]

    def save_port_fields(self, label: str, freqs) -> None:
        """Mode fields of the wave ports on the port faces (Port Field Display): E and H at the nodes of the
        port triangles for every mode at the frequencies freqs (the modes solved nearest to them)."""
        try:
            mesh = self.m.mesh
            out, meta = {"freq": np.asarray(freqs, float)}, []
            for q, bc in self.wave_ports():
                tris = mesh.tris[:, mesh.get_triangles(bc.tags)]
                ids, inv = np.unique(tris.ravel(), return_inverse=True)
                xyz = mesh.nodes[:, ids]
                n = q["number"]
                out[f"nodes_{n}"] = xyz.T.astype(np.float64)
                out[f"tris_{n}"] = inv.reshape(tris.shape).T.astype(np.int32)
                rows = []
                for k, f in enumerate(np.atleast_1d(freqs)):
                    k0 = 2 * np.pi * float(f) / C0
                    modes = bc.get_modes(k0)
                    for i, md in enumerate(modes[:len(bc.port_modes)]):
                        out[f"E_{n}_{i + 1}_{k}"] = np.asarray(md.E_function(*xyz), complex).T.astype(np.complex64)
                        out[f"H_{n}_{i + 1}_{k}"] = np.asarray(md.H_function(*xyz), complex).T.astype(np.complex64)
                        rows.append({"freq_index": k, "mode": i + 1, "type": str(md.modetype),
                                     "gamma": [float(np.real(1j * md.beta)), float(np.imag(1j * md.beta))],
                                     "zo": float(np.real(md.Z0))})
                meta.append({"name": q["name"], "number": n, "modes": rows})
            out["meta"] = np.array(json.dumps(meta))
            if meta:
                self.store.save_npz(f"portfields_{label}.npz", **out)
        except Exception as e:  # noqa: BLE001
            warn(f"Port fields not saved: {e}")

    def run_ports_only(self) -> None:
        """Solve only the port modes: propagation constant, port impedance and port fields at the adaptive
        frequencies and at the frequencies of every sweep, no 3D solution (Solve Ports Only)."""
        from emerge._emerge.physics.microwave.bcs.port_bcs import ModalPort
        mw = self.m.mw
        if not self.wave_ports():
            raise RuntimeError("Solve ports only needs at least one wave port")
        sols = [("LastAdaptive", np.array(adaptive_frequencies(self.setup, self.ev), float))]
        sols += [(sw.name, sweep_frequencies(sw.props, self.ev)) for sw in self.setup.sweeps
                 if sw.props.get("Enabled", "True") == "True"]
        lumped = [q for q in self.port_map if q.get("kind") != "Wave Port"]
        if lumped:
            warn("Solve ports only: lumped ports have no port modes; only the wave ports are solved")
        for i, (label, fr) in enumerate(sols):
            emit("progress", value=15 + 80 * i / len(sols), text=f"Port modes: {label}")
            S, labels = [], []
            for f in fr:
                portnumbers = []
                for q, bc in self.wave_ports():
                    bc.clear_modes()
                    mt = bc.forced_modetype
                    mw.modal_analysis(bc, len(bc.port_modes), direct=False, freq=float(f), TEM=mt == "TEM")
                    nm = len(bc.port_modes)
                    portnumbers += [q["number"]] if nm == 1 else [float(f"{q['number']}.{k + 1}") for k in range(nm)]
                k0 = 2 * np.pi * float(f) / C0
                if TERMINALS is not None:
                    TERMINALS.transform([f], np.zeros((1, len(portnumbers), len(portnumbers))), portnumbers)
                else:
                    g, z = [], []
                    for q, bc in self.wave_ports():
                        for md in bc.get_modes(k0)[:len(bc.port_modes)]:
                            g.append(1j * md.beta)
                            z.append(md.Z0)
                    PORT_RECORDS[float(f)] = (np.array(g, complex), np.array(z, complex))
                labels = portnumbers
            n = len(TERMINALS.names) if TERMINALS is not None else len(labels)
            nan = np.full((len(fr), n, n), np.nan, complex)
            z0 = TERMINALS.zref if TERMINALS is not None else np.full(n, 50.0)
            self.save_s(label, fr, nan, z0, solved=fr, ports_only=True)
            self.save_port_fields(label, fr)
            info(f"Port modes of '{label}' at {len(fr)} frequenc{'y' if len(fr) == 1 else 'ies'}")
        self.save_mesh()

    def save_mesh(self) -> None:
        m = self.m
        try:
            nodes = np.asarray(m.mesh.nodes).T
            tets = np.asarray(m.mesh.tets).T
            info(f"Mesh statistics: {tets.shape[0]} tetrahedra, {nodes.shape[0]} nodes")
            stats = []
            groups = {}
            if self.region_geo is not None and "Region" not in self.geos:
                groups[f"Region ({getattr(self, 'open_kind', 'open')})"] = self.region_geo
            groups.update(self.geos)
            # owning object of every tetrahedron (field plots are drawn per object like the reference tool)
            # where volumes overlap, the tetrahedron belongs to the object EMerge takes the material from:
            # highest _priority, the later one on ties (build_model: smaller volume wins, regions lose)
            tet_obj = np.full(len(tets), -1, np.int32)
            tet_prio = np.full(len(tets), -np.inf)
            members = []        # all tetrahedra inside each object's geometry, overlaps included
            names = []
            for name, g in groups.items():
                try:
                    if getattr(g, "dim", 3) != 3:
                        continue
                    ids = np.asarray(m.mesh.get_tetrahedra(list(g.tags)), int)
                    if len(ids) == 0:
                        continue
                    prio = float(getattr(g, "_priority", 0))
                    own = ids[tet_prio[ids] <= prio]
                    tet_obj[own] = len(names)
                    tet_prio[own] = prio
                    names.append(name.split(" (")[0] if name.startswith("Region (") else name)
                    members.append(ids)
                    stats.append({"object": name})
                except Exception:  # noqa: BLE001
                    pass
            # statistics over the tetrahedra each object finally owns (overlapped ones counted once)
            for i, st in enumerate(stats):
                own = tets[tet_obj == i]
                st.update(tet_statistics(nodes, own) if len(own) else {"tets": 0})
            self.store.save_npz("mesh.npz", nodes=nodes.astype(np.float64), tets=tets.astype(np.int32),
                                tet_obj=tet_obj, obj_names=np.array(names or [""], dtype=str),
                                obj_ptr=np.cumsum([0] + [len(i) for i in members]).astype(np.int64),
                                obj_tets=np.concatenate(members or [np.zeros(0, int)]).astype(np.int32),
                                **self.curved_mesh())
            stats.append({"object": "Total", **tet_statistics(nodes, tets)})
            self.store.write_json("mesh_stats.json", {"tets": int(tets.shape[0]), "nodes": int(nodes.shape[0]),
                                                      "objects": stats})
        except Exception as ex:  # noqa: BLE001
            warn(f"Could not save mesh: {ex}")

    def curved_mesh(self) -> dict:
        """Curved tetrahedra (ids, edge midpoint displacements (nc, 6, 3) in curvilinear._PAIRS order), saved
        with the mesh so that the plots draw them curved; empty when nothing is curved."""
        try:
            from . import curvilinear as CV
            if not CV.enabled():
                return {}
            cd = CV.curved_data_bcs(self.m.mesh, self.m.mw.bc.boundary_conditions)
            if cd is None:
                return {}
            return {"curv_tets": np.asarray(cd[0], np.int32), "curv_disp": np.asarray(cd[1], np.float64)}
        except Exception as ex:  # noqa: BLE001
            warn(f"Curved elements not saved for the plots: {ex}")
            return {}

    def compute_fields(self) -> None:
        """Field data of the final solutions, computed once at the end of the analysis: the passes and
        sweep points only save the solution (lazy_fields), so the first plot does not have to wait."""
        from .lazy_fields import fields_from_solution, _stamp
        todo = []
        for sp in sorted(self.store.folder.glob("solution_*.npz")):
            label = sp.name[len("solution_"):-len(".npz")]
            fp = self.store.folder / f"fields_{label}.npz"
            s = _stamp(sp)
            if not (fp.exists() and s is not None and _stamp(fp) == s):
                todo.append((label, sp))
        for i, (label, sp) in enumerate(todo):
            t = time.time()
            base, span = 96 + 3 * i / len(todo), 3 / len(todo)
            emit("progress", value=base, text=f"Computing field data ({label})")
            try:
                with np.load(sp, allow_pickle=False) as d:
                    sol = {k: d[k] for k in d.files}

                def prog(frac, label=label, base=base, span=span):
                    emit("progress", value=base + span * frac,
                         text=f"Computing field data ({label}): {100 * frac:.0f}%")
                self.store.save_npz(f"fields_{label}.npz", **fields_from_solution(sol, prog))
                info(f"Field data '{label}' computed in {time.time() - t:.1f} s")
            except Exception as e:  # noqa: BLE001
                warn(f"Field data '{label}' not computed ({e}); it is computed when plotted")

    def fields_of(self) -> list[dict]:
        """The excitations whose fields are stored: the ports, or the terminals (see build)."""
        return getattr(self, "field_map", None) or self.port_map

    def save_fields(self, fields: list, label: str, progress: bool = True) -> None:
        """E and H on the mesh nodes for each port excitation."""
        if progress:
            emit("progress", value=60, text="Saving fields")
        m = self.m
        # values at the 4 corners of every tetrahedron, taken from that tetrahedron: the field stays
        # discontinuous across material interfaces (normal E jumps by eps_r) like in the reference tool
        # With few enough tets also the edge midpoints are stored (10-node quadratic tets, VTK order),
        # so the plots show the 2nd order field inside an element and not only a linear interpolation.
        tets = np.asarray(m.mesh.tets)
        nt = tets.shape[1]
        C = np.asarray(m.mesh.nodes)[:, tets]                    # (3, 4, N)
        quad = nt * max(1, len(self.fields_of())) * max(1, len(fields)) <= 600_000
        if quad:
            mids = [(C[:, i] + C[:, j]) / 2 for i, j in TET10_EDGES]
            C = np.concatenate([C, np.stack(mids, axis=1)], axis=1)   # (3, 10, N)
        npt = C.shape[1]
        cm = self.curved_mesh()
        lazy = os.environ.get("HERTZON_EAGER_FIELDS") != "1" and \
            not any(p.get("kind") == "Incident Plane Wave" for p in self.port_map) and \
            not any(getattr(f, "_rel", False) for f in fields)
        if lazy:   # only the solution: fields are computed when a plot needs them (hertzon.solver.lazy_fields)
            from .lazy_fields import solution_arrays
            self.store.save_npz(f"solution_{label}.npz",
                                **solution_arrays(m, fields, self.fields_of(), excite, npt == 10, cm))
            info("Solution saved (field data is computed at the end of the analysis)")
            return
        P = C.transpose(0, 2, 1).reshape(3, -1)                  # tet-major
        x, y, z = P[0], P[1], P[2]
        mapping = np.repeat(np.arange(nt, dtype=np.int64), npt)
        out = {"tet4" if npt == 4 else "tet10": np.array([1])}
        # curved tetrahedra: the plots put the midpoints on the curved edges; the solution lives on the
        # straight reference tet, so map E and H to the curved element (covariant Piola transform)
        if cm:
            from . import curvilinear as CV
            ct = cm["curv_tets"].astype(np.int64)
            lam = np.eye(4)
            if npt == 10:
                lam = np.vstack([lam] + [(lam[i] + lam[j]) / 2 for i, j in TET10_EDGES])
            Xc, D = CV.curved_map(np.transpose(C[:, :4, ct], (2, 1, 0)), cm["curv_disp"], lam)
            DiT = np.linalg.inv(D).transpose(0, 1, 3, 2)
            DdD = D / np.linalg.det(D)[..., None, None]
            rows = (ct[:, None] * npt + np.arange(npt)).ravel()
            Xs = P[:, rows].T                                   # straight positions of the same points
            Xc = Xc.reshape(-1, 3)

        def curve(fld, Ev, Hv):
            if not cm:
                return Ev, Hv
            # the FEM solution is the total field; a relative (scattered) field is that minus the incident
            # wave, which is taken at the curved point
            rel = getattr(fld, "_rel", False) and bool(getattr(fld, "background_fields", None))
            Ec, Hc = Ev[rows], Hv[rows]
            if rel:
                ok = np.ones(len(rows), bool)
                Eb = [fld.backE(*X.T, ok).T for X in (Xs, Xc)]
                Hb = [fld.backH(*X.T, ok).T for X in (Xs, Xc)]
                Ec, Hc = Ec + Eb[0], Hc + Hb[0]
            Ec = np.einsum("tpij,tpj->tpi", DiT, Ec.reshape(len(ct), npt, 3)).reshape(-1, 3)
            Hc = np.einsum("tpij,tpj->tpi", DdD, Hc.reshape(len(ct), npt, 3)).reshape(-1, 3)
            if rel:
                Ec, Hc = Ec - Eb[1], Hc - Hb[1]
            Ev, Hv = Ev.copy(), Hv.copy()
            Ev[rows], Hv[rows] = Ec, Hc
            return Ev, Hv

        for k, fld in enumerate(fields):
            freq = float(getattr(fld, "freq", 0.0))
            for port in self.fields_of():
                try:
                    excite(fld, port)
                    _interp_known_tets(fld, x, y, z, mapping)
                    Ev, Hv = curve(fld, np.vstack([fld.Ex, fld.Ey, fld.Ez]).T, np.vstack([fld.Hx, fld.Hy, fld.Hz]).T)
                    out[f"E_{k}_{port['number']}"] = Ev.astype(np.complex64)
                    out[f"H_{k}_{port['number']}"] = Hv.astype(np.complex64)
                except Exception as ex:  # noqa: BLE001
                    warn(f"Field export for port {port['number']} failed: {ex}")
            out[f"freq_{k}"] = np.array([freq])
        self.store.save_npz(f"fields_{label}.npz", **out)
        try:     # an older on-demand solution of this label must not win over these fields
            (self.store.folder / f"solution_{label}.npz").unlink(missing_ok=True)
        except OSError:
            pass
        info("Fields saved")

    def save_huygens(self, fields: list, label: str) -> bool:
        """E/H on the open boundary for post-processed far fields (infinite spheres can be
        added or edited after the solve without analyzing again)."""
        if self.open_faces is None:
            return False
        m = self.m
        if getattr(self, "ff_bounds", None) is not None:
            b = np.array(self.ff_bounds, np.float64)
            skip = set()
            for _, ax, p in self.symmetry_planes():
                # only the solved half lies in the mesh; the face on the mirror plane is left out
                if b[ax] < p < b[ax + 3] or abs(p - b[ax]) < 1e-9 or abs(p - b[ax + 3]) < 1e-9:
                    lower = (self.ff_region[ax] + self.ff_region[ax + 3]) / 2 < p
                    b[ax + 3 if lower else ax] = p
                    skip.add((ax, 1 if lower else 0))
            vis, wns = box_quadrature(b, self.ff_panel, skip)
        else:
            try:
                surf = m.mesh.boundary_surface(self.open_faces.tags, inward_normal=False)
                vis = np.asarray(surf.edge_centers, np.float64)
                wns = np.zeros_like(vis)
                nrm, ar, t2e = surf.normals, surf.areas, surf.tri_to_edge
                for j in range(3):
                    np.add.at(wns.T, t2e[j], (nrm * ar / 3).T)
            except Exception as ex:  # noqa: BLE001
                warn(f"Far field surface not available: {ex}")
                return False
        out = {"vis": vis, "wns": wns, "stamp": np.array([time.time_ns()])}
        planes = self.symmetry_planes()
        if planes:
            out["sym_kind"] = np.array([p[0] for p in planes])
            out["sym_axis"] = np.array([p[1] for p in planes], np.int64)
            out["sym_pos"] = np.array([p[2] for p in planes], np.float64)
        pw = self.plane_wave()
        if pw is not None:
            out["E0"] = np.array([abs(self.ev.value(pw.props.get("EMagnitude", "1") or "1"))])
            from ..model import planewave
            kv, _, _ = planewave.vectors(pw.props, self.ev)
            out["mono_dir"] = np.array([float(np.arccos(np.clip(-kv[2], -1, 1))), float(np.arctan2(-kv[1], -kv[0]))])
        mapping = None
        for k, fld in enumerate(fields):
            out[f"freq_{k}"] = np.array([float(fld.freq)])
            for port in self.fields_of():
                try:
                    excite(fld, port)
                    f2 = fld.relative if port.get("kind") == "Incident Plane Wave" else fld
                    if mapping is None:   # the point location (tet search) once, not once per port
                        mapping = np.asarray(fld.basis.interpolate_index(*vis), np.int64)
                    if (mapping >= 0).all():
                        _interp_known_tets(f2, *vis, mapping)
                        eh = f2
                    else:
                        eh = f2.interpolate(*vis)
                    E, H = (np.array([eh.Ex, eh.Ey, eh.Ez]), np.array([eh.Hx, eh.Hy, eh.Hz])) if eh is f2 else \
                        (np.asarray(eh.E), np.asarray(eh.H))
                    n = port["number"]
                    out[f"E_{k}_{n}"], out[f"H_{k}_{n}"] = E.astype(np.complex128), H.astype(np.complex128)
                    S = 0.5 * np.cross(E.T, np.conj(H).T).T
                    out[f"Prad_{k}_{n}"] = np.array([float(np.real(np.sum(S * wns)))])
                    if port.get("kind") == "Incident Plane Wave":
                        out[f"pw_{n}"] = np.array([1])
                except Exception as ex:  # noqa: BLE001
                    warn(f"Far field surface fields for port {port['number']} failed: {ex}")
        self.store.save_npz(f"huygens_{label}.npz", **out)
        return True

    def symmetry_planes(self) -> list[tuple[str, int, float]]:
        """Symmetry boundaries as mirror planes of the far field: (E or H, axis 0/1/2, position in m). Only a
        plane parallel to a coordinate plane can be mirrored, and only one plane per axis."""
        planes: list[tuple[str, int, float]] = []
        m = self.m
        for kind, name, tags in getattr(self, "sym_faces", []):
            try:
                tris = np.asarray(m.mesh.get_triangles(tags), np.int64)
                pts = np.asarray(m.mesh.nodes)[:, np.unique(np.asarray(m.mesh.tris)[:, tris])]
            except Exception as ex:  # noqa: BLE001
                warn(f"{name}: symmetry plane not found in the mesh ({ex}); the far field is not mirrored there")
                continue
            span = np.ptp(pts, axis=1)
            ax = int(np.argmin(span))
            if span[ax] > 1e-6 * max(float(span.max()), 1e-12):
                warn(f"{name}: the symmetry faces are not one plane parallel to XY, YZ or ZX; "
                     "the far field is not mirrored there")
                continue
            pos = float(np.mean(pts[ax]))
            same = [p for p in planes if p[1] == ax]
            if same:
                if abs(same[0][2] - pos) > 1e-9 + 1e-6 * float(span.max()) or same[0][0] != kind:
                    warn(f"{name}: a second symmetry plane normal to {'XYZ'[ax]}; "
                         "the far field is mirrored only at the first one")
                continue
            planes.append((kind, ax, pos))
        return planes

    def antenna_pass(self, step: int) -> None:
        """Antenna parameters of an adaptive pass (Antenna Parameters report over the passes)."""
        if not self.design.spheres or not self.design.ports():
            return
        try:
            from .ffquant import record_antenna_pass
            record_antenna_pass(self.store, self.design, self.ev, step)
        except Exception as ex:  # noqa: BLE001
            warn(f"Antenna parameters of pass {step} not stored: {ex}")

    def save_farfields(self, fields: list, label: str) -> None:
        if not self.save_huygens(fields, label):
            if self.design.spheres:
                warn("Far field requested but no radiation/PML boundary present")
            return
        from .farfield_post import farfield
        for sph in self.design.spheres:
            try:
                farfield(self.store, self.design, sph.name, label, self.ev)
                info(f"Far field '{sph.name}' computed")
            except Exception as ex:  # noqa: BLE001
                warn(f"Far field '{sph.name}' failed: {ex}")

    def run_sweeps(self) -> None:
        """All enabled sweeps on the final mesh. SPARTA keeps its symbolic analysis from one batch of
        frequencies to the next (an interpolating sweep solves in batches, and the analysis costs two to three
        factorizations); EMerge's own adaptive sweep does the same with _reset_solvers=False."""
        global KEEP_SOLVER
        if self.sweep_solver and not self.sparta:
            self.set_sparta(self.sweep_solver)
        KEEP_SOLVER = self.sparta
        try:
            self._run_sweeps()
        finally:
            KEEP_SOLVER = False
            if self.sparta:
                try:
                    self.m.mw.solveroutine.reset()
                except Exception:  # noqa: BLE001
                    pass

    def _run_sweeps(self) -> None:
        m, ev = self.m, self.ev
        sweeps = [s for s in self.setup.sweeps if s.props.get("Enabled", "True") == "True"]
        self.factor_memory()
        for i, sw in enumerate(sweeps):
            t = time.time()
            try:
                from . import mt_pardiso
                mt_pardiso.TIMES.update(assembly=0.0, solve=0.0)
            except Exception:  # noqa: BLE001
                mt_pardiso = None
            p = sw.props
            fr = sweep_frequencies(p, ev)
            for nm, z in self.touchstone_elements:
                if len(fr) and (fr.min() < z.f[0] * (1 - 1e-9) or fr.max() > z.f[-1] * (1 + 1e-9)):
                    warn(f"{nm}: sweep '{sw.name}' goes beyond the Touchstone data ({z.f[0] / 1e9:g} to "
                         f"{z.f[-1] / 1e9:g} GHz); outside it the nearest data point is used")
            par = p.get("Parallel") == "True"
            if par and self.sparta:
                # EMerge's sweep worker threads only take solvers that release the GIL, so they would not
                # use SPARTA; SPARTA itself uses all cores (CPU) or the GPU on one frequency at a time
                info(f"Sweep '{sw.name}': SPARTA solves the frequencies one after another")
                par = False
            nw = max(1, int(os.environ.get("HERTZON_SWEEP_WORKERS", "2") or 2))
            prog = SweepProgress(sw.name, 65 + 30 * i / max(1, len(sweeps)), 30 / max(1, len(sweeps)))
            kind = p.get("SweepType", "Interpolating")
            if kind in ("Fast", "Discrete") or len(fr) <= 1:
                prog.planned = len(fr)
            prog.show()
            if p.get("SweepType") == "Fast" and len(fr) > 1:
                from .fast_sweep import fast_sweep
                tol = max(1e-6, float(p.get("ErrorTolerance", "0.5")) / 100.0 * 0.02)
                f, S, z0, data = fast_sweep(m, fr, tol=tol, log=lambda s, n=sw.name: info(f"Fast sweep '{n}': {s}"),
                                            on_solved=prog.solved)
                self.save_s(sw.name, f, S, z0, solved=f)
                if p.get("SaveFields") == "True":
                    self.save_fields(list(data.field)[-len(f):], sw.name)
                    self.save_farfields(list(data.field)[-len(f):], sw.name)     # antenna parameters over f
            elif p.get("SweepType", "Interpolating") == "Discrete":
                f, S, z0, data = solve_at(m, fr, parallel=par, n_workers=nw, on_solved=prog.solved)
                self.save_s(sw.name, f, S, z0, solved=f)
                if p.get("SaveFields") == "True":
                    self.save_fields(list(data.field)[-len(f):], sw.name)
                    self.save_farfields(list(data.field)[-len(f):], sw.name)     # antenna parameters over f
            else:
                self.interpolating_sweep(sw.name, fr, p, par, nw, prog)
            if prog.done:
                with prog._lock:
                    prog.planned, prog.estimate = prog.done, False
                prog.show()
            self.step(f"Sweep {sw.name}", t)
            info(f"Sweep '{sw.name}' finished in {time.time() - t:.1f} s")
            if mt_pardiso is not None and mt_pardiso.TIMES["solve"] > 0:
                info(f"Sweep '{sw.name}': matrix assembly {mt_pardiso.TIMES['assembly']:.1f} s, PARDISO "
                     f"{mt_pardiso.TIMES['solve']:.1f} s (summed over the parallel frequencies)")

    def factor_memory(self) -> None:
        """PARDISO factor size of the final mesh: saved after the adaptive passes, read back by a run that
        only solves a sweep, so that the memory check of a parallel sweep knows it before the first solve."""
        try:
            from .fast_assembly import FACTOR
            if FACTOR.get("bytes"):
                self.store.write_json("solver_memory.json", dict(FACTOR))
                info(f"Matrix solve: {FACTOR['unknowns']} unknowns, {FACTOR['kind']}, "
                     f"factorization {FACTOR['bytes'] / 2 ** 30:.2f} GB")
            else:
                FACTOR.update(self.store.read_json("solver_memory.json", {}) or {})
        except Exception:  # noqa: BLE001
            pass

    def interpolating_sweep(self, name: str, fr: np.ndarray, p: dict, par: bool, nw: int,
                            prog: "SweepProgress | None" = None) -> None:
        """Rational (vector fitting) interpolation with independent validation points."""
        m = self.m
        tol = float(p.get("ErrorTolerance", "0.5")) / 100.0
        nmax = int(p.get("MaxSolutions", "250"))
        f1, f2 = float(fr.min()), float(fr.max())
        # validation points from the reduced basis of the direct solutions (hybrid_sweep): 5 direct start
        # points are enough then, every check point that the basis cannot reach is solved directly
        from . import hybrid_sweep
        hyb = None
        if os.environ.get("HERTZON_HYBRID_SWEEP", "1") != "0":
            hybrid_sweep.apply()
            hyb = hybrid_sweep.Hybrid(tol=max(1e-6, tol / 10))     # see hybrid_sweep for the measured errors
        # with k frequencies at a time, start and validation batches are multiples of k (no half
        # empty round: 6 instead of 5 start points with 2, validation points in rounds of k)
        k = _parallel_workers(m, 64, nw) if par else 1
        n_start, n_check = -(-(5 if hyb else 7) // k) * k, -(-4 // k) * k
        fs: list[float] = list(np.linspace(f1, f2, n_start))
        S_all: dict[float, np.ndarray] = {}
        errs: list[float] = []
        prog = prog or SweepProgress(name, 65, 30)
        prog.plan(len(fs), errs, tol, 0, nmax)
        hybrid_sweep.ACTIVE = hyb
        try:
            self._interpolate(name, fr, nmax, tol, fs, S_all, errs, n_check, par, nw, prog, hyb)
        finally:
            hybrid_sweep.ACTIVE = None
        if hyb is not None:
            info(f"Sweep '{name}': {hyb.summary()}")

    def _interpolate(self, name, fr, nmax, tol, fs, S_all, errs, n_check, par, nw, prog, hyb) -> None:
        from emerge._emerge.physics.microwave.adaptive_freq import SparamModel
        m = self.m
        z0 = None
        passes_ok = 0
        it = 0
        while True:
            it += 1
            todo = [f for f in fs if f not in S_all]
            if todo:
                if hyb is not None:
                    hyb.reduced = False             # start points: direct (in parallel when switched on)
                gf, Sm, z0, _ = solve_at(m, todo, parallel=par, n_workers=nw, on_solved=prog.solved)
                Sm = align_batch(S_all, gf, Sm)
                for f in todo:
                    k = int(np.argmin(np.abs(gf - f)))
                    S_all[f] = Sm[k]
            ff = np.array(sorted(S_all))
            SS = np.array([S_all[f] for f in ff])
            if len(ff) >= nmax:
                warn(f"Sweep '{name}': maximum number of solutions ({nmax}) reached")
                break
            # validation: fit on all but every other new candidate, check midpoints of the largest gaps
            gaps = np.diff(ff)
            cand = sorted(((ff[i] + ff[i + 1]) / 2 for i in np.argsort(gaps)[::-1][:n_check]))
            model = _fit(SparamModel, ff, SS)
            if hyb is not None and hyb.V is not None:
                gf, Sm = self._check_points(m, cand, S_all, par, nw, prog, hyb)
            else:
                gf, Sm, _, _ = solve_at(m, cand, parallel=par, n_workers=nw, on_solved=prog.solved)
                Sm = align_batch(S_all, gf, Sm)
            err = 0.0
            for f in cand:
                k = int(np.argmin(np.abs(gf - f)))
                S_all[f] = Sm[k]
                err = max(err, float(np.max(np.abs(model(np.array([f]))[0] - Sm[k]))))
            emit("log", level="info", msg=f"Sweep '{name}': {len(S_all)} solutions, max interpolation error {err:.2e}")
            if err <= tol:
                passes_ok += 1
                if passes_ok >= 2:
                    break
            else:
                passes_ok = 0
            errs.append(err)
            prog.plan(len(S_all), errs, tol, passes_ok, nmax)
            fs = sorted(S_all)
        ff = np.array(sorted(S_all))
        SS = np.array([S_all[f] for f in ff])
        model = _fit(SparamModel, ff, SS)
        dense = model(fr)
        self.save_s(name, fr, dense, z0, solved=ff)

    @staticmethod
    def _check_points(m, cand, S_all, par, nw, prog, hyb):
        """Check frequencies of the interpolating sweep: first all from the reduced basis (main thread,
        no factorization), then those the basis cannot reach directly (in parallel when switched on);
        their solutions join the basis."""
        hyb.reduced = hyb.probe = True
        hyb.missed = []
        try:
            gf, Sm, _, _ = solve_at(m, cand)
        finally:
            hyb.reduced = hyb.probe = False
        Sm = align_batch(S_all, gf, Sm)
        bad = np.array([any(np.isclose(f, g, rtol=1e-12, atol=0) for g in hyb.missed) for f in gf], bool)
        for _ in range(int((~bad).sum())):
            prog.solved()
        gf, Sm = gf[~bad], Sm[~bad]
        if bad.any():
            miss = [f for f in cand if not np.any(np.isclose(gf, f, rtol=1e-12, atol=0))]
            g2, S2, _, _ = solve_at(m, miss, parallel=par, n_workers=nw, on_solved=prog.solved)
            S2 = align_batch(S_all, g2, S2)
            gf, Sm = np.r_[gf, g2], np.concatenate([Sm, S2]) if len(Sm) else S2
            o = np.argsort(gf)
            gf, Sm = gf[o], Sm[o]
        return gf, Sm

    def run_eigen(self) -> None:
        p = self.setup.props
        fmin = self.ev.freq(p.get("MinimumFrequency", "1GHz"))
        n = int(p.get("NumberOfModes", "3"))
        t = time.time()
        emit("progress", value=30, text="Eigenmode solve")
        data = self.m.mw.eigenmode(search_frequency=fmin, nmodes=n)
        modes = []
        for fld in data.field:
            f = complex(getattr(fld, "freq", 0))
            q = f.real / (2 * f.imag) if f.imag else float("inf")
            modes.append({"freq": f.real, "Q": q})
        self.store.write_json("eigenmodes.json", modes)
        self.save_mesh()
        self.step("Eigenmode", t)
        info("Eigenmodes: " + ", ".join("%.5g GHz" % (x["freq"] / 1e9) for x in modes))


def _solver_stats(row: dict, step: int) -> None:
    """Unknowns, factorization (PARDISO iparm 15-17) and peak memory of the last solve: into the pass row
    (convergence.json) and the log."""
    try:
        from .fast_assembly import FACTOR
    except Exception:  # noqa: BLE001
        FACTOR = {}
    mem = peak_memory()
    if mem > 0:
        row["memory_mb"] = round(mem, 1)
    if FACTOR.get("bytes"):
        row["unknowns"] = int(FACTOR["unknowns"])
        row["factor_mb"] = round(FACTOR["bytes"] / 2 ** 20, 1)
        row["matrix"] = FACTOR["kind"]
        info(f"Pass {step}: {row['unknowns']} unknowns, {FACTOR['kind']}, factorization "
             f"{FACTOR['bytes'] / 2 ** 30:.2f} GB, peak memory {mem / 1024:.2f} GB")


def aligned_delta(S_old: np.ndarray, S_new: np.ndarray) -> tuple[float, float, float]:
    """Max |dS|, max d|S| and max dphase between passes, insensitive to port mode polarity flips."""
    n = S_new.shape[0]
    sgn = np.ones(n)
    for j in range(1, n):
        ref = S_new[0, j] * np.conj(S_old[0, j])
        if abs(S_old[0, j]) > 1e-6 and ref.real < 0:
            sgn[j] = -1.0
    S_al = S_old * np.outer(sgn, sgn)
    d = float(np.max(np.abs(S_new - S_al)))
    dm = float(np.max(np.abs(np.abs(S_new) - np.abs(S_al))))
    with np.errstate(all="ignore"):
        ph = np.angle(S_new / np.where(np.abs(S_al) > 1e-12, S_al, 1)) * 180 / np.pi
    ph[np.abs(S_new) < 1e-6] = 0
    return d, dm, float(np.max(np.abs(ph)))


def adaptive_loop(m, fa: list[float], p: dict, out: Path, on_pass=None, key: str = "", resume: dict | None = None) -> dict:
    """Adaptive mesh refinement with the reference tool's semantics.

    Pass 1 solves the initial mesh; from pass 2 on Delta S is the maximum change of any S-parameter
    between consecutive passes. The loop stops after `MinimumConvergedPasses` consecutive converged
    passes (but not before `MinimumPasses`), at `MaximumPasses`, or when the tetrahedra limit is hit.
    """
    from .emerge_compat import select_refinement_indices, tet_to_node
    maxp = max(1, int(p.get("MaximumPasses", "6")))
    max_ds = float(p.get("MaximumDeltaS", "0.02"))
    use_mat = p.get("UseMatrixConvergence") == "True"
    max_dm = float(p.get("MaxDeltaMag", "0.02")) if use_mat else float("inf")
    max_dp = float(p.get("MaxDeltaPhase", "2")) if use_mat else float("inf")
    min_passes = max(2, int(p.get("MinimumPasses", "2")))
    min_conv = max(1, int(p.get("MinimumConvergedPasses", "1")))
    max_tets = int(float(p.get("MaximumRefinement", "1000000")))
    pct = float(p.get("PercentRefinement", "30"))
    incl = float(p.get("ErrorInclusion", "50")) / 100.0
    ratio0 = float(p.get("RefinementRatio", "0.6"))
    growth = float(p.get("GrowthRate", "1.6"))
    enforce = p.get("EnforceRefinementPercent", "True") == "True"

    passes: list[dict] = []
    S_prev = None
    passed = 0
    converged = False
    ratio = ratio0
    last_n = m.mesh.n_tets
    m.state.stash()
    _amr(m).reset()
    m._local_mesh = None
    m._aura_mesh = None
    m._aura_initial = np.asarray(m.mesh.nodes).T.copy()      # hzAURA state: checked when it is restored
    first = 1
    if resume and resume["passes"] and (len(resume["amr_sizes"]) or resume.get("mesh") or resume.get("aura")) and len(resume["passes"]) <= maxp:
        # continue from the last adaptive mesh: rebuild it, re-solve its pass and go on refining
        restore_amr_mesh(m, resume, growth)
        ratio = resume["ratio"]
        passes = resume["passes"][:-1]
        first = len(resume["passes"])
        if passes:
            S_prev = []
            for S in passes[-1]["S"]:
                v = np.array([complex(*x) for x in np.asarray(S, float).reshape(-1, 2)])
                n = int(round(math.sqrt(len(v))))
                S_prev.append(v.reshape(n, n) if n * n == len(v) else v)
            for r in reversed(passes):
                if not r.get("passed"):
                    break
                passed += 1
        last_n = m.mesh.n_tets
        info(f"Continuing the adaptive analysis from pass {first} ({m.mesh.n_tets} tetrahedra)")
    t_pass = time.time()
    last = None
    for step in range(first, maxp + 1):
        if (out / "ABORT").exists():
            warn("Adaptive meshing aborted by user")
            break
        emit("progress", value=12 + 40 * (step - 1) / maxp, text=f"Adaptive pass {step} of max {maxp}")
        m.data.sim.new(iter_step=step)
        fields, Ss = [], []
        solve_ids = None
        z0 = np.array([50.0])
        for sf in fa:
            fresh_port_modes(m)
            data, solve_ids = m.mw._run_adaptive_mesh(step, sf)
            fields.append(data.field[-1])
            Ss.append(np.array(data.scalar[-1].Sp))
            try:
                if data.scalar[-1].Z0 is not None:
                    z0 = np.asarray(data.scalar[-1].Z0)
            except Exception:  # noqa: BLE001
                pass
            _record_port_data(data.scalar[-1], sf)
            if TERMINALS is not None:
                # convergence and results on the terminal S-parameters (unique for degenerate port modes)
                Ss[-1] = TERMINALS.transform([sf], Ss[-1][None], list(data.scalar[-1]._portnumbers), DEEMBED)[0]
                z0 = TERMINALS.zref
        last = (list(fa), list(Ss), z0, list(fields), m.mesh.n_tets)
        row = {"pass": step, "tets": int(m.mesh.n_tets), "time": time.time() - t_pass,
               "S": [[[float(v.real), float(v.imag)] for v in S.ravel()] for S in Ss]}
        if passes:
            row["delta_tets"] = 100.0 * (m.mesh.n_tets / max(1, passes[-1]["tets"]) - 1)
        _solver_stats(row, step)
        if S_prev is not None:
            ds = [aligned_delta(a, b) for a, b in zip(S_prev, Ss)]
            row["delta_s"] = max(d[0] for d in ds)
            row["delta_mag"] = max(d[1] for d in ds)
            row["delta_phase"] = max(d[2] for d in ds)
            ok = row["delta_s"] <= max_ds and row["delta_mag"] <= max_dm and row["delta_phase"] <= max_dp
            passed = passed + 1 if ok else 0
            row["passed"] = ok
        S_prev = Ss
        passes.append(row)
        _save_pass_results(out, fa, Ss, z0, passes, max_ds)
        if key:
            _save_amr_state(out, key, _amr(m), ratio, passes, fa, m)
        emit("pass", **{k: v for k, v in row.items() if k != "S"})
        t_pass = time.time()
        if passed >= min_conv and step >= min_passes:
            converged = True
            info(f"Pass {step}: converged (Delta S = {row['delta_s']:.4g})")
            break
        if step == maxp:
            break
        if m.mesh.n_tets >= max_tets:
            warn(f"Maximum number of tetrahedra reached ({m.mesh.n_tets} >= {max_tets})")
            break
        need, avail = memory_forecast(m.mesh.n_tets * (1 + pct / 100.0), m.mesh.n_tets)
        if avail and need > 0.85 * avail:
            warn(f"Stopping refinement: the next pass would need about {need / 2**30:.1f} GB of memory, "
                 f"{avail / 2**30:.1f} GB available. Reduce Maximum Refinement or use coarser settings.")
            break
        if on_pass is not None:
            # current mesh + fields as "LastAdaptive" so they can be plotted while the solve continues
            exc = [dict(getattr(fl, "excitation", {}) or {}) for fl in fields]
            try:
                emit("progress", value=12 + 40 * (step - 0.7) / maxp, text=f"Pass {step}: saving mesh and fields")
                on_pass(fields, step)
            except Exception as e:  # noqa: BLE001
                warn(f"Could not store intermediate mesh/fields: {e}")
            for fl, ex in zip(fields, exc):
                fl.excitation = ex
        # ---- refine ----------------------------------------------------------------------------
        emit("progress", value=12 + 40 * (step - 0.5) / maxp,
             text=f"Pass {step}: estimating solution error" + (" (first run compiles solver kernels, "
                                                                 "this can take a few minutes)" if _first_run() else ""))
        info(f"Pass {step}: estimating solution error and refining the mesh")
        t_err = time.time()
        errors = np.empty((m.mesh.n_tets, len(fa)))
        lengths = None
        for i in range(len(fa)):
            err, lengths = fields[i]._solution_quality(solve_ids)
            errors[:, i] = err
        error = errors.max(axis=1)
        info(f"Pass {step}: error estimate {time.time() - t_err:.2f} s ({m.mesh.n_tets} tetrahedra)")
        emit("progress", value=12 + 40 * (step - 0.4) / maxp, text=f"Pass {step}: refining the mesh")
        t_ref = time.time()
        ids = select_refinement_indices(error, incl)[::-1]
        included = np.zeros(m.mesh.n_tets, dtype=np.bool_)
        included[ids] = True
        coords, sizes = tet_to_node(m.mesh.nodes, m.mesh.tets, lengths, included)
        method = str(p.get("RefinementMethod", ""))
        # the current mesh, kept for the stable fallback: a failed local pass may already have reset the model
        snap = (np.asarray(m.mesh.nodes), np.asarray(m.mesh.tets), m.mesh.n_tets)
        grow = _refine_delaunay(m, error, lengths, pct, growth, step, out) if method.startswith("Local") else None
        if grow is None and method.startswith(AURA_METHODS):
            grow = _refine_aura(m, error, pct, step)
        if grow is not None:
            pass
        elif method.startswith("Remesh (stable") or method.startswith("Local") or method.startswith(AURA_METHODS):
            grow = _refine_stable(m, error, lengths, pct, growth, step, snap)
        else:
            # keep the ratio that hit the target in the previous pass (good first guess)
            _amr(m).add_refinement_points(coords, sizes, ratio * np.ones_like(sizes))
            m._reset_mesh()
            _amr(m).set_refinement_function(growth, 2.0)
            m.generate_mesh(True)
            grow = (m.mesh.n_tets / last_n - 1) * 100
            # enforce the requested refinement percentage: bisect the size ratio (smaller ratio = finer)
            lo_r, hi_r, tries = 0.02, 0.98, 0
            info(f"Pass {step}: refined mesh has {m.mesh.n_tets} tetrahedra (+{grow:.1f}%, target {pct:g}%)")
            while enforce and tries < 4 and (grow < pct * 0.5 or grow > pct * 2.0):
                tries += 1
                emit("progress", value=12 + 40 * (step - 0.3) / maxp,
                     text=f"Pass {step}: adjusting refinement to reach {pct:g}% (attempt {tries})")
                if grow < pct * 0.5:
                    hi_r = ratio
                else:
                    lo_r = ratio
                # added elements scale roughly with size^-3: aim straight at the target, stay inside
                # the bracket (plain bisection from 0.02..0.98 overshot and needed several re-meshes)
                guess = ratio * (max(grow, 1.0) / pct) ** (1 / 3)
                ratio = float(guess if lo_r < guess < hi_r else np.sqrt(lo_r * hi_r))
                _amr(m).set_ratio(ratio)
                m._reset_mesh()
                _amr(m).set_refinement_function(growth, 2.0)
                m.generate_mesh(True)
                grow = (m.mesh.n_tets / last_n - 1) * 100
        info(f"Pass {step + 1}: mesh refined to {m.mesh.n_tets} tetrahedra (+{grow:.1f}%) "
             f"in {time.time() - t_ref:.2f} s")
        last_n = m.mesh.n_tets
    m.state.reload()
    m.state.store_geometry_data()
    res = {"passes": passes, "converged": converged, "max_delta_s": max_ds}
    if last is not None and last[4] == m.mesh.n_tets:
        res["_last"] = last          # solution of the final mesh: no need to solve it again
    return res


from .keys import CONVERGENCE_KEYS, model_key, setup_key  # noqa: E402
AMR_STATE = "amr_state.npz"


def _save_amr_state(out: Path, key: str, amr, ratio: float, passes: list, fa, m=None) -> None:
    try:
        arr = {k: (np.asarray(getattr(amr, k)) if getattr(amr, k) is not None else np.zeros(0))
               for k in ("_amr_coords", "_amr_sizes", "_amr_ratios", "_amr_new")}
        tmp = out / (AMR_STATE + ".tmp.npz")
        mesh = getattr(m, "_local_mesh", None) if m is not None else None
        if mesh:
            arr.update({"_mesh_" + k: v for k, v in mesh.items()})
        elif m is not None and getattr(m, "_aura_mesh", None) is not None and m._aura_mesh is m.mesh:
            # hzAURA changes EMerge's mesh directly (gmsh never sees it): store its nodes, tets and tags
            from emerge._emerge.aura.emerge_tools import mesh_to_aura_inputs
            arr.update({"_aura_" + k: v for k, v in mesh_to_aura_inputs(m.mesh).items() if k != "periodic_pairs"})
            arr["_aura_initial_nodes"] = m._aura_initial
        np.savez(tmp, key=np.array(key), ratio=np.array(ratio), passes=np.array(json.dumps(passes)),
                 fa=np.asarray(fa, float), **{k.strip("_"): v for k, v in arr.items()})
        os.replace(tmp, out / AMR_STATE)
    except Exception as e:  # noqa: BLE001
        warn(f"Could not store the adaptive mesh state: {e}")


def _refine_local(m, props, coords, sizes, ratio: float, growth: float, pct: float, error=None,
                  lengths=None) -> bool:
    """refinement: change the mesh locally instead of re-meshing the whole model.

    Mmg: the tetrahedra with the largest error are halved in size (
    graded with the growth rate); how many of them are refined is chosen so that the mesh grows by
    the refinement percentage. Local: bisection towards EMerge's AMR size field.
    Returns False when the model needs the classic re-mesh (setting, periodic cells or an error)."""
    method = props.get("RefinementMethod", "Remesh")
    if method != "Mmg":              # the former "Local" bisection was removed: old setups re-mesh
        return False
    from . import local_refine as LR
    from .emerge_patches import amr_size_at
    if not LR.supported(m):
        return False
    if method == "Mmg":
        from . import mmg_refine
        if not mmg_refine.available():
            warn("Mmg is not installed (pip install pymmg); re-meshing the model instead")
            return False
    try:
        if method == "Mmg" and error is not None and lengths is not None:
            from types import SimpleNamespace
            from .emerge_compat import tet_to_node
            order = np.argsort(-np.asarray(error))
            nodes, tets = m.mesh.nodes, m.mesh.tets

            def size_for(frac: float):
                k = int(np.clip(round(frac * len(order)), 1, len(order)))
                inc = np.zeros(len(order), dtype=np.bool_)
                inc[order[:k]] = True
                c, sz = tet_to_node(nodes, tets, lengths, inc)
                pts = SimpleNamespace(_amr_coords=c, _amr_sizes=sz, _amr_ratios=MMG_SHRINK * np.ones_like(sz))
                return lambda P: amr_size_at(pts, growth, 2.0, P)
            m._local_mesh = mmg_refine.refine_with_mmg(m, size_for, pct, growth)
            return True
        amr = _amr(m)
        amr.add_refinement_points(coords, sizes, ratio * np.ones_like(sizes))
        size_at = lambda pts: amr_size_at(amr, growth, 2.0, pts)  # noqa: E731
        m._local_mesh = LR.refine_to_size(m, size_at, pct)
        return True
    except Exception as e:  # noqa: BLE001
        warn(f"Local mesh refinement failed ({e}); re-meshing the model instead")
        m._local_mesh = None
        return False


def _refine_bisect(m, error, lengths, pct: float, growth: float, step: int) -> bool:
    """Hybrid (local): the worst tets are bisected in place (nested meshes) and bad tets are
    repaired locally. When the element quality has dropped too far below the first mesh's, the model is
    re-meshed once with the current element sizes (clean elements, same resolution) and refined there."""
    from . import bisect, local_refine as LR
    if not LR.supported(m):
        return False
    try:
        q = bisect.mesh_quality(m)
        q1 = float(np.percentile(q, 1))
        if not getattr(m, "_q1_ref", None):
            m._q1_ref = q1
        if q1 < HYBRID_REMESH_QUALITY * m._q1_ref:
            info(f"Pass {step}: element quality dropped ({q1:.3f} vs {m._q1_ref:.3f} at start); re-meshing "
                 "with the current element sizes")
            _refine_stable(m, error, lengths, pct, growth, step)
            m._local_mesh = None
            return True
        grow, m._bis_frac = bisect.refine_emerge(
            m, error, pct, float(getattr(m, "_bis_frac", 0.0)) or pct / 100.0 / 6.0,
            log=lambda s: info(f"Pass {step}: {s}"))
        m._local_mesh = LR.LocalMesh().to_arrays()        # stored for resuming
        return True
    except Exception as e:  # noqa: BLE001
        warn(f"Hybrid refinement failed ({e}); re-meshing the model instead")
        return False


def _refine_zone(m, error, pct: float, step: int) -> bool:
    """Zone (local): re-mesh only a zone around the worst tets with Mmg, all else frozen."""
    from . import mmg_refine, local_refine as LR
    if not LR.supported(m) or not mmg_refine.available():
        return False
    try:
        mmg_refine.zone_refine(m, error, pct, log=lambda s: info(f"Pass {step}: {s}"))
        m._local_mesh = LR.LocalMesh().to_arrays()        # stored for resuming
        return True
    except Exception as e:  # noqa: BLE001
        warn(f"Zone re-mesh failed ({e}); re-meshing the model instead")
        return False


def _hybrid_local(p: dict, step: int) -> bool:
    """Hybrid: is the refinement after pass ``step`` local (else a full re-mesh)?"""
    n0 = int(float(p.get("HybridRemeshPasses", "4") or 4))
    if step <= n0:
        return False
    if "alternating" in str(p.get("RefinementMethod", "")):
        # after the first re-mesh passes: blocks of local and re-mesh passes take turns
        block = max(1, int(float(p.get("HybridBlockPasses", "2") or 2)))
        return ((step - n0 - 1) // block) % 2 == 0
    return True


HYBRID_REMESH_QUALITY = 0.5     # Hybrid: re-mesh once the 1 % quality falls below half of the first mesh's


def _refine_stable(m, error, lengths, pct: float, growth: float, step: int, snap=None) -> float:
    """Remesh (stable): the whole model is re-meshed, but the size field holds every current node's
    element size (ratio 1) and only halves it at the nodes of the worst elements. Regions that are not
    refined keep their mesh size, so consecutive meshes are nearly nested (local) and Delta S is
    not polluted by re-meshing noise. The number of refined elements is chosen to grow the mesh by
    the refinement percentage."""
    nodes, tets, n0 = snap if snap is not None else (np.asarray(m.mesh.nodes), np.asarray(m.mesh.tets), m.mesh.n_tets)
    nn = nodes.shape[1]
    lengths = np.asarray(lengths, float)
    acc = np.zeros(nn)
    cnt = np.zeros(nn)
    for k in range(4):
        np.add.at(acc, tets[k], lengths)
        np.add.at(cnt, tets[k], 1)
    hnode = acc / np.maximum(cnt, 1)
    order = np.argsort(-np.asarray(error))
    frac = float(getattr(m, "_stable_frac", 0.0)) or pct / 100.0 / 5.0
    amr = _amr(m)
    grow = 0.0
    for attempt in range(5):
        k = int(np.clip(round(frac * len(order)), 1, len(order)))
        ratio = np.ones(nn)
        ratio[np.unique(tets[:, order[:k]])] = 0.5
        amr._amr_coords = nodes.astype(float)
        amr._amr_sizes = hnode.copy()
        amr._amr_ratios = ratio
        amr._amr_new = (ratio < 1).astype(float)
        m._reset_mesh()
        amr.set_refinement_function(growth, 2.0)
        m.generate_mesh(True)
        grow = (m.mesh.n_tets / n0 - 1) * 100
        info(f"Pass {step}: stable re-mesh, {k} elements halved -> {m.mesh.n_tets} tetrahedra (+{grow:.1f}%)")
        if pct * 0.6 <= grow <= pct * 1.6 or attempt == 4:
            break
        emit("progress", value=50, text=f"Pass {step}: adjusting refinement to reach {pct:g}% (attempt {attempt + 1})")
        frac = float(np.clip(frac * pct / max(grow, 1.0), 1e-4, 0.9))
    m._stable_frac = frac
    return grow


def _refine_delaunay(m, error, lengths, pct: float, growth: float, step: int, out=None):
    """Local (Delaunay): local local refinement with hzmesh. Every node and every element outside
    the refinement zone stays exactly as it is; inside, new nodes are inserted by constrained Delaunay
    refinement (curved CAD surfaces are followed). Returns the growth in percent, None when the model
    has to be re-meshed instead (library missing, periodic cells, error)."""
    try:
        from . import local_refine as LR
        from ..mesher import adapt
        if out is not None:            # a failed refinement saves its input there (mesher/replay.py)
            adapt.FAIL_DIR = str(out)
        if not LR.supported(m) or not adapt.available():
            warn("Local (Delaunay) refinement is not available here; re-meshing the model instead")
            return None
        grow, m._dm_frac, m._local_mesh, st = adapt.refine_model(
            m, error, lengths, pct, growth, float(getattr(m, "_dm_frac", 0.0) or 0.0),
            log=lambda s: info(f"Pass {step}: {s}"), first=getattr(m, "_local_mesh", None) is None)
        info(f"Pass {step}: local Delaunay refinement -> {m.mesh.n_tets} tetrahedra (+{grow:.1f}%), "
             f"min. quality {st['qmin']:.3f}, {st['t_total']:.2f} s (EMerge import {st['t_load']:.2f} s)")
        return grow
    except Exception as e:  # noqa: BLE001
        warn(f"Local (Delaunay) refinement failed ({e}); re-meshing the model instead")
        m._local_mesh = None
        return None


AURA_METHODS = ("hzAURA", "AURA")   # "AURA (EMerge)" is the former name of "hzAURA (EMerge)" (scripts, AI clients)


def _refine_aura(m, error, pct: float, step: int):
    """hzAURA (EMerge): EMerge's own local refinement, longest-edge bisection of the worst tets followed by
    flips and smoothing. Every old node stays. Returns the growth in percent, None when it failed (the
    caller then re-meshes with the stable size field)."""
    try:
        from emerge._emerge.aura import BrepSnapshot
        from emerge._emerge.aura.brep import snapshot_gmsh
        from emerge._emerge.aura.emerge_tools import refine_mesh_step
        if getattr(m, "_aura_brep", None) is None:
            try:
                m._aura_brep = snapshot_gmsh(scale=1 / m._geo_scale_factor)
            except Exception as e:  # noqa: BLE001
                warn(f"AURA: no geometry snapshot ({e}); new boundary nodes stay on the facets")
                m._aura_brep = BrepSnapshot.empty()
        periodic = [(bc.face1.tags, bc.face2.tags, np.asarray(bc.dv)) for bc in m.mesher._get_periodic_bcs()]
        t0 = time.time()
        # cavity: hzAURA, new boundary nodes that bisection cannot project are put on the CAD surface and
        # their neighbourhood is re-meshed (constrained Delaunay cavity), so the refined surface has no dents
        new, rep = refine_mesh_step(m.mesh, np.asarray(error, float), brep=m._aura_brep, growth=pct / 100.0,
                                    periodic=periodic, cavity=True)
        n0 = m.mesh.n_tets
        m._install_refined_mesh(new)
        m._local_mesh = None
        m._aura_mesh = m.mesh               # stored for resuming (_save_amr_state)
        grow = (m.mesh.n_tets / n0 - 1) * 100
        info(f"Pass {step}: hzAURA refinement -> {m.mesh.n_tets} tetrahedra (+{grow:.1f}%), "
             f"min. quality {rep['q_min']:.3f}, {time.time() - t0:.2f} s")
        return grow
    except Exception as e:  # noqa: BLE001
        warn(f"hzAURA refinement failed ({e}); re-meshing the model instead")
        return None


MMG_SHRINK = 0.5           # Mmg: refined elements are halved (a quarter made the passes slower overall)


def restore_amr_mesh(m, resume: dict, growth: float) -> None:
    """Rebuild the last adaptive mesh from the saved refinement state."""
    if resume.get("mesh") is not None:
        from . import local_refine as LR
        m.data.sim.new(iter_step=0)
        m._local_mesh = resume["mesh"]
        LR.load_mesh(m, resume["mesh"], what="stored adaptive mesh")
        return
    if resume.get("aura") is not None:
        restore_aura_mesh(m, resume["aura"])
        return
    amr = _amr(m)
    amr._amr_coords = np.asarray(resume["amr_coords"], float)
    amr._amr_sizes = np.asarray(resume["amr_sizes"], float)
    amr._amr_ratios = np.asarray(resume["amr_ratios"], float)
    amr._amr_new = np.asarray(resume["amr_new"], float)
    m.data.sim.new(iter_step=0)
    m._reset_mesh()
    amr.set_refinement_function(growth, 2.0)
    m.generate_mesh(True)


def restore_aura_mesh(m, a: dict) -> None:
    """Rebuild a stored hzAURA mesh on the initial mesh ``m`` has now. hzAURA keeps the numbering of every old
    node and appends the new ones, so the initial mesh supplies the tags and the geometry data of EMerge's mesh."""
    from emerge._emerge.aura import RefineResult
    from emerge._emerge.aura.emerge_tools import refine_mesh3d
    m.data.sim.new(iter_step=0)
    # the smoothing moves old nodes, so the initial mesh is compared with the one stored with the state
    n0, init = np.asarray(m.mesh.nodes).T, np.asarray(a.get("initial_nodes", np.zeros((0, 3))), float)
    if n0.shape != init.shape or not np.allclose(n0, init, rtol=0, atol=1e-9 * max(1.0, float(np.abs(n0).max()))):
        raise RuntimeError("The stored adaptive mesh does not fit the initial mesh; analyze the whole setup again")
    nodes = np.asarray(a["nodes"], float)
    tets = np.asarray(a["tets"], np.int64)
    res = RefineResult(nodes=nodes, tets=tets, tet_root=np.zeros(len(tets), np.int64),
                       tet_region=np.asarray(a["tet_region"]), tet_new=np.zeros(len(tets), bool),
                       face_tris=np.asarray(a["face_tris"], np.int64), face_tri_tags=np.asarray(a["face_tri_tags"]),
                       curve_edges=np.asarray(a["curve_edges"], np.int64),
                       curve_edge_tags=np.asarray(a["curve_edge_tags"]))
    m._install_refined_mesh(refine_mesh3d(m.mesh, res))
    m._local_mesh = None
    m._aura_mesh = m.mesh


def peak_memory() -> float:
    """Peak resident memory of this process in MB (0 when unknown)."""
    rss = 0.0
    try:
        import psutil
        mi = psutil.Process().memory_info()
        if getattr(mi, "peak_wset", 0):             # Windows
            return float(mi.peak_wset) / 2 ** 20
        rss = float(mi.rss) / 2 ** 20
    except Exception:  # noqa: BLE001
        pass
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return float(r) / (2 ** 20 if sys.platform == "darwin" else 2 ** 10)
    except Exception:  # noqa: BLE001
        return rss


def load_amr_state(out: Path, key: str, fa) -> dict | None:
    f = out / AMR_STATE
    if not f.exists():
        return None
    try:
        d = np.load(f, allow_pickle=False)
        if str(d["key"]) != key or not np.allclose(d["fa"], np.asarray(fa, float)):
            return None
        mesh = {k[len("mesh_"):]: d[k] for k in d.files if k.startswith("mesh_")} or None
        aura = {k[len("aura_"):]: d[k] for k in d.files if k.startswith("aura_")} or None
        return {"ratio": float(d["ratio"]), "passes": json.loads(str(d["passes"])), "mesh": mesh, "aura": aura,
                **{k: d[k] for k in ("amr_coords", "amr_sizes", "amr_ratios", "amr_new")}}
    except Exception:  # noqa: BLE001
        return None


TET10_EDGES = [(0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3)]   # VTK quadratic tetra mid-edge order


def scattered_energy(fld, m) -> tuple[float, float]:
    """Electric energy of the scattered and of the total field, sampled at the tetrahedron centroids."""
    fld.set_field_vector()
    nodes, tets = np.asarray(m.mesh.nodes), np.asarray(m.mesh.tets)
    c = nodes[:, tets].mean(axis=1)
    a, b, d = (nodes[:, tets[i]] - nodes[:, tets[0]] for i in (1, 2, 3))
    vol = np.abs(np.einsum("ij,ij->j", a, np.cross(b, d, axis=0))) / 6
    mapping = np.arange(tets.shape[1], dtype=np.int64)
    fld._rel = True
    _interp_known_tets(fld, c[0], c[1], c[2], mapping)
    e2 = np.abs(fld.Ex) ** 2 + np.abs(fld.Ey) ** 2 + np.abs(fld.Ez) ** 2
    ws = float(np.nansum(np.real(fld.er) * e2 * vol))
    fld.set_field_vector()
    _interp_known_tets(fld, c[0], c[1], c[2], mapping)
    e2 = np.abs(fld.Ex) ** 2 + np.abs(fld.Ey) ** 2 + np.abs(fld.Ez) ** 2
    return ws, float(np.nansum(np.real(fld.er) * e2 * vol))


def keep_global_max_size(m) -> None:
    """Keep gmsh's global maximum element size (Mesh.MeshSizeMax, a quarter of the model diagonal from the
    geometry build) for the solver mesh. EMerge 2.8.9 left the option alone, so it limited the initial mesh
    and every re-mesh; EMerge 3 resets it to "no limit" unless Mesher.set_global_max_size is given, which
    made the initial mesh about three times coarser and the adaptive passes many more."""
    mesher = getattr(m, "mesher", None)
    if mesher is None or not hasattr(mesher, "set_global_max_size"):
        return
    try:
        import gmsh
        h = float(gmsh.option.getNumber("Mesh.MeshSizeMax"))
    except Exception:  # noqa: BLE001
        return
    if 0 < h < 1e20:
        mesher.set_global_max_size(h / float(getattr(mesher, "_scale", 1.0) or 1.0))


def excite(fld, port: dict) -> None:
    if port.get("kind") == "Terminal":
        fld.excitation = terminal_excitation(fld, port["number"] - 1)
    elif port.get("kind") == "Incident Plane Wave":
        fld.set_field_vector()
    else:
        # a port with several modes has the S indices n.1, n.2, ...: its field is that of the first mode
        n = port["number"]
        keys = list(getattr(fld, "_fields", None) or [])
        fld.excite_port(n if n in keys or not keys else min((k for k in keys if int(k) == n), default=n))


def terminal_excitation(fld, t: int) -> dict:
    """Mode amplitudes of the solution fld for 1 W incident on terminal t, all terminals terminated in their
    reference impedances: with V = D (a + S a), I = D^-T (a - S a) the incident terminal waves are
    (V + Zr I) / (2 sqrt(Zr)) (terminals.py)."""
    keys = [pm.smat_index for pm in fld.port_modes]
    D, _, _ = TERMINALS._blocks(2 * np.pi * float(fld.freq) / C0, keys)
    S = np.asarray(fld._Sp, complex)
    zr = TERMINALS.zref
    I = np.eye(len(keys))
    A = D @ (I + S) + np.diag(zr) @ np.linalg.solve(D.T, I - S)
    rhs = np.zeros(len(zr), complex)
    rhs[t] = 2 * np.sqrt(zr[t])
    return dict(zip(keys, np.linalg.solve(A, rhs)))


def _interp_known_tets(fld, x, y, z, mapping) -> None:
    """fld.interpolate at points whose containing tetrahedron is known (skips the point location search)."""
    fld.interpolate(x, y, z, usenan=False, mapping=mapping)


def _peak_rss() -> float:
    """Peak resident memory of this process (bytes), 0 if unknown (psutil, resource, or the Windows API:
    Windows has no resource module, without psutil the forecast did not learn there)."""
    mb = peak_memory()
    return mb * 2 ** 20 if mb > 0 else _win_memory()[1]


def _win_memory() -> tuple[float, float]:
    """(working set, peak working set) of this process on Windows in bytes, zeros elsewhere."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
                [(n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return float(c.WorkingSetSize), float(c.PeakWorkingSetSize)
    except Exception:  # noqa: BLE001
        pass
    return 0.0, 0.0


def available_memory() -> float:
    """Physical memory usable by this process (respects container limits)."""
    total = 0.0
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                            ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
            ms = MS()
            ms.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))  # type: ignore[attr-defined]
            total = float(ms.total)
        except Exception:  # noqa: BLE001
            return 0.0
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(f).read_text(encoding="utf-8").strip()
            if v.isdigit():
                total = min(total, float(v))
        except OSError:
            pass
    return total


def memory_forecast(next_tets: float, cur_tets: int) -> tuple[float, float]:
    """(estimated bytes for a solve with next_tets, available bytes). Learns bytes/tet from the peak so far.

    The fill-in of a direct factorization of a 3D mesh grows faster than the mesh (nested dissection:
    about n^(4/3) entries), so the learned memory per tetrahedron is extrapolated with that exponent."""
    per_tet = 140e3   # 2nd-order Nedelec + direct solver, measured
    peak = _peak_rss()
    if peak > 0 and cur_tets > 0:
        per_tet = max(per_tet * 0.5, peak / cur_tets)
        if next_tets > cur_tets:
            per_tet *= (next_tets / cur_tets) ** (1.0 / 3.0)
    return next_tets * per_tet, available_memory()


def align_batch(S_all: dict, gf: np.ndarray, Sm: np.ndarray) -> np.ndarray:
    """Every solve run recomputes the wave port modes, whose sign is arbitrary. Flip the port
    polarity of a new batch so that it matches the closest already solved frequency."""
    if not S_all or Sm.shape[1] < 2:
        return Sm
    ff = np.array(sorted(S_all))
    k = int(np.argmin(np.abs(ff - gf[0])))
    ref = S_all[ff[k]]
    new = Sm[0]
    n = new.shape[0]
    sgn = np.ones(n)
    for j in range(1, n):
        a = new[0, j] * np.conj(ref[0, j])
        if abs(ref[0, j]) > 1e-4 and a.real < 0:
            sgn[j] = -1.0
    return Sm * np.outer(sgn, sgn)[None]


def tet_statistics(nodes: np.ndarray, tets: np.ndarray) -> dict:
    """Mesh statistics like the reference tool: element count, edge lengths and volumes."""
    P = nodes[tets]                                     # (N, 4, 3)
    edges = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    L = np.stack([np.linalg.norm(P[:, i] - P[:, j], axis=1) for i, j in edges], axis=1)
    V = np.abs(np.einsum("ij,ij->i", P[:, 1] - P[:, 0], np.cross(P[:, 2] - P[:, 0], P[:, 3] - P[:, 0]))) / 6
    return {"tets": int(len(tets)), "min_edge": float(L.min()), "max_edge": float(L.max()),
            "rms_edge": float(np.sqrt((L ** 2).mean())), "min_vol": float(V.min()), "max_vol": float(V.max()),
            "mean_vol": float(V.mean()), "std_vol": float(V.std()), "total_vol": float(V.sum())}


def _save_pass_results(out: Path, fa: list[float], Ss: list, z0, passes: list, max_ds: float) -> None:
    """After every pass: current LastAdaptive S-parameters and convergence, so reports update during the solve."""
    try:
        store = ResultStore(out)
        ports = store.read_json("ports.json", []) or []
        S = np.array(Ss)
        S, z = postprocess_ports(np.asarray(fa, float), S, z0, ports)
        gamma, zo = port_data(fa)
        store.save_sparams("LastAdaptive", np.asarray(fa, float), S, z, ports, gamma=gamma, zo=zo,
                           terminals=TERMINALS.info() if TERMINALS is not None else None)
        store.write_json("convergence.json", {"passes": passes, "converged": False, "max_delta_s": max_ds,
                                              "frequencies": list(fa), "running": True})
    except Exception as e:  # noqa: BLE001
        warn(f"Could not store intermediate pass results: {e}")


def kernel_tag() -> str:
    """Versions the compiled numba kernels depend on: a new EMerge (version and the wheel or commit it was
    installed from), numba, Python or Hertzon kernels (kernels_version.txt) compiles them again. The Hertzon
    version is not part of it: an update that changes none of these keeps EMerge's kernels, which take
    minutes to compile. Hertzon's own few kernels are compiled again quickly on the first solve (the
    update replaces their files and numba's cache next to them), which needs neither warm-up nor a note."""
    from importlib.metadata import distribution, version
    try:
        return (f"emerge {version('emerge')}{_emerge_source(distribution('emerge'))}, numba {version('numba')}, "
                f"python {sys.version.split()[0]}, hertzon kernels {KERNELS_VERSION}")
    except Exception:  # noqa: BLE001
        return "1"


def _emerge_source(dist) -> str:
    """Where pip installed EMerge from (direct_url.json: git commit or wheel URL), empty for an editable or
    plain installation: EMerge built from another commit can keep its version number, its source changes."""
    try:
        d = json.loads(dist.read_text("direct_url.json") or "{}")
    except (OSError, ValueError):
        return ""
    src = (d.get("vcs_info") or {}).get("commit_id") or d.get("url", "")
    return f" from {src}" if src and not d.get("dir_info", {}).get("editable") else ""


def _kernels_version() -> str:
    """hertzon/solver/kernels_version.txt: raised when Hertzon's own numba kernels change; Hertzon.exe then
    compiles them again before the next start (warm-up), like after the installation."""
    try:
        return (Path(__file__).with_name("kernels_version.txt")).read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        return "0"


KERNELS_VERSION = _kernels_version()


def _first_run() -> bool:
    """True until the numba kernels have been compiled on this machine for the installed versions
    (marker in the user cache, written after a finished solve or by python -m hertzon.solver.warmup)."""
    marker = Path.home() / ".hertzon" / "kernels_compiled"
    try:
        return marker.read_text(encoding="utf-8").strip() != kernel_tag()
    except OSError:
        return True


HARD_EXIT = False      # set by the solver process (run.py)


def hard_exit() -> None:
    """End the solver process right after the results are written. On Windows the teardown of gmsh,
    MKL/Pardiso and numba threads at interpreter exit can crash with an access violation (exit code
    0xC0000005), which reported a finished solution as failed. os._exit still runs the DLL detach code
    there (ExitProcess) and crashed the same way now and then, so Windows ends with TerminateProcess."""
    if not HARD_EXIT:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    if os.name == "nt":
        try:
            import ctypes
            terminate_self(ctypes.WinDLL("kernel32", use_last_error=True))
        except Exception:  # noqa: BLE001
            pass
    os._exit(0)


def terminate_self(k32) -> bool:
    """TerminateProcess on this process with exit code 0; False if Windows refused it.

    The functions need their HANDLE types: untyped, ctypes passes the pseudo handle of GetCurrentProcess
    as a 32 bit int (an invalid handle on 64 bit Windows), and once _win_memory has set restype on the
    shared windll.kernel32 the 64 bit value no longer converts at all (ArgumentError). Either way
    TerminateProcess never ran and os._exit crashed as before; a private WinDLL keeps other callers apart."""
    from ctypes import wintypes
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.argtypes = []
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    return bool(k32.TerminateProcess(k32.GetCurrentProcess(), 0))


def _mark_compiled() -> None:
    try:
        marker = Path.home() / ".hertzon" / "kernels_compiled"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(kernel_tag(), encoding="utf-8")
    except OSError:
        pass


def solver_threads(setup) -> int:
    """Setup 'Solver threads' if set, else the application option (all cores by default)."""
    n = int(setup.props.get("Threads", "0") or 0)
    if n <= 0:
        n = int(os.environ.get("HERTZON_CORES", "0") or 0)
    return max(1, n or (os.cpu_count() or 1))


SOLVER_THREADS = 0      # cores of this solve (solver_threads), set when the model is built
KEEP_SOLVER = False     # sweeps with SPARTA: run_sweep keeps the solver state between batches (run_sweeps)
_PAR_NOTE: list = []


def _parallel_workers(m, nfreq: int, want: int) -> int:
    """Frequencies solved at once in a parallel sweep: the requested number, at most one per frequency,
    at least 2 cores each, and as many factorizations as fit into the memory.

    Every parallel frequency needs its own factorization. Its size is what PARDISO reported for the
    last solve (iparm 15-17), so a batch that ran in parallel does not make the next one look twice
    as large; before the first solve the process forecast is used."""
    cores = SOLVER_THREADS or int(os.environ.get("HERTZON_CORES", "0") or 0) or (os.cpu_count() or 1)
    nw = max(1, min(int(want), nfreq, cores // 2 or 1))
    if nw <= 1:
        return nw
    nt = int(getattr(m.mesh, "n_tets", 0) or 0)
    total = available_memory()
    try:
        from .fast_assembly import FACTOR
        per = 1.3 * float(FACTOR.get("bytes", 0.0))     # factor + matrix, right-hand sides, solutions
    except Exception:  # noqa: BLE001
        per = 0.0
    if per > 0 and total > 0:
        free = 0.85 * total - _current_rss()
        fit = max(1, int(free // per))
        if fit < nw:
            note = f"Parallel sweep: memory for {fit} of {nw} frequencies at a time " \
                   f"({per / 2 ** 30:.1f} GB each, {max(free, 0) / 2 ** 30:.1f} GB free)"
            if note not in _PAR_NOTE:
                _PAR_NOTE.append(note)
                info(note)
        nw = min(nw, fit)
    else:
        need, _ = memory_forecast(nt, nt)
        if need > 0 and total:
            nw = min(nw, max(1, int(0.8 * total // need)))
    return nw


def _current_rss() -> float:
    """Resident memory of this process now (bytes), 0 if unknown."""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            return float(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:  # noqa: BLE001
        pass
    return _win_memory()[0]


class SweepProgress:
    """Progress of one frequency sweep: solved frequencies of the planned ones. An interpolating sweep
    does not know in advance how many it needs; it plans from the error of the last validations, so the
    bar can go back when it adds points."""
    BATCH = 4                    # validation frequencies per round of the interpolating sweep

    def __init__(self, name: str, base: float, span: float):
        self.name, self.base, self.span = name, base, span
        self.done = 0            # frequencies solved in this sweep
        self.planned = 0         # frequencies expected in total (estimate for an interpolating sweep)
        self.estimate = False
        self._lock = threading.Lock()

    def plan(self, n: int, errs: list, tol: float, passes_ok: int, nmax: int) -> None:
        """Interpolating sweep. n: frequencies solved so far, before the first validation the initial
        points about to be solved. Each further round adds BATCH frequencies; the sweep is converged
        after two rounds in a row within the tolerance. The rounds still needed come from how fast
        the interpolation error fell in the last rounds."""
        if not errs:
            rounds = 2
        elif errs[-1] <= tol:
            rounds = 2 - passes_ok
        else:
            rates = [math.log(a / b) for a, b in zip(errs[-3:-1], errs[-2:]) if b > 0 and a > b]
            need = math.ceil(math.log(errs[-1] / tol) / (sum(rates) / len(rates))) if rates else 2
            rounds = min(10, max(1, need)) + 1          # until within the tolerance, then once more
        with self._lock:
            if errs:
                self.done = max(self.done, n)
            self.planned = max(self.done + 1, min(n + self.BATCH * rounds, nmax))
            self.estimate = True
        self.show()

    def solved(self) -> None:
        with self._lock:
            self.done += 1
            if self.planned and self.done >= self.planned:
                self.planned = self.done + (self.BATCH if self.estimate else 0)
        self.show()

    def show(self) -> None:
        with self._lock:
            done, planned = self.done, self.planned
        if planned <= 0:
            emit("progress", value=self.base, text=f"Frequency sweep '{self.name}'")
            return
        frac = min(1.0, done / planned)
        about = "~" if self.estimate else ""
        emit("progress", value=self.base + self.span * frac,
             text=f"Sweep '{self.name}': {done} of {about}{planned} frequencies")


_SOLVED_CB: list = []            # callbacks of the solve_at calls running now (one per solved frequency)


def _hook_solved() -> None:
    """Every frequency EMerge solves ends in SimJob.submit_solution (also in the worker threads)."""
    from emerge._emerge.physics.microwave.simjob import SimJob
    if getattr(SimJob.submit_solution, "_hertzon", False):
        return
    orig = SimJob.submit_solution

    def submit_solution(self, *a, **k):
        out = orig(self, *a, **k)
        for cb in list(_SOLVED_CB):
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass
        return out
    submit_solution._hertzon = True
    SimJob.submit_solution = submit_solution


def solve_at(m, freqs, on_solved=None, **kw):
    """Run a frequency sweep and return only the new results: (freq, S, z0, data).
    on_solved: called after every solved frequency (progress)."""
    from . import mt_pardiso
    freqs = [float(f) for f in np.atleast_1d(freqs)]
    if on_solved is not None:
        try:
            _hook_solved()
            _SOLVED_CB.append(on_solved)
        except Exception:  # noqa: BLE001
            on_solved = None
    try:
        return _solve_at(m, freqs, **kw)
    finally:
        if on_solved is not None:
            _SOLVED_CB.remove(on_solved)


def fresh_port_modes(m) -> None:
    """Ports with several modes: their modes are solved once for each solve (at the mean frequency of a
    sweep, at the frequency of an adaptive pass), so all frequencies of a solve use the same mode basis.
    (EMerge before the hertzon branch of 26 October 2026 also added the modes of a repeated modal analysis at
    a frequency to the ones it already had; one analysis per solve avoided that as well.)"""
    try:
        from emerge._emerge.physics.microwave.bcs.port_bcs import ModalPort
        for bc in m.mw.bc.oftype(ModalPort):
            if len(bc.port_modes) > 1:
                bc.clear_modes()
    except Exception:  # noqa: BLE001
        pass


def _solve_at(m, freqs, **kw):
    from . import mt_pardiso
    fresh_port_modes(m)
    try:
        n0 = int(m.mw.data.scalar.n)
    except Exception:  # noqa: BLE001
        n0 = 0
    m.mw.set_frequency(freqs if len(freqs) > 1 else freqs[0])
    if KEEP_SOLVER:
        kw = dict(kw, _reset_solvers=False)
    if kw.get("parallel"):
        # several frequencies at once, each with PARDISO on its share of the cores (mt_pardiso);
        # without it EMerge would use SuperLU in the worker threads
        nw = _parallel_workers(m, len(freqs), kw.get("n_workers", 2))
        if nw <= 1:
            kw = dict(kw, parallel=False)
        else:
            cores = SOLVER_THREADS or (os.cpu_count() or 1)
            mt_pardiso.WORKER_THREADS = max(1, cores // nw)
            kw = dict(kw, n_workers=nw)
            note = (nw, mt_pardiso.WORKER_THREADS)
            if note not in _PAR_NOTE:          # once per setting (an interpolating sweep solves in batches)
                _PAR_NOTE.append(note)
                info(f"Parallel sweep: {nw} frequencies at a time, PARDISO with {note[1]} cores each")
    try:
        data = m.mw.run_sweep(**kw)
    except Exception as e:  # noqa: BLE001
        if not kw.get("parallel"):
            raise
        warn(f"Parallel sweep failed ({e}); solving the frequencies one after another")
        try:
            n0 = int(m.mw.data.scalar.n)
        except Exception:  # noqa: BLE001
            pass
        data = m.mw.run_sweep(**dict(kw, parallel=False))
    finally:
        mt_pardiso.WORKER_THREADS = 0
    entries = [data.scalar[i] for i in range(n0, data.scalar.n)]
    entries.sort(key=lambda e: float(np.real(e.freq)))
    f = np.array([float(np.real(e.freq)) for e in entries])
    S = np.array([np.asarray(e.Sp) for e in entries])
    z0 = np.asarray(entries[0].Z0) if entries and entries[0].Z0 is not None else np.array([50.0])
    for e, fk in zip(entries, f):
        _record_port_data(e, fk)
    if TERMINALS is not None and len(entries):
        S, z0 = TERMINALS.transform(f, S, list(entries[0]._portnumbers), DEEMBED), TERMINALS.zref
    return f, S, z0, data


BETA: dict[float, np.ndarray] = {}     # solved frequency -> propagation constant per port
PORT_RECORDS: dict[float, tuple[np.ndarray, np.ndarray]] = {}    # solved frequency -> (gamma, Zo) per port mode
TERMINALS = None                       # terminals.Terminals of a Terminal design (set by the Bridge)
MULTI_TERMINAL_PORTS = False           # a port with several signal conductors is in the model (set by the Bridge)
DEEMBED: dict[int, float] = {}         # port number -> de-embedding distance (terminal designs)


def _record_port_data(entry, freq: float) -> None:
    """Propagation constant and port impedance of every port mode of one solved frequency."""
    try:
        if entry.beta is not None:
            beta = np.asarray(entry.beta, complex).ravel()
            BETA[float(freq)] = beta
            z = np.asarray(entry.Z0, complex).ravel() if entry.Z0 is not None else np.full(len(beta), np.nan)
            PORT_RECORDS[float(freq)] = (1j * beta, z)
    except Exception:  # noqa: BLE001
        pass



def port_data(freq) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Propagation constant gamma (per port mode) and characteristic impedance Zo (per port, or per terminal
    in a terminal design) at the frequencies freq, interpolated between the solved frequencies."""
    from .terminals import interp_records
    if TERMINALS is not None:
        return interp_records(TERMINALS.records, freq)
    return interp_records(PORT_RECORDS, freq)


def port_beta(freq: np.ndarray, nports: int) -> np.ndarray:
    """Propagation constant per port at arbitrary frequencies (beta^2 linear in f^2, exact for uniform ports)."""
    fs = np.array(sorted(BETA))
    if len(fs) == 0:
        return np.zeros((len(freq), nports), complex)
    B = np.array([np.resize(BETA[f], nports) for f in fs])
    out = np.zeros((len(freq), nports), complex)
    for i in range(nports):
        if len(fs) == 1:
            out[:, i] = B[0, i]
            continue
        A = np.c_[fs ** 2, np.ones_like(fs)]
        coef, *_ = np.linalg.lstsq(A, B[:, i] ** 2, rcond=None)
        out[:, i] = np.emath.sqrt(coef[0] * freq ** 2 + coef[1])
    return out


def postprocess_ports(freq, S, z0, port_map: list) -> tuple[np.ndarray, np.ndarray]:
    """Wave-port de-embedding and renormalization (port post-processing). Terminal S-parameters are
    de-embedded and referred to the terminal impedances when they are made (terminals.Terminals)."""
    S = np.array(S, complex)
    if TERMINALS is not None:
        return S, np.asarray(z0, complex).ravel()
    n = S.shape[1]
    ports = port_entries(port_map)[:n]
    beta = port_beta(np.asarray(freq, float), n) if any(q.get("deembed") for q in ports) else None
    return deembed_renormalize(S, z0, ports, beta)


def _z0(grid) -> np.ndarray:
    try:
        return np.asarray(grid.Z0)
    except Exception:  # noqa: BLE001
        return np.array([50.0])


def _fit(SparamModel, ff: np.ndarray, SS: np.ndarray):
    n = SS.shape[1]
    # pole count bounded by the information in the samples (filterbench): many poles fitted to a handful of
    # samples overfit, the validation points miss and the sweep solves far more frequencies than needed.
    # No fixed limit of ten: a wide band with many resonances or long lines (phase turns) needs more poles
    poles = min(100, max(2, len(ff) // 2))
    models = [[SparamModel(ff, SS[:, i, j], n_poles=poles, _warn=False) for j in range(n)] for i in range(n)]

    def call(f):
        out = np.zeros((len(f), n, n), complex)
        for i in range(n):
            for j in range(n):
                out[:, i, j] = models[i][j](f)
        return out
    return call


# --------------------------------------------------------------------------------------------
# EMerge log forwarding
# --------------------------------------------------------------------------------------------
def _install_log_sink() -> None:
    from loguru import logger
    logger.remove()

    from . import local_refine as LR
    # while a finished mesh is handed to EMerge, its mesh-generation messages are not true: gmsh only
    # carries the arrays, nothing is meshed
    loading = {"Starting mesh generation phase.": None,
               "Calling GMSH mesher": "Loading the {} into EMerge (no gmsh meshing)",
               "GMSH Meshing complete!": None}
    # a port with several signal conductors has no single conductor pair, so EMerge finds no voltage line for
    # its modes; the terminal impedances come from the line voltages of every conductor (terminals.py)
    no_pair = ("PEC islands without a terminal definition", "Skipping characteristic impedance calculation.")

    def sink(message):
        rec = message.record
        msg = rec["message"]
        lvl = rec["level"].name.lower()
        if LR.LOADING and msg in loading:
            if loading[msg] is None:
                return
            msg = loading[msg].format(LR.LOADING[-1])
        if MULTI_TERMINAL_PORTS and any(x in msg for x in no_pair):
            return
        if lvl in ("warning", "error", "critical"):
            emit("log", level="warning" if lvl == "warning" else "error", msg=msg)
        elif lvl == "info":
            emit("emerge", msg=msg)
    logger.add(sink, level="INFO")


def run(project_path: str, design: str, setup: str, out_dir: str) -> int:
    try:
        proj = Project.load(project_path)
        des = proj.design(design)
        st = des.setup(setup)
        Bridge(proj, des, st, Path(out_dir)).run()
        return 0
    except Exception as ex:  # noqa: BLE001
        emit("log", level="error", msg=f"{type(ex).__name__}: {ex}")
        emit("traceback", text=traceback.format_exc())
        if not os.environ.get("HERTZON_TUNING"):
            try:
                ResultStore(Path(out_dir)).write_json("status.json", {"state": "failed", "error": str(ex)})
            except Exception:  # noqa: BLE001
                pass
        emit("failed", error=str(ex))
        return 1
