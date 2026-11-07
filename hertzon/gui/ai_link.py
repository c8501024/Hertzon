"""Tools > AI Assistants: the live link for the Hertzon MCP server and the client registration dialog.

With the link on, the window listens on 127.0.0.1 (random port, random token in
~/.hertzon/links/<pid>.json, readable only by this user). The MCP server started by Claude Code,
Codex or VS Code then edits the open project through this window (with undo) and queues analyses
in its solve manager, so everything the assistant does is visible here.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton, QGridLayout,
                               QGroupBox, QPlainTextEdit, QDialogButtonBox, QMessageBox, QApplication)

from ..mcp import clients
from ..mcp.live import link_dir


def removed_clients(settings) -> list[str]:
    """Assistants the user removed Hertzon from in the dialog; the live link does not register those again."""
    v = settings.value("ai/removed", [])
    return [v] if isinstance(v, str) else list(v or [])


def show_restart_note(win, keys: list[str]) -> None:
    """The assistants read their server list only when they start: say what to restart, per assistant."""
    names = ", ".join(clients.TITLES[k] for k in keys)
    steps = "\n\n".join(clients.RESTART[k] for k in keys)
    win.msgs.add("info", f"Hertzon server registered for {names}; restart the assistant to use Hertzon there")
    QMessageBox.information(win, "AI Assistants",
                            f"Hertzon is now registered for {names}.\n\nTo use it, restart the assistant:\n\n{steps}"
                            "\n\nThen ask it, for example: \"Are you connected to Hertzon?\"")


class AiLink(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.server: QTcpServer | None = None
        self.token = ""
        self.file: Path | None = None
        self._busy = False
        self._bufs: dict = {}

    @property
    def running(self) -> bool:
        return self.server is not None

    def start(self, auto_register: bool = True) -> bool:
        if self.server is not None:
            return True
        srv = QTcpServer(self)
        if not srv.listen(QHostAddress.LocalHost, 0):
            self.win.msgs.add("error", f"AI assistant link: cannot listen ({srv.errorString()})")
            return False
        srv.newConnection.connect(self._on_connection)
        self.server = srv
        self.token = secrets.token_hex(16)
        d = link_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.file = d / f"{os.getpid()}.json"
        self.file.write_text(json.dumps({"port": srv.serverPort(), "token": self.token, "pid": os.getpid(),
                                         "started": time.time()}), encoding="utf-8")
        try:
            os.chmod(self.file, 0o600)
        except OSError:
            pass
        self.win.msgs.add("info", "AI assistant link on: MCP clients can work in this window")
        if auto_register:
            QTimer.singleShot(0, self._auto_register)
        return True

    def _auto_register(self) -> None:
        skip = removed_clients(self.win.settings)
        try:
            done = clients.auto_register(skip)
        except Exception as e:  # noqa: BLE001
            self.win.msgs.add("warning", f"AI assistants: registering the Hertzon server failed ({e})")
            return
        if done:
            show_restart_note(self.win, done)

    def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server.deleteLater()
            self.server = None
        if self.file is not None:
            try:
                self.file.unlink(missing_ok=True)
            except OSError:
                pass
            self.file = None

    # ---- transport ----------------------------------------------------------------------------
    def _on_connection(self) -> None:
        while self.server is not None and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._bufs[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_read(s))
            sock.disconnected.connect(lambda s=sock: (self._bufs.pop(s, None), s.deleteLater()))

    def _on_read(self, sock) -> None:
        self._bufs[sock] = self._bufs.get(sock, b"") + bytes(sock.readAll())
        if b"\n" not in self._bufs[sock]:
            return
        line = self._bufs[sock].split(b"\n", 1)[0]
        self._bufs[sock] = b""
        try:
            req = json.loads(line.decode("utf-8"))
            if not secrets.compare_digest(str(req.get("token", "")), self.token):
                raise PermissionError("bad token")
            if self._busy:
                raise RuntimeError("Hertzon is busy with another request; try again")
            self._busy = True
            try:
                res = {"ok": True, "result": self.handle(req.get("cmd", ""), req.get("args") or {})}
            finally:
                self._busy = False
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": f"{e}"}
        sock.write((json.dumps(res, default=str) + "\n").encode("utf-8"))
        sock.flush()
        sock.disconnectFromHost()

    # ---- commands -----------------------------------------------------------------------------
    def handle(self, cmd: str, a: dict):
        w, doc = self.win, self.win.doc
        if cmd == "hello":
            sv = w.solver
            return {"path": doc.project.path, "name": doc.project.name, "design": doc.design_name,
                    "designs": [d.name for d in doc.project.designs], "dirty": doc.dirty,
                    "solving": sv.job.key if sv.busy and sv.job else None, "queue": [j.key for j in sv.queue],
                    "pid": os.getpid()}
        if cmd == "get_project":
            return {"path": doc.project.path, "design": doc.design_name, "project": doc.project.to_dict()}
        if cmd == "set_project":
            doc.checkpoint()
            doc._restore(a["project"])
            des = a.get("design")
            if des and des != doc.design_name and any(d.name == des for d in doc.project.designs):
                doc.set_design(des)
            w.msgs.add("info", f"AI assistant: {a.get('label') or 'changed the project'} (Ctrl+Z to undo)")
            return {"errors": dict(doc.errors)}
        if cmd == "open_project":
            if doc.dirty:
                raise RuntimeError("the window has unsaved changes")
            w.file_open(a["path"])
            return {"path": doc.project.path}
        if cmd == "save":
            path = a.get("path")
            if path:
                if not path.lower().endswith(".hzn"):
                    path += ".hzn"
                doc.project.name = Path(path).stem
                doc.project.save(path)
                doc.dirty = False
                w._add_recent(path)
                w._update_title()
                w.project_tree.rebuild()
                return path
            if not doc.project.path:
                raise RuntimeError("the project has no file yet; pass a path")
            w.file_save()
            return doc.project.path
        if cmd == "solve":
            from ..model.validate import validate
            from .solve import Job
            des = a.get("design") or doc.design_name
            if des != doc.design_name:
                doc.set_design(des)
            if not doc.project.path:
                raise RuntimeError("save the project first (save_project with a path)")
            if not a.get("sweep"):
                bad = [c.message for c in validate(doc.project, doc.design, doc.tess, doc.errors,
                                                        w.solid_overlaps()) if c.level == "error"]
                if bad:
                    raise RuntimeError("validation failed: " + "; ".join(bad))
            if doc.dirty:
                w.file_save()
            job = Job(doc.project.path, des, a.get("setup") or "", mesh_only=bool(a.get("mesh_only")),
                      sweep=a.get("sweep") or "", optimization=a.get("optimization") or "",
                      parametric=a.get("parametric") or "")
            w.solver.submit(job)
            w.project_tree.rebuild()
            return {"key": job.key}
        if cmd == "open_report":
            # MCP set_report(open=true): the report as a tab of the window
            des = a.get("design") or doc.design_name
            if des != doc.design_name:
                doc.set_design(des)
            if not any(r.name == a["name"] for r in doc.design.reports):
                raise RuntimeError(f"no report named {a['name']!r}")
            w.open_report(a["name"])
            return {"opened": a["name"]}
        if cmd == "queue":
            sv = w.solver
            return {"running": sv.job.key if sv.busy and sv.job else None, "queued": [j.key for j in sv.queue]}
        if cmd == "abort":
            sv, key = w.solver, a.get("key", "")
            if sv.busy and sv.job and sv.job.key == key:
                sv.abort()
                return {"aborted": True}
            if any(j.key == key for j in sv.queue):
                sv.remove(key)
                return {"removed_from_queue": True}
            return {"aborted": False}
        if cmd == "tuning":
            # derivative tuning model of a solved setup (MCP compute_tuning)
            from .solve import Job
            from .trees import solution_state
            des = a.get("design") or doc.design_name
            if des != doc.design_name:
                doc.set_design(des)
            if solution_state(doc, a["setup"]) != "done":
                raise RuntimeError(f"analyze {a['setup']} first (with the current design)")
            if not doc.project.path:
                raise RuntimeError("save the project first")
            if doc.dirty:
                w.file_save()
            job = Job(doc.project.path, des, a["setup"], tuning=a["tuning"])
            w.solver.submit(job)
            return {"key": job.key}
        if cmd == "apply_evaluation":
            # an optimization result into the design (MCP apply_optimization), as Apply to Design
            des = a.get("design") or doc.design_name
            if des != doc.design_name:
                doc.set_design(des)
            row = a["row"]
            w.apply_evaluation(a["optimization"], row, f"evaluation {row.get('eval')} of {a['optimization']} "
                                                       "(AI assistant)")
            return {"applied": row.get("variables")}
        if cmd == "analyze_circuit":
            # a circuit is solved at once in the window (MCP run_simulation on a circuit design)
            import time as _time
            from ..solver.circuit import CircuitError, run_setup
            des = a.get("design") or doc.design_name
            if des != doc.design_name:
                doc.set_design(des)
            if not doc.project.path:
                raise RuntimeError("save the project first (save_project with a path)")
            if doc.dirty:
                w.file_save()
            if not w._solve_cross_sections():
                raise RuntimeError("the cross-sections of the full-wave line model could not be solved")
            out = {}
            for s in a.get("setups") or [x.name for x in doc.design.setups]:
                t = _time.time()
                try:
                    _, warns = run_setup(doc.project, doc.design, doc.design.setup(s))
                except CircuitError as e:
                    w.msgs.add("error", f"{des} {s}: {e}")
                    raise RuntimeError(f"{s}: {e}") from None
                for m in warns:
                    w.msgs.add("warning", f"{des}: {m}")
                w.msgs.add("info", f"{des} {s}: circuit solved in {_time.time() - t:.2f} s (AI assistant)")
                out[s] = {"state": "done", "seconds": round(_time.time() - t, 2), **({"warnings": warns} if warns else {})}
            w.project_tree.rebuild()
            w._refresh_reports()
            return out
        raise RuntimeError(f"unknown command {cmd!r}")


class AiAssistantsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("AI Assistants (MCP)")
        self.resize(760, 620)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Hertzon includes an MCP server. Registered with Claude Code, Codex or VS Code, the assistant can "
            "build models, assign ports and boundaries, run analyses and read the results, for example "
            "\"build a 2.45 GHz patch antenna on FR4 and give me S11 and the gain\".")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.live = QCheckBox("Let AI assistants work in this window (live link: changes appear here, Ctrl+Z undoes "
                              "them, analyses run in this solve queue). Registers Hertzon with the installed "
                              "assistants.")
        self.live.setChecked(win.ai_link.running)
        self._quiet = False
        self.live.toggled.connect(self._toggle_live)
        lay.addWidget(self.live)
        box = QGroupBox("Register the Hertzon server")
        g = QGridLayout(box)
        self.status = {}
        for r, c in enumerate(clients.CLIENTS):
            g.addWidget(QLabel(clients.TITLES[c]), r, 0)
            st = QLabel("checking ...")
            self.status[c] = st
            g.addWidget(st, r, 1)
            b1 = QPushButton("Register")
            b1.clicked.connect(lambda _=False, c=c: self._do(c, True))
            b2 = QPushButton("Remove")
            b2.clicked.connect(lambda _=False, c=c: self._do(c, False))
            g.addWidget(b1, r, 2)
            g.addWidget(b2, r, 3)
        g.setColumnStretch(1, 1)
        lay.addWidget(box)
        lay.addWidget(QLabel("Manual configuration (other clients, or if registering fails):"))
        self.text = QPlainTextEdit(clients.snippets())
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        cp = QPushButton("Copy")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        row.addWidget(cp)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        QTimer.singleShot(50, self._refresh)

    def _toggle_live(self, on: bool) -> None:
        self.win.settings.setValue("ai/live_link", on)
        if on:
            self.win.ai_link.start(auto_register=not self._quiet)
            QTimer.singleShot(300, self._refresh)
        else:
            self.win.ai_link.stop()
            self.win.msgs.add("info", "AI assistant link off")

    def _refresh(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for c, lab in self.status.items():
                try:
                    lab.setText(clients.status(c))
                except Exception as e:  # noqa: BLE001
                    lab.setText(f"unknown ({e})")
        finally:
            QApplication.restoreOverrideCursor()

    def _do(self, client: str, install: bool) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            msg = (clients.install if install else clients.remove)(client)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, clients.TITLES[client], str(e))
            self._refresh()
            return
        QApplication.restoreOverrideCursor()
        removed = set(removed_clients(self.win.settings))
        (removed.discard if install else removed.add)(client)
        self.win.settings.setValue("ai/removed", sorted(removed))
        if install:
            keys = [client]
            if not self.live.isChecked():      # the link registers the other installed assistants too
                self._quiet = True
                self.live.setChecked(True)
                self._quiet = False
                skip = set(removed_clients(self.win.settings)) | {client}
                keys += clients.auto_register(skip)
            show_restart_note(self.win, keys)
        else:
            self.win.msgs.add("info", msg)
            QMessageBox.information(self, clients.TITLES[client], msg)
        self._refresh()
