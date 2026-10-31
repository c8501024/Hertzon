"""Project Manager tree and the modeler history tree."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap, QPainter, QPalette
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView, QStyle, QStyledItemDelegate

from .doc import Document
from ..model import groups as G
from ..model.schema import BOUNDARY_COLORS, EXCITATION_COLORS
from ..solver.results import results_dir, run_status
from .doc import result_dir

ROLE = Qt.UserRole + 1


def color_icon(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setBrush(QColor(color))
    p.setPen(QColor("#333333"))
    p.drawRect(1, 1, size - 3, size - 3)
    p.end()
    return QIcon(pm)


SELECTED = "#f3dca9"      # selected rows: gold, also while another widget has the focus


class _BoldSelection(QStyledItemDelegate):
    """Selected rows in bold, so that the selection stands out at a glance."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if option.state & QStyle.State_Selected:
            option.font.setBold(True)


def strong_selection(tree: QTreeWidget) -> None:
    """Selection colours for both trees: the same gold with dark text when the tree has the focus
    and when it has not (Windows draws an inactive selection in a pale grey that was easy to overlook)."""
    pal = tree.palette()
    for group in (QPalette.Active, QPalette.Inactive):
        pal.setColor(group, QPalette.Highlight, QColor(SELECTED))
        pal.setColor(group, QPalette.HighlightedText, QColor("#1f2633"))
    tree.setPalette(pal)
    tree.setItemDelegate(_BoldSelection(tree))


