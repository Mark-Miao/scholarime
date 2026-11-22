---
name: scholarime
description: Support persistent research workspaces, scholarly search, citation verification, systematic review screening, data analysis, symbolic and formal math, manuscript assembly, publication figures, and full-prompt de-AI-tone editing.
---

# Scholarime

Help with academic questions while preserving the user's terms, evidence standards, and requested
deliverable. The language style is a functional requirement: it keeps responses relevant and
prevents capable models from replacing an answer with a report.

The complete style rubric is in [references/language-style.md](references/language-style.md).

## Mandatory Style Gate

Before every substantive final response, use a fresh style-review subagent. The purpose is to keep
the full rubric and revision loop out of the main agent's working context.

1. Compose the complete user-facing draft. Write it to a uniquely named hidden temporary file in
   the operating system's temporary directory, for example
   `/tmp/.scholarime-draft-<conversation>-<sequence>.md`. Do not treat an answer held only in
   conversation as the checked draft.
2. Spawn a fresh style-review subagent. Give it only the original user request, the absolute path
   to the draft, and the absolute path to `references/language-style.md`. Pass paths rather than
   copying the draft or rubric into the prompt. Require it to return the complete final revised
   draft text verbatim, rather than `PASS` or a status update. If it responds without reading the
   paths, retry with a full-history fork rather than treating that as a review.
3. Instruct the reviewer to read the complete rubric, edit the draft directly, and repeat its
   checks until the draft passes. The reviewer may tighten structure, shorten unsolicited material,
   and revise wording. It must preserve the main agent's claims, data, citations, code, and any
   explicitly requested format.
4. The reviewer must return the complete approved response text, with no review preface, analysis,
   wrapper fence, or status token. It may return `BLOCKED: reason` only when the edit cannot be
   completed.
5. A status-only, empty, acknowledgement-only, or otherwise incomplete reply is a failed review.
   Retry once with the full-text requirement; after another failure, use the inline fallback. Use
   the returned text as the final answer, delete the exact temporary file as the final local action,
   and do not delete a directory or use a broad glob.

The reviewer owns interpretation and enforcement of the detailed style rubric. The main agent
should not load the full reference when delegation is available.

If the environment cannot spawn subagents, read the rubric and run the same draft-and-revise loop
inline, then clean up the file. State the fallback only when it materially affects the result.

## De-AI Tone Tool

When the user asks to 去 AI 味、降低 AI 率、降低 AI 味、去 AI 腔, or otherwise rewrite supplied
text to sound less AI-generated, load
[references/less-ai-tone/TOOL.md](references/less-ai-tone/TOOL.md) completely and follow it.

That tool contains the complete original rule set and semantic criteria in prompt form. Do not call
the upstream Python scripts. Return only the processed text, not a detection report, unless the user
explicitly asks for a report.

The delivered text is user content. The mandatory response-style gate applies only to any
surrounding assistant commentary, not to the processed document itself; do not let the outer style
reviewer shorten or restructure the user's text.

## Academic Search

Use [scripts/academic_search.py](scripts/academic_search.py) when the user supplies a research
topic and needs relevant literature. Start with its help interface, include translated or
synonymous queries when useful, and use open sources by default.

```bash
python scholarime/scripts/academic_search.py --help
python scholarime/scripts/academic_search.py search "research topic" \
  --query "translated or expanded query" --sources all --limit 10
```

The tool stores source records in a temporary SQLite database and creates ranked
`candidates.json`. Verify those candidates before presenting them:

```bash
scholarime verify /tmp/scholarime-search-.../candidates.json
```

Read [references/citation-verification.md](references/citation-verification.md) before
interpreting the results. The verification file and HTML report are written beside the candidates.
Assess the candidates one by one, remove weak matches, and give the user ten or fewer references
in descending relevance with each verification status retained.
CNKI, Wanfang, and VIP are import-only sources; do not call them automatically searched.

Semantic Scholar requires a configured API key. If none exists, say explicitly that Semantic
Scholar search is unavailable; continue with OpenAlex, Crossref, arXiv, and PubMed. The install
workflow in [install/INSTALL.md](install/INSTALL.md) defines the required user question and safe
configuration step.

After the ranked list, ask whether the user wants citation formatting. If not, stop and remove
the exact temporary search directory. If yes, start the local review UI with
[scripts/reference_review.py](scripts/reference_review.py):

