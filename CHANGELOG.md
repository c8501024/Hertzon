# Changelog

All notable changes of each release. The release workflow copies the section of the released version into
the GitHub release notes.

## Unreleased

### Added
- **Derivative tuning with sliders.** Results > Tuning (also Optimization > Derivative Tuning and the right
  click on a sweep) computes, once, how the S-parameters of a sweep depend on chosen design variables, on
  the converged adaptive mesh of the setup. Then sliders in a dock panel move the variables and every open
  report on that solution follows live (rectangular plots, Smith charts, data tables and radiation
  patterns, with all their quantities, markers and exports): the tuned curve solid, labelled with the
  slider values, over the solved one dashed. *Apply to Design* sets the variables to the slider values. The
  mesh is morphed instead of remeshed (the nodes follow the faces that move), the derivatives of the FEM
  matrix come from assemblies on slightly morphed meshes, and one factorization per frequency gives the
  solution and its derivatives, which span a small reduced model. Resonances therefore shift with the
  sliders instead of being smeared. At the solved values the response is exactly the solved one; on a
  microstrip stub the notch moved 0.3 GHz per millimetre of stub length, within 0.01 of exact solves on
  the morphed mesh over +-2 mm. Each slider is limited to the range in which no tetrahedron shrinks below
  30 % of its volume. Plane faces may change shape, curved faces may move. Not yet for terminal designs.
  - A setup without a sweep is tuned at its solution frequency.
  - Faces may slide along plane parts of the outer boundary, and lumped ports and lumped RLC elements
    stretch with the mesh (width, height, integration lines and excitation follow), so a substrate height
    can be tuned with a lumped port across it. Wave ports stay in place.
  - An analyzed interpolating sweep is tuned at the frequencies it solved, and the tuned curve is
    interpolated over the sweep frequencies the same way, instead of at a fixed number of points.
  - Computing the derivatives of a band is faster: one reduced basis serves all frequencies, the volume
    matrices are assembled once, and only a few frequencies are factorized (chosen where the residual is
    largest); every other frequency is solved in that basis. Patch antenna, two variables, 51
    frequencies: 85 s instead of 204 s, the same response within 3e-6.
  - The sliders move smoothly: a band model is evaluated directly at the sweep frequencies (the volume
    part exactly, only the boundary terms interpolated) instead of fitting the tuned curve at every
    move, and all frequencies are solved in one batch.
  - Radiation patterns follow the sliders (gain, realized gain, directivity ... at the report frequency):
    the model keeps the fields of its basis on the far field surface, so a slider move only sums their
    far fields; at the solved design it reproduces the solved far field exactly.
  - Antenna Parameters reports and tables follow the sliders too (peak realized gain, radiation
    efficiency ... over the sweep frequencies of a sweep with saved fields). The far fields of the basis are
    computed once per frequency, with progress; after that a slider move takes a fraction of a second.
  - Patterns and antenna parameters also follow the sliders for a setup without a sweep (or a sweep
    without a band model): every frequency keeps the far field basis of its own reduced model. Earlier
    such tuning models had no far field, so these reports did not change. A pattern or antenna report on
    another solution of the tuned setup (e.g. LastAdaptive while a sweep is tuned) follows too, and the
    3D polar plot shows the tuned pattern. When a report cannot show a tuned curve, its info line says why
    (no far field in the model, or a frequency the model does not have).
  - Plot against a design variable: Rectangular plots and data tables of a solution with a tuning model
    have an *X axis* choice, Frequency or one of the tuned variables over its tuning range at one frequency
    (*At*), with the other variables at their slider values. Dashed line: the solved value, solid line: the
    slider value, shaded: the outer part of the range with lower accuracy. Works without the slider panel
    open, from the stored tuning model.
    Antenna Parameters reports and tables have it too (e.g. peak realized gain over a substrate height).
- **Goal preview in Setup Optimization.** The Goals tab draws every goal as a mask over the current
  solution (each calculation as a curve, each goal as a line over its range in the colour of its
  calculation, the forbidden side shaded) and shows its margin in a new Margin column: positive = reserve
  (green), negative = violated (red). It follows the table while you edit.
- **Antenna parameters as optimization goals.** A goal can use PeakRealizedGain, PeakGain,
  RadiationEfficiency, FrontToBackRatio and the other antenna parameters (dB() is 10 log10 for gains),
  directly or through an output variable. They are evaluated over the frequencies with saved fields:
  LastAdaptive, or a sweep with Save fields on; the first far field setup gives the directions. Without
  saved fields the goal says what to turn on, and an optimization with antenna goals checks this before
  it starts.

## 0.2.8 (2026-10-30)

