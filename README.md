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

🌐 **Website:** [c8501024.github.io/Hertzon](https://c8501024.github.io/Hertzon/)

An open-source 3D full-wave electromagnetic simulator (FEM, frequency domain) for **antennas, microwave
filters, waveguides, PCB structures and radar cross section**, with a **classic engineering workflow**:
draw the model, assign boundaries and excitations, add an analysis setup with
**adaptive meshing**, add frequency sweeps, analyze, then look at S-parameters, fields
and radiation patterns. The finite-element solver underneath is
[EMerge](https://github.com/FennisRobert/EMerge) (2nd-order Nédélec FEM, gmsh/OpenCASCADE geometry).

Hertzon is by **Dominik Mair**, built on EMerge, the FEM solver by **Robert Fennis**.

> 📘 **New to Hertzon? Start with the [step-by-step tutorial](docs/TUTORIAL.md):** a waveguide with wave
> ports, a coaxial cable, a half-wave dipole and the RCS of a sphere, each from the empty window to the
> result, your own result quantities with equations, plus how to let an AI assistant (Claude Code, Codex, VS Code) build and run simulations for you
> through Hertzon's **MCP server**.

> **Built on EMerge.** The finite-element core of Hertzon (Nédélec formulation, element matrices, ports,
> boundary conditions, solver interface) is [EMerge](https://github.com/FennisRobert/EMerge), the open-source
> FEM solver developed by **Robert Fennis**. Many thanks to Robert Fennis for making
> EMerge available under the GPL. The adaptive mesh refinement (AURA), the error estimate and the curved
> elements on curved surfaces are EMerge's as well. Hertzon adds the graphical workflow on top: the
> adaptive pass loop with its convergence criteria, a second local refinement method (hzmesh, local
> Delaunay), fast and interpolating sweeps with parallel sweep points, far-field and RCS post-processing
> (also of half and quarter models with symmetry planes), optimization and parametric sweeps, and fixes
> for large models.

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

**Updates:** when a new version is out, Hertzon shows its release notes in a small dialog after the start
(at most once every 7 days) with **Install**, **Skip this version** and **Later**; **Help > Check for
Updates...** checks at any time. Install downloads the installer, checks it and keeps your settings and the
Python environment. The check can be switched off in Tools > Options > General, or with
`HERTZON_NO_UPDATE_CHECK=1`.

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

EMerge comes from a pinned commit (see docs/EMERGE_CHANGES.md): prebuilt wheels for Windows x64 and Linux
x86_64; on other platforms pip builds it from source, which needs a Rust compiler.

EMerge 3 ships its solver kernels compiled; the first solve only compiles Hertzon's own numba kernels
(matrix handling, the size field of the re-mesh refinement), which takes about a minute once; later
solves start immediately. On Linux without a desktop, run under `xvfb-run`.

## The workflow

1. **Project > Insert Design**, then **Simulation > Solution Type**: Modal, Terminal or Eigenmode,
   with **Auto Open** (radiation or PML chosen automatically) or **Auto Open - Filter**
   (air box with fixed margin and a radiation boundary, for filters and other closed-ish circuits).
2. **Draw** boxes, cylinders, spheres, cones, tori, polyhedra, rectangles, circles, ellipses,
   polygons, polylines and arcs (3 point, center point), region. Click on the grid (snaps to grid and vertices) or type coordinates
   in the status bar; every parameter accepts expressions with **variables** and units. Relative, face and
   object coordinate systems set the working plane; grid and axes stay at its origin.
   **Modeler > Boolean** unite / subtract / intersect / split, arrange, duplicate, thicken, sweep, import STEP;
   **Fillet** and **Chamfer** on the edges of solids or the corner vertices of sheets.
   A left drag in the 3D view selects everything inside the rectangle; **Edit > Group** (Ctrl+G) makes
   objects move, rotate and mirror as one unit, and integration lines follow their objects.
3. Select faces (**F**) or objects (**O**) and assign **Boundaries** (Perfect E/H, Finite
   Conductivity, Impedance, Lumped RLC, Radiation, PML, Symmetry) and **Excitations** (Wave Port,
   Lumped Port, Incident Plane Wave). With Symmetry boundaries a half or quarter model is enough for the
   far field, which includes the mirror images; a port cut by the plane is still referred to the
   impedance of the half port ([BACKLOG.md](BACKLOG.md)). Primary / Secondary (periodic) boundaries make
   the model a unit cell of an infinite array, with the scan angle on the Secondary boundaries; Floquet
   ports above and below the cell excite and absorb the plane waves TE00 and TM00.
   The **Lumped Elements** tab inserts chip resistors, inductors and capacitors (0201 to 1206 and RF
   series of Coilcraft and Murata) with a click on the board: pads, element sheet and Lumped RLC boundary
   with an ideal value, typical ESL/ESR or a Touchstone file of the vendor ([docs/LUMPED_ELEMENTS.md](docs/LUMPED_ELEMENTS.md)).
4. **Analysis > Add Solution Setup**: solution frequency (single / multi / broadband), maximum
   passes, maximum Delta S, matrix convergence, minimum (converged) passes, percent refinement,
   maximum refinement, lambda refinement, solver choice, mesh refinement method (hzAURA, EMerge's AURA
   with exact curved surfaces, by default; local Delaunay or re-mesh). **Add Frequency Sweep**: discrete, interpolating or fast,
   linear step / count / log / single points.
5. **Validation Check** (also checks overlapping solids exactly: partial overlaps of different
   materials are errors, except a metal in a dielectric with the design setting *Material override*; one
   click resolves them), then **Analyze All** (F5). The progress window shows each adaptive pass;
   **Solution Data** shows the convergence table and plot, matrix data and mesh statistics, also of every
   solved parametric variation and optimization evaluation (*Design Variation*).
6. **Results**: rectangular plots, Smith chart, data table, radiation pattern, 3D polar plot;
   **Fields**: magnitude, complex magnitude and vector plots of E, H and surface current on faces,
   objects, cut planes or drawn planes, with phase animation and **Export GIF** of the animation as
   shown. Export Touchstone, CSV, images.
   **Calculator** (Fields Calculator): your own quantities as equations, from the S-parameters
   (`RL = -dB(S(1,1))`) or the fields (`P_in = flux(Port1, re(S))`, losses, energies, Q), typed in a
   typeset formula editor, shown in reports, **Fields reports** and field plots
   ([tutorial](docs/TUTORIAL.md#8-your-own-quantities-with-equations)).
   **Optimization** (conjugate gradient on report goals, or the **Derivative Trust Region**, which minimizes
   the goals on the derivative tuning model and checks each step with a solve) and **Parametric** sweeps of
   design variables. **Apply to Design** on an evaluation shows its solution without solving again.
   **Derivative tuning**: sliders for design variables, and S-parameters, patterns and antenna parameters
   follow live in the reports from one solve ([how it works](docs/TUNING.md),
   [tutorial](docs/TUTORIAL.md#10-tune-a-patch-antenna-with-sliders)).

See [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) for an overview of what is implemented and
what is still missing.

See [docs/TUNING.md](docs/TUNING.md) for derivative tuning: the workflow, the method (morphed mesh, matrix
derivatives, reduced model), its accuracy and limits.

See [docs/MESHER.md](docs/MESHER.md) for how the adaptive mesh refinement works: hzAURA (EMerge's AURA, the
default), Hertzon's local Delaunay mesher hzmesh, curved elements and benchmark results.

See [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for solve times on a 32-core workstation: a patch antenna
with 613k unknowns, swept over a 3:1 band with 101 points, in 203 s (1375 s on one core).

See [docs/ROADMAP.md](docs/ROADMAP.md) for what comes next: a solver interface for several
solvers, openEMS (FDTD), electrostatics and magnetostatics, circuit co-simulation and photonics.

## Examples

In Hertzon, **File > Open Example** opens any of them as an untitled copy, so the shipped files stay unchanged.
All of them use the current defaults: **hzAURA** mesh refinement and curved elements. Most open with their
reports and field plots ready: after **Analyze All** (F5) double-click a report, or right-click a field plot
> **Animate** and **Export GIF...**.

| File | What it shows |
|---|---|
| `examples/WaveguidePost.hzn` | WR-90 waveguide with an inductive PEC post, wave ports, interpolating sweep 8-12 GHz, Mag E on a cut plane halfway up the guide ([tutorial](docs/TUTORIAL.md#1-waveguide-with-wave-ports)); equations for the return loss, the power through both ports and the power flow along the guide ([tutorial](docs/TUTORIAL.md#8-your-own-quantities-with-equations)) |
| `examples/Microstrip.hzn` | 50 Ω microstrip on FR4, lumped ports, thin-conductor trace, **Auto Open - Filter**, sweep 1-6 GHz; second design with a **2.2 pF 0603 DC-block capacitor** (Lumped RLC from the Lumped Elements tab, inserted as a group) ([tutorial](docs/TUTORIAL.md#7-a-chip-capacitor-on-a-microstrip-line)) |
| `examples/PatchAntenna.hzn` | 2.45 GHz inset-fed patch on FR4, **Auto Open** (radiation boundary), far field, gain pattern and 3D gain, surface current on the patch and Mag E on a cut plane through the feed |
| `examples/HairpinFilter.hzn` | Third-order hairpin band-pass filter at 2.45 GHz on RO4350B, copper losses, sweep 2-2.9 GHz, an optimization and the surface current on the hairpins |
| `examples/GPS_L1_RHCP.hzn` | 1.575 GHz GPS L1 ceramic patch (εr 20) with probe feed and stub on a feed board, circular polarization (RHCP) far field |
| `examples/RotmanLens_24GHz.hzn` | 24 GHz Rotman lens beamformer on RO4350B in three designs: the lens with beam, array and dummy ports, the single array patch, and lens plus patch array with beam far fields |
| `examples/PlaneWave.hzn` | Incident plane wave on a PEC sphere in a radiation box drawn by hand, and the same plane wave in an empty box |
| `examples/NATO-Sphere.hzn` | RCS of a PEC sphere (radius 250 mm) at 1 GHz, plane wave, **Auto Open** with one wavelength of air, curved second-order elements, Mag E on a drawn plane |
| `examples/DroneRCS.hzn` | RCS of an imported drone STL model (`Delta_Composites_Demo_3_Layers/`) at 1 GHz, plane wave, 3D RCS pattern |
| `examples/Coax.hzn` | 20 mm, 50 Ω PTFE coax with TEM wave ports, sweep 1-10 GHz, Vector E across and Mag E along the line on cut planes ([tutorial](docs/TUTORIAL.md#2-coaxial-cable-with-wave-ports)) |
| `examples/Dipole.hzn` | Half-wave dipole for 1 GHz with a lumped port, **Auto Open**, gain, impedance and antenna parameters; second design: the **half model with a Symmetry boundary** (Perfect H), whose far field includes the mirror image ([tutorial](docs/TUTORIAL.md#3-half-wave-dipole)) |
| `examples/DifferentialPair.hzn` | 100 Ω edge-coupled microstrip pair on RO4350B with the **terminal solution**: one wave port per end with a terminal per trace (even and odd mode each absorbed with its own propagation constant), **differential pairs** and the mixed-mode S-parameters `S(Diff1,Diff1)`, `S(Comm1,Diff1)`, sweep 1-12 GHz ([tutorial](docs/TUTORIAL.md#9-a-differential-pair-with-the-terminal-solution)) |
| `examples/FSS_UnitCell.hzn` | Frequency selective surface: a cross dipole on RO3003 as the **periodic unit cell** of an infinite array (Primary / Secondary boundaries, scan angle `scan_theta`), **Floquet ports** below and above with TE00 and TM00, transmission and reflection 6-18 GHz |
| `examples/GerberTestBoard.hzn` | Two-layer board with copper shapes, cut-outs, a via (grouped with its pad) and holes: the test case of **File > Export > Gerber** ([docs](docs/GERBER_EXPORT.md)) |
| `examples/SphereRCS.hzn` | RCS of a 100 mm PEC sphere at 1 GHz, plane wave, **Auto Open**, bistatic and monostatic RCS reports, checked against the Mie series ([tutorial](docs/TUTORIAL.md#4-radar-cross-section-of-a-sphere)) |
| `examples/Showcase_AllFeatures.hzn` | Test project that uses nearly every feature in six designs: patch array (all report types, field overlays, Fields Calculator, optimization, parametric sweeps, three setups, all sweep types, all mesh operations, coordinate systems), half-model iris filter (Symmetry, wave ports with modes, de-embedding and renormalization), eigenmode cavity, a geometry zoo with every create command and operation under a plane wave (RCS), a terminal coax monopole with PML and a periodic unit cell (grounded cross with a Floquet port, reflection phase). Built by `python examples/make_showcase.py` |

`examples/benchmark/` holds the validation and timing cases (patch antenna, waveguide post against a reference,
sphere RCS against the Mie series, scaling from small to large models) with their scripts.
`python examples/make_examples.py` builds the first versions of five of them (waveguide, microstrip, patch,
hairpin, differential pair) from code; the shipped files were finished in Hertzon.

## Manufacturing data (Gerber)

**File > Export > Gerber...** writes Gerber X2 files per copper layer, the board outline and Excellon drill
files (plated / non-plated) from the real CAD geometry: choose the board plane (any coordinate system),
origin and units, check the suggested layer assignment in the preview and export. Arcs stay exact,
overlapping copper is merged, cut-outs and islands keep their polarity, and simulation helpers (air
region, ports, radiation boundaries) are left out. See [docs/GERBER_EXPORT.md](docs/GERBER_EXPORT.md).

## Scripting

* **File > Export > EMerge Python Script** writes a stand-alone script that rebuilds the same EMerge
  model (geometry, materials, boundaries, ports, initial mesh) and runs the setup, or stops after
  meshing with `--prepare-only` so you can continue in plain EMerge.
* Solve without the GUI: `python -m hertzon.solver.run Project.hzn DesignName Setup1 out_folder`.
* Solve on another computer (experimental): a computer in your network runs `hertzon-serve` (or Tools >
  Options > Remote > Share this computer) and the laptop pairs with it in Tools > Options > Remote; or
  Hertzon creates a cloud server for the session in Tools > Options > Cloud (Hetzner Cloud; Amazon Web
  Services, Google Cloud and Microsoft Azure are marked *untested*) and deletes it again. **Solve on** in the
  status bar switches between this computer, the remote one and the cloud. See [docs/REMOTE.md](docs/REMOTE.md).
* Project files (`.hzn`) are plain JSON; results go to `<project>.hznresults/<design>/<setup>/`
  (`sparams_*.npz`, Touchstone `*.sNp`, `fields_*.npz`, `farfield_*.npz`, `convergence.json`); their layout,
  units and status values are in [docs/RESULT_FILES.md](docs/RESULT_FILES.md).

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

## Design principles

Hertzon's design principles are in [docs/PRINCIPLES.md](docs/PRINCIPLES.md). The first one: **every export
is WYSIWYG**. Images, animations, plots, reports and data are saved exactly as Hertzon shows them: same
size, speed, colours, scales and view.

## Layout

The Python package keeps its short name `hertzon`, and projects keep the `.hzn` extension.

```
hertzon/model    data model, expressions with units, materials, property schema, validation
hertzon/kernel   OpenCASCADE (gmsh) geometry builder and display tessellation
hertzon/solver   EMerge bridge: auto open, boundary mapping, adaptive passes, sweeps, far field
hertzon/gui      main window, 3D modeler, project tree, property grids, reports, field overlays
hertzon/mcp      MCP server for AI assistants, live link to the window, client registration
```

## Changes

What changed in each release: [CHANGELOG.md](CHANGELOG.md).

## Tests

```bash
pip install -e ".[test]" && python -m pytest tests
```

Run the install again whenever `packaging/emerge-commit` changes: the tests stop with a message when the
installed EMerge is not the pinned commit.

## Branches and releases

- Work goes to the `dev` branch (pull requests target `dev`). Every push to `dev` builds test installers
  for Windows and Linux, checks the Windows installation and keeps both as an artifact of the run
  (Actions tab > Dev build > the run > Artifacts) for 30 days. They are not published as a release.
- For a release, raise the version on `dev` (`pyproject.toml`, `hertzon/__init__.py`,
  `hertzon/mcp/__main__.py`, `CITATION.cff`) and name the section in `CHANGELOG.md`, then merge `dev`
  into `main` with a merge commit. A new version on `main` builds the installers and publishes the
  release `v<version>`; merges without a new version publish nothing.

## Contributors

Thanks to everyone who helps make Hertzon better, whether by reporting a bug, suggesting a feature or
writing code. The list follows the [all-contributors](https://allcontributors.org) convention; the
[emoji key](https://allcontributors.org/docs/en/emoji-key) explains the badges.

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/c8501024"><img src="https://avatars.githubusercontent.com/u/29594166?v=4?s=80" width="80px;" alt="c8501024"/><br /><sub><b>c8501024</b></sub></a><br /><a href="https://github.com/c8501024/hertzon/commits?author=c8501024" title="Code">💻</a> <a href="#maintenance-c8501024" title="Maintenance">🚧</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/FeruzTopalov"><img src="https://avatars.githubusercontent.com/u/32893186?v=4?s=80" width="80px;" alt="FeruzTopalov"/><br /><sub><b>FeruzTopalov</b></sub></a><br /><a href="https://github.com/c8501024/hertzon/issues?q=author%3AFeruzTopalov" title="Bug reports">🐛</a> <a href="#ideas-FeruzTopalov" title="Ideas, Planning, & Feedback">🤔</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

To add someone, comment on an issue or pull request, for example
`@all-contributors please add @username for bug` (or `ideas`, `code`, `doc`, ...).

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
