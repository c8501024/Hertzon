"""Full-wave line models of the circuit simulator: the cross-section of a line is solved with the port mode
solver (a 2D eigenmode problem on the port face of a thin slab of the line in a shielded box) at a few
frequencies up to the highest analysis frequency, for the substrate and the widths set in the schematic.

The result (propagation constant and line impedance over frequency) is cached per cross-section; the line
length is applied to it in the circuit analysis, so changing a length needs no new solve. The mode solver
gives the dispersion, the line impedance and the dielectric loss of the substrate; the conductor loss is
added from the closed-form surface-resistance model, because the mode solver treats the metal as a perfect
conductor."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from . import tlines as TL

VERSION = 2                 # part of the cache key: raise it when the cross-section model changes
POINTS = 8                  # solved frequencies, from fmax / POINTS to fmax
KINDS = ("MS", "CPW", "GCPW")


class XSecError(RuntimeError):
    pass


@dataclass(frozen=True)
class XSec:
    """A line cross-section. kind: MS (microstrip), CPW (coplanar, no metal under the substrate), GCPW
    (coplanar with a ground plane under the substrate). w: strip width, s: gap of the coplanar lines (m)."""
    kind: str
    w: float
    s: float
    h: float
    t: float
    er: float
    tand: float

    def key(self, fmax: float) -> str:
        d = {k: (round(v, 12) if isinstance(v, float) else v) for k, v in asdict(self).items()}
        d.update(version=VERSION, fmax=round(fmax), points=POINTS)
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]

    def label(self) -> str:
        g = f", gap {self.s * 1e3:.4g} mm" if self.kind != "MS" else ""
        name = {"MS": "Microstrip", "CPW": "Coplanar", "GCPW": "Grounded coplanar"}[self.kind]
        return f"{name} W {self.w * 1e3:.4g} mm{g}"


def grid_fmax(f: float) -> float:
    """The highest solved frequency: f rounded up to 1, 2 or 5 times a power of ten (GHz steps), so small
    changes of the sweep reuse the cached solution."""
    f = max(float(f), 1e8)
    e = 10 ** math.floor(math.log10(f))
    for m in (1, 2, 5, 10):
        if m * e >= f * (1 - 1e-9):
            return m * e
    return 10 * e


def grid(fmax: float) -> np.ndarray:
    return np.linspace(fmax / POINTS, fmax, POINTS)


@dataclass
class Table:
    """Solved propagation constant and impedance of one cross-section over frequency."""
    xs: XSec
    freq: np.ndarray
    gamma: np.ndarray           # complex, without conductor loss
    zo: np.ndarray              # real

    def eeff(self) -> np.ndarray:
        return (self.gamma.imag * TL.C0 / (2 * np.pi * self.freq)) ** 2

    def at(self, f: float, cond: float) -> tuple[float, complex]:
        """(Z0, gamma) at f: effective permittivity, dielectric loss per hertz and impedance interpolated
        linearly (held constant below the lowest solved frequency), conductor loss added."""
        fr = self.freq
        ee = float(np.interp(f, fr, self.eeff()))
        z0 = float(np.interp(f, fr, self.zo))
        ad = float(np.interp(f, fr, self.gamma.real / fr)) * f
        beta = 2 * np.pi * f * math.sqrt(max(ee, 1e-9)) / TL.C0
        x = self.xs
        if x.kind == "MS":
            ac = TL.surface_resistance(f, cond) / (z0 * x.w)
        else:
            ac = TL.cpw_conductor_loss(f, x.w, x.s, x.t, cond, ee, z0)
        return z0, complex(ad + ac, beta)


# ---- the cross-section model ----------------------------------------------------------------------------
def _mm(v: float) -> str:
    return f"{v * 1e3:.6g}mm"


def build(xs: XSec, fmax: float):
    """A project with one 3D design: a thin slab of the line in a box with perfectly conducting walls, a
    wave port over the whole cross-section, a setup that solves only the port modes."""
    from ..model import schema
    from ..model.design import Assignment, Project, Setup, Sweep
    from ..model.materials import Material
    w, s, h, t = xs.w, xs.s, xs.h, max(xs.t, 0.0)
    p = Project("LineModel")
    d = p.designs[0]
    d.name = "CrossSection"
    p.materials.append(Material("LineSubstrate", permittivity=f"{xs.er:g}", loss_tangent=f"{xs.tand:g}"))
    fine = min(w, h, s if xs.kind != "MS" else w) / 10
    coarse = min(h, w) / 2
    slab = coarse * 0.6
    if xs.kind == "MS":
        half = w / 2 + max(8 * h, w)
        below = 0.0
    else:
        b = w / 2 + s
        # grounded: the substrate under the coplanar grounds is a waveguide closed by the side walls; kept
        # narrow, so that its first mode is in cutoff
        half = b + (max(b, 3 * h) if xs.kind == "GCPW" else max(3 * b, 5 * h))
        if xs.kind == "GCPW":       # its cutoff 30 % above fmax, but grounds at least as wide as the gap
            half = max(min(half, TL.C0 / (4 * 1.3 * fmax * math.sqrt(xs.er))), b + max(s, w / 4))
        below = 0.0 if xs.kind == "GCPW" else max(8 * h, 2 * b)
    above = max(12 * h, 2 * half / 3)
    x0, z1 = -half, h + above
    mm = _mm
    d.add_object("CreateBox", name="Substrate", material="LineSubstrate", Position=f"{mm(x0)},0,0",
                 XSize=mm(2 * half), YSize=mm(slab), ZSize=mm(h))
    d.add_object("CreateBox", name="Air", material="air", Position=f"{mm(x0)},0,{mm(h)}", XSize=mm(2 * half),
                 YSize=mm(slab), ZSize=mm(above))
    if below:
        d.add_object("CreateBox", name="AirBelow", material="air", Position=f"{mm(x0)},0,{mm(-below)}",
                     XSize=mm(2 * half), YSize=mm(slab), ZSize=mm(below))

    def metal(name: str, xa: float, xb: float):
        if t > 0:
            return d.add_object("CreateBox", name=name, material="pec", Position=f"{mm(xa)},0,{mm(h)}",
                                XSize=mm(xb - xa), YSize=mm(slab), ZSize=mm(t))
        o = d.add_object("CreateRectangle", name=name, Position=f"{mm(xa)},0,{mm(h)}", Axis="Z",
                         XSize=mm(xb - xa), YSize=mm(slab))
        q = schema.defaults(schema.BOUNDARIES["Perfect E"])
        d.boundaries.append(Assignment(f"PEC_{name}", "Perfect E", q, objects=[name]))
        return o

    fine_objs = [metal("Strip", -w / 2, w / 2).name]
    if xs.kind != "MS":
        edge = w / 2 + s
        band = min(max(s, w / 2), half - edge)
        for side, sg in (("L", -1), ("R", 1)):
            a, b_ = sorted((sg * edge, sg * (edge + band)))
            fine_objs.append(metal(f"GroundEdge{side}", a, b_).name)
            if half - edge - band > 1e-9:
                a, b_ = sorted((sg * (edge + band), sg * half))
                metal(f"Ground{side}", a, b_)
    port = d.add_object("CreateRectangle", name="PortFace", Position=f"{mm(x0)},0,{mm(-below)}", Axis="Y",
                        XSize=mm(2 * half), ZSize=mm(z1 + below))
    q = schema.defaults(schema.EXCITATIONS["Wave Port"])
    # power and current: the current on the strip is unambiguous where a voltage path between the strip and
    # several grounds (coplanar lines, the box walls) is not
    q.update(PortNumber="1", ImpedanceDefinition="Zpi")
    d.excitations.append(Assignment("1", "Wave Port", q, objects=[port.name]))
    for name, objs, size in (("Fine", fine_objs, fine), ("Face", [port.name], coarse)):
        q = schema.defaults(schema.MESH_OPS["Length Based On Selection"])
        q.update(MaxLength=mm(size))
        d.mesh_ops.append(Assignment(name, "Length Based On Selection", q, objects=objs))
    fr = grid(fmax)
    st = Setup("Setup1")
    st.props.update(Frequency=f"{fmax:.9g}Hz", PortsOnly="True")
    sw = Sweep("Sweep")
    sw.props.update(SweepType="Discrete", Distribution="Linear Count", Start=f"{fr[0]:.9g}Hz",
                    Stop=f"{fr[-1]:.9g}Hz", Count=str(len(fr)))
    st.sweeps.append(sw)
    d.setups.append(st)
    return p


# ---- solving and the cache ------------------------------------------------------------------------------
def cache_dir(project_path: str | None) -> Path:
    """Next to the project's results (they move with the project), else in the temporary folder."""
    if project_path:
        p = Path(project_path)
        return p.with_suffix((p.suffix or ".hzn") + "results") / "_LineModels"
    return Path(tempfile.gettempdir()) / "hertzon_line_models"


