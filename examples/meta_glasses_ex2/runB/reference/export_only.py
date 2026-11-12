# -*- coding: utf-8 -*-
# Open a solved project of Meta_Ex2_RunB_reference.py and export what exists (used after stopping the S11 sweep).
#   set HFSS_CASE=B3_final ; <solver executable> -ng -RunScriptAndExit export_only.py
import ScriptEnv, os, traceback
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
HERE = os.path.join(os.path.expanduser("~"), "Documents", "Meta_Ex2_RunB_reference")  # OUT_DIR base of Meta_Ex2_RunB_reference.py
CASE = os.environ.get("HFSS_CASE", "B3_final")
OUT = os.path.join(HERE, CASE)
LOG = open(os.path.join(OUT, "export_log.txt"), "w")
def log(s):
    LOG.write(str(s) + "\n"); LOG.flush()
try:
    lock = os.path.join(OUT, "Meta_Ex2_RunB_%s.aedt.lock" % CASE)
    if os.path.exists(lock):
        os.remove(lock)
    oProject = oDesktop.OpenProject(os.path.join(OUT, "Meta_Ex2_RunB_%s.aedt" % CASE))
    oDesign = oProject.SetActiveDesign("Ex2")
    oRep = oDesign.GetModule("ReportSetup")
    jobs = [("S11 points", "Modal Solution Data", "Setup1 : Eff", [], ["Freq:=", ["All"]],
             ["re(S(P1,P1))", "im(S(P1,P1))", "dB(S(P1,P1))"], "S11_points.csv", "Freq"),
            ("Efficiency", "Antenna Parameters", "Setup1 : Eff", ["Context:=", "FF"], ["Freq:=", ["All"]],
             ["RadiatedPower", "AcceptedPower", "IncidentPower", "RadiationEfficiency"], "Efficiency.csv", "Freq"),
            ("Convergence", "Modal Solution Data", "Setup1 : AdaptivePass", [], ["Pass:=", ["All"], "Freq:=", ["All"]],
             ["dB(S(P1,P1))"], "Convergence.csv", "Pass"),
            ("S11 table", "Modal Solution Data", "Setup1 : S11", [], ["Freq:=", ["All"]],
             ["re(S(P1,P1))", "im(S(P1,P1))", "dB(S(P1,P1))"], "S11.csv", "Freq")]
    for name, typ, sol, ctx, fam, ys, fn, xc in jobs:
        try:
            oRep.CreateReport(name, typ, "Data Table", sol, ctx, fam, ["X Component:=", xc, "Y Component:=", ys], [])
            oRep.ExportToFile(name, os.path.join(OUT, fn))
            log("exported " + fn)
        except Exception:
            log("failed " + fn + ": " + traceback.format_exc().splitlines()[-1])
    oProject.Close()
except Exception:
    log(traceback.format_exc())
finally:
    LOG.close()
