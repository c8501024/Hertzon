# Meta AI-glasses antenna benchmark, Example 2

Meta Reality Labs published two antenna design benchmarks based on AI-glasses temple arms in
*IEEE Antennas and Propagation Magazine*, vol. 68, no. 5, Oct. 2026, Industry Activities
([IEEE Xplore 11775874](https://ieeexplore.ieee.org/document/11775874)). This folder holds solutions of Example 2,
found by a general-purpose LLM agent (Claude in Claude Code) that drove Hertzon only through its MCP interface, and
cross-checked with a commercial FEM reference solver. The current solution is **Run 4**; its files are in
[run4/](run4/README.md), the full report is [run4/REPORT.md](run4/REPORT.md).

## Result: Run 4

The final antenna A29, without matching parts, reaches a mean total efficiency of **68.7 % in 2401–2483 MHz and
71.7 % in 5170–7125 MHz in the reference solver** on Meta's original STEP model, **70.2 %** with both bands weighted
equally. This is the highest equal-weight result so far. Run 4 builds on Run 3 and adds a grounded parasitic that
creates a second resonance near 7.2 GHz: at 7.125 GHz the total efficiency rises from 44.3 to 61.8 %.

![Layout of A29 on the FPC in mm](run4/results/Ex2_Run4_layout_mm.png)

## How we got here

Each run was a new session of the agent with its own prompt. The table lists what each run was asked for and what
it reached in the reference solver.

| Run | Prompt (what the run was asked for) | 2.4 GHz band | 5.17–7.125 GHz | Equal weight | Design time | Solves |
| --- | --- | --- | --- | --- | --- | --- |
| Run 1 | Solve Example 2 through the Hertzon MCP | 63.9 % | 68.6 % | 66.3 % | 3.75 h | 74 |
| Run 2 (faulty) | Rerun with the fixed Hertzon MCP | 74.0 % | 54.3 % | 64.2 % | 4.1 h | 24 |
| Run 3 | No band without a radiating mode | 70.7 % | 66.5 % | 68.6 % | about 1.1 h | 13 |
| **Run 4** | **An extra resonance for 6–7 GHz** | **68.7 %** | **71.7 %** | **70.2 %** | **about 2.5 h** | **39** |

Run 1 used its own feed model in the reference solver; Runs 2 to 4 use Meta's STEP unmodified, so only they compare
directly. Run 3 and Run 4 did not start from zero (see their reports), so their design times are not comparable with
Runs 1 and 2.

![Total efficiency of Runs 2, 3 and 4 in the reference solver](run4/results/Ex2_runs_reference.png)

![Final antennas of Runs 2, 3 and 4 on the FPC, to the same scale](run4/results/Ex2_layouts_run2_run4.png)

1. **Run 1** found a folded IFA with about 65 % in both bands. Its reference check used its own feed model, not
   Meta's STEP. It is not kept in this folder.
2. **Run 2** ([run2/](run2/README.md)) is **faulty**. It was made with Hertzon versions that still had bugs in the
   solver and in the MCP interface (lumped-port voltage #336, no efficiency split over MCP #333, loss power #311,
   meshing crash and hanging calls #298, #291). It reached the best 2.4 GHz band but left a dip near 6.5 GHz (15.8 %),
   which it took for a mismatch. Its radiated power is right; its diagnosis and design decisions are not.
3. **Run 3** ([run3/](run3/README.md)) was asked to leave no band without a radiating mode, with the tool fixes and the
   Run 2 findings at hand. One broad high-band mode from a branch on the feed stem removed the dip (6.55 GHz: 64.8 %
   instead of 15.8 %). Its band edges stayed limited by reflection: 51 % at 5.17 GHz and 44 % at 7.125 GHz.
4. **Run 4** ([run4/](run4/README.md)) followed a question from Jiang Zhu (Meta): could a further resonance cover
   6–7 GHz better? Starting from Run 3, a grounded parasitic right of the feed stem adds a resonance just above the band.
   Branches on the stem and a parasitic under it did not work, and a resonance placed right at 7.1 GHz lost most of
   its power in the narrow copper, so it sits just above the band. After one reference check the 2.4 GHz arm and the
   short were retuned (A28 to A29). The upper band rose, the lower band edge and 2.4 GHz lost a little.

![Run 3 against Run 4 in the reference solver](run4/results/Ex2_Run3_vs_Run4_reference.png)

## Benchmark (Example 2)

- Simplified temple arm: irregular copper PCB of about 40 mm length, simplified chassis with a stainless rear part,
  FPC (Dk 3.09, tan δ 0.0105) next to the PCB, about 15.2 x 2.7 mm, with two alignment holes.
- Goal: maximize free-space total efficiency in 2401–2483 MHz and 5170–7125 MHz, bands weighted equally.
- One feed, any number of ground connections as vias between FPC and PCB; antenna inside the FPC outline, at least
  0.1 mm from holes and edges; optional matching network of up to five lossy 0201 parts.

![Complete structure](run2/results/Ex2_structure_full.png)

![PCB and FPC](run2/results/Ex2_structure_pcb_fpc.png)

## Files of Run 4

| File | What it is |
| --- | --- |
| `run4/Meta_Ex2_Run4.hzn` | Hertzon project of the final design A29 (Setup1: adaptive at 2.45 + 6.1 GHz, S11 and efficiency sweeps) |
| `run4/reference/Meta_Ex2_Run4_reference.py` | IronPython script for the reference solver on Meta's STEP (set `STP_FILE`; the STEP is not included here) |
| `run4/results/` | Reference efficiency and S11 of A29 and A28, comparison of Runs 2–4, plots, layout |
| `run4/tools/make_plots.py`, `run4/tools/plot_layouts.py` | Plots, comparison table, layouts of Runs 2–4 |
| [run4/REPORT.md](run4/REPORT.md), [run4/Timing_Log.md](run4/Timing_Log.md) | Full report and timing |

Reproduce: open `run4/Meta_Ex2_Run4.hzn` in Hertzon 0.3.11 or newer and run Setup1. Total efficiency is radiated
power over incident power of the `Eff` sweep; `get_results` with `kind = efficiency` on the MCP interface gives the
split into mismatch and radiation efficiency.
