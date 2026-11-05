# PCB import (Gerber, Excellon)

**File > Import PCB (Gerber, Excellon)...** (also in the Modeler menu and as *Import PCB* in the ribbon)
turns the manufacturing files of a board into a simulation model: the stackup with its `stk_` variables,
the substrate in the board shape, the copper of every layer, the vias and the holes.

The rule of the import: **what is loaded is simulated**. Solder mask, legend (silkscreen), paste and files
that are not matched to a copper layer are not loaded, and the import report names each of them. Nothing
is folded silently into other values.

## Files

Add single files, a folder or a zip archive. Read are:

| File | What is read |
|---|---|
| Gerber X2 and RS-274X | apertures C, R, O, P (with holes), aperture macros (primitives 1, 2/20, 21, 22, 4, 5, 6, 7, variables and arithmetic), block apertures (AB), flashes, lines, arcs (G75 and single quadrant G74), regions with several contours and cut-ins, clear polarity, LM/LR/LS, step and repeat; deprecated: G70/G71, G90/G91, G54/G55, MI/OF/SF, trailing zero omission, coordinates without an operation code |
| Excellon | METRIC/INCH, LZ/TZ, `FILE_FORMAT`, tool table, hits, G85 slots, routed slots (G00/M15/G01/M16), plated/non-plated per tool (KiCad `;TYPE=`), the layer span of the X2 comment (`Plated,1,2,Blind`) |
| Gerber job file (.gbrjob) | stackup (thicknesses, εr, tan δ, materials), board thickness, which file is which layer |

What each file is comes from, in this order: its X2 attribute (`%TF.FileFunction%`), the job file, the usual
file names (KiCad `-F_Cu`, `-In1_Cu`, `-B_Cu`, `-Edge_Cuts`; Altium/Protel `.GTL`, `.GBL`, `.G1`, `.GKO`;
Eagle `.cmp`, `.sol`). The column *Found by* says which; every entry can be changed. One file per copper
layer: giving a layer to a file takes it from the file that had it.

## Stackup

The stackup comes from the job file; without one the design's stackup is used when it has the same number
of copper layers, else a usual stack of 1.6 mm is assumed (and marked as assumed). *Edit stackup...* opens
the stackup editor on it before the import. Each copper layer is made as the stackup's *Model* column
says: a sheet with a *Finite Conductivity* boundary (thickness `stk_<layer>_t`, thin conductor) or solid
copper of the layer thickness.

## Geometry

* **Board shape**: the outline (Profile / Edge.Cuts) with its cut-outs, the centre of the drawn line is the
  board edge. Without an outline file, the extent of the copper. The substrate is one slab per dielectric
  layer in this shape (and a fill slab over each inner copper layer), group *Stackup*.
* **Origin**: the lower left corner of the board (or of the import area) becomes the board origin, or the
  origin of the files (option). The report gives the file point used.
* **Copper**: one covered polyline per copper island, its holes in the *Holes* property, on the coordinate
  system `stk_<layer>` of its layer (group *PCB &lt;layer&gt;*). Arcs and circles are made of straight
  segments (*Segments per circle*, 48 by default).
* **Plated holes**: copper cylinders of the finished hole size between the copper layers they connect,
  filled (group *PCB Vias*); plated slots as swept stadiums. They are cut out of the substrate slabs.
* **Non-plated holes**: cut out of the substrate (the hole objects in *PCB Holes* are not in the model).
* **Import area**: Shift + drag in the preview, or the four values. Copper is cut at its edges, holes that
  are not fully inside are left out (and counted in the report).

Importing again replaces the objects of the earlier PCB import. Changing the stackup later moves and
resizes everything through the `stk_` variables.

## Not yet

KiCad `.kicad_pcb` directly (with nets), net filters, true arcs in the model (now segments).
