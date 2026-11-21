# Repository Guidelines

## Project Structure & Module Organization

The distributable skill lives in `scholarime/`:

- Root `pyproject.toml` defines the package, optional dependency groups, and `scholarime` entry
  point.
- `scholarime/SKILL.md` is the entrypoint and workflow.
- `scholarime/scripts/` contains executable Python tools.
- `scholarime/tests/` contains standard-library `unittest` tests.
- `scholarime/references/` contains detailed guidance loaded only when needed.
- `scholarime/assets/` contains citation style assets and provenance notes.
- `scholarime/install/` contains dependency and installation instructions.

Keep generated PNG files outside the skill's source directories unless they are deliberate test
fixtures. Do not commit caches, dependency directories, credentials, or temporary drafts.

## Mandatory Language Style

When Scholarime is active, its language style is a functional requirement rather than cosmetic
polish. It prevents capable models from answering a simple question with an unsolicited report.
The single source of truth is
[scholarime/references/language-style.md](scholarime/references/language-style.md). All agents
working on or through Scholarime must follow the gates below and keep the skill's `SKILL.md`
aligned with this rule.

The rubric adapts selected response-level rules from `lieflat-less-ai-tone` under CC BY-NC 4.0.
See [scholarime/references/licenses/lieflat-less-ai-tone.md](scholarime/references/licenses/lieflat-less-ai-tone.md).
Commercial use of the adapted material requires checking the upstream license and obtaining
permission when necessary.

### Draft and subagent review

Before every substantive user-facing final response:

1. Compose the complete response and write it to a uniquely named hidden temporary file under the
   operating system's temporary directory. Use a path such as
   `/tmp/.scholarime-draft-<conversation>-<sequence>.md`.
2. Spawn a fresh style-review subagent. Give it only the original user request, the absolute draft
   path, and the absolute language-style path. Pass paths, not the full draft or rubric. Tell it
   explicitly: "Return the complete final revised draft text verbatim. Do not return PASS or a
   status message instead of the text." If it replies only that it needs a task or asks what to
   do, treat that as a dispatch failure and retry with a full-history fork that can see this
   conversation.
3. The subagent reads the full style specification, edits the draft directly when needed, and
   repeats the pass/revise check until the draft complies. It must preserve factual claims, data,
   citations, code, and any explicitly requested format.
4. The subagent returns the complete approved response text, with no preface, analysis, headings
   added by the reviewer, Markdown fence, or status token. If the draft is unchanged, it still
   returns the complete text. If the task cannot be completed, it returns only
   `BLOCKED: concise reason`.
5. The main agent treats a status-only, empty, or acknowledgement-only reply as a failed review.
   Retry once with the explicit full-text instruction; if it still fails, use the inline fallback.
   Otherwise use the returned text as the final answer, delete that exact temporary file as the
   final local action, and send the approved text without adding material.

Using a separate reviewer keeps the complete rubric and iterative style edits out of the main
agent's context. If subagents are unavailable, the main agent must read the style reference, run
the same loop inline, and still remove the exact temporary file. Never fabricate a `PASS`.

## Build, Test, and Development Commands

There is no package build step. Install the Python dependencies by following
[scholarime/install/INSTALL.md](scholarime/install/INSTALL.md).

```bash
python3 -m unittest discover -s scholarime/tests -v
python3 -m scholarime --help
python3 scholarime/scripts/academic_search.py --help
python3 scholarime/scripts/scholarly_data.py --help
python3 scholarime/scripts/symbolic_derive.py --help
python3 scholarime/scripts/reference_review.py --help
python3 scholarime/scripts/scholarly_chart.py --help
python3 scholarime/scripts/scholarly_chart.py --list-palettes
python3 scholarime/scripts/scholarly_chart.py --list-fonts
```

Use `python` instead of `python3` where that is the active interpreter name. Run commands from the
repository root unless an instruction says otherwise.

## Chart Generation Tool

