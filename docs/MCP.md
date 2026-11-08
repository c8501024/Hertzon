# What is MCP? Co-design with AI

Hertzon's vision is **co-design**: every design is made by an engineer and an AI agent together. Hertzon
provides its methods (modelling, adaptive solving, sweeps, optimizers, tuning, circuits, PCB import) as
tools that an AI agent can call through MCP. The AI picks and combines them, and the engineer sets the goal,
watches every step in the open window and makes the decisions.

## What MCP is

**MCP, the Model Context Protocol,** is an open standard for connecting AI assistants to software. A
program that speaks MCP (an *MCP server*) describes what it can do as a list of **tools**: each tool has a
name, a short explanation and its parameters, for example "create a box with these dimensions" or "run
this analysis and return S11". An AI assistant that speaks MCP (an *MCP client*, such as Claude Code, the
Claude desktop app, Codex or VS Code in agent mode) reads this list and can then call the tools on its own
while it works on your request.

You can think of MCP as the USB port for AI assistants: one plug, and any assistant can use any program
that offers it, without custom code on either side.

## What MCP does in Hertzon

Hertzon ships an MCP server. Once it is registered with your assistant
([set it up once](TUTORIAL.md#51-set-it-up-once)), you can write a request in plain language, for example
*"build a 2.45 GHz patch antenna on FR4 with a lumped port, solve it and give me S11 and the gain"*, and the
assistant does the steps itself:

1. it creates the project, the variables and the geometry,
2. assigns materials, boundaries and ports,
3. defines the analysis setup with adaptive meshing and the frequency sweep,
4. runs the solver and follows the adaptive passes,
5. reads S-parameters, fields, gain or RCS, and decides what to try next.

With the live link (**Tools > AI Assistants (MCP)... > Let AI assistants work in this window**) every step
appears in the open Hertzon window at once. You see what the AI does, you can change things yourself in
between, and **Ctrl+Z** undoes any of its changes. The full list of tools is in the
[tutorial](TUTORIAL.md#53-what-the-assistant-can-do).

## Methods as tools: why this matters

An AI agent is good at understanding a goal, planning and combining steps. It is not a field solver. In
Hertzon the AI does not guess physics: it uses the same validated methods an engineer uses, and the
numbers come from the FEM solver. The more powerful the methods Hertzon offers, the less the AI has to
iterate by hand, and the fewer tokens and the smaller the models it needs:

- **Solving**: 3D full-wave FEM with adaptive meshing, fast and interpolating sweeps, solving on remote and
  cloud machines.
- **Design assistants**: the patch antenna and hairpin filter assistants turn a specification into a model.
- **Optimizers**: three optimizers for design variables, among them a derivative trust region on the
  adaptive mesh, so the AI states the goal and Hertzon does the iterations in one call.
- **Derivative tuning**: sensitivities from mesh morphing tell how S-parameters change with each variable
  without solving again.
- **Circuits and PCBs**: schematics with lumped parts and lines, their 3D layout, 3D designs as blocks, and
  boards imported from Gerber files with their stackup.
- **Next: topology optimization.** From the author's research on pixelated antennas
  ([Mair & Baumgarten, Sci. Rep. 14, 26664, 2024](https://doi.org/10.1038/s41598-024-77695-x)): instead of
  tuning a few dimensions, the shape of the metal itself is optimized. As an MCP tool it lets an AI agent
  explore designs that no parametric model contains.

## Engineer and AI together

Co-design does not mean handing the design over. The engineer brings the goal, the judgement and the
responsibility; the AI brings speed, patience and the ability to run and compare many variants. Hertzon
keeps the engineer in control:

- everything the AI does happens in the open project and can be inspected, changed and undone,
- results are the solver's results, shown with the same plots and reports as for any manual run,
- every model the AI builds is an ordinary Hertzon project that you can open, edit and share.

Real runs are on the [website](https://c8501024.github.io/Hertzon/#ai), for example a dual-band antenna for
AI glasses designed by an AI agent through Hertzon's MCP server in 2 h 25 min
([examples/meta_glasses_ex1](../examples/meta_glasses_ex1/README.md)).
