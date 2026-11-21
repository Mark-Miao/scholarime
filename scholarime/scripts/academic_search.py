#!/usr/bin/env python3
"""Search open scholarly sources and persist review candidates in a temporary SQLite run."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scholar_common import Reference, clean_text, deduplicate_references, rank_references, utc_now
from scholar_config import semantic_scholar_key


VERSION = "1.0.0"
OPEN_SOURCES = ("openalex", "crossref", "arxiv", "pubmed")
ALL_SOURCES = (*OPEN_SOURCES, "semantic")
USER_AGENT = f"Scholarime/{VERSION} (academic search tool)"
API_TIMEOUT = 30
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class SearchError(RuntimeError):
    """A user-correctable search failure."""


@dataclass
class SearchHit:
    reference: Reference
    raw: Mapping[str, Any]


@dataclass
class SourceReport:
    source: str
    available: bool
    queries: int = 0
    records: int = 0
    error: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.available:
            return "unavailable"
        if self.records and self.errors:
            return "partial"
        if self.errors:
            return "failed"
        return "ok"


@dataclass
class SearchResult:
    run_id: str
    run_dir: Path
    database_path: Path
    candidates_path: Path
    topic: str
    queries: list[str]
    references: list[Reference]
    sources: list[SourceReport]
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "topic": self.topic,
            "queries": self.queries,
            "database": str(self.database_path),
            "candidates": str(self.candidates_path),
            "created_at": utc_now(),
            "sources": [
                {
                    "source": report.source,
                    "available": report.available,
                    "queries": report.queries,
                    "records": report.records,
                    "error": report.error,
                    "errors": report.errors,
                    "status": report.status,
                }
                for report in self.sources
            ],
            "warnings": self.warnings,
            "results": [reference.to_dict() for reference in self.references],
        }


class HttpClient:
    def __init__(
        self,
        contact_email: str = "",
        ncbi_api_key: str = "",
        min_interval: float = 0.5,
        max_attempts: int = 4,
        base_delay: float = 1.0,
        sleep_func=time.sleep,
    ) -> None:
        self.contact_email = contact_email.strip()
        self.ncbi_api_key = ncbi_api_key.strip()
        self.min_interval = max(0.0, min_interval)
        self.max_attempts = max(1, max_attempts)
        self.base_delay = max(0.0, base_delay)
        self.sleep_func = sleep_func
        self.last_request_at = 0.0

    def _request(self, url: str, headers: Mapping[str, str] | None = None) -> bytes:
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/atom+xml, application/xml, text/xml",
        }
        if self.contact_email:
            request_headers["From"] = self.contact_email
        if headers:
            request_headers.update(headers)
        for attempt in range(self.max_attempts):
            elapsed = time.monotonic() - self.last_request_at
            if elapsed < self.min_interval:
                self.sleep_func(self.min_interval - elapsed)
            self.last_request_at = time.monotonic()
            request = urllib.request.Request(url, headers=request_headers)
            try:
                with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
                    data = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise SearchError("Source response exceeded the 20 MiB limit.")
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504}:
                    exc.close()
                    raise SearchError(
                        f"HTTP {exc.code} {exc.reason} for {urllib.parse.urlsplit(url).netloc}"
                    ) from exc
                if attempt + 1 >= self.max_attempts:
                    exc.close()
                    raise SearchError(
                        f"HTTP {exc.code} after {self.max_attempts} attempts"
                    ) from exc
                retry_after = self._retry_after_seconds(exc.headers.get("Retry-After"))
                exc.close()
                delay = max(retry_after, self.base_delay * (2**attempt))
                self.sleep_func(delay + random.uniform(0, 0.25))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt + 1 >= self.max_attempts:
                    raise SearchError(
                        f"Network failure after {self.max_attempts} attempts: {exc}"
                    ) from exc
                delay = self.base_delay * (2**attempt)
                self.sleep_func(delay + random.uniform(0, 0.25))
        raise SearchError("Request failed.")

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return max(0.0, min(30.0, float(value)))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                now = datetime.now(timezone.utc)
                return max(0.0, min(30.0, (target - now).total_seconds()))
            except (TypeError, ValueError):
                return 0.0

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        data = self._request(url, headers)
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SearchError(f"Source returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SearchError("Source returned an unexpected JSON value.")
        return parsed

    def get_text(self, url: str, headers: Mapping[str, str] | None = None) -> str:
        data = self._request(url, headers)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SearchError(f"Source returned invalid UTF-8 text: {exc}") from exc


def _source_id(value: Any) -> str:
    text = clean_text(value)
    return text.rsplit("/", 1)[-1] if "/" in text else text


def _openalex_abstract(inverted: Any) -> str:
    if not isinstance(inverted, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if isinstance(positions, list):
            positioned.extend((int(position), str(word)) for position in positions)
    return " ".join(word for _, word in sorted(positioned))


def search_openalex(
    query: str,
    limit: int,
    client: HttpClient,
) -> list[SearchHit]:
    parameters: dict[str, Any] = {"search": query, "per-page": limit}
    contact_email = clean_text(getattr(client, "contact_email", ""))
    if contact_email:
        parameters["mailto"] = contact_email
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(parameters)
    payload = client.get_json(url)
    hits: list[SearchHit] = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        authors = [
            clean_text((authorship.get("author") or {}).get("display_name"))
            for authorship in item.get("authorships", [])
            if isinstance(authorship, dict)
        ]
        location = item.get("primary_location") or {}
        source = (location.get("source") or {}) if isinstance(location, dict) else {}
        best_oa = item.get("best_oa_location") or {}
        reference = Reference(
            title=clean_text(item.get("display_name")),
            source="OpenAlex",
            source_id=_source_id(item.get("id")),
            authors=[author for author in authors if author],
            year=item.get("publication_year"),
            container=clean_text(source.get("display_name")),
            doi=clean_text(item.get("doi")),
            url=clean_text(item.get("id")),
            pdf_url=clean_text(best_oa.get("pdf_url")) if isinstance(best_oa, dict) else "",
            abstract=_openalex_abstract(item.get("abstract_inverted_index")),
            keywords=[
                clean_text(concept.get("display_name"))
                for concept in item.get("concepts", [])[:8]
                if isinstance(concept, dict)
            ],
            item_type=clean_text(item.get("type")) or "article",
            language=clean_text(item.get("language")),
            citation_count=item.get("cited_by_count"),
        )
        if reference.title:
            hits.append(SearchHit(reference, item))
    return hits


def _crossref_author(author: Mapping[str, Any]) -> str:
    given = clean_text(author.get("given"))
    family = clean_text(author.get("family"))
    if given and family:
        return f"{given} {family}"
    return clean_text(author.get("name"))


def _first_path(payload: Mapping[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def search_crossref(
    query: str,
    limit: int,
    client: HttpClient,
) -> list[SearchHit]:
    parameters: dict[str, Any] = {
        "query": query,
        "rows": limit,
    }
    contact_email = clean_text(getattr(client, "contact_email", ""))
    if contact_email:
        parameters["mailto"] = contact_email
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(parameters)
    payload = client.get_json(url)
    hits: list[SearchHit] = []
    message = payload.get("message") or {}
    for item in message.get("items", []):
        if not isinstance(item, dict):
            continue
        titles = item.get("title") or []
        title = clean_text(titles[0] if titles else "")
        containers = item.get("container-title") or []
        container = clean_text(containers[0] if containers else "")
        authors = [
            _crossref_author(author)
            for author in item.get("author", [])
            if isinstance(author, dict)
        ]
        year = None
        for date_key in ("published", "published-print", "published-online"):
            date_parts = _first_path(item, date_key, "date-parts")
            if date_parts and isinstance(date_parts, list) and date_parts[0]:
                year = date_parts[0][0]
                break
        reference = Reference(
            title=title,
            source="Crossref",
            source_id=clean_text(item.get("DOI")),
            authors=[author for author in authors if author],
            year=year,
            container=container,
            volume=clean_text(item.get("volume")),
            issue=clean_text(item.get("issue")),
            pages=clean_text(item.get("page")),
            doi=clean_text(item.get("DOI")),
            url=clean_text(item.get("URL")),
            abstract=clean_text(item.get("abstract")),
            item_type=clean_text(item.get("type")) or "article",
            publisher=clean_text(item.get("publisher")),
            language=clean_text(item.get("language")),
            citation_count=item.get("is-referenced-by-count"),
        )
        if reference.title:
            hits.append(SearchHit(reference, item))
    return hits


def search_arxiv(
    query: str,
    limit: int,
    client: HttpClient,
) -> list[SearchHit]:
    terms = [
        term
        for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", query)
        if len(term) > 1
    ][:8]
    search_query = (
        " AND ".join(f"all:{term}" for term in terms)
        if len(terms) >= 2
        else f'all:"{query}"'
    )
    parameters = {
        "search_query": search_query,
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(parameters)
    text = client.get_text(url)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SearchError(f"arXiv returned invalid XML: {exc}") from exc
    namespace = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    hits: list[SearchHit] = []
    for entry in root.findall("atom:entry", namespace):
        title = clean_text(entry.findtext("atom:title", default="", namespaces=namespace))
        authors = [
            clean_text(author.findtext("atom:name", default="", namespaces=namespace))
            for author in entry.findall("atom:author", namespace)
        ]
        entry_id = clean_text(entry.findtext("atom:id", default="", namespaces=namespace))
        pdf_url = ""
        for link in entry.findall("atom:link", namespace):
            if link.attrib.get("title") == "pdf":
                pdf_url = clean_text(link.attrib.get("href"))
        published = clean_text(entry.findtext("atom:published", default="", namespaces=namespace))
        categories = [
            clean_text(category.attrib.get("term"))
            for category in entry.findall("atom:category", namespace)
        ]
        reference = Reference(
            title=title,
            source="arXiv",
            source_id=_source_id(entry_id),
            authors=authors,
            year=published[:4],
            container="arXiv",
            url=entry_id,
            pdf_url=pdf_url,
            abstract=clean_text(entry.findtext("atom:summary", default="", namespaces=namespace)),
            keywords=categories,
            item_type="preprint",
            language="en",
        )
        if reference.title:
            hits.append(
                SearchHit(
                    reference,
                    {
                        "id": entry_id,
                        "title": title,
                        "published": published,
                        "summary": reference.abstract,
                    },
                )
            )
    return hits


def _iter_text(element: ET.Element) -> str:
    return clean_text("".join(element.itertext()))


def search_pubmed(
    query: str,
    limit: int,
    client: HttpClient,
) -> list[SearchHit]:
    common_params: dict[str, Any] = {"tool": "Scholarime"}
    if clean_text(getattr(client, "contact_email", "")):
        common_params["email"] = client.contact_email
    if clean_text(getattr(client, "ncbi_api_key", "")):
        common_params["api_key"] = client.ncbi_api_key
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": limit,
        "sort": "relevance",
        **common_params,
    }
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
        search_params
    )
    search_payload = client.get_json(search_url)
    ids = (search_payload.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml",
        **common_params,
    }
    fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(
        fetch_params
    )
    text = client.get_text(fetch_url)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SearchError(f"PubMed returned invalid XML: {exc}") from exc
    hits: list[SearchHit] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = clean_text(article.findtext(".//PMID", default=""))
        title = _iter_text(article.find(".//ArticleTitle")) if article.find(".//ArticleTitle") is not None else ""
        authors: list[str] = []
        for author in article.findall(".//Author"):
            given = clean_text(author.findtext("ForeName", default=""))
            family = clean_text(author.findtext("LastName", default=""))
            collective = clean_text(author.findtext("CollectiveName", default=""))
            name = collective or " ".join(part for part in (given, family) if part)
            if name:
                authors.append(name)
        abstract = " ".join(
            _iter_text(part)
            for part in article.findall(".//Abstract/AbstractText")
            if _iter_text(part)
        )
        journal = clean_text(article.findtext(".//Journal/Title", default=""))
        year = clean_text(article.findtext(".//JournalIssue/PubDate/Year", default=""))
        volume = clean_text(article.findtext(".//JournalIssue/Volume", default=""))
        issue = clean_text(article.findtext(".//JournalIssue/Issue", default=""))
        pages = clean_text(article.findtext(".//Pagination/MedlinePgn", default=""))
        doi = ""
        for identifier in article.findall(".//ArticleId"):
            if identifier.attrib.get("IdType") == "doi":
                doi = clean_text(identifier.text)
                break
        reference = Reference(
            title=title,
            source="PubMed",
            source_id=pmid,
            authors=authors,
            year=year,
            container=journal,
            volume=volume,
            issue=issue,
            pages=pages,
            doi=doi,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            abstract=abstract,
            item_type="article",
            language="en",
        )
        if reference.title:
            hits.append(
                SearchHit(
                    reference,
                    {
                        "pmid": pmid,
                        "title": title,
                        "journal": journal,
                        "year": year,
                        "doi": doi,
                    },
                )
            )
    return hits


def search_semantic(
    query: str,
    limit: int,
    client: HttpClient,
    api_key: str,
) -> list[SearchHit]:
    if not api_key:
        raise SearchError("Semantic Scholar search unavailable: no API key configured.")
    parameters = {
        "query": query,
        "limit": limit,
        "fields": (
            "title,authors,year,venue,abstract,externalIds,url,openAccessPdf,"
            "publicationTypes,citationCount,publicationDate"
        ),
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(
        parameters
    )
    payload = client.get_json(url, headers={"x-api-key": api_key})
    hits: list[SearchHit] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        external = item.get("externalIds") or {}
        open_pdf = item.get("openAccessPdf") or {}
        publication_types = item.get("publicationTypes") or []
        reference = Reference(
            title=clean_text(item.get("title")),
            source="Semantic Scholar",
            source_id=clean_text(item.get("paperId")),
            authors=[
                clean_text(author.get("name"))
                for author in item.get("authors", [])
                if isinstance(author, dict)
            ],
            year=item.get("year"),
            container=clean_text(item.get("venue")),
            doi=clean_text(external.get("DOI")) if isinstance(external, dict) else "",
            url=clean_text(item.get("url")),
            pdf_url=clean_text(open_pdf.get("url")) if isinstance(open_pdf, dict) else "",
            abstract=clean_text(item.get("abstract")),
            item_type=(
                clean_text(publication_types[0]).casefold()
                if publication_types
                else "article"
            ),
            citation_count=item.get("citationCount"),
        )
        if reference.title:
            hits.append(SearchHit(reference, item))
    return hits


SOURCE_FUNCTIONS: dict[
    str,
    Callable[[str, int, HttpClient], list[SearchHit]],
] = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "arxiv": search_arxiv,
    "pubmed": search_pubmed,
}


def _parse_sources(value: str) -> list[str]:
    requested = [item.strip().casefold() for item in value.split(",") if item.strip()]
    if not requested:
        raise SearchError("At least one source is required.")
    if "all" in requested:
        requested = list(ALL_SOURCES)
    unknown = [source for source in requested if source not in ALL_SOURCES]
    if unknown:
        raise SearchError(f"Unknown source(s): {', '.join(unknown)}")
    return list(dict.fromkeys(requested))


def _write_database(
    database_path: Path,
    run_id: str,
    topic: str,
    queries: Sequence[str],
    hit_groups: Sequence[tuple[str, str, Sequence[SearchHit], str]],
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE run (
                run_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE query_attempt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                query TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                error TEXT NOT NULL
            );
            CREATE TABLE record (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                doi TEXT NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                retrieved_at TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                raw_json TEXT NOT NULL
            );
            """
        )
        created_at = utc_now()
        connection.execute(
            "INSERT INTO run(run_id, topic, created_at) VALUES (?, ?, ?)",
            (run_id, topic, created_at),
        )
        for source, query, hits, error in hit_groups:
            connection.execute(
                """
                INSERT INTO query_attempt(run_id, source, query, record_count, error)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, source, query, len(hits), error),
            )
            for hit in hits:
                reference = hit.reference
                connection.execute(
                    """
                    INSERT INTO record(
                        run_id, source, source_id, doi, title, year, retrieved_at,
                        normalized_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        reference.source,
                        reference.source_id,
                        reference.doi,
                        reference.title,
                        reference.year,
                        reference.retrieved_at,
                        json.dumps(reference.to_dict(), ensure_ascii=False),
                        json.dumps(hit.raw, ensure_ascii=False),
                    ),
                )
        connection.commit()
    finally:
        connection.close()


