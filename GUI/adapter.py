"""GUI model <-> NR load-flow engine."""

from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------
# Project root: parent directory containing GUI/ and NR/
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------
# GUI-local imports
# ---------------------------------------------------------

from check import ERROR, Issue, check_network
from model import BIG_Q, Network

# ---------------------------------------------------------
# NR solver imports
# ---------------------------------------------------------

ENGINE_PKG = "NR_Solver.loadflow"


def _eng():
    """Import NR solver modules."""

    try:
        models = importlib.import_module(f"{ENGINE_PKG}.models")
        solver = importlib.import_module(f"{ENGINE_PKG}.solver")

    except ModuleNotFoundError as exc:
        raise ImportError(
            "Could not import NR.Loadflow.\n"
            "Expected project structure:\n"
            "Project_Root/\n"
            "    GUI/\n"
            "    NR/\n"
            "        Loadflow/\n"
            "            __init__.py\n"
            "            models.py\n"
            "            solver.py"
        ) from exc

    return models, solver
# ---------------------------------------------------------------- study settings
@dataclass
class StudySettings:
    tolerance: float = 1e-8
    max_iterations: int = 20
    flat_start: bool = True
    enforce_q_limits: bool = False   # forwarded to the engine (ignored until it implements it)
    load_scale: float = 1.0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ------------------------------------------------------------------ unit helpers
def branch_pu(net: Network, br) -> Tuple[float, float, float]:
    """(r, x, b) in per unit on the system base, from whichever input mode is active."""
    if br.input_mode != "phys":
        return br.r_pu, br.x_pu, br.b_pu
    if br.kind == "transformer":
        z = br.z_percent / 100.0 * net.base_mva / max(br.rated_mva, 1e-9)
        k = math.sqrt(1.0 + br.x_over_r ** 2)
        return z / k, z * br.x_over_r / k, 0.0
    kv = net.buses[br.from_bus].base_kv if br.from_bus in net.buses else 1.0
    zbase = kv ** 2 / net.base_mva
    return (br.r_ohm_km * br.length_km / zbase,
            br.x_ohm_km * br.length_km / zbase,
            br.b_us_km * 1e-6 * br.length_km * zbase)


def gui_checks(net: Network) -> List[Issue]:
    return check_network(net)


# ------------------------------------------------------------------ GUI results
@dataclass
class BusRow:
    number: int
    name: str
    bus_type: str
    vm: float
    kv: float
    va: float
    pg: float
    qg: float
    pd: float
    qd: float
    p_inj: float
    q_inj: float
    vmin: float
    vmax: float
    status: str          # "ok" | "under" | "over"


@dataclass
class BranchRow:
    id: int
    name: str
    from_bus: int
    to_bus: int
    in_service: bool
    pf: float
    qf: float
    pt: float
    qt: float
    sf: float
    st: float
    loading: float       # percent, nan if no rating
    ploss: float
    qloss: float
    status: str          # "ok" | "warn" (80-100 %) | "over" (>100 %) | "out"


@dataclass
class GenRow:
    kind: str            # "generator" | "ext_grid"
    id: int
    name: str
    bus: int
    p: float
    q: float
    qmin: float
    qmax: float
    status: str


@dataclass
class GuiResults:
    converged: bool = False
    iterations: int = 0
    max_mismatch: float = float("nan")
    solve_time_ms: float = 0.0
    message: str = ""
    mismatch_history: List[float] = field(default_factory=list)
    buses: List[BusRow] = field(default_factory=list)
    branches: List[BranchRow] = field(default_factory=list)
    gens: List[GenRow] = field(default_factory=list)
    violations: List[Tuple[str, int, str]] = field(default_factory=list)  # (kind, id, text)
    total_loss_mw: float = 0.0
    total_loss_mvar: float = 0.0
    total_gen_mw: float = 0.0
    total_load_mw: float = 0.0
    worst_buses: List[int] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    vm_va: Dict[int, Tuple[float, float]] = field(default_factory=dict)

    def bus(self, n: int) -> Optional[BusRow]:
        return next((b for b in self.buses if b.number == n), None)

    def branch(self, i: int) -> Optional[BranchRow]:
        return next((b for b in self.branches if b.id == i), None)


