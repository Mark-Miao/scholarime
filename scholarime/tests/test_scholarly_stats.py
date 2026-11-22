from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import scholarly_stats


class ScholarlyStatsTests(unittest.TestCase):
    def _data(self, root: Path) -> Path:
        path = root / "data.csv"
        path.write_text(
            "\n".join(
                [
                    "group,score,age",
                    "A,10,20",
                    "A,12,21",
                    "B,15,22",
                    "B,18,23",
                    "B,,24",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def test_descriptive_and_frequency_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = self._data(root)
            recipe = root / "stats.json"
            recipe.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "analyses": [
                            {"op": "describe", "columns": ["score", "age"]},
                            {"op": "frequency", "columns": ["group"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "result.json"
            report = root / "result.md"
            result = scholarly_stats.main(
                [
                    "run",
                    str(data),
                    str(recipe),
                    "--output",
                    str(output),
                    "--report",
                    str(report),
                ]
            )
            self.assertEqual(result, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["results"][0]["columns"]["score"]["n"], 4)
            self.assertEqual(payload["results"][0]["columns"]["score"]["missing"], 1)
            self.assertEqual(payload["results"][1]["columns"]["group"]["A"], 2)
            self.assertIn("# Statistical Analysis", report.read_text(encoding="utf-8"))

    @unittest.skipUnless(importlib.util.find_spec("scipy"), "SciPy is not installed")
    def test_inferential_analysis_when_scipy_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = self._data(root)
            recipe = root / "stats.json"
            recipe.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "analyses": [
                            {
                                "op": "t_test",
                                "value": "score",
                                "group": "group",
                                "groups": ["A", "B"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "result.json"
            self.assertEqual(
                scholarly_stats.main(
                    [
                        "run",
                        str(data),
                        str(recipe),
                        "--output",
                        str(output),
                        "--report",
                        str(root / "result.md"),
                    ]
                ),
                0,
            )
            result = json.loads(output.read_text(encoding="utf-8"))["results"][0]
            self.assertIn("p_value", result)
            self.assertFalse(
                result["diagnostics"]["equal_variance"]["available"]
            )

    def test_validate_rejects_unknown_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary) / "bad.json"
            recipe.write_text(
                json.dumps({"version": 1, "analyses": [{"op": "causal_magic"}]}),
                encoding="utf-8",
            )
            self.assertEqual(scholarly_stats.main(["validate", str(recipe)]), 2)

    @unittest.skipUnless(
        importlib.util.find_spec("statsmodels"),
        "Statsmodels is not installed",
    )
    def test_extended_models_diagnostics_and_reporting_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "model-data.csv"
            rows = ["score,age,site,time,outcome"]
            for index in range(24):
                score = 10 + index * 0.7 + (index % 3) * 1.1
                age = 20 + index
                site = "A" if index % 2 == 0 else "B"
                time = 5 + math.sin(index / 3) + index * 0.15
                outcome = 1 if (score + age / 10) > 32 else 0
                rows.append(f"{score:.4f},{age},{site},{time:.4f},{outcome}")
            data.write_text("\n".join(rows) + "\n", encoding="utf-8")
            recipe = root / "analysis.json"
            recipe.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "analyses": [
                            {
                                "op": "regression",
                                "formula": "score ~ age + C(site)",
                                "cov_type": "HC3",
                            },
                            {
                                "op": "diagnostics",
                                "formula": "score ~ age + C(site)",
                            },
                            {
                                "op": "multiple_comparison",
                                "p_values": [0.01, 0.04, 0.2],
                                "method": "fdr_bh",
                            },
                            {
                                "op": "time_series",
                                "test": "adf",
                                "column": "time",
                                "autolag": None,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "result.json"
            report = root / "result.md"
            self.assertEqual(
                scholarly_stats.main(
                    [
                        "run",
                        str(data),
                        str(recipe),
                        "--output",
                        str(output),
                        "--report",
                        str(report),
                    ]
                ),
                0,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            regression, diagnostics, multiple, time_series = payload["results"]
            self.assertEqual(regression["model_type"], "ols")
            self.assertEqual(regression["cov_type"], "HC3")
            self.assertIn("breusch_pagan", regression["diagnostics"])
            self.assertIn("vif", diagnostics["diagnostics"])
            self.assertEqual(multiple["method"], "fdr_bh")
            self.assertEqual(len(multiple["adjusted_p_values"]), 3)
            self.assertEqual(time_series["test"], "adf")
            self.assertIn("reporting_audit", payload)
            self.assertIn("experimental", report.read_text(encoding="utf-8"))

    @unittest.skipUnless(
        importlib.util.find_spec("statsmodels") and importlib.util.find_spec("scipy"),
        "Statsmodels and SciPy are not installed",
    )
    def test_glm_mixed_model_and_arima_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "models.csv"
            rows = ["group,score,age,outcome"]
            for index in range(16):
                score = 5 + index * 0.4
                age = 20 + index
                outcome = 1 if index % 3 else 0
                rows.append(f"{'A' if index < 8 else 'B'},{score:.3f},{age},{outcome}")
            data.write_text("\n".join(rows) + "\n", encoding="utf-8")
            table = scholarly_stats.read_table(data, scholarly_stats.InputOptions())
            glm = scholarly_stats.run_analysis(
                table,
                {
                    "op": "regression",
                    "model": "glm",
                    "family": "binomial",
                    "formula": "outcome ~ age + score",
                },
            )
            self.assertEqual(glm["model_type"], "glm")
            self.assertEqual(glm["family"], "binomial")
            mixed = scholarly_stats.run_analysis(
                table,
                {
                    "op": "regression",
                    "model": "mixedlm",
                    "formula": "score ~ age",
                    "groups": "group",
                },
            )
            self.assertEqual(mixed["model_type"], "mixedlm")
            self.assertIn("coefficients", mixed)
            arima = scholarly_stats.run_analysis(
                table,
                {
                    "op": "time_series",
                    "test": "arima",
                    "column": "score",
                    "order": [1, 1, 0],
                    "trend": "n",
                    "forecast_steps": 1,
                },
            )
            self.assertEqual(arima["test"], "arima")
            self.assertEqual(len(arima["forecast"]), 1)


if __name__ == "__main__":
    unittest.main()
