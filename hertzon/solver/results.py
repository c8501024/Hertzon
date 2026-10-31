"""Result files of one analysis setup.

Layout: <project>.hznresults/<design>/<setup>/
    status.json, profile.json, convergence.json, ports.json, mesh_stats.json, auto_open.json
    sparams_<solution>.npz       (freq, S[nf,np,np], z0, solved)
    <solution>.s<N>p             Touchstone
    solution_<solution>.npz      FEM coefficients; fields_<solution>.npz is computed from it on demand
    fields_<solution>.npz, farfield_<sphere>_<solution>.npz, mesh.npz, eigenmodes.json
"""
from __future__ import annotations

import json
import time
import re
from pathlib import Path

import numpy as np


def port_entries(port_map: list) -> list[dict]:
    """One entry per row of S: a port with several modes has a row per mode, in port number order."""
    out = []
    for q in sorted(port_map, key=lambda q: q.get("number", 0)):
        n = int(q.get("modes") or 1)
        names = q.get("mode_names") or [f"Mode{i + 1}" for i in range(n)]
        name = q.get("name", str(q.get("number", "")))
        out += [dict(q, mode=i + 1, label=name if n == 1 else f"{name}:{names[i]}") for i in range(n)]
    return out


def row_names(ports: list, terminals: list | None = None) -> list[str]:
    """Name of every row of S, usable in report expressions as S(P1_TraceP,P1_TraceN): the terminals of a
    terminal solution, else with ports of several modes PortName_M1, PortName_M2 (Floquet ports the mode,
    PortName_TE00) and the port name for a port with one mode. Without several modes or terminals the
    rows have no names (S(1,2)). A name that is no identifier stays the row number."""
    if terminals:
        out = [t for q in sorted(terminals, key=lambda q: q["number"]) for t in q["terminals"]]
    else:
        rows = port_entries(ports)
        if not any(int(q.get("modes") or 1) > 1 for q in ports):
            return [str(i + 1) for i in range(len(rows))]
        out = []
        for q in rows:
            name = q.get("name", "")
            if int(q.get("modes") or 1) == 1:
                out.append(name)
            else:
                mn = q.get("mode_names")
                out.append(f"{name}_{mn[q['mode'] - 1]}" if mn else f"{name}_M{q['mode']}")
    return [n if n.isidentifier() else str(i + 1) for i, n in enumerate(out)]


def s_labels(store) -> list[tuple[str, str]]:
    """(name, kind) of every row of the stored S-parameters (see row_names)."""
    term = store.read_json("terminals.json", None)
    ports = store.read_json("ports.json", []) or []
    if term:
        return [(n, "Terminal") for n in row_names(ports, term)]
    rows = port_entries(ports)
    return [(p["label"] if n.isdigit() else n, p["kind"]) for n, p in zip(row_names(ports), rows)]


def results_dir(project_path: str | Path, design: str, setup: str) -> Path:
    p = Path(project_path)
    return p.with_suffix((p.suffix or ".hzn") + "results") / _safe(design) / _safe(setup)


def studies_dir(project_path: str | Path, design: str) -> Path:
    """Folder of the optimization and parametric runs of a design (projects of version 0.1 used another name)."""
    new = results_dir(project_path, design, "Optimization")
    old = results_dir(project_path, design, "Optimetrics")
    return old if old.is_dir() and not new.exists() else new