def load(xs: XSec, fmax: float, folder: Path) -> Table | None:
    f = folder / f"{xs.key(fmax)}.npz"
    if not f.exists():
        return None
    try:
        with np.load(f) as z:
            return Table(xs, np.asarray(z["freq"], float), np.asarray(z["gamma"], complex),
                         np.asarray(z["zo"], float))
    except Exception:  # noqa: BLE001
        return None


def _run(xs: XSec, fmax: float, folder: Path, python: str, cores: int) -> Table:
    from .results import ResultStore
    work = Path(tempfile.mkdtemp(prefix="xsec_", dir=folder))
    proj = build(xs, fmax)
    path = work / "line.hzn"
    proj.save(path)
    env = dict(os.environ, PYTHONUTF8="1", HERTZON_CORES=str(cores), HERTZON_MESH_ONLY="0",
               HERTZON_ONLY_SWEEP="", HERTZON_TUNING="")
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
        env[v] = str(cores)
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else root
    flags = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
    r = subprocess.run([python, "-m", "hertzon.solver.run", str(path), "CrossSection", "Setup1", str(work / "out")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                       creationflags=flags)
    data = ResultStore(work / "out").sparams("Sweep")
    if r.returncode != 0 or data is None or "gamma" not in data:
        err = [ln for ln in r.stdout.splitlines() if '"failed"' in ln or '"error"' in ln]
        msg = err[-1] if err else (r.stderr.strip().splitlines() or ["no result"])[-1]
        raise XSecError(f"{xs.label()}: the cross-section solve failed ({msg[:300]})")
    fr = np.asarray(data["freq"], float)
    g = np.asarray(data["gamma"], complex)[:, 0]
    z = np.real(np.asarray(data["zo"], complex)[:, 0])
    ok = np.isfinite(g) & np.isfinite(z) & (g.imag > 0) & (z > 0)
    if ok.sum() < 2:
        raise XSecError(f"{xs.label()}: the mode solver found no line mode")
    if not ok.all():            # a frequency without a mode impedance: from its neighbours
        z = np.interp(fr, fr[ok], z[ok])
        g = np.interp(fr, fr[ok], g[ok].real) + 1j * np.interp(fr, fr[ok], g[ok].imag)
    np.savez(folder / f"{xs.key(fmax)}.npz", freq=fr, gamma=g, zo=z, xsec=json.dumps(asdict(xs)))
    try:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    return Table(xs, fr, g, z)


def solve(items: list[XSec], fmax: float, project_path: str | None = None, python: str | None = None,
          progress=None) -> dict[XSec, Table]:
    """Tables of all cross-sections up to fmax (grid_fmax rounds it up): the cached ones are read, the others
    solved in parallel solver processes. progress(done, total, text) is called from worker threads."""
    fm = grid_fmax(fmax)
    folder = cache_dir(project_path)
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[XSec, Table] = {}
    todo = []
    for xs in dict.fromkeys(items):
        tb = load(xs, fm, folder)
        if tb is None:
            todo.append(xs)
        else:
            out[xs] = tb
    if not todo:
        return out
    from ..mcp.clients import python_exe
    python = python or python_exe()
    cpu = os.cpu_count() or 2
    workers = max(1, min(len(todo), cpu // 2, 4))
    cores = max(1, cpu // workers)
    done = [0]
    if progress:
        progress(0, len(todo), f"Solving {len(todo)} line cross-section(s)")

    def one(xs: XSec) -> Table:
        tb = _run(xs, fm, folder, python, cores)
        done[0] += 1
        if progress:
            progress(done[0], len(todo), xs.label())
        return tb
    with ThreadPoolExecutor(workers) as pool:
        for xs, tb in zip(todo, pool.map(one, todo)):
            out[xs] = tb
    return out


def missing(items: list[XSec], fmax: float, project_path: str | None) -> list[XSec]:
    fm = grid_fmax(fmax)
    folder = cache_dir(project_path)
    return [xs for xs in dict.fromkeys(items) if load(xs, fm, folder) is None]
