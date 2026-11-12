# -*- coding: utf-8 -*-
"""
Meta AI-glasses benchmark, Example 2, Run C: reference-solver check of the Hertzon design "T_opp".

IronPython script for the commercial reference solver. It imports PCB, FPC and chassis from the benchmark STEP file
(the same three bodies the Hertzon project uses; not Meta's original STEP), rebuilds antenna copper, vias, signal pad
and port from hfss_RunC.json (written by make_geo.py from tools/drc.py, next to this script), solves and exports S11
and antenna parameters (CSV).

Batch: optional HFSS_MESH (length seeding on the copper, default 0.3mm), HFSS_DELTA (default 0.02),
       HFSS_MINPASS (default 4), HFSS_TAG (suffix of the output folder)
       <solver executable> -ng -RunScriptAndExit Meta_Ex2_RunC_reference.py    Tested with version 2025 R1.
       Fine run in results/reference_fine: HFSS_DELTA=0.005, HFSS_MINPASS=9.
Total efficiency = RadiatedPower / IncidentPower.
"""
import ScriptEnv, os, json, traceback
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = "RunC"
STP_FILE = os.path.join(HERE, "..", "..", "runB", "input", "Ex2_Benchmark_geometry.step")
GEO = json.load(open(os.path.join(HERE, "hfss_%s.json" % CASE)))
OUT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Meta_Ex2_RunC_reference",
                       CASE + os.environ.get("HFSS_TAG", ""))  # change if needed
PROJECT_NAME = "Meta_Ex2_%s" % CASE
Z_PCB = 1.2
MESH_LEN = os.environ.get("HFSS_MESH", "0.3mm")
TAG = os.environ.get("HFSS_TAG", "")
DELTA = float(os.environ.get("HFSS_DELTA", "0.02"))
MINPASS = int(os.environ.get("HFSS_MINPASS", "4"))

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
    return "%.6gmm" % v

def attrs(name, mat, solve=True, color="(143 175 143)", transp=0):
    return ["NAME:Attributes", "Name:=", name, "Flags:=", "", "Color:=", color, "Transparency:=", transp,
            "PartCoordinateSystem:=", "Global", "UDMId:=", "", "MaterialValue:=", "\"" + mat + "\"",
            "SurfaceMaterialValue:=", "\"\"", "SolveInside:=", solve, "IsMaterialEditable:=", True,
            "UseMaterialAppearance:=", False, "IsLightweight:=", False]

def poly(name, xy, z, mat="vacuum", solve=True, color="(255 128 64)"):
    closed = list(xy) + [xy[0]]
    pts = [["NAME:PLPoint", "X:=", mm(x), "Y:=", mm(y), "Z:=", mm(z)] for x, y in closed]
    segs = [["NAME:PLSegment", "SegmentType:=", "Line", "StartIndex:=", i, "NoOfPoints:=", 2] for i in range(len(xy))]
    oEditor.CreatePolyline(["NAME:PolylineParameters", "IsPolylineCovered:=", True, "IsPolylineClosed:=", True,
                            ["NAME:PolylinePoints"] + pts, ["NAME:PolylineSegments"] + segs,
                            ["NAME:PolylineXSection", "XSectionType:=", "None", "XSectionOrient:=", "Auto",
                             "XSectionWidth:=", "0mm", "XSectionTopWidth:=", "0mm", "XSectionHeight:=", "0mm",
                             "XSectionNumSegments:=", "0", "XSectionBendType:=", "Corner"]],
                           attrs(name, mat, solve, color))

def setprops(obj, mat, solve):
    oEditor.ChangeProperty(["NAME:AllTabs", ["NAME:Geometry3DAttributeTab", ["NAME:PropServers", obj],
                            ["NAME:ChangedProps", ["NAME:Material", "Value:=", "\"" + mat + "\""],
                             ["NAME:Solve Inside", "Value:=", solve]]]])

