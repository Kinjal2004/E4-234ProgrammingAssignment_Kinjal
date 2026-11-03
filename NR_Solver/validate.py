
"""Combined numerical validation and performance benchmarking.

Validates the Newton-Raphson load-flow solver against MATPOWER reference
results and measures execution time.

Usage:
python validate.py data/case14.m data/case30.m data/case57.m data/case118.m \
    --reference-dir reference_results --runs 10
"""

import argparse
import statistics
from pathlib import Path
from contextlib import redirect_stdout

from loadflow.matpower_parser import load_case
from loadflow.solver import solve_load_flow, StudyOptions
from loadflow.comparison import load_reference, compare_result


# ============================================================
# CONSTANTS: ASSIGNMENT ACCEPTANCE CRITERIA
# ============================================================

VM_LIMIT = 1e-4
VA_LIMIT_DEG = 0.01
BRANCH_LIMIT = 0.01
LOSS_LIMIT_PERCENT = 0.1
IEEE118_MAX_ITER = 7


# ============================================================
# FORMATTING UTILITIES
# ============================================================

def print_section(title):
    print("\n" + "=" * 100)
    print(f"  {title}")
    print("=" * 100)


def print_table(headers, rows):
    """Print neatly aligned text tables."""

    widths = [
        max(
            len(str(header)),
            *(len(str(row[i])) for row in rows)
        )
        for i, header in enumerate(headers)
    ]

    header_line = " | ".join(
        str(header).ljust(widths[i])
        for i, header in enumerate(headers)
    )

    separator = "-+-".join(
        "-" * width for width in widths
    )

    print(header_line)
    print(separator)

    for row in rows:
        print(
            " | ".join(
                str(value).ljust(widths[i])
                for i, value in enumerate(row)
            )
        )

