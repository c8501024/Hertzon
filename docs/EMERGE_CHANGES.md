# Where Hertzon's EMerge changes go

Hertzon still changes parts of [EMerge](https://github.com/FennisRobert/EMerge) at run time
(see [EMERGE_INTEGRATION.md](EMERGE_INTEGRATION.md) for what each change does and what it is worth in
speed). Long term, none of this should stay as a run-time patch in Hertzon. This page decides for each
item where it goes instead, and why.

## The rules

The EMerge maintainer set the rules in the
[`CLAUDE.md` of EMerge's `hertzon` branch](https://github.com/FennisRobert/EMerge/blob/hertzon/CLAUDE.md).
In short:

| Rule | Content |
|---|---|
| R1 | A bug fix in EMerge itself (wrong results, crashes, broken API) is **always a PR to `main`**, one fix per PR, with a test. |
| R2 | A change that only matters for the Hertzon GUI stays on `hertzon`, never a PR to `main`. |
| R3 | Code written by an AI is marked `# [Written by Claude]` (Python) or `// [Written by Claude]` (Rust). |
| R4 | New technical features in EMerge's core (elements, solvers, assembly, EM formulations) stay on `hertzon`. A PR to `main` is discouraged: the maintainer wants to write this code himself. |
| R5 | Code already marked as AI written (AURA refinement, adaptive frequency sweep) may be improved freely, also on `main`. |
| R6 | On `hertzon`, add new code next to the existing code instead of editing it ("add, don't rewrite"), so `main` keeps merging into `hertzon` without conflicts. |

Hertzon adds two rules of its own:

| Rule | Content |
|---|---|
| H1 | Every change to EMerge is agreed with the Hertzon maintainer first. EMerge changes are made in EMerge (on `hertzon` or as a PR to `main`), not as new run-time patches in Hertzon. |
| H2 | Use of the public EMerge API that only the GUI needs stays in Hertzon. It is no patch and needs no change in EMerge. |

`main` is protected: changes reach it only as pull requests that the maintainer merges. Fix branches for
these pull requests start from `main`, never from `hertzon`.

## Decisions

### Bugs: pull request to `main` (R1)

| Item | Hertzon code today | Why `main` | Status |
|---|---|---|---|
| Parallel sweep threads share one solver (`AutomaticRoutine` singleton) | – | Crashes in MKL; affects every user of parallel sweeps | Fixed on `main` (`e80e840`); Hertzon's workaround removed |
| Factorizations of sweep worker threads are never released | – | Memory leak in every threaded sweep: scipy's SuperLU frees a factorization only in the thread that created it, and EMerge drops the worker routines in the main thread (+350 MB per batch of 4 frequencies on a 15k unknown matrix) | Fixed on `main` (#72, `821607b`); Hertzon's workaround removed |
| Thin conductor DoF numbering differs between assembly and post-processing | – | Fields and surface currents at every thin conductor | Fixed by the maintainer on `main` (`77beeb6`): both sides of the sheet are now solved separately. Hertzon's patch removed |
| Port mode solve ends the whole solve on `ArpackNoConvergence` | `emerge_patches._robust_eigs` | – | No PR: since 3.0.0b5 the port mode solvers factorize the shift once, use an oversampled Krylov space and skip a failing shift. Three waveport runs without Hertzon's retry and all benchmark runs: no failure. Dropped from Hertzon |
| Error estimate counts the current jump at finite-conductivity sheets as error | – | – | No PR. The face terms of EMerge's estimate contribute practically nothing (unnormalised coefficients); normalising them made the adaptive meshing worse on four models. Hertzon's patch changed no mesh and is removed |
| gmsh fails on trimmed B-spline faces of CAD files | `emerge_patches.apply_mesh_fallback` | Meshing crash for imported CAD, not GUI specific | To do, low priority |
| Far field with symmetry planes: radiated power scaled by `factor**2` although only the simulated part is integrated | – | Wrong radiated power (and directivity 3 dB too high per plane) for every user of `syms` | Fixed on `main` (#75, `0a607f2`), checked with a dipole as full and half model. Hertzon mirrors its stored surface fields itself with the same scaling (`farfield_post.mirrored`) |
| A repeated modal analysis at the same frequency adds its modes to the ones already stored (`ModalPort.try_add_mode`); `run_sweep` analyses the mean frequency and then every frequency, so a port with several modes mixes the modes of two analyses | `bridge.fresh_port_modes` clears the modes of ports with several modes before every solve (public `clear_modes()`, no patch) and solves them once per solve | Wrong S-parameters (not lossless on a lossless model) of every port with several modes | On `hertzon` (FennisRobert/EMerge#84, a new analysis at the same frequency replaces the modes); PR to `main` FennisRobert/EMerge#82. `fresh_port_modes` stays: it keeps one modal analysis per solve |
| Degenerate port modes (equal propagation constant, e.g. two conductors in a homogeneous dielectric) are not orthogonal: the eigen solver returns two almost equal modes (cross power 0.93 of the mode power) | – | A port with several conductors cannot represent all its TEM fields: wrong S-parameters | On `hertzon` (FennisRobert/EMerge#84, the modes of one eigenvalue are made real and orthogonal in the power product); PR to `main` FennisRobert/EMerge#83. Two wires in a box: \|S^H S − I\| of the modal S from 0.54 to 1e-6, of the terminal S from up to 0.16 to below 0.01 (`tests/test_terminal.py::test_two_wires_lossless`) |
| The eigen solver may return a degenerate lossless eigenspace as complex combinations of its real modes, which no phase factor makes real; the orthogonalized modes then stay complex | – | On refined meshes the two-wire modes kept a cross term up to 0.02 in the unconjugated product, and the terminal S was off from unitary by up to 0.06, depending on the start vector of the eigen solver | Real basis of the eigenspace (leading singular vectors of [Re V, Im V]) before the orthogonalization: on `hertzon` with `MultiModeWavePort`, added to FennisRobert/EMerge#83 for `main`. Two wires: \|S^H S − I\| 4e-12 in every run |

### Speed features in the solver core: `hertzon` branch (R4, R6)

These are new technical capability in EMerge's core, so they go to `hertzon` as new code next to the
existing code, switched on by a setting, and marked as AI written (R3).

| Item | Hertzon code today | Gain (EMERGE_INTEGRATION.md) | Status |
|---|---|---|---|
| PARDISO complex symmetric LDL^T (mtype 6) | `fast_assembly._pardiso_solve` | 7–34 % per model | Waits for the maintainer's solver plans (SPARTA) |
| Parallel removal of PEC rows/columns | – | 5–10 % | On `hertzon` (FennisRobert/EMerge#78); Hertzon's copy removed |
| PARDISO per worker thread in parallel sweeps (EMerge uses SuperLU there) | `mt_pardiso` | parallel sweeps 5× faster, no out-of-memory | Waits for the maintainer's solver plans (SPARTA) |
| Far field for many excitations (phases once) as a new Rust kernel | – | 3.7–4.6× on 11 ports | On `hertzon` (FennisRobert/EMerge#76); Hertzon calls `emr.sc_multi` |
| Error estimate for many excitations in one pass | – | up to about 9 % with many ports, low priority | Open |
| Curved (isoparametric) elements | `curvilinear.py` keeps only the map for the plots | accuracy on curved surfaces | On `hertzon` (FennisRobert/EMerge#77, `Settings.mw_curved_elements`); Hertzon's kernels removed |

The maintainer may later take any of them into `main` in his own way.

### EM formulation in the core: `hertzon` branch (R4, R6)

| Item | Hertzon code today | Why | Status |
|---|---|---|---|
| Wave port with several modes of different propagation constant (coupled microstrip lines: even and odd mode): the port condition uses the propagation constant of the first mode for all modes, so the others are not absorbed exactly | – | Needed for terminal wave ports on differential lines | `MultiModeWavePort` on `hertzon` (agreed with the Hertzon maintainer, 26 October 2026): the dense wave port condition of `WavePortIH` with one projection term per mode, each with its own propagation constant, and S-parameters from the same projection. Coupled microstrip: odd mode reflection −38 dB instead of −23 dB, S unitary to 1e-11. Hertzon uses it for terminal wave ports with several signal conductors |

### AI-written EMerge code: free to improve (R5)

| Item | Decision |
|---|---|
| Hertzon's reduced-basis interpolating sweep (`hybrid_sweep`, 30–48 % of the run time) | First compare with EMerge's adaptive sweep (`adaptive_sweep.py`, AI written). If Hertzon's approach is better, improve `adaptive_sweep.py` (PR to `main` allowed) and switch Hertzon to it; otherwise Hertzon drops its own sweep. |
| Hertzon's refinement methods (local Delaunay, bisection, Mmg, remesh) | Stay in Hertzon for now. Compare with AURA (AI written); improvements to AURA that are clearly better and more stable may go to `main`. |

### GUI only: `hertzon` branch (R2) or Hertzon itself (H2)

| Item | Decision |
|---|---|
| Per-frequency "solved" callback for progress bars (wraps `SimJob.submit_solution`) | Small hook on `hertzon` |
| Solve hook for the reduced-basis sweep (wraps `SolveRoutine.solve`) | Disappears with the sweep decision above |
| Private API used by Hertzon (`Mesher._set_size_on_edge`, `Simulation._reset_mesh`, `MATHLIB`, `Assembler.assemble_*`) | Ask for stable names; until then Hertzon keeps using them |
| Plane wave angle convention (`definition="EA"`), initial mesh size (`set_global_max_size`) | Public API, stays in Hertzon |
| Hard process exit on Windows after the results are saved | Process handling, stays in Hertzon |
| Lazy fields, stored boundary fields for far fields after the solve, far field quantities (realized gain, efficiency, port superposition), face matching | Built on public results, stays in Hertzon |
| `MWField.interpolate(mapping=...)` instead of replacing `interpolate_index` | Done: Hertzon uses EMerge's API |

### Not EMerge: emsutil

`EHFieldFF` in [emsutil](https://github.com/FennisRobert/emsutil) (axial ratio sign, `gain`/`dir` with a
`kind` that cannot be passed, RCS without θ/φ split) are small bugs there, so pull requests to emsutil.

### Removed from Hertzon

Fixed in EMerge 3.0.0b5 or dead on EMerge 3, nothing to send: thin conductor split after a re-mesh
(`_thin_conductor_new_mesh`), the ARPACK retry (`_robust_eigs`), the welcome prompt workaround in `setup.ps1`, and the replacements for
EMerge 2.8.9 (`fast_error.py`, vectorised `_pre_update`, 2.8.9 assembly, `AMRPoints` patch).

## How Hertzon depends on EMerge

Hertzon installs EMerge from a pinned commit, normally the head of EMerge's `hertzon` branch, instead of a
PyPI release:

1. `packaging/emerge-commit` holds the commit.
2. When it changes, `.github/workflows/emerge-wheels.yml` builds the wheels (Windows x64, Linux x86_64,
   abi3, version `<EMerge version>.post<commit time>`) and publishes them as the pre-release
   `emerge-<short sha>` of the Hertzon repository, with a link to the EMerge source commit (GPL).
3. The `emerge` entries of `pyproject.toml` point at these wheels; on other platforms pip builds the same
   commit from source (needs Rust).

Updating EMerge is therefore one Hertzon pull request: new commit, wheel URLs, benchmark
(`examples/benchmark/tools/emerge_bench.py`) against the previous commit, results in
[EMERGE_INTEGRATION.md](EMERGE_INTEGRATION.md).
