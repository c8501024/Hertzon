"""Help texts for every property (tooltips and the F1 help)."""
from __future__ import annotations

# property name -> text; "Group.Prop" entries override for one group (group = schema key, e.g. "Wave Port")
HELP: dict[str, str] = {
    # ---- geometry -----------------------------------------------------------------------------
    "Position": "Start corner of the primitive (x, y, z). Expressions and variables are allowed, e.g. -w/2, 0, h.",
    "CenterPosition": "Centre of the primitive (x, y, z).",
    "StartPosition": "A point on the outer contour; its distance from the centre sets the size and its direction "
                     "the orientation of the first vertex.",
    "XSize": "Extent along X (negative values extend in -X).",
    "YSize": "Extent along Y (negative values extend in -Y).",
    "ZSize": "Extent along Z (negative values extend in -Z).",
    "Axis": "Axis the primitive is aligned with (normal of a sheet, axis of a cylinder or cone).",
    "Radius": "Radius.",
    "Height": "Height along the axis (negative values extend in the negative axis direction).",
    "Width": "Size along the first in-plane direction.",
    "LowerRadius": "Radius of the base of the cone.",
    "UpperRadius": "Radius of the top of the cone (0 gives a pointed cone).",
    "MajorRadius": "Distance from the centre to the middle of the ring (torus) or the major semi-axis (ellipse).",
    "MinorRadius": "Radius of the ring cross-section.",
    "NumberOfSides": "Number of sides of the regular polygon / polyhedron.",
    "NumberOfSegments": "0 = true circle; a value >= 3 approximates the circle by a polygon (faster meshing).",
    "Ratio": "Minor-to-major axis ratio of the ellipse.",
    "Points": "Polyline points, separated by ';', each as x, y, z.",
    "Pitch": "Helix: advance along the axis per turn. Spiral: radial growth per turn.",
    "Turns": "Number of turns (may be fractional).",
    "RightHanded": "Winding sense: right handed (counter-clockwise looking against the axis) or left handed.",
    "WireRadius": "Radius of the round wire swept along the helix; 0 creates only the helix curve.",
    "InnerRadius": "Radius where the spiral starts.",
    "SegmentTypes": ("Edge types of the polyline segments: Line, Arc (3 point arc), Spline:n or "
                     "AngularArc:angle:plane (center point arc: start, center and end point; the angle in degrees, "
                     "counter-clockwise about the normal of the plane XY, YZ or ZX, decides the end point). "
                     "While drawing press L, A, C or S."),
    "EndPosition": "End point of the bondwire (on the second pad).",
    "H1": "Bondwire loop height above the start point (JEDEC h1).",
    "H2": "Bondwire height above the end point (JEDEC h2).",
    "Diameter": "Wire diameter.",
    "Trihedron": "How the profile is oriented along the path (OpenCASCADE sweep trihedron); only used for curved paths.",
    "Ruled": "Connect with straight (ruled) faces instead of a smooth loft.",
    "Distance": "Chamfer distance, or the length of the sweep along the face normal.",
    "Faces": "Face indices of the object, comma separated.",
    "Closed": "Connect the last point with the first one.",
    "Covered": "Fill a closed polyline with a sheet.",
    "PaddingType": "Region padding: percentage of the model size or absolute offset per direction.",
    "+X": "Region padding in +X.", "-X": "Region padding in -X", "+Y": "Region padding in +Y.",
    "-Y": "Region padding in -Y.", "+Z": "Region padding in +Z.", "-Z": "Region padding in -Z.",
    "File": "STEP/IGES file to import.",
    "Body": "Index of the body in the file (empty = all bodies).",
    "Vector": "Displacement vector (dx, dy, dz).",
    "Angle": "Rotation angle (degrees unless a unit is given).",
    "Center": "Point on the rotation axis / centre of scaling.",
    "Base": "A point on the mirror plane.",
    "Normal": "Normal vector of the mirror plane.",
    "Factor": "Scale factor (1 = unchanged).",
    "Thickness": "Thickness. A positive value thickens to the positive side of the sheet's main axis "
                 "(+Z for a sheet in XY), a negative value or Reverse direction to the other side. "
                 "The arrow in the 3D view shows the direction.",
    "BothSides": "Thicken symmetrically to both sides of the sheet.",
    "ReverseDirection": "Thicken to the other side of the sheet (-Z for a sheet in XY).",
    "Tools": "Objects used as tools of the boolean operation (comma separated).",
    "KeepOriginals": "Keep copies of the tool objects after the operation.",
    "Plane": "Cutting plane through the global origin.",
    "Keep": "Which side of the cut to keep.",
    "Edges": "Edge indices to round, comma separated.",
    "Vertices": "Vertex indices of a sheet, comma separated: each of these corners is rounded with the radius "
                "(Fillet) or cut off by the distance (Chamfer).",
    # ---- object attributes --------------------------------------------------------------------
    "Name": "Name of the item; must be unique in the design.",
    "Material": "Material assigned to the object. Perfect conductors and metals are not meshed inside.",
    "SolveInside": "Mesh and solve the fields inside the object. Off for good conductors (the surface is then "
                   "a boundary). Not available for Non Model objects.",
    "Model": "Model objects are part of the simulation. Non Model objects are only drawn (e.g. for field plots "
             "on lines or cut planes) and do not invalidate solutions.",
    "Color": "Display colour.",
    "Transparency": "Display transparency, 0 (opaque) to 1 (invisible).",
    "DisplayWireframe": "Show only the edges of the object.",
    "Orientation": "Coordinate system the object is defined in.",
    # ---- boundaries ---------------------------------------------------------------------------
    "InfGroundPlane": "Treat the plane as infinite for far-field calculations.",
    "UseMaterial": "Take conductivity and permeability from a material instead of entering them.",
    "Conductivity": "Electric conductivity in S/m.",
    "Permeability": "Relative permeability of the conductor.",
    "Roughness": "Surface roughness (RMS); increases the loss above the skin-depth model.",
    "UseThickness": "Take the conductor thickness into account: the surface impedance grows once the "
                    "thickness gets comparable to the skin depth ('Use thickness').",
    "TwoSided": "Thin conductor: current flows on both sides of the sheet. Off: one-sided surface "
                "impedance, enough for a trace or patch on a thin substrate.",
    "Resistance": "Resistance (Ohm, or Ohm/square for an impedance boundary).",
    "Reactance": "Reactance (Ohm/square).",
    "UseResistance": "Include a resistor in the lumped RLC.",
    "UseInductance": "Include an inductor in the lumped RLC.",
    "UseCapacitance": "Include a capacitor in the lumped RLC.",
    "Inductance": "Inductance.",
    "Capacitance": "Capacitance.",
    "Order": "Absorbing boundary order: first order or a second-order ABC (less reflection at oblique "
             "incidence). Keep the boundary at least lambda/3 from radiators (guidance; PML: lambda/4).",
    "FarFieldReference": "Use this boundary as the integration surface for far fields.",
    "Layers": "Number of mesh layers / PML layers.",
    "Exponent": "Grading exponent of the PML conductivity profile.",
    "MaxAttenuation": "Attenuation at the outer PML layer.",
    "Type": "Symmetry type: Perfect E (tangential E = 0) or Perfect H (tangential H = 0).",
    "ImpedanceMultiplier": "Factor for port impedances when a symmetry plane cuts a port (2 for one plane). "
                           "Not applied by the solver yet: a port cut by the plane is referred to the half port.",
    "UVector": "In-plane U direction of the periodic boundary (origin;point). The solver links each Secondary "
               "face to the Primary face it is a translated copy of; rotated periodicity is not supported.",
    "Primary": "Primary boundary this secondary boundary is linked to. Its faces have to be the Primary faces "
               "moved by one lattice vector.",
    "PhaseMode": "How the phase between primary and secondary boundary is given: by the scan angles of the "
                 "incident wave (the phase follows the frequency) or as a fixed phase delay (converted to scan "
                 "angles at the solution frequency, which are kept over the sweep).",
    "ScanTheta": "Scan angle theta of the incident wave, from the +z axis. The same for all Secondary "
                 "boundaries and the Floquet ports of the cell.",
    "ScanPhi": "Scan angle phi of the incident wave, from the +x axis in the xy plane.",
    "PhaseDelay": "Phase difference between primary and secondary boundary: the field on the secondary is the "
                  "field on the primary times exp(-j delay).",
    # ---- excitations --------------------------------------------------------------------------
    "PortNumber": "Port index used in S(i,j).",
    "NumberOfModes": "Number of port modes / eigenmodes to compute.",
    "ModeType": "Mode solver type of the port.",
    "ImpedanceDefinition": "How the characteristic impedance Zo of the port is defined (Zpi, Zpv, Zvi).",
    "Renormalize": "Renormalize S-parameters to the reference impedance below.",
    "RenormImpedance": "Reference impedance for renormalization (usually 50 Ohm).",
    "Deembed": "Shift the port reference plane.",
    "DeembedDistance": "De-embedding distance; positive values move the reference plane into the port.",
    "Impedance": "Port reference impedance (Ohm).",
    "Permittivity": "Relative permittivity of the medium at the Floquet port. EMerge's Floquet port assumes free "
                    "space (1): put the port in air or vacuum.",
    "Theta": "Incidence angle theta.",
    "Phi": "Incidence angle phi.",
    "Polarization": "Polarization of the incident plane wave.",
    "EMagnitude": "Amplitude of the incident E field (V/m).",
    # ---- setup --------------------------------------------------------------------------------
    "Enabled": "Include this setup / sweep in Analyze All.",
    "SolutionFrequencyMode": "Single frequency, multiple frequencies or broadband adaptive meshing.",
    "Frequency": "Solution (adaptive meshing) frequency. Choose the highest frequency of interest for "
                 "filters; for antennas the operating frequency.",
    "MultiFrequencies": "Adaptive frequencies, separated by ';'.",
    "LowFrequency": "Lower adaptive frequency (broadband).",
    "HighFrequency": "Upper adaptive frequency (broadband).",
    "MaximumPasses": "Maximum number of adaptive passes. Raising it later continues from the last adaptive "
                     "mesh (only convergence settings changed).",
    "MaximumDeltaS": "Convergence target: maximum change of any S-parameter magnitude between two passes.",
    "UseMatrixConvergence": "Use separate magnitude and phase targets instead of Delta S.",
    "MaxDeltaMag": "Maximum change of |S| between passes.",
    "MaxDeltaPhase": "Maximum change of the S-parameter phase between passes (degrees).",
    "LambdaRefinement": "Initial mesh refinement so that elements are smaller than a fraction of the wavelength.",
    "LambdaTarget": "Initial element size as a fraction of the wavelength (default: 0.1 / 0.3333 / 0.6667 "
                    "for Zero / First / Second Order).",
    "UseFreeSpaceLambda": "Use the free-space wavelength instead of the wavelength in the material.",
    "PercentRefinement": "Target growth of the number of tetrahedra per adaptive pass (%).",
    "MaximumRefinement": "Upper limit of the number of tetrahedra.",
    "MinimumPasses": "Minimum number of adaptive passes before convergence is accepted (at least 2).",
    "MinimumConvergedPasses": "Number of consecutive converged passes required.",
    "BasisOrder": "Order of basis functions: Zero Order (6 per tetrahedron), First Order (20, "
                  "default), Second Order (higher order for strong field gradients).",
    "Solver": "Sparse matrix solver (Auto selects the fastest available direct solver). SPARTA (GPU) factors "
              "the matrix on the graphics card (Metal on Apple silicon, Vulkan on AMD and NVIDIA) in single "
              "precision, SPARTA (CPU) on the processor in double precision; both need SPARTA installed "
              "(Tools > Options > Install SPARTA). 'PARDISO + SPARTA sweep' solves the adaptive passes with PARDISO "
              "(every pass is a new mesh, where SPARTA's analysis is slow) and the sweeps with SPARTA. Without a usable GPU, SPARTA (GPU) factorizes on the CPU. "
              "With SPARTA the frequencies of a sweep are solved one after another, and the Fast sweep keeps "
              "PARDISO.",
    "ErrorInclusion": "Share of the estimated error that selects elements for refinement (%).",
    "RefinementRatio": "Size reduction of refined elements.",
    "GrowthRate": "Mesh grading away from refined regions.",
    "EnforceRefinementPercent": "Adjust the refinement until the target percentage is reached.",
    "CurvedElements": "Tetrahedra at curved metal and material surfaces (cylinders, spheres, splines) get curved edges that follow the true geometry instead of flat facets. Removes the faceting error, so round posts, rods and dielectric resonators converge with far fewer elements. Applies to PEC, finite-conductivity metal, symmetry and material surfaces. Surfaces that touch a port, and impedance, lumped and thin-conductor faces, stay flat. Faceted primitives (NumberOfSegments/NumberOfVertices > 0) are flat by definition. Switch off only to compare with the plain flat-faceted mesh.",
    "RefinementMethod": "hzAURA (EMerge), the default: EMerge's local refinement, the worst elements are bisected along their longest edge and the new elements are improved by flips and smoothing; where a new node on a curved surface cannot be moved onto it that way, the elements around it are re-meshed (Delaunay cavity) so the surface stays exact; old nodes never move (nested meshes, no gmsh run per pass). Local (Delaunay): local, the mesh is refined in place by local Delaunay point insertion around the worst elements; everything else stays exactly the same (nested meshes, no re-meshing noise, no gmsh run per pass). Remesh (slow): re-mesh the whole model each pass with gmsh. Remesh (stable, experimental): re-mesh, but every region keeps its current element size and only the worst elements are halved (nearly nested meshes, smoother convergence; test option).",
    "SaveFields": "Store fields for field plots (needs disk space). In a sweep (Discrete or Fast): the fields at "
                  "every frequency, needed for far fields, gain patterns and antenna parameters over frequency; "
                  "an Interpolating sweep has S-parameters only.",
    "Threads": "Number of CPU cores used by the solver (0 = all).",
    "MinimumFrequency": "Lowest eigenfrequency to search for.",
    "MaximumDeltaFreq": "Convergence target: maximum relative change of the eigenfrequencies (%).",
    # ---- sweeps -------------------------------------------------------------------------------
    "SweepType": "Discrete solves every point; Interpolating solves few points and fits a rational model; "
                 "Fast factorizes the matrix once at the centre frequency and solves every point in a reduced basis built from it (model order reduction, Fast/ALPS); S-parameters and fields exist at every point, accuracy is checked by the residual at each frequency.",
    "Distribution": "Linear Step, Linear Count, Log Scale or a list of single points.",
    "Start": "First frequency of the sweep.",
    "Stop": "Last frequency of the sweep.",
    "Step": "Frequency step.",
    "Count": "Number of frequency points.",
    "SamplesPerDecade": "Points per decade for logarithmic sweeps.",
    "ErrorTolerance": "Interpolating sweep: convergence tolerance of the fit (%). Fast sweep: the allowed relative residual of every reduced solution is 0.02 times this value (0.5 % gives 1e-4).",
    "MaxSolutions": "Interpolating sweep: maximum number of solved frequencies.",
    "Parallel": "Solve several sweep frequencies at the same time, each with PARDISO on its share of the cores (Tools > Options: Parallel sweep processes, e.g. 4 on 64 cores = 16 cores per frequency). PARDISO gets less efficient with very many threads on one matrix, so a few frequencies at once usually finish a sweep sooner. Every frequency in flight needs its own factorization in memory; Hertzon runs fewer at once when the memory would not suffice.",
    # ---- far field ----------------------------------------------------------------------------
    "PhiStart": "Start of the phi range.", "PhiStop": "End of the phi range.", "PhiStep": "Phi step.",
    "ThetaStart": "Start of the theta range.", "ThetaStop": "End of the theta range.", "ThetaStep": "Theta step.",
    # ---- mesh operations ----------------------------------------------------------------------
    "MaxLength": "Maximum element edge length on / inside the selection.",
    "SkinDepth": "Skin depth; elements near the surface are refined to resolve it.",
    "SurfaceTriangleLength": "Maximum surface triangle edge length.",
    "RestrictElements": "Limit the number of elements this operation may create; the length is enlarged if needed (Restrict the number of elements).",
    "MaxElements": "Maximum number of elements created by this mesh operation.",
    # ---- design settings ----------------------------------------------------------------------
    "SolutionType": "Modal (S-parameters by port modes), Terminal (S-parameters by terminals: one per lumped "
                    "port and one per signal conductor of a wave port, with differential pairs) or Eigenmode.",
    "AutoOpen": "Automatically add an air region with a radiation boundary around the model.",
    "AutoOpenBoundary": "Boundary type of the automatic open region. With an incident plane wave the radiation boundary is "
                        "always used (the incident wave enters through it).",
    "AutoOpenPadding": "Distance of the open region from the model.",
    "FilterAirMargin": "Air margin around filters for the open region.",
    "MaterialOverride": "Where solids overlap, a metal overrides a dielectric and an object completely inside "
                        "another overrides it. Any other partial overlap of different materials is an error. "
                        "Off: every partial overlap of different materials is an error and an object inside "
                        "another always wins, also a dielectric inside a metal.",
}


def help_for(prop, group: str = "") -> str:
    """Help text of a schema Prop (its own help, a group override, or the general text)."""
    if getattr(prop, "help", ""):
        return prop.help
    return HELP.get(f"{group}.{prop.name}", HELP.get(prop.name, ""))
