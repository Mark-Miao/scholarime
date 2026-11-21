#!/usr/bin/env python3
"""Assemble LaTeX or DOCX manuscripts from a Scholarime project and citation map."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_workspace import record_artifact
from scholar_common import Reference, format_citations


VERSION = "1.0.0"
CITATION_RE = re.compile(r"\[@([^\]]+)\]")


class ManuscriptError(ValueError):
    """A user-correctable manuscript error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_project_references(project_dir: Path) -> list[Reference]:
    database = project_dir.expanduser().resolve() / "project.sqlite3"
    if not database.is_file():
        raise ManuscriptError(f"No research workspace database at {database}.")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM sources ORDER BY position, id").fetchall()
    finally:
        connection.close()
    references = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        references.append(
            Reference.from_dict(
                {
                    **metadata,
                    "record_id": row["record_id"],
                    "title": row["title"],
                    "authors": json.loads(row["authors_json"]),
                    "year": row["year"],
                    "doi": row["doi"],
                    "url": row["url"],
                    "source": row["source"],
                    "item_type": row["item_type"],
                }
            )
        )
    if not references:
        raise ManuscriptError("The project has no imported sources.")
    return references


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _reference_to_csl(reference: Reference) -> dict[str, Any]:
    type_map = {
        "article": "article-journal",
        "book": "book",
        "conference": "paper-conference",
        "report": "report",
        "thesis": "thesis",
        "webpage": "webpage",
        "preprint": "article",
    }
    authors = []
    for author in reference.authors:
        if "," in author:
            family, given = author.split(",", 1)
        else:
            parts = author.split()
            family = parts[-1] if parts else author
            given = " ".join(parts[:-1])
        authors.append({"family": family.strip(), "given": given.strip()})
    csl: dict[str, Any] = {
        "id": reference.record_id,
        "type": type_map.get(reference.item_type, "article"),
        "title": reference.title,
        "author": authors,
    }
    if reference.year:
        csl["issued"] = {"date-parts": [[reference.year]]}
    if reference.container:
        csl["container-title"] = reference.container
    if reference.volume:
        csl["volume"] = reference.volume
    if reference.issue:
        csl["issue"] = reference.issue
    if reference.pages:
        csl["page"] = reference.pages
    if reference.doi:
        csl["DOI"] = reference.doi
    if reference.url:
        csl["URL"] = reference.url
    return csl


def csl_bibliography(
    references: Sequence[Reference],
    style_path: Path,
) -> tuple[list[str], str]:
    try:
        from citeproc import Citation, CitationItem, CitationStylesBibliography, CitationStylesStyle
        from citeproc import formatter
        from citeproc.source.json import CiteProcJSON
    except ImportError:
        return [], "unavailable"
    try:
        style = CitationStylesStyle(str(style_path), validate=False)
        source = CiteProcJSON([_reference_to_csl(reference) for reference in references])
        bibliography = CitationStylesBibliography(style, source, formatter.plain)
        for reference in references:
            bibliography.register(Citation([CitationItem(reference.record_id)]))
        entries = [str(entry) for entry in bibliography.bibliography()]
        return entries, "citeproc"
    except Exception:
        return [], "error"


