#!/usr/bin/env python3
"""Run protocol-driven systematic-review screening on Scholarime references."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scholarime" / "scripts"
VENDOR = ROOT / "scholarime" / "vendor" / "academic_research_skills" / "sr_screener"
VENDOR_SCRIPTS = VENDOR / "scripts"


class ScreeningError(ValueError):
    """A user-correctable screening error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_script(script: str, args: Sequence[str]) -> int:
    path = VENDOR_SCRIPTS / script
    if not path.is_file():
        print(f"error: bundled screening tool is missing: {path}", file=sys.stderr)
        return 2
    return subprocess.run(
        [sys.executable, str(path), *args],
        check=False,
    ).returncode


def _load_references(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScreeningError(f"Could not read reference file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ScreeningError(f"Reference file is invalid JSON: {exc}") from exc
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = (
            payload.get("candidates")
            or payload.get("references")
            or payload.get("results")
            or []
        )
    else:
        raise ScreeningError("Reference JSON must contain a list or object.")
    references = [
        dict(item)
        for item in items
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]
    if not references:
        raise ScreeningError(f"No usable references found in {path}.")
    return references


def _ris_type(item_type: str) -> str:
    return {
        "article": "JOUR",
        "journal-article": "JOUR",
        "book": "BOOK",
        "book-chapter": "CHAP",
        "chapter": "CHAP",
        "conference": "CPAPER",
        "conference-paper": "CPAPER",
        "report": "RPRT",
        "thesis": "THES",
        "webpage": "ELEC",
        "web": "ELEC",
    }.get(item_type.casefold(), "GEN")


def _ris_line(tag: str, value: Any) -> str | None:
    text = str(value or "").strip()
    return f"{tag}  - {text}" if text else None


def references_to_ris(references: Sequence[Mapping[str, Any]], path: Path) -> None:
    lines: list[str] = []
    for reference in references:
        lines.append(f"TY  - {_ris_type(str(reference.get('item_type') or 'article'))}")
        for tag, key in (
            ("TI", "title"),
            ("PY", "year"),
            ("JO", "container"),
            ("VL", "volume"),
            ("IS", "issue"),
            ("DO", "doi"),
            ("UR", "url"),
            ("AB", "abstract"),
        ):
            line = _ris_line(tag, reference.get(key))
            if line:
                lines.append(line)
        pages = str(reference.get("pages") or "").strip()
        if pages:
            first, separator, last = pages.partition("-")
            lines.append(f"SP  - {first.strip()}")
            if separator and last.strip():
                lines.append(f"EP  - {last.strip()}")
        for author in reference.get("authors") or []:
            line = _ris_line("AU", author)
            if line:
                lines.append(line)
        for keyword in reference.get("keywords") or []:
            line = _ris_line("KW", keyword)
            if line:
                lines.append(line)
        status = reference.get("verification_status") or ""
        record_id = reference.get("record_id") or ""
        note = "; ".join(
            part
            for part in (
                f"Scholarime record_id: {record_id}" if record_id else "",
                f"verification: {status}" if status else "",
            )
            if part
        )
        if note:
            lines.append(f"N1  - {note}")
        lines.append("ER  -")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _record_workspace_artifacts(
    workspace: Path | None,
    paths: Sequence[Path],
    kind: str,
) -> None:
    if workspace is None:
        return
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from research_workspace import record_artifact

    for path in paths:
        if path.is_file():
            record_artifact(workspace, path, kind)


def initialize(work: Path, force: bool = False) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    config = work / "screening_config.json"
    protocol = work / "screening_protocol.md"
    sources = (
        (VENDOR / "templates" / "screening_config.template.json", config),
        (VENDOR / "references" / "protocol_template.md", protocol),
    )
    for source, destination in sources:
        if destination.exists() and not force:
            raise ScreeningError(
                f"Refusing to overwrite {destination}. Pass --force."
            )
        shutil.copy2(source, destination)
    return [config, protocol]


def prepare(
    work: Path,
    inputs: Sequence[Path],
    *,
    config: Path | None,
    force: bool,
    db_labels: Sequence[str],
    workspace: Path | None,
) -> int:
    with tempfile.TemporaryDirectory(prefix="scholarime-screening-") as temporary:
        converted: list[Path] = []
        extra_db = list(db_labels)
        for index, source in enumerate(inputs, 1):
            if source.suffix.casefold() == ".json":
                destination = Path(temporary) / f"scholarime-{index}.ris"
                references_to_ris(_load_references(source), destination)
                converted.append(destination)
                extra_db.append(f"{destination.name}=Scholarime")
            else:
                converted.append(source)
        args = ["--inputs", *map(str, converted), "--work", str(work)]
        if config:
            args.extend(["--config", str(config)])
        if force:
            args.append("--force")
        for label in extra_db:
            args.extend(["--db", label])
        code = _run_script("prepare_records.py", args)
    if code == 0:
        _record_workspace_artifacts(
            workspace,
            [work / "records.json", work / "identification.json"],
            "screening-preparation",
        )
    return code


def _source_selection(
    work: Path,
    stage: str,
    selection: str,
) -> list[dict[str, Any]]:
    try:
        records = json.loads((work / "records.json").read_text(encoding="utf-8"))
        decisions = json.loads(
            (work / ("decisions.json" if stage == "ta" else "ft_decisions.json")).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ScreeningError(f"Could not read screening outputs: {exc}") from exc
    selected_ids: set[str] = set()
    for record_id, decision in decisions.items():
        final = decision.get("final") or {}
        label = final.get("d")
        keep = (
            selection == "all"
            or (selection == "advanced" and label in {"include", "unclear"})
            or (selection == "included" and label == "include")
        )
        if keep:
            selected_ids.add(record_id)
    results = []
    for record in records.get("unique") or []:
        if record.get("id") not in selected_ids:
            continue
        results.append(
            {
                "record_id": record.get("id"),
                "title": record.get("title"),
                "authors": record.get("authors") or [],
                "year": record.get("year") or None,
                "container": record.get("journal") or "",
                "doi": record.get("doi") or "",
                "source": "Scholarime screening",
                "source_id": record.get("pmid") or record.get("doi") or record.get("id"),
                "item_type": "article",
                "abstract": record.get("abstract") or "",
                "keywords": record.get("kw") or [],
                "screening_stage": stage,
                "screening_batch": record.get("batch") or "",
            }
        )
    return results


def build_outputs(
    work: Path,
    output: Path,
    *,
    stage: str,
    config: Path | None,
    tag_keywords: bool,
    workspace: Path | None,
    selection: str,
) -> int:
    args = ["--work", str(work), "--out", str(output), "--stage", stage]
    if config:
        args.extend(["--config", str(config)])
    if tag_keywords:
        args.append("--tag-keywords")
    code = _run_script("build_outputs.py", args)
    if code:
        return code
    references = _source_selection(work, stage, selection)
    source_file = output / f"{stage.upper()}_scholarime_sources.json"
    source_file.write_text(
        json.dumps(
            {
                "version": VERSION,
                "generated_at": utc_now(),
                "screening_work": str(work.resolve()),
                "selection": selection,
                "results": references,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Scholarime sources: {source_file}")
    if workspace is not None and references:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        from research_workspace import add_sources

        imported = add_sources(workspace, source_file)
        print(f"Workspace sources imported or updated: {imported}")
    _record_workspace_artifacts(
        workspace,
        [
            output / f"{stage.upper()}_prisma_counts.json",
            output / f"{stage.upper()}_prisma_counts.md",
            source_file,
        ],
        f"screening-{stage}",
    )
    return 0


def status(work: Path) -> int:
    required = ("records.json", "manifest.json", "identification.json")
    present = {name: (work / name).is_file() for name in required}
    print(f"Screening work: {work.resolve()}")
    for name, exists in present.items():
        print(f"{name}: {'yes' if exists else 'no'}")
    if (work / "decisions.json").is_file():
        decisions = json.loads((work / "decisions.json").read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for decision in decisions.values():
            label = str((decision.get("final") or {}).get("d") or "pending")
            counts[label] = counts.get(label, 0) + 1
        print(f"Title/abstract decisions: {json.dumps(counts, ensure_ascii=False)}")
    return 0 if all(present.values()) else 2


def _init_parser(command: str) -> argparse.ArgumentParser:
    if command == "init":
        parser = argparse.ArgumentParser(prog="scholarime screening init")
        parser.add_argument("work", type=Path)
        parser.add_argument("--force", action="store_true")
        return parser
    if command == "prepare":
        parser = argparse.ArgumentParser(prog="scholarime screening prepare")
        parser.add_argument("--work", type=Path, required=True)
        parser.add_argument("--inputs", nargs="+", type=Path, required=True)
        parser.add_argument("--config", type=Path)
        parser.add_argument("--workspace", type=Path)
        parser.add_argument("--db", action="append", default=[])
        parser.add_argument("--force", action="store_true")
        return parser
    if command == "outputs":
        parser = argparse.ArgumentParser(prog="scholarime screening outputs")
        parser.add_argument("--work", type=Path, required=True)
        parser.add_argument("--out", type=Path, required=True)
        parser.add_argument("--stage", choices=("ta", "ft"), default="ta")
        parser.add_argument("--config", type=Path)
        parser.add_argument("--workspace", type=Path)
        parser.add_argument(
            "--selection",
            choices=("advanced", "included", "all"),
            default="advanced",
        )
        parser.add_argument("--tag-keywords", action="store_true")
        return parser
    parser = argparse.ArgumentParser(prog="scholarime screening status")
    parser.add_argument("--work", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: scholarime screening <init|prepare|workflow|merge|outputs|status> [options]\n\n"
            "  init      copy the protocol and screening configuration templates\n"
            "  prepare   import RIS/NBIB/CSV or Scholarime reference JSON and de-duplicate records\n"
            "  workflow  generate blinded screening prompts or workflow files\n"
            "  merge     merge reviewer decisions, adjudications, QC, and pending work\n"
            "  outputs   write PRISMA, logs, RIS groups, methods text, and workspace sources\n"
            "  status    summarize prepared and decided records\n\n"
            "Direct `workflow` and `merge` options are passed to the bundled sr-screener tools;\n"
            "run `scholarime screening workflow --help` for their full interface."
        )
        return 0
    command = args.pop(0)
    if command == "workflow":
        return _run_script("build_workflow.py", args)
    if command == "merge":
        return _run_script("merge_decisions.py", args)
    if command not in {"init", "prepare", "outputs", "status"}:
        print(f"error: unknown screening command {command!r}", file=sys.stderr)
        return 2
    parsed = _init_parser(command).parse_args(args)
    try:
        if command == "init":
            paths = initialize(parsed.work, parsed.force)
            for path in paths:
                print(f"Created: {path}")
            return 0
        if command == "prepare":
            return prepare(
                parsed.work,
                parsed.inputs,
                config=parsed.config,
                force=parsed.force,
                db_labels=parsed.db,
                workspace=parsed.workspace,
            )
        if command == "outputs":
            return build_outputs(
                parsed.work,
                parsed.out,
                stage=parsed.stage,
                config=parsed.config,
                tag_keywords=parsed.tag_keywords,
                workspace=parsed.workspace,
                selection=parsed.selection,
            )
        return status(parsed.work)
    except (OSError, ScreeningError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
