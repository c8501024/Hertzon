---
description: Verify the current Hertzon design before trusting or reporting its results (convergence, ports, power balance, efficiency split, counter tests)
argument-hint: [design or setup to check, optional]
---

Verify the current Hertzon design $ARGUMENTS with the skill `results-check`. Do not change the design itself.

1. `get_context()` and `get_project()` to find the open project, the design, its setups and its goal (the
   design log, if there is one).
2. Go through the checks of `results-check`, from what is already solved. If a check needs a new solve
   (lossless counter test, far fields at in-band frequencies), say which and how long it would take.
3. Answer with a short table: check, result, passed or not. Then the figure of merit with its uncertainty, which
   statements are confirmed and which are hypotheses, and what would still be needed for an independent check.
