from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "scholarly_verify.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location("scholarly_verify", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
verify_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify_tool
SPEC.loader.exec_module(verify_tool)
citeverify_lookup = verify_tool.citeverify_lookup


class ScholarlyVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _candidate(self, index: int, **overrides):
        candidate = {
            "record_id": f"record-{index}",
            "title": f"Academic Research Method {index}",
            "source": "openalex",
            "authors": ["Ada Scholar"],
            "year": 2025,
            "container": "Journal of Tests",
            "item_type": "article",
        }
        candidate.update(overrides)
        return candidate

    @staticmethod
    def _fake_result(item, status: str):
        return {
            "reference_number": item["number"],
            "raw_citation": item["raw_citation"],
            "processed_citation": item["cleaned_citation"],
            "title_queried": item["title_candidates"][0]["title"],
            "doi": item["doi"],
            "citation_type": item["citation_type"],
            "verification": {"status": status, "source": "test"},
        }

    def test_candidate_maps_to_citeverify_diagnostic(self) -> None:
        candidate = self._candidate(1, doi="10.1000/test")
        diagnostic = verify_tool.candidate_to_diagnostic(candidate, 3)

        self.assertEqual(diagnostic["number"], 3)
        self.assertEqual(diagnostic["doi"], "10.1000/test")
        self.assertEqual(diagnostic["citation_type"], "article_or_conference")
        self.assertEqual(
            diagnostic["title_candidates"],
            [{"title": candidate["title"], "method": "before_in", "confidence": 1.0}],
        )
        self.assertIn("Ada Scholar (2025)", diagnostic["raw_citation"])
        self.assertIn("Candidate ID: record-1.", diagnostic["parser_notes"])

    def test_verification_enriches_candidates_and_counts_statuses(self) -> None:
        candidates = [self._candidate(index) for index in range(1, 6)]
        statuses = iter(
            [
                "conflict",
                "unverified",
                "unable_to_check",
                "verified_title_variant",
                "verified",
            ]
        )
        calls = []

        def fake_verifier(item, pause, openalex_key, semantic_key, sequence_number):
            calls.append((pause, openalex_key, semantic_key, sequence_number))
            return self._fake_result(item, next(statuses))

        payload = verify_tool.verify_candidates(
            candidates,
            source="candidates.json",
            source_sha256="abc123",
            pause=-1,
            openalex_key="openalex-secret",
            semantic_key="semantic-secret",
            verifier=fake_verifier,
        )

        self.assertEqual(
            payload["status_counts"],
            {
                "conflict": 1,
                "unverified": 1,
                "unable_to_check": 1,
                "verified_title_variant": 1,
                "verified": 1,
            },
        )
        self.assertEqual(
            [item["verification_status"] for item in payload["candidates"]],
            [
                "conflict",
                "unverified",
                "unable_to_check",
                "verified_title_variant",
                "verified",
            ],
        )
        self.assertEqual(sum(payload["field_status_counts"].values()), 5)
        self.assertIn("conflict", payload["field_status_counts"])
        self.assertIn("insufficient_metadata", payload["field_status_counts"])
        self.assertEqual(calls[0], (0.0, "openalex-secret", "semantic-secret", 1))
        self.assertNotIn("openalex-secret", json.dumps(payload))
        self.assertNotIn("semantic-secret", json.dumps(payload))

    def test_limit_and_outputs(self) -> None:
        candidates = [self._candidate(1), self._candidate(2)]

        def fake_verifier(item, pause, openalex_key, semantic_key, sequence_number):
            return self._fake_result(item, "verified")

        payload = verify_tool.verify_candidates(
            candidates,
            source="candidates.json",
            source_sha256="abc123",
            limit=1,
            verifier=fake_verifier,
        )
        output = self.root / "citation-verification.json"
        report = self.root / "citation-verification.html"
        verify_tool._write_outputs(payload, output, report, overwrite=False)

        self.assertEqual(payload["reference_count"], 1)
        self.assertTrue(output.is_file())
        self.assertTrue(report.is_file())
        written = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(written["candidates"][0]["record_id"], "record-1")
        self.assertIn("Academic Research Method 1", report.read_text(encoding="utf-8"))
        with self.assertRaises(verify_tool.VerificationError):
            verify_tool._write_outputs(payload, output, report, overwrite=False)
        with self.assertRaisesRegex(verify_tool.VerificationError, "different paths"):
            verify_tool._write_outputs(payload, output, output, overwrite=True)

    def test_invalid_limit_and_missing_title_are_rejected(self) -> None:
        with self.assertRaisesRegex(verify_tool.VerificationError, "limit"):
            verify_tool.verify_candidates(
                [self._candidate(1)],
                source="candidates.json",
                source_sha256="abc123",
                limit=0,
                verifier=lambda *args: {},
            )
        with self.assertRaisesRegex(verify_tool.VerificationError, "no title"):
            verify_tool.candidate_to_diagnostic(self._candidate(1, title=""), 1)

    def test_key_file_is_read_without_echo(self) -> None:
        key_file = self.root / "openalex.key"
        key_file.write_text("local-secret\n", encoding="utf-8")
        self.assertEqual(verify_tool.read_key_file(key_file), "local-secret")

    def test_field_comparison_flags_metadata_conflicts(self) -> None:
        candidate = self._candidate(
            1,
            authors=["Ada Scholar"],
            year=2025,
            volume="12",
            issue="3",
            pages="101-110",
        )
        verification = {
            "status": "verified",
            "matched_title": candidate["title"],
            "matched_authors": ["Ada Scholar"],
            "matched_year": 2025,
            "matched_volume": "13",
            "matched_issue": "3",
            "matched_pages": "101-110",
            "matched_container": candidate["container"],
        }
        comparison = verify_tool.compare_candidate_fields(candidate, verification)

        self.assertEqual(comparison["status"], "conflict")
        self.assertEqual(comparison["fields"]["volume"]["status"], "mismatch")
        self.assertEqual(comparison["fields"]["authors"]["status"], "match")
        self.assertEqual(comparison["fields"]["year"]["status"], "match")

    def test_vendored_lookup_retains_crossref_fields(self) -> None:
        result = citeverify_lookup._doi_result(
            "Crossref",
            "10.1000/test",
            {
                "message": {
                    "title": ["Academic Research Method 1"],
                    "author": [{"given": "Ada", "family": "Scholar"}],
                    "issued": {"date-parts": [[2025, 3, 1]]},
                    "container-title": ["Journal of Tests"],
                    "volume": "12",
                    "issue": "3",
                    "page": "101-110",
                    "URL": "https://doi.org/10.1000/test",
                }
            },
            "Academic Research Method 1",
        )

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["matched_year"], 2025)
        self.assertEqual(result["matched_container"], "Journal of Tests")
        self.assertEqual(result["matched_volume"], "12")
        self.assertEqual(result["matched_issue"], "3")
        self.assertEqual(result["matched_pages"], "101-110")

    def test_verification_output_can_be_reloaded(self) -> None:
        source = self.root / "verification.json"
        source.write_text(
            json.dumps(
                {
                    "run_id": "run-1",
                    "candidates": [self._candidate(1, verification_status="verified")],
                    "results": [{"verification": {"status": "verified"}}],
                }
            ),
            encoding="utf-8",
        )
        document, candidates = verify_tool.load_candidates(source)

        self.assertEqual(document["run_id"], "run-1")
        self.assertEqual(candidates[0]["title"], "Academic Research Method 1")

    def test_cli_writes_metadata_only_verification(self) -> None:
        source = self.root / "candidates.json"
        source.write_text(
            json.dumps({"run_id": "run-1", "results": [self._candidate(1)]}),
            encoding="utf-8",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            code = verify_tool.main([str(source), "--pause", "0"])

        self.assertEqual(code, 0)
        self.assertIn("Verification JSON:", output.getvalue())
        self.assertTrue((self.root / "citation-verification.json").is_file())
        payload = json.loads(
            (self.root / "citation-verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["status_counts"], {"unverified": 1})


if __name__ == "__main__":
    unittest.main()