def run_search(
    topic: str,
    expanded_queries: Sequence[str] | None = None,
    sources: Sequence[str] | None = None,
    limit: int = 10,
    per_source: int = 20,
    contact_email: str = "",
    semantic_key: str = "",
    client: HttpClient | None = None,
    run_dir: Path | None = None,
) -> SearchResult:
    topic = clean_text(topic)
    if not topic:
        raise SearchError("A non-empty topic is required.")
    queries = [topic]
    for query in expanded_queries or []:
        cleaned = clean_text(query)
        if cleaned and cleaned not in queries:
            queries.append(cleaned)
    requested_sources = list(sources or ALL_SOURCES)
    unknown = [source for source in requested_sources if source not in ALL_SOURCES]
    if unknown:
        raise SearchError(f"Unknown source(s): {', '.join(unknown)}")
    if limit < 1 or per_source < 1:
        raise SearchError("limit and per_source must be positive.")

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(3).hex()
    directory = (run_dir or Path(tempfile.mkdtemp(prefix="scholarime-search-"))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    database_path = directory / "sources.sqlite3"
    candidates_path = directory / "candidates.json"
    client = client or HttpClient(
        contact_email,
        ncbi_api_key=os.environ.get("NCBI_API_KEY", ""),
    )
    reports: list[SourceReport] = []
    warnings: list[str] = []
    hit_groups: list[tuple[str, str, Sequence[SearchHit], str]] = []
    all_hits: list[SearchHit] = []

    for source in requested_sources:
        if source == "semantic" and not semantic_key:
            message = "Semantic Scholar search unavailable: no API key configured."
            warnings.append(message)
            reports.append(SourceReport(source=source, available=False, error=message))
            continue
        report = SourceReport(source=source, available=True)
        source_hits: list[SearchHit] = []
        for query in queries:
            report.queries += 1
            try:
                if source == "semantic":
                    hits = search_semantic(query, per_source, client, semantic_key)
                else:
                    hits = SOURCE_FUNCTIONS[source](query, per_source, client)
                source_hits.extend(hits)
                report.records += len(hits)
                hit_groups.append((source, query, hits, ""))
            except Exception as exc:
                error = clean_text(str(exc)) or exc.__class__.__name__
                report.error = error
                report.errors.append(f"{query}: {error}")
                hit_groups.append((source, query, [], error))
        if report.errors:
            warnings.append(
                f"{source}: {len(report.errors)} query attempt(s) failed; "
                "results may be incomplete."
            )
        all_hits.extend(source_hits)
        reports.append(report)

    if not all_hits:
        errors = "; ".join(
            f"{report.source}: {report.error}" for report in reports if report.error
        )
        details = "; ".join(part for part in (errors, *warnings) if part)
        raise SearchError(f"No references were retrieved. {details}".strip())

    ranked = rank_references((hit.reference for hit in all_hits), queries, limit=limit)
    _write_database(database_path, run_id, topic, queries, hit_groups)
    result = SearchResult(
        run_id=run_id,
        run_dir=directory,
        database_path=database_path,
        candidates_path=candidates_path,
        topic=topic,
        queries=queries,
        references=ranked,
        sources=reports,
        warnings=warnings,
    )
    candidates_path.write_text(
        json.dumps(result.summary(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def cleanup_run(run_dir: Path | str) -> None:
    directory = Path(run_dir).expanduser().resolve()
    if not directory.name.startswith("scholarime-search-"):
        raise SearchError("Refusing to remove a directory outside the Scholarime run prefix.")
    if directory.is_dir():
        shutil.rmtree(directory)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="academic_search.py",
        description="Search open academic sources and rank a temporary review set.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python academic_search.py search 'retrieval augmented generation'\n"
            "  python academic_search.py search '大语言模型 学术写作' \\\n"
            "    --query 'large language models academic writing' --sources all --limit 10\n"
            "  python academic_search.py cleanup /tmp/scholarime-search-abc123\n\n"
            "Semantic Scholar is skipped with a warning when no API key is configured.\n"
            "Set SCHOLARIME_CONTACT_EMAIL for polite OpenAlex and Crossref requests.\n"
            "Set NCBI_API_KEY to increase PubMed E-utilities limits."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    search_parser = subparsers.add_parser("search", help="Run a new literature search.")
    search_parser.add_argument("topic", help="Research topic or primary query.")
    search_parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Additional translated or expanded query; may be repeated.",
    )
    search_parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated sources: openalex,crossref,arxiv,pubmed,semantic,all.",
    )
    search_parser.add_argument("--limit", type=int, default=10, help="Final ranked result count.")
    search_parser.add_argument(
        "--per-source",
        type=int,
        default=20,
        help="Maximum records requested per source and query.",
    )
    search_parser.add_argument("--run-dir", type=Path, help="Explicit temporary run directory.")
    search_parser.add_argument(
        "--contact-email",
        default=os.environ.get("SCHOLARIME_CONTACT_EMAIL", ""),
        help="Polite-pool email for OpenAlex and Crossref.",
    )
    cleanup_parser = subparsers.add_parser("cleanup", help="Remove an exact Scholarime run directory.")
    cleanup_parser.add_argument("run_dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "cleanup":
            cleanup_run(args.run_dir)
            print(f"Removed search run: {Path(args.run_dir).expanduser().resolve()}")
            return 0
        result = run_search(
            topic=args.topic,
            expanded_queries=args.query,
            sources=_parse_sources(args.sources),
            limit=args.limit,
            per_source=args.per_source,
            contact_email=args.contact_email,
            semantic_key=semantic_scholar_key(),
            run_dir=args.run_dir,
        )
    except (SearchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Search run: {result.run_id}")
    print(f"Source database: {result.database_path}")
    print(f"Ranked candidates: {result.candidates_path}")
    print(f"Results: {len(result.references)}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for report in result.sources:
        if report.status == "partial":
            status = f"partial; {len(report.errors)} query attempt(s) failed"
        elif report.status == "ok":
            status = "ok"
        elif report.status == "failed":
            status = f"failed: {report.error}"
        else:
            status = report.error or "unavailable"
        print(f"- {report.source}: {report.records} records ({status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
