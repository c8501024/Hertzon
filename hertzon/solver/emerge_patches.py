"""Run-time fixes for EMerge 3 (applied once in the solver worker): mesh retry for broken CAD faces and the grid
accelerated size field of Hertzon's re-mesh refinement. Curved elements are EMerge's own since
FennisRobert/EMerge#77 (Settings.mw_curved_elements, set in bridge.py). docs/EMERGE_INTEGRATION.md lists
each one with its status upstream."""
from __future__ import annotations

import os

import numpy as np

_DONE = False


def native(name: str) -> bool:
    """True when the Hertzon replacement ``name`` is switched off for a comparison with plain EMerge:
    HERTZON_EMERGE_NATIVE=mt_pardiso,mesh_fallback"""
    return name in os.environ.get("HERTZON_EMERGE_NATIVE", "").replace(" ", "").split(",")


def apply() -> None:
    global _DONE
    if _DONE:
        return
    _DONE = True
    try:
        from . import fast_assembly
        fast_assembly.apply()
    except Exception as ex:  # noqa: BLE001
        from loguru import logger
        logger.warning(f"Fast matrix assembly not available ({ex}); using EMerge's assembly")
    try:      # after fast_assembly: wraps its PARDISO solve
        from . import mt_pardiso
        if not native("mt_pardiso"):
            mt_pardiso.apply()
    except Exception as ex:  # noqa: BLE001
        from loguru import logger
        logger.warning(f"Parallel sweeps with PARDISO not available ({ex})")
    try:      # wraps EMerge's SolveRoutine.solve
        from . import hybrid_sweep
        hybrid_sweep.apply()
    except Exception as ex:  # noqa: BLE001
        from loguru import logger
        logger.warning(f"Reduced basis for the interpolating sweep not available ({ex})")
    if not native("mesh_fallback"):
        apply_mesh_fallback()
    if not native("port_field_lookup"):
        apply_port_field_lookup()


def apply_port_field_lookup() -> None:
    """EMerge evaluates the field for the S-parameters only in the tetrahedra that share a triangle with the port
    face; a sample outside them finds no tetrahedron and counts as zero field. The voltage line of a lumped port
    runs along the edge of its sheet, where refinement moves samples into neighbouring tetrahedra: the port voltage
    then fell by the share of those samples, S jumped from pass to pass and the accepted power was too high while
    the radiated power stayed right. Such samples are now looked up in the whole mesh."""
    from emerge._emerge.elements.nedelec2 import Nedelec2
    plain = Nedelec2.interpolate_index

    def interpolate_index(self, xs, ys, zs, tetids=None, usenan=True):
        vals = plain(self, xs, ys, zs, tetids, usenan)
        if tetids is not None:
            miss = np.flatnonzero(vals < 0)
            if miss.size:
                vals[miss] = plain(self, xs[miss], ys[miss], zs[miss], None, usenan)
        return vals
    Nedelec2.interpolate_index = interpolate_index


# ------------------------------------------------------------------------------------------------
# Adaptive refinement size field
# ------------------------------------------------------------------------------------------------
# size(x) = min(lc, min_i max(s_i, A_i + c*|x - p_i|))  over *all* refinement points (they accumulate pass
# after pass) for every gmsh size query (emerge_compat.AMRPoints, the size field of EMerge 2.8.9). Evaluated
# with a uniform grid over the points and a pruned shell search; the kernel is compiled once (cached on disk).

_KERNEL = None


def _kernel():
    global _KERNEL
    if _KERNEL is None:
        from numba import njit

        @njit(cache=True, nogil=True, fastmath=True)
        def query(x, y, z, lc, px, py, pz, ps, pa, c, amin, lo, h, dims, start, order, ring0):
            best = lc
            nx, ny, nz = dims[0], dims[1], dims[2]
            ix = min(max(int((x - lo[0]) / h), 0), nx - 1)
            iy = min(max(int((y - lo[1]) / h), 0), ny - 1)
            iz = min(max(int((z - lo[2]) / h), 0), nz - 1)
            # distance from the query to the grid box, for points outside it
            ox = max(lo[0] - x, 0.0, x - (lo[0] + nx * h))
            oy = max(lo[1] - y, 0.0, y - (lo[1] + ny * h))
            oz = max(lo[2] - z, 0.0, z - (lo[2] + nz * h))
            off = (ox * ox + oy * oy + oz * oz) ** 0.5
            rmax = max(nx, ny, nz)
            # rings closer than ring0 contain no points (chessboard distance transform)
            for r in range(ring0[(ix * ny + iy) * nz + iz], rmax + 1):
                g = max(r - 1, 0) * h
                bound = amin + c * (off * off + g * g) ** 0.5
                if bound >= best:
                    break
                for jx in range(max(ix - r, 0), min(ix + r, nx - 1) + 1):
                    xface = abs(jx - ix) == r
                    for jy in range(max(iy - r, 0), min(iy + r, ny - 1) + 1):
                        if xface or abs(jy - iy) == r:
                            z0 = max(iz - r, 0)
                            z1 = min(iz + r, nz - 1)
                            zs = 1
                        else:
                            # interior column of the shell: only its two end caps
                            z0 = iz - r
                            z1 = iz + r
                            zs = 2 * r if r > 0 else 1
                        for jz in range(z0, z1 + 1, zs):
                            if jz < 0 or jz >= nz:
                                continue
                            cell = (jx * ny + jy) * nz + jz
                            for k in range(start[cell], start[cell + 1]):
                                i = order[k]
                                dx = x - px[i]
                                dy = y - py[i]
                                dz = z - pz[i]
                                v = pa[i] + c * (dx * dx + dy * dy + dz * dz) ** 0.5
                                if v < ps[i]:
                                    v = ps[i]
                                if v < best:
                                    best = v
            return best
        _KERNEL = query
    return _KERNEL


