"""Network data model.

Design rules (these are what keep the GUI, solver and I/O decoupled):

* Pure data. No numpy arrays, no solver state, no Qt objects in here.
* Every element has an integer ``id`` that is unique inside its own table and
  never reused while the element exists.  The GUI keeps a dict
  ``{element id -> QGraphicsItem}`` and the solver returns results keyed by the
  same ids, so "click a table row -> select element on canvas" is a dict lookup.
* Buses are identified by their (user-visible) ``number``.  The solver builds
  its own compact 0..n-1 numbering internally (see ``compile.py``), so the user
  can delete / take out of service / renumber freely.
* Units: powers in MW / MVAr, impedances in per unit on ``Network.base_mva``,
  voltages in per unit (kV only for display), angles in degrees.
* Bus type (slack / PV / PQ) is NOT stored: it is derived from what is
  connected (external grid -> slack, voltage-controlling generator -> PV,
  otherwise PQ).  This is exactly the behaviour requested in B5.
* Everything round-trips through ``Network.to_dict`` / ``from_dict`` (JSON).
  ``Network.copy()`` is a deep copy, which is all the GUI needs for undo/redo
  (keep a stack of snapshots).
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

# ---- string constants (strings, not Enums, so JSON stays human readable) ----
LOAD_CONST_PQ = "const_pq"
LOAD_CONST_Z = "const_z"
LOAD_ZIP = "zip"
LOAD_MODELS = (LOAD_CONST_PQ, LOAD_CONST_Z, LOAD_ZIP)

TAP_FROM = "from"
TAP_TO = "to"

BIG_Q = 9999.0  # "no reactive limit" (finite so JSON stays standard)


@dataclass
class Bus:
    number: int
    name: str = ""
    base_kv: float = 1.0
    vmin: float = 0.95
    vmax: float = 1.05
    area: int = 1

    def __post_init__(self):
        if not self.name:
            self.name = f"Bus {self.number}"


@dataclass
class Branch:
    """Line or two-winding transformer (MATPOWER branch model).

    r, x, b are per unit on the *system* base.  b is the TOTAL line charging.
    ``tap`` is the off-nominal ratio magnitude, ``shift_deg`` the phase shift
    (positive = voltage on the tap side leads).  ``tap_side`` says on which
    terminal the ideal transformer sits ("from" is the MATPOWER convention).
    A plain line has tap = 1, shift = 0.
    """
    id: int
    from_bus: int
    to_bus: int
    r_pu: float = 0.0
    x_pu: float = 0.1
    b_pu: float = 0.0
    tap: float = 1.0
    shift_deg: float = 0.0
    tap_side: str = TAP_FROM
    rating_mva: float = 0.0  # 0 = unlimited
    in_service: bool = True
    kind: str = "line"  # "line" | "transformer"
    name: str = ""
    # --- line entry in physical units (input_mode == "phys") ---
    input_mode: str = "pu"          # "pu" | "phys"
    length_km: float = 1.0
    r_ohm_km: float = 0.0
    x_ohm_km: float = 0.0
    b_us_km: float = 0.0
    # --- transformer nameplate data (used to derive r, x when input_mode == "phys") ---
    rated_mva: float = 100.0
    kv_hv: float = 0.0
    kv_lv: float = 0.0
    z_percent: float = 10.0
    x_over_r: float = 10.0
    tap_min: float = 0.9
    tap_max: float = 1.1
    tap_step: float = 0.0125

    def __post_init__(self):
        if not self.name:
            self.name = f"{'Line' if self.kind == 'line' else 'Trafo'} {self.id}"


@dataclass
class Generator:
    """Synchronous generator.

    ``control_voltage=True``  -> PV behaviour: holds ``v_set_pu``, Q is solved
                                 and limited to [qmin, qmax].
    ``control_voltage=False`` -> fixed P and fixed Q (``q_mvar``).
    """
    id: int
    bus: int
    p_mw: float = 0.0
    v_set_pu: float = 1.0
    qmin_mvar: float = -BIG_Q
    qmax_mvar: float = BIG_Q
    control_voltage: bool = True
    q_mvar: float = 0.0  # used only when control_voltage is False
    in_service: bool = True
    name: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = f"Gen {self.id}"


@dataclass
class Load:
    """Load with a ZIP voltage dependence:  P(V) = P0 (z V^2 + i V + p).

    const_pq -> (z, i, p) = (0, 0, 1);  const_z -> (1, 0, 0);
    zip      -> user coefficients (should sum to 1 so that P(1 pu) = P0).
    The same coefficients apply to P and Q.  P0, Q0 are the values at 1 pu.
    """
    id: int
    bus: int
    p_mw: float = 0.0
    q_mvar: float = 0.0
    model: str = LOAD_CONST_PQ
    zip_z: float = 0.0
    zip_i: float = 0.0
    zip_p: float = 1.0
    in_service: bool = True
    name: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = f"Load {self.id}"

    def coefficients(self):
        """Return (z, i, p) actually used by the solver."""
        if self.model == LOAD_CONST_Z:
            return 1.0, 0.0, 0.0
        if self.model == LOAD_ZIP:
            return self.zip_z, self.zip_i, self.zip_p
        return 0.0, 0.0, 1.0

    @staticmethod
    def q_from_pf(p_mw: float, pf: float, lagging: bool = True) -> float:
        """Q (MVAr) from P and power factor (lagging = inductive load, Q > 0)."""
        if not 0.0 < pf <= 1.0:
            raise ValueError("power factor must be in (0, 1]")
        q = abs(p_mw) * ((1.0 - pf * pf) ** 0.5) / pf
        return q if lagging else -q


@dataclass
class Shunt:
    """Fixed shunt, specified at 1 pu voltage (MATPOWER GS/BS convention):
    g_mw = power consumed (MW); b_mvar = reactive power INJECTED (capacitor > 0)."""
    id: int
    bus: int
    g_mw: float = 0.0
    b_mvar: float = 0.0
    in_service: bool = True
    name: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = f"Shunt {self.id}"


@dataclass
class ExternalGrid:
    """Slack / reference source: fixes |V| and the angle at its bus."""
    id: int
    bus: int
    v_set_pu: float = 1.0
    angle_deg: float = 0.0
    in_service: bool = True
    name: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = f"Ext grid {self.id}"


# kind string -> (dataclass, attribute name of the table inside Network)
ELEMENT_TABLES = {
    "bus": (Bus, "buses"),
    "branch": (Branch, "branches"),
    "generator": (Generator, "generators"),
    "load": (Load, "loads"),
    "shunt": (Shunt, "shunts"),
    "ext_grid": (ExternalGrid, "ext_grids"),
}


def _build(cls, d: Dict[str, Any]):
    """Construct a dataclass from a dict, ignoring unknown keys (forward compat)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Network:
    name: str = "untitled"
    base_mva: float = 100.0
    freq_hz: float = 50.0
    buses: Dict[int, Bus] = field(default_factory=dict)
    branches: Dict[int, Branch] = field(default_factory=dict)
    generators: Dict[int, Generator] = field(default_factory=dict)
    loads: Dict[int, Load] = field(default_factory=dict)
    shunts: Dict[int, Shunt] = field(default_factory=dict)
    ext_grids: Dict[int, ExternalGrid] = field(default_factory=dict)

    # ------------------------------------------------------------------ build
    @staticmethod
    def _next_id(table: Dict[int, Any]) -> int:
        return max(table, default=0) + 1

    def add_bus(self, number: Optional[int] = None, **kw) -> Bus:
        if number is None:
            number = self._next_id(self.buses)
        if number in self.buses:
            raise ValueError(f"bus number {number} already exists")
        b = Bus(number=number, **kw)
        self.buses[number] = b
        return b

    def add_branch(self, from_bus: int, to_bus: int, **kw) -> Branch:
        e = Branch(id=self._next_id(self.branches), from_bus=from_bus, to_bus=to_bus, **kw)
        self.branches[e.id] = e
        return e

    def add_generator(self, bus: int, **kw) -> Generator:
        e = Generator(id=self._next_id(self.generators), bus=bus, **kw)
        self.generators[e.id] = e
        return e

    def add_load(self, bus: int, **kw) -> Load:
        e = Load(id=self._next_id(self.loads), bus=bus, **kw)
        self.loads[e.id] = e
        return e

    def add_shunt(self, bus: int, **kw) -> Shunt:
        e = Shunt(id=self._next_id(self.shunts), bus=bus, **kw)
        self.shunts[e.id] = e
        return e

    def add_ext_grid(self, bus: int, **kw) -> ExternalGrid:
        e = ExternalGrid(id=self._next_id(self.ext_grids), bus=bus, **kw)
        self.ext_grids[e.id] = e
        return e

    # ----------------------------------------------------------------- access
    def table(self, kind: str) -> Dict[int, Any]:
        return getattr(self, ELEMENT_TABLES[kind][1])

    def get(self, kind: str, ident: int):
        return self.table(kind)[ident]

    def elements_at_bus(self, number: int) -> Dict[str, List[Any]]:
        """Everything attached to a bus (used by the GUI to build the bus dialog
        and by remove_bus)."""
        out: Dict[str, List[Any]] = {k: [] for k in ELEMENT_TABLES if k != "bus"}
        for br in self.branches.values():
            if number in (br.from_bus, br.to_bus):
                out["branch"].append(br)
        for kind in ("generator", "load", "shunt", "ext_grid"):
            out[kind] = [e for e in self.table(kind).values() if e.bus == number]
        return out

    # ----------------------------------------------------------------- remove
    def remove(self, kind: str, ident: int) -> None:
        if kind == "bus":
            self.remove_bus(ident)
        else:
            del self.table(kind)[ident]

    def remove_bus(self, number: int) -> None:
        """Delete a bus and everything attached to it."""
        for kind, items in self.elements_at_bus(number).items():
            for e in items:
                del self.table(kind)[e.id]
        del self.buses[number]

    # ------------------------------------------------------------ persistence
    def copy(self) -> "Network":
        """Deep copy - use as an undo/redo snapshot."""
        return copy.deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "base_mva": self.base_mva, "freq_hz": self.freq_hz}
        for kind, (_, attr) in ELEMENT_TABLES.items():
            d[attr] = [asdict(e) for e in getattr(self, attr).values()]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Network":
        net = cls(name=d.get("name", "untitled"), base_mva=d.get("base_mva", 100.0),
                  freq_hz=d.get("freq_hz", 50.0))
        for kind, (klass, attr) in ELEMENT_TABLES.items():
            key = "number" if kind == "bus" else "id"
            for row in d.get(attr, []):
                e = _build(klass, row)
                getattr(net, attr)[getattr(e, key)] = e
        return net

    def save_json(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load_json(cls, path: str) -> "Network":
        with open(path) as fh:
            return cls.from_dict(json.load(fh))
