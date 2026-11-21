"""Shared reference normalization, deduplication, ranking, and citation helpers."""

from __future__ import annotations

import hashlib
import html
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


VERSION = "1.0.0"
CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
YEAR_RE = re.compile(r"(?<!\d)(1[5-9]\d{2}|20\d{2}|21\d{2})(?!\d)")
DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)", re.IGNORECASE)
PUNCT_RE = re.compile(r"[^\w\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", re.UNICODE)
TAG_RE = re.compile(r"<[^>]+>")

ENGLISH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "to",
    "using",
    "via",
    "with",
}
CHINESE_STOP_CHARS = set("的了和与及在中对为是等一种研究")
ITEM_TYPE_ALIASES = {
    "article": "article",
    "journal": "article",
    "journal-article": "article",
    "journalarticle": "article",
    "journal_article": "article",
    "review": "article",
    "conference": "conference",
    "conference-paper": "conference",
    "proceedings": "conference",
    "proceedings-article": "conference",
    "proceedingsarticle": "conference",
    "book": "book",
    "book-chapter": "book",
    "bookchapter": "book",
    "manual": "manual",
    "report": "report",
    "techreport": "report",
    "thesis": "thesis",
    "dissertation": "thesis",
    "preprint": "preprint",
    "posted-content": "preprint",
    "webpage": "webpage",
    "web": "webpage",
    "website": "webpage",
    "online": "webpage",
}

