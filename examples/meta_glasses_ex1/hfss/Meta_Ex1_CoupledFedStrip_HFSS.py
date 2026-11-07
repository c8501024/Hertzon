# -*- coding: utf-8 -*-
"""
Meta AI-glasses temple-arm antenna benchmark, Example 1 (IEEE AP Magazine, Oct. 2026, Industry Activities).
Final design: coupled-fed shorted strip, top layer of the 25 x 5 mm keep-out, no matching components.

Self-contained Ansys Electronics Desktop (IronPython) script. It builds the full model from scratch
(geometry, materials, boundaries, lumped port, setup, sweeps, far-field sphere), solves it, exports
S11 and antenna parameters (CSV) and the geometry (STEP), and saves the project.

GUI:   Tools > Run Script...  and pick this file.
Batch: ansysedt.exe -ng -RunScriptAndExit Meta_Ex1_CoupledFedStrip_HFSS.py

Output folder: OUT_DIR below (created if missing). Total efficiency = RadiatedPower / IncidentPower (1 W).
Tested with AEDT 2025 R1.
"""
import ScriptEnv, os
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")

OUT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Meta_Ex1_HFSS")  # change if needed
PROJECT_NAME = "Meta_Ex1_CoupledFedStrip"
RUN_ANALYSIS = True

# ---- design parameters (mm); x from the left PCB end, y from the bottom PCB edge, z = 0..1 PCB thickness ----
P = {
    # benchmark geometry (fixed by Meta)
    "pcb_l": 120.0, "pcb_w": 12.5, "pcb_t": 1.0,
    "ko_x": 20.5, "ko_y": 7.5, "ko_l": 25.0, "ko_w": 5.0,       # keep-out notch at the top edge
    # antenna (all on the top face, z = pcb_t)
    "xs": 21.0,   # shorting leg x
    "wt": 0.5,    # leg width
    "ya": 12.3,   # top edge of the shorted arm
    "wa": 1.5,    # shorted arm width
    "L1": 15.5,   # shorted arm length
    "gc": 0.4,    # gap arm / feed strip
    "w2": 1.2,    # feed strip width
    "xf": 32.0,   # feed x
    "gp": 0.5,    # port gap at the notch bottom
    "Lb": 2.0,    # feed strip left of the feed
    "L2": 6.0,    # feed strip right of the feed
}
y2 = P["ya"] - P["wa"] - P["gc"] - P["w2"]   # bottom of the feed strip (9.2 mm)
zt = P["pcb_t"]

if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)

oProject = oDesktop.NewProject()
oProject.InsertDesign("HFSS", "Ex1", "DrivenModal", "")
oDesign = oProject.SetActiveDesign("Ex1")
oEditor = oDesign.SetActiveEditor("3D Modeler")
oEditor.SetModelUnits(["NAME:Units Parameter", "Units:=", "mm", "Rescale:=", False])

def mm(v):
    return "%gmm" % v

def attrs(name, mat, solve=True, color="(143 175 143)", transp=0):
    return ["NAME:Attributes", "Name:=", name, "Flags:=", "", "Color:=", color, "Transparency:=", transp,
            "PartCoordinateSystem:=", "Global", "UDMId:=", "", "MaterialValue:=", "\"" + mat + "\"",
            "SurfaceMaterialValue:=", "\"\"", "SolveInside:=", solve, "IsMaterialEditable:=", True,
            "UseMaterialAppearance:=", False, "IsLightweight:=", False]

def box(name, x, y, z, dx, dy, dz, mat, solve=True, color="(143 175 143)"):
    oEditor.CreateBox(["NAME:BoxParameters", "XPosition:=", mm(x), "YPosition:=", mm(y), "ZPosition:=", mm(z),
                       "XSize:=", mm(dx), "YSize:=", mm(dy), "ZSize:=", mm(dz)], attrs(name, mat, solve, color))

def rect(name, x, y, dx, dy, color="(255 128 64)"):
    oEditor.CreateRectangle(["NAME:RectangleParameters", "IsCovered:=", True, "XStart:=", mm(x), "YStart:=", mm(y),
                             "ZStart:=", mm(zt), "Width:=", mm(dx), "Height:=", mm(dy), "WhichAxis:=", "Z"],
                            attrs(name, "vacuum", True, color))

def unite(names):
    oEditor.Unite(["NAME:Selections", "Selections:=", ",".join(names)], ["NAME:UniteParameters", "KeepOriginals:=", False])

# ---- materials ----
oProject.GetDefinitionManager().AddMaterial(["NAME:KeepOut_Dk3p7", "CoordinateSystemType:=", "Cartesian",
                                             "BulkOrSurfaceType:=", 1, ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
                                             "permittivity:=", "3.7", "dielectric_loss_tangent:=", "0.02"])

# ---- benchmark geometry: copper PCB with notch, keep-out dielectric ----
box("PCB", 0, 0, 0, P["pcb_l"], P["pcb_w"], P["pcb_t"], "copper", False, "(230 200 40)")
box("NotchCut", P["ko_x"], P["ko_y"], 0, P["ko_l"], P["ko_w"], P["pcb_t"], "vacuum")
oEditor.Subtract(["NAME:Selections", "Blank Parts:=", "PCB", "Tool Parts:=", "NotchCut"],
                 ["NAME:SubtractParameters", "KeepOriginals:=", False])
box("KeepOut", P["ko_x"], P["ko_y"], 0, P["ko_l"], P["ko_w"], P["pcb_t"], "KeepOut_Dk3p7", True, "(95 200 224)")

