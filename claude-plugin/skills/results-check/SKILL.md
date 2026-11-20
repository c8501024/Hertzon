---
name: results-check
description: "Checklist for reading and verifying Hertzon simulation results before drawing conclusions: mesh convergence, ports, power balance, efficiency split, counter tests, confirmed cause versus hypothesis. Use after every solve whose numbers lead to a decision, and to verify a final design."
---

# Checking results in Hertzon

Plausible explanations are as easy to produce as correct ones. Wrong numbers lead to convincing but wrong
conclusions. These are the checks that tell a trustworthy result from a wrong one.

## 1. Did the mesh converge?

- `get_status()` lists the adaptive passes with tetrahedra and Delta S; `get_results()` kind `convergence`
  gives the history; `show_result()` with what `solutions` shows the convergence table to the user.
  `get_status()` and `get_results()` add a `convergence_warning` when the target was not reached: such
  numbers are not converged. Raising `MaximumPasses` and solving again continues from the last mesh.
- Delta S should fall from pass to pass. If it jumps, suspect the port or a geometry detail far below the
  mesh size.
- Deep dips and high-Q points are the least reliable. Say so in the report.

## 2. Is the excitation right?

- Lumped port: a small sheet between two conductors, IntegrationLine across it, nothing else touching it.
- Wave port: on the boundary, covering the whole cross section of the line, with its line impedance as expected.
- Wave port with several modes: `get_results()` kind `ports` names every mode ("P1 M2 (TE10-like, beta ...)"),
  with beta, Zo and its symmetry; `image=true` shows the mode fields. Check that the modes are the ones
  intended before reading S of a mode; field plots take `port` and `mode`.
- `render_image()` shows ports and boundaries on the faces; look at it.

## 3. Does the energy add up?

- For antennas: Prad + Ploss should match the accepted power (`get_results()` kind `efficiency` or
  `summary`). A power balance far from zero is a warning sign for the mesh or the port. On a coarse
  screening mesh with a coarse far-field sphere (10 degrees) a few percent are normal; the balance says
  something about the port only on a converged mesh.
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

An independent check (another solver or a measurement) is the strongest confirmation for a design that will be
built or published.
