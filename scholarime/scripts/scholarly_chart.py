#!/usr/bin/env python3
"""Generate publication-oriented PNG charts from CSV data."""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from PIL import Image, ImageColor, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - exercised when Pillow is absent
    Image = None
    ImageColor = None
    ImageDraw = None
    ImageFont = None


VERSION = "1.0.0"
CHART_TYPES = ("bar", "line", "pie", "scatter")
MISSING_VALUES = {"", "na", "n/a", "nan", "null", "none", "--", "-"}

PALETTES: dict[str, tuple[str, ...]] = {
    "academic-blue": (
        "#1F4E79",
        "#2E75B6",
        "#7FA9D1",
        "#A6C9E8",
        "#C9A227",
        "#B85C38",
    ),
    "academic-earth": (
        "#3F4A3C",
        "#6B705C",
        "#A5A58D",
        "#C2B8A3",
        "#CB997E",
        "#8C5E58",
    ),
    "academic-slate": (
        "#243447",
        "#4F6D7A",
        "#8AA5B1",
        "#B7C4C9",
        "#7A6F5D",
        "#A1846A",
    ),
    "academic-warm": (
        "#7F3C2A",
        "#B5651D",
        "#D9A066",
        "#E8D5B5",
        "#5F6F65",
        "#9A8C98",
    ),
    "academic-mono": (
        "#111827",
        "#374151",
        "#6B7280",
        "#9CA3AF",
        "#D1D5DB",
        "#4B5563",
    ),
}

CHINESE_TEXT_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
METADATA_RE = re.compile(
    r"^\s*#\s*scholarime\s*:\s*([A-Za-z0-9_-]+)\s*=\s*(.*?)\s*$",
    re.IGNORECASE,
)

METADATA_ALIASES = {
    "chart": "chart_type",
    "type": "chart_type",
    "header": "header_row",
    "x": "x_column",
    "y": "y_columns",
    "colors": "legend_colors",
    "legend_color": "legend_colors",
    "legend_colours": "legend_colors",
    "font": "font_family",
}

LATIN_FONTS = (
    "Times New Roman",
    "Liberation Serif",
    "Nimbus Roman",
    "DejaVu Serif",
    "Georgia",
    "Palatino Linotype",
    "Book Antiqua",
    "Baskerville",
    "STIXGeneral",
    "DejaVu Sans",
)

CJK_FONTS = (
    "Noto Serif CJK SC",
    "Source Han Serif SC",
    "SimSun",
    "Songti SC",
    "STSong",
    "Noto Sans CJK SC",
    "Microsoft YaHei",
    "WenQuanYi Zen Hei",
    "DejaVu Sans",
)

FONT_ALIASES: dict[str, tuple[str, ...]] = {
    "timesnewroman": ("timesnewroman", "times"),
    "liberationserif": ("liberationserif",),
    "nimbusroman": ("nimbusroman",),
    "dejavuserif": ("dejavuserif",),
    "georgia": ("georgia",),
    "palatinolinotype": ("palatinolinotype", "pala"),
    "bookantiqua": ("bookantiqua",),
    "baskerville": ("baskerville", "baskervilleoldface"),
    "stixgeneral": ("stixgeneral",),
    "dejavusans": ("dejavusans",),
    "notoserifcjksc": ("notoserifcjksc", "notoserifcjkregular"),
    "sourcehanserifsc": (
        "sourcehanserifsc",
        "sourcehanserifcn",
        "sourcehanserifregular",
    ),
    "simsun": ("simsun",),
    "songtisc": ("songti",),
    "stsong": ("stsong",),
    "notosanscjksc": ("notosanscjksc", "notosanscjkregular"),
    "microsoftyahei": ("microsoftyahei", "msyh"),
    "wenquanyizenhei": ("wenquanyizenhei", "wqyzenhei"),
}


class ChartError(ValueError):
    """A user-correctable chart generation error."""


@dataclass
class ChartConfig:
    csv_path: Path
    output_path: Path
    chart_type: str = "bar"
    delimiter: str | None = None
    encoding: str = "utf-8-sig"
    header_row: int = 1
    title: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    x_column: str | None = None
    y_columns: tuple[str, ...] = ()
    legend: bool | None = None
    legend_labels: tuple[str, ...] = ()
    legend_colors: tuple[str, ...] = ()
    palette: str = "academic-blue"
    font_family: str = "Times New Roman"
    font_size: float | None = None
    width: float = 9.0
    height: float = 5.6
    dpi: int = 200
    grid: bool = True
    transparent: bool = False
    overwrite: bool = False


@dataclass
class ChartSeries:
    label: str
    values: tuple[float | None, ...]


@dataclass
class ChartData:
    headers: tuple[str, ...]
    x_header: str
    x_values: tuple[str, ...]
    series: tuple[ChartSeries, ...]


@dataclass
class FontChoice:
    family: str
    path: Path | None
    index: int = 0
    requested: str = "Times New Roman"
    fallback: bool = False
    reason: str | None = None


@dataclass
class RenderResult:
    output_path: Path
    chart_type: str
    font_family: str
    requested_font: str
    font_fallback: bool
    fallback_reason: str | None
    font_path: Path | None
    font_size: float
    font_size_auto: bool
    dpi: int
    width: float
    height: float
    series_count: int
    point_count: int


@dataclass
class _FontSet:
    regular: Any
    title: Any
    small: Any
    regular_px: int
    title_px: int
    small_px: int


@dataclass
class _Layout:
    image: Any
    draw: Any
    width: int
    height: int
    plot_left: int
    plot_top: int
    plot_right: int
    plot_bottom: int
    margin: int
    legend_x: int | None
    legend_width: int
    colors: tuple[tuple[int, int, int], ...] = field(default_factory=tuple)


def _require_pillow() -> None:
    if Image is None:
        raise ChartError(
            "Pillow is required. Install it with "
            "`python -m pip install -r scholarime/install/dependencies.txt`."
        )


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _text_has_cjk(values: Iterable[str | None]) -> bool:
    return any(value and CHINESE_TEXT_RE.search(value) for value in values)


def _parse_bool(key: str, value: str) -> bool:
    normalised = value.strip().casefold()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ChartError(f"{key} must be true/false, yes/no, on/off, or 1/0.")


