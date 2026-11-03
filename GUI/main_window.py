"""Main window: wires canvas, properties, results dock, menus, undo/redo, run."""
from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict
from typing import Dict, List, Optional

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QAction, QActionGroup, QImage, QKeySequence, QPageSize, QPainter, QPdfWriter,
                           QTextDocument)
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QSpinBox, QComboBox, QVBoxLayout, QWidget)

import report
from adapter import GuiResults, StudySettings, run_study
from canvas import BUS_W, PaletteList, SldScene, SldView, snap
from check import ERROR, check_network, has_errors
from layout import auto_layout
from matpower_io import export_matpower, load_matpower
from model import ELEMENT_TABLES, Network
from props import PropertiesPanel
from results_view import ResultsDock

APP_NAME = "E4 234 Load Flow Simulator"
VERSION = "1.0"
STUDENT_NAME = "Your Name"          # <- fill in (shown in Help > About)
ROLL_NUMBER = "Your Roll Number"    # <- fill in
MAX_UNDO = 50


class SolveThread(QThread):
    done = Signal(object, str)

    def __init__(self, net, settings, prev):
        super().__init__()
        self.net, self.settings, self.prev = net, settings, prev

    def run(self):
        try:
            self.done.emit(run_study(self.net, self.settings, self.prev), "")
        except Exception as exc:  # engine errors must never kill the GUI
            self.done.emit(None, f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.net = Network(name="untitled")
        self.layout: Dict[int, tuple] = {}
        self.settings = StudySettings()
        self.results: Optional[GuiResults] = None
        self.stale = False
        self.path: Optional[str] = None
        self.dirty = False
        self.undo: List[tuple] = []
        self.redo: List[tuple] = []
        self.clip = None
        self.issues = []
        self.worker: Optional[SolveThread] = None
        self._in_refresh = False
        self.qs = QSettings("E4234", "LoadFlowSimulator")

        # ---- central canvas ------------------------------------------------
        self.scene = SldScene()
        self.view = SldView(self.scene)
        self.setCentralWidget(self.view)

        # ---- docks -----------------------------------------------------------
        self.palette = PaletteList()
        pal = QWidget()
        pl = QVBoxLayout(pal)
        pl.addWidget(self.palette)
        pl.addWidget(QLabel("drag onto canvas,\nor click, then click\nthe canvas / buses"))
        self.dock_pal = self._dock("Element palette", pal, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.props = PropertiesPanel()
        self.dock_props = self._dock("Properties", self.props, Qt.DockWidgetArea.RightDockWidgetArea)
        self.rdock = ResultsDock()
        self.dock_res = self._dock("Results", self.rdock, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.resize(1400, 850)

        self._make_actions()
        self._make_menus()
        self._make_toolbar()
        self._make_statusbar()
        self._default_state = self.saveState()

        # ---- signals ----------------------------------------------------------
        sc = self.scene
        sc.place_requested.connect(self.on_place)
        sc.attach_requested.connect(self.on_attach)
        sc.connect_requested.connect(self.on_connect)
        sc.layout_moved.connect(self.on_layout_moved)
        sc.context_action.connect(self.on_context)
        sc.item_double_clicked.connect(lambda k, i: self.focus_properties())
        sc.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        sc.selectionChanged.connect(self.on_selection)
        sc.tool_changed.connect(lambda t: self.view.setCursor(Qt.CursorShape.CrossCursor if t else Qt.CursorShape.ArrowCursor))
        self.palette.itemClicked.connect(lambda it: sc.set_tool(it.data(Qt.ItemDataRole.UserRole)))
        self.props.apply_requested.connect(self.on_apply)
        self.rdock.element_selected.connect(self.select_from_table)

        self.refresh()
        self.update_recent()

    # ================================================================ helpers
    def _dock(self, title, widget, area):
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _act(self, text, slot, keys=None, checkable=False, checked=False):
        a = QAction(text, self)
        if keys:
            a.setShortcuts([QKeySequence(k) for k in ([keys] if isinstance(keys, str) else keys)])
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
            a.toggled.connect(slot)
        else:
            a.triggered.connect(lambda _=False, s=slot: s())
        self.addAction(a)
        return a

    # ================================================================ actions
    def _make_actions(self):
        A = self._act
        self.a_new = A("New", self.new_project, "Ctrl+N")
        self.a_open = A("Open project\u2026", self.open_project, "Ctrl+O")
        self.a_save = A("Save", self.save_project, "Ctrl+S")
        self.a_saveas = A("Save as\u2026", self.save_project_as, "Ctrl+Shift+S")
        self.a_imp_m = A("Import MATPOWER (.m)\u2026", self.import_matpower)
        self.a_imp_cdf = A("Import IEEE CDF\u2026", self.import_cdf)
        self.a_exp_m = A("Export case as MATPOWER\u2026", self.export_case)
        self.a_exp_res = A("Export results (CSV/XLSX)\u2026", self.export_results)
        self.a_exp_rep = A("Export report (PDF/HTML)\u2026", self.export_report)
        self.a_exit = A("Exit", self.close, "Ctrl+Q")

        self.a_undo = A("Undo", self.do_undo, "Ctrl+Z")
        self.a_redo = A("Redo", self.do_redo, ["Ctrl+Y", "Ctrl+Shift+Z"])
        self.a_copy = A("Copy", self.copy_sel, "Ctrl+C")
        self.a_paste = A("Paste", self.paste, "Ctrl+V")
        self.a_del = A("Delete", self.delete_selected, "Del")
        self.a_all = A("Select all", self.select_all, "Ctrl+A")
        self.a_find = A("Find element\u2026", self.find_element, "Ctrl+F")

        self.a_zin = A("Zoom in", lambda: self.view.zoom(1.2), ["Ctrl++", "Ctrl+="])
        self.a_zout = A("Zoom out", lambda: self.view.zoom(1 / 1.2), "Ctrl+-")
        self.a_fit = A("Fit to window", self.view.fit, "F")
        self.a_boxes = A("Show result boxes", lambda c: self.scene.set_flags(boxes=c), None, True, True)
        self.a_color = A("Colour coding", self.toggle_color, None, True, True)
        self.a_kv = A("Show V in kV", lambda c: self.scene.set_flags(kv=c), None, True, False)
        self.a_grid = A("Show grid", lambda c: self.scene.set_flags(grid=c), None, True, True)
        self.a_reset = A("Reset panel layout", self.reset_layout)

        self.a_check = A("Check network", self.check_network_action, "Ctrl+K")
        self.a_layout = A("Auto-layout", self.do_auto_layout)
        self.a_base = A("System base MVA and frequency\u2026", self.base_dialog)

        self.a_settings = A("Load flow settings\u2026", self.settings_dialog)
        self.a_run = A("Run NR", self.run_nr, "F5")
        self.a_scale = A("Global load scaling\u2026", self.scale_dialog)
        self.a_qlim = A("Q-limit enforcement", self.toggle_qlim, None, True, False)

        self.a_tab_bus = A("Bus results", lambda: self.show_tab(self.rdock.bus_tab))
        self.a_tab_br = A("Branch results", lambda: self.show_tab(self.rdock.br_tab))
        self.a_tab_gen = A("Generator results", lambda: self.show_tab(self.rdock.gen_tab))
        self.a_loss = A("Loss summary", self.loss_summary)
        self.a_vio = A("Violations list", lambda: self.show_tab(self.rdock.vio_tab))
        self.a_conv = A("Convergence plot", lambda: self.show_tab(self.rdock.canvas))

        self.a_esc = A("Cancel tool", lambda: self.scene.set_tool(None), "Esc")

        self.a_guide = A("User guide", self.user_guide, "F1")
        self.a_about = A("About", self.about)

        self.a_select = A("Select", lambda c: c and self.view.set_mode("select"), None, True, True)
        self.a_pan = A("Pan", lambda c: c and self.view.set_mode("pan"), None, True, False)
        g = QActionGroup(self)
        g.setExclusive(True)
        g.addAction(self.a_select)
        g.addAction(self.a_pan)

    def _make_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addActions([self.a_new, self.a_open, self.a_save, self.a_saveas])
        m.addSeparator()
        m.addActions([self.a_imp_m, self.a_imp_cdf, self.a_exp_m, self.a_exp_res, self.a_exp_rep])
        m.addSeparator()
        self.recent_menu = m.addMenu("Recent files")
        m.addSeparator()
        m.addAction(self.a_exit)
        m = mb.addMenu("&Edit")
        m.addActions([self.a_undo, self.a_redo])
        m.addSeparator()
        m.addActions([self.a_copy, self.a_paste, self.a_del, self.a_all, self.a_find])
        m = mb.addMenu("&View")
        m.addActions([self.a_zin, self.a_zout, self.a_fit])
        m.addSeparator()
        m.addActions([self.a_boxes, self.a_color, self.a_kv, self.a_grid])
        m.addSeparator()
        m.addAction(self.a_reset)
        m = mb.addMenu("&Network")
        m.addActions([self.a_check, self.a_layout, self.a_base])
        m = mb.addMenu("&Study")
        m.addActions([self.a_settings, self.a_run, self.a_scale, self.a_qlim])
        m = mb.addMenu("&Results")
        m.addActions([self.a_tab_bus, self.a_tab_br, self.a_tab_gen, self.a_loss, self.a_vio, self.a_conv])
        m = mb.addMenu("&Help")
        m.addActions([self.a_guide, self.a_about])

    def _make_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setObjectName("Main toolbar")
        tb.addActions([self.a_new, self.a_open, self.a_save])
        tb.addSeparator()
        tb.addActions([self.a_undo, self.a_redo])
        tb.addSeparator()
        tb.addActions([self.a_select, self.a_pan, self.a_fit])
        tb.addSeparator()
        tb.addAction(self.a_check)
        self.a_run.setText("Run NR (F5)")
        tb.addAction(self.a_run)
        self.a_boxes.setText("Show results")
        tb.addAction(self.a_boxes)

    def _make_statusbar(self):
        sb = self.statusBar()
        self.l_status, self.l_iter, self.l_mis, self.l_time, self.l_base = (QLabel() for _ in range(5))
        for w in (self.l_status, self.l_iter, self.l_mis, self.l_time, self.l_base):
            sb.addPermanentWidget(w)

    # ================================================================ refresh
    def refresh(self):
        self._in_refresh = True
        res = self.results
        self.scene.rebuild(self.net, self.layout, res, self.stale)
        self.view.set_legend(self.scene.color_coding and res is not None and res.converged and not self.stale)
        self.rdock.show_results(res if res and res.converged else None, self.stale)
        self.issues = check_network(self.net)
        runnable = not has_errors(self.issues) and not (self.worker and self.worker.isRunning())
        self.a_run.setEnabled(runnable)
        self.a_run.setToolTip("Run Newton-Raphson load flow (F5)" if runnable else
                              "Disabled: network check failed (Network > Check network)")
        self.a_undo.setEnabled(bool(self.undo))
        self.a_redo.setEnabled(bool(self.redo))
        self._status_labels()
        self.l_base.setText(f"base {self.net.base_mva:g} MVA, {self.net.freq_hz:g} Hz")
        self.setWindowTitle(f"{APP_NAME} \u2014 {os.path.basename(self.path) if self.path else self.net.name}"
                            f"{'*' if self.dirty else ''}")
        self._in_refresh = False
        self.on_selection()

    def _status_labels(self):
        r = self.results
        if r is None:
            self.l_status.setText("Status: no results")
            self.l_iter.setText("")
            self.l_mis.setText("")
            self.l_time.setText("")
            return
        txt = "converged" if r.converged else "DIVERGED"
        if self.stale:
            txt += " (stale \u2013 network edited)"
        colour = "#b0b0b0" if self.stale else ("#15803d" if r.converged else "#dc2626")
        self.l_status.setText(f"Status: <b style='color:{colour}'>{txt}</b>")
        self.l_iter.setText(f"  iterations: {r.iterations}")
        self.l_mis.setText(f"  max mismatch: {r.max_mismatch:.2e} pu")
        self.l_time.setText(f"  solve time: {r.solve_time_ms:.2f} ms")

    # ================================================================ undo/redo
    def _snap(self):
        return (self.net.copy(), dict(self.layout))

    def _restore(self, s):
        self.net, self.layout = s[0].copy(), dict(s[1])

    def edit(self, fn, electrical: bool = True):
        s = self._snap()
        fn()
        self.undo.append(s)
        del self.undo[:-MAX_UNDO]
        self.redo.clear()
        self.dirty = True
        if electrical and self.results is not None:
            self.stale = True
        self.refresh()

    def do_undo(self):
        if self.undo:
            self.redo.append(self._snap())
            self._restore(self.undo.pop())
            self.dirty = True
            self.stale = self.results is not None
            self.refresh()

    def do_redo(self):
        if self.redo:
            self.undo.append(self._snap())
            self._restore(self.redo.pop())
            self.dirty = True
            self.stale = self.results is not None
            self.refresh()

    # ================================================================ editing
    def on_place(self, kind, pos: QPointF):
        def fn():
            n = max(self.net.buses, default=0) + 1
            self.net.add_bus(n, base_kv=110.0)
            self.layout[n] = (snap(pos.x() - BUS_W / 2), snap(pos.y()))
        self.edit(fn)

    def on_attach(self, kind: str, bus: int):
        has_gen = any(g.bus == bus for g in self.net.generators.values())
        has_ext = any(g.bus == bus for g in self.net.ext_grids.values())
        if kind in ("generator", "ext_grid") and (has_gen or has_ext):
            self.statusBar().showMessage("Only one generator / external grid per bus is supported", 6000)
            return

        def fn():
            if kind == "generator":
                self.net.add_generator(bus, p_mw=50.0, qmin_mvar=-50.0, qmax_mvar=50.0)
            elif kind == "ext_grid":
                self.net.add_ext_grid(bus)
            elif kind == "load":
                self.net.add_load(bus, p_mw=10.0, q_mvar=5.0)
            else:
                self.net.add_shunt(bus, b_mvar=10.0)
        self.edit(fn)

    def on_connect(self, kind: str, a: int, b: int):
        def fn():
            if kind == "line":
                self.net.add_branch(a, b, r_pu=0.01, x_pu=0.1, kind="line")
            else:
                self.net.add_branch(a, b, r_pu=0.0, x_pu=0.1, kind="transformer", rated_mva=100.0, rating_mva=100.0)
        self.edit(fn)

    def on_layout_moved(self):
        pos = self.scene.bus_positions()
        if pos == self.layout:
            return
        s = self._snap()
        self.layout = dict(pos)
        self.undo.append(s)
        del self.undo[:-MAX_UNDO]
        self.redo.clear()
        self.dirty = True
        self.a_undo.setEnabled(True)
        self.a_redo.setEnabled(False)
        self.setWindowTitle(self.windowTitle().rstrip("*") + "*")

    def selected(self):
        return [(i.kind, i.eid) for i in self.scene.selectedItems() if hasattr(i, "eid")]

    def delete_selected(self):
        sel = self.selected()
        if not sel:
            return

        def fn():
            for kind, eid in sel:
                if eid in self.net.table(kind):
                    self.net.remove(kind, eid)
                if kind == "bus":
                    self.layout.pop(eid, None)
        self.edit(fn)

    def on_context(self, action: str, kind: str, eid: int):
        if action == "properties":
            self.focus_properties()
        elif action == "delete":
            self.delete_selected()
        elif action == "toggle":
            sel = [(k, i) for k, i in self.selected() if k != "bus"] or [(kind, eid)]

            def fn():
                for k, i in sel:
                    e = self.net.get(k, i)
                    e.in_service = not e.in_service
            self.edit(fn)

    def on_apply(self, kind: str, eid: int, vals: dict):
        def fn():
            e = self.net.get(kind, eid)
            for k, v in vals.items():
                setattr(e, k, v)
            if kind == "branch" and e.kind == "transformer":
                e.rating_mva = e.rated_mva
        self.edit(fn)
        self.statusBar().showMessage("Applied", 3000)

    def select_all(self):
        for it in self.scene.items():
            if hasattr(it, "eid"):
                it.setSelected(True)

    def copy_sel(self):
        buses = {i for k, i in self.selected() if k == "bus"}
        if not buses:
            self.statusBar().showMessage("Select one or more buses to copy (with their attached elements)", 4000)
            return
        sub = Network(base_mva=self.net.base_mva)
        for n in buses:
            sub.buses[n] = self.net.buses[n]
        for kind in ("generator", "load", "shunt", "ext_grid"):
            for e in self.net.table(kind).values():
                if e.bus in buses:
                    sub.table(kind)[e.id] = e
        for br in self.net.branches.values():
            if br.from_bus in buses and br.to_bus in buses:
                sub.branches[br.id] = br
        self.clip = (sub.copy(), {n: self.layout.get(n, (0, 0)) for n in buses})
        self.statusBar().showMessage(f"Copied {len(buses)} bus(es)", 3000)

    def paste(self):
        if not self.clip:
            return
        sub, lay = self.clip

        def fn():
            nxt = max(self.net.buses, default=0)
            m = {}
            for n in sorted(sub.buses):
                nxt += 1
                m[n] = nxt
                b = sub.buses[n]
                nb = self.net.add_bus(nxt, name=f"{b.name} (copy)", base_kv=b.base_kv, vmin=b.vmin, vmax=b.vmax, area=b.area)
                x, y = lay[n]
                self.layout[nxt] = (x + 60, y + 60)
            for kind in ("generator", "load", "shunt", "ext_grid"):
                for e in sub.table(kind).values():
                    d = {k: v for k, v in vars(e).items() if k not in ("id", "bus")}
                    getattr(self.net, f"add_{kind}")(m[e.bus], **d)
            for br in sub.branches.values():
                d = {k: v for k, v in vars(br).items() if k not in ("id", "from_bus", "to_bus")}
                self.net.add_branch(m[br.from_bus], m[br.to_bus], **d)
        self.edit(fn)

    def find_element(self):
        text, ok = QInputDialog.getText(self, "Find element", "Name or number:")
        if not ok or not text.strip():
            return
        t = text.strip().lower()
        for kind in ("bus", "branch", "generator", "load", "shunt", "ext_grid"):
            for eid, e in self.net.table(kind).items():
                if t == str(eid) and kind == "bus" or t in e.name.lower():
                    it = self.scene.select_element(kind, eid)
                    self.view.center_on_item(it)
                    return
        self.statusBar().showMessage(f"'{text}' not found", 4000)

    # ================================================================ selection
    def on_selection(self):
        if self._in_refresh:
            return
        sel = self.selected()
        if len(sel) == 1 and sel[0][1] in self.net.table(sel[0][0]):
            self.props.show_element(self.net, *sel[0])
            self.rdock.select_row_for(*sel[0])
        elif len(sel) > 1:
            self.props.clear(f"{len(sel)} elements selected")
        else:
            self.props.clear()

    def select_from_table(self, kind, eid):
        it = self.scene.select_element(kind, eid)
        self.view.center_on_item(it)

    def focus_properties(self):
        self.dock_props.show()
        self.dock_props.raise_()

    # ================================================================ view/menu
    def toggle_color(self, on):
        self.scene.set_flags(color=on)
        self.view.set_legend(on and self.results is not None and self.results.converged and not self.stale)

    def reset_layout(self):
        self.restoreState(self._default_state)
        for d in (self.dock_pal, self.dock_props, self.dock_res):
            d.show()

    def show_tab(self, w):
        self.dock_res.show()
        self.rdock.setCurrentWidget(w)

    def loss_summary(self):
        r = self.results
        if not r or not r.converged:
            QMessageBox.information(self, "Loss summary", "Run a converged load flow first.")
            return
        QMessageBox.information(self, "Loss summary",
                                f"Total generation: {r.total_gen_mw:.3f} MW\nTotal load: {r.total_load_mw:.3f} MW\n"
                                f"Total P losses: {r.total_loss_mw:.4f} MW\nTotal Q losses: {r.total_loss_mvar:.4f} MVAr"
                                f"{'  (results are stale)' if self.stale else ''}")

    def do_auto_layout(self):
        def fn():
            self.layout = auto_layout(self.net)
        self.edit(fn, electrical=False)
        self.view.fit()

    def check_network_action(self):
        self.issues = check_network(self.net)
        if not self.issues:
            QMessageBox.information(self, "Check network", "No problems found.")
            return
        errs = [i for i in self.issues if i.severity == ERROR]
        QMessageBox.warning(self, "Check network", f"{len(errs)} error(s), {len(self.issues) - len(errs)} warning(s):\n\n"
                            + "\n".join(str(i) for i in self.issues[:25]))
        first = self.issues[0]
        if first.element_kind in ELEMENT_TABLES and first.element_id in self.net.table(first.element_kind):
            self.view.center_on_item(self.scene.select_element(first.element_kind, first.element_id))

    def base_dialog(self):
        d = QDialog(self)
        d.setWindowTitle("System base")
        form = QFormLayout(d)
        sb = QDoubleSpinBox()
        sb.setRange(0.1, 1e6)
        sb.setValue(self.net.base_mva)
        sb.setSuffix(" MVA")
        sf = QDoubleSpinBox()
        sf.setRange(1, 1000)
        sf.setValue(self.net.freq_hz)
        sf.setSuffix(" Hz")
        form.addRow("Base power", sb)
        form.addRow("Frequency", sf)
        form.addRow(QLabel("Per-unit data entered in pu is rescaled so that the physical\nvalues stay the same."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec():
            def fn():
                old, new = self.net.base_mva, sb.value()
                for br in self.net.branches.values():
                    if br.input_mode == "pu":
                        br.r_pu *= new / old
                        br.x_pu *= new / old
                        br.b_pu *= old / new
                self.net.base_mva, self.net.freq_hz = new, sf.value()
            self.edit(fn)

    def settings_dialog(self):
        d = QDialog(self)
        d.setWindowTitle("Load flow settings")
        form = QFormLayout(d)
        tol = QLineEdit(f"{self.settings.tolerance:g}")
        it = QSpinBox()
        it.setRange(1, 1000)
        it.setValue(self.settings.max_iterations)
        start = QComboBox()
        start.addItems(["Flat start", "Previous solution"])
        start.setCurrentIndex(0 if self.settings.flat_start else 1)
        ql = QCheckBox("Enforce generator Q limits")
        ql.setChecked(self.settings.enforce_q_limits)
        sc = QDoubleSpinBox()
        sc.setRange(0.0, 10.0)
        sc.setDecimals(3)
        sc.setSingleStep(0.05)
        sc.setValue(self.settings.load_scale)
        form.addRow("Tolerance (pu)", tol)
        form.addRow("Max iterations", it)
        form.addRow("Initial point", start)
        form.addRow("", ql)
        form.addRow("Global load scaling", sc)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec():
            try:
                t = float(tol.text())
                assert t > 0
            except (ValueError, AssertionError):
                QMessageBox.warning(self, "Settings", "Tolerance must be a positive number.")
                return
            self.settings = StudySettings(t, it.value(), start.currentIndex() == 0, ql.isChecked(), sc.value())
            self.a_qlim.blockSignals(True)
            self.a_qlim.setChecked(self.settings.enforce_q_limits)
            self.a_qlim.blockSignals(False)
            self.dirty = True
            self.stale = self.results is not None

            self.refresh()

    def scale_dialog(self):
        v, ok = QInputDialog.getDouble(self, "Global load scaling", "Scale all loads by:", self.settings.load_scale, 0.0, 10.0, 3)
        if ok:
            self.settings.load_scale = v
            self.dirty = True
            self.stale = self.results is not None
            self.refresh()

    def toggle_qlim(self, on):
        self.settings.enforce_q_limits = on
        self.dirty = True
        self.stale = self.results is not None
        self.refresh()

    # ================================================================ run
    def run_nr(self):
        if self.worker and self.worker.isRunning():
            return
        self.issues = check_network(self.net)
        if has_errors(self.issues):
            self.check_network_action()
            return
        self.l_status.setText("Status: <b>running\u2026</b>")
        self.a_run.setEnabled(False)
        self.worker = SolveThread(self.net.copy(), StudySettings(**asdict(self.settings)), self.results)
        self.worker.done.connect(self.on_solved)
        self.worker.start()

    def on_solved(self, res, err: str):
        if res is None:
            self.rdock.set_log([f"ERROR: {err}"])
            self.show_tab(self.rdock.log)
            QMessageBox.critical(self, "Load flow error", err)
            self.refresh()
            return
        self.results, self.stale = res, False
        self.rdock.set_log(res.log)
        self.rdock.draw_plot(res.mismatch_history)
        self.refresh()
        if res.converged:
            self.statusBar().showMessage("Converged", 4000)
        else:
            self.show_tab(self.rdock.log)
            QMessageBox.warning(self, "Load flow diverged",
                                f"{res.message}\nIterations: {res.iterations}\nLast max mismatch: {res.max_mismatch:.3e} pu\n"
                                f"Buses with largest mismatch (highlighted red): {res.worst_buses}")

    # ================================================================ files
    def maybe_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to the project?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save_project()
        return r == QMessageBox.StandardButton.Discard

    def _reset_project(self, net, layout, settings=None, path=None):
        self.net, self.layout, self.path = net, layout, path
        self.settings = settings or StudySettings()
        self.results, self.stale, self.dirty = None, False, False
        self.undo.clear()
        self.redo.clear()
        self.a_qlim.blockSignals(True)
        self.a_qlim.setChecked(self.settings.enforce_q_limits)
        self.a_qlim.blockSignals(False)
        self.rdock.set_log([])
        self.rdock.draw_plot([])
        self.refresh()
        self.view.fit()

    def new_project(self):
        if self.maybe_discard():
            self._reset_project(Network(name="untitled"), {})

    def save_project(self) -> bool:
        if not self.path:
            return self.save_project_as()
        return self._write(self.path)

    def save_project_as(self) -> bool:
        p, _ = QFileDialog.getSaveFileName(self, "Save project", self.path or "project.json", "Project (*.json)")
        if not p:
            return False
        return self._write(p if p.endswith(".json") else p + ".json")

    def _write(self, p: str) -> bool:
        try:
            data = {"version": 1, "network": self.net.to_dict(),
                    "layout": {str(k): list(v) for k, v in self.layout.items()}, "settings": self.settings.to_dict()}
            with open(p, "w") as fh:
                json.dump(data, fh, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.path, self.dirty = p, False
        self.add_recent(p)
        self.refresh()
        return True

    def open_project(self, path: Optional[str] = None):
        if not self.maybe_discard():
            return
        p = path or QFileDialog.getOpenFileName(self, "Open project", "", "Project (*.json)")[0]
        if not p:
            return
        try:
            with open(p) as fh:
                d = json.load(fh)
            net = Network.from_dict(d["network"])
            lay = {int(k): tuple(v) for k, v in d.get("layout", {}).items()}
            st = StudySettings.from_dict(d.get("settings", {}))
        except (OSError, KeyError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Open failed", f"Could not read project:\n{exc}")
            return
        for n in net.buses:
            lay.setdefault(n, (0.0, 0.0))
        self._reset_project(net, lay, st, p)
        self.add_recent(p)

    def import_matpower(self):
        if not self.maybe_discard():
            return
        p, _ = QFileDialog.getOpenFileName(self, "Import MATPOWER case", "", "MATPOWER (*.m)")
        if not p:
            return
        try:
            net, warns = load_matpower(p)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"{type(exc).__name__}: {exc}")
            return
        self._reset_project(net, auto_layout(net))
        self.dirty = True
        self.refresh()
        self.view.fit()
        if warns:
            self.rdock.set_log(["Import notes:"] + warns)
            self.statusBar().showMessage(f"Imported with {len(warns)} note(s) - see Iteration log", 8000)

    def import_cdf(self):
        QMessageBox.information(self, "Import IEEE CDF",
                                "IEEE CDF import is not implemented in this version.\n"
                                "Convert the file to MATPOWER (.m) and use File > Import MATPOWER.")

    def export_case(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export as MATPOWER", f"{self.net.name}.m", "MATPOWER (*.m)")
        if p:
            export_matpower(self.net, p)

    def _need_results(self) -> bool:
        if self.results is None or not self.results.converged:
            QMessageBox.information(self, "No results", "Run a converged load flow first.")
            return False
        if self.stale:
            QMessageBox.information(self, "Stale results", "The network was edited after the last run; exporting the previous results.")
        return True

    def export_results(self):
        if not self._need_results():
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export results", "results.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if not p:
            return
        try:
            (report.export_xlsx if p.lower().endswith(".xlsx") else report.export_csv)(self.results, p)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _diagram_image(self) -> QImage:
        self.scene.clearSelection()
        r = self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        k = 1.5
        img = QImage(max(1, int(r.width() * k)), max(1, int(r.height() * k)), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.white)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene.render(p, QRectF(img.rect()), r)
        p.end()
        return img

    def export_report(self):
        if not self._need_results():
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export report", "report.html", "HTML (*.html);;PDF (*.pdf)")
        if not p:
            return
        img = self._diagram_image()
        try:
            if p.lower().endswith(".pdf"):
                doc = QTextDocument()
                doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("diagram.png"), img)
                doc.setHtml(report.build_html(self.net, self.results, self.settings, "diagram.png"))
                w = QPdfWriter(p)
                w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
                doc.print_(w)
            else:
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                img.save(buf, "PNG")
                uri = "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode()
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(report.build_html(self.net, self.results, self.settings, uri))
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    # ---- recent files --------------------------------------------------------
    def add_recent(self, p: str):
        lst = [x for x in self.qs.value("recent", [], type=list) if x != p]
        self.qs.setValue("recent", ([p] + lst)[:5])
        self.update_recent()

    def update_recent(self):
        self.recent_menu.clear()
        for p in self.qs.value("recent", [], type=list):
            self.recent_menu.addAction(p, lambda p=p: self.open_project(p))
        self.recent_menu.setEnabled(not self.recent_menu.isEmpty())

    # ================================================================ help
    def user_guide(self):
        QMessageBox.information(self, "User guide", (
            "BUILD:  drag an element from the palette onto the canvas (or click it, then click).\n"
            "  - Bus: drop on canvas.   Generator / load / shunt / external grid: drop onto a bus.\n"
            "  - Line / transformer: click the palette item, then click two buses.\n"
            "  - One external grid (slack) per island; one generator per bus.\n"
            "EDIT:   click an element, change fields in Properties, press Apply. Right-click for\n"
            "  delete / out-of-service. Ctrl+Z / Ctrl+Y undo / redo.\n"
            "RUN:    Network > Check network (Ctrl+K), then Run NR (F5). Settings under Study.\n"
            "RESULTS: boxes on the diagram, tables below (click a row to select the element).\n"
            "  Colours: blue = under-voltage, magenta = over-voltage, amber 80-100 %, red > 100 %.\n"
            "  Editing the network greys out results until the next run.\n"
            "FILES:  Save/Open (JSON), Import MATPOWER, export results (CSV/XLSX) and report (PDF/HTML)."))

    def about(self):
        QMessageBox.about(self, "About", f"{APP_NAME}\nVersion {VERSION}\n\nStudent: {STUDENT_NAME}\nRoll number: {ROLL_NUMBER}\n"
                                         "E4 234 Advanced Power System Analysis")

    def closeEvent(self, ev):
        if self.maybe_discard():
            ev.accept()
        else:
            ev.ignore()
