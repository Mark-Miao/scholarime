# Statistical Analysis and Reporting

Use `scholarime stats` for reproducible computation and this checklist for the reporting boundary.
The tool can describe data, compare groups, fit OLS/WLS, logit, probit, GLM, and mixed models,
compute robust inference, run model diagnostics, correct multiple comparisons, and perform basic
time-series tests.

## Recipe Examples

OLS with heteroskedasticity-robust inference:

```json
{"op": "regression", "formula": "score ~ age + C(group)", "cov_type": "HC3"}
```

Logistic GLM and model diagnostics:

```json
{"op": "regression", "model": "glm", "family": "binomial", "formula": "event ~ treatment + age"}
{"op": "diagnostics", "formula": "score ~ age + C(group)"}
```

Mixed model, multiplicity correction, and stationarity test:

```json
{"op": "regression", "model": "mixedlm", "formula": "score ~ time", "groups": "site"}
{"op": "multiple_comparison", "p_values": [0.01, 0.04, 0.20], "method": "fdr_bh"}
{"op": "time_series", "test": "adf", "column": "score"}
```

## Reporting Gate

Every analysis report includes a reporting audit. Treat its `AUTHOR_INPUT_NEEDED` entries as work
the software cannot infer:

- Identify the experimental or observational unit used as `n`. Do not silently treat cells,
  technical replicates, repeated readings, or model runs as independent experimental samples.
- Report replication, missing-data handling, exclusions, and the analysis population.
- Report effect sizes and uncertainty intervals. Exact p-values may supplement them but cannot
  replace them.
- Name the test or model, its assumptions, covariance estimator, link/family, and model formula.
- State the multiplicity policy when several comparisons support one claim.
- Record software versions and enough model detail for reproduction.

A diagnostic warning is not proof that a model is invalid. It is a prompt to inspect the design,
the data, and the consequences for the claim. Conversely, a non-significant diagnostic test does
not establish that assumptions hold.

Use `AUTHOR_INPUT_NEEDED` when design information is absent. Do not invent sample sizes,
randomization, blinding, exclusion rules, or causal identification. A statistically significant
association is not automatically a causal effect.
