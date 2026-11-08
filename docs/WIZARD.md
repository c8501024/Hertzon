# Design Wizard

**Tools › Design Wizard** turns a specification into a ready design: couplers, dividers, matching networks,
filters and antennas. For a planar structure it creates

* a **circuit design** with named design variables (`Ws`, `Ls`, `Wp` ...), start values from closed-form
  synthesis,
* the **3D design** with the same variables (the 3D layout of the circuit, linked to it, so *Update 3D Layout*
  and *Update Schematic* work),
* an **optimization** on the circuit with **multi-fidelity** switched on: the goals are optimized on the circuit
  in seconds, then every round solves the 3D design once, fits the circuit to it and corrects the variables
  (space mapping). Typically 3 to 6 3D solves. See
  [Circuit designs](CIRCUIT.md#multi-fidelity-optimize-a-circuit-and-correct-it-with-its-3d-layout).

The wizard is also on the start page (*Start from a template › Design Wizard*) and on the ribbon (*Tools*).

## Step 1: choose a structure

The structures are shown as cards by category (antennas, matching, couplers and hybrids, power dividers,
filters, feed networks, bias). A search field filters them. Cards marked *Available* can be built; cards marked
*Planned* show what comes next and what each one still needs (switch them off with *Show planned structures*).
The rectangular patch and the hairpin filter open their own designers, which have more options.

## Step 2: specification

Enter what the structure has to do (frequency, coupling, impedances, bandwidth, return loss) and the substrate.
The table shows the synthesized start values and the ranges of the optimization while you type, the picture the
3D design from above, and the right column what will be created and the goals. The substrate is remembered for
the next structure.

* **Smallest width or gap**: no variable range goes below it (manufacturing).
* **Variable range ±**: the range of each optimized variable around its start value.
* **Multi-fidelity**: off, the optimization runs on the circuit only.

**Create** adds the designs; **Create and Optimize** also starts the optimization.

## Structures

| Structure | Specification | Variables | Goals |
|---|---|---|---|
| Quarter-wave transformer | f0, port and load impedance, 1 to 4 sections, bandwidth, return loss | width `W1..WN` and length `L1..LN` of each section | S11 in the band |
| Branchline coupler | f0, coupling (3 dB or unequal), port impedance, bandwidth, return loss and isolation, amplitude and phase tolerance | series arms `Ws`, `Ls`, shunt arms `Wp`, `Lp` | S11 and S41 (isolation), S21 and S31 within the tolerance, S31/S21 at −90° |

The quarter-wave transformer is binomial (maximally flat): ln(Z[k+1]/Z[k]) = 2^−N C(N,k) ln(ZL/Z0); port 2 has
the load impedance. The branchline coupler has series arms Z0·√(1−k²) and shunt arms Z_series/k with
k = 10^(−C/20); port 1 is the input, 2 through, 3 coupled, 4 isolated. The line lengths are a quarter guided
wavelength between the tee centres.

## AI assistants (MCP)

`list_structures` returns the catalogue with the fields of every available structure; `create_structure(key,
spec, material, height_mm, ...)` does what the wizard does. Then `run_simulation(design=<circuit>,
optimization="MultiFidelity1")` and `apply_optimization`.

## What comes next

The plan follows the order of the cards: stub matching, T-junction divider, stepped-impedance lowpass, stub
filters and the first antennas (dipole, monopole, IFA, circular and circularly polarized patch), then Wilkinson,
coupled-line coupler, rat-race (after a curved line part), edge-coupled bandpass and the others.
