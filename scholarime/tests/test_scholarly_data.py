from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scholarly_data.py"
SPEC = importlib.util.spec_from_file_location("scholarly_data", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
data_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = data_tool
SPEC.loader.exec_module(data_tool)


class ScholarlyDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.csv_path = self.root / "survey.csv"
        self.csv_path.write_text(
            "\n".join(
                [
                    "title row",
                    "id,year,score,group,date,consent,notes",
                    "001,2020,10,A,2020-01-01,yes, first ",
                    "002,2021,12,B,2021-01-01,no,",
                    "003,2021,,A,2021-02-01,yes,third",
                    "003,2021,,A,2021-02-01,yes,third",
                    "004,notyear,1000,B,2023-01-01,maybe,outlier",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _options(self, header_row: int = 2) -> data_tool.InputOptions:
        return data_tool.InputOptions(header_row=header_row)

    def test_profile_infers_types_and_data_quality(self) -> None:
        table = data_tool.read_table(self.csv_path, self._options())
        profile = data_tool.profile_table(table, self._options())
        columns = {column["name"]: column for column in profile["columns"]}

        self.assertEqual(profile["row_count"], 5)
        self.assertEqual(profile["duplicate_rows"], 1)
        self.assertEqual(columns["id"]["semantic_role"], "identifier")
        self.assertEqual(columns["id"]["leading_zero_values"], 5)
        self.assertEqual(columns["year"]["inferred_type"], "mixed")
        self.assertEqual(columns["group"]["inferred_type"], "categorical")
        self.assertEqual(columns["date"]["inferred_type"], "date")
        self.assertEqual(columns["consent"]["inferred_type"], "categorical")
        self.assertNotIn("sample_values", columns["notes"])
        self.assertNotIn("top_values", columns["notes"]["stats"])
        self.assertTrue(any(issue["topic"] == "duplicates" for issue in profile["issues"]))
        self.assertIn("# Data Profile", data_tool.profile_markdown(profile))

    def test_profile_samples_require_opt_in(self) -> None:
        table = data_tool.read_table(self.csv_path, self._options())
        profile = data_tool.profile_table(table, self._options(), include_samples=True)
        columns = {column["name"]: column for column in profile["columns"]}
        self.assertIn("sample_values", columns["group"])
        self.assertIn("top_values", columns["group"]["stats"])

    def test_apply_recipe_produces_audited_output(self) -> None:
        recipe_path = self.root / "recipe.json"
        recipe_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "operations": [
                        {"op": "trim_whitespace", "columns": ["notes"]},
                        {"op": "rename", "mapping": {"group": "cohort"}},
                        {"op": "convert", "columns": {"year": "integer"}, "invalid": "null"},
                        {
                            "op": "filter",
                            "conditions": [
                                {"column": "year", "operator": "not_null"}
                            ],
                        },
                        {"op": "convert", "columns": {"score": "float"}, "invalid": "null"},
                        {
                            "op": "fill_missing",
                            "column": "score",
                            "strategy": "mean",
                        },
                        {
                            "op": "map_values",
                            "column": "consent",
                            "mapping": {"yes": "true", "no": "false"},
                            "unmapped": "null",
                        },
                        {"op": "deduplicate", "subset": ["id"], "keep": "first"},
                        {"op": "derive", "column": "adjusted", "expression": "score + 1"},
                        {
                            "op": "sort",
                            "by": ["adjusted"],
                            "descending": True,
                            "nulls": "last",
                        },
                        {"op": "limit", "count": 3},
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "cleaned.csv"
        result = data_tool.main(
            [
                "apply",
                str(self.csv_path),
                str(recipe_path),
                "--header-row",
                "2",
                "-o",
                str(output),
            ]
        )
        self.assertEqual(result, 0)
        with output.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertIn("cohort", rows[0])
        self.assertNotIn("group", rows[0])
        self.assertEqual(rows[0]["notes"], "")
        self.assertEqual(rows[0]["consent"], "false")
        self.assertEqual(float(rows[0]["score"]), 12.0)
        self.assertTrue((self.root / "cleaned.audit.json").is_file())
        self.assertTrue((self.root / "cleaned.audit.md").is_file())
        audit = json.loads((self.root / "cleaned.audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["input"]["rows"], 5)
        self.assertEqual(audit["output"]["rows"], 3)
        self.assertEqual(len(audit["operations"]), 11)

    def test_groupby_and_sort_nulls(self) -> None:
        table = data_tool.read_table(self.csv_path, self._options())
        processed, audit = data_tool.apply_operations(
            table,
            [
                {"op": "convert", "columns": {"score": "float"}, "invalid": "null"},
                {
                    "op": "groupby",
                    "by": ["consent"],
                    "aggregations": {
                        "record_count": {"function": "count", "column": "*"},
                        "mean_score": {"function": "mean", "column": "score"},
                    },
                },
                {
                    "op": "sort",
                    "by": ["mean_score"],
                    "descending": True,
                    "nulls": "first",
                },
            ],
            data_tool.DEFAULT_NULL_TOKENS,
        )
        self.assertEqual(processed.headers, ["consent", "record_count", "mean_score"])
        self.assertEqual(processed.rows[0]["consent"], "maybe")
        self.assertEqual(len(audit), 3)

    def test_malicious_derive_expression_is_rejected(self) -> None:
        with self.assertRaises(data_tool.DataError):
            data_tool._evaluate_expression("__import__('os').system('echo bad')", {})

    def test_original_output_and_overwrite_are_protected(self) -> None:
        recipe_path = self.root / "identity.json"
        recipe_path.write_text(
            json.dumps({"version": 1, "operations": []}),
            encoding="utf-8",
        )
        result = data_tool.main(
            [
                "apply",
                str(self.csv_path),
                str(recipe_path),
                "--header-row",
                "2",
                "-o",
                str(self.csv_path),
            ]
        )
        self.assertEqual(result, 2)
        self.assertTrue(self.csv_path.is_file())

        output = self.root / "existing.csv"
        output.write_text("keep", encoding="utf-8")
        result = data_tool.main(
            [
                "apply",
                str(self.csv_path),
                str(recipe_path),
                "--header-row",
                "2",
                "-o",
                str(output),
            ]
        )
        self.assertEqual(result, 2)
        self.assertEqual(output.read_text(encoding="utf-8"), "keep")

    def test_jsonl_and_headerless_inputs(self) -> None:
        jsonl = self.root / "records.jsonl"
        jsonl.write_text(
            "\n".join(
                [
                    '{"name":"A","value":1}',
                    '{"name":"B","value":2}',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        table = data_tool.read_table(jsonl, data_tool.InputOptions())
        self.assertEqual(table.headers, ["name", "value"])
        self.assertEqual(len(table.rows), 2)

        headerless = self.root / "headerless.csv"
        headerless.write_text("A,1\nB,2\n", encoding="utf-8")
        table = data_tool.read_table(
            headerless,
            data_tool.InputOptions(header_row=0),
        )
        self.assertEqual(table.headers, ["Column 1", "Column 2"])

    def test_validate_recipe_accepts_column_dependent_operations(self) -> None:
        recipe_path = self.root / "validate.json"
        recipe_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "operations": [
                        {"op": "convert", "columns": {"score": "float"}},
                        {"op": "filter", "conditions": [{"column": "score", "operator": "gt", "value": 0}]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(data_tool.main(["validate", str(recipe_path)]), 0)


if __name__ == "__main__":
    unittest.main()