def _size_args(amr, gr: float, _qf: float):
    """Grid acceleration structure for EMerge's AMR size function (None when it does not apply)."""
    if amr._amr_coords is None:
        return None
    P = np.ascontiguousarray(amr._amr_coords, dtype=np.float64)
    ns = np.ascontiguousarray(amr._amr_ratios * amr._amr_sizes, dtype=np.float64)
    A = ns / gr
    c = float(-(1 - gr) / gr * _qf)
    if P.shape[1] == 0 or c <= 0:
        return None
    lo = P.min(axis=1)
    span = np.maximum(P.max(axis=1) - lo, 1e-12)
    n = P.shape[1]
    h = float(max(span.max() / 128.0, (np.prod(span) / max(n / 4.0, 1.0)) ** (1 / 3), 1e-12))
    dims = np.maximum(np.ceil(span / h).astype(np.int64), 1)
    ijk = np.minimum(((P - lo[:, None]) / h).astype(np.int64), (dims - 1)[:, None])
    cell = (ijk[0] * dims[1] + ijk[1]) * dims[2] + ijk[2]
    order = np.argsort(cell, kind="stable").astype(np.int64)
    counts = np.bincount(cell, minlength=int(np.prod(dims)))
    from scipy.ndimage import distance_transform_cdt
    ring0 = distance_transform_cdt((counts == 0).reshape(tuple(dims)), metric="chessboard")
    ring0 = np.ascontiguousarray(ring0.ravel(), dtype=np.int64)
    start = np.zeros(len(counts) + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    px, py, pz = (np.ascontiguousarray(P[i]) for i in range(3))
    return (px, py, pz, ns, np.ascontiguousarray(A), c, float(A.min()), np.ascontiguousarray(lo), h, dims,
            start, order, ring0)


def amr_size_at(amr, gr: float, _qf: float, pts: np.ndarray, lc: float = 1e300) -> np.ndarray:
    """EMerge's AMR target element size evaluated at the points ``pts`` (3, n)."""
    args = _size_args(amr, gr, _qf)
    pts = np.asarray(pts, float)
    if args is None:
        return np.full(pts.shape[1], lc)
    q = _kernel()
    return np.array([q(x, y, z, lc, *args) for x, y, z in pts.T.tolist()])


def apply_mesh_fallback() -> None:
    """Trimmed B-spline faces of CAD exports (degenerate poles, edges of a few micrometres) can break
    gmsh's surface mesher when the element size follows the curvature ("Impossible case in triangle
    search"). The mesh is then generated again without curvature based sizing; the size fields
    (wavelength, mesh operations, refinement) stay in force, so only curved faces start coarser."""
    import gmsh
    orig = gmsh.model.mesh.generate
    if getattr(orig, "_hertzon_fallback", False):
        return

    def generate(dim=3):
        try:
            return orig(dim)
        except Exception as e:  # noqa: BLE001
            curv = gmsh.option.getNumber("Mesh.MeshSizeFromCurvature")
            if dim < 2 or curv == 0:
                raise
            from .bridge import warn
            warn(f"Mesh failed ({e}); meshing again without curvature based element sizes")
            gmsh.model.mesh.clear()
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            try:
                out = orig(dim)
            finally:
                gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", curv)
            # after a failed 3D pass gmsh can report success with an empty volume mesh
            if dim >= 3 and not any(len(t) for t in gmsh.model.mesh.getElements(3)[1]):
                raise RuntimeError(f"{e}. The geometry has overlapping or nearly touching surfaces (often sliver "
                                   "faces of a CAD export); repair them in CAD or import a cleaned STL") from e
            return out
    generate._hertzon_fallback = True
    gmsh.model.mesh.generate = generate
