#!/usr/bin/env python3
"""Persistent local research workspace backed by SQLite and a file store."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


VERSION = "1.0.0"
DB_NAME = "project.sqlite3"


class WorkspaceError(ValueError):
    """A user-correctable workspace error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(project_dir: Path | str) -> sqlite3.Connection:
    root = Path(project_dir).expanduser().resolve()
    database = root / DB_NAME
    if not database.is_file():
        raise WorkspaceError(
            f"No research workspace at {root}. Run `research_workspace.py init {root}` first."
        )
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(project_dir: Path | str, title: str = "") -> Path:
    root = Path(project_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "files" / "sources").mkdir(parents=True, exist_ok=True)
    (root / "files" / "artifacts").mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / DB_NAME)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                authors_json TEXT NOT NULL,
                year INTEGER,
                doi TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                item_type TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                page_count INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
                page INTEGER,
                kind TEXT NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(claim_id, segment_id, relation)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES('schema_version', '1') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        if title:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES('title', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (title,),
            )
        connection.commit()
    finally:
        connection.close()
    return root


def _write_atomic(path: Path, data: bytes, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise WorkspaceError(f"Output already exists: {path}. Pass --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def load_reference_payload(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"Could not read source list: {exc}") from exc
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("references") or payload.get("results") or []
    else:
        items = []
    references = [dict(item) for item in items if isinstance(item, dict) and item.get("title")]
    if not references:
        raise WorkspaceError("Source list contains no usable references.")
    return references


def add_sources(
    project_dir: Path | str,
    source_file: Path | str,
) -> int:
    connection = connect(project_dir)
    references = load_reference_payload(Path(source_file).expanduser().resolve())
    created = utc_now()
    added = 0
    try:
        current_position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM sources"
        ).fetchone()[0]
        for offset, reference in enumerate(references):
            record_id = str(
                reference.get("record_id")
                or reference.get("doi")
                or hashlib.sha1(
                    f"{reference.get('title')}|{reference.get('year')}".encode("utf-8")
                ).hexdigest()[:16]
            )
            values = {
                "record_id": str(record_id),
                "title": str(reference.get("title") or "").strip(),
                "authors_json": json.dumps(reference.get("authors") or [], ensure_ascii=False),
                "year": reference.get("year"),
                "doi": str(reference.get("doi") or ""),
                "url": str(reference.get("url") or ""),
                "source": str(reference.get("source") or ""),
                "item_type": str(reference.get("item_type") or ""),
                "metadata_json": json.dumps(reference, ensure_ascii=False),
                "position": current_position + offset,
                "created_at": created,
            }
            before = connection.total_changes
            connection.execute(
                """
                INSERT INTO sources(
                    record_id, title, authors_json, year, doi, url, source,
                    item_type, metadata_json, position, created_at
                ) VALUES(
                    :record_id, :title, :authors_json, :year, :doi, :url, :source,
                    :item_type, :metadata_json, :position, :created_at
                )
                ON CONFLICT(record_id) DO UPDATE SET
                    title=excluded.title,
                    authors_json=excluded.authors_json,
                    year=excluded.year,
                    doi=excluded.doi,
                    url=excluded.url,
                    source=excluded.source,
                    item_type=excluded.item_type,
                    metadata_json=excluded.metadata_json
                """,
                values,
            )
            if connection.total_changes > before:
                added += 1
        connection.commit()
    finally:
        connection.close()
    return added


def list_sources(project_dir: Path | str) -> list[dict[str, Any]]:
    connection = connect(project_dir)
    try:
        rows = connection.execute(
            """
            SELECT s.*, COUNT(a.id) AS attachment_count
            FROM sources s
            LEFT JOIN attachments a ON a.source_id = s.id
            GROUP BY s.id
            ORDER BY s.position, s.id
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row["id"],
            "record_id": row["record_id"],
            "title": row["title"],
            "authors": json.loads(row["authors_json"]),
            "year": row["year"],
            "doi": row["doi"],
            "url": row["url"],
            "source": row["source"],
            "item_type": row["item_type"],
            "attachment_count": row["attachment_count"],
        }
        for row in rows
    ]


def attach_pdf(
    project_dir: Path | str,
    source_id: int,
    file_path: Path | str,
) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    source = Path(file_path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        raise WorkspaceError("attach-pdf requires an existing PDF file.")
    if not source.read_bytes().startswith(b"%PDF-"):
        raise WorkspaceError("The attachment does not look like a PDF file.")
    connection = connect(root)
    try:
        source_row = connection.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
        if source_row is None:
            raise WorkspaceError(f"Source id {source_id} does not exist.")
        digest = sha256_file(source)
        destination = root / "files" / "sources" / f"{source_id}-{digest[:12]}-{source.name}"
        if not destination.exists():
            shutil.copy2(source, destination)
        relative = destination.relative_to(root).as_posix()
        connection.execute(
            """
            INSERT INTO attachments(source_id, path, filename, sha256, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (source_id, relative, source.name, digest, utc_now()),
        )
        attachment_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    return {
        "attachment_id": attachment_id,
        "source_id": source_id,
        "path": str(destination),
        "sha256": digest,
    }


def list_attachments(
    project_dir: Path | str,
    source_id: int | None = None,
) -> list[dict[str, Any]]:
    connection = connect(project_dir)
    try:
        if source_id is None:
            rows = connection.execute(
                """
                SELECT a.*, s.title
                FROM attachments a
                JOIN sources s ON s.id = a.source_id
                ORDER BY a.id
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT a.*, s.title
                FROM attachments a
                JOIN sources s ON s.id = a.source_id
                WHERE a.source_id=?
                ORDER BY a.id
                """,
                (source_id,),
            ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row["id"],
            "source_id": row["source_id"],
            "source_title": row["title"],
            "path": row["path"],
            "filename": row["filename"],
            "sha256": row["sha256"],
            "page_count": row["page_count"],
        }
        for row in rows
    ]


def ocr_pdf(
    project_dir: Path | str,
    attachment_id: int,
    language: str = "eng",
    dpi: int = 200,
) -> dict[str, int]:
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:
        raise WorkspaceError(
            "OCR requires pdf2image and pytesseract plus system Poppler/Tesseract."
        ) from exc
    if dpi < 72 or dpi > 600:
        raise WorkspaceError("OCR dpi must be between 72 and 600.")
    try:
        available_languages = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        raise WorkspaceError(
            "Could not inspect Tesseract language data. Verify the Tesseract installation."
        ) from exc
    if language not in available_languages:
        raise WorkspaceError(
            f"Tesseract language {language!r} is unavailable. Installed languages: "
            f"{', '.join(sorted(available_languages)) or 'none'}."
        )
    root = Path(project_dir).expanduser().resolve()
    connection = connect(root)
    try:
        row = connection.execute(
            "SELECT path FROM attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
        if row is None:
            raise WorkspaceError(f"Attachment id {attachment_id} does not exist.")
        pdf_path = root / row["path"]
        images = convert_from_path(str(pdf_path), dpi=dpi)
        created = utc_now()
        added = 0
        skipped = 0
        for page_number, image in enumerate(images, 1):
            existing = connection.execute(
                """
                SELECT 1 FROM segments
                WHERE attachment_id=? AND page=? AND kind='ocr'
                """,
                (attachment_id, page_number),
            ).fetchone()
            if existing:
                skipped += 1
                continue
            try:
                text = pytesseract.image_to_string(image, lang=language).strip()
            except Exception as exc:
                raise WorkspaceError(f"Tesseract OCR failed: {exc}") from exc
            if not text:
                skipped += 1
                continue
            connection.execute(
                """
                INSERT INTO segments(attachment_id, page, kind, ordinal, text, sha256, created_at)
                VALUES(?, ?, 'ocr', 0, ?, ?, ?)
                """,
                (
                    attachment_id,
                    page_number,
                    text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    created,
                ),
            )
            added += 1
        connection.execute(
            "UPDATE attachments SET page_count=? WHERE id=?",
            (len(images), attachment_id),
        )
        connection.commit()
    finally:
        connection.close()
    return {"pages": len(images), "ocr_segments": added, "skipped_pages": skipped}


def extract_pdf(
    project_dir: Path | str,
    attachment_id: int,
    force: bool = False,
) -> dict[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise WorkspaceError(
            "PDF extraction requires pypdf. Install the workspace dependencies."
        ) from exc
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None
    root = Path(project_dir).expanduser().resolve()
    connection = connect(root)
    try:
        row = connection.execute(
            "SELECT path FROM attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
        if row is None:
            raise WorkspaceError(f"Attachment id {attachment_id} does not exist.")
        linked = connection.execute(
            """
            SELECT COUNT(*)
            FROM evidence e
            JOIN segments s ON s.id = e.segment_id
            WHERE s.attachment_id=?
            """,
            (attachment_id,),
        ).fetchone()[0]
        if linked and not force:
            raise WorkspaceError(
                "Re-extraction would remove linked evidence. Use --force only after checking links."
            )
        pdf_path = root / row["path"]
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
        connection.execute("DELETE FROM segments WHERE attachment_id=?", (attachment_id,))
        text_segments = 0
        table_segments = 0
        created = utc_now()
        for page_index, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                connection.execute(
                    """
                    INSERT INTO segments(attachment_id, page, kind, ordinal, text, sha256, created_at)
                    VALUES(?, ?, 'text', 0, ?, ?, ?)
                    """,
                    (attachment_id, page_index, text, hashlib.sha256(text.encode("utf-8")).hexdigest(), created),
                )
                text_segments += 1
        if pdfplumber is not None:
            with pdfplumber.open(str(pdf_path)) as document:
                for page_index, page in enumerate(document.pages, 1):
                    for table_index, table in enumerate(page.extract_tables() or []):
                        if not table:
                            continue
                        text = "\n".join(
                            "\t".join("" if cell is None else str(cell) for cell in table_row)
                            for table_row in table
                        )
                        connection.execute(
                            """
                            INSERT INTO segments(
                                attachment_id, page, kind, ordinal, text, sha256, created_at
                            ) VALUES(?, ?, 'table', ?, ?, ?, ?)
                            """,
                            (
                                attachment_id,
                                page_index,
                                table_index,
                                text,
                                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                created,
                            ),
                        )
                        table_segments += 1
        connection.execute(
            "UPDATE attachments SET page_count=? WHERE id=?",
            (page_count, attachment_id),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "pages": page_count,
        "text_segments": text_segments,
        "table_segments": table_segments,
    }


def list_segments(
    project_dir: Path | str,
    attachment_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    connection = connect(project_dir)
    try:
        if attachment_id is None:
            rows = connection.execute(
                """
                SELECT s.*, a.filename
                FROM segments s
                JOIN attachments a ON a.id = s.attachment_id
                ORDER BY s.attachment_id, s.page, s.ordinal
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT s.*, a.filename
                FROM segments s
                JOIN attachments a ON a.id = s.attachment_id
                WHERE s.attachment_id=?
                ORDER BY s.page, s.ordinal
                LIMIT ?
                """,
                (attachment_id, limit),
            ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row["id"],
            "attachment_id": row["attachment_id"],
            "filename": row["filename"],
            "page": row["page"],
            "kind": row["kind"],
            "ordinal": row["ordinal"],
            "text": row["text"],
        }
        for row in rows
    ]


def add_segment(
    project_dir: Path | str,
    attachment_id: int,
    page: int | None,
    kind: str,
    text: str,
) -> int:
    if not text.strip():
        raise WorkspaceError("Evidence segment text must not be empty.")
    connection = connect(project_dir)
    try:
        exists = connection.execute(
            "SELECT 1 FROM attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
        if exists is None:
            raise WorkspaceError(f"Attachment id {attachment_id} does not exist.")
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM segments WHERE attachment_id=? AND kind=?",
            (attachment_id, kind),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO segments(attachment_id, page, kind, ordinal, text, sha256, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attachment_id,
                page,
                kind,
                ordinal,
                text.strip(),
                hashlib.sha256(text.strip().encode("utf-8")).hexdigest(),
                utc_now(),
            ),
        )
        segment_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    return segment_id


def add_claim(project_dir: Path | str, text: str, notes: str = "") -> int:
    if not text.strip():
        raise WorkspaceError("Claim text must not be empty.")
    connection = connect(project_dir)
    try:
        connection.execute(
            "INSERT INTO claims(text, notes, created_at) VALUES(?, ?, ?)",
            (text.strip(), notes.strip(), utc_now()),
        )
        claim_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    return claim_id


def add_decision(project_dir: Path | str, text: str) -> int:
    if not text.strip():
        raise WorkspaceError("Decision text must not be empty.")
    connection = connect(project_dir)
    try:
        connection.execute(
            "INSERT INTO decisions(text, created_at) VALUES(?, ?)",
            (text.strip(), utc_now()),
        )
        decision_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    return decision_id


def link_evidence(
    project_dir: Path | str,
    claim_id: int,
    segment_id: int,
    relation: str,
    note: str = "",
) -> int:
    if relation not in {"supports", "challenges", "context"}:
        raise WorkspaceError("relation must be supports, challenges, or context.")
    connection = connect(project_dir)
    try:
        connection.execute(
            """
            INSERT INTO evidence(claim_id, segment_id, relation, note, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (claim_id, segment_id, relation, note.strip(), utc_now()),
        )
        evidence_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()
    except sqlite3.IntegrityError as exc:
        raise WorkspaceError("This claim/segment/relation link already exists.") from exc
    finally:
        connection.close()
    return evidence_id


def evidence_matrix(project_dir: Path | str) -> dict[str, Any]:
    connection = connect(project_dir)
    try:
        claims = connection.execute(
            "SELECT * FROM claims ORDER BY id"
        ).fetchall()
        matrix = []
        for claim in claims:
            rows = connection.execute(
                """
                SELECT e.relation, e.note AS evidence_note,
                       s.id AS segment_id, s.page, s.kind, s.text,
                       a.id AS attachment_id, a.filename,
                       src.id AS source_id, src.record_id, src.title,
                       src.authors_json, src.year, src.doi, src.url
                FROM evidence e
                JOIN segments s ON s.id = e.segment_id
                JOIN attachments a ON a.id = s.attachment_id
                JOIN sources src ON src.id = a.source_id
                WHERE e.claim_id=?
                ORDER BY e.id
                """,
                (claim["id"],),
            ).fetchall()
            matrix.append(
                {
                    "claim_id": claim["id"],
                    "claim": claim["text"],
                    "status": claim["status"],
                    "notes": claim["notes"],
                    "evidence": [
                        {
                            "relation": row["relation"],
                            "note": row["evidence_note"],
                            "segment_id": row["segment_id"],
                            "page": row["page"],
                            "kind": row["kind"],
                            "text": row["text"],
                            "attachment_id": row["attachment_id"],
                            "filename": row["filename"],
                            "source_id": row["source_id"],
                            "record_id": row["record_id"],
                            "title": row["title"],
                            "authors": json.loads(row["authors_json"]),
                            "year": row["year"],
                            "doi": row["doi"],
                            "url": row["url"],
                        }
                        for row in rows
                    ],
                }
            )
        decisions = [
            {"id": row["id"], "text": row["text"], "created_at": row["created_at"]}
            for row in connection.execute("SELECT * FROM decisions ORDER BY id").fetchall()
        ]
    finally:
        connection.close()
    return {"version": VERSION, "claims": matrix, "decisions": decisions}


def _matrix_markdown(matrix: Mapping[str, Any]) -> str:
    lines = ["# Evidence Matrix", ""]
    for claim in matrix["claims"]:
        lines.extend(
            [
                f"## Claim {claim['claim_id']}",
                "",
                claim["claim"],
                "",
            ]
        )
        if not claim["evidence"]:
            lines.extend(["_No linked evidence._", ""])
            continue
        lines.extend(
            [
                "| Relation | Source | Location | Evidence |",
                "|---|---|---|---|",
            ]
        )
        for item in claim["evidence"]:
            location = f"p. {item['page']}" if item["page"] else item["filename"]
            source = f"{item['title']} ({item['year'] or 'n.d.'})"
            evidence_text = item["text"].replace("|", "\\|").replace("\n", " ")[:500]
            lines.append(
                f"| {item['relation']} | {source} | {location} | {evidence_text} |"
            )
        lines.append("")
    if matrix["decisions"]:
        lines.extend(["# Decisions", ""])
        lines.extend(f"- {item['text']}" for item in matrix["decisions"])
        lines.append("")
    return "\n".join(lines)


def workspace_status(project_dir: Path | str) -> dict[str, Any]:
    connection = connect(project_dir)
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "attachments", "segments", "claims", "evidence", "decisions", "artifacts")
        }
        metadata = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key, value FROM metadata").fetchall()
        }
    finally:
        connection.close()
    return {"project": str(Path(project_dir).expanduser().resolve()), "counts": counts, "metadata": metadata}


def record_artifact(
    project_dir: Path | str,
    path: Path | str,
    kind: str,
) -> int:
    root = Path(project_dir).expanduser().resolve()
    artifact = Path(path).expanduser().resolve()
    if not artifact.is_file():
        raise WorkspaceError(f"Artifact not found: {artifact}")
    connection = connect(root)
    try:
        connection.execute(
            "INSERT INTO artifacts(kind, path, sha256, created_at) VALUES(?, ?, ?, ?)",
            (kind, str(artifact), sha256_file(artifact), utc_now()),
        )
        artifact_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    return artifact_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_workspace.py",
        description="Manage persistent sources, PDF evidence, claims, and project artifacts.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or update a research workspace.")
    init_parser.add_argument("project_dir", type=Path)
    init_parser.add_argument("--title", default="")

    add_source_parser = subparsers.add_parser("add-sources", help="Import a candidate/reference JSON list.")
    add_source_parser.add_argument("project_dir", type=Path)
    add_source_parser.add_argument("source_file", type=Path)

    list_parser = subparsers.add_parser("sources", help="List project sources.")
    list_parser.add_argument("project_dir", type=Path)

    attach_parser = subparsers.add_parser("attach-pdf", help="Copy an authorized PDF into the project.")
    attach_parser.add_argument("project_dir", type=Path)
    attach_parser.add_argument("--source-id", type=int, required=True)
    attach_parser.add_argument("--file", type=Path, required=True)

    extract_parser = subparsers.add_parser("extract-pdf", help="Extract text and tables into segments.")
    extract_parser.add_argument("project_dir", type=Path)
    extract_parser.add_argument("--attachment-id", type=int, required=True)
    extract_parser.add_argument("--force", action="store_true")

    attachments_parser = subparsers.add_parser("attachments", help="List attached PDFs.")
    attachments_parser.add_argument("project_dir", type=Path)
    attachments_parser.add_argument("--source-id", type=int)

    ocr_parser = subparsers.add_parser("ocr-pdf", help="OCR a scanned PDF into evidence segments.")
    ocr_parser.add_argument("project_dir", type=Path)
    ocr_parser.add_argument("--attachment-id", type=int, required=True)
    ocr_parser.add_argument("--language", default="eng")
    ocr_parser.add_argument("--dpi", type=int, default=200)

    segments_parser = subparsers.add_parser("segments", help="List extracted evidence segments.")
    segments_parser.add_argument("project_dir", type=Path)
    segments_parser.add_argument("--attachment-id", type=int)
    segments_parser.add_argument("--limit", type=int, default=100)

    segment_parser = subparsers.add_parser("add-segment", help="Add an OCR or manual evidence segment.")
    segment_parser.add_argument("project_dir", type=Path)
    segment_parser.add_argument("--attachment-id", type=int, required=True)
    segment_parser.add_argument("--page", type=int)
    segment_parser.add_argument("--kind", default="text")
    segment_parser.add_argument("--text", required=True)

    claim_parser = subparsers.add_parser("add-claim", help="Add a research claim.")
    claim_parser.add_argument("project_dir", type=Path)
    claim_parser.add_argument("--text", required=True)
    claim_parser.add_argument("--notes", default="")

    decision_parser = subparsers.add_parser("add-decision", help="Record a project decision.")
    decision_parser.add_argument("project_dir", type=Path)
    decision_parser.add_argument("--text", required=True)

    evidence_parser = subparsers.add_parser("link-evidence", help="Link a segment to a claim.")
    evidence_parser.add_argument("project_dir", type=Path)
    evidence_parser.add_argument("--claim-id", type=int, required=True)
    evidence_parser.add_argument("--segment-id", type=int, required=True)
    evidence_parser.add_argument(
        "--relation",
        choices=("supports", "challenges", "context"),
        default="supports",
    )
    evidence_parser.add_argument("--note", default="")

    matrix_parser = subparsers.add_parser("matrix", help="Export the claim-evidence matrix.")
    matrix_parser.add_argument("project_dir", type=Path)
    matrix_parser.add_argument("--output", type=Path, required=True)
    matrix_parser.add_argument("--overwrite", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show project counts and metadata.")
    status_parser.add_argument("project_dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            path = initialize(args.project_dir, title=args.title)
            print(f"Research workspace: {path}")
            return 0
        if args.command == "add-sources":
            count = add_sources(args.project_dir, args.source_file)
            print(f"Imported or updated {count} source(s).")
            return 0
        if args.command == "sources":
            print(json.dumps(list_sources(args.project_dir), indent=2, ensure_ascii=False))
            return 0
        if args.command == "attach-pdf":
            print(
                json.dumps(
                    attach_pdf(args.project_dir, args.source_id, args.file),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "extract-pdf":
            print(
                json.dumps(
                    extract_pdf(args.project_dir, args.attachment_id, force=args.force),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "attachments":
            print(
                json.dumps(
                    list_attachments(args.project_dir, args.source_id),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "ocr-pdf":
            print(
                json.dumps(
                    ocr_pdf(
                        args.project_dir,
                        args.attachment_id,
                        language=args.language,
                        dpi=args.dpi,
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "segments":
            print(
                json.dumps(
                    list_segments(args.project_dir, args.attachment_id, args.limit),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "add-segment":
            segment_id = add_segment(
                args.project_dir,
                args.attachment_id,
                args.page,
                args.kind,
                args.text,
            )
            print(f"Segment ID: {segment_id}")
            return 0
        if args.command == "add-claim":
            print(f"Claim ID: {add_claim(args.project_dir, args.text, args.notes)}")
            return 0
        if args.command == "add-decision":
            print(f"Decision ID: {add_decision(args.project_dir, args.text)}")
            return 0
        if args.command == "link-evidence":
            evidence_id = link_evidence(
                args.project_dir,
                args.claim_id,
                args.segment_id,
                args.relation,
                args.note,
            )
            print(f"Evidence link ID: {evidence_id}")
            return 0
        if args.command == "matrix":
            matrix = evidence_matrix(args.project_dir)
            payload = json.dumps(matrix, indent=2, ensure_ascii=False).encode("utf-8")
            markdown_path = args.output.with_suffix(".md")
            if args.output.exists() and not args.overwrite:
                raise WorkspaceError(
                    f"Output already exists: {args.output}. Pass --overwrite."
                )
            if markdown_path.exists() and not args.overwrite:
                raise WorkspaceError(
                    f"Output already exists: {markdown_path}. Pass --overwrite."
                )
            _write_atomic(args.output, payload, overwrite=args.overwrite)
            _write_atomic(
                markdown_path,
                _matrix_markdown(matrix).encode("utf-8"),
                overwrite=args.overwrite,
            )
            print(f"Evidence matrix: {args.output}")
            print(f"Evidence report: {markdown_path}")
            return 0
        if args.command == "status":
            print(json.dumps(workspace_status(args.project_dir), indent=2, ensure_ascii=False))
            return 0
        return 2
    except (WorkspaceError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
