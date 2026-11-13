# Notes for contributors and coding assistants

- Follow the design principles in [docs/PRINCIPLES.md](docs/PRINCIPLES.md). In particular, every export
  (images, GIFs, plots, reports, data) must be WYSIWYG: it reproduces exactly what the user sees, with the
  same size, speed, colours, scales and view. Any deviation is an explicit user option, never a default.
- Pull requests target the `dev` branch (see "Branches and releases" in the README).
- Any change that can affect meshing, adaptive refinement, ports or the solver is measured against the last
  release before the pull request: initial and final tetrahedra, passes, total time, peak memory and the
  S-parameters against a finer reference, at least on a thin substrate (patch on 0.1 and 0.25 mm), the
  edge-coupled filter on 0.508 mm and the hairpin filter. Put the table in the pull request. A model that gets
  slower (beyond about 10 % noise), needs more memory or gets less accurate is never accepted as the price of
  something else without the maintainer's explicit approval. `tests/test_mesh_gate.py` checks the initial mesh
  sizes in CI; update its baselines only after that approval.
