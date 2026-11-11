# Command line (headless)

`hertzon-cli` solves a Hertzon design without the window and writes its S-parameters as a Touchstone file. It
is meant for scripts, batch runs and for other programs that use Hertzon as their EM engine: a circuit
simulator can call it for a 3D block, set the block's variables, and read the `.sNp` file back.

```
hertzon-cli version [--json]
hertzon-cli describe PROJECT.hzn [--design NAME]
hertzon-cli solve PROJECT.hzn [--design NAME] [--setup NAME] [--sweep NAME]
                  [--set VAR=VALUE ...] [--freq START:STOP:COUNT | --points F1,F2,...]
                  [--sweep-type discrete|interpolating|fast] [-o OUT.sNp | -o FOLDER | -o -]
                  [--cores N] [--force] [--fields] [--validate] [--require-convergence]
                  [--progress text|json|none] [--json]
```

`python -m hertzon solve ...` and `python -m hertzon.cli solve ...` do the same. Where the command is:

* Windows: `%LOCALAPPDATA%\Programs\Hertzon\.venv\Scripts\hertzon-cli.exe`
* Linux: `~/.local/share/hertzon/.venv/bin/hertzon-cli` (or the `.venv/bin` of the folder Hertzon was
  installed to)
* From source: `pip install -e .` puts `hertzon-cli` on the path of that environment.

The project file is never changed. All frequencies are in **Hz** unless a unit is given (`2.4GHz`), all
lengths in the JSON in **metres**, impedances in **ohm**. Ports are identified by their **number**; the rows
of the Touchstone file are in port number order (a wave port with several modes has one row per mode).

## Examples

```bash
# what can be set and which ports there are
hertzon-cli describe Filter.hzn --design Hairpin

# solve with the sweep of the setup, Touchstone next to it
hertzon-cli solve Filter.hzn --design Hairpin -o hairpin.s2p

# other dimensions and another band; machine-readable progress and summary
hertzon-cli solve Filter.hzn --design Hairpin --set L1=11.2mm --set gap=0.25mm \
    --freq 1.5GHz:3.5GHz:401 -o out/hairpin.s2p --progress json --json

# Touchstone to stdout (for a pipe), nothing else on stdout
hertzon-cli solve Microstrip.hzn --design Microstrip50 -o - --progress none > line.s2p
```

## `describe`

Prints one JSON object on stdout:

```json
{
 "cli_schema": 1,
 "hertzon": "0.3.17",
 "project": "/path/Microstrip.hzn",
 "file_format": {"format": "hertzon-project", "version": 2},
 "designs": [
  {
   "name": "Microstrip50", "kind": "3d", "units": "mm",
   "ports": [
    {"number": 1, "name": "1", "kind": "Lumped Port", "modes": 1, "z0_ohm": 50.0},
    {"number": 2, "name": "2", "kind": "Lumped Port", "modes": 1, "z0_ohm": 50.0}
   ],
   "variables": [
    {"name": "w", "expression": "3mm", "scope": "design", "value": 0.003, "unit": "m"}
   ],
   "setups": [
    {"name": "Setup1", "enabled": true,
     "adaptive": {"mode": "Single", "frequency_hz": 4e9}, "max_delta_s": 0.02, "max_passes": 20,
     "sweeps": [{"name": "Sweep", "enabled": true, "type": "Interpolating", "distribution": "Linear Count",
                 "start_hz": 1e9, "stop_hz": 6e9, "points": 101}]}
   ]
  }
 ]
}
```

* `kind` is `3d` (full-wave FEM with adaptive meshing) or `circuit` (Hertzon's linear schematic).
* `ports[].z0_ohm` is the reference impedance of the port. `null` for a wave port without renormalization:
  its S-parameters are normalized to the impedance of the port mode.
* `variables[].value` is the SI value (`unit` `m`, `Hz`, `m^2`, ... or `""` for a plain number); project
  variables have `scope: "project"` and a name starting with `$`.
* Without `--design` all designs are listed.

## `solve`

* **Design**: `--design`, by default the only design (or the only one with a setup).
* **Setup and sweep**: `--setup` (default: the first enabled one) and `--sweep` (default: its first enabled
  sweep; `LastAdaptive` for the S-parameters at the adaptive frequency). Only the chosen sweep is solved.
* **Variables**: `--set VAR=VALUE` for design variables and project variables (`--set '$eps=4.4'`); a value
  without unit has the unit of the design (`--set w=1.2` is 1.2 mm in a design in mm). An unknown variable
  is an error, nothing is solved.
* **Frequencies**: `--freq START:STOP:COUNT` (linear) or `--points F1,F2,...` replace the frequencies of the
  sweep for this run (a setup without sweep gets one). `--sweep-type` changes the sweep method.
* **Output**: `-o FILE` writes that file, `-o FOLDER` writes `<design>_<setup>_<solution>.sNp` into it, `-o -`
  writes the Touchstone to stdout. The file is the same as **Results > Export Touchstone** in the window
  (Touchstone 1.0, `# HZ S RI R <z0>`).
* **Reuse**: 3D runs are solved on a copy in `<project>.hznresults/<design>/CLI/<id>/`. A call is answered
  from an existing solution, without solving, when the model and setup are unchanged: the solution of the
  window (`<project>.hznresults/<design>/<setup>/`) or that of an earlier identical call. `--force` solves
  again. Field solutions are not kept unless `--fields` is given.
* `--validate` runs the model check of the window first (builds the geometry); an error ends with exit code 4.
* `--require-convergence` ends with exit code 5 when adaptive meshing did not converge (the Touchstone file
  is still written).

### Progress (stderr)

`--progress text` (default) prints lines like `[ 42.0%] Adaptive pass 3`, warnings and errors.
`--progress json` prints one JSON object per line:

```json
{"event": "progress", "percent": 42.0, "message": "Adaptive pass 3"}
{"event": "pass", "pass": 3, "tets": 18234, "delta_s": 0.031}
{"event": "log", "level": "warning", "message": "..."}
{"event": "done", "touchstone": "/path/out.s2p", "seconds": 81.2, "cached": false}
{"event": "error", "code": 3, "message": "No variable 'x' in design 'Hairpin'; variables: [...]"}
```

`--progress none` prints only errors. The full solver output is in `solver.log` in the result folder.

### Summary (stdout, `--json`)

```json
{"cli_schema": 1, "hertzon": "0.3.17", "project": "...", "design": "Microstrip50", "setup": "Setup1",
 "solution": "Sweep", "variables": {"w": "2.9mm"}, "cached": false, "results": "...",
 "converged": true, "passes": 6, "tetrahedra": 21544,
 "ports": [{"row": 1, "number": 1, "name": "1", "kind": "Lumped Port", "mode": 1}, ...],
 "frequencies": {"count": 101, "start_hz": 1e9, "stop_hz": 6e9},
 "reference_impedance_ohm": 50.0, "touchstone": "/path/out.s2p", "seconds": 81.2}
```

`converged` is `null` for circuits and for solutions without adaptive meshing.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | done, Touchstone written |
| 1 | the solver failed (meshing, solve); see `solver.log` |
| 2 | wrong command line |
| 3 | bad input: project not found or not readable, unknown design, setup, sweep or variable, bad value |
| 4 | the model check failed (`--validate`) |
| 5 | adaptive meshing did not converge (`--require-convergence`) |
| 130 | interrupted (Ctrl+C) |

The codes, the JSON keys and the progress events are stable within `cli_schema` 1; new keys may be added.