`scholarime/scripts/scholarly_chart.py` turns CSV tables into publication-oriented PNG charts.
Use it for chart requests instead of writing a one-off plotting script. It supports grouped bar,
line, pie, and scatter charts, academic palettes, manual legend colors, configurable header
position, explicit font and size, automatic font sizing, and Times New Roman fallback.

Minimal command:

```bash
python3 scholarime/scripts/scholarly_chart.py data.csv -o figure.png
```

Common overrides:

```bash
python3 scholarime/scripts/scholarly_chart.py data.csv -o figure.png \
  --chart-type line \
  --header-row 1 \
  --x-column Year \
  --y-columns Control,Treatment \
  --legend \
  --palette academic-earth \
  --font "Times New Roman" \
  --font-size 11
```

CSV files can carry their own settings in `# scholarime: key=value` records. Command-line options
override CSV settings:

```csv
# scholarime: chart_type=bar
# scholarime: header_row=1
# scholarime: x_column=Category
# scholarime: y_columns=Control,Treatment
# scholarime: legend=true
# scholarime: legend_colors=#1F4E79,#B5651D
# scholarime: palette=academic-blue
# scholarime: font_family=Times New Roman
# scholarime: font_size=auto
Category,Control,Treatment
Baseline,0.72,0.78
Intervention,0.81,0.89
```

`header_row` counts non-comment CSV records. Use `0` for a headerless file. Available palettes are
`academic-blue`, `academic-earth`, `academic-slate`, `academic-warm`, and `academic-mono`;
`legend_colors` accepts comma-separated CSS color names or hex values.

Use `--export-csv-template example.csv` for a complete starter file. Use `--overwrite` only when
the user intends to replace an existing PNG or template. The tool reports the absolute PNG path,
the font actually used, whether fallback occurred, and the font size. Preserve those values when
describing the result to the user.

## Data Preparation Tool

`scholarime/scripts/scholarly_data.py` profiles and transforms CSV, TSV, JSON, JSONL, and Excel
data. It is a reproducible data-preparation tool, not an unrestricted Python analysis agent.
Always profile before proposing transformations:

```bash
python3 scholarime/scripts/scholarly_data.py profile data.csv
```

Use the profile's type evidence, missingness, uniqueness, leading-zero warnings, duplicates, and
outlier notices to generate a small JSON recipe. Read
[scholarime/references/data-recipes.md](scholarime/references/data-recipes.md) before writing one.
Explain the proposed conversions, row drops, imputations, and deduplication to the user and obtain
confirmation before running:

```bash
python3 scholarime/scripts/scholarly_data.py validate recipe.json
python3 scholarime/scripts/scholarly_data.py apply data.csv recipe.json -o cleaned.csv
```

Type inference is evidence, not permission to mutate data. Preserve identifiers and leading
zeros unless the user explicitly intends conversion. Do not silently treat meaningful categories
such as "未知" or "无" as missing; use `--null-token` to declare missing codes. Samples and
category values appear in the profile only when `--include-samples` is explicitly requested.

The tool never modifies the input file, refuses to overwrite outputs without `--overwrite`, and
writes JSON and Markdown audit reports beside the output by default. Preserve and report those
paths, row/column changes, and operation summaries. Excel support depends on `openpyxl` from the
installation dependency file.

## Symbolic Derivation and Formal Verification

Use `scholarime/scripts/symbolic_derive.py` for algebra, calculus, equations, limits, and formal
proof checking. Read [scholarime/references/symbolic-tasks.md](scholarime/references/symbolic-tasks.md)
before constructing a compute recipe or proof task. Check installed engines with:

```bash
python3 scholarime/scripts/symbolic_derive.py doctor
python3 scholarime/scripts/symbolic_derive.py compute derivation.json
python3 scholarime/scripts/symbolic_derive.py verify proof.lean --project path/to/project
python3 scholarime/scripts/symbolic_derive.py verify proof.v --prover coq
```

