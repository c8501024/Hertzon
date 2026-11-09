# Example 2 (Run 3): timing log

All times UTC, 9 November 2026, from the session log. Hertzon 0.3.11 with the lumped-port fix (#336) and MCP diagnostics (#333), one workstation. The antenna coordinates are new. The fixed benchmark environment (FPC, PCB, chassis, feed parts) was taken over from the Run 2 model of Meta's STEP data. The agent knew the earlier runs, see "Starting point of this run" in the report, so the times are not a from-zero effort. Output tokens are the agent's own, from the session log.

## Steps

| Step | Start | End | Wall | Full-wave runs | Solver time | Output tokens | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Check Hertzon version, read paper rules, parse STEP | 06:56 | 07:00 | 4 min | 0 | | 18.8k | Installed was 0.3.10; 0.3.11 requested from the user. |
| New project, environment, port, setup, first antenna, mesh check; wait for 0.3.11 | 07:00 | 07:07 | 7 min | 0 | | 19.1k | Installed by the user at 07:06, waiting 07:03 to 07:07. |
| A1–A4: short position, arm length | 07:07 | 07:21 | 14 min | 4 | 11 min | 9.6k | Hole at 5.2–5.9 GHz with any short on the driven arm; A2 without a short radiates over the whole high band. |
| A5: grounded parasitic for 2.4 GHz | 07:21 | 07:26 | 5 min | 1 | 4 min | 7.3k | Dropped: 2.4 GHz radiation efficiency 33–39 %. |
| A6–A9: branch on the feed stem, shortened in steps | 07:26 | 07:44 | 18 min | 4 | 15 min | 8.7k | High band moves into the centre, no hole left. |
| A10–A12: wider branch, right stub, right lobe | 07:44 | 08:00 | 16 min | 3 | 14 min | 4.7k | Small changes; design stable. |
| A13: 0.1 mm clearance, final Hertzon run | 08:00 | 08:03 | 3 min | 1 | 2 min | 5.9k | 71.0 % / 62.3 %, converged at 33.6k tets. |
| **Total design** | 06:56 | 08:03 | **1 h 07 min** | **13** | **46 min** | **74k** | |
| Reference-solver check on Meta's STEP | 08:03 | 08:08 | 5 min | 1 (reference) | 5 min | | 70.7 % / 66.5 %. |
