"""Small parser for standard MATPOWER case files.

Supports numeric mpc.baseMVA and numeric matrix assignments:
mpc.bus, mpc.gen, mpc.branch. It is not a MATLAB interpreter.
"""
import re
from pathlib import Path
from .models import Network, Bus, Generator, Branch


def _strip_comments(text):
    return re.sub(r'%[^\n]*', '', text)


def _extract_scalar(text, name):
    m = re.search(rf'\bmpc\.{name}\s*=\s*([-+]?\d+(?:\.\d*)?(?:[eEdD][-+]?\d+)?)\s*;', text)
    if not m:
        raise ValueError(f"Could not find numeric mpc.{name} assignment")
    return float(m.group(1).replace('D', 'E').replace('d', 'e'))


def _extract_matrix(text, name):
    # Matrix body between [ and ]; comments have already been removed.
    m = re.search(rf'\bmpc\.{name}\s*=\s*\[(.*?)\]\s*;', text, flags=re.S)
    if not m:
        raise ValueError(f"Could not find mpc.{name} matrix")
    body = m.group(1).replace('...', ' ')
    rows = []
    for raw in body.split(';'):
        raw = raw.strip()
        if not raw:
            continue
        # MATPOWER matrices are whitespace-separated numeric columns.
        tokens = raw.replace(',', ' ').split()
        try:
            row = [float(tok.replace('D', 'E').replace('d', 'e')) for tok in tokens]
        except ValueError as exc:
            raise ValueError(
                f"Non-numeric expression in mpc.{name}: {raw!r}. "
                "This importer supports standard numeric case matrices only."
            ) from exc
        rows.append(row)
    if not rows:
        raise ValueError(f"mpc.{name} is empty")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise ValueError(f"Inconsistent row lengths in mpc.{name}")
    return rows


def load_case(path):
    path = Path(path)
    text = _strip_comments(path.read_text(encoding="utf-8"))
    base = _extract_scalar(text, "baseMVA")
    bus_data = _extract_matrix(text, "bus")
    gen_data = _extract_matrix(text, "gen")
    branch_data = _extract_matrix(text, "branch")

    buses = []
    for r in bus_data:
        if len(r) < 13:
            raise ValueError("MATPOWER bus rows must contain at least 13 columns")
        buses.append(Bus(
            bus_id=int(r[0]), bus_type=int(r[1]), pd_mw=r[2], qd_mvar=r[3],
            gs_mw=r[4], bs_mvar=r[5], area=int(r[6]), vm=r[7], va_deg=r[8],
            base_kv=r[9], vmax=r[11], vmin=r[12]
        ))

    generators = []
    for r in gen_data:
        if len(r) < 10:
            raise ValueError("MATPOWER gen rows must contain at least 10 columns")
        generators.append(Generator(
            bus_id=int(r[0]), pg_mw=r[1], qg_mvar=r[2], qmax_mvar=r[3],
            qmin_mvar=r[4], vg_pu=r[5], mbase_mva=r[6], status=bool(r[7]),
            pmax_mw=r[8], pmin_mw=r[9]
        ))

    branches = []
    for r in branch_data:
        if len(r) < 11:
            raise ValueError("MATPOWER branch rows must contain at least 11 columns")
        tap = r[8] if r[8] != 0 else 1.0
        branches.append(Branch(
            from_bus=int(r[0]), to_bus=int(r[1]), r_pu=r[2], x_pu=r[3],
            b_pu=r[4], rate_a_mva=r[5], tap=tap, shift_deg=r[9],
            status=bool(r[10]),
            angmin_deg=r[11] if len(r) > 11 else -360.0,
            angmax_deg=r[12] if len(r) > 12 else 360.0
        ))

    return Network(base, buses, generators, branches, path.stem)
