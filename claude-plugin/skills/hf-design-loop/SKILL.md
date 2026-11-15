---
name: hf-design-loop
description: Step-by-step workflow for designing an RF/microwave structure (antenna, filter, coupler, matching network, circuit or PCB) with Hertzon's MCP tools, from the specification to a verified design and a report. Use whenever the user asks to design, tune or improve an RF design in Hertzon.
---

# Designing with Hertzon: the design loop

Hertzon's MCP tools (server `hertzon`) let you build, solve and evaluate 3D full-wave FEM models and circuits.
The numbers come from the solver, not from you. Your job is to plan, run and compare variants, and to keep
the engineer in control. The steps below are a guide, not a script: you decide which method fits the problem.

## 0. Check the connection

Call `get_context()`. If the Hertzon tools are missing, tell the user to open Hertzon, choose
**Tools > AI Assistants (MCP)...**, press **Register** next to their assistant and start a new session.
Recommend ticking **Let AI assistants work in this window**: then every change appears in the open window and
Ctrl+Z undoes it.

## 1. Write the goal down

Before modelling, write the goal down in a short design log, `design-log.md`, next to the project (or in the
working folder). Keep it brief:

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
- similar shipped examples: `list_examples()`, `open_example()` (not when the task asks for a design from
  scratch);
- boards: `import_pcb()` and `set_stackup()`;
- circuits: `create_circuit()`, `edit_circuit()`; `circuit_layout()` turns a schematic into a 3D design and back.

**Multi-fidelity is the principle.** Anything a circuit can describe (filters, couplers, dividers, matching
networks, feed networks, active circuits) is designed in the circuit first, where an evaluation costs
milliseconds, and checked and corrected in full wave afterwards with as few 3D solves as possible. Use the
skill `circuit-first` for that, also and especially for structures the Design Wizard does not know yet.
Antennas and other radiating structures have no circuit model: they start in 3D (skill `antenna-design`).

Otherwise build the model with `set_design()` variables for every dimension you may tune, so that sweeps,
optimizers and derivative tuning can use them. Look at it with `render_image()` before solving.

Every solve costs minutes to hours. Plan for the fewest solves:

- When a design assistant creates an optimization (PatchOpt, HairpinCG ...), run it right away with
  `run_simulation()` and `optimization=...`: it solves the starting design itself. Do not solve the baseline first.
  Keep the setup the assistant made, and when the optimization meets the goal, take its result: no extra solve.
  Let the optimization finish; do not abort it to take an earlier evaluation.
- **When the goal is met, stop.** A design that meets the specification is done: no further tuning for margin,
  no extra variants, only the one final check below.

## Mesh and convergence

Hertzon's adaptive meshing decides where the mesh is refined. Do not add mesh operations, change the basis
order, the mesh sizes or the adaptive frequencies an assistant chose, and do not run mesh studies.
Two things are yours:

- **Not converged:** `get_status()` and `get_results()` say clearly when the adaptive passes stopped before
  reaching the target. Read that after every solve, also when you evaluate result files with your own scripts.
  Never read numbers from such a solve as if they were final. Raise `MaximumPasses` with
  `set_setup()` (for example by 5) and run the analysis again: it continues from the last adaptive mesh, so only
  the extra passes cost time. Do this, not a new setup and not a finer Delta S.
- **One final check:** when the design meets the goal, solve it once with MaximumDeltaS 0.01 and
  MinimumConvergedPasses 2 (on the same setup; it continues from the existing mesh), sweeps included. That is
  the confirmation. Nothing more: no second fine solve, no study of mesh, port or air-box variants.

## 3. Baseline

When you did not start from an assistant's optimization: `validate()`, `run_simulation()`, `get_status()` with
`wait_seconds`, then `get_results()` with kind `summary`.
Check mesh convergence and the ports before reading any number (skill `results-check`). Record the baseline
in the log.

If `get_status()` says the analysis will take long, tell the user the expected time, then wait for it with
`get_status()` and `wait_seconds`.

## 4. Iterate with one idea at a time

- State a hypothesis, change one thing, compare. Note in the log in one line: variant, why, result.
- Choose the cheapest tool that answers the question. You decide which; none of them is mandatory:
  - a single targeted solve of the next variant;
  - `compute_tuning()` / `get_tuning()` after a solve: S-parameters at other variable values in milliseconds;
  - an optimizer, `set_optimization()` (Derivative Trust Region for at most 6 variables and a fixed topology,
    Latin Hypercube + Nelder-Mead far from the start), then `apply_optimization()`;
  - a parametric sweep, `set_parametric()`. Every value is a full solve, so this is the most expensive tool:
    use it only as a last resort, when you cannot reach the goal otherwise and do not know which way to go,
    with few values.
- When a topology does not reach the goal after a fair try, change the topology, not only the dimensions.
- Record failed ideas and why they failed. They are results too.

## 5. Verify before you report

A fast result is not yet a correct one. Check the final design with `results-check`, using the final check
solve of the mesh section and the solves you already have: converged mesh, the real figure of merit (for
antennas far-field efficiency, not only S11) and power balance. No further solves: suggest further checks
(counter tests, another solver, a measurement) to the user in the report instead of running them.

## 6. Report

Finish the design log as a short report:

- specification and final result as numbers, side by side;
- final variable values and where the project file is;
- plots as reports (`set_report()`) and field plots (`set_field_plot()`), shown to the user with
  `show_result()` (Hertzon draws them as in its window), and exported data (`export()`, e.g. Touchstone);
- the variants in a short table and the total time;
- failed ideas, open risks, and which statements are confirmed by a test and which are hypotheses.
