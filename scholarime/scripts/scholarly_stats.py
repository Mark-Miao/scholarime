#!/usr/bin/env python3
"""Reproducible descriptive and inferential statistics for Scholarime data."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from scholarly_data import DataError, InputOptions, _number, read_table


VERSION = "1.0.0"
SUPPORTED = {
    "describe",
    "frequency",
    "correlation",
    "t_test",
    "anova",
    "chi_square",
    "regression",
    "diagnostics",
    "multiple_comparison",
    "time_series",
    "power",
}


class StatsError(ValueError):
    """A user-correctable statistics error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _columns(headers: Sequence[str], value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StatsError("columns must be a string or list of column names.")
    missing = [column for column in value if column not in headers]
    if missing:
        raise StatsError(f"Unknown column(s): {', '.join(missing)}")
    return value


def _numeric(values: Sequence[Any]) -> list[float]:
    result = []
    for value in values:
        number = _number(value)
        if number is not None and math.isfinite(number):
            result.append(number)
    return result


def _descriptive(values: Sequence[Any]) -> dict[str, Any]:
    numbers = _numeric(values)
    missing = len(values) - len(numbers)
    if not numbers:
        return {"n": 0, "missing": missing}
    ordered = sorted(numbers)

    def quantile(proportion: float) -> float:
        position = (len(ordered) - 1) * proportion
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        fraction = position - lower
        return ordered[lower] * (1 - fraction) + ordered[upper] * fraction

    return {
        "n": len(numbers),
        "missing": missing,
        "mean": statistics.fmean(numbers),
        "median": statistics.median(numbers),
        "std_dev": statistics.stdev(numbers) if len(numbers) > 1 else 0.0,
        "min": min(numbers),
        "max": max(numbers),
        "q1": quantile(0.25),
        "q3": quantile(0.75),
    }


def _json_number(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _normality(values: Sequence[float], stats: Any) -> dict[str, Any]:
    if len(values) < 3 or len(set(values)) < 2:
        return {"available": False, "reason": "too few values or constant data"}
    statistic, p_value = stats.shapiro(values)
    return {
        "test": "Shapiro-Wilk",
        "statistic": _json_number(statistic),
        "p_value": _json_number(p_value),
    }


def _variance_test(groups: Sequence[Sequence[float]], stats: Any) -> dict[str, Any]:
    if min(map(len, groups)) < 3:
        return {"available": False, "reason": "Levene requires at least three values per group"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = stats.levene(*groups)
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "test": "Levene",
        "statistic": _json_number(result.statistic),
        "p_value": _json_number(result.pvalue),
    }


def _require_scipy():
    try:
        from scipy import stats
    except ImportError as exc:
        raise StatsError(
            "Inferential statistics require SciPy. Install the stats dependencies."
        ) from exc
    return stats


def _require_statsmodels():
    try:
        import statsmodels.api as sm
        import statsmodels.formula.api as smf
    except ImportError as exc:
        raise StatsError(
            "Regression requires Statsmodels. Install the stats dependencies."
        ) from exc
    return sm, smf


def _formula_columns(table: Any, formula: str) -> list[str]:
    if not formula or "~" not in formula:
        raise StatsError("A model formula such as y ~ x1 + x2 is required.")
    required = [
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula)
        if token not in {"C", "Q", "np", "log", "exp", "I"}
    ]
    missing = [column for column in required if column not in table.headers]
    if missing:
        raise StatsError(f"Unknown model column(s): {', '.join(missing)}")
    return required


def _model_data(table: Any) -> Any:
    try:
        import pandas as pd
    except ImportError:
        return table.rows
    data = pd.DataFrame(table.rows)
    for column in table.headers:
        converted = pd.to_numeric(data[column], errors="coerce")
        nonempty = data[column].notna() & data[column].astype(str).str.strip().ne("")
        if nonempty.any() and converted[nonempty].notna().all():
            data[column] = converted
    return data


def _field_value(container: Any, name: str, index: int) -> Any:
    try:
        if hasattr(container, "loc"):
            return container.loc[name]
        return container[index]
    except (KeyError, IndexError, TypeError):
        return None


def _confidence_interval(container: Any, name: str, index: int) -> tuple[Any, Any]:
    try:
        if hasattr(container, "loc"):
            row = container.loc[name]
            return row.iloc[0], row.iloc[1]
        row = container[index]
        return row[0], row[1]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, None


def _model_metrics(model: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    aliases = {
        "rsquared": "r_squared",
        "rsquared_adj": "adjusted_r_squared",
        "prsquared": "pseudo_rsquared",
        "pseudo_rsquared": "pseudo_rsquared",
        "fvalue": "f_statistic",
        "f_pvalue": "f_p_value",
        "llf": "log_likelihood",
        "aic": "aic",
        "bic": "bic",
    }
    for attribute, key in aliases.items():
        value = getattr(model, attribute, None)
        if value is not None:
            metrics[key] = _json_number(value)
    return metrics


def _model_coefficients(model: Any) -> list[dict[str, Any]]:
    names = list(getattr(getattr(model, "model", None), "exog_names", []) or [])
    coefficients = []
    for index, name in enumerate(names):
        estimate = _field_value(model.params, name, index)
        if estimate is None:
            continue
        low, high = _confidence_interval(model.conf_int(), name, index)
        coefficients.append(
            {
                "term": name,
                "estimate": _json_number(estimate),
                "std_error": _json_number(_field_value(model.bse, name, index)),
                "statistic": _json_number(_field_value(model.tvalues, name, index)),
                "p_value": _json_number(_field_value(model.pvalues, name, index)),
                "ci_low": _json_number(low),
                "ci_high": _json_number(high),
            }
        )
    return coefficients


def _ols_diagnostics(model: Any, sm: Any) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    try:
        from statsmodels.stats.diagnostic import het_breuschpagan

        statistic, p_value, f_statistic, f_p_value = het_breuschpagan(
            model.resid,
            model.model.exog,
        )
        diagnostics["breusch_pagan"] = {
            "available": True,
            "statistic": _json_number(statistic),
            "p_value": _json_number(p_value),
            "f_statistic": _json_number(f_statistic),
            "f_p_value": _json_number(f_p_value),
        }
    except Exception as exc:
        diagnostics["breusch_pagan"] = {"available": False, "reason": str(exc)}
    try:
        from statsmodels.stats.stattools import durbin_watson, jarque_bera

        diagnostics["durbin_watson"] = _json_number(durbin_watson(model.resid))
        jb_statistic, jb_p_value, skew, kurtosis = jarque_bera(model.resid)
        diagnostics["jarque_bera"] = {
            "statistic": _json_number(jb_statistic),
            "p_value": _json_number(jb_p_value),
            "skew": _json_number(skew),
            "kurtosis": _json_number(kurtosis),
        }
    except Exception as exc:
        diagnostics["residual_tests"] = {"available": False, "reason": str(exc)}
    try:
        from statsmodels.stats.outliers_influence import variance_inflation_factor

        values = {}
        for index, name in enumerate(model.model.exog_names):
            if name == "Intercept":
                continue
            values[name] = _json_number(
                variance_inflation_factor(model.model.exog, index)
            )
        diagnostics["vif"] = values
    except Exception as exc:
        diagnostics["vif"] = {"available": False, "reason": str(exc)}
    try:
        diagnostics["condition_number"] = _json_number(
            sm.tools.tools.condition_number(model.model.exog)
        )
    except Exception as exc:
        diagnostics["condition_number"] = {"available": False, "reason": str(exc)}
    return diagnostics


def _fit_regression(table: Any, analysis: Mapping[str, Any]) -> dict[str, Any]:
    sm, smf = _require_statsmodels()
    formula = str(analysis.get("formula", "")).strip()
    _formula_columns(table, formula)
    model_type = str(analysis.get("model", "ols")).casefold()
    data = _model_data(table)
    fit_kwargs: dict[str, Any] = {}

    if model_type in {"ols", "wls"}:
        weights = None
        if model_type == "wls":
            weight_column = _columns(table.headers, analysis.get("weights"))[0]
            weights = data[weight_column] if hasattr(data, "columns") else [
                row.get(weight_column) for row in data
            ]
        model = smf.wls(formula, data=data, weights=weights) if model_type == "wls" else smf.ols(
            formula,
            data=data,
        )
        cov_type = str(analysis.get("cov_type", "nonrobust")).upper()
        if cov_type != "NONROBUST":
            if cov_type == "CLUSTER":
                cluster_column = _columns(table.headers, analysis.get("cluster"))[0]
                fitted = model.fit()
                fitted = fitted.get_robustcov_results(
                    cov_type="cluster",
                    groups=(
                        data[cluster_column]
                        if hasattr(data, "columns")
                        else [row.get(cluster_column) for row in data]
                    ),
                )
            else:
                if cov_type == "HAC":
                    fit_kwargs["cov_kwds"] = {
                        "maxlags": int(analysis.get("maxlags", 1)),
                    }
                fitted = model.fit(cov_type=cov_type, **fit_kwargs)
        else:
            fitted = model.fit()
        extra = {"diagnostics": _ols_diagnostics(fitted, sm)}
    elif model_type in {"logit", "probit"}:
        model = getattr(smf, model_type)(formula, data=data)
        fitted = model.fit(disp=False)
        extra = {}
    elif model_type == "glm":
        family_name = str(analysis.get("family", "gaussian")).casefold()
        family_class = {
            "gaussian": sm.families.Gaussian,
            "binomial": sm.families.Binomial,
            "poisson": sm.families.Poisson,
            "gamma": sm.families.Gamma,
            "inverse_gaussian": sm.families.InverseGaussian,
        }.get(family_name)
        if family_class is None:
            raise StatsError(
                "GLM family must be gaussian, binomial, poisson, gamma, or inverse_gaussian."
            )
        link_name = str(analysis.get("link", "")).strip().casefold()
        links = sm.families.links
        link_classes = {
            name: candidate
            for name, candidate in {
                "identity": getattr(links, "Identity", None),
                "log": getattr(links, "Log", None),
                "logit": getattr(links, "Logit", None),
                "probit": getattr(links, "Probit", None),
                "inverse": getattr(links, "Inverse", None)
                or getattr(links, "InversePower", None),
                "inverse_power": getattr(links, "InversePower", None),
                "sqrt": getattr(links, "Sqrt", None),
                "cloglog": getattr(links, "CLogLog", None),
            }.items()
            if candidate is not None
        }
        link_class = link_classes.get(link_name)
        if link_name and link_class is None:
            raise StatsError(
                "GLM link must be identity, log, logit, probit, inverse, sqrt, or cloglog."
            )
        family = family_class(link=link_class()) if link_class else family_class()
        fitted = smf.glm(formula, data=data, family=family).fit()
        extra = {"family": family_name, "link": link_name or "default"}
    elif model_type == "mixedlm":
        group_column = _columns(table.headers, analysis.get("groups"))[0]
        groups = data[group_column] if hasattr(data, "columns") else [
            row.get(group_column) for row in data
        ]
        model = smf.mixedlm(
            formula,
            data=data,
            groups=groups,
            re_formula=analysis.get("re_formula"),
        )
        fitted = model.fit(reml=bool(analysis.get("reml", True)))
        extra = {"groups": group_column, "re_formula": analysis.get("re_formula") or "1"}
    else:
        raise StatsError(
            "Regression model must be ols, wls, logit, probit, glm, or mixedlm."
        )

    result = {
        "formula": formula,
        "model_type": model_type,
        "n": int(getattr(fitted, "nobs", len(data))),
        "coefficients": _model_coefficients(fitted),
    }
    result.update(_model_metrics(fitted))
    result.update(extra)
    if model_type in {"ols", "wls"}:
        result["cov_type"] = str(analysis.get("cov_type", "nonrobust")).upper()
    return result


def _reporting_audit(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    for index, result in enumerate(results, 1):
        op = result.get("op")
        if op in {"regression", "diagnostics", "time_series"} and not result.get("n"):
            checks.append(
                {
                    "analysis": str(index),
                    "level": "FAIL",
                    "message": "The analysed sample size could not be extracted.",
                }
            )
        if op in {"anova", "t_test", "regression"} and not any(
            key in result
            for key in (
                "cohens_d",
                "eta_squared",
                "r_squared",
                "pseudo_rsquared",
            )
        ):
            checks.append(
                {
                    "analysis": str(index),
                    "level": "WARN",
                    "message": "No effect-size or explained-variation measure was reported.",
                }
            )
        if op in {"correlation", "t_test", "anova", "chi_square", "regression"}:
            checks.append(
                {
                    "analysis": str(index),
                    "level": "AUTHOR_INPUT_NEEDED",
                    "message": "State the experimental unit, design, missing-data handling, and whether assumptions were checked.",
                }
            )
    return {
        "checks": checks,
        "required_author_inputs": [
            "experimental or observational unit used as n",
            "replication and independence assumptions",
            "missing-data handling and excluded observations",
            "effect size, uncertainty interval, and exact test/model definition",
            "multiplicity policy for multiple comparisons",
            "software and model specification sufficient for reproduction",
        ],
    }


def _pairwise_complete(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> tuple[list[Mapping[str, Any]], int]:
    kept = [
        row
        for row in rows
        if all(row.get(column) is not None and str(row.get(column)).strip() != "" for column in columns)
    ]
    return kept, len(rows) - len(kept)


def run_analysis(
    table: Any,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    op = str(analysis.get("op", "")).casefold()
    if op not in SUPPORTED:
        raise StatsError(f"Unsupported analysis: {op or '(missing)'}")
    result: dict[str, Any] = {"op": op}
    rows = table.rows
    if op == "describe":
        columns = (
            _columns(table.headers, analysis["columns"])
            if analysis.get("columns")
            else list(table.headers)
        )
        result["columns"] = {
            column: _descriptive([row.get(column) for row in rows])
            for column in columns
        }
        return result
    if op == "frequency":
        columns = _columns(table.headers, analysis.get("columns") or [])
        if not columns:
            raise StatsError("frequency requires columns.")
        result["columns"] = {}
        for column in columns:
            counts = {}
            for row in rows:
                value = row.get(column)
                key = "<missing>" if value is None or str(value).strip() == "" else str(value)
                counts[key] = counts.get(key, 0) + 1
            result["columns"][column] = counts
        return result

    if op == "correlation":
        stats = _require_scipy()
        columns = _columns(table.headers, analysis.get("columns"))
        if len(columns) != 2:
            raise StatsError("correlation requires exactly two columns.")
        kept, dropped = _pairwise_complete(rows, columns)
        left = _numeric([row.get(columns[0]) for row in kept])
        right = _numeric([row.get(columns[1]) for row in kept])
        if len(left) != len(kept) or len(right) != len(kept) or len(left) < 3:
            raise StatsError("correlation requires at least three complete numeric pairs.")
        method = str(analysis.get("method", "pearson")).casefold()
        if method == "pearson":
            value, p_value = stats.pearsonr(left, right)
        elif method == "spearman":
            value, p_value = stats.spearmanr(left, right)
        else:
            raise StatsError("correlation method must be pearson or spearman.")
        result.update(
            {
                "columns": columns,
                "method": method,
                "n": len(left),
                "dropped_rows": dropped,
                "statistic": _json_number(value),
                "p_value": _json_number(p_value),
            }
        )
        return result

    if op == "t_test":
        stats = _require_scipy()
        if analysis.get("columns"):
            columns = _columns(table.headers, analysis["columns"])
            if len(columns) != 2:
                raise StatsError("paired t-test requires two columns.")
            kept, dropped = _pairwise_complete(rows, columns)
            left = _numeric([row.get(columns[0]) for row in kept])
            right = _numeric([row.get(columns[1]) for row in kept])
            if len(left) < 2 or len(left) != len(kept) or len(right) != len(kept):
                raise StatsError("paired t-test requires complete numeric pairs.")
            statistic, p_value = stats.ttest_rel(left, right)
            differences = [a - b for a, b in zip(left, right, strict=True)]
            result.update(
                {
                    "paired": True,
                    "columns": columns,
                    "n_pairs": len(left),
                    "dropped_rows": dropped,
                    "diagnostics": {
                        "difference_normality": _normality(differences, stats)
                    },
                    "mean_difference": statistics.fmean(differences),
                }
            )
        else:
            value_column = _columns(table.headers, analysis.get("value"))[0]
            group_column = _columns(table.headers, analysis.get("group"))[0]
            groups = analysis.get("groups")
            if groups is None:
                groups = list(
                    dict.fromkeys(
                        str(row.get(group_column))
                        for row in rows
                        if row.get(group_column) is not None
                    )
                )[:2]
            if not isinstance(groups, list) or len(groups) != 2:
                raise StatsError("independent t-test requires exactly two groups.")
            values = []
            for group in groups:
                values.append(
                    _numeric(
                        [
                            row.get(value_column)
                            for row in rows
                            if str(row.get(group_column)) == str(group)
                        ]
                    )
                )
            if min(map(len, values)) < 2:
                raise StatsError("Each group requires at least two numeric values.")
            statistic, p_value = stats.ttest_ind(values[0], values[1], equal_var=False)
            pooled_variance = (
                (len(values[0]) - 1) * statistics.variance(values[0])
                + (len(values[1]) - 1) * statistics.variance(values[1])
            ) / max(1, len(values[0]) + len(values[1]) - 2)
            effect_size = (
                (statistics.fmean(values[0]) - statistics.fmean(values[1]))
                / math.sqrt(pooled_variance)
                if pooled_variance > 0
                else 0.0
            )
            variance_diagnostic = _variance_test(values, stats)
            result.update(
                {
                    "paired": False,
                    "value": value_column,
                    "group": group_column,
                    "groups": groups,
                    "n": [len(values[0]), len(values[1])],
                    "cohens_d": _json_number(effect_size),
                    "diagnostics": {
                        "group_normality": [
                            _normality(values[0], stats),
                            _normality(values[1], stats),
                        ],
                        "equal_variance": variance_diagnostic,
                    },
                }
            )
        result.update({"statistic": _json_number(statistic), "p_value": _json_number(p_value)})
        return result

    if op == "anova":
        stats = _require_scipy()
        value_column = _columns(table.headers, analysis.get("value"))[0]
        group_column = _columns(table.headers, analysis.get("group"))[0]
        grouped: dict[str, list[float]] = {}
        for row in rows:
            group = row.get(group_column)
            number = _number(row.get(value_column))
            if group is None or number is None:
                continue
            grouped.setdefault(str(group), []).append(number)
        if len(grouped) < 2 or any(len(values) < 2 for values in grouped.values()):
            raise StatsError("ANOVA requires at least two groups with two values each.")
        statistic, p_value = stats.f_oneway(*grouped.values())
        all_values = [value for values in grouped.values() for value in values]
        grand_mean = statistics.fmean(all_values)
        ss_between = sum(
            len(values) * (statistics.fmean(values) - grand_mean) ** 2
            for values in grouped.values()
        )
        ss_total = sum((value - grand_mean) ** 2 for value in all_values)
        variance_diagnostic = _variance_test(list(grouped.values()), stats)
        result.update(
            {
                "value": value_column,
                "group": group_column,
                "groups": {key: len(value) for key, value in grouped.items()},
                "statistic": _json_number(statistic),
                "p_value": _json_number(p_value),
                "eta_squared": _json_number(ss_between / ss_total) if ss_total else 0.0,
                "diagnostics": {
                    "group_normality": {
                        key: _normality(values, stats) for key, values in grouped.items()
                    },
                    "equal_variance": variance_diagnostic,
                },
            }
        )
        return result

    if op == "chi_square":
        stats = _require_scipy()
        columns = _columns(table.headers, analysis.get("columns"))
        if len(columns) != 2:
            raise StatsError("chi_square requires exactly two columns.")
        kept, dropped = _pairwise_complete(rows, columns)
        labels_left = list(dict.fromkeys(str(row.get(columns[0])) for row in kept))
        labels_right = list(dict.fromkeys(str(row.get(columns[1])) for row in kept))
        table_values = [[0 for _ in labels_right] for _ in labels_left]
        for row in kept:
            table_values[labels_left.index(str(row.get(columns[0])))][
                labels_right.index(str(row.get(columns[1])))
            ] += 1
        statistic, p_value, dof, expected = stats.chi2_contingency(table_values)
        result.update(
            {
                "columns": columns,
                "rows": labels_left,
                "columns_values": labels_right,
                "table": table_values,
                "dropped_rows": dropped,
                "statistic": _json_number(statistic),
                "p_value": _json_number(p_value),
                "degrees_of_freedom": int(dof),
                "expected": [[_json_number(value) for value in row] for row in expected],
            }
        )
        return result

    if op == "regression":
        result.update(_fit_regression(table, analysis))
        return result

    if op == "diagnostics":
        sm, smf = _require_statsmodels()
        formula = str(analysis.get("formula", "")).strip()
        _formula_columns(table, formula)
        fitted = smf.ols(formula, data=_model_data(table)).fit()
        result.update(
            {
                "formula": formula,
                "n": int(fitted.nobs),
                "diagnostics": _ols_diagnostics(fitted, sm),
            }
        )
        return result

    if op == "multiple_comparison":
        try:
            from statsmodels.stats.multitest import multipletests
        except ImportError as exc:
            raise StatsError("Multiple-comparison correction requires Statsmodels.") from exc
        values = analysis.get("p_values")
        if not isinstance(values, list) or not values:
            raise StatsError("multiple_comparison requires a non-empty p_values list.")
        try:
            p_values = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise StatsError("All p_values must be numeric.") from exc
        if any(value < 0 or value > 1 for value in p_values):
            raise StatsError("p_values must be between 0 and 1.")
        method = str(analysis.get("method", "holm")).casefold()
        alpha = float(analysis.get("alpha", 0.05))
        try:
            rejected, adjusted, _, _ = multipletests(
                p_values,
                alpha=alpha,
                method=method,
            )
        except (ValueError, TypeError) as exc:
            raise StatsError(f"Unsupported or invalid correction method: {method}") from exc
        result.update(
            {
                "method": method,
                "alpha": alpha,
                "p_values": p_values,
                "adjusted_p_values": [_json_number(value) for value in adjusted],
                "rejected": [bool(value) for value in rejected],
            }
        )
        return result

    if op == "time_series":
        try:
            from statsmodels.tsa.arima.model import ARIMA
            from statsmodels.tsa.stattools import adfuller
        except ImportError as exc:
            raise StatsError("Time-series analysis requires Statsmodels.") from exc
        column = _columns(table.headers, analysis.get("column"))[0]
        values = _numeric([row.get(column) for row in rows])
        if len(values) < 3:
            raise StatsError("time_series requires at least three numeric observations.")
        test = str(analysis.get("test", "adf")).casefold()
        if test == "adf":
            adf_options = {
                "maxlag": analysis.get("maxlag"),
                "regression": str(analysis.get("regression", "c")),
                "autolag": analysis.get("autolag", "AIC"),
            }
            try:
                adf_result = adfuller(values, result_object=False, **adf_options)
            except TypeError:
                adf_result = adfuller(values, **adf_options)
            statistic, p_value, used_lag, nobs, critical = adf_result[:5]
            result.update(
                {
                    "test": "adf",
                    "column": column,
                    "n": len(values),
                    "statistic": _json_number(statistic),
                    "p_value": _json_number(p_value),
                    "used_lag": int(used_lag),
                    "regression": str(analysis.get("regression", "c")),
                    "critical_values": {
                        key: _json_number(value) for key, value in critical.items()
                    },
                    "nobs": int(nobs),
                }
            )
            return result
        if test == "arima":
            order = analysis.get("order", [1, 1, 0])
            if (
                not isinstance(order, list)
                or len(order) != 3
                or not all(isinstance(value, int) and value >= 0 for value in order)
            ):
                raise StatsError("ARIMA order must be a list of three non-negative integers.")
            fitted = ARIMA(values, order=tuple(order), trend=analysis.get("trend", "c")).fit()
            steps = int(analysis.get("forecast_steps", 0))
            forecast = fitted.forecast(steps=steps) if steps > 0 else []
            result.update(
                {
                    "test": "arima",
                    "column": column,
                    "n": len(values),
                    "order": order,
                    "aic": _json_number(fitted.aic),
                    "bic": _json_number(fitted.bic),
                    "parameters": {
                        str(name): _json_number(value)
                        for name, value in (
                            fitted.params.items()
                            if hasattr(fitted.params, "items")
                            else zip(fitted.param_names, fitted.params, strict=True)
                        )
                    },
                    "forecast": [_json_number(value) for value in forecast],
                }
            )
            return result
        raise StatsError("time_series test must be adf or arima.")

    try:
        from statsmodels.stats.power import TTestIndPower
    except ImportError as exc:
        raise StatsError("power analysis requires Statsmodels.") from exc
    effect_size = float(analysis.get("effect_size"))
    alpha = float(analysis.get("alpha", 0.05))
    power = analysis.get("power")
    nobs = analysis.get("nobs")
    analysis_obj = TTestIndPower()
    if power is None and nobs is not None:
        value = analysis_obj.power(
            effect_size=effect_size,
            nobs1=int(nobs),
            alpha=alpha,
            ratio=float(analysis.get("ratio", 1.0)),
        )
        result.update({"requested": "power", "power": _json_number(value)})
    elif nobs is None and power is not None:
        value = analysis_obj.solve_power(
            effect_size=effect_size,
            power=float(power),
            alpha=alpha,
            ratio=float(analysis.get("ratio", 1.0)),
        )
        result.update({"requested": "nobs_per_group", "nobs_per_group": _json_number(value)})
    else:
        raise StatsError("power requires exactly one of power or nobs.")
    result.update(
        {
            "test": "two_sample_independent_t",
            "effect_size": effect_size,
            "alpha": alpha,
        }
    )
    return result


def validate_recipe(recipe: Mapping[str, Any]) -> None:
    if recipe.get("version") != 1:
        raise StatsError("Statistics recipe version must be 1.")
    analyses = recipe.get("analyses")
    if not isinstance(analyses, list) or not analyses:
        raise StatsError("analyses must be a non-empty list.")
    for index, analysis in enumerate(analyses):
        if not isinstance(analysis, dict):
            raise StatsError(f"Analysis {index} must be an object.")
        if str(analysis.get("op", "")).casefold() not in SUPPORTED:
            raise StatsError(f"Unsupported analysis at index {index}.")


def report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Statistical Analysis",
        "",
        f"- Source: `{report['source']}`",
        f"- Rows: {report['row_count']}",
        f"- Generated: {report['generated_at']}",
        "",
        "| # | Analysis | Result |",
        "|---:|---|---|",
    ]
    for index, item in enumerate(report["results"], 1):
        summary = {
            key: value
            for key, value in item.items()
            if key
            in {
                "statistic",
                "p_value",
                "r_squared",
                "pseudo_rsquared",
                "aic",
                "power",
                "nobs_per_group",
                "n",
            }
        }
        lines.append(
            f"| {index} | {item['op']} | {json.dumps(summary, ensure_ascii=False)} |"
        )
    lines.extend(
        [
            "",
            "## Statistical Reporting Audit",
            "",
        ]
    )
    audit = report.get("reporting_audit") or {}
    checks = audit.get("checks") or []
    if checks:
        for check in checks:
            lines.append(
                f"- Analysis {check['analysis']} [{check['level']}]: {check['message']}"
            )
    else:
        lines.append("- No automatic reporting warnings were generated.")
    lines.extend(
        [
            "",
            "The author must still provide the experimental or observational unit used as n,",
            "replication, missing-data handling, uncertainty, exact model/test definitions,",
            "and the multiplicity policy. Automatic checks cannot infer study design.",
            "",
            "Inferential results require checking assumptions, study design, and effect sizes;",
            "p-values alone do not establish practical or causal significance.",
            "",
        ]
    )
    return "\n".join(lines)


def _add_input_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--delimiter", default="auto")
    parser.add_argument("--header-row", type=int, default=1)
    parser.add_argument("--no-header", action="store_const", const=0, dest="header_row")
    parser.add_argument("--sheet")
    parser.add_argument("--null-token", action="append", default=[])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scholarly_stats.py",
        description="Run reproducible descriptive and inferential statistical analyses.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="Validate a statistics recipe.")
    validate_parser.add_argument("recipe", type=Path)
    run_parser = subparsers.add_parser("run", help="Run a statistics recipe.")
    run_parser.add_argument("input", type=Path)
    run_parser.add_argument("recipe", type=Path)
    _add_input_options(run_parser)
    run_parser.add_argument("--output", type=Path)
    run_parser.add_argument("--report", type=Path)
    run_parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        recipe = json.loads(args.recipe.read_text(encoding="utf-8"))
        if not isinstance(recipe, dict):
            raise StatsError("Statistics recipe must be a JSON object.")
        validate_recipe(recipe)
        if args.command == "validate":
            print("Statistics recipe structure is valid.")
            return 0
        null_tokens = tuple(args.null_token) if args.null_token else ("", "na", "n/a", "nan", "null", "none", "missing", "未填", "缺失")
        table = read_table(
            args.input,
            InputOptions(
                encoding=args.encoding,
                delimiter=args.delimiter,
                header_row=args.header_row,
                sheet=args.sheet,
                null_tokens=null_tokens,
            ),
        )
        results = [run_analysis(table, analysis) for analysis in recipe["analyses"]]
        report = {
            "version": VERSION,
            "generated_at": utc_now(),
            "source": str(table.source),
            "source_sha256": table.metadata.get("source_sha256"),
            "row_count": len(table.rows),
            "results": results,
            "reporting_audit": _reporting_audit(results),
        }
        output = (
            args.output.expanduser().resolve()
            if args.output
            else args.input.with_name(args.input.stem + ".stats.json")
        )
        markdown = (
            args.report.expanduser().resolve()
            if args.report
            else output.with_suffix(".md")
        )
        for path in (output, markdown):
            if path.exists() and not args.overwrite:
                raise StatsError(f"Output already exists: {path}. Pass --overwrite.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown.write_text(report_markdown(report), encoding="utf-8")
        print(f"Statistics JSON: {output}")
        print(f"Statistics report: {markdown}")
        return 0
    except (StatsError, DataError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
