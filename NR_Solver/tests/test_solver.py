from loadflow.models import Network, Bus, Generator, Branch
from loadflow.solver import solve_load_flow, StudyOptions


def test_two_bus_load_flow_converges():
    buses = [
        Bus(1, 3, 0, 0, 0, 0, 100, 1.0, 0.0, 1.1, 0.9),
        Bus(2, 1, 50, 30, 0, 0, 100, 1.0, 0.0, 1.1, 0.9),
    ]
    gens = [Generator(1, 50, 0, 100, -100, 1.0, 100, True, 200, 0)]
    branches = [Branch(1, 2, 0.01, 0.1, 0.02, 100, 1.0, 0, True)]
    net = Network(100, buses, gens, branches)
    result = solve_load_flow(net, StudyOptions(tolerance=1e-10, max_iterations=30))
    assert result.converged
    assert result.max_mismatch < 1e-10
    assert result.voltage_magnitude[1] < 1.0
