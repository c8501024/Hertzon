---
name: results-check
description: Checklist for reading and verifying Hertzon simulation results before drawing conclusions: mesh convergence, ports, power balance, efficiency split, counter tests, confirmed cause versus hypothesis. Use after every solve whose numbers lead to a decision, and to verify a final design.
---

# Checking results in Hertzon

Plausible explanations are as easy to produce as correct ones. Wrong numbers lead to convincing but wrong
conclusions. Before you act on a result, go through these checks in order and say which ones passed.

## 1. Did the mesh converge?

- `get_status()` lists the adaptive passes with tetrahedra and Delta S; `get_results()` kind `convergence`
  gives the history. `get_results()` adds a `convergence_warning` when the target was not reached; never
  ignore it.
- Delta S should fall from pass to pass. If it jumps, suspect the port or a geometry detail far below the
  mesh size.
- Deep dips and high-Q points are the least reliable. Add them as adaptive frequencies or raise the passes
  before you trust them.
- For a final answer, solve once more with a tighter Delta S and check that the figure of merit barely moves.

## 2. Is the excitation right?

- Lumped port: a small sheet between two conductors, IntegrationLine across it, nothing else touching it.
- Wave port: on the boundary, covering the whole cross section of the line, with its line impedance as expected.
- `render_image()` shows ports and boundaries on the faces; look at it.

## 3. Does the energy add up?

- For antennas: Prad + Ploss should match the accepted power (`get_results()` kind `efficiency` or
  `summary`). A power balance far from zero is a warning sign for the mesh or the port.
- For passive two-ports: |S11|^2 + |S21|^2 should not exceed 1 and should match the expected losses.

## 4. Which factor limits?

- Total efficiency = mismatch efficiency x radiation efficiency. Look at both before naming a cause.
- "Mismatch" is a claim that the structure radiates well but is badly matched. It needs a high radiation
  efficiency at that frequency, and a matching network that actually recovers the efficiency.
- Strong reflection with a high Q_Z and no resonance nearby usually means there is no radiating mode at that
  frequency. The fix is geometric, not a matching network. Look at surface currents (`set_field_plot()`).
- Separate loss from radiation with a counter test: solve once lossless (PEC metal, tan delta = 0). If the
  total efficiency barely changes, losses are not the cause.

## 5. Confirmed or hypothesis?

State a cause as confirmed only after a test that could have refuted it. Otherwise call it a hypothesis and
name the test that would decide it. In reports, mark each conclusion as one or the other.

## 6. Independent check

For a design that will be built or published, recommend an independent check: another solver, a different
mesh or basis order (BasisOrder in `set_setup()`), or a measurement. Say in the report what was checked and how.