For computation, declare variables and domain assumptions, use explicit SymPy/Python syntax, and
prefer a short declarative operation chain. Show the final LaTeX and material warnings about
integration constants, branch behavior, singularities, and failed equivalence checks. Never
describe a CAS result as a formal proof.

For formal verification, first show the user the exact formal theorem statement. Do not silently
change the statement to fit a proof. The verifier rejects proof admission, undeclared axioms,
custom execution mechanisms, and other unsafe declarations before invoking Lean or Coq. Mark a
source verified only when the compiler exits successfully. If repair is needed, retain the compiler
diagnostics and stop after three automatic attempts so the user can inspect the obstruction.

Lean 4 and Coq are external toolchains, not pip dependencies. Do not install or replace them
without user consent. If an engine is unavailable, report that status instead of claiming
verification. Arch Linux exposes Coq 9 as the official `rocq` package, so accept either `coqc` or
`rocq compile`. Install `rocq-stdlib` when proofs import the Rocq standard library. SymPy is
installed through `scholarime/install/dependencies.txt`.

## Academic Search and Citation Review

Use `scholarime/scripts/academic_search.py` for literature discovery. OpenAlex, Crossref, arXiv,
and PubMed work without credentials. Semantic Scholar is enabled only after an API key is saved.
The agent must never invent a key or claim Semantic Scholar was searched when it was skipped.
CNKI, Wanfang, and VIP do not have approved public search interfaces in this project; their
records enter through the import step in the review UI. Do not describe those databases as
automatically searched.

Start a search with:

```bash
python3 scholarime/scripts/academic_search.py search "研究主题" \
  --query "English translation or synonymous query" \
  --sources all \
  --limit 10
```

The tool stores raw and normalized source records in a temporary SQLite database and writes ranked
candidates to `candidates.json`. Review those candidates one at a time using their titles,
abstracts, source metadata, DOI, year, citation count, and relevance explanation. Remove off-topic
records and present ten or fewer references in strong-to-weak order. Cite only metadata that was
actually retrieved or supplied by the user.

Set `SCHOLARIME_CONTACT_EMAIL` for the OpenAlex and Crossref polite pools. Set `NCBI_API_KEY` when
available to raise PubMed E-utilities limits. HTTP 429/5xx and transient network failures are
retried with backoff. A source with some successful queries and some failures is reported as
`partial`; tell the user that coverage is incomplete rather than treating it as a total failure.

After presenting the list, ask whether the user wants citation formatting. If they decline, stop
and clean up the temporary search run:

```bash
python3 scholarime/scripts/academic_search.py cleanup /tmp/scholarime-search-...
```

If they accept, launch the local review UI:

```bash
python3 scholarime/scripts/reference_review.py \
  --input /tmp/scholarime-search-.../candidates.json
```

The UI starts with the ranked candidates already loaded. It supports drag-and-drop ordering,
deletion, manual technical manuals or web sources, and imports from RIS, BibTeX, EndNote, and
RefWorks. On confirmation it writes `final_references.json` beside the candidate file and closes
the native window when `pywebview` is available. Read that JSON and use its final order and
citation list. Do not reorder it without the user's instruction. After the result has been
delivered and no longer needs the run directory, remove the exact temporary search directory.

The UI supports GB/T 7714-2025 sequential citations and APA 7. The required CSL assets are included
under `scholarime/assets/citation-styles/`; follow their third-party license when redistributing
them.

## Research Workspace and PDF Evidence

Use `scholarime workspace` or `scholarime/scripts/research_workspace.py` to keep a project's source
library, authorized PDFs, page-level evidence, claims, decisions, and artifacts together. Read
[scholarime/references/research-pipeline.md](scholarime/references/research-pipeline.md) for the
full schema.

