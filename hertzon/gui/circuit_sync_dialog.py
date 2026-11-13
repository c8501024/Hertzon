"""Sync with 3D: the differences between a circuit made by Create Circuit and its 3D design, each taken in the
direction chosen on its row."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..model import circuit_sync as CS

LABELS = {"": "Skip", CS.TO_CIRCUIT: "→ Circuit", CS.TO_3D: "→ 3D design"}
KINDS = {"variable": "Variable", "dimension": "Dimension", "topology": "Element"}


class SyncDialog(QDialog):
    """One row per difference: what it is, the value in the circuit and in the 3D design, the direction."""

    def __init__(self, parent, circuit: str, design: str, diffs: list[CS.Difference]):
        super().__init__(parent)
        self.setWindowTitle(f"Sync {circuit} with {design}")
        self.diffs = diffs
        lay = QVBoxLayout(self)
        head = QLabel(f"What differs between the circuit <b>{circuit}</b> and its 3D design <b>{design}</b>. "
                      "Variables of the same name are the shared dimensions; an element that is only on one side "
                      "is a changed topology, which the circuit takes by being recognized again.")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.table = QTableWidget(len(diffs), 5)
        self.table.setHorizontalHeaderLabels(["Kind", "Name", "Circuit", "3D design", "Direction"])
        self.table.verticalHeader().setVisible(False)
        self.combos: list[QComboBox] = []
        for r, d in enumerate(diffs):
            for c, text in enumerate((KINDS.get(d.kind, d.kind), d.name, d.circuit or "–", d.model or "–")):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if d.note:
                    it.setToolTip(d.note)
                self.table.setItem(r, c, it)
            box = QComboBox()
            for key in ("", *d.directions):
                box.addItem(LABELS[key], key)
            if d.note:
                box.setToolTip(d.note)
            # default: a shared value or a lost link to the circuit (the 3D design is what was solved)
            box.setCurrentIndex(1 if d.directions and d.directions[0] == CS.TO_CIRCUIT and d.kind != "topology"
                                else 0)
            self.table.setCellWidget(r, 4, box)
            self.combos.append(box)
        hh = self.table.horizontalHeader()
        for c in range(4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        for label, key in (("All to circuit", CS.TO_CIRCUIT), ("All to 3D design", CS.TO_3D), ("Skip all", "")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self.set_all(k))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(820, 420)

    def set_all(self, key: str) -> None:
        for box in self.combos:
            i = box.findData(key)
            box.setCurrentIndex(i if i >= 0 else 0)

    def choices(self) -> list[tuple[CS.Difference, str]]:
        return [(d, box.currentData()) for d, box in zip(self.diffs, self.combos) if box.currentData()]
