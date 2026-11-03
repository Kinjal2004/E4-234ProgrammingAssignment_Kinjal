"""Report / export helpers (no Qt): HTML report, CSV and XLSX results export."""
from __future__ import annotations

import csv
import html
from datetime import datetime
from typing import List, Tuple

BUS_HDR = ["Bus", "Name", "Type", "V (pu)", "V (kV)", "Angle (deg)", "Pg (MW)", "Qg (MVAr)", "Pd (MW)", "Qd (MVAr)",
           "P inj (MW)", "Q inj (MVAr)", "Status"]
BR_HDR = ["ID", "Name", "From", "To", "Pf (MW)", "Qf (MVAr)", "Pt (MW)", "Qt (MVAr)", "Sf (MVA)", "St (MVA)",
          "Loading (%)", "P loss (MW)", "Q loss (MVAr)", "Status"]
GEN_HDR = ["Type", "ID", "Name", "Bus", "P (MW)", "Q (MVAr)", "Qmin", "Qmax", "Status"]


def tables(res) -> List[Tuple[str, List[str], List[list]]]:
    bus = [[b.number, b.name, b.bus_type, round(b.vm, 5), round(b.kv, 3), round(b.va, 4), round(b.pg, 3), round(b.qg, 3),
            round(b.pd, 3), round(b.qd, 3), round(b.p_inj, 3), round(b.q_inj, 3), b.status] for b in res.buses]
    br = [[r.id, r.name, r.from_bus, r.to_bus, round(r.pf, 3), round(r.qf, 3), round(r.pt, 3), round(r.qt, 3), round(r.sf, 3),
           round(r.st, 3), "" if r.loading != r.loading else round(r.loading, 1), round(r.ploss, 4), round(r.qloss, 4), r.status]
          for r in res.branches]
    gen = [[g.kind, g.id, g.name, g.bus, round(g.p, 3), round(g.q, 3), g.qmin, g.qmax, g.status] for g in res.gens]
    return [("Bus results", BUS_HDR, bus), ("Branch results", BR_HDR, br), ("Generators", GEN_HDR, gen)]


def export_csv(res, path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        for title, hdr, rows in tables(res):
            w.writerow([title])
            w.writerow(hdr)
            w.writerows(rows)
            w.writerow([])
        w.writerow(["Total losses (MW)", round(res.total_loss_mw, 4), "Total losses (MVAr)", round(res.total_loss_mvar, 4)])


def export_xlsx(res, path: str) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for title, hdr, rows in tables(res):
        ws = wb.create_sheet(title)
        ws.append(hdr)
        for r in rows:
            ws.append(r)
    ws = wb.create_sheet("Summary")
    for row in (["Converged", res.converged], ["Iterations", res.iterations], ["Max mismatch (pu)", res.max_mismatch],
                ["Solve time (ms)", round(res.solve_time_ms, 3)], ["Total P loss (MW)", round(res.total_loss_mw, 4)],
                ["Total Q loss (MVAr)", round(res.total_loss_mvar, 4)]):
        ws.append(row)
    wb.save(path)


def _table(hdr, rows, cls="") -> str:
    h = "".join(f"<th>{html.escape(str(x))}</th>" for x in hdr)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table border='1' cellspacing='0' cellpadding='3' {cls}><tr>{h}</tr>{body}</table>"


def build_html(net, res, settings, img_src: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    s = [f"<html><head><meta charset='utf-8'><title>{html.escape(net.name)} - load flow report</title>"
         "<style>body{font-family:Arial,sans-serif;font-size:10pt}table{border-collapse:collapse;font-size:8pt}"
         "th{background:#e8e8e8}h2{margin-top:18px}</style></head><body>",
         f"<h1>Load flow report: {html.escape(net.name)}</h1><p>{stamp} &middot; base {net.base_mva:g} MVA, {net.freq_hz:g} Hz</p>",
         "<h2>Summary</h2>",
         _table(["Item", "Value"], [
             ["Status", "converged" if res.converged else "DIVERGED"], ["Iterations", res.iterations],
             ["Max mismatch (pu)", f"{res.max_mismatch:.3e}"], ["Solve time (ms)", f"{res.solve_time_ms:.2f}"],
             ["Tolerance (pu)", f"{settings.tolerance:g}"], ["Start", "flat" if settings.flat_start else "previous solution"],
             ["Load scaling", f"{settings.load_scale:g}"], ["Q limits enforced", settings.enforce_q_limits],
             ["Total generation (MW)", f"{res.total_gen_mw:.3f}"], ["Total load (MW)", f"{res.total_load_mw:.3f}"],
             ["Total P losses (MW)", f"{res.total_loss_mw:.4f}"], ["Total Q losses (MVAr)", f"{res.total_loss_mvar:.4f}"]]),
         "<h2>Diagram</h2>", f"<img src='{img_src}' width='680'>"]
    if res.violations:
        s += ["<h2>Violations</h2>", _table(["Type", "ID", "Description"], [list(v) for v in res.violations])]
    for title, hdr, rows in tables(res):
        s += [f"<h2>{title}</h2>", _table(hdr, rows)]
    s.append("</body></html>")
    return "".join(s)