```bash
python3 -m scholarime workspace init project --title "Research project"
python3 -m scholarime workspace add-sources project final_references.json
python3 -m scholarime workspace attach-pdf project --source-id 1 --file paper.pdf
python3 -m scholarime workspace extract-pdf project --attachment-id 1
python3 -m scholarime workspace segments project --attachment-id 1
python3 -m scholarime workspace add-claim project --text "Claim text"
python3 -m scholarime workspace link-evidence project --claim-id 1 --segment-id 2
python3 -m scholarime workspace matrix project --output evidence.json
```

Use only PDFs the user is authorized to process. Keep page/source attribution intact. OCR is
optional and requires Poppler and Tesseract. Re-extraction is blocked when it would remove linked
evidence. Do not describe a segment as proving a claim automatically; relations such as
`supports`, `challenges`, and `context` remain explicit researcher decisions.

## Statistical Analysis

Use `scholarime stats` on cleaned tabular data. Read the workspace/statistics/manuscript guide
before writing a recipe:

```bash
python3 -m scholarime stats validate analysis.json
python3 -m scholarime stats run cleaned.csv analysis.json
```

Descriptive statistics work with the standard library. Inferential tests and regression require
SciPy and Statsmodels. Review missing-data handling, assumptions, effect sizes, confidence
intervals, and study design. A p-value alone is not evidence of importance or causality.

## Manuscript Assembly

Use `scholarime manuscript` to assemble LaTeX or DOCX from a project and a structured manuscript
JSON:

```bash
python3 -m scholarime manuscript build \
  --project-dir project \
  --manuscript manuscript.json \
  --output-prefix paper \
  --formats latex,docx
```

Citation markers use `[@record_id]` or `[@doi]` and resolve only against project sources. The tool
uses `citeproc-py` and the downloaded CSL assets when available; otherwise it reports the fallback
to the built-in formatter. DOCX requires `python-docx`. The generated build report and artifact
hashes are recorded in the project. The author must review the final document and citation mapping.

## Coding Style & Naming Conventions

No formatter or linter is configured. Follow the chosen language's conventions, add its
formatter/linter configuration at the root, use formatter-defined indentation, and never mix tabs
and spaces. Prefer descriptive names and one deliberate casing convention per language or directory.

## Testing Guidelines

Use standard-library `unittest` for Python tools. Add tests under `scholarime/tests/` and name them
by behavior, such as `test_missing_font_falls_back_and_is_reported`. For chart changes, verify the
generated file is a readable PNG and cover both CSV-driven settings and CLI overrides. For data
changes, cover type inference, missing values, row preservation/drop counts, unsafe expressions,
overwrite protection, and audit output. For symbolic changes, cover expression sandboxing,
forbidden proof constructs, compiler success/failure states, and timeout/unavailable-engine
reporting. For workspace changes, cover source deduplication, claim/evidence links, and matrix
output. For writing changes, cover citation resolution, CSL fallback, and partial-output
protection. Run:

```bash
python3 -m unittest discover -s scholarime/tests -v
```

Require tests for behavior changes unless the pull request explains why none apply.

## Commit & Pull Request Guidelines

No commit history is available, so use concise imperative subjects such as
`Add citation import validation` and keep unrelated changes separate. Pull requests should
summarize the change, list verification commands, note migrations, link issues, and include
screenshots for interface changes. Resolve review feedback before merging.

## Configuration & Security

Store secrets in untracked environment configuration or a managed secret store. Commit an
example configuration with placeholders when setup would otherwise be unclear, and never commit
real credentials or local tool state.

Original code and documentation are licensed under CC BY-NC-SA 4.0. Commercial use is prohibited
by the project license. CSL assets, adapted language-style material, and external proof tools
retain their own terms. Keep [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) accurate when adding
third-party code or content.

Semantic Scholar installation must ask the user whether they have an API key. If yes, run
`scholarime/install/configure_semantic_scholar.py` and use its hidden prompt; do not pass the key
through shell arguments, logs, or chat when a safer path exists. If no, tell the user that
Semantic Scholar search is unavailable and continue with the open sources. The saved configuration
uses the operating system's per-user config directory, and `SEMANTIC_SCHOLAR_API_KEY` overrides it.
