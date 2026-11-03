"""Results dock: sortable / filterable tables, violations, iteration log and
convergence plot.  Clicking a row emits ``element_selected(kind, id)``."""
from __future__ import annotations

import math
from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLineEdit, QPlainTextEdit, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

GREY = QBrush(QColor("#9a9a9a"))
STATUS_COL = {"under": QColor("#1f6feb"), "over": QColor("#c026d3"), "warn": QColor("#b45309"),
              "Qmax violated": QColor("#dc2626"), "Qmin violated": QColor("#dc2626")}


class NumItem(QTableWidgetItem):
    """Sorts numerically when it holds a number, otherwise as text."""

    def __init__(self, text: str, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        a, b = self.key, getattr(other, "key", other.text())
        try:
            if isinstance(a, float) and math.isnan(a):
                return False
            return a < b
        except TypeError:
            return str(a) < str(b)


class TableTab(QWidget):
    row_selected = Signal(str, int)

    def __init__(self, headers: List[str], kind_of_row):
        super().__init__()
        self.kind_of_row = kind_of_row
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter rows (type any text)\u2026")
        self.filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.itemSelectionChanged.connect(self._clicked)
        lay.addWidget(self.table)

    def set_rows(self, rows: List[tuple], stale: bool):
        """rows: (kind, id, [(text, sortkey), ...], status)"""
        t = self.table
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, (kind, eid, cells, status) in enumerate(rows):
            for c, (text, key) in enumerate(cells):
                it = NumItem(text, key)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, (kind, eid))
                if stale:
                    it.setForeground(GREY)
                elif status in STATUS_COL:
                    it.setForeground(QBrush(STATUS_COL[status]))
                t.setItem(r, c, it)
        t.setSortingEnabled(True)
        t.resizeColumnsToContents()
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text: str):
        text = text.lower().strip()
        for r in range(self.table.rowCount()):
            hit = not text or any(text in (self.table.item(r, c).text().lower() if self.table.item(r, c) else "")
                                  for c in range(self.table.columnCount()))
            self.table.setRowHidden(r, not hit)

    def _clicked(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            d = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            if d:
                self.row_selected.emit(d[0], d[1])

    def select_row_for(self, kind: str, eid: int):
        for r in range(self.table.rowCount()):
            d = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) if self.table.item(r, 0) else None
            if d == (kind, eid):
                self.table.blockSignals(True)
                self.table.selectRow(r)
                self.table.blockSignals(False)
                return


def f(v, n=3):
    return (f"{v:.{n}f}", float(v))


def f0(v):
    return ("" if v != v else f"{v:.1f}", float(v))


class ResultsDock(QTabWidget):
    element_selected = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.bus_tab = TableTab(["Bus", "Name", "Type", "V (pu)", "V (kV)", "\u03b8 (deg)", "Pg (MW)", "Qg (MVAr)",
                                 "Pd (MW)", "Qd (MVAr)", "P inj (MW)", "Q inj (MVAr)", "Limit status"], None)
        self.br_tab = TableTab(["ID", "Name", "From", "To", "Pf (MW)", "Qf (MVAr)", "Pt (MW)", "Qt (MVAr)", "Sf (MVA)",
                                "St (MVA)", "Loading (%)", "P loss (MW)", "Q loss (MVAr)", "Status"], None)
        self.gen_tab = TableTab(["Type", "ID", "Name", "Bus", "P (MW)", "Q (MVAr)", "Qmin", "Qmax", "Status"], None)
        self.vio_tab = TableTab(["Type", "ID", "Description"], None)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.fig = Figure(figsize=(5, 2.6), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)
        for w, name in ((self.bus_tab, "Bus results"), (self.br_tab, "Branch results"), (self.gen_tab, "Generators"),
                        (self.vio_tab, "Violations"), (self.log, "Iteration log"), (self.canvas, "Convergence plot")):
            self.addTab(w, name)
        for t in (self.bus_tab, self.br_tab, self.gen_tab, self.vio_tab):
            t.row_selected.connect(self.element_selected)
        self.draw_plot([])

    def show_results(self, res, stale: bool):
        if res is None:
            for t in (self.bus_tab, self.br_tab, self.gen_tab, self.vio_tab):
                t.set_rows([], False)
            return
        self.bus_tab.set_rows([("bus", b.number, [(str(b.number), b.number), (b.name, b.name), (b.bus_type, b.bus_type),
                                                  f(b.vm, 4), f(b.kv, 2), f(b.va, 3), f(b.pg, 2), f(b.qg, 2), f(b.pd, 2),
                                                  f(b.qd, 2), f(b.p_inj, 2), f(b.q_inj, 2), (b.status, b.status)], b.status)
                               for b in res.buses], stale)
        self.br_tab.set_rows([("branch", r.id, [(str(r.id), r.id), (r.name, r.name), (str(r.from_bus), r.from_bus),
                                                (str(r.to_bus), r.to_bus), f(r.pf, 2), f(r.qf, 2), f(r.pt, 2), f(r.qt, 2),
                                                f(r.sf, 2), f(r.st, 2), f0(r.loading), f(r.ploss, 3), f(r.qloss, 3),
                                                (r.status, r.status)], r.status) for r in res.branches], stale)
        self.gen_tab.set_rows([(g.kind, g.id, [(g.kind, g.kind), (str(g.id), g.id), (g.name, g.name), (str(g.bus), g.bus),
                                               f(g.p, 2), f(g.q, 2), f(g.qmin, 1), f(g.qmax, 1), (g.status, g.status)], g.status)
                               for g in res.gens], stale)
        self.vio_tab.set_rows([(k, i, [(k, k), (str(i), i), (txt, txt)], "over") for k, i, txt in res.violations], stale)

    def set_log(self, lines: List[str]):
        self.log.setPlainText("\n".join(lines))

    def draw_plot(self, history: List[float]):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        pts = [(k, math.log10(max(h, 1e-16))) for k, h in enumerate(history)]
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], "o-")
            ax.axhline(-8, color="grey", ls="--", lw=0.8)
        ax.set_xlabel("iteration")
        ax.set_ylabel("log10(max mismatch, pu)")
        ax.grid(True, alpha=0.3)
        self.canvas.draw_idle()

    def select_row_for(self, kind: str, eid: int):
        tab = {"bus": self.bus_tab, "branch": self.br_tab}.get(kind, self.gen_tab if kind in ("generator", "ext_grid") else None)
        if tab is not None:
            tab.select_row_for(kind, eid)
