# Meta AI-glasses benchmark, Example 2, Run 3: no band without a radiating mode

November 2026, Hertzon 0.3.11, checked with a commercial FEM reference solver

## Result

The new antenna, without matching parts, reaches a mean total efficiency of **70.7 % in 2401–2483 MHz and 66.5 % in 5170–7125 MHz in the reference solver** on Meta's original STEP model, 68.6 % with both bands weighted equally. Hertzon gives 71.0 % and 62.3 % for the same design (66.6 %). This is the highest equal-weight result of the three runs, and the band around 6.5 GHz that stayed dark in Run 2 now radiates: 64.8 % total efficiency in the reference solver at 6.55 GHz instead of 15.8 %.

| Run | Solver | 2.4 GHz band | 5.17–7.125 GHz | Equal weight | At 6.55 GHz | Design time | Solves |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Run 1 | Reference | 63.9 % | 68.6 % | 66.3 % | 74.8 % | 3.75 h | 74 |
| Run 2 | Reference | 74.0 % | 54.3 % | 64.2 % | 15.8 % | 4.1 h | 24 |
| **Run 3** | **Reference** | **70.7 %** | **66.5 %** | **68.6 %** | **64.8 %** | **about 1.1 h** | **13** |
| Run 3 | Hertzon 0.3.11 | 71.0 % | 62.3 % | 66.6 % | 57.7 % | | |

Run 1 and Run 2 values are from their own reports and used older Hertzon versions; only the reference rows compare directly.

![Total efficiency of Run 3 in Hertzon and the reference solver, with the Run 2 dip for reference](results/Ex2_Run3_hertzon_vs_reference.png)

## Band by band

Total efficiency is Prad / Pinc with 1 W incident in both solvers. Hertzon radiation efficiency is Prad / Pacc from the 0.3.11 efficiency split.

| Frequency (GHz) | Hertzon total (%) | Hertzon radiation (%) | Reference total (%) | Reference accepted (%) | Run 2 reference total (%) |
| --- | --- | --- | --- | --- | --- |
| 2.401 | 62.2 | 81.1 | 66.0 | 81.8 | 61.4 |
| 2.442 | 74.3 | 83.4 | 73.9 | 90.2 | 80.0 |
| 2.483 | 76.5 | 84.4 | 72.2 | 87.7 | 80.7 |
| 5.17 | 52.9 | 86.1 | 51.0 | 54.9 | 46.2 |
| 5.5 | 77.2 | 92.2 | 78.5 | 81.4 | 89.7 |
| 5.85 | 86.3 | 94.9 | 88.7 | 89.6 | 67.3 |
| 6.2 | 79.7 | 94.5 | 86.1 | 86.1 | 39.4 |
| 6.55 | 57.7 | 92.5 | 64.8 | 65.1 | 15.8 |
| 6.85 | 45.6 | 90.2 | 52.0 | 52.1 | 41.6 |
| 7.125 | 36.8 | 87.2 | 44.3 | 44.2 | 80.2 |

## Why there is no dip now

Radiation efficiency stays between 86 and 95 % across the whole high band in Hertzon. In the reference solver the radiated power equals the accepted power to within a few percent from 5.5 GHz up. What is left at the band edges is reflection: the high band is served by one broad, low-Q series resonance near 5.8 GHz (R about 28 Ω, Q about 8.7 in A9), not by a gap between two narrow modes. Its Q_Z is 5 to 12 from 5.17 to 7.125 GHz; Run 2 had about 58 at the dip.

The path to this shape (`results/iterations.csv`):

1. A1, A3 and A4 used a shorted arm and a wide top bar. The upper band end radiated well, but 5.17–5.85 GHz had radiation efficiency of only 32 to 77 % and high Q wherever the short was placed. A short anywhere on the driven structure produced this.
2. A2 removed the short: radiation efficiency 82–97 % over the whole high band, but 2.4 GHz had R of only 5 Ω and did not match.
3. A5 tried a separate grounded parasitic for 2.4 GHz. It radiated badly at 2.4 GHz (33–39 %) and its folded arm reintroduced loss at 7 GHz, so it was dropped.
4. A6 to A9 kept the short for 2.4 GHz and added a branch directly on the feed stem. Shortening that branch from x = −2.6 to 1.4 mm moved the high-band mode into the band centre; radiation efficiency stayed above 83 % throughout, and the 2.4 GHz arm stayed in band.
5. A10 to A13 widened the branch to 0.8 mm, added a short wide stub to the right of the feed, retuned the 2.4 GHz arm and set the 0.1 mm edge clearance. A11 and A12 changed little, so the design is not sensitive to the right-hand lobe.

