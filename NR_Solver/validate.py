
"""Combined numerical validation and performance benchmarking.

Compares the local Newton-Raphson solver against MATPOWER reference JSON
and measures average execution time.

Usage:
python validate.py data/case14.m data/case30.m data/case57.m data/case118.m \
    --reference-dir reference_results --runs 5
"""

import argparse
import statistics
from pathlib import Path
from loadflow.matpower_parser import load_case
from loadflow.solver import solve_load_flow, StudyOptions
from loadflow.comparison import load_reference, compare_result


def main():
    parser = argparse.ArgumentParser(
        description="Validate and benchmark the Newton-Raphson load-flow solver."
    )
    parser.add_argument("cases", nargs="+", help="MATPOWER .m case files")
    parser.add_argument("--reference-dir", default="reference_results")
    parser.add_argument("--tol", type=float, default=1e-8)
    parser.add_argument("--max-iter", type=int, default=20)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    options = StudyOptions(
        tolerance=args.tol,
        max_iterations=args.max_iter,
        flat_start=True,
        enforce_q_limits=False,
    )

    headings = (
        f"{'Case':16} {'Iter':5} {'Mismatch':>12} {'dVm':>10} "
        f"{'dVa(deg)':>10} {'dBranch':>10} {'dLoss(%)':>10} "
        f"{'Avg ms':>12} {'ms/iter':>12} {'Overall':>9}"
    )

    print(headings)
    print("-" * len(headings))

    all_pass = True

    for case_file in args.cases:
        case_path = Path(case_file)
        ref_path = Path(args.reference_dir) / f"{case_path.stem}.json"

        if not ref_path.exists():
            raise FileNotFoundError(
                f"Missing {ref_path}. Run generate_reference.m first."
            )

        network = load_case(case_path)
        reference = load_reference(ref_path)

        # Warm-up run: excluded from benchmark statistics.
        warmup = solve_load_flow(network, options)

        # Numerical validation result.
        result = solve_load_flow(network, options)
        report = compare_result(network, result, reference)

        checks = dict(report["checks"])

        if case_path.stem.lower() in ("case118", "case_ieee118"):
            checks["ieee118_iterations"] = result.iterations <= 7

        passed = result.converged and all(checks.values())
        all_pass = all_pass and passed

        # Performance measurements.
        benchmark_results = [
            solve_load_flow(network, options)
            for _ in range(args.runs)
        ]

        avg_ms = statistics.mean(
            r.solve_time for r in benchmark_results
        ) * 1000.0

        iterations = result.iterations
        per_iter = avg_ms / iterations if iterations else float("nan")

        print(
            f"{case_path.stem:16} {iterations:5d} "
            f"{report['max_mismatch_pu']:12.3e} "
            f"{report['max_vm_error_pu']:10.3e} "
            f"{report['max_va_error_deg']:10.4f} "
            f"{report['max_branch_pq_error_mw_mvar']:10.4f} "
            f"{report['p_loss_error_percent']:10.4f} "
            f"{avg_ms:12.4f} {per_iter:12.4f} "
            f"{'PASS' if passed else 'FAIL':>9}"
        )

        for name, ok in checks.items():
            print(f"    {'PASS' if ok else 'FAIL'}  {name}")

        print(
            f"    Local/reference iterations: "
            f"{result.iterations}/{report['reference_iterations']}; "
            f"P loss local/reference: "
            f"{report['our_p_loss_mw']:.6f}/"
            f"{report['reference_p_loss_mw']:.6f} MW"
        )

        print(
            f"    Average solve time: {avg_ms:.4f} ms "
            f"over {args.runs} runs; "
            f"average time per iteration: {per_iter:.4f} ms"
        )

        if not result.converged:
            print("    Solver:", result.message)

    print("\nOVERALL VALIDATION:", "PASS" if all_pass else "FAIL")
    raise SystemExit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
