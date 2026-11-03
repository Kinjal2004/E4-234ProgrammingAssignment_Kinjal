from dataclasses import dataclass
import time
import numpy as np
from scipy.sparse.linalg import splu
from .indexing import compile_network
from .power import calculate_power
from .jacobian import build_jacobian
from .results import make_result


@dataclass
class StudyOptions:
    tolerance: float = 1e-8
    max_iterations: int = 20
    flat_start: bool = True
    enforce_q_limits: bool = False  # Reserved for next implementation stage


@dataclass
class LoadFlowResult:
    converged: bool
    iterations: int
    max_mismatch: float
    solve_time: float
    bus_ids: list
    voltage_magnitude: np.ndarray
    voltage_angle_deg: np.ndarray
    p_injection_mw: np.ndarray
    q_injection_mvar: np.ndarray
    branch_results: list
    total_p_loss_mw: float
    total_q_loss_mvar: float
    mismatch_history: list
    message: str


def solve_load_flow(network, options):
    options = options or StudyOptions()
    if options.tolerance <= 0 or options.max_iterations < 1:
        raise ValueError("Tolerance and maximum iterations must be positive")

    compiled = compile_network(network)
    idx = compiled.index
    vm = np.ones_like(compiled.vm0) if options.flat_start else compiled.vm0.copy()
    va = np.zeros_like(compiled.va0) if options.flat_start else compiled.va0.copy()

    # A flat start must still honour specified PV and reference voltage magnitudes.
    if options.flat_start:
        for i in np.r_[idx.ref, idx.pv]:
            vm[i] = compiled.vm0[i]
        for i in idx.ref:
            va[i] = compiled.va0[i]

    history = []
    start = time.perf_counter()
    converged = False
    mismatch_norm = float("inf")
    iterations = 0
    message = "Maximum iterations reached"

    for iteration in range(options.max_iterations + 1):
        p_calc, q_calc = calculate_power(compiled.Ybus, vm, va)
        dp = compiled.p_spec[idx.pvpq] - p_calc[idx.pvpq]
        dq = compiled.q_spec[idx.pq] - q_calc[idx.pq]
        mismatch = np.r_[dp, dq]
        mismatch_norm = float(np.max(np.abs(mismatch))) if mismatch.size else 0.0
        history.append(mismatch_norm)
        iterations = iteration

        if mismatch_norm < options.tolerance:
            converged = True
            message = "Converged"
            break
        if iteration == options.max_iterations:
            break

        J = build_jacobian(compiled.Ybus, vm, va, p_calc, q_calc,
                           idx.pvpq, idx.pq)
        try:
            dx = splu(J).solve(mismatch)
        except Exception as exc:
            message = f"Sparse LU failed: {exc}"
            break

        nang = len(idx.pvpq)
        va[idx.pvpq] += dx[:nang]
        vm[idx.pq] += dx[nang:]

        if np.any(vm[idx.pq] <= 0):
            message = "Diverged: non-positive PQ bus voltage encountered"
            break

    elapsed = time.perf_counter() - start
    raw = make_result(network, compiled, vm, va, converged, iterations,
                      mismatch_norm, elapsed, history, message)
    return LoadFlowResult(**raw)
