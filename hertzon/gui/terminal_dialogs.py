"""Terminal solution in the GUI: the Differential Pairs dialog and the Port Field Display."""
from __future__ import annotations

import json

import numpy as np
import pyvista as pv
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QComboBox,
                               QPushButton, QLabel, QDialogButtonBox, QHeaderView)

from ..solver.results import ResultStore


def solved_terminals(doc) -> list[dict] | None:
    """Terminals of the newest solution of the design (terminals.json of its setups), None if none."""
    from .doc import result_dir
    best, t_best = None, -1.0
    for s in doc.design.setups:
        try:
            p = result_dir(doc, s.name) / "terminals.json"
        except Exception:  # noqa: BLE001
            continue
        if p.exists() and p.stat().st_mtime > t_best:
            best, t_best = ResultStore(p.parent).read_json("terminals.json"), p.stat().st_mtime
    return best


def terminal_names(doc) -> list[str]:
    """Names of the terminals in the order of the terminal S-parameters: from the newest solution, else one
    per port (lumped ports are always one terminal; the terminals of a wave port are known after solving)."""
    term = solved_terminals(doc)
    if term:
        return [t for p in sorted(term, key=lambda q: q["number"]) for t in p["terminals"]]
    out = []
    for e in doc.design.ports():
        out.append(e.name if e.kind == "Lumped Port" else f"{e.name} (terminals known after solving)")
    return out


