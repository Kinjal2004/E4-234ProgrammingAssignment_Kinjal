import numpy as np
from .models import BranchResult
from .power import calculate_power


def calculate_branch_results(network, mapping, vm, va):
    V = vm * np.exp(1j * va)
    results = []
    for br in network.branches:
        if not br.status or br.from_bus not in mapping or br.to_bus not in mapping:
            continue
        f, t = mapping[br.from_bus], mapping[br.to_bus]
        y = 1.0 / complex(br.r_pu, br.x_pu)
        tap_mag = br.tap if br.tap != 0 else 1.0
        tap = tap_mag * np.exp(1j * np.deg2rad(br.shift_deg))
        ysh = 1j * br.b_pu
        yff = (y + ysh / 2) / (abs(tap) ** 2)
        yft = -y / np.conj(tap)
        ytf = -y / tap
        ytt = y + ysh / 2
        If = yff * V[f] + yft * V[t]
        It = ytf * V[f] + ytt * V[t]
        Sf = V[f] * np.conj(If) * network.base_mva
        St = V[t] * np.conj(It) * network.base_mva
        sf, st = abs(Sf), abs(St)
        rating = br.rate_a_mva
        loading = max(sf, st) / rating * 100 if rating > 0 else 0.0
        results.append(BranchResult(
            br.from_bus, br.to_bus, Sf.real, Sf.imag, St.real, St.imag,
            sf, st, loading, (Sf + St).real, (Sf + St).imag
        ))
    return results


def make_result(network, compiled, vm, va, converged, iterations, mismatch,
                elapsed, history, message):
    p, q = calculate_power(compiled.Ybus, vm, va)
    p_mw = p * network.base_mva
    q_mvar = q * network.base_mva
    branch_results = calculate_branch_results(
        network, compiled.index.bus_id_to_idx, vm, va)
    return {
        "converged": converged,
        "iterations": iterations,
        "max_mismatch": mismatch,
        "solve_time": elapsed,
        "bus_ids": list(compiled.index.idx_to_bus_id),
        "voltage_magnitude": vm.copy(),
        "voltage_angle_deg": np.rad2deg(va.copy()),
        "p_injection_mw": p_mw,
        "q_injection_mvar": q_mvar,
        "branch_results": branch_results,
        "total_p_loss_mw": sum(x.p_loss_mw for x in branch_results),
        "total_q_loss_mvar": sum(x.q_loss_mvar for x in branch_results),
        "mismatch_history": list(history),
        "message": message,
    }
