# Hertzon plugin for Claude Code

[Hertzon](https://github.com/c8501024/hertzon) is an open electromagnetic design suite (3D full-wave FEM with
adaptive meshing, circuits, PCBs). Its MCP server lets an AI assistant build and solve models. This plugin adds
the know-how on top: in which order to work and what to check before trusting a result.

| Part | Name | What it does |
|---|---|---|
| Skill | `hf-design-loop` | Design workflow: goal in a design log, start from a Design Wizard or example, baseline, one idea at a time with sweeps, derivative tuning and optimizers, verification, report with time per step |
| Skill | `antenna-design` | Open region, feeds, adaptive frequencies for several bands, far-field efficiency, typical tuning knobs |
| Skill | `results-check` | Mesh convergence, ports, power balance, efficiency split, counter tests, confirmed cause versus hypothesis |
| Command | `/hertzon:design <spec>` | Runs the workflow for a specification |
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
