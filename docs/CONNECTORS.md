# Edge-launch connectors

Hertzon ships a library of 131 PCB edge-launch (end-launch) connectors for SMA, 2.92 mm and 2.4 mm: Southwest
Microwave, Cinch (Johnson), Amphenol SV Microwave, Amphenol RF, HUBER+SUHNER, Rosenberger, Samtec and Molex, with
interface, maximum frequency, mounting (clamp block or solder legs), board thickness range, launch pin and links to
data sheets and 3D files (`hertzon/model/data/edge_launch_connectors.json`). "2.5 mm" connectors do not exist as
a standard; 2.4 mm (50 GHz) is the interface between 2.92 mm and 1.85 mm.

A connector does not replace your board. It stands at a **lumped port at a board edge** and brings its launch
footprint along. You choose how much of it is solved, and you can switch at any time without losing anything.

## Simulate with Real Connector

Select a lumped port at a board edge (in the project tree, or its sheet in 3D), then **Connectors › Real
Connector** (or right-click the port › *Simulate with Real Connector*). The port sheet, from the ground to the
trace, tells Hertzon everything it needs:

- the board edge and the direction the connector points to (away from the substrate that ends at the sheet),
- the board thickness (the integration line) and the trace width (the trace that ends at the sheet),
- the permittivity of the substrate.

The dialog lists the parts of the chosen interface, those made for the board thickness first, with data sheet
and 3D model links. The connector gets a name (J1, J2 ...); its objects form a group of that name and move with
the variables `J1_x`, `J1_y`, `J1_z`.

## Three ways to simulate it

| Mode | 3D model | What you get |
|---|---|---|
| **Ideal port** | the lumped port is solved; connector and footprint are drawn transparent, outside the model | exactly the results without a connector |
| **Transition** | as Ideal | a circuit design `<Design>_Connectors` that cascades your lumped-port solution with a solved 3D model of the connector-to-board transition |
| **Full 3D** | connector and footprint are solved with the board, the wave port sits on the coax at the mating plane | the most faithful model, and the most expensive |

**Connectors › Simulate As** switches the selected connectors (or all of them). The project tree shows them
under *Connectors* with their port and mode; right-click a connector for its mode, the transition design, the
launch optimization, Flatten and Remove.

Switching is lossless. Nothing is deleted or rebuilt; the mode only switches parts on and off:

- the *Model* flag and transparency of the connector and footprint objects,
- the cut of the footprint out of the board conductors (a Subtract in the conductor's history, suppressed
  outside Full 3D),
- the lumped port or the connector's wave port (the wave port has the lumped port's number and is renormalized
  to its impedance, so the S-parameters of all modes compare directly),
- the footprint's conductor boundary,
- the air region: a wave port has to lie on the outer boundary, so in Full 3D a region object gets no padding
  toward the connector and its open boundary leaves that face, which becomes Perfect E like the connector body.
  With **Auto Open**, an explicit region with the same padding and a Radiation boundary takes its place while a
  connector is in Full 3D. Everything that was changed is restored when the last connector leaves Full 3D.

**Flatten** makes a connector plain geometry: it is solved in 3D from then on, and the lumped port and its
sheet are deleted. This is the only one-way step (Undo still works).

## The footprint

