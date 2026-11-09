"""Write examples/meta_glasses_ex2/README.md: the full Run 3 report (run3/REPORT.md, links rewritten for the parent
folder), the benchmark description and a short note on the faulty Run 2. Run again after editing run3/REPORT.md."""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
RUN3 = os.path.normpath(os.path.join(HERE, ".."))
EX2 = os.path.dirname(RUN3)

HEAD = """# Meta AI-glasses antenna benchmark, Example 2

Meta Reality Labs published two antenna design benchmarks based on AI-glasses temple arms in
*IEEE Antennas and Propagation Magazine*, vol. 68, no. 5, Oct. 2026, Industry Activities
([IEEE Xplore 11775874](https://ieeexplore.ieee.org/document/11775874)). This folder holds a solution of Example 2,
found by a general-purpose LLM agent (Claude in Claude Code) that drove Hertzon only through its MCP interface, and
cross-checked with a commercial FEM reference solver. The report below is the current solution, Run 3; its files are in
[run3/](run3/README.md).

"""

BENCH = """
## Benchmark (Example 2)

- Simplified temple arm: irregular copper PCB of about 40 mm length, simplified chassis with a stainless rear part,
  FPC (Dk 3.09, tan δ 0.0105) next to the PCB, about 15.2 x 2.7 mm, with two alignment holes.
- Goal: maximize free-space total efficiency in 2401–2483 MHz and 5170–7125 MHz, bands weighted equally.
- One feed, any number of ground connections as vias between FPC and PCB; antenna inside the FPC outline, at least
  0.1 mm from holes and edges; optional matching network of up to five lossy 0201 parts.

![Complete structure](run2/results/Ex2_structure_full.png)

![PCB and FPC](run2/results/Ex2_structure_pcb_fpc.png)

## Files of Run 3

| File | What it is |
| --- | --- |
| `run3/Meta_Ex2_Run3.hzn` | Hertzon project of the final design A13 (Setup1: adaptive at 2.45 + 6.1 GHz, S11 and efficiency sweeps) |
| `run3/reference/Meta_Ex2_Run3_reference.py` | IronPython script for the reference solver on Meta's STEP (set `STP_FILE`; the STEP is not included here) |
| `run3/results/` | S11 of A13, reference efficiency and S11, iteration table, comparison table, plots, layout |
| `run3/tools/` | Iteration table, comparison plots, layout plot, this overview |
| [run3/Timing_Log.md](run3/Timing_Log.md) | Timing of every design step |

Reproduce: open `run3/Meta_Ex2_Run3.hzn` in Hertzon 0.3.11 or newer and run Setup1. Total efficiency is radiated power
over incident power of the `Eff` sweep; `get_results` with `kind = efficiency` on the MCP interface gives the split into
mismatch and radiation efficiency.

## Earlier runs

**Run 2 ([run2/](run2/README.md)) is faulty.** It was made with Hertzon versions that still had bugs in the solver and in
the Hertzon MCP interface: wrong lumped-port voltage (#336), no efficiency split, Q or convergence warning over MCP (#333),
wrong loss power of copper solids (#311), a meshing crash and MCP calls without an answer (#298, #291). Its radiated power
and total efficiency are right, but its diagnosis and design decisions are not. It is kept only as a record; Run 3
replaces it. Run 1 is an earlier, separate solution with its own feed model.
"""


def main():
    rep = open(os.path.join(RUN3, "REPORT.md"), encoding="utf-8").read()
    title, body = rep.split("\n", 1)
    body = body.lstrip("\n")
    # links and paths relative to run3 -> relative to the parent folder
    body = body.replace("](../README.md#", "](#").replace("](../run2/", "](run2/")
    body = re.sub(r"\]\((?!https?:|#|run2/)", "](run3/", body)
    body = re.sub(r"`(results/|reference/|tools/)", r"`run3/\1", body)
    body = body.replace("See `Timing_Log.md`.", "See [run3/Timing_Log.md](run3/Timing_Log.md).")
    body = re.sub(r"^## ", "### ", body, flags=re.M)
    run3_title = "## " + title.lstrip("# ").replace("Meta AI-glasses benchmark, Example 2, ", "")
    out = HEAD + run3_title + "\n\n" + body.rstrip() + "\n" + BENCH
    with open(os.path.join(EX2, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(out)


if __name__ == "__main__":
    main()
