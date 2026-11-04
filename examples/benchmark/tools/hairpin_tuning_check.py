"""Hairpin filter with derivative tuning: proof on a large machine.

python hairpin_tuning_check.py WORKDIR [--skip-accuracy] [--skip-opt]
PYTHONPATH must point at the Hertzon source.

1. full analysis of the hairpin example (adaptive mesh + interpolating sweep)
2. tuning model of all six variables (L, w, b, g, yP, M) on that mesh: memory, time, ranges
3. accuracy: tuned S against fresh full analyses (own adaptive mesh) at a few offsets
4. Derivative Trust Region optimizations: L, g, yP (the wizard default) and all six variables
Everything is written to WORKDIR/summary.json and WORKDIR/*.log.
"""
import copy, json, os, subprocess, sys, time
from pathlib import Path

import numpy as np

W = Path(sys.argv[1]).resolve()
W.mkdir(parents=True, exist_ok=True)
PY = sys.executable
from hertzon.model.design import Project  # noqa: E402
from hertzon.solver.results import ResultStore  # noqa: E402
from hertzon.solver.tuning import TuningModel, tuning_path  # noqa: E402

SUM = W / "summary.json"
summary = json.loads(SUM.read_text()) if SUM.exists() else {}


def save():
    SUM.write_text(json.dumps(summary, indent=1, default=str))


def run(args, log, env_extra=None):
    env = dict(os.environ, **(env_extra or {}))
    t = time.time()
    with open(W / log, "w", encoding="utf-8") as f:
        p = subprocess.Popen([PY, *args], stdout=f, stderr=subprocess.STDOUT, env=env)
        peak = 0
        try:
            import psutil
            pp = psutil.Process(p.pid)
            while p.poll() is None:
                try:
                    rss = pp.memory_info().rss + sum(c.memory_info().rss for c in pp.children(recursive=True))
                    peak = max(peak, rss)
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1)
        except ImportError:
            p.wait()
    return p.returncode, time.time() - t, peak / 2 ** 20


import hertzon  # noqa: E402
EXAMPLE = Path(hertzon.__file__).resolve().parents[1] / "examples" / "HairpinFilter.hzn"


def example():
    return Project.load(str(EXAMPLE))


def project(name, **var):
    p = example()
    d = p.designs[0]
    d.variables.update(var)
    path = W / f"{name}.hzn"
    p.save(str(path))
    return path, d.name


def analyze(name, **var):
    path, des = project(name, **var)
    out = W / f"res_{name}"
    rc, dt, mb = run(["-m", "hertzon.solver.run", str(path), des, "Setup1", str(out)], f"{name}.log")
    conv = json.loads((out / "convergence.json").read_text()) if (out / "convergence.json").exists() else {}
    return out, {"rc": rc, "seconds": round(dt, 1), "peak_mb": round(mb), "tets": conv.get("final_tets")}


nominal = example().designs[0].variables
val = lambda k: float(nominal[k].rstrip("mm"))  # noqa: E731
VARS = ["L", "w", "b", "g", "yP", "M"]

if "--skip-accuracy" not in sys.argv:
    # 1 + 2
    out0, info0 = analyze("nominal")
    summary["nominal"] = info0
    save()
    path0 = W / "nominal.hzn"
    spec = json.dumps({"sweep": "Sweep", "variables": VARS, "points": 51})
    rc, dt, mb = run(["-m", "hertzon.solver.run", str(path0), "Hairpin_N3", "Setup1", str(out0)], "tuning6.log",
                     {"HERTZON_TUNING": spec})
    summary["tuning6"] = {"rc": rc, "seconds": round(dt, 1), "peak_mb": round(mb)}
    save()
    # 3
    model = TuningModel(tuning_path(out0, "Sweep"))
    summary["tuning6"]["ranges"] = dict(zip(VARS, model.ranges.tolist()))
    S0 = ResultStore(out0).sparams("Sweep")
    cases = {"g+0.1": {"g": 0.1}, "g-0.1": {"g": -0.1}, "L-0.5": {"L": -0.5}, "L+0.5": {"L": 0.5},
             "yP+0.3": {"yP": 0.3}, "w+0.05": {"w": 0.05}, "mix": {"L": -0.3, "g": 0.08, "yP": 0.2}}
    acc = summary.setdefault("accuracy", {})
    for name, off in cases.items():
        if name in acc:
            continue
        var = {k: f"{val(k) + d:.6g}mm" for k, d in off.items()}
        outc, info = analyze("case_" + name.replace("+", "p").replace("-", "m"), **var)
        Sx = ResultStore(outc).sparams("Sweep")
        dp = np.array([off.get(v, 0.0) for v in VARS])
        St, _ = model.sparams(dp)
        f = np.asarray(Sx["freq"], float)
        ft = np.asarray(model.freq_out, float)
        db = lambda S: 20 * np.log10(np.maximum(np.abs(S), 1e-12))  # noqa: E731
        idx = [int(np.argmin(np.abs(ft - x))) for x in f]
        e21 = np.abs(db(Sx["S"][:, 1, 0]) - db(St[idx, 1, 0]))
        e11 = np.abs(db(Sx["S"][:, 0, 0]) - db(St[idx, 0, 0]))
        ch21 = np.abs(db(Sx["S"][:, 1, 0]) - db(S0["S"][:, 1, 0]))
        acc[name] = dict(info, max_err_S21_dB=float(e21.max()), mean_err_S21_dB=float(e21.mean()),
                         max_err_S11_dB=float(e11.max()), max_change_S21_dB=float(ch21.max()),
                         max_abs_err=float(np.abs(Sx["S"] - St[idx]).max()),
                         freq=f.tolist(), S21_solved=db(Sx["S"][:, 1, 0]).tolist(), S21_tuned=db(St[idx, 1, 0]).tolist(),
                         S11_solved=db(Sx["S"][:, 0, 0]).tolist(), S11_tuned=db(St[idx, 0, 0]).tolist(),
                         S21_nominal=db(S0["S"][:, 1, 0]).tolist())
        save()

if "--skip-opt" not in sys.argv:
    # 4
    for label, incl in (("dtr3", ("L", "g", "yP")), ("dtr6", tuple(VARS))):
        if label in summary.get("opt", {}):
            continue
        p = example()
        d = p.designs[0]
        op = next(o for o in d.optimetrics if o.name == "HairpinDTR")
        for v in op.variables:
            v["include"] = v["name"] in incl
        path = W / f"opt_{label}.hzn"
        p.save(str(path))
        rc, dt, mb = run(["-m", "hertzon.solver.optimize", str(path), d.name, "HairpinDTR", str(W / f"opt_{label}")],
                         f"opt_{label}.log")
        sm = W / f"opt_{label}" / "summary.json"
        summary.setdefault("opt", {})[label] = {"rc": rc, "seconds": round(dt, 1), "peak_mb": round(mb),
                                                "summary": json.loads(sm.read_text()) if sm.exists() else None}
        save()
print("FERTIG")
(W / "FERTIG.txt").write_text("FERTIG")
