"""Remote solving: the Hertzon GUI sends an analysis to another computer and gets the results back.

The other computer runs a normal Hertzon installation and the remote service (``hertzon-serve`` or
``python -m hertzon.remote serve``). The GUI talks to it over HTTP with a token: it uploads the project and
the job's results folder, reads the solver's progress lines while it runs, can abort it, and mirrors the
results folder back next to the local project. The solver itself is the same headless process the GUI starts
locally (``hertzon.solver.run`` and friends), so a remote result is the same result.

Only the standard library is used here, so the service also runs without the GUI packages.
"""
from __future__ import annotations

PROTOCOL = 1                 # raised when client and service can no longer talk to each other
DEFAULT_PORT = 47820
