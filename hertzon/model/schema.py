"""Property schemas.

Every editable item (geometry command, boundary, excitation, mesh operation,
setup, sweep ...) is a ``kind`` plus a dict of string properties. The schemas
below drive the property grids and dialogs, so adding a property here makes it
editable everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Prop:
    name: str
    type: str                 # length point vector angle freq expr int float bool choice material text points objects color
    default: str = ""
    label: str = ""
    choices: tuple[str, ...] = ()
    tab: str = "General"
    help: str = ""
    visible_if: tuple[str, str] | None = None   # (prop, value) or (prop, "!value")
    enabled_if: tuple[str, str] | None = None   # greyed out (bool shown unchecked) unless prop == value

    @property
    def title(self) -> str:
        return self.label or self.name


AXES = ("X", "Y", "Z")
# matrix solvers of a setup; the SPARTA ones need the optional sparta-solver package (solver/sparta_setup.py)
SOLVERS = ("Auto", "PARDISO", "SPARTA (GPU)", "SPARTA (CPU)", "MUMPS", "UMFPACK", "SuperLU")


def P(name, type_, default="", label="", choices=(), tab="General", help="", visible_if=None, enabled_if=None):
    return Prop(name, type_, str(default), label, tuple(choices), tab, help, visible_if, enabled_if)


# Rotate / Sweep Around Axis: the axis and the centre are given in this coordinate system
AXIS_CS = P("CoordinateSystem", "choice", "Global", label="Coordinate system", choices=("Global",),
            help="Axis and Center are given in this coordinate system")


# --------------------------------------------------------------------------------------------
# Geometry commands
# --------------------------------------------------------------------------------------------
GEOMETRY: dict[str, list[Prop]] = {
    "CreateBox": [P("Position", "point", "0,0,0"), P("XSize", "length", "1"), P("YSize", "length", "1"),
                  P("ZSize", "length", "1")],
    "CreateCylinder": [P("CenterPosition", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES),
                       P("Radius", "length", "1"), P("Height", "length", "1"),
                       P("NumberOfSegments", "int", "0", help="0 = true surface")],
    "CreateSphere": [P("CenterPosition", "point", "0,0,0"), P("Radius", "length", "1"),
                     P("NumberOfVertices", "int", "0", label="Number of vertices",
                       help="0 = true surface; N >= 4: faceted polyhedron with N evenly spread corners and "
                            "no poles (4 = tetrahedron, 6 = octahedron, 12 = icosahedron)")],
    "CreateCone": [P("CenterPosition", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES),
                   P("LowerRadius", "length", "1"), P("UpperRadius", "length", "0"), P("Height", "length", "1"),
                   P("NumberOfSegments", "int", "0", help="0 = true surface; n >= 3: faceted with n flat sides")],
    "CreateTorus": [P("CenterPosition", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES),
                    P("MajorRadius", "length", "2"), P("MinorRadius", "length", "0.5")],
    "CreateRegularPolyhedron": [P("CenterPosition", "point", "0,0,0"), P("StartPosition", "point", "1,0,0"),
                                P("Height", "length", "1"), P("NumberOfSides", "int", "6"),
                                P("Axis", "choice", "Z", choices=AXES)],
    "CreateRectangle": [P("Position", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES,
                                                             help="Normal of the sheet"),
                        # only the two sizes in the sheet plane are shown (XSize/YSize for a Z normal)
                        P("XSize", "length", "1", visible_if=("Axis", "!X")),
                        P("YSize", "length", "1", visible_if=("Axis", "!Y")),
                        P("ZSize", "length", "1", visible_if=("Axis", "!Z"))],
    "CreateCircle": [P("CenterPosition", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES),
                     P("Radius", "length", "1"), P("NumberOfSegments", "int", "0")],
    "CreateEllipse": [P("CenterPosition", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES),
                      P("MajorRadius", "length", "2"), P("Ratio", "expr", "0.5")],
    "CreateRegularPolygon": [P("CenterPosition", "point", "0,0,0"), P("StartPosition", "point", "1,0,0"),
                             P("NumberOfSides", "int", "6"), P("Axis", "choice", "Z", choices=AXES)],
    "CreatePolyline": [P("Points", "points", "0,0,0;1,0,0"),
                       P("SegmentTypes", "text", "", label="Segment types",
                         help="Comma separated: Line, Arc (3 point arc), Spline:n, AngularArc:angle:plane "
                              "(center point arc); empty = all lines"),
                       P("Closed", "bool", "False"),
                       P("Covered", "bool", "False", help="Closed and covered polylines become sheets")],
    "CreateHelix": [P("CenterPosition", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES),
                    P("Radius", "length", "1"), P("Pitch", "length", "0.5"), P("Turns", "expr", "3"),
                    P("RightHanded", "bool", "True", label="Right handed"),
                    P("WireRadius", "length", "0", label="Wire radius (0 = line)"),
                    P("NumberOfSegmentsPerTurn", "int", "0", label="Segments per turn",
                      help="0 = true (smooth) helix, N >= 3 = polyline with N straight segments per turn")],
    "CreateSpiral": [P("CenterPosition", "point", "0,0,0"), P("Axis", "choice", "Z", choices=AXES),
                     P("InnerRadius", "length", "0.5"), P("Pitch", "length", "0.5", label="Radial pitch per turn"),
                     P("Turns", "expr", "3"), P("RightHanded", "bool", "True", label="Right handed"),
                     P("Width", "length", "0", label="Trace width (0 = line)")],
    "CreateBondwire": [P("Type", "choice", "JEDEC 4-Point", choices=("JEDEC 4-Point", "JEDEC 5-Point")),
                       P("StartPosition", "point", "0,0,0"), P("EndPosition", "point", "2,0,0"),
                       P("H1", "length", "0.2", label="Height above start (h1)"),
                       P("H2", "length", "0", label="Height above end (h2)"),
                       P("Diameter", "length", "0.025")],
    "CreateRegion": [P("PaddingType", "choice", "Percentage Offset", choices=("Percentage Offset", "Absolute Offset")),
                     P("+X", "expr", "20"), P("-X", "expr", "20"), P("+Y", "expr", "20"),
                     P("-Y", "expr", "20"), P("+Z", "expr", "20"), P("-Z", "expr", "20")],
    "Import": [P("File", "text", ""), P("Body", "int", "0")],
    # operations on an existing object
    "Move": [P("Vector", "vector", "0,0,0")],
    "Rotate": [P("Axis", "choice", "Z", choices=AXES), P("Angle", "angle", "90deg"),
               P("Center", "point", "0,0,0"), AXIS_CS],
    "Mirror": [P("Base", "point", "0,0,0"), P("Normal", "vector", "1,0,0")],
    "Scale": [P("Factor", "expr", "1"), P("Center", "point", "0,0,0")],
    "ThickenSheet": [P("Thickness", "length", "1"), P("BothSides", "bool", "False"),
                     P("ReverseDirection", "bool", "False", label="Reverse direction",
                       visible_if=("BothSides", "False"))],
    "SweepAlongVector": [P("Vector", "vector", "0,0,1")],
    "SweepAroundAxis": [P("Axis", "choice", "Z", choices=AXES), P("Angle", "angle", "360deg"),
                        P("Center", "point", "0,0,0"), AXIS_CS],
    "Cover": [],
    "Unite": [P("Tools", "objects", ""), P("KeepOriginals", "bool", "False")],
    "Subtract": [P("Tools", "objects", ""), P("KeepOriginals", "bool", "False")],
    "Intersect": [P("Tools", "objects", ""), P("KeepOriginals", "bool", "False")],
    "Split": [P("Plane", "choice", "XY", choices=("XY", "YZ", "XZ", "Custom")),
              P("Base", "point", "0,0,0", visible_if=("Plane", "Custom")),
              P("Normal", "vector", "0,0,1", visible_if=("Plane", "Custom")),
              P("Keep", "choice", "Positive", choices=("Positive", "Negative")),
              P("CoordinateSystem", "choice", "Global", label="Coordinate system", choices=("Global",),
                help="The plane, Base and Normal are given in this coordinate system")],
    "SweepAlongPath": [P("Tools", "objects", "", label="Path"),
                       P("Trihedron", "choice", "DiscreteTrihedron",
                         choices=("DiscreteTrihedron", "CorrectedFrenet", "Fixed", "Frenet"))],
    "Connect": [P("Tools", "objects", ""), P("Ruled", "bool", "False")],
    "Imprint": [P("Tools", "objects", "")],
    "Chamfer": [P("Distance", "length", "0.1"), P("Edges", "text", "", help="Edge indices, comma separated"),
                P("Vertices", "text", "", help="Vertex indices of a sheet, comma separated: a chamfer at each")],
    "UncoverFaces": [P("Faces", "text", "", help="Face indices, comma separated")],
    "KeepFaces": [P("Faces", "text", "", help="Face indices, comma separated")],
    "SweepFacesAlongNormal": [P("Faces", "text", ""), P("Distance", "length", "1")],
    "KeepBody": [P("Body", "int", "0")],
    "Section": [P("Base", "point", "0,0,0"), P("Normal", "vector", "0,0,1")],
    "Fillet": [P("Radius", "length", "0.1"), P("Edges", "text", "", help="Edge indices, comma separated"),
               P("Vertices", "text", "", help="Vertex indices of a sheet, comma separated: a fillet at each")],
}

CREATE_COMMANDS = [k for k in GEOMETRY if k.startswith("Create") or k == "Import"]
SHEET_COMMANDS = {"CreateRectangle", "CreateCircle", "CreateEllipse", "CreateRegularPolygon"}

OBJECT_ATTRIBUTES: list[Prop] = [
    P("Name", "text"), P("Material", "material", "vacuum"), P("SolveInside", "bool", "True", enabled_if=("Model", "True"),
                                                    help="Non Model objects are not solved"),
    P("Model", "bool", "True"), P("Color", "color", "#8f8f8f"), P("Transparency", "float", "0"),
    P("DisplayWireframe", "bool", "False"), P("Orientation", "choice", "Global", choices=("Global",)),
]

# --------------------------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------------------------
BOUNDARIES: dict[str, list[Prop]] = {
    "Perfect E": [P("InfGroundPlane", "bool", "False", label="Infinite ground plane")],
    "Perfect H": [],
    "Finite Conductivity": [
        P("UseMaterial", "bool", "True"), P("Material", "material", "copper", visible_if=("UseMaterial", "True")),
        P("Conductivity", "expr", "58000000", label="Conductivity (S/m)", visible_if=("UseMaterial", "False")),
        P("Permeability", "expr", "1", visible_if=("UseMaterial", "False")),
        P("Roughness", "length", "0um", label="Surface roughness (RMS)"),
        P("UseThickness", "bool", "False", label="Use thickness"),
        P("Thickness", "length", "35um", visible_if=("UseThickness", "True")),
        P("TwoSided", "bool", "False", label="Thin conductor (two-sided)", visible_if=("UseThickness", "True")),
    ],
    "Impedance": [P("Resistance", "expr", "50", label="Resistance (ohm/sq)"),
                  P("Reactance", "expr", "0", label="Reactance (ohm/sq)")],
    "Lumped RLC": [P("Model", "choice", "Parallel RLC", choices=("Parallel RLC", "Series RLC", "Touchstone"),
                     help="Parallel RLC: R, L and C in parallel. Series RLC: R, L and C in series (a chip "
                          "component with its ESR and ESL). Touchstone: the measured impedance of a component "
                          "from a .s1p/.s2p file"),
                   P("UseResistance", "bool", "True", visible_if=("Model", "!Touchstone")),
                   P("Resistance", "expr", "50ohm", visible_if=("Model", "!Touchstone")),
                   P("UseInductance", "bool", "False", visible_if=("Model", "!Touchstone")),
                   P("Inductance", "expr", "1nH", visible_if=("Model", "!Touchstone")),
                   P("UseCapacitance", "bool", "False", visible_if=("Model", "!Touchstone")),
                   P("Capacitance", "expr", "1pF", visible_if=("Model", "!Touchstone")),
                   P("TouchstoneFile", "text", "", label="Touchstone file", visible_if=("Model", "Touchstone"),
                     help="Path of the .s1p/.s2p file (absolute or relative to the project file)"),
                   P("TouchstoneMount", "choice", "Series", choices=("Series", "Shunt"), label="2-port measured as",
                     visible_if=("Model", "Touchstone"),
                     help="How the component sat in the 2-port test fixture: in series between the ports (usual "
                          "for chip inductors and capacitors) or as a shunt to ground. Ignored for .s1p files"),
                   P("Component", "text", "", help="Package or part number of a component inserted from the "
                                                    "Lumped Elements tab (information only)"),
                   P("IntegrationLine", "points", "", help="Start;End point of the current flow line, from one conductor to the other. Set "
                     "automatically between the two conductors touching the sheet; right click the boundary to "
                     "draw it in the 3D view or to place it automatically again. Given in the coordinates of the sheet as created: "
                     "the line moves, rotates and mirrors with its object")],
    "Radiation": [P("Order", "choice", "First order", choices=("First order", "Second order")),
                  P("FarFieldReference", "bool", "True", label="Include for near/far field calculation")],
    "PML": [P("Thickness", "length", "", help="Empty = lambda/4 at lowest frequency"),
            P("Layers", "int", "5", label="Mesh layers"), P("Exponent", "expr", "2"),
            P("MaxAttenuation", "expr", "8", label="Max. attenuation coefficient")],
    "Symmetry": [P("Type", "choice", "Perfect E", choices=("Perfect E", "Perfect H")),
                 P("ImpedanceMultiplier", "expr", "1")],
    "Primary": [P("UVector", "points", "", label="U vector (origin;point)")],
    "Secondary": [P("Primary", "text", ""), P("UVector", "points", ""),
                  P("PhaseMode", "choice", "Scan Angles", label="Phase delay",
                    choices=("Scan Angles", "Input Phase Delay")),
                  P("ScanTheta", "angle", "0deg", label="Scan angle theta", visible_if=("PhaseMode", "Scan Angles")),
                  P("ScanPhi", "angle", "0deg", label="Scan angle phi", visible_if=("PhaseMode", "Scan Angles")),
                  P("PhaseDelay", "angle", "0deg", visible_if=("PhaseMode", "Input Phase Delay"))],
    "Layered Impedance": [],
}
UNSUPPORTED_BOUNDARIES = {"Layered Impedance": "EMerge has no layered impedance boundary yet."}
BOUNDARY_COLORS = {
    "Perfect E": "#d4a017", "Perfect H": "#2a9df4", "Finite Conductivity": "#e07b39", "Impedance": "#9b59b6",
    "Lumped RLC": "#16a085", "Radiation": "#27ae60", "PML": "#2c3e50", "Symmetry": "#7f8c8d",
    "Primary": "#c0392b", "Secondary": "#8e44ad", "Layered Impedance": "#95a5a6",
}

# --------------------------------------------------------------------------------------------
# Excitations
# --------------------------------------------------------------------------------------------
EXCITATIONS: dict[str, list[Prop]] = {
    "Wave Port": [P("PortNumber", "int", "1"), P("NumberOfModes", "int", "1"),
                  P("ModeType", "choice", "Auto", choices=("Auto", "TEM", "TE", "TM")),
                  P("ImpedanceDefinition", "choice", "Zpv", choices=("Zpv", "Zpi", "Zvi")),
                  P("Renormalize", "bool", "False", tab="Post Processing"),
                  P("RenormImpedance", "expr", "50ohm", tab="Post Processing", visible_if=("Renormalize", "True")),
                  P("Deembed", "bool", "False", tab="Post Processing"),
                  P("DeembedDistance", "length", "0", tab="Post Processing", visible_if=("Deembed", "True")),
                  P("ReferenceConductor", "text", "", label="Reference conductor", tab="Terminals",
                    help="Terminal solution: the conductor on the port face that is the reference (ground) of "
                         "the terminals, as an object or boundary name. Empty: the largest conductor. Every "
                         "other conductor touching the port face is one terminal; the terminals refer to the "
                         "renormalization impedance when Renormalize is on, else to 50 ohm")],
    "Lumped Port": [P("PortNumber", "int", "1"), P("Impedance", "expr", "50ohm", label="Full port impedance"),
                    P("IntegrationLine", "points", "", help="Start;End of the voltage line, from one conductor to the other. Set "
                     "automatically between the two conductors touching the sheet (empty = auto); right click the "
                     "port to draw it in the 3D view or to place it automatically again. Given in the coordinates of the sheet as "
                     "created: the line moves, rotates and mirrors with its object")],
    "Floquet Port": [P("PortNumber", "int", "1"), P("NumberOfModes", "int", "2", help="1 = TE00, 2 = TE00 and TM00"),
                     P("Permittivity", "expr", "1", label="Medium permittivity")],
    "Incident Plane Wave": [P("VectorInput", "choice", "Cartesian", label="Vector input format",
                              choices=("Cartesian", "Spherical")),
                            P("E0Vector", "text", "1,0,0", label="Eo vector (X,Y,Z)",
                              help="Polarization; normalized to a unit vector, scale with E0",
                              visible_if=("VectorInput", "Cartesian")),
                            P("KVector", "text", "0,0,1", label="k direction (X,Y,Z)",
                              visible_if=("VectorInput", "Cartesian")),
                            P("Theta", "angle", "0deg", label="IWavTheta", visible_if=("VectorInput", "Spherical")),
                            P("Phi", "angle", "0deg", label="IWavPhi", visible_if=("VectorInput", "Spherical")),
                            P("Polarization", "angle", "0deg", label="Polarization angle",
                              visible_if=("VectorInput", "Spherical")),
                            P("ZeroPhasePosition", "point", "0,0,0", label="Zero phase position"),
                            P("EMagnitude", "expr", "1", label="E0 (V/m)")],
}
FACE_EXCITATIONS = {"Wave Port", "Lumped Port", "Floquet Port"}
EXCITATION_COLORS = {"Wave Port": "#e74c3c", "Lumped Port": "#e67e22", "Floquet Port": "#c0392b",
                     "Incident Plane Wave": "#f1c40f"}

# --------------------------------------------------------------------------------------------
# Mesh operations
# --------------------------------------------------------------------------------------------
MESH_OPS: dict[str, list[Prop]] = {
    "Length Based On Selection": [P("MaxLength", "length", "1", label="Maximum length of elements"),
                                  P("RestrictElements", "bool", "False", label="Restrict the number of elements"),
                                  P("MaxElements", "int", "1000", label="Maximum number of elements",
                                    visible_if=("RestrictElements", "True"))],
    "Length Based Inside Selection": [P("MaxLength", "length", "1", label="Maximum length of elements"),
                                  P("RestrictElements", "bool", "False", label="Restrict the number of elements"),
                                  P("MaxElements", "int", "1000", label="Maximum number of elements",
                                    visible_if=("RestrictElements", "True"))],
    "Length Based On Edges": [P("MaxLength", "length", "0.5", label="Maximum length of elements"),
                                  P("RestrictElements", "bool", "False", label="Restrict the number of elements"),
                                  P("MaxElements", "int", "1000", label="Maximum number of elements",
                                    visible_if=("RestrictElements", "True"))],
    "Skin Depth Based": [P("SkinDepth", "length", "2um"), P("Layers", "int", "2"),
                         P("SurfaceTriangleLength", "length", "1")],
}

# --------------------------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------------------------
SETUP: list[Prop] = [
    P("Enabled", "bool", "True"),
    P("SolutionFrequencyMode", "choice", "Single", label="Solution frequency",
      choices=("Single", "Multi-Frequencies", "Broadband")),
    P("Frequency", "freq", "5GHz", visible_if=("SolutionFrequencyMode", "Single")),
    P("MultiFrequencies", "text", "1GHz,2GHz", label="Frequencies (each with own Max Delta S)",
      visible_if=("SolutionFrequencyMode", "Multi-Frequencies")),
    P("LowFrequency", "freq", "1GHz", visible_if=("SolutionFrequencyMode", "Broadband")),
    P("HighFrequency", "freq", "10GHz", visible_if=("SolutionFrequencyMode", "Broadband")),
    P("PortsOnly", "bool", "False", label="Solve ports only",
      help="Solve only the port modes of the wave ports: port impedance Zo, propagation constant Gamma and "
           "the port fields at the solution frequency and the sweep frequencies, without the 3D solution "
           "(no S-parameters). For checking ports quickly"),
    P("MaximumPasses", "int", "6", label="Maximum number of passes"),
    P("MaximumDeltaS", "float", "0.02", label="Maximum Delta S"),
    P("MaximumDeltaEnergy", "float", "0.001", label="Maximum Delta Energy",
      help="Incident wave designs (no ports): relative change of the scattered energy between passes (default 0.001)"),
    P("UseMatrixConvergence", "bool", "False", label="Use matrix convergence"),
    P("MaxDeltaMag", "float", "0.02", label="Max delta |S|", visible_if=("UseMatrixConvergence", "True")),
    P("MaxDeltaPhase", "float", "2", label="Max delta phase S (deg)", visible_if=("UseMatrixConvergence", "True")),
    # Options
    P("LambdaRefinement", "bool", "True", label="Do lambda refinement", tab="Options",
      help="On: the initial mesh has no element longer than Lambda target x wavelength in "
           "the material. Off: the initial mesh only follows the geometry, the adaptive passes refine it."),
    P("LambdaTarget", "float", "0.3333", label="Lambda target", tab="Options",
      enabled_if=("LambdaRefinement", "True"),
      help="Maximum element length of the initial mesh as a fraction of the wavelength (0.02 to 1). The "
           "default follows the basis order: 0.1 for Zero Order, 0.3333 for First Order, 0.6667 "
           "for Second Order; changing the order in this dialog changes a default value with it."),
    P("UseFreeSpaceLambda", "bool", "False", label="Use free space lambda", tab="Options"),
    P("PercentRefinement", "float", "30", label="Maximum refinement per pass (%)", tab="Options"),
    P("MaximumRefinement", "int", "1000000", label="Maximum number of tetrahedra", tab="Options"),
    P("MinimumPasses", "int", "2", label="Minimum number of passes", tab="Options",
      help="At least 2: the first pass has nothing to compare with"),
    P("MinimumConvergedPasses", "int", "1", label="Minimum converged passes", tab="Options"),
    P("BasisOrder", "choice", "First Order", label="Order of basis functions", tab="Options",
      choices=("Zero Order", "First Order", "Second Order"),
      help="Zero Order: 6 edge functions per tetrahedron (fast, needs a fine mesh). "
           "First Order: 20 functions, quadratic mixed order (default). Second Order: higher order for "
           "strong field gradients; Hertzon uses the complete quadratic space (30 functions). "
           "'Mixed Order' (order chosen per element) is not available in the EMerge solver."),
    P("CurvedElements", "bool", "True", label="Curved elements on curved surfaces", tab="Options"),
    P("Solver", "choice", "Auto", label="Matrix solver", tab="Options", choices=SOLVERS),
    # Advanced
    P("ErrorInclusion", "float", "50", label="Error field inclusion (%)", tab="Advanced"),
    P("RefinementRatio", "float", "0.6", label="Element size ratio of refined elements", tab="Advanced"),
    P("GrowthRate", "float", "1.6", label="Mesh growth rate", tab="Advanced"),
    P("EnforceRefinementPercent", "bool", "True", label="Enforce refinement percentage", tab="Advanced"),
    P("RefinementMethod", "choice", "hzAURA (EMerge)", label="Mesh refinement", tab="Advanced", choices=("hzAURA (EMerge)", "Local (Delaunay)", "Remesh (slow)", "Remesh (stable, experimental)")),
    P("SaveFields", "bool", "True", label="Save fields at solution frequency", tab="Advanced"),
    P("Threads", "int", "0", label="Solver cores (0 = Tools > Options)", tab="Advanced"),
]

SWEEP: list[Prop] = [
    P("Enabled", "bool", "True"),
    P("SweepType", "choice", "Interpolating", choices=("Discrete", "Interpolating", "Fast")),
    P("Distribution", "choice", "Linear Step", choices=("Linear Step", "Linear Count", "Log Scale", "Single Points")),
    P("Start", "freq", "1GHz", visible_if=("Distribution", "!Single Points")),
    P("Stop", "freq", "10GHz", visible_if=("Distribution", "!Single Points")),
    P("Step", "freq", "0.1GHz", visible_if=("Distribution", "Linear Step")),
    P("Count", "int", "101", visible_if=("Distribution", "Linear Count")),
    P("SamplesPerDecade", "int", "20", visible_if=("Distribution", "Log Scale")),
    P("Points", "text", "1GHz,2GHz", visible_if=("Distribution", "Single Points")),
    P("SaveFields", "bool", "False", label="Save fields (all frequencies)", visible_if=("SweepType", "!Interpolating"),
      help="Store the fields at every sweep frequency: field plots, far fields, gain patterns and antenna "
           "parameters over frequency (needs disk space). Discrete and Fast sweeps"),
    P("ErrorTolerance", "float", "0.5", label="Error tolerance (%)", visible_if=("SweepType", "!Discrete")),
    P("MaxSolutions", "int", "250", label="Max solutions", visible_if=("SweepType", "!Discrete")),
    P("Parallel", "bool", "False", label="Solve frequencies in parallel"),
]

OPTIMIZERS = ("Conjugate Gradient",)
COST_FUNCTIONS = ("Error Area", "Worst Case")
GOAL_CONDITIONS = ("<=", ">=", "=", "Ripple <=")

# Optimization > Parametric ('Add/Edit Sweep' types and the Options tab)
PARAMETRIC_SWEEP_TYPES = ("Single value", "Linear step", "Linear count", "Decade count", "Octave count",
                          "Exponential count")
CALC_FUNCTIONS = ("Value", "Max", "Min", "Mean", "Peak to Peak")
PARAMETRIC: list[Prop] = [
    P("SaveFields", "bool", "False", label="Save fields and mesh",
      help="Keep fields and mesh of every variation (field overlays, far fields). Off: only S-parameters, "
           "convergence and the calculations are kept"),
    P("ParallelVariations", "int", "1", label="Parallel variations",
      help="Variations solved at the same time; the cores are shared between them"),
]

# Optimization > Optimization. Conjugate gradient defaults: the settings of the hairpin filter runs
# (filterbench configs hairpin_3_conjugateGradient_3 / Hairpin_5_conjugateGradient)
OPTIMIZATION: list[Prop] = [
    P("Optimizer", "choice", "Conjugate Gradient", choices=OPTIMIZERS,
      help="Projected nonlinear conjugate gradient (Polak-Ribiere+) on the variables scaled to 0..1 "
           "between their Min and Max, with a finite-difference gradient and a backtracking line search"),
    P("CostFunction", "choice", "Error Area", label="Cost function", choices=COST_FUNCTIONS,
      help="Error Area: area of the goal violations over frequency (dB GHz), weighted and summed. "
           "Worst Case: weighted mean of the squared largest violation of every goal"),
    P("MaxEvaluations", "int", "3000", label="Maximum evaluations",
      help="Upper limit of solved designs; the optimizer usually stops earlier (tolerance or no improvement)"),
    P("ParallelEvaluations", "int", "6", label="Parallel evaluations",
      help="Designs solved at the same time (the gradient samples are independent); the cores are shared"),
    P("DifferenceStep", "float", "0.05", label="Difference step", tab="Conjugate Gradient",
      help="Finite-difference step of the gradient, as a fraction of each variable's range"),
    P("InitialStep", "float", "0.1", label="Initial step", tab="Conjugate Gradient",
      help="First line-search step along the search direction, as a fraction of the variable ranges"),
    P("GradientTolerance", "float", "0.0001", label="Gradient tolerance", tab="Conjugate Gradient",
      help="Stop when the largest projected gradient component falls below this value"),
    P("MaxLineTrials", "int", "16", label="Line search trials", tab="Conjugate Gradient",
      help="Step halvings without improvement before the optimizer stops"),
]

COORDINATE_SYSTEM: list[Prop] = [
    P("Mode", "choice", "Axis/Position", choices=("Axis/Position", "Euler Angle ZXZ", "Euler Angle ZYZ")),
    P("Origin", "point", "0,0,0", help="Origin in the reference coordinate system"),
    P("XAxis", "point", "1,0,0", label="X Axis", visible_if=("Mode", "Axis/Position"),
      help="Direction of the local X axis (vector from the origin)"),
    P("YPoint", "point", "0,1,0", label="Y Point", visible_if=("Mode", "Axis/Position"),
      help="Vector from the origin to a point in the local XY plane"),
    P("Phi", "angle", "0deg", visible_if=("Mode", "!Axis/Position")),
    P("Theta", "angle", "0deg", visible_if=("Mode", "!Axis/Position")),
    P("Psi", "angle", "0deg", visible_if=("Mode", "!Axis/Position")),
]

INFINITE_SPHERE: list[Prop] = [
    P("CoordinateSystem", "choice", "Global", label="Coordinate system", choices=("Global",),
      help="theta/phi are measured in this coordinate system"),
    P("PhiStart", "angle", "0deg"), P("PhiStop", "angle", "360deg"), P("PhiStep", "angle", "10deg"),
    P("ThetaStart", "angle", "0deg"), P("ThetaStop", "angle", "180deg"), P("ThetaStep", "angle", "10deg"),
]

SOLUTION_TYPES = ("Modal", "Terminal", "Eigenmode")
AUTO_OPEN_MODES = ("Off", "Auto Open", "Auto Open - Filter")

DESIGN_SETTINGS: list[Prop] = [
    P("SolutionType", "choice", "Modal", label="Solution type", choices=SOLUTION_TYPES),
    P("AutoOpen", "choice", "Off", label="Auto open region", choices=AUTO_OPEN_MODES),
    P("AutoOpenBoundary", "choice", "Auto", label="Open boundary", choices=("Auto", "Radiation", "PML"),
      visible_if=("AutoOpen", "!Off")),
    P("AutoOpenPadding", "length", "", label="Padding (empty = automatic)", visible_if=("AutoOpen", "!Off")),
    P("FilterAirMargin", "length", "12mm", label="Filter air margin", visible_if=("AutoOpen", "Auto Open - Filter")),
    P("MaterialOverride", "bool", "True", label="Material override"),
]


def material_override(design) -> bool:
    """design setting "Material override": a metal overrides a dielectric where solids overlap."""
    return str(design.settings.get("MaterialOverride", "True")) == "True"

EIGEN_SETUP: list[Prop] = [
    P("MinimumFrequency", "freq", "2GHz"), P("NumberOfModes", "int", "3"),
    P("MaximumPasses", "int", "6"), P("MaximumDeltaFreq", "float", "2", label="Maximum delta frequency (%)"),
    P("MinimumConvergedPasses", "int", "1", tab="Options"), P("LambdaTarget", "float", "0.3333", tab="Options"),
]


# default lambda target per basis order (Options tab of the setup)
LAMBDA_TARGETS = {"Zero Order": "0.1", "First Order": "0.3333", "Second Order": "0.6667"}


def defaults(props: list[Prop]) -> dict[str, str]:
    return {p.name: p.default for p in props}


RECT_SIZES = {"Z": ("XSize", "YSize"), "X": ("YSize", "ZSize"), "Y": ("ZSize", "XSize")}   # (first, second) in-plane


# renamed choice values: old -> current (projects, scripts and AI clients may still send the old ones)
# "Zone (experimental)" (Mmg zone re-mesh) was dropped and fell back to the slow full re-mesh. It comes
# first so that the reverse map (solution keys) gives "Local (Delaunay, experimental)" for Local.
RENAMED_VALUES = {"RefinementMethod": {"Zone (experimental)": "Local (Delaunay)",
                                       "Local (Delaunay, experimental)": "Local (Delaunay)", "Remesh": "Remesh (slow)",
                                       "AURA (EMerge, experimental)": "hzAURA (EMerge)",
                                       # last, so that the reverse map gives "AURA (EMerge)" for hzAURA
                                       "AURA (EMerge)": "hzAURA (EMerge)"}}


def upgrade_values(props: dict) -> dict:
    for k, ren in RENAMED_VALUES.items():
        if props.get(k) in ren:
            props[k] = ren[props[k]]
    return props


def upgrade_props(kind: str, props: dict) -> dict:
    """Older property names to the current ones, in place (projects, scripts and AI clients may still use
    them). CreateRectangle: Width/Height (along the first/second in-plane direction) -> XSize/YSize/ZSize."""
    if kind == "CreateRectangle" and ("Width" in props or "Height" in props):
        a, b = RECT_SIZES.get(str(props.get("Axis", "Z")), RECT_SIZES["Z"])
        if "Width" in props:
            props[a] = props.pop("Width")
        if "Height" in props:
            props[b] = props.pop("Height")
    return props


def is_visible(prop: Prop, values: dict[str, str]) -> bool:
    if not prop.visible_if:
        return True
    key, want = prop.visible_if
    have = str(values.get(key, ""))
    if want.startswith("!"):
        return have != want[1:]
    return have == want
