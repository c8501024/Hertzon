# Changelog

All notable changes of each release. The release workflow copies the section of the released version into
the GitHub release notes.

## Unreleased

### Added
- **Realized gain per polarization:** RealizedGainTheta, RealizedGainPhi, RealizedGainLHCP and RealizedGainRHCP
  in far-field reports, 3D polar plots and equations (the component gains times accepted / incident power) (#439).
- **Edge-launch connectors at lumped ports.** A library of 131 PCB edge-launch connectors (SMA, 2.92 mm,
  2.4 mm) and **Connectors › Real Connector** (or right-click a lumped port › *Simulate with Real Connector*):
  the connector stands at a lumped port at the board edge, found from the port sheet (edge, direction, board
  thickness, trace width, substrate), and brings a grounded coplanar launch footprint after Southwest Microwave's
  50 GHz design rules (narrowed pad and taper, via fence, all dimensions variables). Three modes, switched at any
  time without losing anything: **Ideal port** (the lumped port is solved, the connector is a transparent
  preview), **Transition** (a small 3D design of the connector-to-board transition, cascaded with the lumped-port
  solution in a generated circuit design) and **Full 3D** (connector and footprint solved, wave port on the coax,
  air region and Auto Open adjusted and restored). **Flatten** makes it plain geometry; **Optimize Launch** tunes
  the footprint in the small transition design. MCP tools `list_edge_connectors`, `attach_edge_connector`,
  `set_connector_mode`, `get_connectors`. See [docs/CONNECTORS.md](docs/CONNECTORS.md).
- Materials Ultem 1000 (support beads of precision connectors) and Kel-F (PCTFE).
- A Microstrip Line of a circuit may have a negative length: a de-embedding line.
- **Footprints of circuit parts and the board in 3D.** Parts carry a KiCad footprint and symbol (`.kicad_mod`,
  `.kicad_sym`, also the downloads of SamacSys, SnapEDA, Ultra Librarian) and the assignment of pads to the
  terminals of their model, shown to scale in the Component dialog. Create 3D Layout puts R, L and C on their
  pads and transistors and ICs on their footprints with one port per pad; **Board Circuit** puts the solved
  layout back into a schematic with every device pin on its pad port. KiCad library folders in Tools › Options.
  **Board Layout** places the parts in 2D before the 3D layout: drag, turn, ratsnest, overlap check, tracks.
  The LNA_2G4 example comes with its board placed (0402 parts, the transistor in SC-82FL); Board Circuit adds a
  circuit model of the layout so that the bias point has the DC paths of the board.
- **Subdesigns connected automatically on the board.** 3D design blocks (Wilkinson, rat-race, filters of the
  Design Wizard, any 3D design with edge ports, slanted ports too) are parts of the Board Layout. **Connect** lays
  the missing connections as lines of one impedance round the other copper, with circular or mitered bends and a
  taper where a pin width differs; **Match Phase** gives selected routes the same insertion phase at f0, or a
  set phase difference per route, with meanders. Double-click a bend for its radius or a miter. The routes are
  simulated with the circuit and copied into the 3D layout with the blocks. Example **VNA_TestSet_2G4**: the
  test set of a network analyzer from wizard blocks, connected and phase matched on the board.
- **Nonlinear devices in circuit designs.** Transistors and diodes from SPICE libraries of process design kits
  (subcircuits), model cards or Verilog-A models, DC voltage and current sources, a SPICE Library part. Hertzon
  solves the schematic and reduces all linear parts (lines, junctions, 3D blocks) to one network at the device
  pins, ports and sources. With devices, the S-parameter analysis takes the bias point and the small-signal device
  admittances from ngspice; a new **Harmonic Balance** analysis (single tone, swept drive power) runs circuitRF on
  Hertzon's network. Both programs are optional and installed separately. Checked against ngspice alone
  (IHP SG13G2 NMOS and HBT to 30 GHz, within 1e-5) and an ngspice transient (harmonics within 0.25 %).
- **Full-wave FEM or circuit model per block.** A 3D design block (or Touchstone block) in a circuit uses its
  full-wave data or a circuit model, a circuit design of the project with the same ports (Properties or
  right-click). Where the data do not cover the analysis (harmonics of a harmonic balance included), the block
  switches to its circuit model with a warning; without one the analysis stops with an error instead of using the
  nearest data point. The block's colour shows the model: blue full-wave FEM, red circuit, red dashed switched,
  grey no usable model. A block of a generated 3D layout takes its circuit as the circuit model.
- **Mixer analysis in circuit designs.** A two-tone harmonic balance (LO and RF) by circuitRF on Hertzon's
  network: conversion loss, LO-RF, LO-IF and RF-IF isolation over an RF band and a list of LO powers, results in
  `mixer.csv`. Diode model cards of SPICE libraries now run in harmonic balance. New example
  `examples/make_mixer.py`: a broadband double-balanced diode mixer (about 1.25 to 4 GHz) with two stripline
  Marchand baluns as a 3D design and the HSMS-2829 ring quad; checked against an ngspice transient analysis.
- Units V, mV, A, mA, uA, kohm in expressions.
- **Example LNA_2G4**: a 2.4 GHz amplifier that can be built (onsemi NSVF4009SG4 on RO4350B, 0402 parts, 5 V):
  13 dB gain, S11 and S22 below −14 dB, unconditionally stable, output P1dB 6 dBm. Bias and S-parameters by
  ngspice, compression by circuitRF, both on a Verilog-A Gummel-Poon model with onsemi's data-sheet parameters
  (`examples/models/`); a second design uses onsemi's measured transistor S-parameters on the same board.
- Model files of circuit parts (Verilog-A, SPICE libraries, Touchstone) given relative to the project are found
  when an example is opened as an untitled copy; a Verilog-A model in a read-only folder is compiled into the
  temporary folder.
- **macOS:** the mesher library `libhzmesh.dylib` (universal, arm64 and x86_64) is included; `build.sh` builds
  it on macOS and a CI workflow on a macOS runner keeps it up to date. EMerge comes as a prebuilt wheel on
  Apple Silicon, so the install no longer needs Rust there. README section on macOS (#405).

### Changed
- **Filter Design and Antenna Design are gone from the ribbon and the Tools menu.** Everything they offered is in
  **Tools › Design Wizard**: the cards *Rectangular patch*, *Hairpin bandpass* and the new card
  *Hairpin diplexer / triplexer* open the same designers as before.
- **The Desktop tab of a circuit design has the tools of the 3D Desktop tab:** Options, Materials, Design Wizard,
  Help with Feature Overview, Copy/Paste, and an Import / Export group (3D designs from a project file,
  Touchstone). Both tabs are built by one function, so they stay alike.

### Fixed
- Circuit designs: a part with two pins on the same net (for example both cold ends of a balun block on one IF
  node, or a 3D block with two ports tied together) lost one of the two in the network; now both count.
- Harmonic balance with 3D blocks or Touchstone data that start above 0 Hz: DC takes the real part of the lowest
  point (an imaginary part at DC is not physical and kept the balance from converging).
- Properties panel: changing a design variable now updates the Evaluated Value of the variables that use it
  right away, not only after selecting the design again (#430).
- Interpolating sweep on macOS: no more spurious "divide by zero / overflow in matmul" warnings from Apple's
  Accelerate in the reduced-basis step; a reduced solution that is really not finite is rejected and solved
  directly (#405).

## 0.3.22 (2026-11-13)

### Fixed
- **Much smaller initial mesh on large or very thin boards.** Since 0.3.12 the element size in a thin substrate was
  limited to six times its thickness, and the bound meant to keep that in check almost never applied: a user's
  model started with 23,134 instead of 964 tetrahedra and each pass took up to 77 s instead of 4 s; a patch on a
  0.1 mm substrate ran out of memory. The limit now adds at most as many elements as the mesh has without it.
  Patch antenna on 0.5 / 0.25 / 0.1 mm: 24 / 15 / 25 s instead of 26 / 27 s / out of memory, S11 unchanged
  against a fine reference.
  The limit stays because without it a hairpin filter on 0.508 mm stopped after four passes with S21 0.6 dB off.
- A mesh in which other random seeds still leave tetrahedra without volume is repaired with gmsh's optimizer.

## 0.3.21 (2026-11-12)

### Changed
- **EMerge updated to 3.0.6** (`hertzon` branch at `deb5c94`). Brings EMerge's fixes since the last update, among
  them thin conductors (factor 2), the PEC threshold for volumes and wave ports through copper. Wave ports with
  several modes now use EMerge's own `WavePort(number_of_modes=N)`. PARDISO solves symmetric systems as LDL^T
  without scaling and weighted matching (13 to 20 % faster, same S-parameters). Results of 10 reference models
  are unchanged.
- **Ports with Perfect H walls (TM modes) and magnetic materials** are now treated correctly at wave ports (#407).
- **Port modes are computed with the basis functions of the volume** (EMerge 3.0.6). With "Second Order" modal and
  wave ports no longer lose power (on a lossless line |S11|² + |S21|² was about 0.88 at a modal port, and a wave port
  reflected nearly everything); with "Zero Order" the port modes are first order like the volume. The mode solver
  factorizes the port matrix in SuperLU's symmetric mode, which keeps it fast with the new basis.

### Fixed
- **Adaptive meshing no longer runs out of memory in the frequency sweep after the last pass.** The memory check
  before a further pass now also reserves the memory of the sweep on that mesh (about 1.25 times the pass).
- **Message Manager and Progress stay resizable while Derivative Tuning is open.** The tuning panel scrolls instead
  of reserving about 430 px of height, so on short windows it no longer pins the docks below it.

## 0.3.20 (2026-11-12)

### Added
- **Monostatic RCS report: Families.** With Families set to a parametric setup, the Monostatic RCS report (plot and
  table) draws the RCS over the swept variable when every variation is solved at one frequency, for example the
  RCS over the radius of a sphere; other swept variables give one curve each. With several frequencies or over the
  adaptive passes it draws one curve per variation. Export, markers and the Values... filter work as in the other
  family reports.

## 0.3.19 (2026-11-11)

### Added
- **Plane wave: warning for material on the radiation boundary.** The validation warns when a dielectric or lossy
  object touches the radiation boundary of an incident plane wave with a side face: the boundary injects the
  free-space wave on every face it covers, so the wave would also enter from the sides. Use an air region, or
  periodic / Perfect E / Perfect H side walls for an infinite wall. Entry and exit faces (normal along k) are fine.

### Fixed
- **Materials: storing or editing a material no longer hangs for about 22 s.** The material list fitted its column
  widths on every cell while it was refilled; it now fits them once (milliseconds). EMerge's material library is
  cached in `~/.hertzon/cache`, and repeated clicks no longer open further materials dialogs.
- **Keyboard Shortcuts dialog: Enter confirms, Esc cancels**, also while a capture field has focus; with a
  modifier (e.g. Ctrl+Return) they are still recorded as shortcuts.
- **`hertzon-cli solve` stops with a clear message for incident plane wave designs** (no ports, no S-parameters)
  instead of solving and then reporting a missing solution.
- **3D view: coordinate system and plane wave arrows line up with their labels again.** With a solid at the
  origin of the working coordinate system the axis triad and the k / Eo arrows were cut or hidden by the solid,
  while the X/Y/Z, k and Eo labels (always drawn) floated on its faces. Triad and plane wave arrows are now
  drawn over the model, so each label sits at the tip of its visible axis.

## 0.3.18 (2026-11-11)

### Added
- **Design Wizard: UWB monopoles.** A printed **disc monopole** and a **rectangular monopole** with bevelled
  lower corners, each fed either by a **microstrip with a partial ground** on the bottom layer or by a
  **coplanar line** with the ground on the same layer (a new choice field *Feed*). Start values from the lowest
  frequency of a planar monopole; the optimization tunes the radiator, the gap and the ground length on the sweep
  of the whole band (S11 ≤ −10 dB from 3.1 to 10.6 GHz by default). Also over MCP (`create_structure` with
  `{"feed": "coplanar"}`). See [Design Wizard](docs/WIZARD.md).
- **Command line without the window** (`hertzon-cli`, also `python -m hertzon solve ...`): `describe` prints the
  designs, ports (number, kind, reference impedance), variables (expression and SI value) and setups/sweeps of a
  project as JSON; `solve` solves a design and writes its S-parameters as Touchstone (the same file as Export
  Touchstone in the window), with `--set VAR=VALUE` for variables, `--freq START:STOP:COUNT` or `--points` for
  the frequencies (Hz unless a unit is given), progress as text or JSON lines on stderr, a JSON summary and
  stable exit codes. The project file is not changed: other values are solved on a copy in
  `<project>.hznresults/<design>/CLI/`, and a call whose model and setup match an existing solution (of the
  window or of an earlier call) is answered from it without solving. See [docs/CLI.md](docs/CLI.md).

### Changed
- **Less reserved memory on PCs with many cores:** numpy, scipy and gmsh each bring their own OpenBLAS, which
  reserves a work buffer for every CPU thread when it is loaded. The window and the MCP processes now load them
  with two threads (the solver processes keep all cores). On a laptop with 16 logical CPUs an empty project
  had 1.8 GB committed memory (Task Manager, Details, "Commit size"), about 1.2 GB of it these buffers.
  `HERTZON_BLAS_THREADS=0` switches it off; an `OPENBLAS_NUM_THREADS` set by the user is kept.

## 0.3.17 (2026-11-11)

### Changed
- **Less space in the user profile on Windows** (#377): the setup keeps pip's download cache next to `.venv`
  in the installation folder instead of `%LOCALAPPDATA%\pip\Cache` (several GB with Qt, VTK and the solver
  packages) and deletes it when it is done. The installer's folder page now says that the Python environment
  is installed into the chosen folder, so a folder on another drive keeps the profile small.
- **Wave port mode below cutoff at the solution frequency:** since EMerge `767fb7b` such a mode decays at the
  port instead of stopping the solve with "the port face carries only N modes". The log now warns about it
  ("mode 2 is below cutoff at the adaptive frequency ... and decays at the port"), and the test checks the new
  behaviour (WR-90 at 10 GHz: TE20 with alpha = sqrt(kc² − k0²), no coupling, TE10 passes).

## 0.3.16 (2026-11-10)

### Fixed
- **Wave ports with several modes** (EMerge `767fb7b`, FennisRobert/EMerge#97): in an overmoded guide the mode
  analysis returned high order modes instead of the dominant ones (WR-90 at 40 GHz with 2 modes: two modes with
  kc 550 and 618 rad/m instead of TE10 and TE20), whose sign then flipped between adaptive passes (Delta S 2).
  A sweep below the cutoff of the second mode stopped with "float division by zero"; that mode now decays at the
  port (S = exp(-alpha L)). A repeated mode analysis no longer searches exactly on the first mode, which left the
  other modes unconverged.
- **Interpolating sweep of wave ports with several modes:** a sweep did not converge when a port had two modes
  (WR-90, 100 mm, TE10 + TE20, 8–40 GHz: interpolation error about 1 after 120 solutions). Before every batch of
  solutions the sweep flipped port signs to match the nearest solved frequency, judged by the coupling to port 1:
  for the second mode that coupling is mesh noise, and on a long line the phase also turns by more than 90°
  between solutions, so modes were flipped at random. The solver's own sign rule for port modes is kept instead.
  The entries of a mode cut off at the lower band edge are fitted in sqrt(f² − fc²), where their phase is a plain
  delay (the TE20 phase just above its cutoff was off by up to 0.09 with 60 solutions), and start frequencies keep
  1 % from a cutoff, where the propagation constant is zero. The example converges with 74 solutions, |S| within
  0.005 of 1.

## 0.3.15 (2026-11-10)

### Fixed
- **Broadband / Multi-Frequencies adaptive meshing:** every adaptive frequency now refines its own worst
  tetrahedra. The error of each frequency is scaled to its own maximum before the largest per tetrahedron is
  refined. Before, the frequency with the larger error everywhere (in a plain WR-90 the highest one) chose all
  tetrahedra, and Broadband 8–12 GHz gave exactly the mesh of a single solution at 12 GHz. Single-frequency setups
  are unchanged.

### Added
- **Broadband adapts inside the band:** at low, middle and high frequency by default (setting "Adaptive frequencies
  in the band", evenly spaced, at least 2) instead of only at the two edges. It converges when the mean Delta S of
  these frequencies is below Max Delta S.
- **Multi-Frequencies with their own Max Delta S:** an entry may carry its own limit after ':'
  (`8GHz:0.001, 12GHz:0.0001`); entries without one use Max Delta S. The analysis converges when every frequency
  has reached its limit. The convergence data stores Delta S per frequency.

### Changed
- **Example 2 of the Meta benchmark:** Run 4 in `examples/meta_glasses_ex2/run4`, a refinement of Run 3 with a
  grounded parasitic next to the feed stem that adds a resonance near 7.2 GHz. Reference solver on Meta's STEP:
  68.7 % at 2.4 GHz, 71.7 % in 5.17–7.125 GHz, 70.2 % with equal weight (Run 3: 68.6 %), 61.8 % instead of 44.3 %
  at 7.125 GHz. The overview now opens with Run 4 and shows how Runs 1 to 4 got there.

## 0.3.14 (2026-11-09)

### Fixed
- **Antenna goals on a frequency sweep:** an optimization with goals on antenna parameters (e.g. total efficiency
  `RadiatedPower/IncidentPower`, `RadiationEfficiency`) over a discrete sweep with Save fields failed in every
  evaluation ("antenna parameters need the fields of 'Sweep'"): the evaluation copy switched Save fields off in
  all sweeps, the goal sweep included. The goal sweep now keeps its fields; they are removed after the cost is
  known, except for the best evaluation. Before, antenna goals only worked on LastAdaptive.
- **Wave ports below the cutoff of their mode:** an interpolating sweep across the cutoff frequency of a wave port
  mode is now fitted separately below and above it (S has a square-root branch point there that no rational fit
  follows; across the WR-90 cutoff the sweep was up to 3 dB off below it and +0.05 dB above unity just above it,
  now within 0.01 dB of the solved points). The log names the cutoff. An adaptive frequency below the cutoff of a
  wave port stops with a message that gives the cutoff frequency (instead of "no modes"). De-embedding below
  cutoff uses the decaying port mode (exp(alpha d)), and the fit of the propagation constant over frequency keeps
  its cutoff term.
- **Wave ports below cutoff give the decaying mode** (EMerge `a92bf13`, FennisRobert/EMerge#96): below the cutoff
  of its mode a wave port sometimes absorbed a growing instead of a decaying mode (WR-90, 40 mm, 5 GHz: S21
  −105.7 dB instead of −30.9 dB); now S21 = exp(−αL), also in filled guides. An adaptive frequency below the
  cutoff no longer stops with "no modes": the port takes its mode shape from a higher frequency and warns.

### Changed
- **Hairpin diplexer layout**: the two filters are stacked as in most published hairpin diplexers: the common port
  at the left edge, the T-junction right behind it, channel A above and channel B below, Ports 2 and 3 at the right
  edge. Each junction line goes up or down to its row and turns towards it (both bends mitered; the circuit has the
  same line, mitered bend and line); the rows keep a fixed distance and hang towards the middle when the lines are
  long enough. The board of the default diplexer is about 40 % smaller (35 x 46 mm instead of 70 x 38 mm). In the
  triplexer, the filters of A and B hang below the line of the ports, so the empty half above it is gone.
- **Design Wizard rat-race coupler looks like a real board:** the four feeds leave the ring radially, 60 degrees
  apart (port 2 left, ports 1 and 3 below, port 4 right, the three-quarter-wave arc over the top), and each ends
  on a board edge square to it, where the connector goes. The board is cut to fit the feeds (chamfers at ports 1
  and 3). With the default specification (2.45 GHz) the 3D model before any optimization gives S11 −30 dB, isolation −35 dB and −3.2 dB to ports 2 and 3, in phase within 2 degrees.

### Fixed
- **Update from a Hertzon window that does not close:** when the Hertzon window from which Install was clicked
  was still open after two minutes (for example because a second window asked to save and the save was
  cancelled), the setup did not notice that window, since it had started the installer, and updated the
  Python packages under it. The setup now lists that window and closes it after asking, like any other open
  Hertzon window.
- **Update of an installation in `%LOCALAPPDATA%\Hertzon`:** Install downloads the update into the Updates folder,
  which then lies inside the installation, and the installer stopped itself together with the background
  processes that use the installation, so the update ended without replacing anything. The installer now
  leaves itself out (also when it is started from inside the installation folder by hand).

## 0.3.13 (2026-11-09)

### Added
- **Hairpin diplexer and triplexer**: Tools › Filter Design › Hairpin Diplexer (and MCP
  `create_hairpin_diplexer`) builds hairpin filters for two or three channel specifications: the 3D design, the
  same diplexer as a circuit and the space mapping optimization *DiplexerSM* on all channel masks and the
  isolation of every pair of ports. Junctions: a T-junction (the lines start where each filter looks open in the
  band of the other channel), a common resonator shared by both filters (about half the board), or for three
  channels a star junction with the third filter turned downwards.
- **MCP: port and power-balance checks in the result hints.** The efficiency split reports the power balance
  (Prad + Ploss − Pacc) and warns above 10 % of the incident power; the convergence block warns when Delta S rises
  again instead of settling (as with the lumped-port voltage samples fixed in 0.3.11). The server instructions
  say to check the port model first when S11 jumps between passes or the power balance is off.

### Changed
- **Less memory after start:** the formula editor of the Fields Calculator (a web view, which runs a browser in
  processes of its own) starts when the Equations tab is first opened, not with Hertzon. An empty 3D project
  now takes about 0.9 GB instead of about 1.4 GB (measured on Linux, all Hertzon processes together); opening
  the Equations tab the first time takes a moment longer.
- **Design Wizard pictures:** the structure pictures on the cards are redrawn from common building blocks, so
  lines join cleanly. The rat-race ports sit on the ring, and the branchline, coupled-line, Lange, T-junction,
  edge-coupled and other pictures follow the real layout.
- **Mesh of thin substrates:** the elements in a thin dielectric layer are at most six times as wide as the
  layer is thick. The wavelength rule alone made them up to twenty times wider than a 0.5 mm board: a third of
  the tetrahedra were flat, and a few had no volume at all, which stopped the analysis with "NaN detected in the
  a-posteriori error estimate". The initial mesh grows by about 7 % on a microstrip filter; a patch antenna now
  converges instead of running out of passes.
- A mesh that still has tetrahedra without volume (now rare, e.g. at the faces of the air box) is meshed again
  with other seeds, as before.
- **Example 2 of the Meta benchmark:** examples/meta_glasses_ex2 now opens with the full report of Run 3 (with
  S11 and a layout plot in mm). The faulty Run 2 moved to `run2/` and is summed up in a short note: it was made with
  Hertzon versions that still had bugs in the solver and in the MCP interface.

### Fixed
- **Install Update on Windows did nothing:** Hertzon closed and the installer never came up. The hidden
  PowerShell that was to start the installer once Hertzon had closed was started without a console, and on some
  Windows versions Windows PowerShell then ends at once, without running anything and without an error. Hertzon
  now starts the installer itself; the installer waits until Hertzon has closed and then opens. Each step is
  written to `update.log` in `%LOCALAPPDATA%\Hertzon\Updates`, and when the installer cannot be started or ends
  right away, Hertzon stays open and says so. The installer also starts PowerShell (for the Python environment)
  by its full path. Computers on which Install did nothing install this version once from the release page.
- **Linux: Hertzon did not start from the application menu** when system libraries for Qt's X11 platform plugin
  (e.g. `libxcb-cursor0`) were missing: Qt stopped with "no Qt platform plugin could be initialized" and the menu
  entry showed nothing. The installer now checks the libraries Qt and gmsh are linked against, prints the install
  command for apt, dnf, pacman or zypper and offers to run it; the menu entry shows a message with that command
  instead of failing silently and writes the output to `~/.local/share/hertzon/hertzon.log`. The Linux
  installer is in English.

## 0.3.12 (2026-11-09)

### Fixed
- **Installer: temporary GitHub server errors**: when a package download failed with a server error (such as
  504 Gateway Time-out for the EMerge wheel), the setup stopped at once. It now tries the installation again up
  to three times with a pause. pip downloaded the EMerge wheel on every update even when exactly that wheel was
  installed; an update now downloads EMerge only when it is missing or a different one is required, and
  otherwise installs only Hertzon.

## 0.3.11 (2026-11-09)

### Added
- **Design Wizard** (Tools › Design Wizard, the ribbon and the start page): one window for couplers, dividers,
  matching networks, filters and antennas. Choose a structure from cards by category, enter the specification
  and the substrate, and see the synthesized start values while typing. It creates the circuit, its 3D design
  with the same variables and an optimization on the circuit with multi-fidelity switched on. First structures:
  the **quarter-wave transformer** (1 to 4 binomial sections), the **branchline coupler** (3 dB or unequal), the **rat-race coupler**, the
  **coupled-line coupler**, the **Wilkinson divider**, the **T-junction divider** (equal or unequal), **stub matching** of a complex load, the **stepped-impedance
  lowpass** (Chebyshev), the **shorted-stub bandpass** (wideband, quarter-wave stubs with vias), the
  **edge-coupled bandpass** (parallel coupled half-wave resonators), the **open-stub bandstop** and the first
  antennas, the **dipole**, the **monopole**, the printed **inverted-F antenna**, the probe-fed **circular patch**
  and the **circularly polarized patch** with truncated corners (a 3D design tuned on its adaptive solution, no
  circuit).
  The patch and hairpin designers open from their cards; the planned structures are shown greyed out. MCP:
  `list_structures`, `create_structure`. See [Design Wizard](docs/WIZARD.md).
- **MCP: diagnostics that keep an AI from wrong conclusions.** `get_results` now splits total efficiency into
  mismatch efficiency (Pacc/Pinc) and radiation efficiency (Prad/Pacc) and names the limiting factor (new
  `kind="efficiency"`: the split at every far-field frequency, with Q_Z). New `kind="impedance"`: R, X, Q_Z
  (Yaghjian–Best) and the resonances (X = 0) of a port over a sweep. The summary lists the port resonances, hints
  for strongly reflecting ranges (e.g. high Q without a resonance: likely no radiating mode, matching cannot fix
  it) and a clear warning when the adaptive mesh did not reach its Delta S target. The server instructions gain
  an *Interpreting results* section: when a dip may be called a mismatch, how to separate loss from radiation
  (lossless counter test), check convergence first, and call a cause confirmed only after a test that could have
  refuted it.

### Changed
- A mesh with flat (zero-volume) tetrahedra is meshed again with another seed of the mesher: such slivers made
  the solution void and stopped the analysis with "NaN detected in the a-posteriori error estimate".
- Circuit: a free end of a coupled microstrip line (a pin on no other part) now carries the open end capacitance,
  as its layout does; coupled-line filters agree with the 3D design within about 1 %.

### Fixed
- **Lumped ports: S-parameters jumped from pass to pass and the accepted power was too high.** Samples of the
  port voltage line that fell outside the tetrahedra next to the port counted as zero field, so the port voltage
  dropped by their share (about 5 to 15 %) in some passes. The radiated power stayed right, the power balance got
  worse and Delta S did not settle at high Q. Those samples are now looked up in the whole mesh. The voltage is
  now integrated along the port's Integration Line; before, only its direction and length were used and the line
  ran along the edge of the sheet.

## 0.3.10 (2026-11-08)

### Added
- **Multi-fidelity optimization of circuits**: the optimization of a circuit design can correct itself with
  the circuit's 3D layout (Optimizer tab: *Multi-fidelity: correct with the 3D layout*). The circuit is the
  fast model, the layout the exact one: the goals are optimized on the circuit, then each round solves the
  layout once in 3D and corrects the variables by space mapping, as for the hairpin filter. The layout uses the
  circuit's variables under their names, so nothing has to be mapped; Apply Best Variables sets the circuit and
  the layout. See [Circuit designs](docs/CIRCUIT.md#multi-fidelity-optimize-a-circuit-and-correct-it-with-its-3d-layout).
- **Hairpin filter assistant: what the specification allows** (Tools › Filter Design › Tapped Hairpin): a
  *Response* tab shows, live while the values change, the ideal Chebyshev filter of N resonators with the loss of
  the board (unloaded Q of the resonator line), the same for N − 1 and N + 1, the circuit of the start design and
  the filter mask. A table lists for every N the rejection in the stopbands, the insertion loss in the passband
  and where S21 falls below the rejection goal; **Suggest N** picks the smallest N that meets the goals (or says
  which goal the board cannot meet) and enlarges the region and board where the hairpins need room.
- **Create and Optimize**: creates the filter with its circuit model and starts the space mapping optimization
  *HairpinSM* right away (the circuit is optimized in seconds, a few 3D solves correct it).
- **Patch antenna assistant: Create and Optimize** (Tools › Antenna Design › Rectangular Patch): creates the patch
  and starts its optimization of patch length and inset (*PatchOpt*) right away, instead of only setting it up.

### Changed
- **Space Mapping** fits the circuit to all S-parameters (any number of ports) instead of S11 and S21, and goes
  back with half the step when a 3D solve is worse than the best one.
- **The circuit of the hairpin filter looks like the filter**: the U of every hairpin, the coupled arms side by
  side, the feeds at the taps, joined by wires instead of net labels; short lines get named lengths (`lv`, `lxi`,
  `lo1` ...). The netlist and the S-parameters are unchanged.
- Schematic: the text of an upright, mirrored part is drawn on its left side.

### Fixed
- **Install Update on Windows**: on some computers the update stopped with "The installer could not be started:
  [WinError 2]". PowerShell, which starts the installer once Hertzon has closed, is now started by its full
  path instead of through PATH; without PowerShell the installer is started directly. Computers on 0.3.5 or
  older install 0.3.10 once from the release page.

## 0.3.9 (2026-11-08)

### Added
- **Optimization of circuit designs**: the optimizers (Conjugate Gradient, Latin Hypercube + Nelder-Mead) now
  work on a schematic too (**Simulation › Optimization**, or the *Optimization* group of the Project Manager).
  Every evaluation is a circuit analysis in the optimizer process, so thousands of designs take seconds; Apply
  Best Variables analyzes the circuit again. A circuit optimized this way is a fast start design for its 3D
  layout. See [Circuit designs](docs/CIRCUIT.md#optimization).
- **Space Mapping optimizer**: a 3D design is optimized with a circuit design of the same variables as its fast
  coarse model. The goals are optimized on the circuit, then each round solves the 3D design once, fits the
  circuit to the solved response and corrects the 3D variables (aggressive space mapping, Broyden update).
- **Hairpin filter as a circuit**: Tools › Filter Design › Tapped Hairpin (and MCP `create_hairpin_filter`)
  also creates the filter as a circuit (coupled lines, taps, bends; within about 1 % of the 3D response) and the
  optimization *HairpinSM*. On the third-order example it reached the circuit optimum in three 3D solves, about
  13 minutes on 4 cores, instead of 4.5 hours with the Derivative Trust Region.
- **Hairpin filter: one gap per pair of neighbours** (g in the middle, g2, g3 ... outwards, symmetric), each
  from its own coupling of the Chebyshev prototype and included in the optimizations. With one common gap,
  four or more resonators could not reach a Chebyshev response; the fifth-order circuit now reaches a return
  loss of 18 dB in the passband.

### Changed
- **Faster circuit analysis**: expressions are parsed once and the line and junction models are cached; the
  hairpin circuit takes 30 ms instead of 95 ms per analysis.
- **Hertzon is now the "Electromagnetic Design Suite"**: new subtitle on the splash screen, the website, the
  README, the About dialog and the package description. It names what Hertzon has grown into besides the 3D
  full-wave FEM solver: circuits with their 3D layout, PCB import, optimization and AI agents through MCP.
- **Co-design with AI**: the website and the README now lead with the vision of co-design by engineer and AI
  agent; the new page [What is MCP?](docs/MCP.md) explains MCP and how Hertzon offers its methods as tools.

### Fixed
- **S-parameters of wide lumped ports** (#231): the port voltage was integrated on a single line at a corner
  of the port sheet (EMerge starts its lines at the edge nodes with exactly the lowest coordinate, which with
  rounding is often one node). The voltage is now averaged over lines spread evenly across the width of the
  port. On the UWB dipole of #231 (a 12.8 x 3 mm port) S11 changes by up to 2 dB and the radiated power now
  matches the accepted power within 0.5 % over the whole sweep (it was up to 1.7 % above).
- **Discrete sweeps ran out of memory on large meshes**: the system matrices of every frequency of the sweep
  were assembled before the first was solved. A 71-point sweep of a UWB dipole with 250,000 unknowns needed
  14 GB and was killed; it is now assembled in batches of the frequencies solved at a time (6 GB peak, same
  results).

## 0.3.8 (2026-11-08)

### Fixed
- **Loss power and power balance of antennas with copper solids**: the tetrahedra of conductor solids
  (conductivity above 1e4 S/m) were counted as lossy volume. EMerge solves no field inside them (it puts a
  surface impedance on their faces), so sigma |E|^2 of the interpolated surface field gave a loss power of up
  to hundreds of times the input power (Meta benchmark Example 2: Power Balance +150 to +1200 %; the FR4 patch
  with a copper ground: Ploss = 171 W for 1 W incident, now 0.46 W with a balance of -1.3 %). Their loss is
  that of the surface impedance on their faces, which was already counted.
- **Multi-Frequencies setups accept ';'**: the help said the frequencies are separated by ';', but only ','
  worked ("Invalid expression '2.45GHz;6.1GHz'"). Both work now, and the Validation Check names a bad entry.
- **Conductor loss of sheets, radiation efficiency and gain**: Finite Conductivity sheets were one-sided by
  default: one surface impedance carried the current of both sides of the sheet, about twice the conductor loss
  of a strip in air. They are two-sided now by default (like a sheet inside the model in common FEM solvers), also without
  "Use thickness"; a face on the outer boundary stays one-sided. The loss power of a two-sided sheet counted one
  side only (half its loss), so the radiation efficiency, GainTotal and the power balance were too optimistic;
  both sides count now. Lossy strip dipole (2e4 S/m, 1 GHz): one-sided efficiency 86.0 %, two-sided 91.9 %, a
  0.2 mm conductor solid 92.2 %; the balance of the two-sided sheet went from -4.3 % to -0.3 %. Projects
  saved with "Two-sided" off keep it, and the Validation Check warns about it; solutions of sheets with
  "Two-sided" on and no thickness are outdated (they were solved one-sided).
- **STEP export was 1000x too small**: coordinates were written in metres into a file that states millimetres
  (a 147 mm model ended at 0.147 mm). GUI and MCP export write millimetres now.

### Added
- **Validation Check: objects that almost touch**: parallel faces of two objects a few micrometres apart (a gap
  or an overlap smaller than 20 um, or 0.1 % of the model size) and a straight edge or plane face that touches a
  cylinder tangentially (a via exactly as wide as the trace that ends on it) are named as warnings, with the
  distance. gmsh can fail on the first ("Unknown model face with tag N", "Invalid boundary mesh"), the second
  can give "NaN detected in the a-posteriori error estimate". When a solve fails, the error message names
  them as the possible cause. Thin solids (copper foil on a substrate) are not reported.

## 0.3.7 (2026-11-08)

### Fixed
- **Meshing no longer fails with "index N is out of bounds"** on imported STEP bodies with slots or pockets:
  after a boolean the geometry kernel keeps edges and points inside a face, gmsh meshes them as embedded
  entities, and their nodes were missing from the per-face node list. The face matching before each mesh and
  the display tessellation now look the nodes up in the node table of the whole mesh.

### Added
- **Example: Meta AI-glasses benchmark, Example 2** (dual-branch IFA) in examples/meta_glasses_ex2, set up and
  solved via MCP, with project, results and report.

## 0.3.6 (2026-11-08)

### Fixed
- **Install Update closed Hertzon and then nothing happened**: the installer is started by a small script
  without a window after Hertzon has closed. When the downloaded installer was gone by then (virus protection
  may remove a freshly downloaded program, cleaning tools empty the temporary folder) or could not be
  started, nothing was shown. Hertzon now downloads to %LOCALAPPDATA%\Hertzon\Updates instead of the
  temporary folder, checks after the download that the installer is still there (if not, Hertzon stays open
  and offers the release page), and the script checks the file again, writes update.log next to the
  installer and shows a message with the path and the release page when the installer cannot be started.

## 0.3.5 (2026-11-08)

### Fixed
- **MCP calls no longer hang until the client gives up**: in the Meta benchmark (Example 2) three calls got no
  answer for 30 min each, until Claude Code aborted them, and the server answered the next call at once.
  Everything the server and its children print (gmsh, EMerge, progress lines, tracebacks) used to go into the
  client's stderr pipe; a client that does not read that pipe lets it fill up, and the next write blocks the
  server. It now goes to a log file, ~/.hertzon/logs/mcp-<date>-<pid>.log (the last 20 are kept,
  HERTZON_MCP_LOG sets another path, '-' the old behaviour). The standard input of the server is the null
  device, so a child started by a tool can no longer read or wait on the protocol stream, and on Windows the
  pipe is read only when data is there (PeekNamedPipe): a waiting read held every DLL load (gmsh, scipy,
  EMerge) and child start in the server until the client wrote again. Answers with NaN or
  infinite values (written as NaN/Infinity, which is not JSON, so the client dropped them) carry null instead.
- **MCP log**: each request is logged with its duration, and a request that runs longer than 5 min writes the
  stacks of all threads to the log (again every 5 min; HERTZON_MCP_WATCHDOG sets the time), so a call that
  does not come back shows where it waits.
- **MCP project tools are 12 s faster per call** on a machine with stale link files: link files of Hertzon
  windows that are gone (left by a crash) are removed, and the remaining windows are asked at the same time
  instead of one after another with 2 s each.
- **Simulations start as fast as before 0.3.4**: the check for sheets that run through conductors no longer
  runs before every simulation. It built the whole model a second time and cut every sheet with every solid
  (on a board with 85 vias 26 s against 2 s for the model itself), before each run and each optimization
  step. It now runs only when meshing or solving fails, to name the sheets in the error message; the
  Validation Check shows the same warnings as before.
- **MCP**: the geometry process for render_image, validate, get_geometry and run_simulation starts with the
  server and loads gmsh and Hertzon right away, so the first call no longer waits for it
  (HERTZON_NO_PRELOAD=1 turns it off).
- **MCP import_geometry** reads the STEP, IGES, BREP or STL file in the geometry process, with a time limit
  (timeout_s), progress and cancel like render_image. A large file no longer holds the whole server without
  an answer.
- **STEP and IGES imports are read from a cache**: the file is translated once and kept as BREP
  (~/.hertzon/cad_cache). Every later build reads the cache, whether in the window, over MCP, or for each
  simulation and optimization step. On an assembly with 800 bodies that took 0.4 s instead of 13 s. A changed
  file is translated again.

## 0.3.4 (2026-11-07)

### Added
- **Equation Based Curve** (Draw > Equation Based Curve..., and in the 1D group of the Draw ribbon): a curve
  from X(t), Y(t), Z(t) with t running from a start to an end value in a given number of points. The
  functions may use design variables and units (plain numbers are model units), so the curve follows
  parametric sweeps and optimizations. A smooth spline or straight lines through the points; a closed curve
  can be covered into a sheet (e.g. spiral or exponentially tapered outlines).
- **Equation Based Curve dialog**: its own window with presets (circle, arc, ellipse, helix, Archimedean
  spiral, exponential taper, sine meander), Cartesian, Cylindrical (r, phi, z) and Y = f(X) coordinates, and a
  live preview with start, end, length and number of points while typing. An error names the value of t where
  it happens and marks the field. The parameter is t; _t (as in other tools) works too.
- The Equation Based Curve dialog has the same **Constants & Functions...** button as Design Properties: a
  double-click inserts a constant, function, unit or variable at the cursor of the last edited field.
- **Hairpin filter with the Derivative Trust Region**: Tools > Filter Design (tapped hairpin) and the hairpin
  example add a second optimization, *HairpinDTR*, with the same goals as the conjugate gradient one. It
  varies L, g and yP (centre frequency, coupling, match); w, b and M can be included. All six variables
  morph without remeshing. On the initial mesh the tuned S-parameters stay within 3e-4 of exact solves for
  L ±1 mm, within 2e-3 for w, b, yP and the gap ±0.05 mm, and within 0.05 (about 0.5 dB) for the gap
  ±0.15 mm. A run of HairpinDTR from the example's start values on a 32-core machine lowered the cost from 6.83
  to 0.64 in 10 rounds (5 h 36 min, about 33 min per round, up to 37 GB of memory). The solves confirmed the
  model in rounds 1 to 8 (actual / predicted improvement 0.44 to 1.14). After that, the change of the cost
  from one adaptive mesh to the next (about 5 % for the same design) was as large as the improvement still
  left, so the last two steps were rejected. The mask was not met with these three variables.
- **Hairpin start values from the filter synthesis**: Filter Design starts the gap and the tap height from the
  Chebyshev prototype (couplings k and external Q for the return loss goal + 3 dB) instead of the middle of
  their ranges. The gap follows from the even and odd mode impedances of the coupled arms (new coupled
  microstrip formulas), the tap from the external Q of a tapped half-wave resonator. For the 2.3 to 2.6 GHz
  example this gives g = 0.38 mm and yP = −5.56 mm; the optimization from the middle of the ranges had ended
  at g = 0.37 mm and yP = −5.37 mm. An option keeps the middle of the ranges (the filterbench start).
- **File dialogs remember their folders**: File > Open no longer starts in the install folder. After the start
  it opens the *Default project folder* (new in Tools > Options > General), later the folder of the project
  opened or saved last. Save As starts in the folder of the current project, an untitled project in the
  folder saved to last. The Touchstone export starts in the folder of the open project file, without one in
  the folder of the last Touchstone export.
- **File > Close** (Ctrl+W): closes the project after asking to save changes; an empty untitled project
  remains.

### Changed
- **Derivative tuning needs much less memory**: every morphed mesh state keeps only what its boundary terms
  need, and the matrix differences are kept in single precision. Hairpin filter, 2 variables: 2.6 GB → 0.16 GB
  with the same tuned response; 6 variables: 5.3 GB (before: more than 14 GB, the process was killed).
- **Derivative Trust Region: a rejected step is not solved twice.** After a rejection the region shrinks to at
  most half of that step, so the next round cannot return the same step. Before, a halved region could still
  contain it, and the same design was solved again (seen on the hairpin filter).
- **A tuning model that does not fit into memory stops at once** with the memory it needs and the number of
  variables that would fit, instead of the solver process being killed half way.

### Fixed
- **Sheets that split conductors are named**: a sheet that runs along the inside of a conductor solid (for
  example a via fence of Perfect E sheets that splits the pec cylinders on it) is now a warning of the
  Validation Check and of MCP `validate`, and the solver log lists it before meshing. Sheets across a conductor
  (an inner copper plane through a via) are normal and not reported. When meshing or solving fails (singular
  matrix, inconsistent mesh, an index error), the error message names these sheets as the probable cause.
- **CPU load while idle (again)**: 0.3.2 and 0.3.3 still redrew the empty 3D view five times a second. The scale bar and
  the axes triad adjust themselves at the start of every redraw, and that counted as a change of the scene for
  the next check. The check now compares with the scene as it was drawn, so an idle view is no longer redrawn
  (measured: 0 redraws in 20 s instead of about 100).
- **PCB import dropped a valid copper shape ("Curve loop is not closed")**: a copper area whose outline came
  closer to itself than the geometry kernel's tolerance (a net tie joining antenna and ground plane in one
  shape, a hole on the outline, holes that touch) could not be built, and the whole shape, for example the top
  ground plane, was missing with only a geometry error. Every copper shape is now cleaned on import (repeated
  and collinear points, ring orientation), made valid where needed, and touching rings are repaired with a
  copper bridge of a few µm, so parts that meet in a point stay one conductor. A shape that still cannot be
  built is a warning of the import with its layer and area.
- **MCP: render_image, get_geometry and validate could block the server**: the geometry is now built in a
  separate process with a time limit (`timeout_s`, default 300 s or `HERTZON_MCP_BUILD_TIMEOUT`), progress
  notifications and cancellation. When the limit is reached the build is stopped with a clear message, and
  the server stays usable; pings are answered during a build. `render_image(detail="fast")` leaves out the
  drill holes of a board (the via cylinders fill them), and `import_pcb` takes `arc_segments`.
- **Ratios of lengths in Design Properties**: a variable such as `c = a/b` with two lengths showed a unit
  when the result was below 1 (a = 2mm, b = 6mm gave "333.333mm"). The units of an expression are now
  followed through the calculation: a ratio of lengths is a plain number (0.333333) and, used as a length,
  counts in model units like any plain number; lengths show in mm (areas in mm^2), frequencies with their
  unit, and `c0/f` is a length.

## 0.3.3 (2026-11-07)

### Fixed
- **Update stayed on an empty setup window**: at the start of an update the setup waited silently for Hertzon
  processes to end, with slow process queries without a time limit. It now says at once what it checks, lists
  the Hertzon processes from the installation folder, announces that they will be closed (with a note on
  unsaved work if a Hertzon window is open) and closes them after Enter.

### Added
- **Documentation for circuits and PCB import**: [docs/CIRCUIT.md](docs/CIRCUIT.md) describes circuit designs
  (parts, junctions, line calculator, full-wave line model, 3D designs as blocks, 3D layout and back). The
  tutorial has two new sections: a stub filter as a circuit with its 3D layout, solved, tuned in 3D and copied
  back (new example `examples/StubFilter.hzn`), and a board imported from Gerber files. README, feature
  overview, roadmap and website describe the start page, circuits, PCB import, the Constants & Functions list
  and the newer MCP tools; the tutorial screenshots show the current user interface.
- **AI demos on the website**: the landing page replays three real MCP sessions: Meta's AI-glasses antenna
  benchmark (from `examples/meta_glasses_ex1`), a patch antenna made with the design tool and its optimizer,
  and a dipole drawn from scratch (screen recordings of the Hertzon window), each ending with the time it took
  and the result. A short outlook describes the plan for optimizers on any structure.
- **MCP `set_report(open=true)`** opens the report as a tab in the Hertzon window, so the assistant can show
  the S-parameters or the 3D pattern it has just set up.

## 0.3.2 (2026-11-07)

### Fixed
- **CPU load while idle**: the 3D view (and 3D polar reports) redrew an unchanged scene five times a second,
  which kept the CPU at a few percent with an empty project. It now redraws only when something changed.
- **Project Manager, part library and schematic could not be widened**: the long hint line of the circuit
  editor set a minimum width of about 1250 px for the whole central area, so the docks on the left (and the
  part library next to the schematic) stopped at a limit. The hint now wraps and the panels move freely again.

## 0.3.1 (2026-11-07)

### Fixed
- **Cover Lines after uniting lines with several arcs**: united lines whose curves did not come out of the
  boolean in outline order (for example two arcs and two straight lines) failed with "Curve loop is not
  closed"; the curves are now chained end to end before the sheet is built. Lines that really do not close
  say so. A second closed loop inside the outline becomes a hole, whichever way it was drawn.

## 0.3.0 (2026-11-07)

The new user interface: a start page that asks what to simulate, circuit designs (linear networks) next to the
3D designs with a schematic editor, 3D layouts built from a schematic (3D designs included as blocks), PCB import
from Gerber and Excellon files with the stackup driving the geometry, and AI assistants (MCP) that reach all of it.

### Added
- **AI assistants (MCP) reach the newer features**: the patch antenna and hairpin filter assistants
  (`create_patch_antenna`, `create_hairpin_filter`), the choice of optimizer in `set_optimization` (Conjugate
  Gradient, Latin Hypercube + Nelder-Mead, Derivative Trust Region) and `apply_optimization` (as *Apply to
  Design*), derivative tuning (`compute_tuning`, `get_tuning`), circuit designs (`create_circuit`, `get_circuit`,
  `edit_circuit` with parts, wires, 3D design and Touchstone blocks, `run_simulation` and `get_results` on
  schematics, `render_image` draws the schematic, `circuit_layout` to the 3D layout and back,
  `import_3d_designs`), exports (`export`: Touchstone, STEP, Gerber, EMerge script), `import_pcb`, `set_stackup`,
  `manage_design` (copy, delete) and the examples (`list_examples`, `open_example`). Without a window, the
  tools keep working on the design used last.
- **3D designs in the 3D layout of a schematic**: *Create 3D Layout* and *Update 3D Layout* now build 3D design
  blocks in with their geometry instead of leaving them out. Each block is turned in steps of 90 degrees and
  moved so that the lines of the schematic attach exactly at its ports; the ports a line connects to are
  removed, a port that only a Power Port connects to stays a port of the layout (numbered and terminated like
  that Power Port). Objects, boundaries, mesh operations and design variables come along with the block name as
  prefix (EM1_W), the air region and open boundaries of the block are left out, the board and ground plane reach
  over the block. Works for blocks whose ports are vertical lumped or wave ports on the edge of the design.
- **3D designs from another project file** (circuit, Data › 3D Design › From Project File, or drag a `.hzn`
  file into the schematic): the 3D designs with ports of that project are copied into this one, with their
  project variables, materials and solved results, and placed as blocks.
- **Copy and paste in the schematic**: Ctrl+C / Ctrl+V (Edit menu, ribbon and right-click menu) copy the
  selected parts with their values, the wires between them and the selected wires. The copies land at the mouse
  pointer (else shifted), pasting again steps on, names and port numbers stay unique, and copied lines keep their
  substrate (it comes along into another circuit design). The clipboard works across circuit designs.
- **Loss power, efficiency and power balance of antennas** (#231): the power dissipated in lossy dielectrics,
  magnetic materials, conductors and on finite conductivity, impedance and lumped RLC boundaries (Ploss) is
  computed with the far field data, only for the lossy parts of the mesh. The radiation efficiency is now
  Prad / (Prad + Ploss): it does not depend on 1 − |S11|², which is inaccurate where the antenna is badly
  matched, and is exactly 1 for a lossless antenna. The Antenna Parameters show the Loss Power and the Power
  Balance (Prad + Ploss − Pacc) / Pin, with a warning above 2 %; both are also report quantities
  (`LossPower`, `PowerBalance`). Results solved before keep the efficiency Prad / Pacc.
- **Power balance convergence** (setup, off by default): *Use power balance convergence* with *Max power
  balance* and *Max delta efficiency* makes a pass converge only when the power is conserved and the
  efficiency stable, next to Delta S. The Convergence tab lists and plots both per pass.
- **3D Layout buttons** (circuit, Simulation › 3D Layout): *Create 3D Layout* makes a new layout. *Update 3D
  Layout* (schematic → 3D) rebuilds the layout from the schematic as it is now, with parts added, removed or
  exchanged, and shows it; its setups and reports stay, the variables of removed parts go. *Update Schematic*
  (3D → schematic) copies dimensions changed in the layout back, also trace sizes edited on the objects. A click
  takes the newest layout of the schematic, the drop-down chooses one.
- **Via to Ground** (circuit, Transmission Lines › Other): N round vias of diameter D in a row at the pitch P
  through the substrate to the ground plane. Inductance from the partial inductances of the vias and their images
  in the ground plane, mutual coupling between the vias included; skin-effect resistance of the barrels. The 3D
  layout draws the vias as cylinders just inside the end of the line. Against full-wave solves of a shorted
  18 mm microstrip line with one and with two 0.4 mm vias, the S11 phase agrees within 3 degrees rms
  (1 to 10 GHz).
- **Full-wave line model** (circuit, Transmission Lines › Line Model › *Full Wave*): microstrip lines and stubs
  and coplanar lines take their propagation constant and impedance from a full-wave solve of their cross-section
  (the port mode solver on the substrate and widths of the schematic, metal thickness included, in a shielded
  box) at eight frequencies up to the highest sweep frequency. The length is applied in the circuit, so changing
  a length needs no new solve; each cross-section is solved once (about 10 s) and cached next to the project
  results. The mode solver gives dispersion, impedance (power and current) and the dielectric loss; the conductor
  loss is added from the surface resistance. Junctions, coupled lines and open-end corrections keep their
  closed-form models.
- **Coplanar Line** (Transmission Lines › Other): coplanar waveguide with centre strip W and gaps S, optionally
  with a ground plane under the substrate (*Backed*). Closed form by conformal mapping with the thickness
  correction after Gupta and the dispersion after Frankel, or the full-wave line model. A 3D layout draws only
  its centre strip for now.
- **Import PCB (Gerber, Excellon)**: File > Import PCB (also Modeler, and *Import PCB* in the ribbon) reads
  the manufacturing files of a board: Gerber X2 and older RS-274X (apertures and aperture macros, arcs,
  regions with cut-ins, clear polarity, step and repeat, block apertures, deprecated parts), Excellon drill
  files (tool tables, zero formats, slots), the job file (.gbrjob) for the stackup, and zip archives or whole
  folders. Each file is recognised from its X2 attributes, the job file or the usual KiCad, Altium and Eagle
  file names, and can be changed in the dialog. The copper becomes sheets with finite conductivity (or solid
  copper, as the stackup says) on the copper layer coordinate systems, plated holes become copper cylinders
  between the layers they connect (also blind and buried), non-plated holes and the board outline with its
  cut-outs shape the substrate. A part of the board can be imported (Shift + drag in the preview).
  Everything follows the stackup variables. What is not simulated (solder mask, legend, paste) is not
  loaded and the import report names it. Covered polylines can now have holes (*Holes* property).
- **PCB stackup drives the geometry**: the stackup writes design variables per layer (`stk_<layer>_t`
  thickness, `stk_<layer>_z` bottom, `stk_<layer>_zs` copper sheet height, `stk_total`), a board coordinate
  system `stk_Board` and one coordinate system per copper layer to draw on. *Substrate...* in the stackup
  editor makes one box per dielectric layer and a fill box over each inner copper layer, so no air gap is
  left where the copper is; they live in the group "Stackup" and follow every later change of the stackup.
  Layers with their own εr / tan δ get a project material `stk_<layer>`. The `stk_` variables are shown
  read-only in Design Properties: they are changed in the stackup.
- **Constants & Functions in Design Properties**: a button in the variables dialog opens the list of what a
  value may use: constants (pi, c0, e0, u0), functions (sin, sqrt, if, ...), units and the other variables,
  with value and description and a search field. Double-click (or Insert) puts the entry at the cursor of
  the selected value; a function goes around selected text. The list stays open for further inserts.
- **Eigenmode Parameters report**: Results > Create Eigenmode Parameters Report > Data Table lists the
  eigenfrequencies and Q of an eigenmode solution, one row per mode (exportable as CSV, copy to clipboard).
- **Solve on a computer reached over SSH**: Tools > Options > Remote > Connection *SSH login* with `user@host`
  (or a host of `~/.ssh/config`) and an optional key file. Hertzon logs in, starts its remote service there if
  it does not run yet (installed Hertzon, or the solver image with Docker) and solves through an SSH tunnel:
  no pairing and no open port besides SSH. Pairing in the local network stays as it is. See docs/REMOTE.md.
- **Start page** (File > New and at program start): choose what to simulate, **3D FEM** or **Circuit**, with a
  symbol for each, then the solution type (Modal, Terminal, Eigenmode), the units and the project name. On the
  right: recent projects, the examples and the patch and hairpin templates. **Empty Project** keeps the old
  behaviour; **Show at startup** switches the page off at program start. Project > Insert Design shows the same
  choice, because a project can hold several designs, 3D and circuit designs mixed. The low-frequency solution
  types (electrostatic, DC conduction, magnetostatic, eddy current) and the transient and harmonic balance
  circuit analyses are shown greyed out as "not implemented yet".
- **Microstrip Tee** (Transmission Lines): the T junction of microstrip lines as a part of its own, with widths
  W1 and W2 of the main line and W3 of the branch, after Hammerstad's equivalent circuit (reference plane
  shifts, transformers and the junction susceptance). The lines attach at its edges. A 3D layout draws it as the
  junction area with the lines at its edges. Against a full-wave solve of a stub notch filter the circuit with
  the tee is within 0.4 % (an ideal node: 2 %).
- **Microstrip discontinuities**: Corner, Mitered Bend (50 %), Cross, Step and Gap besides the Tee, on the
  Transmission Lines tab (group Discontinuities), each with its 3D layout. **Automatic** (on by default) finds them
  where microstrip lines meet: two lines at a right angle get a corner, two in line with different widths a step,
  three a tee and four a cross, with the widths of the lines. A diamond marks each one, its tooltip names the model.
  A junction part placed by hand, or Automatic off, overrides it. The 3D layout uses the same junctions.
- **Line calculator** in the dialog of a microstrip line or stripline: Z0 and electrical length at a frequency give
  W and L (an open stub's end extension is subtracted), and the current Z0, effective permittivity and electrical
  length are shown while typing. The tooltip of a line shows the same at the middle of the sweep, and a selected
  part is edited in the Properties panel.
- **Schematic editing**: wires stay attached to a part that is dragged (with an elbow where a wire would become
  slanted; a pin that sat on a wire or on another pin gets a new wire), Escape always ends wire drawing and part
  placing and returns to Select, and Delete removes the selected parts and wires also when the focus is not on
  the schematic.
- **Schematic to 3D layout**: Simulation > *Create 3D Layout* in a circuit design builds a new 3D design from the
  schematic: the substrate with its ground plane, the microstrip traces end to end in the direction they have in
  the schematic (open and shorted stubs, coupled lines, corners and T junctions), R, L and C as Lumped RLC
  sheets in a gap of the trace or from the trace to ground, and the power ports as Lumped Ports on the board
  edge. Every dimension is a design variable named after its part (`MS1_W`, `MS1_L`, `Sub1_H`), with the sweep
  of the circuit. Ideal lines become microstrip of the same impedance and electrical length; parts without a
  layout (Touchstone and 3D blocks, attenuators, coaxial lines) are listed in the Message Manager.
  *Update from Layout* copies the tuned dimensions of such a 3D design back into the schematic.
- **Circuit designs** (linear network): a schematic editor in place of the 3D modeler, with its own ribbon
  (Desktop, Schematic, Lumped Elements, Transmission Lines, Data, Simulation, Results) instead of the 3D ribbon
  and a part library with search next to the schematic (drag a part in, or double-click and click to place; the
  part follows the mouse, R rotates, M mirrors). Parts: power ports (source with internal resistance, port
  number and impedance), ground, net labels; resistor, inductor, capacitor, series and parallel RLC, DC block,
  DC feed, attenuator; ideal lines and open or shorted stubs; microstrip lines and stubs (Hammerstad-Jensen,
  dispersion after Kirschning-Jansen, conductor and dielectric loss, open-end extension), coupled microstrip
  (even and odd mode), stripline and coaxial line, all on a substrate part; Touchstone files. A wire ends on a
  pin or another wire. Values are shown at each part and accept expressions with variables.
  **Drag a 3D design from the Project Manager into the schematic**: it becomes a block with one pin per port and
  a picture of the structure, and its solved S-parameters are used in the circuit (right-click > Update ports
  and picture after a change). Analyze solves the network at once over the sweep of the circuit (1 to 10 GHz by
  default) and writes the results like a 3D design, so the S-parameter reports, Touchstone export and Solution
  Data work the same way.
- **Design type in the project file**: every design stores its type (`"kind": "3d"` or `"circuit"`); files
  without it open as 3D designs.
- **Plot Mesh on the Auto Open region** (Mesh menu, or right-click *Auto Open* in the project tree): the
  mesh on the open boundary of the air box Auto Open builds, drawn see-through. That box is no model object,
  so it could not be picked for Plot Mesh before (#231).
- **Families for far field reports** (#221): Radiation Pattern, Antenna Parameters (plot and table) and the 3D
  Polar Plot offer *Families* like the S-parameter reports: one curve per variation of a parametric setup
  (Values... chooses the variable values shown), exported as drawn. The 3D Polar Plot shows one variation at
  a time, chosen under *Variation*. A new far field report of an unsolved nominal design opens on the
  families of a solved parametric setup.

### Changed
- **Circuit reports**: right-click on *Results* of a schematic offers only what a circuit produces: rectangular
  plot, Smith chart and data table of the port parameters. No field or near-field reports any more.
- **Ideal lines** (circuit, Transmission Lines): TLIN, TLOC and TLSC are in a group *Ideal (TEM)* of their own at
  the end of the page and are named *Ideal Open Stub* / *Ideal Short Stub*: no substrate, no via.
- The remote service no longer answers network discovery when it listens only on the loopback address.
- Solution Data of an eigenmode setup opens on the *Eigenmode Data* tab (eigenfrequencies and Q; a lossless
  mode shows Q as infinite instead of a huge number of either sign).

### Fixed
- **Mesh error after an optimization** ("The stored adaptive mesh does not fit the initial mesh"): the
  optimizer solved its evaluations with other sweeps switched on than the design, so the air box of Auto Open
  had another size and the mesh taken over by *Apply to Design* no longer fit. Evaluations now keep the sweeps
  of the design, and a stored mesh that does not fit restarts the adaptive passes with a warning instead of
  stopping the run. Switching on or lowering a sweep that enlarges the air box marks the setup outdated and
  says so. The patch antenna tool creates its sweep switched off.
- **Seam in the translucent 3D radiation pattern**: with anti-aliasing (SSAA) on, a sharp meridian line showed
  where front and back of the pattern swap order. Translucent surfaces are now blended order-independently.
- **First analysis after a restart or an update no longer waits about a minute at "Starting solver"**: on
  Windows the first solver process read EMerge and its libraries from disk while the virus scanner checked
  them ("import emerge" took about 50 s on a laptop, about 1 s afterwards). Since 0.2.6 the setup no longer
  runs the warm-up after every update, which had loaded them before. Hertzon now loads them once in a
  background process at low priority right after its window opens, so they are ready before the first
  Analyze. `HERTZON_NO_PRELOAD=1` switches it off.
- **RHCP and LHCP were swapped** (#220): the right-hand circular part of the far field (IEEE: the field turns
  from θ to φ with the thumb along the outgoing direction; (θ − jφ)/√2 with the time convention e^{jωt}) was
  shown as LHCP and the left-hand part as RHCP, in the gains, directivities, rE and the Antenna Parameters. The
  axial ratio and the total quantities did not change. The GPS L1 example was mirrored at the XZ plane so that
  it is right-hand circular as its name says (same S11).
- **Circuit and 3D layout agree at shorted ends**: a short stub or a microstrip line to ground now includes
  the inductance of the via that shorts it in the 3D layout (a wall of the line width through the substrate,
  about 0.14 nH for 1.1 mm on 0.508 mm). An ideal short stub is laid out shorter by the length the via adds.
  Against a full-wave solve of a circuit with two shorted stubs, the S11 phase error drops from about 25 to
  9 degrees rms (1 to 10 GHz).
- **Schematic to 3D layout: a line ending at ground was left open**: a microstrip line whose far end is
  connected to ground is now shorted there by a via, like a short stub. Before, the 3D layout had an open end
  where the circuit had a short, so the two results differed completely.
- The marker of an automatic junction is drawn where the wires of the lines meet, not at the pin of one line.
- **Update from within Hertzon no longer hangs on Windows**: the installer now starts only after Hertzon has
  exited, and it stops the Hertzon processes without a window that still use the installation (the MCP
  server of an AI app, the remote service, solver processes) before it replaces the files. A Python started
  through `.venv\Scripts\python.exe` runs as a second process outside the installation folder; it was not
  found and kept files of `.venv` in use, so the setup could not finish until the computer was restarted.
  The setup waits for a closing Hertzon instead of asking at once, stops background processes and repeats
  the package installation only when it fails, and keeps the previous log as `setup.prev.log`.
- **Radiated power above the accepted power** (#231): with Auto Open, the radiated power and the far field of
  an antenna were integrated on the mesh faces of the absorbing boundary, where the mesh is coarsest and the
  boundary reflects a little; lossless antennas showed a radiated power up to ~10 % above the accepted
  power. They are now integrated on a box of Gauss points close around the model (as for incident plane
  waves already). A lossless half-wave dipole: radiated / accepted power 1.016 before, 0.999 now. A Region
  object with a Radiation boundary still integrates on its faces.
- **Field plots of eigenmode solutions were empty**: the eigenmode solve saved no fields. Each mode is now
  stored as solution *LastAdaptive*, one frequency per mode; the field plot dialog lists them as
  "Mode 1: 4.576 GHz", ... and the plot text names the mode. An eigenmode has no amplitude of its own, so its
  values are relative (each mode is scaled to unit norm, phase 0 shows its strongest E field).

## 0.2.13 (2026-11-04)

### Added
- **Optimizer "Latin Hypercube + Nelder-Mead"** (Optimization > Add Optimization > Optimizer): solves a Latin
  hypercube sample of the variable ranges and the start design in parallel, then runs Nelder-Mead chains from
  the best distinct sample points side by side, each with its own evaluation budget. Needs no gradient and finds
  good designs far from the start values (antennas with several resonances, rough costs). Settings on the
  Nelder-Mead tab: sample designs, chains, evaluations per chain, initial simplex step, tolerance, random seed.

### Changed
- **Solver comparison shows the solver start separately**: the time before the first phase (starting the
  solver process) is its own segment in the chart and its own column in the table, and the fastest solver,
  the "Others" times and the comparison with the first solver leave it out. The first run of a benchmark loads
  the libraries from disk (18.3 s against 1.4 s for the second run in a test), which made the first solver
  look 46 % slower instead of 19 %.

### Fixed
- **Interpolating sweep**: the rational model of the solved frequencies now uses the fewest pole pairs that
  reproduce them, instead of a pole count from the number of samples. Smooth responses (waveguides, lines)
  no longer get surplus poles far off the band, which made the fit singular ("Ill Conditioned Matrix" warning)
  and let the model follow solver noise between the samples. Same number of solutions on the examples, equal or
  lower interpolation error.
- **Showcase H_TwoModeWaveguide**: the band is now 10.3 to 11.8 GHz. Above 11.95 GHz TE11 and TM11 propagate
  in the 22.86 x 15 mm guide as well, which the two-mode ports do not absorb.

## 0.2.12 (2026-11-03)

### Added
- **PCB stackup** (Modeler > PCB Stackup): the layer stack of a board, with copper and dielectric layers, thicknesses as expressions (also `1oz`), materials from the library or new ones, and for copper the
  choice between sheet and solid (with the material that fills the gaps between the traces). The editor shows
  the cross-section (schematic or true scale, with a z ruler) and the top view of the selected layer with
  rulers, the detected board size and a two-click measurement. Every value shows where it came from (file,
  assumed, template, suggestion, user). Suspicious values are marked in the table and the cross-section, with
  the reason as a tooltip and a suggestion on right-click: missing values, unit mix-ups (mm/µm, mil, ounces,
  loss tangent in percent), implausible values, layers in a wrong order. There is no list of problems and
  nothing is blocked; the sum of the layers is shown next to the stated board thickness. Matching materials are suggested by name or by εr and tan δ. Gerber job files (.gbrjob) are read
  with their stackup; what a job file lacks is filled with marked assumptions, legend and paste layers are not
  loaded (and the notes say so). Solder mask layers of a job file are shown in the cross-section, marked as
  not implemented yet, and are not simulated. Stackups can be saved and opened as `.hzstack` files.
- **Automatic tests**: the test suite runs on Linux for every pull request and every push to `dev` and `main`
  (`.github/workflows/tests.yml`).
- **CONTRIBUTING.md**: how to report problems, ask questions, set up a development environment, run the tests
  and open a pull request.

### Fixed
- Windows setup: works when the user name contains non-ASCII characters such as `ä` (the path of Python was
  read with the wrong code page and no `.venv` was created) (#195).

## 0.2.11 (2026-11-02)

### Fixed
- **Export Data with units like Ω** (e.g. re/im of Z(1,1)) failed on Windows with a "charmap" error and left an
  empty file. Report and matrix data are now written as UTF-8 (with a byte order mark, so spreadsheet programs
  show Ω and ° correctly), and a failed export leaves no empty or half-written file behind.
- **Export Data header**: every column name is quoted the same way, so `S(1,1)` no longer looks different from
  the others. Export Matrix Data quotes its names too; before, names like `re(S(1,1))` were split at the comma.
- **Thin straight cylinders look round again** in the 3D view: dipole arms, monopoles and coax inner conductors were
  drawn as octagons since 0.2.10. Only the display is affected; the solver mesh never was. Helices and swept
  pipes are unchanged.
- **Function *<none>* for complex quantities** (S, Y, Z, Gamma, Port Zo, propagation constant) drew the
  magnitude under the bare name, so it was not clear what was plotted. It is no longer offered for them; traces
  such as `S(1,1)` in existing projects are shown as `mag(S(1,1))`. The Smith chart is unchanged.
- **Windows update with Hertzon still running in the background**: the installer only checked the Hertzon
  window, so an MCP server started by an AI app or the remote server (`hertzon.remote serve`) could keep files
  of the Python environment open, and the installer then reported "The Python environment could not be set
  up". The setup now lists such processes and waits until they are closed (or stops them on request).
- The Windows setup writes its output to `setup.log` in the installation folder, and the installer's error
  message names that file, so a failed setup can be diagnosed after its window is closed.

## 0.2.10 (2026-11-01)

### Highlights
- **Tune with sliders, live**: compute the derivatives once, then move design variables with sliders and every
  open report (S-parameters, Smith chart, radiation pattern, antenna parameters) follows in real time. It works
  with Hertzon's own mesh deformation instead of remeshing, which suits optimization very well; more built on it is coming.
  (Introduced in 0.2.9.)
- **New optimizer, Derivative Trust Region**: minimizes the goals on the derivative tuning model and checks each
  step with one solve. The patch antenna wizard (Tools > Antenna Design > Rectangular Patch) starts from the
  textbook design formulas and is optimized after only 2-3 simulations.
- **Solving in the cloud**: Hertzon starts a cloud server, solves there and deletes it again. Tested with Hetzner
  Cloud; Amazon Web Services, Google Cloud and Microsoft Azure are implemented but untested, feedback welcome.
  Large servers and HPC clusters for studies are in progress.
- **Apply to Design keeps the solution**, and Solution Data lists every variation and optimization evaluation.
- **Wave ports with several modes** and **eigenmode setups** fixed.

### Added
- **Showcase project** brought up to date: Derivative Trust Region optimizations (goals on S-parameters, antenna parameters and output variables), output variables with S, Y, Z and VSWR, reports against a design variable, report axis settings, bandwidth and delta markers, SPARTA matrix solvers, ports-only setups, Edit Sources, Rotate / Sweep Around Axis in a coordinate system, and two new designs: a terminal differential pair (reference conductor, mixed-mode S-parameters) and a waveguide with two-mode wave ports (TE10 and TE01). The iris filter's first port has one mode again (the half model has no second mode, the solve failed) and the geometry zoo solves again (loft to an ellipse, a gap around the kept Subtract tool).
- **Tools > Antenna Design > Rectangular Patch** optimizes patch length and inset at the design frequency
  only, on the adaptive solution (no sweep is solved per evaluation), with the Derivative Trust Region
  instead of Conjugate Gradient (named PatchOpt). The dialog no longer asks for a bandwidth, a return loss
  over the band and the sweep span: they only served the band goal; the ±15 % sweep stays for looking at
  the resonance.
- **Solver image for cloud computers**: `ghcr.io/c8501024/hertzon-solver` (`:<version>`, `:dev`) holds the
  headless solver with the remote service and precompiled kernels; a Linux computer with Docker solves for
  the GUI without a Hertzon installation (`docker run ... -e HERTZON_TOKEN=...`, see docs/REMOTE.md). Built
  by `.github/workflows/solver-image.yml`.
- **Solving in the cloud (Hetzner Cloud)**: Tools > Options > Cloud with the project's API token; the first
  analysis creates a server (location and type with prices from the account), further ones use it, Hertzon
  deletes it when it closes and the server deletes itself when idle, hung or without a GUI (watchdog). The
  connection is an SSH tunnel with a host key known in advance. **Solve on** offers the cloud next to this
  computer and a remote computer. See docs/REMOTE.md.
- **Amazon Web Services, Google Cloud and Microsoft Azure are marked *(untested)***: only Hetzner Cloud has
  been tried with a real account; the Cloud tab warns to use the others at your own risk.
- **Solving in the cloud on Amazon Web Services**: Tools > Options > Cloud > Provider *Amazon Web Services*
  with the access key of an IAM user (policy in docs/REMOTE.md), region and instance type with vCPUs,
  physical cores, memory and on-demand price. Hertzon starts an Ubuntu instance in the default VPC that
  installs Docker and the solver image; the access key stays on this computer, the instance deletes itself
  by shutting down (terminate on shutdown). A sold-out zone is retried in the other zones of the region; the
  vCPU limit of new accounts gets a clear message.
- **Solving in the cloud on Google Cloud**: Provider *Google Cloud* with the JSON key of a service account
  (roles in docs/REMOTE.md), or, where the organisation forbids service account keys, with the gcloud login
  (`gcloud auth application-default login`; Hertzon finds its file and asks for the project ID); zone and machine type with vCPUs, cores and memory. Hertzon signs in with the key
  itself (no extra package), starts an Ubuntu instance that installs Docker and the solver image, reads its SSH
  host key through the API and tries the other zones of the region when a zone is full. The key (or the gcloud
  login) stays on this computer; the instance deletes itself with its own service account.
- **Solving in the cloud on Microsoft Azure**: Provider *Microsoft Azure* with a service principal (the JSON of
  `az ad sp create-for-rbac`, Contributor on the resource group `hertzon`) and the subscription ID; region and
  size with vCPUs, cores, memory and the pay-as-you-go price in EUR. Hertzon creates the network once per
  region and per server an address, a network card and an Ubuntu machine that installs Docker and the solver
  image; deleting the machine deletes the rest. The server deletes itself with the service principal.
- **S Parameter menu on the Equation page** (Fields Calculator, new Network group): lists S(i, j) for every
  port pair, with Y, Z and VSWR in submenus, from the solved data or the design's ports; a click inserts the
  term into the equation.
- **Optimizer "Derivative Trust Region"**: every round solves the design, computes its derivative tuning
  model and minimizes the goals on the model (milliseconds per evaluation) within a part of the tuning
  range; the step is checked with a solve, and the region grows or shrinks with the agreement. On the patch
  antenna (Lp, inset) from Lp = 31 mm, inset = 4 mm (dip at 2.275 GHz) to S11 ≤ −15 dB from 2.43 to 2.47 GHz:
  six solves and five tuning models, dip at 2.45 GHz with −34.9 dB. At most 6 variables. A tuning model that
  fails (for example out of memory) ends the run with the best design so far. See
  [docs/TUNING.md](docs/TUNING.md#optimize-with-the-tuning-model).

### Fixed
- **Wave ports with several modes** (modal solution) absorb every mode with its own propagation constant, as terminal ports already did. Before, all modes used the first mode's constant: in an empty 22.86 x 15 mm guide at 12 GHz, TE01 was reflected with |S22| = 0.34. Gamma and Zo of every mode row are now the mode's own (they showed the first mode's for all rows), which also corrects their de-embedding.
- **Eigenmode** setups solve again: the mesh is sized at the minimum frequency (the solve stopped with 'Frequency'), and the eigen search climbs from the minimum frequency until the wanted number of modes is found. A search below the first resonance returned only the static null space, so no modes were listed.
- A **wave port with more modes than its face carries** (for example two TE modes on a WR-90 port at 10 GHz) now stops with a message naming the port and the number of modes it has, instead of a division by zero in the S-parameters.
- **Optimization > Apply to Design** takes the solution of the applied evaluation over as the solution of
  the setup, without solving again: reports, Solution Data, field overlays and **Derivative Tuning** (on the
  evaluation's adaptive mesh) work on it as after an analysis. The best evaluation keeps its mesh and the
  fields at the adaptive frequency; the other evaluations keep S-parameters, passes and log. Before, the
  design had to be analyzed again.
- **Results > Solution Data** has a *Design Variation* list: next to the current design it shows every
  solved parametric variation and every optimization evaluation, with its passes (Convergence), Matrix
  Data, Profile and Mesh Statistics, without changing the design. While an optimization runs and the design
  has no solution of its own, it shows the evaluation being solved and follows its passes.
- **Tools > Options** opens at once. It used to wait for the check whether this computer's remote service
  runs (on Windows a refused connection to this computer alone takes about two seconds), for the host name
  lookup of the share addresses and for the scan of the installed SPARTA version. These now run in the
  background; their fields show "Checking ..." for a moment and the buttons that depend on them wait.
- A parallel frequency sweep with the matrix solver set to PARDISO warned for every frequency that
  the solver "cannot be used in MultiThreading as it does not release the GIL". The worker threads use
  PARDISO through the automatic choice anyway; the warning is gone and the results are unchanged.

## 0.2.9 (2026-10-31)

### Added
- **Solving on another computer (experimental).** For now only with another computer in the local
  network; a full solution, also for servers and cloud machines, is in progress. A computer in the local network with Hertzon installed runs the remote
  service, started in Tools > Options > Remote > Share this computer (optionally at every login) or with
  `hertzon-serve`. On the laptop, *Find in Network* in the same tab lists the shared computers and connects
  with the six-digit pairing code shown on them; *Solve on* in the status bar switches between this
  computer and the remote one. The solving computer's Progress window lists the analyses sent to it. *Analyze* then uploads the project and the
  setup's results folder, shows progress, adaptive passes and the log live, can abort, and mirrors the
  results folder back, so reports and field plots work as after a local solve. S-parameters and
  convergence are there as soon as the solver ends; large sweep field files follow in the background. Also
  for parametric and optimization runs. Several users can share one service; jobs run one after the other.
  See docs/REMOTE.md.
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
  - Disabled sweeps are greyed out in the tuning panel, and Compute Derivatives asks to analyze first when
    the chosen sweep has no solution yet (its tuned curves could not be shown in any report).
  - An open report offers the variables of a new tuning model on its X axis at once (no new report needed).
  - Documentation: [docs/TUNING.md](docs/TUNING.md) explains the workflow, the method, the accuracy and the
    limits; tutorial section 10 tunes the patch length and inset of the patch antenna.
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
    Antenna Parameters reports and tables have it too (e.g. peak realized gain over the patch length).
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
- **Result files described**: [docs/RESULT_FILES.md](docs/RESULT_FILES.md) explains the per-tetrahedron
  layout of `fields_*.npz`, the far field files and their sampling, the states in `status.json`, and that
  gain, directivity and realized gain are linear ratios (`dB(...)` gives dBi). The files carry this too:
  `fields_*.npz` has a `layout` entry, `farfield_*.npz` a `sampling` entry with the angular grid.

- **Solver Comparison with parallel frequencies.** Tools > Solver Benchmark > Solver Comparison can also
  solve the sweeps with 2, 4, ... frequencies at a time, for every checked solver, on the reference's final
  mesh (as the Core Scaling tab does for one solver). The table and the chart show each run, and the advice
  names the fastest combination of solver and parallel frequencies. A run that solved fewer frequencies at
  a time than asked (SPARTA solves them one after another for now, or the memory allowed fewer) says so in
  the Note column.

### Fixed
- **Solver Comparison on the same mesh was unfair to the other solvers**: the reference solver's sweep time
  came from its full run, the others' from a fresh process that only solved the sweep and paid the solver
  start (for SPARTA on the GPU the device start and its analysis) inside the sweep. Now every solver,
  the reference too, solves the sweeps there in a fresh process. SPARTA on the GPU starts the GPU while the
  boundaries are set up, with the time in the log and in the Note column; it is still part of every
  analysis and of the times. New columns show the direct and reduced solutions of each sweep: an
  interpolating sweep solves directly only where its reduced basis is not accurate enough, so single
  precision can make it solve more frequencies, which the advice now points out.
- **Mesh operations that miss their target** now say so: Length Based On Edges with only objects or faces
  assigned (no edges) warns in the solver log and in Validation Check instead of "no edges could be
  located"; an operation whose objects are not part of the solve, or whose selection is empty, is reported
  as not applied (it was logged as applied). Objects of a boundary or excitation that are not part of the
  solve warn like missing faces.
- **NaN in the error estimate** no longer ends the analysis after a pass: the tetrahedra concerned are named
  with their material and left out of that refinement; if no tetrahedron has a finite error, the adaptive
  analysis stops with the pass it has.
- **A killed or crashed solve no longer stays "running"**: `status.json` records the solver process, and a
  run whose process is gone shows as aborted (in the project tree, the solution data and over MCP).

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
