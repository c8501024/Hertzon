# Meta AI-glasses antenna benchmark, Example 1, solved through the Hertzon MCP interface

Meta Reality Labs published two antenna design benchmarks based on AI-glasses temple arms in
*IEEE Antennas and Propagation Magazine*, vol. 68, no. 5, Oct. 2026, Industry Activities
([IEEE Xplore 11775874](https://ieeexplore.ieee.org/document/11775874)). This folder holds a solution of
Example 1, found by a general-purpose LLM agent (Claude Opus 5.5 in Claude Code) that drove Hertzon only
through its MCP interface, and cross-checked in Ansys HFSS.

## Result

Coupled-fed shorted strip on the top layer of the 25 x 5 mm keep-out, **no matching components**.

| Band | Hertzon total efficiency | HFSS 2025 R1 total efficiency |
| --- | --- | --- |
| 2401–2483 MHz | 79–82 %, average 81 % | 84–86 %, average 85 % |
| 5170–7125 MHz | 64–87 %, average 81 % (76 % with radiation efficiency capped at 95 %) | 61–83 %, average 77 % |

Effort: 2 h 25 min wall-clock from the request to the verified design, 50 full-wave runs, ~85 min solver
time on one 64-core workstation, ~95k model output tokens.

## Benchmark (Example 1)

- PCB 120 x 12.5 x 1 mm, all copper; keep-out notch 25 x 5 mm at the long edge, 20.5 mm from the left end,
  Dk 3.7, tan δ 0.02.
- Goal: maximize free-space total efficiency in 2401–2483 MHz and 5170–7125 MHz, bands weighted equally.
- One feed, any number of ground connections, optional matching network of up to five lossy 0201 parts.

## Final geometry (mm, x from the left PCB end, y from the bottom edge, all at z = 1 mm)

| Element | Position and size |
| --- | --- |
| Shorting leg | x 21.0–21.5, y 7.5–10.8 |
| Shorted arm | x 21.0–36.5 (15.5 long), y 10.8–12.3 (1.5 wide) |
| Feed (lumped port) | x 32.0–32.5, y 7.5–8.0 |
| Feed leg | x 32.0–32.5, y 8.0–9.2 |
| Feed strip | x 30.0–38.0, y 9.2–10.4 (0.4 mm below the arm) |

## Files

| File | What it is |
| --- | --- |
| `Meta_Ex1_CoupledFedStrip.hzn` | Hertzon project of the final design (Setup1: adaptive at 2.45 + 6.1 GHz, S11 and efficiency sweeps) |
| `Meta_Ex1_CoupledFedStrip.step` | Geometry (PCB, keep-out, antenna sheets, port) |
| `hfss/Meta_Ex1_CoupledFedStrip_HFSS.py` | Self-contained AEDT IronPython script: builds, solves and exports the HFSS model (Tools > Run Script) |
| `results/` | Hertzon S11, HFSS S11 and efficiency, plots |
| `tools/match.py`, `tools/s1p.py` | Lossy matching-network search used while screening designs |

## Reproduce

- Hertzon: open the `.hzn` and run Setup1 (about 4 min on 64 cores). Total efficiency = radiated power /
  incident power of the Eff sweep.
- HFSS: Tools > Run Script > `hfss/Meta_Ex1_CoupledFedStrip_HFSS.py` (about 3 min). Results land in
  `Documents/Meta_Ex1_HFSS`.
