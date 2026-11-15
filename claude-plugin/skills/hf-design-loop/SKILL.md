---
name: hf-design-loop
description: "What Hertzon's MCP tools offer for designing an RF/microwave structure (antenna, filter, coupler, matching network, circuit or PCB) and how to read what they return. Use whenever the user asks to design, tune or improve an RF design in Hertzon."
---

# Designing with Hertzon

Hertzon's MCP tools (server `hertzon`) build, solve and evaluate 3D full-wave FEM models and circuits. The
numbers come from the solver. How you approach the design is your decision; this page only tells you what
Hertzon offers and what its answers mean.

## Connection

`get_context()` shows whether Hertzon is reachable. If the tools are missing, the user opens Hertzon, chooses
**Tools > AI Assistants (MCP)...**, presses **Register** next to their assistant and starts a new session.
With **Let AI assistants work in this window** every change appears in the open window and Ctrl+Z undoes it.

## What Hertzon offers

- Design assistants: `create_patch_antenna()`, `create_hairpin_filter()`, `create_hairpin_diplexer()`,
  `list_structures()` / `create_structure()` (a circuit and its 3D design with shared variables). An assistant
  sets up mesh, ports and, where it applies, an optimization; `run_simulation()` with `optimization=...`
  solves the starting design itself.
- Shipped examples: `list_examples()`, `open_example()`.
- Boards: `import_pcb()`, `set_stackup()`. Circuits: `create_circuit()`, `edit_circuit()`; `circuit_layout()`
  turns a schematic into a 3D design and back. A circuit evaluates in milliseconds, a full-wave solve in
  minutes (skill `circuit-first`). Antennas have no circuit model (skill `antenna-design`).
- `set_design()` variables let sweeps, optimizers and derivative tuning change a dimension.
  `render_image()` shows the model.
- Ways to explore and tune:
  - single solves: `run_simulation()`, then `get_status()` with `wait_seconds` and `get_results()`;
  - `compute_tuning()` / `get_tuning()` after a solve: S-parameters at other variable values in milliseconds;
  - optimizers, `set_optimization()`: Derivative Trust Region (up to about 6 variables, fixed topology),
    Latin Hypercube + Nelder-Mead (far from the start); `apply_optimization()` takes the result;
  - parametric sweeps, `set_parametric()`: every value is a full solve.
- Reports and pictures: `set_report()`, `set_field_plot()`, `show_result()` (Hertzon draws them as in its
  window), `export()` (e.g. Touchstone).

## Mesh, setup and convergence

- Hertzon refines the mesh adaptively at the setup's adaptive frequencies until Delta S falls below
  MaximumDeltaS (MinimumConvergedPasses times in a row) or MaximumPasses is reached. Sweeps are solved on that
  mesh and never refine it.
- `set_setup()` changes the setup. Raising `MaximumPasses` and solving again continues from the last
  adaptive mesh, so only the extra passes cost time.
- A looser setup (for example MaximumDeltaS 0.03, MaximumPasses 6, fields only at a few frequencies plus an
  interpolating S11 sweep without fields) solves several times faster; its numbers are rougher.
- The tightest useful target is MaximumDeltaS 0.01: below it solve time and memory grow steeply without
  changing design decisions, so do not go tighter.
- Solve time and memory grow quickly with a tighter Delta S, second-order elements, many discrete sweep
  points with fields and parallel sweeps.
- `get_status()` and `get_results()` add a `convergence_warning` when the passes stopped before the target:
  numbers from such a solve are not converged. `phase_note` says when only the phase of a small S11 kept
  Delta S above the target.

## Reading results

See the skill `results-check`: convergence, ports, power balance, the real figure of merit (for antennas the
far-field efficiency, not only S11).
