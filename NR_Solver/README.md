# E4234 AC Load-Flow Solver (initial numerical engine)

## Scope
- Reads standard numeric MATPOWER `.m` case files (`mpc.baseMVA`, `mpc.bus`,
  `mpc.gen`, `mpc.branch`).
- Builds sparse Ybus using the MATPOWER complex-tap branch model.
- Runs polar Newton-Raphson with an analytical Jacobian and SciPy sparse LU.
- Q-limit enforcement is deliberately not implemented in this first version;
  `StudyOptions.enforce_q_limits` is reserved and defaults to `False`.
- Calculates bus injections, branch end flows and total branch losses.

## Install
```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
python validate.py data/case14.m data/case_ieee30.m data/case57.m data/case118.m
```

Copy the four MATPOWER case files into `data/` first. Obtain them from the official
MATPOWER repository. The parser supports numeric matrix assignments, not arbitrary
MATLAB expressions or scripts.

## Important validation notes
1. Compare against MATPOWER with the same case, flat start, tolerance, and Q-limit
   enforcement disabled.
2. The timer in this version measures compilation + Ybus + NR + result post-processing.
3. MATPOWER case bus types are respected. Exactly one REF bus is required.
4. Bus shunts use MATPOWER's Gs/Bs convention in MW/MVAr at 1 pu.
5. Transformer tap zero is interpreted as unity; phase shift is in degrees.
6. ZIP loads, Q-limit PV/PQ switching, multiple reference islands, and extended
   MATPOWER syntax are not included yet.
7. This is an initial implementation: run the provided reference comparisons and
   tests before relying on its numerical results.