class ProjectTree(QTreeWidget):
    """Project > Design > Model / Boundaries / Excitations / Mesh / Analysis / Results / ..."""

    itemActivatedData = Signal(object)       # double click
    itemSelectedData = Signal(object)        # single selection
    contextRequested = Signal(object, object)  # data, global pos

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        strong_selection(self)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.itemDoubleClicked.connect(lambda it, c: self.itemActivatedData.emit(it.data(0, ROLE)))
        self.itemSelectionChanged.connect(self._sel)
        self._expanded: set[str] = {"project", "design", "Boundaries", "Excitations", "Analysis", "Equations",
                                    "Results", "Mesh", "Radiation", "Field Overlays"}
        doc.changed.connect(lambda *_: self.rebuild())
        self.rebuild()

    def _sel(self) -> None:
        items = self.selectedItems()
        self.itemSelectedData.emit([it.data(0, ROLE) for it in items] if len(items) > 1 else
                                   (items[0].data(0, ROLE) if items else None))

    def _ctx(self, pos) -> None:
        it = self.itemAt(pos)
        if it is not None:
            if not it.isSelected():
                self.setCurrentItem(it)
            self.contextRequested.emit(it.data(0, ROLE), self.viewport().mapToGlobal(pos))

    def _remember(self) -> None:
        def walk(it):
            d = it.data(0, ROLE)
            key = d[1] if isinstance(d, tuple) and d[0] == "group" else (d[0] if isinstance(d, tuple) else None)
            if key and it.isExpanded():
                self._expanded.add(str(key))
            elif key:
                self._expanded.discard(str(key))
            for i in range(it.childCount()):
                walk(it.child(i))
        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))

    def _item(self, parent, text: str, data, icon: QIcon | None = None, bold=False) -> QTreeWidgetItem:
        it = QTreeWidgetItem([text])
        it.setData(0, ROLE, data)
        if icon:
            it.setIcon(0, icon)
        if bold:
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
        (parent.addChild if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem)(it)
        key = data[1] if isinstance(data, tuple) and data[0] == "group" else (data[0] if isinstance(data, tuple) else None)
        if key and str(key) in self._expanded:
            it.setExpanded(True)
        return it

    def _optim_status(self, name: str) -> str:
        if not self.doc.project.path:
            return ""
        from .solve import optimization_dir
        from ..solver.results import run_status
        return run_status(optimization_dir(self.doc.project.path, self.doc.design_name, name)).get("state", "")

    def rebuild(self) -> None:
        if self.topLevelItemCount():
            self._remember()
        self.blockSignals(True)
        self.clear()
        doc = self.doc
        proj = doc.project
        root = self._item(self, proj.name + ("*" if doc.dirty else ""), ("project",), bold=True)
        root.setExpanded(True)
        for des in proj.designs:
            active = des.name == doc.design_name
            dn = self._item(root, f"{des.name} ({des.settings.get('SolutionType', 'Modal')})", ("design", des.name),
                            bold=active)
            dn.setExpanded(active)
            if not active:
                continue
            self._item(dn, "Model", ("model",))
            g = self._item(dn, "Boundaries", ("group", "Boundaries"))
            if des.settings.get("AutoOpen", "Off") != "Off":
                self._item(g, f"Auto Open ({des.settings.get('AutoOpen')}, {des.settings.get('AutoOpenBoundary', 'Auto')})",
                           ("autoopen",), color_icon("#27ae60"))
            for b in des.boundaries:
                it = self._item(g, b.name, ("boundary", b.name), color_icon(BOUNDARY_COLORS.get(b.kind, "#888")))
                if not b.enabled:
                    it.setForeground(0, QBrush(QColor("#999")))
            g = self._item(dn, "Excitations", ("group", "Excitations"))
            for e in des.excitations:
                it = self._item(g, e.name, ("excitation", e.name), color_icon(EXCITATION_COLORS.get(e.kind, "#888")))
                if not e.enabled:
                    it.setForeground(0, QBrush(QColor("#999")))
            g = self._item(dn, "Mesh", ("group", "Mesh"))
            for m in des.mesh_ops:
                self._item(g, m.name, ("meshop", m.name))
            g = self._item(dn, "Analysis", ("group", "Analysis"))
            for s in des.setups:
                st = self._status(s.name)
                label = s.name + {"done": "", "running": "  [running]", "failed": "  [failed]",
                                  "aborted": "  [aborted]", "outdated": "  [outdated]"}.get(st, "")
                si = self._item(g, label, ("setup", s.name))
                si.setExpanded(True)
                if st == "failed":
                    si.setForeground(0, QBrush(QColor("#c0392b")))
                elif st == "outdated":
                    si.setForeground(0, QBrush(QColor("#b36b00")))
                    si.setToolTip(0, "The model or setup changed after this solution was computed. "
                                     "Analyze again to update it.")
                for sw in s.sweeps:
                    it = self._item(si, sw.name, ("sweep", s.name, sw.name))
                    if sw.props.get("Enabled", "True") != "True":
                        it.setForeground(0, QBrush(QColor("#999")))
            g = self._item(dn, "Optimization", ("group", "Optimization"))
            from ..solver.parametric import design_variations, variation_label, current_values
            try:
                solved = design_variations(self.doc.project, des)
            except Exception:  # noqa: BLE001
                solved = []
            cur = current_values(self.doc.project, des)
            for ps in des.parametrics:
                st = self._optim_status(ps.name)
                pi = self._item(g, ps.name + {"running": "  [running]", "failed": "  [failed]",
                                              "aborted": "  [aborted]"}.get(st, ""), ("param", ps.name))
                for name, texts, _ in solved:
                    if name != ps.name:
                        continue
                    shown = all(cur.get(k) == v for k, v in texts.items())
                    vi = self._item(pi, variation_label(texts) + ("  [shown]" if shown else ""),
                                    ("variation", ps.name, json.dumps(texts)))
                    vi.setToolTip(0, "Double-click: show this variation (geometry, fields and reports, "
                                     "without solving again)")
            for op in des.optimetrics:
                st = self._optim_status(op.name)
                self._item(g, op.name + {"running": "  [running]", "failed": "  [failed]",
                                         "aborted": "  [aborted]"}.get(st, ""), ("optim", op.name))
            g = self._item(dn, "Equations", ("group", "Equations"))
            g.setToolTip(0, "Equations of the Fields Calculator (Results > Calculator): ∫ values, ◧ fields, "
                            "ƒ functions")
            if des.field_exprs:
                from .calc_bridge import expression_kinds
                try:
                    kinds = expression_kinds(self.doc)
                except Exception:  # noqa: BLE001
                    kinds = {}
                for e in des.field_exprs:
                    k = kinds.get(e.name, "")
                    it = self._item(g, f"{'◧' if k == 'field' else '⚠' if k == 'error' else '∫'}  {e.name}",
                                    ("equation", e.name))
                    it.setToolTip(0, f"{e.name} = {e.expr}" + (f"\n{e.description}" if e.description else ""))
            for text in des.field_funcs:
                head = text.split("=", 1)[0].strip()
                it = self._item(g, f"ƒ  {head}", ("eqfunc", head.split("(", 1)[0].strip()))
                it.setToolTip(0, text)
            g = self._item(dn, "Results", ("group", "Results"))
            for r in des.reports:
                self._item(g, r.name, ("report", r.name))
            g = self._item(dn, "Field Overlays", ("group", "Field Overlays"))
            for fp in des.field_plots:
                self._item(g, fp.name, ("fieldplot", fp.name))
            g = self._item(dn, "Radiation", ("group", "Radiation"))
            for sp in des.spheres:
                self._item(g, sp.name, ("sphere", sp.name))
        defs = self._item(root, "Definitions", ("group", "Definitions"))
        self._item(defs, f"Materials ({len(proj.materials)} project)", ("materials",))
        self._item(defs, f"Project Variables ({len(proj.variables)})", ("projvars",))
        self.blockSignals(False)

    def select_data(self, data) -> None:
        """Select and show the tree item with this role data."""
        def walk(it):
            d = it.data(0, ROLE)
            if d is not None and tuple(d) == tuple(data):
                return it
            for i in range(it.childCount()):
                r = walk(it.child(i))
                if r is not None:
                    return r
            return None
        for i in range(self.topLevelItemCount()):
            it = walk(self.topLevelItem(i))
            if it is not None:
                self.blockSignals(True)
                self.setCurrentItem(it)
                self.scrollToItem(it)
                self.blockSignals(False)
                return

    def _status(self, setup: str) -> str:
        return solution_state(self.doc, setup)


