"""Connectors tab: put an edge-launch connector of the library at a lumped port (Simulate with Real Connector)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from ..model import connectors as C

MODE_HELP = {
    "Ideal": "The lumped port is solved as before; the connector and its footprint are shown transparent, "
             "outside the model.",
    "Transition": "The 3D model stays as in Ideal. A small 3D design of the connector-to-board transition "
                  "(solved once) is cascaded with the lumped port solution in a circuit design.",
    "3D": "The connector and its footprint are solved with the board, with a wave port on the coax. The air "
          "region gets no padding toward the connector.",
}


class ConnectorDialog(QDialog):
    """Interface, part, name and mode of a connector at the lumped port ``port``; ``place`` (EdgePlacement) shows
    what was found at the port: board edge, board thickness, trace width and permittivity."""

    def __init__(self, parent, port: str, place: C.EdgePlacement | None, substrate: str = "",
                 interface: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Simulate with Real Connector: {port}")
        self.place = place
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)
        if place is not None:
            found = QLabel(f"Board edge {place.direction}, board {place.board_h:g} mm"
                           + (f" ({substrate}, εr {place.er:g})" if substrate else f", εr {place.er:g}")
                           + f", trace {place.trace_w:g} mm")
            found.setWordWrap(True)
            form.addRow("At the port:", found)
        self.interface = QComboBox()
        for it in C.INTERFACES:
            self.interface.addItem(it, it)
        if interface in C.INTERFACES:
            self.interface.setCurrentIndex(C.INTERFACES.index(interface))
        form.addRow("Interface:", self.interface)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter: manufacturer, part number, mounting")
        form.addRow("Search:", self.search)
        self.list = QListWidget()
        self.list.setMinimumSize(520, 220)
        form.addRow("Part:", self.list)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info.setOpenExternalLinks(True)
        form.addRow("", self.info)
        self.name = QLineEdit("")
        self.name.setPlaceholderText("automatic (J1, J2 ...)")
        form.addRow("Name:", self.name)
        self.mode = QComboBox()
        for m in C.MODES:
            self.mode.addItem(C.MODE_LABELS[m], m)
        form.addRow("Simulate as:", self.mode)
        self.mode_help = QLabel()
        self.mode_help.setWordWrap(True)
        form.addRow("", self.mode_help)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.interface.currentIndexChanged.connect(self._fill)
        self.search.textChanged.connect(self._fill)
        self.list.currentItemChanged.connect(self._show)
        self.mode.currentIndexChanged.connect(lambda _: self.mode_help.setText(MODE_HELP[self.mode.currentData()]))
        self.mode_help.setText(MODE_HELP["Ideal"])
        self._fill()

    def _suits(self, p: dict) -> bool:
        """Parts made for the board thickness first (or without a stated range)."""
        rng = [x for x in (p.get("pcb_thickness_mm") or []) if x is not None]
        if self.place is None or len(rng) < 2:
            return True
        return rng[0] - 0.05 <= self.place.board_h <= rng[1] + 0.05

    def _fill(self) -> None:
        it = self.interface.currentData()
        q = self.search.text().strip().lower()
        self.list.clear()
        ps = [p for p in C.parts(it) if not q or q in " ".join(
            str(p.get(k, "")) for k in ("manufacturer", "part_number", "mounting", "notes", "gender")).lower()]
        ps.sort(key=lambda p: (not self._suits(p), p["manufacturer"], p["part_number"]))
        for p in ps:
            item = QListWidgetItem(C.label(p) + ("" if self._suits(p) else "   (other board thickness)"))
            item.setData(Qt.UserRole, p["id"])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.info.setText("No part matches.")

    def _show(self, item) -> None:
        if item is None:
            self.info.setText("")
            return
        p = C.part(item.data(Qt.UserRole))
        rng = [x for x in (p.get("pcb_thickness_mm") or []) if x is not None]
        lines = [f"{p.get('mounting', '')}, {p.get('gender', '')}, up to {C.f_max(p):g} GHz"]
        if rng:
            lines.append(f"board {rng[0]:g}–{rng[-1]:g} mm")
        if p.get("launch_pin_od_mm"):
            lines.append(f"launch pin {p['launch_pin_od_mm']:g} mm")
        urls = (("datasheet_url", p.get("datasheet_url")), ("product page", p.get("product_url")),
                ("3D model", (p.get("cad") or {}).get("step_url")))
        links = [f'<a href="{u}">{"data sheet" if t == "datasheet_url" else t}</a>' for t, u in urls if u]
        if links:
            lines.append(" · ".join(links))
        if self.place is not None and rng and not self._suits(p):
            lines.append(f"<b>Made for {rng[0]:g}–{rng[-1]:g} mm boards, this board is {self.place.board_h:g} mm.</b>")
        self.info.setText("<br>".join(lines))

    def _accept(self) -> None:
        if self.list.currentItem() is None:
            return
        self.accept()

    def result_values(self) -> dict:
        return {"part": self.list.currentItem().data(Qt.UserRole), "name": self.name.text().strip() or "J1",
                "mode": self.mode.currentData()}