class DiffPairsDialog(QDialog):
    """Differential pairs of the terminals: positive and negative terminal and the reference impedances of the
    differential and the common mode (empty: twice and half the terminal reference). Report quantities:
    S(Diff1,Diff1), S(Comm1,Diff1), Z(Diff1,Diff1), VSWR(Diff1) ..."""

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Differential Pairs")
        self.names = terminal_names(doc)
        try:
            pairs = json.loads(doc.design.settings.get("DifferentialPairs", "") or "[]")
        except ValueError:
            pairs = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Pair k gives the report quantities Diffk (differential mode) and Commk (common "
                             "mode), e.g. S(Diff1,Diff1), S(Diff2,Diff1), S(Comm1,Diff1).\nEmpty reference "
                             "impedances: twice (differential) and half (common) the terminal reference."))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Positive terminal", "Negative terminal", "Z diff", "Z common"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add, rem = QPushButton("Add Pair"), QPushButton("Remove")
        add.clicked.connect(lambda: self._add({}))
        rem.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for q in pairs:
            if isinstance(q, dict):
                self._add(q)
        self.resize(640, 320)

    def _combo(self, value: int) -> QComboBox:
        cb = QComboBox()
        n = max(len(self.names), value)
        for i in range(n):
            cb.addItem(f"{i + 1}: {self.names[i]}" if i < len(self.names) else f"{i + 1}", i + 1)
        cb.setCurrentIndex(max(0, value - 1))
        return cb

    def _add(self, q: dict) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        n = self.table.rowCount()
        self.table.setCellWidget(r, 0, self._combo(int(q.get("pos", 2 * n - 1))))
        self.table.setCellWidget(r, 1, self._combo(int(q.get("neg", 2 * n))))
        self.table.setItem(r, 2, QTableWidgetItem(str(q.get("zdiff", ""))))
        self.table.setItem(r, 3, QTableWidgetItem(str(q.get("zcomm", ""))))

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()} or {self.table.rowCount() - 1}, reverse=True)
        for r in rows:
            if r >= 0:
                self.table.removeRow(r)

    def pairs(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            q = {"pos": self.table.cellWidget(r, 0).currentData(), "neg": self.table.cellWidget(r, 1).currentData()}
            for c, k in ((2, "zdiff"), (3, "zcomm")):
                it = self.table.item(r, c)
                if it is not None and it.text().strip():
                    q[k] = it.text().strip()
            out.append(q)
        return out

    def validate(self) -> str:
        used = set()
        for k, q in enumerate(self.pairs()):
            if q["pos"] == q["neg"]:
                return f"Pair {k + 1}: positive and negative terminal are the same"
            if {q["pos"], q["neg"]} & used:
                return f"Pair {k + 1}: a terminal is already part of another pair"
            used |= {q["pos"], q["neg"]}
        return ""

    def accept(self) -> None:
        msg = self.validate()
        if msg:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Differential Pairs", msg)
            return
        super().accept()


# ---- Port Field Display ---------------------------------------------------------------------------
def port_field_solutions(doc) -> list[str]:
    """Solutions ('Setup : Solution') with stored port fields."""
    from .doc import result_dir
    out = []
    for s in doc.design.setups:
        try:
            folder = result_dir(doc, s.name)
        except Exception:  # noqa: BLE001
            continue
        for p in sorted(folder.glob("portfields_*.npz")):
            out.append(f"{s.name} : {p.stem[len('portfields_'):]}")
    return out


def port_field_meshes(doc, port: str, solution: str | None = None, mode: int = 1, freq_index: int = 0,
                      which: str = "E") -> tuple[list, str]:
    """The mode field of a wave port on its face for the 3D view: magnitude in colour and the field at
    phase 0 as arrows. Returns (meshes for the viewer's set_overlay, info text)."""
    from .doc import result_dir
    from .fields import COLOR_MAPS, ATTR_DEFAULTS
    sols = port_field_solutions(doc)
    if solution is None:
        if not sols:
            raise RuntimeError("No port fields: analyze the design (or solve the ports only) first")
        solution = next((s for s in sols if s.endswith("LastAdaptive")), sols[0])
    setup, _, sol = [x.strip() for x in solution.partition(":")]
    d = ResultStore(result_dir(doc, setup)).npz(f"portfields_{sol}.npz")
    if d is None:
        raise RuntimeError(f"No port fields in '{solution}'")
    meta = json.loads(str(d["meta"]))
    q = next((m for m in meta if m["name"] == port), None)
    if q is None:
        raise RuntimeError(f"'{port}' has no port fields in '{solution}' (only wave ports have port modes)")
    n = q["number"]
    key = f"{which}_{n}_{mode}_{freq_index}"
    if key not in d:
        raise RuntimeError(f"'{port}' has no mode {mode} in '{solution}'")
    xyz, tris, F = d[f"nodes_{n}"], d[f"tris_{n}"], np.asarray(d[key], complex)
    faces = np.hstack([np.full((len(tris), 1), 3), tris]).ravel()
    surf = pv.PolyData(np.asarray(xyz, float), faces)
    mag = np.sqrt((np.abs(F) ** 2).sum(axis=1))
    unit = "V/m" if which == "E" else "A/m"
    surf.point_data[f"|{which}|"] = mag
    cmap = COLOR_MAPS.get(ATTR_DEFAULTS["ColorMap"], "jet")
    meshes = [(surf, {"scalars": f"|{which}|", "cmap": cmap, "clim": (0.0, float(mag.max() or 1.0)),
                      "show_scalar_bar": True,
                      "scalar_bar_args": {"title": f"{which} mode {mode} [{unit}]", "vertical": True}})]
    # arrows of the field at phase 0 at the triangle centres, scaled to the triangle size
    c = xyz[tris].mean(axis=1)
    re = np.real(F[tris].mean(axis=1))
    size = float(np.sqrt(surf.area / max(1, len(tris))))
    r = np.linalg.norm(re, axis=1)
    keep = r > 1e-3 * (r.max() or 1.0)
    if keep.any():
        pts = pv.PolyData(c[keep])
        pts.point_data["v"] = re[keep] / r.max()
        glyphs = pts.glyph(orient="v", scale="v", factor=2.5 * size, geom=pv.Arrow())
        meshes.append((glyphs, {"color": "black", "show_scalar_bar": False}))
    row = next((m for m in q["modes"] if m["mode"] == mode and m["freq_index"] == freq_index), {})
    f = float(np.ravel(d["freq"])[freq_index]) if "freq" in d else 0.0
    g = row.get("gamma", [0.0, 0.0])
    info = (f"{port}, mode {mode} ({row.get('type', '')}) at {f / 1e9:.6g} GHz: Zo = {row.get('zo', 0):.4g} ohm, "
            f"Gamma = {g[0]:.4g} + j{g[1]:.4g} 1/m  ({solution})")
    return meshes, info
