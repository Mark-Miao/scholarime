from __future__ import annotations

import importlib.util
import json
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


if __name__ == "__main__":
    unittest.main()
