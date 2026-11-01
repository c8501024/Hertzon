# Derivative tuning: sliders for design variables

Derivative tuning answers the question *"what happens if this dimension is a little different?"* without
solving again. One extra computation after the solve gives a small model of the design around the solved
values. Sliders then move design variables, and every open report follows live. That covers S-parameters,
Smith charts, data tables, radiation patterns and antenna parameters. A report can also plot a quantity
against a variable instead of frequency.

The same model can drive an optimizer ([Optimize with the tuning model](#optimize-with-the-tuning-model)).
A step-by-step example (patch length and inset of a patch antenna) is in the
[tutorial](TUTORIAL.md#10-tune-a-patch-antenna-with-sliders). This page describes the workflow in
detail, then the method, its accuracy and its limits.

## Workflow

1. **Analyze the setup.** The derivatives are computed on the converged adaptive mesh of the setup.
   The sweep you want to tune must be enabled and analyzed: the reports draw the tuned curves over its
   stored solution. A disabled sweep is greyed out in the tuning panel.
2. **Open the tuning panel**: ribbon **Results > Tuning**, **Optimization > Derivative Tuning...**, or
   right-click a setup or sweep in the project tree > **Derivative Tuning...**. The panel docks at the
   right of the window, next to the reports.
3. **Choose the setup and the sweep** to tune. *Solution frequency* tunes the adaptive solution of a
   setup without a sweep.
   - A **discrete** sweep is tuned at its own points.
   - An analyzed **interpolating** sweep is tuned at the frequencies it solved. The tuned curve is
     interpolated over the sweep frequencies the same way as the solved one.
   - A **fast** sweep, or a sweep not analyzed yet, is tuned at **Points** equally spaced frequencies.
     The default is 51 and the maximum is 201.
4. **Check the variables** to tune (at most 6) and click **Compute Derivatives**. The job runs in the
   solver like an analysis, with its progress in the Progress window. A patch antenna with two variables
   and 51 frequencies takes about 85 s on a workstation. The model is stored with the results
   (`tuning_<sweep>.npz`), so it is still there the next time you open the project.
5. **Move the sliders**, or type a value next to a slider. Every open report on that solution shows the
   tuned curve **solid**, labelled with the slider values, for example `dB(S(1,1))  (tuned: h = 2mm)`.
   The solved curve stays in the plot **dashed**, in the same colour. Markers, Copy Data and Export
   include the tuned curves as drawn.
6. **Reset All** puts every slider back to its solved value. **Apply to Design** sets the design
   variables to the slider values. Analyze again to confirm the result with a full solve.

### What follows the sliders

| Report | Tuned |
| --- | --- |
| Rectangular plot, data table | S, Y, Z, VSWR and every expression of them, output variables included |
| Smith chart | S-parameters |
| Radiation pattern (2D cut) | Gain, realized gain, directivity, rE, axial ratio, ... at the report frequency |
| 3D polar plot | The tuned pattern, with the solved maximum in the info line |
| Antenna Parameters plot and table | Peak realized gain, radiation efficiency, front-to-back ratio, ... over frequency |
| Fields Calculator traces, field plots | Not tuned (the model has no volume fields) |

Patterns and antenna parameters need the fields on the far field surface. The design needs a radiation
or PML boundary and a far field setup. They are tuned at the frequencies of the model that also have a
solved far field. Over a sweep, that means a discrete sweep with **Save fields**. A pattern report on
another solution of the tuned setup also follows the sliders where the frequencies match, for example
LastAdaptive while a sweep is tuned. When a report cannot show a tuned curve, its info line says why.

### Plot against a variable

Rectangular plots, data tables and Antenna Parameters reports of a solution with a tuning model have an
**X axis** choice. It is either **Frequency** or one of the tuned variables. With a variable, **At** picks
the frequency. The curve runs over the whole tuning range of that variable, with the other variables at
their slider values (their solved values while the panel is closed).

- **Dashed vertical line:** the solved value.
- **Solid vertical line:** the current slider value.
- **Shaded:** the outer 40 % of the range on each side, where the accuracy drops (see below).

This works without the tuning panel open, from the stored model. An S-parameter plot has 101 points.
An Antenna Parameters plot has 41, because every point needs its own far field.

## Optimize with the tuning model

The optimizer **Derivative Trust Region** (Optimization > Add Optimization > Optimizer tab) uses the tuning
model instead of many solves. Goals, variables and cost function are the same as for Conjugate Gradient.
Every round:

1. **Solve** the design.
2. **Compute its tuning model** on the adaptive mesh of that solve, with the included variables (at most 6).
3. **Minimize the goals on the model.** One model evaluation costs a few milliseconds, so a search over the
   variables (Powell's method from several starting points) takes about a second. The step stays within
   the *trust region*: a fraction of each variable's tuning range, 60 % at the start, and within Min and
   Max.
4. **Check the step with a solve.** The ratio of the actual to the predicted improvement decides:
   - at least 0.1: the step is taken, and the next round computes a new model around the new design;
   - at least 0.75 with the step at the edge of the region: the region doubles (up to the whole range);
   - below 0.25: the region halves. A rejected step reuses the current model.

The optimizer stops when every goal is met (cost 0), when the model finds no further improvement, when the
region becomes too small, or after *Maximum rounds*. A round costs one solve, plus one tuning model when
the step is taken.

Two examples on the patch antenna (variables Lp and inset, discrete sweep 2.2 to 2.7 GHz):

- Lp = 28.5 mm, inset = 8 mm: the dip was at 2.475 GHz, S11 = −12.6 dB at 2.45 GHz. With the goal
  dB(S(1,1)) ≤ −10 from 2.43 to 2.47 GHz, the first model step (Lp +0.18 mm, inset +0.36 mm) moved the dip to
  2.45 GHz with −20.9 dB: two solves and one tuning model.
- Lp = 31 mm, inset = 4 mm: the dip was at 2.275 GHz (−9.3 dB), S11 = −1.9 dB at 2.45 GHz. With the goal
  ≤ −15 dB from 2.43 to 2.47 GHz, the first step shortened Lp by 2.4 mm, the next ones raised the inset to
  8.8 mm. After six solves and five tuning models the dip was at 2.45 GHz with −34.9 dB (Lp = 28.79 mm). In
  every round the solve confirmed the predicted improvement (ratio 0.5 to 1.2). The last two rounds were
  small corrections: every solve refines its own mesh, so the solved cost moves a little from mesh to mesh.

**Apply to Design** on an evaluation (Optimization results) sets its variables and shows its solution without
solving again: S-parameters, reports and the adaptive passes, and for the best evaluation also the mesh and
the fields at the adaptive frequency. **Results > Solution Data > Design Variation** lists every evaluation
with its passes. The wizard **Tools > Antenna Design > Rectangular Patch** uses this optimizer with a goal at
the design frequency only, on the adaptive solution, so no sweep is solved per round.

Use it for designs that are near their goals already, for example from a design formula or a wizard. A
variable that changes the topology, or a step beyond the tuning range, needs Conjugate Gradient.

## Method

The idea is to approximate the **FEM system matrix**, not the S-parameters. A Taylor series of S(p) would
smear a resonance that moves with the variable. A Taylor series of the matrix A(p) is smooth, and solving
the approximate system lets the resonance move.

### 1. Morphing the mesh

To differentiate the matrix with respect to a dimension, the mesh must change smoothly with it. A remesh
would change the connectivity and make the matrix jump. Hertzon therefore keeps the converged adaptive
mesh and **moves its nodes** (`hertzon/solver/morph.py`):

- The model is built twice more in scratch CAD models, with the variable a small step below and above
  its value. Comparing the faces with the nominal build gives the motion of every CAD face per unit of
  the variable, its **velocity**.
- A node on a moving **plane face** follows the face. The motion of the face boundary is spread over the
  face with mean value coordinates, which is exact for any affine motion, so neighbouring faces agree on
  their common edges. A **curved face** may only translate.
- A node on a face that stays put may slide within that face. The mesh next to a moving edge then
  stretches over the whole face, not just one element layer. Nodes on plane parts of the outer
  boundary slide within it, so a substrate that fills the region can change its height.
- Nodes on wave and Floquet ports, and on curved parts of the outer boundary, stay where they are.
  **Lumped ports and lumped RLC elements stretch** with the mesh: their width, height, integration line
  and excitation follow.
- All interior nodes follow a smooth extension of the boundary motion. This is a vector Laplace problem
  on the tetrahedra, stiffened in small elements (weight 1/volume). The fine mesh near conductors then
  moves almost rigidly, and the large elements absorb the deformation.

The result is a velocity field V for every variable. The mesh at an offset dp is `nodes + dp · V`, with
the same connectivity.

### 2. Derivatives of the matrix

At every frequency the system is assembled on the nominal mesh and on slightly morphed meshes. Central
differences give the first and second derivatives with respect to every variable, plus the mixed second
derivatives:

```
A(p0 + dp) ≈ A0 + Σ dp_i A_i + ½ Σ dp_i dp_j A_ij        (the same for the right-hand side b)
```

### 3. One factorization, a reduced basis

The nominal system is **factorized once**. The same factorization gives the solution and its first and
second derivatives:

```
A0 x0 = b0
A0 x_i  = b_i − A_i x0
A0 x_ij = b_ij − (A_ij x0 + A_i x_j + A_j x_i)
```

These vectors span a small basis W, with a few dozen columns instead of hundreds of thousands of
unknowns. The matrix terms are projected onto it (`Wᵀ A W`), and the S-parameter functionals of the
ports are evaluated on its columns.

### 4. One basis for the whole band

With several frequencies, one basis serves the whole band. The volume matrices of every mesh state do
not depend on frequency, so they are assembled once. Only the boundary terms are assembled per
frequency. The basis holds the solutions and derivatives of a few **expansion frequencies**, added where
the residual of the reduced solution is largest, until it is below 1e-4 at every frequency. Only those
few frequencies are factorized. Every other frequency is still the exact FEM system, solved in the
basis. For the patch antenna (two variables, 51 frequencies) this took 85 s instead of 204 s, with the
same response within 3e-6. If a variable makes the system non-affine, every frequency is reduced on its
own instead.

### 5. A slider move

A slider position costs one small dense solve per frequency:

```
(Wᵀ A(p0 + dp) W) y = Wᵀ b(p0 + dp),     S = L W y
```

All frequencies are solved in one batch, so the sliders move smoothly. At `dp = 0` the result is the
solved response exactly.

### 6. Far fields

The model also keeps the fields of its basis vectors on the far field surface. That is the tangential
E and the curl term that gives H, either once for the band or once per frequency. The tuned surface
field is a sum over the reduced solution `y`. The far field follows from the same Stratton-Chu integral
as for a solved field, with the mirror images at symmetry planes and the scaling to the incident power.

Because the far field is linear in `y`, the far fields of the basis vectors are computed once per
frequency and direction grid. That step shows a progress bar and takes about 1.5 s per frequency for the
patch on a 2° grid. After that, a slider move only sums them.

The accepted power comes from the tuned S-parameters (1 − Σ|S_i,port|²). The radiated power is
integrated from the tuned surface fields. Realized gain, efficiency and the other antenna parameters
are computed from those two powers.

## Accuracy

- **At the solved values** the model reproduces the solve exactly: S-parameters, far field and antenna
  parameters. For the patch, the tuned far field equals the solved one within 1e-8.
- **Near the solved values** the matrix approximation is second order in dp. Two checks:
  - On a microstrip stub, the notch moved 0.3 GHz per millimetre of stub length. Over ±2 mm it stayed
    within 0.01 of exact solves on the morphed mesh.
  - On the patch antenna with the substrate height +0.2 mm, peak realized gain matched a remeshed solve
    within 0.01 to 0.09 dB, and radiation efficiency within about 0.1 percentage point.
- **The range of every slider** is limited to the offsets at which no tetrahedron shrinks below 30 % of
  its volume. A positive length also stays above 10 % of its value.
- **The further a slider is from the solved value, the larger the error.** The outer 40 % of each side
  of the range is marked: an orange hint under the sliders, and the shaded areas in a plot against a
  variable. Use the sliders to find the direction and the size of a change. Then **Apply to Design** and
  analyze again: the full solve is the reference.
- If the design or the setup changes after the analysis, the panel says so. The model still shows the
  analyzed design.

## Limits

- At most 6 variables at a time.
- A variable must move faces of the model, not change its topology. Plane faces may change shape.
  Curved faces may only move. Wave and Floquet ports stay in place, so a variable that moves a wave port
  face cannot be tuned.
- Not yet for terminal designs.
- Fields Calculator quantities and field plots are not tuned: the model has no volume fields.
- Patterns and antenna parameters exist only at frequencies with a solved far field, as for a solve.
  With **Edit Sources**, the tuned far field excites the first port with 1 W.

## Files

- `hertzon/solver/morph.py`: velocity fields, valid ranges.
- `hertzon/solver/tuning.py`: derivatives, reduced and band models, far field basis, the `TuningModel`
  that evaluates slider positions.
- `hertzon/gui/tuning.py`: the tuning panel and the shared tuning state that the reports listen to.
- `tuning_<sweep>.npz` in the result folder of the setup: the stored model (see
  [RESULT_FILES.md](RESULT_FILES.md)).
