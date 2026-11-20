---
description: Design an RF structure (antenna, filter, coupler, matching network, circuit) in Hertzon from a specification, verified and with a report
argument-hint: <what to design, e.g. "2.45 GHz patch antenna on 1.6 mm FR4, S11 < -15 dB">
---

Design the following with Hertzon: $ARGUMENTS

The skills `hf-design-loop`, `antenna-design`, `circuit-first` and `results-check` describe what Hertzon offers
and how to read its results; the approach is yours.

- If no specification is given above, ask the user what to design and the numbers it must reach.
- Ask the user only about what changes the goal (environment, figure of merit, what may change). Choose and
  record sensible defaults for everything else.
- Before an analysis that will take long, say how long, then wait for it with `get_status()` and `wait_seconds`.
- End with a short report: specification and result side by side, final values, where the project is.
