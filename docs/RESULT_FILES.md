# Result files

Every analysis setup writes its results to `<project>.hznresults/<design>/<setup>/`. The GUI, the MCP server
and the reports read these files through `hertzon.solver.results.ResultStore`; this page is for reading them
from your own scripts. `<solution>` is `LastAdaptive` or the name of a sweep.

## status.json

`{"state": ..., ...}` with the state `running`, `meshed`, `done`, `failed` (with `error`) or `aborted`.

A running solve records the process id and the computer (`pid`, `host`). When a solver process is killed
or crashes before it can write its final state, the file still says `running`;
`hertzon.solver.results.run_status(folder)` then reports `aborted` (with `"stale": true`) once that process
no longer exists on this computer. Use it instead of reading the file directly; the GUI, the MCP server and
the result trees do.

## fields_\<solution\>.npz: E and H per tetrahedron

The fields are stored **per tetrahedron**, not per mesh node:

| key | content |
|---|---|
| `tet4` or `tet10` | which points each tetrahedron has: 4 corners, or 4 corners + 6 edge midpoints |
| `layout` | the same description in words |
| `E_<k>_<port>`, `H_<k>_<port>` | complex (n_tets · npt, 3): row `t*npt + i` is point `i` of tetrahedron `t` |
| `freq_<k>` | frequency of index `k` in Hz |

Reshape to `(n_tets, npt, 3)`; `t` indexes the tetrahedra `tets` of `mesh.npz` (nodes in `nodes`). The midpoints
are in VTK quadratic tetra order, edges (0,1), (1,2), (0,2), (0,3), (1,3), (2,3). At curved elements the
midpoints lie on the curved edge (the plots use the same points).

Each value comes from its own tetrahedron, so a node shared by several tetrahedra has several values: the
normal E jumps at material interfaces, and the tangential components of a second-order solution differ
slightly between neighbours. That spread is the discretisation, not noise. Read the values per tetrahedron;
averaging at the nodes (or taking whichever tetrahedron comes first) mixes values from both sides of an
interface.

After a solve only `solution_<solution>.npz` (the FEM coefficients) may exist; `fields_<solution>.npz` is
computed from it the first time a plot or `ResultStore.npz("fields_<solution>.npz")` asks for it.

## farfield_\<sphere\>_\<solution\>.npz: far field of an infinite sphere

| key | content |
|---|---|
| `theta`, `phi` | the angles in radians (the grid of the sphere) |
| `sampling` | the grid in words, e.g. `theta 0..180 deg step 2 (91), phi 0..360 deg step 2 (181)` |
| `E_<k>_<port>` | complex far field r·E, cartesian components, shape (3, n_theta, n_phi) |
| `Prad_<k>_<port>`, `freq_<k>` | radiated power in W, frequency in Hz |
| `sig` | the sphere settings the file was computed with |

The far field is computed from the stored boundary fields (`huygens_<solution>.npz`). The file is a cache: when
the sphere's settings (angles, steps, coordinate system) differ from `sig`, the next read through
`hertzon.solver.farfield_post.farfield` computes it again and overwrites it. Check `sampling` (or `theta`/`phi`)
before you compare files from different runs: values read off a sampled pattern, such as a -3 dB beam width,
depend on the step.

## Gain, directivity and realized gain are linear

`DirTotal`, `GainTotal`, `RealizedGainTotal` and their Theta/Phi/LHCP/RHCP parts, as returned by
`hertzon.solver.ffquant.farfield_quantities` and used in equations, are **linear power ratios**. A half-wave
dipole has `DirTotal` ≈ 1.64, which is 2.15 dBi. Use `dB(GainTotal)` (= 10·log10) in reports and equations for
dBi; the MCP far field results are already in dBi and say so in `unit`. `rETotal`, `rETheta` and `rEPhi` are
field magnitudes in V.

With a full sphere (θ 0…180°, φ over 360°) the directivity is normalised by the power integrated over the
sphere's own grid, otherwise by the radiated power of the solve; a coarse grid can shift the peak a little.
