from dataclasses import dataclass
import time
import numpy as np
from scipy.sparse.linalg import splu
from .indexing import compile_network
from .power import calculate_power
from .jacobian import build_jacobian
from .models import LoadFlowResult
from .results import make_result


@dataclass
class StudyOptions:
    tolerance: float = 1e-8
    max_iterations: int = 20
    flat_start: bool = True
    enforce_q_limits: bool = False
    max_q_passes: int = 30           # outer PV/PQ switching passes
    max_switches_per_bus: int = 4    # anti-cycling


def _newton(compiled, vm, va, tol, max_iter, history):
    """One NR solve with the CURRENT bus types -> (converged, iterations, norm, message)."""
    idx = compiled.index
    nang = len(idx.pvpq)
    for it in range(max_iter + 1):
        p_calc, q_calc = calculate_power(compiled.Ybus, vm, va)
        dp = compiled.p_spec[idx.pvpq] - p_calc[idx.pvpq]
        dq = compiled.q_spec[idx.pq] - q_calc[idx.pq]
        mismatch = np.r_[dp, dq]
        norm = float(np.max(np.abs(mismatch))) if mismatch.size else 0.0
        history.append(norm)
        if norm < tol:
            return True, it, norm, "Converged"
        if it == max_iter:
            return False, it, norm, "Maximum iterations reached"
        J = build_jacobian(compiled.Ybus, vm, va, p_calc, q_calc, idx.pvpq, idx.pq)
        try:
            dx = splu(J).solve(mismatch)
        except Exception as exc:
            return False, it, norm, f"Sparse LU failed: {exc}"
        va[idx.pvpq] += dx[:nang]
        vm[idx.pq] += dx[nang:]
        if np.any(vm[idx.pq] <= 0):
            return False, it + 1, norm, "Diverged: non-positive PQ bus voltage encountered"
    return False, max_iter, norm, "Maximum iterations reached"


def _q_limit_data(network, compiled):
    """Per-bus Qmin/Qmax (MVAr) of in-service gens, bus load Qd (MVAr), voltage set-point."""
    m = compiled.index.bus_id_to_idx
    n = len(compiled.index.idx_to_bus_id)
    qmin, qmax, qd, vset = np.zeros(n), np.zeros(n), np.zeros(n), np.ones(n)
    has = np.zeros(n, dtype=bool)
    for b in network.buses:
        qd[m[b.bus_id]] = b.qd_mvar
    for g in network.generators:
        if g.status and g.bus_id in m:
            i = m[g.bus_id]
            qmin[i] += g.qmin_mvar
            qmax[i] += g.qmax_mvar
            if not has[i]:
                vset[i] = g.vg_pu
            has[i] = True
    return qmin, qmax, qd, vset, has


def _q_limit_pass(compiled, vm, va, data, limited, counts, opts, base, npass, log):
    """Check Q limits once. Edits bus types (idx.pv/pq/pvpq), q_spec, vm, `limited`.
    Returns True if any bus changed type."""
    qmin, qmax, qd, vset, has = data
    idx = compiled.index
    ids = idx.idx_to_bus_id
    _, q_calc = calculate_power(compiled.Ybus, vm, va)
    qg = q_calc * base + qd                       # generator Q at each bus, MVAr
    pv, pq = set(idx.pv.tolist()), set(idx.pq.tolist())
    tol_q, tol_v = 1e-4, 1e-6
    was_limited = dict(limited)                   # only these may switch back this pass
    changed = False

    for i in sorted(pv):                          # ---- PV -> PQ
        if not has[i]:
            continue
        if qg[i] > qmax[i] + tol_q:
            lim, qlim = "Qmax", qmax[i]
        elif qg[i] < qmin[i] - tol_q:
            lim, qlim = "Qmin", qmin[i]
        else:
            continue
        pv.discard(i); pq.add(i)
        compiled.q_spec[i] = (qlim - qd[i]) / base
        limited[i] = lim
        counts[i] = counts.get(i, 0) + 1
        changed = True
        log.append(f"Pass {npass}: bus {ids[i]}: PV -> PQ, Qg = {qg[i]:.2f} MVAr violates {lim} = {qlim:.2f} MVAr (Q fixed at limit)")

    for i, lim in was_limited.items():            # ---- PQ -> PV (voltage recovered)
        if counts.get(i, 0) >= opts.max_switches_per_bus:
            continue                              # anti-cycling: stay PQ
        back = (lim == "Qmax" and vm[i] > vset[i] + tol_v) or (lim == "Qmin" and vm[i] < vset[i] - tol_v)
        if back:
            v_before = vm[i]
            pq.discard(i); pv.add(i)
            vm[i] = vset[i]
            del limited[i]
            changed = True
            log.append(f"Pass {npass}: bus {ids[i]}: PQ -> PV, V = {v_before:.4f} pu passed set-point {vset[i]:.4f} ({lim} released)")

    if changed:
        idx.pv = np.array(sorted(pv), dtype=int)
        idx.pq = np.array(sorted(pq), dtype=int)
        idx.pvpq = np.sort(np.r_[idx.pv, idx.pq]).astype(int)
    return changed


def solve_load_flow(network, options):
    options = options or StudyOptions()
    if options.tolerance <= 0 or options.max_iterations < 1:
        raise ValueError("Tolerance and maximum iterations must be positive")

    compiled = compile_network(network)
    compiled.q_spec = compiled.q_spec.copy()      # edited when buses switch
    idx = compiled.index
    vm = np.ones_like(compiled.vm0) if options.flat_start else compiled.vm0.copy()
    va = np.zeros_like(compiled.va0) if options.flat_start else compiled.va0.copy()
    if options.flat_start:
        for i in np.r_[idx.ref, idx.pv]:
            vm[i] = compiled.vm0[i]
        for i in idx.ref:
            va[i] = compiled.va0[i]

    history, log, limited, counts = [], [], {}, {}
    data = _q_limit_data(network, compiled) if options.enforce_q_limits else None
    total_iters, passes = 0, 0
    start = time.perf_counter()

    while True:
        passes += 1
        converged, k, norm, message = _newton(compiled, vm, va, options.tolerance,
                                              options.max_iterations, history)
        total_iters += k
        if not converged or not options.enforce_q_limits:
            break
        if not _q_limit_pass(compiled, vm, va, data, limited, counts, options,
                             network.base_mva, passes, log):
            break
        if passes >= options.max_q_passes:
            converged, message = False, "Q-limit switching did not settle (max passes reached)"
            log.append(message)
            break

    if options.enforce_q_limits and converged:
        log.append(f"Q-limit enforcement: {passes} pass(es), {len(limited)} generator bus(es) at a limit")
    elapsed = time.perf_counter() - start
    raw = make_result(network, compiled, vm, va, converged, total_iters, norm, elapsed,
                      history, message, log=log, q_limited=limited)
    return LoadFlowResult(**raw)