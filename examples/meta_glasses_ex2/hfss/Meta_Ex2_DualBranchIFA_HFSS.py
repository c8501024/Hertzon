# -*- coding: utf-8 -*-
"""
Meta AI-glasses benchmark, Example 2, Run 2 final design (A22): independent Ansys HFSS check.

Uses Meta's original STEP for the complete environment (FPC, PCB, feed parts, chassis, port sheet) instead of the
simplified Hertzon primitives, and adds only the antenna sheet, the shorting pin and a 30 um bridge from the FPC to the
feed pin. Total efficiency = RadiatedPower / IncidentPower (1 W).

Batch: ansysedt.exe -ng -RunScriptAndExit Meta_Ex2_Run2_HFSS.py    Tested with AEDT 2025 R1.
"""
import ScriptEnv, os, traceback
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")

STP_FILE = r"C:\path\to\Example2.stp"  # Meta's benchmark STEP, not included here
OUT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Meta_Ex2_HFSS")  # change if needed
PROJECT_NAME = "Meta_Ex2_DualBranchIFA"
RUN_ANALYSIS = True

if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)
LOG = open(os.path.join(OUT_DIR, "script_log.txt"), "w")
def log(s):
    LOG.write(str(s) + "\n")
    LOG.flush()

oProject = oDesktop.NewProject()
oProject.InsertDesign("HFSS", "Ex2", "DrivenModal", "")
oDesign = oProject.SetActiveDesign("Ex2")
oEditor = oDesign.SetActiveEditor("3D Modeler")
oEditor.SetModelUnits(["NAME:Units Parameter", "Units:=", "mm", "Rescale:=", False])

def mm(v):
    return "%.7gmm" % v

def attrs(name, mat, solve=True, color="(255 128 64)", transp=0):
    return ["NAME:Attributes", "Name:=", name, "Flags:=", "", "Color:=", color, "Transparency:=", transp,
            "PartCoordinateSystem:=", "Global", "UDMId:=", "", "MaterialValue:=", "\"" + mat + "\"",
            "SurfaceMaterialValue:=", "\"\"", "SolveInside:=", solve, "IsMaterialEditable:=", True,
            "UseMaterialAppearance:=", False, "IsLightweight:=", False]

def rect(name, x0, x1, y0, y1, z=0.0):
    oEditor.CreateRectangle(["NAME:RectangleParameters", "IsCovered:=", True, "XStart:=", mm(x0), "YStart:=", mm(y0),
                             "ZStart:=", mm(z), "Width:=", mm(x1 - x0), "Height:=", mm(y1 - y0), "WhichAxis:=", "Z"],
                            attrs(name, "vacuum", True))

def cyl(name, x, y, z, r, h, mat="copper"):
    oEditor.CreateCylinder(["NAME:CylinderParameters", "XCenter:=", mm(x), "YCenter:=", mm(y), "ZCenter:=", mm(z),
                            "Radius:=", mm(r), "Height:=", mm(h), "WhichAxis:=", "Z", "NumSides:=", "0"],
                           attrs(name, mat, False, "(230 200 40)"))

def unite(names):
    oEditor.Unite(["NAME:Selections", "Selections:=", ",".join(names)], ["NAME:UniteParameters", "KeepOriginals:=", False])

def set_props(name, new_name, mat=None, solve=None):
    props = [["NAME:Name", "Value:=", new_name]]
    if mat is not None:
        props.append(["NAME:Material", "Value:=", "\"" + mat + "\""])
    if solve is not None:
        props.append(["NAME:Solve Inside", "Value:=", solve])
    oEditor.ChangeProperty(["NAME:AllTabs", ["NAME:Geometry3DAttributeTab", ["NAME:PropServers", name],
                            ["NAME:ChangedProps"] + props]])