CITATION_ASSETS = {
    "apa": Path("assets/citation-styles/apa.csl"),
    "gb-t-7714-2025": Path("assets/citation-styles/gbt7714-2025-numeric.csl"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalise_doi(value: Any) -> str:
    match = DOI_RE.search(str(value or "").strip())
    return match.group(1).rstrip(".,;)").casefold() if match else ""


def parse_year(value: Any) -> int | None:
    if isinstance(value, int) and 1500 <= value <= 2199:
        return value
    match = YEAR_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def normalise_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    return PUNCT_RE.sub(" ", text).strip()


def _stable_id(source: str, source_id: str, title: str, year: int | None) -> str:
    payload = "|".join((source, source_id, normalise_title(title), str(year or "")))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class Reference:
    title: str
    source: str
    source_id: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    container: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    url: str = ""
    pdf_url: str = ""
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    item_type: str = "article"
    publisher: str = ""
    language: str = ""
    citation_count: int | None = None
    retrieved_at: str = field(default_factory=utc_now)
    origins: list[str] = field(default_factory=list)
    relevance_score: float | None = None
    relevance_reason: str = ""
    record_id: str = ""

    def __post_init__(self) -> None:
        self.title = clean_text(self.title)
        self.source = clean_text(self.source) or "unknown"
        self.source_id = clean_text(self.source_id)
        self.year = parse_year(self.year)
        self.authors = [clean_text(author) for author in self.authors if clean_text(author)]
        self.container = clean_text(self.container)
        self.volume = clean_text(self.volume)
        self.issue = clean_text(self.issue)
        self.pages = clean_text(self.pages)
        self.doi = normalise_doi(self.doi)
        self.url = clean_text(self.url)
        self.pdf_url = clean_text(self.pdf_url)
        self.abstract = clean_text(self.abstract)
        self.keywords = [clean_text(keyword) for keyword in self.keywords if clean_text(keyword)]
        raw_type = clean_text(self.item_type).casefold() or "other"
        self.item_type = ITEM_TYPE_ALIASES.get(raw_type, raw_type)
        self.publisher = clean_text(self.publisher)
        self.language = clean_text(self.language)
        if not self.origins:
            self.origins = [self.source]
        if not self.record_id:
            self.record_id = _stable_id(self.source, self.source_id, self.title, self.year)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], default_source: str = "unknown") -> "Reference":
        authors = data.get("authors") or []
        if isinstance(authors, str):
            authors = [part.strip() for part in re.split(r"\s*;\s*", authors) if part.strip()]
        keywords = data.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [part.strip() for part in keywords.split(";") if part.strip()]
        return cls(
            title=clean_text(data.get("title")),
            source=clean_text(data.get("source")) or default_source,
            source_id=clean_text(data.get("source_id")),
            authors=list(authors),
            year=parse_year(data.get("year")),
            container=clean_text(data.get("container")),
            volume=clean_text(data.get("volume")),
            issue=clean_text(data.get("issue")),
            pages=clean_text(data.get("pages")),
            doi=clean_text(data.get("doi")),
            url=clean_text(data.get("url")),
            pdf_url=clean_text(data.get("pdf_url")),
            abstract=clean_text(data.get("abstract")),
            keywords=list(keywords),
            item_type=clean_text(data.get("item_type")) or "other",
            publisher=clean_text(data.get("publisher")),
            language=clean_text(data.get("language")),
            citation_count=(
                int(data["citation_count"])
                if str(data.get("citation_count", "")).strip().isdigit()
                else None
            ),
            retrieved_at=clean_text(data.get("retrieved_at")) or utc_now(),
            origins=list(data.get("origins") or []),
            relevance_score=(
                float(data["relevance_score"])
                if data.get("relevance_score") is not None
                else None
            ),
            relevance_reason=clean_text(data.get("relevance_reason")),
            record_id=clean_text(data.get("record_id")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "title": self.title,
            "source": self.source,
            "source_id": self.source_id,
            "origins": list(self.origins),
            "authors": list(self.authors),
            "year": self.year,
            "container": self.container,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "doi": self.doi,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "abstract": self.abstract,
            "keywords": list(self.keywords),
            "item_type": self.item_type,
            "publisher": self.publisher,
            "language": self.language,
            "citation_count": self.citation_count,
            "retrieved_at": self.retrieved_at,
            "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason,
        }


def _author_overlap(left: Reference, right: Reference) -> bool:
    left_names = {name.casefold() for name in left.authors}
    right_names = {name.casefold() for name in right.authors}
    return bool(left_names & right_names)


def references_match(left: Reference, right: Reference) -> bool:
    if left.doi and right.doi:
        return left.doi == right.doi
    left_title = normalise_title(left.title)
    right_title = normalise_title(right.title)
    if not left_title or not right_title:
        return False
    if left_title == right_title:
        return True
    if left.year and right.year and abs(left.year - right.year) > 1:
        return False
    ratio = SequenceMatcher(None, left_title, right_title).ratio()
    return ratio >= 0.96 and (_author_overlap(left, right) or ratio >= 0.985)


def merge_references(primary: Reference, secondary: Reference) -> Reference:
    def first(current: str, incoming: str) -> str:
        return current or incoming

    primary.source_id = first(primary.source_id, secondary.source_id)
    primary.year = primary.year or secondary.year
    primary.container = first(primary.container, secondary.container)
    primary.volume = first(primary.volume, secondary.volume)
    primary.issue = first(primary.issue, secondary.issue)
    primary.pages = first(primary.pages, secondary.pages)
    primary.doi = first(primary.doi, secondary.doi)
    primary.url = first(primary.url, secondary.url)
    primary.pdf_url = first(primary.pdf_url, secondary.pdf_url)
    if len(secondary.abstract) > len(primary.abstract):
        primary.abstract = secondary.abstract
    primary.publisher = first(primary.publisher, secondary.publisher)
    primary.language = first(primary.language, secondary.language)
    if primary.citation_count is None:
        primary.citation_count = secondary.citation_count
    for origin in secondary.origins:
        if origin not in primary.origins:
            primary.origins.append(origin)
    for author in secondary.authors:
        if author not in primary.authors:
            primary.authors.append(author)
    for keyword in secondary.keywords:
        if keyword not in primary.keywords:
            primary.keywords.append(keyword)
    return primary


def deduplicate_references(references: Iterable[Reference]) -> list[Reference]:
    unique: list[Reference] = []
    for reference in references:
        duplicate = next(
            (existing for existing in unique if references_match(existing, reference)),
            None,
        )
        if duplicate:
            merge_references(duplicate, reference)
        else:
            unique.append(reference)
    return unique


def _query_tokens(query: str) -> list[str]:
    normalised = unicodedata.normalize("NFKC", query).casefold()
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]{2,}", normalised):
        if token.isascii():
            if token not in ENGLISH_STOPWORDS and len(token) > 1:
                tokens.append(token)
            continue
        tokens.append(token)
        tokens.extend(
            token[index : index + 2]
            for index in range(len(token) - 1)
            if token[index] not in CHINESE_STOP_CHARS
        )
    return list(dict.fromkeys(tokens))


