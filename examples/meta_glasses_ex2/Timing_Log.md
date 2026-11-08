# Example 2 rerun (Run 2): timing log

All times UTC, 8 November 2026. Hertzon 0.3.5 with the MCP hang fix (PR #291), one 64-core workstation. No model, geometry or results from Run 1 were reused.

## Steps

| Step | Start | End | Wall | Full-wave runs |
| --- | --- | --- | --- | --- |
| Read paper and CST/HFSS/STEP data, rebuild FPC and PCB as primitives, find and fix the meshing failure | 08:30 | 08:51 | 21 min | 0 (about 15 mesh-only tries) |
| Topology search A1 to A6, loss split (tan d = 0) | 08:51 | 09:27 | 36 min | 7 |
| IFA with short right of the feed (A7), high-band branches A8 to A16 | 09:27 | 10:55 | 88 min | 10 (2 failed starts, NaN) |
| 2.4 GHz retune A17, high-band variants A18 to A21 | 10:55 | 11:43 | 48 min | 5 |
| Fine-mesh check of A17 (14 passes, 373k tets), matching search and Hertzon Circuits, rule-compliant final A22 | 11:43 | 12:34 | 51 min | 2 |
| Report and files | 12:34 | 12:34 | ~3 min | 0 |
| **Total** | 08:30 | 12:34 | **about 4 h 6 min** | **24 solved, about 200 min solver time** |

Run 1 for comparison: 3.75 h, 74 solves.

Follow-up on the user's request, after the design was finished:

| Step | Start | End | Wall | Runs |
| --- | --- | --- | --- | --- |
| Investigate the 6.2–6.85 GHz dip (lossless run, third branch in two lengths) | 12:36 | 12:55 | 19 min | 3 Hertzon |
| HFSS on Meta's STEP (3 starts, script errors) | 12:55 | 13:03 | 8 min | 1 HFSS |
| Two-sided sheet test in Hertzon | 13:03 | 13:12 | 9 min | 1 Hertzon |
| HFSS on the Hertzon geometry (STEP export needed a x1000 scale) and report | 13:12 | 13:25 | 13 min | 1 HFSS |

## Model and token usage (Claude, session transcript, up to 12:34 UTC)

| Step | Model calls | Output tokens | Cache-write tokens | Cache-read tokens |
| --- | --- | --- | --- | --- |
| Start, paper/CAD, model build, mesher bug and geometry debug | 168 | 95,740 | 290,253 | 38.5 M |
| Topology search A1 to A6, loss split | 49 | 22,385 | 40,260 | 15.2 M |
| IFA short right of feed, high-band branches A7 to A16 | 79 | 34,886 | 61,656 | 28.5 M |
| 2.4 GHz retune A17, high-band tests A18 to A21 | 40 | 15,244 | 33,833 | 16.4 M |
| Fine verification, matching, final A22 | 24 | 14,647 | 35,263 | 10.7 M |
| **Total up to the final design** | **360** | **182,902** | **461,265** | **109.3 M** |

## MCP hangs

None. All Hertzon tools were called one at a time, as in Run 1. Parallel calls were not tested.

## Hertzon problems found

1. `solver/facematch.py`, `final_face_samples`: `getNodes(2, tag, includeBoundary=True)` misses interior nodes of some faces, which gave `index N out of bounds`. This is likely the real cause of the Run 1 crashes with STEP bodies. I patched one line in the installed copy so the run could continue (global node list, original saved as `facematch.py.orig_run2`). This is a change to the installed solver code for this run; it does not affect the physics. The repo fix is being handled in a separate thread.
2. Near-coincident faces of touching CAD bodies (gaps of a few um) give `Unknown model face with tag N` or `Invalid boundary mesh`. Workaround: let metal bodies overlap by at least 0.05 mm. A clearer error message and a repair hint would help.
3. A via cylinder exactly tangent to the edge of a sheet gives a NaN in the error estimate. The geometry should be caught at validation.
4. `Setup.MultiFrequencies`: the help text says separator `;`, but only `,` works (`Invalid expression '2.45GHz;6.1GHz'`).
5. `Loss Power` and `Power Balance` in the far-field summary are not usable (power balance +150 to +1200 %); radiated power over incident power was used. Since #231 the displayed Radiation Efficiency, GainTotal and RealizedGainTotal use this Ploss, so they are also too low. Reported for a new repo thread.
6. `export` kind `step` writes coordinates in metres but labels the file mm, so an import is 1000 times too small. Worked around with a x1000 scale in HFSS.
7. One-sided Finite Conductivity on zero-thickness sheets appears to overestimate the sheet loss: two-sided sheets raised the 2.4 GHz mean from 66.5 to 69.8 % (HFSS 74.0 %).

## Data for animation and report

- `results/iterations.csv` and `results/Ex2_Run2_progress.png`: one line per iteration.
- `results/A*.s1p`: S11 of every iteration; `results/A*_eff.csv`: far-field efficiency where computed.
- `results/Ex2_Run2_final_efficiency.png`, `results/FINAL_table.csv`, `results/Ex2_Run2_layout_top.png`.
- `results/fine373k/`: far-field data of the fine-mesh check.