## Design

One feed through Meta's feed pin, one ground pin r = 0.15 mm, copper on the top FPC layer only, no matching parts. Coordinates in mm from the supplied CAD origin, all with at least 0.1 mm clearance from FPC edges and holes.

| Element | x | y |
| --- | --- | --- |
| Top bar | −3.1 to 12.0 | 5.85–6.55 |
| Feed stem over the feed pin at (7.486, 4.892) | 7.066–7.906 | 4.442–5.85 |
| High-band branch on the stem | 1.4–7.086 | 4.3–5.1 |
| Stub right of the stem | 7.886–9.9 | 4.7–5.3 |
| Short stub, pin at (11.0, 3.8) | 10.8–11.2 | 3.605–5.85 |
| Left leg | −3.1 to −2.7 | 3.605–5.85 |
| Lower arm for 2.4 GHz | −3.1 to 8.4 | 3.605–4.005 |
| Right drop | 11.5–12.0 | 4.45–5.85 |
| Right lobe | 11.5–13.3 | 4.45–4.7 |
| Right lobe, wide part | 12.1–13.3 | 3.7–4.45 |

![Layout on the FPC](results/Ex2_Run3_layout.png)

No part of Meta's reference design or of the Run 1 and Run 2 antennas was reused. The environment (FPC, PCB, chassis, feed parts, port) is Meta's benchmark geometry, rebuilt from the STEP data with the same meshing simplifications as Run 2.

## Setup

Hertzon 0.3.11 (lumped-port voltage fix, MCP diagnostics), first-order elements, adaptive at 2.45 and 6.1 GHz, up to 10 passes, ΔS ≤ 0.02. The final run converged after 7 passes at 33.6k tetrahedra (ΔS 0.0198). Antenna copper is a two-sided sheet of 35 µm. The reference solver (version 2025 R1) uses Meta's STEP unmodified plus the antenna sheet, the ground pin and a 30 µm bridge to the feed pin (`reference/Meta_Ex2_Run3_reference.py`), adaptive at 2.45 and 6.1 GHz, ΔS ≤ 0.01, 3° far-field sphere, two-sided copper sheet.

## Limits

- The high band is a single broad mode, so total efficiency falls towards both edges: 51 % at 5.17 GHz and 44 % at 7.125 GHz in the reference solver, 53 % and 37 % in Hertzon. Those edges are limited by reflection, not by missing radiation; a matching network or a second mode near 7 GHz could improve them, and this was not tried.
- Hertzon is 4 points lower than the reference solver on the high-band mean and within 0.3 on the 2.4 GHz mean. The difference is largest at 6.2–7.125 GHz (up to 7.5 points).
- The reference solver reports a radiation efficiency of 99.6 to 100.1 % above 6 GHz, which is above what a lossy FPC and finite copper can give, so its far-field integration is optimistic by up to a few percent and the high-band reference figures should be read with that margin. Hertzon's split gives 87–95 % at the same frequencies.
- The Hertzon final run met ΔS only just (0.0198 against 0.02). The nearly identical designs A11 and A12 reached 76.9k and 58.3k tetrahedra with ΔS 0.0306 and 0.0187; their band means are within 1.5 points of A13.
- The current distribution was judged from the radiation efficiency split and Q, not from a field plot.

## Effort

About 63 minutes wall time for the design, including 4 minutes waiting for the Hertzon update, 13 full-wave solves with 46 minutes of solver time, plus one reference-solver run of 5 minutes. Context use at the end was about 331k tokens, most of it tool output. See `Timing_Log.md`.