def score_reference(reference: Reference, queries: Sequence[str]) -> tuple[float, str]:
    title = normalise_title(reference.title)
    body = normalise_title(
        " ".join([reference.abstract, " ".join(reference.keywords), reference.container])
    )
    query_tokens: list[str] = []
    for query in queries:
        query_tokens.extend(_query_tokens(query))
    query_tokens = list(dict.fromkeys(query_tokens))
    if not query_tokens:
        return 0.0, "no query terms"

    title_hits: list[str] = []
    body_hits: list[str] = []
    raw = 0.0
    for token in query_tokens:
        in_title = token in title
        in_body = token in body
        if in_title:
            title_hits.append(token)
            raw += 4.0
        elif in_body:
            body_hits.append(token)
            raw += 1.25
    for query in queries:
        phrase = normalise_title(query)
        if phrase and phrase in title:
            raw += 6.0
        elif phrase and phrase in body:
            raw += 2.0
    if reference.citation_count:
        raw += min(1.0, math.log10(reference.citation_count + 1) / 5)
    normalised = round(100 * min(1.0, raw / max(1.0, len(query_tokens) * 4.0)), 2)
    reasons: list[str] = []
    if title_hits:
        reasons.append("title: " + ", ".join(title_hits[:5]))
    if body_hits:
        reasons.append("abstract/keywords: " + ", ".join(body_hits[:5]))
    return normalised, "; ".join(reasons) or "weak lexical match"


def rank_references(
    references: Iterable[Reference],
    queries: Sequence[str],
    limit: int = 10,
) -> list[Reference]:
    unique = deduplicate_references(references)
    for reference in unique:
        score, reason = score_reference(reference, queries)
        reference.relevance_score = score
        reference.relevance_reason = reason
    unique.sort(
        key=lambda item: (
            -(item.relevance_score or 0.0),
            -(item.year or 0),
            item.title.casefold(),
        )
    )
    return unique[: max(1, limit)]


def _is_cjk_author(name: str) -> bool:
    return bool(CJK_RE.search(name))


def _split_author(name: str) -> tuple[str, str]:
    name = clean_text(name)
    if "," in name:
        family, given = name.split(",", 1)
        return clean_text(family), clean_text(given)
    if _is_cjk_author(name):
        return name, ""
    parts = name.split()
    if len(parts) == 1:
        return name, ""
    return parts[-1], " ".join(parts[:-1])


def _initials(given: str) -> str:
    if not given:
        return ""
    pieces = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", given)
    return " ".join(f"{piece[0].upper()}." for piece in pieces if piece)


def _apa_author(name: str) -> str:
    if _is_cjk_author(name):
        return name
    family, given = _split_author(name)
    initials = _initials(given)
    return f"{family}, {initials}".rstrip(", ")


def _apa_authors(authors: Sequence[str]) -> str:
    formatted = [_apa_author(author) for author in authors if clean_text(author)]
    if not formatted:
        return ""
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    if len(formatted) > 20:
        formatted = formatted[:19] + ["…"] + formatted[-1:]
    return ", ".join(formatted[:-1]) + f", & {formatted[-1]}"


def _year_label(year: int | None) -> str:
    return str(year) if year else "n.d."


def _sentence_title(title: str) -> str:
    title = clean_text(title).rstrip(".")
    return title[:1].upper() + title[1:] if title and title.isascii() else title


def _doi_url(reference: Reference) -> str:
    return f"https://doi.org/{reference.doi}" if reference.doi else ""


