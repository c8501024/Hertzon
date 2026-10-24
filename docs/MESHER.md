# hzmesh: local Delaunay refinement for adaptive meshing

hzmesh is Hertzon's own mesher for the adaptive solution loop. It refines the tetrahedral mesh of a
model **locally**, as commercial solvers do: every node and every element outside the region the error estimator
marks stays exactly as it is, and new nodes appear only where the field needs them. EMerge solves on
the result as usual.

- Core: C++17 with a C interface, `Hertzon/mesher/src/hzmesh.cpp` and `hzmesh.h`, built into
  `Hertzon/mesher/_bin/` (`libhzmesh.so` for Linux, `hzmesh.dll` for Windows).
- Python side: `Hertzon/mesher/core.py` (ctypes binding, gmsh in/out), `Hertzon/mesher/adapt.py`
  (size field, growth control, one adaptive pass) and `Hertzon/mesher/replay.py` (repeat a pass
  offline).
- Integration: `Hertzon/solver/bridge.py` (refinement methods, fallback),
  `Hertzon/solver/curvilinear.py` (curved elements), `Hertzon/solver/emerge_patches.py` (faster
  mesh import).

## Contents

- [Refinement methods](#refinement-methods)
- [Why local Delaunay refinement](#why-local-delaunay-refinement)
- [Algorithm](#algorithm)
- [Robustness](#robustness)
- [Curved elements](#curved-elements)
- [Chord rule (off by default)](#chord-rule-off-by-default)
- [Faster EMerge import](#faster-emerge-import)
- [Threading and parallelism](#threading-and-parallelism)
- [Results](#results)
- [Usage and settings](#usage-and-settings)
- [Building](#building)
- [C interface](#c-interface)
- [Known limitations](#known-limitations)
- [How it was developed](#how-it-was-developed)

## Refinement methods

Setup > Advanced > **Mesh refinement** (`RefinementMethod`):

| Method | What it does | Meshes nested? |
|---|---|---|
| **Local (Delaunay)** (default) | hzmesh refines the current mesh around the worst elements | yes, old nodes never move |
| Remesh (slow) | gmsh meshes the whole model again with EMerge's size field | no |
| Remesh (stable, experimental) | gmsh re-meshes, but the size field holds every current node's size and only shrinks it at the worst elements | nearly |

If hzmesh is not available or a pass fails, Hertzon re-meshes that pass with gmsh and writes a
warning to the log. Older project values (`Local (Delaunay, experimental)`, `Zone (experimental)`,
`Remesh`) are mapped to the current names on load (`Hertzon/model/schema.py`).

## Why local Delaunay refinement

A complete re-mesh in every pass makes Delta S compare two unrelated meshes. From about pass 7 on,
this re-meshing noise is as large as the convergence threshold and Delta S jumps. Local refinement avoids that:
it refines in place and nested, so Delta S only measures real progress. Two things are needed at
the same time:

1. Outside the error zone the mesh stays **exactly** the same (otherwise noise in Delta S).
2. Inside the zone the mesh is not only split but meshed **well**, including surfaces.

No off-the-shelf tool did both. What was tried (patch antenna, second order):

| Approach | Nested? | Quality | Outcome |
|---|---|---|---|
| Remesh (gmsh, from scratch) | no | good | Delta S jumps in late passes |
| Bisection (newest vertex) | yes | gets worse, bad elements are inherited | smooth, but too many tets |
| Bisection + gmsh optimiser | almost | volume fine, surfaces not repaired | Delta S 0.006 at 22k tets |
| Mmg, global | no | good | noisy, slow |
| Mmg zone re-mesh (frozen outside) | yes | good | dropped, fell back to re-mesh |
| TetGen `-r -q` | no, only about 20 % of the old nodes stay | good | growth not controllable (x4-5 per pass), AGPL |
| gmsh Delaunay with fixed old nodes | yes | collapses (5 % quality 0.53 -> 0.01) | unusable |

Bisection keeps the mesh nested and conforming, but flat start tets stay flat (exactly what a thin
patch substrate is made of), and halving can never improve quality. Delaunay insertion with a
cavity (Bowyer-Watson) also keeps every old node, so it is nested too. It reconnects the
neighbourhood of each new point, though, so bad elements are repaired while refining instead of
inherited.

## Algorithm

One adaptive pass (`adapt.refine_model`):

1. **Input from gmsh**
   - nodes with their classification (point, curve, surface or volume, plus the entity tag);
   - tets with their volume tag (material);
   - boundary triangles with their surface tag (ports, PEC, boundaries) and curve segments.
2. **Size field** (`adapt.py`)
   - Current size h per node: mean of EMerge's element sizes of the surrounding tets.
   - The nodes of the k worst tets get the target 0.7 h.
   - The field is graded towards the rest of the mesh with the growth rate (Dijkstra in C++,
     `hzm_grade`).
   - Zone = nodes whose target is below their current size. Outside the zone **nothing** changes.
3. **Refine**, in the order curve segments -> boundary triangles -> tets
   - Size: if the **longest edge** is longer than h, its midpoint is inserted (on the curve,
     surface or in the volume the edge belongs to).
   - Shape: elements with a bad circumradius-to-edge ratio (tet > 2, triangle > 1.3) get their
     circumcentre, but only inside the zone.
   - Insertion uses a Bowyer-Watson cavity that is bounded by CAD surfaces and curves
     (constrained Delaunay), so no material or port boundary is ever cut.
   - A boundary triangle or segment that the new point encroaches on is split first.
   - New points on curved CAD surfaces and curves (sphere, cylinder, spline) are projected onto
     the real geometry with `gmsh.model.getClosestPoint`.
4. **Improve**, new elements only: 2-3 and 3-2 flips, edge removal (optimal triangulation of 3
   to 7 tets around an edge) and smoothing of the new nodes (surface nodes stay on their surface).
   Old nodes never move.
5. **Growth control**: k is chosen so that the mesh grows by the refinement percentage (0.8 to
   1.25 times the target). A trial on a copy (`hzm_clone`) costs milliseconds; at most 6 trials.
6. **Back to EMerge**: the mesh is written into the gmsh model (old node tags are kept), then the
   normal EMerge import runs. Ports, materials and far field therefore work unchanged.

## Robustness

- Shewchuk's exact predicates (`orient3d`, `insphere`, `predicates.c`), compiled with
  `-ffp-contract=off`. The Linux library and the Windows DLL produce bit-identical meshes.
- Every cavity is checked before it replaces the old elements: closed hull, no face used twice, no
  holes, all boundary faces tagged, and volume preserved for flat entities. If a check fails, the
  point is dropped and the mesh stays valid.
- A cavity must not swallow a free curve (edge of a sheet, embedded curve) or a boundary triangle
  that is not being re-triangulated. Such triangles are split together with the cavity, otherwise
  the point is dropped.
- After every pass `hzm_check` verifies the whole topology. On a problem or an exception Hertzon falls
  back to a gmsh re-mesh for that pass and logs a warning.
- Stress tests on box, sphere, cylinder, coax and patch over many passes
  (`Hertzon/mesher/src/test/t_stress.py`): always valid, old nodes unmoved, volumes and surfaces
  preserved.
- `HZMESH_DUMP=<folder>` saves the input of every pass; `python -m Hertzon.mesher.replay FILE`
  repeats a failed refinement offline.

## Curved elements

EMerge treats every tet as affine, with straight edges and flat faces. A round post, rod or
resonator becomes a polygon. For strongly reflecting curved objects this faceting error dominates
the S-parameters, and the error estimator cannot see it, because it rates the field on the mesh and
not the mesh's distance from the geometry.

![Sphere: CAD, mesh with curved tets, surface current](screenshots/mesher_curved_sphere.png)

Tets on curved surfaces therefore get quadratic edges (isoparametric, 10 nodes,
`Hertzon/solver/curvilinear.py`):

- The midpoint of every mesh edge on a curved CAD surface or curve is moved onto the real geometry
  with `gmsh.model.getClosestPoint` (displacement d_e).
- Mapping: x = y + sum d_e phi_e(y) with phi_e = 4 lambda_i lambda_j on the straight tet y,
  Jacobian D = I + sum d_e (x) grad phi_e.
- The covariant Piola transform can be written as a straight tet with a pointwise material:
  - curl term: mu^-1 -> D^T mu^-1 D / det D
  - mass term: eps -> det D D^-1 eps D^-T
- EMerge's basis functions therefore stay unchanged. Only the element matrices of the curved tets
  are recomputed after EMerge's assembly (numba, parallel, 64-point quadrature of degree 7).
- Conforming: a face without a curved edge stays exactly flat and keeps its tangential trace. Every
  tet that contains a curved edge is curved itself and uses the same displacement for that edge.
- Validity: det D >= 0.3 at 79 check points per tet; otherwise the displacement of the affected
  edges is halved, down to zero if needed, and checked again.
- An independent reference (finite-difference field of the mapping, anisotropic complex materials,
  both tet orientations) confirms the element matrices: deviation 1e-10 for small curvature, at most
  3e-5 at det D of about 0.5.
- Curved are PEC, metal (finite conductivity), symmetry and material faces, including ellipses,
  splines, pipes and lofts. For metal only the very small impedance term stays on the flat facet.
- Straight stay edges on faces with their own surface integrals: ports, absorbers, impedance and
  RLC boundaries, thin conductors and periodic boundaries.
- A curved face that touches a port stays completely straight. Otherwise the straight port mode
  would see a curved line behind it, i.e. an impedance step (coax, circular waveguide).
- With zero displacement the matrices of all 7 EMerge basis types match bit for bit (relative
  deviation 5e-16). The post volume is right to 0.09 % instead of 4.7 %.
- Setting: Setup > Options > **Curved elements on curved surfaces** (default on).

## Chord rule (off by default)

Before curved elements existed, hzmesh split edges on curved surfaces whose chord deviated too far
from the geometry (parameter `sag`, fraction of the target size). With curved elements this only
costs tets, so `sag` is 0 by default. `HZMESH_SAG=0.02` turns it on for experiments. It measures
the deviation relative to the end points and stops at edges below 2 % of the target size, so a node
that had to be inserted next to the surface cannot trigger endless splitting.

## Faster EMerge import

After the mesher, `Mesh3D._pre_update` was the most expensive step: it builds edges, triangles and
all index tables in Python loops per tet. `Hertzon/solver/emerge_patches.py` replaces these loops
with numpy sorting and searching:

| Mesh | Original | Hertzon |
|---|---|---|
| 35k tets | 1.9 s | 0.3 s |
| 74k tets | 5.2 s | 1.3 s |
| 296k tets | 44-60 s | 3.7 s |

- All arrays and dicts are **bit-identical** to the original, including order (tested on 6 meshes).
- A block is only replaced if the EMerge source matches exactly; with another EMerge version or on
  an error the original runs.
- `HERTZON_SLOW_IMPORT=1` turns the speed-up off.

## Threading and parallelism

hzmesh currently runs **serially** on one core:

- The C++ core starts no threads and uses no OpenMP; the `thread_local` buffers in `hzmesh.cpp`
  are reused scratch memory, not parallelism. The build scripts use neither `-fopenmp` nor
  `-pthread`.
- Python calls the core once per pass, and the up to 6 growth-control trials run one after another,
  each on its own copy.
- Only the solver runs in parallel (EMerge, PARDISO, the numba kernels of the curved elements).

At 570k tets the core needs 0.15-0.3 s per pass, less than 1 % of the solve time, so parallelising
has not paid off yet. If it ever does, there are two deterministic options:

- Insert the points of a sweep in groups whose cavities do not touch (graph colouring on the
  cavity tets).
- Split the zone into spatially separate clusters, one thread each, with locked cluster borders that
  are finished serially at the end.

Both keep the result unchanged as long as the insertion order stays deterministic. Running the
growth-control trials concurrently would be simpler, but only saves milliseconds.

## Results

Benchmark: WR90 waveguide (10 GHz) with a centred full-height post. The field is exactly
z-independent there, so there is an accurate 2D reference (isoparametric P2 FEM,
`Hertzon/mesher/src/test/ref_wgpost.py`, converged to 6 digits). Error = |S11 - S11,ref|. All runs:
second order, at most 12 passes, Delta S <= 0.0005 in 2 consecutive passes, 30 % refinement. The
projects are in `examples/benchmark`.

Metal post r = 1.5 mm, reference S11 = 0.18037 + 0.93542j:

| Method | straight elements | curved elements |
|---|---|---|
| Local (Delaunay) | 0.013 at 23.3k tets, drifts away | **0.0006** at 16.9k tets, converged (81 s) |
| Remesh | 0.0030 at 34.7k tets, not converged | **0.0011** at 7.7k tets, converged (70 s) |
| Remesh (stable) | 0.0074 at 15.8k tets, not converged | **0.0007** at 9.7k tets, converged (86 s) |

Dielectric post r = 2 mm, eps_r 9.8, tan delta 1e-4, reference S11 = -0.33672 + 0.77887j:

| Method | straight elements | curved elements |
|---|---|---|
| Local (Delaunay) | 0.0044 at 23.5k tets, not converged | **0.0003** at 23.5k tets, converged (178 s) |
| Remesh | 0.0021 at 29.1k tets, not converged | **0.0006** at 15.8k tets, converged (143 s) |

- With straight elements an error of 0.002-0.013 remains that no refinement removes quickly.
- With curved elements the error falls smoothly with mesh size for every method, and the
  convergence criterion stops close to the right value: about 5 to 15 times smaller error at the
  same tet count.
- The run times with curved elements include the one-time numba compilation of about 40 s.
- Local and Remesh are on par with curved elements. Local needs about 0.2 s per pass instead of
  0.6-5 s for gmsh; the solve dominates in both cases.

Patch antenna (no curved surfaces, no exact reference): the mesh costs 3.9 s with Local against
16 s (Remesh) and 50 s (Remesh stable), because only the zone is touched.

## Usage and settings

In the GUI: Setup > Advanced > **Mesh refinement** = Local (Delaunay). *Maximum refinement per
pass (%)* and *Mesh growth rate* work as for Remesh.

Environment variables for experiments:

| Variable | Effect |
|---|---|
| `HZMESH_SHRINK` | target size of the marked elements relative to their size (default 0.7) |
| `HZMESH_GRADE=0` | no grading of the size field |
| `HZMESH_DUMP=<folder>` | save the input of every pass (for `replay`) |
| `HZMESH_LIB=<path>` | load another hzmesh library |
| `HZMESH_SAG=<fraction>` | turn on the chord rule |
| `HERTZON_CURVED=0` / `=1` | force curved elements off or on |
| `HERTZON_CURVED_CHECK=1` | log straight vs. curved volume |
| `HERTZON_SLOW_IMPORT=1` | use EMerge's original mesh import |

## Building

```
Hertzon/mesher/src/build.sh     # Linux; with mingw-w64 installed it also builds the Windows DLL
Hertzon/mesher/src/build.cmd    # Windows with MinGW-w64 (gcc/g++ in PATH, e.g. MSYS2 or w64devkit)
```

The libraries land in `Hertzon/mesher/_bin/`. Always compile with `-ffp-contract=off`, otherwise
the exact predicates lose their guarantees and Linux and Windows meshes differ.

## C interface

`hzmesh.h`, all indices 0-based, used from Python through ctypes (`core.py`):

| Function | Purpose |
|---|---|
| `hzm_create` | mesh from nodes (with classification), tets (with volume tag), tagged triangles and segments |
| `hzm_clone`, `hzm_free` | copy (for growth trials) and free |
| `hzm_set_curved`, `hzm_set_projector` | mark curved entities, callback that projects onto the CAD |
| `hzm_set_size` | target size and zone flag per node |
| `hzm_grade` | graded size field: `min(hcap, min_z h0[z] + slope * path distance)` |
| `hzm_refine` | refine and improve; parameters in, 32 statistics out |
| `hzm_check` | topology and geometry check, number of problems |
| `hzm_sizes`, `hzm_get` | read the mesh back |
| `hzm_quality` | mean ratio and minimum dihedral angle per tet |
| `hzm_last_error` | message of the last error or warning |

## Known limitations

- Curved port faces are not implemented: the 2D mode solver and the port integrals would need to
  work curved as well. Coax and circular waveguide ports keep straight elements on the adjacent
  conductor faces.
- Faceted primitives (NumberOfSegments or NumberOfVertices > 0) are flat and get no curved
  elements. Use the true geometry (value 0) for round bodies.
- With many CAD faces, writing the mesh back to gmsh takes longer (one call per face and curve):
  2 s per pass for a faceted sphere with 1600 faces, 0.2 s for smooth models.
- The error estimator still works on the straight tets. This only affects which tets are marked,
  not the result.
- A few tets with 3-8 degree dihedral angles can remain. They almost all come from the gmsh start
  mesh in thin substrates; removing them would mean moving old nodes, so the meshes would no longer
  be strictly nested.
- The error estimator and marking come from EMerge. The slow convergence of the patch (S11 still
  drifts at 20-40k tets) belongs to the problem; Remesh only hides the drift behind its noise.

## How it was developed

The adaptive meshing in Hertzon (hzmesh, curved elements, the parallel error estimator, the faster
import and the convergence loop) was developed by [c8501024](https://github.com/c8501024) with
AI-assisted programming (Claude Code); most commits in this area are authored by the assistant.

- **c8501024**: set the goals (nested local refinement without remeshing noise in Delta S, geometry-true
  curved surfaces), defined the tests and benchmarks and gave the instructions for running them,
  evaluated the results, and decided which approach to keep. The approaches compared above
  (bisection, Mmg, TetGen, gmsh with fixed nodes, remesh variants) were tried and dropped on that basis.
- **AI assistant**: wrote most of the code, the documentation and the test scripts following these
  instructions.
- **EMerge** ([Robert Fennis](https://github.com/FennisRobert/EMerge)): FEM solver, basis functions,
  error estimator and element marking, which Hertzon builds on.