# ---- antenna: shorted arm (short at xs) + coupled feed strip (fed at xf) ----
rect("ShortedArm", P["xs"], P["ko_y"], P["wt"], P["ya"] - P["wa"] - P["ko_y"])
rect("ShortedArm_top", P["xs"], P["ya"] - P["wa"], P["L1"], P["wa"])
unite(["ShortedArm", "ShortedArm_top"])
rect("FeedStrip", P["xf"], P["ko_y"] + P["gp"], P["wt"], y2 - P["ko_y"] - P["gp"])
rect("FeedStrip_top", P["xf"] - P["Lb"], y2, P["Lb"] + P["L2"], P["w2"])
unite(["FeedStrip", "FeedStrip_top"])
rect("Port1", P["xf"], P["ko_y"], P["wt"], P["gp"], "(255 0 0)")

# ---- air region (32 mm ~ lambda/4 at 2.4 GHz) ----
pad = []
for ax in ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]:
    pad += [ax + "PaddingType:=", "Absolute Offset", ax + "Padding:=", "32mm"]
oEditor.CreateRegion(["NAME:RegionParameters"] + pad, attrs("Region", "air", True, "(143 175 143)", 0.9))

# ---- boundaries and port ----
oBnd = oDesign.GetModule("BoundarySetup")
oBnd.AssignFiniteCond(["NAME:AntennaCopper", "Objects:=", ["ShortedArm", "FeedStrip"], "UseMaterial:=", True,
                       "Material:=", "copper", "UseThickness:=", False, "Roughness:=", "0um",
                       "InfGroundPlane:=", False, "IsTwoSided:=", False, "IsInternal:=", True])
oBnd.AssignRadiation(["NAME:Rad1", "Objects:=", ["Region"], "IsFssReference:=", False, "IsForPML:=", False])
xc = P["xf"] + P["wt"] / 2.0
oBnd.AssignLumpedPort(["NAME:P1", "Objects:=", ["Port1"], "RenormalizeAllTerminals:=", True, "DoDeembed:=", False,
                       ["NAME:Modes", ["NAME:Mode1", "ModeNum:=", 1, "UseIntLine:=", True,
                                       ["NAME:IntLine", "Start:=", [mm(xc), mm(P["ko_y"]), mm(zt)],
                                        "End:=", [mm(xc), mm(P["ko_y"] + P["gp"]), mm(zt)]],
                                       "AlignmentGroup:=", 0, "CharImp:=", "Zpi", "RenormImp:=", "50ohm"]],
                       "ShowReporterFilter:=", False, "ReporterFilter:=", [True], "Impedance:=", "50ohm"])

# ---- setup: adaptive at both band centres, dS 0.01; S11 sweep and in-band discrete points with fields ----
oAna = oDesign.GetModule("AnalysisSetup")
oAna.InsertSetup("HfssDriven", ["NAME:Setup1", "SolveType:=", "MultiFrequency",
                                ["NAME:MultipleAdaptiveFreqsSetup", "2.45GHz:=", [0.01], "6.1GHz:=", [0.01]],
                                "MaximumPasses:=", 20, "MinimumPasses:=", 3, "MinimumConvergedPasses:=", 2,
                                "PercentRefinement:=", 30, "IsEnabled:=", True, "BasisOrder:=", 1,
                                "DoLambdaRefine:=", True, "DoMaterialLambda:=", True, "SetLambdaTarget:=", False])
oAna.InsertFrequencySweep("Setup1", ["NAME:S11", "IsEnabled:=", True, "RangeType:=", "LinearStep",
                                     "RangeStart:=", "1.5GHz", "RangeEnd:=", "8GHz", "RangeStep:=", "0.025GHz",
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

proj_path = os.path.join(OUT_DIR, PROJECT_NAME + ".aedt")
oProject.SaveAs(proj_path, True)

# geometry for other tools (PCB, keep-out and antenna sheets)
oEditor.Export(["NAME:ExportParameters", "AllowRegionDependentPartSelectionForPMLCreation:=", True,
                "AllowRegionSelectionForPMLCreation:=", True,
                "Selections:=", "PCB,KeepOut,ShortedArm,FeedStrip,Port1",
                "File Name:=", os.path.join(OUT_DIR, PROJECT_NAME + ".step"),
                "Major Version:=", -1, "Minor Version:=", -1])

if RUN_ANALYSIS:
    oDesign.AnalyzeAll()
    oProject.Save()
    oRep = oDesign.GetModule("ReportSetup")
    oRep.CreateReport("S11 dB", "Modal Solution Data", "Rectangular Plot", "Setup1 : S11", [], ["Freq:=", ["All"]],
                      ["X Component:=", "Freq", "Y Component:=", ["dB(S(P1,P1))"]], [])
    oRep.CreateReport("S11 table", "Modal Solution Data", "Data Table", "Setup1 : S11", [], ["Freq:=", ["All"]],
                      ["X Component:=", "Freq", "Y Component:=", ["re(S(P1,P1))", "im(S(P1,P1))", "dB(S(P1,P1))"]], [])
    oRep.ExportToFile("S11 table", os.path.join(OUT_DIR, "S11.csv"))
    oRep.CreateReport("Efficiency", "Antenna Parameters", "Data Table", "Setup1 : Eff", ["Context:=", "FF"],
                      ["Freq:=", ["All"]],
                      ["X Component:=", "Freq",
                       "Y Component:=", ["RadiatedPower", "AcceptedPower", "IncidentPower", "RadiationEfficiency"]], [])
    oRep.ExportToFile("Efficiency", os.path.join(OUT_DIR, "Efficiency.csv"))
    oProject.Save()