# ---------------------------------------------------------------- GUI -> engine
@dataclass
class Mapping:
    branch_ids: List[int]
    bus_load: Dict[int, dict]     # bus -> {"pq": P, "qq": Q, "pz": P@1pu, "qz": Q@1pu}
    gen_at: Dict[int, Tuple[str, int]]   # bus -> ("generator"|"ext_grid", id)
    fixed_gs: Dict[int, float]    # bus -> fixed shunt G (MW @1pu), excl. Z-loads


def to_engine(net: Network, settings: StudySettings, prev: Optional[GuiResults] = None):
    M, _ = _eng()
    base = net.base_mva
    ebus = {}
    for n in sorted(net.buses):
        b = net.buses[n]
        ebus[n] = M.Bus(bus_id=n, bus_type=1, pd_mw=0.0, qd_mvar=0.0, gs_mw=0.0, bs_mvar=0.0,
                        base_kv=b.base_kv, vm=1.0, va_deg=0.0, vmax=b.vmax, vmin=b.vmin, area=b.area)
    mp = Mapping([], {n: dict(pq=0.0, qq=0.0, pz=0.0, qz=0.0) for n in ebus}, {}, {n: 0.0 for n in ebus})

    for ld in net.loads.values():
        if not ld.in_service:
            continue
        P, Q = ld.p_mw * settings.load_scale, ld.q_mvar * settings.load_scale
        if getattr(ld, "model", "const_pq") == "const_z":
            # A constant-impedance load is represented entirely as a bus shunt.
            # P and Q are its consumption at |V| = 1 pu.
            z, i, p = 1.0, 0.0, 0.0
        else:
            z, i, p = ld.coefficients()      # I part is folded into constant PQ (approximation)

        e, d = ebus[ld.bus], mp.bus_load[ld.bus]
        e.pd_mw += (i + p) * P
        e.qd_mvar += (i + p) * Q
        e.gs_mw += z * P                      # constant Z -> shunt (P*V^2, -Q*V^2)
        e.bs_mvar -= z * Q
        d["pq"] += (i + p) * P
        d["qq"] += (i + p) * Q
        d["pz"] += z * P
        d["qz"] += z * Q
    for s in net.shunts.values():
        if s.in_service:
            ebus[s.bus].gs_mw += s.g_mw
            ebus[s.bus].bs_mvar += s.b_mvar
            mp.fixed_gs[s.bus] += s.g_mw

    egen = []
    for g in net.ext_grids.values():
        if g.in_service and g.bus not in mp.gen_at:
            e = ebus[g.bus]
            e.bus_type, e.vm, e.va_deg = 3, g.v_set_pu, g.angle_deg
            egen.append(M.Generator(bus_id=g.bus, pg_mw=0.0, qg_mvar=0.0, qmax_mvar=BIG_Q,
                                    qmin_mvar=-BIG_Q, vg_pu=g.v_set_pu, mbase_mva=base,
                                    status=True, pmax_mw=BIG_Q, pmin_mw=0.0))
            mp.gen_at[g.bus] = ("ext_grid", g.id)
    for g in net.generators.values():
        if g.in_service and g.bus not in mp.gen_at:
            e = ebus[g.bus]
            if g.control_voltage:
                e.bus_type, e.vm = 2, g.v_set_pu
            egen.append(M.Generator(
                bus_id=g.bus, pg_mw=g.p_mw, qg_mvar=0.0 if g.control_voltage else g.q_mvar,
                qmax_mvar=g.qmax_mvar, qmin_mvar=g.qmin_mvar, vg_pu=g.v_set_pu, mbase_mva=base,
                status=True, pmax_mw=BIG_Q, pmin_mw=0.0))
            mp.gen_at[g.bus] = ("generator", g.id)

    if prev is not None and not settings.flat_start:
        for n, e in ebus.items():
            if n in prev.vm_va:
                vm, va = prev.vm_va[n]
                if e.bus_type == 1:
                    e.vm = vm
                if e.bus_type != 3:
                    e.va_deg = va

    ebr = []
    for br in sorted(net.branches.values(), key=lambda x: x.id):
        if not br.in_service:
            continue
        r, x, b = branch_pu(net, br)
        tap, shift = (br.tap if br.tap else 1.0), br.shift_deg
        if br.tap_side == "to" and (tap != 1.0 or shift != 0.0):
            r, x, tap, shift = r * tap ** 2, x * tap ** 2, 1.0 / tap, -shift   # see module docstring
        ebr.append(M.Branch(from_bus=br.from_bus, to_bus=br.to_bus, r_pu=r, x_pu=x, b_pu=b,
                            rate_a_mva=br.rating_mva, tap=tap, shift_deg=shift, status=True))
        mp.branch_ids.append(br.id)

    enet = M.Network(base_mva=base, buses=[ebus[n] for n in sorted(ebus)],
                     generators=egen, branches=ebr, case_name=net.name)
    return enet, mp