try:
    oProject.GetDefinitionManager().AddMaterial(["NAME:FPC_substrate", "CoordinateSystemType:=", "Cartesian",
                                                 "BulkOrSurfaceType:=", 1, ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
                                                 "permittivity:=", "3.09", "dielectric_loss_tangent:=", "0.0105"])

    # ---- Meta's environment, all bodies and the port sheet ----
    oEditor.Import(["NAME:NativeBodyParameters", "HealOption:=", 0, "Options:=", "0", "CreateLightweightPart:=", False,
                    "ImportFreeSurfaces:=", True, "GroupByAssembly:=", False, "STLFileUnit:=", "Auto",
                    "MergeFacesAngle:=", -1, "PointCoincidenceTol:=", 1e-06, "CreateGroup:=", False,
                    "SourceFile:=", STP_FILE])
    names = list(oEditor.GetMatchedObjectName("*"))
    log("imported: %s" % names)

    def find(key, exclude=()):
        m = [n for n in names if key in n and not any(e in n for e in exclude)]
        if len(m) != 1:
            raise Exception("name match for %s: %s" % (key, m))
        return m[0]

    set_props(find("Extrude"), "FPC", "FPC_substrate", True)
    set_props(find("port1"), "Port1")
    ground = {"PCB": find("gnd_keepout"), "ART10": find("ART10"), "S51": find("solid5_1"), "S52": find("solid5_2"),
              "S6": find("solid6"), "S7": find("solid7"), "S8": find("solid8"), "S9": find("solid9")}
    for new, old in ground.items():
        set_props(old, new, "copper", False)
    set_props(find("solid5", ("solid5_1", "solid5_2")), "ChassisSteel", "steel_stainless", False)
    signal = {"Pad": find("02_TOP"), "Trace": find("28_BOTTOM"), "ViaTB": find("VIA_TOP"), "FeedPin": find("solid10")}
    for new, old in signal.items():
        set_props(old, new, "copper", False)

    # ---- connection of the feed pin to the antenna (pin ends 14-17 um above the FPC in the CAD) ----
    cyl("FeedBridge", 7.4864, 4.8921, 0.0, 0.3, 0.03)
    # ---- shorting pin from the antenna into the PCB ----
    cyl("GndPin", 8.5, 3.75, 0.0, 0.15, 1.35)

    # ---- antenna sheet on the FPC top face (final design A22) ----
    parts = [("A1", -3.0, 11.9, 6.15, 6.55), ("A2", 7.1864, 7.7864, 4.5921, 6.15), ("A3", 8.3, 8.7, 3.6, 6.15),
             ("A4", -3.0, -2.6, 3.6, 6.15), ("A5", -3.0, 4.9, 3.6, 4.0), ("A6", 4.5, 4.9, 3.6, 4.75),
             ("A7", -1.4, 4.9, 4.35, 4.75), ("A8", -1.4, -1.0, 4.35, 5.95), ("A9", -1.4, -0.6, 5.55, 5.95),
             ("A10", 1.05, 7.1964, 5.3, 5.7), ("A11", 11.5, 11.9, 4.35, 6.15), ("A12", 11.5, 14.3, 4.35, 4.73)]
    for p in parts:
        rect(*p)
    unite([p[0] for p in parts])
    set_props("A1", "Antenna")

    # ---- metal merged so that touching and overlapping CAD bodies are one conductor ----
    unite(["PCB", "ART10", "S51", "S52", "S6", "S7", "S8", "S9", "GndPin"])
    unite(["Pad", "Trace", "ViaTB", "FeedPin", "FeedBridge"])
    oEditor.Subtract(["NAME:Selections", "Blank Parts:=", "ChassisSteel", "Tool Parts:=", "PCB"],
                     ["NAME:SubtractParameters", "KeepOriginals:=", True])

    pad = []
    for ax in ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]:
        pad += [ax + "PaddingType:=", "Absolute Offset", ax + "Padding:=", "40mm"]
    oEditor.CreateRegion(["NAME:RegionParameters"] + pad, attrs("Region", "air", True, "(143 175 143)", 0.9))

    oBnd = oDesign.GetModule("BoundarySetup")
    oBnd.AssignFiniteCond(["NAME:AntennaCopper", "Objects:=", ["Antenna"], "UseMaterial:=", True,
                           "Material:=", "copper", "UseThickness:=", False, "Roughness:=", "0um",
                           "InfGroundPlane:=", False, "IsTwoSided:=", False, "IsInternal:=", True])
    oBnd.AssignRadiation(["NAME:Rad1", "Objects:=", ["Region"], "IsFssReference:=", False, "IsForPML:=", False])
    # integration line between the midpoints of the two conductor edges of Meta's port sheet (exact vertices)
    verts = [[float(c) for c in oEditor.GetVertexPosition(v)] for v in oEditor.GetVertexIDsFromObject("Port1")]
    verts.sort(key=lambda p: -p[1])
    log("port vertices: %s" % verts)
    a = [(verts[0][i] + verts[1][i]) / 2 for i in range(3)]
    b = [(verts[2][i] + verts[3][i]) / 2 for i in range(3)]
    oBnd.AssignLumpedPort(["NAME:P1", "Objects:=", ["Port1"], "RenormalizeAllTerminals:=", True, "DoDeembed:=", False,
                           ["NAME:Modes", ["NAME:Mode1", "ModeNum:=", 1, "UseIntLine:=", True,
                                           ["NAME:IntLine", "Start:=", ["%.12gmm" % c for c in a],
                                            "End:=", ["%.12gmm" % c for c in b]],
                                           "AlignmentGroup:=", 0, "CharImp:=", "Zpi", "RenormImp:=", "50ohm"]],
                           "ShowReporterFilter:=", False, "ReporterFilter:=", [True], "Impedance:=", "50ohm"])

    oAna = oDesign.GetModule("AnalysisSetup")
    oAna.InsertSetup("HfssDriven", ["NAME:Setup1", "SolveType:=", "MultiFrequency",
                                    ["NAME:MultipleAdaptiveFreqsSetup", "2.45GHz:=", [0.01], "6.1GHz:=", [0.01]],
                                    "MaximumPasses:=", 20, "MinimumPasses:=", 3, "MinimumConvergedPasses:=", 2,
                                    "PercentRefinement:=", 30, "IsEnabled:=", True, "BasisOrder:=", 1,
                                    "DoLambdaRefine:=", True, "DoMaterialLambda:=", True, "SetLambdaTarget:=", False])
    oAna.InsertFrequencySweep("Setup1", ["NAME:S11", "IsEnabled:=", True, "RangeType:=", "LinearStep",
                                         "RangeStart:=", "2GHz", "RangeEnd:=", "8GHz", "RangeStep:=", "0.01GHz",
                                         "Type:=", "Interpolating", "SaveFields:=", False, "InterpTolerance:=", 0.5,
                                         "InterpMaxSolns:=", 250, "InterpMinSolns:=", 0, "InterpMinSubranges:=", 1])
    pts = ["2.401GHz", "2.442GHz", "2.483GHz", "5.17GHz", "5.5GHz", "5.85GHz", "6.2GHz", "6.55GHz", "6.85GHz", "7.125GHz"]
    sweep = ["NAME:Eff", "IsEnabled:=", True, "SetupType:=", "SinglePoints", "Type:=", "Discrete", "SaveFields:=", True,
             "RangeType:=", "SinglePoints", "RangeStart:=", pts[0], "RangeEnd:=", pts[0], "SaveSingleField:=", False]
    sweep += [["NAME:SweepRanges"] + [["NAME:Subrange", "RangeType:=", "SinglePoints", "RangeStart:=", p,
                                       "RangeEnd:=", p, "SaveSingleField:=", True] for p in pts[1:]]]
    oAna.InsertFrequencySweep("Setup1", sweep)

    oDesign.GetModule("RadField").InsertInfiniteSphereSetup(
        ["NAME:FF", "UseCustomRadiationSurface:=", False, "ThetaStart:=", "0deg", "ThetaStop:=", "180deg",
         "ThetaStep:=", "3deg", "PhiStart:=", "0deg", "PhiStop:=", "360deg", "PhiStep:=", "3deg", "UseLocalCS:=", False])

    oProject.SaveAs(os.path.join(OUT_DIR, PROJECT_NAME + ".aedt"), True)
    log("model saved")
    log("validation: %s" % oDesign.ValidateDesign())

    if RUN_ANALYSIS:
        oDesign.AnalyzeAll()
        oProject.Save()
        log("analysis finished")
        oRep = oDesign.GetModule("ReportSetup")
        oRep.CreateReport("S11 table", "Modal Solution Data", "Data Table", "Setup1 : S11", [], ["Freq:=", ["All"]],
                          ["X Component:=", "Freq", "Y Component:=", ["re(S(P1,P1))", "im(S(P1,P1))", "dB(S(P1,P1))"]], [])
        oRep.ExportToFile("S11 table", os.path.join(OUT_DIR, "S11.csv"))
        oRep.CreateReport("Efficiency", "Antenna Parameters", "Data Table", "Setup1 : Eff", ["Context:=", "FF"],
                          ["Freq:=", ["All"]],
                          ["X Component:=", "Freq",
                           "Y Component:=", ["RadiatedPower", "AcceptedPower", "IncidentPower", "RadiationEfficiency"]], [])
        oRep.ExportToFile("Efficiency", os.path.join(OUT_DIR, "Efficiency.csv"))
        oProject.Save()
        log("exports done")
except Exception:
    log(traceback.format_exc())
    raise
finally:
    LOG.close()