def render_manuscript(
    manuscript: Mapping[str, Any],
    references: Sequence[Reference],
    style: str,
    style_path: Path,
) -> dict[str, Any]:
    if manuscript.get("version") != 1:
        raise ManuscriptError("Manuscript version must be 1.")
    sections = manuscript.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ManuscriptError("Manuscript requires a non-empty sections list.")
    by_id = {reference.record_id: reference for reference in references}
    by_doi = {reference.doi: reference for reference in references if reference.doi}
    order: list[str] = []
    warnings: list[str] = []

    def resolve(token: str) -> Reference:
        key = token.strip().lstrip("@").strip()
        reference = by_id.get(key) or by_doi.get(key)
        if reference is None:
            raise ManuscriptError(f"Unknown citation key: {key}")
        if reference.record_id not in order:
            order.append(reference.record_id)
        return reference

    def scan(value: Any) -> None:
        if isinstance(value, str):
            for match in CITATION_RE.finditer(value):
                for token in match.group(1).split(";"):
                    resolve(token)
        elif isinstance(value, list):
            for item in value:
                scan(item)
        elif isinstance(value, dict):
            for item in value.values():
                scan(item)

    for section in sections:
        if not isinstance(section, dict):
            raise ManuscriptError("Each section must be an object.")
        if not isinstance(section.get("paragraphs", []), list):
            raise ManuscriptError("Section paragraphs must be a list.")
    scan(manuscript.get("abstract", ""))
    scan(sections)
    scan(manuscript.get("figures", []))
    scan(manuscript.get("tables", []))
    cited_references = [by_id[key] for key in order]
    formatted = format_citations(style, cited_references)
    label_by_id = dict(zip(order, formatted["in_text"], strict=True))

    def replace_citations(text: str) -> str:
        def replacement(match: re.Match[str]) -> str:
            labels = []
            for token in match.group(1).split(";"):
                reference = resolve(token)
                labels.append(label_by_id[reference.record_id])
            if formatted["style"] == "gb-t-7714-2025":
                numbers = []
                for label in labels:
                    numbers.extend(part.strip() for part in label.strip("[]").split(","))
                return "[" + ",".join(dict.fromkeys(numbers)) + "]"
            return "(" + "; ".join(label.strip("()") for label in labels) + ")"

        return CITATION_RE.sub(replacement, text)

    csl_entries, csl_engine = (
        csl_bibliography(cited_references, style_path)
        if cited_references
        else ([], "citeproc")
    )
    if csl_engine != "citeproc":
        warnings.append(
            "CSL processor unavailable or failed; bibliography uses the built-in formatter."
        )
        bibliography = formatted["references"]
    else:
        bibliography = csl_entries
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "citation_style": formatted["style"],
        "citation_engine": csl_engine,
        "warnings": warnings,
        "manuscript": json.loads(json.dumps(manuscript)),
        "cited_reference_ids": order,
        "bibliography": bibliography,
    }


def _replace_rendered(
    manuscript: Mapping[str, Any],
    rendered: Mapping[str, Any],
    references: Sequence[Reference],
) -> dict[str, Any]:
    by_id = {reference.record_id: reference for reference in references}
    by_doi = {reference.doi: reference for reference in references if reference.doi}
    order = list(rendered["cited_reference_ids"])
    formatted = format_citations(rendered["citation_style"], [by_id[key] for key in order])
    labels = dict(zip(order, formatted["in_text"], strict=True))

    def resolve(token: str) -> Reference:
        key = token.strip().lstrip("@").strip()
        reference = by_id.get(key) or by_doi.get(key)
        if reference is None:
            raise ManuscriptError(f"Unknown citation key: {key}")
        return reference

    def replace_text(text: str) -> str:
        def replacement(match: re.Match[str]) -> str:
            tokens = [resolve(token) for token in match.group(1).split(";")]
            if rendered["citation_style"] == "gb-t-7714-2025":
                numbers = []
                for reference in tokens:
                    numbers.extend(
                        part.strip()
                        for part in labels[reference.record_id].strip("[]").split(",")
                    )
                return "[" + ",".join(dict.fromkeys(numbers)) + "]"
            return "(" + "; ".join(
                labels[reference.record_id].strip("()") for reference in tokens
            ) + ")"

        return CITATION_RE.sub(replacement, text)

    output = json.loads(json.dumps(manuscript))
    output["sections"] = []
    for section in manuscript["sections"]:
        output["sections"].append(
            {
                **section,
                "paragraphs": [replace_text(str(text)) for text in section.get("paragraphs", [])],
            }
        )
    output["abstract"] = replace_text(str(manuscript.get("abstract", "")))
    output["figures"] = [
        {
            **figure,
            "caption": replace_text(str(figure.get("caption", ""))),
        }
        for figure in manuscript.get("figures", [])
    ]
    output["tables"] = [
        {
            **table,
            "caption": replace_text(str(table.get("caption", ""))),
            "headers": [replace_text(str(cell)) for cell in table.get("headers", [])],
            "rows": [
                [replace_text(str(cell)) for cell in row]
                for row in table.get("rows", [])
            ],
        }
        for table in manuscript.get("tables", [])
    ]
    return output


