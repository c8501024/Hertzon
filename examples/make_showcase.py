"""Create the feature showcase project: one file that uses nearly every modelling, setup and post-processing
feature of Hertzon, for testing the GUI, the MCP server and scripts against a single rich project.

python examples/make_showcase.py  ->  examples/Showcase_AllFeatures.hzn

Face and edge indices are taken from the built geometry, so the script needs the geometry kernel (gmsh).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hertzon.model.design import (Project, Design, Command, Assignment, FaceRef, Setup, Sweep,  # noqa: E402
                                  InfiniteSphere, CoordSys, Report, FieldPlot, FieldExpression, Optimization,
                                  ParametricSetup)
from hertzon.model.materials import Material  # noqa: E402
from hertzon.model import schema  # noqa: E402

OUT = Path(__file__).resolve().parent / "Showcase_AllFeatures.hzn"


# ---- helpers (as in make_examples.py) ----------------------------------------------------------------------
def boundary(d: Design, kind: str, name: str, faces=(), objects=(), edges=(), **props) -> Assignment:
    p = schema.defaults(schema.BOUNDARIES[kind])
    p.update({k: str(v) for k, v in props.items()})
    a = Assignment(name, kind, p, faces=list(faces), objects=list(objects), edges=list(edges))
    d.boundaries.append(a)
    return a


def excitation(d: Design, kind: str, name: str, faces=(), objects=(), **props) -> Assignment:
    p = schema.defaults(schema.EXCITATIONS[kind])
    p.update({k: str(v) for k, v in props.items()})
    a = Assignment(name, kind, p, faces=list(faces), objects=list(objects))
    d.excitations.append(a)
    return a


def mesh_op(d: Design, kind: str, name: str, faces=(), objects=(), edges=(), **props) -> Assignment:
    p = schema.defaults(schema.MESH_OPS[kind])
    p.update({k: str(v) for k, v in props.items()})
    a = Assignment(name, kind, p, faces=list(faces), objects=list(objects), edges=list(edges))
    d.mesh_ops.append(a)
    return a


def setup(d: Design, name="Setup1", **props) -> Setup:
    s = Setup(name)
    s.props.update({k: str(v) for k, v in props.items()})
    d.setups.append(s)
    return s


def eigen_setup(d: Design, name="Eigen1", **props) -> Setup:
    s = Setup(name, eigen=True)
    s.props = {**schema.defaults(schema.EIGEN_SETUP), "_BasisNames": "v2"}
    s.props.update({k: str(v) for k, v in props.items()})
    d.setups.append(s)
    return s


def sweep(s: Setup, name="Sweep", **props) -> Sweep:
    sw = Sweep(name)
    sw.props.update({k: str(v) for k, v in props.items()})
    s.sweeps.append(sw)
    return sw


def sphere(d: Design, name: str, **props) -> InfiniteSphere:
    sp = InfiniteSphere(name)
    sp.props.update({k: str(v) for k, v in props.items()})
    d.spheres.append(sp)
    return sp


def cs(d: Design, name: str, kind="Relative", reference="Global", **props) -> CoordSys:
    c = CoordSys(name, kind, reference)
    c.props.update({k: str(v) for k, v in props.items()})
    d.coord_systems.append(c)
    return c


def op(o, kind: str, **props) -> Command:
    c = Command.new(kind, **props)
    o.history.append(c)
    return c


def consume(d: Design, blank, kind: str, *tools, **props) -> Command:
    """Boolean / two-object operation: the tools are consumed by the blank."""
    for t in tools:
        d.obj(t.name).consumed_by = blank.name
    return op(blank, kind, Tools=",".join(t.name for t in tools), **props)


def field_plot(d: Design, name: str, quantity: str, solution: str, objects=(), faces=(), plane="", **props):
    fp = FieldPlot(name, quantity, solution, faces=list(faces), objects=list(objects), plane=plane)
    fp.props.update({k: str(v) for k, v in props.items()})
    d.field_plots.append(fp)
    return fp


# ---- built geometry: face and edge indices by position -----------------------------------------------------
class Built:
    """The design built with the geometry kernel, for picking faces and edges by their position."""

    def __init__(self, p: Project, d: Design):
        from hertzon.kernel import occ as K
        K.ensure_gmsh("make_showcase")
        self.ev = d.evaluator(p)
        self.ctx = K.build_design(d, self.ev)
        if self.ctx.errors:
            raise RuntimeError(f"{d.name}: {self.ctx.errors}")
        self.tess = K.tessellate(self.ctx)
        self.scale = {"mm": 1e-3, "um": 1e-6, "cm": 1e-2, "m": 1.0, "mil": 25.4e-6, "in": 0.0254}[d.units]

    def faces(self, obj: str, where) -> list[FaceRef]:
        """Faces of obj whose centre (design units) and unit normal satisfy where(c, n)."""
        t = self.tess[obj]
        out = []
        for i, (c, n, a) in enumerate(zip(t.face_center, t.face_normal, t.face_area)):
            c = np.asarray(c, float)
            n = np.asarray(n, float)
            if where(c / self.scale, n / (np.linalg.norm(n) or 1.0)):
                out.append(FaceRef(obj, i, [*map(float, c), *map(float, n), float(a)]))
        if not out:
            raise RuntimeError(f"no face of {obj} matches")
        return out


# ---- materials ----------------------------------------------------------------------------------------------
def project_materials() -> list[Material]:
    return [
        Material("Showcase_Laminate_DS", permittivity="er_lam", loss_tangent="0.0027", color="#c8a060",
                 description="Laminate with Djordjevic-Sarkar dispersion (values at 10 GHz)",
                 dispersion="Djordjevic-Sarkar", measured_frequency="10GHz"),
        Material("Showcase_Debye_Absorber", permittivity="6", loss_tangent="0.05", color="#556b2f",
                 description="Debye relaxation material", dispersion="Debye", eps_static="12",
                 eps_infinity="4", relaxation_frequency="2GHz"),
        Material("Showcase_Anisotropic", permittivity="9.4", loss_tangent="0.0002", color="#9fc5e8",
                 description="Uniaxial crystal (diagonal tensor)", anisotropic=True,
                 permittivity_tensor="9.4,9.4,11.6", permeability_tensor="1,1,1",
                 loss_tangent_tensor="0.0002,0.0002,0.0003"),
        Material("Showcase_Ferrite", permittivity="13", permeability="40", loss_tangent="0.0005",
                 magnetic_loss_tangent="0.02", color="#3d3d3d", description="Lossy magnetic material"),
        Material("Showcase_Brass", conductivity="15900000", color="#c9a227", description="Conductor"),
        Material("Showcase_PEC", pec=True, color="#ffd700", description="Perfect conductor (project copy)"),
    ]


# ---- A: 2x1 patch array, everything around a driven antenna ------------------------------------------------
def design_patch_array(p: Project) -> Design:
    d = Design("A_PatchArray")
    d.notes = ("Driven antenna: 2x1 inset-fed patch array on a dispersive laminate with a DC-block chip capacitor "
               "(Lumped RLC, grouped with its body), lumped port, finite conductivity, a radome in a relative "
               "coordinate system, all mesh operations, three setups (single, multi-frequency, broadband), "
               "Interpolating / Fast / Discrete sweeps, two far-field spheres, output variables, Fields "
               "Calculator expressions and functions, every report type of a driven antenna, field overlays on "
               "objects, faces and cut planes, an optimization and a parametric sweep.")
    d.settings.update(AutoOpen="Auto Open", AutoOpenBoundary="Radiation", RadiatingStructure="True",
                      OutputVariables="RL = -dB(S(1,1))\nZin_re = re(Z(1,1))\nZin_im = im(Z(1,1))")
    d.variables.update({
        "h": "1.524mm", "Lp": "32.4mm", "Wp": "40mm", "inset": "10mm", "gap": "1mm", "wf": "3.4mm",
        "d_el": "0.8*c0/f0", "y0": "0mm", "yj": "-14mm", "Wsub": "d_el+Wp+30mm", "Lsub": "Lp+50mm",
        "ysub": "-Lsub+Lp+15mm", "cut": "2.5mm", "g_c": "1mm", "yc": "(ysub+yj)/2", "h_rad": "10mm",
        "t_rad": "2mm",
    })
    # substrate and ground
    d.add_object("CreateBox", name="Substrate", material="Showcase_Laminate_DS",
                 Position="-Wsub/2,ysub,0", XSize="Wsub", YSize="Lsub", ZSize="h")
    d.add_object("CreateRectangle", name="Ground", Position="-Wsub/2,ysub,0", Axis="Z", XSize="Wsub", YSize="Lsub")
    # patches: inset notches subtracted, chamfered corners on the right patch, filleted on the left one
    radiator = None
    for side, xc in (("R", "d_el/2"), ("L", "-d_el/2")):
        pt = d.add_object("CreateRectangle", name=f"Patch{side}", material="pec", Position=f"{xc}-Wp/2,y0,h",
                          Axis="Z", XSize="Wp", YSize="Lp")
        if side == "R":
            op(pt, "Chamfer", Distance="cut", Vertices="1,3")
        else:
            op(pt, "Fillet", Radius="cut", Vertices="1,3")
        notch = d.add_object("CreateRectangle", name=f"Notch{side}", Position=f"{xc}-wf/2-gap,y0,h", Axis="Z",
                             XSize="wf+2*gap", YSize="inset")
        consume(d, pt, "Subtract", notch)
        feed = d.add_object("CreateRectangle", name=f"Feed{side}", Position=f"{xc}-wf/2,yj,h", Axis="Z",
                            XSize="wf", YSize="y0+inset-yj")
        consume(d, pt, "Unite", feed)
        if radiator is None:
            radiator = pt
        else:
            consume(d, radiator, "Unite", pt)
    bar = d.add_object("CreateRectangle", name="FeedBar", Position="-d_el/2-wf/2,yj-wf,h", Axis="Z",
                       XSize="d_el+wf", YSize="wf")
    main_b = d.add_object("CreateRectangle", name="MainB", Position="-wf/2,yc+g_c/2,h", Axis="Z", XSize="wf",
                          YSize="yj-wf-yc-g_c/2")
    consume(d, radiator, "Unite", bar, main_b)
    radiator.name = "Radiator"
    for o in d.objects:
        if o.consumed_by == "PatchR":
            o.consumed_by = "Radiator"
    radiator.color = "#d4a017"
    main_a = d.add_object("CreateRectangle", name="MainA", material="pec", Position="-wf/2,ysub,h", Axis="Z",
                          XSize="wf", YSize="yc-g_c/2-ysub")
    main_a.color = "#d4a017"
    # DC block: chip capacitor sheet + 3D body (not in the model) as one group
    cap = d.add_object("CreateRectangle", name="C1", Position="-wf/2,yc-g_c/2,h", Axis="Z", XSize="wf",
                       YSize="g_c")
    body = d.add_object("CreateBox", name="C1_Body", material="Al2_O3_ceramic",
                        Position="-0.8mm,yc-g_c/2-0.4mm,h", XSize="1.6mm", YSize="g_c+0.8mm", ZSize="0.8mm")
    body.model = False
    body.transparency = 0.4
    cap.group = body.group = "C1_0603"
    # port sheet at the board edge
    port = d.add_object("CreateRectangle", name="PortSheet", Position="-wf/2,ysub,0", Axis="Y", ZSize="h",
                        XSize="wf")
    # radome: defined in a relative coordinate system, filleted edges, transparent
    cs(d, "CS_Array", Origin="0,Lp/2,h", XAxis="1,0,0", YPoint="0,1,0")
    cs(d, "CS_Tilted", reference="CS_Array", Mode="Euler Angle ZXZ", Phi="0deg", Theta="30deg", Psi="0deg")
    cs(d, "CS_Rot45", reference="CS_Array", Mode="Euler Angle ZYZ", Phi="45deg", Theta="0deg", Psi="0deg")
    rad = d.add_object("CreateBox", name="Radome", material="PLA", Position="-Wsub/2,-Lp/2-12mm,h_rad",
                       XSize="Wsub", YSize="Lp+24mm", ZSize="t_rad")
    rad.orientation = "CS_Array"
    rad.transparency = 0.7
    op(rad, "Fillet", Radius="0.5mm", Edges="0,2,4,6")

    # boundaries and excitation
    boundary(d, "Finite Conductivity", "Copper_Traces", objects=["Radiator", "MainA"], UseMaterial="True",
             Material="copper", Roughness="1um", UseThickness="True", Thickness="35um")
    boundary(d, "Finite Conductivity", "Ground_Brass", objects=["Ground"], UseMaterial="False",
             Conductivity="15.9e6", Permeability="1")
    boundary(d, "Lumped RLC", "C1_RLC", objects=["C1"], Model="Series RLC", UseResistance="True",
             Resistance="0.15ohm", UseInductance="True", Inductance="0.6nH", UseCapacitance="True",
             Capacitance="22pF", Component="0603", IntegrationLine="0,yc-g_c/2,h;0,yc+g_c/2,h")
    excitation(d, "Lumped Port", "P1", objects=["PortSheet"], PortNumber=1, Impedance="50ohm")

    b = Built(p, d)
    top = b.faces("Substrate", lambda c, n: n[2] > 0.9)
    mesh_op(d, "Length Based On Selection", "Mesh_Radiator", objects=["Radiator"], MaxLength="3mm")
    mesh_op(d, "Length Based Inside Selection", "Mesh_Substrate", objects=["Substrate"], MaxLength="6mm",
            RestrictElements="True", MaxElements="60000")
    mesh_op(d, "Length Based On Edges", "Mesh_FeedEdges", edges=[FaceRef("MainA", i) for i in range(4)],
            MaxLength="0.8mm")
    mesh_op(d, "Skin Depth Based", "Mesh_GroundSkin", objects=["Ground"], SkinDepth="1.3um", Layers="2",
            SurfaceTriangleLength="4mm")
    c, n = top[0].sig[:3], top[0].sig[3:6]
    fc = CoordSys("FaceCS_SubstrateTop", "Face", "Global")
    fc.props.update(Mode="Axis/Position", Origin=",".join(f"{v / b.scale:.6g}" for v in c), XAxis="1,0,0",
                    YPoint="0,1,0", Face=f"Substrate:{top[0].face}")
    d.coord_systems.append(fc)
    d.working_cs = "CS_Array"

    # analysis
    s1 = setup(d, "Setup1", Frequency="f0", MaximumPasses=10, MaximumDeltaS=0.02, UseMatrixConvergence="True",
               MaxDeltaMag=0.02, MaxDeltaPhase=2, MinimumPasses=3, MinimumConvergedPasses=2,
               PercentRefinement=25, MaximumRefinement=600000, Solver="Auto")
    sweep(s1, "Interp", SweepType="Interpolating", Distribution="Linear Count", Start="2GHz", Stop="3GHz",
          Count=201, ErrorTolerance=0.5, MaxSolutions=60)
    sweep(s1, "Fast", SweepType="Fast", Distribution="Linear Step", Start="2.3GHz", Stop="2.6GHz", Step="0.01GHz",
          SaveFields="True")
    sweep(s1, "Points", SweepType="Discrete", Distribution="Single Points", Points="2.4GHz,2.45GHz,2.5GHz",
          SaveFields="True", Parallel="True")
    sweep(s1, "LogScan", SweepType="Interpolating", Distribution="Log Scale", Start="1GHz", Stop="6GHz",
          SamplesPerDecade=40, Enabled="False")
    s2 = setup(d, "Setup_Multi", SolutionFrequencyMode="Multi-Frequencies", MultiFrequencies="2.4GHz,2.5GHz",
               MaximumPasses=8, BasisOrder="Second Order", LambdaTarget="0.6667", UseFreeSpaceLambda="True",
               SaveFields="True", Threads=4, Enabled="False")
    sweep(s2, "Step", SweepType="Discrete", Distribution="Linear Step", Start="2.3GHz", Stop="2.6GHz",
          Step="0.05GHz")
    setup(d, "Setup_Broadband", SolutionFrequencyMode="Broadband", LowFrequency="2GHz", HighFrequency="3GHz",
          MaximumPasses=6, BasisOrder="Zero Order", LambdaTarget="0.1", LambdaRefinement="True",
          ErrorInclusion=40, RefinementRatio=0.5, GrowthRate=1.5, Solver="MUMPS", Enabled="False")
    sphere(d, "FF_Global", PhiStep="5deg", ThetaStep="5deg")
    sphere(d, "FF_Upper_Tilted", CoordinateSystem="CS_Tilted", ThetaStop="90deg", PhiStep="15deg", ThetaStep="3deg")

    # fields calculator
    d.field_exprs += [FieldExpression("Pin", "flux(PortSheet, re(S))", "power into the port sheet"),
                      FieldExpression("magE", "|E|", "magnitude of E"),
                      FieldExpression("Emax_sub", "emax(Substrate)", "largest |E| in the laminate"),
                      FieldExpression("RL_calc", "-dB(S(1,1))", "return loss from the network data")]
    d.field_funcs.append("emax(G) = max(G, |E|)")

    # reports
    R = d.reports
    R.append(Report("S11 and Return Loss", "Rectangular", "Setup1 : Interp", ["dB(S(1,1))", "RL"],
                    {"markers": "2.45e9"}))
    R.append(Report("Input Impedance", "Rectangular", "Setup1 : Interp", ["Zin_re", "Zin_im", "VSWR(1)"]))
    R.append(Report("Smith Chart 1", "Smith Chart", "Setup1 : Interp", ["S(1,1)"]))
    R.append(Report("Data Table 1", "Data Table", "Setup1 : Points", ["dB(S(1,1))", "ang_deg(S(1,1))",
                                                                       "re(Z(1,1))", "im(Z(1,1))"]))
    R.append(Report("Convergence", "Rectangular", "Setup1 : AdaptivePasses", ["dB(S(1,1))"]))
    R.append(Report("Gain E-Plane", "Radiation Pattern", "Setup1 : LastAdaptive", ["dB(GainTotal)"],
                    {"cut": "Phi", "cut_value": "90", "sphere": "FF_Global"}))
    R.append(Report("Gain H-Plane", "Radiation Pattern", "Setup1 : LastAdaptive", ["dB(GainTotal)", "dB(GainTheta)",
                                                                                     "dB(GainPhi)"],
                    {"cut": "Phi", "cut_value": "0", "sphere": "FF_Global"}))
    R.append(Report("Far Field Plot 1", "Radiation Pattern", "Setup1 : LastAdaptive", ["dB(RealizedGainTotal)"],
                    {"cut": "Theta", "cut_value": "30", "plot": "Rectangular", "sphere": "FF_Upper_Tilted"}))
    R.append(Report("3D Gain", "3D Polar Plot", "Setup1 : LastAdaptive", ["dB(GainTotal)"], {"sphere": "FF_Global"}))
    R.append(Report("Antenna Parameters Plot 1", "Antenna Parameters", "Setup1 : Fast",
                    ["dB(PeakGain)", "dB(PeakRealizedGain)", "dB(PeakDirectivity)"], {"sphere": "FF_Global"}))
    R.append(Report("Antenna Parameters Table 1", "Antenna Parameters Table", "Setup1 : LastAdaptive",
                    ["dB(PeakDirectivity)", "dB(PeakGain)", "dB(PeakRealizedGain)", "RadiationEfficiency",
                     "RadiatedPower", "AcceptedPower", "dB(FrontToBackRatio)", "dB(AxialRatioAtMax)"],
                    {"sphere": "FF_Global"}))
    R.append(Report("Near Field 1", "Near Field Line", "Setup1 : LastAdaptive", ["mag(NearETotal)"],
                    {"start": "-60,15,20", "end": "60,15,20", "points": "121"}))
    R.append(Report("Fields Plot 1", "Rectangular", "Setup1 : Fast", ["Pin"], {"port": "1", "fields": "1"}))
    R.append(Report("Fields Table 1", "Data Table", "Setup1 : Points", ["Pin", "Emax_sub"],
                    {"port": "1", "fields": "1"}))
    R.append(Report("Inset Families", "Rectangular", "Setup1 : Interp", ["dB(S(1,1))"],
                    {"families": "ParametricInset"}))

    # field overlays
    field_plot(d, "Jsurf_Radiator", "Mag_Jsurf", "Setup1 : LastAdaptive", objects=["Radiator"], Log="True",
               ColorMap="Rainbow")
    field_plot(d, "Vector_Jsurf_Radiator", "Vector_Jsurf", "Setup1 : LastAdaptive", objects=["Radiator"],
               ArrowCount="800")
    field_plot(d, "E_SubstrateTop", "ComplexMag_E", "Setup1 : LastAdaptive", faces=top)
    field_plot(d, "E_Cut_YZ", "Mag_E", "Setup1 : LastAdaptive", plane="YZ@d_el/2", dB="True")
    field_plot(d, "H_Cut_XZ", "Mag_H", "Setup1 : LastAdaptive", plane="XZ@Lp/2")
    field_plot(d, "Vector_E_XY", "Vector_E", "Setup1 : LastAdaptive", plane="XY@h/2")
    field_plot(d, "Gain3D", "RadiationField", "LastAdaptive")

    # optimetrics
    opt = Optimization("Optim_Match", "Setup1 : Interp",
                       [{"name": "Lp", "include": True, "min": "30mm", "max": "35mm"},
                        {"name": "inset", "include": True, "min": "6mm", "max": "13mm"},
                        {"name": "wf", "include": False, "min": "2.8mm", "max": "4mm"}],
                       [{"calc": "dB(S(1,1))", "start": "2.4GHz", "stop": "2.5GHz", "cond": "<=", "value": "-15",
                         "weight": "1"},
                        {"calc": "re(Z(1,1))", "start": "2.45GHz", "stop": "2.45GHz", "cond": "=", "value": "50",
                         "weight": "0.2"},
                        {"calc": "dB(S(1,1))", "start": "2.42GHz", "stop": "2.48GHz", "cond": "Ripple <=",
                         "value": "3", "weight": "0.5"}])
    opt.props.update(CostFunction="Worst Case", MaxEvaluations="200", ParallelEvaluations="4",
                     DifferenceStep="0.04", InitialStep="0.15")
    d.optimetrics.append(opt)
    ps = ParametricSetup("ParametricInset",
                         sweeps=[{"variable": "inset", "type": "Linear step", "start": "8mm", "stop": "12mm",
                                  "step": "1mm", "sync": ""},
                                 {"variable": "Lp", "type": "Linear count", "start": "32mm", "stop": "33mm",
                                  "step": "3", "sync": ""}],
                         setups=["Setup1"],
                         calcs=[{"calc": "dB(S(1,1))", "solution": "Setup1 : Interp", "func": "Min", "start": "2GHz",
                                 "stop": "3GHz"},
                                {"calc": "re(Z(1,1))", "solution": "Setup1 : LastAdaptive", "func": "Value",
                                 "start": "", "stop": ""}])
    ps.props.update(SaveFields="False", ParallelVariations="2")
    d.parametrics.append(ps)
    ps2 = ParametricSetup("ParametricWidthSync",
                          sweeps=[{"variable": "wf", "type": "Single value", "start": "3.2mm", "sync": "1"},
                                  {"variable": "gap", "type": "Single value", "start": "0.8mm", "sync": "1"}],
                          setups=["Setup1"],
                          calcs=[{"calc": "dB(S(1,1))", "solution": "Setup1 : Interp", "func": "Peak to Peak",
                                  "start": "2.4GHz", "stop": "2.5GHz"}])
    d.parametrics.append(ps2)
    return d


# ---- B: waveguide iris filter, half model with a symmetry plane -------------------------------------------
def design_waveguide_filter(p: Project) -> Design:
    d = Design("B_WaveguideIrisFilter_Half")
    d.notes = ("WR-90 two-cavity iris filter, half model: the full guide is cut with Split, the symmetry plane "
               "x = a/2 is a Perfect H Symmetry boundary. Wave ports with two modes, renormalization and "
               "de-embedding, an impedance wall, a dielectric tuning post (Unite with a clone, KeepOriginals), "
               "Perfect E / Perfect H assignments, a parametric family over the iris width and an eigen-free "
               "driven sweep.")
    d.variables.update({"a": "22.86mm", "b": "10.16mm", "L": "60mm", "t_ir": "2mm", "w_ir": "11mm",
                        "Lc": "17mm", "r_tune": "1.2mm", "h_tune": "3mm"})
    wg = d.add_object("CreateBox", name="Guide", material="vacuum", Position="0,0,0", XSize="a", YSize="L",
                      ZSize="b")
    irises = []
    for i, y in enumerate(("L/2-Lc/2-t_ir", "L/2+Lc/2"), 1):
        for j, x in enumerate(("0", "a/2+w_ir/2"), 1):
            ir = d.add_object("CreateBox", name=f"Iris{i}{'LR'[j - 1]}", material="pec",
                              Position=f"{x},{y},0", XSize="a/2-w_ir/2", YSize="t_ir", ZSize="b")
            irises.append(ir)
    consume(d, wg, "Subtract", *irises)
    op(wg, "Split", Plane="YZ", Keep="Negative", CoordinateSystem="CS_Mid")
    cs(d, "CS_Mid", Origin="a/2,0,0")
    tune = d.add_object("CreateCylinder", name="TuningPost", material="Al2_O3_ceramic", CenterPosition="a/4,L/2,b",
                        Radius="r_tune", Height="-h_tune", Axis="Z", NumberOfSegments=12)
    tune.color = "#f7f7ef"
    p1 = d.add_object("CreateRectangle", name="Port1Sheet", Position="0,0,0", Axis="Y", ZSize="b", XSize="a/2")
    p2 = d.add_object("CreateRectangle", name="Port2Sheet", Position="0,L,0", Axis="Y", ZSize="b", XSize="a/2")
    excitation(d, "Wave Port", "WP1", objects=[p1.name], PortNumber=1, NumberOfModes=2, ModeType="TE",
               ImpedanceDefinition="Zpi", Deembed="True", DeembedDistance="-5mm")
    excitation(d, "Wave Port", "WP2", objects=[p2.name], PortNumber=2, NumberOfModes=1, ImpedanceDefinition="Zvi",
               Renormalize="True", RenormImpedance="50ohm")
    b = Built(p, d)
    sym = b.faces("Guide", lambda c, n: abs(c[0] - 11.43) < 1e-6 and abs(n[0]) > 0.99)
    boundary(d, "Symmetry", "Sym_PerfectH", faces=sym, Type="Perfect H", ImpedanceMultiplier="2")
    topw = b.faces("Guide", lambda c, n: abs(c[2] - 10.16) < 1e-6 and n[2] > 0.99)
    boundary(d, "Impedance", "TopWall_Rs", faces=topw, Resistance="0.03", Reactance="0.03")
    botw = b.faces("Guide", lambda c, n: abs(c[2]) < 1e-6 and n[2] < -0.99)
    boundary(d, "Perfect E", "BottomWall_PEC", faces=botw)
    s = setup(d, "Setup1", Frequency="10GHz", MaximumPasses=10, MaximumDeltaS=0.01, PercentRefinement=30)
    sweep(s, "Sweep", SweepType="Interpolating", Distribution="Linear Count", Start="8.5GHz", Stop="11.5GHz",
          Count=301)
    sweep(s, "Coarse", SweepType="Discrete", Distribution="Linear Step", Start="8.5GHz", Stop="11.5GHz",
          Step="0.25GHz", SaveFields="True")
    d.reports.append(Report("S Parameters", "Rectangular", "Setup1 : Sweep",
                            ["dB(S(1,1))", "dB(S(2,1))", "dB(S(1:2,1:1))"]))
    d.reports.append(Report("Group Delay", "Rectangular", "Setup1 : Sweep", ["GroupDelay(2,1)"]))
    d.reports.append(Report("Phase", "Rectangular", "Setup1 : Sweep", ["ang_deg(S(2,1))", "ang_rad(S(1,1))"]))
    d.reports.append(Report("Smith", "Smith Chart", "Setup1 : Sweep", ["S(1,1)", "S(2,2)"]))
    d.reports.append(Report("Iris Families", "Rectangular", "Setup1 : Sweep", ["dB(S(2,1))"],
                            {"families": "IrisWidth"}))
    field_plot(d, "E_MidHeight", "Mag_E", "Setup1 : LastAdaptive", plane="XY@b/2")
    field_plot(d, "H_Vector_MidHeight", "Vector_H", "Setup1 : LastAdaptive", plane="XY@b/2")
    field_plot(d, "J_Walls", "Mag_Jsurf", "Setup1 : LastAdaptive", faces=topw + botw)
    d.parametrics.append(ParametricSetup(
        "IrisWidth", sweeps=[{"variable": "w_ir", "type": "Linear step", "start": "10mm", "stop": "12mm",
                              "step": "0.5mm", "sync": ""}], setups=["Setup1"],
        calcs=[{"calc": "dB(S(2,1))", "solution": "Setup1 : Sweep", "func": "Max", "start": "9.5GHz",
                "stop": "10.5GHz"}]))
    return d


# ---- C: dielectric resonator in a cavity, eigenmode --------------------------------------------------------
def design_cavity(p: Project) -> Design:
    d = Design("C_DielectricResonator_Eigen")
    d.notes = ("Eigenmode solution: a cylindrical copper cavity with a ceramic puck on a faceted cone support "
               "and a ferrite tuning disc; finite conductivity walls, field overlays on cut planes.")
    d.settings["SolutionType"] = "Eigenmode"
    d.variables.update({"Rc": "20mm", "Hc": "24mm", "Rd": "8.6mm", "Hd": "7mm", "Hs": "6mm"})
    cav = d.add_object("CreateCylinder", name="Cavity", material="vacuum", CenterPosition="0,0,0", Axis="Z",
                       Radius="Rc", Height="Hc")
    cav.transparency = 0.8
    d.add_object("CreateCone", name="Support", material="polystyrene", CenterPosition="0,0,0", Axis="Z",
                 LowerRadius="Rd*0.8", UpperRadius="Rd*0.4", Height="Hs", NumberOfSegments=8)
    puck = d.add_object("CreateCylinder", name="Puck", material="Showcase_Anisotropic",
                        CenterPosition="0,0,Hs", Axis="Z", Radius="Rd", Height="Hd")
    hole = d.add_object("CreateCylinder", name="PuckHole", CenterPosition="0,0,Hs", Axis="Z", Radius="Rd/4",
                        Height="Hd")
    consume(d, puck, "Subtract", hole)
    disc = d.add_object("CreateCylinder", name="TuningDisc", material="Showcase_Ferrite",
                        CenterPosition="0,0,Hc-2mm", Axis="Z", Radius="Rd/2", Height="1mm")
    op(disc, "Move", Vector="0,0,-1mm")
    boundary(d, "Finite Conductivity", "CavityWalls", objects=["Cavity"], Material="copper")
    eigen_setup(d, "Eigen1", MinimumFrequency="3GHz", NumberOfModes=4, MaximumPasses=8, MaximumDeltaFreq=1,
                MinimumConvergedPasses=2, LambdaTarget="0.3333")
    field_plot(d, "E_Mid", "Mag_E", "Eigen1 : LastAdaptive", plane="XY@Hs+Hd/2")
    field_plot(d, "H_Side", "Mag_H", "Eigen1 : LastAdaptive", plane="XZ@0")
    return d


# ---- D: geometry zoo, illuminated by a plane wave ---------------------------------------------------------
def design_geometry_zoo(p: Project) -> Design:
    d = Design("D_GeometryZoo_RCS")
    d.notes = ("Every create command and every operation of the modeler on a grid of objects (pitch 'sp'), "
               "inside an explicit Region with a Radiation boundary, illuminated by an incident plane wave "
               "(monostatic and bistatic RCS). Also: groups, an object in a relative coordinate system, a "
               "suppressed history command, hidden / wireframe / non-model objects and project materials "
               "(Debye, anisotropic, ferrite, brass, project PEC).")
    d.variables.update({"sp": "40mm", "s": "12mm"})
    cs(d, "CS_Zoo", Origin="0,0,-2*sp", XAxis="cos(20deg),sin(20deg),0", YPoint="-sin(20deg),cos(20deg),0")
    pos = {}

    def at(i, j, z="0"):
        return f"({i})*sp,({j})*sp,{z}"

    # row 0: primitives
    pos["box"] = d.add_object("CreateBox", name="Box_ChamferFillet", material="Showcase_PEC",
                              Position=at(-2, -2), XSize="s", YSize="s", ZSize="s")
    op(pos["box"], "Chamfer", Distance="1.5mm", Edges="0")
    op(pos["box"], "Fillet", Radius="1mm", Edges="5")
    d.add_object("CreateCylinder", name="Cyl_Faceted", material="Showcase_Brass", CenterPosition=at(-1, -2),
                 Axis="X", Radius="s/2", Height="s", NumberOfSegments=10)
    d.add_object("CreateSphere", name="Sphere_True", material="pec", CenterPosition=at(0, -2), Radius="s/2")
    d.add_object("CreateSphere", name="Sphere_Icosa", material="pec", CenterPosition=at(1, -2), Radius="s/2",
                 NumberOfVertices=12)
    d.add_object("CreateCone", name="Cone", material="Showcase_Debye_Absorber", CenterPosition=at(2, -2),
                 Axis="Y", LowerRadius="s/2", UpperRadius="s/6", Height="s")
    # row 1
    d.add_object("CreateTorus", name="Torus", material="pec", CenterPosition=at(-2, -1), Axis="Z",
                 MajorRadius="s/2", MinorRadius="s/6")
    d.add_object("CreateRegularPolyhedron", name="Hexagonal_Prism", material="PTFE_glass",
                 CenterPosition=at(-1, -1), StartPosition="(-1)*sp+s/2,(-1)*sp,0", Height="s", NumberOfSides=6,
                 Axis="Z")
    sh = d.add_object("CreateEllipse", name="Ellipse_Thick", material="pec", CenterPosition=at(0, -1), Axis="Z",
                      MajorRadius="s/2", Ratio="0.6")
    op(sh, "ThickenSheet", Thickness="2mm", BothSides="True")
    pg = d.add_object("CreateRegularPolygon", name="Pentagon_Extruded", material="Showcase_Ferrite",
                      CenterPosition=at(1, -1), StartPosition="(1)*sp+s/2,(-1)*sp,0", NumberOfSides=5, Axis="Z")
    op(pg, "SweepAlongVector", Vector="0,0,s/2")
    ring = d.add_object("CreateRectangle", name="Ring_Revolved", material="pec", Position="(2)*sp+s/4,(-1)*sp,0",
                        Axis="Y", XSize="s/4", ZSize="s/4")
    op(ring, "SweepAroundAxis", Axis="Z", Angle="270deg", Center=at(2, -1))
    # row 2: polylines, helix, spiral, bondwire
    pl = d.add_object("CreatePolyline", name="Polyline_Mixed", material="pec",
                      Points=";".join([at(-2, 0), "(-2)*sp+s,(0)*sp,0", "(-2)*sp+s+s/3,(0)*sp+s/2,0",
                                       "(-2)*sp+s,(0)*sp+s,0", "(-2)*sp+s/2,(0)*sp+s+s/4,0",
                                       "(-2)*sp,(0)*sp+s,0"]),
                      SegmentTypes="Line,Arc,Spline:3", Closed="True", Covered="True")
    op(pl, "ThickenSheet", Thickness="1mm")
    loop = d.add_object("CreatePolyline", name="Loop_Covered", material="pec",
                        Points="(-1)*sp,0,0;(-1)*sp+s,0,0;(-1)*sp+s,s,0;(-1)*sp,s,0",
                        SegmentTypes="Line,AngularArc:90deg:XY", Closed="True")
    op(loop, "Cover")
    d.add_object("CreateHelix", name="Helix_Wire", material="Showcase_PEC", CenterPosition=at(0, 0), Axis="Z",
                 Radius="s/3", Pitch="4mm", Turns="3", RightHanded="True", WireRadius="0.6mm")
    d.add_object("CreateSpiral", name="Spiral_Trace", material="pec", CenterPosition=at(1, 0), Axis="Z",
                 InnerRadius="2mm", Pitch="2mm", Turns="2.5", RightHanded="False", Width="0.8mm")
    d.add_object("CreateBondwire", name="Bondwire", material="pec", Type="JEDEC 5-Point",
                 StartPosition=at(2, 0), EndPosition="(2)*sp+s,0,0", H1="3mm", H2="0.5mm", Diameter="0.5mm")
    # row 3: booleans and two-object operations
    u = d.add_object("CreateBox", name="Unite_Result", material="pec", Position=at(-2, 1), XSize="s",
                     YSize="s/3", ZSize="s/3")
    u2 = d.add_object("CreateCylinder", name="UniteTool", CenterPosition="(-2)*sp+s/2,(1)*sp,s/6", Axis="Y",
                      Radius="s/4", Height="s")
    consume(d, u, "Unite", u2)
    sb = d.add_object("CreateBox", name="Subtract_KeepOriginal", material="Showcase_Brass", Position=at(-1, 1),
                      XSize="s", YSize="s", ZSize="s/2")
    sbt = d.add_object("CreateSphere", name="SubtractTool", material="PLA", CenterPosition="(-1)*sp+s,(1)*sp+s,s/2",
                       Radius="s/2")
    clone = Command.new("CreateSphere", CenterPosition="(-1)*sp+s,(1)*sp+s,s/2", Radius="s/2")
    tool_clone = d.add_object("CreateSphere", name="SubtractTool_tool", **clone.props)
    tool_clone.consumed_by = sb.name
    op(sb, "Subtract", Tools=tool_clone.name, KeepOriginals="True")
    sbt.transparency = 0.6
    sbt.wireframe = True
    it = d.add_object("CreateSphere", name="Intersect_Lens", material="Showcase_Anisotropic",
                      CenterPosition="(0)*sp,(1)*sp,-s/3", Radius="s/2")
    it2 = d.add_object("CreateSphere", name="IntersectTool", CenterPosition="(0)*sp,(1)*sp,s/3", Radius="s/2")
    consume(d, it, "Intersect", it2)
    cyl = d.add_object("CreateCircle", name="Swept_Tube", material="pec", CenterPosition=at(1, 1), Axis="Y",
                       Radius="1.2mm")
    path = d.add_object("CreatePolyline", name="SweepPath",
                        Points="(1)*sp,(1)*sp,0;(1)*sp,(1)*sp+s/2,s/3;(1)*sp+s/2,(1)*sp+s,s/2",
                        SegmentTypes="Spline:3")
    consume(d, cyl, "SweepAlongPath", path, Trihedron="CorrectedFrenet")
    c1 = d.add_object("CreateCircle", name="Loft_Connect", material="pec", CenterPosition=at(2, 1), Axis="Z",
                      Radius="s/2")
    c2 = d.add_object("CreateRectangle", name="LoftTop", Position="(2)*sp-s/4,(1)*sp-s/4,s", Axis="Z",
                      XSize="s/2", YSize="s/2")
    consume(d, c1, "Connect", c2, Ruled="False")
    # row 4: face operations, split, section, transforms
    imp = d.add_object("CreateBox", name="Imprinted_Box", material="silicon", Position=at(-2, 2), XSize="s",
                       YSize="s", ZSize="s/4")
    pad = d.add_object("CreateRectangle", name="ImprintPad", Position="(-2)*sp+s/4,(2)*sp+s/4,s/4", Axis="Z",
                       XSize="s/2", YSize="s/2")
    consume(d, imp, "Imprint", pad)
    shell = d.add_object("CreateBox", name="OpenShell_Uncover", material="pec", Position=at(-1, 2), XSize="s",
                         YSize="s", ZSize="s/2")
    op(shell, "UncoverFaces", Faces="5")
    kf = d.add_object("CreateBox", name="KeepFaces_L", material="pec", Position=at(0, 2), XSize="s", YSize="s",
                      ZSize="s/2")
    op(kf, "KeepFaces", Faces="0,2")
    sw = d.add_object("CreateBox", name="SweepFaces", material="glass", Position=at(1, 2), XSize="s/2", YSize="s/2",
                      ZSize="s/4")
    op(sw, "SweepFacesAlongNormal", Faces="5", Distance="3mm")
    sp_ = d.add_object("CreateSphere", name="Split_Hemisphere", material="pec", CenterPosition=at(2, 2),
                       Radius="s/2")
    op(sp_, "Split", Plane="XY", Keep="Positive")
    two = d.add_object("CreateBox", name="KeepBody_Part", material="pec", Position=at(-2, 3), XSize="s",
                       YSize="s/4", ZSize="s/4")
    cutter = d.add_object("CreateBox", name="KeepBodyCutter", Position="(-2)*sp+s/2-1mm,(3)*sp-1mm,-1mm",
                          XSize="2mm", YSize="s/4+2mm", ZSize="s/4+2mm")
    consume(d, two, "Subtract", cutter)
    op(two, "KeepBody", Body="1")
    sec = d.add_object("CreateSphere", name="Section_Disc", material="pec", CenterPosition=at(-1, 3), Radius="s/2")
    op(sec, "Section", Base=at(-1, 3), Normal="0,1,1")
    tr = d.add_object("CreateBox", name="Transformed", material="pec", Position=at(0, 3), XSize="s/2", YSize="s/4",
                      ZSize="s/6")
    op(tr, "Move", Vector="2mm,0,0")
    op(tr, "Rotate", Axis="Z", Angle="30deg", Center=at(0, 3))
    op(tr, "Mirror", Base=at(0, 3), Normal="1,0,0")
    op(tr, "Scale", Factor="1.2", Center=at(0, 3))
    op(tr, "Move", Vector="0,0,50mm").suppressed = True
    # in a relative coordinate system, grouped with a hidden and a non-model object
    rel = d.add_object("CreateBox", name="InRelativeCS", material="pec", Position="-s/2,-s/2,0", XSize="s",
                       YSize="s/3", ZSize="s/5")
    rel.orientation = "CS_Zoo"
    ghost = d.add_object("CreateSphere", name="HiddenSphere", material="pec", CenterPosition=at(1, 3),
                         Radius="s/4")
    ghost.visible = False
    note = d.add_object("CreateCylinder", name="NonModel_Marker", material="nylon", CenterPosition=at(2, 3),
                        Radius="s/4", Height="s", Axis="Z")
    note.model = False
    note.wireframe = True
    for o in (rel, ghost, note):
        o.group = "Zoo_Group"
    # region and open boundary
    reg = d.add_object("CreateRegion", name="Region", PaddingType="Absolute Offset", **{
        "+X": "40mm", "-X": "40mm", "+Y": "40mm", "-Y": "40mm", "+Z": "40mm", "-Z": "40mm"})
    boundary(d, "Radiation", "Rad_Region", objects=[reg.name], Order="First order")
    excitation(d, "Incident Plane Wave", "PlaneWave", VectorInput="Spherical", Theta="45deg", Phi="30deg",
               Polarization="90deg", ZeroPhasePosition="0,0,0", EMagnitude="1")
    excitation(d, "Incident Plane Wave", "PlaneWave_Cartesian", VectorInput="Cartesian", E0Vector="0,1,0",
               KVector="0,0,-1").enabled = False
    s = setup(d, "Setup1", Frequency="1.5GHz", MaximumPasses=6, MaximumDeltaEnergy=0.002)
    sweep(s, "RCS_Sweep", SweepType="Discrete", Distribution="Linear Count", Start="1GHz", Stop="2GHz", Count=5,
          SaveFields="True")
    sphere(d, "FF_RCS", PhiStep="10deg", ThetaStep="5deg")
    d.reports.append(Report("Bistatic RCS", "Radiation Pattern", "Setup1 : LastAdaptive", ["dBsm(BistaticRCSTotal)"],
                            {"cut": "Phi", "cut_value": "30", "plot": "Rectangular", "sphere": "FF_RCS"}))
    d.reports.append(Report("Bistatic RCS 3D", "3D Polar Plot", "Setup1 : LastAdaptive", ["dBsm(BistaticRCSTotal)"],
                            {"sphere": "FF_RCS"}))
    d.reports.append(Report("Monostatic RCS", "Monostatic RCS", "Setup1 : RCS_Sweep", ["dBsm(MonostaticRCSTotal)"]))
    d.reports.append(Report("Monostatic RCS Table", "Monostatic RCS Table", "Setup1 : AdaptivePasses",
                            ["dBsm(MonostaticRCSTotal)"]))
    field_plot(d, "E_Total_XY", "Mag_E", "Setup1 : LastAdaptive", plane="XY@s/4")
    field_plot(d, "J_Torus", "Mag_Jsurf", "Setup1 : LastAdaptive", objects=["Torus", "Helix_Wire"])
    field_plot(d, "J_Volume_Ferrite", "Mag_J", "Setup1 : LastAdaptive", objects=["Pentagon_Extruded"])
    return d


# ---- E: coax-fed monopole with PML, terminal solution ------------------------------------------------------
def design_monopole(p: Project) -> Design:
    d = Design("E_CoaxMonopole_PML")
    d.units = "cm"
    d.notes = ("Terminal solution type, units cm: a quarter-wave monopole fed through the ground disc by a 50 ohm "
               "PTFE coax (the inner conductor continues as the monopole, the outer wall is a Perfect E face) "
               "with an annular wave port; explicit air box with PML on its five open faces, a Perfect E infinite "
               "ground plane, Length Based On Edges mesh refinement and a far-field sphere.")
    d.settings["SolutionType"] = "Terminal"
    d.variables.update({"Lm": "c0/f_mono/4*0.95", "rw": "0.065cm", "ro": "rw*exp(50ohm*sqrt(2.1)/60ohm)",
                        "Lc": "1cm", "Rg": "8cm"})
    p.variables.setdefault("f_mono", "1GHz")
    # inner conductor and monopole in one piece, from the port up to the tip
    d.add_object("CreateCylinder", name="Monopole", material="copper", CenterPosition="0,0,-Lc", Axis="Z",
                 Radius="rw", Height="Lc+Lm")
    gnd = d.add_object("CreateCircle", name="GroundDisc", CenterPosition="0,0,0", Axis="Z", Radius="Rg")
    hole = d.add_object("CreateCircle", name="GroundHole", CenterPosition="0,0,0", Axis="Z", Radius="ro")
    consume(d, gnd, "Subtract", hole)
    coax = d.add_object("CreateCylinder", name="CoaxDielectric", material="Teflon (tm)",
                        CenterPosition="0,0,-Lc", Axis="Z", Radius="ro", Height="Lc")
    coax.transparency = 0.5
    port = d.add_object("CreateCircle", name="CoaxPort", CenterPosition="0,0,-Lc", Axis="Z", Radius="ro")
    pin = d.add_object("CreateCircle", name="CoaxPortPin", CenterPosition="0,0,-Lc", Axis="Z", Radius="rw")
    consume(d, port, "Subtract", pin)
    air = d.add_object("CreateBox", name="Air", material="air", Position="-Rg-2cm,-Rg-2cm,0",
                       XSize="2*Rg+4cm", YSize="2*Rg+4cm", ZSize="Lm+6cm")
    air.transparency = 0.9
    boundary(d, "Perfect E", "GroundPlane", objects=["GroundDisc"], InfGroundPlane="True")
    excitation(d, "Wave Port", "Feed", objects=[port.name], PortNumber=1, ModeType="TEM")
    b = Built(p, d)
    outer = b.faces("Air", lambda c, n: c[2] > 1e-6)     # all faces of the air box above the ground plane
    wall = b.faces("CoaxDielectric", lambda c, n: abs(n[2]) < 0.5)    # the round outer wall
    boundary(d, "Perfect E", "CoaxOuterWall", faces=wall)
    boundary(d, "PML", "PML_Air", faces=outer, Thickness="5cm", Layers=3, Exponent=2, MaxAttenuation=8)
    mesh_op(d, "Length Based On Edges", "Mesh_MonopoleEdges", edges=[FaceRef("Monopole", 0), FaceRef("Monopole", 2)],
            MaxLength="0.3cm")
    s = setup(d, "Setup1", Frequency="f_mono", MaximumPasses=8, MaximumDeltaS=0.02)
    sweep(s, "Sweep", SweepType="Fast", Distribution="Linear Count", Start="0.7GHz", Stop="1.3GHz", Count=121)
    sphere(d, "FF", ThetaStop="90deg")
    d.reports.append(Report("Terminal S11", "Rectangular", "Setup1 : Sweep", ["dB(S(1,1))", "re(Z(1,1))",
                                                                             "im(Z(1,1))"]))
    d.reports.append(Report("Elevation Pattern", "Radiation Pattern", "Setup1 : LastAdaptive", ["dB(DirTotal)"],
                            {"cut": "Phi", "cut_value": "0", "sphere": "FF"}))
    field_plot(d, "E_XZ", "Mag_E", "Setup1 : LastAdaptive", plane="XZ@0")
    return d


# ---- F: periodic unit cell with a Floquet port ---------------------------------------------------------
def design_unit_cell(p: Project) -> Design:
    d = Design("F_UnitCell_Periodic")
    d.notes = ("Periodic unit cell (the infinite array) of a cross on a grounded RO3003 spacer: Primary / "
               "Secondary pairs in x and y with the scan angles theta = scan_theta, phi = 0 and a Floquet port "
               "with TE00 and TM00 above. The ground reflects everything; the result is the reflection phase "
               "over frequency (a reflectarray element).")
    d.variables.update({"px": "15mm", "py": "15mm", "hz": "30mm", "ls": "11mm", "ws": "1.5mm", "scan_theta": "0deg"})
    cell = d.add_object("CreateBox", name="Cell", material="vacuum", Position="-px/2,-py/2,0", XSize="px",
                        YSize="py", ZSize="hz")
    cell.transparency = 0.85
    d.add_object("CreateBox", name="Spacer", material="Rogers RO3003 (tm)", Position="-px/2,-py/2,0", XSize="px",
                 YSize="py", ZSize="1.52mm")
    a = d.add_object("CreateRectangle", name="CrossA", material="pec", Position="-ls/2,-ws/2,1.52mm", Axis="Z",
                     XSize="ls", YSize="ws")
    bb = d.add_object("CreateRectangle", name="CrossB", Position="-ws/2,-ls/2,1.52mm", Axis="Z", XSize="ws",
                      YSize="ls")
    consume(d, a, "Unite", bb)
    boundary(d, "Perfect E", "CrossPEC", objects=["CrossA"])
    b = Built(p, d)
    f = lambda ax, s: b.faces("Cell", lambda c, n: n[ax] * s > 0.99)   # noqa: E731
    boundary(d, "Primary", "Master_X", faces=f(0, -1), UVector="-px/2,-py/2,0;-px/2,py/2,0")
    boundary(d, "Secondary", "Slave_X", faces=f(0, 1), Primary="Master_X", UVector="px/2,-py/2,0;px/2,py/2,0",
             PhaseMode="Scan Angles", ScanTheta="scan_theta", ScanPhi="0deg")
    boundary(d, "Primary", "Master_Y", faces=f(1, -1), UVector="-px/2,-py/2,0;px/2,-py/2,0")
    boundary(d, "Secondary", "Slave_Y", faces=f(1, 1), Primary="Master_Y", UVector="-px/2,py/2,0;px/2,py/2,0",
             PhaseMode="Scan Angles", ScanTheta="scan_theta", ScanPhi="0deg")
    boundary(d, "Perfect E", "Backplane", faces=f(2, -1))
    excitation(d, "Floquet Port", "FP1", faces=f(2, 1), PortNumber=1, NumberOfModes=2, Permittivity="1")
    s = setup(d, "Setup1", Frequency="10GHz", MaximumPasses=6)
    sweep(s, "Sweep", SweepType="Interpolating", Distribution="Linear Count", Start="6GHz", Stop="14GHz", Count=161)
    # S indices: 1 = FP1:TE00, 2 = FP1:TM00
    d.reports.append(Report("Reflection Phase", "Rectangular", "Setup1 : Sweep", ["ang_deg(S(1,1))", "ang_deg(S(2,2))"]))
    d.reports.append(Report("Reflection", "Rectangular", "Setup1 : Sweep", ["dB(S(1,1))", "dB(S(2,1))"]))
    return d


def showcase() -> Project:
    p = Project("Showcase_AllFeatures", designs=[])
    p.variables.update({"f0": "2.45GHz", "er_lam": "3.55"})
    p.materials = project_materials()
    p.designs = [design_patch_array(p), design_waveguide_filter(p), design_cavity(p), design_geometry_zoo(p),
                 design_monopole(p), design_unit_cell(p)]
    return p


if __name__ == "__main__":
    pr = showcase()
    pr.save(OUT)
    print("wrote", OUT)