def solution_state(doc, setup: str) -> str:
    """'' (no solution), running, failed, aborted, done, or outdated (model/setup changed since the solve)."""
    if not doc.project.path:
        return ""
    d = result_dir(doc, setup)
    nominal = results_dir(doc.project.path, doc.design_name, setup)
    if d != nominal:                        # a solved parametric variation that matches the design
        running = run_status(nominal).get("state") == "running"
        return "running" if running else "done"
    if not (d / "status.json").exists():
        return ""
    st = run_status(d)
    state = st.get("state", "")
    if state == "done" and "model_key" in st:
        from ..solver.keys import model_key, setup_key
        try:
            s = doc.design.setup(setup)
            if st["model_key"] != model_key(doc.project, doc.design, s) or \
                    st.get("setup_key", "") != setup_key(s):
                return "outdated"
        except Exception:  # noqa: BLE001
            pass
    return state


class ModelTree(QTreeWidget):
    """Objects grouped as Solids (by material) / Sheets / Lines with their command history; members of
    object groups are listed under their group in Groups."""

    objectSelected = Signal(list, bool)          # names, add
    commandSelected = Signal(str, int)           # object, command index
    commandActivated = Signal(str, int)
    contextRequested = Signal(object, object)
    csActivated = Signal(str)

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        strong_selection(self)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.itemSelectionChanged.connect(self._sel)
        self.itemDoubleClicked.connect(self._dbl)
        self._syncing = False
        self._expanded: set[str] = {"Solids", "Sheets", "Lines", "Non Model"}
        doc.changed.connect(lambda *_: self.rebuild())
        doc.geometryChanged.connect(self.rebuild)
        doc.selectionChanged.connect(self.sync_selection)
        self.rebuild()

    def _ctx(self, pos) -> None:
        it = self.itemAt(pos)
        self.contextRequested.emit(it.data(0, ROLE) if it else None, self.viewport().mapToGlobal(pos))

    def _sel(self) -> None:
        if self._syncing:
            return
        items = self.selectedItems()
        objs = []
        for it in items:
            d = it.data(0, ROLE)
            if d and d[0] == "objgroup":
                objs += [n for n in G.members(self.doc.design, d[1]) if n not in objs]
            elif d and d[0] == "object" and d[1] not in objs:
                objs.append(d[1])
        cmds = [it.data(0, ROLE) for it in items if it.data(0, ROLE) and it.data(0, ROLE)[0] == "command"]
        if objs:
            self.objectSelected.emit(objs, False)
        elif cmds:
            self.commandSelected.emit(cmds[0][1], cmds[0][2])

    def _dbl(self, it, col) -> None:
        d = it.data(0, ROLE)
        if d and d[0] == "command":
            self.commandActivated.emit(d[1], d[2])
        elif d and d[0] == "object":
            self.commandActivated.emit(d[1], -1)
        elif d and d[0] == "cs":
            self.csActivated.emit(d[1])

    def rebuild(self) -> None:
        # remember expansion
        def walk(it):
            d = it.data(0, ROLE)
            if d and d[0] in ("group", "material", "object", "objgroup"):
                key = f"{d[0]}:{d[1]}"
                (self._expanded.add if it.isExpanded() else self._expanded.discard)(key)
            for i in range(it.childCount()):
                walk(it.child(i))
        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))
        self.blockSignals(True)
        self.clear()
        des = self.doc.design
        groups = {}
        grouped = G.groups(des)
        titles = (["Groups"] if grouped else []) + ["Solids", "Sheets", "Lines"]
        if any(not o.model for o in des.live_objects()):
            titles.append("Non Model")
        for title in titles:
            g = QTreeWidgetItem([title])
            g.setData(0, ROLE, ("group", title))
            self.addTopLevelItem(g)
            g.setExpanded(f"group:{title}" in self._expanded or title in self._expanded)
            groups[title] = g
        for gname, names in grouped.items():
            gi = QTreeWidgetItem([gname])
            gi.setData(0, ROLE, ("objgroup", gname))
            gi.setToolTip(0, f"Group of {len(names)} objects: selected, moved and transformed together")
            groups["Groups"].addChild(gi)
            for n in names:
                self._add_object(gi, des.obj(n))
            gi.setExpanded(f"objgroup:{gname}" in self._expanded)
        mats: dict[str, QTreeWidgetItem] = {}
        for o in des.live_objects():
            if o.group in grouped:
                continue
            t = self.doc.tess.get(o.name)
            dim = t.dim if t else o.dim
            if not o.model:
                self._add_object(groups["Non Model"], o)
                continue
            if dim == 3:
                if o.material not in mats:
                    mi = QTreeWidgetItem([o.material])
                    mi.setData(0, ROLE, ("material", o.material))
                    groups["Solids"].addChild(mi)
                    mi.setExpanded(f"material:{o.material}" in self._expanded or True)
                    mats[o.material] = mi
                parent = mats[o.material]
            elif dim == 2:
                parent = groups["Sheets"]
            else:
                parent = groups["Lines"]
            self._add_object(parent, o)
        cs = QTreeWidgetItem(["Coordinate Systems"])
        cs.setData(0, ROLE, ("group", "Coordinate Systems"))
        from PySide6.QtGui import QFont
        for n in des.cs_names():
            g = QTreeWidgetItem([n])
            g.setData(0, ROLE, ("cs", n))
            if n == des.working_cs:      # the working coordinate system is shown bold
                f = QFont()
                f.setBold(True)
                g.setFont(0, f)
                g.setToolTip(0, "Working coordinate system")
            cs.addChild(g)
        self.addTopLevelItem(cs)
        cs.setExpanded(True)
        pl = QTreeWidgetItem(["Planes"])
        pl.setData(0, ROLE, ("group", "Planes"))
        for p in ("Global:XY", "Global:YZ", "Global:XZ"):
            x = QTreeWidgetItem([p])
            x.setData(0, ROLE, ("plane", p))
            pl.addChild(x)
        self.addTopLevelItem(pl)
        self.blockSignals(False)
        self.sync_selection()

    def _add_object(self, parent: QTreeWidgetItem, o, depth: int = 0) -> None:
        it = QTreeWidgetItem([o.name])
        it.setData(0, ROLE, ("object", o.name))
        it.setIcon(0, color_icon(o.color))
        if o.name in self.doc.errors:
            it.setForeground(0, QBrush(QColor("#c0392b")))
            it.setToolTip(0, self.doc.errors[o.name])
        if not o.visible:
            f = it.font(0)
            f.setItalic(True)
            it.setFont(0, f)
            it.setForeground(0, QBrush(QColor("#888")))
        parent.addChild(it)
        for i, c in enumerate(o.history):
            ci = QTreeWidgetItem([c.kind + (" (suppressed)" if c.suppressed else "")])
            ci.setData(0, ROLE, ("command", o.name, i))
            if c.suppressed:
                ci.setForeground(0, QBrush(QColor("#999")))
            it.addChild(ci)
            if c.kind in ("Unite", "Subtract", "Intersect") and depth < 4:
                for tname in [t.strip() for t in c.props.get("Tools", "").split(",") if t.strip()]:
                    if self.doc.design.has_obj(tname):
                        self._add_object(ci, self.doc.design.obj(tname), depth + 1)
        it.setExpanded(f"object:{o.name}" in self._expanded)

    def sync_selection(self) -> None:
        names = set(self.doc.selected_objects())
        self._syncing = True
        self.blockSignals(True)
        self.clearSelection()

        whole = {g for g, m in G.groups(self.doc.design).items() if names.issuperset(m)}

        def walk(it):
            d = it.data(0, ROLE)
            if d and d[0] == "objgroup" and d[1] in whole and self.doc.selection_mode == "object":
                it.setSelected(True)
                it.parent().setExpanded(True)
                return                      # a whole group shows as its group item
            if d and d[0] == "object" and d[1] in names and self.doc.selection_mode == "object":
                it.setSelected(True)
                p = it.parent()
                while p is not None:
                    p.setExpanded(True)
                    p = p.parent()
            for i in range(it.childCount()):
                walk(it.child(i))
        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))
        self.blockSignals(False)
        self._syncing = False
