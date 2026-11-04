"""Properties panel (B5): every field shows its unit, MW/MVAr/MVA fields show the
per-unit value alongside, edits are validated on Apply and invalid fields are
highlighted in red.  The panel never touches the model: it emits
``apply_requested(kind, id, values)`` and the main window applies it (with undo)."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from adapter import branch_pu
from model import Load

BAD = "border:1.5px solid #dc2626;background:#fee2e2;"


@dataclass
class Fld:
    attr: str
    label: str
    unit: str = ""
    typ: str = "float"            # float | int | str | bool | choice
    pu: bool = False              # show value / base_mva alongside
    min: Optional[float] = None   # value >= min
    gt: Optional[float] = None    # value > gt
    ro: bool = False
    choices: Tuple = ()           # ((value, label), ...)
    optional: bool = False


F = Fld
MODE = (("pu", "per unit"), ("phys", "physical / nameplate"))

SPECS = {
    "bus": lambda e: [
        F("number", "Number", "", "int", ro=True), F("name", "Name", "", "str"),
        F("base_kv", "Nominal voltage", "kV", gt=0), F("vmin", "Vmin", "pu", gt=0), F("vmax", "Vmax", "pu", gt=0),
        F("area", "Area", "", "int")],
    "branch": lambda e: (
        [F("name", "Name", "", "str"), F("from_bus", "From bus" if e.kind == "line" else "HV bus", "", "int", ro=True),
         F("to_bus", "To bus" if e.kind == "line" else "LV bus", "", "int", ro=True),
         F("input_mode", "Parameter entry", "", "choice", choices=MODE)]
        + ([F("r_pu", "R", "pu"), F("x_pu", "X", "pu"), F("b_pu", "Total charging B", "pu")]
           if e.kind == "line" and e.input_mode == "pu" else
           [F("length_km", "Length", "km", gt=0), F("r_ohm_km", "R", "\u03a9/km", min=0), F("x_ohm_km", "X", "\u03a9/km", min=0),
            F("b_us_km", "Charging B", "\u00b5S/km", min=0)] if e.kind == "line" else
           [F("r_pu", "R", "pu"), F("x_pu", "X", "pu")] if e.input_mode == "pu" else
           [F("z_percent", "Impedance %Z", "% on rated MVA", gt=0), F("x_over_r", "X/R", "", min=0)])
        + ([F("rating_mva", "Rating", "MVA", pu=True, min=0)] if e.kind == "line" else
           [F("rated_mva", "Rated power", "MVA", pu=True, gt=0), F("kv_hv", "Rated kV HV", "kV", min=0),
            F("kv_lv", "Rated kV LV", "kV", min=0), F("tap", "Tap ratio", "pu", gt=0),
            F("tap_side", "Tap side", "", "choice", choices=(("from", "HV / from"), ("to", "LV / to"))),
            F("tap_min", "Tap min", "pu", gt=0), F("tap_max", "Tap max", "pu", gt=0), F("tap_step", "Tap step", "pu", min=0),
            F("shift_deg", "Phase shift", "deg")])
        + [F("in_service", "In service", "", "bool")]),
    "generator": lambda e: [
        F("name", "Name", "", "str"), F("bus", "Bus", "", "int", ro=True),
        F("control_voltage", "Voltage control (PV)", "", "bool"),
        F("p_mw", "P set-point", "MW", pu=True), F("v_set_pu", "V set-point", "pu", gt=0),
        F("qmin_mvar", "Qmin", "MVAr", pu=True), F("qmax_mvar", "Qmax", "MVAr", pu=True),
        F("q_mvar", "Q (when not PV)", "MVAr", pu=True), F("in_service", "In service", "", "bool")],
    "load": lambda e: [
        F("name", "Name", "", "str"), F("bus", "Bus", "", "int", ro=True),
        F("p_mw", "P at 1 pu" if e.model == "const_z" else "P",
          "MW", pu=True),
        F("q_mvar", "Q at 1 pu" if e.model == "const_z" else "Q",
          "MVAr", pu=True),
        F("pf", "or power factor (lagging)", "", optional=True),
        F("model", "Load model", "", "choice",
          choices=(("const_pq", "constant PQ"), ("const_z", "constant Z"))),
        F("in_service", "In service", "", "bool")],
    "shunt": lambda e: [
        F("name", "Name", "", "str"), F("bus", "Bus", "", "int", ro=True),
        F("g_mw", "G (at 1 pu)", "MW", pu=True), F("b_mvar", "B (at 1 pu, + = capacitive)", "MVAr", pu=True),
        F("in_service", "In service", "", "bool")],
    "ext_grid": lambda e: [
        F("name", "Name", "", "str"), F("bus", "Bus", "", "int", ro=True),
        F("v_set_pu", "V set-point", "pu", gt=0), F("angle_deg", "Angle", "deg"), F("in_service", "In service", "", "bool")],
}


class PropertiesPanel(QWidget):
    apply_requested = Signal(str, int, dict)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        self.title = QLabel("No selection")
        outer.addWidget(self.title)
        self.host = QWidget()
        self.form = QFormLayout(self.host)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.host)
        outer.addWidget(sc, 1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        self.btn = QPushButton("Apply")
        self.btn.clicked.connect(self._apply)
        outer.addWidget(self.btn)
        self.net = self.kind = self.eid = self.elem = None
        self.w: Dict[str, QWidget] = {}
        self.fields: List[Fld] = []
        self.clear()

    def clear(self, text: str = "No selection"):
        self.title.setText(text)
        self._wipe()
        self.btn.setEnabled(False)
        self.note.setText("")
        self.kind = None

    def _wipe(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.w = {}

    def show_element(self, net, kind: str, eid: int):
        self.net, self.kind, self.eid = net, kind, eid
        self.elem = copy.copy(net.get(kind, eid))
        self._build()

    def _build(self):
        self._wipe()
        e, base = self.elem, self.net.base_mva
        self.fields = SPECS[self.kind](e)
        self.title.setText(f"{self.kind.replace('_', ' ').title()}: {getattr(e, 'name', '')}")
        for f in self.fields:
            if f.typ == "bool":
                w = QCheckBox()
                w.setChecked(bool(getattr(e, f.attr)))
                self.form.addRow(f.label, w)
            elif f.typ == "choice":
                w = QComboBox()
                for val, lab in f.choices:
                    w.addItem(lab, val)
                values = [v for v, _ in f.choices]
                cur = getattr(e, f.attr)
                w.setCurrentIndex(values.index(cur) if cur in values else 0)
                if f.attr == "input_mode":
                    w.currentIndexChanged.connect(self._mode_changed)
                elif f.attr == "model" and self.kind == "load":
                    w.currentIndexChanged.connect(self._load_model_changed)
                self.form.addRow(f.label, w)
            else:
                val = getattr(e, f.attr, None) if f.attr != "pf" else None
                w = QLineEdit("" if val is None else f"{val:.6g}" if isinstance(val, float) else str(val))
                w.setReadOnly(f.ro)
                w.returnPressed.connect(self._apply)
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(w, 1)
                if f.unit:
                    h.addWidget(QLabel(f.unit))
                if f.pu:
                    pl = QLabel()
                    h.addWidget(pl)
                    w.textChanged.connect(lambda t, pl=pl: self._pu_label(pl, t))
                    self._pu_label(pl, w.text())
                self.form.addRow(f.label, row)
            self.w[f.attr] = w
        if self.kind == "branch":
            r, x, b = branch_pu(self.net, e)
            self.note.setText(f"On {base:g} MVA base:  R = {r:.5f}  X = {x:.5f}  B = {b:.5f} pu")
        elif self.kind == "bus":
            self.note.setText("Bus type (slack / PV / PQ) is derived from the connected elements.")
        elif self.kind == "load" and e.model == "const_z":
            self.note.setText(
                "Constant-Z load: P and Q are reference consumption at 1.0 pu. "
                "Actual P and Q vary with |V|^2."
            )
        else:
            self.note.setText("")
        self.btn.setEnabled(True)

    def _pu_label(self, label: QLabel, text: str):
        try:
            label.setText(f"= {float(text) / self.net.base_mva:.5g} pu")
        except ValueError:
            label.setText("")

    def _mode_changed(self):
        mode = self.w["input_mode"].currentData()

        def rebuild():                       # deferred: never delete a widget inside its own signal handler
            self.elem.input_mode = mode
            self._build()
        QTimer.singleShot(0, rebuild)

    def _load_model_changed(self):
        """Rebuild load fields when switching between PQ and Z representations."""
        model = self.w["model"].currentData()

        # Preserve edits made in the current form before its widgets are destroyed.
        for attr in ("p_mw", "q_mvar"):
            widget = self.w.get(attr)
            if isinstance(widget, QLineEdit):
                try:
                    setattr(self.elem, attr, float(widget.text().strip()))
                except ValueError:
                    pass

        pf_widget = self.w.get("pf")
        if isinstance(pf_widget, QLineEdit):
            text = pf_widget.text().strip()
            try:
                self.elem.pf = float(text) if text else None
            except ValueError:
                pass

        def rebuild():                       # defer deletion of the active combo box
            self.elem.model = model
            self._build()

        QTimer.singleShot(0, rebuild)

    # ---- validation -----------------------------------------------------------
    def _read(self):
        vals, bad = {}, {}
        for f in self.fields:
            w = self.w[f.attr]
            w.setStyleSheet("")
            if f.ro:
                continue
            if f.typ == "bool":
                vals[f.attr] = w.isChecked()
            elif f.typ == "choice":
                vals[f.attr] = w.currentData()
            else:
                txt = w.text().strip()
                if f.typ == "str":
                    vals[f.attr] = txt
                    continue
                if f.optional and txt == "":
                    vals[f.attr] = None
                    continue
                try:
                    v = int(txt) if f.typ == "int" else float(txt)
                except ValueError:
                    bad[f.attr] = "not a valid number"
                    continue
                if f.min is not None and v < f.min:
                    bad[f.attr] = f"must be \u2265 {f.min:g}"
                if f.gt is not None and v <= f.gt:
                    bad[f.attr] = f"must be > {f.gt:g}"
                vals[f.attr] = v
        bad.update(self._cross(vals, bad))
        return vals, bad

    def _cross(self, v, bad) -> Dict[str, str]:
        out = {}
        g = lambda a: v.get(a, getattr(self.elem, a, None))
        if self.kind == "bus" and "vmin" not in bad and "vmax" not in bad and g("vmin") >= g("vmax"):
            out["vmin"] = "Vmin must be below Vmax"
        if self.kind == "generator" and "qmin_mvar" not in bad and "qmax_mvar" not in bad and g("qmin_mvar") > g("qmax_mvar"):
            out["qmin_mvar"] = "Qmin must not exceed Qmax"
        if self.kind == "load" and v.get("pf") is not None and not 0 < v["pf"] <= 1:
            out["pf"] = "power factor must be in (0, 1]"
        if self.kind == "branch":
            if g("input_mode") == "pu" and "r_pu" in v and "x_pu" in v and v["r_pu"] == 0 and v["x_pu"] == 0:
                out["x_pu"] = "R and X cannot both be zero"
            if self.elem.kind == "transformer":
                if g("tap_min") >= g("tap_max"):
                    out["tap_min"] = "tap min must be below tap max"
                elif "tap" in v and not g("tap_min") <= v["tap"] <= g("tap_max"):
                    out["tap"] = f"tap must be within [{g('tap_min'):g}, {g('tap_max'):g}]"
        return out

    def _apply(self):
        if self.kind is None:
            return
        vals, bad = self._read()
        if bad:
            for a, msg in bad.items():
                self.w[a].setStyleSheet(BAD)
                self.w[a].setToolTip(msg)
            first = next(iter(bad))
            self.note.setText(f"<span style='color:#dc2626'>Cannot apply: {self.fields_label(first)} {bad[first]}</span>")
            return
        if self.kind == "load" and vals.get("pf") is not None:
            vals["q_mvar"] = Load.q_from_pf(vals["p_mw"], vals["pf"])
        vals.pop("pf", None)
        kind, eid = self.kind, self.eid
        QTimer.singleShot(0, lambda: self.apply_requested.emit(kind, eid, vals))   # deferred, see _mode_changed

    def fields_label(self, attr: str) -> str:
        return next((f.label for f in self.fields if f.attr == attr), attr)
