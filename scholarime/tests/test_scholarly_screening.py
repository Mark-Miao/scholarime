from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "scholarly_screening.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location("scholarly_screening", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
screening_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = screening_tool
SPEC.loader.exec_module(screening_tool)

import research_workspace


class ScholarimeScreeningTests(unittest.TestCase):
    def _candidate_file(self, root: Path) -> Path:
        path = root / "candidates.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": "search-1",
                    "results": [
                        {
                            "record_id": "search-record-1",
                            "title": "Academic Writing with Language Models",
                            "authors": ["Ada Scholar", "Lin Reader"],
                            "year": 2025,
                            "container": "Journal of Academic AI",
                            "doi": "10.1000/screening.1",
                            "abstract": "A study of academic writing support.",
                            "keywords": ["academic writing", "language models"],
                            "verification_status": "verified",
                            "item_type": "article",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_init_copies_protocol_and_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "review"
            output = io.StringIO()
            with redirect_stdout(output):
                code = screening_tool.main(["init", str(work)])
            self.assertEqual(code, 0)
            self.assertTrue((work / "screening_config.json").is_file())
            self.assertTrue((work / "screening_protocol.md").is_file())

    def test_prepare_converts_scholarime_json_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = self._candidate_file(root)
            work = root / "screening"
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = screening_tool.main(
                    [
                        "prepare",
                        "--work",
                        str(work),
                        "--inputs",
                        str(candidates),
                    ]
                )
            self.assertEqual(code, 0, errors.getvalue() + output.getvalue())
            records = json.loads((work / "records.json").read_text(encoding="utf-8"))
            self.assertEqual(len(records["unique"]), 1)
            self.assertEqual(
                records["unique"][0]["title"],
                "Academic Writing with Language Models",
            )
            self.assertTrue((work / "batches" / "b001.txt").is_file())

    def test_selection_returns_workspace_compatible_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            records = {
                "unique": [
                    {
                        "id": "R001",
                        "title": "Included Study",
                        "authors": ["Ada Scholar"],
                        "year": "2024",
                        "journal": "Journal",
                        "doi": "10.1000/include",
                        "pmid": "",
                        "abstract": "Included abstract",
                        "kw": ["evidence"],
                        "batch": "b001",
                    },
                    {
                        "id": "R002",
                        "title": "Excluded Study",
                        "authors": [],
                        "year": "2023",
                        "journal": "",
                        "doi": "",
                        "pmid": "",
                        "abstract": "",
                        "kw": [],
                        "batch": "b001",
                    },
                ]
            }
            decisions = {
                "R001": {"final": {"d": "include", "code": "INC"}},
                "R002": {"final": {"d": "exclude", "code": "E1"}},
            }
            (work / "records.json").write_text(json.dumps(records), encoding="utf-8")
            (work / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")

            references = screening_tool._source_selection(work, "ta", "advanced")

            self.assertEqual(len(references), 1)
            self.assertEqual(references[0]["record_id"], "R001")
            self.assertEqual(references[0]["source"], "Scholarime screening")

    def test_outputs_import_selected_sources_into_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "screening"
            output = root / "output"
            workspace = root / "workspace"
            work.mkdir()
            output.mkdir()
            research_workspace.initialize(workspace, title="Review")
            (work / "records.json").write_text(
                json.dumps(
                    {
                        "unique": [
                            {
                                "id": "R001",
                                "title": "Included Study",
                                "authors": ["Ada Scholar"],
                                "year": "2024",
                                "journal": "Journal",
                                "doi": "10.1000/include",
                                "pmid": "",
                                "abstract": "Included abstract",
                                "kw": ["evidence"],
                                "batch": "b001",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (work / "decisions.json").write_text(
                json.dumps({"R001": {"final": {"d": "include", "code": "INC"}}}),
                encoding="utf-8",
            )

            def fake_run(_script, _args):
                (output / "TA_prisma_counts.json").write_text("{}", encoding="utf-8")
                (output / "TA_prisma_counts.md").write_text("# PRISMA", encoding="utf-8")
                return 0

            stdout = io.StringIO()
            with mock.patch.object(screening_tool, "_run_script", side_effect=fake_run):
                with redirect_stdout(stdout):
                    code = screening_tool.build_outputs(
                        work,
                        output,
                        stage="ta",
                        config=None,
                        tag_keywords=False,
                        workspace=workspace,
                        selection="advanced",
                    )

            self.assertEqual(code, 0)
            self.assertTrue((output / "TA_scholarime_sources.json").is_file())
            sources = research_workspace.list_sources(workspace)
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["title"], "Included Study")


if __name__ == "__main__":
    unittest.main()
