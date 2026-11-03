"""Run the local solver on MATPOWER .m cases and print benchmark results.

Usage:
    python validate.py data/case14.m data/case_ieee30.m data/case57.m data/case118.m
"""
import argparse
import statistics
from pathlib import Path
from loadflow.matpower_parser import load_case
from loadflow.solver import solve_load_flow, StudyOptions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="+", help="MATPOWER .m case paths")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--tol", type=float, default=1e-8)
    parser.add_argument("--max-iter", type=int, default=20)
    args = parser.parse_args()

    options = StudyOptions(args.tol, args.max_iter, True, False)
    print(f"{'Case':20s} {'OK':>4s} {'Iter':>6s} {'Avg ms':>12s} {'ms/iter':>12s} {'Mismatch':>13s}")
    for case_path in args.cases:
        network = load_case(case_path)
        # First run warms up imports/linear algebra; all runs are reported.
        results = [solve_load_flow(network, options) for _ in range(args.runs)]
        result = results[-1]
        avg_ms = statistics.mean(r.solve_time for r in results) * 1000
        per_iter = avg_ms / result.iterations if result.iterations else 0.0
        print(f"{Path(case_path).stem:20s} {str(result.converged):>4s} "
              f"{result.iterations:6d} {avg_ms:12.4f} {per_iter:12.4f} "
              f"{result.max_mismatch:13.4e}")
        if not result.converged:
            print("  Solver message:", result.message)


if __name__ == "__main__":
    main()