def write_nrlf_results(case_name, network, result, output_file):
    """Write detailed Newton-Raphson load-flow results."""

    # Support both dictionary and object-style results
    def get(field):
        if isinstance(result, dict):
            return result[field]
        return getattr(result, field)

    with open(output_file, "a", encoding="utf-8") as f:

        f.write("\n" + "=" * 110 + "\n")
        f.write(f"NEWTON-RAPHSON LOAD FLOW RESULTS: {case_name.upper()}\n")
        f.write("=" * 110 + "\n\n")

        f.write(f"Converged       : {get('converged')}\n")
        f.write(f"Iterations      : {get('iterations')}\n")
        f.write(f"Maximum mismatch: {get('max_mismatch'):.6e} pu\n")
        f.write(f"Solve time      : {get('solve_time') * 1000:.4f} ms\n")
        f.write(f"Base MVA        : {network.base_mva:.2f}\n")
        f.write(f"Message         : {get('message')}\n")

        # ------------------------------------------------
        # BUS RESULTS
        # ------------------------------------------------

        f.write("\n\nBUS RESULTS\n")
        f.write("-" * 110 + "\n")

        f.write(
            f"{'Bus':>8} {'Type':>8} {'Voltage(pu)':>15} "
            f"{'Angle(deg)':>15} {'P inj(MW)':>15} {'Q inj(MVAr)':>15}\n"
        )

        bus_types = {
            1: "PQ",
            2: "PV",
            3: "REF",
            4: "ISOLATED"
        }

        bus_lookup = {bus.bus_id: bus for bus in network.buses}

        bus_ids = get("bus_ids")
        vm = get("voltage_magnitude")
        va = get("voltage_angle_deg")
        p = get("p_injection_mw")
        q = get("q_injection_mvar")

        for i, bus_id in enumerate(bus_ids):
            bus = bus_lookup[bus_id]

            f.write(
                f"{bus_id:>8} "
                f"{bus_types.get(bus.bus_type, 'UNKNOWN'):>8} "
                f"{vm[i]:>15.6f} "
                f"{va[i]:>15.6f} "
                f"{p[i]:>15.6f} "
                f"{q[i]:>15.6f}\n"
            )

        # ------------------------------------------------
        # GENERATOR INPUT DATA
        # ------------------------------------------------

        f.write("\n\nGENERATOR DATA (INPUT / SCHEDULED VALUES)\n")
        f.write("-" * 110 + "\n")

        f.write(
            f"{'Bus':>8} {'Pg(MW)':>15} {'Qg initial(MVAr)':>20} "
            f"{'Pmax(MW)':>15} {'Pmin(MW)':>15} {'Status':>10}\n"
        )

        for gen in network.generators:
            if gen.status:
                f.write(
                    f"{gen.bus_id:>8} "
                    f"{gen.pg_mw:>15.6f} "
                    f"{gen.qg_mvar:>20.6f} "
                    f"{gen.pmax_mw:>15.6f} "
                    f"{gen.pmin_mw:>15.6f} "
                    f"{str(gen.status):>10}\n"
                )

        # ------------------------------------------------
        # BRANCH RESULTS
        # ------------------------------------------------

        f.write("\n\nBRANCH POWER FLOW RESULTS\n")
        f.write("-" * 140 + "\n")

        f.write(
            f"{'From':>8} {'To':>8} "
            f"{'Pf(MW)':>13} {'Qf(MVAr)':>13} "
            f"{'Pt(MW)':>13} {'Qt(MVAr)':>13} "
            f"{'Loading(%)':>13} "
            f"{'P loss(MW)':>13} {'Q loss(MVAr)':>15}\n"
        )

        for br in get("branch_results"):
            f.write(
                f"{br.from_bus:>8} {br.to_bus:>8} "
                f"{br.pf_mw:>13.6f} {br.qf_mvar:>13.6f} "
                f"{br.pt_mw:>13.6f} {br.qt_mvar:>13.6f} "
                f"{br.loading_percent:>13.4f} "
                f"{br.p_loss_mw:>13.6f} "
                f"{br.q_loss_mvar:>15.6f}\n"
            )

        # ------------------------------------------------
        # SYSTEM LOSSES
        # ------------------------------------------------

        f.write("\n\nTOTAL SYSTEM LOSSES\n")
        f.write("-" * 60 + "\n")

        f.write(
            f"Active power loss   : {get('total_p_loss_mw'):.6f} MW\n"
        )
        f.write(
            f"Reactive power loss : {get('total_q_loss_mvar'):.6f} MVAr\n"
        )

        # ------------------------------------------------
        # MISMATCH HISTORY
        # ------------------------------------------------

        f.write("\n\nNEWTON-RAPHSON MISMATCH HISTORY\n")
        f.write("-" * 60 + "\n")

        f.write(f"{'Iteration':>12} {'Maximum mismatch (pu)':>25}\n")

        for i, mismatch in enumerate(get("mismatch_history")):
            f.write(f"{i:>12} {mismatch:>25.8e}\n")

        f.write("\n")
