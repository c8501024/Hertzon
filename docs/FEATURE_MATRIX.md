# Hertzon feature overview

What Hertzon covers of a typical 3D frequency-domain FEM workflow (no asymptotic, integral-equation or
transient solvers), and how each step is done with the EMerge FEM engine underneath.

Legend: **✔** implemented and tested · **◐** implemented with limitations · **✖** not yet

## Project and design

| Function | In Hertzon | Status | Notes |
|---|---|---|---|
| Project with several designs | Project Manager: Project > Design | ✔ | Insert / copy / rename / delete design |
| Project and design variables (`$var`, `var`) | Project Variables, Design Properties | ✔ | Expressions with units (`2*w+1mm`, `c0/f`), functions `sin cos sqrt …` |
| Solution Type Modal / Terminal / Eigenmode | Simulation > Solution Type | ◐ | Terminal: terminals of lumped ports and of wave ports (one per signal conductor, reference conductor automatic or named), terminal S-parameters at 50 ohm or the renormalization impedance, differential pairs, Zo and Gamma, port field display, solve ports only, wave ports with several signal conductors (each mode absorbed with its own propagation constant) |
| Auto Open region | Solution Type dialog: Auto Open | ✔ | Radiation or PML picked automatically (PML when the design radiates: far-field sphere, plane wave or "Radiating structure") |
| — | **Auto Open - Filter** | ✔ | Air box with a fixed margin and radiation boundary on all outer faces (filter workflow) |
| Undo / Redo, copy / paste | Edit menu, Ctrl+Z / Ctrl+Y / Ctrl+C / Ctrl+V | ✔ | |
| Validation Check | Simulation > Validation Check | ✔ | Design, 3D model, boundaries/excitations, mesh, setups |
| Overlapping objects (material override) | Design setting Material override; Validation Check > Resolve Overlap / Resolve All Overlaps | ✔ | Contained objects allowed (inner wins); partial overlaps of different materials are errors that block the analysis, except metal over dielectric with Material override (default on); Resolve subtracts the winner (clone) |
| Message Manager, Progress window with Abort | Docks at the bottom | ✔ | |
| Save / Save As / Recent | File menu (`.hzn` JSON file, results in `.hznresults/`) | ✔ | |

## 3D modeler

| Function | In Hertzon | Status | Notes |
|---|---|---|---|
| Box, Cylinder, Sphere, Cone, Torus, Regular Polyhedron | Draw menu, interactive (click-click-height) or by dialog | ✔ | Snaps to grid and vertices, X/Y/Z entry in the status bar |
| Rectangle, Circle, Ellipse, Regular Polygon | Draw menu | ✔ | Working plane XY / YZ / ZX |
| Polyline (lines, closed and covered) | Draw > Polyline | ✔ | Straight, 3 point arc, center point arc (AngularArc), spline segments |
| Arc (3 point, center point) | Draw > Arc | ✔ | A polyline of one arc segment |
| Region (padding % or absolute) | Draw > Region | ✔ | |
| Unite, Subtract (blank/tool, clone), Intersect, Split | Modeler > Boolean | ✔ | |
| Move, Rotate, Mirror, Scale, Duplicate along line / around axis / mirror | Edit > Arrange / Duplicate | ✔ | |
| Object groups (also of solids, sheets and lines together), ungroup, rename | Edit > Group (Ctrl+G) / Ungroup (Ctrl+Shift+G), model tree > Groups | ✔ | A group is selected and transformed as one unit; SMD components are groups; saved in the project |
| Thicken sheet, Sweep along vector / around axis / along path, Cover lines | Draw / Modeler | ✔ | |
| Fillet, Chamfer | Modeler > Fillet / Chamfer | ◐ | Edges of solids; on flat sheets fillet and chamfer work on the selected vertices |
| Import STEP / IGES / BREP, Export STEP | File / Modeler | ✔ | |
| History tree with editable commands, suppress | Model tree + Properties | ✔ | Every command parameter accepts expressions |
| Object attributes: material, solve inside, model, color, transparency, wireframe | Properties > Attribute | ✔ | |
| Selection modes Object (O), Face (F), Edge (E), Vertex (V), select by name, Ctrl+A | ✔ | ✔ | |
| Box selection (left drag, Ctrl + drag adds) | 3D view | ✔ | Selects what lies completely inside; a group only when all members are inside |
| Units, grid, measure, fit all / fit selection, views, ortho/perspective | View / Modeler | ✔ | Grid and axis triad stay at the origin of the working CS |
| Material library (Edit Materials, add/clone) | Tools > Edit Materials | ✔ | εr, μr, σ, tan δ, magnetic tan δ; frequency-dependent materials ✖ |
| User coordinate systems (Relative CS, Face CS, Object CS) | Modeler > Coordinate System | ✔ | Working CS for drawing, grid and Split |

