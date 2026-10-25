# Changelog

All notable changes of each release. The release workflow copies the section of the released version into
the GitHub release notes.

## Unreleased

### Changed
- **More accurate RCS of scattering problems**: with an incident plane wave, Auto Open now leaves one
  wavelength of air around the objects (was half a wavelength), and the far field is integrated with Gauss
  points over a box λ/8 around the scatterers instead of over mesh faces halfway to the boundary. For the
  250 mm PEC sphere at 1 GHz the error of the bistatic RCS against the exact solution drops from 0.6 dB to
  about 0.4 dB (monostatic from +1.4 dB to +0.2 dB), with fewer tetrahedra (26k instead of 38k). A padding
  set in the design is used as before.
- **Warning for dents with AURA refinement**: when AURA cannot move new boundary nodes fully onto a curved
  CAD face, the Message Manager says so once per analysis, with a hint to Local (Delaunay) refinement, which
  follows the surface exactly.

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