# ============================================================
# MAIN VALIDATION AND BENCHMARKING
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Numerical validation and performance benchmarking "
            "of the Newton-Raphson load-flow solver."
        )
    )

    parser.add_argument(
        "cases",
        nargs="+",
        help="MATPOWER .m case files"
    )

    parser.add_argument(
        "--reference-dir",
        default="reference_results"
    )

    parser.add_argument(
        "--tol",
        type=float,
        default=1e-8
    )

    parser.add_argument(
        "--max-iter",
        type=int,
        default=20
    )

    parser.add_argument(
        "--runs",
        type=int,
        default=5
    )

    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    if args.tol <= 0:
        parser.error("--tol must be positive")

    if args.max_iter < 1:
        parser.error("--max-iter must be at least 1")

    options = StudyOptions(
        tolerance=args.tol,
        max_iterations=args.max_iter,
        flat_start=True,
        enforce_q_limits=False,
    )

    overall_results = []

    # ========================================================
    # PRINT NRLF OUTPUTS
    # ========================================================

    nrlf_output = "nrlf_results.txt"

    with open(nrlf_output, "w", encoding="utf-8") as f:
        f.write("DETAILED NEWTON-RAPHSON LOAD FLOW RESULTS\n")

    # ========================================================
    # SIMULATION CONFIGURATION
    # ========================================================

    print_section("SIMULATION CONFIGURATION")

    config_rows = [
        ("Algorithm", "Newton-Raphson"),
        ("Coordinate system", "Polar"),
        ("Initialization", "Flat start"),
        ("Convergence tolerance", f"{args.tol:.1e} pu"),
        ("Maximum iterations", str(args.max_iter)),
        ("Q-limit enforcement", "Disabled"),
        ("Benchmark repetitions", str(args.runs)),
        ("Reference solver", "MATPOWER"),
    ]

    print_table(
        ["Parameter", "Setting"],
        config_rows
    )

    # ========================================================
    # PROCESS EACH TEST CASE
    # ========================================================

    for case_file in args.cases:

        case_path = Path(case_file)
        case_name = case_path.stem

        ref_path = (
            Path(args.reference_dir)
            / f"{case_name}.json"
        )

        print_section(f"TEST CASE: {case_name.upper()}")

        if not ref_path.exists():
            raise FileNotFoundError(
                f"Missing reference file: {ref_path}. "
                "Run generate_reference.m first."
            )

        # ----------------------------------------------------
        # LOAD NETWORK AND REFERENCE
        # ----------------------------------------------------

        network = load_case(case_path)
        reference = load_reference(ref_path)

        # ----------------------------------------------------
        # WARM-UP
        # ----------------------------------------------------

        solve_load_flow(network, options)

        # ----------------------------------------------------
        # NUMERICAL VALIDATION
        # ----------------------------------------------------

        result = solve_load_flow(network, options)
        write_nrlf_results(
            case_name,
            network,
            result,
            nrlf_output
        )

        report = compare_result(
            network,
            result,
            reference
        )

        checks = dict(report["checks"])

        # IEEE 118 iteration criterion
        if case_name.lower() in (
            "case118",
            "case_ieee118"
        ):
            checks["ieee118_iterations"] = (
                result.iterations <= IEEE118_MAX_ITER
            )

        passed = (
            result.converged
            and all(checks.values())
        )

        # ----------------------------------------------------
        # PERFORMANCE BENCHMARK
        # ----------------------------------------------------

        benchmark_results = [
            solve_load_flow(network, options)
            for _ in range(args.runs)
        ]

        timings_ms = [
            r.solve_time * 1000.0
            for r in benchmark_results
        ]

        avg_ms = statistics.mean(timings_ms)

        std_ms = (
            statistics.stdev(timings_ms)
            if args.runs > 1
            else 0.0
        )

        iterations = result.iterations

        per_iter = (
            avg_ms / iterations
            if iterations > 0
            else float("nan")
        )

        # ====================================================
        # 1. NUMERICAL VALIDATION TABLE
        # ====================================================

        print("\n[1] NUMERICAL VALIDATION")

        validation_rows = [
            (
                "Convergence",
                str(result.converged),
                "Converged",
                "PASS" if checks["convergence"] else "FAIL"
            ),
            (
                "Maximum mismatch",
                f"{report['max_mismatch_pu']:.3e} pu",
                f"<= {args.tol:.1e} pu",
                "PASS" if checks["convergence"] else "FAIL"
            ),
            (
                "Voltage magnitude error",
                f"{report['max_vm_error_pu']:.3e} pu",
                f"<= {VM_LIMIT:.1e} pu",
                "PASS" if checks["voltage_magnitude"] else "FAIL"
            ),
            (
                "Voltage angle error",
                f"{report['max_va_error_deg']:.6f} deg",
                f"<= {VA_LIMIT_DEG} deg",
                "PASS" if checks["voltage_angle"] else "FAIL"
            ),
            (
                "Maximum branch-flow error",
                f"{report['max_branch_pq_error_mw_mvar']:.6f} MW/MVAr",
                f"<= {BRANCH_LIMIT} MW/MVAr",
                "PASS" if checks["branch_flows"] else "FAIL"
            ),
            (
                "Active power loss error",
                f"{report['p_loss_error_percent']:.6f} %",
                f"<= {LOSS_LIMIT_PERCENT} %",
                "PASS" if checks["total_losses"] else "FAIL"
            ),
        ]

        if "ieee118_iterations" in checks:
            validation_rows.append(
                (
                    "IEEE 118 iteration limit",
                    f"{iterations} iterations",
                    f"<= {IEEE118_MAX_ITER} iterations",
                    "PASS" if checks["ieee118_iterations"] else "FAIL"
                )
            )

        print_table(
            [
                "Validation criterion",
                "Measured value",
                "Acceptance limit",
                "Status"
            ],
            validation_rows
        )

        # ====================================================
        # 2. MATPOWER REFERENCE COMPARISON
        # ====================================================

        print("\n[2] MATPOWER REFERENCE COMPARISON")

        reference_rows = [
            (
                "NR iterations",
                str(iterations),
                str(report["reference_iterations"])
            ),
            (
                "Active power loss",
                f"{report['our_p_loss_mw']:.6f} MW",
                f"{report['reference_p_loss_mw']:.6f} MW"
            ),
        ]

        print_table(
            [
                "Parameter",
                "Local solver",
                "MATPOWER reference"
            ],
            reference_rows
        )

        # ====================================================
        # 3. PERFORMANCE BENCHMARK
        # ====================================================

        print("\n[3] PERFORMANCE BENCHMARK")

        performance_rows = [
            (
                "Number of timed runs",
                str(args.runs)
            ),
            (
                "Average solve time",
                f"{avg_ms:.4f} ms"
            ),
            (
                "Standard deviation",
                f"{std_ms:.4f} ms"
            ),
            (
                "Average time per iteration",
                f"{per_iter:.4f} ms"
            ),
        ]

        print_table(
            [
                "Performance metric",
                "Measured value"
            ],
            performance_rows
        )

        # ====================================================
        # CASE RESULT
        # ====================================================

        print("\nCASE ACCEPTANCE STATUS:",
              "PASS" if passed else "FAIL")

        if not result.converged:
            print("Solver message:", result.message)

        overall_results.append(
            (
                case_name,
                iterations,
                avg_ms,
                passed
            )
        )

    # ========================================================
    # FINAL OVERALL SUMMARY
    # ========================================================

    print_section("OVERALL TEST SUMMARY")

    summary_rows = [
        (
            name,
            str(iterations),
            f"{avg_ms:.4f} ms",
            "PASS" if passed else "FAIL"
        )
        for name, iterations, avg_ms, passed
        in overall_results
    ]

    print_table(
        [
            "Test case",
            "NR iterations",
            "Average solve time",
            "Result"
        ],
        summary_rows
    )

    all_pass = (
        len(overall_results) > 0
        and all(item[3] for item in overall_results)
    )

    print("\n" + "=" * 100)
    print(
        "FINAL ACCEPTANCE STATUS:",
        "PASS" if all_pass else "FAIL"
    )
    print("=" * 100)

    raise SystemExit(
        0 if all_pass else 1
    )


# ============================================================
# EXECUTION AND REPORT FILE GENERATION
# ============================================================

if __name__ == "__main__":

    output_file = "validation_report.txt"

    try:
        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as f:

            with redirect_stdout(f):
                main()

    except SystemExit as exc:

        print(
            f"\nValidation report saved to: {output_file}"
        )

        # Preserve normal exit codes:
        # 0 = all tests passed
        # 1 = validation failure
        # 2 = command-line usage error
        raise

    except Exception:
        print(
            f"\nAn error occurred. Partial report saved to: "
            f"{output_file}"
        )
        raise