### Fixed
- **Far field with a PML drawn by hand**: PML assigned on the faces of an air box gave no far field ("Far
  field requested but no radiation/PML boundary present"). The faces under the PML are now the far-field
  surface, as with Auto Open.
- **Slow display of thin wires**: the display mesh of a thin wire, a bondwire or the inner conductor of a
  coax followed its small radius along the whole length (a monopole wire had 53 000 triangles, opening such
  a design took seconds). Thin tubes now get the coarser display sizing of swept pipes; other cylinders keep
  their round outline.
- **Showcase project**: the coax-fed monopole has its inner conductor, 50 ohm, an annular port and a far
  field with its PML.

## 0.2.7 (2026-10-30)

### Added
- **SPARTA as matrix solver.** A setup's *Matrix solver* (and the default in Tools > Options) now offers
  *SPARTA (GPU)* and *SPARTA (CPU)*: the sparse direct solver of EMerge's author, which factors the matrix on
  the graphics card (Metal on Apple silicon, Vulkan on AMD and NVIDIA under Windows and Linux, single
  precision) or on the processor (double precision). SPARTA is optional (AGPL-3.0): *Install SPARTA* in
  Tools > Options > HPC and Analysis installs its release wheel into Hertzon's Python environment. Without a
  usable GPU, SPARTA (GPU) factorizes on the CPU and says so in the log; periodic cells use its LU, as their
  matrix is not symmetric. SPARTA's CPU solver uses MKL (installed with PARDISO) instead of its OpenBLAS,
  which crashed the solver with 64 threads, and keeps its symbolic analysis for all frequencies of the sweeps
  on the final mesh (it was repeated for every batch of an interpolating sweep). With SPARTA the frequencies of a sweep are solved one after another, and the Fast
  sweep keeps PARDISO. *PARDISO + SPARTA sweep (CPU/GPU)* solves the adaptive passes with PARDISO (every pass
  is a new mesh, where SPARTA's analysis is slow) and the sweeps on the final mesh with SPARTA.
- **Solver comparison in the benchmark**: `python -m hertzon.solver.benchmark ... --solvers "PARDISO,SPARTA (CPU)"`
  solves the setup with each solver and then the sweeps of every further solver on the first solver's final
  mesh, with the sweep time and the largest S-parameter difference to the first solver. In Tools > Solver
  Benchmark this is the *Solver Comparison* tab: check the solvers (PARDISO, SPARTA (CPU) and SPARTA (GPU)
  by default), solve with all cores, and compare the time per phase, the sweeps on the same mesh, the peak
  memory and the S-parameter difference; a run that could not use its solver (SPARTA not installed, no
  usable GPU) is marked. *Make Fastest the Default Solver* sets the default for new setups.
- **Rename a report** (#130): right click a report in the project tree > Rename... gives the report and its
  open tab a new name; a name that another report already has is refused.

### Fixed
- **First click on an object no longer hangs for seconds.** The Properties of the clicked object list every
  material, and the first time this read the EMerge material library, whose import (with scipy) took several
  seconds on a cold start. The library is now read in the background as soon as the window is open.
- **3D polar plot** (#129) no longer shows the *Constant* and *Value (deg)* fields of the 2D radiation
  pattern cut; the 3D plot shows the whole sphere, and the fields had no effect on it.
- **Port Zo over a sweep** (#125): the Zo of a TEM port is the same at every frequency up to rounding
  (1e-12 ohm), and a report of it alone autoscaled onto those last digits, so the flat line looked like
  random data. A curve that is constant up to rounding is now drawn in a range of +-1 % around its value,
  without an axis offset; set Y min/max in the report to choose another range.
- **Export Data and Copy Data of a report** (#136) write the curves as the report draws them: the x values
  in the units of the axis (GHz, the angle of a far-field cut in deg, the distance of a near-field line), one
  x column per curve when the curves have different x values, and quoted column names. Far-field cuts and
  sweeps with fields gave an empty file or none; a failed export now says why in the report's info line. A
  complex trace without a function (`S(1,1)`, `Zo(1)`) is drawn and exported as its magnitude, not its real
  part.
- **Curved elements next to a radiation boundary or lumped port.** A curved surface that touched a radiation
  boundary, a lumped port or an impedance face was meshed flat, and so was every curved surface connected to
  it: a cone or post standing on the air box lost all its curved elements (the mesh plot showed the facets).
  Now only the edges on such a face stay straight; only a wave port still keeps the curved surfaces connected
  to it flat. (EMerge `78a7003`, FennisRobert/EMerge#91.)

## 0.2.6 (2026-10-28)

### Fixed
- **Update without "Set up the Python environment"**: when that component of the installer was switched off
  for an update (or failed), the new program ran on the packages of the old version (for example the previous EMerge)
  and showed "First start detected" until its first solve, because the setup never compiled the kernels.
  The installer now marks the Python environment as outdated, so Hertzon.exe updates it and compiles the
  kernels at the next start, as the installer's message already promised.
- **Solver kernels are compiled again only when they change**: a new EMerge (also another build with the same
  version number), numba, Python or a change of Hertzon's own kernels. An update that keeps all of them no
  longer runs the warm-up in the setup and shows no "First start detected" note; Hertzon's own few kernels
  are compiled quickly during the first solve.
- **Vector values in reports**: an equation that is a vector value, e.g. the integral `int_surf(Patch, E × H)`,
  was offered nowhere. Fields reports now list its magnitude and components (`mag(Spp)`, `x(Spp)`, ...),
  and Add to Report on the equation makes a Fields report of its magnitude. The calculator says that an
  integral is a value, not a field plot, and that the integrand without the integral is the field to plot.

## 0.2.5 (2026-10-27)

### Added
- **Periodic unit cells and Floquet ports** are now solved. Primary / Secondary boundaries tie every
  Secondary face to the Primary face it is a translated copy of (one lattice vector away) and make the model
  a unit cell of an infinite array. The phase between them comes from the new *Scan angle theta / phi* of the
  Secondary boundary, or from the phase delay (*Phase delay: Input Phase Delay*), which is converted to the
  scan angles at the solution frequency. Floquet ports on faces normal to z excite and absorb the plane
  waves TE00 and, with two modes, TM00 in the scan direction; S-parameters have one row per mode
  (Matrix Data lists them as `FP1:TE00`, `FP1:TM00`, ...). Checked against the exact transmission through a
  dielectric slab at normal and oblique incidence. Limits: higher Floquet modes, a Floquet port in a
  dielectric, and TM00 at oblique incidence (absorbed with the TE00 propagation constant) are not available.
  Periodic models are solved with PARDISO (EMerge `9ef0053` solves the non-symmetric system of a periodic
  cell correctly); with an EMerge whose PARDISO interface solves the transposed system Hertzon falls back to
  SuperLU.
- **Example `examples/FSS_UnitCell.hzn`** (File > Open Example): a cross dipole frequency selective surface
  as a periodic unit cell with Floquet ports below and above. The showcase's unit cell design is solved now.
- **Terminal solution**: a design of solution type Terminal is now solved as terminals instead of modes.
  Each lumped port is one terminal; a wave port gets one terminal per signal conductor touching its face,
  with the largest conductor (or the one named in the port's new *Reference conductor*, Terminals tab) as
  reference. The terminal S-parameters refer to 50 ohm, or to the renormalization impedance when
  Renormalize is on. The solver log names the terminals of every port. A wave port with one signal
  conductor gives exactly the renormalized result of the modal port. A wave port with several signal
  conductors absorbs each of its modes with the mode's own propagation constant, so the even and odd mode of
  coupled lines (differential microstrip pairs) leave the model without spurious reflection.
- **Differential pairs** (Simulation > Excitations > Diff. Pairs, or right click Excitations): pairs of
  terminals with the reference impedances of the differential and common mode (default twice and half the
  terminal reference). Reports offer the mixed-mode quantities `S(Diff1,Diff1)`, `S(Diff2,Diff1)`,
  `S(Comm1,Diff1)`, `Z(...)`, `Y(...)` and `VSWR(Diff1)` next to the single-ended ones, also in output
  variables, optimization goals and parametric calculations. Changing the pairs needs no new solve.
- **Port impedance and propagation constant as report quantities**: `Zo(i)` (ohm, per port or terminal) and
  `Gamma(i)` (1/m, per port mode; `re(Gamma(1))` is the attenuation, `im(Gamma(1))` the phase constant),
  categories *Port Zo* and *Propagation Constant*. Stored over frequency for every new solution.
- **Port Field Display**: right click a wave port > Port Field Display: Mode k shows the mode field on the
  port face (magnitude and arrows) with its Zo and Gamma; Hide Port Field removes it.
- **Example `examples/DifferentialPair.hzn`** (File > Open Example): a 100 ohm edge-coupled microstrip pair
  with the terminal solution, differential pairs and reports of the mixed-mode S-parameters, the line
  impedances and the propagation constants; tutorial section 9 walks through it.
- **Named rows of S**: with terminals or ports of several modes, the report quantities name the rows,
  `S(P1_TraceP,P1_TraceN)` for terminals and `S(P1_M1,P1_M2)` for port modes (Floquet ports
  `S(FP1_TE00,FP1_TM00)`); the numbers keep working. The report dialog and the data table list which
  terminal, port or mode each number is.
- **Edit Sources per terminal**: a terminal design with a port of several terminals stores its fields per
  terminal (1 W incident on the terminal, the others terminated in their reference impedance), and Edit
  Sources lists the terminals. A differential pair is driven with 0.5 W at 0° and 180° on its two terminals,
  so field plots, animations and far fields show the differential or the common mode.
- **Solve ports only** (setup option): solves only the port modes of the wave ports at the solution and
  sweep frequencies (Zo, Gamma, port fields) without the 3D solution, to check ports quickly.

### Fixed
- Hertzon now requires gmsh 4.15 or newer. With gmsh 4.13 or 4.14 one failed mesh generation (for example
  on faces of an imported CAD model) left every later display and solver mesh of the session empty, so
  ports and boundaries of the next model had no faces.
- **Field plots of the Poynting vector** no longer turn with the plot phase. `S = 1/2 E x H*` is a time
  average; the plot showed `Re{S e^(j phase)}`, so at other phases and in phase animations the arrows showed
  the reactive power, and a real expression such as `re(S)` reversed its direction every half period. Real
  expressions and expressions with `S` now show the same picture at every phase: the real part of `S`, the
  active power flow.
- **First click in the 3D view no longer hangs.** Right after the start, the first hover or click in the 3D
  view stalled while the graphics driver built the shaders of the highlight styles (half transparent faces,
  wide lines, vertex spheres). They are now drawn once, unseen, when the window opens.
- **Rotate, Sweep Around Axis and Duplicate Around Axis** now use the working coordinate system (for example
  an object or face coordinate system) instead of always the global one. The dialogs have a *Coordinate
  system* field, preset to the working CS; Axis and Center are given in it.
- Terminal solution, wave port with several signal conductors and mode type Auto: the port only accepted
  TEM modes, and on coupled microstrip the odd quasi-TEM mode (a small axial field at high frequency) was
  rejected, so the solve stopped with a division by zero. Auto now accepts every mode type; the log no
  longer reports the missing conductor pair of such ports, whose impedances come from the terminals.
- Wave ports with several modes: EMerge added the modes of a repeated modal analysis at one frequency to the
  modes it already had (a sweep analyses its mean frequency and then every frequency), so a port with
  several modes could mix the modes of two analyses. Hertzon now solves the modes of such ports once per
  solve, and EMerge replaces the modes of a repeated analysis (EMerge `debe7ba`).
- Wave ports with several modes of the same propagation constant (several conductors in one homogeneous
  dielectric): EMerge returned these modes in any basis, often two almost equal modes, so the port could not
  represent all fields and the S-parameters of a lossless model were not lossless (|S^H S − I| up to 0.54 on
  two wires in a box). EMerge `debe7ba` makes them real and orthogonal; the same model now gives 1e-6.

## 0.2.4 (2026-10-26)

### Added
- **Showcase project** `examples/Showcase_AllFeatures.hzn` (built by `examples/make_showcase.py`): one
  project with six designs that uses nearly every modelling, setup and post-processing feature, for testing
  the GUI, scripts and the MCP server against a single rich file.
- **Update dialog**: when a new Hertzon version is out, a small dialog after the start shows its version and
  the release notes of every version since the installed one, with **Install**, **Skip this version** and
  **Later**. Install downloads the installer from the release (checksum checked), asks to save open changes,
  closes Hertzon and starts the installer; settings and the Python environment are kept. Because new
  versions come out often, the dialog comes up by itself at most once every 7 days; in between the status
  bar says "Hertzon X is available: Update to X..." and opens the dialog on a click. Skip this version asks again
  only for a newer version, Later after the 7 days. The dialog does not block the start or the
  work, and nothing is installed without a click on Install. Without the Windows installer (Linux, pip or a
  source checkout) the button is **Download** and opens the release page. **Help > Check for Updates...**
  checks on request. The check at start can still be switched off in Tools > Options > General, and the
  environment variable `HERTZON_NO_UPDATE_CHECK=1` switches off both for managed or offline computers.
- **Fields Calculator** (Calculator in the Results ribbon): an Equations tab next to the 3D Modeler for
  equations of the stored fields. An equation is typed with its name in front (`P_in = flux(Port1, re(S))`)
  and saved under that name. The tab only defines equations and shows their type (scalar or vector, field
  or value) and unit; saved equations are used in Plot Fields (fields) and in reports (values). While the
  tab is open the ribbon has an Equation page with fields, symbols, fractions, roots,
  integrals, large operators, brackets, functions and the library. The formula is typed into a typeset
  formula editor as in a desktop office program: a command after a backslash turns into its symbol with
  Space (`\times` gives ×, `\curl` gives ∇×, `\iint` a surface integral). Text shows the same formula as one
  line of text with units, which is also what is saved. An integral or another operation over a geometry
  switches to the 3D Modeler and asks for the face, object or line, then comes back with it
  below the sign. Examples:
  `flux(Port1, re(S))`, `int_line(Radial, E)`, `max(Dielectric, |E|)` or
  `int_vol(Dielectric, 0.5*omega*eps0*eps_r*tand*|E|^2)`. Each equation shows its type and unit and a typeset
  formula while you type; errors are underlined where they are. Integrals over lines,
  faces and volumes of model and non-model objects, cut planes (`cut_xy(10mm)`), points, components in
  cylindrical and spherical coordinate systems, curl, divergence and gradient. Values are computed on the
  solver's elements with their quadrature, curved elements included. Equations and user functions are saved
  with the design and listed in the project tree under Equations (open, plot, add to a report, rename,
  delete, and export to or import from a file for other designs); a library has power, loss, energy, Q, voltage and current. A named field can be
  plotted like E or H (Plot Fields > Quantity). A value computed from the fields is shown over frequency in a
  Fields report (Results > Create Fields Report). Equations can also use the network data, `S(2,1)`, `Y(1,1)`,
  `Z(1,1)` and `VSWR(1)`, for example `RL = -dB(S(1,1))`; an equation of these alone needs no fields and is
  shown in the solution data reports under the category Equations, at every frequency of the solution.
- **Volume field plots**: Plot on "(selection, inside solids)" (or "Inside the selected solids" in the plot
  attributes) shows a scalar field through a solid such as a substrate as coloured dots instead of on its
  surface; vectors keep their arrows inside solids. Works for the built-in quantities and for named
  equations of the Fields Calculator.
- **Plot Fields with equations, also without a selection**: the Plot Fields menu (right click in the 3D view,
  More in the Results ribbon) has an Equations entry with the field equations of the Fields Calculator and
  New Equation. A right click on empty space in the 3D view offers Plot Fields too; without a selected object
  the field is plotted on a cut plane.

- **AI assistants (MCP) cover the new features**: `get_geometry(include_edges=true)` lists the edges and
  vertices with the indices of Fillet and Chamfer (`render_image` can label them), so an assistant can round
  or cut single corners. New tools: `set_equation` (equations and functions of the Fields Calculator, import
  and export), `set_report` (all report kinds, also Fields reports), `set_field_plot` (on objects, faces or a
  cut plane), `set_coordinate_system` (new objects are made in the working coordinate system, as in the
  window), `set_parametric` and `set_optimization`; `run_simulation`, `get_status`, `abort_simulation` and
  `get_results` take `parametric=` or `optimization=`. `get_project` lists all of these and `delete` removes
  them. Equations are evaluated in the window, in field plots and reports.

### Changed
- **Tutorial: equations** (section 8): the return loss from the S-parameters, the power through both ports
  of the waveguide as flux integrals (checked against the S-parameters), the power flow along the guide as
  a field plot, and the library. `examples/WaveguidePost.hzn` has these equations, the reports `Return Loss`
  and `Port Powers` and the field plot `Sy_Cut`. The README, the feature overview and the tutorial basics
  also describe the Fields Calculator, Fillet and Chamfer at vertices and the update dialog.
- **Chamfer of a sheet at its vertices**: like Fillet, a sheet (for example a patch) is now chamfered by
  selecting its corner vertices (press V, also in the context menu); each selected corner is cut off by the
  distance. Solids are still chamfered along their edges.
- **Equation library**: right click on Equations → **Open Library...** opens a window with the ready-made
  equations and functions; tick one or more (or a whole group) and import them into the design at once.
  It replaces the From Library submenu.
- **Surface resistance in the ribbon**: Function → Rₛ inserts `rs(σ)`, the surface resistance of a good
  conductor (√(ωμ₀/2σ), in Ω), e.g. for conductor losses.
- **Units on the plot axes**: the y axis of reports shows the unit of the traces (`[dB]`, `[Ω]`, `[W]`, the
  unit of an equation), with the trace name when there is only one; the radial axis of a polar radiation
  pattern shows its values with the unit (`-10 dB`). Data tables, the CSV export and the colour bar of the
  3D polar plot name the unit too. A colour bar of a dimensionless field no longer shows empty brackets.
- **Field plots on a selection: pick in the 3D view**: Plot on "(selection)" or "(selection, inside solids)"
  with nothing selected no longer makes an empty plot. After OK a box over the 3D view says what to pick
  (faces or objects, or the solids), shows what is selected and plots it with Plot. The same for Modify Plot
  and for Plot Mesh without a selection. The cut position is only shown for cut planes.
- **Fillet of a sheet at its vertices**: a sheet (for example a patch) is now filleted by selecting its corner
  vertices (press V) instead of edges; each selected vertex gets one fillet with the radius. Projects that
  filleted a sheet by its edges in 0.2.3 build as before.
- **Benchmarks**: the Solver Benchmark re-run on the 32-core workstation with 0.2.3 and hzAURA, compared with
  the runs of 19 October (patch antenna 2.5 to 3.7 times faster with the same result, microstrip 1.4 to 1.6
  times), and the hairpin filter added (docs/BENCHMARKS.md). The raw results of both are in
  examples/benchmark/scaling/results.

### Fixed
- **Fillet and chamfer on an object in a relative coordinate system** failed with "Could not compute
  fillet": placing the object in its coordinate system turned its planes and cylinders into general
  surfaces. It is now rotated and moved, so the surfaces stay exact.
- Validation Check no longer reports a **Length Based On Edges** mesh operation as "has no assignment" (an
  error that blocked the analysis): its edges count as its assignment.
- **Wave port on a metal face found no modes**: a wave port on a face of a PEC solid, for example the
  bottom of a coax bore in a metal body, failed with "ModalPort could find no modes" (and "k=-2 must be
  greater than 0"), because the metal also zeroed the field on the port face. Such a solid now backs the
  port: no field inside and PEC on its surface, with the port face left free. The log says which object
  backs which port.
- **Unit dB of equations**: an equation of a level in decibels such as `RL = -dB(S(1,1))` showed the unit 1 in
  the Equations tab while the report axis said dB. The Equations tab, values, report axes, data tables and
  colour bars now show dB, also for sums and multiples of levels (`RL + 3`, `2*RL`).
- **Solutions shown as outdated**: the solutions of projects solved with 0.2.3 or earlier were shown as
  outdated (and an analysis started the adaptive passes again from the initial mesh), and saving an equation
  of the Fields Calculator did the same to a current solution. Equations are post-processing and no longer
  count as a change of the model.
- **Coarse cylinders next to a helix**: a helix wire (or another swept pipe) in the model made every
  cylinder, sphere and round hole in the 3D view coarse (8 segments per turn), also in objects not touching
  the helix. The coarser display sizing now applies only to the pipe surface itself.
- **Solve failed with a KeyError after a helix wire**: the round profile that a helix wire or a Sweep
  Along Path is swept from stayed in the model as a loose sheet, and meshing stopped with
  `KeyError: (n1, n2, n3)`. The profile is removed after the sweep.
- **Field plots of a changed equation**: saving an equation (or a function it uses) draws its field plots
  on screen again with the new formula; before, they kept the old field until cleared and plotted again.
  An equation changed from a field to a value deletes its field plots.
- **Square root in the formula editor**: `\sqrt(...)` and `sqrt(...)` typed with parentheses (or `sqrt`
  alone) become the root sign over the argument; before, `\sqrt(...)` stayed as text with parentheses.
- **Numbers with units in the formula editor**: `5.8·10⁷ S/m`, `5.8e7 S/m` or a unit as a fraction after a
  number are read as one number with its unit; before, the unit came out as unknown names and the formula
  showed an error.
- **Selection rectangle left in the 3D view**: when the mouse release of a box selection did not reach the
  3D view (for example after switching tabs), the rectangle stayed on screen. It now goes with the next move
  or click in the view.
- **Slow update with the Windows installer**: installing a new version over an existing one hung for minutes
  after copying the files, because the installer took the size for Settings > Apps by walking the whole
  folder, including the existing Python environment; it now takes the size from the installer itself.
- **Report tabs push the window off screen**: opening a report (for example an XY plot) raised the main
  window's minimum height to the full height of the trace editor, so on a 1080p screen with display scaling
  a maximized window reached below the screen and the status bar with the coordinates was cut off; after
  maximizing again the panels could be squeezed. The trace editor on the left of a report now scrolls when
  the window is low.
- **Analyze a single sweep and continue an adaptive run with hzAURA**: the adaptive mesh refined with hzAURA
  was not stored with the refinement state, so analyzing only a sweep stopped with "No adaptive solution to
  reuse", and an interrupted run started again from the initial mesh. The mesh is now stored and rebuilt
  from it; this also makes the parallel sweep runs of the Solver Benchmark work again.

## 0.2.3 (2026-10-25)

### Changed
- **Examples on the current features**: all examples use hzAURA refinement and curved elements and are
  saved in the current file format. New in the examples: field plots on cut planes (waveguide post, coax,
  dipole, sphere, patch), a half dipole with a Perfect H symmetry plane next to the full one, a microstrip
  line with a grouped 0603 chip capacitor (Lumped RLC) as a DC block, a dipole sweep with saved fields for
  animations, 3D gain plots, and reports for impedance, gain cuts, antenna parameters and bistatic and
  monostatic RCS in dBsm. Files saved by 0.2.2 and earlier still open as before.
- **Tutorial**: new steps for cut planes, field animations and GIF export, a half model with a symmetry
  plane (3.6) and a chip capacitor on a microstrip line (7); screenshots and numbers from the current
  version.
- **Solver Benchmark**: the three projects of Tools > Solver Benchmark use hzAURA; the mesher comparison
  projects have an hzAURA variant next to each Local (Delaunay) one.

### Fixed
- **Fillet on sheets**: a fillet on a sheet (for example the patch of a patch antenna) made the whole object
  disappear, because the fillet only worked on solids. On a flat sheet it now rounds the corners at both ends
  of each selected edge. When a fillet or chamfer cannot be applied (for example a radius larger than the
  adjacent edges allow), the object stays unchanged and a message says why.

## 0.2.2 (2026-10-25)

### Changed
- **AURA refinement keeps curved surfaces exact (hzAURA)**: EMerge `55ebd4d`. Where a new node on a curved
  face or edge cannot be moved onto the CAD geometry by bisection, it is put there anyway and the elements
  around it are re-meshed as a constrained Delaunay cavity, instead of leaving the node closer to the chord
  (a dent). Faces that are neither spheres, cylinders nor planes are followed by a bicubic interpolation of
  their CAD parametrisation: on a lofted free-form body new surface nodes stay within 0.14 mm of the face
  (was 1.4 mm). AURA's refinement time is unchanged. The setting is now called **hzAURA (EMerge)**; projects
  and scripts with "AURA (EMerge)" keep working and keep their solutions.
- **More accurate RCS of scattering problems**: with an incident plane wave, Auto Open now leaves one
  wavelength of air around the objects (was half a wavelength), and the far field is integrated with Gauss
  points over a box λ/8 around the scatterers instead of over mesh faces halfway to the boundary. For the
  250 mm PEC sphere at 1 GHz the error of the bistatic RCS against the exact solution drops from 0.6 dB to
  about 0.4 dB (monostatic from +1.4 dB to +0.2 dB), with fewer tetrahedra (26k instead of 38k). A padding
  set in the design is used as before.

## 0.2.1 (2026-10-25)

### Added
- **Update hint**: at start Hertzon asks GitHub in the background for the newest release. When it is newer
  than the running version, the Message Manager and the status bar say "Hertzon X is available" with a link
  to the release page. The start does not wait for it, and without network nothing is shown. Switch it off in
  Tools > Options > General (or with the environment variable `HERTZON_NO_UPDATE_CHECK=1`).
- **File > Open Example**: lists the example projects shipped with Hertzon (patch antenna, hairpin filter,
  waveguide post, RCS models, ...) and opens the chosen one as an untitled copy. The example files stay
  unchanged; Save asks for a new file name (in the Documents folder) and an analysis asks to save first.

### Changed
- **Field plots on helper sheets follow the sheet**: a field plot on a non-model object is drawn again on
  the new shape when the object is moved, resized or otherwise changed. The solution stays valid, as
  non-model objects are not part of the solve.

### Fixed
- **Clean edges for field plots on drawn planes**: the plot on a non-model sheet now ends exactly on the
  sheet's outline, also for round outlines and sheets with holes. Before, whole cut triangles stuck out over
  the edge or were missing next to it.
- **Animate no longer resizes the 3D view**: the player bar under the view asked for about 1100 pixels of
  width, so starting an animation squeezed the model tree and widened the 3D view to the same size every
  time (and it stayed so after closing the player). The bar now fits the view as it is; long texts in it
  are cut instead.
- **RCS and far fields of scattering problems with AURA refinement**: the far-field surface (the box
  between scatterer and radiation boundary) lies inside the air, and the direction of its normals was taken
  from a neighbouring element. After an AURA pass part of them pointed inwards, so the monostatic RCS
  jumped by several dB from pass to pass (NATO sphere: between -6.5 and +3.5 dB from the Mie value, and
  the scattered power through the box fell below zero). The normals now point away from the box centre.
  Local (Delaunay) and Remesh were not affected.

## 0.2.0 (2026-10-25)

### Added
- **Animations as GIF**: **Export GIF...** in the animation bar (or Fields > Export Animation as GIF...)
  saves all frames of the running animation as a looping GIF. Each frame is the view exactly as shown, in
  its pixel size, and the GIF runs at the speed you see: the bar measures the frames per second actually
  drawn during playback (shown next to the speed setting, it can be lower than the setting for heavy
  plots) and the GIF uses that rate. Without a measurement the export first plays the animation for a
  moment to measure it. All frames share one colour palette, so the colour scale and the field do not
  change colour from frame to frame.
- **Object groups**: Edit > Group (Ctrl+G) puts the selected objects, also solids, sheets and lines
  together, into one group. A click on a member in the 3D view selects the whole group, so Move, Rotate,
  Mirror, Copy, Hide or Delete act on all of it; the model tree lists the groups under **Groups** (rename,
  ungroup, single members selectable there). Edit > Ungroup (Ctrl+Shift+G) dissolves a group to edit single
  objects again. Groups are saved in the project. SMD components (C, L, R, Touchstone parts) are inserted as
  a group of pads, terminations and element sheet, and the integration line of a lumped port or element now
  moves with its objects.
- **Box selection in the 3D view**: a left drag draws a rectangle and selects every object that lies
  completely inside it (in face, edge or vertex mode: every face, edge or vertex completely inside).
  Ctrl + drag adds to the selection. A group is selected only when all its members are inside. Hidden
  objects are skipped; a click still selects one item.
- **Mesh refinement "AURA (EMerge)"**, EMerge's own local refinement, with the same error estimate,
  convergence criterion and curved elements as the other methods. A comparison script measures the time to solution against
  Local (Delaunay) on the benchmark models (`examples/benchmark/mesher`).
- **Lumped Elements tab**: inserts a chip resistor, inductor or capacitor, or a component measured by a
  Touchstone file: pick the package in the menu of the button and click the position on the board. The
  part comes with its pads (Perfect E), and its position is the design variables <name>_x/_y/_z, so it
  moves as a whole. With two traces selected it is placed in the gap between them. Packages 0201, 0402, 0603, 0805, 1206 and the RF series
  Coilcraft 0402HP/0402HPH/0402CS/0603HP/0603CS and Murata LQW15AN/LQP03TN/GJM03/GJM15 with their body
  dimensions. Hertzon adds the pads of the land pattern (made like the traces), the element sheet with its
  Lumped RLC boundary and integration line, and optionally a 3D body with metal terminations (the
  mounted inductance from the field solution). The ideal value can carry the typical ESL and ESR of the size.
- **Lumped RLC: Model** Parallel RLC (as before), Series RLC or Touchstone (.s1p, or .s2p measured in
  series or as a shunt). The Validation Check reads the Touchstone file; the solver warns when a sweep
  leaves its frequency range.
- **Draw Integration Line** (right click a Lumped Port or Lumped RLC in the project tree): start and end
  point picked with two clicks in the 3D view, with snapping to vertices and edge centers.
  **Auto Integration Line** places it between the conductors again.

### Changed
- **Field plot animations compute much faster**: a phase animation builds the plot (surfaces, cut planes,
  arrow positions) once and only turns the field for each frame, instead of building the whole plot again.
  On a patch antenna with 54 000 tetrahedra, 18 frames of Mag_E on the patch take 0.15 s instead of 21 s,
  on a cut plane 0.2 s instead of 6 s. Frequency and excitation animations no longer read the field file
  again for every frame, and the mesh faces of a plotted sheet or object are found only once per solution.
- **Phase animation default: 35 steps** (was 19) from 0 to 360 deg, for a smoother wave.
- **Documentation** brought up to the current state: the README, the website and the feature overview
  describe AURA as the default refinement, EMerge's curved elements, object groups, box selection,
  coordinate systems, parametric sweeps, plane waves and symmetry planes; docs/MESHER.md covers all
  refinement methods. Primary / Secondary boundaries are marked as not passed to the solver yet.
- **Faster 3D view with many objects**: the model shows several times faster (300 objects: 1.8 s instead
  of 7.7 s), a click to select, an attribute change and the edge pre-highlight no longer slow down with
  the number of objects (300 objects: select 0.3 s instead of 0.8 s, edge hover 2 ms instead of 0.4 s).
  Several changes in one step now redraw the view once instead of two or three times. The view looks
  and picks exactly as before.
- **Assign Material only for solids**: greyed out in the ribbon, the Modeler menu and the context menus
  when only sheets or lines are selected (they get a boundary such as Finite Conductivity or Impedance);
  with solids and sheets selected the material goes to the solids. The object properties of sheets and
  lines have no Material row. A material stored on a sheet by an
  older project is kept and, as before, not used by the solve.
- **AURA (EMerge) is the default mesh refinement for new setups** (was Local (Delaunay)). On the
  benchmark models it reached the convergence criterion in 0.35 to 0.6 times the time in three of four
  cases and was as accurate or more accurate per tetrahedron. Saved setups keep their method; Local (Delaunay) stays selectable.
- **EMerge `e03a085` (its `hertzon` branch)**, which now contains three of Hertzon's speed features: the
  far field of all ports in one pass, the curved elements on curved surfaces and the faster removal of the
  PEC unknowns before each solve. Hertzon uses EMerge's versions and drops its own (about 1200 lines).
  Results and meshes are the same on all benchmark models; far fields of many ports are computed about
  twice as fast, and the one-time compilation of the curved element kernels after an installation
  (about a minute) is gone.
- **EMerge 3.0.0b5 plus the fixes on its `hertzon` branch** (commit `2811e9a`; was 3.0.0a25, as advised in
  #25). Hertzon now installs EMerge from wheels built from a pinned commit, so solver fixes arrive without
  waiting for a release. Thin conductors with thickness (two-sided Finite Conductivity) are now solved
  with separate fields on both sides of the sheet: filters with such traces show slightly more conductor
  loss (hairpin example: S21 about 0.3 dB lower) and smooth surface currents. Parallel sweeps no longer
  keep the memory of every finished frequency batch. Hertzon's own workarounds for these points, for the
  port mode solve, for conductor sheets in the error estimate (it changed no mesh) and for the welcome
  prompt of the setup are removed. Speed and results were checked
  on the benchmark models (`examples/benchmark/tools/emerge_bench.py`, docs/EMERGE_INTEGRATION.md).
- **Integration line of Lumped Port and Lumped RLC**: placed automatically between the two conductors
  that touch the sheet, from the centre of one contact to the centre of the other (conductors: objects of a
  metal material and faces or objects with a Perfect E or Finite Conductivity boundary). A gap longer than
  the traces are wide or a sheet wider than the traces now gets the right line. Without two touching
  conductors the line runs across the shorter side of the sheet as before.

### Fixed
- **Cut planes no longer show jagged spots at material boundaries and metal sheets**: the field is stored per
  element and jumps at a substrate surface, a patch or a ground plane, but the cut plane merged the points of
  both sides and gave each one the value of just one side. Along a patch antenna this drew red sawtooth streaks
  on the patch and at the ground plane. The cut now keeps the elements apart, and the smooth plot averages
  only within one material. The peaks at metal edges and corners stay: they are real.
  A field plot on a drawn plane (a sheet that is not part of the model) is now cut from the solver mesh the
  same way and cropped to the sheet. Before, the field was interpolated onto the sheet's own triangles, which
  cross the material boundaries and drew the same sawtooth there.
- **Thicken sheet direction**: a positive thickness now always thickens to the positive side of the
  sheet's main axis (+Z for a sheet in XY), a negative one to the other side. Before, the direction followed
  the winding of the outline, so a rectangle dragged to -X or -Y, or a polyline drawn clockwise and covered,
  was thickened to -Z. While the Thicken dialog is open, an arrow on each sheet in the 3D view shows the
  direction, and the new option **Reverse direction** thickens to the other side.
  Projects saved with an earlier version keep their shape: on loading, Reverse direction is set for the
  sheets that were thickened to the negative side.
- **Far fields with symmetry planes**: the far field (gain, directivity, pattern, RCS) of a half or quarter
  model now includes the mirror images at Symmetry boundaries (Perfect E or Perfect H, planes parallel to XY,
  YZ or ZX). Before, only the simulated part radiated. Fields and radiated power refer to 1 W into the full
  structure, as in EMerge's `farfield(syms=...)`. Checked with a dipole as full and half model: peak
  directivity 1.645 and 1.650.
- **Grid and axes stay at the origin**: with the model far from the origin of the working coordinate
  system, the grid now spans from that origin to the model instead of sitting under the model only, and the
  axis triad at the origin is drawn in full. Before, only its X/Y/Z captions were left floating in space.
  The automatic grid spacing grows with a far origin so the grid keeps a bounded number of lines.
- **Integration lines follow their object**: the current or voltage line of a lumped element or lumped port
  is now given in the coordinates of its sheet as created, so it moves, rotates, mirrors and scales with the
  sheet (Move, Rotate, Mirror, Scale, Orientation), in the 3D view and in the solve. Before, it stayed where
  it was set and a moved component kept its arrow, and its port direction, at the old place. Highlighted
  boundaries and ports are drawn again when the geometry changes. Older projects are converted on loading.
- **Split in a Face CS or Object CS**: the split plane (XY, YZ, XZ or Custom Base/Normal) is now taken in
  the working coordinate system, stored with the operation as *Coordinate system*. Before, the plane always
  lay in the Global CS, and an object far from that plane was deleted instead of split. Older projects keep
  splitting in the Global CS.

## 0.1.25 (2026-10-23)

### Changed
- **Contributors** section in the README (all-contributors convention, `.all-contributorsrc`): bug reports
  and feature ideas are credited.

### Fixed
- **Radiation Pattern, polar plot**: a Phi cut is now labelled over theta -180..180 deg, as defined on the
  far field sphere and shown in the rectangular plot, instead of 0..360 deg (#18). A Theta cut keeps phi
  0..360 deg.

## 0.1.24 (2026-10-23)

### New
- **Exact overlap check of solids**: an exact geometry check in the Validation Check lists the
  solids that overlap and how much. A partial overlap of two different dielectrics or two different metals
  is now an **error** that blocks Analyze, optimization and parametric runs (also from scripts and the AI
  link, and checked again in the solver). Objects completely inside another stay allowed (the inner one
  applies); a dielectric completely inside a conductor is flagged as without effect.
- **Design setting Material override** (on by default): a metal overrides a
  dielectric where they overlap. Off: every partial overlap of different materials is an error and the
  inner object always wins, also a dielectric inside a metal.
- **Resolve Overlap** / **Resolve All Overlaps** in the Validation Check subtract the winning object from
  the other one (Subtract with clone), so the model shows what is solved and is free of overlaps.
  Described in the tutorial (basics).

### Changed
- **Maximum Delta Energy defaults to 0.001** (was 0.1) for new setups, and the examples use it. It is the
  convergence criterion of incident wave designs (RCS); 0.1 stopped the adaptive passes far too early.
  Saved projects keep their value.

### Fixed
- **Solver process crash at exit (code -1073741819 / 0xC0000005 after the solution was saved)**: the
  TerminateProcess exit of 0.1.19 never took effect. Called through ctypes without HANDLE types, the
  process handle was cut to 32 bits (or, once the memory query had typed the shared kernel32, did not convert
  at all), so the process still ended through os._exit and the crashing DLL teardown. The calls are typed
  now; the same fix makes the opt-out of Windows efficiency mode for the solver process work.

## 0.1.23 (2026-10-23)

### New
- **Draw > Arc > 3 Point / Center Point**, also in the ribbon next to Polyline: a polyline of
  one arc. Center Point: click the start point, the center, then move the cursor around the center; the arc
  follows it in both directions and past 180 degrees, the click sets the angle.
- **Center Point Arc segments in polylines** (key **C** while drawing, like L / A / S): stored as the
  segment type `AngularArc:<angle>:<plane>` in *Segment types*, so the angle can be edited (and be an
  expression, e.g. `AngularArc:ang:XY`); the end point follows the angle. Closed arcs can be covered.
- **First start note**: when the solver kernels are not compiled yet (Hertzon started without the setup's
  warm-up, e.g. a pip installation, or after an update), the Message Manager and the status bar say
  "First start detected", and the first solve shows "compiling the solver kernels (once, a few minutes)".

### Changed
- **NATO sphere example**: radiation boundary instead of PML and Max Delta E 0.001; the sphere RCS example
  and the tutorial name the radiation boundary as well (with a plane wave the radiation boundary is used
  in any case, the incident wave enters through it; the help of *Open boundary* now says so).
- **Overlapping solids** with material override: a conductor wins over a dielectric (or air) in
  the overlap whatever its size; otherwise the smaller object wins, regions stay background. Partial
  overlaps of two dielectrics or two conductors of different materials give a warning.

### Fixed
- **Tools > Keyboard Shortcuts** opens again: the dialog failed at once because its table widgets were not
  imported (NameError), so shortcuts could not be viewed or changed.
- **RCS with air or vacuum objects** (e.g. a mesh refinement box): the far-field surface enclosed them too,
  and its numerical floor grew to the level of the result (NATO sphere -15..-20 dBsm instead of -7). It now
  encloses the scatterers only.
- **Field and mesh plots of overlapping objects** cover the whole object (e.g. Jsurf over the whole sphere,
  not only the part outside an overlapping box), and the per-object mesh statistics count each
  tetrahedron once.
- **Field plots on a sheet the mesh does not follow** (a model sheet drawn after the solve) interpolate
  over the whole sheet instead of showing a few scattered element faces.
- **Create as Non Model? > Don't ask again** keeps drawing Non Model objects; later objects were silently
  created as Model objects.

## 0.1.22 (2026-10-22)

### Changed
- **Solver core EMerge 3.0.0a25** (was 2.8.9): matrix assembly in EMerge's Rust core, about 4x faster
  sweep assembly; the first solve after the installation no longer waits for numba compiles of EMerge
  (cold start of the microstrip benchmark 12 min -> 2.5 min). Python 3.10 to 3.13.
- Hertzon's own extensions (re-mesh refinement, curved elements, thin conductors, PARDISO LDLT, parallel
  sweeps) ported; the curved-element kernels are now part of Hertzon (`ned2_kernels.py`, from EMerge 2.8.9).

### New
- **EMerge material library** (emsutil): 255 more materials (dielectrics, metals, semiconductors, foams,
  solder masks, liquids) in the Materials dialog, with a "Libraries" filter; frequency dependent data is
  used by the solver.
- **Markers in the polar Radiation Pattern**: double-click or right-click > Marker (max/min, next max/min,
  delta), drag a marker around the circle, marker table under the plot.
- **Beamwidth / side lobes** (right-click > Marker): opening angle of the main lobe at a level below the
  maximum (default -3 dB, e.g. -10 dB), its edges and the side lobe level, in the polar and the rectangular
  pattern.
- **Create Far Fields Report > Rectangular Plot**: the pattern cut over the angle as its own menu entry.
- **Save fields** of a sweep also for Fast sweeps: far fields, gain patterns and antenna parameters over
  frequency. Far field and antenna reports name the solved sweeps without fields (Interpolating, or Save
  fields off) instead of leaving them out silently.

### Fixed
- The Windows setup no longer stops at EMerge 3's one-time welcome text ("Press Enter to continue"):
  the setup marks it as shown and runs its checks without console input.
- Plane wave excitation on EMerge 3 (its new default angle convention sent the wave in the wrong direction).
- Thin conductors after a re-mesh pass (EMerge 3 did not split the new mesh).
- Rare crash of parallel sweeps on EMerge 3 (the worker threads shared one solver routine).

## 0.1.21 (2026-10-22)

### New
- **Subtract** dialog: arrow buttons **→ / ←** move the selected objects between Blank Parts and
  Tool Parts; several objects are selected by dragging with the mouse, Ctrl or Shift. Several blank parts are
  each cut by all tool parts. Swap exchanges all blank and tool parts.
- **Radiation Pattern** of a sweep with saved fields: the report shows the far field of the chosen solution
  (no longer always LastAdaptive) and has a **Frequency** selector
  ([#5](https://github.com/c8501024/Hertzon/issues/5)); the 3D Polar Plot too.
- Radiation Pattern **Plot: Rectangular**: the cut over the angle with markers (max/min, delta, bandwidth),
  axis properties and the marker table.

### Fixed
- View: the projection button is now **Orthographic** and shows its state (pressed: orthographic, not
  pressed: perspective), also in View > Orthographic Projection; switching keeps the model size on screen
  ([#4](https://github.com/c8501024/Hertzon/issues/4)).

## 0.1.20 (2026-10-22)

### New
- **File > Export > Gerber...**: manufacturing data from the real CAD geometry, as in the layout tools of
  commercial simulators ([docs/GERBER_EXPORT.md](https://github.com/c8501024/Hertzon/blob/main/docs/GERBER_EXPORT.md),
  [tutorial](https://github.com/c8501024/Hertzon/blob/main/docs/TUTORIAL.md#6-gerber-export-of-a-printed-circuit-board)).
  - One Gerber X2 file per copper layer (`TF.FileFunction Copper,Ln,Top/Inr/Bot`), the board outline
    (`Profile,NP`) and Excellon drill files, split into plated (vias) and non-plated holes.
  - Board plane: XY, YZ or ZX plane of any coordinate system; one origin for all files (coordinate system
    origin, lower left corner of the board outline or a point); mm or inch; nothing is mirrored.
  - Copper layers are found from the elevations of conductor sheets and thin conductor bodies; the layer
    assignment is suggested per object and can be changed. Bodies are cut at half height, never projected.
  - Overlapping copper is merged, cut-outs are written with clear polarity and islands inside them stay;
    lines and circular arcs are exact, ellipses and splines within an adjustable tolerance.
  - Air region, ports and radiation/PML/symmetry boundaries are never exported. Ambiguous geometry is
    reported with the object names; errors block the export, nothing is written then.
  - Dialog with folder, units, plane, origin, layer table, assignment, preview and a message after the
    export ("Export finished!" with Open Folder, or the reason it failed). Ribbon: Import / Export > Export Gerber.
- `examples/GerberTestBoard.hzn`: two-layer test board with copper shapes, cut-out with island, via and holes.

### Changed
- The exports are grouped in **File > Export** (Geometry (STEP), Touchstone, EMerge Python Script, Gerber).
- `CHANGELOG.md` (this file); the GitHub release notes are taken from it.

## 0.1.19 (2026-10-22)
- The solver process ends with TerminateProcess on Windows (the exit still crashed with 0xC0000005 now and
  then); the warm-up counts a finished solve as done.

## 0.1.18 (2026-10-22)
- Setup warm-up also solves a coax with wave ports, so the first pass of a wave port model no longer
  compiles for about a minute after the installation; a hanging warm-up solve is stopped after a time limit
  and logged to `hertzon_warmup.log`.
- Roadmap: openEMS, time domain, low frequency, circuits and photonics.

## 0.1.17 (2026-10-22)
- Alt + left drag rotates the view; navigation table in the tutorial.

## 0.1.16 (2026-10-22)
- Benchmarks: workstation timings of the solver benchmark and its broadband sweeps.

## 0.1.15 (2026-10-21)
- Tutorial screenshots of every model, field plots, S-parameters, patterns, RCS and the MCP dialog.

## 0.1.14 (2026-10-21)
- Step-by-step tutorial (waveguide, coax, dipole, sphere RCS, MCP) with its example projects; the lumped
  port size of a sheet ignores gmsh's bounding box tolerance.

## 0.1.13 (2026-10-21)
- No-warranty notice in the README and the About dialog; all shipped examples listed.

## 0.1.11 / 0.1.12 (2026-10-21)
- EMerge credit and citation texts (Robert Fennis, Zenodo DOI), README badges, the window opens maximized
  without a cut-off edge.

## 0.1.9 (2026-10-21)
- First release archived on Zenodo with a DOI; `CITATION.cff`.

Earlier versions: see the git history.
