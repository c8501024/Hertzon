# Example 2 (Run B): timing log

Local time, 11 and 12 November 2026, Hertzon 0.3.18 on one workstation, model Fable 5.2 with medium effort.
From scratch: only the benchmark package was available.

## Run up to `Final` (internal Run 7)

Times from the time stamps of the result folders. Tokens per step are rough estimates made during the run (no counter
per step was available); "Solves" counts analyses that ran, aborts caused by tool errors are in brackets.

| Step | Time | Solves | Tokens (estimate) | Content |
| --- | --- | --- | --- | --- |
| 0 Package, task statement, prompt | 22:15–22:18 | 0 | 45k | Copy of the project |
| 1 Geometry builder, folded IFA (K1, K1b) | 22:18–22:30 | 2 (+5) | 60k | Script `ant_lib.py` with automatic design-rule clipping; first tool error "tuple index out of range" |
| 2 Capacitively loaded IFA (K2, K4), T antenna (K3) | 22:30–23:22 | 8 | 70k | 2.4 GHz good with the full arm, 5–7 GHz good with the T; loss check with lossless FPC |
| 3 Gap, meander choke, plate, slot with series C (K5, K6, K8) | 23:22–00:05 | 6 | 70k | Choke does not isolate; slot mode only 9 % wide |
| 4 T arm plus loop (K9, K10), mirrored (K11) | 00:05–01:00 | 8 (+2) | 80k | Loop on the left radiates better; NaN error with a sheet in the FPC tab |
| 5 Equivalent-circuit fit, long ground strip, hairpin (K9d–g, K12, K13) | 01:00–02:10 | 7 (+3) | 110k | 5–7 GHz bandwidth from 15 to 20 %; "singular matrix" with undivided sheets |
| 6 Mirrored variant with hairpin (K14, K15), fine mesh, power-balance checks | 02:10–03:35 | 12 (+1) | 130k | 460k tetrahedra; PEC chassis, PML, 90 mm air, PEC copper, PEC vias |
| 7 Fine tuning with 220k tetrahedra and 28 points (K16, K17, K18, `Final` ×2) | 03:35–05:35 | 5 | 90k | 22–25 min each |
| 8 Mesh confirmation (13 passes), images, report | 05:35–06:30 | 1 (+2) | 60k | |
| Sum | 8 h 15 min | 49 (+13) | see note | |

Solve time: 2 to 8 min with 8 passes (about 95k tetrahedra) and both sweeps, 22 to 25 min with 11 passes (about 220k
tetrahedra) and 28 field points, 24 min with 14 passes (460k tetrahedra) and 15 field points.

Note: the per-step estimates above add up to about 715k tokens, which was too high. The whole session context at the
end of study B was about 590k tokens.

## Study B (target: at least 50 % at every frequency), 12 November 2026

Times from the Hertzon MCP log; a study runs from creating the design to its last result, waiting for the user is not
included. Tokens from the Claude Code session log, summed over the time window of each study.

| Study | Time window | Solves | Solver time | Mesh | Output tokens | New context | Context read (cache) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | 05:51–06:08 | 2 | 15.8 min | 97k | 26.7k | 29.6k | 13.3 M |
| B2 | 06:22–06:39 | 2 | 16.1 min | 97k | 12.9k | 18.1k | 6.8 M |
| B3 | 06:39–07:33 | 2 | 53.0 min | 222k, 28 points | 36.7k | 35.1k | 15.1 M |
| **Sum B1–B3** | | **6** | **84.9 min** | | **76.4k** | **82.8k** | **35.1 M** |
| C1 (60 % check) | 08:09–08:15 | 1 | 6.1 min | 74k | 8.1k | 9.9k | 5.1 M |
| B3 check | 08:15–08:29 | 1 | 13.1 min | 343k, 10 points | 8.2k | 13.5k | 7.1 M |
| B3_final, report, combined plot, example folder | 09:15–09:48 | 1 | 25.9 min | 344k, 28 points | 60.3k | 80.8k | 25.7 M |
| Sum | | 9 | 130 min | | 152.9k | 187.0k | |

- From the first solve of B1 to the result of B3: 1 h 42 min on the clock; to the result of B3_final: 3 h 51 min,
  including 14 min and twice about 40 min of waiting for the user.
- The window of B1 also contains a requested S11/efficiency plot, that of B3 the first answer to the 60 % target.
- Output tokens can count multi-part answers twice and are an upper bound.

## Reference solver (version 2025 R1, same workstation)

| Run | Start | Duration | Result |
| --- | --- | --- | --- |
| B3_final, no mesh seeding | 12 Nov, about 08:50 | about 3 min adaptive (7 passes) plus sweep | Upper band confirmed, 2.4 GHz disputed |
| B3_final, 0.1 mm length seeding on the copper | 12 Nov, 09:55 | still running at the time of writing (17 passes, 1.76 M tetrahedra, 17–23 min per frequency point) | pending |
