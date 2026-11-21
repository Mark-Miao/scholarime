from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import academic_search
import reference_import
import reference_review
import scholar_common
import scholar_config


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.urls: list[str] = []

    def get_json(self, url: str, headers=None):
        self.urls.append(url)
        self.headers.update(headers or {})
        if "openalex.org/works" in url:
            return {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "display_name": "Large Language Models for Academic Writing",
                        "publication_year": 2025,
                        "authorships": [{"author": {"display_name": "Ada Scholar"}}],
                        "primary_location": {
                            "source": {"display_name": "Journal of Scholarly AI"}
                        },
                        "doi": "https://doi.org/10.1000/test.1",
                        "cited_by_count": 42,
                        "type": "article",
                        "language": "en",
                        "concepts": [{"display_name": "Academic writing"}],
                        "abstract_inverted_index": {
                            "Academic": [0],
                            "writing": [1],
                            "with": [2],
                            "language": [3],
                            "models": [4],
                        },
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "display_name": "Marine sediment transport",
                        "publication_year": 2019,
                        "authorships": [],
                        "primary_location": {},
                        "doi": "",
                        "cited_by_count": 1000,
                        "type": "article",
                        "abstract_inverted_index": {},
                    },
                ]
            }
        if "api.crossref.org" in url:
            return {
                "message": {
                    "items": [
                        {
                            "DOI": "10.1000/test.1",
                            "title": ["Large Language Models for Academic Writing"],
                            "author": [{"given": "Ada", "family": "Scholar"}],
                            "published": {"date-parts": [[2025]]},
                            "container-title": ["Journal of Scholarly AI"],
                            "volume": "12",
                            "issue": "3",
                            "page": "1-18",
                            "URL": "https://doi.org/10.1000/test.1",
                            "type": "journal-article",
                            "publisher": "Scholar Press",
                            "is-referenced-by-count": 42,
                        }
                    ]
                }
            }
        if "semanticscholar.org" in url:
            return {
                "data": [
                    {
                        "paperId": "semantic-1",
                        "title": "Semantic retrieval for scholarship",
                        "authors": [{"name": "Lin Reader"}],
                        "year": 2024,
                        "venue": "SIGIR",
                        "abstract": "Retrieval across academic literature.",
                        "externalIds": {"DOI": "10.1000/semantic"},
                        "url": "https://www.semanticscholar.org/paper/semantic-1",
                        "openAccessPdf": {"url": "https://example.org/paper.pdf"},
                        "publicationTypes": ["JournalArticle"],
                        "citationCount": 7,
                    }
                ]
            }
        if "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" in url:
            return {"esearchresult": {"idlist": ["12345"]}}
        raise AssertionError(f"Unexpected URL: {url}")

    def get_text(self, url: str, headers=None):
        self.urls.append(url)
        if "export.arxiv.org/api/query" in url:
            return """<feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
              <id>https://arxiv.org/abs/2501.00001</id>
              <title>Large language models for academic writing</title>
              <author><name>Ada Reader</name></author>
              <published>2025-01-01T00:00:00Z</published>
              <summary>An arXiv test abstract.</summary>
              <link title="pdf" href="https://arxiv.org/pdf/2501.00001"/>
              <category term="cs.CL"/>
            </entry>
            </feed>"""
        if "eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi" in url:
            return """<PubmedArticleSet><PubmedArticle>
            <MedlineCitation><PMID>12345</PMID>
            <Article><ArticleTitle>Academic search test</ArticleTitle>
            <Abstract><AbstractText>Test abstract.</AbstractText></Abstract>
            <AuthorList><Author><ForeName>Ada</ForeName><LastName>Reader</LastName></Author></AuthorList>
            <Journal><Title>Test Journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
            </Article>
            </MedlineCitation>
            <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/pubmed</ArticleId></ArticleIdList></PubmedData>
            </PubmedArticle></PubmedArticleSet>"""
        raise AssertionError(f"Unexpected text URL: {url}")


class ReferenceImportTests(unittest.TestCase):
    def test_ris_import(self) -> None:
        content = """TY  - JOUR
TI  - Chinese and English retrieval
AU  - Zhang, San
AU  - Reader, Ada
PY  - 2025
JO  - Journal of Retrieval
DO  - 10.1000/ris
AB  - A bilingual study.
ER  -
"""
        references = reference_import.parse_import("sample.ris", content)
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].title, "Chinese and English retrieval")
        self.assertEqual(references[0].doi, "10.1000/ris")
        self.assertEqual(references[0].authors, ["Zhang, San", "Reader, Ada"])

    def test_bibtex_import(self) -> None:
        content = r"""
@article{scholar2025,
  title = {Retrieval for {Academic} Writing},
  author = {Ada Reader and Lin Scholar},
  journal = {Journal of Search},
  year = {2025},
  volume = {4},
  number = {2},
  pages = {10--22},
  doi = {10.1000/bib}
}
"""
        references = reference_import.parse_import("sample.bib", content)
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].title, "Retrieval for Academic Writing")
        self.assertEqual(references[0].container, "Journal of Search")
        self.assertEqual(references[0].doi, "10.1000/bib")

    def test_endnote_and_refworks_import(self) -> None:
        endnote = """%0 Journal Article
%A Zhang, San
%T 中文文献检索
%D 2024
%J 学术研究
%R 10.1000/enw

"""
        refworks = """RT Journal Article
A1 Zhang, San
T1 中文文献检索
YR 2024
JF 学术研究
DO 10.1000/ref

"""
        self.assertEqual(reference_import.parse_import("sample.enw", endnote)[0].year, 2024)
        self.assertEqual(
            reference_import.parse_import("export.txt", refworks)[0].doi,
            "10.1000/ref",
        )


