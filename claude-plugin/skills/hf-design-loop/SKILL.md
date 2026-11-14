---
name: hf-design-loop
description: Step-by-step workflow for designing an RF/microwave structure (antenna, filter, coupler, matching network, circuit or PCB) with Hertzon's MCP tools, from the specification to a verified design and a report. Use whenever the user asks to design, tune or improve an RF design in Hertzon.
---

# Designing with Hertzon: the design loop

Hertzon's MCP tools (server `hertzon`) let you build, solve and evaluate 3D full-wave FEM models and circuits.
The numbers come from the solver, not from you. Your job is to plan, run and compare variants, and to keep
the engineer in control. Follow these steps in order. Skip a step only when it clearly does not apply, and say so.

## 0. Check the connection

Call `get_context()`. If the Hertzon tools are missing, tell the user to open Hertzon, choose
**Tools > AI Assistants (MCP)...**, press **Register** next to their assistant and start a new session.
Recommend ticking **Let AI assistants work in this window**: then every change appears in the open window and
Ctrl+Z undoes it.

## 1. Write the goal down

Before modelling, write a short design log, `design-log.md`, next to the project (or in the working folder)
with:

- the specification as a table: bands, figure of merit (S11, total efficiency, insertion loss, isolation, gain),
  limits on size, materials, stackup, connectors, lumped parts;
- what may change and what may not (board outline, housing, keep-out areas);
- the environment the design is evaluated in (free space, on a ground plane, in a housing, on a body).
  The agent solves exactly the problem it is given, so ask the user when this is not stated;
- how success is measured, as numbers.

Ask the user only about what changes the goal. Pick sensible defaults for the rest and record them in the log.

## 2. Start from a method, not from a blank model

Use the most specific method Hertzon offers before building geometry by hand:

- patch antennas: `create_patch_antenna()`;
- hairpin band-pass filters and diplexers: `create_hairpin_filter()`, `create_hairpin_diplexer()`;
- couplers, dividers, matching networks, other filters: `list_structures()`, then `create_structure()`
  (a circuit and its 3D design with shared variables);
- similar shipped examples: `list_examples()`, `open_example()`;
- boards: `import_pcb()` and `set_stackup()`;
- circuits: `create_circuit()`, `edit_circuit()`; `circuit_layout()` turns a schematic into a 3D design and back.

Otherwise build the model with `set_design()` variables for every dimension you may tune, so that sweeps,
optimizers and derivative tuning can use them. Look at it with `render_image()` before solving.

## 3. Baseline

`validate()`, `run_simulation()`, `get_status()` with `wait_seconds`, then `get_results()` with kind `summary`.
Check mesh convergence and the ports before reading any number (skill `results-check`). Record the baseline
in the log.

If `get_status()` says the analysis will take long, tell the user the expected time and stop instead of polling.

## 4. Iterate with one idea at a time

- State a hypothesis, change one thing, compare. Note in the log: variant, why, result, solver time.
- Explore with `set_parametric()` instead of single runs, so several values are solved in one go.
- After a solve, `compute_tuning()` and `get_tuning()` give S-parameters at other variable values in
  milliseconds. Use them for what-if questions before solving again.
- Near the goal, hand the iterations to an optimizer: `set_optimization()` (Derivative Trust Region for at most
  6 variables and a fixed topology, Latin Hypercube + Nelder-Mead far from the start), then
  `apply_optimization()`.
- When a topology does not reach the goal after a fair try, change the topology, not only the dimensions.
- Record failed ideas and why they failed. They are results too.

## 5. Verify before you report

A fast result is not yet a correct one. Run the checks of `/hertzon:verify` (skill `results-check`) on the final
design: converged mesh with a tighter Delta S, the real figure of merit (for antennas far-field efficiency, not
only S11), power balance, and a counter test for every claimed cause. For designs that matter, recommend an
independent check (another solver or a measurement) and say so in the report.

## 6. Report

Finish the design log as a short report:

- specification and final result as numbers, side by side;
- final variable values and where the project file is;
- plots in the window (`set_report()`) and exported data (`export()`, e.g. Touchstone);
- the table of variants with time per step and the total wall-clock and solver time;
- failed ideas, open risks, and which statements are confirmed by a test and which are hypotheses.
