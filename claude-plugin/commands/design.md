---
description: Design an RF structure (antenna, filter, coupler, matching network, circuit) in Hertzon from a specification, verified and with a report
argument-hint: <what to design, e.g. "2.45 GHz patch antenna on 1.6 mm FR4, S11 < -15 dB">
---

Design the following with Hertzon: $ARGUMENTS

Follow the skill `hf-design-loop` step by step. For antennas also use `antenna-design`, and check every result
that leads to a decision with `results-check`.

- If no specification is given above, ask the user what to design and the numbers it must reach.
- Begin with `get_context()` and write the goal into the design log before you build anything.
- Ask the user only about what changes the goal (environment, figure of merit, what may change). Choose and
  record sensible defaults for everything else.
- Before an analysis that will take long, say how long, and stop rather than polling.
- End with the verification and the report of the design loop.