class SearchTests(unittest.TestCase):
    def test_search_ranks_deduplicates_and_persists_raw_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "scholarime-search-test"
            result = academic_search.run_search(
                "academic writing with language models",
                expanded_queries=["academic writing large language models"],
                sources=["openalex", "crossref"],
                limit=10,
                per_source=5,
                client=FakeClient(),
                run_dir=run_dir,
            )
            self.assertEqual(result.references[0].doi, "10.1000/test.1")
            self.assertGreater(result.references[0].relevance_score or 0, 0)
            self.assertTrue(result.database_path.is_file())
            self.assertTrue(result.candidates_path.is_file())
            self.assertEqual(len(result.references), 2)
            connection = academic_search.sqlite3.connect(result.database_path)
            try:
                count = connection.execute("SELECT COUNT(*) FROM record").fetchone()[0]
                raw = connection.execute("SELECT raw_json FROM record LIMIT 1").fetchone()[0]
            finally:
                connection.close()
            self.assertGreater(count, 0)
            self.assertIn("display_name", raw + connection_text(result.database_path))

    def test_semantic_scholar_requires_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(academic_search.SearchError, "no API key configured"):
                academic_search.run_search(
                    "retrieval",
                    sources=["semantic"],
                    semantic_key="",
                    client=FakeClient(),
                    run_dir=Path(temporary) / "scholarime-search-test",
                )

    def test_semantic_scholar_sends_key_header(self) -> None:
        client = FakeClient()
        hits = academic_search.search_semantic("semantic retrieval", 5, client, "secret-key")
        self.assertEqual(client.headers["x-api-key"], "secret-key")
        self.assertEqual(hits[0].reference.doi, "10.1000/semantic")

    def test_crossref_request_omits_select_and_uses_polite_email(self) -> None:
        client = FakeClient()
        client.contact_email = "researcher@example.org"
        hits = academic_search.search_crossref("academic writing", 5, client)
        self.assertTrue(hits)
        parsed = academic_search.urllib.parse.parse_qs(
            academic_search.urllib.parse.urlsplit(client.urls[-1]).query
        )
        self.assertNotIn("select", parsed)
        self.assertEqual(parsed["mailto"], ["researcher@example.org"])

    def test_pubmed_request_includes_ncbi_identity(self) -> None:
        client = FakeClient()
        client.contact_email = "researcher@example.org"
        client.ncbi_api_key = "ncbi-key"
        hits = academic_search.search_pubmed("academic writing", 5, client)
        self.assertEqual(hits[0].reference.doi, "10.1000/pubmed")
        first_query = academic_search.urllib.parse.parse_qs(
            academic_search.urllib.parse.urlsplit(client.urls[0]).query
        )
        self.assertEqual(first_query["tool"], ["Scholarime"])
        self.assertEqual(first_query["email"], ["researcher@example.org"])
        self.assertEqual(first_query["api_key"], ["ncbi-key"])

    def test_arxiv_expands_phrase_to_and_query(self) -> None:
        client = FakeClient()
        hits = academic_search.search_arxiv(
            "large language models academic writing",
            5,
            client,
        )
        self.assertEqual(hits[0].reference.source_id, "2501.00001")
        parsed = academic_search.urllib.parse.parse_qs(
            academic_search.urllib.parse.urlsplit(client.urls[0]).query
        )
        search_query = parsed["search_query"][0]
        self.assertIn("all:large AND all:language", search_query)

    def test_partial_source_results_are_reported(self) -> None:
        class PartialClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.openalex_calls = 0

            def get_json(self, url, headers=None):
                if "openalex.org/works" in url:
                    self.openalex_calls += 1
                    if self.openalex_calls == 2:
                        raise academic_search.SearchError(
                            "HTTP 429 after 4 attempts"
                        )
                return super().get_json(url, headers)

        with tempfile.TemporaryDirectory() as temporary:
            result = academic_search.run_search(
                "academic writing",
                expanded_queries=["academic writing models"],
                sources=["openalex"],
                client=PartialClient(),
                run_dir=Path(temporary) / "scholarime-search-partial",
            )
            self.assertEqual(result.sources[0].records, 2)
            self.assertEqual(len(result.sources[0].errors), 1)
            self.assertTrue(any("results may be incomplete" in item for item in result.warnings))
            summary = result.summary()
            self.assertEqual(summary["sources"][0]["status"], "partial")

    def test_http_client_retries_429(self) -> None:
        headers = Message()
        headers["Retry-After"] = "0"
        error = academic_search.urllib.error.HTTPError(
            "https://example.org",
            429,
            "Too Many Requests",
            headers,
            None,
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                return b"{}"

        sleeps = []
        client = academic_search.HttpClient(
            min_interval=0,
            max_attempts=2,
            base_delay=0,
            sleep_func=lambda seconds: sleeps.append(seconds),
        )
        with mock.patch(
            "academic_search.urllib.request.urlopen",
            side_effect=[error, Response()],
        ) as mocked:
            self.assertEqual(client._request("https://example.org"), b"{}")
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(sleeps), 1)


