"""Auto-layout (Fruchterman-Reingold) for imported networks.  Pure numpy."""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from model import Network

GRID = 20.0


def auto_layout(net: Network, spacing: float = 140.0, seed: int = 1) -> Dict[int, Tuple[float, float]]:
    nums = sorted(net.buses)
    n = len(nums)
    if n == 0:
        return {}
    idx = {b: i for i, b in enumerate(nums)}
    rng = np.random.default_rng(seed)
    side = spacing * np.sqrt(n)
    pos = rng.random((n, 2)) * side
    edges = {(idx[b.from_bus], idx[b.to_bus]) for b in net.branches.values()
             if b.from_bus in idx and b.to_bus in idx}
    k = side / np.sqrt(n)
    temp = side / 8.0
    for _ in range(300):
        d = pos[:, None, :] - pos[None, :, :]
        dist = np.maximum(np.linalg.norm(d, axis=2), 1e-3)
        disp = (d / dist[..., None] * (k * k / dist)[..., None]).sum(axis=1)     # repulsion
        for a, b in edges:
            v = pos[a] - pos[b]
            dl = max(np.linalg.norm(v), 1e-3)
            f = v / dl * (dl * dl / k)
            disp[a] -= f
            disp[b] += f
        length = np.maximum(np.linalg.norm(disp, axis=1), 1e-3)
        pos += disp / length[:, None] * np.minimum(length, temp)[:, None]
        temp *= 0.98
    pos -= pos.min(axis=0)
    pos = np.round(pos / GRID) * GRID
    return {b: (float(pos[i, 0]), float(pos[i, 1])) for b, i in idx.items()}
