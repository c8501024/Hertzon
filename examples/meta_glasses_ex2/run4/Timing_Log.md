# Example 2 (Run 4): timing log

All times UTC, 9 November 2026, Hertzon 0.3.13 and the reference solver (version 2025 R1) on one workstation. Refinement of Run 3: started from the Run 3 project (A13) with the Run 3 report and project memory available. Times from 20:43 on are from the shell clock; the step boundaries between 19:30 and 20:35 are estimated from the solve durations (2–6 min each) and can be off by about 10 minutes. Tokens per step were not recorded.

| Step | Start | End | Wall | Hertzon solves | Reference | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Copy Run 3, baseline A13 rerun, impedance | 19:20 | 19:30 | 10 min | 1 | | 69.3 / 63.4 %, inductive above 6.4 GHz |
| A14–A18: stem branches, bottom-layer parasitic, fork, right lobe | 19:30 | 20:00 | 30 min | 5 | | No usable effect |
| A19–A27: grounded parasitic right of the stem | 20:00 | 20:35 | 35 min | 9 | | Resonance from >8 to 7.0 GHz; A27 lossy at 7.1 GHz |
| A28 | 20:35 | 20:44 | 9 min | 1 | | Hertzon 69.8 / 67.6 % |
| Reference check A28 | 20:44 | 20:55 | 11 min | | 1 | 65.8 / 70.9 % |
| Parametric: arm length, branch end × short position | 20:55 | 21:40 | 45 min | 22 | | 5 + 16 variants plus 1 test |
| A29 (final) | 21:40 | 21:45 | 5 min | 1 | | Hertzon 67.5 / 68.5 % |
| Reference check A29 | | | 6 min | | 1 | 68.7 / 71.7 % |
| **Total until A29** | **19:20** | **21:45** | **about 2.5 h** | **39** | **2** | |