def _safe(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    # "", "." or ".." would point the folder at the project's parent; results there get deleted on re-solve
    return safe if safe.strip(".") else "_" * max(1, len(safe))


class ResultStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    # ---- writing --------------------------------------------------------------------------------
    def write_json(self, name: str, data) -> None:
        tmp = self.folder / (name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=_json_default), encoding="utf-8")
        _replace(tmp, self.folder / name)

    def save_npz(self, name: str, **arrays) -> None:
        tmp = self.folder / (name + ".tmp.npz")
        # field data hardly compresses (complex floats) and zlib is single-threaded: a field file of a few
        # 100 MB took 20 s per adaptive pass compressed, about 1 s written as it is
        big = sum(getattr(a, "nbytes", 0) for a in arrays.values()) > 32_000_000
        (np.savez if big else np.savez_compressed)(tmp, **arrays)
        _replace(tmp, self.folder / name)

    def save_sparams(self, solution: str, freq, S, z0, ports, solved=None, gamma=None, zo=None,
                     terminals=None, ports_only: bool = False) -> None:
        """gamma, zo: propagation constant per port mode and characteristic impedance per port (terminal)
        over frequency; terminals: the terminals of a terminal solution; ports_only: only the port modes
        were solved (no S-parameters, no Touchstone file)."""
        freq = np.asarray(freq, float).ravel()
        S = np.asarray(S, complex)
        if S.ndim == 2:
            S = S[None]
        z0 = np.asarray(z0)
        extra = {}
        if gamma is not None:
            extra["gamma"] = np.asarray(gamma, complex)
        if zo is not None:
            extra["zo"] = np.asarray(zo, complex)
        if terminals is not None:
            extra["terminals"] = np.array(json.dumps(terminals))
        if ports_only:
            extra["ports_only"] = np.array(True)
        self.save_npz(f"sparams_{_safe(solution)}.npz", freq=freq, S=S, z0=z0,
                      solved=np.asarray(solved if solved is not None else freq, float),
                      ports=np.array(json.dumps(ports)), **extra)
        if not ports_only:
            self.write_touchstone(solution, freq, S, z0)

    def write_touchstone(self, solution: str, freq, S, z0, path: Path | str | None = None) -> Path:
        n = S.shape[1]
        ref = float(np.real(np.ravel(z0)[0])) if np.size(z0) else 50.0
        path = Path(path) if path else self.folder / f"{_safe(solution)}.s{n}p"
        lines = ["! Generated by Hertzon (EMerge FEM)", f"# HZ S RI R {ref:g}"]
        for k, f in enumerate(freq):
            vals = []
            M = S[k]
            # Touchstone 1.0 order: for 2 ports S11 S21 S12 S22, otherwise row major
            order = [(0, 0), (1, 0), (0, 1), (1, 1)] if n == 2 else [(i, j) for i in range(n) for j in range(n)]
            for i, j in order:
                vals += [f"{M[i, j].real:.10e}", f"{M[i, j].imag:.10e}"]
            if n <= 2:
                lines.append(f"{f:.10e} " + " ".join(vals))
            else:
                chunks = [vals[i:i + 8] for i in range(0, len(vals), 8)]
                lines.append(f"{f:.10e} " + " ".join(chunks[0]))
                lines += ["  " + " ".join(c) for c in chunks[1:]]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    # ---- reading --------------------------------------------------------------------------------
    def read_json(self, name: str, default=None):
        return _read_json(self.folder / name, default)

    def solutions(self) -> list[str]:
        out = []
        for p in sorted(self.folder.glob("sparams_*.npz")):
            out.append(p.stem[len("sparams_"):])
        if "LastAdaptive" in out:
            out.remove("LastAdaptive")
            out.insert(0, "LastAdaptive")
        return out

    def sparams(self, solution: str) -> dict | None:
        p = self.folder / f"sparams_{_safe(solution)}.npz"
        if not p.exists():
            return None
        with np.load(p, allow_pickle=False) as d:
            out = {"freq": d["freq"], "S": d["S"], "z0": d["z0"], "solved": d["solved"],
                   "ports": json.loads(str(d["ports"]))}
            for k in ("gamma", "zo"):
                if k in d.files:
                    out[k] = d[k]
            if "terminals" in d.files:
                out["terminals"] = json.loads(str(d["terminals"]))
            if "ports_only" in d.files:
                out["ports_only"] = bool(d["ports_only"])
            return out

    def npz(self, name: str):
        if name.startswith("fields_") and name.endswith(".npz"):    # computed from the solution on demand
            from .lazy_fields import fields_npz
            return fields_npz(self, name[len("fields_"):-len(".npz")], lambda p: self._load(p.name))
        return self._load(name)

    def _load(self, name: str):
        p = self.folder / name
        if not p.exists():
            return None
        for i in range(5):
            try:
                with np.load(p, allow_pickle=False) as d:
                    return {k: d[k] for k in d.files}
            except (OSError, ValueError, EOFError):
                if i == 4:
                    raise
                time.sleep(0.1)


def _read_json(p: Path, default=None):
    if not p.exists():
        return default
    for _ in range(5):   # the solver may be replacing the file right now (Windows: sharing violation)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            time.sleep(0.05)
    return default


