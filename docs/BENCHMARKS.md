# Benchmarks

Timings of **Tools > Solver Benchmark** on a workstation. Every core count solves the whole setup in a fresh
solver process: initial mesh, adaptive passes and a 101-point interpolating frequency sweep. The projects are
in [`examples/benchmark/scaling`](../examples/benchmark/scaling), the raw results of every run in
[`examples/benchmark/scaling/results`](../examples/benchmark/scaling/results). Run the same benchmark on your
own computer to find the best number of cores and sweep processes for it.

## Hertzon Bench

The first tab of **Tools > Solver Benchmark** runs *Hertzon Bench* (currently Hertzon Bench 2), a fixed suite of eight models in
[`examples/benchmark/suite`](../examples/benchmark/suite) in two parts with their own score, each model solved
once with all cores:

| Part | Test | What it measures |
|---|---|---|
| RF | Microstrip line | Two lumped ports, first order, 1–6 GHz sweep: start-up and small matrices |
| RF | Hairpin filter | Three resonators on 0.508 mm, second order, interpolating sweep |
| RF | Patch antenna | Radiation boundary, far field, broadband 1.2–3.7 GHz sweep |
| RF | Patch on 0.1 mm | Very thin substrate: the mesh of thin layers |
| RF | Waveguide post | WR90 wave ports, 8.2–12.4 GHz sweep |
| RCS | Sphere RCS | Incident plane wave, monostatic RCS of a PEC sphere (2 GHz) |
| RCS | Dielectric sphere RCS | Incident plane wave, lossless dielectric sphere (εr = 4, 1 GHz): fields inside and outside |
| RCS | Drone RCS (medium) | Imported surface of a delta-wing drone, PEC, 0.5 GHz: a realistic model, about 6 GB |

Every test of the parts runs on a computer with 16 GB of memory, and every computer, also one with much more
memory, runs the same tests, so their scores compare directly. Workstation tests are the optional extra for
high-memory machines (below).

The parts can be run on their own (check boxes in the window, `--parts rf` or `--parts rcs` on the command line).

Every result is checked: the S-parameters against the reference run (largest |ΔS| at most 0.05), the RCS of the
spheres against the exact Mie solution (at most 1 dB: at the fixed number of passes the RCS of a sphere still moves by about ±0.5 dB from pass to pass, and Windows and Linux refine slightly different meshes), the RCS of the drones against the reference run (at most 1 dB). A test with a wrong result gets no score. The score of a
test is the reference time divided by your time, times 1000; a part score (RF, RCS) is the geometric mean of its
tests, and the overall score is the geometric mean of the two part scores.
Every test solves a fixed number of adaptive passes, the number of its reference run (Minimum = Maximum
Passes in its setup), so every computer does the same work: the initial mesh differs slightly between operating
systems, and with a convergence criterion a computer could otherwise stop a pass earlier, solve a much smaller
mesh and get an unfairly high score. A test counts only when it solved all its passes with the matrix solver that
was asked for and its result is correct. When the memory guard stopped its refinement, the operating system ended
the solver for lack of memory, the solver fell back to another one (e.g. no usable GPU for SPARTA) or it stopped
before its last pass, the test has no score and is marked with the reason. A part has a score only when every one of its tests counts, and the overall score only
when both parts have one; a failed RCS test leaves the RF score valid, and that result can still be submitted. SuperLU is not offered: it is far too
slow for these models.

**Workstation tests.** Two tests are meant for workstations with much more memory than 16 GB (the check box
*Workstation tests* in the window, `--no-large` leaves them out). Each runs only when the computer has enough
usable memory for it, else it is listed as not run with the reason, and each has its own score outside the part
and overall scores, so that these stay comparable for computers with 16 GB.

| Part | Test | Mesh | Memory | Reference time |
|---|---|---|---|---|
| RF | Rotman lens array: 24 GHz Rotman lens with its patch array on RO4350B, 11 lumped ports, second order, far field, discrete sweep at 22, 24, 26 GHz | 144k tetrahedra, 8 passes | about 26 GB | 446 s |
| RCS | Drone RCS: imported surface of a delta-wing drone, PEC, 1 GHz, monostatic RCS | 183k tetrahedra, 5 passes | about 36 GB | 282 s |

Their score 1000 is a 32-core AMD Ryzen Threadripper 3970X (256 GB, Windows, all 64 threads) with Hertzon 0.4.6.

**Two benchmarks.** The *full benchmark* (multi-core, on by default) solves every test of the chosen parts with
all cores and gives the scores above. The *scaling benchmark* (single- to multi-core, off by default because it
takes longer) solves one test per part with 1, 2, 4, ... up to all cores: the hairpin filter for RF (1-core
reference 207 s) and the PEC sphere for RCS (84 s). Its 1-core times give the *single-core scores* (per part and,
with both parts, overall), the other runs speedup and parallel efficiency. The site ranks multi-core and
single-core separately, like other benchmarks, and plots the speedup of every result. Both can be run alone or
together (`--no-full`, `--scaling` on the command line).

