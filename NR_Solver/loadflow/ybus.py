import numpy as np
from scipy.sparse import coo_matrix


def build_ybus(network, bus_id_to_idx, idx_to_bus_id=None):
    """Construct MATPOWER-convention Ybus, including complex off-nominal taps."""
    rows, cols, data = [], [], []
    n = len(bus_id_to_idx)

    def stamp(i, k, value):
        rows.append(i)
        cols.append(k)
        data.append(value)

    for br in network.branches:
        if not br.status:
            continue
        if br.from_bus not in bus_id_to_idx or br.to_bus not in bus_id_to_idx:
            continue
        if br.r_pu == 0 and br.x_pu == 0:
            raise ValueError(f"Branch {br.from_bus}-{br.to_bus} has zero impedance")
        f = bus_id_to_idx[br.from_bus]
        t = bus_id_to_idx[br.to_bus]
        y = 1.0 / complex(br.r_pu, br.x_pu)
        b = 1j * br.b_pu
        tap_mag = br.tap if br.tap != 0 else 1.0
        tap = tap_mag * np.exp(1j * np.deg2rad(br.shift_deg))

        yff = (y + b / 2.0) / (abs(tap) ** 2)
        yft = -y / np.conj(tap)
        ytf = -y / tap
        ytt = y + b / 2.0

        stamp(f, f, yff)
        stamp(f, t, yft)
        stamp(t, f, ytf)
        stamp(t, t, ytt)

    # MATPOWER Gs/Bs are MW/MVAr consumed/injected at |V|=1 pu.
    for bus in network.buses:
        if bus.bus_id not in bus_id_to_idx:
            continue
        i = bus_id_to_idx[bus.bus_id]
        ysh = complex(bus.gs_mw, bus.bs_mvar) / network.base_mva
        if ysh != 0:
            stamp(i, i, ysh)

    return coo_matrix((np.asarray(data, dtype=complex), (rows, cols)),
                      shape=(n, n)).tocsr()
