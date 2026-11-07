# Waiting on EMerge

Hertzon uses a pinned commit of EMerge's `hertzon` branch (`packaging/emerge-commit`). This list
says which EMerge fixes Hertzon is waiting for, what in Hertzon depends on each one, and what to check once
it arrives. Background on every EMerge change is in `docs/EMERGE_INTEGRATION.md` and
`docs/EMERGE_CHANGES.md`.

**When `packaging/emerge-commit` moves to a new EMerge commit:** go through this list. For every entry whose
fix is in that commit, run its check. Only when the check passes, remove the workaround (or enable the
feature) in its own PR and move the entry to "Done" below.

## Open: workarounds waiting for an EMerge fix

(none)

## Open: Hertzon features waiting for an EMerge fix

(none)

## Done

| EMerge fix | Removed Hertzon workaround | EMerge version or commit |
|---|---|---|
| Parallel sweep threads share one solver (`AutomaticRoutine` singleton) | `mt_pardiso._duplicate` | `hertzon` branch `2811e9a` |
| Factorizations of sweep worker threads are released on their own thread (FennisRobert/EMerge#72) | `mt_pardiso.release` | `hertzon` branch `2811e9a` |
| ThinConductor sheet split on both sides in the solve | `emerge_patches.apply_thin_conductor` | `hertzon` branch `2811e9a` |
| Port mode solve stops on `ArpackNoConvergence` | `emerge_patches._robust_eigs` | 3.0.0b5 |
| Thin conductor split after a new mesh | `_thin_conductor_new_mesh` | 3.0.0b5 |
| Far field of all ports in one pass, Rust kernel `emr.sc_multi` (FennisRobert/EMerge#76) | numpy/numba kernel in `farfield_post.stratton_chu_multi` | `hertzon` branch `e03a085` |
| Curved (isoparametric) elements, `Settings.mw_curved_elements` (FennisRobert/EMerge#77) | `emerge_patches.apply_curved`, the element matrices in `curvilinear.py`, `ned2_kernels.py` | `hertzon` branch `e03a085` |
| Parallel removal of the PEC rows and columns (FennisRobert/EMerge#78) | `fast_assembly.submatrix` and its source patch of `SolveRoutine.solve` | `hertzon` branch `e03a085` |
| Far field with symmetry planes: the radiated power is not scaled (FennisRobert/EMerge#75) | Feature enabled: `farfield_post` mirrors the stored surface at symmetry planes with the same scaling (Hertzon does not call EMerge's `farfield(syms=...)`) | `main` `0a607f2` |