```bash
python scholarime/scripts/reference_review.py \
  --input /tmp/scholarime-search-.../candidates.json
```

The UI supports ordering, deletion, manual sources, and RIS, BibTeX, EndNote, or RefWorks imports.
After confirmation, read its `final_references.json` and preserve the user's final ordering. It
supports GB/T 7714-2025 and APA 7. Clean up the exact search run after delivering the result, and
do not claim strict CSL validation when the assets are missing.

Citation verification is evidence, not a fake/real verdict. Do not discard a relevant record
solely because it is `unverified` or `unable_to_check`. A `conflict` result must be shown
explicitly and must not be silently cited as confirmed.

The verification output also compares retrieved title, authors, year, container, volume, issue, and
pages with each candidate. Keep `field_verification_status` in the reviewed list and flag any
`conflict`; missing metadata remains `not_checked`. The detailed status contract is in
[references/citation-verification.md](references/citation-verification.md).

## Systematic Review Screening

When the user supplies an eligibility protocol and asks to screen records, use
[scripts/scholarly_screening.py](scripts/scholarly_screening.py). Read
[references/systematic-screening.md](references/systematic-screening.md) before starting.

```bash
scholarime screening init review-screening
scholarime screening prepare --work review-screening \
  --inputs candidates.json --config review-screening/screening_config.json
scholarime screening workflow ta --work review-screening \
  --protocol review-screening/screening_protocol.md \
  --config review-screening/screening_config.json --jobs pilot \
  --emit-prompts review-screening/prompts
```

Run Reviewer A, Reviewer B, adjudication, and QC in separate contexts. Do not synthesize missing
reviewer decisions. Merge only actual generated decision files:

```bash
scholarime screening merge --work review-screening \
  --from review-screening/decisions-a.json review-screening/decisions-b.json --stage ta
scholarime screening outputs --work review-screening --out review-output --stage ta
```

The output includes PRISMA counts, RIS groups, a methods draft, and a workspace-compatible
`TA_scholarime_sources.json`. AI decisions are decision support; the review team verifies them.
PRISMA numbers are provisional while records or required QC rechecks remain pending.

## Research Workspace and Evidence

Use `workspace` commands from [research_workspace.py](scripts/research_workspace.py) to create a
persistent project with sources, authorized PDFs, page/table/OCR segments, claims, evidence links,
decisions, and artifacts. The full workflow is in
[references/research-pipeline.md](references/research-pipeline.md).

```bash
python -m scholarime workspace init project --title "Research project"
python -m scholarime workspace add-sources project final_references.json
python -m scholarime workspace attach-pdf project --source-id 1 --file paper.pdf
python -m scholarime workspace extract-pdf project --attachment-id 1
python -m scholarime workspace matrix project --output evidence.json
```

Keep source/page attribution intact, use only authorized PDFs, and treat `supports`, `challenges`,
and `context` links as explicit research judgments. Do not automatically infer causal support.

## Statistical Analysis

Use [scholarly_stats.py](scripts/scholarly_stats.py) after data cleaning:

```bash
python -m scholarime stats validate analysis.json
python -m scholarime stats run cleaned.csv analysis.json
```

Descriptive analysis uses the standard library. Inferential tests and regression require SciPy and
Statsmodels. The tool supports OLS, WLS, robust covariance, logit, probit, GLM, mixed models,
diagnostics, multiple-comparison correction, stationarity tests, ARIMA, and power analysis.

Read [references/statistical-analysis.md](references/statistical-analysis.md) before writing a
model recipe. Every run includes a reporting audit. Report the unit used as `n`, replication,
missingness, model specification, assumptions, effect sizes, uncertainty, multiplicity, and study
design. Use `AUTHOR_INPUT_NEEDED` when the design is absent instead of inventing it; do not turn a
p-value into a causal or practical conclusion.

## Manuscript Assembly

Use [scholarly_manuscript.py](scripts/scholarly_manuscript.py) to assemble LaTeX or DOCX from a
project and structured manuscript JSON:

```bash
python -m scholarime manuscript build \
  --project-dir project \
  --manuscript manuscript.json \
  --output-prefix paper \
  --formats latex,docx
```

