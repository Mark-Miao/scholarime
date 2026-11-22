from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "scholarly_figure.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location("scholarly_figure", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
figure_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = figure_tool
SPEC.loader.exec_module(figure_tool)


class ScholarlyFigureTests(unittest.TestCase):
    def test_help_lists_templates_and_audits(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(figure_tool.main(["--help"]), 0)
        text = output.getvalue()
        for command in ("template", "validate-source", "collision", "panel-alignment", "svg"):
            self.assertIn(command, text)
        self.assertIn("heatmap", figure_tool.usage())
        self.assertIn("forest", figure_tool.usage())

    def test_dependency_free_audit_self_tests(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = figure_tool.main(["self-test"])
        self.assertEqual(code, 0, errors.getvalue() + output.getvalue())
        self.assertIn("audit_svg_schematic.py", output.getvalue())

    @unittest.skipUnless(
        importlib.util.find_spec("matplotlib") and importlib.util.find_spec("numpy"),
        "Matplotlib or NumPy is not installed",
    )
    def test_volcano_template_writes_png_and_qa(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "volcano"
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = figure_tool.main(
                    [
                        "template",
                        "volcano",
                        "--demo",
                        "--output",
                        str(prefix),
                        "--font-size",
                        "7",
                    ]
                )
            self.assertEqual(code, 0, errors.getvalue() + output.getvalue())
            self.assertTrue(prefix.with_suffix(".png").is_file())
            qa = json.loads(prefix.with_suffix(".qa.json").read_text(encoding="utf-8"))
            self.assertEqual(qa["template"], "volcano")
            self.assertTrue(qa["demo"])
            self.assertEqual(qa["font"]["requested_family"], "Times New Roman")
            self.assertTrue(qa["font"]["resolved_family"])


if __name__ == "__main__":
    unittest.main()
