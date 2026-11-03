from dataclasses import dataclass, field
from typing import List
import numpy as np
from scipy.sparse import csr_matrix


@dataclass
class Bus:
    bus_id: int
    bus_type: int                 # MATPOWER: 1 PQ, 2 PV, 3 REF, 4 isolated
    pd_mw: float
    qd_mvar: float
    gs_mw: float
    bs_mvar: float
    base_kv: float
    vm: float
    va_deg: float
    vmax: float
    vmin: float
    area: int = 1


@dataclass
class Generator:
    bus_id: int
    pg_mw: float
    qg_mvar: float
    qmax_mvar: float
    qmin_mvar: float
    vg_pu: float
    mbase_mva: float
    status: bool
    pmax_mw: float
    pmin_mw: float


@dataclass
class Branch:
    from_bus: int
    to_bus: int
    r_pu: float
    x_pu: float
    b_pu: float
    rate_a_mva: float
    tap: float
    shift_deg: float
    status: bool
    angmin_deg: float = -360.0
    angmax_deg: float = 360.0


@dataclass
class Network:
    base_mva: float
    buses: List[Bus]
    generators: List[Generator]
    branches: List[Branch]
    case_name: str = "MATPOWER case"


@dataclass
class BusIndex:
    bus_id_to_idx: dict
    idx_to_bus_id: list
    ref: np.ndarray
    pv: np.ndarray
    pq: np.ndarray
    pvpq: np.ndarray


@dataclass
class CompiledNetwork:
    network: Network
    Ybus: csr_matrix
    index: BusIndex
    p_spec: np.ndarray            # net specified injection, pu
    q_spec: np.ndarray            # net specified injection, pu
    vm0: np.ndarray
    va0: np.ndarray               # radians


@dataclass
class BranchResult:
    from_bus: int
    to_bus: int
    pf_mw: float
    qf_mvar: float
    pt_mw: float
    qt_mvar: float
    sf_mva: float
    st_mva: float
    loading_percent: float
    p_loss_mw: float
    q_loss_mvar: float


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
    branch_results: list = field(default_factory=list)
    total_p_loss_mw: float = 0.0
    total_q_loss_mvar: float = 0.0
    mismatch_history: list = field(default_factory=list)
    message: str = ""