def connection_text(database_path: Path) -> str:
    connection = academic_search.sqlite3.connect(database_path)
    try:
        return "\n".join(
            row[0] for row in connection.execute("SELECT raw_json FROM record").fetchall()
        )
    finally:
        connection.close()


class CitationTests(unittest.TestCase):
    def test_citation_formats(self) -> None:
        reference = scholar_common.Reference(
            title="Academic search with Python",
            source="test",
            authors=["Ada Reader", "Lin Scholar"],
            year=2025,
            container="Journal of Search",
            volume="4",
            issue="2",
            pages="10-22",
            doi="10.1000/cite",
            item_type="article",
        )
        apa = scholar_common.format_citations("apa", [reference])
        gbt = scholar_common.format_citations("gb-t-7714-2025", [reference])
        self.assertIn("Reader, A., & Scholar, L.", apa["references"][0])
        self.assertIn("https://doi.org/10.1000/cite", apa["references"][0])
        self.assertIn("[J]", gbt["references"][0])
        self.assertEqual(gbt["in_text"], ["[1]"])


class ConfigTests(unittest.TestCase):
    def test_semantic_key_is_saved_in_config_override_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            with mock.patch.dict(
                os.environ,
                {"SCHOLARIME_CONFIG_PATH": str(path), "SEMANTIC_SCHOLAR_API_KEY": ""},
            ):
                saved = scholar_config.save_semantic_scholar_key("test-key-1234")
                self.assertEqual(saved, path.resolve())
                self.assertEqual(scholar_config.semantic_scholar_key(), "test-key-1234")
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class ReviewBackendTests(unittest.TestCase):
    def test_web_backend_import_preview_and_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = scholar_common.Reference(
                title="Initial candidate",
                source="OpenAlex",
                authors=["Ada Reader"],
                year=2025,
                item_type="article",
            )
            state = reference_review.ReviewState(
                references=[candidate],
                output_path=root / "final.json",
                skill_root=SKILL_ROOT,
            )
            try:
                server = reference_review.ReviewServer(("127.0.0.1", 0), state)
            except PermissionError:
                self.skipTest("Local loopback sockets are blocked by this sandbox.")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                request = urllib.request.Request(
                    base + "/api/state",
                    headers={"X-Scholarime-Token": state.token},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["references"][0]["title"], "Initial candidate")

                ris = """TY  - JOUR
TI  - Imported source
AU  - Lin Scholar
PY  - 2024
ER  -
"""
                self._post(
                    base + "/api/import",
                    state.token,
                    {"filename": "import.ris", "content": ris},
                )
                self.assertEqual(len(state.references), 2)

                references = [reference.to_dict() for reference in reversed(state.references)]
                self._post(base + "/api/order", state.token, {"references": references})
                preview = self._post(
                    base + "/api/preview",
                    state.token,
                    {"style": "apa"},
                )
                self.assertEqual(len(preview["references"]), 2)
                final = self._post(
                    base + "/api/confirm",
                    state.token,
                    {"references": references, "citation_style": "gb-t-7714-2025"},
                )
                self.assertEqual(final["citation_style"], "gb-t-7714-2025")
                self.assertTrue((root / "final.json").is_file())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_review_state_formats_and_writes_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "final.json"
            state = reference_review.ReviewState(
                references=[
                    scholar_common.Reference(
                        title="Reviewed source",
                        source="Manual entry",
                        authors=["Ada Reader"],
                        year=2025,
                        item_type="webpage",
                        url="https://example.org/source",
                    )
                ],
                output_path=output,
                skill_root=SKILL_ROOT,
            )
            final = state.confirm(state.references, "apa")
            self.assertTrue(state.done_event.is_set())
            self.assertEqual(final["citation_style"], "apa")
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["references"][0]["title"], "Reviewed source")

    def _post(self, url: str, token: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Scholarime-Token": token,
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))


class InstallerTests(unittest.TestCase):
    def test_no_key_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ)
            env["SCHOLARIME_CONFIG_PATH"] = str(Path(temporary) / "config.json")
            env.pop("SEMANTIC_SCHOLAR_API_KEY", None)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SKILL_ROOT / "install" / "configure_semantic_scholar.py"),
                    "--no-key",
                ],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Semantic Scholar search is unavailable", result.stdout)



if __name__ == "__main__":
    unittest.main()