def latex_document(
    manuscript: Mapping[str, Any],
    rendered: Mapping[str, Any],
    references: Sequence[Reference],
) -> str:
    content = _replace_rendered(manuscript, rendered, references)
    lines = [
        r"\documentclass[12pt]{article}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{hyperref}",
        r"\usepackage{graphicx}",
        r"\title{" + _latex_escape(str(content.get("title", ""))) + "}",
        r"\author{" + r" \and ".join(_latex_escape(author) for author in content.get("authors", [])) + "}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
    ]
    abstract = str(content.get("abstract", "")).strip()
    if abstract:
        lines.extend(
            [
                r"\begin{abstract}",
                _latex_escape(abstract),
                r"\end{abstract}",
            ]
        )
    for section in content["sections"]:
        level = int(section.get("level", 1))
        command = "section" if level <= 1 else "subsection" if level == 2 else "subsubsection"
        heading = section.get("heading")
        if heading:
            lines.append(f"\\{command}{{{_latex_escape(str(heading))}}}")
        for paragraph in section.get("paragraphs", []):
            lines.extend([_latex_escape(str(paragraph)), ""])
    for index, figure in enumerate(content.get("figures", []), 1):
        path = Path(str(figure.get("path", ""))).expanduser().resolve()
        if not path.is_file():
            raise ManuscriptError(f"Figure file not found: {path}")
        label = str(figure.get("label") or f"fig:{index}")
        lines.extend(
            [
                r"\begin{figure}[htbp]",
                r"\centering",
                r"\includegraphics[width=\linewidth]{\detokenize{" + path.as_posix() + "}}",
                r"\caption{" + _latex_escape(str(figure.get("caption", ""))) + "}",
                r"\label{" + _latex_escape(label) + "}",
                r"\end{figure}",
                "",
            ]
        )
    for index, table in enumerate(content.get("tables", []), 1):
        headers = table.get("headers", [])
        rows = table.get("rows", [])
        if not headers:
            raise ManuscriptError("Table output requires headers.")
        columns = "l" * len(headers)
        label = str(table.get("label") or f"tab:{index}")
        lines.extend(
            [
                r"\begin{table}[htbp]",
                r"\centering",
                r"\begin{tabular}{" + columns + "}",
                " & ".join(_latex_escape(str(cell)) for cell in headers) + r" \\ \hline",
            ]
        )
        for row in rows:
            lines.append(" & ".join(_latex_escape(str(cell)) for cell in row) + r" \\")
        lines.extend(
            [
                r"\end{tabular}",
                r"\caption{" + _latex_escape(str(table.get("caption", ""))) + "}",
                r"\label{" + _latex_escape(label) + "}",
                r"\end{table}",
                "",
            ]
        )
    if rendered["bibliography"]:
        lines.extend([r"\section*{References}", r"\begin{list}{}{}"])
        for entry in rendered["bibliography"]:
            lines.extend([r"\item " + _latex_escape(entry), ""])
        lines.append(r"\end{list}")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def docx_document(
    path: Path,
    manuscript: Mapping[str, Any],
    rendered: Mapping[str, Any],
    references: Sequence[Reference],
) -> None:
    try:
        from docx import Document
    except ImportError as exc:
        raise ManuscriptError(
            "DOCX output requires python-docx. Install the writing dependencies."
        ) from exc
    content = _replace_rendered(manuscript, rendered, references)
    document = Document()
    document.add_heading(str(content.get("title", "Untitled")), level=0)
    if content.get("authors"):
        document.add_paragraph("; ".join(str(author) for author in content["authors"]))
    abstract = str(content.get("abstract", "")).strip()
    if abstract:
        document.add_heading("Abstract", level=1)
        document.add_paragraph(abstract)
    for section in content["sections"]:
        if section.get("heading"):
            document.add_heading(
                str(section["heading"]),
                level=max(1, min(3, int(section.get("level", 1)))),
            )
        for paragraph in section.get("paragraphs", []):
            document.add_paragraph(str(paragraph))
    for index, figure in enumerate(content.get("figures", []), 1):
        path = Path(str(figure.get("path", ""))).expanduser().resolve()
        if not path.is_file():
            raise ManuscriptError(f"Figure file not found: {path}")
        document.add_picture(str(path), width=None)
        document.add_paragraph(f"Figure {index}. {figure.get('caption', '')}")
    for index, table_data in enumerate(content.get("tables", []), 1):
        headers = table_data.get("headers", [])
        rows = table_data.get("rows", [])
        if not headers:
            raise ManuscriptError("Table output requires headers.")
        document.add_paragraph(f"Table {index}. {table_data.get('caption', '')}")
        table = document.add_table(rows=1, cols=len(headers))
        for cell, value in zip(table.rows[0].cells, headers, strict=True):
            cell.text = str(value)
        for row in rows:
            cells = table.add_row().cells
            for cell, value in zip(cells, row, strict=False):
                cell.text = str(value)
    if rendered["bibliography"]:
        document.add_heading("References", level=1)
        for entry in rendered["bibliography"]:
            document.add_paragraph(entry)
    document.save(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scholarly_manuscript.py",
        description="Build LaTeX and DOCX manuscripts from a research workspace.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Build manuscript output files.")
    build.add_argument("--project-dir", type=Path, required=True)
    build.add_argument("--manuscript", type=Path, required=True)
    build.add_argument("--output-prefix", type=Path, required=True)
    build.add_argument("--formats", default="latex", help="Comma-separated latex,docx.")
    build.add_argument("--citation-style", choices=("apa", "gb-t-7714-2025"))
    build.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    build.add_argument("--overwrite", action="store_true")
    return parser