try:
    oProject.GetDefinitionManager().AddMaterial(["NAME:FPC_substrate", "CoordinateSystemType:=", "Cartesian",
                                                 "BulkOrSurfaceType:=", 1, ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
                                                 "permittivity:=", "3.09", "dielectric_loss_tangent:=", "0.0105"])
    oProject.GetDefinitionManager().AddMaterial(["NAME:chassis_steel", "CoordinateSystemType:=", "Cartesian",
                                                 "BulkOrSurfaceType:=", 1, ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
                                                 "conductivity:=", "1100000"])
    # ---- environment: the three bodies of the benchmark package ----
    oEditor.Import(["NAME:NativeBodyParameters", "HealOption:=", 0, "Options:=", "0", "CreateLightweightPart:=", False,
                    "ImportFreeSurfaces:=", False, "GroupByAssembly:=", False, "STLFileUnit:=", "Auto",
                    "MergeFacesAngle:=", -1, "PointCoincidenceTol:=", 1e-06, "CreateGroup:=", False,
                    "SourceFile:=", STP_FILE])
    names = list(oEditor.GetMatchedObjectName("*"))
    log("imported: %s" % names)
    found = {}
    for n in names:
        for key in ("PCB", "FPC", "Chassis"):
            if key.lower() in n.lower():
                found[key] = n
    log("mapped: %s" % found)
    for key in ("PCB", "FPC", "Chassis"):
        bb = oEditor.GetModelBoundingBox() if False else None
    setprops(found["PCB"], "copper", False)
    setprops(found["FPC"], "FPC_substrate", True)
    setprops(found["Chassis"], "chassis_steel", False)

    # ---- antenna copper on the FPC top face (z = 0) ----
    cu = []
    for i, rings in enumerate(GEO["copper"]):
        n = "Antenna%d" % (i + 1)
        poly(n, rings[0], 0.0)
        cu.append(n)
    # ---- vias: copper cylinders from the FPC top to the PCB bottom ----
    vias = [("GndVia%d" % (i + 1), x, y) for i, (x, y) in enumerate(GEO["gvias"])] + [("FeedVia", GEO["feed"][0], GEO["feed"][1])]
    for n, x, y in vias:
        oEditor.CreateCylinder(["NAME:CylinderParameters", "XCenter:=", mm(x), "YCenter:=", mm(y), "ZCenter:=", "0mm",
                                "Radius:=", mm(GEO["via_d"] / 2.0), "Height:=", mm(Z_PCB), "WhichAxis:=", "Z",
                                "NumSides:=", "0"], attrs(n, "copper", False, "(230 200 40)"))
    # ---- signal pad in the plane of the PCB bottom and the port between PCB edge and pad ----
    poly("Pad", GEO["pad"], Z_PCB)
    cu.append("Pad")
    poly("Port1", GEO["port"], Z_PCB, color="(255 0 0)")

    pad = []
    for ax in ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]:
        pad += [ax + "PaddingType:=", "Absolute Offset", ax + "Padding:=", "40mm"]
    oEditor.CreateRegion(["NAME:RegionParameters"] + pad, attrs("Region", "air", True, "(143 175 143)", 0.9))

    oBnd = oDesign.GetModule("BoundarySetup")
    oBnd.AssignFiniteCond(["NAME:AntennaCopper", "Objects:=", cu, "UseMaterial:=", True,
                           "Material:=", "copper", "UseThickness:=", False, "Roughness:=", "0um",
                           "InfGroundPlane:=", False, "IsTwoSided:=", False, "IsInternal:=", True])
    oBnd.AssignRadiation(["NAME:Rad1", "Objects:=", ["Region"], "IsFssReference:=", False, "IsForPML:=", False])
    (a, b) = GEO["intline"]
    oBnd.AssignLumpedPort(["NAME:P1", "Objects:=", ["Port1"], "RenormalizeAllTerminals:=", True, "DoDeembed:=", False,
                           ["NAME:Modes", ["NAME:Mode1", "ModeNum:=", 1, "UseIntLine:=", True,
                                           ["NAME:IntLine", "Start:=", [mm(a[0]), mm(a[1]), mm(Z_PCB)],
                                            "End:=", [mm(b[0]), mm(b[1]), mm(Z_PCB)]],
                                           "AlignmentGroup:=", 0, "CharImp:=", "Zpi", "RenormImp:=", "50ohm"]],
                           "ShowReporterFilter:=", False, "ReporterFilter:=", [True], "Impedance:=", "50ohm"])

    # smallest gap and trace width are 0.3 mm: a moderate local length restriction on the copper only
    oMesh = oDesign.GetModule("MeshSetup")
    oMesh.AssignLengthOp(["NAME:CopperLength", "RefineInside:=", False, "Enabled:=", True, "Objects:=", cu,
                          "RestrictElem:=", False, "NumMaxElem:=", "1000", "RestrictLength:=", True,
                          "MaxLength:=", MESH_LEN])
    oAna = oDesign.GetModule("AnalysisSetup")
    oAna.InsertSetup("HfssDriven", ["NAME:Setup1", "SolveType:=", "MultiFrequency",
                                    ["NAME:MultipleAdaptiveFreqsSetup", "2.44GHz:=", [DELTA], "5.4GHz:=", [DELTA], "6.8GHz:=", [DELTA]],
                                    "MaximumPasses:=", 16, "MinimumPasses:=", MINPASS, "MinimumConvergedPasses:=", 2,
                                    "PercentRefinement:=", 30, "IsEnabled:=", True, "BasisOrder:=", 1,
                                    "DoLambdaRefine:=", True, "DoMaterialLambda:=", True, "SetLambdaTarget:=", False])
    oAna.InsertFrequencySweep("Setup1", ["NAME:S11", "IsEnabled:=", True, "RangeType:=", "LinearStep",
                                         "RangeStart:=", "2GHz", "RangeEnd:=", "8GHz", "RangeStep:=", "0.01GHz",
                                         "Type:=", "Interpolating", "SaveFields:=", False, "InterpTolerance:=", 0.5,
                                         "InterpMaxSolns:=", 250, "InterpMinSolns:=", 0, "InterpMinSubranges:=", 1])
    pts = ["2.401GHz", "2.42GHz", "2.44GHz", "2.46GHz", "2.483GHz", "5.17GHz"]
    pts += ["%.2fGHz" % (5.27 + 0.1 * k) for k in range(19)] + ["7.125GHz"]
    sweep = ["NAME:Eff", "IsEnabled:=", True, "SetupType:=", "SinglePoints", "Type:=", "Discrete", "SaveFields:=", True,
             "RangeType:=", "SinglePoints", "RangeStart:=", pts[0], "RangeEnd:=", pts[0], "SaveSingleField:=", False]
    sweep += [["NAME:SweepRanges"] + [["NAME:Subrange", "RangeType:=", "SinglePoints", "RangeStart:=", p,
                                       "RangeEnd:=", p, "SaveSingleField:=", True] for p in pts[1:]]]
    oAna.InsertFrequencySweep("Setup1", sweep)
    oDesign.GetModule("RadField").InsertInfiniteSphereSetup(
        ["NAME:FF", "UseCustomRadiationSurface:=", False, "ThetaStart:=", "0deg", "ThetaStop:=", "180deg",
         "ThetaStep:=", "3deg", "PhiStart:=", "0deg", "PhiStop:=", "360deg", "PhiStep:=", "3deg", "UseLocalCS:=", False])

    oProject.SaveAs(os.path.join(OUT_DIR, PROJECT_NAME + ".aedt"), True)
    log("model saved; validation: %s" % oDesign.ValidateDesign())
    oDesign.AnalyzeAll()
    oProject.Save()
    log("analysis finished")
    oRep = oDesign.GetModule("ReportSetup")
    oRep.CreateReport("S11 table", "Modal Solution Data", "Data Table", "Setup1 : S11", [], ["Freq:=", ["All"]],
                      ["X Component:=", "Freq", "Y Component:=", ["re(S(P1,P1))", "im(S(P1,P1))", "dB(S(P1,P1))"]], [])
    oRep.ExportToFile("S11 table", os.path.join(OUT_DIR, "S11.csv"))
    oRep.CreateReport("S11 points", "Modal Solution Data", "Data Table", "Setup1 : Eff", [], ["Freq:=", ["All"]],
                      ["X Component:=", "Freq", "Y Component:=", ["re(S(P1,P1))", "im(S(P1,P1))", "dB(S(P1,P1))"]], [])
    oRep.ExportToFile("S11 points", os.path.join(OUT_DIR, "S11_points.csv"))
    oRep.CreateReport("Efficiency", "Antenna Parameters", "Data Table", "Setup1 : Eff", ["Context:=", "FF"],
                      ["Freq:=", ["All"]],
                      ["X Component:=", "Freq",
                       "Y Component:=", ["RadiatedPower", "AcceptedPower", "IncidentPower", "RadiationEfficiency"]], [])
    oRep.ExportToFile("Efficiency", os.path.join(OUT_DIR, "Efficiency.csv"))
    try:
        oRep.CreateReport("Convergence", "Modal Solution Data", "Data Table", "Setup1 : AdaptivePass", [],
                          ["Pass:=", ["All"], "Freq:=", ["All"]],
                          ["X Component:=", "Pass", "Y Component:=", ["dB(S(P1,P1))"]], [])
        oRep.ExportToFile("Convergence", os.path.join(OUT_DIR, "Convergence.csv"))
    except Exception:
        log("convergence report failed: " + traceback.format_exc())
    oProject.Save()
    log("exports done")
except Exception:
    log(traceback.format_exc())
    raise
finally:
    LOG.close()

