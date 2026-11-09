# Example 2 (Run 3): timing log

All times UTC, 9 November 2026. Hertzon 0.3.11 with the lumped-port fix (#336) and MCP diagnostics (#333), one workstation. No antenna geometry or results from Run 1 or Run 2 were reused. The fixed benchmark environment (FPC, PCB, chassis, feed parts) was rebuilt from Meta's STEP data with the same simplifications as Run 2, because those were needed for meshing.

Tokens: only the total was measured (about 331k context tokens at the end of the run, most of them tool output); per-step tokens were not recorded.

## Steps

| Step | Start | End | Wall | Full-wave runs | Solver time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Check Hertzon version, read paper rules, parse STEP | 06:57 | 07:00 | 3 min | 0 | | Installed was 0.3.10; 0.3.11 requested from the user. |
| New project, environment, port, setup, first antenna A1 | 07:00 | 07:03 | 3 min | 0 | | Mesh-only check passed. |
| Wait for Hertzon 0.3.11 | 07:03 | 07:07 | 4 min | 0 | | Installed by the user at 07:06. |
| A1–A4: short position, arm length | 07:07 | 07:21 | 14 min | 4 | 11 min | Hole at 5.2–5.9 GHz with any short on the driven arm; A2 without a short radiates over the whole high band. |
| A5: grounded parasitic for 2.4 GHz | 07:21 | 07:26 | 5 min | 1 | 4 min | Dropped: 2.4 GHz radiation efficiency 33–39 %. |
| A6–A9: branch on the feed stem, shortened in steps | 07:26 | 07:44 | 18 min | 4 | 15 min | High band moves into the centre, no hole left. |
| A10–A12: wider branch, right stub, right lobe | 07:44 | 07:57 | 13 min | 3 | 14 min | Small changes; design stable. |
| A13: 0.1 mm clearance, final Hertzon run | 07:57 | 08:02 | 5 min | 1 | 2 min | 71.0 % / 62.3 %, converged at 33.6k tets. |
| Reference-solver check on Meta's STEP | 08:03 | 08:08 | 5 min | 1 (reference) | 5 min | 70.7 % / 66.5 %. |
| Report and files | 08:08 | 08:10 | 2 min | 0 | | |
| **Total design (06:57–08:02)** | | | **about 63 min** | **13 Hertzon + 1 reference** | **46 min Hertzon** | |

Intermediate times are from the solver status and the log marks at A8 (07:38) and A13 (08:00); per-step boundaries are approximate to about 1 minute.