def _resolve_manuscript_assets(manuscript: dict[str, Any], base: Path) -> None:
    for figure in manuscript.get("figures", []):
        raw = Path(str(figure.get("path", ""))).expanduser()
        if not raw.is_absolute():
            figure["path"] = str((base / raw).resolve())


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manuscript = json.loads(args.manuscript.read_text(encoding="utf-8"))
        if not isinstance(manuscript, dict):
            raise ManuscriptError("Manuscript must be a JSON object.")
        _resolve_manuscript_assets(
            manuscript,
            args.manuscript.expanduser().resolve().parent,
        )
        references = load_project_references(args.project_dir)
        style = args.citation_style or str(manuscript.get("citation_style", "apa"))
        style_file = {
            "apa": "apa.csl",
            "gb-t-7714-2025": "gbt7714-2025-numeric.csl",
        }.get(style)
        if style_file is None:
            raise ManuscriptError(f"Unsupported citation style: {style}")
        style_path = args.skill_root.resolve() / "assets" / "citation-styles" / style_file
        rendered = render_manuscript(manuscript, references, style, style_path)
        formats = {item.strip().casefold() for item in args.formats.split(",") if item.strip()}
        unknown = formats - {"latex", "docx"}
        if unknown:
            raise ManuscriptError(f"Unknown output format(s): {', '.join(sorted(unknown))}")
        if not formats:
            raise ManuscriptError("At least one output format is required.")
        prefix = args.output_prefix.expanduser().resolve()
        outputs = {}
        if "latex" in formats:
            outputs["latex"] = prefix.with_suffix(".tex")
        if "docx" in formats:
            outputs["docx"] = prefix.with_suffix(".docx")
        report_path = prefix.with_suffix(".build.json")
        for path in (*outputs.values(), report_path):
            if path.exists() and not args.overwrite:
                raise ManuscriptError(f"Output already exists: {path}. Pass --overwrite.")
        prefix.parent.mkdir(parents=True, exist_ok=True)
        temp_docx = None
        try:
            if "docx" in outputs:
                handle = tempfile.NamedTemporaryFile(
                    delete=False,
                    dir=prefix.parent,
                    prefix=f".{prefix.stem}.",
                    suffix=".docx",
                )
                temp_docx = Path(handle.name)
                handle.close()
                docx_document(temp_docx, manuscript, rendered, references)
            if "latex" in outputs:
                latex_text = latex_document(manuscript, rendered, references)
                temporary = outputs["latex"].with_suffix(".tex.tmp")
                temporary.write_text(latex_text, encoding="utf-8")
                temporary.replace(outputs["latex"])
            if temp_docx is not None:
                temp_docx.replace(outputs["docx"])
        finally:
            if temp_docx is not None and temp_docx.exists():
                temp_docx.unlink()
        artifacts = {
            kind: {
                "path": str(path),
                "sha256": _sha256(path),
                "artifact_id": record_artifact(args.project_dir, path, kind),
            }
            for kind, path in outputs.items()
        }
        report = {
            **rendered,
            "outputs": artifacts,
        }
        temporary_report = report_path.with_suffix(".build.json.tmp")
        temporary_report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_report.replace(report_path)
        print(f"Manuscript outputs: {', '.join(str(path) for path in outputs.values())}")
        print(f"Build report: {report_path}")
        print(f"Citation engine: {rendered['citation_engine']}")
        for warning in rendered["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        return 0
    except (ManuscriptError, OSError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
