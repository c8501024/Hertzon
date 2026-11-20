---
name: hf-design-loop
description: "What Hertzon's MCP tools offer for designing an RF/microwave structure (antenna, filter, coupler, matching network, circuit or PCB), how to explore many variants quickly and how to read what the tools return. Use whenever the user asks to design, tune or improve an RF design in Hertzon."
---

# Designing with Hertzon

Hertzon's MCP tools (server `hertzon`) build, solve and evaluate 3D full-wave FEM models and circuits. The
numbers come from the solver. How you approach the design is your decision; this page tells you what Hertzon
offers, how to get through many variants quickly and what its answers mean.

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
- `set_design()` variables let sweeps, optimizers and derivative tuning change a dimension; `set_design()` can
  also add a new design, so every variant can live in a design of its own. `render_image()` shows the model.
- Ways to explore and tune:
  - solves: `run_simulation()`, then `get_status()` with `wait_seconds` and `get_results()`;
  - `compute_tuning()` / `get_tuning()` after a solve: S-parameters at other variable values in milliseconds;
  - optimizers, `set_optimization()`: Derivative Trust Region (up to about 6 variables, fixed topology),
    Latin Hypercube + Nelder-Mead (far from the start); `apply_optimization()` takes the result;
  - parametric sweeps, `set_parametric()`: every value is a full solve; with the property
    `ParallelVariations` several variations are solved at the same time.
- Reports and pictures: `set_report()`, `set_field_plot()`, `show_result()` (Hertzon draws them as in its
  window), `export()` (e.g. Touchstone).

## Many variants at once

For a simple structure (a single patch, a standard filter) start with the design assistant
(`create_patch_antenna()`, `create_hairpin_filter()`, ...) or `list_structures()`, one solve and then
derivative tuning or the optimization it sets up. No broad search is needed there.

A demanding design (several bands, tight space, an unusual topology) is found fastest by exploring
broadly and in batches before settling on anything:

- `run_simulation()` returns at once. Start several variants (designs or setups) one after another: the
  window queues them and solves them back to back while you wait. Then wait once with `get_status()` on the
  last one and compare all results together, instead of one solve, one look, one change.
- A parametric sweep with `ParallelVariations` solves variations of one design truly in parallel, as far
  as cores and memory allow.
- Compare whole concepts (topologies, not only dimensions) side by side in the first batches; tuning
  dimensions does not rescue a concept that cannot reach the goal.
- A coarse setup (below) makes each variant of such a batch take minutes instead of tens of minutes.
- Hertzon keeps the fields of every solve, several GB per design. In a long search check the free disk
  space now and then: a full disk aborts solves.

## Systems of many parts

A system such as a VNA test set (dividers, couplers, filters, switches, amplifiers) is not designed as one
model:

- Split it into its parts and their interfaces first: function, band, port impedance and the phase each
  path must have. Write this plan down for the user before solving anything.
- Design each part on its own, with ports at the interface impedance (a design assistant or
  `list_structures()` where one fits, `create_circuit()` for what a circuit can describe), and check it on its
  own. Parts that should be solved at the same time go into projects of their own: every project opens in its
  own Hertzon window, and each window has its own solve queue.
- Assemble the system in a circuit: every part as a 3D design block (type EM, `import_3d_designs()` brings
  them from the part projects) or a Touchstone block. `board()` lays out the board of the circuit:
  `connect` routes the joins with the width for the line impedance, tapers and bends; `match_phase` makes
  several paths equal in phase at f0, or gives each a set offset, with meanders; the routes are simulated as
  part of the circuit. The example `VNA_TestSet_2G4` (`open_example()`) shows the whole chain.
  `circuit_layout()` turns the result into a 3D design for a final full-wave check of the assembly.
- Before starting, estimate for the user how long the development takes and what it costs, in two ways:
  - serially on this computer: the sum over all parts of (solves expected × time per solve), plus the
    assembly;
  - in parallel on cloud servers, one part per window and server: the longest part plus the assembly, and
    the cost as servers × hours × price per hour. `cloud(action='offers')` lists the server types of the
    user's provider with cores, memory and price; count about 3 minutes start and the idle time before a
    server deletes itself.
  Base the time per solve on a first coarse solve of a typical part, say which numbers are guesses, and
  update the estimate when the first parts are done.
- Cloud servers cost money: start them only when the user agrees to the estimate. Then
  `cloud(action='use', target='cloud', own_server=true)` in each part's window makes its solves run on a
  server of its own; `cloud(action='stop')` deletes it when the part is done.

## When Hertzon lacks something

When a part needs more than Hertzon's tools offer (for example a Wilkinson divider over an octave needs
several sections, and the assistant builds only one), build it another way (circuit-first, `set_design()`,
`run_python()`) and tell the user what was missing. Then offer an issue for Hertzon:

- Draft it: title, what you were designing, what was missing (for example "multi-section Wilkinson in the
  Design Wizard"), how you worked around it. No project data without the user's consent.
- `report_issue()` builds the text and a link to the filled-in GitHub form. Show both and ask; issues are
  public. Post only after the user says so (with `gh issue create --repo c8501024/Hertzon` or a GitHub
  connector when one is available); otherwise the user opens the link and submits it.

## Mesh, setup and convergence

- Hertzon refines the mesh adaptively at the setup's adaptive frequencies until Delta S falls below
  MaximumDeltaS (MinimumConvergedPasses times in a row) or MaximumPasses is reached. Sweeps are solved on that
  mesh and never refine it.
- `set_setup()` changes the setup. Raising `MaximumPasses` and solving again continues from the last
  adaptive mesh, so only the extra passes cost time.
- A coarse setup (for example MaximumDeltaS 0.03, MaximumPasses 6, fields only at a few frequencies plus an
  interpolating S11 sweep without fields) solves several times faster; its numbers are rougher.
- Below MaximumDeltaS 0.01 solve time and memory grow steeply without changing design decisions, so do not
  go tighter.
- Solve time and memory grow quickly with a tighter Delta S, second-order elements, many discrete sweep
  points with fields and parallel sweeps.
- `get_status()` and `get_results()` add a `convergence_warning` when the passes stopped before the target:
  numbers from such a solve are not converged. `phase_note` says when only the phase of a small S11 kept
  Delta S above the target.
- Always tell the user when a solve did not converge: which design and setup, the Delta S it reached and
  the target, and that its numbers are not reliable. Do this for coarse screening solves too, and in the
  final summary name every result that comes from an unconverged solve. If the result matters, raising
  `MaximumPasses` continues from the last pass.

## Reading results

See the skill `results-check`: convergence, ports, power balance, the real figure of merit (for antennas the
far-field efficiency, not only S11).
