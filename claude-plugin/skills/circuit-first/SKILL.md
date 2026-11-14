---
name: circuit-first
description: Multi-fidelity workflow for planar circuits in Hertzon (filters, couplers, dividers, matching and feed networks, also structures the Design Wizard does not know yet, and active circuits such as LNAs): design and optimize in the circuit first, then check and correct in full wave with a few 3D solves. Use together with hf-design-loop for anything a circuit can describe.
---

# Circuit first, full wave second

Use this with the design loop (skill `hf-design-loop`). A circuit evaluation takes milliseconds, a 3D solve
minutes. So the circuit does the searching, and full wave only checks and corrects. Never optimize a planar
circuit with 3D solves alone.

## 1. Known structure: the Design Wizard

`list_structures()` lists what Hertzon can synthesize (couplers, dividers, matching networks, filters,
antennas). For an available one, `create_structure()` with the specification builds the circuit, its 3D layout
with the same variables and an optimization on the circuit with multi-fidelity. Run that optimization directly
with `run_simulation()` and `optimization=...`, then `apply_optimization()`. Keep the setups it made.
Hairpin filters and diplexers have their own assistants (`create_hairpin_filter()`, `create_hairpin_diplexer()`,
optimizer Space Mapping).

## 2. New structure: build the circuit yourself

When the wizard does not know the structure (a filter type, a coupler variant, a network of several parts):

1. Synthesize it: order and element values from the specification (for filters the low-pass prototype,
   impedance or coupling values, then line widths and lengths). Write the synthesis into the design log.
2. `create_circuit()`, then `edit_circuit()` with microstrip/stripline/coplanar parts (lines, tees, bends,
   coupled lines, open ends, gaps, lumped parts). Make every dimension a design variable, with names you will
   also use in 3D. `get_circuit()` shows unconnected pins; fix them first.
3. Solve the circuit (`run_simulation()` works on circuit designs and returns at once, `get_results()` kind
   `sparameters`) and compare it with the synthesis. Then optimize it on the circuit with `set_optimization()`
   (goals on its S-parameter setup; Conjugate Gradient near the start, Latin Hypercube + Nelder-Mead when the
   start is far off). This is cheap: use as many evaluations as it takes.
4. Note what the circuit models cannot do: coupling between more than two lines, radiation, box modes,
   discontinuities without a model. These decide how far the 3D result will move.

## 3. Full wave: check and correct

1. `circuit_layout()` action `create` turns the schematic into a 3D design with the same variables. Look at it
   with `render_image()`: lines must meet, gaps must be open, ports at the board edges.
2. Set the circuit optimization to multi-fidelity (`set_optimization()` props `MultiFidelity` True; FineModel
   and FineSolution default to the layout and its first sweep) and run it: every round solves the layout once in
   3D, fits the circuit to it and moves the variables. Typically 3 to 6 3D solves.
3. If the 3D result stays off, calibrate instead of optimizing in 3D: `circuit_layout()` action `calibrate`
   (Cal_L, Cal_W, Cal_Er) or replace a part the circuit cannot model by a 3D design block, then optimize the
   circuit again.
4. Take the result with `apply_optimization()`: it sets the values in the circuit and in the layout.

The 3D setups stay as Hertzon makes them (see `hf-design-loop`): no changes to Delta S, passes or mesh.

## 4. Systems of several parts

Design each part (filter, coupler, divider, amplifier) on its own as above, then place them as blocks on the
board, connect them and check the whole board once in 3D. A shipped example to start from:
`open_example()` `VNA_TestSet_2G4` (Wilkinson and two couplers, equal phase on three paths).

## 5. Active circuits

Amplifiers and mixers are circuit designs with transistor models or Touchstone data (examples `LNA_2G4`,
`Mixer_DBM`). Check stability (mu >= 1 over the whole band, not only in band) before gain and match; check the
bias point, then the matching networks, then large-signal results (P1dB, harmonic balance) where they matter.
The passive parts around them (matching lines, bias feeds) go through full wave as in section 3. Say plainly
what Hertzon does not compute yet (for example the noise figure), instead of estimating it.
