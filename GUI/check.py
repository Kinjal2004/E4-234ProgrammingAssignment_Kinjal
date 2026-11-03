"""Network sanity check.

``check_network`` returns a list of ``Issue``.  The GUI:
  * disables "Run NR" while any issue has severity "error";
  * shows the list in a dialog and uses (element_kind, element_id) to
    highlight the offending element on the canvas.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

from model import LOAD_MODELS, Network

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    severity: str          # "error" | "warning"
    element_kind: str      # "bus", "branch", "generator", ... or "network"
    element_id: int        # bus number for kind "bus"; 0 for "network"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.element_kind} {self.element_id}: {self.message}"


def has_errors(issues: List[Issue]) -> bool:
    return any(i.severity == ERROR for i in issues)


def check_network(net: Network) -> List[Issue]:
    out: List[Issue] = []

    def err(kind, ident, msg):
        out.append(Issue(ERROR, kind, ident, msg))

    def warn(kind, ident, msg):
        out.append(Issue(WARNING, kind, ident, msg))

    if not net.buses:
        err("network", 0, "network has no buses")
        return out
    if net.base_mva <= 0:
        err("network", 0, "system base MVA must be positive")

    # ---- buses
    for b in net.buses.values():
        if b.base_kv <= 0:
            err("bus", b.number, "nominal kV must be positive")
        if b.vmin >= b.vmax:
            err("bus", b.number, "Vmin must be below Vmax")

    # ---- branches
    for br in net.branches.values():
        for end in (br.from_bus, br.to_bus):
            if end not in net.buses:
                err("branch", br.id, f"connected to non-existent bus {end} (dangling branch)")
        if br.from_bus == br.to_bus:
            err("branch", br.id, "from-bus and to-bus are identical")
        if br.r_pu == 0 and br.x_pu == 0:
            err("branch", br.id, "zero impedance (R = X = 0)")
        if br.rating_mva < 0:
            err("branch", br.id, "negative rating")
        if br.tap <= 0:
            err("branch", br.id, "tap ratio must be positive")
        if br.tap_side not in ("from", "to"):
            err("branch", br.id, "tap side must be 'from' or 'to'")

    # ---- bus-connected elements
    for kind in ("generator", "load", "shunt", "ext_grid"):
        for e in net.table(kind).values():
            if e.bus not in net.buses:
                err(kind, e.id, f"connected to non-existent bus {e.bus}")
    for g in net.generators.values():
        if g.qmin_mvar > g.qmax_mvar:
            err("generator", g.id, "Qmin is greater than Qmax")
        if g.control_voltage and g.v_set_pu <= 0:
            err("generator", g.id, "voltage set-point must be positive")
    for g in net.ext_grids.values():
        if g.v_set_pu <= 0:
            err("ext_grid", g.id, "voltage set-point must be positive")
    for ld in net.loads.values():
        if ld.model not in LOAD_MODELS:
            err("load", ld.id, f"unknown load model '{ld.model}'")
        elif ld.model == "zip" and abs(ld.zip_z + ld.zip_i + ld.zip_p - 1.0) > 1e-6:
            warn("load", ld.id, "ZIP coefficients do not sum to 1")

    if has_errors(out):
        return out  # topology checks below need valid bus references

    # ---- islands: components of the graph of in-service branches
    nums = sorted(net.buses)
    idx = {n: i for i, n in enumerate(nums)}
    live = [b for b in net.branches.values() if b.in_service]
    nb = len(nums)
    if live:
        rows = [idx[b.from_bus] for b in live]
        cols = [idx[b.to_bus] for b in live]
        adj = sp.coo_matrix((np.ones(len(live)), (rows, cols)), shape=(nb, nb))
    else:
        adj = sp.coo_matrix((nb, nb))
    ncomp, label = connected_components(adj, directed=False)

    ref_buses = {}
    for g in net.ext_grids.values():
        if g.in_service:
            ref_buses.setdefault(g.bus, []).append(g.id)
    for bus, ids in ref_buses.items():
        if len(ids) > 1:
            err("ext_grid", ids[0], f"more than one external grid at bus {bus}")

    for c in range(ncomp):
        members = [nums[i] for i in np.flatnonzero(label == c)]
        refs = [b for b in members if b in ref_buses]
        if not refs:
            err("bus", members[0],
                f"island {members if len(members) <= 6 else str(members[:6])[:-1] + ', ...]'} "
                "has no slack bus (no in-service external grid)")
        elif len(refs) > 1:
            err("bus", refs[0], f"island contains several slack buses {refs}")

    # ---- warnings
    # engine limitation: one generator per bus, and none on a slack bus
    gens_by_bus = {}
    for g in net.generators.values():
        if g.in_service:
            gens_by_bus.setdefault(g.bus, []).append(g.id)
    for bus, ids in gens_by_bus.items():
        if len(ids) > 1:
            err("generator", ids[1], f"more than one in-service generator at bus {bus} (not supported)")
        if bus in ref_buses:
            err("generator", ids[0], f"generator and external grid on the same bus {bus} (not supported)")
    by_bus = {}
    for g in net.generators.values():
        if g.in_service and g.control_voltage and g.bus not in ref_buses:
            by_bus.setdefault(g.bus, set()).add(round(g.v_set_pu, 6))
    for bus, vs in by_bus.items():
        if len(vs) > 1:
            warn("bus", bus, "generators at this bus have different voltage set-points; first one is used")
    for b in net.buses.values():
        has_branch = any(b.number in (x.from_bus, x.to_bus) for x in live)
        if not has_branch and ncomp > 1:
            warn("bus", b.number, "bus has no in-service branch")
    return out
