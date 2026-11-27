"""One solve: python run.py <label> <tree> <solver|Auto> <project.hzn> -> <label>_<project>.json/.s2p/.log"""
import json, os, subprocess, sys, time
import psutil
label, tree, solver, proj = sys.argv[1:5]
tag = f"{label}_{os.path.basename(proj)[:-4]}"
env = dict(os.environ, PYTHONPATH=tree)
env.pop("HERTZON_SOLVER", None)
if solver != "Auto":
    env["HERTZON_SOLVER"] = solver
design = json.load(open(proj))["designs"][0]["name"]
t = time.time()
p = subprocess.Popen([sys.executable, "-m", "hertzon.cli", "solve", proj, "--design", design, "--force", "--json",
                      "--progress", "text", "-o", f"{tag}.sNp"], env=env, stdout=open(tag + ".out", "w"),
                     stderr=open(tag + ".log", "w"))
pp = psutil.Process(p.pid); peak = 0
while p.poll() is None:
    try:
        peak = max(peak, sum(c.memory_info().rss for c in [pp] + pp.children(recursive=True)))
    except psutil.Error:
        pass
    time.sleep(0.3)
res = {"label": label, "proj": os.path.basename(proj), "rc": p.returncode, "time_s": round(time.time() - t, 1),
       "peak_gb": round(peak / 2**30, 2)}
try:
    j = json.load(open(tag + ".out"))
    res.update(passes=j.get("passes"), tets=j.get("tetrahedra"), converged=j.get("converged"), sNp=j.get("touchstone"))
except Exception as e:  # noqa: BLE001
    res["error"] = str(e)
log = open(tag + ".log", errors="replace").read()
res["solver_lines"] = sorted({l.strip()[-140:] for l in log.splitlines()
                              if any(k in l for k in ("Matrix solver", "Accelerate", "SPARTA", "SuperLU", "solves the frequencies"))})[:8]
json.dump(res, open(tag + ".json", "w"), indent=1)
print(json.dumps({k: res[k] for k in res if k != "solver_lines"}), flush=True)
