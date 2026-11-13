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
* [Nonlinear devices](#nonlinear-devices)
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
  picture*, *Open 3D design* and the choice between its full-wave FEM model and its circuit model. Right-click on empty space: place a part, Paste, Fit All, Analyze.

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
| | Coupled Microstrip | W, S, L | symmetric pair, even and odd mode (Garg-Bahl), four pins; a pin connected to nothing is an open end with its fringing capacitance |
| Discontinuities | Tee, Cross, Corner, Mitered Bend (50 %), Step, Gap | W1..W4, S | closed-form junction models (Hammerstad, Kirschning-Jansen-Koster); the lines attach at their edges |
| Other lines | Coplanar Line | W, S, L, Backed | coplanar waveguide, conformal mapping with the thickness correction after Gupta and the dispersion after Frankel; *Backed* adds a ground plane under the substrate |
| | Via to Ground | D, N, P | N round vias of diameter D in a row at pitch P through the substrate: partial inductances with their images in the ground plane and the mutual coupling, skin-effect resistance of the barrels |
| | Stripline | W, L | centred strip between two ground planes spaced H |
| | Coaxial Line | Di, Do, Er, TanD, Cond, L | TEM with dielectric and conductor loss |
| Ideal (TEM) | Ideal Line, Ideal Open Stub, Ideal Short Stub | Z0, E, F | lossless, impedance and electrical length E at frequency F, no substrate |
| Data | Touchstone | File, Model | N-port from a `.sNp` file, ports against ground (or its circuit model, as a 3D design block) |
| | 3D Design | Design, Solution, Model | the solved S-parameters of a 3D design of the project, or its circuit model, see [below](#3d-designs-as-blocks) |

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

### Full-wave FEM or circuit model

Every 3D design block (and every Touchstone block) has a **Model**: its data (the full-wave FEM solution, or the
file) or a **circuit model**, a circuit design of this project with the same ports (matched by port name, else by
port number). Choose it in the block's Properties, or right-click the block > *Use Full-Wave FEM Model* /
*Use Circuit Model*. A block in a layout made with *Create 3D Layout* takes the circuit it was made from as its
circuit model.

The data are never extrapolated. When the analysis needs frequencies the data do not cover (for a harmonic
balance: up to the highest harmonic), the block switches to its circuit model and the analysis says so in a
warning; a block without a circuit model stops the analysis with an error that names the ranges. A bias point
needs DC, below the data: it comes from the circuit model if the block has one.

The colour of a block shows the model the next analysis takes:

| Colour | Meaning |
|---|---|
| blue, *FULL-WAVE FEM* (or *TOUCHSTONE*) | the data, which cover the analyses |
| red, *CIRCUIT* | the circuit model, as chosen |
| red dashed, *CIRCUIT (AUTO)*, gold mark | the data were chosen but do not cover the analyses: the circuit model stands in |
| grey, *NO MODEL*, crimson mark | no usable model (no data yet, or not covering the analyses and no circuit model): the analysis stops |

## Analyze and results

**Simulation › Sweep** sets the frequency sweep (1 to 10 GHz by default); **Analyze** (or F5) solves the network
at once. Right-click *Results* of the circuit design for a rectangular plot, a Smith chart or a data table of
the port parameters (`dB(S(2,1))`, `Z(1,1)`, `VSWR(1)`, ...). **Results › Touchstone** writes the S-parameters
as `.sNp`. Problems (no port, a port shorted to ground, a line without a substrate, a 3D block that is not
solved yet, a missing Touchstone file) are explained in the Message Manager.

## Nonlinear devices

Transistors and diodes go into the same schematic as the lines and the 3D blocks. **Who computes what:**

| Part of the work | Done by |
|---|---|
| Schematic, parts, lines, junctions, Touchstone and 3D blocks, the full-wave line model | Hertzon |
| The linear network: all linear parts reduced (Kron elimination) to one admittance matrix at the ports, the device pins and the DC sources, at DC and at every frequency | Hertzon |
| Small-signal S-parameters of the whole circuit about the bias point | Hertzon, with the device admittances from ngspice |
| DC bias point; small-signal admittance of each device (SPICE libraries of process design kits, model cards, compiled Verilog-A models) | [ngspice](https://ngspice.sourceforge.io) |
| Harmonic balance (single tone, swept drive power, harmonics, compression) and the two-tone mixer analysis (conversion loss, isolation) | [circuitRF](https://github.com/potatobeanradio/circuitRF), on Hertzon's linear network |

The engines see only Hertzon's reduced network (as conductances at DC for ngspice, as one Touchstone N-port at DC
and the harmonics for circuitRF), the devices, the DC sources and the ports. Both are separate programs the user
installs; Hertzon writes their input, runs them and reads their output (see [THIRD_PARTY.md](THIRD_PARTY.md)).

**Parts** (ribbon tab *Nonlinear*):

| Part | Properties |
|---|---|
| Nonlinear Device | *Kind*: **Subcircuit** (a `.subckt` of a SPICE Library, e.g. a transistor of a PDK), **Model card** (a `.model` of type D, NPN/PNP, NMOS/PMOS, NJF/PJF) or **Verilog-A** (*File*: `.va` source or compiled `.osdi`); *Model*: subcircuit, model or Verilog-A module name; *Pins*: the terminal names in the model's order (`c b e`, `d g s b`); *Params*: instance parameters (`w=10u l=0.13u ng=4`) |
| SPICE Library | *File* and *Section* as in `.lib File Section` (empty *Section*: `.include`); *OSDI*: compiled Verilog-A models the library needs, separated by `;` (for example the PSP model of IHP SG13G2) |
| DC Voltage Source, DC Current Source | the value; the top pin is positive (the current flows out of it). A voltage source is a short for the RF signal |

**Analyses** (properties of the setup):

* **S-Parameters** with devices: ngspice solves the bias point (the ports are their Z0 to ground, as an instrument
  is), then the small-signal admittance of each device about it; Hertzon adds those to its network and gives the
  S-parameters of the whole circuit. Results as for a linear circuit, plus `operating_point.json` (pin voltages,
  supply currents).
* **Harmonic Balance**: *Fundamental*, *Harmonics*, *Drive port* and *Available power* in dBm (one value, a list
  or `start:step:stop`). The drive port is a source of that available power, the other ports terminate in their
  Z0. Results: `harmonic_balance.csv` (power of every harmonic at every port, gain, DC supply currents), `.npz`
  (complex port voltages) and `.json`. Harmonic balance runs compiled Verilog-A models (Kind Verilog-A) and
  diode model cards (`.model ... D(...)`, mapped to circuitRF's diode; parameters it has no counterpart for, such
  as `IKF`, are listed in the messages); SPICE subcircuits and the other model cards run in the S-parameter
  analysis.
* **Mixer**: a two-tone harmonic balance with the LO and the RF at once. *LO port*, *LO power* (one value, a list
  or `start:step:stop`), *RF port*, *RF power*, *IF port*, the RF band (*RF start*, *RF stop*, *RF points*),
  *IF frequency* and *LO side* (low: f_LO = f_RF - f_IF; high: f_RF + f_IF). The LO and RF ports are sources of
  that available power, all other ports terminate in their Z0. *Mixing order* is the largest |k1| + |k2| of the
  products k1 f_LO + k2 f_RF: a single diode needs about 13 (order 7 was 1 dB off an ngspice transient, 11 within
  0.01 dB), a diode ring at 5 is within about 0.2 dB of 9 (at most 0.7 dB); the time grows fast with the order (ring: 0.7 s per point
  at 5, 3.6 s at 9, 9 s at 11). Every product must lie inside the data of the 3D and Touchstone blocks (up to
  order x the highest RF or LO frequency), since block data are never extrapolated. Results: `mixer.csv`
  (conversion loss, LO to RF, LO to IF, RF to IF and RF to LO isolation, IF power and a convergence flag for every
  LO power and RF frequency), `.npz` (power and complex voltage of every product at every port) and `.json`. A point that does not converge is flagged, not dropped.

**Setup.** Install ngspice 43 or later (older versions cannot load models compiled by current OpenVAF) and
circuitRF, and put them on `PATH` or set `HERTZON_NGSPICE` and `HERTZON_CIRCUITRF` to the programs. Verilog-A
sources are compiled with `openvaf-r` from OpenVAF-Reloaded on `PATH` (into `.hertzon-osdi` next to the source);
a compiled `.osdi` needs no compiler. For a PDK such as IHP SG13G2, give its `.lib` file and corner section and
the compiled models of its `.spiceinit` (OSDI).

**Checked against** (2026-11): a common-emitter stage with a VBIC transistor (Verilog-A) gave the same harmonics
in circuitRF's harmonic balance (0.6 s) and in an ngspice transient analysis (8.5 min): fundamental within
0.03 %, 2nd to 4th harmonic within 0.25 %. The small-signal S-parameters of an IHP SG13G2 NMOS (PSP, OSDI) and
HBT (VBIC) stage from 1 to 30 GHz match one ngspice AC analysis of the whole circuit within 1e-5. At low drive
the harmonic-balance gain equals S21 within 0.003 dB. A single-diode mixer with the HSMS-282x card
([examples/mixer/check_ngspice.py](../examples/mixer/check_ngspice.py), LO 3 GHz, RF 3.2 GHz) gives the same IF
power as an ngspice transient analysis within 0.13 dB at LO 0 to 10 dBm, and the same LO leakage within 0.001 dB.

**Example:** [examples/make_mixer.py](../examples/make_mixer.py) builds `Mixer_DBM.hzn`, a double-balanced diode
mixer for about 1.25 to 4 GHz on a standard 1.6 mm 4-layer board (RO4350B, RO4450F prepreg): two broadside
Marchand baluns in stripline as one 3D design (EMerge), an HSMS-2829 ring quad with the manufacturer's card,
0402 bypass capacitors, coplanar IF lines and an IF choke. Solve the design *Balun* first, then the setups *Band*
(conversion loss and isolation from 1 to 5 GHz) and *LODrive* (LO -3 to 13 dBm at 3 GHz).

**Not yet:** transient analysis (needs a time-domain model of the frequency-dependent network, e.g. vector
fitting), load pull, noise, harmonic-balance and mixer results in the report dialog (they are in the result
files), two-tone harmonic balance with transistor model cards and subcircuits, file pickers for the device and
library files.

### Example: a 2.4 GHz LNA that can be built

**File > Open Example > LNA_2G4** is a one-transistor amplifier for 2.4 to 2.5 GHz drawn as it is built: the
onsemi **NSVF4009SG4** (in production, AEC-Q101) on a 0.508 mm RO4350B board, 0402 parts, 5 V supply. The
schematic holds the 50 Ω feed lines, the traces at the transistor pins, the emitter traces with two vias per
emitter lead, the bias network and the parasitics of the parts (capacitors with series inductance and
resistance, wire-wound inductors with their loss and winding capacitance). It is made by
[examples/make_lna.py](../examples/make_lna.py).

| Part | Value | Purpose |
|---|---|---|
| C1, L1 | 3.6 pF, 1.2 nH | input: series resonant at 2.45 GHz; blocks DC and cuts the gain below the band |
| TL_e1, TL_e2, V_e1, V_e2 | 0.4 × 0.85 mm, 2 vias Ø 0.3 mm each | emitter degeneration: input match and stability |
| L2 | 6.8 nH | collector feed and shunt element of the output match |
| C2 | 3.9 pF | output DC block and series element of the output match |
| R3, C3 | 12 Ω, 68 pF | the feed is bypassed after R3: resistive load below the band (stability) |
| R1, R2 | 180 Ω, 24 kΩ | collector-feedback bias from 5 V: VCE = 3.0 V, IC = 10 mA |
| C4 | 1 µF | supply decoupling |

Capacitors C0G (e.g. Murata GJM15 series), inductors wire-wound 0402 (e.g. Murata LQW15/LQP15 series), resistors
0402 1 %. The values are E-series values; one step up or down in any of them keeps S11 and S22 below −13 dB and
μ above 1.04.

| Who | What |
|---|---|
| Hertzon | schematic, microstrip lines, junctions and vias, the parts, the reduction of the board to the transistor pins, the ports and the supply; S-parameters of the board with the measured transistor data (design *LNA_Measured*) |
| ngspice | bias point and small-signal admittance of the transistor (setup *SParameters_ngspice*) |
| circuitRF | harmonic balance at 2.45 GHz, −30 to 0 dBm (setup *Compression_circuitRF*) |

Both engines run the same transistor model: [examples/models/nsvf4009sg4.va](../examples/models/nsvf4009sg4.va),
onsemi's Gummel-Poon parameters and package values from the data sheet on Hertzon's Verilog-A Gummel-Poon
([examples/models/README.md](../examples/models/README.md) has the checks against ngspice's own model and
against onsemi's measured S-parameters).

| Result (2.4 to 2.5 GHz) | Model (Hertzon + ngspice, circuitRF) | Measured transistor data (Hertzon) |
|---|---|---|
| Gain S21 | 13.2 to 12.9 dB | 13.6 to 13.3 dB |
| S11, S22 | ≤ −14.3 dB, ≤ −14.7 dB | ≤ −14.0 dB, ≤ −13.9 dB |
| Stability μ (the smaller of μ and μ′) | ≥ 1.05 from 0.3 to 12 GHz, ≥ 1.0006 below | ≥ 1.07 from 0.5 to 8 GHz (data range) |
| Bias | VCE 3.01 V, supply current 10.3 mA | |
| Output 1 dB compression | 6.1 dBm (input −5.9 dBm) | |
| 2nd, 3rd harmonic at −20 dBm in | −49 dBc, −67 dBc | |

The gain is highest near 1 GHz (17 dB), as usual for a single bipolar stage; put a band-pass filter in front
where strong signals below the band are expected. The noise figure is not computed (Hertzon has no noise
analysis yet); onsemi gives 1.1 dB at 2 GHz for the transistor.

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
minute), then every round solves the 3D design once, fits the circuit to all solved S-parameters (which
circuit values give the same response) and moves the 3D variables so that the fitted values reach the circuit
optimum (aggressive space mapping with a Broyden update, Bandler et al.). The error of the circuit (coupling,
junctions, open ends) is corrected by the 3D solves; a few rounds are enough when the circuit has the right
shape of response. **Tools › Design Wizard › Hairpin bandpass** adds the filter as a circuit
(`Hairpin_N3_Circuit`: coupled lines for the arms of neighbouring hairpins, tee and open stub at the taps,
mitered bends) and the optimization *HairpinSM* on L, dL, g and yP. On the third-order example the circuit
lies within about 1 % of the 3D response, and *HairpinSM* reached the cost of the circuit optimum (0.31) in
three 3D solves, about 13 minutes on 4 cores, where the Derivative Trust Region needed 4.5 hours for 0.60.
The schematic is drawn like the filter seen from above: the U of every hairpin, the coupled arms side by side,
the feeds at the taps. The dialog shows, while the values change, the ideal Chebyshev response of N resonators
with the loss of the board, the circuit of the start design and the mask; its table of N and **Suggest N** help
choosing the number of resonators, and **Create and Optimize** creates the design and starts *HairpinSM*.

**Tools › Design Wizard › Hairpin diplexer / triplexer** builds two hairpin filters on a T-junction, stacked as in most
published hairpin diplexers: the common Port 1 at the left edge, the T right behind it, channel A above and channel
B below, Port 2 and Port 3 at the right edge on the tap lines of their rows. Each channel has its own length, outer
length offset, gaps, tap and junction line (`L_A`, `dL_A`, `g_A`, `g2_A`, `yP_A`, `j_A`, ...); trace width, inner
width and miter are shared. Each junction line runs up (A) or down (B) to the tap line of its row (`v_A`, which
keeps the rows `gr` apart), turns and runs `ja_A` to the row, `v_A + ja_A = j_A`; the circuit has the same line,
mitered bend and line. When both lines are long enough, the rows hang from their tap lines towards the middle and
the lines climb along them (a narrower board), otherwise they stand away from the middle. The junction lines start
where each filter looks like an open circuit at the junction in the band of the other channel (from the circuit of
that channel alone). The diplexer comes with its circuit (`Diplexer_N3_N3_Circuit`) and the optimization
*DiplexerSM* on both channel masks (insertion loss, ripple, return loss at the common and the channel port,
rejection in the other channel and in the outer stopband) and the isolation S32 in both passbands. *Junction:
Common resonator* replaces the T-junction by a hairpin shared by both filters (`L_0`, `g_0A`, `g_0B`, `yP_0`),
tapped by Port 1 from inside its U: one row of hairpins on a smaller board. *Third channel* makes a triplexer: A
and B hang below the line of the ports, the third filter is turned downwards on a cross junction to Port 4, with
isolation goals for every pair of channel ports.

A round whose 3D solve is worse than the best one goes back to it with half the step (at most three times in a
row), so a circuit that is only roughly right does not lead the 3D design away.

### Multi-fidelity: optimize a circuit and correct it with its 3D layout

The same method starts from the circuit: create the layout (**Create 3D Layout**, below), set up its analysis,
and switch on **Multi-fidelity: correct with the 3D layout** in the Optimizer tab of the circuit's
optimization. No mapping of variables is needed: the layout keeps the variables of the schematic under their
names (`MS1_L` = `ms_len`, the coordinates are expressions of them), so a circuit variable is the same
variable in 3D. The goals, variables and ranges of the circuit optimization are used; the circuit is optimized
first (milliseconds per design), then every round solves the layout once (one 3D solve per round, typically
three to six rounds, *Maximum rounds* and *Mapping tolerance* as above). *3D layout design* and *3D setup :
sweep* are found by themselves (the layout made from this circuit, its first sweep) and can be entered. The
other circuit variables take their circuit values in the 3D solves too. When the schematic was edited after
the layout was made, the run warns: use **Update 3D Layout** first. The evaluations of the results window are
the 3D solves; **Apply Best Variables** sets the values in the circuit and in the layout, and the 3D solution
of the best evaluation becomes the layout's solution without solving again.

Suited are the dimensions the circuit models: lengths, widths, gaps, tap positions and part values; with more
than about eight variables the fit of the circuit to the 3D response is no longer unique.

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
connection; Touchstone blocks without a footprint, attenuators, coaxial lines and DC feeds have no layout
and are left out. The
coplanar line is drawn as its centre strip only for now. The Message Manager lists everything that was changed
or left out.

**Update 3D Layout** (schematic → 3D) rebuilds the layout from the schematic as it is now, with parts added,
removed or exchanged; its setups and reports stay, the variables of removed parts go. **Update Schematic**
(3D → schematic) copies the dimensions changed in the layout back, also trace sizes edited directly on the
objects. A click on either button takes the newest layout of the schematic, the drop-down arrow chooses one.

### Footprints: parts on their pads

A part that goes on the board (R, L, C, the RLC networks, a nonlinear device, a Touchstone block) can carry a
**footprint** (Component dialog, group *Footprint*):

* **Footprint**: a KiCad library id `Library:Name` or a `.kicad_mod` file. KiCad's files are the common format:
  SamacSys (Component Search Engine), SnapEDA, Ultra Librarian and the distributors deliver it, so their downloads
  can be used as they are. A library id is looked up in the project folder, the *KiCad library folders* of
  **Tools › Options › General**, `HERTZON_KICAD_LIBS` and the folders of a KiCad installation (`Name.pretty`).
  Only where no file is found, a built-in package of the same name is used (chip R, C, L 0201 to 1206, SOT-23,
  SOT-323, SOT-343, QFN, and SC-82FL as onsemi recommends it): a typical land pattern, and the dialog says so. A file picked from outside the project
  is copied into its `footprints` folder.
* **Symbol** (optional): a KiCad symbol (`Library:Name`, `file.kicad_sym:Name`, also `.kicad_symdir`
  libraries) that names the pins of the pads.
* **Pads**: which pads belong to which terminal of the model, for example `b=3; c=1; e1=2; e2=4` for a
  transistor with two emitter pins or `d=5,6,7,8; g=4; s=1,2,3,17` for a MOSFET in a QFN (17 the exposed pad).
  `NC=...` marks pads that stay open on purpose. Empty: assigned automatically, pad 1 and 2 of a two-terminal
  part, otherwise by the pin names of the symbol (D/Drain, G/Gate, S/Source, B/Base, C/Collector, E/Emitter,
  ...; an exposed pad the symbol does not name goes with the source or emitter).

The dialog draws the footprint to scale, seen from the top, every pad coloured by its terminal, and lists what
does not fit: a terminal without a pad or a pad that does not exist (errors), pads left open (warning).

With footprints, **Create 3D Layout** builds the board as it is soldered:

* a two-terminal part gets its two pads as copper, the line attaches at the outer edge of the pad, the Lumped
  RLC sheet fills the gap between the pads in pad width; a shunt part to ground gets its ground pad with vias
  of `Via_D` (0.3 mm) to the ground plane (reported: they are not in the schematic);
* a nonlinear device or a Touchstone block is turned in steps of 90 degrees so that the pad of the terminal the
  line reaches faces that line (or by the part's `LayoutRot`), moved so that the pad edge touches the line end;
  the lines of its other terminals leave from the edge of their pad that faces the next part in the schematic;
  plated holes of the footprint (thermal vias of an exposed pad) become vias;
* **every pad of a device gets a Lumped Port of its own** to the ground plane, at its inner edge, named
  `Q1_b_pad3` (part, terminal, pad); a DC source gets a port at the end of its line (`VCC_supply`). The layout
  remembers which port stands for which part, terminal and pad;
* copper of two different nets that lies on each other is reported (the schematic is not drawn as the board),
  and a loop of the circuit that the parts do not close is closed by a thin trace of `Route_W` (reported).

Pads are laid out as their bounding rectangle (round corners and ovals as rectangles). A device without a
footprint is reported and left out, with the parts behind it.

**Board Circuit** (Simulation › 3D Layout) then makes a new circuit design: the layout as a 3D design block,
and every pad port connected by a net label to the terminal of its device (several pads of one terminal in
parallel, as on the board), the power ports at theirs, the DC sources at their supply ports, with the setups,
variables and SPICE libraries of the schematic. After the layout is solved, this design simulates the board
with the devices: S-parameters with the bias of ngspice, harmonic balance with circuitRF. Checked end to end:
lines and a transistor (measured S-parameters on a SOT-23), laid out, solved in 3D and put back on its pad
ports, give the S-parameters of the schematic within 0.1 dB in S21 from 1 to 4 GHz
(`tests/test_circuit_layout.py`).

The full-wave data start above 0 Hz, but the bias point needs the DC paths of the board (supply, chokes, bias
resistors, DC blocks). Board Circuit therefore also makes **`<name>Model`**: the schematic without its devices
and DC sources, with a port where each port of the layout is, and sets it as the circuit model of the block. The
bias point takes the block at 0 Hz from it; the RF analysis takes the solved data.

### Board Layout: placing the parts

A schematic is drawn for the circuit, not for the board: bias parts, feedback loops and parallel branches drawn
side by side would land on each other in 3D. **Board Layout** (Simulation › 3D Layout) shows the board of the
schematic in 2D, seen from the top, and lets the parts be placed:

* the first time, every part is placed automatically as the schematic is drawn (also **Auto Place**): lines
  attach at the pad edge that faces them, a transistor turns so that the pad of the line's terminal faces it;
* drag a part, **R** turns the selected parts by 90 degrees; a pin dropped near a pin of the same net snaps onto
  it;
* connections the copper does not make are drawn as dashed lines (ratsnest), copper of different nets on each
  other is outlined in red, the status line counts both;
* **Track** draws copper the schematic does not have (DC routing): click the corners, double-click or right-click
  to end; Del removes a selected track.

Each change is one undo step and is stored with the circuit design. Once a board has been placed, Create 3D
Layout builds the 3D design as placed, with the same ports and port map as above, so Board Circuit works the
same; open connections, overlaps and tracks are reported. The dimensions are still design variables, but a
line made longer in the schematic does not push the parts behind it: the ratsnest shows the gap. A shunt part on
its footprint gets vias of 0.3 mm in its ground pad (reported). Vendor downloads in the KiCad 5 format
(`module`) are read too, and pin names such as `EMITTER_1`, `EMITTER_2` are matched to `e1`, `e2` (or both to `e`).

### Connecting subdesigns automatically

Finished structures (a Wilkinson divider, a rat-race, a filter of the Design Wizard, any 3D design whose ports
are vertical sheets on its edge) are placed in the schematic as 3D design blocks and wired as they belong
together. On the board each block is one part with its copper seen from the top, its lumped resistors in red and
a pin at every port; ports at an angle (the feeds of a rat-race) keep their angle. Place the blocks roughly, then:

* **Connect** lays a line for every connection still missing (only for the selected parts, if any are selected):
  microstrip of the impedance **Z0** on the circuit's substrate (its width from the closed-form line model of the
  circuit simulation at f0), led at right angles round the other copper at a gap of 3 line widths where there is
  room, with few bends. **Arc** bends are circular with a radius of 3 line widths (smaller where a jog leaves less
  room), **Miter** bends are 50 % mitered; a bend from a slanted port is always circular. Where a pin is more
  than 2 % wider or narrower than the line, a linear taper of 3 line widths joins them, so the impedance has no
  step. Hover a route for its length and its phase at f0.
* **Match Phase** gives the selected routes the same insertion phase at **f0**, or a set difference: the dialog
  asks for each route's phase relative to the group (0 for all: equal phase; +90 degrees: this route lags by 90
  degrees more, as for a feed of a circularly polarized antenna). Each route that is short of its target gets a
  meander (a row of U-shaped detours) in its longest straight piece, its height first from the electrical length
  and then corrected on the route's circuit model to within 0.2 degrees. Matching again starts from the routes
  without their meanders.
* Double-click a bend of a route to set its radius, or a 50 % miter for a 90 degree bend (for that bend or all
  bends of the route); where the straight pieces leave less room, the radius is made smaller and the status line
  says so.
* A route whose part was moved is outlined in red and no longer counts as a connection; select it, Del, and
  Connect again.

The routes are part of the circuit: the S-parameter and harmonic-balance analyses replace the ideal connection of
the schematic by the chain of the route (its straight pieces as MLIN, the tapers as five MLIN steps, a mitered
bend as MMBEND, a circular bend as an MLIN of its arc length), and say so in their messages. Create 3D Layout
copies every block's 3D design into the layout, turned and moved as placed (its own ports go: the board connects
it), with the routes as copper, so the full-wave check of the whole system is one click away.

**File > Open Example > VNA_TestSet_2G4** shows it on a whole board: the reflectometer test set of a two-port
network analyzer from a Wilkinson divider and two coupled-line couplers of the Design Wizard, with the lines to
the receivers R, A and B matched in phase. Move a block and press Connect and Match Phase again.

## AI assistants

The MCP server covers circuit designs: `create_circuit`, `get_circuit`, `edit_circuit` (parts, wires, 3D
design and Touchstone blocks), `run_simulation`, `get_results` and `render_image` on a schematic,
`circuit_layout` (create or update the 3D layout, or copy it back to the schematic) and `import_3d_designs`;
`set_optimization`, `run_simulation(optimization=...)` and `apply_optimization` work on circuits as on 3D designs.
See the [tutorial](TUTORIAL.md#5-let-an-ai-assistant-build-and-run-it-mcp).

## Not yet

Transient analysis and two-tone harmonic balance (see [Nonlinear devices](#nonlinear-devices)); pushing the port voltages of the schematic back into
a 3D design (fields of the loaded structure); SPICE netlist import and export; a full coplanar layout (now
the centre strip only). See [ROADMAP.md](ROADMAP.md#step-3-circuit-design).
