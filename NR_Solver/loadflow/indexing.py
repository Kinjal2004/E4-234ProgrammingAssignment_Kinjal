import numpy as np
from .models import BusIndex, CompiledNetwork
from .ybus import build_ybus


def compile_network(network):
    active_buses = [b for b in network.buses if b.bus_type != 4]
    ids = [b.bus_id for b in active_buses]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate bus numbers")
    mapping = {bus_id: i for i, bus_id in enumerate(ids)}
    n = len(ids)

    ref = np.array([i for i, b in enumerate(active_buses) if b.bus_type == 3], dtype=int)
    pv = np.array([i for i, b in enumerate(active_buses) if b.bus_type == 2], dtype=int)
    pq = np.array([i for i, b in enumerate(active_buses) if b.bus_type == 1], dtype=int)
    if len(ref) != 1:
        raise ValueError(f"Expected exactly one in-service reference bus; found {len(ref)}")

    # Include PV buses with an in-service generator; preserve MATPOWER bus types.
    for g in network.generators:
        if g.status and g.bus_id not in mapping:
            raise ValueError(f"Generator references unavailable bus {g.bus_id}")
    for br in network.branches:
        if br.status and (br.from_bus not in mapping or br.to_bus not in mapping):
            raise ValueError(f"In-service branch references unavailable bus: {br}")

    index = BusIndex(mapping, ids, ref, pv, pq, np.sort(np.r_[pv, pq]))
    Ybus = build_ybus(network, mapping, ids)

    p_spec = np.zeros(n)
    q_spec = np.zeros(n)
    for i, bus in enumerate(active_buses):
        p_spec[i] -= bus.pd_mw / network.base_mva
        q_spec[i] -= bus.qd_mvar / network.base_mva

    for gen in network.generators:
        if gen.status:
            i = mapping[gen.bus_id]
            p_spec[i] += gen.pg_mw / network.base_mva
            # Q at PV and REF buses is solved, not specified in mismatch.
            if active_buses[i].bus_type == 1:
                q_spec[i] += gen.qg_mvar / network.base_mva

    vm0 = np.array([b.vm for b in active_buses], dtype=float)
    va0 = np.deg2rad([b.va_deg for b in active_buses])
    # Enforce generator voltage setpoint at PV and REF buses.
    for gen in network.generators:
        if gen.status and gen.bus_id in mapping:
            i = mapping[gen.bus_id]
            if active_buses[i].bus_type in (2, 3):
                vm0[i] = gen.vg_pu

    return CompiledNetwork(network, Ybus, index, p_spec, q_spec, vm0, va0)