## Boundaries

| Function | In Hertzon | EMerge | Status |
|---|---|---|---|
| Perfect E | Perfect E | `PEC` | ✔ |
| Perfect H | Perfect H | `PMC` | ✔ |
| Finite Conductivity (material / σ, roughness, thickness) | Finite Conductivity | `SurfaceImpedance` / `ThinConductor` | ✔ |
| Impedance (R + jX) | Impedance | `SurfaceImpedance` | ✔ |
| Lumped RLC (parallel or series RLC, Touchstone component) | Lumped RLC | `LumpedElement` with Z(f) | ✔ |
| SMD components between two traces (0201 to 1206, vendor series, pads, optional 3D terminations, Touchstone parts) | Lumped Elements tab | `LumpedElement` | ✔ |
| Radiation (first order) | Radiation | `AbsorbingBoundary` | ✔ (second order ◐) |
| PML | PML (on region faces) and Auto Open | `pmlbox` | ✔ |
| Symmetry (Perfect E / Perfect H) | Symmetry | `PEC` / `PMC` | ✔ far field with mirror images at planes parallel to XY, YZ or ZX (impedance multiplier ✖) |
| Periodic (Primary / Secondary) | Primary / Secondary | — | ✖ (can be assigned, not passed to the solver: it warns and skips them) |
| Layered Impedance | — | — | ✖ |

## Excitations

| Function | In Hertzon | Status | Notes |
|---|---|---|---|
| Wave Port (modes, Zpi/Zpv/Zvi, renormalize, de-embed) | Wave Port | ✔ | De-embedding uses the solved port propagation constant |
| Lumped Port (impedance, integration line) | Lumped Port | ✔ | Integration line placed automatically between the touching conductors, or drawn in the 3D view; it moves, rotates, mirrors and scales with its sheet |
| Floquet Port | Floquet Port | ◐ | Needs periodic boundaries, which are not available yet |
| Incident Plane Wave | Incident Plane Wave | ✔ | Scattered-field formulation with a radiation boundary, RCS (monostatic and bistatic); one plane wave per design, ignored while ports are present |

## Mesh operations

| Function | In Hertzon | Status |
|---|---|---|
| Length Based (on selection / inside selection) | ✔ |
| Length Based on edges | ✔ |
| Skin Depth Based | ◐ (mapped to a surface length of skin depth × layers) |
| Initial mesh: Lambda refinement, lambda target, free-space lambda | ✔ (Setup > Options; default target follows the basis order: 0.1 / 0.3333 / 0.6667) |
| Plot Mesh | ✔ |

## Analysis setup (driven)

| Setting | In Hertzon | Status |
|---|---|---|
| Solution frequency: Single / Multi-Frequencies / Broadband | ✔ |
| Maximum Number of Passes | ✔ |
| Maximum Delta S | ✔ (port-mode sign aligned) |
| Use Matrix Convergence (ΔMag, ΔPhase) | ✔ |
| Minimum Number of Passes, Minimum Converged Passes | ✔ |
| Percent Refinement per pass | ✔ (enforced, adaptive error-driven refinement) |
| Mesh refinement method | ✔ hzAURA (EMerge, default), Local (Delaunay, hzmesh), Remesh (slow), Remesh (stable, experimental); see [MESHER.md](MESHER.md) |
| Curved elements on curved surfaces | ✔ (EMerge, default on) |
| Maximum Refinement (tets) | ✔ |
| Basis function order | ✔ Zero Order (6), First Order (20, default), Second Order (complete quadratic, 30); no Mixed Order |
| Solver: Auto / Direct (PARDISO, MUMPS, UMFPACK, SuperLU) | ✔ |
| Save fields | ✔ |
| Eigenmode setup (min frequency, number of modes) | ✔ |