def running_status() -> dict:
    """status.json of a run that starts now: with the solver's process id and host, so that a reader can
    tell a run that is still going from one whose process ended without finishing (killed, crashed,
    interrupted): see run_status."""
    import os
    import socket
    return {"state": "running", "started": time.time(), "pid": os.getpid(), "host": socket.gethostname()}


def _process_alive(pid: int) -> bool:
    import os
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except ImportError:
        pass
    except Exception:  # noqa: BLE001  (gone between the two calls, no access ...)
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_status(folder: Path, default: dict | None = None) -> dict:
    """status.json of a result folder. A run left 'running' by a solver process that no longer exists on this
    computer is reported as 'aborted' (with "stale": true): the process was killed or crashed before it could
    write its final state."""
    d = _read_json(Path(folder) / "status.json", None)
    if not isinstance(d, dict):
        return dict(default or {})
    if d.get("state") == "running" and d.get("pid"):
        import socket
        if d.get("host") in (None, socket.gethostname()) and not _process_alive(int(d["pid"])):
            d = {**d, "state": "aborted", "stale": True,
                 "error": "the solver process ended without finishing (killed, crashed or interrupted)"}
    return d


def _replace(src: Path, dst: Path) -> None:
    """Atomic replace; retried because a reader may hold the target open on Windows."""
    for i in range(20):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, complex):
        return [o.real, o.imag]
    return str(o)


def fit_sparams(ff: np.ndarray, SS: np.ndarray):
    """Rational (vector fitting) model of S-matrices SS (frequency x n x n) at the frequencies ff, as the
    interpolating sweep fits its solutions; returns f -> S(f)."""
    from emerge._emerge.physics.microwave.adaptive_freq import SparamModel
    n = SS.shape[1]
    # pole count bounded by the information in the samples (filterbench): many poles fitted to a handful of
    # samples overfit, the validation points miss and the sweep solves far more frequencies than needed.
    # No fixed limit of ten: a wide band with many resonances or long lines (phase turns) needs more poles
    poles = min(100, max(2, len(ff) // 2))
    models = [[SparamModel(ff, SS[:, i, j], n_poles=poles, _warn=False) for j in range(n)] for i in range(n)]

    def call(f):
        out = np.zeros((len(f), n, n), complex)
        for i in range(n):
            for j in range(n):
                out[:, i, j] = models[i][j](f)
        return out
    return call


def deembed_renormalize(S: np.ndarray, z0, ports: list, beta) -> tuple[np.ndarray, np.ndarray]:
    """Wave-port de-embedding (beta: propagation constant per frequency and port, or None without
    de-embedding) and renormalization of the S-matrices (frequency x n x n) of the port rows `ports`
    (port_entries). Returns (S, reference impedance per row)."""
    n = S.shape[1]
    z0v = np.resize(np.asarray(z0, complex).ravel(), n)
    dist = np.array([float(q.get("deembed") or 0.0) for q in ports] + [0.0] * (n - len(ports)))
    if np.any(dist != 0) and beta is not None:
        ph = np.exp(1j * np.asarray(beta) * dist[None, :])
        S = S * ph[:, :, None] * ph[:, None, :]
    zref = []
    for i in range(n):
        r = ports[i].get("renorm_ohm") if i < len(ports) else None
        zref.append(complex(r) if r is not None else z0v[i])
    zref = np.array(zref)
    if np.allclose(zref, z0v):
        return S, z0v
    return renormalize(S, z0v, zref), zref


def renormalize(S: np.ndarray, z0v: np.ndarray, zref: np.ndarray) -> np.ndarray:
    """S-matrices (frequency x n x n) referred to the port impedances z0v, referred to zref instead."""
    n = S.shape[1]
    I = np.eye(n)
    sq0, sqr = np.diag(np.sqrt(z0v)), np.diag(1 / np.sqrt(zref))
    out = np.empty_like(S)
    for k in range(len(S)):
        Z = sq0 @ (I + S[k]) @ np.linalg.inv(I - S[k]) @ sq0
        Zn = sqr @ Z @ sqr
        out[k] = (Zn - I) @ np.linalg.inv(Zn + I)
    return out
