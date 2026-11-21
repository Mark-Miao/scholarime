"""Import RIS, EndNote, RefWorks, and BibTeX records into Scholarime references."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from scholar_common import Reference, clean_text, deduplicate_references


class ReferenceImportError(ValueError):
    """A user-correctable import error."""


TYPE_MAP = {
    "journal article": "article",
    "journalarticle": "article",
    "article": "article",
    "journal": "article",
    "book": "book",
    "book section": "book",
    "conference paper": "conference",
    "conference proceedings": "conference",
    "report": "report",
    "manual": "manual",
    "thesis": "thesis",
    "dissertation": "thesis",
    "web page": "webpage",
    "webpage": "webpage",
    "electronic article": "webpage",
    "preprint": "preprint",
    "generic": "other",
}


def _item_type(value: str) -> str:
    normalised = clean_text(value).casefold()
    return TYPE_MAP.get(normalised, normalised or "other")


def _author_list(value: str, separator: str = ";") -> list[str]:
    return [clean_text(part) for part in value.split(separator) if clean_text(part)]


def _record_from_fields(
    fields: dict[str, list[str]],
    source: str,
) -> Reference | None:
    def first(*keys: str) -> str:
        for key in keys:
            values = fields.get(key.lower(), [])
            if values:
                return clean_text(values[0])
        return ""

    title = first("T1", "TI", "T", "title")
    if not title:
        return None
    authors = []
    for key in ("AU", "A1", "A", "author"):
        authors.extend(_author_list(first_all(fields, key), ";"))
    if not authors:
        authors = _author_list(first("A1", "AU", "author"), ";")
    keywords = [
        clean_text(item)
        for key in ("KW", "K1", "keywords")
        for item in fields.get(key.lower(), [])
        if clean_text(item)
    ]
    year_value = first("PY", "Y1", "YR", "D", "year", "date")
    item_type = _item_type(first("TY", "RT", "0", "type", "entry_type"))
    doi = first("DO", "R", "doi")
    start_page = first("SP", "startpage")
    end_page = first("EP", "endpage")
    pages = f"{start_page}-{end_page}" if start_page and end_page else start_page
    return Reference(
        title=title,
        source=source,
        source_id=first("ID", "AN", "C1", "key"),
        authors=list(dict.fromkeys(authors)),
        year=year_value,
        container=first("JO", "JF", "T2", "J", "journal", "booktitle"),
        volume=first("VL", "V", "volume"),
        issue=first("IS", "N", "number", "issue"),
        pages=first("pages") or pages,
        doi=doi,
        url=first("UR", "UL", "URL", "url"),
        pdf_url=first("L1", "link", "pdf"),
        abstract=first("AB", "X", "abstract"),
        keywords=keywords,
        item_type=item_type,
        publisher=first("PB", "publisher"),
        language=first("LA", "language"),
    )


def first_all(fields: dict[str, list[str]], key: str) -> str:
    return "; ".join(fields.get(key.lower(), []))


def parse_ris(content: str) -> list[Reference]:
    records: list[Reference] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for raw_line in content.lstrip("\ufeff").splitlines():
        if not raw_line.strip():
            continue
        match = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s?(.*)$", raw_line)
        if not match:
            if last_tag and raw_line.startswith((" ", "\t")):
                current.setdefault(last_tag.lower(), []).append(raw_line.strip())
            continue
        tag, value = match.groups()
        tag = tag.upper()
        if tag == "ER":
            reference = _record_from_fields(current, "RIS import")
            if reference:
                records.append(reference)
            current = {}
            last_tag = ""
            continue
        current.setdefault(tag.lower(), []).append(clean_text(value))
        last_tag = tag
    if current:
        reference = _record_from_fields(current, "RIS import")
        if reference:
            records.append(reference)
    if not records:
        raise ReferenceImportError("No RIS records were found.")
    return records


def parse_endnote(content: str) -> list[Reference]:
    records: list[Reference] = []
    current: dict[str, list[str]] = {}
    for raw_line in content.lstrip("\ufeff").splitlines():
        if not raw_line.strip():
            reference = _record_from_fields(current, "EndNote import")
            if reference:
                records.append(reference)
            current = {}
            continue
        match = re.match(r"^%([A-Z0-9])\s?(.*)$", raw_line)
        if not match:
            continue
        tag, value = match.groups()
        if tag == "0" and current:
            reference = _record_from_fields(current, "EndNote import")
            if reference:
                records.append(reference)
            current = {}
        current.setdefault(tag.lower(), []).append(clean_text(value))
    if current:
        reference = _record_from_fields(current, "EndNote import")
        if reference:
            records.append(reference)
    if not records:
        raise ReferenceImportError("No EndNote records were found.")
    return records


def parse_refworks(content: str) -> list[Reference]:
    records: list[Reference] = []
    current: dict[str, list[str]] = {}
    for raw_line in content.lstrip("\ufeff").splitlines():
        if not raw_line.strip():
            reference = _record_from_fields(current, "RefWorks import")
            if reference:
                records.append(reference)
            current = {}
            continue
        match = re.match(r"^([A-Z0-9]{2})\s+(.*)$", raw_line)
        if not match:
            continue
        tag, value = match.groups()
        if tag.upper() == "RT" and current:
            reference = _record_from_fields(current, "RefWorks import")
            if reference:
                records.append(reference)
            current = {}
        current.setdefault(tag.lower(), []).append(clean_text(value))
    if current:
        reference = _record_from_fields(current, "RefWorks import")
        if reference:
            records.append(reference)
    if not records:
        raise ReferenceImportError("No RefWorks records were found.")
    return records


def _balanced_entry_end(content: str, start: int) -> int:
    depth = 0
    quote = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"' and depth == 0:
            quote = not quote
        if quote:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ReferenceImportError("Unbalanced braces in BibTeX input.")


def _split_top_level(value: str, separator: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"' and depth == 0:
            quote = not quote
        elif not quote:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif char == separator and depth == 0:
                parts.append(value[start:index])
                start = index + 1
    parts.append(value[start:])
    return parts


def _bib_value(value: str) -> str:
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1]
    elif value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    value = value.replace("\\&", "&").replace("\\_", "_")
    value = re.sub(r"\\[a-zA-Z]+\s*", "", value)
    value = value.replace("{", "").replace("}", "")
    return clean_text(value)


def _parse_bib_fields(body: str) -> dict[str, list[str]]:
    entries = _split_top_level(body)
    if not entries:
        return {}
    fields: dict[str, list[str]] = {"key": [clean_text(entries[0])]}
    for entry in entries[1:]:
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = clean_text(key).casefold()
        pieces = _split_top_level(value, "#")
        fields.setdefault(key, []).append(" ".join(_bib_value(piece) for piece in pieces))
    return fields


def parse_bibtex(content: str) -> list[Reference]:
    records: list[Reference] = []
    content = content.lstrip("\ufeff")
    index = 0
    while True:
        match = re.search(r"@([A-Za-z]+)\s*\{", content[index:])
        if not match:
            break
        at = index + match.start()
        entry_type = match.group(1).casefold()
        brace_index = content.find("{", at)
        end = _balanced_entry_end(content, brace_index)
        body = content[brace_index + 1 : end]
        index = end + 1
        if entry_type in {"comment", "preamble", "string"}:
            continue
        if "," not in body:
            continue
        fields = _parse_bib_fields(body)
        fields["entry_type"] = [entry_type]
        reference = _record_from_fields(fields, "BibTeX import")
        if reference:
            records.append(reference)
    if not records:
        raise ReferenceImportError("No BibTeX records were found.")
    return records


def detect_format(filename: str, content: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".ris":
        return "ris"
    if suffix in {".enw"}:
        return "endnote"
    if suffix in {".bib", ".bibtex"}:
        return "bibtex"
    stripped = content.lstrip("\ufeff \t\r\n")
    if stripped.startswith("@") or re.search(r"@\w+\s*\{", stripped):
        return "bibtex"
    if re.search(r"(?m)^%[0-9A-Z]\s", stripped):
        return "endnote"
    if re.search(r"(?m)^TY\s{1,2}-", stripped):
        return "ris"
    if re.search(r"(?m)^RT\s+", stripped) or re.search(r"(?m)^A1\s+", stripped):
        return "refworks"
    raise ReferenceImportError("Could not detect RIS, EndNote, RefWorks, or BibTeX format.")


def parse_import(filename: str, content: str) -> list[Reference]:
    if len(content.encode("utf-8")) > 4 * 1024 * 1024:
        raise ReferenceImportError("Import file is larger than the 4 MiB limit.")
    format_name = detect_format(filename, content)
    if format_name == "ris":
        references = parse_ris(content)
    elif format_name == "endnote":
        references = parse_endnote(content)
    elif format_name == "refworks":
        references = parse_refworks(content)
    elif format_name == "bibtex":
        references = parse_bibtex(content)
    else:
        raise ReferenceImportError(f"Unsupported import format: {format_name}")
    return deduplicate_references(references)