def _split_list(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    try:
        return tuple(item.strip() for item in next(csv.reader([value])) if item.strip())
    except csv.Error as exc:
        raise ChartError(f"Could not parse list value: {value!r}.") from exc


def _normalise_delimiter(value: str | None) -> str | None:
    if value is None:
        return None
    names = {
        "comma": ",",
        "semicolon": ";",
        "tab": "\t",
        "pipe": "|",
        "\\t": "\t",
    }
    resolved = names.get(value.casefold(), value)
    if len(resolved) != 1:
        raise ChartError("delimiter must be one character, comma, semicolon, tab, or pipe.")
    return resolved


def extract_metadata(text: str) -> tuple[dict[str, str], str]:
    metadata: dict[str, str] = {}
    body_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = METADATA_RE.match(line.rstrip("\r\n"))
        if match:
            key = match.group(1).strip().replace("-", "_").casefold()
            key = METADATA_ALIASES.get(key, key)
            metadata[key] = match.group(2).strip()
            continue
        if line.lstrip().startswith("#"):
            continue
        body_lines.append(line)
    return metadata, "".join(body_lines)


def _metadata_options(metadata: Mapping[str, str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    int_keys = {"header_row", "dpi"}
    float_keys = {"font_size", "width", "height"}
    bool_keys = {"legend", "grid", "transparent", "overwrite"}
    list_keys = {"y_columns", "legend_labels", "legend_colors"}
    string_keys = {
        "chart_type",
        "delimiter",
        "title",
        "x_label",
        "y_label",
        "x_column",
        "palette",
        "font_family",
        "output",
    }
    allowed = int_keys | float_keys | bool_keys | list_keys | string_keys

    for raw_key, raw_value in metadata.items():
        key = raw_key.replace("-", "_").casefold()
        if key not in allowed:
            raise ChartError(f"Unknown CSV option: {raw_key!r}.")
        if key in int_keys:
            try:
                options[key] = int(raw_value)
            except ValueError as exc:
                raise ChartError(f"{key} must be an integer.") from exc
        elif key in float_keys:
            if key == "font_size" and raw_value.strip().casefold() in {"auto", "default"}:
                options[key] = None
                continue
            try:
                options[key] = float(raw_value)
            except ValueError as exc:
                raise ChartError(f"{key} must be a number.") from exc
        elif key in bool_keys:
            options[key] = _parse_bool(key, raw_value)
        elif key in list_keys:
            options[key] = _split_list(raw_value)
        elif key == "delimiter":
            options[key] = _normalise_delimiter(raw_value)
        else:
            options[key] = raw_value
    return options


def _detect_delimiter(body: str, requested: str | None) -> str:
    normalised = _normalise_delimiter(requested)
    if normalised:
        return normalised
    sample = body[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        nonempty = [line for line in sample.splitlines() if line.strip()]
        if not nonempty:
            raise ChartError("CSV file is empty.")
        counts = {candidate: nonempty[0].count(candidate) for candidate in (",", ";", "\t", "|")}
        delimiter = max(counts, key=counts.get)
        return delimiter if counts[delimiter] else ","


def _coerce_config(
    csv_path: Path,
    metadata: Mapping[str, str],
    body: str,
    cli_options: Mapping[str, Any],
) -> ChartConfig:
    merged = _metadata_options(metadata)
    cli_clean = {key: value for key, value in cli_options.items() if value is not None}
    metadata_output = merged.pop("output", None)
    cli_output = cli_clean.pop("output", None)
    merged.update(cli_clean)

    if "delimiter" in merged:
        merged["delimiter"] = _normalise_delimiter(merged["delimiter"])
    else:
        merged["delimiter"] = _detect_delimiter(body, None)

    if cli_output:
        output_path = Path(str(cli_output)).expanduser().resolve()
    elif metadata_output:
        output_path = (csv_path.parent / str(metadata_output)).expanduser().resolve()
    else:
        output_path = csv_path.with_suffix(".png").resolve()

    allowed = {
        "chart_type",
        "delimiter",
        "encoding",
        "header_row",
        "title",
        "x_label",
        "y_label",
        "x_column",
        "y_columns",
        "legend",
        "legend_labels",
        "legend_colors",
        "palette",
        "font_family",
        "font_size",
        "width",
        "height",
        "dpi",
        "grid",
        "transparent",
        "overwrite",
    }
    unknown = set(merged) - allowed
    if unknown:
        raise ChartError(f"Unknown option(s): {', '.join(sorted(unknown))}.")

    config = ChartConfig(csv_path=csv_path, output_path=output_path, **merged)
    _validate_config(config)
    return config


def _validate_config(config: ChartConfig) -> None:
    config.chart_type = config.chart_type.casefold()
    if config.chart_type not in CHART_TYPES:
        raise ChartError(
            f"Unknown chart type {config.chart_type!r}; choose from {', '.join(CHART_TYPES)}."
        )
    if config.header_row < 0:
        raise ChartError("header_row must be 0 (no header) or a positive row number.")
    if config.output_path.suffix.casefold() != ".png":
        raise ChartError("Output must use a .png extension.")
    if not 3 <= config.width <= 30 or not 2 <= config.height <= 30:
        raise ChartError("width and height must be between 3-30 and 2-30 inches.")
    if not 72 <= config.dpi <= 600:
        raise ChartError("dpi must be between 72 and 600.")
    if config.font_size is not None and config.font_size <= 0:
        raise ChartError("font_size must be greater than zero.")
    if config.palette not in PALETTES:
        raise ChartError(
            f"Unknown palette {config.palette!r}; use --list-palettes to see options."
        )
    config.output_path = config.output_path.with_suffix(".png").resolve()


def _clean_rows(text: str, delimiter: str) -> list[list[str]]:
    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    for row in reader:
        while row and not row[-1].strip():
            row.pop()
        if any(cell.strip() for cell in row):
            rows.append([cell.strip() for cell in row])
    return rows


def _resolve_column(token: str, headers: Sequence[str]) -> int:
    token = str(token).strip()
    exact = [index for index, name in enumerate(headers) if name == token]
    if exact:
        return exact[0]
    folded = [index for index, name in enumerate(headers) if name.casefold() == token.casefold()]
    if folded:
        return folded[0]
    if token.isdigit():
        index = int(token) - 1
        if 0 <= index < len(headers):
            return index
    raise ChartError(
        f"Column {token!r} was not found. Available columns: {', '.join(headers)}."
    )


def parse_number(value: str) -> float | None:
    cleaned = value.strip().replace("\u2212", "-")
    if cleaned.casefold() in MISSING_VALUES:
        return None
    try:
        number = float(cleaned)
    except ValueError as exc:
        raise ChartError(f"Expected a number, got {value!r}.") from exc
    return number if math.isfinite(number) else None


def load_chart_data(config: ChartConfig, body: str) -> ChartData:
    rows = _clean_rows(body, config.delimiter or ",")
    if not rows:
        raise ChartError("CSV file contains no data rows.")

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    if config.header_row == 0:
        headers = [f"Column {index + 1}" for index in range(width)]
        data_rows = rows
    else:
        if config.header_row > len(rows):
            raise ChartError(
                f"header_row={config.header_row} is beyond the {len(rows)} available CSV rows."
            )
        header_index = config.header_row - 1
        headers = [
            name.strip() or f"Column {index + 1}" for index, name in enumerate(rows[header_index])
        ]
        data_rows = rows[header_index + 1 :]

    if not data_rows:
        raise ChartError("CSV file contains headers but no data rows.")
    seen: set[str] = set()
    for name in headers:
        if name in seen:
            raise ChartError(f"Duplicate column name {name!r}; headers must be unique.")
        seen.add(name)

    x_index = _resolve_column(config.x_column, headers) if config.x_column else 0

    if config.y_columns:
        y_indices = [_resolve_column(token, headers) for token in config.y_columns]
    else:
        y_indices = []
        for index in range(len(headers)):
            if index == x_index:
                continue
            for row in data_rows:
                try:
                    if parse_number(row[index]) is not None:
                        y_indices.append(index)
                        break
                except ChartError:
                    continue

    if config.chart_type == "pie":
        if len(y_indices) > 1:
            raise ChartError("Pie charts accept exactly one y column.")
        if not y_indices:
            raise ChartError("Pie charts require one numeric y column.")

    if not y_indices:
        raise ChartError("No numeric y columns were found.")
    if len(set(y_indices)) != len(y_indices):
        raise ChartError("y columns must not contain duplicates.")

    x_values: list[str] = []
    series_values: list[list[float | None]] = [[] for _ in y_indices]
    for row_number, row in enumerate(data_rows, start=config.header_row + 1):
        x_value = row[x_index].strip()
        if not x_value:
            raise ChartError(f"Empty x value at data row {row_number}.")
        x_values.append(x_value)
        for series_index, column_index in enumerate(y_indices):
            try:
                value = parse_number(row[column_index])
            except ChartError as exc:
                raise ChartError(
                    f"Invalid value for {headers[column_index]!r} at data row {row_number}: {exc}"
                ) from exc
            series_values[series_index].append(value)

    series: list[ChartSeries] = []
    for column_index, values in zip(y_indices, series_values, strict=True):
        if values and all(value is None for value in values):
            raise ChartError(f"Column {headers[column_index]!r} contains no numeric values.")
        series.append(ChartSeries(label=headers[column_index], values=tuple(values)))

    if config.chart_type == "pie":
        pie_values = series[0].values
        if any(value is None for value in pie_values):
            raise ChartError("Pie charts do not accept missing values.")
        if any(value is not None and value < 0 for value in pie_values):
            raise ChartError("Pie charts do not accept negative values.")
        if sum(value or 0 for value in pie_values) <= 0:
            raise ChartError("Pie chart values must have a positive sum.")

    return ChartData(
        headers=tuple(headers),
        x_header=headers[x_index],
        x_values=tuple(x_values),
        series=tuple(series),
    )


def _font_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    if sys.platform == "win32":
        roots.extend(
            [
                Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
            ]
        )
    elif sys.platform == "darwin":
        roots.extend(
            [
                Path("/System/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"),
                Path.home() / "Library" / "Fonts",
            ]
        )
    roots.extend(
        [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".local" / "share" / "fonts",
            Path.home() / ".fonts",
        ]
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root and root not in seen and root.exists():
            unique.append(root)
            seen.add(root)
    return tuple(unique)


_FONT_FILE_CACHE: list[Path] | None = None


def _font_files() -> list[Path]:
    global _FONT_FILE_CACHE
    if _FONT_FILE_CACHE is not None:
        return _FONT_FILE_CACHE
    files: list[Path] = []
    for root in _font_roots():
        try:
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.casefold() in {".ttf", ".otf", ".ttc"}
            )
        except OSError:
            continue
    _FONT_FILE_CACHE = sorted(set(files), key=lambda item: str(item).casefold())
    return _FONT_FILE_CACHE


def _font_index_for_family(path: Path, family: str) -> int:
    if path.suffix.casefold() not in {".ttc", ".otc"}:
        return 0
    wanted = _normalise_name(family)
    for index in range(0, 10):
        try:
            font = ImageFont.truetype(str(path), 24, index=index)
        except OSError:
            break
        actual = _normalise_name(font.getname()[0])
        if wanted in actual or actual in wanted:
            return index
    return 0


def _find_named_font(name: str) -> FontChoice | None:
    normalised = _normalise_name(name)
    aliases = FONT_ALIASES.get(normalised, (normalised,))
    matches: list[tuple[int, Path]] = []
    for path in _font_files():
        stem = _normalise_name(path.stem)
        for alias in aliases:
            if stem == alias:
                matches.append((0, path))
                break
            if stem.startswith(alias):
                score = 3
                if any(word in stem for word in ("bold", "italic", "oblique", "light", "heavy")):
                    score += 2
                matches.append((score, path))
                break
    if not matches:
        return None
    path = min(matches, key=lambda item: (item[0], len(item[1].name), str(item[1])))[1]
    index = _font_index_for_family(path, name)
    try:
        font = ImageFont.truetype(str(path), 24, index=index)
        actual_family = font.getname()[0]
    except OSError:
        actual_family = name
    return FontChoice(family=actual_family, path=path, index=index, requested=name)


def _supports_cjk(choice: FontChoice) -> bool:
    if not choice.path:
        return False
    try:
        font = ImageFont.truetype(str(choice.path), 32, index=choice.index)
        first = bytes(font.getmask("中"))
        second = bytes(font.getmask("文"))
        return bool(first and second and first != second)
    except OSError:
        return False


def resolve_font(requested: str, need_cjk: bool) -> FontChoice:
    requested = requested.strip() or "Times New Roman"
    requested_path = Path(requested).expanduser()
    if requested_path.is_file():
        try:
            font = ImageFont.truetype(str(requested_path), 24)
            family = font.getname()[0]
        except OSError as exc:
            raise ChartError(f"Could not open font file {requested_path}: {exc}") from exc
        primary = FontChoice(
            family=family,
            path=requested_path.resolve(),
            requested=requested_path.name,
        )
    else:
        primary = _find_named_font(requested)

    if primary and (not need_cjk or _supports_cjk(primary)):
        return primary

    reason = (
        f"{primary.family!r} does not contain Chinese glyphs"
        if primary and need_cjk
        else f"{requested!r} was not found"
    )
    candidates = CJK_FONTS if need_cjk else LATIN_FONTS
    for family in candidates:
        if primary and _normalise_name(family) == _normalise_name(primary.family):
            continue
        candidate = _find_named_font(family)
        if candidate and (not need_cjk or _supports_cjk(candidate)):
            candidate.requested = requested
            candidate.fallback = True
            candidate.reason = reason
            return candidate

    return FontChoice(
        family="Pillow Default",
        path=None,
        requested=requested,
        fallback=True,
        reason=reason,
    )


def list_available_fonts() -> list[FontChoice]:
    available: list[FontChoice] = []
    seen: set[tuple[Path | None, int]] = set()
    for family in (*LATIN_FONTS, *CJK_FONTS):
        choice = _find_named_font(family)
        key = (choice.path if choice else None, choice.index if choice else 0)
        if choice and key not in seen:
            available.append(choice)
            seen.add(key)
    return available


def _load_font(path: Path | None, index: int, px: int) -> Any:
    if path is None:
        try:
            return ImageFont.load_default(size=px)
        except TypeError:
            return ImageFont.load_default()
    return ImageFont.truetype(str(path), px, index=index)


def _choose_font_size(config: ChartConfig, data: ChartData) -> tuple[float, bool]:
    if config.font_size is not None:
        return float(config.font_size), False
    count = max(len(data.x_values), len(data.series))
    if count <= 6:
        size = 12.0
    elif count <= 12:
        size = 11.0
    elif count <= 20:
        size = 10.0
    elif count <= 40:
        size = 9.0
    else:
        size = 7.5
    longest = max(
        [len(value) for value in data.x_values]
        + [len(series.label) for series in data.series]
        + [1 if not config.title else len(config.title)]
    )
    if longest > 24:
        size -= 0.75
    if longest > 48:
        size -= 1.0
    if config.width < 7:
        size -= 0.75
    if config.height < 4.5:
        size -= 0.5
    return round(max(7.0, min(14.0, size)), 1), True


def _resolve_colors(config: ChartConfig, count: int) -> tuple[tuple[int, int, int], ...]:
    manual = config.legend_colors
    raw = manual or PALETTES[config.palette]
    if not raw:
        raw = PALETTES["academic-blue"]
    colors: list[tuple[int, int, int]] = []
    for index in range(count):
        value = raw[index % len(raw)]
        try:
            colors.append(ImageColor.getrgb(value))
        except ValueError as exc:
            raise ChartError(f"Invalid color {value!r}.") from exc
    return tuple(colors)


def _text_size(draw: Any, text: str, font: Any) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return max(0, box[2] - box[0]), max(0, box[3] - box[1])


def _wrap_text(draw: Any, text: str, font: Any, max_width: int, limit: int = 3) -> list[str]:
    if not text:
        return [""]
    if _text_size(draw, text, font)[0] <= max_width:
        return [text]
    tokens = list(text) if CHINESE_TEXT_RE.search(text) else re.findall(r"\S+\s*", text)
    lines: list[str] = []
    current = ""
    for token in tokens:
        trial = current + token
        if current and _text_size(draw, trial, font)[0] > max_width:
            lines.append(current.rstrip())
            current = token.lstrip()
        else:
            current = trial
    if current:
        lines.append(current.rstrip())
    if len(lines) > limit:
        lines = lines[:limit]
        last = lines[-1]
        while last and _text_size(draw, last + "…", font)[0] > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _draw_multiline_center(
    draw: Any,
    x: int,
    y: int,
    lines: Sequence[str],
    font: Any,
    fill: tuple[int, int, int],
) -> int:
    line_height = max(1, _text_size(draw, "Ag", font)[1])
    spacing = max(1, int(line_height * 0.15))
    cursor = y
    for line in lines:
        width, _ = _text_size(draw, line, font)
        draw.text((x - width // 2, cursor), line, font=font, fill=fill)
        cursor += line_height + spacing
    return cursor


def _draw_rotated_text(
    image: Any,
    position: tuple[int, int],
    text: str,
    font: Any,
    fill: tuple[int, int, int],
    angle: int = 90,
    anchor: str = "top_left",
) -> None:
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    width, height = _text_size(probe, text, font)
    tile = Image.new("RGBA", (width + 8, height + 8), (0, 0, 0, 0))
    tile_draw = ImageDraw.Draw(tile)
    tile_draw.text((4, 4), text, font=font, fill=fill)
    rotated = tile.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    if anchor == "center":
        position = (
            position[0] - rotated.width // 2,
            position[1] - rotated.height // 2,
        )
    image.alpha_composite(rotated, position)


def _format_number(value: float) -> str:
    if value == 0:
        return "0"
    absolute = abs(value)
    if absolute >= 10000 or absolute < 0.01:
        return f"{value:.2e}"
    if absolute >= 100:
        return f"{value:.0f}"
    if absolute >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _nice_number(value: float, round_value: bool) -> float:
    if value <= 0:
        return 0.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10**exponent)
    if round_value:
        if fraction < 1.5:
            nice_fraction = 1
        elif fraction < 3:
            nice_fraction = 2
        elif fraction < 7:
            nice_fraction = 5
        else:
            nice_fraction = 10
    elif fraction <= 1:
        nice_fraction = 1
    elif fraction <= 2:
        nice_fraction = 2
    elif fraction <= 5:
        nice_fraction = 5
    else:
        nice_fraction = 10
    return nice_fraction * (10**exponent)


def _nice_axis(
    values: Iterable[float | None], include_zero: bool, target_ticks: int = 6
) -> tuple[float, float, list[float]]:
    present = [value for value in values if value is not None]
    if not present:
        raise ChartError("No numeric values are available for the selected columns.")
    low = min(present)
    high = max(present)
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if low == high:
        padding = abs(low) * 0.1 or 1.0
        low -= padding
        high += padding
    else:
        padding = (high - low) * 0.06
        low -= padding
        high += padding
    if include_zero:
        if min(present) >= 0:
            low = 0.0
        if max(present) <= 0:
            high = 0.0
    step = _nice_number((high - low) / max(1, target_ticks - 1), True)
    graph_low = math.floor(low / step) * step
    graph_high = math.ceil(high / step) * step
    ticks: list[float] = []
    value = graph_low
    while value <= graph_high + step * 0.5:
        ticks.append(round(value, 12))
        value += step
    return graph_low, graph_high, ticks


def _dashed_line(
    draw: Any,
    start: tuple[int, int],
    end: tuple[int, int],
    fill: tuple[int, int, int],
    width: int,
    dash: int,
    gap: int,
) -> None:
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    dx = (x2 - x1) / length
    dy = (y2 - y1) / length
    offset = 0.0
    while offset < length:
        segment_end = min(length, offset + dash)
        draw.line(
            [
                (x1 + dx * offset, y1 + dy * offset),
                (x1 + dx * segment_end, y1 + dy * segment_end),
            ],
            fill=fill,
            width=width,
        )
        offset += dash + gap


def _legend_entries(
    config: ChartConfig,
    data: ChartData,
    colors: Sequence[tuple[int, int, int]],
) -> list[tuple[str, tuple[int, int, int]]]:
    if config.chart_type == "pie":
        values = [value or 0 for value in data.series[0].values]
        total = sum(values)
        labels = config.legend_labels or data.x_values
        if len(labels) != len(values):
            raise ChartError("For pie charts, legend_labels must match the number of categories.")
        return [
            (f"{label} ({value / total * 100:.1f}%)", colors[index % len(colors)])
            for index, (label, value) in enumerate(zip(labels, values, strict=True))
        ]
    labels = config.legend_labels or tuple(series.label for series in data.series)
    if len(labels) != len(data.series):
        raise ChartError("legend_labels must match the number of y columns.")
    return [
        (label, colors[index % len(colors)])
        for index, label in enumerate(labels)
    ]


def _create_layout(
    config: ChartConfig,
    data: ChartData,
    font_choice: FontChoice,
    font_size: float,
    legend_entries: Sequence[tuple[str, tuple[int, int, int]]],
    show_legend: bool,
    colors: Sequence[tuple[int, int, int]],
) -> tuple[_Layout, _FontSet, Any]:
    scale = 2
    width = max(1, round(config.width * config.dpi))
    height = max(1, round(config.height * config.dpi))
    canvas_width = width * scale
    canvas_height = height * scale
    background = (255, 255, 255, 0) if config.transparent else (255, 255, 255, 255)
    image = Image.new("RGBA", (canvas_width, canvas_height), background)
    draw = ImageDraw.Draw(image)

    regular_px = max(1, round(font_size * config.dpi / 72 * scale))
    title_px = max(1, round(regular_px * 1.12))
    small_px = max(1, round(regular_px * 0.92))
    regular = _load_font(font_choice.path, font_choice.index, regular_px)
    title = _load_font(font_choice.path, font_choice.index, title_px)
    small = _load_font(font_choice.path, font_choice.index, small_px)
    fonts = _FontSet(regular, title, small, regular_px, title_px, small_px)

    margin = max(20 * scale, round(min(canvas_width, canvas_height) * 0.055))
    title_height = 0
    if config.title:
        title_height = _text_size(draw, config.title, title)[1] + margin // 3
    legend_text_width = max(
        (_text_size(draw, label, small)[0] for label, _ in legend_entries),
        default=0,
    )
    sample_width = 34 * scale
    legend_width = (
        min(round(canvas_width * 0.30), legend_text_width + sample_width + 42 * scale)
        if show_legend
        else 0
    )
    legend_x = canvas_width - margin - legend_width if show_legend else None

    plot_left = max(round(canvas_width * 0.11), 80 * scale)
    plot_top = margin + title_height
    plot_right = (legend_x - 30 * scale) if legend_x else canvas_width - margin
    plot_bottom = canvas_height - margin - round(canvas_height * 0.17)

    if plot_right <= plot_left or plot_bottom <= plot_top:
        raise ChartError("Figure dimensions leave no room for the plot.")

    layout = _Layout(
        image=image,
        draw=draw,
        width=width,
        height=height,
        plot_left=plot_left,
        plot_top=plot_top,
        plot_right=plot_right,
        plot_bottom=plot_bottom,
        margin=margin,
        legend_x=legend_x,
        legend_width=legend_width,
        colors=tuple(colors),
    )
    return layout, fonts, scale


def _draw_title_and_legend(
    layout: _Layout,
    config: ChartConfig,
    fonts: _FontSet,
    legend_entries: Sequence[tuple[str, tuple[int, int, int]]],
    show_legend: bool,
    scale: int,
) -> None:
    draw = layout.draw
    if config.title:
        width, height = _text_size(draw, config.title, fonts.title)
        draw.text(
            ((layout.image.width - width) // 2, layout.margin),
            config.title,
            font=fonts.title,
            fill=(20, 20, 20, 255),
        )

    if not show_legend or layout.legend_x is None:
        return
    cursor = layout.plot_top
    line_height = max(
        _text_size(draw, "Ag", fonts.small)[1],
        28 * scale,
    )
    for label, color in legend_entries:
        draw.rounded_rectangle(
            [
                (layout.legend_x, cursor + line_height // 2 - 6 * scale),
                (layout.legend_x + 22 * scale, cursor + line_height // 2 + 6 * scale),
            ],
            radius=2 * scale,
            fill=(*color, 255),
        )
        max_width = max(10, layout.width * scale - layout.legend_x - layout.margin)
        lines = _wrap_text(draw, label, fonts.small, max_width, limit=2)
        y = cursor
        for line in lines:
            draw.text(
                (layout.legend_x + 28 * scale, y),
                line,
                font=fonts.small,
                fill=(35, 35, 35, 255),
            )
            y += _text_size(draw, "Ag", fonts.small)[1] + 3 * scale
        cursor += max(line_height, y - cursor) + 12 * scale


def _series_values(data: ChartData) -> list[float | None]:
    return [value for series in data.series for value in series.values]


def _x_numeric_values(data: ChartData) -> list[float] | None:
    values: list[float] = []
    for value in data.x_values:
        try:
            number = float(value)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        values.append(number)
    return values


def _draw_axes(
    layout: _Layout,
    config: ChartConfig,
    data: ChartData,
    fonts: _FontSet,
    scale: int,
    y_low: float,
    y_high: float,
    y_ticks: Sequence[float],
    x_numeric: Sequence[float] | None = None,
    x_bounds: tuple[float, float] | None = None,
) -> None:
    draw = layout.draw
    axis_color = (45, 45, 45, 255)
    grid_color = (220, 220, 220, 255)
    text_color = (55, 55, 55, 255)
    axis_width = max(1, round(config.dpi / 180 * scale))
    draw.line(
        [(layout.plot_left, layout.plot_top), (layout.plot_left, layout.plot_bottom)],
        fill=axis_color,
        width=axis_width,
    )
    draw.line(
        [(layout.plot_left, layout.plot_bottom), (layout.plot_right, layout.plot_bottom)],
        fill=axis_color,
        width=axis_width,
    )

    for tick in y_ticks:
        ratio = (tick - y_low) / (y_high - y_low)
        y = round(layout.plot_bottom - ratio * (layout.plot_bottom - layout.plot_top))
        if config.grid:
            _dashed_line(
                draw,
                (layout.plot_left, y),
                (layout.plot_right, y),
                grid_color,
                max(1, scale),
                8 * scale,
                6 * scale,
            )
        label = _format_number(tick)
        width, height = _text_size(draw, label, fonts.small)
        draw.text(
            (layout.plot_left - 12 * scale - width, y - height // 2),
            label,
            font=fonts.small,
            fill=text_color,
        )

    if x_numeric is not None:
        x_ticks = _nice_axis(x_numeric, include_zero=False, target_ticks=6)[2]
        x_min, x_max = x_bounds or (min(x_numeric), max(x_numeric))
        for tick in x_ticks:
            if not x_min <= tick <= x_max:
                continue
            x = layout.plot_left + (tick - x_min) / (x_max - x_min) * (
                layout.plot_right - layout.plot_left
            )
            label = _format_number(tick)
            width, _ = _text_size(draw, label, fonts.small)
            draw.text(
                (round(x - width / 2), layout.plot_bottom + 12 * scale),
                label,
                font=fonts.small,
                fill=text_color,
            )
    else:
        x_max_labels = 16
        step = max(1, math.ceil(len(data.x_values) / x_max_labels))
        group_width = (layout.plot_right - layout.plot_left) / max(1, len(data.x_values))
        label_width = max(30 * scale, round(group_width * max(1.2, step * 1.6)))
        for index in range(0, len(data.x_values), step):
            x = layout.plot_left + group_width * (index + 0.5)
            lines = _wrap_text(draw, data.x_values[index], fonts.small, label_width, limit=3)
            _draw_multiline_center(
                draw,
                round(x),
                layout.plot_bottom + 12 * scale,
                lines,
                fonts.small,
                text_color,
            )

    if config.x_label:
        width, height = _text_size(draw, config.x_label, fonts.regular)
        draw.text(
            (
                (layout.plot_left + layout.plot_right - width) // 2,
                layout.image.height - layout.margin - height,
            ),
            config.x_label,
            font=fonts.regular,
            fill=text_color,
        )
    if config.y_label:
        _draw_rotated_text(
            layout.image,
            (layout.margin // 2, (layout.plot_top + layout.plot_bottom) // 2),
            config.y_label,
            fonts.regular,
            text_color,
            angle=90,
            anchor="center",
        )


def _draw_bar_chart(
    layout: _Layout,
    config: ChartConfig,
    data: ChartData,
    fonts: _FontSet,
    scale: int,
) -> None:
    y_low, y_high, ticks = _nice_axis(_series_values(data), include_zero=True)
    _draw_axes(layout, config, data, fonts, scale, y_low, y_high, ticks)
    draw = layout.draw
    group_width = (layout.plot_right - layout.plot_left) / max(1, len(data.x_values))
    group_inner = group_width * 0.82
    bar_width = group_inner / max(1, len(data.series))
    base_y = layout.plot_bottom - (
        (0 - y_low) / (y_high - y_low) * (layout.plot_bottom - layout.plot_top)
    )
    for series_index, series in enumerate(data.series):
        color = (*layout.colors[series_index % len(layout.colors)], 255)
        for index, value in enumerate(series.values):
            if value is None:
                continue
            center = layout.plot_left + group_width * (index + 0.5)
            left = center - group_inner / 2 + series_index * bar_width
            right = left + bar_width * 0.88
            y_value = layout.plot_bottom - (
                (value - y_low) / (y_high - y_low) * (layout.plot_bottom - layout.plot_top)
            )
            draw.rectangle(
                [
                    (round(left), round(min(y_value, base_y))),
                    (round(right), round(max(y_value, base_y))),
                ],
                fill=color,
                outline=color,
                width=max(1, scale // 2),
            )


def _draw_line_chart(
    layout: _Layout,
    config: ChartConfig,
    data: ChartData,
    fonts: _FontSet,
    scale: int,
) -> None:
    y_low, y_high, ticks = _nice_axis(_series_values(data), include_zero=False)
    _draw_axes(layout, config, data, fonts, scale, y_low, y_high, ticks)
    draw = layout.draw
    group_width = (layout.plot_right - layout.plot_left) / max(1, len(data.x_values))
    line_width = max(2, round(config.dpi / 100 * scale))
    marker_radius = max(3 * scale, round(config.dpi / 110 * scale * 1.8))
    for series_index, series in enumerate(data.series):
        color = (*layout.colors[series_index % len(layout.colors)], 255)
        segment: list[tuple[float, float]] = []
        for index, value in enumerate(series.values):
            if value is None:
                if len(segment) >= 2:
                    draw.line(segment, fill=color, width=line_width, joint="curve")
                segment = []
                continue
            x = layout.plot_left + group_width * (index + 0.5)
            y = layout.plot_bottom - (
                (value - y_low) / (y_high - y_low) * (layout.plot_bottom - layout.plot_top)
            )
            segment.append((x, y))
        if len(segment) >= 2:
            draw.line(segment, fill=color, width=line_width, joint="curve")
        for point in segment:
            draw.ellipse(
                [
                    (point[0] - marker_radius, point[1] - marker_radius),
                    (point[0] + marker_radius, point[1] + marker_radius),
                ],
                fill=(255, 255, 255, 255),
                outline=color,
                width=max(2, line_width // 2),
            )


def _draw_scatter_chart(
    layout: _Layout,
    config: ChartConfig,
    data: ChartData,
    fonts: _FontSet,
    scale: int,
) -> None:
    y_low, y_high, ticks = _nice_axis(_series_values(data), include_zero=False)
    numeric_x = _x_numeric_values(data)
    x_bounds: tuple[float, float] | None = None
    if numeric_x is not None:
        x_bounds = _nice_axis(numeric_x, include_zero=False)[:2]
    _draw_axes(
        layout,
        config,
        data,
        fonts,
        scale,
        y_low,
        y_high,
        ticks,
        x_numeric=numeric_x,
        x_bounds=x_bounds,
    )
    draw = layout.draw
    if numeric_x is None:
        group_width = (layout.plot_right - layout.plot_left) / max(1, len(data.x_values))
        x_positions = [
            layout.plot_left + group_width * (index + 0.5)
            for index in range(len(data.x_values))
        ]
    else:
        x_min, x_max = x_bounds or (min(numeric_x), max(numeric_x))
        x_positions = [
            layout.plot_left
            + (value - x_min) / (x_max - x_min) * (layout.plot_right - layout.plot_left)
            for value in numeric_x
        ]
    radius = max(4 * scale, round(config.dpi / 105 * scale * 2))
    for series_index, series in enumerate(data.series):
        color = (*layout.colors[series_index % len(layout.colors)], 255)
        for x, value in zip(x_positions, series.values, strict=True):
            if value is None:
                continue
            y = layout.plot_bottom - (
                (value - y_low) / (y_high - y_low) * (layout.plot_bottom - layout.plot_top)
            )
            draw.ellipse(
                [(x - radius, y - radius), (x + radius, y + radius)],
                fill=color,
                outline=(255, 255, 255, 255),
                width=max(1, scale),
            )


def _draw_pie_chart(
    layout: _Layout,
    config: ChartConfig,
    data: ChartData,
    fonts: _FontSet,
    scale: int,
) -> None:
    draw = layout.draw
    values = [value or 0 for value in data.series[0].values]
    total = sum(values)
    left = layout.plot_left
    right = layout.legend_x - 30 * scale if layout.legend_x else layout.plot_right
    top = layout.plot_top
    bottom = layout.plot_bottom
    diameter = max(1, min(right - left, bottom - top))
    center_x = left + diameter / 2
    center_y = top + (bottom - top) / 2
    bbox = [
        (center_x - diameter / 2, center_y - diameter / 2),
        (center_x + diameter / 2, center_y + diameter / 2),
    ]
    start = -90.0
    for index, value in enumerate(values):
        extent = value / total * 360
        color = (*layout.colors[index % len(layout.colors)], 255)
        draw.pieslice(
            bbox,
            start=start,
            end=start + extent,
            fill=color,
            outline=(255, 255, 255, 255),
            width=max(1, scale),
        )
        start += extent


def render_chart(config: ChartConfig, data: ChartData) -> RenderResult:
    _require_pillow()
    if config.output_path.exists() and not config.overwrite:
        raise ChartError(
            f"Output already exists: {config.output_path}. Choose another path or pass --overwrite."
        )
    if config.x_label is None and config.chart_type != "pie":
        config.x_label = data.x_header
    if config.y_label is None and config.chart_type != "pie":
        config.y_label = data.series[0].label if len(data.series) == 1 else "Value"
    all_text = (
        [config.title, config.x_label, config.y_label, data.x_header]
        + list(data.x_values)
        + [series.label for series in data.series]
        + list(config.legend_labels)
    )
    need_cjk = _text_has_cjk(all_text)
    font_choice = resolve_font(config.font_family, need_cjk)
    font_size, font_size_auto = _choose_font_size(config, data)

    show_legend = config.legend if config.legend is not None else (
        len(data.series) > 1 or config.chart_type == "pie"
    )
    legend_count = len(data.x_values) if config.chart_type == "pie" else len(data.series)
    colors = _resolve_colors(config, max(legend_count, len(data.series), 1))
    legend_entries = _legend_entries(config, data, colors) if show_legend else []

    layout, fonts, scale = _create_layout(
        config,
        data,
        font_choice,
        font_size,
        legend_entries,
        show_legend,
        colors,
    )
    _draw_title_and_legend(layout, config, fonts, legend_entries, show_legend, scale)

    if config.chart_type == "bar":
        _draw_bar_chart(layout, config, data, fonts, scale)
    elif config.chart_type == "line":
        _draw_line_chart(layout, config, data, fonts, scale)
    elif config.chart_type == "scatter":
        _draw_scatter_chart(layout, config, data, fonts, scale)
    elif config.chart_type == "pie":
        _draw_pie_chart(layout, config, data, fonts, scale)

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    final = layout.image.resize((layout.width, layout.height), Image.Resampling.LANCZOS)
    if config.transparent:
        final = final.convert("RGBA")
    else:
        final = final.convert("RGB")
    final.save(config.output_path, format="PNG", dpi=(config.dpi, config.dpi))

    point_count = sum(
        1 for series in data.series for value in series.values if value is not None
    )
    return RenderResult(
        output_path=config.output_path,
        chart_type=config.chart_type,
        font_family=font_choice.family,
        requested_font=font_choice.requested,
        font_fallback=font_choice.fallback,
        fallback_reason=font_choice.reason,
        font_path=font_choice.path,
        font_size=font_size,
        font_size_auto=font_size_auto,
        dpi=config.dpi,
        width=config.width,
        height=config.height,
        series_count=len(data.series),
        point_count=point_count,
    )


def generate_chart(
    csv_path: Path | str,
    cli_options: Mapping[str, Any] | None = None,
) -> RenderResult:
    _require_pillow()
    path = Path(csv_path).expanduser().resolve()
    if not path.is_file():
        raise ChartError(f"CSV file not found: {path}")
    options = dict(cli_options or {})
    encoding = str(options.get("encoding") or "utf-8-sig")
    try:
        text = path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise ChartError(
            f"Could not decode {path.name} as {encoding}; use --encoding to select the file encoding."
        ) from exc
    metadata, body = extract_metadata(text)
    config = _coerce_config(path, metadata, body, options)
    data = load_chart_data(config, body)
    return render_chart(config, data)


def write_csv_template(path: Path | str, overwrite: bool = False) -> Path:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise ChartError(
            f"CSV template already exists: {destination}. Choose another path or pass --overwrite."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "\n".join(
            [
                "# scholarime: chart_type=bar",
                "# scholarime: header_row=1",
                "# scholarime: x_column=Category",
                "# scholarime: y_columns=Control,Treatment",
                "# scholarime: legend=true",
                "# scholarime: palette=academic-blue",
                "# scholarime: legend_colors=",
                "# scholarime: font_family=Times New Roman",
                "# scholarime: font_size=auto",
                "# scholarime: title=Example comparison",
                "Category,Control,Treatment",
                "Baseline,0.72,0.78",
                "Intervention,0.81,0.89",
                "Follow-up,0.79,0.91",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scholarly_chart.py",
        description=(
            "Generate a PNG academic chart from a CSV table. CSV settings use comment rows "
            "such as '# scholarime: chart_type=line'. CLI options override CSV settings."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scholarly_chart.py data.csv -o figure.png\n"
            "  python scholarly_chart.py data.csv --chart-type line --palette academic-earth\n"
            "  python scholarly_chart.py data.csv --font-size 11 --legend-colors '#1F4E79,#B85C38'\n"
            "  python scholarly_chart.py --list-palettes\n"
            "  python scholarly_chart.py --export-csv-template example.csv\n\n"
            "CSV metadata keys:\n"
            "  chart_type, header_row, delimiter, title, x_label, y_label, x_column,\n"
            "  y_columns, legend, legend_labels, legend_colors, palette, font_family,\n"
            "  font_size, width, height, dpi, grid, transparent, overwrite, output\n\n"
            "header_row counts non-comment CSV records; use 0 when the file has no header."
        ),
    )
    parser.add_argument("csv_file", nargs="?", type=Path, help="Input CSV file.")
    parser.add_argument("-o", "--output", type=Path, help="Output .png path.")
    parser.add_argument("--chart-type", choices=CHART_TYPES)
    parser.add_argument("--header-row", type=int, help="Header row among non-comment records.")
    parser.add_argument("--no-header", action="store_const", const=0, dest="header_row")
    parser.add_argument("--delimiter", help="Comma, semicolon, tab, pipe, or one character.")
    parser.add_argument("--encoding", default=None, help="CSV encoding (default: utf-8-sig).")
    parser.add_argument("--title")
    parser.add_argument("--x-label")
    parser.add_argument("--y-label")
    parser.add_argument("--x-column", help="X column name or 1-based index.")
    parser.add_argument("--y-columns", help="Comma-separated y column names or indices.")
    parser.add_argument(
        "--legend",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show or hide the legend.",
    )
    parser.add_argument("--legend-labels", help="Comma-separated series labels.")
    parser.add_argument(
        "--legend-colors",
        help="Comma-separated colors; overrides the academic palette.",
    )
    parser.add_argument("--palette", help="Academic palette name; see --list-palettes.")
    parser.add_argument("--font", dest="font_family", help="Font family or font file path.")
    parser.add_argument("--font-size", type=float, help="Font size in points; default is automatic.")
    parser.add_argument("--width", type=float, help="Figure width in inches.")
    parser.add_argument("--height", type=float, help="Figure height in inches.")
    parser.add_argument("--dpi", type=int, help="PNG resolution in dots per inch.")
    parser.add_argument(
        "--grid",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show or hide horizontal grid lines.",
    )
    parser.add_argument(
        "--transparent",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use a transparent background.",
    )
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow replacing an existing output file.",
    )
    parser.add_argument("--list-palettes", action="store_true", help="List academic palettes.")
    parser.add_argument("--list-fonts", action="store_true", help="List detected academic fonts.")
    parser.add_argument("--export-csv-template", type=Path, help="Write an example CSV and exit.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _cli_options(args: argparse.Namespace) -> dict[str, Any]:
    names = (
        "output",
        "chart_type",
        "header_row",
        "delimiter",
        "encoding",
        "title",
        "x_label",
        "y_label",
        "x_column",
        "legend",
        "palette",
        "font_family",
        "font_size",
        "width",
        "height",
        "dpi",
        "grid",
        "transparent",
        "overwrite",
    )
    options = {name: getattr(args, name) for name in names if getattr(args, name) is not None}
    if args.y_columns is not None:
        options["y_columns"] = _split_list(args.y_columns)
    if args.legend_labels is not None:
        options["legend_labels"] = _split_list(args.legend_labels)
    if args.legend_colors is not None:
        options["legend_colors"] = _split_list(args.legend_colors)
    return options


def _print_result(result: RenderResult) -> None:
    print(f"Chart saved: {result.output_path}")
    if result.font_fallback:
        reason = f"; {result.fallback_reason}" if result.fallback_reason else ""
        print(f"Font: {result.font_family} (fallback from {result.requested_font}{reason})")
    else:
        print(f"Font: {result.font_family}")
    mode = "auto" if result.font_size_auto else "manual"
    print(f"Font size: {result.font_size:.1f} pt ({mode})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.export_csv_template:
        try:
            path = write_csv_template(args.export_csv_template, overwrite=bool(args.overwrite))
        except ChartError as exc:
            parser.exit(2, f"error: {exc}\n")
        print(f"CSV template written: {path}")
        return 0
    if args.list_palettes:
        for name, colors in PALETTES.items():
            print(f"{name}: {', '.join(colors)}")
        return 0
    if args.list_fonts:
        _require_pillow()
        fonts = list_available_fonts()
        if not fonts:
            print("No supported font files were detected.")
            return 0
        for font in fonts:
            print(f"{font.family}: {font.path}")
        return 0
    if args.csv_file is None:
        parser.error("csv_file is required unless using --list-palettes, --list-fonts, or --export-csv-template.")

    try:
        result = generate_chart(args.csv_file, _cli_options(args))
    except ChartError as exc:
        parser.exit(2, f"error: {exc}\n")
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
