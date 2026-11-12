"""Write the final Run C geometry (same numbers as the Hertzon design T_opp) to JSON for the reference-solver script."""
import sys, os, json, io, contextlib
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "tools"))
with contextlib.redirect_stdout(io.StringIO()):
    import drc

ye = lambda x: 3.93 + 0.0055866 * (x + 3.5)   # upper PCB edge
xf, gp = drc.V["xf"], 0.3
geo = {
    "copper": [[[list(p) for p in drc.ant]]],   # one copper area, outer ring only
    "gvias": [[drc.V["xsv"], 3.8]],
    "feed": [xf, 4.65],
    "via_d": 0.25,
    "pad": [[xf - .5, ye(xf - .5) + gp], [xf + .5, ye(xf + .5) + gp], [xf + .5, 5.0], [xf - .5, 5.0]],
    "port": [[xf - .5, ye(xf - .5)], [xf + .5, ye(xf + .5)], [xf + .5, ye(xf + .5) + gp], [xf - .5, ye(xf - .5) + gp]],
    "intline": [[xf, ye(xf)], [xf, ye(xf) + gp]],
}
json.dump(geo, open(os.path.join(here, "hfss_RunC.json"), "w"), indent=1)
print(len(geo["copper"][0]), "copper points written")
