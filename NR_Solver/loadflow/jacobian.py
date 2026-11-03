import numpy as np
from scipy.sparse import coo_matrix


def build_jacobian(Ybus, vm, va, p_calc, q_calc, pvpq, pq):
    """Analytical polar-coordinate NR Jacobian in [P(PV+PQ), Q(PQ)] order."""
    G = Ybus.real.toarray()
    B = Ybus.imag.toarray()
    n = len(vm)
    nang = len(pvpq)
    nvolt = len(pq)

    H = np.zeros((nang, nang))
    N = np.zeros((nang, nvolt))
    M = np.zeros((nvolt, nang))
    L = np.zeros((nvolt, nvolt))

    p_row = {bus: r for r, bus in enumerate(pvpq)}
    q_row = {bus: r for r, bus in enumerate(pq)}
    ang_col = {bus: c for c, bus in enumerate(pvpq)}
    volt_col = {bus: c for c, bus in enumerate(pq)}

    for i in pvpq:
        ri = p_row[i]
        for k in pvpq:
            ck = ang_col[k]
            if i == k:
                H[ri, ck] = -q_calc[i] - B[i, i] * vm[i] ** 2
            else:
                d = va[i] - va[k]
                H[ri, ck] = vm[i] * vm[k] * (
                    G[i, k] * np.sin(d) - B[i, k] * np.cos(d))

        for k in pq:
            ck = volt_col[k]
            if i == k:
                N[ri, ck] = p_calc[i] / vm[i] + G[i, i] * vm[i]
            else:
                d = va[i] - va[k]
                N[ri, ck] = vm[i] * (
                    G[i, k] * np.cos(d) + B[i, k] * np.sin(d))

    for i in pq:
        ri = q_row[i]
        for k in pvpq:
            ck = ang_col[k]
            if i == k:
                M[ri, ck] = p_calc[i] - G[i, i] * vm[i] ** 2
            else:
                d = va[i] - va[k]
                M[ri, ck] = -vm[i] * vm[k] * (
                    G[i, k] * np.cos(d) + B[i, k] * np.sin(d))

        for k in pq:
            ck = volt_col[k]
            if i == k:
                L[ri, ck] = q_calc[i] / vm[i] - B[i, i] * vm[i]
            else:
                d = va[i] - va[k]
                L[ri, ck] = vm[i] * (
                    G[i, k] * np.sin(d) - B[i, k] * np.cos(d))

    J = np.block([[H, N], [M, L]])
    return coo_matrix(J).tocsc()