The reference computer (score 1000) is a 4-core cloud server (Intel Xeon 2.1 GHz, 16 GB, Linux) with Hertzon
0.4.6. The suite takes about 5 to 15 minutes and up to 9 GB of memory (16 GB recommended).

**Submit Results** sends the result to the comparison site, [hertzon-bench.pages.dev](https://hertzon-bench.pages.dev), after showing exactly what is sent and asking for
consent: the times, passes, tetrahedra, memory, checks and scores, and the description of the computer (device
model, CPU, cores, clock, memory size, type and speed, GPU, operating system, Python, NumPy, SciPy, BLAS and
EMerge versions, power source, whether it is a virtual machine, and the memory bandwidth Hertzon measures by
copying a 256 MB array). Not sent: user or computer name, serial numbers, network addresses, paths, projects. The
site ranks the results by system (the median of repeated runs), filters by operating system, architecture,
device, solver and Hertzon version, and shows for each version how the same hardware changed against the
version before. Each submission gets a delete key to remove it again.

Without the window: `python -m hertzon.solver.suite OUTDIR [--solver NAME] [--cores N]`.
The site itself is in [`bench-site`](../bench-site) (Cloudflare Pages with a D1 database).

## Computer

| | |
|---|---|
| CPU | AMD Zen 2 (reported as "AMD64 Family 23 Model 49"), 32 cores, 64 threads |
| Memory | 256 GB |
| System | Windows 10, Python 3.13 |

Measured on 25 October 2026 with version 0.2.3 (EMerge 3.0.0b5, hzAURA refinement), one run after the
other on an otherwise idle computer. The earlier runs on the same computer (18 and 19 October, with the
refinement of that time) are compared [below](#compared-with-the-runs-of-19-october).

## Broadband sweeps

The cases sweep a wide band, not a few frequencies around one point: the patch antenna from 1.225 to
3.675 GHz (3:1, half to one and a half times its 2.45 GHz resonance), the microstrip from 1 to 6 GHz (6:1),
the hairpin filter from 2 to 2.9 GHz. Each sweep has 101 points. The interpolating sweep solves only some of
them (circles) and fits a rational model through them, adding frequencies until the model changes by less
than the tolerance between steps. For the patch, 6 of its 21 solved frequencies come from the reduced basis
of the earlier solutions instead of a new factorization, so 15 factorizations cover the whole 3:1 band.

![S-parameters of the sweeps with the solved frequencies](benchmark/sweeps.png)

*Results of the 64-core runs. Line: the 101 sweep points. Circles: the frequencies that were solved.
Dashed: S11 of the runs of 19 October.*

## Patch antenna (examples/benchmark/scaling/large_patch.hzn)

2.45 GHz patch antenna, radiation boundary, second-order elements. 8 adaptive passes up to 34.5k
tetrahedra (319,488 unknowns, 2.7 GB per factorization, 7.2 GB peak memory), then the 1.225–3.675 GHz sweep.

| Cores | Total | Adaptive passes | Sweep | Speedup |
|---|---|---|---|---|
| 1 | 370 s | 83 s | 284 s | 1.0 |
| 2 | 228 s | 58 s | 167 s | 1.6 |
| 4 | 146 s | 42 s | 101 s | 2.5 |
| 8 | 103 s | 33 s | 66 s | 3.6 |
| 16 | 85 s | 30 s | 52 s | 4.3 |
| 32 | 81 s | 30 s | 48 s | 4.6 |
| 64 | 82 s | 31 s | 49 s | 4.5 |

Sweep with parallel processes (32 cores shared): 1 process 51 s, **2 processes 34 s**, 4 processes 34 s,
8 processes 38 s.

## Small microstrip (examples/benchmark/scaling/small_microstrip.hzn)

50 Ω microstrip with two lumped ports. 5 adaptive passes up to 13.4k tetrahedra (90,042 unknowns, 1.7 GB peak
memory), then the 1–6 GHz sweep with 17 solved frequencies.

| Cores | Total | Adaptive passes | Sweep | Speedup |
|---|---|---|---|---|
| 1 | 50 s | 17 s | 29 s | 1.0 |
| 2 | 33 s | 12 s | 18 s | 1.5 |
| 4 | 25 s | 10 s | 13 s | 2.0 |
| 8 | 22 s | 9 s | 10 s | 2.3 |
| 16 | 21 s | 9 s | 9 s | 2.4 |
| 32 | 21 s | 9 s | 9 s | 2.4 |
| 64 | 21 s | 9 s | 9 s | 2.4 |

Sweep with parallel processes (16 cores shared): 1 process 10 s, 2 processes 8.8 s, **4 processes 7.8 s**,
8 or more processes 10 s.

## Hairpin filter (examples/benchmark/scaling/medium_hairpin.hzn)

Band-pass filter with three hairpin resonators and two ports. 5 adaptive passes up to 18.5k tetrahedra
(190,587 unknowns, 1.8 GB per factorization, 4.4 GB peak memory), then the 2–2.9 GHz sweep with 17 solved
frequencies (7 factorizations). Not part of the earlier runs.

| Cores | Total | Adaptive passes | Sweep | Speedup |
|---|---|---|---|---|
| 1 | 145 s | 45 s | 97 s | 1.0 |
| 2 | 93 s | 32 s | 58 s | 1.6 |
| 4 | 65 s | 26 s | 36 s | 2.2 |
| 8 | 49 s | 21 s | 24 s | 3.0 |
| 16 | 43 s | 20 s | 20 s | 3.4 |
| 32 | 42 s | 20 s | 19 s | 3.5 |
| 64 | 42 s | 20 s | 19 s | 3.5 |

Sweep with parallel processes (32 cores shared): 1 process 21 s, 2 processes 15 s, **4 processes 15 s**,
8 processes 17 s.

## Compared with the runs of 19 October

Same computer, same projects and the same benchmark settings (cores 1 to 64, 101-point sweeps). The runs of
18 and 19 October used the mesh refinement of that time; with hzAURA the adaptive passes end on a different
mesh, so the mesh and the solve times changed together.

**Patch antenna**: before 66.9k tetrahedra in 10 passes (612,762 unknowns, 15.4 GB peak memory), now 34.5k in
8 passes (319,488 unknowns, 7.2 GB). Both stop on the same Maximum Delta S; the swept S11 agrees within
0.011 (resonance at 2.47 GHz, −25.1 dB, in both), see the plot above.

| Cores | Total before | Total now | Passes before | Passes now | Sweep before | Sweep now | Faster |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1375 s | 370 s | 315 s | 83 s | 1043 s | 284 s | 3.7× |
| 2 | 777 s | 228 s | 173 s | 58 s | 601 s | 167 s | 3.4× |
| 4 | 475 s | 146 s | 103 s | 42 s | 368 s | 101 s | 3.3× |
| 8 | 303 s | 103 s | 68 s | 33 s | 231 s | 66 s | 2.9× |
| 16 | 230 s | 85 s | 51 s | 30 s | 174 s | 52 s | 2.7× |
| 32 | 207 s | 81 s | 48 s | 30 s | 155 s | 48 s | 2.6× |
| 64 | 203 s | 82 s | 44 s | 31 s | 154 s | 49 s | 2.5× |

Fastest sweep with parallel processes: before 122 s (2 processes, 64 cores), now 34 s (2 processes, 32 cores).

**Small microstrip**: before 11.6k tetrahedra in 4 passes (2.0 GB peak memory), now 13.4k in 5 passes (1.7 GB),
17 instead of 19 solved frequencies. The mesh is a little larger and every run is still faster. S11 stays
below −21 dB in both runs; at that level the two curves differ by up to 0.024.

| Cores | Total before | Total now | Passes before | Passes now | Sweep before | Sweep now | Faster |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 78 s | 50 s | 20 s | 17 s | 54 s | 29 s | 1.6× |
| 2 | 53 s | 33 s | 16 s | 12 s | 33 s | 18 s | 1.6× |
| 4 | 39 s | 25 s | 14 s | 10 s | 21 s | 13 s | 1.5× |
| 8 | 32 s | 22 s | 13 s | 9 s | 15 s | 10 s | 1.5× |
| 16 | 30 s | 21 s | 13 s | 9 s | 13 s | 9 s | 1.4× |
| 32 | 29 s | 21 s | 12 s | 9 s | 13 s | 9 s | 1.4× |
| 64 | 29 s | 21 s | 12 s | 9 s | 13 s | 9 s | 1.4× |

Fastest sweep with parallel processes: before 10.5 s (8 processes, 64 cores), now 7.8 s (4 processes, 16 cores).

## What this means

* The patch antenna is solved 2.5 to 3.7 times faster than on 19 October, with the same result. Most of it
  comes from the smaller mesh that hzAURA converges to (half the unknowns); the microstrip, whose mesh grew a
  little, is still 1.4 to 1.6 times faster.
* The sweep is half to three quarters of the time of the patch and the filter. Its cost is the number of
  factorizations, not the number of sweep points.
* The models scale well up to 8 cores and gain little above 16, most likely because the sparse factorization
  is limited by memory bandwidth rather than by the number of cores.
* On a many-core computer, 2 to 4 parallel sweep processes cut the sweep by another 20 to 33 %.
* The **Fast** sweep factorizes once at the band centre (a few times for very wide bands) and could cut
  the sweep further; it was not part of this benchmark.
