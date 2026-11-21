# Symbolic Derivation and Formal Verification

`scripts/symbolic_derive.py` separates machine computation from formal proof:

- SymPy computes candidate expressions and transformation traces.
- Lean 4 or Coq compiles formal proof sources.
- A CAS result is not a formal proof.

## Compute Recipes

Compute recipes use Python/SymPy syntax, not LaTeX. Every variable should be declared with the
assumptions needed for correct simplification and integration.

```json
{
  "version": 1,
  "variables": {
    "x": {"real": true, "positive": true}
  },
  "expression": "x**3 + sin(x)",
  "operations": [
    {"op": "differentiate", "variable": "x"},
    {"op": "simplify"}
  ]
}
```

An equation may replace `expression`:

```json
{
  "version": 1,
  "variables": {"x": {"real": true}},
  "equation": {"lhs": "x**2", "rhs": "4"},
  "operations": [
    {"op": "solve", "variable": "x"}
  ]
}
```

Supported operations are `simplify`, `expand`, `factor`, `cancel`, `apart`, `together`,
`trigsimp`, `substitute`, `differentiate`, `integrate`, `limit`, `series`, `solve`, `evaluate`,
and `check_equals`. Supported assumptions are `real`, `complex`, `integer`, `rational`,
`irrational`, `positive`, `negative`, `nonzero`, `nonnegative`, `nonpositive`, `even`, `odd`, and
`prime`.

Run:

```bash
python scholarime/scripts/symbolic_derive.py compute derivation.json
```

The output contains the final expression, LaTeX, per-step expressions, equivalence checks, and
warnings. Integration constants, branch behavior, singularities, and failed equivalence checks
must be reported. Computation has a timeout and expression/complexity limits.

## Formal Verification

Formal sources must contain a theorem or lemma. The verifier scans source code before compilation
and rejects:

- Lean `sorry`, `admit`, undeclared axioms, unsafe/partial definitions, custom macros or
  elaborators, external execution, and unrecognized import roots.
- Coq `admit`, `Admitted`, axioms, parameters, conjectures, aborted proofs, ML module declarations,
  and disabled guard/positivity/universe checks.

The verifier checks declarations in the submitted source. It does not audit every imported library
for foundational axioms.

Lean:

```bash
python scholarime/scripts/symbolic_derive.py verify proof.lean \
  --project path/to/lake-project
```

Coq:

```bash
python scholarime/scripts/symbolic_derive.py verify proof.v --prover coq
```

A source is marked `verified` only when the compiler exits successfully after passing the safety
scan. Compiler errors, timeouts, unavailable executables, and rejected patterns are separate
states. The Agent should present the formal theorem statement to the user before attempting a
proof and should not retry an automatically repaired proof indefinitely.

Use `doctor` to inspect installed engines:

```bash
python scholarime/scripts/symbolic_derive.py doctor
```
