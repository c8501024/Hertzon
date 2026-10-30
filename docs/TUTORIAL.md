# Hertzon tutorial: from an empty window to S-parameters, gain and RCS

This guide walks through five complete simulations step by step:

* [1. A waveguide with wave ports](#1-waveguide-with-wave-ports) (WR-90 with an inductive post, field on a
  cut plane, animation as GIF)
* [2. A coaxial cable](#2-coaxial-cable-with-wave-ports) (50 Ω line with TEM wave ports)
* [3. A half-wave dipole](#3-half-wave-dipole) (lumped port, open region, far field and gain, and the
  [half model with a symmetry plane](#36-half-model-with-a-symmetry-plane))
* [4. The radar cross section of a sphere](#4-radar-cross-section-of-a-sphere) (incident plane wave, RCS)
* [7. A chip capacitor on a microstrip line](#7-a-chip-capacitor-on-a-microstrip-line) (Lumped Elements tab,
  Lumped RLC, groups)
* [9. A differential pair](#9-a-differential-pair-with-the-terminal-solution) (terminal solution, one wave port
  for two traces, mixed-mode S-parameters)

and shows how to let an AI assistant do the same work for you through
[Hertzon's MCP server](#5-let-an-ai-assistant-build-and-run-it-mcp), how to
[export a printed circuit board as Gerber files](#6-gerber-export-of-a-printed-circuit-board) for manufacturing,
and how to [compute your own quantities with equations](#8-your-own-quantities-with-equations) (return loss,
power through a port, a field of your own) in the Fields Calculator.

Every finished model is in `examples/` and opens with **File > Open Example**, with its reports and field
plots ready: **Analyze All** (F5), then double-click a report or a field plot in the project tree.

> **Fastest start: let an AI assistant do it.** Open **Tools > AI Assistants (MCP)...**, press
> *Register* next to Claude Code, Claude desktop, Codex or VS Code and tick **Let AI assistants work in
> this window**. Then ask: *"Draw a half-wave dipole for 1 GHz with a lumped port, solve it and give me
> the input impedance and the gain."* The model appears in the open window while the assistant builds it.
> Details in [section 5](#5-let-an-ai-assistant-build-and-run-it-mcp).

Every step below uses menu names as they appear in Hertzon. Most commands are on the ribbon too
(tabs **Draw**, **Model**, **Simulation**, **Results**), and every one is in the menu bar.

## 0. The basics in two minutes

![The Hertzon window](tutorial/window.png)
*The Hertzon window with the dipole of section 3: ribbon on top, Project Manager and Properties on the left, 3D Modeler tree and 3D view in the middle, messages and progress at the bottom.*

**The window.** On the left, the **Project Manager** shows the project tree (designs, Boundaries,
Excitations, Analysis, Results, Field Overlays, Radiation). Below it is the **Properties** panel of whatever
is selected. In the middle, the **3D Modeler** tree lists the objects (solids by material, sheets, lines)
next to the 3D view. At the bottom are the **Message Manager** and the **Progress** of running analyses.

**Selecting.** Press **O** to select whole objects and **F** to select single faces (E for edges, V for
vertices). Click to select, Ctrl+click to add. Drag a box with the left mouse button to select everything
that lies completely inside it (objects, or faces, edges and vertices in those modes); Ctrl + drag adds to
the selection. **Edit > Select by Name** finds an object in a large model.

**Groups.** **Edit > Group** (Ctrl+G) joins the selected objects into one group: a click on a member then
selects the whole group, and Move, Rotate, Mirror, Copy or Delete act on all of it. **Edit > Ungroup**
(Ctrl+Shift+G) dissolves it again. The model tree lists the groups under **Groups**. SMD components from
the **Lumped Elements** tab are inserted as a group, and the integration line of a port moves with its sheet.

**Navigating.**

| | Mouse | Alternatives |
|---|---|---|
| Rotate | Middle button drag | Alt + left drag |
| Pan | Shift + left drag | Ctrl + middle drag |
| Zoom | Mouse wheel (at the cursor) | Right drag, Shift + middle drag, Alt + Shift + left drag |

A plain left drag does not move the view, it draws the selection box. Without a middle button (touchpad), the
**Rotate** and **Pan** buttons on the Draw tab turn the left mouse button into rotate or pan mode
until you press Esc or click the button again. **View > Fit All** (or the ribbon button) brings
everything into view.

**Drawing with exact values.** You can draw by clicking on the grid, or type a coordinate into the
X/Y/Z fields of the status bar and press Enter. For the tutorials it is easiest to draw roughly and then
type the exact values: in the **3D Modeler** tree next to the view, expand the object and click its
creation entry (for example *CreateBox*), then edit Position, sizes and so on in the **Properties** panel.
Clicking the object itself shows its name, material and display attributes. **Draw > Create Primitive by
Dialog...** asks for all values at once instead.

**Thicken.** **Draw > Thicken Sheet...** turns a sheet into a solid. A positive thickness grows to the
positive side of the sheet's main axis (+Z for a sheet in the XY plane), a negative one to the other side.
While the dialog is open, an arrow on each sheet shows the direction; **Reverse direction** flips it.

**Fillet and chamfer.** **Modeler > Fillet...** rounds corners, **Modeler > Chamfer...** cuts them off. On a
solid, select its edges (**E**); on a flat sheet such as a patch, select its corner vertices (**V**): each
selected corner gets one fillet with the radius, or is cut off by the distance. When the radius or distance
does not fit, the object stays unchanged and a message says why.

**Variables.** Every number accepts expressions with units, such as `a/2`, `lambda0/4` or `2.5mm+t`. Define
variables in **Project > Design Properties...** and change them later: the model, ports and integration
lines follow.

**Solving.** **Simulation > Validation Check** lists problems before you start. **Simulation > Analyze All**
(F5) solves every enabled setup. Each adaptive pass appears in the Progress window, and **Results >
Solution Data...** shows the convergence table, the mesh statistics and the matrix data. New setups refine
the mesh with **hzAURA (EMerge)**: after each pass the elements with the largest error are split in place,
and new nodes on curved faces (cylinders, spheres, free-form surfaces) land exactly on the CAD surface.
**Curved elements** (on by default) make the tetrahedra at those surfaces follow the true shape. Both
settings are in the setup (tabs **Advanced** and **Options**); the examples use them throughout.

**Field plots, cut planes and animations.** **Results > Mag E** (or Vector E, Mag H, Mag Jsurf, ...)
plots the field on the selected faces or objects. With **Plot on** XY, YZ or XZ and a **Cut position**
(any expression, e.g. `b/2`) it plots on a cut plane through the whole model instead. For a plane of
any shape and size, draw a sheet as **Non Model** object (Modeler > New Objects > Non Model, or answer
the question after drawing) and plot on it: the plot is cut from the solver mesh, ends exactly on the
sheet's outline and follows the sheet when you move or resize it, without solving again. Right-click a
field plot > **Animate** runs the phase from 0 to 360°. **Export GIF...** in the animation bar saves the
animation as a looping GIF exactly as the view shows it: same size, colours, scale and speed.

**Overlapping objects.** Hertzon checks overlapping solids exactly. An object completely inside
another (substrate in an air box, a via in the substrate) is the usual case and needs nothing: the inner
object applies there, and the air region is always background. A partial overlap of two objects with
different materials is an **error** that blocks the analysis until you resolve it, with one exception: with
the design setting **Material override** (on by default) a metal overrides a
dielectric (or air) whatever its size. You find the setting in the Properties window when the design is
selected in the project tree. The **Validation Check** lists every overlap that matters:

| Overlap | Example | Material override on (default) | Material override off |
|---|---|---|---|
| Conductor partly in a dielectric | via sticking out of the substrate, trace over the board edge | ✔ note: the conductor applies | ✖ error |
| Two dielectrics (or two conductors) of different materials, partly | substrate and a glue layer shifted into each other | ✖ error | ✖ error |
| Dielectric completely inside a conductor | dielectric inside a PEC bounding box | ⚠ warning: the dielectric has no effect | not listed (the dielectric applies) |
| One object completely inside another otherwise | substrate in the air box | not listed (the inner object applies) | not listed |
| Same material | two copper pieces touching | not listed (nothing changes) | not listed |

Analyze, optimization and parametric runs (also from scripts and the AI link) stop with the Validation
Check open when it finds an error; the solver checks the same rule once more.

Select an overlap and click **Resolve Overlap**, or **Resolve All Overlaps**: Hertzon subtracts the object
whose material applies (the conductor, otherwise the smaller object) from the other one (**Subtract** with *Clone tool objects before operation*, so both stay). The
model then shows exactly what is solved, and the geometry is free of overlaps (for example before an export). Undo (Ctrl+Z) takes it back.

> The very first analysis after installing or updating compiles Hertzon's solver kernels and takes a few
> minutes longer. The installer already does this once, so normally you do not notice it.

**Updates.** When a new version is out, a small dialog after the start shows what changed since your
version, with **Install**, **Skip this version** and **Later** (at most once every 7 days; in between the
status bar names the new version). Install keeps your settings and the Python environment and asks to save
open changes first. **Help > Check for Updates...** checks at any time; Tools > Options > General switches
the check at start off.

---

## 1. Waveguide with wave ports

**Goal:** S11 and S21 of a WR-90 rectangular waveguide (22.86 × 10.16 mm) with a metal post in the
middle, from 8 to 12 GHz. The finished project is `examples/WaveguidePost.hzn`.

### 1.1 New design and variables

1. **File > New**. The project starts with one empty design.
2. **Simulation > Solution Type...**: choose **Modal** and leave **Auto open region** at **Off**.
   A waveguide is a closed problem: every outer face that is not a port is a perfect conductor.
3. **Project > Design Properties...**: add the variables

   | Name | Value |
   |---|---|
   | `a` | `22.86mm` |
   | `b` | `10.16mm` |
   | `L` | `50mm` |
   | `r_post` | `1.5mm` |

### 1.2 Geometry

1. **Draw > Box**, then set in Properties: Position `0,0,0`, XSize `a`, YSize `L`, ZSize `b`. Rename the
   object to `Waveguide` (Name field). Material stays **vacuum**: the box is the air inside the guide.
2. **Draw > Cylinder**: CenterPosition `a/2,L/2,0`, Radius `r_post`, Height `b`, Axis `Z`. Name it
   `Post` and set **Material** to **pec**.
3. Select `Waveguide` first, then Ctrl+click `Post`, and choose **Modeler > Boolean > Subtract**. The post
   is cut out of the air box. Its surface becomes a perfect conductor automatically, like the outer walls.

![WR-90 waveguide with post](tutorial/wg_model.png)
*The air box of the waveguide with the post cut out of it. The two end faces (blue) carry the wave ports.*

### 1.3 Wave ports

A wave port sits on an outer face of the model. Hertzon computes its modes (here the TE10 mode of the guide)
with a 2D eigenmode solve and feeds the 3D problem with it.

1. Press **F** (face selection) and click the end face of the box at y = 0.
2. **Simulation > Excitations > Assign > Wave Port** (or the large **Wave Port** button on the
   Simulation tab). Keep **Number of modes 1** and **Mode type Auto**, then press OK. It becomes port 1.
3. Select the face at y = L and assign a second **Wave Port**. It becomes port 2.

> Instead of a face of the box you can also draw a rectangle exactly on the end face (**Draw >
> Rectangle**, Axis `Y`) and assign the port to that sheet. The example file does this.

### 1.4 Solution setup and sweep

1. **Simulation > Analysis Setup > Add Solution Setup...**:
   * Solution frequency **Single**, Frequency `10GHz`, a frequency in the upper part of the band,
     where the mesh needs to be finest.
   * Maximum number of passes `6`, Maximum Delta S `0.02`.
   * Leave the **Advanced** tab at **Mesh refinement: hzAURA (EMerge)** and the **Options** tab at
     **Curved elements** on: the round post is then meshed with curved elements on its true surface.
2. Select `Setup1` in the tree, then **Simulation > Analysis Setup > Add Frequency Sweep...**:
   Sweep type **Interpolating**, Distribution **Linear Count**, Start `8GHz`, Stop `12GHz`, Count `201`.
   An interpolating sweep solves only as many frequencies as it needs and fits the rest.

### 1.5 Solve and look at the results

1. **Simulation > Validation Check**, then **Simulation > Analyze All** (F5).
2. Watch the passes in the Progress window. The analysis stops as soon as S changes by less than 0.02
   between two passes.
3. **Results > Create Report > Create Modal Solution Data Report > Rectangular Plot**: choose
   `dB(S(1,1))` and `dB(S(2,1))` over frequency. **Smith Chart** shows S11 on the Smith chart.
4. **Results > Mag E** with nothing selected: set **Plot on** to `XY` and **Cut position** to `b/2`. The
   field appears on the plane halfway up the guide. **View > Orientation > Top** looks onto it.
5. Right-click the plot (`Mag_E_Cut` in the example) under **Field Overlays** > **Animate**. The wave runs
   from port 1 to the post and is partly reflected. **Export GIF...** in the bar under the view saves it.
6. **File > Export Touchstone...** writes `WR90_Post.s2p` for other tools.

![S-parameters of the waveguide post](tutorial/wg_sparams.png)
*S11 and S21 from 8 to 12 GHz. The thin post acts as a shunt inductance and reflects most of the power, less at higher frequencies (S21 from −15.4 dB at 8 GHz to −7.2 dB at 12 GHz).*

![Electric field in the waveguide](tutorial/wg_field.png)
*`Mag_E_Cut`: Mag E on the cut plane halfway up the guide (z = b/2) at 10 GHz, seen from the top. The wave comes from port 1 at the bottom, forms a standing wave in front of the post and leaks past it.*

![Animation of the field in the waveguide](tutorial/wg_animation.gif)
*The same plot animated over the phase (Animate) and saved with **Export GIF...**: the GIF has the size, colours, scale and speed of the view.*

> **What you should get** (this model solved with Hertzon 0.2.2 and hzAURA: 2 adaptive passes, 1 376
> tetrahedra, a few seconds): at 10 GHz S11 = −0.41 dB and S21 = −10.5 dB.

---

## 2. Coaxial cable with wave ports

**Goal:** a 20 mm long 50 Ω coax line with PTFE filling, from 1 to 10 GHz. It should be nearly perfectly
matched: |S11| far below −30 dB and |S21| ≈ 0 dB, with the phase of S21 falling linearly with
frequency. The finished project is `examples/Coax.hzn`.

For a coax with inner radius *a*, outer radius *b* and permittivity εr:
Z0 = 60 Ω / √εr · ln(b/a). With εr = 2.1 (Teflon) and *a* = 0.5 mm, 50 Ω needs *b* = 1.675 mm.

### 2.1 Design and variables

1. **Project > Insert Design** (or **File > New**), **Solution Type**: **Modal**, Auto open **Off**.
2. **Design Properties**: `a = 0.5mm`, `b = 1.675mm`, `L = 20mm`.

### 2.2 Geometry

1. **Draw > Cylinder**: CenterPosition `0,0,0`, Radius `b`, Height `L`, Axis `Z`. Name `Dielectric`,
   material **Teflon (tm)**. Its outer surface is the outer conductor: an outer face without a
   boundary is a perfect conductor.
2. **Draw > Cylinder**: CenterPosition `0,0,0`, Radius `a`, Height `L`, Axis `Z`. Name `Inner`,
   material **pec**.
3. Select `Dielectric`, Ctrl+click `Inner`, **Modeler > Boolean > Subtract**. The dialog shows
   `Dielectric` under *Blank Parts* and `Inner` under *Tool Parts*. Tick **Clone tool objects before
   operation** if you want to keep the inner conductor as a visible PEC object. Without it, the hole's
   surface is a perfect conductor anyway, which gives the same result.

![Coax model](tutorial/coax_model.png)
*The PTFE cylinder with the PEC inner conductor. The end face (blue) is one of the circular port sheets.*

### 2.3 Wave ports on the annular end faces

1. **Draw > Circle**: CenterPosition `0,0,0`, Radius `b`, Axis `Z`. Name `Port1`.
2. **Draw > Circle**: CenterPosition `0,0,L`, Radius `b`, Axis `Z`. Name `Port2`.
3. Select `Port1` (press **O**), **Simulation > Excitations > Assign > Wave Port**, OK. Do the same for
   `Port2`.

   The port solver finds the TEM mode of the ring between inner and outer conductor by itself. Mode type
   **Auto** is right here. **TEM** would force it.
4. Optional: in the port's **Post Processing** tab, **Renormalize** to `50ohm` and **Deembed** a
   distance if you want the reference plane elsewhere.

### 2.4 Setup, sweep and results

1. **Add Solution Setup...**: Frequency `5GHz`, Maximum passes `6`, Maximum Delta S `0.02`.
2. **Add Frequency Sweep...**: **Interpolating**, Linear Count, `1GHz` to `10GHz`, Count `37`.
3. **Analyze All**.
4. Plot `dB(S(1,1))`, `dB(S(2,1))` and `ang_deg(S(2,1))`. A deep S11 over the whole band shows that the
   line is matched to the port, which means its impedance is the 50 Ω you designed.
5. **Results > Vector E**, **Plot on** `XY`, **Cut position** `L/2`: the field across the line. **Results >
   Mag E**, **Plot on** `XZ`, **Cut position** `0`: the field along it. The example has both
   (`Vector_E_Cross`, `Mag_E_Along`).

![S-parameters of the coax](tutorial/coax_sparams.png)
*S11 stays below −33 dB from 1 to 10 GHz, S21 is at 0 dB.*

![Vector E in the coax cross-section](tutorial/coax_field.png)
***Vector E** on the cross-section halfway along the line (plane XY at z = L/2): the radial TEM field between inner and outer conductor, strongest at the inner conductor.*

![Mag E along the coax](tutorial/coax_field2.png)
***Mag E** on the plane XZ through the axis at phase 0°: the field passes through zero about halfway along the line, half a wavelength at 5 GHz. **Animate** shows it travel from port 1 to port 2 without a standing-wave pattern, because the line is matched.*

> **What you should get** (this model solved with Hertzon 0.2.2 and hzAURA: 2 adaptive passes, 8 854
> tetrahedra, about 20 seconds): S11 stays below −33 dB from 1 to 10 GHz, and S21 is better than −0.03 dB.
> The phase of S21 falls by 34.8° per GHz. The theoretical value for 20 mm of PTFE line is
> 360° · √2.1 · 20 mm / c0 = 34.8° per GHz.

---

## 3. Half-wave dipole

**Goal:** a dipole for 1 GHz, made of two PEC rods with a small feed gap, fed by a lumped port.
Results are the input impedance (about 73 Ω at resonance), S11, the radiation pattern and the gain
(about 2.15 dBi). The finished project is `examples/Dipole.hzn`.

### 3.1 Design with an open region

1. **Insert Design**, then **Simulation > Solution Type...**: **Modal**, **Auto open region: Auto Open**,
   **Open boundary: Auto**.

   Auto Open puts an air box around the model and an absorbing boundary on its surface, at least a
   third of a wavelength away. You do not draw an air box yourself. Choose **PML** instead of Auto if
   the outer boundary must be very close to the model.
2. **Design Properties**: `Larm = 70mm` (one arm, about λ/4 a little shortened), `r = 1mm` (rod radius),
   `g = 2mm` (feed gap).

### 3.2 Geometry

1. **Draw > Cylinder**: CenterPosition `0,0,g/2`, Radius `r`, Height `Larm`, Axis `Z`. Name `ArmTop`,
   material **pec**.
2. **Draw > Cylinder**: CenterPosition `0,0,-g/2`, Radius `r`, Height `-Larm`, Axis `Z`. Name
   `ArmBottom`, material **pec**. A negative height draws downwards.
3. **Draw > Rectangle**: Position `-r,0,-g/2`, Axis `Y`, XSize `2*r`, ZSize `g`. Name `Gap`. This
   sheet fills the gap between the two rod ends and becomes the port.

### 3.3 Lumped port

1. Select `Gap` (**O**), **Simulation > Excitations > Assign > Lumped Port** (or the **Lumped Port**
   ribbon button).
2. **Full port impedance**: `73ohm` (or `50ohm`, if you want S11 against a 50 Ω generator).
3. **Integration line**: Hertzon places the line between the two conductors that touch the sheet, from the
   centre of one contact to the centre of the other, here from one arm to the other along Z:
   `0,0,-g/2;0,0,g/2`. The line is drawn as an arrow on the model. To set it yourself, enter start and end
   point, or right click the port in the project tree > **Draw Integration Line** and click both points
   in the 3D view (**Auto Integration Line** places it automatically again).

### 3.4 Far field, setup and sweep

1. **Simulation > Radiation > Insert Far Field Setup > Infinite Sphere...** (ribbon: **Far Field**):
   θ 0..180° and φ 0..360°. Set both steps from the default 10° to `2deg` for smooth pattern plots.
   This tells Hertzon where to evaluate the radiation.
2. **Add Solution Setup...**: Frequency `1GHz`, Maximum passes `10`, Maximum Delta S `0.02`.
3. **Add Frequency Sweep...**: **Interpolating**, Linear Count, `0.7GHz` to `1.3GHz`, Count `121`.
   This is enough for S-parameters and impedance over frequency.
4. For gain, patterns and antenna parameters **over frequency**, add a second sweep: **Fast**, Linear
   Count, `0.7GHz` to `1.3GHz`, Count `7`, with **Save fields (all frequencies)**. Far fields are computed
   from the stored fields, so only a Discrete or Fast sweep with Save fields has them at every frequency;
   an Interpolating sweep has S-parameters only, and the far field reports then offer LastAdaptive.
5. **Validation Check**, **Analyze All**.

### 3.5 Results

1. **Results > Create Report > Create Modal Solution Data Report > Rectangular Plot**:
   `dB(S(1,1))` shows the resonance. Choose the category **Z Parameter** for `re(Z(1,1))` and
   `im(Z(1,1))`, the input impedance. Resonance is where the imaginary part crosses zero.
2. **Create Far Fields Report > Radiation Pattern**: `dB(GainTotal)` over θ at φ = 0° is the
   familiar figure eight. **3D Polar Plot** shows the doughnut. With the solution **Setup1 : Sweep** of
   the Fast sweep, **Frequency** picks the pattern at each sweep frequency. Right-click > **Marker** sets
   markers, **Beamwidth / Side Lobes...** gives the opening angle (-3 dB) and the side lobe level;
   **Create Far Fields Report > Rectangular Plot** shows the same cut over the angle.
3. **Results > Overlay 3D Pattern on Model** puts the 3D pattern onto the dipole.
4. **Create Antenna Parameters Report > Data Table**: peak gain, directivity, radiated power and
   radiation efficiency; with the Fast sweep as solution over frequency (**Rectangular Plot**).
5. **Results > Mag E** on a cut plane through the dipole, then **Animate**: you can watch the waves
   leave the antenna.

![S11 of the dipole](tutorial/dipole_s11.png)
*S11 against the 73 Ω port: resonance at 0.98 GHz with −46 dB.*

![Radiation pattern of the dipole](tutorial/dipole_pattern.png)
***Radiation Pattern**, dB(GainTotal) over θ at φ = 0°: the figure eight with 2.2 dBi at θ = 90° and deep nulls along the dipole axis.*

![3D pattern on the dipole](tutorial/dipole_3d.png)
***Overlay 3D Pattern on Model**: the doughnut-shaped gain pattern around the dipole.*

![Field of the dipole](tutorial/dipole_field.png)
***Mag E** on the XZ plane with a logarithmic scale (Field plot **Attributes**: Log, limits 3 to 3000 V/m). The field is strongest at the rod ends and in the feed gap and falls off with distance, slowest broadside.*

> **What you should get** (this model solved with Hertzon 0.2.2 and hzAURA: 4 adaptive passes, 49 782
> tetrahedra, about three minutes on 4 cores): the input impedance is 71.6 Ω, purely real, at 0.979 GHz.
> Against the 73 Ω port the deepest S11 is −46 dB at 0.98 GHz. The peak gain is 1.67, that is 2.22 dBi,
> at θ = 90°. A thin half-wave dipole has 73 Ω and 2.15 dBi in theory.

The example opens with these reports (`S11`, `Input Impedance`, `Gain Pattern`, `Antenna Parameters`), the
field plot `Mag_E_XZ` and the 3D pattern `Gain3D`, and with the Fast sweep `FastFields` of step 4.

### 3.6 Half model with a symmetry plane

The dipole is mirror symmetric to the plane x = 0: the electric field lies in that plane and the magnetic
field crosses it at right angles, as on a perfect magnetic conductor. A **Symmetry** boundary of type
**Perfect H** on that plane lets you solve only the half x ≥ 0, with fewer elements and in less time. The
far field (gain, pattern, RCS) is computed for the whole dipole: Hertzon adds the mirror image at the
symmetry plane. The finished design is `HalfWave1GHz_Half` in `examples/Dipole.hzn`.

1. Select the design and choose **Project > Copy Design**, then rename the copy.
2. **Simulation > Solution Type...**: **Auto open region: Off**. The symmetry plane has to be an outer face
   of the model, so the air box is drawn by hand instead of by Auto Open.
3. Select `ArmTop` and `ArmBottom`, **Modeler > Boolean > Split...**: plane **YZ**, keep **Positive**. Only
   the halves at x ≥ 0 remain.
4. Set the `Gap` sheet to Position `0,0,-g/2`, XSize `r`: the half of the port sheet.
5. **Design Properties**: add `pad = 143mm`, a third of the wavelength at the lowest sweep frequency
   (0.7 GHz), the distance Auto Open used for the full model.
6. **Draw > Box** `AirBox`: Position `0,-r-pad,-g/2-Larm-pad`, XSize `r+pad`, YSize `2*(r+pad)`,
   ZSize `g+2*(Larm+pad)`, material **air**. Its face at x = 0 lies exactly on the cut faces of the arms.
7. Press **F**, select the face of `AirBox` at x = 0 and choose **Simulation > Boundaries > Assign >
   Symmetry**, type **Perfect H**. Select the other five faces and assign **Radiation**.
8. Set the **Full port impedance** of the lumped port to `146ohm`. The symmetry plane cuts the port in half:
   it sees the same voltage and half the current, so twice the impedance of the full port. (The impedance
   multiplier of the Symmetry boundary is not applied by the solver yet, so set the port itself.)
9. **Validation Check**, **Analyze All**.

![Half model of the dipole](tutorial/dipole_half.png)
*The half model: the air box drawn from the plane x = 0, whose face there is the Perfect H symmetry plane (highlighted). The half dipole lies on that face, in its middle.*

> **What you should get** (Hertzon 0.2.2, hzAURA: 4 adaptive passes, 18 515 tetrahedra, 45 seconds on 4
> cores instead of about three minutes for the full model): resonance at 0.978 GHz with 143.5 Ω against
> the 146 Ω port, that is 71.8 Ω for the full dipole (full model: 71.6 Ω at 0.979 GHz). The peak gain of
> the mirrored far field is 2.4 dBi, 0.2 dB above the full model.

Change `Larm` and **Analyze All** again to move the resonance. Or let **Simulation > Optimization > Add
Optimization...** find the length for S11 < −20 dB at 1 GHz.

---

## 4. Radar cross section of a sphere

**Goal:** the monostatic and bistatic RCS of a perfectly conducting sphere of radius 100 mm at 1 GHz,
lit by a plane wave. For a sphere the exact answer is known (Mie series), so this is the classic way
to check an RCS setup. The finished project is `examples/SphereRCS.hzn`, and the 250 mm sphere in
`examples/NATO-Sphere.hzn` is set up the same way.

### 4.1 Design

1. **Insert Design**, **Solution Type**: **Modal**, **Auto Open**, **Open boundary: Radiation**.
   With an incident plane wave Hertzon solves for the scattered field, and the incident wave enters
   through the radiation boundary. It is used for plane waves whatever boundary is picked here, and
   the region gets one wavelength of air around the object, as the radiation boundary is more
   accurate the farther out it lies. The far field is integrated over a box λ/8 around the object.
2. **Design Properties**: `R = 100mm`.

### 4.2 Geometry

1. **Draw > Sphere**: CenterPosition `0,0,0`, Radius `R`. Name `Sphere`, material **pec**.

That is the whole model: the air region and its radiation boundary come from Auto Open.

### 4.3 Incident plane wave

1. With nothing selected, **Simulation > Excitations > Assign > Incident Plane Wave**.
2. **Vector input format: Cartesian**, **k direction** `0,0,1` (the wave travels in +z), **Eo vector**
   `1,0,0` (x polarization), **E0** `1` V/m, zero phase at `0,0,0`.

   With **Spherical** you give the direction of incidence as θ, φ and a polarization angle instead.

### 4.4 Far field and setup

1. **Insert Far Field Setup > Infinite Sphere...**: θ 0..180°, φ 0..360°, both steps `2deg`. RCS is computed
   on this sphere.
2. **Add Solution Setup...**:
   * Frequency `1GHz`, Maximum passes `8`.
   * With only an incident wave there are no S-parameters. Convergence uses **Maximum Delta Energy**
     instead, the relative change of the scattered energy between passes. The default `0.001` fits.
   * **Options** tab: **Order of basis functions: Second Order** and **Curved elements** on (default).
     Curved second-order elements follow the sphere surface exactly and give accurate RCS with
     few elements. The refinement (hzAURA) puts every new node of the sphere surface onto the sphere.
3. **Analyze All**.

### 4.5 Results

The example has the reports of this section (`Bistatic RCS`, `Monostatic RCS`) and the field plot `Mag_E_XZ`.

1. **Create Far Fields Report > Radiation Pattern** with **Bistatic RCS**: `dBsm(BistaticRCSTotal)` in
   dBsm over θ at φ = 0° (the E-plane) and φ = 90° (the H-plane). θ = 180° is the backscatter
   direction, θ = 0° the forward scatter.
2. **Create Monostatic RCS Report > Data Table** gives the backscatter RCS in m² and dBsm.
   **Rectangular Plot** draws it over the adaptive passes, so you see it converge.
3. **Results > Mag E** on a cut plane (for example the XZ plane) shows the shadow behind the sphere and the
   standing wave in front of it. In **Simulation > Excitations > Edit Sources...** choose whether the plot
   shows the **Total**, the **Scattered** or the **Incident** field.

![Field around the sphere](tutorial/sphere_field.png)
*Total **Mag E** on the XZ plane at phase 0°. The wave comes from below (k = +z, E along x): its wave fronts are bent around the sphere, with a standing wave in front of it and the shadow behind it. **Animate** shows the wave pass the sphere.*

![Bistatic RCS of the sphere](tutorial/sphere_bistatic.png)
*Bistatic RCS in dBsm over θ at φ = 0°. The forward scatter at θ = 0° is strongest. θ = 180° is the backscatter, the monostatic RCS.*

![Monostatic RCS over the passes](tutorial/sphere_mono.png)
***Monostatic RCS** over the adaptive passes: −13.45 dBsm after the first and −13.74 dBsm after the second pass, when the scattered energy has converged. The Mie series gives −13.48 dBsm.*

> **What you should get** (this model solved with Hertzon 0.2.2 and hzAURA: 2 adaptive passes, 5 657 curved
> second-order tetrahedra, about ten seconds): a monostatic RCS of 0.0423 m² (−13.74 dBsm). The Mie
> series gives 0.0448 m² (−13.48 dBsm), so the difference is about 0.25 dB. With more passes (Minimum
> number of passes) the value moves by about ±0.15 dB around −13.7 dBsm.
> `examples/benchmark/sphere_rcs/mie_pec.py` computes the Mie value for any radius and frequency.

For your own RCS models, import the geometry with **Modeler > Import...** (STEP, IGES, BREP or STL)
and set the material to pec. `examples/DroneRCS.hzn` does that with a drone from an STL file.

---

## 5. Let an AI assistant build and run it (MCP)

Hertzon includes an MCP server, a standard interface through which AI assistants use programs. With it,
Claude Code, the Claude desktop app, Codex or VS Code (Copilot agent mode) can build every model of this
tutorial for you, run it and read the results. They can also explain them and change the design until a
goal is met.

### 5.1 Set it up once

1. **Tools > AI Assistants (MCP)...**
2. Press **Register** next to your assistant:

   | Assistant | Where Hertzon is registered |
   |---|---|
   | Claude Code | `~/.claude.json` |
   | Claude desktop app | `claude_desktop_config.json` |
   | Codex | `~/.codex/config.toml` |
   | VS Code | the user `mcp.json` |

   Without the GUI: `python -m hertzon.mcp --install claude` (or `desktop`, `codex`, `vscode`, `all`).
   `python -m hertzon.mcp --config` prints the settings for any other MCP client.
3. Tick **Let AI assistants work in this window**. Everything the assistant does then happens in your
   open window: you see objects appear, **Ctrl+Z** undoes them, and its analyses run in the normal
   solve queue with the normal progress display. Without the live link the assistant works on `.hzn`
   files and solves in the background.
4. Start a new session of your assistant so it loads the Hertzon server.

![AI Assistants dialog](tutorial/mcp_dialog.png)
***Tools > AI Assistants (MCP)...**: the live link checkbox and one Register button per assistant.*

### 5.2 Ask for a simulation

Write what you want in plain language. Some examples matching this tutorial:

* *"Create a WR-90 waveguide, 50 mm long, with a PEC post of radius 1.5 mm in the middle, wave ports on
  both ends, and sweep 8-12 GHz. Plot S11 and S21."*
* *"Build a 50 Ω PTFE coax, 20 mm long, with wave ports, and check that S11 is below −30 dB from 1 to
  10 GHz."*
* *"Draw a half-wave dipole for 1 GHz with a lumped port, solve it and give me the input impedance at
  resonance and the peak gain. Then shorten it until S11 at 1 GHz is below −20 dB."*
* *"Compute the monostatic RCS of a 100 mm PEC sphere at 1 GHz and compare it with the Mie series."*
* *"Open examples/PatchAntenna.hzn, explain what the model does, and render a picture of it."*

The assistant works step by step with the same building blocks you used above, and tells you what it did.

### 5.3 What the assistant can do

| Area | Tools |
|---|---|
| Projects | `create_project`, `open_project`, `get_project`, `save_project`, `get_context` |
| Settings and materials | `set_design` (units, variables, solution type, Auto Open), `list_materials`, `add_material`, `get_schema` |
| Geometry | `add_object`, `import_geometry`, `modify_object`, `add_operation` (subtract, unite, ...), `delete`, `get_geometry` (faces with index, centre, normal; edges and vertices for Fillet/Chamfer), `set_coordinate_system` |
| Boundaries and ports | `assign`, `modify_assignment` (boundaries, wave/lumped ports, plane waves, mesh operations) |
| Analysis | `set_setup`, `set_sweep`, `set_far_field_sphere`, `validate`, `run_simulation`, `get_status`, `abort_simulation` |
| Results | `get_results` (S-parameters, impedances, gain, RCS, convergence), `render_image` (a picture of the model), `set_report`, `set_field_plot`, `set_equation` (Fields Calculator) |
| Parametric and optimization | `set_parametric`, `set_optimization`, run and read with `run_simulation`, `get_status`, `get_results` |
| Scripting | `run_python` for anything else, with the Hertzon model API |

Simulation results can be wrong, whether you or an assistant set the model up. Check them as described in
the README's *No warranty* section.

---

## 6. Gerber export of a printed circuit board

**Goal:** manufacturing data (Gerber X2 copper layers, board outline, Excellon drill files) of a planar
design. The project is `examples/GerberTestBoard.hzn`: a 40 x 30 mm FR4 board, 1.6 mm thick, with copper
shapes on top (rectangle, circle, a plane with a round cut-out and an island in it, overlapping rectangle
and circle, an ellipse, a 35 µm thick trace body, a via pad), a ground plane with clearances on the bottom,
a copper via, a mounting hole and a milled slot. A port sheet, a radiation sheet and the air region show
that simulation helpers stay out of the files. `examples/PatchAntenna.hzn` and `examples/HairpinFilter.hzn`
export the same way.

1. Open the project and choose **File > Export > Gerber...** (ribbon: **Project > Import / Export > Export
   Gerber**).
2. **Board plane and origin:** keep *Global*, plane *XY*, origin *Lower left corner of the board outline*.
   For a board drawn in another plane pick its coordinate system and plane; all files use this one frame
   and the bottom layer is not mirrored.
3. **Layer assignment** tab: Hertzon suggests the target of every object. Copper sheets (Perfect E or
   Finite Conductivity) go to *L1 Top* or *L2 Bottom* by their height, the copper trace body is cut at half
   its thickness, the via becomes a *Plated hole* and the substrate the *Board outline*. Port, radiation
   sheet and region are *Simulation helpers* and cannot be exported. Change a target with its drop-down list.
4. **Layers** tab: the copper layers from top to bottom with their elevation. Rename a layer (the name goes
   into the file name) or untick it to leave it out.
5. **Preview:** the copper as it will be written, the outline, PTH (white) and NPTH (red) holes and the
   origin. The copper cut-out shows the island inside it, the overlapping rectangle and circle are one shape,
   and the ground clearance around the mounting hole is free. The message list says which curves are
   approximated (here the ellipse, within the 1 µm curve tolerance; circles and arcs stay exact).
6. Choose the folder and press **Export**. A message lists the written files (**Open Folder** opens them):

| File | Content |
|---|---|
| `TwoLayerBoard-L1_Top.gbr`, `TwoLayerBoard-L2_Bottom.gbr` | copper layers |
| `TwoLayerBoard-Profile.gbr` | board outline with the slot |
| `TwoLayerBoard-PTH_L1-L2.drl` | the via, 0.8 mm, plated |
| `TwoLayerBoard-NPTH.drl` | the mounting hole, 3.2 mm, non-plated |
| `TwoLayerBoard-export-report.txt` | settings, layers, objects, holes and messages |

![Gerber export dialog with the test board](tutorial/gerber_dialog.png)

Open the files in any Gerber viewer (e.g. the KiCad Gerber viewer) to compare them with the preview.
If something cannot be exported unambiguously (a tilted copper sheet, a thick conductor that is no via),
the dialog names the object; errors keep the *Export* button disabled. How layers, polarity, arcs and
drills are derived is described in [GERBER_EXPORT.md](GERBER_EXPORT.md).

---

## 7. A chip capacitor on a microstrip line

**Goal:** a DC-block capacitor (2.2 pF, size 0603) in a 50 Ω microstrip line, as it would be mounted:
pads, the part with its typical series inductance and loss, and the field solution around it. The finished
design is `Microstrip50_DCBlock` in `examples/Microstrip.hzn`, next to the plain line `Microstrip50`.

1. Open `examples/Microstrip.hzn` (**File > Open Example**), select the design `Microstrip50` and choose
   **Project > Copy Design**.
2. **Design Properties**: add `gap = 1mm`. Rename `Trace` to `TraceA` and set its YSize to
   `Lsub/2-gap/2`. Draw a second trace `TraceB`: **Draw > Rectangle**, Position `-w/2,Lsub/2+gap/2,h`,
   Axis `Z`, XSize `w`, YSize `Lsub/2-gap/2`. Add it to the boundary `Copper_Trace` (and to the mesh
   operation), so both halves are the same 35 µm copper.
3. Press **O**, click `TraceA` and Ctrl+click `TraceB`. On the **Lumped Elements** tab open the menu of
   **Capacitor** and pick **0603**.
4. In the dialog enter `2.2pF`, keep **Planar** and **Typical ESL and ESR** (0.45 nH and 0.05 Ω for 0603)
   and press OK. Hertzon puts the part into the gap: two pads from the trace ends to the land pattern
   (made of the same copper as `TraceA`), the element sheet `C1` with the **Lumped RLC** boundary
   `C1_RLC` (Series RLC) and its integration line from pad to pad.
5. The three parts form the group `C1` (model tree, **Groups**): a click on any of them selects the whole
   part, so **Edit > Arrange > Move** moves pads, element and integration line together.
6. **Analyze All** and plot `dB(S(1,1))` and `dB(S(2,1))` (report `S Parameters` in the example).

![The DC-block capacitor in the gap of the line](tutorial/dcblock_model.png)
*The line with the 0603 capacitor in the middle: the two pads and the element sheet (green) between them form the group `C1`.*

![S-parameters with the DC-block capacitor](tutorial/dcblock_sparams.png)
*S11 and S21 from 1 to 6 GHz. At 1 GHz the 2.2 pF still block part of the signal (S21 −1.6 dB); at 3.4 GHz the capacitance, the ESL and the inductance of the pads cancel and the line is matched (S11 −54 dB). Near 4.75 GHz a narrow resonance of the mounted part takes up to 1.4 dB (also found with a discrete sweep).*

For a measured part, choose **Touchstone** on the Lumped Elements tab and the vendor's .s2p file; with
**3D** the part gets a body with metal terminations and its mounted inductance comes from the field
solution instead of the typical ESL. See [LUMPED_ELEMENTS.md](LUMPED_ELEMENTS.md).

---

## 8. Your own quantities with equations

**Goal:** define your own result quantities in the **Fields Calculator** and show them in reports and
field plots: the return loss of a port from the S-parameters, the power that flows through each port from
the fields, and the power flow along the guide as a field of its own. We use the waveguide of
[section 1](#1-waveguide-with-wave-ports); the equations, reports and the field plot of this section are in
`examples/WaveguidePost.hzn` as well.

An **equation** has a name and a formula, for example `RL = -dB(S(1,1))`. Hertzon works out its type from
the formula:

| Type | Example | Where it is shown |
|---|---|---|
| Value of the S-parameters (no fields needed) | `RL = -dB(S(1,1))` | Solution data reports, category **Equations**, at every frequency of the solution |
| Value from the fields (an integral, a maximum, a point value) | `P_in = flux(Port1Sheet, re(S))` | **Fields reports**, at every frequency with saved fields |
| Field (a value at every point) | `Sy = y(re(S))` | **Plot Fields**, like Mag E, on faces, objects or cut planes |

Equations are saved with the design and listed in the project tree under **Equations**.

### 8.1 Return loss from the S-parameters

1. Open `examples/WaveguidePost.hzn` (or your model from section 1) and solve it (**Analyze All**, F5).
2. **Results > Calculator**. The **Equations** tab opens next to the 3D Modeler, and the ribbon shows the
   **Equation** page with fields, symbols, fractions, integrals and functions.
3. Type `RL = -dB(S(1,1))` and press **Save** (optionally with a description above the formula). While you
   type, the formula is shown typeset; on the right, the type (**Scalar value**), the unit and where to
   use it. An error is underlined where it is.
4. Right-click `RL` under **Equations** in the project tree > **Add to Report**. The report shows the return
   loss over the whole interpolating sweep from 8 to 12 GHz: an equation of S-parameters needs no fields,
   so it is computed at every frequency of the sweep. In an existing report, choose the category
   **Equations** and the quantity `RL`.

![The Equations tab with the return loss](tutorial/eq_rl.png)
*The Equations tab: the formula typeset in the middle, the same as one line of text below it, the type and unit on the right. The ribbon shows the Equation page while the tab is open.*

![Return loss over the sweep](tutorial/eq_rl_report.png)
*`RL` over the interpolating sweep (report `Return Loss` in the example): from 0.13 dB at 8 GHz over 0.41 dB at 10 GHz to 0.92 dB at 12 GHz. The post reflects most of the power, so the return loss is small.*

Equations can combine the network data in any way: `Y(1,1)`, `Z(1,1)` and `VSWR(1)` work as well, for
example `mismatch = 1 - |S(1,1)|^2` for the share of the power that enters the guide.

### 8.2 Power through the ports from the fields

The ports are fed with 1 W. The active power that flows through the port sheet is the flux of the real part
of the Poynting vector `S` through it.

1. In the Equations tab, type `P_in = ` and open **Integral > Flux through a face** on the ribbon. Hertzon
   switches to the 3D Modeler and a box asks for the face: click the sheet of port 1 at y = 0 (or choose
   `Port1Sheet` in the box) and press **Use**. Back in the Equations tab the name stands below the
   integral sign. Fill the box with **Function > Re** and the field **S**. As text this is
   `P_in = flux(Port1Sheet, re(S))`, which you can also type directly. **Save**.
2. The same for port 2: `P_out = flux(Port2Sheet, re(S))`. The flux counts along the normal of the sheet,
   here +y for both sheets, so both values are positive. `flip(Port2Sheet)` would count the other way.
3. **Results > Create Report > Create Fields Report > Data Table**, then add `P_out` next to `P_in`
   (category **Equations**). A Fields report computes its values from the saved fields, so it has the
   frequencies with fields: here the adaptive solution at 10 GHz, because the interpolating sweep stores
   only S-parameters. With a discrete or fast sweep with **Save fields**, a Fields report shows the value
   over frequency.

![The power through port 1 as a flux integral](tutorial/eq_pin.png)
*`P_in` typeset: the face below the integral sign, Re{S}·n dA. Type: scalar value, unit W, computed in the direction of the normal of `Port1Sheet`.*

![Power through both ports](tutorial/eq_power_table.png)
*Fields report `Port Powers` at 10 GHz: 91.8 mW enter at port 1 and 91.5 mW leave at port 2.*

> **What you should get:** with 1 W incident, the power that enters the guide is 1 W · (1 − |S11|²) =
> 89.8 mW, and the power that leaves at port 2 is 1 W · |S21|² = 89.8 mW. The flux integrals give 91.8 mW
> and 91.5 mW: the guide is lossless, so in and out agree to 0.4 %, and both agree with the S-parameters to
> 2 % on this coarse mesh of 1 376 tetrahedra. A tighter Maximum Delta S brings them closer.

### 8.3 A field of your own

1. Type `Sy = y(re(S))`, the y component of the active power density, and **Save**. Its type is
   **Scalar field** with the unit W/m².
2. **Results > More > Equations > Sy** (or right-click `Sy` in the project tree > **Plot Field...**): set
   **Plot on** to `XY` and **Cut position** to `b/2`, as for the Mag E plot of section 1.
3. **View > Orientation > Top**. The plot shows how the power flows from port 1 around the post to port 2.
   It is drawn on screen again whenever you change and save the equation.

![The power flow along the guide](tutorial/eq_sy_field.png)
*`Sy` on the cut plane halfway up the guide at 10 GHz (field plot `Sy_Cut` in the example): the power passes the post on both sides, up to 880 W/m².*

### 8.4 The library, functions and other designs

Right-click **Equations** in the project tree > **Open Library...** for ready-made equations and functions:
loss densities, energy densities, the RMS and instantaneous field, and functions such as `power(A)`,
`voltage(L)`, `current(C)`, `loss_diel(V)`, `loss_wall(A, sig)`, `energy_e(V)` and `q_diel(V)` (quality
factor from the dielectric loss). Tick one or more, or a whole group, and **Import**; they are copied into
the design and can be changed there. With the function `power`, `P_out = power(Port2Sheet)` gives the same
as above.

![The equation library](tutorial/eq_library.png)
*Open Library: the ready-made equations (field densities) and functions (integrals over a geometry passed as argument).*

A few more examples of what the language covers:

| Equation | Meaning |
|---|---|
| `W = energy_e(Waveguide) + energy_m(Waveguide)` | Stored energy in the guide (J), with functions of the library |
| `E_peak = max(Waveguide, \|E\|)` | Peak field strength in a volume (V/m) |
| `V = int_line(Radial, E)` | Voltage along a drawn line, e.g. between inner and outer conductor of a coax |
| `P_mid = flux(cut_xz(L/2), re(S))` | Power through a cut plane at y = L/2, no sheet needed |
| `p_wall = 0.5*rs(5.8e7 S/m)*\|Js\|^2` | Wall loss density of a copper surface (W/m²), with the surface resistance `rs(σ)` |

Numbers can carry units (`5.8e7 S/m`, `2mm`), and design variables can be used. Right-click **Equations** >
**Export Equations...** writes all equations and functions of a design into a file; **Import
Equations...** reads them into another design or project. AI assistants can create equations, reports
and field plots of them as well (MCP tools `set_equation`, `set_report`, `set_field_plot`).

---

## 9. A differential pair with the terminal solution

**Goal:** the differential S-parameters of an edge-coupled microstrip pair (100 Ω differential on 0.508 mm
RO4350B, 20 mm long). The finished project is `examples/DifferentialPair.hzn`.

1. **Simulation > Solution Type...**: choose **Terminal**. The S-parameters then refer to terminals (one per
   trace) instead of port modes.
2. **Design Properties**: `h = 0.508mm`, `w = 0.8mm`, `s = 0.4mm`, `L = 20mm`, `Wbox = 6mm`, `Hair = 3mm`.
3. Draw the box `Substrate` (Rogers RO4350B (tm)) and `Air` above it, both `Wbox` wide and `L` long, and the
   traces `TraceP` and `TraceN` as rectangles on top of the substrate, `w` wide with the gap `s` between them.
   Assign them **Perfect E**. The outer faces of the box are a perfect conductor, the ground of the lines.
4. Draw one sheet across each end of the box (Axis `Y`, `Wbox` by `h+Hair`) and assign a **Wave Port** to
   each. Hertzon finds the two traces on each port face and gives the port two terminals (`P1_TraceP`,
   `P1_TraceN`) with the box as reference, so it solves two modes: the even and the odd mode of the pair.
   Each mode leaves the model with its own propagation constant, so neither reflects at the port.
5. **Simulation > Excitations > Diff. Pairs**: pair terminal 1 with 2 and 3 with 4. The differential mode
   refers to 100 Ω, the common mode to 25 Ω.
6. **Analyze All** and plot `dB(S(Diff1,Diff1))` and `dB(S(Diff2,Diff1))`, the mode conversion
   `dB(S(Comm1,Diff1))`, and `Zo(i)` and `Gamma(i)` of the port modes (reports in the example). The
   differential return loss stays below −20 dB from 1 to 12 GHz and the conversion into the common mode
   below −36 dB.
7. The terminals are numbered in port order: 1 = `P1_TraceP`, 2 = `P1_TraceN`, 3 = `P2_TraceP`,
   4 = `P2_TraceN` (shown under the quantity in the report dialog). Reports can also name them:
   `dB(S(P2_TraceP,P1_TraceP))` is the same as `dB(S(3,1))`.
8. To see the differential field, open **Edit Sources** (right click Excitations): it lists the terminals.
   Set `P1_TraceP` to 0.5 W at 0° and `P1_TraceN` to 0.5 W at 180° (1 W in the differential mode), the
   other terminals to 0. Field plots and animations then show the differential mode (same phase: the common
   mode). No new solve is needed.

Changing the pairs needs no new solve: the mixed-mode quantities are computed from the terminal S-parameters.
Ports with several modes are named the same way in modal designs: `S(P1_M2,P1_M1)` is the coupling from
mode 1 to mode 2 of port P1, and a Floquet port uses its modes (`S(FP1_TM00,FP1_TE00)`).

---

## Where to go next

* `examples/` has fifteen finished projects, from a microstrip line to a Rotman lens, all with hzAURA
  and curved elements; most open with their reports and field plots. See the table in the
  [README](../README.md#examples).
* **Tools > Antenna Design > Rectangular Patch...** and **Tools > Filter Design > Tapped Hairpin...** create
  complete, parametrized designs with setup, sweep and optimization from a few specifications.
* **Results > Calculator** computes your own quantities from the fields and S-parameters
  ([section 8](#8-your-own-quantities-with-equations)); **Open Library...** under Equations has ready-made ones.
* [MESHER.md](MESHER.md) explains how adaptive meshing works. [FEATURE_MATRIX.md](FEATURE_MATRIX.md)
  shows what is implemented.
* **Tools > Solver Benchmark** times the projects of `examples/benchmark/scaling` on your computer and
  suggests the number of cores and sweep processes; its *Solver Comparison* tab times the matrix solvers
  (e.g. PARDISO, SPARTA on the CPU and on the GPU) with all cores.
* **File > Export > EMerge Python Script** turns any design into a stand-alone EMerge script.
* **File > Export > Gerber...** writes Gerber X2 and Excellon files of a board ([section 6](#6-gerber-export-of-a-printed-circuit-board)).
