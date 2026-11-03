import numpy as np


def calculate_power(Ybus, vm, va):
    """Return calculated net bus injections P and Q in per unit."""
    V = vm * np.exp(1j * va)
    I = Ybus @ V
    S = V * np.conj(I)
    return S.real, S.imag
