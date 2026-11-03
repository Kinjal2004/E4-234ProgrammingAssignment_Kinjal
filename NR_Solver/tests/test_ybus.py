import numpy as np
from loadflow.models import Network, Bus, Branch
from loadflow.ybus import build_ybus


def test_two_bus_line_ybus():
    buses = [
        Bus(1, 3, 0, 0, 0, 0, 100, 1, 0, 1.1, 0.9),
        Bus(2, 1, 0, 0, 0, 0, 100, 1, 0, 1.1, 0.9),
    ]
    branches = [Branch(1, 2, 0.0, 0.1, 0.02, 100, 1, 0, True)]
    net = Network(100, buses, [], branches)
    Y = build_ybus(net, {1: 0, 2: 1}).toarray()
    y = 1 / (0.1j)
    assert np.allclose(Y[0, 0], y + 0.01j)
    assert np.allclose(Y[0, 1], -y)
    assert np.allclose(Y[1, 0], -y)