# ---------------------------------------------------------------- engine -> GUI
def _status_v(vm, lo, hi):
    return "under" if vm < lo else "over" if vm > hi else "ok"


def map_results(net: Network, mp: Mapping, res, settings: StudySettings) -> GuiResults:
    out = GuiResults(converged=bool(res.converged), iterations=int(res.iterations),
                     max_mismatch=float(res.max_mismatch), solve_time_ms=res.solve_time * 1e3,
                     message=res.message, mismatch_history=[float(v) for v in res.mismatch_history])
    ids = [int(i) for i in res.bus_ids]
    vm_a, va_a = np.asarray(res.voltage_magnitude), np.asarray(res.voltage_angle_deg)
    out.vm_va = {n: (float(vm_a[k]), float(va_a[k])) for k, n in enumerate(ids)}

    # ---- iteration log: NR mismatches, final message, then the engine's PV/PQ switch events
    for k, h in enumerate(out.mismatch_history):
        out.log.append(f"iteration {k:2d}   max mismatch = {h:.3e} pu")
    out.log.append(out.message)
    out.log.extend(getattr(res, "log", None) or [])
    q_lim = getattr(res, "q_limited", None) or {}        # {bus_id: "Qmax" | "Qmin"}
    if not out.converged:
        return out

    pinj = {n: float(res.p_injection_mw[k]) for k, n in enumerate(ids)}
    qinj = {n: float(res.q_injection_mvar[k]) for k, n in enumerate(ids)}

    # ---- generators / external grid: Pg = P_inj + PQ-load, same for Q
    for bus, (kind, eid) in sorted(mp.gen_at.items()):
        d = mp.bus_load[bus]
        p, q = pinj[bus] + d["pq"], qinj[bus] + d["qq"]
        if kind == "ext_grid":
            e = net.ext_grids[eid]
            out.gens.append(GenRow(kind, eid, e.name, bus, p, q, -BIG_Q, BIG_Q, "slack"))
        else:
            e = net.generators[eid]
            st = "fixed"
            if e.control_voltage:
                if bus in q_lim:
                    st = f"at {q_lim[bus]}"
                elif q > e.qmax_mvar + 1e-4:
                    st = "Qmax violated"
                elif q < e.qmin_mvar - 1e-4:
                    st = "Qmin violated"
                else:
                    st = "ok"
                p = e.p_mw
            out.gens.append(GenRow(kind, eid, e.name, bus, p, q, e.qmin_mvar, e.qmax_mvar, st))
    gen_pq = {g.bus: (g.p, g.q) for g in out.gens}

    # ---- buses
    for n in sorted(net.buses):
        b = net.buses[n]
        vm, va = out.vm_va[n]
        d = mp.bus_load[n]
        pd, qd = d["pq"] + d["pz"] * vm ** 2, d["qq"] + d["qz"] * vm ** 2
        pg, qg = gen_pq.get(n, (0.0, 0.0))
        kind = mp.gen_at.get(n, ("", 0))[0]
        typ = "REF" if kind == "ext_grid" else "PV" if (kind == "generator" and net.generators[mp.gen_at[n][1]].control_voltage) else "PQ"
        st = _status_v(vm, b.vmin, b.vmax)
        out.buses.append(BusRow(n, b.name, typ, vm, vm * b.base_kv, va, pg, qg, pd, qd,
                                pg - pd, qg - qd, b.vmin, b.vmax, st))
        if st != "ok":
            out.violations.append(("bus", n, f"{b.name}: V = {vm:.4f} pu is {'below Vmin' if st == 'under' else 'above Vmax'} "
                                             f"({b.vmin:.3f}-{b.vmax:.3f})"))

    # ---- branches
    rows = {}
    if len(res.branch_results) != len(mp.branch_ids):
        raise RuntimeError("engine returned %d branch results for %d in-service branches"
                           % (len(res.branch_results), len(mp.branch_ids)))
    for bid, r in zip(mp.branch_ids, res.branch_results):
        br = net.branches[bid]
        load = max(r.sf_mva, r.st_mva) / br.rating_mva * 100.0 if br.rating_mva > 0 else float("nan")
        st = "ok" if not (load == load) or load < 80 else "warn" if load <= 100 else "over"
        rows[bid] = BranchRow(bid, br.name, br.from_bus, br.to_bus, True, r.pf_mw, r.qf_mvar, r.pt_mw,
                              r.qt_mvar, r.sf_mva, r.st_mva, load, r.p_loss_mw, r.q_loss_mvar, st)
        if st != "ok":
            out.violations.append(("branch", bid, f"{br.name}: loading {load:.1f} % of {br.rating_mva:g} MVA"))
    for bid, br in sorted(net.branches.items()):
        out.branches.append(rows.get(bid) or BranchRow(bid, br.name, br.from_bus, br.to_bus, False,
                                                        0, 0, 0, 0, 0, 0, float("nan"), 0, 0, "out"))

    out.total_loss_mw, out.total_loss_mvar = float(res.total_p_loss_mw), float(res.total_q_loss_mvar)
    out.total_gen_mw = sum(g.p for g in out.gens)
    out.total_load_mw = sum(b.pd for b in out.buses)

    # ---- sanity check of the engine's injection convention
    shunt_p = sum(mp.fixed_gs[n] * out.vm_va[n][0] ** 2 for n in mp.fixed_gs)
    imb = out.total_gen_mw - out.total_load_mw - shunt_p - out.total_loss_mw
    if abs(imb) > 0.01:
        out.log.append(f"WARNING: power balance off by {imb:.4f} MW - check the engine's P-injection convention")
    return out

