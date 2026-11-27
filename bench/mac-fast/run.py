"""Temporary (removed before the PR). One solve: python run.py LABEL TREE FACTOR project.hzn -> JSON line
FACTOR: the fast sweep factorization of this branch (HERTZON_FAST_FACTOR), '-' for the release."""
import json, os, re, subprocess, sys, time
import psutil
label, tree, factor, proj = sys.argv[1:5]
tag = f"{label}_{os.path.basename(proj)[:-4]}"
env = dict(os.environ, PYTHONPATH=tree)
env.pop("HERTZON_FAST_FACTOR", None)
if factor != "-":
    env["HERTZON_FAST_FACTOR"] = factor
design = json.load(open(proj))["designs"][0]["name"]
t = time.time()
p = subprocess.Popen([sys.executable, "-m", "hertzon.cli", "solve", proj, "--design", design, "--force", "--json",
                      "--progress", "text", "-o", f"{tag}.sNp"], env=env, stdout=open(tag + ".out", "w"),
                     stderr=open(tag + ".log", "w"))
pp = psutil.Process(p.pid); peak = 0; timeout = False
while p.poll() is None:
    try:
        peak = max(peak, sum(c.memory_info().rss for c in [pp] + pp.children(recursive=True)))
    except psutil.Error:
        pass
    if time.time() - t > 2400:
        timeout = True
        for c in pp.children(recursive=True) + [pp]:
            try: c.kill()
            except psutil.Error: pass
        break
    time.sleep(0.3)
res = {"label": label, "proj": os.path.basename(proj), "rc": p.returncode, "time_s": round(time.time() - t, 1),
       "peak_gb": round(peak / 2**30, 2), "timeout": timeout}
try:
    j = json.load(open(tag + ".out"))
    res.update(passes=j.get("passes"), tets=j.get("tetrahedra"), sNp=j.get("touchstone"))
except Exception as e:  # noqa: BLE001
    res["error"] = str(e)[:200]
log = open(tag + ".log", errors="replace").read()
for root, _, files in os.walk(os.path.dirname(os.path.abspath(proj)) or "."):
    for f in files:
        fp = os.path.join(root, f)
        if f == "solver.log" and os.path.getmtime(fp) >= t:
            log += open(fp, errors="replace").read()
res["fast"] = sorted({l.strip()[-200:] for l in log.splitlines() if "factorization(s)" in l})[:3]
# S-parameters: every |S| at every frequency of the touchstone, for the comparison
try:
    import numpy as np
    rows = [l.split() for l in open(res["sNp"]) if l.strip() and l[0] not in "!#"]
    res["S"] = [[float(x) for x in r] for r in rows]
except Exception:  # noqa: BLE001
    pass
print("RESULT " + json.dumps(res), flush=True)
