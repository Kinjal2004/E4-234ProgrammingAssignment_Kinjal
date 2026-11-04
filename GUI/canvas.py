"""Single-line-diagram canvas (PySide6 / QGraphicsScene).

The scene is a *view* of the network: it never edits the model.  User gestures
are turned into signals (place / attach / connect / layout_moved / context
action); the main window mutates the model (with undo) and calls ``rebuild``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QDrag, QFont, QFontMetricsF, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QTransform)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QLabel, QListWidget,
                               QListWidgetItem, QMenu)

GRID = 20.0
BUS_W = 80.0
MIME = "application/x-sld-kind"

COL_UNDER = QColor("#1f6feb")     # undervoltage  (blue)
COL_OVER = QColor("#c026d3")      # overvoltage   (magenta)
COL_WARN = QColor("#f59e0b")      # branch 80-100 % (amber)
COL_OVERLOAD = QColor("#dc2626")  # branch > 100 % (red)
COL_OOS = QColor("#8a8a8a")       # out of service (dashed grey)
COL_NORMAL = QColor("#111111")
COL_STALE = QColor("#b0b0b0")

ATTACH_KINDS = ("generator", "load", "shunt", "ext_grid")
BRANCH_TOOLS = ("line", "transformer")
PALETTE = [("Bus", "bus"), ("Line", "line"), ("Transformer", "transformer"), ("Generator", "generator"),
           ("Load", "load"), ("Shunt", "shunt"), ("External grid", "ext_grid")]


def snap(v: float) -> float:
    return round(v / GRID) * GRID


# ----------------------------------------------------------------------- items
class ResultBox(QGraphicsItem):
    def __init__(self, parent):
        super().__init__(parent)
        self.lines: List[str] = []
        self.fg = COL_NORMAL
        self.setZValue(5)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._font = QFont("Sans Serif", 7)

    def set(self, lines: List[str], fg: QColor):
        self.prepareGeometryChange()
        self.lines, self.fg = lines, fg
        self.setVisible(bool(lines))
        self.update()

    def boundingRect(self) -> QRectF:
        if not self.lines:
            return QRectF()
        fm = QFontMetricsF(self._font)
        return QRectF(0, 0, max(fm.horizontalAdvance(s) for s in self.lines) + 8, fm.height() * len(self.lines) + 4)

    def paint(self, p: QPainter, opt, widget=None):
        if not self.lines:
            return
        r = self.boundingRect()
        p.setPen(QPen(QColor("#999999"), 0.8))
        p.setBrush(QBrush(QColor(255, 255, 235, 235)))
        p.drawRoundedRect(r, 3, 3)
        p.setFont(self._font)
        p.setPen(self.fg)
        fm = QFontMetricsF(self._font)
        for i, s in enumerate(self.lines):
            p.drawText(QPointF(4, 2 + fm.ascent() + i * fm.height()), s)


class BusItem(QGraphicsRectItem):
    kind = "bus"

    def __init__(self, number: int, name: str):
        super().__init__(0, -3, BUS_W, 6)
        self.eid = number
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(COL_NORMAL))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(2)
        self.label = QGraphicsSimpleTextItem(f"{number}: {name}", self)
        self.label.setFont(QFont("Sans Serif", 8))
        self.label.setPos(BUS_W + 6, -22)
        self.box = ResultBox(self)
        self.box.setPos(BUS_W + 6, -6)

    def center_x(self) -> float:
        return self.pos().x() + BUS_W / 2

    def itemChange(self, change, value):
        sc = self.scene()
        if sc is not None and not getattr(sc, "building", True):
            if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
                return QPointF(snap(value.x()), snap(value.y()))
            if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
                sc.bus_moved(self)
        return super().itemChange(change, value)


class BranchItem(QGraphicsPathItem):
    def __init__(self, scene_: "SldScene", bid: int, a: int, b: int, kind: str, k: int, n: int):
        super().__init__()
        self.eid, self.a, self.b, self.sym, self.k, self.n = bid, a, b, kind, k, n
        self.kind = "branch"
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(1)
        self.box = ResultBox(self)
        self._scene = scene_
        self._mid = QPointF()
        self.update_path()

    def update_path(self):
        ia, ib = self._scene.bus_items.get(self.a), self._scene.bus_items.get(self.b)
        if ia is None or ib is None:
            return
        off = (self.k - (self.n - 1) / 2.0) * 8.0
        xa = min(max(ib.center_x() + off, ia.pos().x() + 8), ia.pos().x() + BUS_W - 8)
        xb = min(max(ia.center_x() + off, ib.pos().x() + 8), ib.pos().x() + BUS_W - 8)
        ya, yb = ia.pos().y(), ib.pos().y()
        ym = (ya + yb) / 2 + off if abs(ya - yb) > 30 else min(ya, yb) - 40 - abs(off) * 1.5
        path = QPainterPath(QPointF(xa, ya))
        for pt in (QPointF(xa, ym), QPointF(xb, ym), QPointF(xb, yb)):
            path.lineTo(pt)
        mid = QPointF((xa + xb) / 2, ym)
        self._mid = mid
        if self.sym == "transformer":
            d = QPointF(5, 0) if abs(xa - xb) > 1 else QPointF(0, 5)
            path.addEllipse(mid - d, 7, 7)
            path.addEllipse(mid + d, 7, 7)
        self.prepareGeometryChange()
        self.setPath(path)
        self.box.setPos(mid + QPointF(10, 6))

    def shape(self) -> QPainterPath:
        s = QPainterPathStroker()
        s.setWidth(10)
        return s.createStroke(self.path())


class AttachItem(QGraphicsItem):
    """Generator / load / shunt / external grid symbol hanging off a bus."""

    def __init__(self, kind: str, eid: int, bus: int, up: bool):
        super().__init__()
        self.kind, self.eid, self.bus, self.up = kind, eid, bus, up
        self.in_service = True
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(3)

    def boundingRect(self) -> QRectF:
        return QRectF(-16, -62, 32, 62) if self.up else QRectF(-16, 0, 32, 62)

    def paint(self, p: QPainter, opt, widget=None):
        col = COL_OOS if not self.in_service else COL_NORMAL
        pen = QPen(col, 1.6)
        if not self.in_service:
            pen.setStyle(Qt.PenStyle.DashLine)
        if self.isSelected():
            pen = QPen(QColor("#1f6feb"), 2.4)
        p.setPen(pen)
        p.setBrush(QBrush(QColor("white")))
        s = -1 if self.up else 1
        p.drawLine(QPointF(0, 0), QPointF(0, s * 26))
        c = QPointF(0, s * 38)
        if self.kind == "generator":
            p.drawEllipse(c, 12, 12)
            p.setFont(QFont("Sans Serif", 8, QFont.Weight.Bold))
            p.drawText(QRectF(c.x() - 12, c.y() - 12, 24, 24), Qt.AlignmentFlag.AlignCenter, "G")
        elif self.kind == "ext_grid":
            p.drawRect(QRectF(c.x() - 13, c.y() - 11, 26, 22))
            p.setFont(QFont("Sans Serif", 7, QFont.Weight.Bold))
            p.drawText(QRectF(c.x() - 13, c.y() - 11, 26, 22), Qt.AlignmentFlag.AlignCenter, "Grid")
        elif self.kind == "load":
            tri = QPainterPath(QPointF(-10, 26))
            tri.lineTo(10, 26)
            tri.lineTo(0, 46)
            tri.closeSubpath()
            p.drawPath(tri)
        else:  # shunt: capacitor to ground
            p.drawLine(QPointF(-10, 30), QPointF(10, 30))
            p.drawLine(QPointF(-10, 36), QPointF(10, 36))
            p.drawLine(QPointF(0, 36), QPointF(0, 44))
            p.drawLine(QPointF(-8, 44), QPointF(8, 44))
            p.drawLine(QPointF(-5, 47), QPointF(5, 47))


# ----------------------------------------------------------------------- scene
class SldScene(QGraphicsScene):
    place_requested = Signal(str, QPointF)
    attach_requested = Signal(str, int)
    connect_requested = Signal(str, int, int)
    layout_moved = Signal()
    context_action = Signal(str, str, int)
    item_double_clicked = Signal(str, int)
    message = Signal(str)
    tool_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.building = True
        self.bus_items: Dict[int, BusItem] = {}
        self.branch_items: Dict[int, BranchItem] = {}
        self.attach_items: List[AttachItem] = []
        self.tool: Optional[str] = None
        self.pending: Optional[int] = None
        self.show_boxes = True
        self.color_coding = True
        self.show_kv = False
        self.show_grid = True
        self._moved = False
        self.setBackgroundBrush(QBrush(QColor("white")))
        self._net = self._results = None
        self._stale = False

    # ---- build -------------------------------------------------------------
    def rebuild(self, net, layout, results, stale: bool):
        keep = {(i.kind, i.eid) for i in self.selectedItems() if hasattr(i, "eid")}
        self.building = True
        self.clear()
        self.bus_items, self.branch_items, self.attach_items = {}, {}, []
        self._net, self._results, self._stale = net, results, stale
        for n, b in net.buses.items():
            it = BusItem(n, b.name)
            self.addItem(it)
            it.setPos(*layout.get(n, (0.0, 0.0)))
            self.bus_items[n] = it
        groups: Dict[frozenset, List] = {}
        for br in sorted(net.branches.values(), key=lambda x: x.id):
            groups.setdefault(frozenset((br.from_bus, br.to_bus)), []).append(br)
        for brs in groups.values():
            for k, br in enumerate(brs):
                if br.from_bus in self.bus_items and br.to_bus in self.bus_items:
                    it = BranchItem(self, br.id, br.from_bus, br.to_bus, br.kind, k, len(brs))
                    self.addItem(it)
                    self.branch_items[br.id] = it
        slots: Dict[tuple, int] = {}
        for kind in ATTACH_KINDS:
            for e in sorted(net.table(kind).values(), key=lambda x: x.id):
                if e.bus not in self.bus_items:
                    continue
                up = kind in ("generator", "ext_grid")
                k = slots.get((e.bus, up), 0)
                slots[(e.bus, up)] = k + 1
                it = AttachItem(kind, e.id, e.bus, up)
                it.in_service = e.in_service
                self.addItem(it)
                it.setPos(self.bus_items[e.bus].pos() + QPointF(14 + 30 * k, 0))
                it.setToolTip(f"{e.name}")
                self.attach_items.append(it)
        self.building = False
        self.apply_visuals()
        for it in self.items():
            if hasattr(it, "eid") and (it.kind, it.eid) in keep:
                it.setSelected(True)
        r = self.itemsBoundingRect().adjusted(-200, -150, 200, 150)
        self.setSceneRect(r.united(QRectF(-400, -300, 1200, 800)))

    def bus_positions(self) -> Dict[int, tuple]:
        return {n: (it.pos().x(), it.pos().y()) for n, it in self.bus_items.items()}

    def bus_moved(self, item: BusItem):
        self._moved = True
        for br in self.branch_items.values():
            if item.eid in (br.a, br.b):
                br.update_path()
        for at in self.attach_items:
            if at.bus == item.eid:
                idx = [x for x in self.attach_items if x.bus == item.eid and x.up == at.up].index(at)
                at.setPos(item.pos() + QPointF(14 + 30 * idx, 0))

    # ---- visuals -----------------------------------------------------------
    def apply_visuals(self):
        net, res, stale = self._net, self._results, self._stale
        live = res is not None and res.converged
        cc = self.color_coding and live and not stale
        fg = COL_STALE if stale else COL_NORMAL
        for n, it in self.bus_items.items():
            col, lines = COL_NORMAL, []
            row = res.bus(n) if live else None
            if row is not None:
                v = f"V = {row.kv:.2f} kV" if self.show_kv else f"V = {row.vm:.3f} pu"
                lines = [v, f"\u03b8 = {row.va:.2f}\u00b0"]
                if cc and row.status == "under":
                    col = COL_UNDER
                elif cc and row.status == "over":
                    col = COL_OVER
            it.setBrush(QBrush(COL_STALE if stale and live else col))
            if res is not None and not res.converged and n in res.worst_buses:
                it.setPen(QPen(COL_OVERLOAD, 3))
                it.setBrush(QBrush(COL_OVERLOAD))
            else:
                it.setPen(QPen(Qt.PenStyle.NoPen))
            it.box.set(lines if self.show_boxes else [], fg)
        for bid, it in self.branch_items.items():
            br = net.branches[bid]
            col, lines = COL_NORMAL, []
            pen_style = Qt.PenStyle.SolidLine
            row = res.branch(bid) if live else None
            if not br.in_service:
                col, pen_style = COL_OOS, Qt.PenStyle.DashLine
            elif row is not None:
                send = (row.pf, row.qf, row.from_bus, row.to_bus) if row.pf >= 0 else (row.pt, row.qt, row.to_bus, row.from_bus)
                lines = [f"{send[2]} \u2192 {send[3]}", f"P {abs(send[0]):.1f} MW", f"Q {send[1]:+.1f} MVAr"]
                if row.loading == row.loading:
                    lines.append(f"load {row.loading:.0f} %")
                if cc and row.status == "warn":
                    col = COL_WARN
                elif cc and row.status == "over":
                    col = COL_OVERLOAD
                if stale:
                    col = COL_STALE
            pen = QPen(col, 2.2)
            pen.setStyle(pen_style)
            it.setPen(pen)
            it.box.set(lines if self.show_boxes else [], fg)
        for at in self.attach_items:
            at.update()

    def set_flags(self, boxes=None, color=None, kv=None, grid=None):
        if boxes is not None:
            self.show_boxes = boxes
        if color is not None:
            self.color_coding = color
        if kv is not None:
            self.show_kv = kv
        if grid is not None:
            self.show_grid = grid
        if self._net is not None:
            self.apply_visuals()
        self.invalidate(self.sceneRect(), QGraphicsScene.SceneLayer.BackgroundLayer)

    def drawBackground(self, p: QPainter, rect: QRectF):
        super().drawBackground(p, rect)
        if not self.show_grid:
            return
        minor = QPen(QColor("#e6e6e6"), 0)      # width 0 = always 1 px, whatever the zoom
        major = QPen(QColor("#c4c4c4"), 0)      # every 5th line, darker
        x = int(rect.left() // GRID) * GRID
        while x < rect.right():
            p.setPen(major if int(round(x / GRID)) % 5 == 0 else minor)
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += GRID
        y = int(rect.top() // GRID) * GRID
        while y < rect.bottom():
            p.setPen(major if int(round(y / GRID)) % 5 == 0 else minor)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += GRID
    # ---- tools -------------------------------------------------------------
    def set_tool(self, tool: Optional[str]):
        self.tool, self.pending = tool, None
        self.tool_changed.emit(tool)
        hints = {"bus": "click on the canvas to place a bus",
                 "line": "click the first bus, then the second bus", "transformer": "click the first bus, then the second bus"}
        if tool in hints:
            self.message.emit(hints[tool])
        elif tool in ATTACH_KINDS:
            self.message.emit(f"click a bus to attach the {tool.replace('_', ' ')}")

    def bus_near(self, pos: QPointF) -> Optional[BusItem]:
        for it in self.bus_items.values():
            if it.sceneBoundingRect().adjusted(-8, -16, 8, 16).contains(pos):
                return it
        return None

    def drop_kind(self, kind: str, pos: QPointF):
        if kind == "bus":
            self.place_requested.emit(kind, pos)
        elif kind in ATTACH_KINDS:
            b = self.bus_near(pos)
            if b is None:
                self.message.emit("drop generators, loads, shunts and external grids onto a bus")
            else:
                self.attach_requested.emit(kind, b.eid)
        else:
            self.set_tool(kind)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.tool:
            pos = ev.scenePos()
            t = self.tool
            if t == "bus":
                self.place_requested.emit("bus", pos)
                self.set_tool(None)
            else:
                b = self.bus_near(pos)
                if b is None:
                    self.message.emit("click on a bus (Esc cancels)")
                elif t in ATTACH_KINDS:
                    self.attach_requested.emit(t, b.eid)
                    self.set_tool(None)
                elif self.pending is None:
                    self.pending = b.eid
                    b.setSelected(True)
                    self.message.emit(f"bus {b.eid} selected - click the second bus")
                elif b.eid != self.pending:
                    self.connect_requested.emit(t, self.pending, b.eid)
                    self.set_tool(None)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if self._moved:
            self._moved = False
            self.layout_moved.emit()

    def mouseDoubleClickEvent(self, ev):
        it = self._element_at(ev.scenePos())
        if it is not None:
            self.item_double_clicked.emit(it.kind, it.eid)
        super().mouseDoubleClickEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.set_tool(None)
        super().keyPressEvent(ev)

    def _element_at(self, pos):
        for it in self.items(pos):
            while it is not None and not hasattr(it, "eid"):
                it = it.parentItem()
            if it is not None:
                return it
        return None

    def contextMenuEvent(self, ev):
        it = self._element_at(ev.scenePos())
        if it is None:
            return
        if not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        m = QMenu()
        m.addAction("Properties\u2026", lambda: self.context_action.emit("properties", it.kind, it.eid))
        if it.kind != "bus":
            oos = self._net.get(it.kind, it.eid).in_service
            m.addAction("Take out of service" if oos else "Put in service",
                        lambda: self.context_action.emit("toggle", it.kind, it.eid))
        m.addAction("Delete", lambda: self.context_action.emit("delete", it.kind, it.eid))
        m.exec(ev.screenPos())
        ev.accept()

    def select_element(self, kind: str, eid: int) -> Optional[QGraphicsItem]:
        self.clearSelection()
        target = None
        if kind == "bus":
            target = self.bus_items.get(eid)
        elif kind == "branch":
            target = self.branch_items.get(eid)
        else:
            target = next((a for a in self.attach_items if a.kind == kind and a.eid == eid), None)
        if target is not None:
            target.setSelected(True)
        return target


# ------------------------------------------------------------------------ view
class SldView(QGraphicsView):
    def __init__(self, scene: SldScene):
        super().__init__(scene)
        self.sld = scene
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setAcceptDrops(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.legend = QLabel(self.viewport())
        self.legend.setStyleSheet("background:rgba(255,255,255,235);border:1px solid #999;padding:4px;color:#111;")
        self.legend.setText(
            "<b>Legend</b><br>"
            "<span style='color:#1f6feb'>&#9632;</span> bus below Vmin<br>"
            "<span style='color:#c026d3'>&#9632;</span> bus above Vmax<br>"
            "<span style='color:#f59e0b'>&#9632;</span> branch 80\u2013100 %<br>"
            "<span style='color:#dc2626'>&#9632;</span> branch &gt; 100 % / worst mismatch<br>"
            "<span style='color:#8a8a8a'>- - -</span> out of service<br>"
            "<span style='color:#b0b0b0'>&#9632;</span> stale result")
        self.legend.adjustSize()
        self.legend.hide()

    def set_legend(self, visible: bool):
        self.legend.setVisible(visible)
        self._place_legend()

    def mousePressEvent(self, ev):
        self.setFocus()
        super().mousePressEvent(ev)

    def _place_legend(self):
        self.legend.move(self.viewport().width() - self.legend.width() - 8, 8)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_legend()

    def set_mode(self, mode: str):
        if mode == "pan":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

    def zoom(self, f: float):
        self.scale(f, f)

    def fit(self):
        r = self.scene().itemsBoundingRect()
        if not r.isEmpty():
            self.fitInView(r.adjusted(-40, -60, 40, 60), Qt.AspectRatioMode.KeepAspectRatio)

    def center_on_item(self, it):
        if it is not None:
            self.centerOn(it)

    def wheelEvent(self, ev):
        self.zoom(1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(MIME):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat(MIME):
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        if ev.mimeData().hasFormat(MIME):
            kind = bytes(ev.mimeData().data(MIME)).decode()
            self.sld.drop_kind(kind, self.mapToScene(ev.position().toPoint()))
            ev.acceptProposedAction()


class PaletteList(QListWidget):
    def __init__(self):
        super().__init__()
        for text, kind in PALETTE:
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, kind)
            self.addItem(it)
        self.setDragEnabled(True)

    def startDrag(self, actions):
        it = self.currentItem()
        if it is None:
            return
        md = QMimeData()
        md.setData(MIME, it.data(Qt.ItemDataRole.UserRole).encode())
        d = QDrag(self)
        d.setMimeData(md)
        d.exec(Qt.DropAction.CopyAction)