## Frequency sweeps

| Function | In Hertzon | Status |
|---|---|---|
| Discrete | ✔ |
| Interpolating (error tolerance, max solutions) | ✔ (rational fit with adaptive validation points; validation points from a reduced basis of the direct solutions, factorized only when its residual is too large) |
| Fast (ALPS) | ✔ (one centre factorization, LDLᵀ, reduced basis with residual control per point) |
| Linear Step / Linear Count / Log Scale / Single Points | ✔ |
| Save fields (all frequencies) | ✔ Discrete and Fast: field plots, far fields, gain patterns and antenna parameters at every sweep frequency; Interpolating stores S-parameters only |

## Results and post-processing

| Function | In Hertzon | Status |
|---|---|---|
| Solution Data: Profile, Convergence (table + plot), Matrix Data S/Y/Z (mag/phase, dB, re/im), Mesh Statistics | ✔ |
| Rectangular plot, Smith chart, Data table, markers | ✔ |
| Expressions `dB(S(1,1))`, `VSWR`, `GroupDelay`, `Z`, `Y`, `Zo`, `Gamma`, mixed-mode `S(Diff1,Comm1)`, `ang_deg`, `re`, `im` | ✔ (also as named equations, e.g. `RL = -dB(S(1,1))`, category Equations) |
| Units on report axes, data tables, CSV export and colour bars | ✔ |
| Radiation: Infinite Sphere, 2D pattern cuts, 3D polar plot, Gain / Directivity / Realized gain, Eθ/Eφ, markers, beamwidth and side lobes | ✔ (half and quarter models with Symmetry boundaries are mirrored) |
| Field overlays on faces, objects and cut planes: Mag, Complex Mag and Vector of E, H, Jsurf and J (volume current sigma E), phase animation | ✔ (with nothing selected, the faces or objects are picked in the 3D view) |
| Volume field plots (scalar field through a solid as coloured dots) | ✔ Plot on "(selection, inside solids)" |
| Export Touchstone, CSV, images | ✔ |
| Export Gerber X2 (copper layers, profile) and Excellon (PTH / NPTH), File > Export > Gerber ([details](GERBER_EXPORT.md)) | ✔ |
| Optimization: conjugate gradient (projected Polak-Ribiere+), goals on report expressions, error-area or worst-case cost, filter mask, parallel gradient evaluations | ✔ |
| Parametric sweep | ✔ Optimization > Add Parametric; solved variations can be shown again without solving |
| Filter design toolkit: Tools > Filter Design > Tapped Hairpin (N = 2 to 9 resonators, design frequency, hfsynpy microstrip synthesis, parametrized geometry, setup, sweep and conjugate gradient optimization on the filter mask) | ✔ |
| Antenna design toolkit: Tools > Antenna Design > Rectangular Patch (frequency and substrate in; transmission line model, inset feed, setup, sweep, far-field sphere and optimization of length and inset on the return loss) | ✔ |
| Fields calculator: named equations of E, H, J, Js, S and the network data (S, Y, Z, VSWR), typeset formula editor, units | ✔ Results > Calculator (Equations tab); integrals over lines, faces, volumes and cut planes, flux, circulation, max/min/avg, point values, curl, div, grad, cylindrical and spherical components, on the solver's elements; library of loss, energy, Q, voltage, current and power; import/export ([tutorial](TUTORIAL.md#8-your-own-quantities-with-equations)) |
| Fields report (values computed from the fields over frequency) | ✔ Results > Create Fields Report (Rectangular Plot, Data Table); frequencies with saved fields |
| Solve with HPC / distributed | ◐ (threads per solve, parallel sweep points) |

## Extras

* **File > Export > EMerge Python Script**: writes a stand-alone script that rebuilds the same EMerge model
  and runs the setup (or stops after meshing for custom scripting).
* Plain-text project files (JSON), easy to diff and generate.
