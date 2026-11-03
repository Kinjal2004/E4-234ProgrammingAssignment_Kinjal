"""MATPOWER .m case import / export (GUI model <-> text file)."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

import numpy as np

from model import BIG_Q, Network

_MAT = re.compile(r"mpc\.(\w+)\s*=\s*\[(.*?)\]\s*;", re.S)
_BASE = re.compile(r"mpc\.baseMVA\s*=\s*([-+0-9.eE]+)")


def parse_matpower(text: str) -> Dict[str, np.ndarray]:
    """Return {'baseMVA': float, 'bus': 2-D array, 'gen': ..., 'branch': ...}."""
    text = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
    out: Dict[str, object] = {}
    m = _BASE.search(text)
    out["baseMVA"] = float(m.group(1)) if m else 100.0
    for name, body in _MAT.findall(text):
        if name not in ("bus", "gen", "branch"):
            continue
        rows = []
        for row in body.replace("\n", ";").split(";"):
            vals = row.replace(",", " ").split()
            if vals:
                rows.append([float(v) for v in vals])
        out[name] = np.array(rows, dtype=float)
    for k in ("bus", "gen", "branch"):
        if k not in out:
            raise ValueError(f"MATPOWER file has no mpc.{k} matrix")
    return out


def load_matpower(path: str) -> Tuple[Network, List[str]]:
    with open(path) as fh:
        ppc = parse_matpower(fh.read())
    name = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return network_from_ppc(ppc, name)


def network_from_ppc(ppc, name="imported") -> Tuple[Network, List[str]]:
    warnings: List[str] = []
    net = Network(name=name, base_mva=float(ppc["baseMVA"]))
    types = {}
    for r in ppc["bus"]:
        n = int(r[0])
        types[n] = int(r[1])
        net.add_bus(n, base_kv=r[9] if r[9] > 0 else 1.0, vmin=r[12], vmax=r[11], area=int(r[6]))
        if r[2] != 0 or r[3] != 0:
            net.add_load(n, p_mw=r[2], q_mvar=r[3])
        if r[4] != 0 or r[5] != 0:
            net.add_shunt(n, g_mw=r[4], b_mvar=r[5])

    bus_rows = {int(r[0]): r for r in ppc["bus"]}
    gens: Dict[int, List] = {}
    for g in ppc["gen"]:
        if g[7] > 0:  # in service only
            gens.setdefault(int(g[0]), []).append(g)

    for n, t in types.items():
        gl = gens.get(n, [])
        if t == 3:
            vg = gl[0][5] if gl else bus_rows[n][7]
            net.add_ext_grid(n, v_set_pu=vg, angle_deg=bus_rows[n][8])
            if len(gl) > 1:
                warnings.append(f"bus {n}: extra generators at the slack bus ignored")
        elif gl:
            if len(gl) > 1:
                warnings.append(f"bus {n}: {len(gl)} generators merged into one")
            net.add_generator(
                n, p_mw=sum(g[1] for g in gl), v_set_pu=gl[0][5],
                qmin_mvar=sum(g[4] for g in gl), qmax_mvar=sum(g[3] for g in gl),
                control_voltage=(t == 2), q_mvar=sum(g[2] for g in gl))

    for r in ppc["branch"]:
        tap = r[8] if r[8] != 0 else 1.0
        is_tr = tap != 1.0 or r[9] != 0
        net.add_branch(int(r[0]), int(r[1]), r_pu=r[2], x_pu=r[3], b_pu=r[4],
                       rating_mva=r[5], tap=tap, shift_deg=r[9], in_service=bool(r[10] > 0),
                       kind="transformer" if is_tr else "line")
    return net, warnings


def export_matpower(net: Network, path: str) -> None:
    ref = {g.bus for g in net.ext_grids.values() if g.in_service}
    pv = {g.bus for g in net.generators.values() if g.in_service and g.control_voltage}
    lines = ["function mpc = " + (net.name.replace(" ", "_") or "case_export"),
             "mpc.version = '2';", f"mpc.baseMVA = {net.base_mva:g};", "mpc.bus = ["]
    for n in sorted(net.buses):
        b = net.buses[n]
        pd = sum(x.p_mw for x in net.loads.values() if x.bus == n and x.in_service)
        qd = sum(x.q_mvar for x in net.loads.values() if x.bus == n and x.in_service)
        gs = sum(x.g_mw for x in net.shunts.values() if x.bus == n and x.in_service)
        bs = sum(x.b_mvar for x in net.shunts.values() if x.bus == n and x.in_service)
        t = 3 if n in ref else 2 if n in pv else 1
        lines.append(f"\t{n}\t{t}\t{pd:g}\t{qd:g}\t{gs:g}\t{bs:g}\t{b.area}\t1\t0\t{b.base_kv:g}\t1\t{b.vmax:g}\t{b.vmin:g};")
    lines += ["];", "mpc.gen = ["]
    for g in net.ext_grids.values():
        lines.append(f"\t{g.bus}\t0\t0\t{BIG_Q:g}\t{-BIG_Q:g}\t{g.v_set_pu:g}\t{net.base_mva:g}\t{int(g.in_service)}\t{BIG_Q:g}\t0;")
    for g in net.generators.values():
        lines.append(f"\t{g.bus}\t{g.p_mw:g}\t{g.q_mvar:g}\t{g.qmax_mvar:g}\t{g.qmin_mvar:g}\t{g.v_set_pu:g}\t{net.base_mva:g}\t{int(g.in_service)}\t{BIG_Q:g}\t0;")
    lines += ["];", "mpc.branch = ["]
    for b in net.branches.values():
        tap = b.tap if b.tap != 1.0 else 0
        lines.append(f"\t{b.from_bus}\t{b.to_bus}\t{b.r_pu:g}\t{b.x_pu:g}\t{b.b_pu:g}\t{b.rating_mva:g}\t0\t0\t{tap:g}\t{b.shift_deg:g}\t{int(b.in_service)}\t-360\t360;")
    lines.append("];")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
