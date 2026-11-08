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
| T-junction divider | f0, power ratio P3/P2, port impedance, bandwidth, return loss, split tolerance | quarter-wave transformers `Wt2`, `Lt2`, `Wt3`, `Lt3` | S11, S21 and S31 within the tolerance of the split |
| Stub matching | f, port impedance, load R + jX, bandwidth, return loss | distance `d` from the load and length `l` of the open stub | S11 in the band |
| Stepped-impedance lowpass | cutoff, order, ripple, high and low impedance, stopband start and rejection | section lengths `L1..LN` (the widths stay) | S11 below the cutoff, S21 in the stopband |
| Shorted-stub bandpass | f0, bandwidth (30 to 80 %), stubs, ripple, rejection and distance of the stopbands | stub widths `Ws1..` and lengths `Ls1..`, line lengths `Lc1..` (symmetric) | S11 in the band, S21 in both stopbands |
| Edge-coupled bandpass | f0, bandwidth (2 to 30 %), resonators, ripple, rejection and distance of the stopbands | width `W1..`, gap `S1..` and length `L1..` of each coupled section (symmetric) | S11 in the band, S21 in both stopbands |
| Open-stub bandstop | f0, width of the stopband with the rejection, stubs, ripple, rejection | stub widths `Ws1..` and lengths `Ls1..`, line lengths `Lc1..`, line width `Wu` | S21 in the stopband, S11 in both passbands |
| Branchline coupler | f0, coupling (3 dB or unequal), port impedance, bandwidth, return loss and isolation, amplitude and phase tolerance | series arms `Ws`, `Ls`, shunt arms `Wp`, `Lp` | S11 and S41 (isolation), S21 and S31 within the tolerance, S31/S21 at −90° |

The T-junction divider splits the input admittance in the power ratio K = P3/P2 and brings each half back to the
port impedance with a quarter-wave transformer of sqrt(Z_in Z0); it has no isolation between the outputs. The
stepped-impedance lowpass is a Chebyshev prototype of shunt capacitors (low impedance) and series inductors (high
impedance), beta l = g Z_low/Z0 or g Z0/Z_high at the cutoff.

Stub matching uses a shunt open stub at a distance d from the load (first of the two solutions); the load is a
series RLC to ground with the reactance at the frequency, a lumped RLC sheet in the 3D design. The shorted-stub
bandpass has quarter-wave stubs pi Z0 FBW/(4 g) shorted by vias, joined by quarter-wave lines of the port
impedance; the stubs are synthesized for a 1.4 times wider band because the connecting lines narrow it, and the
symmetric stubs share their variables.

The edge-coupled bandpass has N+1 coupled sections with J Z0 from the Chebyshev prototype and
Z0e,o = Z0 (1 ± J Z0 + (J Z0)²); width and gap are solved from the coupled-line model of the circuit, the length
is a quarter wavelength of the mean phase constant. The open ends of the resonators shift the 3D response down
by a few %, which the multi-fidelity rounds correct. Wide bands need gaps below the smallest feature (the wizard
warns): on 0.508 mm RO4350B about 7 % for three resonators.

The open-stub bandstop has quarter-wave open stubs 4 Z0/(π g FBW) joined by quarter-wave lines. FBW is the
bandwidth between the ripple edges; the rejection holds over FBW/Ω_s of it (Ω_s where the Chebyshev response
reaches the rejection), so the stopband you enter is narrower than FBW and the passbands start a little outside
the ripple edges. The width of the connecting lines is a variable too, and the optimization starts from a Latin
hypercube sample with Nelder-Mead chains because the passband match has local minima.

The quarter-wave transformer is binomial (maximally flat): ln(Z[k+1]/Z[k]) = 2^−N C(N,k) ln(ZL/Z0); port 2 has
the load impedance. The branchline coupler has series arms Z0·√(1−k²) and shunt arms Z_series/k with
k = 10^(−C/20); port 1 is the input, 2 through, 3 coupled, 4 isolated. The line lengths are a quarter guided
wavelength between the tee centres.

## AI assistants (MCP)

`list_structures` returns the catalogue with the fields of every available structure; `create_structure(key,
spec, material, height_mm, ...)` does what the wizard does. Then `run_simulation(design=<circuit>,
optimization="MultiFidelity1")` and `apply_optimization`.

## What comes next

The plan follows the order of the cards: the stub filters and the first antennas (dipole, monopole, IFA, circular and circularly polarized patch), then Wilkinson,
coupled-line coupler, rat-race (after a curved line part), edge-coupled bandpass and the others.
