# Hertzon on EMerge: what is replaced, why, and what it costs to drop it

Hertzon runs its solves with [EMerge](https://github.com/FennisRobert/EMerge). Over time it added
run-time replacements for parts of EMerge, mostly for speed. This page lists every one of them with
a measurement against EMerge's own code, so that each can either move into EMerge or be dropped from
Hertzon. It is written for the EMerge maintainer and for Hertzon developers.

Changes to EMerge are agreed first and made in EMerge (bug fixes as pull requests to `main`, everything else
on its `hertzon` branch), not as new run-time patches in Hertzon. [EMERGE_CHANGES.md](EMERGE_CHANGES.md)
decides where each item goes.

Measured on 23 October 2026 with EMerge 3.0.0a25 and 3.0.0b5 (PyPI), Hertzon `dev` at `13647d2` plus
the commits on this branch.

## How it was measured

- `examples/benchmark/tools/emerge_bench.py run OUTDIR [CASE ...] [--env NAME=VALUE]` solves each case in a
  fresh process with timers around the expensive phases and stores the result folder.
  `emerge_bench.py compare A B` prints the times side by side and the largest relative difference of
  the S-parameters (last adaptive pass and sweeps) and the far fields.
- Every case runs a fixed number of adaptive passes (4), so two runs solve the same meshes; the tetrahedra
  per pass are compared too. Before a series, Hertzon's warm-up compiles all numba kernels.
- `HERTZON_EMERGE_NATIVE=mt_pardiso,mesh_fallback`,
  `HERTZON_SLOW_ASSEMBLY=1`, `HERTZON_PARDISO_STRUCT=1` and `HERTZON_HYBRID_SWEEP=0` switch single
  replacements off, so EMerge's own code runs instead.
- `examples/benchmark/tools/farfield_kernels.py HUYGENS_NPZ` compares the far field kernels on stored
  boundary fields.
- Computer: Linux container, 4 cores, 15 GB memory. Absolute times are not representative of a workstation;
  the ratios are. Repeated runs of the same configuration differ by about ±3–8 %.

| Case | Model | Elements | Ports | Sweep |
|---|---|---|---|---|
| microstrip | `scaling/small_microstrip.hzn`, thin conductor trace | 1st order, 4k → 12k tets | 2 lumped | interpolating, 101 points |
| hairpin | `scaling/medium_hairpin.hzn` | 2nd order, 6k → 15k tets | 2 lumped | interpolating, 101 points |
| patch | `scaling/large_patch.hzn`, far field sphere | 2nd order, 5k → 12k tets | 1 lumped | interpolating, 101 points |
| waveport | `waveguide_post/post_pec_stable.hzn` | 2nd order, 1k → 2.4k tets | 2 wave ports | interpolating 5–12 GHz (201), discrete 9 points |
| rotman | `RotmanLens_24GHz.hzn`, design RotmanArray, far field sphere | 1st order, 21k → 49k tets | 11 lumped | none (see open issues) |

## Step 1: EMerge 3.0.0a25 → 3.0.0b5

Same Hertzon code, then with the b5 changes (pin, setup without the welcome prompt workaround,
`_thin_conductor_new_mesh` removed because b5 splits the thin conductor DoFs for every new basis).

| Case | a25 total | b5 total | S-parameters, far field |
|---|---|---|---|
| microstrip | 22.1 s | 22.8 s | bit-identical |
| hairpin | 78.4 s | 77.8 s | bit-identical |
| patch | 63.0 s | 61.5 s | bit-identical |
| waveport | 21.2 s | see below | adaptive and discrete: identical to 1e-14 |
| rotman | 64.6 s | 64.7 s | bit-identical |

No phase got slower. The waveport interpolating sweep takes either about 18 s or about 150 s, on a25 as
on b5 (2 of 4 a25 runs hit the 250 solution limit). That is a Hertzon issue, see open issues.

## Step 2: `MWField.interpolate(mapping=...)`

Hertzon evaluates fields at points whose tetrahedron is already known (corners and edge midpoints of every
tetrahedron, the far field surface). It used to replace `basis.interpolate_index` for the call; EMerge 3
takes the mapping directly. Same times, bit-identical results. Done.

## Step 3: EMerge from a pinned commit of its `hertzon` branch (`2811e9a`)

Hertzon now installs EMerge from wheels built from a fixed commit (`packaging/emerge-commit`,
`.github/workflows/emerge-wheels.yml`), so EMerge fixes reach Hertzon without a release on PyPI. `2811e9a`
is 3.0.0b5 plus the worker thread fixes (`e80e840`, `821607b`), the two-sided thin conductor split
(`77beeb6`) and the curve sampling of mesh size fields (`7b2d581`). All five cases against b5:

| Case | b5 | `2811e9a` | Results |
|---|---|---|---|
| microstrip | 37.6 s | 25.0 s | same meshes; S11 within 0.12 dB, S21 within 0.01 dB (thin conductor trace) |
| hairpin | 79.2 s | 60.1 s | meshes differ from pass 2; S21 0.27 dB lower on average, S11 up to 2.6 dB at −13 dB |
| patch | 60.2 s | 64.6 s | bit-identical, far field too |
| waveport | 157.6 s | 155.0 s | adaptive and discrete identical to 1e-14 (interpolating sweep: see open issues) |
| rotman | 66.5 s | 63.5 s | bit-identical, far fields too |

The differences come from the thin conductor split: the hairpin's traces are two-sided thin conductors,
which are now solved with separate fields on both sides (more conductor loss; the EMerge test checks the
loss of a stripline against Wheeler's incremental inductance rule). No case got slower beyond the noise
(the microstrip b5 run was the first after the warm-up and slower than usual).

## Step 4: Hertzon's speed features in EMerge (`e03a085`)

Three of Hertzon's replacements are now EMerge code on its `hertzon` branch, and Hertzon uses them:
the far field of all ports in one pass (FennisRobert/EMerge#76, Rust kernel `emr.sc_multi`), curved
elements (#77, `Settings.mw_curved_elements`, set from the setup option "Curved elements") and the parallel
removal of the PEC rows and columns (#78). Hertzon's own versions are removed (about 1200 lines, among them
`ned2_kernels.py`, the numba copy of EMerge 2.8.9's basis functions). All five cases, `2811e9a` with
Hertzon's versions against `e03a085` with EMerge's:

| Case | `2811e9a` | `e03a085` | Results |
|---|---|---|---|
| microstrip | 32.0 s | 24.2 s | same meshes, S-parameters identical |
| hairpin | 71.2 s | 61.1 s | same meshes, S-parameters identical |
| patch | 73.1 s | 67.4 s | same meshes, S-parameters identical, far field within 5e-10 |
| waveport | 25.5 s | 24.3 s | same meshes (curved post), adaptive and discrete within 2e-14 |
| rotman | 69.0 s | 68.4 s | same meshes, S-parameters identical, far fields within 4e-10 |

No case got slower; the differences are within the run-to-run noise, except that the first waveport run
on `2811e9a` took 104 s: Hertzon's numba kernels for the curved elements were compiled first (80 s, cached
on disk afterwards, normally during the warm-up after the installation). EMerge's kernel is compiled Rust,
so this compilation is gone. The interpolating sweep of the waveport is not reproducible from run to run
(see open issues), on both commits.

## Replacements that stay for now (candidates for EMerge's `hertzon` branch)

Each row: the replacement switched off, i.e. EMerge's own code, relative to Hertzon with all replacements
on (EMerge 3.0.0b5).

| Replacement | microstrip | hairpin | patch | rotman | Results |
|---|---|---|---|---|---|
| PARDISO LDLT off (`HERTZON_PARDISO_STRUCT=1`) | +9 % | +34 % | +34 % | +7 % | same |
| Reduced basis in the interpolating sweep off | +30 % | +48 % | +30 % | – | same within tolerance |
| `mt_pardiso` off, sweep frequencies in parallel | +442 % | out of memory | out of memory | – | – |
| Thin conductor DoF numbering off | ±0 | ±0 | ±0 | ±0 | S identical |
| Conductor sheets in the error estimate off | ±0 | ±0 | ±0 | ±0 | same meshes |

### PARDISO: complex symmetric LDL^T (`fast_assembly._pardiso_solve`)

EMerge factorizes the complex symmetric system matrix with PARDISO mtype 3 (structurally symmetric LU).
mtype 6 (complex symmetric, upper triangle) needs about half the factor memory and is 20–40 % faster on the
second order cases. Hertzon uses it only when EMerge classifies the matrix as symmetric and a random
bilinear test confirms it, and falls back to LU on any PARDISO error.
**Proposal:** move into EMerge's PARDISO interface, with this benchmark.

### PARDISO in parallel sweeps (`mt_pardiso`)

With parallel sweeps, EMerge's worker threads use SuperLU. On the microstrip the sweep took 120 s instead of
14 s; hairpin and patch (15k and 12k second order tetrahedra) ran out of 15 GB memory. Hertzon gives every
worker thread its own PARDISO instance with `mkl_set_num_threads_local`. Two EMerge bugs are fixed on the
way:

1. `AutomaticRoutine` is a singleton (`__new__`), so `SolveRoutine.duplicate()` returns the same object and
   the worker threads share one solver (and can factorize in one PARDISO handle at the same time). Fixed in
   EMerge `e80e840`.
2. The worker routines were dropped in the main thread after each batch. scipy's SuperLU frees a
   factorization only in the thread that created it, and PARDISO only on `reset()`, so every batch left its
   factorizations allocated. Fixed in EMerge `821607b` (FennisRobert/EMerge#72): each worker resets its
   routine on its own thread. Hertzon's own release only worked before EMerge moved the threaded solve into
   `_dispatch_jobs` (3.0.0b5); it is removed with the move to `2811e9a`, together with the singleton
   workaround and the pipelined assembly, which did not apply to b5 either.

**Proposal:** PARDISO per worker thread on EMerge's `hertzon` branch.

### Reduced basis in the interpolating sweep (`hybrid_sweep`)

Check frequencies of the interpolating sweep are first solved in the span of the earlier solutions (no
factorization); only those that the basis cannot reach are factorized. 30–48 % of the total time.
**Proposal:** compare with EMerge's `run_adaptive_sweep` (AAA) before deciding whether this goes upstream
or Hertzon switches to it.

### Error estimate with many excitations

EMerge 3 computes the error estimate in Rust (`emr.adaptive_mesh.compute_error_single`), in parallel over
the tetrahedra, and calls it once per excitation. Rotman lens, pass 3 (35872 tetrahedra, 11 excitations):
2.5 s with 4 threads, 7.9 s with `RAYON_NUM_THREADS=1` (3.1×). The time grows linearly with the number of
excitations (about 6 µs per tetrahedron and excitation on one core, the same on the 2-port microstrip), and
it is about 9 % of the Rotman run time. Hertzon's former parallel kernel for EMerge 2.8.9 also evaluated all
excitations in one pass over the tetrahedra (geometry and neighbour data once).
**Proposal (low priority):** a multi-excitation variant of `compute_error_single`; nothing to keep in Hertzon.

### Robustness only (no effect unless something fails)

- `apply_mesh_fallback`: when gmsh fails on trimmed B-spline faces of CAD files, mesh again without
  curvature based sizing.
- `apply_port_field_lookup`: a sample of a port integral outside the tetrahedra next to the port is looked up in
  the whole mesh instead of counting as zero field. **Proposal:** the same fallback in `interpolate_Ef`.

## Removed

- `fast_assembly.submatrix` (parallel removal of the PEC rows and columns, 5–10 %): in EMerge since
  FennisRobert/EMerge#78 (Rust, 9–13× faster than scipy's indexing, bit-identical).
- The far field kernel of `farfield_post.stratton_chu_multi` (phase factors once for all ports, 3.7–4.2×
  faster than one call per port): `farfield_post` calls EMerge's `emr.sc_multi` (#76), which is about
  twice as fast again.
- The curved elements (`emerge_patches.apply_curved`, the element matrices in `curvilinear.py`,
  `ned2_kernels.py`): developed in Hertzon and moved to EMerge's `hertzon` branch with #77. `curvilinear.py` keeps the curved map for the plots.
- `bridge._with_conductor_sheets`, which passed conductor sheets to the error estimate like PEC faces.
  It changed nothing: microstrip and hairpin give the same meshes and S-parameters without it (the face
  terms of EMerge's estimate contribute practically nothing, see
  `emerge-integration/fehlerschaetzer/amr-vergleich-results.md` in the project files).
- `_thin_conductor_new_mesh` (fixed in EMerge 3.0.0b5).
- `apply_thin_conductor`, which kept `tet_to_field` in the numbering the system was assembled with. EMerge
  `77beeb6` makes the split itself right: every tetrahedron on one side of the sheet uses the copied DoFs,
  in the assembly as in the fields, so both sides of a thin conductor are solved separately.
- The worker thread fixes of `mt_pardiso` (shared routine, release of the factorizations, pipelined
  assembly), see above.
- `_robust_eigs`, the ARPACK retry of the port mode solve: since 3.0.0b5 EMerge factorizes the shift once,
  oversamples the Krylov space and skips a failing shift. No failure in three waveport runs without it nor
  in any benchmark run.
- Replacements written for EMerge 2.8.9 that never run on EMerge 3, because EMerge 3 has the code in its
  compiled core: parallel error estimate (`fast_error.py`), vectorised mesh import (`Mesh3D._pre_update`),
  element matrices and CSC pattern (`fast_assembly`), `AMRPoints` size field patch. About 1300 lines.
  After the removal: same meshes, S-parameters and far fields, times within the noise (−7 % to −0 %).

## Hertzon features built on EMerge (stay in Hertzon)

Hertzon's own refinement methods, lazy field evaluation, stored boundary
fields for far fields after the solve, far field quantities (realized gain, efficiency, superposition of
ports), face matching. The curved elements were developed in Hertzon and now live in EMerge's `hertzon` branch (#77); Hertzon
switches them on and draws the curved tetrahedra.

## Open issues found on the way (Hertzon)

- **Interpolating sweep not reproducible**: the same waveport run needs either 25–29 or 250 solutions
  (limit), with up to 0.13 difference in S above cutoff. Below the waveguide cutoff the fit does not
  converge, and whether the reduced basis accepts a check point depends on the run.
- **Fast sweep memory**: the Rotman lens sweep (11 ports, 49k tetrahedra, 9 frequencies) grows past 14 GB.
- **Symmetry planes and ports**: the port impedance factor of a symmetry plane (`ImpedanceMultiplier`) is
  not applied in the solver, so a port cut by a symmetry plane is referred to the impedance of the half port.
  Far fields are mirrored at symmetry planes (`farfield_post.mirrored`, same scaling as EMerge's
  `farfield(syms=...)` after #75); directivity and pattern do not depend on the port impedance.