def worst_mismatch_buses(enet, res, n: int = 3) -> List[int]:
    """Buses with the largest P/Q mismatch at the last iterate (for divergence highlighting)."""
    try:
        idx_mod = importlib.import_module(f"{ENGINE_PKG}.indexing")
        pow_mod = importlib.import_module(f"{ENGINE_PKG}.power")
        comp = idx_mod.compile_network(enet)
        vm, va = np.asarray(res.voltage_magnitude), np.asarray(res.voltage_angle_deg)
        order = [comp.index.bus_id_to_idx[int(b)] for b in res.bus_ids]
        VM, VA = np.empty(len(vm)), np.empty(len(vm))
        VM[order], VA[order] = vm, np.deg2rad(va)
        p, q = pow_mod.calculate_power(comp.Ybus, VM, VA)
        mis = np.zeros(len(VM))
        pvpq, pq = comp.index.pvpq, comp.index.pq
        mis[pvpq] = np.abs(comp.p_spec[pvpq] - p[pvpq])
        mis[pq] = np.maximum(mis[pq], np.abs(comp.q_spec[pq] - q[pq]))
        top = np.argsort(-mis)[:n]
        return [int(comp.index.idx_to_bus_id[i]) for i in top if mis[i] > 0]
    except Exception:
        return []


def run_study(net: Network, settings: StudySettings, prev: Optional[GuiResults] = None) -> GuiResults:
    issues = check_network(net)
    errs = [i for i in issues if i.severity == ERROR]
    if errs:
        r = GuiResults(message="Network check failed: " + "; ".join(str(e) for e in errs[:3]))
        r.log.append(r.message)
        return r
    _, S = _eng()
    enet, mp = to_engine(net, settings, prev)
    opts = S.StudyOptions(tolerance=settings.tolerance, max_iterations=settings.max_iterations,
                          flat_start=settings.flat_start, enforce_q_limits=settings.enforce_q_limits)
    res = S.solve_load_flow(enet, opts)
    out = map_results(net, mp, res, settings)
    if not out.converged:
        out.worst_buses = worst_mismatch_buses(enet, res)
        out.log.append(f"DIVERGED - last max mismatch {out.max_mismatch:.3e} pu; worst buses: {out.worst_buses}")
    else:
        out.log.append(f"Converged in {out.iterations} iterations, {out.solve_time_ms:.2f} ms")
    if settings.enforce_q_limits:
        out.log.append("note: Q-limit switching is performed by the engine; violations shown in the Generators tab")
    return out
