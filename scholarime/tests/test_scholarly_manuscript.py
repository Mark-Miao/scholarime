from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_workspace
import scholarly_manuscript


class ScholarlyManuscriptTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "project"
        research_workspace.initialize(project, title="Manuscript test")
        sources = root / "sources.json"
        sources.write_text(
            json.dumps(
                {
                    "references": [
                        {
                            "record_id": "ref-a",
                            "title": "Academic retrieval",
                            "authors": ["Ada Reader"],
                            "year": 2025,
                            "doi": "10.1000/a",
                            "container": "Journal of Search",
                            "source": "OpenAlex",
                            "item_type": "article",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        research_workspace.add_sources(project, sources)
        return project

    def test_latex_manuscript_and_citation_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            figure = root / "figure.png"
            figure.write_bytes(b"not-decoded-by-latex-test")
            manuscript = root / "manuscript.json"
            manuscript.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "title": "A Test Manuscript",
                        "authors": ["Ada Reader"],
                        "abstract": "Academic systems matter [@ref-a].",
                        "citation_style": "apa",
                        "sections": [
                            {
                                "heading": "Introduction",
                                "level": 1,
                                "paragraphs": ["Retrieval is changing [@ref-a]."],
                            }
                        ],
                        "figures": [
                            {
                                "path": str(figure),
                                "caption": "Retrieval workflow",
                                "label": "fig:workflow",
                            }
                        ],
                        "tables": [
                            {
                                "caption": "Example values",
                                "headers": ["Group", "Score"],
                                "rows": [["A", "10"], ["B", "12"]],
                                "label": "tab:values",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            prefix = root / "paper"
            result = scholarly_manuscript.main(
                [
                    "build",
                    "--project-dir",
                    str(project),
                    "--manuscript",
                    str(manuscript),
                    "--output-prefix",
                    str(prefix),
                    "--formats",
                    "latex",
                    "--skill-root",
                    str(SKILL_ROOT),
                ]
            )
            self.assertEqual(result, 0)
            latex = prefix.with_suffix(".tex").read_text(encoding="utf-8")
            self.assertIn(r"\section{Introduction}", latex)
            self.assertIn("Academic retrieval", latex)
            self.assertIn(r"\includegraphics", latex)
            self.assertIn(r"\begin{table}", latex)
            report = json.loads(prefix.with_suffix(".build.json").read_text(encoding="utf-8"))
            self.assertIn(report["citation_engine"], {"citeproc", "unavailable", "error"})
            self.assertEqual(len(report["cited_reference_ids"]), 1)

    def test_unknown_citation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            manuscript = root / "bad.json"
            manuscript.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "title": "Bad",
                        "sections": [
                            {"heading": "Intro", "paragraphs": ["Missing [@nope]."]}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                scholarly_manuscript.main(
                    [
                        "build",
                        "--project-dir",
                        str(project),
                        "--manuscript",
                        str(manuscript),
                        "--output-prefix",
                        str(root / "bad"),
                        "--formats",
                        "latex",
                    ]
                ),
                2,
            )

    @unittest.skipUnless(
        importlib.util.find_spec("docx") is not None
        and importlib.util.find_spec("citeproc") is not None,
        "python-docx and citeproc-py are required",
    )
    def test_docx_and_csl_processor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            manuscript = root / "manuscript.json"
            manuscript.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "title": "DOCX Test",
                        "sections": [
                            {
                                "heading": "Introduction",
                                "paragraphs": ["Citation [@ref-a]."],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            prefix = root / "paper"
            result = scholarly_manuscript.main(
                [
                    "build",
                    "--project-dir",
                    str(project),
                    "--manuscript",
                    str(manuscript),
                    "--output-prefix",
                    str(prefix),
                    "--formats",
                    "latex,docx",
                    "--skill-root",
                    str(SKILL_ROOT),
                ]
            )
            self.assertEqual(result, 0)
            self.assertTrue(prefix.with_suffix(".docx").is_file())
            report = json.loads(prefix.with_suffix(".build.json").read_text(encoding="utf-8"))
            self.assertEqual(report["citation_engine"], "citeproc")

    @unittest.skipIf(
        importlib.util.find_spec("docx") is not None,
        "python-docx is installed; missing-dependency path is not applicable",
    )
    def test_docx_missing_dependency_does_not_leave_latex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            manuscript = root / "manuscript.json"
            manuscript.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "title": "No partial output",
                        "sections": [{"heading": "Intro", "paragraphs": ["Text."]}],
                    }
                ),
                encoding="utf-8",
            )
            prefix = root / "partial"
            result = scholarly_manuscript.main(
                [
                    "build",
                    "--project-dir",
                    str(project),
                    "--manuscript",
                    str(manuscript),
                    "--output-prefix",
                    str(prefix),
                    "--formats",
                    "latex,docx",
                ]
            )
            self.assertEqual(result, 2)
            self.assertFalse(prefix.with_suffix(".tex").exists())
            self.assertFalse(prefix.with_suffix(".docx").exists())


if __name__ == "__main__":
    unittest.main()
