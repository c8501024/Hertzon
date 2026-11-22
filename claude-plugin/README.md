# Hertzon plugin for Claude Code

[Hertzon](https://github.com/c8501024/hertzon) is an open electromagnetic design suite (3D full-wave FEM with
adaptive meshing, circuits, PCBs). Its MCP server lets an AI assistant build and solve models. This plugin adds
the know-how on top: what Hertzon offers, how to read its results and what to check before trusting them. The
approach stays the assistant's own; the skills set no fixed order of steps.

> 🧪 **Experimental.** The plugin and its skills are still experimental: how well a design turns out depends on
> the assistant, its model and the task, and an assistant can still miss a goal or stop early. Research into
> better results is ongoing, and the skills change with it.

| Part | Name | What it does |
|---|---|---|
| Skill | `hf-design-loop` | What Hertzon offers for a design: design assistants and tuning for simple structures, broad batched search for demanding ones, systems of many parts assembled on the board (`board`) with a serial and parallel cloud estimate (`cloud`), coarse setups, convergence reported to the user |
| Skill | `circuit-first` | Multi-fidelity for planar circuits: design and optimize in the circuit (Design Wizard or a schematic of your own), then check and correct in full wave with a few 3D solves; systems of several parts; active circuits |
| Skill | `antenna-design` | Open region, feeds, adaptive frequencies for several bands, far-field efficiency, typical tuning knobs |
| Skill | `results-check` | Mesh convergence, ports, power balance, efficiency split, counter tests, confirmed cause versus hypothesis |
| Command | `/hertzon:design <spec>` | Designs a structure from a specification, verified and with a report |
| Command | `/hertzon:verify` | Checks the open design without changing it |
| Pane | Hertzon | Every picture Hertzon draws for the assistant (`show_result`, `render_image`): reports, field plots, 3D view, convergence, as shown in Hertzon; `/hertzon-results` opens it |
| Status | above the prompt | Adaptive pass, tetrahedra and Delta S of a running analysis |

## Install

1. Install Hertzon and register its MCP server: **Tools > AI Assistants (MCP)... > Register** next to Claude Code
   (or `python -m hertzon.mcp --install claude`). This also installs the plugin from your Hertzon folder (it loads
   in place, so it matches your version and is updated with Hertzon), when the `claude` command is on the computer.
   The plugin does not start the server itself, because it runs in the Python of your Hertzon installation.
2. Only without the `claude` command, in Claude Code:

   ```
   /plugin install hertzon --marketplace c8501024/hertzon
   ```

3. Start a new session and ask, for example, `/hertzon:design 2.45 GHz patch antenna on 1.6 mm FR4, S11 below -15 dB`.

Installed from GitHub (step 2), the plugin follows the repository: `claude plugin update hertzon@hertzon` takes
the latest version. Simulation results can be wrong, whoever sets the model up; the engineer stays responsible
for checking them.
