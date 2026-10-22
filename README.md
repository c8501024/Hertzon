# Hertzon

[![DOI](https://zenodo.org/badge/1427567849.svg)](https://zenodo.org/badge/latestdoi/1427567849)
[![Release](https://img.shields.io/github/v/release/c8501024/Hertzon)](https://github.com/c8501024/Hertzon/releases/latest)
[![Release build](https://github.com/c8501024/Hertzon/actions/workflows/release.yml/badge.svg)](https://github.com/c8501024/Hertzon/actions/workflows/release.yml)
[![Downloads](https://img.shields.io/github/downloads/c8501024/Hertzon/total)](https://github.com/c8501024/Hertzon/releases)
[![License: GPL v2+](https://img.shields.io/badge/license-GPL--2.0--or--later-blue)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)](https://github.com/c8501024/Hertzon/releases/latest)
[![Solver: EMerge](https://img.shields.io/badge/solver-EMerge-orange)](https://github.com/FennisRobert/EMerge)

![Hertzon](hertzon/gui/splash.jpg)

> ⚠️ **Hertzon is under active development. It can still contain bugs, which are being fixed step by step.
> Please verify important results independently and report problems as GitHub issues.**

An open-source 3D full-wave electromagnetic simulator with a **classic engineering workflow**:
draw the model, assign boundaries and excitations, add an analysis setup with
**adaptive meshing**, add frequency sweeps, analyze, then look at S-parameters, fields
and radiation patterns. The finite-element solver underneath is
[EMerge](https://github.com/FennisRobert/EMerge) (2nd-order Nédélec FEM, gmsh/OpenCASCADE geometry).

> 📘 **New to Hertzon? Start with the [step-by-step tutorial](docs/TUTORIAL.md):** a waveguide with wave
> ports, a coaxial cable, a half-wave dipole and the RCS of a sphere, each from the empty window to the
> result, plus how to let an AI assistant (Claude Code, Codex, VS Code) build and run simulations for you
> through Hertzon's **MCP server**.

> **Built on EMerge.** The finite-element core of Hertzon (Nédélec formulation, element matrices, ports,
> boundary conditions, solver interface) is [EMerge](https://github.com/FennisRobert/EMerge), the open-source
> FEM solver developed by **Robert Fennis**. Many thanks to Robert Fennis for making
> EMerge available under the GPL. Hertzon adds the graphical workflow and its own solver extensions on top:
> the hzmesh mesher, adaptive refinement (local Delaunay, bisection, Mmg), a fast error estimator, faster
> assembly kernels, curved elements, far-field and RCS post-processing, and fixes for large models.

Hertzon is a desktop application (Qt + VTK) built for responsiveness with large models:
geometry is tessellated incrementally per object, the solver runs in its own process, and
results are streamed back into the GUI while it keeps working.

> Hertzon is an independent open-source project. It is not affiliated with any vendor of commercial
> simulation software.

## Install

Download the installers from the [Releases page](https://github.com/c8501024/Hertzon/releases/latest).

**Windows installer:** run **`Hertzon-Setup-<version>.exe`**. It installs Hertzon for your user
(no administrator rights) into `%LOCALAPPDATA%\Programs\Hertzon`, adds it to the start menu, the
desktop and Settings > Apps (uninstall there), opens `.hzn` projects with Hertzon and sets up the
Python environment (about 1 GB download) before it finishes.

**Linux:** unpack `Hertzon-<version>-linux.tar.gz` and run `./install.sh`. It installs for your user
into `~/.local/share/hertzon`, adds Hertzon to the application menu, the command `hertzon` and the
`.hzn` file type. Run `install.sh` of a newer package to update, `~/.local/share/hertzon/uninstall.sh`
to remove it.

Both are built with `packaging/build_installer.sh` (needs NSIS for the Windows installer); GitHub Actions
builds them and publishes a release for every version (`.github/workflows/release.yml`).

**Windows, without installing:** unzip and double-click **`Hertzon.exe`**. On the first start it installs
everything it needs into a `.venv` folder next to it (Python 3.13 via winget if missing, then
EMerge, Qt and VTK, about 1 GB) and compiles the solver with a small test solve, then opens
Hertzon. Later starts open Hertzon directly, and the first simulation runs at full speed.
You can drag a `.hzn` file onto `Hertzon.exe` to open it. To reinstall, delete `.venv`.

**Manually (any OS),** Python 3.10 or newer:

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .\setup.ps1 then .\run.ps1
pip install -e .                                        # installs emerge, PySide6, pyvista, pyvistaqt ...
python -m hertzon examples/WaveguidePost.hzn             # or just: hertzon
```

The first solve compiles EMerge's numba kernels, which takes a few minutes once; later solves start
immediately. On Linux without a desktop, run under `xvfb-run`.

## The workflow

1. **Project > Insert Design**, then **Simulation > Solution Type**: Modal, Terminal or Eigenmode,
   with **Auto Open** (radiation or PML chosen automatically) or **Auto Open - Filter**
   (air box with fixed margin and a radiation boundary, for filters and other closed-ish circuits).
2. **Draw** boxes, cylinders, spheres, cones, tori, polyhedra, rectangles, circles, ellipses,
   polygons, polylines, region. Click on the grid (snaps to grid and vertices) or type coordinates
   in the status bar; every parameter accepts expressions with **variables** and units.
   **Modeler > Boolean** unite / subtract / intersect / split, arrange, duplicate, thicken, sweep, import STEP.
3. Select faces (**F**) or objects (**O**) and assign **Boundaries** (Perfect E/H, Finite
   Conductivity, Impedance, Lumped RLC, Radiation, PML, Symmetry) and **Excitations** (Wave Port,
   Lumped Port, Floquet Port).
4. **Analysis > Add Solution Setup**: solution frequency (single / multi / broadband), maximum
   passes, maximum Delta S, matrix convergence, minimum (converged) passes, percent refinement,
   maximum refinement, lambda refinement, solver choice. **Add Frequency Sweep**: discrete or
   interpolating, linear step / count / log / single points.
5. **Validation Check**, then **Analyze All** (F5). The progress window shows each adaptive pass;
   **Solution Data** shows the convergence table and plot, matrix data and mesh statistics.
6. **Results**: rectangular plots, Smith chart, data table, radiation pattern, 3D polar plot;
   **Fields**: magnitude, complex magnitude and vector plots of E, H and surface current on faces,
   objects or cut planes, with phase animation. Export Touchstone, CSV, images.

See [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) for an overview of what is implemented and
what is still missing.

See [docs/MESHER.md](docs/MESHER.md) for how the adaptive mesh refinement works: hzmesh, the local
Delaunay mesher, curved elements and benchmark results.

See [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for solve times on a 32-core workstation: a patch antenna
with 613k unknowns, swept over a 3:1 band with 101 points, in 203 s (1375 s on one core).

See [docs/ROADMAP.md](docs/ROADMAP.md) for what comes next: a solver interface for several
solvers, openEMS (FDTD), electrostatics and magnetostatics, circuit co-simulation and photonics.

## Examples

| File | What it shows |
|---|---|
| `examples/WaveguidePost.hzn` | WR-90 waveguide with an inductive PEC post, wave ports, interpolating sweep 8-12 GHz |
| `examples/Microstrip.hzn` | 50 Ω microstrip on FR4, lumped ports, thin-conductor trace, **Auto Open - Filter**, sweep 1-6 GHz |
| `examples/PatchAntenna.hzn` | 2.45 GHz inset-fed patch on FR4, **Auto Open** (radiation boundary), far field, gain pattern |
| `examples/HairpinFilter.hzn` | Third-order hairpin band-pass filter at 2.45 GHz on RO4350B, copper losses, sweep 2-2.9 GHz and an optimization |
| `examples/GPS_L1_RHCP.hzn` | 1.575 GHz GPS L1 ceramic patch (εr 20) with probe feed and stub on a feed board, circular polarization (RHCP) far field |
| `examples/RotmanLens_24GHz.hzn` | 24 GHz Rotman lens beamformer on RO4350B in three designs: the lens with beam, array and dummy ports, the single array patch, and lens plus patch array with beam far fields |
| `examples/PlaneWave.hzn` | Incident plane wave on a PEC sphere in a radiation box, and the same plane wave in an empty box |
| `examples/NATO-Sphere.hzn` | RCS of a PEC sphere (radius 250 mm) at 1 GHz, plane wave, PML, curved second-order elements |
| `examples/DroneRCS.hzn` | RCS of an imported drone STL model (`Delta_Composites_Demo_3_Layers/`) at 1 GHz, plane wave |
| `examples/Coax.hzn` | 20 mm, 50 Ω PTFE coax with TEM wave ports, sweep 1-10 GHz ([tutorial](docs/TUTORIAL.md#2-coaxial-cable-with-wave-ports)) |
| `examples/Dipole.hzn` | Half-wave dipole for 1 GHz with a lumped port, **Auto Open**, far field and gain ([tutorial](docs/TUTORIAL.md#3-half-wave-dipole)) |
| `examples/SphereRCS.hzn` | RCS of a 100 mm PEC sphere at 1 GHz, plane wave, PML, checked against the Mie series ([tutorial](docs/TUTORIAL.md#4-radar-cross-section-of-a-sphere)) |

`examples/benchmark/` holds the validation and timing cases (patch antenna, waveguide post against a reference,
sphere RCS against the Mie series, scaling from small to large models) with their scripts.
The first four examples are regenerated with `python examples/make_examples.py`.

## Scripting

* **File > Export EMerge Python Script** writes a stand-alone script that rebuilds the same EMerge
  model (geometry, materials, boundaries, ports, initial mesh) and runs the setup, or stops after
  meshing with `--prepare-only` so you can continue in plain EMerge.
* Solve without the GUI: `python -m hertzon.solver.run Project.hzn DesignName Setup1 out_folder`.
* Project files (`.hzn`) are plain JSON; results go to `<project>.hznresults/<design>/<setup>/`
  (`sparams_*.npz`, Touchstone `*.sNp`, `fields_*.npz`, `farfield_*.npz`, `convergence.json`).

## AI assistants (MCP)

The [tutorial](docs/TUTORIAL.md#5-let-an-ai-assistant-build-and-run-it-mcp) shows the setup and example prompts.

Hertzon ships an MCP server, so Claude Code, Codex or VS Code (Copilot agent mode) can build and run
simulations: "build a 2.45 GHz patch on FR4 with a lumped port, solve it and give me S11 and the gain".

1. **Tools > AI Assistants (MCP)...**: press *Register* next to your assistant (Claude Code gets an
   entry in `~/.claude.json`, the Claude desktop app in `claude_desktop_config.json`, Codex in
   `~/.codex/config.toml`, VS Code in its user `mcp.json`).
   Without the GUI: `python -m hertzon.mcp --install claude` (or `desktop`, `codex`, `vscode`, `all`);
   `python -m hertzon.mcp --config` prints the settings for other clients.
2. Tick **Let AI assistants work in this window**. This also registers Hertzon with every installed
   Claude Code, Claude desktop app and Codex that does not list it yet. Changes then appear in the open window at once
   (Ctrl+Z undoes them) and analyses run in its solve queue. Without the live link the assistant works
   on the `.hzn` files and runs the solver in the background.
3. Start a new session of the assistant and ask for a simulation.

The server offers tools to create projects, set variables and design settings, draw and import
geometry, list faces (index, centre, normal), assign boundaries, ports and mesh operations, define
setups, sweeps and far-field spheres, validate, run, follow the adaptive passes, read S-parameters,
gain or RCS, and render a picture of the model.

## Layout

The Python package keeps its short name `hertzon`, and projects keep the `.hzn` extension.

```
hertzon/model    data model, expressions with units, materials, property schema, validation
hertzon/kernel   OpenCASCADE (gmsh) geometry builder and display tessellation
hertzon/solver   EMerge bridge: auto open, boundary mapping, adaptive passes, sweeps, far field
hertzon/gui      main window, 3D modeler, project tree, property grids, reports, field overlays
hertzon/mcp      MCP server for AI assistants, live link to the window, client registration
```

## Tests

```bash
pip install pytest && python -m pytest tests
```

## Citing Hertzon

If you use Hertzon in a publication, please cite it. GitHub shows a ready-made reference under
**Cite this repository** on the repository page (from `CITATION.cff`), as APA or BibTeX. Every release
is archived on Zenodo with its own DOI; the badge at the top links to the latest one. Please also cite
[EMerge](https://github.com/FennisRobert/EMerge) by Robert Fennis, the solver Hertzon is
built on: DOI [10.5281/zenodo.17025518](https://doi.org/10.5281/zenodo.17025518).

## License

GPL-2.0-or-later, the same as EMerge, which Hertzon links against.
All Python packages Hertzon uses and their licences are listed in [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md).

## No warranty

Hertzon is provided "as is", without any warranty and without liability for damages, as set out in
sections 11 and 12 of the GPL. Simulation results can be wrong. Check them yourself (measurements,
analytical estimates or a second tool) before you rely on them for a design, a product or a decision.