def _apa_entry(reference: Reference) -> str:
    authors = _apa_authors(reference.authors)
    year = _year_label(reference.year)
    title = _sentence_title(reference.title)
    prefix = f"{authors} ({year}). {title}." if authors else f"{title}. ({year})."
    item_type = reference.item_type.casefold()
    if item_type in {"article", "journal", "conference", "proceedings"}:
        container = reference.container
        volume = reference.volume
        issue = f"({reference.issue})" if reference.issue else ""
        pages = reference.pages
        detail = container
        if volume:
            detail += f", {volume}{issue}"
        if pages:
            detail += f", {pages}"
        if detail:
            prefix += f" {detail}."
    elif item_type in {"book", "manual"}:
        if reference.publisher:
            prefix += f" {reference.publisher}."
    elif item_type in {"webpage", "web", "website"}:
        if reference.container:
            prefix += f" {reference.container}."
    if reference.url:
        prefix += f" {reference.url}"
    doi = _doi_url(reference)
    if doi:
        prefix += f" {doi}"
    return re.sub(r"\s+", " ", prefix).strip()


def _gbt_author(name: str) -> str:
    family, given = _split_author(name)
    if _is_cjk_author(name) or not family:
        return name
    return f"{family}, {given}".strip(", ")


def _gbt_type(reference: Reference) -> str:
    return {
        "article": "J",
        "journal": "J",
        "book": "M",
        "conference": "C",
        "proceedings": "C",
        "report": "R",
        "manual": "M",
        "thesis": "D",
        "webpage": "EB/OL",
        "web": "EB/OL",
        "website": "EB/OL",
        "preprint": "EB/OL",
    }.get(reference.item_type.casefold(), "Z")


def _gbt_entry(reference: Reference, index: int) -> str:
    authors = ", ".join(_gbt_author(author) for author in reference.authors)
    title = clean_text(reference.title).rstrip(".")
    type_code = _gbt_type(reference)
    parts = [
        f"[{index}]",
        f"{authors}." if authors else "",
        f"{title}[{type_code}].",
    ]
    item_type = reference.item_type.casefold()
    if item_type in {"article", "journal", "conference", "proceedings"}:
        container = reference.container
        info = container
        if reference.year:
            info += f", {reference.year}"
        if reference.volume:
            info += f", {reference.volume}"
        if reference.issue:
            info += f"({reference.issue})"
        if reference.pages:
            info += f": {reference.pages}"
        if info:
            parts.append(f"{info}.")
    elif item_type in {"book", "manual", "report"} and reference.publisher:
        gbt_year = str(reference.year) if reference.year else "出版年不详"
        parts.append(f"{reference.publisher}, {gbt_year}.")
    if reference.url:
        parts.append(reference.url)
    doi = _doi_url(reference)
    if doi:
        parts.append(doi)
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()


def _apa_in_text(reference: Reference) -> str:
    names = [_split_author(author)[0] for author in reference.authors if clean_text(author)]
    if not names:
        label = _sentence_title(reference.title)
    elif len(names) == 1:
        label = names[0]
    elif len(names) == 2:
        label = f"{names[0]} & {names[1]}"
    else:
        label = f"{names[0]} et al."
    return f"({label}, {_year_label(reference.year)})"


def format_citations(style: str, references: Sequence[Reference]) -> dict[str, Any]:
    style_key = style.casefold()
    if style_key in {"apa", "apa-7", "apa7"}:
        return {
            "style": "apa",
            "references": [_apa_entry(reference) for reference in references],
            "in_text": [_apa_in_text(reference) for reference in references],
        }
    if style_key in {"gb-t-7714-2025", "gbt7714", "gb/t 7714-2025"}:
        return {
            "style": "gb-t-7714-2025",
            "references": [
                _gbt_entry(reference, index) for index, reference in enumerate(references, 1)
            ],
            "in_text": [f"[{index}]" for index in range(1, len(references) + 1)],
        }
    raise ValueError("style must be apa or gb-t-7714-2025")


def citation_asset_status(skill_root: Path | str) -> dict[str, dict[str, Any]]:
    root = Path(skill_root)
    status: dict[str, dict[str, Any]] = {}
    for style, relative_path in CITATION_ASSETS.items():
        path = root / relative_path
        status[style] = {
            "path": str(path),
            "available": path.is_file(),
            "implementation": "built-in" if not path.is_file() else "asset-present",
        }
    return status
