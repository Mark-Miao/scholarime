from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_workspace


class ResearchWorkspaceTests(unittest.TestCase):
    def test_sources_claims_and_evidence_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            research_workspace.initialize(root, title="Evidence test")
            sources = root / "sources.json"
            sources.write_text(
                json.dumps(
                    {
                        "references": [
                            {
                                "record_id": "ref-1",
                                "title": "Evidence paper",
                                "authors": ["Ada Reader"],
                                "year": 2025,
                                "doi": "10.1000/evidence",
                                "source": "OpenAlex",
                                "item_type": "article",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(research_workspace.add_sources(root, sources), 1)
            listed = research_workspace.list_sources(root)
            self.assertEqual(listed[0]["record_id"], "ref-1")

            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% test fixture\n")
            attachment = research_workspace.attach_pdf(root, listed[0]["id"], pdf)
            segment_id = research_workspace.add_segment(
                root,
                attachment["attachment_id"],
                page=3,
                kind="text",
                text="Participants improved after the intervention.",
            )
            claim_id = research_workspace.add_claim(
                root,
                "The intervention improved participant outcomes.",
            )
            evidence_id = research_workspace.link_evidence(
                root,
                claim_id,
                segment_id,
                "supports",
                "Results section",
            )
            self.assertGreater(evidence_id, 0)
            matrix = research_workspace.evidence_matrix(root)
            self.assertEqual(matrix["claims"][0]["evidence"][0]["page"], 3)
            self.assertEqual(
                matrix["claims"][0]["evidence"][0]["record_id"],
                "ref-1",
            )

            output = root / "matrix.json"
            self.assertEqual(
                research_workspace.main(["matrix", str(root), "--output", str(output)]),
                0,
            )
            self.assertTrue(output.is_file())
            self.assertTrue(output.with_suffix(".md").is_file())

            segments = research_workspace.list_segments(root, attachment["attachment_id"])
            self.assertEqual(segments[0]["text"], "Participants improved after the intervention.")

    def test_status_and_duplicate_source_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            research_workspace.initialize(root)
            sources = root / "sources.json"
            payload = {
                "results": [
                    {
                        "record_id": "x",
                        "title": "First title",
                        "authors": [],
                        "year": 2024,
                    }
                ]
            }
            sources.write_text(json.dumps(payload), encoding="utf-8")
            research_workspace.add_sources(root, sources)
            payload["results"][0]["title"] = "Updated title"
            sources.write_text(json.dumps(payload), encoding="utf-8")
            research_workspace.add_sources(root, sources)
            listed = research_workspace.list_sources(root)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["title"], "Updated title")
            status = research_workspace.workspace_status(root)
            self.assertEqual(status["counts"]["sources"], 1)


if __name__ == "__main__":
    unittest.main()
