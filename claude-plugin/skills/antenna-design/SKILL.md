---
name: antenna-design
description: Know-how for modelling, solving and tuning antennas in Hertzon (patch, monopole, dipole, IFA/PIFA, multi-band, small antennas on boards): open region, ports, adaptive frequencies, far fields and efficiency. Use together with hf-design-loop whenever an antenna is designed or analysed.
---

# Antennas in Hertzon

Use this with the design loop (skill `hf-design-loop`). It covers what is specific to radiating structures.

## Model

- Open region: `set_design()` with the setting AutoOpen = "Auto Open" adds an air region with a radiation
  boundary. The automatic padding is fine for most cases; for a fixed padding keep at least lambda/4 at the
  lowest frequency.
- Far fields need a sphere: `set_far_field_sphere()`. Without it there is no gain and no efficiency.
- Thin metal (patches, traces, ground planes) as sheets with "Finite Conductivity" (copper) or "Perfect E";
  thick metal as solids of material "copper" or "pec".
- Everything that carries antenna current belongs in the model: the ground plane or board, the housing,
  nearby metal. The ground size changes resonance and bandwidth of small antennas a lot.
- Make every dimension you may tune a variable (`set_design()`), including feed and short positions.

## Feed

- A Lumped Port is a small sheet bridging the gap between two conductors (for example board edge and feed
  trace) with an IntegrationLine from one conductor to the other. Keep the port small compared with the
  wavelength and the feature it feeds, and make sure nothing else touches it.
- A Wave Port sits on the boundary of the solution region over the cross section of a line (microstrip,
  coax, grounded CPW). Use it when the feed line itself is part of the design.
- If Delta S jumps between passes or S11 changes a lot from pass to pass, check the port before anything else.

## Frequencies and mesh

- Adapt where the answer matters: the operating frequency for one band; for several bands
  SolutionFrequencyMode "Multi-Frequencies" with the band centres or edges; for very wide bands "Broadband".
  A sweep never refines the mesh: it is solved on the mesh adapted at these frequencies.
- Design, tune and optimize with MaximumDeltaS 0.02 and MinimumConvergedPasses 1. Tighten Delta S (0.01,
  two converged passes) only for the final check: the mesh, the memory and the time of every later solve,
  tuning model and optimizer step grow with it (a patch went from 42k to 72k tetrahedra and 4.6 to 7.2 GB).
- Use an interpolating sweep over a range wider than the bands for S11, and a discrete sweep with fields saved
  at a few in-band frequencies for far fields.

## Figure of merit

- Screen variants with S11 or the mismatch efficiency from `get_results()` kind `impedance`; it is fast.
- Judge the final design with far fields: `get_results()` kind `efficiency` (total = mismatch x radiation
  efficiency, per frequency, with the limiting factor) and kind `farfield` (gain, pattern cuts). A good S11 is
  not a good antenna if the radiation efficiency is low.
- Average or worst case over a band, as the specification says, not only at the centre frequency.

## Typical knobs

- Patch: length sets the frequency, inset or feed position sets the match, substrate height and permittivity
  set the bandwidth. `create_patch_antenna()` builds one with its optimizer PatchOpt; run that optimization
  directly (`run_simulation()` with optimization "PatchOpt"): it usually meets the match in one or two solves.
- IFA/PIFA: total length from the short sets the frequency, the feed-to-short distance sets the resistance,
  the height above ground and the ground clearance set the bandwidth.
- Monopole/dipole: length sets the frequency; a wider or tapered element gives more bandwidth.
- A second band usually needs a second resonance: a branch, a slot or a parasitic element. Matching parts
  can widen a band but cannot create radiation that is not there.
- Small antennas: bandwidth is limited by size. Check Q_Z from `get_results()` kind `impedance`: a very
  high Q_Z means the bandwidth goal may be out of reach without changing the size or the topology.

## Matching networks

A lumped matching network can recover mismatch, not radiation efficiency. Model real parts as lossy (finite Q),
and check the efficiency with the network included, not only S11. If a reasonable network gains nothing, the
problem is not (only) the match.
