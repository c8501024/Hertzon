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
| Dipole | f, wire radius, port impedance, return loss | arm length `Larm` | S11 at the frequency |
| Monopole | f, wire radius, ground plane side, port impedance, return loss | wire length `L` | S11 at the frequency |
| Inverted-F (IFA) | f, board width, ground length, arm height, trace width, port impedance, return loss | radiating arm `L`, feed position `s` | S11 at the frequency |
| Quarter-wave transformer | f0, port and load impedance, 1 to 4 sections, bandwidth, return loss | width `W1..WN` and length `L1..LN` of each section | S11 in the band |
| Wilkinson divider | f0, port impedance, bandwidth, return loss, isolation, split tolerance | arm width `Wa` and run `Lb` | S11, S22, S33 and S32 (isolation), S21 and S31 within the tolerance |
| T-junction divider | f0, power ratio P3/P2, port impedance, bandwidth, return loss, split tolerance | quarter-wave transformers `Wt2`, `Lt2`, `Wt3`, `Lt3` | S11, S21 and S31 within the tolerance of the split |
| Stub matching | f, port impedance, load R + jX, bandwidth, return loss | distance `d` from the load and length `l` of the open stub | S11 in the band |
| Stepped-impedance lowpass | cutoff, order, ripple, high and low impedance, stopband start and rejection | section lengths `L1..LN` (the widths stay) | S11 below the cutoff, S21 in the stopband |
| Shorted-stub bandpass | f0, bandwidth (30 to 80 %), stubs, ripple, rejection and distance of the stopbands | stub widths `Ws1..` and lengths `Ls1..`, line lengths `Lc1..` (symmetric) | S11 in the band, S21 in both stopbands |
| Edge-coupled bandpass | f0, bandwidth (2 to 30 %), resonators, ripple, rejection and distance of the stopbands | width `W` of all resonators, gap `S1..` and length `L1..` of each coupled section (symmetric) | S11 in the band, S21 in both stopbands |
| Open-stub bandstop | f0, width of the stopband with the rejection, stubs, ripple, rejection | stub widths `Ws1..` and lengths `Ls1..`, line lengths `Lc1..`, line width `Wu` | S21 in the stopband, S11 in both passbands |
| Coupled-line coupler | f0, coupling, port impedance, bandwidth, return loss, isolation, coupling tolerance | width `W`, gap `S`, length `L` | S11, S41 (isolation), S31 within the tolerance |
| Branchline coupler | f0, coupling (3 dB or unequal), port impedance, bandwidth, return loss and isolation, amplitude and phase tolerance | series arms `Ws`, `Ls`, shunt arms `Wp`, `Lp` | S11 and S41 (isolation), S21 and S31 within the tolerance, S31/S21 at −90° |

The Wilkinson divider has arms of Z0·√2 and a resistor of 2 Z0. The arms rise from the input tee by `La`, turn and
run in parallel to the resistor; in the 3D design the resistor is a lumped RLC sheet of length 2 La + Wa between
the two arm ends, so the layout closes.

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
Z0e,o = Z0 (1 ± J Z0 + (J Z0)²). All resonators have one width (from the outer section) and each section its
gap for the coupling (Z0e − Z0o)/(Z0e + Z0o): with different widths the corner of a wide resonator would touch the
end of the narrower line before it. The length is a quarter wavelength of the mean phase constant less the open
end. Wide bands need gaps below the smallest feature (the wizard warns): on 0.508 mm RO4350B about 7 % for three
resonators. The optimization starts from a Latin hypercube sample with Nelder-Mead chains.

The open-stub bandstop has quarter-wave open stubs 4 Z0/(π g FBW) joined by quarter-wave lines. FBW is the
bandwidth between the ripple edges; the rejection holds over FBW/Ω_s of it (Ω_s where the Chebyshev response
reaches the rejection), so the stopband you enter is narrower than FBW and the passbands start a little outside
the ripple edges. The width of the connecting lines is a variable too, and the optimization starts from a Latin
hypercube sample with Nelder-Mead chains because the passband match has local minima.

The coupled-line coupler is a quarter-wave section with Z0e,o = Z0·√((1 ± c)/(1 ∓ c)), c = 10^(−C/20); width
and gap come from the coupled-line model of the circuit. It couples backwards: port 1 in, 2 through, 3 coupled
(beside the input), 4 isolated. In microstrip the even and odd modes travel at different speeds, which limits the
isolation to roughly 15 to 25 dB; coupling much stronger than 12 dB needs gaps below 0.15 mm on 0.508 mm boards.

Antennas have no circuit (nothing in a schematic radiates): the wizard creates the 3D design and an optimization
on its adaptive solution at the frequency with the Derivative Trust Region, like the patch designer; multi-fidelity
and the substrate do not apply to the wire antennas. The dipole is two PEC cylinders with a lumped port in the gap,
its resonant length 0.5 − 0.12/ln(L/d) wavelengths (about 0.475 λ for L/d = 100), its input resistance about
70 Ω. The monopole is half of it over a square PEC plane, fed by a lumped port between the plane and the wire,
about 36 Ω. The printed IFA sits at the edge of a board with the ground and the antenna on the top layer: a short
arm to the ground, the radiating arm parallel to the ground edge and the feed arm at a distance s from the short,
fed by a lumped port across a gap at the ground edge. L + H starts at a quarter wavelength with an effective
permittivity of 1 + 0.15 (εr − 1) (the field runs mostly in air); s sets the input resistance. The ground length
is part of the antenna and a specification, not a variable.

The quarter-wave transformer is binomial (maximally flat): ln(Z[k+1]/Z[k]) = 2^−N C(N,k) ln(ZL/Z0); port 2 has
the load impedance. The branchline coupler has series arms Z0·√(1−k²) and shunt arms Z_series/k with
k = 10^(−C/20); port 1 is the input, 2 through, 3 coupled, 4 isolated. The line lengths are a quarter guided
wavelength between the tee centres.

## AI assistants (MCP)

`list_structures` returns the catalogue with the fields of every available structure; `create_structure(key,
spec, material, height_mm, ...)` does what the wizard does. Then `run_simulation(design=<circuit>,
optimization="MultiFidelity1")` and `apply_optimization`.

## What comes next

The plan follows the order of the cards: the circular and the circularly polarized patch, then the
rat-race (after a curved line part) and the others.
