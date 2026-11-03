# NR Solver – Setup and Execution

## 1. Project and environment structure

Keep the virtual environment and solver directory as siblings:

```text
PSA_project/
│
├── venv/
│
└── NR_Solver/
    ├── data/
    ├── loadflow/
    ├── reference_results/
    ├── validate.py
    ├── requirements.txt
    └── generate_reference.m
```

Open a terminal in `PSA_project`.

## 2. Set up the Python environment

Python 3.10 or later is required.

### macOS

Create and activate the environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

### Windows (PowerShell)

Create and activate the environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If script execution is blocked:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

## 3. Install dependencies

With the environment activated, navigate to the solver directory:

```bash
cd ./NR_Solver
pip install -r requirements.txt
```

## 4. Run validation and benchmarking

Execute from inside `NR_Solver`:

```bash
python validate.py data/case14.m data/case30.m data/case57.m data/case118.m --reference-dir reference_results --tol 1e-8 --max-iter 20 --runs 10
```

Arguments:

- `--reference-dir`: Directory containing MATPOWER reference results.
- `--tol`: Newton–Raphson convergence tolerance.
- `--max-iter`: Maximum number of iterations.
- `--runs`: Number of benchmark repetitions per test case.

Generate the MATPOWER reference JSON files using `generate_reference.m` before running validation.

## 5. Output files

Both files are generated inside `NR_Solver/`.

| File | Contents |
|---|---|
| `validation_report.txt` | Numerical validation, MATPOWER comparison, performance benchmarks and acceptance status. |
| `nrlf_results.txt` | Bus voltages, angles, power injections, generator input data, branch power flows, system losses and mismatch history. |

The validation summary is also displayed in the terminal.