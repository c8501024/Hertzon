# Circuit designs

A circuit design is a schematic of a linear network next to the 3D designs of the same project: lumped parts,
transmission lines on a substrate, Touchstone files and the solved 3D designs of the project as S-parameter
blocks. **Analyze** solves the network over the frequency sweep in a moment, and the results go where the
results of a 3D design go, so the S-parameter reports, Smith charts, data tables, Touchstone export and
Solution Data work the same way.

A schematic also turns into a **3D layout**: a new 3D design with the substrate, the traces, vias, lumped parts
and ports, every dimension a design variable. The layout is solved full-wave, tuned in 3D if needed, and the
changed dimensions go back into the schematic.

The [tutorial](TUTORIAL.md#11-a-stub-filter-as-a-circuit-and-its-3d-layout) builds a stub filter step by step.

## Contents

* [Create a circuit design](#create-a-circuit-design)
* [The schematic](#the-schematic)
* [Parts](#parts)
* [Junctions of microstrip lines](#junctions-of-microstrip-lines)
* [Line calculator and properties](#line-calculator-and-properties)
* [Line model: closed form or full wave](#line-model-closed-form-or-full-wave)
* [3D designs as blocks](#3d-designs-as-blocks)
* [Analyze and results](#analyze-and-results)
* [Optimization](#optimization)
* [3D layout](#3d-layout)
* [AI assistants](#ai-assistants)
* [Not yet](#not-yet)

## Create a circuit design

**File > New** opens the start page: choose **Circuit**, then **Linear Network**, and click **Create**.
**Project > Insert Design** shows the same choice, so one project holds 3D and circuit designs side by side.
Transient and harmonic balance are shown on the start page but greyed out (not implemented yet).

A circuit design has its own ribbon instead of the 3D one:

| Tab | What is on it |
|---|---|
| Desktop | save, new, open, undo and redo, Insert Design, Copy Design, project and design variables |
| Schematic | Select, Wire, Rotate, Mirror, Delete; Ground, Power Port, Net Label; Fit All, zoom |
| Lumped Elements | R, L, C, series and parallel RLC, attenuator, DC block, DC feed, Touchstone |
| Transmission Lines | substrate, microstrip lines and stubs, coupled microstrip; *Line Model › Full Wave*; *Discontinuities › Automatic* and the junction parts; coplanar line, Via to Ground, stripline, coaxial line; ideal (TEM) lines and stubs |
| Data | Touchstone, 3D Design (a design of this project, or *From Project File...*) |
| Simulation | Sweep, Add Sweep, Design Variables, Analyze; *Create 3D Layout*, *Update 3D Layout*, *Update Schematic* |
| Results | Rectangular Plot, Smith Chart, Data Table, Solution Data, Touchstone export |

## The schematic

The part library next to the schematic lists every part with a search field. Drag a part into the schematic,
or double-click it (or click its ribbon button) and click to place it; the part follows the mouse until you
click. While placing, **R** rotates and **M** mirrors; right-click or **Esc** ends placing.

| Key | In the schematic |
|---|---|
| W | draw a wire: click the corners, a click on a pin or on another wire ends it (also a double-click or right-click) |
| R / M | rotate by 90 degrees / mirror the selected parts |
| G | place a ground |
| Del | delete the selected parts and wires (also when the focus is in another panel) |
| Ctrl+C / Ctrl+V | copy and paste the selected parts with their values and the wires between them |
| F | fit the schematic into the view |
| Esc | end wire drawing or placing, back to Select |

* **Wires follow their parts.** Drag a part and its wires stay attached; an elbow is added where a wire would
  become slanted. A pin that sat on a wire or another pin gets a new wire.
* **Connections**: a wire ends on a pin or on another wire (a T junction). Parts whose pins touch are
  connected too. A **Net Label** connects every point with the same net name without a wire (a label named
  `GND` or `0` is ground).
* **Copy and paste**: Ctrl+C / Ctrl+V (also in the Edit menu, the ribbon and the right-click menu). The copies
  land at the mouse pointer (otherwise shifted a little), pasting again steps on. Names and port numbers stay
  unique, and copied lines keep their substrate: pasted into another circuit design, the substrate comes
  along. The clipboard works across circuit designs.
* **Values** are shown at each part and accept expressions with units and design variables (`Wline`,
  `lambda_g/4`, `2*L1`). **Simulation > Design Variables** defines them.
* **Right-click** a part: Properties, Rotate, Mirror, Copy, Delete; on a 3D design block also *Update ports and
  picture* and *Open 3D design*. Right-click on empty space: place a part, Paste, Fit All, Analyze.

## Parts

| Group | Part | Values | Model |
|---|---|---|---|
| Connect | Power Port | Num, Z0 | port of the S-parameters: a source with internal resistance Z0 against ground; Num sets the port order (empty: by name) |
| | Ground, Net Label | Net | reference node; connection by name |
| Lumped | Resistor, Inductor, Capacitor | R, L, C | ideal |
| | Series RLC, Parallel RLC | R, L, C | R, L and C in series or in parallel |
| | Attenuator | Loss, Z0 | matched resistive pi network |
| | DC Block, DC Feed | | ideal: open at DC and short above / short at DC and open above |
| Microstrip | Substrate | Er, H, T, TanD, Cond | used by every line that names it in *Subst*; several substrates per schematic are fine |
| | Microstrip Line | W, L | Hammerstad-Jensen, dispersion after Kirschning-Jansen, conductor and dielectric loss |
| | Microstrip Open Stub, Short Stub | W, L | as the line, open end with its length extension; the short includes the inductance of the via of line width that shorts it |
| | Coupled Microstrip | W, S, L | symmetric pair, even and odd mode (Garg-Bahl), four pins |
| Discontinuities | Tee, Cross, Corner, Mitered Bend (50 %), Step, Gap | W1..W4, S | closed-form junction models (Hammerstad, Kirschning-Jansen-Koster); the lines attach at their edges |
| Other lines | Coplanar Line | W, S, L, Backed | coplanar waveguide, conformal mapping with the thickness correction after Gupta and the dispersion after Frankel; *Backed* adds a ground plane under the substrate |
| | Via to Ground | D, N, P | N round vias of diameter D in a row at pitch P through the substrate: partial inductances with their images in the ground plane and the mutual coupling, skin-effect resistance of the barrels |
| | Stripline | W, L | centred strip between two ground planes spaced H |
| | Coaxial Line | Di, Do, Er, TanD, Cond, L | TEM with dielectric and conductor loss |
| Ideal (TEM) | Ideal Line, Ideal Open Stub, Ideal Short Stub | Z0, E, F | lossless, impedance and electrical length E at frequency F, no substrate |
| Data | Touchstone | File | N-port from a `.sNp` file, ports against ground |
| | 3D Design | Design, Solution | the solved S-parameters of a 3D design of the project, see [below](#3d-designs-as-blocks) |

Against full-wave solves: a shorted 18 mm microstrip line with one and with two 0.4 mm vias agrees within
3 degrees rms in the S11 phase from 1 to 10 GHz; a stub notch filter with the tee model agrees within 0.4 %
(with an ideal node instead of the tee: 2 %).

## Junctions of microstrip lines

With **Transmission Lines › Discontinuities › Automatic** on (the default), Hertzon finds the junctions where
microstrip lines meet and models them with the widths of the lines:

| Lines that meet | Junction |
|---|---|
| two at a right angle | corner |
| two in line with different widths | step |
| three | tee |
| four | cross |

A diamond marks each automatic junction where the wires meet; its tooltip names the model and the lines. A
junction part placed by hand takes precedence, and with *Automatic* off the lines meet at an ideal node unless
a junction part is placed. The 3D layout uses the same junctions.

## Line calculator and properties

Select a part to edit it in the **Properties** panel, or double-click it for its dialog. The dialog of a
microstrip line, stub or stripline has a **Line calculator**:

* *Now* shows Z0, the effective permittivity and the electrical length of the line as it is, updated while you
  type, at the middle of the sweep (or the frequency you enter).
* Enter an impedance, an electrical length and a frequency and press **Calculate W and L**. For an open stub
  the end extension is subtracted from the length.

The tooltip of a line shows the same values at the middle of the sweep.

## Line model: closed form or full wave

**Transmission Lines › Line Model › Full Wave** switches the microstrip lines and stubs and the coplanar lines
from their closed-form models to a full-wave solve of their cross-section: the port mode solver runs on the
substrate and widths of the schematic, metal thickness included, in a shielded box, at eight frequencies up to
the highest sweep frequency. It gives the dispersion, the impedance and the dielectric loss; the conductor loss
is added from the surface resistance. The length is applied in the circuit, so changing a length needs no new
solve. Each cross-section is solved once (about 10 s) and cached next to the project results. Junctions,
coupled lines and the open-end corrections keep their closed-form models.

## 3D designs as blocks

* **Drag a 3D design** from the Project Manager into the schematic (or **Data › 3D Design** and pick it). It
  becomes a block with one pin per port and a picture of the structure. Its solved S-parameters are used in
  the circuit; analyze the 3D design first. After a change of the 3D design, right-click the block >
  **Update ports and picture**.
* **3D designs from another project**: **Data › 3D Design › From Project File...**, or drag a `.hzn` file into
  the schematic. The 3D designs with ports of that project are copied into this one with their project
  variables, materials and solved results, and placed as blocks.

## Analyze and results

**Simulation › Sweep** sets the frequency sweep (1 to 10 GHz by default); **Analyze** (or F5) solves the network
at once. Right-click *Results* of the circuit design for a rectangular plot, a Smith chart or a data table of
the port parameters (`dB(S(2,1))`, `Z(1,1)`, `VSWR(1)`, ...). **Results › Touchstone** writes the S-parameters
as `.sNp`. Problems (no port, a port shorted to ground, a line without a substrate, a 3D block that is not
solved yet, a missing Touchstone file) are explained in the Message Manager.

## Optimization

Part values written as design variables (for example `L` = `ms_len` in a line, with `ms_len` = `12mm` under
**Simulation › Design Variables**) can be optimized as in a 3D design: **Simulation › Optimization** (or
right-click *Optimization* in the Project Manager) sets the variables with their ranges, the goals (also the
filter mask) and the optimizer. Every evaluation is a circuit analysis in the optimizer process, a few
milliseconds instead of a 3D solve, so thousands of designs take seconds. **Conjugate Gradient** and **Latin
Hypercube + Nelder-Mead** are offered; the **Derivative Trust Region** needs the tuning model of a 3D mesh and
is for 3D designs only. The goals are evaluated on a frequency sweep of the circuit. **Apply Best Variables**
sets the variables and analyzes the circuit again; **Create 3D Layout** then takes the optimized values into
the 3D design as the start for a full-wave check or a final optimization there. With the full-wave line model,
new cross-sections (changed widths) are solved and cached during the run.

### Space mapping: the circuit as the fast model of a 3D design

A 3D optimization with the optimizer **Space Mapping** uses a circuit design with the same variable names as
its coarse model (Optimizer tab: *Circuit design*). The goals are first optimized on the circuit (about a
minute), then every round solves the 3D design once, fits the circuit to the solved S11 and S21 (which circuit
values give the same response) and moves the 3D variables so that the fitted values reach the circuit
optimum (aggressive space mapping with a Broyden update, Bandler et al.). The error of the circuit (coupling,
junctions, open ends) is corrected by the 3D solves; a few rounds are enough when the circuit has the right
shape of response. **Tools › Filter Design › Tapped Hairpin** adds the filter as a circuit
(`Hairpin_N3_Circuit`: coupled lines for the arms of neighbouring hairpins, tee and open stub at the taps,
mitered bends) and the optimization *HairpinSM* on L, dL, g and yP. On the third-order example the circuit
lies within about 1 % of the 3D response, and *HairpinSM* reached the cost of the circuit optimum (0.31) in
three 3D solves, about 13 minutes on 4 cores, where the Derivative Trust Region needed 4.5 hours for 0.60.

## 3D layout

**Simulation › 3D Layout › Create 3D Layout** builds a new 3D design from the schematic:

* the substrate with its ground plane, the traces end to end in the directions they have in the schematic
  (right is +x, up is +y), starting at the first power port;
* open and shorted stubs (shorted by a via of the line width), coupled lines, the junctions (tee, cross,
  corner, bend, step, gap), *Via to Ground* parts as round vias just inside the end of the line, a line whose
  far end is grounded is shorted there by a via;
* R, L, C and the RLC networks as **Lumped RLC** sheets in a gap of the trace, or from the trace to ground for a
  shunt part;
* the power ports as **Lumped Ports** on the board edge, with the sweep of the circuit;
* **3D design blocks with their geometry**: each block is turned in steps of 90 degrees and moved so that the
  lines attach exactly at its ports. The ports a line connects to are removed; a port that only a Power Port
  connects to stays a port of the layout. Objects, boundaries, mesh operations and design variables come along
  with the block name as prefix (`EM1_W`), the air region and open boundaries of the block are left out, the
  board and ground plane reach over the block. This works for blocks whose ports are vertical lumped or wave
  ports on the edge of the design, on a substrate of the same height (otherwise a warning).

Every dimension is a design variable named after its part (`MS1_W`, `MS1_L`, `Sub1_H`), so the layout can be
swept, optimized or tuned with sliders. Ideal lines become microstrip of the same impedance and electrical
length (an ideal short stub is laid out shorter by the length of its via); a DC block becomes a direct
connection; Touchstone blocks, attenuators, coaxial lines and DC feeds have no layout and are left out. The
coplanar line is drawn as its centre strip only for now. The Message Manager lists everything that was changed
or left out.

**Update 3D Layout** (schematic → 3D) rebuilds the layout from the schematic as it is now, with parts added,
removed or exchanged; its setups and reports stay, the variables of removed parts go. **Update Schematic**
(3D → schematic) copies the dimensions changed in the layout back, also trace sizes edited directly on the
objects. A click on either button takes the newest layout of the schematic, the drop-down arrow chooses one.

## AI assistants

The MCP server covers circuit designs: `create_circuit`, `get_circuit`, `edit_circuit` (parts, wires, 3D
design and Touchstone blocks), `run_simulation`, `get_results` and `render_image` on a schematic,
`circuit_layout` (create or update the 3D layout, or copy it back to the schematic) and `import_3d_designs`;
`set_optimization`, `run_simulation(optimization=...)` and `apply_optimization` work on circuits as on 3D designs.
See the [tutorial](TUTORIAL.md#5-let-an-ai-assistant-build-and-run-it-mcp).

## Not yet

Transient analysis, harmonic balance and nonlinear parts; pushing the port voltages of the schematic back into
a 3D design (fields of the loaded structure); SPICE netlist import and export; a full coplanar layout (now
the centre strip only). See [ROADMAP.md](ROADMAP.md#step-3-circuit-design).
