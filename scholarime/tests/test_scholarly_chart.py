from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scholarly_chart.py"
SPEC = importlib.util.spec_from_file_location("scholarly_chart", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
chart = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chart
SPEC.loader.exec_module(chart)


class ScholarlyChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_csv(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def _assert_png(self, path: Path) -> None:
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)

    def test_csv_metadata_and_cli_overrides(self) -> None:
        csv_path = self._write_csv(
            "metadata.csv",
            "\n".join(
                [
                    "# scholarime: chart_type=line",
                    "# scholarime: header_row=2",
                    "# scholarime: y_columns=Control,Treatment",
                    "# scholarime: palette=academic-earth",
                    "# scholarime: output=configured.png",
                    "Unused title row",
                    "Category,Control,Treatment",
                    "Baseline,0.72,0.78",
                    "Intervention,0.81,0.89",
                    "",
                ]
            ),
        )
        output = self.root / "override.png"
        result = chart.generate_chart(
            csv_path,
            {
                "output": output,
                "chart_type": "bar",
                "font_size": 9.5,
                "legend": True,
            },
        )

        self._assert_png(result.output_path)
        self.assertEqual(result.output_path, output.resolve())
        self.assertEqual(result.chart_type, "bar")
        self.assertEqual(result.series_count, 2)
        self.assertEqual(result.font_size, 9.5)
        self.assertFalse(result.font_size_auto)

    def test_metadata_output_is_relative_to_csv(self) -> None:
        csv_path = self._write_csv(
            "relative.csv",
            "\n".join(
                [
                    "# scholarime: output=figures/result.png",
                    "Category,Value",
                    "A,1",
                    "B,2",
                    "",
                ]
            ),
        )
        result = chart.generate_chart(csv_path)

        self._assert_png(self.root / "figures" / "result.png")
        self.assertEqual(result.output_path, (self.root / "figures" / "result.png").resolve())
        self.assertTrue(result.font_size_auto)
        self.assertGreater(result.font_size, 0)

    def test_four_chart_types_render(self) -> None:
        series_csv = self._write_csv(
            "series.csv",
            "\n".join(
                [
                    "Category,First,Second",
                    "A,1,2",
                    "B,2,3",
                    "C,3,5",
                    "",
                ]
            ),
        )
        for chart_type in ("bar", "line", "scatter"):
            with self.subTest(chart_type=chart_type):
                output = self.root / f"{chart_type}.png"
                result = chart.generate_chart(
                    series_csv,
                    {
                        "output": output,
                        "chart_type": chart_type,
                        "legend_colors": ("#123456", "#B5651D"),
                    },
                )
                self._assert_png(result.output_path)

        pie_csv = self._write_csv(
            "pie.csv",
            "\n".join(
                [
                    "Category,Share",
                    "Methods,40",
                    "Results,35",
                    "Discussion,25",
                    "",
                ]
            ),
        )
        pie_output = self.root / "pie.png"
        result = chart.generate_chart(
            pie_csv,
            {"output": pie_output, "chart_type": "pie", "palette": "academic-warm"},
        )
        self._assert_png(result.output_path)

    def test_headerless_csv(self) -> None:
        csv_path = self._write_csv(
            "headerless.csv",
            "\n".join(
                [
                    "A,10",
                    "B,20",
                    "C,30",
                    "",
                ]
            ),
        )
        result = chart.generate_chart(
            csv_path,
            {
                "output": self.root / "headerless.png",
                "header_row": 0,
                "x_column": "1",
                "y_columns": ("2",),
            },
        )
        self._assert_png(result.output_path)

    def test_missing_font_falls_back_and_is_reported(self) -> None:
        csv_path = self._write_csv(
            "font.csv",
            "\n".join(["Category,Value", "A,1", "B,2", ""]),
        )
        result = chart.generate_chart(
            csv_path,
            {
                "output": self.root / "font.png",
                "font_family": "Definitely Missing Font",
            },
        )
        self.assertTrue(result.font_fallback)
        self.assertTrue(result.font_family)
        self.assertEqual(result.requested_font, "Definitely Missing Font")
        self._assert_png(result.output_path)

    def test_existing_output_requires_overwrite_flag(self) -> None:
        csv_path = self._write_csv(
            "overwrite.csv",
            "\n".join(["Category,Value", "A,1", "B,2", ""]),
        )
        output = self.root / "existing.png"
        output.write_bytes(b"do not replace")

        with self.assertRaisesRegex(chart.ChartError, "Output already exists"):
            chart.generate_chart(csv_path, {"output": output})

        self.assertEqual(output.read_bytes(), b"do not replace")
        result = chart.generate_chart(csv_path, {"output": output, "overwrite": True})
        self._assert_png(result.output_path)

    def test_invalid_column_raises_clear_error(self) -> None:
        csv_path = self._write_csv(
            "invalid.csv",
            "\n".join(["Category,Value", "A,1", "B,2", ""]),
        )
        with self.assertRaisesRegex(chart.ChartError, "Column 'Missing' was not found"):
            chart.generate_chart(
                csv_path,
                {
                    "output": self.root / "invalid.png",
                    "x_column": "Missing",
                },
            )

    def test_cli_help_and_template_export(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--list-palettes", help_result.stdout)
        self.assertIn("--legend-colors", help_result.stdout)

        template_path = self.root / "template.csv"
        template_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--export-csv-template",
                str(template_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(template_result.returncode, 0, template_result.stderr)
        self.assertIn("# scholarime: chart_type=bar", template_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
