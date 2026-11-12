# Example 2 (Run C): timing log

Start 12 November 2026, 15:49 local time, Hertzon 0.3.18 on one workstation, model Fable 5.2 with medium effort.
Tokens are estimates from the conversation length; an exact count per step was not available.

| Step | Time | Solves | Tokens (estimate) | Result |
| --- | --- | --- | --- | --- |
| Read the task, create the folder, open the project copy | 15:49–15:53 | 0 | 45k | Goal of B: at least 50 % at every point, one layer, no matching |
| Model setup (antenna sheet, pad, port, 2 vias 0.25 mm, setup, sweeps) | 15:53–15:57 | 0 | 15k | validated |
| Concept A1: folded IFA, fed at the left | 15:57–16:01 | 1 (172 s) | 8k | Low band at 2.93 GHz, high band only narrow at 6.8 GHz |
| Concept A2: return conductor at the bottom, longer | 16:02–16:06 | 1 (175 s) | 6k | Low band 2.36 GHz with 13 ohm, high band only narrow at 6.2 GHz |
| Concept B1: mirrored, short in the right tab, high-band branch between the conductors | 16:08–16:15 | 1 (313 s) | 10k | Low band 2.22 GHz, high band 4.8–6.1 GHz wide, radiation efficiency 0.77 / 0.9 |
| B2, B3: low band and branch tuned, wider branch | 16:16–16:33 | 2 | 12k | Low band 69–87 %, only one high-band resonance (5.6–6.9 GHz), 13–36 % at the band edges |
| Concept D (high-band conductor on the outer edge, low band inside), 4 variants | 16:35–17:25 | 4 | 35k | Rejected: low-band tip couples to the high-band conductor, modes not separately tunable |
| Concept E (grounded parasitic strip at the PCB edge) | 17:05–17:12 | 1 | 10k | Rejected: radiation efficiency 0.33 at 7 GHz |
| B without branch (diagnosis) | 17:27–17:33 | 1 | 8k | Third harmonic of the low-band arm at 7.06 GHz; the branch only loads it |
| Concept G (plate instead of branch) | 17:40–17:46 | 1 | 6k | Rejected: narrower than the branch |
| Concept T (short and feed at x = 8–9 mm, low-band arm to the left, high-band arm to the right) | until about 17:00 | 2 | 20k | Two high-band resonances (5.66 and 7.76 GHz), low band 44–70 % |
| T tuning: tab pad, short distance, thick arms (rejected), feed 0.5 mm to the left | about 17:00–17:35 | 5 | 40k | Coarse mesh: low band min 49 %, high band min 49 %, means 61 / 68 % |
| T on the fine mesh (12 passes, 289k tetrahedra, far field 5°) | 17:35–18:26 | 1 (3079 s) | 8k | Low band min 53.2 %, high band min 50.4 % (14 points), power balance within 1.7 % |
| Feed strip 1.0 mm wide | 18:27–18:38 | 1 (616 s) | 6k | High band min 53 %, mean 71 % |
| Short path 1.2 mm longer (via at x = 6.3) | 18:39–18:46 | 1 (379 s) | 8k | High band min 61.5 %, mean 74 %, low band too low |
| Low band retuned (two steps), 26 points | 18:47–19:06 | 2 | 10k | 10 passes, converged: low band 59.8 / 71.9 %, high band 60.2 / 75.1 % |
| Check with 18 µm thick copper, design-rule script | 19:07–19:20 | 1 (707 s) | 15k | At most 1.5 points difference |
| Final solve on the fine mesh (12 passes, 249k tetrahedra), 26 points | 19:20–19:41 | 1 (1344 s) | 6k | Low band 62.0 / 72.0 %, high band 60.8 / 75.4 % |
| Plots, report | 19:41–19:55 | 0 | 15k | |

Sum: about 4 h (15:49 to about 19:50), 26 solves, context at the end about 360k tokens (measured, including 100k for
tool and system texts).

Note from the run: the clock times of the rows "B2" to "Concept T" were estimated afterwards and are too late; reliable
are the start at 15:49 and the state at 18:27 after 21 solves.

Reference solver: not run yet.
