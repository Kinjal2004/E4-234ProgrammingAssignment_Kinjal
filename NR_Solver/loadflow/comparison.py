import json
from pathlib import Path
import numpy as np


def load_reference(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare_result(network, result, reference):
    """Compare local result with MATPOWER JSON reference."""
    ref_bus_ids = np.asarray(reference["bus"]["bus_id"], dtype=int)
    ref_vm = np.asarray(reference["bus"]["Vm"], dtype=float)
    ref_va = np.asarray(reference["bus"]["Va"], dtype=float)

    our_ids = np.asarray(result.bus_ids, dtype=int)
    if not np.array_equal(our_ids, ref_bus_ids):
        raise ValueError("Bus ordering/IDs differ between local solver and reference")

    vm_err = np.abs(result.voltage_magnitude - ref_vm)
    # Wrap angle difference to [-180, 180] before taking absolute error.
    va_delta = (result.voltage_angle_deg - ref_va + 180.0) % 360.0 - 180.0
    va_err = np.abs(va_delta)

    # print("\n--- ANGLE DIAGNOSTICS ---")

    # # Print all buses with significant angle differences
    # for i, bus_id in enumerate(ref_bus_ids):
    #     if va_err[i] > 0.01:
    #         print(
    #             f"Bus {bus_id}: "
    #             f"Local={result.voltage_angle_deg[i]:.6f}°, "
    #             f"Reference={ref_va[i]:.6f}°, "
    #             f"Difference={va_delta[i]:.6f}°"
    #         )

    # # Check whether the difference is approximately uniform
    # print(f"Mean angle difference: {np.mean(va_delta):.6f}°")
    # print(f"Minimum angle difference: {np.min(va_delta):.6f}°")
    # print(f"Maximum angle difference: {np.max(va_delta):.6f}°")

    # # Identify reference/slack bus
    # slack_indices = [
    # i for i, bus in enumerate(network.buses)
    # if bus.bus_type == 3
    # ]

    # for i in slack_indices:
    #     print(
    #         f"Slack bus {our_ids[i]}: "
    #         f"Local={result.voltage_angle_deg[i]:.6f}°, "
    #         f"Reference={ref_va[i]:.6f}°"
    #     )

    #     worst_idx = np.argmax(va_err)
    
    ref_br = reference["branch"]
    ref_from = np.asarray(ref_br["from_bus"], dtype=int)
    ref_to = np.asarray(ref_br["to_bus"], dtype=int)
    ref_pf = np.asarray(ref_br["PF"], dtype=float)
    ref_qf = np.asarray(ref_br["QF"], dtype=float)
    ref_pt = np.asarray(ref_br["PT"], dtype=float)
    ref_qt = np.asarray(ref_br["QT"], dtype=float)

    our_br = result.branch_results
    if len(our_br) != len(ref_from):
        raise ValueError(
            f"Branch count differs: local={len(our_br)}, reference={len(ref_from)}. "
            "Check out-of-service branch handling and reference export."
        )

    # Compare by ordered from/to bus pair, not just assuming identical row order.
    branch_errors = []
    used = set()
    for item in our_br:
        match = None
        for j, (f, t) in enumerate(zip(ref_from, ref_to)):
            if j not in used and int(f) == item.from_bus and int(t) == item.to_bus:
                match = j
                break
        if match is None:
            raise ValueError(f"Could not match branch {item.from_bus}->{item.to_bus}")
        used.add(match)
        branch_errors.extend([
            abs(item.pf_mw - ref_pf[match]),
            abs(item.qf_mvar - ref_qf[match]),
            abs(item.pt_mw - ref_pt[match]),
            abs(item.qt_mvar - ref_qt[match]),
        ])

    max_branch_error = max(branch_errors, default=0.0)
    ref_loss = float(reference["summary"]["total_p_loss_mw"])
    ref_qloss = float(reference["summary"]["total_q_loss_mvar"])
    our_loss = float(result.total_p_loss_mw)
    our_qloss = float(result.total_q_loss_mvar)

    if abs(ref_loss) > 1e-12:
        loss_error_pct = abs(our_loss - ref_loss) / abs(ref_loss) * 100.0
    else:
        loss_error_pct = 0.0 if abs(our_loss) <= 1e-12 else float("inf")

    # Also report reactive-loss discrepancy for diagnosis, though assignment criterion
    # specifies total system losses without separating P and Q.
    qloss_error = abs(our_qloss - ref_qloss)

    checks = {
        "convergence": bool(result.converged and result.max_mismatch <= 1e-8),
        "voltage_magnitude": bool(np.max(vm_err, initial=0.0) <= 1e-4),
        "voltage_angle": bool(np.max(va_err, initial=0.0) <= 0.01),
        "branch_flows": bool(max_branch_error <= 0.01),
        "total_losses": bool(loss_error_pct <= 0.1),
    }

    return {
        "checks": checks,
        "max_mismatch_pu": float(result.max_mismatch),
        "max_vm_error_pu": float(np.max(vm_err, initial=0.0)),
        "max_va_error_deg": float(np.max(va_err, initial=0.0)),
        "max_branch_pq_error_mw_mvar": float(max_branch_error),
        "our_p_loss_mw": our_loss,
        "reference_p_loss_mw": ref_loss,
        "p_loss_error_percent": float(loss_error_pct),
        "q_loss_error_mvar": float(qloss_error),
        "iterations": int(result.iterations),
        "reference_iterations": int(reference["summary"]["iterations"]),
    }