The connector brings a grounded coplanar launch after the design rules of Southwest Microwave's application
notes ("Optimizing Test Boards for 50 GHz End Launch Connectors", 2007; "The Design and Test of Broadband
Launches up to 50 GHz on Thin and Thick Substrates", 2011):

- **Narrow at the launch, not wide.** The contact adds capacitance. The traditional rule (trace as wide as the
  pin, tapered over the length of the pin) over-compensates. Instead the line is narrowed at the edge, which adds
  series inductance: the pad is about 0.73 of the trace width (their optimized 30 mil board: 0.033 in at the
  edge for a 0.045 in trace), at least 1.5 pin diameters, and tapers to the trace over the length the contact
  rests on it. A solder tab gets a pad of its width plus 0.1 mm over its length.
- **Coplanar ground at the edge.** The ground-to-ground width at the edge is about the diameter of the
  connector's launch dielectric (clamp-block connectors); otherwise the gap of a 50 ohm grounded coplanar line.
- **Via fence.** The edge-to-edge gap between vias keeps the quarter-wave resonance
  `f = c / (4 · gap · √εr)` above 1.25 · f_max; vias sit 0.15 mm from the gap, the first one close to the edge.
- Conductor sheets of the board in the board plane that reach into the footprint get it cut out; pad, taper and
  coplanar grounds conduct like the trace (same boundary type).

Pad width, pad length, gap and taper length are design variables (`J1_wp`, `J1_lp`, `J1_gl`, `J1_lt`). Plain
microstrip without a top ground radiates at the edge above about 30 GHz on a 0.76 mm board (the papers: 12 dB
against 6 dB total loss on a 1 inch board at 50 GHz), which is why the footprint is coplanar.

## Transition model and cascade

In mode Transition Hertzon makes a small 3D design `Transition_<part>_<board>`: the connector with its footprint
on a piece of the board (substrate, trace, the same conductor boundary), a wave port on the coax (port 1, *Coax*)
and one on the line (port 2, *Board*), Radiation on the top and sides. Connectors with the same part, board,
trace and footprint share one transition design. It carries a signature of what it was made from; when the board,
the footprint or the frequency range changes, it is marked *stale* (orange in the tree) and setting the mode
again rebuilds it.

The circuit design `<Design>_Connectors` holds a 3D design block of your design (its lumped-port solution), and
at every port with a Transition connector:

    Power Port ── transition block (Coax | Board) ── microstrip line of length −Lb ── your design's port

The microstrip line of negative length removes the piece of line between the board edge and port 2 of the
transition design, so the transition is referred to the board edge, where the lumped port sits. The trace of
your design from the edge on (which the footprint replaces in Full 3D) is cancelled by the same line: the
cascade is connector + footprint + the rest of your board. The line is the closed-form microstrip model
(Hammerstad-Jensen with Kirschning-Jansen dispersion and loss), valid at every frequency of the analysis.

Order: analyze the transition design(s) once, then your design, then the circuit design. Hertzon lists what is
missing (for example a solution of your design made with other ports, after it was solved in Full 3D).

The lumped port at the edge and the transition referred to the same plane are not exactly the same thing (fringing
at the edge, the reference plane of a lumped port), so Transition is a fast estimate between Ideal and Full 3D.

## Optimizing the launch

**Connectors › Optimize Launch** adds an optimization of pad width, pad length, gap and taper length to the
transition design, with the goal `dB(S(1,1)) <= -20` from 0.5 GHz to the top of the sweep, and shows that design.
Run it there: the transition model is small, so this takes minutes instead of solving the whole board for every
step. **Apply Optimized Footprint** copies the result into the connector (`J1_wp` ...), and the transition design
stays current.

## Accuracy of the connector model

The interface dimensions (outer and centre conductor, dielectric) are those of IEEE 287 / MIL-STD-348 /
IEC 61169. The support bead has the necked centre conductor that keeps it at 50 ohm (2.92 mm: Ultem, 0.665 mm
neck; 2.4 mm: 0.546 mm). Body, clamp block and bead positions are typical values, because the vendors publish them
only as drawings; only the faces that touch the field matter. The launch pin and launch dielectric of clamp
connectors come from the data sheets (Southwest Microwave: 0.010/0.0635 in, 0.007/0.039 in, 0.005/0.029 in).
Exact vendor models (STEP, encrypted HFSS models) can be imported as geometry instead.

## Scripting and MCP

```python
from hertzon.model import connectors as C
inst = C.attach_connector(project, design, "P1", "southwest-1092-03a-12")   # mode Ideal
C.set_mode(project, design, "3D", ["J1"])         # or "Transition", "Ideal"; names None: all
C.flatten(project, design, "J1")
```

MCP tools: `list_edge_connectors`, `attach_edge_connector`, `set_connector_mode` (also flatten, remove,
optimize_launch, apply_footprint) and `get_connectors`.
