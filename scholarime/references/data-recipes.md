# Data Processing Recipes

`scripts/scholarly_data.py` profiles tabular data and applies a declarative JSON recipe. The source
file is never modified. Profile first, inspect type evidence and quality issues, ask the user to
confirm the proposed transformations, then apply the recipe.

## Inputs and Outputs

Inputs: CSV, TSV, JSON (list of objects), JSONL/NDJSON, and XLSX/XLSM.
Outputs: CSV, TSV, JSON, JSONL, and XLSX.

CSV encoding is detected from UTF-8 BOM, UTF-8, GB18030, and UTF-16 unless `--encoding` is set.
Header position is controlled by `--header-row`; use `--no-header` when column names must be
generated. Excel users select a worksheet with `--sheet`.

## Agent Workflow

```bash
python scholarime/scripts/scholarly_data.py profile data.csv
python scholarime/scripts/scholarly_data.py validate recipe.json
python scholarime/scripts/scholarly_data.py apply data.csv recipe.json -o cleaned.csv
```

Profile output includes row and column counts, duplicate rows, inferred types, type confidence,
missingness, uniqueness, identifier risks, leading zeros, numeric distributions, IQR outlier
counts, date ranges, and quality notices. Samples and top category values are omitted unless
`--include-samples` is explicitly requested.

Treat type inference as evidence, not permission to mutate data. Ask the user before converting
identifiers, dropping rows, imputing values, deduplicating, or changing date representations.

## Recipe Format

```json
{
  "version": 1,
  "operations": [
    {"op": "trim_whitespace", "columns": ["*"]},
    {"op": "convert", "columns": {"year": "integer"}, "invalid": "error"},
    {"op": "filter", "conditions": [
      {"column": "year", "operator": "gte", "value": 2020}
    ]}
  ]
}
```

Supported operations:

- `rename`: `mapping` from old to new names.
- `select` and `drop_columns`: `columns`.
- `trim_whitespace`: optional `columns`, default all.
- `convert`: `columns` mapping to `string`, `integer`, `float`, `boolean`, `date`, or `datetime`;
  `invalid` may be `error`, `null`, or `keep`.
- `map_values`: `column`, `mapping`, and `unmapped` policy `keep`, `null`, or `error`.
- `fill_missing`: `column` and `strategy` `constant`, `mean`, `median`, `mode`, `forward`, or
  `backward`. Constant strategy requires `value`.
- `drop_missing`: `columns` and `how` `any` or `all`.
- `deduplicate`: optional `subset`, with `keep` `first` or `last`.
- `filter`: `conditions`; `match` is `all` or `any`. Operators are `eq`, `neq`, `gt`, `gte`, `lt`,
  `lte`, `in`, `not_in`, `contains`, `regex`, `is_null`, and `not_null`.
- `derive`: `column` and a restricted arithmetic `expression`; approved functions are `abs`,
  `min`, `max`, `round`, `len`, `lower`, `upper`, `str`, `int`, and `float`.
- `sort`: `by`, optional `descending`, and `nulls` `first` or `last`.
- `groupby`: `by` plus `aggregations`; supported functions are `count`, `nunique`, `sum`, `mean`,
  `median`, `min`, `max`, `first`, and `last`.
- `limit`: non-negative `count`.

```json
{
  "version": 1,
  "operations": [
    {
      "op": "groupby",
      "by": ["year"],
      "aggregations": {
        "records": {"function": "count", "column": "*"},
        "mean_score": {"function": "mean", "column": "score"}
      }
    }
  ]
}
```

Derive expressions do not execute arbitrary Python. Column names used in expressions must be
valid identifiers; rename columns first when they contain spaces or punctuation.

## Audit Output

`apply` writes the processed file plus `*.audit.json` and `*.audit.md` by default. The audit records
input/output hashes, row and column counts, every operation, and operation summary details. Existing
output or report files are rejected unless `--overwrite` is explicit.
