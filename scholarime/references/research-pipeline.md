# Research Workspace, Statistics, and Manuscripts

## Package Entry Point

After installation, every tool is available through one command:

```bash
scholarime --help
python -m scholarime --help
```

Optional dependency groups are defined in `pyproject.toml`: `charts`, `ui`, `excel`, `symbolic`,
`figures`, `figure-pdf`, `workspace`, `stats`, `writing`, and `all`.

## Research Workspace

```bash
scholarime workspace init project --title "Research project"
scholarime workspace add-sources project final_references.json
scholarime workspace sources project
scholarime workspace attach-pdf project --source-id 1 --file paper.pdf
scholarime workspace attachments project --source-id 1
scholarime workspace extract-pdf project --attachment-id 1
scholarime workspace ocr-pdf project --attachment-id 1 --language eng
scholarime workspace segments project --attachment-id 1
```

The project directory contains `project.sqlite3` and `files/`. Source metadata, authorized PDFs,
page-level text/table/OCR segments, claims, evidence links, decisions, and artifacts remain local.
Re-extracting a PDF is refused when linked evidence would be removed.

Claims and evidence:

```bash
scholarime workspace add-claim project --text "The intervention improved outcomes."
scholarime workspace link-evidence project --claim-id 1 --segment-id 3 --relation supports
scholarime workspace matrix project --output evidence.json
```

Treat `supports`, `challenges`, and `context` as researcher decisions. Do not infer causal support
from a co-occurring sentence.

## Systematic Review Screening

Protocol-driven screening uses `scholarime screening`. The full workflow, blinding requirements,
QC rules, and PRISMA handoff are in [systematic-screening.md](systematic-screening.md).
Scholarime search and review JSON files can be imported directly, and
`TA_scholarime_sources.json` can be passed back to `workspace add-sources`.

## Statistical Analysis

Run statistics on an existing CSV/Excel/JSON dataset. Data cleaning remains in `scholarly_data.py`.

```json
{
  "version": 1,
  "analyses": [
    {"op": "describe", "columns": ["score"]},
    {"op": "correlation", "columns": ["score", "age"], "method": "pearson"},
    {"op": "t_test", "value": "score", "group": "condition", "groups": ["control", "treatment"]},
    {"op": "regression", "formula": "score ~ age + C(condition)", "cov_type": "HC3"},
    {"op": "diagnostics", "formula": "score ~ age + C(condition)"},
    {"op": "multiple_comparison", "p_values": [0.01, 0.04, 0.2], "method": "holm"}
  ]
}
```

```bash
scholarime stats validate analysis.json
scholarime stats run cleaned.csv analysis.json
```

Descriptive analysis uses the standard library. Inferential tests and modelling require SciPy and
Statsmodels. The model schema and reporting requirements are in
[statistical-analysis.md](statistical-analysis.md). Every report includes an automatic reporting
audit, but the author must still supply the experimental unit, replication, missing-data handling,
model assumptions, and multiplicity policy. A p-value alone is not a conclusion.

## Publication Figures

Use `scholarime chart` for general CSV bar, line, pie, and scatter charts. Use
`scholarime figure` for validated volcano, ROC, dot, marginal, paired, heatmap, and forest
templates plus source, PDF, SVG, and panel audits. See
[figure-production.md](figure-production.md).

## Manuscript Assembly

The manuscript JSON contains metadata and sections with citation markers:

```json
{
  "version": 1,
  "title": "A Study",
  "authors": ["Ada Reader"],
  "abstract": "The topic matters [@ref-1].",
  "citation_style": "apa",
  "sections": [
    {
      "heading": "Introduction",
      "level": 1,
      "paragraphs": ["Prior work is relevant [@ref-1]."]
    }
  ],
  "figures": [
    {
      "path": "figure.png",
      "caption": "Retrieval workflow",
      "label": "fig:workflow"
    }
  ],
  "tables": [
    {
      "caption": "Descriptive values",
      "headers": ["Group", "Score"],
      "rows": [["A", "10"], ["B", "12"]],
      "label": "tab:values"
    }
  ]
}
```

```bash
scholarime manuscript build \
  --project-dir project \
  --manuscript manuscript.json \
  --output-prefix paper \
  --formats latex,docx
```

LaTeX is always available. DOCX requires `python-docx`. Citation markers resolve against sources in
the project database. The CSL processor is used when `citeproc-py` and the style asset are
available; otherwise the tool reports a warning and uses Scholarime's built-in formatter. The final
theorem is not implied: authors must review the generated document and citation mapping.
