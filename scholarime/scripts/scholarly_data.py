#!/usr/bin/env python3
"""Profile and reproducibly transform academic tabular data."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import math
import operator
import re
import statistics
import sys
import tempfile
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import cmp_to_key, lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


VERSION = "1.0.0"
DEFAULT_NULL_TOKENS = ("", "na", "n/a", "nan", "null", "none", "missing", "未填", "缺失")
SUPPORTED_INPUTS = {".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".xlsx", ".xlsm"}
SUPPORTED_OUTPUTS = {".csv", ".tsv", ".json", ".jsonl", ".xlsx"}
SUPPORTED_OPERATIONS = {
    "rename",
    "select",
    "drop_columns",
    "trim_whitespace",
    "convert",
    "map_values",
    "fill_missing",
    "drop_missing",
    "deduplicate",
    "filter",
    "derive",
    "sort",
    "groupby",
    "limit",
}
IDENTIFIER_HINT_RE = re.compile(
    r"(^|[_\s-])(id|uid|guid|uuid|code|key|编号|编码|学号|工号|身份证|手机号|电话|邮编|zip)([_\s-]|$)",
    re.IGNORECASE,
)
INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")
DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
)


class DataError(ValueError):
    """A user-correctable data processing error."""


@dataclass
class InputOptions:
    encoding: str = "auto"
    delimiter: str = "auto"
    header_row: int = 1
    sheet: str | None = None
    null_tokens: tuple[str, ...] = DEFAULT_NULL_TOKENS


@dataclass
class DataTable:
    headers: list[str]
    rows: list[dict[str, Any]]
    source: Path | None = None
    source_format: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "DataTable":
        return DataTable(
            headers=list(self.headers),
            rows=[dict(row) for row in self.rows],
            source=self.source,
            source_format=self.source_format,
            metadata=dict(self.metadata),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_null_tokens(values: Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return DEFAULT_NULL_TOKENS
    result = tuple(dict.fromkeys(value.strip().casefold() for value in values))
    return result or DEFAULT_NULL_TOKENS


@lru_cache(maxsize=32)
def _null_token_set(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(values)


def is_missing(value: Any, null_tokens: Sequence[str]) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _null_token_set(tuple(null_tokens))
    return False


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    if INT_RE.fullmatch(text):
        return int(text)
    return None


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = str(value).strip()
    if not FLOAT_RE.fullmatch(text):
        return None
    try:
        result = float(text.replace(",", ""))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return None
    text = str(value).strip().casefold()
    if text in {"true", "yes", "y", "是"}:
        return True
    if text in {"false", "no", "n", "否"}:
        return False
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed
    except ValueError:
        pass
    for pattern in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _is_integer_like(value: Any) -> bool:
    return _parse_int(value) is not None


def _is_float_like(value: Any) -> bool:
    return _parse_float(value) is not None


def _is_date_like(value: Any) -> bool:
    return _parse_date(value) is not None


def _is_datetime_like(value: Any) -> bool:
    return _parse_datetime(value) is not None


def _read_text(path: Path, requested: str) -> tuple[str, str]:
    encodings = (
        (requested,)
        if requested and requested.casefold() != "auto"
        else ("utf-8-sig", "utf-8", "gb18030", "utf-16")
    )
    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise DataError(
        f"Could not decode {path} with {', '.join(encodings)}. "
        "Use --encoding explicitly."
    ) from last_error


def _detect_delimiter(text: str, requested: str) -> str:
    named = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|", "\\t": "\t"}
    if requested and requested.casefold() != "auto":
        resolved = named.get(requested.casefold(), requested)
        if len(resolved) != 1:
            raise DataError("delimiter must be one character or comma/semicolon/tab/pipe.")
        return resolved
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = next((line for line in sample.splitlines() if line.strip()), "")
        counts = {candidate: first.count(candidate) for candidate in (",", ";", "\t", "|")}
        delimiter = max(counts, key=counts.get)
        return delimiter if counts[delimiter] else ","


def _clean_rows(raw_rows: Iterable[Sequence[Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for raw in raw_rows:
        row = list(raw)
        while row and (row[-1] is None or str(row[-1]).strip() == ""):
            row.pop()
        if any(value is not None and str(value).strip() for value in row):
            rows.append(row)
    return rows


def _headers_from_rows(
    rows: list[list[Any]],
    header_row: int,
) -> tuple[list[str], list[list[Any]], list[list[Any]]]:
    if not rows:
        raise DataError("Input contains no records.")
    width = max(len(row) for row in rows)
    rows = [row + [None] * (width - len(row)) for row in rows]
    if header_row == 0:
        headers = [f"Column {index + 1}" for index in range(width)]
        return headers, rows, rows
    if header_row < 0 or header_row > len(rows):
        raise DataError(f"header_row={header_row} is outside the available rows.")
    header_values = rows[header_row - 1]
    headers = [
        str(value).strip() if value is not None and str(value).strip() else f"Column {index + 1}"
        for index, value in enumerate(header_values)
    ]
    if len(headers) != len(set(headers)):
        duplicates = [name for name, count in Counter(headers).items() if count > 1]
        raise DataError(f"Duplicate column names: {', '.join(duplicates)}")
    data_rows = rows[header_row:]
    return headers, data_rows, rows[:header_row]


def _table_from_rows(
    rows: list[list[Any]],
    header_row: int,
    metadata: Mapping[str, Any],
) -> DataTable:
    headers, data_rows, prefix = _headers_from_rows(rows, header_row)
    records = []
    for row in data_rows:
        records.append(
            OrderedDict(
                (header, value if value != "" else None) for header, value in zip(headers, row)
            )
        )
    table_metadata = dict(metadata)
    table_metadata["ignored_prefix_rows"] = len(prefix)
    return DataTable(headers=headers, rows=records, metadata=table_metadata)


def read_table(path: Path | str, options: InputOptions) -> DataTable:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise DataError(f"Input file not found: {source}")
    suffix = source.suffix.casefold()
    if suffix not in SUPPORTED_INPUTS:
        raise DataError(
            f"Unsupported input format {suffix or '(none)'}; "
            f"expected {', '.join(sorted(SUPPORTED_INPUTS))}."
        )

    if suffix in {".csv", ".tsv"}:
        text, encoding = _read_text(source, options.encoding)
        delimiter = _detect_delimiter(text, options.delimiter or ("\t" if suffix == ".tsv" else "auto"))
        raw_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        table = _table_from_rows(
            _clean_rows(raw_rows),
            options.header_row,
            {
                "encoding": encoding,
                "delimiter": delimiter,
                "header_row": options.header_row,
            },
        )
    elif suffix in {".json", ".jsonl", ".ndjson"}:
        if suffix == ".json":
            text, encoding = _read_text(source, options.encoding)
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DataError(f"Invalid JSON: {exc}") from exc
            if isinstance(payload, dict):
                payload = payload.get("records") or payload.get("data")
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise DataError("JSON input must be a list of objects or contain records/data.")
            records = payload
            headers = list(dict.fromkeys(key for record in records for key in record))
            rows = [
                OrderedDict((header, record.get(header)) for header in headers)
                for record in records
            ]
            table = DataTable(
                headers=headers,
                rows=rows,
                metadata={"encoding": encoding, "header_row": 1},
            )
        else:
            text, encoding = _read_text(source, options.encoding)
            records = []
            for line_number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DataError(f"Invalid JSONL at line {line_number}: {exc}") from exc
                if not isinstance(record, dict):
                    raise DataError(f"JSONL line {line_number} is not an object.")
                records.append(record)
            headers = list(dict.fromkeys(key for record in records for key in record))
            table = DataTable(
                headers=headers,
                rows=[OrderedDict((header, record.get(header)) for header in headers) for record in records],
                metadata={"encoding": encoding, "header_row": 1},
            )
    else:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise DataError(
                "Excel support requires openpyxl. Install "
                "scholarime/install/dependencies.txt."
            ) from exc
        workbook = load_workbook(source, read_only=True, data_only=True)
        try:
            if options.sheet:
                if options.sheet not in workbook.sheetnames:
                    raise DataError(
                        f"Sheet {options.sheet!r} not found. Available: "
                        f"{', '.join(workbook.sheetnames)}"
                    )
                sheet = workbook[options.sheet]
            else:
                sheet = workbook.active
            raw_rows = list(sheet.iter_rows(values_only=True))
            table = _table_from_rows(
                _clean_rows(raw_rows),
                options.header_row,
                {
                    "encoding": "xlsx",
                    "delimiter": "",
                    "header_row": options.header_row,
                    "sheet": sheet.title,
                },
            )
        finally:
            workbook.close()

    table.source = source
    table.source_format = suffix.lstrip(".")
    table.metadata["source_sha256"] = _sha256_file(source)
    return table


def _clean_string(value: Any) -> str:
    return str(value).strip()


def _non_missing(values: Sequence[Any], null_tokens: Sequence[str]) -> list[Any]:
    return [value for value in values if not is_missing(value, null_tokens)]


def _unique_ratio(values: Sequence[Any]) -> float:
    return len(set(map(_hashable, values))) / len(values) if values else 0.0


def _hashable(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _quantile(sorted_values: Sequence[float], proportion: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * proportion
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _infer_column(
    name: str,
    values: Sequence[Any],
    null_tokens: Sequence[str],
    include_samples: bool,
) -> dict[str, Any]:
    present = _non_missing(values, null_tokens)
    total = len(values)
    missing = total - len(present)
    unique = len({_hashable(value) for value in present})
    unique_ratio = unique / len(present) if present else 0.0
    candidate_types = {
        "boolean": sum(_parse_bool(value) is not None for value in present),
        "integer": sum(_parse_int(value) is not None for value in present),
        "float": sum(_parse_float(value) is not None for value in present),
        "date": sum(_parse_date(value) is not None for value in present),
        "datetime": sum(_parse_datetime(value) is not None for value in present),
    } if present else {key: 0 for key in ("boolean", "integer", "float", "date", "datetime")}
    present_count = len(present)

    inferred_type = "unknown"
    confidence = 0.0
    if present:
        if candidate_types["boolean"] == present_count:
            inferred_type, confidence = "boolean", 1.0
        elif candidate_types["integer"] == present_count:
            inferred_type, confidence = "integer", 1.0
        elif candidate_types["float"] == present_count:
            inferred_type, confidence = "float", 1.0
        elif candidate_types["date"] == present_count:
            inferred_type, confidence = "date", 1.0
        elif candidate_types["datetime"] == present_count:
            inferred_type, confidence = "datetime", 1.0
        else:
            numeric_ratio = max(
                candidate_types["integer"],
                candidate_types["float"],
            ) / present_count
            if 0.2 <= numeric_ratio < 1:
                inferred_type = "mixed"
                confidence = round(max(numeric_ratio, 1 - numeric_ratio), 3)
            elif unique_ratio <= 0.5 or unique <= 20:
                inferred_type, confidence = "categorical", 1.0
            else:
                inferred_type, confidence = "text", round(1 - numeric_ratio, 3)

    leading_zero = any(
        isinstance(value, str)
        and re.fullmatch(r"[+-]?0\d+", value.strip())
        for value in present
    )
    possible_identifier = bool(
        IDENTIFIER_HINT_RE.search(name)
        or (leading_zero and unique_ratio > 0.8)
        or (unique_ratio == 1 and present_count >= 3 and all(len(_clean_string(v)) <= 48 for v in present))
    )
    if possible_identifier and inferred_type in {"integer", "float", "text", "mixed"}:
        role = "identifier"
    elif inferred_type == "categorical":
        role = "category"
    else:
        role = "measure"

    stats: dict[str, Any] = {}
    if inferred_type in {"integer", "float"}:
        numeric_values = [
            number
            for number in (_parse_float(value) for value in present)
            if number is not None
        ]
        if numeric_values:
            ordered = sorted(numeric_values)
            q1 = _quantile(ordered, 0.25)
            q3 = _quantile(ordered, 0.75)
            iqr = q3 - q1
            low_bound = q1 - 1.5 * iqr
            high_bound = q3 + 1.5 * iqr
            stats = {
                "min": min(ordered),
                "max": max(ordered),
                "mean": round(statistics.fmean(ordered), 6),
                "median": round(statistics.median(ordered), 6),
                "q1": round(q1, 6),
                "q3": round(q3, 6),
                "outlier_count": sum(
                    number < low_bound or number > high_bound for number in ordered
                ) if len(ordered) >= 4 else 0,
            }
    elif inferred_type in {"date", "datetime"}:
        parsed = [
            parsed_value
            for parsed_value in (
                _parse_datetime(value) if inferred_type == "datetime" else _parse_date(value)
                for value in present
            )
            if parsed_value is not None
        ]
        if parsed:
            stats = {
                "min": min(parsed).isoformat(),
                "max": max(parsed).isoformat(),
            }
    if inferred_type in {"text", "categorical", "mixed"} and present:
        lengths = [len(_clean_string(value)) for value in present]
        stats = {
            "min_length": min(lengths),
            "max_length": max(lengths),
            "mean_length": round(statistics.fmean(lengths), 3),
        }
        if include_samples:
            stats["top_values"] = Counter(_clean_string(value) for value in present).most_common(5)

    whitespace_count = sum(
        isinstance(value, str) and value != value.strip() for value in present
    )
    result = {
        "name": name,
        "inferred_type": inferred_type,
        "semantic_role": role,
        "type_confidence": confidence,
        "candidate_type_counts": candidate_types,
        "total_rows": total,
        "non_missing": len(present),
        "missing": missing,
        "missing_rate": round(missing / total, 6) if total else 0.0,
        "unique": unique,
        "unique_ratio": round(unique_ratio, 6),
        "whitespace_values": whitespace_count,
        "leading_zero_values": sum(
            isinstance(value, str) and bool(re.fullmatch(r"[+-]?0\d+", value.strip()))
            for value in present
        ),
        "possible_identifier": possible_identifier,
        "stats": stats,
    }
    if include_samples:
        result["sample_values"] = [_artifact_value(value) for value in present[:5]]
    return result


def _artifact_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def profile_table(
    table: DataTable,
    options: InputOptions,
    include_samples: bool = False,
) -> dict[str, Any]:
    columns = [
        _infer_column(
            header,
            [row.get(header) for row in table.rows],
            options.null_tokens,
            include_samples,
        )
        for header in table.headers
    ]
    exact_rows = [
        tuple(_hashable(row.get(header)) for header in table.headers)
        for row in table.rows
    ]
    duplicate_rows = len(exact_rows) - len(set(exact_rows))
    issues: list[dict[str, str]] = []
    if duplicate_rows:
        issues.append(
            {
                "level": "warning",
                "topic": "duplicates",
                "message": f"{duplicate_rows} exact duplicate row(s) detected.",
            }
        )
    for column in columns:
        if column["missing_rate"] == 1:
            issues.append(
                {
                    "level": "warning",
                    "topic": "all_missing",
                    "message": f"Column {column['name']!r} is entirely missing.",
                }
            )
        elif column["missing_rate"] >= 0.2:
            issues.append(
                {
                    "level": "notice",
                    "topic": "missingness",
                    "message": f"Column {column['name']!r} has {column['missing_rate']:.1%} missing values.",
                }
            )
        if column["inferred_type"] == "mixed":
            issues.append(
                {
                    "level": "warning",
                    "topic": "mixed_types",
                    "message": f"Column {column['name']!r} contains mixed type patterns.",
                }
            )
        if column["leading_zero_values"]:
            issues.append(
                {
                    "level": "warning",
                    "topic": "preserve_identifiers",
                    "message": (
                        f"Column {column['name']!r} has leading-zero values; "
                        "convert to integer only with explicit intent."
                    ),
                }
            )
        if column["stats"].get("outlier_count"):
            issues.append(
                {
                    "level": "notice",
                    "topic": "outliers",
                    "message": (
                        f"Column {column['name']!r} has "
                        f"{column['stats']['outlier_count']} IQR outlier value(s). "
                        "Outliers are marked, not removed."
                    ),
                }
            )
    return {
        "version": VERSION,
        "generated_at": _utc_now(),
        "source": str(table.source) if table.source else None,
        "source_format": table.source_format,
        "source_sha256": table.metadata.get("source_sha256"),
        "encoding": table.metadata.get("encoding"),
        "delimiter": table.metadata.get("delimiter"),
        "sheet": table.metadata.get("sheet"),
        "header_row": table.metadata.get("header_row"),
        "null_tokens": list(options.null_tokens),
        "row_count": len(table.rows),
        "column_count": len(table.headers),
        "duplicate_rows": duplicate_rows,
        "columns": columns,
        "issues": issues,
    }


def profile_markdown(profile: Mapping[str, Any]) -> str:
    lines = [
        "# Data Profile",
        "",
        f"- Source: `{profile.get('source')}`",
        f"- Rows: {profile['row_count']}",
        f"- Columns: {profile['column_count']}",
        f"- Exact duplicate rows: {profile['duplicate_rows']}",
        f"- Encoding: {profile.get('encoding') or 'n/a'}",
        f"- Delimiter: {repr(profile.get('delimiter')) if profile.get('delimiter') else 'n/a'}",
        f"- Sheet: {profile.get('sheet') or 'n/a'}",
        "",
        "| Column | Type | Role | Missing | Unique | Evidence |",
        "|---|---|---|---:|---:|---|",
    ]
    for column in profile["columns"]:
        evidence = (
            f"confidence {column['type_confidence']:.2f}; "
            f"whitespace {column['whitespace_values']}"
        )
        lines.append(
            "| {name} | {type} | {role} | {missing} ({rate:.1%}) | {unique} | {evidence} |".format(
                name=str(column["name"]).replace("|", "\\|"),
                type=column["inferred_type"],
                role=column["semantic_role"],
                missing=column["missing"],
                rate=column["missing_rate"],
                unique=column["unique"],
                evidence=evidence,
            )
        )
    lines.extend(["", "## Issues", ""])
    if profile["issues"]:
        for issue in profile["issues"]:
            lines.append(f"- **{issue['level']} / {issue['topic']}**: {issue['message']}")
    else:
        lines.append("- No structural issues were detected.")
    lines.extend(
        [
            "",
            "Type inference is evidence for the Agent, not an automatic transformation decision.",
            "Generate a declarative recipe and obtain user confirmation before writing cleaned data.",
            "",
        ]
    )
    return "\n".join(lines)


def _require_columns(headers: Sequence[str], columns: Any, default_all: bool = False) -> list[str]:
    if columns is None and default_all:
        return list(headers)
    if isinstance(columns, str):
        columns = [columns]
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        raise DataError("columns must be a string or a list of column names.")
    if "*" in columns:
        if len(columns) != 1:
            raise DataError("'*' cannot be combined with other column names.")
        return list(headers)
    missing = [column for column in columns if column not in headers]
    if missing:
        raise DataError(f"Unknown column(s): {', '.join(missing)}")
    return columns


def _number(value: Any) -> float | None:
    return _parse_float(value)


def _coerce_compare(left: Any, right: Any) -> tuple[Any, Any]:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is not None and right_number is not None:
        return left_number, right_number
    left_date = _parse_datetime(left)
    right_date = _parse_datetime(right)
    if left_date is not None and right_date is not None:
        return left_date, right_date
    return str(left), str(right)


def _filter_matches(value: Any, condition: Mapping[str, Any], null_tokens: Sequence[str]) -> bool:
    op = str(condition.get("operator", "eq")).casefold()
    if op == "is_null":
        return is_missing(value, null_tokens)
    if op == "not_null":
        return not is_missing(value, null_tokens)
    if is_missing(value, null_tokens):
        return False
    expected = condition.get("value")
    left, right = _coerce_compare(value, expected)
    if op == "eq":
        return left == right
    if op == "neq":
        return left != right
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op in {"in", "not_in"}:
        if not isinstance(expected, list):
            raise DataError(f"{op} requires a list value.")
        values = [_coerce_compare(value, item)[1] for item in expected]
        contains = left in values
        return contains if op == "in" else not contains
    if op == "contains":
        return str(expected).casefold() in str(value).casefold()
    if op == "regex":
        try:
            return bool(re.search(str(expected), str(value)))
        except re.error as exc:
            raise DataError(f"Invalid filter regex: {exc}") from exc
    raise DataError(f"Unsupported filter operator: {op}")


def _convert_value(value: Any, target: str, invalid: str) -> Any:
    target = target.casefold()
    parser: Callable[[Any], Any]
    parser = {
        "string": lambda item: str(item),
        "integer": _parse_int,
        "float": _parse_float,
        "boolean": _parse_bool,
        "date": _parse_date,
        "datetime": _parse_datetime,
    }.get(target)
    if parser is None:
        raise DataError(f"Unsupported conversion target: {target}")
    result = parser(value)
    if result is None and value is not None and str(value).strip() != "":
        if invalid == "null":
            return None
        if invalid == "keep":
            return value
        raise DataError(f"Cannot convert {value!r} to {target}.")
    if isinstance(result, (date, datetime)):
        return result.isoformat()
    return result


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_SAFE_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
    "lower": lambda value: str(value).lower(),
    "upper": lambda value: str(value).upper(),
    "str": str,
    "int": int,
    "float": float,
}


def _evaluate_expression(expression: str, row: Mapping[str, Any]) -> Any:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise DataError(f"Invalid derive expression: {exc}") from exc

    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in row:
                raise DataError(f"Unknown column in derive expression: {node.id}")
            return row[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = evaluate(node.left)
            right = evaluate(node.right)
            if left is None or right is None:
                return None
            return _BINARY_OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            value = evaluate(node.operand)
            return None if value is None else _UNARY_OPERATORS[type(node.op)](value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _SAFE_FUNCTIONS
            and not node.keywords
        ):
            args = [evaluate(argument) for argument in node.args]
            if any(value is None for value in args):
                return None
            return _SAFE_FUNCTIONS[node.func.id](*args)
        raise DataError("Derive expressions allow arithmetic and approved functions only.")

    return evaluate(tree)


def _aggregate(values: Sequence[Any], function: str, column: str) -> Any:
    present = [value for value in values if value is not None and str(value).strip() != ""]
    function = function.casefold()
    if function == "count":
        return len(values) if column == "*" else len(present)
    if function == "nunique":
        return len({_hashable(value) for value in present})
    if function in {"first", "last"}:
        if not present:
            return None
        return present[0] if function == "first" else present[-1]
    numeric = [number for number in (_number(value) for value in present) if number is not None]
    if function in {"sum", "mean", "median", "min", "max"}:
        if not numeric:
            return None
        if function == "sum":
            return sum(numeric)
        if function == "mean":
            return statistics.fmean(numeric)
        if function == "median":
            return statistics.median(numeric)
        return min(numeric) if function == "min" else max(numeric)
    raise DataError(f"Unsupported aggregation function: {function}")


def _sort_value(value: Any) -> tuple[int, Any]:
    if value is None or str(value).strip() == "":
        return (2, "")
    number = _number(value)
    if number is not None:
        return (0, number)
    return (1, str(value).casefold())


def apply_operations(
    table: DataTable,
    operations: Sequence[Mapping[str, Any]],
    null_tokens: Sequence[str],
) -> tuple[DataTable, list[dict[str, Any]]]:
    current = table.copy()
    audit: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise DataError(f"Operation {index} must be an object.")
        op_name = str(operation.get("op", "")).strip().casefold()
        rows_before = len(current.rows)
        columns_before = list(current.headers)
        details: dict[str, Any] = {}
        if op_name == "rename":
            mapping = operation.get("mapping")
            if not isinstance(mapping, Mapping):
                raise DataError("rename requires a mapping object.")
            missing = [old for old in mapping if old not in current.headers]
            if missing:
                raise DataError(f"rename references unknown columns: {', '.join(missing)}")
            new_headers = [str(mapping.get(header, header)) for header in current.headers]
            if len(new_headers) != len(set(new_headers)):
                raise DataError("rename would create duplicate column names.")
            current.rows = [
                OrderedDict(
                    (str(mapping.get(header, header)), value) for header, value in row.items()
                )
                for row in current.rows
            ]
            current.headers = new_headers
            details["mapping"] = dict(mapping)
        elif op_name == "select":
            selected = _require_columns(current.headers, operation.get("columns"))
            current.headers = selected
            current.rows = [
                OrderedDict((header, row.get(header)) for header in selected)
                for row in current.rows
            ]
            details["columns"] = selected
        elif op_name == "drop_columns":
            dropped = _require_columns(current.headers, operation.get("columns"))
            kept = [header for header in current.headers if header not in dropped]
            current.headers = kept
            current.rows = [
                OrderedDict((header, row.get(header)) for header in kept)
                for row in current.rows
            ]
            details["dropped"] = dropped
        elif op_name == "trim_whitespace":
            columns = _require_columns(current.headers, operation.get("columns"), default_all=True)
            changed = 0
            for row in current.rows:
                for column in columns:
                    value = row.get(column)
                    if isinstance(value, str) and value != value.strip():
                        row[column] = value.strip()
                        changed += 1
            details["changed_values"] = changed
        elif op_name == "convert":
            mapping = operation.get("columns")
            if not isinstance(mapping, Mapping):
                raise DataError("convert requires a columns object.")
            invalid = str(operation.get("invalid", "error")).casefold()
            if invalid not in {"error", "null", "keep"}:
                raise DataError("convert invalid must be error, null, or keep.")
            changed = 0
            for column, target in mapping.items():
                _require_columns(current.headers, [column])
                for row_number, row in enumerate(current.rows, 2):
                    value = row.get(column)
                    if is_missing(value, null_tokens):
                        row[column] = None
                        continue
                    try:
                        converted = _convert_value(value, str(target), invalid)
                    except DataError as exc:
                        raise DataError(
                            f"convert failed at row {row_number}, column {column!r}: {exc}"
                        ) from exc
                    if converted != value:
                        row[column] = converted
                        changed += 1
            details["changed_values"] = changed
        elif op_name == "map_values":
            column = str(operation.get("column", ""))
            _require_columns(current.headers, [column])
            mapping = operation.get("mapping")
            if not isinstance(mapping, Mapping):
                raise DataError("map_values requires a mapping object.")
            unmapped = str(operation.get("unmapped", "keep")).casefold()
            if unmapped not in {"keep", "null", "error"}:
                raise DataError("map_values unmapped must be keep, null, or error.")
            changed = 0
            for row_number, row in enumerate(current.rows, 2):
                value = row.get(column)
                if is_missing(value, null_tokens):
                    continue
                key = str(value)
                if key in mapping:
                    row[column] = mapping[key]
                    changed += 1
                elif unmapped == "null":
                    row[column] = None
                    changed += 1
                elif unmapped == "error":
                    raise DataError(
                        f"Unmapped value {value!r} at row {row_number}, column {column!r}."
                    )
            details["changed_values"] = changed
        elif op_name == "fill_missing":
            column = str(operation.get("column", ""))
            _require_columns(current.headers, [column])
            strategy = str(operation.get("strategy", "constant")).casefold()
            values = [row.get(column) for row in current.rows]
            fill_value = operation.get("value")
            if strategy == "constant":
                if "value" not in operation:
                    raise DataError("fill_missing with constant strategy requires value.")
                replacement = fill_value
                if replacement is None:
                    raise DataError("fill_missing constant value must not be null.")
            elif strategy in {"mean", "median"}:
                numeric = [number for number in (_number(value) for value in values) if number is not None]
                if not numeric:
                    raise DataError(f"Cannot use {strategy} on {column!r}: no numeric values.")
                replacement = statistics.fmean(numeric) if strategy == "mean" else statistics.median(numeric)
            elif strategy == "mode":
                present = [value for value in values if not is_missing(value, null_tokens)]
                counts = Counter(_hashable(value) for value in present)
                if counts:
                    mode_key = counts.most_common(1)[0][0]
                    replacement = next(
                        value for value in present if _hashable(value) == mode_key
                    )
                else:
                    raise DataError(f"Cannot use mode on {column!r}: no non-null values.")
            elif strategy in {"forward", "backward"}:
                replacement = None
            else:
                raise DataError(f"Unsupported fill strategy: {strategy}")
            changed = 0
            if strategy in {"forward", "backward"}:
                order = range(len(current.rows))
                if strategy == "backward":
                    order = range(len(current.rows) - 1, -1, -1)
                last = None
                for index in order:
                    value = current.rows[index].get(column)
                    if not is_missing(value, null_tokens):
                        last = value
                    elif last is not None:
                        current.rows[index][column] = last
                        changed += 1
            else:
                for row in current.rows:
                    if is_missing(row.get(column), null_tokens):
                        row[column] = replacement
                        changed += 1
            details["changed_values"] = changed
            details["strategy"] = strategy
        elif op_name == "drop_missing":
            columns = _require_columns(current.headers, operation.get("columns"), default_all=True)
            how = str(operation.get("how", "any")).casefold()
            if how not in {"any", "all"}:
                raise DataError("drop_missing how must be any or all.")
            before = len(current.rows)
            if how == "any":
                current.rows = [
                    row for row in current.rows
                    if not any(is_missing(row.get(column), null_tokens) for column in columns)
                ]
            else:
                current.rows = [
                    row for row in current.rows
                    if not all(is_missing(row.get(column), null_tokens) for column in columns)
                ]
            details["dropped_rows"] = before - len(current.rows)
        elif op_name == "deduplicate":
            subset = _require_columns(
                current.headers,
                operation.get("subset"),
                default_all=True,
            )
            keep = str(operation.get("keep", "first")).casefold()
            if keep not in {"first", "last"}:
                raise DataError("deduplicate keep must be first or last.")
            before = len(current.rows)
            if keep == "first":
                seen = set()
                kept_rows = []
                for row in current.rows:
                    key = tuple(_hashable(row.get(column)) for column in subset)
                    if key not in seen:
                        seen.add(key)
                        kept_rows.append(row)
            else:
                seen = set()
                kept_rows_rev = []
                for row in reversed(current.rows):
                    key = tuple(_hashable(row.get(column)) for column in subset)
                    if key not in seen:
                        seen.add(key)
                        kept_rows_rev.append(row)
                kept_rows = list(reversed(kept_rows_rev))
            current.rows = kept_rows
            details["dropped_rows"] = before - len(current.rows)
        elif op_name == "filter":
            conditions = operation.get("conditions")
            if not isinstance(conditions, list) or not conditions:
                raise DataError("filter requires a non-empty conditions list.")
            conjunction = str(operation.get("match", "all")).casefold()
            if conjunction not in {"all", "any"}:
                raise DataError("filter match must be all or any.")
            before = len(current.rows)

            def keep(row: Mapping[str, Any]) -> bool:
                results = []
                for condition in conditions:
                    if not isinstance(condition, Mapping):
                        raise DataError("Each filter condition must be an object.")
                    column = str(condition.get("column", ""))
                    _require_columns(current.headers, [column])
                    results.append(
                        _filter_matches(row.get(column), condition, null_tokens)
                    )
                return all(results) if conjunction == "all" else any(results)

            current.rows = [row for row in current.rows if keep(row)]
            details["dropped_rows"] = before - len(current.rows)
        elif op_name == "derive":
            column = str(operation.get("column", "")).strip()
            expression = str(operation.get("expression", "")).strip()
            if not column or not expression:
                raise DataError("derive requires column and expression.")
            if column in current.headers:
                raise DataError(f"derive would overwrite existing column {column!r}.")
            for row_number, row in enumerate(current.rows, 2):
                try:
                    row[column] = _evaluate_expression(expression, row)
                except DataError as exc:
                    raise DataError(f"derive failed at row {row_number}: {exc}") from exc
            current.headers.append(column)
            details["expression"] = expression
        elif op_name == "sort":
            by = _require_columns(current.headers, operation.get("by"))
            descending = operation.get("descending", False)
            if isinstance(descending, bool):
                descending_values = [descending] * len(by)
            elif isinstance(descending, list) and len(descending) == len(by):
                descending_values = [bool(value) for value in descending]
            else:
                raise DataError("sort descending must be a boolean or a matching list.")
            nulls = str(operation.get("nulls", "last")).casefold()
            if nulls not in {"first", "last"}:
                raise DataError("sort nulls must be first or last.")

            def compare_rows(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
                for column, reverse in zip(by, descending_values, strict=True):
                    left_value = left.get(column)
                    right_value = right.get(column)
                    left_missing = left_value is None or str(left_value).strip() == ""
                    right_missing = right_value is None or str(right_value).strip() == ""
                    if left_missing != right_missing:
                        if nulls == "first":
                            return -1 if left_missing else 1
                        return 1 if left_missing else -1
                    if left_missing:
                        continue
                    left_key = _sort_value(left_value)
                    right_key = _sort_value(right_value)
                    if left_key == right_key:
                        continue
                    result = -1 if left_key < right_key else 1
                    return -result if reverse else result
                return 0

            current.rows.sort(key=cmp_to_key(compare_rows))
        elif op_name == "groupby":
            by = _require_columns(current.headers, operation.get("by"))
            aggregations = operation.get("aggregations")
            if not isinstance(aggregations, Mapping) or not aggregations:
                raise DataError("groupby requires an aggregations object.")
            grouped: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()
            for row in current.rows:
                key = tuple(row.get(column) for column in by)
                grouped.setdefault(key, []).append(row)
            output_rows = []
            for key, group_rows in grouped.items():
                output_row = OrderedDict(zip(by, key))
                for output_name, spec in aggregations.items():
                    if not isinstance(spec, Mapping):
                        raise DataError("Each aggregation must be an object.")
                    function = str(spec.get("function", "")).casefold()
                    column = str(spec.get("column", "*"))
                    if column != "*":
                        _require_columns(current.headers, [column])
                    output_row[str(output_name)] = _aggregate(
                        [row.get(column) for row in group_rows] if column != "*" else group_rows,
                        function,
                        column,
                    )
                output_rows.append(output_row)
            current.headers = [*by, *map(str, aggregations.keys())]
            current.rows = output_rows
            details["groups"] = len(output_rows)
        elif op_name == "limit":
            count = operation.get("count")
            if not isinstance(count, int) or count < 0:
                raise DataError("limit count must be a non-negative integer.")
            current.rows = current.rows[:count]
        else:
            raise DataError(f"Unsupported operation: {op_name or '(missing op)'}")
        audit.append(
            {
                "index": index,
                "op": op_name,
                "rows_before": rows_before,
                "rows_after": len(current.rows),
                "columns_before": len(columns_before),
                "columns_after": len(current.headers),
                **details,
            }
        )
    return current, audit


def load_recipe(path: Path | str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DataError(f"Could not read recipe: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"Invalid recipe JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DataError("Recipe must be a JSON object.")
    if payload.get("version") != 1:
        raise DataError("Recipe version must be 1.")
    if not isinstance(payload.get("operations"), list):
        raise DataError("Recipe operations must be a list.")
    for index, operation in enumerate(payload["operations"]):
        if not isinstance(operation, dict):
            raise DataError(f"Operation {index} must be an object.")
        op_name = str(operation.get("op", "")).strip().casefold()
        if op_name not in SUPPORTED_OPERATIONS:
            raise DataError(f"Operation {index} has unsupported op {op_name or '(missing)'}.")
    return payload


def _ensure_destinations(
    paths: Sequence[Path],
    overwrite: bool,
    input_path: Path | None = None,
) -> None:
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            raise DataError(f"Output paths must be distinct: {resolved}")
        seen.add(resolved)
        if input_path and resolved == input_path.expanduser().resolve():
            raise DataError("Output and reports must not overwrite the input file.")
        if resolved.exists() and not overwrite:
            raise DataError(f"Destination already exists: {resolved}. Pass --overwrite.")


def _write_output(table: DataTable, path: Path, encoding: str, overwrite: bool) -> None:
    if table.source and path.resolve() == table.source.resolve():
        raise DataError("Output must not overwrite the input file.")
    if path.exists() and not overwrite:
        raise DataError(f"Output already exists: {path}. Pass --overwrite to replace it.")
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_OUTPUTS:
        raise DataError(f"Unsupported output format {suffix or '(none)'}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=path.suffix,
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        if suffix in {".csv", ".tsv"}:
            with temp_path.open("w", encoding=encoding, newline="") as handle:
                writer = csv.writer(handle, delimiter="\t" if suffix == ".tsv" else ",")
                writer.writerow(table.headers)
                for row in table.rows:
                    writer.writerow(
                        [
                            _artifact_value(row.get(header))
                            if row.get(header) is not None
                            else ""
                            for header in table.headers
                        ]
                    )
        elif suffix in {".json", ".jsonl"}:
            records = [
                {header: _artifact_value(row.get(header)) for header in table.headers}
                for row in table.rows
            ]
            with temp_path.open("w", encoding=encoding) as handle:
                if suffix == ".json":
                    json.dump(records, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                else:
                    for record in records:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            try:
                from openpyxl import Workbook
            except ImportError as exc:
                raise DataError(
                    "Excel output requires openpyxl. Install "
                    "scholarime/install/dependencies.txt."
                ) from exc
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Processed Data"
            sheet.append(table.headers)
            for row_index, row in enumerate(table.rows, 2):
                for column_index, header in enumerate(table.headers, 1):
                    value = _artifact_value(row.get(header))
                    cell = sheet.cell(row=row_index, column=column_index, value=value)
                    if isinstance(value, str):
                        cell.data_type = "s"
            workbook.save(temp_path)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_json(path: Path, payload: Mapping[str, Any], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise DataError(f"Report already exists: {path}. Pass --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_markdown(path: Path, text: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise DataError(f"Report already exists: {path}. Pass --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _audit_markdown(audit: Mapping[str, Any]) -> str:
    lines = [
        "# Data Processing Audit",
        "",
        f"- Input: `{audit['input']['path']}`",
        f"- Input SHA-256: `{audit['input']['sha256']}`",
        f"- Recipe: `{audit['recipe']['path']}`",
        f"- Recipe SHA-256: `{audit['recipe']['sha256']}`",
        f"- Output: `{audit['output']['path']}`",
        f"- Output SHA-256: `{audit['output']['sha256']}`",
        f"- Rows: {audit['input']['rows']} → {audit['output']['rows']}",
        f"- Columns: {audit['input']['columns']} → {audit['output']['columns']}",
        "",
        "| # | Operation | Rows | Columns | Details |",
        "|---:|---|---:|---:|---|",
    ]
    for item in audit["operations"]:
        detail_keys = [
            key for key in item
            if key not in {"index", "op", "rows_before", "rows_after", "columns_before", "columns_after"}
        ]
        details = "; ".join(
            f"{key}={json.dumps(item[key], ensure_ascii=False)}"
            for key in detail_keys
        )
        lines.append(
            f"| {item['index']} | {item['op']} | "
            f"{item['rows_before']} → {item['rows_after']} | "
            f"{item['columns_before']} → {item['columns_after']} | "
            f"{details.replace('|', '\\|')} |"
        )
    lines.extend(["", "Generated by Scholarime. The source file was not modified.", ""])
    return "\n".join(lines)


def _default_report_path(output: Path, extension: str) -> Path:
    return output.with_name(output.stem + f".audit.{extension}")


def _add_input_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--encoding", default="auto", help="Input encoding or auto.")
    parser.add_argument("--delimiter", default="auto", help="Delimiter or auto.")
    parser.add_argument("--header-row", type=int, default=1)
    parser.add_argument("--no-header", action="store_const", const=0, dest="header_row")
    parser.add_argument("--sheet", help="Excel worksheet name.")
    parser.add_argument(
        "--null-token",
        action="append",
        default=[],
        help="Replacement null tokens; may be repeated. Defaults cover common NA/NULL spellings.",
    )


def _options_from_args(args: argparse.Namespace) -> InputOptions:
    return InputOptions(
        encoding=args.encoding,
        delimiter=args.delimiter,
        header_row=args.header_row,
        sheet=args.sheet,
        null_tokens=_normalise_null_tokens(args.null_token),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scholarly_data.py",
        description="Profile and reproducibly transform CSV, TSV, JSON, JSONL, and Excel data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scholarly_data.py profile data.csv\n"
            "  python scholarly_data.py validate recipe.json\n"
            "  python scholarly_data.py apply data.csv recipe.json -o cleaned.csv\n\n"
            "Profile first, let the Agent select a recipe from the evidence, and apply only after\n"
            "the user confirms the proposed transformations. The input file is never modified."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    profile_parser = subparsers.add_parser("profile", help="Create JSON and Markdown data profiles.")
    profile_parser.add_argument("input", type=Path)
    _add_input_options(profile_parser)
    profile_parser.add_argument("--output", type=Path, help="Profile JSON output.")
    profile_parser.add_argument("--report", type=Path, help="Profile Markdown output.")
    profile_parser.add_argument("--include-samples", action="store_true")
    profile_parser.add_argument("--overwrite", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="Validate a recipe structure.")
    validate_parser.add_argument("recipe", type=Path)

    apply_parser = subparsers.add_parser("apply", help="Apply a recipe and write an audit report.")
    apply_parser.add_argument("input", type=Path)
    apply_parser.add_argument("recipe", type=Path)
    _add_input_options(apply_parser)
    apply_parser.add_argument("-o", "--output", type=Path, required=True)
    apply_parser.add_argument("--output-encoding", default="utf-8-sig")
    apply_parser.add_argument("--report-json", type=Path)
    apply_parser.add_argument("--report-md", type=Path)
    apply_parser.add_argument("--overwrite", action="store_true")
    return parser


def _default_profile_paths(source: Path) -> tuple[Path, Path]:
    return (
        source.with_name(source.stem + ".profile.json"),
        source.with_name(source.stem + ".profile.md"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            load_recipe(args.recipe)
            print("Recipe structure is valid.")
            return 0
        options = _options_from_args(args)
        table = read_table(args.input, options)
        if args.command == "profile":
            default_json, default_md = _default_profile_paths(Path(args.input))
            output_json = (args.output or default_json).expanduser().resolve()
            output_md = (args.report or default_md).expanduser().resolve()
            _ensure_destinations(
                [output_json, output_md],
                overwrite=args.overwrite,
                input_path=Path(args.input).expanduser().resolve(),
            )
            profile = profile_table(table, options, include_samples=args.include_samples)
            _write_json(output_json, profile, overwrite=args.overwrite)
            _write_markdown(output_md, profile_markdown(profile), overwrite=args.overwrite)
            print(f"Profile JSON: {output_json}")
            print(f"Profile report: {output_md}")
            print(f"Rows: {profile['row_count']}; columns: {profile['column_count']}")
            for issue in profile["issues"]:
                print(f"{issue['level']}: {issue['message']}")
            return 0

        recipe_path = Path(args.recipe).expanduser().resolve()
        recipe = load_recipe(recipe_path)
        processed, audit_operations = apply_operations(
            table,
            recipe["operations"],
            options.null_tokens,
        )
        output = Path(args.output).expanduser().resolve()
        report_json = (
            args.report_json.expanduser().resolve()
            if args.report_json
            else _default_report_path(output, "json")
        )
        report_md = (
            args.report_md.expanduser().resolve()
            if args.report_md
            else _default_report_path(output, "md")
        )
        _ensure_destinations(
            [output, report_json, report_md],
            overwrite=args.overwrite,
            input_path=recipe_path,
        )
        _ensure_destinations(
            [output, report_json, report_md],
            overwrite=args.overwrite,
            input_path=Path(args.input).expanduser().resolve(),
        )
        _write_output(processed, output, args.output_encoding, overwrite=args.overwrite)
        audit = {
            "version": VERSION,
            "generated_at": _utc_now(),
            "input": {
                "path": str(table.source),
                "sha256": table.metadata.get("source_sha256"),
                "format": table.source_format,
                "rows": len(table.rows),
                "columns": len(table.headers),
                "encoding": table.metadata.get("encoding"),
                "delimiter": table.metadata.get("delimiter"),
                "sheet": table.metadata.get("sheet"),
                "null_tokens": list(options.null_tokens),
            },
            "recipe": {
                "path": str(recipe_path),
                "sha256": _sha256_file(recipe_path),
            },
            "operations": audit_operations,
            "output": {
                "path": str(output),
                "sha256": _sha256_file(output),
                "rows": len(processed.rows),
                "columns": len(processed.headers),
                "encoding": args.output_encoding,
            },
        }
        _write_json(report_json, audit, overwrite=args.overwrite)
        _write_markdown(report_md, _audit_markdown(audit), overwrite=args.overwrite)
        print(f"Processed data: {output}")
        print(f"Audit JSON: {report_json}")
        print(f"Audit report: {report_md}")
        print(f"Rows: {len(table.rows)} -> {len(processed.rows)}")
        print(f"Columns: {len(table.headers)} -> {len(processed.headers)}")
        return 0
    except (DataError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
