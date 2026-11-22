#!/usr/bin/env python3
"""Verify scholarly search candidates with the vendored CiteVerify engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scholar_config import semantic_scholar_key

try:
    from scholarime.vendor.citeverify import citeverify_lookup
except ModuleNotFoundError as exc:
    if exc.name != "scholarime":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scholarime.vendor.citeverify import citeverify_lookup


VERSION = "1.0.0"
STATUS_ORDER = {
    "conflict": 0,
    "unverified": 1,
    "unable_to_check": 2,
    "verified_title_variant": 3,
    "verified": 4,
}


class VerificationError(ValueError):
    """A user-correctable verification error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_key_file(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.expanduser().read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise VerificationError(f"Could not read API key file: {exc}") from exc


def load_candidates(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VerificationError(f"Could not read candidates file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"Candidates file is invalid JSON: {exc}") from exc
    if isinstance(payload, list):
        items = payload
        document: dict[str, Any] = {"source": str(path), "results": items}
    elif isinstance(payload, dict):
        document = payload
        items = (
            payload.get("candidates")
            or payload.get("results")
            or payload.get("references")
            or []
        )
    else:
        raise VerificationError("Candidates file must contain a list or object.")
    candidates = [dict(item) for item in items if isinstance(item, dict)]
    if not candidates:
        raise VerificationError("Candidates file contains no references.")
    return document, candidates


def _citation_text(candidate: Mapping[str, Any]) -> str:
    authors = "; ".join(str(author) for author in candidate.get("authors") or [])
    year = candidate.get("year") or "n.d."
    title = str(candidate.get("title") or "").strip()
    container = str(candidate.get("container") or "").strip()
    doi = str(candidate.get("doi") or "").strip()
    parts = [f"{authors} ({year})." if authors else f"({year}).", title]
    if container:
        parts.append(f"{container}.")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    return " ".join(part for part in parts if part).strip()


def candidate_to_diagnostic(
    candidate: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    title = str(candidate.get("title") or "").strip()
    if not title:
        raise VerificationError(f"Candidate {index} has no title.")
    item_type = str(candidate.get("item_type") or "").casefold()
    if item_type in {"book", "manual", "report"}:
        citation_type = "book_or_report"
    elif item_type in {"webpage", "web", "website"}:
        citation_type = "standard_or_web"
    else:
        citation_type = "article_or_conference"
    citation = _citation_text(candidate)
    return {
        "number": index,
        "raw_citation": citation,
        "cleaned_citation": citation,
        "title_candidates": [
            {
                "title": title,
                "method": "before_in",
                "confidence": 1.0,
            }
        ],
        "doi": str(candidate.get("doi") or "").strip() or None,
        "isbn": str(candidate.get("isbn") or "").strip() or None,
        "citation_type": citation_type,
        "parser_confidence": "high" if candidate.get("doi") else "medium",
        "parser_notes": [
            f"Imported from {candidate.get('source') or 'Scholarly search'}.",
            f"Candidate ID: {candidate.get('record_id') or index}.",
        ],
    }


def _normalise_field(value: Any) -> str:
    text = str(value or "").casefold()
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", text)


def _author_surname(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    if "," in name:
        return _normalise_field(name.split(",", 1)[0])
    parts = re.findall(r"[\w\u3400-\u9fff.-]+", name)
    return _normalise_field(parts[-1]) if parts else ""


def _compare_exact(
    candidate_value: Any,
    matched_value: Any,
    *,
    container: bool = False,
) -> dict[str, Any]:
    candidate_text = str(candidate_value or "").strip()
    matched_text = str(matched_value or "").strip()
    if not candidate_text or not matched_text:
        return {
            "status": "not_checked",
            "candidate": candidate_text or None,
            "matched": matched_text or None,
        }
    left = _normalise_field(candidate_text)
    right = _normalise_field(matched_text)
    if left == right:
        status = "match"
    elif container and left and right and (left in right or right in left):
        status = "variant"
    else:
        status = "mismatch"
    return {
        "status": status,
        "candidate": candidate_text,
        "matched": matched_text,
    }


def _compare_year(candidate_value: Any, matched_value: Any) -> dict[str, Any]:
    try:
        candidate_year = int(candidate_value)
    except (TypeError, ValueError):
        candidate_year = None
    try:
        matched_year = int(matched_value)
    except (TypeError, ValueError):
        matched_year = None
    if candidate_year is None or matched_year is None:
        return {
            "status": "not_checked",
            "candidate": candidate_year,
            "matched": matched_year,
        }
    if candidate_year == matched_year:
        status = "match"
    elif abs(candidate_year - matched_year) <= 1:
        status = "variant"
    else:
        status = "mismatch"
    return {
        "status": status,
        "candidate": candidate_year,
        "matched": matched_year,
    }


def _compare_authors(candidate: Mapping[str, Any], verification: Mapping[str, Any]) -> dict[str, Any]:
    candidate_authors = [
        _author_surname(author) for author in candidate.get("authors") or []
    ]
    matched_authors = [
        _author_surname(author)
        for author in verification.get("matched_authors") or []
    ]
    candidate_authors = [author for author in candidate_authors if author]
    matched_authors = [author for author in matched_authors if author]
    if not candidate_authors or not matched_authors:
        return {
            "status": "not_checked",
            "candidate_count": len(candidate_authors),
            "matched_count": len(matched_authors),
        }
    overlap = len(set(candidate_authors) & set(matched_authors)) / len(candidate_authors)
    first_matches = candidate_authors[0] == matched_authors[0]
    if overlap == 1 and first_matches:
        status = "match"
    elif overlap >= 0.5:
        status = "variant"
    else:
        status = "mismatch"
    return {
        "status": status,
        "candidate_count": len(candidate_authors),
        "matched_count": len(matched_authors),
        "overlap": round(overlap, 3),
        "first_author_matches": first_matches,
        "candidate_first": candidate_authors[0],
        "matched_first": matched_authors[0],
    }


def compare_candidate_fields(
    candidate: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    status = str(verification.get("status") or "")
    title_status = {
        "verified": "match",
        "verified_title_variant": "variant",
        "conflict": "mismatch",
    }.get(status, "not_checked")
    fields = {
        "title": {
            "status": title_status,
            "candidate": candidate.get("title"),
            "matched": verification.get("matched_title"),
        },
        "authors": _compare_authors(candidate, verification),
        "year": _compare_year(candidate.get("year"), verification.get("matched_year")),
        "container": _compare_exact(
            candidate.get("container"),
            verification.get("matched_container"),
            container=True,
        ),
        "volume": _compare_exact(
            candidate.get("volume"),
            verification.get("matched_volume"),
        ),
        "issue": _compare_exact(
            candidate.get("issue"),
            verification.get("matched_issue"),
        ),
        "pages": _compare_exact(
            candidate.get("pages"),
            verification.get("matched_pages"),
        ),
    }
    summary: dict[str, int] = {}
    for field in fields.values():
        field_status = str(field["status"])
        summary[field_status] = summary.get(field_status, 0) + 1
    if title_status == "mismatch" or summary.get("mismatch"):
        overall = "conflict"
    elif summary.get("variant"):
        overall = "review"
    elif summary.get("match"):
        overall = "consistent"
    else:
        overall = "insufficient_metadata"
    return {
        "status": overall,
        "summary": summary,
        "fields": fields,
    }


def _status_counts(results: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        status = str((item.get("verification") or {}).get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: STATUS_ORDER.get(item[0], 99)))


def _field_status_counts(results: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        status = str(
            (item.get("verification") or {}).get("field_status")
            or "unknown"
        )
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def verify_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    source: str,
    source_sha256: str,
    pause: float = 0.2,
    limit: int | None = None,
    openalex_key: str = "",
    semantic_key: str = "",
    verifier: Callable[..., dict[str, Any]] = citeverify_lookup.verify_item,
) -> dict[str, Any]:
    if limit is not None and limit < 1:
        raise VerificationError("limit must be positive.")
    selected = list(candidates[:limit] if limit is not None else candidates)
    diagnostics = [
        candidate_to_diagnostic(candidate, index)
        for index, candidate in enumerate(selected, start=1)
    ]
    results = []
    for index, item in enumerate(diagnostics, start=1):
        verification = verifier(
            item,
            max(0.0, pause),
            openalex_key or None,
            semantic_key or None,
            index,
        )
        results.append(verification)
    enriched = []
    for candidate, result in zip(selected, results, strict=True):
        verification = dict(result["verification"])
        fields = compare_candidate_fields(candidate, verification)
        verification["field_comparison"] = fields
        verification["field_status"] = fields["status"]
        result["verification"] = verification
        item = dict(candidate)
        item["verification_status"] = verification.get("status", "unknown")
        item["field_verification_status"] = fields["status"]
        item["verification"] = verification
        enriched.append(item)
    return {
        "version": VERSION,
        "verifier": f"CiteVerify {citeverify_lookup.USER_AGENT.split('/')[1].split()[0]}",
        "generated_at": utc_now(),
        "source": source,
        "source_sha256": source_sha256,
        "reference_count": len(results),
        "warning": "Verification reports evidence only. Unverified does not mean fabricated.",
        "status_counts": _status_counts(results),
        "field_status_counts": _field_status_counts(results),
        "diagnostics": diagnostics,
        "results": results,
        "candidates": enriched,
    }


def _write_outputs(
    payload: Mapping[str, Any],
    output: Path,
    report: Path,
    overwrite: bool,
) -> None:
    if output.resolve() == report.resolve():
        raise VerificationError("JSON output and HTML report must use different paths.")
    for path in (output, report):
        if path.exists() and not overwrite:
            raise VerificationError(f"Output already exists: {path}. Pass --overwrite.")
    report_html = citeverify_lookup.render_html_report(
        str(payload.get("source")),
        list(payload["results"]),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report.write_text(report_html, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scholarly_verify.py",
        description="Verify scholarly search candidates with the vendored CiteVerify engine.",
    )
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument(
        "--openalex-key-file",
        type=Path,
        help="Read an OpenAlex key from a local file; never commit this file.",
    )
    parser.add_argument(
        "--s2-api-key-file",
        type=Path,
        help="Read a Semantic Scholar key from a local file.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = args.candidates.expanduser().resolve()
        document, candidates = load_candidates(source)
        output = (
            args.output.expanduser().resolve()
            if args.output
            else source.with_name("citation-verification.json")
        )
        report = (
            args.report.expanduser().resolve()
            if args.report
            else output.with_suffix(".html")
        )
        if source in {output, report}:
            raise VerificationError("Output paths must differ from the candidates input.")
        openalex_key = read_key_file(args.openalex_key_file) or os.environ.get(
            "OPENALEX_KEY", ""
        ).strip()
        semantic_key = (
            read_key_file(args.s2_api_key_file)
            or os.environ.get("S2_API_KEY", "").strip()
            or semantic_scholar_key()
        )
        payload = verify_candidates(
            candidates,
            source=str(source),
            source_sha256=sha256_file(source),
            pause=args.pause,
            limit=args.limit,
            openalex_key=openalex_key,
            semantic_key=semantic_key,
        )
        payload["search_run_id"] = document.get("run_id")
        _write_outputs(payload, output, report, args.overwrite)
        print(f"Verification JSON: {output}")
        print(f"Verification report: {report}")
        for status, count in payload["status_counts"].items():
            print(f"{status}: {count}")
        return 0
    except (VerificationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