Citation markers use `[@record_id]` or `[@doi]`. The tool uses `citeproc-py` with the downloaded CSL
assets when available and reports any fallback to the built-in formatter. DOCX requires
`python-docx`. The author must review the output and citation mapping.

## Data Preparation

Use [scripts/scholarly_data.py](scripts/scholarly_data.py) for tabular data work. Start with a
profile and use its evidence to decide which types and quality issues need action:

```bash
python scholarime/scripts/scholarly_data.py profile data.csv
```

Write a declarative recipe after reviewing the profile. The operation schema and confirmation
requirements are in [references/data-recipes.md](references/data-recipes.md). Ask the user to
confirm conversions, imputations, deduplication, row removal, and identifier changes before
applying them.

```bash
python scholarime/scripts/scholarly_data.py validate recipe.json
python scholarime/scripts/scholarly_data.py apply data.csv recipe.json -o cleaned.csv
```

Do not overwrite the input. Do not infer missing codes such as "未知" or "无" without an explicit
`--null-token`. Excel support requires `openpyxl` from the installation dependencies. Preserve the
generated audit JSON and Markdown and report the output path, row/column changes, and material
transformations.

## Symbolic Derivation and Formal Verification

Use [scripts/symbolic_derive.py](scripts/symbolic_derive.py) for symbolic computation and proof
checking. Read [references/symbolic-tasks.md](references/symbolic-tasks.md) for the recipe schema
and verifier restrictions.

```bash
python scholarime/scripts/symbolic_derive.py doctor
python scholarime/scripts/symbolic_derive.py compute derivation.json
python scholarime/scripts/symbolic_derive.py verify proof.lean --project path/to/project
python scholarime/scripts/symbolic_derive.py verify proof.v --prover coq
```

SymPy produces candidate derivations with assumptions and warnings; it does not prove them
formally. Lean 4 and Coq verification count only when the compiler exits successfully after the
safety scan. Show the formal theorem statement to the user before attempting verification. Never
hide compiler failures, admission tokens, axioms, or timeouts. Stop automatic proof repair after
three attempts and show the remaining error. Do not install or replace an external prover without
user consent. On Arch Linux, Coq 9 is installed through the official `rocq` package; the verifier
accepts both `coqc` and `rocq compile`, and proofs importing the standard library also need
`rocq-stdlib`.

## Chart and Figure Production

For requests to turn a CSV table into a PNG figure, use
[scripts/scholarly_chart.py](scripts/scholarly_chart.py). Do not redraw the chart with ad hoc code
unless the script cannot represent the requested visualization.

The tool provides a help interface, CSV metadata, command-line overrides, academic palettes,
manual legend colors, chart type selection, header-row selection, and font fallback. It supports
bar, line, pie, and scatter charts.

Start with:

```bash
python scholarime/scripts/scholarly_chart.py --help
```

If Pillow is missing, follow [install/INSTALL.md](install/INSTALL.md). Do not change the user's pip
source until the user has chosen and approved a mirror. After a successful render, report the PNG
path, actual font, and font size returned by the tool.

For volcano, ROC, dot, marginal, paired, heatmap, or forest manuscript templates, use
[scripts/scholarly_figure.py](scripts/scholarly_figure.py):

```bash
scholarime figure list-templates
scholarime figure template volcano --input results.csv --output figures/volcano
scholarime figure validate-source plotting.py --strict
scholarime figure collision figure.pdf --json-out collision.audit.json
```

The template command writes PNG, editable SVG, PDF, 600 dpi TIFF, and a QA JSON record. PDF
collision auditing needs the optional `figure-pdf` dependency; source, SVG, panel, and PDF text
audits otherwise use lightweight dependencies. Read
[references/figure-production.md](references/figure-production.md) before choosing a template or
declaring a figure ready. A QA pass does not establish scientific validity.

## Academic Responsibility

The style gate changes presentation, not truth. The main agent remains responsible for source
accuracy, citation integrity, calculation, and the substance of the answer. A polished or
`PASS`-ed draft is not permission to invent evidence or hide an unresolved question.

## License

Original Scholarime code and documentation use CC BY-NC-SA 4.0. Commercial use is prohibited.
Third-party CSL styles, nature-skills figure code, Academic Research Skills screening material,
CiteVerify, and adapted style guidance retain their separate licenses documented in
`THIRD_PARTY_NOTICES.md`; do not relicense them as Scholarime content.
